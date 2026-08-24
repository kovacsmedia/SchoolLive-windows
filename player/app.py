# player/app.py
#
# Snap-only rendszer:
#   • URL lejátszás eltávolítva – hang csak Snapcaston szólhat
#   • OFFLINE MÓD: csak csengetések szólnak lokálisan (bell cache)
#   • TTS + rádió offline módban: overlay sem jelenik meg (nincs hang)
#   • SnapStatus + WsStatus → UI státuszpontok frissítése

import time
import threading
import datetime
from dataclasses import dataclass
from typing import Optional

import sys

import api_client as api
import audio_manager as audio
from api_client       import DEVICE_KEY, SHORT_ID, HARDWARE_ID
from config           import load_settings, save_settings
from snapcast_manager import SnapcastManager, SnapStatus
from sync_client      import SyncClient, WsStatus
from updater_client   import AutoUpdater
from system_volume    import set_system_volume, set_system_mute
from ui               import PlayerUI

BEACON_INTERVAL_S = 30
POLL_INTERVAL_S   = 5


class SchoolLiveApp:
    def __init__(self, ui: PlayerUI):
        self.ui        = ui
        self._settings = load_settings()
        self._bells:   list = []
        self._status   = "provisioning"
        self._ws_online  = False
        self._snap_muted = False
        self._last_bell_key   = ""
        self._device_id: Optional[str] = None

        # ── Snapcast manager ──────────────────────────────────────────────────
        self._snap = SnapcastManager(
            on_connected     = self._on_snap_connected,
            on_disconnected  = self._on_snap_disconnected,
            on_status_change = self._on_snap_status,
            on_error         = self._on_snap_error,
        )

        # ── WebSocket sync kliens ─────────────────────────────────────────────
        self._ws = SyncClient(
            on_prepare       = self._on_prepare,
            on_play          = self._on_play,
            on_immediate     = self._on_immediate,
            on_connected     = self._on_ws_connected,
            on_disconnected  = self._on_ws_disconnected,
            on_status_change = self._on_ws_status,
            on_device_id     = self._on_ws_device_id,
            device_key       = DEVICE_KEY,
        )

        self._pending: dict = {}

        ui.on_volume_change = self._handle_volume
        self._volume = self._settings.get("volume", 7)
        ui.set_volume_display(self._volume)
        self._handle_volume(self._volume)

        # SET_VOLUME/MUTE/REBOOT/SHOW_MESSAGE mostantól a WS `_on_immediate`-en
        # érkeznek real-time (nem a korábbi 2s-es HTTP /devices/poll-on
        # keresztül) – ld. device_agent.py (törölve) helyett fent.

        self._updater = AutoUpdater(
            on_update_available = self._on_update_available,
            on_downloading      = self._on_update_downloading,
            on_ready_to_install = self._on_update_ready,
            on_error            = lambda e: print(f"[Update] {e}"),
        )

        self._boot()

    # ── Snap mód döntő ───────────────────────────────────────────────────────

    def _snap_usable(self) -> bool:
        return (
            self._snap.available
            and self._snap.connected
            and not self._snap_muted
        )

    # ── Boot ─────────────────────────────────────────────────────────────────

    def _boot(self) -> None:
        threading.Thread(target=self._provision_loop, daemon=True).start()

    def _provision_loop(self) -> None:
        self.ui.show_pending()
        status = api.provision()
        if status == "active":
            self.ui.hide_pending()
            self._activate()
            return
        while True:
            time.sleep(POLL_INTERVAL_S)
            status = api.poll_status()
            if status == "active":
                self.ui.hide_pending()
                self._activate()
                return
            api.provision()

    def _activate(self) -> None:
        print(f"[App] Aktiválva: {SHORT_ID}")
        self._status = "active"

        def _fetch_info():
            name = api.fetch_tenant_name(DEVICE_KEY)
            if name:
                self.ui.set_institution(name)
            device_id = api.get_device_id(DEVICE_KEY)
            if device_id:
                self._device_id = device_id
            snap_port = api.fetch_snap_port(DEVICE_KEY)
            if snap_port:
                self._snap.set_port(snap_port)
            if self._snap.available:
                self._snap.start()
            else:
                self.ui.set_snap_status(SnapStatus.OFFLINE)

        threading.Thread(target=_fetch_info, daemon=True).start()
        self._ws.start()
        threading.Thread(target=self._sync_bells,     daemon=True).start()
        threading.Thread(target=self._bell_tick_loop, daemon=True).start()
        threading.Thread(target=self._beacon_loop,    daemon=True).start()
        self._updater.start()

    # ── Snap callbacks ────────────────────────────────────────────────────────

    def _on_snap_connected(self) -> None:
        print("[App] 🟢 Snap mód aktív")
        audio.stop()
        self.ui.hide_overlay()

    def _on_snap_disconnected(self) -> None:
        print("[App] 🔴 Snap offline mód")
        self.ui.hide_overlay()

    def _on_snap_status(self, status: SnapStatus) -> None:
        self.ui.set_snap_status(status)

    def _on_snap_error(self, msg: str) -> None:
        print(f"[Snap] Hiba: {msg}")

    # ── WS callbacks ─────────────────────────────────────────────────────────

    def _on_ws_connected(self) -> None:
        self._ws_online = True
        self.ui.set_online(True)

    def _on_ws_disconnected(self) -> None:
        self._ws_online = False
        self.ui.set_online(False)

    def _on_ws_status(self, status: WsStatus) -> None:
        self.ui.set_ws_status(status)

    def _on_ws_device_id(self, device_id: str) -> None:
        """HELLO deviceId – korábban csak a HTTP beacon válaszából ismertük
        meg. Csak egyszer mentjük el (idempotens, akárhányszor csatlakozunk
        újra)."""
        if not self._device_id:
            self._device_id = device_id
            api.save_device_id(device_id)

    # ── PREPARE ──────────────────────────────────────────────────────────────

    def _on_prepare(self, msg: dict) -> None:
        command_id  = msg.get("commandId", "")
        snap_active = msg.get("snapcastActive", False)

        target_ids = msg.get("targetDeviceIds")
        if target_ids is not None and isinstance(target_ids, list):
            is_targeted = (self._device_id in target_ids) if self._device_id else True
            if not is_targeted and not self._snap_muted:
                self._snap_muted = True
                self._snap.mute(True)
            elif is_targeted and self._snap_muted:
                self._snap_muted = False
                self._snap.mute(False)
        else:
            if self._snap_muted:
                self._snap_muted = False
                self._snap.mute(False)

        self._pending[command_id] = {
            "action":      msg.get("action", ""),
            "url":         msg.get("url"),
            "text":        msg.get("text"),
            "title":       msg.get("title"),
            "snap_active": snap_active,
        }
        self._ws.send_ack(command_id, 0)

    # ── PLAY ─────────────────────────────────────────────────────────────────

    def _on_play(self, msg: dict) -> None:
        command_id  = msg.get("commandId", "")
        play_at_ms  = msg.get("playAtMs")
        duration_ms = msg.get("durationMs")
        prepare     = self._pending.pop(command_id, None)
        if not prepare:
            return

        # PREPARE-en NEM célzott eszközön se snap-en, se lokálisan ne szóljon
        # a hang, és HUD se jelenjen meg. A `_snap_muted` flag-et a PREPARE
        # állítja be a `targetDeviceIds` alapján. (Egyezően az Android
        # `applyTargeting` skip-pel.)
        if self._snap_muted:
            print(f"[App] PLAY: nem-célzott eszköz (snap muted), skip")
            return

        server_now = self._ws.clock.server_now_ms()
        if play_at_ms:
            delay_ms = play_at_ms - server_now
            if delay_ms < -10_000:
                print(f"[App] PLAY stale ({-delay_ms:.0f}ms) → skip")
                return
            delay_ms = max(0, delay_ms)
        else:
            delay_ms = 0

        snap_usable = bool(prepare.get("snap_active", False) and self._snap_usable())

        ctx = {
            "action":      prepare["action"],
            "url":         prepare["url"],
            "text":        prepare["text"],
            "title":       prepare["title"],
            "duration_ms": duration_ms,
            "snap_usable": snap_usable,
            "volume":      self._volume,
            "delay_ms":    delay_ms,
        }

        def _execute(ctx=ctx):
            if ctx["delay_ms"] > 50:
                time.sleep(ctx["delay_ms"] / 1000)
            self._dispatch_action(ctx)

        threading.Thread(target=_execute, daemon=True).start()

    # ── Azonnali broadcast ────────────────────────────────────────────────────

    def _on_immediate(self, msg: dict) -> None:
        action      = msg.get("action", "")
        snap_active = msg.get("snapcastActive", False)
        dur_ms      = msg.get("durationMs")
        snap_usable = snap_active and self._snap_usable()

        # HUD-célzás: a NOW_PLAYING_INFO / immediate BELL / TTS / PLAY_URL
        # broadcast minden tenant-eszközre megy (a backend source:start
        # eventjén nincs targeting-lista). Az utolsó PREPARE célzása alapján
        # `_snap_muted` jelzi, hogy a kliens hallja-e a snap streamet. Ha
        # nem hallja, akkor HUD-ot sem mutatunk – egyezően az Android
        # kliens viselkedésével.
        if self._snap_muted and action in ("BELL", "TTS", "PLAY_URL", "NOW_PLAYING_INFO"):
            print(f"[App] {action}: snap muted (nem célzott) → HUD skip")
            return

        if action == "BELL":
            url = msg.get("url", "")
            now = datetime.datetime.now()
            key = f"{now.hour}:{now.minute}"
            if self._last_bell_key == key:
                return
            self._last_bell_key = key
            self._dispatch_action({
                "action": "BELL", "url": url, "text": None, "title": None,
                "duration_ms": dur_ms, "snap_usable": snap_usable,
                "volume": self._volume, "delay_ms": 0,
            })

        elif action == "TTS":
            self._dispatch_action({
                "action": "TTS", "url": msg.get("url"), "text": msg.get("text", ""),
                "title": None, "duration_ms": dur_ms, "snap_usable": snap_usable,
                "volume": self._volume, "delay_ms": 0,
            })

        elif action == "PLAY_URL":
            self._dispatch_action({
                "action": "PLAY_URL", "url": msg.get("url"),
                "title": msg.get("title") or "Iskolarádió",
                "text": None, "duration_ms": dur_ms, "snap_usable": snap_usable,
                "volume": self._volume, "delay_ms": 0,
            })

        elif action == "STOP_PLAYBACK":
            self._do_stop()

        elif action == "NOW_PLAYING_INFO":
            # Backend forrás-start: az aktuálisan szóló forrás HUD-frissítése.
            # A snap stream folyamatosan megy, csak az UI-t frissítjük.
            title = msg.get("title") or ""
            job_type = msg.get("jobType") or ""
            source_type = msg.get("sourceType") or ""
            print(f"[App] NOW_PLAYING_INFO: {job_type} '{title}' (source={source_type})")
            try:
                if hasattr(self.ui, "show_now_playing"):
                    self.ui.show_now_playing(title, job_type)
                elif title and hasattr(self.ui, "show_radio_overlay"):
                    self.ui.show_radio_overlay(title, 0)
            except Exception:
                pass

        elif action == "SYNC_BELLS":
            threading.Thread(target=self._sync_bells, daemon=True).start()

        # ── Vezérlő parancsok (korábban csak a HTTP /devices/poll DeviceAgent
        # kapta meg ezeket – ld. device_agent.py _execute). A commandId jelenléte
        # esetén CMD_ACK-ot küldünk, hogy a backend ACKED-re állítsa a parancsot
        # (ne duplikálódjon, ha a kliens időközben újracsatlakozik).
        elif action == "SET_VOLUME":
            command_id = msg.get("commandId", "")
            vol = msg.get("volume")
            if not isinstance(vol, (int, float)):
                if command_id:
                    self._ws.send_cmd_ack(command_id, False, "No volume")
                return
            vol_i = max(0, min(10, int(vol)))
            print(f"[App] SET_VOLUME (WS) → {vol_i}")
            self._on_remote_set_volume(vol_i)
            if command_id:
                self._ws.send_cmd_ack(command_id, True)

        elif action == "MUTE":
            command_id = msg.get("commandId", "")
            muted = bool(msg.get("mute", True))
            print(f"[App] MUTE (WS) → {muted}")
            self._on_remote_mute(muted)
            if command_id:
                self._ws.send_cmd_ack(command_id, True)

        elif action == "REBOOT":
            command_id = msg.get("commandId", "")
            print("[App] REBOOT (WS)")
            # ACK ELŐSZÖR, hogy a backend lássa a sikert, mielőtt a kliens
            # kilép (ld. _on_remote_reboot).
            if command_id:
                self._ws.send_cmd_ack(command_id, True)
            self._on_remote_reboot()

        elif action == "SHOW_MESSAGE":
            command_id = msg.get("commandId", "")
            text = str(msg.get("message") or "")
            if not text:
                if command_id:
                    self._ws.send_cmd_ack(command_id, False, "No message")
                return
            print(f"[App] SHOW_MESSAGE (WS): {text}")
            self._on_remote_show_message(text)
            if command_id:
                self._ws.send_cmd_ack(command_id, True)

    # ── Diszpécser ───────────────────────────────────────────────────────────

    def _dispatch_action(self, ctx: dict) -> None:
        action      = ctx["action"]
        url         = ctx["url"]
        snap_usable = ctx["snap_usable"]
        volume      = ctx["volume"]
        dur_ms      = ctx["duration_ms"]

        print(f"[App] ▶ {action} snap={snap_usable} dur={dur_ms}ms")

        if action == "BELL":
            sound_file = url.split("/")[-1] if url else ""
            if snap_usable:
                # Snap kezeli a hangot, csak overlay
                self.ui.show_bell_overlay(dur_ms)
            else:
                # Offline: lokális bell pygame-mel
                self.ui.show_bell_overlay(dur_ms)
                audio.play_bell(
                    sound_file,
                    volume / 10.0,
                    on_done=lambda: self.ui.hide_overlay(),
                )

        elif action == "TTS":
            text = ctx.get("text", "")
            overlay_ms = dur_ms if dur_ms else self._calc_reading_ms(text)
            if snap_usable:
                # Snap kezeli a hangot, overlay mutatása
                if text:
                    self.ui.show_message_overlay(text, overlay_ms)
            else:
                # Offline: TTS nem szól – sem hang, sem overlay
                print("[App] TTS: snap nem elérhető → kihagyva (offline mód)")

        elif action == "PLAY_URL":
            title = ctx.get("title") or "Iskolarádió"
            if snap_usable:
                self.ui.show_radio_overlay(title, dur_ms)
            else:
                print("[App] PLAY_URL: snap nem elérhető → kihagyva (offline mód)")

    # ── Stop ─────────────────────────────────────────────────────────────────

    def _do_stop(self) -> None:
        audio.stop()
        self.ui.hide_overlay()
        if self._snap_muted:
            self._snap_muted = False
            self._snap.mute(False)

    # ── Csengetési rend ───────────────────────────────────────────────────────

    def _sync_bells(self) -> None:
        bells = api.fetch_bells(DEVICE_KEY)
        if bells:
            self._bells = bells
            audio.prefetch_bells(bells)
            self.ui.set_bells(bells)
            self.ui.set_cache_status(f"{len(bells)} csengő betöltve")
        else:
            self.ui.set_cache_status("Csengetési rend üres")

    # ── Offline bell tick ─────────────────────────────────────────────────────

    def _bell_tick_loop(self) -> None:
        """
        Offline csengetés: csak ha snap NEM elérhető.
        Ha snap elérhető → a szerver küldi a BELL parancsot.
        """
        while True:
            time.sleep(5)
            if self._status != "active" or not self._bells:
                continue
            if self._snap_usable():
                continue

            now = datetime.datetime.now()
            if now.second > 58:
                continue

            key = f"{now.hour}:{now.minute}"
            if self._last_bell_key == key:
                continue

            due = next(
                (b for b in self._bells
                 if b["hour"] == now.hour and b["minute"] == now.minute),
                None,
            )
            if not due:
                continue

            self._last_bell_key = key
            sound_file = due.get("soundFile", "kibecsengo.mp3")
            print(f"[App] 🔔 Offline bell: {sound_file} ({now.hour:02d}:{now.minute:02d})")
            self.ui.show_bell_overlay(None)
            audio.play_bell(
                sound_file,
                self._volume / 10.0,
                on_done=lambda: self.ui.hide_overlay(),
            )

    # ── Beacon ───────────────────────────────────────────────────────────────
    #
    # WS-alapú beacon (korábban 30s-enkénti HTTP POST /devices/native/beacon
    # volt) – ugyanaz a payload, csak a SyncEngine WS "BEACON" handlere
    # dolgozza fel. A deviceId-t már nem a beacon válaszából tanuljuk meg,
    # hanem a HELLO üzenetből (ld. _on_ws_device_id).

    def _beacon_loop(self) -> None:
        while True:
            try:
                status_payload = {
                    "snapStatus": self._snap.status.name if hasattr(self._snap, "status") else "OFFLINE",
                    "wsOnline":   self._ws_online,
                    "hardwareId": HARDWARE_ID,
                    "shortId":    SHORT_ID,
                    "platform":   "windows",
                    "appVersion": "1.1.0",
                }
                self._ws.send_beacon(
                    volume=int(self._volume),
                    muted=bool(self._snap_muted),
                    firmware_version="1.1.0",
                    status_payload=status_payload,
                )
            except Exception:
                pass
            time.sleep(BEACON_INTERVAL_S)

    # ── Remote parancsok (DeviceAgent callback-ek) ────────────────────────────

    def _on_remote_set_volume(self, vol: int) -> None:
        """Backend SET_VOLUME parancs → UI + Snap volume frissítés (0..10)."""
        try:
            self.ui.set_volume_display(vol)
        except Exception:
            pass
        self._handle_volume(vol)

    def _on_remote_mute(self, muted: bool) -> None:
        """Backend MUTE parancs → snap mute toggle + OS master mute."""
        self._snap_muted = muted
        try:
            self._snap.mute(muted)
        except Exception as e:
            print(f"[App] remote mute hiba: {e}")
        # OS master mute is (nircmd, ha PATH-on)
        set_system_mute(muted)

    def _on_remote_reboot(self) -> None:
        """Backend REBOOT parancs → kilépés (a launcher/systemd visszahozza)."""
        print("[App] REBOOT parancs érkezett, kilépés...")
        # Egy rövid várakozás, hogy a CMD_ACK WS-frame ténylegesen kimenjen
        # a hálózatra a kilépés előtt.
        time.sleep(1)
        sys.exit(0)

    def _on_remote_show_message(self, msg: str) -> None:
        """Backend SHOW_MESSAGE parancs → UI banner / overlay (best effort)."""
        try:
            # A PlayerUI-nak vagy show_banner vagy show_message_overlay függvénye van;
            # mindkettő best-effort-ot megpróbálunk, nem fatális ha nincs.
            if hasattr(self.ui, "show_banner"):
                self.ui.show_banner(msg)
            elif hasattr(self.ui, "show_message_overlay"):
                self.ui.show_message_overlay(msg, 10000)
            else:
                print(f"[App] SHOW_MESSAGE (UI nincs): {msg}")
        except Exception as e:
            print(f"[App] show_message hiba: {e}")

    # ── Hangerő ──────────────────────────────────────────────────────────────

    def _handle_volume(self, vol: int) -> None:
        self._volume = vol
        # 1) snapclient saját puffer-gain (csak a snap stream-re hat)
        self._snap.set_volume(int(vol * 10))
        # 2) OS master mixer is mozogjon, hogy a 10/10 tényleg max hangerő legyen
        #    (nircmd, ha telepítve van a PATH-on)
        set_system_volume(int(vol * 10))
        self._settings["volume"] = vol
        save_settings(self._settings)

    # ── Auto-update ───────────────────────────────────────────────────────────

    def _on_update_available(self, tag: str) -> None:
        self.ui.show_update_banner(f"Új verzió: {tag} – letöltés...")

    def _on_update_downloading(self, pct: int) -> None:
        self.ui.show_update_banner(f"Letöltés: {pct}%")

    def _on_update_ready(self) -> None:
        self.ui.show_update_banner(
            "Frissítés kész – kattints a telepítéshez",
            on_click=self._install_update,
        )

    def _install_update(self) -> None:
        self._updater.install_now()

    @staticmethod
    def _calc_reading_ms(text: str) -> int:
        chars = len(text.strip())
        return max(6_000, min(30_000, chars * 300))
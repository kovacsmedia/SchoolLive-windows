# schoollive_player/app.py
#
# Főalkalmazás: összeköti az UI-t, a WS klienst, a Snapcastot és az audio managert.

import time
import threading
import datetime
from typing import Optional

import api_client   as api
import audio_manager as audio
from config           import load_settings, save_settings
from snapcast_manager import SnapcastManager
from sync_client      import SyncClient
from updater_client   import AutoUpdater
from ui               import PlayerUI

BELL_POLL_INTERVAL_S  = 60
BEACON_INTERVAL_S     = 30
POLL_INTERVAL_S       = 5

class SchoolLiveApp:
    def __init__(self, ui: PlayerUI):
        self.ui         = ui
        self._settings  = load_settings()
        self._bells:    list = []
        self._status    = "registering"   # registering | pending | active
        self._online    = False
        self._last_bell_key = ""          # deduplikáció

        # Snapcast manager
        self._snap = SnapcastManager(
            on_connected    = self._on_snap_connected,
            on_disconnected = self._on_snap_disconnected,
            on_error        = self._on_snap_error,
        )

        # WS SyncCast kliens
        self._ws = SyncClient(
            on_prepare      = self._on_prepare,
            on_play         = self._on_play,
            on_immediate    = self._on_immediate,
            on_connected    = self._on_ws_connected,
            on_disconnected = self._on_ws_disconnected,
        )

        # Pending PREPARE-ok (commandId → context)
        self._pending: dict = {}

        # Auto-updater
        self._updater = AutoUpdater(
            on_update_available = self._on_update_available,
            on_downloading      = self._on_update_downloading,
            on_ready_to_install = self._on_update_ready,
            on_error            = lambda e: print(f"[Update] {e}"),
        )

        # UI callbacks
        ui.on_login          = self._handle_login
        ui.on_volume_change  = self._handle_volume

        self._volume = self._settings.get("volume", 7)
        ui.set_volume_display(self._volume)
        self._handle_volume(self._volume)

        # Indítás
        self._boot()

    # ── Boot ───────────────────────────────────────────────────────────────────
    def _boot(self) -> None:
        token = api.get_token()
        if token:
            # Token megvan → regisztráció
            threading.Thread(target=self._register, daemon=True).start()
        else:
            self.ui.show_login()

    # ── Login ──────────────────────────────────────────────────────────────────
    def _handle_login(self, email: str, password: str) -> None:
        try:
            api.login(email, password)
            self.ui.hide_login()
            self._register()
        except Exception as e:
            self.ui.set_login_error(str(e))

    # ── Regisztráció ───────────────────────────────────────────────────────────
    def _register(self) -> None:
        status = api.register_device()
        self._status = status
        if status == "active":
            self._activate()
        else:
            self.ui.show_pending()
            # Polling amíg aktív lesz
            threading.Thread(target=self._poll_activation, daemon=True).start()

    def _poll_activation(self) -> None:
        while self._status != "active":
            time.sleep(5)
            s = api.register_device()
            if s == "active":
                self._status = "active"
                self.ui.hide_pending()
                self._activate()
                return

    # ── Aktiválás ──────────────────────────────────────────────────────────────
    def _activate(self) -> None:
        # Intézmény neve a tokenből
        payload = api.decode_token_payload()
        name = payload.get("tenantName", payload.get("tenant", {}).get("name", ""))
        if name:
            self.ui.set_institution(name)

        # Snapcast indítás
        if self._snap.available:
            self._snap.start()
            self.ui.set_snap_status("⏳ Snapcast csatlakozás...")
        else:
            self.ui.set_snap_status("⚠ snapclient nem found")

        # WS SyncCast
        self._ws.start()

        # Csengetési rend
        threading.Thread(target=self._sync_bells, daemon=True).start()

        # Beacon + poll háttér taskok
        threading.Thread(target=self._beacon_loop,   daemon=True).start()
        threading.Thread(target=self._poll_loop,     daemon=True).start()
        threading.Thread(target=self._bell_tick_loop,daemon=True).start()

        # Auto-updater indítása
        self._updater.start()

    # ── Snapcast callbacks ─────────────────────────────────────────────────────
    def _on_snap_connected(self) -> None:
        self.ui.set_snap_status("🔊 Snapcast csatlakozva")

    def _on_snap_disconnected(self) -> None:
        self.ui.set_snap_status("⚠ Snapcast lecsatlakozva")

    def _on_snap_error(self, msg: str) -> None:
        self.ui.set_snap_status(f"❌ {msg[:40]}")

    # ── WS callbacks ──────────────────────────────────────────────────────────
    def _on_ws_connected(self) -> None:
        self._online = True
        self.ui.set_online(True)

    def _on_ws_disconnected(self) -> None:
        self._online = False
        self.ui.set_online(False)

    # ── SyncCast PREPARE ──────────────────────────────────────────────────────
    def _on_prepare(self, msg: dict) -> None:
        command_id = msg.get("commandId", "")
        action     = msg.get("action", "")
        url        = msg.get("url")
        snap_active = msg.get("snapcastActive", False)

        # Ha Snapcast aktív, az audio a szerveren játszik – nem kell prefetch
        if snap_active:
            self._pending[command_id] = {
                "action": action, "url": url,
                "text": msg.get("text"), "title": msg.get("title"),
                "snap_active": True,
            }
            self._ws.send_ack(command_id, 0)
            return

        # Offline mód: audio prefetch
        started = time.monotonic()
        self._pending[command_id] = {
            "action": action, "url": url,
            "text": msg.get("text"), "title": msg.get("title"),
            "snap_active": False,
        }
        buffer_ms = int((time.monotonic() - started) * 1000)
        self._ws.send_ack(command_id, buffer_ms)

    # ── SyncCast PLAY ─────────────────────────────────────────────────────────
    def _on_play(self, msg: dict) -> None:
        command_id  = msg.get("commandId", "")
        play_at_ms  = msg.get("playAtMs")
        prepare     = self._pending.pop(command_id, None)
        if not prepare:
            return

        server_now  = self._ws.clock.server_now_ms()
        delay_ms    = max(0, (play_at_ms or 0) - server_now) if play_at_ms else 0

        action      = prepare.get("action", "")
        url         = prepare.get("url")
        snap_active = prepare.get("snap_active", False)

        def _execute():
            if delay_ms > 50:
                time.sleep(delay_ms / 1000)

            # Overlay megjelenítése
            if action == "TTS" and prepare.get("text"):
                from audio_manager import play_url
                reading_ms = self._calc_reading_ms(prepare["text"])
                self.ui.show_message_overlay(prepare["text"], reading_ms)
                if not snap_active and url:
                    play_url(url, self._volume / 10)
            elif action == "PLAY_URL":
                self.ui.show_radio_overlay(prepare.get("title", "Iskolarádió"))
                if not snap_active and url:
                    audio.play_url(url, self._volume / 10)
            elif action == "BELL":
                self.ui.show_bell_banner(True)
                if not snap_active and url:
                    sound_file = url.split("/")[-1]
                    audio.play_bell(sound_file, self._volume / 10,
                                    on_done=lambda: self.ui.show_bell_banner(False))

        threading.Thread(target=_execute, daemon=True).start()

    # ── Azonnali broadcast parancsok ──────────────────────────────────────────
    def _on_immediate(self, msg: dict) -> None:
        action      = msg.get("action", "")
        snap_active = msg.get("snapcastActive", False)

        if action == "BELL":
            url = msg.get("url", "")
            now = datetime.datetime.now()
            key = f"{now.hour}:{now.minute}"
            if self._last_bell_key == key:
                return
            self._last_bell_key = key
            self.ui.show_bell_banner(True)
            if not snap_active and url:
                sound_file = url.split("/")[-1]
                audio.play_bell(sound_file, self._volume / 10,
                                on_done=lambda: self.ui.show_bell_banner(False))
            else:
                # Snapcast kezeli az audiót, csak UI
                threading.Timer(3.0, lambda: self.ui.show_bell_banner(False)).start()

        elif action == "TTS":
            url  = msg.get("url")
            text = msg.get("text", "")
            if text:
                reading_ms = self._calc_reading_ms(text)
                self.ui.show_message_overlay(text, reading_ms)
                if not snap_active and url:
                    audio.play_url(url, self._volume / 10)

        elif action == "PLAY_URL":
            title = msg.get("title", "Iskolarádió")
            url   = msg.get("url")
            self.ui.show_radio_overlay(title)
            if not snap_active and url:
                audio.play_url(url, self._volume / 10)

        elif action == "STOP_PLAYBACK":
            audio.stop()
            self.ui.hide_overlay()
            self.ui.show_bell_banner(False)

        elif action == "SYNC_BELLS":
            threading.Thread(target=self._sync_bells, daemon=True).start()

    # ── Csengetési rend ────────────────────────────────────────────────────────
    def _sync_bells(self) -> None:
        bells = api.fetch_bells()
        if bells:
            self._bells = bells
            audio.prefetch_bells(bells)
            self.ui.set_bells(bells)
            self.ui.set_cache_status(f"🔔 {len(bells)} csengő betöltve")
        else:
            self.ui.set_cache_status("⚠ Csengetési rend üres")

    # ── Offline bell ticker ────────────────────────────────────────────────────
    def _bell_tick_loop(self) -> None:
        while True:
            time.sleep(5)
            if self._status != "active" or not self._bells:
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
                None
            )
            if not due:
                continue
            # Offline bell: csak ha nincs WS kapcsolat
            if not self._online:
                self._last_bell_key = key
                self.ui.show_bell_banner(True)
                audio.play_bell(
                    due["soundFile"], self._volume / 10,
                    on_done=lambda: self.ui.show_bell_banner(False),
                )

    # ── Beacon loop ───────────────────────────────────────────────────────────
    def _beacon_loop(self) -> None:
        while True:
            api.beacon()
            time.sleep(BEACON_INTERVAL_S)

    # ── Poll loop (fallback ha nincs WS) ──────────────────────────────────────
    def _poll_loop(self) -> None:
        while True:
            time.sleep(POLL_INTERVAL_S)
            if self._online:
                continue   # WS aktív, nem kell poll
            cmd = api.poll_command()
            if cmd:
                self._on_immediate(cmd.get("payload", {}))
                api.ack_command(cmd.get("id", ""))

    # ── Hangerő ───────────────────────────────────────────────────────────────
    def _handle_volume(self, vol: int) -> None:
        self._volume = vol
        self._snap.set_volume(int(vol * 10))   # 0-10 → 0-100
        self._settings["volume"] = vol
        save_settings(self._settings)

    # ── Auto-update callbacks ──────────────────────────────────────────────────
    def _on_update_available(self, tag: str) -> None:
        self.ui.show_update_banner(f"Új verzió érhető el: {tag} – letöltés...")

    def _on_update_downloading(self, pct: int) -> None:
        self.ui.show_update_banner(f"Letöltés: {pct}%")

    def _on_update_ready(self) -> None:
        self.ui.show_update_banner(
            "Frissítés kész – kattints a telepítéshez",
            on_click=self._install_update,
        )

    def _install_update(self) -> None:
        self._updater.install_now()
        # install_now sys.exit(0)-t hív ha sikeres

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _calc_reading_ms(text: str) -> int:
        chars = len(text.strip())
        return max(6000, min(30000, chars * 300))

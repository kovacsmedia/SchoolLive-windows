# player/app.py
#
# Főalkalmazás – native provisioning flow:
#   1. provision() → pending vagy active
#   2. Ha pending: provisioning képernyő (WP-XXXXXXXX mutatva)
#   3. Poll amíg active nem lesz
#   4. Active → deviceKey-jel WS csatlakozás (mint ESP32, JWT nélkül)

import time
import threading
import datetime
from typing import Optional

import api_client   as api
import audio_manager as audio
from api_client       import DEVICE_KEY, SHORT_ID, HARDWARE_ID
from config           import load_settings, save_settings
from snapcast_manager import SnapcastManager
from sync_client      import SyncClient
from updater_client   import AutoUpdater
from ui               import PlayerUI

BEACON_INTERVAL_S = 30
POLL_INTERVAL_S   = 5

class SchoolLiveApp:
    def __init__(self, ui: PlayerUI):
        self.ui         = ui
        self._settings  = load_settings()
        self._bells:    list = []
        self._status    = "provisioning"   # provisioning | active
        self._online    = False
        self._last_bell_key = ""

        # Snapcast manager
        self._snap = SnapcastManager(
            on_connected    = self._on_snap_connected,
            on_disconnected = self._on_snap_disconnected,
            on_error        = self._on_snap_error,
        )

        # WS SyncCast kliens – deviceKey-jel csatlakozik (mint ESP32)
        self._ws = SyncClient(
            on_prepare      = self._on_prepare,
            on_play         = self._on_play,
            on_immediate    = self._on_immediate,
            on_connected    = self._on_ws_connected,
            on_disconnected = self._on_ws_disconnected,
            device_key      = DEVICE_KEY,   # JWT helyett device key
        )

        # Pending PREPARE-ok
        self._pending: dict = {}

        # UI callbacks
        ui.on_volume_change = self._handle_volume

        self._volume = self._settings.get("volume", 7)
        ui.set_volume_display(self._volume)
        self._handle_volume(self._volume)

        # Auto-updater
        self._updater = AutoUpdater(
            on_update_available = self._on_update_available,
            on_downloading      = self._on_update_downloading,
            on_ready_to_install = self._on_update_ready,
            on_error            = lambda e: print(f"[Update] {e}"),
        )

        # Indítás
        self._boot()

    # ── Boot – provisioning flow ───────────────────────────────────────────────
    def _boot(self) -> None:
        threading.Thread(target=self._provision_loop, daemon=True).start()

    def _provision_loop(self) -> None:
        """Provision és poll amíg active nem lesz."""
        # Első provision hívás
        status = api.provision()

        # Provisioning képernyő röviden megjelenik (stack alapból ott van)
        self.ui.show_pending()

        if status == "active":
            print(f"[App] Már aktivált: {SHORT_ID}")
            self.ui.hide_pending()
            self._activate()
            return

        print(f"[App] Provisioning mód: {SHORT_ID}")

        # Poll amíg aktív nem lesz
        while True:
            time.sleep(POLL_INTERVAL_S)
            status = api.poll_status()
            if status == "active":
                self.ui.hide_pending()
                self._activate()
                return
            # Provision hívás megismétlése (frissíti a lastSeenAt-t és a keyHash-t)
            api.provision()

    # ── Aktiválás ──────────────────────────────────────────────────────────────
    def _activate(self) -> None:
        print(f"[App] Aktiválva: {SHORT_ID}")
        self._status = "active"

        # Snapcast indítás
        if self._snap.available:
            self._snap.start()
            self.ui.set_snap_status("⏳ Snapcast csatlakozás...")
        else:
            self.ui.set_snap_status("⚠ snapclient nem found")

        # WS SyncCast csatlakozás
        self._ws.start()

        # Csengetési rend
        threading.Thread(target=self._sync_bells, daemon=True).start()

        # Háttér taskok
        threading.Thread(target=self._bell_tick_loop, daemon=True).start()
        threading.Thread(target=self._beacon_loop,    daemon=True).start()

        # Auto-updater
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
        command_id  = msg.get("commandId", "")
        action      = msg.get("action", "")
        url         = msg.get("url")
        snap_active = msg.get("snapcastActive", False)

        self._pending[command_id] = {
            "action": action, "url": url,
            "text": msg.get("text"), "title": msg.get("title"),
            "snap_active": snap_active,
        }
        self._ws.send_ack(command_id, 0)

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

        # Ha snap_active=True de snapclient nincs telepítve → lokális fallback
        snap_usable = snap_active and self._snap.available and self._snap.connected

        def _execute():
            if delay_ms > 50:
                time.sleep(delay_ms / 1000)

            if action == "TTS" and prepare.get("text"):
                reading_ms = self._calc_reading_ms(prepare["text"])
                self.ui.show_message_overlay(prepare["text"], reading_ms)
                if not snap_usable and url:
                    audio.play_url(url, self._volume / 10)

            elif action == "PLAY_URL":
                self.ui.show_radio_overlay(prepare.get("title", "Iskolarádió"))
                if not snap_usable and url:
                    audio.play_url(url, self._volume / 10)

            elif action == "BELL":
                self.ui.show_bell_banner(True)
                if not snap_usable and url:
                    sound_file = url.split("/")[-1]
                    audio.play_bell(sound_file, self._volume / 10,
                                    on_done=lambda: self.ui.show_bell_banner(False))
                else:
                    threading.Timer(3.0, lambda: self.ui.show_bell_banner(False)).start()

        threading.Thread(target=_execute, daemon=True).start()

    # ── Azonnali broadcast parancsok ──────────────────────────────────────────
    def _on_immediate(self, msg: dict) -> None:
        action      = msg.get("action", "")
        snap_active = msg.get("snapcastActive", False)
        snap_usable = snap_active and self._snap.available and self._snap.connected

        if action == "BELL":
            url = msg.get("url", "")
            now = datetime.datetime.now()
            key = f"{now.hour}:{now.minute}"
            if self._last_bell_key == key:
                return
            self._last_bell_key = key
            self.ui.show_bell_banner(True)
            if not snap_usable and url:
                sound_file = url.split("/")[-1]
                audio.play_bell(sound_file, self._volume / 10,
                                on_done=lambda: self.ui.show_bell_banner(False))
            else:
                threading.Timer(3.0, lambda: self.ui.show_bell_banner(False)).start()

        elif action == "TTS":
            url  = msg.get("url")
            text = msg.get("text", "")
            if text:
                reading_ms = self._calc_reading_ms(text)
                self.ui.show_message_overlay(text, reading_ms)
                if not snap_usable and url:
                    audio.play_url(url, self._volume / 10)

        elif action == "PLAY_URL":
            title = msg.get("title", "Iskolarádió")
            url   = msg.get("url")
            self.ui.show_radio_overlay(title)
            if not snap_usable and url:
                audio.play_url(url, self._volume / 10)

        elif action == "STOP_PLAYBACK":
            audio.stop()
            self.ui.hide_overlay()
            self.ui.show_bell_banner(False)

        elif action == "SYNC_BELLS":
            threading.Thread(target=self._sync_bells, daemon=True).start()

    # ── Csengetési rend ────────────────────────────────────────────────────────
    def _sync_bells(self) -> None:
        bells = api.fetch_bells(DEVICE_KEY)
        if bells:
            self._bells = bells
            audio.prefetch_bells(bells)
            self.ui.set_bells(bells)
            self.ui.set_cache_status(f"🔔 {len(bells)} csengő betöltve")
        else:
            self.ui.set_cache_status("⚠ Csengetési rend üres")

    # ── Beacon loop ───────────────────────────────────────────────────────────
    def _beacon_loop(self) -> None:
        """30 másodpercenként jelzi a backendnek hogy az eszköz online."""
        while True:
            try:
                import urllib.request, json
                from config import API_BASE
                body = json.dumps({
                    "hardwareId": HARDWARE_ID,
                    "shortId":    SHORT_ID,
                }).encode()
                req = urllib.request.Request(
                    f"{API_BASE}/devices/native/beacon",
                    data=body,
                    headers={"Content-Type": "application/json",
                             "x-device-key": DEVICE_KEY},
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass
            time.sleep(BEACON_INTERVAL_S)
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

    # ── Hangerő ───────────────────────────────────────────────────────────────
    def _handle_volume(self, vol: int) -> None:
        self._volume = vol
        self._snap.set_volume(int(vol * 10))
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

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _calc_reading_ms(text: str) -> int:
        chars = len(text.strip())
        return max(6000, min(30000, chars * 300))
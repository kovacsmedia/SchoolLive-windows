# schoollive_player/snapcast_manager.py
#
# v3 változások:
#   • SnapStatus enum: CONNECTING, CONNECTED, TIMEOUT, OFFLINE
#   • on_status_change(SnapStatus) callback – UI státuszjelzéshez
#   • 60s timeout: TIMEOUT állapotba vált, de tovább próbál újra
#   • connected detektálás log-alapú (nem hamis pozitív)

import subprocess
import threading
import time
import platform
from enum import Enum, auto
from typing import Optional, Callable
from config import API_BASE, get_snapclient_bin


class SnapStatus(Enum):
    CONNECTING = auto()   # Próbál csatlakozni (< 60s)
    CONNECTED  = auto()   # Csatlakozva, lejátszásra kész
    TIMEOUT    = auto()   # 60s után sem sikerült, de tovább próbál
    OFFLINE    = auto()   # Nincs snapclient bináris / le van állítva


def _get_snapserver_host() -> str:
    import os
    override = os.environ.get("SNAP_SERVER_HOST")
    if override:
        return override
    return API_BASE.replace("https://", "").replace("http://", "").split("/")[0]


class SnapcastManager:
    """
    Kezeli a snapclient subprocess-t.

    Állapotok:
      CONNECTING  → fut, de még nem csatlakozott (< 60s)
      CONNECTED   → csatlakozva, hang lejátszható
      TIMEOUT     → 60s eltelt, még mindig nem sikerült – de folytatja
      OFFLINE     → snapclient bináris nincs / stop() hívva

    Callbackek:
      on_connected()             → CONNECTED belépéskor
      on_disconnected()          → CONNECTED elhagyásakor
      on_status_change(status)   → minden állapotváltáskor (UI-hoz)
      on_error(msg)              → fatális hiba
    """

    CONNECT_TIMEOUT_S = 60

    # Kapcsolat megerősítő log markerek (csak ha NEM error sor)
    _CONNECTED_MARKERS    = ("connected", "audio player", "latency", "pcm")
    # Kapcsolatbontás / hiba markerek
    _DISCONNECTED_MARKERS = ("disconnected", "connection refused",
                             "host not found", "failed to resolve",
                             "operation canceled", "operation cancelled",
                             "timed out", "time sync request failed")

    def __init__(self,
                 on_connected:     Optional[Callable]                       = None,
                 on_disconnected:  Optional[Callable]                       = None,
                 on_status_change: Optional[Callable[["SnapStatus"], None]] = None,
                 on_error:         Optional[Callable[[str], None]]          = None):
        self._bin               = get_snapclient_bin()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running           = False
        self._connected         = False
        self._status            = SnapStatus.OFFLINE
        self._volume            = 100
        self._muted             = False
        self._volume_before_mute = 100
        self._server_host       = _get_snapserver_host()
        self._port              = 1704
        self._on_connected      = on_connected
        self._on_disconnected   = on_disconnected
        self._on_status_change  = on_status_change
        self._on_error          = on_error
        self._restart_delay     = 3
        self._lock              = threading.Lock()
        self._connect_start: Optional[float] = None

    # ── Publikus állapot ──────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._bin is not None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def status(self) -> "SnapStatus":
        return self._status

    # ── Konfiguráció ──────────────────────────────────────────────────────────

    def set_port(self, port: int) -> None:
        self._port = port
        print(f"[Snapcast] port: {self._port}")

    # ── Életciklus ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self.available:
            self._set_status(SnapStatus.OFFLINE)
            if self._on_error:
                self._on_error("snapclient nem található")
            return
        with self._lock:
            if self._running:
                return
            self._running = True
        self._connect_start = time.monotonic()
        self._set_status(SnapStatus.CONNECTING)
        self._launch_thread()
        self._launch_timeout_watcher()

    def stop(self) -> None:
        with self._lock:
            self._running = False
        self._kill_proc()
        self._set_status(SnapStatus.OFFLINE)

    def restart(self) -> None:
        self._kill_proc()
        with self._lock:
            was_running = self._running
            if not was_running:
                self._running = True
        self._connect_start = time.monotonic()
        if self._status != SnapStatus.CONNECTED:
            self._set_status(SnapStatus.CONNECTING)
        if not was_running:
            self._launch_thread()
        self._launch_timeout_watcher()

    # ── Hangerő / mute ────────────────────────────────────────────────────────

    def set_volume(self, volume: int) -> None:
        volume = max(0, min(100, volume))
        if self._muted:
            self._volume_before_mute = volume
        else:
            self._volume = volume
            if self._proc and self._proc.poll() is None:
                self._kill_proc()

    def mute(self, muted: bool) -> None:
        if self._muted == muted:
            return
        self._muted = muted
        print(f"[Snapcast] mute={'ON' if muted else 'OFF'}")
        if muted:
            self._volume_before_mute = self._volume
            self._volume = 0
        else:
            self._volume = self._volume_before_mute
        if self._proc and self._proc.poll() is None:
            self._kill_proc()

    # ── Belső: státusz ────────────────────────────────────────────────────────

    def _set_status(self, status: "SnapStatus") -> None:
        if self._status == status:
            return
        self._status = status
        print(f"[Snapcast] Státusz → {status.name}")
        if self._on_status_change:
            self._on_status_change(status)

    # ── Belső: timeout figyelő ────────────────────────────────────────────────

    def _launch_timeout_watcher(self) -> None:
        """60s után TIMEOUT állapot ha még nem CONNECTED."""
        start = self._connect_start

        def _watch():
            time.sleep(self.CONNECT_TIMEOUT_S)
            # Csak ha ugyanaz a csatlakozási kísérlet fut még
            if self._connect_start != start:
                return
            with self._lock:
                if not self._running:
                    return
            if not self._connected:
                print(f"[Snapcast] ⏱ {self.CONNECT_TIMEOUT_S}s timeout → TIMEOUT")
                self._set_status(SnapStatus.TIMEOUT)

        threading.Thread(target=_watch, daemon=True).start()

    # ── Belső: loop ───────────────────────────────────────────────────────────

    def _launch_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    break

            self._start_proc()   # Blokkol amíg a process él

            prev_connected = self._connected
            self._connected = False

            if prev_connected:
                if self._on_disconnected:
                    self._on_disconnected()
                # Reconnect indul → új timeout figyelő
                self._connect_start = time.monotonic()
                self._launch_timeout_watcher()
                self._set_status(SnapStatus.CONNECTING)

            with self._lock:
                if not self._running:
                    break

            time.sleep(self._restart_delay)

    def _start_proc(self) -> None:
        import shutil

        use_flatpak_spawn = (
            self._bin.startswith("/run/host/")
            and shutil.which("flatpak-spawn") is not None
        )

        common_args = [
            "--host",      self._server_host,
            "--port",      str(self._port),
            "--logfilter", "error",
            "--volume",    str(self._volume),
        ]

        args = (
            ["flatpak-spawn", "--host", self._bin.replace("/run/host", "")]
            + common_args
            if use_flatpak_spawn
            else [self._bin] + common_args
        )

        print(f"[Snapcast] Indítás: {self._server_host}:{self._port} vol={self._volume}")

        kwargs = {}
        if platform.system() == "Windows":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **kwargs,
            )

            for line in self._proc.stdout:
                decoded = line.decode(errors="replace").strip()
                if not decoded:
                    continue
                lower = decoded.lower()
                print(f"[snapclient] {decoded}")

                is_error_line = any(e in lower for e in (
                    "error", "failed", "host not found", "resolve",
                    "canceled", "cancelled", "timed out",
                ))

                if not self._connected:
                    if (any(m in lower for m in self._CONNECTED_MARKERS)
                            and not is_error_line):
                        self._connected = True
                        self._connect_start = None
                        print("[Snapcast] ✅ Kapcsolódva")
                        self._set_status(SnapStatus.CONNECTED)
                        if self._on_connected:
                            self._on_connected()
                else:
                    if any(m in lower for m in self._DISCONNECTED_MARKERS):
                        self._connected = False
                        print("[Snapcast] ❌ Kapcsolat bontva")
                        self._connect_start = time.monotonic()
                        self._launch_timeout_watcher()
                        self._set_status(SnapStatus.CONNECTING)
                        if self._on_disconnected:
                            self._on_disconnected()

        except Exception as e:
            self._connected = False
            print(f"[Snapcast] Hiba: {e}")
            if self._on_error:
                self._on_error(str(e))
        finally:
            self._proc = None

    def _kill_proc(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._proc      = None
        self._connected = False
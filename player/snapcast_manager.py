# schoollive_player/snapcast_manager.py

import subprocess
import threading
import time
import platform
from typing    import Optional, Callable
from config    import API_BASE, get_snapclient_bin

# Snapcast szerver host kinyerése az API URL-ből
def _get_snapserver_host() -> str:
    host = API_BASE.replace("https://", "").replace("http://", "").split("/")[0]
    # api.schoollive.hu → api.schoollive.hu (snapserver ugyanott fut)
    return host

class SnapcastManager:
    """
    Kezeli a snapclient subprocess-t.
    Automatikusan újraindítja ha elhal, és figyeli a kapcsolat állapotát.
    """

    def __init__(self,
                 on_connected: Optional[Callable] = None,
                 on_disconnected: Optional[Callable] = None,
                 on_error: Optional[Callable[[str], None]] = None):
        self._bin           = get_snapclient_bin()
        self._proc:  Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running       = False
        self._connected     = False
        self._volume        = 100
        self._server_host   = _get_snapserver_host()
        self._on_connected    = on_connected
        self._on_disconnected = on_disconnected
        self._on_error        = on_error
        self._restart_delay   = 3   # sec
        self._lock            = threading.Lock()

    @property
    def available(self) -> bool:
        return self._bin is not None

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        if not self.available:
            if self._on_error:
                self._on_error(
                    "snapclient nem található. Töltsd le: https://github.com/badaix/snapcast/releases"
                )
            return
        with self._lock:
            if self._running:
                return
            self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
        self._kill_proc()

    def set_volume(self, volume: int) -> None:
        """0-100"""
        self._volume = max(0, min(100, volume))
        # snapclient-nek nincs runtime volume API, újraindul az új értékkel
        if self._proc and self._proc.poll() is None:
            self._kill_proc()

    def _run_loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    break
            self._start_proc()
            # Várunk amíg a process él
            if self._proc:
                self._proc.wait()
            self._connected = False
            if self._on_disconnected:
                self._on_disconnected()
            with self._lock:
                if not self._running:
                    break
            time.sleep(self._restart_delay)

    def _start_proc(self) -> None:
        args = [
            self._bin,
            "--host",   self._server_host,
            "--port",   "1704",
            "--logfilter", "error",
            "--volume", str(self._volume),
        ]
        # Windows: elrejti a konzolablakot
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
            self._connected = True
            if self._on_connected:
                self._on_connected()

            # Logok olvasása háttérben (kapcsolat elvesztés detektáláshoz)
            for line in self._proc.stdout:
                decoded = line.decode(errors="replace").strip()
                if decoded:
                    print(f"[snapclient] {decoded}")
                    # Ha csatlakozás elvesztés jelzi
                    if "disconnected" in decoded.lower() or "error" in decoded.lower():
                        self._connected = False
                        if self._on_disconnected:
                            self._on_disconnected()

        except Exception as e:
            self._connected = False
            if self._on_error:
                self._on_error(f"snapclient hiba: {e}")

    def _kill_proc(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        self._connected = False

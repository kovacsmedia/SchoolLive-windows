# schoollive_player/config.py

import os
import json
import platform
from pathlib import Path

APP_NAME    = "SchoolLive Player"
APP_VERSION = "1.0.0"
API_BASE    = os.environ.get("SL_API_BASE", "https://api.schoollive.hu")
WS_URL      = API_BASE.replace("https://", "wss://").replace("http://", "ws://") + "/sync"

# Snapclient bináris keresési útvonalak platformonként
SNAPCLIENT_CANDIDATES_WIN = [
    r"C:\Program Files\Snapcast\snapclient.exe",
    r"C:\Program Files (x86)\Snapcast\snapclient.exe",
    str(Path.home() / "AppData" / "Local" / "Snapcast" / "snapclient.exe"),
    "snapclient.exe",   # PATH-ban van
]
SNAPCLIENT_CANDIDATES_LINUX = [
    "/usr/bin/snapclient",
    "/usr/local/bin/snapclient",
    str(Path.home() / ".local" / "bin" / "snapclient"),
    "snapclient",
]

def get_snapclient_bin() -> str | None:
    candidates = (
        SNAPCLIENT_CANDIDATES_WIN
        if platform.system() == "Windows"
        else SNAPCLIENT_CANDIDATES_LINUX
    )
    for c in candidates:
        if Path(c).is_file() or c in ("snapclient.exe", "snapclient"):
            # PATH-ban lévőt shutil-lel ellenőrizzük
            import shutil
            if Path(c).is_file() or shutil.which(c):
                return c
    return None

# Adat könyvtár (token, client_id, hangok cache)
def get_data_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".local" / "share"
    d = base / "SchoolLivePlayer"
    d.mkdir(parents=True, exist_ok=True)
    return d

# Beállítások betöltése / mentése
def load_settings() -> dict:
    p = get_data_dir() / "settings.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_settings(settings: dict) -> None:
    p = get_data_dir() / "settings.json"
    p.write_text(json.dumps(settings, indent=2), encoding="utf-8")

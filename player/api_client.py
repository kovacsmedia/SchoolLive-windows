# player/api_client.py
#
# Native player API kliens – JWT nélkül, MAC alapú provisioning
# Aktiválás után deviceKey-jel csatlakozik a WS-re (mint ESP32)

import json
import uuid
import hashlib
import platform
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing  import Optional

from config import API_BASE, get_data_dir

# ── Hardware ID (MAC cím alapú) ───────────────────────────────────────────────
def get_hardware_id() -> str:
    """
    MAC cím alapú hardware ID.
    Formátum: "aa:bb:cc:dd:ee:ff"
    Ha nem sikerül lekérni, UUID alapú fallback (elmentve).
    """
    cache = get_data_dir() / "hardware_id.txt"
    if cache.exists():
        hid = cache.read_text().strip()
        if hid:
            return hid

    # MAC cím lekérése
    try:
        mac_int = uuid.getnode()
        # Ha az uuid.getnode() szoftveres MAC-et ad (multicast bit set), jelzés
        if (mac_int >> 40) & 1:
            raise ValueError("Multicast bit set – szoftveres MAC")
        mac = ":".join(f"{(mac_int >> (8*i)) & 0xff:02x}" for i in range(5, -1, -1))
    except Exception:
        # Fallback: perzisztens UUID
        mac = str(uuid.uuid4())

    cache.write_text(mac)
    return mac

# ── Short ID (WP-XXXXXXXX) ────────────────────────────────────────────────────
def get_short_id(hardware_id: str) -> str:
    """MAC cím → determinisztikus WP-XXXXXXXX azonosító."""
    h = hashlib.sha256(hardware_id.encode()).hexdigest()[:8].upper()
    return f"WP-{h}"

# ── Device Key (az eszköz titkos kulcsa) ─────────────────────────────────────
def get_or_create_device_key() -> str:
    """
    Első indításkor random UUID generálás + mentés.
    Ezzel a kulccsal csatlakozik a WS-re aktiválás után.
    """
    key_file = get_data_dir() / "device_key.txt"
    if key_file.exists():
        key = key_file.read_text().strip()
        if key:
            return key
    key = str(uuid.uuid4())
    key_file.write_text(key)
    return key

def get_device_key_hash(device_key: str) -> str:
    """
    bcrypt hash a device key-ből.
    A backend ezt tárolja, a kliens a plain key-t.
    """
    try:
        import bcrypt
        return bcrypt.hashpw(device_key.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        # Ha nincs bcrypt, sha256 fallback (kevésbé biztonságos)
        return hashlib.sha256(device_key.encode()).hexdigest()

# ── Singleton értékek ─────────────────────────────────────────────────────────
HARDWARE_ID = get_hardware_id()
SHORT_ID    = get_short_id(HARDWARE_ID)
DEVICE_KEY  = get_or_create_device_key()
CLIENT_ID   = SHORT_ID   # kompatibilitás a sync_client.py-val

# ── HTTP helper ────────────────────────────────────────────────────────────────
def _request(method: str, path: str, body: Optional[dict] = None,
             timeout: int = 8, token: Optional[str] = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(
        f"{API_BASE}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.reason}")

# ── Native Player Provisioning ────────────────────────────────────────────────

def provision() -> str:
    """
    Regisztrálja az eszközt a backenddel.
    Visszatér: "active" vagy "pending"
    
    - Első hívásnál a backend létrehozza a pending rekordot
    - Admin aktiválás után "active" lesz
    """
    device_key_hash = get_device_key_hash(DEVICE_KEY)

    try:
        resp = _request("POST", "/devices/native/provision", {
            "hardwareId":    HARDWARE_ID,
            "deviceKeyHash": device_key_hash,
            "shortId":       SHORT_ID,
            "platform":      platform.system().lower(),  # "windows" | "linux"
            "version":       "1.0.0",
            "userAgent":     f"{platform.system()} {platform.release()}",
        })
        return resp.get("status", "pending")
    except Exception as e:
        print(f"[API] Provisioning hiba: {e}")
        return "pending"

def get_cached_device_id() -> Optional[str]:
    p = get_data_dir() / "device_id.txt"
    if p.exists():
        return p.read_text().strip() or None
    return None

def save_device_id(device_id: str) -> None:
    (get_data_dir() / "device_id.txt").write_text(device_id)

def poll_status() -> str:
    """Ellenőrzi hogy az eszköz aktív-e már."""
    try:
        resp = _request("GET", f"/devices/native/status/{HARDWARE_ID}")
        return resp.get("status", "pending")
    except Exception:
        return "pending"

# ── Bell lekérés ──────────────────────────────────────────────────────────────
def fetch_bells(device_key: str) -> list:
    """
    Csengetési rend lekérése – device key auth headerrel.
    A backend az x-device-key headert fogadja el.
    """
    try:
        headers = {"Content-Type": "application/json", "x-device-key": device_key}
        req = urllib.request.Request(
            f"{API_BASE}/bells/today",
            headers=headers,
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            return data.get("bells", [])
    except Exception as e:
        print(f"[API] fetchBells hiba: {e}")
        return []

def fetch_tenant_name(device_key: str) -> Optional[str]:
    """Visszaadja az intézmény nevét device key alapján."""
    try:
        req = urllib.request.Request(
            f"{API_BASE}/devices/native/info",
            headers={"x-device-key": device_key},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            return data.get("tenantName")
    except Exception as e:
        print(f"[API] fetch_tenant_name hiba: {e}")
        return None
    """Unix ms"""
    try:
        resp = _request("GET", "/time")
        return resp.get("now")
    except Exception:
        return None
# schoollive_player/api_client.py

import json
import uuid
import platform
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing  import Optional
from config  import API_BASE, get_data_dir

# ── Client ID (perzisztens) ────────────────────────────────────────────────────
def get_or_create_client_id() -> str:
    p = get_data_dir() / "client_id.txt"
    if p.exists():
        cid = p.read_text().strip()
        if cid:
            return cid
    cid = str(uuid.uuid4())
    p.write_text(cid)
    return cid

CLIENT_ID = get_or_create_client_id()

# ── Token tárolás ──────────────────────────────────────────────────────────────
_token: Optional[str] = None
_token_lock = threading.Lock()

def set_token(token: str) -> None:
    global _token
    with _token_lock:
        _token = token
    p = get_data_dir() / "token.txt"
    p.write_text(token)

def get_token() -> Optional[str]:
    global _token
    with _token_lock:
        if _token:
            return _token
    p = get_data_dir() / "token.txt"
    if p.exists():
        t = p.read_text().strip()
        if t:
            with _token_lock:
                _token = t
            return t
    return None

def clear_token() -> None:
    global _token
    with _token_lock:
        _token = None
    p = get_data_dir() / "token.txt"
    if p.exists():
        p.unlink()

# ── HTTP helper ────────────────────────────────────────────────────────────────
def _request(method: str, path: str, body: Optional[dict] = None,
             timeout: int = 8) -> dict:
    token = get_token()
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

# ── Auth ───────────────────────────────────────────────────────────────────────
def login(email: str, password: str) -> dict:
    resp = _request("POST", "/auth/login", {"email": email, "password": password})
    if resp.get("accessToken"):
        set_token(resp["accessToken"])
        # Hitelesítő adatok mentése auto-reloginhoz
        p = get_data_dir() / "credentials.json"
        p.write_text(json.dumps({"email": email, "password": password}))
    return resp

def relogin() -> bool:
    p = get_data_dir() / "credentials.json"
    if not p.exists():
        return False
    try:
        creds = json.loads(p.read_text())
        login(creds["email"], creds["password"])
        return True
    except Exception:
        return False

def decode_token_payload() -> dict:
    token = get_token()
    if not token:
        return {}
    try:
        import base64
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        pad  = parts[1] + "=" * (4 - len(parts[1]) % 4)
        data = base64.urlsafe_b64decode(pad)
        return json.loads(data)
    except Exception:
        return {}

# ── Player regisztráció ────────────────────────────────────────────────────────
def register_device() -> str:
    """Visszatér: 'active' vagy 'pending'"""
    try:
        resp = _request("POST", "/player/device/register", {
            "clientId":  CLIENT_ID,
            "userAgent": f"SchoolLivePlayer/{platform.system()}/{platform.release()}",
        })
        return resp.get("status", "pending")
    except Exception:
        return "pending"

def poll_command() -> Optional[dict]:
    """Poll egy parancsot, visszatér a command dict-tel vagy None-nal."""
    try:
        resp = _request("POST", "/player/device/poll", {})
        if resp.get("status") == "active" and resp.get("command"):
            return resp["command"]
    except RuntimeError as e:
        if "401" in str(e):
            relogin()
    except Exception:
        pass
    return None

def ack_command(command_id: str) -> None:
    try:
        _request("POST", "/player/device/ack", {"commandId": command_id})
    except Exception:
        pass

def beacon() -> None:
    try:
        _request("POST", "/player/device/beacon", {"clientId": CLIENT_ID})
    except Exception:
        pass

def fetch_bells() -> list:
    try:
        resp = _request("GET", "/bells/today")
        return resp.get("bells", [])
    except Exception:
        return []

def fetch_server_time() -> Optional[int]:
    """Unix ms"""
    try:
        resp = _request("GET", "/time")
        return resp.get("now")
    except Exception:
        return None

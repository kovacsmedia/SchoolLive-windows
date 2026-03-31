# player/audio_manager.py
# Bővítések:
#   • pause_music()      – leállít, visszaadja az eltelt ms-t
#   • resume_url(ms)     – folytatja az utoljára letöltött fájlt adott pozíciótól
#   • play_url skip_ms   – letöltés után adott pozíciótól indul
#   • _state             – modul szintű állapot a resume-hoz

import os
import time
import threading
import tempfile
import urllib.request
from pathlib import Path
from typing  import Optional, Callable
from config  import API_BASE, get_data_dir

try:
    import pygame
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
    PYGAME_AVAILABLE = True
    print("[Audio] pygame inicializálva")
except Exception as e:
    PYGAME_AVAILABLE = False
    print(f"[Audio] pygame nem elérhető: {e}")

_cache_dir = get_data_dir() / "bells_cache"
_cache_dir.mkdir(exist_ok=True)

_lock = threading.Lock()

# ── Lejátszási állapot (resume-hoz) ──────────────────────────────────────────
_current_tmp_path:  Optional[str]   = None  # jelenlegi URL letöltés temp fájlja
_current_skip_ms:   int             = 0     # hány ms-t ugrottunk a fájl elejéről
_current_started_at: float          = 0.0   # time.time() a play() híváskor
_current_volume:    float           = 0.5
_current_url:       Optional[str]   = None  # eredeti URL (ha újra kell tölteni)

def _safe_vol(volume: float) -> float:
    return max(0.0, min(0.5, volume * 0.5))

# ── Bell cache ────────────────────────────────────────────────────────────────

def _cache_path(sound_file: str) -> Path:
    return _cache_dir / sound_file

def prefetch_bell(sound_file: str) -> None:
    def _fetch():
        dest = _cache_path(sound_file)
        if dest.exists():
            return
        try:
            url = f"{API_BASE}/audio/bells/{sound_file}"
            urllib.request.urlretrieve(url, dest)
            print(f"[Audio] Cached: {sound_file}")
        except Exception as e:
            print(f"[Audio] Fetch failed: {sound_file}: {e}")
    threading.Thread(target=_fetch, daemon=True).start()

def prefetch_bells(bells: list) -> None:
    seen = set()
    for b in bells:
        sf = b.get("soundFile", "")
        if sf and sf not in seen:
            seen.add(sf)
            prefetch_bell(sf)

# ── Aktuális pozíció lekérése ─────────────────────────────────────────────────

def get_elapsed_ms() -> int:
    """Visszaadja a jelenlegi lejátszás eltelt ms-ét (skip + valódi pozíció)."""
    if not PYGAME_AVAILABLE:
        return 0
    try:
        pos = pygame.mixer.music.get_pos()  # ms az aktuális play() hívás óta, -1 ha nem szól
        if pos < 0:
            return 0
        return _current_skip_ms + pos
    except Exception:
        return 0

# ── Pause (interrupt-hoz) ─────────────────────────────────────────────────────

def pause_music() -> int:
    """
    Megállítja a lejátszást, visszaadja az eltelt ms-t.
    A _current_tmp_path megmarad, resume_url() használható utána.
    """
    elapsed = get_elapsed_ms()
    if PYGAME_AVAILABLE:
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
    print(f"[Audio] Pause @ {elapsed}ms")
    return elapsed

# ── Bell lejátszás ────────────────────────────────────────────────────────────

def play_bell(sound_file: str, volume: float = 0.7,
              on_done: Optional[Callable] = None) -> None:
    if not PYGAME_AVAILABLE:
        print(f"[Audio] pygame nem elérhető, bell kihagyva: {sound_file}")
        if on_done:
            on_done()
        return

    def _play():
        global _current_skip_ms, _current_started_at, _current_volume, _current_url
        with _lock:
            dest = _cache_path(sound_file)
            if not dest.exists():
                try:
                    url = f"{API_BASE}/audio/bells/{sound_file}"
                    urllib.request.urlretrieve(url, dest)
                except Exception as e:
                    print(f"[Audio] Bell letöltés sikertelen: {sound_file}: {e}")
                    if on_done:
                        on_done()
                    return
            try:
                _current_skip_ms    = 0
                _current_started_at = time.time()
                _current_volume     = volume
                _current_url        = None
                pygame.mixer.music.load(str(dest))
                pygame.mixer.music.set_volume(_safe_vol(volume))
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.wait(100)
            except Exception as e:
                print(f"[Audio] Bell lejátszás hiba: {sound_file}: {e}")
            finally:
                if on_done:
                    on_done()

    threading.Thread(target=_play, daemon=True).start()

# ── URL lejátszás (TTS fallback) ──────────────────────────────────────────────

def play_url(url: str, volume: float = 0.7,
             on_done: Optional[Callable] = None,
             skip_ms: int = 0) -> None:
    """
    Letölti és lejátssza az URL-t.
    skip_ms: hány ms-t ugorjon a fájl elejéről (resume-hoz).
    A letöltött temp fájl elérhetővé válik pause_music() + resume_url() hívásokhoz.
    """
    if not PYGAME_AVAILABLE:
        print(f"[Audio] pygame nem elérhető, URL kihagyva: {url[:60]}")
        if on_done:
            on_done()
        return

    print(f"[Audio] play_url: {url[:80]} skip={skip_ms}ms")

    def _play():
        global _current_tmp_path, _current_skip_ms, _current_started_at
        global _current_volume, _current_url

        tmp_path = None
        try:
            suffix = ".wav" if ".wav" in url else ".mp3" if ".mp3" in url else ".mp3"
            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            os.close(fd)
            urllib.request.urlretrieve(url, tmp_path)
            print(f"[Audio] Letöltve ({os.path.getsize(tmp_path)} byte), lejátszás skip={skip_ms}ms")

            with _lock:
                # Tároljuk a temp fájl elérési útját a resume-hoz
                _current_tmp_path   = tmp_path
                _current_skip_ms    = skip_ms
                _current_started_at = time.time()
                _current_volume     = volume
                _current_url        = url

                pygame.mixer.music.load(tmp_path)
                pygame.mixer.music.set_volume(_safe_vol(volume))
                pygame.mixer.music.play(0, skip_ms / 1000.0)  # skip_ms → másodperc
                while pygame.mixer.music.get_busy():
                    pygame.time.wait(100)
            print("[Audio] Lejátszás kész")

        except Exception as e:
            print(f"[Audio] play_url hiba: {e}")
        finally:
            # Csak akkor töröljük ha ez az aktuális fájl (nem pause alatt)
            if tmp_path and tmp_path == _current_tmp_path:
                _current_tmp_path = None
                _current_url      = None
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            if on_done:
                on_done()

    threading.Thread(target=_play, daemon=True).start()

# ── Resume (pause után folytatás) ─────────────────────────────────────────────

def resume_url(skip_ms: int, volume: Optional[float] = None,
               on_done: Optional[Callable] = None) -> bool:
    """
    Folytatja az URL lejátszást skip_ms pozíciótól.
    Ha a temp fájl még elérhető: helyi seek.
    Ha már nincs: újra letölti az eredeti URL-t.
    Visszaad True-t ha sikerült indítani.
    """
    global _current_url

    vol = volume if volume is not None else _current_volume
    saved_url = _current_url

    if _current_tmp_path and os.path.exists(_current_tmp_path):
        # Temp fájl még megvan → helyi seek
        tmp = _current_tmp_path
        def _resume_local():
            global _current_skip_ms, _current_started_at
            with _lock:
                try:
                    _current_skip_ms    = skip_ms
                    _current_started_at = time.time()
                    pygame.mixer.music.load(tmp)
                    pygame.mixer.music.set_volume(_safe_vol(vol))
                    pygame.mixer.music.play(0, skip_ms / 1000.0)
                    while pygame.mixer.music.get_busy():
                        pygame.time.wait(100)
                except Exception as e:
                    print(f"[Audio] resume_local hiba: {e}")
                finally:
                    if on_done:
                        on_done()
        print(f"[Audio] Resume (local) @ {skip_ms}ms")
        threading.Thread(target=_resume_local, daemon=True).start()
        return True

    elif saved_url:
        # Temp fájl már törölve → újra letöltés, seek-kel
        print(f"[Audio] Resume (re-download) @ {skip_ms}ms")
        play_url(saved_url, vol, on_done, skip_ms=skip_ms)
        return True

    else:
        print("[Audio] Resume: nincs mit folytatni")
        if on_done:
            on_done()
        return False

# ── Stop ──────────────────────────────────────────────────────────────────────

def stop() -> None:
    global _current_tmp_path, _current_url, _current_skip_ms
    if PYGAME_AVAILABLE:
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
    # Temp fájl törlése
    if _current_tmp_path and os.path.exists(_current_tmp_path):
        try:
            os.unlink(_current_tmp_path)
        except Exception:
            pass
    _current_tmp_path = None
    _current_url      = None
    _current_skip_ms  = 0
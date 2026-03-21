# schoollive_player/audio_manager.py
#
# Offline bell fallback: ha a Snapcast szerver nem elérhető,
# a csengetési hangokat lokálisan játssza le pygame-mel.
# Hangfájlok cache-elve a data dir-ben.

import os
import threading
import urllib.request
from pathlib  import Path
from typing   import Optional
from config   import API_BASE, get_data_dir

try:
    import pygame
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
    PYGAME_AVAILABLE = True
except Exception:
    PYGAME_AVAILABLE = False

_cache_dir = get_data_dir() / "bells_cache"
_cache_dir.mkdir(exist_ok=True)

_lock = threading.Lock()

def _cache_path(sound_file: str) -> Path:
    return _cache_dir / sound_file

def prefetch_bell(sound_file: str) -> None:
    """Háttérben letölti és cache-eli a hangfájlt."""
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

def play_bell(sound_file: str, volume: float = 1.0,
              on_done: Optional[callable] = None) -> None:
    """
    Lejátssza a bell hangfájlt pygame-mel.
    on_done callback a lejátszás végén.
    """
    if not PYGAME_AVAILABLE:
        print(f"[Audio] pygame nem elérhető, bell kihagyva: {sound_file}")
        if on_done:
            on_done()
        return

    def _play():
        with _lock:
            dest = _cache_path(sound_file)
            if not dest.exists():
                # Szinkron letöltés (most kell)
                try:
                    url = f"{API_BASE}/audio/bells/{sound_file}"
                    urllib.request.urlretrieve(url, dest)
                except Exception as e:
                    print(f"[Audio] Letöltés sikertelen: {sound_file}: {e}")
                    if on_done:
                        on_done()
                    return
            try:
                pygame.mixer.music.load(str(dest))
                pygame.mixer.music.set_volume(max(0.0, min(1.0, volume)))
                pygame.mixer.music.play()
                # Várakozás a lejátszás végéig
                while pygame.mixer.music.get_busy():
                    pygame.time.wait(100)
            except Exception as e:
                print(f"[Audio] Lejátszás hiba: {sound_file}: {e}")
            finally:
                if on_done:
                    on_done()

    threading.Thread(target=_play, daemon=True).start()

def play_url(url: str, volume: float = 1.0) -> None:
    """TTS/rádió URL lejátszása (Snapcast fallback)."""
    if not PYGAME_AVAILABLE:
        return

    def _play():
        try:
            pygame.mixer.music.load(url)
            pygame.mixer.music.set_volume(max(0.0, min(1.0, volume)))
            pygame.mixer.music.play()
        except Exception as e:
            print(f"[Audio] URL lejátszás hiba: {url}: {e}")

    threading.Thread(target=_play, daemon=True).start()

def stop() -> None:
    if PYGAME_AVAILABLE:
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass

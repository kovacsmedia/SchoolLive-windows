#!/usr/bin/env python3
# updater/updater.py
#
# SchoolLiveUpdater – önálló kis bináris.
# Argumentumok:
#   --pid    <PID>      a főalkalmazás process ID-ja
#   --old    <PATH>     a jelenlegi exe teljes útvonala
#   --new    <PATH>     az új (letöltött) exe teljes útvonala
#   --restart           újraindítja az alkalmazást telepítés után
#
# Flow:
#   1. Vár amíg a főalkalmazás (PID) kilép (max 30mp)
#   2. Felülírja az old exe-t a new exe-vel
#   3. Ha --restart: elindítja az új exe-t
#   4. Kilép

import sys
import os
import time
import shutil
import argparse
import platform
import subprocess

def wait_for_pid(pid: int, timeout: float = 30.0) -> bool:
    """Vár amíg a folyamat kilép. True ha sikeresen kilépett."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if platform.system() == "Windows":
                import ctypes
                SYNCHRONIZE = 0x00100000
                handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
                if handle == 0:
                    return True   # folyamat már nem létezik
                result = ctypes.windll.kernel32.WaitForSingleObject(handle, 1000)
                ctypes.windll.kernel32.CloseHandle(handle)
                if result == 0:   # WAIT_OBJECT_0 = kilépett
                    return True
            else:
                # Linux/macOS: kill(pid, 0) ha OSError → nem létezik
                os.kill(pid, 0)
                time.sleep(0.5)
        except (ProcessLookupError, PermissionError, OSError):
            return True
    return False

def replace_exe(old_path: str, new_path: str) -> bool:
    """Felülírja az old exe-t a new exe-vel."""
    old = os.path.abspath(old_path)
    new = os.path.abspath(new_path)

    if not os.path.exists(new):
        print(f"[Updater] Hiba: új exe nem található: {new}")
        return False

    # Backup az előző verzióból (rollback lehetőség)
    backup = old + ".bak"
    try:
        if os.path.exists(backup):
            os.remove(backup)
        shutil.copy2(old, backup)
        print(f"[Updater] Backup: {backup}")
    except Exception as e:
        print(f"[Updater] Backup hiba (folytatás): {e}")

    # Felülírás – Windows-on a futó exe nem törölhető, ezért átnevezzük
    try:
        if platform.system() == "Windows":
            old_rename = old + ".old"
            if os.path.exists(old_rename):
                os.remove(old_rename)
            os.rename(old, old_rename)
            shutil.copy2(new, old)
            try:
                os.remove(old_rename)
            except Exception:
                pass  # következő indításkor töröljük
        else:
            shutil.copy2(new, old)
            os.chmod(old, 0o755)

        print(f"[Updater] ✅ Frissítés kész: {old}")
        return True

    except Exception as e:
        print(f"[Updater] Felülírás hiba: {e}")
        # Rollback
        try:
            if os.path.exists(backup):
                shutil.copy2(backup, old)
                print("[Updater] Rollback sikerült")
        except Exception:
            pass
        return False

def restart_app(exe_path: str) -> None:
    kwargs = {}
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe_path], **kwargs)
    print(f"[Updater] Újraindítva: {exe_path}")

def cleanup_temp(new_path: str) -> None:
    try:
        os.remove(new_path)
    except Exception:
        pass

def main():
    parser = argparse.ArgumentParser(description="SchoolLive Player Updater")
    parser.add_argument("--pid",     type=int, required=True,  help="Főalkalmazás PID")
    parser.add_argument("--old",     type=str, required=True,  help="Jelenlegi exe útvonala")
    parser.add_argument("--new",     type=str, required=True,  help="Új exe útvonala")
    parser.add_argument("--restart", action="store_true",      help="Újraindítás telepítés után")
    args = parser.parse_args()

    print(f"[Updater] SchoolLive Updater indult")
    print(f"[Updater] PID: {args.pid} | old: {args.old} | new: {args.new}")

    # 1. Várakozás a főalkalmazás kilépésére
    print(f"[Updater] Várakozás a főalkalmazás ({args.pid}) kilépésére...")
    exited = wait_for_pid(args.pid, timeout=30.0)
    if not exited:
        print("[Updater] Timeout – a főalkalmazás nem állt le 30 másodperc alatt")
        # Megpróbáljuk így is
    else:
        print("[Updater] Főalkalmazás kilépett")

    # Kis szünet hogy az OS elengedje a fájlt
    time.sleep(1.0)

    # 2. Felülírás
    success = replace_exe(args.old, args.new)

    # 3. Temp fájl törlése
    cleanup_temp(args.new)

    # 4. Újraindítás
    if success and args.restart:
        time.sleep(0.5)
        restart_app(args.old)   # az old út már az új exe

    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()

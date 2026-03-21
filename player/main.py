#!/usr/bin/env python3
# schoollive_player/main.py

import tkinter as tk
import sys
import os

# Ha PyInstaller bundle-ból fut, az erőforrások a _MEIPASS-ban vannak
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))

from ui  import PlayerUI
from app import SchoolLiveApp

def main():
    root = tk.Tk()

    # DPI awareness Windows-on (éles szöveg HiDPI kijelzőn)
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # Ikon beállítása ha van
    try:
        root.iconbitmap("schoollive.ico")
    except Exception:
        pass

    ui  = PlayerUI(root)
    app = SchoolLiveApp(ui)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()

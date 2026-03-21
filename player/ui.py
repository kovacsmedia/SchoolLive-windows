# schoollive_player/ui.py
#
# Teljes képernyős dark UI – ugyanolyan feeling mint a VirtualPlayer.tsx

import tkinter as tk
from tkinter import font as tkfont
import threading
import time
from typing import Optional

# ── Színpaletta (megegyezik a VP CSS-sel) ─────────────────────────────────────
BG          = "#07101f"
BG2         = "#0d1b2e"
BORDER      = "#1a2d47"
TEXT        = "#f0f6ff"
TEXT_MUTED  = "#8da4c0"
TEXT_DIM    = "#4a6280"
BLUE        = "#3b82f6"
GREEN       = "#22c55e"
RED         = "#ef4444"
AMBER       = "#f59e0b"

class PlayerUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._setup_window()
        self._build_ui()
        self._clock_running = True
        self._tick()

    # ── Ablak beállítás ────────────────────────────────────────────────────────
    def _setup_window(self) -> None:
        self.root.title("SchoolLive Player")
        self.root.configure(bg=BG)
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>",   lambda e: self._toggle_fullscreen())
        self.root.bind("<F11>",      lambda e: self._toggle_fullscreen())
        self.root.bind("<F4>",       lambda e: self.root.destroy())
        self.root.bind("<Key>",      self._on_key)
        self._fullscreen = True

    def _toggle_fullscreen(self) -> None:
        self._fullscreen = not self._fullscreen
        self.root.attributes("-fullscreen", self._fullscreen)

    def _on_key(self, event) -> None:
        # Hangerő: + / -
        if event.char in ("+", "="):
            self._vol_up()
        elif event.char == "-":
            self._vol_down()

    # ── UI építés ─────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        # ── Header ────────────────────────────────────────────────────────────
        self.header = tk.Frame(self.root, bg=BG2, height=64)
        self.header.pack(fill=tk.X, side=tk.TOP)
        self.header.pack_propagate(False)

        # Bal: brand + intézmény neve
        left = tk.Frame(self.header, bg=BG2)
        left.pack(side=tk.LEFT, padx=28, pady=8)
        tk.Label(left, text="SchoolLive", bg=BG2, fg=BLUE,
                 font=("Segoe UI", 14, "bold")).pack(anchor="w")
        self.lbl_inst = tk.Label(left, text="", bg=BG2, fg=TEXT_MUTED,
                                 font=("Segoe UI", 11))
        self.lbl_inst.pack(anchor="w")

        # Jobb: státusz
        right = tk.Frame(self.header, bg=BG2)
        right.pack(side=tk.RIGHT, padx=28)
        status_row = tk.Frame(right, bg=BG2)
        status_row.pack(side=tk.RIGHT)
        self.canvas_dot = tk.Canvas(status_row, width=12, height=12,
                                    bg=BG2, highlightthickness=0)
        self.canvas_dot.pack(side=tk.LEFT)
        self._dot = self.canvas_dot.create_oval(2, 2, 10, 10, fill=RED, outline="")
        self.lbl_status = tk.Label(status_row, text="Offline", bg=BG2,
                                   fg=TEXT_DIM, font=("Segoe UI", 11, "bold"))
        self.lbl_status.pack(side=tk.LEFT, padx=(4, 0))

        # Snapcast státusz
        self.lbl_snap = tk.Label(right, text="", bg=BG2, fg=TEXT_DIM,
                                 font=("Segoe UI", 10))
        self.lbl_snap.pack(side=tk.RIGHT, padx=(0, 12))

        # ── Középső terület ────────────────────────────────────────────────────
        self.center = tk.Frame(self.root, bg=BG)
        self.center.pack(fill=tk.BOTH, expand=True)

        # Óra
        self.lbl_clock = tk.Label(
            self.center, text="--:--:--", bg=BG, fg=TEXT,
            font=("Segoe UI", 96, "bold"),
        )
        self.lbl_clock.place(relx=0.5, rely=0.38, anchor="center")

        # Dátum
        self.lbl_date = tk.Label(
            self.center, text="", bg=BG, fg=TEXT_MUTED,
            font=("Segoe UI", 22),
        )
        self.lbl_date.place(relx=0.5, rely=0.56, anchor="center")

        # Következő csengetés
        self.bell_frame = tk.Frame(self.center, bg=BG2,
                                   highlightbackground=BORDER,
                                   highlightthickness=1)
        self.bell_frame.place(relx=0.5, rely=0.68, anchor="center")
        tk.Label(self.bell_frame, text="🔔", bg=BG2,
                 font=("Segoe UI", 16)).pack(side=tk.LEFT, padx=(16, 8), pady=10)
        tk.Label(self.bell_frame, text="Következő csengetés:", bg=BG2,
                 fg=TEXT_MUTED, font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        self.lbl_next_bell = tk.Label(self.bell_frame, text="--:--", bg=BG2,
                                      fg=TEXT, font=("Segoe UI", 20, "bold"))
        self.lbl_next_bell.pack(side=tk.LEFT, padx=(8, 16), pady=10)
        self.bell_frame.place_forget()

        # ── Csengetés banner (felső sáv) ──────────────────────────────────────
        self.bell_banner = tk.Frame(self.root, bg=AMBER, height=40)
        self.lbl_banner  = tk.Label(self.bell_banner,
                                    text="🔔 Csengetés folyamatban",
                                    bg=AMBER, fg="white",
                                    font=("Segoe UI", 13, "bold"))
        self.lbl_banner.pack(expand=True)
        self._banner_visible = False

        # ── Üzenet overlay ─────────────────────────────────────────────────────
        self.overlay = tk.Frame(self.root, bg=BG)
        self._overlay_visible = False

        # Overlay tartalom
        self.lbl_msg_text = tk.Label(
            self.overlay, text="", bg=BG, fg=TEXT,
            font=("Segoe UI", 32, "bold"),
            wraplength=0, justify="center",
        )
        self.lbl_msg_text.place(relx=0.5, rely=0.45, anchor="center")

        # Progress sáv
        self.prog_canvas = tk.Canvas(self.overlay, bg=BORDER,
                                     height=6, highlightthickness=0)
        self.prog_canvas.place(relx=0.5, rely=0.72, anchor="center",
                               relwidth=0.5)
        self._prog_bar = None
        self._prog_pct = 0.0

        # Rádió overlay
        self.lbl_radio_icon  = tk.Label(self.overlay, text="📻", bg=BG,
                                        font=("Segoe UI", 56))
        self.lbl_radio_title = tk.Label(self.overlay, text="Iskolarádió",
                                        bg=BG, fg=BLUE,
                                        font=("Segoe UI", 44, "bold"))
        self.lbl_radio_time  = tk.Label(self.overlay, text="", bg=BG,
                                        fg=TEXT_MUTED, font=("Segoe UI", 18))

        # ── Footer ─────────────────────────────────────────────────────────────
        self.footer = tk.Frame(self.root, bg=BG2, height=44)
        self.footer.pack(fill=tk.X, side=tk.BOTTOM)
        self.footer.pack_propagate(False)

        # Device ID
        from api_client import CLIENT_ID
        short_id = "WP-" + CLIENT_ID[:8].upper()
        tk.Label(self.footer, text=f"📱 {short_id}", bg=BG2, fg=TEXT_DIM,
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=20, pady=12)

        # Hangerő
        vol_frame = tk.Frame(self.footer, bg=BG2)
        vol_frame.pack(side=tk.RIGHT, padx=20, pady=8)
        tk.Button(vol_frame, text="−", bg=BG2, fg=TEXT_MUTED,
                  font=("Segoe UI", 12), relief="flat", bd=0,
                  command=self._vol_down,
                  activebackground=BORDER, cursor="hand2").pack(side=tk.LEFT)
        self.lbl_vol = tk.Label(vol_frame, text="7", bg=BG2, fg=TEXT_MUTED,
                                font=("Segoe UI", 11), width=3)
        self.lbl_vol.pack(side=tk.LEFT)
        tk.Button(vol_frame, text="+", bg=BG2, fg=TEXT_MUTED,
                  font=("Segoe UI", 12), relief="flat", bd=0,
                  command=self._vol_up,
                  activebackground=BORDER, cursor="hand2").pack(side=tk.LEFT)
        tk.Label(vol_frame, text="🔊", bg=BG2,
                 font=("Segoe UI", 12)).pack(side=tk.LEFT, padx=(4, 0))

        # Bell cache státusz
        self.lbl_cache = tk.Label(self.footer, text="", bg=BG2, fg=TEXT_DIM,
                                  font=("Segoe UI", 10))
        self.lbl_cache.pack(side=tk.LEFT, padx=8)

        # ── Login overlay ──────────────────────────────────────────────────────
        self._build_login_overlay()

        # ── Pending overlay ────────────────────────────────────────────────────
        self._build_pending_overlay()

        # Hangerő callback
        self.on_volume_change: Optional[callable] = None
        self._volume = 7

    # ── Login overlay ──────────────────────────────────────────────────────────
    def _build_login_overlay(self) -> None:
        self.login_overlay = tk.Frame(self.root, bg=BG)
        self.login_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)

        inner = tk.Frame(self.login_overlay, bg=BG2,
                         highlightbackground=BORDER, highlightthickness=1)
        inner.place(relx=0.5, rely=0.5, anchor="center", width=400)

        tk.Label(inner, text="🔊", bg=BG2,
                 font=("Segoe UI", 56)).pack(pady=(32, 4))
        tk.Label(inner, text="SchoolLive Player", bg=BG2, fg=TEXT,
                 font=("Segoe UI", 22, "bold")).pack()
        tk.Label(inner, text="Bejelentkezés", bg=BG2, fg=TEXT_MUTED,
                 font=("Segoe UI", 13)).pack(pady=(4, 24))

        # Email
        tk.Label(inner, text="Email", bg=BG2, fg=TEXT_MUTED,
                 font=("Segoe UI", 11)).pack(anchor="w", padx=32)
        self.entry_email = tk.Entry(inner, bg=BORDER, fg=TEXT, insertbackground=TEXT,
                                    font=("Segoe UI", 13), relief="flat",
                                    highlightthickness=1,
                                    highlightbackground=BORDER,
                                    highlightcolor=BLUE)
        self.entry_email.pack(fill=tk.X, padx=32, pady=(2, 12), ipady=8)

        # Jelszó
        tk.Label(inner, text="Jelszó", bg=BG2, fg=TEXT_MUTED,
                 font=("Segoe UI", 11)).pack(anchor="w", padx=32)
        self.entry_pass = tk.Entry(inner, bg=BORDER, fg=TEXT, insertbackground=TEXT,
                                   font=("Segoe UI", 13), relief="flat", show="•",
                                   highlightthickness=1,
                                   highlightbackground=BORDER,
                                   highlightcolor=BLUE)
        self.entry_pass.pack(fill=tk.X, padx=32, pady=(2, 8), ipady=8)
        self.entry_pass.bind("<Return>", lambda e: self._do_login())

        self.lbl_login_err = tk.Label(inner, text="", bg=BG2, fg=RED,
                                      font=("Segoe UI", 11))
        self.lbl_login_err.pack(pady=(0, 8))

        self.btn_login = tk.Button(
            inner, text="▶ Belépés", bg=BLUE, fg="white",
            font=("Segoe UI", 13, "bold"), relief="flat", bd=0,
            padx=24, pady=10, cursor="hand2",
            command=self._do_login,
        )
        self.btn_login.pack(pady=(0, 32))

        self.on_login: Optional[callable] = None

    def _build_pending_overlay(self) -> None:
        self.pending_overlay = tk.Frame(self.root, bg=BG)

        tk.Label(self.pending_overlay, text="📱", bg=BG,
                 font=("Segoe UI", 64)).pack(pady=(80, 8))
        tk.Label(self.pending_overlay, text="Virtuális lejátszó", bg=BG, fg=TEXT,
                 font=("Segoe UI", 24, "bold")).pack()
        tk.Label(self.pending_overlay,
                 text="Ez az eszköz még nincs aktiválva.\n"
                      "Kérj meg egy rendszergazdát,\n"
                      "hogy aktiválja az Eszközök menüben.",
                 bg=BG, fg=TEXT_MUTED, font=("Segoe UI", 13),
                 justify="center").pack(pady=12)

        from api_client import CLIENT_ID
        id_frame = tk.Frame(self.pending_overlay, bg=BG2,
                            highlightbackground=BORDER, highlightthickness=1)
        id_frame.pack(pady=8, padx=80, fill=tk.X)
        tk.Label(id_frame, text="ESZKÖZ AZONOSÍTÓ", bg=BG2, fg=TEXT_DIM,
                 font=("Segoe UI", 9)).pack(pady=(8, 2))
        tk.Label(id_frame, text="WP-" + CLIENT_ID[:8].upper(),
                 bg=BG2, fg=BLUE, font=("Segoe UI", 14, "bold")).pack(pady=(0, 8))

    # ── Publikus metódusok (thread-safe, tk.after-rel) ─────────────────────────

    def show_login(self) -> None:
        self.root.after(0, lambda: self.login_overlay.place(relx=0, rely=0,
                                                             relwidth=1, relheight=1))

    def hide_login(self) -> None:
        self.root.after(0, self.login_overlay.place_forget)

    def show_pending(self) -> None:
        self.root.after(0, lambda: (
            self.login_overlay.place_forget(),
            self.pending_overlay.place(relx=0, rely=0, relwidth=1, relheight=1),
        ))

    def hide_pending(self) -> None:
        self.root.after(0, self.pending_overlay.place_forget)

    def set_institution(self, name: str) -> None:
        self.root.after(0, lambda: self.lbl_inst.config(text=name))

    def set_online(self, online: bool) -> None:
        color = GREEN if online else RED
        label = "Online" if online else "Offline"
        self.root.after(0, lambda: (
            self.canvas_dot.itemconfig(self._dot, fill=color),
            self.lbl_status.config(text=label, fg=TEXT_DIM),
        ))

    def set_snap_status(self, text: str) -> None:
        self.root.after(0, lambda: self.lbl_snap.config(text=text))

    def set_bells(self, bells: list) -> None:
        def _update():
            if not bells:
                self.bell_frame.place_forget()
                return
            now_min = self._now_minutes()
            future = [b for b in bells if b["hour"] * 60 + b["minute"] > now_min]
            if future:
                next_b = min(future, key=lambda b: b["hour"] * 60 + b["minute"])
                t = f"{next_b['hour']:02d}:{next_b['minute']:02d}"
                self.lbl_next_bell.config(text=t)
                self.bell_frame.place(relx=0.5, rely=0.68, anchor="center")
            else:
                self.bell_frame.place_forget()
        self.root.after(0, _update)

    def set_cache_status(self, text: str) -> None:
        self.root.after(0, lambda: self.lbl_cache.config(text=text))

    def show_update_banner(self, text: str,
                           on_click: Optional[callable] = None) -> None:
        def _show():
            self.lbl_update.config(text=text)
            self._update_click_cb = on_click
            self.lbl_update.unbind("<Button-1>")
            if on_click:
                self.lbl_update.bind("<Button-1>", lambda e: on_click())
            if not self._update_banner_visible:
                self.update_banner.pack(fill=tk.X, after=self.header)
                self._update_banner_visible = True
        self.root.after(0, _show)

    def hide_update_banner(self) -> None:
        def _hide():
            self.update_banner.pack_forget()
            self._update_banner_visible = False
        self.root.after(0, _hide)
        def _upd():
            if show and not self._banner_visible:
                self.bell_banner.pack(fill=tk.X, after=self.header)
                self._banner_visible = True
            elif not show and self._banner_visible:
                self.bell_banner.pack_forget()
                self._banner_visible = False
        self.root.after(0, _upd)

    def show_message_overlay(self, text: str, reading_ms: int = 0) -> None:
        def _show():
            # Elrejtjük a rádió widgeteket
            self.lbl_radio_icon.place_forget()
            self.lbl_radio_title.place_forget()
            self.lbl_radio_time.place_forget()

            # Betűméret a szöveg hosszától függően
            length = len(text.strip())
            if length <= 40:   size = 48
            elif length <= 80: size = 36
            elif length <= 160: size = 26
            else:              size = 20

            self.lbl_msg_text.config(
                text=text,
                font=("Segoe UI", size, "bold"),
                wraplength=int(self.root.winfo_width() * 0.85),
            )
            self.lbl_msg_text.place(relx=0.5, rely=0.45, anchor="center")

            if reading_ms > 0:
                self._start_progress(reading_ms)

            self.overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._overlay_visible = True
        self.root.after(0, _show)

    def show_radio_overlay(self, title: str = "Iskolarádió") -> None:
        def _show():
            self.lbl_msg_text.place_forget()
            self.prog_canvas.place_forget()

            self.lbl_radio_icon.place(relx=0.5, rely=0.30, anchor="center")
            self.lbl_radio_title.config(text=title)
            self.lbl_radio_title.place(relx=0.5, rely=0.48, anchor="center")
            self.lbl_radio_time.place(relx=0.5, rely=0.60, anchor="center")

            self.overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._overlay_visible = True
        self.root.after(0, _show)

    def hide_overlay(self) -> None:
        def _hide():
            self._stop_progress()
            self.overlay.place_forget()
            self._overlay_visible = False
        self.root.after(0, _hide)

    def update_radio_time(self, seconds_left: int) -> None:
        m, s = divmod(max(0, seconds_left), 60)
        self.root.after(0, lambda: self.lbl_radio_time.config(
            text=f"{m}:{s:02d}"
        ))

    def set_volume_display(self, vol: int) -> None:
        self.root.after(0, lambda: self.lbl_vol.config(text=str(vol)))

    # ── Login ──────────────────────────────────────────────────────────────────
    def _do_login(self) -> None:
        email = self.entry_email.get().strip()
        pwd   = self.entry_pass.get()
        if not email or not pwd:
            self.lbl_login_err.config(text="Email és jelszó kötelező")
            return
        self.btn_login.config(state="disabled", text="...")
        self.lbl_login_err.config(text="")
        if self.on_login:
            threading.Thread(target=self.on_login,
                             args=(email, pwd), daemon=True).start()

    def set_login_error(self, msg: str) -> None:
        self.root.after(0, lambda: (
            self.lbl_login_err.config(text=msg),
            self.btn_login.config(state="normal", text="▶ Belépés"),
        ))

    # ── Progress sáv ──────────────────────────────────────────────────────────
    def _start_progress(self, total_ms: int) -> None:
        self._stop_progress()
        self._prog_start = time.monotonic()
        self._prog_total = total_ms / 1000
        self.prog_canvas.place(relx=0.5, rely=0.72, anchor="center", relwidth=0.5)
        self._animate_progress()

    def _animate_progress(self) -> None:
        if not self._overlay_visible:
            return
        elapsed = time.monotonic() - self._prog_start
        pct = min(1.0, elapsed / self._prog_total) if self._prog_total > 0 else 1.0
        w = self.prog_canvas.winfo_width()
        h = self.prog_canvas.winfo_height()
        self.prog_canvas.delete("all")
        self.prog_canvas.create_rectangle(0, 0, int(w * pct), h,
                                          fill=BLUE, outline="")
        if pct < 1.0:
            self.root.after(100, self._animate_progress)

    def _stop_progress(self) -> None:
        self._prog_start = 0
        self._prog_total = 0

    # ── Hangerő ────────────────────────────────────────────────────────────────
    def _vol_up(self) -> None:
        self._volume = min(10, self._volume + 1)
        self.set_volume_display(self._volume)
        if self.on_volume_change:
            self.on_volume_change(self._volume)

    def _vol_down(self) -> None:
        self._volume = max(0, self._volume - 1)
        self.set_volume_display(self._volume)
        if self.on_volume_change:
            self.on_volume_change(self._volume)

    # ── Óra ticker ─────────────────────────────────────────────────────────────
    def _tick(self) -> None:
        if not self._clock_running:
            return
        import datetime
        now = datetime.datetime.now()
        self.lbl_clock.config(text=now.strftime("%H:%M:%S"))
        self.lbl_date.config(
            text=now.strftime("%Y. %B %d., %A").capitalize()
        )
        self.root.after(1000, self._tick)

    @staticmethod
    def _now_minutes() -> int:
        import datetime
        n = datetime.datetime.now()
        return n.hour * 60 + n.minute

    def destroy(self) -> None:
        self._clock_running = False
        self.root.destroy()

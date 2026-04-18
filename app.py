"""
NiftyEdge Pro v3 — Windows Desktop Application
================================================
Professional trading dashboard as a native Windows app.

SETUP:
  pip install flask requests flask-cors

  Optional (for embedded browser + tray + notifications):
  pip install pywebview pystray Pillow win10toast

RUN:
  python app.py
"""

import sys, os, threading, time, json, subprocess, webbrowser, socket
import tkinter as tk
from tkinter import ttk, messagebox, font as tkfont

# ── Optional imports ──────────────────────────────────────────────────────────
try:
    import webview
    HAS_WEBVIEW = True
except ImportError:
    HAS_WEBVIEW = False

try:
    from win10toast import ToastNotifier
    HAS_TOAST = True
    _toaster = ToastNotifier()
except ImportError:
    HAS_TOAST = False
    _toaster = None

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# ── Config ────────────────────────────────────────────────────────────────────
APP_NAME   = "NiftyEdge Pro"
APP_VER    = "v3"
# Port and server URL can be overridden with environment variables.
PORT       = int(os.environ.get("PORT", "5000"))
APP_URL    = os.environ.get("APP_URL", f"http://localhost:{PORT}")
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))

# Auto-detect dashboard filename (supports both names)
DASHBOARD  = None
for name in ["nifty_options_dashboard.html", "dashboard.html", "index.html"]:
    p = os.path.join(BASE_DIR, name)
    if os.path.exists(p):
        DASHBOARD = p
        break

SERVER_PY  = os.path.join(BASE_DIR, "server.py")

# ── Color palette (Bloomberg-inspired dark) ───────────────────────────────────
C = {
    "bg":       "#0a0d12",
    "bg2":      "#0f1419",
    "bg3":      "#141c24",
    "bg4":      "#1a2535",
    "border":   "#1e2d3d",
    "border2":  "#243447",
    "muted":    "#3a5068",
    "dim":      "#5a7a94",
    "body":     "#8fa8be",
    "text":     "#c2d6e8",
    "bright":   "#e8f2fa",
    "white":    "#f0f6fc",
    "emerald":  "#10b981",
    "emerald2": "#059669",
    "rose":     "#f43f5e",
    "amber":    "#f59e0b",
    "sky":      "#38bdf8",
    "violet":   "#a78bfa",
}

# ── Server management ─────────────────────────────────────────────────────────
_server_proc = None
_server_ready = False

def _is_port_open(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("localhost", port), timeout=timeout):
            return True
    except OSError:
        return False

def start_server_bg(on_ready=None):
    """Start server.py in background, call on_ready() when up."""
    global _server_proc, _server_ready

    if _is_port_open(PORT):
        _server_ready = True
        if on_ready: on_ready(True)
        return

    if not os.path.exists(SERVER_PY):
        print(f"[APP] server.py not found at {SERVER_PY}")
        if on_ready: on_ready(False)
        return

    try:
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW
        _server_proc = subprocess.Popen(
            [sys.executable, SERVER_PY],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            cwd=BASE_DIR,
        )
        # Wait up to 15s for server to start
        for _ in range(30):
            time.sleep(0.5)
            if _is_port_open(PORT):
                _server_ready = True
                print("[APP] ✓ Server ready")
                if on_ready: on_ready(True)
                return
        print("[APP] ✗ Server did not start in time")
        if on_ready: on_ready(False)
    except Exception as e:
        print(f"[APP] ✗ Server error: {e}")
        if on_ready: on_ready(False)

def stop_server():
    global _server_proc
    if _server_proc:
        try:
            _server_proc.terminate()
            _server_proc.wait(timeout=3)
        except Exception:
            pass
        _server_proc = None

# ── Tray icon ─────────────────────────────────────────────────────────────────
def _make_tray_image(size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Dark background
    d.rounded_rectangle([0, 0, size-1, size-1], radius=12, fill=(10, 13, 18, 255))
    # Green accent bar left
    d.rectangle([0, 0, 3, size-1], fill=(16, 185, 129, 255))
    # Simple candlestick chart
    bars = [(10,48,18,28),(20,48,28,16),(30,48,38,32),(40,48,48,12),(50,48,58,22)]
    for x1,y1,x2,y2 in bars:
        clr = (16,185,129,220) if y2 < y1-20 else (244,63,94,200)
        d.rectangle([x1, y2, x2, y1], fill=clr)
        d.line([((x1+x2)//2, max(0,y2-6)), ((x1+x2)//2, y2)], fill=clr, width=1)
    return img

def setup_tray(app_win):
    if not HAS_TRAY:
        return None

    def _show(_icon, _item):
        app_win.after(0, lambda: (app_win.deiconify(), app_win.lift(), app_win.focus_force()))

    def _browser(_icon, _item):
        if DASHBOARD:
            webbrowser.open(f"file:///{DASHBOARD.replace(os.sep,'/')}")

    def _quit(_icon, _item):
        _icon.stop()
        stop_server()
        app_win.after(0, app_win.destroy)

    img = _make_tray_image(64)
    menu = pystray.Menu(
        pystray.MenuItem("Show Window",     _show, default=True),
        pystray.MenuItem("Open in Browser", _browser),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit NiftyEdge",  _quit),
    )
    icon = pystray.Icon(APP_NAME, img, f"{APP_NAME} {APP_VER}", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    return icon

# ── Toast notifications ───────────────────────────────────────────────────────
def notify(title: str, msg: str, duration: int = 5):
    if HAS_TOAST and _toaster:
        threading.Thread(
            target=lambda: _toaster.show_toast(title, msg, duration=duration, threaded=True),
            daemon=True,
        ).start()

# ── API polling for tip alerts ────────────────────────────────────────────────
def _tip_alert_loop(log_fn):
    import urllib.request, json as _json
    seen = set()
    while True:
        try:
            with urllib.request.urlopen(f"{APP_URL}/api/accuracy", timeout=5) as r:
                data = _json.loads(r.read())
            for tip in (data.get("tips") or [])[:10]:
                tid = tip.get("id")
                if tid and tid not in seen and (tip.get("confidence") or 0) >= 75:
                    seen.add(tid)
                    d = tip.get("direction","")
                    log_fn(
                        f"🎯  {d}  {tip.get('instrument','')}  |  Conf {tip.get('confidence')}%  |  {tip.get('timeframe','')}",
                        "emerald" if d == "BUY" else "rose"
                    )
                    notify(
                        f"{'📈' if d=='BUY' else '📉'} {d} Signal — {tip.get('symbol','')}",
                        f"{tip.get('instrument','')}  ·  Entry ₹{tip.get('entry','')}  ·  Conf {tip.get('confidence')}%"
                    )
        except Exception:
            pass
        time.sleep(15)

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════════════════════════════
class NiftyEdgeApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VER}")
        self.geometry("1400x820")
        self.minsize(900, 600)
        self.configure(bg=C["bg"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._tray = None
        self._status_var   = tk.StringVar(value="Starting server…")
        self._conn_var     = tk.StringVar(value="⬤  OFFLINE")
        self._time_var     = tk.StringVar(value="—")
        self._tick_var     = tk.StringVar(value="TICKS  0")

        self._build()

    # ── Build UI ───────────────────────────────────────────────────────────────
    def _build(self):
        self._configure_styles()
        self._build_topbar()
        self._build_toolbar()
        self._build_body()
        self._build_statusbar()

    def _configure_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame",    background=C["bg"])
        s.configure("Dark.TFrame", background=C["bg2"])
        s.configure("Card.TFrame", background=C["bg3"])

    def _build_topbar(self):
        bar = tk.Frame(self, bg=C["bg2"], height=60)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        # Left accent stripe
        tk.Frame(bar, bg=C["sky"], width=3).pack(side="left", fill="y")

        # Brand
        brand_frame = tk.Frame(bar, bg=C["bg2"])
        brand_frame.pack(side="left", padx=(18, 0), pady=0)
        tk.Label(brand_frame, text="NIFTY", bg=C["bg2"], fg=C["white"],
                 font=("Consolas", 22, "bold")).pack(side="left")
        tk.Label(brand_frame, text="EDGE", bg=C["bg2"], fg=C["emerald"],
                 font=("Consolas", 22, "bold")).pack(side="left")
        tk.Label(brand_frame, text=f" {APP_VER}", bg=C["bg2"], fg=C["muted"],
                 font=("Consolas", 11)).pack(side="left", pady=(6, 0))

        # Index tiles
        tile_frame = tk.Frame(bar, bg=C["bg2"])
        tile_frame.pack(side="left", padx=20, pady=8)
        self._nifty_tile  = self._idx_tile(tile_frame, "NIFTY 50",   "N", active=True)
        self._bnf_tile    = self._idx_tile(tile_frame, "BANK NIFTY", "B", active=False)

        # Right: status
        right = tk.Frame(bar, bg=C["bg2"])
        right.pack(side="right", padx=18)

        tk.Label(right, textvariable=self._conn_var, bg=C["bg2"], fg=C["emerald"],
                 font=("Consolas", 11, "bold")).pack(side="right", padx=(12, 0))
        tk.Label(right, textvariable=self._time_var, bg=C["bg2"], fg=C["dim"],
                 font=("Consolas", 11)).pack(side="right", padx=(12, 0))
        tk.Label(right, textvariable=self._tick_var, bg=C["bg2"], fg=C["dim"],
                 font=("Consolas", 10)).pack(side="right", padx=(12, 0))

    def _idx_tile(self, parent, label, key, active=False):
        border_color = C["sky"] if active else C["border2"]
        bg = C["bg4"] if active else C["bg3"]
        frame = tk.Frame(parent, bg=border_color, padx=1, pady=1)
        frame.pack(side="left", padx=5)
        inner = tk.Frame(frame, bg=bg, padx=14, pady=6)
        inner.pack()
        tk.Label(inner, text=label, bg=bg, fg=C["dim"],
                 font=("Consolas", 9)).pack(anchor="w")
        price_lbl = tk.Label(inner, text="—", bg=bg, fg=C["white"],
                              font=("Consolas", 16, "bold"))
        price_lbl.pack(anchor="w")
        chg_lbl = tk.Label(inner, text="—", bg=bg, fg=C["dim"],
                            font=("Consolas", 10))
        chg_lbl.pack(anchor="w")
        setattr(self, f"_px{key}", price_lbl)
        setattr(self, f"_chg{key}", chg_lbl)
        return frame

    def _build_toolbar(self):
        bar = tk.Frame(self, bg=C["bg2"], height=44)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)
        tk.Frame(bar, bg=C["border"], height=1).pack(side="top", fill="x")

        btn_cfg = dict(bg=C["bg3"], fg=C["sky"], activebackground=C["bg4"],
                       activeforeground=C["sky"], relief="flat",
                       font=("Segoe UI", 10, "bold"), padx=14, pady=4,
                       cursor="hand2", bd=0)

        tk.Button(bar, text="📊  Open Dashboard", command=self._open_dashboard, **btn_cfg).pack(side="left", padx=(14,2), pady=6)
        tk.Button(bar, text="⚡  Live Tips",       command=self._show_tips,      **btn_cfg).pack(side="left", padx=2, pady=6)
        tk.Button(bar, text="🏆  Accuracy",        command=self._show_accuracy,  **btn_cfg).pack(side="left", padx=2, pady=6)
        tk.Button(bar, text="⚙  Weights",         command=self._show_weights,   **btn_cfg).pack(side="left", padx=2, pady=6)
        tk.Button(bar, text="↻  Refresh",          command=self._refresh,        **btn_cfg).pack(side="left", padx=2, pady=6)

        # TF buttons
        tk.Frame(bar, bg=C["border"], width=1).pack(side="left", fill="y", padx=8, pady=8)
        tk.Label(bar, text="FRAME:", bg=C["bg2"], fg=C["muted"],
                 font=("Consolas", 9)).pack(side="left", padx=(0,4))
        self._cur_tf = tk.StringVar(value="10M")
        for tf in ["10M","20M","30M","1H","3H"]:
            rb = tk.Radiobutton(bar, text=tf, variable=self._cur_tf, value=tf,
                                bg=C["bg2"], fg=C["dim"],
                                selectcolor=C["bg4"],
                                activebackground=C["bg2"], activeforeground=C["sky"],
                                font=("Segoe UI", 10, "bold"),
                                indicatoron=False, relief="flat",
                                padx=10, pady=4, cursor="hand2",
                                command=self._on_tf_change)
            rb.pack(side="left", padx=1)

    def _build_body(self):
        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=0, pady=0)

        # Left: live feed panel
        left = tk.Frame(body, bg=C["bg2"], width=580)
        left.pack(side="left", fill="both", expand=True, padx=(10,5), pady=10)
        left.pack_propagate(False)

        self._card(left, "⚡  LIVE SIGNAL FEED", self._build_feed_panel)

        # Right: stats panel
        right = tk.Frame(body, bg=C["bg"], width=380)
        right.pack(side="right", fill="both", padx=(5,10), pady=10)
        right.pack_propagate(False)

        self._card(right, "📈  ACCURACY & STATS", self._build_stats_panel)

    def _card(self, parent, title, builder_fn):
        wrapper = tk.Frame(parent, bg=C["bg2"], bd=0)
        wrapper.pack(fill="both", expand=True)
        # Border frame
        border = tk.Frame(wrapper, bg=C["border2"], bd=0)
        border.pack(fill="both", expand=True, padx=1, pady=1)
        # Header
        head = tk.Frame(border, bg=C["bg3"], height=36)
        head.pack(fill="x", side="top")
        head.pack_propagate(False)
        tk.Frame(head, bg=C["sky"], width=3).pack(side="left", fill="y")
        tk.Label(head, text=title, bg=C["bg3"], fg=C["dim"],
                 font=("Consolas", 10, "bold")).pack(side="left", padx=12, pady=8)
        # Body
        body_frame = tk.Frame(border, bg=C["bg2"])
        body_frame.pack(fill="both", expand=True)
        builder_fn(body_frame)

    def _build_feed_panel(self, parent):
        # Signal hero area
        hero = tk.Frame(parent, bg=C["bg3"])
        hero.pack(fill="x", padx=10, pady=(10,6))

        self._hero_dir = tk.Label(hero, text="—", bg=C["bg3"], fg=C["white"],
                                  font=("Consolas", 36, "bold"), anchor="w")
        self._hero_dir.pack(side="left", padx=(16,8), pady=10)

        hero_info = tk.Frame(hero, bg=C["bg3"])
        hero_info.pack(side="left", fill="both", expand=True, pady=8)
        self._hero_instr = tk.Label(hero_info, text="—", bg=C["bg3"], fg=C["sky"],
                                     font=("Consolas", 13, "bold"), anchor="w")
        self._hero_instr.pack(anchor="w")
        self._hero_tf = tk.Label(hero_info, text="—", bg=C["bg3"], fg=C["dim"],
                                  font=("Consolas", 10), anchor="w")
        self._hero_tf.pack(anchor="w")

        hero_score = tk.Frame(hero, bg=C["bg3"])
        hero_score.pack(side="right", padx=(0,16), pady=8)
        self._hero_conf = tk.Label(hero_score, text="—", bg=C["bg3"], fg=C["amber"],
                                    font=("Consolas", 22, "bold"))
        self._hero_conf.pack()
        tk.Label(hero_score, text="CONFIDENCE", bg=C["bg3"], fg=C["muted"],
                 font=("Consolas", 8)).pack()

        # Levels grid
        lvl_frame = tk.Frame(parent, bg=C["bg2"])
        lvl_frame.pack(fill="x", padx=10, pady=(0,6))

        self._lvl_labels = {}
        for i,(key,lbl,col) in enumerate([
            ("entry","ENTRY",C["sky"]),("target","TARGET",C["emerald"]),
            ("sl","STOP LOSS",C["rose"]),("rr","R : R",C["amber"])
        ]):
            box = tk.Frame(lvl_frame, bg=C["bg3"])
            box.grid(row=0, column=i, padx=3, pady=0, sticky="ew")
            lvl_frame.columnconfigure(i, weight=1)
            tk.Label(box, text=lbl, bg=C["bg3"], fg=C["muted"],
                     font=("Consolas", 8), pady=4).pack()
            val = tk.Label(box, text="—", bg=C["bg3"], fg=col,
                           font=("Consolas", 14, "bold"), pady=4)
            val.pack()
            self._lvl_labels[key] = val

        # Separator
        tk.Frame(parent, bg=C["border"], height=1).pack(fill="x", padx=10, pady=4)

        # Live feed text
        feed_frame = tk.Frame(parent, bg=C["bg2"])
        feed_frame.pack(fill="both", expand=True, padx=10, pady=(0,10))

        self._feed = tk.Text(
            feed_frame, bg=C["bg3"], fg=C["body"],
            font=("Consolas", 10), relief="flat",
            state="disabled", wrap="word",
            insertbackground=C["sky"],
            selectbackground=C["bg4"],
            padx=12, pady=10,
        )
        sb = tk.Scrollbar(feed_frame, command=self._feed.yview,
                          bg=C["bg2"], troughcolor=C["bg3"], relief="flat")
        self._feed.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._feed.pack(fill="both", expand=True)

        # Configure text tags
        for tag, fg in [
            ("buy",   C["emerald"]),  ("sell",   C["rose"]),
            ("amber", C["amber"]),    ("sky",    C["sky"]),
            ("dim",   C["dim"]),      ("white",  C["white"]),
            ("muted", C["muted"]),
        ]:
            self._feed.tag_configure(tag, foreground=fg)
        self._feed.tag_configure("heading", foreground=C["sky"], font=("Consolas",10,"bold"))
        self._feed.tag_configure("dim_italic", foreground=C["muted"], font=("Consolas",9,"italic"))

    def _build_stats_panel(self, parent):
        # PCR + Bull prob cards
        self._stats_text = tk.Text(
            parent, bg=C["bg2"], fg=C["body"],
            font=("Consolas", 10), relief="flat",
            state="disabled", wrap="word",
            padx=12, pady=10,
        )
        sb2 = tk.Scrollbar(parent, command=self._stats_text.yview,
                           bg=C["bg2"], troughcolor=C["bg3"], relief="flat")
        self._stats_text.configure(yscrollcommand=sb2.set)
        sb2.pack(side="right", fill="y")
        self._stats_text.pack(fill="both", expand=True)

        for tag, fg, fnt in [
            ("heading",  C["sky"],     ("Consolas",10,"bold")),
            ("emerald",  C["emerald"], ("Consolas",10,"bold")),
            ("rose",     C["rose"],    ("Consolas",10,"bold")),
            ("amber",    C["amber"],   ("Consolas",10,"bold")),
            ("dim",      C["dim"],     ("Consolas",9,"normal")),
            ("white",    C["white"],   ("Consolas",11,"bold")),
            ("sky",      C["sky"],     ("Consolas",10,"normal")),
            ("muted",    C["muted"],   ("Consolas",9,"italic")),
            ("separator",C["border2"],("Consolas",9,"normal")),
        ]:
            self._stats_text.tag_configure(tag, foreground=fg, font=fnt)

        self._log_stats("⚡ Waiting for data…", "dim")

    def _build_statusbar(self):
        bar = tk.Frame(self, bg=C["bg2"], height=28)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        tk.Frame(bar, bg=C["border"], height=1).pack(side="top", fill="x")
        tk.Label(bar, textvariable=self._status_var, bg=C["bg2"], fg=C["dim"],
                 font=("Consolas", 9), anchor="w").pack(side="left", padx=14)
        tk.Label(bar, text="⚠ Educational use only  ·  Not SEBI registered",
                 bg=C["bg2"], fg=C["amber"], font=("Consolas", 9)).pack(side="right", padx=14)

    # ── Text helpers ───────────────────────────────────────────────────────────
    def _write(self, text, tag=""):
        self._feed.configure(state="normal")
        if tag:
            self._feed.insert("end", text, tag)
        else:
            self._feed.insert("end", text)
        # Trim to 400 lines
        lines = int(self._feed.index("end-1c").split(".")[0])
        if lines > 400:
            self._feed.delete("1.0", f"{lines-400}.0")
        self._feed.see("end")
        self._feed.configure(state="disabled")

    def _log_stats(self, text, tag=""):
        self._stats_text.configure(state="normal")
        self._stats_text.insert("end", text, tag)
        self._stats_text.see("end")
        self._stats_text.configure(state="disabled")

    def _clear_stats(self):
        self._stats_text.configure(state="normal")
        self._stats_text.delete("1.0", "end")
        self._stats_text.configure(state="disabled")

    def _separator(self):
        self._write("─" * 60 + "\n", "separator") if hasattr(self,'_feed') else None

    # ── Actions ───────────────────────────────────────────────────────────────
    def _open_dashboard(self):
        if not DASHBOARD:
            messagebox.showerror("Not Found",
                "Dashboard file not found.\n"
                "Make sure nifty_options_dashboard.html is in the same folder as app.py.")
            return
        if HAS_WEBVIEW:
            threading.Thread(
                target=lambda: webview.create_window(
                    f"{APP_NAME} {APP_VER}", DASHBOARD,
                    width=1600, height=900
                ),
                daemon=True
            ).start()
        else:
            webbrowser.open(f"file:///{DASHBOARD.replace(os.sep, '/')}")

    def _show_tips(self):
        import urllib.request, json as _json
        self._write("\n", "")
        self._separator()
        self._write(" LIVE TIPS\n", "heading")
        self._separator()
        try:
            for sym in ["NIFTY", "BANKNIFTY"]:
                with urllib.request.urlopen(f"{APP_URL}/api/snapshot/{sym}", timeout=6) as r:
                    data = _json.loads(r.read())
                tips = data.get("tips", {})
                self._write(f"\n  {sym}\n", "sky")
                for tf_key in ["10","20","30","60","180"]:
                    tip = tips.get(tf_key)
                    if not tip: continue
                    d = tip.get("direction","")
                    tag = "buy" if d=="BUY" else "sell" if d=="SELL" else "amber"
                    tf_lbl = {"10":"10 Min","20":"20 Min","30":"30 Min","60":"1 Hour","180":"3 Hour"}.get(tf_key, tf_key+"m")
                    self._write(f"  [{tf_lbl:7}]  ", "dim")
                    self._write(f"{d:7}", tag)
                    self._write(f"  {tip.get('instrument','—'):32}", "white")
                    self._write(f"  Entry ₹{tip.get('entry','—')}  Tgt ₹{tip.get('target','—')}  SL ₹{tip.get('sl','—')}  Conf {tip.get('confidence','—')}%\n", "dim")
        except Exception as e:
            self._write(f"  Server offline or no data: {e}\n", "rose")

    def _show_accuracy(self):
        import urllib.request, json as _json
        self._clear_stats()
        try:
            with urllib.request.urlopen(f"{APP_URL}/api/accuracy", timeout=6) as r:
                data = _json.loads(r.read())
            stats = data.get("accuracy", [])
            tips  = data.get("tips", [])

            self._log_stats(" ACCURACY REPORT\n", "heading")
            self._log_stats("─"*44+"\n", "separator")
            self._log_stats(f"{'SYM':<8} {'TF':<8} {'DIR':<6} {'W':<4} {'L':<4} {'ACC':<8} {'AVG P&L'}\n", "sky")
            self._log_stats("─"*44+"\n", "separator")

            for s in stats:
                acc = s.get("accuracy_pct", 0)
                tag = "emerald" if acc >= 65 else "amber" if acc >= 50 else "rose"
                line = f"{s['symbol']:<8} {s['timeframe']:<8} {s['direction']:<6} {s['wins']:<4} {s['losses']:<4} {acc:<8.1f} {s.get('avg_pnl_pct',0):+.1f}%\n"
                self._log_stats(line, tag)

            self._log_stats("\n RECENT TIPS\n", "heading")
            self._log_stats("─"*44+"\n", "separator")
            for t in tips[:20]:
                oc = t.get("outcome","PENDING")
                tag = "emerald" if oc=="WIN" else "rose" if oc=="LOSS" else "dim"
                pnl = f"  {t['pnl_pct']:+.1f}%" if t.get("pnl_pct") is not None else ""
                line = f" [{oc:8}]  {t.get('symbol',''):<10} {t.get('timeframe',''):<8} {t.get('direction',''):<6} {t.get('instrument','—')}{pnl}\n"
                self._log_stats(line, tag)

            if not stats and not tips:
                self._log_stats(" No data yet. Tips resolve as WIN/LOSS once server monitors prices.\n", "muted")

        except Exception as e:
            self._clear_stats()
            self._log_stats(f" Server offline: {e}\n Start server.py first.\n", "rose")

    def _show_weights(self):
        import urllib.request, json as _json
        self._clear_stats()
        try:
            with urllib.request.urlopen(f"{APP_URL}/api/weights", timeout=6) as r:
                data = _json.loads(r.read())
            self._log_stats(" ADAPTIVE FACTOR WEIGHTS\n", "heading")
            self._log_stats("─"*44+"\n","separator")
            TFL = {"10":"10 Min","20":"20 Min","30":"30 Min","60":"1 Hour","180":"3 Hour"}
            for sym, tfs in data.items():
                self._log_stats(f"\n {sym}\n","sky")
                for tf, weights in tfs.items():
                    self._log_stats(f"  {TFL.get(tf,tf+'m')}\n","dim")
                    for factor, val in weights.items():
                        bar_len = int(val / 5.0 * 20)
                        bar = "█" * bar_len + "░" * (20 - bar_len)
                        self._log_stats(f"  {factor:<14} {bar}  {val:.2f}\n","body" if hasattr(self,'body') else "")
        except Exception as e:
            self._clear_stats()
            self._log_stats(f" Server offline: {e}\n","rose")

    def _refresh(self):
        self._write("\n[↻] Refreshing…\n", "sky")
        self._show_accuracy()

    def _on_tf_change(self):
        tf = self._cur_tf.get()
        self._write(f"\n  [TF → {tf}]\n","sky")

    # ── Price updates ─────────────────────────────────────────────────────────
    def update_price(self, sym: str, spot: float, chg: float, pchg: float):
        key = "N" if sym == "NIFTY" else "B"
        px_lbl  = getattr(self, f"_px{key}",  None)
        chg_lbl = getattr(self, f"_chg{key}", None)
        if px_lbl:
            px_lbl.configure(text=f"{spot:,.2f}", fg=C["white"])
        if chg_lbl:
            color = C["emerald"] if chg >= 0 else C["rose"]
            sign  = "+" if chg >= 0 else ""
            chg_lbl.configure(
                text=f"{sign}{chg:.2f}  ({sign}{pchg:.2f}%)",
                fg=color
            )

    def update_hero(self, tip: dict):
        d = tip.get("direction","—")
        color = C["emerald"] if d=="BUY" else C["rose"] if d=="SELL" else C["amber"]
        self._hero_dir.configure(text=d, fg=color)
        self._hero_instr.configure(text=tip.get("instrument","—"))
        self._hero_tf.configure(text=f"{tip.get('timeframe','—')}  ·  Score {tip.get('score',0):+.1f}/14")
        self._hero_conf.configure(text=f"{tip.get('confidence','—')}%", fg=color)
        self._lvl_labels["entry"].configure(text=f"₹{tip.get('entry','—')}")
        self._lvl_labels["target"].configure(text=f"₹{tip.get('target','—')}")
        self._lvl_labels["sl"].configure(text=f"₹{tip.get('sl','—')}")
        self._lvl_labels["rr"].configure(text=f"1:{tip.get('rr','—')}")

    # ── Background data loop ──────────────────────────────────────────────────
    def start_data_loop(self):
        def loop():
            import urllib.request, json as _json
            from datetime import datetime, timezone, timedelta
            IST = timezone(timedelta(hours=5, minutes=30))
            base = {"NIFTY": 0, "BANKNIFTY": 0}
            ticks_seen = 0

            while True:
                now_ist = datetime.now(IST).strftime("%H:%M:%S")
                self.after(0, lambda t=now_ist: self._time_var.set(t))

                if not _is_port_open(PORT):
                    self.after(0, lambda: self._conn_var.set("⬤  OFFLINE"))
                    self.after(0, lambda: self._status_var.set(f"Server offline  ·  {now_ist}  ·  Run server.py"))
                    time.sleep(3); continue

                self.after(0, lambda: self._conn_var.set("⬤  LIVE"))

                try:
                    for sym in ["NIFTY","BANKNIFTY"]:
                        with urllib.request.urlopen(f"{APP_URL}/api/snapshot/{sym}", timeout=6) as r:
                            data = _json.loads(r.read())
                        a = data.get("analysis",{})
                        if not a: continue
                        spot = a.get("spot",0)
                        if not base[sym]: base[sym] = spot
                        chg  = spot - base[sym]
                        pchg = chg / base[sym] * 100 if base[sym] else 0
                        self.after(0, lambda s=sym, sp=spot, c=chg, p=pchg: self.update_price(s,sp,c,p))

                        # Update hero for current symbol
                        tips = data.get("tips",{})
                        for tf_key in ["10","20","30","60","180"]:
                            tip = tips.get(tf_key)
                            if tip and tip.get("direction") != "NEUTRAL":
                                self.after(0, lambda t=tip: self.update_hero(t))
                                break

                        ticks_seen += 1
                        self.after(0, lambda t=ticks_seen: self._tick_var.set(f"TICKS  {t}"))

                        # Log line to feed
                        pcr = a.get("pcr",0)
                        bp  = a.get("bull_prob",0)
                        iv  = a.get("atm_iv",0)
                        msg = f"[{now_ist}]  {sym:<12} ₹{spot:<10.2f}  PCR {pcr:.3f}  BullP {bp:.0f}%  IV {iv:.1f}%\n"
                        self.after(0, lambda m=msg: self._write(m,"dim"))

                    self.after(0, lambda: self._status_var.set(
                        f"Connected  ·  {now_ist}  ·  Auto-refresh every 5s"))

                except Exception as e:
                    self.after(0, lambda err=str(e): self._write(f"  [ERR] {err}\n","rose"))

                time.sleep(5)

        threading.Thread(target=loop, daemon=True).start()

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def _on_close(self):
        if messagebox.askyesno("Quit NiftyEdge Pro", "Stop server and close the application?"):
            stop_server()
            self.destroy()


# ═════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═════════════════════════════════════════════════════════════════════════════
def main():
    print()
    print("=" * 55)
    print(f"   {APP_NAME} {APP_VER} — Windows Desktop Application")
    print("=" * 55)

    if DASHBOARD:
        print(f"  Dashboard : {os.path.basename(DASHBOARD)}")
    else:
        print("  ⚠ Dashboard file not found in current directory!")

    print(f"  Server    : {SERVER_PY}")
    print()

    # Create window
    app = NiftyEdgeApp()

    # Welcome message
    app._write(f"  {APP_NAME} {APP_VER} — starting…\n\n", "heading")
    if not DASHBOARD:
        app._write("  ⚠  Dashboard file not found!\n"
                   "  Place nifty_options_dashboard.html in the same folder.\n\n", "rose")

    # Start server in background
    def _on_server_ready(ok):
        if ok:
            app.after(0, lambda: app._write("  ✓  Server ready — live data streaming\n\n", "emerald"))
            app.after(0, lambda: app._status_var.set("Server ready"))
        else:
            app.after(0, lambda: app._write(
                "  ⚠  Could not start server automatically.\n"
                "  Run: python server.py  (in the same folder)\n\n", "amber"))

    threading.Thread(target=start_server_bg, args=(_on_server_ready,), daemon=True).start()

    # Start data polling loop after 2s
    app.after(2000, app.start_data_loop)

    # Open dashboard after 3s
    if DASHBOARD:
        app.after(3000, app._open_dashboard)

    # Setup tray
    app.after(1000, lambda: setattr(app, "_tray", setup_tray(app)))

    # Tip alert watcher
    if HAS_TOAST:
        threading.Thread(
            target=_tip_alert_loop,
            args=(lambda m, t: app.after(0, lambda: app._write(m+"\n", t)),),
            daemon=True
        ).start()

    print("  ✓ Application launched")
    print("=" * 55)
    print()

    app.mainloop()
    stop_server()


if __name__ == "__main__":
    main()

"""Graphical wizard and settings window (tkinter).

Design goal: a complete beginner can go from nothing to "my TV sleeps when I
walk away" in under a minute, using a clean, modern-looking window.

Two screens, switched in-place inside one window:

* SetupWizard  - shown until setup is complete: Find TV -> Pair -> Timeout.
* SettingsPanel - the everyday screen: a big On/Off switch and a slider for the
  idle timeout, plus a "Test my TV" button and a status line.

All TV/idle logic lives in the verified core modules; this file only wires
widgets to them and never blocks the UI thread (network work runs in threads).

The look is a flat dark theme built on ttk's "clam" engine plus a couple of small
hand-drawn widgets (a pill toggle switch, an accent rule), so it stays dependency
-free and renders the same on Windows and Linux.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Optional

from . import __version__
from . import autostart as autostart_mod
from . import branding
from .config import Config, Device, fmt_timeout
from .daemon import Daemon
from . import idle as idle_mod
from .discovery import discover_tvs
from .netdiag import probe_tv, subnet_report
from .webos import WebOSClient, pair_with_fallback

PAD = 14

# How often the settings panel re-reads the config file to notice what a
# background watcher has learned (e.g. which input this PC is on).
INPUT_POLL_MS = 2000

# Exit code meaning "the user stopped the service on purpose - do not start a
# watcher after this window closes". The launchers run their supervisor once the
# GUI returns, so without this contract the "kill process" button would be
# undone the moment the window was closed.
EXIT_SERVICE_STOPPED = 10

# Flat dark palette. Kept in one place so every widget pulls the same colours.
PALETTE = {
    "bg":        "#15171C",   # window background
    "surface":   "#1E2128",   # cards
    "inset":     "#262A33",   # fields: entry, listbox, text, slider trough
    "border":    "#343A45",
    "text":      "#ECEEF2",
    "muted":     "#98A0AD",
    "accent":    "#5B8CFF",
    "accent_hi": "#7AA2FF",
    "accent_lo": "#4377F0",
    "danger":    "#FF6B6B",
    "danger_bg": "#2B1B20",   # dark red-tinted surface for warning cards
    "ok":        "#48D597",
}

# Populated by ``_apply_theme`` with the palette plus the resolved font families,
# so the hand-drawn widgets (which aren't ttk-styled) can read them too.
THEME: dict = dict(PALETTE, ui="Helvetica", mono="Courier")


def _apply_theme(root: tk.Misc) -> dict:
    """Configure ttk styles for the whole app and return the resolved THEME."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    families = set(tkfont.families(root))

    def pick(prefs, default):
        for name in prefs:
            if name in families:
                return name
        return default

    ui = pick(["Segoe UI", "Inter", "SF Pro Text", "Ubuntu", "Cantarell",
               "Noto Sans", "DejaVu Sans"], "Helvetica")
    mono = pick(["Cascadia Mono", "Cascadia Code", "Consolas", "SF Mono",
                 "Ubuntu Mono", "DejaVu Sans Mono", "Noto Sans Mono"], "Courier")
    THEME.update(PALETTE)
    THEME["ui"], THEME["mono"] = ui, mono
    P = PALETTE

    root.configure(bg=P["bg"])
    style.configure(".", background=P["bg"], foreground=P["text"],
                    fieldbackground=P["inset"], bordercolor=P["border"],
                    lightcolor=P["bg"], darkcolor=P["bg"], font=(ui, 10))
    style.map(".", foreground=[("disabled", P["muted"])])

    style.configure("TFrame", background=P["bg"])
    style.configure("Card.TFrame", background=P["surface"])

    style.configure("TLabel", background=P["bg"], foreground=P["text"], font=(ui, 10))
    style.configure("Brand.TLabel", background=P["bg"], foreground=P["text"],
                    font=(ui, 13, "bold"))
    style.configure("Title.TLabel", background=P["bg"], foreground=P["text"],
                    font=(ui, 19, "bold"))
    style.configure("Sub.TLabel", background=P["bg"], foreground=P["muted"], font=(ui, 10))
    style.configure("Card.TLabel", background=P["surface"], foreground=P["text"], font=(ui, 10))
    style.configure("CardTitle.TLabel", background=P["surface"], foreground=P["text"],
                    font=(ui, 11, "bold"))
    style.configure("CardMuted.TLabel", background=P["surface"], foreground=P["muted"],
                    font=(ui, 9))
    # Settled/confirmed sub-state on a card, e.g. "this PC is on HDMI 2". Green
    # so "it worked it out" is distinguishable at a glance from the muted grey
    # of "still working it out".
    style.configure("CardOk.TLabel", background=P["surface"], foreground=P["ok"],
                    font=(ui, 9))
    style.configure("Value.TLabel", background=P["surface"], foreground=P["accent"],
                    font=(ui, 24, "bold"))

    # The "no TV is set up" warning: a red card that cannot be read as chrome.
    style.configure("Danger.TFrame", background=P["danger_bg"])
    style.configure("DangerTitle.TLabel", background=P["danger_bg"],
                    foreground=P["danger"], font=(ui, 12, "bold"))
    style.configure("DangerBody.TLabel", background=P["danger_bg"],
                    foreground=P["text"], font=(ui, 10))
    style.configure("DangerMuted.TLabel", background=P["danger_bg"],
                    foreground=P["muted"], font=(ui, 9))
    style.configure("Danger.TButton", background=P["danger"], foreground="#2B1B20",
                    bordercolor=P["danger"], lightcolor=P["danger"],
                    darkcolor=P["danger"], borderwidth=0, relief="flat",
                    padding=(16, 9), font=(ui, 10, "bold"))
    style.map("Danger.TButton", background=[("active", "#FF8585"),
                                            ("pressed", "#E85C5C")])

    style.configure("TButton", background=P["inset"], foreground=P["text"],
                    bordercolor=P["border"], lightcolor=P["inset"], darkcolor=P["inset"],
                    borderwidth=1, relief="flat", focusthickness=0,
                    padding=(14, 9), font=(ui, 10))
    style.map("TButton", background=[("active", P["border"]), ("pressed", P["border"])],
              bordercolor=[("focus", P["accent"])])
    style.configure("Accent.TButton", background=P["accent"], foreground="#FFFFFF",
                    bordercolor=P["accent"], lightcolor=P["accent"], darkcolor=P["accent"],
                    borderwidth=0, relief="flat", padding=(18, 10), font=(ui, 10, "bold"))
    style.map("Accent.TButton",
              background=[("active", P["accent_hi"]), ("pressed", P["accent_lo"])],
              foreground=[("disabled", "#FFFFFF")])
    # A destructive action that still lives in a footer: red enough to read as
    # "this stops things", flat enough not to shout over the primary buttons.
    style.configure("DangerGhost.TButton", background=P["bg"], foreground=P["danger"],
                    bordercolor=P["bg"], lightcolor=P["bg"], darkcolor=P["bg"],
                    borderwidth=0, relief="flat", padding=(12, 9), font=(ui, 10))
    style.map("DangerGhost.TButton", background=[("active", P["danger_bg"])],
              foreground=[("disabled", P["muted"])])
    style.configure("Ghost.TButton", background=P["bg"], foreground=P["muted"],
                    bordercolor=P["bg"], lightcolor=P["bg"], darkcolor=P["bg"],
                    borderwidth=0, relief="flat", padding=(12, 9), font=(ui, 10))
    style.map("Ghost.TButton", background=[("active", P["surface"])],
              foreground=[("active", P["text"])])

    style.configure("TEntry", fieldbackground=P["inset"], foreground=P["text"],
                    bordercolor=P["border"], insertcolor=P["text"], relief="flat",
                    padding=6)
    style.map("TEntry", bordercolor=[("focus", P["accent"])])
    style.configure("TSpinbox", fieldbackground=P["inset"], foreground=P["text"],
                    background=P["inset"], bordercolor=P["border"], arrowcolor=P["muted"],
                    relief="flat", padding=5)
    style.map("TSpinbox", bordercolor=[("focus", P["accent"])])

    # gripcount=0 drops clam's default "barcode" dashes on the slider handle for a
    # clean solid grip; a defined sliderlength keeps it a comfortable target.
    style.configure("Horizontal.TScale", background=P["accent"], troughcolor=P["inset"],
                    bordercolor=P["surface"], lightcolor=P["accent"], darkcolor=P["accent"],
                    gripcount=0, sliderlength=24)
    style.map("Horizontal.TScale", background=[("active", P["accent_hi"])])
    style.configure("TProgressbar", background=P["accent"], troughcolor=P["inset"],
                    bordercolor=P["surface"], lightcolor=P["accent"], darkcolor=P["accent"])
    style.configure("Vertical.TScrollbar", background=P["inset"], troughcolor=P["bg"],
                    bordercolor=P["bg"], arrowcolor=P["muted"], relief="flat")
    style.map("Vertical.TScrollbar", background=[("active", P["border"])])
    return THEME


class ToggleSwitch(tk.Canvas):
    """A small pill on/off switch bound to a ``tk.BooleanVar`` (hand-drawn).

    ttk's checkbutton indicator can't be themed cleanly across platforms, so the
    boolean options use this instead - it reads as a modern toggle and follows
    the variable both ways (clicking flips it; setting the var redraws it)."""

    WIDTH, HEIGHT = 48, 26

    def __init__(self, parent, variable: tk.BooleanVar, command=None, bg=None):
        super().__init__(parent, width=self.WIDTH, height=self.HEIGHT,
                         highlightthickness=0, bd=0,
                         bg=bg or THEME["surface"], cursor="hand2")
        self.var = variable
        self.command = command
        self.bind("<Button-1>", self._clicked)
        self.var.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _clicked(self, _event=None):
        self.var.set(not bool(self.var.get()))
        if self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        on = bool(self.var.get())
        track = THEME["accent"] if on else THEME["inset"]
        knob = "#FFFFFF" if on else THEME["muted"]
        h, w = self.HEIGHT, self.WIDTH
        self.create_oval(0, 0, h, h, fill=track, outline=track)
        self.create_oval(w - h, 0, w, h, fill=track, outline=track)
        self.create_rectangle(h / 2, 0, w - h / 2, h, fill=track, outline=track)
        pad = 3
        d = h - 2 * pad
        x = (w - h + pad) if on else pad
        self.create_oval(x, pad, x + d, pad + d, fill=knob, outline=knob)


def _build_steps(*ranges) -> "list":
    """Distinct values from (start, stop, step) ranges, in order.

    Used to build a non-linear timeout scale: fine steps where small values
    matter, coarse steps higher up - so the slider is precise at 10 seconds and
    still reaches 2 hours without a thousand positions in between.
    """
    vals: list = []
    for start, stop, step in ranges:
        v = start
        while v <= stop + 1e-9:
            iv = int(round(v))
            if iv not in vals:
                vals.append(iv)
            v += step
    return vals


# Sleep (screen-off): 10s->1min by 10s, 1->10min by 1min, 10->60min by 5min,
# 60->120min by 10min.
SLEEP_STEPS_SEC = _build_steps((10, 60, 10), (60, 600, 60),
                               (600, 3600, 300), (3600, 7200, 600))
# Deep power-off is "a longer idle", so it starts at 1 minute (no sub-minute);
# its upper range matches sleep (5-minute steps to 60, then 10-minute steps).
DEEP_STEPS_SEC = _build_steps((60, 600, 60), (600, 3600, 300), (3600, 7200, 600))


class SteppedSlider(ttk.Frame):
    """A slider that snaps to a fixed list of values, with a live value label.

    tkinter's Scale is linear; driving it over indices into ``values`` gives the
    non-linear feel we want and sidesteps the flaky ttk.Spinbox (whose mouse-wheel
    / typing handling misbehaves on Linux). ``fmt`` renders a value for the label;
    ``command`` (optional) fires once each time the snapped value changes.
    """

    def __init__(self, parent, *, values, initial, fmt, command=None):
        super().__init__(parent, style="Card.TFrame")
        self.values = list(values)
        self._fmt = fmt
        self._command = command
        self._idx = self._nearest(initial)
        self._busy = False
        self.label = ttk.Label(self, style="Value.TLabel")
        self.label.pack(anchor="w")
        self.scale = ttk.Scale(self, from_=0, to=len(self.values) - 1,
                               command=self._on_move)
        # Quantise to a step while dragging (so the value/label are always a real
        # step), but only snap the handle itself once the drag ends - setting the
        # scale value mid-motion can fight the drag gesture on some Tk builds.
        self.scale.bind("<ButtonRelease-1>", self._snap)
        self.scale.bind("<KeyRelease>", self._snap)
        self.scale.pack(fill="x", pady=(6, 0))
        self._busy = True            # set initial position without firing command
        self.scale.set(self._idx)
        self._busy = False
        self._refresh()

    def _nearest(self, value) -> int:
        return min(range(len(self.values)),
                   key=lambda i: abs(self.values[i] - value))

    def _on_move(self, raw):
        if self._busy:
            return
        idx = max(0, min(len(self.values) - 1, int(round(float(raw)))))
        changed = idx != self._idx
        self._idx = idx
        self._refresh()
        if changed and self._command:
            self._command()

    def _snap(self, _event=None):
        """Rest the handle exactly on the selected step once the drag ends."""
        self._busy = True
        self.scale.set(self._idx)
        self._busy = False

    def _refresh(self):
        self.label.config(text=self._fmt(self.value()))

    def value(self):
        """The currently selected raw value (seconds)."""
        return self.values[self._idx]

    def set_value(self, value):
        """Set programmatically to the nearest step (no command fired)."""
        self._idx = self._nearest(value)
        self._busy = True
        self.scale.set(self._idx)
        self._busy = False
        self._refresh()


def usable_screen(window: tk.Misc) -> "tuple[int, int]":
    """How big a window may get here without running off the display.

    ``wm_maxsize`` is the honest answer on Windows - it already excludes the
    taskbar - and falls back to the raw screen size elsewhere, where a margin
    covers the title bar and any panel/dock. Everything that sizes a window
    routes through this, because a window taller than the screen is the one
    shape a user cannot fix: the bottom of it is simply unreachable.
    """
    try:
        max_w, max_h = window.wm_maxsize()
    except tk.TclError:  # pragma: no cover - no window manager
        max_w = max_h = 0
    screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
    max_w = min(max_w or screen_w, screen_w)
    max_h = min(max_h or screen_h, screen_h)
    return max(360, max_w - 40), max(320, max_h - 80)


class ScrollArea(ttk.Frame):
    """A vertically scrolling viewport. Add content to ``.inner``.

    Why this exists: the window used to grow to whatever height the active panel
    asked for and pin that as its minimum, on the theory that "this app never
    scrolls". On a laptop - or any 1080p screen once the deep power-off slider
    and the warning banner are both showing - the panel is taller than the
    display, so the bottom cards were drawn off-screen with no way to reach
    them: the window could not be shrunk (minsize) and would not scroll.

    So the viewport keeps asking for exactly the height its content wants, up to
    a ceiling the window sets from the screen size (:meth:`set_max_height`).
    Below the ceiling nothing changes and no scrollbar appears; above it, the
    bar appears and every control stays reachable.
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._max_height = 10_000
        self._applied = (0, 0)

        # A fixed scroll unit (rather than Tk's default tenth-of-a-window) keeps
        # a wheel notch feeling the same whatever size the window is.
        self.canvas = tk.Canvas(self, bg=THEME["bg"], highlightthickness=0,
                                bd=0, takefocus=0, yscrollincrement=20)
        self.vbar = ttk.Scrollbar(self, orient="vertical",
                                  command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._scroll_set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self._bar_shown = False

        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner,
                                                 anchor="nw")
        self.inner.bind("<Configure>", self._inner_configured)
        self.canvas.bind("<Configure>", self._canvas_configured)
        # Wheel events are bound app-wide and filtered by where the pointer
        # actually is (see _owns_wheel). Binding them on <Enter> and dropping
        # them on <Leave> looks tidier and does not work: Tk sends this frame a
        # Leave the moment the pointer moves onto any widget inside it, so the
        # wheel would only scroll over the bare gaps between the cards.
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")   # Windows/macOS
        self.bind_all("<Button-4>", self._on_wheel, add="+")     # X11 wheel up
        self.bind_all("<Button-5>", self._on_wheel, add="+")     # X11 wheel down

    # ----- geometry ----------------------------------------------------
    def set_max_height(self, height: int) -> None:
        """Cap the viewport; content taller than this scrolls."""
        self._max_height = max(120, int(height))
        self._sync_request()

    def _sync_request(self) -> None:
        """Ask for exactly as much room as the content wants, up to the cap."""
        want_w = self.inner.winfo_reqwidth()
        want_h = min(self.inner.winfo_reqheight(), self._max_height)
        if (want_w, want_h) == self._applied:
            return
        self._applied = (want_w, want_h)
        self.canvas.configure(width=want_w, height=want_h)

    def _inner_configured(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._sync_request()

    def _canvas_configured(self, event) -> None:
        # Keep the content as wide as the viewport so cards still fill="x".
        self.canvas.itemconfigure(self._window, width=event.width)

    def _scroll_set(self, first, last) -> None:
        """Show the scrollbar only when some of the content is out of view."""
        needed = not (float(first) <= 0.0 and float(last) >= 1.0)
        if needed and not self._bar_shown:
            self.vbar.pack(side="right", fill="y")
            self._bar_shown = True
        elif not needed and self._bar_shown:
            self.vbar.pack_forget()
            self._bar_shown = False
        self.vbar.set(first, last)

    def to_top(self) -> None:
        try:
            self.canvas.yview_moveto(0.0)
        except tk.TclError:  # pragma: no cover - destroyed mid-switch
            pass

    # ----- mouse wheel --------------------------------------------------
    def _owns_wheel(self, event) -> bool:
        """False when the pointer is over something that scrolls itself.

        The diagnostics log and the TV list have their own scrollbars; rolling
        the wheel over one of those should move that list, not the page. Windows
        sends wheel events to the focused widget rather than the one under the
        pointer, so the pointer position is what we go by.
        """
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if isinstance(widget, (tk.Text, tk.Listbox)):
                return False
            if widget is self:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _on_wheel(self, event):
        if not self._bar_shown or not self._owns_wheel(event):
            return
        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            # Windows reports 120 per notch; X11/macOS deliver small deltas.
            step = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(step * 3, "units")
        return "break"


def make_diag(app: "App", parent: tk.Misc, height: int = 6):
    """A read-only, scrollable diagnostics text area + a thread-safe appender.

    Shared by the setup wizard and the repair dialog. The returned callable can
    be handed straight to worker threads (and to ``selfheal``/``discovery`` as a
    ``log``): it marshals each line back onto the UI thread via ``app.post`` and
    ignores writes to a widget that has since been destroyed, so a still-running
    worker can never freeze the UI by logging into a closed screen.
    """
    frame = ttk.Frame(parent, style="Card.TFrame")
    frame.pack(fill="both", expand=True, pady=(6, 0))
    text = tk.Text(frame, height=height, wrap="word", font=(THEME["mono"], 9),
                   state="disabled", background=THEME["inset"],
                   foreground=THEME["muted"], relief="flat", borderwidth=0,
                   highlightthickness=1, highlightbackground=THEME["border"],
                   highlightcolor=THEME["border"], padx=8, pady=6,
                   insertbackground=THEME["text"])
    sb = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)

    def append(line):
        try:
            text.configure(state="normal")
            text.insert(tk.END, line + "\n")
            text.see(tk.END)
            text.configure(state="disabled")
        except tk.TclError:
            pass

    return lambda line: app.post(lambda: append(line))


NO_TV_TITLE = "No TV is set up"
NO_TV_BODY = ("Easy Mode is running but has nothing to control, so your TV "
              "will never sleep on idle.")


def make_no_tv_banner(parent: tk.Misc, reason: str, on_setup=None):
    """A red 'no TV is set up' card. Returns the frame (already packed).

    The state it announces is silent by nature - the app looks perfectly healthy
    while doing nothing at all - so it is deliberately the loudest thing on
    screen: red on a red-tinted card, above everything else.
    """
    card = ttk.Frame(parent, style="Danger.TFrame", padding=PAD)
    card.pack(fill="x", pady=(0, PAD - 4))
    head = ttk.Frame(card, style="Danger.TFrame")
    head.pack(fill="x")
    dot = tk.Canvas(head, width=12, height=12, highlightthickness=0, bd=0,
                    bg=THEME["danger_bg"])
    dot.create_oval(1, 1, 11, 11, fill=THEME["danger"], outline=THEME["danger"])
    dot.pack(side="left", padx=(0, 8), pady=(4, 0), anchor="n")
    ttk.Label(head, text=NO_TV_TITLE, style="DangerTitle.TLabel").pack(
        side="left", anchor="w")
    ttk.Label(card, text=NO_TV_BODY, style="DangerBody.TLabel",
              wraplength=420, justify="left").pack(anchor="w", pady=(6, 0))
    ttk.Label(card, text=reason, style="DangerMuted.TLabel",
              wraplength=420, justify="left").pack(anchor="w", pady=(4, 0))
    if on_setup is not None:
        ttk.Button(card, text="Set up my TV", style="Danger.TButton",
                   command=on_setup).pack(anchor="w", pady=(PAD - 2, 0))
    return card


def show_no_tv_alert(reason: str, dismiss_after: float = 300.0) -> None:
    """Standalone red warning window, for when there is no console to print to.

    Owns its own Tk root because the caller is the headless watcher process
    (started by pythonw on Windows, or a Terminal=false .desktop entry on
    Linux), not the GUI. "Set up my TV" launches the real control panel as a
    separate process, so this window never has to become one.

    Blocks until dismissed or ``dismiss_after`` seconds pass. The timeout
    matters because "no console" also covers redirected output and systemd user
    units: waiting forever there would hang a script on a window nobody is sat
    in front of. Nothing is lost when it closes itself - the reason is in the
    log file, and every later ``status`` still says so.
    """
    branding.set_app_id()
    root = tk.Tk(className=branding.WM_CLASS)
    root.title("LGTV Companion Easy Mode")
    branding.apply_icon(root)
    _apply_theme(root)
    root.resizable(False, False)
    tk.Frame(root, height=3, bg=THEME["danger"]).pack(fill="x")
    body = ttk.Frame(root, padding=(PAD + 4, PAD, PAD + 4, PAD + 4))
    body.pack(fill="both", expand=True)

    def open_setup():
        import subprocess
        try:
            # branding.launch_command knows whether we are a frozen .exe or a
            # source checkout; the watcher that opened this window is neither
            # necessarily.
            subprocess.Popen(branding.launch_command("gui"),
                             cwd=str(branding.app_dir()))
        except Exception:  # noqa: BLE001 - nothing useful to do if it won't start
            pass
        root.destroy()

    make_no_tv_banner(body, reason, on_setup=open_setup)
    ttk.Button(body, text="Close", style="Ghost.TButton",
               command=root.destroy).pack(anchor="e")
    root.update_idletasks()
    root.minsize(root.winfo_reqwidth(), root.winfo_reqheight())
    if dismiss_after and dismiss_after > 0:
        root.after(int(dismiss_after * 1000), root.destroy)
    root.mainloop()


# Smallest the window may be dragged to. It is deliberately well under the
# height of any panel: the viewport scrolls, so a small window hides nothing,
# and a laptop screen must always be able to show the whole frame.
MIN_WIDTH, MIN_HEIGHT = 500, 440
WANT_WIDTH, WANT_HEIGHT = 540, 715


class App(tk.Tk):
    def __init__(self):
        # Claim the app's taskbar identity before the first window exists, or
        # Windows will already have filed us under whatever launched us
        # (python.exe, and its icon) for the life of the process.
        branding.set_app_id()
        super().__init__(className=branding.WM_CLASS)
        self.title("LGTV Companion Easy Mode")
        branding.apply_icon(self)
        _apply_theme(self)
        avail_w, avail_h = usable_screen(self)
        self.minsize(min(MIN_WIDTH, avail_w), min(MIN_HEIGHT, avail_h))
        self.geometry(f"{min(WANT_WIDTH, avail_w)}x{min(WANT_HEIGHT, avail_h)}")
        try:
            self.tk.call("tk", "scaling", 1.2)
        except tk.TclError:
            pass

        self.cfg = Config.load()
        self.daemon: Optional[Daemon] = None
        self._lock = None  # singleton guard held while WE run the watcher
        # Latched by the "kill process" button. Once set, nothing in this window
        # may start a watcher again - not applying a setting, not closing the
        # window - until the app is restarted. A "stop" that something quietly
        # undoes a moment later is worse than no stop button at all.
        self.service_stopped = False
        # Thread -> UI message pump so worker threads never touch widgets.
        self._events: "queue.Queue" = queue.Queue()
        self._pump_id = self.after(100, self._pump)

        self._install_reload_signal()
        self._build_chrome()
        # Everything below the brand bar lives in a scrolling viewport, so no
        # card can ever end up drawn past the bottom of the screen.
        self.scroll = ScrollArea(self)
        self.scroll.pack(fill="both", expand=True)
        self.container = ttk.Frame(self.scroll.inner,
                                   padding=(PAD + 4, 0, PAD + 4, PAD + 4))
        self.container.pack(fill="both", expand=True)
        self.bind("<Prior>", lambda _e: self.scroll.canvas.yview_scroll(-1, "pages"))
        self.bind("<Next>", lambda _e: self.scroll.canvas.yview_scroll(1, "pages"))
        self._show_initial()

    # ----- infrastructure ---------------------------------------------
    def _install_reload_signal(self):
        """Handle SIGHUP so a `lgtv-easy set` from a terminal applies live when
        this window owns the watcher - and, just as importantly, so that nudge
        never falls through to SIGHUP's default action of killing the window.
        POSIX only; harmless when we don't own the daemon."""
        import signal
        if not hasattr(signal, "SIGHUP"):
            return

        def _on_hup(_signum=None, _frame=None):
            if self.daemon is not None:
                self.daemon.request_reload()

        try:
            signal.signal(signal.SIGHUP, _on_hup)
        except (ValueError, OSError):
            pass
    def _build_chrome(self):
        """A persistent brand bar + accent rule across the top of the window."""
        bar = ttk.Frame(self, padding=(PAD + 4, 16, PAD + 4, 12))
        bar.pack(fill="x")
        dot = tk.Canvas(bar, width=12, height=12, highlightthickness=0, bd=0,
                        bg=THEME["bg"])
        dot.create_oval(1, 1, 11, 11, fill=THEME["accent"], outline=THEME["accent"])
        dot.pack(side="left", padx=(0, 9))
        ttk.Label(bar, text="LGTV Companion", style="Brand.TLabel").pack(side="left")
        ttk.Label(bar, text="Easy Mode", style="Sub.TLabel").pack(
            side="left", padx=(8, 0))
        tk.Frame(self, height=2, bg=THEME["accent"]).pack(fill="x")

    def post(self, fn):
        """Schedule ``fn`` to run on the UI thread from any thread."""
        self._events.put(fn)

    def _pump(self):
        # Drain queued UI callbacks. Each is isolated in try/except: one failing
        # callback (e.g. writing to a widget the wizard just destroyed) must not
        # stop the pump, or the whole window would freeze. We always reschedule.
        try:
            while True:
                fn = self._events.get_nowait()
                try:
                    fn()
                except Exception:  # noqa: BLE001 - keep the pump alive
                    pass
        except queue.Empty:
            pass
        self._pump_id = self.after(100, self._pump)

    def _clear(self):
        for child in self.container.winfo_children():
            child.destroy()

    def _show_initial(self):
        if self.cfg.setup_complete and self.cfg.device.paired:
            self.show_settings()
        else:
            self.show_wizard()

    def show_wizard(self):
        self._clear()
        SetupWizard(self.container, self).pack(fill="both", expand=True)
        self.scroll.to_top()
        self._fit_to_content()

    def show_settings(self):
        self._clear()
        SettingsPanel(self.container, self).pack(fill="both", expand=True)
        self.start_daemon()
        self.scroll.to_top()
        self._fit_to_content()

    def _fit_to_content(self):
        """Open as tall as the active panel needs, but never taller than the
        screen - past that, the viewport scrolls.

        Both halves matter. Every panel pins its footer to its own bottom edge,
        so a window shorter than its content used to silently clip the last card
        (the deep power-off slider was the usual casualty); growing to the
        requested height fixes that where there is room. Where there isn't - a
        laptop, or a 1080p screen showing the warning banner as well - the
        window stops at the usable screen height and :class:`ScrollArea` takes
        over, which is why the minimum size stays small instead of being pinned
        to the content height.
        """
        def fit():
            self.update_idletasks()
            avail_w, avail_h = usable_screen(self)
            # Room the panel can have = the screen, less our own fixed chrome.
            chrome = max(0, self.winfo_reqheight() - self.scroll.winfo_reqheight())
            self.scroll.set_max_height(avail_h - chrome)
            self.update_idletasks()
            height = min(self.winfo_reqheight(), avail_h)
            width = min(max(self.winfo_width(), self.winfo_reqwidth(),
                            WANT_WIDTH), avail_w)
            self.minsize(min(MIN_WIDTH, avail_w), min(MIN_HEIGHT, height))
            self.geometry(f"{width}x{height}")
        self.after_idle(fit)

    # ----- daemon lifecycle -------------------------------------------
    def start_daemon(self):
        """Watch for idle while the window is open - but only if nobody else is.

        A background supervisor (the launcher) or a login auto-start entry may
        already be driving the TV. Exactly one watcher must own it, so we take
        the same single-instance lock the headless ``run`` command uses. If it's
        already held, we leave the running watcher alone and just act as a
        settings panel; the status line says so.
        """
        if self.service_stopped:
            return  # deliberately killed; only restarting the app resumes it
        if self.daemon:
            # We own the watcher in-process: the daemon already shares this very
            # config object, so the edit is visible; nudge it to apply now.
            self.daemon.config = self.cfg
            self.daemon.nudge()
            return
        if not self.cfg.device.paired:
            return
        from .singleton import SingleInstance
        if self._lock is None:
            self._lock = SingleInstance("daemon")
        if not self._lock.acquire(wait=False):
            self._lock = None  # someone else owns the watcher; don't compete
            return
        self.daemon = Daemon(self.cfg)
        self.daemon.start()

    def watcher_holder(self):
        """PID of whatever process currently owns the watcher lock (or None)."""
        from .singleton import SingleInstance
        return SingleInstance("daemon").holder()

    def notify_running_daemon(self):
        """Tell a *separate* background watcher to re-read the settings we just
        saved, so the change applies at once instead of on its next restart.

        A no-op when this window owns the watcher (it already shares the config
        object and was nudged directly) or when the OS has no SIGHUP (Windows):
        ``signal`` never targets our own process.
        """
        import signal
        sig = getattr(signal, "SIGHUP", None)
        if sig is None:
            return
        from .singleton import SingleInstance
        SingleInstance("daemon").signal(sig)

    def stop_service(self) -> str:
        """Stop the watcher outright and keep it stopped. Returns what happened.

        Three things can be driving the TV, and stopping only some of them looks
        exactly like the button not working:

        1. a daemon running inside THIS window,
        2. a separate daemon process (the login auto-start, or a supervised one),
        3. the launcher's supervisor - which restarts its daemon five seconds
           after it dies, so killing the daemon while leaving this alive would
           undo itself before the user finished reading the message.

        The supervisor goes first for exactly that reason; its own handler takes
        its daemon child down with it. Nothing here uses SIGTERM - see
        SingleInstance.stop_holder - so the TV is left exactly as it is.
        """
        from .singleton import SingleInstance
        self.service_stopped = True  # latch first: nothing may re-arm behind us
        stopped = []
        if self.daemon:
            self.daemon.stop()
            self.daemon = None
            stopped.append("this window's watcher")
        if self._lock:
            self._lock.release()
            self._lock = None
        # The name checks matter most on Windows, which recycles pids freely: a
        # pidfile left by a killed supervisor keeps a number that some innocent
        # program may since have inherited.
        supervisor = SingleInstance("launcher").stop_holder(
            expect=("powershell", "pwsh", "bash"))
        if supervisor:
            stopped.append(f"supervisor pid {supervisor}")
        watcher = SingleInstance("daemon").stop_holder(expect=("python",))
        if watcher:
            stopped.append(f"watcher pid {watcher}")
        if not stopped:
            return "Nothing was running."
        return "Stopped " + ", ".join(stopped) + "."

    def on_close(self):
        if self.daemon:
            self.daemon.stop()
            self.daemon = None
        if self._lock:
            self._lock.release()
            self._lock = None
        # Cancel the pending pump callback so it can't fire on a destroyed window.
        if getattr(self, "_pump_id", None) is not None:
            try:
                self.after_cancel(self._pump_id)
            except tk.TclError:
                pass
            self._pump_id = None
        self.destroy()


class SetupWizard(ttk.Frame):
    """Three-step wizard: find -> pair -> timeout."""

    def __init__(self, parent, app: App):
        super().__init__(parent)
        self.app = app
        self.step = 0
        self.found = []
        self.selected_ip = tk.StringVar(value=app.cfg.device.ip)
        self.selected_name = tk.StringVar(value=app.cfg.device.name or "My LG TV")
        self.client_key = app.cfg.device.key
        self.secure = app.cfg.device.secure
        self._build_step1()

    def _header(self, title, subtitle):
        ttk.Label(self, text=title, style="Title.TLabel").pack(anchor="w", pady=(8, 0))
        ttk.Label(self, text=subtitle, style="Sub.TLabel",
                  wraplength=440, justify="left").pack(anchor="w", pady=(4, PAD))

    def _step_badge(self, n):
        ttk.Label(self, text=f"STEP {n} OF 3", style="Sub.TLabel").pack(anchor="w")

    def _reset(self):
        for c in self.winfo_children():
            c.destroy()

    def _card(self):
        card = ttk.Frame(self, style="Card.TFrame", padding=PAD)
        card.pack(fill="x", pady=(0, PAD))
        return card

    def _make_diag(self, parent=None, height=6):
        """A read-only, scrollable text area for diagnostics, plus a thread-safe
        appender. Worker threads call the returned function via app.post()."""
        return make_diag(self.app, parent or self, height)

    # ----- step 1: find ------------------------------------------------
    def _build_step1(self):
        self._reset()
        self._step_badge(1)
        self._header("Find your TV",
                     "Make sure your LG TV is switched on and on the same "
                     "network as this PC.")

        card = self._card()
        self.listbox = tk.Listbox(card, height=5, background=THEME["inset"],
                                  foreground=THEME["text"],
                                  selectbackground=THEME["accent"],
                                  selectforeground="#FFFFFF", relief="flat",
                                  borderwidth=0, highlightthickness=1,
                                  highlightbackground=THEME["border"],
                                  highlightcolor=THEME["accent"],
                                  font=(THEME["ui"], 10), activestyle="none")
        self.listbox.pack(fill="x")
        self.scan_status = ttk.Label(card, text="", style="CardMuted.TLabel")
        self.scan_status.pack(anchor="w", pady=(8, 0))

        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x", pady=(PAD, 0))
        ttk.Button(row, text="Scan for TVs", command=self._scan).pack(side="left")
        ttk.Label(row, text="or type the IP:", style="CardMuted.TLabel").pack(
            side="left", padx=(10, 6))
        ttk.Entry(row, textvariable=self.selected_ip, width=16).pack(
            side="left", fill="x", expand=True)

        ttk.Label(self, text="Details", style="Sub.TLabel").pack(anchor="w")
        self.diag = self._make_diag(height=5)
        # Show which network this PC is on up front: a TV that won't be found is
        # most often simply on a different network/subnet than the PC.
        threading.Thread(target=lambda: subnet_report("", self.diag),
                         daemon=True).start()

        ttk.Button(self, text="Next  →", style="Accent.TButton",
                   command=self._goto_pair).pack(side="bottom", anchor="e",
                                                 pady=(PAD, 0))

    def _scan(self):
        self.scan_status.config(text="Scanning the network…")
        self.listbox.delete(0, tk.END)

        def worker():
            results = discover_tvs(log=self.diag)
            self.app.post(lambda: self._scan_done(results))

        threading.Thread(target=worker, daemon=True).start()

    def _scan_done(self, results):
        self.found = results
        if not results:
            self.scan_status.config(
                text="No TVs found. Type the IP address manually above.")
            return
        for dev in results:
            self.listbox.insert(tk.END, f"{dev.name}   ({dev.ip})")
        self.listbox.selection_set(0)
        self.scan_status.config(text=f"Found {len(results)} TV(s).")

    def _goto_pair(self):
        sel = self.listbox.curselection()
        if sel and self.found:
            dev = self.found[sel[0]]
            self.selected_ip.set(dev.ip)
            self.selected_name.set(dev.name)
        if not self.selected_ip.get().strip():
            messagebox.showwarning("Pick a TV",
                                   "Choose a TV from the list or type its IP.")
            return
        self._build_step2()

    # ----- step 2: pair ------------------------------------------------
    def _build_step2(self):
        self._reset()
        self._step_badge(2)
        self._header("Pair with the TV",
                     f"Connecting to {self.selected_ip.get()} …")

        card = self._card()
        self.pair_status = ttk.Label(card, text="Connecting…",
                                     style="Card.TLabel", wraplength=440)
        self.pair_status.pack(anchor="w")
        self.progress = ttk.Progressbar(card, mode="indeterminate")
        self.progress.pack(fill="x", pady=(PAD, 0))
        self.progress.start(12)

        ttk.Label(self, text="Details", style="Sub.TLabel").pack(anchor="w")
        self.diag = self._make_diag(height=6)
        nav = ttk.Frame(self)
        nav.pack(side="bottom", fill="x", pady=(PAD, 0))
        ttk.Button(nav, text="←  Back", style="Ghost.TButton",
                   command=self._build_step1).pack(side="left")
        self._pair()

    def _pair(self):
        ip = self.selected_ip.get().strip()

        def worker():
            # Surface the subnet check immediately (incl. the Google/Nest Wifi
            # double-NAT hint) so a mismatch is obvious before any timeout.
            subnet_report(ip, self.diag)
            client = WebOSClient(ip)
            try:
                key = pair_with_fallback(
                    client,
                    client_key=self.client_key,
                    on_prompt=lambda: self.app.post(self._prompt_accept),
                    prompt_timeout=120.0, log=self.diag,
                    prefer_secure=self.secure)
                secure = client.secure
                self.app.post(lambda: self._pair_done(key, secure))
            except Exception as exc:  # noqa: BLE001
                probe_tv(ip, self.diag)
                self.app.post(lambda e=exc: self._pair_failed(e))
            finally:
                client.close()

        threading.Thread(target=worker, daemon=True).start()

    def _prompt_accept(self):
        self.pair_status.config(
            text="👉  Look at your TV: press OK / Accept on the pairing prompt "
                 "with the remote.")

    def _pair_done(self, key, secure=False):
        self.client_key = key
        self.secure = secure
        self.progress.stop()
        self._build_step3()

    def _pair_failed(self, exc):
        self.progress.stop()
        self.pair_status.config(
            text=f"Could not pair: {exc}\n\nCheck the TV is on and the IP is "
                 "correct, then try again.")

    # ----- step 3: timeout --------------------------------------------
    def _build_step3(self):
        self._reset()
        self._step_badge(3)
        self._header("Sleep timeout",
                     "How long should the PC be idle before the TV screen "
                     "turns off?")

        card = self._card()
        self.sleep_slider = SteppedSlider(
            card, values=SLEEP_STEPS_SEC, initial=self.app.cfg.idle_minutes * 60,
            fmt=lambda s: f"{fmt_timeout(s)} of inactivity")
        self.sleep_slider.pack(fill="x")
        ttk.Label(self, text="Tip: 7 minutes is a good default for a desk "
                             "monitor.", style="Sub.TLabel").pack(anchor="w")
        ttk.Button(self, text="Finish  ✓", style="Accent.TButton",
                   command=self._finish).pack(side="bottom", anchor="e",
                                              pady=(PAD, 0))

    def _finish(self):
        cfg = self.app.cfg
        cfg.device = Device(name=self.selected_name.get(),
                            ip=self.selected_ip.get().strip(),
                            mac=cfg.device.mac, key=self.client_key,
                            secure=self.secure)
        cfg.idle_minutes = self.sleep_slider.value() / 60.0
        cfg.idle_enabled = True
        cfg.setup_complete = True
        cfg.save()
        self.app.show_settings()
        self.app.notify_running_daemon()


class SettingsPanel(ttk.Frame):
    """The everyday screen: big On/Off switch + timeout slider."""

    def __init__(self, parent, app: App):
        super().__init__(parent)
        self.app = app
        cfg = app.cfg
        self.enabled = tk.BooleanVar(value=cfg.idle_enabled)
        self.mute = tk.BooleanVar(value=cfg.mute_on_sleep)
        self.follow_sleep = tk.BooleanVar(value=cfg.screen_off_on_pc_sleep)
        self.deep = tk.BooleanVar(value=cfg.deep_off_enabled)
        self.only_mine = tk.BooleanVar(value=cfg.only_my_input)
        self.autostart = tk.BooleanVar(value=autostart_mod.is_enabled())
        self._status_dot = None
        self._build()

    # ----- small builders ---------------------------------------------
    def _card(self, title=None):
        card = ttk.Frame(self, style="Card.TFrame", padding=PAD)
        card.pack(fill="x", pady=(0, PAD - 4))
        if title:
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(
                anchor="w", pady=(0, 6))
        return card

    def _switch_row(self, parent, text, variable, command, desc=None):
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=4)
        labels = ttk.Frame(row, style="Card.TFrame")
        labels.pack(side="left", fill="x", expand=True)
        ttk.Label(labels, text=text, style="Card.TLabel").pack(anchor="w")
        if desc:
            ttk.Label(labels, text=desc, style="CardMuted.TLabel",
                      wraplength=360, justify="left").pack(anchor="w")
        ToggleSwitch(row, variable, command=command,
                     bg=THEME["surface"]).pack(side="right", padx=(10, 0))
        return row

    def _build(self):
        cfg = self.app.cfg

        # Footer first, pinned to the bottom so the actions stay visible no matter
        # how tall the cards above end up.
        nav = ttk.Frame(self)
        nav.pack(side="bottom", fill="x", pady=(PAD, 0))
        ttk.Button(nav, text="Test my TV", command=self._test).pack(side="left")
        ttk.Button(nav, text="Re-run setup", style="Ghost.TButton",
                   command=self.app.show_wizard).pack(side="left", padx=6)
        ttk.Label(nav, text=f"v{__version__}", style="Sub.TLabel").pack(side="right")
        self._kill_btn = ttk.Button(nav, text="Kill process",
                                    style="DangerGhost.TButton",
                                    command=self._kill_service)
        self._kill_btn.pack(side="right", padx=6)

        statusrow = ttk.Frame(self)
        statusrow.pack(side="bottom", fill="x", pady=(PAD - 4, 0))
        self._status_dot = tk.Canvas(statusrow, width=10, height=10,
                                     highlightthickness=0, bd=0, bg=THEME["bg"])
        self._status_dot.pack(side="left", padx=(0, 8), pady=(3, 0), anchor="n")
        self.status = ttk.Label(statusrow, text="", style="Sub.TLabel",
                                wraplength=420, justify="left")
        self.status.pack(side="left", fill="x", expand=True)

        # If the saved TV has gone missing, say so in red before anything else.
        # An unreadable or wiped config silently loads as "no TV", and without
        # this the everyday screen still looks perfectly healthy while the app
        # controls nothing at all.
        reason = cfg.unconfigured_reason()
        if reason is not None:
            make_no_tv_banner(self, reason, on_setup=self.app.show_wizard)

        # Compact "connected to" line instead of a whole card. Kept on the panel
        # so the startup self-test / repair can update the address if the TV moved.
        self._conn_label = ttk.Label(
            self, text=self._conn_text(), style="Sub.TLabel")
        self._conn_label.pack(anchor="w", pady=(0, PAD - 2))

        # Hero: the big switch + timeout slider.
        hero = self._card()
        top = ttk.Frame(hero, style="Card.TFrame")
        top.pack(fill="x")
        tl = ttk.Frame(top, style="Card.TFrame")
        tl.pack(side="left", fill="x", expand=True)
        ttk.Label(tl, text="Turn the screen off when I'm away",
                  style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(tl, text="Any key or mouse move wakes it.",
                  style="CardMuted.TLabel").pack(anchor="w")
        ToggleSwitch(top, self.enabled, command=self._apply,
                     bg=THEME["surface"]).pack(side="right", padx=(10, 0))

        ttk.Label(hero, text="Sleep after", style="CardMuted.TLabel").pack(
            anchor="w", pady=(PAD - 2, 0))
        self.sleep_slider = SteppedSlider(
            hero, values=SLEEP_STEPS_SEC, initial=cfg.idle_minutes * 60,
            fmt=fmt_timeout, command=self._apply)
        self.sleep_slider.pack(fill="x")

        # When it sleeps.
        opts = self._card("When it sleeps")
        self._switch_row(opts, "Also mute the speakers", self.mute, self._apply)
        self._switch_row(opts, "Sleep the TV when the PC sleeps",
                         self.follow_sleep, self._apply,
                         desc="Follows the PC into and back out of suspend.")
        self._switch_row(
            opts, "Only when the TV is showing this PC", self.only_mine,
            self._apply,
            desc="Leaves the TV alone when another computer or a TV app is on "
                 "screen. Turn off only if this PC's input is misdetected.")
        # Live read-out of the input learning, indented under its toggle. Which
        # socket this PC is on is worked out from the TV rather than configured,
        # and until this line existed there was no way to see whether that had
        # happened - so "it isn't blanking" and "it hasn't learned yet" looked
        # identical from the window.
        self._input_line = ttk.Label(opts, style="CardMuted.TLabel",
                                     wraplength=380, justify="left")
        self._input_line.pack(anchor="w", padx=(0, 0), pady=(0, 4))
        self._refresh_input_line()
        self._watch_input()

        # More options: energy saving + start at login. The "Power off after"
        # slider only makes sense once deep power-off is on, so it's revealed with
        # the toggle (progressive disclosure) rather than sitting there inert while
        # the feature is off. The reveal is an in-place show/hide - no rebuild - so
        # it's instant, and the slider applies its timing live as it's dragged.
        more = self._card("More options")
        self._switch_row(
            more, "Fully power the TV off after a longer idle", self.deep,
            self._apply_deep, desc="Maximum energy saving; wakes over Wake-on-LAN.")
        self._deep_row = ttk.Frame(more, style="Card.TFrame")
        ttk.Label(self._deep_row, text="Power off after",
                  style="CardMuted.TLabel").pack(anchor="w")
        self.deep_slider = SteppedSlider(
            self._deep_row, values=DEEP_STEPS_SEC, initial=cfg.deep_off_minutes * 60,
            fmt=fmt_timeout, command=self._apply)
        self.deep_slider.pack(fill="x")
        # Keep the login row's handle so the slider can be inserted just above it
        # (pack with `before=`) when revealed, preserving the card's order.
        self._autostart_row = self._switch_row(
            more, "Start automatically when I log in",
            self.autostart, self._apply_autostart)
        self._sync_deep_row()

        self._refresh_status()
        self._kickoff_selftest()

    # ----- which input this PC is on -----------------------------------
    def _adopt_learned_device(self) -> bool:
        """Pull in what the watcher has learned about the TV since we loaded.

        The daemon works things out at runtime - the TV's address, its MAC, and
        which input this PC occupies - and persists them itself. ``App.cfg`` is
        loaded once at startup, so when the watcher is a separate background
        process (the normal case) nothing here ever hears about it. Two things
        then go wrong: this panel shows stale state, and saving any setting from
        this window writes our empty fields back over what was learned.

        ``input_id`` takes the file's value outright - the daemon is the only
        thing that sets it, and it may legitimately change when a cable moves.
        The rest only fill blanks, so a value the user actually edited still
        wins. Returns True if anything changed, so the caller can redraw.
        """
        try:
            disk = Config.load()
        except Exception:  # noqa: BLE001 - unreadable or half-written file
            return False
        live = self.app.cfg.device
        changed = False
        if disk.device.input_id != live.input_id:
            live.input_id = disk.device.input_id
            changed = True
        for name in ("ip", "mac", "key"):
            value = getattr(disk.device, name)
            if value and not getattr(live, name):
                setattr(live, name, value)
                changed = True
        if disk.device.secure and not live.secure:
            live.secure = True
            changed = True
        return changed

    def _refresh_input_line(self):
        """Render the three states of the input guard: off, learning, learned."""
        line = getattr(self, "_input_line", None)
        if line is None:
            return
        from .webos import input_label
        input_id = self.app.cfg.device.input_id
        if not self.only_mine.get():
            text = ("Off — the TV is controlled whatever it happens to be showing."
                    if not input_id else
                    f"Off — ignoring that this PC is on {input_label(input_id)}.")
            style = "CardMuted.TLabel"
        elif input_id:
            text = f"✓ Learned — this PC is on {input_label(input_id)}."
            style = "CardOk.TLabel"
        else:
            text = ("Learning… picked up the first time you use this PC while "
                    "the TV is showing it.")
            style = "CardMuted.TLabel"
        try:
            line.config(text=text, style=style)
        except tk.TclError:
            pass

    def _watch_input(self):
        """Re-read the config so the line above goes live.

        The learning happens inside the daemon and lands in the config file, so
        for a background watcher a periodic re-read is the only way this window
        can see it happen. One small JSON file every couple of seconds, and only
        while the panel is on screen.
        """
        if not self.winfo_exists():
            return  # panel swapped out (re-run setup) - stop the timer
        if self._adopt_learned_device():
            self._refresh_input_line()
            self._refresh_conn_label()
        self.after(INPUT_POLL_MS, self._watch_input)

    def _sync_deep_row(self):
        """Show the power-off timing slider iff deep power-off is enabled."""
        if self.deep.get():
            self._deep_row.pack(fill="x", pady=(4, 8), before=self._autostart_row)
        else:
            self._deep_row.pack_forget()

    def _apply_deep(self):
        """Toggle handler for deep power-off: apply, then reveal/hide its slider."""
        self._apply()
        self._sync_deep_row()

    def _apply(self):
        cfg = self.app.cfg
        # Take on anything the watcher learned since this window opened, so the
        # save below carries it forward instead of writing our older, emptier
        # copy of the TV's details back over it.
        self._adopt_learned_device()
        cfg.idle_enabled = self.enabled.get()
        cfg.idle_minutes = self.sleep_slider.value() / 60.0
        cfg.mute_on_sleep = self.mute.get()
        cfg.screen_off_on_pc_sleep = self.follow_sleep.get()
        cfg.only_my_input = self.only_mine.get()
        cfg.deep_off_enabled = self.deep.get()
        cfg.deep_off_minutes = self.deep_slider.value() / 60.0
        cfg.save()
        self._refresh_input_line()  # the guard toggle changes what this says
        self.app.start_daemon()
        self.app.notify_running_daemon()
        self._refresh_status()

    def _apply_autostart(self):
        autostart_mod.set_enabled(self.autostart.get())
        self._refresh_status()

    def _kill_service(self):
        """Stop the watcher for good. No confirmation dialog: this is trivially
        reversible (restart the app) and the message says exactly how."""
        self._killed_detail = self.app.stop_service()
        try:
            self._kill_btn.state(["disabled"])
        except tk.TclError:
            pass
        from .applog import get_logger
        get_logger().info("Service stopped from the settings window: %s",
                          self._killed_detail)
        self._refresh_status()

    def _refresh_status(self):
        cfg = self.app.cfg
        # A stopped service outranks every other status: nothing below is true
        # any more, and the one thing the user needs is how to get it back.
        if self.app.service_stopped:
            if self._status_dot is not None:
                colour = THEME["danger"]
                self._status_dot.delete("all")
                self._status_dot.create_oval(1, 1, 9, 9, fill=colour, outline=colour)
            detail = getattr(self, "_killed_detail", "")
            self.status.config(
                text=(f"{detail} " if detail else "")
                     + "Restart the app to resume service.")
            return
        backend = idle_mod.idle_backend_name()
        warn = "" if idle_mod.is_real_backend() else \
            "  (warning: OS idle detection unavailable here)"
        state = "ON" if cfg.idle_enabled else "OFF"
        deep = (f" Full power-off after {fmt_timeout(cfg.deep_off_minutes * 60)}."
                if cfg.deep_off_enabled else "")
        # Who is actually watching for idle right now: this window, or an
        # already-running background watcher we deliberately didn't duplicate.
        if self.app.daemon is not None:
            who = " Watching now."
        else:
            holder = self.app.watcher_holder()
            who = (f" Running in the background (pid {holder})."
                   if holder else " Watcher will start when you close this window.")
        if self._status_dot is not None:
            colour = THEME["ok"] if cfg.idle_enabled else THEME["muted"]
            self._status_dot.delete("all")
            self._status_dot.create_oval(1, 1, 9, 9, fill=colour, outline=colour)
        self.status.config(
            text=f"Idle-sleep is {state}, after {fmt_timeout(cfg.idle_minutes * 60)}."
                 f"{deep}{who} Idle detection: {backend}.{warn}")

    def _test(self):
        cfg = self.app.cfg
        self.status.config(text="Testing: turning your screen off, then on…")

        def worker():
            from .recovery import connect_tv
            ok, err, showing = True, "", None
            client = None
            try:
                # connect_tv heals a stale IP (DHCP moved the TV) before testing.
                client = connect_tv(cfg, log=lambda _m: None)
                # Ask what's on screen while we're connected. It's the one thing
                # the config can't tell you, and it's how you find out whether
                # this panel can see other computers at all - some older ones
                # won't answer, and then the guard can never engage.
                try:
                    showing = client.get_foreground_input()
                except Exception:  # noqa: BLE001 - the blink still matters
                    showing = None
                client.screen_off()
                import time
                time.sleep(2)
                client.screen_on()
            except Exception as exc:  # noqa: BLE001
                ok, err = False, str(exc)
            finally:
                if client is not None:
                    client.close()
            self.app.post(lambda: self._test_done(ok, err, showing))

        threading.Thread(target=worker, daemon=True).start()

    def _test_done(self, ok, err, showing=None):
        if ok:
            # cfg.device.ip may have just been corrected by the recovery step.
            self._refresh_conn_label()
            from .webos import input_label
            if showing:
                extra = f" It's showing {input_label(showing)}."
            elif showing == "":
                # Answered, but wouldn't say. Worth stating plainly: it means the
                # "only when it's showing this PC" guard can never take effect.
                extra = (" It won't say which input it's on, so Easy Mode can't "
                         "tell when another computer is on screen.")
            else:
                extra = ""
            self.status.config(
                text=f"Test OK — your TV responded at {self.app.cfg.device.ip}. ✓"
                     f"{extra}")
            self._refresh_status()
        else:
            # Don't dead-end on the raw error (the reported bug): open a repair
            # session that diagnoses and fixes it - relocating the TV, reconnecting
            # and blinking the screen - with the full details shown live.
            self.status.config(
                text=f"Couldn't reach your TV ({err}). Starting repair…")
            RepairDialog(self.app, self)

    # ----- connection self-test / repair ------------------------------
    def _conn_text(self) -> str:
        cfg = self.app.cfg
        if not cfg.device.ip:
            return "No TV connected"
        return f"Connected to  {cfg.device.name}  ·  {cfg.device.ip}"

    def _refresh_conn_label(self):
        """Re-render the 'Connected to … · IP' line (the IP can change on repair)."""
        label = getattr(self, "_conn_label", None)
        if label is not None:
            try:
                label.config(text=self._conn_text())
            except tk.TclError:
                pass

    def _kickoff_selftest(self):
        """On startup, quietly verify the TV is reachable and self-heal if not.

        A fast TCP health check decides whether anything is wrong; only if it is
        do we run a background repair (relocate by MAC and persist the corrected
        address - no screen blink, so it's invisible when all is well).
        Gated by LGTV_EASY_NO_SELFTEST so tests and headless CI stay hermetic.

        This one runs on its own the moment the window opens, so it relocates by
        MAC only (allow_guess=False). Adopting an unidentified TV would put a
        pairing prompt on whatever screen it picked, and nobody asked for
        anything yet - they only opened the settings window. The "Test my TV"
        button, which is a request, keeps the wider search.
        """
        import os
        if os.environ.get("LGTV_EASY_NO_SELFTEST") == "1":
            return
        if not self.app.cfg.device.paired:
            return

        def worker():
            from . import selfheal
            cfg = self.app.cfg
            try:
                if selfheal.quick_health_check(cfg):
                    self.app.post(self._refresh_status)
                    return
                res = selfheal.repair(cfg, connect=False, blink=False,
                                      allow_guess=False)
            except Exception:  # noqa: BLE001 - a self-test must never crash the app
                return
            self.app.post(lambda: self._selftest_done(res))

        threading.Thread(target=worker, daemon=True).start()

    def _selftest_done(self, res):
        self._refresh_conn_label()
        if res.repaired and res.ok:
            self.status.config(
                text=f"Reconnected — your TV had moved to {self.app.cfg.device.ip}. ✓")
        elif not res.ok:
            # Unreachable at startup: say so plainly and point at the repair button.
            self.status.config(
                text=f"{res.summary}  (Press “Test my TV” to run a full repair.)")
        else:
            self._refresh_status()


class RepairDialog(tk.Toplevel):
    """A live 'repair session' window opened when the TV can't be reached.

    Runs :func:`selfheal.repair` in a worker thread - probing the network,
    relocating the TV by MAC/discovery, reconnecting and blinking the screen -
    and narrates every step into a scrollable log, ending with a clear outcome
    and a 'Try again' button. On success it refreshes the parent panel, whose
    saved address may have just been corrected.
    """

    def __init__(self, app: App, panel: "SettingsPanel"):
        super().__init__(app)
        self.app = app
        self.panel = panel
        self._running = False
        self.title("Repair TV connection")
        self.configure(bg=THEME["bg"])
        branding.apply_icon(self)
        # Clamp to the display: this dialog opens on top of a failure, which is
        # the worst moment for its buttons to be off the bottom of a small screen.
        avail_w, avail_h = usable_screen(self)
        self.geometry(f"{min(520, avail_w)}x{min(470, avail_h)}")
        self.minsize(min(460, avail_w), min(400, avail_h))
        try:
            self.transient(app)
        except tk.TclError:
            pass
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self.start()

    def _build(self):
        frame = ttk.Frame(self, padding=(PAD + 4, PAD, PAD + 4, PAD + 4))
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Repairing the TV connection",
                  style="Title.TLabel").pack(anchor="w")
        self.status = ttk.Label(frame, text="Looking for your TV…",
                                style="Sub.TLabel", wraplength=460, justify="left")
        self.status.pack(anchor="w", pady=(6, PAD))
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill="x")
        ttk.Label(frame, text="Details", style="Sub.TLabel").pack(
            anchor="w", pady=(PAD, 0))
        self.diag = make_diag(self.app, frame, height=8)
        nav = ttk.Frame(frame)
        nav.pack(fill="x", pady=(PAD, 0))
        self.close_btn = ttk.Button(nav, text="Close", style="Ghost.TButton",
                                    command=self._on_close)
        self.close_btn.pack(side="right")
        self.retry_btn = ttk.Button(nav, text="Try again", style="Accent.TButton",
                                    command=self.start)
        self.retry_btn.pack(side="right", padx=(0, 6))
        self.retry_btn.state(["disabled"])

    def start(self):
        if self._running:
            return
        self._running = True
        try:
            self.retry_btn.state(["disabled"])
            self.status.config(text="Looking for your TV…")
            self.progress.start(12)
        except tk.TclError:
            pass
        diag = self.diag

        def worker():
            from . import selfheal
            res = selfheal.repair(
                self.app.cfg, log=diag, connect=True, blink=True,
                on_prompt=lambda: self.app.post(self._on_prompt),
                prompt_timeout=20.0)
            if res.client is not None:
                try:
                    res.client.close()
                except Exception:  # noqa: BLE001
                    pass
            self.app.post(lambda: self._done(res))

        threading.Thread(target=worker, daemon=True).start()

    def _on_prompt(self):
        try:
            self.status.config(
                text="👉  Look at your TV and press OK / Accept on the pairing "
                     "prompt with the remote.")
        except tk.TclError:
            pass

    def _done(self, res):
        self._running = False
        try:
            self.progress.stop()
            self.status.config(text=res.summary)
            self.retry_btn.state(["!disabled"])
        except tk.TclError:
            pass
        # Reflect the result on the parent panel (the IP may have moved).
        try:
            self.panel._refresh_conn_label()
            self.panel.status.config(text=res.summary)
        except tk.TclError:
            pass

    def _on_close(self):
        # The worker may still be mid-connect; its UI posts no-op against a
        # destroyed window (every callback is TclError-guarded), so closing now
        # is safe - the diagnostics simply stop updating.
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        self.destroy()


def main() -> int:
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
    # Tell the launcher the stop was deliberate, so it does not start its
    # supervisor the moment this window closes and undo the whole thing.
    return EXIT_SERVICE_STOPPED if app.service_stopped else 0


if __name__ == "__main__":
    main()

"""The Windows installer and uninstaller for LGTV Companion Easy Mode.

Built into a single ``LGTVCompanionEasyMode-Setup.exe`` that carries the whole
application inside it (see installer.spec). What it does is deliberately small
and per-user, so it never needs an administrator:

* copies the app into ``%LOCALAPPDATA%\\Programs\\LGTV Companion Easy Mode``
* creates the Start Menu and Desktop shortcuts - stamped with the app's
  AppUserModelID, which is what makes Windows show the app's own icon on the
  taskbar and pin it as one button (see shortcuts.py)
* optionally registers the watcher to start at login
* registers itself in Add/Remove Programs, and can uninstall cleanly

Command line (for scripted installs; everything else opens the window):

    Setup.exe /S                    install silently with the defaults
    Setup.exe /D=<dir>              install somewhere else
    Setup.exe /desktop=0            skip the desktop icon
    Setup.exe /autostart=0          do not start at login
    Setup.exe /launch=0             do not open the app afterwards
    Setup.exe /uninstall [/S]       remove it again
    Setup.exe /uninstall /purge     ...and delete the saved settings too

Every run appends to %TEMP%\\lgtv-easy-setup.log, because a windowed installer
that fails silently is impossible to support.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path

import shortcuts

APP_NAME = "LGTV Companion Easy Mode"
GUI_EXE = "LGTV Companion Easy Mode.exe"
CLI_EXE = "lgtv-easy.exe"
# Must match lgtv_easy.branding.APP_ID - Windows compares the two strings to
# decide whether a running window belongs to a pinned shortcut.
APP_ID = "LGTVCompanion.EasyMode"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\LGTVCompanionEasyMode"
PUBLISHER = "LGTV Companion contributors"
CONFIG_DIR_NAME = "LGTV Companion Easy Mode"      # under %APPDATA%
NO_WINDOW = 0x08000000                            # CREATE_NO_WINDOW

BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PAYLOAD = BUNDLE / "payload"
LOG_FILE = Path(tempfile.gettempdir()) / "lgtv-easy-setup.log"


def _version() -> str:
    try:
        return (BUNDLE / "app-version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.0.0"


VERSION = _version()


def default_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Programs" / APP_NAME


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {message}"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(line)          # a no-op in the windowed build; useful when debugging


# ----- Windows odds and ends -------------------------------------------------
def run_quiet(args, timeout: float = 60.0) -> int:
    """Run a helper without letting a console window flash on screen."""
    try:
        return subprocess.run(args, timeout=timeout, creationflags=NO_WINDOW,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode
    except Exception as exc:  # noqa: BLE001 - a missing tool is not fatal
        log(f"  (command failed: {args[0]}: {exc})")
        return 1


def stop_running_app() -> None:
    """Close a copy that is already running, so its .exe can be replaced.

    The idle watcher runs from the very files we are about to overwrite, and
    Windows locks a running image. Killing it outright is right here: the TV is
    left exactly as it is (the power-off-on-shutdown path is a different signal),
    and the watcher comes back at the next login or app start.
    """
    for image in (GUI_EXE, CLI_EXE):
        run_quiet(["taskkill", "/F", "/IM", image], timeout=20)


def shell_folder(csidl_name: str) -> Path:
    """A known folder from the registry, falling back to the obvious guess."""
    fallbacks = {
        "Desktop": Path.home() / "Desktop",
        "Programs": Path(os.environ.get("APPDATA", Path.home())) / "Microsoft"
                    / "Windows" / "Start Menu" / "Programs",
    }
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
        with key:
            value, _ = winreg.QueryValueEx(key, csidl_name)
            path = Path(os.path.expandvars(value))
            if path.is_dir():
                return path
    except OSError:
        pass
    return fallbacks[csidl_name]


def start_menu_link() -> Path:
    return shell_folder("Programs") / f"{APP_NAME}.lnk"


def desktop_link() -> Path:
    return shell_folder("Desktop") / f"{APP_NAME}.lnk"


def installed_location() -> "Path | None":
    """Where a previous install put itself, if there is one."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "InstallLocation")
            return Path(value) if value else None
    except OSError:
        return None


def dir_size_kb(path: Path) -> int:
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return max(1, total // 1024)


def autostart_registered() -> bool:
    """Whether the app currently starts at login, by either method the app
    supports (the Startup folder entry, or the Scheduled Task it falls back to
    where policy blocks that folder)."""
    base = os.environ.get("APPDATA")
    if base and (Path(base) / "Microsoft" / "Windows" / "Start Menu" /
                 "Programs" / "Startup" / "LGTV-Easy-Mode.cmd").exists():
        return True
    return run_quiet(["schtasks", "/Query", "/TN", APP_NAME], timeout=20) == 0


def current_choices() -> dict:
    """Defaults for the option checkboxes.

    First install: everything on. Upgrading: whatever the user has now - an
    update that silently switches start-at-login back on, or puts back a desktop
    icon they deleted, is an update that overrules them.
    """
    if installed_location() is None:
        return {"desktop": True, "autostart": True}
    return {"desktop": desktop_link().exists(),
            "autostart": autostart_registered()}


# ----- install ----------------------------------------------------------------
def install(dest: Path, *, desktop_icon: bool = True, autostart: bool = True,
            progress=lambda _msg: None) -> Path:
    """Install into ``dest``. Returns the path of the installed app exe."""
    if not PAYLOAD.is_dir():
        raise RuntimeError(
            "This installer has no application inside it (the build step that "
            "embeds packaging/windows/dist did not run).")

    log(f"Installing {APP_NAME} {VERSION} into {dest}")
    progress("Closing any running copy…")
    stop_running_app()

    progress("Copying files…")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PAYLOAD, dest, dirs_exist_ok=True)
    app_exe = dest / GUI_EXE
    icon = dest / "_internal" / "lgtv_easy" / "assets" / "icon.ico"
    if not icon.exists():                     # onedir layout may change someday
        found = next(dest.rglob("icon.ico"), None)
        icon = found or app_exe
    log(f"  copied {dir_size_kb(dest)} KB; icon at {icon}")

    progress("Creating shortcuts…")
    for link, wanted in ((start_menu_link(), True), (desktop_link(), desktop_icon)):
        if not wanted:
            continue
        try:
            shortcuts.create_shortcut(
                link, app_exe, working_dir=str(dest), icon=str(icon),
                description="Sleep your LG TV like a PC monitor", app_id=APP_ID)
            log(f"  shortcut: {link}")
        except OSError as exc:
            # Not fatal: the app is installed and runnable either way.
            log(f"  WARNING: could not create {link}: {exc}")
    if not desktop_icon:
        _remove(desktop_link())
    shortcuts.notify_shell_changed()

    progress("Registering…")
    register_uninstall(dest, app_exe, icon)

    # Do this last: it launches the installed app, which must already be in place.
    progress("Setting up start-at-login…" if autostart else "Finishing…")
    action = "enable" if autostart else "disable"
    run_quiet([str(app_exe), "autostart", action], timeout=90)
    log(f"  autostart {action}d")

    log("Install complete.")
    return app_exe


def register_uninstall(dest: Path, app_exe: Path, icon: Path) -> None:
    """Put the app in Settings -> Apps, with a working Uninstall button."""
    uninstaller = dest / "uninstall.exe"
    try:
        if getattr(sys, "frozen", False):
            shutil.copy2(sys.executable, uninstaller)
    except OSError as exc:
        log(f"  WARNING: could not place the uninstaller: {exc}")

    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": VERSION,
        "DisplayIcon": str(icon),
        "Publisher": PUBLISHER,
        "InstallLocation": str(dest),
        "UninstallString": f'"{uninstaller}" /uninstall',
        "QuietUninstallString": f'"{uninstaller}" /uninstall /S',
        "URLInfoAbout": "https://github.com/routine88/lgtvcompanion-easier",
    }
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            for name, value in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            for name, number in (("NoModify", 1), ("NoRepair", 1),
                                 ("EstimatedSize", dir_size_kb(dest))):
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, number)
    except OSError as exc:
        log(f"  WARNING: could not register in Add/Remove Programs: {exc}")


# ----- uninstall ---------------------------------------------------------------
def _remove(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except OSError as exc:
        log(f"  could not remove {path}: {exc}")


def uninstall(dest: Path, *, purge_settings: bool = False,
              progress=lambda _msg: None) -> None:
    log(f"Uninstalling {APP_NAME} from {dest}")
    app_exe = dest / GUI_EXE

    # Remove the login entry and the shutdown task while the app that knows how
    # to do it still exists on disk.
    progress("Removing the start-at-login entry…")
    if app_exe.exists():
        run_quiet([str(app_exe), "autostart", "disable"], timeout=60)

    progress("Closing the app…")
    stop_running_app()
    time.sleep(0.5)          # let Windows release the image locks

    progress("Removing shortcuts…")
    _remove(start_menu_link())
    _remove(desktop_link())
    shortcuts.notify_shell_changed()

    progress("Removing files…")
    _remove(dest)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass

    if purge_settings:
        base = os.environ.get("APPDATA")
        if base:
            _remove(Path(base) / CONFIG_DIR_NAME)
            log("  deleted saved settings")
    log("Uninstall complete.")


def relaunch_from_temp(args) -> int:
    """Copy ourselves to %TEMP% and re-run, so we can delete our own folder.

    ``uninstall.exe`` lives inside the directory it has to remove, and Windows
    will not delete a running executable. The copy has no such problem.
    """
    temp = Path(tempfile.mkdtemp(prefix="lgtv-uninstall-")) / "uninstall.exe"
    shutil.copy2(sys.executable, temp)
    subprocess.Popen([str(temp), *args], creationflags=NO_WINDOW)
    return 0


# ----- the window ---------------------------------------------------------------
# Palette copied from lgtv_easy.gui so the installer looks like the app it is
# installing. Kept as literals rather than an import: the installer must not
# depend on the payload it carries.
BG, SURFACE, INSET, BORDER = "#15171C", "#1E2128", "#262A33", "#343A45"
TEXT, MUTED, ACCENT, ACCENT_HI, OK = "#ECEEF2", "#98A0AD", "#5B8CFF", "#7AA2FF", "#48D597"


class SetupWindow:
    """A small, dark, two-screen wizard: options, then progress."""

    def __init__(self, mode: str, dest: Path, options: dict):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.mode = mode                     # "install" or "uninstall"
        self.options = options
        self.result = 0

        self.root = tk.Tk(className="LGTVCompanionEasyModeSetup")
        self.root.title(("Install " if mode == "install" else "Uninstall ") + APP_NAME)
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self._icon()
        self._theme()

        self.dest = tk.StringVar(value=str(dest))
        self.desktop = tk.BooleanVar(value=options.get("desktop", True))
        self.autostart = tk.BooleanVar(value=options.get("autostart", True))
        self.launch = tk.BooleanVar(value=options.get("launch", True))
        self.purge = tk.BooleanVar(value=options.get("purge", False))

        self.body = ttk.Frame(self.root, padding=20, style="TFrame")
        self.body.pack(fill="both", expand=True)
        self._build_options()
        self._centre()

    # ----- chrome -------------------------------------------------------
    def _icon(self):
        for name in ("icon.ico",):
            candidate = next(PAYLOAD.rglob(name), None) if PAYLOAD.is_dir() else None
            if candidate:
                try:
                    self.root.iconbitmap(default=str(candidate))
                except Exception:  # noqa: BLE001 - cosmetic
                    pass

    def _theme(self):
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass
        ui = "Segoe UI"
        style.configure(".", background=BG, foreground=TEXT, font=(ui, 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Card.TLabel", background=SURFACE, foreground=TEXT)
        style.configure("Title.TLabel", font=(ui, 17, "bold"))
        style.configure("Sub.TLabel", foreground=MUTED, font=(ui, 9))
        style.configure("CardSub.TLabel", background=SURFACE, foreground=MUTED,
                        font=(ui, 9))
        style.configure("TButton", background=INSET, foreground=TEXT,
                        bordercolor=BORDER, lightcolor=INSET, darkcolor=INSET,
                        borderwidth=1, relief="flat", padding=(14, 8))
        style.map("TButton", background=[("active", BORDER)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF",
                        bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                        borderwidth=0, relief="flat", padding=(18, 9),
                        font=(ui, 10, "bold"))
        style.map("Accent.TButton", background=[("active", ACCENT_HI)])
        # clam draws the tick in "indicatorforeground" on a box filled with
        # "indicatorbackground"; left at their defaults the boxes come out
        # bright white, which on this dark card reads as three flashing errors.
        style.configure("TCheckbutton", background=SURFACE, foreground=TEXT,
                        indicatorbackground=INSET, indicatorforeground=ACCENT,
                        indicatormargin=(0, 0, 8, 0), bordercolor=BORDER,
                        lightcolor=INSET, darkcolor=INSET, focuscolor=SURFACE,
                        padding=(0, 3))
        style.map("TCheckbutton",
                  background=[("active", SURFACE)],
                  indicatorbackground=[("selected", INSET), ("pressed", BORDER)],
                  indicatorforeground=[("selected", ACCENT),
                                       ("active", ACCENT_HI)])
        style.configure("TEntry", fieldbackground=INSET, foreground=TEXT,
                        bordercolor=BORDER, insertcolor=TEXT, relief="flat",
                        padding=6)
        style.configure("TProgressbar", background=ACCENT, troughcolor=INSET,
                        bordercolor=SURFACE, lightcolor=ACCENT, darkcolor=ACCENT)

    def _centre(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 3
        self.root.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")

    def _clear(self):
        for child in self.body.winfo_children():
            child.destroy()

    def _header(self, title, subtitle):
        ttk = self.ttk
        ttk.Label(self.body, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(self.body, text=subtitle, style="Sub.TLabel",
                  wraplength=470, justify="left").pack(anchor="w", pady=(4, 16))

    # ----- screen 1: what will happen ------------------------------------
    def _build_options(self):
        tk, ttk = self.tk, self.ttk
        self._clear()
        existing = installed_location()
        if self.mode == "uninstall":
            self._header(f"Uninstall {APP_NAME}",
                         "This removes the app and its shortcuts. Your TV is "
                         "left exactly as it is.")
            card = ttk.Frame(self.body, style="Card.TFrame", padding=14)
            card.pack(fill="x")
            ttk.Label(card, text=str(self.dest.get()), style="Card.TLabel").pack(anchor="w")
            ttk.Checkbutton(card, text="Also delete my saved settings",
                            variable=self.purge, style="TCheckbutton").pack(
                anchor="w", pady=(10, 0))
            action = "Uninstall"
        else:
            verb = "Update" if existing else "Install"
            self._header(f"{verb} {APP_NAME}",
                         f"Version {VERSION}. Installs for your account only - "
                         "no administrator needed.")
            card = ttk.Frame(self.body, style="Card.TFrame", padding=14)
            card.pack(fill="x")
            ttk.Label(card, text="Install to", style="CardSub.TLabel").pack(anchor="w")
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill="x", pady=(4, 12))
            ttk.Entry(row, textvariable=self.dest, width=44).pack(
                side="left", fill="x", expand=True)
            ttk.Button(row, text="Browse…", command=self._browse).pack(
                side="left", padx=(8, 0))
            ttk.Checkbutton(card, text="Create a desktop icon",
                            variable=self.desktop, style="TCheckbutton").pack(anchor="w")
            ttk.Checkbutton(card, text="Start watching for idle when I log in",
                            variable=self.autostart, style="TCheckbutton").pack(
                anchor="w", pady=(6, 0))
            ttk.Checkbutton(card, text="Open it when the install finishes",
                            variable=self.launch, style="TCheckbutton").pack(
                anchor="w", pady=(6, 0))
            ttk.Label(self.body,
                      text="A Start Menu entry is always created. To keep it on "
                           "the taskbar, right-click the app there once it is "
                           "running and choose “Pin to taskbar”.",
                      style="Sub.TLabel", wraplength=470,
                      justify="left").pack(anchor="w", pady=(12, 0))
            action = verb

        nav = ttk.Frame(self.body)
        nav.pack(fill="x", pady=(18, 0))
        ttk.Button(nav, text="Cancel", command=self._cancel).pack(side="right")
        ttk.Button(nav, text=action, style="Accent.TButton",
                   command=self._go).pack(side="right", padx=(0, 8))
        if self.mode == "install" and existing:
            ttk.Button(nav, text="Uninstall", command=self._switch_to_uninstall).pack(
                side="left")
        self._centre()

    def _browse(self):
        from tkinter import filedialog
        chosen = filedialog.askdirectory(title="Choose a folder to install into")
        if chosen:
            self.dest.set(str(Path(chosen) / APP_NAME
                              if Path(chosen).name != APP_NAME else Path(chosen)))

    def _switch_to_uninstall(self):
        self.mode = "uninstall"
        self.dest.set(str(installed_location() or self.dest.get()))
        self.root.title("Uninstall " + APP_NAME)
        self._build_options()

    def _cancel(self):
        self.result = 1
        self.root.destroy()

    # ----- screen 2: doing it ---------------------------------------------
    def _go(self):
        ttk = self.ttk
        self._clear()
        installing = self.mode == "install"
        self._header("Installing…" if installing else "Uninstalling…",
                     "This takes a few seconds.")
        self.bar = ttk.Progressbar(self.body, mode="indeterminate", length=470)
        self.bar.pack(fill="x")
        self.bar.start(12)
        self.step = ttk.Label(self.body, text="", style="Sub.TLabel",
                              wraplength=470, justify="left")
        self.step.pack(anchor="w", pady=(12, 0))
        self._centre()
        self.root.after(80, self._work)

    def _progress(self, message: str):
        try:
            self.step.config(text=message)
            self.root.update()
        except Exception:  # noqa: BLE001 - window closed
            pass

    def _work(self):
        dest = Path(self.dest.get()).expanduser()
        try:
            if self.mode == "install":
                app_exe = install(dest, desktop_icon=self.desktop.get(),
                                  autostart=self.autostart.get(),
                                  progress=self._progress)
                self._done(app_exe)
            else:
                uninstall(dest, purge_settings=self.purge.get(),
                          progress=self._progress)
                self._done(None)
        except Exception as exc:  # noqa: BLE001 - report, never vanish
            log(f"FAILED: {exc}")
            self._failed(exc)

    def _done(self, app_exe):
        ttk = self.ttk
        self.bar.stop()
        self._clear()
        if app_exe is None:
            self._header("Uninstalled", f"{APP_NAME} has been removed. Your TV "
                                        "settings on the TV itself are untouched.")
        else:
            self._header("Installed",
                         f"{APP_NAME} is in your Start Menu"
                         + (" and on your desktop." if self.desktop.get() else ".")
                         + ("\nIt will start watching for idle when you log in."
                            if self.autostart.get() else ""))
        nav = ttk.Frame(self.body)
        nav.pack(fill="x", pady=(8, 0))
        ttk.Button(nav, text="Close", style="Accent.TButton",
                   command=self.root.destroy).pack(side="right")
        if app_exe is not None and self.launch.get():
            self.root.after(400, lambda: self._launch(app_exe))
        self._centre()

    def _launch(self, app_exe):
        try:
            subprocess.Popen([str(app_exe)], cwd=str(Path(app_exe).parent))
        except OSError as exc:
            log(f"could not start the app: {exc}")
        self.root.destroy()

    def _failed(self, exc):
        ttk = self.ttk
        try:
            self.bar.stop()
        except Exception:  # noqa: BLE001
            pass
        self._clear()
        self.result = 1
        self._header("That didn't work",
                     f"{exc}\n\nThe full log is at {LOG_FILE}")
        ttk.Button(self.body, text="Close", command=self.root.destroy).pack(
            anchor="e", pady=(8, 0))
        self._centre()

    def run(self) -> int:
        self.root.mainloop()
        return self.result


# ----- command line ---------------------------------------------------------------
def parse_args(argv) -> dict:
    """NSIS-style switches: /S, /D=path, /flag=0|1. Case-insensitive.

    ``desktop``/``autostart`` start as None - "not asked for either way" - so a
    plain run can fall back to what the machine already has (see
    :func:`current_choices`) instead of overriding it.
    """
    opts = {"silent": False, "uninstall": False, "purge": False, "dir": None,
            "desktop": None, "autostart": None, "launch": True, "from": None}
    for raw in argv:
        arg = raw.lstrip("-")
        low = arg.lower()
        if low in ("s", "silent", "/s"):
            opts["silent"] = True
        elif low in ("uninstall", "remove", "/uninstall"):
            opts["uninstall"] = True
        elif low == "purge":
            opts["purge"] = True
        elif low.startswith("d="):
            opts["dir"] = arg[2:].strip('"')
        elif low.startswith("from="):
            opts["from"] = arg[5:].strip('"')
        elif "=" in low:
            name, _, value = low.partition("=")
            if name in ("desktop", "autostart", "launch"):
                opts[name] = value not in ("0", "no", "false", "off")
    return opts


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Windows passes /S with a leading slash; strip it into the same namespace.
    opts = parse_args([a[1:] if a.startswith("/") else a for a in argv])

    if opts["uninstall"]:
        dest = Path(opts["from"] or opts["dir"] or installed_location()
                    or default_dir())
        running_inside = getattr(sys, "frozen", False) and \
            Path(sys.executable).resolve().parent == dest.resolve()
        if running_inside and not opts["from"]:
            # Step aside so the folder we are standing in can be deleted.
            passthrough = [f"/from={dest}"] + [a for a in argv
                                               if not a.lower().startswith(("/from", "-from"))]
            return relaunch_from_temp(passthrough)
        if opts["silent"]:
            uninstall(dest, purge_settings=opts["purge"])
            return 0
        return SetupWindow("uninstall", dest, opts).run()

    dest = Path(opts["dir"] or installed_location() or default_dir())
    for name, value in current_choices().items():
        if opts[name] is None:
            opts[name] = value
    if opts["silent"]:
        app_exe = install(dest, desktop_icon=opts["desktop"],
                          autostart=opts["autostart"])
        if opts["launch"]:
            subprocess.Popen([str(app_exe)], cwd=str(dest))
        return 0
    return SetupWindow("install", dest, opts).run()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # noqa: BLE001 - last resort: say something
        log(f"FATAL: {error}")
        try:
            import tkinter.messagebox as mb
            mb.showerror(APP_NAME + " Setup", f"{error}\n\nLog: {LOG_FILE}")
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)

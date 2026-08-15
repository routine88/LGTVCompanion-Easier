"""Icons, window identity, and "how do I launch myself again?".

Three jobs, all of them about Easy Mode looking and behaving like an installed
application rather than a Python script someone happens to be running:

* :func:`apply_icon` puts the real app icon on every window, so the taskbar and
  the alt-tab switcher stop showing the generic Python feather.
* :func:`set_app_id` (Windows) and :data:`WM_CLASS` (Linux) give the process a
  stable identity, which is what lets the OS match a running window to the
  shortcut that started it - the difference between a pinned icon that lights up
  and a second, anonymous one appearing beside it.
* :func:`launch_command` builds the argv needed to start Easy Mode again. It has
  to answer differently depending on how *this* copy was started (from source,
  or as a frozen .exe), and several places - auto-start entries, the "Set up my
  TV" button on the headless warning - would otherwise each get it wrong.

Everything here is best-effort: a missing icon file or an old Windows build must
never stop the app from running.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# The Windows Application User Model ID. Windows groups taskbar buttons and
# matches pinned shortcuts by this string, so the installer stamps the very same
# value onto the shortcuts it creates (see packaging/windows/installer.py).
# If you change it, change it there too.
APP_ID = "LGTVCompanion.EasyMode"

# The X11/Wayland WM_CLASS. Deliberately spelled so that capitalising the first
# letter leaves it unchanged: Tk derives the class half of WM_CLASS from the
# className we pass it, and different Tk builds disagree about whether they
# capitalise it. A name that is already "capitalised" is identical either way,
# which keeps it matching StartupWMClass in the .desktop file - the hook GNOME
# and KDE use to show our icon on the dock button instead of a grey question mark.
WM_CLASS = "LGTVCompanionEasyMode"

# Executable names produced by the Windows build (packaging/windows/app.spec).
GUI_EXE = "LGTV Companion Easy Mode.exe"   # windowed: no console flashes
CLI_EXE = "lgtv-easy.exe"                  # console: for terminal use

ASSETS = Path(__file__).resolve().parent / "assets"

# Largest first: Tk uses the first icon that fits, and window managers scale a
# too-big icon down far more gracefully than they scale a small one up.
_PNG_SIZES = (256, 128, 64, 48, 32, 24, 16)


def frozen() -> bool:
    """True when running from a PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Directory to run from: the exe's folder when frozen, else the package's
    parent (so ``python -m lgtv_easy`` resolves)."""
    if frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def ico_path() -> str:
    """The multi-resolution .ico, or "" if the assets are missing."""
    p = ASSETS / "icon.ico"
    return str(p) if p.exists() else ""


def icon_png() -> str:
    """The 512px master PNG, or "". Used where a single file is wanted - a
    ``.desktop`` ``Icon=`` line, say - rather than a whole set."""
    p = ASSETS / "icon.png"
    return str(p) if p.exists() else ""


def png_paths() -> "list[str]":
    """Every PNG size we ship, largest first."""
    out = []
    for size in _PNG_SIZES:
        p = ASSETS / f"icon-{size}.png"
        if p.exists():
            out.append(str(p))
    return out


def set_app_id(app_id: str = APP_ID) -> bool:
    """Windows: declare this process's Application User Model ID.

    Without it, Windows derives an identity from the host process - which for a
    source checkout is ``python.exe``/``pythonw.exe``, so every Python program on
    the machine shares one taskbar button and one Python icon. Returns True when
    the identity was set; a harmless no-op everywhere else.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        return True
    except Exception:  # noqa: BLE001 - pre-Win7, or a stub shell32
        return False


def apply_icon(window) -> bool:
    """Give a Tk window (root or Toplevel) the app icon. Never raises.

    Uses the .ico on Windows - it carries every size, so the taskbar, the title
    bar and alt-tab each pick the right one - and PNGs elsewhere. ``default=``
    makes it apply to windows opened later too, so dialogs inherit it.
    """
    ok = False
    if os.name == "nt":
        ico = ico_path()
        if ico:
            try:
                window.tk.call("wm", "iconbitmap", window._w, "-default", ico)
                ok = True
            except Exception:  # noqa: BLE001 - old Tk, or an unreadable .ico
                pass
    if ok:
        return True
    paths = png_paths()
    if not paths:
        return False
    try:
        import tkinter as tk
        images = [tk.PhotoImage(master=window, file=p) for p in paths]
        # Tk drops PhotoImages that nothing references, taking the icon with
        # them - park them on the window so they live as long as it does.
        window._icon_images = images
        window.iconphoto(True, *images)
        return True
    except Exception:  # noqa: BLE001 - no PNG support in this Tk build
        return False


def _neighbour_exe(name: str) -> str:
    """A sibling executable of the running one, if it was shipped alongside."""
    candidate = Path(sys.executable).resolve().with_name(name)
    return str(candidate) if candidate.exists() else ""


def _pythonw() -> str:
    """pythonw.exe when it exists, so a background start flashes no console."""
    exe = Path(sys.executable) if sys.executable else Path("python")
    windowed = exe.with_name("pythonw.exe")
    return str(windowed if windowed.exists() else exe)


def launch_command(*args: str, windowed: bool = True) -> "list[str]":
    """argv that starts Easy Mode again with ``args`` (e.g. ``"run"``, ``"gui"``).

    Frozen, that is the app's own executable plus the subcommand - emphatically
    *not* ``-m lgtv_easy``, which a frozen build would hand to the app as two
    stray arguments. From source it is the interpreter plus ``-m lgtv_easy``.
    ``windowed`` picks the console-free variant, which is what anything starting
    at login wants.
    """
    if frozen():
        exe = sys.executable
        wanted = GUI_EXE if windowed else CLI_EXE
        if Path(exe).name.lower() != wanted.lower():
            exe = _neighbour_exe(wanted) or exe
        return [exe, *args]
    python = _pythonw() if (windowed and os.name == "nt") else \
        (sys.executable or "python3")
    return [python, "-m", "lgtv_easy", *args]

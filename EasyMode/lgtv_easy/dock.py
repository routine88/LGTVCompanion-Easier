"""Put Easy Mode on the launcher bar, and take it off again.

"Launcher bar" is whatever strip of always-visible app buttons the desktop calls
its own: the Ubuntu dock / GNOME favourites on Linux, the Quick Launch toolbar
(and, where Windows still permits it, the taskbar) on Windows. The installers
already write a menu entry and a desktop shortcut; neither of those puts an icon
where most people actually click, which is the gap this module fills.

Linux
    GNOME-family shells keep the dock's contents in a GSettings list of *desktop
    file ids* - plain basenames like ``lgtv-companion-easy.desktop`` - so pinning
    is two steps: make sure such an entry exists somewhere the shell looks (the
    installer normally wrote one; a portable run has none, so we write it), then
    add its id to the list. We write to every favourites schema that is actually
    installed rather than guessing which shell is running: pinning inside a shell
    you are not currently using is invisible and correct the next time you log
    into it, whereas guessing wrong leaves the icon missing.

    KDE Plasma keeps the same information inside a panel-applet config file whose
    group path differs from user to user, so there is nothing safe to edit by
    hand. We say so instead of guessing.

Windows
    Quick Launch is a plain folder of shortcuts, so pinning there is one .lnk -
    written by :mod:`lgtv_easy.winshortcut`, which stamps the AppUserModelID that
    makes the running window and the shortcut the same app.

    The taskbar is not a folder. Microsoft removed the programmatic pin in 8.1,
    and the shell verb that used to stand in for it is hidden from non-Explorer
    callers on current builds. We try it anyway - it still works on some - and
    treat failure as ordinary, because the Quick Launch shortcut is already there
    and a hand-pin is one right-click.

Everything here is best-effort: it reports what it managed to do and raises
nothing at the caller, because an app that will not install because it could not
decorate a dock is worse than one without the icon.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import sys
from pathlib import Path

from . import branding
from . import proc

# The desktop-file id the whole project agrees on. It must match APP_ID in
# packaging/linux/install.sh and lgtv_easy.autostart.APP_ID, or we pin an entry
# that the shell cannot resolve and the dock shows nothing at all.
# tests/test_dock.py holds the three together.
DESKTOP_ID = "lgtv-companion-easy"
DESKTOP_FILE = f"{DESKTOP_ID}.desktop"
FRIENDLY = "LGTV Companion Easy Mode"
LINK_NAME = f"{FRIENDLY}.lnk"          # Windows: the Quick Launch shortcut

# What the launcher bar is called in front of the user, per platform. The GUI
# switch and the installers both label themselves from this, so the wording is
# fixed in one place.
BAR_NAME = "Quick Launch" if os.name == "nt" else "the dock"


def _sandbox() -> str:
    """A directory to stand in for the machine's launcher bar.

    The dock is the second thing Easy Mode writes that lives *outside*
    LGTV_EASY_HOME - auto-start was the first: on Linux it is a GSettings key
    belonging to the running shell, on Windows a folder under %APPDATA%. Without
    this, a test calling :func:`add` would pin, and a test calling :func:`remove`
    would unpin, real icons on the dock of whoever ran the suite. tests/conftest
    points it at a throwaway directory for the whole run.
    """
    return os.environ.get("LGTV_EASY_DOCK_SANDBOX", "")


# ----- Linux: the favourites lists -------------------------------------------
# schema, key, and the prefix that schema puts in front of a desktop id. Unity
# is the odd one out with "application://"; everything since spells it plain.
_FAVOURITE_KEYS = (
    ("org.gnome.shell", "favorite-apps", ""),
    ("org.cinnamon", "favorite-apps", ""),
    ("org.buddiesofbudgie.budgie-panel", "pinned-launchers", ""),
    ("com.solus-project.budgie-panel", "pinned-launchers", ""),
    ("com.canonical.Unity.Launcher", "favorites", "application://"),
)


def _run(args, timeout: float = 15.0, env=None) -> "tuple[int, str]":
    """Run a helper and hand back (returncode, stdout). Never raises.

    Through lgtv_easy.proc, not subprocess: the taskbar pin below shells out to
    powershell.exe, and the app that calls it is a windowed process - so a plain
    subprocess call would put a black console box on screen for as long as the
    pin takes.
    """
    try:
        done = proc.run(args, capture_output=True, text=True, timeout=timeout,
                        **({"env": env} if env else {}))
        return done.returncode, (done.stdout or "").strip()
    except Exception:  # noqa: BLE001 - a missing tool is an answer, not a crash
        return 1, ""


def _installed_schemas() -> "set[str]":
    rc, out = _run(["gsettings", "list-schemas"])
    return set(out.split()) if rc == 0 else set()


def _targets() -> "list[tuple[str, str, str]]":
    """The favourites lists we can actually write to on this machine."""
    if _sandbox():
        # One schema, always present: the tests exercise the real code path
        # without depending on what the developer's machine has installed.
        return [_FAVOURITE_KEYS[0]]
    present = _installed_schemas()
    return [entry for entry in _FAVOURITE_KEYS if entry[0] in present]


def _store() -> Path:
    return Path(_sandbox()) / "favourites.json"


def _load_store() -> dict:
    try:
        return json.loads(_store().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _parse_list(text: str) -> "list[str]":
    """Turn what ``gsettings get`` printed into a list of strings.

    An empty list comes back as ``@as []`` - the type annotation GVariant adds
    when the value alone would be ambiguous - which is not Python syntax, so it
    has to come off before literal_eval sees it.
    """
    text = text.strip()
    if text.startswith("@as"):
        text = text[3:].strip()
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return []
    return [str(item) for item in value] if isinstance(value, (list, tuple)) else []


def _get(schema: str, key: str) -> "list[str]":
    if _sandbox():
        return list(_load_store().get(f"{schema} {key}", []))
    rc, out = _run(["gsettings", "get", schema, key])
    return _parse_list(out) if rc == 0 else []


def _set(schema: str, key: str, items) -> bool:
    items = list(items)
    if _sandbox():
        data = _load_store()
        data[f"{schema} {key}"] = items
        try:
            _store().parent.mkdir(parents=True, exist_ok=True)
            _store().write_text(json.dumps(data), encoding="utf-8")
            return True
        except OSError:
            return False
    # repr() of a list of plain strings is already valid GVariant syntax.
    rc, _ = _run(["gsettings", "set", schema, key, repr(items)])
    return rc == 0


# ----- Linux: the desktop entry the dock points at ---------------------------
def _data_home() -> Path:
    if _sandbox():
        return Path(_sandbox()) / "share"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base)


def _data_dirs() -> "list[Path]":
    """Everywhere a menu entry may legitimately live, most-local first."""
    dirs = [_data_home()]
    if not _sandbox():
        extra = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
        dirs += [Path(part) for part in extra.split(":") if part]
    return dirs


def installed_entry() -> "Path | None":
    """The menu entry an installer already wrote, if there is one."""
    for directory in _data_dirs():
        candidate = directory / "applications" / DESKTOP_FILE
        if candidate.is_file():
            return candidate
    return None


def _exec_line() -> str:
    """The ``Exec=`` a menu entry should carry to start the settings window.

    Three cases, in descending order of how well they survive being moved: the
    installed ``lgtv-easy`` command, a frozen executable, and a source checkout -
    where ``-m lgtv_easy`` only resolves from the directory holding the package,
    so the command has to cd there first.
    """
    installed = shutil.which("lgtv-easy") or str(
        Path.home() / ".local" / "bin" / "lgtv-easy")
    if Path(installed).exists():
        return f'"{installed}" gui'
    argv = branding.launch_command("gui", windowed=True)
    inner = " ".join(f'"{part}"' if " " in part else part for part in argv)
    if branding.frozen():
        return inner
    return f"sh -c 'cd \"{branding.app_dir()}\" && {inner}'"


def _entry_content() -> str:
    icon = branding.icon_png()
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        f"Name={FRIENDLY}\n"
        "GenericName=TV screen sleep\n"
        "Comment=Sleep your LG TV like a PC monitor, and wake it when you return\n"
        f"Exec={_exec_line()}\n"
        + (f"Icon={icon}\n" if icon else "") +
        "Terminal=false\n"
        "StartupNotify=true\n"
        # The line that ties the running window to this entry. Without it the
        # dock button shows a grey placeholder labelled "python3" beside the
        # pinned icon rather than lighting the pinned icon up.
        f"StartupWMClass={branding.WM_CLASS}\n"
        "Categories=Utility;Settings;HardwareSettings;\n"
        "Keywords=LG;TV;OLED;monitor;idle;sleep;screen;burn-in;\n"
    )


def ensure_entry() -> "Path | None":
    """The menu entry to pin, writing one first if nothing installed it.

    Returns None only if we could not write one, in which case there is nothing
    a dock could point at.
    """
    existing = installed_entry()
    if existing:
        return existing
    path = _data_home() / "applications" / DESKTOP_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_entry_content(), encoding="utf-8")
        os.chmod(path, 0o644)
    except OSError:
        return None
    if not _sandbox():
        _run(["update-desktop-database", str(path.parent)])
    return path


# ----- Windows: Quick Launch, and a go at the taskbar ------------------------
def _quick_launch_dir() -> Path:
    if _sandbox():
        return Path(_sandbox()) / "Quick Launch"
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Microsoft" / "Internet Explorer" / "Quick Launch"


def quick_launch_link() -> Path:
    """The shortcut we own in the Quick Launch folder.

    Public because the Windows installer creates and deletes the same file: it
    knows the .exe it just copied, so it writes the shortcut itself rather than
    making this module guess where the install went.
    """
    return _quick_launch_dir() / LINK_NAME


# Enumerating the shell verbs is the only way left to reach the taskbar pin, and
# the only way to tell "Windows refused" from "already pinned": when an app is
# pinned, the "Pin to taskbar" verb is replaced by "Unpin from taskbar". The
# names are localised, so this works on English Windows and quietly does nothing
# elsewhere - which is the same outcome as the pin being blocked, and is handled
# the same way.
#
# The two values come in through the environment, not the command line:
# powershell.exe -Command does not populate $args - it appends everything after
# the script to the command string, so a path with a space in it (which
# "Quick Launch" guarantees) would be read as more script. -EncodedCommand or
# $env: are the two ways out; $env: stays readable.
_VERB_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "$f=$env:LGTV_EASY_PIN_TARGET; $pattern=$env:LGTV_EASY_PIN_VERB;"
    "$item=(New-Object -ComObject Shell.Application)"
    ".Namespace((Split-Path -Parent $f)).ParseName((Split-Path -Leaf $f));"
    "if(-not $item){ exit 3 };"
    "$verb=$item.Verbs() | Where-Object { $_.Name.Replace('&','') -match $pattern }"
    " | Select-Object -First 1;"
    "if(-not $verb){ exit 2 };"
    "$verb.DoIt(); exit 0"
)


def _shell_verb(link: Path, pattern: str) -> bool:
    """Invoke a taskbar verb on ``link``. False means Windows did not offer it."""
    if os.name != "nt" or _sandbox():
        return False
    env = dict(os.environ, LGTV_EASY_PIN_TARGET=str(link),
               LGTV_EASY_PIN_VERB=pattern)
    rc, _ = _run(["powershell.exe", "-NoProfile", "-NonInteractive",
                  "-ExecutionPolicy", "Bypass", "-Command", _VERB_SCRIPT],
                 timeout=30, env=env)
    return rc == 0


# Anchored, because "Unpin from taskbar" contains the other one's keyword: a
# loose pattern would have the installer unpin the app it had just pinned.
PIN_VERB = "^Pin to .*taskbar$"
UNPIN_VERB = "^Unpin from .*taskbar$"


def try_pin_to_taskbar(link: Path) -> bool:
    return _shell_verb(link, PIN_VERB)


def try_unpin_from_taskbar(link: Path) -> bool:
    return _shell_verb(link, UNPIN_VERB)


def _write_link(link: Path, target: str = "", icon: str = "") -> Path:
    """Write one .lnk pointing at this copy of the app (or at ``target``)."""
    from . import winshortcut

    link.parent.mkdir(parents=True, exist_ok=True)
    if target:
        exe, arguments = target, ""
    else:
        argv = branding.launch_command("gui", windowed=True)
        exe = argv[0]
        arguments = " ".join(f'"{a}"' if " " in a else a for a in argv[1:])
    if _sandbox():
        # No COM in the test sandbox; the file's existence is what is asserted.
        link.write_text(f"{exe} {arguments}".strip(), encoding="utf-8")
        return link
    winshortcut.create_shortcut(
        link, exe, arguments=arguments, working_dir=str(branding.app_dir()),
        icon=icon or branding.ico_path(), app_id=branding.APP_ID,
        description="Sleep your LG TV like a PC monitor")
    winshortcut.notify_shell_changed()
    return link


# ----- the desktop shortcut ---------------------------------------------------
def desktop_dir() -> Path:
    """The user's Desktop, which is not always ~/Desktop: it is translated on a
    non-English system, and OneDrive moves it on Windows."""
    if _sandbox():
        return Path(_sandbox()) / "Desktop"
    if os.name == "nt":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
            with key:
                value, _ = winreg.QueryValueEx(key, "Desktop")
                path = Path(os.path.expandvars(value))
                if path.is_dir():
                    return path
        except (OSError, ImportError):
            pass
        return Path.home() / "Desktop"
    rc, out = _run(["xdg-user-dir", "DESKTOP"])
    if rc == 0 and out:
        return Path(out)
    return Path.home() / "Desktop"


def desktop_shortcut() -> Path:
    return desktop_dir() / (LINK_NAME if os.name == "nt" else DESKTOP_FILE)


def ensure_desktop_shortcut() -> "Path | None":
    """Put a shortcut on the desktop. None if there is nowhere to put one.

    The installers write their own - they know exactly what they laid down - so
    this is for the portable launchers, which until now left nothing on the
    desktop at all and no way back into the app but the script you started from.
    """
    target = desktop_shortcut()
    if not target.parent.is_dir():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
    try:
        if os.name == "nt":
            _write_link(target)
        else:
            entry = ensure_entry()
            if entry is None:
                return None
            shutil.copyfile(entry, target)
            # GNOME 42+ refuses to run a desktop file it does not trust, and
            # says so with a dialog rather than starting the app.
            os.chmod(target, 0o755)
            if not _sandbox():
                _run(["gio", "set", str(target), "metadata::trusted", "true"])
    except Exception:  # noqa: BLE001 - a decoration on the desktop, never fatal
        return None
    return target


# ----- public API -------------------------------------------------------------
def supported() -> "tuple[bool, str]":
    """Can this desktop's launcher bar be configured from here, and if not, why?

    The reason is written to be shown to the user verbatim: someone on KDE
    should be told the one thing that does work, not that we failed.
    """
    if os.name == "nt":
        return True, ""
    if _targets():
        return True, ""
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "kde" in desktop or "plasma" in desktop:
        return False, ("KDE keeps its Task Manager launchers in the panel's own "
                       "configuration. Right-click Easy Mode in the application "
                       "menu and choose “Pin to Task Manager”.")
    if not shutil.which("gsettings"):
        return False, ("gsettings is not installed, so the dock's contents "
                       "cannot be read or changed from here.")
    return False, (f"This desktop ({desktop or 'unknown'}) does not keep its "
                   "launcher bar anywhere Easy Mode can write. The applications "
                   "menu entry is there; pin it from the menu by hand.")


def is_pinned() -> bool:
    """True when the app is on the launcher bar."""
    try:
        if os.name == "nt":
            return quick_launch_link().exists()
        return any(prefix + DESKTOP_FILE in _get(schema, key)
                   for schema, key, prefix in _targets())
    except OSError:
        return False


def add(*, target: str = "", icon: str = "") -> str:
    """Pin the app. Returns a line describing what happened.

    ``target``/``icon`` are for the Windows installer, which knows exactly which
    .exe it just laid down; left empty we work it out from the running copy.
    """
    if os.name == "nt":
        try:
            link = _write_link(quick_launch_link(), target, icon)
        except OSError as exc:
            # COM said no. Say which shortcut is missing rather than raising
            # into an installer that is otherwise finished and correct.
            return f"could not write the Quick Launch shortcut: {exc}"
        if try_pin_to_taskbar(link):
            return f"pinned to the taskbar and added to Quick Launch ({link})"
        return (f"added to Quick Launch ({link}). Windows does not let an "
                "installer pin to the taskbar; right-click the app there once "
                "it is running and choose “Pin to taskbar”.")

    ok, reason = supported()
    if not ok:
        return reason
    entry = ensure_entry()
    if entry is None:
        return ("could not write the applications-menu entry, so there is "
                "nothing for the dock to point at")
    pinned = []
    for schema, key, prefix in _targets():
        items = _get(schema, key)
        value = prefix + DESKTOP_FILE
        if value in items:
            pinned.append(schema)
            continue
        if _set(schema, key, items + [value]):
            pinned.append(schema)
    if not pinned:
        return "could not change the dock's contents (gsettings refused)"
    return f"added to {BAR_NAME} ({', '.join(pinned)})"


def remove() -> bool:
    """Unpin the app. True if anything was actually removed."""
    if os.name == "nt":
        link = quick_launch_link()
        try_unpin_from_taskbar(link)
        try:
            link.unlink()
            return True
        except OSError:
            return False
    removed = False
    for schema, key, prefix in _targets():
        items = _get(schema, key)
        value = prefix + DESKTOP_FILE
        if value not in items:
            continue
        if _set(schema, key, [item for item in items if item != value]):
            removed = True
    return removed


def set_pinned(pinned: bool) -> str:
    return add() if pinned else (
        f"removed from {BAR_NAME}" if remove() else f"not on {BAR_NAME}")


def status() -> str:
    ok, reason = supported()
    if not ok:
        return f"unavailable - {reason}"
    return f"on {BAR_NAME}" if is_pinned() else f"not on {BAR_NAME}"


if __name__ == "__main__":  # manual check: python -m lgtv_easy.dock [add|remove]
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    print(add() if action == "add" else
          (set_pinned(False) if action == "remove" else status()))

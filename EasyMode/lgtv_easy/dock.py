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


def _try_exec() -> str:
    """The command whose absence should hide this entry, if we can name one."""
    installed = shutil.which("lgtv-easy") or str(
        Path.home() / ".local" / "bin" / "lgtv-easy")
    return installed if Path(installed).exists() else ""


def _action_exec(subcommand: str) -> str:
    """``Exec=`` for a right-click action that runs one CLI subcommand."""
    installed = shutil.which("lgtv-easy") or str(
        Path.home() / ".local" / "bin" / "lgtv-easy")
    if Path(installed).exists():
        return f'"{installed}" {subcommand}'
    argv = branding.launch_command(subcommand, windowed=True)
    inner = " ".join(f'"{part}"' if " " in part else part for part in argv)
    if branding.frozen():
        return inner
    return f"sh -c 'cd \"{branding.app_dir()}\" && {inner}'"


def _icon_dir() -> Path:
    return _data_home() / "icons" / "hicolor"


def install_icons() -> int:
    """Copy the app's PNGs into the icon theme. Returns how many were placed.

    Without these, ``Icon=lgtv-companion-easy`` resolves to nothing and the dock
    shows a grey placeholder - which to the user looks exactly like the shortcut
    having failed to appear at all.
    """
    placed = 0
    for source in sorted(branding.ASSETS.glob("icon-*.png")):
        size = source.stem.split("-", 1)[1]
        if not size.isdigit():
            continue
        target = _icon_dir() / f"{size}x{size}" / "apps" / f"{DESKTOP_ID}.png"
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            placed += 1
        except OSError:
            continue
    svg = branding.ASSETS / "icon.svg"
    scalable = _icon_dir() / "scalable" / "apps" / f"{DESKTOP_ID}.svg"
    if svg.exists() and not scalable.exists():
        try:
            scalable.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(svg, scalable)
            placed += 1
        except OSError:
            pass
    if placed and not _sandbox():
        _run(["gtk-update-icon-cache", "-f", "-t", str(_icon_dir())])
    return placed


def _icon_value() -> str:
    """What to put after ``Icon=``.

    The icon-theme *name* whenever the theme can actually resolve it, because a
    name survives the app being moved, reinstalled or run from somewhere else.
    An absolute path is the fallback and nothing more: pointing a permanent
    desktop entry at wherever this copy happens to be running from is how an
    icon quietly breaks the day that folder moves.
    """
    if any((_icon_dir() / f"{size}x{size}" / "apps" / f"{DESKTOP_ID}.png").exists()
           for size in (16, 22, 24, 32, 48, 64, 128, 256, 512)):
        return DESKTOP_ID
    if (_icon_dir() / "scalable" / "apps" / f"{DESKTOP_ID}.svg").exists():
        return DESKTOP_ID
    return branding.icon_png()


def _entry_content() -> str:
    icon = _icon_value()
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        f"Name={FRIENDLY}\n"
        "GenericName=TV screen sleep\n"
        "Comment=Sleep your LG TV like a PC monitor, and wake it when you return\n"
        f"Exec={_exec_line()}\n"
        + (f"TryExec={_try_exec()}\n" if _try_exec() else "")
        + (f"Icon={icon}\n" if icon else "") +
        "Terminal=false\n"
        "StartupNotify=true\n"
        # The line that ties the running window to this entry. Without it the
        # dock button shows a grey placeholder labelled "python3" beside the
        # pinned icon rather than lighting the pinned icon up.
        f"StartupWMClass={branding.WM_CLASS}\n"
        "Categories=Utility;Settings;HardwareSettings;\n"
        "Keywords=LG;TV;OLED;monitor;idle;sleep;screen;burn-in;\n"
        "Actions=Repair;TVOff;\n"
        "\n"
        "[Desktop Action Repair]\n"
        f"Name=Test and repair the TV connection\n"
        f"Exec={_exec_line()}\n"
        "\n"
        "[Desktop Action TVOff]\n"
        "Name=Turn the TV off now\n"
        f"Exec={_action_exec('off')}\n"
    )


# Only Name, Icon and Exec may appear in a [Desktop Action] group. "Terminal"
# there is a spec violation that desktop-file-validate rejects outright - and an
# entry the validator rejects is one some shells decline to show at all, which
# is a dock icon that silently never appears.
_ACTION_KEYS_ALLOWED = ("Name", "Icon", "Exec", "X-")


def _entry_is_broken(path: Path) -> bool:
    """True when an entry on disk would fail desktop-file-validate.

    Only the defect we know we shipped is looked for: a key that is not legal
    inside a [Desktop Action] group. Anything else on disk is left alone - this
    repairs our own past mistakes, it does not police the file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    in_action = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_action = line.startswith("[Desktop Action")
            continue
        if not in_action or "=" not in line or line.startswith("#"):
            continue
        key = line.split("=", 1)[0].strip()
        if not key.startswith(_ACTION_KEYS_ALLOWED):
            return True
    return False


def ensure_entry() -> "Path | None":
    """The menu entry to pin, writing one first if nothing installed it.

    Returns None only if we could not write one, in which case there is nothing
    a dock could point at.
    """
    existing = installed_entry()
    if existing and not _entry_is_broken(existing):
        return existing
    if existing:
        # Ours to fix: an installer we shipped wrote an entry the spec rejects.
        # Rewriting it is why this repair runs at every launch rather than only
        # at install time - nobody reinstalls an app to fix its icon.
        try:
            install_icons()
            existing.write_text(_entry_content(), encoding="utf-8")
            os.chmod(existing, 0o644)
            if not _sandbox():
                _run(["update-desktop-database", str(existing.parent)])
            return existing
        except OSError:
            return existing
    install_icons()
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


# Written the first time we pin, and never removed. It is what separates "this
# user has never had the icon" from "this user had it and took it off their
# dock" - and re-adding an icon somebody deliberately dragged away is the
# behaviour that gets an app uninstalled.
PINNED_ONCE_MARKER = "dock-pinned-once"


def _marker_path() -> Path:
    if _sandbox():
        return Path(_sandbox()) / PINNED_ONCE_MARKER
    from .config import config_dir
    return Path(config_dir()) / PINNED_ONCE_MARKER


def ensure_on_launch() -> str:
    """Make the app's icon exist and, the first time only, put it on the dock.

    Called every time the app opens, because an icon that only ever appears if
    you happen to run an installer is an icon most people never get. Two halves,
    deliberately different:

    * the menu entry and its icons are *repaired* every launch. That is
      correctness, not preference - a missing or spec-invalid entry is a bug
      whoever's machine it is on.
    * the dock pin happens **once**, and is remembered. After that the dock
      belongs to the user, and if they take the icon off it stays off.

    Returns a line for the log. Never raises.
    """
    try:
        entry_is_new = installed_entry() is None
        entry = ensure_entry()
        if entry is None:
            return "could not create the applications-menu entry"
        marker = _marker_path()
        if marker.exists():
            return f"menu entry is in place ({entry.name}); dock left as the user set it"
        result = _pin_and_confirm(entry_is_new)
        if not is_pinned():
            # Deliberately no marker: the pin did not take, so the next launch
            # must try again. Writing "done" for something that did not happen
            # is how an icon stays missing for ever.
            return result
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("", encoding="utf-8")
        except OSError:
            pass
        return result
    except Exception as exc:  # noqa: BLE001 - a decoration, never fatal
        return f"could not set up the launcher icon: {exc}"


# Seconds to let the desktop index a newly written entry before pinning it, and
# how long to watch afterwards before believing the pin survived. Both are
# generous because both are wrong-by-default: the failure they guard against
# reports success at the time and only undoes itself afterwards.
_INDEX_SETTLE_SECONDS = 3.0
_CONFIRM_WINDOW_SECONDS = 12.0
_PIN_ATTEMPTS = 3


def _pin_and_confirm(entry_is_new: bool) -> str:
    """Pin, then watch long enough to know whether it was allowed to stay.

    GNOME Shell keeps its favourites as desktop-file *ids* and silently drops
    any it cannot resolve. Write the entry and pin it in the same breath and the
    Shell has not indexed the new file yet, so it prunes the pin - not at once,
    but seconds later. gsettings reports success, an immediate check agrees, the
    icon never appears, and nothing anywhere records that it failed. Checking
    straight after writing is therefore worse than useless: it produces a
    confident false positive, which is exactly how this shipped broken.

    So: give the desktop time to notice a new entry first, and afterwards watch
    for long enough to catch a late prune.
    """
    import time

    if entry_is_new:
        time.sleep(_INDEX_SETTLE_SECONDS)
    def survives() -> bool:
        """True if the pin is still there after the whole watch window.

        Always checks at least once, however short the window: a confirmation
        that can skip its own check is the false positive all over again.
        """
        deadline = time.monotonic() + _CONFIRM_WINDOW_SECONDS
        while True:
            if not is_pinned():
                return False
            if time.monotonic() >= deadline:
                return True
            time.sleep(1.0)

    last = ""
    for attempt in range(_PIN_ATTEMPTS):
        last = add()
        if survives():
            return last
        if attempt + 1 < _PIN_ATTEMPTS:
            time.sleep(_INDEX_SETTLE_SECONDS)
    return (f"{last} - but the desktop keeps removing it; it has probably not "
            "indexed the new entry yet, and the next launch will try again")


def status() -> str:
    ok, reason = supported()
    if not ok:
        return f"unavailable - {reason}"
    return f"on {BAR_NAME}" if is_pinned() else f"not on {BAR_NAME}"


if __name__ == "__main__":  # manual check: python -m lgtv_easy.dock [add|remove]
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    print(add() if action == "add" else
          (set_pinned(False) if action == "remove" else status()))

"""Cross-platform "start when I log in" support.

Registers (or removes) a per-user auto-start entry that launches the idle daemon
quietly at login, so the TV keeps sleeping on idle without the user having to run
anything. No third-party dependencies, no admin rights.

Methods:

* Linux: a freedesktop ``~/.config/autostart/*.desktop`` entry.
* Windows "startup" (default): a small ``.cmd`` in the user's Startup folder that
  runs the daemon with ``pythonw`` (no console window).
* Windows "task": a per-user Scheduled Task that runs at logon. Useful when the
  Startup folder is restricted by group policy.

Everything here is best-effort and reports what it did; callers handle errors.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import branding

APP_ID = "lgtv-companion-easy"
FRIENDLY = "LGTV Companion Easy Mode"
TASK_NAME = "LGTV Companion Easy Mode"


def _app_dir() -> str:
    """Working directory for a login start: the folder holding the ``lgtv_easy``
    package (so ``-m lgtv_easy`` resolves), or the installed .exe's own folder
    when this is a frozen build."""
    return str(branding.app_dir())


def _command(*args: str) -> "list[str]":
    """argv that starts Easy Mode with ``args``, windowless.

    An installed .exe and a source checkout need completely different argv - the
    frozen app would read ``-m lgtv_easy`` as two junk arguments and refuse to
    start - so every entry written here is built from the one helper that knows
    the difference.
    """
    return branding.launch_command(*args, windowed=True)


def _join(argv) -> str:
    """Render argv as a command line, quoting the parts that need it."""
    return " ".join(f'"{a}"' if (" " in a or not a) else a for a in argv)


def _sandbox() -> str:
    """A directory to pretend is the machine's start-up configuration.

    Set by the test suite (see tests/conftest.py). Everything else Easy Mode
    writes already lives under LGTV_EASY_HOME, but auto-start by definition does
    not: the Startup folder comes from %APPDATA% and a Scheduled Task has no
    path at all. So a test that answered "no" to "start at login" - several of
    the wizard tests do - reached straight past the temporary config directory
    and deleted the *developer's own* login entry and shutdown task. Running the
    tests must not reconfigure the machine running them.
    """
    return os.environ.get("LGTV_EASY_AUTOSTART_SANDBOX", "")


# ----- Windows: Startup folder ------------------------------------------------
def _startup_dir() -> Path:
    sandbox = _sandbox()
    if sandbox:
        return Path(sandbox) / "Startup"
    base = os.environ.get("APPDATA") or str(Path.home())
    return (Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            / "Startup")


def _startup_link() -> Path:
    """The shortcut we prefer to register: Windows runs it with no console.

    A .cmd in the Startup folder means cmd.exe, and cmd.exe means a black
    window flashing up in the user's face at every single login - for a program
    whose entire purpose is to sit quietly in the background. A shortcut runs
    the executable directly, so nothing appears at all.
    """
    return _startup_dir() / f"{FRIENDLY}.lnk"


def _startup_target() -> Path:
    """The older .cmd form. Still written when a shortcut can't be created, and
    always removed on disable so an upgrade never leaves two entries behind."""
    return _startup_dir() / "LGTV-Easy-Mode.cmd"


def _windows_run_cmd_content() -> str:
    """The fallback login script. ``start ""`` hands off and lets cmd.exe close
    immediately, so the console it opens is a flash rather than a window that
    sits there - but a flash is still a flash, which is why the shortcut above
    is tried first."""
    return (
        "@echo off\r\n"
        f'cd /d "{_app_dir()}"\r\n'
        f'start "" {_join(_command("run"))}\r\n'
    )


def _write_startup_shortcut() -> Path:
    """Put a login shortcut in the Startup folder. Raises if it can't."""
    from .winshortcut import create_shortcut
    argv = _command("run")
    link = _startup_link()
    link.parent.mkdir(parents=True, exist_ok=True)
    create_shortcut(link, argv[0], arguments=_join(argv[1:]),
                    working_dir=_app_dir(), icon=branding.ico_path(),
                    description=f"{FRIENDLY} - watch for idle and sleep the TV",
                    app_id=branding.APP_ID)
    return link


# ----- Windows: Scheduled Task ------------------------------------------------
# Both tasks run the application directly. They used to run `cmd /c <wrapper>`,
# which put a console window on screen every time they fired - at login for one,
# and mid-shutdown for the other.


def _run(args) -> "tuple[int, str]":
    if _sandbox():
        # Scheduled Tasks are machine-wide and have no path to redirect, so the
        # only safe thing inside the sandbox is not to call schtasks at all.
        # Reporting failure reads as "there is no task", which is true of the
        # pretend machine the tests are running against.
        return 1, "schtasks not run (LGTV_EASY_AUTOSTART_SANDBOX is set)"
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:  # noqa: BLE001 - tool missing, timeout, etc.
        return 1, str(exc)


def _task_create_args(command=None) -> list:
    """schtasks arguments for the logon task.

    ``/TR`` takes a whole command line, so the executable is quoted for spaces
    and its arguments follow outside the quotes. ``command`` defaults to the
    app's own "run" invocation; it is a parameter so tests can pass a path.
    """
    if command is None:
        argv = _command("run")
    elif isinstance(command, (str, os.PathLike)):
        argv = [str(command)]                   # a bare path
    else:
        argv = [str(part) for part in command]
    return ["schtasks", "/Create", "/TN", TASK_NAME,
            "/TR", _join(argv), "/SC", "ONLOGON", "/F"]


def _task_exists() -> bool:
    if os.name != "nt":
        return False
    rc, _ = _run(["schtasks", "/Query", "/TN", TASK_NAME])
    return rc == 0


def _enable_task() -> str:
    rc, out = _run(_task_create_args())
    if rc != 0:
        raise OSError(f"schtasks could not create the task: {out.strip()}")
    return f"Scheduled Task '{TASK_NAME}'"


def _disable_task() -> bool:
    removed = False
    if _task_exists():
        rc, _ = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        removed = rc == 0
    return removed


# ----- Windows: power-off-at-shutdown Scheduled Task --------------------------
SHUTDOWN_TASK_NAME = "LGTV Companion Easy Mode Shutdown"


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _shutdown_task_xml() -> str:
    argv = _command("off", "--only-if-configured")
    # Trigger on System-log event 1074 (User32) = a shutdown/restart/logoff was
    # initiated - early enough that the network is still up to reach the TV.
    sub = ("&lt;QueryList&gt;&lt;Query Id=\"0\" Path=\"System\"&gt;"
           "&lt;Select Path=\"System\"&gt;*[System[Provider[@Name='User32'] "
           "and (EventID=1074)]]&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;")
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\r\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\r\n'
        '  <RegistrationInfo><Description>Power the LG TV off when the PC shuts down.</Description></RegistrationInfo>\r\n'
        '  <Triggers><EventTrigger><Enabled>true</Enabled>'
        f'<Subscription>{sub}</Subscription></EventTrigger></Triggers>\r\n'
        '  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType>'
        '<RunLevel>LeastPrivilege</RunLevel></Principal></Principals>\r\n'
        '  <Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>'
        '<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>'
        '<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>'
        '<ExecutionTimeLimit>PT30S</ExecutionTimeLimit><Enabled>true</Enabled></Settings>\r\n'
        # Run the app itself. Going through `cmd /c <wrapper.cmd>` put a console
        # window on screen every time the PC shut down.
        f'  <Actions Context="Author"><Exec><Command>{_xml_escape(argv[0])}</Command>'
        f'<Arguments>{_xml_escape(_join(argv[1:]))}</Arguments>'
        f'<WorkingDirectory>{_xml_escape(_app_dir())}</WorkingDirectory>'
        '</Exec></Actions>\r\n'
        '</Task>\r\n'
    )


def enable_shutdown_hook() -> str:
    """Windows: register a task that powers the TV off when the PC shuts down.

    On other platforms this is a no-op - the running daemon catches SIGTERM at
    logout/shutdown and powers the TV off itself.
    """
    if os.name != "nt":
        return "handled by the daemon (SIGTERM)"
    import tempfile
    fd, xml_path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        Path(xml_path).write_text(_shutdown_task_xml(), encoding="utf-16")
        rc, out = _run(["schtasks", "/Create", "/TN", SHUTDOWN_TASK_NAME,
                        "/XML", xml_path, "/F"])
        if rc != 0:
            raise OSError(f"schtasks could not create the shutdown task: {out.strip()}")
    finally:
        try:
            os.remove(xml_path)
        except OSError:
            pass
    return f"Scheduled Task '{SHUTDOWN_TASK_NAME}'"


def disable_shutdown_hook() -> None:
    if os.name != "nt":
        return
    _run(["schtasks", "/Delete", "/TN", SHUTDOWN_TASK_NAME, "/F"])
    # Older versions drove the task through a .cmd next to the config; clear it
    # so an upgraded install doesn't leave litter behind.
    for legacy in ("shutdown-off.cmd", "autostart-run.cmd"):
        try:
            from .config import config_dir
            (Path(config_dir()) / legacy).unlink()
        except OSError:
            pass


# ----- Linux: autostart .desktop ----------------------------------------------
def _linux_target() -> Path:
    base = _sandbox() or os.environ.get("XDG_CONFIG_HOME") \
        or str(Path.home() / ".config")
    return Path(base) / "autostart" / f"{APP_ID}.desktop"


def _linux_desktop_content() -> str:
    inner = " ".join(f'"{a}"' if " " in a else a for a in _command("run"))
    icon = branding.icon_png()
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={FRIENDLY}\n"
        "Comment=Sleep the TV screen when this PC is idle\n"
        f"Exec=sh -c 'cd \"{_app_dir()}\" && {inner}'\n"
        + (f"Icon={icon}\n" if icon else "") +
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


# ----- public API -------------------------------------------------------------
def default_method() -> str:
    return "startup" if os.name == "nt" else "desktop"


def is_enabled() -> bool:
    """True if auto-start is active by *any* supported method."""
    try:
        if os.name == "nt":
            return (_startup_link().exists() or _startup_target().exists()
                    or _task_exists())
        return _linux_target().exists()
    except OSError:
        return False


def _try_enable_shutdown_hook() -> None:
    try:
        enable_shutdown_hook()
    except Exception:  # noqa: BLE001 - must not break login auto-start
        pass


def enable(method: str = "") -> str:
    """Create the auto-start entry. Returns a short label; raises on failure."""
    method = method or default_method()
    if os.name == "nt":
        # Pair it with the "off at shutdown" task (best-effort; never fatal).
        _try_enable_shutdown_hook()
        if method == "task":
            return _enable_task()
        # A shortcut runs the app directly, so nothing flashes on screen at
        # login. Only if that fails do we fall back to the .cmd, which does.
        try:
            link = _write_startup_shortcut()
            _remove(_startup_target())      # drop the old form on upgrade
            return f"Startup folder ({link})"
        except Exception:  # noqa: BLE001 - no COM, an odd profile, a locked dir
            pass
        path = _startup_target()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_windows_run_cmd_content(), encoding="utf-8")
        return f"Startup folder ({path})"
    path = _linux_target()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_linux_desktop_content(), encoding="utf-8")
    return f"autostart entry ({path})"


def _remove(path: Path) -> bool:
    try:
        if path.exists():
            path.unlink()
            return True
    except OSError:
        pass
    return False


def disable() -> bool:
    """Remove the auto-start entry/entries. Returns True if anything was removed."""
    removed = False
    if os.name == "nt":
        # Both forms: an install upgraded from the .cmd era has the shortcut,
        # and leaving the other behind would start the watcher twice.
        for path in (_startup_link(), _startup_target()):
            removed = _remove(path) or removed
        if _disable_task():
            removed = True
        disable_shutdown_hook()
        return removed
    try:
        if _linux_target().exists():
            _linux_target().unlink()
            removed = True
    except OSError:
        pass
    return removed


def set_enabled(enabled: bool, method: str = "") -> str:
    """Convenience: enable or disable, returning a short human status string.

    Never raises - a failure to register auto-start must not abort setup.
    """
    if enabled:
        try:
            return f"auto-start at login ENABLED via {enable(method)}"
        except Exception as exc:  # noqa: BLE001 - never let this break the wizard
            return f"could NOT enable auto-start ({exc}); everything else is set"
    try:
        disable()
    except Exception:  # noqa: BLE001
        pass
    return "auto-start at login DISABLED"


def status() -> str:
    if not is_enabled():
        return "disabled"
    if os.name == "nt":
        how = []
        if _startup_link().exists():
            how.append("Startup folder")
        if _startup_target().exists():
            how.append("Startup folder (.cmd)")
        if _task_exists():
            how.append("Scheduled Task")
        return "enabled (" + ", ".join(how) + ")"
    return f"enabled ({_linux_target()})"

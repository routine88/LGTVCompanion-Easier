"""Tell the user something when there is no window open to tell them in.

The watcher runs unattended for weeks. When it works out that it cannot reach
the TV *and* that the reason needs a human - the pairing was cleared, the PC is
on the wrong network - writing that to a log file nobody reads is the same as
saying nothing, which is exactly how an app comes to "not work" for days without
anyone knowing why.

So: a desktop notification. Deliberately thin, because notifications are the
kind of thing that must never be the reason the watcher fell over:

* every backend is best-effort and returns True/False rather than raising;
* nothing is sent when there is no desktop session to send it to (a headless
  server, an SSH login), where a notification daemon does not exist and the
  attempt would just time out;
* the caller decides *when* - this module has no opinion about rate limiting,
  and will happily send the same thing twice if asked.

Linux uses ``notify-send`` when it is installed and ``gdbus`` otherwise; the
D-Bus method takes an ``a{sv}`` argument that this project's tiny hand-written
marshaller (``_dbus.py``) cannot build, and gdbus can. Windows asks PowerShell
for a toast; that path is written from the API docs and has not been run on a
Windows machine, so it fails quietly like every other backend here.
"""
from __future__ import annotations

import os
import shutil

from . import proc

APP_NAME = "LGTV Companion Easy Mode"
# The desktop-entry id, so the shell can put our icon on the notification and
# route a click back to the app. Must match lgtv_easy.dock.DESKTOP_ID.
DESKTOP_ID = "lgtv-companion-easy"


def have_desktop() -> bool:
    """Is there a graphical session to show a notification in?

    Checked before spending two seconds discovering that a headless machine has
    no notification daemon.
    """
    if os.name == "nt":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _notify_send(title: str, message: str, urgency: str) -> bool:
    if not shutil.which("notify-send"):
        return False
    args = ["notify-send", "--app-name", APP_NAME, "--icon", DESKTOP_ID,
            "--urgency", urgency]
    # Critical notifications stay on screen until dismissed; the rest expire.
    # A message about a TV nobody can reach is worth interrupting for.
    args += ["--expire-time", "0" if urgency == "critical" else "20000"]
    args += [title, message]
    try:
        return proc.run(args, timeout=10).returncode == 0
    except Exception:  # noqa: BLE001 - a notification is never worth an exception
        return False


def _gdbus(title: str, message: str, urgency: str) -> bool:
    if not shutil.which("gdbus"):
        return False
    # Notify(app_name, replaces_id, icon, summary, body, actions, hints, timeout)
    args = [
        "gdbus", "call", "--session",
        "--dest", "org.freedesktop.Notifications",
        "--object-path", "/org/freedesktop/Notifications",
        "--method", "org.freedesktop.Notifications.Notify",
        APP_NAME, "0", DESKTOP_ID, title, message, "[]",
        "{'urgency': <byte %d>}" % (2 if urgency == "critical" else 1),
        "0" if urgency == "critical" else "20000",
    ]
    try:
        return proc.run(args, timeout=10).returncode == 0
    except Exception:  # noqa: BLE001
        return False


# PowerShell, because a toast otherwise needs a packaged app identity or a
# third-party module, and the installer must not depend on either. Written from
# the WinRT documentation and never run on Windows - see the module docstring.
_TOAST_PS = (
    "$ErrorActionPreference='Stop';"
    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
    " ContentType=WindowsRuntime] > $null;"
    "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$n=$t.GetElementsByTagName('text');"
    "$n.Item(0).AppendChild($t.CreateTextNode($env:LGTV_EASY_TOAST_TITLE)) > $null;"
    "$n.Item(1).AppendChild($t.CreateTextNode($env:LGTV_EASY_TOAST_BODY)) > $null;"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
    "$env:LGTV_EASY_TOAST_APPID).Show("
    "[Windows.UI.Notifications.ToastNotification]::new($t))"
)


def _windows_toast(title: str, message: str) -> bool:
    # Through the environment, not the command line: powershell.exe -Command does
    # not populate $args, and a message with a quote in it would otherwise be
    # read as more script.
    env = dict(os.environ, LGTV_EASY_TOAST_TITLE=title,
               LGTV_EASY_TOAST_BODY=message,
               LGTV_EASY_TOAST_APPID="LGTVCompanion.EasyMode")
    try:
        return proc.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                         "-ExecutionPolicy", "Bypass", "-Command", _TOAST_PS],
                        timeout=20, env=env).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def notify(title: str, message: str, *, urgency: str = "normal") -> bool:
    """Put a message on the user's desktop. True if something accepted it.

    ``urgency`` is "normal" or "critical"; critical stays on screen until it is
    dismissed, which is right for "your TV cannot be reached and I need you".
    """
    if os.environ.get("LGTV_EASY_NO_NOTIFY") == "1" or not have_desktop():
        return False
    if os.name == "nt":
        return _windows_toast(title, message)
    return _notify_send(title, message, urgency) or _gdbus(title, message, urgency)

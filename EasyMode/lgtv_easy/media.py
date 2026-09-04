"""Is something playing on this PC right now?

The screen-off and power-off timers exist for a PC nobody is using. A film is
exactly the case where nobody touches the keyboard for two hours and yet the
screen must stay on - so before darkening anything, Easy Mode asks the desktop
whether an app is currently playing.

What we ask is not "is a video decoding" (nothing exposes that) but the signal
apps *already* raise for this precise purpose: the idle inhibitor. A browser
playing video, VLC, mpv and every other player tell the desktop "don't blank the
screen while this plays", which is how a full-screen film keeps a laptop awake.
Reading that back means Easy Mode agrees with the desktop by construction: if
your own monitor would not blank right now, neither will the TV. It also picks
up anything else holding the screen awake - a presentation, a long render - which
is the same answer for the same reason.

Backends, in the order they're tried:

* Linux/GNOME: ``org.gnome.SessionManager.IsInhibited(8)`` - flag 8 is "idle".
  GNOME also names the app and its reason, so the log can say *why* the TV
  stayed on ("Firefox: Playing video").
* Linux/KDE and other freedesktop desktops:
  ``org.freedesktop.PowerManagement.Inhibit.HasInhibit()``.
* Linux, anything else (sway, wlroots): MPRIS - any ``org.mpris.MediaPlayer2.*``
  player reporting ``PlaybackStatus == "Playing"``. Broader than the inhibitor
  (a music player counts too), which is why it is only the fallback.
* Windows: the system's execution-state request. Apps that must keep the display
  on call ``SetThreadExecutionState(ES_DISPLAY_REQUIRED)`` - the same thing
  ``powercfg /requests`` reports - and ``CallNtPowerInformation`` reads the
  current state back without needing an elevated process.
* Anywhere else, or where none of the above answers: "none", which always
  reports "nothing playing" so the timers behave exactly as they always did.

Like :mod:`lgtv_easy.idle` this module never raises: ``is_playing()`` always
returns a bool, so the daemon can call it every poll without a guard.
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import sys
from subprocess import DEVNULL

from . import _dbus
from . import proc

# GNOME session-manager inhibit flags; 8 is "inhibit the session going idle",
# which is the one screen-blanking honours.
INHIBIT_IDLE = 8

# Windows EXECUTION_STATE flag and the CallNtPowerInformation level that reads
# the current state back (SystemExecutionState).
_ES_DISPLAY_REQUIRED = 0x00000002
_SYSTEM_EXECUTION_STATE = 16

_BACKEND = None   # cached (name, is_playing_fn, detail_fn)
_GDBUS_PATH = None


# ----- shared D-Bus plumbing ------------------------------------------------
def _gdbus_path() -> "str | None":
    """Resolve the gdbus binary once (this is polled forever - don't re-scan
    PATH on every call)."""
    global _GDBUS_PATH
    if _GDBUS_PATH is None:
        _GDBUS_PATH = shutil.which("gdbus") or False
    return _GDBUS_PATH or None


def _gdbus_call(dest: str, path: str, method: str, *args: str) -> "str | None":
    """Call a session-bus method through the gdbus CLI; return stdout or None."""
    gdbus = _gdbus_path()
    if not gdbus:
        return None
    try:
        return proc.check_output(
            [gdbus, "call", "--session", "--dest", dest, "--object-path", path,
             "--method", method, *args],
            stderr=DEVNULL, timeout=3, text=True).strip()
    except Exception:  # noqa: BLE001 - "no answer" is a valid outcome
        return None


def _parse_bool(text: "str | None") -> "bool | None":
    """Pull the boolean out of a gdbus reply like '(true,)'."""
    if not text:
        return None
    match = re.search(r"\b(true|false)\b", text)
    return match.group(1) == "true" if match else None


def _parse_strings(text: "str | None") -> "list[str]":
    """Every quoted string in a gdbus reply, in order."""
    if not text:
        return []
    return re.findall(r"'([^']*)'", text)


def _call_bool(dest: str, path: str, interface: str, member: str,
               signature: str = "", args=()) -> "bool | None":
    """A session-bus method returning a bool, native first then gdbus."""
    value = _dbus.session_call(dest, path, interface, member, signature, args)
    if isinstance(value, bool):
        return value
    cli_args = [str(a) for a in args]
    return _parse_bool(_gdbus_call(dest, path, f"{interface}.{member}", *cli_args))


def _call_uint(dest: str, path: str, interface: str, member: str) -> "int | None":
    """A session-bus method returning an unsigned int, native first then gdbus."""
    value = _dbus.session_call(dest, path, interface, member)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = _gdbus_call(dest, path, f"{interface}.{member}")
    if not text:
        return None
    # gdbus spells the type out first ('(uint32 8,)'), and the type name itself
    # contains digits - so match the number that follows it.
    match = re.search(r"u?int\d+\s+(\d+)", text) or re.search(r"(\d+)", text)
    return int(match.group(1)) if match else None


def _call_strings(dest: str, path: str, interface: str, member: str,
                  signature: str = "", args=()) -> "list[str] | None":
    """A session-bus method returning a string or a list of them."""
    value = _dbus.session_call(dest, path, interface, member, signature, args)
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    if isinstance(value, str):
        return [value]
    cli_args = [str(a) for a in args]
    text = _gdbus_call(dest, path, f"{interface}.{member}", *cli_args)
    if text is None:
        return None
    return _parse_strings(text)


# ----- Linux: the desktop's idle inhibitors ---------------------------------
_GNOME_DEST = "org.gnome.SessionManager"
_GNOME_PATH = "/org/gnome/SessionManager"


def _tidy_app_id(app_id: str) -> str:
    """'firefox_firefox' / 'vlc.desktop' -> 'firefox' / 'vlc'.

    Sandboxed apps register a doubled id (snap) or a desktop-file name; neither
    reads well in a log line meant for a person.
    """
    name = app_id.strip()
    if name.endswith(".desktop"):
        name = name[: -len(".desktop")]
    head, sep, tail = name.partition("_")
    if sep and head == tail:
        name = head
    return name or app_id


def _gnome_detail() -> str:
    """Name what is holding the screen awake, e.g. 'Firefox: Playing video'."""
    paths = _call_strings(_GNOME_DEST, _GNOME_PATH, _GNOME_DEST, "GetInhibitors")
    seen = []
    for path in paths or []:
        if not path.startswith("/"):
            continue
        iface = "org.gnome.SessionManager.Inhibitor"
        flags = _call_uint(_GNOME_DEST, path, iface, "GetFlags")
        if flags is not None and not flags & INHIBIT_IDLE:
            continue   # a shutdown/logout inhibitor - nothing to do with idling
        app = (_call_strings(_GNOME_DEST, path, iface, "GetAppId") or [""])[0]
        why = (_call_strings(_GNOME_DEST, path, iface, "GetReason") or [""])[0]
        app = _tidy_app_id(app)
        label = f"{app}: {why}" if app and why else (app or why)
        if label and label not in seen:
            seen.append(label)
    return ", ".join(seen)


def _gnome_backend():
    def _playing() -> bool:
        return bool(_call_bool(_GNOME_DEST, _GNOME_PATH, _GNOME_DEST,
                               "IsInhibited", "u", (INHIBIT_IDLE,)))

    if _call_bool(_GNOME_DEST, _GNOME_PATH, _GNOME_DEST, "IsInhibited", "u",
                  (INHIBIT_IDLE,)) is None:
        return None   # not GNOME, or no session bus
    return ("gnome-inhibit", _playing, _gnome_detail)


_FDO_DEST = "org.freedesktop.PowerManagement"
_FDO_PATH = "/org/freedesktop/PowerManagement/Inhibit"
_FDO_IFACE = "org.freedesktop.PowerManagement.Inhibit"


def _freedesktop_backend():
    """KDE Plasma and friends: powerdevil answers HasInhibit()."""
    def _playing() -> bool:
        return bool(_call_bool(_FDO_DEST, _FDO_PATH, _FDO_IFACE, "HasInhibit"))

    if _call_bool(_FDO_DEST, _FDO_PATH, _FDO_IFACE, "HasInhibit") is None:
        return None
    return ("freedesktop-inhibit", _playing,
            lambda: "an app is asking the screen to stay on")


# ----- Linux: MPRIS, the fallback where no inhibitor query exists ------------
_MPRIS_PREFIX = "org.mpris.MediaPlayer2."
_MPRIS_PATH = "/org/mpris/MediaPlayer2"
_MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"
_PROPS = "org.freedesktop.DBus.Properties"


def _bus_names() -> "list[str] | None":
    return _call_strings("org.freedesktop.DBus", "/org/freedesktop/DBus",
                         "org.freedesktop.DBus", "ListNames")


def _mpris_players() -> "list[str]":
    return [n for n in (_bus_names() or []) if n.startswith(_MPRIS_PREFIX)]


def _mpris_playing_names() -> "list[str]":
    """The MPRIS players currently reporting PlaybackStatus == 'Playing'."""
    playing = []
    for name in _mpris_players():
        status = _call_strings(name, _MPRIS_PATH, _PROPS, "Get", "ss",
                               (_MPRIS_PLAYER, "PlaybackStatus"))
        if status and status[-1] == "Playing":
            playing.append(name[len(_MPRIS_PREFIX):].split(".")[0])
    return playing


def _mpris_backend():
    if _bus_names() is None:
        return None   # no session bus at all
    return ("mpris", lambda: bool(_mpris_playing_names()),
            lambda: ", ".join(_mpris_playing_names()))


# ----- Windows: the system's display request --------------------------------
def _windows_backend():
    """Read back the EXECUTION_STATE any app has asked the system to hold.

    A player that must keep the display on calls ``SetThreadExecutionState`` with
    ``ES_DISPLAY_REQUIRED``; ``CallNtPowerInformation(SystemExecutionState, ..)``
    reports the state currently in force, and unlike ``powercfg /requests`` it
    needs no elevation. Where the OS declines to answer we return None here and
    the caller falls through to the "none" backend, so nothing changes rather
    than something going wrong.
    """
    powrprof = ctypes.WinDLL("powrprof")   # type: ignore[attr-defined]
    powrprof.CallNtPowerInformation.argtypes = [
        ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong,
        ctypes.c_void_p, ctypes.c_ulong]
    powrprof.CallNtPowerInformation.restype = ctypes.c_long
    state = ctypes.c_ulong(0)

    def _query() -> "int | None":
        status = powrprof.CallNtPowerInformation(
            _SYSTEM_EXECUTION_STATE, None, 0,
            ctypes.byref(state), ctypes.sizeof(state))
        return state.value if status == 0 else None

    if _query() is None:
        return None

    def _playing() -> bool:
        value = _query()
        return bool(value and value & _ES_DISPLAY_REQUIRED)

    return ("windows-power-request", _playing,
            lambda: "an app is asking Windows to keep the display on")


# ----- backend selection ----------------------------------------------------
def _select_backend():
    if sys.platform.startswith("win"):
        try:
            backend = _windows_backend()
        except Exception:  # noqa: BLE001 - a missing DLL is not an error here
            backend = None
        if backend:
            return backend
    elif sys.platform.startswith("linux"):
        for factory in (_gnome_backend, _freedesktop_backend, _mpris_backend):
            try:
                backend = factory()
            except Exception:  # noqa: BLE001
                backend = None
            if backend:
                return backend
    return ("none", lambda: False, lambda: "")


def _backend():
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = _select_backend()
    return _BACKEND


def _fake() -> "bool | None":
    """``LGTV_EASY_FAKE_MEDIA`` forces the answer, for tests and headless runs."""
    value = os.environ.get("LGTV_EASY_FAKE_MEDIA")
    if value is None:
        return None
    return value.strip().lower() in ("1", "true", "yes", "on", "playing")


# ----- public ---------------------------------------------------------------
def is_playing() -> bool:
    """True when something on this PC is asking that the screen stay on.

    Never raises: an unreachable bus, a missing service or a malformed reply all
    mean "nothing playing", which leaves the idle timers behaving as before.
    """
    forced = _fake()
    if forced is not None:
        return forced
    try:
        return bool(_backend()[1]())
    except Exception:  # noqa: BLE001
        return False


def playing_detail() -> str:
    """A short human description of what is playing, or '' if we can't say."""
    if _fake() is not None:
        return "forced by LGTV_EASY_FAKE_MEDIA"
    try:
        return _backend()[2]() or ""
    except Exception:  # noqa: BLE001
        return ""


def backend_name() -> str:
    return _backend()[0]


def is_available() -> bool:
    """True when this system can actually tell us whether something is playing."""
    if _fake() is not None:
        return True
    return _backend()[0] != "none"


def reset_backend() -> None:
    """Forget the cached backend. For tests, and after a session change."""
    global _BACKEND
    _BACKEND = None

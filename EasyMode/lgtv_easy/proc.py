"""Run helper programs without flashing a console window on Windows.

Easy Mode's watcher is a *windowed* process: the installed build is
``console=False`` (see packaging/windows/app.spec) and a source checkout starts
at login through ``pythonw``. A windowed process has no console of its own - and
that is exactly what makes plain ``subprocess`` calls so visible here. When such
a process starts a console program (``arp.exe``, ``schtasks.exe``, ...) Windows
has nowhere to attach the child's console, so it *allocates a brand new one* and
puts it on screen. The command finishes in milliseconds and the window vanishes:
a black terminal that blinks in the user's face.

It blinks a lot, too. When the TV is off - which it is every time the PC boots
before the TV is switched on - the daemon keeps trying to find it again, and each
attempt reads the OS ARP table a few times over. With the reconnect backoff
starting at the poll interval and doubling to five minutes, that is a burst of
console flashes at login and every few dozen seconds for the next few minutes,
which is precisely the symptom this module exists to remove.

The cure is two flags, applied to *every* child this app starts:

* ``CREATE_NO_WINDOW`` - the child gets no console at all, so none is created.
* a ``STARTUPINFO`` with ``SW_HIDE`` - belt and braces for the odd program that
  asks for a window itself.

Both are Windows-only and simply absent elsewhere, so every helper here is safe
to call from any platform. Nothing in ``lgtv_easy`` should call ``subprocess``
directly; ``tests/test_no_console_windows.py`` enforces that, because a single
forgotten call site is enough to bring the flashing back.
"""
from __future__ import annotations

import os
import subprocess

# CREATE_NO_WINDOW (winbase.h). Spelled out rather than taken from subprocess so
# this module imports cleanly on Linux, where the constant does not exist.
CREATE_NO_WINDOW = 0x08000000
_SW_HIDE = 0


def _startupinfo():
    """A STARTUPINFO that asks for a hidden window, or None off Windows."""
    make = getattr(subprocess, "STARTUPINFO", None)
    if make is None:
        return None
    info = make()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = _SW_HIDE
    return info


def hidden_kwargs(windows: "bool | None" = None) -> dict:
    """Keyword arguments that keep a child process off the screen.

    Empty everywhere but Windows. ``windows`` overrides the platform check so the
    test suite can verify the Windows behaviour while running on Linux CI.
    """
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        return {}
    kwargs = {"creationflags": CREATE_NO_WINDOW}
    info = _startupinfo()
    if info is not None:
        kwargs["startupinfo"] = info
    return kwargs


def _merge(kwargs: dict) -> dict:
    """Fold the hidden-window arguments into a caller's kwargs.

    ``creationflags`` is OR-ed rather than overwritten so a caller that needs its
    own flag keeps it *and* stays windowless.
    """
    merged = dict(hidden_kwargs())
    flags = merged.pop("creationflags", 0) | kwargs.get("creationflags", 0)
    merged.update(kwargs)
    if flags:
        merged["creationflags"] = flags
    return merged


def run(args, **kwargs):
    """``subprocess.run`` that never puts a console window on screen."""
    return subprocess.run(args, **_merge(kwargs))


def popen(args, **kwargs):
    """``subprocess.Popen`` that never puts a console window on screen."""
    return subprocess.Popen(args, **_merge(kwargs))


def check_output(args, **kwargs):
    """``subprocess.check_output`` that never puts a console window on screen."""
    return subprocess.check_output(args, **_merge(kwargs))

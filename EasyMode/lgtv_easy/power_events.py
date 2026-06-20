"""OS suspend / resume notifications, so the TV follows the PC like a monitor.

The daemon already detects *resume* on its own (it notices wall-clock time jumped
while it was frozen), but it cannot see *suspend* that way - by the time it would
run again the machine is already asleep. These hooks give it the prompt
"PC is going to sleep -> blank the TV now" half, like a monitor losing its signal.

Everything here is best-effort: ``start_power_listener`` returns a ``stop()``
callable when a hook was installed, or ``None`` when the platform/tools aren't
available. The daemon's gap detector is the safety net either way.

* Linux: systemd-logind broadcasts ``PrepareForSleep(b)`` on the system bus -
  ``true`` just before sleeping, ``false`` just after resuming. We follow it with
  ``gdbus monitor`` (already a dependency of the idle backend).
* Windows: ``PowerRegisterSuspendResumeNotification`` with a callback delivers
  ``PBT_APMSUSPEND`` / ``PBT_APMRESUME*`` without needing a window message loop.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from typing import Callable, Optional

StopFn = Callable[[], None]


def start_power_listener(on_suspend: Callable[[], None],
                         on_resume: Callable[[], None],
                         logger) -> Optional[StopFn]:
    """Install a suspend/resume listener. Return a stop() callable, or None."""
    try:
        if sys.platform.startswith("linux"):
            return _linux_logind_listener(on_suspend, on_resume, logger)
        if os.name == "nt":
            return _windows_power_listener(on_suspend, on_resume, logger)
    except Exception as exc:  # noqa: BLE001 - hooks are an optional nicety
        logger.debug("Power listener unavailable: %s", exc)
    return None


def _linux_logind_listener(on_suspend, on_resume, logger) -> Optional[StopFn]:
    gdbus = shutil.which("gdbus")
    if not gdbus:
        return None
    # Watch the login1 manager object; PrepareForSleep(true) fires before sleep,
    # PrepareForSleep(false) after resume.
    proc = subprocess.Popen(
        [gdbus, "monitor", "--system", "--dest", "org.freedesktop.login1",
         "--object-path", "/org/freedesktop/login1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    stop = threading.Event()

    def reader():
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                if stop.is_set():
                    break
                if "PrepareForSleep" not in line:
                    continue
                # The signal argument prints as 'true' (sleeping) or 'false'
                # (resuming) in the gdbus monitor output.
                low = line.lower()
                if "true" in low:
                    on_suspend()
                elif "false" in low:
                    on_resume()
        except Exception as exc:  # noqa: BLE001
            logger.debug("logind monitor reader stopped: %s", exc)

    threading.Thread(target=reader, daemon=True,
                     name="lgtv-easy-logind").start()

    def _stop():
        stop.set()
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass

    return _stop


def _windows_power_listener(on_suspend, on_resume, logger) -> Optional[StopFn]:
    import ctypes
    from ctypes import wintypes

    PBT_APMSUSPEND = 0x0004
    PBT_APMRESUMESUSPEND = 0x0007
    PBT_APMRESUMEAUTOMATIC = 0x0012
    DEVICE_NOTIFY_CALLBACK = 0x00000002

    # DEVICE_NOTIFY_CALLBACK_ROUTINE: ULONG (*)(PVOID Context, ULONG Type, PVOID Setting)
    CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p,
                                  wintypes.DWORD, ctypes.c_void_p)

    def _on_event(_context, dtype, _setting):
        try:
            if dtype == PBT_APMSUSPEND:
                on_suspend()
            elif dtype in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
                on_resume()
        except Exception as exc:  # noqa: BLE001 - never raise into the OS
            logger.debug("power callback error: %s", exc)
        return 0

    cb = CALLBACK(_on_event)

    class SUBSCRIBE_PARAMS(ctypes.Structure):
        _fields_ = [("Callback", CALLBACK), ("Context", ctypes.c_void_p)]

    params = SUBSCRIBE_PARAMS(cb, None)
    handle = ctypes.c_void_p()
    powrprof = ctypes.windll.powrprof  # type: ignore[attr-defined]
    rc = powrprof.PowerRegisterSuspendResumeNotification(
        DEVICE_NOTIFY_CALLBACK, ctypes.byref(params), ctypes.byref(handle))
    if rc != 0:  # ERROR_SUCCESS == 0
        return None

    def _stop():
        try:
            powrprof.PowerUnregisterSuspendResumeNotification(handle)
        except Exception:  # noqa: BLE001
            pass

    # Keep the ctypes callback alive for as long as the registration lives.
    _stop._cb = cb  # type: ignore[attr-defined]
    return _stop

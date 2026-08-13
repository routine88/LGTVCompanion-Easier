"""A tiny cross-platform single-instance guard for the idle daemon.

Now that the daemon can be launched several ways - by the auto-start entry at
login, by the supervising launcher, or by hand - we must ensure only one copy is
actually driving the TV at a time. A pidfile in the config directory does that:
each daemon records its PID and checks whether a live one already holds it.

Two acquisition modes:
* ``wait=False`` (default, e.g. a manual ``lgtv-easy run``): if another live
  daemon holds the lock, give up immediately so the command can exit politely.
* ``wait=True`` (used by the supervisor, via LGTV_EASY_WAIT_LOCK=1): block until
  the lock is free, so a supervised child quietly stands by instead of spinning.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from .config import config_dir


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    # A dead-but-unreaped process still answers signal 0, so os.kill alone
    # reports a zombie as a live lock holder. That matters twice over: a zombie
    # daemon would block a new one from ever acquiring the lock, and stop_holder
    # would refuse to clear the pidfile of something it had just killed. Linux
    # exposes the real state; elsewhere we fall through to the old assumption.
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as fh:
            # The comm field can contain spaces and parentheses, so the state
            # letter is the first field AFTER the final ')'.
            fields = fh.read().rpartition(")")[2].split()
        if fields and fields[0] == "Z":
            return False
    except OSError:
        pass
    return True


class SingleInstance:
    def __init__(self, name: str = "daemon"):
        self.path = os.path.join(config_dir(), f"{name}.pid")
        self.acquired = False

    def _holder(self) -> Optional[int]:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                pid = int(fh.read().strip())
        except (OSError, ValueError):
            return None
        return pid if _alive(pid) else None

    def holder(self) -> Optional[int]:
        """PID of the live process currently holding the lock, or None."""
        return self._holder()

    def signal(self, sig: int) -> bool:
        """Send ``sig`` to a *different* live process holding the lock (POSIX).

        Used to nudge a background daemon to re-read its config after the GUI or
        CLI changes a setting, so the change applies without a restart. Returns
        True only if another live holder was actually signalled - never signals
        ourselves, and is a harmless no-op when nobody holds the lock.
        """
        pid = self._holder()
        if not pid or pid == os.getpid():
            return False
        try:
            os.kill(pid, sig)
            return True
        except OSError:
            return False

    def stop_holder(self, timeout: float = 5.0) -> Optional[int]:
        """Stop whatever process holds this lock. Returns the pid, or None.

        Used by the GUI's "kill process" button, which has to stop both the
        watcher and the supervisor that would otherwise restart it five seconds
        later.

        **Never SIGTERM.** To the daemon, SIGTERM means "the machine is shutting
        down" and its handler powers the TV OFF - the exact opposite of what
        stopping the watcher should do. SIGUSR1 is its "stand down and leave the
        TV alone" signal. Windows has neither: there ``os.kill`` is
        TerminateProcess, which runs no handler at all, so the TV is untouched
        for the same reason.

        Escalates to SIGKILL if the process ignores the polite signal - also
        uncatchable, so also safe for the TV.
        """
        import signal as _signal
        pid = self._holder()
        if not pid or pid == os.getpid():
            return None
        polite = getattr(_signal, "SIGUSR1", None) or _signal.SIGTERM
        try:
            os.kill(pid, polite)
        except OSError:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not _alive(pid):
                break
            time.sleep(0.1)
        if _alive(pid):
            try:
                os.kill(pid, getattr(_signal, "SIGKILL", _signal.SIGTERM))
            except OSError:
                pass
        # A killed process never runs its own cleanup, so the pidfile it left
        # behind would masquerade as a live lock. Clear it once it's really gone.
        if not _alive(pid):
            try:
                os.remove(self.path)
            except OSError:
                pass
        return pid

    def _write(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))

    def acquire(self, wait: bool = False, poll: float = 5.0,
                sleep_fn=time.sleep) -> bool:
        """Take the lock. Returns True if held, False if busy and not waiting."""
        while True:
            holder = self._holder()
            if holder is None or holder == os.getpid():
                self._write()
                self.acquired = True
                return True
            if not wait:
                return False
            sleep_fn(poll)

    def release(self) -> None:
        if self.acquired:
            try:
                # Only remove if it's still ours.
                if self._holder() == os.getpid():
                    os.remove(self.path)
            except OSError:
                pass
            self.acquired = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()

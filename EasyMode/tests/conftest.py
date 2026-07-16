import os
import sys

import pytest

# Make the package importable when running tests from the repo without install.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Never let a daemon started during tests spawn the real OS suspend/resume
# monitors (gdbus + systemd-inhibit on Linux, the power-notify registration on
# Windows). The daemon checks this before starting its sleep watcher.
os.environ.setdefault("LGTV_EASY_NO_SLEEP_WATCH", "1")

# Likewise, keep the GUI's startup connection self-test from firing real network
# probes/discovery when a settings panel is built in a test; scenarios that want
# it exercise selfheal/the repair dialog explicitly.
os.environ.setdefault("LGTV_EASY_NO_SELFTEST", "1")


@pytest.fixture(autouse=True)
def no_real_autostart_hooks(monkeypatch, tmp_path_factory):
    """Never let a test install or remove the REAL machine's start-up hooks.

    LGTV_EASY_HOME/XDG_CONFIG_HOME redirect *files*, which is why this looked
    safe - but the Windows Task Scheduler and the Startup folder are global and
    neither is covered by those. The wizard tests answer "no" to start-at-login,
    which calls the production ``autostart.disable()``; on a real Windows box
    that shelled out to ``schtasks /Delete`` and silently uninstalled the user's
    own "power the TV off at shutdown" task. (The evidence was baffling: the task
    vanished while its shutdown-off.cmd survived - because the *wrapper* path IS
    redirected to tmp, so only the unlink was sandboxed, not the schtasks call.)

    Stub the subprocess helper so no test can reach schtasks, and point the
    Startup-folder entry at tmp. Tests that assert on schtasks arguments install
    their own ``_run`` stub, which overrides this one.
    """
    from lgtv_easy import autostart

    def _fake_run(args):
        # Mimic a clean machine: nothing is registered, create/delete succeed.
        if "/Query" in args:
            return (1, "ERROR: The system cannot find the file specified.")
        return (0, "")

    monkeypatch.setattr(autostart, "_run", _fake_run)
    startup = tmp_path_factory.mktemp("startup") / "LGTV-Easy-Mode.cmd"
    monkeypatch.setattr(autostart, "_startup_target", lambda: startup)

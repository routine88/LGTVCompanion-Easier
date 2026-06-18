"""End-to-end execution of the Linux launcher (not just static text checks).

`test_launchers.py` greps the scripts; this file actually *runs*
``LGTV-Easy-Mode-UBUNTU.sh`` against an in-process mock TV and proves the whole
background path works: detach -> supervise -> daemon connects -> screen blanks on
idle -> ``--stop`` cleans everything up.

It deliberately invokes the launcher by a *bare* name from the repo root, which
is the exact situation that used to break: ``setsid "$0" ...`` with a directory-
less ``$0`` made the OS search ``$PATH`` instead of the current directory, so the
background watcher silently never started. Running the real script here guards
that regression for good.

Skipped automatically on Windows or where ``bash`` isn't available.
"""
import json
import os
import shutil
import subprocess
import time

import pytest

from lgtv_easy.mock_tv import MockTV

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."))
LAUNCHER_NAME = "LGTV-Easy-Mode-UBUNTU.sh"
LAUNCHER = os.path.join(REPO_ROOT, LAUNCHER_NAME)

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None
    or not os.path.exists(LAUNCHER),
    reason="Linux launcher integration test needs bash and the .sh launcher")


def _fake_bin(tmp_path):
    """A PATH front that neutralises install_deps so the test never touches the
    real system: a no-op apt-get and a stub xprintidle. git/python3 stay real."""
    bind = tmp_path / "bin"
    bind.mkdir()
    for name in ("apt-get", "xprintidle"):
        p = bind / name
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    return bind


def _run(args, env, **kw):
    # cwd = repo root + a bare launcher name reproduces the directoryless-$0 bug.
    return subprocess.run(["bash", LAUNCHER_NAME, *args], cwd=REPO_ROOT,
                          env=env, timeout=90, capture_output=True, text=True, **kw)


def _wait(predicate, timeout=25.0, interval=0.4):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_background_launch_blanks_tv_then_stop_cleans_up(tmp_path):
    tv = MockTV(require_pairing=False, host="127.0.0.1").start()
    try:
        state = tmp_path / "state"
        state.mkdir()
        cfg = {
            "idle_minutes": 0.05, "idle_enabled": True, "poll_seconds": 1.0,
            "mute_on_sleep": False, "deep_off_enabled": False,
            "deep_off_minutes": 30.0, "tv_off_on_shutdown": False,
            "setup_complete": True,
            "device": {"name": "MockTV", "ip": f"127.0.0.1:{tv.port}",
                       "mac": "", "key": "MOCK-KEY-0001", "secure": False},
        }
        (state / "config.json").write_text(json.dumps(cfg))

        env = dict(os.environ)
        env["PATH"] = f"{_fake_bin(tmp_path)}{os.pathsep}{env.get('PATH', '')}"
        env["LGTV_EASY_HOME"] = str(state)
        env["LGTV_EASY_APP_HOME"] = REPO_ROOT
        env["LGTV_EASY_NO_UPDATE"] = "1"
        env["LGTV_EASY_FAKE_IDLE"] = "9999"  # always idle -> blank the screen

        proc = _run(["--background"], env)
        assert proc.returncode == 0, proc.stdout + proc.stderr

        # The detached supervisor should start the daemon, which connects to the
        # mock TV and blanks the screen because we're "idle".
        assert _wait(lambda: tv.screen_on is False), (
            "TV was never blanked; launcher.log:\n"
            + (state / "launcher.log").read_text())
        assert (state / "launcher.pid").exists()
        assert any("turnOffScreen" in u for u in tv.requests)

        stop = _run(["--stop"], env)
        assert stop.returncode == 0, stop.stdout + stop.stderr

        # Both the supervisor and its daemon child must be gone, locks released.
        assert _wait(lambda: not (state / "launcher.pid").exists())
        assert _wait(lambda: not (state / "daemon.pid").exists())
    finally:
        # Best-effort: make sure nothing we spawned outlives the test.
        try:
            _run(["--stop"], dict(os.environ, LGTV_EASY_HOME=str(tmp_path / "state")))
        except Exception:
            pass
        tv.stop()

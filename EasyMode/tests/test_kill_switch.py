"""The settings window's "Kill process" button.

The hard part is not stopping things - it is keeping them stopped. Three
separate mechanisms would otherwise restart the watcher within seconds:
applying any setting, closing the window, and the launcher's supervisor. A stop
that something quietly undoes is worse than no stop button at all.
"""
import os
import tempfile

import pytest

from lgtv_easy.config import Config, Device
from lgtv_easy.singleton import SingleInstance

tk = pytest.importorskip("tkinter")


@pytest.fixture
def panel(monkeypatch):
    """A built SettingsPanel over a throwaway config, or skip if no display."""
    home = tempfile.mkdtemp(prefix="lgtv-kill-")
    monkeypatch.setenv("LGTV_EASY_HOME", home)
    monkeypatch.setenv("LGTV_EASY_NO_SELFTEST", "1")
    monkeypatch.setenv("LGTV_EASY_NO_SLEEP_WATCH", "1")

    cfg = Config(setup_complete=True)
    cfg.device = Device(name="mock", ip="127.0.0.1", key="k")
    cfg.save()

    from lgtv_easy import gui
    try:
        app = gui.App()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    app.update_idletasks()
    app.update()
    built = app.container.winfo_children()[0]
    if not isinstance(built, gui.SettingsPanel):
        app.destroy()
        pytest.skip("settings panel not shown for this config")
    yield built
    try:
        app.destroy()
    except tk.TclError:
        pass


def test_the_button_stops_the_in_window_watcher(panel):
    panel.app.start_daemon()
    assert panel.app.daemon is not None, "expected this window to own the watcher"

    panel._kill_service()

    assert panel.app.daemon is None
    assert panel.app.service_stopped is True


def test_applying_a_setting_does_not_bring_it_back(panel):
    """The reported requirement: it must not respawn even with the window open.
    Every switch on the panel calls start_daemon() as part of applying."""
    panel._kill_service()

    panel.mute.set(True)
    panel._apply()

    assert panel.app.daemon is None, "applying a setting restarted the watcher"
    assert panel.app.service_stopped is True


def test_start_daemon_is_latched_off_outright(panel):
    panel._kill_service()
    panel.app.start_daemon()
    assert panel.app.daemon is None


def test_the_message_says_how_to_get_it_back(panel):
    panel._kill_service()
    text = str(panel.status.cget("text"))
    assert "Restart the app to resume service." in text
    # ...and it survives anything else that redraws the status line.
    panel._refresh_status()
    assert "Restart the app to resume service." in str(panel.status.cget("text"))


def test_the_button_disables_itself(panel):
    panel._kill_service()
    assert "disabled" in panel._kill_btn.state()


def test_closing_the_window_reports_a_deliberate_stop(panel):
    """The launcher starts its supervisor once the GUI returns, so the exit code
    is the only thing standing between "kill process" and it all coming straight
    back when the window is closed."""
    from lgtv_easy import gui
    panel._kill_service()
    assert panel.app.service_stopped is True
    # main() maps that latch onto the exit code the launchers watch for.
    assert gui.EXIT_SERVICE_STOPPED == 10


def test_a_normal_session_exits_zero(panel):
    assert panel.app.service_stopped is False


# ----- the process-stopping primitive ----------------------------------
def test_stop_holder_stops_another_process_and_clears_the_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    import subprocess
    import sys
    # A stand-in watcher that ignores nothing and simply waits.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    lock = SingleInstance("daemon")
    with open(lock.path, "w", encoding="utf-8") as fh:
        fh.write(str(child.pid))

    assert lock.holder() == child.pid
    stopped = lock.stop_holder(timeout=3.0)

    assert stopped == child.pid
    child.wait(timeout=5)
    assert lock.holder() is None
    assert not os.path.exists(lock.path), (
        "a killed process never cleans up, so the lock file must be cleared here "
        "or it masquerades as a live watcher forever")


def test_stop_holder_never_uses_sigterm():
    """SIGTERM is the daemon's "machine is shutting down" signal and powers the
    TV OFF. Stopping the watcher must never look like a shutdown."""
    import inspect
    src = inspect.getsource(SingleInstance.stop_holder)
    body = src.split('"""')[2]  # past the docstring
    assert "SIGUSR1" in body
    assert "SIGKILL" in body, "should escalate to an uncatchable signal, not TERM"
    # SIGTERM appears only as the Windows fallback, where os.kill is
    # TerminateProcess and runs no handler at all.
    assert body.count("SIGTERM") == 2, (
        "SIGTERM should appear only as the two Windows fallbacks")


def test_stop_holder_is_a_no_op_with_nobody_holding(tmp_path, monkeypatch):
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    assert SingleInstance("daemon").stop_holder() is None


def test_stop_holder_refuses_to_stop_us(tmp_path, monkeypatch):
    """Never shoot ourselves: the GUI holds this same lock when it owns the
    in-process watcher, and it stops that one directly instead."""
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    lock = SingleInstance("daemon")
    lock.acquire()
    assert lock.stop_holder() is None

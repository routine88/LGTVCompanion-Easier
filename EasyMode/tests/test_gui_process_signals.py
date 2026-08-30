"""SIGUSR1/SIGTERM aimed at the GUI process must not leak the daemon's locks.

Before this fix, gui.py registered no handler for either signal, so an
installer, updater, or supervisor sending SIGUSR1 to restart the window hit
Python's default action - immediate termination - which skipped Daemon.stop()
entirely and leaked the systemd-inhibit sleep/shutdown locks system_sleep.py
holds. Those locks then sat there blocking the machine's sleep and shutdown
until something killed them by hand. cli.cmd_run's headless daemon already
had this handling (_install_shutdown_hooks); this is the GUI-side mirror of
it, tested by invoking the registered handler directly - not via a real
os.kill - since it deliberately raises SystemExit.
"""
import signal
import tempfile

import pytest

from lgtv_easy.config import Config, Device
from lgtv_easy.singleton import SingleInstance

tk = pytest.importorskip("tkinter")


@pytest.fixture
def app(monkeypatch):
    home = tempfile.mkdtemp(prefix="lgtv-signals-")
    monkeypatch.setenv("LGTV_EASY_HOME", home)
    monkeypatch.setenv("LGTV_EASY_NO_SELFTEST", "1")
    monkeypatch.setenv("LGTV_EASY_NO_SLEEP_WATCH", "1")

    cfg = Config(setup_complete=True)
    cfg.device = Device(name="mock", ip="127.0.0.1", key="k")
    cfg.save()

    from lgtv_easy import gui
    try:
        instance = gui.App()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    instance.update_idletasks()
    instance.update()
    yield instance
    try:
        instance.destroy()
    except tk.TclError:
        pass


def _handler_for(sig):
    handler = signal.getsignal(sig)
    assert callable(handler), f"App() registered nothing for {sig!r}"
    return handler


def test_sigusr1_stops_the_daemon_and_frees_the_lock_before_exiting(app):
    app.start_daemon()
    assert app.daemon is not None
    assert app._lock is not None

    with pytest.raises(SystemExit) as exc:
        _handler_for(signal.SIGUSR1)(signal.SIGUSR1, None)

    assert exc.value.code == 0
    assert app.daemon is None
    assert app._lock is None
    # Not just forgotten about - actually free for the next owner to take.
    fresh = SingleInstance("daemon")
    assert fresh.acquire(wait=False) is True
    fresh.release()


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="POSIX only")
def test_sigterm_cleans_up_without_reaching_the_network_when_disabled(app, monkeypatch):
    app.cfg.tv_off_on_shutdown = False
    app.start_daemon()
    assert app.daemon is not None

    def _fail_if_called(*a, **k):
        pytest.fail("power-off must not run when tv_off_on_shutdown is False")
    monkeypatch.setattr("lgtv_easy.cli._tv_power_off", _fail_if_called)

    with pytest.raises(SystemExit) as exc:
        _handler_for(signal.SIGTERM)(signal.SIGTERM, None)

    assert exc.value.code == 0
    assert app.daemon is None
    assert app._lock is None


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="POSIX only")
def test_sigterm_skips_power_off_already_handled_by_the_dbus_hook(app, monkeypatch):
    """system_sleep.py's PrepareForShutdown hook runs first and sets
    daemon._shutdown_handled - the SIGTERM path is only a fallback for when
    that hook never fired, and must not power off an already-off TV."""
    app.cfg.tv_off_on_shutdown = True
    app.start_daemon()
    app.daemon._shutdown_handled = True

    def _fail_if_called(*a, **k):
        pytest.fail("power-off must not run once _shutdown_handled is set")
    monkeypatch.setattr("lgtv_easy.cli._tv_power_off", _fail_if_called)

    with pytest.raises(SystemExit) as exc:
        _handler_for(signal.SIGTERM)(signal.SIGTERM, None)

    assert exc.value.code == 0
    assert app.daemon is None
    assert app._lock is None

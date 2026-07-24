"""The 'there is no TV set up' warning.

The state these cover is the quiet one: Easy Mode installed, auto-starting,
apparently healthy - and controlling nothing, because no TV was ever saved (or
the config was wiped and silently reloaded as empty). Every surface has to say
so out loud rather than carry on as if all is well.
"""
import logging

import pytest

from lgtv_easy import cli
from lgtv_easy.config import Config, Device, log_path


def _configured() -> Config:
    cfg = Config(setup_complete=True)
    cfg.device = Device(name="Office C2", ip="192.168.1.5", key="abc123")
    return cfg


# ----- Config.unconfigured_reason ------------------------------------------
def test_fresh_config_is_unconfigured():
    cfg = Config()
    assert cfg.tv_configured is False
    assert cfg.unconfigured_reason() == "No TV has been set up yet."


def test_fully_paired_config_is_configured():
    cfg = _configured()
    assert cfg.tv_configured is True
    assert cfg.unconfigured_reason() is None


def test_address_without_pairing_key_is_called_out():
    cfg = Config(setup_complete=True)
    cfg.device = Device(ip="192.168.1.5")
    reason = cfg.unconfigured_reason()
    assert reason is not None and "never paired" in reason
    assert "192.168.1.5" in reason


def test_pairing_key_without_address_is_called_out():
    cfg = Config(setup_complete=True)
    cfg.device = Device(key="abc123")
    reason = cfg.unconfigured_reason()
    assert reason is not None and "no address" in reason


def test_unfinished_setup_is_called_out():
    """Paired and addressable, but the wizard never completed."""
    cfg = _configured()
    cfg.setup_complete = False
    reason = cfg.unconfigured_reason()
    assert reason is not None and "never finished" in reason


# ----- the shared banner ----------------------------------------------------
def test_banner_shouts_when_no_tv(capsys, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")   # assert on text, not escape codes
    cli.warn_no_tv(Config(), action="The idle watcher will NOT start.")
    out = capsys.readouterr().out
    assert "NO TV IS SET UP" in out
    assert "No TV has been set up yet." in out
    assert "The idle watcher will NOT start." in out
    assert "lgtv-easy gui" in out          # and how to fix it


def test_banner_is_silent_when_a_tv_is_configured(capsys):
    cli.warn_no_tv(_configured())
    assert capsys.readouterr().out == ""


def test_banner_is_plain_ascii(capsys, monkeypatch):
    """It has to survive a legacy cp437/cp1252 console - that is where a
    confused user reads it."""
    monkeypatch.setenv("NO_COLOR", "1")
    cli.warn_no_tv(Config())
    capsys.readouterr().out.encode("ascii")  # raises if we smuggled in unicode


def test_no_colour_when_output_is_redirected(monkeypatch):
    """Escape codes must not land in a piped log file."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert cli._red("x") == "x"             # pytest's stdout is not a tty


def test_no_color_env_is_honoured(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert cli._colour_ok() is False


# ----- the login watcher ----------------------------------------------------
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("LGTV_EASY_NO_ALERT", "1")   # never open a real window
    # applog caches its logger process-wide; start clean so the assertions below
    # read this test's log file and not one left by an earlier test.
    from lgtv_easy import applog
    logger = logging.getLogger("lgtv_easy")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    monkeypatch.setattr(applog, "_LOGGER", None)


def test_run_refuses_and_shouts_when_no_tv(capsys, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    Config().save()

    assert cli.cmd_run(None) == 1
    assert "NO TV IS SET UP" in capsys.readouterr().out


def test_run_records_the_reason_in_the_log_file(capsys, tmp_path, monkeypatch):
    """The console it printed to at login does not exist - the log is the only
    lasting evidence, and used to hold nothing at all."""
    _isolate(monkeypatch, tmp_path)
    Config().save()

    cli.cmd_run(None)
    capsys.readouterr()
    with open(log_path(), encoding="utf-8") as fh:
        logged = fh.read()
    assert "Watcher not started" in logged
    assert "No TV has been set up yet." in logged


def test_run_alerts_on_screen_when_there_is_no_console(tmp_path, monkeypatch):
    """pythonw at login leaves sys.stdout as None: printing is useless there, so
    the warning has to become a window instead."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("LGTV_EASY_NO_ALERT")
    Config().save()
    shown = []
    monkeypatch.setattr(cli, "_alert_no_tv", shown.append)
    # print() is a no-op while sys.stdout is None, so capsys sees nothing here -
    # which is exactly the login behaviour being reproduced.
    monkeypatch.setattr(cli.sys, "stdout", None)
    try:
        assert cli.cmd_run(None) == 1
    finally:
        monkeypatch.undo()
    assert shown == ["No TV has been set up yet."]


def test_run_does_not_alert_when_a_console_is_present(tmp_path, monkeypatch,
                                                      capsys):
    _isolate(monkeypatch, tmp_path)
    Config().save()
    shown = []
    monkeypatch.setattr(cli, "_alert_no_tv", shown.append)

    cli.cmd_run(None)
    capsys.readouterr()
    assert shown == []          # it printed; a window would just be noise


def test_alert_never_raises_without_a_display(monkeypatch):
    """A missing warning must not take the watcher down with it."""
    monkeypatch.delenv("LGTV_EASY_NO_ALERT", raising=False)
    pytest.importorskip("tkinter")
    import lgtv_easy.gui as gui_mod
    monkeypatch.setattr(gui_mod, "show_no_tv_alert",
                        lambda _r: (_ for _ in ()).throw(RuntimeError("no display")))
    cli._alert_no_tv("No TV has been set up yet.")   # must not raise


# ----- status ---------------------------------------------------------------
def test_status_leads_with_the_warning(capsys, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    Config().save()

    assert cli.cmd_status(None) == 0
    lines = capsys.readouterr().out.splitlines()
    banner = next(i for i, l in enumerate(lines) if "NO TV IS SET UP" in l)
    details = next(i for i, l in enumerate(lines) if "Config file" in l)
    assert banner < details, "the warning must come before the detail list"


def test_status_is_quiet_when_a_tv_is_configured(capsys, tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    _configured().save()

    cli.cmd_status(None)
    assert "NO TV IS SET UP" not in capsys.readouterr().out


@pytest.mark.parametrize("command", ["cmd_test", "cmd_repair"])
def test_tv_commands_shout_instead_of_muttering(command, capsys, tmp_path,
                                                monkeypatch):
    _isolate(monkeypatch, tmp_path)
    Config().save()

    assert getattr(cli, command)(None) == 1
    assert "NO TV IS SET UP" in capsys.readouterr().out

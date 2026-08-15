"""Auto-start at login: enable creates an entry, disable removes it."""
import os

import pytest

from lgtv_easy import autostart

# The two Linux tests below force os.name="posix". Because that attribute lives
# on the shared os module, the change is process-global and makes pathlib build
# PosixPath - which cannot instantiate on a real Windows runner. So skip the
# Linux-only autostart tests there (Windows paths are covered separately below).
linux_only = pytest.mark.skipif(
    os.name == "nt", reason="Linux autostart path; PosixPath can't run on Windows")


@linux_only
def test_enable_disable_roundtrip_linux(tmp_path, monkeypatch):
    # Force the Linux autostart location into a temp dir so the test is hermetic.
    monkeypatch.setattr(autostart.os, "name", "posix")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert autostart.is_enabled() is False
    autostart.enable()
    assert autostart.is_enabled() is True
    body = autostart._linux_target().read_text(encoding="utf-8")
    assert "Desktop Entry" in body
    assert "lgtv_easy run" in body

    assert autostart.disable() is True
    assert autostart.is_enabled() is False
    # Disabling again is a harmless no-op.
    assert autostart.disable() is False


@linux_only
def test_set_enabled_reports_status(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart.os, "name", "posix")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    msg_on = autostart.set_enabled(True)
    assert "ENABLED" in msg_on
    assert autostart.is_enabled() is True
    msg_off = autostart.set_enabled(False)
    assert "DISABLED" in msg_off
    assert autostart.is_enabled() is False


def test_task_method_creates_logon_task(tmp_path, monkeypatch):
    # Exercise the Scheduled Task path directly (faking os.name='nt' would make
    # pathlib build WindowsPath, which can't instantiate on Linux). Stub schtasks.
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    calls = []

    def fake_run(args):
        calls.append(args)
        return (0, "")

    monkeypatch.setattr(autostart, "_run", fake_run)

    label = autostart._enable_task()
    assert "Scheduled Task" in label

    # schtasks was asked to create a logon-triggered task...
    create = [c for c in calls if "/Create" in c]
    assert create, "schtasks /Create should have been called"
    assert "ONLOGON" in create[0]
    assert autostart.TASK_NAME in create[0]

    # ...that runs the app itself. It used to run `cmd /c <wrapper.cmd>`, which
    # threw a console window on screen at every login.
    run = create[0][create[0].index("/TR") + 1]
    assert "lgtv_easy run" in run or run.endswith(" run")
    assert "cmd" not in run.split()[0].lower()


def test_task_create_args_quotes_the_path():
    from pathlib import PurePath
    args = autostart._task_create_args(PurePath("/tmp/a b/run.cmd"))
    tr = args[args.index("/TR") + 1]
    assert tr.startswith('"') and tr.endswith('"')  # quoted for spaces


def test_windows_run_cmd_content_uses_module_run():
    body = autostart._windows_run_cmd_content()
    assert "-m lgtv_easy run" in body
    assert body.lower().startswith("@echo off")


# ----- the tests must not reconfigure the machine running them ----------
# Everything else Easy Mode writes lives under LGTV_EASY_HOME. Auto-start
# cannot: the Startup folder comes from %APPDATA%, a Scheduled Task has no path
# at all, and the Linux entry follows XDG_CONFIG_HOME. Several wizard tests
# answer "no" to "start at login", and until the sandbox existed that switched
# off the login entry belonging to whoever ran the suite - on this machine it
# really did, mid-session.
def test_the_sandbox_keeps_the_startup_entry_out_of_the_real_profile(tmp_path,
                                                                     monkeypatch):
    monkeypatch.delenv("LGTV_EASY_AUTOSTART_SANDBOX", raising=False)
    real = autostart._startup_target()          # where a real install writes
    monkeypatch.setenv("LGTV_EASY_AUTOSTART_SANDBOX", str(tmp_path))
    sandboxed = autostart._startup_target()

    assert sandboxed != real
    assert str(sandboxed).startswith(str(tmp_path))


def test_the_sandbox_never_calls_schtasks(tmp_path, monkeypatch):
    """A Scheduled Task is machine-wide - there is no directory to redirect -
    so inside the sandbox schtasks must simply not be invoked."""
    monkeypatch.setenv("LGTV_EASY_AUTOSTART_SANDBOX", str(tmp_path))
    code, message = autostart._run(["schtasks", "/Delete", "/TN", "anything"])
    assert code != 0 and "not run" in message
    assert autostart._task_exists() is False


def test_the_suite_is_running_inside_the_sandbox():
    """conftest sets it for the whole run; without that the guard above is
    just decoration."""
    import os as _os
    assert _os.environ.get("LGTV_EASY_AUTOSTART_SANDBOX"), \
        "tests/conftest.py should point auto-start at a throwaway directory"


# ----- the installed (frozen) build ------------------------------------
# An installed copy is an .exe, not a script. Every entry written here has to
# say so, or logging in runs `"...\LGTV Companion Easy Mode.exe" -m lgtv_easy
# run` - which the app reads as junk arguments and refuses to start, silently,
# at every login.
def _pretend_frozen(monkeypatch, tmp_path):
    import sys
    from lgtv_easy import branding
    exe = tmp_path / branding.GUI_EXE
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return exe


def test_frozen_login_entry_runs_the_exe_not_the_module(tmp_path, monkeypatch):
    exe = _pretend_frozen(monkeypatch, tmp_path)
    body = autostart._windows_run_cmd_content()
    assert f'"{exe}" run' in body
    assert "-m lgtv_easy" not in body
    # ...and it starts in the folder the app was installed into.
    assert f'cd /d "{tmp_path}"' in body


def test_frozen_shutdown_hook_runs_the_exe(tmp_path, monkeypatch):
    exe = _pretend_frozen(monkeypatch, tmp_path)
    xml = autostart._shutdown_task_xml()
    assert f"<Command>{exe}</Command>" in xml
    assert "<Arguments>off --only-if-configured</Arguments>" in xml
    assert "-m lgtv_easy" not in xml


def test_the_shutdown_task_does_not_go_through_cmd():
    """It fired `cmd /c <wrapper.cmd>`, so every shutdown flashed up a console
    window on its way out."""
    xml = autostart._shutdown_task_xml()
    assert "<Command>cmd</Command>" not in xml
    assert "/c " not in xml


def test_the_login_entry_is_a_shortcut_not_a_batch_file(tmp_path, monkeypatch):
    """cmd.exe in the Startup folder means a black window at every login, for a
    program whose whole job is to be invisible."""
    monkeypatch.setenv("LGTV_EASY_AUTOSTART_SANDBOX", str(tmp_path))
    assert autostart._startup_link().suffix == ".lnk"
    assert autostart._startup_link().parent == autostart._startup_target().parent


@pytest.mark.skipif(os.name != "nt", reason="shortcuts are a Windows thing")
def test_enable_writes_a_working_shortcut_and_drops_the_old_cmd(tmp_path,
                                                                monkeypatch):
    monkeypatch.setenv("LGTV_EASY_AUTOSTART_SANDBOX", str(tmp_path))
    legacy = autostart._startup_target()        # an install from the .cmd era
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("@echo off\r\n", encoding="utf-8")

    autostart.enable()

    link = autostart._startup_link()
    assert link.exists() and link.stat().st_size > 0, "no login shortcut written"
    assert not legacy.exists(), "the old .cmd would start a second watcher"
    assert autostart.is_enabled() is True
    assert autostart.disable() is True
    assert not link.exists()


@linux_only
def test_frozen_linux_autostart_entry_runs_the_exe(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart.os, "name", "posix")
    _pretend_frozen(monkeypatch, tmp_path)
    body = autostart._linux_desktop_content()
    assert "-m lgtv_easy" not in body
    assert "Exec=" in body and "run'" in body


@linux_only
def test_linux_autostart_entry_carries_the_icon(tmp_path, monkeypatch):
    """It shows up in "Startup Applications"; without this it is a blank square."""
    monkeypatch.setattr(autostart.os, "name", "posix")
    body = autostart._linux_desktop_content()
    assert "Icon=" in body and "icon.png" in body

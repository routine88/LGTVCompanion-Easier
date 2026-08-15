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

    # The wrapper .cmd the task points at must exist and run the daemon.
    wrapper = autostart._task_wrapper_path()
    assert wrapper.exists()
    assert "lgtv_easy run" in wrapper.read_text(encoding="utf-8")

    # schtasks was asked to create a logon-triggered task.
    create = [c for c in calls if "/Create" in c]
    assert create, "schtasks /Create should have been called"
    assert "ONLOGON" in create[0]
    assert autostart.TASK_NAME in create[0]


def test_task_create_args_quotes_the_path():
    from pathlib import PurePath
    args = autostart._task_create_args(PurePath("/tmp/a b/run.cmd"))
    tr = args[args.index("/TR") + 1]
    assert tr.startswith('"') and tr.endswith('"')  # quoted for spaces


def test_windows_run_cmd_content_uses_module_run():
    body = autostart._windows_run_cmd_content()
    assert "-m lgtv_easy run" in body
    assert body.lower().startswith("@echo off")


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
    body = autostart._shutdown_wrapper_content()
    assert f'"{exe}" off --only-if-configured' in body
    assert "-m lgtv_easy" not in body


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

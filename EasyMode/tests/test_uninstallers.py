"""Guard-rails for the two uninstallers at the repository root.

Uninstalling is the one operation nobody runs twice: if it misses the login
entry or a Scheduled Task, the user is left with an app that keeps starting
itself from files that no longer exist - and no obvious way to stop it. That is
exactly the state this pair of scripts was written to clean up, so the names and
paths they hunt for must stay in step with the ones the app actually writes.

Plain-text checks, no shell or cmd.exe needed: every artefact the app can leave
behind is asserted to appear in the matching script, the settings must survive
unless the user asks for them to go, and the .bat is checked for the cmd.exe
parsing traps that would abort it halfway through.
"""
import os
import re
import shutil
import subprocess

import pytest

from lgtv_easy import autostart
from lgtv_easy.config import config_dir

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."))
WIN_BAT = os.path.join(REPO_ROOT, "Windows Uninstall.bat")
LINUX_SH = os.path.join(REPO_ROOT, "Linux Uninstall.sh")


def _read(path):
    if not os.path.exists(path):
        pytest.skip(f"{path} not present (running outside a full checkout)")
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_both_uninstallers_exist():
    assert os.path.exists(WIN_BAT) and os.path.exists(LINUX_SH)


# ----- Windows ----------------------------------------------------------------
def test_windows_removes_every_autostart_form():
    bat = _read(WIN_BAT)
    # The login entry has taken three forms across versions, and an upgraded
    # install can carry more than one. Missing any of them leaves the watcher
    # starting itself forever.
    assert f"{autostart.FRIENDLY}.lnk" in bat or "%APP_NAME%.lnk" in bat
    assert "LGTV-Easy-Mode.cmd" in bat
    assert f'set "TASK={autostart.TASK_NAME}"' in bat
    assert f'set "SHUTDOWN_TASK={autostart.SHUTDOWN_TASK_NAME}"' in bat
    assert "schtasks /Delete" in bat


def test_windows_removes_both_kinds_of_install():
    bat = _read(WIN_BAT)
    # The installer's copy, wherever the user put it...
    assert "InstallLocation" in bat, "must honour the recorded install location"
    assert "%LOCALAPPDATA%\\Programs\\%APP_NAME%" in bat
    # ...and the self-updating clone the portable launcher downloads.
    assert "%LOCALAPPDATA%\\lgtv-companion-easy" in bat


def test_windows_keeps_settings_unless_asked():
    bat = _read(WIN_BAT)
    assert "--purge" in bat
    # The settings directory may only be removed under the purge branch.
    purge_block = bat.split(":purge_settings", 1)[1]
    assert 'call :rm_dir "%STATE_DIR%"' in purge_block
    assert bat.count('call :rm_dir "%STATE_DIR%"') == 1, \
        "the settings must not be deleted outside the --purge branch"


def test_windows_never_uses_a_parenthesised_if_block():
    # cmd.exe parses a whole "if ... ( ... )" block the moment it reaches the
    # "if", true or not, so one stray parenthesis inside aborts the script - in
    # the middle of an uninstall, which is the worst possible moment. The
    # launcher next door documents the rule; this enforces it.
    for lineno, line in enumerate(_read(WIN_BAT).splitlines(), 1):
        stripped = line.strip()
        if stripped.upper().startswith("REM ") or stripped.startswith("::"):
            continue
        if not re.match(r"(?i)^if\b", stripped):
            continue
        # An unescaped "(" anywhere on an "if" line opens a block, whether it
        # ends the line or wraps a one-liner.
        if re.search(r"(?<!\^)\(", stripped):
            pytest.fail(f"{WIN_BAT}:{lineno} opens a parenthesised if block: "
                        f"{stripped}")


def test_windows_jumps_only_to_labels_that_exist():
    bat = _read(WIN_BAT)
    labels = {m.group(1).lower()
              for m in re.finditer(r"(?m)^\s*:([A-Za-z_][\w]*)", bat)}
    labels.add("eof")                      # cmd.exe's built-in return target
    targets = {m.group(1).lower() for m in
               re.finditer(r"(?im)\b(?:goto|call)\s+:([A-Za-z_][\w]*)", bat)}
    missing = sorted(targets - labels)
    assert not missing, f"jumps to labels that do not exist: {missing}"


def test_windows_proves_a_pid_is_ours_before_killing_it():
    bat = _read(WIN_BAT)
    # Windows recycles pids freely. A stale pidfile whose number now belongs to
    # some innocent program must never get that program killed.
    # The subroutine body: from its label to the next one (the call sites
    # earlier in the file mention the name too, hence the anchored split).
    stop = re.split(r"(?m)^:stop_pidfile\s*$", bat)[-1]
    stop = re.split(r"(?m)^:\w+", stop)[0]
    assert "tasklist" in stop and "findstr" in stop, \
        "the pidfile path must check the process name before taskkill"


# ----- Linux ------------------------------------------------------------------
def test_linux_is_valid_shell():
    sh = shutil.which("sh")
    if not sh:
        pytest.skip("no sh available")
    _read(LINUX_SH)     # skips cleanly outside a checkout
    assert subprocess.run([sh, "-n", LINUX_SH]).returncode == 0


def test_linux_removes_every_installed_artefact():
    body = _read(LINUX_SH)
    assert "$AUTOSTART_FILE" in body                      # the login entry
    assert "$APPS_DIR/$APP_ID.desktop" in body            # the menu entry
    assert "$(desktop_dir)/$APP_ID.desktop" in body       # the desktop shortcut
    assert "$LAUNCHER" in body                            # bin/lgtv-easy
    assert "$LIB_DIR" in body                             # the installed app
    assert "$PORTABLE_DIR" in body                        # the launcher's clone
    assert "icon" in body.lower()


def test_linux_paths_match_the_app():
    body = _read(LINUX_SH)
    assert f'APP_ID="{autostart.APP_ID}"' in body
    # config_dir() is the app's own answer for where the settings live; the
    # uninstaller has to look in the same place.
    assert os.path.basename(config_dir()) == autostart.APP_ID or \
        os.environ.get("LGTV_EASY_HOME"), "config dir moved - update the script"
    assert "LGTV_EASY_HOME" in body


def test_linux_keeps_settings_unless_asked():
    body = _read(LINUX_SH)
    assert "--purge" in body
    assert body.count('rm_path "$STATE_DIR"') == 1
    purge_branch = body.split('if [ "$PURGE" = "yes" ]', 1)[1]
    assert 'rm_path "$STATE_DIR"' in purge_branch.split("elif", 1)[0]


def test_linux_never_sigterms_the_watcher():
    body = _read(LINUX_SH)
    # SIGTERM is the shutdown signal: the daemon reads it as "the PC is going
    # down" and powers the TV OFF. Uninstalling must leave the TV exactly as it
    # is - it may well be the screen the user is reading this on. SIGUSR1 means
    # "just stop".
    assert "-USR1" in body
    assert "-TERM" not in body and "kill -15" not in body


def test_both_scripts_survive_deleting_their_own_directory():
    # The portable clone contains a copy of these scripts. Both shells read a
    # script as they go, so removing that directory mid-run would stop the
    # uninstall halfway - login entry gone, app still there, or the reverse.
    assert "TEMP_COPY" in _read(WIN_BAT)
    assert "LGTV_UNINSTALL_RELAUNCHED" in _read(LINUX_SH)

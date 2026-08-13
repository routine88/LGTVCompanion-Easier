"""Guard-rails for the two portable launchers at the repository root.

These are plain-text checks (no shell/PowerShell execution needed) that catch the
classes of mistake that would silently break the one-double-click experience:

* the launcher pointing at the wrong app subdirectory,
* the launcher opening the old text wizard instead of the graphical front door,
* the Windows .bat losing its link to the .ps1 that does the real work.

If the app folder is ever renamed, or a launcher reverts to ``wizard``, one of
these fails loudly instead of shipping a broken installer.
"""
import os

import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."))
APP_DIR_NAME = "EasyMode"

WIN_BAT = os.path.join(REPO_ROOT, "Windows Launch.bat")
# The PowerShell engine lives inside the app folder; the .bat at the root is the
# only Windows file a user touches.
WIN_PS1 = os.path.join(REPO_ROOT, APP_DIR_NAME, "LGTV-Easy-Mode-WINDOWS.ps1")
LINUX_SH = os.path.join(REPO_ROOT, "Linux Launch.sh")


def _read(path):
    if not os.path.exists(path):
        pytest.skip(f"{path} not present (running outside a full checkout)")
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def test_app_subdir_matches_real_folder():
    assert os.path.isdir(os.path.join(REPO_ROOT, APP_DIR_NAME))


def test_windows_launcher_points_at_real_app_dir():
    ps1 = _read(WIN_PS1)
    assert f'$SubDir = "{APP_DIR_NAME}"' in ps1


def test_linux_launcher_points_at_real_app_dir():
    sh = _read(LINUX_SH)
    assert f'SUBDIR="{APP_DIR_NAME}"' in sh


def test_launchers_open_the_graphical_front_door():
    # The whole point of this update: the launchers open the GUI ("gui"),
    # not the old text-only wizard, as the everyday front door.
    ps1 = _read(WIN_PS1)
    sh = _read(LINUX_SH)
    assert 'Run-Cli @("gui")' in ps1, "Windows launcher should open the GUI"
    assert "run_cli gui" in sh, "Linux launcher should open the GUI"


def test_bat_invokes_the_ps1():
    bat = _read(WIN_BAT)
    assert "LGTV-Easy-Mode-WINDOWS.ps1" in bat
    assert "powershell" in bat.lower()


def test_bat_points_into_the_app_folder():
    # The .ps1 engine moved into the app folder, so the root .bat must reference
    # it via that subdirectory (both the cloned copy and the local fallback).
    bat = _read(WIN_BAT)
    assert f"{APP_DIR_NAME}\\LGTV-Easy-Mode-WINDOWS.ps1" in bat


def test_ps1_lives_in_the_app_folder():
    assert os.path.exists(WIN_PS1), "the PowerShell engine should live in EasyMode/"
    assert not os.path.exists(
        os.path.join(REPO_ROOT, "LGTV-Easy-Mode-WINDOWS.ps1")
    ), "the .ps1 should no longer sit at the repo root"


def test_launchers_self_update_from_a_repo():
    ps1 = _read(WIN_PS1)
    sh = _read(LINUX_SH)
    assert "LGTV_EASY_REPO" in ps1 and "git clone" in ps1
    assert "LGTV_EASY_REPO" in sh and "git clone" in sh


def test_windows_detached_launch_quotes_the_script_path():
    """A detached watcher must survive a space in its own path.

    ``Start-Process`` joins ``-ArgumentList`` with spaces and does NOT quote the
    entries. An unquoted script path under, say, ``C:\\Users\\First Last\\`` then
    reaches powershell.exe cut in half - it reports ``-File 'C:\\Users\\First'``
    and exits at once. The watcher is started hidden, so that failure is silent:
    the launcher happily reports "running in the background" while nothing runs,
    which is exactly the bug this guards. (The bash launcher quotes correctly, so
    Linux never saw it.)

    Both halves are checked: every detached PowerShell must go through the single
    helper, and that helper must quote the path.
    """
    import re
    ps1 = _read(WIN_PS1)
    spawns = re.findall(r'Start-Process\s+-FilePath\s+"powershell\.exe"', ps1)
    assert len(spawns) == 1, (
        "detached PowerShell should be spawned from exactly one helper "
        "(Start-Detached), so the path is quoted in a single place; found "
        f"{len(spawns)} Start-Process calls for powershell.exe")
    assert "function Start-Detached" in ps1, "expected a Start-Detached helper"
    # The helper has to build a genuinely quoted path out of $scriptPath...
    m = re.search(r"\$quoted\s*=\s*(.+)", ps1)
    assert m and '"' in m.group(1) and "$scriptPath" in m.group(1), (
        "Start-Detached must wrap the script path in double quotes")
    # ...and that quoted value is what -File receives.
    assert re.search(r'"-File",\s*\$quoted', ps1), (
        "the quoted path must be the argument that follows -File")


def test_windows_supervisor_guards_against_a_second_watcher():
    # Mirrors the Linux launcher: a supervisor that finds a live one already
    # holding the pidfile stands down, instead of clobbering the pidfile and
    # stacking another daemon that blocks forever on the single-instance lock.
    ps1 = _read(WIN_PS1)
    sh = _read(LINUX_SH)
    assert "not starting another" in ps1, (
        "the Windows supervisor should stand down when one is already running")
    assert "not starting another" in sh


def test_launchers_retire_the_daemon_holding_the_lock_after_an_update():
    """The update that lands on disk and never runs.

    Python reads its source once, at process start. A daemon started at LOGIN
    (Linux .desktop, Windows Startup-folder .cmd) holds the single-instance lock
    for the whole session, so a launcher that only restarts its OWN child leaves
    the real holder running old code forever - and its child just queues behind
    the lock. Both machines hit this; on Windows it looked exactly like an
    update that silently did nothing.

    So each launcher must reach for the DAEMON's pidfile, not just its child.
    """
    ps1 = _read(WIN_PS1)
    sh = _read(LINUX_SH)
    assert "function Retire-StaleDaemon" in ps1
    assert "retire_stale_daemon()" in sh
    assert "daemon.pid" in ps1 and "DAEMON_PID_FILE" in sh, (
        "retiring the stale daemon means finding it via the daemon pidfile "
        "(the single-instance lock), not via the launcher's own child handle")
    # And it must actually be wired to the update, not merely defined.
    assert "Retire-StaleDaemon" in ps1.split("function Retire-StaleDaemon")[1], (
        "Retire-StaleDaemon is defined but never called")
    assert "retire_stale_daemon" in sh.split("retire_stale_daemon()")[1], (
        "retire_stale_daemon is defined but never called")


def test_linux_retire_never_uses_sigterm():
    """SIGTERM is the daemon's "the machine is shutting down" signal and powers
    the TV OFF. Retiring a stale daemon must never look like a shutdown."""
    sh = _read(LINUX_SH)
    body = sh.split("retire_stale_daemon()")[1].split("\n}")[0]
    assert "-USR1" in body, "retiring should stop the daemon with SIGUSR1"
    assert "-TERM" not in body and "SIGTERM" not in body, (
        "retiring a stale daemon must not use SIGTERM - that powers the TV off")


def test_launchers_report_whether_the_update_worked():
    """"It launched" and "it updated" are different claims. The launcher has to
    distinguish updated / already-current / offline / failed, or a silently
    skipped update is indistinguishable from a successful one."""
    ps1 = _read(WIN_PS1)
    sh = _read(LINUX_SH)
    for name, text in (("Windows", ps1), ("Linux", sh)):
        for state in ("updated", "current", "offline", "failed"):
            assert state in text, f"{name} launcher never reports '{state}'"
        assert "Already up to date" in text, f"{name} launcher: no up-to-date message"
        assert "Could not reach GitHub" in text, f"{name} launcher: no offline message"
        assert "Update FAILED" in text, f"{name} launcher: no failure message"


def test_launchers_only_restart_when_code_actually_changed():
    """The old code restarted the daemon on every periodic check regardless, so
    a no-op check still dropped the TV connection. Gate it on a real change."""
    ps1 = _read(WIN_PS1)
    sh = _read(LINUX_SH)
    assert "function Sync-Changed" in ps1 and "if (Sync-Changed)" in ps1
    assert "sync_changed()" in sh and "if sync_changed; then" in sh


def test_launchers_do_not_poll_for_updates_in_the_background():
    """Updates apply when the user runs the launcher, and at no other time: a
    watcher that rewrites its own code mid-evening and restarts itself is a
    surprise, not a feature."""
    ps1 = _read(WIN_PS1)
    sh = _read(LINUX_SH)
    for name, text in (("Windows", ps1), ("Linux", sh)):
        assert "LGTV_EASY_UPDATE_INTERVAL" not in text, (
            f"{name} launcher still has a periodic update interval")
        assert "Periodic update check" not in text, (
            f"{name} launcher still polls for updates in the background")


def test_windows_supervisor_does_not_redirect_both_streams_to_one_file():
    # PowerShell's Start-Process raises a terminating error when standard output
    # and standard error are redirected to the SAME file - that would crash the
    # background watcher on every Windows launch. Make sure the two redirects
    # never name the same path again.
    ps1 = _read(WIN_PS1)
    import re
    # The two redirect flags sit next to each other on one Start-Process call.
    pairs = re.findall(
        r"-RedirectStandardError\s+(\S+)\s+-RedirectStandardOutput\s+(\S+)", ps1)
    assert pairs, "expected the supervisor to redirect the daemon's streams"
    for err, out in pairs:
        assert err != out, (
            "Start-Process redirects stdout and stderr to the same file "
            f"({err}); PowerShell forbids this and the supervisor will crash.")

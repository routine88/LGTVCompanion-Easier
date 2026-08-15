"""Guard-rails for the installers, in the spirit of test_launchers.py.

Nothing here builds anything - these are plain-text checks on the packaging
files, aimed at the mistakes that only show up on a user's machine after a
release: a name that has to match in two files quietly drifting apart. Each of
these pairs is invisible until something looks wrong on a desktop nobody on the
project is running.
"""
import os
import re

import pytest

from lgtv_easy import branding

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WIN = os.path.join(REPO, "packaging", "windows")
LINUX = os.path.join(REPO, "packaging", "linux")

APP_ID_LINUX = "lgtv-companion-easy"          # the .desktop / icon-theme name


def read(*parts):
    path = os.path.join(*parts)
    if not os.path.exists(path):
        pytest.skip(f"{path} not present (running outside a full checkout)")
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ----- everything a build needs is committed ---------------------------------
def test_the_packaging_files_exist():
    for name in ("app.spec", "app_entry.py", "installer.spec", "installer.py",
                 "shortcuts.py", "build.ps1"):
        assert os.path.exists(os.path.join(WIN, name)), f"missing windows/{name}"
    for name in ("install.sh", "uninstall.sh"):
        assert os.path.exists(os.path.join(LINUX, name)), f"missing linux/{name}"


# ----- Windows ---------------------------------------------------------------
def test_the_installer_stamps_the_apps_own_app_id():
    """Shortcut AppUserModelID vs the one the app declares at startup. When
    these disagree, pinning the app to the taskbar produces a second, dead
    button beside the running one."""
    installer = read(WIN, "installer.py")
    assert f'APP_ID = "{branding.APP_ID}"' in installer
    assert "app_id=APP_ID" in installer, "the shortcuts must actually carry it"


def test_the_installer_uses_the_exe_names_the_build_produces():
    spec = read(WIN, "app.spec")
    installer = read(WIN, "installer.py")
    assert f'GUI_NAME = "{os.path.splitext(branding.GUI_EXE)[0]}"' in spec
    assert f'CLI_NAME = "{os.path.splitext(branding.CLI_EXE)[0]}"' in spec
    assert f'GUI_EXE = "{branding.GUI_EXE}"' in installer
    assert f'CLI_EXE = "{branding.CLI_EXE}"' in installer


def test_the_windows_build_ships_the_icon_and_the_assets():
    spec = read(WIN, "app.spec")
    assert "icon.ico" in spec, "the .exe needs its icon embedded"
    assert "lgtv_easy/assets" in spec, "the app looks for its icons at runtime"


def test_both_windows_exes_are_built_and_only_one_has_a_console():
    """One windowed (no black box at login), one console (so `status` prints)."""
    spec = read(WIN, "app.spec")
    assert spec.count("console=False") == 1
    assert spec.count("console=True") == 1
    assert "COLLECT(" in spec and "gui_exe, cli_exe" in spec


def test_the_installer_registers_an_uninstaller():
    installer = read(WIN, "installer.py")
    assert "UninstallString" in installer and "QuietUninstallString" in installer
    assert "CurrentVersion\\Uninstall" in installer
    # Per-user install: an app that needs admin to install is a different
    # (and much more annoying) product.
    assert "LOCALAPPDATA" in installer
    assert "HKEY_LOCAL_MACHINE" not in installer


def test_the_uninstaller_steps_out_of_the_folder_it_deletes():
    installer = read(WIN, "installer.py")
    assert "relaunch_from_temp" in installer, (
        "uninstall.exe lives in the directory it has to remove; Windows will "
        "not delete a running executable, so it must re-run from %TEMP%")


# ----- Linux ------------------------------------------------------------------
def test_the_desktop_entry_matches_the_windows_class_tk_reports():
    """StartupWMClass is how GNOME/KDE tie the running window to this entry -
    and therefore how the dock button gets our icon instead of a placeholder."""
    sh = read(LINUX, "install.sh")
    assert f'WM_CLASS="{branding.WM_CLASS}"' in sh
    assert "StartupWMClass=$WM_CLASS" in sh


def test_the_desktop_entry_has_what_a_menu_needs():
    sh = read(LINUX, "install.sh")
    for line in ("Type=Application", "Name=$APP_NAME", "Exec=$LAUNCHER gui",
                 "Icon=$APP_ID", "Terminal=false", "Categories="):
        assert line in sh, f"the .desktop template is missing {line!r}"


def test_the_installer_places_every_icon_size_in_the_hicolor_theme():
    sh = read(LINUX, "install.sh")
    match = re.search(r"for size in ([0-9 ]+); do\n\s+src=", sh)
    assert match, "expected a loop installing the PNG icon sizes"
    sizes = {int(s) for s in match.group(1).split()}
    shipped = {int(p.stem.split("-")[1])
               for p in branding.ASSETS.glob("icon-*.png")}
    assert sizes == shipped, (
        f"installer copies {sorted(sizes)} but the package ships "
        f"{sorted(shipped)}")
    assert "hicolor/scalable/apps" not in sh or "icon.svg" in sh


def test_the_desktop_shortcut_is_marked_trusted():
    """GNOME 42+ refuses to launch a desktop file that is not trusted; without
    this the icon lands on the desktop and then does nothing when clicked."""
    sh = read(LINUX, "install.sh")
    assert "metadata::trusted" in sh
    assert "chmod 0755" in sh


def test_uninstall_removes_exactly_what_install_created():
    sh = read(LINUX, "install.sh")
    uninstall = sh.split("if [ \"$MODE\" = \"uninstall\" ]")[1].split("exit 0")[0]
    for path in ("$DESKTOP_FILE", "$LAUNCHER", "$LIB_DIR", "$AUTOSTART_FILE"):
        assert path in uninstall, f"uninstall leaves {path} behind"
    assert "$ICONS_DIR" in uninstall
    # Settings are the user's, not ours: they survive unless --purge is given.
    assert "PURGE" in uninstall


def test_the_installer_does_not_power_the_tv_off_while_installing():
    """SIGTERM means "the machine is going down, turn the TV off" to the daemon.
    Stopping a watcher in order to replace its files must use SIGUSR1 instead,
    or installing an update blanks the screen you are working on."""
    sh = read(LINUX, "install.sh")
    stop = sh.split("stop_watcher() {")[1].split("\n}")[0]
    assert "kill -USR1" in stop
    assert "kill -TERM" not in stop and "kill -9" not in stop


def test_the_linux_installer_is_posix_sh():
    sh = read(LINUX, "install.sh")
    assert sh.startswith("#!/bin/sh"), "must run under dash, not just bash"
    for bashism in ("[[", "declare ", "local ", "=(", "function "):
        assert bashism not in sh, f"bash-only syntax in a /bin/sh script: {bashism!r}"

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

from lgtv_easy import branding, dock

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
                 "build.ps1"):
        assert os.path.exists(os.path.join(WIN, name)), f"missing windows/{name}"
    # The shortcut writer lives in the package: the installer uses it for the
    # Start Menu and desktop icons, and autostart uses it for the login entry.
    assert os.path.exists(os.path.join(REPO, "EasyMode", "lgtv_easy",
                                       "winshortcut.py"))
    # And the launcher-bar module, which both installers call to put the app
    # where the user actually clicks.
    assert os.path.exists(os.path.join(REPO, "EasyMode", "lgtv_easy", "dock.py"))
    for name in ("install.sh", "uninstall.sh"):
        assert os.path.exists(os.path.join(LINUX, name)), f"missing linux/{name}"


# ----- Windows ---------------------------------------------------------------
def test_the_installer_uses_the_packaged_shortcut_writer():
    installer = read(WIN, "installer.py")
    assert "from lgtv_easy import winshortcut as shortcuts" in installer
    spec = read(WIN, "installer.spec")
    for module in ("lgtv_easy.winshortcut", "lgtv_easy.dock"):
        assert module in spec, (
            f"the frozen installer must bundle {module}, which it imports")


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


def test_the_windows_installer_puts_the_app_on_the_launcher_bar():
    """A Start Menu entry is not somewhere anyone looks. Quick Launch is a plain
    folder of shortcuts, so it is the one launcher-bar slot an installer can
    actually fill on Windows - the taskbar pin has had no supported API since
    8.1, which is why it is attempted rather than relied on."""
    installer = read(WIN, "installer.py")
    assert "quick_launch_link()" in installer
    assert "dock.quick_launch_link()" in installer, (
        "the path must come from lgtv_easy.dock, or the installer and the app's "
        "own switch write two different files and the icon appears twice")
    assert "quick_launch=" in installer, "the choice must be plumbed through"
    assert "try_pin_to_taskbar" in installer


def test_the_windows_uninstaller_takes_the_launcher_bar_icon_with_it():
    installer = read(WIN, "installer.py")
    body = installer.split("def uninstall(")[1].split("\ndef ")[0]
    assert "_remove(quick_launch_link())" in body
    assert "try_unpin_from_taskbar" in body


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


def test_the_linux_installer_pins_the_app_to_the_dock():
    """The .desktop file puts the app in the applications menu; nothing about
    that makes an icon appear on the dock, which is where an Ubuntu user looks
    for it. The installer has to ask for the pin explicitly."""
    sh = read(LINUX, "install.sh")
    assert '"$LAUNCHER" dock add' in sh
    assert "--no-dock-icon" in sh, "and it has to be declinable"
    # Per-user state written through the caller's own session bus: a --system
    # install running as root would otherwise pin the app to root's dock.
    pin = sh.split("# ---- the dock / favourites bar")[1].split("# ----")[0]
    assert '[ "$SYSTEM" != "1" ]' in pin


def test_linux_uninstall_unpins_before_it_deletes_the_app():
    """Ordering matters: once the launcher is gone, nothing on the machine can
    unpin it, and the dock keeps a dead icon the user cannot get rid of."""
    sh = read(LINUX, "install.sh")
    uninstall = sh.split('if [ "$MODE" = "uninstall" ]')[1].split("exit 0")[0]
    assert '"$LAUNCHER" dock remove' in uninstall
    assert uninstall.index('dock remove') < uninstall.index('rm -rf "$LIB_DIR"')


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


def test_the_desktop_actions_carry_only_legal_keys():
    """"Terminal" is legal in [Desktop Entry] and a spec violation inside a
    [Desktop Action]. The installer shipped it for months; desktop-file-validate
    rejected the result, and an entry the validator rejects is one some shells
    decline to show - which reads to the user as "the shortcut never appeared".
    """
    sh = read(LINUX, "install.sh")
    actions = sh.split("[Desktop Action", 1)[1].split("EOF", 1)[0]
    for line in actions.splitlines():
        line = line.strip()
        if "=" not in line or line.startswith(("#", "[")):
            continue
        key = line.split("=", 1)[0]
        assert key in ("Name", "Icon", "Exec") or key.startswith("X-"), \
            f"{key!r} is not allowed in a [Desktop Action] group"

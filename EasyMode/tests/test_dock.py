"""The launcher bar: the dock on Linux, Quick Launch on Windows.

A menu entry nobody has to look for is the whole point of pinning, and the
failure mode is silent - the install "works", the app runs, and the icon the
user expected simply is not there. So these tests pin, unpin and re-pin against
the sandbox from conftest (never the machine's real dock) and hold the
desktop-file id together across the three files that have to agree on it.
"""
import os

import pytest

from lgtv_easy import autostart, branding, dock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture(autouse=True)
def own_sandbox(tmp_path, monkeypatch):
    """A dock of this test's own, so the order tests run in cannot matter."""
    monkeypatch.setenv("LGTV_EASY_DOCK_SANDBOX", str(tmp_path))
    return tmp_path


def read(*parts):
    path = os.path.join(REPO, *parts)
    if not os.path.exists(path):
        pytest.skip(f"{path} not present (running outside a full checkout)")
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ----- the one name everything has to agree on -------------------------------
def test_the_desktop_id_matches_everywhere_it_is_written():
    """dock.py pins an id; install.sh writes the file that id names; autostart
    writes a second entry beside it. If any of the three drifts, the dock ends
    up pointing at a .desktop that does not exist - which shows up as an icon
    that does nothing, or no icon at all."""
    assert dock.DESKTOP_ID == autostart.APP_ID
    assert f'APP_ID="{dock.DESKTOP_ID}"' in read("packaging", "linux", "install.sh")
    assert f'APP_ID="{dock.DESKTOP_ID}"' in read("Linux Uninstall.sh")


# ----- pinning ----------------------------------------------------------------
def test_pinning_is_off_until_asked_for():
    assert dock.is_pinned() is False
    assert "not on" in dock.status()


def test_add_pins_and_remove_unpins():
    dock.add()
    assert dock.is_pinned() is True
    assert dock.remove() is True
    assert dock.is_pinned() is False


def test_adding_twice_leaves_one_icon():
    """A re-run of the installer is the normal case, not the exception, and a
    dock with the same app on it three times is what a naive append gives you."""
    dock.add()
    dock.add()
    if os.name == "nt":
        assert dock.quick_launch_link().exists()
    else:
        schema, key, _ = dock._targets()[0]
        assert dock._get(schema, key).count(dock.DESKTOP_FILE) == 1


def test_removing_when_not_pinned_says_so_rather_than_lying():
    assert dock.remove() is False
    assert "not on" in dock.set_pinned(False)


def test_set_pinned_round_trips():
    dock.set_pinned(True)
    assert dock.is_pinned() is True
    dock.set_pinned(False)
    assert dock.is_pinned() is False


# ----- Linux specifics --------------------------------------------------------
linux_only = pytest.mark.skipif(os.name == "nt", reason="Linux dock only")


@linux_only
def test_pinning_writes_a_menu_entry_when_nothing_installed_one():
    """The portable launcher installs no .desktop at all, and a shell cannot pin
    an app it has never heard of - so pinning has to create the entry first."""
    assert dock.installed_entry() is None
    dock.add()
    entry = dock.installed_entry()
    assert entry is not None and entry.name == dock.DESKTOP_FILE


@linux_only
def test_the_entry_carries_the_window_class_that_lights_the_icon_up():
    """Without StartupWMClass the shell cannot tell that the running window
    belongs to the pinned icon, and puts a second, anonymous button beside it."""
    dock.add()
    text = dock.installed_entry().read_text(encoding="utf-8")
    assert f"StartupWMClass={branding.WM_CLASS}" in text
    assert "Exec=" in text and "Type=Application" in text


@linux_only
def test_an_entry_that_is_already_installed_is_left_alone():
    """install.sh writes a richer entry than this module would - with actions,
    TryExec and the icon-theme name - and pinning must not overwrite it."""
    entry = dock._data_home() / "applications" / dock.DESKTOP_FILE
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("[Desktop Entry]\nType=Application\nName=Installed\n",
                     encoding="utf-8")
    dock.add()
    assert "Name=Installed" in entry.read_text(encoding="utf-8")


@linux_only
def test_pinning_appends_and_keeps_what_was_already_on_the_dock():
    schema, key, _ = dock._targets()[0]
    dock._set(schema, key, ["firefox.desktop", "org.gnome.Nautilus.desktop"])
    dock.add()
    assert dock._get(schema, key) == ["firefox.desktop",
                                      "org.gnome.Nautilus.desktop",
                                      dock.DESKTOP_FILE]
    dock.remove()
    assert dock._get(schema, key) == ["firefox.desktop",
                                      "org.gnome.Nautilus.desktop"]


@linux_only
@pytest.mark.parametrize("printed, expected", [
    ("@as []", []),
    ("['a.desktop']", ["a.desktop"]),
    ("['a.desktop', 'b.desktop']", ["a.desktop", "b.desktop"]),
    ("", []),
    ("not a list at all", []),
])
def test_gsettings_output_is_read_back_correctly(printed, expected):
    """`gsettings get` prints "@as []" for an empty list - the GVariant type
    annotation - which is not Python syntax and used to be read as junk."""
    assert dock._parse_list(printed) == expected


# ----- the desktop shortcut ---------------------------------------------------
def test_a_desktop_shortcut_is_placed_only_when_asked_for():
    """The installers write their own - they know exactly what they laid down -
    so pinning must not add a second icon beside it."""
    dock.add()
    assert not dock.desktop_shortcut().exists()
    placed = dock.ensure_desktop_shortcut()
    assert placed is not None and placed.exists()


def test_the_cli_flag_is_what_places_it():
    from lgtv_easy import cli

    assert cli.main(["dock", "add"]) == 0
    assert not dock.desktop_shortcut().exists()
    assert cli.main(["dock", "add", "--with-desktop-shortcut"]) == 0
    assert dock.desktop_shortcut().exists()


def test_the_cli_reports_and_reverses_the_pin():
    from lgtv_easy import cli

    assert cli.main(["dock", "status"]) == 0
    assert cli.main(["dock", "add"]) == 0
    assert dock.is_pinned() is True
    assert cli.main(["dock", "remove"]) == 0
    assert dock.is_pinned() is False


# ----- Windows specifics ------------------------------------------------------
def test_the_taskbar_verb_script_does_not_expect_command_line_arguments():
    """powershell.exe -Command does not fill $args - it appends whatever follows
    to the command string. The Quick Launch path always contains a space, so a
    script reading $args[0] would silently run against the wrong path, or fail
    to parse. The values go through the environment instead."""
    assert "$args" not in dock._VERB_SCRIPT
    assert "$env:LGTV_EASY_PIN_TARGET" in dock._VERB_SCRIPT
    assert "$env:LGTV_EASY_PIN_VERB" in dock._VERB_SCRIPT


def test_the_pin_and_unpin_verbs_cannot_match_each_other():
    """"Unpin from taskbar" contains "Pin to"'s keyword; a loose pattern would
    have the installer unpin the app it just pinned."""
    import re

    pin, unpin = dock.PIN_VERB, dock.UNPIN_VERB
    assert re.match(pin, "Pin to Taskbar", re.I)
    assert not re.match(pin, "Unpin from Taskbar", re.I)
    assert re.match(unpin, "Unpin from Taskbar", re.I)
    assert not re.match(unpin, "Pin to Taskbar", re.I)



@pytest.mark.skipif(os.name != "nt", reason="Quick Launch is Windows-only")
def test_the_quick_launch_shortcut_lands_in_the_real_folder(monkeypatch,
                                                            tmp_path):
    monkeypatch.delenv("LGTV_EASY_DOCK_SANDBOX", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert dock.quick_launch_link() == (
        tmp_path / "Microsoft" / "Internet Explorer" / "Quick Launch"
        / f"{dock.FRIENDLY}.lnk")

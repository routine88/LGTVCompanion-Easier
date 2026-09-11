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


# ----- the icon has to appear without anyone running an installer ------------
def test_launching_the_app_creates_the_menu_entry_and_pins_it_once():
    """An icon you only get if you happened to run an installer is an icon most
    people never get. The app makes it on launch instead."""
    assert dock.installed_entry() is None
    assert dock.is_pinned() is False
    dock.ensure_on_launch()
    assert dock.installed_entry() is not None
    assert dock.is_pinned() is True


def test_an_icon_the_user_removed_stays_removed():
    """The dock belongs to the user. Putting back something they deliberately
    dragged off it at every launch is how an app gets uninstalled."""
    dock.ensure_on_launch()
    assert dock.remove() is True
    dock.ensure_on_launch()
    assert dock.is_pinned() is False, "re-pinned an icon the user took off"


def test_the_menu_entry_is_still_repaired_after_the_user_unpins():
    """Preference and correctness are different things: the dock is theirs, a
    missing or broken menu entry is a bug."""
    dock.ensure_on_launch()
    dock.remove()
    dock.installed_entry().unlink()
    dock.ensure_on_launch()
    assert dock.installed_entry() is not None
    assert dock.is_pinned() is False


# ----- the entry we write has to be legal ------------------------------------
def test_actions_carry_only_the_keys_the_spec_allows():
    """"Terminal" is legal in [Desktop Entry] and a violation inside a
    [Desktop Action]; desktop-file-validate rejects the file outright, and a
    rejected file is one some shells decline to show at all."""
    entry = dock.ensure_entry()
    text = entry.read_text(encoding="utf-8")
    in_action = False
    for line in text.splitlines():
        if line.startswith("["):
            in_action = line.startswith("[Desktop Action")
            continue
        if in_action and "=" in line:
            key = line.split("=", 1)[0]
            assert key in ("Name", "Icon", "Exec") or key.startswith("X-"), \
                f"{key!r} is not allowed in a [Desktop Action] group"


def test_an_entry_with_an_illegal_action_key_is_detected_and_rewritten():
    entry = dock.ensure_entry()
    entry.write_text(entry.read_text(encoding="utf-8") + "\nTerminal=true\n",
                     encoding="utf-8")
    assert dock._entry_is_broken(entry) is True
    dock.ensure_entry()
    assert dock._entry_is_broken(entry) is False


def test_a_healthy_entry_is_left_exactly_as_it_is():
    """install.sh writes a richer entry than this module would. Repair must fix
    what is broken, not overwrite what is not."""
    entry = dock._data_home() / "applications" / dock.DESKTOP_FILE
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("[Desktop Entry]\nType=Application\nName=Installed by the "
                     "installer\nExec=/usr/bin/true\n", encoding="utf-8")
    dock.ensure_entry()
    assert "Installed by the installer" in entry.read_text(encoding="utf-8")


def test_the_icon_is_named_not_pathed_once_the_theme_has_it():
    """An absolute path into wherever this copy happens to be running from is an
    icon that breaks the day that folder moves."""
    dock.install_icons()
    entry = dock.ensure_entry()
    text = entry.read_text(encoding="utf-8")
    assert f"Icon={dock.DESKTOP_ID}\n" in text, "the entry hard-codes an icon path"


def test_icons_are_installed_into_the_theme():
    assert dock.install_icons() > 0
    sizes = list((dock._icon_dir()).glob("*/apps/" + dock.DESKTOP_ID + ".*"))
    assert sizes, "no icons landed in the hicolor theme"
    # Running twice must not re-copy what is already there.
    assert dock.install_icons() == 0


def test_a_pin_that_does_not_stick_leaves_no_done_marker(monkeypatch):
    """GNOME keeps favourites as desktop ids and silently drops the ones it
    cannot resolve - so a brand-new entry gets its pin pruned moments after it
    is written. gsettings reports success and the icon never appears. Recording
    "done" for that is how an icon stays missing for ever: the next launch has
    to try again."""
    monkeypatch.setattr(dock, "add", lambda **kw: "claimed success")
    monkeypatch.setattr(dock, "is_pinned", lambda: False)
    monkeypatch.setattr(dock, "_PIN_ATTEMPTS", 1)
    monkeypatch.setattr(dock, "_CONFIRM_WINDOW_SECONDS", 0.0)
    monkeypatch.setattr(dock, "_INDEX_SETTLE_SECONDS", 0.0)
    dock.ensure_on_launch()
    assert not dock._marker_path().exists(), "marked done for a pin that failed"


def test_the_pin_is_retried_until_the_desktop_keeps_it(monkeypatch):
    attempts = {"n": 0}
    stuck = {"v": False}

    def flaky_add(**kw):
        attempts["n"] += 1
        stuck["v"] = attempts["n"] >= 3      # the shell keeps it on the 3rd go
        return "added"

    monkeypatch.setattr(dock, "add", flaky_add)
    monkeypatch.setattr(dock, "is_pinned", lambda: stuck["v"])
    monkeypatch.setattr(dock, "_PIN_ATTEMPTS", 4)
    monkeypatch.setattr(dock, "_CONFIRM_WINDOW_SECONDS", 0.0)
    monkeypatch.setattr(dock, "_INDEX_SETTLE_SECONDS", 0.0)
    dock.ensure_on_launch()
    assert attempts["n"] == 3
    assert dock._marker_path().exists(), "a pin that stuck was not recorded"


def test_a_pin_that_sticks_first_time_is_not_retried(monkeypatch):
    attempts = {"n": 0}

    def counting_add(**kw):
        attempts["n"] += 1
        return "added"

    monkeypatch.setattr(dock, "add", counting_add)
    monkeypatch.setattr(dock, "is_pinned", lambda: True)
    monkeypatch.setattr(dock, "_CONFIRM_WINDOW_SECONDS", 0.0)
    monkeypatch.setattr(dock, "_INDEX_SETTLE_SECONDS", 0.0)
    dock.ensure_on_launch()
    assert attempts["n"] == 1


def test_the_logger_does_not_double_up_across_imports():
    """Module-global memoisation over a process-global logger: import the module
    twice under different names - a PYTHONPATH'd install plus a source tree does
    it easily - and the second import attaches a second file handler. Nothing
    breaks; every line just appears twice for ever, which makes the log read as
    though the app is doing everything twice."""
    import importlib
    import logging

    from lgtv_easy import applog

    applog.get_logger()
    for _ in range(3):
        importlib.reload(applog)
        applog.get_logger()
    assert len(logging.getLogger("lgtv_easy").handlers) == 1

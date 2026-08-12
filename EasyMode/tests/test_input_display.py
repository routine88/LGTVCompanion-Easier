"""The settings panel's read-out of which input this PC is on.

Which socket this PC occupies is worked out from the TV rather than configured,
so the window has to say whether that has happened - otherwise "it isn't
blanking the TV" and "it hasn't learned yet" look identical from the GUI.

These drive the real tkinter panel on an off-screen display; they skip cleanly
where there is no display or no tkinter.
"""
import os
import tempfile

import pytest

from lgtv_easy.config import Config, Device
from lgtv_easy.webos import input_label

tk = pytest.importorskip("tkinter")


@pytest.fixture
def panel(monkeypatch):
    """A built SettingsPanel over a throwaway config, or skip if no display."""
    home = tempfile.mkdtemp(prefix="lgtv-input-")
    monkeypatch.setenv("LGTV_EASY_HOME", home)
    monkeypatch.setenv("LGTV_EASY_NO_SELFTEST", "1")
    monkeypatch.setenv("LGTV_EASY_NO_SLEEP_WATCH", "1")

    cfg = Config(setup_complete=True)
    cfg.device = Device(name="mock", ip="127.0.0.1", key="k")
    cfg.save()

    from lgtv_easy import gui
    try:
        app = gui.App()
    except tk.TclError as exc:  # no X display
        pytest.skip(f"no display: {exc}")
    app.update_idletasks()
    app.update()
    built = app.container.winfo_children()[0]
    if not isinstance(built, gui.SettingsPanel):
        app.destroy()
        pytest.skip("settings panel not shown for this config")
    yield built
    app.destroy()


def _text(panel) -> str:
    return str(panel._input_line.cget("text"))


def _style(panel) -> str:
    return str(panel._input_line.cget("style"))


# ----- the three states ------------------------------------------------
def test_says_it_is_still_learning_before_any_input_is_known(panel):
    assert panel.app.cfg.device.input_id == ""
    assert "Learning" in _text(panel)
    assert _style(panel) == "CardMuted.TLabel"


def test_says_which_input_once_learned(panel):
    panel.app.cfg.device.input_id = "hdmi2"
    panel._refresh_input_line()
    text = _text(panel)
    assert "Learned" in text
    assert "HDMI 2" in text, "should read as the TV's own input name"
    assert _style(panel) == "CardOk.TLabel"


def test_says_it_is_off_when_the_guard_is_disabled(panel):
    panel.app.cfg.device.input_id = "hdmi2"
    panel.only_mine.set(False)
    panel._refresh_input_line()
    text = _text(panel)
    assert text.startswith("Off")
    assert "HDMI 2" in text, "still worth saying what it's ignoring"


# ----- picking up what a background watcher learned --------------------
def test_notices_an_input_learned_by_a_separate_watcher(panel):
    """The daemon usually runs in another process and persists what it learns,
    so the panel has to re-read the file to ever show it."""
    assert "Learning" in _text(panel)

    disk = Config.load()          # simulate the watcher learning and saving
    disk.device.input_id = "hdmi3"
    disk.save()

    assert panel._adopt_learned_device() is True
    panel._refresh_input_line()
    assert "HDMI 3" in _text(panel)


def test_saving_a_setting_does_not_wipe_what_the_watcher_learned(panel):
    """The panel loads config once at startup. Without re-reading, toggling any
    switch would write its stale, empty copy of the TV's details back over the
    address, MAC and input the watcher had since worked out."""
    disk = Config.load()
    disk.device.input_id = "hdmi4"
    disk.device.mac = "AA:BB:CC:DD:EE:FF"
    disk.save()

    panel.mute.set(True)
    panel._apply()                # writes the whole config back out

    saved = Config.load()
    assert saved.device.input_id == "hdmi4"
    assert saved.device.mac == "AA:BB:CC:DD:EE:FF"
    assert saved.mute_on_sleep is True, "the actual edit still applied"


def test_a_relearned_input_replaces_the_old_one(panel):
    """A cable moved to another socket: the file wins, since the daemon is the
    only thing that sets this and a stale value would be written back."""
    panel.app.cfg.device.input_id = "hdmi1"
    disk = Config.load()
    disk.device.input_id = "hdmi3"
    disk.save()

    assert panel._adopt_learned_device() is True
    assert panel.app.cfg.device.input_id == "hdmi3"


def test_the_poll_stops_once_the_panel_is_gone(panel):
    """The timer reschedules itself; left unguarded it would keep firing against
    a destroyed widget after 'Re-run setup' swaps the panel out."""
    panel.destroy()
    panel._watch_input()  # must return quietly rather than raise TclError


# ----- naming ----------------------------------------------------------
def test_input_ids_read_as_the_tvs_own_names():
    assert input_label("hdmi2") == "HDMI 2"
    assert input_label("livetv") == "Live TV"
    assert input_label("netflix") == "Netflix"
    assert input_label("") == ""
    assert input_label("weird.thing") == "weird.thing", "never mangle the unknown"

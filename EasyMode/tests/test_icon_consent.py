"""Asking permission before putting icons on somebody's desktop.

The app used to place a dock icon on its own, on the reasoning that an icon
nobody ever gets is no use. That is true and still not the app's call to make
unasked. So it asks - once, only about an icon that is both unanswered and
missing - and an answer of "no" is permanent.

The rules these pin down, in the order they matter:
  * an icon already there is never asked about;
  * "no" is asked once and never again;
  * "yes" is maintained, so an icon that gets deleted comes back;
  * nothing here ever deletes an icon the user put there.
"""
import pytest

from lgtv_easy import dock
from lgtv_easy.config import Config


@pytest.fixture(autouse=True)
def own_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("LGTV_EASY_DOCK_SANDBOX", str(tmp_path))
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path / "cfg"))
    # The real pin waits on the desktop; nothing here is testing that.
    monkeypatch.setattr(dock, "_CONFIRM_WINDOW_SECONDS", 0.0)
    monkeypatch.setattr(dock, "_INDEX_SETTLE_SECONDS", 0.0)
    return tmp_path


# ----- when the question gets asked ------------------------------------------
def test_both_are_asked_when_nothing_exists_and_nothing_is_known():
    cfg = Config()
    assert dock.pending_questions(cfg) == (True, True)


def test_an_icon_already_there_is_never_asked_about():
    """Nobody wants to be asked permission for something they can already see."""
    cfg = Config()
    dock.ensure_desktop_shortcut()
    dock.add()
    assert dock.pending_questions(cfg) == (False, False)
    # ...and it is recorded as wanted, so it will be maintained from now on.
    assert cfg.desktop_icon is True
    assert cfg.taskbar_icon is True


def test_only_the_missing_one_is_asked_about():
    cfg = Config()
    dock.ensure_desktop_shortcut()
    assert dock.pending_questions(cfg) == (False, True)
    assert cfg.desktop_icon is True


@pytest.mark.parametrize("answer", [True, False])
def test_an_answered_question_is_never_asked_again(answer):
    cfg = Config(desktop_icon=answer, taskbar_icon=answer)
    assert dock.pending_questions(cfg) == (False, False)


def test_no_means_no_even_though_the_icon_is_missing():
    """The point of the whole exercise: a refusal must not turn into the same
    offer again at the next launch."""
    cfg = Config(desktop_icon=False, taskbar_icon=False)
    assert not dock.desktop_shortcut().exists() and not dock.is_pinned()
    assert dock.pending_questions(cfg) == (False, False)


def test_an_icon_removed_under_the_old_behaviour_is_not_re_offered(own_sandbox):
    """Older builds pinned without asking. Someone who took that icon off has
    already answered, even though nobody asked them."""
    cfg = Config()
    dock._marker_path().parent.mkdir(parents=True, exist_ok=True)
    dock._marker_path().write_text("", encoding="utf-8")
    ask_desktop, ask_taskbar = dock.pending_questions(cfg)
    assert ask_taskbar is False
    assert cfg.taskbar_icon is False


# ----- what it does with the answer ------------------------------------------
def test_yes_is_maintained_when_an_icon_gets_deleted():
    cfg = Config(desktop_icon=True, taskbar_icon=True)
    dock.apply_preferences(cfg)
    assert dock.desktop_shortcut().exists() and dock.is_pinned()

    dock.desktop_shortcut().unlink()
    dock.remove()
    dock.apply_preferences(cfg)
    assert dock.desktop_shortcut().exists(), "a wanted icon was not restored"
    assert dock.is_pinned(), "a wanted pin was not restored"


def test_no_creates_nothing():
    cfg = Config(desktop_icon=False, taskbar_icon=False)
    dock.apply_preferences(cfg)
    assert not dock.desktop_shortcut().exists()
    assert not dock.is_pinned()


def test_applying_never_deletes_an_icon_the_user_has():
    """"No" is honoured by not creating. An icon they made themselves is theirs,
    and the launch check is not the place to tidy it away."""
    cfg = Config(desktop_icon=False, taskbar_icon=False)
    dock.ensure_desktop_shortcut()
    dock.add()
    dock.apply_preferences(cfg)
    assert dock.desktop_shortcut().exists()
    assert dock.is_pinned()


# ----- the menu entry is not a preference ------------------------------------
def test_the_menu_entry_is_repaired_whatever_the_user_said():
    """Without it the app cannot be found in the applications list at all, so it
    is correctness rather than decoration - and never gated on consent."""
    cfg = Config(desktop_icon=False, taskbar_icon=False)
    dock.ensure_on_launch(cfg)
    assert dock.installed_entry() is not None


def test_ensure_on_launch_with_no_config_only_repairs():
    """Called without a config it must not guess at preferences."""
    dock.ensure_on_launch()
    assert dock.installed_entry() is not None
    assert not dock.desktop_shortcut().exists()
    assert not dock.is_pinned()

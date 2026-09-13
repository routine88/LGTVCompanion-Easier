"""What the shortcuts question turns into.

Two rules here are easy to get wrong and expensive when they are: a question
that was never asked must not come back as an answer, and closing the window
must not be recorded as a permanent refusal - a "no" that lasts for ever should
come from somebody choosing it, not from a stray click on the X.

These drive gui.icon_answers rather than the dialog itself. The window ends in
wait_window under a grab, which cannot be driven from a test process that has
already built other Tk roots (see test_window_fits.py on Tk's unreliability
after a few of those); an earlier version of this file deadlocked the whole
suite trying. The window is thin; the rules are not, and the rules are here.
"""
import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    window.withdraw()
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


def _asked(root, **switches):
    return {key: tk.BooleanVar(master=root, value=value)
            for key, value in switches.items()}


def test_a_question_that_was_not_asked_returns_no_answer(root):
    """Returning False for a question nobody saw would silently record a
    refusal the user never gave."""
    from lgtv_easy import gui

    answers = gui.icon_answers(_asked(root, desktop=True), answered=True)
    assert answers == {"desktop": True, "taskbar": None}


def test_both_questions_come_back_when_both_were_asked(root):
    from lgtv_easy import gui

    answers = gui.icon_answers(_asked(root, desktop=True, taskbar=True),
                               answered=True)
    assert answers == {"desktop": True, "taskbar": True}


def test_switches_left_off_record_a_refusal(root):
    from lgtv_easy import gui

    answers = gui.icon_answers(_asked(root, desktop=False, taskbar=False),
                               answered=True)
    assert answers == {"desktop": False, "taskbar": False}


def test_one_of_each(root):
    from lgtv_easy import gui

    answers = gui.icon_answers(_asked(root, desktop=True, taskbar=False),
                               answered=True)
    assert answers == {"desktop": True, "taskbar": False}


def test_closing_the_window_is_not_a_refusal(root):
    """It is "not now" - so the question stands, rather than being silently
    settled against the user by a click they may not have meant."""
    from lgtv_easy import gui

    assert gui.icon_answers(_asked(root, desktop=True, taskbar=True),
                            answered=False) is None

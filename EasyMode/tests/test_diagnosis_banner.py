"""The red card that tells the user what the watcher found while they were away.

The watcher reaches its conclusion overnight, with no window open. This card is
how that conclusion survives until somebody opens one - and the reason it has
tests of its own is the rule that keeps it worth reading: it appears only for a
fault that will still be there tomorrow. A TV that was switched off overnight is
not a fault, and a red box every morning is a red box nobody reads.
"""
import time

import pytest

from lgtv_easy import selfheal

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="module")
def root():
    from lgtv_easy import gui
    try:
        window = tk.Tk()
    except tk.TclError as exc:                     # no display
        pytest.skip(f"no display: {exc}")
    gui._apply_theme(window)
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


def _banner(root, verdict):
    from lgtv_easy import gui
    diagnosis = selfheal.Diagnosis(verdict=verdict, summary="what went wrong",
                                   at=time.time())
    frame = tk.Frame(root)
    return gui.make_diagnosis_banner(frame, diagnosis), frame


@pytest.mark.parametrize("verdict", list(selfheal.NEEDS_USER))
def test_a_fault_that_needs_a_human_is_shown(root, verdict):
    card, _parent = _banner(root, verdict)
    assert card is not None


@pytest.mark.parametrize("verdict", [selfheal.VERDICT_TV_OFF, selfheal.VERDICT_OK,
                                     selfheal.VERDICT_RECOVERED])
def test_nothing_is_shown_for_a_state_that_is_not_a_fault(root, verdict):
    card, _parent = _banner(root, verdict)
    assert card is None


def test_no_diagnosis_at_all_shows_nothing(root):
    from lgtv_easy import gui
    assert gui.make_diagnosis_banner(tk.Frame(root), None) is None


def test_only_a_cleared_pairing_offers_the_setup_button(root):
    """Re-running setup cannot move a PC onto the right network or plug in an
    Ethernet cable, so offering it there would be a button that does nothing."""
    from lgtv_easy import gui

    def buttons(verdict):
        diagnosis = selfheal.Diagnosis(verdict=verdict, summary="x", at=time.time())
        card = gui.make_diagnosis_banner(tk.Frame(root), diagnosis,
                                         on_repair=lambda: None,
                                         on_setup=lambda: None)
        found = []
        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, gui.ttk.Button):
                    found.append(str(child.cget("text")))
                walk(child)
        walk(card)
        return found

    assert "Re-run setup" in buttons(selfheal.VERDICT_PAIRING)
    assert "Re-run setup" not in buttons(selfheal.VERDICT_WRONG_NETWORK)
    # The repair button is offered whatever the fault - it is the cheapest thing
    # a user can try, and it is what re-checks after they have fixed the network.
    assert "Test and repair" in buttons(selfheal.VERDICT_WRONG_NETWORK)

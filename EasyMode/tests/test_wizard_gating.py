"""Step 1 of the wizard must not let anyone past it empty-handed.

Pressing "Next" with no TV found sent the user into a pairing step that could
only fail, and the failure surfaced two screens later where it explained
nothing. Worse, a half-filled config could be carried onward. So the button is
armed by exactly one thing: a TV that is actually in the list.

A typed-in address still works - it is the only way in on a network where
discovery cannot run - but it has to be *checked* first, which puts it in the
list like any other. Typing is a way in, not a way past.
"""
import pytest

from lgtv_easy.config import Config, Device
from lgtv_easy.discovery import Discovered

tk = pytest.importorskip("tkinter")


def _disabled(button):
    return "disabled" in button.state()


@pytest.fixture
def wizard(tmp_path, monkeypatch):
    """A real wizard whose scan finds nothing until a test says otherwise."""
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    monkeypatch.setenv("LGTV_EASY_NO_SELFTEST", "1")
    monkeypatch.setenv("LGTV_EASY_NO_SLEEP_WATCH", "1")
    from lgtv_easy import gui

    monkeypatch.setattr(gui, "discover_tvs", lambda *a, **k: [])
    try:
        app = gui.App()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    for _ in range(20):
        app.update_idletasks()
        app.update()
    panel = app.container.winfo_children()[0]
    assert isinstance(panel, gui.SetupWizard)
    yield app, panel
    try:
        app.on_close()
    except tk.TclError:
        pass


def _pump(app, n=30):
    for _ in range(n):
        app.update_idletasks()
        app.update()


def test_next_is_dead_until_a_tv_is_found(wizard):
    app, w = wizard
    _pump(app)
    assert _disabled(w.next_btn), "Next was live with an empty list"


def test_a_scan_that_finds_nothing_leaves_next_dead(wizard):
    app, w = wizard
    w._scan_done([])
    _pump(app)
    assert _disabled(w.next_btn)
    assert "No TV found" in w.scan_status.cget("text")


def test_finding_a_tv_arms_next_and_selects_it(wizard):
    app, w = wizard
    w._scan_done([Discovered(ip="127.0.0.1", name="LG B-series", is_lg=True)])
    _pump(app)
    assert not _disabled(w.next_btn)
    assert w.selected_ip.get() == "127.0.0.1"
    assert w.selected_name.get() == "LG B-series"


def test_typing_an_ip_alone_does_not_arm_next(wizard):
    """The hole this closes: an unverified address just moves the failure to
    the pairing screen."""
    app, w = wizard
    w.selected_ip.set("192.168.86.99")
    _pump(app)
    assert _disabled(w.next_btn)


def test_a_typed_ip_that_checks_out_joins_the_list_and_arms_next(wizard):
    app, w = wizard
    w._check_done("192.168.86.99", True)
    _pump(app)
    assert not _disabled(w.next_btn)
    assert [d.ip for d in w.found] == ["192.168.86.99"]
    assert w.listbox.size() == 1


def test_a_typed_ip_that_is_not_a_tv_is_refused(wizard):
    app, w = wizard
    w._check_done("192.168.86.99", False)
    _pump(app)
    assert _disabled(w.next_btn)
    assert w.found == []
    assert "answered as an LG TV" in w.scan_status.cget("text")


def test_checking_the_same_ip_twice_does_not_duplicate_it(wizard):
    app, w = wizard
    w._check_done("192.168.86.99", True)
    w._check_done("192.168.86.99", True)
    _pump(app)
    assert w.listbox.size() == 1
    assert len(w.found) == 1


def test_next_goes_dead_again_while_a_scan_is_running(wizard):
    """A stale selection from the previous scan must not stay clickable while
    the list underneath it is being rebuilt."""
    app, w = wizard
    w._scan_done([Discovered(ip="127.0.0.1", name="LG", is_lg=True)])
    _pump(app)
    assert not _disabled(w.next_btn)
    w._scan()
    _pump(app, 5)
    assert _disabled(w.next_btn)


def test_the_wizard_never_advances_on_an_empty_selection(wizard, monkeypatch):
    """Belt and braces: whatever route reaches _goto_pair, it must not build
    step 2 with nothing chosen."""
    app, w = wizard
    from lgtv_easy import gui

    warned = []
    monkeypatch.setattr(gui.messagebox, "showwarning",
                        lambda *a, **k: warned.append(a))
    built = []
    monkeypatch.setattr(w, "_build_step2", lambda: built.append(1))
    w.selected_ip.set("")
    w._goto_pair()
    assert built == [] and warned


def test_a_non_lg_device_is_not_offered_as_a_tv(wizard):
    """discover_tvs also reports the other UPnP gadgets that answered. Listing a
    printer as something to pair with is how a wizard talks somebody into the
    wrong choice - and this user has already been talked into one wrong TV."""
    app, w = wizard
    w._scan_done([Discovered(ip="192.168.86.30", name="Some Printer", is_lg=False)])
    _pump(app)
    assert w.found == []
    assert w.listbox.size() == 0
    assert _disabled(w.next_btn)
    assert "none of them an LG TV" in w.scan_status.cget("text")


def test_only_the_lg_devices_are_listed_when_both_answer(wizard):
    app, w = wizard
    w._scan_done([Discovered(ip="192.168.86.30", name="Printer", is_lg=False),
                  Discovered(ip="192.168.86.51", name="LG TV", is_lg=True)])
    _pump(app)
    assert [d.ip for d in w.found] == ["192.168.86.51"]
    assert w.listbox.size() == 1
    assert not _disabled(w.next_btn)
    assert w.selected_ip.get() == "192.168.86.51"

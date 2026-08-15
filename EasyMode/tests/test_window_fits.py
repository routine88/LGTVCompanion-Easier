"""The window has to fit on the screen it opens on.

The failure these cover: the panel is a stack of cards, and with the warning
banner or the deep power-off slider showing it is taller than a laptop display.
The window used to grow to the panel's full height *and pin that as its minimum
size*, so the last cards were drawn below the bottom of the screen with no way
to reach them - not by resizing, and not by scrolling.

So: fit the content where there is room, cap at the usable screen height where
there isn't, and scroll the remainder. These drive the real tkinter window and
skip cleanly where there is no display.

One window is built for the whole module and re-fitted against each pretend
screen size. That is not just for speed: Tk gets unreliable after a handful of
roots have been created and destroyed inside one process ("invalid command name
tcl_findLibrary"), and a test file that burns through them makes whichever GUI
test runs next fail for no reason of its own.
"""
import tempfile

import pytest

from lgtv_easy.config import Config, Device

tk = pytest.importorskip("tkinter")

SMALL_SCREEN = (620, 480)      # a netbook, or a big window on a small display
ROOMY_SCREEN = (1600, 1200)


@pytest.fixture(scope="module")
def app():
    """One real App over a throwaway config, or skip if there is no display."""
    import os
    home = tempfile.mkdtemp(prefix="lgtv-fit-")
    saved = {k: os.environ.get(k) for k in
             ("LGTV_EASY_HOME", "LGTV_EASY_NO_SELFTEST", "LGTV_EASY_NO_SLEEP_WATCH")}
    os.environ.update(LGTV_EASY_HOME=home, LGTV_EASY_NO_SELFTEST="1",
                      LGTV_EASY_NO_SLEEP_WATCH="1")

    cfg = Config(setup_complete=True, deep_off_enabled=True)
    cfg.device = Device(name="mock", ip="127.0.0.1", key="k")
    cfg.save()

    from lgtv_easy import gui
    try:
        window = gui.App()
    except tk.TclError as exc:                     # no display
        pytest.skip(f"no display: {exc}")
    yield window
    try:
        window.on_close()
    except tk.TclError:
        pass
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def _restore_screen():
    """Undo any pretend screen size a test installed."""
    from lgtv_easy import gui
    real = gui.usable_screen
    yield
    gui.usable_screen = real


def pump(window, times=8):
    for _ in range(times):
        window.update_idletasks()
        window.update()


def fit_to(window, screen):
    """Re-lay the window out as if the display were ``screen`` pixels."""
    from lgtv_easy import gui
    gui.usable_screen = lambda _w: screen
    window.show_settings()          # rebuild the panel, then re-fit
    pump(window)
    return window


def geometry(window):
    width, height = window.geometry().split("+")[0].split("x")
    return int(width), int(height)


# ----- the regression ---------------------------------------------------
def test_the_window_never_opens_taller_than_the_screen(app):
    fit_to(app, SMALL_SCREEN)
    _, height = geometry(app)
    assert height <= SMALL_SCREEN[1], (
        f"window is {height}px tall on a {SMALL_SCREEN[1]}px screen")


def test_it_can_still_be_shrunk_on_a_small_screen(app):
    """minsize used to be pinned to the content height, which is what made the
    clipped window impossible to fix by dragging it."""
    fit_to(app, SMALL_SCREEN)
    min_w, min_h = app.minsize()
    assert min_h <= SMALL_SCREEN[1]
    assert min_w <= SMALL_SCREEN[0]


def test_content_that_does_not_fit_gets_a_scrollbar(app):
    fit_to(app, SMALL_SCREEN)
    assert app.scroll._bar_shown, "no scrollbar, and the cards below are lost"
    first, last = app.scroll.canvas.yview()
    assert (last - first) < 1.0, "nothing to scroll, so nothing was cut off?"


def test_scrolling_reaches_the_bottom_of_the_panel(app):
    fit_to(app, SMALL_SCREEN)
    app.scroll.canvas.yview_moveto(1.0)
    pump(app, 2)
    _, last = app.scroll.canvas.yview()
    assert last == pytest.approx(1.0, abs=0.01), "cannot scroll to the last card"


# ----- and it stays out of the way when there is room -------------------
def test_a_roomy_screen_gets_no_scrollbar(app):
    """Given room for everything, the scrollbar stays out of the way.

    Skipped on a genuinely small display. Pretending the screen is 1200px tall
    does not make it so: the window manager still clamps the window to the real
    desktop, so on a 768px CI runner the viewport is short whatever we claim and
    the bar is right to appear.
    """
    from lgtv_easy import gui
    real_height = gui.usable_screen(app)[1]        # before fit_to patches it
    fit_to(app, ROOMY_SCREEN)
    if app.scroll.inner.winfo_reqheight() > real_height:
        pytest.skip(f"display is only {real_height}px tall; the panel cannot fit")
    assert not app.scroll._bar_shown, "scrollbar shown when everything fits"


def test_the_whole_panel_is_visible_when_it_fits(app):
    fit_to(app, ROOMY_SCREEN)
    viewport = app.scroll.canvas.winfo_reqheight()
    content = app.scroll.inner.winfo_reqheight()
    assert viewport >= content, "the viewport should ask for all of the content"


def test_switching_panels_returns_to_the_top(app):
    fit_to(app, SMALL_SCREEN)
    app.scroll.canvas.yview_moveto(1.0)
    app.show_wizard()
    pump(app)
    assert app.scroll.canvas.yview()[0] == pytest.approx(0.0, abs=0.001)
    app.show_settings()             # leave the shared window as we found it
    pump(app)


# ----- the wheel --------------------------------------------------------
def _pointer_over(window):
    """A point inside the window, or None if it is not mapped where the
    pointer-based checks can see it."""
    x = window.winfo_rootx() + window.winfo_width() // 3
    y = window.winfo_rooty() + window.winfo_height() // 2
    return (x, y) if window.winfo_containing(x, y) is not None else None


def test_the_wheel_scrolls_the_page(app):
    """A scrollbar nobody can drive with the wheel is only half a fix."""
    fit_to(app, SMALL_SCREEN)
    point = _pointer_over(app)
    if point is None:
        pytest.skip("window is not mapped where a pointer test can reach it")
    x, y = point

    top = app.scroll.canvas.yview()[0]
    app.event_generate("<MouseWheel>", delta=-120, rootx=x, rooty=y, x=20, y=5)
    pump(app, 4)
    scrolled = app.scroll.canvas.yview()[0]
    assert scrolled > top, "wheel down did not move the page"

    app.event_generate("<MouseWheel>", delta=120, rootx=x, rooty=y, x=20, y=5)
    pump(app, 4)
    assert app.scroll.canvas.yview()[0] < scrolled, "wheel up did not come back"


def test_the_wheel_leaves_a_scrolling_log_alone(app):
    """Rolling the wheel over the diagnostics log should move that log, not the
    page underneath it."""
    from lgtv_easy import gui
    gui.usable_screen = lambda _w: SMALL_SCREEN
    app.show_wizard()
    pump(app)
    try:
        wizard = app.container.winfo_children()[0]
        logs = [child for frame in wizard.winfo_children()
                if isinstance(frame, gui.ttk.Frame)
                for child in frame.winfo_children()
                if isinstance(child, tk.Text)]
        if not logs:
            pytest.skip("no diagnostics log on this screen")
        text = logs[0]
        x, y = text.winfo_rootx() + 10, text.winfo_rooty() + 10
        if app.winfo_containing(x, y) is None:
            pytest.skip("window is not mapped where the pointer test can see it")

        event = type("Event", (), {"x_root": x, "y_root": y, "delta": -120})()
        assert app.scroll._owns_wheel(event) is False
    finally:
        app.show_settings()
        pump(app)


# ----- the helper the sizing rests on -----------------------------------
def test_usable_screen_stays_inside_the_display(app):
    from lgtv_easy import gui
    width, height = gui.usable_screen(app)      # the real screen, not a pretend one
    assert 0 < width <= app.winfo_screenwidth()
    assert 0 < height <= app.winfo_screenheight()

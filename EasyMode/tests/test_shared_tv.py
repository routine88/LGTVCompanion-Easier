"""Two PCs on one TV: the idle one must not blank the one that's on screen.

The bug this covers: both machines run Easy Mode, and the TV can only show one
of them. Whichever PC went idle first used to blank the panel - even while the
other computer was up on screen and being used. Everything here drives a mock TV
whose current source the test can flip, exactly as a person would with the
remote.
"""
import logging

from lgtv_easy.config import Config, Device
from lgtv_easy.daemon import (INPUT_RELEARN_SECONDS, STATE_OFF, STATE_ON,
                              STATE_STANDBY, Daemon)
from lgtv_easy.mock_tv import MockTV
from lgtv_easy.webos import WebOSClient, is_external_input, normalize_input_id


def _quiet_logger():
    lg = logging.getLogger("test-shared-tv")
    lg.addHandler(logging.NullHandler())
    return lg


def _make(tv: MockTV, cfg: Config) -> Daemon:
    """A daemon wired to the mock TV, with idle time and the clock under test
    control so the relearn window can be crossed without waiting it out."""
    def factory():
        c = WebOSClient("127.0.0.1")
        c._url = lambda: tv.url
        return c

    idle_box = {"v": 0.0}
    clock_box = {"v": 1000.0}
    d = Daemon(cfg, client_factory=factory,
               idle_fn=lambda: idle_box["v"],
               clock_fn=lambda: clock_box["v"],
               locator_fn=lambda mac: None,
               wol_fn=lambda deep: None,
               logger=_quiet_logger())
    d._idle_box = idle_box
    d._clock_box = clock_box
    return d


def _cfg(minutes=7.0, **kw) -> Config:
    cfg = Config(idle_minutes=minutes, **kw)
    cfg.device = Device(name="t", ip="127.0.0.1", key="MOCK-KEY-0001")
    return cfg


def _advance(d: Daemon, seconds: float) -> None:
    """Move the clock on, past the input-sample throttle."""
    d._clock_box["v"] += seconds


# ----- learning which input this PC is on ------------------------------
def test_learns_its_own_input_from_the_tv_while_the_user_is_here():
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv, _cfg())
        d._idle_box["v"] = 0  # user at the keyboard
        d.tick()
        assert d.config.device.input_id == "hdmi2"


def test_never_adopts_a_tv_app_as_this_pc():
    """Netflix is not a computer; adopting it would make the guard meaningless."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "netflix"
        d = _make(tv, _cfg())
        d._idle_box["v"] = 0
        d.tick()
        assert d.config.device.input_id == ""


# ----- the actual bug --------------------------------------------------
def test_idle_pc_does_not_blank_the_tv_showing_the_other_pc():
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"  # this PC
        d = _make(tv, _cfg(minutes=7.0))
        d._idle_box["v"] = 0
        d.tick()
        assert d.config.device.input_id == "hdmi2"

        # The user switches the TV over to the other computer and walks away
        # from this one.
        tv.foreground_app = "com.webos.app.hdmi1"
        d._idle_box["v"] = 8 * 60
        _advance(d, 60)
        d.tick()

        assert tv.screen_on is True, "blanked the TV while the other PC was on it"
        assert d.screen_state == STATE_ON
        assert d.sleeps == 0


def test_blanks_normally_once_the_tv_is_back_on_this_pc():
    """Back on this input, the timeout applies again as usual - but measured
    from the moment it came back on screen, not from the last keypress. Time
    spent behind the other computer is not held against it."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv, _cfg(minutes=7.0))
        d._idle_box["v"] = 0
        d.tick()

        tv.foreground_app = "com.webos.app.hdmi1"
        d._idle_box["v"] = 8 * 60
        _advance(d, 60)
        d.tick()
        assert tv.screen_on is True

        tv.foreground_app = "com.webos.app.hdmi2"  # switched back to this PC
        _advance(d, 60)
        d.tick()
        assert tv.screen_on is True, "a minute back on screen is inside the 7"

        _advance(d, 7 * 60)                        # now the timeout really has run
        d._idle_box["v"] += 7 * 60
        d.tick()
        assert tv.screen_on is False
        assert d.screen_state == STATE_OFF


def test_deep_power_off_leaves_the_other_pc_alone():
    """Worse than blanking: cutting the whole TV while someone else is on it."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        cfg = _cfg(minutes=1.0, deep_off_enabled=True, deep_off_minutes=2.0)
        cfg.device.mac = "AA:BB:CC:DD:EE:FF"
        d = _make(tv, cfg)
        d._idle_box["v"] = 0
        d.tick()
        d._idle_box["v"] = 90  # past the screen-off stage
        _advance(d, 60)
        d.tick()
        assert d.screen_state == STATE_OFF

        # Someone wakes the TV with the remote onto the other computer while
        # this PC keeps counting up towards its full power-off.
        tv.foreground_app = "com.webos.app.hdmi1"
        d._idle_box["v"] = 300
        _advance(d, 60)
        d.tick()

        assert tv.powered_on is True, "powered the TV off under the other PC"
        assert d.screen_state != STATE_STANDBY
        assert d.deep_offs == 0


def test_pc_sleep_does_not_blank_the_other_pc():
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv, _cfg())
        d._idle_box["v"] = 0
        d.tick()

        tv.foreground_app = "com.webos.app.hdmi1"
        _advance(d, 60)
        d.tick()  # refresh what's on screen
        d._on_system_sleep()

        assert tv.screen_on is True
        assert d.sleeps == 0


# ----- not regressing the ordinary single-PC setup ---------------------
def test_single_pc_setup_is_unaffected():
    """One computer, one TV: the input matches, so nothing changes."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi1"
        d = _make(tv, _cfg(minutes=7.0))
        d._idle_box["v"] = 0
        d.tick()
        d._idle_box["v"] = 8 * 60
        _advance(d, 60)
        d.tick()
        assert tv.screen_on is False
        assert d.sleeps == 1


def test_a_tv_that_wont_say_still_gets_blanked():
    """Older firmware answers with no appId. Unknown must mean "carry on", not
    "never touch the TV again"."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv, _cfg(minutes=7.0))
        d._idle_box["v"] = 0
        d.tick()
        assert d.config.device.input_id == "hdmi2"

        tv.foreground_app = ""  # panel stops reporting
        d._idle_box["v"] = 8 * 60
        _advance(d, 60)
        d.tick()
        assert tv.screen_on is False


def test_guard_can_be_turned_off():
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        cfg = _cfg(minutes=7.0)
        cfg.device.input_id = "hdmi2"
        cfg.only_my_input = False
        d = _make(tv, cfg)
        tv.foreground_app = "com.webos.app.hdmi1"
        d._idle_box["v"] = 8 * 60
        d.tick()
        assert tv.screen_on is False


# ----- following a cable moved to a different socket -------------------
def test_a_rival_input_does_not_steal_ownership_straight_away():
    """Working at this PC for a while with the other machine on screen (a second
    monitor on the desk) must not hand it the panel."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv, _cfg())
        d._idle_box["v"] = 0
        d.tick()
        assert d.config.device.input_id == "hdmi2"

        tv.foreground_app = "com.webos.app.hdmi1"
        for _ in range(5):
            _advance(d, 60)
            d.tick()
        assert d.config.device.input_id == "hdmi2"


def test_follows_the_pc_to_a_new_socket_after_the_relearn_window():
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv, _cfg())
        d._idle_box["v"] = 0
        d.tick()

        tv.foreground_app = "com.webos.app.hdmi3"  # replugged
        _advance(d, 60)
        d.tick()  # starts the clock on the new candidate
        _advance(d, INPUT_RELEARN_SECONDS + 60)
        d.tick()
        assert d.config.device.input_id == "hdmi3"


# ----- coming back to this input ---------------------------------------
def test_switching_back_restarts_the_countdown_instead_of_blanking():
    """The reported bug: switch the TV back to this PC and its desktop shows for
    a second or two, then goes black. It never entered the off state (the guard
    kept refusing), so it sat at ON with a long-expired timer and fired the
    instant the guard let go - and the OS idle timer can't know the user just
    pressed the remote rather than this keyboard."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv, _cfg(minutes=1.0))
        d._idle_box["v"] = 0
        d.tick()
        assert d.config.device.input_id == "hdmi2"

        # Off to the other computer; this one idles far past its timeout.
        tv.foreground_app = "com.webos.app.hdmi4"
        d._idle_box["v"] = 10 * 60
        for _ in range(5):
            _advance(d, 60)
            d.tick()
        assert tv.screen_on is True

        # Back to this PC. The user hasn't touched this keyboard, so the OS
        # still reports ten minutes idle - but they are plainly here.
        tv.foreground_app = "com.webos.app.hdmi2"
        _advance(d, 2)
        d.tick()
        assert tv.screen_on is True, "blanked the screen just after switching to it"
        assert d.sleeps == 0


def test_the_restarted_countdown_still_expires():
    """Restarting the timer must not mean never blanking: switch back, walk away
    without touching anything, and it should still go dark on schedule."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv, _cfg(minutes=1.0))
        d._idle_box["v"] = 0
        d.tick()

        tv.foreground_app = "com.webos.app.hdmi4"
        d._idle_box["v"] = 10 * 60
        _advance(d, 300)
        d.tick()

        tv.foreground_app = "com.webos.app.hdmi2"   # switched back
        _advance(d, 2)
        d.tick()
        assert tv.screen_on is True

        _advance(d, 70)          # a full minute on screen, still untouched
        d._idle_box["v"] += 70
        d.tick()
        assert tv.screen_on is False, "the restarted countdown never expired"
        assert d.sleeps == 1


def test_a_stale_reading_still_holds_the_screen():
    """The sample is throttled, so the switch can be noticed late. Because the
    baseline is pinned while hidden rather than on the switch itself, the last
    thing known is 'not on screen' and blanking is held off regardless."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv, _cfg(minutes=1.0))
        d._idle_box["v"] = 0
        d.tick()

        tv.foreground_app = "com.webos.app.hdmi4"
        d._idle_box["v"] = 10 * 60
        _advance(d, 120)
        d.tick()

        # Switch back and tick immediately, before any new sample is due.
        tv.foreground_app = "com.webos.app.hdmi2"
        _advance(d, 1)
        d.tick()
        assert tv.screen_on is True


def test_being_switched_to_brings_a_blanked_screen_back():
    """If this PC had blanked its own screen before the user went away, coming
    back to its input should light it up, not leave them at a black panel."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv, _cfg(minutes=1.0))
        d._idle_box["v"] = 0
        d.tick()
        d._idle_box["v"] = 120           # blanks while it is the one on screen
        _advance(d, 60)
        d.tick()
        assert d.screen_state == STATE_OFF

        tv.foreground_app = "com.webos.app.hdmi4"
        _advance(d, 60)
        d.tick()

        tv.foreground_app = "com.webos.app.hdmi2"
        _advance(d, 2)
        d.tick()
        assert tv.screen_on is True
        assert d.screen_state == STATE_ON
        assert d.wakes == 1


def test_time_behind_another_input_is_not_held_against_a_single_pc_setup():
    """With the guard off, nothing is capped - the plain OS idle timer rules."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        cfg = _cfg(minutes=1.0)
        cfg.device.input_id = "hdmi2"
        cfg.only_my_input = False
        d = _make(tv, cfg)
        d._visible_since = 1000.0        # stale leftover from before it was off
        d._idle_box["v"] = 120
        d.tick()
        assert tv.screen_on is False


# ----- both machines running at once -----------------------------------
def test_two_pcs_one_tv_the_idle_one_keeps_its_hands_off():
    """The reported bug, end to end: two computers on one TV, both running Easy
    Mode. Work on each in turn, then leave one idle while the other is in use."""
    with MockTV(require_pairing=False) as tv:
        pc_a = _make(tv, _cfg(minutes=5.0))
        pc_b = _make(tv, _cfg(minutes=5.0))

        # Each machine gets used while it is the one on screen, which is how it
        # finds out which socket it is on.
        tv.foreground_app = "com.webos.app.hdmi1"
        pc_a._idle_box["v"] = 0
        pc_a.tick()
        tv.foreground_app = "com.webos.app.hdmi2"
        pc_b._idle_box["v"] = 0
        pc_b.tick()
        assert pc_a.config.device.input_id == "hdmi1"
        assert pc_b.config.device.input_id == "hdmi2"

        # Now the user settles in on PC B. PC A sits there, awake but untouched,
        # counting past its timeout - and must not darken the panel.
        pc_a._idle_box["v"] = 30 * 60
        pc_b._idle_box["v"] = 0
        for _ in range(6):
            _advance(pc_a, 60)
            _advance(pc_b, 60)
            pc_a.tick()
            pc_b.tick()

        assert tv.screen_on is True, "the idle PC blanked the TV under the other"
        assert tv.powered_on is True
        assert pc_a.sleeps == 0
        assert pc_b.screen_state == STATE_ON

        # And when the user does leave PC B, the machine that IS on screen still
        # does its job.
        pc_b._idle_box["v"] = 30 * 60
        _advance(pc_a, 60)
        _advance(pc_b, 60)
        pc_a.tick()
        pc_b.tick()
        assert tv.screen_on is False
        assert pc_b.sleeps == 1
        assert pc_a.sleeps == 0


# ----- id normalisation ------------------------------------------------
def test_input_ids_are_normalised():
    assert normalize_input_id("com.webos.app.hdmi2") == "hdmi2"
    assert normalize_input_id("com.webos.app.HDMI2") == "hdmi2"
    assert normalize_input_id("netflix") == "netflix"
    assert normalize_input_id("") == ""
    assert normalize_input_id(None) == ""


def test_only_physical_sockets_count_as_a_pc():
    assert is_external_input("hdmi1") is True
    assert is_external_input("externalinput.av1") is True
    assert is_external_input("netflix") is False
    assert is_external_input("livetv") is False
    assert is_external_input("") is False

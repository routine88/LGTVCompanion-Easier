"""The TV must not go dark in the middle of a film.

Idle-at-the-keyboard is not the same as nobody-is-watching: two hours of a
motionless mouse is exactly what watching a film looks like to the OS. These
cover the hold that fixes it - the screen-off and power-off countdowns are held
while something is playing on this PC, and restart from the end of playback
rather than firing the instant the credits roll.
"""
import logging

from lgtv_easy.config import Config, Device
from lgtv_easy.daemon import STATE_OFF, STATE_ON, STATE_STANDBY, Daemon
from lgtv_easy.mock_tv import MockTV
from lgtv_easy.webos import WebOSClient


def _quiet_logger(records=None):
    lg = logging.getLogger(f"test-media-{id(records)}")
    lg.handlers.clear()
    lg.setLevel(logging.INFO)
    if records is not None:
        class _Collect(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())
        lg.addHandler(_Collect())
    else:
        lg.addHandler(logging.NullHandler())
    lg.propagate = False
    return lg


def _cfg(minutes=1.0, **kw) -> Config:
    # only_my_input off: this file is about the media hold, and the input guard
    # would otherwise add TV round trips to every tick.
    cfg = Config(idle_minutes=minutes, only_my_input=False, **kw)
    cfg.device = Device(name="t", ip="127.0.0.1", key="MOCK-KEY-0001")
    return cfg


def _make(tv: MockTV, cfg: Config, records=None) -> Daemon:
    """A daemon on a fake clock, with idle and 'is anything playing' driven by
    the test rather than the machine it runs on."""
    def factory():
        c = WebOSClient("127.0.0.1")
        c._url = lambda: tv.url
        return c

    state = {"idle": 0.0, "clock": 0.0, "playing": False}
    d = Daemon(cfg, client_factory=factory,
               idle_fn=lambda: state["idle"],
               media_fn=lambda: state["playing"],
               media_detail_fn=lambda: "Test Player: Playing video",
               clock_fn=lambda: state["clock"],
               locator_fn=lambda mac: None,
               wol_fn=lambda deep: None,
               logger=_quiet_logger(records))
    d.state = state
    return d


def _at(d: Daemon, clock: float, idle: float = None, playing: bool = None):
    """Move the clock (and optionally idle/playback) and run one tick."""
    d.state["clock"] = clock
    if idle is not None:
        d.state["idle"] = idle
    if playing is not None:
        d.state["playing"] = playing
    d.tick()


def test_screen_stays_on_while_something_is_playing():
    with MockTV(require_pairing=False) as tv:
        d = _make(tv, _cfg(minutes=1.0))
        _at(d, 0, idle=0, playing=True)
        assert d.screen_state == STATE_ON

        # Two hours of a motionless mouse: a film, not an empty desk.
        _at(d, 7200, idle=7200)
        assert d.screen_state == STATE_ON
        assert tv.screen_on is True
        assert d.sleeps == 0


def test_the_timer_restarts_when_playback_stops():
    with MockTV(require_pairing=False) as tv:
        d = _make(tv, _cfg(minutes=1.0))          # 60s threshold
        _at(d, 0, idle=0, playing=True)
        _at(d, 600, idle=600)                     # ten minutes into the film
        assert d.screen_state == STATE_ON

        _at(d, 605, idle=605, playing=False)      # the film ends
        assert d.screen_state == STATE_ON, "must not blank the moment it ends"

        _at(d, 650, idle=650)                     # 45s later - still inside 60s
        assert d.screen_state == STATE_ON

        _at(d, 670, idle=670)                     # 65s after playback stopped
        assert d.screen_state == STATE_OFF
        assert d.sleeps == 1


def test_full_power_off_is_held_too():
    cfg = _cfg(minutes=1.0, deep_off_enabled=True, deep_off_minutes=2.0)
    cfg.device.mac = "aa:bb:cc:dd:ee:ff"          # deep-off needs a WOL target
    with MockTV(require_pairing=False) as tv:
        d = _make(tv, cfg)
        _at(d, 0, idle=300, playing=False)        # nothing playing: blanks
        assert d.screen_state == STATE_OFF

        # Now something starts playing (music through the TV's speakers, say).
        # Cutting the TV's power would cut that off, so it must not happen.
        _at(d, 10, idle=310, playing=True)
        _at(d, 3600, idle=3910)
        assert d.screen_state == STATE_OFF
        assert d.deep_offs == 0

        _at(d, 3605, idle=3915, playing=False)
        _at(d, 3730, idle=4040)                   # 125s after playback stopped
        assert d.screen_state == STATE_STANDBY
        assert d.deep_offs == 1


def test_playing_does_not_wake_a_screen_that_is_already_off():
    """A video that starts on its own must not light an empty room.

    The hold stops the TV going dark; it deliberately does not turn it back on.
    Waking stays keyed to real input, exactly as before.
    """
    with MockTV(require_pairing=False) as tv:
        d = _make(tv, _cfg(minutes=1.0))
        _at(d, 0, idle=300, playing=False)
        assert d.screen_state == STATE_OFF

        _at(d, 10, idle=310, playing=True)
        assert d.screen_state == STATE_OFF
        assert d.wakes == 0

        _at(d, 20, idle=0)                        # somebody touches the keyboard
        assert d.screen_state == STATE_ON
        assert d.wakes == 1


def test_the_setting_can_be_turned_off():
    with MockTV(require_pairing=False) as tv:
        d = _make(tv, _cfg(minutes=1.0, stay_on_while_playing=False))
        _at(d, 0, idle=0, playing=True)
        _at(d, 300, idle=300)
        assert d.screen_state == STATE_OFF, "the hold must be opt-out-able"


def test_nothing_changes_when_nothing_ever_plays():
    with MockTV(require_pairing=False) as tv:
        d = _make(tv, _cfg(minutes=1.0))
        _at(d, 0, idle=0, playing=False)
        _at(d, 61, idle=61)
        assert d.screen_state == STATE_OFF
        assert d.sleeps == 1


def test_a_broken_detector_never_breaks_the_daemon():
    """Detection is best-effort: if the session bus explodes, the timers must
    behave exactly as they did before this feature existed."""
    with MockTV(require_pairing=False) as tv:
        d = _make(tv, _cfg(minutes=1.0))

        def boom():
            raise RuntimeError("no session bus")

        d._media_fn = boom
        _at(d, 0, idle=0)
        _at(d, 61, idle=61)
        assert d.screen_state == STATE_OFF


def test_start_and_stop_of_playback_are_logged_once_each():
    """A held screen has to be explainable. Without a log line, 'the TV stopped
    blanking' and 'the app is broken' look identical - and a stuck inhibitor
    would hold the screen on forever with nothing to point at."""
    records = []
    with MockTV(require_pairing=False) as tv:
        d = _make(tv, _cfg(minutes=1.0), records=records)
        for t in (0, 10, 20, 30):
            _at(d, t, idle=t, playing=True)
        started = [r for r in records if "is playing on this PC" in r]
        assert len(started) == 1, records
        assert "Test Player: Playing video" in started[0]

        for t in (40, 50, 60):
            _at(d, t, idle=t, playing=False)
        stopped = [r for r in records if "Nothing is playing any more" in r]
        assert len(stopped) == 1, records

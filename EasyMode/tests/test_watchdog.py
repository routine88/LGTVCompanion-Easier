"""The watcher noticing it is broken, and saying so.

This exists because of a real outage: after a power cut the TV never came back,
and the watcher retried a dead address every five minutes for two days. It logged
one line - "retrying quietly" - and then nothing, so from the outside the app
simply did not work and there was no way to find out why. Nothing here tests the
retrying; it tests the *escalation* that was missing, and the rule that keeps it
bearable: a TV that is merely switched off must never interrupt anybody.
"""
import time

import pytest

from lgtv_easy import selfheal
from lgtv_easy.config import Config, Device
from lgtv_easy.daemon import DIAGNOSE_AFTER_SECONDS, Daemon


def _quiet_logger():
    """The watchdog logs a warning per check; the suite's output is not the
    place for them."""
    import logging

    logger = logging.getLogger("lgtv-easy-test-watchdog")
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _daemon(monkeypatch, verdict, *, has_ui=False):
    """A daemon whose self-check returns ``verdict`` without touching a network."""
    cfg = Config()
    cfg.device = Device(name="t", ip="192.0.2.10", mac="AA:BB:CC:DD:EE:FF",
                        key="k")
    clock = _Clock()
    daemon = Daemon(cfg, clock_fn=clock, locator_fn=lambda mac: None,
                    logger=_quiet_logger())
    daemon.has_ui = has_ui
    daemon._clock_obj = clock

    diagnosis = selfheal.Diagnosis(verdict=verdict, summary=f"summary for {verdict}",
                                   at=time.time())
    monkeypatch.setattr(selfheal, "diagnose", lambda *a, **k: diagnosis)
    return daemon, clock, diagnosis


@pytest.fixture(autouse=True)
def no_real_escalation(monkeypatch, tmp_path):
    """Never notify, never open a window, never write outside the tmp dir."""
    sent = []
    from lgtv_easy import notify
    monkeypatch.setattr(notify, "notify",
                        lambda title, message, **kw: sent.append((title, message)) or True)
    monkeypatch.setenv("LGTV_EASY_NO_AUTO_SETUP", "1")
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    return sent


# ----- when the check runs at all --------------------------------------------
def test_a_brief_blip_is_not_investigated(monkeypatch):
    """A TV reboots, a Wi-Fi client roams. Sweeping the LAN over a ten-second
    gap would be all cost and no information."""
    daemon, clock, _ = _daemon(monkeypatch, selfheal.VERDICT_TV_OFF)
    daemon._note_connect_failure(OSError("no route"))
    clock.advance(DIAGNOSE_AFTER_SECONDS / 2)
    daemon._maybe_diagnose()
    assert daemon.diagnoses == 0


def test_a_sustained_outage_is_investigated(monkeypatch):
    daemon, clock, _ = _daemon(monkeypatch, selfheal.VERDICT_TV_OFF)
    daemon._note_connect_failure(OSError("no route"))
    clock.advance(DIAGNOSE_AFTER_SECONDS + 1)
    daemon._diagnose_now()          # what _maybe_diagnose runs on its thread
    assert daemon.diagnoses == 1
    assert daemon.last_diagnosis.verdict == selfheal.VERDICT_TV_OFF


def test_the_outage_clock_starts_at_the_first_failure_not_the_last(monkeypatch):
    """Backoff means later failures arrive minutes apart; timing the outage from
    the most recent one would push the check further away with every retry - the
    check would never run at all."""
    daemon, clock, _ = _daemon(monkeypatch, selfheal.VERDICT_TV_OFF)
    daemon._note_connect_failure(OSError("no route"))
    began = daemon._failing_since
    clock.advance(60)
    daemon._note_connect_failure(OSError("no route"))
    assert daemon._failing_since == began


def test_reconnecting_stands_the_watchdog_down(monkeypatch):
    daemon, clock, _ = _daemon(monkeypatch, selfheal.VERDICT_PAIRING)
    daemon._note_connect_failure(OSError("no route"))
    clock.advance(DIAGNOSE_AFTER_SECONDS + 1)
    daemon._diagnose_now()
    assert daemon.last_diagnosis is not None
    daemon._end_outage()
    assert daemon._failing_since is None
    assert daemon.last_diagnosis is None
    # ...and the recorded verdict goes with it, so the next window opened does
    # not report a problem that has already gone away.
    assert selfheal.load_diagnosis() is None


# ----- what it does about the answer -----------------------------------------
def test_a_tv_that_is_merely_off_never_interrupts_anyone(no_real_escalation,
                                                         monkeypatch):
    """The normal overnight state. Waking someone for it is how an app teaches
    people to ignore it."""
    daemon, clock, _ = _daemon(monkeypatch, selfheal.VERDICT_TV_OFF)
    daemon._note_connect_failure(OSError("no route"))
    clock.advance(DIAGNOSE_AFTER_SECONDS + 1)
    daemon._diagnose_now()
    assert no_real_escalation == []


@pytest.mark.parametrize("verdict", list(selfheal.NEEDS_USER))
def test_a_fault_that_cannot_fix_itself_notifies(no_real_escalation, monkeypatch,
                                                 verdict):
    daemon, clock, _ = _daemon(monkeypatch, verdict)
    daemon._note_connect_failure(OSError("no route"))
    clock.advance(DIAGNOSE_AFTER_SECONDS + 1)
    daemon._diagnose_now()
    assert len(no_real_escalation) == 1
    assert no_real_escalation[0][1] == f"summary for {verdict}"


def test_the_user_is_interrupted_once_per_outage_not_once_per_check(
        no_real_escalation, monkeypatch):
    """The check repeats while the outage lasts. Repeating the notification with
    it would put the same red box on screen every half hour, all night."""
    daemon, clock, _ = _daemon(monkeypatch, selfheal.VERDICT_PAIRING)
    daemon._note_connect_failure(OSError("no route"))
    clock.advance(DIAGNOSE_AFTER_SECONDS + 1)
    daemon._diagnose_now()
    daemon._diagnose_now()
    daemon._diagnose_now()
    assert len(no_real_escalation) == 1
    assert daemon.diagnoses == 3


def test_a_new_outage_may_interrupt_again(no_real_escalation, monkeypatch):
    daemon, clock, _ = _daemon(monkeypatch, selfheal.VERDICT_PAIRING)
    daemon._note_connect_failure(OSError("no route"))
    clock.advance(DIAGNOSE_AFTER_SECONDS + 1)
    daemon._diagnose_now()
    daemon._end_outage()                       # TV came back
    daemon._note_connect_failure(OSError("no route"))   # ...and went again
    clock.advance(DIAGNOSE_AFTER_SECONDS + 1)
    daemon._diagnose_now()
    assert len(no_real_escalation) == 2


def test_a_self_check_that_fixes_it_retries_at_once(monkeypatch):
    """The check relocates the TV and persists the new address. Leaving the
    inherited five-minute backoff in place would mean the app sat there, fixed,
    doing nothing, for another five minutes."""
    daemon, clock, _ = _daemon(monkeypatch, selfheal.VERDICT_RECOVERED)
    daemon._note_connect_failure(OSError("no route"))
    assert daemon._next_connect_at != 0.0      # a backoff is in force
    clock.advance(DIAGNOSE_AFTER_SECONDS + 1)
    daemon._diagnose_now()
    assert daemon._connect_failures == 0
    assert daemon._next_connect_at == 0.0
    assert daemon._failing_since is None


def test_a_window_that_is_already_open_is_not_opened_again(monkeypatch):
    """The GUI runs its own watcher in-process. Spawning a second copy of the
    app at somebody already looking at the first one is not a fix.

    The guard sits in _open_setup rather than in the caller on purpose: the
    notification must still go out, because a window that is open behind six
    others is not the same as being told.
    """
    daemon, _clock, _diag = _daemon(monkeypatch, selfheal.VERDICT_PAIRING,
                                    has_ui=True)
    spawned = []
    monkeypatch.setattr("lgtv_easy.proc.popen", lambda *a, **k: spawned.append(a))
    monkeypatch.delenv("LGTV_EASY_NO_AUTO_SETUP", raising=False)
    daemon._open_setup()
    assert spawned == []
    # ...and with no window open, the same call does spawn one.
    daemon.has_ui = False
    monkeypatch.setenv("DISPLAY", ":0")
    daemon._open_setup()
    assert len(spawned) == 1


def test_only_a_cleared_pairing_opens_the_setup_window(monkeypatch,
                                                       no_real_escalation):
    """A TV on another network, or a PC with no network, is not something the
    setup wizard can do anything about - notifying is the whole response."""
    opened = []
    for verdict in selfheal.NEEDS_USER:
        daemon, _clock, diagnosis = _daemon(monkeypatch, verdict)
        monkeypatch.setattr(daemon, "_open_setup",
                            lambda v=verdict: opened.append(v))
        daemon._escalate(diagnosis)
    assert opened == [selfheal.VERDICT_PAIRING]


# ----- the verdict outlives the process --------------------------------------
def test_the_verdict_is_written_down_for_the_next_window(monkeypatch):
    """The watcher reaches its conclusion with no window open; the user's next
    move is to open one. The file is how the conclusion survives that gap."""
    daemon, clock, _ = _daemon(monkeypatch, selfheal.VERDICT_PAIRING)
    daemon._note_connect_failure(OSError("no route"))
    clock.advance(DIAGNOSE_AFTER_SECONDS + 1)
    daemon._diagnose_now()
    stored = selfheal.load_diagnosis()
    assert stored is not None
    assert stored.verdict == selfheal.VERDICT_PAIRING
    assert stored.needs_user is True


def test_an_unreadable_verdict_file_is_just_no_news(tmp_path, monkeypatch):
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    (tmp_path / selfheal.DIAGNOSIS_FILE).write_text("{ not json", encoding="utf-8")
    assert selfheal.load_diagnosis() is None

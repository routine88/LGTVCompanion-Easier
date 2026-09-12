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


def _daemon(monkeypatch, verdict):
    """A daemon whose self-check returns ``verdict`` without touching a network."""
    cfg = Config()
    cfg.device = Device(name="t", ip="192.0.2.10", mac="AA:BB:CC:DD:EE:FF",
                        key="k")
    clock = _Clock()
    daemon = Daemon(cfg, clock_fn=clock, locator_fn=lambda mac: None,
                    logger=_quiet_logger())
    daemon._clock_obj = clock

    diagnosis = selfheal.Diagnosis(verdict=verdict, summary=f"summary for {verdict}",
                                   at=time.time())
    monkeypatch.setattr(selfheal, "diagnose", lambda *a, **k: diagnosis)
    return daemon, clock, diagnosis


@pytest.fixture(autouse=True)
def interruptions(monkeypatch, tmp_path):
    """Catch anything the watcher tries to put in front of the user.

    The app used to raise a desktop notification and, for a cleared pairing,
    open the setup window on its own. Both were removed: they fired on a verdict
    that was sometimes wrong, and an interruption that is sometimes mistaken
    teaches people to distrust the accurate ones too. This fixture is what keeps
    them from creeping back.
    """
    spawned = []
    monkeypatch.setattr("lgtv_easy.proc.popen",
                        lambda *a, **k: spawned.append(a))
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    return spawned


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
@pytest.mark.parametrize("verdict", [selfheal.VERDICT_TV_OFF,
                                     selfheal.VERDICT_PAIRING,
                                     selfheal.VERDICT_NO_NETWORK,
                                     selfheal.VERDICT_WRONG_NETWORK,
                                     selfheal.VERDICT_TV_NOT_ON_NETWORK])
def test_no_verdict_interrupts_the_user(interruptions, monkeypatch, verdict):
    """Not even the ones that need a human. The watcher records what it found
    and says so where the user is already looking - the status line, and the
    panel when they next open it - rather than pushing a window or a toast at
    somebody who did not ask for one."""
    daemon, clock, _ = _daemon(monkeypatch, verdict)
    daemon._note_connect_failure(OSError("no route"))
    clock.advance(DIAGNOSE_AFTER_SECONDS + 1)
    daemon._diagnose_now()
    assert interruptions == [], f"{verdict} put something in front of the user"


def test_the_app_no_longer_ships_a_notifier():
    """Removed rather than left switched off: a dormant notifier is one import
    away from coming back by accident."""
    import importlib

    with pytest.raises(ImportError):
        importlib.import_module("lgtv_easy.notify")


def test_the_daemon_has_no_way_to_open_a_window(monkeypatch):
    daemon, _clock, _d = _daemon(monkeypatch, selfheal.VERDICT_PAIRING)
    for gone in ("_escalate", "_open_setup"):
        assert not hasattr(daemon, gone), f"{gone} is back"


def test_every_verdict_is_still_recorded_and_logged(interruptions, monkeypatch):
    """Quiet is not the same as silent. Removing the interruption must not
    remove the finding - that is the whole point of the self-check."""
    daemon, clock, _ = _daemon(monkeypatch, selfheal.VERDICT_PAIRING)
    daemon._note_connect_failure(OSError("no route"))
    clock.advance(DIAGNOSE_AFTER_SECONDS + 1)
    daemon._diagnose_now()
    assert daemon.last_diagnosis.verdict == selfheal.VERDICT_PAIRING
    assert selfheal.load_diagnosis().verdict == selfheal.VERDICT_PAIRING


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


# ----- "probably switched off" is a claim, and it needs evidence -------------
def test_a_tv_on_this_pcs_own_cable_is_never_reported_as_switched_off(monkeypatch):
    """The misdiagnosis this whole feature exists to stop. The app told a user
    their TV was probably off while that TV was driving the desktop they were
    reading it on - and "wait, it will come back" is the opposite of the advice
    they needed."""
    from lgtv_easy import display, netdiag
    from lgtv_easy.config import Config, Device

    cfg = Config()
    # Same subnet as the PC, so the "different networks" verdict cannot fire
    # first and mask what is being tested here.
    cfg.device = Device(name="t", ip="192.168.86.10", key="k")
    monkeypatch.setattr(netdiag, "local_ipv4s", lambda: ["192.168.86.21"])
    failed = selfheal.RepairResult(ok=False, error="timed out")

    monkeypatch.setattr(display, "tv_is_physically_on", lambda: True)
    assert selfheal._classify(cfg, failed) == selfheal.VERDICT_TV_NOT_ON_NETWORK

    # ...and with no display attached there is no evidence, so the old, calm
    # answer is still the right one.
    monkeypatch.setattr(display, "tv_is_physically_on", lambda: False)
    assert selfheal._classify(cfg, failed) == selfheal.VERDICT_TV_OFF


def test_a_tv_that_is_on_but_unreachable_asks_for_help(monkeypatch):
    """It will not fix itself, so it must not be filed with the quiet ones."""
    assert selfheal.VERDICT_TV_NOT_ON_NETWORK in selfheal.NEEDS_USER
    assert selfheal.VERDICT_TV_OFF not in selfheal.NEEDS_USER

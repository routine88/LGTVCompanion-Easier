"""A TV that has gone silent on the network: wake it, but only if it was on.

The TV can drop every packet sent to it while it is on - it answers ARP,
ignores the rest - and a sustained Wake-on-LAN burst brings it back at once.
The logs show this starting around the idle timeout: the screen-off fails and
the panel stays lit until somebody comes back.

A burst to a TV that is really off switches it on, and the obvious evidence
cannot tell the two apart. Tested 2026-10-02 against the real set: switched off
with its remote, its HDMI connector stayed "connected", DPMS "On", EDID
readable, and its control port kept answering. What it did change was its own
report: "Active" -> "Request Power Off Logo" -> "Active Standby", answering
throughout. So the watcher samples that report and sends a burst only when the
last answer before the silence said the set was on.
"""
import logging

import pytest

from lgtv_easy import display, netdiag, recovery, selfheal, wol
from lgtv_easy.config import Config, Device
from lgtv_easy.daemon import (NETWORK_WAKE_AFTER_SECONDS,
                              NETWORK_WAKE_MAX_PER_OUTAGE,
                              NETWORK_WAKE_REPEAT_SECONDS, POWER_FRESH_SECONDS,
                              STATE_STANDBY, Daemon)
from lgtv_easy.mock_tv import MockTV
from lgtv_easy.webos import WebOSClient

OURS = "B8:16:5F:72:64:C6"


def _quiet():
    log = logging.getLogger("lgtv-easy-test-network-wake")
    log.addHandler(logging.NullHandler())
    log.propagate = False
    return log


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def tv(tmp_path, monkeypatch):
    """A TV that answers until it goes deaf, and comes back on a WoL burst."""
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    mock = MockTV(require_pairing=True).start()
    state = {"awake": True, "bursts": []}

    def factory():
        if not state["awake"]:
            raise OSError("timed out")
        client = WebOSClient("127.0.0.1")
        client._url = lambda: mock.url
        return client

    def wol_fn(deep):
        state["bursts"].append(deep)
        state["awake"] = True

    def make(mac=OURS):
        cfg = Config()
        cfg.device = Device(name="t", ip="192.168.86.38", mac=mac,
                            key=mock.known_key)
        clock = _Clock()
        daemon = Daemon(cfg, client_factory=factory,
                        locator_fn=lambda mac: None,
                        idle_fn=lambda: 3600.0,      # nobody at the PC
                        wol_fn=wol_fn, clock_fn=clock, logger=_quiet())
        return daemon, clock

    yield make, state, mock
    mock.stop()


def _goes_deaf(daemon, state, clock, after):
    """The TV stops answering; ``after`` seconds of silence pass."""
    state["awake"] = False
    daemon._drop_client()
    assert daemon._ensure_client() is None       # starts the outage clock
    clock.t += after


# ----- the watcher -------------------------------------------------------
def test_a_tv_that_went_silent_while_on_is_woken_even_with_nobody_here(tv):
    """The case that left the screen lit for two hours: the user is away, the
    idle timeout wants to blank the TV, and the TV is deaf."""
    make, state, mock = tv
    daemon, clock = make()
    daemon._sample_power()                       # the TV says "Active"
    assert daemon._tv_power[0] == "Active"
    _goes_deaf(daemon, state, clock, NETWORK_WAKE_AFTER_SECONDS + 1)

    assert daemon._ensure_client() is not None, "the woken TV was not reconnected"
    assert state["bursts"] == [True], "should be one sustained burst"
    assert daemon.network_wakes == 1


def test_a_tv_switched_off_with_its_remote_is_left_off(tv):
    """What the real set said when switched off: no burst may follow it."""
    make, state, _ = tv
    daemon, clock = make()
    daemon._note_power({"state": "Active Standby"})
    _goes_deaf(daemon, state, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    assert daemon._ensure_client(force=True) is None
    assert state["bursts"] == []


def test_a_tv_on_its_way_off_is_left_off(tv):
    """The first answer after pressing power: still "Active", but processing
    the power-off."""
    make, state, _ = tv
    daemon, clock = make()
    daemon._note_power({"state": "Active", "processing": "Request Power Off Logo"})
    _goes_deaf(daemon, state, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    assert daemon._ensure_client(force=True) is None
    assert state["bursts"] == []


def test_a_tv_we_blanked_counts_as_on(tv):
    """Screen-off is ours, and the set is on behind it: waking its network is
    how the screen comes back when the user returns."""
    make, state, _ = tv
    daemon, clock = make()
    daemon._note_power({"state": "Screen Off"})
    _goes_deaf(daemon, state, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    assert daemon._ensure_client(force=True) is not None
    assert state["bursts"] == [True]


def test_no_report_means_no_burst(tv):
    make, state, _ = tv
    daemon, clock = make()
    _goes_deaf(daemon, state, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    assert daemon._ensure_client(force=True) is None
    assert state["bursts"] == []


def test_a_stale_report_means_no_burst(tv):
    """"Active" an hour before the silence says nothing about the moment the
    TV went quiet."""
    make, state, _ = tv
    daemon, clock = make()
    daemon._note_power({"state": "Active"})
    clock.t += POWER_FRESH_SECONDS + 1
    _goes_deaf(daemon, state, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    assert daemon._ensure_client(force=True) is None
    assert state["bursts"] == []


def test_not_before_the_silence_has_lasted(tv):
    make, state, _ = tv
    daemon, clock = make()
    daemon._note_power({"state": "Active"})
    _goes_deaf(daemon, state, clock, NETWORK_WAKE_AFTER_SECONDS - 5)
    assert daemon._ensure_client(force=True) is None
    assert state["bursts"] == []


def test_not_after_our_own_deep_power_off(tv):
    """Waking from standby is wake_screen's decision, not this one's."""
    make, state, _ = tv
    daemon, clock = make()
    daemon._note_power({"state": "Active"})
    daemon.screen_state = STATE_STANDBY
    _goes_deaf(daemon, state, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    assert daemon._ensure_client(force=True) is None
    assert state["bursts"] == []


def test_not_without_a_mac(tv):
    make, state, _ = tv
    daemon, clock = make(mac="")
    daemon._note_power({"state": "Active"})
    _goes_deaf(daemon, state, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    assert daemon._ensure_client(force=True) is None
    assert state["bursts"] == []


def test_bursts_are_spaced_and_capped_per_outage(tv):
    """Each burst blocks the loop for seconds, and a set unplugged at the wall
    must not get one every five minutes for a week."""
    make, state, _ = tv
    daemon, clock = make()
    daemon._wol_fn = lambda deep: state["bursts"].append(deep)   # stays deaf
    daemon._note_power({"state": "Active"})
    _goes_deaf(daemon, state, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    daemon._ensure_client(force=True)
    clock.t += 30
    daemon._ensure_client(force=True)
    assert len(state["bursts"]) == 1
    for _ in range(NETWORK_WAKE_MAX_PER_OUTAGE + 2):
        clock.t += NETWORK_WAKE_REPEAT_SECONDS
        daemon._ensure_client(force=True)
    assert len(state["bursts"]) == NETWORK_WAKE_MAX_PER_OUTAGE


def test_a_failed_power_sample_starts_the_outage_clock(tv):
    """A dead socket must be noticed at the next sample, not whenever the next
    screen action happens to need it - the bursts are timed from it."""
    make, state, _ = tv
    daemon, clock = make()
    daemon._sample_power()
    assert daemon._client is not None

    class Dead:
        connected = True

        def get_power_state(self):
            raise OSError("Connection closed awaiting response")

        def close(self):
            pass

    daemon._client = Dead()
    state["awake"] = False
    clock.t += 11
    daemon._sample_power()                      # fails, drops the client
    clock.t += 11
    daemon._sample_power()                      # reconnect fails: outage begins
    assert daemon._failing_since is not None
    assert daemon._was_on_when_silenced() is True


def test_the_self_check_is_told_what_the_tv_last_said(tv, monkeypatch):
    make, state, _ = tv
    daemon, clock = make()
    daemon._note_power({"state": "Active Standby"})
    _goes_deaf(daemon, state, clock, 1)
    seen = {}

    def diagnose(cfg, **kwargs):
        seen.update(kwargs)
        return selfheal.Diagnosis(verdict=selfheal.VERDICT_TV_OFF)

    monkeypatch.setattr(selfheal, "diagnose", diagnose)
    monkeypatch.setattr(selfheal, "save_diagnosis", lambda d: True)
    daemon._diagnose_now()
    assert seen["was_on"] is False


# ----- a person asked ----------------------------------------------------
@pytest.fixture
def attended(tmp_path, monkeypatch):
    """Pairing fails until a burst is sent."""
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    state = {"awake": False, "bursts": 0}

    def burst(mac, ip=""):
        state["bursts"] += 1
        state["awake"] = True

    def pair(client, **kwargs):
        if not state["awake"]:
            raise OSError("timed out")
        return kwargs.get("client_key", "")

    monkeypatch.setattr(wol, "wake_burst", burst)
    monkeypatch.setattr(recovery, "pair_with_fallback", pair)
    monkeypatch.setattr(selfheal, "pair_with_fallback", pair)
    monkeypatch.setattr(netdiag, "tcp_probe",
                        lambda ip, port, timeout=2.0: (False, "timed out"))
    monkeypatch.setattr(netdiag, "local_ipv4s", lambda: ["192.168.86.21"])
    from lgtv_easy import discovery
    monkeypatch.setattr(discovery, "locate_tv", lambda *a, **k: None)
    cfg = Config()
    cfg.device = Device(name="t", ip="192.168.86.38", mac=OURS, key="k")
    return cfg, state


def test_test_my_tv_wakes_a_silent_tv(attended):
    """Whether it was deaf or in standby, the person asked for it to work."""
    cfg, state = attended
    client = recovery.connect_tv(cfg)
    client.close()
    assert state["bursts"] == 1


def test_unattended_connect_tv_sends_no_burst(attended):
    cfg, state = attended
    with pytest.raises(OSError):
        recovery.connect_tv(cfg, allow_guess=False)
    assert state["bursts"] == 0


def test_repair_wakes_it(attended):
    cfg, state = attended
    res = selfheal.repair(cfg, connect=False)
    assert res.ok is True and state["bursts"] == 1
    assert "wake-on-lan" in res.summary.lower()


def test_unattended_repair_leaves_the_burst_to_the_watcher(attended):
    cfg, state = attended
    assert selfheal.repair(cfg, connect=False, allow_guess=False).ok is False
    assert state["bursts"] == 0


# ----- what the self-check says ------------------------------------------
@pytest.fixture
def unreachable(monkeypatch):
    cfg = Config()
    cfg.device = Device(name="t", ip="192.168.86.38", mac=OURS, key="k")
    monkeypatch.setattr(netdiag, "local_ipv4s", lambda: ["192.168.86.21"])
    return cfg, selfheal.RepairResult(ok=False, error="timed out")


def test_the_tvs_word_decides_on_or_off(unreachable, monkeypatch):
    cfg, failed = unreachable
    monkeypatch.setattr(netdiag, "arp_table",
                        lambda *a, **k: [("192.168.86.38", OURS)])
    assert selfheal._classify(cfg, failed, True) == selfheal.VERDICT_TV_NETWORK_ASLEEP
    assert selfheal._classify(cfg, failed, False) == selfheal.VERDICT_TV_OFF

    monkeypatch.setattr(netdiag, "arp_table", lambda *a, **k: [])
    assert selfheal._classify(cfg, failed, True) == selfheal.VERDICT_TV_NOT_ON_NETWORK


def test_the_hdmi_cable_alone_no_longer_raises_the_alarm(unreachable, monkeypatch):
    """The cable said "on" for a TV that was switched off, so every overnight
    standby used to become a needs-you warning about the TV's Wi-Fi."""
    cfg, failed = unreachable
    monkeypatch.setattr(display, "tv_is_physically_on", lambda: True)
    assert selfheal._classify(cfg, failed, None) == selfheal.VERDICT_TV_OFF


@pytest.mark.parametrize("verdict", [selfheal.VERDICT_TV_NETWORK_ASLEEP,
                                     selfheal.VERDICT_TV_NOT_ON_NETWORK])
def test_no_summary_claims_the_tv_is_switched_on(monkeypatch, verdict):
    cfg = Config()
    cfg.device = Device(name="t", ip="192.168.86.38", mac=OURS, key="k")
    monkeypatch.setattr(selfheal, "repair", lambda *a, **k: selfheal.RepairResult(
        ok=False, error="timed out", steps=[]))
    monkeypatch.setattr(selfheal, "_classify", lambda cfg, res, was_on: verdict)
    text = selfheal.diagnose(cfg, was_on=True).summary.lower()
    assert "is switched on" not in text and "display cable" not in text
    assert "was on when it stopped answering" in text
    if verdict == selfheal.VERDICT_TV_NETWORK_ASLEEP:
        assert "remote" in text and "wi-fi" not in text

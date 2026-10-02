"""A TV that is switched on but has stopped answering on the network.

Seen 2026-10-02: the TV was lit and showing this PC (its EDID said so, and it
later confirmed HDMI 2 was on screen), its MAC answered ARP, and every IP packet
to it - ping, 3000, 3001, anything - timed out. One sustained Wake-on-LAN burst
and port 3001 opened at once. The outage of 2026-09-30 was five hours of the
same, during which the watcher retried, swept the LAN, and finally adopted the
other LG TV in the house.

A burst to a TV that is really off switches it on, so the watcher only sends
one with every piece of evidence in hand: our panel lit on the cable, someone
at the keyboard, a silence longer than a press of the remote's power button,
and never after its own deep power-off. A person pressing 'Test my TV' is the
evidence of the second kind, so the attended paths need only the cable.
"""
import logging

import pytest

from lgtv_easy import display, netdiag, recovery, selfheal, wol
from lgtv_easy.config import Config, Device
# Bound at import, before conftest stubs display.panel_is_lit for every test.
from lgtv_easy.display import panel_is_lit as real_panel_is_lit
from lgtv_easy.daemon import (INPUT_ACTIVE_SECONDS, NETWORK_WAKE_AFTER_SECONDS,
                              NETWORK_WAKE_REPEAT_SECONDS, STATE_STANDBY, Daemon)
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
def deaf_tv(tmp_path, monkeypatch):
    """A TV that drops every connection until a Wake-on-LAN burst arrives."""
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    tv = MockTV(require_pairing=True).start()
    state = {"awake": False, "bursts": [], "idle": 0.0, "lit": True}

    def factory():
        if not state["awake"]:
            raise OSError("timed out")
        client = WebOSClient("127.0.0.1")
        client._url = lambda: tv.url
        return client

    def wol_fn(deep):
        state["bursts"].append(deep)
        state["awake"] = True

    def make(mac=OURS):
        cfg = Config()
        cfg.device = Device(name="t", ip="192.168.86.38", mac=mac,
                            key=tv.known_key)
        clock = _Clock()
        daemon = Daemon(cfg, client_factory=factory,
                        locator_fn=lambda mac: None,
                        idle_fn=lambda: state["idle"],
                        panel_on_fn=lambda: state["lit"],
                        wol_fn=wol_fn, clock_fn=clock, logger=_quiet())
        return daemon, clock

    yield make, state, tv
    tv.stop()


def _silent_for(daemon, clock, seconds):
    """Fail once to start the outage clock, then let ``seconds`` pass."""
    assert daemon._ensure_client() is None
    clock.t += seconds


# ----- the watcher -------------------------------------------------------
def test_a_lit_silent_tv_gets_one_burst_and_comes_back(deaf_tv):
    make, state, tv = deaf_tv
    daemon, clock = make()
    _silent_for(daemon, clock, NETWORK_WAKE_AFTER_SECONDS + 1)

    assert daemon._ensure_client() is not None, "the woken TV was not reconnected"
    assert state["bursts"] == [True], "should be one sustained burst"
    assert daemon.network_wakes == 1
    assert tv.pair_prompts == 0


def test_not_before_the_silence_has_lasted(deaf_tv):
    """A few seconds of silence is somebody pressing the power button."""
    make, state, _ = deaf_tv
    daemon, clock = make()
    _silent_for(daemon, clock, NETWORK_WAKE_AFTER_SECONDS - 5)
    assert daemon._ensure_client(force=True) is None
    assert state["bursts"] == []


def test_not_when_nobody_is_at_the_pc(deaf_tv):
    """The cable can say "connected" for a TV in standby; with nobody here, a
    burst could switch on a TV that its owner turned off."""
    make, state, _ = deaf_tv
    state["idle"] = INPUT_ACTIVE_SECONDS + 1
    daemon, clock = make()
    _silent_for(daemon, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    assert daemon._ensure_client(force=True) is None
    assert state["bursts"] == []


def test_not_when_the_cable_does_not_show_our_tv_lit(deaf_tv):
    make, state, _ = deaf_tv
    state["lit"] = False
    daemon, clock = make()
    _silent_for(daemon, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    assert daemon._ensure_client(force=True) is None
    assert state["bursts"] == []


def test_not_after_our_own_deep_power_off(deaf_tv):
    """Waking from standby is wake_screen's decision, not this one's."""
    make, state, _ = deaf_tv
    daemon, clock = make()
    daemon.screen_state = STATE_STANDBY
    _silent_for(daemon, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    assert daemon._ensure_client(force=True) is None
    assert state["bursts"] == []


def test_not_without_a_mac(deaf_tv):
    make, state, _ = deaf_tv
    daemon, clock = make(mac="")
    _silent_for(daemon, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    assert daemon._ensure_client(force=True) is None
    assert state["bursts"] == []


def test_one_burst_per_interval_while_it_stays_silent(deaf_tv):
    """Each burst blocks the loop for seconds; a TV that won't wake must not get
    one on every poll."""
    make, state, _ = deaf_tv
    daemon, clock = make()

    def stays_deaf(deep):
        state["bursts"].append(deep)          # sent, but the TV stays deaf

    daemon._wol_fn = stays_deaf
    _silent_for(daemon, clock, NETWORK_WAKE_AFTER_SECONDS + 1)
    daemon._ensure_client(force=True)
    clock.t += 30
    daemon._ensure_client(force=True)
    assert len(state["bursts"]) == 1
    clock.t += NETWORK_WAKE_REPEAT_SECONDS
    daemon._ensure_client(force=True)
    assert len(state["bursts"]) == 2


def test_the_default_panel_check_wants_the_panel_setup_recorded(monkeypatch):
    """Another LG display on the cable - a monitor, a different set - is not
    evidence about the TV we are trying to reach."""
    from lgtv_easy.display import Panel
    lit = Panel(connector="HDMI-A-1", vendor="GSM", product=0xC0C8,
                name="LG TV SSCR2", powered=True)
    monkeypatch.setattr(display, "lg_panel", lambda: lit)
    assert real_panel_is_lit("GSM:C0C8:LG TV SSCR2") is True
    assert real_panel_is_lit("") is True                 # nothing pinned yet
    assert real_panel_is_lit("GSM:5B09:LG ULTRAGEAR") is False
    lit.powered = False
    assert real_panel_is_lit("GSM:C0C8:LG TV SSCR2") is False


# ----- a person asked ----------------------------------------------------
@pytest.fixture
def attended(tmp_path, monkeypatch):
    """Pairing fails until a burst is sent; the cable shows our TV lit."""
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
    monkeypatch.setattr(display, "panel_is_lit", lambda identity="": True)
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


def test_test_my_tv_wakes_a_lit_silent_tv(attended):
    cfg, state = attended
    client = recovery.connect_tv(cfg)
    client.close()
    assert state["bursts"] == 1


def test_unattended_connect_tv_sends_no_burst(attended):
    cfg, state = attended
    with pytest.raises(OSError):
        recovery.connect_tv(cfg, allow_guess=False)
    assert state["bursts"] == 0


def test_repair_wakes_it_and_says_what_was_wrong(attended):
    cfg, state = attended
    res = selfheal.repair(cfg, connect=False)
    assert res.ok is True and state["bursts"] == 1
    assert "gone to sleep" in res.summary


def test_unattended_repair_leaves_the_burst_to_the_watcher(attended):
    cfg, state = attended
    assert selfheal.repair(cfg, connect=False, allow_guess=False).ok is False
    assert state["bursts"] == 0


# ----- what the self-check says ------------------------------------------
def test_a_lit_tv_whose_mac_is_here_is_asleep_not_elsewhere(monkeypatch):
    """The old verdict told the user to move the TV to the right Wi-Fi - for a
    TV that was on the right Wi-Fi and answering ARP."""
    cfg = Config()
    cfg.device = Device(name="t", ip="192.168.86.38", mac=OURS, key="k")
    monkeypatch.setattr(netdiag, "local_ipv4s", lambda: ["192.168.86.21"])
    monkeypatch.setattr(display, "tv_is_physically_on", lambda: True)
    failed = selfheal.RepairResult(ok=False, error="timed out")

    monkeypatch.setattr(netdiag, "arp_table",
                        lambda *a, **k: [("192.168.86.38", OURS)])
    assert selfheal._classify(cfg, failed) == selfheal.VERDICT_TV_NETWORK_ASLEEP

    monkeypatch.setattr(netdiag, "arp_table", lambda *a, **k: [])
    assert selfheal._classify(cfg, failed) == selfheal.VERDICT_TV_NOT_ON_NETWORK


def test_the_asleep_summary_does_not_send_the_user_to_the_wifi_menu(monkeypatch):
    cfg = Config()
    cfg.device = Device(name="t", ip="192.168.86.38", mac=OURS, key="k")
    monkeypatch.setattr(selfheal, "repair", lambda *a, **k: selfheal.RepairResult(
        ok=False, error="timed out", steps=[]))
    monkeypatch.setattr(selfheal, "_classify",
                        lambda cfg, res: selfheal.VERDICT_TV_NETWORK_ASLEEP)
    monkeypatch.setattr(display, "lg_panel", lambda: None)
    text = selfheal.diagnose(cfg).summary.lower()
    assert "gone to sleep" in text and "remote" in text
    assert "different network" not in text and "wi-fi this pc uses" not in text
    assert selfheal.VERDICT_TV_NETWORK_ASLEEP in selfheal.NEEDS_USER

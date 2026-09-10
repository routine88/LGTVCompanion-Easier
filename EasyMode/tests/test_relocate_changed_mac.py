"""Finding the TV again after its MAC has changed.

The outage these come from: the TV moved to a new DHCP address *and* came back
on a different MAC (a set has one per interface, so moving between Wi-Fi and
Ethernet changes it). The recovery code treated the stored MAC as a permanent
identifier, so `locate_by_mac` matched nothing and `locate_tv` gave up on the
spot - while the TV sat on the LAN answering anyone who asked. The app hunted a
device that no longer existed, every thirty seconds, for days.

The fix has to work *unattended*, which is the hard part: the watcher must never
adopt a TV by contacting it, because a registration the TV does not recognise
puts a pairing prompt on somebody's screen. So identification uses two signals
that send the TV nothing it can react to - an LG vendor prefix, and a completed
WebSocket handshake on a control port.
"""
import socket

import pytest

from lgtv_easy import discovery, netdiag
from lgtv_easy.mock_tv import MockTV

OURS = "B8:16:5F:72:64:C6"          # the MAC that is stored and no longer exists
THEIRS = "64:CB:E9:70:CF:A4"        # the one the TV actually has now


# ----- telling an LG TV from a Node server -----------------------------------
def test_lg_macs_are_recognised_including_innotek():
    """A TV's MAC is usually LG *Innotek* - the subsidiary that makes the Wi-Fi
    module - not LG Electronics. Checking only the obvious vendor misses it."""
    assert netdiag.is_lg_mac(OURS)
    assert netdiag.is_lg_mac(THEIRS)
    assert netdiag.is_lg_mac("b8:16:5f:00:00:01")          # case-insensitive


@pytest.mark.parametrize("mac", ["52:8f:38:59:47:7b",      # a Docker bridge
                                 "b0:6a:41:ae:6f:60",      # Google/Nest router
                                 "", "not a mac", "zz:zz:zz:zz:zz:zz"])
def test_everything_else_is_not_an_lg_tv(mac):
    assert not netdiag.is_lg_mac(mac)


def test_a_websocket_endpoint_is_recognised_and_a_plain_socket_is_not():
    """Port 3000 is the most-used dev-server port there is. A bare TCP connect -
    which is all this used to do - offered the user a Node container and a random
    appliance as candidate TVs."""
    tv = MockTV(require_pairing=True).start()
    try:
        assert netdiag.speaks_webos(tv.host, tv.port, timeout=3.0)
    finally:
        tv.stop()

    # A socket that accepts connections but speaks no WebSocket: the false
    # positive this check exists to reject.
    dumb = socket.socket()
    dumb.bind(("127.0.0.1", 0))
    dumb.listen(1)
    try:
        assert not netdiag.speaks_webos(*dumb.getsockname(), timeout=1.0)
    finally:
        dumb.close()


def test_probing_never_registers_with_the_tv():
    """The whole reason this identification is allowed to run unattended: it
    must be impossible for it to make a TV show a pairing prompt."""
    tv = MockTV(require_pairing=True).start()
    try:
        assert netdiag.speaks_webos(tv.host, tv.port, timeout=3.0)
        assert getattr(tv, "registrations", 0) == 0, (
            "the probe registered with the TV - that can put a prompt on screen")
    finally:
        tv.stop()


# ----- the fall-through ------------------------------------------------------
def test_a_stale_mac_no_longer_ends_the_search(monkeypatch):
    """The bug itself: locate_tv returned None the moment the stored MAC missed,
    never trying the sweep that had the answer."""
    monkeypatch.setattr(discovery, "locate_by_mac", lambda *a, **k: None)
    monkeypatch.setattr(netdiag, "lg_tv_hosts",
                        lambda *a, **k: [("192.168.86.51", THEIRS)])
    assert discovery.locate_tv(OURS, allow_guess=False) == "192.168.86.51"


def test_the_fall_through_works_for_the_unattended_watcher(monkeypatch):
    """allow_guess=False is the daemon's setting. If the fix only worked with
    a person present it would not have fixed this outage at all."""
    monkeypatch.setattr(discovery, "locate_by_mac", lambda *a, **k: None)
    monkeypatch.setattr(netdiag, "lg_tv_hosts",
                        lambda *a, **k: [("10.0.0.5", THEIRS)])
    for allow_guess in (True, False):
        assert discovery.locate_tv(OURS, allow_guess=allow_guess) == "10.0.0.5"


def test_two_tvs_are_never_guessed_between(monkeypatch):
    """With the stored MAC matching neither, there is nothing to tell them apart
    - and connecting to the wrong one prompts a household that isn't ours."""
    monkeypatch.setattr(discovery, "locate_by_mac", lambda *a, **k: None)
    monkeypatch.setattr(netdiag, "lg_tv_hosts",
                        lambda *a, **k: [("10.0.0.5", THEIRS),
                                         ("10.0.0.6", "00:1C:62:00:00:01")])
    assert discovery.locate_tv(OURS, allow_guess=False) is None


def test_no_lg_tv_at_all_still_gives_up(monkeypatch):
    monkeypatch.setattr(discovery, "locate_by_mac", lambda *a, **k: None)
    monkeypatch.setattr(netdiag, "lg_tv_hosts", lambda *a, **k: [])
    assert discovery.locate_tv(OURS, allow_guess=False) is None


def test_a_mac_that_still_matches_takes_the_fast_path(monkeypatch):
    """The sweep is slow and the ARP hit is instant; the fall-through must not
    displace it."""
    swept = []
    monkeypatch.setattr(discovery, "locate_by_mac", lambda *a, **k: "10.0.0.9")
    monkeypatch.setattr(netdiag, "lg_tv_hosts",
                        lambda *a, **k: swept.append(1) or [])
    assert discovery.locate_tv(OURS, allow_guess=False) == "10.0.0.9"
    assert swept == [], "swept the LAN even though the stored MAC matched"


# ----- and the daemon remembers ----------------------------------------------
def test_the_daemon_saves_the_tvs_new_mac(monkeypatch, tmp_path):
    """Without this the app re-runs the slow search on every future outage, and
    aims its wake-on-LAN at a MAC that belongs to nothing."""
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    from lgtv_easy.config import Config, Device
    from lgtv_easy.daemon import Daemon

    cfg = Config()
    cfg.device = Device(name="t", ip="192.168.86.27", mac=OURS, key="k")
    daemon = Daemon(cfg, locator_fn=lambda mac: "192.168.86.51")
    monkeypatch.setattr(netdiag, "mac_for_ip", lambda ip: THEIRS)

    assert daemon._relocate(force=True) is True
    assert cfg.device.ip == "192.168.86.51"
    assert netdiag.canon_mac(cfg.device.mac) == netdiag.canon_mac(THEIRS)


def test_a_non_lg_mac_is_never_adopted_as_the_tvs(monkeypatch, tmp_path):
    """mac_for_ip reads the ARP table, which can hand back a router's MAC for an
    address that has just moved. Writing that in would break wake-on-LAN."""
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    from lgtv_easy.config import Config, Device
    from lgtv_easy.daemon import Daemon

    cfg = Config()
    cfg.device = Device(name="t", ip="192.168.86.27", mac=OURS, key="k")
    daemon = Daemon(cfg, locator_fn=lambda mac: "192.168.86.51")
    monkeypatch.setattr(netdiag, "mac_for_ip", lambda ip: "b0:6a:41:ae:6f:60")

    assert daemon._relocate(force=True) is True
    assert netdiag.canon_mac(cfg.device.mac) == netdiag.canon_mac(OURS)

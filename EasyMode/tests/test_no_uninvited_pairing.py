"""Nothing unattended may adopt a TV it cannot identify.

Connecting to a WebOS TV means registering with it, and a TV that does not
recognise the registration asks its owner - by putting "a mobile device wants
to connect?" on screen, over whatever they were watching. That is a reasonable
thing to cause while someone is sitting in the app waiting to press Accept. It
is not something a watcher running unattended for weeks should ever do, still
less to a TV that isn't theirs.

So the rule: relocating a lost TV falls back to "adopt the only LG TV on the
network" **only** when a person asked for it. Everything that runs on its own
relocates by MAC or not at all.

(Not a hypothetical. A test suite doing this to a real TV on the developer's
LAN is what prompted the rule - about twenty times in one evening.)
"""
import threading

import pytest

from lgtv_easy import discovery, netdiag, selfheal
from lgtv_easy.config import Config, Device
from lgtv_easy.daemon import Daemon
from lgtv_easy.discovery import Discovered


@pytest.fixture
def no_mac_but_a_tv_out_there(monkeypatch):
    """No saved MAC, and exactly one LG TV answering - the tempting case."""
    monkeypatch.setattr(netdiag, "ip_for_mac", lambda mac, timeout=4.0: "")
    seen = {"discovered": False}

    def discover(timeout=3.0, log=None):
        seen["discovered"] = True
        return [Discovered(ip="192.168.86.33", name="LG someone else's", is_lg=True)]

    monkeypatch.setattr(discovery, "discover", discover)
    monkeypatch.setattr(netdiag, "webos_hosts", lambda *a, **k: ["192.168.86.33"])
    return seen


# ----- the rule itself ---------------------------------------------------
def test_asked_for_it_the_single_tv_is_adopted(no_mac_but_a_tv_out_there):
    """The existing behaviour, unchanged, for a person who pressed a button."""
    assert discovery.locate_tv("") == "192.168.86.33"


def test_unattended_it_is_left_alone(no_mac_but_a_tv_out_there):
    assert discovery.locate_tv("", allow_guess=False) is None
    assert no_mac_but_a_tv_out_there["discovered"] is False, (
        "it should not even go looking - the search is what finds a stranger's TV")


def test_the_refusal_says_what_to_do_instead(no_mac_but_a_tv_out_there):
    said = []
    discovery.locate_tv("", allow_guess=False, log=said.append)
    text = " ".join(said).lower()
    assert "mac" in text and "test my tv" in text


def test_a_known_mac_is_still_tracked_unattended(monkeypatch):
    """The restriction is about *unidentified* TVs. A MAC match is proof of
    identity, so the watcher must still follow its own TV across DHCP."""
    monkeypatch.setattr(netdiag, "ip_for_mac",
                        lambda mac, timeout=4.0: "192.168.86.9")
    monkeypatch.setattr(discovery, "discover", lambda **k: pytest.fail(
        "a cached MAC match should not need discovery"))
    assert discovery.locate_tv("B8:16:5F:72:64:C6",
                               allow_guess=False) == "192.168.86.9"


# ----- every unattended caller passes it ---------------------------------
def test_the_watcher_relocates_by_mac_only(monkeypatch):
    """The daemon runs for weeks with nobody in front of it."""
    captured = {}

    def fake_locate(mac, timeout=3.0, log=None, allow_guess=True):
        captured["mac"], captured["allow_guess"] = mac, allow_guess
        return None

    monkeypatch.setattr(discovery, "locate_tv", fake_locate)
    daemon = Daemon(Config())
    daemon._default_locator("B8:16:5F:72:64:C6")
    assert captured["allow_guess"] is False


def test_repair_passes_the_restriction_through(monkeypatch, tmp_path):
    """selfheal.repair is the shared engine; it must not quietly widen the
    search on its callers' behalf."""
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    monkeypatch.setattr(netdiag, "tcp_probe",
                        lambda ip, port, timeout=2.0: (False, "no route"))
    captured = {}

    def fake_locate(mac, timeout=3.0, log=None, allow_guess=True):
        captured["allow_guess"] = allow_guess
        return None

    monkeypatch.setattr(discovery, "locate_tv", fake_locate)
    cfg = Config()
    cfg.device = Device(name="t", ip="10.0.0.5", key="k")

    selfheal.repair(cfg, allow_guess=False)
    assert captured["allow_guess"] is False

    selfheal.repair(cfg)                      # a person asked: unchanged
    assert captured["allow_guess"] is True


def test_the_windows_startup_selftest_is_unattended(monkeypatch, tmp_path):
    """Opening the settings window is not a request to go hunting: the check
    that runs by itself on open must relocate by MAC only. 'Test my TV', which
    is a request, keeps the wider search."""
    tk = pytest.importorskip("tkinter")
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    monkeypatch.setenv("LGTV_EASY_NO_SLEEP_WATCH", "1")
    monkeypatch.delenv("LGTV_EASY_NO_SELFTEST", raising=False)

    cfg = Config(setup_complete=True)
    cfg.device = Device(name="mock", ip="127.0.0.1", key="k")
    cfg.save()

    from lgtv_easy import gui

    done = threading.Event()
    captured = {}

    monkeypatch.setattr(selfheal, "quick_health_check", lambda cfg: False)

    def fake_repair(cfg, **kwargs):
        captured.update(kwargs)
        done.set()
        return selfheal.RepairResult(ok=True, summary="stub", steps=[])

    monkeypatch.setattr(selfheal, "repair", fake_repair)

    try:
        app = gui.App()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    try:
        for _ in range(40):
            app.update_idletasks()
            app.update()
            if done.wait(0.05):
                break
        assert done.is_set(), "the startup self-test never ran"
        assert captured.get("allow_guess") is False
    finally:
        app.on_close()

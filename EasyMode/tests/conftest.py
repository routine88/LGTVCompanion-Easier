import os
import socket
import sys
import tempfile

# Make the package importable when running tests from the repo without install.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Never let a daemon started during tests spawn the real OS suspend/resume
# monitors (gdbus + systemd-inhibit on Linux, the power-notify registration on
# Windows). The daemon checks this before starting its sleep watcher.
os.environ.setdefault("LGTV_EASY_NO_SLEEP_WATCH", "1")

# Likewise, keep the GUI's startup connection self-test from firing real network
# probes/discovery when a settings panel is built in a test; scenarios that want
# it exercise selfheal/the repair dialog explicitly.
os.environ.setdefault("LGTV_EASY_NO_SELFTEST", "1")

# Point the config directory at a throwaway home for the whole run. Several code
# paths persist what they learn (the TV's MAC, its address, which input this PC
# is on) as a side effect, and without this a test run would rewrite the real
# user's config - including that of the copy actually driving their TV. Tests
# that need their own directory still monkeypatch this per-test.
os.environ.setdefault("LGTV_EASY_HOME",
                      tempfile.mkdtemp(prefix="lgtv-easy-tests-"))

# Auto-start is the one thing Easy Mode writes that does NOT live under
# LGTV_EASY_HOME: on Windows it is a file in the real Startup folder (from
# %APPDATA%) plus a Scheduled Task, and on Linux a file under XDG_CONFIG_HOME.
# Several wizard tests answer "no" to "start when I log in", which means they
# were reaching past the temporary config directory and switching off the login
# entry of whoever ran the suite. Point that whole mechanism at a throwaway
# directory instead.
os.environ.setdefault("LGTV_EASY_AUTOSTART_SANDBOX",
                      tempfile.mkdtemp(prefix="lgtv-easy-autostart-"))


# ----- the tests must not touch anything on the real network -----------------
# This app's whole job is talking to a TV on the LAN, and a test that slips past
# its mock reaches a *real* one. That is not a quiet failure: connecting without
# a valid client-key makes the TV throw "a mobile device wants to connect?" up on
# screen, over whatever the household is watching. It happened - about twenty
# times - while a full suite was running on a machine with a paired TV nearby.
#
# So the whole session is fenced: loopback (the MockTV) is allowed, everything
# else is refused before a packet leaves. Refusing looks to the code under test
# exactly like an unreachable host, which every network path here already
# handles, and the offenders are listed at the end of the run so a leak gets
# fixed rather than tolerated.
#
# Set LGTV_EASY_TESTS_ALLOW_NETWORK=1 to lift it (nothing in the suite needs to).
_LEAKS: "list" = []
_CURRENT_TEST = {"id": "(collection)"}
_ALLOW_NETWORK = os.environ.get("LGTV_EASY_TESTS_ALLOW_NETWORK") == "1"
# SSDP's multicast group: discovery shouts here, and every TV on the LAN answers.
_SSDP = "239.255.255.250"


def _is_local(host) -> bool:
    if not isinstance(host, str):
        return False
    return (host.startswith("127.") or host in ("localhost", "::1", "")
            or host == "0.0.0.0")


def _guard(kind: str, address) -> None:
    """Record and refuse a connection that would leave this machine."""
    host = address[0] if isinstance(address, tuple) and address else address
    _LEAKS.append((_CURRENT_TEST["id"], f"{kind} -> {host}"))
    raise OSError(
        f"[test network guard] {_CURRENT_TEST['id']} tried to {kind} {host}. "
        "Tests must not touch the real network - a live TV would show a "
        "pairing prompt. Stub the discovery/netdiag call, or point it at the "
        "MockTV on 127.0.0.1.")


if not _ALLOW_NETWORK:
    _real_connect = socket.socket.connect
    _real_connect_ex = socket.socket.connect_ex
    _real_sendto = socket.socket.sendto

    def _connect(self, address):
        # A datagram "connect" sends nothing - netdiag uses one to ask the OS
        # which local address would route outward - so it is left alone.
        if self.type == socket.SOCK_STREAM and not _is_local(
                address[0] if isinstance(address, tuple) else address):
            _guard("connect to", address)
        return _real_connect(self, address)

    def _connect_ex(self, address):
        if self.type == socket.SOCK_STREAM and not _is_local(
                address[0] if isinstance(address, tuple) else address):
            _LEAKS.append((_CURRENT_TEST["id"], f"probe -> {address}"))
            return 111        # ECONNREFUSED: "nothing there", the honest answer
        return _real_connect_ex(self, address)

    def _sendto(self, data, *args):
        target = args[-1] if args else None
        host = target[0] if isinstance(target, tuple) and target else None
        if host and (host == _SSDP or not _is_local(host)):
            _guard("send a discovery packet to", target)
        return _real_sendto(self, data, *args)

    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex
    socket.socket.sendto = _sendto


def pytest_runtest_setup(item):
    _CURRENT_TEST["id"] = item.nodeid


def pytest_terminal_summary(terminalreporter):
    if not _LEAKS:
        return
    terminalreporter.write_sep("=", "tests that tried to reach the network")
    seen = set()
    for test_id, what in _LEAKS:
        if (test_id, what) in seen:
            continue
        seen.add((test_id, what))
        terminalreporter.write_line(f"  {test_id}\n      {what}")

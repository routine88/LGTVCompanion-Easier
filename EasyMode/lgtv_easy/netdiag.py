"""Small, dependency-free network diagnostics helpers.

These exist so the setup wizard (and the launchers) can show a beginner exactly
*why* finding or reaching their TV failed - which network the PC is on, whether
the TV's ports answer, and so on - instead of a bare "timed out". Everything here
is best-effort and never raises; on any error it returns empty/safe values.
"""
from __future__ import annotations

import platform
import re
import socket
import sys
import time
from typing import List, Optional, Tuple

from . import proc

# WebOS control ports: plain WebSocket (3000) and TLS WebSocket (3001).
WEBOS_PORTS = (3000, 3001)


def _default_route_ipv4() -> str:
    """The address this PC would use to reach the internet, or "".

    A UDP "connect" only sets the route; it sends nothing.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return ""


# SIOCGIFADDR from <bits/ioctls.h>. Linux-only: the BSDs and macOS number their
# ioctls differently, and asking them this one would read the wrong struct.
_SIOCGIFADDR = 0x8915


def _interface_ipv4s() -> "set[str]":
    """Every interface's IPv4 address, asked of the kernel directly (Linux).

    This is the only method that sees an interface which is neither the default
    route nor whatever the hostname resolves to - and on a machine with a VPN up,
    the LAN interface is exactly that. Empty set anywhere it cannot be done.
    """
    if not sys.platform.startswith("linux"):
        return set()
    try:
        import fcntl
        import struct
    except ImportError:                      # not a POSIX build
        return set()
    try:
        names = [name for _index, name in socket.if_nameindex()]
    except (AttributeError, OSError):
        return set()
    found = set()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return set()
    try:
        for name in names:
            try:
                packed = fcntl.ioctl(
                    sock.fileno(), _SIOCGIFADDR,
                    struct.pack("256s", name.encode("utf-8")[:15]))
                found.add(socket.inet_ntoa(packed[20:24]))
            except OSError:
                continue                     # no IPv4 on this interface
    finally:
        sock.close()
    return found


def local_ipv4s() -> List[str]:
    """Return this PC's usable IPv4 addresses, most-likely-useful first.

    Beginners on a desktop often have several interfaces (e.g. a wired Ethernet
    link plus Wi-Fi). Knowing them all lets discovery send its search out of each
    one, lets the ARP sweep cover the right subnet, and lets the diagnostics tell
    the user which network the PC is actually on - the single most common reason
    a TV "can't be found" is the PC and TV being on different subnets.

    Three sources, because no one of them is complete:

    * the default route - the best single answer, and first in the list;
    * every interface the kernel knows about (Linux) - the one that matters when
      a VPN or a container bridge owns the default route, because then the LAN
      interface the TV is actually on appears in *no* other source. Without it,
      a machine with a VPN up searched only the tunnel and could never find its
      own TV, however long it looked;
    * whatever the hostname resolves to - which is how Windows reports its NICs,
      and on Linux is usually just 127.0.1.1.
    """
    ips = set()
    primary = _default_route_ipv4()
    if primary:
        ips.add(primary)
    ips |= _interface_ipv4s()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    usable = [
        ip for ip in ips
        if ip and not ip.startswith("127.") and not ip.startswith("169.254.")
        and ip != "0.0.0.0"
    ]
    # The default route first - it is the answer most likely to be the one the
    # user means - then the rest in a stable order.
    usable.sort(key=lambda ip: (ip != primary, ip))
    return usable


def tcp_probe(host: str, port: int, timeout: float = 2.0) -> Tuple[bool, str]:
    """Try to open a TCP connection; return (reachable, human description)."""
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ms = (time.monotonic() - start) * 1000.0
            return True, f"open ({ms:.0f} ms)"
    except socket.timeout:
        ms = (time.monotonic() - start) * 1000.0
        return False, f"no response / timed out after {ms:.0f} ms (firewall or wrong IP?)"
    except OSError as exc:
        ms = (time.monotonic() - start) * 1000.0
        return False, f"{type(exc).__name__}: {exc} ({ms:.0f} ms)"


def _is_google_wifi(ip: str) -> bool:
    """192.168.86.x is the default LAN that Google/Nest Wifi routers hand out."""
    return ip.startswith("192.168.86.")


def same_subnet_guess(pc_ips: List[str], tv_ip: str) -> str:
    """A friendly note about whether the TV looks like it's on the PC's network.

    Uses a naive /24 comparison (the common home-router case). It is only a hint,
    so the wording stays soft.
    """
    if not tv_ip:
        return ""
    tv_prefix = tv_ip.rsplit(".", 1)[0]
    for ip in pc_ips:
        if ip.rsplit(".", 1)[0] == tv_prefix:
            return f"TV {tv_ip} looks like it's on the same network as {ip}. Good."
    if pc_ips:
        joined = ", ".join(pc_ips)
        msg = (f"WARNING: TV {tv_ip} does not look like it's on the same network "
               f"as this PC ({joined}). They must share a subnet to talk to each "
               f"other - check both are on the same router/SSID.")
        # Catch the very common Google/Nest Wifi double-NAT case, where one side
        # sits behind the Google router (192.168.86.x) and the other is upstream.
        if _is_google_wifi(tv_ip) and not any(_is_google_wifi(p) for p in pc_ips):
            msg += ("\n  NOTE: The TV's 192.168.86.x address means it's on a "
                    "Google/Nest Wifi network, but this PC is not. Connect the PC "
                    "to the same Google Wifi (plug its Ethernet into a Google Wifi "
                    "LAN port, or join that Wi-Fi) so both get a 192.168.86.x "
                    "address, then run setup again.")
        elif any(_is_google_wifi(p) for p in pc_ips) and not _is_google_wifi(tv_ip):
            msg += ("\n  NOTE: This PC is on a Google/Nest Wifi network "
                    "(192.168.86.x) but the TV is not. Put the TV on the same "
                    "Google Wifi network so they share a subnet.")
        return msg
    return ""


def subnet_report(tv_ip: str, log) -> None:
    """Report the PC's network address(es) and whether the TV shares the subnet.

    Fast and non-blocking (no TCP connections), so callers can show it the moment
    a TV IP is known - the subnet mismatch is the most common reason a TV can't be
    reached, and the user shouldn't have to wait for a timeout to find out.
    """
    pc_ips = local_ipv4s()
    log(f"This PC's network address(es): {', '.join(pc_ips) if pc_ips else '(none detected!)'}")
    note = same_subnet_guess(pc_ips, tv_ip)
    if note:
        log(note)


def probe_tv(tv_ip: str, log) -> None:
    """Run and report the standard reachability checks for a TV IP."""
    subnet_report(tv_ip, log)
    for port in WEBOS_PORTS:
        kind = "plain ws" if port == 3000 else "secure wss"
        ok, detail = tcp_probe(tv_ip, port)
        mark = "OK  " if ok else "FAIL"
        log(f"  [{mark}] TCP {tv_ip}:{port} ({kind}) - {detail}")
    log("If both ports FAIL: confirm the TV's IP (Settings > Network), and that")
    log("the TV setting 'LG Connect Apps' / 'Mobile TV On' / network control is enabled.")


_MAC_RE = re.compile(r"([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})")
_IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def canon_mac(mac: str) -> str:
    """Normalise a MAC to upper-case colon form (e.g. 'B8:16:5F:72:64:C6').

    Accepts the ':' , '-' or bare-hex spellings the OS tools and users produce so
    a stored address always compares equal to one freshly read from the system.
    Returns '' if the input doesn't contain a MAC.
    """
    m = _MAC_RE.search(mac or "")
    return m.group(1).replace("-", ":").upper() if m else ""


def _warm_arp(ip: str) -> None:
    """Provoke an ARP entry by briefly touching the host (the SYN resolves the
    MAC even if the port is closed), so the lookup below has something to read."""
    for port in (3001, 3000, 80):
        try:
            socket.create_connection((ip, port), timeout=0.6).close()
            return
        except OSError:
            continue


def mac_for_ip(ip: str, timeout: float = 4.0) -> str:
    """Best-effort lookup of a device's MAC from the OS ARP/neighbour table.

    Lets Easy Mode store the TV's hardware address automatically and use
    Wake-on-LAN to power it back on from deep standby - no manual MAC hunting.
    Returns "" if it can't be determined; never raises.
    """
    if not ip or ip.startswith("127.") or ip in ("localhost", "::1"):
        return ""
    _warm_arp(ip)
    for out in _arp_outputs(ip, timeout):
        # Only trust a MAC found on a line that names the target IP, so we never
        # pick up a neighbouring device's address from a full table dump.
        for line in out.splitlines():
            if ip not in line:
                continue
            m = _MAC_RE.search(line)
            if m:
                return m.group(1).replace("-", ":").upper()
    return ""


def _arp_commands(ip: str) -> "list":
    if sys.platform.startswith("win"):
        return [["arp", "-a", ip], ["arp", "-a"]]
    return [["ip", "neigh", "show", ip], ["arp", "-n", ip],
            ["ip", "neigh"], ["arp", "-n"]]


def _arp_outputs(ip: str, timeout: float = 4.0):
    for cmd in _arp_commands(ip):
        try:
            yield proc.run(cmd, capture_output=True, text=True,
                           timeout=timeout).stdout or ""
        except Exception:  # noqa: BLE001 - tool missing, timeout, etc.
            continue


def arp_dump(ip: str) -> str:
    """Raw ARP/neighbour output for the target IP, for diagnostics."""
    _warm_arp(ip)
    lines = []
    for out in _arp_outputs(ip):
        for line in out.splitlines():
            if ip in line:
                lines.append(line.strip())
    return "\n".join(lines) if lines else "(no ARP entry found for this IP)"


def _arp_dump_commands() -> "list":
    """Commands that print the *whole* ARP/neighbour table (no IP filter)."""
    if sys.platform.startswith("win"):
        return [["arp", "-a"]]
    return [["ip", "neigh"], ["arp", "-n"], ["arp", "-a"]]


def arp_table(timeout: float = 4.0) -> "List[Tuple[str, str]]":
    """Parse the OS ARP/neighbour table into ``(ip, mac)`` pairs.

    The reverse of :func:`mac_for_ip`, this is what lets Easy Mode follow a TV
    that DHCP has moved to a new address: the saved IP stops answering, but the
    MAC is forever, so we look the MAC up here to learn its current IP. MACs come
    back upper-cased and colon-separated. Best-effort: returns ``[]`` if no ARP
    tool is available, and never raises.
    """
    pairs: List[Tuple[str, str]] = []
    seen = set()
    for cmd in _arp_dump_commands():
        try:
            out = proc.run(cmd, capture_output=True, text=True,
                           timeout=timeout).stdout or ""
        except Exception:  # noqa: BLE001 - tool missing, timeout, etc.
            continue
        # One ARP entry per line: an IP and the MAC it resolved to belong
        # together. Lines without both (incomplete/FAILED entries) are skipped.
        for line in out.splitlines():
            mac_m = _MAC_RE.search(line)
            ip_m = _IP_RE.search(line)
            if not mac_m or not ip_m:
                continue
            mac = mac_m.group(1).replace("-", ":").upper()
            if mac == "00:00:00:00:00:00" or mac.lower() == "ff:ff:ff:ff:ff:ff":
                continue
            entry = (ip_m.group(1), mac)
            if entry not in seen:
                seen.add(entry)
                pairs.append(entry)
        if pairs:
            break  # the first tool that produced entries is enough
    return pairs


def ip_for_mac(mac: str, timeout: float = 4.0) -> str:
    """Return the IP currently bound to ``mac`` per the OS ARP table, or ''.

    Only reads what the table already knows; call :func:`find_ip_by_mac` to also
    sweep the subnet when the TV isn't cached yet.
    """
    target = canon_mac(mac)
    if not target:
        return ""
    for ip, found in arp_table(timeout):
        if found == target:
            return ip
    return ""


def sweep_arp(settle: float = 1.5) -> None:
    """Provoke ARP entries for every host on this PC's /24(s).

    When the TV moves to a new DHCP address nothing in the ARP table points at it
    until a packet is sent there. A tiny UDP datagram to each host on the subnet
    forces the kernel to resolve - and cache - the MAC of whoever is online, so a
    follow-up :func:`ip_for_mac` can find the TV at its new address. Hosts that
    are off simply never answer. Best-effort and silent; never raises.
    """
    prefixes = set()
    for ip in local_ipv4s():
        parts = ip.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            prefixes.add(".".join(parts[:3]))
    if not prefixes:
        return
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return
    try:
        for prefix in prefixes:
            for host in range(1, 255):
                try:
                    sock.sendto(b"", (f"{prefix}.{host}", 9))
                except OSError:
                    continue
    finally:
        sock.close()
    # Give the kernel a moment to receive the ARP replies before we read them.
    time.sleep(max(0.0, settle))


def find_ip_by_mac(mac: str, settle: float = 1.5) -> str:
    """Best-effort current IP for ``mac`` on the LAN, '' if it can't be found.

    Checks the ARP table first (instant); if the MAC isn't cached - e.g. the TV
    just took a new DHCP lease - it sweeps the local subnet to repopulate the
    table and looks again. Never raises.
    """
    target = canon_mac(mac)
    if not target:
        return ""
    found = ip_for_mac(target)
    if found:
        return found
    try:
        sweep_arp(settle=settle)
    except Exception:  # noqa: BLE001 - best effort
        pass
    return ip_for_mac(target)


# ----- is this an LG device? -------------------------------------------------
# Every OUI the IEEE has registered to LG Electronics or LG Innotek (the
# subsidiary that makes the Wi-Fi modules inside LG TVs - a TV's MAC is very
# often an Innotek one rather than an Electronics one). Regenerate with:
#
#   grep -iE "\(hex\).*(LG Electronics|LG Innotek)" /usr/share/ieee-data/oui.txt \
#     | awk '{print tolower($1)}' | tr '-' ':' | sort -u
#
# This exists so the app can tell "that is an LG TV" from "that is a Node server
# on port 3000" *without* connecting to it. Identification must never require a
# registration, because a registration the TV does not recognise puts a prompt
# on its screen - see discovery.locate_tv for why that matters.
LG_OUIS = frozenset([
    "00:05:c9", "00:1c:62", "00:1e:75", "00:1e:b2", "00:1f:6b",
    "00:1f:e3", "00:21:fb", "00:22:a9", "00:24:83", "00:25:e5",
    "00:26:e2", "00:34:da", "00:3d:e8", "00:51:ed", "00:57:c1",
    "00:aa:70", "00:e0:91", "04:1b:6d", "04:4e:af", "08:d4:6a",
    "0c:48:85", "10:68:3f", "10:f1:f2", "10:f9:6f", "14:c9:13",
    "1c:08:c1", "20:17:42", "20:21:a5", "20:3d:bd", "24:e8:53",
    "28:0f:eb", "2c:2b:f9", "2c:54:cf", "2c:59:8a", "30:0e:b8",
    "30:76:6f", "30:a9:de", "30:b4:b8", "30:fc:eb", "34:4d:f7",
    "34:fc:ef", "38:30:f9", "38:8c:50", "3c:bd:d8", "3c:cd:93",
    "40:2f:86", "40:b0:fa", "44:cb:8b", "48:59:29", "48:60:5f",
    "48:90:2f", "4c:ba:d7", "4c:bc:e9", "50:55:27", "58:3f:54",
    "58:a2:b5", "58:fd:b1", "5c:70:a3", "5c:af:06", "60:3c:ee",
    "60:ab:14", "60:e3:ac", "64:0d:22", "64:89:9a", "64:95:6c",
    "64:bc:0c", "64:c2:de", "64:cb:e9", "6c:d0:32", "6c:d6:8a",
    "70:05:14", "74:40:be", "74:a7:22", "74:e6:b8", "78:5d:c8",
    "78:f8:82", "7c:1c:4e", "7c:f3:1b", "80:5a:04", "80:5b:65",
    "88:07:4b", "88:36:5f", "88:c9:d0", "8c:3a:e3", "8c:56:46",
    "94:44:44", "98:93:cc", "98:b8:ba", "98:d6:f7", "a0:39:f7",
    "a0:4f:85", "a0:6f:aa", "a0:91:69", "a8:16:b2", "a8:23:fe",
    "a8:92:2c", "a8:b8:6e", "ac:0d:1b", "ac:5a:f0", "ac:f1:08",
    "ac:f6:f7", "b0:37:95", "b4:b2:91", "b4:e6:2a", "b4:f1:da",
    "b4:f7:a1", "b8:16:5f", "b8:1d:aa", "bc:f5:ac", "c0:41:f6",
    "c4:36:6c", "c4:43:8f", "c4:9a:02", "c8:02:10", "c8:08:e9",
    "c8:f3:19", "cc:2d:8c", "cc:88:26", "cc:fa:00", "d0:13:fd",
    "d8:4f:b8", "dc:03:98", "dc:0b:34", "e8:5b:5b", "e8:92:a4",
    "e8:f2:e2", "f0:1c:13", "f8:0c:f3", "f8:38:69", "f8:95:c7",
    "f8:a9:d0", "f8:b9:5a",
])


def is_lg_mac(mac: str) -> bool:
    """True when ``mac``'s vendor prefix belongs to LG. Never raises."""
    canon = canon_mac(mac)
    return bool(canon) and canon[:8].lower() in LG_OUIS


def speaks_webos(ip: str, port: int, timeout: float = 2.0) -> bool:
    """True when ``ip:port`` completes a WebSocket handshake, as a webOS control
    port does.

    Strictly the handshake - no SSAP message is sent, so nothing is registered
    and this can never make a TV show a pairing prompt. It is what separates a
    real control port from the many other things that listen on port 3000: an
    ordinary HTTP server refuses the upgrade, and a bare TCP connect (which is
    all this used to do) cannot tell the difference.
    """
    from ._ws import WebSocket

    scheme = "wss" if port == 3001 else "ws"
    try:
        ws = WebSocket.connect("%s://%s:%d/" % (scheme, ip, port), timeout=timeout)
    except Exception:  # noqa: BLE001 - anything but a clean 101 means "no"
        return False
    try:
        ws.close()
    except Exception:  # noqa: BLE001
        pass
    return True


def webos_hosts(probe_timeout: float = 0.6) -> "List[str]":
    """Live hosts on the LAN that answer on a WebOS control port.

    A MAC-free way to locate the TV when SSDP discovery is blocked - which is the
    norm on Google/Nest Wifi and other mesh routers that don't forward multicast.
    Sweeps the subnet to learn which hosts are up, then probes the WebOS ports on
    each. Returns the matching IP(s) (usually just the TV). Best-effort: returns
    ``[]`` on any error and never raises.
    """
    try:
        sweep_arp()
    except Exception:  # noqa: BLE001 - best effort
        pass
    live: List[str] = []
    seen = set()
    for ip, _mac in arp_table():
        if ip not in seen:
            seen.add(ip)
            live.append(ip)
    found: List[str] = []
    for ip in live:
        for port in WEBOS_PORTS:
            # A WebSocket handshake, not a bare TCP connect. Port 3000 is the
            # most-used development-server port there is, so "something answered"
            # used to offer the user a Node container and a random appliance as
            # candidate TVs - and on a mesh network, where SSDP is dead, this
            # sweep is the *only* way the TV is ever found.
            if speaks_webos(ip, port, timeout=probe_timeout):
                found.append(ip)
                break
    return found


def lg_tv_hosts(probe_timeout: float = 1.5) -> "List[tuple]":
    """Every host on the LAN that is positively an LG webOS TV: ``[(ip, mac)]``.

    Two independent signals, neither of which contacts the TV in a way it could
    react to: the MAC belongs to LG, and the host completes a WebSocket
    handshake on a webOS control port. Together they are strong enough to adopt
    an address on - which matters, because a TV's MAC is *not* the permanent
    identifier the recovery code once assumed. A TV has one MAC per interface
    and will present a different one after moving between Wi-Fi and Ethernet, at
    which point looking it up by the stored MAC can never succeed again.
    """
    try:
        sweep_arp()
    except Exception:  # noqa: BLE001 - best effort
        pass
    out = []
    seen = set()
    for ip, mac in arp_table():
        if ip in seen or not is_lg_mac(mac):
            continue
        seen.add(ip)
        if any(speaks_webos(ip, port, timeout=probe_timeout)
               for port in WEBOS_PORTS):
            out.append((ip, canon_mac(mac)))
    return out


def env_summary() -> List[str]:
    """A compact environment fingerprint to paste into a bug report."""
    from . import __version__
    ips = local_ipv4s()
    return [
        f"App version : {__version__}",
        f"OS          : {platform.platform()}",
        f"Python      : {platform.python_version()}",
        f"Hostname    : {socket.gethostname()}",
        f"This PC IPs : {', '.join(ips) if ips else '(none detected)'}",
    ]

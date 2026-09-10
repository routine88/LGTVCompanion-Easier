"""What the display cable already knows about the TV.

The app spent this project's worst outage guessing, from network silence alone,
whether the TV was switched off - and getting it wrong, repeatedly, while the TV
sat there driving the user's desktop over HDMI. The cable had the answer the
whole time.

Every display sends EDID down the DDC lines: a manufacturer's PnP id, a product
code, a serial, and usually a human name ("LG TV SSCR2"). It is not a network
identity - EDID has no field for a MAC or an IP, and no amount of wishing adds
one - but it settles two questions the app used to answer by inference:

* **Is the TV actually on?**  A connected connector with readable EDID and DPMS
  on is a TV that is powered. "Not answering on the network" then means exactly
  that, and nothing more - which is a completely different message to give
  somebody than "it is probably switched off".
* **Which TV is mine?**  The panel on this PC's own cable is, by definition, the
  one the user means. Recording its identity at setup is what stops the app
  adopting some other LG TV that happens to be the only one answering.

Two things this deliberately does not do. It does not try to control the TV -
HDMI-CEC could, but PC graphics cards almost never wire the CEC pin, so
``/dev/cec*`` is absent on ordinary desktops and building on it would work for
nearly nobody. And it never raises: a machine with no display, a headless
server, an unreadable sysfs node all return "nothing known", because a
diagnostic that fails is worse than one that shrugs.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import List, Optional

# PnP vendor ids that mean LG. GSM is the one TVs use (it is Goldstar, LG's old
# name); the other two turn up on LG-made laptop and monitor panels, which are
# not TVs but are worth recognising rather than reporting as unknown.
LG_PNP_IDS = frozenset({"GSM", "LGD", "LPL"})

EDID_HEADER = b"\x00\xff\xff\xff\xff\xff\xff\x00"


@dataclass
class Panel:
    """One display, as described by its own EDID."""

    connector: str = ""          # "HDMI-A-1", or the Windows instance id
    vendor: str = ""             # PnP id: "GSM" for an LG TV
    product: int = 0             # vendor's product code
    serial: int = 0
    name: str = ""               # the monitor-name descriptor, when present
    powered: bool = False        # connected *and* not in a DPMS off state

    @property
    def is_lg(self) -> bool:
        return self.vendor in LG_PNP_IDS

    @property
    def identity(self) -> str:
        """A stable id for this panel, for pinning at setup.

        Deliberately not the serial: LG ships TVs with a placeholder serial
        (0x01010101 on the set this was written against), so including it would
        make the id neither unique nor stable.
        """
        return f"{self.vendor}:{self.product:04X}:{self.name}".rstrip(":")

    def describe(self) -> str:
        bits = [self.name or self.identity]
        if self.connector:
            bits.append(f"on {self.connector}")
        return " ".join(bits)


def _text_descriptor(block: bytes, tag: int) -> str:
    """One of EDID's 18-byte descriptor blocks, if it carries text of ``tag``."""
    if len(block) < 18 or block[0:3] != b"\x00\x00\x00" or block[3] != tag:
        return ""
    raw = block[5:18].split(b"\n")[0]
    return raw.decode("ascii", "replace").strip()


def parse_edid(data: bytes) -> Optional[Panel]:
    """Turn raw EDID bytes into a :class:`Panel`, or None if they aren't EDID."""
    if not data or len(data) < 128 or not data.startswith(EDID_HEADER):
        return None
    # The vendor id is three 5-bit letters packed big-endian into two bytes.
    packed = (data[8] << 8) | data[9]
    vendor = "".join(chr(((packed >> shift) & 0x1F) + ord("A") - 1)
                     for shift in (10, 5, 0))
    if not re.fullmatch(r"[A-Z]{3}", vendor):
        return None
    panel = Panel(vendor=vendor,
                  product=data[10] | (data[11] << 8),
                  serial=int.from_bytes(data[12:16], "little"))
    for offset in (54, 72, 90, 108):
        block = data[offset:offset + 18]
        panel.name = panel.name or _text_descriptor(block, 0xFC)
    return panel


# ----- Linux ------------------------------------------------------------------
def _linux_panels() -> List[Panel]:
    root = "/sys/class/drm"
    out: List[Panel] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return out
    for entry in entries:
        base = os.path.join(root, entry)
        try:
            with open(os.path.join(base, "status"), encoding="utf-8") as fh:
                if fh.read().strip() != "connected":
                    continue
            # sysfs reports st_size 0 for this node, so it must be read rather
            # than stat()ed - checking the size first finds nothing, always.
            with open(os.path.join(base, "edid"), "rb") as fh:
                data = fh.read(512)
        except OSError:
            continue
        panel = parse_edid(data)
        if panel is None:
            continue
        panel.connector = entry.split("-", 1)[1] if "-" in entry else entry
        try:
            with open(os.path.join(base, "dpms"), encoding="utf-8") as fh:
                panel.powered = fh.read().strip().lower() == "on"
        except OSError:
            panel.powered = True     # connected with EDID: assume it is awake
        out.append(panel)
    return out


# ----- Windows ----------------------------------------------------------------
def _windows_panels() -> List[Panel]:
    """EDID as Windows cached it when the monitor was last attached.

    Note the difference from Linux: this is the registry's record, so it says
    what is *known*, not necessarily what is plugged in this second. Good enough
    for identity, which is what it is used for there.
    """
    out: List[Panel] = []
    try:
        import winreg
    except ImportError:
        return out
    path = r"SYSTEM\CurrentControlSet\Enum\DISPLAY"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except OSError:
        return out
    with root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                model = winreg.EnumKey(root, i)
                with winreg.OpenKey(root, model) as model_key:
                    for j in range(winreg.QueryInfoKey(model_key)[0]):
                        instance = winreg.EnumKey(model_key, j)
                        sub = f"{model}\\{instance}\\Device Parameters"
                        try:
                            with winreg.OpenKey(root, sub) as params:
                                data, _ = winreg.QueryValueEx(params, "EDID")
                        except OSError:
                            continue
                        panel = parse_edid(bytes(data))
                        if panel is None:
                            continue
                        panel.connector = f"{model}\\{instance}"
                        panel.powered = True
                        out.append(panel)
            except OSError:
                continue
    return out


# ----- public -----------------------------------------------------------------
def panels() -> List[Panel]:
    """Every display this PC can describe. Empty where that isn't possible."""
    if os.environ.get("LGTV_EASY_NO_EDID") == "1":
        return []
    try:
        if sys.platform.startswith("win"):
            return _windows_panels()
        if sys.platform.startswith("linux"):
            return _linux_panels()
    except Exception:  # noqa: BLE001 - a diagnostic must never be the fault
        pass
    return []


def lg_panel() -> Optional[Panel]:
    """The LG display attached to this PC, if there is one.

    Prefers one that is powered: with two LG panels attached, the awake one is
    the TV the user is looking at.
    """
    found = [p for p in panels() if p.is_lg]
    if not found:
        return None
    found.sort(key=lambda p: not p.powered)
    return found[0]


def tv_is_physically_on() -> bool:
    """True when an LG display is attached to this PC and awake.

    The single fact that would have prevented this project's worst
    misdiagnosis: it is evidence *from the TV itself*, not an inference drawn
    from the network's silence.
    """
    panel = lg_panel()
    return bool(panel and panel.powered)

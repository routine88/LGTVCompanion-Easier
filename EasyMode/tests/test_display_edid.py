"""Reading the TV's identity off the display cable.

Written after the app spent two days telling a user their TV was "probably
switched off" while that same TV was driving the desktop they were reading the
message on. EDID carries no MAC and no IP - the cable simply does not have that
information - but it does settle "is the TV on" and "which TV is this", and both
of those were being guessed at from network silence and guessed wrong.
"""
import pytest

from lgtv_easy import display

# A real LG TV's EDID header block: GSM (LG), product 0xC0C8, "LG TV SSCR2".
REAL_LG_TV = bytes.fromhex(
    "00ffffffffffff001e6dc8c001010101"
    "0120010380a05a780aee91a3544c9926"
    "0f5054a1080031404540614071408180"
    "d1c0010101010 8e80030f2705a80b058".replace(" ", "")
    + "8a0040846300001e6fc200a0a0a05550"
    + "3020350040846300001e00000 0fd0018".replace(" ", "")
    + "781eff77000a2020202020200000 00fc".replace(" ", "")
    + "004c472054562053534352320a200164"
)


def test_a_real_lg_tv_edid_is_understood():
    panel = display.parse_edid(REAL_LG_TV)
    assert panel is not None
    assert panel.vendor == "GSM"          # Goldstar, i.e. LG Electronics
    assert panel.is_lg
    assert panel.product == 0xC0C8
    assert panel.name == "LG TV SSCR2"
    assert panel.identity == "GSM:C0C8:LG TV SSCR2"


def test_the_identity_leaves_out_the_serial():
    """LG ships sets with a placeholder serial (0x01010101 on the one this was
    written against), so folding it in would make the id neither unique nor
    stable across a firmware update."""
    panel = display.parse_edid(REAL_LG_TV)
    assert str(panel.serial) not in panel.identity


@pytest.mark.parametrize("data", [b"", b"\x00" * 128, b"rubbish",
                                  b"\x00\xff\xff\xff\xff\xff\xff\x00"])
def test_anything_that_is_not_edid_is_rejected(data):
    assert display.parse_edid(data) is None


def test_a_non_lg_panel_is_not_mistaken_for_a_tv():
    edid = bytearray(REAL_LG_TV)
    edid[8], edid[9] = 0x04, 0x72        # "ACR" - Acer
    panel = display.parse_edid(bytes(edid))
    assert panel is not None and not panel.is_lg


def test_nothing_raises_when_there_is_no_display(monkeypatch):
    """A headless server, a locked-down sysfs, a Windows box with no cached
    EDID: all of them must return "nothing known" rather than fail."""
    monkeypatch.setenv("LGTV_EASY_NO_EDID", "1")
    assert display.panels() == []
    assert display.lg_panel() is None
    assert display.tv_is_physically_on() is False


def test_the_powered_panel_wins_when_two_lg_displays_are_attached(monkeypatch):
    off = display.Panel(connector="HDMI-A-2", vendor="GSM", product=1,
                        name="asleep", powered=False)
    on = display.Panel(connector="HDMI-A-1", vendor="GSM", product=2,
                       name="awake", powered=True)
    monkeypatch.setattr(display, "panels", lambda: [off, on])
    assert display.lg_panel().name == "awake"
    assert display.tv_is_physically_on() is True

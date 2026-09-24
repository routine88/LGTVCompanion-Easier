"""Which socket is this PC? Asking the TV what is behind each one.

The bug this covers (2026-09-24): this PC (AMD graphics) is on HDMI 2 and a
second computer (an NVIDIA card) is on HDMI 4. The user kept working on this PC
- remotely, while the TV showed the other machine - and after a quarter of an
hour Easy Mode decided this PC had "moved" to HDMI 4. From then on it blanked
the TV whenever it went idle *while the other computer was on screen*, which is
exactly what the guard exists to prevent.

The TV knows better: getExternalInputList says a cable is still in HDMI 2 (so
nothing moved), and that the device on HDMI 4 announces itself as an NVIDIA
GeForce - which this PC is not.
"""
import logging

from lgtv_easy.config import Config, Device
from lgtv_easy.daemon import (INPUT_MOVED_SECONDS, INPUT_RELEARN_SECONDS,
                              STATE_ON, Daemon)
from lgtv_easy.display import _linux_gpu_vendors, gpu_vendor_from_spd
from lgtv_easy.mock_tv import MockTV
from lgtv_easy.webos import InputSource, WebOSClient, parse_input_list


def _socket(port, plugged=True, vendor="", product=""):
    """One getExternalInputList entry, in the shape the real TV sends."""
    dev = {"id": f"HDMI_{port}", "label": f"HDMI {port}", "port": port,
           "connected": plugged, "hdmiPlugIn": plugged,
           "hdmiSignalExist": plugged,
           "appId": f"com.webos.app.hdmi{port}"}
    if vendor:
        dev.update(spdVendorName=vendor, spdProductDescription=product,
                   spdSourceDeviceInfo="PC general")
    return dev


# The user's TV, as it answered on 2026-09-24.
USERS_TV = [_socket(1, plugged=False), _socket(2), _socket(3, plugged=False),
            _socket(4, vendor="NVIDIA", product="GeForce RTX 5070")]


class _Log(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _make(tv: MockTV, input_id="", gpus=("amd",), minutes=7.0):
    cfg = Config(idle_minutes=minutes)
    cfg.device = Device(name="t", ip="127.0.0.1", key="MOCK-KEY-0001",
                        input_id=input_id)
    cfg.save = lambda: None  # never write the developer's real config

    def factory():
        c = WebOSClient("127.0.0.1")
        c._url = lambda: tv.url
        return c

    log = _Log()
    logger = logging.getLogger(f"test-input-identity-{id(log)}")
    logger.addHandler(log)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    idle_box = {"v": 0.0}
    clock_box = {"v": 1000.0}
    d = Daemon(cfg, client_factory=factory,
               gpu_vendors_fn=lambda: frozenset(gpus),
               idle_fn=lambda: idle_box["v"],
               clock_fn=lambda: clock_box["v"],
               locator_fn=lambda mac: None,
               wol_fn=lambda deep: None,
               media_fn=lambda: False,
               logger=logger)
    d._idle_box, d._clock_box, d._log = idle_box, clock_box, log
    return d


def _use(d: Daemon, seconds: float, step: float = 10.0) -> None:
    """The user keeps working at this PC for ``seconds``."""
    t = 0.0
    while t < seconds:
        d._clock_box["v"] += step
        d._idle_box["v"] = 0.0
        d.tick()
        t += step


# ----- the reported bug --------------------------------------------------
def test_working_here_while_the_other_pc_is_on_screen_never_moves_this_pc():
    with MockTV(require_pairing=False) as tv:
        tv.input_list = USERS_TV
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv)
        _use(d, 30)
        assert d.config.device.input_id == "hdmi2"

        tv.foreground_app = "com.webos.app.hdmi4"      # the NVIDIA machine
        _use(d, 2 * INPUT_RELEARN_SECONDS)
        assert d.config.device.input_id == "hdmi2"


def test_does_not_blank_the_other_pc_after_a_long_stretch_of_remote_use():
    """The user-visible half: the TV must not go dark on the other machine."""
    with MockTV(require_pairing=False) as tv:
        tv.input_list = USERS_TV
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv, minutes=5.0)
        _use(d, 30)
        tv.foreground_app = "com.webos.app.hdmi4"
        _use(d, 2 * INPUT_RELEARN_SECONDS)

        d._idle_box["v"] = 6 * 60                      # user steps away
        for _ in range(10):
            d._clock_box["v"] += 60
            d._idle_box["v"] += 60
            d.tick()
        assert tv.screen_on is True
        assert d.sleeps == 0
        assert d.screen_state == STATE_ON


def test_never_learns_another_makers_graphics_card_as_this_pc():
    with MockTV(require_pairing=False) as tv:
        tv.input_list = USERS_TV
        tv.foreground_app = "com.webos.app.hdmi4"
        d = _make(tv)
        _use(d, 60)
        assert d.config.device.input_id == ""
        assert any("another computer" in m and "NVIDIA" in m
                   for m in d._log.lines)

        tv.foreground_app = "com.webos.app.hdmi2"
        _use(d, 20)
        assert d.config.device.input_id == "hdmi2"


def test_forgets_a_saved_input_that_the_tv_proves_is_another_pc():
    """Heals a config already poisoned by the old rule."""
    with MockTV(require_pairing=False) as tv:
        tv.input_list = USERS_TV
        tv.foreground_app = "com.webos.app.hdmi4"
        d = _make(tv, input_id="hdmi4")
        d._idle_box["v"] = 10 * 60
        d._clock_box["v"] += 10
        d.tick()
        assert d.config.device.input_id == ""
        assert tv.screen_on is True, "blanked the other PC on a stale belief"

        tv.foreground_app = "com.webos.app.hdmi2"
        _use(d, 20)
        assert d.config.device.input_id == "hdmi2"


def test_leaves_the_other_pc_alone_even_before_learning_its_own_input():
    with MockTV(require_pairing=False) as tv:
        tv.input_list = USERS_TV
        tv.foreground_app = "com.webos.app.hdmi4"
        d = _make(tv)
        d._idle_box["v"] = 10 * 60
        for _ in range(3):
            d._clock_box["v"] += 30
            d._idle_box["v"] += 30
            d.tick()
        assert tv.screen_on is True
        assert d.sleeps == 0


# ----- a cable that really moved -----------------------------------------
def test_follows_a_moved_cable_once_the_old_socket_is_empty():
    with MockTV(require_pairing=False) as tv:
        tv.input_list = USERS_TV
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv)
        _use(d, 20)
        assert d.config.device.input_id == "hdmi2"

        # Cable moved from HDMI 2 to HDMI 3.
        tv.input_list = [_socket(1, plugged=False), _socket(2, plugged=False),
                         _socket(3), USERS_TV[3]]
        tv.foreground_app = "com.webos.app.hdmi3"
        _use(d, INPUT_MOVED_SECONDS / 2)
        assert d.config.device.input_id == "hdmi2", "followed it too eagerly"
        _use(d, INPUT_MOVED_SECONDS)
        assert d.config.device.input_id == "hdmi3"


def test_an_occupied_old_socket_holds_even_without_graphics_info():
    """Two PCs whose cards send no identity: the TV still says both sockets are
    in use, so nothing moved and the rival is the other machine."""
    with MockTV(require_pairing=False) as tv:
        tv.input_list = [_socket(1), _socket(2)]
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv)
        _use(d, 20)
        tv.foreground_app = "com.webos.app.hdmi1"
        _use(d, 2 * INPUT_RELEARN_SECONDS)
        assert d.config.device.input_id == "hdmi2"


# ----- no false vetoes ---------------------------------------------------
def test_same_maker_is_not_mistaken_for_another_pc():
    with MockTV(require_pairing=False) as tv:
        tv.input_list = USERS_TV
        tv.foreground_app = "com.webos.app.hdmi4"
        d = _make(tv, gpus=("intel", "nvidia"))        # hybrid laptop
        _use(d, 20)
        assert d.config.device.input_id == "hdmi4"


def test_unknown_own_graphics_is_no_evidence():
    with MockTV(require_pairing=False) as tv:
        tv.input_list = USERS_TV
        tv.foreground_app = "com.webos.app.hdmi4"
        d = _make(tv, gpus=())
        _use(d, 20)
        assert d.config.device.input_id == "hdmi4"


# ----- TVs that won't list their sockets ----------------------------------
def test_two_stray_sightings_far_apart_are_not_a_quarter_hour_of_use():
    """The old rule's hole: a rival seen once, then again 15 minutes later,
    was taken as "held for 15 minutes"."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        d = _make(tv)
        _use(d, 20)
        tv.foreground_app = "com.webos.app.hdmi1"
        _use(d, 10)                                    # one glance
        d._idle_box["v"] = 10 * 60
        d._clock_box["v"] += INPUT_RELEARN_SECONDS
        d.tick()
        _use(d, 10)                                    # another, much later
        assert d.config.device.input_id == "hdmi2"


def test_learning_needs_the_user_at_the_keyboard_now_not_minutes_ago():
    """Minutes-old input is inside a long idle timeout but doesn't mean the
    user is looking at whatever is on screen this second."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi1"
        d = _make(tv, minutes=55.0)
        d._idle_box["v"] = 4 * 60
        d._clock_box["v"] += 10
        d.tick()
        assert d.config.device.input_id == ""


# ----- parsing -----------------------------------------------------------
def test_parses_the_tvs_input_list():
    got = parse_input_list({"devices": USERS_TV})
    assert got["hdmi2"] == InputSource(plugged=True)
    assert got["hdmi1"].plugged is False
    assert got["hdmi4"].vendor == "NVIDIA"
    assert got["hdmi4"].describe() == "NVIDIA GeForce RTX 5070"


def test_a_list_without_devices_is_unknown_not_empty():
    assert parse_input_list({"returnValue": True}) is None
    assert parse_input_list({"devices": []}) == {}


def test_input_id_from_the_socket_id_when_appid_is_missing():
    got = parse_input_list({"devices": [{"id": "HDMI_3", "connected": True}]})
    assert "hdmi3" in got


def test_recognises_graphics_makers_by_their_spd_name():
    assert gpu_vendor_from_spd("NVIDIA") == "nvidia"
    assert gpu_vendor_from_spd("AMD") == "amd"
    assert gpu_vendor_from_spd("ATI Tech") == "amd"
    assert gpu_vendor_from_spd("Intel") == "intel"
    assert gpu_vendor_from_spd("SONY") == ""
    assert gpu_vendor_from_spd("") == ""


def test_reads_this_pcs_graphics_makers_from_pci(tmp_path):
    def dev(name, cls, vendor):
        d = tmp_path / name
        d.mkdir()
        (d / "class").write_text(cls + "\n")
        (d / "vendor").write_text(vendor + "\n")

    dev("0000:c5:00.0", "0x030000", "0x1002")          # AMD display
    dev("0000:c5:00.1", "0x040300", "0x1002")          # AMD audio: not a GPU
    dev("0000:01:00.0", "0x030200", "0x10de")          # NVIDIA 3D controller
    dev("0000:00:14.0", "0x0c0330", "0x8086")          # Intel USB: not a GPU
    assert _linux_gpu_vendors(str(tmp_path)) == frozenset({"amd", "nvidia"})
    assert _linux_gpu_vendors(str(tmp_path / "missing")) == frozenset()

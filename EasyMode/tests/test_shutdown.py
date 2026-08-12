"""The `off` command and its shutdown-hook gating."""
from lgtv_easy import cli
from lgtv_easy.config import Config, Device
from lgtv_easy.mock_tv import MockTV


def _configure(tv, tmp_path, monkeypatch, off_on_shutdown=True, input_id=""):
    monkeypatch.setenv("LGTV_EASY_HOME", str(tmp_path))
    cfg = Config(setup_complete=True, tv_off_on_shutdown=off_on_shutdown)
    cfg.device = Device(name="mock", ip=f"127.0.0.1:{tv.port}", key="MOCK-KEY-0001",
                        input_id=input_id)
    cfg.save()


def test_off_command_powers_off_the_tv(tmp_path, monkeypatch):
    with MockTV(require_pairing=False) as tv:
        _configure(tv, tmp_path, monkeypatch)
        assert cli.main(["off"]) == 0
        assert tv.powered_on is False


def test_off_only_if_configured_skips_when_disabled(tmp_path, monkeypatch):
    with MockTV(require_pairing=False) as tv:
        _configure(tv, tmp_path, monkeypatch, off_on_shutdown=False)
        assert cli.main(["off", "--only-if-configured"]) == 0
        assert tv.powered_on is True  # honoured the setting, did nothing


def test_off_only_if_configured_powers_off_when_enabled(tmp_path, monkeypatch):
    with MockTV(require_pairing=False) as tv:
        _configure(tv, tmp_path, monkeypatch, off_on_shutdown=True)
        assert cli.main(["off", "--only-if-configured"]) == 0
        assert tv.powered_on is False


def test_shutdown_leaves_a_tv_showing_another_pc_alone(tmp_path, monkeypatch):
    """This PC shutting down says nothing about the computer currently on
    screen, so the shutdown hook must not cut the whole TV."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi1"  # the other computer
        _configure(tv, tmp_path, monkeypatch, input_id="hdmi2")
        assert cli.main(["off", "--only-if-configured"]) == 0
        assert tv.powered_on is True


def test_shutdown_powers_off_when_this_pc_is_the_one_on_screen(tmp_path, monkeypatch):
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi2"
        _configure(tv, tmp_path, monkeypatch, input_id="hdmi2")
        assert cli.main(["off", "--only-if-configured"]) == 0
        assert tv.powered_on is False


def test_explicit_off_command_ignores_which_input_is_showing(tmp_path, monkeypatch):
    """`lgtv-easy off` is the user asking directly - honour it either way."""
    with MockTV(require_pairing=False) as tv:
        tv.foreground_app = "com.webos.app.hdmi1"
        _configure(tv, tmp_path, monkeypatch, input_id="hdmi2")
        assert cli.main(["off"]) == 0
        assert tv.powered_on is False

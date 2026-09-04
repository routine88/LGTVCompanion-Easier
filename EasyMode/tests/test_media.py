"""'Is anything playing on this PC?' - the detector behind the mid-film hold.

The daemon calls into this every poll and never guards the call, so the contract
these enforce is: always a bool, never an exception, and "no idea" means False
so the timers behave exactly as they did before the feature existed.

Nothing here touches a real session bus - CI has none, and the developer's own
desktop would make the answers depend on whatever happened to be playing.
"""
import pytest

from lgtv_easy import media


@pytest.fixture(autouse=True)
def _fresh_backend(monkeypatch):
    """Every test picks its own backend, and none may shell out to gdbus."""
    media.reset_backend()
    monkeypatch.delenv("LGTV_EASY_FAKE_MEDIA", raising=False)
    monkeypatch.setattr(media, "_gdbus_path", lambda: None)
    yield
    media.reset_backend()


# ----- reply parsing --------------------------------------------------------
def test_parse_bool_reads_a_gdbus_reply():
    assert media._parse_bool("(true,)") is True
    assert media._parse_bool("(false,)") is False
    assert media._parse_bool("") is None
    assert media._parse_bool(None) is None
    assert media._parse_bool("nonsense") is None


def test_parse_strings_pulls_every_quoted_value():
    reply = "([objectpath '/org/gnome/SessionManager/Inhibitor1', '/x/2'],)"
    assert media._parse_strings(reply) == [
        "/org/gnome/SessionManager/Inhibitor1", "/x/2"]
    assert media._parse_strings("(<'Playing'>,)") == ["Playing"]
    assert media._parse_strings(None) == []


def test_app_ids_are_tidied_for_humans():
    # Snap and flatpak register a doubled id; a desktop file keeps its suffix.
    assert media._tidy_app_id("firefox_firefox") == "firefox"
    assert media._tidy_app_id("vlc.desktop") == "vlc"
    assert media._tidy_app_id("mpv") == "mpv"
    # An id that merely contains an underscore keeps both halves.
    assert media._tidy_app_id("org.gnome_shell") == "org.gnome_shell"


# ----- a fake session bus ---------------------------------------------------
class FakeBus:
    """Answers the exact (interface, member) pairs a backend asks for."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, dest, path, interface, member, signature="", args=(),
                 timeout=2.0):
        self.calls.append((dest, path, f"{interface}.{member}", tuple(args)))
        for key, value in self.answers.items():
            if callable(key):
                if key(dest, path, interface, member, args):
                    return value
            elif key == f"{interface}.{member}":
                return value
        return None      # "the native path can't answer" - as the real one does


def _install(monkeypatch, answers, platform="linux"):
    bus = FakeBus(answers)
    monkeypatch.setattr(media._dbus, "session_call", bus)
    monkeypatch.setattr(media.sys, "platform", platform)
    return bus


# ----- GNOME ---------------------------------------------------------------
GNOME_IS_INHIBITED = "org.gnome.SessionManager.IsInhibited"


def test_gnome_backend_reports_the_idle_inhibitor(monkeypatch):
    _install(monkeypatch, {GNOME_IS_INHIBITED: True})
    assert media.backend_name() == "gnome-inhibit"
    assert media.is_available() is True
    assert media.is_playing() is True


def test_gnome_backend_is_selected_even_when_nothing_is_playing(monkeypatch):
    # False is a real answer ("nothing inhibiting"), unlike None ("can't ask").
    _install(monkeypatch, {GNOME_IS_INHIBITED: False})
    assert media.backend_name() == "gnome-inhibit"
    assert media.is_playing() is False


def test_gnome_backend_passes_the_idle_flag(monkeypatch):
    bus = _install(monkeypatch, {GNOME_IS_INHIBITED: True})
    media.is_playing()
    assert any(call[2] == GNOME_IS_INHIBITED and call[3] == (media.INHIBIT_IDLE,)
               for call in bus.calls), bus.calls


def test_gnome_detail_names_the_app_and_skips_non_idle_inhibitors(monkeypatch):
    inhibitor = "org.gnome.SessionManager.Inhibitor"

    def answer(dest, path, interface, member, args):
        return interface == inhibitor and path == "/i/2"

    _install(monkeypatch, {
        GNOME_IS_INHIBITED: True,
        "org.gnome.SessionManager.GetInhibitors": ["/i/1", "/i/2"],
        # /i/1 is a logout inhibitor (flag 1): nothing to do with the screen.
        lambda d, p, i, m, a: p == "/i/1" and m == "GetFlags": 1,
        lambda d, p, i, m, a: p == "/i/2" and m == "GetFlags": media.INHIBIT_IDLE,
        lambda d, p, i, m, a: p == "/i/2" and m == "GetAppId": "firefox_firefox",
        lambda d, p, i, m, a: p == "/i/2" and m == "GetReason": "Playing video",
    })
    assert media.playing_detail() == "firefox: Playing video"


# ----- KDE / other freedesktop desktops ------------------------------------
def test_freedesktop_backend_used_when_gnome_is_absent(monkeypatch):
    _install(monkeypatch, {
        "org.freedesktop.PowerManagement.Inhibit.HasInhibit": True})
    assert media.backend_name() == "freedesktop-inhibit"
    assert media.is_playing() is True


# ----- MPRIS, the last-resort fallback --------------------------------------
def _mpris_bus(monkeypatch, statuses):
    names = ["org.freedesktop.DBus", "org.gnome.Shell"]
    names += [f"org.mpris.MediaPlayer2.{n}" for n in statuses]

    def answer_status(dest, path, interface, member, args):
        return member == "Get" and dest.startswith(media._MPRIS_PREFIX)

    answers = {"org.freedesktop.DBus.ListNames": names}
    for name, status in statuses.items():
        answers[(lambda n: (lambda d, p, i, m, a:
                            m == "Get" and d.endswith("." + n)))(name)] = status
    return _install(monkeypatch, answers)


def test_mpris_backend_sees_a_playing_player(monkeypatch):
    _mpris_bus(monkeypatch, {"mpv": "Playing"})
    assert media.backend_name() == "mpris"
    assert media.is_playing() is True
    assert media.playing_detail() == "mpv"


def test_mpris_backend_ignores_a_paused_player(monkeypatch):
    _mpris_bus(monkeypatch, {"vlc": "Paused"})
    assert media.backend_name() == "mpris"
    assert media.is_playing() is False


# ----- nothing at all -------------------------------------------------------
def test_no_backend_reports_nothing_playing(monkeypatch):
    _install(monkeypatch, {})     # a desktop that answers none of the above
    assert media.backend_name() == "none"
    assert media.is_available() is False
    assert media.is_playing() is False
    assert media.playing_detail() == ""


def test_is_playing_never_raises(monkeypatch):
    def explode(*_a, **_kw):
        raise RuntimeError("the bus went away mid-poll")

    monkeypatch.setattr(media, "_BACKEND", ("broken", explode, explode))
    assert media.is_playing() is False
    assert media.playing_detail() == ""


def test_the_environment_can_force_the_answer(monkeypatch):
    # The same escape hatch idle detection has, for headless runs and tests.
    monkeypatch.setattr(media, "_BACKEND", ("none", lambda: False, lambda: ""))
    monkeypatch.setenv("LGTV_EASY_FAKE_MEDIA", "1")
    assert media.is_playing() is True
    assert media.is_available() is True
    monkeypatch.setenv("LGTV_EASY_FAKE_MEDIA", "0")
    assert media.is_playing() is False


def test_windows_selection_falls_back_cleanly_when_it_cannot_ask(monkeypatch):
    """The Windows path uses ctypes against powrprof.dll, which cannot be
    exercised off Windows - so what is pinned here is the failure mode: a
    backend that can't be built must leave the timers exactly as they were."""
    monkeypatch.setattr(media.sys, "platform", "win32")
    monkeypatch.setattr(media, "_windows_backend",
                        lambda: (_ for _ in ()).throw(OSError("no powrprof")))
    assert media.backend_name() == "none"
    assert media.is_playing() is False


def test_windows_backend_reads_the_display_request_flag(monkeypatch):
    """The decode itself, with the OS call stubbed: only ES_DISPLAY_REQUIRED
    counts - ES_SYSTEM_REQUIRED alone means a background job, not a video."""
    monkeypatch.setattr(media.sys, "platform", "win32")
    value = {"state": media._ES_DISPLAY_REQUIRED | 0x80000000}   # + ES_CONTINUOUS

    def fake_windows_backend():
        return ("windows-power-request",
                lambda: bool(value["state"] & media._ES_DISPLAY_REQUIRED),
                lambda: "")

    monkeypatch.setattr(media, "_windows_backend", fake_windows_backend)
    assert media.backend_name() == "windows-power-request"
    assert media.is_playing() is True
    value["state"] = 0x00000001 | 0x80000000        # ES_SYSTEM_REQUIRED only
    assert media.is_playing() is False

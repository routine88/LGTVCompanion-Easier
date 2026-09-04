"""Unit tests for the tiny hand-rolled D-Bus client (lgtv_easy._dbus).

These cover the pure marshalling/parsing helpers and the never-raise contract
without needing a live session bus (CI has none). The live wire format is
exercised end-to-end by the idle integration on a real GNOME box.
"""
import struct

import pytest

from lgtv_easy import _dbus


def test_pad_to_alignment():
    assert _dbus._pad(0, 8) == 0
    assert _dbus._pad(1, 8) == 7
    assert _dbus._pad(8, 8) == 0
    assert _dbus._pad(5, 4) == 3


def test_marshal_string_and_signature():
    assert _dbus._marshal_string("ab") == struct.pack("<I", 2) + b"ab\x00"
    assert _dbus._marshal_signature("t") == b"\x01t\x00"
    assert _dbus._marshal_signature("") == b"\x00\x00"


def test_decode_uint_handles_each_supported_type():
    dec = _dbus._Connection._decode_uint
    assert dec("u", struct.pack("<I", 4242)) == 4242
    assert dec("t", struct.pack("<Q", 2 ** 40)) == 2 ** 40
    assert dec("i", struct.pack("<i", -5)) == -5
    assert dec("x", struct.pack("<q", -7)) == -7


def test_decode_uint_rejects_unknown_signature():
    with pytest.raises(_dbus._DBusErrorReply):
        _dbus._Connection._decode_uint("s", b"\x00\x00\x00\x00")


def test_parse_fields_extracts_signature_and_reply_serial():
    # Build a header-field array exactly as a bus would: REPLY_SERIAL (code 5,
    # type 'u') = 42 followed by SIGNATURE (code 8, type 'g') = 't'.
    raw = struct.pack("<B", 5) + _dbus._marshal_signature("u")
    raw += b"\x00" * _dbus._pad(len(raw), 4)
    raw += struct.pack("<I", 42)
    raw += b"\x00" * _dbus._pad(len(raw), 8)          # next field struct is 8-aligned
    raw += struct.pack("<B", 8) + _dbus._marshal_signature("g")
    raw += _dbus._marshal_signature("t")             # the variant value is a signature

    sig, reply_serial, error = _dbus._Connection._parse_fields(raw)
    assert sig == "t"
    assert reply_serial == 42
    assert error is None


def test_session_get_uint_never_raises_and_disables_without_a_bus(monkeypatch):
    # Point at a socket that doesn't exist: every call must return None (not
    # raise), and after a few failures the native path disables itself so it
    # stops adding cost on a system where it simply can't connect.
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS",
                       "unix:path=/nonexistent/lgtv-test-bus-socket")
    conn = _dbus._Connection()
    for _ in range(_dbus._MAX_CONN_FAILURES):
        assert conn.get_uint("a.b", "/a", "a.b", "C") is None
    assert conn._disabled is True
    assert conn.get_uint("a.b", "/a", "a.b", "C") is None


# ----- calls with arguments, and the wider reply types they come back as -----
# The idle query is a no-arg method returning a number. Asking the desktop
# whether anything is playing needs more: an argument (the inhibit flag), and
# replies that are booleans, strings, arrays and variants.

def test_sig_len_walks_compound_types():
    assert _dbus._sig_len("as", 0) == 2          # one type: array of strings
    assert _dbus._sig_len("as", 1) == 1          # ...whose element is 's'
    assert _dbus._sig_len("(bs)u", 0) == 4
    assert _dbus._sig_len("a{sv}", 0) == 5


def test_marshal_body_packs_uint_and_string_arguments():
    assert _dbus._marshal_body("u", (8,)) == struct.pack("<I", 8)
    # The second string is padded out to its own 4-byte alignment.
    assert _dbus._marshal_body("ss", ("a", "bb")) == (
        struct.pack("<I", 1) + b"a\x00"
        + b"\x00\x00"
        + struct.pack("<I", 2) + b"bb\x00")


def test_marshal_body_refuses_what_it_cannot_pack():
    # Better to fail one call than to desynchronise the shared connection with
    # a body the bus will read as the wrong length.
    with pytest.raises(ValueError):
        _dbus._marshal_body("d", (1.5,))
    with pytest.raises(ValueError):
        _dbus._marshal_body("ss", ("only-one",))


def test_decode_reply_reads_the_types_the_desktop_answers_with():
    assert _dbus._decode_reply("b", struct.pack("<I", 1)) is True
    assert _dbus._decode_reply("b", struct.pack("<I", 0)) is False
    assert _dbus._decode_reply("s", struct.pack("<I", 3) + b"mpv\x00") == "mpv"
    assert _dbus._decode_reply("", b"") is None


def test_decode_reply_reads_an_array_of_strings():
    first = struct.pack("<I", 1) + b"x\x00"           # 6 bytes
    padding = b"\x00" * 2                             # next element is 4-aligned
    second = struct.pack("<I", 2) + b"yy\x00"         # 7 bytes
    content = first + padding + second
    body = struct.pack("<I", len(content)) + content
    assert _dbus._decode_reply("as", body) == ["x", "yy"]


def test_decode_reply_unwraps_a_variant():
    # What Properties.Get returns: a variant wrapping the actual value.
    body = _dbus._marshal_signature("s")              # 3 bytes: the inner type
    body += b"\x00"                                   # pad to the string's 4
    body += struct.pack("<I", 7) + b"Playing\x00"
    assert _dbus._decode_reply("v", body) == "Playing"


def test_decode_reply_rejects_a_type_it_does_not_speak():
    with pytest.raises(_dbus._DBusErrorReply):
        _dbus._decode_reply("h", b"\x00\x00\x00\x00")   # a unix fd


class _CapturingSocket:
    def __init__(self):
        self.sent = b""

    def sendall(self, data):
        self.sent += data


def test_a_call_with_arguments_declares_its_signature():
    """A body without a SIGNATURE header field is a malformed message, and the
    bus answers with an error instead of the value - so this is the difference
    between the feature working and silently never firing."""
    conn = _dbus._Connection()
    sock = _CapturingSocket()
    conn._sock = sock
    conn._send_method_call("org.gnome.SessionManager", "/org/gnome/SessionManager",
                           "org.gnome.SessionManager", "IsInhibited", "u", (8,))

    body_len, _serial, fields_len = struct.unpack("<III", sock.sent[4:16])
    assert body_len == 4
    sig, _reply_serial, _error = _dbus._Connection._parse_fields(
        sock.sent[16:16 + fields_len])
    assert sig == "u", "the argument signature must be declared in the header"
    assert sock.sent[-4:] == struct.pack("<I", 8), "the argument itself is last"
    # The body starts on an 8-byte boundary, after the header fields' padding.
    assert (len(sock.sent) - body_len) % 8 == 0


def test_session_call_never_raises_without_a_bus(monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS",
                       "unix:path=/nonexistent/lgtv-easy-no-such-bus")
    conn = _dbus._Connection()
    for _ in range(5):
        assert conn.call("org.example", "/", "org.example", "Method",
                         "u", (8,), timeout=0.2) is None
    assert conn._disabled, "it must stop trying where it plainly cannot connect"

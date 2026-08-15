"""Create Windows shortcuts (.lnk) that the taskbar treats as a real app.

``WScript.Shell`` can make a shortcut in three lines, so why this? Because it
cannot set one property, and that property is the whole point of the exercise:

    System.AppUserModel.ID

An application that calls ``SetCurrentProcessExplicitAppUserModelID`` - which
Easy Mode does, so its windows stop being filed under python.exe - must stamp
the *same* id onto its shortcuts. Windows matches a running window to a pinned
or Start Menu shortcut by comparing the two ids; if the shortcut has none, it
falls back to the target path, they disagree, and pinning the app produces a
second, dead taskbar button beside the live one.

So: IShellLinkW for the shortcut, IPropertyStore for the id, IPersistFile to
save - through raw ctypes, because the build must not need pywin32 or comtypes.
Nothing here is imported on other platforms.
"""
from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_int, c_ulong, c_void_p, c_wchar_p
from ctypes.wintypes import DWORD, WORD

# ----- COM plumbing ---------------------------------------------------------
ole32 = ctypes.OleDLL("ole32")
shlwapi = ctypes.OleDLL("shlwapi")

CLSCTX_INPROC_SERVER = 1
COINIT_APARTMENTTHREADED = 0x2
SW_SHOWNORMAL = 1
MAX_PATH = 260


class GUID(ctypes.Structure):
    _fields_ = [("Data1", DWORD), ("Data2", WORD), ("Data3", WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text: str):
        super().__init__()
        ole32.CLSIDFromString(text, byref(self))


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


VT_LPWSTR = 31


class PROPVARIANT(ctypes.Structure):
    # The union begins at offset 8 in both 32- and 64-bit builds; we only ever
    # store a string in it, and the trailing pointer pads the struct out to the
    # size the SDK declares (16 bytes on x86, 24 on x64).
    _fields_ = [("vt", WORD), ("reserved", WORD * 3),
                ("pwszVal", c_void_p), ("_pad", c_void_p)]


CLSID_ShellLink = "{00021401-0000-0000-C000-000000000046}"
IID_IShellLinkW = "{000214F9-0000-0000-C000-000000000046}"
IID_IPersistFile = "{0000010B-0000-0000-C000-000000000046}"
IID_IPropertyStore = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"
FMTID_AppUserModel = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
PID_APPUSERMODEL_ID = 5

# Vtable slots, counted from IUnknown's three. Hand-written because there is no
# type library to read them from; each list mirrors the order the methods are
# declared in the SDK headers.
_SHELLLINK = {"SetDescription": 7, "SetWorkingDirectory": 9, "SetArguments": 11,
              "SetShowCmd": 15, "SetIconLocation": 17, "SetPath": 20}
_PERSISTFILE = {"Save": 6}
_PROPERTYSTORE = {"SetValue": 6, "Commit": 7}


def _method(interface, slot, restype, *argtypes):
    """Bind vtable entry ``slot`` of ``interface`` as a callable."""
    vtable = ctypes.cast(interface, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtable[slot])


def _release(interface) -> None:
    if interface:
        _method(interface, 2, c_ulong)(interface)   # IUnknown::Release


def _query(interface, iid: str):
    out = c_void_p()
    qi = _method(interface, 0, c_int, POINTER(GUID), POINTER(c_void_p))
    qi(interface, byref(GUID(iid)), byref(out))     # OleDLL raises on failure
    return out


# ----- the public bit -------------------------------------------------------
def create_shortcut(path, target, *, arguments="", working_dir="",
                    icon="", icon_index=0, description="", app_id=""):
    """Write a .lnk at ``path`` pointing at ``target``.

    ``app_id`` is the AppUserModelID described above; pass the same string the
    application declares at startup. Raises OSError on failure - the caller
    decides whether a missing shortcut is fatal (it isn't, quite).
    """
    path, target = str(path), str(target)
    try:
        ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    except OSError:
        pass  # already initialised on this thread, in some other mode

    link = c_void_p()
    ole32.CoCreateInstance(byref(GUID(CLSID_ShellLink)), None,
                           CLSCTX_INPROC_SERVER, byref(GUID(IID_IShellLinkW)),
                           byref(link))
    store = persist = None
    try:
        call = lambda name, *a: _method(  # noqa: E731 - a table lookup, not logic
            link, _SHELLLINK[name], c_int, *[c_wchar_p] * len(a))(link, *a)
        call("SetPath", target)
        if arguments:
            call("SetArguments", arguments)
        if working_dir:
            call("SetWorkingDirectory", working_dir)
        if description:
            # Windows shows this as the shortcut's tooltip; it is capped at 260
            # characters and silently fails above that.
            call("SetDescription", description[:MAX_PATH - 1])
        if icon:
            _method(link, _SHELLLINK["SetIconLocation"], c_int,
                    c_wchar_p, c_int)(link, icon, icon_index)
        _method(link, _SHELLLINK["SetShowCmd"], c_int, c_int)(link, SW_SHOWNORMAL)

        if app_id:
            store = _query(link, IID_IPropertyStore)
            key = PROPERTYKEY()
            key.fmtid = GUID(FMTID_AppUserModel)
            key.pid = PID_APPUSERMODEL_ID
            # InitPropVariantFromString is an inline in the SDK headers, not an
            # export, so build the PROPVARIANT by hand: SHStrDupW allocates the
            # copy with CoTaskMemAlloc, which is what PropVariantClear frees.
            value = PROPVARIANT()
            text = c_void_p()
            shlwapi.SHStrDupW(app_id, byref(text))
            value.vt = VT_LPWSTR
            value.pwszVal = text
            try:
                _method(store, _PROPERTYSTORE["SetValue"], c_int,
                        POINTER(PROPERTYKEY), POINTER(PROPVARIANT))(
                            store, byref(key), byref(value))
                _method(store, _PROPERTYSTORE["Commit"], c_int)(store)
            finally:
                ole32.PropVariantClear(byref(value))

        persist = _query(link, IID_IPersistFile)
        _method(persist, _PERSISTFILE["Save"], c_int,
                c_wchar_p, c_int)(persist, path, True)
    finally:
        _release(store)
        _release(persist)
        _release(link)
    return path


def notify_shell_changed() -> None:
    """Tell Explorer new shortcuts exist, so the desktop redraws at once
    instead of when it next feels like it."""
    SHCNE_ASSOCCHANGED, SHCNF_IDLIST = 0x08000000, 0x0000
    try:
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST,
                                             None, None)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


# Note for the next person who goes looking: there is no "pin to taskbar" call
# to add here. Windows removed the programmatic pin in 8.1, which is why the
# installer asks the user to do it by hand instead of quietly not working.


if __name__ == "__main__":  # manual check: python shortcuts.py <out.lnk> <target>
    import sys
    print(create_shortcut(sys.argv[1], sys.argv[2], app_id="Test.AppId",
                          description="test shortcut"))

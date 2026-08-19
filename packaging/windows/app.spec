# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the Windows application.

Produces one folder holding two executables that share a single copy of Python
and the runtime:

    LGTV Companion Easy Mode.exe   windowed - the desktop/Start Menu icon
    lgtv-easy.exe                  console  - the same commands, for a terminal

Two, because one executable cannot be both: a windowed build has no console to
print ``status`` into, and a console build flashes a black window every time the
watcher starts at login. They are built from one Analysis, so the pair costs
almost nothing over a single exe.

Build with packaging/windows/build.ps1 (which also builds the installer), or
directly:  pyinstaller --clean --noconfirm packaging/windows/app.spec
"""
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo)

HERE = Path(SPECPATH).resolve()               # noqa: F821 - injected by PyInstaller
REPO = HERE.parents[1]
APP = REPO / "EasyMode"
PKG = APP / "lgtv_easy"
ASSETS = PKG / "assets"
ICON = ASSETS / "icon.ico"

GUI_NAME = "LGTV Companion Easy Mode"
CLI_NAME = "lgtv-easy"

_source = (PKG / "__init__.py").read_text(encoding="utf-8")
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', _source).group(1)
_parts = [int(p) for p in VERSION.split(".")]
VERSION_TUPLE = tuple((_parts + [0, 0, 0, 0])[:4])


def version_resource(description: str, exe_name: str) -> VSVersionInfo:
    """The Properties -> Details tab of the .exe.

    Windows shows FileDescription as the process name in Task Manager, so an
    empty one here is why unbranded builds show up as "python".
    """
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=VERSION_TUPLE, prodvers=VERSION_TUPLE,
                          mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                          subtype=0x0),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "LGTV Companion contributors"),
                StringStruct("FileDescription", description),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", exe_name),
                StringStruct("LegalCopyright", "MIT licensed"),
                StringStruct("OriginalFilename", exe_name + ".exe"),
                StringStruct("ProductName", GUI_NAME),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ])


a = Analysis(
    [str(HERE / "app_entry.py")],
    pathex=[str(APP)],
    binaries=[],
    datas=[(str(ASSETS / "*"), "lgtv_easy/assets")],
    # Everything the app imports lazily inside a function. PyInstaller does find
    # most of these by walking the bytecode; naming them costs nothing and means
    # a missed one can never ship as a crash on some rarely-used screen.
    hiddenimports=[
        "lgtv_easy.applog", "lgtv_easy.autostart", "lgtv_easy.branding",
        "lgtv_easy.cli", "lgtv_easy.config", "lgtv_easy.daemon",
        "lgtv_easy.discovery", "lgtv_easy.gui", "lgtv_easy.idle",
        "lgtv_easy.netdiag", "lgtv_easy.proc", "lgtv_easy.recovery",
        "lgtv_easy.selfheal", "lgtv_easy.singleton", "lgtv_easy.system_sleep",
        "lgtv_easy.webos", "lgtv_easy.wizard_text", "lgtv_easy.wol",
        "lgtv_easy._dbus", "lgtv_easy._ws",
    ],
    hookspath=[],
    runtime_hooks=[],
    # The app is standard-library only; these are just whatever happens to be in
    # the build machine's site-packages, and they would triple the download.
    excludes=["numpy", "PIL", "pytest", "setuptools", "pip", "matplotlib",
              "pandas", "IPython", "pkg_resources"],
    noarchive=False,
)
pyz = PYZ(a.pure)

gui_exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=GUI_NAME,
    icon=str(ICON),
    version=version_resource("LGTV Companion Easy Mode", GUI_NAME),
    console=False,            # no console window behind the app
    disable_windowed_traceback=False,
    upx=False,                # UPX-packed exes trip antivirus heuristics
)

cli_exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=CLI_NAME,
    icon=str(ICON),
    version=version_resource("LGTV Companion Easy Mode (command line)", CLI_NAME),
    console=True,
    upx=False,
)

coll = COLLECT(
    gui_exe, cli_exe,
    a.binaries, a.datas,
    strip=False, upx=False,
    name=GUI_NAME,
)

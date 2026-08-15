# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for LGTVCompanionEasyMode-Setup.exe.

A single-file installer that carries the whole application inside it, so the
download is one .exe and there is nothing to unzip. It expects app.spec to have
run first: everything under packaging/windows/dist/LGTV Companion Easy Mode/ is
embedded as ``payload/``.

Build both with packaging/windows/build.ps1.
"""
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo)

HERE = Path(SPECPATH).resolve()               # noqa: F821 - injected by PyInstaller
REPO = HERE.parents[1]
PKG = REPO / "EasyMode" / "lgtv_easy"
ICON = PKG / "assets" / "icon.ico"

APP_NAME = "LGTV Companion Easy Mode"
SETUP_NAME = "LGTVCompanionEasyMode-Setup"
APP_DIST = HERE / "dist" / APP_NAME

VERSION = re.search(r'__version__\s*=\s*"([^"]+)"',
                    (PKG / "__init__.py").read_text(encoding="utf-8")).group(1)
_parts = [int(p) for p in VERSION.split(".")]
VERSION_TUPLE = tuple((_parts + [0, 0, 0, 0])[:4])

if not APP_DIST.is_dir():
    raise SystemExit(
        f"Build the application first - {APP_DIST} does not exist.\n"
        "Run packaging/windows/build.ps1, or pyinstaller on app.spec.")

# Every file of the built app, mapped into payload/ with its layout intact.
datas = [(str(f), (Path("payload") / f.relative_to(APP_DIST).parent).as_posix())
         for f in APP_DIST.rglob("*") if f.is_file()]

# The installer shows the version it is about to install; the app's own module
# is inside a .pyz by then, so hand it over as a plain file.
generated = HERE / "build" / "generated"
generated.mkdir(parents=True, exist_ok=True)
version_txt = generated / "app-version.txt"
version_txt.write_text(VERSION, encoding="utf-8")
datas.append((str(version_txt), "."))

a = Analysis(
    [str(HERE / "installer.py")],
    pathex=[str(HERE)],            # so `import shortcuts` resolves
    binaries=[],
    datas=datas,
    hiddenimports=["shortcuts"],
    excludes=["numpy", "PIL", "pytest", "setuptools", "pip", "pkg_resources"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name=SETUP_NAME,
    icon=str(ICON),
    version=VSVersionInfo(
        ffi=FixedFileInfo(filevers=VERSION_TUPLE, prodvers=VERSION_TUPLE,
                          mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                          subtype=0x0),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", "LGTV Companion contributors"),
                StringStruct("FileDescription", f"{APP_NAME} Setup"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", SETUP_NAME),
                StringStruct("LegalCopyright", "MIT licensed"),
                StringStruct("OriginalFilename", SETUP_NAME + ".exe"),
                StringStruct("ProductName", APP_NAME),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ]),
    console=False,        # it is a window, not a command; /S writes to the log
    upx=False,
    runtime_tmpdir=None,
)

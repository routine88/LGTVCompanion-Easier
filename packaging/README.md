# `packaging/` — turning the app into an installed application

Everything here exists to answer one complaint: run from source, Easy Mode looks
like a Python script. It has a Python feather on the taskbar, no desktop icon,
and no obvious way to install or remove it. This folder produces a real Windows
`.exe` with an installer, and a Linux installer that registers the app with the
desktop properly.

| Path | What it is |
|------|------------|
| `make_icons.py` | draws the icon and writes every size (run only when the artwork changes) |
| `windows/app.spec` | PyInstaller recipe for the two application executables |
| `windows/installer.py` | the installer/uninstaller program itself |
| `windows/installer.spec` | packs the app *inside* a one-file `Setup.exe` |
| `windows/build.ps1` | builds all of the above |
| `linux/install.sh` | installs, with menu entry, icons, desktop shortcut and dock icon |
| `linux/uninstall.sh` | removes it again |

The artwork itself lives in `EasyMode/lgtv_easy/assets/` — inside the package, so
the running app finds it identically from source, from pip, or frozen in an exe.
The shortcut writer lives in the package too, as `lgtv_easy/winshortcut.py`:
the installer uses it for the Start Menu and desktop icons, and `autostart` uses
it for the login entry. Beside it, `lgtv_easy/dock.py` handles the *launcher
bar* — the strip of icons people actually click — which is a different thing on
each platform and is described under "The launcher bar" below.

**The built installer is committed at the repository root** as
`LGTVCompanionEasyMode-Setup.exe`, so anyone who downloads the project as a zip
can just run it. After changing anything the app ships, rebuild and refresh that
copy:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
Copy-Item packaging\windows\dist\LGTVCompanionEasyMode-Setup.exe . -Force
```

## Windows

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
```

PyInstaller is installed into a throwaway venv at `packaging\windows\.build-venv`,
so the build never touches the Python you use for anything else. Output lands in
`packaging\windows\dist\`:

```
LGTV Companion Easy Mode\           the app: two exes + a shared runtime
  LGTV Companion Easy Mode.exe        windowed  - the desktop/Start Menu icon
  lgtv-easy.exe                       console   - the same commands, in a terminal
LGTVCompanionEasyMode-Setup.exe     one-file installer carrying the folder above
```

Two executables because one cannot be both: a windowed build has no console to
print `status` into, and a console build flashes a black window every time the
watcher starts at login. They share a single copy of Python, so the pair costs
almost nothing.

### What the installer does

Per-user, so it never asks for an administrator:

- copies the app to `%LOCALAPPDATA%\Programs\LGTV Companion Easy Mode`
- creates the Start Menu entry and (optionally) the desktop icon
- creates the Quick Launch shortcut and asks Windows for a taskbar pin
- registers in Settings → Apps, with a working Uninstall button
- optionally enables start-at-login

```
Setup.exe                    the window
Setup.exe /S                 silent, with the defaults
Setup.exe /S /D=C:\Apps\LGTV /desktop=0 /quicklaunch=0 /autostart=0 /launch=0
Setup.exe /uninstall [/S] [/purge]
```

Every run appends to `%TEMP%\lgtv-easy-setup.log`.

### Nothing may open a terminal

The app is a windowed executable, and everything that starts it on the user's
behalf runs it **directly**:

- the login entry is a `.lnk` in the Startup folder, not a `.cmd` (cmd.exe there
  means a black window flashing up at every single login)
- both Scheduled Tasks - the logon fallback and the power-off-at-shutdown hook -
  name the executable in their action, rather than `cmd /c <wrapper.cmd>`

`lgtv-easy.exe` is the console build, and is only ever run by a human in a
terminal that is already open.

### Why the shortcuts are not made with WScript.Shell

An app that calls `SetCurrentProcessExplicitAppUserModelID` — which Easy Mode
does, so its windows stop being filed under `python.exe` — must stamp the *same*
id onto its shortcuts. Windows compares the two to decide whether a running
window belongs to a pinned shortcut; when only one side has an id, pinning the
app leaves a second, dead taskbar button beside the live one. `WScript.Shell`
cannot set that property, so `lgtv_easy/winshortcut.py` drives `IShellLink`
and `IPropertyStore` through ctypes instead. The id is `LGTVCompanion.EasyMode`,
defined in `lgtv_easy/branding.py`; a test keeps the two files in step.

### Signing

The build is unsigned, so SmartScreen will warn on first run ("More info" →
"Run anyway"). Signing needs a certificate this project does not have; if you
have one, `signtool sign /fd sha256 /a` both exes and then the Setup.exe, in
that order — the installer embeds the app, so signing it first would be pointless.

## Linux

```sh
sh packaging/linux/install.sh              # user install, no root
sh packaging/linux/install.sh --system     # /opt + /usr/share, needs root
sh packaging/linux/install.sh --uninstall  # (--purge also deletes settings)
```

It installs the package to `~/.local/lib/lgtv-companion-easy`, a `lgtv-easy`
command to `~/.local/bin`, the icons into the hicolor theme at every size, a
`.desktop` entry into `~/.local/share/applications`, a trusted copy of that entry
on the Desktop, and the app's id on the dock (`--no-dock-icon` to skip).
`python3-tk` and friends are installed through whichever of apt/dnf/pacman/zypper
is present (`--no-deps` to skip).

The line that matters for the dock is `StartupWMClass=LGTVCompanionEasyMode`: it
ties the running window to the menu entry, which is how the taskbar button gets
the app's icon and name. Tk reports that class because `gui.App` passes
`className=branding.WM_CLASS`, and the name is spelled so that Tk capitalising
its first letter cannot change it.

`~/.local/lib/...` is deliberately not `~/.local/share/lgtv-companion-easy` —
the portable `Linux Launch.sh` keeps its git clone there, and the two must not
delete each other's files.

## The launcher bar

A menu entry is not somewhere anyone looks, and that is what "I installed it and
there is no icon" actually means. `lgtv_easy/dock.py` puts the app on the strip
people click, and both installers and both portable launchers call it:

```sh
lgtv-easy dock add       # or: remove, status
```

**Linux.** GNOME-family shells keep the dock's contents in a GSettings list of
*desktop-file ids*, so pinning is `favorite-apps` plus the id
`lgtv-companion-easy.desktop` — which is why that name has to match in
`install.sh`, `autostart.py` and `dock.py`, and why a test holds the three
together. Every favourites schema that is actually installed is written to
(GNOME, Cinnamon, Budgie, Unity) rather than guessing which shell is running:
pinning inside a shell you are not using is invisible and correct next time you
log into it, where guessing wrong leaves the icon missing. KDE Plasma keeps the
same list inside a panel-applet config file whose group path differs per user, so
there is nothing safe to edit — the app says so and points at the menu instead.

**Windows.** Quick Launch is a plain folder of shortcuts, so that part is one
`.lnk`, stamped with the AppUserModelID like every other shortcut. The taskbar is
not: Microsoft removed the programmatic pin in 8.1 and the shell verb that stood
in for it is hidden from non-Explorer callers on current builds. The installer
asks anyway and treats a refusal as ordinary — the Quick Launch shortcut is there
either way and a hand-pin is one right-click.

Both uninstallers take the icon back off, and the Linux one does it in shell
rather than by asking the app: it has to work when the app is already gone, and a
pinned icon left behind is the one leftover a user *cannot* remove by hand —
right-clicking it offers nothing, because the entry it names no longer exists.

## The icons

`make_icons.py` needs Pillow; the files it writes are committed, so neither
building nor installing does. Change the artwork, then:

```sh
python packaging/make_icons.py
```

It writes `icon.ico` (16→256 in one file), `icon-<N>.png` for every hicolor
size, a 512px `icon.png`, and a hand-written `icon.svg` twin for
`scalable/apps`. Tests assert the set is complete and that the `.ico` carries
the small sizes — a one-size `.ico` is the usual cause of a blurry taskbar icon.

## Releasing

1. Bump `__version__` in `EasyMode/lgtv_easy/__init__.py` (both specs read it).
2. `python -m pytest` in `EasyMode/`.
3. `packaging\windows\build.ps1`, then copy the installer over the committed
   copy at the repository root and commit it.
4. Tag `easy-mode-v<version>`; the Windows installer workflow rebuilds it on a
   clean runner, install-tests it, and attaches it to the release.
5. Linux users install from the source archive with `install.sh`; there is
   nothing to build.

LGTV Companion - Easy Mode
==========================

Make your LG OLED TV sleep like a PC monitor: the screen turns off after a few
minutes of inactivity - or the moment you put the PC to sleep - and wakes again
as soon as you move the mouse or press a key. That is the whole app - one job, a
simple window, almost nothing to configure.


INSTALL IT
----------
  Windows : run   LGTVCompanionEasyMode-Setup.exe
            You get a desktop icon and a Start Menu entry, and it can start
            watching for idle automatically when you log in. It installs for
            your account only, so it never asks for an administrator, and it
            uninstalls from Settings -> Apps like any other program.

            No Setup.exe next to this file? Download the latest from
              https://github.com/routine88/LGTVCompanion-Easier/releases
            or build one yourself - see BUILDING below.

  Linux   : open a terminal in this folder and run
                sh packaging/linux/install.sh
            It adds the app to your applications menu with its own icon, puts a
            shortcut on your desktop, and installs the bits it needs
            (python3-tk and friends). No root needed.

Then open "LGTV Companion Easy Mode" from the desktop icon or your menu. The
first run walks through 3 steps:

  1. Find your TV   - click Scan (or type its IP).
  2. Pair           - press OK / Accept on the prompt that pops up on the TV.
  3. Timeout        - drag the slider. 7 minutes is a good default.

After setup it keeps your TV sleeping in the background. Closing the window does
NOT stop it. (No graphical desktop? The same steps run as a text wizard.)


OR RUN IT WITHOUT INSTALLING
----------------------------
The portable route. It keeps itself up to date from the project's repository,
which the installed app does not.

  Windows : Double-click  "Windows Launch".

  Linux   : Right-click  "Linux Launch.sh"  ->  "Run as a Program".
            First time only: if that option isn't there, the file just needs
            permission to run (files unzipped from a download arrive without
            it). Right-click -> Properties -> Permissions -> tick "Allow
            executing file as program", then try again. Prefer a terminal?
            Open one in this folder and run:   bash "Linux Launch.sh"

The first run installs what it needs (Git + Python), downloads the app, keeps
itself up to date, then opens the same setup window.


TO STOP THE BACKGROUND WATCHER
------------------------------
  Installed : Windows - close it from the app, or uninstall it.
              Linux   - lgtv-easy autostart disable, then log out and back in.
  Portable  : Windows - open a terminal here and run "Windows Launch.bat" -Stop
              Linux   - open a terminal here and run bash "Linux Launch.sh" --stop


TO UNINSTALL
------------
  Windows : Settings -> Apps -> LGTV Companion Easy Mode -> Uninstall
  Linux   : sh packaging/linux/uninstall.sh          (add --purge to also
                                                      delete your settings)


ON YOUR TV (one-time)
---------------------
Enable "Turn on via Wi-Fi" (a.k.a. "Quick Start+" / "Always Ready") so the TV
can be woken over the network. Keep the TV and the PC on the SAME network - a
Google/Nest Wifi mesh is fine over Ethernet or Wi-Fi, as long as it is not a
separate "guest" network. The setup window warns you if they look different.


WHAT'S IN THIS FOLDER
---------------------
  Windows Launch.bat - the portable Windows launcher (double-click)
  Linux Launch.sh    - the portable Linux launcher (right-click -> Run)
  EasyMode/          - the app itself (and its Windows engine)
  packaging/         - the installers, and how to build the Windows .exe
  readme.txt         - this file


BUILDING THE WINDOWS INSTALLER
------------------------------
You only need this if you are making a release (or want an .exe of your own
changes). On a Windows PC with Python installed:

  powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1

It produces packaging\windows\dist\LGTVCompanionEasyMode-Setup.exe, plus the
application folder it installs. See packaging/README.md for the details.

Linux has nothing to build: install.sh installs the app as it is.


IF SOMETHING GOES WRONG
-----------------------
The launcher keeps its window open on an error and writes a log you can read or
share:
  Windows : %APPDATA%\LGTV Companion Easy Mode\launcher.log
  Linux   : ~/.config/lgtv-companion-easy/launcher.log
The Windows installer logs to %TEMP%\lgtv-easy-setup.log.

The first time you run the Setup.exe, Windows SmartScreen may warn that the
publisher is unknown - the build is not code-signed. Choose "More info" ->
"Run anyway", or build it yourself with the command above.

To freeze the code on disk (no auto-update), set LGTV_EASY_NO_UPDATE=1 before
launching. Developer notes and the optional command line live in EasyMode/.

LGTV Companion Easy Mode is an independent, MIT-licensed project (see LICENSE).
It controls the TV directly over the LG WebOS (SSAP) network protocol, and keeps
itself up to date from its own repository.

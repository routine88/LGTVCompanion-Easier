@echo off
REM ===========================================================================
REM  LGTV Companion Easy Mode - Windows uninstaller  (just double-click this)
REM ===========================================================================
REM  Removes every trace of the app, however it got here:
REM
REM    * the installed copy from LGTVCompanionEasyMode-Setup.exe
REM    * the self-updating copy the portable "Windows Launch.bat" downloads
REM    * the start-at-login entry AND the power-off-at-shutdown Scheduled Task
REM    * the Start Menu and Desktop shortcuts
REM    * the Add/Remove Programs entry
REM
REM  Why this exists when Settings -> Apps already has an Uninstall button: that
REM  button only knows about the installer's copy. Someone who ran the portable
REM  launcher - or who installed once and then deleted the folder by hand - is
REM  left with a login entry and two Scheduled Tasks pointing at files that are
REM  gone. Nothing in Settings will ever clean those up. This will.
REM
REM  Your settings (the TV you paired with, your idle timeout) are KEPT unless
REM  you say otherwise, so reinstalling picks up where you left off.
REM
REM    "Windows Uninstall.bat"                 ask about the settings
REM    "Windows Uninstall.bat" --purge         delete the settings too
REM    "Windows Uninstall.bat" --keep-settings keep them, don't ask
REM    "Windows Uninstall.bat" --no-pause      don't wait for a keypress at the end
REM
REM  Style note - why this file uses GOTO labels and not "if ... ( ... )" blocks:
REM  cmd.exe parses a whole parenthesised block the moment it reaches the "if",
REM  whether or not the condition is true, so one stray parenthesis anywhere
REM  inside aborts the batch file instantly. The launcher next door explains this
REM  at greater length; the rule is the same here.
REM ===========================================================================
setlocal EnableExtensions
set "ARGS=%*"

REM ---- what we are looking for -----------------------------------------------
set "APP_NAME=LGTV Companion Easy Mode"
set "GUI_EXE=LGTV Companion Easy Mode.exe"
set "CLI_EXE=lgtv-easy.exe"
set "TASK=LGTV Companion Easy Mode"
set "SHUTDOWN_TASK=LGTV Companion Easy Mode Shutdown"
set "UNINSTALL_KEY=HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\LGTVCompanionEasyMode"
REM Where the installer puts the app, and where the portable launcher keeps its
REM self-updating clone. Both are per-user - nothing here needs an administrator.
set "INSTALL_DIR=%LOCALAPPDATA%\Programs\%APP_NAME%"
set "PORTABLE_DIR=%LOCALAPPDATA%\lgtv-companion-easy"
set "STATE_DIR=%APPDATA%\%APP_NAME%"
set "PROGRAMS_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
set "STARTUP_DIR=%PROGRAMS_DIR%\Startup"

set "PURGE=ask"
set "PAUSE_AT_END=1"
set "RELAUNCHED="
set "REMOVED=0"

REM ---- options ---------------------------------------------------------------
:parse
if "%~1"=="" goto :parsed
if /I "%~1"=="--purge" goto :opt_purge
if /I "%~1"=="--keep-settings" goto :opt_keep
if /I "%~1"=="--no-pause" goto :opt_nopause
if /I "%~1"=="--relaunched" goto :opt_relaunched
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="--help" goto :usage
if /I "%~1"=="/?" goto :usage
echo Unknown option: %~1
echo.
goto :usage

:opt_purge
set "PURGE=yes"
shift
goto :parse

:opt_keep
set "PURGE=no"
shift
goto :parse

:opt_nopause
set "PAUSE_AT_END=0"
shift
goto :parse

:opt_relaunched
set "RELAUNCHED=1"
shift
goto :parse

:usage
echo Usage: "Windows Uninstall.bat" [--purge^|--keep-settings] [--no-pause]
echo.
echo   --purge          also delete your saved settings and TV pairing
echo   --keep-settings  keep them without being asked
echo   --no-pause       do not wait for a keypress when finished
endlocal
exit /b 2

:parsed

REM ---- run from a copy if we are standing on ground we are about to delete ----
REM The portable launcher's clone contains this very file. cmd.exe reads a batch
REM file as it goes, so deleting the folder out from under ourselves would stop
REM the uninstall halfway - with the login entry already gone and the app still
REM there, or the other way round. Copy to %TEMP% and hand over.
if defined RELAUNCHED goto :begin
set "HERE=%~dp0"
echo "%HERE%" | findstr /I /C:"%PORTABLE_DIR%" >nul
if not errorlevel 1 goto :relaunch
echo "%HERE%" | findstr /I /C:"%INSTALL_DIR%" >nul
if not errorlevel 1 goto :relaunch
goto :begin

:relaunch
set "TEMP_COPY=%TEMP%\lgtv-easy-uninstall.bat"
copy /Y "%~f0" "%TEMP_COPY%" >nul 2>&1
if errorlevel 1 goto :begin
call "%TEMP_COPY%" --relaunched %ARGS%
endlocal
exit /b %ERRORLEVEL%

REM ---- do it -----------------------------------------------------------------
:begin
REM The installer lets you choose where the app goes and records the answer, so
REM trust that over the default path - otherwise an install to D:\Apps survives
REM this, and so does the copy of the app that knows how to undo its own
REM start-at-login registration.
set "RECORDED_DIR="
for /f "tokens=2,*" %%A in ('reg query "%UNINSTALL_KEY%" /v InstallLocation 2^>nul') do set "RECORDED_DIR=%%B"
if not defined RECORDED_DIR set "RECORDED_DIR=%INSTALL_DIR%"

echo.
echo  Uninstalling %APP_NAME%
echo  ---------------------------------------------------------------
echo.

if /I "%PURGE%"=="ask" goto :ask_settings
goto :stop_app

:ask_settings
echo  Your settings - the TV you paired with, and your idle timeout - live in
echo    %STATE_DIR%
echo  Keeping them means a reinstall needs no setup at all.
echo.
set "ANSWER="
set /p "ANSWER=Delete them as well? Type y then Enter, or just press Enter to keep: "
set "PURGE=no"
if /I "%ANSWER%"=="y" set "PURGE=yes"
if /I "%ANSWER%"=="yes" set "PURGE=yes"
echo.

:stop_app
echo  Stopping the watcher...
REM Ask the installed app to remove its own login entry first, while it is still
REM on disk and knows every form it might have taken.
if exist "%RECORDED_DIR%\%GUI_EXE%" "%RECORDED_DIR%\%GUI_EXE%" autostart disable >nul 2>&1
call :stop_pidfile "%STATE_DIR%\daemon.pid"
call :stop_pidfile "%STATE_DIR%\launcher.pid"
REM Killing the app leaves the TV exactly as it is: powering it off is a
REM different signal entirely, sent only on a real shutdown.
taskkill /F /IM "%GUI_EXE%" >nul 2>&1
taskkill /F /IM "%CLI_EXE%" >nul 2>&1

echo  Removing the start-at-login entry...
call :rm_file "%STARTUP_DIR%\%APP_NAME%.lnk"
REM The .cmd is the older form. An install that was upgraded may still have it,
REM and leaving it behind would start a watcher that no longer exists.
call :rm_file "%STARTUP_DIR%\LGTV-Easy-Mode.cmd"
call :rm_task "%TASK%"
call :rm_task "%SHUTDOWN_TASK%"

echo  Removing shortcuts...
call :rm_file "%PROGRAMS_DIR%\%APP_NAME%.lnk"
call :rm_desktop_shortcuts

echo  Removing the app...
call :rm_dir "%RECORDED_DIR%"
if /I not "%RECORDED_DIR%"=="%INSTALL_DIR%" call :rm_dir "%INSTALL_DIR%"
call :rm_dir "%PORTABLE_DIR%"
reg delete "%UNINSTALL_KEY%" /f >nul 2>&1
if not errorlevel 1 call :note "Add/Remove Programs entry"

if /I "%PURGE%"=="yes" goto :purge_settings
if not exist "%STATE_DIR%" goto :done
echo.
echo  Kept your settings in %STATE_DIR%
echo  Delete that folder by hand, or re-run this with --purge, to be rid of them.
goto :done

:purge_settings
call :rm_dir "%STATE_DIR%"

:done
echo.
if "%REMOVED%"=="0" goto :nothing_found
echo  Done. %APP_NAME% is gone.
echo.
echo  The portable "Windows Launch.bat" and the setup .exe both still work if
echo  you want it back - nothing here touched the folder you are reading this in.
goto :finish

:nothing_found
echo  Nothing to remove - %APP_NAME% was not installed for this account.

:finish
echo.
if "%PAUSE_AT_END%"=="1" pause
endlocal
exit /b 0

REM ---- helpers ---------------------------------------------------------------

REM Stop a process named in a pidfile, but only after proving it is ours.
REM Windows recycles pids freely, so a stale pidfile whose number now belongs to
REM some innocent program must never get that program killed.
:stop_pidfile
if not exist "%~1" goto :eof
set "PID="
for /f "usebackq tokens=1" %%P in ("%~1") do set "PID=%%P"
if not defined PID goto :eof
tasklist /FI "PID eq %PID%" /NH 2>nul | findstr /I /R "python powershell lgtv" >nul
if errorlevel 1 goto :eof
taskkill /F /PID %PID% >nul 2>&1
echo    stopped a running watcher, pid %PID%
set "REMOVED=1"
goto :eof

:rm_file
if not exist "%~1" goto :eof
del /f /q "%~1" >nul 2>&1
if exist "%~1" goto :rm_file_failed
echo    removed %~1
set "REMOVED=1"
goto :eof
:rm_file_failed
echo    COULD NOT REMOVE %~1 - delete it by hand
goto :eof

:rm_dir
if not exist "%~1" goto :eof
rd /s /q "%~1" >nul 2>&1
if exist "%~1" goto :rm_dir_failed
echo    removed %~1
set "REMOVED=1"
goto :eof
:rm_dir_failed
echo    COULD NOT REMOVE %~1 - delete it by hand
goto :eof

:rm_task
schtasks /Query /TN "%~1" >nul 2>&1
if errorlevel 1 goto :eof
schtasks /Delete /TN "%~1" /F >nul 2>&1
if errorlevel 1 goto :rm_task_failed
echo    removed Scheduled Task "%~1"
set "REMOVED=1"
goto :eof
:rm_task_failed
echo    COULD NOT REMOVE Scheduled Task "%~1" - delete it in Task Scheduler
goto :eof

REM The Desktop is not always %USERPROFILE%\Desktop - OneDrive moves it, and so
REM does a redirected profile - so ask the registry where it really is, and check
REM the obvious places too in case the shortcut predates the move.
:rm_desktop_shortcuts
call :rm_file "%USERPROFILE%\Desktop\%APP_NAME%.lnk"
if defined OneDrive call :rm_file "%OneDrive%\Desktop\%APP_NAME%.lnk"
set "SHELL_DESKTOP="
for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders" /v Desktop 2^>nul') do set "SHELL_DESKTOP=%%B"
if not defined SHELL_DESKTOP goto :eof
call :rm_file "%SHELL_DESKTOP%\%APP_NAME%.lnk"
goto :eof

:note
echo    removed %~1
set "REMOVED=1"
goto :eof

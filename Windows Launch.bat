@echo off
REM ===========================================================================
REM  LGTV Companion Easy Mode - Windows launcher  (just double-click this file)
REM ===========================================================================
REM  This file is a tiny, stable shim: it finds the real PowerShell launcher and
REM  runs it. It deliberately prefers the SELF-UPDATING internal copy (kept in
REM  %LOCALAPPDATA%) over the copy next to this file, so all the real logic stays
REM  current even though this .bat never changes. The window is kept open at the
REM  end so it can never just vanish on a double-click.
REM
REM  Style note - why this file uses GOTO labels and not "if ... ( ... )" blocks:
REM  cmd.exe parses a whole parenthesised block the moment it reaches the "if",
REM  whether or not the condition is true. An unescaped "(" or ")" anywhere
REM  inside - even in a message we never intended to print - is a syntax error
REM  that aborts the batch file instantly, which looks exactly like a
REM  double-click that "does nothing". Labels sidestep that entirely, and any
REM  literal parenthesis in an ECHO is written ^( ^) to be safe.
setlocal
set "APP_PS1=%LOCALAPPDATA%\lgtv-companion-easy\app\EasyMode\LGTV-Easy-Mode-WINDOWS.ps1"
set "LOCAL_PS1=%~dp0EasyMode\LGTV-Easy-Mode-WINDOWS.ps1"

REM Prefer the auto-updated internal copy; fall back to the one in the EasyMode
REM folder beside this file (needed for the very first run, before any clone).
set "PS1=%LOCAL_PS1%"
if exist "%APP_PS1%" set "PS1=%APP_PS1%"

if not exist "%PS1%" goto :nolauncher

echo Starting LGTV Companion Easy Mode... ^(this window will stay open^)
echo.
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%PS1%" -Background %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" goto :failed

REM Always pause so the window never just vanishes on a double-click. The TV
REM watcher, when started, runs as a separate background process and keeps
REM running after this window is closed.
pause
endlocal
exit /b 0

:failed
echo ----------------------------------------------------------------------
echo  The launcher exited with an error ^(code %RC%^).
echo  Scroll up to read the messages above - they explain what went wrong,
echo  and can be shared to get help.
echo ----------------------------------------------------------------------
pause
endlocal
exit /b %RC%

:nolauncher
echo.
echo ERROR: Could not find the PowerShell launcher:
echo   "%PS1%"
echo Make sure "Windows Launch.bat" is next to the EasyMode folder
echo ^(which contains LGTV-Easy-Mode-WINDOWS.ps1^), then run this again.
echo.
pause
endlocal
exit /b 1

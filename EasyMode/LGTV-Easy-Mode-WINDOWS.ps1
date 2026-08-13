<#
  LGTV Companion Easy Mode - self-updating launcher (Windows / PowerShell)
  ---------------------------------------------------------------------------
  One script that:
    1. Installs dependencies (git + Python, via winget; tkinter ships with the
       official Python installer).
    2. Updates the app from GitHub - INCLUDING updates to this very launcher
       (it re-runs itself if the script changed) - and REPORTS what happened:
       updated (with what arrived), already current, or couldn't reach GitHub.
       An update that silently does nothing is the failure this reporting
       exists to make impossible.
    3. Opens the graphical setup window on first use (text wizard if headless).
    4. Supervises the idle daemon in the background, restarting it if it crashes.
       All errors go to a persistent log.

  Updates are applied WHEN YOU RUN THIS LAUNCHER, and at no other time. There is
  deliberately no periodic background check: a watcher that rewrites its own code
  and restarts itself midway through an evening is a surprise, not a feature.

  Usage (from PowerShell, or just double-click Windows Launch.bat):
    .\LGTV-Easy-Mode-WINDOWS.ps1              # set up if needed, then supervise
    .\LGTV-Easy-Mode-WINDOWS.ps1 -Background  # detach and supervise in background
    .\LGTV-Easy-Mode-WINDOWS.ps1 -Setup       # force the setup wizard, then exit
    .\LGTV-Easy-Mode-WINDOWS.ps1 -Stop        # stop a running background supervisor
#>
[CmdletBinding()]
param(
    [switch]$Background,
    [switch]$Supervise,
    [switch]$Setup,
    [switch]$Stop
)

$ErrorActionPreference = "Continue"

# ---- configuration ----------------------------------------------------------
$RepoUrl    = if ($env:LGTV_EASY_REPO)   { $env:LGTV_EASY_REPO }   else { "https://github.com/routine88/lgtvcompanion-easier.git" }
# Track the repository's default branch (master). Override with LGTV_EASY_BRANCH.
$RepoBranch = if ($env:LGTV_EASY_BRANCH) { $env:LGTV_EASY_BRANCH } else { "master" }
$AppHome    = if ($env:LGTV_EASY_APP_HOME) { $env:LGTV_EASY_APP_HOME } else { Join-Path $env:LOCALAPPDATA "lgtv-companion-easy\app" }
$StateDir   = if ($env:LGTV_EASY_HOME) { $env:LGTV_EASY_HOME } else { Join-Path $env:APPDATA "LGTV Companion Easy Mode" }
# Set LGTV_EASY_NO_UPDATE=1 to freeze the code: no git fetch/clone and no
# self-update. Run only the code already on disk.
$NoUpdate = ($env:LGTV_EASY_NO_UPDATE -eq "1")
# The Python app and this launcher both live in the EasyMode/ subdirectory of
# the cloned repo; the .bat shim at the repo root points into here.
$SubDir = "EasyMode"
$LauncherName = "LGTV-Easy-Mode-WINDOWS.ps1"

$LogFile = Join-Path $StateDir "launcher.log"
$PidFile = Join-Path $StateDir "launcher.pid"
# The supervised daemon's redirected streams go here, kept apart from launcher.log
# so two processes never write the same file at once (and stdout/stderr must be
# different files - PowerShell's Start-Process forbids sharing one).
$WatcherLog = Join-Path $StateDir "watcher.log"
$WatcherOutLog = Join-Path $StateDir "watcher-stdout.log"
New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

# Hash of this script when we started, to detect a git update rewriting it.
$SelfPath = $PSCommandPath
$LauncherStartHash = if ($SelfPath -and (Test-Path $SelfPath)) {
    (Get-FileHash $SelfPath).Hash
} else { "none" }

# Two audiences, two functions. Log() keeps the timestamped engineering record;
# Say() talks to the person watching the window - and is recorded too, so a
# pasted log tells the same story the window did.
#
# On the console Log() drops the timestamp and [launcher] tag and dims the line:
# they are what a log needs and exactly what a person reading a window does not,
# and burying the progress that matters in machine prefixes is what made these
# launchers feel barebones. The file above keeps the full form either way.
#
# ASCII only in all of this - it has to survive a legacy cp437/cp1252 console,
# which is precisely where a confused user ends up reading it.
$Rule = "======================================================================"

function Log($msg) {
    $line = "{0} [launcher] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $LogFile -Value $line
    Write-Host ("  " + $msg) -ForegroundColor DarkGray
}

function Say([string]$msg, [string]$colour = "Gray") {
    Write-Host $msg -ForegroundColor $colour
    Add-Content -Path $LogFile -Value ("{0} [ui] {1}" -f `
        (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg.Trim())
}

function Banner {
    Write-Host ""
    Write-Host $Rule -ForegroundColor Cyan
    Write-Host "  LGTV Companion Easy Mode" -ForegroundColor White
    Write-Host $Rule -ForegroundColor Cyan
}

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

# ---- process helpers --------------------------------------------------------
# Read a pid file and return the pid ONLY if that process is really alive (and,
# when given, has a matching name - Windows recycles pids, so a bare "does pid N
# exist" check can match a stranger's process and make us report a watcher that
# isn't ours, or kill something unrelated). The Linux launcher's `kill -0` guard
# is the same idea.
function Get-LivePid([string]$path, [string]$nameLike = "") {
    if (-not (Test-Path $path)) { return $null }
    $raw = ((Get-Content $path -ErrorAction SilentlyContinue) -join "").Trim()
    if ($raw -notmatch '^\d+$') { return $null }
    $proc = Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }
    if ($nameLike -and $proc.Name -notlike $nameLike) { return $null }
    return [int]$raw
}

# The one place we spawn a detached PowerShell (the background watcher, and the
# self-update hand-off). Everything goes through here because of the quoting:
#
# Start-Process joins -ArgumentList with spaces and does NOT quote the entries
# for you. An unquoted path containing a space - C:\Users\First Last\..., the
# normal case for a Windows account with a space in its name - therefore reaches
# powershell.exe cut in half: it reports `-File 'C:\Users\First'` and exits
# immediately. Because the watcher is started -WindowStyle Hidden, that failure
# was completely invisible: the launcher went on to announce "running in the
# background" while nothing was running at all. (The bash launcher never had this
# bug - `exec "$repo_launcher"` quotes properly - which is why only Windows broke.)
# Quote the path exactly once, here, so no caller can get it wrong again.
function Start-Detached([string]$scriptPath, [string[]]$extraArgs = @()) {
    $quoted = '"' + $scriptPath + '"'
    $argList = @("-ExecutionPolicy", "Bypass", "-NoProfile", "-File", $quoted) + $extraArgs
    return Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -PassThru `
        -ArgumentList $argList
}

# The idle-daemon child the supervisor is currently running (the equivalent of
# $daemon_pid in the bash launcher), tracked at script scope so a self-update can
# stop it before handing off instead of orphaning it.
$script:DaemonProc = $null
function Stop-DaemonChild {
    $p = $script:DaemonProc
    $script:DaemonProc = $null
    if ($p -and -not $p.HasExited) {
        try { $p.Kill() } catch {}
    }
}

# Print-only banners. The actual "keep the window open" pause lives in the .bat
# (the single, version-robust place a double-click goes through), so these just
# explain what happened; they must NOT block, or we'd pause twice.
function Pause-BeforeExit {
    Write-Host ""
    Write-Host "----------------------------------------------------------------------"
    Write-Host "  Setup did not finish. The diagnostics above (and the log file"
    Write-Host "  $LogFile) can be shared to get help."
    Write-Host "----------------------------------------------------------------------"
}

# Positive confirmation for the common "already set up" case: the watcher runs as
# a detached background process. Closing the window does NOT stop it.
function Pause-Info([string[]]$lines) {
    Write-Host ""
    Write-Host "======================================================================"
    foreach ($l in $lines) { Write-Host "  $l" }
    Write-Host "======================================================================"
}

# ---- dependency installation ------------------------------------------------
function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path","User")
}

function Install-Deps {
    if (-not (Have "winget")) {
        Log "winget not found. Please install Git and Python 3 manually from python.org and git-scm.com."
    }
    if (-not (Have "git")) {
        Log "Installing Git via winget..."
        winget install --id Git.Git -e --source winget --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        Refresh-Path
    }
    if (-not (Have "python")) {
        Log "Installing Python 3 via winget..."
        winget install --id Python.Python.3.12 -e --source winget --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
        Refresh-Path
    }
    # tkinter check (ships with the official installer).
    & python -c "import tkinter" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Log "WARNING: tkinter not available in this Python. The graphical wizard needs it; the text wizard still works."
    }
}

# Report what we actually have to work with - the first thing to check when the
# program "won't launch" is whether Python and Git are even on PATH.
function Log-Diagnostics {
    if (Have "python") {
        Log ("Python: " + ((& python --version 2>&1) -join " "))
    } else {
        Log "Python: NOT FOUND on PATH."
    }
    if (Have "git") {
        Log ("Git: " + ((& git --version 2>&1) -join " "))
    } else {
        Log "Git: NOT FOUND on PATH."
    }
    Log "App folder: $AppHome"
    Log "Log file  : $LogFile"
}

# If Python still isn't usable we cannot run the app at all - say so plainly and
# keep the window open, instead of failing somewhere deeper with a cryptic error.
function Require-Python {
    if (Have "python") {
        & python -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { return }
    }
    Log "ERROR: Python 3 is required but was not found (or won't run)."
    Write-Host ""
    Write-Host "Python 3 could not be found on this PC. Easy Mode needs it to run."
    Write-Host "Fix: install Python 3 from https://www.python.org/downloads/ and,"
    Write-Host "on the first installer screen, TICK 'Add python.exe to PATH'."
    Write-Host "Then run this launcher again."
    Pause-BeforeExit
    exit 1
}

# ---- repository / self-update ----------------------------------------------
# Outcome of the last Sync-Repo, so the caller can both REPORT it and decide
# whether anything actually needs restarting. Previously the caller could only
# tell "git didn't error", which is not the same question at all - it restarted
# the daemon on every check whether or not a single byte had changed.
#   $script:SyncResult : cloned | updated | current | offline | failed
$script:SyncResult   = ""
$script:SyncOld      = ""
$script:SyncNew      = ""
$script:SyncCount    = 0
$script:SyncSubjects = @()

function Repo-Head {
    if (-not (Test-Path (Join-Path $AppHome ".git"))) { return "" }
    $h = (& git -C $AppHome rev-parse --short HEAD 2>$null)
    if ($LASTEXITCODE -ne 0) { return "" }
    return ("" + $h).Trim()
}

function Repo-Age {
    $a = (& git -C $AppHome log -1 --format=%cr 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $a) { return "unknown age" }
    return ("" + $a).Trim()
}

function Sync-Repo {
    $script:SyncResult = ""; $script:SyncOld = ""; $script:SyncNew = ""
    $script:SyncCount = 0; $script:SyncSubjects = @()
    if (-not (Test-Path (Join-Path $AppHome ".git"))) {
        Log "Cloning $RepoUrl into $AppHome"
        New-Item -ItemType Directory -Force -Path (Split-Path $AppHome) | Out-Null
        git clone --branch $RepoBranch $RepoUrl $AppHome 2>&1 | Add-Content $LogFile
        if ($LASTEXITCODE -eq 0) {
            $script:SyncResult = "cloned"; $script:SyncNew = Repo-Head
        } else {
            $script:SyncResult = "failed"; Log "ERROR: clone failed"
        }
        return
    }
    $script:SyncOld = Repo-Head
    Log "Checking GitHub for updates ($RepoBranch)"
    git -C $AppHome fetch --quiet origin $RepoBranch 2>&1 | Add-Content $LogFile
    if ($LASTEXITCODE -ne 0) {
        $script:SyncResult = "offline"; $script:SyncNew = $script:SyncOld
        Log "Fetch failed (offline?); keeping the copy on disk."
        return
    }
    git -C $AppHome checkout --quiet $RepoBranch 2>&1 | Add-Content $LogFile
    git -C $AppHome reset --hard "origin/$RepoBranch" 2>&1 | Add-Content $LogFile
    if ($LASTEXITCODE -ne 0) {
        $script:SyncResult = "failed"; $script:SyncNew = $script:SyncOld
        Log "ERROR: could not apply the update."
        return
    }
    $script:SyncNew = Repo-Head
    if ($script:SyncNew -eq $script:SyncOld) { $script:SyncResult = "current"; return }
    $script:SyncResult = "updated"
    # A force-push can leave the old commit unreachable, so this is best-effort
    # decoration - never let it turn a good update into a reported failure.
    $subjects = @(& git -C $AppHome log --format=%s "$($script:SyncOld)..$($script:SyncNew)" 2>$null)
    if ($LASTEXITCODE -ne 0) { $subjects = @() }
    $script:SyncCount    = @($subjects).Count
    $script:SyncSubjects = @($subjects | Select-Object -First 6)
}

# True only when new code actually arrived - the trigger for restarting anything.
function Sync-Changed {
    return ($script:SyncResult -eq "updated" -or $script:SyncResult -eq "cloned")
}

# Say plainly whether the update worked. "It launched" and "it updated" are
# different claims, and only one of them used to be visible.
function Report-Update {
    switch ($script:SyncResult) {
        "cloned"  { Say ("  [ok] Installed from GitHub (" + $script:SyncNew + ")") "Green" }
        "updated" {
            Say ("  [ok] Updated " + $script:SyncOld + " -> " + $script:SyncNew +
                 "  (" + $script:SyncCount + " new)") "Green"
            foreach ($s in $script:SyncSubjects) { Say ("         - " + $s) "DarkGray" }
        }
        "current" { Say ("  [ok] Already up to date (" + $script:SyncNew + ", " + (Repo-Age) + ")") "Green" }
        "offline" {
            Say "  [!]  Could not reach GitHub - update SKIPPED." "Yellow"
            Say ("       Running the copy already on disk (" + $script:SyncNew +
                 ", " + (Repo-Age) + ").") "DarkGray"
        }
        "failed"  {
            Say ("  [X]  Update FAILED - see " + $LogFile) "Red"
            Say ("       Running the copy already on disk (" + (Repo-Head) + ").") "DarkGray"
        }
        default   { Say "  [-]  Update check skipped." "DarkGray" }
    }
}

# Retire whatever daemon holds the single-instance lock, so newly-pulled code is
# actually loaded.
#
# THE trap this exists for: Python reads its source once, at process start. A
# daemon started at LOGIN (the Startup-folder entry, as pythonw) holds the lock
# for the whole session, so after an update the supervisor's own child just
# queues behind it forever and the app stays pinned to old code with nothing on
# screen to say so. Restarting our own child is not enough, because the holder
# is often not our child - on Windows this looked exactly like an update that
# simply never took.
#
# A forced stop is TerminateProcess: it does NOT run the console control
# handler, so unlike a real shutdown event this leaves the TV exactly as it is.
function Retire-StaleDaemon {
    $daemonPidFile = Join-Path $StateDir "daemon.pid"
    $dp = Get-LivePid $daemonPidFile "python*"
    if (-not $dp) { return }
    Log "Retiring daemon pid $dp so the new code is loaded."
    try {
        Stop-Process -Id $dp -Force -ErrorAction Stop
        Remove-Item $daemonPidFile -ErrorAction SilentlyContinue
        Log "Daemon pid $dp stood down."
    } catch {
        Log "WARNING: could not retire daemon pid $dp; it may still run old code."
    }
}

function Maybe-SelfUpdate {
    $repoLauncher = Join-Path (App-Dir) $LauncherName
    if (-not (Test-Path $repoLauncher)) { return }
    if (-not $SelfPath) { return }
    try { $selfFull = (Resolve-Path $SelfPath).Path } catch { return }
    try { $repoFull = (Resolve-Path $repoLauncher).Path } catch { return }
    if ($selfFull -ine $repoFull) {
        # Started from a bootstrap/Desktop copy: run the up-to-date repo copy IN
        # THIS SAME WINDOW and adopt its exit code. Running it in place (rather
        # than Start-Process, which opens a detached window and loses the exit
        # code) keeps everything in one console, so the .bat's "keep window open
        # on failure" safety net still works and nothing flashes past.
        Log "Running the up-to-date launcher from $AppHome."
        $fwd = @($script:ForwardArgs)
        $env:LGTV_EASY_HANDOFF = "1"
        & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $repoFull @fwd
        exit $LASTEXITCODE
    }
    # We ARE the repo copy; if git rewrote it underneath us, re-run the new one.
    if ((Get-FileHash $selfFull).Hash -ne $LauncherStartHash) {
        Log "Launcher updated itself; re-running the new version."
        $fwd = @($script:ForwardArgs)
        $env:LGTV_EASY_HANDOFF = "1"
        if ($Supervise) {
            # The hidden background supervisor restarts itself detached, so the
            # old process doesn't linger waiting on the new one. Mirroring the
            # Linux launcher, stop our daemon child and drop the pidfile FIRST:
            # otherwise the old daemon outlives this process still holding the
            # single-instance lock, the fresh supervisor's daemon blocks forever
            # waiting for it, and -Stop can no longer find the supervisor that
            # owns the (now overwritten) pidfile.
            Stop-DaemonChild
            Remove-Item $PidFile -ErrorAction SilentlyContinue
            Start-Detached $selfFull $fwd | Out-Null
            exit 0
        }
        & powershell.exe -ExecutionPolicy Bypass -NoProfile -File $selfFull @fwd
        exit $LASTEXITCODE
    }
}

function App-Dir { Join-Path $AppHome $SubDir }
function Run-Cli([string[]]$cliArgs) {
    Push-Location (App-Dir)
    try { & python -m lgtv_easy @cliArgs } finally { Pop-Location }
}

function Needs-Setup {
    $cfg = Join-Path $StateDir "config.json"
    if (-not (Test-Path $cfg)) { return $true }
    try {
        $j = Get-Content $cfg -Raw | ConvertFrom-Json
        return -not ($j.setup_complete -and $j.device.key)
    } catch { return $true }
}

# ---- supervisor loop --------------------------------------------------------
function Start-Supervisor {
    # Never run two supervisors at once (the Linux launcher guards this the same
    # way). Re-opening the app falls through to here; without the guard each open
    # would clobber the pidfile - orphaning the previous supervisor so -Stop can
    # no longer find it - and spawn another daemon that just blocks forever on the
    # lock the first daemon already holds, so watchers pile up on every re-open.
    $existing = Get-LivePid $PidFile "powershell*"
    if ($existing -and $existing -ne $PID) {
        Log "A background watcher is already running (pid $existing); not starting another."
        return
    }
    Set-Content -Path $PidFile -Value $PID
    Log "Supervisor started (pid $PID). Daemon output -> $WatcherLog"
    # If another watcher (e.g. the login auto-start) already holds the lock, our
    # daemon child should wait for it rather than spin-restart.
    $env:LGTV_EASY_WAIT_LOCK = "1"
    try {
        while ($true) {
            Log "Starting idle daemon."
            # The daemon's own activity log is easy-mode.log; this captures its
            # console/stderr stream (and any raw startup traceback) into a
            # SEPARATE file. Start-Process refuses to point both standard streams
            # at the same path, and writing to launcher.log here would also fight
            # the supervisor's own Add-Content, so use a dedicated watcher log.
            $proc = Start-Process -FilePath "python" -ArgumentList @("-m","lgtv_easy","run") `
                -WorkingDirectory (App-Dir) -NoNewWindow -PassThru `
                -RedirectStandardError $WatcherLog -RedirectStandardOutput $WatcherOutLog
            $script:DaemonProc = $proc
            # Watch the daemon. There is deliberately no update check in here:
            # updates are applied when the user runs the launcher, so a watcher
            # never rewrites its own code and restarts itself out from under
            # someone mid-evening. (The old hourly check also restarted the
            # daemon on EVERY pass, whether or not anything had changed.)
            while (-not $proc.HasExited) {
                Start-Sleep -Seconds 15
            }
            Log "Daemon exited (code $($proc.ExitCode)). Restarting in 5s."
            Start-Sleep -Seconds 5
        }
    } finally {
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    }
}

function Stop-Background {
    $stopped = $false
    # Match on the process name as well as the pid: Windows recycles pids, and a
    # stale pidfile whose number now belongs to some unrelated program must never
    # get that program killed.
    $sp = Get-LivePid $PidFile "powershell*"
    if ($sp) {
        try {
            Stop-Process -Id $sp -Force -ErrorAction Stop
            Log "Stopped background supervisor (pid $sp)."
            $stopped = $true
        } catch { Log "Could not stop supervisor (pid $sp): $($_.Exception.Message)" }
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    # Hard-killing the supervisor leaves its idle-daemon child orphaned (and
    # still holding the single-instance lock), so stop that too. The daemon
    # records its own PID in daemon.pid (as python, or pythonw under the login
    # auto-start). A forced stop here does NOT power the TV off (that only
    # happens on a real console shutdown event / the shutdown Scheduled Task),
    # which matches the Linux launcher's --stop: leave the TV exactly as it is.
    $daemonPidFile = Join-Path $StateDir "daemon.pid"
    $dp = Get-LivePid $daemonPidFile "python*"
    if ($dp) {
        try {
            Stop-Process -Id $dp -Force -ErrorAction Stop
            Log "Stopped idle daemon (pid $dp)."
            $stopped = $true
            # Only drop the pidfile once the daemon is really gone: that file IS
            # the single-instance lock, so removing it under a daemon we failed to
            # kill would let a second one start alongside it.
            Remove-Item $daemonPidFile -ErrorAction SilentlyContinue
        } catch {}
    } else {
        # Nobody live holds it; clear a stale pidfile so it can't confuse a
        # later run.
        Remove-Item $daemonPidFile -ErrorAction SilentlyContinue
    }
    if ($stopped) { Log "Easy Mode stopped. Your TV is left as-is." }
    else { Log "No running background watcher found." }
}

# ---- main -------------------------------------------------------------------
$script:ForwardArgs = @()
if ($Supervise)  { $script:ForwardArgs += "-Supervise" }
if ($Background) { $script:ForwardArgs += "-Background" }
if ($Setup)      { $script:ForwardArgs += "-Setup" }

if ($Stop) { Stop-Background; exit 0 }

# Catch-all backstop: any unexpected error prints clearly and keeps the window
# open, rather than the console vanishing before it can be read.
trap {
    Log "FATAL: $($_.Exception.Message)"
    Write-Host ""
    Write-Host "Unexpected error while starting Easy Mode:"
    Write-Host "  $($_.Exception.Message)"
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace }
    Pause-BeforeExit
    exit 1
}

# The bootstrap copy installs deps and self-updates, then hands off to the
# up-to-date internal copy (LGTV_EASY_HANDOFF=1) - which skips redoing all that.
if ($env:LGTV_EASY_HANDOFF -eq "1") {
    Log "Running the up-to-date launcher."
} else {
    Banner
    Say "  Checking dependencies..." "DarkGray"
    Install-Deps
    Log-Diagnostics
    Require-Python
    if ($NoUpdate) {
        Say "  [-]  Updates are off (LGTV_EASY_NO_UPDATE=1); using the copy on disk." "DarkGray"
    } else {
        Sync-Repo
        Report-Update
        # New code arrived, so any daemon still running is by definition stale -
        # and it may be one we did not start (the Startup-folder entry at login),
        # holding the single-instance lock. Retire it here, before our own child
        # queues up behind it forever. Without this the update lands on disk and
        # never actually runs, which is exactly what it looked like in practice.
        if (Sync-Changed) { Retire-StaleDaemon }
        Maybe-SelfUpdate
    }
    Say ("  App folder : " + $AppHome) "DarkGray"
    Say ("  Log file   : " + $LogFile) "DarkGray"
    Write-Host $Rule -ForegroundColor Cyan
    Write-Host ""
}

if ($Setup) {
    Log "Opening the setup window (forced)."
    Run-Cli @("gui")
    if (Needs-Setup) { Pause-BeforeExit; exit 1 }
    exit 0
}

if ($Supervise) { Start-Supervisor; exit 0 }

if ($Background) {
    # A manual launch is a little control panel: open the graphical window (the
    # setup wizard on first run, the settings panel afterwards; text wizard if
    # there's no display), then make sure the background watcher is running.
    Log "Opening the control panel window."
    Run-Cli @("gui")
    if (Needs-Setup) { Log "Setup not completed."; Pause-BeforeExit; exit 1 }

    $running = Get-LivePid $PidFile "powershell*"
    if ($running) {
        Log "Watcher already running (pid $running)."
        Pause-Info @("Settings saved. Easy Mode is already running in the background (pid $running).",
                     "Your LG TV will blank/sleep when the PC is idle.",
                     "To stop it: run this launcher again with  -Stop")
        exit 0
    }

    Log "Detaching watcher to background. Log: $LogFile"
    $repoSelf = Join-Path (App-Dir) $LauncherName
    $useSelf = if (Test-Path $repoSelf) { $repoSelf } else { $PSCommandPath }
    # Drop a stale pidfile first, so the wait below is positive proof that the NEW
    # supervisor came up (publishing its pid is its very first action).
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    $child = Start-Detached $useSelf @("-Supervise")

    # Confirm the watcher actually started instead of assuming it did. It runs
    # detached and hidden, so a watcher that dies on startup leaves nothing on
    # screen - which is exactly how a broken detach could report "running in the
    # background" while the TV was quietly never being watched. Wait for the
    # supervisor to publish its pid, or for the child to fall over.
    $live = $null
    foreach ($i in 1..40) {
        $live = Get-LivePid $PidFile "powershell*"
        if ($live) { break }
        if ($child -and $child.HasExited) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $live) {
        $code = if ($child -and $child.HasExited) { "$($child.ExitCode)" } else { "did not report" }
        Log "ERROR: the background watcher failed to start (exit code: $code)."
        Write-Host ""
        Write-Host "Your settings were saved, but Easy Mode could not start its"
        Write-Host "background watcher - so the TV would NOT sleep when the PC is idle."
        Write-Host "The log has the details: $LogFile"
        Pause-BeforeExit
        exit 1
    }
    Log "Watcher confirmed running (pid $live)."
    Pause-Info @("Settings saved. Easy Mode is now running in the background (pid $live).",
                 "Your LG TV will blank/sleep when the PC is idle, and wake when you",
                 "move the mouse or press a key.",
                 "Closing this window does NOT stop it. To stop: run with  -Stop")
    exit 0
}

# Default: foreground. Run setup first if needed, then supervise.
if (Needs-Setup) {
    Log "First run: opening the setup window."
    Run-Cli @("gui")
    if (Needs-Setup) { Log "Setup not completed."; Pause-BeforeExit; exit 1 }
}
Start-Supervisor

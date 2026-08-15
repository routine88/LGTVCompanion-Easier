<#
.SYNOPSIS
    Build the Windows application and its installer.

.DESCRIPTION
    Produces, in packaging\windows\dist\ :

      LGTV Companion Easy Mode\        the app (two .exes + runtime)
      LGTVCompanionEasyMode-Setup.exe  a one-file installer carrying the above

    PyInstaller is installed into a throwaway virtual environment under
    packaging\windows\.build-venv, so the build never touches the Python you use
    for anything else. Re-runs reuse it.

.PARAMETER Python
    Interpreter to build with. Defaults to the newest one 'py -3' resolves to.
    Whatever you pick is the Python that ends up inside the .exe, so it must
    have tkinter (a standard Windows install does).

.PARAMETER SkipInstaller
    Build just the application folder, not the Setup.exe.

.PARAMETER Clean
    Delete previous build/ and dist/ output first.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
#>
[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$SkipInstaller,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = (Resolve-Path (Join-Path $Here "..\..")).Path
$Venv = Join-Path $Here ".build-venv"
$DistDir = Join-Path $Here "dist"
$WorkDir = Join-Path $Here "build"

function Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Assert-LastExit([string]$What) {
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

# ---- 1. find an interpreter --------------------------------------------------
Step "Locating Python"
if (-not $Python) {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $Python = (& py -3 -c "import sys; print(sys.executable)")
    } else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $cmd) { throw "No Python found. Install it from python.org, then re-run." }
        $Python = $cmd.Source
    }
}
& $Python -c "import sys, tkinter; print(sys.version)"
Assert-LastExit "Python check (is tkinter installed?)"
Write-Host "    using $Python"

# ---- 2. the icons have to exist ---------------------------------------------
$Icon = Join-Path $Repo "EasyMode\lgtv_easy\assets\icon.ico"
if (-not (Test-Path $Icon)) {
    throw "Missing $Icon - run 'python packaging\make_icons.py' (needs Pillow)."
}

# ---- 3. build environment ----------------------------------------------------
if ($Clean) {
    Step "Cleaning previous output"
    foreach ($path in @($DistDir, $WorkDir)) {
        if (Test-Path $path) { Remove-Item -Recurse -Force $path }
    }
}

$VenvPython = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Step "Creating the build virtual environment"
    & $Python -m venv $Venv
    Assert-LastExit "venv creation"
}
Step "Installing PyInstaller into it"
& $VenvPython -m pip install --disable-pip-version-check --quiet --upgrade pip
& $VenvPython -m pip install --disable-pip-version-check --quiet "pyinstaller>=6.0"
Assert-LastExit "pip install pyinstaller"

# ---- 4. the application ------------------------------------------------------
Step "Building the application"
& $VenvPython -m PyInstaller --noconfirm --clean `
    --distpath $DistDir --workpath $WorkDir `
    (Join-Path $Here "app.spec")
Assert-LastExit "PyInstaller (app.spec)"

$AppDir = Join-Path $DistDir "LGTV Companion Easy Mode"
$AppExe = Join-Path $AppDir "LGTV Companion Easy Mode.exe"
if (-not (Test-Path $AppExe)) { throw "Expected $AppExe to exist after the build" }

# A build that cannot even print its own version is broken; catch it here rather
# than in front of a user.
Step "Smoke-testing the built app"
$cliExe = Join-Path $AppDir "lgtv-easy.exe"
$reported = (& $cliExe --version) 2>&1
Assert-LastExit "lgtv-easy.exe --version"
Write-Host "    $reported"

# ---- 5. the installer --------------------------------------------------------
if (-not $SkipInstaller) {
    Step "Building the installer"
    & $VenvPython -m PyInstaller --noconfirm --clean `
        --distpath $DistDir --workpath $WorkDir `
        (Join-Path $Here "installer.spec")
    Assert-LastExit "PyInstaller (installer.spec)"
}

# ---- 6. what came out --------------------------------------------------------
Step "Done"
Get-ChildItem $DistDir | ForEach-Object {
    if ($_.PSIsContainer) {
        $size = (Get-ChildItem $_.FullName -Recurse -File |
                 Measure-Object -Property Length -Sum).Sum
    } else {
        $size = $_.Length
    }
    "{0,-42} {1,8:N1} MB" -f $_.Name, ($size / 1MB)
}
Write-Host ""
Write-Host "Installer: $(Join-Path $DistDir 'LGTVCompanionEasyMode-Setup.exe')"
Write-Host "Run it to install, or 'LGTVCompanionEasyMode-Setup.exe /S' for silent."

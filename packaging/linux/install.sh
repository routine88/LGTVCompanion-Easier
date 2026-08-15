#!/bin/sh
# =============================================================================
#  LGTV Companion Easy Mode - Linux installer
# =============================================================================
#  Installs the app the way a desktop expects one to be installed, so it shows
#  up in the applications menu with its own icon, has a desktop shortcut, and
#  puts the right icon on the dock/taskbar button when it is running:
#
#    * the app             -> ~/.local/lib/lgtv-companion-easy   (or /opt)
#    * a command           -> ~/.local/bin/lgtv-easy
#    * menu entry          -> ~/.local/share/applications/
#    * icons, every size   -> ~/.local/share/icons/hicolor/*/apps/
#    * a desktop shortcut  -> ~/Desktop (marked trusted, so it just works)
#    * optionally, start-at-login
#
#  Run it from anywhere:      sh packaging/linux/install.sh
#  Remove everything again:   sh packaging/linux/install.sh --uninstall
#
#  Plain POSIX shell on purpose - it has to run under dash on a Debian netinst
#  as happily as under bash.
# =============================================================================
set -eu

APP_NAME="LGTV Companion Easy Mode"
APP_ID="lgtv-companion-easy"
# Must match lgtv_easy.branding.WM_CLASS, or the dock shows a generic icon for
# the running window instead of ours.
WM_CLASS="LGTVCompanionEasyMode"
CLI_NAME="lgtv-easy"

SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(CDPATH= cd -- "$SELF_DIR/../.." && pwd)
SOURCE_PKG="$REPO_DIR/EasyMode/lgtv_easy"

MODE="install"
SYSTEM=0
WANT_DESKTOP_ICON=1
WANT_AUTOSTART=1
WANT_DEPS=1
PURGE=0
QUIET=0

usage() {
    cat <<EOF
$APP_NAME - installer

Usage: sh install.sh [options]

  --system          install for all users (needs root; /opt + /usr/share)
  --no-desktop-icon do not put a shortcut on the Desktop
  --no-autostart    do not start the TV watcher when you log in
  --no-deps         do not try to install python3-tk and friends
  --uninstall       remove the app again (keeps your settings)
  --purge           with --uninstall: delete the saved settings too
  --quiet           only print problems
  -h, --help        this message
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --system) SYSTEM=1 ;;
        --no-desktop-icon) WANT_DESKTOP_ICON=0 ;;
        --no-autostart) WANT_AUTOSTART=0 ;;
        --no-deps) WANT_DEPS=0 ;;
        --uninstall|--remove) MODE="uninstall" ;;
        --purge) PURGE=1 ;;
        --quiet) QUIET=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

say() { [ "$QUIET" = "1" ] || printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---- where everything goes ---------------------------------------------------
if [ "$SYSTEM" = "1" ]; then
    [ "$(id -u)" = "0" ] || die "--system needs root. Re-run with sudo."
    LIB_DIR="/opt/$APP_ID"
    BIN_DIR="/usr/local/bin"
    DATA_DIR="/usr/share"
else
    # Deliberately ~/.local/lib and not ~/.local/share/$APP_ID: the portable
    # "Linux Launch.sh" keeps its git clone in the latter, and the two must not
    # end up deleting each other's files.
    LIB_DIR="$HOME/.local/lib/$APP_ID"
    BIN_DIR="$HOME/.local/bin"
    DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
fi
APPS_DIR="$DATA_DIR/applications"
ICONS_DIR="$DATA_DIR/icons/hicolor"
DESKTOP_FILE="$APPS_DIR/$APP_ID.desktop"
LAUNCHER="$BIN_DIR/$CLI_NAME"
STATE_DIR="${LGTV_EASY_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}/$APP_ID}"
AUTOSTART_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/$APP_ID.desktop"

desktop_dir() {
    if have xdg-user-dir; then
        xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop"
    else
        echo "$HOME/Desktop"
    fi
}

# ---- stop anything that is running ------------------------------------------
# SIGUSR1 means "stop the watcher and leave the TV exactly as it is"; SIGTERM
# would be read as a shutdown and power the TV off, which installing must not do.
stop_watcher() {
    for pidfile in "$STATE_DIR/daemon.pid" "$STATE_DIR/launcher.pid"; do
        [ -f "$pidfile" ] || continue
        pid=$(cat "$pidfile" 2>/dev/null || true)
        [ -n "${pid:-}" ] || continue
        if kill -0 "$pid" 2>/dev/null; then
            say "  stopping the running watcher (pid $pid)"
            kill -USR1 "$pid" 2>/dev/null || true
            sleep 1
        fi
    done
}

# ---- uninstall ---------------------------------------------------------------
if [ "$MODE" = "uninstall" ]; then
    say "Removing $APP_NAME…"
    stop_watcher
    if [ -x "$LAUNCHER" ]; then
        "$LAUNCHER" autostart disable >/dev/null 2>&1 || true
    fi
    rm -f "$AUTOSTART_FILE"
    rm -f "$DESKTOP_FILE" "$LAUNCHER"
    rm -f "$(desktop_dir)/$APP_ID.desktop"
    for size in 16 22 24 32 48 64 128 256 512; do
        rm -f "$ICONS_DIR/${size}x${size}/apps/$APP_ID.png"
    done
    rm -f "$ICONS_DIR/scalable/apps/$APP_ID.svg"
    rm -rf "$LIB_DIR"
    if [ "$PURGE" = "1" ]; then
        rm -rf "$STATE_DIR"
        say "  deleted saved settings ($STATE_DIR)"
    else
        say "  kept your settings in $STATE_DIR (use --purge to delete them)"
    fi
    have update-desktop-database && update-desktop-database "$APPS_DIR" 2>/dev/null || true
    have gtk-update-icon-cache && gtk-update-icon-cache -f -t "$ICONS_DIR" 2>/dev/null || true
    say "Done. $APP_NAME has been removed."
    exit 0
fi

# ---- sanity ------------------------------------------------------------------
[ -d "$SOURCE_PKG" ] || die "Cannot find the app at $SOURCE_PKG.
Run this script from inside the project folder you downloaded."
[ -f "$SOURCE_PKG/assets/icon.png" ] || warn "icons are missing from the source tree"

# ---- dependencies ------------------------------------------------------------
PYTHON=""
for candidate in python3 python; do
    if have "$candidate"; then PYTHON=$(command -v "$candidate"); break; fi
done

install_packages() {
    # $@ = distro-independent wish list, already narrowed to what is missing.
    [ $# -gt 0 ] || return 0
    SUDO=""
    [ "$(id -u)" != "0" ] && have sudo && SUDO="sudo"
    if have apt-get; then
        $SUDO apt-get update -qq || true
        $SUDO apt-get install -y "$@"
    elif have dnf; then
        $SUDO dnf install -y "$@"
    elif have pacman; then
        $SUDO pacman -S --needed --noconfirm "$@"
    elif have zypper; then
        $SUDO zypper install -y "$@"
    else
        warn "no known package manager; please install by hand: $*"
        return 1
    fi
}

# Package names differ per distro for the same three things: Python, its Tk
# bindings (the graphical window), and the two optional helpers that make idle
# detection accurate on X11 and Wayland.
missing=""
if [ -z "$PYTHON" ]; then missing="$missing python3"; fi
if [ -n "$PYTHON" ] && ! "$PYTHON" -c "import tkinter" >/dev/null 2>&1; then
    if have apt-get; then missing="$missing python3-tk"
    elif have dnf; then missing="$missing python3-tkinter"
    elif have pacman; then missing="$missing tk"
    elif have zypper; then missing="$missing python3-tk"
    fi
fi
have xprintidle || missing="$missing xprintidle"
if ! have gdbus; then
    if have apt-get; then missing="$missing libglib2.0-bin"
    elif have dnf || have zypper; then missing="$missing glib2"
    elif have pacman; then missing="$missing glib2"
    fi
fi

if [ -n "$missing" ] && [ "$WANT_DEPS" = "1" ]; then
    say "Installing what's missing:$missing"
    # shellcheck disable=SC2086 - deliberate word splitting of the wish list
    install_packages $missing || warn "could not install:$missing (continuing)"
    for candidate in python3 python; do
        if have "$candidate"; then PYTHON=$(command -v "$candidate"); break; fi
    done
fi

[ -n "$PYTHON" ] || die "Python 3 is required and could not be installed automatically."
if ! "$PYTHON" -c "import tkinter" >/dev/null 2>&1; then
    warn "python3-tk is missing: the setup window cannot open, so setup will
         run as a text wizard in a terminal instead."
fi

# ---- install -----------------------------------------------------------------
say "Installing $APP_NAME"
say "  app      -> $LIB_DIR"
stop_watcher

rm -rf "$LIB_DIR"
mkdir -p "$LIB_DIR"
cp -r "$SOURCE_PKG" "$LIB_DIR/lgtv_easy"
# Byte-code caches from the source tree are noise at best and stale at worst.
find "$LIB_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

mkdir -p "$BIN_DIR"
cat >"$LAUNCHER" <<EOF
#!/bin/sh
# $APP_NAME - installed by packaging/linux/install.sh
# Runs the app from $LIB_DIR whatever the working directory is.
PYTHONPATH="$LIB_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
export PYTHONPATH
exec "$PYTHON" -m lgtv_easy "\$@"
EOF
chmod 0755 "$LAUNCHER"
say "  command  -> $LAUNCHER"

# ---- icons -------------------------------------------------------------------
for size in 16 22 24 32 48 64 128 256 512; do
    src="$SOURCE_PKG/assets/icon-$size.png"
    [ -f "$src" ] || continue
    mkdir -p "$ICONS_DIR/${size}x${size}/apps"
    cp "$src" "$ICONS_DIR/${size}x${size}/apps/$APP_ID.png"
done
if [ -f "$SOURCE_PKG/assets/icon.svg" ]; then
    mkdir -p "$ICONS_DIR/scalable/apps"
    cp "$SOURCE_PKG/assets/icon.svg" "$ICONS_DIR/scalable/apps/$APP_ID.svg"
fi
say "  icons    -> $ICONS_DIR"

# ---- the menu entry ----------------------------------------------------------
# StartupWMClass is the important line: it ties the running window to this entry,
# which is how the dock/taskbar button gets the app's icon and name instead of a
# grey placeholder marked "python3".
mkdir -p "$APPS_DIR"
cat >"$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$APP_NAME
GenericName=TV screen sleep
Comment=Sleep your LG TV like a PC monitor, and wake it when you return
Exec=$LAUNCHER gui
TryExec=$LAUNCHER
Icon=$APP_ID
Terminal=false
StartupNotify=true
StartupWMClass=$WM_CLASS
Categories=Utility;Settings;HardwareSettings;
Keywords=LG;TV;OLED;monitor;idle;sleep;screen;burn-in;
Actions=Repair;TVOff;

[Desktop Action Repair]
Name=Test and repair the TV connection
Exec=$LAUNCHER repair
Terminal=true

[Desktop Action TVOff]
Name=Turn the TV off now
Exec=$LAUNCHER off
Terminal=false
EOF
chmod 0644 "$DESKTOP_FILE"
say "  menu     -> $DESKTOP_FILE"

if have desktop-file-validate; then
    desktop-file-validate "$DESKTOP_FILE" || warn "the .desktop file did not validate (see above)"
fi

# ---- the desktop shortcut ----------------------------------------------------
if [ "$WANT_DESKTOP_ICON" = "1" ] && [ "$SYSTEM" != "1" ]; then
    DESK=$(desktop_dir)
    if [ -d "$DESK" ]; then
        cp "$DESKTOP_FILE" "$DESK/$APP_ID.desktop"
        chmod 0755 "$DESK/$APP_ID.desktop"
        # GNOME 42+ refuses to run a desktop file unless it is marked trusted;
        # without this the user gets "Untrusted application launcher".
        if have gio; then
            gio set "$DESK/$APP_ID.desktop" "metadata::trusted" true 2>/dev/null || true
        fi
        say "  shortcut -> $DESK/$APP_ID.desktop"
    else
        warn "no Desktop folder found at $DESK; skipped the desktop shortcut"
    fi
fi

# ---- refresh the desktop's caches -------------------------------------------
have update-desktop-database && update-desktop-database "$APPS_DIR" 2>/dev/null || true
have gtk-update-icon-cache && gtk-update-icon-cache -f -t "$ICONS_DIR" 2>/dev/null || true
have xdg-desktop-menu && xdg-desktop-menu forceupdate 2>/dev/null || true

# ---- start at login ----------------------------------------------------------
if [ "$WANT_AUTOSTART" = "1" ]; then
    if "$LAUNCHER" autostart enable >/dev/null 2>&1; then
        say "  login    -> the TV watcher starts when you log in"
    else
        warn "could not register the login auto-start; enable it in the app's window"
    fi
fi

# ---- is ~/.local/bin actually usable? ---------------------------------------
case ":${PATH}:" in
    *":$BIN_DIR:"*) ;;
    *) [ "$SYSTEM" = "1" ] || say "
Note: $BIN_DIR is not on your PATH, so the '$CLI_NAME' command will not be
found in a terminal until you add it (log out and back in usually does it).
The menu entry and desktop icon work regardless." ;;
esac

say "
$APP_NAME is installed.

  Open it     : from your applications menu, or the desktop icon
  In a shell  : $CLI_NAME gui        (setup)   /   $CLI_NAME status
  Uninstall   : sh $SELF_DIR/install.sh --uninstall

On the TV, turn on \"Turn on via Wi-Fi\" (Quick Start+ / Always Ready) so it can
be woken over the network."

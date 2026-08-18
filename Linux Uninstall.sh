#!/bin/sh
# ============================================================================
#  LGTV Companion Easy Mode - Linux uninstaller
# ============================================================================
#  Removes every trace of the app, however it got here:
#
#    * the installed copy from packaging/linux/install.sh
#    * the self-updating clone the portable "Linux Launch.sh" downloads
#    * the start-at-login entry
#    * the applications-menu entry, the desktop shortcut and the icons
#
#  Why this exists when packaging/linux/uninstall.sh already does the first
#  half: that one is a wrapper around the installer, so it only knows what the
#  installer put there. Someone who ran the portable launcher never touched the
#  installer at all, and is left with an autostart entry and a git clone that
#  nothing will ever clean up. This handles both, and needs neither the
#  installer nor the app to still be on disk.
#
#  Your settings (the TV you paired with, your idle timeout) are KEPT unless you
#  ask otherwise, so reinstalling picks up where you left off.
#
#    sh "Linux Uninstall.sh"                  ask about the settings
#    sh "Linux Uninstall.sh" --purge          delete the settings too
#    sh "Linux Uninstall.sh" --keep-settings  keep them, don't ask
#    sh "Linux Uninstall.sh" --system         also remove a system-wide install
#                                             (needs sudo)
# ============================================================================
set -eu

APP_NAME="LGTV Companion Easy Mode"
APP_ID="lgtv-companion-easy"
CLI_NAME="lgtv-easy"

PURGE="ask"
SYSTEM=0
REMOVED=0

say() { printf '%s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
    cat <<'USAGE'
Usage: sh "Linux Uninstall.sh" [options]

  --purge          also delete your saved settings and TV pairing
  --keep-settings  keep them without being asked
  --system         also remove a system-wide install from /opt and /usr (root)
  -h, --help       this text
USAGE
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --purge) PURGE="yes" ;;
        --keep-settings|--keep) PURGE="no" ;;
        --system) SYSTEM=1 ;;
        -h|--help) usage ;;
        *) warn "unknown option: $1"; usage ;;
    esac
    shift
done

# ---- where everything lives --------------------------------------------------
# These mirror packaging/linux/install.sh exactly. They are repeated rather than
# sourced because this script has to work when the checkout it came from is
# already gone - which is precisely the state that leaves an autostart entry
# pointing at nothing.
if [ "$SYSTEM" = "1" ]; then
    [ "$(id -u)" = "0" ] || die "--system needs root. Re-run with sudo."
    LIB_DIR="/opt/$APP_ID"
    BIN_DIR="/usr/local/bin"
    DATA_DIR="/usr/share"
else
    LIB_DIR="$HOME/.local/lib/$APP_ID"
    BIN_DIR="$HOME/.local/bin"
    DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"
fi
APPS_DIR="$DATA_DIR/applications"
ICONS_DIR="$DATA_DIR/icons/hicolor"
LAUNCHER="$BIN_DIR/$CLI_NAME"
STATE_DIR="${LGTV_EASY_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}/$APP_ID}"
AUTOSTART_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/$APP_ID.desktop"
# The portable launcher's clone. Deliberately NOT the same as LIB_DIR - the two
# installs coexist, and one must never delete the other's files by accident.
PORTABLE_DIR="${LGTV_EASY_APP_HOME:-$HOME/.local/share/$APP_ID}"

desktop_dir() {
    if have xdg-user-dir; then
        xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop"
    else
        echo "$HOME/Desktop"
    fi
}

# ---- run from a copy if we are standing on ground we are about to delete -----
# The portable clone contains this very file, and the shell reads a script as it
# goes: deleting the directory out from under ourselves would stop the uninstall
# halfway, with the autostart entry gone and the app still there or the reverse.
SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/$(basename -- "$0")
case "$SELF" in
    "$PORTABLE_DIR"/*|"$LIB_DIR"/*)
        if [ "${LGTV_UNINSTALL_RELAUNCHED:-}" != "1" ]; then
            COPY=$(mktemp "${TMPDIR:-/tmp}/lgtv-easy-uninstall.XXXXXX")
            cat "$SELF" > "$COPY"
            # Hand the copy the same answers we were given, so it never asks
            # about the settings a second time.
            set --
            if [ "$PURGE" = "yes" ]; then set -- "$@" --purge; fi
            if [ "$PURGE" = "no" ]; then set -- "$@" --keep-settings; fi
            if [ "$SYSTEM" = "1" ]; then set -- "$@" --system; fi
            rc=0
            LGTV_UNINSTALL_RELAUNCHED=1 sh "$COPY" "$@" || rc=$?
            rm -f "$COPY"
            exit "$rc"
        fi
        ;;
esac

# ---- the settings question ---------------------------------------------------
if [ "$PURGE" = "ask" ]; then
    if [ -t 0 ] && [ -d "$STATE_DIR" ]; then
        say "Your settings - the TV you paired with, and your idle timeout - live in"
        say "  $STATE_DIR"
        say "Keeping them means a reinstall needs no setup at all."
        printf 'Delete them as well? [y/N] '
        read -r answer || answer=""
        case "$answer" in
            [Yy]|[Yy][Ee][Ss]) PURGE="yes" ;;
            *) PURGE="no" ;;
        esac
        say ""
    else
        PURGE="no"
    fi
fi

# ---- helpers -----------------------------------------------------------------
rm_path() {
    [ -e "$1" ] || [ -L "$1" ] || return 0
    if rm -rf "$1" 2>/dev/null; then
        say "  removed $1"
        REMOVED=1
    else
        warn "could not remove $1 - delete it by hand"
    fi
}

# SIGUSR1 means "stop the watcher and leave the TV exactly as it is". SIGTERM
# would be read as a shutdown and power the TV off, which uninstalling must not
# do - the screen you are reading this on may well be that TV.
stop_watcher() {
    for pidfile in "$STATE_DIR/daemon.pid" "$STATE_DIR/launcher.pid"; do
        [ -f "$pidfile" ] || continue
        pid=$(cat "$pidfile" 2>/dev/null || true)
        [ -n "${pid:-}" ] || continue
        if kill -0 "$pid" 2>/dev/null; then
            say "  stopped a running watcher, pid $pid"
            kill -USR1 "$pid" 2>/dev/null || true
            REMOVED=1
            sleep 1
        fi
    done
}

# ---- do it -------------------------------------------------------------------
say ""
say " Uninstalling $APP_NAME"
say " ---------------------------------------------------------------"
say ""

say "Stopping the watcher..."
stop_watcher

say "Removing the start-at-login entry..."
# Ask the app to undo its own registration first, while it is still on disk and
# knows every form that entry might have taken.
if [ -x "$LAUNCHER" ]; then
    "$LAUNCHER" autostart disable >/dev/null 2>&1 || true
fi
rm_path "$AUTOSTART_FILE"

say "Removing the menu entry, shortcut and icons..."
rm_path "$APPS_DIR/$APP_ID.desktop"
rm_path "$(desktop_dir)/$APP_ID.desktop"
for size in 16 22 24 32 48 64 128 256 512; do
    rm_path "$ICONS_DIR/${size}x${size}/apps/$APP_ID.png"
done
rm_path "$ICONS_DIR/scalable/apps/$APP_ID.svg"

say "Removing the app..."
rm_path "$LAUNCHER"
rm_path "$LIB_DIR"
rm_path "$PORTABLE_DIR"

have update-desktop-database && update-desktop-database "$APPS_DIR" 2>/dev/null || true
have gtk-update-icon-cache && gtk-update-icon-cache -f -t "$ICONS_DIR" 2>/dev/null || true

if [ "$PURGE" = "yes" ]; then
    rm_path "$STATE_DIR"
elif [ -d "$STATE_DIR" ]; then
    say ""
    say "Kept your settings in $STATE_DIR"
    say "Delete that folder by hand, or re-run this with --purge, to be rid of them."
fi

say ""
if [ "$REMOVED" = "1" ]; then
    say "Done. $APP_NAME is gone."
    say ""
    say "The portable \"Linux Launch.sh\" and packaging/linux/install.sh both still"
    say "work if you want it back - nothing here touched the folder you ran this from."
else
    say "Nothing to remove - $APP_NAME was not installed for this account."
    [ "$SYSTEM" = "1" ] || say "A system-wide install? Re-run with:  sudo sh \"Linux Uninstall.sh\" --system"
fi

#!/usr/bin/env bash
#
# LGTV Companion Easy Mode - self-updating launcher (Ubuntu / Linux)
# -----------------------------------------------------------------------------
# This is the ONE file Linux users run. It:
#   1. Installs dependencies (git, python3, tkinter for the GUI).
#   2. Updates the app from GitHub's default branch - INCLUDING updates to this
#      very launcher (it re-executes itself if it changed) - and REPORTS what
#      happened: updated (with what arrived), already current, or couldn't
#      reach GitHub. An update that silently does nothing is the failure this
#      reporting exists to make impossible.
#   3. Opens the graphical setup window on first use (text wizard if headless).
#   4. Supervises the idle daemon in the background, restarting it if it crashes.
#      All errors go to a persistent log.
#
# Updates are applied WHEN YOU RUN THIS LAUNCHER, and at no other time. There is
# deliberately no periodic background check: a watcher that rewrites its own code
# and restarts itself midway through an evening is a surprise, not a feature.
#
# Usage:
#   ./"Linux Launch.sh"              # set up (if needed), run in foreground
#   ./"Linux Launch.sh" --background # detach and run as a background daemon
#   ./"Linux Launch.sh" --setup      # force the setup wizard, then exit
#   ./"Linux Launch.sh" --stop       # stop a running background supervisor
#
# Safe to re-run any time; it is idempotent.
# -----------------------------------------------------------------------------
set -uo pipefail

# ---- configuration ----------------------------------------------------------
REPO_URL="${LGTV_EASY_REPO:-https://github.com/routine88/lgtvcompanion-easier.git}"
# Track the repository's default branch (master). Override with LGTV_EASY_BRANCH.
REPO_BRANCH="${LGTV_EASY_BRANCH:-master}"
APP_HOME="${LGTV_EASY_APP_HOME:-$HOME/.local/share/lgtv-companion-easy}"
STATE_DIR="${LGTV_EASY_HOME:-$HOME/.config/lgtv-companion-easy}"
LOG_FILE="$STATE_DIR/launcher.log"
PID_FILE="$STATE_DIR/launcher.pid"
# The single-instance lock the idle daemon takes (lgtv_easy/singleton.py). We
# need it by name to retire a stale daemon after an update - see
# retire_stale_daemon, which is the whole reason updates used to not take.
DAEMON_PID_FILE="$STATE_DIR/daemon.pid"
# Set LGTV_EASY_NO_UPDATE=1 to freeze the code: no git fetch/clone and no
# self-update. Run only the code already on disk.
NO_UPDATE="${LGTV_EASY_NO_UPDATE:-0}"
# The Python app lives in the EasyMode/ subdirectory of the repo; this launcher
# lives at the repo root.
SUBDIR="EasyMode"
LAUNCHER_NAME="Linux Launch.sh"
# The GUI returns this when the user pressed "Kill process": the stop was
# deliberate, so do NOT start the supervisor once the window closes. Without
# honouring it, closing the window would silently restart everything the button
# just stopped. Must match EXIT_SERVICE_STOPPED in lgtv_easy/gui.py.
EXIT_SERVICE_STOPPED=10

mkdir -p "$STATE_DIR"

# ---- console presentation ---------------------------------------------------
# Defined before log(), which uses these: `set -u` is on, so an unset colour
# variable would abort the script rather than merely print plainly.
#
# Two audiences, two functions. log() keeps the timestamped engineering record;
# say() talks to the person watching the window. Everything say()s is recorded
# too (with the colour stripped), so a pasted log tells the same story the
# window did - the usual support trap is a screen that said something the log
# did not.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_ACC=$'\033[36m'
else
  C_RESET=""; C_DIM=""; C_BOLD=""; C_OK=""; C_WARN=""; C_ERR=""; C_ACC=""
fi
RULE="======================================================================"

log() {
  local line; line="$(date '+%Y-%m-%d %H:%M:%S') [launcher] $*"
  printf '%s\n' "$line" >>"$LOG_FILE"
  # Echo to the terminal too, but only when one is attached. In the detached
  # background supervisor stderr is already redirected to the log file, so
  # writing there as well would duplicate every line.
  #
  # On a terminal the timestamp and [launcher] tag are dropped and the line is
  # dimmed: they are what a log needs and exactly what a person reading a window
  # does not. Nothing is lost - the file above keeps the full form - and the
  # progress the user actually cares about (say/report_update) stays legible
  # instead of being buried in machine prefixes.
  [ -t 2 ] && printf '%s\n' "  ${C_DIM}$*${C_RESET}" >&2
  return 0
}

say() {
  local msg="$*"
  # Only to the terminal: the detached supervisor has stdout pointed at the log
  # already, so printing there too would double every line.
  [ -t 1 ] && printf '%s\n' "$msg"
  printf '%s [ui] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" \
    "$(printf '%s' "$msg" | sed $'s/\033\\[[0-9;]*m//g')" >>"$LOG_FILE"
  return 0
}

banner() {
  say ""
  say "${C_ACC}${RULE}${C_RESET}"
  say "  ${C_BOLD}LGTV Companion Easy Mode${C_RESET}"
  say "${C_ACC}${RULE}${C_RESET}"
}

# Keep the terminal open after a failure so the user can read and report the
# diagnostics printed above (the window otherwise closes the instant we exit).
pause_before_exit() {
  if [ -t 0 ]; then
    echo ""
    echo "----------------------------------------------------------------------"
    echo "  Setup did not finish. The diagnostics above (and the log file"
    echo "  $LOG_FILE) can be shared to get help."
    echo "  This window will stay open so nothing is lost."
    echo "----------------------------------------------------------------------"
    read -r -p "Press Enter to close this window... " _ || true
  fi
}

# Hash of this script as it was when we started, so we can tell if a git update
# rewrote it underneath us and re-execute the new version.
SELF_PATH="$(readlink -f "$0" 2>/dev/null || echo "$0")"
LAUNCHER_START_HASH="$( (sha1sum "$SELF_PATH" 2>/dev/null || echo none) | cut -d' ' -f1)"

# ---- dependency installation ------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

install_deps() {
  local need_pkgs=()
  have git || need_pkgs+=("git")
  have python3 || need_pkgs+=("python3")
  # tkinter is needed for the graphical wizard; the app still works headless.
  python3 -c "import tkinter" >/dev/null 2>&1 || need_pkgs+=("python3-tk")
  # xprintidle gives accurate idle detection on X11 (optional but recommended).
  have xprintidle || need_pkgs+=("xprintidle")
  # gdbus (from glib) is used for Wayland/GNOME idle detection and to notice when
  # the PC suspends so the TV can sleep with it. Optional - the app degrades if
  # it's missing - but recommended.
  have gdbus || need_pkgs+=("libglib2.0-bin")

  if [ "${#need_pkgs[@]}" -eq 0 ]; then
    log "All dependencies present."
    return 0
  fi
  log "Installing dependencies: ${need_pkgs[*]}"
  if have apt-get; then
    local SUDO=""; [ "$(id -u)" -ne 0 ] && have sudo && SUDO="sudo"
    $SUDO apt-get update -y -q >>"$LOG_FILE" 2>&1 || log "apt-get update failed (continuing)"
    $SUDO apt-get install -y -q "${need_pkgs[@]}" >>"$LOG_FILE" 2>&1 \
      || log "WARNING: could not install some packages: ${need_pkgs[*]}"
  else
    log "WARNING: apt-get not found. Please install manually: ${need_pkgs[*]}"
  fi
}

# ---- repository / self-update ----------------------------------------------
# Outcome of the last sync_repo, so the caller can both REPORT it and decide
# whether anything actually needs restarting. Previously the caller could only
# tell "git didn't error", which is not the same question at all - it restarted
# the daemon on every check whether or not a single byte had changed.
#   SYNC_RESULT : cloned | updated | current | offline | failed
SYNC_RESULT=""
SYNC_OLD=""       # commit before
SYNC_NEW=""       # commit after
SYNC_COUNT=0      # how many commits arrived
SYNC_SUBJECTS=""  # their subject lines, newest first

repo_head() { git -C "$APP_HOME" rev-parse --short HEAD 2>/dev/null || echo ""; }
repo_age()  { git -C "$APP_HOME" log -1 --format='%cr' 2>/dev/null || echo "unknown age"; }

sync_repo() {
  SYNC_RESULT=""; SYNC_OLD=""; SYNC_NEW=""; SYNC_COUNT=0; SYNC_SUBJECTS=""
  if [ ! -d "$APP_HOME/.git" ]; then
    log "Cloning $REPO_URL into $APP_HOME"
    if git clone --branch "$REPO_BRANCH" "$REPO_URL" "$APP_HOME" >>"$LOG_FILE" 2>&1; then
      SYNC_RESULT="cloned"; SYNC_NEW="$(repo_head)"; return 0
    fi
    SYNC_RESULT="failed"; log "ERROR: clone failed"; return 1
  fi
  SYNC_OLD="$(repo_head)"
  log "Checking GitHub for updates ($REPO_BRANCH)"
  if ! git -C "$APP_HOME" fetch --quiet origin "$REPO_BRANCH" >>"$LOG_FILE" 2>&1; then
    SYNC_RESULT="offline"; SYNC_NEW="$SYNC_OLD"
    log "Fetch failed (offline?); keeping the copy on disk."
    return 0
  fi
  git -C "$APP_HOME" checkout --quiet "$REPO_BRANCH" >>"$LOG_FILE" 2>&1 || true
  if ! git -C "$APP_HOME" reset --hard "origin/$REPO_BRANCH" >>"$LOG_FILE" 2>&1; then
    SYNC_RESULT="failed"; SYNC_NEW="$SYNC_OLD"
    log "ERROR: could not apply the update."
    return 1
  fi
  SYNC_NEW="$(repo_head)"
  if [ "$SYNC_NEW" = "$SYNC_OLD" ]; then
    SYNC_RESULT="current"
  else
    SYNC_RESULT="updated"
    # A force-push can leave the old commit unreachable, so both of these are
    # best-effort decoration - never let them fail the update.
    SYNC_COUNT="$(git -C "$APP_HOME" rev-list --count "$SYNC_OLD..$SYNC_NEW" 2>/dev/null || echo 0)"
    SYNC_SUBJECTS="$(git -C "$APP_HOME" log --format='%s' "$SYNC_OLD..$SYNC_NEW" 2>/dev/null | head -6)"
  fi
  return 0
}

# True only when new code actually arrived - the trigger for restarting anything.
sync_changed() { [ "$SYNC_RESULT" = "updated" ] || [ "$SYNC_RESULT" = "cloned" ]; }

# Say plainly whether the update worked. "It launched" and "it updated" are
# different claims, and only one of them used to be visible.
report_update() {
  case "$SYNC_RESULT" in
    cloned)
      say "  ${C_OK}[ok]${C_RESET} Installed from GitHub ${C_DIM}(${SYNC_NEW})${C_RESET}"
      ;;
    updated)
      say "  ${C_OK}[ok]${C_RESET} Updated ${C_DIM}${SYNC_OLD}${C_RESET} ${C_BOLD}->${C_RESET} ${C_ACC}${SYNC_NEW}${C_RESET}  ${C_DIM}(${SYNC_COUNT} new)${C_RESET}"
      printf '%s\n' "$SYNC_SUBJECTS" | while IFS= read -r subject; do
        [ -n "$subject" ] && say "         ${C_DIM}- ${subject}${C_RESET}"
      done
      ;;
    current)
      say "  ${C_OK}[ok]${C_RESET} Already up to date ${C_DIM}(${SYNC_NEW}, $(repo_age))${C_RESET}"
      ;;
    offline)
      say "  ${C_WARN}[!]${C_RESET}  Could not reach GitHub - update SKIPPED."
      say "       ${C_DIM}Running the copy already on disk (${SYNC_NEW}, $(repo_age)).${C_RESET}"
      ;;
    failed)
      say "  ${C_ERR}[X]${C_RESET}  Update FAILED - see $LOG_FILE"
      say "       ${C_DIM}Running the copy already on disk ($(repo_head)).${C_RESET}"
      ;;
    *)
      say "  ${C_DIM}[-]  Update check skipped.${C_RESET}"
      ;;
  esac
}

# Retire whatever daemon holds the single-instance lock, so newly-pulled code is
# actually loaded.
#
# THE trap this exists for: Python reads its source once, at process start. A
# daemon started at LOGIN (the autostart entry) holds the lock for the whole
# session, so after an update the supervisor's own child just queues behind it
# forever and the app stays pinned to old code with nothing on screen to say so.
# Restarting our own child is not enough, because the holder is often not our
# child. Both machines hit this; on Windows it looked like an update that simply
# never took.
#
# SIGUSR1, never SIGTERM: the daemon's TERM handler powers the TV off.
retire_stale_daemon() {
  local holder
  holder="$(cat "$DAEMON_PID_FILE" 2>/dev/null || echo)"
  case "$holder" in ''|*[!0-9]*) return 0 ;; esac
  kill -0 "$holder" 2>/dev/null || return 0
  log "Retiring daemon pid $holder so the new code is loaded."
  kill -USR1 "$holder" 2>/dev/null || return 0
  local _i
  for _i in $(seq 1 20); do
    kill -0 "$holder" 2>/dev/null || { log "Daemon pid $holder stood down."; return 0; }
    sleep 0.25
  done
  log "WARNING: daemon pid $holder did not stand down; it may still run old code."
  return 0
}

# This is how the launcher updates itself after a git pull:
#  - If we were started from a copy outside the repo (a bootstrap), hand off to
#    the canonical repo copy.
#  - If we ARE the repo copy and git rewrote it underneath us, re-exec the new
#    version (detected by comparing the start-time hash to the on-disk hash).
# True if we're a bootstrap copy that should hand off to the canonical repo
# launcher, or git rewrote this launcher underneath us (start-time hash differs
# from the on-disk hash). Either way the running launcher should re-exec.
launcher_changed() {
  local repo_launcher="$APP_HOME/$LAUNCHER_NAME"
  [ -f "$repo_launcher" ] || return 1
  [ "$SELF_PATH" != "$(readlink -f "$repo_launcher")" ] && return 0
  local now_hash; now_hash="$( (sha1sum "$SELF_PATH" 2>/dev/null || echo none) | cut -d' ' -f1)"
  [ "$now_hash" != "$LAUNCHER_START_HASH" ]
}

maybe_self_update() {
  launcher_changed || return 0
  local repo_launcher="$APP_HOME/$LAUNCHER_NAME"
  export LGTV_EASY_HANDOFF=1
  if [ "$SELF_PATH" != "$(readlink -f "$repo_launcher")" ]; then
    log "Handing off to the canonical repo launcher."
    exec "$repo_launcher" "$@"
  fi
  log "Launcher updated itself; re-executing new version."
  exec "$SELF_PATH" "$@"
}

APP_DIR() { echo "$APP_HOME/$SUBDIR"; }

run_cli() { ( cd "$(APP_DIR)" && python3 -m lgtv_easy "$@" ); }

needs_setup() {
  ! python3 - "$STATE_DIR/config.json" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    sys.exit(0 if d.get("setup_complete") and d.get("device", {}).get("key") else 1)
except Exception:
    sys.exit(1)
PY
}

# ---- the supervisor loop ----------------------------------------------------
supervise() {
  # Never run two supervisors at once. Re-opening the app runs the default case,
  # which falls through to supervise(); without this guard each open would clobber
  # PID_FILE (orphaning the previous supervisor so --stop can't find it) and spawn
  # another daemon that just blocks forever waiting for the lock the first daemon
  # already holds - so watchers pile up on every re-open. If a live supervisor
  # already owns the pidfile, stand down and let it keep driving the TV.
  if [ -f "$PID_FILE" ]; then
    local existing; existing="$(cat "$PID_FILE" 2>/dev/null || echo)"
    if [ -n "$existing" ] && [ "$existing" != "$$" ] && kill -0 "$existing" 2>/dev/null; then
      log "A background watcher is already running (pid $existing); not starting another."
      return 0
    fi
  fi
  echo $$ > "$PID_FILE"
  local daemon_pid=""
  # Signal handling, kept deliberately distinct because the two outcomes are
  # opposite - and the daemon child reads them the same way:
  #   * SIGUSR1 / SIGINT -> a plain "stop the watcher" (--stop, or Ctrl+C):
  #     leave the TV exactly as it is. We forward SIGUSR1 to the daemon.
  #   * SIGTERM -> a real machine shutdown or logoff: power the TV OFF. We
  #     forward SIGTERM so the daemon's shutdown handler turns it off.
  # Earlier this forwarded SIGUSR1 on *both* INT and TERM ("never power off"),
  # which at real shutdown raced systemd's own SIGTERM to the daemon and usually
  # won - so the daemon exited before powering off and the TV was left on. Now
  # --stop targets the supervisor with SIGUSR1 (see stop_background), leaving
  # SIGTERM to mean shutdown.
  stop_leave_tv() {
    log "Supervisor stopping (leaving the TV as-is)."
    [ -n "$daemon_pid" ] && kill -USR1 "$daemon_pid" 2>/dev/null
    rm -f "$PID_FILE"; exit 0
  }
  stop_power_off() {
    log "Supervisor stopping for shutdown (powering the TV off)."
    [ -n "$daemon_pid" ] && kill -TERM "$daemon_pid" 2>/dev/null
    rm -f "$PID_FILE"; exit 0
  }
  trap stop_leave_tv INT USR1
  trap stop_power_off TERM
  log "Supervisor started (pid $$). Daemon errors are logged here."
  # If another watcher (e.g. the login auto-start) already holds the lock, our
  # daemon child should wait for it rather than spin-restart.
  export LGTV_EASY_WAIT_LOCK=1

  while true; do
    log "Starting idle daemon."
    # Run the daemon; capture its stderr/stdout into the persistent log.
    ( cd "$(APP_DIR)" && exec python3 -m lgtv_easy run ) >>"$LOG_FILE" 2>&1 &
    daemon_pid=$!

    # Watch the daemon. There is deliberately no update check in here: updates
    # are applied when the user runs the launcher, so a watcher never rewrites
    # its own code and restarts itself out from under someone mid-evening. (The
    # old hourly check also restarted the daemon on EVERY pass, whether or not
    # anything had actually changed.)
    while kill -0 "$daemon_pid" 2>/dev/null; do
      # Interruptible sleep: backgrounding sleep and waiting on it lets a stop
      # signal take effect immediately, instead of after the full interval
      # (bash defers traps until the current foreground command returns).
      sleep 15 & wait $! 2>/dev/null
    done

    wait "$daemon_pid" 2>/dev/null
    local rc=$?
    log "Daemon exited (code $rc). Restarting in 5s."
    sleep 5 & wait $! 2>/dev/null
  done
}

stop_background() {
  local stopped=0
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    local sp; sp="$(cat "$PID_FILE")"
    log "Stopping background supervisor (pid $sp)."
    # SIGUSR1 = "stop the watcher, leave the TV alone". SIGTERM is reserved for a
    # real OS shutdown (power the TV off), so --stop must NOT use it.
    kill -USR1 "$sp" 2>/dev/null
    # Give it up to ~10s to run its trap (stop the daemon) and exit.
    local _i
    for _i in $(seq 1 20); do kill -0 "$sp" 2>/dev/null || break; sleep 0.5; done
    stopped=1
  fi
  # Also stop the idle daemon directly, in case it outlived its supervisor (or
  # was started by the login auto-start, which has no supervisor). SIGUSR1 means
  # "quit without powering off the TV"; fall back to SIGKILL, never SIGTERM
  # (which would power the TV off).
  local dp="$STATE_DIR/daemon.pid"
  if [ -f "$dp" ] && kill -0 "$(cat "$dp")" 2>/dev/null; then
    local d; d="$(cat "$dp")"
    log "Stopping idle daemon (pid $d)."
    kill -USR1 "$d" 2>/dev/null
    sleep 1
    kill -0 "$d" 2>/dev/null && kill -KILL "$d" 2>/dev/null
    stopped=1
  fi
  rm -f "$PID_FILE"
  if [ "$stopped" = "1" ]; then
    log "Easy Mode stopped. Your TV is left as-is."
  else
    log "No running background watcher found."
  fi
}

# ---- main -------------------------------------------------------------------
main() {
  case "${1:-}" in
    --stop) stop_background; exit 0 ;;
  esac

  # The bootstrap copy installs deps and self-updates, then hands off to the
  # up-to-date internal copy (LGTV_EASY_HANDOFF=1) - which skips redoing all that.
  if [ "${LGTV_EASY_HANDOFF:-0}" = "1" ]; then
    log "Running the up-to-date launcher."
  else
    banner
    say "  ${C_DIM}Checking dependencies...${C_RESET}"
    install_deps
    if [ "$NO_UPDATE" = "1" ]; then
      say "  ${C_DIM}[-]  Updates are off (LGTV_EASY_NO_UPDATE=1); using the copy on disk.${C_RESET}"
    else
      sync_repo || true
      report_update
      # New code arrived, so any daemon still running is by definition stale -
      # and it may be one we did not start (the login autostart), holding the
      # single-instance lock. Retire it here, before our own child queues up
      # behind it forever. Without this the update lands on disk and never runs.
      if sync_changed; then
        retire_stale_daemon
      fi
      maybe_self_update "$@"
    fi
    say "  ${C_DIM}App folder : $APP_HOME${C_RESET}"
    say "  ${C_DIM}Log file   : $LOG_FILE${C_RESET}"
    say "${C_ACC}${RULE}${C_RESET}"
    say ""
  fi

  case "${1:-}" in
    --setup)
      log "Opening the setup window (forced)."
      if ! run_cli gui; then
        pause_before_exit
        exit 1
      fi
      exit 0
      ;;
    --background)
      if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        log "Already running in background (pid $(cat "$PID_FILE"))."
        exit 0
      fi
      if needs_setup; then
        log "First run: opening the setup window before backgrounding."
        run_cli gui
        local rc=$?
        if [ "$rc" = "$EXIT_SERVICE_STOPPED" ]; then
          say "  ${C_WARN}[!]${C_RESET}  Easy Mode was stopped from the window."
          say "       ${C_DIM}Restart the app to resume service.${C_RESET}"
          exit 0
        fi
        if needs_setup; then
          log "Setup not completed; not backgrounding."
          pause_before_exit
          exit 1
        fi
      fi
      log "Detaching to background. Log: $LOG_FILE"
      setsid "$0" --supervise </dev/null >>"$LOG_FILE" 2>&1 &
      exit 0
      ;;
    --supervise)
      supervise "$@"
      ;;
    *)
      # A manual run is a control panel: open the graphical window (setup wizard
      # on first run, settings panel afterwards; text wizard if there's no
      # display), then run the watcher in the foreground.
      log "Opening the control panel window."
      run_cli gui
      local rc=$?
      if [ "$rc" = "$EXIT_SERVICE_STOPPED" ]; then
        say "  ${C_WARN}[!]${C_RESET}  Easy Mode was stopped from the window."
        say "       ${C_DIM}Restart the app to resume service.${C_RESET}"
        exit 0
      fi
      if [ "$rc" != "0" ] || needs_setup; then
        log "Setup not completed."
        pause_before_exit
        exit 1
      fi
      supervise "$@"
      ;;
  esac
}

main "$@"

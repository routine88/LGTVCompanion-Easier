"""Command-line interface for Easy Mode.

Everything the GUI can do is also available here, which makes the app scriptable,
testable, and usable on headless machines. Subcommands:

    scan      discover LG TVs on the network
    find      re-locate the saved TV by MAC and fix its IP (after a DHCP change)
    pair      pair with a TV (by IP) and save it
    set       change settings, e.g. the idle timeout in minutes
    status    show current configuration and idle backend
    test      verify the saved TV by blinking the screen off then on
    repair    self-test the connection and auto-fix it (doctor)
    run       run the idle-monitoring daemon in the foreground
    gui       open the graphical control panel (text wizard fallback)
    wizard    run the interactive text setup wizard
"""
from __future__ import annotations

import argparse
import sys
import time

from . import __version__
from .config import Config, Device, config_path, fmt_timeout, log_path
from .daemon import Daemon
from . import idle as idle_mod
from .webos import WebOSClient


def _print(msg: str = "") -> None:
    print(msg, flush=True)


# ----- "no TV configured" warning ------------------------------------------
# The failure this exists to prevent: Easy Mode sitting there running, doing
# absolutely nothing, because there is no TV to drive - and never saying so. It
# is the one state where every command should shout rather than mention.

def _colour_ok() -> bool:
    """True if it's safe to emit ANSI colour on this stdout.

    Honours NO_COLOR, skips redirected output, and switches the legacy Windows
    console into VT mode (Windows Terminal already is; conhost is not, and would
    otherwise print the escape codes literally).
    """
    import os
    if os.environ.get("NO_COLOR"):
        return False
    stream = sys.stdout
    if stream is None or not hasattr(stream, "isatty") or not stream.isatty():
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        # 0x0004 = ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001 - no console, or an old Windows build
        return False


def _red(text: str) -> str:
    return f"\033[1;31m{text}\033[0m" if _colour_ok() else text


# Kept ASCII on purpose: this banner has to survive a legacy cp437/cp1252
# console, which is exactly where a confused user ends up reading it.
def warn_no_tv(cfg, action: str = "") -> None:
    """Print the loud red 'there is no TV' banner. ``action`` names what is
    being skipped because of it, e.g. 'The watcher will not start'."""
    reason = cfg.unconfigured_reason()
    if reason is None:
        return
    rule = "=" * 68
    _print(_red(rule))
    _print(_red("  NO TV IS SET UP  -  Easy Mode has nothing to control."))
    _print(_red(f"  {reason}"))
    if action:
        _print(_red(f"  {action}"))
    _print(_red("  Set one up with:  lgtv-easy gui        (guided setup)"))
    _print(_red("                    lgtv-easy pair <ip>  (if you know the IP)"))
    _print(_red(rule))


def _has_console() -> bool:
    """False when nobody can read what we print.

    Both auto-start paths land here, for different reasons:

    * Windows: the Startup entry runs ``pythonw`` so no console window flashes,
      which leaves ``sys.stdout`` as None outright.
    * Linux: the ``.desktop`` entry sets ``Terminal=false``, so stdout is a real
      file descriptor - pointing at the journal or ~/.xsession-errors, where no
      user will ever see it. Testing for None alone would wrongly conclude
      somebody is watching.

    So the question is whether stdout is a terminal, not whether it exists.
    """
    stream = sys.stdout
    if stream is None:
        return False
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001 - a detached or dummy stream
        return False


def _has_display() -> bool:
    """True if there is a desktop session that could show a window at all."""
    import os
    if os.name == "nt":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _alert_no_tv(reason: str) -> None:
    """Best-effort on-screen 'no TV is set up' window. Never raises.

    Skipped when LGTV_EASY_NO_ALERT=1 (tests, headless CI) or when there is no
    display / no tkinter - a missing warning must never take the process down.
    """
    import os
    if os.environ.get("LGTV_EASY_NO_ALERT") == "1":
        return
    if not _has_display():
        return
    try:
        from .gui import show_no_tv_alert
        show_no_tv_alert(reason)
    except Exception:  # noqa: BLE001 - no display, no tk, no window manager
        pass


def cmd_scan(args) -> int:
    from .discovery import discover_tvs
    _print("Scanning the network for LG TVs (a few seconds)...")
    found = discover_tvs(timeout=args.timeout, log=_print)
    if not found:
        _print("No TVs found. You can still add one by IP with: lgtv-easy pair <ip>")
        return 1
    for i, dev in enumerate(found, 1):
        _print(f"  {i}. {dev.name}  @ {dev.ip}")
    return 0


def cmd_find(args) -> int:
    """Locate the saved TV by its MAC and update the stored IP if it moved.

    The everyday failure this fixes: the router hands the TV a new DHCP address,
    the saved IP goes dead, and the daemon can no longer reach it. The MAC never
    changes, so we search the LAN for it and rewrite the IP.
    """
    cfg = Config.load()
    if not cfg.device.mac:
        _print("No TV hardware address (MAC) is saved yet, so I can't search by it.")
        _print("Pair the TV, or run 'lgtv-easy test' once while it's reachable, to")
        _print("learn and store the MAC - then 'find' can track it across IP changes.")
        return 1
    from .discovery import locate_by_mac
    _print(f"Looking for '{cfg.device.name}' by MAC {cfg.device.mac} ...")
    ip = locate_by_mac(cfg.device.mac, log=_print)
    if not ip:
        _print("Could not find the TV. Make sure it's powered on and on this network.")
        return 1
    host = ip.rpartition(":")[0] if ":" in ip else ip
    if host == cfg.device.ip:
        _print(f"TV is still at {host} - nothing to update.")
        return 0
    old = cfg.device.ip or "(unset)"
    cfg.device.ip = host
    cfg.save()
    _print(f"Updated the TV's address {old} -> {host}.")
    return 0


def cmd_pair(args) -> int:
    cfg = Config.load()
    client = WebOSClient(args.ip, secure=args.secure)
    _print(f"Connecting to {args.ip} ...")

    def on_prompt():
        _print(">> Look at your TV and press OK / Accept on the pairing prompt.")

    from .webos import pair_with_fallback
    try:
        key = pair_with_fallback(
            client,
            client_key=cfg.device.key if cfg.device.ip == args.ip else "",
            on_prompt=on_prompt, prompt_timeout=args.timeout, log=_print)
    except Exception as exc:  # noqa: BLE001
        _print(f"Pairing failed: {exc}")
        from .netdiag import probe_tv
        _print("--- Connection diagnostics ---")
        probe_tv(args.ip, _print)
        return 1
    finally:
        client.close()
    mac = args.mac or cfg.device.mac
    if not mac:
        from .netdiag import mac_for_ip
        mac = mac_for_ip(args.ip)
        if mac:
            _print(f"Detected TV hardware address {mac} for Wake-on-LAN.")
    cfg.device = Device(name=args.name or cfg.device.name or "My LG TV",
                        ip=args.ip, mac=mac, key=key, secure=client.secure)
    cfg.setup_complete = True
    cfg.save()
    _print(f"Paired! Saved TV '{cfg.device.name}' at {args.ip}.")
    return 0


def cmd_set(args) -> int:
    cfg = Config.load()
    changed = []
    if args.minutes is not None:
        cfg.idle_minutes = args.minutes
        changed.append(f"timeout={args.minutes} min")
    if args.enabled is not None:
        cfg.idle_enabled = args.enabled
        changed.append(f"enabled={args.enabled}")
    if args.mute is not None:
        cfg.mute_on_sleep = args.mute
        changed.append(f"mute_on_sleep={args.mute}")
    if args.deep_off is not None:
        cfg.deep_off_enabled = args.deep_off
        changed.append(f"deep_off={args.deep_off}")
    if args.deep_off_minutes is not None:
        cfg.deep_off_minutes = args.deep_off_minutes
        changed.append(f"deep_off_minutes={args.deep_off_minutes}")
    if args.off_on_shutdown is not None:
        cfg.tv_off_on_shutdown = args.off_on_shutdown
        changed.append(f"off_on_shutdown={args.off_on_shutdown}")
    if args.sleep_with_pc is not None:
        cfg.screen_off_on_pc_sleep = args.sleep_with_pc
        changed.append(f"sleep_with_pc={args.sleep_with_pc}")
    if args.mac is not None:
        cfg.device.mac = args.mac
        changed.append(f"mac={args.mac}")
    if args.only_my_input is not None:
        cfg.only_my_input = args.only_my_input
        changed.append(f"only_my_input={args.only_my_input}")
    if args.input is not None:
        # 'auto' clears the pin and lets the daemon learn it from the TV again.
        value = "" if args.input.strip().lower() in ("auto", "") else \
            args.input.strip().lower()
        cfg.device.input_id = value
        changed.append(f"input={value or 'auto'}")
    cfg.save()
    _print("Updated: " + (", ".join(changed) if changed else "(nothing)"))
    if changed and _signal_running_daemon():
        _print("Applied live to the running daemon (no restart needed).")
    return 0


def _signal_running_daemon() -> bool:
    """Nudge a background daemon, if one is running, to re-read the config we
    just wrote so the change takes effect without a restart. POSIX only; a
    harmless no-op on Windows or when no daemon is running."""
    import signal
    sig = getattr(signal, "SIGHUP", None)
    if sig is None:
        return False
    from .singleton import SingleInstance
    return SingleInstance("daemon").signal(sig)


def cmd_status(args) -> int:
    cfg = Config.load()
    _print(f"LGTV Companion Easy Mode {__version__}")
    # Lead with it: "no TV" makes every line below meaningless, so it must not
    # be one more row in the list the way it used to be.
    warn_no_tv(cfg, action="Nothing below will have any effect until one is.")
    _print(f"  Config file : {config_path()}")
    _print(f"  Log file    : {log_path()}")
    _print(f"  Setup done  : {cfg.setup_complete}")
    _print(f"  TV          : {cfg.device.name} @ {cfg.device.ip or '(none)'}"
           f"  paired={cfg.device.paired}  "
           f"port={'3001/wss' if cfg.device.secure else '3000/ws'}")
    _print(f"  Idle sleep  : {'ON' if cfg.idle_enabled else 'OFF'} after "
           f"{fmt_timeout(cfg.idle_seconds)}  (mute={cfg.mute_on_sleep})")
    if cfg.deep_off_enabled:
        _print(f"  Deep off    : ON after {fmt_timeout(cfg.deep_off_seconds)} "
               f"(full power-off, WOL mac={cfg.device.mac or '(none!)'})")
    else:
        _print("  Deep off    : OFF (screen blanks only; TV stays powered)")
    _print(f"  Off on quit : {'ON' if cfg.tv_off_on_shutdown else 'OFF'} "
           "(power the TV off when the PC shuts down)")
    _print(f"  PC sleep    : {'ON' if cfg.screen_off_on_pc_sleep else 'OFF'} "
           "(screen follows the PC into sleep and back)")
    if cfg.only_my_input:
        where = cfg.device.input_id or "(learning - not seen yet)"
        _print(f"  This PC's in: {where}  (the TV is left alone whenever it's "
               "showing another source)")
    else:
        _print("  This PC's in: (not checked - Easy Mode acts whatever the TV "
               "is showing)")
    _print(f"  Idle backend: {idle_mod.idle_backend_name()} "
           f"(real={idle_mod.is_real_backend()})")
    if not idle_mod.is_real_backend():
        _print("      ^ no OS idle source here (e.g. a non-GNOME Wayland "
               "session); the screen won't auto-blank. An Xorg login session works.")
    _print(f"  Current idle: {idle_mod.get_idle_seconds():.0f}s")
    from . import autostart
    _print(f"  Auto-start  : {autostart.status()}")
    from .singleton import SingleInstance
    holder = SingleInstance("daemon").holder()
    _print(f"  Watcher     : {'RUNNING (pid ' + str(holder) + ')' if holder else 'NOT running'}")
    return 0


def cmd_test(args) -> int:
    cfg = Config.load()
    if not cfg.device.ip and not cfg.device.mac:
        warn_no_tv(cfg, action="There is nothing to test.")
        return 1
    from .recovery import connect_tv
    try:
        client = connect_tv(cfg, prompt_timeout=10.0, log=_print)
    except Exception as exc:  # noqa: BLE001
        # The quick path failed; escalate to the full self-test and repair, which
        # probes the network, relocates the TV, reconnects and (on success) blinks
        # the screen itself - so a moved/renamed TV heals without user action.
        _print(f"Could not reach the TV at the saved address: {exc}")
        _print("--- Running self-test and repair ---")
        from . import selfheal
        res = selfheal.repair(cfg, log=_print, connect=True, blink=True,
                              prompt_timeout=10.0)
        if res.client is not None:
            res.client.close()
        _print("")
        _print(res.summary)
        if not res.ok:
            _print_bug_footer()
        return 0 if res.ok else 1
    try:
        _print(f"Connected to {cfg.device.name} at {cfg.device.ip}.")
        # Report which source the TV says it's showing. This is what stops a
        # second computer on the same TV from blanking the one you're watching,
        # so it's worth confirming the panel actually answers.
        showing = ""
        try:
            showing = client.get_foreground_input()
        except Exception as exc:  # noqa: BLE001
            _print(f"(Could not ask the TV what it's showing: {exc})")
        if showing:
            _print(f"TV is currently showing: {showing}")
        else:
            _print("This TV won't say which input it's showing, so Easy Mode "
                   "can't tell when another computer is on screen.")
        _print("Turning screen OFF for 3 seconds...")
        client.screen_off()
        time.sleep(3)
        _print("Turning screen ON...")
        client.screen_on()
        # While connected, learn (and save) the TV's MAC for Wake-on-LAN.
        # Newer panels block the WebSocket info APIs, so fall back to the ARP
        # table (the host is in it now, since we just connected).
        host = cfg.device.ip.rpartition(":")[0] if ":" in cfg.device.ip else cfg.device.ip
        mac = client.get_mac()
        if not mac:
            from .netdiag import mac_for_ip
            mac = mac_for_ip(host)
        if mac:
            if mac != cfg.device.mac:
                cfg.device.mac = mac
                cfg.save()
            _print(f"TV MAC for Wake-on-LAN: {mac}  (saved)")
        else:
            from .netdiag import arp_dump
            _print("Could not auto-detect the TV's MAC (this panel blocks the")
            _print("WebSocket info APIs). You can set it by hand with:")
            _print("  lgtv-easy set --mac <the TV's Wi-Fi MAC>")
            _print(f"  ARP says: {arp_dump(host)}")
    except Exception as exc:  # noqa: BLE001
        _print(f"Test failed: {exc}")
        return 1
    finally:
        client.close()
    _print("Test OK - your TV responds to Easy Mode.")
    return 0


def _print_bug_footer() -> None:
    """Print an environment fingerprint + log path for a bug report."""
    from .netdiag import env_summary
    _print("")
    _print("If it still won't connect, copy these details into a bug report:")
    for line in env_summary():
        _print("  " + line)
    _print(f"  Log file    : {log_path()}")


def cmd_repair(args) -> int:
    """Self-test the TV connection and automatically repair it.

    The `test` command verifies a working TV; this one is aimed at a broken one:
    it narrates the full diagnosis (which network the PC is on, whether the TV's
    ports answer), relocates the TV by MAC or discovery, reconnects, and persists
    the corrected address - then says, in plain language, what it found and fixed.
    """
    cfg = Config.load()
    if not cfg.device.ip and not cfg.device.mac:
        warn_no_tv(cfg, action="There is nothing to repair.")
        return 1
    from . import selfheal
    _print("Running a connection self-test and repair...")
    _print("")
    res = selfheal.repair(cfg, log=_print, connect=False, blink=True,
                          prompt_timeout=10.0)
    _print("")
    _print(res.summary)
    if res.repaired and _signal_running_daemon():
        _print("Applied the corrected address to the running daemon (no restart).")
    if not res.ok:
        _print_bug_footer()
    return 0 if res.ok else 1


def _tv_shows_this_pc(cfg, client) -> bool:
    """Whether the TV is currently displaying this PC (see Daemon._may_darken).

    Fails open whenever the answer isn't known, so a single-PC setup is
    unaffected.
    """
    if not cfg.only_my_input or not cfg.device.input_id:
        return True
    try:
        seen = client.get_foreground_input()
    except Exception:  # noqa: BLE001 - unknown is a valid answer
        return True
    return not seen or seen == cfg.device.input_id


def _tv_power_off(cfg, log=lambda m: None, timeout: float = 8.0,
                  recover: bool = False, guard: bool = False) -> str:
    """Connect and fully power the TV off (used by `off` and shutdown hooks).

    ``recover`` re-locates the TV by MAC if the saved IP is stale; it's left off
    for the shutdown hook, which must stay fast while the PC is powering down.
    ``guard`` leaves the TV alone when it is showing another computer - set on
    the shutdown paths, where this PC going down says nothing about the machine
    currently on screen, but not when the user explicitly asked for `off`.

    Returns 'off', 'skipped' (another source is on screen) or 'failed'.
    """
    from .recovery import connect_tv
    try:
        client = connect_tv(cfg, prompt_timeout=timeout, timeout=timeout,
                            recover=recover, log=log)
    except Exception as exc:  # noqa: BLE001
        log(f"power off failed: {exc}")
        return "failed"
    try:
        if guard and not _tv_shows_this_pc(cfg, client):
            log("Leaving the TV on: it is showing another source, not this PC.")
            return "skipped"
        client.power_off()
        return "off"
    except Exception as exc:  # noqa: BLE001
        log(f"power off failed: {exc}")
        return "failed"
    finally:
        client.close()


def cmd_off(args) -> int:
    cfg = Config.load()
    # The shutdown hook calls us with --only-if-configured so it honours the
    # "power off when the PC shuts down" setting.
    if getattr(args, "only_if_configured", False) and not cfg.tv_off_on_shutdown:
        return 0
    if not cfg.device.ip and not cfg.device.mac:
        _print("No TV configured.")
        return 1
    # A user typing `lgtv-easy off` means it; only the shutdown hook
    # (--only-if-configured) defers to whatever is on screen.
    result = _tv_power_off(cfg, log=_print, recover=True,
                           guard=getattr(args, "only_if_configured", False))
    if result == "skipped":
        return 0
    _print("TV powered off." if result == "off" else "Could not power off the TV.")
    return 0 if result == "off" else 1


def cmd_on(args) -> int:
    cfg = Config.load()
    if not cfg.device.ip and not cfg.device.mac:
        _print("No TV configured.")
        return 1
    if cfg.device.mac:
        from .wol import send_wol, wake_targets
        try:
            # A sustained burst (not one blip): a TV asleep on Wi-Fi behind a mesh
            # drops a single magic packet but wakes from packets spread over a few
            # seconds.
            send_wol(cfg.device.mac, broadcast=wake_targets(cfg.device.ip),
                     repeat=20, interval=0.25)
            _print(f"Sent Wake-on-LAN to {cfg.device.mac}.")
        except Exception as exc:  # noqa: BLE001
            _print(f"WOL failed: {exc}")
    # Give the panel a moment to come up, then make sure the screen is on -
    # relocating the TV by MAC if DHCP moved it while it was off.
    from .recovery import connect_tv
    try:
        client = connect_tv(cfg, log=_print)
    except Exception as exc:  # noqa: BLE001
        _print(f"(Could not confirm screen-on, but WOL was sent: {exc})")
        return 0
    try:
        client.screen_on()
        _print(f"TV is on at {cfg.device.ip}.")
    finally:
        client.close()
    return 0


def cmd_run(args) -> int:
    cfg = Config.load()
    reason = cfg.unconfigured_reason()
    if reason is not None:
        # This is the path that used to fail invisibly. At login the watcher is
        # started by pythonw from the Startup folder, with no console attached:
        # the old _print went nowhere, the process exited 1, and the user was
        # left with an app that had silently never done anything. So record it
        # in the log file, and when there is no console to print to, say it on
        # screen instead.
        warn_no_tv(cfg, action="The idle watcher will NOT start.")
        from .applog import get_logger
        get_logger().warning(
            "Watcher not started: %s Set a TV up with 'lgtv-easy gui'.", reason)
        if not _has_console():
            _alert_no_tv(reason)
        return 1
    # Only one daemon should drive the TV. A supervised child (LGTV_EASY_WAIT_LOCK)
    # waits its turn; a manual run exits politely if one is already going.
    import os
    from .singleton import SingleInstance
    lock = SingleInstance("daemon")
    wait = os.environ.get("LGTV_EASY_WAIT_LOCK") == "1"
    if not lock.acquire(wait=wait):
        _print("Another Easy Mode watcher is already running; nothing to do.")
        return 0
    # Show the daemon's activity live in this window (screen off/on, errors).
    from .applog import get_logger
    logger = get_logger(to_console=True)
    daemon = Daemon(cfg, logger=logger)
    _install_shutdown_hooks(cfg, daemon, logger)
    _print(f"Idle daemon running. Screen sleeps after {fmt_timeout(cfg.idle_seconds)}. "
           "Press Ctrl+C to stop.")
    try:
        daemon.run()  # blocks
    except KeyboardInterrupt:
        daemon.stop()
        _print("\nStopped.")
    finally:
        lock.release()
    return 0


def _install_shutdown_hooks(cfg, daemon, logger) -> None:
    """Power the TV off when the OS is shutting down / logging off.

    Carefully distinguishes a real session end (power off the TV) from a routine
    restart by the supervisor or a plain Ctrl+C (just stop, leave the TV alone):

    * Linux: SIGTERM = session end -> off; SIGUSR1 = supervisor restart -> no off.
    * Windows: console CTRL_SHUTDOWN/CTRL_LOGOFF -> off (works when a console is
      attached; the pythonw auto-start uses the Scheduled Task hook instead).
    """
    import os
    import signal

    state = {"terminating": False}

    def power_off():
        # If the logind PrepareForShutdown handler already powered the TV off
        # (while the network was still up), don't fire a second, redundant
        # power-off that would just time out against an off TV and stall exit.
        if getattr(daemon, "_shutdown_handled", False):
            return
        if cfg.tv_off_on_shutdown:
            logger.info("Shutting down: powering the TV off.")
            _tv_power_off(cfg, log=logger.info, timeout=5.0, guard=True)

    def on_term(_signum=None, _frame=None):
        if state["terminating"]:  # a second SIGTERM arrived mid-power-off
            return
        state["terminating"] = True
        power_off()
        daemon.stop()
        raise SystemExit(0)

    def on_restart(_signum=None, _frame=None):
        daemon.stop()  # supervisor is restarting us; don't touch the TV
        raise SystemExit(0)

    def on_reload(_signum=None, _frame=None):
        # The GUI/CLI saved a setting and is asking us to apply it live, without
        # a restart and without touching the TV. Just flag the loop to re-read.
        daemon.request_reload()

    try:
        signal.signal(signal.SIGTERM, on_term)
    except (ValueError, OSError, AttributeError):
        pass
    if hasattr(signal, "SIGUSR1"):
        try:
            signal.signal(signal.SIGUSR1, on_restart)
        except (ValueError, OSError):
            pass
    if hasattr(signal, "SIGHUP"):
        try:
            signal.signal(signal.SIGHUP, on_reload)
        except (ValueError, OSError):
            pass

    if os.name == "nt":
        try:
            import ctypes
            handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

            def _ctrl(ctrl_type):  # 5=CTRL_LOGOFF, 6=CTRL_SHUTDOWN
                if ctrl_type in (5, 6):
                    power_off()
                    daemon.stop()
                return 0

            cb = handler_type(_ctrl)
            _install_shutdown_hooks._cb = cb  # keep a ref so it isn't GC'd
            ctypes.windll.kernel32.SetConsoleCtrlHandler(cb, True)
        except Exception:  # noqa: BLE001 - no console / not supported
            pass


def cmd_autostart(args) -> int:
    from . import autostart
    action = getattr(args, "action", None) or "status"
    if action == "enable":
        try:
            path = autostart.enable(method=getattr(args, "method", "") or "")
        except Exception as exc:  # noqa: BLE001
            _print(f"Could not enable auto-start: {exc}")
            return 1
        _print(f"Auto-start at login ENABLED via {path}")
        # Arming auto-start with no TV saved is how you end up with an app that
        # launches every login and silently does nothing at all.
        warn_no_tv(Config.load(),
                   action="At login the watcher will start and exit immediately.")
    elif action == "disable":
        autostart.disable()
        _print("Auto-start at login DISABLED.")
    else:
        _print(f"Auto-start at login: {autostart.status()}")
    return 0


def cmd_gui(args) -> int:
    """Open the graphical control panel (the everyday front door).

    Tries the tkinter window first - the setup wizard on first run, the
    settings panel afterwards - and quietly falls back to the text wizard when
    there is no display or tkinter isn't available (a headless server, an SSH
    session, a stripped-down Python). This is what the launchers invoke, so the
    friendly window is what people normally see.
    """
    try:
        from .gui import main as gui_main
        return gui_main()
    except Exception as exc:  # noqa: BLE001 - no display / no tk / import error
        _print(f"(Graphical window unavailable: {exc})")
        _print("Starting the text wizard instead.\n")
        return cmd_wizard(args)


def cmd_wizard(args) -> int:
    from .wizard_text import run_text_wizard
    rc = run_text_wizard()
    if rc != 0:
        from .netdiag import env_summary
        _print("")
        _print("=" * 60)
        _print("  Setup did not finish. If you need help, copy everything")
        _print("  above plus these details into a bug report:")
        _print("=" * 60)
        for line in env_summary():
            _print("  " + line)
        _print(f"  Log file    : {log_path()}")
        _print("=" * 60)
    return rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lgtv-easy",
        description="LGTV Companion Easy Mode - sleep your LG TV when idle.")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("scan", help="find LG TVs on the network")
    s.add_argument("--timeout", type=float, default=3.0)
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("find", help="re-locate the saved TV by MAC and fix its IP")
    s.set_defaults(func=cmd_find)

    s = sub.add_parser("pair", help="pair with a TV by IP and save it")
    s.add_argument("ip")
    s.add_argument("--name", default="")
    s.add_argument("--mac", default="")
    s.add_argument("--secure", action="store_true")
    s.add_argument("--timeout", type=float, default=60.0)
    s.set_defaults(func=cmd_pair)

    s = sub.add_parser("set", help="change settings")
    s.add_argument("--minutes", type=float, help="screen-off timeout in minutes")
    s.add_argument("--enabled", type=_boolish, help="true/false")
    s.add_argument("--mute", type=_boolish, help="mute speakers on sleep")
    s.add_argument("--deep-off", dest="deep_off", type=_boolish,
                   help="fully power the TV off after a longer idle (true/false)")
    s.add_argument("--deep-off-minutes", dest="deep_off_minutes", type=float,
                   help="total idle minutes before fully powering off")
    s.add_argument("--off-on-shutdown", dest="off_on_shutdown", type=_boolish,
                   help="power the TV off when the PC shuts down (true/false)")
    s.add_argument("--sleep-with-pc", dest="sleep_with_pc", type=_boolish,
                   help="screen off when the PC sleeps, back on at resume (true/false)")
    s.add_argument("--mac", help="set Wake-on-LAN MAC address")
    s.add_argument("--only-my-input", dest="only_my_input", type=_boolish,
                   help="only touch the TV while it is showing this PC, so a "
                        "second computer on the same TV can't blank it (true/false)")
    s.add_argument("--input", help="which TV input this PC is on, e.g. hdmi2. "
                                   "Use 'auto' to let it be learned from the TV.")
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("status", help="show current settings")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("test", help="blink the screen to verify the TV")
    s.set_defaults(func=cmd_test)

    s = sub.add_parser("repair", aliases=["doctor"],
                       help="self-test the connection and auto-fix it")
    s.set_defaults(func=cmd_repair)

    s = sub.add_parser("run", help="run the idle-monitoring daemon")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("off", help="power the TV off now")
    s.add_argument("--only-if-configured", dest="only_if_configured",
                   action="store_true", help=argparse.SUPPRESS)
    s.set_defaults(func=cmd_off)

    s = sub.add_parser("on", help="turn the TV on (Wake-on-LAN + screen on)")
    s.set_defaults(func=cmd_on)

    s = sub.add_parser("gui", help="open the graphical control panel "
                                   "(falls back to the text wizard)")
    s.set_defaults(func=cmd_gui)

    s = sub.add_parser("wizard", help="interactive text setup wizard")
    s.set_defaults(func=cmd_wizard)

    s = sub.add_parser("autostart", help="start automatically at login")
    s.add_argument("action", nargs="?", choices=["enable", "disable", "status"],
                   default="status")
    s.add_argument("--method", choices=["startup", "task", "desktop"], default="",
                   help="Windows: 'startup' folder (default) or 'task' "
                        "(Task Scheduler, for locked-down Startup folders)")
    s.set_defaults(func=cmd_autostart)
    return p


def _boolish(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # No subcommand: open the graphical control panel (text wizard fallback).
        return cmd_gui(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

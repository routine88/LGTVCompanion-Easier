"""The idle-monitoring daemon: the heart of Easy Mode.

Every ``poll_seconds`` it asks the OS how long the user has been idle. Cross the
configured threshold and the TV screen is blanked; touch the keyboard or mouse
and it comes straight back on. That is the entire job.

The loop is written with injectable dependencies (idle source, client factory,
clock, stop event) so the whole behaviour can be stepped deterministically in
tests without a real TV or a real wait.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

from . import idle as idle_mod
from .applog import get_logger
from .config import Config
from .webos import WebOSClient
from .wol import send_wol

# Screen state as tracked by the daemon.
STATE_ON = "on"
STATE_OFF = "off"          # panel blanked, TV still powered and on the network
STATE_STANDBY = "standby"  # TV fully powered off (deep energy saving)

# If a single poll iteration takes far longer in wall-clock time than we asked it
# to sleep, the process was frozen - i.e. the machine suspended and has just
# resumed. This is the universal (no-OS-API) resume detector.
RESUME_GAP_SECONDS = 30.0


class Daemon:
    def __init__(
        self,
        config: Config,
        client_factory: Optional[Callable[[], WebOSClient]] = None,
        idle_fn: Optional[Callable[[], float]] = None,
        sleep_fn: Optional[Callable[[float], None]] = None,
        clock_fn: Optional[Callable[[], float]] = None,
        logger=None,
    ):
        self.config = config
        self.logger = logger or get_logger()
        self._idle_fn = idle_fn or idle_mod.get_idle_seconds
        self._sleep_fn = sleep_fn or time.sleep
        self._clock = clock_fn or time.monotonic
        self._client_factory = client_factory or self._default_client_factory
        self._client: Optional[WebOSClient] = None
        self.screen_state = STATE_ON  # assume the screen is on at startup
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # The OS power hook and the tick loop both drive the TV from different
        # threads; this serialises access to the single WebOS socket.
        self._lock = threading.RLock()
        # Set by the OS resume hook; consumed by the run loop on its own thread.
        self._resume_event = threading.Event()
        # Counters make tests and the status command observable.
        self.sleeps = 0
        self.wakes = 0
        self.deep_offs = 0
        self.suspends = 0
        self.resumes = 0
        self.last_error = ""
        self._warned_no_wol = False

    # ----- TV connection ----------------------------------------------
    def _default_client_factory(self) -> WebOSClient:
        return WebOSClient(self.config.device.ip, secure=self.config.device.secure)

    def _ensure_client(self) -> Optional[WebOSClient]:
        if self._client and self._client.connected:
            return self._client
        try:
            client = self._client_factory()
            # Try the port the TV actually accepts (3000 vs secure 3001),
            # preferring whichever worked before. Newer panels only allow 3001.
            from .webos import pair_with_fallback
            pair_with_fallback(client, client_key=self.config.device.key,
                               on_prompt=None, prompt_timeout=client.timeout,
                               prefer_secure=self.config.device.secure)
            # Remember (and persist) what we learned about the TV: the port that
            # worked, and its MAC (asked straight from the TV) for Wake-on-LAN.
            changed = False
            if client.secure != self.config.device.secure:
                self.config.device.secure = client.secure
                changed = True
            if not self.config.device.mac:
                mac = client.get_mac()
                if not mac:
                    from .netdiag import mac_for_ip
                    host = (client.ip.rpartition(":")[0]
                            if ":" in client.ip else client.ip)
                    mac = mac_for_ip(host)
                if mac:
                    self.config.device.mac = mac
                    changed = True
                    self.logger.info("Detected TV MAC for Wake-on-LAN: %s", mac)
            if changed:
                try:
                    self.config.save()
                except Exception:  # noqa: BLE001 - persistence is best-effort
                    pass
            self._client = client
            return client
        except Exception as exc:  # noqa: BLE001 - network errors are expected
            self.last_error = f"connect: {exc}"
            self.logger.warning("Could not connect to TV: %s", exc)
            self._client = None
            return None

    def _drop_client(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
        self._client = None

    # ----- actions -----------------------------------------------------
    def sleep_screen(self) -> bool:
        with self._lock:
            client = self._ensure_client()
            if not client:
                return False
            try:
                client.screen_off()
                if self.config.mute_on_sleep:
                    client.set_mute(True)
                self.screen_state = STATE_OFF
                self.sleeps += 1
                self.logger.info("Screen off after %.0f min idle",
                                 self.config.idle_minutes)
                return True
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"sleep: {exc}"
                self.logger.warning("Failed to turn screen off: %s", exc)
                self._drop_client()
                return False

    def power_off_tv(self) -> bool:
        """Fully power the TV off (deep standby) for maximum energy saving."""
        with self._lock:
            client = self._ensure_client()
            if not client:
                return False
            try:
                client.power_off()
                self.screen_state = STATE_STANDBY
                self.deep_offs += 1
                self.logger.info("TV powered off (deep energy saving) after %.0f min idle",
                                 self.config.deep_off_minutes)
                # The socket dies as the TV powers down; reconnect on the next wake.
                self._drop_client()
                return True
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"power_off: {exc}"
                self.logger.warning("Failed to power off TV: %s", exc)
                self._drop_client()
                return False

    def wake_screen(self) -> bool:
        with self._lock:
            # If the panel went into standby it may need a magic packet first. Aim
            # it at both the limited broadcast and the TV's directed subnet
            # broadcast so it wakes reliably across a Google/Nest Wifi mesh (where
            # the limited broadcast isn't always forwarded between wired and
            # wireless segments).
            if self.config.device.mac:
                try:
                    from .wol import broadcast_targets
                    send_wol(self.config.device.mac,
                             broadcast=broadcast_targets(self.config.device.ip))
                except Exception as exc:  # noqa: BLE001
                    self.logger.debug("WOL send failed (often harmless): %s", exc)
            client = self._ensure_client()
            if not client:
                return False
            try:
                client.screen_on()
                if self.config.mute_on_sleep:
                    client.set_mute(False)
                self.screen_state = STATE_ON
                self.wakes += 1
                self.logger.info("Screen on (activity detected)")
                return True
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"wake: {exc}"
                self.logger.warning("Failed to turn screen on: %s", exc)
                self._drop_client()
                return False

    # ----- OS suspend / resume ----------------------------------------
    def on_suspend(self) -> None:
        """Mirror the PC going to sleep onto the TV, like a monitor losing signal.

        Driven by the OS power-event hook (the fast path) so the panel drops to
        standby the instant the machine suspends instead of waiting out the idle
        timer. Idempotent: if the screen is already off/standby there's nothing
        to do.
        """
        if not self.config.manage_suspend:
            return
        if self.screen_state != STATE_ON:
            return
        self.suspends += 1
        self.logger.info("PC suspending; putting the TV to sleep")
        # Mirror deep-off on suspend only if the user enabled it AND we can wake
        # the TV again (WOL needs the MAC); otherwise just blank the screen so a
        # resume lights it back up instantly.
        if self.config.deep_off_enabled and self.config.device.mac:
            self.power_off_tv()
        else:
            self.sleep_screen()

    def on_resume(self) -> None:
        """The PC woke; bring the TV straight back, like a monitor regaining signal.

        We deliberately do *not* trust ``screen_state`` here: while the daemon was
        frozen the TV may have hit its own standby timer, and the PC may have
        suspended before the idle timer ever blanked it (so our state still reads
        ON). Either way we re-assert screen-on, retrying because the network stack
        takes a few seconds to come back after resume.
        """
        if not self.config.manage_suspend:
            return
        self._resume_event.clear()
        self.resumes += 1
        self.logger.info("PC resumed; restoring the TV")
        self._resume_wake()

    def _resume_wake(self) -> bool:
        # The socket from before the suspend is dead; force a fresh connect.
        self._drop_client()
        attempts = 6
        for i in range(attempts):
            if self.wake_screen():
                return True
            if i < attempts - 1:
                self._sleep_fn(self.config.poll_seconds)
                self._drop_client()
        return False

    def notify_suspend(self) -> None:
        """Thread-safe entry for the OS power-event listener (suspend path).

        Runs on the listener's thread and must be quick - one screen-off - so the
        OS isn't held up; the action methods take ``self._lock`` internally.
        """
        try:
            self.on_suspend()
        except Exception as exc:  # noqa: BLE001 - never crash the listener
            self.last_error = f"suspend: {exc}"
            self.logger.warning("Suspend handler failed: %s", exc)

    def notify_resume(self) -> None:
        """Thread-safe entry for the OS power-event listener (resume path).

        Just flags the run loop, which performs the (slow, retrying) wake on its
        own thread, so the OS callback returns immediately.
        """
        self._resume_event.set()

    # ----- the loop ----------------------------------------------------
    def tick(self) -> None:
        """Evaluate idle state once and act. Safe to call from tests.

        Up to three stages, mirroring a real monitor that sleeps then lets the
        PC power down:
          ON       --(idle >= idle_minutes)-->        OFF (screen blanked)
          OFF      --(idle >= deep_off_minutes)-->     STANDBY (TV powered off)
          OFF/STANDBY --(activity)-->                  ON (wake, via WOL if off)
        """
        if not self.config.idle_enabled:
            # Disabled: make sure the screen isn't left off/standby by us.
            if self.screen_state in (STATE_OFF, STATE_STANDBY):
                self.wake_screen()
            return
        idle = self._idle_fn()
        threshold = self.config.idle_seconds
        # Deep power-off only makes sense strictly after the screen-off stage,
        # and only if we can wake the TV again (Wake-on-LAN needs its MAC) -
        # otherwise it would switch off and never come back on its own.
        deep = (self.config.deep_off_enabled
                and self.config.deep_off_seconds > threshold)
        if deep and not self.config.device.mac:
            deep = False
            if not self._warned_no_wol:
                self._warned_no_wol = True
                self.logger.warning(
                    "Deep power-off is on but no Wake-on-LAN MAC is set, so the "
                    "TV could not be woken again - skipping full power-off. Set "
                    "the MAC (lgtv-easy set --mac ..) or turn deep-off off.")
        if self.screen_state == STATE_ON and idle >= threshold:
            self.sleep_screen()
        elif (self.screen_state == STATE_OFF and deep
              and idle >= self.config.deep_off_seconds):
            self.power_off_tv()
        elif self.screen_state in (STATE_OFF, STATE_STANDBY) and idle < threshold:
            # Any input resets the OS idle timer, so this fires on wake.
            self.wake_screen()

    def run(self) -> None:
        self.logger.info(
            "Easy Mode daemon started (idle backend: %s, threshold: %.1f min, "
            "enabled: %s)",
            idle_mod.idle_backend_name(), self.config.idle_minutes,
            self.config.idle_enabled,
        )
        if not idle_mod.is_real_backend():
            self.logger.warning(
                "Idle detection is using the manual fallback; the OS-level "
                "input timer is unavailable in this environment."
            )
        # Connect once up front to learn and persist the TV's port and MAC,
        # even before the first idle event, so the config self-populates promptly.
        try:
            if self._ensure_client():
                self._drop_client()
        except Exception:  # noqa: BLE001 - best effort, never block startup
            pass
        # Subscribe to real OS suspend/resume events where we can (the fast path
        # that blanks the TV the moment the PC sleeps). Best-effort: the wall-clock
        # gap detector below covers resume even when no hook is available.
        stop_listener = self._start_power_listener()
        last = self._clock()
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                self.last_error = f"tick: {exc}"
                self.logger.exception("Unexpected error in daemon loop")
            self._sleep_fn(self.config.poll_seconds)
            # A suspend/resume cycle shows up two ways: the OS hook set the resume
            # event, or wall-clock time jumped far past our poll interval because
            # the process was frozen while the machine slept. Either way, restore.
            now = self._clock()
            gap = now - last
            last = now
            froze = gap > max(RESUME_GAP_SECONDS, self.config.poll_seconds * 3)
            if self._resume_event.is_set() or froze:
                if froze:
                    self.logger.info(
                        "Resume detected (frozen ~%.0fs); restoring TV", gap)
                self.on_resume()
                last = self._clock()  # don't count the wake's own time as a freeze
        if stop_listener:
            try:
                stop_listener()
            except Exception:  # noqa: BLE001
                pass
        self._drop_client()
        self.logger.info("Easy Mode daemon stopped")

    def _start_power_listener(self):
        """Start the OS suspend/resume listener; return a stop() callable or None."""
        if os.environ.get("LGTV_EASY_NO_POWER_HOOK"):
            return None
        try:
            from .power_events import start_power_listener
            stop = start_power_listener(self.notify_suspend, self.notify_resume,
                                        self.logger)
            if stop:
                self.logger.info("OS suspend/resume hook active")
            return stop
        except Exception as exc:  # noqa: BLE001 - hooks are a best-effort extra
            self.logger.debug("No OS power hook available: %s", exc)
            return None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, daemon=True,
                                        name="lgtv-easy-daemon")
        self._thread.start()

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=join_timeout)

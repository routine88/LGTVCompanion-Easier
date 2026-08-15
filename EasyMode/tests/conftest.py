import os
import sys
import tempfile

# Make the package importable when running tests from the repo without install.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Never let a daemon started during tests spawn the real OS suspend/resume
# monitors (gdbus + systemd-inhibit on Linux, the power-notify registration on
# Windows). The daemon checks this before starting its sleep watcher.
os.environ.setdefault("LGTV_EASY_NO_SLEEP_WATCH", "1")

# Likewise, keep the GUI's startup connection self-test from firing real network
# probes/discovery when a settings panel is built in a test; scenarios that want
# it exercise selfheal/the repair dialog explicitly.
os.environ.setdefault("LGTV_EASY_NO_SELFTEST", "1")

# Point the config directory at a throwaway home for the whole run. Several code
# paths persist what they learn (the TV's MAC, its address, which input this PC
# is on) as a side effect, and without this a test run would rewrite the real
# user's config - including that of the copy actually driving their TV. Tests
# that need their own directory still monkeypatch this per-test.
os.environ.setdefault("LGTV_EASY_HOME",
                      tempfile.mkdtemp(prefix="lgtv-easy-tests-"))

# Auto-start is the one thing Easy Mode writes that does NOT live under
# LGTV_EASY_HOME: on Windows it is a file in the real Startup folder (from
# %APPDATA%) plus a Scheduled Task, and on Linux a file under XDG_CONFIG_HOME.
# Several wizard tests answer "no" to "start when I log in", which means they
# were reaching past the temporary config directory and switching off the login
# entry of whoever ran the suite. Point that whole mechanism at a throwaway
# directory instead.
os.environ.setdefault("LGTV_EASY_AUTOSTART_SANDBOX",
                      tempfile.mkdtemp(prefix="lgtv-easy-autostart-"))

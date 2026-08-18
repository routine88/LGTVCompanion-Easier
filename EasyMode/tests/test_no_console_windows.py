"""No black terminal may blink on screen while the app is running.

The watcher is a windowed process (the installed build is ``console=False``;
from source it starts at login under ``pythonw``). A windowed process that
starts a console program - ``arp.exe`` when it hunts for a TV that moved,
``schtasks.exe`` when it registers auto-start - makes Windows allocate a brand
new console for the child and show it. The command takes milliseconds, so what
the user sees is a terminal flashing open and shut, over and over, for the first
few minutes after boot while the TV is still off.

``lgtv_easy.proc`` fixes that with CREATE_NO_WINDOW, and these tests keep it
fixed: the flags must be right, and no module may quietly go back to calling
``subprocess`` directly - one forgotten call site brings the flashing back.
"""
import ast
import re
import subprocess
from pathlib import Path

from lgtv_easy import proc

PKG = Path(proc.__file__).parent

# The calls that start a program. Anything else on the subprocess module (PIPE,
# DEVNULL, TimeoutExpired, the Popen type in an annotation) is inert and fine.
SPAWNERS = {"run", "Popen", "call", "check_call", "check_output", "getoutput",
            "getstatusoutput"}


def test_windows_gets_the_no_window_flag():
    kwargs = proc.hidden_kwargs(windows=True)
    assert kwargs["creationflags"] & proc.CREATE_NO_WINDOW


def test_other_platforms_get_nothing():
    # CREATE_NO_WINDOW is meaningless off Windows and Popen rejects it there.
    assert proc.hidden_kwargs(windows=False) == {}


def test_caller_flags_are_kept_not_clobbered():
    merged = proc._merge({"creationflags": 0x10, "timeout": 3})
    assert merged["timeout"] == 3
    if proc.hidden_kwargs():          # only meaningful on Windows
        assert merged["creationflags"] & proc.CREATE_NO_WINDOW
    assert merged["creationflags"] & 0x10


def test_helpers_still_run_a_real_command():
    # The wrappers must stay drop-in replacements, flags or no flags.
    out = proc.run(["echo", "hello"], capture_output=True, text=True)
    assert out.returncode == 0 and "hello" in out.stdout
    assert "hello" in proc.check_output(["echo", "hello"], text=True)
    child = proc.popen(["echo", "hello"], stdout=subprocess.DEVNULL)
    assert child.wait(timeout=10) == 0


def _direct_spawns(path: Path):
    """Every ``subprocess.<spawner>(...)`` call in one module."""
    tree = ast.parse(path.read_text(), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (isinstance(fn, ast.Attribute) and fn.attr in SPAWNERS
                and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
            hits.append(f"{path.name}:{node.lineno} subprocess.{fn.attr}(")
    return hits


def test_no_module_starts_a_process_behind_procs_back():
    offenders = []
    for path in sorted(PKG.glob("*.py")):
        if path.name == "proc.py":     # the one place that may call subprocess
            continue
        offenders += _direct_spawns(path)
    assert not offenders, (
        "these calls would flash a console window on Windows; route them "
        "through lgtv_easy.proc instead: " + ", ".join(offenders))


def test_the_places_that_shell_out_use_the_helper():
    # A guard against the guard: if these ever stop shelling out, the test above
    # would pass vacuously and the protection would quietly be worth nothing.
    for name in ("netdiag.py", "autostart.py", "system_sleep.py"):
        source = (PKG / name).read_text()
        assert re.search(r"\bproc\.(run|popen|check_output)\(", source), \
            f"{name} no longer runs anything through lgtv_easy.proc"

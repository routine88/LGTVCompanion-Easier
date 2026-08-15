"""Entry point for the frozen Windows build.

Both executables in the package start here - the windowed
``LGTV Companion Easy Mode.exe`` and the console ``lgtv-easy.exe`` - so they
accept exactly the same subcommands as ``python -m lgtv_easy``. With no
arguments that means the graphical control panel, which is what a double-click
on the desktop icon does.

``lgtv_easy.cli`` prints through the ordinary ``print``, and Python makes that a
no-op when there is no console (``sys.stdout is None``), so the windowed build
stays silent rather than crashing on its own status messages.
"""
import multiprocessing
import sys

from lgtv_easy.cli import main

if __name__ == "__main__":
    # A frozen app that ever spawns a child of itself must call this first, or
    # the child re-runs the whole program instead of the worker function.
    multiprocessing.freeze_support()
    sys.exit(main(sys.argv[1:]))

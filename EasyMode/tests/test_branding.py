"""Icons, app identity, and re-launching ourselves.

Small module, but three separate ways to ship a broken app hide in it:

* the icon files going missing from the package (the taskbar silently falls back
  to the generic Python feather, which is exactly what packaging was meant to fix),
* ``launch_command`` handing ``-m lgtv_easy`` to a frozen .exe, which would make
  every auto-start entry created by an installed copy fail at login,
* the Windows AppUserModelID drifting away from the one the installer stamps on
  the shortcuts, which quietly costs you taskbar pinning.
"""
import os
import sys
from pathlib import Path

import pytest

from lgtv_easy import branding

ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)


# ----- the artwork is actually there ------------------------------------
def test_every_icon_size_ships_with_the_package():
    missing = [n for n in ICON_SIZES
               if not (branding.ASSETS / f"icon-{n}.png").exists()]
    assert not missing, f"missing icon sizes: {missing} (run packaging/make_icons.py)"
    for name in ("icon.ico", "icon.png", "icon.svg"):
        assert (branding.ASSETS / name).exists(), f"missing {name}"


def test_icon_lookups_return_real_files():
    assert Path(branding.ico_path()).is_file()
    assert Path(branding.icon_png()).is_file()
    paths = branding.png_paths()
    assert len(paths) >= 5
    assert all(Path(p).is_file() for p in paths)
    # Largest first: Tk takes the first icon that fits, and a scaled-down 256
    # looks far better than a scaled-up 16.
    sizes = [int(Path(p).stem.split("-")[1]) for p in paths]
    assert sizes == sorted(sizes, reverse=True)


def test_the_ico_carries_the_small_sizes_windows_asks_for():
    """A one-size .ico is the classic cause of a blurry taskbar icon."""
    data = (branding.ASSETS / "icon.ico").read_bytes()
    count = int.from_bytes(data[4:6], "little")
    # Each 16-byte directory entry starts with width, height (0 means 256).
    widths = {data[6 + i * 16] or 256 for i in range(count)}
    assert {16, 32, 48, 256} <= widths, f"ico only has {sorted(widths)}"


# ----- how we start ourselves again -------------------------------------
def test_launch_command_from_source_runs_the_module():
    argv = branding.launch_command("run")
    assert argv[1:] == ["-m", "lgtv_easy", "run"]
    assert argv[0], "an interpreter path is required"


def test_launch_command_when_frozen_passes_the_subcommand_straight_through(
        tmp_path, monkeypatch):
    """The bug this exists to prevent: an installed .exe being told to run
    ``-m lgtv_easy run``, which it would read as three stray arguments."""
    exe = tmp_path / branding.GUI_EXE
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))

    assert branding.launch_command("run") == [str(exe), "run"]
    assert branding.app_dir() == tmp_path


def test_a_windowed_start_prefers_the_windowless_exe(tmp_path, monkeypatch):
    """Started from the console build, anything that runs at login should still
    hand off to the windowed one - otherwise every login flashes a black box."""
    gui_exe = tmp_path / branding.GUI_EXE
    cli_exe = tmp_path / branding.CLI_EXE
    for path in (gui_exe, cli_exe):
        path.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli_exe))

    assert branding.launch_command("run")[0] == str(gui_exe)
    assert branding.launch_command("status", windowed=False)[0] == str(cli_exe)


def test_a_missing_sibling_exe_falls_back_to_the_running_one(tmp_path, monkeypatch):
    cli_exe = tmp_path / branding.CLI_EXE
    cli_exe.write_bytes(b"")            # no GUI exe beside it
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(cli_exe))
    assert branding.launch_command("run") == [str(cli_exe), "run"]


def test_app_dir_from_source_is_the_package_parent():
    assert (branding.app_dir() / "lgtv_easy" / "__init__.py").exists()


# ----- identity ---------------------------------------------------------
def test_the_app_id_is_stable():
    # The installer writes this exact string onto the shortcuts it creates
    # (packaging/windows/installer.py), and Windows matches windows to pinned
    # shortcuts by comparing the two.
    assert branding.APP_ID == "LGTVCompanion.EasyMode"


def test_the_wm_class_survives_tks_capitalisation():
    """Tk builds disagree about capitalising the class half of WM_CLASS, so the
    name must be unchanged by it - otherwise it stops matching StartupWMClass in
    the .desktop file and the dock shows a placeholder icon."""
    name = branding.WM_CLASS
    assert name[:1].upper() + name[1:] == name


@pytest.mark.skipif(os.name != "nt", reason="Windows-only shell call")
def test_set_app_id_succeeds_on_windows():
    assert branding.set_app_id() is True


@pytest.mark.skipif(os.name == "nt", reason="checks the no-op path")
def test_set_app_id_is_a_no_op_elsewhere():
    assert branding.set_app_id() is False

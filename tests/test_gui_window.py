"""The TESRACT window: the interface compiles, runs are followed and grouped, settings default sanely."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.gui import runner
from core.gui.session import Session
from core.phase_info import PHASE_INFO

slint = pytest.importorskip("slint")

# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------


def test_the_interface_compiles():
    """Every .slint file loads, and the window exposes the globals the controllers bind to."""
    from core.gui.window import UI_FILE

    app = slint.load_file(UI_FILE).App()
    assert app.Ui.view == "choose" and app.Theme.mode in ("system", "dark", "light")


# ---------------------------------------------------------------------------
# Following and grouping runs
# ---------------------------------------------------------------------------

def test_every_phase_banner_names_its_step():
    """The Convert screen moves to a lane on the banner convert.py prints for it."""
    for _step, (banner, key, _scope) in PHASE_INFO.items():
        assert runner.phase_step(f"  {banner}  ") == key
    assert runner.phase_step("Reading Oblivion.esm") is None


def test_plugins_owing_the_same_steps_share_one_process():
    """One convert.py per group; steps come back in pipeline order, empty plans are dropped."""
    session = Session(cfg={})
    groups = runner.group_plugins(session, {"A.esp": ["scripts", "export"], "B.esp": ["export", "scripts"],
                                            "C.esp": ["meshes"], "D.esp": []})
    assert [(plugins, steps) for plugins, steps, _winding in groups] == [
        (["A.esp", "B.esp"], ["export", "scripts"]), (["C.esp"], ["meshes"])]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def test_load_order_mods_default_on_or_off():
    """Body patch, start mod and journal build unless turned off; the rest wait to be turned on."""
    session = Session(cfg={})
    assert [k for k in ("body", "start", "journal", "masters", "lod", "ui") if session.included(k)] == [
        "body", "start", "journal"]
    session.cfg["include.lod"] = True
    session.cfg["include.journal"] = False
    assert session.included("lod") and not session.included("journal")


# ---------------------------------------------------------------------------
# Starting other programs
# ---------------------------------------------------------------------------

def test_a_script_is_named_by_its_module_for_the_exe():
    """Inside TESRACT.exe a script runs as `--tesract-run <module>`; the name comes from its path."""
    from core import python_cmd

    script = python_cmd.CODE_ROOT / "tools" / "release" / "pack_lod.py"
    assert python_cmd.module_name(script) == "tools.release.pack_lod"
    assert python_cmd.python_cmd(script, "--output-dir", 3)[1:] == ["-u", str(script), "--output-dir", "3"]


def test_run_from_argv_runs_the_named_module_as_main(capsys):
    """The exe's entry point runs the module it was asked for, with the arguments after it."""
    from core import python_cmd

    assert not python_cmd.run_from_argv(["TESRACT.exe"])
    with pytest.raises(SystemExit):
        python_cmd.run_from_argv(["TESRACT.exe", python_cmd.RUN_FLAG, "tools.release.pack_lod", "--help"])
    assert "usage" in capsys.readouterr().out.lower()


def test_every_module_the_exe_runs_has_a_main():
    """TESRACT.exe runs a module by calling its main(); a module without one would do nothing there."""
    import ast

    from core.python_cmd import CODE_ROOT
    from tools.release.build_exe import RUN_MODULES

    for module in RUN_MODULES:
        tree = ast.parse(CODE_ROOT.joinpath(*module.split(".")).with_suffix(".py").read_text(encoding="utf-8"))
        assert any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in tree.body), module

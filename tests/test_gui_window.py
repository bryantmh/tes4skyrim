"""The TESRACT window: the interface compiles, runs are followed and grouped, settings default sanely."""

import ast
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

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


def _returns_value(fn) -> bool:
    """Whether a def has a `return <expr>` other than None."""
    return any(isinstance(n, ast.Return) and n.value is not None
               and not (isinstance(n.value, ast.Constant) and n.value.value is None) for n in ast.walk(fn))


def test_no_interface_callback_returns_a_value():
    """Slint raises on any value a void callback returns (a tuple from `lambda: (a(), b())` broke every filter)."""
    bad = []
    for path in sorted((Path(__file__).resolve().parents[1] / "core" / "gui").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        methods = {f.name: f for c in ast.walk(tree) if isinstance(c, ast.ClassDef)
                   for f in c.body if isinstance(f, ast.FunctionDef)}
        for node in ast.walk(tree):
            target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
            if not (isinstance(target, ast.Attribute) and getattr(target.value, "id", "") == "ui"):
                continue
            value = node.value
            if isinstance(value, ast.Lambda) and isinstance(value.body, (ast.Tuple, ast.BoolOp, ast.List, ast.Dict)):
                bad.append(f"{path.name}:{node.lineno} ui.{target.attr}")
            called = value.body.func if isinstance(value, ast.Lambda) and isinstance(value.body, ast.Call) else value
            if isinstance(called, ast.Attribute) and getattr(called.value, "id", "") == "self" \
                    and called.attr in methods and _returns_value(methods[called.attr]):
                bad.append(f"{path.name}:{node.lineno} ui.{target.attr} -> {called.attr}()")
    assert not bad


def test_a_finished_lod_rescan_never_reopens_a_closed_dialog():
    """Reset rescans on a worker; pressing Done before it ends must not bring the dialog back."""
    from types import SimpleNamespace

    from core.gui.loadorder_view import LoadOrderView

    painted = []
    view = SimpleNamespace(ctx=SimpleNamespace(ui=SimpleNamespace(dialog="")), edit="old",
                           _paint_lod=lambda: painted.append(True))
    LoadOrderView._reset_read(view, "new")
    assert (view.ctx.ui.dialog, view.edit, painted) == ("", "old", [])
    view.ctx.ui.dialog = "lod"
    LoadOrderView._reset_read(view, "new")
    assert (view.edit, painted) == ("new", [True])


def test_a_plugin_unticked_in_the_lod_dialog_stays_unticked_when_reread(monkeypatch):
    """Done keeps an unticked plugin off and in its place on the next read; a plugin it never saw comes in ticked."""
    from core.gui import loadorder

    names = ["Oblivion.esm", "Morrowind_ob.esm"]
    monkeypatch.setattr(loadorder, "default_lod_plugins", lambda session: list(names))
    monkeypatch.setattr(loadorder, "_scan_lod", lambda session, n: ({x: set() for x in n}, {}, {}))
    monkeypatch.setattr(loadorder, "masters_first", lambda plugins, masters_of: list(plugins))
    session = Session(cfg={})
    pick = loadorder.LodPick(session)
    pick.toggle_plugin(1, False)
    assert pick.confirm(session) == ""
    names.insert(0, "New.esp")
    reread = loadorder.LodPick(session)
    assert (reread.order, reread.checked()) == (["Oblivion.esm", "Morrowind_ob.esm", "New.esp"],
                                                ["Oblivion.esm", "New.esp"])
    sel = session.selection
    launched = Session(cfg={"lodChoice": {"order": sel.lod_order, "off": sel.lod_off, "force": True}})
    assert loadorder.LodPick(launched).checked() == ["Oblivion.esm", "New.esp"] and launched.selection.lod_force


def _lod_view(rows, plugin_status):
    """A LoadOrderView holding worldspace `rows` [(world, owners, stale)], with no window."""
    from core.gui.loadorder_view import LoadOrderView

    view = LoadOrderView.__new__(LoadOrderView)
    view.world_rows, view.world_ticks, view.plugin_status = rows, {}, plugin_status
    view.status = {}
    asked, built = [], []
    view.ctx = SimpleNamespace(session=Session(cfg={"include.lod": True}),
                               confirm=lambda *a, **k: asked.append((a, k)))
    view.build = built.append
    return view, asked, built


def test_only_out_of_date_worldspaces_start_ticked_and_a_tick_holds_until_a_run_ends():
    """The ticks start on the stale worldspaces; the user's own tick wins until `run_finished` drops it."""
    view, _asked, _built = _lod_view([("A", ["X.esm"], True), ("B", ["X.esm"], False)], {})
    assert view.ticked_worlds() == ["A"] and "lod" in view.ready_keys()
    view.world_ticks.update({"A": False, "B": True})
    assert view.ticked_worlds() == ["B"]
    view.world_ticks.clear()
    view.world_rows = [("B", ["X.esm"], False)]
    assert view.ticked_worlds() == [] and "lod" not in view.ready_keys()


def test_build_asks_only_when_a_ticked_worldspace_has_an_out_of_date_plugin():
    """A stale plugin under an unticked worldspace builds at once; under a ticked one, Build asks first."""
    from core.gui.loadorder_view import LoadOrderView

    view, asked, built = _lod_view([("A", ["Old.esm"], False), ("B", ["New.esm"], True)], {"Old.esm": "out"})
    LoadOrderView._build_ready(view)
    assert asked == [] and "lod" in built[0]
    view.world_ticks["A"] = True
    LoadOrderView._build_ready(view)
    (title, text, yes, no, _on_yes), extra = asked[0]
    assert ("Old.esm" in text, yes, no, extra["alt"]) == (True, "Build anyway", "Cancel", "Update them first")


def _run(lanes):
    """A RunView holding `lanes`, with no window and no log file."""
    from core.gui.run_view import RunView

    view = RunView.__new__(RunView)
    view.lanes, view.cmd_now, view.started = lanes, None, 0.0
    view.lines, view.total, view.pushed, view.log_rows = [], 0, 0, []
    view.sink = SimpleNamespace(take=lambda line: None)
    return view


def test_a_step_banner_moves_to_its_lane_and_never_closes_the_one_it_started():
    """Creatures then Sounds in one command, then Meshes in the next: each lane runs and ends in turn."""
    from core.gui.run_view import Lane

    lanes = [Lane("Creatures", "", 0, "creatures", ["B.esm"]), Lane("Sounds", "", 0, "sounds", ["B.esm"]),
             Lane("Meshes", "", 1, "meshes", ["T.esm"])]
    view = _run(lanes)
    view._event("cmd", 0)
    view._event("line", PHASE_INFO["creatures"][0])
    assert [lane.state for lane in lanes] == ["run", "idle", "idle"]
    view._event("line", PHASE_INFO["sounds"][0])
    view._event("cmd", 1)
    view._event("line", PHASE_INFO["meshes"][0])
    assert [lane.state for lane in lanes] == ["done", "done", "run"]


def _meshes_lane(plugins=("T.esm",)):
    """A Meshes lane with three sub-steps per plugin, as `eta` plans them."""
    from core.gui.run_view import Lane

    lane = Lane("Meshes", "", 0, "meshes", list(plugins))
    lane.plan([(p, name, secs, marker) for p in plugins for name, secs, marker in (
        ("Assembling Morrowind armor", 10.0, None), ("Converting NIFs", 100.0, r"^Found \d+ NIF files"),
        ("Copying textures", 20.0, r"^Copy Textures to Output"))], 0.0)
    return lane


def test_a_lane_follows_its_sub_steps_through_each_plugin(monkeypatch):
    """A sub-step's first line moves the lane to it; a later plugin's '[plugin] ' line moves to that plugin's
    first sub-step, closing whatever it skipped; the lane learns each finished sub-step's time."""
    from core.gui import eta

    learned = []
    monkeypatch.setattr(eta, "learn", lambda *args: learned.append(args))
    lane = _meshes_lane(("A.esm", "B.esm"))
    view = _run([lane])
    lane.begin(0.0)
    monkeypatch.setattr("time.monotonic", lambda: 12.0)
    view._substep("Found 900 NIF files in export")
    assert (lane.cur, lane.current().name, lane.subs[0].took()) == (1, "Converting NIFs", 12.0)
    monkeypatch.setattr("time.monotonic", lambda: 50.0)
    view._substep("[B.esm] Converting meshes (NIFs + textures)...")
    assert (lane.current().plugin, lane.current().name, lane.subs[2].took()) == ("B.esm", "Assembling Morrowind armor", 0)
    assert [(a[1], a[3]) for a in learned] == [("Assembling Morrowind armor", 12.0), ("Converting NIFs", 38.0)]
    view._substep("Found 9 NIF files in export")
    assert lane.current().name == "Converting NIFs" and lane.current().plugin == "B.esm"


def test_a_running_bar_never_ends_before_now_and_queued_lanes_follow_it():
    """Running: what it has run, what its sub-step still needs (a margin past its estimate) and the later
    sub-steps; the progress rate replaces the estimate once it counts; queued lanes start at its end."""
    from core.gui.run_view import OVERRUN_MIN, OVERRUN_SHARE, SPEED_PRIOR, Lane

    running, queued = _meshes_lane(), Lane("Import", "", 0, "import_", ["T.esm"])
    queued.plan([("T.esm", "Loading masters", 30.0, None)], 0.0)
    running.begin(0.0)
    view = _run([running, queued])
    assert view._times(4.0) == [(0.0, 130.0), (130.0, 160.0)]
    assert view._times(200.0)[0] == (0.0, 200.0 + OVERRUN_SHARE * 200.0 + 100.0 + 20.0)
    running.observe("Found 1000 NIF files", 10.0)
    running.report(100, 1000, 20.0)
    running.report(200, 1000, 30.0)
    assert view._times(30.0)[0] == (0.0, 30.0 + 80.0 + 20.0)
    assert view._times(30.0)[1][0] == 130.0
    running.observe("Copy Textures to Output", 300.0)
    speed = (300.0 + SPEED_PRIOR) / (110.0 + SPEED_PRIOR)
    assert view._times(330.0)[0][1] == pytest.approx(330.0 + 20.0 * speed - 30.0)
    assert view._times(400.0)[0][1] == pytest.approx(400.0 + max(OVERRUN_MIN, OVERRUN_SHARE * 100.0))


def test_a_short_sub_step_running_long_does_not_stretch_the_rest():
    """A 0.3 s sub-step that took 1 s says nothing about the run's speed (seen: a 12 s Creatures bar drawn 40 s)."""
    lane = _meshes_lane()
    lane.subs[0].estimate = 0.3
    lane.begin(0.0)
    lane.observe("Found 9 NIF files", 1.0)
    assert _run([lane])._speed() == 1.0


def test_the_bar_says_what_runs_and_a_queued_lane_its_estimate():
    """The running bar names its sub-step (and plugin when the lane has several, and its count); a queued one
    says Queued and its estimate; each has a short form for a narrow bar."""
    from core.gui.run_view import bar_text

    lane = _meshes_lane(("A.esm", "B.esm"))
    assert bar_text(lane, (0, 0)) == ("Queued · ~4:20", "Queued")
    lane.begin(0.0)
    lane.observe("Found 9 NIF files", 1.0)
    lane.report(3, 9, 2.0)
    assert bar_text(lane, (0, 0)) == ("Converting NIFs · A.esm · 3/9", "Running")


def test_the_log_waits_while_scrolled_up_and_keeps_its_last_lines():
    """New lines reach the log only while it follows its end, row by row, and only the last LOG_SHOWN of them; the
    lines are kept with their color tags."""
    from core.gui.run_view import LOG_SHOWN, RunView

    view = _run([])
    view.ctx = SimpleNamespace(ui=SimpleNamespace(log_follow=False))
    view.sink = SimpleNamespace(take=lambda line: "err" if "ERROR" in line else None)
    for i in range(LOG_SHOWN + 5):
        view._log(f"ERROR {i}" if i % 7 == 0 else str(i))
    RunView._push_log(view)
    assert view.log_rows == []
    view.ctx.ui.log_follow = True
    RunView._push_log(view)
    assert [r["text"] for r in view.log_rows] == [str(i) if i % 7 else f"ERROR {i}" for i in range(5, LOG_SHOWN + 5)]
    assert view.log_rows[-1 - (LOG_SHOWN + 4) % 7]["tag"] == "err"
    view._log("next")
    RunView._push_log(view)
    assert len(view.log_rows) == LOG_SHOWN and view.log_rows[-1] == {"text": "next", "tag": ""}
    view._copy()
    assert view.ctx.ui.log_copy.splitlines()[-1] == "next"


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


def test_the_runtime_reads_out_of_date_when_any_built_dll_is_newer_than_its_packaged_copy(tmp_path, monkeypatch):
    """A rebuilt DLL other than TESRuntime.dll, on a fresh launch with nothing remembered, needs packaging."""
    from tools.release import runtime_files

    dist, out = tmp_path / "dist", tmp_path / "out"
    files = [(dist / name, runtime_files.PLUGINS / name) for name in ("TESRuntime.dll", "MorrowindRuntime.dll")]
    for src, arc in files:
        src.parent.mkdir(exist_ok=True)
        src.write_bytes(b"old")
        (out / runtime_files.MOD_NAME / arc).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out / runtime_files.MOD_NAME / arc)
    monkeypatch.setitem(runtime_files.MODS, runtime_files.MOD_NAME, (tuple(files), ()))
    monkeypatch.setattr(runner.source_registry, "directory_for", lambda *_a: None)
    session = SimpleNamespace(out_root=lambda: out)
    assert runner.global_is_current(session, "package_runtime_dll", {})
    files[1][0].write_bytes(b"rebuilt")
    assert not runner.global_is_current(session, "package_runtime_dll", {})


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

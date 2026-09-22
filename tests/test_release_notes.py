"""Tests for tools/release/release_notes.py -- the path->GUI-step map used to annotate
each auto-tag (.github/workflows/tag-on-push.yml).

The bug these guard against: the notes told the user to re-run all 12 steps for
changes that only touched one stage.  Two causes, both covered below --
convert.py mapping to "ALL" regardless of which phase_* function changed, and
paths with no rule (TESGameSelect/, process_job.py, external/) falling into an
"unmapped -> select everything" precaution.  That precaution is gone: unmapped
paths now select nothing and are simply listed for the reader to judge.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.release.release_notes as rn


def steps(path):
    ordered, unmatched, _ = rn.steps_for_paths([path])
    assert not unmatched, f"{path} matched no rule"
    return ordered


# ── Every rule maps to a real GUI step ────────────────────────────────────

def test_rule_targets_are_known_steps():
    for _pattern, mapped in rn.RULES:
        for step in mapped:
            assert step in rn.STEP_ORDER or step in ("ALL", "GUI", "CONVERT"), step


def test_phase_map_targets_are_known_steps():
    for func, mapped in rn.PHASE_STEPS.items():
        for step in mapped:
            assert step in rn.STEP_ORDER, f"{func} -> {step}"


# ── Stage packages imply their own step, not every step ───────────────────

@pytest.mark.parametrize("path,expected", [
    ("tes5_import/record_types/actors.py", "6. Import"),
    ("script_convert/converter.py",        "8. Scripts"),
    ("asset_convert/nif/nif_converter.py",     "3. Meshes"),
    ("asset_convert/audio/audio_converter.py",   "7. Sounds"),
    ("asset_convert/lod/lod_gen.py",           "Create LOD"),
    ("asset_convert/sources/bsa_extract.py",       "2. Extract"),
    ("asset_convert/spt_reader.py",        "4. SpeedTrees"),
    ("asset_convert/hkx_convert.py",       "5. Creatures"),
])
def test_stage_paths_are_narrow(path, expected):
    got = steps(path)
    assert expected in got
    assert len(got) < len(rn.STEP_ORDER), f"{path} selected everything"


def test_export_also_implies_import():
    """The text cache Export writes is Import's only input."""
    assert steps("tes4_export/record_types/actors.py")[:2] == ["1. Export", "6. Import"]


# ── Non-pipeline paths cost nothing ───────────────────────────────────────

@pytest.mark.parametrize("path", [
    "docs/reference/pipeline.md",
    "tests/test_import.py",
    "tools/release/release_notes.py",
    "TODO.txt",
    "CLAUDE.md",
    ".github/workflows/tag-on-push.yml",
    # Vendored binaries.
    "external/bsarch/bsarch.exe",
    "TESConversion.code-workspace",
])
def test_non_pipeline_paths_need_no_rerun(path):
    assert steps(path) == []


@pytest.mark.parametrize("path", [
    "gui.py", "gui.pyw", "core/gui/app.py", "core/gui/panels.py",
])
def test_gui_change_is_reported_as_gui_only(path):
    """The GUI lives in core/gui/; a change there re-runs no pipeline step."""
    ordered, unmatched, gui_only = rn.steps_for_paths([path])
    assert not unmatched and ordered == [] and gui_only


@pytest.mark.parametrize("path", ["core/run_log.py", "core/plugin_masters.py"])
def test_core_reporting_plumbing_reruns_nothing(path):
    """These only report -- they produce no conversion output to stale."""
    assert steps(path) == []


def test_new_core_module_is_never_unmatched():
    """The bare `^core/` tail catches anything added there later."""
    _ordered, unmatched, _gui = rn.steps_for_paths(["core/some_new_module.py"])
    assert not unmatched


# ── Packaging is a consequence, never a standalone reason ─────────────────

def test_packaging_follows_a_producing_step():
    assert set(rn.PACKAGING_STEPS) <= set(steps("tes5_import/pipeline.py"))


def test_patch_skyrim_alone_does_not_drag_in_packaging():
    """Patch Skyrim writes a standalone ARMA patch that BSA/zip never read."""
    assert steps("asset_convert/character/modify_body_meshes.py") == ["Patch Skyrim"]


@pytest.mark.parametrize("path", [
    "TESGameSelect/scripts/source/TESGameSelectQuest.psc",
    "TESGameSelect/scripts/source/TESGameSelectMQ101.psc",
])
def test_starter_mod_repackages_itself_only(path):
    """Standing outside the pipeline, it re-runs only the packaging action."""
    assert steps(path) == ["Package Start Mod"]


@pytest.mark.parametrize("path", [
    "tes_runtime/MorrowindRuntime.dll",
    "tes_runtime/morrowind_runtime/plugin/game_calls.cpp",
    "tes_runtime/morrowind_runtime/plugin/ids.h",
    "tes_runtime/morrowind_runtime/build.bat",
])
def test_skse_runtime_repackages_itself_only(path):
    """The committed SKSE plugin re-runs only its own packaging action."""
    assert steps(path) == ["Package SKSE Mod"]


# ── Shared plumbing legitimately means everything ─────────────────────────

@pytest.mark.parametrize("path", [
    "core/process_job.py", "core/worker_budget.py", "core/subprocess_flags.py",
])
def test_pool_plumbing_implies_all_steps(path):
    assert steps(path) == rn.STEP_ORDER


# ── native/: the extension rebuilds, its docs do not ──────────────────────

@pytest.mark.parametrize("path", ["native/build.py", "native/dist/navmesh.pyd"])
def test_native_code_implies_the_asset_steps(path):
    assert "3. Meshes" in steps(path)


@pytest.mark.parametrize("path", ["native/dist/README.md", "native/notes.txt"])
def test_native_docs_trigger_nothing(path):
    """0.57 charged a mesh + creature + LOD rebuild for `native/dist/README.md`.

    The doc rule must stay ABOVE the blanket `^native/` rule -- first match
    wins, so reordering silently reinstates hours of needless rebuilding.
    """
    assert steps(path) == []


def test_unmapped_path_selects_no_steps():
    """An unrecognised path is reported, not turned into a 12-step re-run."""
    ordered, unmatched, _ = rn.steps_for_paths(["brand_new_package/thing.py"])
    assert unmatched == ["brand_new_package/thing.py"]
    assert ordered == []


def test_unmapped_path_does_not_widen_a_known_change():
    ordered, unmatched, _ = rn.steps_for_paths(
        ["asset_convert/lod/lod_gen.py", "brand_new_package/thing.py"])
    assert unmatched == ["brand_new_package/thing.py"]
    assert "Create LOD" in ordered
    assert "1. Export" not in ordered


# ── convert.py resolves per phase_* function ──────────────────────────────

def test_every_phase_function_in_convert_py_is_mapped():
    """A new phase_* function must get a PHASE_STEPS entry, or convert.py
    changes silently fall back to selecting all 12 steps."""
    src = (rn.SCRIPT_DIR / "convert.py").read_text(encoding="utf-8")
    import re
    found = set(re.findall(r"^def (phase_\w+)", src, re.MULTILINE))
    assert found - set(rn.PHASE_STEPS) == set(), "unmapped phase_* function"


def test_convert_py_narrows_to_the_changed_phase(monkeypatch):
    monkeypatch.setattr(rn, "_run", lambda a: (
        "@@ -580,17 +580,37 @@ def phase_lod(file_name: str, tes5_data: str, config: dict,\n"
        "@@ -639,15 +659,28 @@ def phase_lod(file_name: str, tes5_data: str, config: dict,\n"
    ))
    assert rn.convert_py_steps("0.39", "0.40") == ["Create LOD"]


def test_main_is_orchestration_and_narrows_to_nothing(monkeypatch):
    """main() parses args and dispatches phases; it produces no output.

    It used to count as unattributable -> every step, so any CLI-plumbing commit
    demanded a full multi-hour reconversion.  Measured on 0.58..0.581: all 16
    convert.py hunks were in main(), and the release asked for all twelve steps
    when only Meshes and Scripts had actually changed.
    """
    monkeypatch.setattr(rn, "_run", lambda a: (
        "@@ -580,17 +580,37 @@ def phase_lod(file_name: str, tes5_data: str, config: dict,\n"
        "@@ -788,4 +822,4 @@ def main():\n"
    ))
    assert rn.convert_py_steps("0.18", "0.19") == ["Create LOD"]


def test_a_main_only_change_costs_no_steps(monkeypatch):
    """An EMPTY list is a real answer -- it must not degrade to None.

    None means "could not attribute" and selects everything, so returning it
    for a main()-only diff would resurrect the exact bug above.
    """
    monkeypatch.setattr(rn, "_run", lambda a: (
        "@@ -788,4 +822,4 @@ def main():\n"
        "@@ -900,2 +934,9 @@ def main():\n"
    ))
    assert rn.convert_py_steps("0.18", "0.19") == []
    ordered, _unmatched, _gui = rn.steps_for_paths(["convert.py"], [])
    assert ordered == []


def test_convert_py_declines_to_narrow_for_shared_code(monkeypatch):
    """A genuinely unattributable hunk still falls back to every step.

    Module scope (no function in the header) and unknown helpers can affect any
    phase, so narrowing there would under-select.
    """
    monkeypatch.setattr(rn, "_run", lambda a: (
        "@@ -580,17 +580,37 @@ def phase_lod(file_name: str, tes5_data: str, config: dict,\n"
        "@@ -12,3 +12,5 @@\n"                       # module scope
    ))
    assert rn.convert_py_steps("0.18", "0.19") is None

    monkeypatch.setattr(rn, "_run", lambda a: (
        "@@ -300,4 +300,8 @@ def _shared_helper(config):\n"
    ))
    assert rn.convert_py_steps("0.18", "0.19") is None


def test_convert_py_fallback_selects_all_steps():
    ordered, unmatched, _ = rn.steps_for_paths(["convert.py"], None)
    assert not unmatched
    assert ordered == rn.STEP_ORDER


def test_convert_py_uses_supplied_attribution():
    ordered, _, _ = rn.steps_for_paths(["convert.py"], ["Create LOD"])
    assert "Create LOD" in ordered
    assert "1. Export" not in ordered


# ── Output shape ──────────────────────────────────────────────────────────

def test_steps_are_emitted_in_gui_order():
    ordered, _, _ = rn.steps_for_paths(
        ["asset_convert/lod/lod_gen.py", "tes4_export/x.py", "script_convert/y.py"])
    assert ordered == [s for s in rn.STEP_ORDER if s in ordered]

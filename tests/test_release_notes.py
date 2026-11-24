"""Tests for tools/release/release_notes.py -- the path->GUI-step map used to annotate
each auto-tag (.github/workflows/tag-on-push.yml).

The bug these guard against: the notes told the user to re-run every step for
changes that only touched one stage, or touched no conversion output at all.
See: docs/commentary/version_upgrade_planning.md#what-a-release-owes
"""
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.release.release_notes as rn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def steps(path):
    """The steps one path selects; fails if no rule matches it."""
    ordered, unmatched, _ = rn.steps_for_paths([path])
    assert not unmatched, f"{path} matched no rule"
    return ordered


def fake_sources(monkeypatch, old, new):
    """Make every file read `old` at the first revision and `new` at any other."""
    monkeypatch.setattr(rn, "_source_at",
                        lambda rev, path: old if rev == "A" else new)


# ---------------------------------------------------------------------------
# Every rule maps to a real GUI step
# ---------------------------------------------------------------------------

def test_rule_targets_are_known_steps():
    """RULES names only GUI steps or the three markers."""
    for _pattern, mapped in rn.RULES:
        for step in mapped:
            assert step in rn.STEP_ORDER or step in ("ALL", "GUI", "CONVERT"), step


def test_phase_map_targets_are_known_steps():
    """PHASE_STEPS names only GUI steps."""
    for func, mapped in rn.PHASE_STEPS.items():
        for step in mapped:
            assert step in rn.STEP_ORDER, f"{func} -> {step}"


def test_every_tracked_path_has_a_rule():
    """A new top-level file or folder needs a rule before it can ship."""
    tracked = subprocess.run(["git", "ls-files"], cwd=rn.SCRIPT_DIR, check=True,
                             capture_output=True, encoding="utf-8").stdout
    assert [p for p in tracked.splitlines() if rn.rule_for(p) is None] == []


# ---------------------------------------------------------------------------
# Stage packages imply their own step, not every step
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("tes5_import/record_types/actors.py", "6. Import"),
    ("script_convert/converter.py",        "8. Scripts"),
    ("papyrus_compile.py",                 "8. Scripts"),
    ("navmesh_pins/Oblivion.esm.json",     "6. Import"),
    ("asset_convert/nif/nif_converter.py",     "3. Meshes"),
    ("asset_convert/audio/audio_converter.py",   "7. Sounds"),
    ("asset_convert/lod/lod_gen.py",           "Create LOD"),
    ("asset_convert/sources/bsa_extract.py",       "2. Extract"),
    ("asset_convert/spt_reader.py",        "4. SpeedTrees"),
    ("asset_convert/hkx_convert.py",       "5. Creatures"),
])
def test_stage_paths_are_narrow(path, expected):
    """A stage module selects its own step and not everything."""
    got = steps(path)
    assert expected in got
    assert len(got) < len(rn.STEP_ORDER), f"{path} selected everything"


def test_export_also_implies_import():
    """The text cache Export writes is Import's only input."""
    assert steps("tes4_export/record_types/actors.py")[:2] == ["1. Export", "6. Import"]


# ---------------------------------------------------------------------------
# Non-pipeline paths cost nothing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "docs/reference/pipeline.md",
    "tests/test_import.py",
    "tools/release/release_notes.py",
    "TODO.txt",
    "CLAUDE.md",
    ".github/workflows/tag-on-push.yml",
    "external/bsarch/bsarch.exe",
    "TESConversion.code-workspace",
    "requirements.txt",
    "convert_cli.py",
    "source_paths.py",
    "game_bridge/TESGameBridge.dll",
    "navmesh_cache/README.md",
])
def test_non_pipeline_paths_need_no_rerun(path):
    """Docs, tests, tools, vendored binaries and CLI plumbing select nothing."""
    assert steps(path) == []


@pytest.mark.parametrize("path", [
    "gui.py", "gui.pyw", "core/gui/window.py", "core/gui/ui/app.slint",
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


# ---------------------------------------------------------------------------
# Packaging is a consequence, never a standalone reason
# ---------------------------------------------------------------------------

def test_packaging_follows_a_producing_step():
    """Pack BSAs and Pack Mod Zip follow any per-plugin step."""
    assert set(rn.PACKAGING_STEPS) <= set(steps("tes5_import/pipeline.py"))


def test_patch_skyrim_alone_does_not_drag_in_packaging():
    """Body Slot Patch writes a standalone patch zip that BSA/zip never read."""
    assert steps("tools/creature/patch_body_slots.py") == ["Body Slot Patch"]


@pytest.mark.parametrize("path", [
    "TESGameSelect/scripts/source/TESGameSelectQuest.psc",
    "TESGameSelect/scripts/source/TESGameSelectMQ101.psc",
])
def test_starter_mod_repackages_itself_only(path):
    """Standing outside the pipeline, it re-runs only the packaging action."""
    assert steps(path) == ["Package Start Mod"]


@pytest.mark.parametrize("path", [
    "tes_runtime/dist/MorrowindRuntime.dll",
    "tes_runtime/morrowind/plugin/game_calls.cpp",
    "tes_runtime/morrowind/plugin/ids.h",
    "tes_runtime/morrowind/build.bat",
])
def test_skse_runtime_repackages_itself_only(path):
    """The committed SKSE plugin re-runs only its own packaging action."""
    assert steps(path) == ["Package SKSE Mod"]


# ---------------------------------------------------------------------------
# Shared plumbing legitimately means everything
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "core/process_job.py", "core/worker_budget.py", "core/subprocess_flags.py",
    "output_layout.py",
])
def test_shared_plumbing_implies_all_steps(path):
    """Pool plumbing and the output layout feed every step."""
    assert steps(path) == rn.STEP_ORDER


def test_notes_name_what_forced_every_step():
    """Every-step causes are listed so the reader can judge them."""
    assert rn.all_steps_causes(["docs/a.md", "core/process_job.py"], []) == \
        ["core/process_job.py"]


# ---------------------------------------------------------------------------
# native/: the extension rebuilds, its docs do not
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["native/build.py", "native/dist/navmesh.pyd"])
def test_native_code_implies_the_asset_steps(path):
    """The native extension feeds meshes, creatures and LOD."""
    assert "3. Meshes" in steps(path)


@pytest.mark.parametrize("path", ["native/dist/README.md", "native/notes.txt"])
def test_native_docs_trigger_nothing(path):
    """The doc rule must stay above the blanket `^native/` rule."""
    assert steps(path) == []


def test_unmapped_path_selects_no_steps():
    """An unrecognised path is reported, not turned into an every-step re-run."""
    ordered, unmatched, _ = rn.steps_for_paths(["brand_new_package/thing.py"])
    assert unmatched == ["brand_new_package/thing.py"]
    assert ordered == []


def test_unmapped_path_does_not_widen_a_known_change():
    """An unmapped path leaves a known change's steps as they are."""
    ordered, unmatched, _ = rn.steps_for_paths(
        ["asset_convert/lod/lod_gen.py", "brand_new_package/thing.py"])
    assert unmatched == ["brand_new_package/thing.py"]
    assert "Create LOD" in ordered
    assert "1. Export" not in ordered


# ---------------------------------------------------------------------------
# Python files: only a modified unit counts
# ---------------------------------------------------------------------------

OLD_MODULE = '''"""Doc."""
import os

LIMIT = 3


def helper(x):
    """Old doc."""
    return x + LIMIT
'''


@pytest.mark.parametrize("new", [
    OLD_MODULE.replace('"""Old doc."""', '"""New doc."""'),
    OLD_MODULE.replace("import os", "import os\nimport re"),
    OLD_MODULE + "\n\ndef brand_new():\n    return 1\n",
    OLD_MODULE.replace("return x + LIMIT", "return x + LIMIT  # note"),
])
def test_docs_imports_comments_and_new_functions_change_nothing(monkeypatch, new):
    """Nothing runs differently, so the file's rule never fires."""
    fake_sources(monkeypatch, OLD_MODULE, new)
    assert rn.modified_units("tes5_import/x.py", "A", "B") == set()
    assert rn.inert_paths(["tes5_import/x.py"], "A", "B") == {"tes5_import/x.py"}


@pytest.mark.parametrize("new,unit", [
    (OLD_MODULE.replace("x + LIMIT", "x - LIMIT"), "helper"),
    (OLD_MODULE.replace("LIMIT = 3", "LIMIT = 4"), "LIMIT"),
    (OLD_MODULE + "\n\n@register\ndef hook():\n    return 1\n", "hook"),
    (OLD_MODULE + "\nhelper(1)\n", rn.MODULE_UNIT),
])
def test_real_code_changes_count(monkeypatch, new, unit):
    """Changed bodies, constants, new decorated defs and loose code are changes."""
    fake_sources(monkeypatch, OLD_MODULE, new)
    assert rn.modified_units("tes5_import/x.py", "A", "B") == {unit}


def test_new_file_is_never_inert(monkeypatch):
    """A file absent at one end gets its rule; nothing proves it unused."""
    monkeypatch.setattr(rn, "_source_at",
                        lambda rev, path: None if rev == "A" else OLD_MODULE)
    assert rn.inert_paths(["tes5_import/x.py"], "A", "B") == set()


# ---------------------------------------------------------------------------
# convert.py resolves through its call graph
# ---------------------------------------------------------------------------

CONVERT_OLD = '''
import sys

OUT = "output"


def _shared(x):
    return x


def phase_import(f):
    return _shared(f)


def phase_lod(f):
    return f


def _run_pipeline():
    return phase_import(OUT)


def main():
    return _run_pipeline()


if __name__ == "__main__":
    sys.exit(main())
'''


def test_every_phase_function_in_convert_py_is_mapped():
    """A new phase_* needs a PHASE_STEPS entry, or its changes select nothing."""
    src = (rn.SCRIPT_DIR / "convert.py").read_text(encoding="utf-8")
    found = set(re.findall(r"^def (phase_\w+)", src, re.MULTILINE))
    assert found - set(rn.PHASE_STEPS) == set(), "unmapped phase_* function"


@pytest.mark.parametrize("old,new,expected", [
    ("return f\n", "return f + 1\n", ["Create LOD"]),
    ("return x\n", "return x * 2\n", ["6. Import"]),
    ("return phase_import(OUT)", "return phase_import(OUT + '/x')", []),
    ("return _run_pipeline()", "return _run_pipeline() or 0", []),
    ("import sys\n", "import sys\nimport os\n", []),
    ('OUT = "output"', 'OUT = "out"', []),
])
def test_convert_py_costs_only_phases_that_reach_the_change(monkeypatch, old, new, expected):
    """Phases, their helpers, and orchestration each cost what they reach."""
    fake_sources(monkeypatch, CONVERT_OLD, CONVERT_OLD.replace(old, new, 1))
    assert rn.convert_py_steps("A", "B") == expected


def test_convert_py_loose_code_change_means_every_step(monkeypatch):
    """Top-level code that binds no name may affect anything."""
    fake_sources(monkeypatch, CONVERT_OLD, CONVERT_OLD + "\nsys.setrecursionlimit(5000)\n")
    assert rn.convert_py_steps("A", "B") is None


def test_convert_py_fallback_selects_all_steps():
    """None from convert_py_steps selects every step."""
    ordered, unmatched, _ = rn.steps_for_paths(["convert.py"], None)
    assert not unmatched
    assert ordered == rn.STEP_ORDER
    assert rn.all_steps_causes(["convert.py"], None) == ["convert.py"]


def test_convert_py_uses_supplied_attribution():
    """A narrowed convert.py answer selects only its steps."""
    ordered, _, _ = rn.steps_for_paths(["convert.py"], ["Create LOD"])
    assert "Create LOD" in ordered
    assert "1. Export" not in ordered


def test_a_convert_py_change_costing_nothing_selects_nothing():
    """An EMPTY answer is real and must not degrade to every step."""
    ordered, _unmatched, _gui = rn.steps_for_paths(["convert.py"], [])
    assert ordered == []


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

def test_steps_are_emitted_in_gui_order():
    """The checklist follows the GUI's order."""
    ordered, _, _ = rn.steps_for_paths(
        ["asset_convert/lod/lod_gen.py", "tes4_export/x.py", "script_convert/y.py"])
    assert ordered == [s for s in rn.STEP_ORDER if s in ordered]

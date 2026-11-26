#!/usr/bin/env python
"""Build release-tag notes: commits since the previous tag plus the GUI
pipeline steps those commits require the user to re-run.

Used by .github/workflows/tag-on-push.yml to annotate each auto-tag; runs
standalone from the repo root (it imports version.STEP_KEYS):

    python -m tools.release.release_notes                  # last tag -> HEAD
    python -m tools.release.release_notes --from 1.07 --to HEAD
    python -m tools.release.release_notes --tag 1.08       # title the notes

See: docs/commentary/version_upgrade_planning.md#what-a-release-owes
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import warnings
from pathlib import Path

from core.subprocess_flags import POPEN_FLAGS
from version import STEP_KEYS

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent

#: Every step label in GUI run order. See: docs/commentary/version_upgrade_planning.md#one-table-not-four
STEP_ORDER = [label for _key, label in STEP_KEYS]

#: Steps that only repackage the per-plugin pipeline's output; added whenever a producing step fires.
PACKAGING_STEPS = ["9. Pack BSAs", "10. Pack Mod Zip"]

#: The standalone LOD mod's packager, added only when its one producing step fires.
LOD_PACKAGING_STEP = "Pack LOD"
LOD_PRODUCING_STEP = "Create LOD"

#: Global actions packaging their own standalone artefact, never read by 9./10.
STANDALONE_STEPS = frozenset(
    {"Body Slot Patch", "Package Start Mod", "Package SKSE Mod",
     LOD_PRODUCING_STEP, LOD_PACKAGING_STEP})

#: (repo-path regex, steps it stales); first match wins. See: docs/commentary/version_upgrade_planning.md#the-rules
RULES: list[tuple[str, list[str]]] = [
    (r"^tes4_export/",            ["1. Export", "6. Import"]),
    (r"^tes5_import/",            ["6. Import"]),
    (r"^script_convert/",         ["8. Scripts"]),
    (r"^papyrus_compile\.py$",    ["8. Scripts"]),
    (r"^navmesh_pins/",           ["6. Import"]),

    (r"^asset_convert/(?:\w+/)*bsa_extract\.py",        ["2. Extract"]),
    (r"^asset_convert/(?:\w+/)*(spt_\w+|flipbook)\.py", ["4. SpeedTrees"]),
    (r"^asset_convert/(?:\w+/)*(creature_pipeline|hkx_\w+|animation_data|"
     r"extract_skeleton_bones|kf_decode|kf_writer)\.py",
                                               ["5. Creatures"]),
    (r"^asset_convert/generated/",             ["5. Creatures"]),
    (r"^asset_convert/(?:\w+/)*(audio_converter)\.py",  ["7. Sounds"]),
    (r"^asset_convert/(?:\w+/)*(sibling_lod|lod_gen|lod_far_gen|terrain_lod|"
     r"terrain_lod_textures|landscape_normals)\.py",
                                               ["Create LOD"]),
    (r"^asset_convert/(?:\w+/)*worldmap_clouds\.py",    ["6. Import", "Create LOD"]),
    (r"^asset_convert/(?:\w+/)*(body_slots|mesh_cut)\.py", ["3. Meshes", "Body Slot Patch"]),
    (r"^tools/creature/patch_body_slots\.py",           ["Body Slot Patch"]),
    (r"^asset_convert/(?:\w+/)*skin_replacement\.py",   ["3. Meshes", "Body Slot Patch"]),
    (r"^asset_convert/(?:\w+/)*(bsa_pack|bsa_write)\.py", ["9. Pack BSAs"]),
    (r"^asset_convert/(?:\w+/)*texture_prune\.py",      ["3. Meshes"]),
    (r"^asset_convert/(?:\w+/)*skyrim_assets\.py",
                                               ["3. Meshes", "5. Creatures",
                                                "Body Slot Patch"]),
    (r"^asset_convert/ui/(?:ui_menus|ui_cursor|swf)\.py$", ["Convert Oblivion UI"]),
    (r"^asset_convert/(?:\w+/)*",                       ["3. Meshes"]),

    (r"^native/.*\.(md|txt)$",    []),
    (r"^native/",                 ["3. Meshes", "5. Creatures", "Create LOD"]),
    (r"^convert\.py$",            ["CONVERT"]),
    (r"^output_layout\.py$",      ["ALL"]),
    (r"^core/collision_options\.py$",  ["3. Meshes"]),
    (r"^core/(?:worker_budget|subprocess_flags|process_job)\.py$", ["ALL"]),
    (r"^core/(?:run_log|plugin_masters)\.py$", []),
    (r"^gui\.py$|^gui\.pyw$|^core/gui/|^core/", ["GUI"]),

    (r"^docs/",                   []),
    (r"^tests/",                  []),
    (r"^tools/release/create_lod\.py$",       ["Create LOD"]),
    (r"^tools/release/pack_lod\.py$",         ["Pack LOD"]),
    (r"^tools/release/package_start_mod\.py$", ["Package Start Mod"]),
    (r"^tools/release/package_runtime_dll\.py$", ["Package SKSE Mod"]),
    (r"^tools/misc/convert_ui\.py$",          ["Convert Oblivion UI"]),
    (r"^tools/",                  []),
    (r"^references/",             []),
    (r"^external/",               []),
    (r"^game_bridge/|^navmesh_cache/", []),
    (r"^\.github/",               []),
    (r"^\.claude/|^\.vscode/",    []),
    (r"^TESGameSelect/",          ["Package Start Mod"]),
    (r"^tes_runtime/",            ["Package SKSE Mod"]),
    (r"^preflight\.py$|^convert_cli\.py$|^source_paths\.py$", []),
    (r"^requirements\.txt$",      []),
    (r"^version\.py$|^VERSION$",  []),
    (r"^CLAUDE\.md$|^README\.md$|^TODO\.txt$|^CK_WARNINGS", []),
    (r"^conversion_config\.json$|^pyproject\.toml$|^\.git\w+$", []),
    (r"^[^/]+\.code-workspace$", []),
]

#: The step each convert.py phase function produces. See: docs/commentary/version_upgrade_planning.md#convert-py
PHASE_STEPS: dict[str, list[str]] = {
    "phase_export":             ["1. Export"],
    "phase_extract":            ["2. Extract"],
    "phase_assets":             ["3. Meshes"],
    "phase_speedtrees":         ["4. SpeedTrees"],
    "phase_creatures":          ["5. Creatures"],
    "phase_import":             ["6. Import"],
    "phase_sounds":             ["7. Sounds"],
    "phase_scripts":            ["8. Scripts"],
    "phase_compile":            ["8. Scripts"],
    "phase_lod":                ["Create LOD"],
    "phase_modify_body_meshes": ["Body Slot Patch"],
    "phase_pack":               ["9. Pack BSAs"],
    "phase_pack_zip":           ["10. Pack Mod Zip"],
}

#: The unit holding a file's top-level code that is not a def, class, assignment or import.
MODULE_UNIT = "<module>"


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

def _run(args: list[str]) -> str:
    """Stdout of `git <args>` run at the repo root, stripped."""
    return subprocess.run(
        ["git", *args], cwd=SCRIPT_DIR, check=True,
        capture_output=True, encoding="utf-8", errors="replace", **POPEN_FLAGS,
    ).stdout.strip()


def _tag_key(tag: str) -> tuple[int, int]:
    """Rank a MAJOR.MM or MAJOR.MMM tag in thousandths; unparseable sorts first."""
    major, _, minor = tag.partition(".")
    try:
        return (int(major), int(minor) * (10 if len(minor) == 2 else 1))
    except ValueError:
        return (-1, -1)


def previous_tag() -> str | None:
    """Latest release tag (MAJOR.MM or MAJOR.MMM) in the repo; None when there is none."""
    try:
        tags = _run(["tag", "-l", "[0-9]*.[0-9][0-9]",
                     "[0-9]*.[0-9][0-9][0-9]"]).splitlines()
    except subprocess.CalledProcessError:
        return None
    tags = [t.strip() for t in tags if t.strip()]
    return max(tags, key=_tag_key) if tags else None


def commits_between(rev_from: str | None, rev_to: str) -> list[tuple[str, str]]:
    """[(short_sha, subject)] oldest-first for rev_from..rev_to."""
    rng = f"{rev_from}..{rev_to}" if rev_from else rev_to
    out = _run(["log", "--reverse", "--no-merges", "--format=%h%x1f%s", rng])
    return [tuple(line.split("\x1f", 1)) for line in out.splitlines()
            if "\x1f" in line]


def changed_files(rev_from: str | None, rev_to: str) -> list[str]:
    """Paths changed in rev_from..rev_to, or every tracked path when rev_from is None."""
    if rev_from:
        out = _run(["diff", "--name-only", f"{rev_from}..{rev_to}"])
    else:
        out = _run(["ls-tree", "-r", "--name-only", rev_to])
    return [p for p in out.splitlines() if p.strip()]


def _source_at(rev: str, path: str) -> str | None:
    """`path`'s text at `rev`; None when it does not exist there."""
    try:
        return _run(["show", f"{rev}:{path}"])
    except subprocess.CalledProcessError:
        return None


# ---------------------------------------------------------------------------
# Python structure: which top-level units really changed
# ---------------------------------------------------------------------------

def _parse(source: str) -> ast.Module:
    """`ast.parse`, without the SyntaxWarnings an old revision's escapes raise."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(source)


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    """Drop every module, class and function docstring from `tree` in place."""
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                              ast.AsyncFunctionDef))
                and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return tree


def _unit_name(node: ast.stmt) -> str | None:
    """The name a top-level def, class or single-name assignment binds, else None."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    targets = getattr(node, "targets", None) or [getattr(node, "target", None)]
    if isinstance(node, (ast.Assign, ast.AnnAssign)) and len(targets) == 1 \
            and isinstance(targets[0], ast.Name):
        return targets[0].id
    return None


def _units(source: str) -> dict[str, tuple[str, bool]] | None:
    """{unit name: (AST dump, decorated)} for a file's top level; None if unparseable.

    Imports are omitted, and every statement that binds no name is pooled
    into MODULE_UNIT.
    """
    try:
        tree = _strip_docstrings(_parse(source))
    except SyntaxError:
        return None
    units: dict[str, tuple[str, bool]] = {}
    loose: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        name = _unit_name(node)
        if name is None:
            loose.append(ast.dump(node))
        else:
            units[name] = (ast.dump(node), bool(getattr(node, "decorator_list", ())))
    units[MODULE_UNIT] = ("\n".join(loose), False)
    return units


def modified_units(path: str, rev_from: str | None, rev_to: str) -> set[str] | None:
    """Top-level units of a Python file whose code changed; None when not comparable.

    A brand-new undecorated unit is not a change: nothing runs it until a
    caller changes, and that caller is a change of its own.  Comments,
    docstrings and imports never count.
    See: docs/commentary/version_upgrade_planning.md#python-files
    """
    if not path.endswith(".py") or not rev_from:
        return None
    sources = [_source_at(rev, path) for rev in (rev_from, rev_to)]
    if None in sources:
        return None
    old, new = (_units(s) for s in sources)
    if old is None or new is None:
        return None
    return ({name for name, unit in old.items() if new.get(name) != unit}
            | {name for name, (_dump, decorated) in new.items()
               if name not in old and decorated})


def _references(node: ast.AST) -> set[str]:
    """Every bare name `node` loads."""
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _reachable(graph: dict[str, set[str]], start: str) -> set[str]:
    """`start` plus every top-level unit it references, transitively."""
    seen, todo = set(), [start]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(graph.get(name, ()))
    return seen


def convert_py_steps(rev_from: str | None, rev_to: str) -> list[str] | None:
    """Steps a convert.py change stales: each phase_* that reaches a changed unit.

    A changed unit no phase reaches (main, the run and dispatch helpers) is
    orchestration and costs nothing.  None -- every step -- when the file is
    new, unparseable, or its loose top-level code changed.
    See: docs/commentary/version_upgrade_planning.md#convert-py
    """
    changed = modified_units("convert.py", rev_from, rev_to)
    if changed is None or MODULE_UNIT in changed:
        return None
    tree = _parse(_source_at(rev_to, "convert.py"))
    graph = {_unit_name(n): _references(n) for n in tree.body if _unit_name(n)}
    steps = {step for phase, mapped in PHASE_STEPS.items()
             if _reachable(graph, phase) & changed for step in mapped}
    return sorted(steps)


def inert_paths(paths: list[str], rev_from: str | None, rev_to: str) -> set[str]:
    """Python files among `paths` whose change modified no top-level unit."""
    return {p for p in paths if p != "convert.py"
            and modified_units(p, rev_from, rev_to) == set()}


# ---------------------------------------------------------------------------
# Paths to steps
# ---------------------------------------------------------------------------

def rule_for(path: str) -> list[str] | None:
    """The steps RULES maps `path` to (first match wins); None when no rule matches."""
    p = path.replace("\\", "/")
    return next((mapped for pattern, mapped in RULES if re.search(pattern, p)), None)


def _forces_all(mapped: list[str], convert_steps: list[str] | None) -> bool:
    """True when a rule's result means every step."""
    return "ALL" in mapped or ("CONVERT" in mapped and convert_steps is None)


def all_steps_causes(paths: list[str], convert_steps: list[str] | None) -> list[str]:
    """The paths that forced every step, for the notes to name."""
    return [p for p in paths
            if (mapped := rule_for(p)) is not None and _forces_all(mapped, convert_steps)]


def _rule_steps(mapped: list[str], convert_steps: list[str] | None) -> list[str]:
    """The concrete steps one rule's result stands for."""
    if _forces_all(mapped, convert_steps):
        return STEP_ORDER
    if "CONVERT" in mapped:
        return convert_steps
    return [] if "GUI" in mapped else mapped


def _with_packaging(steps: set[str]) -> list[str]:
    """`steps` plus the packaging they imply, in GUI order."""
    if steps - STANDALONE_STEPS:
        steps |= set(PACKAGING_STEPS)
    if LOD_PRODUCING_STEP in steps:
        steps.add(LOD_PACKAGING_STEP)
    return [s for s in STEP_ORDER if s in steps]


def steps_for_paths(paths: list[str],
                    convert_steps: list[str] | None = None,
                    ) -> tuple[list[str], list[str], bool]:
    """→ (ordered steps to re-run, paths no rule matched, gui_only_change).

    `convert_steps` is `convert_py_steps`'s answer; None means every step.
    `gui_only_change` is True when the GUI changed but no conversion output did.
    """
    steps: set[str] = set()
    unmatched: list[str] = []
    gui_touched = False
    for path in paths:
        mapped = rule_for(path)
        if mapped is None:
            unmatched.append(path.replace("\\", "/"))
            continue
        gui_touched |= "GUI" in mapped
        steps.update(_rule_steps(mapped, convert_steps))
    return _with_packaging(steps), unmatched, (gui_touched and not steps)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def _commit_lines(rev_from: str | None, commits: list[tuple[str, str]]) -> list[str]:
    """The notes' header block listing each commit."""
    count = f"{len(commits)} commit{'' if len(commits) == 1 else 's'}"
    head = f"Changes since {rev_from} ({count}):" if rev_from else f"Initial release ({count}):"
    body = [f"  {sha}  {subject}" for sha, subject in commits] or ["  (no commits)"]
    return [head, "", *body, ""]


def _empty_checklist_line(gui_only: bool, unmatched: list[str]) -> str:
    """What the checklist says when no step is owed."""
    if gui_only:
        return "  (none -- GUI-only change; relaunch the GUI, no re-run needed)"
    if unmatched:
        return "  (none matched -- see the unmapped paths below)"
    return "  (none -- no conversion code changed)"


def _step_lines(steps: list[str], gui_only: bool, unmatched: list[str],
                causes: list[str]) -> list[str]:
    """The checklist block that version.py parses back, plus why every step fired."""
    lines = ["Steps to re-run in the GUI:", ""]
    lines += [f"  [x] {step}" for step in steps]
    if not steps:
        lines.append(_empty_checklist_line(gui_only, unmatched))
    if causes:
        lines += ["", "  Every step, because of: " + ", ".join(causes)]
    return lines


def _unmatched_lines(unmatched: list[str]) -> list[str]:
    """The trailing block listing paths no rule covers, capped at 20."""
    uniq = sorted(set(unmatched))
    if not uniq:
        return []
    more = [f"  ... and {len(uniq) - 20} more"] if len(uniq) > 20 else []
    return ["", "Unmapped paths (no step selected -- judge for yourself, and "
            "add a rule in tools/release/release_notes.py):",
            *[f"  {p}" for p in uniq[:20]], *more]


def build_notes(tag: str | None, rev_from: str | None, rev_to: str) -> str:
    """The full tag message: title, commit list, step checklist, unmapped paths."""
    paths = changed_files(rev_from, rev_to)
    inert = inert_paths(paths, rev_from, rev_to)
    paths = [p for p in paths if p not in inert]
    convert_steps = convert_py_steps(rev_from, rev_to)
    steps, unmatched, gui_only = steps_for_paths(paths, convert_steps)
    lines = [f"Release {tag}" if tag else "Release notes", ""]
    lines += _commit_lines(rev_from, commits_between(rev_from, rev_to))
    lines += _step_lines(steps, gui_only, unmatched,
                         all_steps_causes(paths, convert_steps))
    lines += _unmatched_lines(unmatched)
    return "\n".join(lines) + "\n"


def main() -> int:
    """CLI entry point: print or write the notes for --from..--to."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="rev_from", default=None,
                    help="Start revision (default: latest MAJOR.MM tag)")
    ap.add_argument("--to", dest="rev_to", default="HEAD",
                    help="End revision (default: HEAD)")
    ap.add_argument("--tag", default=None,
                    help="Tag name to title the notes with")
    ap.add_argument("--output", default=None,
                    help="Write notes to this file instead of stdout")
    args = ap.parse_args()

    rev_from = args.rev_from if args.rev_from is not None else previous_tag()
    notes = build_notes(args.tag, rev_from, args.rev_to)
    if args.output:
        Path(args.output).write_text(notes, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

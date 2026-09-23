#!/usr/bin/env python
"""Build release-tag notes: commits since the previous tag plus the GUI
pipeline steps those commits require the user to re-run.

Used by .github/workflows/tag-on-push.yml to annotate each auto-tag, but it
runs standalone against any two revisions:

    python -m tools.release.release_notes                  # last tag -> HEAD
    python -m tools.release.release_notes --from 1.07 --to HEAD
    python -m tools.release.release_notes --tag 1.08       # title the notes

Run it with `-m` from the repo root: it imports version.STEP_KEYS, which a
by-path invocation cannot resolve.

STEP_ORDER is DERIVED from core/gui/config.py's STEPS and GLOBAL_ACTIONS (via
version.STEP_KEYS), so the GUI's order is the only place it is written.
The step mapping mirrors that table (the numbered checkboxes) and
the phase_* functions in convert.py that each one invokes.  Anything that
changes the plugin body (tes5_import) implies Import; mesh/creature/sound/LOD
work implies its own asset step; and because Pack BSAs / Pack Mod Zip consume
whatever the earlier steps wrote, they are appended whenever any step that
*produces* packaged output is triggered.

Keeping the answer honest means being narrow where the code is narrow:

  * convert.py is attributed per phase_* function via git's hunk headers, so a
    diff confined to phase_lod costs only the LOD step (see PHASE_STEPS).  Only
    module-scope/main()/shared-helper hunks fall back to every step.
  * Paths that are never pipeline input (docs, tests, tools, vendored binaries
    under external/, the standalone TESGameSelect/ plugin) map to no steps.
    Genuinely unrecognised paths select nothing -- they are listed verbatim so
    the reader can judge them (and add a rule), rather than blanket-ticking
    every step over one stray file.

Four RULES entries are not obvious from their pattern alone.  Every LOD module
feeds ONE step: the whole load order's LOD, plus the sibling merge, comes from
the single "Create LOD" action, so there is no per-plugin LOD step to
distinguish sibling_lod.py from.  worldmap_clouds.py is generated from BOTH
sides -- per worldspace by the import (record_types/world.py) and as a merged
union by the sibling pass.  skin_replacement.py is imported by nif_converter,
so it is a mesh change as well as a body-patch one.  skyrim_assets.py is the
vanilla-asset provider that mesh conversion, creature skeletons
(extract_skeleton_bones) and the slot-44 body patch all pull from.

The asset_convert patterns allow any folder depth: the package is organised
into subpackages, and a rule that matched only one level would drop a nested
module through to the mesh catch-all and silently mis-scope its rebuild.

Four tools under tools/ ARE global actions rather than debug utilities, and
their rules precede the blanket tools/ rule (first match wins): a change to one
alters the artefact the user installs, so it stales that action exactly as a
stage module does.  convert_ui.py is joined by asset_convert/ui/'s ui_menus,
ui_cursor and swf, which are the reskin's source; book_inam.py is deliberately
excluded, being the per-plugin book-icon step.

The core/ rules run narrow-to-broad.  worker_budget/subprocess_flags/
process_job are process-pool plumbing every worker-based stage runs through, so
they imply ALL; run_log and plugin_masters only report, producing no conversion
output, so they stale nothing.  The bare `^core/` tail of the GUI rule is the
safety net: a new module added there reads as a GUI change (which costs the
user no re-runs) instead of falling through to the unmatched bucket, which asks
for every step.

tes_runtime/ (TESRuntime.dll, HavokWorldSize, and the MorrowindRuntime
submodule beside it) is the same shape as TESGameSelect/: a committed,
prebuilt SKSE plugin no conversion phase reads, packaged on its own by
package_runtime_dll.py.  A change anywhere under it re-runs only "Package SKSE
Mod", never a per-plugin step.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from version import STEP_KEYS

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent

#: Every step label in GUI run order. See: docs/commentary/version_upgrade_planning.md#one-table-not-four
STEP_ORDER = [label for _key, label in STEP_KEYS]

# Steps that only repackage what earlier steps produced.  Added automatically
# whenever any producing step fires, never a reason to run on their own.
PACKAGING_STEPS = ["9. Pack BSAs", "10. Pack Mod Zip"]

# "Pack LOD" is the same idea for the standalone LOD mod, but it repackages ONE
# step's output rather than the per-plugin pipeline's, so it is triggered by
# that step alone instead of by anything at all being rebuilt.
LOD_PACKAGING_STEP = "Pack LOD"
LOD_PRODUCING_STEP = "Create LOD"

#: Global actions packaging their own standalone artefact, never read by 9./10.
STANDALONE_STEPS = frozenset(
    {"Patch Skyrim", "Package Start Mod", "Package SKSE Mod",
     LOD_PRODUCING_STEP, LOD_PACKAGING_STEP})

# (regex over the repo-relative path, steps it forces).  First match wins per
# rule list order, but every matching rule contributes -- a path may need
# several steps.  Patterns are matched with re.search against forward-slash
# paths.
RULES: list[tuple[str, list[str]]] = [
    # ── Stage packages ────────────────────────────────────────────────────
    (r"^tes4_export/",            ["1. Export", "6. Import"]),
    (r"^tes5_import/",            ["6. Import"]),
    (r"^script_convert/",         ["8. Scripts"]),

    (r"^asset_convert/(?:\w+/)*bsa_extract\.py",        ["2. Extract"]),
    (r"^asset_convert/(?:\w+/)*(spt_\w+|flipbook)\.py", ["4. SpeedTrees"]),
    (r"^asset_convert/(?:\w+/)*(creature_pipeline|hkx_\w+|animation_data|"
     r"extract_skeleton_bones|kf_decode|kf_writer)\.py",
                                               ["5. Creatures"]),
    # Pre-built behavior/skeleton assets shipped with the converter.
    (r"^asset_convert/generated/",             ["5. Creatures"]),
    (r"^asset_convert/(?:\w+/)*(audio_converter)\.py",  ["7. Sounds"]),
    (r"^asset_convert/(?:\w+/)*(sibling_lod|lod_gen|lod_far_gen|terrain_lod|"
     r"terrain_lod_textures|landscape_normals)\.py",
                                               ["Create LOD"]),
    (r"^asset_convert/(?:\w+/)*worldmap_clouds\.py",    ["6. Import", "Create LOD"]),
    (r"^asset_convert/(?:\w+/)*(body_slots|mesh_cut)\.py", ["3. Meshes", "Patch Skyrim"]),
    (r"^tools/creature/patch_body_slots\.py",           ["Patch Skyrim"]),
    (r"^asset_convert/(?:\w+/)*skin_replacement\.py",   ["3. Meshes", "Patch Skyrim"]),
    (r"^asset_convert/(?:\w+/)*(bsa_pack)\.py",         ["9. Pack BSAs"]),
    (r"^asset_convert/(?:\w+/)*texture_prune\.py",      ["3. Meshes"]),
    (r"^asset_convert/(?:\w+/)*skyrim_assets\.py",
                                               ["3. Meshes", "5. Creatures",
                                                "Patch Skyrim"]),
    (r"^asset_convert/ui/(?:ui_menus|ui_cursor|swf)\.py$", ["Convert UI"]),
    (r"^asset_convert/(?:\w+/)*",                       ["3. Meshes"]),

    # ── Native / shared code: conservatively wide ─────────────────────────
    # Docs and build notes shipped alongside the extension are not inputs to
    # anything.  Listed BEFORE the blanket native/ rule (first match wins), or
    # a README edit costs the user a mesh, creature AND LOD rebuild -- which is
    # exactly what 0.57 charged for `native/dist/README.md`.
    (r"^native/.*\.(md|txt)$",    []),
    (r"^native/",                 ["3. Meshes", "5. Creatures", "Create LOD"]),
    # convert.py is resolved per-phase-function instead (see PHASE_STEPS);
    # "ALL" here is only the fallback when the hunks can't be attributed.
    (r"^convert\.py$",            ["CONVERT"]),
    (r"^core/collision_options\.py$",  ["3. Meshes"]),
    (r"^core/(?:worker_budget|subprocess_flags|process_job)\.py$", ["ALL"]),
    (r"^core/(?:run_log|plugin_masters)\.py$", []),
    (r"^gui\.py$|^gui\.pyw$|^core/gui/|^core/", ["GUI"]),

    # ── Non-pipeline: never a reason to re-run anything ───────────────────
    (r"^docs/",                   []),
    (r"^tests/",                  []),
    (r"^tools/release/create_lod\.py$",       ["Create LOD"]),
    (r"^tools/release/pack_lod\.py$",         ["Pack LOD"]),
    (r"^tools/release/package_start_mod\.py$", ["Package Start Mod"]),
    (r"^tools/release/package_runtime_dll\.py$", ["Package SKSE Mod"]),
    (r"^tools/misc/convert_ui\.py$",          ["Convert UI"]),
    (r"^tools/",                  []),
    (r"^references/",             []),
    (r"^external/",               []),
    (r"^\.github/",               []),
    (r"^\.claude/|^\.vscode/",    []),
    # The standalone starter plugin: committed prebuilt, not generated by the
    # pipeline. Changing it makes the shipped zip stale, so it re-runs the
    # packaging action and nothing else -- no per-plugin step reads it.
    (r"^TESGameSelect/",          ["Package Start Mod"]),
    (r"^tes_runtime/",            ["Package SKSE Mod"]),
    # Dependency preflight: gates the run before any phase starts and produces
    # no conversion output of its own, so a change here re-runs nothing.
    (r"^preflight\.py$",          []),
    # Version identity and the upgrade shortcut itself.  It reports what is
    # stale; it never converts anything, so it cannot make output stale.  VERSION
    # is an export-subst template expanded at archive time -- its content is the
    # release number, which likewise changes no output.
    (r"^version\.py$|^VERSION$",  []),
    (r"^CLAUDE\.md$|^README\.md$|^TODO\.txt$|^CK_WARNINGS", []),
    (r"^conversion_config\.json$|^pyproject\.toml$|^\.git\w+$", []),
    (r"^[^/]+\.code-workspace$", []),
]

# convert.py hosts one phase_* function per GUI step.  A change inside exactly
# one of them implies only that step -- historically the blanket "ALL" here was
# the single biggest source of "re-run everything" noise (e.g. 0.40, whose
# convert.py diff was entirely inside phase_lod).
# convert.py functions that ORCHESTRATE phases without producing output.
# A hunk in one of these narrows to nothing rather than falling back to every
# step: main() parses arguments and dispatches, so editing it changes which
# phases the user can ask for, never what a phase writes.  `_mark` just records
# that a step completed (version.record_step_run) and is likewise output-inert.
ORCHESTRATION_FUNCS = frozenset({"main", "_mark"})

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
    # phase_lod was deleted when LOD stopped being per-plugin work; the entry
    # stays so a diff against an older revision still attributes correctly.
    "phase_lod":                ["Create LOD"],
    "phase_modify_body_meshes": ["Patch Skyrim"],
    "phase_pack":               ["9. Pack BSAs"],
    "phase_pack_zip":           ["10. Pack Mod Zip"],
}


def _run(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], cwd=SCRIPT_DIR, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def previous_tag(before: str = "HEAD") -> str | None:
    """Latest release tag reachable from `before`, matching the workflow's own
    tag scheme.  None when the repo has no release tag yet.

    Tags through 0.58 are MAJOR.MM (hundredths); 0.581 onward are MAJOR.MMM
    (thousandths).  Both forms must be globbed AND ranked on a common scale:
    comparing the minor fields as bare ints would sort 0.59 above 0.580, and
    globbing only two digits would pin the notes to 0.58 forever.
    """
    try:
        tags = _run(["tag", "-l", "[0-9]*.[0-9][0-9]",
                     "[0-9]*.[0-9][0-9][0-9]"]).splitlines()
    except subprocess.CalledProcessError:
        return None
    tags = [t.strip() for t in tags if t.strip()]
    if not tags:
        return None

    def key(t: str) -> tuple[int, int]:
        major, _, minor = t.partition(".")
        try:
            # Scale by width so both schemes compare in thousandths.
            scale = 10 if len(minor) == 2 else 1
            return (int(major), int(minor) * scale)
        except ValueError:
            return (-1, -1)

    return sorted(tags, key=key)[-1]


def commits_between(rev_from: str | None, rev_to: str) -> list[tuple[str, str]]:
    """[(short_sha, subject)] oldest-first for rev_from..rev_to."""
    rng = f"{rev_from}..{rev_to}" if rev_from else rev_to
    out = _run(["log", "--reverse", "--no-merges", "--format=%h%x1f%s", rng])
    rows = []
    for line in out.splitlines():
        if "\x1f" in line:
            sha, _, subject = line.partition("\x1f")
            rows.append((sha, subject))
    return rows


def changed_files(rev_from: str | None, rev_to: str) -> list[str]:
    if rev_from:
        out = _run(["diff", "--name-only", f"{rev_from}..{rev_to}"])
    else:
        out = _run(["ls-tree", "-r", "--name-only", rev_to])
    return [p for p in out.splitlines() if p.strip()]


_HUNK_FUNC = re.compile(r"^@@ .*? @@\s*(?:def\s+)?([A-Za-z_]\w*)")


def convert_py_steps(rev_from: str | None, rev_to: str) -> list[str] | None:
    """Steps implied by a convert.py change, resolved per phase_* function.

    Git's hunk headers name the enclosing function, so a diff confined to
    phase_lod costs only the LOD step.  Returns None when the change can't be
    attributed -- shared helpers, main(), module scope, or a brand-new file --
    in which case the caller falls back to every step.
    """
    if not rev_from:
        return None
    try:
        diff = _run(["diff", "-U0", f"{rev_from}..{rev_to}", "--", "convert.py"])
    except subprocess.CalledProcessError:
        return None

    steps: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("@@"):
            continue
        m = _HUNK_FUNC.match(line)
        if not m:
            # Hunk outside any function (imports, constants) -- affects
            # everything, so don't narrow.
            return None
        func = m.group(1)
        if func in ORCHESTRATION_FUNCS:
            # main() is argument parsing and phase dispatch.  It decides WHICH
            # phases run, never what any of them produces, so a change confined
            # to it costs no re-conversion.
            #
            # It used to fall through to "cannot attribute" -> every step, which
            # made any CLI-plumbing commit (a new flag, the per-step state
            # recording) demand a full multi-hour reconversion.  Measured on
            # 0.58..0.581: all 16 hunks were in main(), and the release asked
            # for all twelve steps when only Meshes and Scripts had changed.
            continue
        mapped = PHASE_STEPS.get(func)
        if mapped is None:
            # A shared helper or an unknown function -- genuinely unattributable.
            return None
        steps.update(mapped)

    # An EMPTY set is a real answer here, not a failure: it means every hunk
    # was orchestration, which costs nothing to re-run.  Returning None for it
    # would resurrect the all-twelve-steps bug this function exists to avoid.
    return sorted(steps)


def steps_for_paths(paths: list[str],
                    convert_steps: list[str] | None = None,
                    ) -> tuple[list[str], list[str], bool]:
    """→ (ordered steps to re-run, paths no rule matched, gui_only_change).

    `convert_steps` is the per-phase attribution of a convert.py change from
    `convert_py_steps`; None means "couldn't narrow it", i.e. every step.

    `gui_only_change` is True when the GUI itself changed but nothing that
    alters conversion output did -- the user needs a fresh GUI, not a re-run.
    Unrecognised paths select no steps and are returned separately.
    """
    steps: set[str] = set()
    unmatched: list[str] = []
    gui_touched = False
    run_all = False

    for path in paths:
        p = path.replace("\\", "/")
        matched = False
        for pattern, mapped in RULES:
            if re.search(pattern, p):
                matched = True
                if "ALL" in mapped:
                    run_all = True
                elif "GUI" in mapped:
                    gui_touched = True
                elif "CONVERT" in mapped:
                    if convert_steps is None:
                        run_all = True
                    else:
                        steps.update(convert_steps)
                else:
                    steps.update(mapped)
                break
        if not matched:
            unmatched.append(p)

    if run_all:
        steps.update(STEP_ORDER)

    if steps - STANDALONE_STEPS:
        steps.update(PACKAGING_STEPS)

    if LOD_PRODUCING_STEP in steps:
        steps.add(LOD_PACKAGING_STEP)

    ordered = [s for s in STEP_ORDER if s in steps]
    return ordered, unmatched, (gui_touched and not steps)


def build_notes(tag: str | None, rev_from: str | None, rev_to: str) -> str:
    commits = commits_between(rev_from, rev_to)
    paths = changed_files(rev_from, rev_to)
    steps, unmatched, gui_only = steps_for_paths(
        paths, convert_py_steps(rev_from, rev_to))

    lines: list[str] = []
    lines.append(f"Release {tag}" if tag else "Release notes")
    lines.append("")

    if rev_from:
        lines.append(f"Changes since {rev_from} ({len(commits)} commit"
                     f"{'' if len(commits) == 1 else 's'}):")
    else:
        lines.append(f"Initial release ({len(commits)} commits):")
    lines.append("")
    for sha, subject in commits:
        lines.append(f"  {sha}  {subject}")
    if not commits:
        lines.append("  (no commits)")
    lines.append("")

    lines.append("Steps to re-run in the GUI:")
    lines.append("")
    if steps:
        for step in steps:
            lines.append(f"  [x] {step}")
    elif gui_only:
        lines.append("  (none -- GUI-only change; relaunch the GUI, no re-run needed)")
    elif unmatched:
        lines.append("  (none matched -- see the unmapped paths below)")
    else:
        lines.append("  (none -- no conversion code changed)")

    if unmatched:
        uniq = sorted(set(unmatched))
        lines.append("")
        lines.append("Unmapped paths (no step selected -- judge for yourself, and "
                     "add a rule in tools/release/release_notes.py):")
        for p in uniq[:20]:
            lines.append(f"  {p}")
        if len(uniq) > 20:
            lines.append(f"  ... and {len(uniq) - 20} more")

    return "\n".join(lines) + "\n"


def main() -> int:
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

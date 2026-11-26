"""convert.py's command line: the argument parser and which steps a run selects.

Split out of convert.py.  Nothing here runs a phase.
"""

import argparse

from core.collision_options import WINDING_FIX_DEFAULT_PLUGINS
from core.navmesh_options import DEFAULT_GENERATOR, GENERATORS
from tes4_export import text_encoding
from tes4_export.export_morrowind import (MORROWIND_SOURCE_KEY, SOURCE_HYBRID,
                                          SOURCE_MORROBLIVION, SOURCE_VANILLA)

#: Pipeline steps in run order, with the `--*-only` flag that selects each alone.
STEP_FLAGS = (
    ('export', 'export_only'),
    ('extract', 'extract_only'),
    ('meshes', 'meshes_only'),
    ('speedtrees', 'speedtrees_only'),
    ('creatures', 'creatures_only'),
    ('import', 'import_only'),
    ('sounds', 'sounds_only'),
    ('scripts', 'scripts_only'),
    ('lod', 'lod_only'),
    ('skyrim_patch', 'modify_body_meshes'),
    ('pack_bsa', 'pack_only'),
    ('pack_zip', 'pack_zip_only'),
)

#: Steps the default run (no `--*-only`) leaves out.
NOT_DEFAULT = frozenset({'lod', 'pack_zip'})

#: What `--build-morrowind-patch` runs over the patch: the steps it has content for.
PATCH_STEPS = ('export', 'meshes', 'creatures', 'import', 'sounds', 'scripts')

#: Steps that honor `--only`; any other step with it would silently rebuild everything.
SCOPED_STEPS = frozenset({'creatures'})

#: Steps that write files the plugin's archives hold; a run with any of them ends by packing.
PACKED_OUTPUT_STEPS = frozenset({'meshes', 'speedtrees', 'creatures', 'import', 'sounds', 'scripts'})

#: (flag, help) for every `--*-only` step flag.
_ONLY_FLAGS = (
    ("--export-only", "Parse TES4 binary -> key/value text cache"),
    ("--import-only", "Convert text cache -> TES5 binary ESM/ESP"),
    ("--extract-only", "Extract BSA archives into export/<name>/"),
    ("--meshes-only", "Convert NIFs and copy textures only"),
    ("--speedtrees-only", "Convert SPT (SpeedTree) files only"),
    ("--creatures-only", "Convert creatures (behavior projects, "
                         "skeleton/body meshes, animation registration)"),
    ("--sounds-only", "Copy extracted sound files to output"),
    ("--lod-only", "Generate object & terrain LOD meshes"),
    ("--modify-body-meshes", "Write the body-slot patch over a Skyrim load order"),
    ("--scripts-only", "Convert TES4 scripts to Papyrus .psc source"),
    ("--pack-only", "Pack output assets into Skyrim SE BSA archives"),
    ("--pack-zip-only", "Zip converted plugin/BSA files for distribution"),
)


def selected_steps(args) -> list:
    """The steps this run executes, in run order; any `PACKED_OUTPUT_STEPS` step brings the pack.

    See: docs/commentary/asset_convert_bsa.md#archive-only-output
    """
    if args.build_morrowind_patch:
        chosen = set(PATCH_STEPS)
    else:
        chosen = ({step for step, flag in STEP_FLAGS if getattr(args, flag)}
                  or {step for step, _flag in STEP_FLAGS if step not in NOT_DEFAULT})
    if chosen & PACKED_OUTPUT_STEPS:
        chosen.add('pack_bsa')
    return [step for step, _flag in STEP_FLAGS if step in chosen]


def unscoped_steps(args, steps: list) -> list:
    """The selected steps `--only` cannot narrow; empty when it was not given. The pack is incremental."""
    return [step for step in steps if step not in SCOPED_STEPS | {'pack_bsa'}] if args.only else []


def confirm_mode_change(args, config: dict) -> None:
    """Stop the run unless a --morrowind-source that differs from the configured mode is confirmed.

    Confirmation is `--confirm-mode-change` or a `y` at the prompt; no answer (no console) refuses.
    See: docs/reference/pipeline.md#morrowind-mode-guard
    """
    current = str(config.get(MORROWIND_SOURCE_KEY) or SOURCE_VANILLA).strip().lower()
    if args.morrowind_source == current or args.confirm_mode_change:
        return
    print(f"NOTICE: the configured Morrowind source is '{current}', but this run asks for "
          f"'{args.morrowind_source}'.\nBuilding in another mode rewrites export/ and output/ "
          f"for that mode and removes what only '{current}' builds (such as the Hybrid.esp).")
    try:
        answer = input(f"Really build in '{args.morrowind_source}' mode? [y/N] ")
    except (EOFError, OSError):
        answer = ''
    if answer.strip().lower() not in ('y', 'yes'):
        raise SystemExit("Stopped: not confirmed. Drop --morrowind-source to build in the "
                         "configured mode, or pass --confirm-mode-change if you are sure.")


def apply_config_overrides(args, config: dict) -> None:
    """Let this run's flags override conversion_config.json, without saving it,
    and hand the source text encoding setting to every stage."""
    if args.no_engine_branches:
        config["speedtreeEngineBranches"] = False
    if args.keep_loose:
        config["deleteLooseAfterPack"] = False
    if args.morrowind_source:
        confirm_mode_change(args, config)
        config[MORROWIND_SOURCE_KEY] = args.morrowind_source
    if args.source_encoding:
        config[text_encoding.CONFIG_KEY] = args.source_encoding
    text_encoding.configure(config.get(text_encoding.CONFIG_KEY))


def build_parser() -> argparse.ArgumentParser:
    """The full convert.py argument parser."""
    parser = argparse.ArgumentParser(
        description="TES4-to-TES5 Conversion Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Default pipeline (no --*-only): export + import + extract + assets\n"
            "Each --*-only flag runs exactly that step, then packs the BSAs\n"
            "when the step writes files they hold."
        ),
    )
    _add_run_args(parser)
    for flag, text in _ONLY_FLAGS:
        parser.add_argument(flag, action="store_true", help=text)
    _add_mod_args(parser)
    _add_mesh_args(parser)
    parser.add_argument("--navmesh-generator", choices=GENERATORS, default=None,
                        help="Navmesh generator for the import stage. Default: "
                             + DEFAULT_GENERATOR)
    parser.add_argument("--navmesh-pins", metavar="DIR", default=None,
                        help="Folder of your own navmesh pins, read over the "
                             "shipped navmesh_pins/ (the navmesh editor's save "
                             "location)")
    return parser


def _add_run_args(parser) -> None:
    """Which plugins, from where, and into where."""
    parser.add_argument("-f", "--files", nargs="+", metavar="FILE",
                        help="Plugin filename(s) to process (default: all from config)")
    parser.add_argument("--config", metavar="PATH",
                        help="Path to conversion_config.json")
    parser.add_argument("--data-dir", metavar="PATH",
                        help="Data folder to read plugins from (overrides "
                             "tes4DataPath); picks which copy of a same-named "
                             "plugin converts")
    parser.add_argument("--output-dir", metavar="PATH",
                        help="Output directory (default: output/ in project root)")
    parser.add_argument("--keep-loose", action="store_true",
                        help="After packing, keep the loose copies of the packed "
                             "files (default: deleted, unless "
                             "\"deleteLooseAfterPack\": false in the config)")
    parser.add_argument("--no-engine-branches", action="store_true",
                        help="Force the pure-Python SpeedTree generator. "
                             "Engine branches (from the game's own code) are "
                             "the DEFAULT and already fall back to Python per "
                             "tree when no Oblivion.exe is configured or the "
                             "native harness is missing.")
    parser.add_argument("--morrowind-source",
                        choices=(SOURCE_VANILLA, SOURCE_MORROBLIVION, SOURCE_HYBRID),
                        help="Export Morrowind-engine plugins in this mode for "
                             "this run only, instead of the configured "
                             "Settings > Morrowind source (which is left "
                             "unchanged). A mode other than the configured "
                             "one asks for confirmation first.")
    parser.add_argument("--confirm-mode-change", action="store_true",
                        help="Answer yes to the --morrowind-source confirmation.")
    parser.add_argument("--source-encoding",
                        choices=(text_encoding.AUTO,) + text_encoding.CODECS,
                        help="Code page the source plugins' text is in, for "
                             "this run only: cp1252 Western, cp1251 Russian, "
                             "cp1250 Central European. Default auto: cp1251 "
                             "or cp1252, read from each plugin's own text.")
    parser.add_argument("--only", nargs="+", metavar="NAME",
                        help="Scope the stage to these units instead of "
                             "rebuilding all of them. Honored by "
                             "--creatures-only: creature folder names "
                             "(e.g. rat mudcrab). Any other stage refuses it.")
    parser.add_argument("--patch-plugins", nargs="+", metavar="PLUGIN",
                        help="Skyrim plugin filenames to generate a slot-44 "
                             "patch for (e.g. Skyrim.esm Dawnguard.esm). "
                             "Default: Skyrim.esm only.")


def _add_mod_args(parser) -> None:
    """Source management: importing, listing and removing mods."""
    parser.add_argument("--import-mod", metavar="ARCHIVE", nargs="+",
                        help="Import a mod archive (.zip/.7z/.rar) or an "
                             "already-extracted mod folder as a conversion "
                             "source, then exit")
    parser.add_argument("--fresh", action="store_true",
                        help="With several --import-mod sources: clear the "
                             "target asset tree first. A merge is defined "
                             "by its FULL source list, so re-importing a "
                             "different list without this leaves the "
                             "dropped mod's files behind and the index "
                             "reports something that is no longer true.")
    parser.add_argument("--base", nargs="+", metavar="PLUGIN",
                        help="With --import-mod: the plugin(s) this mod "
                             "builds on (e.g. Nehrim.esm), so its meshes "
                             "can resolve textures it does not ship.")
    parser.add_argument("--as", dest="merge_as", metavar="NAME",
                        help="With several --import-mod sources: the name "
                             "of the merged asset tree. Required for a "
                             "multi-source import.")
    parser.add_argument("--plugin-member", nargs="+", metavar="PATH",
                        help="With --import-mod: which plugin(s) inside the "
                             "archive to register (default: all found)")
    parser.add_argument("--no-keep-archive", action="store_true",
                        help="With --import-mod: do not retain a copy of the "
                             "archive (re-importing then needs the original)")
    parser.add_argument("--list-mods", action="store_true",
                        help="List Data folders, same-named plugin copies and "
                             "imported mod archives, then exit")
    parser.add_argument("--build-morrowind-patch", metavar="DATA_FILES",
                        help="Register a Morrowind 'Data Files' folder and "
                             "build the Morroblivion compatibility patch from "
                             "it (export, meshes, creatures, import, sounds, "
                             "scripts). "
                             "Each step also runs alone: -f "
                             "Morrowind-Morroblivion-Compatibility.esp "
                             "--<step>-only")
    parser.add_argument("--remove-mod", metavar="PLUGIN",
                        help="Remove an imported mod (deletes its export "
                             "folder and registry entry), then exit")


def _add_mesh_args(parser) -> None:
    """Mesh-stage options: subfolder filter, collision winding, parallax.

    Winding is tri-state: unspecified defers to the per-plugin default in
    collision_options.  Parallax is opt-in because a correct parallax shape
    renders wrong under vanilla SSE.
    """
    parser.add_argument("--mesh-subdirs", nargs="+", metavar="SUBDIR",
                        help="Limit mesh conversion to these folders or meshes "
                             "under meshes/ (e.g. architecture tr/l). Default: all.")
    parser.add_argument("--skip-hair", action="store_true",
                        help="Skip the hair baking pass after mesh conversion.")
    winding = parser.add_mutually_exclusive_group()
    winding.add_argument("--collision-winding-fix", dest="collision_winding_fix",
                         action="store_true", default=None,
                         help="Also INFER collision winding from adjacency, "
                              "enclosed volume and the render mesh, on top of "
                              "the always-on authored-normal repair. Guesses, "
                              "so it can invert correct geometry -- only for "
                              "plugins whose exporter destroyed the normals. "
                              "Default: on only for "
                              + ", ".join(sorted(WINDING_FIX_DEFAULT_PLUGINS)))
    winding.add_argument("--no-collision-winding-fix", dest="collision_winding_fix",
                         action="store_false", default=None,
                         help="Disable the inferred winding steps (the "
                              "authored-normal repair still runs).")
    parser.add_argument("--parallax", action="store_true",
                        help="Carry Oblivion's parallax across as Skyrim "
                             "height maps. REQUIRES Community Shaders or ENB "
                             "in the player's setup -- under vanilla SSE the "
                             "affected surfaces render wrong. Off by default.")
    parser.add_argument("--textures-only", action="store_true",
                        help="Mesh stage: read and analyse every NIF but write "
                             "none. Ships textures only (with their _p height "
                             "maps), for use with PGPatcher. Pair with "
                             "--parallax.")

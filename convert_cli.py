"""convert.py's command line: the argument parser and which steps a run selects.

Split out of convert.py.  Nothing here runs a phase.
"""

import argparse

from core.collision_options import WINDING_FIX_DEFAULT_PLUGINS

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
NOT_DEFAULT = frozenset({'pack_zip'})

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
    """The steps this run executes, in run order."""
    only = [step for step, flag in STEP_FLAGS if getattr(args, flag)]
    return only or [step for step, _flag in STEP_FLAGS if step not in NOT_DEFAULT]


def build_parser() -> argparse.ArgumentParser:
    """The full convert.py argument parser."""
    parser = argparse.ArgumentParser(
        description="TES4-to-TES5 Conversion Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Default pipeline (no --*-only): export + import + extract + assets\n"
            "Each --*-only flag runs exactly that step and nothing else."
        ),
    )
    _add_run_args(parser)
    for flag, text in _ONLY_FLAGS:
        parser.add_argument(flag, action="store_true", help=text)
    _add_mod_args(parser)
    _add_mesh_args(parser)
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
    parser.add_argument("--no-engine-branches", action="store_true",
                        help="Force the pure-Python SpeedTree generator. "
                             "Engine branches (from the game's own code) are "
                             "the DEFAULT and already fall back to Python per "
                             "tree when no Oblivion.exe is configured or the "
                             "native harness is missing.")
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
                        help="Build the Morroblivion compatibility patch from "
                             "a Morrowind 'Data Files' folder, then exit")
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

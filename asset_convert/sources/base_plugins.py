r"""Which plugins does an export tree build on?

An imported mod ships only what it changes; everything else -- textures its
meshes reference, and the ARMO/CLOT records that say which meshes are worn --
lives in the base game's export. A tree that cannot name its base is blind to
both, and the two blindnesses look identical from the outside: a shape simply
gets the conservative default.

Two carriers, because a mod declares its base in two different ways:

  * a PLUGIN mod names its masters in the export's `_HEADER.txt`, the same
    line `convert_speedtrees` reads to find a master's `.spt` files;
  * an ASSET-ONLY mod has no plugin and therefore no header, so the base is
    recorded at import time -- `--import-mod ... --base Nehrim.esm`, or
    implicitly by seeding the base into an ordered merge.
"""
import os

FILE_NAME = '.base_plugins'


def names_for(own_dir):
    """Base plugin names for the export tree at `own_dir`, nearest first.

    Load order reversed: the header's `Master[i]` lines, then the recorded
    bases (`--base` is load order too), last first -- a later master
    overrides an earlier one, so a first-match lookup must meet it first.
    `--base` lives in the MOD's `_source/`, one level above a nested record dir.
    A nested MOD folder unions its plugins' names (`record_dirs_for_assets`).
    See: docs/commentary/tes5_import_mod_merge.md#base-order-nearest-first
    """
    from output_layout import record_dirs_for_assets
    own_dir = str(own_dir)
    recs = [str(d) for d in record_dirs_for_assets(own_dir)]
    if recs != [os.path.normpath(own_dir)]:
        names = []
        for rec in recs:
            names += [n for n in _own_names(rec) if n not in names]
        return names
    return _own_names(own_dir)


def _own_names(own_dir):
    """`names_for` for one RECORD dir (or a flat plugin's folder)."""
    from output_layout import assets_for
    header = [line.partition('=')[2].strip()
              for line in _lines(os.path.join(own_dir, '_HEADER.txt'))
              if line.startswith('Master[')]
    recorded = []
    for d in dict.fromkeys((own_dir, str(assets_for(own_dir)))):
        recorded += [line.strip()
                     for line in _lines(os.path.join(d, '_source', FILE_NAME))]
    names = []
    for n in header + recorded:
        if n and n not in names:
            names.append(n)
    return names[::-1]


def _lines(path):
    """The lines of text file `path`; [] when it does not exist."""
    if not os.path.isfile(path):
        return []
    with open(path, encoding='utf-8', errors='replace') as fh:
        return fh.readlines()


def export_dirs(own_dir):
    """The bases' RECORD folders that exist, nearest first.

    Resolved through `record_dir` against `export_root_of`, never against
    `dirname(own_dir)`, which is the MOD folder for a nested plugin.
    See: docs/commentary/tes5_import_mod_merge.md#export-root-resolution

    `output_layout` is imported here, not at module scope: it reaches back into
    this package for `source_registry`, so a top-level import is a cycle.
    """
    from output_layout import export_root_of, record_dir
    own_dir = os.path.abspath(str(own_dir))
    export_root = str(export_root_of(own_dir))
    out = []
    for n in names_for(own_dir):
        p = str(record_dir(export_root, n))
        if os.path.isdir(p) and p not in out:
            out.append(p)
    return out


def asset_dirs(own_dir):
    """The bases' ASSET folders (meshes/, textures/), nearest first.

    A nested base keeps its records one level below its assets, so a caller
    joining 'meshes' onto an `export_dirs` entry looks in a folder that does
    not exist. Read assets from these instead.
    """
    from output_layout import assets_for
    out = []
    for d in export_dirs(own_dir):
        p = str(assets_for(d))
        if p not in out:
            out.append(p)
    return out


def record_chain_for_assets(asset_dir):
    """Every RECORD folder serving the assets in `asset_dir`, nearest first.

    The plugins' own (`output_layout.record_dirs_for_assets`: the folder itself
    for a flat plugin, each plugin record dir of a nested mod), then their
    bases (`export_dirs`). A first-match reader lets the own record win.
    See: docs/commentary/tes5_import_mod_merge.md#export-root-resolution
    """
    from output_layout import record_dirs_for_assets
    out = []
    own = [os.path.abspath(str(r)) for r in record_dirs_for_assets(asset_dir)]
    for d in own + export_dirs(asset_dir):
        if d not in out:
            out.append(d)
    return out


def subdirs(own_dir, sub):
    """`asset_dirs` narrowed to an existing subfolder (e.g. 'textures')."""
    out = []
    for d in asset_dirs(own_dir):
        p = os.path.join(d, sub)
        if os.path.isdir(p):
            out.append(p)
    return tuple(out)

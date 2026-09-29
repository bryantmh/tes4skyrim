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
    See: docs/commentary/tes5_import_mod_merge.md#base-order-nearest-first
    """
    own_dir = str(own_dir)
    header = [line.partition('=')[2].strip()
              for line in _lines(os.path.join(own_dir, '_HEADER.txt'))
              if line.startswith('Master[')]
    recorded = [line.strip()
                for line in _lines(os.path.join(own_dir, '_source', FILE_NAME))]
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
    """Sibling export trees for the bases of `own_dir` that exist, nearest first.

    Resolved through `record_dir`: an imported mod's plugins share ONE folder
    named for the MOD, so joining the master's own name onto the export root
    misses it and the base is silently lost (`Tamriel_Data.esm` lives in
    `Tamriel Data (HD)`).

    `output_layout` is imported here, not at module scope: it reaches back into
    this package for `source_registry`, so a top-level import is a cycle.
    """
    from output_layout import record_dir
    own_dir = os.path.abspath(str(own_dir))
    export_root = os.path.dirname(own_dir)
    out = []
    for n in names_for(own_dir):
        p = str(record_dir(export_root, n))
        if os.path.isdir(p) and p not in out:
            out.append(p)
    return out


def subdirs(own_dir, sub):
    """`export_dirs` narrowed to an existing subfolder (e.g. 'textures')."""
    out = []
    for d in export_dirs(own_dir):
        p = os.path.join(d, sub)
        if os.path.isdir(p):
            out.append(p)
    return tuple(out)

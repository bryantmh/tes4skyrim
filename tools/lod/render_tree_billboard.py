"""CLI for rendering Oblivion-style tree billboards in bulk.

The rendering itself lives in `asset_convert/lod/tree_billboard.py`, because the
LOD pipeline calls it directly: `lod_far_gen._far_nif_worker` renders a missing
billboard on the spot so no tree ever reaches the geometry simplifier.  This
wrapper exists for ad-hoc runs — filling in a whole load order up front, or
re-rendering after changing the shading constants.  Renders land in the LOD
mod's textures, never a plugin's.
See: docs/commentary/asset_convert_terrain.md#the-bake-never-writes-a-plugin-folder

Usage:
    python -m tools.lod.render_tree_billboard --all [--workers N] [--dry-run]
    python -m tools.lod.render_tree_billboard --plugin Oblivion.esm [--size 512]
    python -m tools.lod.render_tree_billboard --nif <tree.nif> --out <name.dds>
"""

import argparse
import multiprocessing as mp
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.subprocess_flags import configure_multiprocessing
from core.worker_budget import worker_count
from asset_convert.game_paths import win_join
from asset_convert.lod.esm_scan import parse_esm
from asset_convert.lod.lod_far_gen import is_tree_model
from asset_convert.lod.sibling_lod import LOD_DIR_NAME
from asset_convert.lod.tree_billboard import (billboard_dir, BS, render_billboard,
                                          write_dds_rgba)

configure_multiprocessing()


def billboard_dst(out_root: Path, stem: str) -> Path:
    """Where a render is written: the LOD mod's textures, never a plugin's."""
    return win_join(out_root / LOD_DIR_NAME / 'textures',
                    billboard_dir() + BS + stem + '.dds')


def tree_models(plugin_dirs):
    """{model_rel: owning output dir} for every placed TREE-type base.

    Every plugin in a directory is read, not just the first: a directory can
    hold several and the TREE bases are spread across them.  An unreadable
    plugin is reported on stderr rather than swallowed -- skipping in silence
    renders nothing and still exits 0.
    """
    found = {}
    for base in plugin_dirs:
        for esm in sorted(list(base.glob('*.esm')) + list(base.glob('*.esp'))):
            try:
                _ws, _cells, stats, _refs = parse_esm(esm)
            except (OSError, ValueError, KeyError, struct.error) as exc:
                print('  SKIP %s: %s: %s' % (esm.name, type(exc).__name__, exc),
                      file=sys.stderr)
                continue
            for _fid, st in stats.items():
                if not is_tree_model(st):
                    continue
                m = st.get('model', '')
                if m:
                    found.setdefault(m.lower(), base)
    return found


def has_billboard(stem, tex_roots):
    bare = stem.lstrip('0123456789') or stem
    for cand in {stem, bare}:
        for t in tex_roots:
            if win_join(t, billboard_dir() + BS + cand + '.dds').exists():
                return True
    return False


def _render_one(task):
    """Pool worker: (src, dst, tex_roots, size) -> (ok, stem, note)."""
    src, dst, tex_roots, size = task
    stem = Path(dst).stem
    try:
        img = render_billboard(Path(src), [Path(t) for t in tex_roots], size)
    except Exception as exc:
        return False, stem, repr(exc)[:90]
    if img is None:
        return False, stem, 'empty render'
    try:
        write_dds_rgba(img, Path(dst))
    except Exception as exc:
        return False, stem, repr(exc)[:90]
    return True, stem, ''


def main():
    ap = argparse.ArgumentParser(description='Render tree billboard textures.')
    ap.add_argument('--nif')
    ap.add_argument('--out')
    ap.add_argument('--plugin', action='append',
                    help='output/<plugin> to process; repeatable')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--size', type=int, default=512)
    ap.add_argument('--workers', type=int, default=None)
    ap.add_argument('--force', action='store_true',
                    help='re-render even when a billboard already exists')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--output-dir', default='output')
    a = ap.parse_args()

    out_root = Path(a.output_dir)
    tex_roots = []
    if out_root.is_dir():
        tex_roots = [b / 'textures' for b in sorted(out_root.iterdir())
                     if (b / 'textures').is_dir()]

    if a.nif:
        img = render_billboard(Path(a.nif), tex_roots, a.size)
        if img is None:
            print('nothing rendered')
            return 1
        dst = Path(a.out or (Path(a.nif).stem + '.dds'))
        write_dds_rgba(img, dst)
        print('wrote %s (%dx%d)' % (dst, img.size[0], img.size[1]))
        return 0

    if a.plugin:
        dirs = [out_root / p for p in a.plugin]
    elif a.all:
        dirs = [d for d in sorted(out_root.iterdir())
                if d.is_dir() and d.name.lower() != 'autoconvertlod']
    else:
        ap.error('need --nif, --plugin or --all')
    dirs = [d for d in dirs if d.is_dir()]

    models = tree_models(dirs)
    print('TREE models placed: %d' % len(models), flush=True)

    tasks = []
    skipped = nomesh = 0
    for rel, base in sorted(models.items()):
        stem = os.path.splitext(os.path.basename(rel.replace(BS, '/')))[0]
        if not a.force and has_billboard(stem, tex_roots):
            skipped += 1
            continue
        src = win_join(base / 'meshes', rel)
        if not src.exists():
            nomesh += 1
            continue
        dst = billboard_dst(out_root, stem)
        tasks.append((str(src), str(dst), [str(t) for t in tex_roots], a.size))

    print('to render: %d   already had: %d   no mesh shipped: %d'
          % (len(tasks), skipped, nomesh), flush=True)
    if a.dry_run or not tasks:
        return 0

    # Rasterising is pure-Python per triangle (~1 s a tree), so this is CPU
    # bound and parallelises cleanly — the same pool pattern the rest of the
    # asset pipeline uses.
    workers = a.workers or worker_count()
    workers = max(1, min(workers, len(tasks)))
    made = failed = 0
    print('rendering with %d worker(s)...' % workers, flush=True)
    if workers == 1:
        results = (_render_one(t) for t in tasks)
        for ok, stem, note in results:
            made, failed = (made + 1, failed) if ok else (made, failed + 1)
            if not ok:
                print('  FAIL %s: %s' % (stem, note), flush=True)
    else:
        with mp.Pool(processes=workers) as pool:
            for ok, stem, note in pool.imap_unordered(_render_one, tasks,
                                                      chunksize=2):
                if ok:
                    made += 1
                    if made % 25 == 0:
                        print('  ...%d/%d' % (made, len(tasks)), flush=True)
                else:
                    failed += 1
                    print('  FAIL %s: %s' % (stem, note), flush=True)
    print('rendered %d, already had %d, no mesh %d, failed %d'
          % (made, skipped, nomesh, failed))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""
A hybrid plugin's borrowed Morroblivion files, converted into the shared `Morroblivion Hybrid Assets` mod.

Runs in the plugin's Meshes step, after its own meshes. A borrowed mesh is
converted straight from its owner's export tree, a texture copied and
repaired, and a split half generated into the shared cache folder and
converted from there; nothing is written under any plugin's export tree.
A file already converted from the same source with the same plan is skipped.

See: docs/commentary/tes4_export_morrowind.md#hybrid-shared-assets
"""

import json
import os
import shutil
from pathlib import Path

from asset_convert.character.first_person import first_person_path
from asset_convert.character.hand_pairs import split_models
from asset_convert.game_paths import ascii_dest, current_namespace, namespace_for, set_namespace
from asset_convert.sources import output_files
from asset_convert.nif import nif_batch
from asset_convert.sources.hybrid_assets import (MOD_NAME, NAMESPACE, SPLIT_OWNER, cache_dir, read_borrowed,
                                                 read_lods, read_splits)
from asset_convert.texture.texture_repair import repair_texture_tree
from core.plugin_masters import export_root
from output_layout import asset_root
from version import current_version

#: What each borrowed file was last converted from, in the shared cache folder.
STAMPS = 'converted.json'

#: The shared cache's folder of generated split halves (a mesh tree).
SPLIT_TREE = 'split_meshes'

#: The shared cache's folder of LOD meshes staged under their vanilla names (a mesh tree).
LOD_TREE = 'lod_meshes'


def build_borrowed(rec_dir, out_root, plugin_dir, plan: dict, parallax: bool, textures_only: bool) -> dict:
    """Convert what `rec_dir`'s plugin borrows into the shared mod; {'meshes': n, 'textures': n}, {} if none.

    An older build's converted copy of a borrowed file in the plugin's own output (`plugin_dir`) is dropped.
    """
    rows = read_borrowed(rec_dir)
    if not rows:
        return {}
    print("\n" + "=" * 60 + f"\n{MOD_NAME}\n" + "=" * 60)
    print(f"  Older copies dropped from the plugin's output: {_drop_copies(rows, Path(plugin_dir), rec_dir)}")
    out, cache = Path(out_root) / MOD_NAME, cache_dir(rec_dir)
    stamps = _read_stamps(cache) if out.is_dir() else {}
    lods = 0 if textures_only else _kept_lods(rec_dir, Path(plugin_dir), cache, plan, parallax)
    previous = current_namespace()
    set_namespace(NAMESPACE)
    try:
        stats = {} if textures_only else _meshes(rec_dir, rows, out, cache, plan, stamps, parallax)
        stats['textures'] = _textures(rec_dir, rows, out, stamps, stats)
    finally:
        set_namespace(previous)
        _write_stamps(cache, stamps)
    print(f"  Borrowed: {stats.get('meshes', 0)} meshes converted, {stats['textures']} textures copied, "
          f"{lods} LOD meshes for the plugin's own meshes")
    return stats


def _kept_lods(rec_dir, plugin_dir: Path, cache: Path, plan: dict, parallax: bool) -> int:
    """Convert each kept vanilla mesh's Morroblivion LOD into the plugin's own output, under the vanilla name.

    The LOD bake reads a model's LOD from the tree holding its full mesh.
    See: docs/commentary/tes4_export_morrowind.md#hybrid-lod-meshes
    """
    lods, root, tree = read_lods(rec_dir), export_root(rec_dir), cache / LOD_TREE
    shutil.rmtree(tree, ignore_errors=True)
    rels = []
    for dest, (owner, src) in sorted(lods.items()):
        source, rel = asset_root(root, owner) / 'meshes' / src.replace(chr(92), '/'), dest.replace(chr(92), '/')
        if source.is_file():
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, tree / rel)
            rels.append(rel)
    dst = plugin_dir / 'meshes' / current_namespace()
    return _convert(tree, dst, rels, plan, parallax, None, {}) if rels else 0


def _meshes(rec_dir, rows: dict, out: Path, cache: Path, plan: dict, stamps: dict, parallax: bool) -> dict:
    """Convert the stale borrowed meshes, owner by owner, then the split halves."""
    root, dst, count, stats = export_root(rec_dir), out / 'meshes' / NAMESPACE, 0, {}
    by_owner = {}
    for rel, owner in rows.items():
        if rel.startswith('meshes/') and owner != SPLIT_OWNER:
            by_owner.setdefault(owner, []).append(rel[len('meshes/'):])
    for owner, rels in sorted(by_owner.items()):
        src = asset_root(root, owner) / 'meshes'
        todo = [r for r in rels if _stale(stamps, 'meshes/' + r, src / r, plan)]
        count += _convert(src, dst, todo, plan, parallax, cache, stats)
        stamps.update({'meshes/' + r: _stamp(src / r, plan, r) for r in todo})
    count += _halves(rec_dir, root, dst, cache, plan, parallax, stats)
    stats['meshes'] = count
    return stats


def _convert(src: Path, dst: Path, rels: list, plan: dict, parallax: bool, cache, stats: dict) -> int:
    """Run the batch converter over meshes `rels` of tree `src`, scanning into `cache` (None: no scan); the count."""
    if not rels:
        return 0
    result = nif_batch.batch_convert(str(src), output_dir=str(dst), fix_textures=True, subdir_filter=rels,
                                     wearable_plan=plan, parallax=parallax, scan_dir=str(cache) if cache else None)
    opacity = stats.setdefault('mesh_conversion', {}).setdefault('alpha_opacity_diffuse', set())
    opacity |= set(result.get('alpha_opacity_diffuse', ()))
    return result.get('converted', 0)


def _halves(rec_dir, root, dst: Path, cache: Path, plan: dict, parallax: bool, stats: dict) -> int:
    """Generate every split item's halves into the cache's mesh tree and convert them."""
    tree, rels = cache / SPLIT_TREE, []
    for item in read_splits(rec_dir):
        roots = [asset_root(root, o) / 'meshes' for o in item['owners']]
        for both in split_models(item['models'], roots, tree).values():
            rels += [p.replace(chr(92), '/') for p in both]
    return _convert(tree, dst, sorted(set(rels)), plan, parallax, cache, stats)


def _textures(rec_dir, rows: dict, out: Path, stamps: dict, stats: dict) -> int:
    """Copy the stale borrowed textures into the shared mod and repair the tree; how many were copied."""
    root, dst, copied = export_root(rec_dir), out / 'textures' / NAMESPACE, 0
    for rel, owner in sorted(rows.items()):
        if not rel.startswith('textures/'):
            continue
        src = asset_root(root, owner) / rel
        if src.is_file() and _stale(stamps, rel, src, {}):
            target = ascii_dest(dst, rel[len('textures/'):])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            stamps[rel] = _stamp(src, {}, rel)
            copied += 1
    if copied:
        repair_texture_tree(dst, stats, True)
    return copied


def _drop_copies(rows: dict, plugin_dir: Path, rec_dir) -> int:
    """Discard each borrowed file (meshes with their weight and first-person variants) from the plugin's output; how many went."""
    ns, gone = namespace_for(rec_dir), 0
    for rel in rows:
        part, _sep, below = rel.partition('/')
        stem, ext = os.path.splitext(str(ascii_dest(plugin_dir / part / ns, below)))
        names = {stem + ext, stem + '.dds'}
        if part == 'meshes':
            names = [n for s in ('', '_0', '_1') for n in (stem + s + ext, first_person_path(stem + s + ext))]
        gone += sum(output_files.discard(n) for n in names)
    return gone


def _stamp(src: Path, plan: dict, rel: str) -> list:
    """What converting `src` depends on: its size, time, wearable plan entry and the converter's version."""
    st = src.stat()
    return [st.st_size, st.st_mtime_ns, repr(plan.get(rel.lower().replace(chr(92), '/'))), current_version()]


def _stale(stamps: dict, key: str, src: Path, plan: dict) -> bool:
    """Whether `key` was never converted from `src` as it is now, under this plan."""
    return src.is_file() and stamps.get(key) != _stamp(src, plan, key.split('/', 1)[1])


def _read_stamps(cache: Path) -> dict:
    """The stamps of every borrowed file converted so far."""
    path = cache / STAMPS
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def _write_stamps(cache: Path, stamps: dict) -> None:
    """Keep the stamps for the next build."""
    os.makedirs(cache, exist_ok=True)
    (cache / STAMPS).write_text(json.dumps(stamps, sort_keys=True), encoding='utf-8')

"""
Authored Morrowind wearing Morroblivion's reviewed meshes: the hybrid source mode.

The plugin keeps its own records and masters; only models change. A world model
swaps when `morroblivion_whitelist.json` approves its Morroblivion replacement,
or when that replacement carries furniture markers. A wearable swaps its worn
and ground models when the whitelist approves Morroblivion's worn model of the
same item; a one-hand piece wears its half of Morroblivion's two-handed item.
Every swapped mesh, its hand-made LOD and each texture they name are listed as
borrowed, and a kept vanilla mesh takes its pair's hand-made LOD.

See: docs/commentary/tes4_export_morrowind.md#hybrid-mode
"""

import filecmp
import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from asset_convert.character.hand_pairs import SPLIT_DIR, split_models
from asset_convert.character.morrowind_coverage import SIDED_SLOTS
from asset_convert.character.skyrim_overrides import SBP_59_RIGHT_HAND
from asset_convert.nif.morroblivion_textures import KEEP_NAME, map_dir, read_map, texture_key
from asset_convert.nif.sse_nif import NifFormat, read_nif
from asset_convert.nif.tex_paths import as_dds, texture_rel_path
from asset_convert.sources import source_registry
from asset_convert.lod.lod_gen import LOD_SUFFIXES
from asset_convert.sources.hybrid_assets import LODS, MANIFEST, SPLIT_OWNER, SPLITS, write_borrowed
from asset_convert.sources.morrowind_assets import find_archived_file, source_meshes
from asset_convert.texture.texture_prune import write_manifest
from output_layout import asset_root, assets_for, record_dir
from tes5_import.base.text_reader import parse_export_file

from .morroblivion import MorroblivionModels, archive_path, body_parts, fix_placements
from .morroblivion_ground import GROUND_NAME, MorroblivionGround, icon_stem, match_ground, twin_stem, write_pairs
from .morroblivion_origin import OriginShifts
from .morroblivion_pairs import right_ids
from .morroblivion_weather import copy_weathers
from .morroblivion_texture_map import MESH_PREFIX, map_is_current, prove_map
from .morrowind_ids import encode_editor_id
from .morrowind_patch import patch_masters, supplied_index
from .record_types.common import escape_value
from .tes3_reader import get_subrecord

#: The reviewed pairs: [{kind, vanilla, morroblivion, decision, note}].
WHITELIST = Path(__file__).with_name('morroblivion_whitelist.json')

#: The borrowed files of the last hybrid export, in the plugin's record dir.
MANIFEST_NAME = MANIFEST

#: The split items of the last hybrid export, in the plugin's record dir.
SPLITS_NAME = SPLITS

#: The vanilla master the texture map pairs meshes through.
VANILLA_ESM = 'Morrowind.esm'

#: The plugins whose own records are vanilla Morrowind's.
VANILLA_PLUGINS = ('morrowind.esm', 'tribunal.esm', 'bloodmoon.esm')

#: The line naming a world model.
WORLD_KEY = 'Model.MODL'

#: A wearable's worn and ground model lines.
WEARABLE_KEYS = ('Male.BipedModel.MODL', 'Female.BipedModel.MODL',
                 'Male.WorldModel.MODL', 'Female.WorldModel.MODL')

#: Record types whose models are worn.
WEARABLES = ('ARMO', 'CLOT')

#: Bytes every furniture-marker NIF holds.
FURNITURE_MARKER = b'BSFurnitureMarker'

#: The folder an Oblivion `_far` mesh's low-resolution textures sit in, under `textures`.
LOWRES = 'lowres' + chr(92)

#: Extensions a plugin's own copy of a texture may carry.
_TEXTURE_EXTS = ('.dds', '.tga', '.bmp')

#: Master export files holding no base records, skipped by the placement scan.
_NOT_BASES = frozenset({'REFR', 'ACHR', 'ACRE', 'CELL', 'LAND', 'PGRD', 'INFO',
                        'DIAL', 'WRLD', 'MWDI', 'MWIN', 'MWQZ', 'SCPT'})


def load_whitelist(path=WHITELIST) -> tuple:
    """({vanilla mesh: Morroblivion mesh, or '' when denied}, {approved Morroblivion worn mesh})."""
    rows = json.loads(Path(path).read_text(encoding='utf-8'))
    world = {archive_path(r['vanilla']): archive_path(r['morroblivion']) if r['decision'] == 'approve' else ''
             for r in rows if r['kind'] == 'world'}
    worn = {archive_path(r['morroblivion']) for r in rows
            if r['kind'] == 'worn' and r['decision'] == 'approve'}
    return world, worn


def _side(rec):
    """True for a right-hand piece, False for a left, None for any other wearable."""
    data = get_subrecord(rec, {'ARMO': 'AODT', 'CLOT': 'CTDT'}.get(rec.type, ''))
    if data is None or len(data.data) < 4:
        return None
    slot = SIDED_SLOTS.get((rec.type, int.from_bytes(data.data[:4], 'little')))
    return None if slot is None else slot == SBP_59_RIGHT_HAND


def _items(export_root: str, exports) -> dict:
    """{FormID low 24 bits: {key: model}} of every Morroblivion ARMO/CLOT."""
    out = {}
    for name in exports:
        folder = record_dir(export_root, name)
        for sig in WEARABLES:
            for rec in parse_export_file(os.path.join(folder, f'{sig}.txt')):
                models = {k: rec[k].replace('\\\\', '\\') for k in WEARABLE_KEYS if rec.get(k)}
                out.setdefault((rec.get('FormID') or '').lower()[2:], models)
    return out


def _low(form_id) -> str:
    """A FormID's low 24 bits as lowercase hex; '' for none."""
    return str(form_id or '').lower()[2:]


def _set_line(lines: list, key: str, value: str) -> None:
    """Replace the `key=` line, or append one."""
    text = f'{key}={escape_value(value)}'
    for i, line in enumerate(lines):
        if line.startswith(key + '='):
            lines[i] = text
            return
    lines.append(text)


class HybridModels:
    """Morroblivion's approved replacement for each vanilla model one plugin names."""

    def __init__(self, export_root: str, exports, source_path: str, plugin: str):
        """Index Morroblivion's exports, the whitelist, and where the plugin keeps its assets."""
        self.export_root, self.plugin = export_root, plugin
        self.vanilla = plugin.lower() in VANILLA_PLUGINS
        self.dest = assets_for(record_dir(export_root, plugin))
        self.models = MorroblivionModels(export_root, [(n, None) for n in exports],
                                         source_path, self.dest / 'meshes')
        self.index = supplied_index(export_root, exports)
        self.world, self.worn = load_whitelist()
        self.items = _items(export_root, exports)
        self.owners = list(reversed(patch_masters(export_root, exports)))
        self.roots = owner_roots(export_root, exports)
        self.named, self.split_items = set(), []
        self._furniture, self._halves, self._vanilla, self._split_dir = {}, {}, None, None

    def mesh_roots(self) -> list:
        """The Morroblivion trees replacement meshes are read from."""
        return [r / 'meshes' for r in self.roots]

    def find(self, rel: str):
        """The Morroblivion file at meshes-relative `rel`, or None."""
        return find_under(self.mesh_roots(), rel)

    def owner_of(self, part: str, rel: str):
        """(owner plugin, file) of the first Morroblivion tree holding `part`/`rel`, or None."""
        for owner, root in zip(self.owners, self.roots):
            found = find_under([root / part], rel)
            if found:
                return owner, found
        return None

    def swapped_in(self, model: str) -> bool:
        """Whether `model` is a Morroblivion mesh no vanilla path also names."""
        rel = archive_path(model)
        if self._vanilla is None:
            data_dir = source_registry.directory_for(self.export_root, 'Morrowind.esm') or ''
            self._vanilla = (source_meshes(os.path.join(data_dir, 'Morrowind.esm')), Path(data_dir) / 'meshes')
        archived, loose = self._vanilla
        return bool(self.find(rel)) and rel not in archived and not find_under([loose], rel)

    def world_model(self, path: str) -> str:
        """The approved replacement for vanilla mesh `path`, or ''; furniture is always approved."""
        rel = archive_path(path)
        if not self.vanilla and self.models.owns(rel) and not self.ships_vanilla(MESH_PREFIX + rel):
            return ''
        return self.world[rel] if rel in self.world else self._furniture_model(rel)

    def ships_vanilla(self, stored: str) -> bool:
        """Whether the plugin's own file at archive path `stored` is vanilla's, byte for byte."""
        own = find_under([self.dest], stored)
        vanilla = find_archived_file(self.export_root, stored) if own else None
        return bool(vanilla) and own.stat().st_size == vanilla.stat().st_size \
            and own.read_bytes() == vanilla.read_bytes()

    def _furniture_model(self, rel: str) -> str:
        """The record-paired replacement of `rel` when it carries furniture markers, else ''."""
        if rel not in self._furniture:
            model = archive_path(self.models.replacement(rel, self.index))
            found = self.find(model) if model else None
            self._furniture[rel] = model if found and FURNITURE_MARKER in found.read_bytes() else ''
        return self._furniture[rel]

    def worn_models(self, rec, log=print) -> dict:
        """{wearable key: model} replacing TES3 wearable `rec`'s, or {} when nothing is approved."""
        side = _side(rec)
        ids = [rec.record_id] + (sorted(right_ids(rec.record_id)) if side is False else [])
        item = next((self.items[_low(fid)] for fid in map(self.index.lookup, ids)
                     if _low(fid) in self.items), None)
        if not item or archive_path(item.get(WEARABLE_KEYS[0], '')) not in self.worn:
            return {}
        if side is None:
            return dict(item)
        halves = self._split(item, log)
        if WEARABLE_KEYS[0] not in halves:
            return {}
        return {k: halves[k][int(side)] if k in halves else v for k, v in item.items()}

    def _split(self, item: dict, log) -> dict:
        """{key: (left, right)} halves of a two-handed item; the halves go to a temporary folder only."""
        key = archive_path(item[WEARABLE_KEYS[0]])
        if key not in self._halves:
            if self._split_dir is None:
                self._split_dir = Path(tempfile.mkdtemp(prefix='hybrid_split_'))
            self._halves[key] = split_models(item, self.mesh_roots(), self._split_dir, log)
            if self._halves[key]:
                self.split_items.append({'models': dict(item), 'owners': self.owners})
        return self._halves[key]

    def forget_halves(self) -> None:
        """Delete the temporary folder the split halves were tried in."""
        if self._split_dir is not None:
            shutil.rmtree(self._split_dir, ignore_errors=True)

    def halves(self) -> set:
        """Every split half written so far, as meshes-relative paths."""
        return {archive_path(p) for pair in self._halves.values()
                for both in pair.values() for p in both}


def remap_models(out: dict, records, ctx, hybrid: HybridModels) -> Counter:
    """Swap every approved model line in `out`; counts by kind. `records` are the plugin's TES3 records."""
    counts = Counter()
    wearables = {ctx.resolve(r.record_id, r.type): r for r in records
                 if r.type in WEARABLES and not r.deleted}
    for sig, rows in out.items():
        if sig == 'CREA':
            continue
        for form_id, lines in rows:
            rec = wearables.get(form_id) if sig in WEARABLES else None
            if rec is not None:
                counts['worn'] += _swap_worn(lines, rec, hybrid)
            else:
                counts['world'] += _swap_world(lines, hybrid, ctx)
    return counts


def _swap_worn(lines: list, rec, hybrid: HybridModels) -> int:
    """Give one wearable Morroblivion's approved models; 1 when it changed.

    Outside the vanilla ESMs a record swaps only when its body parts are the
    vanilla record's of the same id: the same item, not a reused name.
    """
    if not hybrid.vanilla and \
            hybrid.models.wearable_parts.get(rec.record_id.lower()) != body_parts(rec):
        return 0
    models = hybrid.worn_models(rec)
    has_female = any(l.startswith(WEARABLE_KEYS[1] + '=') for l in lines)
    for key in WEARABLE_KEYS:
        model = models.get(key)
        if key == WEARABLE_KEYS[1]:
            model = (model or models.get(WEARABLE_KEYS[0])) if has_female else None
        if model:
            _set_line(lines, key, model)
            hybrid.named.add(archive_path(model))
    return int(bool(models))


def _swap_world(lines: list, hybrid: HybridModels, ctx) -> int:
    """Swap one record's approved world model, with its origin shift; 1 when it changed."""
    for i, line in enumerate(lines):
        key, sep, value = line.partition('=')
        if key != WORLD_KEY or not sep or not value:
            continue
        vanilla = value.replace('\\\\', '\\')
        model = hybrid.world_model(vanilla)
        if not model:
            return 0
        lines[i] = f'{key}={escape_value(model)}'
        hybrid.named.add(model)
        shift = ctx.origin_shifts.shift_for(vanilla, model) if ctx.origin_shifts else 0.0
        if shift:
            lines.append(f'Model.OriginShift={shift!r}')
        return 1
    return 0


def origin_shifts(hybrid: HybridModels, master_roots) -> OriginShifts:
    """Shifts measured against the plugin's own tree, its masters', then Morroblivion's."""
    return OriginShifts(hybrid.export_root,
                        [hybrid.dest / 'meshes', *master_roots, *hybrid.mesh_roots()])


def correct_placements(out: dict, master_dirs, hybrid: HybridModels) -> int:
    """Apply Morroblivion's pitch and Z corrections to every reference of a swapped base.

    The bases are the plugin's own records and its masters' whose model was
    swapped in, keyed by their low 24 bits as `fix_placements` reads a
    reference's NAME. A base the origin shift already lowers takes no Z re-seat.
    See: docs/audits/morroblivion_mesh_axis_rotation.md#the-correction
    """
    models, editor_ids = {}, {}
    rows = [[f'FormID={fid}', *lines] for recs in out.values() for fid, lines in recs]
    for folder in master_dirs:
        rows += [[f'{k}={v}' for k, v in rec.items()] for rec in _master_bases(folder)]
    for lines in rows:
        fields = dict(l.partition('=')[::2] for l in lines)
        low = _low(fields.get('FormID'))
        model = fields.get(WORLD_KEY, '').replace('\\\\', '\\')
        if low and model and hybrid.swapped_in(model):
            models[low] = model
            shifted = float(fields.get('Model.OriginShift') or 0)
            editor_ids[low] = '' if shifted else encode_editor_id(fields.get('EditorID', ''))
    return fix_placements(out, models, editor_ids)


def _master_bases(folder: str):
    """Every base record a master's export holds."""
    for name in sorted(os.listdir(folder)):
        if name.endswith('.txt') and name[:-4] not in _NOT_BASES and not name.startswith('_'):
            yield from parse_export_file(os.path.join(folder, name))


def owner_roots(export_root: str, exports) -> list:
    """The asset roots of Morroblivion's plugins and their masters, the ones borrowed from first."""
    return [asset_root(export_root, n) for n in reversed(patch_masters(export_root, exports))]


def with_maps(rel: str) -> set:
    """A diffuse `.dds` path with its normal and glow maps beside it; a copy skips any not shipped."""
    return {rel, rel[:-4] + '_n.dds', rel[:-4] + '_g.dds'}


def _nif_textures(path: Path) -> set:
    """Every texture a NIF names, as `textures`-relative .dds paths, with their maps and a `lowres` one's twin.

    See: docs/commentary/asset_convert_shader.md#lowres-textures
    """
    out = set()
    for block in read_nif(str(path)).blocks:
        names = []
        if isinstance(block, NifFormat.NiSourceTexture) and block.file_name:
            names.append(block.file_name)
        if isinstance(block, NifFormat.BSShaderTextureSet):
            names.extend(t for t in block.textures if t)
        for name in names:
            rel = as_dds(texture_rel_path(bytes(name).decode('latin-1'))).lower()
            out |= with_maps(rel) | with_maps(rel.removeprefix(LOWRES))
    return out


def map_twins(hybrid: HybridModels) -> set:
    """The twin textures the one map names, less any the plugin keeps; writes the kept keys.

    There is one map (`prove_map`). Outside the vanilla ESMs a
    texture the plugin ships itself keeps its own file, since only vanilla's is
    proven to share Morroblivion's twin.
    See: docs/commentary/asset_convert_texture.md#morroblivion-texture-substitution
    """
    table = read_map(map_dir(hybrid.export_root))
    kept = set() if hybrid.vanilla else {k for k in table if _ships_own_texture(hybrid, k)}
    folder = record_dir(hybrid.export_root, hybrid.plugin)
    (folder / KEEP_NAME).unlink(missing_ok=True)
    if kept:
        write_manifest(folder, kept, KEEP_NAME)
    return {m for k, v in table.items() if k not in kept for m in with_maps(v[len('textures' + chr(92)):])}


def swap_ground(out: dict, hybrid: HybridModels, ground: MorroblivionGround, pairs: dict) -> tuple:
    """Give each LTEX Morroblivion's copy of its texture; (count, textures-relative files to copy).

    A texture the plugin ships itself keeps it and loses its pair. Each
    paired texture's grass models join the copied meshes.
    """
    count, textures = 0, set()
    for form_id, lines in out.get('LTEX', ()):
        icon = dict(l.partition('=')[::2] for l in lines).get('ICON', '').replace(chr(92) * 2, chr(92))
        twins = ground.by_stem.get(twin_stem(icon)) or ground.by_stem.get(icon_stem(icon))
        if not twins or (not hybrid.vanilla and _ships_own_texture(hybrid, texture_key(icon))):
            pairs.pop(form_id, None)
            continue
        new_icon = ground.ltex[twins[0]]['ICON']
        _set_line(lines, 'ICON', new_icon)
        textures |= with_maps('landscape' + chr(92) + archive_path(new_icon))
        hybrid.named |= {archive_path(g['Model.MODL']) for g in ground.grasses(pairs.get(form_id, ''))}
        count += 1
    return count, textures


def vanilla_esm(export_root: str) -> str:
    """The installed Morrowind.esm, whose records pair vanilla meshes with Morroblivion's."""
    return os.path.join(source_registry.directory_for(export_root, VANILLA_ESM) or '', VANILLA_ESM)


def apply_hybrid(out: dict, records, ctx, plugin: str, source_path: str, masters,
                 exports, progress=print) -> None:
    """Swap the approved models and land textures in `out`, correct placements, and list what they borrow.

    `masters` are the plugin's (name, export dir) pairs; `exports` the
    converted Morroblivion plugins the replacements come from. No asset is
    written: the borrowed files are listed for the shared mod.
    See: docs/commentary/tes4_export_morrowind.md#hybrid-shared-assets
    """
    root = ctx.export_root
    hybrid = HybridModels(root, exports, source_path, plugin)
    try:
        master_meshes = [assets_for(path) / 'meshes' for _name, path in masters]
        ctx.origin_shifts = origin_shifts(hybrid, master_meshes)
        counts = remap_models(out, records, ctx, hybrid)
        corrected = correct_placements(out, [path for _name, path in masters], hybrid)
        ground = MorroblivionGround(root, exports)
        pairs = match_ground(out, ctx, ground, hybrid.vanilla)
        lands, land_textures = swap_ground(out, hybrid, ground, pairs)
        write_pairs(record_dir(root, plugin), out, pairs)
        if not map_is_current(root, exports):
            prove_map(root, exports, vanilla_esm(root), hybrid.index, progress)
        twins = map_twins(hybrid)
        skies = {archive_path(t).lower() for t in copy_weathers(out, ctx, source_path, exports)}
        meshes, textures = borrow_assets(hybrid, master_meshes, twins | land_textures | skies, progress)
    finally:
        hybrid.forget_halves()
    progress(f"  Morroblivion (hybrid): {counts['world']} world and {counts['worn']} worn "
             f"models swapped, {lands} land textures, {corrected} ref corrections; borrows "
             f'{meshes} meshes and {textures} textures ({len(twins)} map texture files)'
             + (f'; copied the Morroblivion weathers ({len(skies)} sky textures)' if skies else ''))


def _ships_own_texture(hybrid: HybridModels, key: str) -> bool:
    """Whether the plugin ships its own texture `key` (any extension) that is not vanilla's file."""
    for ext in _TEXTURE_EXTS:
        stored = f'textures{chr(92)}{key}{ext}'
        if find_under([hybrid.dest], stored) and not hybrid.ships_vanilla(stored):
            return True
    return False


def find_under(roots, rel: str):
    """The first file at `rel` under `roots`, or None."""
    for root in roots:
        path = Path(root) / archive_path(rel).replace(chr(92), '/')
        if path.is_file():
            return path
    return None


def borrow_assets(hybrid: HybridModels, master_roots, twins=(), log=print) -> tuple:
    """List each named mesh with its hand-made LOD, the textures they name and `twins` as borrowed; (meshes, textures).

    A file the plugin's own tree or a master's holds is theirs, never borrowed.
    A split half is borrowed from no owner: the shared mod generates it, with
    the textures of the model it is split from. A vanilla mesh the plugin keeps
    takes its Morroblivion pair's LOD mesh (`kept_lods`).
    See: docs/commentary/tes4_export_morrowind.md#hybrid-lod-meshes
    """
    rows, textures = {}, set(twins)
    own = [hybrid.dest / 'meshes'] + list(master_roots)
    for rel in sorted(hybrid.named):
        found = None if find_under(own, rel) else hybrid.owner_of('meshes', rel)
        if found:
            textures |= _borrow_mesh(rows, rel, found, log)
            lod = lod_of(hybrid, rel)
            textures |= _borrow_mesh(rows, lod[0], lod[1], log) if lod else set()
    lods = kept_lods(hybrid)
    for _owner, src in lods.values():
        textures |= _textures_of(hybrid.find(src), src, log)
    for model in sorted({m for item in hybrid.split_items for m in item['models'].values()}):
        found = hybrid.owner_of('meshes', model)
        textures |= _textures_of(found[1], model, log) if found else set()
    for rel in sorted(hybrid.halves()):
        rows[_posix('meshes', rel)] = SPLIT_OWNER
    meshes = len(rows)
    own_textures = [Path(r).parent / 'textures' for r in own]
    for rel in sorted(textures):
        found = None if find_under(own_textures, rel) else hybrid.owner_of('textures', rel)
        if found:
            rows[_posix('textures', rel)] = found[0]
    write_borrowed(record_dir(hybrid.export_root, hybrid.plugin), rows, hybrid.split_items, lods)
    return meshes + len(lods), len(rows) - meshes


def lod_of(hybrid: HybridModels, rel: str):
    """(meshes rel, (owner, file)) of the hand-made LOD mesh Morroblivion ships beside mesh `rel`, or None."""
    for suffix in LOD_SUFFIXES:
        lod = rel[:-4] + suffix + '.nif'
        found = hybrid.owner_of('meshes', lod)
        if found:
            return lod, found
    return None


def kept_lods(hybrid: HybridModels) -> dict:
    """{plugin meshes rel: (owner, Morroblivion meshes rel)}: the paired LOD of each vanilla mesh the plugin keeps.

    Only a mesh the plugin ships as vanilla's file, unswapped and with no LOD
    of its own, takes one; its references keep the vanilla placement.
    See: docs/commentary/tes4_export_morrowind.md#hybrid-lod-meshes
    """
    out = {}
    for rel in sorted(hybrid.models.owners):
        model = archive_path(hybrid.models.replacement(rel, hybrid.index))
        lod = lod_of(hybrid, model) if model else None
        if lod and _keeps_vanilla(hybrid, rel):
            dest = rel[:-4] + lod[0][len(model) - 4:]
            if not find_under([hybrid.dest / 'meshes'], dest):
                out[dest] = (lod[1][0], lod[0])
    return out


def _keeps_vanilla(hybrid: HybridModels, rel: str) -> bool:
    """Whether the plugin ships vanilla mesh `rel` itself, as vanilla's file, and does not swap it."""
    shipped = hybrid.models.owns(rel) and (hybrid.vanilla or hybrid.ships_vanilla(MESH_PREFIX + rel))
    return shipped and not hybrid.world_model(rel)


def _borrow_mesh(rows: dict, rel: str, found: tuple, log) -> set:
    """List mesh `rel` as borrowed from `found` (owner, file); the textures it names."""
    rows[_posix('meshes', rel)] = found[0]
    return _textures_of(found[1], rel, log)


def _textures_of(path: Path, rel: str, log) -> set:
    """The textures mesh `path` names, or none when it cannot be read."""
    try:
        return _nif_textures(path)
    except Exception as exc:
        log(f'  [hybrid] {rel}: textures unread ({type(exc).__name__})')
        return set()


def _posix(part: str, rel: str) -> str:
    """`rel` under `part` ('meshes' or 'textures') as a forward-slash path."""
    return part + '/' + archive_path(rel).replace(chr(92), '/')


def remove_copies(export_root: str, plugin: str, sources) -> tuple:
    """Delete an older build's copies from the plugin's tree; (deleted, kept), and drop the borrowed lists.

    An old list has no tab. A copy goes when it is byte-identical to its file
    under `sources` (Morroblivion's asset roots) or is a generated split half;
    any other file is kept, since it may be the plugin's own.
    See: docs/commentary/tes4_export_morrowind.md#hybrid-shared-assets
    """
    folder = record_dir(export_root, plugin)
    path = folder / MANIFEST_NAME
    lines = path.read_text(encoding='utf-8').splitlines() if path.is_file() else ()
    deleted = kept = 0
    for rel in (line for line in lines if line and chr(9) not in line):
        target = assets_for(folder) / rel
        if target.is_file() and _old_copy(target, rel, sources):
            target.unlink()
            deleted += 1
        elif target.is_file():
            kept += 1
    for name in (MANIFEST_NAME, SPLITS_NAME, LODS, KEEP_NAME, GROUND_NAME):
        (folder / name).unlink(missing_ok=True)
    return deleted, kept


def _old_copy(target: Path, rel: str, sources) -> bool:
    """Whether `target` is an older build's copy: a split half, or its source's bytes."""
    part, _sep, below = rel.partition('/')
    if part == 'meshes' and below.lower().startswith(SPLIT_DIR.replace(chr(92), '/') + '/'):
        return True
    source = find_under([Path(r) / part for r in sources], below)
    return bool(source) and filecmp.cmp(source, target, shallow=False)

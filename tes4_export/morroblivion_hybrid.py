"""
Authored Morrowind wearing Morroblivion's reviewed meshes: the hybrid source mode.

The plugin keeps its own records and masters; only models change. A world model
swaps when `morroblivion_whitelist.json` approves its Morroblivion replacement,
or when that replacement carries furniture markers. A wearable swaps its worn
and ground models when the whitelist approves Morroblivion's worn model of the
same item; a one-hand piece wears its half of Morroblivion's two-handed item.
Every swapped mesh, and each texture it names, is copied into the plugin's own
asset tree, so the plugin ships them.

See: docs/commentary/tes4_export_morrowind.md#hybrid-mode
"""

import json
import os
import shutil
from collections import Counter
from pathlib import Path

from asset_convert.character.hand_pairs import split_models
from asset_convert.character.morrowind_coverage import SIDED_SLOTS
from asset_convert.character.skyrim_overrides import SBP_59_RIGHT_HAND
from asset_convert.nif.morroblivion_textures import MAP_NAME, write_map
from asset_convert.nif.sse_nif import NifFormat, read_nif
from asset_convert.nif.tex_paths import as_dds, texture_rel_path
from asset_convert.sources import source_registry
from asset_convert.sources.morrowind_assets import find_archived_file, find_vanilla_mesh, source_meshes
from output_layout import asset_root, assets_for, record_dir
from tes5_import.base.text_reader import parse_export_file

from .morroblivion import MorroblivionModels, archive_path, body_parts, fix_placements
from .morroblivion_origin import OriginShifts
from .morroblivion_pairs import right_ids
from .morroblivion_texture_map import MESH_PREFIX, build_map, paired_meshes
from .morrowind_ids import encode_editor_id
from .morrowind_patch import patch_masters, supplied_index
from .record_types.common import escape_value
from .tes3_reader import get_subrecord

#: The reviewed pairs: [{kind, vanilla, morroblivion, decision, note}].
WHITELIST = Path(__file__).with_name('morroblivion_whitelist.json')

#: The copied files of the last hybrid export, in the plugin's record dir.
MANIFEST_NAME = 'morroblivion_hybrid_assets.txt'

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
        self.roots = [asset_root(export_root, n)
                      for n in reversed(patch_masters(export_root, exports))]
        self.named = set()
        self._furniture, self._halves, self._vanilla = {}, {}, None

    def mesh_roots(self) -> list:
        """The Morroblivion trees replacement meshes are read from."""
        return [r / 'meshes' for r in self.roots]

    def find(self, rel: str):
        """The Morroblivion file at meshes-relative `rel`, or None."""
        return find_under(self.mesh_roots(), rel)

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
        """{key: (left, right)} halves of a two-handed item, written once under the plugin's meshes."""
        key = archive_path(item[WEARABLE_KEYS[0]])
        if key not in self._halves:
            self._halves[key] = split_models(item, self.mesh_roots(), self.dest / 'meshes', log)
        return self._halves[key]

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


def _nif_textures(path: Path) -> set:
    """Every texture a NIF names, as `textures`-relative .dds paths, with their normal and glow maps."""
    out = set()
    for block in read_nif(str(path)).blocks:
        names = []
        if isinstance(block, NifFormat.NiSourceTexture) and block.file_name:
            names.append(block.file_name)
        if isinstance(block, NifFormat.BSShaderTextureSet):
            names.extend(t for t in block.textures if t)
        for name in names:
            rel = as_dds(texture_rel_path(bytes(name).decode('latin-1'))).lower()
            out.update({rel, rel[:-4] + '_n.dds', rel[:-4] + '_g.dds'})
    return out


def prove_texture_map(hybrid: HybridModels, exports, inherited: bool, progress=print) -> set:
    """Write the plugin's Morroblivion texture map unless `inherited`; the twin textures it names.

    Outside the vanilla ESMs a texture the plugin ships itself keeps its own
    file, since only vanilla's is proven to share Morroblivion's twin.
    See: docs/commentary/asset_convert_texture.md#morroblivion-texture-substitution
    """
    if inherited:
        return set()
    twins = paired_meshes(hybrid.models, hybrid.mesh_roots(), hybrid.index)
    meshes = {}
    for key in twins:
        found = find_vanilla_mesh(hybrid.export_root, key[len(MESH_PREFIX):])
        if found is not None:
            meshes[key] = found.read_bytes()
    table = build_map(meshes, {k: twins[k] for k in meshes}, hybrid.export_root, exports, progress)
    if not hybrid.vanilla:
        table = {k: v for k, v in table.items() if not _ships_own_texture(hybrid, k)}
    write_map(record_dir(hybrid.export_root, hybrid.plugin), table)
    return {v[len('textures' + chr(92)):] for v in table.values()}


def apply_hybrid(out: dict, records, ctx, plugin: str, source_path: str, masters,
                 exports, progress=print) -> None:
    """Swap the approved models in `out`, correct their placements, and copy what they need.

    `masters` are the plugin's (name, export dir) pairs; `exports` the
    converted Morroblivion plugins the replacements come from.
    """
    root = ctx.export_root
    hybrid = HybridModels(root, exports, source_path, plugin)
    master_meshes = [assets_for(path) / 'meshes' for _name, path in masters]
    ctx.origin_shifts = origin_shifts(hybrid, master_meshes)
    counts = remap_models(out, records, ctx, hybrid)
    corrected = correct_placements(out, [path for _name, path in masters], hybrid)
    inherited = any((Path(path) / MAP_NAME).is_file() for _name, path in masters)
    twins = prove_texture_map(hybrid, exports, inherited, progress)
    meshes, textures = copy_assets(hybrid, master_meshes, twins, progress)
    progress(f"  Morroblivion (hybrid): {counts['world']} world and {counts['worn']} worn "
             f"models swapped, {corrected} ref corrections; copied {meshes} meshes, "
             f'{textures} textures' + ('' if inherited else f', {len(twins)} texture twins'))


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


def copy_assets(hybrid: HybridModels, master_roots, twins=(), log=print) -> tuple:
    """Copy each named mesh, its textures and the `twins` textures into the plugin's tree.

    A file a master's tree already holds is left to the master. Returns
    (meshes copied, textures copied); every copy is listed in the manifest.
    """
    copied, textures = [], set(twins)
    own = hybrid.dest / 'meshes'
    for rel in sorted(hybrid.named | hybrid.halves()):
        source = hybrid.find(rel) or find_under([own], rel)
        if source is None or find_under(master_roots, rel):
            continue
        _copy(source, own, rel, copied)
        try:
            textures |= _nif_textures(source)
        except Exception as exc:
            log(f'  [hybrid] {rel}: textures unread ({type(exc).__name__})')
    meshes = len(copied)
    master_textures = [Path(r).parent / 'textures' for r in master_roots]
    tex_roots = [r / 'textures' for r in hybrid.roots]
    for rel in sorted(textures):
        found = None if find_under(master_textures, rel) else find_under(tex_roots, rel)
        if found:
            _copy(found, hybrid.dest / 'textures', rel, copied)
    write_manifest(hybrid.export_root, hybrid.plugin, copied)
    return meshes, len(copied) - meshes


def _copy(source: Path, root: Path, rel: str, copied: list) -> None:
    """Copy `source` to `root`/`rel` (unless it is that file) and list it as a copy."""
    target = root / archive_path(rel).replace(chr(92), '/')
    if target.resolve() != source.resolve():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    copied.append(target.relative_to(root.parent).as_posix())


def write_manifest(export_root: str, plugin: str, copied: list) -> None:
    """Record the files this export copied, beside the plugin's records."""
    path = record_dir(export_root, plugin) / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(f'{p}\n' for p in sorted(set(copied))), encoding='utf-8')


def remove_copies(export_root: str, plugin: str) -> int:
    """Delete the files an earlier hybrid export copied, and its texture map; how many were removed."""
    folder = record_dir(export_root, plugin)
    path = folder / MANIFEST_NAME
    removed = 0
    if path.is_file():
        root = assets_for(folder)
        for rel in path.read_text(encoding='utf-8').splitlines():
            if rel and (root / rel).is_file():
                (root / rel).unlink()
                removed += 1
        path.unlink()
    (folder / MAP_NAME).unlink(missing_ok=True)
    return removed

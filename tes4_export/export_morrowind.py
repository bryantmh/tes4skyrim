"""
Morrowind export: string IDs to FormIDs, and cells to cells plus placements.

Everything downstream of the export text assumes a 32-bit FormID, so the
identity Morrowind lacks has to be minted here rather than at import. An ID
resolves in three tiers: an already-converted master (Morroblivion first when
the source set says so, so a converted mod shares its objects instead of
duplicating them), then an engine marker, then a FormID derived from the
authored string itself -- but only for objects this plugin defines. A master's
object whose master is not converted is dropped and counted, never minted.

See: docs/commentary/tes4_export_morrowind.md#masters
"""

import hashlib
import os
import struct
import time
from collections import Counter

from output_layout import assets_for, record_dir
from core.plugin_masters import masters_from_export_header

from .morroblivion import (MORROBLIVION_PREFIX, MorroblivionModels,
                           remap_vanilla_models)
from .morroblivion_origin import OriginShifts
from .morrowind_armor import load_body_models
from .morrowind_cell import parse_cell
from .morrowind_ids import (IdIndex, exterior_key, interior_key, land_key,
                            load_master_doors, persistent_key,
                            load_index, marker_formid)
from .morrowind_markers import MarkerBuilder, marker_lines
from .morrowind_pathgrid import pathgrid_records
from .morrowind_patch import PATCH_NAME
from .morrowind_grass import (GrassTally, grass_records, is_grass_model,
                              ltex_grass_lines, master_ltex_fields,
                              master_texture_grid)
from .morrowind_land import (MAX_QUAD_LAYERS, decode_heights,
                             decode_textures, encode_heights, inner_patch,
                             layer_lines, ltex_index, pad_grid,
                             quadrant_normals, quadrant_textures,
                             shift_textures, sub_patch)
from .morrowind_world import (TES4_CELL_SIZE, WORLDSPACE_EDID, cell_editor_id,
                              cell_grid, tes3_cell_quadrants)
from .record_types.morrowind import (MORROWIND_ITEM_EXPORTERS, emit_ref,
                                     tes4_signature)
from .record_types.morrowind_actors import (MORROWIND_ACTOR_EXPORTERS,
                                            register_sound_gens)
from .record_types.morrowind_packages import (package_records,
                                              prune_dropped_packages)
from .record_types.morrowind_scripts import MORROWIND_SCRIPT_EXPORTERS
from .tes3_reader import get_subrecord, read_file, read_masters

#: TES3 signature -> exporter, over every base record type this pass converts.
EXPORTERS = {**MORROWIND_ITEM_EXPORTERS, **MORROWIND_ACTOR_EXPORTERS,
             **MORROWIND_SCRIPT_EXPORTERS}

#: conversion_config.json key choosing which converted plugins a Morrowind mod borrows from.
MORROWIND_SOURCE_KEY = 'morrowindSource'
SOURCE_VANILLA = 'vanilla'
SOURCE_MORROBLIVION = 'morroblivion'

#: The vanilla ESMs Morroblivion replaces wholesale.
VANILLA_MASTERS = ('morrowind.esm', 'tribunal.esm', 'bloodmoon.esm')

#: Derived ids live above Morroblivion's blocks so the two never collide.
_DERIVED_BASE = 0x00200000
_DERIVED_SPAN = 0x00D00000

#: Rehash attempts before a derived id is declared impossible.
_MAX_REHASH = 64

#: SkyrimClimate (0x812), the climate vanilla Tamriel itself uses.
_SKYRIM_CLIMATE = '00000812'

#: LAND DATA 0x1D (4,207 vanilla LANDs): normals, layers, unknown4, auto-calc.
_LAND_FLAGS = 29

#: The same without the layers bit, when the source names no texture at all.
_LAND_FLAGS_NO_TEX = 25

#: Base-record signature -> the placement record type that names it.
_PLACEMENT_SIGNATURES = {'NPC_': 'ACHR', 'CREA': 'ACRE'}

#: TES4 record flag for a persistent reference; every load door carries it.
_PERSISTENT = 1024

#: How far a DODT destination may sit from the door it arrives beside.
_PARTNER_RADIUS = 1024.0

#: Signature every engine marker resolves as.
_MARKER_SIGNATURE = 'STAT'

#: The WRLD lines that carry the worldspace extents.
_BOUNDS_KEYS = ('NAM0.MinX', 'NAM0.MinY', 'NAM9.MaxX', 'NAM9.MaxY')


class MorrowindContext:
    """Resolves Morrowind string IDs to FormIDs for one conversion.

    `index` holds every record an already-converted master can supply; it is
    empty when nothing has been converted yet, which is what makes the
    master tier optional rather than required. `own_index` is the load-order
    byte of this plugin's own records: the length of its converted master
    list, as the importer expects.
    """

    def __init__(self, index: IdIndex = None, own_index: int = 0):
        """Start from whatever an already-converted plugin can supply."""
        self.index = index if index is not None else IdIndex()
        self.own_index = own_index
        self.master_bounds = None
        self.body_models = {}
        self.morroblivion = None
        self.origin_shifts = None
        self.derived = {}
        self.unresolved = Counter()
        self.own_ids = {}
        self._taken = set(self.index.form_ids())
        self._exterior_cells = set()
        self.exterior_cell_ids = set()
        self.rehomed_persistent = 0
        self._ref_counts = Counter()
        self.ltex_by_index = {}
        self.interior_names = set()
        self.markers = MarkerBuilder()
        self.known_grids = set()
        self.pending_teleports = []
        self.doors_by_cell = {}
        self.pending_packages = []
        self.dropped_packages = set()
        self.placements = {}
        self.sound_gens = {}
        self.gap_ids = {}
        self.unlinked_doors = 0
        self.vtex_by_cell = {}
        self.master_dirs = []
        self.grass = None
        self.grass_models = {}
        self.grass_ltex = {}

    def grass_id(self, record_id: str, texture: str) -> str:
        """The FormID of the GRAS one static becomes on one land texture."""
        return self.derive('gras:' + record_id.lower() + ':' + texture.upper())

    def register_ltex(self, index: int, form_id: str) -> None:
        """Note the FormID a LAND's VTEX index resolves to."""
        if form_id:
            self.ltex_by_index[index] = form_id

    def land_texture(self, vtex_value: int) -> str:
        """The LTEX FormID a VTEX entry names, or '' for the default.

        See: docs/commentary/tes4_export_morrowind.md#land-terrain
        """
        index = ltex_index(vtex_value)
        if index is None:
            return ''
        return self.ltex_by_index.get(index, '')

    def worldspace_id(self) -> str:
        """The FormID every converted exterior cell hangs under.

        See: docs/commentary/tes4_export_morrowind.md#the-synthetic-worldspace
        """
        return (self.index.lookup_editor_id(WORLDSPACE_EDID)
                or self.derive('wrld:' + WORLDSPACE_EDID))

    def owns_worldspace(self) -> bool:
        """Whether this plugin has to define the worldspace itself."""
        return self.index.lookup_editor_id(WORLDSPACE_EDID) is None

    def exterior_cell_id(self, grid: tuple) -> str:
        """The FormID for an exterior cell: a master's, else keyed on the grid."""
        form_id = (self.index.lookup_exterior(grid)
                   or self.derive(exterior_key(grid)))
        self.exterior_cell_ids.add(form_id)
        return form_id

    def persistent_cell_id(self) -> str:
        """The dummy cell for persistent refs: a master's own, else ours.

        See: docs/commentary/tes4_export_morrowind.md#teleport-doors
        """
        return (self.index.lookup_persistent(self.worldspace_id())
                or self.derive(persistent_key(WORLDSPACE_EDID)))

    def interior_cell_id(self, name: str) -> str:
        """The FormID for an interior cell: a master's, else keyed on the name."""
        return self.index.lookup_interior(name) or self.derive(interior_key(name))

    def land_id(self, grid: tuple) -> str:
        """The FormID for the LAND under one exterior cell, a master's first."""
        return (self.index.lookup_editor_id(land_key(self.exterior_cell_id(grid)))
                or self.derive('land:%d:%d' % grid))

    def world_bounds(self) -> tuple:
        """(MinX, MinY, MaxX, MaxY) in world units over every claimed cell.

        A worldspace whose bounds are an empty rectangle at the origin makes
        the engine build a degenerate object-LOD quadtree over every
        reference in it, and the load never completes. A master's extents are
        folded in, so a plugin that grows the world grows the rectangle.
        See: docs/commentary/tes4_export_morrowind.md#the-synthetic-worldspace
        """
        if not self._exterior_cells:
            return self.master_bounds or (0.0, 0.0, 0.0, 0.0)
        xs = [g[0] for g in self._exterior_cells]
        ys = [g[1] for g in self._exterior_cells]
        own = (float(min(xs) * TES4_CELL_SIZE), float(min(ys) * TES4_CELL_SIZE),
               float((max(xs) + 1) * TES4_CELL_SIZE),
               float((max(ys) + 1) * TES4_CELL_SIZE))
        if self.master_bounds is None:
            return own
        return (min(own[0], self.master_bounds[0]),
                min(own[1], self.master_bounds[1]),
                max(own[2], self.master_bounds[2]),
                max(own[3], self.master_bounds[3]))

    def claim_exterior(self, grid: tuple) -> bool:
        """Claim a grid square; True when another cell already emitted it.

        See: docs/commentary/tes4_export_morrowind.md#coordinates-and-cell-splitting
        """
        seen = grid in self._exterior_cells
        self._exterior_cells.add(grid)
        return seen

    def note_cell(self, cell) -> None:
        """Record that a cell exists, so a door may teleport into it.

        See: docs/commentary/tes4_export_morrowind.md#teleport-doors
        """
        if cell.interior:
            self.interior_names.add(cell.name.lower())
            self.markers.note_interior(cell)
        else:
            self.known_grids.update(tes3_cell_quadrants(*cell.grid))

    def destination_cell(self, ref) -> str:
        """The FormID of the cell a door reference teleports into, or ''.

        The cell may be this plugin's own or a converted master's.
        """
        if ref.dest_cell:
            if (ref.dest_cell.lower() not in self.interior_names
                    and self.index.lookup_interior(ref.dest_cell) is None):
                return ''
            return self.interior_cell_id(ref.dest_cell)
        grid = cell_grid(ref.dest_pos[0], ref.dest_pos[1])
        if grid in self.known_grids or self.index.lookup_exterior(grid):
            return self.exterior_cell_id(grid)
        return ''

    def next_ref_id(self, parent_cell: str) -> str:
        """The FormID for the next reference placed in this cell."""
        index = self._ref_counts[parent_cell]
        self._ref_counts[parent_cell] = index + 1
        return self.derive('refr:%s:%d' % (parent_cell, index))

    def register_own(self, record_id: str, signature: str = '') -> None:
        """Note that this plugin exports a base record for `record_id`."""
        if record_id:
            own = self.own_ids.setdefault(record_id.lower(), [])
            if signature not in own:
                own.append(signature)

    def base_signature(self, record_id: str, want: str = '') -> str:
        """The TES4 record type the object `record_id` converts to, or ''.

        `want` chooses among the types one Morrowind ID is exported under;
        without it the first registered wins, which is what an untyped cell
        reference gets.
        See: docs/commentary/tes4_export_morrowind.md#actors-and-placements
        """
        if marker_formid(record_id) is not None:
            return _MARKER_SIGNATURE
        own = self.own_ids.get(record_id.lower(), ())
        if want and want in own:
            return want
        return (self.index.lookup_signature(record_id)
                or (own[0] if own else ''))

    def resolve(self, record_id: str, signature: str = '') -> str:
        """The FormID this ID converts to, or '' when nothing supplies one.

        Returning '' keeps references to base objects this pass does not
        convert out of the output. Such a reference would name a record that
        is never written, which crashes the engine rather than merely showing
        nothing.

        `signature` picks between the record types one Morrowind ID may name,
        which its call site knows and a cell reference does not.
        See: docs/commentary/tes4_export_morrowind.md#per-type-id-namespaces
        """
        if not record_id:
            return ''
        existing = self.index.lookup(record_id)
        if existing:
            return existing
        own = self.base_signature(record_id, signature)
        filled = self.gap_ids.get((own, record_id.lower()))
        if filled:
            return filled
        marker = marker_formid(record_id)
        if marker is not None:
            return '%08X' % marker
        if record_id.lower() in self.own_ids:
            return self.derive('%s:%s' % (own, record_id))
        return ''

    def derive(self, record_id: str) -> str:
        """A stable FormID minted from the authored string ID.

        Hashed, never counted, so allocation order is irrelevant and adding
        records never moves existing ids. A clash is resolved by rehashing
        with a salt rather than by probing, so an id depends only on its own
        key and the keys that hash before it.
        """
        key = record_id.lower()
        found = self.derived.get(key)
        if found is not None:
            return found
        for salt in range(_MAX_REHASH):
            probe = key if salt == 0 else '%s\x00%d' % (key, salt)
            digest = hashlib.md5(probe.encode('utf-8')).digest()
            offset = int.from_bytes(digest[:4], 'little') % _DERIVED_SPAN
            candidate = '%08X' % ((self.own_index << 24)
                                  | (_DERIVED_BASE + offset))
            if candidate not in self._taken:
                self._taken.add(candidate)
                self.derived[key] = candidate
                return candidate
        raise RuntimeError('no free derived FormID for %r' % record_id)


def load_context(export_root: str, masters=()) -> MorrowindContext:
    """Build a context from the converted masters, in master-list order.

    Each master's ids are re-keyed into THIS plugin's master list: the
    master's own byte (its header's master count) becomes its slot here and
    each of its masters is translated by name, exactly as the importer's
    `load_master_export` does.
    See: docs/commentary/tes4_export_morrowind.md#masters
    """
    paths = [p if os.path.isabs(p) else os.path.join(export_root, p)
             for _n, p in masters]
    slot_of = {n.lower(): i for i, n in enumerate(_master_list(masters))}
    index = IdIndex()
    master_doors = []
    master_remaps = []
    for slot, path in enumerate(paths):
        own = masters_from_export_header(path)
        remap = {len(own): slot}
        for k, sub in enumerate(own):
            target = slot_of.get(sub.lower())
            if target is not None:
                remap[k] = target
        index.merge(load_index(path, remap=remap))
        master_doors.append(load_master_doors(path, remap))
        master_remaps.append((path, remap))
    ctx = MorrowindContext(index, own_index=len(paths))
    for doors in master_doors:
        for cell, entries in doors.items():
            ctx.doors_by_cell.setdefault(cell, []).extend(entries)
    ctx.master_bounds = _master_world_bounds(paths)
    ctx.master_dirs = master_remaps
    return ctx


def _master_world_bounds(master_dirs) -> tuple:
    """The NAM0/NAM9 extents of the first master defining our worldspace, or None."""
    for path in master_dirs:
        wrld = os.path.join(path, 'WRLD.txt')
        if not os.path.isfile(wrld):
            continue
        with open(wrld, encoding='utf-8') as fh:
            for block in fh.read().split('---RECORD_BEGIN---'):
                fields = dict(line.partition('=')[::2] for line in block.splitlines()
                              if '=' in line)
                if fields.get('EditorID', '').lower() == WORLDSPACE_EDID.lower() \
                        and all(k in fields for k in _BOUNDS_KEYS):
                    return tuple(float(fields[k]) for k in _BOUNDS_KEYS)
    return None


def _missing_master_message(plugin: str, missing: list) -> str:
    """The refusal text naming each unconverted master and how to convert it."""
    lines = [f'{plugin} declares masters that have not been converted.', '',
             "A Morrowind plugin names its masters' objects by plain string, so",
             'without them those objects resolve to nothing and this plugin',
             "would claim their load-order slots as its own.", '', 'Missing:']
    lines += [f'  {name}' for name in missing]
    if PATCH_NAME in missing:
        lines += ['', f'Build {PATCH_NAME} from Settings > Morrowind source > '
                      'Build compatibility patch, or run:', '',
                  '  python convert.py --build-morrowind-patch '
                  '"<Morrowind>/Data Files"']
    others = [n for n in missing if n != PATCH_NAME]
    if others:
        lines += ['', 'Convert the rest first:']
        lines += [f'  python convert.py -f {name}' for name in others]
    return chr(10).join(lines)


def run_export(file_name: str, source: str, export_dir: str,
               config: dict = None) -> bool:
    """Export one Morrowind plugin and report what it produced.

    Refuses when a declared master has no export, exactly as the import stage
    refuses a missing converted master: the master list fixes this plugin's own
    load-order byte, so exporting without one silently renumbers every record.
    See: docs/commentary/tes4_export_morrowind.md#masters
    """
    start = time.time()
    print(f'[{file_name}] Exporting (Morrowind)...')
    mode = (config or {}).get(MORROWIND_SOURCE_KEY, SOURCE_VANILLA)
    masters, missing = converted_master_dirs(export_dir, file_name, source, mode)
    if missing:
        print(f'[{file_name}] ERROR: '
              + _missing_master_message(file_name, missing))
        return False
    if masters:
        print(f"  Borrowing objects from {', '.join(_master_list(masters))}")
    result = export_plugin(source, export_dir, masters)
    total = sum(result['counts'].values())
    print(f"  Wrote {total} records to {result['output']}")
    if result['dropped']:
        print(f"  Skipped {result['dropped']} references whose base object "
              f'is not converted')
    if result['unlinked_doors']:
        print(f"  {result['unlinked_doors']} load doors lead to a cell that "
              f'is not converted and stay plain doors')
    print(f'[{file_name}] Export complete in {time.time() - start:.2f}s')
    return True


def converted_master_dirs(export_dir: str, plugin: str, source_path: str,
                          mode: str = SOURCE_VANILLA) -> tuple:
    """([(name, export dir)], unconverted names) for the declared masters.

    The plugin's own MAST chain, in its order. In Morroblivion mode every
    converted `Morrowind_ob*` export comes first, then the compatibility patch
    supplying what Morroblivion lacks, and the three vanilla ESMs it replaces
    are dropped, so those three are never reported missing.
    See: docs/commentary/tes4_export_morrowind.md#masters
    """
    names = read_masters(source_path)
    if mode == SOURCE_MORROBLIVION:
        names = morroblivion_exports(export_dir) + [PATCH_NAME] + [
            n for n in names if n.lower() not in VANILLA_MASTERS]
    found, missing = [], []
    for name in names:
        if name.lower() == plugin.lower():
            continue
        path = str(record_dir(export_dir, name))
        if os.path.isfile(os.path.join(path, '_HEADER.txt')):
            found.append((name, path))
        else:
            missing.append(name)
    return found, missing


def morroblivion_exports(export_dir: str) -> list:
    """Every converted Morroblivion plugin, the ESM ahead of its patches."""
    if not os.path.isdir(export_dir):
        return []
    names = [n for n in os.listdir(export_dir)
             if n.lower().startswith(MORROBLIVION_PREFIX)]
    return sorted(names, key=lambda n: (not n.lower().endswith('.esm'),
                                        n.lower()))


def export_plugin(source_path: str, export_dir: str, masters=()) -> dict:
    """Convert one Morrowind plugin into the standard export tree.

    Records land in `record_dir(export_dir, <plugin>)`, the same resolver every
    other stage uses, so nothing downstream needs to know the source was TES3.
    """
    plugin = os.path.basename(source_path)
    ctx = load_context(export_dir, masters)
    records = read_file(source_path)[1]
    ctx.body_models = load_body_models(source_path, records, export_dir)
    own_meshes = assets_for(record_dir(export_dir, plugin)) / 'meshes'
    ctx.morroblivion = MorroblivionModels(
        export_dir, masters, source_path, own_meshes)
    ctx.origin_shifts = OriginShifts(
        export_dir, [own_meshes] + _master_mesh_roots(export_dir, masters))
    out = convert_plugin(records, ctx)
    owned = sum(1 for _f, lines in out.get('CREA', [])
                if any(l.startswith('MorrowindModel') for l in lines))
    if out.get('CREA'):
        print(f"  Creatures: {owned} of {len(out['CREA'])} converted here, "
              f"the rest from a master")
    remapped, shifted, pitched = remap_vanilla_models(out, ctx)
    if remapped:
        print(f'  Morroblivion models: {remapped} vanilla mesh references remapped'
              + (f', {shifted} re-seated' if shifted else '')
              + (f', {pitched} refs axis-pitched' if pitched else ''))
    out_dir = str(record_dir(export_dir, plugin))
    counts = write_export(out, out_dir)
    write_header(out_dir, _master_list(masters), sum(counts.values()),
                 f'Converted from {plugin}')
    return {'plugin': plugin, 'output': out_dir, 'counts': counts,
            'dropped': sum(ctx.unresolved.values()),
            'unlinked_doors': ctx.unlinked_doors}


def _master_mesh_roots(export_dir: str, masters) -> list:
    """Each Morroblivion master's mesh tree, holding the replacement meshes."""
    return [assets_for(record_dir(export_dir, name)) / 'meshes'
            for name, _path in masters
            if name.lower().startswith(MORROBLIVION_PREFIX)]


def _master_list(masters) -> list:
    """The plugin NAMES this export declares as masters, in order.

    Read from the pairs, never from a directory basename: a plugin inside an
    imported mod lives in its GROUP's folder. Skyrim.esm is never listed.
    See: docs/commentary/tes4_export_morrowind.md#masters
    """
    names = []
    for name, _path in masters:
        if name and name not in names:
            names.append(name)
    return names


def export_record(rec, ctx: MorrowindContext) -> list:
    """The KEY=VALUE lines for one Morrowind base record, or [] if unhandled."""
    exporter = EXPORTERS.get(rec.type)
    if exporter is None:
        return []
    lines = exporter(rec, ctx)
    emit_ref(lines, 'SCRI', rec, 'SCRI', ctx, 'SCPT')
    return lines


def convert_plugin(records, ctx: MorrowindContext) -> dict:
    """Every TES4-shaped record one Morrowind plugin becomes.

    Base records are registered first so that a reference is only written once
    its base object is known to exist, and every cell is noted before the
    doors are linked.
    """
    for rec in records:
        if rec.type in EXPORTERS and not rec.deleted:
            ctx.register_own(rec.record_id, tes4_signature(rec))
    _register_land_textures(records, ctx)
    _register_land_grids(records, ctx)
    _register_groundcover(records, ctx)
    register_sound_gens(records, ctx)

    out = {sig: [] for sig in
           ('CELL', 'REFR', 'ACHR', 'ACRE', 'LAND', 'PGRD')}
    out['PGRD'] = pathgrid_records(_collect_records(records, ctx, out), ctx)
    teleport_records(ctx)
    out['PACK'], travel_markers = package_records(ctx)
    for sig in ('NPC_', 'CREA'):
        prune_dropped_packages(out.get(sig, []), ctx)
    out['REFR'].extend(travel_markers)
    out['REFR'].extend(map_marker_records(ctx))
    out['WRLD'] = worldspace_record(ctx)
    out['CELL'].extend(persistent_cell_record(ctx))
    _emit_groundcover(out, ctx)
    return out


def _collect_records(records, ctx: MorrowindContext, out: dict) -> list:
    """Route every record to its output bucket; return the pathgrids.

    A pathgrid is held back rather than converted in place: it names the cell
    it belongs to, so every CELL has to be claimed before one can resolve.
    """
    pathgrids = []
    collectors = {'CELL': lambda rec: _collect_cell(rec, ctx, out),
                  'LAND': lambda rec: out['LAND'].extend(
                      land_records(rec, ctx)),
                  'PGRD': pathgrids.append}
    for rec in records:
        if rec.deleted:
            continue
        collect = collectors.get(rec.type)
        if collect is not None:
            collect(rec)
        elif _is_convertible(rec, ctx):
            _collect_base(rec, ctx, out)
    return pathgrids


def _collect_base(rec, ctx: MorrowindContext, out: dict) -> None:
    """Add one converted base record to the bucket for its TES4 signature."""
    sig = tes4_signature(rec)
    out.setdefault(sig, []).append(
        (ctx.resolve(rec.record_id, sig), export_record(rec, ctx)))


def _emit_groundcover(out: dict, ctx: MorrowindContext) -> None:
    """Turn the tallied placements into GRAS records and LTEX bindings.

    The LTEX records are OVERRIDES of the master's own: a groundcover plugin
    defines no texture itself, and the binding has to land on the texture the
    terrain actually names.
    See: docs/commentary/tes4_export_morrowind.md#groundcover-as-grass
    """
    if ctx.grass is None:
        return
    placed = sum(ctx.grass.pairs.values())
    bindings = ctx.grass.bindings()
    records = grass_records(ctx.grass, ctx.grass_models, ctx.grass_id)
    if not records:
        print('  Groundcover: no grass survived binding; %d placements dropped'
              % (placed + ctx.grass.unplaced))
        return
    out['GRAS'] = records
    out.setdefault('LTEX', [])
    for texture in sorted(bindings):
        lines = ltex_grass_lines(bindings, texture, ctx.grass_id,
                                 ctx.grass_ltex.get(texture.upper()))
        if lines:
            out['LTEX'].append((texture, lines))
    print('  Groundcover: %d placements -> %d GRAS over %d textures '
          '(%d off-terrain)'
          % (placed, len(records), len(bindings), ctx.grass.unplaced))


def map_marker_records(ctx: MorrowindContext) -> list:
    """Every map marker this plugin's interiors imply, as (FormID, lines).

    Markers are persistent references, so they live in the worldspace's dummy
    cell rather than in the grid square they stand on.
    See: docs/commentary/tes4_export_morrowind.md#map-markers
    """
    parent = ctx.persistent_cell_id()
    out = []
    for name, icon, pos in ctx.markers.markers():
        form_id = ctx.derive(f'mapmarker:{name.lower()}')
        out.append((form_id, marker_lines(name, icon, pos, parent)))
    if out:
        ctx.rehomed_persistent += len(out)
        print(f"  Synthesized {len(out)} map markers")
    return out


def _register_land_textures(records, ctx: MorrowindContext) -> None:
    """Map each LTEX's own index to its FormID, before any LAND is written.

    A LAND names its textures by index, so the whole table has to exist
    before the first terrain record is emitted.
    """
    for rec in records:
        if rec.type != 'LTEX' or rec.deleted:
            continue
        intv = get_subrecord(rec, 'INTV')
        if intv is None or len(intv.data) < 4:
            continue
        ctx.register_ltex(struct.unpack_from('<I', intv.data, 0)[0],
                          ctx.resolve(rec.record_id, 'LTEX'))


def _register_groundcover(records, ctx: MorrowindContext) -> None:
    """Note every groundcover static, and open a tally over the master terrain.

    A plugin with no grass statics leaves `ctx.grass` None, which keeps every
    ordinary Morrowind plugin on the untouched path.
    See: docs/commentary/tes4_export_morrowind.md#groundcover-as-grass
    """
    for rec in records:
        if rec.type != 'STAT' or rec.deleted:
            continue
        model = get_subrecord(rec, 'MODL')
        path = '' if model is None else model.data.split(
            bytes(1))[0].decode('cp1252', 'replace')
        if is_grass_model(path):
            ctx.grass_models[rec.record_id] = path
    if not ctx.grass_models:
        return
    grid = {}
    for master, remap in ctx.master_dirs:
        for cell, quads in master_texture_grid(master, remap).items():
            grid.setdefault(cell, quads)
    ctx.grass = GrassTally(grid)
    for master, remap in ctx.master_dirs:
        for fid, edid in master_ltex_fields(master, remap).items():
            ctx.grass_ltex.setdefault(fid, edid)
    print('  Groundcover: %d grass statics over %d textured cells'
          % (len(ctx.grass_models), len(grid)))


def _register_land_grids(records, ctx: MorrowindContext) -> None:
    """Index every LAND's decoded VTEX grid by cell, before any is written.

    A cell's west strip of ground shows its WEST neighbour's textures, so a
    LAND cannot be exported until the grid beside it is known.
    See: docs/commentary/tes4_export_morrowind.md#vtex-is-offset-one-column
    """
    for rec in records:
        if rec.type != 'LAND' or rec.deleted:
            continue
        intv = get_subrecord(rec, 'INTV')
        vtex = get_subrecord(rec, 'VTEX')
        if intv is None or len(intv.data) < 8 or vtex is None:
            continue
        grid = decode_textures(vtex.data)
        if grid:
            ctx.vtex_by_cell[struct.unpack_from('<ii', intv.data, 0)] = grid


def worldspace_record(ctx: MorrowindContext) -> list:
    """The WRLD every exterior cell hangs under, or the master's with new bounds.

    Emitted AFTER the cells so NAM0/NAM9 can span the grid they actually
    cover; the engine builds the worldspace extents and the object-LOD
    quadtree from that rectangle while it parses the file. A plugin whose
    cells stay inside its master's rectangle emits nothing; one that reaches
    past it re-emits the master's WRLD, which the importer applies as an
    override.
    See: docs/commentary/tes4_export_morrowind.md#the-synthetic-worldspace
    """
    min_x, min_y, max_x, max_y = ctx.world_bounds()
    if not ctx.owns_worldspace() and (
            ctx.master_bounds is None
            or (min_x, min_y, max_x, max_y) == ctx.master_bounds):
        return []
    return [(ctx.worldspace_id(),
             [f'EditorID={WORLDSPACE_EDID}', 'FULL=Morrowind',
              f'CNAM.Vanilla={_SKYRIM_CLIMATE}', 'DATA.Flags=0',
              f'NAM0.MinX={min_x}', f'NAM0.MinY={min_y}',
              f'NAM9.MaxX={max_x}', f'NAM9.MaxY={max_y}'])]


def _is_convertible(rec, ctx: MorrowindContext) -> bool:
    """Whether this plugin writes a base record of its own for `rec`."""
    return (rec.type in EXPORTERS
            and marker_formid(rec.record_id) is None
            and ctx.index.lookup(rec.record_id) is None)


def _collect_cell(rec, ctx: MorrowindContext, out: dict) -> None:
    """Add one CELL's cells and placements to the output buckets."""
    cell = parse_cell(rec)
    ctx.note_cell(cell)
    if cell.interior:
        cells, placements = _interior_cell(cell, ctx)
    else:
        cells, placements = _exterior_cells(cell, ctx)
    out['CELL'].extend(cells)
    for signature, form_id, lines in placements:
        out[signature].append((form_id, lines))


def format_record(signature: str, form_id: str, lines: list) -> str:
    """One record as the same delimited block the TES4 exporter writes.

    A `RecordFlags=` line among `lines` supplies the header flags.
    """
    out = ['---RECORD_BEGIN---', f'Signature={signature}',
           f'FormID={form_id}']
    edid = [line for line in lines if line.startswith('EditorID=')]
    if edid:
        out.append(edid[0])
    flags = [line for line in lines if line.startswith('RecordFlags=')]
    out.append(flags[-1] if flags else 'RecordFlags=0')
    out.extend(line for line in lines
               if not line.startswith(('EditorID=', 'RecordFlags=')))
    out.append('---RECORD_END---')
    return '\n'.join(out)


def write_export(out: dict, output_dir: str) -> dict:
    """Write one file per record type; return the counts written.

    A record file left by an earlier export of a type this run no longer
    emits is removed, or its records would be imported beside their
    replacements under the same FormIDs.
    """
    os.makedirs(output_dir, exist_ok=True)
    for name in os.listdir(output_dir):
        stem, ext = os.path.splitext(name)
        if ext == '.txt' and len(stem) == 4 and stem.isupper() \
                and not out.get(stem):
            os.remove(os.path.join(output_dir, name))
    counts = {}
    for signature, records in sorted(out.items()):
        if not records:
            continue
        path = os.path.join(output_dir, f'{signature}.txt')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('\n\n'.join(
                format_record(signature, fid, lines)
                for fid, lines in records) + '\n')
        counts[signature] = len(records)
    return counts


def write_header(output_dir: str, masters: list, num_records: int,
                 description: str = 'Converted from Morrowind') -> None:
    """Write the _HEADER.txt the import stage reads for the master list."""
    lines = ['HEDR.Version=1.0', f'HEDR.NumRecords={num_records}',
             'HEDR.NextObjectID=2048',
             'CNAM.Author=TESConversion',
             f'SNAM.Description={description}']
    lines.extend(f'Master[{i}]={name}' for i, name in enumerate(masters))
    lines.append('Flags=1')
    with open(os.path.join(output_dir, '_HEADER.txt'), 'w',
              encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')


def land_records(rec, ctx: MorrowindContext) -> list:
    """The four Oblivion LAND records one Morrowind LAND becomes.

    Each quadrant carries the 33x33 sub-grid of the source 65x65 field, which
    needs no resampling because vertex spacing is 128 units in both games.
    """
    intv = get_subrecord(rec, 'INTV')
    if intv is None or len(intv.data) < 8:
        return []
    cell_x, cell_y = struct.unpack_from('<ii', intv.data, 0)
    vhgt = get_subrecord(rec, 'VHGT')
    if vhgt is None:
        return []
    heights = decode_heights(vhgt.data)
    vnml = get_subrecord(rec, 'VNML')
    normals = vnml.data if vnml else b''
    textures = _padded_grid(ctx, cell_x, cell_y)

    out = []
    for quad in ((0, 0), (1, 0), (0, 1), (1, 1)):
        grid = (cell_x * 2 + quad[0], cell_y * 2 + quad[1])
        layers = _land_layers(textures, quad, ctx)
        lines = [f'DATA.Flags={_LAND_FLAGS if layers else _LAND_FLAGS_NO_TEX}',
                 'VHGT=' + encode_heights(heights, quad).hex().upper()]
        quad_normals = quadrant_normals(normals, quad)
        if quad_normals:
            lines.append('VNML=' + quad_normals.hex().upper())
        lines.extend(layers)
        lines.append(f'ParentWRLD={ctx.worldspace_id()}')
        lines.append(f'ParentCELL={ctx.exterior_cell_id(grid)}')
        out.append((ctx.land_id(grid), lines))
    return out


def _shifted_grid(ctx: MorrowindContext, cell_x: int, cell_y: int) -> list:
    """One cell's 16x16 VTEX grid as the engine applies it, or []."""
    textures = ctx.vtex_by_cell.get((cell_x, cell_y), [])
    if not textures:
        return []
    return shift_textures(textures, ctx.vtex_by_cell.get((cell_x - 1, cell_y)))


def _padded_grid(ctx: MorrowindContext, cell_x: int, cell_y: int) -> list:
    """A cell's applied grid ringed by its neighbours', or [] with no VTEX."""
    center = _shifted_grid(ctx, cell_x, cell_y)
    if not center:
        return []
    return pad_grid(center, lambda dx, dy: _shifted_grid(
        ctx, cell_x + dx, cell_y + dy))


def _land_layers(textures: list, quad: tuple, ctx: MorrowindContext) -> list:
    """Every texture a TES4 quadrant uses: a dominant BASE plus ALPHA layers.

    Terrain with no base layer is what crashed the game on entering a cell.
    Morrowind carries no blend weight, but it does author which patch uses
    which texture, so the non-dominant ones become alpha layers masked by the
    patches naming them rather than being discarded.
    See: docs/commentary/tes4_export_morrowind.md#terrain-texture-blending
    """
    if not textures:
        return []
    cell = quadrant_textures(textures, quad)
    lines, count = [], 0
    for sub in ((0, 0), (1, 0), (0, 1), (1, 1)):
        patch = sub_patch(cell, sub)
        quadrant = sub[0] + 2 * sub[1]
        for rank, (value, form_id) in enumerate(_ranked_textures(patch, ctx)):
            lines.extend(layer_lines(count, quadrant, form_id, rank,
                                     patch, value))
            count += 1
    return [f'LayerCount={count}'] + lines if count else []


def _ranked_textures(patch: list, ctx: MorrowindContext) -> list:
    """One quadrant's (VTEX value, LTEX FormID) pairs, widest coverage first.

    Textures seen only in the ring come last: they reach the quadrant's edge
    vertices and nothing else. Ties break on the VTEX value so the layer
    order is stable across runs, and the quadrant's layer cap is honoured.
    """
    tally = Counter(v for v in inner_patch(patch) if ctx.land_texture(v))
    ring = sorted({v for v in patch if v not in tally and ctx.land_texture(v)})
    ranked = [value for value, _n in sorted(tally.items(),
                                            key=lambda kv: (-kv[1], kv[0]))]
    return [(value, ctx.land_texture(value))
            for value in (ranked + ring)[:MAX_QUAD_LAYERS]]


def _interior_cell(cell, ctx: MorrowindContext) -> tuple:
    """One interior cell and every placement it holds."""
    form_id = ctx.interior_cell_id(cell.name)
    lines = [f'EditorID={cell.name}', f'FULL={cell.name}', 'DATA.Flags=1']
    if cell.water_height is not None:
        lines.append(f'XCLW.WaterHeight={cell.water_height}')
    return [(form_id, lines)], _emit_refs(cell.refs, form_id, ctx)


def _exterior_cells(cell, ctx: MorrowindContext) -> tuple:
    """The four Oblivion cells one Morrowind exterior cell becomes.

    All four are emitted whether or not a reference falls in them: the cell's
    terrain is split four ways regardless, and a LAND naming a cell that was
    never written is an orphan. Emitting only the quadrants that held a
    reference stranded 1,060 of Morrowind.esm's 5,560 LAND quadrants.
    A reference sitting outside its own cell's quadrants -- which Morrowind
    tolerates -- still adds the grid square it truly falls in.
    """
    buckets = {grid: [] for grid in tes3_cell_quadrants(*cell.grid)}
    for ref in cell.refs:
        if ref.deleted:
            continue
        buckets.setdefault(cell_grid(ref.pos[0], ref.pos[1]), []).append(ref)

    base = cell.name or cell.region or 'Wilderness'
    cells, placements = [], []
    for grid, held in sorted(buckets.items()):
        edid = cell_editor_id(_safe_name(base), grid[0], grid[1])
        form_id = ctx.exterior_cell_id(grid)
        lines = [f'EditorID={edid}', 'DATA.Flags=2',
                 f'XCLC.X={grid[0]}', f'XCLC.Y={grid[1]}',
                 f'ParentWRLD={ctx.worldspace_id()}']
        if cell.name:
            lines.append(f'FULL={cell.name}')
        if not ctx.claim_exterior(grid):
            cells.append((form_id, lines))
        placements.extend(_emit_refs(held, form_id, ctx))
    return cells, placements


def _emit_refs(refs, parent_cell: str, ctx: MorrowindContext) -> list:
    """Every emittable placement in one cell, as (signature, FormID, lines).

    Keyed on the owning cell's own FormID plus the placement's index, which is
    unique because cell ids already are -- a cell NAME is not, since Morrowind
    leaves most exterior cells unnamed. A placed NPC is an ACHR and a placed
    creature an ACRE; everything else, leveled creatures included, is a REFR.
    See: docs/commentary/tes4_export_morrowind.md#actors-and-placements
    """
    out = []
    for ref in refs:
        if ref.deleted:
            continue
        if ctx.grass is not None and ref.record_id in ctx.grass_models:
            ctx.grass.add(ref.record_id, ref.pos, ref.scale)
            continue
        lines = _ref_lines(ref, parent_cell, ctx)
        if not lines:
            continue
        form_id = ctx.next_ref_id(parent_cell)
        signature = _PLACEMENT_SIGNATURES.get(
            ctx.base_signature(ref.record_id), 'REFR')
        if ctx.base_signature(ref.record_id) == 'DOOR':
            ctx.doors_by_cell.setdefault(parent_cell, []).append(
                (form_id, ref.pos))
        if ref.teleport and ref.dest_pos and signature == 'REFR':
            ctx.pending_teleports.append((form_id, lines, ref))
        elif signature != 'REFR':
            ctx.placements.setdefault(ref.record_id.lower(), []).append(
                (form_id, parent_cell))
        out.append((signature, form_id, lines))
    return out


def _safe_name(name: str) -> str:
    """A cell name reduced to the alphanumerics an EditorID allows."""
    return ''.join(ch for ch in name if ch.isalnum()) or 'Wilderness'


def _placement_lines(pos: tuple, rot: tuple, prefix: str = '') -> list:
    """Position and rotation keys, optionally under the XTEL prefix."""
    return [f'{prefix}PosX={pos[0]}', f'{prefix}PosY={pos[1]}',
            f'{prefix}PosZ={pos[2]}', f'{prefix}RotX={rot[0]}',
            f'{prefix}RotY={rot[1]}', f'{prefix}RotZ={rot[2]}']


def _ref_lines(ref, parent_cell: str, ctx: MorrowindContext):
    """One placed reference, or None when its base object cannot resolve.

    A reference whose base record is missing crashes the engine, so an
    unresolvable one is dropped and counted rather than emitted.
    """
    base = ctx.resolve(ref.record_id)
    if not base:
        ctx.unresolved[ref.record_id] += 1
        return None
    lines = [f'NAME={base}', f'ParentCELL={parent_cell}']
    lines.extend(_placement_lines(ref.pos, ref.rot))
    if ref.scale != 1.0:
        lines.append(f'XSCL.Scale={ref.scale}')
    owner = ctx.resolve(ref.owner) or ctx.resolve(ref.faction)
    if owner:
        lines.append(f'XOWN.Owner={owner}')
    if ref.lock_level > 0:
        lines.append(f'XLOC.Level={min(ref.lock_level, 100)}')
        key = ctx.resolve(ref.key)
        if key:
            lines.append(f'XLOC.Key={key}')
    return lines


def _rehome_persistent(lines: list, ctx: MorrowindContext) -> None:
    """Move a persistent reference out of its exterior grid cell, in place.

    A worldspace keeps every persistent reference in one dummy cell; the
    engine loads them by worldspace rather than by grid, so a persistent ref
    filed under the grid cell it stands in is never attached and never drawn.
    Interior refs are already in the only cell they can be in.
    See: docs/commentary/tes4_export_morrowind.md#teleport-doors
    """
    parent = [line for line in lines if line.startswith('ParentCELL=')]
    if not parent or parent[-1][len('ParentCELL='):] not in ctx.exterior_cell_ids:
        return
    lines[:] = [line for line in lines if not line.startswith('ParentCELL=')]
    lines.append(f'ParentCELL={ctx.persistent_cell_id()}')
    ctx.rehomed_persistent += 1


def persistent_cell_record(ctx: MorrowindContext) -> list:
    """The worldspace's dummy cell, when any persistent reference needs it.

    It carries no grid: the importer files a cell flagged persistent directly
    under the worldspace group rather than in the block tree.

    A MASTER's cell is re-emitted as an override rather than skipped. The
    plugin's own rehomed references name it, so a plugin that leaves it out
    parents them to a cell no record defines.
    See: docs/commentary/tes4_export_morrowind.md#teleport-doors
    """
    if not ctx.rehomed_persistent:
        return []
    return [(ctx.persistent_cell_id(),
             [f'EditorID={WORLDSPACE_EDID}Persistent', 'DATA.Flags=2',
              f'ParentWRLD={ctx.worldspace_id()}',
              f'RecordFlags={_PERSISTENT}'])]


def _partner_door(ctx: MorrowindContext, cell: str, pos: tuple,
                  door_id: str) -> str:
    """The door reference standing at `pos` in `cell`, or '' if none is near.

    Candidates are this plugin's placements AND its masters', since a
    dependent plugin's door usually arrives in a cell the master owns.
    See: docs/commentary/tes4_export_morrowind.md#teleport-doors
    """
    best, best_dist = '', _PARTNER_RADIUS
    for form_id, other in ctx.doors_by_cell.get(cell, ()):
        if form_id == door_id:
            continue
        dist = sum((a - b) ** 2 for a, b in zip(other, pos)) ** 0.5
        if dist < best_dist:
            best, best_dist = form_id, dist
    return best


def teleport_records(ctx: MorrowindContext) -> list:
    """Point every load door's XTEL at the door reference it arrives beside.

    Skyrim's XTEL names a destination REFERENCE and vanilla's is another DOOR
    in 1,703 of 1,722 cases -- never once an XMarker, which is what an earlier
    pass minted. A door whose destination cell is not converted, or which has
    no partner door there, stays a plain door and is counted.
    See: docs/commentary/tes4_export_morrowind.md#teleport-doors
    """
    for door_id, lines, ref in ctx.pending_teleports:
        cell = ctx.destination_cell(ref)
        partner = (_partner_door(ctx, cell, ref.dest_pos, door_id)
                   if cell else '')
        if not partner:
            ctx.unlinked_doors += 1
            continue
        rot = ref.dest_rot or (0.0, 0.0, 0.0)
        lines.append(f'XTEL.Door={partner}')
        lines.extend(_placement_lines(ref.dest_pos, rot, 'XTEL.'))
        lines.append(f'RecordFlags={_PERSISTENT}')
        _rehome_persistent(lines, ctx)

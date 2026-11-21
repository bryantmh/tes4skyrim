"""
Morrowind groundcover plugins as real Skyrim grass.

Morrowind has no grass record. Groundcover mods place one static per clump --
2.73M of them in Aesthesia's Tamriel Rebuilt set -- because the engine offers
nothing else. Converting those literally yields millions of STAT references
Skyrim renders individually, with no instancing, LOD or wind.

Skyrim instead scatters grass procedurally per LANDSCAPE TEXTURE: a GRAS record
names the model, and an LTEX names the grasses that grow on it. So the
placements are inverted into that model -- sample the land texture under every
clump, tally which grasses grow on which texture, and emit the GRAS plus the
LTEX bindings that reproduce the distribution.

The terrain being sampled is never the plugin's own: a groundcover plugin
ships cells and statics only, never LAND, and its statics may be a master's.

See: docs/commentary/tes4_export_morrowind.md#groundcover-as-grass
"""

import math
import os
import struct
from collections import Counter, defaultdict

from core.plugin_masters import (export_source, is_master_export,
                                 master_chain, masters_from_export_header)
from output_layout import record_dir

from .morrowind_cell import FLAG_INTERIOR, parse_cell
from .morrowind_ids import remap_form_id
from .morrowind_world import TES4_CELL_SIZE, tes3_cell_quadrants
from .record_types.common import escape_value
from .tes3_reader import get_string, get_subrecord

#: Model-path prefix every groundcover static shares -- the authored marker.
GRASS_MODEL_PREFIX = 'grass' + chr(92)

#: Vertices per side of one TES4 LAND quadrant's alpha grid.
QUAD_VERTS = 17

#: Vertices in one TES4 LAND quadrant's alpha grid.
QUAD_VERTS_TOTAL = QUAD_VERTS * QUAD_VERTS

#: Skyrim saturates grass density well below the record byte's 0-255.
MAX_DENSITY = 100

#: Side of the block the planter fills at once (two vertex spacings), and blocks per quad side.
PLANTER_BLOCK = 256.0
BLOCKS_PER_QUAD_SIDE = 8

#: iMinGrassSize in Skyrim.ini; the planter caps its grid step at it.
SKYRIM_MIN_GRASS_SIZE = 20.0

#: 3 candidates per block side; 256/3 truncates to 2 in the engine's float32, and convert_GRAS floors at 80.
POSITION_RANGE = 85.0

#: ColorRange and WavePeriod every vanilla grass sets; Morrowind has neither.
VANILLA_COLOR_RANGE = 0.2
VANILLA_WAVE_PERIOD = 120.0

#: The slope ceiling vanilla grass uses; 90 would grow it up cliff faces.
_MAX_SLOPE = 45

#: Placements a (texture, model) pairing needs before it counts as authored.
MIN_PAIR_SUPPORT = 8

#: Grasses the planter takes per texture at the default ini: the cap is 2 and the loop's test is `jg`, so 3.
MAX_GRASSES_PER_TEXTURE = 3

#: Keys an LTEX override never repeats; a splice of these re-mints the TXST.
_LTEX_OWN_KEYS = frozenset({'Signature', 'FormID', 'RecordFlags',
                            'ICON', 'TextureIndex'})

#: Scales kept per model to characterise its spread.
_SCALE_SAMPLE = 4096

#: Uniform Scaling + Fit to Slope, the flags all 27 vanilla GRAS records set.
_GRAS_FLAGS = 6

#: Slope floor vanilla grass accepts, in degrees.
_MIN_SLOPE = 0

#: HeightRange ceiling: a draw of -1 must still leave the instance a tenth of its size.
_MAX_HEIGHT_RANGE = 0.9


def is_grass_model(model: str) -> bool:
    """Whether a static's model path marks it as groundcover."""
    return bool(model) and model.replace('/', chr(92)).lower().startswith(
        GRASS_MODEL_PREFIX)


def record_blocks(path: str):
    """Each record in an export dump as a dict of its scalar fields.

    Bulk vertex arrays are skipped by prefix rather than parsed: they are ~97%
    of a LAND dump's bytes and name no texture.
    """
    if not os.path.isfile(path):
        return
    fields = {}
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if line.startswith(('VHGT', 'VNML', 'VCLR')):
                continue
            if line.startswith('---RECORD_END---'):
                if fields:
                    yield fields
                fields = {}
                continue
            key, sep, value = line.rstrip('\n').partition('=')
            if sep:
                fields[key] = value


def cell_grids(export_dir: str) -> dict:
    """Exterior cell FormID -> its (X, Y) grid, from one export's CELL dump."""
    out = {}
    for rec in record_blocks(os.path.join(export_dir, 'CELL.txt')):
        x, y = rec.get('XCLC.X'), rec.get('XCLC.Y')
        if x is not None and y is not None and rec.get('FormID'):
            out[rec['FormID'].upper()] = (int(x), int(y))
    return out


def _vertex_opacity(rec: dict, layer: int) -> dict:
    """{vertex index: opacity} of every vertex an ALPHA layer paints."""
    out = {}
    for j in range(int(rec.get('Layer[%d].VTXTCount' % layer, 0) or 0)):
        pos = int(rec.get('Layer[%d].VT[%d].Pos' % (layer, j), 0) or 0)
        out[pos] = float(rec.get('Layer[%d].VT[%d].Opacity' % (layer, j), 0) or 0)
    return out


def _read_land_layers(rec: dict) -> dict:
    """One LAND record's layers: each quadrant's BASE, and its ALPHA vertex opacities."""
    out = {}
    for i in range(int(rec.get('LayerCount', 0) or 0)):
        kind = rec.get('Layer[%d].Type' % i, '')
        if kind == 'BASE':
            quad = int(rec.get('Layer[%d].BTXT.Quadrant' % i, 0) or 0)
            out['BTXT.%d' % quad] = rec.get('Layer[%d].BTXT.Texture' % i, '')
        elif kind == 'ALPHA':
            quad = int(rec.get('Layer[%d].ATXT.Quadrant' % i, 0) or 0)
            out.setdefault('ATXT.%d' % quad, []).append(
                (rec.get('Layer[%d].ATXT.Texture' % i, ''),
                 _vertex_opacity(rec, i)))
    return out


def _vertex_share(pos: int) -> float:
    """A vertex's share of its quad's 8x8 planter blocks: 1/4, 1/8 on an edge."""
    x, y = pos % QUAD_VERTS, pos // QUAD_VERTS
    edges = (x in (0, QUAD_VERTS - 1)) + (y in (0, QUAD_VERTS - 1))
    return (4, 2, 1)[edges] / (16.0 * BLOCKS_PER_QUAD_SIDE * BLOCKS_PER_QUAD_SIDE)


def _quadrant_weights(layers: dict, quadrant: int) -> tuple:
    """({LTEX: blend share}, {LTEX: planted share}) of one quadrant.

    The blend share picks the ground a clump stands on: an ALPHA counts for
    its mean opacity, the BASE for what the alphas leave. The planted share
    is the engine's: at fTexturePctThreshold 0 a vertex gets the FULL
    density wherever its texture shows at all, weighed by `_vertex_share`.
    See: docs/commentary/tes4_export_morrowind.md#groundcover-density
    """
    blend, shown, covered = {}, defaultdict(set), Counter()
    base = layers.get('BTXT.%d' % quadrant, '')
    if base:
        blend[base] = 1.0
    for texture, verts in layers.get('ATXT.%d' % quadrant, ()):
        share = min(1.0, sum(verts.values()) / QUAD_VERTS_TOTAL)
        blend[texture] = blend.get(texture, 0.0) + share
        if base:
            blend[base] = max(0.0, blend[base] - share)
        shown[texture].update(p for p, op in verts.items() if op > 0)
        covered.update(verts)
    if base:
        shown[base].update(p for p in range(QUAD_VERTS_TOTAL)
                           if covered[p] < 1.0 - 1e-6)
    planted = {t: sum(_vertex_share(p) for p in ps) for t, ps in shown.items()}
    return blend, planted


def _texture_key(form_id: str, owners: list, allowed: dict) -> str:
    """`<file>|<low 24 bits>` naming one LTEX, or '' when its file is not bindable.

    `owners` names the file behind each load-order byte of the export the id
    comes from; `allowed` maps each bindable file, lower-cased, to its name.
    """
    try:
        raw = int(form_id, 16)
    except ValueError:
        return ''
    slot = raw >> 24
    name = allowed.get(owners[slot].lower()) if slot < len(owners) else None
    return '%s|%06X' % (name, raw & 0xFFFFFF) if name else ''


def _keyed_weights(weights: dict, owners: list, allowed: dict) -> dict:
    """One quadrant's {LTEX FormID: weight} re-keyed by `_texture_key`."""
    out = Counter()
    for texture, weight in weights.items():
        out[_texture_key(texture, owners, allowed)] += weight
    return dict(out)


def land_texture_grid(export_dir: str, grids: dict, owners: list,
                      allowed: dict) -> dict:
    """TES4 cell grid -> ([dominant texture per quadrant], [{texture: planted share}]).

    Reads one export's LAND dump, already split into Oblivion cells, so no
    VTEX de-swizzling is needed. `grids` is that export's `cell_grids`.
    Textures are keyed by `_texture_key`, naming the file the binding has to
    override; one no bindable file owns becomes ''.
    See: docs/commentary/tes4_export_morrowind.md#groundcover-as-grass
    """
    out = {}
    for rec in record_blocks(os.path.join(export_dir, 'LAND.txt')):
        grid = grids.get(rec.get('ParentCELL', '').upper())
        if grid is None:
            continue
        layers = _read_land_layers(rec)
        quads, planted = [], []
        for q in range(4):
            blend, shares = _quadrant_weights(layers, q)
            blend = _keyed_weights(blend, owners, allowed)
            quads.append(max(blend, key=blend.get) if blend else '')
            planted.append(_keyed_weights(shares, owners, allowed))
        if any(quads):
            out[grid] = (quads, planted)
    return out


def master_ltex_records(master_dirs) -> dict:
    """LTEX FormID, re-keyed into this plugin -> that master record's fields.

    `master_dirs` is `ctx.master_dirs`; the first master to supply an id wins.
    """
    out = {}
    for path, remap in master_dirs:
        for rec in record_blocks(os.path.join(path, 'LTEX.txt')):
            fid = remap_form_id(rec.get('FormID', ''), remap)
            if fid:
                out.setdefault(fid.upper(), rec)
    return out


def _owner_ltex_fields(export_root: str, keys) -> dict:
    """Texture key -> the owner's LTEX fields an override repeats verbatim.

    Everything the diff must NOT see is left out (`_LTEX_OWN_KEYS`, plus the
    owner's own grass -- repeating a key makes the reader fold both values
    into a LIST and the diff cannot order it), so the only authored change is
    the grass run and the owner's own TNAM survives untouched.
    See: docs/commentary/tes4_export_morrowind.md#groundcover-as-grass
    """
    out = {}
    for owner in sorted({key.split('|')[0] for key in keys}):
        path = str(record_dir(export_root, owner))
        own = '%02X' % len(masters_from_export_header(path))
        for rec in record_blocks(os.path.join(path, 'LTEX.txt')):
            fid = rec.get('FormID', '').upper()
            key = '%s|%s' % (owner, fid[2:])
            if fid.startswith(own) and key in keys:
                out[key] = {k: v for k, v in rec.items()
                            if k not in _LTEX_OWN_KEYS
                            and not k.startswith('Grass')}
    return out


def _sample(grid_map: dict, pos_x: float, pos_y: float) -> str:
    """The texture key of the ground under one world position, or ''."""
    grid = (int(pos_x // TES4_CELL_SIZE), int(pos_y // TES4_CELL_SIZE))
    quads = grid_map.get(grid, ((), ()))[0]
    if not quads:
        return ''
    half = TES4_CELL_SIZE / 2.0
    local_x = pos_x - grid[0] * TES4_CELL_SIZE
    local_y = pos_y - grid[1] * TES4_CELL_SIZE
    return quads[(0 if local_x < half else 1) + (0 if local_y < half else 2)]


class GrassTally:
    """Groundcover placements, accumulated per (land texture, grass model).

    Fed one reference at a time during the cell walk, so the 2.73M placements
    are never held in memory at once.
    """

    def __init__(self, grid_map: dict, models: dict):
        """Start empty over a texture grid keyed by `_texture_key`.

        `models` maps each lower-cased grass static id to its escaped model.
        """
        self.grid_map = grid_map
        self.models = models
        self.area = Counter()
        for _quads, weights in grid_map.values():
            for per_quad in weights:
                for tex, w in per_quad.items():
                    if tex:
                        self.area[tex] += w
        self.pairs = Counter()
        self.scales = defaultdict(list)
        self.unplaced = 0
        self._bindings = None

    def absorb(self, ref) -> bool:
        """Tally one placed reference as grass; False keeps it a static.

        A clump over no ground this plugin can bind stays the authored static.
        """
        model_key = ref.record_id.lower()
        if model_key not in self.models:
            return False
        texture = _sample(self.grid_map, ref.pos[0], ref.pos[1])
        if not texture:
            self.unplaced += 1
            return False
        self.pairs[(texture, model_key)] += 1
        if len(self.scales[model_key]) < _SCALE_SAMPLE:
            self.scales[model_key].append(ref.scale)
        return True

    def bindings(self) -> dict:
        """Texture key -> {model: clumps it plants} on that texture.

        The engine plants only the first MAX_GRASSES_PER_TEXTURE grasses a
        texture names, so the commonest models on a texture are kept and
        absorb the rest of its clumps in proportion: the texture ends up as
        thick as the author made it, with its dominant grasses.
        See: docs/commentary/tes4_export_morrowind.md#groundcover-as-grass
        """
        if self._bindings is not None:
            return self._bindings
        by_texture = defaultdict(Counter)
        for (texture, model), count in self.pairs.items():
            by_texture[texture][model] = count
        out = {}
        for texture, counts in by_texture.items():
            kept = [(m, c) for m, c in counts.most_common(
                MAX_GRASSES_PER_TEXTURE) if c >= MIN_PAIR_SUPPORT]
            share = sum(c for _, c in kept)
            if not share:
                continue
            total = sum(counts.values())
            planted = {m: c * total / share for m, c in kept}
            planted = {m: c for m, c in planted.items()
                       if self._density(texture, c)}
            if planted:
                out[texture] = planted
        self._bindings = out
        return self._bindings

    def _per_quad(self, texture: str, planted: float) -> float:
        """Clumps per fully planted LAND quad of one texture."""
        return max(0.01, planted / max(0.01, self.area[texture]))

    def _density(self, texture: str, planted: float) -> int:
        """The GRAS density planting `planted` clumps; 0 when too few to express.

        The planter fills each quad as 8x8 blocks of 256 units, laying
        floor(256/PositionRange) candidates per block side (capped by
        iMinGrassSize) and keeping each with probability Density%, so
        matching the source count is a division. A pairing that rounds to 0
        is dropped rather than planted at 1, which over-plants it 2-28x.
        See: docs/commentary/asset_convert_terrain.md#grass-placement-parity
        """
        side = BLOCKS_PER_QUAD_SIDE * math.floor(min(
            PLANTER_BLOCK / POSITION_RANGE, PLANTER_BLOCK / SKYRIM_MIN_GRASS_SIZE))
        share = self._per_quad(texture, planted) / (side * side) * MAX_DENSITY
        return min(MAX_DENSITY, round(share))

    def density(self, texture: str, model_key: str) -> int:
        """Density for one bound pairing; never 0, those are not bound."""
        return self._density(texture, self.bindings()[texture][model_key])

    def height_range(self, model_key: str) -> float:
        """HeightRange matching the spread of one model's authored scales.

        The planter scales an instance by `1 + HeightRange * rand(-1..1)`, so
        the authored deviation relative to the mean maps through the uniform
        draw's sqrt(3) and every instance keeps a positive scale.
        See: docs/commentary/asset_convert_terrain.md#grass-placement-parity
        """
        values = self.scales.get(model_key) or [1.0]
        mean = sum(values) / len(values)
        spread = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        return min(_MAX_HEIGHT_RANGE, math.sqrt(3.0) * spread / mean)


def grass_records(tally: GrassTally, resolve, form_ids: dict) -> list:
    """The GRAS records one plugin's groundcover implies, as (FormID, lines).

    One record per (texture, model) pairing, so each texture carries the
    density its author gave it. `resolve(model, texture)` mints the FormID,
    keyed on the authored ids so they hold still across runs; `form_ids`
    maps each texture key to its LTEX FormID.
    """
    out = []
    for texture in sorted(tally.bindings()):
        for model_key in sorted(tally.bindings()[texture]):
            out.append((resolve(model_key, texture), [
                f'EditorID={model_key}_{form_ids[texture]}',
                f'Model.MODL={tally.models[model_key]}',
                f'DATA.Density={tally.density(texture, model_key)}',
                f'DATA.MinSlope={_MIN_SLOPE}',
                f'DATA.MaxSlope={_MAX_SLOPE}',
                'DATA.UnitFromWaterAmount=0',
                'DATA.UnitFromWaterType=0',
                f'DATA.PositionRange={POSITION_RANGE:.6f}',
                f'DATA.HeightRange={tally.height_range(model_key):.6f}',
                f'DATA.ColorRange={VANILLA_COLOR_RANGE:.6f}',
                f'DATA.WavePeriod={VANILLA_WAVE_PERIOD:.6f}',
                f'DATA.Flags={_GRAS_FLAGS}',
            ]))
    return out


def ltex_grass_lines(bindings: dict, texture: str, resolve,
                     fields: dict = None) -> list:
    """One land texture re-emitted with its grasses bound.

    `fields` is the master's own record, repeated verbatim so the override
    changes the grass run and nothing else.
    """
    grasses = sorted(bindings.get(texture) or ())
    if not grasses:
        return []
    lines = [f'{k}={v}' for k, v in (fields or {}).items()]
    lines.append(f'GrassCount={len(grasses)}')
    lines.extend(f'Grass[{i}]={resolve(model, texture)}'
                 for i, model in enumerate(grasses))
    return lines


#: The `Source=` a converted Morrowind plugin's export header carries.
_TES3_SOURCE = 'TES3'


def _ref_bases(rec) -> set:
    """Lower-cased base id of every reference one CELL record holds."""
    out, in_ref = set(), False
    for sub in rec.subrecords:
        if sub.type == 'FRMR':
            in_ref = True
        elif in_ref and sub.type == 'NAME':
            out.add(get_string(sub).lower())
            in_ref = False
    return out


def _placed_bases(records) -> tuple:
    """(lower-cased base ids placed in exterior cells, the TES4 grids they cover)."""
    ids, grids = set(), set()
    for rec in records:
        if rec.type != 'CELL' or rec.deleted:
            continue
        data = get_subrecord(rec, 'DATA')
        if data is None or len(data.data) < 12:
            continue
        flags, grid_x, grid_y = struct.unpack_from('<iii', data.data, 0)
        if not flags & FLAG_INTERIOR:
            grids.update(tes3_cell_quadrants(grid_x, grid_y))
            ids.update(_ref_bases(rec))
    return ids, grids


def master_grass_models(master_dirs) -> dict:
    """Master STAT FormID, re-keyed into this plugin -> its escaped grass model."""
    out = {}
    for path, remap in master_dirs:
        for rec in record_blocks(os.path.join(path, 'STAT.txt')):
            model = rec.get('Model.MODL', '')
            fid = remap_form_id(rec.get('FormID', ''), remap)
            if fid and is_grass_model(model):
                out.setdefault(fid.upper(), model)
    return out


def _grass_models(records, ctx, placed: set) -> dict:
    """Lower-cased id -> escaped model of each grass static this plugin places.

    An id a master supplies is the master's static, as `ctx.resolve` has it.
    """
    own = {}
    for rec in records:
        model = get_subrecord(rec, 'MODL') if rec.type == 'STAT' else None
        if model is not None and not rec.deleted:
            own[rec.record_id.lower()] = escape_value(get_string(model))
    masters = master_grass_models(ctx.master_dirs)
    out = {}
    for key in placed:
        fid = ctx.index.lookup(key)
        path = masters.get(fid, '') if fid else own.get(key, '')
        if is_grass_model(path):
            out[key] = path
    return out


def _landmass_exports(export_root: str, skip: set) -> list:
    """Every converted Morrowind ESM's record folder under `export_root`."""
    out = []
    for top in sorted(os.listdir(export_root)):
        base = os.path.join(export_root, top)
        if os.path.isdir(base):
            out.extend(path for path in [base] + [
                os.path.join(base, n) for n in sorted(os.listdir(base))]
                if _is_landmass(path, skip))
    return out


def _is_landmass(path: str, skip: set) -> bool:
    """Whether `path` is a converted Morrowind ESM's export, not one in `skip`."""
    return (os.path.isfile(os.path.join(path, '_HEADER.txt'))
            and os.path.normcase(os.path.abspath(path)) not in skip
            and export_source(path) == _TES3_SOURCE
            and is_master_export(path))


def _norm(path) -> str:
    """A folder path compared case- and separator-blind."""
    return os.path.normcase(os.path.abspath(str(path)))


def _bindable_files(ctx) -> dict:
    """Lower-cased name -> name of each file whose LTEX this plugin may bind.

    Its masters and, transitively, theirs: an override of a master's master
    only needs that file added to the master list.
    """
    out = {}
    for name in ctx.master_names:
        for each in master_chain(record_dir(ctx.export_root, name)) + [name]:
            out.setdefault(each.lower(), each)
    return out


def _overlapping_land(path: str, needed: set, names: dict,
                      allowed: dict) -> dict:
    """One export's texture grid when any of its cells is in `needed`, else {}.

    `names` maps a bindable file's normalized record folder to its name.
    """
    grids = cell_grids(path)
    if needed.isdisjoint(grids.values()):
        return {}
    owners = masters_from_export_header(path) + [names.get(_norm(path), '')]
    return land_texture_grid(path, grids, owners, allowed)


def groundcover_grid(ctx, needed: set) -> dict:
    """TES4 grid -> the textures under it, for the `needed` cells' land.

    A master's land wins wherever it has any. Elsewhere the ground is any
    other converted Morrowind ESM's; two naming different textures leave the
    cell unknown, so its clumps stay statics. Only a texture `_bindable_files`
    owns can carry grass.
    See: docs/commentary/tes4_export_morrowind.md#groundcover-as-grass
    """
    allowed = _bindable_files(ctx)
    names = {_norm(record_dir(ctx.export_root, n)): n for n in allowed.values()}
    masters = [path for path, _remap in ctx.master_dirs]
    grid = {}
    for path in masters:
        for cell, quads in _overlapping_land(path, needed, names, allowed).items():
            grid.setdefault(cell, quads)
    claims = defaultdict(list)
    skip = {_norm(p) for p in [ctx.own_dir] + masters}
    for path in _landmass_exports(ctx.export_root, skip):
        rest = needed - grid.keys()
        for cell, quads in _overlapping_land(path, rest, names, allowed).items():
            if cell not in grid:
                claims[cell].append(quads)
    for cell, found in claims.items():
        if all(quads[0] == found[0][0] for quads in found):
            grid[cell] = found[0]
    return grid


def _ground_owners(records, tally: GrassTally) -> set:
    """Every file owning a texture under one of this plugin's grass clumps."""
    out = set()
    for rec in records:
        if rec.type != 'CELL' or rec.deleted:
            continue
        cell = parse_cell(rec)
        if cell.interior:
            continue
        for ref in cell.refs:
            if not ref.deleted and ref.record_id.lower() in tally.models:
                out.add(_sample(tally.grid_map, ref.pos[0], ref.pos[1]))
    return {key.split('|')[0] for key in out if key}


def register_groundcover(records, ctx) -> set:
    """Open a tally over the land under this plugin's grass; the files owning it.

    Every file whose texture lies under a clump must be a master before any
    FormID is minted, so the binding can override it. A plugin placing no
    grass leaves `ctx.grass` None and returns nothing.
    See: docs/commentary/tes4_export_morrowind.md#groundcover-as-grass
    """
    placed, needed = _placed_bases(records)
    models = _grass_models(records, ctx, placed)
    if not models:
        return set()
    grid = groundcover_grid(ctx, needed)
    ctx.grass = GrassTally(grid, models)
    print('  Groundcover: %d grass statics over %d textured cells'
          % (len(models), len(grid)))
    return _ground_owners(records, ctx.grass)


def grass_master_list(export_root: str, names: list, owners) -> list:
    """`names` with each file of `owners` it lacks, ahead of the master inheriting it."""
    extra = {o.lower() for o in owners} - {n.lower() for n in names}
    out = []
    for name in names:
        for each in master_chain(record_dir(export_root, name)):
            if each.lower() in extra and each.lower() not in {
                    n.lower() for n in out}:
                out.append(each)
        out.append(name)
    return out


def _texture_form_id(key: str, names: list) -> str:
    """The LTEX FormID a texture key names in a plugin with master list `names`."""
    owner, low = key.split('|')
    return '%02X%s' % ([n.lower() for n in names].index(owner.lower()), low)


def emit_groundcover(out: dict, ctx) -> None:
    """Turn the tallied placements into GRAS records and LTEX bindings.

    The LTEX records are OVERRIDES of the owner's own: the binding has to land
    on the texture the terrain actually names.
    See: docs/commentary/tes4_export_morrowind.md#groundcover-as-grass
    """
    tally = ctx.grass
    if tally is None:
        return
    placed = sum(tally.pairs.values())
    bindings = tally.bindings()
    form_ids = {key: _texture_form_id(key, ctx.master_names) for key in bindings}
    records = grass_records(tally, ctx.grass_id, form_ids)
    if not records:
        print('  Groundcover: no grass survived binding; %d placements dropped, '
              '%d off known ground kept as statics' % (placed, tally.unplaced))
        return
    fields = _owner_ltex_fields(ctx.export_root, bindings)
    out['GRAS'] = records
    out.setdefault('LTEX', []).extend(
        (form_ids[key], ltex_grass_lines(bindings, key, ctx.grass_id,
                                         fields.get(key)))
        for key in sorted(bindings))
    print('  Groundcover: %d placements -> %d GRAS over %d textures '
          '(%d off known ground, kept as statics)'
          % (placed, len(records), len(bindings), tally.unplaced))

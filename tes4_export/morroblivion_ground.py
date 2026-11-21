"""
Morroblivion's painted ground on a hybrid plugin's land, and the Morroblivion record behind each land texture.

Morroblivion repainted the vanilla landmass (its towns use NoGrass textures),
so a vanilla plugin's LAND takes Morroblivion's texture layers wherever
Morroblivion has land on the same grid. Each land texture is paired with the
Morroblivion LTEX painted on the same vertices, which decides its grass; the
pairs are written beside the plugin's records for its companion and for other
plugins naming the same texture.

See: docs/commentary/tes4_export_morrowind.md#hybrid-ground
"""

import os
from collections import Counter, defaultdict

from core.plugin_masters import masters_from_export_header
from output_layout import record_dir
from tes5_import.base.text_reader import parse_export_file

from .morroblivion import archive_path
from .morrowind_grass import cell_grids, record_blocks
from .morrowind_patch import patch_masters
from .morrowind_world import WORLDSPACE_EDID
from .record_types.common import escape_value

#: Beside a hybrid plugin's records: `<own LTEX EditorID>=<owner plugin>|<FormID in the owner's export>`.
GROUND_NAME = 'morroblivion_ground.txt'

#: The vanilla master whose pairs other plugins' same-named land textures take.
VANILLA_ESM = 'Morrowind.esm'

#: A layer naming no LTEX: the engine's default ground, kept as written.
NO_TEXTURE = '00000000'

#: Vertices in one quadrant's 17x17 alpha grid.
QUAD_VERTICES = 289

#: LTEX fields a minted texture leaves out: the writer emits the head, the companion the grass.
_SKIP_FIELDS = ('Signature', 'FormID', 'Grass')


def icon_stem(icon: str) -> str:
    """A land texture's file stem, lower case."""
    return os.path.splitext(archive_path(icon).rsplit(chr(92), 1)[-1])[0]


def twin_stem(icon: str) -> str:
    """The stem of Morroblivion's renamed copy of vanilla land texture `icon`."""
    return icon_stem(icon).replace('_', 'u')


def _resolve(form_id: str, names: list) -> str:
    """`owner|FormID` of an id in an export whose load order is `names`, or ''."""
    if len(form_id) != 8 or int(form_id[:2], 16) >= len(names):
        return ''
    return f'{names[int(form_id[:2], 16)]}|{form_id.upper()}'


def _load_order(folder) -> list:
    """An export's masters then itself, by name."""
    return masters_from_export_header(str(folder)) + [os.path.basename(str(folder))]


class MorroblivionGround:
    """Morroblivion's land textures and grasses, keyed `owner|FormID`, read once."""

    def __init__(self, export_root: str, exports):
        """Read every LTEX and GRAS of the Morroblivion plugins and their masters."""
        self.export_root, self.exports = export_root, list(exports)
        self.ltex, self.gras, self.by_stem = {}, {}, defaultdict(list)
        for name in patch_masters(export_root, exports):
            folder = record_dir(export_root, name)
            names = _load_order(folder)
            for rec in parse_export_file(os.path.join(folder, 'GRAS.txt')):
                self.gras[_resolve(rec.get('FormID', ''), names)] = rec
            for rec in parse_export_file(os.path.join(folder, 'LTEX.txt')):
                key = _resolve(rec.get('FormID', ''), names)
                rec['Grass'] = [_resolve(rec.get(f'Grass[{i}]', ''), names)
                                for i in range(int(rec.get('GrassCount') or 0))]
                self.ltex[key] = rec
                self.by_stem[icon_stem(rec.get('ICON', ''))].append(key)

    def grasses(self, key: str) -> list:
        """The GRAS records Morroblivion LTEX `key` lists, in order."""
        return [self.gras[g] for g in self.ltex.get(key, {}).get('Grass', ()) if g in self.gras]

    def lands(self) -> dict:
        """(X, Y) -> Morroblivion's layer fields on that grid of its main worldspace, ids as `owner|FormID`."""
        out = {}
        for name in self.exports:
            folder = record_dir(self.export_root, name)
            names = _load_order(folder)
            worlds = {r.get('FormID') for r in record_blocks(os.path.join(folder, 'WRLD.txt'))
                      if r.get('EditorID', '').lower() == WORLDSPACE_EDID.lower()}
            grids = cell_grids(str(folder))
            for rec in record_blocks(os.path.join(folder, 'LAND.txt')):
                grid = grids.get(rec.get('ParentCELL', '').upper())
                if rec.get('ParentWRLD') in worlds and grid and rec.get('LayerCount'):
                    out.setdefault(grid, _layers(rec, names))
        return out


def _layers(rec: dict, names: list) -> dict:
    """A LAND's layer fields, texture ids resolved to `owner|FormID`."""
    out = {}
    for key, value in rec.items():
        if key == 'LayerCount' or key.startswith('Layer['):
            out[key] = _resolve(value, names) if key.endswith('.Texture') and value != NO_TEXTURE else value
    return out


def dominant(fields: dict) -> dict:
    """(quadrant, vertex) -> the texture weighing most there; base weighs one less the alphas."""
    base, alpha = {}, defaultdict(lambda: defaultdict(float))
    for i in range(int(fields.get('LayerCount') or 0)):
        if fields.get(f'Layer[{i}].Type') == 'BASE':
            base[int(fields.get(f'Layer[{i}].BTXT.Quadrant') or 0)] = fields.get(f'Layer[{i}].BTXT.Texture')
            continue
        quad, tex = int(fields.get(f'Layer[{i}].ATXT.Quadrant') or 0), fields.get(f'Layer[{i}].ATXT.Texture')
        for j in range(int(fields.get(f'Layer[{i}].VTXTCount') or 0)):
            pos = int(fields.get(f'Layer[{i}].VT[{j}].Pos') or 0)
            alpha[(quad, pos)][tex] += float(fields.get(f'Layer[{i}].VT[{j}].Opacity') or 0)
    out = {}
    for quad, tex in base.items():
        for pos in range(QUAD_VERTICES):
            weights = dict(alpha.get((quad, pos), {}))
            weights[tex] = weights.get(tex, 0.0) + max(0.0, 1.0 - sum(weights.values()))
            out[(quad, pos)] = max(sorted(weights), key=weights.get)
    return out


def _fields(lines: list) -> dict:
    """An exported record's lines as {key: value}."""
    return dict(line.partition('=')[::2] for line in lines)


def _land_grids(out: dict) -> dict:
    """Own LAND FormID -> its exterior (X, Y) grid."""
    grids = {}
    for form_id, lines in out.get('CELL', ()):
        fields = _fields(lines)
        if fields.get('XCLC.X') is not None:
            grids[form_id.upper()] = (int(fields['XCLC.X']), int(fields['XCLC.Y']))
    return {form_id: grids.get(_fields(lines).get('ParentCELL', '').upper())
            for form_id, lines in out.get('LAND', ())}


def pair_painted(out: dict, ground: MorroblivionGround, lands: dict) -> Counter:
    """Counter of (own LTEX FormID, Morroblivion key) painted on the same vertex with the same texture file."""
    stems = {fid.upper(): twin_stem(_fields(lines).get('ICON', '')) for fid, lines in out.get('LTEX', ())}
    pairs = Counter()
    grids = _land_grids(out)
    for form_id, lines in out.get('LAND', ()):
        theirs = lands.get(grids.get(form_id))
        if theirs is None:
            continue
        mine = dominant(_fields(lines))
        for spot, key in dominant(theirs).items():
            own = (mine.get(spot) or '').upper()
            if own in stems and stems[own] == icon_stem(ground.ltex.get(key, {}).get('ICON', '')):
                pairs[(own, key)] += 1
    return pairs


def read_pairs(folder) -> dict:
    """{lower EditorID: Morroblivion key} written beside a plugin's records."""
    path = os.path.join(str(folder), GROUND_NAME)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding='utf-8') as fh:
        return {k.lower(): v for k, _sep, v in (line.rstrip('\n').partition('=') for line in fh) if v}


def choose_pairs(out: dict, ground: MorroblivionGround, painted: Counter, vanilla: dict) -> dict:
    """Own LTEX FormID -> its Morroblivion key: most painted beside it, the vanilla pair, or the stem's only record."""
    best = {}
    for (own, key), count in sorted(painted.items()):
        if count > best.get(own, ('', 0))[1]:
            best[own] = (key, count)
    out_pairs = {}
    for form_id, lines in out.get('LTEX', ()):
        fields = _fields(lines)
        stem = twin_stem(fields.get('ICON', ''))
        named = vanilla.get(fields.get('EditorID', '').lower(), '')
        only = ground.by_stem.get(stem, [])
        if form_id.upper() in best:
            out_pairs[form_id] = best[form_id.upper()][0]
        elif named and icon_stem(ground.ltex.get(named, {}).get('ICON', '')) == stem:
            out_pairs[form_id] = named
        elif len(only) == 1:
            out_pairs[form_id] = only[0]
    return out_pairs


def mint(out: dict, ctx, ground: MorroblivionGround, key: str) -> str:
    """A new own LTEX copying Morroblivion's `key`, keyed by its EditorID; its FormID."""
    rec = ground.ltex[key]
    form_id = ctx.derive('ltex:' + rec['EditorID'])
    lines = [f'{k}={escape_value(v)}' for k, v in rec.items() if not k.startswith(_SKIP_FIELDS)]
    out.setdefault('LTEX', []).append((form_id, lines))
    return form_id


def _targets(pairs: dict, painted: Counter) -> dict:
    """Morroblivion key -> the own LTEX paired with it that was painted beside it most."""
    out = {}
    for own, key in sorted(pairs.items()):
        if key not in out or painted[(own.upper(), key)] > painted[(out[key].upper(), key)]:
            out[key] = own
    return out


def paint(out: dict, ctx, ground: MorroblivionGround, lands: dict, pairs: dict, painted: Counter) -> tuple:
    """Give each own LAND Morroblivion's layers on its grid; (lands repainted, LTEX minted).

    A Morroblivion texture lands on its paired own LTEX, a master's paired
    one, or a new LTEX, which joins `pairs`.
    """
    target = _targets(pairs, painted)
    target[NO_TEXTURE] = NO_TEXTURE
    grids = _land_grids(out)
    repainted, minted = 0, 0
    for form_id, lines in out.get('LAND', ()):
        theirs = lands.get(grids.get(form_id))
        if theirs is None or not any(line.startswith('LayerCount=') for line in lines):
            continue
        needed = {v for k, v in theirs.items() if k.endswith('.Texture')} - target.keys()
        if not needed <= ground.ltex.keys():
            continue
        for key in sorted(needed):
            found = _master_ltex(ctx, key)
            if not found:
                found = mint(out, ctx, ground, key)
                pairs[found] = key
                minted += 1
            target[key] = found
        _replace_layers(lines, {k: target[v] if k.endswith('.Texture') else v for k, v in theirs.items()})
        repainted += 1
    return repainted, minted


def _master_ltex(ctx, key: str) -> str:
    """The master LTEX a vanilla master's pairs give Morroblivion `key`, in this plugin's ids; ''."""
    for name in ctx.master_names:
        for edid, paired in read_pairs(record_dir(ctx.export_root, name)).items():
            if paired == key:
                found = ctx.index.lookup_editor_id(edid)
                if found:
                    return found
    return ''


def _replace_layers(lines: list, layers: dict) -> None:
    """Swap a LAND's layer lines for `layers`, where its own stood."""
    at = next(i for i, line in enumerate(lines) if line.startswith('LayerCount='))
    lines[:] = [line for line in lines if not line.startswith(('LayerCount=', 'Layer['))]
    lines[at:at] = [f'{k}={v}' for k, v in layers.items()]


def write_pairs(folder, out: dict, pairs: dict) -> None:
    """Write each own LTEX's EditorID and Morroblivion key beside the plugin's records."""
    names = {fid: _fields(lines).get('EditorID', '') for fid, lines in out.get('LTEX', ())}
    rows = sorted(f'{names[fid]}={key}\n' for fid, key in pairs.items() if names.get(fid))
    os.makedirs(str(folder), exist_ok=True)
    with open(os.path.join(str(folder), GROUND_NAME), 'w', encoding='utf-8') as fh:
        fh.writelines(rows)


def match_ground(out: dict, ctx, ground: MorroblivionGround, vanilla_plugin: bool) -> dict:
    """Pair and repaint a hybrid plugin's ground; {own LTEX FormID: Morroblivion key}.

    See: docs/commentary/tes4_export_morrowind.md#hybrid-ground
    """
    lands = ground.lands() if vanilla_plugin else {}
    vanilla = read_pairs(record_dir(ctx.export_root, VANILLA_ESM))
    painted = pair_painted(out, ground, lands)
    pairs = choose_pairs(out, ground, painted, vanilla)
    repainted, minted = paint(out, ctx, ground, lands, pairs, painted)
    print(f'  Morroblivion ground: {len(pairs)} land textures paired, {repainted} lands '
          f'repainted, {minted} land textures added')
    return pairs

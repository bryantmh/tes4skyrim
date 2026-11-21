"""
A hybrid plugin's companion: its furniture as Skyrim furniture, and Morroblivion's grass on its land.

Hybrid mode gives the furniture Morroblivion's meshes, whose markers let an
actor sit or sleep, but Morrowind places them as STAT and ACTI records, which
nobody can use, and its land has no grass. A record cannot change type in an
override, so the plugin's own Export step writes `<plugin> - Morroblivion
Hybrid.esp` beside it: one FURN per furniture base, an override of each
placement of one, and each land texture carrying the grass its Morroblivion
twin lists. Its masters are the plugin's, the plugin, and their companions.

See: docs/commentary/tes4_export_morrowind.md#hybrid-companion
"""

import os
import shutil
import time

from asset_convert.sources.source_registry import companion_name
from core.plugin_masters import masters_from_export_header
from output_layout import assets_for, record_dir
from tes5_import.base.text_reader import parse_export_file

from .export_morrowind import morroblivion_exports, write_export, write_header
from .morroblivion import archive_path
from .morroblivion_hybrid import (FURNITURE_MARKER, MANIFEST_NAME, find_under, grass_ids,
                                  ground_index, icon_stem)
from .morrowind_ids import remap_form_id
from .morrowind_patch import patch_formid
from .record_types.common import escape_value

#: FormID derivation site of its FURN records.
FURNITURE_SITE = 'mwfurn'

#: FormID derivation site of its GRAS records.
GRASS_SITE = 'mwgras'

#: Base record types a Morrowind furniture piece is exported as.
BASE_TYPES = ('STAT', 'ACTI')

#: A base's fields the FURN keeps.
BASE_FIELDS = ('EditorID', 'FULL', 'Model.MODL', 'Model.MODB', 'Model.OriginShift', 'SCRI')

#: Fields of a base or placement that hold a FormID.
ID_FIELDS = ('FormID', 'NAME', 'ParentCELL', 'XOWN.Owner', 'XTEL.Door', 'XLOC.Key', 'SCRI')

#: Lines a copied record never carries over: the writer emits them.
_HEAD_KEYS = ('Signature', 'FormID')

#: TES4 record flag of a deleted record.
_DELETED = 0x20


def companion_masters(export_dir: str, plugin: str) -> list:
    """[(name, record dir)]: the plugin's masters, the plugin, then each master's exported companion."""
    own = record_dir(export_dir, plugin)
    masters = masters_from_export_header(own)
    out = [(m, str(record_dir(export_dir, m))) for m in masters] + [(plugin, str(own))]
    for master in masters:
        folder = record_dir(export_dir, companion_name(master))
        if (folder / '_HEADER.txt').is_file():
            out.append((companion_name(master), str(folder)))
    return out


def _remap(path: str, name: str, names: list) -> dict:
    """The index re-keying from one master's export into this plugin's master list."""
    own = masters_from_export_header(path)
    slot = {n.lower(): i for i, n in enumerate(names)}
    remap = {len(own): slot[name.lower()]}
    remap.update({k: slot[m.lower()] for k, m in enumerate(own) if m.lower() in slot})
    return remap


def _rebased(rec: dict, remap: dict) -> dict:
    """`rec` with every FormID field re-keyed; a field naming an unreachable file is dropped."""
    out = {}
    for key, value in rec.items():
        if key in ID_FIELDS:
            value = remap_form_id(value, remap)
        if value is not None:
            out[key] = value
    return out


def _records(masters, names, signatures):
    """Every record of `signatures` the masters export, re-keyed into this plugin's list, in load order."""
    for name, path in masters:
        remap = _remap(path, name, names)
        for sig in signatures:
            for rec in parse_export_file(os.path.join(path, f'{sig}.txt')):
                yield _rebased(rec, remap)


def _lines(rec: dict) -> list:
    """A parsed record's lines, less the ones the writer emits."""
    return [f'{k}={escape_value(v)}' for k, v in rec.items() if k not in _HEAD_KEYS]


def _by_editor_id(companions, names, signature: str) -> dict:
    """{EditorID, lower case: FormID} of the masters' companions' `signature` records."""
    return {r['EditorID'].lower(): r['FormID'] for r in _records(companions, names, (signature,))
            if r.get('EditorID') and r.get('FormID')}


def _marker_meshes(masters):
    """A test of whether a mesh path is a furniture mesh in any master's tree, reading each file once."""
    roots = [assets_for(path) / 'meshes' for _name, path in masters]
    seen = {}

    def is_marker(model: str) -> bool:
        """Whether `model` carries furniture markers."""
        rel = archive_path(model)
        if rel not in seen:
            found = find_under(roots, rel)
            seen[rel] = bool(found) and FURNITURE_MARKER in found.read_bytes()
        return seen[rel]
    return is_marker


def _morroblivion_flags(export_dir: str) -> dict:
    """{Morroblivion furniture model: its MNAM flags}, which open its seats and mark a bed."""
    out = {}
    for name in morroblivion_exports(export_dir):
        for rec in parse_export_file(os.path.join(record_dir(export_dir, name), 'FURN.txt')):
            if rec.get('Model.MODL'):
                out.setdefault(archive_path(rec['Model.MODL']), rec.get('MNAM.Flags', '0'))
    return out


def furniture(layout: dict, flags: dict) -> tuple:
    """({base FormID: FURN FormID}, [(FURN FormID, lines)] the plugin adds or overrides).

    A master's furniture base keeps its companion's FURN; the plugin's own
    bases, and its overrides of a master's, are written here.
    """
    names, own_index = layout['names'], len(layout['names'])
    known = _by_editor_id(layout['companions'], names, 'FURN')
    bases = {r['FormID']: known[r['EditorID'].lower()]
             for r in _records(layout['masters'], names, BASE_TYPES)
             if r.get('FormID') and r.get('EditorID', '').lower() in known}
    is_marker = _marker_meshes(layout['masters'] + [layout['own']])
    out = []
    for rec in _records([layout['own']], names, BASE_TYPES):
        base, edid = rec.get('FormID'), rec.get('EditorID') or rec.get('FormID')
        if not base or not is_marker(rec.get('Model.MODL', '')):
            bases.pop(base, None)
            continue
        form_id = known.get(edid.lower()) or patch_formid(('FURN', edid), own_index, FURNITURE_SITE)
        lines = ['RecordFlags=0'] + [f'{k}={escape_value(rec[k])}' for k in BASE_FIELDS if rec.get(k)]
        lines.append(f"MNAM.Flags={flags.get(archive_path(rec['Model.MODL']), '0')}")
        bases[base] = form_id
        out.append((form_id, lines))
    return bases, out


def placements(layout: dict, bases: dict) -> list:
    """[(FormID, lines)]: each placement of a furniture base, and each the masters' companions override.

    Restating every placement a master's companion overrides keeps the
    plugin's own edit of it, deletion included, above that companion's.
    """
    names = layout['names']
    overridden = {r['FormID'] for r in _records(layout['companions'], names, ('REFR',)) if r.get('FormID')}
    out = []
    for rec in _records([layout['own']], names, ('REFR',)):
        form_id, base = rec.get('FormID'), rec.get('NAME')
        listed = form_id in overridden
        if not form_id or (base not in bases and not listed):
            continue
        if not listed and int(rec.get('RecordFlags') or 0) & _DELETED:
            continue
        if base in bases:
            rec['NAME'] = bases[base]
        out.append((form_id, _lines(rec)))
    return out


def grass(layout: dict, ground: dict) -> tuple:
    """([(GRAS FormID, lines)] new here, [(LTEX FormID, lines)] carrying its twin's grass)."""
    names, own_index = layout['names'], len(layout['names'])
    known = _by_editor_id(layout['companions'], names, 'GRAS')
    added, lands = {}, []
    for rec in _records([layout['own']], names, ('LTEX',)):
        twin = ground.get(icon_stem(rec.get('ICON', '')))
        ids = []
        for gid in grass_ids(twin[0]) if twin and rec.get('FormID') else ():
            gras = twin[1].get(gid)
            if gras is None:
                continue
            edid = gras['EditorID'].lower()
            if edid not in known:
                known[edid] = patch_formid(('GRAS', edid), own_index, GRASS_SITE)
                added[known[edid]] = _lines(gras)
            ids.append(known[edid])
        if ids:
            kept = {k: v for k, v in rec.items() if not k.startswith('Grass')}
            lands.append((rec['FormID'], _lines(kept) + [f'GrassCount={len(ids)}']
                          + [f'Grass[{i}]={g}' for i, g in enumerate(ids)]))
    return sorted(added.items()), lands


def export_companion(export_dir: str, plugin: str, progress=print) -> bool:
    """Write `plugin`'s companion export when it was exported in hybrid mode; drop a stale one otherwise."""
    start = time.time()
    folder = record_dir(export_dir, companion_name(plugin))
    if folder.is_dir():
        shutil.rmtree(folder)
    own = record_dir(export_dir, plugin)
    if not (own / MANIFEST_NAME).is_file():
        return True
    entries = companion_masters(export_dir, plugin)
    names = [name for name, _path in entries]
    at = names.index(plugin)
    layout = {'names': names, 'masters': entries[:at], 'own': entries[at], 'companions': entries[at + 1:]}
    bases, furn = furniture(layout, _morroblivion_flags(export_dir))
    refs = placements(layout, bases)
    grasses, lands = grass(layout, ground_index(export_dir, morroblivion_exports(export_dir)))
    counts = write_export({'FURN': furn, 'REFR': refs, 'GRAS': grasses, 'LTEX': lands}, str(folder))
    write_header(str(folder), names, sum(counts.values()), f'{plugin} furniture and grass for Morroblivion', 0)
    progress(f'[{companion_name(plugin)}] {len(furn)} furniture, {len(refs)} placements, {len(grasses)} grasses '
             f'on {len(lands)} land textures, in {time.time() - start:.1f}s')
    return True

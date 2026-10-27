"""
What the Morrowind sidecar reads from an export folder's text: each file is
read ONCE per run, and every table built from it takes its share of that read.

See: docs/commentary/morrowind_runtime.md#sidecar
"""

import functools
import math
import os
import re

from asset_convert.sources import source_registry
from core.plugin_masters import export_root, masters_from_export_header
from tes4_export.morrowind_ids import load_index

#: Every TES3 type that can carry a script.
SCRIPTED_EXPORTS = ('NPC_.txt', 'CREA.txt', 'ACTI.txt', 'ALCH.txt', 'AMMO.txt',
                    'APPA.txt', 'ARMO.txt', 'BOOK.txt', 'CLOT.txt', 'CONT.txt',
                    'DOOR.txt', 'INGR.txt', 'KEYM.txt', 'LIGH.txt', 'MISC.txt',
                    'WEAP.txt')

#: The record types of `SCRIPTED_EXPORTS`.
SCRIPTED_TYPES = tuple(name[:-4] for name in SCRIPTED_EXPORTS)

#: The bases `id->` can name: every scriptable one, plus statics a script disables or moves.
PLACED_EXPORTS = SCRIPTED_EXPORTS + ('STAT.txt',)

#: The record types of `PLACED_EXPORTS`.
PLACED_TYPES = tuple(name[:-4] for name in PLACED_EXPORTS)

#: The exports holding PLACED references, which name their base by FormID.
PLACEMENT_EXPORTS = ('REFR.txt', 'ACHR.txt', 'ACRE.txt')

#: The exports holding MWScript text, with the field each keeps it in.
_SCRIPT_TEXT = (('SCPT.txt', 'SCTX'), ('MWIN.txt', 'ResultScript'))

#: A quoted name, whole, or a bare word that may be an id.
_NAME_TOKEN = re.compile(r'"([^"]*)"|([A-Za-z0-9_\'.-]+)')

#: Everything on a script line before its `;` comment, quotes respected.
_CODE_PART = re.compile(r'^((?:[^";]|"[^"]*")*)')

#: TES4 marker base -> the kind the runtime keys on; the TES3 export writes the same ids.
MARKER_KINDS = {0x00000005: 'divine', 0x00000006: 'temple'}

#: The EditorID prefix of the persistent markers the export mints for travel destinations.
TRAVEL_PREFIX = 'TES3Travel'

#: Morroblivion's plugins: their SCPTs are TES4's language, never MWScript.
TES4_PLUGIN_PREFIX = 'morrowind_ob'

_RECORD_MARK = '---RECORD_BEGIN---'
_PLACEMENT_KEYS = ('PosX', 'PosY', 'PosZ', 'RotX', 'RotY', 'RotZ')
_REF_KEYS = ('FormID', 'NAME', 'ParentCELL', 'EditorID') + _PLACEMENT_KEYS
_SCRIPTED_KEYS = ('FormID', 'EditorID', 'SCRI')
_CELL_KEYS = ('FormID', 'EditorID', 'FULL', 'ParentWRLD')


def export_records(path: str, keys: tuple):
    """Each record in an export file as a dict of just `keys`."""
    if not os.path.isfile(path):
        return
    wanted = tuple(key + '=' for key in keys)
    record = None
    with open(path, encoding='utf-8', errors='replace') as handle:
        for line in handle:
            if line.startswith(_RECORD_MARK):
                if record:
                    yield record
                record = {}
            elif record is not None and line.startswith(wanted):
                key, _, value = line.rstrip('\n').partition('=')
                record[key] = value
    if record:
        yield record


def placement(rec: dict) -> str:
    """`x,y,z,rx,ry,rz` for a placed ref, the angles in DEGREES.

    The runtime's SetAngle hook takes degrees; the record stores radians.
    """
    out = []
    for key in _PLACEMENT_KEYS:
        value = float(rec.get(key) or 0.0)
        if key.startswith('Rot'):
            value = math.degrees(value)
        out.append(f'{value:g}')
    return ','.join(out)


def _base_id(base: str) -> int:
    """A placement's NAME as an int, 0 when absent or malformed."""
    try:
        return int(base or '0', 16)
    except ValueError:
        return 0


def _take_ref(out: dict, rec: dict, in_refr: bool, owner: str,
              scripted: set) -> None:
    """Fold one placed reference into `folder_tables`' `first`, `instances`,
    and, for a REFR, `travel` and `markers`."""
    base, formid = rec.get('NAME', ''), rec.get('FormID', '')
    if base and formid[:2].upper() == owner:
        out['first'].setdefault(base, formid)
    if formid and base.upper() in scripted:
        out['instances'].append(rec)
    if not in_refr:
        return
    edid = rec.get('EditorID', '')
    if formid and edid.startswith(TRAVEL_PREFIX):
        out['travel'].setdefault(edid.lower(), formid)
    if _base_id(base) in MARKER_KINDS:
        out['markers'].append(rec)


def rebased_formid(formid: str, folder: str, plugin: str, header: list):
    """`formid` as `plugin`'s export at `folder` writes it, spelled the way an
    export whose masters are `header` spells it; None when that one cannot.

    The low 24 bits are the record; the index byte is the owner's position in
    the WRITER's master list, or that list's length for its own records.
    """
    try:
        index = int(formid[:2], 16)
    except ValueError:
        return None
    masters = masters_from_export_header(folder)
    owner = plugin if index == len(masters) else (
        masters[index] if index < len(masters) else '')
    names = [name.lower() for name in header]
    if not owner or owner.lower() not in names:
        return None
    return f'{names.index(owner.lower()):02X}{formid[2:]}'


def runtime_masters(folder: str) -> list:
    """`(record_dir, plugin)` for each exported master whose scripts are MWScript.

    A Morroblivion master's scripts run as Papyrus, so its records never
    stage an instance here; that would run the object's script twice.
    """
    root = export_root(folder)
    out = []
    for name in masters_from_export_header(folder):
        if name.lower().startswith(TES4_PLUGIN_PREFIX):
            continue
        master_dir = str(source_registry.record_dir(root, name))
        if os.path.isdir(master_dir):
            out.append((master_dir, name))
    return out


def master_scripted(folder: str) -> list:
    """`(base FormID as `folder` spells it, record, master dir, master)` per
    scripted record a `runtime_masters` master owns.

    🛑 A plugin PLACES its masters' scripted objects -- Tamriel Rebuilt's cells
    hold Tamriel Data's banners -- and each placement is this plugin's to stage.
    See: CLAUDE.md#master-blindness
    """
    header = masters_from_export_header(folder)
    out = []
    for master_dir, name in runtime_masters(folder):
        for _file, rec in folder_tables(master_dir)['scripted']:
            if not rec.get('SCRI') or not rec.get('FormID'):
                continue
            here = rebased_formid(rec['FormID'], master_dir, name, header)
            if here:
                out.append((here.upper(), rec, master_dir, name))
    return out


@functools.lru_cache(maxsize=16)
def folder_tables(folder: str) -> dict:
    """What the sidecar reads from the export at `folder`, each file once:
    `scripted` `[(file, record)]` of `SCRIPTED_EXPORTS`, `cells` CELL.txt's
    records, `first` `{base: first placement FormID the folder owns}`,
    `travel` `{lower TES3Travel EditorID: FormID}`, `markers` the REFRs of a
    `MARKER_KINDS` base, and `instances` the placements of a scripted base,
    the folder's own or a `runtime_masters` master's.
    """
    scripted = [(name, rec) for name in SCRIPTED_EXPORTS
                for rec in export_records(os.path.join(folder, name), _SCRIPTED_KEYS)]
    bases = {rec['FormID'].upper() for _name, rec in scripted
             if rec.get('FormID') and rec.get('SCRI')}
    bases.update(base for base, _rec, _dir, _name in master_scripted(folder))
    owner = f'{len(masters_from_export_header(folder)):02X}'
    out = {'scripted': scripted, 'first': {}, 'travel': {}, 'markers': [],
           'instances': [],
           'cells': list(export_records(os.path.join(folder, 'CELL.txt'), _CELL_KEYS))}
    for name in PLACEMENT_EXPORTS:
        for rec in export_records(os.path.join(folder, name), _REF_KEYS):
            _take_ref(out, rec, name == 'REFR.txt', owner, bases)
    return out


def cell_place(rec: dict, masters: list, plugin: str) -> str:
    """`Plugin|FormID` of a CELL row's place -- the CELL of an interior, the
    WORLDSPACE of an exterior -- attributed to the loaded plugin its index byte
    names among the export's `masters`; '' when the row names neither."""
    place = rec.get('ParentWRLD') or rec.get('FormID', '')
    if not place:
        return ''
    index = int(place[:2], 16)
    return f'{masters[index] if index < len(masters) else plugin}|{place}'


def forget_folder_tables() -> None:
    """Drop every folder's `folder_tables`; called after the sidecar is staged."""
    folder_tables.cache_clear()


def first_placements(folder: str, own: int, ids: dict) -> dict:
    """`{lower id: placement FormID}`: the FIRST placement `folder` owns of each
    base `ids` names, which is the reference `id->` acts on, as OpenMW's
    `searchPtr` finds it within a cell store.
    See: docs/commentary/morrowind_runtime.md#placed-references
    """
    placed = folder_tables(folder)['first']
    if not placed:
        return {}
    index = load_index(folder, PLACED_TYPES, {own: own})
    out = {}
    for key, edid in ids.items():
        base = index.lookup(edid)
        ref = placed.get(base) if base else None
        if ref:
            out[key] = ref
    return out


def _line_names(line: str) -> list:
    """The quoted names and bare words on one script line, its comment dropped."""
    code = _CODE_PART.match(line).group(1)
    return [quoted or bare.rstrip('-') for quoted, bare in _NAME_TOKEN.findall(code)]


def script_named_ids(folder: str) -> dict:
    """`{lower name: name}` for every quoted name and bare word the export's
    scripts and dialogue results hold outside a comment: a superset of the
    ids they name, which only ever meets a real id's placement.
    See: docs/commentary/tes5_import_world.md#tes3-script-named-refs-persist
    """
    out = {}
    for name, field in _SCRIPT_TEXT:
        for rec in export_records(os.path.join(folder, name), (field,)):
            for line in rec.get(field, '').replace('\\r', '').split('\\n'):
                out.update((word.lower(), word) for word in _line_names(line) if word)
    return out

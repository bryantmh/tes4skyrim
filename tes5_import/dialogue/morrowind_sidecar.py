"""
Stage a plugin's TES3 dialogue where MorrowindRuntime.dll can read it.

The runtime is an SKSE plugin in the player's install, so it never sees this
repo's `export/`. Everything it needs goes into the plugin's own output under
`SKSE/Plugins/MorrowindRuntime/<plugin>/`, which installs with every other
asset and keeps one plugin's data separate from another's.

The dialogue and the actor table are MERGED over the plugin's TES3 masters
from the binaries (`morrowind_sidecar_source`). Beside them go the tables the
result-script compiler and its commands need, built from the text exports:
the globals, each script's declared locals, which script each object runs,
and the FormID behind each item id.

See: docs/commentary/morrowind_runtime.md#sidecar
"""

import math
import os
import re
import shutil
import struct

from asset_convert.sources import source_registry
from core.plugin_masters import (get_masters_from_binary,
                                 masters_from_export_header)
from tes4_export.morrowind_ids import encode_editor_id, load_index
from tes4_export.morrowind_patch import PATCH_NAME

from .morrowind_sidecar_source import (gather, plugin_chain,
                                       write_merged_dialogue)
from .morrowind_travel import TRAVEL_TABLE, marker_index, travel_lines
from .say_morrowind import SAY_TABLE, say_rows

#: Where the runtime looks, relative to a plugin's output root.
SIDECAR_DIR = os.path.join('SKSE', 'Plugins', 'MorrowindRuntime')

#: The dialogue as the SIDECAR names it; topics first, a response needs one.
DIALOGUE_FILES = ('DIAL.txt', 'INFO.txt')

#: The same two in the EXPORT, which keeps tes4_export's TES3 signatures.
_EXPORT_DIALOGUE = ('MWDI.txt', 'MWIN.txt')

#: The FormID -> TES3 id index the activation hook routes on.
ACTOR_INDEX = 'NPC__index.txt'

#: The compiler's tables: `name=type,value`, `script=t:var,...`, `object=script`.
GLOBALS_TABLE = 'GLOB.txt'
SCRIPT_LOCALS_TABLE = 'SCPT_locals.txt'
ACTOR_SCRIPTS_TABLE = 'SCPT_objects.txt'

#: Every SCPT body the object-script tick compiles, `script=<escaped source>`.
SCRIPT_BODIES_TABLE = 'SCPT_source.txt'

#: One script INSTANCE per placed reference, `placement FormID=script`.
SCRIPT_INSTANCES_TABLE = 'SCPT_instances.txt'

#: What the FILTER and persuasion need about each NPC; the columns are `_actor_line`'s.
ACTORS_TABLE = 'NPC_.txt'

#: The global scripts an SSCR starts at new game and on every load, `script=1`.
START_SCRIPTS_TABLE = 'SSCR.txt'

#: FACT rank requirements, which the filter's RankRequirement reads.
FACTIONS_TABLE = 'FACT.txt'

#: Every GMST of the chain, `name=type,value`, and the SKIL rows persuasion credits skill use from.
GMST_TABLE = 'GMST.txt'
SKILLS_TABLE = 'SKIL.txt'

#: What AddItem and its kin need: `item id=Plugin.esm|FormID`.
ITEMS_TABLE = 'items_formid.txt'

#: What `id->Command` needs: the PLACED reference, `id=Plugin.esm|FormID`.
REFS_TABLE = 'refs_formid.txt'

#: What PlaceAtPC needs: the BASE record, `id=Plugin.esm|FormID`.
BASES_TABLE = 'bases_formid.txt'

#: What PositionCell needs: `cell name=Plugin.esm|anchor REFR FormID`.
CELLS_TABLE = 'cells_formid.txt'

#: What PlaySound3D and its kin need: `sound id=Plugin.esm|SNDR FormID`.
SOUNDS_TABLE = 'SOUN.txt'

#: What AddSpell and its kin need: `spell id=Plugin.esm|SPEL FormID|effect indices`.
SPELLS_TABLE = 'SPEL.txt'

#: What GetEffect and RemoveEffects need: `TES3 index=Plugin.esm|MGEF FormID|Name`.
EFFECTS_TABLE = 'MGEF.txt'

#: What AddSoulGem needs: `gem id_Filled<n>=Plugin.esm|SLGM FormID`.
SOULGEMS_TABLE = 'SLGM.txt'

#: What AddSoulGem's creature argument needs: `creature id=soul size`.
SOULS_TABLE = 'CREA_soul.txt'

#: A TES5 record's body starts after its 24-byte header.
_TES5_HEADER = slice(24, None)

#: The exports the tables are built from; `_SCRIPTED_EXPORTS` is every TES3 type that can carry a script.
_NPC_EXPORT = 'NPC_.txt'
_SCRIPTED_EXPORTS = ('NPC_.txt', 'CREA.txt', 'ACTI.txt', 'ALCH.txt', 'AMMO.txt',
                     'APPA.txt', 'ARMO.txt', 'BOOK.txt', 'CLOT.txt', 'CONT.txt',
                     'DOOR.txt', 'INGR.txt', 'KEYM.txt', 'LIGH.txt', 'MISC.txt',
                     'WEAP.txt')

#: The exports holding PLACED references, which name their base by FormID.
_PLACEMENT_EXPORTS = ('REFR.txt', 'ACHR.txt', 'ACRE.txt')

#: The exports whose records can sit in an inventory.
_ITEM_EXPORTS = ('ALCH.txt', 'AMMO.txt', 'APPA.txt', 'ARMO.txt', 'BOOK.txt',
                 'CLOT.txt', 'INGR.txt', 'KEYM.txt', 'LIGH.txt', 'MISC.txt',
                 'WEAP.txt')
#: The exports holding spells, which AddSpell and GetSpell name by TES3 id.
_SPELL_EXPORTS = ('SPEL.txt',)

#: Where the soul sizes come from, and the filled gems AddSoulGem resolves.
_CREATURE_EXPORT = 'CREA.txt'
_SOULGEM_EXPORTS = ('SLGM.txt',)

#: The magic effects, and what `MW038Recall` carries before the name itself.
_EFFECT_EXPORT = 'MGEF.txt'
_EFFECT_PREFIX = 'MW'
_EFFECT_DIGITS = 3

#: TES3's own cap on a spell's effects; measured max is 8 across the corpus.
_MAX_EFFECTS = 8

#: The same types as the index names them.
_ITEM_TYPES = tuple(name[:-4] for name in _ITEM_EXPORTS)
_SCRIPTED_TYPES = tuple(name[:-4] for name in _SCRIPTED_EXPORTS)
_SPELL_TYPES = tuple(name[:-4] for name in _SPELL_EXPORTS)
_GLOBAL_EXPORT = 'GLOB.txt'
_SCRIPT_EXPORT = 'SCPT.txt'
#: Where the cell names and their FormIDs come from, for the anchor table.
_CELL_EXPORT = 'CELL.txt'
_RECORD_MARK = '---RECORD_BEGIN---'

#: What marks the export ROOT, as opposed to a record dir beneath it.
_REGISTRY_FILE = 'sources.json'

#: A local declaration in MWScript source: `short name`, `long name`, `float name`.
_DECLARATION = re.compile(r'^\s*(short|long|float)\s+([A-Za-z_][A-Za-z0-9_]*)',
                          re.IGNORECASE)

#: Morroblivion's plugins: their SCPTs are TES4's language, never MWScript.
_TES4_PLUGIN_PREFIX = 'morrowind_ob'


def plugin_stem(plugin_name: str) -> str:
    """`Morrowind.esm` -> `Morrowind`, the per-plugin sidecar folder name."""
    return os.path.splitext(os.path.basename(plugin_name))[0]


def sidecar_dir(output_path: str, plugin_name: str) -> str:
    """The folder a plugin's sidecar lives in, beside its output ESM."""
    return os.path.join(os.path.dirname(output_path), SIDECAR_DIR,
                        plugin_stem(plugin_name))


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


def _actor_index(export_dir: str) -> str:
    """`FormID=EditorID` for every NPC: the TES3 id dialogue filters on.

    The runtime routes activation by FormID but filters by TES3 id, so without
    this index it can tell an actor is Morrowind's and still not know who they
    are. Built here because the FormID is minted during import.
    See: docs/commentary/morrowind_runtime.md#activation
    """
    lines = [f"{rec['FormID']}={rec['EditorID']}"
             for rec in export_records(os.path.join(export_dir, _NPC_EXPORT),
                                       ('FormID', 'EditorID'))
             if rec.get('FormID') and rec.get('EditorID')]
    return '\n'.join(lines) + ('\n' if lines else '')


def _export_root(export_dir: str) -> str:
    """The folder holding the source registry, at or above `export_dir`."""
    path = os.path.abspath(export_dir)
    while not os.path.isfile(os.path.join(path, _REGISTRY_FILE)):
        parent = os.path.dirname(path)
        if parent == path:
            return os.path.dirname(os.path.abspath(export_dir))
        path = parent
    return path


def table_dirs(export_dir: str, plugin_name: str) -> list:
    """`(record_dir, plugin)` for this plugin, then each TES3 master that has
    been exported -- a dialogue script names its masters' globals freely."""
    root = _export_root(export_dir)
    binary = source_registry.plugin_binary(root, plugin_name)
    masters = get_masters_from_binary(str(binary)) if binary else []
    dirs = [(export_dir, plugin_name)]
    for master in masters:
        candidate = str(source_registry.record_dir(root, master))
        if os.path.isfile(os.path.join(candidate, _GLOBAL_EXPORT)):
            dirs.append((candidate, master))
    return dirs


def _formid_here(formid: str, folder: str, master: str, header: list):
    """A master's own FormID as THIS plugin's export spells it, or None.

    The low 24 bits are the record; the index byte is where the owner sits
    in a load order. A master's own records carry ITS master count there, and
    this plugin refers to them by that master's position in its own header.
    """
    own = f'{len(masters_from_export_header(folder)):02X}'
    names = [name.lower() for name in header]
    if formid[:2].upper() != own or master.lower() not in names:
        return None
    return f'{names.index(master.lower()):02X}{formid[2:]}'


#: What an out-of-range or NaN FLTV reads as; the engine clamps to this.
_INT32_MIN = -2147483648
_INT32_MAX = 2147483647


def _global_value(kind: str, raw: str) -> str:
    """A GLOB's FLTV as its FNAM type reads it: `s`/`l` TRUNCATE toward zero.

    TES3 stores every global's value as a float, so vanilla short globals carry
    uninitialized junk -- `WearingOrdinatorUni` holds 7.1e-31, which must read
    as 0 the way the engine reads it.
    See: docs/commentary/tes4_export_morrowind.md#globals-are-always-filled
    """
    if kind.lower() not in ('s', 'l'):
        return raw
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return '0'
    if math.isnan(value) or not _INT32_MIN <= value <= _INT32_MAX:
        return str(_INT32_MIN)
    return str(int(value))


def _global_lines(dirs: list) -> list:
    """`name=type,value,plugin|formid` per GLOB, the plugin's own winning.

    The FormID is what lets the runtime read the LIVE value out of the
    converted plugin's GLOB, so a global Papyrus writes -- `CharGenState`, the
    one that starts Morrowind's chargen -- reaches the scripts polling it.
    See: docs/commentary/morrowind_runtime.md#vanilla-morrowind-chargen
    """
    seen = {}
    for folder, plugin in dirs:
        for rec in export_records(os.path.join(folder, _GLOBAL_EXPORT),
                                  ('EditorID', 'FNAM.Type', 'FLTV.Value',
                                   'FormID')):
            name = rec.get('EditorID', '')
            if name and name.lower() not in seen:
                kind = rec.get('FNAM.Type', 'f')
                seen[name.lower()] = (
                    f"{name}={kind},"
                    f"{_global_value(kind, rec.get('FLTV.Value', '0'))},"
                    f"{plugin}|{rec.get('FormID', '')}")
    return list(seen.values())


def declared_locals(source: str) -> list:
    """`t:name` per local an MWScript source declares, in order, once each."""
    out, seen = [], set()
    for line in source.replace('\\r', '').split('\\n'):
        match = _DECLARATION.match(line)
        if match and match.group(2).lower() not in seen:
            seen.add(match.group(2).lower())
            out.append(f'{match.group(1)[0].lower()}:{match.group(2)}')
    return out


def _resolution_dirs(dirs: list, loaded: list) -> list:
    """`dirs[0]` first, then every OTHER plugin the game loads.

    🛑 A SCRI is resolved across the whole load order but STAGED only from
    `dirs[0]`, so the head must stay this plugin's own dir. In Morroblivion
    mode a TES3 plugin masters neither Morrowind_ob nor the patch, yet its
    items carry their scripts: `T_De_Necrom_Cuirass_01`'s SCRI is the patch's
    `OrdinatorUniform`, and without it the armor stages no script at all.
    See: docs/commentary/morrowind_runtime.md#sidecar
    """
    out = list(dirs)
    seen = {os.path.normcase(os.path.abspath(f)) for f, _p in out}
    for folder, plugin, _own in loaded:
        key = os.path.normcase(os.path.abspath(folder))
        if key not in seen:
            seen.add(key)
            out.append((folder, plugin))
    return out


def _script_tables(dirs: list) -> tuple:
    """`(locals_lines, body_lines, script_name_by_formid)`.

    🛑 The two halves have DIFFERENT scopes. `by_formid` spans every dir, so a
    SCRI naming a master's script still resolves; the staged lines cover only
    `dirs[0]`, because a master stages its own. A Morroblivion plugin stages no
    body at all -- in that mode alone is a TES3 plugin's master a TES4 one.
    See: docs/plans/morrowind_object_scripts.md#masters-stage-themselves
    """
    lines, bodies, by_formid, seen = [], [], {}, set()
    header = masters_from_export_header(dirs[0][0])
    tes4 = plugin_stem(dirs[0][1]).lower().startswith(_TES4_PLUGIN_PREFIX)
    for index, (folder, plugin) in enumerate(dirs):
        for rec in export_records(os.path.join(folder, _SCRIPT_EXPORT),
                                  ('FormID', 'EditorID', 'SCTX')):
            name = rec.get('EditorID', '')
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            formid = rec.get('FormID', '')
            here = formid if index == 0 else _formid_here(formid, folder,
                                                          plugin, header)
            if here:
                by_formid[here.upper()] = name
            if index:
                continue
            source = rec.get('SCTX', '')
            declared = declared_locals(source)
            if declared:
                lines.append(f"{name}={','.join(declared)}")
            if source.strip() and not tes4:
                bodies.append(f'{name}={source}')
    return lines, bodies, by_formid


def _scripted_bases(export_dir: str, by_formid: dict) -> dict:
    """`base FormID -> (TES3 id, script name)` per scripted object.

    The id rides along because a bare `Activate` or `Enable` inside the body
    acts on the object running it, which the runtime resolves BY id.
    """
    bases = {}
    for name in _SCRIPTED_EXPORTS:
        for rec in export_records(os.path.join(export_dir, name),
                                  ('FormID', 'EditorID', 'SCRI')):
            script = by_formid.get(rec.get('SCRI', '').upper())
            if script and rec.get('FormID'):
                bases[rec['FormID'].upper()] = (rec.get('EditorID', ''),
                                                script)
    return bases


#: The authored placement `SetAtStart` restores, as the export spells it.
_PLACEMENT_KEYS = ('PosX', 'PosY', 'PosZ', 'RotX', 'RotY', 'RotZ')


def _placement(rec: dict) -> str:
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


def _instance_lines(export_dir: str, bases: dict, plugin_name: str) -> list:
    """`placement FormID=Plugin.esm|base id|script|x,y,z,rx,ry,rz` per placed
    ref running one.

    One line per placement, not per base: a table keyed by the base would give
    every door of a kind one shared variable set.

    🛑 The plugin's FULL file name rides in the row, exactly as
    `refs_formid.txt` carries it, never rebuilt from the sidecar FOLDER name.
    See: docs/plans/morrowind_object_scripts.md#instances
    """
    lines = []
    for name in _PLACEMENT_EXPORTS:
        for rec in export_records(os.path.join(export_dir, name),
                                  ('FormID', 'NAME') + _PLACEMENT_KEYS):
            found = bases.get(rec.get('NAME', '').upper())
            if found and rec.get('FormID'):
                base_id, script = found
                lines.append(f"{rec['FormID']}={plugin_name}|{base_id}|"
                             f"{script}|{_placement(rec)}")
    return lines


def _object_script_lines(export_dir: str, by_formid: dict) -> list:
    """`object=script` for every scripted object whose script was resolved."""
    lines = []
    for name in _SCRIPTED_EXPORTS:
        for rec in export_records(os.path.join(export_dir, name),
                                  ('EditorID', 'SCRI')):
            script = by_formid.get(rec.get('SCRI', '').upper())
            if script and rec.get('EditorID'):
                lines.append(f"{rec['EditorID']}={script}")
    return lines


def _loaded_dirs(root: str, own_dir: str, plugin: str) -> list:
    """`(record_dir, plugin, own index byte)` for this plugin and each
    `_HEADER.txt` master that has an export: the files the GAME loads, in
    the order it resolves them.

    A table keyed by TES3 id has to point at THESE, not at the TES3 masters:
    in Morroblivion mode the game never loads a converted Morrowind.esm, and
    `p_restore_willpower_e` is Morrowind_ob.esm's `0pUrestoreUwillpowerUe`.
    `load_index` answers a raw id through that escape.
    See: docs/commentary/morrowind_runtime.md#sidecar
    """
    loaded = [(own_dir, plugin)] + [
        (str(source_registry.record_dir(root, master)), master)
        for master in masters_from_export_header(own_dir)]
    return [(folder, name, len(masters_from_export_header(folder)))
            for folder, name in loaded if os.path.isdir(folder)]


def _wanted_ids(dirs: list, ids: dict, exports: tuple) -> dict:
    """`{lower id: id}`: `ids` from the TES3 binaries, plus each record the
    exported plugins in `dirs` define in `exports`."""
    wanted = dict(ids)
    for folder, _plugin in dirs:
        for name in exports:
            for rec in export_records(os.path.join(folder, name), ('EditorID',)):
                edid = rec.get('EditorID', '')
                if edid:
                    wanted.setdefault(edid.lower(), edid)
    return wanted


def _owned_lines(dirs: list, root: str, ids: dict, exports: tuple,
                 types: tuple, extra: dict = None) -> list:
    """`id=Plugin|FormID` for every id of `exports`, resolved through the
    plugins the game loads -- the NEAREST one defining it wins. `extra` adds a
    third field per id, for a table that carries one.

    The runtime resolves the pair through the running load order, so the
    FormID is kept as its owner wrote it and never re-indexed here.
    """
    wanted = _wanted_ids(dirs, ids, exports)
    indexes = [(plugin, load_index(folder, types, {own: own}))
               for folder, plugin, own in _loaded_dirs(root, *dirs[0])]
    lines = []
    for key, edid in wanted.items():
        for plugin, index in indexes:
            formid = index.lookup(edid)
            if not formid:
                continue
            tail = (extra or {}).get(key, '')
            lines.append(f'{edid}={plugin}|{formid}'
                         + (f'|{tail}' if tail else ''))
            break
    return lines


def _spell_effects(dirs: list) -> dict:
    """`{lower spell id: comma-joined TES3 effect indices}` over the chain.

    EVERY effect, not just the first: `RemoveEffects` names one effect index
    and removes each spell CONTAINING it, so the runtime has to know a spell's
    whole effect list to decide whether that spell matches.
    See: docs/commentary/morrowind_runtime.md#spell-commands
    """
    keys = tuple(f'Effect[{i}].MorrowindIndex' for i in range(_MAX_EFFECTS))
    found = {}
    for folder, _plugin, _own in dirs:
        for rec in export_records(os.path.join(folder, _SPELL_EXPORTS[0]),
                                  ('EditorID',) + keys):
            edid = rec.get('EditorID', '')
            indices = [rec[key] for key in keys if rec.get(key)]
            if edid and indices:
                found.setdefault(edid.lower(), ','.join(indices))
    return found


def _effect_lines(dirs: list) -> list:
    """`index=Plugin|MGEF FormID|Name` for each magic effect of the chain.

    BOTH directions are staged because the commands disagree: `GetEffect`
    names an effect (`sEffectRecall` is the export's `MW038Recall` without the
    prefix and index) while `RemoveEffects` names its TES3 INDEX.

    🛑 Only a plugin's OWN records are staged, by the index byte matching its
    master count: the same record is numbered differently in each dependent.
    See: docs/commentary/morrowind_runtime.md#spell-commands
    """
    strip = len(_EFFECT_PREFIX) + _EFFECT_DIGITS
    seen = {}
    for folder, plugin, own in dirs:
        for rec in export_records(
                os.path.join(folder, _EFFECT_EXPORT),
                ('FormID', 'EditorID', 'MorrowindEffectIndex')):
            index = rec.get('MorrowindEffectIndex', '')
            name = rec.get('EditorID', '')[strip:]
            formid = rec.get('FormID', '')
            if not index or not name or formid[:2].upper() != f'{own:02X}':
                continue
            seen.setdefault(index, f'{index}={plugin}|{formid}|{name}')
    return [seen[key] for key in sorted(seen, key=int)]


def _soul_lines(dirs: list, raw: dict) -> list:
    """`creature id=soul size` for each CREA the chain defines, keyed by every
    spelling a script may write. `raw` is the TES3 binaries' own creature ids.

    🛑 In Morroblivion mode the export's id is ESCAPED -- `ogrim` is
    `0Ogrim` -- so each raw id is encoded and looked up, never inverted.
    See: docs/commentary/tes4_export_morrowind.md#masters
    """
    souls = {}
    for folder, _plugin, _own in dirs:
        for rec in export_records(os.path.join(folder, _CREATURE_EXPORT),
                                  ('EditorID', 'DATA.Soul')):
            edid = rec.get('EditorID', '')
            soul = rec.get('DATA.Soul', '')
            if not edid or not soul or soul == '0':
                continue
            souls.setdefault(edid.lower(), (edid, soul))
    rows = {key: f'{edid}={soul}' for key, (edid, soul) in souls.items()}
    for key, tes3 in raw.items():
        found = souls.get(key) or souls.get(encode_editor_id(tes3).lower())
        if found:
            rows.setdefault(key, f'{tes3}={found[1]}')
    return list(rows.values())


def _soulgem_lines(dirs: list) -> list:
    """`filled gem id=Plugin|FormID` for each gem of the chain.

    🛑 Over the LOADED plugins, not this plugin's own export: the six vanilla
    gems and their filled variants belong to the Morroblivion compatibility
    patch, so a Tamriel Rebuilt conversion owns none and would stage nothing.
    Each row is attributed to the plugin whose master count matches the id's
    index byte, as `_effect_lines` does.
    See: docs/commentary/morrowind_runtime.md#soul-gems
    """
    seen = {}
    for folder, plugin, own in dirs:
        for rec in export_records(os.path.join(folder, _SOULGEM_EXPORTS[0]),
                                  ('FormID', 'EditorID', 'SOUL')):
            edid = rec.get('EditorID', '')
            formid = rec.get('FormID', '')
            soul = rec.get('SOUL', '0')
            if not edid or soul == '0' or formid[:2].upper() != f'{own:02X}':
                continue
            seen.setdefault(edid.lower(), f'{edid}={plugin}|{formid}')
    return list(seen.values())


def _placed_refs(folder: str, owner: str) -> dict:
    """`base FormID -> placement FormID` for the references `folder` OWNS.

    First placement wins, which is what OpenMW's `searchPtr` does within a
    cell store.
    See: docs/commentary/morrowind_runtime.md#placed-references
    """
    placed = {}
    for name in _PLACEMENT_EXPORTS:
        for rec in export_records(os.path.join(folder, name),
                                  ('FormID', 'NAME')):
            base = rec.get('NAME', '')
            formid = rec.get('FormID', '')
            if base and formid[:2].upper() == owner and base not in placed:
                placed[base] = formid
    return placed


def _base_lines(dirs: list, root: str, ids: dict) -> list:
    """`id=Plugin|FormID` for each TES3 id's BASE record, never a placement.
    See: docs/plans/morrowind_object_scripts.md#placeatpc
    """
    return _owned_lines(dirs, root, ids, _SCRIPTED_EXPORTS, _SCRIPTED_TYPES)


def _ref_lines(dirs: list, root: str, ids: dict) -> list:
    """`id=Plugin|FormID` for each TES3 id with a placed reference, resolved
    through the plugins the game loads: a placement counts when the same
    plugin defines the base it names.

    The id is the BASE record's EditorID and the FormID is the PLACEMENT's,
    because `id->Disable` acts on the thing in the world, not its template.
    See: docs/commentary/morrowind_runtime.md#placed-references
    """
    wanted = _wanted_ids(dirs, ids, _SCRIPTED_EXPORTS)
    seen = {}
    for folder, plugin, own in _loaded_dirs(root, *dirs[0]):
        placed = _placed_refs(folder, f'{own:02X}')
        if not placed:
            continue
        index = load_index(folder, _SCRIPTED_TYPES, {own: own})
        for key, edid in wanted.items():
            base = index.lookup(edid) if key not in seen else None
            ref = placed.get(base) if base else None
            if ref:
                seen[key] = f'{edid}={plugin}|{ref}'
    return list(seen.values())


def _cell_lines(export_dir: str, plugin_name: str) -> list:
    """`cell name=Plugin|FormID` for each cell this plugin defines: the CELL
    of an interior, the WORLDSPACE of an exterior, attributed to the loaded
    plugin its index byte names.

    Both names a cell carries are keyed, because a script may write either:
    the export keeps the cell's own name as its `EditorID`, while `FULL`
    repeats that for an interior and names the REGION for an exterior.
    See: docs/commentary/morrowind_runtime.md#positioncell-moves-into-the-cell
    """
    masters = masters_from_export_header(export_dir)
    lines = []
    for rec in export_records(os.path.join(export_dir, _CELL_EXPORT),
                              ('FormID', 'EditorID', 'FULL', 'ParentWRLD')):
        place = rec.get('ParentWRLD') or rec.get('FormID', '')
        if not place:
            continue
        index = int(place[:2], 16)
        owner = masters[index] if index < len(masters) else plugin_name
        for key in ('EditorID', 'FULL'):
            if rec.get(key):
                lines.append(f'{rec[key]}={owner}|{place}')
    return sorted(set(lines))


def _write_lines(path: str, lines: list) -> int:
    """Write a table; 1 when it has anything in it, else 0 and NO file.

    An empty table DELETES any file left by an earlier build: the runtime
    merges every sidecar folder first-write-wins, so a stale table silently
    beats the plugin that owns the rows now.
    """
    if not lines:
        if os.path.isfile(path):
            os.remove(path)
        return 0
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    return 1


def write_script_tables(export_dir: str, out_dir: str, plugin_name: str,
                        gathered: dict = None) -> int:
    """The export-built tables, into `out_dir`. Returns files written.
    `gathered` is what `gather` read from the TES3 binaries, when any.

    🛑 THIS PLUGIN'S OWN records only -- a master stages its own sidecar and
    the runtime reads every sidecar into one set of tables.
    See: docs/plans/morrowind_object_scripts.md#masters-stage-themselves
    """
    dirs = table_dirs(export_dir, plugin_name)
    root = _export_root(export_dir)
    ids = gathered or {}
    loaded = _loaded_dirs(root, export_dir, plugin_name)
    locals_lines, body_lines, by_formid = _script_tables(
        _resolution_dirs(dirs, loaded))
    return (_write_lines(os.path.join(out_dir, SPELLS_TABLE),
                         _owned_lines(dirs, root, ids.get('spells', {}),
                                      _SPELL_EXPORTS, _SPELL_TYPES,
                                      _spell_effects(loaded)))
            + _write_lines(os.path.join(out_dir, EFFECTS_TABLE),
                           _effect_lines(loaded))
            + _write_lines(os.path.join(out_dir, SOULGEMS_TABLE),
                           _soulgem_lines(loaded))
            + _write_lines(os.path.join(out_dir, SOULS_TABLE),
                           _soul_lines(loaded, ids.get('objects', {})))
            + _write_lines(os.path.join(out_dir, GLOBALS_TABLE),
                           _global_lines(dirs[:1]))
            + _write_lines(os.path.join(out_dir, SCRIPT_BODIES_TABLE),
                           body_lines)
            + _write_lines(os.path.join(out_dir, SCRIPT_LOCALS_TABLE),
                           locals_lines)
            + _write_lines(os.path.join(out_dir, ACTOR_SCRIPTS_TABLE),
                           _object_script_lines(export_dir, by_formid))
            + _write_lines(os.path.join(out_dir, SCRIPT_INSTANCES_TABLE),
                           _instance_lines(export_dir,
                                           _scripted_bases(export_dir,
                                                           by_formid),
                                           plugin_name))
            + _write_lines(os.path.join(out_dir, ITEMS_TABLE),
                           _owned_lines(dirs, root, ids.get('items', {}),
                                        _ITEM_EXPORTS, _ITEM_TYPES))
            + _write_lines(os.path.join(out_dir, REFS_TABLE),
                           _ref_lines(dirs, root, ids.get('objects', {})))
            + _write_lines(os.path.join(out_dir, BASES_TABLE),
                           _base_lines(dirs, root, ids.get('objects', {})))
            + _write_lines(os.path.join(out_dir, CELLS_TABLE),
                           _cell_lines(export_dir, plugin_name)))


def _stage_dialogue(export_dir: str, out_dir: str, present: list,
                    chain: list, gathered: dict) -> int:
    """The dialogue and the actor table, MERGED over the plugin's TES3 masters
    when its binary can be found; else this plugin's own export, copied.

    🛑 TODO: this merge DUPLICATES every master's dialogue into each dependent
    -- 76 MB against TR_Mainland's own 51 MB -- and must move to per-owner
    staging. It cannot just be deleted: `Ordinal` is the only order the runtime
    has and it is only meaningful across a merged chain, so PNAM/NNAM must be
    exported and merged at load FIRST.
    See: docs/plans/morrowind_object_scripts.md#cumulative-gather-must-go
    """
    if not chain:
        for name in present:
            staged_as = DIALOGUE_FILES[_EXPORT_DIALOGUE.index(name)]
            shutil.copyfile(os.path.join(export_dir, name),
                            os.path.join(out_dir, staged_as))
        return len(present)
    topics, infos = write_merged_dialogue(gathered, out_dir)
    folders = [(folder, plugin) for folder, plugin, _own in _loaded_dirs(
        _export_root(export_dir), export_dir, chain[-1][0])]
    gathered['travel'] = travel_lines(gathered,
                                      marker_index(folders, export_records))
    print(f'    sidecar: {topics} topics, {infos} responses merged over '
          f'{", ".join(name for name, _path in chain)}')
    staged = len(DIALOGUE_FILES)
    for name, key in ((ACTORS_TABLE, 'actors'), (FACTIONS_TABLE, 'factions'),
                      (GMST_TABLE, 'gmsts'), (SKILLS_TABLE, 'skills'),
                      (START_SCRIPTS_TABLE, 'start_scripts'),
                      (TRAVEL_TABLE, 'travel')):
        staged += _write_lines(os.path.join(out_dir, name),
                               list(gathered[key].values()))
    return staged


def _journal_quests(writer, out_dir: str, plugin_name: str) -> int:
    """The journal QUSTs and their id table, when there is a `writer` to add
    records to; a restage with no import leaves the existing table alone.

    `quest_morrowind` is imported HERE because it imports this module for the
    export reader: a module-scope import would be a cycle.
    """
    if writer is None:
        return 0
    from .quest_morrowind import write_journal_quests
    quests = write_journal_quests(writer, out_dir, plugin_name)
    print(f'    sidecar: {quests} journal quest(s) written as QUST')
    from .ai_packages_morrowind import write_ai_packages
    packages = write_ai_packages(writer, out_dir, plugin_name)
    print(f'    sidecar: {packages} AI package(s) written as PACK')
    return 1 if quests else 0


def is_tes3_export(export_dir: str) -> bool:
    """True when this export came from TES3, by its dialogue signatures."""
    return any(os.path.isfile(os.path.join(export_dir, name))
               for name in _EXPORT_DIALOGUE)


def _converted_sounds(esm_path: str) -> dict:
    """`lowercased EditorID -> SNDR FormID` for every SOUN in a converted plugin.

    Case-folded because Morrowind ids are case-insensitive while the records
    keep their authored spelling. The SNDR is the SDSC the SOUN names, READ
    rather than re-derived, so no id is minted in the wrong index space.
    """
    from ..base.tes5_reader import first_sub
    from ..overrides.master_index import MasterIndex
    out = {}
    index = MasterIndex(esm_path)
    for formid in index.formids():
        if index.signature(formid) != b'SOUN':
            continue
        body = index.record(formid)[_TES5_HEADER]
        edid = first_sub(body, b'EDID')
        sdsc = first_sub(body, b'SDSC')
        if edid is None or sdsc is None or len(sdsc) < 4:
            continue
        key = edid.rstrip(b'\0').decode('ascii', 'replace').lower()
        out.setdefault(key, struct.unpack_from('<I', sdsc, 0)[0])
    return out


def _sound_owners(export_dir: str, output_root: str) -> list:
    """`(plugin, {edid: SNDR})` for each converted master, nearest first.

    The chain comes from the EXPORT header, not the source binary: in
    Morroblivion mode the binary still names Morrowind/Tribunal/Bloodmoon while
    the conversion actually resolves against Morrowind_ob and the gap patch.
    See: docs/commentary/tes4_export_morrowind.md#masters
    """
    from output_layout import plugin_esm
    root = _export_root(export_dir)
    owners = []
    for master in masters_from_export_header(export_dir):
        esm = str(plugin_esm(output_root, master, root))
        if os.path.isfile(esm):
            owners.append((master, _converted_sounds(esm)))
    return owners


def stage_sound_table(export_dir: str, output_path: str, plugin_name: str,
                      own: dict) -> int:
    """Write the TES3 sound table for the runtime; how many rows it holds.

    `own` is `{EditorID: SNDR}` for the SOUNs this import converted. Every
    other authored sound id of the TES3 chain is looked for in each converted
    master, by the raw id AND by its Morroblivion escape -- a Morroblivion-mode
    master spells `Door Stone Open` `0DoorSStoneSOpen`, so a raw comparison
    matches none of its 567 sounds.

    See: docs/commentary/tes5_import_sound.md#the-runtime-sound-table
    """
    if not is_tes3_export(export_dir):
        return 0
    plugin = os.path.basename(plugin_name)
    chain = plugin_chain(_export_root(export_dir), plugin)
    ids = gather(chain)['sounds'] if chain else {}
    rows = {edid.lower(): f'{edid}={plugin}|{sndr:08X}'
            for edid, sndr in own.items() if edid and sndr}
    output_root = os.path.dirname(os.path.dirname(output_path))
    for master, sounds in _sound_owners(export_dir, output_root):
        for key, tes3 in ids.items():
            if key in rows:
                continue
            sndr = sounds.get(key) or sounds.get(
                encode_editor_id(tes3).lower())
            if sndr:
                rows[key] = f'{tes3}={master}|{sndr:08X}'
    if not rows:
        return 0
    out_dir = sidecar_dir(output_path, plugin_name)
    os.makedirs(out_dir, exist_ok=True)
    _write_lines(os.path.join(out_dir, SOUNDS_TABLE), sorted(rows.values()))
    return len(rows)


def write_morrowind_sidecar(export_dir: str, output_path: str,
                            plugin_name: str, writer=None) -> int:
    """Stage this plugin's dialogue and tables into its SKSE sidecar folder,
    and with a `writer`, add its journal quests to the plugin being written.

    Returns files staged; 0 for a source that is not TES3. The compat patch
    holds no dialogue of its own yet stages anyway: it alone carries vanilla's
    GLOBs, and a global no sidecar holds makes the condition testing it PASS.
    See: docs/commentary/tes4_export_morrowind.md#globals-are-always-filled
    """
    if not (is_tes3_export(export_dir)
            or os.path.basename(plugin_name) == PATCH_NAME):
        return 0
    present = [name for name in _EXPORT_DIALOGUE
               if os.path.isfile(os.path.join(export_dir, name))]
    out_dir = sidecar_dir(output_path, plugin_name)
    os.makedirs(out_dir, exist_ok=True)
    chain = plugin_chain(_export_root(export_dir), plugin_name)
    gathered = gather(chain) if chain else {}
    staged = (_stage_dialogue(export_dir, out_dir, present, chain, gathered)
              + write_script_tables(export_dir, out_dir, plugin_name,
                                    gathered)
              + _write_lines(os.path.join(out_dir, SAY_TABLE),
                             say_rows(export_dir,
                                      os.path.basename(plugin_name)))
              + _journal_quests(writer, out_dir, plugin_name))
    index = _actor_index(export_dir)
    if not index:
        return staged
    with open(os.path.join(out_dir, ACTOR_INDEX), 'w',
              encoding='utf-8') as handle:
        handle.write(index)
    return staged + 1

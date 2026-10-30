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
from output_layout import plugin_esm
from core.skyrim_language import text_codec
from core.plugin_masters import (export_root, export_source,
                                 get_masters_from_binary,
                                 masters_from_export_header)
from tes4_export.morrowind_ids import encode_editor_id, load_index
from tes4_export.morrowind_patch import PATCH_NAME, START_SCRIPTS_SIG

from .morrowind_placements import (PLACED_EXPORTS, SCRIPTED_EXPORTS, SCRIPTED_TYPES, TES4_PLUGIN_PREFIX,
                                   cell_place, export_records, first_placements, folder_tables,
                                   forget_folder_tables, master_scripted,
                                   placement, rebased_formid)
from .morrowind_sidecar_source import (forget_gathered, gather,
                                       own_dialogue_blocks, source_chain,
                                       spell_kinds, weapon_kinds)
from .morrowind_temples import skyrim_rows
from ..record_types.magic_morrowind import effect_form_rows
from ..record_types.weather_morrowind import weather_rows
from ..actors.brawl import brawl_rows
from ..actors.disposition import disposition_rows
from .morrowind_teleport import (ANCHORS_TABLE, EFFECT_FORMS_TABLE, MARKERS_TABLE,
                                 TELEPORTS_TABLE, WORLDS_TABLE, marker_lines,
                                 target_lines, teleport_lines, world_lines)
from .morrowind_travel import (CELL_REGIONS_TABLE, EXTERIOR_NAMES_TABLE, TRAVEL_TABLE,
                               cell_region_lines, exterior_name_lines,
                               marker_index, travel_lines)
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

#: What the player's faction pushes need: `faction id=Plugin.esm|FACT FormID`.
FACTION_FORMS_TABLE = 'factions_formid.txt'

#: The GLOBs the runtime publishes bark state into: `journal:<id>=Plugin.esm|FormID`.
STATE_TABLE = 'state_formid.txt'

#: The crime faction PC Crime Level and the fine and jail opcodes act on: `crime=Plugin.esm|FormID`.
CRIME_TABLE = 'crime_formid.txt'

#: Every GMST of the chain, `name=type,value`, and the SKIL rows persuasion credits skill use from.
GMST_TABLE = 'GMST.txt'
SKILLS_TABLE = 'SKIL.txt'

#: Each race's starting attributes under the Skyrim races a player of it wears: `FORMID=race|male|female`.
RACES_TABLE = 'RACE.txt'

#: What AddItem and its kin need: `item id=Plugin.esm|FormID`, plus `|TES3 type` on a weapon.
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

#: The exports the actor index is built from.
_ACTOR_EXPORTS = ('NPC_.txt', 'CREA.txt')

#: The exports whose records can sit in an inventory.
_ITEM_EXPORTS = ('ALCH.txt', 'AMMO.txt', 'APPA.txt', 'ARMO.txt', 'BOOK.txt',
                 'CLOT.txt', 'INGR.txt', 'KEYM.txt', 'LIGH.txt', 'MISC.txt',
                 'WEAP.txt')
#: The exports holding spells, which AddSpell and GetSpell name by TES3 id.
_SPELL_EXPORTS = ('SPEL.txt',)

#: The factions `PCJoinFaction` and its kin name by TES3 id.
_FACTION_EXPORTS = ('FACT.txt',)

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
_SPELL_TYPES = tuple(name[:-4] for name in _SPELL_EXPORTS)
_FACTION_TYPES = tuple(name[:-4] for name in _FACTION_EXPORTS)
_GLOBAL_EXPORT = 'GLOB.txt'
_SCRIPT_EXPORT = 'SCPT.txt'

#: A local declaration in MWScript source: `short name`, `long name`, `float name`.
_DECLARATION = re.compile(r'^\s*(short|long|float)\s+([A-Za-z_][A-Za-z0-9_]*)',
                          re.IGNORECASE)

def plugin_stem(plugin_name: str) -> str:
    """`Morrowind.esm` -> `Morrowind`, the per-plugin sidecar folder name."""
    return os.path.splitext(os.path.basename(plugin_name))[0]


def sidecar_dir(output_path: str, plugin_name: str) -> str:
    """The folder a plugin's sidecar lives in, beside its output ESM."""
    return os.path.join(os.path.dirname(output_path), SIDECAR_DIR,
                        plugin_stem(plugin_name))


def _scripted_records(folder: str, name: str) -> list:
    """The records of one `SCRIPTED_EXPORTS` file at `folder`: FormID, EditorID, SCRI."""
    return [rec for export, rec in folder_tables(folder)['scripted'] if export == name]


def _actor_index(export_dir: str) -> str:
    """`FormID=EditorID` for every NPC and creature: the TES3 id dialogue filters on.

    The runtime routes activation by FormID but filters by TES3 id, so without
    this index it can tell an actor is Morrowind's and still not know who they
    are. Built here because the FormID is minted during import.
    See: docs/commentary/morrowind_runtime.md#activation
    """
    lines = [f"{rec['FormID']}={rec['EditorID']}"
             for name in _ACTOR_EXPORTS for rec in _scripted_records(export_dir, name)
             if rec.get('FormID') and rec.get('EditorID')]
    return '\n'.join(lines) + ('\n' if lines else '')


def table_dirs(export_dir: str, plugin_name: str) -> list:
    """`(record_dir, plugin)` for this plugin, then each TES3 master that has
    been exported -- a dialogue script names its masters' globals freely."""
    root = export_root(export_dir)
    binary = source_registry.plugin_binary(root, plugin_name)
    masters = get_masters_from_binary(str(binary)) if binary else []
    dirs = [(export_dir, plugin_name)]
    for master in masters:
        candidate = str(source_registry.record_dir(root, master))
        if os.path.isfile(os.path.join(candidate, _GLOBAL_EXPORT)):
            dirs.append((candidate, master))
    return dirs


#: What an out-of-range or NaN FLTV reads as; the engine clamps to this.
_INT32_MIN = -2147483648
_INT32_MAX = 2147483647


def _global_value(kind: str, raw: str) -> str:
    """A GLOB's FLTV as its FNAM type reads it, as OpenMW's `floatCast` does.

    `s`/`l` truncate toward zero; out of range or NaN reads as INT32_MIN, and a
    short keeps only its low 16 bits of that. Vanilla short globals carry
    uninitialized junk (`WearingOrdinatorUni` 7.1e-31, `MuseumDonations`
    7.6e+23), which must read as 0.
    See: docs/commentary/tes4_export_morrowind.md#globals-are-always-filled
    """
    if kind.lower() not in ('s', 'l'):
        return raw
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return '0'
    whole = (_INT32_MIN if math.isnan(value) or not _INT32_MIN <= value <= _INT32_MAX
             else int(value))
    if kind.lower() == 's':
        whole = (whole + 0x8000) % 0x10000 - 0x8000
    return str(whole)


def _state_lines(dirs: list) -> list:
    """`state key=plugin|formid` per GLOB the export minted for bark state.

    See: docs/commentary/morrowind_runtime.md#published-state
    """
    return [f"{rec['MorrowindState']}={plugin}|{rec['FormID']}"
            for folder, plugin in dirs
            for rec in export_records(os.path.join(folder, _GLOBAL_EXPORT),
                                      ('MorrowindState', 'FormID'))
            if rec.get('MorrowindState') and rec.get('FormID')]


def _global_lines(dirs: list) -> list:
    """`name=type,value,plugin|formid` per GLOB, the plugin's own winning.

    The FormID is what lets the runtime read the LIVE value out of the
    converted plugin's GLOB, so a global Papyrus writes -- `CharGenState`, the
    one that starts Morrowind's chargen -- reaches the scripts polling it.
    A bark-state GLOB is no TES3 global and stays out.
    See: docs/commentary/morrowind_runtime.md#vanilla-morrowind-chargen
    """
    seen = {}
    for folder, plugin in dirs:
        for rec in export_records(os.path.join(folder, _GLOBAL_EXPORT),
                                  ('EditorID', 'FNAM.Type', 'FLTV.Value',
                                   'FormID', 'MorrowindState')):
            name = rec.get('EditorID', '')
            if rec.get('MorrowindState'):
                continue
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
    body. A dir this plugin cannot name a FormID in claims no name: the
    authored Morrowind.esm export would hide the patch's `OutsideBanner`.
    See: docs/plans/morrowind_object_scripts.md#masters-stage-themselves
    """
    lines, bodies, by_formid, seen = [], [], {}, set()
    header = masters_from_export_header(dirs[0][0])
    tes4 = plugin_stem(dirs[0][1]).lower().startswith(TES4_PLUGIN_PREFIX)
    for index, (folder, plugin) in enumerate(dirs):
        for rec in export_records(os.path.join(folder, _SCRIPT_EXPORT),
                                  ('FormID', 'EditorID', 'SCTX')):
            name = rec.get('EditorID', '')
            formid = rec.get('FormID', '')
            here = formid if index == 0 else rebased_formid(formid, folder,
                                                            plugin, header)
            if not name or not here or name.lower() in seen:
                continue
            seen.add(name.lower())
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
    """`base FormID -> (TES3 id, script name)` per scripted object this plugin
    can place: its own, then its MWScript masters' (`master_scripted`).

    The id rides along because a bare `Activate` or `Enable` inside the body
    acts on the object running it, which the runtime resolves BY id.
    """
    bases = {}
    for _name, rec in folder_tables(export_dir)['scripted']:
        script = by_formid.get(rec.get('SCRI', '').upper())
        if script and rec.get('FormID'):
            bases[rec['FormID'].upper()] = (rec.get('EditorID', ''), script)
    header = masters_from_export_header(export_dir)
    for base, rec, master_dir, master in master_scripted(export_dir):
        scri = rebased_formid(rec['SCRI'], master_dir, master, header) or ''
        script = by_formid.get(scri.upper())
        if script:
            bases.setdefault(base, (rec.get('EditorID', ''), script))
    return bases


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
    for rec in folder_tables(export_dir)['instances']:
        found = bases.get(rec['NAME'].upper())
        if found:
            base_id, script = found
            lines.append(f"{rec['FormID']}={plugin_name}|{base_id}|"
                         f"{script}|{placement(rec)}")
    return lines


def _object_script_lines(export_dir: str, by_formid: dict) -> list:
    """`object=script` for every scripted object whose script was resolved."""
    lines = []
    for _name, rec in folder_tables(export_dir)['scripted']:
        script = by_formid.get(rec.get('SCRI', '').upper())
        if script and rec.get('EditorID'):
            lines.append(f"{rec['EditorID']}={script}")
    return lines


def _loaded_dirs(root: str, own_dir: str, plugin: str) -> list:
    """`(record_dir, plugin, own index byte)` for this plugin and each
    `_HEADER.txt` master that has an export: the files the GAME loads,
    NEAREST first, as a later plugin's record of an id overrides an earlier's.

    A table keyed by TES3 id has to point at THESE, not at the TES3 masters:
    in Morroblivion mode the game never loads a converted Morrowind.esm, and
    `p_restore_willpower_e` is Morrowind_ob.esm's `0pUrestoreUwillpowerUe`.
    `load_index` answers a raw id through that escape.
    See: docs/commentary/morrowind_runtime.md#sidecar
    """
    loaded = [(own_dir, plugin)] + [
        (str(source_registry.record_dir(root, master)), master)
        for master in reversed(masters_from_export_header(own_dir))]
    return [(folder, name, len(masters_from_export_header(folder)))
            for folder, name in loaded if os.path.isdir(folder)]


def _wanted_ids(dirs: list, ids: dict, exports: tuple) -> dict:
    """`{lower id: id}`: `ids` from the TES3 binaries, plus each record the
    exported plugins in `dirs` define in `exports`."""
    wanted = dict(ids)
    for folder, _plugin in dirs:
        for name in exports:
            records = (_scripted_records(folder, name) if name in SCRIPTED_EXPORTS
                       else export_records(os.path.join(folder, name), ('EditorID',)))
            for rec in records:
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


def _spell_effects(dirs: list, kinds: dict) -> dict:
    """`{lower spell id: 'effect indices|TES3 type'}` over the chain.

    EVERY effect, not just the first: `RemoveEffects` names one effect index
    and removes each spell CONTAINING it, so the runtime has to know a spell's
    whole effect list to decide whether that spell matches. The type (`kinds`)
    is what `GetCommonDisease` and `GetBlightDisease` sort spells by.
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
    return {key: found.get(key, '') + (f'|{kinds[key]}' if key in kinds else '')
            for key in set(found) | set(kinds)}


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


def _kept_dirs(loaded: list, export_dir: str, plugin_name: str) -> list:
    """The `loaded` dirs whose records this sidecar stages, by `_row_keeper`."""
    keeps = _row_keeper(export_dir, plugin_name)
    return [entry for entry in loaded if keeps(entry[1])]


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


def _weapon_types(gathered: dict) -> dict:
    """`{lower weapon id: TES3 type}`, the items table's third field."""
    return {key: str(kind) for key, kind
            in weapon_kinds(gathered.get('table_records', [])).items()}


def _line_ids(gathered: dict, table: str) -> dict:
    """`{lower id: id}` for a gathered table whose values are `id=...` lines."""
    return {key: line.split('=', 1)[0]
            for key, line in gathered.get(table, {}).items() if line}


def _base_lines(dirs: list, root: str, ids: dict) -> list:
    """`id=Plugin|FormID` for each TES3 id's BASE record, never a placement.
    See: docs/plans/morrowind_object_scripts.md#placeatpc
    """
    return _owned_lines(dirs, root, ids, SCRIPTED_EXPORTS, SCRIPTED_TYPES)


def _ref_lines(dirs: list, root: str, ids: dict) -> list:
    """`id=Plugin|FormID` for each TES3 id with a placed reference, resolved
    through the plugins the game loads: a placement counts when the same
    plugin defines the base it names, and its FIRST placement wins, as
    OpenMW's `searchPtr` does within a cell store.

    The id is the BASE record's EditorID and the FormID is the PLACEMENT's,
    because `id->Disable` acts on the thing in the world, not its template.
    See: docs/commentary/morrowind_runtime.md#placed-references
    """
    wanted = _wanted_ids(dirs, ids, PLACED_EXPORTS)
    seen = {}
    for folder, plugin, own in _loaded_dirs(root, *dirs[0]):
        unseen = {key: edid for key, edid in wanted.items() if key not in seen}
        for key, ref in first_placements(folder, own, unseen).items():
            seen[key] = f'{wanted[key]}={plugin}|{ref}'
    return list(seen.values())


def _cell_rows(folder: str, plugin: str, wilderness: str) -> set:
    """`cell name=Plugin|FormID` for each cell the export at `folder` names:
    the CELL of an interior, the WORLDSPACE of an exterior, attributed to the
    loaded plugin its index byte names.

    Both names a cell carries are keyed: its own name (`EditorID`) and `FULL`,
    which names the REGION of an exterior. An exterior is also keyed by
    `wilderness`, OpenMW's name for an exterior with neither.
    See: docs/commentary/morrowind_runtime.md#positioncell-moves-into-the-cell
    """
    masters = masters_from_export_header(folder)
    rows = set()
    for rec in folder_tables(folder)['cells']:
        place = cell_place(rec, masters, plugin)
        if not place:
            continue
        names = [rec[key] for key in ('EditorID', 'FULL') if rec.get(key)]
        rows.update(f'{name}={place}'
                    for name in names + ([wilderness] if rec.get('ParentWRLD') else []))
    return rows


def _wilderness(gathered: dict) -> str:
    """`sDefaultCellname`: OpenMW's name for an exterior with no name or region."""
    line = gathered.get('gmsts', {}).get('sdefaultcellname', '')
    return line.partition('=')[2][2:] or 'Wilderness'


def _cell_names(gathered: dict) -> set:
    """The lowercased cell, region and wilderness names the TES3 chain authors."""
    return gathered.get('cells', set()) | {_wilderness(gathered).lower()} | {
        name.lower() for name in gathered.get('regions', {}).values()}


def _cell_lines(loaded: list, keeps, names: set, wilderness: str) -> list:
    """This plugin's cell rows that no master's export repeats -- a renamed
    override is its own -- plus the rows naming a TES3 cell or region in
    `names` of each master it `keeps`, which has no sidecar to stage them.

    See: docs/plans/morrowind_object_scripts.md#cumulative-gather-must-go
    """
    lines = _cell_rows(*loaded[0][:2], wilderness)
    for folder, plugin, _own in loaded[1:]:
        rows = _cell_rows(folder, plugin, wilderness)
        if keeps(plugin):
            lines |= {row for row in rows
                      if row.partition('=')[0].lower() in names}
        else:
            lines -= rows
    return sorted(lines)


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
    root = export_root(export_dir)
    ids = gathered or {}
    loaded = _loaded_dirs(root, export_dir, plugin_name)
    locals_lines, body_lines, by_formid = _script_tables(
        _resolution_dirs(dirs, loaded))
    return (_write_lines(os.path.join(out_dir, SPELLS_TABLE),
                         _owned_lines(dirs, root, ids.get('spells', {}),
                                      _SPELL_EXPORTS, _SPELL_TYPES,
                                      _spell_effects(loaded, spell_kinds(
                                          ids.get('table_records', [])))))
            + _write_lines(os.path.join(out_dir, EFFECTS_TABLE),
                           _effect_lines(loaded))
            + _write_lines(os.path.join(out_dir, SOULGEMS_TABLE),
                           _soulgem_lines(loaded))
            + _write_lines(os.path.join(out_dir, SOULS_TABLE),
                           _soul_lines(_kept_dirs(loaded, export_dir,
                                                  plugin_name),
                                       ids.get('objects', {})))
            + _write_lines(os.path.join(out_dir, GLOBALS_TABLE),
                           _global_lines(dirs[:1]))
            + _write_lines(os.path.join(out_dir, STATE_TABLE),
                           _state_lines(dirs[:1]))
            + _write_lines(os.path.join(out_dir, FACTION_FORMS_TABLE),
                           _owned_lines(dirs, root, _line_ids(ids, 'factions'),
                                        _FACTION_EXPORTS, _FACTION_TYPES))
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
                                        _ITEM_EXPORTS, _ITEM_TYPES,
                                        _weapon_types(ids)))
            + _write_lines(os.path.join(out_dir, REFS_TABLE),
                           _ref_lines(dirs, root, ids.get('objects', {})))
            + _write_lines(os.path.join(out_dir, BASES_TABLE),
                           _base_lines(dirs, root, ids.get('objects', {})))
            + _write_lines(os.path.join(out_dir, CELLS_TABLE),
                           _cell_lines(loaded,
                                       _row_keeper(export_dir, plugin_name),
                                       _cell_names(ids), _wilderness(ids)))
            + _write_lines(os.path.join(out_dir, EXTERIOR_NAMES_TABLE),
                           exterior_name_lines(ids))
            + _write_lines(os.path.join(out_dir, CELL_REGIONS_TABLE),
                           cell_region_lines(ids)))


def _export_starts(export_dir: str) -> dict:
    """`{lower id: 'id=1'}` for each start script this export authors."""
    rows = export_records(
        os.path.join(export_dir, f'{START_SCRIPTS_SIG}.txt'), ('EditorID',))
    return {rec['EditorID'].lower(): f"{rec['EditorID']}=1"
            for rec in rows if rec.get('EditorID')}


def _gathered(export_dir: str, plugin_name: str, chain: list) -> dict:
    """What `gather` reads from `chain`: the Morroblivion patch keeps every
    vanilla plugin's own records and its export's start scripts.

    See: docs/plans/morrowind_object_scripts.md#cumulative-gather-must-go
    """
    if not chain:
        return {}
    if os.path.basename(plugin_name) != PATCH_NAME:
        return gather(chain)
    whole = gather(chain, whole=True)
    whole['start_scripts'] = _export_starts(export_dir)
    return whole


def _stage_dialogue(export_dir: str, out_dir: str, present: list,
                    chain: list, gathered: dict, plugin_name: str) -> int:
    """The plugin's OWN dialogue and actor tables, read from its binary with
    the links the runtime merges by; else its own export, copied unlinked.

    See: docs/plans/morrowind_object_scripts.md#cumulative-gather-must-go
    """
    if not chain:
        for name in present:
            staged_as = DIALOGUE_FILES[_EXPORT_DIALOGUE.index(name)]
            shutil.copyfile(os.path.join(export_dir, name),
                            os.path.join(out_dir, staged_as))
        return len(present) + _write_lines(
            os.path.join(out_dir, START_SCRIPTS_TABLE),
            list(_export_starts(export_dir).values()))
    dial_blocks, info_blocks = own_dialogue_blocks(gathered)
    for name, blocks in zip(DIALOGUE_FILES, (dial_blocks, info_blocks)):
        _write_lines(os.path.join(out_dir, name),
                     ['\n\n'.join(blocks)] if blocks else [])
    folders = [(folder, plugin) for folder, plugin, _own in _loaded_dirs(
        export_root(export_dir), export_dir, plugin_name)]
    gathered['travel'] = travel_lines(gathered, marker_index(folders))
    print(f'    sidecar: {len(dial_blocks)} topics, {len(info_blocks)} '
          f'responses of its own')
    staged = len(DIALOGUE_FILES)
    for name, key in ((ACTORS_TABLE, 'actors'), (FACTIONS_TABLE, 'own_factions'),
                      (GMST_TABLE, 'own_gmsts'), (SKILLS_TABLE, 'skills'),
                      (RACES_TABLE, 'own_races'),
                      (START_SCRIPTS_TABLE, 'start_scripts'),
                      (TRAVEL_TABLE, 'travel')):
        staged += _write_lines(os.path.join(out_dir, name),
                               list(gathered[key].values()))
    return staged


def _master_sidecars(export_dir: str, output_path: str) -> list:
    """`(plugin, sidecar dir)` for each export-header master with a staged
    sidecar, in load order."""
    root = export_root(export_dir)
    output_root = os.path.dirname(os.path.dirname(output_path))
    found = [(master, sidecar_dir(str(plugin_esm(output_root, master, root)),
                                  master))
             for master in masters_from_export_header(export_dir)]
    return [(master, folder) for master, folder in found
            if os.path.isdir(folder)]


def _journal_quests(writer, out_dir: str, plugin_name: str,
                    masters: list, export_dir: str) -> int:
    """The journal QUSTs and their id table, when there is a `writer` to add
    records to; a restage with no import leaves the existing table alone.

    `quest_morrowind` is imported HERE because it imports this module for the
    export reader: a module-scope import would be a cycle.
    """
    if writer is None:
        return 0
    from .quest_morrowind import write_journal_quests
    quests = write_journal_quests(writer, out_dir, plugin_name, masters)
    print(f'    sidecar: {quests} journal quest(s) written as QUST')
    from .ai_packages_morrowind import write_ai_packages
    packages = write_ai_packages(writer, out_dir, plugin_name, export_dir)
    print(f'    sidecar: {packages} AI package(s) written as PACK')
    return 1 if quests else 0


def is_tes3_export(export_dir: str) -> bool:
    """True when this export came from TES3, by its `_HEADER.txt` source."""
    return export_source(export_dir) == 'TES3'


def _converted_sounds(esm_path: str, own_only: bool = False) -> dict:
    """`lowercased EditorID -> SNDR FormID` for every SOUN in a converted
    plugin, or with `own_only` those it defines itself, as authored.

    Case-folded because Morrowind ids are case-insensitive while the records
    keep their authored spelling. The SNDR is the SDSC the SOUN names, READ
    rather than re-derived, so no id is minted in the wrong index space.
    """
    from ..base.tes5_reader import first_sub
    from ..overrides.master_index import MasterIndex
    out = {}
    index = MasterIndex(esm_path)
    for formid in index.formids():
        if index.signature(formid) != b'SOUN' or (
                own_only and formid >> 24 != index.own_index):
            continue
        body = index.record(formid)[_TES5_HEADER]
        edid = first_sub(body, b'EDID')
        sdsc = first_sub(body, b'SDSC')
        if edid is None or sdsc is None or len(sdsc) < 4:
            continue
        key = edid.rstrip(b'\0').decode(text_codec(), 'replace')
        out.setdefault(key if own_only else key.lower(),
                       struct.unpack_from('<I', sdsc, 0)[0])
    return out


def _sound_owners(export_dir: str, output_root: str, keeps) -> list:
    """`(plugin, {edid: SNDR})` for each converted master whose rows this
    sidecar `keeps`, nearest first.

    The chain comes from the EXPORT header, not the source binary: in
    Morroblivion mode the binary still names Morrowind/Tribunal/Bloodmoon while
    the conversion actually resolves against Morrowind_ob and the gap patch.
    See: docs/commentary/tes4_export_morrowind.md#masters
    """
    root = export_root(export_dir)
    owners = []
    for master in reversed(masters_from_export_header(export_dir)):
        esm = str(plugin_esm(output_root, master, root))
        if keeps(master) and os.path.isfile(esm):
            owners.append((master, _converted_sounds(esm)))
    return owners


def stage_sound_table(export_dir: str, output_path: str, plugin_name: str,
                      own: dict = None) -> int:
    """Write the TES3 sound table for the runtime; how many rows it holds.

    `own` is `{EditorID: SNDR}` for the SOUNs this import converted, else
    read from the converted plugin at `output_path` (a restage). Every
    other authored sound id of the TES3 chain is looked for in each converted
    master, by the raw id AND by its Morroblivion escape -- a Morroblivion-mode
    master spells `Door Stone Open` `0DoorSStoneSOpen`, so a raw comparison
    matches none of its 567 sounds.

    See: docs/commentary/tes5_import_sound.md#the-runtime-sound-table
    """
    plugin = os.path.basename(plugin_name)
    if not (is_tes3_export(export_dir) or plugin == PATCH_NAME):
        return 0
    output_root = os.path.dirname(os.path.dirname(output_path))
    owners = _sound_owners(export_dir, output_root,
                           _row_keeper(export_dir, plugin_name))
    chain = source_chain(export_root(export_dir), plugin) if owners else []
    ids = gather(chain, plugin == PATCH_NAME)['sounds'] if chain else {}
    forget_gathered()
    if own is None:
        own = (_converted_sounds(output_path, own_only=True)
               if os.path.isfile(output_path) else {})
    rows = {edid.lower(): f'{edid}={plugin}|{sndr:08X}'
            for edid, sndr in own.items() if edid and sndr}
    for master, sounds in owners:
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


def _crime_lines() -> list:
    """Realm crime rows; late import breaks crime->nested->actor_common->here."""
    from ..record_types.crime import default_crime_rows
    return default_crime_rows()


def _stages_base_game(loaded: list) -> bool:
    """Whether no loaded master is a TES3 plugin or the patch, staging Skyrim's."""
    return not any(is_tes3_export(folder) or plugin == PATCH_NAME
                   for folder, plugin, _own in loaded[1:])


#: The tables whose rows name their record's plugin, `...Plugin.esm|FormID`.
_OWNED_TABLES = (GLOBALS_TABLE, STATE_TABLE, FACTION_FORMS_TABLE, ITEMS_TABLE,
                 REFS_TABLE, BASES_TABLE, SOUNDS_TABLE,
                 SPELLS_TABLE, EFFECTS_TABLE, SOULGEMS_TABLE, CRIME_TABLE,
                 SAY_TABLE, ANCHORS_TABLE, MARKERS_TABLE, WORLDS_TABLE,
                 TELEPORTS_TABLE, EFFECT_FORMS_TABLE)


def _row_owner(line: str) -> str:
    """The plugin a table row names: before the `|` of its key when the key
    has one, else of its value, after any `,`. '' when it names none."""
    key, _, value = line.partition('=')
    text = key if '|' in key else value
    bar = text.find('|')
    return text[:bar].rsplit(',', 1)[-1] if bar >= 0 else ''


def _row_keeper(export_dir: str, plugin_name: str):
    """`keeps(owner)`: whether a row naming `owner` belongs in this sidecar.
    Its own plugin's rows do; a plugin that stages a sidecar stages its own;
    and the rest (Morrowind_ob's, Skyrim's) go in the sidecar no master's
    sidecar sits under -- the Morroblivion patch, or a root TES3 master.

    See: docs/plans/morrowind_object_scripts.md#cumulative-gather-must-go
    """
    root = export_root(export_dir)
    base = _stages_base_game(_loaded_dirs(root, export_dir, plugin_name))
    own = os.path.basename(plugin_name).lower()
    known = {}

    def keeps(owner: str) -> bool:
        """This sidecar's row, by the rule above."""
        if not owner or owner.lower() == own:
            return True
        if owner not in known:
            folder = str(source_registry.record_dir(root, owner))
            known[owner] = owner == PATCH_NAME or (
                os.path.isdir(folder) and is_tes3_export(folder))
        return base and not known[owner]
    return keeps


def _drop_foreign_rows(out_dir: str, export_dir: str, plugin_name: str) -> int:
    """Remove every row of `_OWNED_TABLES` another sidecar stages; returns the
    tables that emptied, which are deleted."""
    keeps = _row_keeper(export_dir, plugin_name)
    emptied = 0
    for name in _OWNED_TABLES:
        path = os.path.join(out_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding='utf-8') as handle:
            lines = handle.read().splitlines()
        kept = [line for line in lines if keeps(_row_owner(line))]
        if len(kept) != len(lines):
            emptied += 1 - _write_lines(path, kept)
    return emptied


def _markers(loaded: list, writer, master_index) -> tuple:
    """(marker rows, world rows): the chain's own, each restored Intervention's
    destinations, and Skyrim's when this plugin stages them."""
    markers = marker_lines(loaded)
    worlds = world_lines(loaded)
    if master_index is not None:
        markers += target_lines(loaded[0][0], master_index, writer.masters)
    if _stages_base_game(loaded):
        temples, parents = skyrim_rows()
        markers += temples
        worlds += parents
    return markers, worlds


def _teleport_tables(export_dir: str, output_path: str, plugin_name: str,
                     out_dir: str, writer, master_index) -> int:
    """The teleport effects, Intervention markers, worlds and interior anchors
    over the loaded chain; `crime` is imported late for the reason
    `_crime_lines` gives. A restage with no `writer` leaves all but the
    anchors alone, as only an import knows the effects and destinations.
    See: docs/commentary/morrowind_runtime.md#teleport-effects
    """
    from ..record_types.crime import anchor_rows
    loaded = _loaded_dirs(export_root(export_dir), export_dir, plugin_name)
    output_root = os.path.dirname(os.path.dirname(output_path))
    staged = _write_lines(os.path.join(out_dir, ANCHORS_TABLE),
                          anchor_rows([plugin for _dir, plugin, _own in loaded],
                                      output_root))
    if writer is None:
        return staged
    markers, worlds = _markers(loaded, writer, master_index)
    return (staged + _write_lines(os.path.join(out_dir, MARKERS_TABLE), markers)
            + _write_lines(os.path.join(out_dir, WORLDS_TABLE), worlds)
            + _write_lines(os.path.join(out_dir, TELEPORTS_TABLE),
                           teleport_lines(_effect_lines(loaded), plugin_name,
                                          len(writer.masters)))
            + _write_lines(os.path.join(out_dir, EFFECT_FORMS_TABLE),
                           effect_form_rows(plugin_name, writer.masters)
                           + disposition_rows(plugin_name, writer.masters)
                           + brawl_rows(plugin_name, writer.masters)
                           + weather_rows(plugin_name, writer.masters,
                                          master_index)))


def _apparatus(export_dir: str, output_path: str, plugin_name: str,
               gathered=None) -> int:
    """TESRuntime's apparatus sidecar, imported late: `record_types.apparatus`
    reads this module's export helpers at import."""
    from ..record_types.apparatus import write_apparatus_sidecar
    return write_apparatus_sidecar(export_dir, output_path, plugin_name,
                                   gathered)


def write_morrowind_sidecar(export_dir: str, output_path: str,
                            plugin_name: str, writer=None,
                            master_index=None) -> int:
    """Stage this plugin's dialogue and tables into its SKSE sidecar folder,
    and with a `writer`, add its journal quests to the plugin being written.
    `master_index` is the converted masters', which the teleport table reads.

    Returns files staged. A source that is not TES3 stages only its
    TESRuntime apparatus sidecar. The compat patch holds no dialogue of its own yet stages anyway: it
    alone carries vanilla's GLOBs, and a global no sidecar holds makes the
    condition testing it PASS.
    See: docs/commentary/tes4_export_morrowind.md#globals-are-always-filled
    """
    if not (is_tes3_export(export_dir)
            or os.path.basename(plugin_name) == PATCH_NAME):
        return _apparatus(export_dir, output_path, plugin_name)
    present = [name for name in _EXPORT_DIALOGUE
               if os.path.isfile(os.path.join(export_dir, name))]
    out_dir = sidecar_dir(output_path, plugin_name)
    os.makedirs(out_dir, exist_ok=True)
    chain = source_chain(export_root(export_dir), plugin_name)
    gathered = _gathered(export_dir, plugin_name, chain)
    staged = (_apparatus(export_dir, output_path, plugin_name, gathered)
              + _stage_dialogue(export_dir, out_dir, present, chain, gathered,
                                plugin_name)
              + write_script_tables(export_dir, out_dir, plugin_name,
                                    gathered)
              + _write_lines(os.path.join(out_dir, SAY_TABLE),
                             say_rows(export_dir,
                                      os.path.basename(plugin_name)))
              + _write_lines(os.path.join(out_dir, CRIME_TABLE), _crime_lines())
              + _teleport_tables(export_dir, output_path, plugin_name, out_dir,
                                 writer, master_index)
              + _journal_quests(writer, out_dir, plugin_name,
                                _master_sidecars(export_dir, output_path),
                                export_dir))
    staged -= _drop_foreign_rows(out_dir, export_dir, plugin_name)
    index = _actor_index(export_dir)
    forget_folder_tables()
    forget_gathered(keep=('sounds',))
    if not index:
        return staged
    with open(os.path.join(out_dir, ACTOR_INDEX), 'w',
              encoding='utf-8') as handle:
        handle.write(index)
    return staged + 1

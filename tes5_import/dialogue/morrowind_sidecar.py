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

import os
import re
import shutil
import struct

from asset_convert.sources import source_registry
from core.plugin_masters import (get_masters_from_binary,
                                 masters_from_export_header)
from tes4_export.morrowind_ids import encode_editor_id, load_index

from .morrowind_sidecar_source import (gather, plugin_chain,
                                       write_merged_dialogue)

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

#: What PlaySound3D and its kin need: `sound id=Plugin.esm|SNDR FormID`.
SOUNDS_TABLE = 'SOUN.txt'

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
#: The same types as the index names them.
_ITEM_TYPES = tuple(name[:-4] for name in _ITEM_EXPORTS)
_SCRIPTED_TYPES = tuple(name[:-4] for name in _SCRIPTED_EXPORTS)
_GLOBAL_EXPORT = 'GLOB.txt'
_SCRIPT_EXPORT = 'SCPT.txt'
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


def _global_lines(dirs: list) -> list:
    """`name=type,value` per GLOB, the plugin's own definition winning."""
    seen = {}
    for folder, _plugin in dirs:
        for rec in export_records(os.path.join(folder, _GLOBAL_EXPORT),
                                  ('EditorID', 'FNAM.Type', 'FLTV.Value')):
            name = rec.get('EditorID', '')
            if name and name.lower() not in seen:
                seen[name.lower()] = (f"{name}={rec.get('FNAM.Type', 'f')},"
                                      f"{rec.get('FLTV.Value', '0')}")
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


def _instance_lines(export_dir: str, bases: dict, plugin_name: str) -> list:
    """`placement FormID=Plugin.esm|base id|script` per placed ref running one.

    One line per placement, not per base: 798 scripted bases of TR_Mainland are
    placed more than once and 303 of those declare locals, so a table keyed by
    the base would give 116 doors one shared variable set.

    🛑 The plugin's FULL file name rides in the row, exactly as
    `refs_formid.txt` carries it. Rebuilding it from the sidecar FOLDER name
    and appending `.esm` bound 0 of 15,540 instances in game.
    See: docs/plans/morrowind_object_scripts.md#instances
    """
    lines = []
    for name in _PLACEMENT_EXPORTS:
        for rec in export_records(os.path.join(export_dir, name),
                                  ('FormID', 'NAME')):
            found = bases.get(rec.get('NAME', '').upper())
            if found and rec.get('FormID'):
                base_id, script = found
                lines.append(f"{rec['FormID']}={plugin_name}|{base_id}|"
                             f"{script}")
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


def _item_lines(dirs: list, root: str, ids: dict) -> list:
    """`item id=Plugin|FormID` for every inventory id a script can name,
    resolved through the plugins the game loads.

    The runtime resolves the pair through the running load order, so the
    FormID is kept as its owner wrote it and never re-indexed here.
    """
    wanted = _wanted_ids(dirs, ids, _ITEM_EXPORTS)
    indexes = [(plugin, load_index(folder, _ITEM_TYPES, {own: own}))
               for folder, plugin, own in _loaded_dirs(root, *dirs[0])]
    lines = []
    for edid in wanted.values():
        for plugin, index in indexes:
            formid = index.lookup(edid)
            if formid:
                lines.append(f'{edid}={plugin}|{formid}')
                break
    return lines


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
    """`id=Plugin|FormID` for each TES3 id's BASE record.

    🛑 The base, not a placement: `PlaceAtPC` creates a reference from a
    template that the world may never place, which is the only way the TR_m3
    vermai enters the game.
    See: docs/plans/morrowind_object_scripts.md#placeatpc
    """
    wanted = _wanted_ids(dirs, ids, _SCRIPTED_EXPORTS)
    indexes = [(plugin, load_index(folder, _SCRIPTED_TYPES, {own: own}))
               for folder, plugin, own in _loaded_dirs(root, *dirs[0])]
    lines = []
    for edid in wanted.values():
        for plugin, index in indexes:
            formid = index.lookup(edid)
            if formid:
                lines.append(f'{edid}={plugin}|{formid}')
                break
    return lines


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


def _write_lines(path: str, lines: list) -> int:
    """Write a table; 1 when it has anything in it, else 0 and no file."""
    if not lines:
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
    locals_lines, body_lines, by_formid = _script_tables(dirs)
    return (_write_lines(os.path.join(out_dir, GLOBALS_TABLE),
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
                           _item_lines(dirs, root, ids.get('items', {})))
            + _write_lines(os.path.join(out_dir, REFS_TABLE),
                           _ref_lines(dirs, root, ids.get('objects', {})))
            + _write_lines(os.path.join(out_dir, BASES_TABLE),
                           _base_lines(dirs, root, ids.get('objects', {}))))


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
    print(f'    sidecar: {topics} topics, {infos} responses merged over '
          f'{", ".join(name for name, _path in chain)}')
    staged = len(DIALOGUE_FILES)
    for name, key in ((ACTORS_TABLE, 'actors'), (FACTIONS_TABLE, 'factions'),
                      (GMST_TABLE, 'gmsts'), (SKILLS_TABLE, 'skills')):
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

    Returns the number of files staged; 0 when the plugin has no dialogue,
    which is every non-TES3 source and any TES3 plugin that defines none.
    """
    present = [name for name in _EXPORT_DIALOGUE
               if os.path.isfile(os.path.join(export_dir, name))]
    if not present:
        return 0
    out_dir = sidecar_dir(output_path, plugin_name)
    os.makedirs(out_dir, exist_ok=True)
    chain = plugin_chain(_export_root(export_dir), plugin_name)
    gathered = gather(chain) if chain else {}
    staged = (_stage_dialogue(export_dir, out_dir, present, chain, gathered)
              + write_script_tables(export_dir, out_dir, plugin_name,
                                    gathered)
              + _journal_quests(writer, out_dir, plugin_name))
    index = _actor_index(export_dir)
    if not index:
        return staged
    with open(os.path.join(out_dir, ACTOR_INDEX), 'w',
              encoding='utf-8') as handle:
        handle.write(index)
    return staged + 1

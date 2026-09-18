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

from asset_convert.sources import source_registry
from core.plugin_masters import (get_masters_from_binary,
                                 masters_from_export_header)
from tes4_export.morrowind_ids import load_index

from .morrowind_sidecar_source import (gather, plugin_chain,
                                       write_merged_dialogue)

#: Where the runtime looks, relative to a plugin's output root.
SIDECAR_DIR = os.path.join('SKSE', 'Plugins', 'MorrowindRuntime')

#: Export files the runtime reads; topics first, since a response needs one.
DIALOGUE_FILES = ('MWDI.txt', 'MWIN.txt')

#: The FormID -> TES3 id index the activation hook routes on.
ACTOR_INDEX = 'MWAC.txt'

#: The compiler's tables: `name=type,value`, `script=t:var,...`, `object=script`.
GLOBALS_TABLE = 'MWGL.txt'
SCRIPT_LOCALS_TABLE = 'MWSV.txt'
ACTOR_SCRIPTS_TABLE = 'MWOS.txt'

#: What the FILTER needs about each NPC: `id=race|class|faction|rank|disposition|female|name`.
ACTORS_TABLE = 'MWNP.txt'

#: FACT rank requirements, which the filter's RankRequirement reads.
FACTIONS_TABLE = 'MWFA.txt'

#: What AddItem and its kin need: `item id=Plugin.esm|FormID`.
ITEMS_TABLE = 'MWID.txt'

#: What `id->Command` needs: the PLACED reference, `id=Plugin.esm|FormID`.
REFS_TABLE = 'MWRF.txt'

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
    """`(locals_lines, script_name_by_formid)`, the FormIDs as THIS plugin's
    export spells them: its own verbatim, a master's through `_formid_here`."""
    lines, by_formid, seen = [], {}, set()
    header = masters_from_export_header(dirs[0][0])
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
            declared = declared_locals(rec.get('SCTX', ''))
            if declared:
                lines.append(f"{name}={','.join(declared)}")
    return lines, by_formid


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
    `gathered` is what `gather` read from the TES3 binaries, when any."""
    dirs = table_dirs(export_dir, plugin_name)
    root = _export_root(export_dir)
    ids = gathered or {}
    locals_lines, by_formid = _script_tables(dirs)
    return (_write_lines(os.path.join(out_dir, GLOBALS_TABLE),
                         _global_lines(dirs))
            + _write_lines(os.path.join(out_dir, SCRIPT_LOCALS_TABLE),
                           locals_lines)
            + _write_lines(os.path.join(out_dir, ACTOR_SCRIPTS_TABLE),
                           _object_script_lines(export_dir, by_formid))
            + _write_lines(os.path.join(out_dir, ITEMS_TABLE),
                           _item_lines(dirs, root, ids.get('items', {})))
            + _write_lines(os.path.join(out_dir, REFS_TABLE),
                           _ref_lines(dirs, root, ids.get('objects', {}))))


def _stage_dialogue(export_dir: str, out_dir: str, present: list,
                    chain: list, gathered: dict) -> int:
    """The dialogue and the actor table, MERGED over the plugin's TES3 masters
    when its binary can be found; else this plugin's own export, copied."""
    if not chain:
        for name in present:
            shutil.copyfile(os.path.join(export_dir, name),
                            os.path.join(out_dir, name))
        return len(present)
    topics, infos = write_merged_dialogue(gathered, out_dir)
    print(f'    sidecar: {topics} topics, {infos} responses merged over '
          f'{", ".join(name for name, _path in chain)}')
    staged = _write_lines(os.path.join(out_dir, ACTORS_TABLE),
                          list(gathered['actors'].values()))
    staged += _write_lines(os.path.join(out_dir, FACTIONS_TABLE),
                           list(gathered['factions'].values()))
    return len(DIALOGUE_FILES) + staged


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


def write_morrowind_sidecar(export_dir: str, output_path: str,
                            plugin_name: str, writer=None) -> int:
    """Stage this plugin's dialogue and tables into its SKSE sidecar folder,
    and with a `writer`, add its journal quests to the plugin being written.

    Returns the number of files staged; 0 when the plugin has no dialogue,
    which is every non-TES3 source and any TES3 plugin that defines none.
    """
    present = [name for name in DIALOGUE_FILES
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

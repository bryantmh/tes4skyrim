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

#: What AddItem and its kin need: `item id=Plugin.esm|FormID`.
ITEMS_TABLE = 'MWID.txt'

#: The exports the tables are built from; `_SCRIPTED_EXPORTS` is every TES3 type that can carry a script.
_NPC_EXPORT = 'NPC_.txt'
_SCRIPTED_EXPORTS = ('NPC_.txt', 'CREA.txt', 'ACTI.txt', 'ALCH.txt', 'AMMO.txt',
                     'APPA.txt', 'ARMO.txt', 'BOOK.txt', 'CLOT.txt', 'CONT.txt',
                     'DOOR.txt', 'INGR.txt', 'KEYM.txt', 'LIGH.txt', 'MISC.txt',
                     'WEAP.txt')

#: The exports whose records can sit in an inventory.
_ITEM_EXPORTS = ('ALCH.txt', 'AMMO.txt', 'APPA.txt', 'ARMO.txt', 'BOOK.txt',
                 'CLOT.txt', 'INGR.txt', 'KEYM.txt', 'LIGH.txt', 'MISC.txt',
                 'WEAP.txt')
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


def _item_lines(dirs: list) -> list:
    """`item id=Plugin|FormID` for every inventory record each plugin OWNS.

    The runtime resolves the pair through the running load order, so the
    FormID is kept as its owner wrote it and never re-indexed here.
    """
    seen = {}
    for folder, plugin in dirs:
        own = f'{len(masters_from_export_header(folder)):02X}'
        for name in _ITEM_EXPORTS:
            for rec in export_records(os.path.join(folder, name),
                                      ('FormID', 'EditorID')):
                key = rec.get('EditorID', '').lower()
                formid = rec.get('FormID', '')
                if key and key not in seen and formid[:2].upper() == own:
                    seen[key] = f"{rec['EditorID']}={plugin}|{formid}"
    return list(seen.values())


def _write_lines(path: str, lines: list) -> int:
    """Write a table; 1 when it has anything in it, else 0 and no file."""
    if not lines:
        return 0
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    return 1


def write_script_tables(export_dir: str, out_dir: str,
                        plugin_name: str) -> int:
    """The export-built tables, into `out_dir`. Returns files written."""
    dirs = table_dirs(export_dir, plugin_name)
    locals_lines, by_formid = _script_tables(dirs)
    return (_write_lines(os.path.join(out_dir, GLOBALS_TABLE),
                         _global_lines(dirs))
            + _write_lines(os.path.join(out_dir, SCRIPT_LOCALS_TABLE),
                           locals_lines)
            + _write_lines(os.path.join(out_dir, ACTOR_SCRIPTS_TABLE),
                           _object_script_lines(export_dir, by_formid))
            + _write_lines(os.path.join(out_dir, ITEMS_TABLE),
                           _item_lines(dirs)))


def _stage_dialogue(export_dir: str, out_dir: str, plugin_name: str,
                    present: list) -> int:
    """The dialogue and the actor table, MERGED over the plugin's TES3 masters
    when its binary can be found; else this plugin's own export, copied."""
    chain = plugin_chain(_export_root(export_dir), plugin_name)
    if not chain:
        for name in present:
            shutil.copyfile(os.path.join(export_dir, name),
                            os.path.join(out_dir, name))
        return len(present)
    gathered = gather(chain)
    topics, infos = write_merged_dialogue(gathered, out_dir)
    print(f'    sidecar: {topics} topics, {infos} responses merged over '
          f'{", ".join(name for name, _path in chain)}')
    return len(DIALOGUE_FILES) + _write_lines(
        os.path.join(out_dir, ACTORS_TABLE), list(gathered['actors'].values()))


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
    staged = (_stage_dialogue(export_dir, out_dir, plugin_name, present)
              + write_script_tables(export_dir, out_dir, plugin_name)
              + _journal_quests(writer, out_dir, plugin_name))
    index = _actor_index(export_dir)
    if not index:
        return staged
    with open(os.path.join(out_dir, ACTOR_INDEX), 'w',
              encoding='utf-8') as handle:
        handle.write(index)
    return staged + 1

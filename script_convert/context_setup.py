"""Per-plugin setup for script conversion: output directory, caches and plans.

The phases `pipeline.build_script_context` runs before it hands work to the
converter pool.  Each is a pure function of the export/output directories or
of the parsed record tables, so a subset rebuild and the full stage share them.

See: docs/commentary/script_convert.md#script-output-dir
"""

import os
import re
import shutil

from asset_convert.collision.collision_extract import bounds_cache_is_current
from core.plugin_masters import master_dir, masters_from_export_header
from output_layout import assets_for
from script_convert.cross_ref import CrossRefGraph
from script_convert.message_menus import build_chargen_menus
from script_convert.static_scripts import STATIC_DIR, static_script_files
from script_convert.ownership import prune_removed_owners, read_owned, sibling_owned
from tes5_import.base.mesh_bounds import load_mesh_bounds
from tes5_import.base.text_reader import parse_export_file
from tes5_import.dialogue.converter import service_menu_kind

#: Static Papyrus sources deployed beside the generated scripts of a masterless plugin.


def prepare_output_dir(output_dir: str, owner: str, export_dir: str = None) -> set:
    """Clear `owner`'s previous scripts; returns the names other plugins here own.

    A folder no other plugin shares is wiped whole, .psc tree and sibling .pex.
    In a shared one only the files on `owner`'s own list go.
    See: docs/commentary/script_convert.md#wipe-output-dir
    """
    if export_dir is not None:
        prune_removed_owners(output_dir, export_dir)
    shared = sibling_owned(output_dir, owner)
    pex_dir = os.path.dirname(output_dir)
    if not shared:
        if os.path.isdir(output_dir):
            shutil.rmtree(output_dir, onexc=_keep_held_dir)
        stale = [n[:-4] for n in (os.listdir(pex_dir) if os.path.isdir(pex_dir) else [])
                 if n.lower().endswith('.pex')]
    else:
        stale = read_owned(output_dir, owner) - shared
    for name in stale:
        _remove_quietly(os.path.join(output_dir, name + '.psc'))
        _remove_quietly(os.path.join(pex_dir, name + '.pex'))
    os.makedirs(output_dir, exist_ok=True)
    return shared


def _keep_held_dir(func, path: str, exc: BaseException) -> None:
    """rmtree hook: keep an emptied dir another process holds; else raise.

    See: docs/commentary/script_convert.md#wipe-output-dir
    """
    if func is not os.rmdir or not isinstance(exc, PermissionError):
        raise exc


def _remove_quietly(path: str) -> None:
    """Remove a file, tolerating one that vanished or is locked."""
    try:
        os.remove(path)
    except OSError:
        pass


def load_bounds_cache(export_dir: str) -> str:
    """Load the mesh-bounds cache and return its path, warning when stale.

    See: docs/commentary/script_convert.md#bounds-cache-schema
    """
    cache = str(assets_for(export_dir) / 'mesh_bounds_cache.json')
    if not bounds_cache_is_current(cache):
        print("  WARNING: mesh bounds cache is missing or predates the current "
              "schema.\n"
              "           Breakaway/trap havok releases will NOT be emitted "
              "(planks and traps\n"
              "           will hang instead of falling).  Run the import or "
              "meshes step to\n"
              f"           rebuild it: {cache}")
    load_mesh_bounds(cache, quiet=True)
    return cache


def deploy_static_scripts(export_dir: str, output_dir: str,
                          shared: frozenset = frozenset()) -> list:
    """Copy the static scripts for a masterless plugin; purge them for a dependent.

    Returns the script names copied. A copy another plugin in this folder owns
    (`shared`) is never purged.
    See: docs/commentary/script_convert.md#static-scripts-ownership
    """
    names = static_script_files()
    if not masters_from_export_header(export_dir):
        for name in names:
            shutil.copy2(os.path.join(STATIC_DIR, name),
                         os.path.join(output_dir, name))
        return [name[:-4] for name in names]
    print('  Static scripts: skipped (owned by this plugin\'s master)')
    for name in (n for n in names if n[:-4] not in shared):
        for stale in (os.path.join(output_dir, name),
                      os.path.join(os.path.dirname(output_dir),
                                   name[:-4] + '.pex')):
            if os.path.isfile(stale):
                os.remove(stale)
                print(f'    removed stale master-owned copy: {stale}')
    return []


def build_xref(export_dir: str) -> CrossRefGraph:
    """The cross-reference graph, with the cross-script ref-as-int analysis."""
    print('  Building cross-reference graph...')
    xref = CrossRefGraph()
    xref.load_from_export(export_dir)
    print(f'    {len(xref.formid_to_edid)} FormID->EditorID mappings')
    print(f'    {len(xref.script_formid_to_edid)} scripts, '
          f'{len(xref.quest_edids)} quests')
    scpt_path = os.path.join(export_dir, 'SCPT.txt')
    if os.path.exists(scpt_path):
        xref.build_ref_as_int_map(scpt_path)
        if xref.ref_as_int:
            print(f'    {len(xref.ref_as_int)} ref variables detected as '
                  f'integer-only (cross-script)')
    return xref


def load_records(export_dir: str, sigs: tuple) -> dict:
    """signature -> parsed records; a missing export file is an empty list."""
    out = {}
    for sig in sigs:
        path = os.path.join(export_dir, f'{sig}.txt')
        out[sig] = parse_export_file(path) if os.path.exists(path) else []
    return out


def service_menu_topics(by_type: dict) -> dict:
    """DIAL FormID -> service menu name, for the service-typed menu topics."""
    return {rec.get('FormID', ''): service_menu_kind(rec)
            for rec in by_type.get('DIAL', []) if service_menu_kind(rec)}


def topic_unlock_globals(by_type: dict, unlock_plan: dict) -> dict:
    """DIAL EditorID (lower) -> unlock global, so a script `AddTopic X` opens the same gate."""
    out = {}
    for d in by_type.get('DIAL', []):
        edid = (d.get('EditorID') or '').lower()
        fid = d.get('FormID', '')
        if not edid or not fid:
            continue
        gname = unlock_plan['gated'].get(int(fid, 16) & 0xFFFFFF)
        if gname:
            out[edid] = gname
    return out


def quest_edids_by_fid(by_type: dict) -> dict:
    """QUST low-24 FormID -> EditorID."""
    return {int(r['FormID'], 16) & 0xFFFFFF: (r.get('EditorID') or '')
            for r in by_type.get('QUST', []) if r.get('FormID')}


#: The record types a chargen plan reads.
_CHARGEN_TYPES = ('BSGN', 'CLAS', 'SPEL')


def _record_owner(rec: dict, masters: list, own: str) -> tuple:
    """(owning plugin, local FormID): the index byte names a master or `own`."""
    fid = int(rec.get('FormID') or '0', 16)
    slot = fid >> 24
    return (masters[slot] if slot < len(masters) else own).lower(), fid & 0xFFFFFF


def chargen_records(export_dir: str, plugin: str = '', types: tuple = _CHARGEN_TYPES) -> dict:
    """{type: [records]} for `types` (BSGN, CLAS and SPEL): each direct master's,
    then the plugin's own, one per record (a later copy overrides). The plugin's
    own are also under 'own:<type>'. Each record carries `_plugin`, the file it
    came from (`plugin` for the own ones), and `_masters`, that file's masters.

    See: docs/commentary/morrowind_runtime.md#chargen-menus
    """
    names = masters_from_export_header(export_dir)
    sources = [(name, master_dir(export_dir, name)) for name in names]
    sources = [(name, d) for name, d in sources if os.path.isdir(d)] + [('', export_dir)]
    merged = {sig: {} for sig in types}
    out = {}
    for own, folder in sources:
        masters = masters_from_export_header(folder)
        for sig in types:
            path = os.path.join(folder, f'{sig}.txt')
            records = parse_export_file(path) if os.path.isfile(path) else []
            for rec in records:
                rec.update({'_plugin': own or plugin, '_masters': masters})
                merged[sig][_record_owner(rec, masters, own)] = rec
            if not own:
                out[f'own:{sig}'] = records
    out.update({sig: list(recs.values()) for sig, recs in merged.items()})
    return out


#: The player's NPC_ record and the class it names, in an export's text.
_PLAYER_CLASS = re.compile(r'^FormID=00000007\n(?:(?!---RECORD_END).)*?CNAM\.Class=([0-9A-Fa-f]{8})',
                           re.S | re.M)


def player_start_class(export_dir: str, clas_records: list) -> str:
    """The lowercase EditorID of the class the player record (0x7) starts in, the
    plugin's own copy over its masters'; '' when none names one."""
    folders = [master_dir(export_dir, name) for name in masters_from_export_header(export_dir)]
    fid = 0
    for folder in folders + [export_dir]:
        path = os.path.join(folder, 'NPC_.txt')
        found = None
        if os.path.isfile(path):
            with open(path, encoding='utf-8', errors='replace') as handle:
                found = _PLAYER_CLASS.search(handle.read())
        fid = int(found.group(1), 16) & 0xFFFFFF if found else fid
    edids = {int(r.get('FormID') or '0', 16) & 0xFFFFFF: r.get('EditorID', '') for r in clas_records}
    return edids.get(fid, '').lower() if fid else ''


def chargen_menu_plan(export_dir: str, plugin: str = '') -> dict:
    """The ShowBirthsignMenu / ShowClassMenu plan, shared with the importer and
    the runtime sidecar: the plugin's and its masters' signs and classes. The
    class plan also names the class the player record starts in (`start`)."""
    records = chargen_records(export_dir, plugin)
    if not (records['BSGN'] or records['CLAS']):
        return {}
    menus = build_chargen_menus(records['BSGN'], records['CLAS'], records['SPEL'])
    for key, sig in (('birthsign', 'BSGN'), ('class', 'CLAS')):
        if key in menus:
            menus[key]['own'] = bool(records[f'own:{sig}'])
    if 'class' in menus:
        menus['class']['start'] = player_start_class(export_dir, records['CLAS'])
    if menus:
        print('    Chargen menus: ' + ', '.join(
            f"{k} ({len(v['actions'])} options, {len(v['pages'])} pages)"
            for k, v in sorted(menus.items())))
    return menus

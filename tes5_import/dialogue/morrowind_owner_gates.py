"""The ownership-gate table: each placed reference a TES3 global frees while it is nonzero.

Skyrim's REFR has no such field, so MorrowindRuntime makes the player the
owner while the global is set and restores the authored owner when it clears.
See: docs/commentary/morrowind_runtime.md#ownership-globals
"""

import os

from asset_convert.sources import source_registry
from core.plugin_masters import export_root, masters_from_export_header
from .morrowind_placements import export_records, folder_tables, rebased_formid

#: The sidecar file: `Plugin|ref=global|OwnerPlugin|owner`, one row per gated reference.
OWNER_GATES_TABLE = 'owner_gates.txt'


def _global_names(folder: str, plugin: str, masters: list) -> dict:
    """`{GLOB FormID as this export spells it: EditorID}` over the export and its exported masters."""
    root = export_root(folder)
    dirs = [(folder, plugin)] + [(str(source_registry.record_dir(root, name)), name)
                                 for name in masters]
    names = {}
    for index, (where, owner) in enumerate(dirs):
        for rec in export_records(os.path.join(where, 'GLOB.txt'), ('FormID', 'EditorID')):
            formid = rec.get('FormID', '')
            here = formid if index == 0 else rebased_formid(formid, where, owner, masters)
            if here and rec.get('EditorID'):
                names.setdefault(here.upper(), rec['EditorID'])
    return names


def _owner_of(formid: str, plugin: str, masters: list) -> str:
    """`Plugin|FormID`, the plugin read off `formid`'s index byte."""
    index = int(formid[:2], 16)
    return f'{masters[index] if index < len(masters) else plugin}|{formid}'


def owner_gate_rows(export_dir: str, plugin_name: str) -> list:
    """The table's rows for this plugin's own gated references."""
    gated = folder_tables(export_dir)['gated']
    if not gated:
        return []
    plugin = os.path.basename(plugin_name)
    masters = masters_from_export_header(export_dir)
    names = _global_names(export_dir, plugin, masters)
    rows = []
    for rec in gated:
        name = names.get(rec['XGLB.Global'].upper())
        if name:
            rows.append(f"{plugin}|{rec['FormID']}={name}|"
                        f"{_owner_of(rec['XOWN.Owner'], plugin, masters)}")
    return rows

"""The EditorID and display name a source worldspace carries once CONVERTED.

One table for every stage that names a worldspace: the WRLD writer, its
persistent cell, its Location, the cloud-bank mesh and script property
binding. An import run calls `set_worldspace_plugins` with the plugin and its
masters once, and every later lookup without an explicit table uses that
chain. Anything that crosses into a worker process carries its own table from
`renames_for`.

See: docs/commentary/script_convert.md#worldspace-property-rename
"""

import os

#: Renamed in every plugin: Oblivion's Tamriel would override Skyrim's.
_ALWAYS = {'tamriel': ('TES4Tamriel', None)}

#: Plugin-name prefix -> {source EDID: (converted EDID, display name or None)}.
_BY_PLUGIN = {'arktwend': {'wrldmorrowind': ('WrldArktwend', 'Arktwend')}}

#: Suffix of the persistent cell the TES3 exporter names after its worldspace.
_PERSISTENT = 'Persistent'


def _entries_for(plugins) -> dict:
    """{source EDID, lowercased: (converted EDID, display name)} for a chain."""
    out = dict(_ALWAYS)
    for name in plugins:
        low = os.path.basename(str(name)).lower()
        for prefix, table in _BY_PLUGIN.items():
            if low.startswith(prefix):
                out.update(table)
    return out


def _edids(entries: dict) -> dict:
    """The EDID renames in `entries`, plus each worldspace's persistent cell."""
    out = {}
    for src, (dst, _name) in entries.items():
        out[src] = dst
        out[src + _PERSISTENT.lower()] = dst + _PERSISTENT
    return out


_active_entries = dict(_ALWAYS)
_active = _edids(_active_entries)


def renames_for(plugins=None) -> dict:
    """{source EDID lowercased: converted EDID}; no chain = the active one."""
    if plugins is None:
        return dict(_active)
    return _edids(_entries_for(plugins))


def set_worldspace_plugins(plugins) -> None:
    """Make `plugins` (the converting plugin and its masters) the active chain."""
    global _active, _active_entries
    _active_entries = _entries_for(plugins)
    _active = _edids(_active_entries)


def converted_worldspace_edid(edid: str, renames: dict = None) -> str:
    """`edid` as the converted plugin names it; unchanged when not renamed."""
    table = _active if renames is None else renames
    return table.get(edid.lower(), edid) if edid else edid


def converted_worldspace_name(edid: str, full: str) -> str:
    """The display name for worldspace `edid`: the active chain's, else `full`."""
    entry = _active_entries.get((edid or '').lower())
    return entry[1] if entry and entry[1] else full


def source_worldspace_edid(edid: str, renames: dict = None) -> str:
    """The source EDID a converted name was renamed from, lowercased."""
    low = edid.lower()
    for src, dst in (_active if renames is None else renames).items():
        if dst.lower() == low:
            return src
    return low

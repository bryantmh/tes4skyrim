"""Header flags and GRUP placement of a REFERENCE (REFR/ACHR/ACRE) in a master's cell.

An override keeps the master's converted bytes, but a reference's Persistent,
Initially Disabled and Visible When Distant bits are authored header state, so
the bits the plugin's author changed are taken from the plugin's export.
The Full-LOD rule here is shared with the master world-group builder.

See: docs/commentary/tes5_import_override.md#override-reference-state
See: docs/commentary/tes5_import_override.md#full-lod-refs
"""

import struct

from asset_convert.lod.effect_mesh import is_effect_mesh

from ..base.text_reader import get_int

#: TES4 signatures of placed references.
REF_SIGS = frozenset({'REFR', 'ACHR', 'ACRE'})

PERSISTENT = 0x400
INITIALLY_DISABLED = 0x800
VISIBLE_WHEN_DISTANT = 0x8000
#: LIGH-ref "Casts Shadows"; on an ACHR the same bit is Starts Dead, which the master run owns.
LIGHT_CASTS_SHADOWS = 0x200

#: TES5 REFR "Is Full LOD": drawn at full detail at any distance, never baked into object LOD.
FULL_LOD = 0x10000

#: The player's FormID, index byte included: an enable parent that is always enabled.
PLAYER_REF = 0x00000014

_TAKEN = PERSISTENT | INITIALLY_DISABLED | VISIBLE_WHEN_DISTANT


def take_mask(out_sig: bytes, base_is_light: bool) -> int:
    """Header bits an override of this output reference takes from the plugin."""
    if out_sig == b'REFR' and base_is_light:
        return _TAKEN | LIGHT_CASTS_SHADOWS
    return _TAKEN


def merge_flags(base: int, master_export: int, plugin_export: int,
                take: int, map_marker: bool) -> int:
    """`base` with only the bits the author changed set to the plugin's value.

    A bit the two exports agree on keeps the master RUN's value, so derived
    bits survive (a map marker's forced 0x400, a TES3 source's settle bit). A
    map marker never loses 0x400: the master run forces it on every marker.
    """
    authored = (master_export ^ plugin_export) & take
    if map_marker:
        authored &= ~PERSISTENT
    return (base & ~authored) | (plugin_export & authored)


def record_flags(record: bytes) -> int:
    """The header flags of a packed record."""
    return struct.unpack_from('<I', record, 8)[0]


def set_flags(record: bytes, flags: int) -> bytes:
    """`record` with its header flags replaced."""
    return record[:8] + struct.pack('<I', flags) + record[12:]


def ref_chain(parent_out: int, persistent: bool) -> tuple:
    """The (6, cell), (8|9, cell) groups a reference sits in under its cell."""
    label = struct.pack('<I', parent_out)
    return ((6, label), (8 if persistent else 9, label))


def ref_path(master_index, parent_out: int, persistent: bool) -> tuple:
    """A reference's full GRUP path under a master's cell; () if it has none."""
    parent = master_index.group_path(parent_out) if parent_out else ()
    return parent + ref_chain(parent_out, persistent) if parent else ()


def placement_fault(path: tuple) -> str:
    """The counter a reference path breaks, or ''.

    A persistent reference belongs in its world's persistent cell, never in an
    exterior block's cell; a temporary one never sits in the persistent cell.
    """
    if len(path) < 3:
        return ''
    parent, gtype = path[:-2], path[-1][0]
    if gtype == 8 and any(step[0] == 4 for step in parent):
        return 'renest-pers-in-block'
    if gtype == 9 and parent[-1][0] == 1:
        return 'renest-temp-in-persistent-cell'
    return ''


def full_lod_bases(stats, master_export=None) -> dict:
    """{STAT FormID (TES4 export hex, this plugin's space): model} for the Full-LOD rule.

    `stats` are this plugin's own STAT export records; `master_export` is the
    masters' export keyed in this plugin's space. The plugin's own copy wins.
    """
    out = {key.upper(): rec.get('Model.MODL') or ''
           for key, rec in (master_export or {}).items()
           if rec.get('Signature') == 'STAT'}
    for rec in stats or ():
        out[(rec.get('FormID') or '').upper()] = rec.get('Model.MODL') or ''
    return out


def _formid(value) -> int:
    """A TES4 export hex FormID as an int; 0 when absent or malformed."""
    try:
        return int((value or '').strip(), 16)
    except ValueError:
        return 0


def is_full_lod_ref(sig: str, rec: dict, bases: dict) -> bool:
    """True when a TES4 reference qualifies to ship as Full LOD.

    An exterior REFR of a STAT, Visible When Distant in its source, enabled by
    a parent that is not the player (the FULL id is compared, so a plugin's
    own 0x14 is not the player), with a model outside the effect folders.
    `bases` is `full_lod_bases`.
    """
    if sig != 'REFR' or not _formid(rec.get('ParentWRLD')):
        return False
    if not get_int(rec, 'RecordFlags') & VISIBLE_WHEN_DISTANT:
        return False
    if _formid(rec.get('XESP.Reference')) in (0, PLAYER_REF):
        return False
    model = (bases or {}).get((rec.get('NAME') or '').upper())
    return model is not None and not is_effect_mesh(model)


def full_lod_flags(flags: int) -> int:
    """Header flags of a Full-LOD reference: persistent, Is Full LOD, no VWD."""
    return (flags | FULL_LOD | PERSISTENT) & ~VISIBLE_WHEN_DISTANT


def report_full_lod(where: str, fids, unresolved=()) -> None:
    """Print the Full-LOD count and every ref, and WARN for refs left unplaced."""
    listed = ', '.join(sorted(fids))
    print(f"  Full LOD ({where}): {len(fids)} ref(s) persistent + Is Full LOD, "
          f"VWD cleared, in the world's persistent cell"
          + (f": {listed}" if listed else ''))
    if unresolved:
        print(f"  WARNING: Full LOD ({where}): {len(unresolved)} qualifying "
              f"ref(s) have no persistent cell and were left unchanged: "
              + ', '.join(sorted(unresolved)))

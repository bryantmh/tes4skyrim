"""Travel destinations as persistent markers.

A TES3 NPC_ lists where its travel service goes as DODT positions, each
optionally followed by a DNAM naming an interior. Skyrim moves a reference TO
another reference, and only a PERSISTENT one exists while its cell is
unloaded, so every destination becomes an XMarker standing on the authored
spot -- which is what a CK-made teleport target is.

See: docs/commentary/morrowind_runtime.md#travel-markers
"""

import struct
from types import SimpleNamespace

#: XMarker, 0x3B in Oblivion.esm and in Skyrim.esm alike.
_XMARKER = '0000003B'

#: The persistent record flag.
_PERSISTENT = 1024

#: DODT: position x, y, z then rotation x, y, z in radians.
_DODT = '<6f'


def destinations(rec) -> list:
    """`[(x, y, z, rx, ry, rz), interior cell name or '']` per DODT, each
    paired with the DNAM that directly follows it."""
    found = []
    for sub in rec.subrecords:
        if sub.type == 'DODT' and len(sub.data) >= struct.calcsize(_DODT):
            found.append([struct.unpack_from(_DODT, sub.data), ''])
        elif sub.type == 'DNAM' and found and not found[-1][1]:
            found[-1][1] = sub.data.split(b'\x00', 1)[0].decode('cp1252',
                                                                'replace')
    return found


def travel_editor_id(npc_id: str, index: int) -> str:
    """The marker's EditorID, which is how the sidecar finds its FormID."""
    stem = ''.join(c for c in npc_id if c.isalnum())
    return f'TES3Travel{stem}{index}'


def _marker_lines(npc_id: str, index: int, spot: tuple, parent: str) -> list:
    """The export text for one destination's XMarker REFR."""
    x, y, z, rot_x, rot_y, rot_z = spot
    return [f'EditorID={travel_editor_id(npc_id, index)}',
            f'RecordFlags={_PERSISTENT}',
            f'ParentCELL={parent}',
            f'NAME={_XMARKER}',
            f'PosX={x}', f'PosY={y}', f'PosZ={z}',
            f'RotX={rot_x}', f'RotY={rot_y}', f'RotZ={rot_z}']


def travel_marker_records(records, ctx, is_own) -> list:
    """`(FormID, lines)` per destination of every NPC_ `is_own` accepts.

    A destination in a cell no plugin of the load order defines is skipped,
    as a teleport door into one is. An exterior marker lives in the
    worldspace's persistent cell, as every persistent exterior reference does.
    """
    out = []
    for rec in records:
        if rec.type != 'NPC_' or rec.deleted or not is_own(rec, ctx):
            continue
        for index, (spot, cell) in enumerate(destinations(rec)):
            target = SimpleNamespace(dest_cell=cell, dest_pos=spot[:3])
            found = ctx.destination_cell(target)
            if not found:
                continue
            parent = found if cell else ctx.persistent_cell_id()
            ctx.rehomed_persistent += 0 if cell else 1
            form_id = ctx.derive(f'travelmarker:{rec.record_id.lower()}:{index}')
            out.append((form_id,
                        _marker_lines(rec.record_id, index, spot, parent)))
    if out:
        print(f'  Synthesized {len(out)} travel markers')
    return out

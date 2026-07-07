"""Build the top-level NAVI (Navmesh Info Map) record for converted navmeshes.

Skyrim only uses a NAVM for pathfinding when it is *also* registered in a
top-level NAVI record.  NAVI holds one NVMI (Navmesh Info) entry per navmesh
that records its FormID, category, centroid, and pathing-cell parent, plus the
edge/door links between navmeshes.  A plugin that adds navmeshes but no NAVI
gets navmeshes the CK/engine ignores.

Source: external/xEdit/Core/wbDefinitionsTES5.pas (wbRecord(NAVI...), ~8181).

NAVI record layout:
  EDID  (optional)
  NVER  U32 version (= 12)
  NVMI  repeated subrecord — one per navmesh (see _pack_nvmi)
  NVPP  Precomputed Pathing struct (we emit an empty one: two 0 counts)
  NVSI  Deleted Navmeshes array (omitted)

NVMI subrecord (in order):
  FormID  Navmesh
  U32     Category (0 = Is Edited)
  float   X, Y, Z              (navmesh centroid)
  4 bytes Preferred Merges Flag (0)
  U32     Edge Link count           + count × FormID   (we emit 0)
  U32     Preferred Edge Link count + count × FormID   (0)
  U32     Door Link count           + count × (U32 CRC, FormID)  (0)
  U8      Is Island (0 = False)
  <Island union: empty when Is Island == 0>
  PathingCell:
    U32   CRC Hash "PathingCell" = 0xA5E9A03C
    FormID Parent Worldspace (0 for interior)
    Parent union — decided by (Parent Worldspace == 0):
       exterior: S16 Grid Y, S16 Grid X
       interior: FormID Parent Cell
"""

import struct

from .writer import pack_subrecord

_PATHING_CELL_CRC = 0xA5E9A03C
_NAVI_VERSION = 12
_NVMI_CATEGORY_EDITED = 0


def _pack_nvmi(meta: dict) -> bytes:
    """Serialise one NVMI subrecord body from a convert_PGRD meta dict."""
    cx, cy, cz = meta['center']
    body = bytearray()
    body += struct.pack('<I', meta['fid'])                 # Navmesh FormID
    body += struct.pack('<I', _NVMI_CATEGORY_EDITED)       # Category
    body += struct.pack('<fff', cx, cy, cz)                # Centroid
    body += struct.pack('<I', 0)                           # Preferred Merges Flag (4 bytes)
    body += struct.pack('<I', 0)                           # Edge Links: count 0
    body += struct.pack('<I', 0)                           # Preferred Edge Links: count 0
    body += struct.pack('<I', 0)                           # Door Links: count 0
    body += struct.pack('<B', 0)                           # Is Island = False
    # Island union omitted (empty when not an island).
    # PathingCell:
    body += struct.pack('<I', _PATHING_CELL_CRC)
    body += struct.pack('<I', meta['wrld_fid'])            # Parent Worldspace
    if meta['is_exterior']:
        body += struct.pack('<hh', meta['grid_y'], meta['grid_x'])
    else:
        body += struct.pack('<I', meta['cell_fid'])
    return pack_subrecord('NVMI', bytes(body))


def build_navi_record(form_id: int, navm_metas: list) -> bytes:
    """Build the whole NAVI top-level record from a list of navmesh metas.

    Returns the packed NAVM-info record bytes (header + subrecords), or b''
    when there are no navmeshes to register.
    """
    if not navm_metas:
        return b''

    # Verified against Skyrim.esm NAVI 0x00012FB4: no EDID, NVER first, then one
    # NVMI per navmesh, then NVPP. (dumped via tools/navmesh_dump.py)
    subs = b''
    subs += pack_subrecord('NVER', struct.pack('<I', _NAVI_VERSION))
    for meta in navm_metas:
        subs += _pack_nvmi(meta)

    # NVPP Precomputed Pathing: empty — two zero counts (Paths, Road Markers).
    subs += pack_subrecord('NVPP', struct.pack('<II', 0, 0))

    # Record header: TES5 NAVI, no special flags.
    header = struct.pack('<4sIIIIHH',
                         b'NAVI', len(subs), 0, form_id,
                         0,   # vcs1
                         44,  # FORM_VERSION_SSE
                         0)   # vcs2
    return header + subs

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
  U32     Flags (0 = edited; 0x20 = Is Island; 0x40 = Not Edited)
  float   X, Y, Z              (navmesh centroid)
  float   Preferred %
  U32     Edge Link count           + count × FormID
  U32     Preferred Edge Link count + count × FormID   (0)
  U32     Door Link count           + count × (U32 CRC "PathingDoor", FormID)
  U8      Has Island Data (0 = False)
  <Island union: empty when Has Island Data == 0>
  PathingCell:
    U32   CRC Hash "PathingCell" = 0xA5E9A03C
    FormID Parent Worldspace (0 for interior)
    Parent union — decided by (Parent Worldspace == 0):
       exterior: S16 Grid Y, S16 Grid X
       interior: FormID Parent Cell

Connectivity contract (verified against ALL 15,462 Skyrim.esm NVMI entries):
  * Edge Links ∪ Preferred Edge Links == the distinct neighbour navmeshes named
    by the mesh's own NVNM Edge Link array, self-links excluded (the 347
    entries that differ do so only by a self-link).  We emit everything as
    plain Edge Links (Preferred is a road-optimisation split we don't have).
  * Door Links == the door REFRs named by the mesh's own NVNM Door Triangles
    (15,462/15,462 exact) with CRC "PathingDoor" 0xE48B73F3.  Each side of a
    load door lists only ITS OWN door ref; the engine joins the two meshes
    through the doors' XTEL pairing.
  Without these arrays the info map declares every navmesh an unreachable
  island: an actor paths inside its current mesh and can never cross a cell
  seam or a load door, so every travel/escort package stalls the moment its
  destination leaves the cell.
"""

import struct

from .writer import pack_subrecord

_PATHING_CELL_CRC = 0xA5E9A03C
_PATHING_DOOR_CRC = 0xE48B73F3
_NAVI_VERSION = 12
_NVMI_FLAGS_EDITED = 0

# The Navmesh Info Map is a SINGLETON living in Skyrim.esm as 0x00012FB4.
# Every vanilla DLC (Update 251, Dawnguard 1873, HearthFires 132, Dragonborn
# 1732 NVMI entries) and every CK-authored plugin registers its navmeshes by
# OVERRIDING that record with its own entries — the engine merges the per-file
# overrides at load.  A NAVI under a fresh FormID is a record the engine never
# consults: none of the plugin's navmeshes get registered, and pathfinding is
# dead everywhere in the plugin's content.
NAVI_SINGLETON_FID = 0x00012FB4


def _pack_nvmi(meta: dict) -> bytes:
    """Serialise one NVMI subrecord body from a convert_PGRD meta dict."""
    cx, cy, cz = meta['center']
    edge_links = meta.get('edge_link_fids') or []
    door_links = meta.get('door_refs') or []
    body = bytearray()
    body += struct.pack('<I', meta['fid'])                 # Navmesh FormID
    body += struct.pack('<I', _NVMI_FLAGS_EDITED)          # Flags
    body += struct.pack('<fff', cx, cy, cz)                # Centroid
    body += struct.pack('<f', 0.0)                         # Preferred %
    body += struct.pack('<I', len(edge_links))             # Edge Links
    for fid in edge_links:                                 # (pre-sorted)
        body += struct.pack('<I', fid)
    body += struct.pack('<I', 0)                           # Preferred Edge Links
    body += struct.pack('<I', len(door_links))             # Door Links
    for fid in door_links:                                 # (pre-sorted by ref)
        body += struct.pack('<II', _PATHING_DOOR_CRC, fid)
    body += struct.pack('<B', 0)                           # Has Island Data = False
    # Island union omitted (empty when not an island).
    # PathingCell:
    body += struct.pack('<I', _PATHING_CELL_CRC)
    body += struct.pack('<I', meta['wrld_fid'])            # Parent Worldspace
    if meta['is_exterior']:
        body += struct.pack('<hh', meta['grid_y'], meta['grid_x'])
    else:
        body += struct.pack('<I', meta['cell_fid'])
    return pack_subrecord('NVMI', bytes(body))


def read_master_nvpp(data_dir: str) -> bytes:
    """The NVPP (Precomputed Pathing) blob our NAVI override must carry.

    Every vanilla master ships a FULL 25,696-byte NVPP in its NAVI override
    (Skyrim/Update/Dawnguard/HearthFires/Dragonborn each carry their own
    edited copy of the same 100-path table).  Since our record is the winning
    override of the singleton, an empty NVPP would replace the vanilla road
    network wholesale; re-shipping the newest vanilla copy keeps the merged
    state vanilla no matter which override the engine honours.  Returns b''
    when no master can be read (caller falls back to an empty struct).
    """
    import os
    import zlib
    best = b''
    for esm in ('Skyrim.esm', 'Update.esm', 'Dawnguard.esm',
                'HearthFires.esm', 'Dragonborn.esm'):
        path = os.path.join(data_dir or '', esm)
        if not os.path.isfile(path):
            continue
        try:
            blob = _extract_nvpp(path)
        except (OSError, struct.error, zlib.error):
            continue
        if blob:
            best = blob            # later masters win, mirroring load order
    return best


def _extract_nvpp(path: str) -> bytes:
    import zlib
    with open(path, 'rb') as f:
        data = f.read()
    hdr = struct.unpack_from('<I', data, 4)[0]
    off = 24 + hdr
    while off + 24 <= len(data):
        sig = data[off:off + 4]
        size = struct.unpack_from('<I', data, off + 4)[0]
        if sig == b'GRUP':
            if data[off + 8:off + 12] == b'NAVI':
                o2 = off + 24
                while o2 + 24 <= off + size:
                    s2 = data[o2:o2 + 4]
                    sz2 = struct.unpack_from('<I', data, o2 + 4)[0]
                    if s2 == b'NAVI':
                        fl = struct.unpack_from('<I', data, o2 + 8)[0]
                        body = data[o2 + 24:o2 + 24 + sz2]
                        if fl & 0x00040000:
                            body = zlib.decompress(body[4:])
                        p = 0
                        override = None
                        while p + 6 <= len(body):
                            ss = body[p:p + 4]
                            s = struct.unpack_from('<H', body, p + 4)[0]
                            p += 6
                            if ss == b'XXXX':
                                override = struct.unpack_from('<I', body, p)[0]
                                p += s
                                continue
                            real = override if override is not None else s
                            override = None
                            if ss == b'NVPP':
                                return body[p:p + real]
                            p += real
                    o2 += 24 + sz2
            off += size
            continue
        off += 24 + size
    return b''


def build_navi_record(form_id: int, navm_metas: list,
                      master_nvpp: bytes = b'') -> bytes:
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

    # NVPP Precomputed Pathing: the vanilla blob when available (see
    # read_master_nvpp), else empty — two zero counts (Paths, Road Markers).
    subs += pack_subrecord('NVPP',
                           master_nvpp or struct.pack('<II', 0, 0))

    # Record header: TES5 NAVI, no special flags.
    header = struct.pack('<4sIIIIHH',
                         b'NAVI', len(subs), 0, form_id,
                         0,   # vcs1
                         44,  # FORM_VERSION_SSE
                         0)   # vcs2
    return header + subs

"""World/cell converters: LTEX, CELL, WRLD, REFR, ACHR, ACRE, LAND, REGN, LSCR, EFSH."""

import math
import struct

from ..constants import (
    MAP_MARKER_TYPE_MAP,
    MATT_MAP,
    SKYRIM_MAP_MARKER_LCRT,
    map_lock_level,
)
from ..locations import WORLD_NAMES
from ..skyrim_overrides import TES4_MARKER_FORMID_TO_SKYRIM
from .items import get_base_origin_shift
from ..text_reader import get_hex_bytes, remap_formid
from .common import (
    _prefix_path,
    get_float,
    get_formid,
    get_int,
    get_str,
    pack_float_subrecord,
    pack_formid_subrecord,
    pack_obnd,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint8_subrecord,
)


# TES4 'DefaultClimate' (Oblivion.esm 0x0000015F).  The engine hardcodes this
# form as the climate for any worldspace with no CNAM — see convert_WRLD.
# Raw TES4 id: it MUST go through remap_formid before being written.
_TES4_DEFAULT_CLIMATE = 0x0000015F


# Interior CELL FormID -> LCTN FormID of the map-marker location it belongs to.
# Populated by tes5_import.locations before the cell groups are built; the CELL
# converter reads it to emit XLCN, which is what lets entering a dungeon
# discover its location (and so reveal its map marker).
_CELL_LOCATION: dict = {}

# (WRLD FormID, grid X, grid Y) -> LCTN FormID naming that exterior cell square.
# Skyrim reads an exterior cell's *name* off its XLCN — no vanilla exterior cell
# has a FULL — so a cell missing from this map shows up as "Wilderness".
_GRID_LOCATION: dict = {}

# WRLD FormID -> LCTN FormID, the catch-all location for that worldspace.
_WORLD_LOCATION: dict = {}

# Door REFR FormID -> (NAVM FormID, triangle index), the reference side of a
# navmesh door link.  Populated from the navmesh metas once every mesh exists
# (Phase 4a) and read by convert_REFR to emit XNDP.  See set_door_navmesh_links.
_DOOR_NAVMESH_LINK: dict = {}

# Output folder (the one holding `meshes\`) for generated world-map cloud
# banks, or None to skip generating them.  See set_cloud_bank_output.
_CLOUD_BANK_ROOT = None

# WRLD FormID -> (min_x, min_y, max_x, max_y) world units of the worldspace's
# real terrain, measured from its exterior cell grid.  See
# set_world_land_extents.
_WORLD_LAND_EXTENT: dict = {}

# Above this magnitude, float32 `a -= 2*pi` no longer changes `a`, because
# 2*pi has fallen below the ULP.  2**24 * 2*pi is already ~1e8 iterations.
_ANGLE_STALL = (2 ** 24) * 2.0 * math.pi


def _safe_angle(a: float) -> float:
    r"""A rotation the engine's normalizer can actually terminate on.

    Skyrim normalizes a placed reference's angles into [0, 2*pi) with an
    uncapped float32 loop (SkyrimSE 1.6.1170 +0x2d8e43..+0x2d8e6f, recovered by
    attaching to a live frozen process):

        while (a <  0   ) a += 2*pi;    # +0x2d8e50
        while (a >  2*pi) a -= 2*pi;    # +0x2d8e66

    There is no iteration cap. Once |a| is large enough that `a -= 2*pi` is a
    no-op under float32 rounding, the loop CANNOT EXIT: one core spins at 100%
    forever, memory stays flat, nothing faults, so there is no CTD and no crash
    log. That is a hard hang the moment the cell attaches.

    TWMP_Valenwood_Elsweyr ships 2,610 such references (RotZ values like
    7.0958e+28, 8.06e+34), all in south-west Valenwood -- the exact region that
    freezes on approach. The values are in the ORIGINAL mod: `tes4_export` dumps
    RotZ verbatim (record_types/world.py:128) and the export text carries
    `RotZ=7.095834960709653e+28`. Oblivion's own normalizer tolerated them;
    Skyrim's does not, so the conversion has to sanitize.

    NaN/Inf are equally fatal: every comparison against NaN is false, so both
    loops fall through and the unnormalized value is stored and propagated.

    Reduced by fmod (exact, and independent of magnitude) rather than clamped,
    so a merely-large-but-meaningful angle keeps its true orientation.
    """
    if a != a or a in (float('inf'), float('-inf')):
        return 0.0
    if abs(a) > _ANGLE_STALL:
        a = math.fmod(a, 2.0 * math.pi)
        # fmod of a huge float carries no real orientation information, and an
        # f32 round-trip of the result can still land outside the range, so
        # anything that survives as non-finite or still-large becomes 0.
        if a != a or abs(a) > _ANGLE_STALL:
            return 0.0
    return a


def set_cloud_bank_output(out_root):
    """Enable per-worldspace world-map cloud banks, written under `out_root`.

    Skyrim's map draws a cloud bank over the terrain.  With no WRLD MODL the
    engine falls back to a HARDCODED mesh sized for Skyrim's Tamriel (verified
    in SkyrimSE.exe at RVA 0x2c7e00 — see asset_convert/worldmap_clouds.py),
    which on a small converted worldspace covers many times its landmass.
    When this is set, convert_WRLD emits a bank scaled to each worldspace's own
    NAM0/NAM9 rectangle and points MODL at it.  Left None (the default, e.g.
    for unit tests) the record is written exactly as before.
    """
    global _CLOUD_BANK_ROOT
    _CLOUD_BANK_ROOT = out_root


def set_world_land_extents(extents: dict):
    """Register WRLD FormID -> real land rectangle, for the cloud banks.

    The bank must be sized and centred on the terrain the player actually
    sees.  MNAM is the map-camera framing an author typed in, and on a
    converted plugin it can simply be wrong: NehrimWorldspace's MNAM sits
    26,624 units south of its land's centre and clips 16,384 units of real
    land off the north edge, which makes a deck built from it both offset and
    undersized.  The exterior cell grid is the authored ground truth.
    """
    _WORLD_LAND_EXTENT.clear()
    _WORLD_LAND_EXTENT.update(extents or {})


def set_door_navmesh_links(door_links: dict):
    """Register door REFR -> (NAVM, triangle) for XNDP emission.

    NVNM's own Door Triangles and NAVI's NVMI Door Links both say "this
    navmesh triangle is a doorway", but neither lets the engine go the other
    way — from the DOOR REFERENCE an actor is pathing towards, to the navmesh
    triangle it must stand on.  That direction is XNDP on the REFR, and it is
    what BSPathingDoor is built from.  Without it a teleport door is not a
    pathing node: the actor has no route through it and simply never leaves the
    room, even though every package, alias and condition is correct.

    Vanilla invariant: 1,705 of 1,722 Skyrim.esm teleport-door REFRs (99.0%)
    carry XNDP, and 1,705 of the 1,706 XNDP records in the file are teleport
    doors — the subrecord is essentially the teleport-door navmesh binding.
    """
    _DOOR_NAVMESH_LINK.clear()
    _DOOR_NAVMESH_LINK.update(door_links or {})


def set_cell_locations(cell_to_location: dict,
                       grid_to_location: dict = None,
                       world_to_location: dict = None):
    """Register the cell → Location maps used to emit CELL XLCN."""
    _CELL_LOCATION.clear()
    _CELL_LOCATION.update(cell_to_location)
    _GRID_LOCATION.clear()
    _GRID_LOCATION.update(grid_to_location or {})
    _WORLD_LOCATION.clear()
    _WORLD_LOCATION.update(world_to_location or {})


def convert_LTEX(rec: dict, writer=None) -> tuple:
    """LTEX — needs companion TXST record in TES5.
    Returns (ltex_bytes, txst_bytes_or_None, txst_formid)."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    icon_path = get_str(rec, 'ICON')
    material = get_int(rec, 'HNAM.Material')
    matt_fid = MATT_MAP.get(material, 0x00012F34)

    # Create TXST record
    txst_fid = 0
    txst_bytes = None
    if icon_path and writer:
        txst_fid = writer.derive_formid('LTEX_TXST', get_formid(rec, 'FormID'))
        txst_subs = b''
        txst_edid = f"TES4_{edid}_TXST" if edid else f"TES4_LTEX_{get_formid(rec, 'FormID'):08X}_TXST"
        txst_subs += pack_string_subrecord('EDID', txst_edid)
        txst_subs += pack_obnd()
        # Oblivion LTEX ICON is relative to Textures\Landscape\ — prepend landscape\
        full_icon = 'landscape\\' + icon_path
        diffuse = _prefix_path(full_icon)
        base_no_ext = diffuse.rsplit('.', 1)[0] if '.' in diffuse else diffuse
        txst_subs += pack_string_subrecord('TX00', diffuse)
        # Normal map (TX01): derive from diffuse with _n suffix
        txst_subs += pack_string_subrecord('TX01', base_no_ext + '_n.dds')
        # No DNAM: landscape TXST records in vanilla Skyrim omit DNAM. The
        # 'No Specular Map' flag (0x0001) only applies to the object shader, not
        # the landscape shader. Writing it causes undefined landscape rendering.
        txst_bytes = pack_record('TXST', txst_fid, 0, txst_subs)

    # TNAM — Texture Set FormID
    if txst_fid:
        subs += pack_formid_subrecord('TNAM', txst_fid)

    # MNAM — Material Type FormID (TES5 uses MNAM, not HNAM, for the MATT reference)
    if matt_fid:
        subs += pack_formid_subrecord('MNAM', matt_fid)

    # HNAM — Havok Data: Friction (U8) + Restitution (U8) = 2 bytes.
    # TES4 LTEX.HNAM has Material(U8)+Friction(U8)+Restitution(U8). In TES5 the
    # material moved to MNAM, so HNAM only carries friction and restitution.
    friction = get_int(rec, 'HNAM.Friction', 30)
    restitution = get_int(rec, 'HNAM.Restitution', 30)
    subs += pack_subrecord('HNAM', struct.pack('<BB', friction, restitution))

    # SNAM — Specular exponent. Passed through from TES4 when present.
    # WARNING: SNAM is a Phong exponent. Setting it to 0 gives pow(NdotH, 0) = 1.0
    # everywhere → the entire landscape becomes blindingly bright white.
    # TES4 landscapes typically use ~30. Leave absent when not in source data.
    spec = get_int(rec, 'SNAM.Specular', -1)
    if spec >= 0:
        subs += pack_uint8_subrecord('SNAM', spec)

    # GNAM — Grass references (one subrecord per GRAS FormID)
    gc = get_int(rec, 'GrassCount')
    for i in range(gc):
        gfid = get_formid(rec, f'Grass[{i}]')
        if gfid:
            subs += pack_formid_subrecord('GNAM', gfid)

    ltex_bytes = pack_record('LTEX', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)
    return ltex_bytes, txst_bytes, txst_fid


def build_cell_xcll(rec: dict):
    """TES5 XCLL payload (92 bytes) from a TES4 CELL record, or None.

    Shared by convert_CELL and the override path (override_builder), so an
    authored lighting change patches the exact bytes conversion writes.

    TES5 XCLL layout (per xEdit wbDefinitionsTES5):
     0 ambient, 4 directional, 8 fog near color, 12 fog near, 16 fog far,
     20 dir rot XY, 24 dir rot Z, 28 dir fade, 32 fog clip, 36 fog power,
     40 directional ambient X+/X-/Y+/Y-/Z+/Z- (6 colors), 64 specular,
     68 scale, 72 fog far color, 76 fog max, 80/84 light fade begin/end,
     88 inherit flags.
    """
    if not get_str(rec, 'XCLL.AmbientR'):
        return None
    ar = get_int(rec, 'XCLL.AmbientR')
    ag = get_int(rec, 'XCLL.AmbientG')
    ab = get_int(rec, 'XCLL.AmbientB')
    dr = get_int(rec, 'XCLL.DirectionalR')
    dg = get_int(rec, 'XCLL.DirectionalG')
    db = get_int(rec, 'XCLL.DirectionalB')
    fr = get_int(rec, 'XCLL.FogR')
    fg = get_int(rec, 'XCLL.FogG')
    fb = get_int(rec, 'XCLL.FogB')
    fog_near = get_float(rec, 'XCLL.FogNear')
    fog_far = get_float(rec, 'XCLL.FogFar')
    rot_xy = get_int(rec, 'XCLL.DirectionalRotXY')
    rot_z = get_int(rec, 'XCLL.DirectionalRotZ')
    dir_fade = get_float(rec, 'XCLL.DirectionalFade', 1.0)
    clip_dist = get_float(rec, 'XCLL.FogClipDist')

    xcll = bytearray(92)
    xcll[0] = ar; xcll[1] = ag; xcll[2] = ab; xcll[3] = 0
    xcll[4] = dr; xcll[5] = dg; xcll[6] = db; xcll[7] = 0
    # Fog near color = same as fog
    xcll[8] = fr; xcll[9] = fg; xcll[10] = fb; xcll[11] = 0
    struct.pack_into('<f', xcll, 12, fog_near)
    struct.pack_into('<f', xcll, 16, fog_far)
    struct.pack_into('<i', xcll, 20, rot_xy)
    struct.pack_into('<i', xcll, 24, rot_z)
    struct.pack_into('<f', xcll, 28, dir_fade)
    struct.pack_into('<f', xcll, 32, clip_dist)
    struct.pack_into('<f', xcll, 36, 1.0)  # Fog power
    # Directional ambient: Skyrim's engine lights interiors from these six
    # colors, not the legacy ambient at offset 0.  TES4 has a single flat
    # ambient, so replicate it into all six directions (vanilla cells set
    # both the legacy ambient and this block).
    for off in range(40, 64, 4):
        xcll[off] = ar; xcll[off + 1] = ag; xcll[off + 2] = ab; xcll[off + 3] = 0
    # Specular color stays black; scale 1.0
    struct.pack_into('<f', xcll, 68, 1.0)
    # Fog far color = same as fog
    xcll[72] = fr; xcll[73] = fg; xcll[74] = fb; xcll[75] = 0
    struct.pack_into('<f', xcll, 76, 1.0)  # Fog max
    # Light fade begin/end 0 = engine defaults (vanilla does the same).
    # Inherit flags 0: nothing comes from the (null) lighting template.
    return bytes(xcll)


def build_cell_xclw(rec: dict):
    """TES5 XCLW payload from a TES4 CELL record, or None to omit.

    TES4 stores -2147483648.0 as "use worldspace default"; writing that
    through as a literal TES5 height puts the cell's water at -2e9 (i.e.
    nowhere).  Omit it so the engine falls back to the worldspace default
    water height (WRLD DNAM).  Shared with the override path.
    """
    wh = get_str(rec, 'XCLW.WaterHeight')
    if not wh:
        return None
    whf = get_float(rec, 'XCLW.WaterHeight')
    if not (-1e9 < whf < 1e9):
        return None
    return struct.pack('<f', whf)


def build_wrld_mnam(rec: dict):
    """TES5 WRLD MNAM payload (28 bytes) from a TES4 WRLD record, or None.

    UsableDimX(i) + UsableDimY(i) + NWCellX(h) + NWCellY(h) + SECellX(h) +
    SECellY(h) + CameraMinHeight(f) + CameraMaxHeight(f) + InitialPitch(f).
    Shared by convert_WRLD and the override path.
    """
    if not get_str(rec, 'MNAM.UsableDimX'):
        return None
    dx = get_int(rec, 'MNAM.UsableDimX')
    dy = get_int(rec, 'MNAM.UsableDimY')
    nwx = get_int(rec, 'MNAM.NWCellX')
    nwy = get_int(rec, 'MNAM.NWCellY')
    sex = get_int(rec, 'MNAM.SECellX')
    sey = get_int(rec, 'MNAM.SECellY')
    # Camera defaults from Skyrim's Tamriel worldspace
    return struct.pack('<iihhhhfff', dx, dy, nwx, nwy, sex, sey,
                       50000.0, 80000.0, 50.0)


def build_wrld_cloud_modl(rec: dict, edid: str = None):
    """Generate this worldspace's world-map cloud bank; return its MODL value.

    Returns None when banks are disabled (set_cloud_bank_output never called),
    when the record has no usable bounds, or when the vanilla source mesh is
    unavailable -- in which case no MODL is written and the engine falls back
    to its own hardcoded bank, i.e. exactly the pre-existing behaviour.

    Shared by convert_WRLD and the override path so both emit the same mesh.
    """
    if not _CLOUD_BANK_ROOT:
        return None
    if edid is None:
        edid = get_str(rec, 'EditorID')
        if edid == 'Tamriel':
            edid = 'TES4Tamriel'      # matches convert_WRLD's rename
    if not edid or not get_str(rec, 'NAM0.MinX'):
        return None
    from asset_convert.worldmap_clouds import (generate_cloud_bank,
                                               compute_center, framed_rect)

    # Size and place against the worldspace's REAL LAND -- the exterior cell
    # grid, registered by set_world_land_extents.  That is the terrain the map
    # draws and the only rectangle that cannot disagree with itself.
    #
    # MNAM (the authored map-camera framing) is the fallback, not the primary,
    # because a converted plugin's MNAM can be plain wrong about its own
    # terrain: NehrimWorldspace's MNAM rectangle is centred 26,624 units south
    # of its land and its north edge cuts 16,384 units of real land off.  Both
    # of those show up in game exactly as reported -- a deck offset away from
    # the landmass, and too small for it.  NAM0/NAM9 is the last resort.
    rect = _WORLD_LAND_EXTENT.get(get_formid(rec, 'FormID'))
    if rect is None and get_str(rec, 'MNAM.NWCellX'):
        rect = framed_rect(get_int(rec, 'MNAM.NWCellX'),
                           get_int(rec, 'MNAM.NWCellY'),
                           get_int(rec, 'MNAM.SECellX'),
                           get_int(rec, 'MNAM.SECellY'))
    if rect is None:
        rect = (get_float(rec, 'NAM0.MinX'), get_float(rec, 'NAM0.MinY'),
                get_float(rec, 'NAM9.MaxX'), get_float(rec, 'NAM9.MaxY'))

    min_x, min_y, max_x, max_y = rect
    width = abs(max_x - min_x)
    height = abs(max_y - min_y)
    if width <= 0.0 or height <= 0.0:
        return None
    # The rectangle is not centred on the worldspace origin, and the stock
    # bank is -- so the sheet has to be moved onto the terrain or the far
    # side of the origin runs out from under the clouds.
    center = compute_center(min_x, min_y, max_x, max_y)
    # NAME only, no file. The bank is ONE mesh at a fixed path shared by every
    # plugin in the worldspace, so a per-plugin copy is a rival version of it:
    # each was sized to its own bounds and the install order picked a winner.
    # sibling_lod.merge_cloud_bank writes the single authoritative copy, sized
    # to the UNION of every sibling's land, into the LOD mod that installs
    # last. MODL is the same string either way, and every validity check still
    # runs -- a worldspace whose bank cannot be built still returns None here.
    return generate_cloud_bank(edid, width, height, _CLOUD_BANK_ROOT,
                               center=center, land_rect=rect, write=False)


def convert_CELL(rec: dict) -> bytes:
    """Convert CELL record."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    # DATA — TES5 uses uint16 flags (not uint8)
    flags = get_int(rec, 'DATA.Flags')
    flags &= ~0x08  # Remove Oblivion interior flag
    flags &= ~0x40  # Remove Hand Changed flag
    subs += pack_subrecord('DATA', struct.pack('<H', flags & 0xFFFF))

    # XCLC — grid coordinates (exterior cells)
    x = get_int(rec, 'XCLC.X', None)
    if x is not None:
        y = get_int(rec, 'XCLC.Y')
        subs += pack_subrecord('XCLC', struct.pack('<iiI', x, y, 0))  # 12 bytes in TES5

    # Interior lighting (XCLL) — shared with the override path
    xcll_payload = build_cell_xcll(rec)
    if xcll_payload is not None:
        subs += pack_subrecord('XCLL', xcll_payload)

    # LTMP — lighting template is a required TES5 subrecord.  TES4 has no
    # equivalent and XCLL inherits nothing, so point it at NULL.
    subs += pack_formid_subrecord('LTMP', 0)

    # Ownership
    xown = get_formid(rec, 'XOWN.Owner')
    if xown:
        subs += pack_formid_subrecord('XOWN', xown)

    # Water height — shared with the override path
    xclw_payload = build_cell_xclw(rec)
    if xclw_payload is not None:
        subs += pack_subrecord('XCLW', xclw_payload)

    # XCLR — the cell's region list.  THIS is how region weather reaches the
    # sky: the engine activates a region's RDWT list only in cells whose XCLR
    # names that region (Skyrim.esm: WeatherWinterhold sits in 30 cells' XCLR)
    # — the RPLD polygons alone do nothing at runtime.  Without XCLR every
    # converted exterior fell back to the climate's own WLST, and Tamriel's
    # is a single Clear weather at 100%, so the sky never changed.  Filtered
    # to regions that actually emitted (weather regions); TES4 lists many
    # object/grass/sound regions here that convert_REGN drops.  Sorted:
    # xEdit declares XCLR wbArrayS.
    region_fids = []
    i = 0
    while f'Region[{i}]' in rec:
        rfid = get_formid(rec, f'Region[{i}]')
        if rfid in _EMITTED_REGION_FIDS:
            region_fids.append(rfid)
        i += 1
    if region_fids:
        subs += pack_subrecord(
            'XCLR', struct.pack(f'<{len(region_fids)}I', *sorted(region_fids)))

    # XLCN — Location.  This does double duty in Skyrim: entering a cell that
    # belongs to a location discovers it (revealing its map marker), and it is
    # also where the engine reads the cell's *name* from.  Not one vanilla
    # exterior cell carries a FULL, so an exterior with no XLCN is displayed as
    # "Wilderness" on a load door.  Interiors are matched by FormID; exteriors
    # by their grid square, falling back to the worldspace's own location.
    lctn_fid = _CELL_LOCATION.get(get_formid(rec, 'FormID'))
    if not lctn_fid and x is not None:
        world_fid = get_formid(rec, 'ParentWRLD')
        lctn_fid = (_GRID_LOCATION.get((world_fid, x, y))
                    or _WORLD_LOCATION.get(world_fid))
    if lctn_fid:
        subs += pack_formid_subrecord('XLCN', lctn_fid)

    return pack_record('CELL', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_WRLD(rec: dict) -> bytes:
    subs = b''
    edid = get_str(rec, 'EditorID')

    # Oblivion's Tamriel (FormID 0x3C) conflicts with Skyrim's Tamriel.
    # After load-order remapping it becomes 0x0100003C which overrides Skyrim's
    # worldspace. Rename to avoid the override.
    if edid == 'Tamriel':
        edid = 'TES4Tamriel'

    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # Name the worldspace with the same resolved name its Location uses, so the
    # dev names Bethesda shipped (AnvilCastleCourtyardWorld's "TestEndGame") do
    # not reach the player, and so Tamriel — which has no FULL at all, because
    # the TES4 engine hardcoded its label — is not left nameless.
    # A worldspace absent from the table resolved to no usable name at all (the
    # unreachable test worlds); leaving it nameless beats shipping "TestMatt".
    full = WORLD_NAMES.get(get_formid(rec, 'FormID'))
    if full:
        subs += pack_string_subrecord('FULL', full)

    wnam = get_formid(rec, 'WNAM.Parent')
    if wnam:
        subs += pack_formid_subrecord('WNAM', wnam)

    # CNAM — Climate.  57 of 84 TES4 worldspaces author no CNAM at all —
    # including Tamriel itself, every Imperial City district and every walled
    # city.  Oblivion resolves those at RUNTIME rather than at load: verified
    # in Oblivion.exe (GOG/Steam 1.2.0.416), the sky setup at 0x667688 calls
    # the worldspace's get-climate (0x4CAF90) and, when it returns null, falls
    # through to 0x543200, which does LookupForm(0x15F) — the engine-created
    # 'DefaultClimate' form (bootstrap at 0x44CCE9 pushes 0x15F and names it
    # from the string at 0xA37CA0).  Skyrim has no such fallback, so TES4's
    # DefaultClimate is written explicitly and the worldspace keeps Cyrodiil's
    # sun, moons and weather list.  SNAM is omitted; it references a TES4
    # record we skip.
    cnam = get_formid(rec, 'CNAM.Climate') or remap_formid(_TES4_DEFAULT_CLIMATE)
    subs += pack_formid_subrecord('CNAM', cnam)

    # Water: TES4 WATR records are in skipTypes (we use Skyrim's water), so
    # point NAM2 (water type) and NAM3 (LOD water type) at Skyrim.esm's
    # DefaultWater (0x18, master index 0).  Vanilla Tamriel uses the same
    # record for both.  Without NAM3 the engine's terrain-LOD water codepath
    # derefs a null WATR pointer and CTDs as soon as a .btr contains a WATER
    # BSMultiBoundNode.  NAM4 = LOD water height; Oblivion's sea level is 0.
    subs += pack_formid_subrecord('NAM2', 0x00000018)
    subs += pack_formid_subrecord('NAM3', 0x00000018)
    subs += pack_float_subrecord('NAM4', 0.0)

    # DNAM — land/water defaults
    subs += pack_subrecord('DNAM', struct.pack('<ff', -2048.0, 0.0))

    # MODL — "Cloud Model", the mesh the WORLD MAP drapes over the terrain.
    # xEdit places the Cloud Model struct after the LOD/land data and before the
    # map data (MNAM), so it is written here.
    #
    # Neither game gives us a source for this: Oblivion has no world-map cloud
    # layer, and vanilla Skyrim authors no MODL either (0 of 35 uncompressed
    # Skyrim.esm WRLDs carry one).  Bethesda instead relies on the engine's
    # hardcoded fallback — but that mesh is sized for Skyrim's Tamriel, so on a
    # smaller converted worldspace it becomes an overcast sheet several times
    # the landmass.  Generate one scaled to THIS worldspace instead.
    cloud_rel = build_wrld_cloud_modl(rec, edid)
    if cloud_rel:
        subs += pack_string_subrecord('MODL', cloud_rel)

    # Map dimensions (MNAM) — after DNAM per xEdit order. Shared with the
    # override path.
    mnam = build_wrld_mnam(rec)
    if mnam is not None:
        subs += pack_subrecord('MNAM', mnam)

    # ONAM — World Map Offset Data (after MNAM per xEdit order)
    subs += pack_subrecord('ONAM', struct.pack('<ffff', 1.0, 0.0, 0.0, 0.0))

    # NAMA — Distant LOD multiplier
    subs += pack_float_subrecord('NAMA', 1.0)

    # DATA — flags (after NAMA per xEdit order)
    data_flags = get_int(rec, 'DATA.Flags')
    data_flags &= ~0x04  # Clear Oblivion flag (bit 2)
    # Move No LOD Water: bit $10 → bit $08
    if data_flags & 0x10:
        data_flags = (data_flags & ~0x10) | 0x08
    subs += pack_uint8_subrecord('DATA', data_flags)

    # NAM0 — World Object Bounds Min (X, Y as raw world-unit floats).
    # NAM9 — World Object Bounds Max. Required by SSELodGen for world map generation.
    # xEdit displays these values scaled by 1/4096 (cells), but the file stores raw
    # world units directly. TES4 and TES5 use the same world-unit scale, so write as-is.
    n0x_raw = get_float(rec, 'NAM0.MinX')
    n0y_raw = get_float(rec, 'NAM0.MinY')
    n9x_raw = get_float(rec, 'NAM9.MaxX')
    n9y_raw = get_float(rec, 'NAM9.MaxY')
    subs += pack_subrecord('NAM0', struct.pack('<ff', n0x_raw, n0y_raw))
    subs += pack_subrecord('NAM9', struct.pack('<ff', n9x_raw, n9y_raw))

    return pack_record('WRLD', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


# REFR record flag 0x400 — "Persistent Reference".
REFR_PERSISTENT_FLAG = 0x00000400


def _reference_location(rec: dict) -> int:
    """LCTN a placed reference belongs to, for its XLCN 'Persistent Location'.

    Skyrim's quest/map-marker system reads the marker position off the TARGET
    REFERENCE's own XLCN (a persistent ref's Location), not just its cell's —
    5639/10504 vanilla ACHR carry it and it mirrors the parent cell's Location.
    A quest target inside a cell whose CELL has XLCN but whose own ref does NOT
    produces a journal entry with no marker. Resolve the ref's Location the same
    way convert_CELL does: interior by parent-cell FormID, exterior by the grid
    square the ref stands in, falling back to the worldspace location.
    """
    parent_cell = get_formid(rec, 'ParentCELL')
    lctn_fid = _CELL_LOCATION.get(parent_cell)
    if lctn_fid:
        return lctn_fid
    world_fid = get_formid(rec, 'ParentWRLD')
    if world_fid:
        gx = _ref_grid(get_float(rec, 'PosX'))
        gy = _ref_grid(get_float(rec, 'PosY'))
        return (_GRID_LOCATION.get((world_fid, gx, gy))
                or _WORLD_LOCATION.get(world_fid) or 0)
    return 0


def _ref_grid(pos: float) -> int:
    """Exterior grid coordinate for a world-space ordinate (matches locations._grid)."""
    return int(pos // 4096.0)

# Map marker FNAM flags (identical in TES4 and TES5).
MAP_MARKER_VISIBLE = 0x01
MAP_MARKER_CAN_TRAVEL = 0x02


def map_marker_flags(rec: dict) -> int:
    """FNAM 'Map Flags' for a converted map marker.

    Oblivion and Skyrim agree on the bits (0x01 Visible, 0x02 Can Travel To) but
    not on how a marker becomes discovered:

    * Oblivion's engine flips Visible/Can Travel To at runtime, using its own
      hardcoded proximity check.  That is why 406 of its 513 markers ship as
      FNAM=0 (every cave, fort, Ayleid ruin and Oblivion gate) — the flags are
      placeholders the engine overwrites.
    * Skyrim has no such system.  A marker is revealed only when the player
      discovers the Location it belongs to, and it is only usable as a
      fast-travel destination if Can Travel To is set on the record.

    Copying FNAM verbatim would therefore leave those 406 markers permanently
    undiscoverable.  So: keep Visible exactly where Oblivion had it (cities and
    stables start revealed), and grant Can Travel To to every marker, letting
    Skyrim's Location discovery do the revealing.  This mirrors vanilla Skyrim,
    whose undiscovered markers pair FNAM=0 with a Location that reveals them.
    """
    tes4_flags = get_int(rec, 'MapMarker.Flags')
    flags = MAP_MARKER_CAN_TRAVEL
    if tes4_flags & MAP_MARKER_VISIBLE:
        flags |= MAP_MARKER_VISIBLE
    return flags


def convert_REFR(rec: dict) -> bytes:
    """REFR — placed object reference.

    TES5 order (from wbDefinitionsTES5.pas):
    EDID VMAD NAME XMBO XPRM ... XTEL XLOC XEZN ... XOWN XESP XLKR
    ... XSCL ... XMRK/FNAM/FULL/TNAM ... XLRT ... DATA
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # NAME = base object FormID (required)
    # For invisible marker base objects, substitute the Skyrim.esm equivalent
    # so REFRs point into Skyrim.esm (index 0) rather than our remapped copy.
    name_raw = int(rec.get('NAME', '0') or '0', 16)
    skyrim_marker = TES4_MARKER_FORMID_TO_SKYRIM.get(name_raw)
    if skyrim_marker is not None:
        name_fid = skyrim_marker  # Already a Skyrim.esm FormID — no offset
    else:
        name_fid = get_formid(rec, 'NAME')
    # Oblivion.esm ships 6 refs on the MapMarker base with no XMRK marker
    # data at all (campsite/battle position markers). Skyrim's map code
    # treats every 0x10-based ref as a map marker and the CK flags them
    # ("Mapmarker ref does not have map marker data") — they are plain
    # position markers, so ground them to XMarker.
    if name_raw == 0x10 and get_str(rec, 'MapMarker') != '1':
        name_fid = 0x0000003B
    if name_fid:
        subs += pack_formid_subrecord('NAME', name_fid)

    # TES4 XACT/ONAM ("Open by Default") are deliberately NOT transferred:
    # in Skyrim they make the door SPAWN open, but Oblivion doors carrying
    # them still spawn closed (in-game verified — every CG portcullis stood
    # open at load).  TES4 opens such doors via the AI bypass instead; see
    # the consume-door handling at XLOC below.

    # Teleport door (XTEL)
    xtel_door = get_formid(rec, 'XTEL.Door')
    if xtel_door:
        px = get_float(rec, 'XTEL.PosX')
        py = get_float(rec, 'XTEL.PosY')
        pz = get_float(rec, 'XTEL.PosZ')
        rx = _safe_angle(get_float(rec, 'XTEL.RotX'))
        ry = _safe_angle(get_float(rec, 'XTEL.RotY'))
        rz = _safe_angle(get_float(rec, 'XTEL.RotZ'))
        # TES5 XTEL is 32 bytes: Door(4) + Pos(12) + Rot(12) + Flags(4)
        # Flags: 0x0001 = No Alarm. Always 0 for converted doors.
        subs += pack_subrecord('XTEL', struct.pack('<IffffffI', xtel_door, px, py, pz, rx, ry, rz, 0))

    # Lock — XLOC is 20 bytes in TES5: Level(1)+pad(3)+Key(4)+Flags(1)+pad(3)+pad(8)
    # Transferred faithfully; TES4 level 100 becomes Requires Key (255) — see
    # map_lock_level.  AI passage through locked barrier doors is granted via
    # OWNERSHIP (the synthesized XOWN below), never by weakening the lock.
    barrier_door = False
    lock_level = get_int(rec, 'XLOC.Level', -1)
    if lock_level >= 0:
        lock_key = get_formid(rec, 'XLOC.Key')
        lock_flags = get_int(rec, 'XLOC.Flags')
        tes5_level = map_lock_level(lock_level, leveled=bool(lock_flags & 0x4))
        if tes5_level == 255 and not lock_key:
            from ..object_scripts import base_is_consume_door
            barrier_door = base_is_consume_door(rec.get('NAME', ''))
        subs += pack_subrecord('XLOC', struct.pack('<BxxxIBxxx8x', tes5_level, lock_key, lock_flags))

    # XLCN — Persistent Location. Only persistent refs carry it (they are the
    # quest-target-eligible ones); it lets the quest/map-marker system place a
    # marker on this reference. Emitted before XESP to match vanilla order.
    if get_int(rec, 'RecordFlags') & REFR_PERSISTENT_FLAG:
        ref_lctn = _reference_location(rec)
        if ref_lctn:
            subs += pack_formid_subrecord('XLCN', ref_lctn)

    # Enable parent (XESP)
    xesp_ref = get_formid(rec, 'XESP.Reference')
    if xesp_ref:
        xesp_flags = get_int(rec, 'XESP.Flags')
        subs += pack_subrecord('XESP', struct.pack('<II', xesp_ref, xesp_flags))

    # XLKR — Linked Reference.  TES4 has no such field; its `GetParentRef`
    # returns the ENABLE PARENT instead (xEdit names XESP 'Enable Parent', and
    # the UESP modding guide states the idiom directly: "make the container its
    # Parent Ref", then `set rCont to GetParentRef`).  Skyrim exposes no getter
    # for the enable parent, so script_convert maps GetParentRef ->
    # GetLinkedRef(), which reads XLKR.
    #
    # Nothing wrote XLKR, so every converted GetParentRef resolved to None.
    # That is why the Vilverin pressure plate did nothing when stepped on: its
    # body ran, but `target = GetLinkedRef()` was None, so `target.Activate()`
    # never reached the mace and the trap hung in the air.  Mirroring the
    # enable parent into XLKR restores the link the script expects.
    #
    # Layout (xEdit + a real Skyrim.esm dump, 11287 vanilla uses): 8 bytes,
    # {Keyword/Ref, Ref} with the keyword slot NULL for a plain link — vanilla
    # writes 00000000 there on the general case.
    #
    # Only emitted when the base record's script actually calls GetParentRef:
    # XESP is ordinary enable-parenting on 9157 Oblivion refs, and turning all
    # of those into linked refs would invent links the game never had.
    from ..object_scripts import base_uses_parent_ref
    if xesp_ref and base_uses_parent_ref(rec.get('NAME', '')):
        subs += pack_subrecord('XLKR', struct.pack('<II', 0, xesp_ref))

    # Ownership (XOWN)
    xown = get_formid(rec, 'XOWN.Owner')
    if not xown and barrier_door:
        # Requires-Key keyless barrier door with a consume script: Skyrim's
        # AI only passes a locked door it OWNS or holds the key for (vanilla
        # 255 doors NPCs path through are exactly those — guards with gate
        # keys, homeowners), while Oblivion's AI ignored locks entirely, so
        # the CharacterGen back gate stranded Glenroy.  Owning these doors to
        # the plugin-origin faction — which every converted actor of a root
        # master already joins, and which carries no crime data — grants all
        # of them the engine's own owner exemption.  The player is not a
        # member: the lock reads Requires Key and activation stays blocked.
        # The OnActivate preamble restores the lock after each AI passage.
        from .actors import get_origin_faction_fid
        xown = get_origin_faction_fid()
    if xown:
        subs += pack_formid_subrecord('XOWN', xown)

    # Scale (XSCL)
    scale = get_float(rec, 'XSCL.Scale')
    if scale and scale != 1.0:
        subs += pack_float_subrecord('XSCL', scale)

    # XTRG does NOT exist in TES5 — skip it entirely

    # Map Marker (XMRK + FNAM + FULL + TNAM, then XLRT).
    is_map_marker = get_str(rec, 'MapMarker') == '1'
    if is_map_marker:
        subs += pack_subrecord('XMRK', b'')
        subs += pack_uint8_subrecord('FNAM', map_marker_flags(rec))
        marker_full = get_str(rec, 'MapMarker.FULL')
        if marker_full:
            subs += pack_string_subrecord('FULL', marker_full)
        marker_type = get_int(rec, 'MapMarker.Type')
        tes5_marker = MAP_MARKER_TYPE_MAP.get(marker_type, 0)
        subs += pack_subrecord('TNAM', struct.pack('<BB', tes5_marker, 0))
        # XLRT — Location Ref Type.  Binds this reference to its Location as
        # that Location's map marker; without it the engine cannot tie the
        # marker to the Location the player discovers.  396/397 vanilla map
        # markers carry MapMarkerRefType here.
        subs += pack_formid_subrecord('XLRT', SKYRIM_MAP_MARKER_LCRT)

    # XNDP — Navmesh Door Link: the navmesh triangle this door stands on.
    # Emitted last, immediately before DATA, which is where all 1,706 vanilla
    # XNDP records sit (after XLOC/XOWN/XLRT/XSCL).  Struct: Navmesh FormID(4)
    # + Triangle s16 + 2 unused.
    door_link = _DOOR_NAVMESH_LINK.get(get_formid(rec, 'FormID'))
    if door_link:
        navm_fid, tri_index = door_link
        subs += pack_subrecord('XNDP', struct.pack('<Ihxx', navm_fid,
                                                   tri_index))

    # Position/Rotation (DATA)
    px = get_float(rec, 'PosX')
    py = get_float(rec, 'PosY')
    pz = get_float(rec, 'PosZ')
    rx = _safe_angle(get_float(rec, 'RotX'))
    ry = _safe_angle(get_float(rec, 'RotY'))
    rz = _safe_angle(get_float(rec, 'RotZ'))

    # Furniture origin compensation: marker-bearing models are re-origined
    # to the floor (+shift inside the NIF), so their placed references drop
    # by the same amount along the model's local Z — world visuals stay
    # identical while the REFR z lands at the floor, where the engine
    # anchors seated actors.  See asset_convert/furniture_markers.py.
    shift = get_base_origin_shift(rec.get('NAME', '') or '')
    if shift:
        s = scale if scale and scale != 1.0 else 1.0
        if abs(rx) < 1e-4 and abs(ry) < 1e-4:
            pz -= shift * s
        else:
            # Local +Z in world for Bethesda euler (R = Rz·Ry·Rx)
            wx = math.cos(rx) * math.sin(ry) * math.cos(rz) + math.sin(rx) * math.sin(rz)
            wy = math.cos(rx) * math.sin(ry) * math.sin(rz) - math.sin(rx) * math.cos(rz)
            wz = math.cos(rx) * math.cos(ry)
            px -= shift * s * wx
            py -= shift * s * wy
            pz -= shift * s * wz
    subs += pack_subrecord('DATA', struct.pack('<ffffff', px, py, pz, rx, ry, rz))

    flags = get_int(rec, 'RecordFlags')
    if is_map_marker:
        # Map markers must be persistent references — the map/fast-travel system
        # resolves them outside the loaded cell.  All 397 vanilla markers set it.
        flags |= REFR_PERSISTENT_FLAG
    return pack_record('REFR', get_formid(rec, 'FormID'), flags, subs)


def convert_ACHR(rec: dict) -> bytes:
    """ACHR — placed NPC reference. TES4 ACRE also maps here."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # VMAD — a converted actor script relocated onto this placed reference so a
    # GetVMScriptVariable package condition (which reads the property off the ref
    # named in its param1, not the base actor) can pass and the quest package can
    # win (see object_scripts._relocate_actor_scripts_to_refs). Skyrim order:
    # EDID VMAD NAME ...
    from ..object_scripts import get_object_vmad
    vmad = get_object_vmad(get_formid(rec, 'FormID'))
    if vmad:
        subs += vmad

    name_fid = get_formid(rec, 'NAME')
    if name_fid:
        subs += pack_formid_subrecord('NAME', name_fid)

    # XLCN — Persistent Location (see convert_REFR). This is what lets a quest
    # marker resolve onto a persistent actor placed inside an interior — without
    # it the objective shows in the journal but no compass/map arrow appears.
    if get_int(rec, 'RecordFlags') & REFR_PERSISTENT_FLAG:
        ref_lctn = _reference_location(rec)
        if ref_lctn:
            subs += pack_formid_subrecord('XLCN', ref_lctn)

    xesp_ref = get_formid(rec, 'XESP.Reference')
    if xesp_ref:
        xesp_flags = get_int(rec, 'XESP.Flags')
        subs += pack_subrecord('XESP', struct.pack('<II', xesp_ref, xesp_flags))

    scale = get_float(rec, 'XSCL.Scale')
    if scale and scale != 1.0:
        subs += pack_float_subrecord('XSCL', scale)

    px = get_float(rec, 'PosX')
    py = get_float(rec, 'PosY')
    pz = get_float(rec, 'PosZ')
    rx = _safe_angle(get_float(rec, 'RotX'))
    ry = _safe_angle(get_float(rec, 'RotY'))
    rz = _safe_angle(get_float(rec, 'RotZ'))
    subs += pack_subrecord('DATA', struct.pack('<ffffff', px, py, pz, rx, ry, rz))

    flags = get_int(rec, 'RecordFlags')
    return pack_record('ACHR', get_formid(rec, 'FormID'), flags, subs)


def convert_ACRE(rec: dict) -> bytes:
    """ACRE → ACHR (placed creature → placed NPC)."""
    return convert_ACHR(rec)


def convert_LAND(rec: dict) -> bytes:
    """LAND record — landscape vertex data."""
    subs = b''

    # DATA flags pass through VERBATIM. Bit 0 (0x01) = "Has Vertex
    # Normals/Height Map", bit 4 (0x10) = "Auto-Calc Normals".
    #
    # A LAND with no VNML/VHGT is the author DELETING that cell's terrain --
    # the "water only, no landscape" case -- and it is legal with or without
    # the auto-calc bit. Skyrim.esm's records that clear bit 0: 149 at
    # flags 28, 3 at flags 30 (these carry VCLR), and 2 at flags 12, which is
    # exactly the value TWMP_ValenwoodImproved uses. Do NOT "normalise" these
    # to 28: a partial census that missed the flags-12 pair made that look
    # like a defect when it is vanilla-legal.
    data_flags = get_int(rec, 'DATA.Flags')
    subs += pack_subrecord('DATA', struct.pack('<I', data_flags))

    # VNML — vertex normals (raw hex)
    vnml_hex = get_str(rec, 'VNML')
    if vnml_hex:
        subs += pack_subrecord('VNML', bytes.fromhex(vnml_hex))

    # VHGT — vertex heights (raw hex)
    vhgt_hex = get_str(rec, 'VHGT')
    if vhgt_hex:
        subs += pack_subrecord('VHGT', bytes.fromhex(vhgt_hex))

    # VCLR — vertex colors (raw hex)
    vclr_hex = get_str(rec, 'VCLR')
    if vclr_hex:
        subs += pack_subrecord('VCLR', bytes.fromhex(vclr_hex))

    subs += build_land_layers(rec)

    # VTEX is a TES4-only subrecord; TES5 LAND does not have it.
    # Texture references are already encoded in BTXT/ATXT FormIDs above.

    flags = get_int(rec, 'RecordFlags')
    return pack_record('LAND', get_formid(rec, 'FormID'), flags, subs)


def build_land_layers(rec: dict) -> bytes:
    """The LAND texture-layer run: BTXT/ATXT/VTXT, in TES5 order.

    Split out of convert_LAND so the override path can rebuild the whole run
    from the PLUGIN's export when an author changes Layer[] (override_builder
    _RUN_LAND_LAYERS). The merge/sort/cap below is lossy and order-dependent,
    so an override MUST reuse this function rather than reimplement it — two
    implementations would disagree and the terrain would re-texture itself on
    every unrelated edit.
    """
    subs = b''

    # Layers (BTXT/ATXT/VTXT)
    # TES5 limit: max 6 alpha layers per quadrant (indices 0–5).
    # Strategy: two-pass approach.
    #   Pass 1: collect all alpha layers per quadrant; merge same-texture layers
    #           by taking the max opacity per vertex position.
    #   Pass 2: sort by coverage score (sum of opacities) descending, keep top 6,
    #           write in coverage order so the most visually significant layers survive.
    _MAX_ALPHA_LAYERS = 6
    layer_count = get_int(rec, 'LayerCount')

    # Pass 1: collect layers
    # base_layers: quad -> (tex, order_index) — we keep first BASE seen per quad
    base_layers: dict = {}
    # alpha_layers: quad -> list of [tex, {pos: opacity}]
    alpha_layers: dict = {}

    for i in range(layer_count):
        pfx = f'Layer[{i}]'
        ltype = get_str(rec, f'{pfx}.Type')
        if ltype == 'BASE':
            tex = get_formid(rec, f'{pfx}.BTXT.Texture')
            quad = get_int(rec, f'{pfx}.BTXT.Quadrant')
            if quad not in base_layers:
                base_layers[quad] = tex
        elif ltype == 'ALPHA':
            tex = get_formid(rec, f'{pfx}.ATXT.Texture')
            quad = get_int(rec, f'{pfx}.ATXT.Quadrant')
            if tex == 0:
                continue
            # Collect vtxt as pos->opacity dict
            vtxt_count = get_int(rec, f'{pfx}.VTXTCount')
            vtxt: dict = {}
            for vi in range(vtxt_count):
                vpos = get_int(rec, f'{pfx}.VT[{vi}].Pos')
                opacity = get_float(rec, f'{pfx}.VT[{vi}].Opacity')
                vtxt[vpos] = opacity
            # Merge duplicate textures in the same quadrant: keep max opacity per vertex
            if quad not in alpha_layers:
                alpha_layers[quad] = []
            existing = next((e for e in alpha_layers[quad] if e[0] == tex), None)
            if existing is not None:
                for pos, op in vtxt.items():
                    if op > existing[1].get(pos, 0.0):
                        existing[1][pos] = op
            else:
                alpha_layers[quad].append([tex, vtxt])

    # Pass 2: emit base layers first, then sorted alpha layers
    for quad in sorted(base_layers):
        tex = base_layers[quad]
        btxt = struct.pack('<IBBxx', tex, quad, 0)
        subs += pack_subrecord('BTXT', btxt)

        layers_for_quad = alpha_layers.get(quad, [])
        # Sort by coverage score descending (sum of opacity values), keep top 6
        layers_for_quad.sort(key=lambda e: sum(e[1].values()), reverse=True)
        for alpha_idx, (tex, vtxt) in enumerate(layers_for_quad[:_MAX_ALPHA_LAYERS]):
            atxt = struct.pack('<IBBH', tex, quad, 0, alpha_idx)
            subs += pack_subrecord('ATXT', atxt)
            if vtxt:
                vtxt_data = bytearray()
                for vpos, opacity in sorted(vtxt.items()):
                    vtxt_data += struct.pack('<HHf', vpos, 0, opacity)
                subs += pack_subrecord('VTXT', bytes(vtxt_data))

    return subs


# TES4 REGN data-entry type enum, shared verbatim by TES5 (xEdit
# wbDefinitionsTES4/TES5): 2 Objects, 3 Weather, 4 Map, 5 Land, 6 Grass,
# 7 Sound.
_REGN_DATA_WEATHER = 3

# Remapped FormIDs of the regions convert_REGN actually emitted.  The engine
# activates a region's weather through the CELL's XCLR region list (verified
# against Skyrim.esm: WeatherWinterhold appears in 30 cells' XCLR,
# WeatherCoastFog in 51), so convert_CELL writes XCLR filtered against this
# set — a cell must never reference a region that was dropped.  Regions are
# converted in import phase 1, before any CELL is built.
_EMITTED_REGION_FIDS = set()


def reset_emitted_regions():
    """Called at import start so a multi-plugin run doesn't leak regions."""
    _EMITTED_REGION_FIDS.clear()


def convert_REGN(rec: dict):
    """REGN — Region, converted for its WEATHER entries only.
    Returns packed bytes, or None to emit nothing.

    This is where Cyrodiil's weather actually lives: TamrielClimate's WLST is
    a single Clear weather at 100%, and the variety (rain in the Blackwood,
    snow around Bruma...) comes from 59 region RDWT weather lists layered over
    it.  Skyrim uses the identical mechanism for its own coasts and holds
    (WeatherCoastFog, WeatherWinterhold...), so the data passes through:
    RDAT header byte-identical, RDWT entries widened 8 -> 12 bytes by the
    trailing Global FormID, RPLI/RPLD area polygons byte-identical.

    The other data types (objects, grass, sound, map) drive TES4-side
    generators with no behavioural equivalent here and are dropped.  A region
    with no weather list — or no area polygon to apply it in — emits nothing.

    TES5 subrecord order (wbDefinitionsTES5): EDID, RCLR, WNAM,
    [RPLI, RPLD]*, [RDAT, RDWT]*.
    """
    n_entries = get_int(rec, 'RegionDataCount')
    weather_entries = []
    for i in range(n_entries):
        if get_int(rec, f'RegionData[{i}].Type') != _REGN_DATA_WEATHER:
            continue
        wlist = b''
        for j in range(get_int(rec, f'RegionData[{i}].WeatherCount')):
            wfid = get_formid(rec, f'RegionData[{i}].Weather[{j}].FormID')
            if not wfid:
                continue
            chance = get_int(rec, f'RegionData[{i}].Weather[{j}].Chance')
            wlist += struct.pack('<III', wfid, chance, 0)
        if wlist:
            weather_entries.append((get_int(rec, f'RegionData[{i}].Override'),
                                    get_int(rec, f'RegionData[{i}].Priority'),
                                    wlist))

    # Area polygons: the engine applies region data only inside these.
    areas = b''
    for i in range(get_int(rec, 'AreaCount')):
        points = get_hex_bytes(rec, f'Area[{i}].PointsHex')
        if not points:
            continue
        areas += pack_subrecord(
            'RPLI', struct.pack('<I', get_int(rec, f'Area[{i}].EdgeFalloff')))
        areas += pack_subrecord('RPLD', points)

    if not weather_entries or not areas:
        return None

    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    subs += pack_subrecord('RCLR', struct.pack(
        '<BBBB', get_int(rec, 'RCLR.R'), get_int(rec, 'RCLR.G'),
        get_int(rec, 'RCLR.B'), 0))

    wnam = get_formid(rec, 'WNAM.Worldspace')
    if wnam:
        subs += pack_formid_subrecord('WNAM', wnam)

    subs += areas

    for override, priority, wlist in weather_entries:
        subs += pack_subrecord('RDAT', struct.pack(
            '<IBBxx', _REGN_DATA_WEATHER, 1 if override else 0,
            min(255, priority)))
        subs += pack_subrecord('RDWT', wlist)

    fid = get_formid(rec, 'FormID')
    _EMITTED_REGION_FIDS.add(fid)
    return pack_record('REGN', fid, get_int(rec, 'RecordFlags'), subs)


def convert_LSCR(rec: dict) -> bytes:
    """LSCR — Loading Screen. No OBND per xEdit.

    TES5 order: EDID ICON DESC CTDA NNAM SNAM RNAM ONAM XNAM MOD2
    NNAM is a FormID → STAT (the loading screen 3D model), required.
    ICON omitted: TES5 loading screens use 3D models, not 2D textures.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    desc = get_str(rec, 'DESC')
    if desc:
        subs += pack_string_subrecord('DESC', desc)
    # NNAM — Loading Screen NIF: FormID → STAT|NULL (required, 4 bytes)
    # TES4 doesn't have a 3D model ref; use NULL (0)
    subs += pack_formid_subrecord('NNAM', 0)
    return pack_record('LSCR', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_WATR(rec: dict) -> bytes:
    """WATR — Water Type conversion.

    TES5 order: EDID FULL NNAM ANAM FNAM MNAM SNAM XNAM DATA DNAM GNAM NAM0 NAM1
    TES5 DATA is 228 bytes, heavily restructured from TES4.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # NNAM — Noise map texture (TES5 uses separate field)
    texture = get_str(rec, 'TNAM.Texture')
    if texture:
        subs += pack_string_subrecord('NNAM', _prefix_path(texture))

    # ANAM — Opacity
    opacity = get_int(rec, 'ANAM.Opacity', 128)
    subs += pack_uint8_subrecord('ANAM', opacity)

    # FNAM — Flags
    flags = get_int(rec, 'FNAM.Flags')
    subs += pack_uint8_subrecord('FNAM', flags)

    # MNAM — Material ID (string)
    mat_id = get_str(rec, 'MNAM.MaterialID')
    if mat_id:
        subs += pack_string_subrecord('MNAM', mat_id)

    # SNAM — Sound (open water sound)
    sound_fid = get_formid(rec, 'SNAM.Sound')
    if sound_fid:
        subs += pack_formid_subrecord('SNAM', sound_fid)

    # DATA — Water properties (228 bytes in TES5)
    # Preserve wind velocity/direction from TES4, fill rest with reasonable defaults
    data = bytearray(228)
    wind_vel = get_float(rec, 'DATA.WindVelocity', 0.3)
    wind_dir = get_float(rec, 'DATA.WindDirection', 0.0)
    # Byte 0-3: Unknown float
    struct.pack_into('<f', data, 0, 0.1)     # Unknown
    struct.pack_into('<f', data, 4, 0.1)     # Unknown
    struct.pack_into('<f', data, 8, 0.1)     # Unknown
    struct.pack_into('<f', data, 12, wind_vel)
    struct.pack_into('<f', data, 16, wind_dir)
    # Sun specular power
    struct.pack_into('<f', data, 20, 100.0)
    # Reflectivity amount
    struct.pack_into('<f', data, 24, 0.5)
    # Fresnel amount
    struct.pack_into('<f', data, 28, 0.025)
    # Scroll speeds (UV for layers)
    struct.pack_into('<f', data, 36, 0.3)
    struct.pack_into('<f', data, 40, 0.3)
    # Fog amount
    struct.pack_into('<f', data, 64, 0.01)
    # Fog near plane distance
    struct.pack_into('<f', data, 68, 1000.0)
    # Fog far plane distance
    struct.pack_into('<f', data, 72, 100000.0)
    # Shallow color (RGBA at offset 76): blue-ish
    data[76] = 64; data[77] = 96; data[78] = 128; data[79] = 200
    # Deep color (RGBA at offset 80): darker blue
    data[80] = 32; data[81] = 48; data[82] = 96; data[83] = 255
    # Reflection color (RGBA at offset 84): light
    data[84] = 200; data[85] = 200; data[86] = 200; data[87] = 128
    # Depth
    struct.pack_into('<f', data, 100, 150.0)
    subs += pack_subrecord('DATA', bytes(data))

    # DNAM — Visual data (196 bytes in TES5) — fill with defaults
    dnam = bytearray(196)
    struct.pack_into('<f', dnam, 0, 10.0)    # Depth normals
    struct.pack_into('<f', dnam, 4, 1.0)     # Depth reflections
    struct.pack_into('<f', dnam, 8, 0.5)     # Depth refraction
    struct.pack_into('<f', dnam, 12, 1.0)    # Depth specular lighting
    subs += pack_subrecord('DNAM', bytes(dnam))

    return pack_record('WATR', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


# ---------------------------------------------------------------------------
# Effect shader texture substitution
# ---------------------------------------------------------------------------
# Oblivion's renderer synthesises a membrane from the DATA colour fields alone,
# so a texture-less EFSH is ordinary there: 42 of Oblivion.esm's 102 records
# name neither a fill (ICON) nor a particle (ICO2) texture.  Skyrim's samples a
# fill texture and modulates it through a gradient palette (NAM8/NAM9) — a
# concept TES4 has no field for — so the same record ported faithfully has
# nothing to sample and composites as opaque black over the actor.  Vanilla
# Skyrim leaves only 4 of its 169 shaders fully texture-less.
#
# Fix: when the source names no texture, borrow the texture set from the
# vanilla shader built for the same job.  The TES4 EditorID is the authored
# indicator — `effectEnchant<School>` and `effect<Element>Shield` say exactly
# which vanilla family the record belongs to — so the match keys off the name,
# never off a colour heuristic.  The source's own colours still drive the DATA
# fields; only the texture paths come from vanilla.
#
# (fill ICON, particle ICO2, membrane palette NAM8, particle palette NAM9)
_TX_ENCH_ARMOR = ('Effects\\VaporTile01.dds', 'Effects\\FXFireAtlas02.dds',
                  'Effects\\Gradients\\GradShockEnchArmor.dds',
                  'Effects\\Gradients\\GradFireExplosion.dds')
_TX_ENCH_FLAME = ('Effects\\EnchFlameProject01.dds', '',
                  'Effects\\Gradients\\GradFlameEnch.dds', '')
_TX_FROST      = ('Effects\\CloudTileFrostSmall.dds', 'Effects\\MagicIceWisps.dds',
                  'Effects\\Gradients\\GradFrostIceForm.dds', '')
_TX_FIRE       = ('Effects\\VaporTile01.dds', 'Effects\\FXFireAtlas02.dds',
                  'Effects\\Gradients\\GradFrostIceFormOrange.dds',
                  'Effects\\Gradients\\GradFireExplosion.dds')
_TX_SHOCK      = ('Effects\\ShockTile01.dds', 'Effects\\ShockParticles02.dds',
                  'Effects\\Gradients\\GradShockHit.dds', '')
_TX_POISON     = ('Effects\\CloudTileFrostSmall.dds', 'Effects\\MagicIceWisps.dds',
                  'Effects\\Gradients\\GradPoisonForm.dds', '')
_TX_SHIELD     = ('Effects\\DarkSwirls.dds', 'Effects\\ShieldParticles.dds',
                  'Effects\\Gradients\\GradShockHit.dds', '')
_TX_ILLUSION   = ('', 'Effects\\MagicSquiggles01.dds', '', '')
_TX_RESTORE    = ('', 'Effects\\FXGlowySparks.dds', '', '')
_TX_TURNUNDEAD = ('Effects\\VaporTile02.dds', 'Effects\\FXFireAtlas02.dds', '',
                  'Effects\\Gradients\\GradTurnMagic.dds')
_TX_DRAIN      = ('Effects\\DarkSwirls.dds', 'Effects\\MagicCaustic01.dds', '', '')
_TX_TELEKINESIS = ('', 'Effects\\SmallGlowSwirls.dds', '',
                   'Effects\\Gradients\\GradTelekinesis01.dds')

# Matched longest-first against the TES4 EditorID, case-insensitively, so
# `effectEnchantDestruction` binds to the destruction entry and not to the
# generic `effectEnchant` one.
_EFSH_TEXTURE_SUBS = (
    ('effectenchantdestruction', _TX_ENCH_FLAME),
    ('effectenchantrestoration', _TX_ENCH_ARMOR),
    ('effectenchantconjuration', _TX_ENCH_ARMOR),
    ('effectenchantalteration',  _TX_ENCH_ARMOR),
    ('effectenchantmysticism',   _TX_ENCH_ARMOR),
    ('effectenchantillusion',    _TX_ILLUSION),
    ('effectenchantturnundead',  _TX_TURNUNDEAD),
    ('effectenchantpoison',      _TX_POISON),
    ('effectresistnormalweapons', _TX_SHIELD),
    ('effectfortifymagicka',     _TX_ENCH_ARMOR),
    ('effectfortifyfatigue',     _TX_ENCH_ARMOR),
    ('effectfortifyhealth',      _TX_RESTORE),
    ('effectrestorehealth',      _TX_RESTORE),
    ('effecttelekinesis',        _TX_TELEKINESIS),
    ('effectdemoralize',         _TX_ILLUSION),
    ('effectfrostshield',        _TX_FROST),
    ('effectshockshield',        _TX_SHOCK),
    ('effectfireshield',         _TX_FIRE),
    ('effectturnundead',         _TX_TURNUNDEAD),
    ('effectdestruction',        _TX_ENCH_FLAME),
    ('effectenchant',            _TX_ENCH_ARMOR),
    ('effectsoultrap',           _TX_RESTORE),
    ('effectweakness',           _TX_DRAIN),
    ('effectdisease',            _TX_POISON),
    ('effectfortify',            _TX_ENCH_ARMOR),
    ('effectrestore',            _TX_RESTORE),
    ('effectpoison',             _TX_POISON),
    ('effectfrenzy',             _TX_ILLUSION),
    ('effectsilence',            _TX_ILLUSION),
    ('effectdamage',             _TX_DRAIN),
    ('effectdrain',              _TX_DRAIN),
    ('effectshield',             _TX_SHIELD),
    ('effectcharm',              _TX_ILLUSION),
    ('effectcalm',               _TX_ILLUSION),
    ('effectrally',              _TX_ILLUSION),
    ('effectcommand',            _TX_ILLUSION),
    ('effectlock',               _TX_ILLUSION),
    ('effectopen',               _TX_ILLUSION),
    ('frostshader',              _TX_FROST),
    ('watershader',              _TX_FROST),
    ('chimeeffect',              _TX_RESTORE),
    ('ordershader',              _TX_ILLUSION),
)

# Whatever a plugin names its shader, a membrane with no fill texture renders
# black, so an unrecognised EditorID still needs a texture set rather than
# nothing.  The enchant-armour set is the least specific of the vanilla
# families and reads as a neutral shimmer.
_TX_DEFAULT = _TX_ENCH_ARMOR

# TES4 DATA.Flags bit 0 = "No Membrane Shader": such a record draws particles
# only, so it needs no fill texture and gets no substitution.
_T4_NO_MEMBRANE = 0x01


def _substitute_textures(edid: str, flags: int):
    """Vanilla texture set for a texture-less TES4 shader, or None."""
    if flags & _T4_NO_MEMBRANE:
        return None
    name = (edid or '').lower()
    for key, tx in _EFSH_TEXTURE_SUBS:
        if key in name:
            return tx
    return _TX_DEFAULT


def convert_EFSH(rec: dict) -> bytes:
    """EFSH — Effect Shader.

    TES5 DATA is 400 bytes and prefix-compatible with TES4's 224: every field
    up to and including the three colour keys sits at an identical offset in
    both games, so the source block is copied field-for-field and only the
    TES5-only tail (holes, addon models, particle rotation, animated frames,
    the widened U32 flags, texture scales) is filled with vanilla defaults.

    Writing a short DATA is not an option: vanilla Skyrim ships 400 bytes on
    152 of 169 records, and the engine reads the tail regardless of the
    subrecord's declared length.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    flags8 = get_int(rec, 'DATA.Flags')
    icon = get_str(rec, 'ICON')
    ico2 = get_str(rec, 'ICO2')
    nam8 = ''
    nam9 = ''
    # Converted TES4 textures live under our own tes4\ namespace; substituted
    # ones are vanilla Skyrim assets that ship in Skyrim's BSAs, so prefixing
    # those would point at files we never wrote and render black all over
    # again.  `borrowed` tracks which case this record is in.
    borrowed = False
    if not icon and not ico2:
        tx = _substitute_textures(edid, flags8)
        if tx:
            icon, ico2, nam8, nam9 = tx
            borrowed = True

    def _tex(p):
        if not p:
            return ''
        return p if borrowed else _prefix_path(p)

    # ICON/ICO2 are SetRequired on the TES4 record and present on every vanilla
    # TES5 one; NAM7 (holes) has no TES4 source and stays empty.
    subs += pack_string_subrecord('ICON', _tex(icon))
    subs += pack_string_subrecord('ICO2', _tex(ico2))
    subs += pack_string_subrecord('NAM7', '')
    if nam8:
        subs += pack_string_subrecord('NAM8', _tex(nam8))
    if nam9:
        subs += pack_string_subrecord('NAM9', _tex(nam9))

    data = bytearray(400)

    def put_f(off, key, default=0.0):
        struct.pack_into('<f', data, off, get_float(rec, key, default))

    def put_u(off, key, default=0):
        struct.pack_into('<I', data, off, get_int(rec, key, default))

    def put_rgb(off, key):
        data[off] = get_int(rec, key + 'R') & 0xFF
        data[off + 1] = get_int(rec, key + 'G') & 0xFF
        data[off + 2] = get_int(rec, key + 'B') & 0xFF
        data[off + 3] = 0

    data[0] = flags8 & 0xFF
    # Membrane shader blend state.  Defaults are the xEdit-documented ones, so
    # a truncated 96-byte source still yields a sane membrane.
    put_u(4, 'DATA.MemSBlend', 5)
    put_u(8, 'DATA.MemBlendOp', 1)
    put_u(12, 'DATA.MemZFunc', 3)
    put_rgb(16, 'DATA.FillColor')
    put_f(20, 'DATA.FillAlphaFadeInTime')
    put_f(24, 'DATA.FillAlphaFull')
    put_f(28, 'DATA.FillAlphaFadeOutTime')
    put_f(32, 'DATA.FillAlphaPersistPercent')
    put_f(36, 'DATA.FillAlphaPulseAmp')
    put_f(40, 'DATA.FillAlphaPulseFreq', 1.0)
    put_f(44, 'DATA.FillTextureAnimSpeedU')
    put_f(48, 'DATA.FillTextureAnimSpeedV')
    put_f(52, 'DATA.EdgeEffectWidth', 1.0)
    put_rgb(56, 'DATA.EdgeColor')
    put_f(60, 'DATA.EdgeAlphaFadeInTime')
    put_f(64, 'DATA.EdgeAlphaFull')
    put_f(68, 'DATA.EdgeAlphaFadeOutTime')
    put_f(72, 'DATA.EdgeAlphaPersistPercent')
    put_f(76, 'DATA.EdgeAlphaPulseAmp')
    put_f(80, 'DATA.EdgeAlphaPulseFreq', 1.0)
    put_f(84, 'DATA.FillFullAlphaRatio', 1.0)
    put_f(88, 'DATA.EdgeFullAlphaRatio', 1.0)
    put_u(92, 'DATA.MemDestBlend', 6)
    # Particle shader
    put_u(96, 'DATA.PartSBlend', 5)
    put_u(100, 'DATA.PartBlendOp', 1)
    put_u(104, 'DATA.PartZFunc', 4)
    put_u(108, 'DATA.PartDestBlend', 6)
    put_f(112, 'DATA.PartBirthRampUp')
    put_f(116, 'DATA.PartFullBirthTime')
    put_f(120, 'DATA.PartBirthRampDown')
    put_f(124, 'DATA.PartFullBirthRatio', 1.0)
    put_f(128, 'DATA.PartPersistBirthRatio', 1.0)
    put_f(132, 'DATA.PartLifetime', 1.0)
    put_f(136, 'DATA.PartLifetimeDelta')
    put_f(140, 'DATA.PartInitSpeedNormal')
    put_f(144, 'DATA.PartAccelNormal')
    put_f(148, 'DATA.PartInitVel1')
    put_f(152, 'DATA.PartInitVel2')
    put_f(156, 'DATA.PartInitVel3')
    put_f(160, 'DATA.PartAccel1')
    put_f(164, 'DATA.PartAccel2')
    put_f(168, 'DATA.PartAccel3')
    put_f(172, 'DATA.PartScaleKey1', 1.0)
    put_f(176, 'DATA.PartScaleKey2', 1.0)
    put_f(180, 'DATA.PartScaleKey1Time')
    put_f(184, 'DATA.PartScaleKey2Time', 1.0)
    put_rgb(188, 'DATA.ColorKey1')
    put_rgb(192, 'DATA.ColorKey2')
    put_rgb(196, 'DATA.ColorKey3')
    put_f(200, 'DATA.ColorKey1Alpha', 1.0)
    put_f(204, 'DATA.ColorKey2Alpha', 1.0)
    put_f(208, 'DATA.ColorKey3Alpha', 1.0)
    put_f(212, 'DATA.ColorKey1Time')
    put_f(216, 'DATA.ColorKey2Time', 0.5)
    put_f(220, 'DATA.ColorKey3Time', 1.0)

    # --- TES5-only tail (offset 224+): no TES4 source, vanilla defaults. ---
    # Particle rotation (224-240) and Addon Models (244) stay 0/absent.
    struct.pack_into('<f', data, 264, 1.0)   # Holes - End Val
    # Edge width in alpha units + its colour mirror the edge block above, which
    # is what vanilla shaders carrying an edge effect do.
    struct.pack_into('<f', data, 268, get_float(rec, 'DATA.EdgeEffectWidth', 1.0))
    data[272] = get_int(rec, 'DATA.EdgeColorR') & 0xFF
    data[273] = get_int(rec, 'DATA.EdgeColorG') & 0xFF
    data[274] = get_int(rec, 'DATA.EdgeColorB') & 0xFF
    struct.pack_into('<I', data, 280, 1)     # Texture Count U
    struct.pack_into('<I', data, 284, 1)     # Texture Count V
    struct.pack_into('<f', data, 288, 1.0)   # Addon Models - Fade In Time
    struct.pack_into('<f', data, 292, 1.0)   # Addon Models - Fade Out Time
    struct.pack_into('<f', data, 296, 1.0)   # Addon Models - Scale Start
    struct.pack_into('<f', data, 300, 1.0)   # Addon Models - Scale End
    struct.pack_into('<f', data, 304, 1.0)   # Addon Models - Scale In Time
    struct.pack_into('<f', data, 308, 1.0)   # Addon Models - Scale Out Time
    # Fill colour keys 2 and 3: TES4 has one fill colour, so all three keys
    # carry it and the membrane holds a steady colour instead of fading to
    # black across the key ramp.
    put_rgb(316, 'DATA.FillColor')
    put_rgb(320, 'DATA.FillColor')
    struct.pack_into('<f', data, 324, 1.0)   # Colour key 1 scale
    struct.pack_into('<f', data, 328, 1.0)   # Colour key 2 scale
    struct.pack_into('<f', data, 332, 1.0)   # Colour key 3 scale
    struct.pack_into('<f', data, 336, 0.0)   # Colour key 1 time
    struct.pack_into('<f', data, 340, 0.5)   # Colour key 2 time
    struct.pack_into('<f', data, 344, 1.0)   # Colour key 3 time
    struct.pack_into('<f', data, 348, 1.0)   # Colour Scale
    # Frame count 1 keeps a non-animated particle on its single frame.
    struct.pack_into('<I', data, 372, 1)     # Frame Count
    # The U32 flags field is what TES5 actually reads; the U8 at offset 0 is
    # marked unused in the TES5 definition.  TES4 bits 0/3/4/5 keep their
    # meaning, so the low byte carries over directly.
    struct.pack_into('<I', data, 384, flags8 & 0xFF)
    struct.pack_into('<f', data, 388, 1.0)   # Texture Scale U
    struct.pack_into('<f', data, 392, 1.0)   # Texture Scale V

    subs += pack_subrecord('DATA', bytes(data))

    return pack_record('EFSH', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)

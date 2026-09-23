"""World/cell converters: LTEX, CELL, WRLD, REFR, ACHR, ACRE, LAND, REGN, LSCR, EFSH."""

import math
import struct

from core.worldspace_names import converted_worldspace_edid

from ..base.constants import (
    MAP_MARKER_TYPE_MAP,
    MATT_MAP,
    SKYRIM_MAP_MARKER_LCRT,
    map_lock_level,
)
from ..base.locations import WORLD_NAMES
from ..base.equivalents import TES4_MARKER_FORMID_TO_SKYRIM
from .world_falloutnv import (marker_substitute, parent_use_flags,
                              tes5_world_flags, world_map_offset)
from .world_morrowind import is_tes3_source, lock_is_exit_only, tes3_refr_flags
from .vendor_stock_morrowind import stock_owner
from .items import get_base_origin_shift
from ..base.text_reader import remap_formid
from .common import (
    TES4_DEFAULT_MUSIC_ENUM,
    landscape_texture_path,
    prefix_path,
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
    pack_uint16_subrecord,
    pack_uint8_subrecord,
    music_for_enum,
    region_was_emitted,
)


#: TES4 WRLD has no DNAM; its land fallback plane sits far below the terrain.
_TES4_DEFAULT_LAND_HEIGHT = -2048.0

#: TES4 sea level: the default water plane for any worldspace with no DNAM.
_TES4_DEFAULT_WATER_HEIGHT = 0.0


# ---------------------------------------------------------------------------
# WRLD water and fallback planes
# ---------------------------------------------------------------------------
def _wrld_edid(rec: dict) -> str:
    """This WRLD's EditorID as the converted plugin names it.

    See: docs/commentary/script_convert.md#worldspace-property-rename
    """
    return converted_worldspace_edid(get_str(rec, 'EditorID'))


def _world_parent(rec: dict) -> bytes:
    """WRLD WNAM, plus PNAM only when the source authored one.

    See: docs/commentary/tes4_export_falloutnv.md#child-worldspaces
    """
    wnam = get_formid(rec, 'WNAM.Parent')
    if not wnam:
        return b''
    pnam = parent_use_flags(rec)
    subs = pack_formid_subrecord('WNAM', wnam)
    if pnam is not None:
        subs += pack_uint16_subrecord('PNAM', pnam)
    return subs


def _world_water_and_planes(rec: dict) -> bytes:
    """WRLD NAM2/NAM3 water types and the NAM4/DNAM fallback planes.

    DNAM/NAM4 are authored only by FO3/FNV. TES4 falls back to a land plane
    far below the terrain and a water plane at sea level; equalizing them
    sinks every unauthored water surface. NAM3 stays on DefaultWater always:
    a null LOD water pointer CTDs as soon as a .btr carries a WATER
    BSMultiBoundNode.
    See: docs/commentary/tes5_import_world.md#wrld-land-and-water-defaults
    """
    subs = pack_formid_subrecord(
        'NAM2', get_formid(rec, 'NAM2.Water') or 0x00000018)
    subs += pack_formid_subrecord('NAM3', 0x00000018)
    subs += pack_float_subrecord('NAM4', get_float(rec, 'NAM4.LODWaterHeight'))
    subs += pack_subrecord('DNAM', struct.pack(
        '<ff',
        get_float(rec, 'DNAM.DefaultLandHeight', _TES4_DEFAULT_LAND_HEIGHT),
        get_float(rec, 'DNAM.DefaultWaterHeight', _TES4_DEFAULT_WATER_HEIGHT)))
    return subs


# TES4 'DefaultClimate' (Oblivion.esm 0x0000015F).  The engine hardcodes this
# form as the climate for any worldspace with no CNAM — see convert_WRLD.
# Raw TES4 id: it MUST go through remap_formid before being written.
_TES4_DEFAULT_CLIMATE = 0x0000015F


#: Interior CELL FormID -> LCTN FormID; set by `base.locations`, emitted as XLCN.
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

# Every exterior grid square that actually has a CELL: {(WRLD FormID, gx, gy)}.
# Paired with _DOOR_PLACEMENT (door REFR FormID -> (WRLD FormID, x, y, z)) so
# convert_REFR can tell whether an XTEL destination lands on a real cell.
# See set_teleport_grid.
_WORLD_GRID_CELLS: set = set()
_DOOR_PLACEMENT: dict = {}

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
    in SkyrimSE.exe at RVA 0x2c7e00 — see asset_convert/lod/worldmap_clouds.py),
    which on a small converted worldspace covers many times its landmass.
    When this is set, convert_WRLD emits a bank scaled to each worldspace's own
    NAM0/NAM9 rectangle and points MODL at it.  Left None (the default, e.g.
    for unit tests) the record is written exactly as before.
    """
    global _CLOUD_BANK_ROOT
    _CLOUD_BANK_ROOT = out_root


def set_world_land_extents(extents: dict):
    """Register WRLD FormID -> real land rectangle.

    Feeds the world-map cloud bank and MNAM's map-camera rectangle.  Both must
    cover the terrain the player actually sees, and an authored MNAM can
    simply be wrong about it: NehrimWorldspace's sits 26,624 units south of
    its land's center and clips 16,384 units off the north edge.  The exterior
    cell grid is the authored ground truth.

    UNIONS into whatever is already registered rather than replacing it.  This
    runs twice per import -- once before the override pass over every cell,
    and again from _build_world_groups over just the own-hierarchy cells -- so
    replacing would let the second, NARROWER call shrink a rectangle the first
    one measured correctly.  A rectangle may only ever grow.

    Pass an empty dict to reset (tests).
    """
    if not extents:
        _WORLD_LAND_EXTENT.clear()
        return
    for fid, rect in extents.items():
        old = _WORLD_LAND_EXTENT.get(fid)
        if old is None:
            _WORLD_LAND_EXTENT[fid] = tuple(rect)
        else:
            _WORLD_LAND_EXTENT[fid] = (
                min(old[0], rect[0]), min(old[1], rect[1]),
                max(old[2], rect[2]), max(old[3], rect[3]))


def _extent_cell_rect(extent):
    """(NW.X, NW.Y, SE.X, SE.Y) cell indices for a land rectangle, or None.

    `extent` is (min_x, min_y, max_x, max_y) in world units, where the max
    edge is the FAR side of the last cell -- hence the -1.  NW is
    (min X, max Y) and SE is (max X, min Y).  Returns None when the grid
    cannot be expressed in MNAM's int16 fields, so callers fall back rather
    than wrap.
    """
    min_x, min_y, max_x, max_y = extent
    nwx = int(math.floor(min_x / 4096.0))
    sey = int(math.floor(min_y / 4096.0))
    sex = int(math.ceil(max_x / 4096.0)) - 1
    nwy = int(math.ceil(max_y / 4096.0)) - 1
    if not all(-32768 <= v <= 32767 for v in (nwx, nwy, sex, sey)):
        return None
    return nwx, nwy, sex, sey


def get_world_land_extent(fid: int):
    """The registered land rectangle for a worldspace, or None.

    Accepts either FormID space: the registry is keyed in OUTPUT space while
    export records carry the raw TES4 source id, and the two coincide only for
    a plugin that OWNS the worldspace.  A pure override (ElsweyrAnequina ->
    Tamriel, 0000003C vs 0100003C) missed an output-space-only lookup and fell
    back to the authored rectangle.
    """
    return (_WORLD_LAND_EXTENT.get(fid)
            or _WORLD_LAND_EXTENT.get(remap_formid(fid, is_own_id=True)))


def set_teleport_grid(grid_cells, door_placement: dict):
    r"""Register what convert_REFR needs to sanity-check an XTEL destination.

    Creation Kit's teleport-data init resolves the linked door and, when that
    door stands in an EXTERIOR cell, re-derives the destination cell from the
    worldspace grid using the XTEL's OWN coordinates.  It null-checks the door
    and the door's parent cell, but NOT that grid lookup: the result is passed
    straight on (`CreationKit.exe` 1.5.73 `+0x15f0893`) and the callee reads
    `[this+0x10]` — EXCEPTION_ACCESS_VIOLATION reading 0x10 the moment the
    square has no cell.  In CK that is not even a clean crash: CKPE's handler
    parks the faulting thread in `Sleep`, and the main thread then spins
    forever in `BSSpinLock::Lock`, so the editor "hangs" at 0% CPU during
    "Initializing References".

    Oblivion never validated this, because it teleports to the target door's
    own parent cell and ignores the coordinates for that purpose.  So its data
    contains pairs that simply disagree: `DAPeryiteDoorTEMPREF` stores an XTEL
    position of (2036, 1785) — grid (0,0) — while its partner
    `DAPeryiteDoorREFX` stands at (67326, 67976) — grid (16,16) — in a
    worldspace whose cells span x[4,36] y[5,26]. Nothing occupies (0,0).
    """
    _WORLD_GRID_CELLS.clear()
    _WORLD_GRID_CELLS.update(grid_cells or ())
    _DOOR_PLACEMENT.clear()
    _DOOR_PLACEMENT.update(door_placement or {})


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


def _direct_matt(rec: dict) -> int:
    """A MATT FormID the source named outright, or 0.

    A vanilla Skyrim FormID is a literal constant, so it must NOT go through
    `get_formid`, whose load-order remapping would rewrite its index byte.
    """
    raw = rec.get('HNAM.MaterialFormID')
    try:
        return int(raw, 16) if raw else 0
    except (TypeError, ValueError):
        return 0


def convert_LTEX(rec: dict, writer=None) -> tuple:
    """LTEX — needs companion TXST record in TES5.
    Returns (ltex_bytes, txst_bytes_or_None, txst_formid)."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    icon_path = get_str(rec, 'ICON')
    material = get_int(rec, 'HNAM.Material')
    matt_fid = _direct_matt(rec) or MATT_MAP.get(material, 0x00012F34)

    # Create TXST record
    txst_fid = 0
    txst_bytes = None
    if icon_path and writer:
        txst_fid = writer.derive_formid('LTEX_TXST', get_formid(rec, 'FormID'))
        txst_subs = b''
        txst_edid = f"TES4_{edid}_TXST" if edid else f"TES4_LTEX_{get_formid(rec, 'FormID'):08X}_TXST"
        txst_subs += pack_string_subrecord('EDID', txst_edid)
        txst_subs += pack_obnd()
        diffuse = landscape_texture_path(icon_path)
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


#: Vanilla XCLL for an interior that authored none. See: docs/commentary/tes5_import_world.md#every-interior-gets-an-xcll
_GENERIC_XCLL = bytes.fromhex(
    '1e1e2800000000000000000000000000000000000000000000000000'
    '00000000000000000000803f211e29001a1e26001e1f28001e1c2800'
    '0e0e12002d2d3d00000000000000803f000000000000803f00000000'
    '000000009f000000')


def _cell_flags(rec: dict) -> int:
    """TES5 CELL DATA flags: drop TES4 bits 3/6, add bit 8 to a Show Sky interior.

    See: docs/commentary/tes5_import_world.md#every-interior-gets-an-xcll
    """
    flags = get_int(rec, 'DATA.Flags') & ~0x08 & ~0x40
    if flags & 0x01 and flags & 0x80:
        flags |= 0x100
    return flags & 0xFFFF


def build_cell_xcll(rec: dict):
    """TES5 XCLL payload (92 bytes) from a TES4 CELL record, or None.

    Shared by convert_CELL and the override path.  No authored lighting:
    `_GENERIC_XCLL` for an interior, None for an exterior.

    Layout (xEdit wbDefinitionsTES5): 0 ambient, 4 directional, 8 fog near
    color, 12 fog near, 16 fog far, 20/24 dir rot XY/Z, 28 dir fade, 32 fog
    clip, 36 fog power, 40 six directional-ambient colors, 64 specular, 68
    scale, 72 fog far color, 76 fog max, 80/84 fade begin/end, 88 inherit.
    """
    if not get_str(rec, 'XCLL.AmbientR'):
        return _GENERIC_XCLL if get_int(rec, 'DATA.Flags') & 0x01 else None
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
    struct.pack_into('<f', xcll, 36, get_float(rec, 'XCLL.FogPower', 1.0))
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

    The NW/SE cell pair is what the world map clamps SCROLLING to: the engine
    builds a border polygon from it and clamps the camera into that polygon
    (SkyrimSE RVA 0x9213e0; NAM0/NAM9 is only the fallback used when all four
    cell values are zero).  See docs/commentary/tes5_import_navmesh.md.

    So the measured cell grid wins over the authored value, exactly as it does
    for the cloud bank in build_wrld_cloud_modl -- an authored MNAM routinely
    does not cover land a plugin adds (Tamriel.esp adds 99,946 cells over grid
    X -192..191, Y -129..159 while copying Oblivion's 119x106-cell rectangle
    verbatim).  The authored value is the fallback for a worldspace whose
    cells we did not measure.

    UsableDimX/Y is written 0, as all 3 Skyrim.esm WRLDs carrying MNAM do.
    """
    have_authored = bool(get_str(rec, 'MNAM.UsableDimX'))

    extent = get_world_land_extent(get_formid(rec, 'FormID'))
    cells = _extent_cell_rect(extent) if extent else None
    if cells is None:
        if not have_authored:
            return None
        cells = (get_int(rec, 'MNAM.NWCellX'), get_int(rec, 'MNAM.NWCellY'),
                 get_int(rec, 'MNAM.SECellX'), get_int(rec, 'MNAM.SECellY'))
    nwx, nwy, sex, sey = cells

    # Camera defaults from Skyrim's Tamriel worldspace
    return struct.pack('<iihhhhfff', 0, 0, nwx, nwy, sex, sey,
                       50000.0, 80000.0, 50.0)


def restamp_wrld_mnam(rec: bytes, out_fid: int) -> bytes:
    """Rewrite a converted WRLD's MNAM cell rectangle from the extent registry.

    Used on the ANCHOR path, where a plugin that adds land to a master's
    worldspace emits the MASTER's converted bytes verbatim to parent its new
    cells. Those bytes hold the master's narrow rectangle, which silently
    re-clamps the world map (see docs/commentary/tes5_import_navmesh.md).

    Only the 8 cell bytes inside an existing 28-byte MNAM are replaced, in
    place, so the record's size and every other subrecord are untouched. A
    record with no MNAM, or a compressed one, is returned unchanged.
    """
    extent = get_world_land_extent(out_fid)
    if not extent or len(rec) < 24:
        return rec
    # Record flag 0x00040000 = compressed body; leave those alone.
    if struct.unpack('<I', rec[8:12])[0] & 0x00040000:
        return rec
    size = struct.unpack('<I', rec[4:8])[0]
    body = bytearray(rec[24:24 + size])
    o = 0
    while o + 6 <= len(body):
        sub = bytes(body[o:o + 4])
        ln = struct.unpack('<H', body[o + 4:o + 6])[0]
        o += 6
        if sub == b'MNAM' and ln == 28:
            cells = _extent_cell_rect(extent)
            if cells is not None:
                body[o + 8:o + 16] = struct.pack('<hhhh', *cells)
            return bytes(rec[:24]) + bytes(body)
        o += ln
    return rec


def build_wrld_cloud_modl(rec: dict, edid: str = None):
    """This worldspace's world-map cloud-bank MODL value, or None.

    None when banks are disabled, bounds are unusable, or the vanilla source
    mesh is missing; the engine then falls back to its hardcoded bank.  Rect =
    the REAL LAND (set_world_land_extents), MNAM then NAM0/NAM9 as fallbacks,
    recentered onto it.  write=False: NAME only, since merge_cloud_bank writes
    the one mesh sized to every sibling's union.  Shared by convert_WRLD and
    the override path.
    See: docs/commentary/tes5_import_navmesh.md#cloud-bank-rect-precedence
    """
    if not _CLOUD_BANK_ROOT:
        return None
    if edid is None:
        edid = _wrld_edid(rec)
    if not edid or not get_str(rec, 'NAM0.MinX'):
        return None
    from asset_convert.lod.worldmap_clouds import (generate_cloud_bank,
                                               compute_center, framed_rect)

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
    center = compute_center(min_x, min_y, max_x, max_y)
    return generate_cloud_bank(edid, width, height, _CLOUD_BANK_ROOT,
                               center=center, land_rect=rect, write=False)


def convert_CELL(rec: dict) -> bytes:
    """Convert CELL record.

    `LTMP` is required by TES5 and always written NULL: the cell lights from
    its own XCLL, which inherits nothing. Pointing it at a converted FO3/FNV
    LGTM turned every interior pitch black. `XCIM`/`XEZN` are FO3/FNV-only.

    See: docs/commentary/tes4_export_falloutnv.md#reference-only-types
    """
    subs = b''
    edid = converted_worldspace_edid(get_str(rec, 'EditorID'))
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    subs += pack_subrecord('DATA', struct.pack('<H', _cell_flags(rec)))

    # XCLC — grid coordinates (exterior cells)
    x = get_int(rec, 'XCLC.X', None)
    if x is not None:
        y = get_int(rec, 'XCLC.Y')
        land = get_int(rec, 'XCLC.LandFlags', 0)
        subs += pack_subrecord('XCLC', struct.pack('<iiI', x, y, land))

    xcll = build_cell_xcll(rec)
    if xcll is not None:
        subs += pack_subrecord('XCLL', xcll)

    subs += pack_formid_subrecord('LTMP', 0)

    # Ownership
    xown = get_formid(rec, 'XOWN.Owner')
    if xown:
        subs += pack_formid_subrecord('XOWN', xown)

    # Water height — shared with the override path
    xclw_payload = build_cell_xclw(rec)
    if xclw_payload is not None:
        subs += pack_subrecord('XCLW', xclw_payload)

    xnam = get_str(rec, 'XNAM.WaterNoiseTexture')
    if xnam:
        subs += pack_string_subrecord('XNAM', prefix_path(xnam))

    subs += _cell_pointers(rec)

    return pack_record('CELL', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_WRLD(rec: dict) -> bytes:
    subs = b''
    edid = _wrld_edid(rec)
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

    # XLCN — the worldspace's own Location.  THIS is how an exterior cell with
    # no Location of its own is named: the engine walks up to the worldspace
    # rather than reading a per-cell XLCN, so one subrecord here names every
    # cell in the world and "Wilderness" never appears.
    #
    # It is also the only way to do it without tripping the CK's location
    # validator.  A cell's OWN XLCN is checked against the target's LCEC cell
    # list ("Cell (x, y) in world 'W' is not in exterior cell data" per cell,
    # 26,124 of them), but a WRLD-level XLCN is exempt — vanilla Blackreach
    # points at BlackreachLocation, which carries NO LCEC at all, and is
    # silent.  32 of Skyrim.esm's 37 worldspaces name themselves this way,
    # while only 982 of its 16,978 exterior cells carry XLCN individually.
    world_lctn = _WORLD_LOCATION.get(get_formid(rec, 'FormID'))
    if world_lctn:
        subs += pack_formid_subrecord('XLCN', world_lctn)

    subs += _world_parent(rec)
    subs += pack_formid_subrecord('CNAM', _world_climate(rec))

    subs += _world_water_and_planes(rec)

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

    subs += pack_subrecord('ONAM', struct.pack('<ffff', *world_map_offset(rec)))

    # NAMA — Distant LOD multiplier
    subs += pack_float_subrecord('NAMA', 1.0)

    subs += pack_uint8_subrecord('DATA', tes5_world_flags(get_int(rec, 'DATA.Flags')))

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

    # ZNAM — the worldspace's default music.  xEdit places it immediately after
    # the world object bounds (wbWorldObjectBounds, ZNAM, NNAM, XNAM...).
    # TES4 stores the same 3-value enum here that CELL uses (SNAM), so it
    # resolves through the same category table: 23 Dungeon + 16 Public in
    # Oblivion.esm, 18 + 5 in Nehrim.esm.  Vanilla sets ZNAM on 27 worldspaces.
    # 🛑 A worldspace with NO authored SNAM still needs music.  TES4 leaves the
    # field off entirely for the main open worlds -- Tamriel, SEWorld,
    # NehrimWorldspace and Arktwend all omit it (45 of Oblivion's 84
    # worldspaces, 11 of Nehrim's 34) -- because Oblivion's engine defaults an
    # unauthored exterior to the Explore set.  UESP, Oblivion:Music: "Explore:
    # All of these tracks are randomly played one after another while the player
    # is in the countryside (outside cities and dungeons)."
    #
    # Skyrim has no such default: with no ZNAM and no XCMO the world is SILENT,
    # which is exactly what "city music works, countryside has none" looks like
    # -- cities are the authored 1/Public cells, and the 33,241 of 33,560
    # Oblivion exterior cells carrying no XCMT at all fall through to here.
    snam = get_int(rec, 'SNAM.Music', None)
    musc = music_for_enum(snam if snam is not None
                          else TES4_DEFAULT_MUSIC_ENUM)
    if musc:
        subs += pack_formid_subrecord('ZNAM', musc)

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
    square the ref stands in.

    No worldspace-wide fallback, for the same reason convert_CELL has none: a
    ref claiming a location whose LCEC does not list the ref's cell fails the
    CK's location validation ("Special Ref 'X' ... is not in the Special Ref
    data", and the persistent-ref equivalent).  Every one of the 489 markers
    still warning after the LCPR/LCSR arrays were added sat in a cell outside
    its location's LCEC, reached through this fallback.
    """
    parent_cell = get_formid(rec, 'ParentCELL')
    lctn_fid = _CELL_LOCATION.get(parent_cell)
    if lctn_fid:
        return lctn_fid
    world_fid = get_formid(rec, 'ParentWRLD')
    if world_fid:
        gx = _ref_grid(get_float(rec, 'PosX'))
        gy = _ref_grid(get_float(rec, 'PosY'))
        return _GRID_LOCATION.get((world_fid, gx, gy)) or 0
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


# -------------------------------------------------------------------------
# REFR base object
# -------------------------------------------------------------------------
def _refr_base_formid(rec: dict, name_raw: int) -> int:
    """The REFR's NAME target, substituting Skyrim's invisible markers.

    A substituted marker is already a Skyrim.esm FormID, so it takes no
    master offset.
    See: docs/commentary/tes4_export_falloutnv.md#marker-base-objects
    """
    marker = TES4_MARKER_FORMID_TO_SKYRIM.get(name_raw)
    if marker is None:
        marker = marker_substitute(name_raw)
    return marker if marker is not None else get_formid(rec, 'NAME')


def _refr_xtel(rec: dict) -> bytes:
    """The XTEL teleport-door subrecord, or b'' when the ref has no XTEL.

    TES5 XTEL is 32 bytes: Door(4) + Pos(12) + Rot(12) + Flags(4), flags
    always 0 for converted doors.  An exterior destination whose grid square
    holds no cell crashes the CK, so it falls back to the target door's own
    placement -- where Oblivion put the player anyway.

    See: docs/commentary/tes5_import_world.md#teleport-doors-by-worldspace
    """
    xtel_door = get_formid(rec, 'XTEL.Door')
    if not xtel_door:
        return b''
    px = get_float(rec, 'XTEL.PosX')
    py = get_float(rec, 'XTEL.PosY')
    pz = get_float(rec, 'XTEL.PosZ')
    rx = _safe_angle(get_float(rec, 'XTEL.RotX'))
    ry = _safe_angle(get_float(rec, 'XTEL.RotY'))
    rz = _safe_angle(get_float(rec, 'XTEL.RotZ'))
    dest = _DOOR_PLACEMENT.get(xtel_door)
    if dest:
        dw, dx, dy, dz = dest
        if dw and (dw, _ref_grid(px), _ref_grid(py)) not in _WORLD_GRID_CELLS:
            px, py, pz = dx, dy, dz
    return pack_subrecord('XTEL', struct.pack(
        '<IffffffI', xtel_door, px, py, pz, rx, ry, rz, 0))


def _refr_xloc(rec: dict):
    """The XLOC lock subrecord and whether this is a keyless barrier door.

    Returns (bytes, barrier_door).  XLOC is 20 bytes in TES5 and the lock is
    transferred faithfully -- TES4 level 100 becomes Requires Key (255).  AI
    passes a locked barrier door by OWNERSHIP, never a weakened lock.  A TES3
    lock sealing only the way out is dropped: TES5 locks the doorway.

    See: docs/commentary/tes5_import_actors.md#barrier-door-ownership
    See: docs/commentary/tes5_import_world.md#tes3-exit-only-door-locks
    """
    lock_level = get_int(rec, 'XLOC.Level', -1)
    if lock_level < 0:
        return b'', False
    if is_tes3_source() and lock_is_exit_only(rec):
        return b'', False
    barrier_door = False
    lock_key = get_formid(rec, 'XLOC.Key')
    lock_flags = get_int(rec, 'XLOC.Flags')
    tes5_level = map_lock_level(lock_level, leveled=bool(lock_flags & 0x4))
    if tes5_level == 255 and not lock_key:
        from ..base.object_scripts import base_is_consume_door
        barrier_door = base_is_consume_door(rec.get('NAME', ''))
    return pack_subrecord('XLOC', struct.pack(
        '<BxxxIBxxx8x', tes5_level, lock_key, lock_flags)), barrier_door


def _refr_map_marker(rec: dict) -> bytes:
    """The map-marker block: XMRK, FNAM, optional FULL, TNAM and XLRT.

    XLRT binds the reference to its Location as that Location's map marker;
    without it the engine cannot tie the marker to the Location the player
    discovers.  396/397 vanilla map markers carry MapMarkerRefType.

    See: docs/commentary/tes5_import_world.md#exclusive-lcec-cell-ownership
    """
    subs = pack_subrecord('XMRK', b'')
    subs += pack_uint8_subrecord('FNAM', map_marker_flags(rec))
    marker_full = get_str(rec, 'MapMarker.FULL')
    if marker_full:
        subs += pack_string_subrecord('FULL', marker_full)
    marker_type = get_int(rec, 'MapMarker.Type')
    subs += pack_subrecord('TNAM', struct.pack(
        '<BB', MAP_MARKER_TYPE_MAP.get(marker_type, 0), 0))
    return subs + pack_formid_subrecord('XLRT', SKYRIM_MAP_MARKER_LCRT)


def shifted_position(rec: dict, scale) -> tuple:
    """`(x, y, z)` for a placed REFR, with the furniture-origin shift applied.

    EVERY consumer of a REFR's world position must use this, not raw PosZ.
    The shift runs along the model's LOCAL Z, so under rotation it moves all
    three axes.

    See: docs/commentary/asset_convert_nif.md#furniture-shift-third-consumer
    """
    px = get_float(rec, 'PosX')
    py = get_float(rec, 'PosY')
    pz = get_float(rec, 'PosZ')
    shift = get_base_origin_shift(rec.get('NAME', '') or '')
    if not shift:
        return px, py, pz
    rx = _safe_angle(get_float(rec, 'RotX'))
    ry = _safe_angle(get_float(rec, 'RotY'))
    rz = _safe_angle(get_float(rec, 'RotZ'))
    s = scale if scale and scale != 1.0 else 1.0
    if abs(rx) < 1e-4 and abs(ry) < 1e-4:
        return px, py, pz - shift * s
    wx = (math.cos(rx) * math.sin(ry) * math.cos(rz)
          + math.sin(rx) * math.sin(rz))
    wy = (math.cos(rx) * math.sin(ry) * math.sin(rz)
          - math.sin(rx) * math.cos(rz))
    wz = math.cos(rx) * math.cos(ry)
    return px - shift * s * wx, py - shift * s * wy, pz - shift * s * wz


def _refr_data(rec: dict, scale) -> bytes:
    """The DATA position/rotation subrecord, with furniture-origin shift."""
    px, py, pz = shifted_position(rec, scale)
    return pack_subrecord('DATA', struct.pack(
        '<ffffff', px, py, pz,
        _safe_angle(get_float(rec, 'RotX')),
        _safe_angle(get_float(rec, 'RotY')),
        _safe_angle(get_float(rec, 'RotZ'))))


def _refr_head(rec: dict) -> bytes:
    """EDID, NAME and the XPRM primitive of a REFR.

    An invisible-marker base is substituted with its Skyrim.esm equivalent so
    the ref points into index 0.  Oblivion.esm ships 6 refs on the MapMarker
    base with no XMRK data (campsite/battle position markers); the map code
    treats every 0x10-based ref as a map marker, so they ground to XMarker.
    FO3/FNV trigger volumes are XPRM primitives, byte-identical to TES5's, so
    the raw subrecord is copied through.
    See: docs/commentary/tes4_export_falloutnv.md#trigger-primitives
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    name_raw = int(rec.get('NAME', '0') or '0', 16)
    name_fid = _refr_base_formid(rec, name_raw)
    if name_raw == 0x10 and get_str(rec, 'MapMarker') != '1':
        name_fid = 0x0000003B
    if name_fid:
        subs += pack_formid_subrecord('NAME', name_fid)
    primitive = get_str(rec, 'XPRM.Raw')
    if primitive:
        subs += pack_subrecord('XPRM', bytes.fromhex(primitive))
    return subs


def convert_REFR(rec: dict) -> bytes:
    """REFR — placed object reference.

    TES5 order (from wbDefinitionsTES5.pas):
    EDID VMAD NAME XMBO XPRM ... XTEL XLOC XEZN ... XOWN XESP XLKR
    ... XSCL ... XMRK/FNAM/FULL/TNAM ... XLRT ... DATA

    A keyless barrier door with no authored owner is owned to the
    plugin-origin faction; TES4 XACT/ONAM is deliberately not transferred.

    See: docs/commentary/tes5_import_actors.md#barrier-door-ownership

    The `object_scripts` import stays INSIDE the body.
    See: docs/reference/tes5_import_architecture.md#object-scripts-import-is-deferred
    """
    subs = _refr_head(rec)
    subs += _refr_xtel(rec)

    lock_bytes, barrier_door = _refr_xloc(rec)
    subs += lock_bytes

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

    from ..base.object_scripts import base_uses_parent_ref
    if xesp_ref and base_uses_parent_ref(rec.get('NAME', '')):
        subs += pack_subrecord('XLKR', struct.pack('<II', 0, xesp_ref))

    xown = (stock_owner(get_formid(rec, 'FormID'))
            or get_formid(rec, 'XOWN.Owner'))
    if not xown and barrier_door:
        from .actor_common import get_origin_faction_fid
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
        subs += _refr_map_marker(rec)

    # XNDP — Navmesh Door Link: the navmesh triangle this door stands on.
    # Emitted last, immediately before DATA, which is where all 1,706 vanilla
    # XNDP records sit (after XLOC/XOWN/XLRT/XSCL).  Struct: Navmesh FormID(4)
    # + Triangle s16 + 2 unused.
    door_link = _DOOR_NAVMESH_LINK.get(get_formid(rec, 'FormID'))
    if door_link:
        navm_fid, tri_index = door_link
        subs += pack_subrecord('XNDP', struct.pack('<Ihxx', navm_fid,
                                                   tri_index))

    subs += _refr_data(rec, scale)

    flags = get_int(rec, 'RecordFlags')
    if is_map_marker:
        # Map markers must be persistent references — the map/fast-travel system
        # resolves them outside the loaded cell.  All 397 vanilla markers set it.
        flags |= REFR_PERSISTENT_FLAG
    return pack_record('REFR', get_formid(rec, 'FormID'), tes3_refr_flags(flags), subs)


def convert_ACHR(rec: dict) -> bytes:
    """ACHR — placed NPC reference. TES4 ACRE also maps here.

    The `object_scripts` import stays INSIDE the body.
    See: docs/reference/tes5_import_architecture.md#object-scripts-import-is-deferred
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    from ..base.object_scripts import get_object_vmad
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
    """LAND record — landscape vertex data.

    DATA flags pass through VERBATIM: a LAND clearing bit 0 is the author
    deleting that cell's terrain, and is vanilla-legal.  Do NOT normalize.
    See: docs/commentary/tes5_import_world.md#land-data-flags-verbatim
    """
    subs = b''

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


# ---------------------------------------------------------------------------
# WRLD climate
# ---------------------------------------------------------------------------
def _world_climate(rec: dict) -> int:
    """The CNAM climate FormID: authored, vanilla-verbatim, or TES4's default.

    `CNAM.Vanilla` is read WITHOUT load-order remapping, so an export can name
    a Skyrim.esm climate for a source game that has no DefaultClimate to
    inherit. SNAM is omitted; it references a TES4 record we skip.
    See: docs/commentary/tes5_import_landscape.md#wrld-climate
    """
    vanilla = rec.get('CNAM.Vanilla')
    if vanilla:
        try:
            return int(vanilla, 16)
        except (ValueError, TypeError):
            pass
    return (get_formid(rec, 'CNAM.Climate')
            or remap_formid(_TES4_DEFAULT_CLIMATE))


# ---------------------------------------------------------------------------
# CELL outbound pointers
# ---------------------------------------------------------------------------
def _cell_regions(rec: dict) -> bytes:
    """CELL XCLR: the region list, filtered to regions that emitted, sorted.

    See: docs/commentary/tes5_import_landscape.md#cell-xclr-regions
    """
    region_fids = []
    i = 0
    while f'Region[{i}]' in rec:
        rfid = get_formid(rec, f'Region[{i}]')
        if region_was_emitted(rfid):
            region_fids.append(rfid)
        i += 1
    if not region_fids:
        return b''
    return pack_subrecord(
        'XCLR', struct.pack(f'<{len(region_fids)}I', *sorted(region_fids)))


def _cell_location(rec: dict) -> bytes:
    """CELL XLCN: the cell's OWN Location claim, else its grid's.

    An interior has no grid, so only the FormID claim can match.

    See: docs/commentary/tes5_import_landscape.md#cell-xlcn-lcec
    """
    lctn_fid = _CELL_LOCATION.get(get_formid(rec, 'FormID'))
    x = get_int(rec, 'XCLC.X', None)
    if not lctn_fid and x is not None:
        lctn_fid = _GRID_LOCATION.get((get_formid(rec, 'ParentWRLD'), x,
                                       get_int(rec, 'XCLC.Y')))
    return pack_formid_subrecord('XLCN', lctn_fid) if lctn_fid else b''


def _cell_music(rec: dict) -> bytes:
    """CELL XCMO: an FO3/FNV MUSC, else TES4's 3-value XCMT enum resolved.

    An interior with no authored XCMT takes the engine default; exteriors are
    left to inherit the worldspace ZNAM.

    See: docs/commentary/tes5_import_landscape.md#cell-water-and-music
    """
    xcmo = get_formid(rec, 'XCMO.Music')
    if xcmo:
        return pack_formid_subrecord('XCMO', xcmo)
    xcmt = get_int(rec, 'XCMT.MusicType', None)
    is_exterior = get_int(rec, 'XCLC.X', None) is not None
    if xcmt is None and not is_exterior:
        xcmt = TES4_DEFAULT_MUSIC_ENUM
    musc = music_for_enum(xcmt) if xcmt is not None else None
    return pack_formid_subrecord('XCMO', musc) if musc else b''


def _cell_pointers(rec: dict) -> bytes:
    """Every CELL subrecord naming another record, in xEdit order.

    XEZN exists only in FO3/FNV sources; XCWT overrides the worldspace NAM2.
    XCIM is deliberately NOT written: TES5 HNAM has no FNV source, so the
    imagespace HDR block is approximated and darkened every interior.
    XCCM names the region a "Show Sky" interior takes its sky and weather
    from, and follows the music exactly as vanilla Skyrim writes it.

    See: docs/commentary/tes5_import_landscape.md#cell-water-and-music
    """
    subs = _cell_regions(rec)
    subs += _cell_location(rec)
    xezn = get_formid(rec, 'XEZN.EncounterZone')
    if xezn:
        subs += pack_formid_subrecord('XEZN', xezn)
    xcwt = get_formid(rec, 'XCWT.Water')
    if xcwt:
        subs += pack_formid_subrecord('XCWT', xcwt)
    subs += _cell_music(rec)
    xccm = get_formid(rec, 'XCCM.Climate')
    if xccm and region_was_emitted(xccm):
        subs += pack_formid_subrecord('XCCM', xccm)
    return subs


# ---------------------------------------------------------------------------
# Effect shader texture substitution
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
    up to and including the three color keys sits at an identical offset in
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
        return p if borrowed else prefix_path(p)

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
    #
    # OFFSETS ARE FROM THE xEdit TES5 EFSH STRUCT, verified field-by-field
    # against 152 real Skyrim.esm records.  Every field from 260 up used to sit
    # 4 bytes too high, which put float 1.0 (0x3F800000) into 'Ambient Sound'
    # at 308; the CK read its low 3 bytes as an object id and reported
    # "Could not find shader effect sound (01800000)" on all 102 records.
    # Holes - End Val: 0.0 on 143 of 152 vanilla records.
    struct.pack_into('<f', data, 260, 0.0)   # Holes - End Val
    # Edge width in alpha units + its color mirror the edge block above, which
    # is what vanilla shaders carrying an edge effect do.
    struct.pack_into('<f', data, 264, get_float(rec, 'DATA.EdgeEffectWidth', 1.0))
    data[268] = get_int(rec, 'DATA.EdgeColorR') & 0xFF
    data[269] = get_int(rec, 'DATA.EdgeColorG') & 0xFF
    data[270] = get_int(rec, 'DATA.EdgeColorB') & 0xFF
    # 272 Explosion Wind Speed stays 0.
    struct.pack_into('<I', data, 276, 1)     # Texture Count U
    struct.pack_into('<I', data, 280, 1)     # Texture Count V
    struct.pack_into('<f', data, 284, 1.0)   # Addon Models - Fade In Time
    struct.pack_into('<f', data, 288, 1.0)   # Addon Models - Fade Out Time
    struct.pack_into('<f', data, 292, 1.0)   # Addon Models - Scale Start
    struct.pack_into('<f', data, 296, 1.0)   # Addon Models - Scale End
    struct.pack_into('<f', data, 300, 1.0)   # Addon Models - Scale In Time
    struct.pack_into('<f', data, 304, 1.0)   # Addon Models - Scale Out Time
    # 308 Ambient Sound (SNDR FormID): TES4 EFSH has no sound field, and 95 of
    # 152 vanilla records leave it null.  Must stay 0 — anything else is read
    # as an object id.
    # Fill color keys 2 and 3: TES4 has one fill color, so all three keys
    # carry it and the membrane holds a steady color instead of fading to
    # black across the key ramp.
    put_rgb(312, 'DATA.FillColor')
    put_rgb(316, 'DATA.FillColor')
    struct.pack_into('<f', data, 320, 1.0)   # Color key 1 scale
    struct.pack_into('<f', data, 324, 1.0)   # Color key 2 scale
    struct.pack_into('<f', data, 328, 1.0)   # Color key 3 scale
    struct.pack_into('<f', data, 332, 0.0)   # Color key 1 time
    struct.pack_into('<f', data, 336, 0.5)   # Color key 2 time
    struct.pack_into('<f', data, 340, 1.0)   # Color key 3 time
    struct.pack_into('<f', data, 344, 1.0)   # Color Scale
    # Frame Count 0 = not a frame-animated texture, which is what 108 of 152
    # vanilla records carry; a TES4 shader has no frame data to convert.
    struct.pack_into('<I', data, 376, 0)     # Frame Count
    # The U32 flags field is what TES5 actually reads; the U8 at offset 0 is
    # marked unused in the TES5 definition.  TES4 bits 0/3/4/5 keep their
    # meaning, so the low byte carries over directly.
    struct.pack_into('<I', data, 384, flags8 & 0xFF)
    struct.pack_into('<f', data, 388, 1.0)   # Texture Scale U
    struct.pack_into('<f', data, 392, 1.0)   # Texture Scale V

    subs += pack_subrecord('DATA', bytes(data))

    return pack_record('EFSH', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)

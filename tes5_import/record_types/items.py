"""Item/object converters: STAT, ACTI, MISC, KEYM, DOOR, FLOR, FURN, GRAS, TREE, LIGH, SLGM, ANIO, CONT."""

from asset_convert.game_paths import current_namespace
import os
import struct

from output_layout import assets_for

from ..base.constants import LOD_SIZE_THRESHOLD
from ..base.mesh_bounds import get_mesh_physics_flags
from .common import (
    VENDOR_KYWD,
    _common_header_subs,
    prefix_path,
    _resolve_obnd,
    _simple_object,
    get_float,
    get_formid,
    get_int,
    get_str,
    pack_float_subrecord,
    pack_formid_subrecord,
    pack_keywords,
    pack_obnd,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint8_subrecord,
    pack_uint32_subrecord,
)


def convert_STAT(rec: dict) -> bytes:
    """Convert STAT record, deriving LOD/world-map flags from mesh bounding box size.

    A STAT whose converted mesh is a constrained dynamic havok island
    (swinging chains, hanging cages) is written as MSTT instead: Skyrim never
    simulates constrained bodies on a STAT reference, so PrisonCellChains01
    hung completely rigid.  Vanilla routes ALL such content through MSTT
    (every swinging inn sign, e.g. SignBraidwoodInn01, MSTT DATA=0) or ACTI
    (TrapBoneAlarmHavok01).  The FormID is unchanged, so placed REFRs keep
    resolving.

    Do NOT extend this to "the mesh has an animation graph".  That was tried on
    2026-08-18 and reverted: promoting every BGED-bearing STAT moved 107
    Oblivion bases / 4,568 placed refs and crashed the game on save load with a
    null TESObjectREFR in the ExtraPromotedRef / QueuedPromoteQuestTask path
    (SKChamberSecretDoor, NightMotherBaseRef).

    The premise was wrong anyway: 94 vanilla STATs DO carry a BGED, including
    self-animating scenery (WRJovaskrBanner02 -> IdleRandomized.hkx,
    PowShrine01, SFarmhouseMill).  A STAT can host an animation graph, so the
    record type is not what stops an animation from playing.
    """
    flags = get_int(rec, 'RecordFlags')
    # Resolve OBND from converted mesh bounds (or type default as fallback).
    bounds = _resolve_obnd(rec, 'STAT')
    x1, y1, z1, x2, y2, z2 = bounds
    max_dim = max(x2 - x1, y2 - y1, z2 - z1)
    if max_dim >= LOD_SIZE_THRESHOLD:
        flags |= 0x8000       # Has Distant LOD — SSELodGen will build LOD for this object
    subs = _common_header_subs(rec, need_full=False, obnd_override=bounds)
    path = get_str(rec, 'Model.MODL')
    if path:
        subs += pack_string_subrecord('MODL', prefix_path(path))
        key = prefix_path(path).lower().replace('\\', '/')
        if get_mesh_physics_flags(key) & 1:
            # MSTT layout (xEdit + Skyrim.esm): EDID OBND [FULL] MODL DATA
            # [SNAM]; DATA is a REQUIRED u8 — 0 on the swinging signs.
            subs += pack_uint8_subrecord('DATA', 0)
            return pack_record('MSTT', get_formid(rec, 'FormID'), flags, subs)
    return pack_record('STAT', get_formid(rec, 'FormID'), flags, subs)


def convert_ACTI(rec: dict) -> bytes:
    # SNAM is the activator's looping sound (the Dwemer steam machines).  TES5
    # wants the descriptor, not the SOUN — the id here is a placeholder that
    # patch_sound_descriptor_slots resolves in Phase 3 (see _SNDR_SLOTS).
    extra = b''
    snam_fid = get_formid(rec, 'SNAM')
    if snam_fid:
        extra += pack_formid_subrecord('SNAM', snam_fid)
    return _simple_object(rec, 'ACTI', extra_subs=extra)


def convert_MISC(rec: dict) -> bytes:
    extra = b''
    # KSIZ/KWDA before DATA per TES5 MISC order (vendor sell-list filter)
    extra += pack_keywords([VENDOR_KYWD['Clutter']])
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    extra += pack_subrecord('DATA', struct.pack('<If', value, weight))
    return _simple_object(rec, 'MISC', extra_subs=extra)


def convert_KEYM(rec: dict) -> bytes:
    extra = b''
    extra += pack_keywords([VENDOR_KYWD['Key']])
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    extra += pack_subrecord('DATA', struct.pack('<If', value, weight))
    return _simple_object(rec, 'KEYM', extra_subs=extra)


#: MESH-authored door sounds: path -> {'open'/'close'/'loop': SOUN FormID}.
_DOOR_MODEL_SOUNDS: dict = {}


def _door_model_key(modl: str) -> str:
    return modl.lower().replace('\\', '/').lstrip('/')


def load_door_model_sounds(meshes_dir, by_type) -> int:
    """Resolve every DOOR model's authored `sound:` text keys to SOUN FormIDs.

    meshes_dir: <export_dir>/meshes (source Oblivion NIFs from BSA extraction).

    Oblivion doors take their audio from the record's SNAM/ANAM OR from
    `sound: <SOUN EditorID>` keys on the model's Open/Close sequences; Skyrim
    has only the record channel, so the mesh-authored names have to be lifted
    onto it or those doors convert silent (see asset_convert.audio.door_sounds).

    The EditorID is resolved against this plugin's own SOUN records, so an
    unknown name simply yields no sound rather than a dangling reference.
    Returns the number of models that contributed a sound.
    """
    import os
    _DOOR_MODEL_SOUNDS.clear()
    doors = by_type.get('DOOR', [])
    if not doors:
        return 0
    try:
        from asset_convert.audio.door_sounds import scan_door_models
    except ImportError as exc:
        print(f"  Door sounds: asset_convert unavailable ({exc}), skipping")
        return 0
    if not os.path.isdir(meshes_dir):
        print(f"  Door sounds: meshes dir not found ({meshes_dir}), skipping")
        return 0

    # Only doors that need the fallback are worth parsing: a record naming its
    # own open AND close sound already has everything TES5 can express.
    wanted = set()
    for rec in doors:
        modl = get_str(rec, 'Model.MODL')
        if not modl:
            continue
        if get_formid(rec, 'SNAM.Open') and get_formid(rec, 'ANAM.Close'):
            continue
        wanted.add(_door_model_key(modl))
    if not wanted:
        return 0

    found, errors = scan_door_models(meshes_dir, wanted)
    for key, err in errors:
        print(f"  Door sounds: failed to read {key}: {err}")

    soun_by_edid = {}
    for rec in by_type.get('SOUN', []):
        edid = get_str(rec, 'EditorID')
        if edid:
            soun_by_edid[edid.lower()] = get_formid(rec, 'FormID')

    resolved = 0
    unknown = set()
    for key, slots in found.items():
        mapped = {}
        for slot, edid in slots.items():
            fid = soun_by_edid.get(edid.lower())
            if fid:
                mapped[slot] = fid
            else:
                unknown.add(edid)
        if mapped:
            _DOOR_MODEL_SOUNDS[key] = mapped
            resolved += 1
    if unknown:
        print(f"  Door sounds: {len(unknown)} mesh sound name(s) match no SOUN "
              f"(e.g. {sorted(unknown)[0]})")
    print(f"  Door sounds: {resolved} door model(s) supply open/close sound "
          f"from the mesh")
    return resolved


def _door_model_sounds(rec: dict) -> dict:
    """{slot: TES4 SOUN FormID} authored in this DOOR's model (may be empty)."""
    modl = get_str(rec, 'Model.MODL')
    if not modl:
        return {}
    return _DOOR_MODEL_SOUNDS.get(_door_model_key(modl), {})


# TES5 sound slots that must hold a sound DESCRIPTOR (SNDR), and never the SOUN
# a TES4 record names there.  Read straight off xEdit's TES5 definitions, each
# of which is a `wbFormIDCk(<slot>, ..., [SNDR])`:
#
#   ACTI  SNAM 'Sound - Looping'   VNAM 'Sound - Activation'
#   CONT  SNAM 'Sound - Open'      QNAM 'Sound - Close'
#   DOOR  SNAM 'Sound - Open'      ANAM 'Sound - Close'   BNAM 'Sound - Loop'
#   LIGH  SNAM 'Sound'
#
# DOOR is confirmed against a real dump as well as the definition: Skyrim.esm's
# WRDragonSideDoor01 has SNAM 0005AFC9, the SNDR DRSWoodImperialDouble01OpenSD.
# LIGH's SNAM is a light's looping ambience — the crackle on every torch,
# sconce and brazier — so although vanilla sounds only 3 lights, the slot is
# live on hundreds of placed refs in a converted interior.
#
# MSTT and TACT also type SNAM as [SNDR], but neither is listed: no MSTT or
# TACT we write can ever hold a placeholder, so an entry would only ever scan
# and never patch.  MSTT exists solely as convert_STAT's havok retype, and TES4
# STAT has no sound field at all (0 SNAM keys across Oblivion's 6,014 and
# Nehrim's 7,205 STATs); TACT is synthesized only by speaker_activators, which
# writes VNAM and never SNAM.  Add one back only alongside a converter that
# actually emits the slot.
#
# 🛑 VNAM IS NOT A SOUND SLOT ON EVERY RECORD.  It is [SNDR] on ACTI
# ('Sound - Activation') but [VTYP] on TACT ('Voice Type') — xEdit
# wbDefinitionsTES5.pas 3324 vs 3348.  Listing TACT's VNAM here would rewrite
# every speaker activator's voice type into a sound descriptor and mute the
# speak-as lines.
#
# VERIFIED: the slot must hold an SNDR.  All 594 populated slots of these types
# in Skyrim.esm are SNDRs and not one is a SOUN (ACTI 52, CONT 272, DOOR 175,
# LIGH 3, plus MSTT 92), which is why a SOUN id here cannot be right whatever
# the engine does with it.  Ours is doubly wrong because our SOUNs are not
# self-describing the way a legacy vanilla SOUN is — convert_SOUN emits
# `EDID + OBND + SDSC` only, with every byte of audio data on the companion
# SNDR — so a slot left pointing at one names a record with no audio payload.
#
# SUSPECTED, NOT CONFIRMED: that this also CRASHES the audio thread (the engine
# taking the id at face value into the audio manager's emitter table and
# BSAudioManagerThread faulting on `mov ecx, [r8+0x48]`).  That story is
# recorded in docs/reference/record_mapping.md but was never reproduced, and
# the crash it was written for was later traced elsewhere.  Do not cite it as
# the reason for this table — the vanilla census above is the reason.
_SNDR_SLOTS = {
    'ACTI': (b'SNAM', b'VNAM'),
    'CONT': (b'SNAM', b'QNAM'),
    'DOOR': (b'SNAM', b'ANAM', b'BNAM'),
    'LIGH': (b'SNAM',),
}


def patch_sound_descriptor_slots(writer, rectype, own_soun_ids=None,
                                 master_sndr=None) -> int:
    """Rewrite one record type's sound slots from TES4 SOUN ids to SNDRs.

    These records are written in Phase 1/2, long before Phase 3 mints the
    descriptors, so the converters store the TES4 SOUN id as a placeholder and
    this resolves it — the same approach actors.patch_actor_sounds uses for
    CSDI.  Allocating the descriptor id at conversion time instead would shift
    every later generated FormID.

    *own_soun_ids* is the low-24 id set of the SOUN records THIS plugin
    converts.  Anything outside it is master-owned, and is handed to
    *master_sndr*, which resolves a master's TES4 SOUN id to the SNDR that
    master's own conversion produced.  A slot that neither answers is left
    exactly as it is: on an override build the group also holds the master's
    already-converted records, whose slots hold real SNDR ids that must not be
    rewritten or dropped.

    Without the master resolver these slots die silently — the master-blindness
    failure mode, where only the current plugin's export is indexed.  It is the
    common case, not an edge case: Morrowind_ob's containers and torches point
    at Oblivion.esm's sounds, which it never overrides.

    A slot that IS one of this run's placeholders but whose SOUN produced no
    descriptor is DROPPED, rather than left pointing at a record of the wrong
    type.
    """
    from .sound import sndr_map
    mapping = sndr_map()
    own = own_soun_ids if own_soun_ids is not None else set(mapping)
    slots = _SNDR_SLOTS[rectype]
    records = writer._top_groups.get(rectype) or []
    patched = 0
    for i, blob in enumerate(records):
        if not any(sig in blob for sig in slots):
            continue
        out = bytearray(blob[:24])
        pos = 24
        changed = False
        while pos + 6 <= len(blob):
            sig = blob[pos:pos + 4]
            size = struct.unpack_from('<H', blob, pos + 4)[0]
            chunk = blob[pos:pos + 6 + size]
            pos += 6 + size
            if sig in slots and size == 4:
                soun = struct.unpack_from('<I', chunk, 6)[0]
                if (soun & 0x00FFFFFF) in own:
                    sndr = mapping.get(soun & 0x00FFFFFF, 0)
                else:
                    sndr = master_sndr(soun) if master_sndr else 0
                    if not sndr:
                        out += chunk      # not ours to resolve — leave alone
                        continue
                if sndr != soun:
                    changed = True
                if sndr:
                    out += chunk[:6] + struct.pack('<I', sndr)
                continue           # no descriptor -> drop the slot entirely
            out += chunk
        if not changed:
            continue
        struct.pack_into('<I', out, 4, len(out) - 24)   # data size
        records[i] = bytes(out)
        patched += 1
    return patched


def convert_DOOR(rec: dict) -> bytes:
    extra = b''
    # TES5 DOOR has SNAM (open) / ANAM (close) / BNAM (loop), and xEdit plus
    # every one of the 90 sounded vanilla Skyrim DOORs agree that all three
    # point at an SNDR — NOT at the SOUN the TES4 record names.  The
    # descriptors do not exist yet (Phase 3 mints them), so the TES4 SOUN id
    # goes in as a placeholder and patch_sound_descriptor_slots() resolves it,
    # exactly as actors do for CSDI.  Allocating the descriptor id here
    # instead would shift every other generated FormID (see
    # dialog_misc._SNDR_FOR_SOUN).
    #
    # A door whose sound lives only in its MESH (`sound: X` text keys on the
    # Open/Close sequences — Oblivion honours both channels, Skyrim only the
    # record) falls back to the model's authored names.  Without this the
    # gate doors, whose TES4 records carry no SNAM/ANAM at all, are silent.
    mesh = _door_model_sounds(rec)
    snam = get_formid(rec, 'SNAM.Open') or mesh.get('open', 0)
    if snam:
        extra += pack_formid_subrecord('SNAM', snam)
    anam = get_formid(rec, 'ANAM.Close') or mesh.get('close', 0)
    if anam:
        extra += pack_formid_subrecord('ANAM', anam)
    bnam = get_formid(rec, 'BNAM.Loop') or mesh.get('loop', 0)
    if bnam:
        extra += pack_formid_subrecord('BNAM', bnam)
    fnam = get_int(rec, 'FNAM.Flags', -1)
    if fnam >= 0:
        # TES4 bit 0 = "Oblivion gate" — no TES5 equivalent, clear it.
        # TES4 bits 1-3 (Automatic, Hidden, Minimal Use) map directly to TES5 bits 1-3.
        fnam = fnam & ~0x01
        extra += pack_uint8_subrecord('FNAM', fnam)
    return _simple_object(rec, 'DOOR', extra_subs=extra)


def convert_FLOR(rec: dict) -> bytes:
    extra = b''
    pfig = get_formid(rec, 'PFIG')
    if pfig:
        extra += pack_formid_subrecord('PFIG', pfig)
    return _simple_object(rec, 'FLOR', extra_subs=extra)


# ---------------------------------------------------------------------------
# FURN marker data
# ---------------------------------------------------------------------------

#: MODL path -> seat list (cluster_seats); MNAM bits 0-23 enable NIF marker 0-23.
_FURN_SEATS: dict = {}
# Original TES4 base FormID (uppercase 8-hex string) -> origin shift for its
# model.  The NIF converter re-origins marker-bearing models to the vanilla
# floor-origin convention (the engine anchors seated actors to the REFR z),
# so every placed reference of these bases must be lowered by the same
# amount.  Applies to ALL record types sharing the model (FURN and STAT).
_BASE_ORIGIN_SHIFT: dict = {}


def _furn_model_key(modl: str) -> str:
    return modl.lower().replace('\\', '/').lstrip('/')


def get_base_origin_shift(base_fid: str) -> float:
    """Origin shift for a placed reference's base record (0.0 if none)."""
    return _BASE_ORIGIN_SHIFT.get(base_fid.upper(), 0.0)


def master_mesh_dirs(ctx) -> list:
    """Each TES4 master's source mesh tree, in _HEADER.txt order."""
    from ..pipeline import master_export_dirs
    return [str(assets_for(d) / 'meshes') for d in master_export_dirs(ctx)]


def _index_origin_shifts(pairs, model_shift: dict) -> int:
    """Register every (base FormID, record) whose model is re-origined.

    Two independent sources add up: the furniture floor re-origin measured
    from the converted NIF, and `Model.OriginShift` authored by the Morrowind
    export when it substituted a Morroblivion mesh sitting at a different
    origin.  `pairs` yields (fid, rec) with fid ALREADY in this plugin's index
    space — for a master that is its `master_export` key, never
    `rec['FormID']`.

    See: docs/commentary/tes4_export_morrowind.md#morroblivion-origin-shift
    """
    shifted = 0
    for fid, rec in pairs:
        if not fid:
            continue
        modl = get_str(rec, 'Model.MODL')
        shift = model_shift.get(_furn_model_key(modl)) if modl else None
        shift = (shift or 0.0) + get_float(rec, 'Model.OriginShift')
        if abs(shift) > 1e-4:
            _BASE_ORIGIN_SHIFT[fid.upper()] = shift
            shifted += 1
    return shifted


def _scan_marker_models(mesh_dirs, scan_marker_nifs) -> list:
    """(model key, absolute NIF path) for every marker-bearing model.

    Earlier directories win, so this plugin's own mesh shadows a master's
    copy of the same path.
    """
    jobs, seen = [], set()
    for mdir in mesh_dirs:
        if not os.path.isdir(mdir):
            continue
        for key in sorted(scan_marker_nifs(mdir)):
            if key in seen:
                continue
            seen.add(key)
            jobs.append((key, os.path.join(mdir, key.replace('/', os.sep))))
    return jobs


def _load_marker_seats(mesh_dirs) -> tuple:
    """(models resolved, {model key: floor re-origin}) over `mesh_dirs`.

    PyFFI parsing is CPU-bound pure Python, so the per-NIF parses run across a
    process pool; threads would serialise on the GIL.
    """
    try:
        from asset_convert.nif.furniture_markers import (furniture_model_info_job,
                                                     scan_marker_nifs)
    except ImportError as exc:
        print(f"  Furniture seats: asset_convert unavailable ({exc}), using fallback")
        return 0, {}
    jobs = _scan_marker_models(mesh_dirs, scan_marker_nifs)
    if not jobs:
        return 0, {}
    model_shift: dict = {}
    resolved = 0

    def _consume(results):
        """Record each parsed model, reporting the ones that would not read."""
        nonlocal resolved
        for key, info, err in results:
            if err is not None:
                print(f"  Furniture seats: failed to read {key}: {err}")
                continue
            _FURN_SEATS[key] = info['seats']
            model_shift[key] = info['origin_shift']
            resolved += 1

    if len(jobs) < 8:
        _consume(map(furniture_model_info_job, jobs))
    else:
        from concurrent.futures import ProcessPoolExecutor
        from core.worker_budget import worker_count
        workers = min(worker_count(), len(jobs))
        with ProcessPoolExecutor(max_workers=workers) as ex:
            _consume(ex.map(furniture_model_info_job, jobs))
    return resolved, model_shift


def load_furniture_models(meshes_dir, by_type, ctx=None, quiet=False) -> int:
    """Compute seat lists + origin shifts for every marker-bearing model.

    meshes_dir is <export_dir>/meshes; by_type maps sig -> records.  `ctx`'s
    masters are indexed too.  An unreadable NIF is skipped, leaving its REFRs
    unshifted.  The base sweep runs even with no marker model, for an authored
    `Model.OriginShift`.  `quiet` drops the summary for a threaded caller,
    which cannot redirect `sys.stdout` here without racing.

    See: docs/commentary/asset_convert_nif.md#master-owned-furniture
    """
    _FURN_SEATS.clear()
    _BASE_ORIGIN_SHIFT.clear()
    resolved, model_shift = _load_marker_seats(
        [meshes_dir] + master_mesh_dirs(ctx))

    master_export = getattr(ctx, 'master_export', None) or {}
    shifted_bases = _index_origin_shifts(master_export.items(), model_shift)
    shifted_bases += _index_origin_shifts(
        ((get_str(rec, 'FormID'), rec)
         for recs in by_type.values() for rec in recs), model_shift)
    if not quiet:
        print(f"  Furniture seats: {resolved} marker models resolved, "
              f"{shifted_bases} base records need REFR z compensation")
    return resolved


def convert_FURN(rec: dict) -> bytes:
    extra = b''
    tes4_flags = get_int(rec, 'MNAM.Flags')

    # PNAM — 4 unknown bytes (empty placeholder, required by engine)
    extra += pack_subrecord('PNAM', b'\x00\x00\x00\x00')
    # FNAM — U16 flags (bit 1 = Ignored By Sandbox); pass 0
    extra += pack_subrecord('FNAM', struct.pack('<H', 0))

    modl = get_str(rec, 'Model.MODL')
    seats = _FURN_SEATS.get(_furn_model_key(modl)) if modl else None

    if seats == []:
        # NIF read successfully but has NO furniture markers: enabling any
        # MNAM bit would make the engine index a non-existent NIF position.
        # Emit no active markers (decorative furniture).
        extra += pack_uint32_subrecord('MNAM', tes4_flags & 0xC0000000)
        extra += pack_subrecord('WBDT', struct.pack('<Bb', 0, -1))
    elif seats:
        # Enable every clustered seat; per-record approach restriction is
        # carried by the FNPR entry flags below (Oblivion restricts by
        # enabling a SUBSET of entry markers — e.g. SEChair01F/R/L share a
        # NIF and enable different entries).
        mnam = (1 << len(seats)) - 1
        mnam |= tes4_flags & 0xC0000000
        any_sleep = any(s['sleep'] for s in seats)
        if any_sleep:
            mnam |= 0x08000000  # Must Exit to Talk (all vanilla beds set it)
        extra += pack_uint32_subrecord('MNAM', mnam)
        # WBDT — workbench data: type None, skill -1 (vanilla standard)
        extra += pack_subrecord('WBDT', struct.pack('<Bb', 0, -1))
        # FNPR — one per NIF marker position, in position order:
        # Type (1=Sit, 2=Sleep) + entry-point flags.  Only the entry
        # directions whose TES4 entry marker was enabled in this record's
        # bitmask are allowed; if the record enables none of a seat's
        # entries, allow all of them (seat unreachable otherwise).
        for seat in seats:
            enabled = 0
            for entry_index, flag in seat['members']:
                if tes4_flags & (1 << entry_index):
                    enabled |= flag
            if not enabled:
                enabled = seat['entry_flags']
            anim_type = 2 if seat['sleep'] else 1
            extra += pack_subrecord('FNPR', struct.pack('<HH', anim_type, enabled))
    else:
        # Source NIF unavailable: conservative single seat, all entries.
        is_sleep = bool(tes4_flags & 0x80000000)
        mnam = 0x00000001 | (tes4_flags & 0xC0000000)
        if is_sleep:
            mnam |= 0x08000000
        extra += pack_uint32_subrecord('MNAM', mnam)
        extra += pack_subrecord('WBDT', struct.pack('<Bb', 0, -1))
        extra += pack_subrecord('FNPR', struct.pack('<HH', 2 if is_sleep else 1, 0x0F))

    return _simple_object(rec, 'FURN', extra_subs=extra)


# ---------------------------------------------------------------------------
# Grass
# ---------------------------------------------------------------------------

#: iMinGrassSize in Oblivion_default.ini and Fallout_default.ini; the source planters cap their grid step at it.
TES4_MIN_GRASS_SIZE = 80.0


def _grass_data(rec: dict) -> bytes:
    """GRAS DATA with PositionRange/Density rewritten for placement parity.

    Skyrim steps its grid at max(PositionRange, 20); the source engines step
    at max(PositionRange, 80) and boost density by 80/PositionRange when the
    cap bites, so both are baked into the record.
    See: docs/commentary/asset_convert_terrain.md#grass-placement-parity
    """
    src_range = get_float(rec, 'DATA.PositionRange')
    boost = TES4_MIN_GRASS_SIZE / src_range if 0 < src_range < TES4_MIN_GRASS_SIZE else 1.0
    data = bytearray(32)
    data[0] = min(100, round(get_int(rec, 'DATA.Density') * boost))
    data[1] = get_int(rec, 'DATA.MinSlope')
    data[2] = get_int(rec, 'DATA.MaxSlope', 90)
    struct.pack_into('<H', data, 4, get_int(rec, 'DATA.UnitFromWaterAmount'))
    struct.pack_into('<I', data, 8, get_int(rec, 'DATA.UnitFromWaterType'))
    struct.pack_into('<f', data, 12, max(src_range, TES4_MIN_GRASS_SIZE))
    struct.pack_into('<f', data, 16, get_float(rec, 'DATA.HeightRange'))
    struct.pack_into('<f', data, 20, get_float(rec, 'DATA.ColorRange'))
    struct.pack_into('<f', data, 24, get_float(rec, 'DATA.WavePeriod'))
    data[28] = get_int(rec, 'DATA.Flags')
    return bytes(data)


def convert_GRAS(rec: dict) -> bytes:
    """GRAS — Grass.

    Built by hand to honor the invariants every working GRAS record shares:
    OBND all zeros, a version-2 MODT stub, and the model under
    meshes\\landscape\\grass\\ (see grass_profile.grass_model_dest).
    See: docs/commentary/asset_convert_terrain.md#grass-conversion-record-invariants-shader
    """
    from asset_convert.nif.grass_profile import grass_model_dest
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd()
    path = get_str(rec, 'Model.MODL')
    if path:
        subs += pack_string_subrecord('MODL', grass_model_dest(path))
        subs += pack_subrecord('MODT', struct.pack('<III', 2, 0, 0))
    subs += pack_subrecord('DATA', _grass_data(rec))
    return pack_record('GRAS', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


# TES5 TREE CNAM (48 bytes = 12 floats): Trunk Flexibility, Branch
# Flexibility, Trunk Amplitude, Front Amplitude, Back Amplitude, Side
# Amplitude, Front Frequency, Back Frequency, Side Frequency, Leaf
# Flexibility, Leaf Amplitude, Leaf Frequency.  Values from vanilla
# TreeReachTree01 — moderate sway that suits full-size trees.
_TREE_CNAM = struct.pack('<12f', 1.0, 1.0, 0.04, 0.03, 0.04, 0.034,
                         0.5, 0.5, 0.4, 1.0, 2.0, 1.0)


def convert_TREE(rec: dict) -> bytes:
    r"""TREE — Tree.

    The SPT converter generates one NIF per TREE record (seeded by the
    record's SNAM seed, leaf-textured by its ICON), named by lowercase
    EditorID: tes4\speedtrees\<editorid>.nif.  OBND comes from the TES4
    billboard dimensions (the tree's real world size); CNAM supplies the
    BSLeafAnimNode wind parameters TES4 has no source for.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    bb_w = get_float(rec, 'BNAM.BillboardWidth')
    bb_h = get_float(rec, 'BNAM.BillboardHeight')
    if bb_w > 0 and bb_h > 0:
        half = min(int(bb_w / 2), 32767)
        bounds = (-half, -half, 0, half, half, min(int(bb_h), 32767))
    else:
        bounds = _resolve_obnd(rec, 'TREE')
    subs += pack_obnd(*bounds)
    model = get_str(rec, 'Model.MODL')
    if model and edid:
        subs += pack_string_subrecord(
            'MODL',
            f'{current_namespace()}\\speedtrees\\{edid.lower()}.nif')
    elif model:
        import os
        stem = os.path.splitext(os.path.basename(model.replace('\\', '/').lstrip('/')))[0]
        subs += pack_string_subrecord(
            'MODL',
            f'{current_namespace()}\\speedtrees\\{stem.lower()}.nif')
    subs += pack_subrecord('PFPC', struct.pack('<I', 0))
    subs += pack_subrecord('CNAM', _TREE_CNAM)
    # Same size-derived LOD flags as STAT: trees flow through the standard
    # object-LOD pipeline (decimated _far.nif -> LODGen) like any other object.
    flags = get_int(rec, 'RecordFlags')
    max_dim = max(bounds[3] - bounds[0], bounds[4] - bounds[1], bounds[5] - bounds[2])
    if max_dim >= LOD_SIZE_THRESHOLD:
        flags |= 0x8000       # Has Distant LOD
    return pack_record('TREE', get_formid(rec, 'FormID'), flags, subs)


def convert_LIGH(rec: dict) -> bytes:
    """LIGH — Light. TES5 order: EDID VMAD OBND MODL FULL DATA FNAM SNAM

    TES4 attaches quest scripts to lights, so VMAD is spliced in here;
    without it a converted script exists but is attached to nothing.

    The `object_scripts` import stays INSIDE the body.
    See: docs/reference/tes5_import_architecture.md#object-scripts-import-is-deferred
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    from ..base.object_scripts import get_object_vmad
    subs += get_object_vmad(get_formid(rec, 'FormID'))
    subs += pack_obnd(*_resolve_obnd(rec, 'LIGH'))
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', prefix_path(model))
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    # DATA (48 bytes)
    time = get_int(rec, 'DATA.Time')
    radius = get_int(rec, 'DATA.Radius', 128)
    r = get_int(rec, 'DATA.Color.R')
    g = get_int(rec, 'DATA.Color.G')
    b = get_int(rec, 'DATA.Color.B')
    flags = get_int(rec, 'DATA.Flags')
    flags &= ~0x10  # TES4 'Unused' bit
    # TES4 stores 0 for "engine default" here; xEdit's wbLIGHAfterLoad applies
    # the same substitution (0 falloff -> 1.0, 0 FOV -> 90).
    falloff = get_float(rec, 'DATA.FalloffExponent', 1.0) or 1.0
    fov = get_float(rec, 'DATA.FOV', 90.0) or 90.0
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    # TES4 has no flicker-effect parameters — the animation was implied by the
    # flags alone.  Skyrim needs explicit period/amplitudes or animated lights
    # render static/degenerate.  Values follow vanilla Skyrim conventions
    # (torches: 0.333/0.5/16; pulse candles: 0.333/0.2/1).
    if flags & 0x08:      # Flicker
        period, int_amp, mov_amp = 0.333, 0.5, 16.0
    elif flags & 0x40:    # Flicker Slow
        period, int_amp, mov_amp = 0.667, 0.5, 16.0
    elif flags & 0x80:    # Pulse
        period, int_amp, mov_amp = 1.0, 0.2, 0.0
    elif flags & 0x100:   # Pulse Slow
        period, int_amp, mov_amp = 2.0, 0.2, 0.0
    else:
        period, int_amp, mov_amp = 1.0, 0.0, 0.0
    data = bytearray(48)
    struct.pack_into('<i', data, 0, time)
    struct.pack_into('<I', data, 4, radius)
    data[8] = r; data[9] = g; data[10] = b; data[11] = 0
    struct.pack_into('<I', data, 12, flags)
    struct.pack_into('<f', data, 16, falloff)
    struct.pack_into('<f', data, 20, fov)
    struct.pack_into('<f', data, 24, 1.0)  # Near clip (vanilla default)
    struct.pack_into('<f', data, 28, period)
    struct.pack_into('<f', data, 32, int_amp)
    struct.pack_into('<f', data, 36, mov_amp)
    struct.pack_into('<I', data, 40, value)
    struct.pack_into('<f', data, 44, weight)
    subs += pack_subrecord('DATA', bytes(data))

    # FNAM is required in TES5; TES4's default when absent is 1.0.
    fade = get_float(rec, 'FNAM.Fade', 1.0)
    subs += pack_float_subrecord('FNAM', fade)
    # SNAM is the light's looping ambience (torch crackle, brazier roar).  TES4
    # names a SOUN there; TES5 wants the sound DESCRIPTOR, so this id is a
    # placeholder patch_sound_descriptor_slots resolves in Phase 3 — leaving a
    # SOUN id here crashes the audio thread (see _SNDR_SLOTS).
    snam = get_formid(rec, 'SNAM.Sound')
    if snam:
        subs += pack_formid_subrecord('SNAM', snam)
    return pack_record('LIGH', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_SLGM(rec: dict) -> bytes:
    extra = b''
    extra += pack_keywords([VENDOR_KYWD['SoulGem']])
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    extra += pack_subrecord('DATA', struct.pack('<If', value, weight))
    soul = get_int(rec, 'SOUL', -1)
    if soul >= 0:
        extra += pack_uint8_subrecord('SOUL', soul)
    slcp = get_int(rec, 'SLCP.Capacity', -1)
    if slcp >= 0:
        extra += pack_uint8_subrecord('SLCP', slcp)
    return _simple_object(rec, 'SLGM', extra_subs=extra)


def convert_ANIO(rec: dict) -> bytes:
    """ANIO — Animated Object. No OBND per xEdit; just EDID + MODL + BNAM."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', prefix_path(model))
    bnam = get_str(rec, 'BNAM')
    if bnam:
        subs += pack_string_subrecord('BNAM', bnam)
    return pack_record('ANIO', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_CONT(rec: dict) -> bytes:
    extra = b''
    # Items (CNTO)
    i = 0
    while True:
        fid = get_formid(rec, f'Item[{i}].FormID')
        if not fid:
            break
        # TES4 merchant chests use NEGATIVE counts for restocking stock
        # ("-100" = keep 100 for sale); Skyrim restocks via the respawn flag
        # and treats count < 1 as "adds nothing" (CK: "Container object has a
        # count of less then 1 ... will cause issues in game").
        count = abs(get_int(rec, f'Item[{i}].Count', 1)) or 1
        extra += pack_subrecord('CNTO', struct.pack('<Ii', fid, count))
        i += 1
    # DATA: Flags(1) + Weight(4) = 5 bytes in TES4
    # TES5: same DATA structure
    flags = get_int(rec, 'DATA.Flags')
    weight = get_float(rec, 'DATA.Weight')
    extra += pack_subrecord('DATA', struct.pack('<Bf', flags, weight))
    # Open/close sounds: TES5 wants descriptors, so these TES4 SOUN ids are
    # placeholders patch_sound_descriptor_slots resolves (see _SNDR_SLOTS).
    snam = get_formid(rec, 'SNAM.OpenSound')
    if snam:
        extra += pack_formid_subrecord('SNAM', snam)
    qnam = get_formid(rec, 'QNAM.CloseSound')
    if qnam:
        extra += pack_formid_subrecord('QNAM', qnam)
    return _simple_object(rec, 'CONT', extra_subs=extra)


# ---------------------------------------------------------------------------
# Leveled list converters
# ---------------------------------------------------------------------------


def _leveled_entries(rec: dict) -> list:
    """(level, FormID, count) per entry, skipping the null-FormID slots.

    LVLO is `Level(U16) pad(U16) FormID(U32) Count(U16) pad(U16)`, 12 bytes.

    See: docs/commentary/ck_warnings.md#leveled-list-null-entries
    """
    entries = []
    for i in range(get_int(rec, 'EntryCount')):
        fid = get_formid(rec, f'Entry[{i}].FormID')
        if not fid:
            continue
        level = get_int(rec, f'Entry[{i}].Level', 1)
        count = abs(get_int(rec, f'Entry[{i}].Count', 1)) or 1
        entries.append((max(1, level), fid, count))
    return entries


def _convert_leveled_list(rec: dict, tes5_sig: str) -> bytes:
    """Pack a TES4 leveled list as `tes5_sig`, LLCT counting surviving entries."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd()

    chance = get_int(rec, 'LVLD.ChanceNone')
    subs += pack_uint8_subrecord('LVLD', chance)
    flags = get_int(rec, 'LVLF.Flags')
    subs += pack_uint8_subrecord('LVLF', flags)

    entries = _leveled_entries(rec)
    if entries:
        subs += pack_subrecord('LLCT', struct.pack('<B', min(len(entries), 255)))
    for level, fid, count in entries:
        subs += pack_subrecord('LVLO', struct.pack('<HxxIHxx', level, fid, count))

    return pack_record(tes5_sig, get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_LVLI(rec: dict) -> bytes:
    """LVLI → LVLI (Leveled Item)."""
    return _convert_leveled_list(rec, 'LVLI')


def convert_LVLC(rec: dict) -> bytes:
    """LVLC → LVLN (Leveled NPC)."""
    return _convert_leveled_list(rec, 'LVLN')


def convert_LVLN(rec: dict) -> bytes:
    """LVLN → LVLN; FO3/FNV has the type natively and Skyrim shares it."""
    return _convert_leveled_list(rec, 'LVLN')


def convert_LVSP(rec: dict) -> bytes:
    """LVSP → LVSP (Leveled Spell)."""
    return _convert_leveled_list(rec, 'LVSP')

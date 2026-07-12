"""Misc converters: SOUN, PACK, WTHR.

All dialog/quest/DIAL/INFO/DLBR/DLVW logic has been moved to
tes5_import.dialog_converter.
"""

import struct

from .common import (
    _prefix_path,
    get_float,
    get_formid,
    get_int,
    get_str,
    pack_formid_subrecord,
    pack_obnd,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint8_subrecord,
    pack_uint32_subrecord,
)


# TES4 SNDX/SNDD flag bits (xEdit wbDefinitionsTES4, SOUN)
_TES4_SND_RANDOM_FREQ_SHIFT = 0x0001
_TES4_SND_LOOP              = 0x0010
_TES4_SND_MENU_SOUND        = 0x0020
_TES4_SND_2D                = 0x0040

# Vanilla Skyrim SOPM constants (verified against references/Skyrim.esm SOPM dump)
_SOPM_2D = 0x000B5183            # SOMDialogue2D — non-attenuating, for menu/2D sounds
_SOPM_ONAM_CHANNELS = bytes.fromhex(
    '646400003232323264000000640064000064000000640064')
# ANAM: unknown[4] minDistance(f32) maxDistance(f32) curve[5] unknown[3]
_SOPM_ANAM_LEAD = bytes.fromhex('809dfa00')   # most common in vanilla (24/69)
_SOPM_ANAM_TAIL = b'\x00\x00\x00'             # most common in vanilla (56/69)
# Standard vanilla falloff curve, shared by every SOMMono*/SOMStereoRad* model
_SOPM_CURVE = bytes((100, 50, 20, 5, 0))


def _build_sopm(writer, min_dist: float, max_dist: float, stereo: bool) -> int:
    """Get-or-create a Sound Output Model with the given attenuation distances.

    Skyrim does not store falloff distances on the sound itself — they live in
    the SOPM the SNDR's ONAM points at (vanilla ships one per distance:
    SOMMono00400, SOMMono03000, SOMMono10000, ...).  Oblivion instead stores the
    distances per-SOUN in SNDX, so we mint a SOPM per distinct distance pair and
    cache it, rather than pinning every sound to a single model.

    Returns the SOPM FormID.
    """
    cache = getattr(writer, '_sopm_cache', None)
    if cache is None:
        cache = writer._sopm_cache = {}
    key = (round(min_dist), round(max_dist), stereo)
    if key in cache:
        return cache[key]

    fid = writer.alloc_formid()
    kind = 'Stereo' if stereo else 'Mono'
    subs = pack_string_subrecord(
        'EDID', f'TES4_SOM{kind}{round(max_dist):05d}_{round(min_dist):05d}')
    # NAM1: Flags(u8) unknown[2] ReverbSend%(u8).  Flag 0x01 = Attenuates With
    # Distance — required, or the sound plays at full volume everywhere.
    subs += pack_subrecord('NAM1', struct.pack('<BHB', 0x01, 0, 30))
    # MNAM: 0 = Uses HRTF (mono), 1 = Defined Speaker Output (stereo)
    subs += pack_uint32_subrecord('MNAM', 1 if stereo else 0)
    if stereo:
        subs += pack_subrecord('ONAM', _SOPM_ONAM_CHANNELS)
    subs += pack_subrecord('ANAM', _SOPM_ANAM_LEAD
                           + struct.pack('<ff', min_dist, max_dist)
                           + _SOPM_CURVE + _SOPM_ANAM_TAIL)
    writer.add_record('SOPM', pack_record('SOPM', fid, 0, subs))
    cache[key] = fid
    return fid


def convert_SOUN(rec: dict, writer=None) -> tuple:
    """SOUN — needs companion SNDR record in TES5.
    Returns (soun_bytes, sndr_bytes_or_None, sndr_formid).

    SOUN order: EDID OBND SDSC
    SNDR order: EDID CNAM GNAM SNAM ANAM[] ONAM LNAM BNAM

    Volume in Skyrim comes from two places, and both must be carried over from
    TES4 or every sound plays far louder than vanilla:
      * SNDR BNAM 'Static Attenuation (db)' — a per-sound volume trim.  Oblivion
        stores the same value in SNDX bytes 8-9 (95% of Oblivion.esm SOUNs set
        it; median 6.6 dB).
      * The SOPM's min/max attenuation distance — how fast the sound falls off
        with distance.  Oblivion stores these in SNDX bytes 0-1.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd()

    # SDSC → link to SNDR
    sndr_fid = 0
    sndr_bytes = None
    filename = get_str(rec, 'FNAM.Filename')
    if filename and writer:
        # SNDX and SNDD hold the same struct; whichever is present wins.
        pfx = 'SNDD' if rec.get('SNDD.MaxAttDist') is not None else 'SNDX'
        tes4_flags = get_int(rec, f'{pfx}.Flags') or 0
        # TES4 stores the distances scaled down: min x5, max x100 (xEdit wbMul).
        min_dist = (get_int(rec, f'{pfx}.MinAttDist') or 0) * 5.0
        max_dist = (get_int(rec, f'{pfx}.MaxAttDist') or 0) * 100.0
        # Static attenuation is a u16 of hundredths of a dB in both games, so it
        # transfers as a raw value with no rescaling.
        static_atten = get_int(rec, f'{pfx}.StaticAttenuation') or 0

        is_2d = bool(tes4_flags & (_TES4_SND_2D | _TES4_SND_MENU_SOUND))

        sndr_fid = writer.alloc_formid()
        sndr_subs = b''
        sndr_edid = f"TES4_{edid}_SNDR" if edid else f"TES4_SOUN_{get_formid(rec, 'FormID'):08X}_SNDR"
        sndr_subs += pack_string_subrecord('EDID', sndr_edid)
        # CNAM = Descriptor Type constant (0x1EEF540A — matches all vanilla SNDR records)
        sndr_subs += pack_uint32_subrecord('CNAM', 0x1EEF540A)
        # GNAM = Category: AudioCategorySFX (FormID 0x000172A1 in Skyrim.esm)
        sndr_subs += pack_formid_subrecord('GNAM', 0x000172A1)
        # ANAM = Sound file path
        sndr_subs += pack_string_subrecord('ANAM', _prefix_path(filename))
        # ONAM = Sound Output Model. Required — CK reports 'Sound Output Model
        # missing' if absent.  2D/menu sounds are not positional, so they take
        # the vanilla non-attenuating model; everything else gets a SOPM built
        # from this sound's own TES4 falloff distances.
        if is_2d or max_dist <= 0:
            onam_fid = _SOPM_2D
        else:
            onam_fid = _build_sopm(writer, min_dist, max_dist, stereo=False)
        sndr_subs += pack_formid_subrecord('ONAM', onam_fid)
        # LNAM = Loop Data struct (4 bytes): byte[0]=Unknown, byte[1]=Looping enum,
        # byte[2]=Unknown, byte[3]=Rumble.  Looping enum: 0x00=None, 0x08=Loop.
        lnam_value = 0x00000800 if (tes4_flags & _TES4_SND_LOOP) else 0
        sndr_subs += pack_subrecord('LNAM', struct.pack('<I', lnam_value))
        # BNAM = Values: FreqShift(S8) FreqVariance(S8) Priority(U8) dbVariance(U8) StaticAttenuation(U16)
        freq_adj = get_int(rec, f'{pfx}.FreqAdj') or 0
        freq_var = 0 if not (tes4_flags & _TES4_SND_RANDOM_FREQ_SHIFT) else 10
        sndr_subs += pack_subrecord(
            'BNAM', struct.pack('<bbBBH', max(-128, min(127, freq_adj)),
                                freq_var, 128, 0, min(65535, static_atten)))
        sndr_bytes = pack_record('SNDR', sndr_fid, 0, sndr_subs)

    if sndr_fid:
        subs += pack_formid_subrecord('SDSC', sndr_fid)

    soun_bytes = pack_record('SOUN', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)
    return soun_bytes, sndr_bytes, sndr_fid


def convert_PACK(rec: dict) -> bytes:
    """PACK — AI Package conversion (TES4 → TES5).

    TES5 PACK uses a procedure tree system. We create skeleton packages
    with PKDT/PSDT/PKCU and the required OnBegin/OnEnd/OnChange markers.
    PLDT/PTDT do NOT exist in TES5 and must NOT be emitted.

    TES5 order: EDID VMAD PKDT PSDT [conditions]
                [idle anims] CNAM QNAM PKCU
                [Package Data] XNAM
                [Procedure Tree] [UNAMs]
                POBA(OnBegin) POEA(OnEnd) POCA(OnChange)

    =========================================================================
    BRAINSTORM: Getting to fully functional PACK records
    =========================================================================

    CURRENT STATE
      We emit a structurally valid but behaviourally empty PACK:
        - PKDT with flag/type approximation, no Procedure Tree branches
        - PSDT schedule (month/dow/date/time/duration) — this part is correct
        - PKCU with zero data inputs and null template
        - XNAM + POBA/POEA/POCA markers (required structure, no behaviour)
      The result is a package the engine accepts without crashing but which
      causes the NPC to stand idle because the Procedure Tree has no branches.

    THE CORE STRUCTURAL PROBLEM
      TES4 PACK behaviour is encoded in PKDT.Type (0=Find, 1=Follow, 2=Escort,
      3=Eat, 4=Sleep, 5=Wander, 6=Travel, 7=Accompany, 8=UseItemAt, 9=Ambush,
      10=FleeNotCombat, 11=CastMagic) plus PLDT (location) and PTDT (target).
      TES5 replaces these with a Procedure Tree: a tree of named branch types
      (ANAM strings like "Travel", "Wander", "Patrol", etc.) where each branch
      has conditions (CTDAs), data inputs (PLDT/PTDA subrecords inside Package
      Data), and a PRCB struct giving branch count + repeat flags. The type
      information that was a single byte in TES4 is now a named branch in TES5.

    TES4 TYPE → TES5 BRANCH TYPE MAPPING
      TES4 Type  Name          TES5 ANAM branch string(s)
      ---------  -----------   --------------------------------
      0          Find          "Travel" branch + "Find" sub-branch
      1          Follow        "Follow"
      2          Escort        "Escort"
      3          Eat           "UseWeapon" or "Activate" (eating = activating food)
      4          Sleep         "Sleep"
      5          Wander        "Wander"
      6          Travel        "Travel"
      7          Accompany     "Escort" (closest equivalent)
      8          UseItemAt     "UseWeapon" / "Activate"
      9          Ambush        "PatrolSit" or custom Combat branch
      10         FleeNotCombat "Flee"
      11         CastMagic     "UseWeapon" (magic combat package)

      These are discovered by inspecting vanilla Skyrim PACK records in
      Skyrim.esm using xEdit. The branch ANAM strings are not enumerated in
      wbDefinitionsTES5.pas — they are freeform strings matching CK templates.

    TES4 PLDT (Location) → TES5 PLDT (inside Package Data)
      Both formats use the same 12-byte PLDT struct (Type S32, Value 4 bytes,
      Radius S32). The Type values are partially compatible:
        TES4 Type 0 = Near reference  → TES5 Type 0 = Near reference (same)
        TES4 Type 1 = In cell         → TES5 Type 1 = In cell (same)
        TES4 Type 2 = Near current location → TES5 Type 2 = Near pkg start loc
        TES4 Type 3 = Near editor loc → TES5 Type 3 = Near editor loc (same)
        TES4 Type 4 = Object ID       → TES5 Type 4 = Object ID (same)
        TES4 Type 5 = Object type     → TES5 Type 5 = Object type (same)
      FormID references in Type 0/1/4 are TES4 FormIDs and need remapping
      via the standard FormID translation table.

    TES4 PTDT (Target) → TES5 PTDA (Target Data inside Package Data)
      TES4 PTDT: Type S32 (0=SpecificRef, 1=ObjectID, 2=ObjectType),
                  Target (FormID or U32), Count S32
      TES5 PTDA wraps wbTargetData:
        Type S32 (0=SpecificRef, 1=ObjectID, 2=ObjectType, 3=LinkedRef,
                  4=RefAlias, 5=Unknown, 6=Self), Target (FormID or U32),
        Count/Distance S32
      TES4 types 0-2 map directly to TES5 types 0-2.

    PKDT FLAGS MAPPING
      TES4 flag (U16 or U32)       TES5 flag (U32)
      ----------------------------  -----------------------------------------
      0x0001 Offers services        0x00000001 Offers Services (same)
      0x0004 Must complete          0x00000004 Must complete (same)
      0x0008 Lock doors at start    0x00000008 Maintain Speed at Goal (≈)
      0x0200 Once per day           no direct equivalent — use PSDT Date=1
      0x2000 Always run             0x02000000 Unknown 26 (closest)
      0x00020000 Always sneak       0x00004000 Unknown 15 (closest, verify)
      0x00040000 Allow swimming     0x00200000 Unknown 22 (closest)
      0x01000000 No idle anims      0x01000000 Unknown 25 (same bit, verify)
      NOTE: TES4 PKDT is U16 in old records (detected by PKDT length=4),
      U32 in newer records (length=8). The exporter reads either form.

    PROCEDURE TREE STRUCTURE (required for the package to execute)
      Minimal branch for any package type:
        ANAM "BranchTypeName" (e.g. "Wander")
        CITC 0 (condition count = 0)
        PRCB struct: BranchCount=1, Flags=0 (or Flags=1 = Repeat when complete)
        PNAM "ProcedureTypeName" (same as ANAM for leaf branches)
        FNAM 0 (no Success Completes Package)
        PKC2 0 (data input index 0)
      The PKC2 index refers to an entry in the Package Data input list
      (the ANAM/CNAM/PLDT/PTDA group inside Package Data).

    PACKAGE DATA INPUTS (inside the PKCU/Package Data group)
      Each data input has:
        ANAM string: "Bool", "Int", "Float", "Topic", "TargetSelector",
                     "LocationSelector", "SingleRef", "ObjectList"
      For a Wander package: LocationSelector input pointing to the PLDT
      For a Travel package: LocationSelector input pointing to the PLDT
      For a Follow package: TargetSelector input pointing to the PTDA
      PKCU.DataInputCount = number of ANAM entries in the Package Data group.

    APPROACH: TEMPLATE-BASED GENERATION
      Rather than building the Procedure Tree from scratch per-package, the
      correct approach is to use Skyrim.esm's built-in package templates as
      a base. Several vanilla templates exist in Skyrim.esm:
        [00015E8F] DefaultWanderHome    — Wander near home marker
        [00015E92] DefaultSit           — Sit at furniture
        [00015E7F] DefaultSandboxCell   — Sandbox in cell
        [000D6B89] DefaultSleepEditor   — Sleep at editor location
        [000D6B8A] DefaultEat           — Eat at editor location
        [000D6B8C] DefaultTravelToRef   — Travel to reference
      Strategy:
        1. Map TES4 Type → nearest Skyrim template FormID (hardcoded table)
        2. Emit PKCU.PackageTemplate pointing at the template FormID
        3. Emit PKCU.DataInputCount = number of inputs the template expects
        4. Emit Package Data inputs (ANAM/CNAM/PLDT/PTDA) matching the template
        5. Omit the Procedure Tree — the template provides it at runtime
      This is how the CK generates packages from templates: the PACK record
      carries only the customisation (data inputs, schedule, conditions) while
      the template provides the behaviour tree. The engine evaluates the
      template's tree with the record's data inputs substituted.

    TES4 TYPE → SKYRIM TEMPLATE MAP (verified from Skyrim.esm)
      TES4 Type  Template EditorID          FormID
      ---------  --------------------------  --------
      0 Find      DefaultSandboxCell         0x00015E8F (nearest available)
      1 Follow    DefaultFollow (custom)     needs creation or closest match
      2 Escort    DefaultEscort (custom)     needs creation or closest match
      3 Eat       DefaultEat                 0x000D6B8A
      4 Sleep     DefaultSleepEditor         0x000D6B89
      5 Wander    DefaultWanderHome          0x00015E8F (sandbox = wander)
      6 Travel    DefaultTravelToRef         0x000D6B8C
      7 Accompany DefaultEscort              0x000D6B8C (travel closest)
      8 UseItemAt DefaultEat (repurpose)     0x000D6B8A (use at location)
      9 Ambush    DefaultSandboxCell         0x00015E8F (no good equivalent)
      10 Flee     no Skyrim template          must build tree manually
      11 CastMagic DefaultSandboxCell        0x00015E8F (fallback)
      These FormIDs are in Skyrim.esm and are always present. They are
      master-file records so no remapping is needed.

    IMPLEMENTATION ORDER
      Step 1: Map PKDT flags correctly (complete the flag table above)
      Step 2: Implement template-based PKCU.PackageTemplate for types 3,4,5,6
              (Eat/Sleep/Wander/Travel — the most common NPC routines)
      Step 3: Translate PLDT (location) and emit as Package Data input with
              ANAM "LocationSelector" for location-based packages
      Step 4: Translate PTDT (target) and emit as Package Data PTDA input
              with ANAM "TargetSelector" for follow/escort packages
      Step 5: For non-template types, build a minimal Procedure Tree manually
              (ANAM + PRCB + PNAM + FNAM + PKC2) — needed for Follow/Escort
      Step 6: Export CTDA conditions from TES4 PACK and map condition functions
              to TES5 equivalents (many are shared by name)
      Step 7: Handle PKDT old format (length=4, flags are U16) vs new (length=8)
              — the exporter currently reads only one form
    =========================================================================
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # PKDT — Package Data (12 bytes in TES5)
    # Flags(U32) + Type(U8) + InterruptOverride(U8) + PreferredSpeed(U8) + pad(U8)
    # + InterruptFlags(U16) + pad(U16) = 12 bytes
    tes4_flags = get_int(rec, 'PKDT.Flags')

    # TES5 type 18 = Package (generic)
    tes5_type = 18

    # Map TES4 flags to TES5 flags (best effort)
    tes5_flags = 0
    if tes4_flags & 0x04:    # MustComplete
        tes5_flags |= 0x04
    if tes4_flags & 0x08:    # LockDoorsAtStart
        tes5_flags |= 0x08
    if tes4_flags & 0x200:   # OncePerDay
        tes5_flags |= 0x200
    if tes4_flags & 0x2000:  # AlwaysRun
        tes5_flags |= 0x2000000
    if tes4_flags & 0x4000:  # AlwaysSneak
        tes5_flags |= 0x4000

    subs += pack_subrecord('PKDT', struct.pack('<IBBBBHH',
                                                tes5_flags, tes5_type, 0, 0, 0, 0, 0))

    # PSDT — Schedule Data (12 bytes)
    month = get_int(rec, 'PSDT.Month')
    dow = get_int(rec, 'PSDT.DayOfWeek')
    date = get_int(rec, 'PSDT.Date')
    time_val = get_int(rec, 'PSDT.Time')
    duration = get_int(rec, 'PSDT.Duration')
    subs += pack_subrecord('PSDT', struct.pack('<bbbbb3xi',
                                                month, dow, date, time_val, 0, duration))

    # PKCU — Package Use (12 bytes: DataInputCount + PackageTemplate + VersionCounter)
    # Null template = custom package
    subs += pack_subrecord('PKCU', struct.pack('<III', 0, 0, 0))

    # XNAM — Marker (empty, required)
    subs += pack_subrecord('XNAM', b'')

    # OnBegin marker (required)
    subs += pack_subrecord('POBA', b'')
    subs += pack_formid_subrecord('INAM', 0)  # Idle = NULL

    # OnEnd marker (required)
    subs += pack_subrecord('POEA', b'')
    subs += pack_formid_subrecord('INAM', 0)  # Idle = NULL

    # OnChange marker (required)
    subs += pack_subrecord('POCA', b'')
    subs += pack_formid_subrecord('INAM', 0)  # Idle = NULL

    return pack_record('PACK', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def _wthr_cloud_sig(layer: int) -> bytes:
    """Build the 4-byte cloud texture signature for a given layer index (0-28).

    Layer 0-16:  first byte is chr(0x30 + layer), rest is '0TX'
                 e.g. layer 0 = '00TX', layer 1 = '10TX', layer 10 = ':0TX'
    Layer 17-28: first byte is chr(0x41 + (layer - 17)), rest is '0TX'
                 e.g. layer 17 = 'A0TX', layer 18 = 'B0TX'
    """
    if layer <= 16:
        return bytes([0x30 + layer]) + b'0TX'
    else:
        return bytes([0x41 + (layer - 17)]) + b'0TX'


def convert_WTHR(rec: dict) -> bytes:
    """WTHR — Weather conversion.

    TES5 subrecord order (from wbDefinitionsTES5.pas):
    EDID, cloud textures (00TX..L0TX), DNAM(unused), CNAM(unused), ANAM(unused),
    BNAM(unused), LNAM, MNAM, NNAM, ONAM(unused), RNAM, QNAM, PNAM, JNAM,
    NAM0, FNAM, DATA, NAM1, SNAM(sounds), TNAM, IMSP, HNAM, DALC×4,
    NAM2(unused), NAM3(unused), MODL/MODT(aurora), GNAM
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # Cloud layer textures — use proper pack_subrecord with correct 4-byte signatures
    lower_cloud = get_str(rec, 'CNAM.LowerCloudLayer')
    upper_cloud = get_str(rec, 'DNAM.UpperCloudLayer')
    if lower_cloud:
        # Layer 0 = signature '00TX' (bytes 0x30,0x30,0x54,0x58)
        sig = _wthr_cloud_sig(0)
        path_bytes = _prefix_path(lower_cloud).encode('utf-8') + b'\x00'
        subs += sig + struct.pack('<H', len(path_bytes)) + path_bytes
    if upper_cloud:
        # Layer 1 = signature '10TX' (bytes 0x31,0x30,0x54,0x58)
        sig = _wthr_cloud_sig(1)
        path_bytes = _prefix_path(upper_cloud).encode('utf-8') + b'\x00'
        subs += sig + struct.pack('<H', len(path_bytes)) + path_bytes

    # LNAM — unknown (4 bytes)
    subs += pack_subrecord('LNAM', struct.pack('<I', 0))

    # NAM0 — Color data (TES5 expects up to 272 bytes for weather colors)
    nam0 = bytearray(272)
    for i in range(0, 272, 4):
        nam0[i] = 128; nam0[i+1] = 128; nam0[i+2] = 128; nam0[i+3] = 255
    subs += pack_subrecord('NAM0', bytes(nam0))

    # FNAM — Fog distances (TES5: 32 bytes — 8 floats)
    fog_day_near = get_float(rec, 'FNAM.FogDayNear', 100.0)
    fog_day_far = get_float(rec, 'FNAM.FogDayFar', 100000.0)
    fog_night_near = get_float(rec, 'FNAM.FogNightNear', 100.0)
    fog_night_far = get_float(rec, 'FNAM.FogNightFar', 100000.0)
    fnam = struct.pack('<ffffffff',
                        fog_day_near, fog_day_far,
                        fog_night_near, fog_night_far,
                        1.0, 1.0,    # Day/Night power
                        1.0, 1.0)    # Day/Night max
    subs += pack_subrecord('FNAM', fnam)

    # DATA — Weather Data (19 bytes in TES5)
    wind_speed = get_int(rec, 'DATA.WindSpeed')
    trans_delta = get_int(rec, 'DATA.TransDelta')
    sun_glare = get_int(rec, 'DATA.SunGlare')
    sun_damage = get_int(rec, 'DATA.SunDamage')
    data = struct.pack('<B2xBBBBBBBBB3xBBBB',
                        wind_speed, trans_delta, sun_glare, sun_damage,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    subs += pack_subrecord('DATA', data)

    # NAM1 — Disabled cloud layers (U32)
    subs += pack_uint32_subrecord('NAM1', 0xFFFFFFFF)  # All disabled (no valid cloud data)

    # Sounds — SNAM (after NAM1 per xEdit)
    sc = get_int(rec, 'SoundCount')
    for i in range(sc):
        sfid = get_formid(rec, f'Sound[{i}].FormID')
        stype = get_int(rec, f'Sound[{i}].Type')
        if sfid:
            subs += pack_subrecord('SNAM', struct.pack('<II', sfid, stype))

    # IMSP — Image Spaces (4 FormIDs: sunrise/day/sunset/night) — after sounds
    subs += pack_subrecord('IMSP', struct.pack('<IIII', 0, 0, 0, 0))

    # DALC — Directional Ambient Lighting Colors (4 sections × 24 bytes)
    for _section in range(4):
        dalc = bytearray(24)
        for i in range(0, 24, 4):
            dalc[i] = 128; dalc[i+1] = 128; dalc[i+2] = 128; dalc[i+3] = 0
        subs += pack_subrecord('DALC', bytes(dalc))

    return pack_record('WTHR', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)

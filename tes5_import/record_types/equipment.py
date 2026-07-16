"""Equipment converters: WEAP, ARMO, CLOT, AMMO, BOOK, ENCH, SPEL, ALCH, INGR, SGST, APPA."""

import re
import struct

from ..constants import ENCH_CAST_TYPE_MAP, ENCH_TYPE_MAP, WEAPON_TYPE_MAP, ARMA_BODY_COVERAGE_EXTRA
from ..magic_effects import aimed_variant, has_projectile
from ..skyrim_overrides import (
    ARMA_ADDITIONAL_RACES,
    CLOTHING_FOOTSTEP_SET,
    DEFAULT_ARROW_PROJECTILE,
    HEAVY_ARMOR_FOOTSTEP_SET,
    LIGHT_ARMOR_FOOTSTEP_SET,
    MGEF_AV_CODE_TO_SKYRIM,
    MGEF_CODE_TO_SKYRIM,
    SHIELD_EQUIP_TYPE,
    TES4_SKILL_TO_TES5_INDEX,
    WEAPON_ANIM_BAMT,
    WEAPON_ANIM_BIDS,
    WEAPON_ANIM_EQUP,
    WEAPON_ANIM_FLAGS,
    WEAPON_ANIM_INAM,
    WEAPON_ANIM_MULT,
    WEAPON_ANIM_NAM8,
    WEAPON_ANIM_NAM9,
    WEAPON_ANIM_STAGGER,
    WEAPON_ANIM_VNAM,
)
from .common import (
    VENDOR_KYWD,
    _common_header_subs,
    _convert_biped_flags,
    _prefix_path,
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
)


def _resolve_mgef(code: str, actor_value: int = -1) -> int:
    """Map a TES4 4-char magic effect code to a Skyrim MGEF FormID.

    Attribute/skill-targeted effects (DRAT, FOSK, ...) resolve through the
    effect's ActorValue; everything else uses the flat code table.
    """
    per_av = MGEF_AV_CODE_TO_SKYRIM.get(code)
    if per_av is not None:
        fid = per_av.get(actor_value)
        if fid:
            return fid
    return MGEF_CODE_TO_SKYRIM.get(code, 0)


# Harmless zero-magnitude filler effects used when a record would otherwise
# have no (or too few) effects. A null EFID hard-crashes the inventory menu,
# so we must always reference a real MGEF.
_FILLER_EFFECTS = (0x0003EB15, 0x0003EB17, 0x0003EB16, 0x0003EAF3)  # AlchRestore{Health,Magicka,Stamina}, AlchFortifyHealth


def _pack_effects(rec: dict, count_key: str = 'EffectCount', pad_to: int = 0,
                  delivery: int = 0, writer=None) -> bytes:
    """Pack EFID/EFIT pairs for all effects on a record.

    Effects with no TES5 equivalent are dropped — an EFID of 0 (null MGEF)
    crashes the game as soon as the item's card is shown in a menu. If all
    effects are dropped, or pad_to demands more (e.g. 4 for INGR), real
    zero-magnitude filler effects are used.

    ``delivery`` is the owning record's delivery (2 = Aimed): an aimed magic
    item fires the projectile of its effects' MGEFs, so if none of them has
    one the item casts NOTHING in game.  In that case the first effect is
    swapped for a synthesized aimed clone (see magic_effects.aimed_variant).
    """
    effects = []
    effect_count = get_int(rec, count_key)
    for i in range(effect_count):
        if pad_to and len(effects) >= pad_to:
            break
        code = get_str(rec, f'Effect[{i}].EFID')
        av = get_int(rec, f'Effect[{i}].ActorValue', -1)
        mgef_fid = _resolve_mgef(code, av) if code else 0
        if not mgef_fid:
            continue
        mag = get_int(rec, f'Effect[{i}].Magnitude')
        area = get_int(rec, f'Effect[{i}].Area')
        dur = get_int(rec, f'Effect[{i}].Duration')
        effects.append((mgef_fid, float(mag), area, dur, code))

    # Every effect-bearing record needs at least one real effect; INGR needs
    # exactly pad_to. Fill with distinct harmless zero-magnitude effects.
    want = max(pad_to, 1)
    used = {fid for fid, _, _, _, _ in effects}
    fillers = iter(fid for fid in _FILLER_EFFECTS if fid not in used)
    while len(effects) < want:
        effects.append((next(fillers, _FILLER_EFFECTS[0]), 0.0, 0, 0, ''))

    if delivery == 2 and not any(has_projectile(fid) for fid, *_ in effects):
        for idx, (fid, mag, area, dur, code) in enumerate(effects):
            variant = aimed_variant(fid, code, writer)
            if variant:
                effects[idx] = (variant, mag, area, dur, code)
                break

    subs = b''
    for mgef_fid, mag, area, dur, _code in effects:
        subs += pack_formid_subrecord('EFID', mgef_fid)
        subs += pack_subrecord('EFIT', struct.pack('<fII', mag, area, dur))
    return subs


def _build_model_stat(edid: str, model_path: str, stat_fid: int) -> bytes:
    """Build a minimal STAT record wrapping a mesh (WEAP WNAM / BOOK INAM target).

    TES5 STAT order: EDID OBND MODL DNAM
    """
    subs = b''
    subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd()
    subs += pack_string_subrecord('MODL', model_path)
    # DNAM: MaxAngle(float) + Directional Material(FormID, null)
    subs += pack_subrecord('DNAM', struct.pack('<fI', 0.0, 0))
    return pack_record('STAT', stat_fid, 0, subs)


def _build_weapon_1stperson_stat(edid: str, model_path: str, stat_fid: int) -> bytes:
    """Build a STAT record for a weapon's 1st-person model (WNAM target).

    We reuse the world model path since Oblivion has no separate 1st-person meshes.
    """
    return _build_model_stat('1stPerson_' + edid, model_path, stat_fid)


def convert_WEAP(rec: dict, writer=None) -> bytes:
    """Convert WEAP.

    TES5 order: EDID OBND FULL MODL EITM ETYP BIDS BAMT INAM WNAM NAM9 NAM8 DATA DNAM CRDT VNAM
    """
    subs = _common_header_subs(rec, obnd_sig='WEAP')
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', _prefix_path(model))

    # EITM — Object Effect (enchantment)
    enam = get_formid(rec, 'ENAM')
    if enam:
        subs += pack_formid_subrecord('EITM', enam)

    # Resolve anim type early — needed for all per-type lookups
    tes4_type = get_int(rec, 'DATA.Type')
    anim_type = WEAPON_TYPE_MAP.get(tes4_type, 1)

    # Refine Blunt 1H (TES4 type 2 → default Mace=4) to WarAxe (3) when the
    # mesh path indicates an axe.  Skyrim's behavior graph uses AnimationType
    # to drive equip/draw animations: Mace (4) looks for the weapon at the
    # WeaponMace skeleton node, while WarAxe (3) uses WeaponAxe.  Our NIF
    # converter already sets Prn=WeaponAxe for axe meshes, so a Mace type
    # makes the draw animation unable to find the weapon → invisible when held.
    if tes4_type == 2 and anim_type == 4:  # Blunt 1H
        modl_lower = model.lower().replace('\\', '/')
        if 'waraxe' in modl_lower or '/axe' in modl_lower or '_axe' in modl_lower:
            anim_type = 3  # WarAxe

    # Refine Blade 1H (TES4 type 0 → default Sword=1) to Dagger (2) when the
    # mesh is a dagger.  The NIF converter sets Prn=WeaponDagger for these
    # (same filename keyword), and Prn must agree with AnimationType or the
    # weapon is invisible when drawn.  Shortswords stay Sword/WeaponSword.
    if tes4_type == 0 and anim_type == 1:
        # basename only — must mirror _remap_prn() in asset_convert/nif_converter.py
        basename = model.lower().replace('\\', '/').rsplit('/', 1)[-1]
        if 'dagger' in basename:
            anim_type = 2  # Dagger

    # ETYP — Equipment Type (EQUP FormID): determines which hand slot is used
    subs += pack_formid_subrecord('ETYP', WEAPON_ANIM_EQUP.get(anim_type, 0x00013F42))

    # BIDS — Block Bash Impact Data Set
    subs += pack_formid_subrecord('BIDS', WEAPON_ANIM_BIDS.get(anim_type, 0x000183FF))

    # BAMT — Block Material
    subs += pack_formid_subrecord('BAMT', WEAPON_ANIM_BAMT.get(anim_type, 0x000774C2))

    # KSIZ/KWDA — vendor keyword (TES4 type 4 = Staff)
    subs += pack_keywords([VENDOR_KYWD['Staff' if tes4_type == 4 else 'Weapon']])

    # INAM — Impact Data Set (hit effects/particles)
    subs += pack_formid_subrecord('INAM', WEAPON_ANIM_INAM.get(anim_type, 0x00013CAC))

    # WNAM — 1st-person model STAT reference.
    # We create a companion STAT record containing the same mesh as the world model.
    # Oblivion has no separate hi-poly 1st-person weapon meshes.
    wnam_fid = 0
    if model and writer is not None:
        edid = get_str(rec, 'EditorID', '')
        wnam_fid = writer.alloc_formid()
        stat_bytes = _build_weapon_1stperson_stat(edid, _prefix_path(model), wnam_fid)
        writer.add_record('STAT', stat_bytes)
    if wnam_fid:
        subs += pack_formid_subrecord('WNAM', wnam_fid)

    # NAM9 — Draw sound descriptor FormID (must come BEFORE DATA)
    subs += pack_formid_subrecord('NAM9', WEAPON_ANIM_NAM9.get(anim_type, 0x0003C72E))

    # NAM8 — Sheathe sound descriptor FormID (must come BEFORE DATA)
    subs += pack_formid_subrecord('NAM8', WEAPON_ANIM_NAM8.get(anim_type, 0x0003C72F))

    # TES5 WEAP DATA: Value(4) + Weight(4) + Damage(2) = 10 bytes
    speed = get_float(rec, 'DATA.Speed', 1.0)
    reach = get_float(rec, 'DATA.Reach', 1.0)
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    damage = get_int(rec, 'DATA.Damage')
    subs += pack_subrecord('DATA', struct.pack('<IfH', value, weight, damage))

    # DNAM — weapon parameters (100 bytes)
    dnam = bytearray(100)
    struct.pack_into('<B', dnam, 0, anim_type)
    struct.pack_into('<f', dnam, 4, WEAPON_ANIM_MULT.get(anim_type, 1.0))   # animationMultiplier
    struct.pack_into('<f', dnam, 8, reach if reach > 0.0 else 1.0)             # Reach (0.0 is invalid; default to 1.0)
    struct.pack_into('<I', dnam, 12, WEAPON_ANIM_FLAGS.get(anim_type, 0))   # Flags
    struct.pack_into('<f', dnam, 44, speed)                                  # Speed (animationAttackMult slot)
    struct.pack_into('<B', dnam, 76, WEAPON_ANIM_STAGGER.get(anim_type, 0)) # Stagger
    subs += pack_subrecord('DNAM', bytes(dnam))

    # CRDT — Critical data (24 bytes for SSE, form version 44)
    subs += pack_subrecord('CRDT', b'\x00' * 24)

    # VNAM — Violence type
    subs += pack_subrecord('VNAM', struct.pack('<I', WEAPON_ANIM_VNAM.get(anim_type, 1)))

    return pack_record('WEAP', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_ARMO(rec: dict, is_clothing: bool = False, writer=None) -> bytes:
    """Convert ARMO or CLOT → ARMO.

    TES5 order: EDID OBND FULL EITM EAMT MOD2 ICON MOD4 ICO2 BOD2
    DEST YNAM ZNAM BMCT ETYP BIDS BAMT RNAM KSIZ KWDA DESC MODL[] DATA DNAM TNAM

    When writer is provided, generates a companion ARMA record and references it.
    """
    subs = _common_header_subs(rec, obnd_sig='ARMO')

    # EITM — Object Effect (enchantment) — NOT ENAM
    enam = get_formid(rec, 'ENAM')
    if enam:
        subs += pack_formid_subrecord('EITM', enam)

    # MOD2 — Male world model (ground/dropped item mesh)
    # TES5 ground models use a separate GND mesh; fall back to biped model
    male_model = get_str(rec, 'Male.BipedModel.MODL')
    male_world = get_str(rec, 'Male.WorldModel.MODL')
    ground_model = male_world if male_world else male_model
    if ground_model:
        subs += pack_string_subrecord('MOD2', _prefix_path(ground_model))

    # MOD4 — Female world model (if different)
    female_world = get_str(rec, 'Female.WorldModel.MODL')
    if female_world:
        subs += pack_string_subrecord('MOD4', _prefix_path(female_world))

    # BOD2 (Biped Object Data) replaces BMDT
    # ArmorType enum: 0=Light Armor, 1=Heavy Armor, 2=Clothing
    tes4_biped = get_int(rec, 'BMDT.BipedFlags')
    tes5_biped = _convert_biped_flags(tes4_biped)
    if is_clothing:
        armor_type = 2  # Clothing
    else:
        gen_flags = get_int(rec, 'BMDT.GeneralFlags')
        # TES4 bit 7 (0x80) = Heavy Armor (from wbDefinitionsTES4.pas)
        armor_type = 1 if gen_flags & 0x80 else 0  # Heavy=1, Light=0
    subs += pack_subrecord('BOD2', struct.pack('<II', tes5_biped, armor_type))

    # ETYP — Equip type for shields (required for equip-to-left-hand)
    is_shield = bool(tes4_biped & (1 << 13))
    if is_shield:
        subs += pack_formid_subrecord('ETYP', SHIELD_EQUIP_TYPE)

    # RNAM — Race (DefaultRace)
    subs += pack_formid_subrecord('RNAM', 0x00000019)

    # KSIZ/KWDA — vendor keyword: rings (TES4 bits 6/7) and amulets (bit 8)
    # are jewelry; otherwise clothing vs armor by armor type.
    if tes4_biped & 0x01C0:
        vendor_kwd = 'Jewelry'
    else:
        vendor_kwd = 'Clothing' if is_clothing else 'Armor'
    subs += pack_keywords([VENDOR_KYWD[vendor_kwd]])

    # MODL[] — Armature (ARMA references): generate ARMA companion record
    if writer is not None and male_model:
        arma_fid = writer.alloc_formid()
        arma_bytes = _build_arma(rec, arma_fid, tes5_biped, armor_type,
                                 is_shield=is_shield)
        writer.add_record('ARMA', arma_bytes)
        subs += pack_formid_subrecord('MODL', arma_fid)

    # DATA: Value(4) + Weight(4) = 8 bytes in TES5
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    subs += pack_subrecord('DATA', struct.pack('<If', value, weight))

    # DNAM — Armor rating as S32
    rating = get_int(rec, 'DATA.ArmorRating') if not is_clothing else 0
    subs += pack_subrecord('DNAM', struct.pack('<i', rating))

    return pack_record('ARMO', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def _build_arma(rec: dict, arma_fid: int, tes5_biped: int, armor_type: int,
                is_shield: bool = False) -> bytes:
    """Build an ARMA (Armor Addon) companion record for an ARMO.

    ARMA holds the actual worn mesh models.
    Order: EDID BOD2 RNAM DNAM MOD2 MOD3 [SNDD] MODL[]
    """
    subs = b''
    edid = get_str(rec, 'EditorID', '')
    subs += pack_string_subrecord('EDID', edid + '_AA')

    # BOD2 — body coverage flags (may be wider than the ARMO's equipment slot).
    # ARMA declares which body regions the mesh covers, e.g. a cuirass mesh
    # covers Body + ForeArms + Calves even though the ARMO only claims "Body".
    arma_biped = tes5_biped
    # Clothing shirts should NOT claim ForeArms — their sleeves are SBP_32_BODY
    # geometry that should remain visible when gloves are equipped.
    # Shoes should NOT claim Calves — only boots (armor foot items) should.
    is_clothing = (armor_type == 2)
    male_model = get_str(rec, 'Male.BipedModel.MODL', '').lower()
    is_boot = ('boot' in male_model)
    for bit, extras in ARMA_BODY_COVERAGE_EXTRA.items():
        if arma_biped & (1 << bit):
            for extra_bit in extras:
                # Skip ForeArms(4) for clothing — sleeves stay visible with gloves
                if is_clothing and extra_bit == 4:
                    continue
                # Skip Calves(8) for shoes (foot items without 'boot' in path)
                if extra_bit == 8 and bit == 7 and not is_boot and is_clothing:
                    continue
                arma_biped |= (1 << extra_bit)
    subs += pack_subrecord('BOD2', struct.pack('<II', arma_biped, armor_type))

    # RNAM — Race (must match parent ARMO)
    subs += pack_formid_subrecord('RNAM', 0x00000019)

    # Weight-slider morphing follows the vanilla convention: ONLY gear
    # covering body/hands/feet uses it (ARMA path <name>_1.nif + slider
    # enabled; engine lerps the _0/_1 pair by actor weight).  Vanilla
    # helmets and shields have the slider DISABLED and a plain path
    # (IronShieldAA / IronHelmetAA), and rigid PRN pieces must never be
    # weight-morphed.  TES4 biped bits: 2=UpperBody 3=LowerBody 4=Hand 5=Foot.
    tes4_biped_flags = get_int(rec, 'BMDT.BipedFlags')
    use_slider = bool(tes4_biped_flags & 0b111100)

    # DNAM — ARMA-specific data (12 bytes)
    # Priority M(U8) + Priority F(U8) + WeightSlider M(U8) + WeightSlider F(U8)
    # + pad(2) + DetectionSoundValue(U8) + pad(U8) + WeaponAdjust(float)
    # Weight slider: 0x02=enabled (vanilla convention)
    # Priority: 10 matches vanilla Skyrim iron armor
    slider = 2 if use_slider else 0
    dnam = struct.pack('<BBBBHBBf', 10, 10, slider, slider, 0, 0, 0, 0.0)
    subs += pack_subrecord('DNAM', dnam)

    def _weighted(path: str) -> str:
        p = _prefix_path(path)
        if use_slider and p.lower().endswith('.nif'):
            return p[:-4] + '_1.nif'
        return p

    # MOD2 — Male biped model (the actual worn mesh)
    male_model = get_str(rec, 'Male.BipedModel.MODL')
    if male_model:
        subs += pack_string_subrecord('MOD2', _weighted(male_model))

    # MOD3 — Female biped model
    female_model = get_str(rec, 'Female.BipedModel.MODL')
    if female_model:
        subs += pack_string_subrecord('MOD3', _weighted(female_model))
    elif male_model:
        # Fall back to male model for female
        subs += pack_string_subrecord('MOD3', _weighted(male_model))

    # MODL[] — Additional Races that can equip this armor addon.
    # Per TES5 record definition: MODL (Additional Races) comes BEFORE SNDD.
    for race_fid in ARMA_ADDITIONAL_RACES:
        subs += pack_formid_subrecord('MODL', race_fid)

    # SNDD — Footstep sound (boots need footstep set)
    tes4_biped = get_int(rec, 'BMDT.BipedFlags')
    is_feet = bool(tes4_biped & (1 << 5))   # TES4 bit 5 = Foot
    if is_feet:
        if armor_type == 1:  # Heavy
            subs += pack_formid_subrecord('SNDD', HEAVY_ARMOR_FOOTSTEP_SET)
        elif armor_type == 0:  # Light
            subs += pack_formid_subrecord('SNDD', LIGHT_ARMOR_FOOTSTEP_SET)
        else:  # Clothing
            subs += pack_formid_subrecord('SNDD', CLOTHING_FOOTSTEP_SET)

    return pack_record('ARMA', arma_fid, 0, subs)


def convert_CLOT(rec: dict, writer=None) -> bytes:
    """CLOT → ARMO with armor type = Clothing."""
    return convert_ARMO(rec, is_clothing=True, writer=writer)


def _build_arrow_proj(edid: str, model_path: str, speed: float, proj_fid: int) -> bytes:
    """Build a minimal PROJ record for a converted arrow.

    TES5 PROJ order: EDID OBND FULL MODL DATA NAM1 VNAM
    DATA (92 bytes) layout per wbDefinitionsTES5.pas, values matched to
    vanilla ArrowIronProjectile (0003BE11):
      {00} Flags(U16) {02} Type(U16) {04} Gravity(f) {08} Speed(f) {12} Range(f)
      {16} Light {20} MuzzleFlashLight {24} TracerChance(f)
      {28} ExplAltTrigProximity(f) {32} ExplAltTrigTimer(f) {36} Explosion
      {40} Sound {44} MuzzleFlashDuration(f) {48} FadeDuration(f)
      {52} ImpactForce(f) {56} SoundCountdown {60} SoundDisable
      {64} DefaultWeaponSource {68} ConeSpread(f) {72} CollisionRadius(f)
      {76} Lifetime(f) {80} RelaunchInterval(f) {84} DecalData {88} CollisionLayer
    Type is a bit value: Arrow = 0x40 (NOT an ordinal — 7 would be
    Missile|Lobber|Beam and the engine spawns no usable projectile).
    Flags 0x00C0 = Can Be Picked Up + Supersonic (as ArrowIronProjectile).
    """
    subs = b''
    subs += pack_string_subrecord('EDID', edid + 'Projectile')
    subs += pack_obnd()
    subs += pack_string_subrecord('MODL', model_path)

    # Scale TES4 normalised speed (0-1) to TES5 units/sec (~3600 for iron arrow)
    # TES4 speed 1.0 → TES5 3600; apply proportionally with a minimum of 500
    tes5_speed = max(500.0, speed * 3600.0)

    data = bytearray(92)
    struct.pack_into('<H', data, 0, 0x00C0)          # Flags: CanBePickedUp|Supersonic
    struct.pack_into('<H', data, 2, 0x40)            # Type: Arrow
    struct.pack_into('<f', data, 4, 0.35)            # Gravity
    struct.pack_into('<f', data, 8, tes5_speed)      # Speed
    struct.pack_into('<f', data, 12, 60000.0)        # Range
    struct.pack_into('<I', data, 40, 0x0003F2B4)     # Sound: WPNBowProjectileSD
    struct.pack_into('<f', data, 48, 5.0)            # Fade Duration
    struct.pack_into('<f', data, 52, 1.0)            # Impact Force
    struct.pack_into('<f', data, 72, 0.5)            # Collision Radius
    struct.pack_into('<f', data, 80, 0.25)           # Relaunch Interval
    subs += pack_subrecord('DATA', bytes(data))
    # NAM1 — muzzle flash model filename (empty)
    subs += pack_string_subrecord('NAM1', '')
    # VNAM — sound level (1 = normal)
    subs += pack_subrecord('VNAM', struct.pack('<I', 1))
    return pack_record('PROJ', proj_fid, 0, subs)


def convert_AMMO(rec: dict, writer=None) -> bytes:
    subs = _common_header_subs(rec, obnd_sig='AMMO')
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', _prefix_path(model))

    damage = get_int(rec, 'DATA.Damage')
    value = get_int(rec, 'DATA.Value')
    flags = get_int(rec, 'DATA.Flags')
    weight = get_float(rec, 'DATA.Weight')
    speed = get_float(rec, 'DATA.Speed', 1.0)

    # Build a companion PROJ record so this arrow has its own projectile.
    # TES4 has no separate PROJ records; we synthesise one per AMMO.
    if writer is not None:
        edid = get_str(rec, 'EditorID', '')
        proj_fid = writer.alloc_formid()
        proj_model = _prefix_path(model) if model else _prefix_path('Weapons\\Iron\\Arrow.NIF')
        proj_bytes = _build_arrow_proj(edid, proj_model, speed, proj_fid)
        writer.add_record('PROJ', proj_bytes)
    else:
        proj_fid = DEFAULT_ARROW_PROJECTILE

    # YNAM/ZNAM — pickup/putdown sounds (as vanilla arrows: ITMGenericWeaponUp/Down)
    subs += pack_formid_subrecord('YNAM', 0x0003E7B7)
    subs += pack_formid_subrecord('ZNAM', 0x0003E877)

    # KSIZ/KWDA — vendor keyword (weapon vendors' list includes Arrow)
    subs += pack_keywords([VENDOR_KYWD['Arrow']])

    # TES5 AMMO DATA (SSE, 20 bytes): Projectile(FormID) Flags(U32) Damage(float) Value(U32) Weight(float)
    # Flags: bit 0 = Ignores Normal Weapon Resistance (carried over from TES4),
    # bit 2 = Non-Bolt — REQUIRED or the engine classifies the ammo as a
    # crossbow bolt (TES4 has no bolts; everything converts as an arrow).
    tes5_flags = (flags & 0x01) | 0x04
    data = struct.pack('<IIfIf', proj_fid, tes5_flags, float(damage), value, weight)
    subs += pack_subrecord('DATA', data)
    subs += pack_string_subrecord('ONAM', '')  # Short name

    return pack_record('AMMO', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


# Oblivion numeric font faces (SFontFile_N in Oblivion.ini) → Skyrim named
# fonts (Interface\fontconfig.txt).  Skyrim's Scaleform BookMenu only knows
# fonts by their $-alias — a numeric <font face=N> resolves to no font at all
# and the book renders with NO VISIBLE TEXT.
#   1 = Kingthings Regular (book body) → $SkyrimBooks
#   2 = Kingthings Shadowed           → $SkyrimBooks
#   3 = Tahoma Bold Small (UI)        → $SkyrimBooks
#   4 = Daedric                       → $DaedricFont
#   5 = Handwritten                   → $HandwrittenFont
_OBLIVION_FACE_TO_SKYRIM_FONT = {
    '1': '$SkyrimBooks',
    '2': '$SkyrimBooks',
    '3': '$SkyrimBooks',
    '4': '$DaedricFont',
    '5': '$HandwrittenFont',
}


def _fix_book_html(text: str) -> str:
    """Update Oblivion book HTML for Skyrim's Scaleform BookMenu compatibility.

    Applies three fixes in order:
    1. Replace Oblivion numeric <font face=N> tags with Skyrim's named fonts
       (see _OBLIVION_FACE_TO_SKYRIM_FONT) and strip other attributes.
    2. Rewrite IMG src paths: Oblivion paths are relative to
       Textures\\Menus\\ (e.g. "Book/foo.dds"); Skyrim Scaleform needs the
       img:// scheme with a full Data-relative path
       (img://textures/tes4/menus/book/foo.dds).
    3. Turn bare \\r\\n line breaks into <br> where Oblivion authors used raw
       newlines as visual breaks.
    """
    # 1. Remap <font face=N> / </font> to Skyrim named fonts.
    text = re.sub(r'<(/?)[Ff][Oo][Nn][Tt](\s[^>]*)?>', _remap_font_tag, text)

    # 2. Rewrite IMG src to the img:// scheme + converted texture path.
    def _prefix_img(m):
        path = m.group(2).replace('\\', '/')
        if not path.lower().startswith('img://'):
            path = 'img://textures/tes4/menus/' + path.lstrip('/')
        return f"<img src='{path}'"
    # Match opening quote, path, and consume the matching closing quote.
    text = re.sub(r'<IMG\s+src=(["\']?)([^"\'>\s]+)\1', _prefix_img, text, flags=re.IGNORECASE)

    # 3. Replace bare \r\n sequences (not already preceded by <br>) with <br>.
    text = re.sub(r'(?<!>)\r\n', '<br>\r\n', text)

    return text


def _remap_font_tag(m: re.Match) -> str:
    """Replace an Oblivion <font ...> tag with a Skyrim-compatible version.

    Preserves close tags (</font>).  For open tags, maps the numeric face to
    the equivalent Skyrim named font and strips all other attributes (color,
    size, etc.) that Skyrim's Scaleform BookMenu doesn't handle safely.
    """
    slash = m.group(1)   # '/' for close tag, '' for open
    attrs = m.group(2) or ''
    if slash:
        return '</font>'
    face_m = re.search(r'face\s*=\s*["\']?(\d)', attrs, flags=re.IGNORECASE)
    face = face_m.group(1) if face_m else '1'
    font = _OBLIVION_FACE_TO_SKYRIM_FONT.get(face, '$SkyrimBooks')
    return f"<font face='{font}'>"


def convert_BOOK(rec: dict, writer=None) -> bytes:
    # TES5 BOOK field order: EDID OBND FULL MODL DESC DATA INAM CNAM
    subs = _common_header_subs(rec, obnd_sig='BOOK')
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', _prefix_path(model))
    desc = get_str(rec, 'DESC')
    if desc:
        desc = _fix_book_html(desc)
        subs += pack_string_subrecord('DESC', desc)

    # TES5 BOOK DATA (16 bytes): Flags(U8) Type(U8) pad(2) Teaches(S32) Value(U32) Weight(float)
    flags = get_int(rec, 'DATA.Flags')
    teaches_tes4 = get_int(rec, 'DATA.Teaches', -1)
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')

    # Map TES4 skill index to TES5
    tes5_flags = 0
    teaches_tes5 = -1  # -1 = None
    if teaches_tes4 >= 0 and teaches_tes4 in TES4_SKILL_TO_TES5_INDEX:
        teaches_tes5 = TES4_SKILL_TO_TES5_INDEX[teaches_tes4]
        tes5_flags |= 0x01  # Teaches Skill
    if flags & 0x02:  # Can't be taken
        tes5_flags |= 0x02

    # KSIZ/KWDA — vendor keyword (TES4 flag 0x01 = Scroll)
    subs += pack_keywords([VENDOR_KYWD['Scroll' if flags & 0x01 else 'Book']])

    # Type: 0 = Book/Tome, 255 = Note/Scroll (TES4 flag 0x01 = Scroll)
    book_type = 255 if flags & 0x01 else 0
    data = struct.pack('<BBHiIf', tes5_flags, book_type, 0, teaches_tes5, value, weight)
    subs += pack_subrecord('DATA', data)

    # INAM — Inventory Art (STAT).  BookMenu null-derefs without it (in-game
    # crash on reading any book), so it must always be present.  Pointing it
    # at a vanilla stand-in (HighPolySkyrimBook) shows the default Skyrim
    # cover in the inventory, so we synthesise a per-book STAT wrapping the
    # book's own converted mesh instead.
    inam_fid = 0x000E894C  # HighPolySkyrimBook — fallback for model-less books
    if writer is not None and model:
        edid = get_str(rec, 'EditorID', '')
        inam_fid = writer.alloc_formid()
        stat_bytes = _build_model_stat('InvArt_' + edid, _prefix_path(model), inam_fid)
        writer.add_record('STAT', stat_bytes)
    subs += pack_formid_subrecord('INAM', inam_fid)

    # CNAM — Description (string, empty like vanilla non-descriptive books).
    subs += pack_string_subrecord('CNAM', '')

    return pack_record('BOOK', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_ENCH(rec: dict, writer=None) -> bytes:
    """ENCH — Enchantment. ENIT completely restructured for TES5."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd()
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    # ENIT (36 bytes in TES5)
    tes4_type = get_int(rec, 'ENIT.Type')
    charge = get_int(rec, 'ENIT.Charge', 100)
    cost = get_int(rec, 'ENIT.Cost')
    tes4_flags = get_int(rec, 'ENIT.Flags')

    tes5_type = ENCH_TYPE_MAP.get(tes4_type, 6)
    cast_type = ENCH_CAST_TYPE_MAP.get(tes4_type, 2)
    # Target type from first effect
    target_type = 0  # Self
    first_effect_type = get_str(rec, 'Effect[0].Type')
    if first_effect_type == 'Touch':
        target_type = 1
    elif first_effect_type == 'Target':
        target_type = 2

    tes5_flags = 0
    if tes4_flags & 0x08:  # No Auto-Calc
        tes5_flags |= 0x01

    enit = bytearray(36)
    struct.pack_into('<I', enit, 0, cost)          # Enchantment cost
    struct.pack_into('<I', enit, 4, tes5_flags)    # Flags
    struct.pack_into('<I', enit, 8, cast_type)     # Cast Type
    struct.pack_into('<I', enit, 12, charge)       # Charge Amount
    struct.pack_into('<I', enit, 16, target_type)  # Target Type
    struct.pack_into('<I', enit, 20, tes5_type)    # Enchantment Type
    struct.pack_into('<f', enit, 24, 0.0)          # Charge Time
    # BaseEnchantment FormID at 28 = 0
    # WornRestrictions at 32 = 0
    subs += pack_subrecord('ENIT', bytes(enit))

    # Effects — TES5 uses EFID(FormID) + EFIT(Magnitude/Area/Duration)
    subs += _pack_effects(rec, delivery=target_type, writer=writer)

    return pack_record('ENCH', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_SPEL(rec: dict, writer=None) -> bytes:
    """SPEL — Spell. SPIT restructured for TES5."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd()
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    # SPIT (36 bytes in TES5)
    cost = get_int(rec, 'SPIT.Cost')
    tes4_flags = get_int(rec, 'SPIT.Flags')
    tes4_type = get_int(rec, 'SPIT.Type')

    # TES5 spell types: 0=Spell, 1=Disease, 2=Power, 3=Lesser Power, 4=Ability, 10=Addiction, 11=Voice
    # TES4: 0=Spell, 1=Disease, 2=Power, 3=Lesser Power, 4=Ability
    tes5_type = tes4_type if tes4_type <= 4 else 0

    # Target from first effect
    target_type = 0
    first_effect_type = get_str(rec, 'Effect[0].Type')
    if first_effect_type == 'Touch':
        target_type = 1
    elif first_effect_type == 'Target':
        target_type = 2

    tes5_flags = 0
    if tes4_flags & 0x10:
        tes5_flags |= 0x80000    # No Absorb/Reflect
    if tes4_flags & 0x20:
        tes5_flags |= 0x100000   # No Dual Cast
    if tes4_flags & 0x40:
        tes5_flags |= 0x200000

    spit = bytearray(36)
    struct.pack_into('<I', spit, 0, cost)          # Cost
    struct.pack_into('<I', spit, 4, tes5_flags)    # Flags
    struct.pack_into('<I', spit, 8, tes5_type)     # Type
    struct.pack_into('<f', spit, 12, 0.0)          # Charge Time
    # Cast Type 1 = Fire and Forget (wbCastEnum; 2 would be Concentration —
    # verified against vanilla Firebolt: SPIT.CastType=1).
    struct.pack_into('<I', spit, 16, 1)
    struct.pack_into('<I', spit, 20, target_type)  # Delivery
    struct.pack_into('<f', spit, 24, 0.0)          # Cast Duration
    struct.pack_into('<f', spit, 28, 0.0)          # Range
    # Half-cost Perk FormID at 32 = 0
    subs += pack_subrecord('SPIT', bytes(spit))

    # Effects
    subs += _pack_effects(rec, delivery=target_type, writer=writer)

    return pack_record('SPEL', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_ALCH(rec: dict) -> bytes:
    subs = _common_header_subs(rec, obnd_sig='ALCH')

    tes4_flags = get_int(rec, 'ENIT.Flags')
    full = get_str(rec, 'FULL', '').lower()
    is_poison = 'poison' in full
    is_food = bool(tes4_flags & 0x02)

    # KSIZ/KWDA — vendor keyword (after FULL per vanilla ALCH order)
    kwd = 'Poison' if is_poison else ('Food' if is_food else 'Potion')
    subs += pack_keywords([VENDOR_KYWD[kwd]])

    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', _prefix_path(model))

    weight = get_float(rec, 'DATA.Weight')
    subs += pack_float_subrecord('DATA', weight)

    # ENIT (Potion) — TES5: Cost(4) + PrimaryFlags(4) + PrimaryEffect(4) +
    #   UseSound(4) + pad(4) = 20 bytes
    value = get_int(rec, 'ENIT.Value')
    tes5_flags = 0
    if tes4_flags & 0x01:  # No auto-calc → Manual Calc
        tes5_flags |= 0x01
    if is_poison:
        tes5_flags |= 0x20000  # Poison (bit 17)
    elif is_food:
        tes5_flags |= 0x02
    enit = struct.pack('<IIIII', value, tes5_flags, 0, 0, 0)
    subs += pack_subrecord('ENIT', enit)

    # Effects
    subs += _pack_effects(rec)

    return pack_record('ALCH', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_INGR(rec: dict) -> bytes:
    subs = _common_header_subs(rec, obnd_sig='INGR')

    # KSIZ/KWDA — vendor keyword (TES4 food is sold by ingredient vendors)
    subs += pack_keywords([VENDOR_KYWD['Ingredient']])

    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', _prefix_path(model))

    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    subs += pack_subrecord('DATA', struct.pack('<If', value, weight))

    # ENIT — TES5 INGR: IngredientValue(s32) + Flags(u32), 8 bytes
    # (unlike ALCH's 20). TES4 flag bits 0x01 no-autocalc / 0x02 food match.
    enit_flags = get_int(rec, 'ENIT.Flags') & 0x03
    subs += pack_subrecord('ENIT', struct.pack('<iI', value, enit_flags))

    # Effects (TES5 ingredients have exactly 4)
    subs += _pack_effects(rec, pad_to=4)

    return pack_record('INGR', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_SGST(rec: dict, writer=None) -> bytes:
    """Sigil Stone → SCRL (Scroll, closest equivalent).

    TES5 SCRL order: EDID OBND FULL KSIZ KWDA DESC MODL DATA SPIT EFID/EFIT
    """
    subs = _common_header_subs(rec, obnd_sig='SCRL')

    # KSIZ/KWDA — vendor keyword
    subs += pack_keywords([VENDOR_KYWD['Scroll']])

    # MDOB (vanilla scroll world model) + ETYP (Either Hand) — vanilla scrolls
    # all carry both; without ETYP the scroll cannot be equipped/cast.
    subs += pack_formid_subrecord('MDOB', 0x00076E8F)
    subs += pack_formid_subrecord('ETYP', 0x00013F44)

    # DESC before MODL per TES5 spec
    desc = get_str(rec, 'DESC', '')
    subs += pack_string_subrecord('DESC', desc)

    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', _prefix_path(model))

    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    subs += pack_subrecord('DATA', struct.pack('<If', value, weight))

    # SPIT — same 36-byte layout as SPEL. CastType 3 = Scroll (matches every
    # vanilla SCRL); Delivery from the first effect like SPEL.
    target_type = 0
    first_effect_type = get_str(rec, 'Effect[0].Type')
    if first_effect_type == 'Touch':
        target_type = 1
    elif first_effect_type == 'Target':
        target_type = 2
    spit = struct.pack('<IIIfIIff4x', 0, 0, 0, 0.0, 3, target_type, 0.0, 0.0)
    subs += pack_subrecord('SPIT', spit)

    # Effects — a sigil stone's effects were what it enchanted with in TES4;
    # as a scroll they become its cast payload. Without them the record is a
    # dead item (CK: "Magic Item ... has no effects defined", one per stone).
    subs += _pack_effects(rec, delivery=target_type, writer=writer)

    return pack_record('SCRL', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_APPA(rec: dict) -> bytes:
    """Apparatus → MISC (no apparatus in TES5)."""
    subs = _common_header_subs(rec, obnd_sig='MISC')
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', _prefix_path(model))
    subs += pack_keywords([VENDOR_KYWD['Clutter']])
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    subs += pack_subrecord('DATA', struct.pack('<If', value, weight))
    return pack_record('MISC', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


# ---------------------------------------------------------------------------
# Actor converters
# ---------------------------------------------------------------------------

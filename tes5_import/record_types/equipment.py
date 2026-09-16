"""Equipment converters: WEAP, ARMO, CLOT, AMMO, BOOK, ENCH, SPEL, ALCH, INGR, SGST, APPA."""

from asset_convert.game_paths import current_namespace
import re
import struct

from ..base.constants import ENCH_CAST_TYPE_MAP, ENCH_TYPE_MAP, WEAPON_TYPE_MAP, ARMA_BODY_COVERAGE_EXTRA
from ..actors.magic_effects import aimed_variant, has_projectile
from ..base.equivalents import (
    ARMA_ADDITIONAL_RACES,
    ARMA_ADDITIONAL_RACES_NONBEAST,
    ARMA_BEAST_RACES,
    CLOTHING_FOOTSTEP_SET,
    DEFAULT_ARROW_PROJECTILE,
    HEAVY_ARMOR_FOOTSTEP_SET,
    LIGHT_ARMOR_FOOTSTEP_SET,
    MGEF_AV_CODE_TO_SKYRIM,
    MGEF_CODE_TO_SKYRIM,
    SHIELD_EQUIP_TYPE,
    SPELL_EQUIP_EITHER_HAND,
    SPELL_TYPE_EQUIP_TYPE,
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
from .equipment_falloutnv import ammo_flags, gun_speed
from .equipment_falloutnv import refine_anim_type as refine_fallout_anim_type
from .projectile_falloutnv import (ammo_projectile, gun_sheathe_sounds,
                                   gun_sound_subs)
from .common import (
    VENDOR_KYWD,
    _common_header_subs,
    _convert_biped_flags,
    prefix_path,
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


from output_layout import assets_for

def _resolve_mgef(code: str, actor_value: int = -1, script_fid: str = '',
                  effect_type: str = '') -> int:
    """Map one TES4 effect instance to the MGEF FormID it should reference.

    Since the MGEF converter landed, the plugin emits its OWN magic effect for
    every TES4 code, so that is the first and normal answer.  Three lookups,
    most specific first:

    1. A **script-effect variant** — SEFF names its script per effect, and
       Skyrim keeps the script on the MGEF, so each distinct script has its own
       record (see magic.build_seff_variants).
    2. A **per-actor-value variant** — DGAT/FOSK/... are parameterised by the
       effect's own ActorValue, which Skyrim moved into the MGEF.
    3. The plugin's plain MGEF for the code.

    The vanilla-alias tables remain only as a fallback for a plugin whose MGEF
    records were not exported (an override plugin that redefines no effects),
    where there is no record of ours to point at.
    """
    from .magic import get_mgef_formid, get_seff_variant

    if code == 'SEFF' and script_fid:
        fid = get_seff_variant(script_fid, effect_type)
        if fid:
            return fid

    fid = get_mgef_formid(code, actor_value)
    if fid:
        return fid

    per_av = MGEF_AV_CODE_TO_SKYRIM.get(code)
    if per_av is not None:
        vanilla = per_av.get(actor_value)
        if vanilla:
            return vanilla
    return MGEF_CODE_TO_SKYRIM.get(code, 0)


def _bound_script_for(mgef_fid: int, writer, uncastable: bool) -> int:
    """Scripted stand-in for a bound-item MGEF, or 0 to keep the native path.

    Two independent reasons a bound effect cannot use Skyrim's own archetype 17:

    * **The item is armor.**  Skyrim has no bound armor at all — every one of
      the seven vanilla archetype-17 effects names a WEAP, none an ARMO — so a
      converted bound cuirass/greaves/helmet is inert under the native path
      regardless of how it is delivered.  Oblivion's whole BA**/Mythic Dawn
      family lands here.
    * **The spell never casts.**  An Ability or Lesser Power is applied
      passively, and BoundItemEffect only fires on a cast, so even a bound
      WEAPON dies when delivered that way.

    A bound weapon on a normal castable spell keeps the engine's own
    implementation, which is better than any script.

    The item the script conjures is the source MGEF's own Assoc. Item, already
    resolved to an output WEAP/ARMO by the MGEF pass — reusing it means the
    script and the native archetype always agree on what gets equipped.
    """
    from .magic import bound_assoc_is_armor, bound_item_assoc, bound_script_variant

    assoc = bound_item_assoc(mgef_fid)
    if not assoc:
        return 0
    if not uncastable and not bound_assoc_is_armor(mgef_fid):
        return 0
    return bound_script_variant(mgef_fid, assoc, writer)


# TES4 ENCH records by raw FormID (uppercase hex), for the enchanted-book →
# scroll conversion: Skyrim's SCRL carries its effects DIRECTLY, so a book
# whose ENAM names an enchantment needs that enchantment's effect list copied
# onto it.  Registered by import_main before the record pass; includes the
# masters' enchantments, since a dependent plugin's scrolls usually name one.
_ENCH_BY_FID: dict = {}


def set_ench_index(ench_records) -> None:
    """Index TES4 ENCH records by raw FormID for enchanted-book conversion."""
    _ENCH_BY_FID.clear()
    for rec in ench_records:
        fid = (rec.get('FormID') or '').upper()
        if fid:
            _ENCH_BY_FID[fid] = rec


# Harmless zero-magnitude filler effects used when a record would otherwise
# have no (or too few) effects. A null EFID hard-crashes the inventory menu,
# so we must always reference a real MGEF.
_FILLER_EFFECTS = (0x0003EB15, 0x0003EB17, 0x0003EB16, 0x0003EAF3)  # AlchRestore{Health,Magicka,Stamina}, AlchFortifyHealth


def _pack_effects(rec: dict, count_key: str = 'EffectCount', pad_to: int = 0,
                  delivery: int = 0, writer=None, uncastable: bool = False) -> bytes:
    """Pack EFID/EFIT pairs for all effects on a record.

    Effects with no TES5 equivalent are dropped — an EFID of 0 (null MGEF)
    crashes the game as soon as the item's card is shown in a menu. If all
    effects are dropped, or pad_to demands more (e.g. 4 for INGR), real
    zero-magnitude filler effects are used.

    ``delivery`` is the owning record's delivery (2 = Aimed): an aimed magic
    item fires the projectile of its effects' MGEFs, so if none of them has
    one the item casts NOTHING in game.  In that case the first effect is
    swapped for a synthesized aimed clone (see magic_effects.aimed_variant).

    Bound-item effects are re-pointed at a scripted stand-in whenever the
    engine's own archetype 17 cannot serve them — always for bound ARMOR
    (Skyrim implements bound weapons only), and for any bound item on a
    never-cast spell.  ``uncastable`` marks that second case: a spell the
    engine APPLIES rather than casts (an Ability or Lesser Power).  See
    _bound_script_for and magic.bound_script_variant.
    """
    effects = []
    effect_count = get_int(rec, count_key)
    dropped_dur = 0
    for i in range(effect_count):
        if pad_to and len(effects) >= pad_to:
            break
        code = get_str(rec, f'Effect[{i}].EFID')
        av = get_int(rec, f'Effect[{i}].ActorValue', -1)
        mgef_fid = _resolve_mgef(
            code, av,
            get_str(rec, f'ScriptEffect[{i}].FormID'),
            get_str(rec, f'Effect[{i}].Type')) if code else 0
        if not mgef_fid:
            dropped_dur = max(dropped_dur, get_int(rec, f'Effect[{i}].Duration'))
            continue
        # Bound armor has no engine implementation at all, and any bound item
        # on a never-cast spell is equally dead — either way the scripted
        # stand-in takes over.  A bound weapon on a castable spell is left on
        # the native archetype.
        scripted = _bound_script_for(mgef_fid, writer, uncastable)
        if scripted:
            mgef_fid = scripted
        mag = get_int(rec, f'Effect[{i}].Magnitude')
        area = get_int(rec, f'Effect[{i}].Area')
        dur = get_int(rec, f'Effect[{i}].Duration')
        effects.append((mgef_fid, float(mag), area, dur, code))

    # Every effect-bearing record needs at least one real effect; INGR needs
    # exactly pad_to. Fill with distinct harmless zero-magnitude effects.
    # When EVERY effect dropped (pure script-effect spells), the first filler
    # keeps the longest dropped duration: the spell then still registers as an
    # active magic effect for as long as the TES4 spell did, which is what the
    # converted IsSpellTarget checks (TES4Polyfill.HasMagicEffectByID) look for.
    want = max(pad_to, 1)
    used = {fid for fid, _, _, _, _ in effects}
    fillers = iter(fid for fid in _FILLER_EFFECTS if fid not in used)
    filler_dur = dropped_dur if not effects else 0
    while len(effects) < want:
        effects.append((next(fillers, _FILLER_EFFECTS[0]), 0.0, 0, filler_dur, ''))
        filler_dur = 0

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


def _book_source_mesh_missing(writer, model: str) -> bool:
    """True when the plugin's BOOK MODL has no source mesh in the export.

    asset_convert/ui/book_inam.py skips those models ("source mesh missing"), so
    the INAM STAT must not point at a mesh that will never be generated.
    Cached per writer; unknown export dir → assume present (old behaviour).
    """
    import os
    export_dir = getattr(writer, 'export_dir', None)
    if not export_dir or not model:
        return False
    cache = getattr(writer, '_book_src_missing', None)
    if cache is None:
        cache = writer._book_src_missing = {}
    key = model.lower()
    hit = cache.get(key)
    if hit is None:
        parts = model.replace('/', '\\').split('\\')
        hit = not os.path.isfile(
        os.path.join(str(assets_for(export_dir)), 'meshes', *parts))
        cache[key] = hit
    return hit


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


# ---------------------------------------------------------------------------
# Weapon converters
# ---------------------------------------------------------------------------

def _weapon_anim_type(rec: dict, tes4_type: int, model: str) -> int:
    """The TES5 AnimationType this weapon should carry.

    Skyrim's behaviour graph drives equip/draw from the animation type, and it
    must agree with the NIF's Prn node or the weapon is invisible when drawn:
    Mace looks at WeaponMace, WarAxe at WeaponAxe, Dagger at WeaponDagger. The
    mesh-name tests here mirror _remap_prn() in asset_convert/nif/nif_converter.py.

    See: docs/commentary/tes4_export_falloutnv.md#weapons-guns-become-crossbows
    """
    anim_type = WEAPON_TYPE_MAP.get(tes4_type, 1)
    path = model.lower().replace('\\', '/')
    if tes4_type == 2 and anim_type == 4:
        if 'waraxe' in path or '/axe' in path or '_axe' in path:
            anim_type = 3
    elif tes4_type == 0 and anim_type == 1:
        if 'dagger' in path.rsplit('/', 1)[-1]:
            anim_type = 2
    return refine_fallout_anim_type(rec, anim_type)


def _weapon_model_and_sounds(rec: dict, writer, anim_type: int,
                             model: str) -> bytes:
    """WNAM and the sound links, in TES5 order, before DATA.

    WNAM is the record's own 1st-person STAT (FO3/FNV) or a companion STAT
    holding the world model (Oblivion has no 1st-person weapon meshes).
    SNAM..UNAM are a FO3/FNV gun's shoot/dry-fire/idle sounds; NAM9/NAM8
    (draw/sheathe) prefer the record's own, else the per-type vanilla ones.
    See: docs/commentary/tes4_export_falloutnv.md#projectiles
    """
    subs = b''
    wnam_fid = get_formid(rec, 'WNAM')
    if not wnam_fid and model and writer is not None:
        edid = get_str(rec, 'EditorID', '')
        wnam_fid = writer.derive_formid('WEAP_STAT', get_formid(rec, 'FormID'))
        writer.add_record('STAT', _build_weapon_1stperson_stat(
            edid, prefix_path(model), wnam_fid))
    if wnam_fid:
        subs += pack_formid_subrecord('WNAM', wnam_fid)
    draw, sheathe = 0, 0
    if writer is not None:
        subs += gun_sound_subs(rec, writer)
        draw, sheathe = gun_sheathe_sounds(rec, writer)
    subs += pack_formid_subrecord(
        'NAM9', draw or WEAPON_ANIM_NAM9.get(anim_type, 0x0003C72E))
    subs += pack_formid_subrecord(
        'NAM8', sheathe or WEAPON_ANIM_NAM8.get(anim_type, 0x0003C72F))
    return subs


def _enchantment_subs(rec: dict) -> bytes:
    """EITM and its EAMT charge pool, or nothing when the weapon is plain.

    TES4 authors the pool as ANAM beside ENAM and TES5 as EAMT beside EITM --
    the same u16 second member of one enchantment struct in both games, so the
    value carries across unchanged. Without EAMT a converted staff has a zero
    charge pool and every cast fails.

    See: docs/commentary/tes5_import_magic.md#enchantment-charge-eamt
    """
    enam = get_formid(rec, 'ENAM')
    if not enam:
        return b''
    charge = min(get_int(rec, 'ANAM'), 0xFFFF)
    return (pack_formid_subrecord('EITM', enam)
            + pack_subrecord('EAMT', struct.pack('<H', charge)))


def convert_WEAP(rec: dict, writer=None) -> bytes:
    """Convert WEAP.

    TES5 order: EDID OBND FULL MODL EITM EAMT ETYP BIDS BAMT INAM WNAM SNAM XNAM
    NAM7 TNAM UNAM NAM9 NAM8 DATA DNAM CRDT VNAM
    """
    subs = _common_header_subs(rec, obnd_sig='WEAP')
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', prefix_path(model))

    subs += _enchantment_subs(rec)

    tes4_type = get_int(rec, 'DATA.Type')
    anim_type = _weapon_anim_type(rec, tes4_type, model)

    # ETYP — Equipment Type (EQUP FormID): determines which hand slot is used
    subs += pack_formid_subrecord('ETYP', WEAPON_ANIM_EQUP.get(anim_type, 0x00013F42))

    # BIDS — Block Bash Impact Data Set
    subs += pack_formid_subrecord('BIDS', WEAPON_ANIM_BIDS.get(anim_type, 0x000183FF))

    # BAMT — Block Material
    subs += pack_formid_subrecord('BAMT', WEAPON_ANIM_BAMT.get(anim_type, 0x000774C2))

    # KSIZ/KWDA — vendor keyword (TES4 type 4 = Staff)
    subs += pack_keywords([VENDOR_KYWD['Staff' if tes4_type == 4 else 'Weapon']])

    subs += pack_formid_subrecord(
        'INAM', get_formid(rec, 'INAM')
        or WEAPON_ANIM_INAM.get(anim_type, 0x00013CAC))

    subs += _weapon_model_and_sounds(rec, writer, anim_type, model)

    speed = gun_speed(rec, get_float(rec, 'DATA.Speed', 1.0))
    reach = get_float(rec, 'DATA.Reach', 1.0)
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    damage = get_int(rec, 'DATA.Damage')
    subs += pack_subrecord('DATA', struct.pack('<IfH', value, weight, damage))

    # DNAM — weapon parameters (100 bytes)
    dnam = bytearray(100)
    struct.pack_into('<B', dnam, 0, anim_type)
    struct.pack_into('<f', dnam, 4, gun_speed(rec, WEAPON_ANIM_MULT.get(anim_type, 1.0)))
    struct.pack_into('<f', dnam, 8, reach if reach > 0.0 else 1.0)
    struct.pack_into('<I', dnam, 12, WEAPON_ANIM_FLAGS.get(anim_type, 0))
    struct.pack_into('<B', dnam, 26, max(1, get_int(rec, 'DNAM.ProjectileCount', 1)))
    struct.pack_into('<f', dnam, 44, speed)
    struct.pack_into('<B', dnam, 76, WEAPON_ANIM_STAGGER.get(anim_type, 0)) # Stagger
    subs += pack_subrecord('DNAM', bytes(dnam))

    # CRDT — Critical data (24 bytes for SSE, form version 44)
    subs += pack_subrecord('CRDT', b'\x00' * 24)

    # VNAM — Violence type
    subs += pack_subrecord('VNAM', struct.pack('<I', WEAPON_ANIM_VNAM.get(anim_type, 1)))

    return pack_record('WEAP', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def _armo_armor_type(rec: dict, is_clothing: bool) -> int:
    """TES5 ArmorType enum (0=Light, 1=Heavy, 2=Clothing) for an ARMO/CLOT."""
    if is_clothing:
        return 2
    gen_flags = get_int(rec, 'BMDT.GeneralFlags')
    # TES4 bit 7 (0x80) = Heavy Armor (from wbDefinitionsTES4.pas)
    return 1 if gen_flags & 0x80 else 0


def build_armo_bod2(rec: dict, is_clothing: bool) -> bytes:
    """TES5 BOD2 payload (8 bytes) from a TES4 ARMO/CLOT record.

    Shared by convert_ARMO and the override path (override_builder), so an
    authored biped/armor-type change patches the exact bytes conversion writes.
    (The ARMA companion's BOD2 stays the master's — companions are never
    re-minted by an override.)
    """
    tes5_biped = _convert_biped_flags(get_int(rec, 'BMDT.BipedFlags'))
    return struct.pack('<II', tes5_biped, _armo_armor_type(rec, is_clothing))


def convert_ARMO(rec: dict, is_clothing: bool = False, writer=None) -> bytes:
    """Convert ARMO or CLOT → ARMO.

    TES5 order: EDID OBND FULL EITM MOD2 ICON MOD4 ICO2 BOD2
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
    # A wearable may be authored for ONE gender only — the male fields are then
    # empty and every "the model" lookup below has to fall back to the female
    # one, or the record ends up with no mesh at all.  Nehrim ships 5 such
    # items (IrlandaRobe, the Silverlight set); they equipped and drew nothing.
    male_model = get_str(rec, 'Male.BipedModel.MODL')
    female_model = get_str(rec, 'Female.BipedModel.MODL')
    male_world = get_str(rec, 'Male.WorldModel.MODL')
    ground_model = (male_world or male_model
                    or get_str(rec, 'Female.WorldModel.MODL') or female_model)
    if ground_model:
        subs += pack_string_subrecord('MOD2', prefix_path(ground_model))

    # MOD4 — Female world model (if different)
    female_world = get_str(rec, 'Female.WorldModel.MODL')
    if female_world:
        subs += pack_string_subrecord('MOD4', prefix_path(female_world))

    # BOD2 (Biped Object Data) replaces BMDT — shared with the override path
    tes4_biped = get_int(rec, 'BMDT.BipedFlags')
    tes5_biped = _convert_biped_flags(tes4_biped)
    armor_type = _armo_armor_type(rec, is_clothing)
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

    # MODL[] — Armature (ARMA references): generate ARMA companion record.
    # EITHER gender's mesh is enough. Gating on the male model alone left
    # female-only wearables with no armature, and an ARMO with no ARMA equips
    # but renders nothing — the actor looks naked while the slot is occupied.
    # Vanilla census (Skyrim.esm, 766 ARMA): 4 carry MOD3 only and 0 carry
    # neither, so a female-only armature is legal and an empty one never is.
    if writer is not None and (male_model or female_model):
        arma_fid = writer.derive_formid('ARMA', get_formid(rec, 'FormID'))
        # HEAD GEAR GETS ONE ARMATURE PER RACE FAMILY, as vanilla ships it
        # (see skyrim_overrides.ARMA_BEAST_RACES).  The converted mesh is
        # fitted to the shared HUMAN skull, so on a khajiit or argonian the
        # same geometry sits inside the head; asset_convert writes a
        # <name>_khajiit / <name>_argonian mesh fitted to that race's own
        # skull and these ARMAs are what make the engine pick them.
        # Non-head gear is unaffected: _beast_arma_races returns () and the
        # single all-races armature below is emitted exactly as before.
        beast = _beast_arma_races(rec)
        arma_bytes = _build_arma(rec, arma_fid, tes5_biped, armor_type,
                                 is_shield=is_shield,
                                 exclude_beast_races=bool(beast))
        writer.add_record('ARMA', arma_bytes)
        subs += pack_formid_subrecord('MODL', arma_fid)
        for race in beast:
            # Keyed on (source FormID, race) so the existing human ARMA id
            # -- derived from the bare source FormID -- never moves.
            b_fid = writer.derive_formid(
                'ARMA', (get_formid(rec, 'FormID'), race))
            writer.add_record('ARMA', _build_arma(
                rec, b_fid, tes5_biped, armor_type, is_shield=is_shield,
                beast_race=race))
            subs += pack_formid_subrecord('MODL', b_fid)

    # DATA: Value(4) + Weight(4) = 8 bytes in TES5
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    subs += pack_subrecord('DATA', struct.pack('<If', value, weight))

    # DNAM — Armor rating as S32
    rating = get_int(rec, 'DATA.ArmorRating') if not is_clothing else 0
    subs += pack_subrecord('DNAM', struct.pack('<i', rating))

    return pack_record('ARMO', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def _beast_arma_races(rec: dict) -> tuple:
    """The beast races this record needs its own armature for.

    HEAD GEAR ONLY, and decided by the record's AUTHORED BMDT flags -- never by
    the filename.  TES4 biped bit 0 (Head) and bit 1 (Hair) are the slots a
    helmet or hood is authored into; a mesh in either rides Skyrim's head bone
    and is fitted to the skull, which is exactly the geometry that breaks on a
    beast race.  Body gear is fitted to the BODY and is race-independent, so it
    keeps its single all-races armature.

    A record must claim ONLY head slots.  A multi-slot suit (Knight of Order:
    head + torso + legs + hands + feet in one NIF, flags 0x3D) is fitted by
    where its vertex MASS sits, which is the body -- asset_convert resolves it
    to a body piece and writes no per-race mesh for it, so emitting a beast
    ARMA here would point the engine at a file that does not exist and the
    wearer would render invisible.  Measured on Oblivion.esm: 14 of 484 beast
    ARMAs pointed at a missing mesh before this gate, all of them that suit.

    Returns () when the record is not head-only gear, which leaves the ARMA
    output byte-identical to what it was before beast variants existed.
    """
    tes4_biped = get_int(rec, 'BMDT.BipedFlags')
    HEAD_BITS = 0b11                    # bit 0 Head, bit 1 Hair
    BODY_BITS = 0b111100                # bits 2-5 UpperBody/LowerBody/Hand/Foot
    if not tes4_biped & HEAD_BITS:
        return ()
    if tes4_biped & BODY_BITS:
        return ()
    return tuple(ARMA_BEAST_RACES)


def _build_arma(rec: dict, arma_fid: int, tes5_biped: int, armor_type: int,
                is_shield: bool = False, beast_race=None,
                exclude_beast_races: bool = False) -> bytes:
    """Build an ARMA (Armor Addon) companion record for an ARMO.

    ARMA holds the actual worn mesh models.
    Order: EDID BOD2 RNAM DNAM MOD2 MOD3 [SNDD] MODL[]

    `beast_race` builds the KHAJIIT or ARGONIAN armature instead of the
    default one: its RNAM is that race, its MODL[] that race's vampire
    variant, and its meshes are the per-race NIFs asset_convert fitted to
    that race's own skull.  `exclude_beast_races` drops the beast races
    from the DEFAULT armature's additional-race list, so the engine cannot
    satisfy a khajiit with the human-fitted mesh and skip the beast one.
    Both mirror vanilla -- see skyrim_overrides.ARMA_BEAST_RACES.
    """
    subs = b''
    edid = get_str(rec, 'EditorID', '')
    if beast_race:
        subs += pack_string_subrecord(
            'EDID', edid + '_' + beast_race.capitalize() + 'AA')
    else:
        subs += pack_string_subrecord('EDID', edid + '_AA')

    # BOD2 — body coverage flags (may be wider than the ARMO's equipment slot).
    # ARMA declares which body regions the mesh covers, e.g. a cuirass mesh
    # covers Body + ForeArms + Calves even though the ARMO only claims "Body".
    arma_biped = tes5_biped
    # Clothing shirts should NOT claim ForeArms — their sleeves are SBP_32_BODY
    # geometry that should remain visible when gloves are equipped.
    # Shoes should NOT claim Calves — only boots (armor foot items) should.
    is_clothing = (armor_type == 2)
    # Either gender's path — a female-only boot is still a boot.
    is_boot = ('boot' in (get_str(rec, 'Male.BipedModel.MODL', '')
                          or get_str(rec, 'Female.BipedModel.MODL', '')).lower())
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
    if beast_race:
        subs += pack_formid_subrecord(
            'RNAM', ARMA_BEAST_RACES[beast_race][0])
    else:
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
        p = prefix_path(path)
        if not p.lower().endswith('.nif'):
            return p
        if beast_race:
            # The per-race mesh asset_convert fitted to THIS race's skull.
            # Head gear never sets the weight slider (vanilla helmets ship a
            # plain path), so the two suffixes can never both apply.
            return p[:-4] + ARMA_BEAST_RACES[beast_race][2] + '.nif'
        if use_slider:
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
    if beast_race:
        race_list = ARMA_BEAST_RACES[beast_race][1]
    elif exclude_beast_races:
        race_list = ARMA_ADDITIONAL_RACES_NONBEAST
    else:
        race_list = ARMA_ADDITIONAL_RACES
    for race_fid in race_list:
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

    Order EDID OBND FULL MODL DATA NAM1 VNAM; DATA is 92 bytes whose offsets,
    Arrow's BIT type 0x40 and the speed scaling are all in the offset map.
    See: docs/reference/record_mapping.md#proj-data-layout
    """
    subs = b''
    subs += pack_string_subrecord('EDID', edid + 'Projectile')
    subs += pack_obnd()
    subs += pack_string_subrecord('MODL', model_path)

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
        subs += pack_string_subrecord('MODL', prefix_path(model))

    damage = get_int(rec, 'DATA.Damage')
    value = get_int(rec, 'DATA.Value')
    flags = get_int(rec, 'DATA.Flags')
    weight = get_float(rec, 'DATA.Weight')
    speed = get_float(rec, 'DATA.Speed', 1.0)

    proj_fid = ammo_projectile(rec) or DEFAULT_ARROW_PROJECTILE
    if proj_fid == DEFAULT_ARROW_PROJECTILE and writer is not None:
        edid = get_str(rec, 'EditorID', '')
        proj_fid = writer.derive_formid('PROJ', get_formid(rec, 'FormID'))
        proj_model = prefix_path(model) if model else prefix_path('Weapons\\Iron\\Arrow.NIF')
        proj_bytes = _build_arrow_proj(edid, proj_model, speed, proj_fid)
        writer.add_record('PROJ', proj_bytes)

    # YNAM/ZNAM — pickup/putdown sounds (as vanilla arrows: ITMGenericWeaponUp/Down)
    subs += pack_formid_subrecord('YNAM', 0x0003E7B7)
    subs += pack_formid_subrecord('ZNAM', 0x0003E877)

    # KSIZ/KWDA — vendor keyword (weapon vendors' list includes Arrow)
    subs += pack_keywords([VENDOR_KYWD['Arrow']])

    data = struct.pack('<IIfIf', proj_fid, ammo_flags(flags), float(damage),
                       value, weight)
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
            path = ('img://textures/' + current_namespace()
                    + '/menus/' + path.lstrip('/'))
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
    """BOOK — or SCRL when the book carries an enchantment.

    A TES4 book with an ENAM is a SCROLL: reading it casts the enchantment and
    consumes the paper.  Skyrim's BOOK record has NO field for an object
    effect, so converting one to a BOOK produces a blank page that can never be
    cast — 503 of them across Oblivion, Nehrim and Morrowind_ob, the Scroll of
    Icarian Flight among them.  Skyrim's own record for this is SCRL, which
    carries the effects directly, so those route there instead.  (The caller
    files the record by the signature these bytes actually carry.)
    """
    ench = _ENCH_BY_FID.get((rec.get('ENAM') or '').upper())
    if ench is not None:
        subs = _build_scrl(rec, ench, get_int(ench, 'ENIT.Cost'), writer)
        return pack_record('SCRL', get_formid(rec, 'FormID'),
                           get_int(rec, 'RecordFlags'), subs)

    # TES5 BOOK field order: EDID OBND FULL MODL DESC DATA INAM CNAM
    subs = _common_header_subs(rec, obnd_sig='BOOK')
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', prefix_path(model))
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

    # Type: always 0 (Book/Tome).  The CK lists 255 = Note/Scroll, but vanilla
    # Skyrim.esm uses 0 for every one of its 821 BOOKs including all notes, so
    # 255 is an engine-untested value; scroll-flagged TES4 books get 0 too.
    book_type = 0
    data = struct.pack('<BBHiIf', tes5_flags, book_type, 0, teaches_tes5, value, weight)
    subs += pack_subrecord('DATA', data)

    # INAM — Inventory Art (STAT).  BookMenu null-derefs without it (in-game
    # crash on reading any book), so it must always be present.  It also must
    # point at one of the rigged Skyrim reading meshes: the open animation and
    # page text come from the template's behavior graph + skinned page bones +
    # PageText quad, so a static mesh here opens invisible with no text.
    # asset_convert/ui/book_inam.py bakes each distinct TES4 book model's cover
    # textures onto the vanilla book/note rig and writes it to
    # meshes\tes4\clutter\books\inv\<inv_basename(model)>.nif; one STAT per
    # model is synthesised here (cached on the writer — BOOKs convert
    # serially).  The basename rule is imported from book_inam so the STAT
    # target and the generated mesh cannot drift apart.
    inam_fid = 0x000E894C  # HighPolySkyrimBook — fallback for model-less books
    if writer is not None and model:
        # Resolve through the plugin-wide collision-aware map, exactly as the
        # asset side does: two BOOK models can share a leaf filename across
        # different directories, and only the map knows which one keeps the
        # bare name.  Built once per writer (BOOKs convert serially).
        from asset_convert.ui.book_inam import inv_basename, inv_basename_map
        bmap = getattr(writer, '_book_inam_names', None)
        if bmap is None:
            models = sorted(getattr(writer, 'book_models', None) or [],
                            key=lambda m: m.lower())
            bmap = writer._book_inam_names = inv_basename_map(models)
        base = bmap.get(model) or inv_basename(model)
        # The asset stage skips models whose source mesh the plugin never
        # ships, so pointing a STAT at the mesh it would have generated
        # leaves BookMenu loading a file that does not exist.  Fall back to
        # the vanilla reading rig for those, same as a model-less book.
        if _book_source_mesh_missing(writer, model):
            base = None
        if base is not None:
            cache = getattr(writer, '_book_inam_stats', None)
            if cache is None:
                cache = writer._book_inam_stats = {}
            inam_fid = cache.get(base)
            if inam_fid is None:
                # SHARED across every book using this mesh, so it cannot key
                # off one book's FormID. `base` is derived (inv_basename_map
                # resolves collisions against the whole model list), so key on
                # the authored source model path instead — that is TES4 data
                # and does not move when our naming logic changes.
                inam_fid = writer.derive_formid('BOOK_INVART', model.lower())
                inv_model = 'clutter\\books\\inv\\' + base + '.nif'
                stat_bytes = _build_model_stat('InvArt_' + base,
                                               prefix_path(inv_model), inam_fid)
                writer.add_record('STAT', stat_bytes)
                cache[base] = inam_fid
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
    """SPEL — Spell. SPIT restructured for TES5.

    TES5 order: EDID OBND FULL KWDA MDOB ETYP DESC SPIT EFID/EFIT*
    """
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

    # ETYP — Equip Type.  MANDATORY: it names the slot the magic menu files the
    # spell under, and a spell without one never appears in the menu at all
    # (converted Bound Dagger/Mace were addable by console but invisible and
    # uncastable).  Oblivion has no equivalent field, so it is derived from the
    # spell type exactly as vanilla does.  Census: 827/827 vanilla spells carry
    # ETYP, no exceptions.
    subs += pack_formid_subrecord(
        'ETYP', SPELL_TYPE_EQUIP_TYPE.get(tes5_type, SPELL_EQUIP_EITHER_HAND))

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

    # Effects.  An Ability (4) or Lesser Power (3) is applied, never cast, so
    # any bound-item effect it carries needs the scripted stand-in.
    from .magic import UNCASTABLE_SPELL_TYPES
    subs += _pack_effects(rec, delivery=target_type, writer=writer,
                          uncastable=tes5_type in UNCASTABLE_SPELL_TYPES)

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
        subs += pack_string_subrecord('MODL', prefix_path(model))

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
        subs += pack_string_subrecord('MODL', prefix_path(model))

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


def _build_scrl(rec: dict, effect_src: dict, cost: int = 0,
                writer=None) -> bytes:
    """Pack a TES5 SCRL (Scroll).

    TES5 SCRL order: EDID OBND FULL KSIZ KWDA MDOB ETYP DESC MODL DATA SPIT
    EFID/EFIT (xEdit wbRecord(SCRL); verified against Skyrim.esm's
    MGR21ScrollMagicka).

    ``rec`` supplies the item (name, model, value, weight); ``effect_src`` the
    magic payload.  They are the SAME record for a sigil stone but differ for
    an enchanted book, whose effects live on the ENCH its ENAM names.
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
        subs += pack_string_subrecord('MODL', prefix_path(model))

    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    subs += pack_subrecord('DATA', struct.pack('<If', value, weight))

    # SPIT — same 36-byte layout as SPEL. CastType 3 = Scroll (matches every
    # vanilla SCRL); Delivery from the first effect like SPEL.
    target_type = 0
    first_effect_type = get_str(effect_src, 'Effect[0].Type')
    if first_effect_type == 'Touch':
        target_type = 1
    elif first_effect_type == 'Target':
        target_type = 2
    spit = struct.pack('<IIIfIIff4x', cost, 0, 0, 0.0, 3, target_type, 0.0, 0.0)
    subs += pack_subrecord('SPIT', spit)

    # A scroll carries its effects DIRECTLY — SCRL has no field for an object
    # effect, so an enchanted book's ENCH payload is copied in here.  Without
    # them the record is a dead item (CK: "Magic Item ... has no effects
    # defined").
    subs += _pack_effects(effect_src, delivery=target_type, writer=writer)
    return subs


def convert_SGST(rec: dict, writer=None) -> bytes:
    """Sigil Stone → SCRL (Scroll, closest equivalent).

    A sigil stone's effects were what it enchanted with in TES4; as a scroll
    they become its cast payload.
    """
    subs = _build_scrl(rec, rec, writer=writer)

    return pack_record('SCRL', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_APPA(rec: dict) -> bytes:
    """Apparatus → MISC (no apparatus in TES5)."""
    subs = _common_header_subs(rec, obnd_sig='MISC')
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', prefix_path(model))
    subs += pack_keywords([VENDOR_KYWD['Clutter']])
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    subs += pack_subrecord('DATA', struct.pack('<If', value, weight))
    return pack_record('MISC', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


# ---------------------------------------------------------------------------
# Actor converters
# ---------------------------------------------------------------------------

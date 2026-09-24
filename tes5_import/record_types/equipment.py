"""Equipment converters: WEAP, ARMO, CLOT, AMMO, BOOK, ENCH, SPEL, ALCH, INGR, SGST, APPA."""

from asset_convert.game_paths import current_namespace
import re
import struct

from ..base.constants import ENCH_CAST_TYPE_MAP, ENCH_TYPE_MAP, WEAPON_TYPE_MAP, ARMA_BODY_COVERAGE_EXTRA
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
from .magic_variants import (DELIVERY_CONTACT, MGEF_CAST_FOR_OWNER, RANGE_DELIVERY,
                             UNCASTABLE_SPELL_TYPES, bound_assoc_is_armor,
                             bound_item_assoc, bound_script_variant,
                             delivery_variant, get_mgef_formid,
                             get_seff_variant, menu_object, owner_delivery,
                             written_once)
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


from asset_convert.character.morrowind_coverage import (BODY_PARTITIONS,
                                                        coverage_bits,
                                                        part_slots, sided_slot)
from asset_convert.ui.book_inam import (find_source_mesh, inv_basename,
                                        inv_basename_map, reads_as_book)

#: Biped slot of BOD2 bit 0.
_FIRST_SLOT = 30
#: Vanilla note reading rig (Clutter\Books\Note01\Note02.nif), the INAM of 77 vanilla notes.
HIGH_POLY_NOTE02 = 0x0001541B
#: Vanilla bound-book reading rig (BookSkyrim01.nif).
HIGH_POLY_SKYRIM_BOOK = 0x000E894C


# ---------------------------------------------------------------------------
# Book inventory art
# ---------------------------------------------------------------------------

def _book_inventory_art(writer, model: str) -> int:
    """INAM FormID (never 0: BookMenu null-derefs) for a BOOK with world model `model`.

    A bound book: its generated rig (asset_convert/ui/book_inam.py), one STAT
    per model keyed on the authored model path, named by the asset side's
    collision-aware basename.  A note, parchment or scroll: vanilla
    HighPolyNote02's clean paper.  A model no asset tree ships, or none: the
    vanilla book.  A writer without `book_roots` takes every model to be a
    book.  Cached per writer.
    """
    if writer is None or not model:
        return HIGH_POLY_SKYRIM_BOOK
    cache = getattr(writer, '_book_inam_fids', None)
    if cache is None:
        cache = writer._book_inam_fids = {}
    key = model.lower()
    if key not in cache:
        cache[key] = _resolve_book_inventory_art(writer, model)
    return cache[key]


def _resolve_book_inventory_art(writer, model: str) -> int:
    """Uncached body of _book_inventory_art."""
    roots = getattr(writer, 'book_roots', None)
    if roots is not None:
        src = find_source_mesh(roots, model)
        if src is None:
            return HIGH_POLY_SKYRIM_BOOK
        if not reads_as_book(src):
            return HIGH_POLY_NOTE02
    bmap = getattr(writer, '_book_inam_names', None)
    if bmap is None:
        models = sorted(getattr(writer, 'book_models', None) or [], key=str.lower)
        bmap = writer._book_inam_names = inv_basename_map(models)
    base = bmap.get(model) or inv_basename(model)
    fid = writer.derive_formid('BOOK_INVART', model.lower())
    writer.add_record('STAT', _build_model_stat(
        'InvArt_' + base, prefix_path('clutter\\books\\inv\\' + base + '.nif'), fid))
    return fid


# ---------------------------------------------------------------------------
# Shared record helpers
# ---------------------------------------------------------------------------

def armo_slots(rec: dict) -> int:
    """The ARMO's BOD2 slots: a one-sided Morrowind piece's own, else the converted flags.

    See: docs/commentary/asset_convert_armor.md#body-slot-layout
    """
    sided = sided_slot(rec)
    if sided:
        return 1 << (sided - _FIRST_SLOT)
    return _convert_biped_flags(get_int(rec, 'BMDT.BipedFlags'))


def _arma_bod2(rec: dict, tes5_biped: int, armor_type: int) -> int:
    """The ARMA's BOD2: the ARMO's slots plus the body regions its mesh covers.

    A Morrowind wearable states its body coverage in its part list; every
    other record takes `ARMA_BODY_COVERAGE_EXTRA`, less forearms for clothing
    and calves for shoes. Head extras apply to both.
    See: docs/commentary/asset_convert_armor.md#morrowind-skin-fill
    """
    slots = part_slots(rec)
    arma_biped = tes5_biped | coverage_bits(slots)
    is_clothing = (armor_type == 2)
    is_boot = ('boot' in (get_str(rec, 'Male.BipedModel.MODL', '')
                          or get_str(rec, 'Female.BipedModel.MODL', '')).lower())
    for bit, extras in ARMA_BODY_COVERAGE_EXTRA.items():
        if not arma_biped & (1 << bit):
            continue
        for extra_bit in extras:
            if slots and extra_bit + _FIRST_SLOT in BODY_PARTITIONS:
                continue
            if is_clothing and extra_bit == 4:
                continue
            if extra_bit == 8 and bit == 7 and not is_boot and is_clothing:
                continue
            arma_biped |= (1 << extra_bit)
    return arma_biped


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


#: Effect owner rule for potions, poisons and ingredients: Fire and Forget on Self, 803 of 803 vanilla slots.
OWNER_CONSUMED = (1, 0)
#: TES5 spell type -> (casting type, delivery): abilities Constant on Self, diseases Constant on Contact.
SPELL_TYPE_CAST = {4: (0, 0), 1: (0, DELIVERY_CONTACT)}
#: TES4 ENCH type -> the delivery its effects are fixed to: a weapon strikes (Contact), apparel is worn (Self).
ENCH_FIXED_DELIVERY = {2: DELIVERY_CONTACT, 3: 0}
#: The spell type both games number 1.
SPELL_TYPE_DISEASE = 1
#: A subrecord's type and size precede its data: 6 bytes.
_SUBRECORD_HEADER = 6
#: (TES4 SPIT flag, TES5 SPIT flag) pairs with one meaning in both games (xEdit SPIT definitions).
SPELL_FLAG_MAP = ((0x01, 0x000001), (0x04, 0x020000), (0x10, 0x080000),
                  (0x20, 0x100000), (0x40, 0x200000))


def _slot_mgef(rec: dict, i: int, code: str, writer, uncastable: bool,
               owner: tuple) -> int:
    """The MGEF effect slot ``i`` references, 0 when its code has none.

    ``owner`` is (the casting type the owner's effects carry, its fixed
    delivery or None for each effect's own TES4 range); the effect is
    re-pointed at a clone carrying that pair.
    """
    mgef_fid = _resolve_mgef(code, get_int(rec, f'Effect[{i}].ActorValue', -1),
                             get_str(rec, f'ScriptEffect[{i}].FormID'),
                             get_str(rec, f'Effect[{i}].Type'))
    if not mgef_fid:
        return 0
    mgef_fid = _bound_script_for(mgef_fid, writer, uncastable) or mgef_fid
    delivery = owner[1]
    if delivery is None:
        delivery = RANGE_DELIVERY.get(get_str(rec, f'Effect[{i}].Type'), 0)
    return delivery_variant(mgef_fid, owner[0], delivery, writer,
                            area=get_int(rec, f'Effect[{i}].Area') > 0)


def _pack_effects(rec: dict, count_key: str = 'EffectCount', pad_to: int = 0,
                  delivery: int = 0, writer=None, uncastable: bool = False,
                  owner: tuple = OWNER_CONSUMED) -> bytes:
    """Pack EFID/EFIT pairs for all effects on a record.

    Effects with no TES5 equivalent are dropped — an EFID of 0 (null MGEF)
    crashes the game as soon as the item's card is shown in a menu. If all
    effects are dropped, or pad_to demands more (e.g. 4 for INGR), real
    zero-magnitude filler effects are used.

    Every slot, filler included, is fitted to ``owner`` (see _slot_mgef).
    ``delivery`` is the owning record's own, which a filler takes when the
    owner fixes none, so an aimed item always reaches a projectile.
    See: docs/commentary/tes5_import_magic.md#aimed-ench-null-projectile

    Bound-item effects are re-pointed at a scripted stand-in whenever the
    engine's own archetype 17 cannot serve them — always for bound ARMOR
    (Skyrim implements bound weapons only), and for any bound item on a
    never-cast spell.  ``uncastable`` marks that second case: a spell the
    engine APPLIES rather than casts (an Ability or Lesser Power).  See
    _bound_script_for and magic.bound_script_variant.
    """
    effects = []
    for i in range(get_int(rec, count_key)):
        if pad_to and len(effects) >= pad_to:
            break
        code = get_str(rec, f'Effect[{i}].EFID')
        mgef_fid = _slot_mgef(rec, i, code, writer, uncastable, owner) if code else 0
        if mgef_fid:
            effects.append((mgef_fid, float(get_int(rec, f'Effect[{i}].Magnitude')),
                            get_int(rec, f'Effect[{i}].Area'),
                            get_int(rec, f'Effect[{i}].Duration')))

    used = {fid for fid, *_ in effects}
    fillers = iter(fid for fid in _FILLER_EFFECTS if fid not in used)
    filler_delivery = delivery if owner[1] is None else owner[1]
    while len(effects) < max(pad_to, 1):
        filler = next(fillers, _FILLER_EFFECTS[0])
        effects.append((delivery_variant(filler, owner[0], filler_delivery, writer),
                        0.0, 0, 0))

    subs = b''
    for mgef_fid, mag, area, dur in effects:
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
    return struct.pack('<II', armo_slots(rec), _armo_armor_type(rec, is_clothing))


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
    tes5_biped = armo_slots(rec)
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


#: BOD2 bits of slots 32 and 49, the body and lower body.
_BODY_SLOTS = (1 << 2) | (1 << 19)

#: TES4 biped bits of the body, hand and foot slots, whose gear is weight-morphed; bit 5 is the foot.
_TES4_BODY_BITS, _TES4_FOOT = 0b111100, 1 << 5

#: ARMA DNAM priority of body and lower-body armor. See: docs/commentary/asset_convert_armor.md#biped-slot-conversion
_BODY_PRIORITY = 5

#: ARMA DNAM priority of every other armor addon (vanilla gauntlets, boots, helmets).
_ITEM_PRIORITY = 10

#: Armor type (0 light, 1 heavy, 2 clothing) -> the footstep set its boots use.
_FOOTSTEP_SETS = {0: LIGHT_ARMOR_FOOTSTEP_SET, 1: HEAVY_ARMOR_FOOTSTEP_SET, 2: CLOTHING_FOOTSTEP_SET}


def _arma_dnam(use_slider: bool, tes5_biped: int) -> bytes:
    """ARMA DNAM: both priorities, both weight sliders (2 = enabled), zero padding and sound.

    Body and lower-body armor draw at 5 so gloves win 34 and boots 38, as in vanilla.
    See: docs/commentary/asset_convert_armor.md#biped-slot-conversion
    """
    slider = 2 if use_slider else 0
    priority = _BODY_PRIORITY if tes5_biped & _BODY_SLOTS else _ITEM_PRIORITY
    return pack_subrecord('DNAM', struct.pack('<BBBBHBBf', priority, priority, slider, slider,
                                              0, 0, 0, 0.0))


def _arma_models(rec: dict, beast_race, use_slider: bool) -> bytes:
    """MOD2/MOD3: the worn meshes, female falling back to male.

    A beast armature wears the per-race mesh fitted to its skull; weight-morphed
    gear names its `_1` variant.
    """
    def _weighted(path: str) -> str:
        p = prefix_path(path)
        if not p.lower().endswith('.nif'):
            return p
        if beast_race:
            return p[:-4] + ARMA_BEAST_RACES[beast_race][2] + '.nif'
        return p[:-4] + '_1.nif' if use_slider else p

    male_model = get_str(rec, 'Male.BipedModel.MODL')
    female_model = get_str(rec, 'Female.BipedModel.MODL') or male_model
    subs = pack_string_subrecord('MOD2', _weighted(male_model)) if male_model else b''
    if female_model:
        subs += pack_string_subrecord('MOD3', _weighted(female_model))
    return subs


def _arma_races(beast_race, exclude_beast_races: bool) -> bytes:
    """MODL[]: the additional races that can wear the armature."""
    if beast_race:
        race_list = ARMA_BEAST_RACES[beast_race][1]
    elif exclude_beast_races:
        race_list = ARMA_ADDITIONAL_RACES_NONBEAST
    else:
        race_list = ARMA_ADDITIONAL_RACES
    return b''.join(pack_formid_subrecord('MODL', race_fid) for race_fid in race_list)


def _build_arma(rec: dict, arma_fid: int, tes5_biped: int, armor_type: int,
                is_shield: bool = False, beast_race=None,
                exclude_beast_races: bool = False) -> bytes:
    """An ARMO's companion ARMA: EDID BOD2 RNAM DNAM MOD2 MOD3 MODL[] [SNDD].

    `beast_race` builds that race's armature (its RNAM, vampire variant and
    skull-fitted meshes); `exclude_beast_races` drops the beast races from
    the default one's list, as vanilla does (skyrim_overrides.ARMA_BEAST_RACES).
    Only body, hand and foot gear is weight-morphed; boots get a footstep set.
    """
    edid = get_str(rec, 'EditorID', '')
    suffix = '_' + beast_race.capitalize() + 'AA' if beast_race else '_AA'
    subs = pack_string_subrecord('EDID', edid + suffix)
    subs += pack_subrecord('BOD2', struct.pack(
        '<II', _arma_bod2(rec, tes5_biped, armor_type), armor_type))
    subs += pack_formid_subrecord(
        'RNAM', ARMA_BEAST_RACES[beast_race][0] if beast_race else 0x00000019)
    tes4_biped = get_int(rec, 'BMDT.BipedFlags')
    use_slider = bool(tes4_biped & _TES4_BODY_BITS)
    subs += _arma_dnam(use_slider, tes5_biped)
    subs += _arma_models(rec, beast_race, use_slider)
    subs += _arma_races(beast_race, exclude_beast_races)
    if tes4_biped & _TES4_FOOT:
        subs += pack_formid_subrecord('SNDD', _FOOTSTEP_SETS.get(armor_type, CLOTHING_FOOTSTEP_SET))
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

    subs += pack_formid_subrecord('INAM', _book_inventory_art(writer, model))

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
    fixed = ENCH_FIXED_DELIVERY.get(tes4_type, 0 if cast_type == 0 else None)
    target_type = owner_delivery(rec) if fixed is None else fixed

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

    subs += _pack_effects(rec, delivery=target_type, writer=writer,
                          owner=(MGEF_CAST_FOR_OWNER.get(cast_type, 1), fixed))

    return pack_record('ENCH', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_SPEL(rec: dict, writer=None) -> bytes:
    """SPEL — Spell, under its own FormID and EditorID."""
    return pack_record('SPEL', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'),
                       _spell_subrecords(rec, get_str(rec, 'EditorID'), writer))


def attack_spell(fid: int, rec: dict, writer) -> int:
    """A creature's ATKD Attack Spell: a Contact copy of spell ``rec``, else ``fid``.

    See: docs/commentary/tes5_import_magic.md#creature-attack-spells
    """
    edid = get_str(rec, 'EditorID') if rec else ''
    if writer is None or not edid:
        return fid
    return written_once('SPEL_ATTACK', edid, writer, lambda new: writer.add_record(
        'SPEL', pack_record('SPEL', new, 0, _spell_subrecords(
            rec, f'{edid}Attack', writer, DELIVERY_CONTACT))))


def _spell_subrecords(rec: dict, edid: str, writer, delivery: int = None) -> bytes:
    """A SPEL's subrecords, SPIT restructured for TES5.

    TES5 order: EDID OBND FULL KWDA MDOB ETYP DESC SPIT EFID/EFIT*.  The
    spell types 0-4 mean the same in both games.  ETYP is mandatory: a spell
    without one never appears in the magic menu (827/827 vanilla carry it).  MDOB is the
    vanilla menu art the first effect calls for, on every type but Disease.
    ``delivery`` fixes a castable spell's delivery, else its effects pick it.
    See: docs/commentary/tes5_import_magic.md#menu-display-object
    """
    subs = pack_string_subrecord('EDID', edid) if edid else b''
    subs += pack_obnd()
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    tes4_flags = get_int(rec, 'SPIT.Flags')
    tes4_type = get_int(rec, 'SPIT.Type')
    tes5_type = tes4_type if tes4_type <= 4 else 0
    cast_type, target_type = SPELL_TYPE_CAST.get(tes5_type, (1, None))
    if target_type is None:
        target_type = owner_delivery(rec) if delivery is None else delivery
    effects = _pack_effects(rec, delivery=target_type, writer=writer,
                            uncastable=tes5_type in UNCASTABLE_SPELL_TYPES,
                            owner=(cast_type, target_type if cast_type == 0 else delivery))

    if tes5_type != SPELL_TYPE_DISEASE:
        first_effect = struct.unpack_from('<I', effects, _SUBRECORD_HEADER)[0]
        subs += pack_formid_subrecord('MDOB', menu_object(first_effect))
    subs += pack_formid_subrecord(
        'ETYP', SPELL_TYPE_EQUIP_TYPE.get(tes5_type, SPELL_EQUIP_EITHER_HAND))

    tes5_flags = 0
    for tes4_bit, tes5_bit in SPELL_FLAG_MAP:
        if tes4_flags & tes4_bit:
            tes5_flags |= tes5_bit
    subs += pack_subrecord('SPIT', struct.pack(
        '<IIIfII12x', get_int(rec, 'SPIT.Cost'), tes5_flags, tes5_type, 0.0,
        cast_type, target_type))
    return subs + effects


def convert_ALCH(rec: dict, writer=None) -> bytes:
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

    subs += _pack_effects(rec, writer=writer)

    return pack_record('ALCH', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_INGR(rec: dict, writer=None) -> bytes:
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

    subs += _pack_effects(rec, pad_to=4, writer=writer)

    return pack_record('INGR', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def _build_scrl(rec: dict, effect_src: dict, cost: int = 0,
                writer=None) -> bytes:
    """Pack a TES5 SCRL (Scroll).

    TES5 SCRL order: EDID OBND FULL KSIZ KWDA MDOB ETYP DESC MODL DATA SPIT
    EFID/EFIT (xEdit wbRecord(SCRL); verified against Skyrim.esm's
    MGR21ScrollMagicka).

    ``rec`` supplies the item (name, model, value, weight); ``effect_src`` the
    magic payload.  They are the SAME record for a sigil stone but differ for
    an enchanted book, whose effects live on the ENCH its ENAM names: SCRL
    carries its effects directly, and one without any is a dead item.
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

    target_type = owner_delivery(effect_src)
    spit = struct.pack('<IIIfIIff4x', cost, 0, 0, 0.0, 3, target_type, 0.0, 0.0)
    subs += pack_subrecord('SPIT', spit)
    subs += _pack_effects(effect_src, delivery=target_type, writer=writer,
                          owner=(1, None))
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

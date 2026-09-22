"""NPC_ conversion: the humanoid actor, its stats, race, voice and head parts.

Everything here is NPC-only; what CREA also needs lives in actor_common.

See: docs/commentary/tes5_import_actors.md
"""

import struct

from ..base.constants import TES5_SKILL_ORDER
from ..actors.creature_races import TES5_HEALTH_LEVEL_BONUS
from ..actors.npc_face_mapper import build_face_tail_subs, build_pnam_subs
from ..actors.outfits import split_inventory
from ..packages.actor_wiring import CSTY_DEFAULT, DPLT_NPC_LIST, npc_packages
from ..base.equivalents import map_hair_color
from .actor_common import (GOLD001_FID, NAM5_UNKNOWN, SOUND_LEVEL_NORMAL,
                           build_aidt, build_outfit, origin_memberships,
                           get_trainer_class_fid, get_trainer_faction_fid,
                           get_vendor_faction_fids_for_actor, read_items,
                           resolve_actor_voice, resolve_npc_race)
from .common import (
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
    prefix_path,
)
from .npc_morrowind import (TES5_RACE_BASE_HEALTH, is_morrowind_npc,
                            morrowind_health_and_level)

#: TES4 NPC_ ACBS bits that mean the same thing in TES5, Female included.
_NPC_COMPATIBLE_FLAGS = 0x4C9B

#: TES4 NPC_ ACBS bit 7, PC Level Offset.
_T4N_PC_LEVEL_OFFSET = 0x80


# ---------------------------------------------------------------------------
#   Stats
# ---------------------------------------------------------------------------


def npc_skills_dnam(rec: dict) -> bytes:
    """Build TES5 NPC_ DNAM subrecord (52 bytes, skills + stats)."""
    dnam = bytearray(52)
    skill_vals = {}
    skill_names_tes4 = [
        "Armorer", "Athletics", "Blade", "Block", "Blunt",
        "HandToHand", "HeavyArmor", "Alchemy", "Alteration",
        "Conjuration", "Destruction", "Illusion", "Mysticism",
        "Restoration", "Acrobatics", "LightArmor", "Marksman",
        "Mercantile", "Security", "Sneak", "Speechcraft"
    ]
    tes4_to_tes5_skill = {
        "Armorer": "Smithing", "Blade": "OneHanded", "Block": "Block",
        "Blunt": "OneHanded", "HandToHand": "OneHanded",
        "HeavyArmor": "HeavyArmor", "Alchemy": "Alchemy",
        "Alteration": "Alteration", "Conjuration": "Conjuration",
        "Destruction": "Destruction", "Illusion": "Illusion",
        "Mysticism": "Illusion", "Restoration": "Restoration",
        "LightArmor": "LightArmor", "Marksman": "Marksman",
        "Mercantile": "Pickpocket", "Security": "Lockpicking",
        "Sneak": "Sneak", "Speechcraft": "Speechcraft",
    }
    for tes4_name in skill_names_tes4:
        val = get_int(rec, f'DATA.{tes4_name}')
        tes5_name = tes4_to_tes5_skill.get(tes4_name)
        if tes5_name and val:
            skill_vals[tes5_name] = max(skill_vals.get(tes5_name, 0), val)
    for i, skill_name in enumerate(TES5_SKILL_ORDER):
        dnam[i] = min(skill_vals.get(skill_name, 15), 255)
    health = get_int(rec, 'DATA.Health', 50)
    struct.pack_into('<H', dnam, 36, max(0, min(health, 65535)))
    magicka = get_int(rec, 'ACBS.SpellPoints', 0)
    struct.pack_into('<H', dnam, 38, max(0, min(magicka, 65535)))
    stamina = get_int(rec, 'ACBS.Fatigue', 100)
    struct.pack_into('<H', dnam, 40, max(0, min(stamina, 65535)))
    return bytes(dnam)


def _health_and_level(tes4_health: int, tes4_level: int, is_pc_level_mult: bool
                      ) -> tuple:
    """TES4 final health pool -> (ACBS.HealthOffset, ACBS.Level) for an NPC.

    Chosen so the engine's own calculation reproduces the TES4 total exactly.
    A pool past the int16 offset is spent through the Level term instead of
    being clamped, which would make an intended-invulnerable actor killable.
    A PC-Level-Mult actor keeps only the race-base subtraction, since its level
    term tracks the player and is unknown at author time.

    See: docs/commentary/tes5_import_actors.md#health-offset
    """
    if is_pc_level_mult:
        offset = tes4_health - TES5_RACE_BASE_HEALTH
        return max(-32768, min(offset, 32767)), 1000

    level = max(1, min(tes4_level, 65535))
    offset = tes4_health - TES5_RACE_BASE_HEALTH - (level - 1) * TES5_HEALTH_LEVEL_BONUS
    if offset > 32767:
        need = tes4_health - TES5_RACE_BASE_HEALTH - 32767
        level = max(level, min(65535, -(-need // TES5_HEALTH_LEVEL_BONUS) + 1))
        offset = tes4_health - TES5_RACE_BASE_HEALTH - (level - 1) * TES5_HEALTH_LEVEL_BONUS
    return max(-32768, min(offset, 32767)), level


def npc_acbs(rec: dict) -> bytes:
    """Build TES5 NPC_ ACBS payload (24 bytes) from a TES4 NPC_ record.

    Shared by convert_NPC_ and the override path (override_builder), so an
    authored ACBS/attribute change patches the exact bytes conversion writes.

    Layout per xEdit wbDefinitionsTES5. Magicka and Stamina offsets are deltas
    from the race base (SpellPoints, Fatigue). A Morrowind actor solves health
    without TES4's level term.

    See: docs/commentary/tes5_import_actors.md#health-offset
    See: docs/commentary/tes5_import_actors.md#morrowind-health-is-absolute
    """
    tes4_flags = get_int(rec, 'ACBS.Flags')
    level = get_int(rec, 'ACBS.Level', 1)
    calc_min = get_int(rec, 'ACBS.CalcMin', 1)
    calc_max = get_int(rec, 'ACBS.CalcMax', 100)
    tes5_acbs_flags = tes4_flags & _NPC_COMPATIBLE_FLAGS
    is_pc_level = bool(tes4_flags & _T4N_PC_LEVEL_OFFSET)
    if is_morrowind_npc(rec):
        health_offset, tes5_level = morrowind_health_and_level(
            get_int(rec, 'DATA.Health', 50), level)
    else:
        health_offset, tes5_level = _health_and_level(
            get_int(rec, 'DATA.Health', 50), level, is_pc_level)
    magicka_offset = max(-32768, min(
        get_int(rec, 'ACBS.SpellPoints', 0) - TES5_RACE_BASE_HEALTH, 32767))
    stamina_offset = max(-32768, min(
        get_int(rec, 'ACBS.Fatigue', 100) - TES5_RACE_BASE_HEALTH, 32767))
    return struct.pack('<IhhHHHHhHhH',
                       tes5_acbs_flags, magicka_offset, stamina_offset, tes5_level,
                       min(calc_min, 65535), min(calc_max, 65535),
                       100, 0, 0, health_offset, 0)
def _pack_snam(fid: int, rank: int = 0) -> bytes:
    """One SNAM faction membership."""
    return pack_subrecord('SNAM', struct.pack('<IbBBB', fid, rank, 0, 0, 0))


def _npc_snams(rec: dict, vendor_fids: list, trainer_clas_fid: int) -> bytes:
    """Every faction this NPC joins, in subrecord order.

    Its authored TES4 factions, then the vendor factions -- the shared
    per-service one that gates barter dialogue plus, for a chest-backed
    merchant, the dedicated one whose VENC stocks the menu -- then the trainer
    faction that gates the generated Training topic, then the plugin-origin
    marker, which keeps this file's unscoped dialogue off another converted
    plugin's actors.

    See: docs/commentary/tes5_import_actors.md#vendor-factions
    """
    subs = b''
    for i in range(get_int(rec, 'FactionCount')):
        subs += _pack_snam(get_formid(rec, f'Faction[{i}].FormID'),
                           get_int(rec, f'Faction[{i}].Rank'))
    for vfid in vendor_fids:
        subs += _pack_snam(vfid)
    if trainer_clas_fid and get_trainer_faction_fid():
        subs += _pack_snam(get_trainer_faction_fid())
    for origin_fid in origin_memberships():
        subs += _pack_snam(origin_fid)
    return subs


def _spell_subs(rec: dict) -> bytes:
    """SPCT + SPLO for the actor's spell list (b'' when it has none)."""
    fids = [get_formid(rec, f'Spell[{i}]')
            for i in range(get_int(rec, 'SpellCount'))]
    fids = [f for f in fids if f]
    if not fids:
        return b''
    subs = pack_subrecord('SPCT', struct.pack('<I', len(fids)))
    for fid in fids:
        subs += pack_formid_subrecord('SPLO', fid)
    return subs


def _inventory_subs(carried: list, barter_gold: int) -> bytes:
    """COCT + CNTO for what the actor carries.

    TES5 has no barter-gold field, so a chest-less vendor's ACBS.BarterGold
    becomes carried gold.

    See: docs/commentary/tes5_import_actors.md#vendor-factions
    """
    items = list(carried)
    if barter_gold > 0:
        items.append((GOLD001_FID, barter_gold))
    if not items:
        return b''
    subs = pack_uint32_subrecord('COCT', len(items))
    for fid, count in items:
        subs += pack_subrecord('CNTO', struct.pack('<Ii', fid, count))
    return subs


def _appearance_subs(rec: dict, race_edid: str, gender: str, writer) -> bytes:
    """Head parts, hair color, combat style and the four required NAM slots.

    ZNAM is forced to the vanilla default combat style: CSTY is skipped, so
    the TES4 reference would dangle. NAM6/NAM7 are neutral 1.0 so the race's
    own scale applies.

    See: docs/commentary/tes5_import_actors.md#required-nam-subrecords
    """
    subs = build_pnam_subs(rec, race_edid, gender, writer)

    rgb = (get_int(rec, 'HCLR.R', 100), get_int(rec, 'HCLR.G', 80),
           get_int(rec, 'HCLR.B', 60))
    hclf = (hair_color_formid(writer, *rgb) if writer is not None
            else map_hair_color(*rgb))
    subs += pack_formid_subrecord('HCLF', hclf)

    if get_formid(rec, 'ZNAM.CombatStyle'):
        subs += pack_formid_subrecord('ZNAM', CSTY_DEFAULT)

    subs += pack_subrecord('NAM5', NAM5_UNKNOWN)
    subs += pack_subrecord('NAM6', struct.pack('<f', 1.0))
    subs += pack_subrecord('NAM7', struct.pack('<f', 1.0))
    subs += pack_uint32_subrecord('NAM8', SOUND_LEVEL_NORMAL)
    return subs


def _identity_subs(rec: dict, skyrim_race: int, gender: str, carried: list,
                   vendor_fid: int, trainer_clas_fid: int) -> bytes:
    """Death item through DNAM: the record's identity, stats and behaviour.

    A trainer NPC gets its synthesized CLAS clone; FULL follows CNAM in TES5
    subrecord order.

    See: docs/commentary/tes5_import_actors.md#package-order
    """
    subs = b''
    inam = get_formid(rec, 'INAM.DeathItem')
    if inam:
        subs += pack_formid_subrecord('INAM', inam)

    voice = resolve_actor_voice(rec, gender)
    if voice:
        subs += pack_formid_subrecord('VTCK', voice)

    subs += pack_formid_subrecord('RNAM', skyrim_race)
    subs += _spell_subs(rec)
    subs += _inventory_subs(carried,
                            get_int(rec, 'ACBS.BarterGold') if vendor_fid else 0)
    subs += pack_subrecord('AIDT', build_aidt(rec))

    pack_fids = [get_formid(rec, f'AIPackage[{i}]')
                 for i in range(get_int(rec, 'AIPackageCount'))]
    for pfid in npc_packages(pack_fids):
        subs += pack_formid_subrecord('PKID', pfid)

    cnam = trainer_clas_fid or get_formid(rec, 'CNAM.Class')
    if cnam:
        subs += pack_formid_subrecord('CNAM', cnam)

    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    subs += pack_subrecord('DATA', b'')
    subs += pack_subrecord('DNAM', npc_skills_dnam(rec))
    return subs


def convert_NPC_(rec: dict, writer=None) -> bytes:
    """NPC_ → NPC_ with TES5 restructuring.

    Imports get_object_vmad in the body to break the cycle through
    object_scripts -> constants -> this module.

    See: docs/commentary/tes5_import_actors.md#required-nam-subrecords

    Correct TES5 subrecord order (from wbDefinitionsTES5.pas):
    EDID VMAD OBND ACBS SNAM[] INAM VTCK TPLT RNAM SPCT SPLO[]
    DEST WNAM ANAM ATKR ATKD/ATKE SPOR OCOR GWOR ECOR PRKZ PRKR[]
    COCT CNTO[] AIDT PKID[] KSIZ KWDA CNAM FULL SHRT DATA DNAM
    PNAM[] HCLF ZNAM GNAM NAM5 NAM6 NAM7 NAM8 DOFT SOFT ...
    """
    race_edid, skyrim_race, gender = resolve_npc_race(rec)

    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    from ..base.object_scripts import get_object_vmad
    subs += get_object_vmad(get_formid(rec, 'FormID'))
    subs += pack_obnd(-12, -12, 0, 12, 12, 60)
    subs += pack_subrecord('ACBS', npc_acbs(rec))

    trainer_clas_fid = get_trainer_class_fid(get_formid(rec, 'FormID'))
    vendor_fids = get_vendor_faction_fids_for_actor(
        get_formid(rec, 'FormID'), get_int(rec, 'AIDT.Services'))
    subs += _npc_snams(rec, vendor_fids, trainer_clas_fid)
    vendor_fid = vendor_fids[0] if vendor_fids else 0

    outfit_fids, carried = split_inventory(read_items(rec))
    subs += _identity_subs(rec, skyrim_race, gender, carried,
                           vendor_fid, trainer_clas_fid)

    subs += _appearance_subs(rec, race_edid, gender, writer)

    if writer is not None and outfit_fids:
        subs += pack_formid_subrecord(
            'DOFT', build_outfit(writer, (edid or 'NPC') + '_Outfit',
                                  outfit_fids, get_formid(rec, 'FormID')))

    subs += pack_formid_subrecord('DPLT', DPLT_NPC_LIST)
    subs += build_face_tail_subs(rec, race_edid, gender)

    return pack_record('NPC_', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


# ---------------------------------------------------------------------------
#   Head parts
# ---------------------------------------------------------------------------


def convert_EYES(rec: dict) -> bytes:
    """EYES is not emitted; build_pnam_subs maps eyes onto vanilla HDPTs."""
    return b''


#: HDPT.PNAM 'Type' enum (xEdit wbDefinitionsTES5): 3 = Hair.
HDPT_TYPE_HAIR = 3

#: HDPT.DATA bits: 0 Playable, 1 Male, 2 Female, 3 IsExtraPart, 4 UseSolidTint.
HDPT_FLAG_PLAYABLE = 0x01
HDPT_FLAG_MALE = 0x02
HDPT_FLAG_FEMALE = 0x04

#: HDPT.NAM0 Part Type 1 (Tri); the engine ignores a races-tri on hair.
HDPT_PART_TRI = 1

#: RNAM Valid Races FLSTs (Skyrim.esm); see tes5_import_actors.md.
HDPT_RNAM_HUMANS = 0x000A8023
HDPT_RNAM_ELVES = 0x000A8024
HDPT_RNAM_ORC = 0x000A8032
HDPT_RNAM_ARGONIAN = 0x000A8039
HDPT_RNAM_KHAJIIT = 0x000A8036
HDPT_RNAM_REDGUARD = 0x000A803B
HDPT_RNAM_DREMORA = 0x000A8027
HDPT_RNAM_ALL_MINUS_BEAST = 0x000A803F

#: EditorID token -> its RNAM list; specific elf races precede bare 'elf'.
_HDPT_RNAM_BY_EDID = (
    ('argonian', HDPT_RNAM_ARGONIAN),
    ('khajiit', HDPT_RNAM_KHAJIIT),
    ('orc', HDPT_RNAM_ORC),
    ('dremora', HDPT_RNAM_DREMORA),
    ('darkelf', HDPT_RNAM_ELVES),
    ('highelf', HDPT_RNAM_ELVES),
    ('woodelf', HDPT_RNAM_ELVES),
    ('elf', HDPT_RNAM_ELVES),
    ('redguard', HDPT_RNAM_REDGUARD),
)


def _hdpt_valid_races(edid: str) -> int:
    """The Valid Races FLST for a converted Oblivion hair.

    Race-specific hair goes on that race's list; anything Oblivion did not name
    for a race (Cropped, Ponytail, MediumLength, Blindfold, the styleNN set)
    is generic and goes on HeadPartsAllRacesMinusBeast, matching how Oblivion
    offered those styles to every non-beast race.

    See: docs/commentary/tes5_import_actors.md#hdpt-valid-races
    """
    low = (edid or '').lower()
    for token, flst in _HDPT_RNAM_BY_EDID:
        if token in low:
            return flst
    for token in _HUMAN_HAIR_TOKENS:
        if token in low:
            return HDPT_RNAM_HUMANS
    return HDPT_RNAM_ALL_MINUS_BEAST


#: Human-named styles Oblivion did not tag with a race token.
_HUMAN_HAIR_TOKENS = ('nord', 'imperial', 'breton')

#: Generic hair, once per scalp group: (formid key, mesh group, RNAM FLST).
HDPT_GROUPS = (
    ('',  None,     HDPT_RNAM_HUMANS),
    ('D', None,     HDPT_RNAM_DREMORA),
    ('E', 'elves',  HDPT_RNAM_ELVES),
    ('O', 'orc',    HDPT_RNAM_ORC),
)

#: CLFM.FNAM 'Playable' -- vanilla hair colors are all playable.
_CLFM_PLAYABLE = 1


def hair_color_formid(writer, r: int, g: int, b: int) -> int:
    """CLFM FormID for an authored Oblivion hair color, generating it once.

    Keyed on the authored RGB, so the same color always lands on the same id.
    One record per DISTINCT color, not per actor.

    See: docs/commentary/tes5_import_actors.md#hair-color
    """
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    key = (r, g, b)
    fid = writer.derive_formid('CLFM_HAIR', key)

    cache = getattr(writer, '_tes4_hair_colors', None)
    if cache is None:
        cache = set()
        writer._tes4_hair_colors = cache
    if key in cache:
        return fid
    cache.add(key)

    subs = pack_string_subrecord('EDID', 'TES4HairColor%02X%02X%02X' % key)
    subs += pack_string_subrecord('FULL', 'Hair %02X%02X%02X' % key)
    subs += pack_subrecord('CNAM', struct.pack('<4B', r, g, b, 0))
    subs += pack_subrecord('FNAM', struct.pack('<I', _CLFM_PLAYABLE))
    writer.add_record('CLFM', pack_record('CLFM', fid, 0, subs))
    return fid


def hair_variant_formid(writer, source_fid: int, bucket: int,
                        female: bool, base_female: bool,
                        group: str = '') -> int:
    """The HDPT FormID for one (hair, bucket, gender, race group) variant.

    The BASE variant -- bucket 0 of the hair's base gender in the human
    group -- keeps the SOURCE FormID, so an NPC whose LNAM is 0 resolves
    straight through its HNAM.  Every other variant derives from authored
    data only: the masked source id, the LNAM bucket, the gender ('F') and
    the race group tag ('D'/'E'/'O') -- see HDPT_GROUPS.
    """
    if bucket <= 0 and female == base_female and not group:
        return source_fid
    key = (source_fid & 0x00FFFFFF, bucket)
    if female:
        key = key + ('F',)
    if group:
        key = key + (group,)
    return writer.derive_formid('HDPT_HAIR', key)


def convert_HAIR(rec: dict, *, writer=None) -> bytes:
    """HAIR -> HDPT (Type 3 / Hair), one per (length, gender) variant.

    Returns the base record (unmorphed mesh, base gender, source FormID) and
    side-emits an HDPT for every other variant the plugin's NPCs ask for:

    LENGTH is baked into the mesh per quantized bucket, and each allowed
    GENDER gets its own fitted mesh and HDPT, as vanilla genders every
    hairstyle. Generic hair is emitted once per race GROUP (HDPT_GROUPS).

    See: docs/commentary/tes5_import_actors.md#hdpt-valid-races
    """
    from asset_convert.character.hair_pipeline import (fit_group_lock, hair_genders,
                                             output_model_path,
                                             output_tri_path, variant_edid)
    from asset_convert.character.head_fit import fit_race_for_hair
    from ..actors.hair_variants import hair_buckets_for, hair_has_tri

    model = get_str(rec, 'Model.MODL')
    source_fid = get_formid(rec, 'FormID')
    edid = get_str(rec, 'EditorID')
    want_tri = bool(model) and hair_has_tri(source_fid)
    generic = (fit_race_for_hair(edid) is None
               and fit_group_lock(edid) is None)
    groups = HDPT_GROUPS if generic else (('', None, 0),)

    genders = hair_genders(get_int(rec, 'DATA.Flags'))
    base_female = genders[0]

    def build(bucket, female, tag, name_grp, rnam, fid_override=0):
        """Pack one variant's HDPT for this hair."""
        edid_grp = {'E': 'elves', 'O': 'orc', 'D': 'dremora'}.get(tag)
        return _build_hdpt(
            rec,
            model_override=output_model_path(model, bucket, female, name_grp)
            if model else '',
            tri_path=output_tri_path(model, bucket, female, name_grp)
            if want_tri else '',
            edid_override=variant_edid(edid, bucket, female, edid_grp),
            fid_override=fid_override,
            female=female,
            rnam_override=rnam)

    base = build(0, base_female, '', None, groups[0][2])
    if writer is None:
        return base

    for female in genders:
        for bucket in hair_buckets_for(source_fid):
            for tag, name_grp, rnam in groups:
                if bucket <= 0 and female == base_female and not tag:
                    continue
                vid = hair_variant_formid(writer, source_fid, bucket,
                                          female, base_female, tag)
                writer.add_record('HDPT', build(bucket, female, tag,
                                                name_grp, rnam, vid))
    return base


def _build_hdpt(rec: dict, *, model_override: str = '',
                tri_path: str = '', edid_override: str = '',
                fid_override: int = 0, female: bool = False,
                rnam_override: int = 0) -> bytes:
    """Pack one HDPT (Type 3 / Hair).

    `tri_path` names the emitted Skyrim .tri, the SkinnyMorph slot the engine
    reads for head parts. Each variant's mesh is fitted to ONE gender's head,
    so the record is single-gender by construction. PNAM is required -- the CK
    rejects the record without it -- and RNAM gives group variants their
    group's list while race-named hair keys off the SOURCE EditorID.

    See: docs/commentary/tes5_import_actors.md#hdpt-valid-races
    """
    subs = b''

    edid = edid_override or get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    model = model_override or get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', prefix_path(model))
        subs += pack_subrecord('MODT', struct.pack('<III', 2, 0, 0))

    flags = HDPT_FLAG_PLAYABLE | (
        HDPT_FLAG_FEMALE if female else HDPT_FLAG_MALE)
    subs += pack_uint8_subrecord('DATA', flags)

    subs += pack_uint32_subrecord('PNAM', HDPT_TYPE_HAIR)

    if tri_path:
        subs += pack_uint32_subrecord('NAM0', HDPT_PART_TRI)
        subs += pack_string_subrecord('NAM1', prefix_path(tri_path))

    subs += pack_formid_subrecord(
        'RNAM', rnam_override or _hdpt_valid_races(get_str(rec, 'EditorID')))

    fid = fid_override or get_formid(rec, 'FormID')
    return pack_record('HDPT', fid, get_int(rec, 'RecordFlags'), subs)

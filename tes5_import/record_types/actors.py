"""Actor/NPC converters: NPC_, CREA, FACT, EYES, HAIR, CLAS, GLOB, GMST, leveled lists."""

import struct

from ..constants import DEFAULT_RACE, RACE_MAP, TES4_SKILL_TO_TES5, TES5_SKILL_ORDER
from ..npc_face_mapper import build_face_tail_subs, build_pnam_subs
from ..skyrim_overrides import (
    ATTRIBUTE_SKILL_MAP,
    TES4_RACE_FID_TO_EDID,
    VOICE_TYPE_MAP,
    map_hair_color,
    resolve_creature_race,
)
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
    pack_uint32_subrecord,
)


def _npc_skills_dnam(rec: dict) -> bytes:
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
    struct.pack_into('<H', dnam, 36, min(health, 65535))
    intelligence = get_int(rec, 'DATA.Intelligence', 50)
    struct.pack_into('<H', dnam, 38, min(intelligence, 65535))
    strength = get_int(rec, 'DATA.Strength', 50)
    struct.pack_into('<H', dnam, 40, min(strength, 65535))
    return bytes(dnam)


def _npc_aidt(rec: dict) -> bytes:
    """Build TES5 AIDT subrecord (12 bytes)."""
    aggr = get_int(rec, 'AIDT.Aggression')
    conf = get_int(rec, 'AIDT.Confidence')
    energy = get_int(rec, 'AIDT.EnergyLevel', 50)
    resp = get_int(rec, 'AIDT.Responsibility')
    tes5_aggr = 2 if aggr >= 70 else (1 if aggr >= 40 else 0)
    tes5_conf = 0 if conf < 30 else (3 if conf >= 70 else 2)
    tes5_assist = 1 if resp >= 30 else 0
    services = get_int(rec, 'AIDT.Services')
    return struct.pack('<BBBBBBHI', tes5_aggr, tes5_conf, energy,
                       0, 4, tes5_assist, 0, services)


def _resolve_npc_race(rec: dict):
    """Resolve TES4 race FormID to (race_edid, skyrim_race_fid, gender_str)."""
    tes4_race_fid = get_formid(rec, 'RNAM.Race')
    # Mask off load-order high byte — TES4_RACE_FID_TO_EDID uses base FormIDs
    race_edid = TES4_RACE_FID_TO_EDID.get(tes4_race_fid & 0x00FFFFFF, 'Imperial')
    skyrim_race = RACE_MAP.get(race_edid, DEFAULT_RACE)
    tes4_flags = get_int(rec, 'ACBS.Flags')
    gender = 'Female' if (tes4_flags & 1) else 'Male'
    return race_edid, skyrim_race, gender


def convert_NPC_(rec: dict, writer=None) -> bytes:
    """NPC_ → NPC_ with TES5 restructuring.

    Correct TES5 subrecord order (from wbDefinitionsTES5.pas):
    EDID VMAD OBND ACBS SNAM[] INAM VTCK TPLT RNAM SPCT SPLO[]
    DEST WNAM ANAM ATKR ATKD/ATKE SPOR OCOR GWOR ECOR PRKZ PRKR[]
    COCT CNTO[] AIDT PKID[] KSIZ KWDA CNAM FULL SHRT DATA DNAM
    PNAM[] HCLF ZNAM GNAM NAM5 NAM6 NAM7 NAM8 DOFT SOFT ...
    """
    race_edid, skyrim_race, gender = _resolve_npc_race(rec)

    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # OBND (NPC_ default bounds)
    subs += pack_obnd(-12, -12, 0, 12, 12, 60)

    # ACBS — 24 bytes in TES5
    tes4_flags = get_int(rec, 'ACBS.Flags')
    level = get_int(rec, 'ACBS.Level', 1)
    calc_min = get_int(rec, 'ACBS.CalcMin', 1)
    calc_max = get_int(rec, 'ACBS.CalcMax', 100)
    # Keep compatible bits + preserve Female flag (bit 0)
    tes5_acbs_flags = tes4_flags & 0x4C9B
    is_pc_level = bool(tes4_flags & 0x80)
    # TES4 PCLevelOffset: level is an additive offset from the player's level.
    # TES5 PCLevelMult: level is a fixed-point multiplier (1000 = 1.0×).
    # We can't map an offset directly to a multiplier so default to 1.0×.
    tes5_level = 1000 if is_pc_level else max(1, level)
    endurance = get_int(rec, 'DATA.Endurance', 50)
    intelligence = get_int(rec, 'DATA.Intelligence', 50)
    strength = get_int(rec, 'DATA.Strength', 50)
    acbs = struct.pack('<IhhhHHHhHhH',
                       tes5_acbs_flags, intelligence, strength, tes5_level,
                       min(calc_min * 2, 1000), min(calc_max * 2, 1000),
                       100, 0, 0, endurance, 0)
    subs += pack_subrecord('ACBS', acbs)

    # SNAM — Factions
    fc = get_int(rec, 'FactionCount')
    for i in range(fc):
        fid = get_formid(rec, f'Faction[{i}].FormID')
        rank = get_int(rec, f'Faction[{i}].Rank')
        subs += pack_subrecord('SNAM', struct.pack('<Ib', fid, rank))

    # INAM — Death item
    inam = get_formid(rec, 'INAM.DeathItem')
    if inam:
        subs += pack_formid_subrecord('INAM', inam)

    # VTCK — Voice type (custom VTYP created in Phase 0)
    # Fall back to Imperial if the exact race/gender is not in the map
    voice = (VOICE_TYPE_MAP.get((race_edid, gender))
             or VOICE_TYPE_MAP.get(('Imperial', gender), 0))
    if voice:
        subs += pack_formid_subrecord('VTCK', voice)

    # RNAM — Race (mapped to Skyrim equivalent)
    subs += pack_formid_subrecord('RNAM', skyrim_race)

    # SPCT + SPLO — Spells
    sc = get_int(rec, 'SpellCount')
    if sc > 0:
        spell_fids = [get_formid(rec, f'Spell[{i}]') for i in range(sc)]
        spell_fids = [s for s in spell_fids if s]
        if spell_fids:
            subs += pack_subrecord('SPCT', struct.pack('<I', len(spell_fids)))
            for sfid in spell_fids:
                subs += pack_formid_subrecord('SPLO', sfid)

    # COCT + CNTO — Items (also collected for OTFT outfit creation)
    item_fids = []
    ic = get_int(rec, 'ItemCount')
    if ic > 0:
        coct = 0
        item_data = b''
        for i in range(ic):
            fid = get_formid(rec, f'Item[{i}].FormID')
            count = get_int(rec, f'Item[{i}].Count', 1)
            if fid:
                item_data += pack_subrecord('CNTO', struct.pack('<Ii', fid, count))
                item_fids.append(fid)
                coct += 1
        if coct:
            subs += pack_uint32_subrecord('COCT', coct)
            subs += item_data

    # AIDT — AI data
    subs += pack_subrecord('AIDT', _npc_aidt(rec))

    # PKID — AI packages
    pc = get_int(rec, 'AIPackageCount')
    for i in range(pc):
        pfid = get_formid(rec, f'AIPackage[{i}]')
        if pfid:
            subs += pack_formid_subrecord('PKID', pfid)

    # CNAM — Class
    cnam = get_formid(rec, 'CNAM.Class')
    if cnam:
        subs += pack_formid_subrecord('CNAM', cnam)

    # FULL — Name (comes after CNAM in TES5!)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    # DATA — empty marker (0 bytes)
    subs += pack_subrecord('DATA', b'')

    # DNAM — Skills + stats (52 bytes)
    subs += pack_subrecord('DNAM', _npc_skills_dnam(rec))

    # PNAM[] — Head parts: hair HDPT + eyes HDPT
    subs += build_pnam_subs(rec, race_edid, gender)

    # HCLF — Hair color (mapped to closest Skyrim CLFM)
    hclr_r = get_int(rec, 'HCLR.R', 100)
    hclr_g = get_int(rec, 'HCLR.G', 80)
    hclr_b = get_int(rec, 'HCLR.B', 60)
    subs += pack_formid_subrecord('HCLF', map_hair_color(hclr_r, hclr_g, hclr_b))

    # ZNAM — Combat style
    znam = get_formid(rec, 'ZNAM.CombatStyle')
    if znam:
        subs += pack_formid_subrecord('ZNAM', znam)

    # NAM6 / NAM7 — Height / Weight (TES4 has these on RACR records, not NPC;
    # use neutral defaults so the race's own scale applies)
    subs += pack_subrecord('NAM6', struct.pack('<f', 1.0))
    subs += pack_subrecord('NAM7', struct.pack('<f', 1.0))

    # DOFT — Default outfit (requires OTFT companion record)
    # TES4 NPCs equip items from CNTO inventory; TES5 requires OTFT + DOFT.
    if writer is not None and item_fids:
        otft_fid = writer.alloc_formid()
        otft_subs = b''
        otft_edid = (edid or 'NPC') + '_Outfit'
        otft_subs += pack_string_subrecord('EDID', otft_edid)
        # INAM — array of item FormIDs (packed as consecutive 4-byte LE uint32)
        inam_data = b''.join(struct.pack('<I', fid) for fid in item_fids)
        otft_subs += pack_subrecord('INAM', inam_data)
        writer.add_record('OTFT', pack_record('OTFT', otft_fid, 0, otft_subs))
        subs += pack_formid_subrecord('DOFT', otft_fid)

    # Trailing face data: FTST, QNAM, NAM9, NAMA
    subs += build_face_tail_subs(rec, race_edid, gender)

    return pack_record('NPC_', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_CREA(rec: dict, writer=None) -> bytes:
    """CREA → NPC_ (creatures become NPCs in TES5).

    Same subrecord order as NPC_: EDID OBND ACBS SNAM INAM RNAM
    COCT/CNTO AIDT PKID CNAM FULL DATA DNAM DOFT
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd(-12, -12, 0, 12, 12, 60)  # NPC_ default bounds

    # ACBS — auto-calc stats for creatures
    tes4_flags = get_int(rec, 'ACBS.Flags')
    level = get_int(rec, 'ACBS.Level', 1)
    calc_min = get_int(rec, 'ACBS.CalcMin', 1)
    calc_max = get_int(rec, 'ACBS.CalcMax', 100)
    tes5_flags = (tes4_flags & 0x4C9B) | 0x10
    acbs = struct.pack('<IhhhHHHhHhH',
                       tes5_flags, 0, 0, max(1, level),
                       calc_min, calc_max, 100, 0, 0, 0, 0)
    subs += pack_subrecord('ACBS', acbs)

    # Factions
    fc = get_int(rec, 'FactionCount')
    for i in range(fc):
        fid = get_formid(rec, f'Faction[{i}].FormID')
        rank = get_int(rec, f'Faction[{i}].Rank')
        subs += pack_subrecord('SNAM', struct.pack('<Ib', fid, rank))

    # Death item
    inam = get_formid(rec, 'INAM.DeathItem')
    if inam:
        subs += pack_formid_subrecord('INAM', inam)

    # Race — resolved from creature EditorID/name via CREA_RACE_PATTERNS
    full = get_str(rec, 'FULL')
    crea_race_fid, _src, _alt = resolve_creature_race(edid, full)
    subs += pack_formid_subrecord('RNAM', crea_race_fid)

    # Items
    item_fids = []
    ic = get_int(rec, 'ItemCount')
    if ic > 0:
        coct = 0
        item_data = b''
        for i in range(ic):
            fid = get_formid(rec, f'Item[{i}].FormID')
            count = get_int(rec, f'Item[{i}].Count', 1)
            if fid:
                item_data += pack_subrecord('CNTO', struct.pack('<Ii', fid, count))
                item_fids.append(fid)
                coct += 1
        if coct:
            subs += pack_uint32_subrecord('COCT', coct)
            subs += item_data

    # AIDT
    aggr = get_int(rec, 'AIDT.Aggression')
    conf = get_int(rec, 'AIDT.Confidence')
    services = get_int(rec, 'AIDT.Services')
    tes5_aggr = 2 if aggr >= 70 else (1 if aggr >= 40 else 0)
    tes5_conf = 0 if conf < 30 else (3 if conf >= 70 else 2)
    aidt = struct.pack('<BBBBBBHI', tes5_aggr, tes5_conf, 50, 0, 4, 0, 0, services)
    subs += pack_subrecord('AIDT', aidt)

    # AI packages
    pc = get_int(rec, 'AIPackageCount')
    for i in range(pc):
        pfid = get_formid(rec, f'AIPackage[{i}]')
        if pfid:
            subs += pack_formid_subrecord('PKID', pfid)

    # FULL — Name (after PKID in TES5 NPC_ order)
    if full:
        subs += pack_string_subrecord('FULL', full)

    # DATA — empty marker
    subs += pack_subrecord('DATA', b'')

    # DNAM — Skills from creature aggregate stats
    dnam = bytearray(52)
    combat = get_int(rec, 'DATA.CombatSkill', 30)
    magic = get_int(rec, 'DATA.MagicSkill', 30)
    stealth = get_int(rec, 'DATA.StealthSkill', 30)

    skill_defaults = {
        'OneHanded': combat, 'TwoHanded': combat, 'Block': combat, 'Smithing': combat,
        'HeavyArmor': combat, 'LightArmor': stealth,
        'Marksman': stealth, 'Sneak': stealth, 'Lockpicking': stealth, 'Pickpocket': stealth,
        'Destruction': magic, 'Conjuration': magic, 'Alteration': magic,
        'Illusion': magic, 'Restoration': magic,
        'Alchemy': magic, 'Speechcraft': stealth, 'Enchanting': magic // 3,
    }
    for i, skill_name in enumerate(TES5_SKILL_ORDER):
        dnam[i] = min(skill_defaults.get(skill_name, 15), 255)

    health = get_int(rec, 'DATA.Health', 50)
    struct.pack_into('<H', dnam, 36, min(health, 65535))
    cr_int = get_int(rec, 'DATA.Intelligence', 50)
    struct.pack_into('<H', dnam, 38, min(cr_int, 65535))
    cr_str = get_int(rec, 'DATA.Strength', 50)
    struct.pack_into('<H', dnam, 40, min(cr_str, 65535))
    subs += pack_subrecord('DNAM', bytes(dnam))

    # DOFT — Default outfit
    if writer is not None and item_fids:
        otft_fid = writer.alloc_formid()
        otft_subs = b''
        otft_edid = (edid or 'CREA') + '_Outfit'
        otft_subs += pack_string_subrecord('EDID', otft_edid)
        inam_data = b''.join(struct.pack('<I', fid) for fid in item_fids)
        otft_subs += pack_subrecord('INAM', inam_data)
        writer.add_record('OTFT', pack_record('OTFT', otft_fid, 0, otft_subs))
        subs += pack_formid_subrecord('DOFT', otft_fid)

    return pack_record('NPC_', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_FACT(rec: dict) -> bytes:
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    # Relations
    rc = get_int(rec, 'RelationCount')
    for i in range(rc):
        fid = get_formid(rec, f'Relation[{i}].Faction')
        disp = get_int(rec, f'Relation[{i}].Disposition')
        # Convert disposition → combat reaction
        if disp <= -50:
            reaction = 1    # Enemy
        elif disp >= 100:
            reaction = 3    # Ally
        elif disp >= 50:
            reaction = 2    # Friend
        else:
            reaction = 0    # Neutral
        subs += pack_subrecord('XNAM', struct.pack('<IiI', fid, disp, reaction))

    # DATA — Flags
    tes4_flags = get_int(rec, 'DATA.Flags')
    tes5_flags = tes4_flags | 0x8000  # Can Be Owner always set
    # Evil flag → Crime flags
    if tes4_flags & 0x02:
        tes5_flags |= 0x0080 | 0x0100 | 0x0200 | 0x0400 | 0x0800 | 0x2000 | 0x10000
    subs += pack_subrecord('DATA', struct.pack('<I', tes5_flags))

    # CNAM → CRVA (Crime Values)
    crime_gold = get_float(rec, 'CNAM.CrimeGold', 1.0)
    crva = struct.pack('<HHHHIfI', 0, 0, 0, 0, 0, crime_gold, 0)
    subs += pack_subrecord('CRVA', crva)

    return pack_record('FACT', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_EYES(rec: dict) -> bytes:
    # Map to TES5 record formIDs instead
    pass


def convert_HAIR(rec: dict) -> bytes:
    # Map to TES5 record formIDs instead
    pass


def convert_CLAS(rec: dict) -> bytes:
    """CLAS — TES5 DATA is 36 bytes with Skyblivion skill-weight algorithm."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)
    desc = get_str(rec, 'DESC', '')
    subs += pack_string_subrecord('DESC', desc)

    # --- Skill weight algorithm (from Skyblivion) ---
    weights = {s: 0 for s in TES5_SKILL_ORDER}

    # 1) Specialization adds +2 to its skill group
    spec = get_int(rec, 'DATA.Specialization')
    SPEC_SKILLS = {
        0: ['OneHanded', 'TwoHanded', 'Block', 'Smithing', 'HeavyArmor', 'Marksman'],   # Combat
        1: ['Alteration', 'Conjuration', 'Destruction', 'Illusion', 'Restoration', 'Enchanting'],  # Magic
        2: ['Sneak', 'LightArmor', 'Lockpicking', 'Pickpocket', 'Speechcraft', 'Alchemy'],  # Stealth
    }
    for skill in SPEC_SKILLS.get(spec, []):
        weights[skill] = weights.get(skill, 0) + 2

    # 2) Two primary attributes: each attribute's associated skills get +1
    TES4_ATTR_NAMES = ['Strength', 'Intelligence', 'Willpower', 'Agility',
                       'Speed', 'Endurance', 'Personality', 'Luck']
    for attr_idx_key in ('DATA.PrimaryAttribute1', 'DATA.PrimaryAttribute2'):
        attr_idx = get_int(rec, attr_idx_key)
        if 0 <= attr_idx < len(TES4_ATTR_NAMES):
            attr_name = TES4_ATTR_NAMES[attr_idx]
            if attr_name == 'Luck':
                for s in TES5_SKILL_ORDER:
                    weights[s] = weights.get(s, 0) + 1
            else:
                for skill in ATTRIBUTE_SKILL_MAP.get(attr_name, []):
                    weights[skill] = weights.get(skill, 0) + 1

    # 3) Seven major skills: mapped to TES5 equivalents, each gets +3
    for i in range(7):
        tes4_skill = get_int(rec, f'DATA.MajorSkill[{i}]')
        tes5_name = TES4_SKILL_TO_TES5.get(tes4_skill)
        if tes5_name:
            weights[tes5_name] = weights.get(tes5_name, 0) + 3

    # Clamp to 0-255
    skill_weights = bytes(min(255, max(0, weights.get(s, 0))) for s in TES5_SKILL_ORDER)

    # Teaches: TES4 stores a 0-based index (0=Armorer, 1=Athletics, 2=Blade...).
    # TES4 actor values for skills start at 12, so add 12 to get the actor value
    # that TES4_SKILL_TO_TES5 uses as keys.
    teaches_tes4 = get_int(rec, 'DATA.Teaches') + 12
    teaches_tes5_name = TES4_SKILL_TO_TES5.get(teaches_tes4)
    if teaches_tes5_name and teaches_tes5_name in TES5_SKILL_ORDER:
        teaches = TES5_SKILL_ORDER.index(teaches_tes5_name)
    else:
        teaches = 0

    max_train = get_int(rec, 'DATA.MaxTraining')

    # TES5 CLAS DATA: Unknown(4) + Teaches(S8,1) + MaxTraining(U8,1) + SkillWeights(18) + Bleedout(float,4) + VoicePoints(U32,4) + AttrWeights(4×U8) = 36 bytes
    data = struct.pack('<I', 0xFFFC0000)  # Flags (vanilla default)
    data += struct.pack('<bB', teaches, max_train)
    data += skill_weights                  # 18 bytes
    data += struct.pack('<f', 0.1)        # Bleedout default (vanilla=0.1)
    data += struct.pack('<I', 0)          # Voice points (vanilla default)
    data += struct.pack('<4B', 1, 1, 1, 0)  # Attr weights: Health, Magicka, Stamina, Unknown
    subs += pack_subrecord('DATA', data)

    return pack_record('CLAS', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_GLOB(rec: dict) -> bytes:
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    type_char = get_str(rec, 'FNAM.Type', 'f')
    subs += pack_uint8_subrecord('FNAM', ord(type_char[0]) if type_char else ord('f'))
    value = get_float(rec, 'FLTV.Value')
    subs += pack_float_subrecord('FLTV', value)
    return pack_record('GLOB', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_GMST(rec: dict) -> bytes:
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    value_str = get_str(rec, 'DATA.Value')
    if edid and edid.startswith('s'):
        subs += pack_string_subrecord('DATA', value_str)
    elif edid and edid.startswith('f'):
        try:
            subs += pack_float_subrecord('DATA', float(value_str))
        except ValueError:
            subs += pack_uint32_subrecord('DATA', 0)
    else:
        try:
            subs += pack_uint32_subrecord('DATA', int(value_str))
        except ValueError:
            subs += pack_uint32_subrecord('DATA', 0)
    return pack_record('GMST', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


# ---------------------------------------------------------------------------
# Leveled list converters
# ---------------------------------------------------------------------------


def _convert_leveled_list(rec: dict, tes5_sig: str) -> bytes:
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd()

    chance = get_int(rec, 'LVLD.ChanceNone')
    subs += pack_uint8_subrecord('LVLD', chance)
    flags = get_int(rec, 'LVLF.Flags')
    subs += pack_uint8_subrecord('LVLF', flags)

    # Entry count — LLCT is U8 in TES5
    ec = get_int(rec, 'EntryCount')
    if ec > 0:
        subs += pack_subrecord('LLCT', struct.pack('<B', min(ec, 255)))
    for i in range(ec):
        level = get_int(rec, f'Entry[{i}].Level', 1)
        fid = get_formid(rec, f'Entry[{i}].FormID')
        count = get_int(rec, f'Entry[{i}].Count', 1)
        # LVLO: Level(U16) + pad(U16) + FormID(U32) + Count(U16) + pad(U16) = 12 bytes
        subs += pack_subrecord('LVLO', struct.pack('<HxxIHxx', level, fid, count))

    return pack_record(tes5_sig, get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_LVLI(rec: dict) -> bytes:
    return _convert_leveled_list(rec, 'LVLI')


def convert_LVLC(rec: dict) -> bytes:
    """LVLC → LVLN (Leveled NPC)."""
    return _convert_leveled_list(rec, 'LVLN')


def convert_LVSP(rec: dict) -> bytes:
    return _convert_leveled_list(rec, 'LVSP')


# ---------------------------------------------------------------------------
# World / Placement converters
# ---------------------------------------------------------------------------

"""Actor/NPC converters: NPC_, CREA, FACT, EYES, HAIR, CLAS, GLOB, GMST, leveled lists."""

import struct

from ..constants import DEFAULT_RACE, RACE_MAP, TES4_SKILL_TO_TES5, TES5_SKILL_ORDER
from ..npc_face_mapper import build_face_tail_subs, build_pnam_subs
from ..outfits import split_inventory
from ..packages import (
    CSTY_ANIMAL,
    CSTY_DEFAULT,
    DPLT_CREATURE_LIST,
    DPLT_NPC_LIST,
    PKID_CREATURE_MASTER,
    npc_packages,
)
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


def _read_items(rec: dict) -> list:
    """Actor's TES4 CNTO inventory as [(fid, count)], in export order."""
    items = []
    for i in range(get_int(rec, 'ItemCount')):
        fid = get_formid(rec, f'Item[{i}].FormID')
        if fid:
            items.append((fid, get_int(rec, f'Item[{i}].Count', 1)))
    return items


def _build_outfit(writer, edid: str, outfit_fids: list) -> int:
    """Emit the OTFT companion record for an actor and return its FormID."""
    otft_fid = writer.alloc_formid()
    subs = pack_string_subrecord('EDID', edid)
    # INAM — item FormIDs packed as consecutive 4-byte LE uint32
    subs += pack_subrecord(
        'INAM', b''.join(struct.pack('<I', fid) for fid in outfit_fids))
    writer.add_record('OTFT', pack_record('OTFT', otft_fid, 0, subs))
    return otft_fid


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
    """Build TES5 AIDT subrecord (20 bytes).

    TES5 layout:
      00: Aggression U8  (0=Unaggressive,1=Aggressive,2=VeryAggressive,3=Frenzied)
      01: Confidence U8  (0=Cowardly,1=Cautious,2=Average,3=Brave,4=Foolhardy)
      02: Energy U8
      03: Morality U8    (0=AnyCrime,1=ViolenceAgainstEnemies,2=PropertyCrimeOnly,3=NoCrime)
      04: Mood U8        (0=Neutral)
      05: Assistance U8  (0=HelpsNobody,1=HelpsAllies,2=HelpsFriendsAndAllies)
      06: AggroRadiusBehavior U8
      07: Unused U8
      08: Warn U32
      0C: Warn/Attack U32
      10: Attack U32
    """
    aggr = get_int(rec, 'AIDT.Aggression')
    conf = get_int(rec, 'AIDT.Confidence')
    energy = get_int(rec, 'AIDT.EnergyLevel', 50)
    resp = get_int(rec, 'AIDT.Responsibility')
    # TES4 aggression → TES5 tier. TES4 semantics: attack anything with
    # disposition < aggression, default 5 — so anything above 5 initiates
    # combat against disliked targets and maps to TES5 1 (attacks enemies).
    # The old >=40 threshold left mid-range actors (dog=30) at 0 =
    # Unaggressive, which never initiates combat at all.
    tes5_aggr = 2 if aggr >= 70 else (1 if aggr > 5 else 0)
    # TES4 confidence → TES5 tier
    tes5_conf = 0 if conf < 30 else (3 if conf >= 70 else 2)
    # TES4 responsibility → TES5 morality (inverted: high resp = no crime)
    tes5_moral = 3 if resp >= 80 else (2 if resp >= 50 else (1 if resp >= 30 else 0))
    # Assistance: low responsibility → helps nobody
    tes5_assist = 1 if resp >= 30 else 0

    return struct.pack('<BBBBBB BB III',
                       tes5_aggr, tes5_conf, energy,
                       tes5_moral, 0, tes5_assist,  # mood=0 (Neutral)
                       0, 0,                          # aggro radius, unused
                       0, 0, 0)                       # warn, warn/attack, attack


# ---------------------------------------------------------------------------
#   Vendor Faction System
# ---------------------------------------------------------------------------

# Skyrim.esm Gold001 (index 0 in the output load order)
GOLD001_FID = 0x0000000F

# TES4 AIDT.Services bitmask → Skyrim VendorItem KYWD FormIDs.
# Training (bit 14), Recharge (bit 16), Repair (bit 17) have no vendor keyword
# equivalent — Training is handled by CLAS, the others are TES4-only.
# MUST stay in sync with the keywords the item converters emit (VENDOR_KYWD in
# record_types/common.py): a vendor only trades items whose keywords appear in
# its faction's VEND formlist.
_TES4_SERVICE_BIT_TO_SKYRIM_KEYWORDS = {
    0:  [0x0008F958, 0x000917E7],             # Weapons → VendorItemWeapon + Arrow
    1:  [0x0008F959],                         # Armor   → VendorItemArmor
    2:  [0x0008F95B, 0x0008F95A],             # Clothing → VendorItemClothing + Jewelry
    3:  [0x000937A2, 0x000A0E57],             # Books → VendorItemBook + Scroll
    4:  [0x0008CDEB, 0x000A0E56,
         0x0008CDEA],                         # Ingredients → Ingredient + FoodRaw + Food (TES4 food = ingredients)
    7:  [0x000914E9],                         # Lights → VendorItemClutter
    8:  [0x000914E9],                         # Apparatus → VendorItemClutter (no TES5 apparatus)
    10: [0x000914ED, 0x000914EA, 0x000914EC,
         0x000914EE, 0x000914E9],             # Misc → Gem + AnimalHide + OreIngot + Tool + Clutter
    11: [0x000937A5, 0x000A0E57,
         0x000937A4],                         # Spells → SpellTome + Scroll + Staff
    12: [0x000937A3, 0x000937A4],             # MagicItems → SoulGem + Staff
    13: [0x0008CDEC, 0x0008CDED,
         0x0008CDEA],                         # Potions → Potion + Poison + Food
}

# Module-level cache: service_bitmask → vendor FACT FormID (populated by Phase 0c)
_vendor_faction_cache: dict[int, int] = {}


def _keywords_for_services(services: int) -> list[int]:
    """Return unique sorted Skyrim KYWD FormIDs for a TES4 services bitmask."""
    kw_set = set()
    for bit, kwds in _TES4_SERVICE_BIT_TO_SKYRIM_KEYWORDS.items():
        if services & (1 << bit):
            kw_set.update(kwds)
    return sorted(kw_set)


def create_vendor_factions(by_type: dict, writer) -> None:
    """Phase 0c: Pre-scan NPC_/CREA for services and create vendor FACTs + FLSTs.

    For each unique TES4 services bitmask combination:
    1. Create an FLST containing the mapped Skyrim VendorItem keywords
    2. Create a FACT with Vendor flag (0x4000) and VEND → that FLST

    The NPC/CREA converters look up _vendor_faction_cache[services] to inject
    a faction membership SNAM when writing the record.
    """
    _vendor_faction_cache.clear()

    # Collect unique non-zero, non-training-only service bitmasks
    # (Training alone = bit 14 has no vendor keyword, handled by CLAS)
    unique_services = set()
    for sig in ('NPC_', 'CREA'):
        for rec in by_type.get(sig, []):
            svc = get_int(rec, 'AIDT.Services')
            # Mask out training/recharge/repair bits for vendor list purposes
            vendor_bits = svc & ~((1 << 14) | (1 << 16) | (1 << 17))
            if vendor_bits:
                unique_services.add(vendor_bits)

    if not unique_services:
        return

    print(f"  Creating vendor factions for {len(unique_services)} service combos...")

    for svc_mask in sorted(unique_services):
        kwds = _keywords_for_services(svc_mask)
        if not kwds:
            continue

        # Also always include VendorNoSale (0x000FF9FB) — prevents selling quest items
        kwds.append(0x000FF9FB)

        # Create FLST
        flst_fid = writer.alloc_formid()
        flst_subs = pack_string_subrecord('EDID', f'TES4VendorList_{svc_mask:06X}')
        for kw_fid in kwds:
            flst_subs += pack_formid_subrecord('LNAM', kw_fid)
        writer.add_record('FLST', pack_record('FLST', flst_fid, 0, flst_subs))

        # Create FACT with vendor data
        fact_fid = writer.alloc_formid()
        fact_subs = pack_string_subrecord('EDID', f'TES4VendorFaction_{svc_mask:06X}')
        fact_subs += pack_string_subrecord('FULL', f'TES4 Vendor ({svc_mask:06X})')
        # DATA: Vendor (0x4000) + CanBeOwner (0x8000)
        fact_subs += pack_subrecord('DATA', struct.pack('<I', 0xC000))
        # CRVA — Crime values (20 bytes of mostly zeros, like vanilla)
        fact_subs += pack_subrecord('CRVA', b'\x01\x01' + b'\x00' * 18)
        # VEND — Vendor buy/sell list → FLST
        fact_subs += pack_formid_subrecord('VEND', flst_fid)
        # VENV — Vendor values: 24h availability, no stolen-only, not sell-buy-only
        # StartHour(U16) + EndHour(U16) + Radius(U16) + Unused(2B) +
        # OnlyBuyStolenItems(U8) + NotSellBuy(U8) + Unused(2B) = 12 bytes
        fact_subs += pack_subrecord('VENV', struct.pack('<HHH BB BB BB',
                                                        0, 24, 0, 0, 0, 0, 0, 0, 0))
        writer.add_record('FACT', pack_record('FACT', fact_fid, 0, fact_subs))

        _vendor_faction_cache[svc_mask] = fact_fid


def get_vendor_faction_fid(services: int) -> int:
    """Return the vendor FACT FormID for a TES4 services bitmask, or 0."""
    vendor_bits = services & ~((1 << 14) | (1 << 16) | (1 << 17))
    return _vendor_faction_cache.get(vendor_bits, 0)


def get_vendor_faction_fids() -> list[int]:
    """All vendor FACT FormIDs (for the barter-topic GetInFaction OR-chain)."""
    return sorted(set(_vendor_faction_cache.values()))


# ---------------------------------------------------------------------------
#   Trainer System
# ---------------------------------------------------------------------------
# Skyrim's training menu reads the trainer's skill and level cap from the
# NPC's CLASS (CLAS DATA Teaches/MaxTrainingLevel), but Oblivion stores them
# per-NPC in AIDT (the class fields are just CS defaults — 92 of 114 vanilla
# trainers disagree with their class). So each trainer NPC gets a clone of its
# own class with Teaches/MaxTraining replaced, plus membership in a synthetic
# trainer faction that gates the generated Training dialogue topic.

_trainer_faction_fid = 0
_trainer_class_by_npc: dict[int, int] = {}   # remapped NPC fid -> CLAS clone fid


def _npc_trainer_params(rec: dict):
    """(teaches_tes5_index, max_level) for a trainer NPC, or None.

    A trainer offers the Training service (AIDT bit 14) with a level cap > 0
    and a skill that still exists in Skyrim (Athletics/Acrobatics don't).
    """
    svc = get_int(rec, 'AIDT.Services')
    if not (svc & (1 << 14)):
        return None
    max_train = get_int(rec, 'AIDT.MaxTraining')
    if max_train <= 0:
        return None
    teaches_name = TES4_SKILL_TO_TES5.get(get_int(rec, 'AIDT.Teaches') + 12)
    if not teaches_name or teaches_name not in TES5_SKILL_ORDER:
        return None
    return TES5_SKILL_ORDER.index(teaches_name), min(255, max_train)


def create_trainer_records(by_type: dict, writer) -> None:
    """Phase 0c2: trainer FACT + per-trainer CLAS clones for NPC_ trainers."""
    global _trainer_faction_fid
    _trainer_faction_fid = 0
    _trainer_class_by_npc.clear()

    clas_by_fid = {get_formid(r, 'FormID'): r for r in by_type.get('CLAS', [])
                   if get_formid(r, 'FormID')}

    trainers = []   # (npc_fid, clas_rec_or_None, teaches_idx, max_level)
    for rec in by_type.get('NPC_', []):
        params = _npc_trainer_params(rec)
        if not params:
            continue
        clas_rec = clas_by_fid.get(get_formid(rec, 'CNAM.Class'))
        trainers.append((get_formid(rec, 'FormID'), clas_rec, *params))

    if not trainers:
        return
    print(f"  Creating trainer records for {len(trainers)} trainer NPCs...")

    # One faction marks every trainer; the generated Training topic is gated
    # on GetInFaction(this). Flags 0 like vanilla JobTrainerFaction.
    _trainer_faction_fid = writer.alloc_formid()
    f = pack_string_subrecord('EDID', 'TES4JobTrainerFaction')
    f += pack_string_subrecord('FULL', 'Trainer')
    f += pack_subrecord('DATA', struct.pack('<I', 0))
    f += pack_subrecord('CRVA', b'\x01\x01' + b'\x00' * 18)
    writer.add_record('FACT', pack_record('FACT', _trainer_faction_fid, 0, f))

    # CLAS clones, deduped per (source class, skill, cap). NPCs without a
    # resolvable class get a minimal default class carrying the trainer data.
    clone_cache: dict[tuple, int] = {}
    for npc_fid, clas_rec, teaches_idx, max_level in trainers:
        src_fid = get_formid(clas_rec, 'FormID') if clas_rec else 0
        key = (src_fid, teaches_idx, max_level)
        clone_fid = clone_cache.get(key)
        if not clone_fid:
            clone_fid = writer.alloc_formid()
            edid = (f'TES4Trainer{TES5_SKILL_ORDER[teaches_idx]}'
                    f'{max_level}_{src_fid & 0xFFFFFF:06X}')
            writer.add_record('CLAS', convert_CLAS(
                clas_rec or {}, override_fid=clone_fid, override_edid=edid,
                override_teaches=teaches_idx, override_maxtrain=max_level))
            clone_cache[key] = clone_fid
        _trainer_class_by_npc[npc_fid] = clone_fid


def get_trainer_faction_fid() -> int:
    """The synthetic trainer FACT FormID (0 when no trainers exist)."""
    return _trainer_faction_fid


def get_trainer_class_fid(npc_fid: int) -> int:
    """The trainer CLAS clone for a (remapped) NPC FormID, or 0."""
    return _trainer_class_by_npc.get(npc_fid, 0)


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

    # VMAD — converted TES4 actor script (SCRI), attached to the base so every
    # placed reference gets an instance (mirrors TES4 semantics).
    from ..object_scripts import get_object_vmad
    subs += get_object_vmad(get_formid(rec, 'FormID'))

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
        subs += pack_subrecord('SNAM', struct.pack('<IbBBB', fid, rank, 0, 0, 0))

    # SNAM — Vendor faction (if this NPC sells anything)
    services = get_int(rec, 'AIDT.Services')
    vendor_fid = get_vendor_faction_fid(services)
    if vendor_fid:
        subs += pack_subrecord('SNAM', struct.pack('<IbBBB', vendor_fid, 0, 0, 0, 0))

    # SNAM — Trainer faction (gates the generated Training dialogue topic)
    trainer_clas_fid = get_trainer_class_fid(get_formid(rec, 'FormID'))
    if trainer_clas_fid and _trainer_faction_fid:
        subs += pack_subrecord('SNAM', struct.pack('<IbBBB',
                                                   _trainer_faction_fid, 0, 0, 0, 0))

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

    # COCT + CNTO — carried inventory, and the wearables that become the outfit.
    # The TES4 inventory holds both; Skyrim needs them SPLIT (see outfits.py):
    # the outfit is added on top of CNTO at load, so an item in both is carried
    # twice, and only wearables may appear in an outfit at all.
    outfit_fids, carried = split_inventory(_read_items(rec))
    coct = 0
    item_data = b''
    for fid, count in carried:
        item_data += pack_subrecord('CNTO', struct.pack('<Ii', fid, count))
        coct += 1
    # Vendor buying power: TES5 has no barter-gold field — a chest-less vendor
    # trades from its own inventory, so ACBS.BarterGold becomes carried gold.
    barter_gold = get_int(rec, 'ACBS.BarterGold') if vendor_fid else 0
    if barter_gold > 0:
        item_data += pack_subrecord('CNTO', struct.pack('<Ii', GOLD001_FID,
                                                        barter_gold))
        coct += 1
    if coct:
        subs += pack_uint32_subrecord('COCT', coct)
        subs += item_data

    # AIDT — AI data
    subs += pack_subrecord('AIDT', _npc_aidt(rec))

    # PKID — the actor's own AI packages, converted (pack_converter.py) and kept
    # in TES4 ORDER: Skyrim, like Oblivion, runs the first package whose
    # conditions pass, so the order IS the behaviour.  Quest packages are
    # excluded — they reach the actor through a QUST reference alias (ALPC),
    # which is what lets them outrank this standing schedule.
    pc = get_int(rec, 'AIPackageCount')
    pack_fids = [get_formid(rec, f'AIPackage[{i}]') for i in range(pc)]
    for pfid in npc_packages(pack_fids):
        subs += pack_formid_subrecord('PKID', pfid)

    # CNAM — Class. Trainer NPCs get their synthesized class clone (carries
    # the AIDT Teaches/MaxTraining data Skyrim's training menu reads).
    cnam = trainer_clas_fid or get_formid(rec, 'CNAM.Class')
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

    # ZNAM — Combat style. CSTY is skipped, so the TES4 reference would
    # dangle; the vanilla default combat style keeps combat AI functional.
    if get_formid(rec, 'ZNAM.CombatStyle'):
        subs += pack_formid_subrecord('ZNAM', CSTY_DEFAULT)

    # NAM6 / NAM7 — Height / Weight (TES4 has these on RACR records, not NPC;
    # use neutral defaults so the race's own scale applies)
    subs += pack_subrecord('NAM6', struct.pack('<f', 1.0))
    subs += pack_subrecord('NAM7', struct.pack('<f', 1.0))

    # DOFT — Default outfit (requires OTFT companion record)
    # TES4 NPCs equip out of CNTO; TES5 wears exactly what the outfit lists.
    if writer is not None and outfit_fids:
        subs += pack_formid_subrecord(
            'DOFT', _build_outfit(writer, (edid or 'NPC') + '_Outfit',
                                  outfit_fids))

    # DPLT — default package list: the vanilla fallback AI most NPCs carry
    subs += pack_formid_subrecord('DPLT', DPLT_NPC_LIST)

    # Trailing face data: FTST, QNAM, NAM9, NAMA
    subs += build_face_tail_subs(rec, race_edid, gender)

    return pack_record('NPC_', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_CREA(rec: dict, writer=None) -> bytes:
    """CREA → NPC_ (creatures become NPCs in TES5).

    Same subrecord order as NPC_: EDID OBND ACBS SNAM INAM VTCK RNAM
    COCT/CNTO AIDT PKID FULL DATA DNAM ZNAM DOFT DPLT
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # VMAD — converted TES4 creature script (SCRI), attached to the base so
    # every placed reference gets an instance (mirrors TES4 semantics).
    from ..object_scripts import get_object_vmad
    subs += get_object_vmad(get_formid(rec, 'FormID'))

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
        subs += pack_subrecord('SNAM', struct.pack('<IbBBB', fid, rank, 0, 0, 0))

    # Vendor faction (if this creature sells anything)
    crea_services = get_int(rec, 'AIDT.Services')
    crea_vendor_fid = get_vendor_faction_fid(crea_services)
    if crea_vendor_fid:
        subs += pack_subrecord('SNAM', struct.pack('<IbBBB', crea_vendor_fid, 0, 0, 0, 0))

    # Death item
    inam = get_formid(rec, 'INAM.DeathItem')
    if inam:
        subs += pack_formid_subrecord('INAM', inam)

    # VTCK — Voice type (must come before RNAM per TES5 NPC_ definition)
    # Race: generated creature race (creature pipeline project) when
    # available; else the legacy Skyrim-race aliasing fallback.
    from ..creature_races import get_creature_race
    full = get_str(rec, 'FULL')
    _src = None
    crea_race_fid = get_creature_race(get_formid(rec, 'FormID') & 0x00FFFFFF)
    if crea_race_fid is None:
        crea_race_fid, _src, _alt = resolve_creature_race(edid, full)
    tes4_flags = get_int(rec, 'ACBS.Flags')
    gender = 'Female' if (tes4_flags & 1) else 'Male'
    tes4_race_fid = get_formid(rec, 'RNAM.Race')
    race_edid = TES4_RACE_FID_TO_EDID.get(tes4_race_fid & 0x00FFFFFF, '')
    if not race_edid:
        race_edid = _src if _src else 'Imperial'
    voice = (VOICE_TYPE_MAP.get((race_edid, gender))
             or VOICE_TYPE_MAP.get(('Imperial', gender), 0))
    if voice:
        subs += pack_formid_subrecord('VTCK', voice)

    # RNAM — Race (after VTCK per TES5 NPC_ definition)
    subs += pack_formid_subrecord('RNAM', crea_race_fid)

    # Items — carried inventory and outfit are disjoint (see convert_NPC_).
    # Creature inventories are mostly loot leveled-lists, which belong in CNTO;
    # only the armed/armored ones (skeletons, dremora) yield an outfit at all.
    outfit_fids, carried = split_inventory(_read_items(rec))
    coct = 0
    item_data = b''
    for fid, count in carried:
        item_data += pack_subrecord('CNTO', struct.pack('<Ii', fid, count))
        coct += 1
    # Vendor buying power (see convert_NPC_): barter gold -> carried gold.
    crea_barter_gold = get_int(rec, 'ACBS.BarterGold') if crea_vendor_fid else 0
    if crea_barter_gold > 0:
        item_data += pack_subrecord('CNTO', struct.pack('<Ii', GOLD001_FID,
                                                        crea_barter_gold))
        coct += 1
    if coct:
        subs += pack_uint32_subrecord('COCT', coct)
        subs += item_data

    # AIDT — 20 bytes (TES5 format, shared with NPC_)
    subs += pack_subrecord('AIDT', _npc_aidt(rec))

    # PKID — TES4 PACK records are skipped (SKIP_TYPES), so a raw pass-through
    # gave creatures NO working packages → the AI layer made no decisions and
    # the engine never sent the graph movement/attack events (the stuck-in-idle
    # root cause). Every vanilla creature carries exactly ONE package,
    # DefaultMasterPackageCreature — give converted creatures the same hookup.
    subs += pack_formid_subrecord('PKID', PKID_CREATURE_MASTER)

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

    # ZNAM — combat style (CSTY is skipped; use vanilla styles).
    # TES4 DATA.Type: 0=Creature, 1=Daedra, 2=Undead, 3=Humanoid, 4=Horse.
    crea_type = get_int(rec, 'DATA.Type')
    subs += pack_formid_subrecord(
        'ZNAM', CSTY_ANIMAL if crea_type in (0, 4) else CSTY_DEFAULT)

    # DOFT — Default outfit
    if writer is not None and outfit_fids:
        subs += pack_formid_subrecord(
            'DOFT', _build_outfit(writer, (edid or 'CREA') + '_Outfit',
                                  outfit_fids))

    # DPLT — default package list, like every vanilla creature (EncWolf etc.)
    subs += pack_formid_subrecord('DPLT', DPLT_CREATURE_LIST)

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


def convert_CLAS(rec: dict, *, override_fid: int = 0, override_edid: str = '',
                 override_teaches: int = -1, override_maxtrain: int = -1) -> bytes:
    """CLAS — TES5 DATA is 36 bytes with Skyblivion skill-weight algorithm.

    The override_* parameters support Phase 0c trainer-class synthesis: Skyrim
    reads a trainer's skill/cap from the NPC's CLASS, but Oblivion stores them
    per-NPC in AIDT, so trainer NPCs get a clone of their own class with just
    Teaches/MaxTraining replaced (override_teaches is a TES5_SKILL_ORDER index).
    """
    subs = b''
    edid = override_edid or get_str(rec, 'EditorID')
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
    if override_teaches >= 0:
        teaches = override_teaches
    if override_maxtrain >= 0:
        max_train = override_maxtrain
    max_train = min(255, max(0, max_train))

    # TES5 CLAS DATA: Unknown(4) + Teaches(S8,1) + MaxTraining(U8,1) + SkillWeights(18) + Bleedout(float,4) + VoicePoints(U32,4) + AttrWeights(4×U8) = 36 bytes
    data = struct.pack('<I', 0xFFFC0000)  # Flags (vanilla default)
    data += struct.pack('<bB', teaches, max_train)
    data += skill_weights                  # 18 bytes
    data += struct.pack('<f', 0.1)        # Bleedout default (vanilla=0.1)
    data += struct.pack('<I', 0)          # Voice points (vanilla default)
    data += struct.pack('<4B', 1, 1, 1, 0)  # Attr weights: Health, Magicka, Stamina, Unknown
    subs += pack_subrecord('DATA', data)

    fid = override_fid or get_formid(rec, 'FormID')
    flags = 0 if override_fid else get_int(rec, 'RecordFlags')
    return pack_record('CLAS', fid, flags, subs)


# TES4 globals whose names collide with Skyrim engine globals. Script
# references to these are canonicalized to the vanilla forms by
# script_convert (_GLOBAL_CANONICAL), so emitting our own copies would only
# create duplicate EditorIDs.
_ENGINE_GLOBALS = {'gamehour', 'gamedayspassed', 'gameday', 'gamemonth',
                   'gameyear', 'timescale'}


def convert_GLOB(rec: dict) -> bytes:
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid and edid.lower() in _ENGINE_GLOBALS:
        return b''
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

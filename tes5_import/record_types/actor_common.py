"""Actor data shared by the NPC_ and CREA converters.

Both converters build the same TES5 actor shape from different TES4 records, so
everything either of them needs twice lives here: inventory and outfits, the
ACBS flag tables, AI data and faction reactions, the vendor/trainer service
subsystem, and voice-type resolution. FACT and CLAS are here because they are
the records those systems emit.

See: docs/commentary/tes5_import_actors.md
"""

import re
import struct

from ..base.constants import (DEFAULT_RACE, RACE_MAP, TES4_SKILL_TO_TES5,
                         TES5_SKILL_ORDER)
from ..base.equivalents import (ATTRIBUTE_SKILL_MAP, TES4_RACE_FID_TO_EDID,
                                VOICE_TYPE_MAP)
from .actors_falloutnv import aidt_tiers
from .common import (
    get_float,
    get_formid,
    get_int,
    get_str,
    pack_formid_subrecord,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
)
from .race_falloutnv import fallout_race_edid
from .vendor_stock_morrowind import claim_stock, owned_stock, plvd_in_cell
from .world_falloutnv import is_fallout_source

# ---------------------------------------------------------------------------
#   Inventory and outfits
# ---------------------------------------------------------------------------


def read_items(rec: dict) -> list:
    """Actor's TES4 CNTO inventory as [(fid, count)], in export order.

    TES4 merchant inventories use NEGATIVE counts for restocking stock;
    Skyrim restocks via respawn and treats count < 1 as adding nothing
    (a CK warning per entry), so counts are normalized to at least 1.
    """
    items = []
    for i in range(get_int(rec, 'ItemCount')):
        fid = get_formid(rec, f'Item[{i}].FormID')
        if fid:
            count = abs(get_int(rec, f'Item[{i}].Count', 1)) or 1
            items.append((fid, count))
    return items

#: NAM8 wbSoundLevelEnum Normal; vanilla writes it on 5116/5118 NPC_ records.
SOUND_LEVEL_NORMAL = 1

#: NAM5 is wbUnknown; every real Skyrim.esm NPC_ writes exactly these 2 bytes.
NAM5_UNKNOWN = b'\xff\x00'
def build_outfit(writer, edid: str, outfit_fids: list,
                  source_fid: int = 0) -> int:
    """Emit the OTFT companion record for an actor and return its FormID.

    INAM packs the item FormIDs as consecutive 4-byte LE uint32.
    """
    otft_fid = writer.derive_formid('OTFT', source_fid or edid)
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_subrecord(
        'INAM', b''.join(struct.pack('<I', fid) for fid in outfit_fids))
    writer.add_record('OTFT', pack_record('OTFT', otft_fid, 0, subs))
    return otft_fid


#: TES4 CREA ACBS bits; NOT the TES4 NPC_ or TES5 bits, which collide by number.
T4C_BIPED           = 0x000001
T4C_ESSENTIAL       = 0x000002
T4C_RESPAWN         = 0x000008
T4C_SWIMS           = 0x000010
T4C_FLIES           = 0x000020
T4C_WALKS           = 0x000040
T4C_PC_LEVEL_OFFSET = 0x000080
T4C_NO_LOW_LEVEL    = 0x000200
T4C_NO_BLOOD_SPRAY  = 0x000800
T4C_NO_BLOOD_DECAL  = 0x001000

#: TES5 NPC_ ACBS flags (xEdit + UESP Skyrim Mod:Mod File Format/NPC).
T5_FEMALE        = 0x00000001
T5_ESSENTIAL     = 0x00000002
T5_RESPAWN       = 0x00000008
T5_AUTOCALC      = 0x00000010
T5_PC_LEVEL_MULT = 0x00000080
T5_PROTECTED     = 0x00000800
T5_SUMMONABLE    = 0x00004000
T5_DOESNT_BLEED  = 0x00010000
T5_SIMPLE_ACTOR  = 0x00100000

#: fid_low24 -> that faction's Relation disposition toward PlayerFaction.
_FACTION_PLAYER_DISP = {}

#: TES4 PlayerFaction; the same FormID in Oblivion.esm and Nehrim.esm.
_PLAYER_FACTION_FID = 0x0001DBCD


#: Lowercased EditorIDs of factions this plugin's scripts treat as crime factions.
TES4_CRIME_FACTIONS: set = set()

#: Get/SetPCFaction{Murder,Attack,Steal} and its first argument.
_CRIME_FN_RE = re.compile(
    r'\b(?:get|set)pcfaction(?:murder|attack|steal)\s+([A-Za-z0-9_]+)', re.I)


def _load_crime_factions(by_type: dict) -> None:
    """Collect faction EditorIDs used by the plugin's crime-flag script calls.

    SCTX arrives with \r\n still escaped; the regex only needs the function
    name and its first argument, so nothing has to be unescaped first.

    See: docs/commentary/tes5_import_actors.md#crime-factions-derived
    """
    TES4_CRIME_FACTIONS.clear()
    for sig in ('SCPT', 'INFO', 'QUST'):
        for rec in by_type.get(sig, []):
            for key, val in rec.items():
                if not isinstance(val, str) or 'pcfaction' not in val.lower():
                    continue
                for m in _CRIME_FN_RE.finditer(val):
                    TES4_CRIME_FACTIONS.add(m.group(1).lower())


def load_faction_player_reactions(by_type: dict) -> None:
    """Index each FACT's disposition modifier toward the player faction.

    Also indexes the prey factions and, via `_load_crime_factions`, the
    factions this plugin's scripts treat as crime factions.

    See: docs/commentary/tes5_import_actors.md#faction-player-disposition
    """
    _FACTION_PLAYER_DISP.clear()
    _PREY_FACTIONS.clear()
    _load_crime_factions(by_type)
    for rec in by_type.get('FACT', []):
        fid = get_formid(rec, 'FormID') & 0xFFFFFF
        edid = (get_str(rec, 'EditorID') or '').lower()
        if 'prey' in edid:
            _PREY_FACTIONS.add(fid)
        n = get_int(rec, 'RelationCount')
        for i in range(n):
            other = get_formid(rec, f'Relation[{i}].Faction') & 0xFFFFFF
            if other == (_PLAYER_FACTION_FID & 0xFFFFFF):
                _FACTION_PLAYER_DISP[fid] = get_int(
                    rec, f'Relation[{i}].Disposition')
                break


#: fid_low24 of every faction whose EditorID marks its members as prey.
_PREY_FACTIONS = set()

#: Player's mid-range starting Personality across races/classes.
_PLAYER_PERSONALITY = 40

#: AIDT Mood: 0 Neutral. TES4 has no equivalent field.
_MOOD_NEUTRAL = 0

#: TES4 confidence floor -> TES5 wbConfidenceEnum tier, highest first.
_CONFIDENCE_TIERS = ((100, 4), (70, 3), (40, 2), (15, 1))

#: Attack margin (aggression-5)-disposition an actor needs to earn tier 2.
_ONSIGHT_MARGIN = 10


def _is_prey(rec: dict) -> bool:
    """True when this actor belongs to a prey faction.

    Prey is the vanilla marker for "harmless": Oblivion's 43 Prey members are
    all horses, deer and sheep, and several carry aggression 100 — the same
    value as the nastiest predators — so aggression alone cannot exclude them.
    UESP Oblivion:Animals confirms deer "are not aggressive" despite that.
    """
    for i in range(get_int(rec, 'FactionCount')):
        if (get_formid(rec, f'Faction[{i}].FormID') & 0xFFFFFF) in _PREY_FACTIONS:
            return True
    return False


def _player_disposition(rec: dict, pers: int) -> int:
    """Estimate this actor's TES4 starting disposition toward the player.

    Personality is the base; every faction the actor belongs to contributes its
    own reaction toward PlayerFaction.  TES4 sums the faction modifiers, so a
    creature in two hostile factions is more hostile than one in a single
    faction — matching the in-game behaviour the formula describes.
    """
    disp = pers
    for i in range(get_int(rec, 'FactionCount')):
        fid = get_formid(rec, f'Faction[{i}].FormID') & 0xFFFFFF
        disp += _FACTION_PLAYER_DISP.get(fid, 0)
    disp += (_PLAYER_PERSONALITY - pers) // 4
    return disp


def _aggression_tier(rec: dict, aggr: int, pers: int) -> int:
    """TES4's 0-100 aggression scalar as a TES5 tier (wbAggressionEnum 0-3).

    Models TES4's per-target rule -- attack when disposition < aggression-5 --
    rather than bucketing aggression alone, so an actor hostile only via its
    faction graph lands on tier 1 and lets XNAM do the targeting.

    See: docs/commentary/tes5_import_actors.md#aggression-tiers
    """
    if aggr <= 5:
        return 0
    if aggr >= 106:
        return 3
    disp = _player_disposition(rec, pers)
    if not _is_prey(rec) and (aggr - 5) - disp >= _ONSIGHT_MARGIN:
        return 2
    return 1


def _confidence_tier(conf: int) -> int:
    """TES4 confidence 0-100 as a TES5 tier; only tier 4 never flees.

    See: docs/commentary/tes5_import_actors.md#confidence-tiers
    """
    for threshold, tier in _CONFIDENCE_TIERS:
        if conf >= threshold:
            return tier
    return 0


def build_aidt(rec: dict) -> bytes:
    """Build TES5 AIDT subrecord (20 bytes).

    Layout: Aggression U8, Confidence U8, Energy U8, Morality U8, Mood U8,
    Assistance U8, AggroRadiusBehavior U8, unused U8, then Warn / Warn+Attack /
    Attack U32. Morality and Assistance derive from TES4 Responsibility.

    FO3/FNV stores Aggression and Confidence in the TES5 enums already, so it
    bypasses the scalar mappings entirely.

    See: docs/commentary/tes5_import_actors.md#aggression-tiers
    """
    energy = get_int(rec, 'AIDT.EnergyLevel', 50)
    resp = get_int(rec, 'AIDT.Responsibility')
    if is_fallout_source():
        tes5_aggr, tes5_conf = aidt_tiers(rec)
    else:
        pers = get_int(rec, 'DATA.Personality', 50)
        tes5_aggr = _aggression_tier(rec, get_int(rec, 'AIDT.Aggression'), pers)
        tes5_conf = _confidence_tier(get_int(rec, 'AIDT.Confidence'))
    tes5_moral = 3 if resp >= 80 else (2 if resp >= 50 else (1 if resp >= 30 else 0))
    tes5_assist = 1 if resp >= 30 else 0

    return struct.pack('<BBBBBB BB III',
                       tes5_aggr, tes5_conf, energy,
                       tes5_moral, _MOOD_NEUTRAL, tes5_assist,
                       0, 0,
                       0, 0, 0)
# ---------------------------------------------------------------------------
#   Vendor Faction System
# ---------------------------------------------------------------------------

#: Skyrim.esm Gold001 (index 0 in the output load order).
GOLD001_FID = 0x0000000F

#: TES4 AIDT.Services bit -> Skyrim VendorItem KYWDs; sync with VENDOR_KYWD.
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

#: VendorNoSale: keeps quest items out of the barter menu.
_KYWD_VENDOR_NO_SALE = 0x000FF9FB

#: FACT DATA 'Vendor'; vanilla vendor factions set this bit and nothing else.
_FACT_VENDOR = 0x4000

#: service_bitmask -> shared vendor FACT FormID, for chestless merchants.
_vendor_faction_cache: dict[int, int] = {}

#: The one faction every merchant joins, so Barter needs a single GetInFaction.
_merchant_marker_faction_fid = 0

#: EditorID of the merchant marker FACT; a dependent adopts its master's by it.
_MERCHANT_MARKER_EDID = 'TES4MerchantFaction'

#: (remapped) actor FormID -> its own merchant FACT (VENC chest or In Cell stock).
_merchant_faction_by_npc: dict[int, int] = {}

#: "Belongs to this converted plugin" marker FACT; root masters only.
_origin_faction_fid = 0


#: The converted masters' origin FACTs, which a dependent's actors join.
_master_origin_fids: list = []

#: EditorID of the plugin-origin marker FACT, the same in every root master.
_ORIGIN_EDID = 'TES4PluginOriginFaction'


def get_origin_faction_fid() -> int:
    """The plugin-origin marker FACT, or 0 when this file isn't gated."""
    return _origin_faction_fid


def is_support_root() -> bool:
    """Whether THIS import created the support records, as no master supplies them.

    See: docs/commentary/tes5_import_pipeline.md#phase-0-dependent-skips-support-records
    """
    return bool(_origin_faction_fid)


def origin_memberships() -> list:
    """Every plugin-origin FACT this file's actors join: its own, else its masters'.

    See: docs/commentary/tes5_import_actors.md#origin-faction
    """
    return ([_origin_faction_fid] if _origin_faction_fid
            else list(_master_origin_fids))


def create_origin_faction(writer) -> int:
    """Create the plugin-origin marker faction. Root masters only.

    See: docs/commentary/tes5_import_actors.md#origin-faction
    """
    global _origin_faction_fid
    _origin_faction_fid = writer.derive_formid('FACT', _ORIGIN_EDID)
    subs = pack_string_subrecord('EDID', _ORIGIN_EDID)
    subs += pack_subrecord('DATA', struct.pack('<I', 0))
    writer.add_record('FACT', pack_record('FACT', _origin_faction_fid, 0, subs))
    return _origin_faction_fid


def reset_origin_faction(master_index=None) -> None:
    """Clear origin-faction state, then adopt the converted masters' FACTs.

    See: docs/commentary/tes5_import_actors.md#origin-faction
    """
    global _origin_faction_fid
    _origin_faction_fid = 0
    _master_origin_fids[:] = (
        master_index.find_all_by_edid(b'FACT', _ORIGIN_EDID)
        if master_index is not None else [])


def _keywords_for_services(services: int) -> list[int]:
    """Return unique sorted Skyrim KYWD FormIDs for a TES4 services bitmask."""
    return sorted({k for bit, kwds in _TES4_SERVICE_BIT_TO_SKYRIM_KEYWORDS.items()
                   if services & (1 << bit) for k in kwds})


def _vendor_bits(services: int) -> int:
    """Services bitmask with the non-vendor (training/recharge/repair) bits cleared."""
    return services & ~((1 << 14) | (1 << 16) | (1 << 17))


def _build_merchant_chest_map(by_type: dict) -> dict[int, int]:
    """(remapped) base-actor FormID → (remapped) merchant-chest REFR FormID.

    In Oblivion a merchant's sale stock lives in a CONT placed in the world and
    linked to the NPC through the placed reference's XMRC (Merchant Container),
    not in the NPC's carried inventory. Skyrim expresses the same thing with a
    VENC on the vendor faction, so we resolve ACHR.NAME (base actor) → the chest
    REFR here and hand it to the faction builder.
    """
    chest_by_npc: dict[int, int] = {}
    for rec in by_type.get('ACHR', []):
        chest = get_formid(rec, 'XMRC.MerchantContainer')
        if not chest:
            continue
        base = get_formid(rec, 'NAME')
        if base:
            chest_by_npc.setdefault(base, chest)
    return chest_by_npc


def _vendor_flst_subs(svc_mask: int) -> bytes:
    """FLST subrecords for a service bitmask's VendorItem keyword filter.

    VendorNoSale is always appended, which prevents selling quest items.
    """
    kwds = _keywords_for_services(svc_mask) + [_KYWD_VENDOR_NO_SALE]
    subs = pack_string_subrecord('EDID', f'TES4VendorList_{svc_mask:06X}')
    for kw_fid in kwds:
        subs += pack_formid_subrecord('LNAM', kw_fid)
    return subs


#: VENV open 0-24h, radius 0: vanilla's anywhere-vendors (ServicesDBBabette).
_VENV_ALWAYS_OPEN = struct.pack('<HHI4x', 0, 24, 0)

#: PLVD type 12 Near Self; a vendor faction without PLVD is never selected.
_PLVD_NEAR_SELF = struct.pack('<iIi', 12, 0, 0)


def _write_vendor_faction(writer, edid: str, flst_fid: int, venc_fid: int = 0,
                          plvd: bytes = _PLVD_NEAR_SELF) -> int:
    """Create a vendor FACT and return its FormID.

    `edid` must be unique per faction and derived from authored data. Fields
    follow vanilla ServicesDBBabette (trades any hour; anywhere unless `plvd`
    names a location); subrecord order is VEND, VENC, VENV, PLVD.

    See: docs/commentary/tes5_import_actors.md#vendor-faction-needs-plvd
    """
    fact_fid = writer.derive_formid('VENDOR_FACT', edid)
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_string_subrecord('FULL', 'Merchant')
    subs += pack_subrecord('DATA', struct.pack('<I', _FACT_VENDOR))
    subs += pack_subrecord('CRVA', b'\x01\x01' + b'\x00' * 18)
    subs += pack_formid_subrecord('VEND', flst_fid)
    if venc_fid:
        subs += pack_formid_subrecord('VENC', venc_fid)
    subs += pack_subrecord('VENV', _VENV_ALWAYS_OPEN)
    subs += pack_subrecord('PLVD', plvd)
    writer.add_record('FACT', pack_record('FACT', fact_fid, 0, subs))
    return fact_fid


def _adopted(master_index, signature: bytes, edid: str) -> int:
    """The masters' record with this EditorID; 0 with no index or no match."""
    return (0 if master_index is None
            else master_index.find_by_edid(signature, edid))


def create_vendor_factions(by_type: dict, writer, master_index=None,
                           stock: dict = None) -> None:
    """Phase 0c: Pre-scan NPC_/CREA for services and create vendor FACTs + FLSTs.

    A shared per-bitmask faction serves most merchants; a dedicated one serves
    each merchant whose placed ref links a chest, or who owns Morrowind `stock`
    (see `owned_stock`). All share one FLST per bitmask. With `master_index`,
    a keyword list, shared faction or marker faction a master already defines
    is adopted by EditorID and only the missing ones are created.

    See: docs/commentary/tes5_import_actors.md#vendor-factions
    """
    global _merchant_marker_faction_fid
    _vendor_faction_cache.clear()
    _merchant_faction_by_npc.clear()
    _merchant_marker_faction_fid = 0

    vendor_actors, unique_services = _collect_vendor_actors(by_type)
    if not unique_services:
        return

    flst_by_svc = _vendor_lists(writer, unique_services, master_index)
    for svc_mask, flst_fid in flst_by_svc.items():
        edid = f'TES4VendorFaction_{svc_mask:06X}'
        _vendor_faction_cache[svc_mask] = (
            _adopted(master_index, b'FACT', edid)
            or _write_vendor_faction(writer, edid, flst_fid))

    n_dedicated = _write_merchant_factions(
        writer, vendor_actors, flst_by_svc, _build_merchant_chest_map(by_type),
        stock or {})
    _merchant_marker_faction_fid = (
        _adopted(master_index, b'FACT', _MERCHANT_MARKER_EDID)
        or _write_merchant_marker(writer))

    print(f"  Vendor factions: {len(flst_by_svc)} shared service combos, "
          f"{n_dedicated} chest/stock merchants, merchant marker "
          f"{_merchant_marker_faction_fid:08X}")


def _collect_vendor_actors(by_type: dict) -> tuple:
    """(actor fid, vendor_bits) per vendor, and the set of distinct bitmasks.

    Training-only actors (bit 14 with no vendor bits) are handled by CLAS.
    """
    vendor_actors: list[tuple[int, int]] = []
    unique_services: set[int] = set()
    for sig in ('NPC_', 'CREA'):
        for rec in by_type.get(sig, []):
            bits = _vendor_bits(get_int(rec, 'AIDT.Services'))
            if not bits:
                continue
            unique_services.add(bits)
            vendor_actors.append((get_formid(rec, 'FormID'), bits))
    return vendor_actors, unique_services


def _vendor_lists(writer, unique_services: set, master_index) -> dict:
    """One keyword FLST per service bitmask, adopted or emitted; both faction kinds share it."""
    flst_by_svc: dict[int, int] = {}
    for svc_mask in sorted(unique_services):
        if not _keywords_for_services(svc_mask):
            continue
        flst_fid = _adopted(master_index, b'FLST', f'TES4VendorList_{svc_mask:06X}')
        if not flst_fid:
            flst_fid = writer.derive_formid('VENDOR_FLST', svc_mask)
            writer.add_record('FLST', pack_record('FLST', flst_fid, 0,
                                                  _vendor_flst_subs(svc_mask)))
        flst_by_svc[svc_mask] = flst_fid
    return flst_by_svc


def _write_merchant_factions(writer, vendor_actors: list, flst_by_svc: dict,
                             chest_by_npc: dict, stock: dict) -> int:
    """Emit a dedicated faction per merchant with a chest or owned stock; return the count.

    A chest merchant's faction carries VENC; a Morrowind merchant owning stock
    gets PLVD In Cell (its placement cell) and the stock is re-owned to it.

    See: docs/commentary/tes5_import_actors.md#morrowind-merchant-stock
    """
    n_dedicated = 0
    for actor_fid, bits in vendor_actors:
        chest = chest_by_npc.get(actor_fid)
        owned = stock.get(actor_fid)
        flst_fid = flst_by_svc.get(bits)
        if not (chest or owned) or not flst_fid:
            continue
        edid = f'TES4Merchant_{actor_fid & 0xFFFFFF:06X}'
        if chest:
            fact = _write_vendor_faction(writer, edid, flst_fid, chest)
        else:
            fact = _write_vendor_faction(writer, edid, flst_fid,
                                         plvd=plvd_in_cell(owned[0]))
            claim_stock(owned[1], fact)
        _merchant_faction_by_npc[actor_fid] = fact
        n_dedicated += 1
    return n_dedicated


def _write_merchant_marker(writer) -> int:
    """Emit the membership-only FACT the Barter topic gates on; return its FormID.

    Deliberately NOT a vendor faction -- no Vendor flag, VEND or VENV -- so it
    can never compete with the real one the engine resolves for the menu.

    See: docs/commentary/tes5_import_actors.md#barter-gate-ctda-limit
    """
    fid = writer.derive_formid('FACT', _MERCHANT_MARKER_EDID)
    marker = pack_string_subrecord('EDID', _MERCHANT_MARKER_EDID)
    marker += pack_string_subrecord('FULL', 'Merchant')
    marker += pack_subrecord('DATA', struct.pack('<I', 0))
    marker += pack_subrecord('CRVA', b'\x01\x01' + b'\x00' * 18)
    writer.add_record('FACT', pack_record('FACT', fid, 0, marker))
    return fid


def get_vendor_faction_fids_for_actor(actor_fid: int, services: int) -> list[int]:
    """Vendor FACT FormIDs this actor should belong to (SNAM memberships).

    A chest-backed merchant gets ONLY its dedicated faction (VENC → its own
    merchant chest), since the engine takes the first vendor faction that
    qualifies; everyone else gets the shared per-service faction. Every
    merchant also joins the marker faction the Barter topic gates on.

    See: docs/commentary/tes5_import_actors.md#vendor-faction-needs-plvd
    """
    fids = []
    dedicated = _merchant_faction_by_npc.get(actor_fid)
    shared = _vendor_faction_cache.get(_vendor_bits(services), 0)
    if dedicated or shared:
        fids.append(dedicated or shared)
    if fids and _merchant_marker_faction_fid:
        fids.append(_merchant_marker_faction_fid)
    return fids


def get_merchant_faction_fid() -> int:
    """The single FACT the Barter topic gates on (0 if no merchants exist).

    See: docs/commentary/tes5_import_actors.md#barter-gate-ctda-limit
    """
    return _merchant_marker_faction_fid


# ---------------------------------------------------------------------------
#   Trainers
# ---------------------------------------------------------------------------

_trainer_faction_fid = 0

#: EditorID of the trainer marker FACT; a dependent adopts its master's by it.
_TRAINER_FACTION_EDID = 'TES4JobTrainerFaction'

#: remapped NPC fid -> its trainer CLAS clone fid.
_trainer_class_by_npc: dict[int, int] = {}


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


def _write_trainer_faction(writer) -> int:
    """Emit the trainer marker FACT (flags 0, like vanilla JobTrainerFaction)."""
    fid = writer.derive_formid('FACT', _TRAINER_FACTION_EDID)
    f = pack_string_subrecord('EDID', _TRAINER_FACTION_EDID)
    f += pack_string_subrecord('FULL', 'Trainer')
    f += pack_subrecord('DATA', struct.pack('<I', 0))
    f += pack_subrecord('CRVA', b'\x01\x01' + b'\x00' * 18)
    writer.add_record('FACT', pack_record('FACT', fid, 0, f))
    return fid


def create_trainer_records(by_type: dict, writer, master_index=None,
                           master_classes=()) -> None:
    """Phase 0c2: trainer FACT + per-trainer CLAS clones for NPC_ trainers.

    One faction marks every trainer and the Training topic gates on it; with
    `master_index` a master's is adopted by EditorID. CLAS clones are deduped
    per (source class, skill, cap), the class found in this plugin or
    `master_classes`; an NPC with no resolvable class gets a minimal default.

    See: docs/commentary/tes5_import_actors.md#trainers
    """
    global _trainer_faction_fid
    _trainer_faction_fid = 0
    _trainer_class_by_npc.clear()

    clas_by_fid = {get_formid(r, 'FormID'): r
                   for r in [*master_classes, *by_type.get('CLAS', [])]
                   if get_formid(r, 'FormID')}

    trainers = []
    for rec in by_type.get('NPC_', []):
        params = _npc_trainer_params(rec)
        if not params:
            continue
        clas_rec = clas_by_fid.get(get_formid(rec, 'CNAM.Class'))
        trainers.append((get_formid(rec, 'FormID'), clas_rec, *params))

    if not trainers:
        return
    print(f"  Creating trainer records for {len(trainers)} trainer NPCs...")

    _trainer_faction_fid = (
        _adopted(master_index, b'FACT', _TRAINER_FACTION_EDID)
        or _write_trainer_faction(writer))

    clone_cache: dict[tuple, int] = {}
    for npc_fid, clas_rec, teaches_idx, max_level in trainers:
        src_fid = get_formid(clas_rec, 'FormID') if clas_rec else 0
        key = (src_fid, teaches_idx, max_level)
        clone_fid = clone_cache.get(key)
        if not clone_fid:
            clone_fid = writer.derive_formid('TRAINER_CLAS', key)
            edid = (f'TES4Trainer{TES5_SKILL_ORDER[teaches_idx]}'
                    f'{max_level}_{src_fid & 0xFFFFFF:06X}')
            writer.add_record('CLAS', convert_CLAS(
                clas_rec or {}, override_fid=clone_fid, override_edid=edid,
                override_teaches=teaches_idx, override_maxtrain=max_level))
            clone_cache[key] = clone_fid
        _trainer_class_by_npc[npc_fid] = clone_fid


def create_service_records(by_type: dict, writer, ctx, export_dir: str) -> None:
    """Phase 0c: the vendor factions, then the trainer faction and CLAS clones.

    A plugin that creates its own support records makes all of them; a
    dependent adopts what its masters define by EditorID, creates the rest, and
    finds its trainers' classes in its masters' export. A Morrowind source's
    merchants also get the stock they own.

    See: docs/commentary/tes5_import_actors.md#vendor-factions-in-a-dependent
    """
    master_export = getattr(ctx, 'master_export', None) or {}
    index = None if is_support_root() else getattr(ctx, 'master_index', None)
    classes = ([] if index is None else
               [r for r in master_export.values() if r.get('Signature') == 'CLAS'])
    create_vendor_factions(by_type, writer, index,
                           owned_stock(by_type, master_export, export_dir))
    create_trainer_records(by_type, writer, index, classes)


def get_trainer_faction_fid() -> int:
    """The synthetic trainer FACT FormID (0 when no trainers exist)."""
    return _trainer_faction_fid


def get_trainer_class_fid(npc_fid: int) -> int:
    """The trainer CLAS clone for a (remapped) NPC FormID, or 0."""
    return _trainer_class_by_npc.get(npc_fid, 0)
#: XNAM Group Combat Reaction (xEdit wbFactionRelations).
_XNAM_NEUTRAL, _XNAM_ENEMY, _XNAM_ALLY, _XNAM_FRIEND = 0, 1, 2, 3

#: TES4 FACT DATA bits (U8); TES5 numbers its own differently.
_T4F_HIDDEN_FROM_PLAYER, _T4F_SPECIAL_COMBAT = 0x01, 0x04

#: TES5 FACT DATA bits (U32).
_T5F_HIDDEN_FROM_NPC = 0x0001
_T5F_SPECIAL_COMBAT = 0x0002
_T5F_TRACK_CRIME = 0x0040
_T5F_CAN_BE_OWNER = 0x8000

#: (remapped) actor FormID -> VTYP, from build_npc_to_vtyp_map in Phase 0.
_npc_voice_map: dict = {}


def set_npc_voice_map(m: dict):
    """Register the NPC->VTYP map (called by import_main before conversion)."""
    global _npc_voice_map
    _npc_voice_map = m or {}


def npc_vtyp(actor_fid: int) -> int:
    """The dialogue pass's VNAM-resolved VTYP for this actor, or 0."""
    return _npc_voice_map.get(actor_fid, 0)


def _actor_race_edid(rec: dict) -> str:
    """Oblivion race EditorID for an actor, FNV races included."""
    fid = get_formid(rec, 'RNAM.Race')
    return (fallout_race_edid(fid)
            or TES4_RACE_FID_TO_EDID.get(fid & 0x00FFFFFF, 'Imperial'))


def resolve_npc_race(rec: dict):
    """Resolve TES4 race FormID to (race_edid, skyrim_race_fid, gender_str)."""
    race_edid = _actor_race_edid(rec)
    skyrim_race = RACE_MAP.get(race_edid, DEFAULT_RACE)
    tes4_flags = get_int(rec, 'ACBS.Flags')
    gender = 'Female' if (tes4_flags & 1) else 'Male'
    return race_edid, skyrim_race, gender


def resolve_actor_voice(rec: dict, gender: str) -> int:
    """VTYP a converted actor gets: dialogue-pass voice first, then race/gender.

    Same chain as convert_NPC_/convert_CREA VTCK. Also used to fill the male/
    female VTCK slots on generated creature RACE records — vanilla creature
    races always fill both (DogRace: CrDogVoice x2), and a null slot makes the
    CK log "Could not find male/female voice type" per race.
    """
    race_edid = _actor_race_edid(rec)
    return (_npc_voice_map.get(get_formid(rec, 'FormID'))
            or VOICE_TYPE_MAP.get((race_edid, gender))
            or VOICE_TYPE_MAP.get(('Imperial', gender), 0))


def convert_FACT(rec: dict) -> bytes:
    """FACT -> FACT: relations as XNAM, flags remapped, CNAM as CRVA.

    Crime amounts follow the vanilla census of the 14 real Skyrim crime
    factions; a faction only gets them when the scripts test it.

    See: docs/commentary/tes5_import_actors.md#faction-relations
    See: docs/commentary/tes5_import_actors.md#crva-layout
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    self_fid = get_formid(rec, 'FormID')
    rc = get_int(rec, 'RelationCount')
    for i in range(rc):
        fid = get_formid(rec, f'Relation[{i}].Faction')
        disp = get_int(rec, f'Relation[{i}].Disposition')
        if disp <= -50:
            reaction = _XNAM_ENEMY
        elif disp >= 50:
            reaction = _XNAM_ALLY if fid == self_fid else _XNAM_FRIEND
        else:
            reaction = _XNAM_NEUTRAL
        subs += pack_subrecord('XNAM', struct.pack('<IiI', fid, 0, reaction))

    tes4_flags = get_int(rec, 'DATA.Flags')
    tes5_flags = _T5F_CAN_BE_OWNER
    if tes4_flags & _T4F_HIDDEN_FROM_PLAYER:
        tes5_flags |= _T5F_HIDDEN_FROM_NPC
    if tes4_flags & _T4F_SPECIAL_COMBAT:
        tes5_flags |= _T5F_SPECIAL_COMBAT
    if (edid or '').lower() in TES4_CRIME_FACTIONS:
        tes5_flags |= _T5F_TRACK_CRIME
    subs += pack_subrecord('DATA', struct.pack('<I', tes5_flags))

    crime_gold = get_float(rec, 'CNAM.CrimeGold', 1.0)
    if tes5_flags & _T5F_TRACK_CRIME:
        crva = struct.pack('<BBHHHHHfHH', 1, 1, 1000, 40, 5, 25, 0,
                           crime_gold, 100, 0)
    else:
        crva = struct.pack('<BBHHHHHfHH', 1, 1, 0, 0, 0, 0, 0,
                           crime_gold, 0, 0)
    subs += pack_subrecord('CRVA', crva)

    return pack_record('FACT', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


# ---------------------------------------------------------------------------
#   Classes
# ---------------------------------------------------------------------------


#: TES4 skill actor values start at 12; DATA.Teaches is a 0-based index.
_TES4_SKILL_AV_BASE = 12

#: CLAS DATA flags and bleedout, both at their vanilla defaults.
_CLAS_FLAGS_DEFAULT = 0xFFFC0000
_CLAS_BLEEDOUT_DEFAULT = 0.1

#: TES4 class specialization -> the six TES5 skills it favours.
_SPEC_SKILLS = {
    0: ('OneHanded', 'TwoHanded', 'Block', 'Smithing', 'HeavyArmor', 'Marksman'),
    1: ('Alteration', 'Conjuration', 'Destruction', 'Illusion', 'Restoration',
        'Enchanting'),
    2: ('Sneak', 'LightArmor', 'Lockpicking', 'Pickpocket', 'Speechcraft',
        'Alchemy'),
}

#: TES4 attribute order, indexed by DATA.PrimaryAttribute1/2.
_TES4_ATTR_NAMES = ('Strength', 'Intelligence', 'Willpower', 'Agility',
                    'Speed', 'Endurance', 'Personality', 'Luck')


def _clas_skill_weights(rec: dict) -> bytes:
    """The 18 TES5 skill weights for a TES4 class, clamped to 0-255.

    Skyblivion's algorithm: specialization gives its six skills +2, each of the
    two primary attributes gives its associated skills +1 (Luck gives every
    skill +1), and each of the seven major skills gives its TES5 equivalent +3.
    """
    weights = {s: 0 for s in TES5_SKILL_ORDER}
    for skill in _SPEC_SKILLS.get(get_int(rec, 'DATA.Specialization'), ()):
        weights[skill] = weights.get(skill, 0) + 2

    for key in ('DATA.PrimaryAttribute1', 'DATA.PrimaryAttribute2'):
        idx = get_int(rec, key)
        if not 0 <= idx < len(_TES4_ATTR_NAMES):
            continue
        attr = _TES4_ATTR_NAMES[idx]
        favoured = (TES5_SKILL_ORDER if attr == 'Luck'
                    else ATTRIBUTE_SKILL_MAP.get(attr, []))
        for skill in favoured:
            weights[skill] = weights.get(skill, 0) + 1

    for i in range(7):
        tes5_name = TES4_SKILL_TO_TES5.get(get_int(rec, f'DATA.MajorSkill[{i}]'))
        if tes5_name:
            weights[tes5_name] = weights.get(tes5_name, 0) + 3

    return bytes(min(255, max(0, weights.get(s, 0))) for s in TES5_SKILL_ORDER)


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

    skill_weights = _clas_skill_weights(rec)

    teaches_tes4 = get_int(rec, 'DATA.Teaches') + _TES4_SKILL_AV_BASE
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

    data = struct.pack('<I', _CLAS_FLAGS_DEFAULT)
    data += struct.pack('<bB', teaches, max_train)
    data += skill_weights
    data += struct.pack('<f', _CLAS_BLEEDOUT_DEFAULT)
    data += struct.pack('<I', 0)
    data += struct.pack('<4B', 1, 1, 1, 0)
    subs += pack_subrecord('DATA', data)

    fid = override_fid or get_formid(rec, 'FormID')
    flags = 0 if override_fid else get_int(rec, 'RecordFlags')
    return pack_record('CLAS', fid, flags, subs)

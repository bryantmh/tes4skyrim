"""CREA conversion: the creature actor, its flags, sounds and behaviour hooks.

Everything here is CREA-only; what NPC_ also needs lives in actor_common.

See: docs/commentary/tes5_import_actors.md
"""

import struct

from ..base.constants import TES5_SKILL_ORDER
from ..actors.creature_races import creature_capped_level, creature_health_offset
from ..actors.outfits import split_inventory
from ..packages.actor_wiring import (CLAS_CREATURE_CASTER, CLAS_CREATURE_PREDATOR,
                        CSTY_ANIMAL, CSTY_DEFAULT, DPLT_CREATURE_LIST,
                        PKID_CREATURE_MASTER)
from ..base.equivalents import (TES4_RACE_FID_TO_EDID, VOICE_TYPE_MAP,
                                resolve_creature_race)
from .actor_common import (GOLD001_FID, NAM5_UNKNOWN, SOUND_LEVEL_NORMAL,
                           T4C_ESSENTIAL, T4C_NO_BLOOD_DECAL,
                           T4C_NO_BLOOD_SPRAY, T4C_PC_LEVEL_OFFSET,
                           T4C_RESPAWN, T5_AUTOCALC, T5_DOESNT_BLEED,
                           T5_ESSENTIAL, T5_PC_LEVEL_MULT, T5_RESPAWN,
                           build_aidt, build_outfit, origin_memberships,
                           get_vendor_faction_fids_for_actor, npc_vtyp,
                           read_items)
from .spell_tomes import tome_items
from .common import (
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

#: The only CREA sound type the TES5 engine reads off the record: 7, Hit.
_RECORD_SOUND_TYPES = (7,)


# ---------------------------------------------------------------------------
#   Sounds
# ---------------------------------------------------------------------------


def _actor_sound_subs(rec: dict) -> bytes:
    """CSDT/CSDI/CSDC array for a TES4 CREA, in TES5 NPC_ order.

    Only _RECORD_SOUND_TYPES are written; b'' when the creature has none of
    its own, and the caller falls back to CSCR inheritance. A type may list
    several sounds, of which the first keeps the un-indexed export key.

    See: docs/commentary/tes5_import_actors.md#creature-sound-channels
    """
    subs = b''
    for i in range(get_int(rec, 'SoundTypeCount')):
        stype = get_int(rec, f'SoundType[{i}].Type', -1)
        if stype not in _RECORD_SOUND_TYPES:
            continue
        pairs, j = [], 0
        while True:
            key = (f'SoundType[{i}].Sound' if j == 0
                   else f'SoundType[{i}].Sound[{j}]')
            soun = get_formid(rec, key)
            if not soun:
                break
            pairs.append((soun, get_int(rec, f'{key}.Chance', 100)))
            j += 1
        if not pairs:
            continue
        subs += pack_uint32_subrecord('CSDT', stype)
        for soun, chance in pairs:
            subs += pack_formid_subrecord('CSDI', soun)
            subs += pack_uint8_subrecord('CSDC', max(0, min(100, chance)))
    return subs


def patch_actor_sounds(writer) -> int:
    """Rewrite every actor CSDI from its TES4 SOUN id to the real SNDR id.

    Actors are written long before Phase 3 creates the descriptors, so
    conversion stores the SOUN FormID and this resolves it. A CSDI whose SOUN
    produced no descriptor is dropped with its CSDT/CSDC.

    See: docs/commentary/tes5_import_actors.md#creature-sound-channels
    """
    from .sound import sndr_map
    mapping = sndr_map()
    records = writer._top_groups.get('NPC_') or []
    if not mapping or not records:
        return _flatten_cscr(records) if records else 0
    resolved = {v for v in mapping.values()}
    patched = 0
    for i, blob in enumerate(records):
        if b'CSDI' not in blob:
            continue
        out = _repatch_sound_block(blob, mapping, resolved, writer.own_index)
        if out is None:
            continue
        records[i] = out
        patched += 1
    patched += _flatten_cscr(records)
    return patched


def _resolve_csdi(soun: int, mapping: dict, resolved: set, own_index: int) -> int:
    """The SNDR a CSDI should hold, or 0 when its SOUN produced no descriptor.

    An index byte below our own slot names a MASTER, so that id was already
    resolved when the master was converted and passes through untouched.
    """
    if soun and (soun >> 24) < own_index:
        return soun
    if soun in resolved:
        return soun
    return mapping.get(soun & 0x00FFFFFF, 0)


def _subrecords(blob: bytes):
    """(signature, whole chunk) for each subrecord after the 24-byte header."""
    pos = 24
    while pos + 6 <= len(blob):
        size = struct.unpack_from('<H', blob, pos + 4)[0]
        yield blob[pos:pos + 4], blob[pos:pos + 6 + size]
        pos += 6 + size


def _repatch_sound_block(blob: bytes, mapping: dict, resolved: set,
                         own_index: int):
    """`blob` with every CSDI resolved, or None when nothing changed.

    A group is one CSDT followed by its CSDI/CSDC pairs, and the CSDT is only
    emitted when at least one of its sounds resolved.
    """
    out = bytearray(blob[:24])
    changed = False
    cur_type = b''
    cur_pairs = b''
    drop_csdc = False

    def _flush():
        """Commit the pending CSDT, but only if a sound under it resolved."""
        nonlocal cur_type, cur_pairs
        if cur_type and cur_pairs:
            out.extend(cur_type + cur_pairs)
        cur_type, cur_pairs = b'', b''

    for sig, chunk in _subrecords(blob):
        if sig == b'CSDT':
            _flush()
            cur_type = chunk
            drop_csdc = False
        elif sig == b'CSDI' and cur_type:
            soun = struct.unpack_from('<I', chunk, 6)[0]
            sndr = _resolve_csdi(soun, mapping, resolved, own_index)
            drop_csdc = not sndr
            changed = changed or sndr != soun
            cur_pairs += (chunk[:6] + struct.pack('<I', sndr)) if sndr else b''
        elif sig == b'CSDC' and cur_type:
            cur_pairs += b'' if drop_csdc else chunk
            drop_csdc = False
        else:
            _flush()
            out += chunk
    _flush()
    if not changed:
        return None
    struct.pack_into('<I', out, 4, len(out) - 24)
    return bytes(out)


def _sound_chunks(blob: bytes) -> bytes:
    """The record's CSDT/CSDI/CSDC subrecord bytes, contiguous, or b''."""
    return b''.join(chunk for sig, chunk in _subrecords(blob)
                    if sig in (b'CSDT', b'CSDI', b'CSDC'))


def _flatten_cscr(records: list) -> int:
    """Replace every actor CSCR with the target's own resolved CSDT array.

    Runs after the CSDI->SNDR patch so the inlined bytes already carry real
    descriptor ids.

    See: docs/commentary/tes5_import_actors.md#creature-sound-channels
    """
    by_fid = {struct.unpack_from('<I', blob, 12)[0]: i
              for i, blob in enumerate(records)}

    def find_cscr(blob):
        """(offset, size) of the CSCR subrecord, or None.

        A proper subrecord walk: a raw .find could match 'CSCR' inside
        another subrecord's data.
        """
        pos = 24
        for sig, chunk in _subrecords(blob):
            if sig == b'CSCR':
                return pos, len(chunk) - 6
            pos += len(chunk)
        return None

    def resolved_sounds(blob, depth=0):
        """This record's own sound chunks, else its CSCR target's."""
        own = _sound_chunks(blob)
        if own:
            return own
        hit = find_cscr(blob)
        if hit is None or depth >= 4:
            return b''
        target = struct.unpack_from('<I', blob, hit[0] + 6)[0]
        k = by_fid.get(target)
        return resolved_sounds(records[k], depth + 1) if k is not None else b''

    patched = 0
    for i, blob in enumerate(records):
        hit = find_cscr(blob)
        if hit is None:
            continue
        j, size = hit
        inline = resolved_sounds(blob)
        out = bytearray(blob[:j]) + inline + blob[j + 6 + size:]
        struct.pack_into('<I', out, 4, len(out) - 24)
        records[i] = bytes(out)
        patched += 1
    return patched


# ---------------------------------------------------------------------------
#   Record fields
# ---------------------------------------------------------------------------


def _crea_flags(tes4_flags: int) -> int:
    """TES4 CREA ACBS flags → TES5 NPC_ ACBS flags, translated by MEANING.

    Only bits whose meaning genuinely carries over are set. Autocalc is always
    set: a creature has no TES4 attribute block, so Skyrim derives its stats
    from race, level and the crea_acbs offsets.

    See: docs/commentary/tes5_import_actors.md#acbs-flag-collision
    """
    out = 0
    if tes4_flags & T4C_ESSENTIAL:
        out |= T5_ESSENTIAL
    if tes4_flags & T4C_RESPAWN:
        out |= T5_RESPAWN
    if tes4_flags & T4C_PC_LEVEL_OFFSET:
        out |= T5_PC_LEVEL_MULT
    if tes4_flags & T4C_NO_BLOOD_SPRAY and tes4_flags & T4C_NO_BLOOD_DECAL:
        out |= T5_DOESNT_BLEED
    out |= T5_AUTOCALC
    return out


def crea_nam6(rec: dict) -> bytes:
    """TES5 NPC_ NAM6 'Height' from the TES4 CREA BNAM 'Base Scale'.

    See: docs/commentary/tes5_import_actors.md#creature-scale
    """
    scale = get_float(rec, 'BNAM.BaseScale', 1.0)
    if scale <= 0.0:
        scale = 1.0
    return struct.pack('<f', scale)


def crea_acbs(rec: dict) -> bytes:
    """Build TES5 ACBS payload (24 bytes) from a TES4 CREA record.

    Shared by convert_CREA and the override path. Creatures auto-calc their
    stats (flag 0x10), so attributes stay zero, and the per-record health and
    magicka offsets carry the whole TES4 pool.

    See: docs/commentary/tes5_import_actors.md#creature-stat-offsets
    """
    tes4_flags = get_int(rec, 'ACBS.Flags')
    calc_min = get_int(rec, 'ACBS.CalcMin', 1)
    calc_max = get_int(rec, 'ACBS.CalcMax', 100)
    tes5_flags = _crea_flags(tes4_flags)
    tes5_level = creature_capped_level(rec)
    health_offset = creature_health_offset(rec)
    magicka_offset = min(get_int(rec, 'ACBS.SpellPoints', 0), 32767)
    return struct.pack('<IhhHHHHhHhH',
                       tes5_flags, magicka_offset, 0, tes5_level,
                       min(calc_min, 65535), min(calc_max, 65535),
                       100, 0, 0, health_offset, 0)
def _crea_vmad(rec: dict, packed: bytes) -> bytes:
    """The creature's VMAD subrecord, plus TES4_GhostDissolve when it applies.

    `packed` is a packed VMAD subrecord (or b''), unwrapped and repacked here.

    See: docs/commentary/tes5_import_actors.md#ghost-dissolve
    """
    from ..actors.creature_races import creature_dissolve_info
    dissolve = creature_dissolve_info(get_formid(rec, 'FormID'))
    if dissolve is None:
        return packed

    from script_convert.pipeline import append_vmad_object_script
    ash_pile, death_secs = dissolve
    raw = packed[6:] if packed else b''
    raw = append_vmad_object_script(
        raw, 'TES4_GhostDissolve',
        object_props={'AshPile': ash_pile},
        value_props={'DeathAnimSeconds': ('float', death_secs or 1.2)})
    return pack_subrecord('VMAD', raw)
def _crea_snams(rec: dict, vendor_fids: list) -> bytes:
    """Every faction this creature joins: its own, vendor, plugin-origin."""
    subs = b''
    for i in range(get_int(rec, 'FactionCount')):
        subs += pack_subrecord('SNAM', struct.pack(
            '<IbBBB', get_formid(rec, f'Faction[{i}].FormID'),
            get_int(rec, f'Faction[{i}].Rank'), 0, 0, 0))
    for vfid in vendor_fids:
        subs += pack_subrecord('SNAM', struct.pack('<IbBBB', vfid, 0, 0, 0, 0))
    for origin_fid in origin_memberships():
        subs += pack_subrecord('SNAM', struct.pack(
            '<IbBBB', origin_fid, 0, 0, 0, 0))
    return subs


def _crea_race(rec: dict, edid: str) -> int:
    """The generated creature RACE, else the Skyrim-race aliasing fallback."""
    from ..actors.creature_races import get_creature_race
    fid = get_creature_race(get_formid(rec, 'FormID') & 0x00FFFFFF)
    if fid is not None:
        return fid
    fid, _src, _alt = resolve_creature_race(edid, get_str(rec, 'FULL'))
    return fid


def _crea_voice(rec: dict) -> int:
    """VTYP for a creature, ALWAYS non-omitted even when the chain misses.

    The TES4 race name wins; failing that the aliased creature race, then
    Imperial. No TES4 creature race is in VOICE_TYPE_MAP, so this normally
    resolves to 0 and patch_creature_voices fills the slot later.

    See: docs/commentary/tes5_import_actors.md#crea-vtck-always
    """
    gender = 'Female' if (get_int(rec, 'ACBS.Flags') & 1) else 'Male'
    race_edid = TES4_RACE_FID_TO_EDID.get(
        get_formid(rec, 'RNAM.Race') & 0x00FFFFFF, '')
    if not race_edid:
        _fid, src, _alt = resolve_creature_race(get_str(rec, 'EditorID'),
                                               get_str(rec, 'FULL'))
        race_edid = src or 'Imperial'
    return (npc_vtyp(get_formid(rec, 'FormID'))
            or VOICE_TYPE_MAP.get((race_edid, gender))
            or VOICE_TYPE_MAP.get(('Imperial', gender), 0))


def _crea_spell_subs(rec: dict) -> bytes:
    """SPCT + SPLO for a creature's spells.

    The target may be a SPEL, SHOU or LVSP -- xEdit types SPLO as all three --
    so a TES4 leveled spell is referenced directly rather than unrolled.

    See: docs/commentary/tes5_import_actors.md#crea-spells
    """
    fids = [get_formid(rec, f'Spell[{i}]')
            for i in range(get_int(rec, 'SpellCount'))]
    fids = [f for f in fids if f]
    if not fids:
        return b''
    subs = pack_subrecord('SPCT', struct.pack('<I', len(fids)))
    for fid in fids:
        subs += pack_formid_subrecord('SPLO', fid)
    return subs


def _crea_inventory_subs(carried: list, barter_gold: int) -> bytes:
    """COCT + CNTO for a creature.

    Creature inventories are mostly loot leveled-lists, which belong in CNTO;
    only the armed/armored ones (skeletons, dremora) yield an outfit at all.
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


#: NAM7 'Weight' for a creature; TES4 has no per-creature weight.
_CREA_WEIGHT = 50.0

#: TES4 CREA DATA.Type values that take the vanilla ANIMAL combat style.
_ANIMAL_CREA_TYPES = (0, 4)


def _crea_class(rec: dict):
    """The CLAS an autocalc creature derives its skill weights from.

    See: docs/commentary/tes5_import_actors.md#creature-class-and-package
    """
    from ..actors.creature_races import creature_has_offensive_spell
    return (CLAS_CREATURE_CASTER if creature_has_offensive_spell([rec])
            else CLAS_CREATURE_PREDATOR)


def _crea_combat_style(rec: dict) -> int:
    """A vanilla CSTY, picked off TES4 DATA.Type; CSTY records are skipped."""
    return (CSTY_ANIMAL if get_int(rec, 'DATA.Type') in _ANIMAL_CREA_TYPES
            else CSTY_DEFAULT)


def _crea_dnam(rec: dict) -> bytes:
    """52-byte DNAM: skills spread from the three TES4 aggregate skills.

    Offsets 36/38/40 are the engine's Health/Magicka/Stamina cache, whose real
    TES4 pools are DATA.Health, ACBS.SpellPoints and ACBS.Fatigue.

    See: docs/commentary/tes5_import_actors.md#health-offset
    """
    dnam = bytearray(52)
    combat = get_int(rec, 'DATA.CombatSkill', 30)
    magic = get_int(rec, 'DATA.MagicSkill', 30)
    stealth = get_int(rec, 'DATA.StealthSkill', 30)
    by_skill = {
        'OneHanded': combat, 'TwoHanded': combat, 'Block': combat,
        'Smithing': combat, 'HeavyArmor': combat, 'LightArmor': stealth,
        'Marksman': stealth, 'Sneak': stealth, 'Lockpicking': stealth,
        'Pickpocket': stealth, 'Destruction': magic, 'Conjuration': magic,
        'Alteration': magic, 'Illusion': magic, 'Restoration': magic,
        'Alchemy': magic, 'Speechcraft': stealth, 'Enchanting': magic // 3,
    }
    for i, skill_name in enumerate(TES5_SKILL_ORDER):
        dnam[i] = min(by_skill.get(skill_name, 15), 255)
    for offset, key, default in ((36, 'DATA.Health', 50),
                                 (38, 'ACBS.SpellPoints', 0),
                                 (40, 'ACBS.Fatigue', 100)):
        struct.pack_into('<H', dnam, offset,
                         max(0, min(get_int(rec, key, default), 65535)))
    return bytes(dnam)


def _crea_nam_subs(rec: dict) -> bytes:
    """The four required NAM slots; NAM6 is the creature's authored scale."""
    subs = pack_subrecord('NAM5', NAM5_UNKNOWN)
    subs += pack_subrecord('NAM6', crea_nam6(rec))
    subs += pack_subrecord('NAM7', struct.pack('<f', _CREA_WEIGHT))
    subs += pack_uint32_subrecord('NAM8', SOUND_LEVEL_NORMAL)
    return subs


def _crea_sound_subs(rec: dict) -> bytes:
    """The creature's own CSDT array, else a CSCR _flatten_cscr resolves later.

    Both sit after ZNAM and before DOFT.

    See: docs/commentary/tes5_import_actors.md#creature-sound-channels
    """
    own = _actor_sound_subs(rec)
    if own:
        return own
    inherit = get_formid(rec, 'CSCR.InheritSound')
    return pack_formid_subrecord('CSCR', inherit) if inherit else b''


def _crea_outfit_subs(rec: dict, edid: str, outfit_fids: list, writer) -> bytes:
    """DOFT (when the creature wears anything) plus the default package list."""
    subs = b''
    if writer is not None and outfit_fids:
        subs += pack_formid_subrecord(
            'DOFT', build_outfit(writer, (edid or 'CREA') + '_Outfit',
                                 outfit_fids, get_formid(rec, 'FormID')))
    return subs + pack_formid_subrecord('DPLT', DPLT_CREATURE_LIST)


def convert_CREA(rec: dict, writer=None) -> bytes:
    """CREA → NPC_ (creatures become NPCs in TES5).

    Same subrecord order as NPC_: EDID OBND ACBS SNAM INAM VTCK RNAM
    SPCT SPLO[] COCT/CNTO AIDT PKID FULL DATA DNAM ZNAM DOFT DPLT.

    Imports get_object_vmad in the body to break the cycle through
    object_scripts -> constants -> this module.

    See: docs/commentary/tes5_import_actors.md#creature-class-and-package
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    from ..base.object_scripts import get_object_vmad
    subs += _crea_vmad(rec, get_object_vmad(get_formid(rec, 'FormID')))
    subs += pack_obnd(-12, -12, 0, 12, 12, 60)
    subs += pack_subrecord('ACBS', crea_acbs(rec))

    crea_vendor_fids = get_vendor_faction_fids_for_actor(
        get_formid(rec, 'FormID'), get_int(rec, 'AIDT.Services'))
    subs += _crea_snams(rec, crea_vendor_fids)
    crea_vendor_fid = crea_vendor_fids[0] if crea_vendor_fids else 0

    inam = get_formid(rec, 'INAM.DeathItem')
    if inam:
        subs += pack_formid_subrecord('INAM', inam)

    crea_race_fid = _crea_race(rec, edid)
    subs += pack_formid_subrecord('VTCK', _crea_voice(rec))
    subs += pack_formid_subrecord('RNAM', crea_race_fid)

    subs += _crea_spell_subs(rec)

    full = get_str(rec, 'FULL')
    outfit_fids, carried = split_inventory(read_items(rec))
    subs += _crea_inventory_subs(
        carried + tome_items(get_formid(rec, 'FormID')),
        get_int(rec, 'ACBS.BarterGold') if crea_vendor_fid else 0)

    subs += pack_subrecord('AIDT', build_aidt(rec))
    subs += pack_formid_subrecord('PKID', PKID_CREATURE_MASTER)
    subs += pack_formid_subrecord('CNAM', _crea_class(rec))

    if full:
        subs += pack_string_subrecord('FULL', full)
    subs += pack_subrecord('DATA', b'')
    subs += pack_subrecord('DNAM', _crea_dnam(rec))
    subs += pack_formid_subrecord('ZNAM', _crea_combat_style(rec))

    subs += _crea_nam_subs(rec)
    subs += _crea_sound_subs(rec)

    subs += _crea_outfit_subs(rec, edid, outfit_fids, writer)
    return pack_record('NPC_', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)

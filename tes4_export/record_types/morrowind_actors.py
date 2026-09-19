"""
Morrowind actor exporters: NPCs, creatures, leveled lists, factions, classes.

Each emits the TES4 actor vocabulary (`ACBS.*`, `AIDT.*`, `DATA.<skill>`,
`Faction[i]`, `Item[i]`) so `convert_NPC_` and `convert_CREA` -- and the
outfit, vendor and Skyrim-race machinery behind them -- run unchanged. Races
are named by Oblivion's RACE FormIDs, which the importer already maps onto
Skyrim's races.

See: docs/commentary/tes4_export_morrowind.md#actors-and-placements
"""

import os
import struct

from ..record_types.common import escape_value
from ..tes3_reader import (Tes3Record, get_all_subrecords, get_string,
                           get_subrecord)
from .morrowind import (MW_SKILL_TO_TES4, emit_inventory, emit_ref, emit_str,
                        unpack)
from .morrowind_packages import emit_packages

#: Morrowind race ID -> the Oblivion RACE FormID the importer maps to Skyrim's.
RACE_FORMIDS = {
    'argonian': 0x00023FE9, 'breton': 0x000224FC, 'dark elf': 0x000191C1,
    'high elf': 0x00019204, 'imperial': 0x00000907, 'khajiit': 0x000223C7,
    'nord': 0x000224FD, 'orc': 0x000191C0, 'redguard': 0x00000D43,
    'wood elf': 0x000223C8,
}

#: Imperial: what the importer itself falls back to.
_DEFAULT_RACE = 0x00000907

#: Attribute order, identical in both games.
_ATTRIBUTES = ('Strength', 'Intelligence', 'Willpower', 'Agility', 'Speed',
               'Endurance', 'Personality', 'Luck')

#: TES4 skill names by TES4 skill index (the NPC DATA.<skill> keys).
_TES4_SKILLS = ('Armorer', 'Athletics', 'Blade', 'Block', 'Blunt',
                'HandToHand', 'HeavyArmor', 'Alchemy', 'Alteration',
                'Conjuration', 'Destruction', 'Illusion', 'Mysticism',
                'Restoration', 'Acrobatics', 'LightArmor', 'Marksman',
                'Mercantile', 'Security', 'Sneak', 'Speechcraft')

#: (Morrowind FLAG bit, TES4 ACBS bit) for NPCs: female, essential, respawn, autocalc.
_NPC_FLAGS = ((0x01, 0x01), (0x02, 0x02), (0x04, 0x08), (0x10, 0x10))

#: The same for creatures: biped, respawn, weapon+shield, swims, flies, walks, essential.
_CREA_FLAGS = ((0x01, 0x01), (0x02, 0x08), (0x04, 0x04), (0x10, 0x10),
               (0x20, 0x20), (0x40, 0x40), (0x80, 0x02))

#: AIDT service bits TES4 lacks: picks, probes, repair items, training, spellmaking.
_SERVICES_TES3_ONLY = 0x20 | 0x40 | 0x200 | 0x4000 | 0x8000

#: The 18 service bits either game defines; creature AIDT carries junk above them.
_SERVICES_MASK = 0x3FFFF & ~_SERVICES_TES3_ONLY

#: TES4 AIDT energy level for every actor; Morrowind has no such value.
_ENERGY_LEVEL = 50

#: Morrowind soul-gem capacities -> TES4 soul level (petty .. greater; else grand).
_SOUL_LEVELS = ((30, 1), (60, 2), (100, 3), (200, 4))
_SOUL_GRAND = 5

#: LEVI flags: Each 0x1 -> TES4 0x2, AllLevels 0x2 -> TES4 0x1. LEVC keeps 0x1.
_LEVI_FLAGS = ((0x1, 0x2), (0x2, 0x1))
_LEVC_FLAGS = ((0x1, 0x1),)

#: FADT layout: 2 attributes, 10 x 5-int rank data, 7 skills, then the flags.
_FADT_FLAGS_OFFSET = 236
_FACTION_HIDDEN = 0x1

#: CLDT: 2 attributes, specialization, 5 x 2 skills, playable, services.
_CLDT_FORMAT = '<2ii10iii'

#: SNDG type -> CSDT slot. See: docs/commentary/tes4_export_morrowind.md#creature-sound-generators
_SOUND_GEN_SLOTS = {0: 0, 1: 1, 2: 2, 3: 3, 4: 5, 5: 6, 6: 7, 7: 8}

#: The bones-only NIF the creature pipeline keys a folder on.
_SKELETON_NIF = 'skeleton.nif'


def _map_flags(value: int, table) -> int:
    """Translate flag bits through a (source bit, target bit) table."""
    return sum(target for source, target in table if value & source)


def _clamp(value: int, top: int) -> int:
    """`value` held to [0, top]."""
    return max(0, min(int(value), top))


def _emit_aidt(lines: list, rec: Tes3Record) -> None:
    """AIDT: hello, fight, flee, alarm and services onto TES4's fields.

    See: docs/commentary/tes4_export_morrowind.md#actors-and-placements
    """
    data = unpack(rec, 'AIDT', '<HBBB3xi')
    if data is None:
        return
    _hello, fight, flee, alarm, services = data
    lines.extend([f'AIDT.Aggression={_clamp(fight, 100)}',
                  f'AIDT.Confidence={_clamp(100 - flee, 100)}',
                  f'AIDT.EnergyLevel={_ENERGY_LEVEL}',
                  f'AIDT.Responsibility={_clamp(alarm, 100)}',
                  f'AIDT.Services={services & _SERVICES_MASK}',
                  'AIDT.Teaches=-1', 'AIDT.MaxTraining=0'])


def _emit_attributes(lines: list, values) -> None:
    """DATA.<attribute> for the eight attributes, in shared order."""
    for name, value in zip(_ATTRIBUTES, values):
        lines.append(f'DATA.{name}={_clamp(value, 255)}')


def _emit_skills(lines: list, skills) -> None:
    """DATA.<skill> for TES4's 21 skills, folding Morrowind's 27 onto them.

    Where two Morrowind skills land on one TES4 skill the larger wins.
    """
    best = {}
    for index, value in enumerate(skills):
        tes4 = MW_SKILL_TO_TES4.get(index)
        if tes4 is not None:
            best[tes4] = max(best.get(tes4, 0), value)
    for index, name in enumerate(_TES4_SKILLS):
        lines.append(f'DATA.{name}={_clamp(best.get(index, 0), 255)}')


def _emit_npc_full_stats(lines: list, data: bytes) -> int:
    """The 52-byte NPDT: authored stats. Returns the faction rank."""
    fields = unpack_bytes(data, '<h8B27BxHHHBBBxi')
    level, attributes, skills = fields[0], fields[1:9], fields[9:36]
    health, mana, fatigue, _disposition, _reputation, rank, gold = fields[36:]
    lines.extend([f'ACBS.Level={level}', f'ACBS.SpellPoints={mana}',
                  f'ACBS.Fatigue={fatigue}', f'ACBS.BarterGold={gold}',
                  f'DATA.Health={health}'])
    _emit_skills(lines, skills)
    _emit_attributes(lines, attributes)
    return rank


def _emit_npc_autocalc_stats(lines: list, data: bytes) -> int:
    """The 12-byte NPDT: level and gold only. Returns the faction rank."""
    level, _disposition, _reputation, rank, gold = unpack_bytes(
        data, '<hBBB3xi')
    lines.extend([f'ACBS.Level={level}', f'ACBS.BarterGold={gold}'])
    return rank


def unpack_bytes(data: bytes, fmt: str) -> tuple:
    """`struct.unpack_from` over a payload that is known to be long enough."""
    return struct.unpack_from(fmt, data, 0)


def _emit_faction(lines: list, rec: Tes3Record, ctx, rank: int) -> None:
    """The single faction Morrowind allows an NPC, with its rank."""
    sub = get_subrecord(rec, 'ANAM')
    form_id = ctx.resolve(get_string(sub)) if sub else ''
    lines.append(f'FactionCount={int(bool(form_id))}')
    if form_id:
        lines.append(f'Faction[0].FormID={form_id}')
        lines.append(f'Faction[0].Rank={rank}')


def _emit_spells(lines: list, rec: Tes3Record, ctx) -> None:
    """Emit the NPCS spell list -- one fixed 32-byte ID per subrecord.

    A spell this pass does not convert is left out rather than written as a
    null FormID.
    """
    spells = []
    for sub in get_all_subrecords(rec, 'NPCS'):
        form_id = ctx.resolve(get_string(sub), 'SPEL')
        if form_id:
            spells.append(form_id)
    lines.append(f'SpellCount={len(spells)}')
    for index, form_id in enumerate(spells):
        lines.append(f'Spell[{index}]={form_id}')


def export_NPC_(rec: Tes3Record, ctx) -> list:
    """An NPC in TES4's actor vocabulary, raced by Oblivion FormID.

    See: docs/commentary/tes4_export_morrowind.md#actors-and-placements
    """
    lines = [f'EditorID={escape_value(rec.record_id)}']
    emit_str(lines, 'FULL', rec, 'FNAM')
    flags = unpack(rec, 'FLAG', '<I')
    lines.append(f'ACBS.Flags={_map_flags(flags[0] if flags else 0, _NPC_FLAGS)}')
    npdt = get_subrecord(rec, 'NPDT')
    rank = 0
    if npdt is not None and len(npdt.data) >= 52:
        rank = _emit_npc_full_stats(lines, npdt.data)
    elif npdt is not None and len(npdt.data) >= 12:
        rank = _emit_npc_autocalc_stats(lines, npdt.data)
    race = get_subrecord(rec, 'RNAM')
    race_id = get_string(race).lower() if race else ''
    lines.append(f'RNAM.Race={RACE_FORMIDS.get(race_id, _DEFAULT_RACE):08X}')
    _emit_faction(lines, rec, ctx, rank)
    emit_inventory(lines, rec, ctx)
    _emit_spells(lines, rec, ctx)
    _emit_aidt(lines, rec)
    emit_packages(lines, rec, ctx)
    emit_ref(lines, 'CNAM.Class', rec, 'CNAM', ctx, 'CLAS')
    return lines


def _soul_level(soul: int) -> int:
    """TES4 soul level for a Morrowind soul value, by gem capacity."""
    if soul <= 0:
        return 0
    for capacity, level in _SOUL_LEVELS:
        if soul <= capacity:
            return level
    return _SOUL_GRAND


def export_CREA(rec: Tes3Record, ctx) -> list:
    """A creature in TES4's CREA vocabulary; the type enums agree.

    See: docs/commentary/tes4_export_morrowind.md#actors-and-placements
    """
    lines = [f'EditorID={escape_value(rec.record_id)}']
    emit_str(lines, 'FULL', rec, 'FNAM')
    _emit_creature_model(lines, rec, ctx)
    flags = unpack(rec, 'FLAG', '<I')
    lines.append(f'ACBS.Flags={_map_flags(flags[0] if flags else 0, _CREA_FLAGS)}')
    data = unpack(rec, 'NPDT', '<ii8i7i6ii')
    if data is not None:
        ctype, level, attributes = data[0], data[1], data[2:10]
        health, mana, fatigue, soul, combat, magic, stealth = data[10:17]
        attacks, gold = data[17:23], data[23]
        lines.extend([f'ACBS.Level={level}',
                      f'ACBS.SpellPoints={_clamp(mana, 65535)}',
                      f'ACBS.Fatigue={_clamp(fatigue, 65535)}',
                      f'ACBS.BarterGold={_clamp(gold, 65535)}',
                      f'DATA.Type={ctype}',
                      f'DATA.CombatSkill={_clamp(combat, 255)}',
                      f'DATA.MagicSkill={_clamp(magic, 255)}',
                      f'DATA.StealthSkill={_clamp(stealth, 255)}',
                      f'DATA.Soul={_soul_level(soul)}',
                      f'DATA.Health={_clamp(health, 65535)}',
                      f'DATA.AttackDamage={_clamp(max(attacks[1::2]), 65535)}'])
        _emit_attributes(lines, attributes)
    scale = unpack(rec, 'XSCL', '<f')
    if scale:
        lines.append(f'BNAM.BaseScale={scale[0]}')
    lines.append('FactionCount=0')
    emit_inventory(lines, rec, ctx)
    _emit_spells(lines, rec, ctx)
    _emit_aidt(lines, rec)
    emit_packages(lines, rec, ctx)
    _emit_sound_slots(lines, rec, ctx)
    return lines


def _emit_creature_model(lines: list, rec: Tes3Record, ctx) -> None:
    """Point the creature at the folder whose project the importer binds it to.

    A mesh in this plugin's own tree is split here (`MorrowindModel`); a
    vanilla mesh Morroblivion replaces takes that creature's model and parts;
    anything else keeps the derived folder for a master's project to fill.
    See: docs/commentary/tes4_export_morrowind.md#morroblivion-creatures
    """
    sub = get_subrecord(rec, 'MODL')
    path = get_string(sub).replace('/', chr(92)).lstrip(chr(92)) if sub else ''
    if not path:
        return
    own = ctx.morroblivion is not None and ctx.morroblivion.owns(path)
    replacement = (None if own or ctx.morroblivion is None
                   else ctx.morroblivion.creature(path))
    if replacement is not None:
        model, parts = replacement
        lines.append(f'Model.MODL={escape_value(model)}')
        lines.append(f'NIFZCount={len(parts)}')
        lines.extend(f'NIFZ[{i}]={escape_value(p)}' for i, p in enumerate(parts))
        return
    folder, name = os.path.split(path)
    stem = os.path.splitext(name)[0].lower()
    skeleton = chr(92).join(p for p in (folder, stem, _SKELETON_NIF) if p)
    body = stem + ('_body' if stem + '.nif' == _SKELETON_NIF else '') + '.nif'
    lines.extend([f'Model.MODL={escape_value(skeleton)}', 'NIFZCount=1',
                  f'NIFZ[0]={body}'])
    if own:
        lines.append(f'MorrowindModel={escape_value(path)}')


def _emit_sound_slots(lines: list, rec: Tes3Record, ctx) -> None:
    """The creature's SNDG sound generators as TES4 CSDT slots."""
    slots = ctx.sound_gens.get(rec.record_id.lower(), {})
    lines.append(f'SoundTypeCount={len(slots)}')
    for index, (slot, form_id) in enumerate(sorted(slots.items())):
        lines.append(f'SoundType[{index}].Type={slot}')
        lines.append(f'SoundType[{index}].Sound={form_id}')


def register_sound_gens(records, ctx) -> None:
    """Index every SNDG by creature: {creature id: {CSDT slot: SOUN FormID}}."""
    for rec in records:
        if rec.type != 'SNDG' or rec.deleted:
            continue
        kind = unpack(rec, 'DATA', '<i')
        creature = get_subrecord(rec, 'CNAM')
        sound = get_subrecord(rec, 'SNAM')
        slot = _SOUND_GEN_SLOTS.get(kind[0]) if kind else None
        form_id = ctx.resolve(get_string(sound), 'SOUN') if sound else ''
        if slot is None or creature is None or not form_id:
            continue
        ctx.sound_gens.setdefault(get_string(creature).lower(), {})[slot] = form_id


def _leveled(rec: Tes3Record, ctx, entry_sig: str, flag_table,
             target: str = '') -> list:
    """A leveled list: chance-none, translated flags and resolvable entries.

    Entries are `entry_sig` (the object) followed by `INTV` (the level);
    `target` is the TES4 type they name, where the list has only one.
    See: docs/commentary/tes4_export_morrowind.md#leveled-lists
    """
    lines = [f'EditorID={escape_value(rec.record_id)}']
    flags = unpack(rec, 'DATA', '<i')
    nnam = get_subrecord(rec, 'NNAM')
    lines.append(f'LVLD.ChanceNone={nnam.data[0] if nnam and nnam.data else 0}')
    lines.append(f'LVLF.Flags={_map_flags(flags[0] if flags else 0, flag_table)}')
    entries = []
    pending = ''
    for sub in rec.subrecords:
        if sub.type == entry_sig:
            pending = ctx.resolve(get_string(sub), target)
        elif sub.type == 'INTV' and len(sub.data) >= 2:
            if pending:
                entries.append((pending, unpack_bytes(sub.data, '<H')[0]))
            pending = ''
    lines.append(f'EntryCount={len(entries)}')
    for index, (form_id, level) in enumerate(entries):
        lines.append(f'Entry[{index}].Level={max(1, level)}')
        lines.append(f'Entry[{index}].FormID={form_id}')
        lines.append(f'Entry[{index}].Count=1')
    return lines


def export_LEVI(rec: Tes3Record, ctx) -> list:
    """A leveled item list -> LVLI."""
    return _leveled(rec, ctx, 'INAM', _LEVI_FLAGS)


def export_LEVC(rec: Tes3Record, ctx) -> list:
    """A leveled creature list -> LVLC."""
    return _leveled(rec, ctx, 'CNAM', _LEVC_FLAGS, 'CREA')


def export_FACT(rec: Tes3Record, ctx) -> list:
    """A faction: name, hidden flag and its reactions to other factions."""
    lines = [f'EditorID={escape_value(rec.record_id)}']
    emit_str(lines, 'FULL', rec, 'FNAM')
    fadt = get_subrecord(rec, 'FADT')
    hidden = 0
    if fadt and len(fadt.data) >= _FADT_FLAGS_OFFSET + 4:
        hidden = unpack_bytes(fadt.data[_FADT_FLAGS_OFFSET:], '<i')[0]
    lines.append(f'DATA.Flags={int(bool(hidden & _FACTION_HIDDEN))}')
    relations = []
    pending = ''
    for sub in rec.subrecords:
        if sub.type == 'ANAM':
            pending = ctx.resolve(get_string(sub), 'FACT')
        elif sub.type == 'INTV' and len(sub.data) >= 4:
            if pending:
                relations.append((pending, unpack_bytes(sub.data, '<i')[0]))
            pending = ''
    lines.append(f'RelationCount={len(relations)}')
    for index, (form_id, disposition) in enumerate(relations):
        lines.append(f'Relation[{index}].Faction={form_id}')
        lines.append(f'Relation[{index}].Disposition={disposition}')
    return lines


def export_CLAS(rec: Tes3Record, ctx) -> list:
    """A character class: specialization, playability and services."""
    lines = [f'EditorID={escape_value(rec.record_id)}']
    emit_str(lines, 'FULL', rec, 'FNAM')
    emit_str(lines, 'DESC', rec, 'DESC')
    data = unpack(rec, 'CLDT', _CLDT_FORMAT)
    if data:
        specialization, playable, services = data[2], data[13], data[14]
        lines.extend([f'DATA.Specialization={specialization}',
                      f'DATA.Flags={playable & 1}',
                      f'DATA.Services={services & _SERVICES_MASK}',
                      'DATA.Teaches=-1', 'DATA.MaxTraining=0'])
    return lines


#: TES3 signature -> exporter for every actor-side record.
MORROWIND_ACTOR_EXPORTERS = {
    'NPC_': export_NPC_,
    'CREA': export_CREA,
    'LEVI': export_LEVI,
    'LEVC': export_LEVC,
    'FACT': export_FACT,
    'CLAS': export_CLAS,
}

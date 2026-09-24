"""Morrowind magic exporters: MGEF, SPEL, ENCH and the shared effect list.

TES3 addresses magic effects by an index into an engine-fixed table of 143,
where TES4 gives each effect a four-character code and its own record.  The
index is the authored datum, so that is what every emitted effect carries and
what the import side keys its archetype table on.

Everything else is the TES4 vocabulary the tes5_import converters already
read, so `_pack_effects` and `convert_SPEL` need no Morrowind-specific code.

See: docs/commentary/tes4_export_morrowind.md#magic
"""

import struct

from ..morrowind_mgef_names import MW_EFFECT_NAMES, MW_HARDCODED_FLAGS
from ..tes3_reader import Tes3Record, get_all_subrecords, get_string, get_subrecord
from .common import escape_value
from .morrowind import (MW_SKILL_TO_TES4, emit_common, emit_icon,
                        emit_str, emit_value_weight, unpack)

#: One ENAM effect: index, skill, attribute, range, area, duration, min, max.
_ENAM = '<hbbiiiii'

#: ENAM range -> the TES4 `Effect[i].Type` name for it.
_RANGES = ('Self', 'Touch', 'Target')

#: TES3 marks "no skill/attribute for this effect" with -1.
_NO_AV = -1

#: TES4 packs skills above the eight attributes; a skill AV is its index + 12.
_SKILL_AV_BASE = 12

#: TES3 spell type -> TES4 SPIT.Type; blight and disease both become Disease.
_SPELL_TYPES = {0: 0, 1: 4, 2: 1, 3: 1, 4: 4, 5: 3}

#: TES3 enchant type -> TES4 ENIT.Type (0 scroll, 1 staff, 2 weapon, 3 apparel).
_ENCHANT_TYPES = {0: 0, 1: 2, 2: 1, 3: 3}

#: ALDT autocalc bit; TES4 ENIT 0x01 means "no auto-calc", the inverse.
_ALCH_AUTOCALC = 0x1

#: SPDT PCStart: a spell character creation may give the player.
_SPDT_PC_START = 0x2
#: TES4 SPIT Manual Spell Cost: SPDT's cost is TES3's own, stored for autocalc spells too.
_SPIT_MANUAL_COST = 0x1
#: TES4 SPIT Player Start Spell.
_SPIT_PLAYER_START = 0x4

#: An INGR names four effects with no magnitude or duration of their own.
_INGREDIENT_EFFECTS = 4

#: IRDT: weight, value, then four each of effect index, skill and attribute.
_IRDT = '<fi4i4i4i'

#: ENAM range -> the TES3 MEDT cast flag standing for it.
_RANGE_FLAGS = {0: 0x40, 1: 0x80, 2: 0x100}
_CAST_RANGES = 0x40 | 0x80 | 0x100

#: (export key, TES3 subrecord) naming the VFX record behind each of an effect's visuals.
_VISUALS = (('MorrowindArt.Cast', 'CVFX'), ('MorrowindArt.Bolt', 'BVFX'),
            ('MorrowindArt.Hit', 'HVFX'), ('MorrowindArt.Area', 'AVFX'))

#: (export key, TES3 subrecord, suffix of the school's default sound) for an effect's four sounds.
_SOUNDS = (('DATA.CastingSound', 'CSND', 'cast'), ('DATA.BoltSound', 'BSND', 'bolt'),
           ('DATA.HitSound', 'HSND', 'hit'), ('DATA.AreaSound', 'ASND', 'area'))

#: MEDT school index -> the name the engine builds a school's default sound ids from.
_SCHOOL_SOUNDS = ('alteration', 'conjuration', 'destruction', 'illusion',
                  'mysticism', 'restoration')

#: Bound-item effect index -> (the GMST naming the item the engine conjures, that item's TES4 type).
_BOUND_ITEMS = {
    120: ('smagicbounddaggerid', 'WEAP'), 121: ('smagicboundlongswordid', 'WEAP'),
    122: ('smagicboundmaceid', 'WEAP'), 123: ('smagicboundbattleaxeid', 'WEAP'),
    124: ('smagicboundspearid', 'WEAP'), 125: ('smagicboundlongbowid', 'WEAP'),
    127: ('smagicboundcuirassid', 'ARMO'), 128: ('smagicboundhelmid', 'ARMO'),
    129: ('smagicboundbootsid', 'ARMO'), 130: ('smagicboundshieldid', 'ARMO'),
    131: ('smagicboundrightgauntletid', 'ARMO'),
}

#: Effect index -> the TES4 resist AV, which picks the element's projectile.
_RESIST_AV = {
    14: 61, 28: 61, 90: 61,                     # fire
    16: 62, 29: 62, 91: 62,                     # frost
    15: 68, 30: 68, 92: 68,                     # shock
    27: 67, 35: 67, 97: 67,                     # poison
    31: 64, 93: 64,                             # magicka
    32: 63, 33: 63, 34: 63, 94: 63, 95: 63, 96: 63,   # disease
    45: 66, 73: 66, 99: 66,                     # paralysis
}


def effect_name(index: int) -> str:
    """The canonical name of one effect index, synthetic past the table end."""
    if 0 <= index < len(MW_EFFECT_NAMES):
        return MW_EFFECT_NAMES[index]
    return f'Effect{index}'


def effect_editor_id(index: int) -> str:
    """The EditorID the synthesized MGEF for one effect index carries."""
    return f'MW{index:03d}{effect_name(index)}'


def _actor_value(skill: int, attribute: int) -> int:
    """The TES4 actor value one ENAM targets, or -1 when it targets none.

    Morrowind's eight attributes are Oblivion's eight, and its 27 skills fold
    onto TES4's 21, so the result lands in the index space the import side's
    `resolve_actor_value` already reads.
    """
    if attribute != _NO_AV:
        return attribute
    if skill != _NO_AV:
        return MW_SKILL_TO_TES4.get(skill, _NO_AV) + _SKILL_AV_BASE
    return _NO_AV


def _emit_effect(lines: list, index: int, entry: tuple) -> None:
    """Emit one `Effect[i].*` block from an unpacked ENAM."""
    effect, skill, attribute, rng, area, duration, low, high = entry
    lines.append(f'Effect[{index}].EFID={effect_editor_id(effect)}')
    lines.append(f'Effect[{index}].MorrowindIndex={effect}')
    lines.append(f'Effect[{index}].Magnitude={(low + high) // 2}')
    lines.append(f'Effect[{index}].Area={area}')
    lines.append(f'Effect[{index}].Duration={duration}')
    if 0 <= rng < len(_RANGES):
        lines.append(f'Effect[{index}].Type={_RANGES[rng]}')
    lines.append(f'Effect[{index}].ActorValue={_actor_value(skill, attribute)}')


def emit_effects(lines: list, rec: Tes3Record) -> None:
    """Emit every ENAM effect a spell, enchantment or potion carries.

    The magnitude is the MEAN of the authored range: a TES5 EFIT holds one
    number, and the maximum would overstate every spell in the game.
    """
    entries = [struct.unpack(_ENAM, sub.data)
               for sub in get_all_subrecords(rec, 'ENAM')
               if len(sub.data) == struct.calcsize(_ENAM)]
    lines.append(f'EffectCount={len(entries)}')
    for index, entry in enumerate(entries):
        _emit_effect(lines, index, entry)


def emit_ingredient_effects(lines: list, rec: Tes3Record) -> None:
    """Emit an ingredient's effects, which carry no magnitude or duration.

    IRDT names them in three parallel arrays rather than as ENAM subrecords,
    and an unused slot is -1.
    """
    data = unpack(rec, 'IRDT', _IRDT)
    if data is None:
        lines.append('EffectCount=0')
        return
    effects = data[2:2 + _INGREDIENT_EFFECTS]
    skills = data[6:6 + _INGREDIENT_EFFECTS]
    attributes = data[10:10 + _INGREDIENT_EFFECTS]
    emitted = []
    count = 0
    for effect, skill, attribute in zip(effects, skills, attributes):
        if effect >= 0:
            _emit_effect(emitted, count, (effect, skill, attribute, 0, 0, 0, 1, 1))
            count += 1
    lines.append(f'EffectCount={count}')
    lines.extend(emitted)


def export_MGEF(rec: Tes3Record, ctx) -> list:
    """One magic effect, keyed by the index TES3 addresses it with.

    MEDT is 36 bytes: school, base cost, flags, an RGB glow and three sizing
    fields.  The flags stay TES3's own bits, translated on the import side
    because their meanings differ from TES4's.
    """
    index = unpack(rec, 'INDX', '<i')
    if index is None:
        return []
    lines = [f'EditorID={effect_editor_id(index[0])}',
             f'MorrowindEffectIndex={index[0]}',
             f'FULL={escape_value(effect_name(index[0]))}']
    emit_str(lines, 'DESC', rec, 'DESC')
    emit_icon(lines, rec, 'ITEX')
    data = unpack(rec, 'MEDT', '<ifiiiifff')
    if data:
        lines.append(f'DATA.School={data[0]}')
        lines.append(f'DATA.BaseCost={data[1]}')
        lines.append(f'DATA.Flags={data[2] | _engine_flags(index[0]) | _used_ranges(ctx, index[0])}')
        lines.append(f'DATA.ProjectileSpeed={data[7]}')
        _emit_sounds(lines, rec, data[0], ctx)
    resist = _RESIST_AV.get(index[0])
    if resist is not None:
        lines.append(f'DATA.ResistValue={resist}')
    for key, sig in _VISUALS:
        emit_str(lines, key, rec, sig)
    _emit_bound_item(lines, index[0], ctx)
    return lines


def _emit_bound_item(lines: list, index: int, ctx) -> None:
    """The item a bound effect conjures: TES3 names it in a GMST, not on the effect.

    See: docs/commentary/tes4_export_morrowind.md#bound-items
    """
    setting = _BOUND_ITEMS.get(index)
    settings = getattr(ctx, 'game_settings', None) or {}
    item = settings.get(setting[0], '') if setting else ''
    form_id = ctx.resolve(item, setting[1]) if item else ''
    if form_id:
        lines.append(f'DATA.AssocItem={form_id}')


def game_settings(records) -> dict:
    """{GMST name, lowercase: its string value} over `records`; a later record wins."""
    out = {}
    for rec in records:
        sub = get_subrecord(rec, 'STRV') if rec.type == 'GMST' and not rec.deleted else None
        if sub is not None:
            out[rec.record_id.lower()] = get_string(sub)
    return out


def _emit_sounds(lines: list, rec: Tes3Record, school: int, ctx) -> None:
    """The effect's four sounds; an unset one is its school's default, which the engine plays.

    See: docs/commentary/tes4_export_morrowind.md#magic
    """
    for key, sig, suffix in _SOUNDS:
        sub = get_subrecord(rec, sig)
        sound = get_string(sub) if sub else ''
        if not sound and 0 <= school < len(_SCHOOL_SOUNDS):
            sound = f'{_SCHOOL_SOUNDS[school]} {suffix}'
        form_id = ctx.resolve(sound, 'SOUN') if ctx else ''
        if form_id:
            lines.append(f'{key}={form_id}')


def _engine_flags(index: int) -> int:
    """The flags the engine adds at load, less the ranges spells decide.

    See: docs/commentary/tes4_export_morrowind.md#engine-flags
    """
    known = 0 <= index < len(MW_HARDCODED_FLAGS)
    return MW_HARDCODED_FLAGS[index] & ~_CAST_RANGES if known else 0


def _used_ranges(ctx, index: int) -> int:
    """The range flags the plugin's own spells cast this effect at."""
    ranges = getattr(ctx, 'effect_ranges', None) if ctx else None
    return ranges.get(index, 0) if ranges else 0


def export_SPEL(rec: Tes3Record, ctx) -> list:
    """A spell, ability, disease, blight or power, with its effects."""
    data = unpack(rec, 'SPDT', '<iiI')
    if data is None:
        return []
    spell_type, cost, flags = data
    lines = [f'EditorID={escape_value(rec.record_id)}']
    emit_str(lines, 'FULL', rec, 'FNAM')
    lines.append(f'SPIT.Type={_SPELL_TYPES.get(spell_type, 0)}')
    lines.append(f'SPIT.Cost={cost}')
    lines.append(f'SPIT.Flags={_spell_flags(flags)}')
    emit_effects(lines, rec)
    return lines


def _spell_flags(flags: int) -> int:
    """TES3 SPDT flags as TES4 SPIT flags.

    See: docs/commentary/tes4_export_morrowind.md#spell-flags
    """
    return _SPIT_MANUAL_COST | (_SPIT_PLAYER_START if flags & _SPDT_PC_START else 0)


def export_ENCH(rec: Tes3Record, ctx) -> list:
    """An enchantment, with its charge and the effects it applies."""
    data = unpack(rec, 'ENDT', '<iiii')
    if data is None:
        return []
    enchant_type, cost, charge, autocalc = data
    lines = [f'EditorID={escape_value(rec.record_id)}',
             f'ENIT.Type={_ENCHANT_TYPES.get(enchant_type, 2)}',
             f'ENIT.Cost={cost}',
             f'ENIT.Charge={charge}',
             f'ENIT.Flags={int(not autocalc)}']
    emit_effects(lines, rec)
    return lines


def export_ALCH(rec: Tes3Record, ctx) -> list:
    """A potion; its icon lives in TEXT and TES4's flag bit is inverted."""
    lines = []
    emit_common(lines, rec)
    emit_icon(lines, rec, 'TEXT')
    data = unpack(rec, 'ALDT', '<fii')
    if data:
        weight, value, flags = data
        lines.append(f'ENIT.Value={value}')
        lines.append(f'ENIT.Flags={int(not flags & _ALCH_AUTOCALC)}')
        lines.append(f'DATA.Weight={weight}')
    emit_effects(lines, rec)
    return lines


def export_INGR(rec: Tes3Record, ctx) -> list:
    """An alchemy ingredient, with the four effects IRDT names."""
    lines = []
    emit_common(lines, rec)
    emit_icon(lines, rec)
    data = unpack(rec, 'IRDT', _IRDT)
    if data:
        emit_value_weight(lines, data[1], data[0])
    lines.append('ENIT.Flags=0')
    emit_ingredient_effects(lines, rec)
    return lines


def effect_ranges(records) -> dict:
    """{effect index: the TES3 MEDT range bits the plugin's spells use it at}.

    TES3 puts range on the SPELL's ENAM, not on the effect, so an MGEF that
    is only ever cast at a target carries no sign of it.  Skyrim commits each
    MGEF to one delivery, and an Aimed one with no projectile null-derefs in
    combat AI, so the ranges an effect is actually used at are collected here
    and emitted as the TES4 range flags the import side already reads.
    """
    found = {}
    for rec in records:
        if rec.type not in ('SPEL', 'ENCH', 'ALCH') or rec.deleted:
            continue
        for sub in get_all_subrecords(rec, 'ENAM'):
            if len(sub.data) != struct.calcsize(_ENAM):
                continue
            entry = struct.unpack(_ENAM, sub.data)
            bit = _RANGE_FLAGS.get(entry[3])
            if bit:
                found[entry[0]] = found.get(entry[0], 0) | bit
    return found


def authored_indices(records) -> set:
    """The effect indices the plugin's own MGEF records supply."""
    found = set()
    for rec in records:
        if rec.type != 'MGEF' or rec.deleted:
            continue
        sub = get_subrecord(rec, 'INDX')
        if sub and len(sub.data) >= 4:
            found.add(struct.unpack_from('<i', sub.data, 0)[0])
    return found


def synthesized_effects(authored: set, ranges: dict = None) -> list:
    """`(index, lines)` for every effect index no MGEF record supplies.

    Morrowind.esm authors 137 of the 143, and a plugin that overrides none at
    all still has spells naming every one, so the rest are emitted from the
    engine's own table or those spells lose their effects.
    """
    records = []
    for index in range(len(MW_EFFECT_NAMES)):
        if index in authored:
            continue
        lines = [f'EditorID={effect_editor_id(index)}',
                 f'MorrowindEffectIndex={index}',
                 f'FULL={escape_value(effect_name(index))}']
        lines.append(f'DATA.Flags={_engine_flags(index) | (ranges or {}).get(index, 0)}')
        resist = _RESIST_AV.get(index)
        if resist is not None:
            lines.append(f'DATA.ResistValue={resist}')
        records.append((index, lines))
    return records


def enchantment_id(rec: Tes3Record) -> str:
    """The enchantment a wearable or weapon names, or '' when it has none."""
    sub = get_subrecord(rec, 'ENAM')
    return get_string(sub) if sub else ''


MORROWIND_MAGIC_EXPORTERS = {
    'ALCH': export_ALCH,
    'INGR': export_INGR,
    'MGEF': export_MGEF,
    'SPEL': export_SPEL,
    'ENCH': export_ENCH,
}

"""
The rules a Morrowind bark states beyond its speaker, as TES4 conditions.

A TES3 response is filtered on the player and the world as much as on who
speaks it: the player's diseases and faction rank, the cell, a journal index,
a global, the disposition. Every rule here becomes a condition that asks the
same question of the running game, or the line is dropped -- a rule left out
does not narrow the line, it opens it to everyone.

Three kinds of answer exist:

  * the engine knows it (race, health, weather, a magic effect) -- a native
    condition, run on the player where TES3 asks about the player;
  * MorrowindRuntime owns it (a journal index, the player's cell, reputation)
    -- a GLOB minted here that the runtime keeps up to date;
  * nothing in Skyrim can answer it -- the line is dropped.

See: docs/commentary/tes4_export_morrowind.md#bark-conditions
"""

import re
import struct
from typing import NamedTuple

from ..morrowind_region import WEATHER_EDITOR_IDS
from ..record_types.common import escape_value
from ..tes3_reader import Tes3Record, get_string, get_subrecord
from .morrowind import MW_SKILL_TO_TES4
from .morrowind_actors import RACE_FORMIDS
from .morrowind_magic import effect_editor_id

#: A 24-byte TES4 CTDA: type, pad, comparison float, function, two params, pad.
_CTDA = struct.Struct('<B3xfH2xII4x')

#: CTDA type-byte flags below the operator nibble.
_OR = 0x01
_RUN_ON_TARGET = 0x02

#: The condition-line value naming the player as the run-on reference.
RUN_ON_PLAYER = 'Player'

#: TES3 comparison digit -> (TES4 operator nibble, test).
_OPERATORS = {
    '0': (0x00, lambda a, b: a == b), '1': (0x20, lambda a, b: a != b),
    '2': (0x40, lambda a, b: a > b), '3': (0x60, lambda a, b: a >= b),
    '4': (0x80, lambda a, b: a < b), '5': (0xA0, lambda a, b: a <= b),
}

#: A comparison and the one that holds exactly when it does not.
_NEGATED = {'0': '1', '1': '0', '2': '5', '3': '4', '4': '3', '5': '2'}

#: A comparison with its operands swapped, `a op b` <=> `b swapped a`.
_SWAPPED = {'0': '0', '1': '1', '2': '4', '3': '5', '4': '2', '5': '3'}

#: INFO DATA `<i i b b b b`: type, disposition, rank, gender, PC rank.
_INFO_DATA = struct.Struct('<iibbbb')

#: Where a SCVR rule's function code, comparison and variable sit.
_CODE = slice(2, 4)
_COMPARISON_AT = 4
_VARIABLE_AT = 5

#: TES4 condition functions, by the index `tes5_import` reads.
_GET_ACTOR_VALUE = 14
_GET_DISEASE = 39
_GET_CLOTHING_VALUE = 41
_GET_DETECTED = 45
_GET_ITEM_COUNT = 47
_GET_TALKED_TO_PC = 50
_GET_SCRIPT_VARIABLE = 53
_GET_FACTION_RANK_DIFFERENCE = 60
_GET_ALARMED = 61
_GET_ATTACKED = 63
_GET_IS_CLASS = 68
_GET_IS_RACE = 69
_GET_IN_FACTION = 71
_GET_IS_ID = 72
_GET_FACTION_RANK = 73
_GET_GLOBAL_VALUE = 74
_GET_DISPOSITION = 76
_GET_RANDOM_PERCENT = 77
_GET_LEVEL = 80
_GET_DEAD_COUNT = 84
_GET_CRIME_GOLD = 116
_GET_PC_IS_RACE = 130
_GET_PC_IS_SEX = 131
_SAME_FACTION_AS_PC = 133
_SAME_RACE_AS_PC = 134
_SAME_SEX_AS_PC = 135
_GET_IS_CURRENT_WEATHER = 149
_GET_PC_EXPELLED = 193
_HAS_MAGIC_EFFECT = 214
_GET_FRIEND_HIT = 288
_IS_IN_INTERIOR = 300

#: TES5 GetHealthPercentage (0..1): TES4 never used 430, so `convert_ctda` passes it through.
_GET_HEALTH_PERCENTAGE = 430

#: PlayerRef, an engine-fixed id every condition converter passes through.
_PLAYER_REF = 0x00000014

#: TES4 actor values the player-stat rules read.
_AV_HEALTH = 8
_AV_MAGICKA = 9
_AV_FATIGUE = 10
_AV_SKILL_BASE = 12

#: TES3 numbered functions (OpenMW `DialogueCondition::Function`).
_FN_HEALTH_PERCENT = 4
_FN_PC_REPUTATION = 5
_FN_PC_LEVEL = 6
_FN_PC_HEALTH_PERCENT = 7
_FN_PC_MAGICKA = 8
_FN_PC_FATIGUE = 9
_FN_FIRST_SKILL = 11
_FN_LAST_SKILL = 37
_FN_PC_GENDER = 38
_FN_PC_EXPELLED = 39
_FN_PC_COMMON_DISEASE = 40
_FN_PC_BLIGHT_DISEASE = 41
_FN_PC_CLOTHING = 42
_FN_PC_CRIME_LEVEL = 43
_FN_SAME_SEX = 44
_FN_SAME_RACE = 45
_FN_SAME_FACTION = 46
_FN_RANK_DIFFERENCE = 47
_FN_DETECTED = 48
_FN_ALARMED = 49
_FN_PC_CORPRUS = 58
_FN_WEATHER = 59
_FN_PC_VAMPIRE = 60
_FN_LEVEL = 61
_FN_ATTACKED = 62
_FN_TALKED_TO_PC = 63
_FN_PC_HEALTH = 64
_FN_FRIEND_HIT = 66

#: TES3 effect indices behind PcCorprus and PcVampire.
_EFFECT_CORPRUS = 132
_EFFECT_VAMPIRISM = 133

#: FriendHit counts to this and stops (OpenMW `hits > 4 ? 4 : hits`).
_FRIEND_HIT_CAP = 4

#: Rules a parameterless native answers: code -> (TES4 function, run on the player).
_NATIVE = {
    _FN_PC_LEVEL: (_GET_LEVEL, True),
    _FN_LEVEL: (_GET_LEVEL, False),
    _FN_TALKED_TO_PC: (_GET_TALKED_TO_PC, False),
    _FN_SAME_SEX: (_SAME_SEX_AS_PC, False),
    _FN_SAME_RACE: (_SAME_RACE_AS_PC, False),
    _FN_SAME_FACTION: (_SAME_FACTION_AS_PC, False),
    _FN_PC_CRIME_LEVEL: (_GET_CRIME_GOLD, False),
    _FN_PC_COMMON_DISEASE: (_GET_DISEASE, True),
    _FN_PC_BLIGHT_DISEASE: (_GET_DISEASE, True),
    _FN_ALARMED: (_GET_ALARMED, False),
    _FN_ATTACKED: (_GET_ATTACKED, False),
}

#: PcHealth/PcMagicka/PcFatigue: the player's current value of one stat.
_PLAYER_STATS = {_FN_PC_HEALTH: _AV_HEALTH, _FN_PC_MAGICKA: _AV_MAGICKA,
                 _FN_PC_FATIGUE: _AV_FATIGUE}

#: `PCRace`'s values, as vanilla's `RaceCheck` script sets them: 1..10.
_PC_RACES = ('argonian', 'breton', 'dark elf', 'high elf', 'imperial',
             'khajiit', 'nord', 'orc', 'redguard', 'wood elf')

#: Globals whose meaning the engine answers directly.
_PC_RACE_GLOBAL = 'pcrace'
_RANDOM_GLOBAL = 'random100'

#: TES3 clock global -> the Skyrim.esm GLOB it reads, the runtime's `kClock` table verbatim.
_CLOCK_GLOBALS = {'gamehour': 0x38, 'day': 0x37, 'month': 0x36, 'year': 0x35,
                  'dayspassed': 0x39, 'timescale': 0x3A}

#: The runtime state a minted GLOB mirrors, keyed `<kind>:<id>`.
STATE_JOURNAL = 'journal'
STATE_CELL = 'cell'
STATE_REPUTATION = 'reputation'
STATE_WEATHER = 'weather'

#: EditorIDs are plain identifiers; journal ids and cell names are free text.
_NOT_IDENTIFIER = re.compile(r'[^A-Za-z0-9]')


# ---------------------------------------------------------------------------
# Building conditions
# ---------------------------------------------------------------------------

class Cond(NamedTuple):
    """One emitted condition: its raw CTDA and the keys that ride beside it."""

    raw: str
    run_on: str = ''
    variable: str = ''


def ctda(function: int, param1: int = 0, value: float = 1.0,
         operator: int = 0x00, param2: int = 0, flags: int = 0) -> str:
    """One TES4 condition as the hex blob `Condition[i].Raw` carries."""
    return _CTDA.pack(operator | flags, value, function, param1,
                      param2).hex()


def _cond(function: int, digit: str, value: float, param1: int = 0,
          player: bool = False, param2: int = 0) -> Cond:
    """A condition comparing `function` against `value` by a TES3 digit."""
    raw = ctda(function, param1, value, _OPERATORS[digit][0], param2,
               _RUN_ON_TARGET if player else 0)
    return Cond(raw, RUN_ON_PLAYER if player else '')


def _is(function: int, param1: int = 0, player: bool = False,
        truth: bool = True) -> Cond:
    """`function(param1) == 1`, or `== 0` when not `truth`."""
    return _cond(function, '0', 1.0 if truth else 0.0, param1, player)


def _holds(digit: str, lhs: float, rhs: float) -> bool:
    """Whether `lhs <digit> rhs` holds."""
    return _OPERATORS[digit][1](lhs, rhs)


def _constant(truth: bool) -> 'list | None':
    """A rule whose answer is known now: nothing to emit, or never passes."""
    return [] if truth else None


def _fid(value: str) -> int:
    """A resolved FormID as an int; 0 when nothing resolved."""
    return int(value, 16) if value else 0


def _one_of(function: int, params: list, player: bool) -> list:
    """`function(p) == 1` for ANY of `params`: an OR group."""
    last = len(params) - 1
    return [Cond(ctda(function, p, 1.0, 0x00, 0,
                      (_RUN_ON_TARGET if player else 0)
                      | (_OR if i < last else 0)),
                 RUN_ON_PLAYER if player else '')
            for i, p in enumerate(params)]


def _members(domain, digit: str, value: float, params: dict, function: int,
             player: bool) -> 'list | None':
    """A rule over a small closed domain, as membership in the values it
    accepts: an OR of `== 1` when few pass, an AND of `== 0` when few fail.

    `params` maps each domain value to the form the function names for it.
    """
    passing = [params[v] for v in domain if _holds(digit, v, value)]
    failing = [params[v] for v in domain if not _holds(digit, v, value)]
    if not failing:
        return []
    if not passing:
        return None
    if len(passing) <= len(failing):
        return _one_of(function, passing, player)
    return [_is(function, p, player, False) for p in failing]


# ---------------------------------------------------------------------------
# Runtime-owned state
# ---------------------------------------------------------------------------

def state_fid(ctx, key: str) -> int:
    """The GLOB the runtime keeps `key` in, minted on first use.

    See: docs/commentary/morrowind_runtime.md#published-state
    """
    found = ctx.bark_states.get(key)
    if found is None:
        found = ctx.bark_states[key] = ctx.derive(f'barkstate:{key}')
    return _fid(found)


def _state(ctx, key: str, digit: str, value: float) -> list:
    """`key`'s runtime value compared as TES3 compares it."""
    return [_cond(_GET_GLOBAL_VALUE, digit, value, state_fid(ctx, key))]


def state_records(ctx) -> list:
    """`(FormID, lines)` per GLOB `state_fid` minted, for the GLOB output.

    `MorrowindState` names what the runtime writes into it.
    See: docs/commentary/morrowind_runtime.md#published-state
    """
    return [(fid, [f'EditorID=MWState_{_NOT_IDENTIFIER.sub("_", key)}',
                   'FNAM.Type=f', 'FLTV.Value=0',
                   f'MorrowindState={escape_value(key)}'])
            for key, fid in sorted(ctx.bark_states.items())]


# ---------------------------------------------------------------------------
# Numbered functions
# ---------------------------------------------------------------------------

def _speaker_faction(audience) -> int:
    """The FormID of the faction the bark states its speaker holds, else 0."""
    return 0 if audience.factionless else audience.fids[2]


def _magic_effect(ctx, index: int, digit: str, value: float) -> 'list | None':
    """PcCorprus/PcVampire: is that effect on the player."""
    form = _fid(ctx.resolve(effect_editor_id(index), 'MGEF'))
    if not form:
        return _constant(_holds(digit, 0, value))
    return [_cond(_HAS_MAGIC_EFFECT, digit, value, form, True)]


def _clothing(digit: str, value: float) -> 'list | None':
    """PcClothingModifier, where only naked-or-dressed carries across.

    See: docs/commentary/tes4_export_morrowind.md#bark-conditions
    """
    naked = _holds(digit, 0, value)
    dressed = _holds(digit, 1, value)
    if dressed != _holds(digit, 1 << 30, value):
        return None
    if naked == dressed:
        return _constant(naked)
    return [_cond(_GET_CLOTHING_VALUE, '5' if naked else '2', 0.0, 0, True)]


def _weather(ctx, digit: str, value: float) -> 'list | None':
    """Weather, never true indoors: the WTHR itself where Morroblivion's ten
    are loaded, else the TES3 weather the runtime reads off Skyrim's.

    See: docs/commentary/morrowind_runtime.md#published-state
    """
    forms = {i: _fid(ctx.index.lookup_editor_id(edid) or '')
             for i, edid in enumerate(WEATHER_EDITOR_IDS)}
    if all(forms.values()):
        rows = _members(range(len(WEATHER_EDITOR_IDS)), digit, value, forms,
                        _GET_IS_CURRENT_WEATHER, True)
    else:
        rows = _state(ctx, STATE_WEATHER, digit, value)
    if rows is None:
        return None
    return rows + [_is(_IS_IN_INTERIOR, player=True, truth=False)]


def _faction_rule(code: int, digit: str, value: float,
                  audience) -> 'list | None':
    """PcExpelled and FactionRankDifference, on the speaker's faction.

    See: docs/commentary/tes4_export_morrowind.md#bark-conditions
    """
    faction = _speaker_faction(audience)
    if not faction:
        return None
    if code == _FN_PC_EXPELLED:
        return [_cond(_GET_PC_EXPELLED, digit, value, faction)]
    return [_cond(_GET_FACTION_RANK_DIFFERENCE, _SWAPPED[digit], -value,
                  faction, param2=_PLAYER_REF)]


def _player_value(code: int, digit: str, value: float) -> 'list | None':
    """A player stat or skill, or a health fraction; None for an attribute,
    which Skyrim has no actor value for."""
    if code in (_FN_HEALTH_PERCENT, _FN_PC_HEALTH_PERCENT):
        return [_cond(_GET_HEALTH_PERCENTAGE, digit, value / 100.0, 0,
                      code == _FN_PC_HEALTH_PERCENT)]
    if code in _PLAYER_STATS:
        return [_cond(_GET_ACTOR_VALUE, digit, value, _PLAYER_STATS[code],
                      True)]
    if _FN_FIRST_SKILL <= code <= _FN_LAST_SKILL:
        skill = MW_SKILL_TO_TES4[code - _FN_FIRST_SKILL] + _AV_SKILL_BASE
        return [_cond(_GET_ACTOR_VALUE, digit, value, skill, True)]
    return None


def _function_rule(code: int, digit: str, value: float, audience,
                   ctx) -> 'list | None':
    """One numbered TES3 function, or None when Skyrim cannot ask it."""
    if code in _NATIVE:
        function, player = _NATIVE[code]
        return [_cond(function, digit, value, 0, player)]
    if code == _FN_PC_GENDER:
        return _members((0, 1), digit, value, {0: 0, 1: 1}, _GET_PC_IS_SEX,
                        False)
    if code in (_FN_PC_EXPELLED, _FN_RANK_DIFFERENCE):
        return _faction_rule(code, digit, value, audience)
    if code == _FN_PC_CLOTHING:
        return _clothing(digit, value)
    if code in (_FN_PC_CORPRUS, _FN_PC_VAMPIRE):
        effect = (_EFFECT_CORPRUS if code == _FN_PC_CORPRUS
                  else _EFFECT_VAMPIRISM)
        return _magic_effect(ctx, effect, digit, value)
    if code == _FN_WEATHER:
        return _weather(ctx, digit, value)
    if code == _FN_FRIEND_HIT:
        if value >= _FRIEND_HIT_CAP and digit == '0':
            digit = '3'
        return [_cond(_GET_FRIEND_HIT, digit, value, _PLAYER_REF)]
    if code == _FN_DETECTED:
        return [_cond(_GET_DETECTED, digit, value, _PLAYER_REF)]
    if code == _FN_PC_REPUTATION:
        return _state(ctx, STATE_REPUTATION, digit, value)
    return _player_value(code, digit, value)


# ---------------------------------------------------------------------------
# Variable rules
# ---------------------------------------------------------------------------

def _global_rule(name: str, digit: str, value: float, ctx) -> list:
    """A TES3 global. One the chain does not define is ignored, as TES3 does."""
    key = name.lower()
    if key == _RANDOM_GLOBAL:
        return [_cond(_GET_RANDOM_PERCENT, digit, value)]
    if key == _PC_RACE_GLOBAL:
        races = {i + 1: RACE_FORMIDS[race] for i, race in enumerate(_PC_RACES)}
        return _members(races, digit, value, races, _GET_PC_IS_RACE, False)
    form = _CLOCK_GLOBALS.get(key) or _fid(ctx.resolve(name, 'GLOB'))
    return [_cond(_GET_GLOBAL_VALUE, digit, value, form)] if form else []


def _speakers_of_race(race: str, ctx) -> list:
    """The FormIDs of every actor of a race no VTYP can carry."""
    out = []
    for who in ctx.bark_speakers:
        if who.race == race:
            form = _fid(ctx.resolve(who.record_id,
                                    'NPC_' if who.is_npc else 'CREA'))
            if form:
                out.append(form)
    return out


def _not_race(race: str, ctx) -> list:
    """NotRace: a vanilla race by its GetIsRace, a custom one by its actors."""
    if race in RACE_FORMIDS:
        return [_is(_GET_IS_RACE, RACE_FORMIDS[race], truth=False)]
    return [_is(_GET_IS_ID, form, truth=False)
            for form in _speakers_of_race(race, ctx)]


def _not_rule(kind: str, name: str, ctx) -> list:
    """The inverted identity rules, which never apply the comparison.

    A form nothing in the chain defines is one the speaker cannot be.
    """
    if kind == 'A':
        return _not_race(name.lower(), ctx)
    if kind == 'B':
        return _state(ctx, f'{STATE_CELL}:{name.lower()}', '0', 0.0)
    function, sig = {'7': (_GET_IS_ID, 'NPC_'), '8': (_GET_IN_FACTION, 'FACT'),
                     '9': (_GET_IS_CLASS, 'CLAS')}[kind]
    form = _fid(ctx.resolve(name, sig)) or (
        _fid(ctx.resolve(name, 'CREA')) if kind == '7' else 0)
    return [_is(function, form, truth=False)] if form else []


def _counted(function: int, form: int, digit: str, value: float,
             player: bool) -> 'list | None':
    """An item or death count; a form the chain lacks counts zero."""
    if not form:
        return _constant(_holds(digit, 0, value))
    return [_cond(function, digit, value, form, player)]


def _variable_rule(kind: str, name: str, digit: str, value: float,
                   ctx) -> 'list | None':
    """One variable-kind rule (global, local, journal, item, dead, Not*)."""
    if kind == '2':
        return _global_rule(name, digit, value, ctx)
    if kind in ('3', 'C'):
        if kind == 'C':
            digit = _NEGATED[digit]
        raw = ctda(_GET_SCRIPT_VARIABLE, 0, value, _OPERATORS[digit][0])
        return [Cond(raw, variable=name)]
    if kind == '4':
        return _state(ctx, f'{STATE_JOURNAL}:{name.lower()}', digit, value)
    if kind == '5':
        return _counted(_GET_ITEM_COUNT, _fid(ctx.resolve(name)), digit,
                        value, True)
    if kind == '6':
        form = _fid(ctx.resolve(name, 'NPC_')) or _fid(ctx.resolve(name,
                                                                   'CREA'))
        return _counted(_GET_DEAD_COUNT, form, digit, value, False)
    if kind in ('7', '8', '9', 'A', 'B'):
        return _not_rule(kind, name, ctx)
    return None


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------

def _rules(rec: Tes3Record):
    """`(rule, value)` per SCVR, the value from the INTV/FLTV after it."""
    subs = rec.subrecords
    for index, sub in enumerate(subs):
        if sub.type != 'SCVR':
            continue
        nxt = subs[index + 1] if index + 1 < len(subs) else None
        value = 0.0
        if nxt is not None and nxt.type in ('INTV', 'FLTV') and nxt.data[:4]:
            value = float(struct.unpack(
                '<i' if nxt.type == 'INTV' else '<f', nxt.data[:4])[0])
        yield get_string(sub), value


def _rule_conditions(rule: str, value: float, audience,
                     ctx) -> 'list | None':
    """One SCVR rule as conditions; None drops the line."""
    if len(rule) <= _COMPARISON_AT or rule[_COMPARISON_AT] not in _OPERATORS:
        return None
    digit = rule[_COMPARISON_AT]
    if rule[1] == '1':
        return _function_rule(int(rule[_CODE]), digit, value, audience, ctx)
    return _variable_rule(rule[1], rule[_VARIABLE_AT:], digit, value, ctx)


def _player_faction(rec: Tes3Record, pc_rank: int, audience,
                    ctx) -> 'list | None':
    """DNAM and the PC rank: the player's rank in that faction, else in the
    speaker's, at least the stated rank."""
    dnam = get_subrecord(rec, 'DNAM')
    if dnam is None and pc_rank < 0:
        return []
    faction = (_fid(ctx.resolve(get_string(dnam), 'FACT')) if dnam is not None
               else _speaker_faction(audience))
    if not faction:
        return None
    return [_cond(_GET_FACTION_RANK, '3', float(max(pc_rank, 0)), faction,
                  True)]


def _field_conditions(rec: Tes3Record, audience, ctx) -> 'list | None':
    """The player-faction, cell and disposition filters DATA/DNAM/ANAM state."""
    data = get_subrecord(rec, 'DATA')
    disposition, pc_rank = 0, -1
    if data is not None and len(data.data) >= _INFO_DATA.size:
        _kind, disposition, _rank, _gender, pc_rank, _pad = (
            _INFO_DATA.unpack_from(data.data, 0))
    out = _player_faction(rec, pc_rank, audience, ctx)
    if out is None:
        return None
    cell = get_subrecord(rec, 'ANAM')
    if cell is not None:
        out += _state(ctx, f'{STATE_CELL}:{get_string(cell).lower()}', '0',
                      1.0)
    if disposition:
        out.append(_cond(_GET_DISPOSITION, '3', float(disposition)))
    return out


def bark_conditions(rec: Tes3Record, audience, ctx) -> 'list | None':
    """Every non-identity filter one bark states, as `Cond`s; None when one
    of them has no equivalent and the line must go.

    See: docs/commentary/tes4_export_morrowind.md#bark-conditions
    """
    out = _field_conditions(rec, audience, ctx)
    if out is None:
        return None
    for rule, value in _rules(rec):
        found = _rule_conditions(rule, value, audience, ctx)
        if found is None:
            return None
        out.extend(found)
    return out

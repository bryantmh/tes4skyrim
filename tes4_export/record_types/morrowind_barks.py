"""
Morrowind's voiced barks as TES4 DIAL/INFO, so the TES5 pipeline converts them.

Every voiced INFO in Morrowind is topic type `Voice` -- hello, attack, hit,
idle, flee, thief, intruder, alarm -- and each names its audio file outright in
`SNAM`. These are the only lines Morrowind voices at all, and the only ones that
must reach Skyrim as REAL dialogue rather than through the runtime's own
channel: the engine moves an actor's mouth only for a voice line it plays
itself, from a `.fuz` carrying a lip track.

So the barks alone leave the sidecar and take the ordinary TES4 road, where
`_EDID_SUBTYPE` already routes each channel to its Skyrim bark subtype and
`build_info_gates` already gates each line on the speaker's voice type. The
conversational topics stay where they are, interpreted by MorrowindRuntime.

Who may speak a line is resolved in `morrowind_audience` from the record's own
filters, never from the folder its recording sits in.

See: docs/commentary/tes4_export_morrowind.md#voiced-barks
"""

import struct

from ..record_types.common import escape_value
from ..tes3_reader import Tes3Record, get_string, get_subrecord
from .morrowind_actors import RACE_FORMIDS
from .morrowind_audience import Audience, resolve

#: TES3 `Voice` topic id -> the TES4 topic EditorID `_EDID_SUBTYPE` keys on.
BARK_TOPICS = {
    'hello': 'HELLO', 'attack': 'Attack', 'hit': 'Hit', 'idle': 'Idle',
    'flee': 'Flee', 'thief': 'Steal', 'intruder': 'Trespass',
    'alarm': 'Assault',
}

#: TES4 DIAL.DATA type for a bark channel; the subtype comes from the EditorID.
_DIAL_TYPE_CONVERSATION = 0

#: TES4 condition function indices, as `tes5_import/base/conditions.py` numbers them.
_FUNC_GET_IS_SEX = 70
_FUNC_GET_IN_FACTION = 71
_FUNC_GET_IS_ID = 72
_FUNC_GET_IS_CLASS = 68
_FUNC_GET_FACTION_RANK = 73

#: CTDA comparison `>=`, for a rank threshold; the high nibble of the type byte.
_OP_AT_LEAST = 0x60

#: CTDA comparison operator `==`, in the high nibble of the type byte.
_OP_EQUAL = 0x00

#: A 24-byte TES4 CTDA: type, pad, comparison float, function, two params, pad.
_CTDA = struct.Struct('<B3xfH2xII4x')

#: Most conditions one INFO may carry; past this the engine drops the line.
_MAX_CONDITIONS = 22

#: TES4 `GetScriptVariable`; the importer turns it into GetVMScriptVariable.
_FUNC_GET_SCRIPT_VARIABLE = 53

#: SCVR rule kinds whose variable names a SCRIPT LOCAL: local, not-local.
_LOCAL_VAR_FUNCS = ('3', 'C')

#: Where a SCVR rule's comparison byte and variable name start.
_RULE_COMPARISON_AT = 4
_RULE_VAR_AT = 5

#: TES3 SCVR comparison digit -> the TES4 CTDA operator nibble.
_SCVR_OPERATORS = {'0': 0x00, '1': 0x20, '2': 0x40, '3': 0x60, '4': 0x80,
                   '5': 0xA0}


def is_bark(rec: Tes3Record, topic: str) -> bool:
    """True when this INFO is a voiced bark of a `Voice` topic."""
    return (topic.lower() in BARK_TOPICS
            and get_subrecord(rec, 'SNAM') is not None)


def ctda(function: int, param1: int, value: float = 1.0,
          operator: int = _OP_EQUAL) -> str:
    """One TES4 condition as the hex blob `Condition[i].Raw` carries."""
    return _CTDA.pack(operator, value, function, param1, 0).hex()


def bark_dial_id(edid: str, ctx) -> str:
    """The FormID of one bark channel's DIAL, minted from its authored name."""
    return ctx.derive(f'barkdial:{edid.lower()}')


def bark_topics(topics, ctx) -> list:
    """`(FormID, lines)` for each bark DIAL a plugin's barks need.

    One DIAL per channel, named so `_EDID_SUBTYPE` routes it: the TES5 side
    reads the EditorID, never the type byte.
    """
    out = []
    for topic in sorted(topics):
        edid = BARK_TOPICS[topic.lower()]
        out.append((bark_dial_id(edid, ctx),
                    [f'EditorID={edid}',
                     f'FULL={escape_value(edid)}',
                     f'DATA.Type={_DIAL_TYPE_CONVERSATION}']))
    return out


def _speaker_ids(audience: Audience, ctx) -> list:
    """`GetIsID` over the actors that really satisfy this bark's filters.

    The whole speaker set is named, never a truncated prefix of it: a chain cut
    short silently drops the actors past the cut, which is the bleed this pass
    exists to prevent. Measured largest such audience: 14 actors.
    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    out = []
    for who in resolve(audience, ctx.bark_speakers, ctx.bark_audiences):
        form_id = ctx.resolve(who.record_id, 'NPC_' if who.is_npc else 'CREA')
        if form_id:
            out.append(ctda(_FUNC_GET_IS_ID, int(form_id, 16)))
    return out


def _needs_speaker_ids(audience: Audience) -> bool:
    """Whether only naming the actors can express this bark's audience.

    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    if audience.actor:
        return True
    if audience.rank > 0 and not audience.faction:
        return True
    return bool(audience.race) and audience.race not in RACE_FORMIDS


def _factionless_conditions(audience: Audience, ctx) -> list:
    """`GetInFaction == 0` per faction this bark's speakers could hold.

    Emitted only while the list fits `_MAX_CONDITIONS`; past that the race
    voice type carries the line alone rather than the engine dropping it.
    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    factions = sorted({who.faction
                       for who in ctx.bark_speakers
                       if who.is_npc and who.faction
                       and (not audience.race or audience.race == who.race)})
    out = []
    for faction in factions:
        form_id = ctx.resolve(faction, 'FACT')
        if form_id:
            out.append(ctda(_FUNC_GET_IN_FACTION, int(form_id, 16), 0.0))
    return out if len(out) <= _MAX_CONDITIONS else []


def _voiceable_race(audience: Audience) -> 'int | None':
    """The vanilla race FormID the importer can turn into a VTYP, or None.

    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    if _needs_speaker_ids(audience):
        return None
    return RACE_FORMIDS.get(audience.race)


def _script_local_conditions(rec: Tes3Record) -> list:
    """`(raw, variable name)` for each script local this bark tests.

    A local is RUNTIME STATE, never identity: 1,553 Tamriel Rebuilt scripts
    across 21 races declare `T_Local_NPC`, and the companion script SETS
    `T_Local_Khajiit` as it runs. So it travels as a condition, which is what
    keeps an abomination's grunt on abominations.
    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    out = []
    subs = rec.subrecords
    for index, sub in enumerate(subs):
        if sub.type != 'SCVR':
            continue
        rule = get_string(sub)
        if len(rule) <= _RULE_VAR_AT or rule[1] not in _LOCAL_VAR_FUNCS:
            continue
        nxt = subs[index + 1] if index + 1 < len(subs) else None
        value = 0.0
        if nxt is not None and nxt.type in ('INTV', 'FLTV') and nxt.data[:4]:
            value = float(struct.unpack('<i' if nxt.type == 'INTV' else '<f',
                                        nxt.data[:4])[0])
        operator = _SCVR_OPERATORS.get(rule[_RULE_COMPARISON_AT], _OP_EQUAL)
        out.append((ctda(_FUNC_GET_SCRIPT_VARIABLE, 0, value, operator),
                    rule[_RULE_VAR_AT:]))
    return out


def _membership_conditions(audience: Audience, ctx) -> 'list | None':
    """The class and faction tests a bark states; None when one names no form.

    See: docs/commentary/tes4_export_morrowind.md#an-audience-that-cannot-be-named
    """
    out = []
    if audience.clazz:
        form_id = ctx.resolve(audience.clazz, 'CLAS')
        if not form_id:
            return None
        out.append(ctda(_FUNC_GET_IS_CLASS, int(form_id, 16)))
    if audience.faction:
        form_id = ctx.resolve(audience.faction, 'FACT')
        if not form_id:
            return None
        out.append(ctda(_FUNC_GET_IN_FACTION, int(form_id, 16)))
        if audience.rank > 0:
            out.append(ctda(_FUNC_GET_FACTION_RANK, int(form_id, 16),
                             float(audience.rank), _OP_AT_LEAST))
    return out


def _audience_lines(audience: Audience, ctx) -> 'tuple | None':
    """`(lines, conditions)` gating one bark on the speakers it names.

    A vanilla race becomes a plugin-owned VTYP, which cannot bleed across
    plugins; every other stated audience -- a custom race, a class, a faction,
    one named actor -- becomes conditions naming the real speakers. A bark
    stating no identity is left open, because the record leaves it open, and
    one whose stated identity names no form is None: it has no speaker here.
    See: docs/commentary/tes4_export_morrowind.md#an-audience-that-cannot-be-named
    """
    lines, conditions = [], []
    race_fid = _voiceable_race(audience)
    if race_fid is not None:
        lines.append(f'BarkRace={race_fid:08X}')
    if _needs_speaker_ids(audience):
        conditions.extend(_speaker_ids(audience, ctx))
        if not conditions:
            return None
    if audience.factionless:
        conditions.extend(_factionless_conditions(audience, ctx))
    membership = _membership_conditions(audience, ctx)
    if membership is None:
        return None
    conditions.extend(membership)
    if audience.gender in (0, 1):
        lines.append(f'BarkSex={audience.gender}')
        conditions.append(ctda(_FUNC_GET_IS_SEX, audience.gender))
    return lines, conditions


def export_bark(rec: Tes3Record, ctx, topic: str,
                info_id: str) -> 'list | None':
    """One voiced bark as a TES4 INFO, or None when nobody here can speak it."""
    audience = Audience(rec).bind(ctx, RACE_FORMIDS)
    gated = _audience_lines(audience, ctx)
    if gated is None:
        return None
    gate, conditions = gated
    edid = BARK_TOPICS[topic.lower()]
    lines = [f'ParentDIAL={bark_dial_id(edid, ctx)}',
             'DATA.DialogType=0', 'DATA.NextSpeaker=0', 'DATA.Flags=0',
             f'MorrowindInfo={escape_value(info_id)}']
    voice = get_subrecord(rec, 'SNAM')
    if voice is not None:
        path = get_string(voice).replace('/', chr(92))
        lines.append(f'MorrowindVoice={escape_value(path)}')
    response = get_subrecord(rec, 'NAME')
    lines.extend(['ResponseCount=1',
                  'Response[0].EmotionType=0',
                  'Response[0].EmotionValue=50',
                  'Response[0].ResponseNumber=1',
                  'Response[0].ResponseText='
                  + escape_value(get_string(response) if response else '')])
    lines.extend(gate)
    named = [(raw, '') for raw in conditions]
    named.extend(_script_local_conditions(rec))
    for index, (raw, variable) in enumerate(named):
        lines.append(f'Condition[{index}].Raw={raw}')
        if variable:
            lines.append(f'Condition[{index}].Variable='
                         + escape_value(variable))
    if named:
        lines.append(f'ConditionCount={len(named)}')
    return lines

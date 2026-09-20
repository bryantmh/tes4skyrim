"""
Morrowind dialogue: DIAL topics and their INFO responses, dumped verbatim.

Unlike every other Morrowind exporter these lines are NOT the TES4 vocabulary:
nothing downstream converts them into TES5 DIAL/INFO. They are read by
MorrowindRuntime.dll, which runs OpenMW's own filter over them, so the dump
keeps TES3's own field names, its string IDs and its SCVR condition encoding
rather than translating any of it.

See: docs/reference/morrowind_dialogue_format.md#info
"""

import struct

from ..record_types.common import escape_value
from ..tes3_reader import Tes3Record, get_string, get_subrecord
from .morrowind_audience import index_speakers, master_speakers
from .morrowind_barks import bark_topics, export_bark, is_bark
from .morrowind_say import say_lines

#: DIAL.DATA type byte -> the name the runtime filters on.
DIAL_TYPES = {0: 'Topic', 1: 'Voice', 2: 'Greeting', 3: 'Persuasion',
              4: 'Journal'}

#: INFO.DATA is `<i i b b b b`: type, disposition/journal index, ranks, gender.
_DATA = struct.Struct('<iibbbb')

#: FNAM holding this means "only when the speaker has NO faction".
_FACTIONLESS = 'FFFF'

#: Subrecord -> export key for the plain string references on an INFO.
_STRING_FIELDS = (('ONAM', 'Actor'), ('RNAM', 'Race'), ('CNAM', 'Class'),
                  ('FNAM', 'Faction'), ('ANAM', 'Cell'), ('DNAM', 'PCFaction'),
                  ('SNAM', 'Voice'))

#: Quest-status subrecord -> its meaning. Tribunal added these; vanilla has none.
_QUEST_FLAGS = (('QSTN', 'Name'), ('QSTF', 'Finished'), ('QSTR', 'Restart'))

#: Export signatures. NOT DIAL/INFO: see dialogue_records.
DIAL_SIG = 'MWDI'
INFO_SIG = 'MWIN'

#: Shortest legal SCVR rule: index, function, var type, 'X', comparison.
_RULE_MIN = 5

#: Rule kind meaning "a numbered function", whose index is rule[2:4].
_NUMBERED = '1'


def _text(sub) -> str:
    """A TES3 string subrecord decoded, or '' when absent."""
    return get_string(sub) if sub is not None else ''


def export_DIAL(rec: Tes3Record) -> list:
    """A dialogue topic: its id and which of the five kinds it is."""
    data = get_subrecord(rec, 'DATA')
    kind = data.data[0] if data is not None and data.data else 0
    return [f'EditorID={escape_value(rec.record_id)}',
            f'DialType={DIAL_TYPES.get(kind, kind)}']


def _value_lines(sub, prefix: str) -> list:
    """The INTV/FLTV a rule compares against, with its type tag preserved."""
    if sub is None or sub.type not in ('INTV', 'FLTV') or len(sub.data) < 4:
        return []
    if sub.type == 'INTV':
        value = struct.unpack('<i', sub.data[:4])[0]
        return [f'{prefix}.ValueType=Int', f'{prefix}.Value={value}']
    value = struct.unpack('<f', sub.data[:4])[0]
    return [f'{prefix}.ValueType=Float', f'{prefix}.Value={value!r}']


def _condition_lines(subs: list, index: int, prefix: str) -> list:
    """One SCVR rule plus the value subrecord that follows it.

    Rule kind `1` is a NUMBERED function whose two-digit index occupies the
    chars a variable rule uses for its type, so the kinds decode apart.
    See: docs/reference/morrowind_dialogue_format.md#conditions
    """
    rule = _text(subs[index])
    if len(rule) < _RULE_MIN:
        return []
    lines = [f'{prefix}.Rule={escape_value(rule)}',
             f'{prefix}.Index={rule[0]}',
             f'{prefix}.Function={rule[1]}',
             f'{prefix}.Comparison={rule[4]}']
    if rule[1] == _NUMBERED:
        lines.append(f'{prefix}.FunctionIndex={rule[2:4]}')
    else:
        lines.append(f'{prefix}.VarType={rule[2]}')
        lines.append(f'{prefix}.Variable={escape_value(rule[_RULE_MIN:])}')
    nxt = subs[index + 1] if index + 1 < len(subs) else None
    lines.extend(_value_lines(nxt, prefix))
    return lines


def _data_lines(sub) -> list:
    """The 12-byte DATA struct: kind, disposition/journal index, ranks, gender."""
    if sub is None or len(sub.data) < _DATA.size:
        return []
    kind, disp, rank, gender, pcrank, _pad = _DATA.unpack_from(sub.data, 0)
    return [f'InfoType={DIAL_TYPES.get(kind, kind)}',
            f'Disposition={disp}',
            f'JournalIndex={disp}',
            f'Rank={rank}',
            f'Gender={gender}',
            f'PCRank={pcrank}']


def _filter_lines(rec: Tes3Record) -> list:
    """The string-id filters, with FNAM's faction-less sentinel split out."""
    lines = []
    for sig, key in _STRING_FIELDS:
        sub = get_subrecord(rec, sig)
        if sub is None:
            continue
        value = _text(sub)
        if sig == 'FNAM' and value == _FACTIONLESS:
            lines.append('FactionLess=1')
            continue
        lines.append(f'{key}={escape_value(value)}')
    return lines


def _conditions(rec: Tes3Record) -> list:
    """Every SCVR on this INFO, numbered, with a trailing count."""
    lines = []
    count = 0
    for i, sub in enumerate(rec.subrecords):
        if sub.type != 'SCVR':
            continue
        emitted = _condition_lines(rec.subrecords, i, f'Condition[{count}]')
        if emitted:
            lines.extend(emitted)
            count += 1
    if count:
        lines.append(f'ConditionCount={count}')
    return lines


def info_id(rec: Tes3Record) -> str:
    """An INFO's own identity, INAM -- NOT `record_id`, which holds NAME."""
    return _text(get_subrecord(rec, 'INAM'))


def export_INFO(rec: Tes3Record, ordinal: int, topic: str) -> list:
    """One response: its place in the topic's list, its filters and its result.

    `ordinal` is the authority for filter precedence; PNAM/NNAM chain the list,
    but a reader walking that chain would have to trust every link.
    See: docs/reference/morrowind_dialogue_format.md#ordinal
    """
    lines = [f'EditorID={escape_value(info_id(rec))}',
             f'Topic={escape_value(topic)}',
             f'Ordinal={ordinal}']
    lines.extend(_data_lines(get_subrecord(rec, 'DATA')))
    lines.extend(_filter_lines(rec))
    response = get_subrecord(rec, 'NAME')
    if response is not None:
        lines.append(f'Response={escape_value(_text(response))}')
    script = get_subrecord(rec, 'BNAM')
    if script is not None:
        lines.append(f'ResultScript={escape_value(_text(script))}')
    for sig, meaning in _QUEST_FLAGS:
        if get_subrecord(rec, sig) is not None:
            lines.append(f'QuestStatus={meaning}')
    lines.extend(_conditions(rec))
    return lines


def dialogue_records(records: list, ctx=None, say: bool = True) -> dict:
    """{'MWDI': [...], 'MWIN': [...], 'DIAL': [...], 'INFO': [...]}, in file order.

    Walks the file itself: every INFO belongs to the last DIAL seen. With a
    `ctx` a VOICED bark is also emitted as TES4 DIAL/INFO, the only road that
    gives it a lip track, and `say` adds the scripted `Say` lines, which only
    a plugin whose scripts the runtime RUNS has any use for.
    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    out = {DIAL_SIG: [], INFO_SIG: [], 'DIAL': [], 'INFO': []}
    if ctx is not None:
        ctx.bark_speakers = (index_speakers(records)
                             + master_speakers(ctx.master_dirs))
        ctx.bark_audiences = {}
    topic = ''
    ordinal = 0
    voiced = set()
    for rec in records:
        if rec.type == 'DIAL':
            if not rec.deleted:
                out[DIAL_SIG].append((rec.record_id, export_DIAL(rec)))
            topic = rec.record_id
            ordinal = 0
            continue
        if rec.type != 'INFO' or rec.deleted or not topic:
            continue
        if ctx is not None and is_bark(rec, topic):
            own = info_id(rec)
            bark = export_bark(rec, ctx, topic, own)
            if bark is None:
                ctx.unresolved['bark audience'] += 1
            else:
                voiced.add(topic.lower())
                out['INFO'].append(
                    (ctx.derive(f'barkinfo:{topic.lower()}:{own.lower()}'),
                     bark))
        out[INFO_SIG].append(
            (info_id(rec), export_INFO(rec, ordinal, topic)))
        ordinal += 1
    out['DIAL'] = bark_topics(voiced, ctx) if ctx is not None else []
    if ctx is not None and say:
        for sig, rows in say_lines(records, ctx).items():
            out.setdefault(sig, []).extend(rows)
    return out

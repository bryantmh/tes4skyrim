"""
TES3 DIAL/INFO export: identity, ordering and condition fidelity.

Every test builds its records by hand, so none needs Morrowind installed.
The format is recorded in docs/reference/morrowind_dialogue_format.md.
"""

import struct

import pytest

from tes4_export.record_types.morrowind_dialog import (DIAL_SIG, DIAL_TYPES,
                                                       INFO_SIG,
                                                       dialogue_records,
                                                       export_DIAL,
                                                       export_INFO, info_id)
from tes4_export.tes3_reader import Tes3Record
from tes4_export.tes4_reader import Subrecord


def _sub(sig: str, data: bytes) -> Subrecord:
    """One subrecord."""
    return Subrecord(type=sig, data=data)


def _cstr(text: str) -> bytes:
    """A cp1252 zero-terminated TES3 string."""
    return text.encode('cp1252') + b'\x00'


def _info(**kw) -> Tes3Record:
    """An INFO carrying only the subrecords a test names."""
    data = struct.pack('<iibbbb', kw.get('kind', 0), kw.get('disp', 0),
                       kw.get('rank', -1), kw.get('gender', -1),
                       kw.get('pcrank', -1), 0)
    subs = [_sub('INAM', _cstr(kw.get('inam', 'id1'))),
            _sub('PNAM', _cstr('')), _sub('NNAM', _cstr('')),
            _sub('DATA', data)]
    if 'response' in kw:
        subs.append(_sub('NAME', _cstr(kw['response'])))
    if 'faction' in kw:
        subs.append(_sub('FNAM', _cstr(kw['faction'])))
    if 'script' in kw:
        subs.append(_sub('BNAM', _cstr(kw['script'])))
    for sig in kw.get('quest', ()):
        subs.append(_sub(sig, b''))
    for rule, vsig, value in kw.get('conditions', ()):
        subs.append(_sub('SCVR', _cstr(rule)))
        subs.append(_sub(vsig, struct.pack('<i', value) if vsig == 'INTV'
                         else struct.pack('<f', value)))
    rec = Tes3Record(type='INFO', flags=0, subrecords=subs)
    rec.record_id = kw.get('response', '')
    return rec


def _dial(name: str, kind: int) -> Tes3Record:
    """A topic of one of the five kinds."""
    rec = Tes3Record(type='DIAL', flags=0,
                     subrecords=[_sub('NAME', _cstr(name)),
                                 _sub('DATA', bytes([kind]))])
    rec.record_id = name
    return rec


def _kv(lines: list) -> dict:
    """Exported KEY=VALUE lines as a dict."""
    return dict(line.split('=', 1) for line in lines if '=' in line)


def test_info_id_is_inam_not_response():
    """The identity is INAM; record_id holds NAME, which is the response."""
    rec = _info(inam='12345', response='Some long spoken paragraph.')
    assert info_id(rec) == '12345'
    assert rec.record_id != '12345'
    assert _kv(export_INFO(rec, 0, 'topic'))['EditorID'] == '12345'


@pytest.mark.parametrize('kind,name', sorted(DIAL_TYPES.items()))
def test_dial_type_names(kind, name):
    """Each DATA type byte exports as its name."""
    assert _kv(export_DIAL(_dial('t', kind)))['DialType'] == name


def test_ordinal_is_position_within_topic_and_resets():
    """Ordinal is per-topic and 0-based: it IS the filter precedence."""
    records = [_dial('TopicA', 0), _info(inam='a0'), _info(inam='a1'),
               _dial('TopicB', 0), _info(inam='b0')]
    out = dialogue_records(records)
    got = [(_kv(l)['Topic'], _kv(l)['Ordinal']) for _fid, l in out[INFO_SIG]]
    assert got == [('TopicA', '0'), ('TopicA', '1'), ('TopicB', '0')]


def test_deleted_info_is_dropped():
    """A deleted INFO leaves the topic intact."""
    records = [_dial('TopicA', 0), _info(inam='a0')]
    records[1].deleted = True
    out = dialogue_records(records)
    assert out[DIAL_SIG] and out[INFO_SIG] == []


def test_one_inam_may_appear_under_several_topics():
    """99 ids are reused across topics in Morrowind.esm -- identity is the pair.

    See: docs/reference/morrowind_dialogue_format.md#info-identity
    """
    records = [_dial('TopicA', 0), _info(inam='shared'),
               _dial('TopicB', 0), _info(inam='shared')]
    rows = [_kv(l) for _fid, l in dialogue_records(records)[INFO_SIG]]
    assert len(rows) == 2
    assert {r['EditorID'] for r in rows} == {'shared'}
    assert {r['Topic'] for r in rows} == {'TopicA', 'TopicB'}


def test_int_and_float_conditions_keep_their_type_tag():
    """24,819 INTV vs 16 FLTV in Morrowind.esm -- collapsing them is 16 bugs."""
    rec = _info(conditions=(('01sX3some_global', 'INTV', 50),
                            ('02fX4other_var', 'FLTV', 1.5)))
    kv = _kv(export_INFO(rec, 0, 't'))
    assert kv['ConditionCount'] == '2'
    assert kv['Condition[0].ValueType'] == 'Int'
    assert kv['Condition[0].Value'] == '50'
    assert kv['Condition[1].ValueType'] == 'Float'
    assert float(kv['Condition[1].Value']) == pytest.approx(1.5)


def test_condition_rule_parts_and_raw_rule_round_trip():
    """Every positional field of a variable rule, plus the raw string."""
    rule = '4CsX2nolore'
    kv = _kv(export_INFO(_info(conditions=((rule, 'INTV', 7),)), 0, 't'))
    assert kv['Condition[0].Rule'] == rule
    assert kv['Condition[0].Index'] == '4'
    assert kv['Condition[0].Function'] == 'C'
    assert kv['Condition[0].VarType'] == 's'
    assert kv['Condition[0].Comparison'] == '2'
    assert kv['Condition[0].Variable'] == 'nolore'
    assert 'Condition[0].FunctionIndex' not in kv


def test_numbered_function_index_is_not_sliced_as_a_variable():
    """'01500' is function 50 (Choice) compared '=', NOT VarType '5'.

    A numbered index occupies the bytes a variable rule uses for its type, so
    slicing both alike silently loses every Choice condition -- 1,748 of them
    in Morrowind.esm, 11,244 in TR_Mainland.
    See: docs/reference/morrowind_dialogue_format.md#conditions
    """
    kv = _kv(export_INFO(_info(conditions=(('01500', 'INTV', 1),)), 0, 't'))
    assert kv['Condition[0].Function'] == '1'
    assert kv['Condition[0].FunctionIndex'] == '50'
    assert kv['Condition[0].Comparison'] == '0'
    assert 'Condition[0].VarType' not in kv
    assert 'Condition[0].Variable' not in kv


def test_malformed_short_rule_is_skipped():
    """A rule under 5 chars cannot be decoded and emits nothing."""
    kv = _kv(export_INFO(_info(conditions=(('0Cs', 'INTV', 1),)), 0, 't'))
    assert 'ConditionCount' not in kv


def test_factionless_sentinel_becomes_a_flag():
    """FNAM 'FFFF' means 'speaker has NO faction', not a faction named FFFF."""
    kv = _kv(export_INFO(_info(faction='FFFF'), 0, 't'))
    assert kv['FactionLess'] == '1'
    assert 'Faction' not in kv
    other = _kv(export_INFO(_info(faction='Fighters Guild'), 0, 't'))
    assert other['Faction'] == 'Fighters Guild'


def test_quest_status_flag():
    """QSTF exports as Finished."""
    kv = _kv(export_INFO(_info(quest=('QSTF',)), 0, 't'))
    assert kv['QuestStatus'] == 'Finished'


def test_journal_index_and_disposition_share_one_field():
    """DATA field 2 is a union: disposition on a topic, index on a journal."""
    kv = _kv(export_INFO(_info(kind=4, disp=30), 0, 't'))
    assert kv['JournalIndex'] == '30' and kv['Disposition'] == '30'


def test_multiline_text_survives_escaping():
    """48 responses and 3,195 result scripts in Morrowind.esm hold newlines."""
    kv = _kv(export_INFO(_info(response='line one\r\nline two',
                               script='short a\r\nset a to 1'), 0, 't'))
    assert '\n' not in kv['Response'] and '\n' not in kv['ResultScript']
    assert 'line two' in kv['Response']
    assert 'set a to 1' in kv['ResultScript']

"""
TES3 DIAL/INFO export: identity, ordering and condition fidelity.

Every test builds its records by hand, so none needs Morrowind installed.
The format is recorded in docs/reference/morrowind_dialogue_format.md.
"""

import collections
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


class _Ctx:
    """The little of MorrowindContext the bark exporter asks for."""

    def derive(self, key: str) -> str:
        """A stable id for a derivation key, as the real context mints one."""
        return f'{abs(hash(key)) & 0xFFFFFF:08X}'

    master_dirs = ()

    def __init__(self):
        """The bark state `dialogue_records` fills in and the exporter reads."""
        self.bark_speakers = []
        self.bark_audiences = {}

    def resolve(self, record_id: str, signature: str = '') -> str:
        """Nothing resolves: a bare context has no id index."""
        return ''


def _voiced(topic: str, path: str, **kw) -> Tes3Record:
    """A voiced bark INFO of `topic`, naming `path` as its recording."""
    rec = _info(kind=1, **kw)
    rec.subrecords.append(_sub('SNAM', _cstr(path)))
    return rec


def test_a_voiced_bark_leaves_the_sidecar_as_dial_and_info():
    """Only the VOICED lines become TES4 records; the rest stay MWDI/MWIN.

    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    records = [_dial('Hello', 1),
               _voiced('Hello', 'Vo\\d\\m\\Hlo_DM008.mp3',
                       response='Good day.'),
               _dial('Chargen', 0),
               _info(inam='plain', response='Not voiced.')]
    out = dialogue_records(records, _Ctx())
    assert [_kv(l)['EditorID'] for _f, l in out['DIAL']] == ['HELLO'], \
        'the Hello channel becomes the topic _EDID_SUBTYPE routes'
    assert len(out['INFO']) == 1, 'only the voiced line leaves the sidecar'
    assert len(out[INFO_SIG]) == 2, 'both still reach the runtime'
    kv = _kv(out['INFO'][0][1])
    assert kv['Response[0].ResponseText'] == 'Good day.', 'the transcript rides along'
    assert 'Hlo_DM008' in kv['MorrowindVoice']


def test_a_bark_takes_the_race_its_RECORD_states_not_its_folder():
    """The record names the race; the folder is never consulted.

    `Vo\\ord\\` holds Ordinator lines whose records say class=guard, so a
    folder-derived race would be wrong wherever it disagreed.
    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    bark = _voiced('Hit', 'Vo\\ord\\Hit_OM002.mp3', response='Woh!',
                   gender=0)
    bark.subrecords.append(_sub('RNAM', _cstr('Imperial')))
    kv = _kv(dialogue_records([_dial('Hit', 1), bark], _Ctx())['INFO'][0][1])
    assert kv['BarkRace'] == '00000907', 'the RECORD says Imperial'
    assert kv['BarkSex'] == '0', 'DATA carries the gender'


def test_a_bark_stating_no_speaker_is_left_open():
    """An open bark gets no invented audience.

    The werewolf scream states no identity because `Fn_Werewolf` gates it;
    giving it a folder-derived race would put it on every NPC of that race.
    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    bark = _voiced('Hit', 'Vo\\ww\\scrm.mp3', response='', gender=-1)
    kv = _kv(dialogue_records([_dial('Hit', 1), bark], _Ctx())['INFO'][0][1])
    assert 'BarkRace' not in kv, 'no race is stated, so none is invented'
    assert 'ConditionCount' not in kv, 'and no audience is invented either'


class _SayCtx(_Ctx):
    """A context that knows one actor, `speaker`, and nothing else."""

    def base_signature(self, record_id: str, want: str = '') -> str:
        """`NPC_` for the one actor this context converts."""
        return 'NPC_' if record_id.lower() == 'speaker' else ''

    def resolve(self, record_id: str, signature: str = '') -> str:
        """The actor's FormID; nothing else resolves."""
        return '00ABCDEF' if record_id.lower() == 'speaker' else ''


def _say_script(script_id: str) -> Tes3Record:
    """A script whose body says one line."""
    script = Tes3Record(type='SCPT', flags=0, subrecords=[
        _sub('SCTX', _cstr('Say "Vo\\Misc\\x.mp3" "Hear me."'))])
    script.record_id = script_id
    return script


def test_an_actors_say_gets_a_topic_of_its_own_gated_on_that_actor():
    """`ObjectReference.Say` takes a TOPIC, so each line needs its own.

    A shared topic would let the engine pick any of its lines, and a bark
    topic would let every actor speak it unprompted.
    See: docs/commentary/morrowind_runtime.md#scripted-say
    """
    npc = Tes3Record(type='NPC_', flags=0, subrecords=[
        _sub('SCRI', _cstr('lineScript'))])
    npc.record_id = 'speaker'
    out = dialogue_records([npc, _say_script('lineScript')], _SayCtx())
    assert len(out['INFO']) == 1 and len(out['DIAL']) == 1
    dial_id, dial = out['DIAL'][0]
    kv = _kv(dial)
    assert kv['MorrowindSay'] == '1' and kv['DATA.Type'] == '1', (
        'a hidden conversation topic, never a bark channel')
    info = _kv(out['INFO'][0][1])
    assert info['ParentDIAL'] == dial_id
    assert info['Response[0].ResponseText'] == 'Hear me.'
    raw = bytes.fromhex(info['Condition[0].Raw'])
    assert raw[8] == 72 and raw[12:16] == bytes.fromhex('EFCDAB00'), (
        'GetIsID on the actor carrying the script')
    assert not out.get('SOUN'), 'an actor needs no sound stand-in'


def test_a_say_with_no_mouth_becomes_a_sound():
    """A script no actor carries speaks through a SOUN, not a topic.

    See: docs/commentary/morrowind_runtime.md#scripted-say
    """
    out = dialogue_records([_say_script('doorScript')], _SayCtx())
    assert not out['INFO'] and not out['DIAL']
    kv = _kv(out['SOUN'][0][1])
    assert kv['MorrowindSay'] == '1'
    assert kv['FNAM.Filename'].replace(chr(92) * 2, chr(92)) == (
        'Vo' + chr(92) + 'Misc' + chr(92) + 'x.mp3')


def test_a_custom_race_bark_names_its_real_speakers():
    """A race the importer cannot voice gates on the actors that HAVE it.

    Every custom Morrowind race exports as Imperial, so a race gate would put
    an Ayleid line on every Imperial. The speakers are resolved by running
    TES3's own filter over the actors instead.
    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    npc = Tes3Record(type='NPC_', flags=0, subrecords=[
        _sub('RNAM', _cstr('T_Cyr_Ayleid')), _sub('FLAG', b'\x00' * 4)])
    npc.record_id = 'T_Cyr_Ayleid_Guard'
    other = Tes3Record(type='NPC_', flags=0, subrecords=[
        _sub('RNAM', _cstr('Imperial')), _sub('FLAG', b'\x00' * 4)])
    other.record_id = 'Ordinary_Imperial'

    bark = _voiced('Hit', 'Vo\\ay\\m\\Hit_AyM001.mp3', response='Ungh.',
                   gender=0)
    bark.subrecords.append(_sub('RNAM', _cstr('T_Cyr_Ayleid')))

    class _Resolving(_Ctx):
        def resolve(self, record_id, signature=''):
            """Every actor in this fixture resolves to a distinct id."""
            return {'T_Cyr_Ayleid_Guard': '0004B1A5',
                    'Ordinary_Imperial': '0004B1A6'}.get(record_id, '')

    out = dialogue_records([npc, other, _dial('Hit', 1), bark], _Resolving())
    kv = _kv(out['INFO'][0][1])
    assert 'BarkRace' not in kv, 'a custom race cannot ride a vanilla VTYP'
    assert kv['ConditionCount'] == '2', 'the Ayleid, plus the gender test'
    assert 'a5b10400' in kv['Condition[0].Raw'], 'names the Ayleid speaker'
    assert 'a6b10400' not in kv['Condition[0].Raw'], 'never the Imperial'


def test_a_vampire_grunt_reaches_only_the_named_vampires():
    """`Vo\\v\\` lines NAME their speakers; no Dark Elf ever inherits them.

    The recordings sit in a vampire folder, but the records name each vampire
    outright, so the folder is never consulted and the gate is exact.
    See: docs/commentary/tes4_export_morrowind.md#voiced-barks
    """
    vampire = Tes3Record(type='NPC_', flags=0, subrecords=[
        _sub('RNAM', _cstr('Dark Elf')), _sub('FLAG', b'\x00' * 4)])
    vampire.record_id = 'aundae vampire 1'
    ordinary = Tes3Record(type='NPC_', flags=0, subrecords=[
        _sub('RNAM', _cstr('Dark Elf')), _sub('FLAG', b'\x00' * 4)])
    ordinary.record_id = 'ordinary dunmer'

    bark = _voiced('Hit', 'Vo\\v\\Hit_vDM004.mp3', response='Ughn.')
    bark.subrecords.append(_sub('ONAM', _cstr('aundae vampire 1')))

    class _Resolving(_Ctx):
        def resolve(self, record_id, signature=''):
            """Both actors resolve, so a wrong gate would be visible."""
            return {'aundae vampire 1': '0004B1A5',
                    'ordinary dunmer': '0004B1A6'}.get(record_id.lower(), '')

    out = dialogue_records([vampire, ordinary, _dial('Hit', 1), bark],
                           _Resolving())
    kv = _kv(out['INFO'][0][1])
    assert 'BarkRace' not in kv, 'a vampire line is not a Dark Elf line'
    assert kv['ConditionCount'] == '1', 'exactly the one named vampire'
    assert 'a5b10400' in kv['Condition[0].Raw'], 'names the vampire'
    assert 'a6b10400' not in kv['Condition[0].Raw'], 'never the ordinary Dunmer'


def _say_export(tmp_path, body: str) -> str:
    """An `INFO.txt` holding `body`, laid out as the export writes one."""
    (tmp_path / 'INFO.txt').write_text(body, encoding='utf-8')
    return str(tmp_path)


def test_say_table_maps_the_path_a_script_wrote_to_its_topic(tmp_path):
    """The runtime holds a FILE; `ObjectReference.Say` takes the line's TOPIC.

    Handing it the INFO instead passes the engine the wrong form type.
    See: docs/commentary/morrowind_runtime.md#scripted-say
    """
    from tes5_import.dialogue.say_morrowind import say_rows
    (tmp_path / 'DIAL.txt').write_text(
        '---RECORD_BEGIN---\n'
        'Signature=DIAL\n'
        'FormID=01001234\n'
        'MorrowindSay=1\n'
        '---RECORD_END---\n', encoding='utf-8')
    body = ('---RECORD_BEGIN---\n'
            'Signature=INFO\n'
            'FormID=0100ABCD\n'
            'ParentDIAL=01001234\n'
            'MorrowindVoice=Vo\\\\Misc\\\\X.mp3\n'
            '---RECORD_END---\n')
    rows = say_rows(_say_export(tmp_path, body), 'Morrowind.esm')
    assert rows == ['vo\\misc\\x.mp3=Morrowind.esm|00001234|0.00|'], (
        'keyed lowercase, naming the TOPIC without its master index')


def test_say_table_names_the_sound_of_a_line_with_no_mouth(tmp_path):
    """A door's or an activator's line plays as a SOUN, so the row names one.

    See: docs/commentary/morrowind_runtime.md#scripted-say
    """
    from tes5_import.dialogue.say_morrowind import say_rows
    (tmp_path / 'SOUN.txt').write_text(
        '---RECORD_BEGIN---\n'
        'Signature=SOUN\n'
        'FormID=01005678\n'
        'EditorID=MWSaySound005678\n'
        'FNAM.Filename=Vo\\\\Misc\\\\Door.wav\n'
        'MorrowindVoice=Vo\\\\Misc\\\\Door.wav\n'
        'MorrowindSay=1\n'
        '---RECORD_END---\n', encoding='utf-8')
    rows = say_rows(str(tmp_path), 'Morrowind.esm')
    assert rows == [
        'vo\\misc\\door.wav=Morrowind.esm|00000000|0.00|MWSaySound005678']


def test_say_table_leaves_barks_out(tmp_path):
    """A bark is picked off the engine's own channel, never named by path.

    See: docs/commentary/morrowind_runtime.md#scripted-say
    """
    from tes5_import.dialogue.say_morrowind import say_rows
    body = ('---RECORD_BEGIN---\n'
            'Signature=INFO\n'
            'FormID=01000001\n'
            'MorrowindInfo=some_bark_id\n'
            'MorrowindVoice=Vo\\\\a\\\\m\\\\Hlo.mp3\n'
            '---RECORD_END---\n')
    assert say_rows(_say_export(tmp_path, body), 'Morrowind.esm') == []


def test_a_package_allows_the_speech_openmw_would():
    """Hello and idle voice follow the actor's Hello setting and package kind.

    See: docs/commentary/tes4_export_morrowind.md#when-a-bark-fires
    """
    from tes5_import.packages.interrupt_morrowind import morrowind_interrupt
    wander = {'PKDT.Type': '5', 'MorrowindHello': '30'}
    follow = {'PKDT.Type': '1', 'MorrowindHello': '30'}
    mute = {'PKDT.Type': '5', 'MorrowindHello': '0'}
    assert morrowind_interrupt(wander, 0x44) == 0x44 | 0x01 | 0x10 | 0x80
    assert morrowind_interrupt(follow, 0x44) == 0x44 | 0x10, (
        'OpenMW neither greets nor chatters under a follow package')
    assert morrowind_interrupt(mute, 0x44) == 0x44 | 0x10
    assert morrowind_interrupt({'PKDT.Type': '5'}, 0x44) == 0x44, (
        'a package Morrowind did not author keeps the default')


def test_a_bark_whose_faction_names_no_form_is_dropped():
    """An unnameable audience drops the LINE; keeping it would widen it.

    See: docs/commentary/tes4_export_morrowind.md#an-audience-that-cannot-be-named
    """
    bark = _voiced('Hello', 'Vo/ord/Hlo_ORM001.mp3', response='Move along.',
                   gender=0)
    bark.subrecords.append(_sub('RNAM', _cstr('Dark Elf')))
    bark.subrecords.append(_sub('FNAM', _cstr('Temple')))
    dial = Tes3Record(type='DIAL', flags=0, subrecords=[
        _sub('DATA', bytes([1]))])
    dial.record_id = 'Hello'
    ctx = _Ctx()
    ctx.unresolved = collections.Counter()
    out = dialogue_records([dial, bark], ctx)
    assert not out['INFO'] and not out['DIAL']
    assert ctx.unresolved['bark audience'] == 1


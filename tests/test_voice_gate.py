"""The voice types a converted INFO may be spoken in (tes5_import/dialogue/voice_gate.py).

See: docs/commentary/tes5_import_dialogue.md#recorded-voice-gate
"""

import struct

import pytest

from tes5_import.base.conditions import read_getisid_fids
from tes5_import.dialogue import voice_gate

FACTION = 0x0100761A
NORD_F, IMPERIAL_M, REDGUARD_M = 0x11, 0x22, 0x33
VTYP_EDIDS = {NORD_F: 'TES4FemaleNord', IMPERIAL_M: 'TES4MaleImperial',
              REDGUARD_M: 'TES4MaleRedguard'}
TOPIC_VTYPS = {IMPERIAL_M, REDGUARD_M}
INFO_FID = 0x013220A5


def _cond(func, value, op=0x00, param=FACTION, on_player=False, or_next=False):
    """One TES4 CTDA as the export writes it (24 bytes, hex)."""
    flags = op | (0x02 if on_player else 0) | (0x01 if or_next else 0)
    return (struct.pack('<B3xfH2xII4x', flags, value, func, param, 0)).hex()


def _info(*conds, fid=INFO_FID):
    """An exported INFO dict carrying *conds*."""
    rec = {'FormID': f'{fid:08X}', 'ConditionCount': str(len(conds))}
    for i, raw in enumerate(conds):
        rec[f'Condition[{i}].Raw'] = raw
    return rec


@pytest.fixture
def recordings(tmp_path):
    """A plugin whose line INFO_FID is recorded only by a female Nord."""
    leaf = tmp_path / 'Oblivion.esm' / 'sound' / 'voice' / 'Oblivion.esm' / 'nord' / 'f'
    leaf.mkdir(parents=True)
    (leaf / f'fgadv_advancement_{INFO_FID & 0xFFFFFF:08x}_1.mp3').write_bytes(b'')
    assert voice_gate.load_recordings(str(tmp_path / 'Oblivion.esm'), 'Oblivion.esm') == 1
    voice_gate.bind_recordings(VTYP_EDIDS)
    yield
    voice_gate.load_recordings('', 'Oblivion.esm')


def _vtyps(info):
    """The voice gate the importer would write for *info* in a topic voiced by TOPIC_VTYPS."""
    return voice_gate.info_voice_types(info, {}, TOPIC_VTYPS, 1)


def test_unvoiced_speaker_faction_line_is_not_voice_gated():
    """Eydis Fire-Eye: a Fighters Guild rank line must not inherit other guilds' voices."""
    assert _vtyps(_info(_cond(73, 7.0, op=0x60))) == set()


@pytest.mark.parametrize('cond', [
    _cond(73, 7.0, op=0x60, on_player=True),
    _cond(71, 0.0),
    _cond(71, 1.0, or_next=True),
    _cond(71, 3.0, op=0x80),
])
def test_condition_that_does_not_scope_the_speaker_keeps_the_topic_gate(cond):
    """Player-side, negated, OR'd and below-rank tests do not name a speaker audience."""
    assert _vtyps(_info(cond)) == TOPIC_VTYPS


def test_voiced_line_goes_to_exactly_its_recorded_voices(recordings):
    """A recorded line is gated to the voices that hold it, audience or not."""
    del recordings
    assert _vtyps(_info()) == {NORD_F}
    assert _vtyps(_info(_cond(73, 7.0, op=0x60))) == {NORD_F}


def test_named_speaker_keeps_their_own_voice(recordings):
    """A GetIsID line takes its speaker's voice, never the recordings index."""
    del recordings
    info = _info(_cond(72, 1.0, param=0x01000ABC))
    (npc,) = read_getisid_fids(info, offset=1)
    assert voice_gate.info_voice_types(info, {npc: REDGUARD_M}, TOPIC_VTYPS, 1) == {REDGUARD_M}

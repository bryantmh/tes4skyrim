"""Tests for tools/live/flight_log.py: naming recorder events from a plugin."""

import json
import struct
import zlib

from tools.live.flight_log import Namer, Plugin, event_ids, read_events, render


def _sub(tag: bytes, payload: bytes) -> bytes:
    """One subrecord."""
    return tag + struct.pack('<H', len(payload)) + payload


def _record(sig: bytes, fid: int, body: bytes, flags: int = 0) -> bytes:
    """One record with a 24-byte header; flag 0x40000 compresses the body."""
    if flags & 0x40000:
        body = struct.pack('<I', len(body)) + zlib.compress(body)
    return sig + struct.pack('<IIIIHH', len(body), flags, fid, 0, 44, 0) + body


def _group(label: int, gtype: int, content: bytes) -> bytes:
    """A GRUP around `content`."""
    return b'GRUP' + struct.pack('<IIiII', 24 + len(content), label, gtype, 0, 0) + content


def _plugin(tmp_path):
    """A one-master plugin with a quest, a topic and line, an NPC and its ref."""
    header = _record(b'TES4', 0, _sub(b'HEDR', bytes(12)) + _sub(b'MAST', b'Skyrim.esm\0'))
    quest = _record(b'QUST', 0x0100104C, _sub(b'EDID', b'VCG01\0'), flags=0x40000)
    dial = _record(b'DIAL', 0x01000500, _sub(b'EDID', b'GREETING\0'))
    info = _record(b'INFO', 0x011057E8, _sub(b'NAM1', b'Easy, now. Take a seat.\0'))
    npc = _record(b'NPC_', 0x0100ABCD, _sub(b'EDID', b'DocMitchell\0'))
    ref = _record(b'ACHR', 0x01001234, _sub(b'NAME', struct.pack('<I', 0x0100ABCD)))
    data = (header + _group(0x54535551, 0, quest)
            + _group(0x4C414944, 0, dial + _group(0x01000500, 7, info))
            + _group(0x5F43504E, 0, npc)
            + _group(0x0, 1, ref))
    path = tmp_path / 'Test.esm'
    path.write_bytes(data)
    return Plugin(path)


def _events(tmp_path):
    """A recorder log: a stage, a dialogue line, and a torn last line."""
    lines = [
        {'t': '18:00:00.000', 'ms': 1, 'ev': 'quest_stage', 'quest': '0F00104C', 'stage': 105},
        {'t': '18:00:01.000', 'ms': 2, 'ev': 'topic_info', 'speaker': '0F001234',
         'speaker_base': '0F00ABCD', 'info': '0F1057E8', 'phase': 'begin'},
        {'t': '18:00:02.000', 'ms': 3, 'ev': 'package', 'actor': '00000014',
         'package': '0F009999', 'package_check': 'unresolved'},
    ]
    path = tmp_path / 'events.jsonl'
    path.write_text('\n'.join(json.dumps(x) for x in lines) + '\n{"t":"18:0', encoding='utf-8')
    return read_events(path)


def test_torn_last_line_is_skipped(tmp_path):
    """A line cut off by a live game is dropped, not an error."""
    assert [e['ev'] for e in _events(tmp_path)] == ['quest_stage', 'topic_info', 'package']


def test_prefix_is_detected_from_the_events(tmp_path):
    """The plugin's load-order slot comes from ids that exist in it."""
    plugin = _plugin(tmp_path)
    plugin.detect_prefix(event_ids(_events(tmp_path)))
    assert plugin.prefix == 0x0F


def test_events_render_with_names(tmp_path):
    """Quests by editor id, lines by topic and text, refs by their base."""
    plugin = _plugin(tmp_path)
    events = _events(tmp_path)
    plugin.detect_prefix(event_ids(events))
    namer = Namer([plugin])
    stage, line, package = (render(e, namer, raw=False) for e in events)
    assert 'quest=VCG01 [0F00104C]' in stage and 'stage=105' in stage
    assert 'speaker=ref of DocMitchell [0F001234]' in line
    assert 'info=GREETING: "Easy, now. Take a seat." [0F1057E8]' in line
    assert 'actor=[00000014]' in package and '!package_check=unresolved' in package

"""
Which topic a scripted `Say "file" "text"` speaks, for the runtime to find.

TES3 names a FILE; `ObjectReference.Say` takes a TOPIC, so the export turned
each distinct line into a topic of its own holding one INFO. What is missing at
runtime is the way back: the interpreter holds the path the script wrote and
needs the FormID of the TOPIC carrying it, and -- because `SayDone` has no
engine counterpart -- how long the recording runs.

So this writes one row per line, `path=Plugin.esm|TOPIC FormID|seconds|SOUN id`,
keyed by the path lowercased with forward slashes folded to backslashes. A line
spoken by something with no mouth carries a SOUN to play in place of a topic.

See: docs/commentary/morrowind_runtime.md#scripted-say
"""

import os
import wave

from asset_convert.audio.mp3_length import mp3_duration
from output_layout import assets_for

from ..base.text_reader import parse_export_file
from ..record_types.common import get_formid

#: What the `Say` opcode needs: `voice path=Plugin.esm|TOPIC FormID|seconds|SOUN id`.
SAY_TABLE = 'say_formid.txt'

#: The export field marking a DIAL that exists only to carry one `Say` line.
SAY_FIELD = 'MorrowindSay'

#: The export field naming a line's recording; only voiced INFOs carry it.
_VOICE_FIELD = 'MorrowindVoice'


def say_topic_fids(by_type: dict) -> list:
    """The raw24 FormID of each topic a Morrowind `Say` line was exported as.

    See: docs/commentary/morrowind_runtime.md#scripted-say
    """
    return [get_formid(rec, 'FormID') & 0xFFFFFF
            for rec in by_type.get('DIAL', []) if rec.get(SAY_FIELD)]


def _wav_seconds(path: str) -> float:
    """A PCM wav's length, or 0.0 for a compressed one `wave` cannot read."""
    try:
        with wave.open(path, 'rb') as clip:
            return clip.getnframes() / float(clip.getframerate() or 1)
    except (wave.Error, OSError, EOFError):
        return 0.0


def _staged_files(voice_root: str) -> dict:
    """{lowercased file stem: path} over the voice tree the extract stage staged."""
    return {os.path.splitext(name)[0].lower(): os.path.join(folder, name)
            for folder, _dirs, names in os.walk(voice_root) for name in names}


def _seconds(path: str) -> float:
    """One recording's length; 0.0 when absent, so the runtime times the subtitle."""
    if not path:
        return 0.0
    return (_wav_seconds(path) if path.lower().endswith('.wav')
            else mp3_duration(path))


def _key(voice: str) -> str:
    """One recording's table key: lowercased, `/` folded to a backslash."""
    return voice.replace('/', chr(92)).lower()


def _say_topics(export_dir: str) -> dict:
    """{key: (TOPIC raw24, INFO raw24)} for each line an actor speaks."""
    topics = {get_formid(rec, 'FormID') for rec in parse_export_file(
        os.path.join(export_dir, 'DIAL.txt')) if rec.get(SAY_FIELD)}
    out = {}
    for rec in parse_export_file(os.path.join(export_dir, 'INFO.txt')):
        topic = get_formid(rec, 'ParentDIAL')
        if topic in topics and rec.get(_VOICE_FIELD):
            out[_key(rec[_VOICE_FIELD])] = (
                topic & 0xFFFFFF, get_formid(rec, 'FormID') & 0xFFFFFF)
    return out


def _say_sounds(export_dir: str) -> dict:
    """{key: (SOUN EditorID, file it names)} for each mouthless line."""
    return {_key(rec[_VOICE_FIELD]): (rec.get('EditorID', ''),
                                      rec.get('FNAM.Filename', ''))
            for rec in parse_export_file(os.path.join(export_dir, 'SOUN.txt'))
            if rec.get(SAY_FIELD) and rec.get(_VOICE_FIELD)}


def _staged_sound(assets: str, named: str) -> str:
    """The copy `stage_say_sounds` made of one recording, or ''."""
    stem = os.path.splitext(os.path.join(
        assets, 'sound', *named.replace('/', chr(92)).split(chr(92))))[0]
    return next((stem + ext for ext in ('.wav', '.mp3')
                 if os.path.isfile(stem + ext)), '')


def say_rows(export_dir: str, plugin: str) -> list:
    """`path=Plugin.esm|TOPIC FormID|seconds|SOUN id` per `Say` line owned here.

    The topic is 00000000 for a line no actor speaks, and the SOUN id empty
    for one only actors speak.
    See: docs/commentary/morrowind_runtime.md#scripted-say
    """
    topics, sounds = _say_topics(export_dir), _say_sounds(export_dir)
    assets = str(assets_for(export_dir))
    staged = _staged_files(os.path.join(assets, 'sound', 'Voice', plugin))
    rows = []
    for key in sorted(set(topics) | set(sounds)):
        topic, info = topics.get(key, (0, 0))
        edid, named = sounds.get(key, ('', ''))
        path = (staged.get(f'mw_{info:08x}_1', '') if info
                else _staged_sound(assets, named))
        rows.append(f'{key}={plugin}|{topic:08X}|{_seconds(path):.2f}|{edid}')
    return rows

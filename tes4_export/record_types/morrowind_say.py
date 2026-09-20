"""
Morrowind's scripted `Say "file" "text"` as TES4 DIAL/INFO.

`ObjectReference.Say` takes a TOPIC and lets the engine pick the INFO, so each
distinct line gets a topic of its own holding exactly one INFO: saying the
topic can then only ever say that line. The topic is a Conversation topic
marked `MorrowindSay`, which the importer keeps off the player's menu and out
of every ambient channel.

Who speaks is authored: the actors carrying the script, or the reference a
call names with `->`. They gate the INFO by `GetIsID`, which is also what
files the recording under each speaker's own voice type.

See: docs/commentary/morrowind_runtime.md#scripted-say
"""

import re

from ..record_types.common import escape_value
from ..tes3_reader import get_string, get_subrecord
from .morrowind_barks import ctda

#: `[target ->] Say "file" "subtitle"`, however it is spaced.
_SAY_CALL = re.compile(
    r'(?:("[^"]+"|[\w\-\.\']+)[ \t]*->[ \t]*)?'
    r'\bsay[ ,\t]+"([^"]+)"[ ,\t]*"([^"]*)"', re.IGNORECASE)

#: The export field marking a record that exists only to carry one `Say` line.
SAY_FIELD = 'MorrowindSay'

#: TES4 DIAL.DATA type Conversation: never on the player's menu.
_DIAL_TYPE_CONVERSATION = 1

#: TES4 `GetIsID`, and the CTDA type bit chaining a test to the next by OR.
_FUNC_GET_IS_ID = 72
_OR = 0x01

#: Most speakers one line may name; the engine drops an INFO past 22 conditions.
_MAX_SPEAKERS = 20

#: SNDX attenuation `export_SOUN` gives a sound stating no range of its own.
_MIN_ATT, _MAX_ATT = 20, 20

#: The record types a `Say` can move a mouth on.
_ACTORS = ('NPC_', 'CREA')


def _script_actors(records) -> dict:
    """{lowercased script id: [actor record ids]} for every scripted actor."""
    out = {}
    for rec in records:
        if rec.type not in _ACTORS or rec.deleted:
            continue
        script = get_subrecord(rec, 'SCRI')
        if script is not None:
            out.setdefault(get_string(script).lower(), []).append(
                rec.record_id)
    return out


def _implicit_speakers(rec, owners: dict) -> list:
    """Who runs a body with no `->`: the script's actors, or an INFO's `ONAM`."""
    if rec.type == 'SCPT':
        return owners.get((rec.record_id or '').lower(), [])
    actor = get_subrecord(rec, 'ONAM')
    return [get_string(actor)] if actor is not None else []


def say_calls(records) -> dict:
    """{voice path key: (path, text, speaker ids)} over every script body.

    The key is the path lowercased with `/` folded to a backslash, the two
    spellings a script may use for one file.
    """
    owners = _script_actors(records)
    out = {}
    for rec in records:
        for sub in rec.subrecords:
            if sub.type not in ('SCTX', 'BNAM'):
                continue
            for target, path, text in _SAY_CALL.findall(get_string(sub)):
                key = path.replace('/', chr(92)).lower()
                row = out.setdefault(key, (path, text, []))
                named = ([target.strip('"')] if target
                         else _implicit_speakers(rec, owners))
                row[2].extend(who for who in named if who not in row[2])
    return out


def _actor_fids(speakers: list, ctx) -> list:
    """The FormID of each speaker that converts to an actor, in call order."""
    fids = []
    for who in speakers:
        signature = ctx.base_signature(who)
        form_id = ctx.resolve(who, signature) if signature in _ACTORS else ''
        if form_id and form_id not in fids:
            fids.append(form_id)
    return fids[:_MAX_SPEAKERS]


def _has_mouthless_speaker(speakers: list, ctx) -> bool:
    """Whether a door, an activator, the player or nobody at all says the line."""
    return not speakers or any(ctx.base_signature(who) not in _ACTORS
                               for who in speakers)


def _topic(key: str, path: str, text: str, fids: list, ctx) -> tuple:
    """`(dial, info)` for one line its actors speak through the voice channel."""
    dial_id = ctx.derive(f'barksaydial:{key}')
    edid = f'MWSay{dial_id[-6:]}'
    dial = (dial_id, [f'EditorID={edid}', f'FULL={edid}',
                      f'DATA.Type={_DIAL_TYPE_CONVERSATION}', f'{SAY_FIELD}=1'])
    lines = [f'ParentDIAL={dial_id}',
             'DATA.DialogType=0', 'DATA.NextSpeaker=0', 'DATA.Flags=0',
             f'MorrowindVoice={escape_value(path)}',
             'ResponseCount=1',
             'Response[0].EmotionType=0',
             'Response[0].EmotionValue=50',
             'Response[0].ResponseNumber=1',
             f'Response[0].ResponseText={escape_value(text)}']
    lines += [f'Condition[{i}].Raw='
              + ctda(_FUNC_GET_IS_ID, int(fid, 16),
                     operator=0 if fid == fids[-1] else _OR)
              for i, fid in enumerate(fids)]
    lines.append(f'ConditionCount={len(fids)}')
    return dial, (ctx.derive(f'barksay:{key}'), lines)


def _sound(key: str, path: str, ctx) -> tuple:
    """The SOUN a mouthless speaker plays the same recording through."""
    form_id = ctx.derive(f'barksaysound:{key}')
    return form_id, [f'EditorID=MWSaySound{form_id[-6:]}',
                     f'FNAM.Filename={escape_value(path)}',
                     f'MorrowindVoice={escape_value(path)}', f'{SAY_FIELD}=1',
                     f'SNDX.MinAttDist={_MIN_ATT}', f'SNDX.MaxAttDist={_MAX_ATT}',
                     'SNDX.FreqAdj=0', 'SNDX.Flags=0',
                     'SNDX.StaticAttenuation=0']


def say_lines(records, ctx) -> dict:
    """{'DIAL', 'INFO', 'SOUN'} records for every scripted `Say` line.

    An actor speaks through a topic of its own, which moves its mouth; a door,
    an activator or the player's own head has no mouth, and plays the file as
    a sound from where it stands.
    See: docs/commentary/morrowind_runtime.md#scripted-say
    """
    out = {'DIAL': [], 'INFO': [], 'SOUN': []}
    for key, (path, text, speakers) in sorted(say_calls(records).items()):
        named = path.replace('/', chr(92))
        fids = _actor_fids(speakers, ctx)
        if fids and text:
            dial, info = _topic(key, named, text, fids, ctx)
            out['DIAL'].append(dial)
            out['INFO'].append(info)
        if _has_mouthless_speaker(speakers, ctx):
            out['SOUN'].append(_sound(key, named, ctx))
    return out

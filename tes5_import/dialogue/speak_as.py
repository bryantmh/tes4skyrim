r"""Speaker activators: give TES4's disembodied voices a real talking activator.

THE PROBLEM
-----------
TES4's `Say` names a speak-as actor separately from the reference that emits
the sound:

    ArenaMatchPlayerRef.Say Announcer 1 ArenaMouth 1
    ^ an XMarker (STAT)                 ^ WHO is speaking

Oblivion resolves the speaker to that NPC, so the line picks up the NPC's
voice folder and is delivered as a real exchange with the player.  Skyrim's
`Say` has no such argument: the speaker stays the XMarker, and **a STAT has no
voice type at all**, so the engine finds no voice folder.

THE FIX -- WHAT VANILLA ACTUALLY DOES
-------------------------------------
A TACT (Talking Activator) is an activator that carries a `VNAM` voice type
and that the engine accepts as a dialogue speaker.  Vanilla ships 25 and
**every single one has a VNAM** -- censused 2026-08-18 against
references/Skyrim.esm/TACT.txt, 25/25, no exceptions.
`DBNightMotherTalkingActivator` even uses `Markers\Marker_LinkMarker.nif`,
the same marker mesh our emitters already are.

So, per (emitter reference, speak-as NPC) pair:

  * mint a TACT carrying that NPC's converted VTYP, and
  * place a REFR of it at the emitter's exact position/cell.

THE LINE IS SPOKEN BY A SCENE, AS VANILLA DOES IT
-------------------------------------------------
A plain `Say()` on a non-actor plays its audio and then never retires its
subtitle: the line's per-frame update (TESObjectREFR vtable slot 0x40) is
driven by an actor's own update, or for a non-actor only by a scene's
dialogue action (BGSSceneActionDialogue slot 19).  Vanilla speaks through a
TACT exactly that way -- 19 scene dialogue actions in Skyrim.esm (Azura, the
Night Mother, Potema, Namira's shrine, the Augur).

So each call site (emitter, voice, topic) gets a one-action SCEN in vanilla
DA11NamiraScene's layout, owned by the start-game quest `TES4SpeakAs`, whose
forced-reference aliases are the speakers; the topic becomes a Scene topic
of that quest too, since the engine names a scene line's voice file after
the scene's quest (every vanilla scene topic is its scene's quest's).
`TES4Polyfill.SpeakAs` ForceStarts it.

A scene starts a non-actor's line with no speaker actor, so the voice type
comes from the INFO's own Speaker (ANAM) -- vanilla's Night Mother and Augur
lines name their voice NPC there, and without it the line is silent and its
subtitle lasts 0.5s.  That NPC is TES4's third `Say` argument.  TES4's fourth
plays the voice at the player's position (CS wiki: VoiceAudioPositionFlag),
which is vanilla's Audio Output Override (ONAM) `SOMDialogue2D`, as on
Potema's lines.

PER PAIR, NOT PER EMITTER
-------------------------
A REFR has one base, so it has one voice type.  Three emitters speak for more
than one voice, and the voices differ in gender -- `SE07ThadonSpeaks` covers
both SEThadon (Male) and SESyl (Female).  Keying on the emitter alone would
give one of them the wrong voice folder.  Measured on Oblivion.esm: 34
emitters, 35 (emitter, voice) pairs.

Layout verified against BOTH xEdit (`wbRecord(TACT ...)` in
Core/wbDefinitionsTES5.pas: EDID VMAD OBND FULL model DEST keywords PNAM SNAM
FNAM VNAM) and a real dump (references/Skyrim.esm/TACT.txt).  PNAM/FNAM are
`wbUnknown(..., cpIgnore, True)` -- required, and zero in every vanilla record.
"""

import re
import struct

from ..base.text_reader import get_formid, get_str
from ..base.writer import (pack_record, pack_subrecord, pack_string_subrecord,
                     pack_formid_subrecord, pack_obnd, pack_float_subrecord,
                     pack_uint16_subrecord, pack_uint32_subrecord)
from .quest import quest_aliases
from ..base.conditions import read_getisid_fids

#: Marker mesh + MODT of vanilla's Night Mother TACT (Skyrim.esm 00022440).

_SAY_SPEAK_AS_RE = re.compile(
    r"([A-Za-z]\w*)[ \t]*\.[ \t]*Say[ \t]+([A-Za-z]\w*)[ \t]+(\d+)[ \t]+"
    r"([A-Za-z]\w*)(?:[ \t]+(\d+))?", re.IGNORECASE)


def _quest_stage_bodies(rec: dict):
    """Every `.say`-bearing stage result script on one QUST record."""
    for key, value in rec.items():
        if (isinstance(value, str) and "ResultScript" in key
                and ".say" in value.lower()):
            yield value


def _bodies(by_type: dict):
    """Every TES4 script body in the plugin: SCPT, INFO results, QUST stages."""
    sources = [("SCPT", "SCTX"), ("INFO", "ResultScript"), ("QUST", None)]
    for sig, field in sources:
        for rec in by_type.get(sig, []):
            if not field:
                yield from _quest_stage_bodies(rec)
                continue
            body = get_str(rec, field) or ""
            if body and ".say" in body.lower():
                yield body


def scan_speak_as_calls(by_type: dict) -> list:
    """Every speak-as `Say` call site, deduplicated and sorted.

    A site seen both with and without the in-head flag collapses to in-head.

    See: docs/commentary/tes5_import_dialogue.md#speaker-activator-construction

    Returns tuples ``(emitter, voice, topic, in_head)`` -- EditorIDs lowercased,
    ``in_head`` True when the authored fourth argument is a non-zero integer.
    Only sites whose topic names a DIAL and whose speak-as token names an
    NPC_/CREA base are returned; anything else in that slot is a flag or a
    stray token, never a voice.
    """
    dials = {(get_str(d, "EditorID") or "").lower()
             for d in by_type.get("DIAL", [])}
    actors = set()
    for sig in ("NPC_", "CREA"):
        for rec in by_type.get(sig, []):
            e = (get_str(rec, "EditorID") or "").lower()
            if e:
                actors.add(e)
    found = set()
    for body in _bodies(by_type):
        for m in _SAY_SPEAK_AS_RE.finditer(body):
            emitter, topic, _force, voice, in_head = m.groups()
            topic = topic.lower()
            voice = voice.lower()
            if topic not in dials or voice not in actors:
                continue
            found.add((emitter.lower(), voice, topic,
                       bool(in_head) and int(in_head) != 0))
    return sorted(found)


def scan_speak_as_topics(by_type: dict) -> set:
    """DIAL FormIDs (24-bit) reachable ONLY through a speak-as `Say`."""
    edid_to_dial = {}
    for rec in by_type.get("DIAL", []):
        edid = (get_str(rec, "EditorID") or "").lower()
        fid = get_formid(rec, "FormID") & 0x00FFFFFF
        if edid and fid:
            edid_to_dial[edid] = fid
    found = set()
    for _emitter, _voice, topic, _in_head in scan_speak_as_calls(by_type):
        fid = edid_to_dial.get(topic)
        if fid:
            found.add(fid)
    return found

_TACT_MODL = 'Markers\\Marker_LinkMarker.nif'
_TACT_MODT = bytes.fromhex('020000000000000000000000')

#: Subrecords a cloned speaker never inherits (teleport, owner, lock, scale).
_CLONE_DROP_PREFIXES = (
    'XTEL', 'XOWN', 'XLOC', 'XESP', 'XPRM', 'XLIB', 'XLKR', 'XSCL',
    'XRDS', 'XEMI', 'XMBR', 'XCNT', 'XRNK', 'XACT', 'XTRG', 'XSED',
    'XCHG', 'XHLT', 'XPPA', 'XATO', 'XLRT', 'XLRL',
)

#: (emitter EditorID lower, voice EditorID lower) -> speaker REFR FormID.
_SPEAKER_REFS = {}

#: (emitter, voice, topic) EditorIDs lower -> the SCEN that speaks it.
_SCENES = {}

#: The start-game quest that owns every speak-as scene, speaker alias and scene topic.
SCENE_QUEST_EDID = 'TES4SpeakAs'

#: The FormID of `SCENE_QUEST_EDID` once built this run (at most one entry).
_SCENE_QUEST = []

#: Speak-as topic (24-bit DIAL FormID) -> (voice NPC source FormIDs, voice at the player).
_TOPIC_VOICES = {}

#: Skyrim.esm SOMDialogue2D: dialogue heard at full volume wherever the player stands.
_SOM_DIALOGUE_2D = 0x000B5183


def reset() -> None:
    """Clear per-run state (the importer may build several plugins)."""
    _SPEAKER_REFS.clear()
    _SCENES.clear()
    _TOPIC_VOICES.clear()
    _SCENE_QUEST.clear()


def scene_quest_fid() -> int:
    """FormID of the quest owning the speak-as scenes and scene topics, or 0."""
    return _SCENE_QUEST[0] if _SCENE_QUEST else 0


def export_scene_map() -> dict:
    """(emitter_edid, voice_edid, topic_edid) -> SCEN FormID."""
    return dict(_SCENES)


def scene_property_name(emitter: str, voice: str, topic: str) -> str:
    """The Papyrus property name script_convert emits for a speak-as scene."""
    return f'TES4Scene_{emitter.lower()}_{voice.lower()}_{topic.lower()}'


def speaker_subrecords(info_rec: dict, offset: int) -> bytes:
    """ANAM Speaker (and ONAM `SOMDialogue2D`) for an INFO of a speak-as topic, else b''.

    The speaker is the NPC the INFO's own GetIsID names -- which line belongs
    to whom -- else the voice its call sites name.
    See: docs/commentary/tes5_import_dialogue.md#speaker-activator-construction
    """
    entry = _TOPIC_VOICES.get(get_formid(info_rec, 'ParentDIAL') & 0x00FFFFFF)
    if not entry:
        return b''
    voices, at_player = entry
    own = read_getisid_fids(info_rec, offset=offset, positive_only=True)
    speaker = min(own) if own else min(voices)
    out = pack_formid_subrecord('ANAM', speaker)
    if at_player:
        out += pack_formid_subrecord('ONAM', _SOM_DIALOGUE_2D)
    return out


def _record_topic_voices(calls: list, npc_by_edid: dict, dial_fids: dict) -> None:
    """Fill `_TOPIC_VOICES` from the call sites whose topic and voice both resolve."""
    for _e, voice, topic, at_player in calls:
        npc, dial = npc_by_edid.get(voice), dial_fids.get(topic)
        if npc is None or not dial:
            continue
        voices, flag = _TOPIC_VOICES.get(dial & 0x00FFFFFF, (set(), False))
        voices.add(get_formid(npc, 'FormID'))
        _TOPIC_VOICES[dial & 0x00FFFFFF] = (voices, flag or at_player)


def _dial_fids(by_type: dict) -> dict:
    """DIAL EditorID (lower) -> source FormID."""
    return {(get_str(d, 'EditorID') or '').lower(): get_formid(d, 'FormID')
            for d in by_type.get('DIAL', [])}


def _pack_tact(fid: int, edid: str, vtyp_fid: int, name: str) -> bytes:
    """One TACT, in the vanilla subrecord order."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_obnd(0, 0, 0, 0, 0, 0)
    if name:
        subs += pack_string_subrecord('FULL', name)
    subs += pack_string_subrecord('MODL', _TACT_MODL)
    subs += pack_subrecord('MODT', _TACT_MODT)
    subs += pack_subrecord('PNAM', struct.pack('<I', 0))
    subs += pack_subrecord('FNAM', struct.pack('<H', 0))
    subs += pack_formid_subrecord('VNAM', vtyp_fid)
    return pack_record('TACT', fid, 0, subs)


def build_speaker_activators(by_type: dict, writer, npc_to_vtyp: dict,
                             offset: int) -> int:
    """Mint a TACT + placed REFR for every (emitter, speak-as voice) pair.

    Mutates ``by_type``: appends the new REFRs so the CELL/WRLD builders place
    them.  Must run BEFORE those builders, like the leveled-actor shells.
    Returns the number of speakers built.

    See: docs/commentary/tes5_import_dialogue.md#speaker-activator-construction
    """
    calls = scan_speak_as_calls(by_type)
    pairs = sorted({(e, v) for e, v, _t, _h in calls})
    if not pairs:
        return 0

    refr_by_edid = {}
    for rec in by_type.get('REFR', []):
        e = (get_str(rec, 'EditorID') or '').lower()
        if e:
            refr_by_edid.setdefault(e, rec)
    npc_by_edid = {}
    for sig in ('NPC_', 'CREA'):
        for rec in by_type.get(sig, []):
            e = (get_str(rec, 'EditorID') or '').lower()
            if e:
                npc_by_edid.setdefault(e, rec)

    new_refrs = []
    for emitter, voice in pairs:
        src = refr_by_edid.get(emitter)
        npc = npc_by_edid.get(voice)
        if src is None or npc is None:
            continue
        vtyp = npc_to_vtyp.get(get_formid(npc, 'FormID'))
        if not vtyp:
            continue
        clone = _mint_speaker(writer, emitter, voice, src, npc, vtyp, offset)
        new_refrs.append(clone)

    if new_refrs:
        by_type.setdefault('REFR', []).extend(new_refrs)
    _record_topic_voices(calls, npc_by_edid, _dial_fids(by_type))
    _build_scenes(by_type, writer, calls)
    return len(new_refrs)


def _build_scenes(by_type: dict, writer, calls: list) -> int:
    """`TES4SpeakAs` and one scene per call site whose speaker was minted.

    See: docs/commentary/tes5_import_dialogue.md#speaker-activator-construction
    """
    dial_fids = _dial_fids(by_type)
    sites = sorted({(e, v, t) for e, v, t, _h in calls
                    if (e, v) in _SPEAKER_REFS and t in dial_fids})
    if not sites:
        return 0
    names = {fid: f'TES4VoiceRef_{e}_{v}' for (e, v), fid in _SPEAKER_REFS.items()}
    speakers = sorted({_SPEAKER_REFS[(e, v)] for e, v, _t in sites})
    alias_by_fid = {fid: i for i, fid in enumerate(speakers)}
    quest_fid = writer.derive_formid('SYNTH_QUST', SCENE_QUEST_EDID)
    _SCENE_QUEST[:] = [quest_fid]
    writer.add_record('QUST', _pack_scene_quest(quest_fid, alias_by_fid, names))
    for e, v, t in sites:
        fid = writer.derive_formid('SPEAK_AS_SCEN', f'{e}|{v}|{t}')
        writer.add_record('SCEN', _pack_scene(
            fid, f'TES4SpeakAs_{e}_{v}_{t}', quest_fid,
            alias_by_fid[_SPEAKER_REFS[(e, v)]], dial_fids[t]))
        _SCENES[(e, v, t)] = fid
    return len(sites)


def _pack_scene_quest(fid: int, alias_by_fid: dict, names: dict) -> bytes:
    """The start-game quest owning the scenes: one forced-reference alias per speaker."""
    q = pack_string_subrecord('EDID', SCENE_QUEST_EDID)
    q += pack_string_subrecord('FULL', 'TES4 Speak-As Scenes')
    q += pack_subrecord('DNAM', struct.pack('<HBBII', 0x0011, 0, 0, 0, 0))
    q += pack_subrecord('NEXT', b'')
    q += pack_uint32_subrecord('ANAM', len(alias_by_fid))
    q += quest_aliases(alias_by_fid, {}, names)
    return pack_record('QUST', fid, 0, q)


def _pack_scene(fid: int, edid: str, quest_fid: int, alias_id: int,
                topic_fid: int) -> bytes:
    """One scene in vanilla DA11NamiraScene's exact layout.

    One phase, the speaker alias as its only actor, and one dialogue action
    that speaks the topic.
    """
    phase = (pack_subrecord('HNAM', b'') + pack_string_subrecord('NAM0', '')
             + pack_subrecord('NEXT', b'') + pack_subrecord('NEXT', b'')
             + pack_uint32_subrecord('WNAM', 200) + pack_subrecord('HNAM', b''))
    actor = (pack_uint32_subrecord('ALID', alias_id)
             + pack_uint32_subrecord('LNAM', 0)
             + pack_uint32_subrecord('DNAM', 0x1A))
    action = (pack_uint16_subrecord('ANAM', 0) + pack_string_subrecord('NAM0', '')
              + pack_uint32_subrecord('ALID', alias_id)
              + pack_uint32_subrecord('INAM', 1)
              + pack_uint32_subrecord('SNAM', 0) + pack_uint32_subrecord('ENAM', 0)
              + pack_formid_subrecord('DATA', topic_fid)
              + pack_subrecord('HTID', struct.pack('<i', -1))
              + pack_float_subrecord('DMAX', 10.0)
              + pack_float_subrecord('DMIN', 1.0)
              + pack_uint32_subrecord('DEMO', 0) + pack_uint32_subrecord('DEVA', 0)
              + pack_subrecord('ANAM', b''))
    subs = (pack_string_subrecord('EDID', edid) + pack_uint32_subrecord('FNAM', 4)
            + phase + actor + action + pack_formid_subrecord('PNAM', quest_fid)
            + pack_uint32_subrecord('INAM', 1)
            + pack_subrecord('VNAM', struct.pack('<4I', 3, 3, 3, 3)))
    return pack_record('SCEN', fid, 0, subs)


def _mint_speaker(writer, emitter: str, voice: str, src: dict, npc: dict,
                  vtyp: int, offset: int) -> dict:
    """The TACT plus the placed REFR clone for one (emitter, voice) pair.

    Adds the TACT to `writer` and returns the REFR record, registering its
    FormID in `_SPEAKER_REFS`.  The REFR is a CLONE of the emitter's own, with
    NAME repointed at the new TACT, so cell/position/rotation carry over
    without being re-derived.  Flag 1024 is Persistent.
    """
    key = f'{emitter}|{voice}'
    tact_fid = writer.derive_formid('SPEAKER_TACT', key)
    writer.add_record('TACT', _pack_tact(
        tact_fid, f'TES4Voice_{voice}', vtyp, get_str(npc, 'FULL') or ''))

    refr_fid = writer.derive_formid('SPEAKER_REFR', key)
    clone = dict(src)
    clone['FormID'] = f'{refr_fid & 0x00FFFFFF:06X}'
    clone['EditorID'] = f'TES4VoiceRef_{emitter}_{voice}'
    high = ((tact_fid >> 24) - offset) & 0xFF
    clone['NAME'] = f'{high:02X}{tact_fid & 0x00FFFFFF:06X}'
    for k in list(clone):
        if k.startswith(_CLONE_DROP_PREFIXES):
            del clone[k]
    clone['RecordFlags'] = '1024'
    _SPEAKER_REFS[(emitter, voice)] = refr_fid
    return clone

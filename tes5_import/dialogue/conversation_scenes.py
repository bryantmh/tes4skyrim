"""Emit an NPC-to-NPC conversation chain as a native Skyrim SCEN scene.

The imperative SayLine driver (conversations.py) replays these chains by
`Actor.Say()`-ing each line with a measured wait. For a SELECTED chain this
module instead emits a native multi-phase SCEN driven by forced-ref aliases:
the engine paces the lines and fires each delivered INFO's End fragment
natively. The generated driver quest then only `ForceStart()`s the scene when
the chain's gate is met (generate_driver_psc), so exactly one path delivers the
chain -- the SCEN'd chain no longer Says its lines.

Records emitted (import side, via the shared writer):
  - one scene quest (start-game) with one forced-ref alias per chain actor,
  - one SCEN: one phase + one BGSSceneActionDialogue per line, each action's
    DATA targeting that line's EXISTING (already-voiced) topic, its ALID the
    speaker's alias.

Voice needs no re-home: a scene action inherits the topic's own owning-quest
voice prefix (converter.voice_file_prefix keys on the topic owner, not the
deliverer). The SCEN layout copies vanilla CWDialogueSoldiers02Scene, verified
by the seam probe v4 (GREEN in-game: multi-action End fragments fire per phase,
persistent placed-NPC aliases deliver).

See: docs/commentary/tes5_import_dialogue.md (Step 4) and
     reports-20260928/deepcode/NATIVE_SCENE/BUILD_PLAN.md
"""
import struct

from tes5_import.base.writer import (
    pack_record, pack_subrecord, pack_string_subrecord, pack_formid_subrecord,
    pack_uint32_subrecord, pack_uint16_subrecord, pack_uint8_subrecord,
    pack_float_subrecord)
from tes5_import.dialogue.quest import quest_aliases

#: derive_formid sites (distinct from speak_as's SYNTH_QUST / SPEAK_AS_SCEN).
CONV_SCENE_QUEST_SITE = 'CONV_SCENE_QUST'
CONV_SCEN_SITE = 'CONV_SCEN'


def _pack_scene_quest(fid: int, edid: str, alias_by_fid: dict,
                      names: dict) -> bytes:
    """A start-game scene quest holding one forced-ref alias per chain actor."""
    q = pack_string_subrecord('EDID', edid)
    q += pack_string_subrecord('FULL', 'TES4 NPC Scene Conversation')
    q += pack_subrecord('DNAM', struct.pack('<HBBII', 0x0011, 0, 0, 0, 0))  # SGE
    q += pack_subrecord('NEXT', b'')
    q += pack_uint32_subrecord('ANAM', len(alias_by_fid))
    q += quest_aliases(alias_by_fid, {}, names)
    return pack_record('QUST', fid, 0, q)


def _pack_scene(fid: int, edid: str, quest_fid: int, lines: list) -> bytes:
    """A conversation SCEN: one phase + one dialogue action per line.

    `lines` = [(alias_id, topic_fid), ...] in delivery order. Layout mirrors
    vanilla CWDialogueSoldiers02Scene (EDID FNAM, one phase block per line, one
    actor block per distinct alias, one dialogue action per line, PNAM/INAM/VNAM).
    """
    subs = pack_string_subrecord('EDID', edid) + pack_uint32_subrecord('FNAM', 3)
    for _ in lines:                                     # one phase per line
        subs += (pack_subrecord('HNAM', b'') + pack_uint8_subrecord('NAM0', 0)
                 + pack_subrecord('NEXT', b'') + pack_subrecord('NEXT', b'')
                 + pack_uint32_subrecord('WNAM', 200) + pack_subrecord('HNAM', b''))
    for alias_id in sorted({a for a, _ in lines}):      # one actor block per alias
        subs += (pack_uint32_subrecord('ALID', alias_id)
                 + pack_uint32_subrecord('LNAM', 0)
                 + pack_uint32_subrecord('DNAM', 0x1A))
    for phase, (alias_id, topic_fid) in enumerate(lines):
        subs += (pack_uint16_subrecord('ANAM', 0) + pack_uint8_subrecord('NAM0', 0)
                 + pack_uint32_subrecord('ALID', alias_id)
                 + pack_uint32_subrecord('INAM', phase + 1)
                 + pack_uint32_subrecord('SNAM', phase)
                 + pack_uint32_subrecord('ENAM', phase)
                 + pack_formid_subrecord('DATA', topic_fid)
                 + pack_subrecord('HTID', struct.pack('<i', -1))
                 + pack_float_subrecord('DMAX', 10.0)
                 + pack_float_subrecord('DMIN', 1.0)
                 + pack_uint32_subrecord('DEMO', 0) + pack_uint32_subrecord('DEVA', 0)
                 + pack_subrecord('ANAM', b''))
    subs += (pack_formid_subrecord('PNAM', quest_fid)
             + pack_uint32_subrecord('INAM', len(lines))
             + pack_subrecord('VNAM', struct.pack('<4I', 3, 3, 3, 3)))
    return pack_record('SCEN', fid, 0, subs)


def scene_lines(chain, resolve_hop_topic, synth_topic_fids: dict) -> list:
    """[(alias_id, topic_fid), ...] for a chain: head (speaker A) then each hop.

    alias 0 = speaker A (subj), alias 1 = speaker B (tgt). The head targets the
    synthesized head topic; each hop its resolved (existing) topic. Lines whose
    topic does not resolve (0) are dropped so the SCEN stays valid.
    """
    i = chain['index']
    out = [(0, synth_topic_fids.get(i, 0))]
    for hop in chain['hops']:
        alias_id = 0 if hop['speaker'] == 'A' else 1
        out.append((alias_id, resolve_hop_topic(hop)))
    return [(a, t) for a, t in out if t]


def emit_conversation_scene(writer, chain, remap, resolve_hop_topic,
                            synth_topic_fids: dict) -> int:
    """Emit the scene quest + SCEN for one chain; return the SCEN's output fid.

    Two forced-ref aliases (0 = subj/A, 1 = tgt/B) bound to the chain actors'
    placed refs. Returns 0 (caller falls back to the SayLine path) if the two
    actors collapse to one ref or no line topic resolves.
    """
    i = chain['index']
    a_ref = remap(chain['subj']['ref_fid'])
    b_ref = remap(chain['tgt']['ref_fid'])
    if not a_ref or not b_ref or a_ref == b_ref:
        return 0
    lines = scene_lines(chain, resolve_hop_topic, synth_topic_fids)
    if not lines:
        return 0

    quest_edid = f'TES4NPCScene{i}'
    scen_edid = f'TES4NPCSceneScn{i}'
    quest_fid = writer.derive_formid(CONV_SCENE_QUEST_SITE, quest_edid)
    alias_by_fid = {a_ref: 0, b_ref: 1}
    names = {a_ref: f'SceneActorA{i}', b_ref: f'SceneActorB{i}'}
    writer.add_record('QUST', _pack_scene_quest(quest_fid, quest_edid,
                                                alias_by_fid, names))

    scen_fid = writer.derive_formid(CONV_SCEN_SITE, scen_edid)
    writer.add_record('SCEN', _pack_scene(scen_fid, scen_edid, quest_fid, lines))
    return scen_fid

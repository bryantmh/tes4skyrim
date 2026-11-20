"""The voice types a converted INFO may be spoken in.

A line naming its speakers takes their voice types. A line naming none takes
the voice types holding its recording, else nothing when it names its own
speaker audience, else the topic's voice set.

See: docs/commentary/tes5_import_dialogue.md#recorded-voice-gate
"""

from asset_convert.audio.audio_converter import recorded_voice_types
from output_layout import assets_for

from ..base.conditions import (has_speaker_audience, is_speak_as_record,
                               read_getisid_fids)
from .barks_morrowind import bark_voice_types

#: {INFO FormID low 24 bits: VTYP EditorIDs with a recording}, loaded once per import.
_RECORDED_EDIDS: dict = {}
#: The same index as VTYP FormIDs, bound once the run's VTYPs are known.
_RECORDED_FIDS: dict = {}


def load_recordings(export_dir: str, plugin: str) -> int:
    """Index the plugin's extracted voice recordings; returns the line count."""
    _RECORDED_EDIDS.clear()
    _RECORDED_FIDS.clear()
    if export_dir:
        _RECORDED_EDIDS.update(recorded_voice_types(assets_for(export_dir), plugin))
    return len(_RECORDED_EDIDS)


def bind_recordings(vtyp_edid_by_fid: dict) -> None:
    """Resolve the recordings index to this run's VTYP FormIDs and report misses.

    See: docs/commentary/tes5_import_dialogue.md#recorded-voice-gate
    """
    fid_by_edid = {e.lower(): f for f, e in vtyp_edid_by_fid.items()}
    missing = set()
    _RECORDED_FIDS.clear()
    for fid24, edids in _RECORDED_EDIDS.items():
        fids = {fid_by_edid.get(e.lower()) for e in edids}
        missing |= {e for e in edids if e.lower() not in fid_by_edid}
        if fids - {None}:
            _RECORDED_FIDS[fid24] = fids - {None}
    print(f"    Recorded voice index: {len(_RECORDED_FIDS)} voiced lines"
          + (f"; no VTYP for {sorted(missing)}" if missing else ''))


def recorded_vtyps(info_rec: dict) -> set:
    """VTYP FormIDs holding a recording of this INFO (empty when unvoiced)."""
    raw = info_rec.get('FormID') or '0'
    return set(_RECORDED_FIDS.get(int(raw, 16) & 0xFFFFFF, ()))


def info_voice_types(info_rec: dict, npc_to_vtyp: dict, topic_vtyps,
                     offset: int) -> set:
    """The VTYP FormIDs one INFO's GetIsVoiceType OR-chain allows; empty = no gate.

    A speak-as line has a non-actor speaker with no voice type, so it is never
    gated (keyed on the topic, not on any NPC it names; see talking_activators.py).
    See: docs/commentary/tes5_import_dialogue.md#recorded-voice-gate
    """
    if is_speak_as_record(info_rec):
        return set()
    own_npcs = read_getisid_fids(info_rec, offset=offset, positive_only=True)
    if own_npcs or bark_voice_types(info_rec):
        return ({npc_to_vtyp[n] for n in own_npcs if n in npc_to_vtyp}
                or bark_voice_types(info_rec))
    recorded = recorded_vtyps(info_rec)
    if recorded:
        return recorded
    if has_speaker_audience(info_rec):
        return set()
    return set(topic_vtyps)

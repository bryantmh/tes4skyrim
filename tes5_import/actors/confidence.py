"""TES4 Confidence in Skyrim: Cowardly or Foolhardy, switched by Oblivion's own flee rule.

Oblivion flees when its flee score (Confidence and its own health) beats the
actor's best attack score; Skyrim's tiers 0-3 compare its strength with its
enemy's, which no Oblivion actor did. Every converted actor is therefore
Cowardly (0) or Foolhardy (4). The margin between the two scores is a rank in
a hidden faction, and a conditioned ability switches the tier at the health
where Oblivion would start to flee.

See: docs/commentary/tes5_import_actors.md#flee-margin
"""

import math
import struct

from ..base.conditions import build_ctda
from ..base.owned_records import clamp_rank, hidden_faction
from ..base.text_reader import get_int
from ..base.writer import (PluginWriter, pack_formid_subrecord, pack_obnd,
                           pack_record, pack_string_subrecord, pack_subrecord)
from .attack_score import attack_score, index_attack_sources
from .combat_style import fleeing_disabled, game_settings

#: The hidden faction whose rank is the actor's authored TES4 Confidence.
FACTION_EDID = 'TES4ConfidenceFaction'

#: The hidden faction whose rank is the actor's flee margin.
MARGIN_FACTION_EDID = 'TES4FleeMarginFaction'

#: The Constant global holding Q, the margin at which fleeing stops.
SCALE_EDID = 'TES4FleeHealthScale'

#: The constant ability whose effects fire once the owner's health is low enough.
FLEE_SPELL_EDID = 'TES4ConfidenceFlee'

#: The ability's script effect and the ActiveMagicEffect script it carries.
FLEE_EFFECT_EDID = 'TES4ConfidenceFleeEffect'
FLEE_SCRIPT = 'TES4_ConfidenceFlee'

#: wbConfidenceEnum tiers the conversion writes.
TIER_COWARDLY, TIER_FOOLHARDY = 0, 4

#: Oblivion.exe compiled-in values of the flee score's game settings.
_EXE_SETTINGS = {'fAIFleeConfBase': 40.0, 'fAIFleeConfMult': -0.5, 'fAIFleeHealthMult': 20.0,
                 'fAICombatFleeScoreThreshold': 10.0}

#: A faction rank is a signed byte.
_RANK_MAX = 127

#: CTDA functions and the Health actor value (vanilla GetActorValuePercent(00000018)).
_FUNC_GET_FACTION_RANK, _FUNC_GET_AV_PERCENT, _AV_HEALTH = 73, 640, 24

#: CTDA comparison operators == and <.
_OP_EQ, _OP_LT = 0x00, 0x80

#: MGEF flags of vanilla's constant self script holders (WereFXFeedBloodHolder): Hide in UI | FX Persist | No Duration.
_EFFECT_FLAGS = 0x9200

#: MGEF archetype 1 Script; casting 0 Constant Effect; delivery 0 Self; SPIT type 4 Ability.
_ARCHETYPE_SCRIPT, _CONSTANT, _SELF, _ABILITY = 1, 0, 0, 4

#: ETYP EitherHand, which vanilla abilities carry.
_EITHER_HAND = 0x00013F44

#: GLOB record flag Constant, which 132 of Skyrim.esm's 664 globals carry.
_GLOB_CONSTANT = 0x40

#: FormIDs of this run's records (0 until created or adopted).
_FIDS = {FACTION_EDID: 0, MARGIN_FACTION_EDID: 0, SCALE_EDID: 0, FLEE_SPELL_EDID: 0}

#: Run state: TES4 source?, the flee settings in effect, and Q.
_STATE = {'active': False, 'settings': dict(_EXE_SETTINGS), 'scale': 20.0}


# ---------------------------------------------------------------------------
# The margin
# ---------------------------------------------------------------------------

def _confidence(rec: dict) -> int:
    """The actor's authored TES4 Confidence."""
    return get_int(rec, 'AIDT.Confidence')


def _slope() -> float:
    """-fAIFleeConfMult: the flee score one Confidence point is worth."""
    return -_STATE['settings']['fAIFleeConfMult']


def flee_margin(rec: dict) -> int:
    """Confidence + (best attack - fAIFleeConfBase) / -fAIFleeConfMult, the attack term capped at Q.

    Below 0 the actor flees on sight, at Q or more never; in between once
    health falls under 1 - margin/Q. Fleeing Disabled puts the attack term at Q.
    See: docs/commentary/tes5_import_actors.md#flee-margin
    """
    s, scale = _STATE['settings'], _STATE['scale']
    if fleeing_disabled(rec):
        term = scale
    else:
        best = max(s['fAICombatFleeScoreThreshold'], attack_score(rec))
        term = min(scale, (best - s['fAIFleeConfBase']) / _slope())
    return clamp_rank(_confidence(rec) + term)


def confidence_tier(rec: dict) -> int:
    """Cowardly if the actor flees on sight, else Foolhardy."""
    if not _STATE['active']:
        return TIER_COWARDLY if _confidence(rec) <= 0 else TIER_FOOLHARDY
    return TIER_COWARDLY if flee_margin(rec) < 0 else TIER_FOOLHARDY


def _in_window(margin: int) -> bool:
    """True when the margin flees only below some health."""
    return 0 <= margin < _STATE['scale']


def flee_memberships(rec: dict) -> list:
    """[(faction FormID, rank)]: the authored Confidence and the flee margin."""
    if not (_STATE['active'] and _FIDS[FACTION_EDID] and _FIDS[MARGIN_FACTION_EDID]):
        return []
    return [(_FIDS[FACTION_EDID], min(_RANK_MAX, _confidence(rec))),
            (_FIDS[MARGIN_FACTION_EDID], flee_margin(rec))]


def flee_spells(rec: dict) -> list:
    """The flee ability, for an actor whose margin flees below some health."""
    fid = _FIDS[FLEE_SPELL_EDID]
    return [fid] if _STATE['active'] and fid and _in_window(flee_margin(rec)) else []


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def _scale_global(fid: int) -> bytes:
    """The Constant float global holding Q."""
    subs = pack_string_subrecord('EDID', SCALE_EDID)
    subs += pack_subrecord('FNAM', struct.pack('<B', ord('f')))
    subs += pack_subrecord('FLTV', struct.pack('<f', _STATE['scale']))
    return pack_record('GLOB', fid, _GLOB_CONSTANT, subs)


def _effect(fid: int) -> bytes:
    """The constant self Script effect carrying TES4_ConfidenceFlee."""
    from script_convert.pipeline import build_vmad_object_script
    data = bytearray(152)
    struct.pack_into('<I', data, 0, _EFFECT_FLAGS)
    struct.pack_into('<ii', data, 12, -1, -1)
    struct.pack_into('<Ii', data, 64, _ARCHETYPE_SCRIPT, -1)
    struct.pack_into('<II', data, 80, _CONSTANT, _SELF)
    struct.pack_into('<i', data, 88, -1)
    struct.pack_into('<f', data, 104, 1.0)
    subs = pack_string_subrecord('EDID', FLEE_EFFECT_EDID)
    subs += pack_subrecord('VMAD', build_vmad_object_script(FLEE_SCRIPT, {
        MARGIN_FACTION_EDID: _FIDS[MARGIN_FACTION_EDID], SCALE_EDID: _FIDS[SCALE_EDID]}))
    subs += pack_subrecord('DATA', bytes(data))
    return pack_record('MGEF', fid, 0, subs)


def _threshold_effect(effect: int, rank: int) -> bytes:
    """Effect k: active while the margin is k and health is under 1 - k/Q."""
    faction = _FIDS[MARGIN_FACTION_EDID]
    subs = pack_formid_subrecord('EFID', effect)
    subs += pack_subrecord('EFIT', struct.pack('<fII', 0.0, 0, 0))
    subs += pack_subrecord('CTDA', build_ctda(
        _FUNC_GET_AV_PERCENT, _AV_HEALTH, 0, 1.0 - rank / _STATE['scale'], _OP_LT))
    return subs + pack_subrecord('CTDA', build_ctda(
        _FUNC_GET_FACTION_RANK, faction, 0, float(rank), _OP_EQ))


def _ability(fid: int, effect: int) -> bytes:
    """The constant ability with one threshold effect per margin 0 .. ceil(Q)-1."""
    subs = pack_string_subrecord('EDID', FLEE_SPELL_EDID)
    subs += pack_obnd()
    subs += pack_subrecord('ETYP', struct.pack('<I', _EITHER_HAND))
    subs += pack_subrecord('SPIT', struct.pack(
        '<IIIfII12x', 0, 0, _ABILITY, 0.0, _CONSTANT, _SELF))
    for rank in range(math.ceil(_STATE['scale'])):
        subs += _threshold_effect(effect, rank)
    return pack_record('SPEL', fid, 0, subs)


def _read_settings(by_type: dict, master_export: dict) -> None:
    """The flee settings in effect and Q = fAIFleeHealthMult / -fAIFleeConfMult, capped to a rank."""
    settings = game_settings(by_type, master_export, _EXE_SETTINGS)[0]
    if settings['fAIFleeConfMult'] >= 0:
        print(f"  WARNING: fAIFleeConfMult {settings['fAIFleeConfMult']} makes Confidence "
              f"meaningless; using the exe value {_EXE_SETTINGS['fAIFleeConfMult']}")
        settings['fAIFleeConfMult'] = _EXE_SETTINGS['fAIFleeConfMult']
    _STATE['settings'] = settings
    _STATE['scale'] = min(float(_RANK_MAX), settings['fAIFleeHealthMult'] / _slope())


def create_confidence_records(writer: PluginWriter, by_type: dict, master_export: dict,
                              master_index=None, wanted: bool = True) -> dict:
    """The factions, Q global and flee ability, adopted from a master that has them.

    Returns {EditorID: FormID} for WELL_KNOWN_PROPERTIES; {} when not `wanted`
    (a Morrowind or FO3/FNV source, whose actors carry no margin).
    See: docs/commentary/tes5_import_actors.md#morrowind-flee
    """
    _FIDS.update(dict.fromkeys(_FIDS, 0))
    _STATE['active'] = wanted
    if not wanted:
        return {}
    _read_settings(by_type, master_export)
    index_attack_sources(by_type, master_export)
    sigs = {FACTION_EDID: b'FACT', MARGIN_FACTION_EDID: b'FACT', SCALE_EDID: b'GLOB',
            FLEE_SPELL_EDID: b'SPEL'}
    found = {edid: (master_index.find_by_edid(sig, edid) if master_index is not None else 0)
             for edid, sig in sigs.items()}
    if not all(found.values()):
        found = {edid: writer.derive_formid(sig.decode(), edid) for edid, sig in sigs.items()}
        _FIDS.update(found)
        effect = writer.derive_formid('MGEF', FLEE_EFFECT_EDID)
        for edid in (FACTION_EDID, MARGIN_FACTION_EDID):
            writer.add_record('FACT', hidden_faction(found[edid], edid))
        writer.add_record('GLOB', _scale_global(found[SCALE_EDID]))
        writer.add_record('MGEF', _effect(effect))
        writer.add_record('SPEL', _ability(found[FLEE_SPELL_EDID], effect))
    _FIDS.update(found)
    return dict(found)

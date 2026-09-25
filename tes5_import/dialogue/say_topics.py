"""
Which topics a SCRIPT speaks, and how their RunOn=Target tests convert.

Skyrim's `Say` has no dialogue target, so a topic driven by `Say`, `SayTo` or
`StartConversation` needs its target conditions retargeted or dropped, has to
survive the NPC-to-NPC drop, and must stay off the player's menu.

See: docs/commentary/tes5_import_dialogue.md#script-driven-type-1-topics
"""

import re
from collections import defaultdict

from ..base.text_reader import info_result_script
from ..record_types.common import get_formid, get_str
from .say_morrowind import say_topic_fids

#: The player reference, which a `player` target token names.
_PLAYER_FORMID = 0x14

#: Say-driven topics: raw24 DIAL fid -> ('ref', fid) or ('drop', None) for RunOn=Target.
SAY_TOPIC_DISPOSITIONS: dict = {}

_SAYTO_RE = re.compile(r'\bsayto[\s,]+(\w+)[\s,]+(\w+)', re.IGNORECASE)
_SAY_RE = re.compile(r'\bsay[\s,]+(\w+)', re.IGNORECASE)
_STARTCONV_RE = re.compile(r'\bstartconversation[\s,]+(\w+)(?:[\s,]+(\w+))?',
                           re.IGNORECASE)


def build_say_topic_dispositions(by_type: dict) -> dict:
    """raw24 DIAL fid -> ('ref', fid) or ('drop', None) per script-driven topic.

    A topic whose call sites name ONE target has its RunOn=Target conditions
    retargeted onto that reference; mixed or unresolvable targets drop them,
    as does a Morrowind `Say` topic, which has no target at all.
    See: docs/commentary/tes5_import_dialogue.md#script-driven-type-1-topics
    """
    votes = _scan_say_votes(collect_script_texts(by_type),
                            *_index_say_targets(by_type))
    out = dict.fromkeys(say_topic_fids(by_type), ('drop', None))
    for dfid, tgts in votes.items():
        real = {t for t in tgts if t is not None}
        if len(real) == 1:
            out[dfid] = ('ref', next(iter(real)))
        else:
            out[dfid] = ('drop', None)
    return out


def collect_script_texts(by_type: dict) -> list:
    """Every script body that can hold a call site: SCPT sources, INFO result
    scripts, and each QUST stage log's result script, in that order."""
    texts = [get_str(r, 'SCTX') or '' for r in by_type.get('SCPT', [])]
    for r in by_type.get('INFO', []):
        texts.append(info_result_script(r))
    for r in by_type.get('QUST', []):
        i = 0
        while f'Stage[{i}].Index' in r:
            j = 0
            while (t := r.get(f'Stage[{i}].Log[{j}].ResultScript')) is not None:
                texts.append(t)
                j += 1
            i += 1
    return texts


def _index_say_targets(by_type: dict) -> tuple:
    """(lowercased DIAL EditorID -> raw24 fid, token -> target ref fid).

    The second resolves a call site's target token: 'player'/'playerref' to the
    player ref, any ACHR/ACRE/REFR EditorID to that ref's FormID, and anything
    else to None (unresolvable).
    """
    dial_by_edid = {get_str(d, 'EditorID', '').lower():
                    get_formid(d, 'FormID') & 0xFFFFFF
                    for d in by_type.get('DIAL', [])
                    if get_str(d, 'EditorID')}
    ref_by_edid = {}
    for sig in ('ACHR', 'ACRE', 'REFR'):
        for r in by_type.get(sig, []):
            e = get_str(r, 'EditorID')
            if e:
                ref_by_edid[e.lower()] = get_formid(r, 'FormID')
    return dial_by_edid, ref_by_edid


def _scan_say_votes(texts: list, dial_by_edid: dict,
                    ref_by_edid: dict) -> dict:
    """raw24 DIAL fid -> the set of targets its call sites name (None = none).

    SayTo/StartConversation vote their target ref; plain Say votes None because
    it has no target at all. SayTo matches are stripped before the Say scan so
    one call site is never counted as both.
    """
    def target_fid(token: str):
        """The ref FormID a target token names, or None when unresolvable."""
        t = token.lower()
        if t in ('player', 'playerref'):
            return _PLAYER_FORMID
        return ref_by_edid.get(t)

    votes = defaultdict(set)
    for text in texts:
        if not text:
            continue
        for raw in text.replace('\\r\\n', '\n').splitlines():
            line = raw.split(';', 1)[0]
            low = line.lower()
            if 'say' not in low and 'startconversation' not in low:
                continue
            _scan_say_line(line, votes, dial_by_edid, target_fid)
    return votes


def _scan_say_line(line: str, votes: dict, dial_by_edid: dict,
                   target_fid) -> None:
    """Add one script line's SayTo / StartConversation / Say votes to `votes`."""
    for m in _SAYTO_RE.finditer(line):
        d = dial_by_edid.get(m.group(2).lower())
        if d is not None:
            votes[d].add(target_fid(m.group(1)))
    for m in _STARTCONV_RE.finditer(line):
        if m.group(2):
            d = dial_by_edid.get(m.group(2).lower())
            if d is not None:
                votes[d].add(target_fid(m.group(1)))
    stripped = _SAYTO_RE.sub(' ', line)
    for m in _SAY_RE.finditer(stripped):
        d = dial_by_edid.get(m.group(1).lower())
        if d is not None:
            votes[d].add(None)

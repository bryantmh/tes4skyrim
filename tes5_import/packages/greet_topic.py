"""The quest whose GREETING a force-greet opens when the package's conditions name none.

Oblivion's force-greet speaks the speaker's GREETING from whichever quest holds a
valid line; Skyrim's ForceGreet needs one topic, and each quest's GREETING lines
are their own topic. So the topic is the quest that owns the speaker's GREETING
lines, and when several do, the one whose lines test the stage that forces the
package and pass at it.
"""

import re
import struct

from ..base.master_export import records_of
from ..base.text_reader import get_formid, get_int, get_str, remap_formid

#: Oblivion's GREETING topic (DIAL 000000C8).
GREETING_DIAL = 0xC8

#: TES4 condition functions GetIsID and GetIsCreature, which name the speaker.
_SPEAKER_FUNCS = (72, 73)

#: TES4 condition function GetStage.
_GET_STAGE = 58

#: TES4 condition functions whose first parameter is a quest.
_QUEST_FUNCS = (56, 58, 59, 79)

#: TES4 compare operators, by the top three bits of the CTDA type byte.
_OPS = {0: lambda a, b: a == b, 1: lambda a, b: a != b, 2: lambda a, b: a > b,
        3: lambda a, b: a >= b, 4: lambda a, b: a < b, 5: lambda a, b: a <= b}

_FORCE_RE = re.compile(r'AddScriptPackage[\s,]+"?(\w+)"?', re.I)

_LOW = 0x00FFFFFF


def _records(by_type: dict, master_export, sig: str):
    """The masters' records of `sig`, then the plugin's own."""
    yield from (rec for _key, rec in records_of(master_export, sig))
    yield from by_type.get(sig, [])


def _ctdas(rec: dict) -> list:
    """(op, value, function, param1) of each TES4 condition of `rec`."""
    out = []
    i = 0
    while (raw := rec.get(f'Condition[{i}].Raw')) is not None:
        i += 1
        try:
            d = bytes.fromhex(raw)
            out.append((d[0] >> 5, struct.unpack_from('<f', d, 4)[0],
                        struct.unpack_from('<I', d, 8)[0], struct.unpack_from('<I', d, 12)[0]))
        except (ValueError, struct.error, IndexError):
            continue
    return out


def _speaker_lines(by_type: dict, master_export) -> dict:
    """speaker FormID (low 24 bits) -> [(quest raw FormID, conditions)] of its GREETING lines."""
    out = {}
    for rec in _records(by_type, master_export, 'INFO'):
        try:
            if int(rec.get('ParentDIAL') or '0', 16) & _LOW != GREETING_DIAL:
                continue
            quest = int(rec.get('QSTI.Quest') or '0', 16)
        except ValueError:
            continue
        conds = _ctdas(rec)
        for _op, _val, func, param in conds:
            if func in _SPEAKER_FUNCS and param:
                out.setdefault(param & _LOW, []).append((quest, conds))
    return out


def _forcing_stages(by_type: dict, master_export) -> dict:
    """package EditorID (lower) -> [(quest raw FormID, stage)] whose stage result forces it."""
    out = {}
    for rec in _records(by_type, master_export, 'QUST'):
        qfid = int(rec.get('FormID') or '0', 16)
        s = 0
        while f'Stage[{s}].Index' in rec:
            stage = get_int(rec, f'Stage[{s}].Index')
            for j in range(max(get_int(rec, f'Stage[{s}].LogCount'), 1)):
                text = get_str(rec, f'Stage[{s}].Log[{j}].ResultScript') or ''
                for m in _FORCE_RE.finditer(text):
                    out.setdefault(m.group(1).lower(), []).append((qfid, stage))
            s += 1
    return out


def _forcing_script_quests(by_type: dict, master_export) -> dict:
    """package EditorID (lower) -> quests (low 24 bits) named by the scripts that force it."""
    quests = {(r.get('EditorID') or '').lower(): int(r.get('FormID') or '0', 16) & _LOW
              for r in _records(by_type, master_export, 'QUST')}
    out = {}
    for rec in _records(by_type, master_export, 'SCPT'):
        text = get_str(rec, 'SCTX') or ''
        forced = [m.group(1).lower() for m in _FORCE_RE.finditer(text)]
        if forced:
            named = {quests[w] for w in re.findall(r'\w+', text.lower()) if w in quests}
            for edid in forced:
                out.setdefault(edid, set()).update(named)
    return out


def _valid_at(conds: list, quest: int, stage: int) -> bool:
    """Do `conds` test `quest`'s stage, and pass every such test at `stage`?"""
    tests = [(op, val) for op, val, func, param in conds
             if func == _GET_STAGE and param & _LOW == quest & _LOW]
    return bool(tests) and all(_OPS.get(op, _OPS[0])(stage, val) for op, val in tests)


def _names_quest(conds: list, named: set) -> bool:
    """Does a condition in `conds` name one of the `named` quests?"""
    return any(f in _QUEST_FUNCS and p & _LOW in named for _o, _v, f, p in conds)


class GreetTopics:
    """Resolves the GREETING quest of a force-greet package from its speakers."""

    def __init__(self, by_type: dict, master_export):
        """Index every GREETING line by speaker, every forced package, every placed actor's base."""
        self.lines = _speaker_lines(by_type, master_export)
        self.forced = _forcing_stages(by_type, master_export)
        self.scripts = _forcing_script_quests(by_type, master_export)
        self.base_of = {get_formid(r, 'FormID') & _LOW: get_formid(r, 'NAME') & _LOW
                        for sig in ('ACHR', 'ACRE') for r in _records(by_type, master_export, sig)}

    def quest_of(self, edid: str, actors, named) -> int:
        """The converted quest FormID whose GREETING the speakers say (`named`: the package's quests), else 0."""
        edid = (edid or '').lower()
        entries = [e for a in actors for e in self.lines.get(self.base_of.get(a & _LOW, a & _LOW), ())]
        raw = {q & _LOW: q for q, _ in entries}
        named = {q & _LOW for q in named if q} | self.scripts.get(edid, set())
        picks = [named & set(raw), set(raw)]
        picks += [{q & _LOW for q, c in entries if _valid_at(c, fq, st)} for fq, st in self.forced.get(edid, ())]
        picks.append({q & _LOW for q, c in entries if _names_quest(c, named)})
        one = next((p for p in picks if len(p) == 1), set())
        return remap_formid(raw[one.pop()]) if one else 0

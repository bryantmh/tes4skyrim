#!/usr/bin/env python3
"""Oblivion dialogue engine emulator -- the TES4 counterpart of dialog_emulator.py.

Reads the TES4 text export directly (never the converted output) and works out
what dialogue an NPC would actually be offered in Oblivion. The point is
comparison: run the same NPC through this and through the Skyrim emulator, and
any difference is a conversion bug rather than a matter of opinion.

Behaviour here follows the tables read out of an unpacked Oblivion.exe by
tools/oblivion_engine_extract.py, so the condition function numbering, the
dialogue type names and the parameter types are the engine's own. See
docs/dialogue_engine_contracts.md.

The two things Oblivion does that Skyrim has no equivalent for, and that
therefore drive most conversion bugs:

  * AddTopic. A conversation topic is invisible until something adds it -- an
    INFO's AddTopic list, an `AddTopic X` in a result script, or a quest-stage
    script. The converter re-expresses this as TES4Unlock_<topic> globals; this
    emulator models the real thing, so `--unlock-all` here corresponds to
    `--unlock-all` there.

  * Topic-level quest ownership. A DIAL belongs to several quests at once
    (QuestCount/Quest[i]), and an INFO names one (QSTI). Skyrim moved ownership
    onto the topic alone.

Usage:
    python tools/oblivion_dialog_emulator.py export/Oblivion.esm \\
        --npc PinarusInventius
    python tools/oblivion_dialog_emulator.py export/Oblivion.esm \\
        --npc PinarusInventius --stage FGC01Rats:40
    python tools/oblivion_dialog_emulator.py export/Oblivion.esm --list-npcs rat
"""

import argparse
import json
import os
import re
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --------------------------------------------------------------------------
# Engine tables
# --------------------------------------------------------------------------
_TABLES = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'tes4_export', 'oblivion_engine_tables.json')


def _load_tables():
    with open(_TABLES, encoding='utf-8') as f:
        t = json.load(f)
    funcs = {f['ctda_index']: f for f in t['functions']
             if f.get('ctda_index') is not None}
    return ({i: f['name'] for i, f in funcs.items()},
            funcs,
            {int(k): v for k, v in t['categories'].items()})


FUNC_NAMES, ENGINE_FUNCTIONS, CATEGORY_NAMES = _load_tables()

# Condition functions this emulator understands. Indices are the engine's
# (opcode - 0x1000), identical numbering to Skyrim's -- which is exactly why a
# side-by-side comparison of the two emulators is meaningful.
F_GETDISTANCE = 1
F_GETITEMCOUNT = 47
F_GETTALKEDTOPC = 50
F_GETSCRIPTVARIABLE = 53
F_GETQUESTRUNNING = 56
F_GETSTAGE = 58
F_GETSTAGEDONE = 59
F_GETINCELL = 67
F_GETISCLASS = 68
F_GETISRACE = 69
F_GETISSEX = 70
F_GETINFACTION = 71
F_GETISID = 72
F_GETFACTIONRANK = 73
F_GETGLOBALVALUE = 74
F_GETDISPOSITION = 76
F_GETRANDOMPERCENT = 77
F_GETQUESTVARIABLE = 79
F_GETLEVEL = 80
F_GETDEADCOUNT = 84
F_ISGUARD = 125
F_GETISCURRENTPACKAGE = 161
F_GETISPLAYABLERACE = 254
F_GETPLAYERINSEWORLD = 365


def _verify_function_indices():
    """Fail loudly if a constant here disagrees with Oblivion.exe."""
    expected = {
        'F_GETDISTANCE': 'GetDistance', 'F_GETITEMCOUNT': 'GetItemCount',
        'F_GETTALKEDTOPC': 'GetTalkedToPC',
        'F_GETSCRIPTVARIABLE': 'GetScriptVariable',
        'F_GETQUESTRUNNING': 'GetQuestRunning', 'F_GETSTAGE': 'GetStage',
        'F_GETSTAGEDONE': 'GetStageDone', 'F_GETINCELL': 'GetInCell',
        'F_GETISCLASS': 'GetIsClass', 'F_GETISRACE': 'GetIsRace',
        'F_GETISSEX': 'GetIsSex', 'F_GETINFACTION': 'GetInFaction',
        'F_GETISID': 'GetIsID', 'F_GETFACTIONRANK': 'GetFactionRank',
        'F_GETGLOBALVALUE': 'GetGlobalValue',
        'F_GETDISPOSITION': 'GetDisposition',
        'F_GETRANDOMPERCENT': 'GetRandomPercent',
        'F_GETQUESTVARIABLE': 'GetQuestVariable', 'F_GETLEVEL': 'GetLevel',
        'F_GETDEADCOUNT': 'GetDeadCount', 'F_ISGUARD': 'IsGuard',
        'F_GETISCURRENTPACKAGE': 'GetIsCurrentPackage',
        'F_GETISPLAYABLERACE': 'GetIsPlayableRace',
        'F_GETPLAYERINSEWORLD': 'GetPlayerInSEWorld',
    }
    wrong = [f'{c}={globals()[c]} is {FUNC_NAMES.get(globals()[c])}, not {w}'
             for c, w in expected.items()
             if FUNC_NAMES.get(globals()[c]) not in (None, w)]
    if wrong:
        raise AssertionError('condition indices disagree with Oblivion.exe: '
                             + '; '.join(wrong))


_verify_function_indices()

# DIAL DATA.Type, verified against all 3,817 vanilla Oblivion DIAL records.
TYPE_TOPIC = 0
TYPE_CONVERSATION = 1
TYPE_COMBAT = 2
TYPE_PERSUASION = 3
TYPE_DETECTION = 4
TYPE_SERVICE = 5
TYPE_MISC = 6

# INFO DATA.Flags
INFO_GOODBYE = 1 << 0
INFO_RANDOM = 1 << 1
INFO_SAYONCE = 1 << 2
INFO_RUNIMMEDIATELY = 1 << 3
INFO_INFOREFUSAL = 1 << 4
INFO_RANDOMEND = 1 << 5
INFO_RUNFORRUMORS = 1 << 6

# Oblivion's fixed-FormID dialogue channels. The engine's type-name table names
# these individually (GREETING sits apart from the HELLO/GOODBYE/ANY group), and
# they are hardcoded ids in every copy of the game. GREETING is DATA.Type 0 --
# the same value ordinary conversation topics use -- so it can only be told
# apart from a real menu topic by its FormID, not by its type.
DIAL_GREETING = 0x000000C8
DIAL_HELLO = 0x000000D2
DIAL_GOODBYE = 0x000000D4
DIAL_INFOGENERAL = 0x000000D7
CHANNEL_NAMES = {
    DIAL_GREETING: 'GREETING',
    DIAL_HELLO: 'HELLO',
    DIAL_GOODBYE: 'GOODBYE',
    DIAL_INFOGENERAL: 'INFO GENERAL',
}


# --------------------------------------------------------------------------
# Export reader
# --------------------------------------------------------------------------

def _records(path):
    """Yield {key: value} for each record in a KEY=VALUE export file."""
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    for chunk in text.split('---RECORD_BEGIN---')[1:]:
        chunk = chunk.split('---RECORD_END---')[0]
        rec = {}
        for line in chunk.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            rec[k] = v
        if rec:
            yield rec


def _int(rec, key, default=0):
    v = rec.get(key)
    if v is None:
        return default
    v = v.split()[0]
    try:
        return int(v, 16) if len(v) == 8 and not v.isdigit() else int(v)
    except ValueError:
        try:
            return int(v, 16)
        except ValueError:
            return default


def _fid(rec, key):
    v = rec.get(key)
    if not v:
        return 0
    try:
        return int(v.split()[0], 16)
    except ValueError:
        return 0


@dataclass
class Condition:
    """A TES4 CTDA. 24 bytes: type, 3 pad, float value, u32 function, 2 params.

    Offsets confirmed by decoding vanilla records -- the comparison value is a
    FLOAT at offset 4 and the function index a u32 at offset 8, which is not
    the same layout as Skyrim's.
    """
    type_byte: int
    comp_type: int
    is_or: bool
    func_idx: int
    comp_value: float
    param1: int
    param2: int

    @classmethod
    def parse(cls, raw: str):
        try:
            b = bytes.fromhex(raw)
        except ValueError:
            return None
        if len(b) < 16:
            return None
        t = b[0]
        value, func = struct.unpack_from('<fI', b, 4)
        p1, p2 = struct.unpack_from('<II', b, 12) if len(b) >= 20 else (0, 0)
        return cls(type_byte=t, comp_type=(t >> 5) & 0x7, is_or=bool(t & 0x01),
                   func_idx=func, comp_value=value, param1=p1, param2=p2)

    @property
    def name(self):
        return FUNC_NAMES.get(self.func_idx, f'Func{self.func_idx}')


@dataclass
class DIAL:
    form_id: int
    editor_id: str
    full_name: str
    dial_type: int
    quests: list


@dataclass
class INFO:
    form_id: int
    parent_dial: int
    dial_type: int
    flags: int
    quest: int
    conditions: list
    responses: list          # (text, emotion_type, emotion_value)
    choices: list            # Choice[i] -- topics this line offers as replies
    add_topics: list         # AddTopic[i] -- topics this line reveals
    result_script: str
    # Set during a walk: this line survived only because some condition could
    # not be decided statically (GetRandomPercent, a script variable, ...).
    runtime_gated: bool = False
    unknown_conditions: list = field(default_factory=list)

    @property
    def say_once(self):
        return bool(self.flags & INFO_SAYONCE)

    @property
    def goodbye(self):
        return bool(self.flags & INFO_GOODBYE)


@dataclass
class NPC:
    form_id: int
    editor_id: str
    name: str
    race: int
    is_female: bool
    level: int
    class_fid: int
    factions: dict = field(default_factory=dict)
    packages: list = field(default_factory=list)
    script: int = 0


@dataclass
class QUST:
    form_id: int
    editor_id: str
    full_name: str
    flags: int
    priority: int
    stages: list


class OblivionDB:
    def __init__(self, export_dir):
        self.dir = export_dir
        self.dials = {}
        self.infos = {}
        self.npcs = {}
        self.qusts = {}
        self.globs = {}
        self.glob_values = {}
        self.facts = {}
        self.cells = {}
        self.races = {}
        self.classes = {}
        self.infos_by_dial = defaultdict(list)
        self.npc_by_edid = {}
        self.quest_by_edid = {}
        self.glob_by_edid = {}
        self._load()

    def _p(self, name):
        return os.path.join(self.dir, name + '.txt')

    def _load(self):
        print(f'Loading {self.dir}...', file=sys.stderr)
        for r in _records(self._p('DIAL')):
            fid = _fid(r, 'FormID')
            quests = [_fid(r, f'Quest[{i}]')
                      for i in range(_int(r, 'QuestCount'))]
            self.dials[fid] = DIAL(fid, r.get('EditorID', ''),
                                   r.get('FULL', ''), _int(r, 'DATA.Type'),
                                   quests)
        for r in _records(self._p('INFO')):
            fid = _fid(r, 'FormID')
            conds = []
            for i in range(_int(r, 'ConditionCount')):
                c = Condition.parse(r.get(f'Condition[{i}].Raw', ''))
                if c:
                    conds.append(c)
            responses = [(r.get(f'Response[{i}].ResponseText', ''),
                          _int(r, f'Response[{i}].EmotionType'),
                          _int(r, f'Response[{i}].EmotionValue'))
                         for i in range(_int(r, 'ResponseCount'))]
            info = INFO(fid, _fid(r, 'ParentDIAL'), _int(r, 'DATA.DialogType'),
                        _int(r, 'DATA.Flags'), _fid(r, 'QSTI.Quest'), conds,
                        responses,
                        [_fid(r, f'Choice[{i}]')
                         for i in range(_int(r, 'ChoiceCount'))],
                        [_fid(r, f'AddTopic[{i}]')
                         for i in range(_int(r, 'AddTopicCount'))],
                        r.get('ResultScript', ''))
            self.infos[fid] = info
            self.infos_by_dial[info.parent_dial].append(info)
        for r in _records(self._p('NPC_')):
            fid = _fid(r, 'FormID')
            facs = {}
            for i in range(_int(r, 'FactionCount')):
                ffid = _fid(r, f'Faction[{i}].FormID')
                if ffid:
                    facs[ffid] = _int(r, f'Faction[{i}].Rank')
            npc = NPC(fid, r.get('EditorID', ''), r.get('FULL', ''),
                      _fid(r, 'RNAM.Race'),
                      # ACBS.Flags bit 0 (0x1) is Female in TES4.
                      bool(_int(r, 'ACBS.Flags') & 0x1),
                      _int(r, 'ACBS.Level'), _fid(r, 'CNAM.Class'), facs,
                      [_fid(r, f'AIPackage[{i}]')
                       for i in range(_int(r, 'AIPackageCount'))],
                      _fid(r, 'SCRI'))
            self.npcs[fid] = npc
            if npc.editor_id:
                self.npc_by_edid[npc.editor_id.lower()] = npc
        for r in _records(self._p('QUST')):
            fid = _fid(r, 'FormID')
            stages = [_int(r, f'Stage[{i}].Index')
                      for i in range(_int(r, 'StageCount'))]
            q = QUST(fid, r.get('EditorID', ''), r.get('FULL', ''),
                     _int(r, 'DATA.Flags'), _int(r, 'DATA.Priority'), stages)
            self.qusts[fid] = q
            if q.editor_id:
                self.quest_by_edid[q.editor_id.lower()] = q
        for r in _records(self._p('GLOB')):
            fid = _fid(r, 'FormID')
            name = r.get('EditorID', '')
            self.globs[fid] = name
            self.glob_by_edid[name.lower()] = fid
            try:
                self.glob_values[fid] = float(r.get('FLTV', '0').split()[0])
            except ValueError:
                self.glob_values[fid] = 0.0
        for r in _records(self._p('FACT')):
            self.facts[_fid(r, 'FormID')] = r.get('EditorID', '')
        for r in _records(self._p('CELL')):
            self.cells[_fid(r, 'FormID')] = r.get('EditorID', '')
        for r in _records(self._p('RACE')):
            self.races[_fid(r, 'FormID')] = r.get('EditorID', '')
        for r in _records(self._p('CLAS')):
            self.classes[_fid(r, 'FormID')] = r.get('EditorID', '')
        print(f'  {len(self.dials)} DIALs, {len(self.infos)} INFOs, '
              f'{len(self.npcs)} NPCs, {len(self.qusts)} QUSTs, '
              f'{len(self.globs)} GLOBs', file=sys.stderr)

    # -- AddTopic ---------------------------------------------------------

    def build_addtopic_map(self):
        """topic fid -> the INFOs that reveal it.

        A topic with no revealer is available from the start; every other
        conversation topic stays hidden until one of its revealers has played.
        This is Oblivion's central visibility rule and has no Skyrim analogue.
        """
        revealers = defaultdict(list)
        for info in self.infos.values():
            for t in info.add_topics:
                revealers[t].append(info)
            # `AddTopic X` in a result script reveals it just the same.
            for name in re.findall(r'\bAddTopic\s+(\w+)',
                                   info.result_script or '', re.I):
                for d in self.dials.values():
                    if d.editor_id.lower() == name.lower():
                        revealers[d.form_id].append(info)
                        break
        return revealers


# --------------------------------------------------------------------------
# Condition evaluation
# --------------------------------------------------------------------------

class Evaluator:
    """Evaluates TES4 conditions against an NPC in a given game state."""

    def __init__(self, db, npc, state=None):
        self.db = db
        self.npc = npc
        self.state = state or {}

    def _cmp(self, actual, comp, want):
        return {0: actual == want, 1: actual != want, 2: actual > want,
                3: actual >= want, 4: actual < want,
                5: actual <= want}.get(comp, False)

    def evaluate(self, c: Condition):
        """True / False, or None when the answer cannot be known statically."""
        f, p1, p2 = c.func_idx, c.param1, c.param2
        subject = self.npc

        # A TES4 condition runs on the subject unless the type byte says
        # otherwise; bit 1 selects the target (the player, in dialogue).
        run_on_target = bool(c.type_byte & 0x02)

        if f == F_GETISID:
            if run_on_target:
                # "Is the speaker the player?" -- in this emulator the player
                # is never one of the NPCs being simulated.
                return self._cmp(0.0, c.comp_type, c.comp_value)
            return self._cmp(1.0 if p1 == subject.form_id else 0.0,
                             c.comp_type, c.comp_value)
        if f == F_GETISSEX:
            if run_on_target:
                return None
            return self._cmp(1.0 if bool(p1) == subject.is_female else 0.0,
                             c.comp_type, c.comp_value)
        if f == F_GETISRACE:
            if run_on_target:
                return None
            return self._cmp(1.0 if p1 == subject.race else 0.0,
                             c.comp_type, c.comp_value)
        if f == F_GETISCLASS:
            if run_on_target:
                return None
            return self._cmp(1.0 if p1 == subject.class_fid else 0.0,
                             c.comp_type, c.comp_value)
        if f == F_GETINFACTION:
            if run_on_target:
                return None
            return self._cmp(1.0 if p1 in subject.factions else 0.0,
                             c.comp_type, c.comp_value)
        if f == F_GETFACTIONRANK:
            if run_on_target:
                return None
            return self._cmp(float(subject.factions.get(p1, -1)),
                             c.comp_type, c.comp_value)
        if f == F_GETLEVEL:
            if run_on_target:
                return None
            return self._cmp(float(subject.level), c.comp_type, c.comp_value)
        if f == F_GETSTAGE:
            return self._cmp(float(self.state.get(f'stage_{p1}', 0)),
                             c.comp_type, c.comp_value)
        if f == F_GETSTAGEDONE:
            key = f'stagedone_{p1}_{p2}'
            done = (self.state[key] if key in self.state
                    else p2 <= self.state.get(f'stage_{p1}', 0))
            return self._cmp(1.0 if done else 0.0, c.comp_type, c.comp_value)
        if f == F_GETQUESTRUNNING:
            running = self.state.get(f'quest_running_{p1}',
                                     self.state.get(f'stage_{p1}', 0) > 0)
            return self._cmp(1.0 if running else 0.0, c.comp_type,
                             c.comp_value)
        if f == F_GETGLOBALVALUE:
            key = f'global_{p1}'
            actual = (float(self.state[key]) if key in self.state
                      else float(self.db.glob_values.get(p1, 0.0)))
            return self._cmp(actual, c.comp_type, c.comp_value)
        if f == F_GETISPLAYABLERACE:
            return self._cmp(1.0, c.comp_type, c.comp_value)
        if f == F_GETPLAYERINSEWORLD:
            return self._cmp(0.0, c.comp_type, c.comp_value)
        if f == F_ISGUARD:
            guard = any('guard' in self.db.facts.get(fid, '').lower()
                        for fid in subject.factions)
            return self._cmp(1.0 if guard else 0.0, c.comp_type, c.comp_value)
        if f == F_GETDISPOSITION:
            # Oblivion gates most generic greetings on disposition, comparing
            # against 30/50/70 tiers, so leaving it undecided makes a report
            # nearly useless. It is modelled as a settable value (--disposition,
            # default 50, which is roughly where a neutral NPC starts) rather
            # than guessed per-NPC. Skyrim has no disposition system at all, so
            # these conditions have no counterpart on the other side -- a topic
            # that Oblivion hid behind low disposition is simply always visible
            # after conversion, which is worth seeing in the comparison.
            return self._cmp(float(self.state.get('disposition', 50)),
                             c.comp_type, c.comp_value)
        if f in (F_GETSCRIPTVARIABLE, F_GETQUESTVARIABLE):
            # Script state; only a running game knows these.
            return None
        if f == F_GETTALKEDTOPC:
            return self._cmp(0.0, c.comp_type, c.comp_value)
        if f == F_GETDEADCOUNT:
            return self._cmp(float(self.state.get(f'deadcount_{p1}', 0)),
                             c.comp_type, c.comp_value)
        if f == F_GETRANDOMPERCENT:
            return None          # genuinely random
        return None

    def passes(self, conditions):
        """Evaluate a condition list with Oblivion's OR-group semantics.

        Consecutive conditions flagged OR form a group that passes if ANY
        member passes; groups are then ANDed together. An unknown is treated as
        passing so that a line gated only on runtime state still shows up --
        the report marks it, rather than hiding it.
        """
        if not conditions:
            return True, []
        failed = []
        unknown = []
        i = 0
        while i < len(conditions):
            group = [conditions[i]]
            while group[-1].is_or and i + 1 < len(conditions):
                i += 1
                group.append(conditions[i])
            results = [self.evaluate(c) for c in group]
            if all(r is False for r in results):
                failed.extend(group)
            elif not any(r is True for r in results):
                # Nothing in the group definitely passed, and nothing
                # definitely failed: the group turns on runtime state
                # (GetRandomPercent, a script variable, disposition).
                unknown.extend(c for c, r in zip(group, results) if r is None)
            i += 1
        self.last_unknown = unknown
        return (not failed), failed


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------

class Simulator:
    def __init__(self, db, state=None):
        self.db = db
        self.state = state or {}
        self.revealers = db.build_addtopic_map()

    def topic_available(self, dial_fid, ev):
        """Whether an AddTopic-gated topic has been revealed.

        `--unlock-all` forces this true, matching the Skyrim emulator's flag of
        the same name so the two reports line up.
        """
        if self.state.get('unlock_all'):
            return True
        revealers = self.revealers.get(dial_fid)
        if not revealers:
            return True          # never gated: available from the start
        key = f'topic_{dial_fid}'
        if key in self.state:
            return bool(self.state[key])
        # Revealed if any revealing line can itself play right now.
        for info in revealers:
            ok, _ = ev.passes(info.conditions)
            if ok:
                return True
        return False

    def walk(self, npc):
        ev = Evaluator(self.db, npc, self.state)
        out = {'topics': [], 'gated': [], 'greetings': [], 'barks':
               defaultdict(list), 'choice_only': []}
        # Topics reachable only as a reply to another line never sit in the menu.
        choice_targets = set()
        for info in self.db.infos.values():
            choice_targets.update(t for t in info.choices if t)

        for dial in self.db.dials.values():
            infos = self.db.infos_by_dial.get(dial.form_id, [])
            if not infos:
                continue
            matching = []
            for info in infos:
                ev.last_unknown = []
                ok, _ = ev.passes(info.conditions)
                if ok:
                    # Remember whether this line only "passes" because some of
                    # its conditions cannot be decided without a running game.
                    info.runtime_gated = bool(ev.last_unknown)
                    info.unknown_conditions = list(ev.last_unknown)
                    matching.append(info)
            if not matching:
                continue
            if dial.form_id == DIAL_GREETING:
                out['greetings'].extend((i, dial) for i in matching)
            elif dial.form_id in CHANNEL_NAMES:
                out['barks'][CHANNEL_NAMES[dial.form_id]].extend(
                    (i, dial) for i in matching)
            elif dial.dial_type == TYPE_TOPIC:
                if dial.form_id in choice_targets:
                    out['choice_only'].append((dial, matching))
                elif self.topic_available(dial.form_id, ev):
                    out['topics'].append((dial, matching))
                else:
                    out['gated'].append((dial, matching))
            elif dial.dial_type == TYPE_CONVERSATION:
                out['barks'][dial.full_name or dial.editor_id].extend(
                    (i, dial) for i in matching)
            else:
                out['barks'][CATEGORY_NAMES.get(dial.dial_type, '?')].extend(
                    (i, dial) for i in matching)
        return out

    def report(self, npc, verbose=False):
        d = self.walk(npc)
        print('=' * 70)
        print(f'NPC: {npc.editor_id} ({npc.name}) [{npc.form_id:08X}]')
        print(f'Race: {self.db.races.get(npc.race, "%08X" % npc.race)}, '
              f'{"Female" if npc.is_female else "Male"}, level {npc.level}')
        print(f'Class: {self.db.classes.get(npc.class_fid, "?")}')
        print(f'Factions: {len(npc.factions)}')
        for fid, rank in npc.factions.items():
            print(f'  {self.db.facts.get(fid, "%08X" % fid)}: rank {rank}')

        greets = d['greetings']
        certain = [g for g in greets if not g[0].runtime_gated]
        maybe = [g for g in greets if g[0].runtime_gated]
        print(f'\n--- Greetings ({len(certain)} certain, '
              f'{len(maybe)} runtime-gated) ---')
        for info, dial in certain[:40]:
            txt = info.responses[0][0][:70] if info.responses else '(no text)'
            print(f'  [{info.form_id:08X}] {txt}')
        for info, dial in maybe[:15]:
            txt = info.responses[0][0][:60] if info.responses else '(no text)'
            why = ', '.join(sorted({c.name for c in info.unknown_conditions}))
            print(f'  [{info.form_id:08X}] ?{txt}  <- needs {why}')

        print(f'\n--- Conversation Topics ({len(d["topics"])}) ---')
        for dial, infos in sorted(d['topics'],
                                  key=lambda x: x[0].full_name or ''):
            total = len(self.db.infos_by_dial.get(dial.form_id, []))
            sure = sum(1 for i in infos if not i.runtime_gated)
            print(f'  [{dial.form_id:08X}] "{dial.full_name}" '
                  f'(DIAL: {dial.editor_id}) {sure} certain / '
                  f'{len(infos)} possible of {total}')
            self._print_children(dial, infos, d, verbose, 1, set())

        if d['gated']:
            print(f'\n--- Hidden, awaiting AddTopic ({len(d["gated"])}) ---')
            for dial, infos in sorted(d['gated'],
                                      key=lambda x: x[0].full_name or ''):
                who = self.revealers.get(dial.form_id, [])
                print(f'  [{dial.form_id:08X}] "{dial.full_name}" '
                      f'(DIAL: {dial.editor_id}) revealed by '
                      f'{len(who)} line(s)')

        print(f'\n--- Barks ---')
        for k, v in sorted(d['barks'].items()):
            print(f'  {k}: {len(v)} INFOs')

    def _print_children(self, dial, infos, d, verbose, depth, seen):
        if dial.form_id in seen or depth > 4:
            return
        seen = seen | {dial.form_id}
        by_fid = {x[0].form_id: x for x in d['choice_only']}
        indent = '  ' + '    ' * depth
        printed = set()
        for info in infos[:10]:
            if verbose:
                txt = (info.responses[0][0][:60] if info.responses
                       else '(no text)')
                print(f'{indent}[{info.form_id:08X}] {txt}')
            for t in info.choices:
                if t in printed or t not in by_fid:
                    continue
                printed.add(t)
                cd, ci = by_fid[t]
                total = len(self.db.infos_by_dial.get(cd.form_id, []))
                print(f'{indent}-> [{cd.form_id:08X}] "{cd.full_name}" '
                      f'(DIAL: {cd.editor_id}) {len(ci)}/{total} passing')
                self._print_children(cd, ci, d, verbose, depth + 1, seen)


def build_state(db, stages, completed, globals_, unlock_all, disposition=50):
    state = {'disposition': disposition}
    if unlock_all:
        state['unlock_all'] = True
        print('  game state: all AddTopic gates treated as revealed')

    def quest(name):
        q = db.quest_by_edid.get(name.lower())
        if q:
            return q.form_id
        try:
            fid = int(name, 16)
        except ValueError:
            sys.exit(f'no quest named {name!r}')
        return fid

    for spec in stages:
        name, _, num = spec.rpartition(':')
        fid = quest(name)
        state[f'stage_{fid}'] = int(num)
        state[f'quest_running_{fid}'] = True
        print(f'  game state: {name} at stage {num} [{fid:08X}]')
    for name in completed:
        state[f'completed_{quest(name)}'] = True
    for spec in globals_:
        name, _, raw = spec.partition('=')
        fid = db.glob_by_edid.get(name.lower())
        if fid is None:
            sys.exit(f'no global named {name!r}')
        state[f'global_{fid}'] = float(raw)
        print(f'  game state: global {name} = {raw}')
    return state


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('export_dir', help='TES4 export directory, e.g. '
                                       'export/Oblivion.esm')
    ap.add_argument('--npc', help='NPC EditorID')
    ap.add_argument('--list-npcs', metavar='SUBSTRING',
                    help='list NPC EditorIDs containing this')
    ap.add_argument('--stage', action='append', default=[], metavar='QUEST:N')
    ap.add_argument('--completed', action='append', default=[], metavar='QUEST')
    ap.add_argument('--global', dest='globals', action='append', default=[],
                    metavar='NAME=VALUE')
    ap.add_argument('--unlock-all', action='store_true',
                    help='treat every AddTopic gate as already revealed')
    ap.add_argument('--disposition', type=int, default=50,
                    help='the disposition GetDisposition conditions see '
                         '(default 50); Oblivion tiers greetings at 30/50/70')
    ap.add_argument('--verbose', '-v', action='store_true')
    args = ap.parse_args()

    db = OblivionDB(args.export_dir)

    if args.list_npcs:
        needle = args.list_npcs.lower()
        for npc in sorted(db.npcs.values(), key=lambda n: n.editor_id):
            if needle in npc.editor_id.lower() or needle in npc.name.lower():
                print(f'  {npc.form_id:08X}  {npc.editor_id}  ({npc.name})')
        return

    if not args.npc:
        print(f'DIALs {len(db.dials)}, INFOs {len(db.infos)}, '
              f'NPCs {len(db.npcs)}, QUSTs {len(db.qusts)}')
        return

    npc = db.npc_by_edid.get(args.npc.lower())
    if not npc:
        sys.exit(f'NPC {args.npc!r} not found (try --list-npcs)')
    state = build_state(db, args.stage, args.completed, args.globals,
                        args.unlock_all, args.disposition)
    Simulator(db, state).report(npc, verbose=args.verbose)


if __name__ == '__main__':
    main()

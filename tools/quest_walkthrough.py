#!/usr/bin/env python3
"""
Quest walkthrough emulator — audits converted quests for completability.

Symbolically "plays" every quest in a converted TES5 plugin: starting from
start-game-enabled state it repeatedly fires every surviving stage-advancement
edge (dialogue result fragments, quest stage fragments, attached quest/object
scripts) whose gates are satisfiable, until a fixpoint. A quest is COMPLETABLE
when a stage flagged "complete quest" in the TES4 source is reachable in the
converted data. The same engine runs over the TES4 export as the baseline, so
stages that were already unreachable in Oblivion are not counted as regressions.

Dialog reachability follows the real engine rules (see the skyrim-dialog-system
skill): a topic's INFO can fire only if its DIAL has a QNAM to a runnable quest,
the topic is menu-reachable (top-level branch / TCLT link / bark subtype /
unlock-global gate revealed), and every CTDA AND-group is satisfiable.

Usage:
    python tools/quest_walkthrough.py --export export/Oblivion.esm \
        --esm output/oblivion.esm/Oblivion.esm \
        --scripts output/oblivion.esm/scripts \
        [--skyrim "C:/.../Skyrim.esm"] [--quest MS16B] [--md report.md] [-v]
"""
import argparse
import io
import os
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

if (getattr(sys.stdout, 'encoding', '') or '').lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                  errors='replace')
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.tes5_esm_reader import (read_tes5_file, TES5Record,
                                   _get, _all, _zstring, _CTDA_FUNC_NAMES)
from tes5_import.text_reader import parse_export_file

# ── Skyrim CTDA function indices (project table, tools/tes5_esm_reader.py) ──
F_GETQUESTRUNNING = 56
F_GETSTAGE = 58
F_GETSTAGEDONE = 59
F_GETINCELL = 67
F_GETISID = 72
F_GETINFACTION = 71
F_GETFACTIONRANK = 73
F_GETGLOBALVALUE = 74
F_GETDEADCOUNT = 84
F_GETISVOICETYPE = 426
F_GETVMQUESTVAR = 629
F_GETVMSCRIPTVAR = 630

# Functions whose param1 is a FormID that must exist for the condition to ever
# evaluate (a dangling param means the condition can never pass).
_P1_FORMID_FUNCS = {
    F_GETQUESTRUNNING, F_GETSTAGE, F_GETSTAGEDONE, F_GETINCELL, F_GETISID,
    F_GETINFACTION, F_GETFACTIONRANK, F_GETGLOBALVALUE, F_GETISVOICETYPE,
    F_GETVMQUESTVAR, 68, 69, 84,
}

_OPS = {0: lambda a, b: a == b, 1: lambda a, b: a != b, 2: lambda a, b: a > b,
        3: lambda a, b: a >= b, 4: lambda a, b: a < b, 5: lambda a, b: a <= b}

_BARK_SNAMS = {  # subtypes the engine fires automatically (no topic menu)
    'HELO', 'GRET', 'IDAT', 'GBYE', 'SHRT',
    'ATCK', 'POAT', 'BASH', 'HIT_', 'FLEE', 'BLED', 'DETH', 'TAUT',
    'NOTC', 'OBCO', 'NOTI', 'CLOS', 'LOST', 'NORM', 'ALTN', 'ALKL',
}


class Ctda:
    __slots__ = ('op', 'is_or', 'use_global', 'comp', 'func', 'p1', 'p2',
                 'run_on', 'cis2')

    def __init__(self, data, cis2=None):
        tb = data[0]
        self.op = (tb >> 5) & 7
        self.is_or = bool(tb & 0x01)
        self.use_global = bool(tb & 0x04)
        self.comp = struct.unpack_from('<f', data, 4)[0]
        self.func = struct.unpack_from('<H', data, 8)[0]
        self.p1 = struct.unpack_from('<I', data, 12)[0]
        self.p2 = struct.unpack_from('<I', data, 16)[0]
        self.run_on = struct.unpack_from('<I', data, 20)[0]
        self.cis2 = cis2


def parse_ctdas(rec: TES5Record):
    """CTDA list with trailing CIS2 strings attached, in subrecord order."""
    out = []
    for s in rec.subrecords:
        if s.type == 'CTDA' and len(s.data) >= 24:
            out.append(Ctda(s.data))
        elif s.type == 'CIS2' and out:
            out[-1].cis2 = _zstring(s.data)
    return out


def and_groups(ctdas):
    """Split a CTDA chain into AND-of-OR-groups (engine semantics)."""
    groups, cur = [], []
    for c in ctdas:
        cur.append(c)
        if not c.is_or:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


# ── VMAD parsing (version 5, objectFormat 2 — what the converter writes) ──

def _wstring(data, pos):
    n = struct.unpack_from('<H', data, pos)[0]
    return data[pos + 2:pos + 2 + n].decode('utf-8', errors='replace'), pos + 2 + n


def parse_vmad(data: bytes):
    """Return (scripts, fragments) from a VMAD blob.

    scripts:   [(script_name, {prop_name_lower: (type, value)})]
    fragments: for QUST [(stage, log_idx, script, frag_func)],
               for INFO [(flag_bit, script, frag_func)] — caller knows which.
    Raises ValueError on malformed data.
    """
    pos = 0
    version, objfmt = struct.unpack_from('<HH', data, 0)
    pos = 4
    nscripts = struct.unpack_from('<H', data, pos)[0]
    pos += 2
    scripts = []
    for _ in range(nscripts):
        name, pos = _wstring(data, pos)
        pos += 1  # status flags
        nprops = struct.unpack_from('<H', data, pos)[0]
        pos += 2
        props = {}
        for _ in range(nprops):
            pname, pos = _wstring(data, pos)
            ptype = data[pos]
            pos += 2  # type + status
            if ptype == 1:      # object: unused u16, alias i16, formid u32
                fid = struct.unpack_from('<I', data, pos + 4)[0]
                props[pname.lower()] = ('obj', fid)
                pos += 8
            elif ptype == 2:
                sval, pos = _wstring(data, pos)
                props[pname.lower()] = ('str', sval)
            elif ptype == 3:
                props[pname.lower()] = ('int', struct.unpack_from('<i', data, pos)[0])
                pos += 4
            elif ptype == 4:
                props[pname.lower()] = ('float', struct.unpack_from('<f', data, pos)[0])
                pos += 4
            elif ptype == 5:
                props[pname.lower()] = ('bool', data[pos])
                pos += 1
            else:
                raise ValueError(f'unsupported VMAD prop type {ptype}')
        scripts.append((name, props))
    return scripts, data[pos:]


def parse_qust_fragments(tail: bytes):
    """Fragment section of a QUST VMAD → [(stage, log_idx, script, func)]."""
    if not tail:
        return []
    pos = 1  # extra-bind version
    count = struct.unpack_from('<H', tail, pos)[0]
    pos += 2
    _fn, pos = _wstring(tail, pos)
    frags = []
    for _ in range(count):
        stage = struct.unpack_from('<H', tail, pos)[0]
        log_idx = struct.unpack_from('<i', tail, pos + 4)[0]
        pos += 9
        sname, pos = _wstring(tail, pos)
        fname, pos = _wstring(tail, pos)
        frags.append((stage, log_idx, sname, fname))
    return frags


def parse_info_fragments(tail: bytes):
    """Fragment section of an INFO VMAD → [(script, func)]."""
    if not tail:
        return []
    pos = 1
    flags = tail[pos]
    pos += 1
    _fn, pos = _wstring(tail, pos)
    frags = []
    for _bit in range(bin(flags).count('1')):
        pos += 1
        sname, pos = _wstring(tail, pos)
        fname, pos = _wstring(tail, pos)
        frags.append((sname, fname))
    return frags


# ── Papyrus source parsing ──────────────────────────────────────────────────

_RE_PROP = re.compile(
    r'^\s*(\w+)\s+Property\s+(\w+)(?:\s*=\s*\S+)?\s+Auto', re.IGNORECASE)
_RE_FUNC = re.compile(r'^\s*(?:Function|Event)\s+(\w+)\s*\(', re.IGNORECASE)
_RE_ENDFUNC = re.compile(r'^\s*End(?:Function|Event)\b', re.IGNORECASE)
_RE_SETSTAGE = re.compile(r'\b(\w+)\.SetStage\(\s*([^)]*?)\s*\)', re.IGNORECASE)
_RE_START = re.compile(r'\b(\w+)\.Start\(\s*\)', re.IGNORECASE)
_RE_STOP = re.compile(r'\b(\w+)\.Stop\(\s*\)', re.IGNORECASE)
_RE_COMPLETE = re.compile(r'\b(?:(\w+)\.)?CompleteQuest\(\s*\)', re.IGNORECASE)
_RE_SETVALUE = re.compile(r'\b(\w+)\.SetValue\(\s*([0-9.]+)', re.IGNORECASE)
_RE_SAY = re.compile(r'\b\w+\.Say\(\s*(\w+)\s*[,)]', re.IGNORECASE)
_RE_CONDVAR = re.compile(
    r'^\s*(\w+)\s+Property\s+(\w+)(?:\s*=\s*\S+)?\s+Auto\s+Conditional',
    re.IGNORECASE)
_RE_CONDRAW = re.compile(r'^\s*(?:int|float|bool)\s+(\w+)(?:\s*=\s*\S+)?\s+Conditional',
                         re.IGNORECASE)


class PscInfo:
    __slots__ = ('name', 'extends', 'props', 'functions', 'cond_vars', 'todos')

    def __init__(self):
        self.name = ''
        self.extends = ''
        self.props = {}        # lower name -> declared type
        self.functions = {}    # lower func name -> body text
        self.cond_vars = set() # lower names of Conditional vars/props
        self.todos = 0


def parse_psc(path: str) -> PscInfo:
    info = PscInfo()
    cur_func, body = None, []
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            if ';TODO' in line:
                info.todos += 1
            m = re.match(r'^\s*ScriptName\s+(\w+)(?:\s+extends\s+(\w+))?',
                         line, re.IGNORECASE)
            if m:
                info.name, info.extends = m.group(1), (m.group(2) or '')
                continue
            m = _RE_CONDVAR.match(line) or _RE_CONDRAW.match(line)
            if m:
                info.cond_vars.add(m.group(m.lastindex).lower())
            m = _RE_PROP.match(line)
            if m:
                info.props[m.group(2).lower()] = m.group(1)
                continue
            m = _RE_FUNC.match(line)
            if m:
                cur_func, body = m.group(1).lower(), []
                continue
            if _RE_ENDFUNC.match(line):
                if cur_func:
                    info.functions[cur_func] = '\n'.join(body)
                cur_func = None
                continue
            if cur_func is not None:
                # strip comments for call matching but keep TODO count above
                body.append(line.split(';', 1)[0])
    return info


def extract_actions(body: str):
    """Symbolic actions in a Papyrus body.

    Returns list of (kind, prop_lower, arg):
      ('setstage', prop, stage_int_or_None)   None = dynamic stage expr
      ('start'|'stop'|'complete', prop_or_'' , None)
      ('setglobal', prop, float_value)
    """
    acts = []
    for m in _RE_SETSTAGE.finditer(body):
        arg = m.group(2).strip()
        try:
            stage = int(float(arg))
        except ValueError:
            stage = None
        acts.append(('setstage', m.group(1).lower(), stage))
    for m in _RE_START.finditer(body):
        acts.append(('start', m.group(1).lower(), None))
    for m in _RE_STOP.finditer(body):
        acts.append(('stop', m.group(1).lower(), None))
    for m in _RE_COMPLETE.finditer(body):
        acts.append(('complete', (m.group(1) or '').lower(), None))
    for m in _RE_SETVALUE.finditer(body):
        acts.append(('setglobal', m.group(1).lower(), float(m.group(2))))
    for m in _RE_SAY.finditer(body):
        acts.append(('say', m.group(1).lower(), None))
    return acts


# ── TES4 export loading ─────────────────────────────────────────────────────

_RE_T4_SETSTAGE = re.compile(r'\bsetstage[\s,]+(\w+)[\s,]+(\S+)', re.IGNORECASE)
_RE_T4_STARTQ = re.compile(r'\bstartquest[\s,]+(\w+)', re.IGNORECASE)


def _low24(fid_str):
    try:
        return int(fid_str, 16) & 0xFFFFFF
    except (TypeError, ValueError):
        return 0


class Tes4Data:
    def __init__(self, export_dir):
        self.quests = {}         # fid24 -> record dict
        self.quest_by_edid = {}  # lower edid -> fid24
        self.infos = {}          # fid24 -> record dict
        self.dials = {}          # fid24 -> record dict
        self.scpts = {}          # fid24 -> record dict
        self.scpt_by_edid = {}
        for rec in parse_export_file(os.path.join(export_dir, 'QUST.txt')):
            fid = _low24(rec.get('FormID'))
            self.quests[fid] = rec
            e = rec.get('EditorID', '').lower()
            if e:
                self.quest_by_edid[e] = fid
        for rec in parse_export_file(os.path.join(export_dir, 'INFO.txt')):
            self.infos[_low24(rec.get('FormID'))] = rec
        for rec in parse_export_file(os.path.join(export_dir, 'DIAL.txt')):
            self.dials[_low24(rec.get('FormID'))] = rec
        for rec in parse_export_file(os.path.join(export_dir, 'SCPT.txt')):
            fid = _low24(rec.get('FormID'))
            self.scpts[fid] = rec
            e = rec.get('EditorID', '').lower()
            if e:
                self.scpt_by_edid[e] = fid
        # Scripts actually attached somewhere (SCRI= lines across the export).
        # An orphaned SCPT (MS14TivelaScript) never runs in Oblivion either, so
        # its setstage lines must not count as baseline-reachable edges.
        self.attached_scpts = set()
        scri_files = ('QUST', 'ACTI', 'ALCH', 'AMMO', 'APPA', 'ARMO', 'BOOK',
                      'CLOT', 'CONT', 'CREA', 'DOOR', 'FLOR', 'FURN', 'INGR',
                      'KEYM', 'LIGH', 'MISC', 'NPC_', 'SGST', 'SLGM', 'WEAP',
                      'SPEL', 'ENCH')
        for sig in scri_files:
            path = os.path.join(export_dir, f'{sig}.txt')
            if not os.path.isfile(path):
                continue
            with open(path, encoding='utf-8', errors='replace') as f:
                for line in f:
                    if line.startswith('SCRI='):
                        self.attached_scpts.add(_low24(line[5:].strip()))
                    elif line.startswith('ScriptEffect[') and '.FormID=' in line:
                        # script-effect (SEFF) reference on a spell/enchant/
                        # ingredient — the script runs when the effect applies
                        self.attached_scpts.add(
                            _low24(line.split('=', 1)[1].strip()))

    def stage_list(self, qrec):
        stages = []
        i = 0
        while f'Stage[{i}].Index' in qrec:
            try:
                stages.append(int(qrec[f'Stage[{i}].Index']))
            except ValueError:
                pass
            i += 1
        return stages

    def complete_stages(self, qrec):
        out = []
        i = 0
        while f'Stage[{i}].Index' in qrec:
            j = 0
            while (f'Stage[{i}].Log[{j}].Flags' in qrec or
                   f'Stage[{i}].Log[{j}].Text' in qrec):
                try:
                    if int(qrec.get(f'Stage[{i}].Log[{j}].Flags', '0')) & 1:
                        out.append(int(qrec[f'Stage[{i}].Index']))
                except ValueError:
                    pass
                j += 1
            i += 1
        return sorted(set(out))


def build_tes4_edges(t4: Tes4Data):
    """All TES4 stage-advancement edges with their source containers.

    Returns [(gate, qfid, action, container)] where container is
    ('INFO', fid24) | ('STAGE', qfid, stage) | ('SCPT', edid).
    """
    edges = []

    def script_edges(text, gate, container):
        if not text:
            return
        # strip TES4 line comments — a commented-out `;setstage X 50` is not
        # an edge (SE01Door taught us this)
        text = '\n'.join(l.split(';', 1)[0] for l in text.splitlines())
        for m in _RE_T4_SETSTAGE.finditer(text):
            q = t4.quest_by_edid.get(m.group(1).lower())
            if q is None:
                continue
            try:
                stage = int(float(m.group(2).rstrip(',')))
            except ValueError:
                stage = None
            edges.append((gate, q, ('setstage', stage), container))
        for m in _RE_T4_STARTQ.finditer(text):
            q = t4.quest_by_edid.get(m.group(1).lower())
            if q is not None:
                edges.append((gate, q, ('start', None), container))

    # INFO result scripts, gated by the INFO's own quest-state conditions
    for fid, rec in t4.infos.items():
        script_edges(rec.get('ResultScript', ''), _t4_gate(rec), ('INFO', fid))
    # Quest stage result scripts, gated on reaching that stage
    for qfid, rec in t4.quests.items():
        i = 0
        while f'Stage[{i}].Index' in rec:
            try:
                sidx = int(rec[f'Stage[{i}].Index'])
            except ValueError:
                i += 1
                continue
            j = 0
            while (f'Stage[{i}].Log[{j}].Flags' in rec or
                   f'Stage[{i}].Log[{j}].Text' in rec):
                script_edges(rec.get(f'Stage[{i}].Log[{j}].ResultScript', ''),
                             [('stage', qfid, sidx)], ('STAGE', qfid, sidx))
                j += 1
            i += 1
    # Object/quest scripts: optimistic, no gate — but only scripts actually
    # attached to something (orphaned SCPTs never run in Oblivion either)
    for fid, rec in t4.scpts.items():
        if fid not in t4.attached_scpts:
            continue
        script_edges(rec.get('SCTX', ''), [],
                     ('SCPT', rec.get('EditorID', f'{fid:06X}')))
    return edges


def tes4_reachability(t4: Tes4Data, edges=None):
    """Optimistic fixpoint over TES4 data. Returns ({qfid24: stages}, running).
    Only GetStage/GetStageDone/GetQuestRunning conditions are evaluated;
    everything else optimistic-true (mirrors the TES5 engine so the
    comparison is apples-to-apples)."""
    if edges is None:
        edges = build_tes4_edges(t4)
    return _fixpoint(t4, edges)


def _t4_gate(info_rec):
    """Extract quest-state gate conditions from a TES4 INFO record."""
    gate = []
    i = 0
    while True:
        raw_hex = info_rec.get(f'Condition[{i}].Raw')
        if raw_hex is None:
            break
        i += 1
        try:
            raw = bytes.fromhex(raw_hex)
        except ValueError:
            continue
        if len(raw) < 20:
            continue
        func = struct.unpack_from('<H', raw, 8)[0]
        op = (raw[0] >> 5) & 7
        comp = struct.unpack_from('<f', raw, 4)[0]
        p1 = struct.unpack_from('<I', raw, 12)[0]
        p2 = struct.unpack_from('<I', raw, 16)[0]
        if func in (56, 58, 59):
            gate.append(('func', func, op, comp, p1 & 0xFFFFFF, p2))
    return gate


def _fixpoint(t4, edges):
    """Shared fixpoint engine for the TES4 baseline."""
    reached = defaultdict(set)
    running = set()
    for qfid, rec in t4.quests.items():
        try:
            if int(rec.get('DATA.Flags', '0')) & 1:
                running.add(qfid)
        except ValueError:
            pass

    def gate_ok(gate):
        for g in gate:
            if g[0] == 'stage':
                if g[2] not in reached[g[1]]:
                    return False
            elif g[0] == 'func':
                _, func, op, comp, p1, p2 = g
                if func == 58:
                    vals = reached[p1] | {0}
                    if not any(_OPS[op](v, comp) for v in vals):
                        return False
                elif func == 59:
                    # Only the "stage must be done" form can block progress
                    if op == 0 and comp == 1.0 and p2 not in reached[p1]:
                        return False
                elif func == 56:
                    if op == 0 and comp == 1.0 and p1 not in running:
                        return False
        return True

    changed = True
    while changed:
        changed = False
        for gate, qfid, (kind, stage), _container in edges:
            if not gate_ok(gate):
                continue
            if kind == 'start' and qfid not in running:
                running.add(qfid)
                changed = True
            elif kind == 'setstage':
                if qfid not in running:
                    running.add(qfid)
                    changed = True
                targets = ([stage] if stage is not None
                           else t4.stage_list(t4.quests.get(qfid, {})))
                for s in targets:
                    if s not in reached[qfid]:
                        reached[qfid].add(s)
                        changed = True
    return reached, running


# ── comparison + diagnosis ──────────────────────────────────────────────────

def _t5_fid(fid24: int) -> int:
    """TES4 low-24 FormID -> converted plugin FormID (master index 01)."""
    return 0x01000000 | fid24


def diagnose_missing_stage(t4, t4_edges, eng, qfid24, stage):
    """Explain why (quest, stage) is unreachable in the converted data by
    walking every TES4 source that set it and naming what broke."""
    reasons = []
    d = eng.d
    for gate, q, (kind, s), container in t4_edges:
        if q != qfid24 or kind != 'setstage':
            continue
        if s is not None and s != stage:
            continue
        ckind = container[0]
        if ckind == 'INFO':
            ifid5 = _t5_fid(container[1])
            info = d.infos.get(ifid5)
            src = f'INFO {container[1]:08X}'
            if info is None:
                reasons.append(f'{src}: dropped in conversion (topic skipped?)')
                continue
            hits = [e for e in eng.edges
                    if e.container[:2] == ('INFO', ifid5)
                    and e.action[0] == 'setstage'
                    and e.action[1] == _t5_fid(q)
                    and (e.action[2] is None or s is None or e.action[2] == s)]
            if not hits:
                notes = eng.edge_notes.get(('INFO', ifid5, ''), [])
                if not info['scripts']:
                    reasons.append(f'{src}: converted INFO has NO fragment '
                                   f'(result script lost)')
                elif notes:
                    reasons.append(f'{src}: {"; ".join(notes[:2])}')
                else:
                    reasons.append(f'{src}: fragment exists but SetStage call '
                                   f'missing from Papyrus body (TODO/dropped)')
                continue
            why = hits[0].blocked_why
            if why:
                reasons.append(f'{src}: edge exists but blocked — {why}')
        elif ckind == 'STAGE':
            owner = t4.quests.get(container[1], {}).get('EditorID', '?')
            src = f'{owner} stage {container[2]} result script'
            hits = [e for e in eng.edges
                    if e.container[0] == 'QUST'
                    and e.container[1] == _t5_fid(container[1])
                    and e.gate == ('stage', _t5_fid(container[1]), container[2])
                    and e.action[0] == 'setstage'
                    and e.action[1] == _t5_fid(q)]
            if not hits:
                reasons.append(f'{src}: converted QF fragment lacks the '
                               f'SetStage call')
            elif hits[0].blocked_why:
                reasons.append(f'{src}: blocked — {hits[0].blocked_why}')
        elif ckind == 'SCPT':
            from script_convert.constants import papyrus_script_name
            sname = papyrus_script_name(container[1])
            low = sname.lower()
            src = f'script {container[1]}'
            psc = d.psc.get(low)
            if psc is None:
                reasons.append(f'{src}: {sname}.psc not generated')
                continue
            if low not in d.pex:
                reasons.append(f'{src}: {sname} not compiled')
                continue
            hits = [e for e in eng.edges
                    if e.script.lower() == low and e.action[0] == 'setstage'
                    and e.action[1] == _t5_fid(q)]
            attached = low in d.attachments or any(
                s2.lower() == low for qq in d.quests.values()
                for s2, _p in qq['scripts'])
            if not attached:
                reasons.append(f'{src}: {sname} converted but attached to '
                               f'NOTHING (edge lost)')
            elif not hits:
                reasons.append(f'{src}: {sname} attached but SetStage line '
                               f'lost/unbound in conversion')
            elif hits[0].blocked_why:
                reasons.append(f'{src}: blocked — {hits[0].blocked_why}')
    if not reasons:
        reasons.append('no TES4 source found (stage was set dynamically — '
                       'variable-stage SetStage or external system)')
    return reasons


def audit(t4, eng, only_quest=None):
    """Run both engines, compare, return per-quest result dicts."""
    t4_edges = build_tes4_edges(t4)
    t4_reached, t4_running = tes4_reachability(t4, t4_edges)
    t5_reached, t5_running, revealed, completed = eng.run()

    results = []
    for qfid24, qrec in sorted(t4.quests.items()):
        edid = qrec.get('EditorID', '')
        if only_quest and edid.lower() != only_quest.lower():
            continue
        qfid5 = _t5_fid(qfid24)
        q5 = eng.d.quests.get(qfid5)
        res = {'edid': edid, 'fid24': qfid24, 'issues': [], 'warns': [],
               'status': 'OK'}
        t4_stages = set(t4.stage_list(qrec))
        complete_stages = set(t4.complete_stages(qrec))
        journal = _has_journal(qrec)
        if q5 is None:
            if t4_stages or journal:
                res['status'] = 'MISSING'
                res['issues'].append('QUST not converted at all')
            results.append(res)
            continue
        if (q5['flags'] & 0x01) and eng.d.seq_fids and \
                qfid5 not in eng.d.seq_fids:
            res['issues'].append('start-game-enabled but MISSING from SEQ '
                                 'file — dialogue never initializes')
        t4r = t4_reached.get(qfid24, set()) & t4_stages
        t5r = t5_reached.get(qfid5, set())
        missing = sorted(t4r - t5r)
        if missing:
            for s in missing:
                for why in diagnose_missing_stage(t4, t4_edges, eng,
                                                  qfid24, s)[:3]:
                    res['issues'].append(f'stage {s} unreachable: {why}')
        if complete_stages:
            t4_completable = bool(complete_stages & t4r)
            t5_completable = bool(complete_stages & t5r) or qfid5 in completed
            if t4_completable and not t5_completable:
                res['status'] = 'BROKEN'
            elif missing:
                res['status'] = 'DEGRADED'
        elif missing:
            res['status'] = 'DEGRADED'
        if journal and not q5['objectives'] and q5['stages']:
            res['warns'].append('journal quest converted with no QOBJ '
                                'objectives')
        results.append(res)
    return results, (t5_reached, t5_running, revealed, completed)


def _has_journal(qrec):
    i = 0
    while f'Stage[{i}].Index' in qrec:
        j = 0
        while (f'Stage[{i}].Log[{j}].Flags' in qrec or
               f'Stage[{i}].Log[{j}].Text' in qrec):
            if qrec.get(f'Stage[{i}].Log[{j}].Text'):
                return True
            j += 1
        i += 1
    return False


def write_md(path, results, eng):
    ok = [r for r in results if r['status'] == 'OK' and not r['issues']]
    broken = [r for r in results if r['status'] in ('BROKEN', 'MISSING')]
    degraded = [r for r in results if r['status'] == 'DEGRADED']
    other_issues = [r for r in results
                    if r['status'] == 'OK' and (r['issues'] or r['warns'])]
    with open(path, 'w', encoding='utf-8') as f:
        f.write('# Quest Walkthrough Audit (machine-generated)\n\n')
        f.write(f'- Quests audited: {len(results)}\n')
        f.write(f'- OK: {len(ok)}  BROKEN: {len(broken)}  '
                f'DEGRADED: {len(degraded)}  OK-with-notes: '
                f'{len(other_issues)}\n\n')

        def section(title, rows):
            if not rows:
                return
            f.write(f'## {title}\n\n')
            for r in rows:
                f.write(f'### {r["edid"]} ({r["fid24"]:08X}) — '
                        f'{r["status"]}\n')
                for i in r['issues']:
                    f.write(f'- ISSUE: {i}\n')
                for w in r['warns']:
                    f.write(f'- warn: {w}\n')
                f.write('\n')
        section('Broken / missing quests', broken)
        section('Degraded quests (lost side content)', degraded)
        section('OK quests with notes', other_issues)

        if eng.script_issues:
            f.write('## Global script issues\n\n')
            for sname, issue in sorted(set(eng.script_issues))[:300]:
                f.write(f'- {sname}: {issue}\n')
        f.write('\n')
    print(f'wrote {path}')


def main():
    import time
    ap = argparse.ArgumentParser(
        description='Quest walkthrough emulator / completability audit')
    ap.add_argument('--export', required=True, help='TES4 export dir')
    ap.add_argument('--esm', required=True, help='converted plugin path')
    ap.add_argument('--scripts', required=True, help='scripts output dir')
    ap.add_argument('--seq', default='', help='SEQ file path')
    ap.add_argument('--skyrim', default=r'C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data\Skyrim.esm')
    ap.add_argument('--cache-dir', default='temp')
    ap.add_argument('--quest', default=None, help='single quest EditorID')
    ap.add_argument('--md', default=None, help='write markdown report here')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args()

    from tools.quest_walkthrough_tes5 import Tes5Data, Tes5Engine
    t0 = time.time()
    print('loading TES4 export...')
    t4 = Tes4Data(args.export)
    print(f'  {len(t4.quests)} quests, {len(t4.infos)} INFOs, '
          f'{len(t4.scpts)} scripts ({time.time()-t0:.1f}s)')
    print('loading TES5 output...')
    d5 = Tes5Data(args.esm, args.scripts, args.seq, args.skyrim,
                  args.cache_dir)
    eng = Tes5Engine(d5, verbose=args.verbose)
    print(f'  {len(eng.edges)} converted stage-advancement edges')
    print('running walkthrough fixpoint...')
    results, _state = audit(t4, eng, only_quest=args.quest)

    broken = [r for r in results if r['status'] in ('BROKEN', 'MISSING')]
    degraded = [r for r in results if r['status'] == 'DEGRADED']
    print(f'\n=== {len(results)} quests: {len(broken)} broken, '
          f'{len(degraded)} degraded ===')
    for r in results:
        if r['status'] != 'OK' or r['issues'] or (args.verbose and r['warns']):
            print(f'\n[{r["status"]}] {r["edid"]} ({r["fid24"]:08X})')
            for i in r['issues'][:12]:
                print(f'  ISSUE: {i}')
            for w in r['warns'][:6]:
                print(f'  warn:  {w}')
    if args.md:
        write_md(args.md, results, eng)


if __name__ == '__main__':
    main()

"""TES5 side of the quest walkthrough emulator (see quest_walkthrough.py).

Loads the converted plugin + generated Papyrus sources and symbolically fires
every surviving stage-advancement edge until a fixpoint, tracking WHY each
non-firing edge is blocked so the auditor can name the conversion break.
"""
import os
import struct
import time
from collections import defaultdict
from pathlib import Path

from tools.tes5_esm_reader import read_tes5_file, _get, _all, _zstring
from tools.quest_walkthrough import (
    Ctda, parse_ctdas, and_groups, parse_vmad, parse_qust_fragments,
    parse_info_fragments, parse_psc, extract_actions, PscInfo, _OPS,
    _BARK_SNAMS, _P1_FORMID_FUNCS,
    F_GETQUESTRUNNING, F_GETSTAGE, F_GETSTAGEDONE, F_GETISID,
    F_GETGLOBALVALUE, F_GETISVOICETYPE, F_GETVMQUESTVAR, F_GETVMSCRIPTVAR,
)

# Record types whose subrecords we actually need (everything else is scanned
# header-only for the FormID existence set).
_PARSE_TYPES = frozenset({
    'QUST', 'DIAL', 'INFO', 'DLBR', 'GLOB', 'NPC_', 'VTYP', 'FLST', 'PACK',
    'FACT', 'SPEL',
    # script-capable object types (object-script VMAD attachments)
    'ACTI', 'ALCH', 'ARMO', 'BOOK', 'CONT', 'DOOR', 'FLOR', 'FURN', 'INGR',
    'KEYM', 'LIGH', 'MISC', 'WEAP', 'APPA', 'SLGM', 'AMMO', 'SCRL',
})


def _load_vanilla_fids(skyrim_esm: str, cache_dir: str) -> set:
    """FormID set of Skyrim.esm, header-only scan, cached to disk."""
    cache = None
    if skyrim_esm and os.path.isfile(skyrim_esm):
        st = os.stat(skyrim_esm)
        cache = os.path.join(cache_dir,
                             f'skyrim_fids_{st.st_size}_{int(st.st_mtime)}.bin')
        if os.path.isfile(cache):
            data = open(cache, 'rb').read()
            return set(struct.unpack(f'<{len(data)//4}I', data))
    if not skyrim_esm or not os.path.isfile(skyrim_esm):
        return set()
    _hdr, recs, _loc = read_tes5_file(skyrim_esm, parse_types=frozenset())
    fids = {r.form_id for r in recs}
    if cache:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache, 'wb') as f:
            f.write(struct.pack(f'<{len(fids)}I', *sorted(fids)))
    return fids


class Tes5Data:
    """Everything the emulator needs from the converted output."""

    def __init__(self, esm_path, scripts_dir, seq_path, skyrim_esm, cache_dir):
        t0 = time.time()
        _hdr, recs, _loc = read_tes5_file(esm_path, parse_types=_PARSE_TYPES)
        self.all_fids = {r.form_id for r in recs}
        self.vanilla_fids = _load_vanilla_fids(skyrim_esm, cache_dir)
        print(f'  [tes5] ESM: {len(recs)} records, '
              f'{len(self.vanilla_fids)} vanilla FormIDs ({time.time()-t0:.1f}s)')

        self.quests = {}     # fid -> dict
        self.dials = {}
        self.infos = {}
        self.dlbrs = {}
        self.globs = {}      # fid -> edid
        self.glob_by_edid = {}
        self.npc_vtck = {}   # npc fid -> vtyp fid
        self.npc_edids = {}
        self.attachments = defaultdict(list)  # script lower -> [(type,fid,props)]
        self.quest_by_edid = {}

        for r in recs:
            if r.type == 'QUST':
                self._add_qust(r)
            elif r.type == 'DIAL':
                self._add_dial(r)
            elif r.type == 'INFO':
                self._add_info(r)
            elif r.type == 'DLBR':
                d = _get(r, 'DNAM')
                s = _get(r, 'SNAM')
                q = _get(r, 'QNAM')
                self.dlbrs[r.form_id] = {
                    'flags': struct.unpack('<I', d.data)[0] if d and len(d.data) == 4 else 0,
                    'start_dial': struct.unpack('<I', s.data)[0] if s else 0,
                    'quest': struct.unpack('<I', q.data)[0] if q else 0,
                }
            elif r.type == 'GLOB':
                e = _get(r, 'EDID')
                edid = _zstring(e.data) if e else ''
                self.globs[r.form_id] = edid
                self.glob_by_edid[edid.lower()] = r.form_id
            elif r.type == 'NPC_':
                v = _get(r, 'VTCK')
                if v and len(v.data) == 4:
                    self.npc_vtck[r.form_id] = struct.unpack('<I', v.data)[0]
                e = _get(r, 'EDID')
                if e:
                    self.npc_edids[r.form_id] = _zstring(e.data)
            # every parsed record may carry object-script VMAD attachments
            v = _get(r, 'VMAD')
            if v and r.type not in ('INFO', 'QUST'):
                try:
                    scripts, _tail = parse_vmad(v.data)
                except (ValueError, struct.error, IndexError):
                    scripts = []
                for name, props in scripts:
                    self.attachments[name.lower()].append((r.type, r.form_id, props))

        # Generated Papyrus sources + compiled set
        t0 = time.time()
        self.psc = {}
        src = os.path.join(scripts_dir, 'source')
        for fn in os.listdir(src):
            if fn.lower().endswith('.psc'):
                info = parse_psc(os.path.join(src, fn))
                key = (info.name or fn[:-4]).lower()
                self.psc[key] = info
        self.pex = {fn[:-4].lower() for fn in os.listdir(scripts_dir)
                    if fn.lower().endswith('.pex')}
        print(f'  [tes5] scripts: {len(self.psc)} psc, {len(self.pex)} pex '
              f'({time.time()-t0:.1f}s)')

        # SEQ file: u32 formids of start-game-enabled quests
        self.seq_fids = set()
        if seq_path and os.path.isfile(seq_path):
            data = open(seq_path, 'rb').read()
            self.seq_fids = set(struct.unpack(f'<{len(data)//4}I', data))

    # ── record loaders ──
    def _add_qust(self, r):
        e = _get(r, 'EDID')
        d = _get(r, 'DNAM')
        flags = struct.unpack_from('<H', d.data, 0)[0] if d else 0
        stages = []
        for s in _all(r, 'INDX'):
            if len(s.data) >= 2:
                stages.append(struct.unpack_from('<H', s.data, 0)[0])
        objectives = []
        for s in _all(r, 'QOBJ'):
            if len(s.data) >= 2:
                objectives.append(struct.unpack_from('<H', s.data, 0)[0])
        vm = _get(r, 'VMAD')
        scripts, frags = [], []
        if vm:
            try:
                scripts, tail = parse_vmad(vm.data)
                frags = parse_qust_fragments(tail)
            except (ValueError, struct.error, IndexError):
                pass
        edid = _zstring(e.data) if e else ''
        self.quests[r.form_id] = {
            'edid': edid, 'flags': flags, 'stages': stages,
            'objectives': objectives, 'scripts': scripts, 'frags': frags,
            'ctdas': parse_ctdas(r),
        }
        if edid:
            self.quest_by_edid[edid.lower()] = r.form_id

    def _add_dial(self, r):
        e = _get(r, 'EDID')
        q = _get(r, 'QNAM')
        b = _get(r, 'BNAM')
        s = _get(r, 'SNAM')
        self.dials[r.form_id] = {
            'edid': _zstring(e.data) if e else '',
            'quest': struct.unpack('<I', q.data)[0] if q and len(q.data) == 4 else 0,
            'branch': struct.unpack('<I', b.data)[0] if b and len(b.data) == 4 else 0,
            'snam': s.data[:4].decode('ascii', errors='replace') if s else '',
        }

    def _add_info(self, r):
        vm = _get(r, 'VMAD')
        scripts, frags = [], []
        if vm:
            try:
                scripts, tail = parse_vmad(vm.data)
                frags = parse_info_fragments(tail)
            except (ValueError, struct.error, IndexError):
                pass
        tclt = [struct.unpack('<I', s.data)[0] for s in _all(r, 'TCLT')
                if len(s.data) == 4]
        self.infos[r.form_id] = {
            'dial': r.parent_dial,
            'ctdas': parse_ctdas(r),
            'tclt': tclt,
            'scripts': scripts, 'frags': frags,
            'has_response': _get(r, 'NAM1') is not None,
        }


class Edge:
    """One potential stage-advancement action in the converted data."""
    __slots__ = ('gate', 'action', 'container', 'script', 'blocked_why')

    def __init__(self, gate, action, container, script):
        self.gate = gate          # ('always') | ('stage', qfid, s) | ('info', ifid) | ('quest', qfid)
        self.action = action      # ('setstage', qfid, stage|None) | ('start'|'stop'|'complete', qfid) | ('setglobal', gfid, val)
        self.container = container
        self.script = script
        self.blocked_why = None


class Tes5Engine:
    def __init__(self, d: Tes5Data, verbose=False):
        self.d = d
        self.verbose = verbose
        self.edges = []
        self.dead_info = {}       # info fid -> reason string (unfixably dead)
        self.edge_notes = defaultdict(list)   # container -> notes
        self.script_issues = []   # (script, issue) global problems
        # reverse TCLT: target DIAL fid -> [source INFO fids]
        self.tclt_rev = defaultdict(list)
        for ifid, inf in d.infos.items():
            for t in inf['tclt']:
                tgt = t if t in d.dials else d.infos.get(t, {}).get('dial')
                if tgt:
                    self.tclt_rev[tgt].append(ifid)
        self._build_edges()

    # ── helpers ──
    def _resolve_prop(self, prop, props, script, container):
        """Property name -> bound FormID (or None with a recorded reason)."""
        got = props.get(prop)
        if got is None:
            return None, f'property "{prop}" of {script} has NO VMAD value (unbound -> None at runtime)'
        kind, val = got
        if kind != 'obj':
            return None, f'property "{prop}" of {script} bound to non-object {kind}'
        if val not in self.d.all_fids and val not in self.d.vanilla_fids:
            return None, f'property "{prop}" of {script} bound to MISSING form {val:08X}'
        return val, None

    def _script_ok(self, sname):
        low = sname.lower()
        if low not in self.d.psc:
            return None, f'script {sname}: .psc source missing'
        if low not in self.d.pex:
            return None, f'script {sname}: NOT COMPILED (no .pex)'
        return self.d.psc[low], None

    def _add_actions(self, body, props, gate, container, sname, psc):
        for kind, prop, arg in extract_actions(body):
            if kind == 'say':
                fid, why = self._resolve_prop(prop, props, sname, container)
                if fid is not None and fid in self.d.dials:
                    self.edges.append(Edge(gate, ('say', fid), container, sname))
                continue
            if kind == 'setglobal':
                fid, why = self._resolve_prop(prop, props, sname, container)
                if fid is None:
                    self.edge_notes[container].append(why)
                    continue
                self.edges.append(Edge(gate, ('setglobal', fid, arg),
                                       container, sname))
            elif kind in ('setstage', 'start', 'stop', 'complete'):
                if kind == 'complete' and not prop:
                    # bare CompleteQuest() -> own quest (QF fragment)
                    qfid = container[1] if container[0] == 'QUST' else None
                    if qfid is None:
                        continue
                    self.edges.append(Edge(gate, ('complete', qfid),
                                           container, sname))
                    continue
                fid, why = self._resolve_prop(prop, props, sname, container)
                if fid is None:
                    # Self.SetStage etc. or unresolvable property
                    if prop in ('self', 'game', 'debug', 'utility'):
                        continue
                    self.edge_notes[container].append(
                        f'{kind} via {why}' if why else f'{kind} unresolved')
                    continue
                if fid not in self.d.quests:
                    if fid in self.d.vanilla_fids:
                        continue        # vanilla quest — out of audit scope
                    self.edge_notes[container].append(
                        f'{kind} target {fid:08X} is not a QUST in output')
                    continue
                if kind == 'setstage':
                    self.edges.append(Edge(gate, ('setstage', fid, arg),
                                           container, sname))
                else:
                    self.edges.append(Edge(gate, (kind, fid), container, sname))

    # ── edge construction ──
    def _build_edges(self):
        d = self.d
        # 1) QUST VMAD: stage fragments + attached quest scripts
        for qfid, q in d.quests.items():
            frag_scripts = {}
            for stage, log_idx, sname, fname in q['frags']:
                frag_scripts.setdefault(sname, []).append((stage, fname))
            for sname, props in q['scripts']:
                psc, why = self._script_ok(sname)
                if psc is None:
                    self.script_issues.append((sname, f'QUST {q["edid"]}: {why}'))
                    continue
                pmap = dict(props)
                frags = frag_scripts.get(sname)
                container = ('QUST', qfid, q['edid'])
                if frags:
                    for stage, fname in frags:
                        body = psc.functions.get(fname.lower())
                        if body is None:
                            self.script_issues.append(
                                (sname, f'QUST {q["edid"]}: fragment {fname} '
                                        f'missing from .psc'))
                            continue
                        self._add_actions(body, pmap, ('stage', qfid, stage),
                                          container, sname, psc)
                else:
                    # attached TES4 quest script: any body may run while the
                    # quest is running (GameMode etc.) — optimistic gate
                    for body in psc.functions.values():
                        self._add_actions(body, pmap, ('quest', qfid),
                                          container, sname, psc)

        # 2) INFO VMAD: TIF fragments
        for ifid, info in d.infos.items():
            for sname, props in info['scripts']:
                psc, why = self._script_ok(sname)
                if psc is None:
                    self.script_issues.append((sname, f'INFO {ifid:08X}: {why}'))
                    continue
                body = '\n'.join(psc.functions.values())
                self._add_actions(body, dict(props), ('info', ifid),
                                  ('INFO', ifid, ''), sname, psc)

        # 3) object-script attachments (base records): optimistic always-gate
        for sname, sites in d.attachments.items():
            psc, why = self._script_ok(sname)
            if psc is None:
                self.script_issues.append(
                    (sname, f'{len(sites)} attachment(s): {why}'))
                continue
            # actions resolved per attachment site (props may differ)
            for rtype, fid, props in sites:
                body = '\n'.join(psc.functions.values())
                self._add_actions(body, dict(props), ('always',),
                                  (rtype, fid, ''), sname, psc)

    # ── condition evaluation ──
    def _ctda_satisfiable(self, c: Ctda, state):
        """(ok, dead_reason). dead_reason set only for permanently-dead."""
        reached, running, revealed, completed = state
        f = c.func
        # dangling FormID param = condition can never pass
        if f in _P1_FORMID_FUNCS and c.p1 and \
                c.p1 not in self.d.all_fids and c.p1 not in self.d.vanilla_fids:
            return False, (f'{_fname(f)} param {c.p1:08X} does not exist '
                           f'in output or Skyrim.esm')
        if f == F_GETSTAGE:
            vals = reached.get(c.p1, set()) | {0}
            return any(_OPS[c.op](v, c.comp) for v in vals), None
        if f == F_GETSTAGEDONE:
            if c.op == 0 and c.comp == 1.0:
                return c.p2 in reached.get(c.p1, set()), None
            return True, None
        if f == F_GETQUESTRUNNING:
            if c.op == 0 and c.comp == 1.0:
                return c.p1 in running, None
            return True, None
        if f == F_GETGLOBALVALUE:
            edid = self.d.globs.get(c.p1, '')
            if edid.startswith('TES4Unlock_'):
                if c.op == 0 and c.comp == 1.0:
                    return c.p1 in revealed, None
            return True, None
        if f in (F_GETVMQUESTVAR, F_GETVMSCRIPTVAR):
            # CIS2 '::x_var' must exist as a Conditional var on the target script
            var = (c.cis2 or '').replace('::', '').removesuffix('_var').lower()
            names = []
            if f == F_GETVMQUESTVAR:
                q = self.d.quests.get(c.p1)
                if q:
                    names = [s for s, _p in q['scripts']]
            else:
                return True, None   # script var on a ref — can't resolve statically
            for sname in names:
                psc = self.d.psc.get(sname.lower())
                if psc and (var in psc.cond_vars or var in psc.props):
                    if var in psc.cond_vars:
                        return True, None
                    return False, (f'GetVMQuestVariable ::{var}_var: property '
                                   f'exists on {sname} but NOT Conditional')
            if names:
                return False, (f'GetVMQuestVariable ::{var}_var: no such '
                               f'variable on {"/".join(names)}')
            return False, ('GetVMQuestVariable: target quest has no attached '
                           'script')
        return True, None

    def _describe_ctda(self, c):
        """Short human-readable form of one condition."""
        from tools.tes5_esm_reader import _CTDA_FUNC_NAMES
        name = _CTDA_FUNC_NAMES.get(c.func, f'Func{c.func}')
        p = ''
        if c.p1:
            edid = (self.d.quests.get(c.p1, {}).get('edid') or
                    self.d.globs.get(c.p1) or
                    self.d.npc_edids.get(c.p1) or
                    (self.d.dials.get(c.p1) or {}).get('edid') or
                    f'{c.p1:08X}')
            p = edid
            if c.func == 59:
                p += f', {c.p2}'
        op = ['==', '!=', '>', '>=', '<', '<='][c.op]
        cis = f' [{c.cis2}]' if c.cis2 else ''
        return f'{name}({p}){cis} {op} {c.comp:g}'

    def _gate_groups_ok(self, ctdas, state):
        """(ok, reason_or_None) for a full CTDA chain. reason names the first
        unsatisfiable OR-group (prefering permanent-dead explanations)."""
        for group in and_groups(ctdas):
            oks, deads = [], []
            for c in group:
                ok, dead = self._ctda_satisfiable(c, state)
                oks.append(ok)
                if dead:
                    deads.append(dead)
            if not any(oks):
                if deads and len(deads) == len(group):
                    return False, deads[0]
                desc = ' OR '.join(self._describe_ctda(c) for c in group[:3])
                return False, f'condition never satisfiable: {desc}'
        return True, None

    # ── dialog reachability ──
    def _topic_reachable(self, dfid, state, tclt_sources):
        d = self.d.dials.get(dfid)
        if d is None:
            return False, 'DIAL missing from output'
        qfid = d['quest']
        if qfid not in self.d.quests:
            return False, f'DIAL {d["edid"]} QNAM {qfid:08X} not a quest'
        _reached, running, _rev, _comp = state
        if qfid not in running:
            return False, (f'owning quest {self.d.quests[qfid]["edid"]} '
                           f'never runs')
        if d['snam'] in _BARK_SNAMS:
            return True, None
        br = self.d.dlbrs.get(d['branch'])
        if br and br['flags'] & 0x01:
            return True, None
        if dfid in tclt_sources:
            return True, None
        if dfid in getattr(self, 'say_sources', ()):
            return True, None       # driven by Actor.Say() from a script
        if br is None and d['branch']:
            return False, f'DIAL {d["edid"]} branch {d["branch"]:08X} missing'
        srcs = self.tclt_rev.get(dfid, [])
        if br is None:
            return False, (f'DIAL {d["edid"]}: no branch and not bark — '
                           f'menu-unreachable')
        if srcs:
            return False, (f'DIAL {d["edid"]}: only reachable via TCLT from '
                           f'INFO {srcs[0]&0xFFFFFF:06X} (+{len(srcs)-1} more) '
                           f'which is itself unreachable')
        return False, (f'DIAL {d["edid"]}: branch not top-level and NO TCLT '
                       f'link points here (choice lost in conversion)')

    # ── main fixpoint ──
    def run(self):
        d = self.d
        reached = defaultdict(set)
        running = set()
        revealed = set()
        completed = set()
        self.say_sources = set()
        for qfid, q in d.quests.items():
            if q['flags'] & 0x01:
                running.add(qfid)

        for _pass in range(60):
            state = (reached, running, revealed, completed)
            # reachable TCLT targets from currently-satisfiable INFOs
            tclt_sources = set()
            info_ok = {}
            for ifid, info in d.infos.items():
                ok, dead = self._gate_groups_ok(info['ctdas'], state)
                info_ok[ifid] = ok
                if dead:
                    self.dead_info.setdefault(ifid, dead)
            # topic reachability needs TCLT sources whose own topic is
            # reachable; sweep until the transitive chain closes (choice
            # chains like CGBaurusA->B->C->D->E are 5+ links deep)
            while True:
                new_sources = set()
                for ifid, info in d.infos.items():
                    if not info_ok.get(ifid):
                        continue
                    t_ok, _why = self._topic_reachable(info['dial'], state,
                                                       tclt_sources)
                    if t_ok:
                        for target in info['tclt']:
                            new_sources.add(target)
                            # TCLT may point at an INFO; map to its topic
                            ti = d.infos.get(target)
                            if ti:
                                new_sources.add(ti['dial'])
                if new_sources <= tclt_sources:
                    break
                tclt_sources |= new_sources

            changed = False
            for e in self.edges:
                ok, why = self._edge_gate_ok(e, state, info_ok, tclt_sources)
                e.blocked_why = why
                if not ok:
                    continue
                kind = e.action[0]
                if kind == 'say':
                    if e.action[1] not in self.say_sources:
                        self.say_sources.add(e.action[1])
                        changed = True
                elif kind == 'setglobal':
                    _k, gfid, val = e.action
                    if val >= 1.0 and gfid not in revealed:
                        revealed.add(gfid)
                        changed = True
                elif kind == 'start':
                    if e.action[1] not in running:
                        running.add(e.action[1])
                        changed = True
                elif kind == 'complete':
                    if e.action[1] not in completed:
                        completed.add(e.action[1])
                        changed = True
                elif kind == 'setstage':
                    _k, qfid, stage = e.action
                    if qfid not in running:
                        running.add(qfid)
                        changed = True
                    targets = ([stage] if stage is not None
                               else d.quests[qfid]['stages'])
                    for s in targets:
                        if s not in reached[qfid]:
                            reached[qfid].add(s)
                            changed = True
            if not changed:
                break

        self.state = (reached, running, revealed, completed)
        self.tclt_sources = tclt_sources
        self.info_ok = info_ok
        return reached, running, revealed, completed

    def _edge_gate_ok(self, e, state, info_ok, tclt_sources):
        g = e.gate
        if g[0] == 'always':
            return True, None
        if g[0] == 'quest':
            reached, running, _r, _c = state
            if g[1] in running:
                return True, None
            return False, 'owning quest never runs'
        if g[0] == 'stage':
            reached = state[0]
            if g[2] in reached.get(g[1], set()):
                return True, None
            return False, f'stage {g[2]} never reached'
        if g[0] == 'info':
            ifid = g[1]
            if not info_ok.get(ifid):
                return False, (self.dead_info.get(ifid) or
                               'INFO conditions never satisfiable')
            t_ok, why = self._topic_reachable(self.d.infos[ifid]['dial'],
                                              state, tclt_sources)
            if not t_ok:
                return False, why
            return True, None
        return True, None


def _fname(idx):
    from tools.tes5_esm_reader import _CTDA_FUNC_NAMES
    return _CTDA_FUNC_NAMES.get(idx, f'Func{idx}')

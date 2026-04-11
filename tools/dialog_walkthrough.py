#!/usr/bin/env python3
"""
Dialog tree walkthrough and chain integrity checker for TES5 ESM files.

Traces dialog trees, checks TCLT chain integrity, verifies NPC dialog access,
and compares quest/dialog structures between files.

Usage:
    # Check TCLT chain integrity + orphan topics
    python tools/dialog_walkthrough.py output/oblivion.esm/Oblivion.esm --check-chains

    # Walk a specific NPC's dialog (by EditorID)
    python tools/dialog_walkthrough.py output/oblivion.esm/Oblivion.esm --npc Jauffre

    # Walk a specific quest's dialog tree
    python tools/dialog_walkthrough.py output/oblivion.esm/Oblivion.esm --quest MS13

    # Compare dialog structure between two ESMs
    python tools/dialog_walkthrough.py output/oblivion.esm/Oblivion.esm --compare "path/to/Skyrim.esm"

    # Full walkthrough report
    python tools/dialog_walkthrough.py output/oblivion.esm/Oblivion.esm --full
"""

import argparse
import io
import struct
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.tes5_esm_reader import read_tes5_file, TES5Record, _CTDA_FUNC_NAMES

# ─── helpers ────────────────────────────────────────────────────────────
_CMP = {0: '==', 1: '!=', 2: '>', 3: '>=', 4: '<', 5: '<='}
_RUNON = {0: 'Subject', 1: 'Target', 2: 'Reference', 3: 'CombatTarget',
          4: 'LinkedRef', 5: 'QuestAlias', 6: 'PackageData', 7: 'EventData'}
_CAT = {0: 'Topic', 1: 'Favor', 2: 'Scene', 3: 'Combat', 4: 'Favors',
        5: 'Detection', 6: 'Service', 7: 'Misc'}

def _get_sub(rec, sig):
    for s in rec.subrecords:
        if s.type == sig:
            return s.data
    return None

def _get_all_subs(rec, sig):
    return [s.data for s in rec.subrecords if s.type == sig]

def _edid(rec):
    d = _get_sub(rec, 'EDID')
    return d.rstrip(b'\x00').decode('utf-8', errors='replace') if d else ''

def _full(rec):
    d = _get_sub(rec, 'FULL')
    return d.rstrip(b'\x00').decode('utf-8', errors='replace') if d else ''

def _fid(data):
    return struct.unpack_from('<I', data, 0)[0] if data and len(data) >= 4 else 0

def _parse_ctda(data):
    if not data or len(data) < 32:
        return None
    tb = data[0]
    return {
        'comp': (tb >> 5) & 7, 'is_or': bool(tb & 1),
        'use_global': bool(tb & 4),
        'val': struct.unpack_from('<f', data, 4)[0],
        'func': struct.unpack_from('<H', data, 8)[0],
        'p1': struct.unpack_from('<I', data, 12)[0],
        'p2': struct.unpack_from('<I', data, 16)[0],
        'runon': struct.unpack_from('<I', data, 20)[0],
        'ref': struct.unpack_from('<I', data, 24)[0],
    }

def _ctda_str(c):
    fname = _CTDA_FUNC_NAMES.get(c['func'], f'Func{c["func"]}')
    cmp = _CMP.get(c['comp'], '??')
    ro = _RUNON.get(c['runon'], f'RunOn{c["runon"]}')
    parts = [f'{fname}({c["p1"]:08X},{c["p2"]:08X})']
    parts.append(f'{cmp} {c["val"]:.1f}')
    parts.append(f'[{ro}]')
    if c['is_or']:
        parts.append('OR')
    if c['use_global']:
        parts.append('GLOB')
    return ' '.join(parts)

def _get_ctdas(rec):
    return [c for c in (_parse_ctda(s.data) for s in rec.subrecords if s.type == 'CTDA') if c]

def _dial_cat_snam(rec):
    data = _get_sub(rec, 'DATA')
    snam = _get_sub(rec, 'SNAM')
    cat = data[1] if data and len(data) >= 2 else -1
    code = snam[:4].decode('ascii', errors='?') if snam and len(snam) >= 4 else '????'
    return cat, code


# ─── loading ────────────────────────────────────────────────────────────
class DialogDB:
    """Indexed dialog database from a TES5 ESM file."""

    def __init__(self, path):
        self.path = path
        _hdr, all_recs, _loc = read_tes5_file(path)
        self.dials = {}    # fid -> rec
        self.infos = defaultdict(list)  # dial_fid -> [rec, ...]
        self.qusts = {}    # fid -> rec
        self.dlbrs = {}    # fid -> rec
        self.npcs = {}     # fid -> rec
        self.vtyps = {}    # fid -> rec

        for r in all_recs:
            if r.type == 'DIAL':
                self.dials[r.form_id] = r
            elif r.type == 'INFO':
                self.infos[r.parent_dial].append(r)
            elif r.type == 'QUST':
                self.qusts[r.form_id] = r
            elif r.type == 'DLBR':
                self.dlbrs[r.form_id] = r
            elif r.type == 'NPC_':
                self.npcs[r.form_id] = r
            elif r.type == 'VTYP':
                self.vtyps[r.form_id] = r

        # Build reverse indexes
        self._dial_by_edid = {_edid(d): d for d in self.dials.values() if _edid(d)}
        self._qust_by_edid = {_edid(q): q for q in self.qusts.values() if _edid(q)}
        self._npc_by_edid = {_edid(n): n for n in self.npcs.values() if _edid(n)}
        self._dlbr_to_dial = {}
        for d in self.dials.values():
            bnam = _get_sub(d, 'BNAM')
            if bnam:
                self._dlbr_to_dial[_fid(bnam)] = d.form_id

        # Quest -> DIAL mapping
        self._qust_to_dials = defaultdict(list)
        for d in self.dials.values():
            qnam = _get_sub(d, 'QNAM')
            if qnam:
                self._qust_to_dials[_fid(qnam)].append(d.form_id)


# ─── chain integrity ───────────────────────────────────────────────────
def check_chains(db: DialogDB):
    """Check TCLT chain integrity and find orphan topics."""
    print('=== TCLT CHAIN INTEGRITY ===')
    broken_tclt = []
    total_tclt = 0
    for dial_fid, info_list in db.infos.items():
        for info in info_list:
            tclts = _get_all_subs(info, 'TCLT')
            for t in tclts:
                total_tclt += 1
                target_fid = _fid(t)
                if target_fid and target_fid not in db.dials:
                    broken_tclt.append((info.form_id, dial_fid, target_fid))

    print(f'  Total TCLT links: {total_tclt}')
    print(f'  Broken TCLT (target DIAL missing): {len(broken_tclt)}')
    for info_fid, dial_fid, target_fid in broken_tclt[:20]:
        d = db.dials.get(dial_fid)
        print(f'    INFO {info_fid:08X} in DIAL {_edid(d) if d else f"{dial_fid:08X}"} '
              f'-> missing DIAL {target_fid:08X}')
    if len(broken_tclt) > 20:
        print(f'    ... and {len(broken_tclt) - 20} more')

    # Find orphan topics: non-bark, non-chain DIALs with no DLBR and not reachable via TCLT
    print('\n=== ORPHAN TOPIC CHECK ===')
    tclt_targets = set()
    for dial_fid, info_list in db.infos.items():
        for info in info_list:
            for t in _get_all_subs(info, 'TCLT'):
                tclt_targets.add(_fid(t))

    has_dlbr = set()
    for d in db.dials.values():
        bnam = _get_sub(d, 'BNAM')
        if bnam and _fid(bnam):
            has_dlbr.add(d.form_id)

    orphans = []
    for d in db.dials.values():
        cat, snam = _dial_cat_snam(d)
        if cat != 0:  # Only check conversation topics
            continue
        if d.form_id in has_dlbr:
            continue
        if d.form_id in tclt_targets:
            continue
        orphans.append(d)

    print(f'  Conversation topics (cat=0): {sum(1 for d in db.dials.values() if _dial_cat_snam(d)[0] == 0)}')
    print(f'  With DLBR (top-level): {len(has_dlbr)}')
    print(f'  Reachable via TCLT only: {len(tclt_targets & set(d.form_id for d in db.dials.values() if _dial_cat_snam(d)[0] == 0))}')
    print(f'  Orphans (unreachable): {len(orphans)}')
    for d in orphans[:30]:
        info_count = len(db.infos.get(d.form_id, []))
        print(f'    DIAL {d.form_id:08X} ({_edid(d)}) FULL="{_full(d)}" infos={info_count}')
    if len(orphans) > 30:
        print(f'    ... and {len(orphans) - 30} more')

    # DLBR -> DIAL -> INFO chain completeness
    print('\n=== DLBR->DIAL->INFO CHAIN ===')
    empty_chains = []
    for br_fid, br in db.dlbrs.items():
        snam = _get_sub(br, 'SNAM')
        if not snam:
            continue
        starting_dial = _fid(snam)
        if starting_dial not in db.dials:
            empty_chains.append((br_fid, starting_dial, 'DIAL missing'))
        elif not db.infos.get(starting_dial):
            empty_chains.append((br_fid, starting_dial, 'no INFOs'))

    print(f'  DLBRs with starting DIAL: {sum(1 for b in db.dlbrs.values() if _get_sub(b, "SNAM"))}')
    print(f'  Broken chains: {len(empty_chains)}')
    for br_fid, dial_fid, reason in empty_chains[:20]:
        br = db.dlbrs.get(br_fid)
        print(f'    DLBR {br_fid:08X} ({_edid(br) if br else "?"}) -> DIAL {dial_fid:08X}: {reason}')
    if len(empty_chains) > 20:
        print(f'    ... and {len(empty_chains) - 20} more')

    return broken_tclt, orphans, empty_chains


# ─── NPC dialog walkthrough ────────────────────────────────────────────
def walk_npc(db: DialogDB, npc_edid: str):
    """Simulate dialog access for a specific NPC."""
    npc = db._npc_by_edid.get(npc_edid)
    if not npc:
        # Try partial match
        matches = [e for e in db._npc_by_edid if npc_edid.lower() in e.lower()]
        if matches:
            print(f'NPC "{npc_edid}" not found. Did you mean: {", ".join(matches[:10])}?')
        else:
            print(f'NPC "{npc_edid}" not found among {len(db.npcs)} NPCs.')
        return

    fid = npc.form_id
    edid = _edid(npc)
    full = _full(npc)
    vtck = _get_sub(npc, 'VTCK')
    vtck_fid = _fid(vtck) if vtck else 0
    vtyp = db.vtyps.get(vtck_fid)
    vtyp_edid = _edid(vtyp) if vtyp else '(none)'

    print(f'=== NPC: {edid} ("{full}") ===')
    print(f'  FormID: {fid:08X}')
    print(f'  VoiceType: {vtck_fid:08X} ({vtyp_edid})')

    # Find all INFOs with GetIsID pointing to this NPC
    npc_specific = []
    for dial_fid, info_list in db.infos.items():
        for info in info_list:
            for c in _get_ctdas(info):
                if c['func'] == 72 and c['p1'] == fid:  # GetIsID
                    npc_specific.append((dial_fid, info))
                    break

    print(f'\n  INFOs with GetIsID({fid:08X}): {len(npc_specific)}')

    # Group by topic
    by_topic = defaultdict(list)
    for dial_fid, info in npc_specific:
        by_topic[dial_fid].append(info)

    # Show greetings
    print(f'\n  --- Greetings (HELO) ---')
    greetings_shown = 0
    for dial_fid, info_list in sorted(by_topic.items()):
        dial = db.dials.get(dial_fid)
        if not dial:
            continue
        cat, snam = _dial_cat_snam(dial)
        if snam != 'HELO':
            continue
        for info in info_list:
            greetings_shown += 1
            ctdas = _get_ctdas(info)
            # Get response text
            texts = []
            for s in info.subrecords:
                if s.type == 'NAM1':
                    texts.append(s.data.rstrip(b'\x00').decode('utf-8', errors='replace'))
            resp = texts[0] if texts else '(no text)'
            print(f'    INFO {info.form_id:08X}: "{resp[:80]}"')
            for c in ctdas:
                print(f'      {_ctda_str(c)}')
    if greetings_shown == 0:
        print('    (none)')

    # Show conversation topics
    print(f'\n  --- Conversation Topics (CUST with DLBR) ---')
    conv_shown = 0
    for dial_fid, info_list in sorted(by_topic.items()):
        dial = db.dials.get(dial_fid)
        if not dial:
            continue
        cat, snam = _dial_cat_snam(dial)
        if snam != 'CUST':
            continue
        bnam = _get_sub(dial, 'BNAM')
        if not bnam or not _fid(bnam):
            continue  # Not top-level
        conv_shown += 1
        print(f'    DIAL {dial_fid:08X} ({_edid(dial)}) FULL="{_full(dial)}"')
        for info in info_list:
            texts = []
            for s in info.subrecords:
                if s.type == 'NAM1':
                    texts.append(s.data.rstrip(b'\x00').decode('utf-8', errors='replace'))
            resp = texts[0] if texts else '(no text)'
            tclts = [_fid(t) for t in _get_all_subs(info, 'TCLT')]
            print(f'      INFO {info.form_id:08X}: "{resp[:80]}"')
            for c in _get_ctdas(info):
                print(f'        {_ctda_str(c)}')
            for tclt_fid in tclts:
                td = db.dials.get(tclt_fid)
                print(f'        -> TCLT {tclt_fid:08X} ({_edid(td) if td else "MISSING"})')
    if conv_shown == 0:
        print('    (none)')

    # Show bark topics (non-HELO)
    print(f'\n  --- Other Barks ---')
    barks_shown = 0
    for dial_fid, info_list in sorted(by_topic.items()):
        dial = db.dials.get(dial_fid)
        if not dial:
            continue
        cat, snam = _dial_cat_snam(dial)
        if snam in ('HELO', 'CUST'):
            continue
        barks_shown += 1
        print(f'    DIAL {_edid(dial)} ({snam} cat={cat})')
        for info in info_list[:3]:
            texts = []
            for s in info.subrecords:
                if s.type == 'NAM1':
                    texts.append(s.data.rstrip(b'\x00').decode('utf-8', errors='replace'))
            print(f'      INFO {info.form_id:08X}: "{texts[0][:60] if texts else "(no text)"}"')
        if len(info_list) > 3:
            print(f'      ... and {len(info_list) - 3} more INFOs')
    if barks_shown == 0:
        print('    (none)')

    # Voice-type matched barks (not NPC-specific but would match via voice type)
    print(f'\n  --- Barks via VoiceType ({vtyp_edid}) ---')
    vt_barks = 0
    for dial_fid, info_list in db.infos.items():
        dial = db.dials.get(dial_fid)
        if not dial:
            continue
        cat, snam = _dial_cat_snam(dial)
        if cat == 0 and snam == 'CUST':
            continue  # Skip conversation topics
        for info in info_list:
            ctdas = _get_ctdas(info)
            has_vtyp_match = any(
                c['func'] == 426 and c['p1'] == vtck_fid
                for c in ctdas
            )
            if has_vtyp_match:
                vt_barks += 1
    print(f'    Total bark INFOs matching VoiceType: {vt_barks}')


# ─── quest walkthrough ─────────────────────────────────────────────────
def walk_quest(db: DialogDB, quest_edid: str):
    """Walk a specific quest's dialog tree."""
    qust = db._qust_by_edid.get(quest_edid)
    if not qust:
        matches = [e for e in db._qust_by_edid if quest_edid.lower() in e.lower()]
        if matches:
            print(f'Quest "{quest_edid}" not found. Did you mean: {", ".join(matches[:15])}?')
        else:
            print(f'Quest "{quest_edid}" not found among {len(db.qusts)} quests.')
        return

    fid = qust.form_id
    edid = _edid(qust)
    dnam = _get_sub(qust, 'DNAM')

    print(f'=== QUEST: {edid} ===')
    print(f'  FormID: {fid:08X}')
    if dnam and len(dnam) >= 12:
        flags, pri, fv = struct.unpack_from('<HBB', dnam, 0)
        qtype = struct.unpack_from('<I', dnam, 8)[0]
        print(f'  Flags: 0x{flags:04X}, Priority: {pri}, FormVer: {fv}, Type: {qtype}')

    # Show quest stages
    stages = []
    in_stage = False
    cur_idx = -1
    for s in qust.subrecords:
        if s.type == 'INDX' and len(s.data) >= 4:
            cur_idx = struct.unpack_from('<H', s.data, 0)[0]
            sflags = s.data[2] if len(s.data) > 2 else 0
            stages.append({'idx': cur_idx, 'flags': sflags, 'logs': []})
        elif s.type == 'CNAM' and stages:
            stages[-1]['logs'].append(s.data.rstrip(b'\x00').decode('utf-8', errors='replace'))

    if stages:
        print(f'\n  --- Stages ({len(stages)}) ---')
        for st in stages[:20]:
            flag_str = []
            if st['flags'] & 0x02:
                flag_str.append('Complete')
            if st['flags'] & 0x04:
                flag_str.append('Fail')
            fstr = f' [{",".join(flag_str)}]' if flag_str else ''
            print(f'    Stage {st["idx"]}{fstr}')
            for log in st['logs']:
                print(f'      "{log[:100]}"')
        if len(stages) > 20:
            print(f'    ... and {len(stages) - 20} more stages')

    # Show quest's dialog topics
    dial_fids = db._qust_to_dials.get(fid, [])
    print(f'\n  --- Dialog Topics ({len(dial_fids)}) ---')

    # Group by category
    by_cat = defaultdict(list)
    for df in dial_fids:
        d = db.dials.get(df)
        if d:
            cat, snam = _dial_cat_snam(d)
            by_cat[(cat, snam)].append(d)

    for (cat, snam), dial_list in sorted(by_cat.items()):
        cat_name = _CAT.get(cat, f'cat{cat}')
        print(f'\n    [{cat_name}/{snam}] ({len(dial_list)} topics)')
        for d in dial_list[:10]:
            info_count = len(db.infos.get(d.form_id, []))
            has_branch = bool(_get_sub(d, 'BNAM'))
            bstr = ' [DLBR]' if has_branch else ''
            print(f'      DIAL {d.form_id:08X} ({_edid(d)}) FULL="{_full(d)}"{bstr} infos={info_count}')

            # Show first few infos
            for info in db.infos.get(d.form_id, [])[:3]:
                texts = [s.data.rstrip(b'\x00').decode('utf-8', errors='replace')
                         for s in info.subrecords if s.type == 'NAM1']
                ctdas = _get_ctdas(info)
                tclts = [_fid(t) for t in _get_all_subs(info, 'TCLT')]
                resp = texts[0][:60] if texts else '(no text)'
                print(f'        INFO {info.form_id:08X}: "{resp}"')
                for c in ctdas[:3]:
                    print(f'          {_ctda_str(c)}')
                if len(ctdas) > 3:
                    print(f'          ... +{len(ctdas)-3} conditions')
                for tf in tclts:
                    td = db.dials.get(tf)
                    print(f'          -> {tf:08X} ({_edid(td) if td else "MISSING"})')

        if len(dial_list) > 10:
            print(f'      ... and {len(dial_list) - 10} more topics')


# ─── structural comparison ─────────────────────────────────────────────
def compare_structure(db1: DialogDB, db2: DialogDB, label1='Ours', label2='Skyrim'):
    """Compare overall dialog architecture between two ESMs."""
    print(f'=== STRUCTURAL COMPARISON: {label1} vs {label2} ===\n')

    # Record counts
    print(f'  {"Metric":<35} {label1:>10} {label2:>10}')
    print(f'  {"-"*35} {"-"*10} {"-"*10}')
    for name, attr in [('DIAL topics', 'dials'), ('INFO responses', 'infos'),
                       ('QUST quests', 'qusts'), ('DLBR branches', 'dlbrs'),
                       ('NPC_ actors', 'npcs'), ('VTYP voice types', 'vtyps')]:
        d1 = getattr(db1, attr)
        d2 = getattr(db2, attr)
        c1 = sum(len(v) for v in d1.values()) if isinstance(d1, defaultdict) else len(d1)
        c2 = sum(len(v) for v in d2.values()) if isinstance(d2, defaultdict) else len(d2)
        print(f'  {name:<35} {c1:>10} {c2:>10}')

    # Category distribution
    print(f'\n  --- DIAL Category Distribution ---')
    def cat_dist(db):
        counts = defaultdict(int)
        for d in db.dials.values():
            cat, snam = _dial_cat_snam(d)
            counts[(cat, snam)] += 1
        return counts

    c1 = cat_dist(db1)
    c2 = cat_dist(db2)
    all_keys = sorted(set(c1) | set(c2))
    print(f'  {"Category":<25} {label1:>8} {label2:>8}')
    print(f'  {"-"*25} {"-"*8} {"-"*8}')
    for k in all_keys:
        cat_name = _CAT.get(k[0], f'cat{k[0]}')
        key_str = f'{cat_name}/{k[1]}'
        print(f'  {key_str:<25} {c1.get(k, 0):>8} {c2.get(k, 0):>8}')

    # DLBR DNAM distribution
    print(f'\n  --- DLBR DNAM (Branch Type) Distribution ---')
    def dlbr_dnam_dist(db):
        counts = defaultdict(int)
        for b in db.dlbrs.values():
            dnam = _get_sub(b, 'DNAM')
            if dnam and len(dnam) >= 4:
                val = struct.unpack_from('<I', dnam, 0)[0]
                counts[val] += 1
            else:
                counts['missing'] += 1
        return counts

    d1 = dlbr_dnam_dist(db1)
    d2 = dlbr_dnam_dist(db2)
    dnam_names = {0: 'Normal', 1: 'TopLevel', 2: 'Blocking', 4: 'Exclusive'}
    all_dnam = sorted(set(d1) | set(d2), key=lambda x: x if isinstance(x, int) else 999)
    print(f'  {"DNAM Value":<25} {label1:>8} {label2:>8}')
    print(f'  {"-"*25} {"-"*8} {"-"*8}')
    for k in all_dnam:
        name = dnam_names.get(k, str(k))
        print(f'  {name:<25} {d1.get(k, 0):>8} {d2.get(k, 0):>8}')

    # QUST flags distribution
    print(f'\n  --- QUST Flags Distribution ---')
    def qust_flag_dist(db):
        counts = defaultdict(int)
        for q in db.qusts.values():
            dnam = _get_sub(q, 'DNAM')
            if dnam and len(dnam) >= 2:
                flags = struct.unpack_from('<H', dnam, 0)[0]
                counts[flags] += 1
        return counts

    q1 = qust_flag_dist(db1)
    q2 = qust_flag_dist(db2)
    all_qflags = sorted(set(q1) | set(q2))
    print(f'  {"Flags":<25} {label1:>8} {label2:>8}')
    print(f'  {"-"*25} {"-"*8} {"-"*8}')
    for k in all_qflags[:20]:
        bits = []
        if k & 0x01: bits.append('StartGameEnabled')
        if k & 0x02: bits.append('Running')
        if k & 0x04: bits.append('AllowRepeatedStages')
        if k & 0x08: bits.append('StartsEnabled')
        if k & 0x10: bits.append('StartsEnabled_2')
        if k & 0x8000: bits.append('HasDialogueData')
        nm = '|'.join(bits) if bits else 'None'
        print(f'  0x{k:04X} ({nm[:40]}){" "*(25-min(len(f"0x{k:04X} ({nm[:40]})"), 25))} {q1.get(k, 0):>8} {q2.get(k, 0):>8}')

    # Condition function distribution comparison
    print(f'\n  --- Top CTDA Functions ---')
    def func_dist(db):
        counts = defaultdict(int)
        for dial_fid, info_list in db.infos.items():
            for info in info_list:
                for c in _get_ctdas(info):
                    counts[c['func']] += 1
        return counts

    f1 = func_dist(db1)
    f2 = func_dist(db2)
    top_funcs = sorted(set(f1) | set(f2), key=lambda x: -(f1.get(x, 0) + f2.get(x, 0)))[:25]
    print(f'  {"Function":<30} {label1:>8} {label2:>8}')
    print(f'  {"-"*30} {"-"*8} {"-"*8}')
    for fn in top_funcs:
        fname = _CTDA_FUNC_NAMES.get(fn, f'Func{fn}')
        print(f'  {fname:<30} {f1.get(fn, 0):>8} {f2.get(fn, 0):>8}')


# ─── full report ────────────────────────────────────────────────────────
def full_report(db: DialogDB):
    """Generate a comprehensive dialog health report."""
    broken_tclt, orphans, empty_chains = check_chains(db)

    # Info about topics with most INFOs
    print('\n=== TOP TOPICS BY INFO COUNT ===')
    topic_sizes = [(fid, len(il)) for fid, il in db.infos.items() if fid in db.dials]
    topic_sizes.sort(key=lambda x: -x[1])
    for fid, count in topic_sizes[:15]:
        d = db.dials[fid]
        cat, snam = _dial_cat_snam(d)
        print(f'  DIAL {fid:08X} ({_edid(d)}) [{snam}]: {count} INFOs')

    # Quests with most topics
    print('\n=== TOP QUESTS BY TOPIC COUNT ===')
    qust_topics = [(qfid, len(dl)) for qfid, dl in db._qust_to_dials.items() if qfid in db.qusts]
    qust_topics.sort(key=lambda x: -x[1])
    for qfid, count in qust_topics[:15]:
        q = db.qusts[qfid]
        print(f'  QUST {qfid:08X} ({_edid(q)}): {count} topics')

    # Sample some well-known quest EditorIDs for walkthrough
    well_known = ['MQ', 'MS', 'Dark', 'TG', 'MG', 'CW', 'Companion']
    found_quests = []
    for prefix in well_known:
        matches = [e for e in db._qust_by_edid if e.startswith(prefix)]
        found_quests.extend(matches[:3])

    if found_quests:
        print(f'\n=== SAMPLE QUEST WALKTHROUGHS ===')
        for qe in found_quests[:5]:
            print()
            walk_quest(db, qe)

    # Summary stats
    print('\n=== SUMMARY ===')
    total_infos_with_ctda = sum(
        1 for il in db.infos.values() for i in il if _get_ctdas(i)
    )
    total_infos = sum(len(il) for il in db.infos.values())
    print(f'  Total INFOs: {total_infos}')
    print(f'  INFOs with conditions: {total_infos_with_ctda} ({total_infos_with_ctda*100/total_infos:.1f}%)')
    print(f'  Broken TCLT links: {len(broken_tclt)}')
    print(f'  Orphan topics: {len(orphans)}')
    print(f'  Empty DLBR chains: {len(empty_chains)}')


# ─── main ───────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description='Dialog tree walkthrough and chain checker')
    p.add_argument('esm', help='Path to ESM/ESP file')
    p.add_argument('--check-chains', action='store_true', help='Check TCLT chain integrity')
    p.add_argument('--npc', help='Walk dialog for NPC by EditorID')
    p.add_argument('--quest', help='Walk dialog for quest by EditorID')
    p.add_argument('--compare', help='Compare with another ESM file')
    p.add_argument('--full', action='store_true', help='Full walkthrough report')
    args = p.parse_args()

    print(f'Loading {args.esm}...')
    db = DialogDB(args.esm)
    print(f'  {len(db.dials)} DIALs, {sum(len(v) for v in db.infos.values())} INFOs, '
          f'{len(db.qusts)} QUSTs, {len(db.dlbrs)} DLBRs, {len(db.npcs)} NPCs, {len(db.vtyps)} VTYPs')

    if args.check_chains:
        check_chains(db)

    if args.npc:
        print()
        walk_npc(db, args.npc)

    if args.quest:
        print()
        walk_quest(db, args.quest)

    if args.compare:
        print(f'\nLoading {args.compare}...')
        db2 = DialogDB(args.compare)
        print(f'  {len(db2.dials)} DIALs, {sum(len(v) for v in db2.infos.values())} INFOs, '
              f'{len(db2.qusts)} QUSTs, {len(db2.dlbrs)} DLBRs')
        print()
        compare_structure(db, db2, 'Ours', 'Skyrim')

    if args.full:
        print()
        full_report(db)

    if not any([args.check_chains, args.npc, args.quest, args.compare, args.full]):
        print('\nUse --check-chains, --npc, --quest, --compare, or --full for analysis.')


if __name__ == '__main__':
    sys.exit(main() or 0)

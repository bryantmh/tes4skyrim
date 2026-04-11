#!/usr/bin/env python3
"""
Advanced dialog/quest structure validator for TES5 conversion output.

Cross-checks a converted plugin against Skyrim.esm patterns:
- DIAL/INFO/QUST/DLBR/DLVW structural integrity
- CTDA condition patterns (voice type scope, function usage)
- QUST flag/field validation
- INFO subrecord completeness
- NPC->VTYP cross-references on bark INFOs

Usage:
    python tools/dialog_validator.py output/oblivion.esm/Oblivion.esm
    python tools/dialog_validator.py output/oblivion.esm/Oblivion.esm --verbose
    python tools/dialog_validator.py output/oblivion.esm/Oblivion.esm --check-npc
"""

import argparse
import io
import mmap
import os
import struct
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Import the ESM reader for parsing
sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.tes5_esm_reader import (
    read_tes5_file, TES5Record, Sub, _CTDA_FUNC_NAMES,
)


def _parse_ctda(data: bytes) -> dict:
    """Parse a 32-byte CTDA into a dict."""
    if len(data) < 32:
        return {}
    type_byte = data[0]
    return {
        'comp_type': (type_byte >> 5) & 0x07,
        'is_or': bool(type_byte & 0x01),
        'use_global': bool(type_byte & 0x04),
        'comp_val': struct.unpack_from('<f', data, 4)[0],
        'func_idx': struct.unpack_from('<H', data, 8)[0],
        'param1': struct.unpack_from('<I', data, 12)[0],
        'param2': struct.unpack_from('<I', data, 16)[0],
        'run_on': struct.unpack_from('<I', data, 20)[0],
        'ref': struct.unpack_from('<I', data, 24)[0],
    }


def _func_name(idx: int) -> str:
    return _CTDA_FUNC_NAMES.get(idx, f'Func{idx}')


def collect_records(esm_path: str, types: set) -> dict:
    """Parse ESM and collect records of given types keyed by signature."""
    by_type = defaultdict(list)
    _header, all_records, _localized = read_tes5_file(esm_path)
    for rec in all_records:
        if rec.type in types:
            by_type[rec.type].append(rec)
    return dict(by_type)


def _get_sub(rec: TES5Record, sig: str):
    """Get first subrecord data by signature. Returns None if not found."""
    for s in rec.subrecords:
        if s.type == sig:
            return s.data
    return None


def _get_all_subs(rec: TES5Record, sig: str) -> list:
    """Get all subrecord data by signature."""
    return [s.data for s in rec.subrecords if s.type == sig]


def _get_edid(rec: TES5Record) -> str:
    data = _get_sub(rec, 'EDID')
    return data.rstrip(b'\x00').decode('utf-8', errors='replace') if data else ''


def _get_ctdas(rec: TES5Record) -> list:
    """Get all parsed CTDAs from a record."""
    return [_parse_ctda(s.data) for s in rec.subrecords if s.type == 'CTDA' and len(s.data) >= 32]


def validate_quest_structure(qusts: list) -> list:
    """Validate QUST records against Skyrim patterns."""
    issues = []
    for rec in qusts:
        fid = rec.form_id
        edid = _get_edid(rec)
        dnam = _get_sub(rec, 'DNAM')
        if dnam is None or len(dnam) < 12:
            issues.append(f'QUST {fid:08X} ({edid}): missing or short DNAM')
            continue
        flags, priority, formver = struct.unpack_from('<HBB', dnam, 0)
        qtype = struct.unpack_from('<I', dnam, 8)[0]

        if formver != 0:
            issues.append(f'QUST {fid:08X} ({edid}): DNAM.FormVer={formver} (should be 0)')
        if _get_sub(rec, 'NEXT') is None:
            issues.append(f'QUST {fid:08X} ({edid}): missing NEXT subrecord')
        if _get_sub(rec, 'ANAM') is None:
            issues.append(f'QUST {fid:08X} ({edid}): missing ANAM subrecord')

        # Check for HasDialogueData flag which blocks processing
        if flags & 0x8000:
            issues.append(f'QUST {fid:08X} ({edid}): HasDialogueData flag (0x8000) set — blocks dialogue!')
    return issues


def validate_dial_structure(dials: list, dlbr_by_fid: dict) -> list:
    """Validate DIAL records."""
    issues = []
    for rec in dials:
        fid = rec.form_id
        edid = _get_edid(rec)

        # Must have QNAM
        qnam = _get_sub(rec, 'QNAM')
        if qnam is None:
            issues.append(f'DIAL {fid:08X} ({edid}): missing QNAM — engine ignores topic!')

        # DATA must be 4 bytes
        data = _get_sub(rec, 'DATA')
        if data is None or len(data) != 4:
            issues.append(f'DIAL {fid:08X} ({edid}): DATA size={len(data)} (expected 4)')
            continue

        cat = data[1]
        bnam = _get_sub(rec, 'BNAM')
        if bnam is not None:
            # Verify BNAM references a valid DLBR
            branch_fid = struct.unpack_from('<I', bnam, 0)[0]
            if branch_fid and branch_fid not in dlbr_by_fid:
                issues.append(f'DIAL {fid:08X} ({edid}): BNAM={branch_fid:08X} references non-existent DLBR')

        # SNAM must be 4 bytes
        snam = _get_sub(rec, 'SNAM')
        if snam is None or len(snam) != 4:
            issues.append(f'DIAL {fid:08X} ({edid}): SNAM size={len(snam) if snam else 0} (expected 4)')

        # TIFC must be present
        tifc = _get_sub(rec, 'TIFC')
        if tifc is None:
            issues.append(f'DIAL {fid:08X} ({edid}): missing TIFC')
    return issues


def validate_info_structure(infos: list) -> list:
    """Validate INFO records for structural integrity."""
    issues = []
    for rec in infos:
        fid = rec.form_id

        # ENAM required
        enam = _get_sub(rec, 'ENAM')
        if enam is None:
            issues.append(f'INFO {fid:08X}: missing ENAM')
        elif len(enam) != 4:
            issues.append(f'INFO {fid:08X}: ENAM size={len(enam)} (expected 4)')

        # CNAM required (1 byte favor level)
        cnam = _get_sub(rec, 'CNAM')
        if cnam is None:
            issues.append(f'INFO {fid:08X}: missing CNAM')
        elif len(cnam) != 1:
            issues.append(f'INFO {fid:08X}: CNAM size={len(cnam)} (expected 1)')

        # TRDT must be 24 bytes each
        for s in rec.subrecords:
            if s.type == 'TRDT' and len(s.data) != 24:
                issues.append(f'INFO {fid:08X}: TRDT size={len(s.data)} (expected 24)')

        # CTDA must be 32 bytes each
        for s in rec.subrecords:
            if s.type == 'CTDA' and len(s.data) != 32:
                issues.append(f'INFO {fid:08X}: CTDA size={len(s.data)} (expected 32)')

    return issues


def validate_dlbr_structure(dlbrs: list, dial_by_fid: dict, qust_by_fid: dict) -> list:
    """Validate DLBR records."""
    issues = []
    for rec in dlbrs:
        fid = rec.form_id
        edid = _get_edid(rec)

        qnam = _get_sub(rec, 'QNAM')
        if qnam is None:
            issues.append(f'DLBR {fid:08X} ({edid}): missing QNAM')
        else:
            quest_fid = struct.unpack_from('<I', qnam, 0)[0]
            if quest_fid and quest_fid not in qust_by_fid:
                issues.append(f'DLBR {fid:08X} ({edid}): QNAM={quest_fid:08X} references non-existent QUST')

        snam = _get_sub(rec, 'SNAM')
        if snam is not None:
            topic_fid = struct.unpack_from('<I', snam, 0)[0]
            if topic_fid and topic_fid not in dial_by_fid:
                issues.append(f'DLBR {fid:08X} ({edid}): SNAM={topic_fid:08X} references non-existent DIAL')

        dnam = _get_sub(rec, 'DNAM')
        if dnam is None:
            issues.append(f'DLBR {fid:08X} ({edid}): missing DNAM')
    return issues


def analyze_voice_type_patterns(infos: list, dials: list) -> dict:
    """Analyze voice type injection patterns per topic category."""
    dial_info = {}
    for d in dials:
        data = _get_sub(d, 'DATA')
        cat = data[1] if len(data) >= 2 else -1
        snam = _get_sub(d, 'SNAM')
        code = snam[:4].decode('ascii', errors='?') if len(snam) >= 4 else '????'
        dial_info[d.form_id] = {'cat': cat, 'snam': code, 'edid': _get_edid(d)}

    stats = defaultdict(lambda: {'total': 0, 'with_vtyp': 0, 'no_ctda': 0})
    for rec in infos:
        parent = rec.parent_dial
        info = dial_info.get(parent, {'cat': -1, 'snam': '????', 'edid': '?'})
        key = f"cat={info['cat']}({info['snam']})"
        stats[key]['total'] += 1
        ctdas = _get_ctdas(rec)
        has_vtyp = any(c['func_idx'] == 426 for c in ctdas)
        if has_vtyp:
            stats[key]['with_vtyp'] += 1
        if not ctdas:
            stats[key]['no_ctda'] += 1

    return dict(stats)


def analyze_npc_vtck(esm_path: str) -> dict:
    """Check NPC_ records for VTCK presence."""
    npcs = collect_records(esm_path, {'NPC_'}).get('NPC_', [])
    total = 0
    with_vtck = 0
    vtck_fids = set()
    for rec in npcs:
        total += 1
        vtck = _get_sub(rec, 'VTCK')
        if vtck and len(vtck) >= 4:
            with_vtck += 1
            vtck_fids.add(struct.unpack_from('<I', vtck, 0)[0])
    return {
        'total': total,
        'with_vtck': with_vtck,
        'missing_vtck': total - with_vtck,
        'unique_vtyp_fids': vtck_fids,
    }


def main():
    parser = argparse.ArgumentParser(description='Validate dialog structure in TES5 output')
    parser.add_argument('esm', help='Path to ESM/ESP file')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show all details')
    parser.add_argument('--check-npc', action='store_true', help='Also check NPC_ VTCK')
    args = parser.parse_args()

    print(f'Loading {args.esm}...')
    types = {'DIAL', 'INFO', 'QUST', 'DLBR', 'DLVW', 'VTYP'}
    recs = collect_records(args.esm, types)

    dials = recs.get('DIAL', [])
    infos = recs.get('INFO', [])
    qusts = recs.get('QUST', [])
    dlbrs = recs.get('DLBR', [])
    dlvws = recs.get('DLVW', [])
    vtyps = recs.get('VTYP', [])

    print(f'\nRecord counts: DIAL={len(dials)}, INFO={len(infos)}, QUST={len(qusts)}, '
          f'DLBR={len(dlbrs)}, DLVW={len(dlvws)}, VTYP={len(vtyps)}')

    # Build lookup tables
    dial_by_fid = {d.form_id: d for d in dials}
    qust_by_fid = {q.form_id: q for q in qusts}
    dlbr_by_fid = {b.form_id: b for b in dlbrs}
    vtyp_fids = {v.form_id for v in vtyps}

    # Run validations
    print('\n=== QUEST VALIDATION ===')
    qust_issues = validate_quest_structure(qusts)
    print(f'  {len(qusts)} QUSTs checked, {len(qust_issues)} issues')
    for issue in qust_issues[:20]:
        print(f'    {issue}')
    if len(qust_issues) > 20:
        print(f'    ... and {len(qust_issues) - 20} more')

    print('\n=== DIAL VALIDATION ===')
    dial_issues = validate_dial_structure(dials, dlbr_by_fid)
    print(f'  {len(dials)} DIALs checked, {len(dial_issues)} issues')
    for issue in dial_issues[:20]:
        print(f'    {issue}')
    if len(dial_issues) > 20:
        print(f'    ... and {len(dial_issues) - 20} more')

    print('\n=== INFO VALIDATION ===')
    info_issues = validate_info_structure(infos)
    print(f'  {len(infos)} INFOs checked, {len(info_issues)} issues')
    for issue in info_issues[:20]:
        print(f'    {issue}')
    if len(info_issues) > 20:
        print(f'    ... and {len(info_issues) - 20} more')

    print('\n=== DLBR VALIDATION ===')
    dlbr_issues = validate_dlbr_structure(dlbrs, dial_by_fid, qust_by_fid)
    print(f'  {len(dlbrs)} DLBRs checked, {len(dlbr_issues)} issues')
    for issue in dlbr_issues[:20]:
        print(f'    {issue}')
    if len(dlbr_issues) > 20:
        print(f'    ... and {len(dlbr_issues) - 20} more')

    # Voice type pattern analysis
    print('\n=== VOICE TYPE INJECTION PATTERNS ===')
    vtyp_patterns = analyze_voice_type_patterns(infos, dials)
    print(f'  {"Category":<25} {"Total":>6} {"w/ VTYP":>8} {"no CTDA":>8} {"VTYP%":>6}')
    print(f'  {"-"*25} {"-"*6} {"-"*8} {"-"*8} {"-"*6}')
    for key in sorted(vtyp_patterns.keys()):
        s = vtyp_patterns[key]
        pct = s['with_vtyp'] / s['total'] * 100 if s['total'] else 0
        print(f'  {key:<25} {s["total"]:>6} {s["with_vtyp"]:>8} {s["no_ctda"]:>8} {pct:>5.1f}%')

    # CTDA function distribution
    print('\n=== CTDA FUNCTION DISTRIBUTION ===')
    func_counts = defaultdict(int)
    for rec in infos:
        for ctda in _get_ctdas(rec):
            func_counts[ctda['func_idx']] += 1
    for idx, count in sorted(func_counts.items(), key=lambda x: -x[1])[:20]:
        print(f'  {_func_name(idx):<30} ({idx:>3}): {count}')

    # VTYP cross-reference check
    print('\n=== VTYP CROSS-REFERENCE ===')
    vtyp_in_ctda = set()
    for rec in infos:
        for ctda in _get_ctdas(rec):
            if ctda['func_idx'] == 426:
                vtyp_in_ctda.add(ctda['param1'])
    print(f'  VTYP records defined: {len(vtyp_fids)}')
    print(f'  VTYP FormIDs in CTDAs: {len(vtyp_in_ctda)}')
    in_ctda_not_record = vtyp_in_ctda - vtyp_fids
    if in_ctda_not_record:
        print(f'  WARNING: {len(in_ctda_not_record)} VTYP FormIDs in CTDAs but no VTYP record:')
        for fid in sorted(in_ctda_not_record)[:10]:
            print(f'    {fid:08X}')

    if args.check_npc:
        print('\n=== NPC VTCK CHECK ===')
        npc_info = analyze_npc_vtck(args.esm)
        print(f'  Total NPCs: {npc_info["total"]}')
        print(f'  With VTCK:  {npc_info["with_vtck"]}')
        print(f'  Missing:    {npc_info["missing_vtck"]}')
        print(f'  Unique VTYP refs: {len(npc_info["unique_vtyp_fids"])}')
        vtck_not_vtyp = npc_info['unique_vtyp_fids'] - vtyp_fids
        if vtck_not_vtyp:
            print(f'  WARNING: {len(vtck_not_vtyp)} NPC VTCK refs to non-existent VTYP records:')
            for fid in sorted(vtck_not_vtyp)[:10]:
                print(f'    {fid:08X}')
        vtck_not_ctda = npc_info['unique_vtyp_fids'] - vtyp_in_ctda
        if vtck_not_ctda:
            print(f'  NOTE: {len(vtck_not_ctda)} NPC VTYP refs not used in any CTDA (may be OK for non-speaking NPCs)')

    total_issues = len(qust_issues) + len(dial_issues) + len(info_issues) + len(dlbr_issues)
    print(f'\n=== TOTAL: {total_issues} structural issues found ===')

    return 0 if total_issues == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

"""Trace which conversation topics a greeting/bark choice promotes to a
top-level Skyrim menu topic.

A bark INFO (HELLO, GREETING, combat/detection bark) can point at a
conversation topic via its Choice[] / TCLT.Choice list. The importer promotes
such a target to a TOP-LEVEL branch ONLY when every revealing bark carries a
real timing gate (GetStage etc.); a target revealed by any *ungated* bark
(the generic always-available HELLO) stays a Normal branch, so generic
emotional-response topics (AnswerNegative/AnswerPositive/FollowupNegative/
SadGeneral, ...) don't sit permanently in the NPC menu.

This tool reproduces that decision from the raw export and prints, per target:
  PROMOTE / normal, the target EditorID, and the revealing bark EditorIDs
(with 'ungated' marks) — so a regression in the promotion rule is visible
without a full import.

Usage:
    python -m tools.trace_bark_choice_promotion export/Oblivion.esm
    python -m tools.trace_bark_choice_promotion export/Oblivion.esm --only AnswerNegative,SadGeneral
    python -m tools.trace_bark_choice_promotion export/Oblivion.esm --promoted-only
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

from tes5_import.text_reader import (
    parse_export_directory, group_records_by_type,
    get_formid, get_int, get_str,
)
from tes5_import.dialog_converter import (
    classify_topic, should_skip_dial, service_menu_kind, _quest_state_ctdas,
)


def _bark_dial_fids(dials):
    out = set()
    for d in dials:
        if should_skip_dial(d) or service_menu_kind(d):
            continue
        _c, _s, _snam, is_bark = classify_topic(
            get_str(d, 'EditorID', ''), get_int(d, 'DATA.Type'))
        if is_bark:
            out.add(get_formid(d, 'FormID'))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('export_dir', help='export/<file> directory')
    ap.add_argument('--only', default='',
                    help='comma-separated target EditorIDs to restrict output')
    ap.add_argument('--promoted-only', action='store_true',
                    help='show only targets that get promoted to top-level')
    args = ap.parse_args(argv)

    records = parse_export_directory(str(Path(args.export_dir)),
                                     type_filter={'DIAL', 'INFO'})
    by_type = group_records_by_type(records)
    dials = by_type.get('DIAL', [])
    infos = by_type.get('INFO', [])

    edid_by_fid = {get_formid(d, 'FormID'): get_str(d, 'EditorID', '')
                   for d in dials}
    bark_dial_fids = _bark_dial_fids(dials)

    # target_fid -> list of (revealer_bark_fid, gate_is_nonempty)
    revealers = defaultdict(list)
    for info_rec in infos:
        parent = get_formid(info_rec, 'ParentDIAL')
        if parent not in bark_dial_fids:
            continue
        targets = []
        for i in range(get_int(info_rec, 'ChoiceCount')):
            cfid = get_formid(info_rec, f'Choice[{i}]')
            if cfid and cfid not in bark_dial_fids:
                targets.append(cfid)
        cfid = get_formid(info_rec, 'TCLT.Choice')
        if cfid and cfid not in bark_dial_fids:
            targets.append(cfid)
        if not targets:
            continue
        # offset=0: raw FormIDs, enough for gate-nonempty detection.
        gate = _quest_state_ctdas(info_rec, 0)
        for cfid in targets:
            revealers[cfid].append((parent, len(gate) > 0))

    only = {s.strip() for s in args.only.split(',') if s.strip()}
    n_promote = 0
    for tgt_fid in sorted(revealers,
                          key=lambda f: edid_by_fid.get(f, '') or f'{f:08X}'):
        gates = [g for _, g in revealers[tgt_fid]]
        promote = bool(gates) and all(gates)
        if promote:
            n_promote += 1
        if args.promoted_only and not promote:
            continue
        tgt_edid = edid_by_fid.get(tgt_fid, f'{tgt_fid:08X}')
        if only and tgt_edid not in only:
            continue
        rev_desc = ', '.join(
            f"{edid_by_fid.get(r, f'{r:08X}')}"
            f"{'' if g else ' (ungated)'}"
            for r, g in revealers[tgt_fid])
        tag = 'PROMOTE' if promote else 'normal '
        print(f"{tag} {tgt_edid:<28} <- {rev_desc}")

    print(f"\n{len(revealers)} bark-choice targets, {n_promote} promoted "
          f"to top-level.", file=sys.stderr)


if __name__ == '__main__':
    main()

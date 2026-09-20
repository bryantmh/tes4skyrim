#!/usr/bin/env python3
"""The fewest TES3 quests that exercise every MWScript opcode the runtime has.

Play-testing MorrowindRuntime one command at a time is impossible -- 326
ported commands, 90k call sites. But a quest is a unit the user can actually
run, and each one exercises a SET of commands: everything its INFO result
scripts and its actors' object scripts call. So "test every opcode" is a
set-cover over quests, and a short cover is a test plan.

    python -m tools.dialog.morrowind_opcode_testplan --plugin TR_Mainland.esm
    python -m tools.dialog.morrowind_opcode_testplan --plugin TR_Mainland.esm \\
        --stubs --markdown docs/audits/morrowind_opcode_testplan.md

See: docs/commentary/morrowind_runtime.md#opcode-test-plan
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from asset_convert.sources import source_registry
from tools.dialog import morrowind_quest_trace as trace
from tools.dialog import mw_testplan_place as place
from tools.dialog import mw_testplan_report as report
from tools.script import mwscript_opcode_audit as opcode_audit


def command_status(root, export_dir):
    """`(ported, stubbed, calls)` -- the two name sets and each one's uses.

    Derived exactly as the audit derives them, so this plan and the audit can
    never disagree.
    See: docs/audits/mwscript_opcodes.md#ported
    """
    commands = opcode_audit.registrations(root)
    real, noops = opcode_audit.installed(root)
    opcode_audit.count_calls(export_dir, commands)
    ported, stubbed = set(), set()
    for cmd in commands.values():
        status = opcode_audit.status_of(cmd, real, noops)
        if status in ('ported', 'CONFLICT'):
            ported.add(cmd.key)
        elif status == 'STUB':
            stubbed.add(cmd.key)
    return ported, stubbed, {c.key: c.calls for c in commands.values()}


def owned_quests(record_dir):
    """The journal ids THIS plugin authors, from its own `MWDI.txt`.

    🛑 The sidecar is CUMULATIVE -- it merges the masters' dialogue, so a
    TR_Mainland sidecar carries vanilla Morrowind's 760 quests too, and a
    plan built from it sends the user to Caius Cosades. The export's own
    MWDI is the authored statement of what this plugin owns; a `tr_` name
    prefix is a guess that also drops TR's own `hh_`/`pc_m0_` quests.
    See: docs/commentary/morrowind_runtime.md#opcode-test-plan
    """
    path = os.path.join(record_dir, 'MWDI.txt')
    if not os.path.isfile(path):
        return None
    return {rec['EditorID'].lower() for rec in trace.records(path)
            if rec.get('DialType') == 'Journal' and rec.get('EditorID')}


def quest_commands(entries, setters, owned):
    """`{quest: row}` for every quest with stages that `owned` admits.

    A row's `commands` is every command reachable from the quest -- the union
    over its stage setters, dialogue and object script alike, since running
    the quest runs both.
    """
    out = {}
    for quest in sorted(set(entries) | set(setters)):
        if owned is not None and quest not in owned:
            continue
        found = trace.stage_rows(quest, entries, setters)
        if not found:
            continue
        name, worst, rows = found
        commands, givers, scripts = set(), {}, {}
        for index, _mark, _why, sets in rows:
            for setter in sets:
                commands |= setter.get('used', set())
                if not setter['actor']:
                    continue
                where = givers if setter['source'] == 'dialogue' else scripts
                where.setdefault(setter['actor'], index)
        out[quest] = {
            'name': name, 'commands': commands, 'worst': worst,
            'stages': sorted(r[0] for r in rows), 'givers': givers,
            'scripts': scripts,
            'first': min((r[0] for r in rows), default=0)}
    return out


def prerequisites(quest, plan, setters):
    """Journal ids this quest gates on: another quest read by its scripts.

    Excludes the quest itself, by id AND by display name -- neither is a
    prerequisite, and both made quests list themselves.
    See: docs/commentary/morrowind_runtime.md#opcode-test-plan
    """
    mine = plan[quest]['name']
    found = set()
    for setter in setters.get(quest, []):
        for other in setter.get('reads', ()):
            if other != quest and other in plan \
                    and plan[other]['name'] != mine:
                found.add(other)
    return found


def pick(plan, targets, budget):
    """Greedy cover: repeatedly take the best new-commands-per-run quest.

    Ties break toward fewer prerequisites, then the earlier first stage (an
    opening quest needs less of the game finished), then the id, so the plan
    is deterministic.
    """
    remaining = set(targets)
    chosen, ran = [], set()
    while remaining and len(chosen) < budget:
        best, best_score = None, None
        for quest, row in plan.items():
            if quest in ran:
                continue
            chain = [q for q in row['prereqs'] if q not in ran]
            gain = set(row['commands'])
            for other in chain:
                gain |= plan[other]['commands']
            gain &= remaining
            if not gain:
                continue
            score = (-len(gain) / (len(chain) + 1), len(chain),
                     row['first'], quest)
            if best_score is None or score < best_score:
                best, best_score = quest, score
        if best is None:
            break
        chain = [q for q in plan[best]['prereqs'] if q not in ran]
        for other in chain + [best]:
            ran.add(other)
            remaining -= plan[other]['commands']
        chosen.append((best, chain))
    return chosen, remaining


def build_plan(args):
    """Read every source and return `(plan, ported, stubbed, places, calls)`."""
    root = os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    out_dir = trace.sidecar_dir(args.plugin, args.export_root,
                                args.output_root)
    info_path = os.path.join(out_dir, 'INFO.txt')
    if not os.path.isfile(info_path):
        raise SystemExit('no staged sidecar at %s -- run the import or '
                         'tools.dialog.mw_sidecar first' % out_dir)
    record_dir = str(source_registry.record_dir(args.export_root,
                                                args.plugin))
    ported, stubbed, calls = command_status(root, record_dir)

    entries, setters = trace.scan_dialogue(info_path)
    for quest, rows in trace.scan_object_scripts(
            os.path.join(record_dir, 'SCPT.txt')).items():
        setters.setdefault(quest, []).extend(rows)
    report.attach_reads(info_path, record_dir, setters, ported | stubbed)

    plan = quest_commands(entries, setters, owned_quests(record_dir))
    for quest, row in plan.items():
        row['prereqs'] = sorted(prerequisites(quest, plan, setters))
    places = place.locate(record_dir, plan, args.export_root)
    return plan, ported, stubbed, places, calls


def main():
    """CLI: print the cover, and optionally write it as markdown."""
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--budget', type=int, default=40,
                    help='most quests to pick (default 40)')
    ap.add_argument('--stubs', action='store_true',
                    help='also cover the called-but-stubbed commands')
    ap.add_argument('--marginal', action='store_true',
                    help='print what each extra quest buys')
    ap.add_argument('--markdown', help='write the plan here')
    ap.add_argument('--export-root', default='export')
    ap.add_argument('--output-root', default='output')
    args = ap.parse_args()

    plan, ported, stubbed, places, calls = build_plan(args)
    print('%d quest(s) with stages; %d ported and %d stubbed command(s) '
          'registered' % (len(plan), len(ported), len(stubbed)))
    chosen, missed = pick(plan, ported, args.budget)
    report.show(plan, places, chosen, missed, 'ported', ported)
    if args.marginal:
        report.show_marginal(plan, chosen, ported)
    called = {c for c in stubbed if calls.get(c)}
    stub_pick = pick(plan, called, args.budget) if args.stubs else None
    if stub_pick:
        report.show(plan, places, stub_pick[0], stub_pick[1], 'stubbed',
                    called)
    if args.markdown:
        report.write_markdown(args.markdown, args.plugin, plan, places,
                              (chosen, missed), stub_pick, ported, called,
                              calls)
    return 0


if __name__ == '__main__':
    sys.exit(main())

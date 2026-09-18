#!/usr/bin/env python3
"""Can a Morrowind quest be finished with what the runtime implements?

A TES3 journal quest advances by `Journal <id> <index>`, which is reached two
ways: from an INFO's result script, which the MorrowindRuntime interpreter
runs, or from an object script, which still goes down the lossy Papyrus path.
A stage only object scripts set is UNREACHABLE today, and a stage whose script
needs an unported command runs but does not do what it says.

So for each stage this reports who sets it, what its script needs, and the
verdict -- which is the question "is this quest completable?" asked of real
data instead of by hand.

    python -m tools.dialog.morrowind_quest_trace --plugin TR_Mainland.esm \\
        --quest TR_m3_FG_OE_Cursing

    # every quest an actor gives, with only the broken ones named
    python -m tools.dialog.morrowind_quest_trace --plugin TR_Mainland.esm \\
        --actor "TR_m3_Sharnoga gra-Mal" --problems

    # the whole plugin, as a ranked summary
    python -m tools.dialog.morrowind_quest_trace --plugin TR_Mainland.esm --all

See: docs/commentary/morrowind_runtime.md#quest-trace
"""

import argparse
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from asset_convert.sources import source_registry
from tes5_import.dialogue.morrowind_sidecar import SIDECAR_DIR, plugin_stem

#: `Journal <id> <index>`, the one statement that advances a quest.
JOURNAL = re.compile(r'\bjournal\b[, \t]+"?([\w\-]+)"?[, \t]+(\d+)',
                     re.IGNORECASE)

#: A leading command word, past any `ref->` or `"ref"->` explicit target.
COMMAND = re.compile(
    r'^[ \t]*(?:"[^"\n]*"|[A-Za-z_][\w]*)?[ \t]*->[ \t]*([A-Za-z_][\w]*)'
    r'|^[ \t]*([A-Za-z_][\w]*)', re.MULTILINE)

#: Control flow and declarations, which are not commands to port.
NOT_A_COMMAND = frozenset((
    'begin', 'end', 'if', 'elseif', 'else', 'endif', 'while', 'endwhile',
    'short', 'long', 'float', 'set', 'to', 'return', 'player'))

#: Commands with a real opcode, plus the five no-ops that are deliberate.
IMPLEMENTED = frozenset((
    'journal', 'setjournalindex', 'getjournalindex', 'addtopic', 'goodbye',
    'choice', 'messagebox', 'moddisposition', 'setdisposition',
    'getdisposition', 'getreputation', 'setreputation', 'modreputation',
    'getpcfacrep', 'setpcfacrep', 'modpcfacrep', 'pcjoinfaction',
    'pcraiserank', 'pclowerrank', 'getpcrank', 'pcexpelled', 'pcexpell',
    'pcclearexpelled', 'samefaction', 'getfactionreaction',
    'setfactionreaction', 'modfactionreaction', 'getpccrimelevel',
    'setpccrimelevel', 'modpccrimelevel', 'additem', 'removeitem',
    'getitemcount', 'startscript', 'stopscript', 'scriptrunning',
    'getdeadcount', 'getfight', 'setfight', 'modfight', 'gethello',
    'sethello', 'modhello', 'getalarm', 'setalarm', 'modalarm', 'getflee',
    'setflee', 'modflee',
    'showmap', 'fadein', 'fadeout', 'fadeto', 'clearinfoactor'))

#: Worst-first, so a quest reports the weakest link in its chain.
SEVERITY = {'OK': 0, 'DEGRADED': 1, 'BLOCKED': 2, 'UNREACHABLE': 3}


def sidecar_dir(plugin, export_root='export', output_root='output'):
    """Where `plugin`'s staged sidecar tables live."""
    group = source_registry.asset_root_name(export_root, plugin)
    return os.path.join(output_root, group, SIDECAR_DIR, plugin_stem(plugin))


def records(path):
    """Yield each sidecar or export record as a dict of KEY=VALUE."""
    current = {}
    handle = io.open(path, encoding='utf-8', errors='replace')
    for line in handle:
        line = line.rstrip('\n')
        if line == '---RECORD_BEGIN---':
            current = {}
        elif line == '---RECORD_END---':
            yield current
        elif '=' in line:
            key, value = line.split('=', 1)
            current[key] = value
    handle.close()


def unescape(text):
    """Escaped newlines AND tabs back into real characters."""
    return (text.replace('\\r\\n', '\n').replace('\\n', '\n')
                .replace('\\t', '\t').replace('\\r', '\n'))


def commands_of(script):
    """The distinct command words a script body uses, in order."""
    found = []
    for match in COMMAND.finditer(unescape(script)):
        name = (match.group(1) or match.group(2)).lower()
        if name not in NOT_A_COMMAND and name not in found:
            found.append(name)
    return found


def scan_dialogue(info_path):
    """`(entries, setters)` for every journal quest in the sidecar.

    `entries` is `{quest: {index: (status, text)}}` -- the pages the journal
    itself authors. `setters` is `{quest: [stage dict]}` for every INFO whose
    result script advances one.
    """
    entries, setters = {}, {}
    for rec in records(info_path):
        topic = rec.get('Topic', '')
        if rec.get('InfoType') == 'Journal':
            index = int(rec.get('JournalIndex', '0'))
            entries.setdefault(topic.lower(), {})[index] = (
                rec.get('QuestStatus', ''), rec.get('Response', ''))
            continue
        script = rec.get('ResultScript', '')
        if not script:
            continue
        for quest, index in JOURNAL.findall(unescape(script)):
            setters.setdefault(quest.lower(), []).append({
                'index': int(index), 'topic': topic,
                'actor': rec.get('Actor', ''), 'source': 'dialogue',
                'commands': commands_of(script)})
    return entries, setters


def scan_object_scripts(scpt_path):
    """`{quest: [stage dict]}` for every OBJECT script that sets a stage."""
    setters = {}
    if not os.path.isfile(scpt_path):
        return setters
    for rec in records(scpt_path):
        body = rec.get('SCTX', '')
        if not body:
            continue
        for quest, index in JOURNAL.findall(unescape(body)):
            setters.setdefault(quest.lower(), []).append({
                'index': int(index), 'topic': '',
                'actor': rec.get('EditorID', ''), 'source': 'object script',
                'commands': commands_of(body)})
    return setters


def verdict(stage_setters):
    """`(mark, why)` for one stage, given everything that can set it."""
    if not stage_setters:
        return 'UNREACHABLE', 'nothing sets this stage'
    spoken = [s for s in stage_setters if s['source'] == 'dialogue']
    if not spoken:
        return 'BLOCKED', 'only an object script sets it (Papyrus path)'
    missing = set()
    for setter in spoken:
        missing |= {c for c in setter['commands'] if c not in IMPLEMENTED}
    if missing:
        return 'DEGRADED', 'runs, but ' + ', '.join(
            sorted(missing)) + ' do nothing'
    return 'OK', 'set from dialogue'


def stage_rows(quest, entries, setters):
    """`(name, worst, rows)` for one quest, or None when it has no stages.

    Index 0 is the quest's NAME page, not a step, so it is skipped unless
    something really sets it -- counting it as unreachable marks every quest
    in the game broken.
    """
    pages = entries.get(quest, {})
    by_index = {}
    for setter in setters.get(quest, []):
        by_index.setdefault(setter['index'], []).append(setter)
    indices = sorted(set(pages) | set(by_index))
    if not indices:
        return None
    rows, worst = [], 'OK'
    for index in indices:
        if index == 0 and not by_index.get(0):
            continue
        mark, why = verdict(by_index.get(index, []))
        if SEVERITY[mark] > SEVERITY[worst]:
            worst = mark
        rows.append((index, mark, why, by_index.get(index, [])))
    name = (pages.get(0, ('', ''))[1] or quest).strip() or quest
    return name, worst, rows


def report_quest(quest, entries, setters, problems_only):
    """Print one quest's stages; returns its worst verdict."""
    found = stage_rows(quest, entries, setters)
    if not found:
        return None
    name, worst, rows = found
    if problems_only and worst == 'OK':
        return worst
    print('=== %s  [%s]' % (name, worst))
    for index, mark, why, sets in rows:
        status = entries.get(quest, {}).get(index, ('', ''))[0]
        print('  %4d %-11s %s%s' % (index, mark, why,
                                    (' ' + status) if status else ''))
        for setter in sets:
            print('         via %-13s %s' % (setter['source'],
                                             setter['topic']
                                             or setter['actor']))
    print()
    return worst


def tally_stage(mark, sets, scripts, missing):
    """Fold one stage's blame into the script and command counters."""
    for setter in sets:
        if mark == 'BLOCKED':
            scripts[setter['actor']] = scripts.get(setter['actor'], 0) + 1
            continue
        if mark != 'DEGRADED' or setter['source'] != 'dialogue':
            continue
        for name in setter['commands']:
            if name not in IMPLEMENTED:
                missing[name] = missing.get(name, 0) + 1


def report_blockers(quests, entries, setters):
    """Rank what actually stops stages, across every quest asked for."""
    scripts, missing, stages = {}, {}, {}
    for quest in quests:
        found = stage_rows(quest, entries, setters)
        if not found:
            continue
        for _index, mark, _why, sets in found[2]:
            stages[mark] = stages.get(mark, 0) + 1
            tally_stage(mark, sets, scripts, missing)
    print('stages: ' + ', '.join('%s %d' % (k, stages[k])
                                 for k in sorted(stages)))
    print()
    print('object scripts blocking the most stages')
    for name, count in sorted(scripts.items(), key=lambda kv: -kv[1])[:15]:
        print('  %5d  %s' % (count, name))
    print()
    print('unported commands degrading the most stages')
    for name, count in sorted(missing.items(), key=lambda kv: -kv[1])[:20]:
        print('  %5d  %s' % (count, name))


def chosen_quests(args, parser, entries, setters):
    """Which quests to report, from --quest / --actor / --all."""
    if args.quest:
        return [args.quest.lower()]
    if args.actor:
        actor = args.actor.lower()
        found = sorted({q for q, rows in setters.items()
                        if any(s['actor'].lower() == actor for s in rows)})
        print('%s advances %d quest(s)\n' % (args.actor, len(found)))
        return found
    if args.all:
        return sorted(set(entries) | set(setters))
    parser.error('pass --quest, --actor or --all')
    return []


def main():
    """CLI: trace one quest, one actor's quests, or the whole plugin."""
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--quest', help='one journal id')
    ap.add_argument('--actor', help='every quest this actor advances')
    ap.add_argument('--all', action='store_true', help='every quest')
    ap.add_argument('--problems', action='store_true',
                    help='name only quests that are not fully OK')
    ap.add_argument('--blockers', action='store_true',
                    help='rank what blocks stages instead of listing quests')
    ap.add_argument('--export-root', default='export')
    ap.add_argument('--output-root', default='output')
    args = ap.parse_args()

    out_dir = sidecar_dir(args.plugin, args.export_root, args.output_root)
    info_path = os.path.join(out_dir, 'INFO.txt')
    if not os.path.isfile(info_path):
        print('no staged sidecar at %s -- run the import first' % out_dir)
        return 1
    entries, setters = scan_dialogue(info_path)
    record_dir = str(source_registry.record_dir(args.export_root, args.plugin))
    for quest, rows in scan_object_scripts(
            os.path.join(record_dir, 'SCPT.txt')).items():
        setters.setdefault(quest, []).extend(rows)

    wanted = chosen_quests(args, ap, entries, setters)
    if args.blockers:
        report_blockers(wanted, entries, setters)
        return 0
    tally = {}
    for quest in wanted:
        worst = report_quest(quest, entries, setters, args.problems)
        if worst:
            tally[worst] = tally.get(worst, 0) + 1
    print('%d quest(s): %s' % (
        sum(tally.values()),
        ', '.join('%s %d' % (k, tally[k]) for k in sorted(tally))))
    return 0


if __name__ == '__main__':
    sys.exit(main())

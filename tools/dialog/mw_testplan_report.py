#!/usr/bin/env python3
"""Printing the opcode test plan, and reading the prerequisites it ranks by.

A quest's prerequisite is authored, not named: a script that gates on another
questline reads its journal index. So the chain comes from
`GetJournalIndex "<other>"` in the same scripts the trace already scanned,
which is why the read is attached to the setter rows rather than re-parsed.

See: docs/commentary/morrowind_runtime.md#opcode-test-plan
"""

import io
import os
import re

from tools.dialog.morrowind_quest_trace import records, unescape

#: `GetJournalIndex "<id>"` -- the one statement that reads another quest.
JOURNAL_READ = re.compile(r'\bgetjournalindex\b[, \t]+"?([\w\-]+)"?',
                          re.IGNORECASE)

#: A bare word, and a quoted literal, which is prose and never a call.
WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
STRING = re.compile(r'"[^"\n]*"')


def commands_used(script, wanted):
    """Every command in `wanted` a script body calls, by the audit's rule.

    🛑 EVERY word on a line, not the leading one. A query is almost always an
    argument -- `if ( GetHealth < 50 )`, `player->AddItem` -- so a
    leading-word scan saw 141 of the 326 ported commands and reported the
    other 185 as untestable.
    See: docs/commentary/morrowind_runtime.md#opcode-audit-strings
    """
    found = set()
    for line in unescape(script).splitlines():
        line = STRING.sub(' ', line.split(';', 1)[0])
        found.update(w.lower() for w in WORD.findall(line)
                     if w.lower() in wanted)
    return found


def _reads(script):
    """The lowercased journal ids a script body reads."""
    return {q.lower() for q in JOURNAL_READ.findall(unescape(script))}


def _attach(setters, quest_of, script, actor, wanted):
    """Record one script's journal reads and commands on its setter rows."""
    found = _reads(script)
    used = commands_used(script, wanted)
    for quest in quest_of:
        for setter in setters.get(quest, []):
            if setter['actor'] != actor:
                continue
            setter.setdefault('reads', set()).update(found)
            setter.setdefault('used', set()).update(used)


def attach_reads(info_path, record_dir, setters, wanted):
    """Give every setter row the journal ids and commands its script uses.

    Walks both corpora once and matches on the actor the trace already
    recorded, so a row's `reads` and `used` are exactly what that script
    consults.
    """
    by_actor = {}
    for quest, rows in setters.items():
        for setter in rows:
            by_actor.setdefault(setter['actor'], set()).add(quest)
    for rec in records(info_path):
        script = rec.get('ResultScript', '')
        actor = rec.get('Actor', '')
        if script and actor in by_actor:
            _attach(setters, by_actor[actor], script, actor, wanted)
    scpt = os.path.join(record_dir, 'SCPT.txt')
    if not os.path.isfile(scpt):
        return
    for rec in records(scpt):
        body, edid = rec.get('SCTX', ''), rec.get('EditorID', '')
        if body and edid in by_actor:
            _attach(setters, by_actor[edid], body, edid, wanted)


def _row(plan, places, quest):
    """`(name, giver, target, stages)` as the tables print them."""
    row = plan[quest]
    giver, target, _kind = places.get(quest, ('', '', ''))
    return (row['name'], giver or '?', target or '?',
            '%d' % len(row['stages']))


def show(plan, places, chosen, missed, label, targets):
    """Print one cover as plain text.

    Counts only the TARGET commands: a quest covers plenty of others too, and
    tallying those reported 163 of 45 stubs covered.
    """
    covered = set()
    for quest, chain in chosen:
        for other in chain + [quest]:
            covered |= plan[other]['commands'] & targets
    print('\n=== %s cover: %d quest(s), %d prerequisite run(s), '
          '%d of %d command(s) covered, %d uncovered' % (
              label, len(chosen), sum(len(c) for _q, c in chosen),
              len(covered), len(targets), len(missed)))
    gained = new_commands(plan, chosen, targets)
    for quest, chain in chosen:
        name, giver, target, stages = _row(plan, places, quest)
        print('  %-40s %-24s %-26s %s stages' % (
            name[:40], giver[:24], target[:26], stages))
        for other in chain:
            print('      prereq: %s' % plan[other]['name'])
        print('      tests: %s' % ', '.join(gained[quest]))
    if missed:
        print('  uncovered: ' + ', '.join(sorted(missed)[:25]))


def new_commands(plan, chosen, targets):
    """`{quest: sorted commands}` -- what each quest is the FIRST to cover.

    Not everything a quest touches: by the tail of the cover most of that is
    already tested, and listing it all would say `journal, choice, additem`
    against every row. This is the reason the quest is in the plan, and a
    prerequisite's own new commands are credited to the quest that pulled it
    in, since the user runs them together.
    """
    seen, out = set(), {}
    for quest, chain in chosen:
        gained = set()
        for other in chain + [quest]:
            gained |= (plan[other]['commands'] & targets) - seen
            seen |= plan[other]['commands'] & targets
        out[quest] = sorted(gained)
    return out


def marginal(plan, chosen, targets):
    """`[(quests, runs, covered, new, prereqs, name)]` down the cover.

    What each additional quest actually buys, which is how a cutoff is chosen
    rather than guessed: the tail of a greedy cover pays many prerequisite
    runs for one or two commands.
    """
    seen, runs, out = set(), 0, []
    for number, (quest, chain) in enumerate(chosen, 1):
        before = len(seen)
        for other in chain + [quest]:
            seen |= plan[other]['commands'] & targets
        runs += len(chain) + 1
        out.append((number, runs, len(seen), len(seen) - before,
                    len(chain), plan[quest]['name']))
    return out


def show_marginal(plan, chosen, targets):
    """Print the marginal-value curve of one cover."""
    print('\n  quests runs  covered  new  prereq  quest')
    for row in marginal(plan, chosen, targets):
        print('  %6d %4d  %7d  %3d  %6d  %s' % row)


def _table(fh, plan, places, chosen, targets):
    """Write one cover as a markdown table."""
    gained = new_commands(plan, chosen, targets)
    fh.write('| # | Quest | Quest giver | `coc` target / location | '
             'Stages | Prerequisite | Commands it is first to test |\n')
    fh.write('|--:|---|---|---|--:|---|---|\n')
    for number, (quest, chain) in enumerate(chosen, 1):
        name, giver, target, stages = _row(plan, places, quest)
        prereq = ', '.join(plan[q]['name'] for q in chain) or '—'
        tested = ', '.join('`%s`' % c for c in gained[quest]) or '—'
        fh.write('| %d | %s | %s | %s | %s | %s | %s |\n' % (
            number, name, giver, target, stages, prereq, tested))
    fh.write('\n')


def _uncovered(fh, missed, calls):
    """Split what no quest reaches into 'never called' and 'called elsewhere'.

    The split is the whole point: a command with no call site in the plugin
    is untestable HERE and needs another plugin, while one that is called but
    unreachable sits on ambient machinery and needs a hand-written probe.
    """
    never = sorted(c for c in missed if not calls.get(c))
    elsewhere = sorted(((calls.get(c, 0), c) for c in missed
                        if calls.get(c)), reverse=True)
    if elsewhere:
        fh.write('**Called by TR, but never from a quest** — these sit on '
                 'ambient object scripts (doors, cranks, lights), so they '
                 'need a hand-written probe rather than a quest:\n\n')
        fh.write(', '.join('`%s` (%d)' % (c, n) for n, c in elsewhere))
        fh.write('\n\n')
    if never:
        fh.write('**No call site anywhere in this plugin** (%d) — untestable '
                 'from TR at all; they need a different plugin:\n\n' %
                 len(never))
        fh.write(', '.join('`%s`' % c for c in never) + '\n\n')


def write_markdown(path, plugin, plan, places, ported_pick, stub_pick,
                   ported, stubbed, calls):
    """Write the whole plan, both covers, as a markdown document."""
    chosen, missed = ported_pick
    with io.open(path, 'w', encoding='utf-8') as fh:
        fh.write('# MorrowindRuntime opcode test plan — %s\n\n' % plugin)
        fh.write('**Tool:** `python -m tools.dialog.'
                 'morrowind_opcode_testplan --plugin %s --stubs '
                 '--markdown <this file>`\n\n' % plugin)
        fh.write('Measured over %d journal quest(s) with stages. The cover '
                 'is greedy on new-commands-per-run, prerequisites '
                 'included.\n\n' % len(plan))
        fh.write('## Ported commands (%d of %d covered)\n\n' % (
            len(ported) - len(missed), len(ported)))
        _table(fh, plan, places, chosen, ported)
        if missed:
            _uncovered(fh, missed, calls)
        if stub_pick:
            picks, left = stub_pick
            fh.write('## Stubbed commands — registered but doing nothing '
                     '(%d of %d covered)\n\n' % (
                         len(stubbed) - len(left), len(stubbed)))
            fh.write('Running these routes the player through commands that '
                     'compile and dispatch but do nothing, so the log names '
                     'the silent no-op behind each broken stage.\n\n')
            _table(fh, plan, places, picks, stubbed)
            _uncovered(fh, left, calls)
    print('\nwrote %s' % path)

#!/usr/bin/env python3
"""Printing the opcode test plan, and reading the prerequisites it ranks by.

A quest's prerequisite is authored in its ENTRY INFO's conditions: a `J`
condition naming another quest's journal index. A result script's
`GetJournalIndex` says the same thing in script form, so both are read, and
so is the topic the entry line sits on -- an unreachable topic gates a quest
as firmly as an unmet journal index.

See: docs/commentary/morrowind_runtime.md#prerequisites-are-conditions
"""

import io
import os
import re

from tools.dialog.morrowind_quest_trace import JOURNAL, records, unescape

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
    """Record one script's journal reads and commands on its setter rows.

    🛑 Journal reads go ONLY to the quests this script actually advances,
    while commands may go to every quest the actor touches. Spreading reads
    the same way gave one Mages Guild quest 30 prerequisites from unrelated
    questlines, because its giver also hands out bounty work.
    See: docs/commentary/morrowind_runtime.md#prerequisites-are-conditions
    """
    found = _reads(script)
    used = commands_used(script, wanted)
    sets = {q.lower() for q, _index in JOURNAL.findall(unescape(script))}
    for quest in quest_of:
        for setter in setters.get(quest, []):
            if setter['actor'] != actor:
                continue
            setter.setdefault('used', set()).update(used)
            if quest in sets:
                setter.setdefault('reads', set()).update(found)


def attach_reads(info_path, record_dirs, setters, wanted):
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
    for record_dir in record_dirs:
        scpt = os.path.join(record_dir, 'SCPT.txt')
        if not os.path.isfile(scpt):
            continue
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
    rows = play_order(chosen)
    covered = set()
    stages = 0
    for quest, _gating in rows:
        covered |= plan[quest]['commands'] & targets
        stages += len(plan[quest]['stages'])
    print('\n=== %s cover: %d quest(s) to play (%d of them prerequisites), '
          '%d stages, %d of %d command(s) covered, %d uncovered' % (
              label, len(rows), len(rows) - len(chosen), stages,
              len(covered), len(targets), len(missed)))
    gained = new_commands(plan, chosen, targets)
    for quest, gating in rows:
        name, giver, target, count = _row(plan, places, quest)
        mark = ' (unlocks %s)' % plan[gating]['name'][:24] if gating else ''
        print('  %-38s %-22s %-24s %s stages%s' % (
            name[:38], giver[:22], target[:24], count, mark))
        print('      tests: %s' % ', '.join(gained[quest]))
    if missed:
        print('  uncovered: ' + ', '.join(sorted(missed)[:25]))


def play_order(chosen):
    """`[(quest, gating_for)]` -- every quest to play, prerequisites included.

    A prerequisite earns its own row, because the tester plays it like any
    other quest; `gating_for` names the quest it was pulled in for, or "" when
    the quest was chosen on its own merit.
    """
    rows = []
    for quest, chain in chosen:
        rows.extend((other, quest) for other in chain)
        rows.append((quest, ''))
    return rows


def new_commands(plan, chosen, targets):
    """`{quest: sorted commands}` -- what each quest is the FIRST to cover.

    Credited to the quest that actually runs them, prerequisite rows
    included, so every command in the plan appears against exactly one row.
    """
    seen, out = set(), {}
    for quest, _gating in play_order(chosen):
        gained = (plan[quest]['commands'] & targets) - seen
        seen |= gained
        out[quest] = sorted(gained)
    return out


def marginal(plan, chosen, targets):
    """`[(picks, stages, covered, new, prereqs, name)]` down the cover.

    What each pick actually buys for the STAGES it costs, prerequisites
    included, which is how a cutoff is chosen rather than guessed.
    """
    seen, stages, out = set(), 0, []
    for number, (quest, chain) in enumerate(chosen, 1):
        before = len(seen)
        for other in chain + [quest]:
            seen |= plan[other]['commands'] & targets
            stages += len(plan[other]['stages'])
        out.append((number, stages, len(seen), len(seen) - before,
                    len(chain), plan[quest]['name']))
    return out


def show_marginal(plan, chosen, targets):
    """Print the marginal-value curve of one cover."""
    print('\n   picks stages  covered  new  prereq  quest')
    for row in marginal(plan, chosen, targets):
        print('  %6d %4d  %7d  %3d  %6d  %s' % row)


def _table(fh, plan, places, chosen, targets):
    """Write one cover as a markdown table, in the order it is PLAYED.

    A prerequisite is its own numbered row, marked with what it unlocks, so
    the stage count in the table is the real cost of the plan.
    """
    gained = new_commands(plan, chosen, targets)
    fh.write('| # | Quest | Quest giver | `coc` target / location | Stages | '
             'Why | Commands it is first to test |\n')
    fh.write('|--:|---|---|---|--:|---|---|\n')
    for number, (quest, gating) in enumerate(play_order(chosen), 1):
        name, giver, target, stages = _row(plan, places, quest)
        tested = ', '.join('`%s`' % c for c in gained[quest]) or '—'
        why = ('unlocks *%s*' % plan[gating]['name']) if gating else '—'
        fh.write('| %d | %s | %s | %s | %s | %s | %s |\n' % (
            number, name, giver, target, stages, why, tested))
    fh.write('\n')


def _uncovered(fh, missed, calls, where):
    """Split what no quest reaches into 'never called' and 'called elsewhere'.

    The split is the whole point: a command with no call site in the plugin
    is untestable HERE and needs another plugin, while one that is called but
    unreachable sits on ambient machinery and needs a hand-written probe.
    """
    never = sorted(c for c in missed if not calls.get(c))
    elsewhere = sorted(((calls.get(c, 0), c) for c in missed
                        if calls.get(c)), reverse=True)
    if elsewhere:
        fh.write('**Called by %s, but never from a quest** — these sit on '
                 'ambient object scripts (doors, cranks, lights), so they '
                 'need a hand-written probe rather than a quest:\n\n' % where)
        fh.write(', '.join('`%s` (%d)' % (c, n) for n, c in elsewhere))
        fh.write('\n\n')
    if never:
        fh.write('**No call site anywhere in %s** (%d) — untestable '
                 'from it at all; they need a different plugin:\n\n' %
                 (where, len(never)))
        fh.write(', '.join('`%s`' % c for c in never) + '\n\n')


def _folding_note(fh, folded):
    """Say how many commands a representative stands in for, and why."""
    if not folded:
        return
    reps = {}
    for name, rep in sorted(folded.items()):
        reps.setdefault(rep, []).append(name)
    fh.write('**%d further command(s) share a handler with one of these and '
             'are covered by testing it** -- `Enable` and `Disable` are one '
             '`OpSetEnabled`, every attribute and skill command one '
             '`OpStat`. The classes come from the runtime\'s own '
             'registrations, and `OpStat` is split by verb and by whether '
             'the stat maps to a Skyrim actor value, since those are '
             'genuinely different code paths.\n\n' % len(folded))
    fh.write('| Tested | Also covers |\n|---|---|\n')
    for rep in sorted(reps, key=lambda r: (-len(reps[r]), r)):
        fh.write('| `%s` | %s |\n' % (
            rep, ', '.join('`%s`' % c for c in reps[rep])))
    fh.write('\n')


def write_markdown(path, plugins, plan, places, ported_pick, stub_pick,
                   ported, stubbed, calls, folded=None):
    """Write the whole plan, both covers, as a markdown document."""
    chosen, missed = ported_pick
    where = ' + '.join(plugins)
    with io.open(path, 'w', encoding='utf-8') as fh:
        fh.write('# MorrowindRuntime opcode test plan — %s\n\n' % where)
        fh.write('**Tool:** `python -m tools.dialog.'
                 'morrowind_opcode_testplan --plugin %s --stubs '
                 '--markdown <this file>`\n\n' % ' '.join(plugins))
        fh.write('Measured over %d journal quest(s) with stages. Ranked by '
                 'new commands per STAGE the tester must play, so short '
                 'quests come first. **A prerequisite is its own row**, '
                 'marked in *Why*, and its stages and commands both count '
                 '-- the table is the whole play order, top to bottom.\n\n'
                 % len(plan))
        fh.write('## Ported commands (%d of %d covered)\n\n' % (
            len(ported) - len(missed), len(ported)))
        _table(fh, plan, places, chosen, ported)
        _folding_note(fh, folded)
        if missed:
            _uncovered(fh, missed, calls, where)
        if stub_pick:
            picks, left = stub_pick
            fh.write('## Stubbed commands — registered but doing nothing '
                     '(%d of %d covered)\n\n' % (
                         len(stubbed) - len(left), len(stubbed)))
            fh.write('Running these routes the player through commands that '
                     'compile and dispatch but do nothing, so the log names '
                     'the silent no-op behind each broken stage.\n\n')
            _table(fh, plan, places, picks, stubbed)
            _uncovered(fh, left, calls, where)
    print('\nwrote %s' % path)

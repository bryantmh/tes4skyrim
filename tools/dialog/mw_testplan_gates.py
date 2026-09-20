#!/usr/bin/env python3
"""What a TES3 quest must have happen before its first line can be reached.

Two authored gates, both on the INFO that sets the quest's EARLIEST stage:

  journal   a condition with `VarType == 'J'` naming another quest and the
            index it must have reached -- TES3's own "not until then"
  topic     the topic that INFO sits on. Morrowind teaches most topics by
            MENTIONING them in a response, so a quest whose entry line is on
            a topic only one other quest's text mentions is gated behind it
            just as firmly.

🛑 `GetJournalIndex` in a result script is the RARE form. Reading only it
reported Caught Off-Guard as free-standing when it needs The Company We Keep
finished first.

See: docs/commentary/morrowind_runtime.md#prerequisites-are-conditions
"""

import re

from tools.dialog.morrowind_quest_trace import JOURNAL, records, unescape

#: `GetJournalIndex "<id>"`: the same gate written as a script read.
JOURNAL_READ = re.compile(r'\bgetjournalindex\b[, \t]+"?([\w\-]+)"?',
                          re.IGNORECASE)

#: A condition whose variable is another quest's journal index.
JOURNAL_VARTYPE = 'J'

#: Topics every actor answers, so sitting on one gates nothing.
UNIVERSAL = frozenset(('greeting 0', 'greeting 1', 'greeting 2',
                       'greeting 3', 'greeting 4', 'greeting 5',
                       'greeting 6', 'greeting 7', 'greeting 8',
                       'greeting 9'))


def _conditions(rec):
    """Yield `(vartype, variable, value)` for each of an INFO's conditions."""
    for index in range(int(rec.get('ConditionCount', '0') or 0)):
        prefix = 'Condition[%d].' % index
        yield (rec.get(prefix + 'VarType'),
               (rec.get(prefix + 'Variable') or '').lower(),
               rec.get(prefix + 'Value'))


def _sets(rec):
    """`{quest: lowest index}` an INFO's result script advances."""
    out = {}
    for quest, index in JOURNAL.findall(unescape(
            rec.get('ResultScript', '') or '')):
        key, value = quest.lower(), int(index)
        out[key] = min(value, out.get(key, value))
    return out


def entries(info_path):
    """`{quest: {'topic', 'journal'}}` for the INFO opening each quest.

    The opener is the INFO setting the quest's LOWEST stage; its topic and
    its journal conditions are together what the player must satisfy to hear
    that line at all.
    """
    entry, best = {}, {}
    for rec in records(info_path):
        topic = (rec.get('Topic') or '').strip().lower()
        gates = {name: value for kind, name, value in _conditions(rec)
                 if kind == JOURNAL_VARTYPE and name}
        for quest, index in _sets(rec).items():
            if quest in best and best[quest] <= index:
                continue
            best[quest] = index
            entry[quest] = {'topic': topic,
                            'journal': {q: v for q, v in gates.items()
                                        if q != quest}}
    return entry


def _topic_finder(rows):
    """One whole-word alternation over every topic name, longest first.

    One combined regex, not one per topic: scanning each of 106,958 responses
    with 2,878 separate patterns did not finish in 300 seconds.
    """
    topics = {(rec.get('Topic') or '').strip().lower() for rec in rows}
    topics -= UNIVERSAL | {''}
    return re.compile(r'\b(%s)\b' % '|'.join(
        re.escape(t) for t in sorted(topics, key=len, reverse=True)))


def teachers(info_path, max_teachers=1):
    """`{topic: {quest: index}}` -- which quest's text teaches each topic.

    Matches WHOLE WORDS, and keeps a topic only when at most `max_teachers`
    QUESTS teach it -- never a speaker count, which drops real gates.
    See: docs/commentary/morrowind_runtime.md#prerequisites-are-conditions
    """
    rows = list(records(info_path))
    finder = _topic_finder(rows)
    out = {}
    for rec in rows:
        response = (rec.get('Response') or '').lower()
        if not response:
            continue
        sets = _sets(rec)
        for topic in set(finder.findall(response)):
            out.setdefault(topic, {}).update(sets)
    return {t: q for t, q in out.items() if q and len(q) <= max_teachers}

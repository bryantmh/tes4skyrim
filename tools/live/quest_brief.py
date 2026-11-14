"""Everything a `.tes4test` case needs to know about one TES4 quest, from the export alone.

    python -m tools.live.quest_brief --export export/Oblivion.esm --quest MS48 [--skeleton]

Per stage: the journal text, the stage's own result script, and every place that
sets it -- a script (with the `begin` block it sits in), another stage, or a
dialogue line (topic, speaker, stage conditions, the choices it offers).
`--skeleton` adds one draft case per stage to start from.

See: docs/commentary/headless_testing.md#drafting-a-case
"""
import argparse
import os
import re

from tools.dialog.quest_runtime_trace import parse_raw_ctda, read_export_type

#: TES4 condition functions worth naming in a brief.
FUNCTIONS = {58: 'GetStage', 59: 'GetStageDone', 72: 'GetIsID', 79: 'GetQuestVariable', 71: 'GetInCell',
             1: 'GetDistance'}
BEGIN = re.compile(r'^\s*begin\b(.*)$', re.IGNORECASE)


def split_lines(script: str) -> list:
    """The lines of an exported script text (its newlines are spelled out)."""
    return [line.replace('\\t', ' ').strip() for line in script.split('\\r\\n')]


def stages(quest: dict) -> list:
    """(index, journal text, result script) of each stage log entry, in record order."""
    out, i = [], 0
    while 'Stage[%d].Index' % i in quest:
        for j in range(int(quest.get('Stage[%d].LogCount' % i, 0) or 0)):
            out.append((int(quest['Stage[%d].Index' % i]), quest.get('Stage[%d].Log[%d].Text' % (i, j), ''),
                        quest.get('Stage[%d].Log[%d].ResultScript' % (i, j), '')))
        i += 1
    return out


def script_setters(export: str, setter) -> dict:
    """{stage: ['script NAME, begin BLOCK']} for every SCPT line that sets a stage of the quest."""
    found = {}
    for record in read_export_type(export, 'SCPT'):
        block = ''
        for line in split_lines(record.get('SCTX', '')):
            begin = BEGIN.match(line)
            block = begin.group(1).strip() if begin else block
            for stage in setter.findall(line):
                found.setdefault(int(stage), []).append(
                    'script %s, begin %s' % (record.get('EditorID', '?'), block or '?'))
    return found


def condition_text(raw: str, names: dict) -> str:
    """One readable condition, or '' for a function the brief does not name."""
    parsed = parse_raw_ctda(raw)
    if not parsed or parsed['func'] not in FUNCTIONS:
        return ''
    target = names.get(parsed['p1'] & 0xFFFFFF, '%08X' % parsed['p1'])
    return '%s %s %s %g' % (FUNCTIONS[parsed['func']], target, parsed['op'], parsed['val'])


def info_text(info: dict, topics: dict, names: dict) -> str:
    """One line for a dialogue INFO: topic, first spoken line, conditions and offered choices."""
    topic = topics.get(info.get('ParentDIAL', ''), ('?', ''))
    conditions = [condition_text(info['Condition[%d].Raw' % i], names)
                  for i in range(int(info.get('ConditionCount', 0) or 0))]
    choices = [topics.get(info['Choice[%d]' % i], ('?', '?'))[1]
               for i in range(int(info.get('ChoiceCount', 0) or 0))]
    return 'INFO %s, topic %s "%s": "%s" [%s]%s' % (
        info.get('FormID'), topic[0], topic[1], info.get('Response[0].ResponseText', '')[:70],
        '; '.join(c for c in conditions if c), ' offers: ' + ' / '.join(choices) if choices else '')


def info_setters(export: str, setter, names: dict) -> dict:
    """{stage: [info_text]} for every dialogue line whose result script sets a stage of the quest."""
    topics = {r['FormID']: (r.get('EditorID', ''), r.get('FULL', '')) for r in read_export_type(export, 'DIAL')}
    infos = list(read_export_type(export, 'INFO'))
    offered = offered_under(infos, topics)
    found = {}
    for info in infos:
        for stage in setter.findall(info.get('ResultScript', '')):
            under = sorted(offered.get(info.get('ParentDIAL', ''), ()))
            found.setdefault(int(stage), []).append(
                info_text(info, topics, names) + (' {a choice under: %s}' % ' | '.join(under) if under else ''))
    return found


def offered_under(infos: list, topics: dict) -> dict:
    """{choice topic FormID: {the topic text of each line that offers it}}."""
    offered = {}
    for info in infos:
        parent = topics.get(info.get('ParentDIAL', ''), ('?', ''))
        for i in range(int(info.get('ChoiceCount', 0) or 0)):
            offered.setdefault(info['Choice[%d]' % i], set()).add(parent[1] or parent[0])
    return offered


def editor_ids(export: str) -> dict:
    """{FormID low 24 bits: EditorID} over the record types a condition names."""
    names = {}
    for rectype in ('QUST', 'NPC_', 'CREA', 'CELL', 'ACHR', 'REFR'):
        for record in read_export_type(export, rectype):
            if record.get('EditorID'):
                names[int(record['FormID'], 16) & 0xFFFFFF] = record['EditorID']
    return names


def brief(export: str, quest_id: str, skeleton: bool) -> list:
    """The printed lines for `quest_id`.

    See: docs/commentary/headless_testing.md#drafting-a-case
    """
    quests = {r.get('EditorID', '').lower(): r for r in read_export_type(export, 'QUST')}
    quest = quests[quest_id.lower()]
    setter = re.compile(r'setstage\s+%s\s*,?\s*(\d+)' % re.escape(quest['EditorID']), re.IGNORECASE)
    names = editor_ids(export)
    setters = script_setters(export, setter)
    for stage, texts in info_setters(export, setter, names).items():
        setters.setdefault(stage, []).extend(texts)
    for other in quests.values():
        for index, _text, result in stages(other):
            for stage in setter.findall(result):
                setters.setdefault(int(stage), []).append('stage %s %d' % (other['EditorID'], index))
    out, seen = [], set()
    for index, text, result in stages(quest):
        out.append('# %s %d: %s' % (quest['EditorID'], index, text[:160]))
        out += ['#   result: %s' % line for line in split_lines(result) if line and not line.startswith(';')]
        wheres = None if index in seen else setters.get(index, [])
        seen.add(index)
        out += ['#   set by %s' % where for where in dict.fromkeys(wheres or ())]
        out += ['#   set by (nothing found)'] if wheres == [] else []
        if skeleton:
            out += ['case %s_%d' % (quest['EditorID'].lower(), index),
                    'until 3600 GetStage %s >= %d' % (quest['EditorID'], index), 'end', '']
    return out


def main():
    """Prints the brief."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--export', required=True, help='TES4 export folder (export/<plugin>)')
    parser.add_argument('--quest', required=True, help='quest EditorID')
    parser.add_argument('--skeleton', action='store_true', help='add a draft case per stage')
    args = parser.parse_args()
    if not os.path.isdir(args.export):
        parser.error('no export folder %s' % args.export)
    print('\n'.join(brief(args.export, args.quest, args.skeleton)))


if __name__ == '__main__':
    main()

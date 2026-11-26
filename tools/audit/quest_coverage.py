"""How many of each plugin's journal quests the headless quest cases check by name.

    python tools/validate/safe_run.py python -m tools.audit.quest_coverage [--export export]
        [--readme | --write-readme | --followers]

A journal quest is a TES4 QUST with at least one stage log text, or a TES3 journal topic (MWDI
`DialType=Journal`), counted only in the plugin that adds it (its masters' ids are left out). A quest
counts as tested when a case line outside comments names its EditorID. Case folders map to plugins
through `tools.live.full_run.STAGES`; a folder missing there takes the stage its name starts with.
Prints a Markdown table per plugin and per case folder; `--readme` prints the README's per-game bars,
each questline (by the ids' authored prefixes) with tested / total quests.
See: docs/audits/quest_test_coverage.md#method
"""
import argparse
import os
import re

from core.plugin_masters import master_dirs
from output_layout import record_dirs_for_assets
from tes5_import.base.text_reader import parse_export_file, unescape_value
from tools.live.full_run import MW, STAGES, TES

#: Case folders that are not quests (the MWScript opcode sweep).
NOT_QUESTS = ('ingame',)
#: The stage log text key of a TES4 QUST.
LOG_TEXT = re.compile(r'Stage\[\d+\]\.Log\[\d+\]\.Text$')
#: An EditorID-shaped word in a case line.
WORD = re.compile(r'[A-Za-z0-9_]+')
#: MorrowindRuntime's journal update line: `journal: <id> = <index>`.
JOURNAL_LINE = re.compile(r'journal: (.+?) = (-?\d+)')
#: Every (full_run stage, journal id) a run has advanced, kept across runs since temp/ logs get cleared.
REACHED_FILE = os.path.join('docs', 'audits', 'quest_test_coverage_reached.txt')


# ---------------------------------------------------------------------------
#  Journal quests and the cases that name them
# ---------------------------------------------------------------------------

def journal_ids(record_dir: str) -> set:
    """Lower-case EditorIDs of the journal quests in one plugin's export, masters' included."""
    ids = set()
    qust = os.path.join(record_dir, 'QUST.txt')
    if os.path.isfile(qust):
        ids |= {r['EditorID'].lower() for r in parse_export_file(qust)
                if r.get('EditorID') and any(LOG_TEXT.match(k) and v for k, v in r.items())}
    mwdi = os.path.join(record_dir, 'MWDI.txt')
    if os.path.isfile(mwdi):
        ids |= {r['EditorID'].lower() for r in parse_export_file(mwdi)
                if r.get('EditorID') and r.get('DialType') == 'Journal'}
    return ids


def own_journal_ids(record_dir: str, memo: dict) -> set:
    """The journal quests `record_dir`'s plugin adds: its own minus every master's, recursively."""
    if record_dir not in memo:
        memo[record_dir] = journal_ids(record_dir)
    own = set(memo[record_dir])
    pending = list(master_dirs(record_dir))
    while pending:
        d = pending.pop()
        if d not in memo:
            memo[d] = journal_ids(d)
        own -= memo[d]
        pending += master_dirs(d)
    return own


def case_folders() -> dict:
    """{folder path: stage name} for every quest case folder, mapped through full_run's stages."""
    listed = {os.path.normpath(f): stage for stage, _, runs in STAGES for _, f, *_ in runs}
    names = [s for s, _, _ in STAGES]
    out = {}
    for root in (os.path.join(MW, 'quests'), TES):
        for name in sorted(os.listdir(root)):
            path = os.path.normpath(os.path.join(root, name))
            if os.path.isdir(path) and name not in NOT_QUESTS:
                out[path] = listed.get(path) or next((s for s in names if name.startswith(s)), None)
    return out


def folder_words(folder: str) -> tuple:
    """(case count, lower-case words on the folder's non-comment case lines)."""
    cases, words = 0, set()
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name), encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith(('#', ';')):
                    continue
                cases += line.startswith('case ')
                words.update(w.lower() for w in WORD.findall(line))
    return cases, words


def stage_record_dirs(export: str, stage: str) -> list:
    """The record folders of every plugin a full_run stage loads."""
    mods = next(m for s, m, _ in STAGES if s == stage)
    dirs = []
    for mod in mods:
        path = os.path.join(export, mod)
        if os.path.isdir(path):
            dirs += [str(d) for d in record_dirs_for_assets(path)]
    return dirs


def reached_from_logs(log_dir: str) -> set:
    """{(stage, journal id)} every MorrowindRuntime log in `log_dir` shows advanced past index 0.

    full_run names each log `<stage>_<folder>_<n>.log`; NOT_QUESTS folders' logs are skipped.
    """
    stages = {s for s, _, _ in STAGES}
    got = set()
    for name in os.listdir(log_dir) if os.path.isdir(log_dir) else []:
        stage, _, rest = name.partition('_')
        if not name.endswith('.log') or stage not in stages or rest.startswith(NOT_QUESTS):
            continue
        with open(os.path.join(log_dir, name), encoding='utf-8', errors='replace') as fh:
            got |= {(stage, m.group(1).lower()) for m in map(JOURNAL_LINE.search, fh)
                    if m and int(m.group(2)) > 0}
    return got


def merge_reached(log_dir: str) -> dict:
    """{stage: journal ids} from REACHED_FILE plus `log_dir`'s logs, saved back to REACHED_FILE."""
    pairs = reached_from_logs(log_dir)
    if os.path.isfile(REACHED_FILE):
        with open(REACHED_FILE, encoding='utf-8') as fh:
            pairs |= {tuple(line.rstrip('\n').split('\t', 1)) for line in fh if '\t' in line}
    with open(REACHED_FILE, 'w', encoding='utf-8', newline='\n') as fh:
        fh.writelines(f'{s}\t{i}\n' for s, i in sorted(pairs))
    out = {}
    for stage, jid in pairs:
        out.setdefault(stage, set()).add(jid)
    return out


def coverage(export: str, reached: dict) -> tuple:
    """({plugin: (own journal ids, ids a case names or a run advanced)}, [(folder, stage, cases, named)])."""
    memo, words_by_dir, folders = {}, {}, []
    for folder, stage in case_folders().items():
        cases, words = folder_words(folder)
        named = 0
        for d in stage_record_dirs(export, stage) if stage else []:
            own = own_journal_ids(d, memo)
            words_by_dir.setdefault(d, set()).update((words | reached.get(stage, set())) & own)
            named += len(words & own)
        folders.append((os.path.basename(folder), stage, cases, named))
    plugins = {os.path.basename(d): (own_journal_ids(d, memo), hit) for d, hit in sorted(words_by_dir.items())}
    return {k: v for k, v in plugins.items() if v[0]}, folders


# ---------------------------------------------------------------------------
#  README view: games and their questlines
# ---------------------------------------------------------------------------

#: Morrowind's faction questlines by the journal id's authored prefix.
MW_FACTIONS = (('Great House Hlaalu', 'hh'), ('Great House Redoran', 'hr'), ('Great House Telvanni', 'ht'),
               ('Fighters Guild', 'fg'), ('Mages Guild', 'mg'), ('Thieves Guild', 'tg'),
               ('Tribunal Temple', 'tt'), ('Imperial Cult', 'ic'), ('Imperial Legion', 'il'),
               ('Morag Tong', 'mt'), ('Daedric shrines', 'da'), ('Vampire clans', 'va'))
#: Morrowind.esm's questlines: the main quest, then each faction id followed by `_` or a digit.
MW_LINES = (('Main quest', r'[abc]\d|cx_'),) + tuple((n, rx + r'(_|\d)') for n, rx in MW_FACTIONS)
#: Morrowind_ob.esm's questlines: Morroblivion prefixes every Morrowind id with `fbmw`.
MB_LINES = tuple((n, 'fbmw' + rx) for n, rx in (('Main quest', r'(mq|chargen|milo|corprus|a\d)'),)
                 + MW_FACTIONS + (('Tribunal', 'tr'), ('Bloodmoon', 'bm'), ('Raven Rock', 'co')))
#: Tamriel Rebuilt's questlines: `tr_m<map>_<faction>_`.
TR_LINES = tuple((n, r'tr_m\d_' + rx + '_') for n, rx in MW_FACTIONS
                 + (('East Empire Company', 'eec'), ('Blades', 'blades')))
#: Oblivion.esm's questlines outside Shivering Isles (`dark` before `da`: the first match wins).
OB_LINES = (('Main quest', r'mq\d|charactergen'), ('Fighters Guild', 'fg'), ('Mages Guild', 'mg'),
            ('Thieves Guild', 'tg'), ('Dark Brotherhood', 'dark'), ('Daedric shrines', 'da'))
#: (game, plugin, id scope regex, questlines, confidence that an untested quest works); unmatched ids are Misc.
GAMES = (
    ('Oblivion', 'Oblivion.esm', r'(?!se\d)', OB_LINES, 'medium'),
    ('Shivering Isles', 'Oblivion.esm', r'se\d', (('Main quest', r'se(0\d|1[0-4])(?!\d)'),), 'medium'),
    ('Nehrim', 'Nehrim.esm', '', (('Main quest', 'mq'),), 'low'),
    ('Morrowind', 'Morrowind.esm', '', MW_LINES, 'high'),
    ('Tribunal', 'Tribunal.esm', '', (('Main quest', 'tr'),), 'high'),
    ('Bloodmoon', 'Bloodmoon.esm', '', (('Main quest', 'bm'), ('Raven Rock', 'co')), 'high'),
    ('Arktwend', 'Arktwend_English.esm', '', (('Main quest', 'a_'),), 'high'),
    ('Morroblivion', 'Morrowind_ob.esm', '', MB_LINES, 'medium'),
    ('Tamriel Rebuilt', 'Tamriel Rebuilt 25.08.12', '', TR_LINES, 'high'),
)
#: (game, questline) with its own case folder run complete and clean.
DONE = (('Oblivion', 'Main quest'), ('Morrowind', 'Main quest'), ('Morrowind', 'Great House Hlaalu'),
        ('Morrowind', 'Thieves Guild'), ('Morrowind', 'Tribunal Temple'), ('Morrowind', 'Daedric shrines'),
        ('Morrowind', 'Vampire clans'), ('Tribunal', 'Main quest'), ('Bloodmoon', 'Main quest'),
        ('Bloodmoon', 'Raven Rock'), ('Arktwend', 'Main quest'), ('Tamriel Rebuilt', 'Great House Telvanni'))
#: The README comments that bracket the generated block.
README_MARKS = ('<!-- quest-coverage:start -->', '<!-- quest-coverage:end -->')
#: Width of a progress bar in characters.
BAR = 20


def game_lines(ids: set, scope: str, lines: tuple) -> list:
    """[(questline, its ids)] for the ids inside `scope`, each id in its first matching line, Misc last."""
    inside = {i for i in ids if re.match(scope, i)}
    groups = []
    for name, rx in lines:
        got = {i for i in inside if re.match(rx, i)}
        groups.append((name, got))
        inside -= got
    return groups + [('Misc', inside)]


def bar(hit: int, total: int) -> str:
    """`████░░░░` progress bar for hit / total."""
    full = round(BAR * hit / total) if total else 0
    return '`' + '█' * full + '░' * (BAR - full) + '`'


def questline_row(game: str, name: str, got: set, hit: set) -> tuple:
    """(quests tested, README line `- [✅ ]<questline>: tested / total`)."""
    mark = '✅ ' if (game, name) in DONE else ''
    return len(got & hit), f'- {mark}{name}: {len(got & hit)} / {len(got)}'


def readme_block(plugins: dict) -> str:
    """The README's per-game bars and confidence, with each questline's tested / total quests."""
    out, all_hit, all_total = [], 0, 0
    for game, plugin, scope, lines, confidence in GAMES:
        own, hit = plugins.get(plugin, (set(), set()))
        rows = [questline_row(game, n, g, hit) for n, g in game_lines(own, scope, lines) if g]
        total, tested = len({i for i in own if re.match(scope, i)}), sum(n for n, _ in rows)
        all_hit, all_total = all_hit + tested, all_total + total
        out.append(f'**{game}** {bar(tested, total)} {tested} / {total:,} ({100 * tested / total:.0f}%), '
                   f'confidence: **{confidence}**')
        out += [line for _, line in rows] + ['']
    out.append(f'**Fallout 3, Fallout: New Vegas** {bar(0, 1)} none yet\n')
    out.append(f'**All games** {bar(all_hit, all_total)} {all_hit:,} / {all_total:,} '
               f'({100 * all_hit / all_total:.0f}%)')
    return '\n'.join(out)


# ---------------------------------------------------------------------------
#  Untested quests with a follower or escort (Morrowind-format plugins)
# ---------------------------------------------------------------------------

#: An AI follow or escort command in a dialogue result or script.
FOLLOW = re.compile(r'\bai(follow|escort)\b', re.I)
#: A journal named by Journal / SetJournalIndex / GetJournalIndex, quoted (may hold spaces) or bare.
JOURNAL_REF = re.compile(r'\b(?:get|set)?journal(?:index)?\b\s*,?\s*(?:"([^"]+)"|([A-Za-z0-9_]+))', re.I)
#: The actor before `->AiFollow` / `->AiEscort`.
FOLLOWER = re.compile(r'"?([^"\r\n]+?)"?\s*->\s*ai(?:follow|escort)', re.I)


def follow_sites(record_dir: str) -> dict:
    """{journal id: {follower or script}} for every INFO result and script that runs AiFollow / AiEscort."""
    sites = {}
    for sig, key in (('MWIN', 'ResultScript'), ('SCPT', None)):
        path = os.path.join(record_dir, f'{sig}.txt')
        for rec in parse_export_file(path) if os.path.isfile(path) else []:
            text = str(rec.get(key, '')) if key else '\n'.join(str(v) for v in rec.values())
            text = unescape_value(text)
            if not FOLLOW.search(text):
                continue
            who = {m.group(1).strip() for m in FOLLOWER.finditer(text)}
            who = who or {rec.get('Actor') or rec.get('EditorID', '?')}
            for quoted, bare in JOURNAL_REF.findall(text):
                sites.setdefault((quoted or bare).lower(), set()).update(who)
    return sites


def untested_followers(export: str, plugins: dict) -> list:
    """[(plugin, journal id, followers)] for each untested own journal quest with a follow / escort site."""
    out = []
    for name, (own, hit) in plugins.items():
        record_dir = next((d for s, _, _ in STAGES for d in stage_record_dirs(export, s)
                           if os.path.basename(d) == name), None)
        if not record_dir or not os.path.isfile(os.path.join(record_dir, 'MWIN.txt')):
            continue
        out += [(name, jid, who) for jid, who in sorted(follow_sites(record_dir).items())
                if jid in own - hit]
    return out


# ---------------------------------------------------------------------------
#  Output
# ---------------------------------------------------------------------------

def write_readme(block: str, path: str = 'README.md') -> None:
    """Replaces the text between README_MARKS in `path` with `block`, UTF-8."""
    with open(path, encoding='utf-8') as fh:
        text = fh.read()
    start, end = README_MARKS
    a, b = text.index(start) + len(start), text.index(end)
    with open(path, 'w', encoding='utf-8', newline='') as fh:
        fh.write(text[:a] + '\n' + block + '\n' + text[b:])


def main() -> None:
    """Prints the plugin and folder tables, or with --readme the README block."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--export', default='export')
    ap.add_argument('--readme', action='store_true', help='print the README per-game block')
    ap.add_argument('--write-readme', action='store_true', help='rewrite the block inside README.md')
    ap.add_argument('--followers', action='store_true',
                    help='list untested journal quests whose dialogue or scripts run AiFollow / AiEscort')
    ap.add_argument('--logs', default=os.path.join('temp', 'full_run'),
                    help='full_run output whose runtime logs add the journals a run advanced')
    args = ap.parse_args()
    plugins, folders = coverage(args.export, merge_reached(args.logs))
    if args.followers:
        for name, jid, who in untested_followers(args.export, plugins):
            print(f'{name}\t{jid}\t{", ".join(sorted(who))}')
        return
    if args.readme or args.write_readme:
        block = readme_block(plugins)
        if args.write_readme:
            write_readme(block)
        print(block)
        return
    print('| Plugin | Journal quests | Checked by a case | Share |\n|---|---:|---:|---:|')
    for name, (own, hit) in plugins.items():
        print(f'| {name} | {len(own)} | {len(hit)} | {100 * len(hit) / len(own):.0f}% |')
    print('\n| Case folder | Stage | Cases | Quests named |\n|---|---|---:|---:|')
    for name, stage, cases, named in folders:
        print(f'| {name} | {stage or "-"} | {cases} | {named} |')


if __name__ == '__main__':
    main()

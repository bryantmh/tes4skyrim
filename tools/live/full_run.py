"""Full headless run of every quest/opcode case folder, chain by chain.

    python tools/validate/safe_run.py python -m tools.live.full_run [stage ...]
    env INSTANCES="1,2" (instances the lanes share), TOKENS="t1,t2" (their `checkout` lease tokens, same order),
        RESUME="<folder>:<case>" (start mid-lane)

Each stage builds its lane's instance with its plugins, then runs its folders in order. Lanes (stages that
share checkpoints) run side by side, one per free instance. A failing case is recorded and the folder resumes
at the next case whose predecessor's checkpoint exists, so one failure never hides the rest. Results append to
temp/full_run/full_run.jsonl and full_run.log as they land; each run's output and runtime log sit beside them.
See: docs/audits/mwscript_full_runs.md#goal-and-procedure
"""
import glob
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from tools.live.test_instance import my_games

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(REPO, 'temp', 'full_run')
MW = os.path.join(REPO, 'tes_runtime', 'morrowind', 'testdata')
TES = os.path.join(REPO, 'tes_runtime', 'tes', 'testdata', 'quests')
FAST = ['--fast', '--checkpoints', '--stop-on-fail']
RUNTIME_LOGS = {'mwtest': 'MorrowindRuntime.log', 'questtest': 'TESRuntime.log'}

MW_BASE = ['Morrowind.esm', 'TESGameSelect']
MW_BM = ['Morrowind.esm', 'Bloodmoon.esm', 'TESGameSelect']
MW_ALL = ['Morrowind.esm', 'Tribunal.esm', 'Bloodmoon.esm', 'TESGameSelect']
MORROBLIVION = ['Oblivion.esm', 'Morrowind_ob.esm', 'Morrowind-Morroblivion-Compatibility.esp',
                'Tamriel Data (HD)', 'Tamriel Rebuilt 25.08.12', 'TESGameSelect']

#: (stage name, build mods, [(tool, folder, start args, flags, timeout)]).
STAGES = [
    ('morrowind', MW_BASE, [
        ('mwtest', os.path.join(MW, 'ingame'), [], [], 1800),
        ('mwtest', os.path.join(MW, 'quests', 'morrowind_main'), ['--new-game', '2'], FAST, 21600),
        ('mwtest', os.path.join(MW, 'quests', 'morrowind_vampire_aundae'), ['--load', 'mwtest_a1_11_report'],
         FAST, 3600),
        ('mwtest', os.path.join(MW, 'quests', 'morrowind_vampire_berne'), ['--load', 'mwtest_a1_11_report'],
         FAST, 3600),
        ('mwtest', os.path.join(MW, 'quests', 'morrowind_vampire_quarra'), ['--load', 'mwtest_a1_11_report'],
         FAST, 3600),
        ('mwtest', os.path.join(MW, 'quests', 'morrowind_disease'), ['--load', 'mwtest_a1_11_report'],
         FAST, 1800),
        ('mwtest', os.path.join(MW, 'quests', 'morrowind_hlaalu'), ['--load', 'mwtest_a1_11_report'],
         FAST, 3600),
        ('mwtest', os.path.join(MW, 'quests', 'morrowind_thieves_guild'), ['--load', 'mwtest_a1_11_report'],
         FAST, 1800),
    ]),
    ('bloodmoon', MW_BM, [
        ('mwtest', os.path.join(MW, 'quests', 'bloodmoon_main'), ['--load', 'mwtest_c3_azura'], FAST, 14400),
        ('mwtest', os.path.join(MW, 'quests', 'raven_rock'), ['--load', 'mwtest_bm_wildhunt'], FAST, 14400),
        ('mwtest', os.path.join(MW, 'quests', 'raven_rock_brawl'), ['--load', 'mwtest_co_mine'],
         ['--checkpoints', '--stop-on-fail'], 3600),
    ]),
    ('tribunal', MW_ALL, [
        ('mwtest', os.path.join(MW, 'quests', 'tribunal_mournhold'), ['--load', 'mwtest_co_estate'], FAST, 14400),
        ('mwtest', os.path.join(MW, 'quests', 'tribunal_story'), ['--load', 'mwtest_ms_scroll_sales'], FAST, 14400),
        ('mwtest', os.path.join(MW, 'quests', 'morrowind_temple'), ['--load', 'mwtest_co_estate'], FAST, 21600),
        ('mwtest', os.path.join(MW, 'quests', 'morrowind_daedric'), ['--load', 'mwtest_tt_ebonymail_done'],
         FAST, 14400),
    ]),
    ('arktwend', ['Arktwend', 'TESGameSelect'], [
        ('mwtest', os.path.join(MW, 'quests', 'arktwend_main'), ['--new-game', '5'], FAST, 21600),
        ('mwtest', os.path.join(MW, 'quests', 'arktwend_side'), ['--load', 'mwtest_storm_markus_reward'],
         FAST, 3600),
    ]),
    ('morroblivion', MORROBLIVION, [
        ('questtest', os.path.join(TES, 'morroblivion_main'), ['--new-game', '3'], FAST, 3600),
        ('questtest', os.path.join(TES, 'morroblivion_quiz'), ['--load', 'mwtest_mb_chargen_dock'], FAST, 3600),
        ('mwtest', os.path.join(MW, 'quests', 'tr_telvanni'), ['--load', 'mwtest_mb_chargen_exit'], FAST, 21600),
        ('mwtest', os.path.join(MW, 'quests', 'tr_necrom'), ['--load', 'mwtest_ht_nu1_done'], FAST, 14400),
    ]),
    ('oblivion', ['Oblivion.esm', 'TESGameSelect'], [
        ('questtest', os.path.join(TES, 'oblivion_main'), ['--new-game', '1'], FAST, 7200),
    ]),
    ('nehrim', ['Nehrim.esm', 'Translation.esp', 'TESGameSelect'], [
        ('questtest', os.path.join(TES, 'nehrim_main'), ['--new-game', '4'], FAST, 10800),
    ]),
]

#: Stages that share checkpoints run in order on one instance; independent lanes take whichever instance is free.
LANES = (('morrowind', 'bloodmoon', 'tribunal'), ('arktwend',), ('morroblivion',))
LANE_INSTANCE = threading.local()


def log(text: str) -> None:
    """Prints `text` and appends it to full_run.log."""
    print(text, flush=True)
    with open(os.path.join(OUT_DIR, 'full_run.log'), 'a', encoding='utf-8') as f:
        f.write(time.strftime('[%H:%M:%S] ') + text + '\n')


def record(row: dict) -> None:
    """Appends one result row to full_run.jsonl."""
    with open(os.path.join(OUT_DIR, 'full_run.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(row) + '\n')


def instance(args: list, out_name: str) -> tuple:
    """(exit code, output) of `test_instance <args>` on this lane's instance, its output kept in `out_name`."""
    slot = getattr(LANE_INSTANCE, 'slot', 1)
    lease = ['--token', LANE_INSTANCE.token] if getattr(LANE_INSTANCE, 'token', '') else []
    proc = subprocess.run([sys.executable, '-m', 'tools.live.test_instance', '--instance', str(slot)] + lease + args,
                          cwd=REPO, capture_output=True, text=True, encoding='utf-8', errors='replace')
    text = proc.stdout + proc.stderr
    with open(os.path.join(OUT_DIR, out_name), 'w', encoding='utf-8') as f:
        f.write(text)
    keep_runtime_log(slot, args[0], out_name)
    return proc.returncode, text


def keep_runtime_log(slot: int, tool: str, out_name: str) -> None:
    """Copies the instance's runtime log beside the run's output (`<out_name>.log`), for case runs."""
    name = RUNTIME_LOGS.get(tool)
    path = os.path.join(my_games(slot), 'SKSE', name or '')
    if name and os.path.isfile(path):
        shutil.copyfile(path, os.path.join(OUT_DIR, os.path.splitext(out_name)[0] + '.log'))


def case_names(folder: str, ext: str) -> list:
    """Every case name in the folder's files, in run order."""
    names = []
    for path in sorted(glob.glob(os.path.join(folder, '*' + ext))):
        with open(path, encoding='utf-8') as f:
            names += re.findall(r'(?im)^\s*case\s+(.+?)\s*$', f.read())
    return names


def has_save(case: str) -> bool:
    """Whether `case`'s checkpoint exists (instances share instance 1's Saves)."""
    return os.path.isfile(os.path.join(my_games(1), 'Saves', 'mwtest_' + re.sub(r'\W', '_', case) + '.ess'))


def resume_point(names: list, after: int) -> int:
    """Index of the first case past `after` whose predecessor has a checkpoint, else -1."""
    for i in range(after + 1, len(names)):
        if has_save(names[i - 1]):
            return i
    return -1


def first_failure(where: dict, first: list, results: list, summary: list) -> int:
    """Index of the attempt's first failed case; a runner that stopped early charges its next case."""
    names = where['names']
    fails = [c for v, c, _ in results if not v.startswith('ok') and c in names]
    if fails:
        return names.index(fails[0])
    ran = [names.index(c) for _v, c, _d in results if c in names]
    failed = (max(ran) + 1) if ran else (names.index(first[1]) if first[:1] == ['--from'] else 0)
    if failed < len(names):
        record(dict(where['row'], case=names[failed], ok=False, detail='runner did not finish: ' + ' | '.join(summary)))
        log(f'  FAIL {names[failed]} (runner did not finish)')
    return failed


def run_attempt(where: dict, args: list) -> tuple:
    """Runs one attempt; records each case result. Returns (exit code, results, summary lines)."""
    code, text = instance(args, f"{where['row']['stage']}_{where['row']['folder']}_{where['row']['attempt']}.out")
    results = re.findall(r'(?m)^(ok  |FAIL)  (\S+)(.*)$', text)
    for verdict, case, detail in results:
        record(dict(where['row'], case=case, ok=verdict.startswith('ok'), detail=detail.strip()[:2000]))
        if not verdict.startswith('ok'):
            log(f'  FAIL {case}: {detail.strip()[:300]}')
    summary = [l for l in text.splitlines() if 'passed,' in l or 'did not finish' in l
               or 'GAME EXITED' in l or 'GAME HUNG' in l or 'SystemExit' in l or 'Error' in l][-3:]
    log(f'  exit {code}; {" | ".join(summary)}')
    return code, results, summary


def run_folder(stage: str, tool: str, folder: str, start: list, flags: list, timeout: int) -> None:
    """Runs one folder to its end, resuming past each failure."""
    names = case_names(folder, '.tes4test' if tool == 'questtest' else '.mwtest')
    label = os.path.basename(folder)
    first = start
    for attempt in range(1, len(names) + 2):
        where = {'names': names, 'row': {'stage': stage, 'folder': label, 'attempt': attempt}}
        log(f'{label}: run {attempt}: {" ".join(first)} ({len(names)} cases)')
        code, results, summary = run_attempt(where, [tool, '--cases', folder, '--timeout', str(timeout)] + flags + first)
        if code == 0:
            return
        failed = first_failure(where, first, results, summary)
        nxt = resume_point(names, failed)
        if nxt < 0:
            for n in names[failed + 1:]:
                record(dict(where['row'], case=n, ok=None, detail='not run: no checkpoint to resume from'))
            log(f'  {len(names) - failed - 1} case(s) left unrun: no checkpoint to resume from')
            return
        first = ['--from', names[nxt]]


def run_lane(slot: tuple, stages: tuple, wanted: set, resume: str) -> None:
    """Runs `stages` in order on instance `slot` (number, lease token); `resume` is `folder:case` to start mid-lane."""
    LANE_INSTANCE.slot, LANE_INSTANCE.token = slot
    slot = slot[0]
    folder_at, case_at = resume.split(':') if resume else ('', '')
    skipping = bool(folder_at)
    for stage, mods, folders in (s for s in STAGES if s[0] in stages):
        if (skipping and not any(os.path.basename(f[1]) == folder_at for f in folders)) or \
                (wanted and stage not in wanted):
            continue
        code, text = instance(['build'] + [a for m in mods for a in ('--mod', m)], f'{stage}_build.out')
        log(f'== {stage} (instance {slot}): build exit {code}: {text.strip().splitlines()[-1] if text.strip() else ""}')
        if code != 0:
            continue
        for tool, folder, start, flags, timeout in folders:
            if skipping and os.path.basename(folder) != folder_at:
                continue
            if skipping:
                start, skipping = ['--from', case_at], False
            run_folder(stage, tool, folder, start, flags, timeout)


def lane_folders(lane: tuple) -> set:
    """The folder names a lane's stages run."""
    return {os.path.basename(f[1]) for stage, _mods, folders in STAGES if stage in lane for f in folders}


def main() -> None:
    """Runs the stages named on the command line, or every lane; each lane takes a free instance."""
    os.makedirs(OUT_DIR, exist_ok=True)
    resume = os.environ.get('RESUME', '')
    wanted = set(sys.argv[1:])
    lanes = [lane for lane in LANES if not wanted or wanted & set(lane)]
    free = queue.Queue()
    slots = os.environ.get('INSTANCES', '1,2').split(',')
    tokens = os.environ.get('TOKENS', '').split(',')
    for i, slot in enumerate(slots):
        free.put((int(slot), tokens[i] if i < len(tokens) else ''))

    def on_free_instance(lane):
        """Runs `lane` on the next free instance, then frees it."""
        slot = free.get()
        try:
            run_lane(slot, lane, wanted, resume if resume.split(':')[0] in lane_folders(lane) else '')
        finally:
            free.put(slot)
    with ThreadPoolExecutor(max_workers=free.qsize()) as pool:
        for job in [pool.submit(on_free_instance, lane) for lane in lanes]:
            job.result()
    log('== full run finished')


if __name__ == '__main__':
    main()

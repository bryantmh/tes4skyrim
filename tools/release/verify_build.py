#!/usr/bin/env python3
"""Release gate for a converted build: every check prints what it looked at.

    python tools/release/verify_build.py --pre-deploy  --log REBUILD.out
    python tools/release/verify_build.py --post-deploy --log REBUILD.out \\
        --deploy "Oblivion.esm.zip=/path/to/mods/Oblivion Port - Oblivion.esm"

Reads the rebuild's own log (never a fixed path), the output and export trees,
and writes `gate_<stamp>.json` beside the log. Exit 0 only when no check
FAILs or REFUSEs; a check whose denominator is 0 REFUSES, since it inspected
nothing, and a check the build gives nothing to judge is N/A (not a failure).
Plugins and worldspaces come from the log; `--baseline` is the previous
run's gate JSON, which a check may not regress against.
See: docs/commentary/tools_release_gate.md
"""

import argparse
import datetime as _dt
import json
import os
import re
import sys
import zipfile
import zlib
from collections import namedtuple
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from asset_convert import case_paths
from output_layout import FINISHED_DIR_NAME, PACK_FAILED_SUFFIX

PASS, INFO, WARN, FAIL, REFUSE = 'PASS', 'INFO', 'WARN', 'FAIL', 'REFUSE'

#: The build holds nothing this check judges (no scripts, no baked LOD...).
NA = 'N/A'

#: Statuses that fail the gate.
BLOCKING = (FAIL, REFUSE)

#: One stamped step of a rebuild log: `=== 2026-09-28 20:26:29 convert X`.
Section = namedtuple('Section', 'label start lines')

_STAMP = re.compile(r'^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (.*)$')

#: `LODGen input: <file> (<n> references)`, one per worldspace a bake wrote.
_LOD_INPUT = re.compile(r'LODGen input: (.+) \((\d+) references\)')

#: What each check is called in the report.
NAMES = {
    'G1': 'case census', 'G2': 'mesh -> texture', 'G3': 'magic art',
    'G4': 'race skin tones', 'G5': 'door axes', 'G6': 'land layers',
    'G7': 'terrain LOD colour', 'G8': 'tree-card alpha', 'G9': 'LOD rows',
    'G10': 'deployed bytes', 'G11': 'LODGen bake', 'G12': 'script compile',
    'G13': 'BSA pack markers',
}

#: Checks each mode runs.
MODES = {
    'pre': ('G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G8', 'G9', 'G11',
            'G12', 'G13'),
    'post': ('G1', 'G10'),
}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def not_applicable(cid, why: str, **data) -> dict:
    """A check this build gives nothing to judge; never fails the gate."""
    return result(cid, NA, '0 applicable', why, **data)


def result(cid, status, denominator, detail, **data) -> dict:
    """One check's outcome; `denominator` says what it inspected."""
    return {'id': cid, 'name': NAMES.get(cid, cid), 'status': status,
            'denominator': denominator, 'detail': detail, 'data': data}


def ratio(cid, good: int, total: int, unit: str, detail='', **data) -> dict:
    """PASS when all `total` are good, FAIL when some are not, REFUSE on 0."""
    if total <= 0:
        return result(cid, REFUSE, f'0 {unit}', detail or 'nothing to inspect',
                      **data)
    status = PASS if good == total else FAIL
    return result(cid, status, f'{good}/{total} {unit}', detail, **data)


def verdict(results) -> str:
    """PASS unless a check FAILed or REFUSEd; WARN, INFO and N/A never fail."""
    return FAIL if any(r['status'] in BLOCKING for r in results) else PASS


def print_result(r) -> None:
    """One report line per check."""
    print(f"  {r['id']:<4} {r['name']:<20} {r['status']:<6} "
          f"[{r['denominator']}] {r['detail']}")


# ---------------------------------------------------------------------------
# The rebuild log
# ---------------------------------------------------------------------------


def read_log(path, since=None) -> list:
    """The log split at each `=== <time> <step>` stamp; text before it is 'preamble'.

    With `since`, every section stamped before it is dropped (a log that
    several runs appended to): the run then starts at the first later stamp.
    See: docs/commentary/tools_release_gate.md#stamps
    """
    sections = [Section('preamble', None, [])]
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            m = _STAMP.match(line.rstrip('\n'))
            if m:
                start = _dt.datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
                sections.append(Section(m.group(2).strip(), start, []))
            else:
                sections[-1].lines.append(line.rstrip('\n'))
    if since is None:
        return sections
    return [Section('preamble', None, [])] + [
        s for s in sections if s.start is not None and s.start >= since]


def run_start(sections):
    """When the rebuild began: its first stamp, else None."""
    return next((s.start for s in sections if s.start is not None), None)


def final_section(sections, prefix: str):
    """The LAST section whose step starts with `prefix`, else None."""
    hits = [s for s in sections if s.label.startswith(prefix)]
    return hits[-1] if hits else None


def all_lines(sections):
    """Every line of the log, sections in order."""
    return [line for s in sections for line in s.lines]


def section_window(sections, sec, log_path) -> tuple:
    """(start, end) epoch seconds of step `sec`: its stamp to the next stamp, or to the log's last write.

    Stamps have whole seconds, so the end gets one second of slack.
    See: docs/commentary/tools_release_gate.md#stamps
    """
    later = [s.start for s in sections[sections.index(sec) + 1:]
             if s.start is not None]
    end = later[0].timestamp() if later else Path(log_path).stat().st_mtime
    return sec.start.timestamp(), end + 1.0


def worldspaces_in(sections) -> list:
    """Worldspaces the final `create_lod` step baked, from its `LODGen input` lines."""
    sec = final_section(sections, 'create_lod')
    out = []
    for line in sec.lines if sec else ():
        m = _LOD_INPUT.search(line)
        name = m and lodgen_worldspace(m.group(1))
        if name and name not in out:
            out.append(name)
    return out


def lodgen_worldspace(path: str) -> str:
    """`.../LODGen TES4Tamriel.txt` -> 'TES4Tamriel' (either separator)."""
    stem = re.split(r'[\\/]', path.strip())[-1]
    stem = stem[:-4] if stem.lower().endswith('.txt') else stem
    return stem[len('LODGen '):] if stem.startswith('LODGen ') else ''


def plugins_in(sections) -> list:
    """Plugins the log converted, from its `convert <plugin>` stamps, in order."""
    out = []
    for s in sections:
        if s.label.startswith('convert '):
            name = s.label[len('convert '):].split()[0]
            if name not in out:
                out.append(name)
    return out


# ---------------------------------------------------------------------------
# Log checks: G3, G4, G9, G11, G12
# ---------------------------------------------------------------------------

_MAGIC = re.compile(r'Magic effect phase meshes: (\d+) written from (\d+) '
                    r'models \((\d+) not converted\)')
_RACES = re.compile(r'Race skin tones: (\d+) races resolved')
_COMPILE = re.compile(r'^\[(.+?)\] Compilation: (\d+)/(\d+) succeeded, '
                      r'(\d+) failed')


def check_magic_art(ctx) -> dict:
    """G3: in each plugin that had magic-effect models, every one split into phases, and ARTO/EXPL exist.

    Per `convert <plugin>` section; the records are counted in that
    plugin's own converted file. A plugin with no such model is left out.
    """
    from output_layout import plugin_esm
    per = magic_lines(ctx.sections)
    if not per:
        return result('G3', REFUSE, '0 models',
                      'no "Magic effect phase meshes" line')
    rows = {p: h for p, h in per.items() if h[1] + h[2] > 0}
    if not rows:
        return not_applicable('G3', 'no plugin has magic-effect models')
    records = {p: record_counts(plugin_esm(ctx.output, p, ctx.export),
                                (b'ARTO', b'EXPL')) for p in rows}
    done = sum(h[1] for h in rows.values())
    total = sum(h[1] + h[2] for h in rows.values())
    r = ratio('G3', done, total, 'models',
              f'{sum(h[0] for h in rows.values())} phase meshes; records '
              f'{records}', records=records)
    if r['status'] == PASS and not all(all(c.values())
                                       for c in records.values()):
        r['status'] = FAIL
    return r


def magic_lines(sections) -> dict:
    """{plugin: (written, models, not converted)} summed over its LAST convert section."""
    out = {}
    for sec in sections:
        if not sec.label.startswith('convert '):
            continue
        hits = [tuple(int(x) for x in m.groups()) for line in sec.lines
                for m in [_MAGIC.search(line)] if m]
        if hits:
            out[sec.label[len('convert '):].split()[0]] = tuple(
                sum(h[i] for h in hits) for i in range(3))
    return out


def record_counts(esm: Path, sigs) -> dict:
    """{signature: records} in a converted plugin, for `sigs`."""
    from tes5_import.base.tes5_reader import walk
    counts = {s.decode(): 0 for s in sigs}
    if not esm.is_file():
        return counts
    for rec, _stack in walk(esm.read_bytes(), *sigs):
        counts[rec.sig.decode()] += 1
    return counts


def read_baseline(path) -> dict:
    """{check id: data} from a previous run's gate JSON; {} without one."""
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    return {c['id']: c.get('data') or {} for c in data.get('checks', [])}


def baseline(ctx, cid: str, key: str, default=None):
    """One measured value of check `cid` in the baseline run, else `default`."""
    return ctx.baseline.get(cid, {}).get(key, default)


def check_races(ctx) -> dict:
    """G4: each converted plugin resolved at least one race skin tone."""
    found = [int(m.group(1)) for line in all_lines(ctx.sections)
             for m in [_RACES.search(line)] if m]
    return ratio('G4', sum(1 for n in found if n > 0), len(found),
                 'plugin lines', f'races resolved per plugin: {found}')


_NO_SCRIPTS = re.compile(r'^\[(.+?)\] No \.psc scripts found')


def check_compile(ctx) -> dict:
    """G12: no plugin fails a script that the baseline did not already fail.

    Failures are keyed by (plugin, script file), read from each plugin's
    `compile_errors.log`, never by count: a count would hide a different
    script failing in the same slot. The allowed set is the `--baseline`
    run's failed set plus `--allow-compile-fail PLUGIN:SCRIPT`. A plugin
    with no scripts is left out.
    See: docs/commentary/tools_release_gate.md#g12
    """
    rows, bare = {}, set()
    for line in all_lines(ctx.sections):
        m, none = _COMPILE.match(line), _NO_SCRIPTS.match(line)
        if m:
            rows[m.group(1)] = tuple(int(x) for x in m.groups()[1:])
        elif none:
            bare.add(none.group(1))
    want = [p for p in (ctx.plugins or sorted(rows))
            if p in rows or p not in bare]
    if not want:
        return not_applicable('G12', 'no plugin has scripts')
    judged = {p: _compile_row(ctx, p, rows.get(p)) for p in want}
    good = [p for p, j in judged.items() if j['status'] == PASS]
    r = ratio('G12', len(good), len(want), 'plugins with scripts',
              '; '.join(f"{p}: {j['why']}" for p, j in judged.items()
                        if j['status'] != PASS),
              rows={p: list(v) for p, v in rows.items()},
              failed={p: j['failed'] for p, j in judged.items()})
    if any(j['status'] == REFUSE for j in judged.values()):
        r['status'] = REFUSE
    return r


def _compile_row(ctx, plugin, row) -> dict:
    """{'status', 'why', 'failed'} for one plugin's compile summary."""
    if row is None:
        return {'status': FAIL, 'why': 'no Compilation line', 'failed': []}
    failed_n = row[2]
    if failed_n == 0:
        return {'status': PASS, 'why': '', 'failed': []}
    entries = compile_errors(ctx, plugin)
    if entries is None:
        return {'status': REFUSE, 'failed': [],
                'why': f'{failed_n} failed but no compile_errors.log from '
                       'this run'}
    failed = sorted({e.split(': ', 1)[0] for e in entries})
    new = sorted(set(failed) - allowed_failures(ctx, plugin))
    if len(entries) != failed_n:
        return {'status': FAIL, 'failed': failed,
                'why': f'log holds {len(entries)} entries, printed {failed_n}'}
    return {'status': FAIL if new else PASS, 'failed': failed,
            'why': f'new failures {new}' if new else ''}


def compile_errors(ctx, plugin):
    """Entries of `plugin`'s compile_errors.log from this run, else None."""
    from output_layout import plugin_out_root
    log = plugin_out_root(ctx.output, plugin, ctx.export) / 'scripts' / \
        'compile_errors.log'
    if not log.is_file() or not _newer(log, ctx.start):
        return None
    return [t for t in log.read_text(encoding='utf-8',
                                     errors='replace').splitlines() if t]


def allowed_failures(ctx, plugin) -> set:
    """Script files `plugin` may fail: the baseline's failed set plus the CLI's."""
    base = baseline(ctx, 'G12', 'failed', {}).get(plugin, [])
    return set(base) | {s for p, s in ctx.allow_compile_fail if p == plugin}


_SELECTION = re.compile(r'Object-LOD selection: (\d+) reference')
_RETRY = re.compile(r'Re-running without (.+) \(attempt \d+\)')


def check_lod_rows(ctx) -> dict:
    """G9: the rows on disk match the counts the bake printed (two instruments).

    Recount == listed passes; a mismatch after a NullReference retry (which
    rewrites the file) warns; any other mismatch, a stale file or a
    selection line disagreeing with the listed count fails.
    """
    sec = final_section(ctx.sections, 'create_lod')
    if sec is None:
        return result('G9', REFUSE, '0 worldspaces', 'no create_lod step')
    rows, problems = [], []
    window = section_window(ctx.sections, sec, ctx.log)
    at = [i for i, line in enumerate(sec.lines) if _LOD_INPUT.search(line)]
    for i, nxt in zip(at, at[1:] + [len(sec.lines)]):
        m = _LOD_INPUT.search(sec.lines[i])
        rows.append(_lod_row(Path(m.group(1)), int(m.group(2)),
                             sec.lines[max(0, i - 12):nxt], min(i, 12),
                             window, problems))
    listed = sum(r['listed'] for r in rows)
    if not rows:
        return result('G9', REFUSE, '0 worldspaces', 'no "LODGen input" line')
    status = FAIL if any(p[1] == FAIL for p in problems) else (
        WARN if problems else PASS)
    return result('G9', status, f'{listed} rows in {len(rows)} worldspaces',
                  '; '.join(p[0] for p in problems[:5]), worldspaces=rows)


def _lod_row(path: Path, listed: int, lines, at: int, window, problems) -> dict:
    """Recount one LODGen input file; add (message, severity) to `problems`.

    `lines` runs from just before its `LODGen input` line (`lines[at]`) to
    the next one, so a retry or selection line is this worldspace's own.
    The file must have been written inside the step's `window`: older is
    someone else's, newer is a LATER run's.
    """
    recount = count_lodgen_rows(path)
    retried = any(_RETRY.search(t) for t in lines[at:])
    sel = [int(m.group(1)) for t in lines[:at]
           for m in [_SELECTION.search(t)] if m]
    outside = path.is_file() and not (
        window[0] <= path.stat().st_mtime <= window[1])
    if recount is None or outside:
        problems.append((f'{path.name}: missing or not written inside the '
                         'create_lod step', FAIL))
    elif recount != listed:
        problems.append((f'{path.name}: {recount} rows, printed {listed}',
                         WARN if retried else FAIL))
    if sel and sel[-1] != listed:
        problems.append((f'{path.name}: selection says {sel[-1]}', FAIL))
    return {'file': str(path), 'listed': listed, 'recount': recount,
            'selection': sel[-1] if sel else None, 'retried': retried}


def count_lodgen_rows(path: Path):
    """Reference rows in a LODGen input file (header lines carry no tab), else None."""
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            return sum(1 for line in fh if line.count('\t') >= 12)
    except OSError:
        return None


#: G11: a worldspace may lose this fraction of the baseline run's tiles.
TILE_TOLERANCE = 0.01


def check_lodgen(ctx) -> dict:
    """G11: the final bake did not die, and each baked worldspace has its minimum tiles.

    The minimum is `--min-bto WRLD=N` when given, else the baseline run's
    count less TILE_TOLERANCE, else 1 (a baked worldspace with no tile).
    See: docs/commentary/tools_release_gate.md#baseline
    """
    sec = final_section(ctx.sections, 'create_lod')
    if sec is None:
        return result('G11', REFUSE, '0 tiles', 'no create_lod step')
    died = sum('NullReferenceException' in line for line in sec.lines)
    empty = sum('LODGen produced no .bto tiles' in line for line in sec.lines)
    tiles = {ws: len(list((ctx.lod / 'meshes' / 'terrain' / ws / 'Objects')
                          .glob('*.bto')))
             for ws in ctx.worldspaces}
    need = {ws: min_tiles(ctx, ws) for ws in tiles}
    short = {ws: f'{n} < {need[ws]}' for ws, n in tiles.items()
             if n < need[ws]}
    total = sum(tiles.values())
    if total == 0:
        return result('G11', REFUSE, '0 tiles', 'no .bto found', tiles=tiles)
    ok = not died and not empty and not short
    return result('G11', PASS if ok else FAIL,
                  f'{total} tiles in {len(tiles)} worldspaces',
                  f'NullReference x{died}, empty bakes x{empty}, '
                  f'below minimum: {short or "none"}', tiles=tiles,
                  minimum=need)


def min_tiles(ctx, ws: str) -> int:
    """The fewest `.bto` tiles worldspace `ws` may have (see check_lodgen)."""
    if ws in ctx.min_bto:
        return ctx.min_bto[ws]
    base = baseline(ctx, 'G11', 'tiles', {}).get(ws)
    return max(1, int(base * (1 - TILE_TOLERANCE))) if base else 1


# ---------------------------------------------------------------------------
# File checks: G1, G5, G8, G10
# ---------------------------------------------------------------------------


def check_census(ctx) -> dict:
    """G1: no two files share one lowercase archive path, in any tree or zip.

    Output trees, the LOD mod and the deploy folders are walked with
    `case_paths.census`; zip members are keyed the same way. Folder twins
    only warn (the packer merges them).
    """
    trees = census_trees(ctx)
    rows = [(label, case_paths.census(path)) for label, path in trees]
    rows += [(z.name, zip_census(z)) for z in ctx.zips]
    empty = [label for label, c in rows if c.files == 0]
    files = sum(c.files for _label, c in rows)
    clashes = {label: len(c.file_collisions) for label, c in rows
               if c.file_collisions}
    twins = sum(len(c.dup_groups) for _label, c in rows)
    if not rows or empty:
        return result('G1', REFUSE, f'{files} files in {len(rows)} trees',
                      f'empty: {empty or "no trees"}')
    status = FAIL if clashes else (WARN if twins else PASS)
    return result('G1', status, f'{files} files in {len(rows)} trees/zips',
                  f'file collisions {clashes or 0}; folder twins {twins}',
                  per_tree={label: c.files for label, c in rows})


def census_trees(ctx) -> list:
    """(label, folder) for every output tree, plus the deploy folders after a deploy."""
    out = [(d.name, d) for d in sorted(ctx.output.iterdir())
           if d.is_dir() and d.name != FINISHED_DIR_NAME]
    return out + [(f'deployed:{Path(d).name}', Path(d))
                  for _z, d in ctx.deploy]


def zip_census(path: Path):
    """A `case_paths.Census` of a zip's members, keyed by lowercase path."""
    keyed = {}
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if not info.is_dir():
                keyed.setdefault(info.filename.lower(), []).append(
                    info.filename)
    return case_paths.Census([], case_paths.collisions(keyed),
                             sum(len(v) for v in keyed.values()))


def check_door_axes(ctx) -> dict:
    """G5: each plugin with readable DOOR models has a current cache holding every one.

    The expectation is derived, never configured: the DOOR models in the
    plugin's export that resolve AND that the classifier reads, recounted
    from the original meshes. A plugin with none is left out; a cache at an
    older schema, or one missing any of those models, fails.
    See: docs/commentary/tools_release_gate.md#g5
    """
    from output_layout import assets_for, record_dir
    good, rows = 0, {}
    for plugin in ctx.plugins:
        rec = record_dir(ctx.export, plugin)
        want = classifiable_doors(rec)
        if not want:
            continue
        rows[plugin] = _door_row(assets_for(rec) / DOOR_AXIS_CACHE, want,
                                 ctx.start)
        good += rows[plugin]['ok']
    if not rows:
        return not_applicable('G5', 'no plugin has a door model that resolves')
    notes = '; '.join(f"{p}: {r['cached']}/{r['want']} current {r['current']}"
                      f" fresh {r['fresh']}" for p, r in rows.items())
    return ratio('G5', good, len(rows), 'plugins with doors', notes,
                 plugins=rows)


def _door_row(cache: Path, want: dict, start) -> dict:
    """How one plugin's door-axis cache covers the models it should hold ({model: path})."""
    from asset_convert.collision.collision_extract import (
        door_axis_cache_is_current)
    have = door_axis_models(cache)
    alone = confirm_alone(want[m] for m in sorted(set(want) - have))
    lost = sorted(m for m in set(want) - have if want[m] in alone)
    current = door_axis_cache_is_current(str(cache))
    return {'want': len(want) - len(set(want) - have) + len(lost),
            'cached': len(set(want) & have), 'lost': lost[:10],
            'current': current, 'fresh': _newer(cache, start),
            'ok': current and not lost}


#: The door-axis cache written beside a plugin's export assets.
DOOR_AXIS_CACHE = 'door_panel_axis_cache.json'


def classifiable_doors(record_dir) -> dict:
    """{model: mesh path} of the plugin's DOOR bases that resolve and classify (the gate's own recount).

    Same inputs as the converter's scan: the DOOR records' models, resolved
    case-blind in the plugin's export meshes, read at the closed pose.
    """
    from concurrent.futures import ProcessPoolExecutor
    from asset_convert.nif.door_plan import build_door_models
    from output_layout import assets_for
    root = assets_for(record_dir) / 'meshes'
    found = [(m, case_paths.resolve([root], m, 'gate_doors'))
             for m in sorted(build_door_models(record_dir))]
    found = [(m, str(p)) for m, p in found if p]
    if not found:
        return {}
    with ProcessPoolExecutor() as ex:
        ok = list(ex.map(door_classifies, [p for _m, p in found], chunksize=8))
    return {m: p for (m, p), good in zip(found, ok) if good}


def confirm_alone(paths) -> set:
    """The `paths` that still classify when each is read in a fresh process.

    pyffi's reader keeps state between files, so one model can classify in
    one batch and not in another; a model the scan may have missed for that
    reason is re-read alone before it counts as lost.
    """
    from concurrent.futures import ProcessPoolExecutor
    paths = list(paths)
    if not paths:
        return set()
    with ProcessPoolExecutor(max_tasks_per_child=1) as ex:
        return {p for p, good in zip(paths, ex.map(door_classifies, paths))
                if good}


def door_classifies(path: str) -> bool:
    """True when the door classifier reads a closed-pose geometry from `path`."""
    from asset_convert.collision.collision_extract import (
        door_closed_geometry, read_nif_data)
    try:
        return door_closed_geometry(read_nif_data(path)) is not None
    except Exception:
        return False


def _model(key: str) -> str:
    """`tes4/architecture/x.nif` -> `architecture/x.nif` (namespace dropped)."""
    return key.split('/', 1)[-1]


def door_axis_models(path: Path) -> set:
    """Models (namespace dropped) a door-axis cache holds; empty when unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set()
    return {_model(k) for k in data if not k.startswith('__')}


def _newer(path: Path, start) -> bool:
    """True when `path` was written after `start` (always True with no start)."""
    try:
        return start is None or path.stat().st_mtime >= start.timestamp()
    except OSError:
        return False


def check_tree_alpha(ctx) -> dict:
    """G8 (informational): tiles with billboard shapes vs tiles carrying an alpha property.

    Over every worldspace the final bake listed.
    """
    cards = alpha = 0
    for ws in ctx.worldspaces:
        for bto in (ctx.lod / 'meshes' / 'terrain' / ws / 'Objects').glob(
                '*.bto'):
            blob = bto.read_bytes().lower()
            if b'trees\\billboards' in blob or b'trees/billboards' in blob:
                cards += 1
                alpha += b'nialphaproperty' in blob
    return result('G8', INFO, f'{cards} tiles with billboards in '
                  f'{len(ctx.worldspaces)} worldspaces',
                  f'{alpha} of them carry NiAlphaProperty (vanilla: 0)')


def check_deploy(ctx) -> dict:
    """G10: every zip member is on disk in its mod folder with the same CRC-32."""
    total = bad = 0
    missing = []
    for zpath, folder in ctx.deploy:
        with zipfile.ZipFile(zpath) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                total += 1
                target = Path(folder) / info.filename
                if not target.is_file() or file_crc(target) != info.CRC:
                    bad += 1
                    missing.append(info.filename)
    return ratio('G10', total - bad, total, 'members',
                 f'differ or absent: {missing[:5]}' if missing else '')


def check_pack_markers(ctx) -> dict:
    """G13: no output plugin folder holds a failed-pack marker (its BSAs are stale).

    See: docs/commentary/tools_release_gate.md#g13
    """
    dirs = [d for d in sorted(ctx.output.iterdir())
            if d.is_dir() and d.name != FINISHED_DIR_NAME and d != ctx.lod]
    marked = [m.relative_to(ctx.output).as_posix() for d in dirs
              for m in sorted(d.glob('*' + PACK_FAILED_SUFFIX))]
    bad = {m.split('/', 1)[0] for m in marked}
    return ratio('G13', len(dirs) - len(bad), len(dirs), 'plugin folders',
                 f'failed or unfinished packs: {marked}' if marked else '',
                 markers=marked)


def file_crc(path: Path) -> int:
    """CRC-32 of a file, streamed."""
    crc = 0
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF


# ---------------------------------------------------------------------------
# G2: every texture a shipped mesh names is shipped by someone
# ---------------------------------------------------------------------------

#: A `.dds` path in mesh bytes: needs a separator, keeps `:` (docs/commentary/tools_release_gate.md#g2).
MESH_TEXTURE_RE = re.compile(
    rb'[A-Za-z0-9_ .:-]{0,200}[\\/][A-Za-z0-9_\\/ .:-]{0,200}?\.dds',
    re.IGNORECASE)


def check_textures(ctx) -> dict:
    """G2: texture paths in the packed meshes and LOD tiles vs every archive's table.

    Tables: our BSAs, the LOD mod's loose textures and the vanilla Skyrim
    BSAs (none found: REFUSE). Keys in `--allow-missing` (full keys), or
    absent in the `--baseline` run too, are allowed: only a NEW absence
    fails. The control, the texture the most meshes name, must be found.
    See: docs/commentary/tools_release_gate.md#g2
    """
    if not vanilla_bsas(ctx):
        return result('G2', REFUSE, '0 vanilla archives',
                      f'no Skyrim Data BSAs ({ctx.skyrim_data}); pass '
                      '--skyrim-data')
    tables = texture_table(ctx)
    refs, meshes = mesh_texture_refs(ctx)
    if meshes == 0 or not tables:
        return result('G2', REFUSE, f'{meshes} meshes',
                      f'{len(tables)} table entries')
    absent = sorted(r for r in refs if r not in tables)
    known = ctx.allow_missing | set(baseline(ctx, 'G2', 'absent', []))
    allowed = [r for r in absent if r in known]
    missing = [r for r in absent if r not in known]
    control = max(sorted(refs), key=refs.get) if refs else None
    blind = control is None or control not in tables
    stale = sorted(ctx.allow_missing - set(absent))
    status = FAIL if missing or blind else PASS
    return result('G2', status,
                  f'{len(refs) - len(absent)}/{len(refs)} textures '
                  f'from {meshes} meshes',
                  f'control {control} found: {not blind}; allowed '
                  f'{len(allowed)} (stale allow entries {len(stale)}); '
                  f'missing: {missing[:8]}',
                  missing=missing, allowed=allowed, stale_allow=stale,
                  control=control, absent=absent)


def read_allow_missing(path) -> frozenset:
    """Full texture keys (as G2 prints them) from `path`; `#` starts a comment."""
    if not path:
        return frozenset()
    keys = set()
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            key = line.split('#', 1)[0].strip().lower().replace('/', '\\')
            if key:
                keys.add(key)
    return frozenset(keys)


def texture_table(ctx) -> set:
    """Lowercase `textures\\`-relative paths every shipped archive or loose LOD file holds."""
    from tools.misc.bsa_list_names import list_names
    table = set()
    for bsa in our_bsas(ctx) + vanilla_bsas(ctx):
        for name in list_names(bsa):
            key = texture_key(name)
            if key:
                table.add(key)
    tex = ctx.lod / 'textures'
    if tex.is_dir():
        table |= {str(p.relative_to(tex)).lower().replace('/', '\\')
                  for p in tex.rglob('*.dds')}
    return table


def texture_key(name: str) -> str:
    """`Data\\Textures\\A\\B.dds` -> `a\\b.dds`; '' when not a `.dds`."""
    s = name.lower().replace('/', '\\').lstrip('\\')
    if not s.endswith('.dds'):
        return ''
    for prefix in ('data\\', 'textures\\'):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s


def our_bsas(ctx) -> list:
    """Every BSA in the output plugin trees."""
    return sorted(p for d in ctx.output.iterdir()
                  if d.is_dir() and d.name != FINISHED_DIR_NAME
                  for p in d.glob('*.bsa'))


def vanilla_bsas(ctx) -> list:
    """Every BSA in the Skyrim Data folder, or [] when it is not known."""
    if not ctx.skyrim_data or not Path(ctx.skyrim_data).is_dir():
        return []
    return sorted(Path(ctx.skyrim_data).glob('*.bsa'))


def mesh_texture_refs(ctx):
    """({texture key: meshes naming it} over shipped meshes and LOD tiles, meshes read)."""
    refs, meshes = {}, 0
    for blob in shipped_mesh_blobs(ctx):
        meshes += 1
        keys = {texture_key(m.decode('latin-1'))
                for m in MESH_TEXTURE_RE.findall(blob)}
        for key in keys - {''}:
            refs[key] = refs.get(key, 0) + 1
    return refs, meshes


_MESH_SUFFIXES = ('.nif', '.bto', '.btr')


def shipped_mesh_blobs(ctx):
    """Yield the bytes of every mesh in our mesh BSAs and the LOD mod's loose meshes."""
    from asset_convert.sources.bsa_extract import read_bsa_files
    from tools.misc.bsa_list_names import list_names
    for bsa in our_bsas(ctx):
        if ' - textures' in bsa.name.lower():
            continue
        names = [n for n in list_names(bsa) if n.lower().endswith(_MESH_SUFFIXES)]
        for i in range(0, len(names), 256):
            yield from read_bsa_files(bsa, names[i:i + 256]).values()
    meshes = ctx.lod / 'meshes'
    for p in sorted(meshes.rglob('*')) if meshes.is_dir() else ():
        if p.suffix.lower() in _MESH_SUFFIXES and p.is_file():
            yield p.read_bytes()


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def _terrain_checks():
    """G6/G7 live in gate_terrain (they need the LAND parser)."""
    from tools.release import gate_terrain
    return {'G6': gate_terrain.check_land_layers,
            'G7': gate_terrain.check_terrain_colour}


CHECKS = {
    'G1': check_census, 'G2': check_textures, 'G3': check_magic_art,
    'G4': check_races, 'G5': check_door_axes, 'G8': check_tree_alpha,
    'G9': check_lod_rows, 'G10': check_deploy, 'G11': check_lodgen,
    'G12': check_compile, 'G13': check_pack_markers,
}


def run_checks(ctx, ids) -> list:
    """Run `ids` in order; a check that raises is a FAIL naming the error."""
    table = dict(CHECKS, **_terrain_checks())
    out = []
    for cid in ids:
        try:
            r = table[cid](ctx)
        except Exception as exc:
            r = result(cid, FAIL, 'error', f'{type(exc).__name__}: {exc}')
        print_result(r)
        out.append(r)
    return out


def _parse_args(argv=None):
    """The command line."""
    ap = argparse.ArgumentParser(description='Verify a converted build.')
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument('--pre-deploy', action='store_true')
    mode.add_argument('--post-deploy', action='store_true')
    ap.add_argument('--log', required=True, help="the rebuild's own .out")
    ap.add_argument('--since', type=_stamp_time, metavar="'YYYY-MM-DD HH:MM:SS'",
                    help='ignore log sections stamped before this')
    ap.add_argument('--output', help="default: the config's outputDir")
    ap.add_argument('--export', help='default: the install export folder')
    ap.add_argument('--plugins', nargs='+', help='default: from the log')
    ap.add_argument('--skyrim-data', help='default: the configured Data')
    ap.add_argument('--source', action='append', default=[],
                    metavar='PLUGIN=PATH',
                    help="G6: a plugin's TES4 file; default: the registry's")
    ap.add_argument('--mae-bound', type=float,
                    help='G7 MAE bound; default: the baseline + tolerance')
    ap.add_argument('--deploy', action='append', default=[],
                    metavar='ZIP=MODDIR', help='post-deploy pairs (G10)')
    ap.add_argument('--min-bto', action='append', default=[],
                    metavar='WRLD=N',
                    help='G11 minimum tiles; default: the baseline less 1%%')
    ap.add_argument('--allow-missing', metavar='FILE',
                    help='G2: full texture keys known absent upstream')
    ap.add_argument('--baseline', metavar='GATE.json',
                    help="the previous run's gate JSON: no regression vs it")
    ap.add_argument('--allow-compile-fail', action='append', default=[],
                    type=_plugin_script, metavar='PLUGIN:SCRIPT',
                    help='G12: a known failing script')
    ap.add_argument('--only', help='comma-separated check ids')
    ap.add_argument('--json', help='default: gate_<stamp>.json beside the log')
    return ap.parse_args(argv)


def _plugin_script(text: str) -> tuple:
    """`Plugin.esp:Script.psc` -> ('Plugin.esp', 'Script.psc')."""
    plugin, sep, script = text.partition(':')
    if not (sep and plugin and script):
        raise argparse.ArgumentTypeError(f'want PLUGIN:SCRIPT, got {text!r}')
    return plugin, script


def _stamp_time(text: str):
    """`2026-09-28 20:26:29` (the log stamp's own format) -> datetime."""
    return _dt.datetime.strptime(text.strip(), '%Y-%m-%d %H:%M:%S')


def default_roots():
    """(output, export) as a convert run with this install's config would use."""
    from output_layout import DEFAULT_EXPORT, configured_output
    from source_paths import load_config
    return configured_output(load_config().get('outputDir')), DEFAULT_EXPORT


def build_context(args):
    """Everything the checks read, resolved once from the command line."""
    from asset_convert.lod.sibling_lod import LOD_DIR_NAME
    from asset_convert.sources.skyrim_assets import find_skyrim_data
    from core.worldspace_names import set_worldspace_plugins
    sections = read_log(args.log, args.since)
    out_default, export_default = default_roots()
    output = Path(args.output or out_default)
    finished = output / FINISHED_DIR_NAME
    deploy = [tuple(pair.split('=', 1)) for pair in args.deploy]
    plugins = args.plugins or plugins_in(sections)
    set_worldspace_plugins(plugins)
    ctx = argparse.Namespace(
        mode='post' if args.post_deploy else 'pre',
        sections=sections, start=run_start(sections), output=output,
        log=Path(args.log),
        export=Path(args.export or export_default),
        lod=output / LOD_DIR_NAME, plugins=plugins,
        worldspaces=worldspaces_in(sections),
        allow_missing=read_allow_missing(args.allow_missing),
        baseline=read_baseline(args.baseline),
        allow_compile_fail=args.allow_compile_fail,
        skyrim_data=args.skyrim_data or find_skyrim_data(),
        sources=dict(v.split('=', 1) for v in args.source),
        mae_bound=args.mae_bound, deploy=deploy,
        zips=sorted(finished.glob('*.zip')) if finished.is_dir() else [],
        min_bto=dict(_min_bto(v) for v in args.min_bto),
        cache={})
    return ctx


def _min_bto(spec: str) -> tuple:
    """`TES4Tamriel=997` -> ('TES4Tamriel', 997)."""
    name, _, n = spec.partition('=')
    return name, int(n)


def main(argv=None) -> int:
    """Run the mode's checks, write the JSON, exit 0 only on PASS."""
    args = _parse_args(argv)
    ctx = build_context(args)
    ids = MODES[ctx.mode]
    if args.only:
        ids = [i for i in ids if i in args.only.split(',')]
    print(f"Release gate ({ctx.mode}-deploy), log {args.log}, run started "
          f"{ctx.start}\n  output {ctx.output}; plugins {ctx.plugins}; "
          f"{len(ctx.worldspaces)} worldspaces")
    results = run_checks(ctx, ids)
    final = verdict(results)
    stamp = _dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    out = Path(args.json or Path(args.log).parent / f'gate_{stamp}.json')
    out.write_text(json.dumps({'mode': ctx.mode, 'log': str(args.log),
                               'run_start': str(ctx.start), 'verdict': final,
                               'plugins': ctx.plugins,
                               'baseline': args.baseline,
                               'worldspaces': ctx.worldspaces,
                               'checks': results}, indent=1, default=str),
                   encoding='utf-8')
    print(f"VERDICT: {final}  ({out})")
    return 0 if final == PASS else 1


if __name__ == '__main__':
    sys.exit(main())

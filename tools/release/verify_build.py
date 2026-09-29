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
from output_layout import FINISHED_DIR_NAME

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
}

#: Checks each mode runs.
MODES = {
    'pre': ('G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G8', 'G9', 'G11',
            'G12'),
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
    """G3: every magic-effect model split into phases, and ARTO/EXPL records exist."""
    hits = [tuple(int(x) for x in m.groups()) for line in all_lines(ctx.sections)
            for m in [_MAGIC.search(line)] if m]
    written = sum(h[0] for h in hits)
    done = sum(h[1] for h in hits)
    total = done + sum(h[2] for h in hits)
    counts = record_counts(ctx.output / 'Oblivion.esm' / 'Oblivion.esm',
                           (b'ARTO', b'EXPL'))
    r = ratio('G3', done, total, 'models',
              f'{written} phase meshes; records {counts}', records=counts)
    if r['status'] == PASS and not all(counts.values()):
        r['status'] = FAIL
    return r


def record_counts(esm: Path, sigs) -> dict:
    """{signature: records} in a converted plugin, for `sigs`."""
    from tes5_import.base.tes5_reader import walk
    counts = {s.decode(): 0 for s in sigs}
    if not esm.is_file():
        return counts
    for rec, _stack in walk(esm.read_bytes(), *sigs):
        counts[rec.sig.decode()] += 1
    return counts


def check_races(ctx) -> dict:
    """G4: each converted plugin resolved at least one race skin tone."""
    found = [int(m.group(1)) for line in all_lines(ctx.sections)
             for m in [_RACES.search(line)] if m]
    return ratio('G4', sum(1 for n in found if n > 0), len(found),
                 'plugin lines', f'races resolved per plugin: {found}')


def check_compile(ctx) -> dict:
    """G12: every plugin's scripts compiled with none failing."""
    rows = {}
    for line in all_lines(ctx.sections):
        m = _COMPILE.match(line)
        if m:
            rows[m.group(1)] = tuple(int(x) for x in m.groups()[1:])
    want = ctx.plugins or sorted(rows)
    good = [p for p in want if p in rows and rows[p][2] == 0
            and rows[p][0] == rows[p][1] > 0]
    absent = [p for p in want if p not in rows]
    return ratio('G12', len(good), len(want), 'plugins',
                 f'no Compilation line: {absent}' if absent else '',
                 rows={p: list(v) for p, v in rows.items()})


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
    at = [i for i, line in enumerate(sec.lines) if _LOD_INPUT.search(line)]
    for i, nxt in zip(at, at[1:] + [len(sec.lines)]):
        m = _LOD_INPUT.search(sec.lines[i])
        rows.append(_lod_row(Path(m.group(1)), int(m.group(2)),
                             sec.lines[max(0, i - 12):nxt], min(i, 12),
                             ctx.start, problems))
    listed = sum(r['listed'] for r in rows)
    if not rows:
        return result('G9', REFUSE, '0 worldspaces', 'no "LODGen input" line')
    status = FAIL if any(p[1] == FAIL for p in problems) else (
        WARN if problems else PASS)
    return result('G9', status, f'{listed} rows in {len(rows)} worldspaces',
                  '; '.join(p[0] for p in problems[:5]), worldspaces=rows)


def _lod_row(path: Path, listed: int, lines, at: int, start, problems) -> dict:
    """Recount one LODGen input file; add (message, severity) to `problems`.

    `lines` runs from just before its `LODGen input` line (`lines[at]`) to
    the next one, so a retry or selection line is this worldspace's own.
    """
    recount = count_lodgen_rows(path)
    retried = any(_RETRY.search(t) for t in lines[at:])
    sel = [int(m.group(1)) for t in lines[:at]
           for m in [_SELECTION.search(t)] if m]
    stale = (path.is_file() and start is not None
             and path.stat().st_mtime < start.timestamp())
    if recount is None or stale:
        problems.append((f'{path.name}: missing or older than the run', FAIL))
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


def check_lodgen(ctx) -> dict:
    """G11: the final bake did not die, and each worldspace has its minimum tiles."""
    sec = final_section(ctx.sections, 'create_lod')
    if sec is None:
        return result('G11', REFUSE, '0 tiles', 'no create_lod step')
    died = sum('NullReferenceException' in line for line in sec.lines)
    empty = sum('LODGen produced no .bto tiles' in line for line in sec.lines)
    tiles = {ws: len(list((ctx.lod / 'meshes' / 'terrain' / ws / 'Objects')
                          .glob('*.bto')))
             for ws in ctx.min_bto}
    short = {ws: n for ws, n in tiles.items() if n < ctx.min_bto[ws]}
    total = sum(tiles.values())
    if total == 0:
        return result('G11', REFUSE, '0 tiles', 'no .bto found', tiles=tiles)
    ok = not died and not empty and not short
    return result('G11', PASS if ok else FAIL, f'{total} tiles',
                  f'NullReference x{died}, empty bakes x{empty}, '
                  f'below minimum: {short or "none"}', tiles=tiles)


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
    """G5: each plugin's door-axis cache exists; where a minimum is set, it is fresh and full."""
    from output_layout import assets_for, record_dir
    good, notes = 0, []
    for plugin in ctx.plugins:
        cache = assets_for(record_dir(ctx.export, plugin)) / DOOR_AXIS_CACHE
        doors, fresh = door_axis_entries(cache), _newer(cache, ctx.start)
        need = DOOR_AXIS_MINIMUM.get(plugin)
        ok = doors is not None and (need is None or (fresh and doors >= need))
        good += ok
        notes.append(f'{plugin}: {doors if doors is not None else "no cache"}'
                     + (f' (need {need}, fresh {fresh})' if need else ''))
    return ratio('G5', good, len(ctx.plugins), 'plugins', '; '.join(notes))


#: The door-axis cache written beside a plugin's export assets.
DOOR_AXIS_CACHE = 'door_panel_axis_cache.json'

#: Doors a plugin's cache must hold (FR: 37 of its 57 door models resolve).
DOOR_AXIS_MINIMUM = {'DLCFrostcragReborn.esp': 37}


def door_axis_entries(path: Path):
    """Door models in a door-axis cache (its schema key excluded), else None."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return sum(1 for k in data if not k.startswith('__'))


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

#: Textures known to be absent upstream too (by file name).
KNOWN_ABSENT = frozenset({'cheydinhalstonewall.dds', 'default.dds'})

#: A texture that must be referenced AND found, or the check is blind.
CONTROL_TEXTURE = 'anmiddlehouselod01.dds'


def check_textures(ctx) -> dict:
    """G2: texture paths in the packed meshes and LOD tiles vs every archive's table.

    Tables: our BSAs, the LOD mod's loose textures and the vanilla Skyrim
    BSAs. Known upstream absentees are allowed; the control must be found.
    """
    tables = texture_table(ctx)
    refs, meshes = mesh_texture_refs(ctx)
    missing = sorted(r for r in refs if r not in tables
                     and r.rsplit('\\', 1)[-1] not in KNOWN_ABSENT)
    control = [r for r in refs if r.endswith(CONTROL_TEXTURE)]
    blind = not control or not all(r in tables for r in control)
    if meshes == 0 or not tables:
        return result('G2', REFUSE, f'{meshes} meshes',
                      f'{len(tables)} table entries')
    status = FAIL if missing or blind else PASS
    return result('G2', status,
                  f'{len(refs) - len(missing)}/{len(refs)} textures '
                  f'from {meshes} meshes',
                  f'control found: {not blind}; missing: {missing[:8]}',
                  missing=missing)


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
    """(texture keys named by shipped meshes and LOD tiles, meshes read)."""
    from asset_convert.lod.lod_gen import TEXTURE_PATH_RE
    refs, meshes = set(), 0
    for blob in shipped_mesh_blobs(ctx):
        meshes += 1
        for m in TEXTURE_PATH_RE.findall(blob):
            key = texture_key(m.decode('latin-1'))
            if key:
                refs.add(key)
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
    'G12': check_compile,
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
    ap.add_argument('--source-esm', help='TES4 Oblivion.esm (G6)')
    ap.add_argument('--deploy', action='append', default=[],
                    metavar='ZIP=MODDIR', help='post-deploy pairs (G10)')
    ap.add_argument('--min-bto', action='append', default=[],
                    metavar='WRLD=N', help='default TES4Tamriel=997')
    ap.add_argument('--only', help='comma-separated check ids')
    ap.add_argument('--json', help='default: gate_<stamp>.json beside the log')
    return ap.parse_args(argv)


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
        export=Path(args.export or export_default),
        lod=output / LOD_DIR_NAME, plugins=plugins,
        worldspaces=worldspaces_in(sections),
        skyrim_data=args.skyrim_data or find_skyrim_data(),
        source_esm=args.source_esm, deploy=deploy,
        zips=sorted(finished.glob('*.zip')) if finished.is_dir() else [],
        min_bto=dict(_min_bto(v) for v in args.min_bto) or {'TES4Tamriel': 997},
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
                               'worldspaces': ctx.worldspaces,
                               'checks': results}, indent=1, default=str),
                   encoding='utf-8')
    print(f"VERDICT: {final}  ({out})")
    return 0 if final == PASS else 1


if __name__ == '__main__':
    sys.exit(main())

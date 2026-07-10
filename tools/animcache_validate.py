"""Validate merged animationdatasinglefile.txt / animationsetdatasinglefile.txt
against the EXACT grammar from ck-cmd (references/ck-cmd-master/include/bs/*.h,
the Arcane University reference implementation of the Skyrim animation cache).

Usage:
  python tools/animcache_validate.py <meshes_dir> [--project NAME] [--dump]

Checks:
  - full-file consumption (no trailing/missing lines)
  - per-project block alignment (a misaligned count corrupts every later block)
  - animationdata <-> animationsetdata creature pairing (<name>Data\\<name>.txt)
  - clip trigger formats, movement block shape
Exit code 1 on any structural error.
"""

import argparse
import os
import sys


class Scanner:
    def __init__(self, lines):
        self.lines = lines
        self.pos = 0

    def next_line(self):
        if self.pos >= len(self.lines):
            raise EOFError(f'read past end at line {self.pos}')
        s = self.lines[self.pos]
        self.pos += 1
        return s

    def next_int(self):
        return int(self.next_line().strip())

    def has_next(self):
        return self.pos < len(self.lines)


def read_string_list(sc, lines_per_block=1):
    """MultiLineBlock.fromASCII: count + count*lines_per_block lines."""
    n = sc.next_int()
    return [sc.next_line() for _ in range(n * lines_per_block)]


def parse_project_block(lines, name):
    """ProjectBlock.parseBlock over an already-extracted wrapper block."""
    sc = Scanner(lines)
    errors = []
    has_files = sc.next_int() == 1
    files = read_string_list(sc) if has_files else []
    has_cache = sc.next_int() == 1
    clips = []
    if has_cache:
        while sc.has_next():
            # ClipGeneratorBlock: name, cache idx, playback, crop, crop,
            # trigger count, triggers..., then a blank separator line
            clip_name = sc.next_line()
            cache_idx = sc.next_int()
            playback = sc.next_line()
            crop1, crop2 = sc.next_line(), sc.next_line()
            ntrig = sc.next_int()
            trigs = [sc.next_line() for _ in range(ntrig)]
            for t in trigs:
                if ':' not in t:
                    errors.append(f'{name}: clip {clip_name} trigger '
                                  f'without ":": {t!r}')
            sep = sc.next_line()          # ProjectBlock.parseBlock nextLine()
            if sep.strip():
                errors.append(f'{name}: clip {clip_name} separator not '
                              f'blank: {sep!r}')
            clips.append((clip_name, cache_idx, ntrig))
    return {'files': files, 'has_cache': has_cache, 'clips': clips,
            'errors': errors}


def parse_animationdata(path):
    lines = open(path, encoding='latin-1').read().splitlines()
    sc = Scanner(lines)
    errors = []
    names = read_string_list(sc)
    projects = {}
    for name in names:
        start = sc.pos
        try:
            block_lines = read_string_list(sc)      # the wrapper
            pb = parse_project_block(block_lines, name)
            errors += pb['errors']
            if pb['has_cache']:
                motion_lines = read_string_list(sc)  # ProjectDataBlock wrapper
                pb['motion_lines'] = len(motion_lines)
                # movement block: per clip: idx, dur, ntrans, rows, nrot,
                # rows, blank
                msc = Scanner(motion_lines)
                moves = 0
                while msc.has_next():
                    msc.next_line()                      # cache index
                    msc.next_line()                      # duration
                    for _ in range(msc.next_int()):      # translation rows
                        row = msc.next_line()
                        if len(row.split()) != 4:
                            errors.append(f'{name}: bad trans row {row!r}')
                    for _ in range(msc.next_int()):      # rotation rows
                        row = msc.next_line()
                        if len(row.split()) != 5:
                            errors.append(f'{name}: bad rot row {row!r}')
                    sep = msc.next_line()
                    if sep.strip():
                        errors.append(f'{name}: movement separator not '
                                      f'blank: {sep!r}')
                    moves += 1
                # NOTE: clips and movement blocks legitimately differ in
                # vanilla (multiple clips share cache indices) — count
                # mismatch is NOT an error, so it is not checked here.
            projects[name] = pb
        except (EOFError, ValueError) as e:
            errors.append(f'{name}: PARSE FAILURE at file line '
                          f'{sc.pos + 1} (block began line {start + 1}): {e}')
            return names, projects, errors
    if sc.has_next():
        rem = len(lines) - sc.pos
        # trailing blank lines are tolerated by Scanner impls; flag content
        if any(l.strip() for l in lines[sc.pos:]):
            errors.append(f'{rem} unconsumed non-blank trailing lines '
                          f'(first: {lines[sc.pos]!r} at line {sc.pos + 1})')
    return names, projects, errors


def parse_animationsetdata(path):
    lines = open(path, encoding='latin-1').read().splitlines()
    sc = Scanner(lines)
    errors = []
    names = read_string_list(sc)
    blocks = {}
    for name in names:
        start = sc.pos
        try:
            set_files = read_string_list(sc)          # projectFiles
            sets = []
            for sf in set_files:
                version = sc.next_line()
                if version != 'V3':
                    errors.append(f'{name}/{sf}: version {version!r} != V3')
                swap_events = read_string_list(sc)
                nvars = sc.next_int()                 # HandVariableData
                for _ in range(nvars):
                    sc.next_line(); sc.next_line(); sc.next_line()
                nattacks = sc.next_int()
                attacks = []
                for _ in range(nattacks):
                    ev = sc.next_line()
                    sc.next_int()                     # mirrored
                    clips = read_string_list(sc)
                    attacks.append((ev, clips))
                crc = read_string_list(sc, lines_per_block=3)
                for j in range(0, len(crc), 3):
                    for k in range(3):
                        if not crc[j + k].strip().isdigit():
                            errors.append(f'{name}/{sf}: non-numeric crc '
                                          f'line {crc[j + k]!r}')
                sets.append({'file': sf, 'swap': swap_events,
                             'attacks': attacks, 'ncrc': len(crc) // 3})
            blocks[name] = sets
        except (EOFError, ValueError) as e:
            errors.append(f'{name}: PARSE FAILURE at file line '
                          f'{sc.pos + 1} (block began line {start + 1}): {e}')
            return names, blocks, errors
    if sc.has_next() and any(l.strip() for l in lines[sc.pos:]):
        errors.append(f'unconsumed trailing content at line {sc.pos + 1}: '
                      f'{lines[sc.pos]!r}')
    return names, blocks, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('meshes_dir')
    ap.add_argument('--project', help='dump this project (substring match)')
    ap.add_argument('--dump', action='store_true')
    args = ap.parse_args()

    ad_path = os.path.join(args.meshes_dir, 'animationdatasinglefile.txt')
    asd_path = os.path.join(args.meshes_dir, 'animationsetdatasinglefile.txt')

    names, projects, errors = parse_animationdata(ad_path)
    print(f'animationdata: {len(names)} projects, '
          f'{sum(1 for p in projects.values() if p["has_cache"])} with cache')
    snames, sblocks, serrors = parse_animationsetdata(asd_path)
    print(f'animationsetdata: {len(snames)} creature projects')

    # pairing check (AnimationCache::build)
    set_lower = {n.lower() for n in snames}
    for n in names:
        stem = os.path.splitext(os.path.basename(n))[0]
        key = f'{stem}data\\{stem}.txt'
        if key in set_lower and not projects.get(n, {}).get('has_cache'):
            errors.append(f'{n}: creature (in setdata) but hasAnimationCache=0')
    for n in snames:
        stem = n.split('\\')[0]
        if stem.lower().endswith('data'):
            stem = stem[:-4]
        if f'{stem.lower()}.txt' not in {x.lower() for x in names}:
            serrors.append(f'setdata {n}: no animationdata project '
                           f'{stem}.txt')

    for e in errors + serrors:
        print('ERROR:', e)

    if args.project:
        for n, p in projects.items():
            if args.project.lower() in n.lower():
                print(f'--- {n}: files={p["files"]} cache={p["has_cache"]} '
                      f'clips={len(p["clips"])}')
                if args.dump:
                    for c in p['clips']:
                        print('   clip', c)
        for n, sets in sblocks.items():
            if args.project.lower() in n.lower():
                for s in sets:
                    print(f'--- setdata {n}/{s["file"]}: '
                          f'swap={s["swap"]} attacks={len(s["attacks"])} '
                          f'crc_files={s["ncrc"]}')
                    if args.dump:
                        for a in s['attacks']:
                            print('   attack', a)

    ok = not errors and not serrors
    print('OK' if ok else 'STRUCTURAL ERRORS FOUND')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()

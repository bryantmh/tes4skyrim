"""Vendor the OpenMW subset the Morrowind runtime needs into external/openmw.

Resolves the transitive `#include <components/...>` closure from a set of roots
so the copied tree is exactly what compiles, rather than a hand-guessed list
that fails at link time. Source is a read-only checkout under references/, which
the pipeline must never build against.

See: docs/commentary/morrowind_runtime.md#vendoring
"""

import argparse
import os
import re
import shutil

#: Directories whose sources seed the include closure.
ROOTS = (
    'components/interpreter',
    'components/compiler',
)

#: TES3 records mwdialogue reads, named rather than closed over from it.
ESM3_RECORDS = (
    'loaddial', 'loadinfo', 'loadscpt', 'loadfact', 'loadskil', 'loadmgef',
    'loadnpc', 'loadcrea', 'loadgmst', 'loadrace', 'loadclas', 'loadcell',
    'dialoguecondition', 'variant', 'variantimp', 'queststate',
    'journalentry', 'dialoguestate', 'infoorder', 'esmreader', 'esmwriter',
    'esmcommon', 'loadtes3', 'cellid', 'cellref', 'formatversion',
    'loadlevlist', 'loadsoun', 'loadglob',
)

#: Copied whole; their closure reaches engine headers the adapters replace.
VERBATIM = (
    'apps/openmw/mwdialogue',
)

#: Single files pulled in without their directory.
EXTRA_FILES = (
    'LICENSE',
)

_INCLUDE = re.compile(r'#\s*include\s*<(components/[^>]+)>')
#: A quoted include resolves against the including file's own directory.
_LOCAL_INCLUDE = re.compile(r'#\s*include\s*"([^"]+)"')
_SOURCE_EXT = ('.cpp', '.hpp', '.h', '.c')


def _sources_in(root: str, rel_dir: str) -> list:
    """Every source file directly inside `rel_dir`, as repo-relative paths."""
    abs_dir = os.path.join(root, rel_dir)
    if not os.path.isdir(abs_dir):
        return []
    return [os.path.join(rel_dir, f).replace(os.sep, '/')
            for f in sorted(os.listdir(abs_dir))
            if f.endswith(_SOURCE_EXT)]


def _companion(rel: str) -> list:
    """A header's implementation file, and vice versa."""
    stem, ext = os.path.splitext(rel)
    if ext in ('.hpp', '.h'):
        return [stem + '.cpp']
    if ext == '.cpp':
        return [stem + '.hpp', stem + '.h']
    return []


def resolve(root: str, seeds: list = None) -> set:
    """Transitive closure of sources reachable from `seeds`, or from ROOTS."""
    pending = list(seeds) if seeds is not None else []
    if seeds is None:
        for rel_dir in ROOTS:
            pending.extend(_sources_in(root, rel_dir))
    seen = set()
    while pending:
        rel = pending.pop()
        if rel in seen:
            continue
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        seen.add(rel)
        text = open(path, encoding='utf-8', errors='replace').read()
        for inc in _INCLUDE.findall(text):
            pending.append(inc)
            pending.extend(_companion(inc))
        here = os.path.dirname(rel)
        for inc in _LOCAL_INCLUDE.findall(text):
            for base in (here, ''):
                cand = os.path.normpath(os.path.join(base, inc))
                cand = cand.replace(os.sep, '/')
                if not cand.startswith(('components/', 'apps/')):
                    continue
                pending.append(cand)
                pending.extend(_companion(cand))
    return seen


def _esm3_seeds() -> list:
    """Both source spellings of every named TES3 record."""
    out = []
    for stem in ESM3_RECORDS:
        out.append(f'components/esm3/{stem}.hpp')
        out.append(f'components/esm3/{stem}.cpp')
    return out


def plan(root: str) -> set:
    """Every repo-relative path to vendor: the closures, VERBATIM and EXTRA_FILES."""
    rels = resolve(root)
    rels |= resolve(root, _esm3_seeds())
    for rel_dir in VERBATIM:
        rels.update(_sources_in(root, rel_dir))
    for name in EXTRA_FILES:
        if os.path.isfile(os.path.join(root, name)):
            rels.add(name)
    return rels


def copy_tree(root: str, dest: str, rels: set) -> int:
    """Copy each path under `dest`, preserving layout; returns the file count."""
    for rel in sorted(rels):
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(root, rel), dst)
    return len(rels)


def write_version(root: str, dest: str) -> None:
    """Record the upstream commit so a rebase knows what it is rebasing from."""
    head = os.path.join(root, '.git', 'HEAD')
    sha = ''
    if os.path.isfile(head):
        ref = open(head, encoding='utf-8', errors='replace').read().strip()
        if ref.startswith('ref: '):
            ref_path = os.path.join(root, '.git', ref[5:])
            if os.path.isfile(ref_path):
                sha = open(ref_path, encoding='utf-8').read().strip()
        else:
            sha = ref
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, 'VERSION'), 'w', encoding='utf-8') as fh:
        fh.write(f'openmw {sha}\n')


def main() -> None:
    """CLI: report the vendoring plan, and apply it unless --dry-run."""
    ap = argparse.ArgumentParser(description='Vendor OpenMW into external/.')
    ap.add_argument('--source', default='references/openmw')
    ap.add_argument('--dest', default='external/openmw')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    rels = plan(args.source)
    by_dir = {}
    for rel in rels:
        key = os.path.dirname(rel) or '.'
        by_dir[key] = by_dir.get(key, 0) + 1
    for key in sorted(by_dir):
        print(f'{by_dir[key]:4d}  {key}')
    print(f'{len(rels):4d}  TOTAL')

    if args.dry_run:
        return
    print(f'copied {copy_tree(args.source, args.dest, rels)} -> {args.dest}')
    write_version(args.source, args.dest)


if __name__ == '__main__':
    main()

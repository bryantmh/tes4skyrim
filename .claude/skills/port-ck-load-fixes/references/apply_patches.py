#!/usr/bin/env python3
"""Apply (or dry-run) this skill's bundled patches without needing git.

Why this exists: the converter is not kept in a git repository, so `git apply`
is not available in every checkout that needs these fixes.  This applies the
same unified diffs directly, and refuses to touch a file whose context does not
match — a drifted fork gets a precise "line N differs" report instead of a
half-applied source tree.

Usage, from the TARGET checkout's root:

    # dry run: does every patch still apply here?
    python .claude/skills/port-ck-load-fixes/references/apply_patches.py --check

    # apply, writing .orig backups next to each patched file
    python .claude/skills/port-ck-load-fixes/references/apply_patches.py --apply

    # also drop the updated tools/ and docs/ in place (step 0 of the skill)
    python .claude/skills/port-ck-load-fixes/references/apply_patches.py --tools

`--check` exits non-zero if any patch would fail, so it can gate a merge.
A file that is ALREADY patched is reported as such and skipped, not failed —
re-running is safe.
"""

import argparse
import hashlib
import os
import re
import shutil
import sys

HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
HERE = os.path.dirname(os.path.abspath(__file__))


def apply_unified(src_lines, diff_lines):
    """Patched lines, or raise AssertionError naming the first mismatch."""
    out, si, i = [], 0, 0
    while i < len(diff_lines):
        m = HUNK.match(diff_lines[i])
        if not m:
            i += 1
            continue
        target = max(int(m.group(1)) - 1, 0)
        if target < si:
            raise AssertionError('hunks out of order')
        out.extend(src_lines[si:target])
        si = target
        i += 1
        while i < len(diff_lines) and not HUNK.match(diff_lines[i]):
            d = diff_lines[i]
            if d.startswith('---') or d.startswith('+++'):
                i += 1
                continue
            tag, text = d[:1], d[1:]
            if tag in ' -':
                if si >= len(src_lines) or src_lines[si] != text:
                    found = src_lines[si] if si < len(src_lines) else '<EOF>'
                    raise AssertionError(
                        f'line {si + 1}: expected {text!r}, found {found!r}')
                if tag == ' ':
                    out.append(src_lines[si])
                si += 1
            elif tag == '+':
                out.append(text)
            else:
                raise AssertionError(f'malformed diff line {d!r}')
            i += 1
    out.extend(src_lines[si:])
    return out


def _read(path):
    with open(path, encoding='utf-8', errors='strict') as fh:
        return fh.read()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', default='.',
                    help='the checkout to patch (default: cwd)')
    ap.add_argument('--check', action='store_true',
                    help='dry run; report only')
    ap.add_argument('--apply', action='store_true',
                    help='write the patched files (.orig backups kept)')
    ap.add_argument('--tools', action='store_true',
                    help='also copy the bundled tools/ and docs/ into place')
    args = ap.parse_args()
    if not (args.check or args.apply or args.tools):
        ap.error('pick --check, --apply or --tools')

    root = os.path.abspath(args.root)
    if not os.path.isdir(os.path.join(root, 'tes5_import')):
        sys.exit(f'{root} does not look like the converter checkout '
                 f'(no tes5_import/) -- pass --root')

    failed = applied = already = 0
    for name in sorted(os.listdir(HERE)):
        if not name.endswith('.diff'):
            continue
        rel = name[:-5].replace('__', os.sep)
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            print(f'MISS  {rel}  (not in this checkout)')
            failed += 1
            continue
        src = _read(path).splitlines(True)
        diff = _read(os.path.join(HERE, name)).splitlines(True)
        try:
            out = apply_unified(src, diff)
        except AssertionError as e:
            # Already patched?  Then the ADDED lines are all present already.
            adds = [d[1:] for d in diff
                    if d.startswith('+') and not d.startswith('+++')]
            if adds and all(a in src for a in adds):
                print(f'SKIP  {rel}  (already patched)')
                already += 1
                continue
            print(f'FAIL  {rel}\n      {e}')
            failed += 1
            continue
        if args.apply:
            shutil.copyfile(path, path + '.orig')
            with open(path, 'w', encoding='utf-8', newline='') as fh:
                fh.writelines(out)
            print(f'PATCH {rel}  (backup: {rel}.orig)')
            applied += 1
        else:
            digest = hashlib.sha1(''.join(out).encode()).hexdigest()[:10]
            print(f'OK    {rel}  applies cleanly -> {digest}')
            applied += 1

    if args.tools:
        for sub in ('tools', 'docs'):
            srcdir = os.path.join(HERE, sub)
            if not os.path.isdir(srcdir):
                continue
            dstdir = os.path.join(root, sub)
            os.makedirs(dstdir, exist_ok=True)
            for f in sorted(os.listdir(srcdir)):
                shutil.copyfile(os.path.join(srcdir, f),
                                os.path.join(dstdir, f))
                print(f'COPY  {sub}/{f}')

    print(f'\n{applied} patch(es) {"applied" if args.apply else "would apply"}, '
          f'{already} already patched, {failed} failed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

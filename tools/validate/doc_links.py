#!/usr/bin/env python3
"""Every link into or between `docs/` must resolve, case included.

    python tools/validate/doc_links.py            # report, exit 1 if broken
    python tools/validate/doc_links.py --index    # also demand a docs/README row

Checks markdown links and bare `docs/...md#anchor` citations from `docs/`
itself, `CLAUDE.md`, `README.md` and every first-party `.py`.  Paths resolve
case-sensitively because Windows does not, which is how a link to a file that
is really `Script_Conversion_Plan.md` survived unnoticed.

Run it BEFORE and AFTER any move under `docs/`: a green run on the tree you
started with is what proves the move introduced nothing.
"""

import argparse
import os
import re
import sys
from pathlib import Path

from tools.validate import code_rules as CR

ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = ROOT / 'docs'

#: `[text](target)`, the markdown form; group 1 is the target.
MD_LINK = re.compile(r'\]\(([^)]+)\)')

#: A bare `docs/x.md#anchor` in prose; `\B` keeps it off `game_bridge/docs/`.
BARE = re.compile(r'(?<![A-Za-z0-9_/-])docs/[A-Za-z0-9_./-]+\.md'
                  r'(?:#[A-Za-z0-9-]+)?')

#: Targets that are not repo files: URLs, anchors on the same page, mailto.
EXTERNAL = ('http://', 'https://', 'mailto:', '#')

#: A link target must look like a PATH; `[]( branch )` in prose is not one.
PATHLIKE = re.compile(r'\.(?:md|py|txt|json|toml|pyw)(?:#|$)|/')

#: `#L172` / `#L78-L85` -- GitHub line anchors, which are not headings.
LINE_ANCHOR = re.compile(r'^L\d+(?:-L\d+)?$')

#: A filename may not repeat the kind its folder already states.
KIND_SUFFIX = {'audits': ('_audit', '_audits'),
               'plans': ('_plan', '_plans'),
               'commentary': ('_commentary', '_notes'),
               'reference': ('_reference',)}

#: Every `commentary/` file names the code it explains on this line.
CODE_LINE = '**Code:**'

#: Files outside docs/ that link into it and must be rewritten on a move.
EXTRA_SOURCES = ('CLAUDE.md', 'README.md', 'TODO.txt')


def _real(path: Path) -> bool:
    """True when `path` exists with EXACTLY this spelling."""
    if not path.parent.exists():
        return False
    return path.name in os.listdir(path.parent)


def _targets(source: Path) -> list:
    """`(line, target)` for every repo-local link in `source`; none once it is gone (another session's temp file)."""
    try:
        text = source.read_text(encoding='utf-8', errors='replace')
    except FileNotFoundError:
        return []
    prose = source.suffix in ('.md', '.txt')
    out = []
    for num, line in enumerate(text.split('\n'), 1):
        if prose:
            for hit in MD_LINK.findall(line):
                target = hit.split()[0].strip()
                if not target.startswith(EXTERNAL) and PATHLIKE.search(target):
                    out.append((num, target))
        elif not CR.REFERENCE.search(line):
            continue
        for hit in BARE.findall(line):
            out.append((num, hit))
    return out


def _resolve(source: Path, target: str) -> Path:
    """The file a link points at, relative to its source or the repo root."""
    rel = target.split('#')[0]
    if rel.startswith('docs/'):
        return ROOT / rel
    return (source.parent / rel).resolve()


def _broken(source: Path) -> list:
    """`(line, target, why)` for each link in `source` that does not resolve."""
    out = []
    for num, target in _targets(source):
        rel, _, anchor = target.partition('#')
        if not rel:
            continue
        path = _resolve(source, target)
        if not _real(path):
            out.append((num, target, 'no such file'))
        elif anchor and path.suffix == '.md' and not LINE_ANCHOR.match(anchor):
            if anchor not in CR._headings(path):
                out.append((num, target, 'no such anchor'))
    return out


def sources() -> list:
    """Every file that may link into `docs/`."""
    found = sorted(DOCS.rglob('*.md'))
    found += [ROOT / name for name in EXTRA_SOURCES if (ROOT / name).exists()]
    return found + CR.repo_files()


def misnamed() -> list:
    """Docs whose filename repeats the kind its folder already states."""
    bad = []
    for doc in sorted(DOCS.rglob('*.md')):
        folder = doc.parent.name
        if folder not in KIND_SUFFIX:
            continue
        for suffix in KIND_SUFFIX[folder]:
            if doc.stem.endswith(suffix):
                bad.append((doc, suffix))
    return bad


def unbound() -> list:
    """Commentary files that do not name the code they explain."""
    folder = DOCS / 'commentary'
    return [d for d in sorted(folder.glob('*.md'))
            if CODE_LINE not in d.read_text(encoding='utf-8', errors='replace')]


def unindexed() -> list:
    """Docs missing a row in `docs/README.md`, which is the index."""
    index = DOCS / 'README.md'
    if not index.exists():
        return []
    listed = index.read_text(encoding='utf-8', errors='replace')
    return [d for d in sorted(DOCS.rglob('*.md'))
            if d != index and d.name not in listed]


def main(argv=None) -> int:
    """Report every unresolved link; 1 when any is broken."""
    parser = argparse.ArgumentParser(
        description='Check that every link into docs/ resolves.')
    parser.add_argument('--index', action='store_true',
                        help='also require each doc to appear in docs/README.md')
    args = parser.parse_args(argv)
    total = 0
    for source in sources():
        for num, target, why in _broken(source):
            try:
                shown = source.relative_to(ROOT)
            except ValueError:
                shown = source
            print('  %s:%d: %s -- %s' % (shown, num, target, why),
                  file=sys.stderr)
            total += 1
    if args.index:
        for doc, suffix in misnamed():
            print('  %s: drop the `%s` suffix -- the folder says the kind'
                  % (doc.relative_to(ROOT), suffix), file=sys.stderr)
            total += 1
        for doc in unbound():
            print('  %s has no %s line naming the code it explains'
                  % (doc.relative_to(ROOT), CODE_LINE), file=sys.stderr)
            total += 1
        for doc in unindexed():
            print('  %s is in no docs/README.md row' % doc.relative_to(ROOT),
                  file=sys.stderr)
            total += 1
    print('  %d broken link%s' % (total, '' if total == 1 else 's'))
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())

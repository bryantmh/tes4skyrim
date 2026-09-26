#!/usr/bin/env python3
"""The repo-wide code rules, enforced per file.

    python tools/validate/code_rules.py --gate-file  PATH   # whole file
    python tools/validate/code_rules.py --gate-diff  PATH   # what YOUR edit owns
    python tools/validate/code_rules.py --dead-code         # repo sweep
    python tools/validate/code_rules.py --legend            # what each rule means

These rules are true of any Python, so they apply everywhere.  The rules that
only mean something inside `script_convert/` live in
`tools/script/arch_fitness.py`, which imports the detectors from here so a rule
can never be enforced under a definition the score does not share.

Per-file rules are an ABSOLUTE gate with no baseline: a single file either has
the property or it does not.  Only package AGGREGATES get a baseline, because
only a trend is meaningful for those.

See: docs/reference/script_convert_architecture.md
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import warnings
from pathlib import Path

from tools.validate import code_rules_ast as D

ROOT = Path(__file__).resolve().parent.parent.parent

#: Scratch, vendored and generated trees are not judged.
SKIP_PARTS = ('temp', 'references', 'external', 'output', 'export', 'build',
              'dist', '.git', 'node_modules', '__pycache__', '.venv')

#: Dataflow facts, not heuristics: ruff reports these with no false positives.
RUFF_CODES = 'F401,F811,F841,F821'

#: An import and its use, which one edit cannot always write together.
PAIRED_CODES = ('F401', 'F811', 'F821')

#: Whole rules whose other half lives elsewhere: a citation and its doc.
PAIRED_RULES = ('dead-citations',)

#: Only 100% confidence; at 60 the `@command` registry alone cries wolf.
VULTURE_CONFIDENCE = '100'
VULTURE_IGNORE_DECORATORS = '@command,@command.*'

#: A citation to a doc, the route prose takes out of the code.
CITATION = re.compile(r'docs/[A-Za-z0-9_./-]+\.md(?:#[A-Za-z0-9-]+)?')

#: A line that CITES a doc, not one naming a path as data or a CLI argument.
REFERENCE = re.compile(r'(?i)\b(?:see|per|details?|rationale|documented)\b'
                       r'|\]\(')

#: An explicit `<a id="...">` target, used by docs for mid-section anchors.
EXPLICIT_ANCHOR = re.compile(r'<a\s+id=["\']([^"\']+)["\']')

#: Renames seen before staging; machine-local, since it only bridges to git.
RENAME_MAP = ROOT / '.claude' / 'renames.json'

#: What each rule MEANS; with REMEDY it is the rule's whole definition.
EXPLAIN = {
    'inline-comments': 'a prose comment inside a function body',
    'stray-comments': 'a prose comment outside every function',
    'comment-blocks': 'comment block over %d chars' % D.MAX_DOC_CHARS,
    'bloated-docstrings': 'docstring over %d chars (%d on a 1-2 line body)'
                          % (D.MAX_DOC_CHARS, D.TINY_DOC_CHARS),
    'missing-docstrings': 'function over %d statements with no docstring'
                          % D.TINY_BODY_LINES,
    'fat-attr-docs': 'a `#:` doc running past one %d-char line'
                     % D.MAX_ATTR_DOC_CHARS,
    'fat-sections': 'section heading over %d chars of prose' % D.MAX_DOC_CHARS,
    'unsectioned-defs': 'a def above the first heading in a sectioned file',
    'god-functions': 'cyclomatic complexity over %d' % D.MAX_COMPLEXITY,
    'long-functions': 'over %d statements' % D.MAX_FUNCTION_LINES,
    'deep-nesting': 'if/for/while/with/try nested over %d deep' % D.MAX_NESTING,
    'multi-return-fns': 'over %d return points -- a dispatch chain'
                        % D.MAX_RETURNS,
    'mutable-class-state': 'class-level dict/list/set as a global channel',
    'oversized-files': 'files over %d code lines' % D.MAX_FILE_LINES,
    'dead-imports': 'an unused import, variable, or undefined name',
    'dead-code': 'a symbol or branch nothing in the repo can reach',
    'dead-citations': 'a `docs/` path or anchor that does not exist',
    'anchorless-citations': 'a docstring citing a `docs/` file with no #anchor',
    'private-imports': "another module's `_name`, which is not an interface",
    'broken-syntax': 'a file Python cannot parse',
}

#: How to FIX each rule; the locator says where, this says what to do.
REMEDY = {
    'inline-comments':
        'lift it into the function docstring, or name a helper after it',
    'stray-comments':
        'move it into the nearest docstring; a measurement or date goes to a '
        '`docs/` file cited by `See:`',
    'comment-blocks':
        'move it into the module docstring, or a `docs/` file cited by `See:`',
    'bloated-docstrings':
        'keep the contract; evidence goes to a `docs/` file cited by `See:`',
    'missing-docstrings':
        'add a one-line docstring saying what it returns',
    'fat-attr-docs':
        'a `#:` doc is ONE line of %d chars; longer goes in a docstring, or a '
        '`docs/` file cited by `See:`' % D.MAX_ATTR_DOC_CHARS,
    'fat-sections':
        'a section heading is a LABEL; move the prose to a docstring, or a '
        '`docs/` file cited by `See:`',
    'unsectioned-defs':
        'this file uses `# ----` sections; move the def under one',
    'god-functions':
        'split it: extract the branch arms, or drive them from a table',
    'long-functions':
        'split it into named phases',
    'deep-nesting':
        'invert the guards and return early, or extract the inner block',
    'multi-return-fns':
        'a dispatch chain -- make it a table lookup',
    'mutable-class-state':
        'a class-level dict/list/set is a global; make it an instance field',
    'oversized-files':
        'split the file by responsibility (CLAUDE.md: keep files under ~%d '
        'CODE lines, comments are NOT counted)' % D.MAX_FILE_LINES,
    'dead-imports':
        'delete it; an unused name is indistinguishable from a mistake',
    'dead-code':
        'delete it; unreachable code is a bug that cannot announce itself',
    'dead-citations':
        'fix the path or the anchor -- a citation that lies loses the knowledge',
    'anchorless-citations':
        'name the SECTION: a bare path points at a whole file, so it points at '
        'nothing.  Add `#the-heading-slug`, or write the fact in the docstring',
    'private-imports':
        'the leading underscore says this is not an interface -- promote the '
        'name (drop the underscore) or call a public function that uses it',
    'broken-syntax':
        'fix the syntax error -- an unparsable file scores NO other rule, so '
        'this passing is indistinguishable from a clean file',
}

#: What to run when a checker is missing, named so a new machine can recover.
INSTALL = 'python -m pip install -e ".[dev]"   (installs ruff, vulture, pytest)'

#: `(rule, substring) -> the fix for THAT cause`; see `_specific_hints`.
SPECIFIC = {
    ('stray-comments', 'noqa: E402'):
        'the import cannot sit at the top because of a `sys.path` insert -- '
        'delete both: `pythonpath` in pyproject.toml (tests) or a package '
        'import (tools) makes the path hack unnecessary',
    ('stray-comments', 'noqa'):
        'a suppression is not prose: fix what it silences, then delete it',
    ('dead-imports', 'F821'):
        'an undefined name is a CRASH waiting on that branch -- add the '
        'import or delete the code; never leave it inside a bare `except`',
    ('dead-imports', 'F841'):
        'the value is computed and thrown away: delete it, or use it',
}

#: Pulls the measured magnitude out of a site detail ("f: 73 lines" -> 73).
MAGNITUDE = re.compile(r'(\d+)')

#: Rules sited at the `def` line, so the body blames through the span.
NODE_SPAN_RULES = frozenset({
    'god-functions', 'long-functions', 'deep-nesting', 'multi-return-fns',
    'bloated-docstrings', 'missing-docstrings', 'unsectioned-defs',
    'anchorless-citations'})

#: Rules reported at line 1 or file-wide: blamed only on crossing a threshold.
FILE_RULES = frozenset({'oversized-files', 'broken-syntax'})


# ---------------------------------------------------------------------------
# Scoring one file
# ---------------------------------------------------------------------------


def _parse(path: Path, text: str = None):
    """`(text, tree)` for one file; an unparsable file gets an empty tree."""
    if text is None:
        text = path.read_text(encoding='utf-8', errors='replace')
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        try:
            return text, ast.parse(text)
        except SyntaxError:
            return text, ast.Module(body=[], type_ignores=[])


def _syntax_sites(path: Path, text: str) -> list:
    """The one site for a file Python cannot parse; empty when it parses.

    Every other rule walks the tree, so an unparsable file scores zero and
    reads as clean -- the loudest possible pass for the worst possible edit.
    See: docs/reference/script_convert_architecture.md#what-the-gate-must-see
    """
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        try:
            ast.parse(text)
        except SyntaxError as exc:
            return [(path, exc.lineno or 1, 'file: %s' % (exc.msg or 'invalid'))]
    return []


def _missing(tool: str) -> bool:
    """True when `tool` is not importable, after saying how to install it."""
    try:
        subprocess.run([sys.executable, '-m', tool, '--version'],
                       capture_output=True, timeout=30, check=True)
        return False
    except Exception:
        print('  %s is NOT INSTALLED, so its rules did not run.' % tool,
              file=sys.stderr)
        print('  INSTALL: %s' % INSTALL, file=sys.stderr)
        return True


def _ruff_sites(path: Path) -> list:
    """Unused imports/variables and undefined names, from ruff."""
    try:
        got = subprocess.run(
            [sys.executable, '-m', 'ruff', 'check', '--select', RUFF_CODES,
             '--output-format', 'json', '--force-exclude', str(path)],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=60)
        rows = json.loads(got.stdout or '[]')
    except Exception:
        _missing('ruff')
        return []
    return [(path, r.get('location', {}).get('row', 1),
             '%s %s' % (r.get('code', '?'), r.get('message', '')))
            for r in rows]


def _headings(doc: Path) -> set:
    """Anchor targets in a markdown file: heading slugs and `<a id>` tags."""
    text = doc.read_text(encoding='utf-8', errors='replace')
    out = set(EXPLICIT_ANCHOR.findall(text))
    for line in text.split('\n'):
        if not line.startswith('#'):
            continue
        title = EXPLICIT_ANCHOR.sub('', line).lstrip('#').strip().lower()
        out.add(re.sub(r'[^a-z0-9\s-]', '', title).replace(' ', '-'))
    return out


def _citation_sites(path: Path, text: str) -> list:
    """`docs/` citations whose file or anchor does not exist.

    Resolved case-sensitively: on Windows `Path.exists()` ignores case, which
    is how a citation to a file that is really `Script_Conversion_Plan.md`
    survived unnoticed.
    """
    hits = []
    for num, line in enumerate(text.split('\n'), 1):
        if not REFERENCE.search(line):
            continue
        for ref in CITATION.findall(line):
            rel, _, anchor = ref.partition('#')
            target = ROOT / rel
            real = target.parent.exists() and target.name in os.listdir(
                target.parent) if target.parent.exists() else False
            if not real:
                hits.append((path, num, 'no such file: %s' % rel))
            elif anchor and anchor not in _headings(target):
                hits.append((path, num, 'no anchor #%s in %s' % (anchor, rel)))
    return hits


def _is_test(path: Path) -> bool:
    """True for a file under `tests/`, exempt from two rules.

    Its length is a COUNT of independent cases, not a responsibility that can
    be split; and a test may reach a private helper a caller may not.
    See: docs/reference/script_convert_architecture.md#why-oversized-files-does-not-judge-tests
    """
    try:
        parts = path.resolve().relative_to(ROOT).parts
    except ValueError:
        parts = path.parts
    return 'tests' in parts


def rule_sites(path: Path, text: str = None, with_tools: bool = True,
               tree=None) -> dict:
    """`{rule: [(path, line, detail), ...]}` for one file's repo-wide rules."""
    if tree is None:
        text, tree = _parse(path, text)
    checks = {
        'inline-comments': D.inline_comments(path, text, tree),
        'stray-comments': D.stray_comments(path, text, tree),
        'comment-blocks': D.comment_blocks(path, text, tree),
        'bloated-docstrings': D.bloated_docstrings(path, text, tree),
        'missing-docstrings': D.missing_docstrings(path, tree),
        'fat-attr-docs': D.fat_attr_docs(path, text),
        'fat-sections': D.fat_sections(path, text),
        'unsectioned-defs': D.unsectioned_defs(path, text, tree),
        'dead-citations': _citation_sites(path, text),
        'anchorless-citations': D.anchorless_citations(path, tree),
        'private-imports': D.private_imports(path, tree),
        'broken-syntax': _syntax_sites(path, text),
    }
    checks.update(D.structural_sites(path, text, tree))
    if _is_test(path):
        checks.pop('oversized-files', None)
        checks.pop('private-imports', None)
    if with_tools:
        checks['dead-imports'] = _ruff_sites(path)
    return {k: v for k, v in checks.items() if v}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _specific_hints(key: str, found: list) -> list:
    """Fixes for the CAUSES actually present, printed under the general remedy.

    One rule fires for causes with different cures -- a `
    paragraph of narration are both `stray-comments`, and "move it into a
    docstring" is wrong for the first.  Matching on the site detail lets the
    report name the real fix instead of the commonest one.
    """
    out = []
    for (rule, needle), hint in SPECIFIC.items():
        if rule != key or hint in out:
            continue
        if any(needle in str(site[2]) for site in found):
            out.append(hint)
    return out


def _report(path: Path, broken: dict, limit: int, headline: str) -> int:
    """Print each violation with what it means and its fix; 1 if any."""
    if not broken:
        return 0
    try:
        shown = path.relative_to(ROOT)
    except ValueError:
        shown = path
    total = sum(len(v) for v in broken.values())
    print('  %s in %s -- %d violation%s'
          % (headline, shown, total, '' if total == 1 else 's'),
          file=sys.stderr)
    for key, found in sorted(broken.items()):
        print('', file=sys.stderr)
        print('    %s (%d) -- %s' % (key, len(found), EXPLAIN.get(key, '')),
              file=sys.stderr)
        print('    FIX: %s' % REMEDY.get(key, ''), file=sys.stderr)
        for hint in _specific_hints(key, found):
            print('    ALSO: %s' % hint, file=sys.stderr)
        for _p, line, detail in sorted(found, key=lambda x: x[1])[:limit]:
            print('      %s:%d: %s' % (shown, line, detail), file=sys.stderr)
        if len(found) > limit:
            print('      ... %d more (same fix)' % (len(found) - limit),
                  file=sys.stderr)
    return 1


def gate_file(path: Path, limit: int = 10) -> int:
    """1 if `path` breaks any repo-wide rule, printing each site and its fix."""
    if not path.exists() or path.suffix != '.py':
        return 0
    return _report(path, rule_sites(path), limit, 'RULES BROKEN')


# ---------------------------------------------------------------------------
# Blaming an edit
# ---------------------------------------------------------------------------


def _git(path: Path, *args) -> tuple:
    """`(ok, stdout)` for a git command about `path`."""
    try:
        rel = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return False, ''
    try:
        got = subprocess.run(['git'] + [a.replace('{}', rel) for a in args],
                             cwd=ROOT, capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=30)
    except Exception:
        return False, ''
    return got.returncode == 0, got.stdout or ''


def _baseline_source(path: Path) -> str:
    """`path` as the INDEX holds it, else HEAD's copy.

    The index comes first so that work someone else STAGED is baseline, not
    the edit's own: git bills a staged change to whoever asks next.
    """
    for spec in (':{}', 'HEAD:{}'):
        ok, text = _git(path, 'show', spec)
        if ok:
            return text
    return ''


def _renames() -> dict:
    """`{new path: donor path}` for renames seen before this file was staged."""
    try:
        return json.loads(RENAME_MAP.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _donor(path: Path, text: str, read: Path = None) -> Path:
    """The file `text` is an EXACT copy of AS IT NOW STANDS, recording it.

    Working-tree content, not the baseline, so a file edited and THEN moved
    still maps.  `read` is the candidate's own copy, which must never be its
    own donor.
    See: docs/reference/script_convert_architecture.md#a-rename-is-not-an-edit
    """
    try:
        key = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return None
    seen = _renames()
    if key in seen:
        return ROOT / seen[key]
    size = len(text.encode('utf-8', 'replace'))
    for other in repo_files():
        if other in (path, read) or not size <= other.stat().st_size <= 2 * size:
            continue
        if other.read_text(encoding='utf-8', errors='replace') == text:
            seen[key] = other.resolve().relative_to(ROOT).as_posix()
            RENAME_MAP.parent.mkdir(parents=True, exist_ok=True)
            RENAME_MAP.write_text(json.dumps(seen, indent=1), encoding='utf-8')
            return other
    return None


def _hunk_lines(out: str) -> set:
    """The `+` line numbers named by every hunk header in a unified diff."""
    touched = set()
    for hunk in re.finditer(r'^@@ -\S+ \+(\d+)(?:,(\d+))? @@', out,
                            re.MULTILINE):
        start = int(hunk.group(1))
        touched.update(range(start, start + int(hunk.group(2) or 1)))
    return touched


def _moved_lines(donor: Path, text: str) -> set:
    """Lines a MOVE added to `donor`'s current content, normally none.

    Diffed against the donor AS IT STANDS, not its baseline: an in-flight edit
    travels with the file, so comparing to the committed copy would bill the
    mover for changes the donor already carried.
    See: docs/reference/script_convert_architecture.md#a-rename-is-not-an-edit
    """
    import difflib
    now = donor.read_text(encoding='utf-8', errors='replace')
    return _hunk_lines('\n'.join(difflib.unified_diff(
        now.splitlines(), text.splitlines(), n=0, lineterm='')))


def _touched_lines(path: Path, candidate: str = None):
    """Line numbers this edit ADDED or CHANGED; None if git knows nothing.

    Diffed against the INDEX, never `HEAD`: `git diff HEAD` folds in staged
    changes, which billed one edit for every violation in 10 files it had
    never opened.  `candidate` is text not yet on disk, diffed in memory so an
    edit is scored BEFORE it is applied.
    """
    import difflib
    tracked, _ = _git(path, 'ls-files', '--error-unmatch', '{}')
    if not tracked:
        return None
    if candidate is not None:
        return _hunk_lines('\n'.join(difflib.unified_diff(
            _baseline_source(path).splitlines(),
            candidate.splitlines(), n=0, lineterm='')))
    ok, out = _git(path, 'diff', '-U0', '--', '{}')
    return _hunk_lines(out) if ok else None


def edit_region(touched: set, text: str) -> set:
    """Changed code lines PLUS the comment run directly above each.

    A comment above a line you edit is yours: blaming only the exact changed
    line absolved every comment sitting above it, which is most of them.
    """
    lines = text.split('\n')
    region = set(touched)
    for line in touched:
        i = line - 1
        while i >= 1 and i <= len(lines):
            raw = lines[i - 1].strip()
            if not raw or raw.startswith('#'):
                region.add(i)
                i -= 1
                continue
            break
    return region


def _worsened(found: list, before: list) -> list:
    """Sites whose MEASURE rose, not merely whose count rose.

    Keyed on the detail before its colon, compared on the first number after.
    Every detail MUST name its site there: a detail that is only a number keys
    on the number, so shrinking mints a new key and scores as worsening.
    """
    old = {}
    for site in before:
        name = str(site[2]).split(':')[0]
        dug = MAGNITUDE.search(str(site[2]))
        old[name] = int(dug.group(1)) if dug else 0
    worse = []
    for site in found:
        name = str(site[2]).split(':')[0]
        dug = MAGNITUDE.search(str(site[2]))
        now_val = int(dug.group(1)) if dug else 0
        if name not in old or now_val > old[name]:
            worse.append(site)
    return worse


def _blame(now: dict, was: dict, touched, text: str, tree) -> dict:
    """Violations this edit owns, by rule family.

    Prose rules blame through `edit_region`, with no file-wide guard: a legacy
    comment above an edited line does not raise the file's count, so a guard
    would absolve exactly the case this exists to catch.  Shape rules keep the
    guard, so a one-line edit never inherits a long function's debt.
    """
    if touched is None:
        return now
    region = edit_region(touched, text)
    spans = D.function_spans(tree)
    owned = {}
    for rule, found in now.items():
        before = was.get(rule, ())
        if rule in FILE_RULES:
            mine = _worsened(found, before)
        elif rule in NODE_SPAN_RULES:
            mine = [s for s in _worsened(found, before)
                    if any(a <= s[1] <= b and touched & set(range(a, b + 1))
                           for a, b in spans)]
        else:
            mine = [s for s in found if s[1] in region]
        if mine:
            owned[rule] = mine
    return owned


def _unpaired(sites: dict) -> dict:
    """`sites` without PAIRED_CODES and PAIRED_RULES, judged at turn end."""
    kept = [s for s in sites.get('dead-imports', ())
            if str(s[2]).split()[0] not in PAIRED_CODES]
    out = dict(sites, **{'dead-imports': kept})
    return {k: v for k, v in out.items() if v and k not in PAIRED_RULES}


def gate_diff(path: Path, limit: int = 10, source: Path = None,
              defer: bool = False) -> int:
    """1 when the agent's own edit owns a violation; else 0.

    `source` holds the CANDIDATE text while `path` names the file it would
    become: the hook scores an edit before applying it, and git must still
    scope the blame to the real file's diff.  `defer` leaves out the
    PAIRED_CODES.
    See: docs/reference/script_convert_architecture.md#what-the-gate-must-see
    """
    read = source or path
    if not read.exists() or read.suffix != '.py':
        return 0
    text, tree = _parse(read)
    moved = None if _baseline_source(path) else _donor(path, text, read)
    origin = moved or path
    touched = (_moved_lines(moved, text) if moved else
               _touched_lines(path, text if source is not None else None))
    if touched == set():
        return 0
    now = rule_sites(read, text, tree=tree)
    if defer:
        now = _unpaired(now)
    before = _baseline_source(origin)
    was = rule_sites(path, before, with_tools=False) if before else {}
    headline = ('NEW FILE -- all of it is yours' if touched is None
                else 'YOUR EDIT BROKE RULES')
    return _report(path, _blame(now, was, touched, text, tree), limit,
                   headline)


# ---------------------------------------------------------------------------
# Repo sweeps
# ---------------------------------------------------------------------------


def repo_files() -> list:
    """Every first-party `.py` file the rules judge.

    Prunes as it walks: `rglob` descends into `output/` and `references/`
    to find 2,752 files and then discards 83% of them, which cost 5.9s of
    every run.  See: docs/commentary/performance.md
    """
    found = []
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_PARTS]
        found += [Path(base) / n for n in names if n.endswith('.py')]
    return sorted(found)


def dead_code(limit: int = 40) -> int:
    """Report whole-program dead code; 1 if any.  Needs the WHOLE repo."""
    try:
        got = subprocess.run(
            [sys.executable, '-m', 'vulture', '.',
             '--min-confidence', VULTURE_CONFIDENCE,
             '--ignore-decorators', VULTURE_IGNORE_DECORATORS,
             '--exclude', ','.join(SKIP_PARTS)],
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=300)
    except Exception as exc:
        if not _missing('vulture'):
            print('  vulture did not run: %s' % exc, file=sys.stderr)
        return 0
    found = [l for l in got.stdout.split('\n') if l.strip()]
    if not found:
        print('  no dead code at %s%% confidence' % VULTURE_CONFIDENCE)
        return 0
    print('  DEAD CODE (%d) -- %s' % (len(found), EXPLAIN['dead-code']),
          file=sys.stderr)
    print('  FIX: %s' % REMEDY['dead-code'], file=sys.stderr)
    for line in found[:limit]:
        print('    %s' % line, file=sys.stderr)
    if len(found) > limit:
        print('    ... %d more' % (len(found) - limit), file=sys.stderr)
    return 1


def sweep(limit: int) -> int:
    """Gate every first-party file; 1 if any breaks a rule."""
    worst = 0
    for path in repo_files():
        worst = max(worst, gate_file(path, limit))
    return worst


def _legend() -> int:
    """Print every rule, what it means, and how to fix it."""
    for key in sorted(EXPLAIN):
        print('  %-20s %s' % (key, EXPLAIN[key]))
        print('  %-20s FIX: %s' % ('', REMEDY.get(key, '')))
    return 0


def _parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(
        description='Enforce the repo-wide code rules on one file or the tree.')
    parser.add_argument('--gate-file', metavar='PATH', action='append',
                        help='score ONE file absolutely; exit 1 on any break')
    parser.add_argument('--gate-diff', metavar='PATH', action='append',
                        help='score only what THIS edit owns (the hook path)')
    parser.add_argument('--candidate', metavar='PATH',
                        help='read the text from here, blame --gate-diff PATH')
    parser.add_argument('--defer-paired', action='store_true',
                        help='skip unused imports, undefined names and '
                             'dead citations')
    parser.add_argument('--sweep', action='store_true',
                        help='gate every first-party file')
    parser.add_argument('--dead-code', action='store_true',
                        help='whole-program dead code (vulture, 100%%)')
    parser.add_argument('--legend', action='store_true',
                        help='every rule, what it means, and its fix')
    parser.add_argument('--limit', type=int, default=10,
                        help='max sites printed per rule')
    return parser


def main(argv=None) -> int:
    """Parse arguments and run the requested gate."""
    try:
        sys.stdout.reconfigure(errors='replace')
        sys.stderr.reconfigure(errors='replace')
    except Exception:
        pass
    args = _parser().parse_args(argv)
    if args.legend:
        return _legend()
    if args.dead_code:
        return dead_code(args.limit)
    if args.sweep:
        return sweep(args.limit)
    if args.gate_file:
        return max(gate_file(Path(n).resolve(), args.limit)
                   for n in args.gate_file)
    if args.gate_diff:
        cand = Path(args.candidate).resolve() if args.candidate else None
        return max(gate_diff(Path(n).resolve(), args.limit, cand,
                             args.defer_paired)
                   for n in args.gate_diff)
    _parser().print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())

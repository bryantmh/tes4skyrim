#!/usr/bin/env python3
"""Fitness functions for `script_convert/` -- the architecture, as numbers.

`script_convert/` is being rewritten from a line-by-line regex rewriter into an
AST compiler (docs/reference/script_convert_architecture.md).  A refactor that large
cannot be policed by review: the failure mode of the last attempt was that
every named helper was DELETED from its old file and RECREATED in a new one,
which reads as progress in a diff and is none.

So the architecture is measured instead.  Each metric below is one number with
a target; a change is scored before and after, and "did that help?" is answered
by arithmetic rather than judgement.

    python tools/script/arch_fitness.py                        # the table
    python tools/script/arch_fitness.py --legend               # what each means
    python tools/script/arch_fitness.py --why stray-comments   # the file:lines
    python tools/script/arch_fitness.py --fail-on-regression   # the edit loop
    python tools/script/arch_fitness.py --update-baseline      # after a stage

`--fail-on-regression` compares against `tools/script/script_convert_fitness.json` and
exits non-zero ONLY when a metric moved the wrong way.  It never blocks
progress toward a target, which is what keeps it from being switched off.

Every metric is NAMED, not numbered.  `F10: 32` needs a legend to act on and
reads as noise in a report; `satellite-cmd-sets: 32` says what to go fix.  The
`group` column ties one back to the anti-pattern it scores (AP1-AP8) or marks
it `doc` for the documentation rules.

Three families:

  AP1-AP8  one per anti-pattern in the architecture doc -- the ABSENCE of a
           known disease.
  (blank)  simple / organised / deduplicated / maintainable / fast -- the GOAL,
           so the package cannot rot in a way the original audit missed.
  doc      the documentation rules: docstrings carry the prose, comments do not.

🛑 EVERY RULE IS PACKAGE-SCOPED.  The doc rules once ran repo-wide, which made
them dead weight here: `stray-comments` read 26,952 with only 2,857 of them in
`script_convert/`, so no amount of work on this package could move the number.

This is a FITNESS FUNCTION, not the correctness gate.  It says the code is
SHAPED right; only `psc_semantic_diff.py` says it still DOES the same thing,
and only that tool protects the play-tested CharacterGen scripts.  Both are
required and neither substitutes for the other.

🛑 THE SUITE IS BUILD-FREE AND MUST STAY UNDER A SECOND.  A conversion-fidelity
metric (counting `;NE:` markers over generated output) was prototyped and
deliberately left out: it needs a build to exist and takes ~2.8s to scan 40,586
scripts.  That job belongs to `psc_semantic_diff.py`, which runs on the same
artifact.  Sampling to make it cheap was tested and rejected too -- 500/1500/
3000-script samples gave 0.184/0.125/0.154 markers per script, noise that
swamps any real regression.

🛑 EVERY STATIC METRIC SHARES ONE AST PARSE PER FILE.  Parsing per-metric is
the difference between 0.25s and several seconds, and a slow fitness function
stops being run.
"""

import argparse
import ast
import json
import re
import statistics
import subprocess
import sys
import time
import warnings
from pathlib import Path

from tools.validate import code_rules as CR
from tools.validate import code_rules_ast as D

ROOT = Path(__file__).resolve().parent.parent.parent

code_lines = D.code_lines
_complexity = D.complexity
_depth = D.depth

PKG = ROOT / 'script_convert'
BASELINE = Path(__file__).resolve().parent / 'script_convert_fitness.json'

#: Scratch and vendored trees, excluded from every rule.
REPO_SKIP = ('temp', 'references', 'external', 'output', 'export', 'build')

#: 'le': lower is better.  'ge': higher is better.
LOWER, HIGHER = 'le', 'ge'

#: key, target, direction, group -- in printed order; the key IS the name.
METRICS = [
    ('text-repair-fns', 0, LOWER, 'AP2'),
    ('god-functions', 0, LOWER, ''),
    ('branch-points', 1500, LOWER, ''),
    ('private-reach-ins', 0, LOWER, 'AP4'),
    ('code-lines', 6500, LOWER, ''),
    ('oversized-files', 0, LOWER, ''),
    ('reparse-round-trip', 0, LOWER, 'AP1'),
    ('source-rescans', 0, LOWER, 'AP3'),
    ('mutable-class-state', 0, LOWER, 'AP4'),
    ('stray-constants', 10, LOWER, 'AP5'),
    ('satellite-cmd-sets', 0, LOWER, 'AP6'),
    ('logic-in-constants', 0, LOWER, 'AP7'),
    ('psc-readback', 0, LOWER, 'AP8'),
    ('duplicate-literals', 0, LOWER, ''),
    ('long-functions', 0, LOWER, ''),
    ('multi-return-fns', 0, LOWER, ''),
    ('deep-nesting', 0, LOWER, ''),
    ('local-imports', 10, LOWER, ''),
    ('regex-ops', 25, LOWER, ''),
    ('ms-per-script', 0, LOWER, ''),
    ('duck-typed-nodes', 0, LOWER, ''),
    ('return-annotations', 95, HIGHER, ''),
    ('comment-blocks', 0, LOWER, 'doc'),
    ('inline-comments', 0, LOWER, 'doc'),
    ('unsectioned-defs', 0, LOWER, 'doc'),
    ('fat-sections', 0, LOWER, 'doc'),
    ('missing-docstrings', 0, LOWER, 'doc'),
    ('bloated-docstrings', 0, LOWER, 'doc'),
    ('fat-attr-docs', 0, LOWER, 'doc'),
    ('stray-comments', 0, LOWER, 'doc'),
    ('dead-imports', 0, LOWER, 'doc'),
    ('dead-citations', 0, LOWER, 'doc'),
]

#: One line each, printed by `--legend`; shared rules take `code_rules`' text.
EXPLAIN = {
    **CR.EXPLAIN,
    'text-repair-fns': 'takes emitted Papyrus back as input (fix the NODE)',
    'branch-points': 'total branches; splitting a function cannot move it',
    'private-reach-ins': 'emit/commands/assemble touching conv._ / ctx._',
    'code-lines': 'package implementation lines (docstrings excluded)',
    'reparse-round-trip': 'a node flattened to TES4 text and re-parsed',
    'source-rescans': 'feature flags regexed from raw source after parse',
    'stray-constants': 'data tables living outside constants.py',
    'satellite-cmd-sets': 'per-command flag sets shadowing COMMAND_ROWS',
    'logic-in-constants': 'constants.py taking a graph, or reading at import',
    'psc-readback': 'a symbol table rebuilt by grepping generated .psc',
    'duplicate-literals': 'redundant copies of one string-literal collection',
    'local-imports': 'function-local imports (they hide layering breaks)',
    'regex-ops': 'the string-era footprint in one number',
    'ms-per-script': 'median ms on the frozen fixture, vs baseline',
    'duck-typed-nodes': 'getattr on an AST node instead of a real field',
    'return-annotations': 'pct of public functions with one',
}

#: The doc-rule keys; scoped to the package, they read 2,857 / 1,617 / 57.
DOC_METRICS = frozenset(k for k, _t, _d, g in METRICS if g == 'doc')

#: Layer-scoped: unscoped this wrongly flags the parser and the classifier.
TEXT_REPAIR_SCOPE = frozenset({'converter.py', 'emit/expr.py', 'emit/stmt.py',
                      'emit/commands.py'})
TEXT_REPAIR_PARAMS = frozenset({'lines', 'line', 'text', 'emitted', 'psc'})
#: Exempt: `emit_string` takes a TES4 LITERAL and `_number` a source spelling.
TEXT_REPAIR_ALLOW = frozenset({
    ('converter.py', 'emit_string'),
    ('emit/expr.py', '_number'),
})

#: The round trip: a node flattened to TES4 text and RE-PARSED.
ROUND_TRIP = re.compile(
    r'_convert_expression\(|_tree_expression|USE_TREE_EXPRESSIONS'
    r'|emit_source\([^)]*\)\s*(?:,\s*extends)?\s*\)\s*$'
    r'|parse\(\s*emit_source|tokenize\(\s*emit_source')

#: A feature flag scanned from raw source after the tree exists.
RAW_SCAN = re.compile(r'in source_low'
                      r'|re\.(?:search|match)\([^\n]*source_low'
                      r'|source\.lower\(\)')

#: Reading back generated .psc -- a symbol table by grep.
PSC_READ = re.compile(r"\.psc['\"]|\*\.psc")

#: The whole string-era footprint in one number.
REGEX_OP = re.compile(r're\.(?:compile|search|match|sub|findall|fullmatch)\(')

#: getattr on an AST node: the node contract worked around, not used.
DUCK_GETATTR = re.compile(r'getattr\(\s*(?:st|node|n|stmt|expr)\b[^)]*\)')

BRANCH_NODES = (ast.If, ast.For, ast.While, ast.And, ast.Or,
                ast.ExceptHandler, ast.Assert, ast.comprehension)

NEST_NODES = (ast.If, ast.For, ast.While, ast.With, ast.Try)

#: A set built by this call is DERIVED from the rows, not authored beside them.
FLAGGED = '_flagged'

#: The command universe, not per-command flags.
SET_UNIVERSE = frozenset({'KNOWN_COMMANDS', 'HANDLED_COMMANDS',
                          '_PAPYRUS_RESERVED'})
CONST_NAME = re.compile(r'^_?[A-Z][A-Z0-9_]*$')


#: FROZEN: a drifting fixture measures nothing (9 lines = 0.282ms, 14 = 0.459).
FIXTURE = '\n'.join([
    'scn T', 'short doonce', 'ref target', 'float timer',
    'begin GameMode',
    ' if doonce == 0',
    '  set doonce to 1',
    '  player.additem Gold001 100',
    '  set target to getself',
    '  if target.getdistance player < 500',
    '   target.setalert 1',
    '  endif',
    ' endif',
    'end',
])
FIXTURE_RUNS, FIXTURE_ITERS = 5, 40
#: Measured run-to-run spread is ~1.10x, so this sits well outside noise.
SPEED_TOLERANCE = 1.5


def _satellite_sets(trees, pkg: Path) -> list:
    """`(path, line, detail)` per name set authored apart from its row.

    Read from SOURCE, not runtime: a derived frozenset is indistinguishable
    from a typed-out one.  Skips a set built by `_flagged`, and one whose names
    are mostly not in `COMMAND_ROWS`.
    """
    tree = trees.get(pkg / 'constants.py')
    if tree is None:
        return []
    try:
        from script_convert import constants as C
        commands = set(C.COMMAND_ROWS)
    except Exception:
        return []
    hits = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        name = getattr(node.targets[0], 'id', '')
        if not CONST_NAME.match(name) or name in SET_UNIVERSE:
            continue
        if any(isinstance(x, ast.Call) and getattr(x.func, 'id', '') == FLAGGED
               for x in ast.walk(node.value)):
            continue
        literal = node.value
        if (isinstance(literal, ast.Call)
                and getattr(literal.func, 'id', '') == 'frozenset'
                and literal.args):
            literal = literal.args[0]
        if not isinstance(literal, ast.Set):
            continue
        if not 3 < len(literal.elts) < 300:
            continue
        members = [e.value.lower() for e in literal.elts
                   if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        named = sum(1 for m in members if m in commands)
        if named * 2 <= len(members):
            continue
        hits.append((pkg / 'constants.py', node.lineno,
                     '%s: %d of %d names have a row'
                     % (name, named, len(members))))
    return hits


def _constants_logic(trees, pkg: Path) -> int:
    """Functions taking a graph, plus import-time file reads, in constants.py.

    Both are the same defect: `constants.py` is meant to be DATA, so a function
    taking an `xref` is conversion logic that has drifted into it, and a read
    at module scope makes importing the data table do 309 KB of disk I/O.
    Only reads at MODULE scope count -- one inside a lazily-called function is
    the fix, not the defect.
    """
    tree = trees.get(pkg / 'constants.py')
    if tree is None:
        return 0
    logic = sum(1 for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                and {a.arg for a in n.args.args} & {'xref', 'conv', 'ctx'})
    fn_spans = [(n.lineno, n.end_lineno) for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    io = sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == 'open'
             and not any(a <= n.lineno <= b for a, b in fn_spans))
    return logic + io


def _duplicate_literals(files, trees) -> list:
    """Redundant COPIES of the same string-literal collection.

    `('self','myself','getself')` appears five times and `('ACHR','ACRE',
    'REFR')` four; each extra copy is one more place to forget when the set
    changes.  Counts copies rather than groups, so the number is the work left.
    """
    seen = {}
    for path in files:
        for node in ast.walk(trees[path]):
            if not isinstance(node, (ast.Tuple, ast.Set, ast.List)):
                continue
            if len(node.elts) < 3:
                continue
            if all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                   for e in node.elts):
                key = tuple(sorted(e.value for e in node.elts))
                seen.setdefault(key, []).append((path, node.lineno))
    hits = []
    for key, spots in seen.items():
        for path, line in spots[1:]:
            hits.append((path, line, '%d copies of %s'
                         % (len(spots), str(key)[:52])))
    return hits


#: Metric key -> [(path, line, detail)] from the last measure(); `--why` reads it.
SITES = {}

_FN_CACHE = {}


def _functions(trees) -> list:
    """(path, FunctionDef) for every function, walked ONCE and memoised.

    Six metrics each ran their own `ast.walk` over the repo, 3.5s of a 5.7s
    total; a fitness function that slow stops being run.  Keyed on the tree
    dict's identity, so the package slice and the repo set stay distinct.
    """
    key = id(trees)
    if key not in _FN_CACHE:
        _FN_CACHE[key] = [
            (path, node) for path, tree in trees.items()
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return _FN_CACHE[key]


def _speed() -> float:
    """Median ms to convert the frozen fixture.

    Deliberately NOT a corpus run: converting anything real takes seconds and
    the suite would stop being run on every edit.  One script, no plugin, no
    export index, no I/O.  An import failure raises, never scores 0.0.
    """
    from script_convert.converter import ScriptConverter
    from script_convert.cross_ref import CrossRefGraph
    conv = ScriptConverter(CrossRefGraph())
    runs = []
    for _ in range(FIXTURE_RUNS):
        start = time.perf_counter()
        for i in range(FIXTURE_ITERS):
            conv.convert_standalone('T%d' % i, FIXTURE, 'ObjectReference',
                                    'T%d' % i)
        runs.append((time.perf_counter() - start) / FIXTURE_ITERS * 1000)
    return round(statistics.median(runs), 3)


def _package_files(pkg: Path) -> list:
    """Every .py file in the package under measurement."""
    return sorted(f for f in pkg.rglob('*.py')
                  if not any(part in REPO_SKIP for part in f.parts))


def _changed_files(pkg: Path) -> list:
    """Package files this working tree has touched vs the MERGE BASE.

    A rule broken in a file nobody edited is inherited debt, and scoring it
    hides whether THIS change helped.  Merge base, not HEAD, so a file
    rewritten earlier on the branch still counts as this rewrite's work.
    """
    base = subprocess.run(['git', 'merge-base', 'HEAD', 'master'],
                          cwd=ROOT, capture_output=True, text=True)
    ref = base.stdout.strip() or 'HEAD'
    out = subprocess.run(['git', 'diff', '--name-only', ref, '--', str(pkg)],
                         cwd=ROOT, capture_output=True, text=True)
    names = [n for n in out.stdout.split(chr(10)) if n.endswith('.py')]
    return sorted({ROOT / n for n in names if (ROOT / n).is_file()})


def _parse_all(paths) -> tuple:
    """(texts, trees) for a list of files, parsed once each.

    Warnings are suppressed rather than printed: several first-party files
    carry invalid escape sequences, and `ast.parse` reports each one on stdout,
    which corrupts `--json` for any caller trying to read the numbers.
    """
    texts = {p: p.read_text(encoding='utf-8', errors='replace') for p in paths}
    trees = {}
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        for path, source in texts.items():
            try:
                trees[path] = ast.parse(source)
            except SyntaxError:
                print('  !! %s does not parse' % path, file=sys.stderr)
                trees[path] = ast.Module(body=[], type_ignores=[])
    return texts, trees


def _pattern_counts(files, texts) -> dict:
    """The regex-based metrics: one findall per pattern per file."""
    return {key: sum(len(rx.findall(texts[p])) for p in files)
            for key, rx in (('reparse-round-trip', ROUND_TRIP),
                            ('source-rescans', RAW_SCAN),
                            ('psc-readback', PSC_READ),
                            ('regex-ops', REGEX_OP),
                            ('duck-typed-nodes', DUCK_GETATTR))}


def _size_counts(files, texts, trees, fns) -> dict:
    """The size and shape metrics, over the shared function walk."""
    return {
        'branch-points': sum(_complexity(fn) - 1 for _, fn in fns),
        'code-lines': sum(code_lines(texts[p], trees[p]) for p in files),
        'local-imports': sum(
            1 for p in files for n in ast.walk(trees[p])
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            for x in ast.walk(n)
            if isinstance(x, (ast.Import, ast.ImportFrom))),
    }


def _package_only(files, texts, trees, rel, fns, pkg) -> dict:
    """Metrics that mean something only inside `script_convert/`."""
    inner = ('emit/', 'commands/', 'assemble/')
    return {
        'text-repair-fns': sum(
            1 for path, fn in fns
            if rel[path] in TEXT_REPAIR_SCOPE
            and ({a.arg for a in fn.args.args}
                 | {a.arg for a in fn.args.kwonlyargs}) & TEXT_REPAIR_PARAMS
            and (rel[path], fn.name) not in TEXT_REPAIR_ALLOW),
        'private-reach-ins': sum(
            texts[p].count('conv._') + texts[p].count('ctx._')
            for p in files if rel[p].startswith(inner)),
        'stray-constants': sum(
            1 for p in files if rel[p] != 'constants.py'
            for n in ast.walk(trees[p]) if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Name) and t.id.lstrip('_').isupper()
            and isinstance(n.value, (ast.Dict, ast.Set, ast.List, ast.Tuple))),
        'logic-in-constants': _constants_logic(trees, pkg),
    }


def _all_sites(files, texts, trees, pkg) -> dict:
    """Every metric that can name its own `(path, line, detail)`.

    The gate and the table read the SAME functions, so a rule cannot be
    enforced on one file under a definition the score does not share.
    """
    sites = {key: [] for key in CR.EXPLAIN}
    for path in files:
        for key, found in CR.rule_sites(path, texts[path], with_tools=False,
                                        tree=trees[path]).items():
            sites.setdefault(key, []).extend(found)
    sites.update({
        'satellite-cmd-sets': _satellite_sets(trees, pkg),
        'duplicate-literals': _duplicate_literals(files, trees),
    })
    return sites


def measure(pkg: Path = PKG, with_speed: bool = True,
            scope: list = None) -> dict:
    """Every metric for one package, from ONE parse per file."""
    files = scope if scope is not None else _package_files(pkg)
    texts, trees = _parse_all(files)
    rel = {p: p.relative_to(pkg).as_posix() for p in files}
    fns = [(p, n) for p in files for n in ast.walk(trees[p])
           if isinstance(n, ast.FunctionDef)]

    out = {}
    out.update(_pattern_counts(files, texts))
    out.update(_size_counts(files, texts, trees, fns))
    out.update(_package_only(files, texts, trees, rel, fns, pkg))

    sites = _all_sites(files, texts, trees, pkg)
    SITES.clear()
    SITES.update(sites)
    out.update({key: len(found) for key, found in sites.items()})

    public = [(p, fn) for p, fn in fns if not fn.name.startswith('_')]
    out['return-annotations'] = round(
        100 * sum(1 for _, fn in public if fn.returns is not None)
        / max(len(public), 1))
    out['ms-per-script'] = _speed() if with_speed else 0.0
    return out


def _print_sites(keys: str, limit: int) -> int:
    """Print `file:line: detail` for each requested metric."""
    wanted = [k.strip().lower() for k in keys.split(',') if k.strip()]
    missing = [k for k in wanted if k not in SITES]
    for key in wanted:
        found = SITES.get(key)
        if found is None:
            print('  %s: no per-site locator (counts only)' % key,
                  file=sys.stderr)
            continue
        print()
        print('  %s -- %d site%s'
              % (key, len(found), '' if len(found) == 1 else 's'))
        for path, line, detail in found[:limit]:
            try:
                shown = Path(path).relative_to(ROOT)
            except ValueError:
                shown = path
            print('    %s:%s: %s' % (shown, line, detail))
        if len(found) > limit:
            print('    ... %d more (raise --limit)' % (len(found) - limit))
    print()
    return 1 if missing else 0


def regressions(now: dict, base: dict) -> list:
    """Metrics that moved the WRONG way.

    Movement TOWARD a target is never reported: a fitness function that blocks
    partial progress gets switched off halfway through a stage.  Speed is judged
    relative to the baseline, never absolutely, so a slower MACHINE cannot fail
    the gate.
    """
    out = []
    for key, _target, direction, _group in METRICS:
        if key not in base or key not in now:
            continue
        was, is_ = base[key], now[key]
        if key == 'ms-per-script':
            if was and is_ > was * SPEED_TOLERANCE:
                out.append('%s: %s -> %s ms (>%sx slower)'
                           % (key, was, is_, SPEED_TOLERANCE))
        elif (is_ > was) if direction == LOWER else (is_ < was):
            out.append('%s: %s -> %s (worse)' % (key, was, is_))
    return out


def _print_table(now: dict, base: dict, elapsed: float) -> None:
    """Render the metric table, marking each against its target."""
    print('\n  script_convert/ fitness      (%.2fs)\n' % elapsed)
    for key, target, direction, group in METRICS:
        value = now[key]
        if key == 'ms-per-script':
            limit = base.get(key, 0) * SPEED_TOLERANCE
            mark = '   ' if not base.get(key) else (
                ' ok' if value <= limit else ' !!')
            goal = '<=%.3f' % limit if base.get(key) else 'baseline'
        else:
            ok = value <= target if direction == LOWER else value >= target
            mark = ' ok' if ok else ' !!'
            goal = '%s%s' % ('<=' if direction == LOWER else '>=', target)
        delta = ''
        if key in base and base[key] != value:
            delta = '  (%g -> %g)' % (base[key], value)
        print('  %s %-20s %8s  %-10s %-4s%s'
              % (mark, key, value, goal, group, delta))
    print()


def _print_legend() -> int:
    """One line per metric: what the name means and why it is scored."""
    print()
    for key, target, direction, group in METRICS:
        goal = '%s%s' % ('<=' if direction == LOWER else '>=', target)
        print('  %-20s %-8s %-4s %s'
              % (key, goal, group, EXPLAIN.get(key, '')))
    print()
    return 0


def _parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(
        description='Score script_convert/ against its architecture targets.')
    parser.add_argument('--json', action='store_true', help='machine-readable')
    parser.add_argument('--fail-on-regression', action='store_true',
                        help='exit 1 when a metric moved away from its target')
    parser.add_argument('--update-baseline', action='store_true',
                        help='write current values to the baseline file')
    parser.add_argument('--accept-regression', action='store_true',
                        help='allow --update-baseline to record a '
                             'metric that moved AWAY from its target')
    parser.add_argument('--baseline', default=str(BASELINE))
    parser.add_argument('--no-speed', action='store_true',
                        help='skip ms-per-script (avoids importing the pkg)')
    parser.add_argument('--why', metavar='METRIC',
                        help='list the offending file:line for one metric '
                             '(e.g. --why stray-comments); comma-separated')
    parser.add_argument('--legend', action='store_true',
                        help='explain every metric name, one line each')
    parser.add_argument('--gate-file', metavar='PATH', action='append',
                        help='score ONE file against the repo-wide rules and '
                             'exit non-zero if any is broken; repeatable. The '
                             'enforcement half -- absolute, not relative to a '
                             'baseline, so it cannot be satisfied by an '
                             'unrelated improvement elsewhere')
    parser.add_argument('--gate-diff', metavar='PATH', action='append',
                        help='score only the LINES this branch changed in one '
                             'file, against the same file at HEAD; repeatable. '
                             'What the post-edit hook runs -- inherited debt in '
                             'a legacy file is not the edit\'s to pay')
    parser.add_argument('--changed', action='store_true',
                        help='score only the package files this branch has '
                             'touched, so inherited debt is not counted')
    parser.add_argument('--limit', type=int, default=40,
                        help='max sites to print per --why metric')
    return parser


def _write_baseline(path: Path, now: dict, base: dict, accept: bool) -> int:
    """Record `now` as the baseline, refusing to bake in a regression."""
    bad = regressions(now, base) if base else []
    if bad and not accept:
        print('  REFUSING to bake in a regression:', file=sys.stderr)
        for line in bad:
            print('    %s' % line, file=sys.stderr)
        print('  fix it, or re-run with --accept-regression.', file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'metrics': now}, indent=2, sort_keys=True)
                    + chr(10), encoding='utf-8')
    print('  baseline written: %s' % path)
    return 0


def _check_regressions(now: dict, base: dict) -> int:
    """1 if any metric moved away from its target since the baseline."""
    if not base:
        print('  no baseline; run --update-baseline first', file=sys.stderr)
        return 1
    bad = regressions(now, base)
    if not bad:
        print('  no regressions')
        return 0
    print('  REGRESSED:', file=sys.stderr)
    for line in bad:
        print('    %s' % line, file=sys.stderr)
    return 1


def main(argv=None) -> int:
    """Parse arguments, measure, and report or gate."""
    try:
        sys.stdout.reconfigure(errors='replace')
        sys.stderr.reconfigure(errors='replace')
    except Exception:
        pass
    args = _parser().parse_args(argv)
    if args.legend:
        return _print_legend()
    start = time.perf_counter()
    scope = _changed_files(PKG) if args.changed else None
    if args.changed and not scope:
        print('  no changed package files')
        return 0
    now = measure(with_speed=not args.no_speed, scope=scope)
    elapsed = time.perf_counter() - start

    baseline_path = Path(args.baseline)
    base = (json.loads(baseline_path.read_text(encoding='utf-8'))
            .get('metrics', {})) if baseline_path.exists() else {}

    if args.why:
        return _print_sites(args.why, args.limit)
    if args.json:
        print(json.dumps({'metrics': now, 'elapsed': round(elapsed, 3)},
                         indent=2, sort_keys=True))
    else:
        _print_table(now, base, elapsed)

    if args.update_baseline:
        return _write_baseline(baseline_path, now, base,
                               args.accept_regression)
    if args.fail_on_regression:
        return _check_regressions(now, base)
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Detectors for the repo-wide code rules: one function per rule, no I/O.

Every detector returns `[(path, line, detail), ...]` so the gate and any
score table read the SAME definition of a rule.  No CLI, no git, no
subprocess: `code_rules.py` owns those.

See: docs/reference/script_convert_architecture.md
"""

import ast
import io
import re
import tokenize

#: Roughly seven lines of prose -- a contract, not a narrative.
MAX_DOC_CHARS = 480

#: A one- or two-line body gets ONE line of docstring, nothing more.
TINY_BODY_LINES = 2
TINY_DOC_CHARS = 80

#: A `#:` doc is ONE line: it labels a declaration, it does not argue.
MAX_ATTR_DOC_CHARS = 120

#: STATEMENTS, not lines, so reflow cannot move it. p90 of 7,791 fns is 30.
MAX_FUNCTION_LINES = 35

MAX_COMPLEXITY = 25
MAX_NESTING = 4
MAX_RETURNS = 10
MAX_FILE_LINES = 1200

#: A section heading is a `# ----` or `# ====` rule above and below a title.
BANNER_RE = re.compile(r'^#\s*(?:-{4,}|={4,})\s*$')

#: Widest legal `# See:` line; past this the citation is carrying prose.
MAX_SEE_CHARS = 80

#: A whole-line citation and NOTHING else, at most MAX_SEE_CHARS wide.
SEE_ONLY = re.compile(r'^#\s*See:\s*docs/[A-Za-z0-9_./-]+\.md(?:#[A-Za-z0-9-]+)?\s*$')

#: A `docs/` citation in a docstring, as `(path, anchor)`; anchor may be empty.
DOC_CITATION = re.compile(r'(docs/[A-Za-z0-9_./-]+\.md)(#[A-Za-z0-9-]+)?')

#: Literal displays whose elements may carry a decoding comment.
LITERAL_NODES = (ast.Dict, ast.Set, ast.List, ast.Tuple)

BRANCH_NODES = (ast.If, ast.For, ast.While, ast.And, ast.Or,
                ast.ExceptHandler, ast.Assert, ast.comprehension)
NEST_NODES = (ast.If, ast.For, ast.While, ast.With, ast.Try)
FUNC_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


# ---------------------------------------------------------------------------
# Shared traversal
# ---------------------------------------------------------------------------


def functions(tree) -> list:
    """Every FunctionDef/AsyncFunctionDef in `tree`, cached ON the tree.

    Keyed by an attribute rather than `id(tree)`: CPython reuses the id of a
    collected object, so an id-keyed cache returned another file's functions
    and made results depend on test order.
    """
    cached = getattr(tree, '_fn_cache', None)
    if cached is None:
        cached = [n for n in ast.walk(tree) if isinstance(n, FUNC_NODES)]
        tree._fn_cache = cached
    return cached


def function_spans(tree) -> list:
    """`(start, end)` line span of every function, for owning-region blame."""
    return [(n.lineno, n.end_lineno) for n in functions(tree)]


def code_lines(text: str, tree=None) -> int:
    """Non-blank, not-comment-only, NOT-docstring lines (one definition)."""
    doc = set()
    for node in ast.walk(tree) if tree is not None else ():
        if not isinstance(node, (ast.Module, ast.ClassDef) + FUNC_NODES):
            continue
        body = getattr(node, 'body', None)
        first = body[0] if body else None
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            doc.update(range(first.lineno, first.end_lineno + 1))
    return sum(1 for n, line in enumerate(text.split('\n'), 1)
               if line.strip() and not line.strip().startswith('#')
               and n not in doc)


def complexity(node) -> int:
    """Cyclomatic complexity: 1 plus every branching node."""
    return 1 + sum(1 for x in ast.walk(node) if isinstance(x, BRANCH_NODES))


def statements(node) -> int:
    """Statements in a function body, excluding its own `def`.

    See: docs/reference/script_convert_architecture.md#length-is-counted-in-statements
    """
    return sum(1 for x in ast.walk(node) if isinstance(x, ast.stmt)) - 1


def depth(node, level: int = 0) -> int:
    """Deepest nesting of if/for/while/with/try inside this node."""
    deepest = level
    for child in ast.iter_child_nodes(node):
        step = level + 1 if isinstance(child, NEST_NODES) else level
        deepest = max(deepest, depth(child, step))
    return deepest


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


def _is_banner_title(lines: list, index: int) -> bool:
    """True when line `index` (0-based) is a title between two rule lines."""
    above = lines[index - 1].strip() if index else ''
    below = lines[index + 1].strip() if index + 1 < len(lines) else ''
    return bool(BANNER_RE.match(above) and BANNER_RE.match(below))


def comment_tokens(text: str) -> list:
    """`(line, body, own_line)` for every comment, trailing ones included.

    Tokenizing is what makes a trailing comment visible: `startswith('#')`
    sees only comments that begin their line, so moving one to the end of the
    previous line used to erase the violation with the prose unchanged.
    """
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return []
    lines = text.split('\n')
    out = []
    for tok in toks:
        if tok.type != tokenize.COMMENT:
            continue
        row = tok.start[0]
        raw = lines[row - 1] if row <= len(lines) else ''
        out.append((row, tok.string.lstrip('#').strip(),
                    raw.strip().startswith('#')))
    return out


def element_lines(tree) -> set:
    """Lines holding exactly one element of a literal dict/set/list/tuple.

    A trailing comment there DECODES an opaque literal, so it is not prose.
    Multi-line elements are excluded: only a one-line element can be labelled
    without the comment drifting from the value it names.
    See: docs/reference/script_convert_architecture.md#a-literal-element-may-carry-its-decoding
    """
    rows, spans = set(), []
    for node in ast.walk(tree):
        if not isinstance(node, LITERAL_NODES):
            continue
        parts = list(node.keys) if isinstance(node, ast.Dict) else list(node.elts)
        for item in parts:
            if item is None:
                continue
            spans.append((item.lineno, item.end_lineno))
            if item.lineno == item.end_lineno:
                rows.add(item.lineno)
    straddled = {r for r in rows if any(a < r <= b for a, b in spans)}
    return rows - straddled


def _scored_comments(path, text: str, tree=None) -> list:
    """Comments that are prose: not a shebang, banner, `#:` doc or directive."""
    lines = text.split('\n')
    spared = element_lines(tree) if tree is not None else set()
    hits = []
    for row, body, own in comment_tokens(text):
        raw = lines[row - 1].strip() if row <= len(lines) else ''
        if raw.startswith('#!') or raw.startswith('#:'):
            continue
        if own and (BANNER_RE.match(raw) or _is_banner_title(lines, row - 1)):
            continue
        if not own and row in spared and len(body) < MAX_SEE_CHARS:
            continue
        hits.append((path, row, body[:60]))
    return hits


def inline_comments(path, text: str, tree) -> list:
    """Prose comments inside a function body.  There is no legal reason."""
    spans = function_spans(tree)
    return [h for h in _scored_comments(path, text, tree)
            if any(a <= h[1] <= b for a, b in spans)]


def _is_citation(lines: list, row: int) -> bool:
    """True for a whole-line `# See: docs/...` carrying no other prose."""
    raw = lines[row - 1].strip() if row <= len(lines) else ''
    return len(raw) <= MAX_SEE_CHARS and bool(SEE_ONLY.match(raw))


def stray_comments(path, text: str, tree) -> list:
    """Prose comments outside every function body, bare citations aside."""
    spans = function_spans(tree)
    lines = text.splitlines()
    return [h for h in _scored_comments(path, text, tree)
            if not any(a <= h[1] <= b for a, b in spans)
            and not _is_citation(lines, h[1])]


def comment_blocks(path, text: str, tree) -> list:
    """A run of comment lines outside any function, over MAX_DOC_CHARS."""
    spans = function_spans(tree)
    lines = text.split('\n')
    hits, run, start = [], 0, 0
    for i, line in enumerate(lines, 1):
        raw = line.strip()
        inside = any(a <= i <= b for a, b in spans)
        counts = (raw.startswith('#') and not raw.startswith(('#:', '#!'))
                  and not BANNER_RE.match(raw) and not inside)
        if counts:
            start = start or i
            run += len(raw.lstrip('#').strip())
            continue
        if run > MAX_DOC_CHARS:
            hits.append((path, start, '%d chars' % run))
        run, start = 0, 0
    if run > MAX_DOC_CHARS:
        hits.append((path, start, '%d chars' % run))
    return hits


def fat_attr_docs(path, text: str) -> list:
    """`#:` docs running past ONE line of MAX_ATTR_DOC_CHARS characters."""
    hits, run, length, start = [], 0, 0, 0
    for i, line in enumerate(text.split('\n'), 1):
        raw = line.strip()
        if raw.startswith('#:'):
            start = start or i
            run += 1
            length += len(raw.lstrip('#:').strip())
            continue
        if run > 1 or length > MAX_ATTR_DOC_CHARS:
            hits.append((path, start, '%d lines, %d chars' % (run, length)))
        run = length = start = 0
    if run > 1 or length > MAX_ATTR_DOC_CHARS:
        hits.append((path, start, '%d lines, %d chars' % (run, length)))
    return hits


def fat_sections(path, text: str) -> list:
    """Section headings whose prose exceeds the docstring limit."""
    lines = text.split('\n')
    hits = []
    for i, line in enumerate(lines):
        if not BANNER_RE.match(line.strip()):
            continue
        prose = []
        for nxt in lines[i + 1:]:
            raw = nxt.strip()
            if not raw.startswith('#') or BANNER_RE.match(raw):
                break
            prose.append(raw.lstrip('#').strip())
        total = sum(len(x) for x in prose)
        if total > MAX_DOC_CHARS:
            hits.append((path, i + 1, '%d chars of prose' % total))
    return hits


# ---------------------------------------------------------------------------
# Docstrings
# ---------------------------------------------------------------------------


def _see_chars(doc: str) -> int:
    """Length of a trailing `See: docs/...` citation, which is not prose."""
    for line in doc.split('\n'):
        if line.strip().startswith('See:'):
            return len(line.strip())
    return 0


def bloated_docstrings(path, text: str, tree) -> list:
    """Docstrings over MAX_DOC_CHARS, or over TINY_DOC_CHARS on a tiny body.

    A flat cap with a low tier for short bodies.  The budget never scales
    with the code: a `200 * code_lines` limit let an agent buy prose by
    writing more code, and fired backwards when a body shrank.
    """
    lines = text.split('\n')
    hits = []
    for node in functions(tree):
        doc = ast.get_docstring(node)
        if not doc:
            continue
        body = [l for l in lines[node.lineno - 1:node.end_lineno]
                if l.strip() and not l.strip().startswith('#')]
        code = max(len(body) - len(doc.split('\n')) - 2, 1)
        limit = (TINY_DOC_CHARS if code <= TINY_BODY_LINES else MAX_DOC_CHARS)
        size = len(doc) - _see_chars(doc)
        if size > limit:
            hits.append((path, node.lineno, '%s: %d chars on %d code lines '
                         '(limit %d)' % (node.name, size, code, limit)))
    return hits


def anchorless_citations(path, tree) -> list:
    """Function/class docstrings citing a `docs/` file with no `#anchor`.

    A bare path names a whole file, so it names no fact.  Module docstrings
    are exempt: a module-wide citation really does mean the whole document.
    See: docs/reference/script_convert_architecture.md#a-citation-must-name-an-anchor
    """
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, FUNC_NODES + (ast.ClassDef,)):
            continue
        doc = ast.get_docstring(node)
        for ref, anchor in DOC_CITATION.findall(doc or '') if doc else []:
            if not anchor:
                hits.append((path, node.lineno,
                             '%s: cites %s with no #anchor' % (node.name, ref)))
    return hits


def missing_docstrings(path, tree) -> list:
    """Functions over TINY_BODY_LINES statements carrying no docstring.

    See: docs/reference/script_convert_architecture.md#a-tiny-function-needs-no-docstring
    """
    return [(path, n.lineno, n.name) for n in functions(tree)
            if ast.get_docstring(n) is None
            and statements(n) > TINY_BODY_LINES]


def unsectioned_defs(path, text: str, tree) -> list:
    """Top-level defs sitting ABOVE the first heading in a sectioned file."""
    lines = text.split('\n')
    heads = [i + 2 for i, line in enumerate(lines)
             if BANNER_RE.match(line.strip()) and i + 2 < len(lines)
             and BANNER_RE.match(lines[i + 2].strip())]
    if not heads:
        return []
    first = min(heads)
    return [(path, n.lineno, '%s: above the first heading' % n.name)
            for n in tree.body
            if isinstance(n, FUNC_NODES + (ast.ClassDef,))
            and n.lineno < first]


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------


def _is_private(name: str) -> bool:
    """True for a single-underscore name, which dunders and `_` itself are not."""
    return name.startswith('_') and not name.startswith('__') and name != '_'


def private_imports(path, tree) -> list:
    """Imports of another module's underscore-prefixed name.

    The underscore says the name is not an interface, so it may be renamed or
    deleted without looking outside its file; an importer voids that silently.
    See: docs/reference/script_convert_architecture.md#a-private-name-belongs-to-its-file
    """
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if _is_private(alias.name):
                hits.append((path, node.lineno, '%s: imports %s from %s'
                             % (alias.asname or alias.name, alias.name,
                                node.module or '.')))
    return hits


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def structural_sites(path, text: str, tree) -> dict:
    """The shape rules: complexity, length, nesting, returns, class state."""
    fns = functions(tree)
    return {
        'god-functions': [
            (path, f.lineno, '%s: complexity %d' % (f.name, c))
            for f in fns for c in [complexity(f)] if c > MAX_COMPLEXITY],
        'long-functions': [
            (path, f.lineno, '%s: %d statements' % (f.name, n))
            for f in fns for n in [statements(f)] if n > MAX_FUNCTION_LINES],
        'deep-nesting': [
            (path, f.lineno, '%s: nested %d deep' % (f.name, d))
            for f in fns for d in [depth(f)] if d > MAX_NESTING],
        'multi-return-fns': [
            (path, f.lineno, '%s: %d returns' % (f.name, r))
            for f in fns
            for r in [sum(1 for x in ast.walk(f)
                          if isinstance(x, ast.Return))] if r > MAX_RETURNS],
        'mutable-class-state': [
            (path, st.lineno, '%s.%s is a class-level %s'
             % (cls.name, getattr(st.targets[0], 'id', '?'),
                type(st.value).__name__.lower()))
            for cls in ast.walk(tree) if isinstance(cls, ast.ClassDef)
            for st in cls.body if isinstance(st, ast.Assign)
            and isinstance(st.value, (ast.Dict, ast.List, ast.Set))],
        'oversized-files': [
            (path, 1, 'file: %d code lines' % n)
            for n in [code_lines(text, tree)] if n > MAX_FILE_LINES],
    }

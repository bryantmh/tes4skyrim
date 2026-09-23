"""The repo-wide code rules: the escape hatches that used to let edits pass.

Each test names a bypass that was measured before the rules moved into
`tools/validate/code_rules.py`.  They are pure in-memory calls -- no git, no
subprocess -- so the whole file runs in well under a second.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tools.validate import code_rules as CR
from tools.validate import code_rules_ast as D

ROOT = Path(__file__).resolve().parent.parent
FAKE = Path('t.py')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def sites(source, rule):
    """Lines flagged for `rule` in `source`."""
    found = CR.rule_sites(FAKE, source, with_tools=False)
    return sorted(s[1] for s in found.get(rule, ()))


def blame(before, after, touched, rule):
    """Lines an edit owns for `rule`, given the lines it changed."""
    text, tree = CR._parse(FAKE, after)
    now = CR.rule_sites(FAKE, after, with_tools=False)
    was = CR.rule_sites(FAKE, before, with_tools=False)
    owned = CR._blame(now, was, set(touched), text, tree)
    return sorted(s[1] for s in owned.get(rule, ()))


# ---------------------------------------------------------------------------
# Comments: the scanner must tokenize, not read the first character
# ---------------------------------------------------------------------------


OWN_LINE = '''"""M."""


def f(x) -> int:
    """D."""
    # a measured 2026 census of 3,740 records
    return x
'''

TRAILING = '''"""M."""


def f(x) -> int:
    """D."""
    return x  # a measured 2026 census of 3,740 records
'''


def test_trailing_comment_is_not_an_escape_hatch():
    """The same prose must be flagged wherever on the line it sits."""
    assert sites(OWN_LINE, 'inline-comments') == [6]
    assert sites(TRAILING, 'inline-comments') == [6]


def test_no_comment_is_exempt_by_its_prefix():
    """A directive is still a comment: nothing may be waved past by prefix.

    An exemption keyed on `# noqa` would let any sentence through by prepending
    one, and the repo's own `# noqa: plugin-path` (14 uses, no reader) shows
    the costume is already worn.
    """
    for comment in ('# noqa: F401', '# noqa', '# noqa: plugin-path (.psc)',
                    '# pragma: no cover', '# type: ignore'):
        src = '"""M."""\nVALUE = 1  %s\n' % comment
        assert sites(src, 'stray-comments') == [2], comment


def test_banner_prefix_cannot_launder_prose():
    """`# --- text ---` is not a heading; a heading is a bare rule line."""
    src = '''"""M."""


def f(x) -> int:
    """D."""
    # --- anything I want to say at length ---
    return x
'''
    assert sites(src, 'inline-comments') == [6]


# ---------------------------------------------------------------------------
# Docstrings: a flat cap, never a multiple of the body
# ---------------------------------------------------------------------------


def _fn(doc_chars, body_lines):
    """A function with a docstring of `doc_chars` over `body_lines` lines."""
    body = '\n'.join('    a%d = %d' % (i, i) for i in range(body_lines))
    return '"""M."""\n\n\ndef f():\n    """%s"""\n%s\n    return 0\n' % (
        'x' * doc_chars, body)


def test_long_body_does_not_buy_prose_budget():
    """A 600-char docstring is over the cap however long the body is."""
    assert sites(_fn(600, 10), 'bloated-docstrings') == [4]
    assert sites(_fn(600, 40), 'bloated-docstrings') == [4]


def test_short_body_keeps_the_tight_limit():
    """A one- or two-line body gets one line of docstring."""
    assert sites(_fn(200, 1), 'bloated-docstrings') == [4]
    assert sites(_fn(60, 1), 'bloated-docstrings') == []


def test_citation_line_is_not_counted_as_prose():
    """The `See:` route must not be punished by the rule that asks for it."""
    doc = 'x' * 70 + '\n\n    See: docs/reference/pipeline.md'
    src = '"""M."""\n\n\ndef f():\n    """%s"""\n    return 0\n' % doc
    assert sites(src, 'bloated-docstrings') == []


# ---------------------------------------------------------------------------
# Blame: what an edit owns
# ---------------------------------------------------------------------------


LEGACY = '''"""M."""


def big(x) -> int:
    """D."""
    # legacy one
    a = x + 1
    # legacy two
    b = a + 1
    return b
'''


def test_legacy_comment_above_an_edited_line_is_owned():
    """Editing a line adopts the comment sitting above it."""
    after = LEGACY.replace('b = a + 1', 'b = a + 9')
    assert blame(LEGACY, after, [9], 'inline-comments') == [8]


def test_edit_with_no_comment_above_owes_nothing():
    """The common case: 91% of one-line edits owe zero."""
    after = LEGACY.replace('    """D."""', '    """D2."""')
    assert blame(LEGACY, after, [5], 'inline-comments') == []


def test_new_comment_is_owned():
    """A comment the edit wrote is always its own."""
    after = LEGACY.replace('    return b', '    # mine\n    return b')
    assert 10 in blame(LEGACY, after, [10, 11], 'inline-comments')


def test_shape_debt_is_not_inherited_by_a_small_edit():
    """A one-line edit never adopts a long function's pre-existing shape."""
    body = '\n'.join('    a%d = %d' % (i, i) for i in range(80))
    src = '"""M."""\n\n\ndef f():\n    """D."""\n%s\n    return 0\n' % body
    assert sites(src, 'long-functions') == [4]
    assert blame(src, src.replace('a5 = 5', 'a5 = 6'), [10],
                 'long-functions') == []


def _sized(body_lines):
    """A function with a one-line docstring over `body_lines` of body."""
    return _fn(4, body_lines)


def _branchy(branches):
    """A function whose cyclomatic complexity is `branches` plus one."""
    body = '\n'.join('    if x == %d:\n        pass' % i
                     for i in range(branches))
    return '"""M."""\n\n\ndef f(x):\n    """D."""\n%s\n    return 0\n' % body


def test_growing_an_already_long_function_is_blamed():
    """A shape metric may never get worse: 100 lines -> 130 is blamed."""
    assert blame(_sized(100), _sized(130), range(106, 136),
                 'long-functions') == [4]


def test_shrinking_a_long_function_passes_even_while_over():
    """Improving is always allowed: the bill is what you added, not the debt."""
    assert blame(_sized(130), _sized(100), range(56, 106),
                 'long-functions') == []


def test_growing_complexity_of_a_god_function_is_blamed():
    """Same ratchet for complexity, which is also one site per function."""
    assert blame(_branchy(29), _branchy(59), range(60, 120),
                 'god-functions') == [4]


def test_nesting_added_below_the_def_is_owned():
    """The site is the `def`, but the edit that deepened it still owns it."""
    before = '''"""M."""


def f(x) -> int:
    """D."""
    if x:
        return 1
    return 0
'''
    after = '''"""M."""


def f(x) -> int:
    """D."""
    if x:
        for i in range(3):
            while i:
                with open('a') as h:
                    if h:
                        return 1
    return 0
'''
    assert blame(before, after, list(range(7, 12)), 'deep-nesting') == [4]


# ---------------------------------------------------------------------------
# The tool itself
# ---------------------------------------------------------------------------


def test_help_does_not_crash():
    """`--help` must work on a cp1252 console: no emoji in the description."""
    got = subprocess.run(
        [sys.executable, str(ROOT / 'tools' / 'validate' / 'code_rules.py'),
         '--help'], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0
    assert got.stderr == ''


def test_a_situational_fix_beats_the_generic_one():
    """A rule with several causes must name the cure for the one it found."""
    sites_found = [(FAKE, 8, 'noqa: E402')]
    hints = CR._specific_hints('stray-comments', sites_found)
    assert any('pythonpath' in h for h in hints)
    assert CR._specific_hints('stray-comments', [(FAKE, 3, 'a plain note')]) == []


def test_every_situational_hint_names_a_real_rule():
    """A hint keyed on a rule that does not exist would never print."""
    assert {rule for rule, _ in CR.SPECIFIC} <= set(CR.EXPLAIN)


def test_every_gated_rule_defines_itself():
    """EXPLAIN and REMEDY are the rule's whole definition to the agent."""
    assert set(CR.EXPLAIN) == set(CR.REMEDY)


def test_multiplier_is_gone():
    """A prose budget must never be a function of the code around it."""
    source = (ROOT / 'tools' / 'validate' / 'code_rules_ast.py').read_text(
        encoding='utf-8')
    assert 'DOC_CHARS_PER_LINE' not in source


# ---------------------------------------------------------------------------
# What the gate must NOT bill: staged work, and files outside the repo
# ---------------------------------------------------------------------------


def _run_gate(*args):
    """`--gate-diff` on `args`, returning the finished process."""
    return subprocess.run(
        [sys.executable, str(ROOT / 'tools' / 'validate' / 'code_rules.py'),
         '--gate-diff'] + list(args), cwd=ROOT, capture_output=True, text=True)


def test_a_staged_file_with_a_clean_worktree_owes_nothing():
    """`git diff HEAD` folds in the INDEX, billing an edit for staged work.

    Reproduced with 10 files staged by a rename: every later tool call was
    blamed for `oversized-files` in two files it had never opened.
    """
    staged = subprocess.run(['git', 'diff', '--cached', '--name-only'],
                            cwd=ROOT, capture_output=True, text=True).stdout
    dirty = subprocess.run(['git', 'diff', '--name-only'], cwd=ROOT,
                           capture_output=True, text=True).stdout
    for name in staged.split():
        if not name.endswith('.py') or name in dirty:
            continue
        got = _run_gate(name)
        assert got.returncode == 0, '%s: %s' % (name, got.stderr)


def test_an_untracked_file_is_still_scored_whole():
    """The index fix must not blind the gate to a brand-new file."""
    probe = ROOT / 'tools' / 'validate' / '_gate_probe_tmp.py'
    probe.write_text(OWN_LINE, encoding='utf-8')
    try:
        got = _run_gate(str(probe))
        assert got.returncode == 1
        assert 'inline-comments' in got.stderr
    finally:
        probe.unlink()


def test_a_file_outside_the_repo_is_not_judged():
    """A scratchpad shares no component with SKIP_PARTS, and Temp != temp."""
    sys.path.insert(0, str(ROOT / '.claude' / 'hooks'))
    try:
        import doc_rules_gate as gate
    finally:
        sys.path.pop(0)
    outside = Path(tempfile.gettempdir()) / 'claude_gate_probe.py'
    outside.write_text(OWN_LINE, encoding='utf-8')
    try:
        assert not gate.judged(str(outside))
        assert gate.judged(str(ROOT / 'tools' / 'validate' / 'code_rules.py'))
    finally:
        outside.unlink()


# ---------------------------------------------------------------------------
# The `See:` exemption: a bare citation only, never a carrier for prose
# ---------------------------------------------------------------------------


def _module_comment(comment):
    """A module-level constant carrying `comment` above it."""
    return '"""M."""\n\n%s\nVALUE = 1\n' % comment


def test_a_bare_see_line_is_legal():
    """The outflow route needs one legal line to point at what it moved."""
    for good in ('# See: docs/commentary/performance.md',
                 '# See: docs/commentary/performance.md#parallelism-rules'):
        assert sites(_module_comment(good), 'stray-comments') == [], good


def test_a_see_line_may_not_carry_prose():
    """`See:` must not become the prefix that launders a sentence."""
    for bad in ('# See: docs/commentary/performance.md, but only when the '
                'writer is single-process',
                '# a measured census. See: docs/commentary/performance.md',
                '# See: docs/commentary/performance.md and the notes above'):
        assert sites(_module_comment(bad), 'stray-comments') == [3], bad


def test_a_see_line_is_capped_at_eighty_characters():
    """Past the cap the line is prose wearing a citation."""
    fits = '# See: docs/%s.md' % ('a' * 60)
    over = '# See: docs/%s.md' % ('a' * 70)
    assert len(fits) <= D.MAX_SEE_CHARS < len(over)
    assert sites(_module_comment(fits), 'stray-comments') == []
    assert sites(_module_comment(over), 'stray-comments') == [3]


def test_the_exemption_does_not_reach_inside_a_function():
    """A citation is module-level bookkeeping, not an in-body comment."""
    src = ('"""M."""\n\n\ndef f(x) -> int:\n    """D."""\n'
           '    # See: docs/commentary/performance.md\n    return x\n')
    assert sites(src, 'inline-comments') == [6]


def _size_site(n):
    """The `oversized-files` site a file of `n` code lines reports."""
    return [(ROOT / 'x.py', 1, 'file: %d code lines' % n)]


def test_shrinking_an_oversized_file_is_absolved():
    """A same-size or smaller file owes nothing, however far over it sits."""
    assert CR._worsened(_size_site(1190), _size_site(1200)) == []
    assert CR._worsened(_size_site(1200), _size_site(1200)) == []


def test_growing_or_crossing_an_oversized_file_is_charged():
    """Climbing while over, and crossing from under, are both owed."""
    assert CR._worsened(_size_site(1210), _size_site(1200))
    assert CR._worsened(_size_site(1010), [])


def test_a_test_file_is_not_judged_on_length():
    """Length is a COUNT of cases there, not a responsibility to split."""
    got = CR.rule_sites(Path(__file__).resolve(), with_tools=False)
    assert 'oversized-files' not in got


def test_the_length_exemption_reaches_no_other_rule():
    """Only `oversized-files` is lifted; a test file owes every other rule."""
    src = '"""M."""\n\n\ndef f(x) -> int:\n    """D."""\n    x += 1  # bump\n    return x\n'
    probe = ROOT / 'tests' / '_gate_probe_tmp.py'
    probe.write_text(src, encoding='utf-8')
    try:
        assert 'inline-comments' in CR.rule_sites(probe, with_tools=False)
    finally:
        probe.unlink()


# ---------------------------------------------------------------------------
# Length is statements, so reflow cannot move it
# ---------------------------------------------------------------------------


def _stmts(body):
    """Statement count of the first function in `body`."""
    import ast
    return D.statements(ast.parse(body).body[0])


def test_a_line_continuation_does_not_change_the_score():
    """Joining or splitting lines is whitespace; the work is identical."""
    split = 'def f():\n    x = (1 +\n         2)\n    return x\n'
    joined = 'def f():\n    x = (1 + 2)\n    return x\n'
    assert _stmts(split) == _stmts(joined)


def test_folding_a_temp_into_the_return_does_not_pay():
    """The golf move that used to clear the rule now costs exactly one."""
    named = 'def f():\n    y = g(1)\n    return y\n'
    folded = 'def f():\n    return g(1)\n'
    assert _stmts(named) - _stmts(folded) == 1


# ---------------------------------------------------------------------------
# A file the gate cannot read must never read as clean
# ---------------------------------------------------------------------------


def test_a_tiny_function_needs_no_docstring():
    """Two statements are the whole contract; a third makes one owed."""
    tiny = '"""M."""\n\n\ndef f(x):\n    y = x + 1\n    return y\n'
    grown = '"""M."""\n\n\ndef f(x):\n    y = x + 1\n    y *= 2\n    return y\n'
    assert sites(tiny, 'missing-docstrings') == []
    assert sites(grown, 'missing-docstrings') == [4]


def test_unparsable_python_is_a_violation():
    """Every other rule walks the tree, so a parse failure scored zero."""
    assert sites('def f(:\n    pass\n', 'broken-syntax') == [1]


def test_valid_python_reports_no_syntax_site():
    """The rule must stay silent on everything that parses."""
    assert sites('"""M."""\n', 'broken-syntax') == []


# ---------------------------------------------------------------------------
# The hook: what a write must not slip past
# ---------------------------------------------------------------------------


BAD_SRC = ('"""M."""\n\n\ndef f(x) -> int:\n    """D."""\n'
           '    x += 1  # bump\n    return x\n')


def _hook(tool, tool_input):
    """(exit code, stderr) for one PreToolUse payload."""
    hook = ROOT / '.claude' / 'hooks' / 'doc_rules_gate.py'
    payload = {'hook_event_name': 'PreToolUse', 'tool_name': tool,
               'tool_input': tool_input}
    got = subprocess.run([sys.executable, str(hook)], input=json.dumps(payload),
                         capture_output=True, text=True, cwd=ROOT)
    return got.returncode, got.stderr


def test_a_write_creating_a_new_file_is_gated():
    """`judged()` required the file on disk, so every new file passed free."""
    probe = ROOT / 'tools' / 'validate' / '_new_file_probe.py'
    assert not probe.exists()
    code, err = _hook('Write', {'file_path': str(probe), 'content': BAD_SRC})
    assert code == 2 and 'inline-comments' in err
    assert not probe.exists()


def test_the_candidate_is_never_written_over_the_real_file():
    """Gating in place made the gate its own bypass when it was killed."""
    target = ROOT / 'tools' / 'validate' / 'code_rules.py'
    before = target.read_bytes()
    _hook('Write', {'file_path': str(target), 'content': BAD_SRC})
    assert target.read_bytes() == before


def test_a_shell_command_must_name_the_wrapper():
    """A bare command can write a `.py` that no gate ever sees."""
    code, err = _hook('Bash', {'command': 'python - <<PY\npass\nPY'})
    assert code == 2 and 'safe_run.py' in err
    assert _hook('Bash', {'command': 'python tools/validate/safe_run.py ls'})[0] == 0


RUN = 'python tools/validate/safe_run.py'

#: Commands that run code outside the wrapper, each a route that once passed.
ESCAPES = [
    RUN + " true && python - <<'PY'\nopen('x.py', 'w')\nPY",
    RUN + ' true; cp a.py b.py',
    'cp a.py b.py; ' + RUN + ' true',
    RUN + ' echo $(python evil.py)',
    RUN + ' echo "`python evil.py`"',
    '(python evil.py); ' + RUN + ' true',
    'echo x > a.py; ' + RUN + ' true',
    RUN + " python - <<'PY'\nx = 1\nPY\npython evil.py",
    'for f in a b; do ' + RUN + ' true; done',
    RUN + ' true | tee a.py',
]

#: Commands that stay inside it, including every shape this session used.
CONTAINED = [
    'cd /c/repo && ' + RUN + ' python -m pytest -q 2>&1 | tail -3; echo "exit $?"',
    RUN + " python - <<'PY'\nprint('a && b; $(x) `y`')\nPY",
    RUN + " -c 'cp a.py b.py && python x.py | tail'",
    RUN + " grep -E '(a|b)' f.py",
    RUN + " echo '$(literal)'",
    RUN + ' git log | Select-Object -First 5',
    'P=1 ' + RUN + ' true > out.py',
    RUN + ' a; ' + RUN + ' b',
    'cd C:/tmp && python C:/repo/tools/validate/safe_run.py -c "pwd"',
    'python "C:\\repo\\tools\\validate\\safe_run.py" ls C:/x',
]


def test_every_escape_is_refused():
    """Code after `&&`, in a subshell, or in `$(...)` never saw the gate."""
    sys.path.insert(0, str(ROOT / '.claude' / 'hooks'))
    try:
        import shell_route
    finally:
        sys.path.pop(0)
    for command in ESCAPES:
        assert shell_route.escape(command), command
    for command in CONTAINED:
        assert shell_route.escape(command) is None, command


def test_the_hook_names_why_a_chain_was_refused():
    """The refusal says which piece escaped and how to wrap it."""
    code, err = _hook('Bash', {'command': ESCAPES[1]})
    assert code == 2 and '`cp a.py b.py` runs outside' in err and '-c' in err


def _wrapped(*args, cwd=ROOT):
    """The finished `safe_run.py` process for `args`, with no outer shell."""
    return subprocess.run(
        [sys.executable, str(ROOT / 'tools' / 'validate' / 'safe_run.py')]
        + list(args), cwd=cwd, capture_output=True, text=True)


def test_the_wrapper_passes_arguments_verbatim():
    """A second shell re-read `(` as syntax and expanded `$HOME` in quotes."""
    tricky = ['(a|b)', '$HOME x', 'say "hi" now', "it's", 'a\\b']
    got = _wrapped(sys.executable, '-c',
                   'import sys, json; print(json.dumps(sys.argv[1:]))',
                   *tricky)
    assert got.returncode == 0, got.stderr
    assert json.loads(got.stdout) == tricky


def test_dash_c_runs_one_string_through_a_shell():
    """Pipes inside the gated region are the `-c` form's job."""
    got = _wrapped('-c', 'echo abc | "%s" -c "import sys; print(sys.stdin.'
                   'read().strip()[::-1])"' % sys.executable.replace('\\', '/'))
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == 'cba'


def test_the_command_runs_in_the_callers_directory(tmp_path):
    """A relative write lands where the caller `cd`-ed, not in the repo root."""
    got = _wrapped(sys.executable, '-c', 'open("marker.txt", "w").close()',
                   cwd=tmp_path)
    assert got.returncode == 0, got.stderr
    assert (tmp_path / 'marker.txt').exists()
    assert not (ROOT / 'marker.txt').exists()


def test_a_missing_program_says_to_use_dash_c():
    """A builtin has no executable; the hint names the form that runs it."""
    got = _wrapped('no_such_program_xyz')
    assert got.returncode == 127 and '-c' in got.stderr


# ---------------------------------------------------------------------------
# An import and its use: deferred per edit, enforced when the turn ends
# ---------------------------------------------------------------------------


HALF_PAIR = '"""M."""\n\nimport os\n'


def _turn_hook(event, session, **extra):
    """(exit code, stderr) for one hook `event` in the test's own session."""
    hook = ROOT / '.claude' / 'hooks' / 'doc_rules_gate.py'
    payload = dict(hook_event_name=event, session_id=session, **extra)
    got = subprocess.run([sys.executable, str(hook)], input=json.dumps(payload),
                         capture_output=True, text=True, cwd=ROOT)
    return got.returncode, got.stderr


def test_an_import_alone_is_deferred_not_refused():
    """One Edit cannot write an import and a far-off use; neither half is refused."""
    probe = ROOT / 'tools' / 'validate' / '_pair_probe_tmp.py'
    probe.write_text(HALF_PAIR, encoding='utf-8')
    try:
        assert _run_gate(str(probe)).returncode == 1
        assert _run_gate(str(probe), '--defer-paired').returncode == 0
    finally:
        probe.unlink()


def test_a_citation_ahead_of_its_doc_is_deferred():
    """The docstring may cite a section the next edit writes."""
    probe = ROOT / 'tools' / 'validate' / '_pair_probe_tmp.py'
    probe.write_text('"""M.\n\nSee: docs/reference/pipeline.md#no-such-anchor\n"""\n',
                     encoding='utf-8')
    try:
        assert 'dead-citations' in _run_gate(str(probe)).stderr
        assert _run_gate(str(probe), '--defer-paired').returncode == 0
    finally:
        probe.unlink()


def test_the_turn_cannot_end_on_half_a_pair():
    """The Post hook remembers the file; Stop re-gates it with every rule."""
    probe = ROOT / 'tools' / 'validate' / '_pair_probe_tmp.py'
    session = 'test_pair_%d' % os.getpid()
    probe.write_text(HALF_PAIR, encoding='utf-8')
    try:
        code, _ = _turn_hook('PostToolUse', session, tool_name='Write',
                             tool_input={'file_path': str(probe)})
        assert code == 0
        code, err = _turn_hook('Stop', session)
        assert code == 2 and 'F401' in err
        probe.write_text('"""M."""\n\nimport os\n\nHOME = os.sep\n',
                         encoding='utf-8')
        assert _turn_hook('Stop', session)[0] == 0
    finally:
        probe.unlink()


# ---------------------------------------------------------------------------
# A literal element may carry its decoding
# ---------------------------------------------------------------------------


VERSIONS = '''"""M."""

_SUPPORTED = {
    0x14000004,  # Gamebryo v20.0.0.4 - primary Oblivion format
    0x0a01006a,  # Gamebryo v10.1.0.106
}
'''


def test_a_literal_element_may_be_decoded():
    """The version table is the case the prose rule used to get wrong."""
    assert sites(VERSIONS, 'stray-comments') == []


def test_every_literal_display_spares_its_elements():
    """A dict, list and tuple label their entries the same way a set does."""
    for open_, close in (('{', '}'), ('[', ']'), ('(', ')')):
        src = '"""M."""\n\nT = %s\n    1,  # one\n%s\n' % (open_, close)
        assert sites(src, 'stray-comments') == [], open_
    pairs = '"""M."""\n\nT = {\n    "XCLC": 12,  # +4 land flags vs TES4\n}\n'
    assert sites(pairs, 'stray-comments') == []


def test_an_own_line_comment_in_a_literal_is_still_prose():
    """A comment on its own line is a heading in disguise, not a label."""
    src = '"""M."""\n\nT = {\n    # a heading in disguise\n    1,\n}\n'
    assert sites(src, 'stray-comments') == [4]


def test_a_decoding_comment_is_capped_like_a_citation():
    """Past the cap the label is an argument, and belongs in the docstring."""
    over = '"""M."""\n\nT = {\n    1,  # %s\n}\n' % ('x' * D.MAX_SEE_CHARS)
    assert sites(over, 'stray-comments') == [4]


def test_the_exemption_does_not_reach_an_assignment():
    """Only an ELEMENT is spared; a statement's trailing comment is prose."""
    src = '"""M."""\n\n\ndef f() -> int:\n    """D."""\n    x = g()  # slow\n    return x\n'
    assert sites(src, 'inline-comments') == [6]


def test_a_multi_line_element_is_not_spared():
    """Its comment has already drifted from the value it claims to label."""
    src = '"""M."""\n\nT = [\n    (1,\n     2),  # a pair\n]\n'
    assert sites(src, 'stray-comments') == [5]


# ---------------------------------------------------------------------------
# A citation must name an anchor
# ---------------------------------------------------------------------------


DOC = 'docs/reference/script_convert_architecture.md'


def _cite(target, kind='def f()'):
    """A function or class whose docstring cites `target`."""
    return '"""M."""\n\n\n%s:\n    """D.\n\n    See: %s\n    """\n' % (kind, target)


def test_a_bare_path_in_a_docstring_is_charged():
    """A 2,346-line file is not a fact; the anchor is what carries it."""
    assert sites(_cite(DOC), 'anchorless-citations') == [4]


def test_an_anchored_citation_passes():
    """The rule demands an anchor, never a longer docstring."""
    assert sites(_cite(DOC + '#length-is-counted-in-statements'),
                 'anchorless-citations') == []


def test_a_class_docstring_is_judged_too():
    """A class states a contract exactly as a function does."""
    assert sites(_cite(DOC, 'class Thing'), 'anchorless-citations') == [4]


def test_a_module_docstring_may_cite_a_whole_file():
    """Module-wide really does mean the whole document; an anchor would lie."""
    assert sites('"""M.\n\nSee: %s\n"""\n' % DOC, 'anchorless-citations') == []


def test_a_short_docstring_with_an_anchor_is_never_charged():
    """No length floor: the accepted repair was SHORTER than the defect."""
    src = _cite(DOC + '#length-is-counted-in-statements')
    assert sites(src, 'anchorless-citations') == []
    assert sites(src, 'bloated-docstrings') == []


def test_an_untouched_anchorless_citation_is_not_owed():
    """Scoped like every other rule: legacy debt waits for whoever edits it."""
    before = _cite(DOC) + '\n\ndef g():\n    """D."""\n'
    after = _cite(DOC) + '\n\ndef g():\n    """D."""\n    return 1\n'
    assert blame(before, after, [12], 'anchorless-citations') == []


# ---------------------------------------------------------------------------
# A private name belongs to its file
# ---------------------------------------------------------------------------


def test_importing_a_private_name_is_charged():
    """The underscore says the name may be renamed without looking outside."""
    assert sites('"""M."""\n\nfrom other import _helper\n',
                 'private-imports') == [3]


def test_a_public_name_and_a_dunder_are_not_private():
    """`__all__` and a public function are interfaces by their spelling."""
    src = '"""M."""\n\nfrom other import public\nfrom mod import __version__\n'
    assert sites(src, 'private-imports') == []


def test_an_alias_does_not_launder_a_private_import():
    """Renaming on the way in changes the spelling, not the guarantee."""
    assert sites('"""M."""\n\nfrom other import _helper as helper\n',
                 'private-imports') == [3]


def test_a_local_import_is_charged_like_a_top_level_one():
    """301 of 537 measured sites hid inside a function body."""
    src = ('"""M."""\n\n\ndef f() -> int:\n    """D."""\n'
           '    from other import _helper\n    return _helper()\n')
    assert sites(src, 'private-imports') == [6]


def test_a_test_file_may_reach_a_private_helper():
    """A test is allowed to know more than a caller, as with file length."""
    got = CR.rule_sites(Path(__file__).resolve(), with_tools=False)
    assert 'private-imports' not in got


def test_an_untouched_private_import_is_not_owed():
    """The 299 measured sites are payable only by whoever edits the line."""
    before = '"""M."""\n\nfrom other import _helper\n'
    after = '"""M."""\n\nfrom other import _helper\n\nVALUE = 1\n'
    assert blame(before, after, [5], 'private-imports') == []


# ---------------------------------------------------------------------------
# A rename is not an edit
# ---------------------------------------------------------------------------


def _rename_probe(content):
    """(exit code, stderr) for writing `content` to an unused path."""
    probe = ROOT / 'tools' / 'validate' / '_rename_test_probe.py'
    mapping = CR.RENAME_MAP
    saved = mapping.read_bytes() if mapping.exists() else None
    try:
        if mapping.exists():
            mapping.unlink()
        code, err = _hook('Write', {'file_path': str(probe),
                                    'content': content})
    finally:
        if probe.exists():
            probe.unlink()
        if saved is None:
            mapping.unlink(missing_ok=True)
        else:
            mapping.write_bytes(saved)
    return code, err


def test_a_rename_owes_nothing():
    """The author moved a file and wrote no code, so nothing is theirs."""
    donor = (ROOT / 'tools' / 'validate' / 'code_rules_ast.py')
    assert _rename_probe(CR._baseline_source(donor))[0] == 0


def test_one_added_line_breaks_the_match():
    """Exact equality is the safety property; there is no threshold to tune."""
    donor = (ROOT / 'tools' / 'validate' / 'code_rules_ast.py')
    dirty = CR._baseline_source(donor).replace(
        'MAX_COMPLEXITY = 25', 'MAX_COMPLEXITY = 25  # snuck in', 1)
    code, err = _rename_probe(dirty)
    assert code == 2 and 'snuck in' in err


def test_an_unrelated_new_file_is_still_scored_whole():
    """No donor means the rename map never fires, as before it existed."""
    code, err = _rename_probe('"""M."""\n\n\ndef f() -> int:\n    """D."""\n'
                              '    x = 1  # bump\n    return x\n')
    assert code == 2 and 'inline-comments' in err


def _with_dirty_donor(mutate):
    """Run `mutate(on_disk_text)` through the gate with the donor DIRTY.

    The donor carries an unstaged violation, as an in-flight edit would, so a
    move of it must not bill the mover for what the donor already had.
    """
    donor = ROOT / 'tools' / 'validate' / 'code_rules_ast.py'
    original = donor.read_bytes()
    try:
        donor.write_bytes(original.replace(
            b'MAX_COMPLEXITY = 25', b'MAX_COMPLEXITY = 25  # donor debt', 1))
        return _rename_probe(mutate(donor.read_text(encoding='utf-8')))
    finally:
        donor.write_bytes(original)


def test_a_file_edited_then_moved_still_maps():
    """The copy carries the donor's in-flight edits, so it must still match."""
    assert _with_dirty_donor(lambda text: text)[0] == 0


def test_only_what_the_move_adds_is_charged():
    """The donor's own debt travels with it; a NEW violation does not."""
    code, err = _with_dirty_donor(lambda text: text.replace(
        'MAX_NESTING = 4', 'MAX_NESTING = 4  # added in transit', 1))
    assert code == 2 and 'added in transit' in err


def test_a_candidate_is_never_its_own_donor():
    """The gate stages a copy beside the file, which matched itself exactly."""
    src = ROOT / 'tools' / 'validate' / 'safe_run.py'
    text = src.read_text(encoding='utf-8')
    probe = ROOT / 'tools' / 'validate' / '.gate_candidate_selftest.py'
    probe.write_text(text, encoding='utf-8', newline='')
    try:
        assert CR._donor(ROOT / 'tools' / 'validate' / '_nope.py',
                         text, probe) == src
    finally:
        probe.unlink()

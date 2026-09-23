#!/usr/bin/env python3
"""PostToolUse gate: an edit must not leave broken rules on the code it owns.

Reporting is not enforcement.  `arch_fitness.py` printed `inline-comments 1617`
on every run for weeks and the number never moved, because nothing made it
move.  This runs whether or not the agent decides to run it, on the file it
just wrote, immediately after writing it.

The unit is the code the edit OWNS: the lines it changed plus the comment run
directly above each.  A whole-file gate made a two-line fix inherit all 1,010
of `converter.py`'s violations -- a bill the edit cannot pay, so the gate gets
switched off.  Measured over every possible one-line edit in `script_convert/`,
91% of edits owe nothing and the median is 0.

🛑 IT ALSO GATES `Bash`.  Matching only Edit/Write leaves the whole gate
optional: a `python - <<'PY'` heredoc writes the same file and names no
`file_path`, so nothing fires.  Nine rule-breaking edits reached
`script_convert/` that way.  A Bash call is therefore judged on every tracked
`.py` that differs from HEAD.

Exit 2 feeds stderr back to the agent as a blocking error.  Exit 0 is silent.
"""

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE = os.path.join(ROOT, 'tools', 'validate', 'code_rules.py')
LINKS = os.path.join(ROOT, 'tools', 'validate', 'doc_links.py')
DOCS = os.path.join(os.path.realpath(ROOT), 'docs')

#: A fresh clone has no checkers and no import path until this is run once.
SETUP = ('  SET UP THIS REPO FIRST:  python -m pip install -e ".[dev]"\n'
         '  It puts the repo on sys.path and installs ruff, vulture, pytest.\n')

#: The one shell entry point: it gates every first-party `.py` its child wrote.
WRAPPER = 'tools/validate/safe_run.py'

#: Trees the rules do not judge: scratch, vendored code, and generated output.
SKIP_PARTS = ('temp', 'references', 'external', 'output', 'export', 'build',
              '.git', 'node_modules', '__pycache__', '.venv')


def judged(path, must_exist=True):
    """Is this a Python file INSIDE the repo that the rules judge?

    A path outside the root is rejected by `os.pardir`, which is what keeps a
    scratchpad under `AppData\\Local\\Temp` unscored; the SKIP_PARTS scan then
    drops the in-repo scratch trees.  The suffix is case-folded because NTFS is
    case-insensitive.  `must_exist` is False for a file an edit is ABOUT to
    create.
    See: docs/reference/script_convert_architecture.md#what-the-gate-must-see
    """
    if not path or not path.lower().endswith('.py'):
        return False
    if must_exist and not os.path.isfile(path):
        return False
    try:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(ROOT))
    except ValueError:
        return False
    if rel.startswith(os.pardir):
        return False
    return not any(p.lower() in SKIP_PARTS for p in rel.split(os.sep))


def edited_paths(payload):
    """Every Python file this tool call may have written.

    A shell command names no file, so this used to answer with every tracked
    `.py` differing from the index -- which billed an edit for files it had
    never opened.  `safe_run.py` hashes before and after instead, so a shell
    command's writes are known exactly and never reach here.
    See: docs/reference/script_convert_architecture.md#what-the-gate-must-see
    """
    tool = payload.get('tool_name')
    if tool in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
        path = (payload.get('tool_input') or {}).get('file_path')
        return [path] if judged(path) else []
    return []


def gate(path, candidate=None):
    """(exit code, report) for one file; code 0 means it passed.

    `candidate` names a copy holding the text an edit WOULD write, while
    `path` stays the file git scopes the blame to.

    A gate that cannot RUN fails closed: a silent 0 is indistinguishable from a
    clean file, which is how a crash became a pass.  Only a missing checker is
    absolved, and only when the child says so on its own exit code.
    See: docs/reference/script_convert_architecture.md#what-the-gate-must-see
    """
    argv = [sys.executable, GATE, '--gate-diff', path]
    if candidate:
        argv += ['--candidate', candidate]
    try:
        got = subprocess.run(
            argv, cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=60)
    except Exception as exc:
        return 2, 'The code-rules gate could not run: %s\n%s' % (exc, SETUP)
    if got.returncode == 0 and 'ModuleNotFoundError' in (got.stderr or ''):
        return 0, (got.stderr or '') + SETUP
    return got.returncode, (got.stderr or got.stdout)


def touched_docs(payload):
    """True when this call wrote a `.md` under `docs/`.

    Unlike the code rules this is not scoped to the edit: links and anchors are
    a property of the whole tree, so one changed `.md` re-checks all of them.
    """
    if payload.get('tool_name') not in ('Edit', 'Write', 'MultiEdit',
                                        'NotebookEdit'):
        return False
    path = (payload.get('tool_input') or {}).get('file_path') or ''
    return path.endswith('.md') and DOCS in os.path.realpath(path)


def gate_docs():
    """(exit code, report) for the doc tree: links, anchors, index, bindings."""
    try:
        got = subprocess.run(
            [sys.executable, LINKS, '--index'], cwd=ROOT,
            capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=60)
    except Exception as exc:
        return 2, 'The doc-link gate could not run: %s\n' % exc
    return got.returncode, got.stderr


def _edited_text(tool, args, text):
    """The post-edit text for an Edit/MultiEdit, or None if unpredictable.

    An `old_string` absent from the file is UNPREDICTABLE, not clean: the tool
    may still write, so declining to guess must not read as a pass.
    """
    edits = args.get('edits') if tool == 'MultiEdit' else [args]
    for one in edits or []:
        old, new = one.get('old_string') or '', one.get('new_string') or ''
        if old not in text:
            return None
        text = text.replace(old, new, -1 if one.get('replace_all') else 1)
    return text


def pending_text(payload):
    """(path, post-edit text) this call WOULD write, or (None, None).

    Only Edit/Write/MultiEdit can be predicted: their arguments fully determine
    the result.  A `Bash` heredoc's effect is unknowable without running it, so
    Bash is denied at the permission layer and wrapped by `safe_run.py`.
    See: docs/reference/script_convert_architecture.md#what-the-gate-must-see
    """
    tool = payload.get('tool_name')
    args = payload.get('tool_input') or {}
    path = args.get('file_path')
    if not judged(path, must_exist=False):
        return None, None
    if tool == 'Write':
        return path, args.get('content') or ''
    if tool not in ('Edit', 'MultiEdit'):
        return None, None
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            text = fh.read()
    except OSError:
        return None, None
    got = _edited_text(tool, args, text)
    return (path, got) if got is not None else (None, None)


def gate_pending(path, text):
    """(exit code, report) for the text an edit WOULD leave on disk.

    Scored on a COPY beside the file, never in place: writing the candidate
    over the real file made the gate its own bypass, since being killed
    mid-write (the harness caps this hook) left unvalidated or truncated code
    behind.  The copy shares the directory so git and the rules see the same
    package, and its report is relabelled to the real path.
    """
    probe = os.path.join(os.path.dirname(path) or ROOT,
                         '.gate_candidate_%d.py' % os.getpid())
    try:
        with open(probe, 'w', encoding='utf-8', newline='') as fh:
            fh.write(text)
    except OSError as exc:
        return 2, 'The code-rules gate could not stage a candidate: %s\n' % exc
    try:
        return gate(path, candidate=probe)
    finally:
        try:
            os.remove(probe)
        except OSError:
            pass


def gate_bash(payload):
    """Route every shell command through the wrapper, which gates its writes.

    A `deny` rule cannot carry a whitelist exception -- deny beats allow at
    any specificity, and beats a hook's own `allow` decision too, so the
    wrapper could never be reached that way.  Exit 2 is evaluated BEFORE the
    permission rules, which makes this hook the only place the rule can live.
    See: docs/reference/script_convert_architecture.md#what-the-gate-must-see
    """
    command = ((payload.get('tool_input') or {}).get('command') or '').strip()
    if not command or WRAPPER in command.replace('\\', '/'):
        return 0
    sys.stderr.write(
        'Shell commands run through the wrapper, which gates what they '
        'write:\n\n    python %s %s\n\nA bare command can write a '
        'first-party .py that no gate ever sees.\n' % (WRAPPER, command))
    return 2


def gate_pre(payload):
    """Deny an Edit/Write that would leave a violation on code it owns."""
    path, text = pending_text(payload)
    if not path:
        return 0
    code, report = gate_pending(path, text)
    if not code:
        return 0
    if not report:
        report = '  the code-rules gate failed with no report (exit %s)\n' % code
    sys.stderr.write(report)
    sys.stderr.write(
        '\nTHIS EDIT WAS NOT APPLIED. Revise it to clear the violations '
        'above -- they are on the lines this edit touches -- then retry. Do '
        'not route around this with Bash.\n')
    return 2


def gate_written(paths):
    """True when any already-written path owns a violation, reporting each."""
    broke = False
    for path in paths:
        code, report = gate(path)
        if report and code == 0 and 'could not run' in report:
            sys.stderr.write(report)
            return False
        if code:
            sys.stderr.write(report)
            broke = True
    return broke


def main():
    """Read the hook payload, gate what was written, block on a violation."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    event = payload.get('hook_event_name')
    if not os.path.isfile(GATE):
        return 0
    if event == 'PreToolUse':
        if payload.get('tool_name') in ('Bash', 'PowerShell'):
            return gate_bash(payload)
        return gate_pre(payload)
    if touched_docs(payload) and os.path.isfile(LINKS):
        code, report = gate_docs()
        if code:
            sys.stderr.write(report)
            sys.stderr.write('\nA broken link or an unindexed doc loses the '
                             'knowledge it points at. Fix it now.\n')
            return 2
    if not gate_written(edited_paths(payload)):
        return 0
    sys.stderr.write('\nThese violations are in lines your edit changed. '
                     'Fix them, then retry the edit.\n')
    return 2


if __name__ == '__main__':
    sys.exit(main())

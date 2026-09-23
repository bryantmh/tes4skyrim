#!/usr/bin/env python3
"""Run a command, then gate every first-party `.py` it wrote.

    python tools/validate/safe_run.py <program> [args...]   # argv, verbatim
    python tools/validate/safe_run.py -c '<shell command>'  # pipes, builtins

Bash is denied at the permission layer because a command's effect cannot be
predicted before it runs, which is how a `python - <<'PY'` heredoc wrote files
the PreToolUse gate never saw.  This is the one allowed passthrough: it hashes
the tracked `.py` files, runs the command with the streams INHERITED so output
still arrives live, re-hashes, and gates whatever changed.

Exit code is the child's, unless a write left a violation -- then it is 2, so a
rule-breaking write can never be reported as success.

See: docs/reference/script_convert_architecture.md#what-the-gate-must-see
"""

import hashlib
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from tools.validate import code_rules as CR

#: Exit code the harness reads as "blocked"; the child's own code otherwise.
BLOCKED = 2

#: The shell's own code for "command not found".
NOT_FOUND = 127

#: The caller's own shell, so a `-c` string runs in the dialect it was written in.
SHELL = os.environ.get('SHELL') or os.environ.get('COMSPEC') or ''


def spawn_args(argv: list):
    """`(args, shell)` for `subprocess.run`, or None for a malformed `-c`.

    Plain arguments run as-is with no inner shell: the caller's shell already
    removed the quoting, so a second shell would re-read `(`, `|` and `$`.
    See: docs/reference/script_convert_architecture.md#the-wrapper-never-re-quotes
    """
    if argv[0] != '-c':
        return argv, False
    if len(argv) != 2:
        return None
    if not SHELL or SHELL.lower().endswith('cmd.exe'):
        return argv[1], True
    return [SHELL, '-c', argv[1]], False


def digests() -> dict:
    """`{path: sha256}` for every first-party `.py` file in the repo."""
    out = {}
    for path in CR.repo_files():
        try:
            out[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def written(before: dict, after: dict) -> list:
    """Files whose contents changed, plus any the command created."""
    return sorted(p for p in after if before.get(p) != after[p])


def gate_paths(paths: list) -> int:
    """Print the gate report for each path; 1 when any of them broke a rule."""
    worst = 0
    for path in paths:
        worst = max(worst, CR.gate_diff(path))
    return worst


def run(spawn) -> int:
    """The child's exit code; NOT_FOUND, with a hint, for a missing program."""
    args, shell = spawn
    try:
        return subprocess.run(args, shell=shell, cwd=CR.ROOT).returncode
    except FileNotFoundError:
        print('safe_run: no program %r -- a shell builtin, pipe or chain '
              'needs -c "<command>"' % args[0], file=sys.stderr)
        return NOT_FOUND


def main(argv: list) -> int:
    """Run the command in `argv` and gate what it wrote."""
    spawn = spawn_args(argv) if argv else None
    if spawn is None:
        print(__doc__, file=sys.stderr)
        return BLOCKED
    before = digests()
    code = run(spawn)
    changed = written(before, digests())
    if not changed:
        return code
    print('\n  safe_run: %d file(s) written -- gating them'
          % len(changed), file=sys.stderr)
    if not gate_paths(changed):
        return code
    print('\nTHE COMMAND WROTE CODE THAT BREAKS THE RULES. Fix the violations '
          'above; the write has already landed, so the file is dirty until '
          'you do.', file=sys.stderr)
    return BLOCKED


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

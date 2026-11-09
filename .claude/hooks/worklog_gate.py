"""Locks every tool after a compaction or resume until the whole mwscript full-run worklog is read.

SessionStart (compact/resume) arms the lock with the worklog's line count. PreToolUse refuses every
tool except a Read of that worklog while armed. PostToolUse on a successful Read records the lines
it covered; the lock lifts only when lines 1..N are all covered.
"""

import json
import os
import sys

ROOT = os.environ.get('CLAUDE_PROJECT_DIR', os.getcwd())
STATE = os.path.join(ROOT, 'temp', 'worklog_gate_state.json')
DEFAULT_LIMIT = 2000
WORKLOG = os.path.join(ROOT, 'docs', 'audits', 'mwscript_full_runs.md')


def active_worklog():
    """The worklog's path (WORKLOG), or None when it is missing."""
    return WORKLOG if os.path.exists(WORKLOG) else None


def same_file(a, b):
    """Whether two paths name the same file."""
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def load():
    """The armed state, or None."""
    try:
        with open(STATE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(state):
    """Writes the state file."""
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, 'w', encoding='utf-8') as f:
        json.dump(state, f)


def unread(state):
    """Line ranges of the worklog not yet read, as 'a-b' strings."""
    seen = set()
    for a, b in state['read']:
        seen.update(range(a, b + 1))
    gaps, start = [], None
    for n in range(1, state['lines'] + 2):
        if n <= state['lines'] and n not in seen:
            start = n if start is None else start
        elif start is not None:
            gaps.append('%d-%d' % (start, n - 1))
            start = None
    return gaps


def arm(event):
    """SessionStart: arm the lock and tell the model why."""
    path = active_worklog()
    if not path or event.get('source') not in ('compact', 'resume'):
        return 0
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = sum(1 for _ in f)
    save({'path': path, 'lines': lines, 'read': []})
    print('WORKLOG LOCK: every tool is blocked until you Read ALL %d lines of %s, in offset chunks. '
          'Do it now, first, before anything else.' % (lines, path))
    return 0


def gate(event):
    """PreToolUse: refuse everything but a Read of the worklog while armed."""
    state = load()
    if not state:
        return 0
    tool = event.get('tool_name', '')
    if tool == 'Read' and same_file(event.get('tool_input', {}).get('file_path'), state['path']):
        return 0
    sys.stderr.write('WORKLOG LOCK: read the WHOLE worklog %s before any other tool. Unread lines: %s. '
                     'Use Read with offset/limit.\n' % (state['path'], ', '.join(unread(state))))
    return 2


def record(event):
    """PostToolUse: mark the lines a successful worklog Read covered; lift the lock when all are read."""
    state = load()
    args = event.get('tool_input', {})
    if not state or event.get('tool_name') != 'Read' or not same_file(args.get('file_path'), state['path']):
        return 0
    first = max(1, int(args.get('offset') or 1))
    last = first + int(args.get('limit') or DEFAULT_LIMIT) - 1
    state['read'].append([first, min(last, state['lines'])])
    gaps = unread(state)
    if gaps:
        save(state)
        print('WORKLOG LOCK: still unread: %s' % ', '.join(gaps))
    else:
        os.remove(STATE)
        print('WORKLOG LOCK lifted: all %d lines read.' % state['lines'])
    return 0


def main():
    """Dispatches on the hook event."""
    event = json.load(sys.stdin)
    handlers = {'SessionStart': arm, 'PreToolUse': gate, 'PostToolUse': record}
    handler = handlers.get(event.get('hook_event_name'))
    return handler(event) if handler else 0


if __name__ == '__main__':
    sys.exit(main())

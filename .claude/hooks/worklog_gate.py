"""After a compaction or resume, locks a session's tools until it has re-read the run worklog IT was using.

PostToolUse on a Read/Edit/Write of a worklog remembers that worklog for the session. SessionStart
(compact/resume) arms the lock only for a session with a remembered worklog. PreToolUse then refuses that
session's tools except a Read of its worklog; the lock lifts once lines 1..N are all read. State is per
session, so one session's compaction never locks another.
"""

import json
import os
import sys

ROOT = os.environ.get('CLAUDE_PROJECT_DIR', os.getcwd())
STATE_DIR = os.path.join(ROOT, 'temp', 'worklog_gate')
SESSIONS = os.path.join(STATE_DIR, 'sessions.json')
DEFAULT_LIMIT = 2000
#: The run worklogs, one per test lane family.
WORKLOGS = tuple(os.path.join(ROOT, 'docs', 'audits', name)
                 for name in ('mwscript_full_runs.md', 'oblivion_quest_runs.md'))


def same_file(a, b):
    """Whether two paths name the same file."""
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def worklog_named(path):
    """The worklog `path` names, or None."""
    return next((w for w in WORKLOGS if same_file(path, w)), None)


def read_json(path, default):
    """The JSON in `path`, or `default`."""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, value):
    """Writes `value` to `path` as JSON."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(value, f)


def lock_path(event):
    """The session's lock file."""
    return os.path.join(STATE_DIR, 'lock_%s.json' % event.get('session_id', 'none'))


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
    """SessionStart: arm the lock when this session was using a worklog."""
    path = read_json(SESSIONS, {}).get(event.get('session_id', ''))
    if not path or not os.path.exists(path) or event.get('source') not in ('compact', 'resume'):
        return 0
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = sum(1 for _ in f)
    write_json(lock_path(event), {'path': path, 'lines': lines, 'read': []})
    print('WORKLOG LOCK: every tool is blocked until you Read ALL %d lines of %s (the worklog this session '
          'was using), in offset chunks. Do it now, first.' % (lines, path))
    return 0


def gate(event):
    """PreToolUse: refuse everything but a Read of the session's worklog while armed."""
    state = read_json(lock_path(event), None)
    if not state:
        return 0
    if event.get('tool_name') == 'Read' and same_file(event.get('tool_input', {}).get('file_path'), state['path']):
        return 0
    sys.stderr.write('WORKLOG LOCK: read the WHOLE worklog %s before any other tool. Unread lines: %s. '
                     'Use Read with offset/limit.\n' % (state['path'], ', '.join(unread(state))))
    return 2


def remember(event, path):
    """Notes `path` as the worklog this session is using."""
    sessions = read_json(SESSIONS, {})
    if sessions.get(event.get('session_id')) != path:
        sessions[event.get('session_id', 'none')] = path
        write_json(SESSIONS, sessions)


def record(event):
    """PostToolUse: remember a touched worklog; mark the lines a Read covered and lift a finished lock."""
    args = event.get('tool_input', {})
    path = worklog_named(args.get('file_path'))
    if not path:
        return 0
    remember(event, path)
    state = read_json(lock_path(event), None)
    if not state or event.get('tool_name') != 'Read' or not same_file(path, state['path']):
        return 0
    first = max(1, int(args.get('offset') or 1))
    last = first + int(args.get('limit') or DEFAULT_LIMIT) - 1
    state['read'].append([first, min(last, state['lines'])])
    gaps = unread(state)
    if gaps:
        write_json(lock_path(event), state)
        print('WORKLOG LOCK: still unread: %s' % ', '.join(gaps))
    else:
        os.remove(lock_path(event))
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

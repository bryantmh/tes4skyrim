#!/usr/bin/env python3
"""Record several quests' Papyrus state from ONE bridge connection.

The game bridge pipe accepts a single client at a time, so running
`quest_debug.py record` once per quest fails with E_NO_PIPE for every
recorder after the first.  This polls all the named quests over one
connection and writes a single merged timeline, which is also what you want
when the bug spans several quests (the Arena announcer chain is Arena +
ArenaAnnouncer + ArenaDialogue).

    python tools/live/quest_multi_record.py --quests Arena ArenaAnnouncer \
        --seconds 1800 --out temp/run.txt

Only CHANGES are written, so a long idle stretch costs a line, not a file.
Prints as it goes so a truncated run is still readable.

`--commands` polls extra console commands (a condition function on a ref, a
full `sqv` for alias fills) and logs each output line that appears or goes.
Creating `<out>.stacks` while recording runs `dumpstacks` once on this same
connection (the pipe takes one client) and deletes the file.
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, __file__.rsplit('tools', 1)[0])
from tools.live.game_bridge import Bridge


_VAR_RE = re.compile(r'^\s*(::\w+|TES4_\w+)\s*=\s*(.+?)\s*$')


def snapshot(bridge, quest: str) -> dict:
    """Papyrus variables + engine state for one quest, as a flat dict."""
    try:
        r = bridge.console(f'sqv {quest}')
    except Exception as exc:                      # bridge hiccup, keep going
        return {'_error': str(exc)}
    text = r if isinstance(r, str) else (r.get('output') or r.get('text') or '')
    out = {}
    for line in text.splitlines():
        m = _VAR_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
        elif 'Current stage:' in line:
            out['_stage'] = line.split(':', 1)[1].strip()
        elif line.strip().startswith('State:'):
            out['_state'] = line.split(':', 1)[1].strip()
    return out


def command_lines(bridge, command: str) -> list:
    """The non-empty output lines of one console command; a bridge error is one line.

    `@<refid> <command>` runs the command on that reference.
    """
    ref = None
    if command.startswith('@'):
        ref, command = command[1:].split(' ', 1)
    try:
        r = bridge.console(command, ref=ref)
    except Exception as exc:
        return [f'error: {exc}']
    text = r if isinstance(r, str) else (r.get('output') or r.get('text') or '')
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def poll_commands(bridge, commands: list, prev: dict, emit) -> None:
    """Log each command's output lines that appeared or vanished since the last poll."""
    for c in commands:
        cur = command_lines(bridge, c)
        old = prev.get(c)
        if old is None:
            emit(f'[{c}] BASELINE ' + ' | '.join(cur))
        else:
            gone = [ln for ln in old if ln not in cur]
            new = [ln for ln in cur if ln not in old]
            if gone or new:
                emit(f'[{c}] -{gone} +{new}')
        prev[c] = cur


def poll_quests(bridge, prev: dict, emit) -> None:
    """Log every quest variable that changed since the last poll."""
    for q, old in prev.items():
        cur = snapshot(bridge, q)
        if not cur:
            continue
        for k in sorted(set(cur) | set(old)):
            a, b = old.get(k), cur.get(k)
            if a != b:
                emit(f'{q}.{k}: {a} -> {b}')
        prev[q] = cur


def dump_stacks_if_asked(bridge, trigger: str, emit) -> None:
    """Run `dumppapyrusstacks` once when the trigger file exists, then remove it."""
    if os.path.exists(trigger):
        os.remove(trigger)
        command_lines(bridge, 'dumppapyrusstacks')
        emit('dumpstacks written to the Papyrus log')


def main() -> int:
    ap = argparse.ArgumentParser(
        description='Record several quests from one bridge connection.')
    ap.add_argument('--quests', nargs='+', required=True)
    ap.add_argument('--commands', nargs='*', default=[])
    ap.add_argument('--seconds', type=float, default=1800)
    ap.add_argument('--interval', type=float, default=1.0)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    bridge = Bridge()
    prev = {q: {} for q in args.quests}
    prev_cmd = {}
    start = time.time()
    fh = open(args.out, 'w', encoding='utf-8')

    def emit(msg: str) -> None:
        stamp = f'[{time.time() - start:7.1f}s] {msg}'
        print(stamp, flush=True)
        fh.write(stamp + '\n')
        fh.flush()

    emit(f'recording {", ".join(args.quests)} '
         f'for {args.seconds:.0f}s @ {args.interval}s')
    for q in args.quests:
        prev[q] = snapshot(bridge, q)
        emit(f'{q} BASELINE stage={prev[q].get("_stage")} '
             f'state={prev[q].get("_state")} vars={len(prev[q])}')
    poll_commands(bridge, args.commands, prev_cmd, emit)

    while time.time() - start < args.seconds:
        time.sleep(args.interval)
        poll_quests(bridge, prev, emit)
        poll_commands(bridge, args.commands, prev_cmd, emit)
        dump_stacks_if_asked(bridge, args.out + '.stacks', emit)
    emit('done')
    fh.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

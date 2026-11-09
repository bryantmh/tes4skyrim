"""Every runner `walk` in MorrowindRuntime logs, as JSON lines: plugins, place, start, target, samples, outcome.

    python tools/navmesh/walk_outcomes.py "<log glob>"... > walks.jsonl

The calibration set for the offline pathfinder: a walk is `reached` when the runner logged its end within
ARRIVED of the target, `stalled` when its case failed with "never reached" that target, else `other`
(ended early by a greeting, a door or a menu). Positions come from the runner's own lines.
"""

import glob
import json
import re
import sys

#: The runner's arrival slack, game units (game_calls_ai.cpp kArrivedDistance).
ARRIVED = 128.0

NUM = r'(-?[\d.]+(?:e[-+]?\d+)?)'
TRAVEL = re.compile(rf'ai: player travels to \({NUM}, {NUM}, {NUM}\)')
SAMPLE = re.compile(rf'ai: the player walks at \({NUM}, {NUM}, {NUM}\)')
ENDS = re.compile(rf"ai: the player's walk ends at \({NUM}, {NUM}\)")
MOVE = re.compile(rf"move: player -> (?:the exterior )?'([^']*)' \({NUM}, {NUM}, {NUM}\)")
CELL = re.compile(r"cell: the player is in '([^']*)'")
PLUGIN = re.compile(r'store:\s+([^/]+)/DIAL\.txt loaded')
NEVER = re.compile(rf'never reached {NUM} {NUM}')


class Reader(object):
    """Walks one log's lines, keeping the player's last known place and position."""

    def __init__(self, path):
        self.path, self.plugins, self.place, self.at, self.walk, self.out = path, [], '', None, None, []

    def close(self, outcome, end):
        """Finishes the open walk with `outcome`, ending at `end`."""
        if self.walk:
            self.walk.update(outcome=outcome, end=end)
            self.out.append(self.walk)
            self.walk = None

    def feed(self, line):
        """Updates state from one log line."""
        m = TRAVEL.search(line)
        if m:
            self.close('other', self.at)
            self.walk = dict(log=self.path, plugins=list(self.plugins), place=self.place, start=self.at,
                             target=[float(v) for v in m.groups()], samples=[])
            return
        self.position(line)
        self.ending(line)

    def position(self, line):
        """Tracks the player's place and position from moves, cell changes and walk samples."""
        m = PLUGIN.search(line)
        if m and m.group(1) not in self.plugins:
            self.plugins.append(m.group(1))
        m = MOVE.search(line)
        if m:
            self.place, self.at = m.group(1), [float(v) for v in m.groups()[1:]]
        m = CELL.search(line)
        if m:
            self.place = m.group(1)
        m = SAMPLE.search(line)
        if m:
            self.at = [float(v) for v in m.groups()]
            if self.walk:
                self.walk['samples'].append(self.at)

    def ending(self, line):
        """Closes the open walk on its logged end or on its case's "never reached" failure."""
        m = ENDS.search(line)
        if m and self.walk:
            x, y = float(m.group(1)), float(m.group(2))
            tx, ty = self.walk['target'][:2]
            near = (x - tx) ** 2 + (y - ty) ** 2 <= ARRIVED * ARRIVED
            self.close('reached' if near else 'other', [x, y, self.at[2] if self.at else 0.0])
            return
        m = NEVER.search(line)
        last = self.walk or (self.out[-1] if self.out else None)
        if m and last and [float(m.group(1)), float(m.group(2))] == [round(v) for v in last['target'][:2]]:
            if last is self.walk:
                self.close('stalled', self.at)
            last['outcome'] = 'stalled'


def read_log(path):
    """The walks of one log."""
    reader = Reader(path)
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            reader.feed(line)
    reader.close('other', reader.at)
    return reader.out


def main():
    """Prints every walk of every log named or globbed, one JSON object per line."""
    for pattern in sys.argv[1:]:
        for path in sorted(glob.glob(pattern)):
            for walk in read_log(path):
                print(json.dumps(walk))


if __name__ == '__main__':
    main()

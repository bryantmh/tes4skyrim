"""The silences between a TES4 case's spoken lines: the harness's answer to `expect quiet <= N`.

The runner stamps each line an actor begins, and each message box or chargen menu that opens, with real
and game milliseconds. A line lasts its own voice file's length, read from the plugin's export. The
silence is what a player at normal speed would hear, worked out at any test speed: game time is what
script timers count, while the audio plays in real time.

See: docs/commentary/headless_testing.md#quiet
"""
import os
import re

from asset_convert.audio.mp3_length import mp3_duration
from asset_convert.game_paths import find_nocase
from output_layout import assets_for
from tools.live import tes4_cases

#: The judge condition this module answers: `quiet <= N [since <text of the first line measured>]`.
QUIET = re.compile(r'^\s*quiet\s*(<=|<)\s*(\d+(?:\.\d+)?)(?:\s+since\s+(.+?))?\s*$', re.I)
#: A voice file's INFO and response number: `<quest>_<topic>_<fid>_<n>.mp3`.
VOICE_NAME = re.compile(r'_([0-9a-fA-F]{8})_(\d+)\.mp3$', re.I)
INFO_FIELD = re.compile(r'^(FormID|Response\[(\d+)\]\.(ResponseNumber|ResponseText))=(.*)$')


def _norm(text: str) -> str:
    """`text` compared case- and spacing-blind."""
    return ' '.join(text.lower().split())


def export_dirs(plugins_txt: str) -> list:
    """The export folder of every converted plugin the instance loads, in load order."""
    out = []
    for name in tes4_cases.load_order(plugins_txt):
        path = tes4_cases.converted_esm(name)
        folder = os.path.join(tes4_cases.REPO, 'export', os.path.basename(os.path.dirname(path))) if path else ''
        if folder and os.path.isdir(folder):
            out.append(folder)
    return out


def _voice_files(export_dir: str) -> dict:
    """(INFO low 24 bits, response number) -> its voice files, every voice type."""
    root = find_nocase(assets_for(export_dir), 'sound', 'voice', kind=os.path.isdir)
    files = {}
    for folder, _dirs, names in os.walk(root) if root else ():
        for name in names:
            found = VOICE_NAME.search(name)
            if found:
                key = (int(found.group(1), 16) & 0xFFFFFF, int(found.group(2)))
                files.setdefault(key, []).append(os.path.join(folder, name))
    return files


def _responses(export_dir: str) -> dict:
    """Each response's text, compared blind -> [(INFO low 24 bits, response number)]."""
    texts, fid, numbers = {}, 0, {}
    path = os.path.join(export_dir, 'INFO.txt')
    if not os.path.isfile(path):
        return texts
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            found = INFO_FIELD.match(line.rstrip('\n'))
            if not found:
                continue
            if found.group(1) == 'FormID':
                fid, numbers = int(found.group(4), 16) & 0xFFFFFF, {}
            elif found.group(3) == 'ResponseNumber':
                numbers[found.group(2)] = int(found.group(4) or 0)
            else:
                number = numbers.get(found.group(2), int(found.group(2)) + 1)
                texts.setdefault(_norm(found.group(4)), []).append((fid, number))
    return texts


class Lengths:
    """Spoken text -> its voice file's length in seconds, over the instance's converted plugins."""

    def __init__(self, folders: list):
        """Indexes nothing until the first lookup."""
        self.folders, self.index, self.cache = folders, None, {}

    def responses(self, text: str) -> set:
        """(INFO low 24 bits, response number) of every response with this text."""
        if self.index is None:
            self.index = [(_responses(d), _voice_files(d)) for d in self.folders]
        return {at for texts, _files in self.index for at in texts.get(_norm(text), ())}

    def follows(self, text: str, after: str) -> bool:
        """Whether `after` can be the response that follows `text`'s in one INFO."""
        later = self.responses(after)
        return any((fid, number + 1) in later for fid, number in self.responses(text))

    def seconds(self, text: str):
        """The longest voice file of any response with this text, or None when none is voiced."""
        if self.index is None:
            self.index = [(_responses(d), _voice_files(d)) for d in self.folders]
        key = _norm(text)
        if key not in self.cache:
            paths = [p for texts, files in self.index for at in texts.get(key, ()) for p in files.get(at, ())]
            self.cache[key] = max((mp3_duration(p) for p in paths), default=None)
        return self.cache[key]


class Silences:
    """The lines and menus of the case under way, and the longest silence between them."""

    def __init__(self, lengths: Lengths):
        """No case under way yet."""
        self.lengths, self.events = lengths, []

    def note(self, line: dict) -> None:
        """Keeps a spoken line or opened menu; a finished case's line starts the next case afresh."""
        if 'case' in line:
            self.events = []
        elif 'ms' in line:
            self.events.append(line)

    def since(self, text: str) -> list:
        """The case's events from the first line containing `text` (all of them for none), else []."""
        if not text:
            return self.events
        want = _norm(text)
        start = next((i for i, e in enumerate(self.events) if want in _norm(e.get('text', ''))), None)
        return [] if start is None else self.events[start:]

    def longest(self, text: str = '') -> tuple:
        """(seconds, what) of the longest silence from a line's end to the next line or menu, from `text`'s line."""
        events = self.since(text)
        worst, what = 0.0, 'no silence between lines' if events else 'no line contains %r' % text
        for before, after in zip(events, events[1:]):
            if 'text' not in before or self.lengths.follows(before['text'], after.get('text', '')):
                continue
            length = self.lengths.seconds(before['text'])
            gap = silence(before, after, length or 0.0)
            if gap > worst:
                worst = gap
                what = '%.1f s after %r (%s) until %r' % (
                    gap, before['text'], 'no voice file' if length is None else '%.2f s line' % length,
                    after.get('text') or after.get('menu'))
        return worst, what

    def judge(self, condition: str) -> str:
        """The runner's answer to `quiet <= N [since <text>]`: `<1|0> <longest silence>`, `-1 null` with no such line."""
        op, limit, text = QUIET.match(condition).groups()
        if not self.since(text):
            print('quiet: no line contains %r' % text)
            return '-1 null'
        worst, what = self.longest(text)
        print('quiet: longest silence %s' % what)
        holds = worst <= float(limit) if op == '<=' else worst < float(limit)
        return '%d %.2f' % (holds, worst)


def silence(line: dict, after: dict, length: float) -> float:
    """Seconds of silence a player at normal speed hears between `line` (`length` s long) and `after`.

    Arriving while the audio still played, `after` was released by game time alone, so the silence is
    the game time between them less the line. Arriving later, it may have waited on the audio, so the
    smaller of the real and game time after the audio ended counts. Without a game clock, real time.
    See: docs/commentary/headless_testing.md#quiet
    """
    real = (after['ms'] - line['ms']) / 1000.0
    game = (after.get('game', 0) - line.get('game', 0)) / 1000.0
    if not line.get('game') or not after.get('game') or real <= 0:
        return real - length
    if real <= length:
        return game - length
    return min(real - length, game - length * max(game / real, 1.0))

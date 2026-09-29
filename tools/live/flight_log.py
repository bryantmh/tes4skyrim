#!/usr/bin/env python3
"""Read the game bridge's flight recorder: what the engine did, with names.

The bridge DLL writes one JSON line per script event (quest stages, dialogue
lines, packages, trigger boxes, activations, cells, Papyrus errors) to
Documents/My Games/Skyrim Special Edition/SKSE/TESGameBridge_events.jsonl
while the game is played, and rotates older runs to .1 and .2. This names the
form ids from the plugins given with --plugin; each plugin's load-order prefix
is detected from the events themselves.

    python tools/live/flight_log.py show --plugin <FalloutNV.esm> [--kinds quest_stage,topic_info]
    python tools/live/flight_log.py show --run 1 --grep VCG01
    python tools/live/flight_log.py status        # live: what the DLL has hooked

See: game_bridge/README.md#flight-recorder
"""

import argparse
import json
import mmap
import re
import struct
import sys
import zlib
from collections import Counter
from pathlib import Path

sys.path.insert(0, __file__.rsplit('tools', 1)[0])
from tools.live.game_bridge import Bridge, BridgeError
from tools.script.papyrus_tail import documents_dir

#: Event fields holding a reference id; `<field>_base` holds its base object.
REF_FIELDS = ('speaker', 'actor', 'trigger', 'target', 'by', 'furniture', 'killer')
#: Event fields holding a plain form id.
FORM_FIELDS = ('quest', 'info', 'package', 'cell', 'item', 'from', 'to')
#: Fields that are bookkeeping, not event content.
HIDDEN = ('t', 'ms', 'ev', 'raw', 'forms')
_COMPRESSED = 0x00040000
_LOCALIZED = 0x80
_TOPIC_CHILDREN = 7


# ----------------------------------------------------------------------------
# events
# ----------------------------------------------------------------------------


def events_path(run: int) -> Path:
    """The recorder's log for run 0 (newest), 1 or 2."""
    suffix = '' if run == 0 else f'.{run}'
    return (documents_dir() / 'My Games' / 'Skyrim Special Edition' / 'SKSE'
            / f'TESGameBridge_events{suffix}.jsonl')


def read_events(path: Path) -> list:
    """Every complete JSON line; a torn last line from a live game is skipped."""
    events = []
    with open(path, encoding='utf-8', errors='replace') as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def event_ids(events: list) -> set:
    """Every form id (as int) any event names, bases included."""
    ids = set()
    for ev in events:
        for key, value in ev.items():
            name = key[:-5] if key.endswith('_base') else key
            if name in REF_FIELDS + FORM_FIELDS and isinstance(value, str):
                ids.add(int(value, 16))
    return ids


# ----------------------------------------------------------------------------
# plugins
# ----------------------------------------------------------------------------


def _subrecords(data: bytes):
    """Yield (tag, payload) for a record body, honoring XXXX sizes."""
    pos, big = 0, None
    while pos + 6 <= len(data):
        tag = data[pos:pos + 4]
        size = struct.unpack_from('<H', data, pos + 4)[0]
        if big is not None:
            size, big = big, None
        payload = data[pos + 6:pos + 6 + size]
        pos += 6 + size
        if tag == b'XXXX':
            big = struct.unpack_from('<I', payload)[0]
            continue
        yield tag, payload


def _cstring(payload: bytes) -> str:
    """A zero-terminated string subrecord as text."""
    return payload.split(b'\0', 1)[0].decode('cp1252', errors='replace')


class Plugin:
    """Record headers of one ESM/ESP, and names read from them on demand."""

    def __init__(self, path: Path):
        """Map the file and index every record's offset and topic parent."""
        self.path = Path(path)
        self._file = open(self.path, 'rb')
        self.mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        self.offsets, self.parent = {}, {}
        self.masters, self.localized = self._header()
        self.own_index = len(self.masters)
        self.prefix = None
        self._index()

    def _header(self):
        """(master names, localized flag) from the TES4 record."""
        size = struct.unpack_from('<I', self.mm, 4)[0]
        flags = struct.unpack_from('<I', self.mm, 8)[0]
        body = self.mm[24:24 + size]
        masters = [_cstring(p) for t, p in _subrecords(body) if t == b'MAST']
        return masters, bool(flags & _LOCALIZED)

    def _index(self):
        """Walk every header once; records are not decompressed here."""
        mm, pos, end, stack = self.mm, 0, len(self.mm), []
        while pos + 24 <= end:
            while stack and pos >= stack[-1][0]:
                stack.pop()
            size = struct.unpack_from('<I', mm, pos + 4)[0]
            if mm[pos:pos + 4] == b'GRUP':
                label, gtype = struct.unpack_from('<Ii', mm, pos + 8)
                stack.append((pos + size, gtype, label))
                pos += 24
                continue
            fid = struct.unpack_from('<I', mm, pos + 12)[0]
            self.offsets[fid] = pos
            if stack and stack[-1][1] == _TOPIC_CHILDREN:
                self.parent[fid] = stack[-1][2]
            pos += 24 + size

    def fields(self, local: int) -> dict:
        """The subrecords this reader names things by, for one record."""
        pos = self.offsets.get(local)
        if pos is None:
            return {}
        sig = self.mm[pos:pos + 4].decode('ascii', errors='replace')
        size, flags = struct.unpack_from('<II', self.mm, pos + 4)
        data = self.mm[pos + 24:pos + 24 + size]
        if flags & _COMPRESSED:
            data = zlib.decompress(data[4:])
        out = {'sig': sig}
        for tag, payload in _subrecords(data):
            key = tag.decode('ascii', errors='replace')
            if key in ('EDID', 'NAM1', 'FULL') and key not in out:
                out[key] = None if self.localized and key != 'EDID' else _cstring(payload)
            elif key in ('NAME', 'DNAM') and key not in out and len(payload) == 4:
                out[key] = struct.unpack_from('<I', payload)[0]
        return out

    def local_id(self, runtime: int):
        """This plugin's own id for a runtime id, or None if not its record."""
        if self.prefix is None:
            return None
        if runtime >> 24 == 0xFE:
            if (runtime >> 12) != self.prefix:
                return None
            return (self.own_index << 24) | (runtime & 0xFFF)
        if runtime >> 24 != self.prefix:
            return None
        return (self.own_index << 24) | (runtime & 0xFFFFFF)

    def detect_prefix(self, ids: set) -> None:
        """Pick the load-order prefix under which most event ids exist here."""
        votes = Counter()
        for rid in ids:
            key = rid >> 12 if rid >> 24 == 0xFE else rid >> 24
            low = rid & (0xFFF if rid >> 24 == 0xFE else 0xFFFFFF)
            if key not in (0, 0xFF) and (self.own_index << 24) | low in self.offsets:
                votes[key] += 1
        self.prefix = votes.most_common(1)[0][0] if votes else None


# ----------------------------------------------------------------------------
# naming
# ----------------------------------------------------------------------------


class Namer:
    """Runtime form id -> readable name, across the given plugins."""

    def __init__(self, plugins: list):
        """Keep the plugins and a cache of names already built."""
        self.plugins = plugins
        self.cache = {}

    def _record(self, runtime: int):
        """(plugin, fields) for the plugin that defines this id, or (None, {})."""
        for plugin in self.plugins:
            local = plugin.local_id(runtime)
            if local is not None and local in plugin.offsets:
                return plugin, plugin.fields(local)
        return None, {}

    def name(self, runtime: int) -> str:
        """`EDID [id]`, a dialogue line's text and topic, or the bare id."""
        if runtime in self.cache:
            return self.cache[runtime]
        plugin, rec = self._record(runtime)
        label = rec.get('EDID') or ''
        if rec.get('sig') == 'INFO':
            label = self._info_label(plugin, runtime, rec)
        elif not label and rec.get('sig') in ('REFR', 'ACHR') and 'NAME' in rec:
            label = f"ref of {self._base_name(plugin, rec['NAME'])}"
        text = f'{label} [{runtime:08X}]' if label else f'[{runtime:08X}]'
        self.cache[runtime] = text
        return text

    def _info_label(self, plugin, runtime: int, rec: dict) -> str:
        """`TOPIC_EDID: "first line of the response..."`."""
        topic = plugin.parent.get(plugin.local_id(runtime))
        topic_rec = plugin.fields(topic) if topic is not None else {}
        if not rec.get('NAM1') and rec.get('DNAM') in plugin.offsets:
            rec = plugin.fields(rec['DNAM'])
        line = (rec.get('NAM1') or '').strip()
        if len(line) > 70:
            line = line[:67] + '...'
        return f"{topic_rec.get('EDID', 'INFO')}: \"{line}\""

    def _base_name(self, plugin, local_base: int) -> str:
        """Editor id of a reference's base, read in the plugin's own ids."""
        if local_base >> 24 == plugin.own_index and local_base in plugin.offsets:
            return plugin.fields(local_base).get('EDID') or f'{local_base:08X}'
        return f'{local_base:08X}'


def render(ev: dict, namer: Namer, raw: bool) -> str:
    """One event as `time  kind  field=value ...`."""
    parts = []
    for key, value in ev.items():
        if key in HIDDEN and not (raw and key in ('raw', 'forms')):
            continue
        if key.endswith('_base'):
            continue
        if key in REF_FIELDS + FORM_FIELDS and isinstance(value, str):
            value = namer.name(int(value, 16))
        elif key.endswith('_check'):
            key = '!' + key
        parts.append(f'{key}={value}')
    return f"{ev.get('t', '?'):>12}  {ev.get('ev', '?'):<14} {'  '.join(parts)}"


# ----------------------------------------------------------------------------
# cli
# ----------------------------------------------------------------------------


def cmd_show(args) -> int:
    """Print the recorded events, named and filtered."""
    path = Path(args.path) if args.path else events_path(args.run)
    if not path.exists():
        print(f'no recorder log at {path}', file=sys.stderr)
        return 1
    events = read_events(path)
    if args.kinds:
        wanted = set(args.kinds.split(','))
        events = [e for e in events if e.get('ev') in wanted]
    plugins = [Plugin(p) for p in args.plugin]
    ids = event_ids(events)
    for plugin in plugins:
        plugin.detect_prefix(ids)
        print(f'# {plugin.path.name}: prefix '
              f"{'%02X' % plugin.prefix if plugin.prefix is not None else 'not found'}",
              file=sys.stderr)
    namer = Namer(plugins)
    lines = [render(e, namer, args.raw) for e in events]
    if args.grep:
        pattern = re.compile(args.grep, re.IGNORECASE)
        lines = [line for line in lines if pattern.search(line)]
    for line in lines[-args.last:] if args.last else lines:
        print(line.encode(sys.stdout.encoding or 'utf-8', 'replace').decode(
            sys.stdout.encoding or 'utf-8'))
    print(f'# {len(lines)} event(s) from {path}', file=sys.stderr)
    return 0


def cmd_status(_args) -> int:
    """Ask the running game's bridge what the recorder has hooked and written."""
    try:
        with Bridge().connect() as bridge:
            out = bridge.recorder()
    except BridgeError as exc:
        print(f'error [{exc.code}]: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0


def main(argv=None) -> int:
    """Parse the command line and run one subcommand."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    show = sub.add_parser('show', help='print the recorded events')
    show.add_argument('--run', type=int, default=0, choices=(0, 1, 2),
                      help='0 = newest run, 1 and 2 = the ones before')
    show.add_argument('--path', help='read this file instead')
    show.add_argument('--plugin', action='append', default=[],
                      help='an ESM/ESP to name forms from (repeatable)')
    show.add_argument('--kinds', help='comma-separated event kinds to keep')
    show.add_argument('--grep', help='keep lines matching this regex')
    show.add_argument('--last', type=int, help='only the last N lines')
    show.add_argument('--raw', action='store_true', help='show raw bytes on failed checks')
    sub.add_parser('status', help="the live recorder's hooks and counts")
    args = ap.parse_args(argv)
    return cmd_show(args) if args.cmd == 'show' else cmd_status(args)


if __name__ == '__main__':
    sys.exit(main())

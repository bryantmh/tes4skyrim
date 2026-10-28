"""The harness's half of a TES4 quest run: names, console lines and conditions.

A `.tes4test` case names the ORIGINAL TES4 EditorIDs. Skyrim keeps no
EditorID for a placed reference or most base objects at runtime, so the
harness reads each converted plugin of the instance's load order once,
turns every name into its runtime FormID, and answers the runner's `run`
and `judge` requests through the bridge console.

See: docs/commentary/headless_testing.md#tes4-quest-cases
"""
import os
import re
import struct

from tes5_import.base.tes5_reader import masters, subrecords, walk, zstr
from tools.live.game_bridge import Bridge, BridgeError
from tools.live.quest_debug import parse_sqv

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#: Always loaded before Plugins.txt, in this order; each takes one load index.
VANILLA_MASTERS = ('skyrim.esm', 'update.esm', 'dawnguard.esm', 'hearthfires.esm', 'dragonborn.esm')
#: The TES4 header flag that makes a plugin light: it takes no full load index.
LIGHT_FLAG = 0x200
#: Steps whose argument is a single reference the runner acts on.
TARGET_STEPS = ('talk', 'activate', 'take')
COMPARE = re.compile(r'^(.*?)\s*(==|!=|>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$')
QUEST_VAR = re.compile(r'^([A-Za-z_]\w*)\.([A-Za-z_]\w*)$')
TARGETED = re.compile(r'^([A-Za-z_]\w*)\.(.+)$')
NUMBER = re.compile(r'(?:>>|=)\s*(-?\d+(?:\.\d+)?)')
TOKEN = re.compile(r'\w+')
HEX_ID = re.compile(r'^[0-9A-Fa-f]{8}$')
POINT = re.compile(r'^-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?\s+-?\d+(?:\.\d+)?$')
#: The prefix a converted record carries when its TES4 EditorID is taken in Skyrim.esm (MQ00 -> TES4MQ00).
RENAMED = 'tes4'
#: Records the game finds by EditorID at runtime (`setstage MQ101 10`), so the console gets their name.
KEPT_NAMES = (b'QUST', b'GLOB')
#: The engine's text, which `ask` and `hear` compare bytes against: cp1252, as the plugins store it.
GAME_ENCODING = 'cp1252'
#: How `sqv` prints a Papyrus bool.
BOOLS = {'true': 1.0, 'false': 0.0}


class Names:
    """Every EditorID of the instance's converted plugins, as runtime FormIDs."""

    def __init__(self):
        """An empty index."""
        self.ids = {}
        self.quests = {}
        self.placed = {}
        #: Load index of each TES4 file a FormID's index byte names, in the plugin under test.
        self.tes4_slots = []
        #: Runtime FormID -> EditorID, made on first use.
        self.by_id = None

    def from_tes4(self, hex_id: str) -> int:
        """The runtime FormID of a TES4 FormID of the plugin under test (the last
        converted one loaded); a converted record keeps its low 24 bits."""
        fid = int(hex_id, 16)
        if not self.tes4_slots:
            return 0
        return (self.tes4_slots[min(fid >> 24, len(self.tes4_slots) - 1)] << 24) | (fid & 0xFFFFFF)

    def runtime(self, name: str) -> int:
        """The runtime FormID `name` (any case, as authored or renamed) names, or 0."""
        return self.ids.get(name.lower()) or self.ids.get(RENAMED + name.lower(), 0)

    def quest(self, name: str) -> str:
        """The converted EditorID of TES4 quest `name`, or ''."""
        return self.quests.get(name.lower()) or self.quests.get(RENAMED + name.lower(), '')

    def name_of(self, hex_id: str) -> str:
        """The EditorID behind runtime FormID `hex_id`, else the hex itself."""
        if self.by_id is None:
            self.by_id = {fid: name for name, fid in self.ids.items()}
        return self.by_id.get(int(hex_id, 16), hex_id)

    def target(self, name: str) -> str:
        """The hex runtime FormID of the reference a step acts on: a placed
        reference's EditorID or TES4 FormID, or a base whose single placement it is.

        Raises SystemExit naming the problem, before the game is launched.
        """
        fid = self.from_tes4(name) if HEX_ID.match(name) else self.runtime(name)
        refs = self.placed.get(fid)
        if refs is not None:
            if len(refs) != 1:
                raise SystemExit('%r is placed %d times; name the reference' % (name, len(refs)))
            fid = refs[0]
        if not fid:
            raise SystemExit('no converted record is named %r' % name)
        return '%08X' % fid


def load_order(plugins_txt: str) -> list:
    """The lowercased plugins holding a full load index, in index order."""
    with open(plugins_txt, encoding='utf-8') as f:
        enabled = [line.strip().lstrip('*').lower() for line in f if line.strip().startswith('*')]
    return list(VANILLA_MASTERS) + [name for name in enabled if not is_light(name)]


def converted_esm(name: str) -> str:
    """The converted plugin `name` in output/, or '' when it was not converted here."""
    folder = os.path.join(REPO, 'output')
    for entry in os.listdir(folder):
        path = os.path.join(folder, entry, entry)
        if entry.lower() == name and os.path.isfile(path):
            return path
    return ''


def is_light(name: str) -> bool:
    """Whether plugin `name` loads as a light plugin (.esl, or the ESL flag)."""
    if name.endswith('.esl'):
        return True
    path = converted_esm(name)
    if not path:
        return False
    with open(path, 'rb') as f:
        return bool(struct.unpack('<I', f.read(12)[8:12])[0] & LIGHT_FLAG)


def index_plugin(names: Names, path: str, order: list) -> None:
    """Adds one converted plugin's EditorIDs, quests and actor placements."""
    with open(path, 'rb') as f:
        raw = f.read()
    owners = masters(raw) + [os.path.basename(path).lower()]
    slots = {i: order.index(owner) for i, owner in enumerate(owners) if owner in order}
    names.tes4_slots = [order.index(o) for o in owners if o not in VANILLA_MASTERS and o in order]

    def runtime(fid: int) -> int:
        """`fid` from this file, as the running load order numbers it."""
        slot = slots.get(min(fid >> 24, len(owners) - 1))
        return 0 if slot is None else (slot << 24) | (fid & 0xFFFFFF)

    for rec, _stack in walk(raw):
        subs = subrecords(rec.body)
        if subs and subs[0][0] == b'EDID':
            name = zstr(subs[0][1])
            if rec.sig == b'QUST':
                names.quests[name.lower()] = name
            elif rec.sig not in KEPT_NAMES:
                names.ids[name.lower()] = runtime(rec.form_id)
        if rec.sig == b'ACHR':
            base = next((data for tag, data in subs if tag == b'NAME'), None)
            if base:
                names.placed.setdefault(runtime(struct.unpack('<I', base)[0]), []).append(
                    runtime(rec.form_id))


def build_names(plugins_txt: str) -> Names:
    """The name index for every converted plugin the instance loads."""
    names = Names()
    order = load_order(plugins_txt)
    for name in order:
        path = converted_esm(name)
        if path:
            index_plugin(names, path, order)
    return names


def prepare(cases: str, names: Names, out: str) -> str:
    """Writes `cases` to `out` with each `talk`/`activate`/`take` target turned
    into its runtime FormID; `out`. Fails before launch on a name no plugin has."""
    with open(cases, encoding='utf-8') as f:
        lines = [prepared_line(line, names) for line in f]
    with open(out, 'w', encoding=GAME_ENCODING) as f:
        f.writelines(lines)
    return out


def prepared_line(line: str, names: Names) -> str:
    """One case-file line with the reference it names as a runtime FormID: a
    `talk`/`activate`/`take` target, a `walk` target that is not `x y z`, and
    a `hear` speaker that is not `any`."""
    word, _, rest = line.strip().partition(' ')
    word, rest = word.lower(), rest.strip()
    if word in TARGET_STEPS and rest:
        return '%s %s\n' % (word, names.target(rest.strip('"')))
    if word == 'walk' and rest and not POINT.match(rest):
        return 'walk %s\n' % names.target(rest.strip('"'))
    parts = rest.split(None, 2)
    if word == 'hear' and len(parts) >= 2 and parts[1].lower() != 'any':
        return 'hear %s\n' % ' '.join([parts[0], names.target(parts[1])] + parts[2:])
    return line


def resolved(text: str, names: Names) -> tuple:
    """(`ref` hex or None, command) for one console line: a leading `<name>.`
    becomes the target, and every argument that is a known name its hex FormID."""
    ref, command = None, text.strip()
    targeted = TARGETED.match(command)
    if targeted and targeted.group(1).lower() != 'player':
        ref, command = names.target(targeted.group(1)), targeted.group(2)
    function, _, args = command.partition(' ')

    def swap(match) -> str:
        """A name's or TES4 FormID's runtime id, a quest's EditorID, else the token."""
        token = match.group(0)
        fid = names.runtime(token) or (names.from_tes4(token) if HEX_ID.match(token) else 0)
        return '%08X' % fid if fid else names.quest(token) or token

    return ref, (function + ' ' + TOKEN.sub(swap, args)).strip()


def console(bridge: Bridge, text: str, names: Names) -> tuple:
    """(ok, output) of one console line, names resolved; not ok when the
    engine rejected the command or the bridge failed."""
    try:
        ref, command = resolved(text, names)
        output = bridge.console(command, ref=None if ref is None else int(ref, 16))
    except (BridgeError, SystemExit) as error:
        return False, 'ERROR %s' % error
    return not Bridge.is_unknown_command(output.splitlines()), output.strip()


def run_body(bridge: Bridge, body: str, names: Names) -> str:
    """The runner's answer to a `run`: `1`, or `0 "<the failing line and output>"`."""
    for line in body.splitlines():
        if line.strip():
            ok, output = console(bridge, line, names)
            print('run %s -> %s' % (line.strip(), output))
            if not ok:
                return '0 "%s: %s"' % (line.strip().replace('"', "'"), output.replace('"', "'")[:200])
    return '1'


def left_value(bridge: Bridge, left: str, names: Names):
    """The value a condition's left side reads, or None when it does not evaluate."""
    quest_var = QUEST_VAR.match(left.strip())
    if quest_var and names.quest(quest_var.group(1)):
        _ok, output = console(bridge, 'sqv %s' % quest_var.group(1), names)
        found = {k.lower(): v.strip().lower() for k, v in parse_sqv(output)['vars'].items()}
        value = found.get(quest_var.group(2).lower(), '')
        number = re.search(r'-?\d+(?:\.\d+)?', value)
        return BOOLS.get(value, float(number.group(0)) if number else None)
    ok, output = console(bridge, left, names)
    numbers = NUMBER.findall(output) if ok else []
    return float(numbers[-1]) if numbers else None


def judge(bridge: Bridge, condition: str, names: Names) -> str:
    """The runner's answer to a `judge`: `<1|0|-1> <left value or null>`."""
    compared = COMPARE.match(condition.strip())
    left, op, right = compared.groups() if compared else (condition, '!=', '0')
    value = left_value(bridge, left, names)
    if value is None:
        return '-1 null'
    holds = {'==': value == float(right), '!=': value != float(right), '>=': value >= float(right),
             '<=': value <= float(right), '>': value > float(right), '<': value < float(right)}[op]
    return '%d %g' % (holds, value)


def print_spoken(line: dict, names: Names) -> None:
    """Prints a line an actor began to speak, its speaker by EditorID."""
    print('  [%s tick %s] %s: %s' % (line['during'], line['tick'], names.name_of(line['speaker']), line['text']))


def answer(bridge: Bridge, request: dict, names: Names) -> str:
    """The answer-file text for one runner request line."""
    if 'run' in request:
        return run_body(bridge, request['run'], names)
    verdict = judge(bridge, request['judge'], names)
    print('judge %s -> %s' % (request['judge'], verdict))
    return verdict


class PapyrusLog:
    """The Papyrus log's error lines, read as they arrive."""

    def __init__(self, path: str):
        """Starts at the log's current end."""
        self.path = path
        self.offset = os.path.getsize(path) if os.path.exists(path) else 0

    def new_errors(self) -> list:
        """The error lines written since the last call."""
        if not os.path.exists(self.path):
            return []
        if os.path.getsize(self.path) < self.offset:
            self.offset = 0
        with open(self.path, encoding='utf-8', errors='replace') as f:
            f.seek(self.offset)
            text = f.read()
            self.offset = f.tell()
        return [line for line in text.splitlines() if 'error' in line.lower()]

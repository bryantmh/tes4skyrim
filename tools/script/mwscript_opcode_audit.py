"""Which MWScript commands the Morrowind runtime implements, and what each costs.

Cross-references three sources so the answer is measured rather than recalled:

  registrations  external/openmw/components/compiler/extensions0.cpp -- the
                 script-facing name, argument signature and return type
  installed      plugin/script_runner.cpp -- which opcode constants get a real
                 `Real<...>` / `Real3<...>` install rather than a stub
  call sites     the INFO result scripts in an export's MWIN.txt, so an
                 unported command is ranked by how much dialogue it actually
                 breaks

Usage:
    python tools/script/mwscript_opcode_audit.py --export "export/<plugin>"
    python tools/script/mwscript_opcode_audit.py --export ... --todo --top 40
    python tools/script/mwscript_opcode_audit.py --export ... --tsv out.tsv
"""

import argparse
import collections
import os
import re
import sys

REGISTRATIONS = 'external/openmw/components/compiler/extensions0.cpp'
RUNNER = 'tes_runtime/morrowind_runtime/plugin/script_runner.cpp'
#: The other installers, by SHAPE so a new `script_ops_*.cpp` is never missed.
RUNNER_PARTS_GLOB = 'tes_runtime/morrowind_runtime/plugin/script_ops_*.cpp'
SCRIPT_FIELD = 'ResultScript'

#: The OTHER corpus: object scripts, whose body is SCPT's `SCTX`.
SCRIPT_EXPORT = 'SCPT.txt'
OBJECT_SCRIPT_FIELD = 'SCTX'

_INSTR = re.compile(r'registerInstruction\s*\(\s*"([^"]+)"\s*,\s*"([^"]*)"'
                    r'\s*,\s*([A-Za-z0-9_:]+)', re.S)
_FUNC = re.compile(r'registerFunction\s*\(\s*"([^"]+)"\s*,\s*\'(\w)\'\s*,'
                   r'\s*"([^"]*)"\s*,\s*([A-Za-z0-9_:]+)', re.S)
_NAMESPACE = re.compile(r'namespace\s+(\w+)\s*\{')
#: `Real<Op<Implicit>>(D::opcodeX)`, or `(S::opcodeX + Which)` for a family.
_INSTALL = re.compile(r'\bReal3?\s*<.*?>\s*\(\s*\n?\s*([A-Za-z0-9_:]+)'
                      r'(?:\s*\+\s*\w+)?\s*\)')
#: A helper whose ARGUMENTS name the opcodes: `InstallFamily` and `InstallPair<Op>`.
_FAMILY = re.compile(r'\bInstall(?:Family|Pair)\s*(?:<.*?>)?\s*\((.*?)\)\s*;',
                     re.S)
_OPCODE = re.compile(r'\bopcode\w+')
#: `kDeliberateNoOps` in script_runner.cpp: nothing to port, by design.
_NOOPS = re.compile(r'kDeliberateNoOps\[\]\s*=\s*\{(.*?)\}', re.S)

#: `static const char* dynamics[...] = { "health", ... };` -- a name array.
_ARRAY = re.compile(r'static\s+const\s+char\*\s+(\w+)\s*\[[^\]]*\]\s*='
                    r'\s*\{(.*?)\}\s*;', re.S)
#: `registerFunction(get + dynamics[i], 'f', "x", opcodeGetDynamic + i, ...)`
_LOOP_FUNC = re.compile(r'registerFunction\s*\(\s*(\w+)\s*\+\s*(\w+)\[i\]'
                        r'[^;]*?\'(\w)\'\s*,\s*"([^"]*)"\s*,'
                        r'\s*([A-Za-z0-9_]+)\s*\+\s*i', re.S)
#: The instruction form of the same loop, which has no return type.
_LOOP_INSTR = re.compile(r'registerInstruction\s*\(\s*(\w+)\s*\+\s*(\w+)\[i\]'
                         r'\s*,\s*"([^"]*)"\s*,'
                         r'\s*([A-Za-z0-9_]+)\s*\+\s*i', re.S)
#: The `std::string get("get");` prefixes those loops concatenate.
_PREFIX = re.compile(r'std::string\s+(\w+)\s*\(\s*"([^"]*)"\s*\)\s*;')
_WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
#: A quoted literal -- dialogue prose, never a command name.
_STRING = re.compile(r'"[^"\n]*"')


class Command:
    """One registered MWScript command, with how often an export calls it."""

    def __init__(self, domain, name, ret, args, opcode):
        """Stores one registration; `calls` is filled in by `count_calls`."""
        self.domain = domain
        self.name = name
        self.ret = ret
        self.args = args
        self.opcode = opcode
        self.calls = 0

    @property
    def key(self):
        """The lowercased name, which is how MWScript matches commands."""
        return self.name.lower()

    def pushed(self):
        """How many arguments reach the stack, stopping at the optional mark."""
        return sum(1 for c in self.args.split('/')[0] if c in 'Sclsf')


def _domain_at(spans, pos):
    """The `namespace` a registration at `pos` sits in, or '?'."""
    name = '?'
    for off, dom in spans:
        if off >= pos:
            break
        name = dom
    return name


def _expand_loops(text, spans, out):
    """Add the families registered in a loop over a name array.

    🛑 Whole families -- the dynamics, attributes, skills and controls --
    register with a COMPUTED name (`get + dynamics[i]`), so there is no string
    literal to match. Skipping them reported all 12 dynamic-stat commands as
    unregistered while they were ported and working.
    """
    arrays = {m.group(1): re.findall(r'"([^"]+)"', m.group(2))
              for m in _ARRAY.finditer(text)}
    prefixes = {m.group(1): m.group(2) for m in _PREFIX.finditer(text)}
    forms = ((_LOOP_FUNC, True), (_LOOP_INSTR, False))
    for pattern, is_func in forms:
        for m in pattern.finditer(text):
            prefix = prefixes.get(m.group(1))
            names = arrays.get(m.group(2))
            if prefix is None or not names:
                continue
            ret = m.group(3) if is_func else '-'
            args = m.group(4) if is_func else m.group(3)
            opcode = m.group(5) if is_func else m.group(4)
            domain = _domain_at(spans, m.start())
            for offset, suffix in enumerate(names):
                cmd = Command(domain, prefix + suffix, ret, args,
                              f'{opcode}+{offset}')
                out.setdefault(cmd.key, cmd)


def registrations(root):
    """Every registered command, keyed by lowercased name."""
    path = os.path.join(root, REGISTRATIONS)
    with open(path, encoding='utf-8', errors='replace') as fh:
        text = fh.read()
    spans = [(m.start(), m.group(1)) for m in _NAMESPACE.finditer(text)]
    out = {}
    for m in _INSTR.finditer(text):
        cmd = Command(_domain_at(spans, m.start()), m.group(1), '-',
                      m.group(2), m.group(3))
        out.setdefault(cmd.key, cmd)
    for m in _FUNC.finditer(text):
        cmd = Command(_domain_at(spans, m.start()), m.group(1), m.group(2),
                      m.group(3), m.group(4))
        out.setdefault(cmd.key, cmd)
    _expand_loops(text, spans, out)
    return out


def installed(root):
    """`(opcode constants with a real handler, deliberate no-op names)`."""
    folder, pattern = os.path.split(RUNNER_PARTS_GLOB)
    prefix, suffix = pattern.split('*')
    here = os.path.join(root, folder)
    parts = sorted(os.path.join(here, name) for name in os.listdir(here)
                   if name.startswith(prefix) and name.endswith(suffix))
    text = ''
    for name in [os.path.join(root, RUNNER)] + parts:
        with open(name, encoding='utf-8', errors='replace') as fh:
            text += fh.read()
    real = {m.group(1).rsplit('::', 1)[-1] for m in _INSTALL.finditer(text)}
    for family in _FAMILY.finditer(text):
        real.update(_OPCODE.findall(family.group(1)))
    block = _NOOPS.search(text)
    noops = set(re.findall(r'"([^"]+)"', block.group(1))) if block else set()
    return real, noops


def _unescape(body):
    """The export escapes tabs, newlines and carriage returns as literals."""
    return (body.replace('\\r', '\n').replace('\\n', '\n')
                .replace('\\t', '\t'))


def _field_bodies(path, field):
    """Every `field=...` value in an export dump, unescaped."""
    if not os.path.isfile(path):
        return
    prefix = field + '='
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            if line.startswith(prefix):
                body = line[len(prefix):].rstrip('\n')
                if body:
                    yield _unescape(body)


def _script_bodies(export_dir):
    """Every MWScript body an export holds: INFO result scripts AND the
    object scripts in SCPT.

    🛑 Both corpora, always. Object scripts are the larger one and use a
    different command set -- scoring only `MWIN.txt` reported `PlaySound3D`,
    `GetSecondsPassed`, `Rotate` and `OnActivate` as UNUSED when object
    scripts call them constantly.
    """
    info = os.path.join(export_dir, 'MWIN.txt')
    scpt = os.path.join(export_dir, SCRIPT_EXPORT)
    if not os.path.isfile(info) and not os.path.isfile(scpt):
        raise SystemExit(f'no MWIN.txt or {SCRIPT_EXPORT} under {export_dir}')
    yield from _field_bodies(info, SCRIPT_FIELD)
    yield from _field_bodies(scpt, OBJECT_SCRIPT_FIELD)


def count_calls(export_dir, commands):
    """Tally how often each registered command appears as a statement word.

    🛑 `ref->Command` puts the target FIRST, so every word on a line is
    considered, not just the leading one -- a leading-word match blames the
    reference and under-reports the command.

    🛑 A quoted STRING is prose, never a call: `Choice "I will help you." 1`
    scored 38 calls for `Help`, ranking a console command as the corpus's
    biggest unported one. See: docs/commentary/morrowind_runtime.md#opcode-audit-strings
    """
    total = 0
    for body in _script_bodies(export_dir):
        for line in body.splitlines():
            line = _STRING.sub(' ', line.split(';', 1)[0])
            seen = set()
            for word in _WORD.findall(line):
                key = word.lower()
                cmd = commands.get(key)
                if cmd is not None and key not in seen:
                    seen.add(key)
                    cmd.calls += 1
                    total += 1
    return total


def _status(cmd, real, noops):
    """'ported', 'no-op' (nothing to port by design), 'STUB' or 'CONFLICT'.

    A family member carries a `+<offset>` tail that names its position in the
    name array; the install site writes `+ Which`, so both sides compare on
    the base constant. CONFLICT is installed AND declared unportable, which
    is an op that cannot act yet counts as ported.
    See: docs/commentary/morrowind_runtime.md#ported-is-not-wired
    """
    base = cmd.opcode.rsplit('::', 1)[-1].split('+', 1)[0]
    installed = base in real or base + 'Explicit' in real
    if installed and cmd.key in noops:
        return 'CONFLICT'
    if installed:
        return 'ported'
    return 'no-op' if cmd.key in noops else 'STUB'


def _ranked(commands):
    """The mapping or any iterable of Commands, most-called first."""
    rows = commands.values() if hasattr(commands, 'values') else commands
    return sorted(rows, key=lambda c: (-c.calls, c.name.lower()))


def report(commands, real, noops, args):
    """Prints the command table, filtered by `--todo` and `--top`."""
    rows = _ranked(commands)
    if args.todo:
        rows = [c for c in rows if _status(c, real, noops) == 'STUB']
    if args.top:
        rows = rows[:args.top]
    width = max((len(c.name) for c in rows), default=4)
    print(f"{'command':<{width}}  {'domain':<12} ret args      calls  status")
    for cmd in rows:
        print(f'{cmd.name:<{width}}  {cmd.domain:<12} {cmd.ret:<3} '
              f'{cmd.args or "-":<9} {cmd.calls:>6}  '
              f'{_status(cmd, real, noops)}')


def summarize(commands, real, noops):
    """Prints the ported/stubbed totals and the stubbed domains, worst first."""
    buckets = collections.defaultdict(list)
    for cmd in commands.values():
        buckets[_status(cmd, real, noops)].append(cmd)
    todo = buckets['STUB']
    print(f'\n{len(commands)} registered command(s): '
          f"{len(buckets['ported'])} ported, {len(buckets['no-op'])} "
          f'deliberate no-op, {len(todo)} stubbed')
    for label in ('ported', 'no-op', 'STUB'):
        calls = sum(c.calls for c in buckets[label])
        print(f'  {label:<7} {calls:>6} call site(s)')
    for cmd in buckets['CONFLICT']:
        print(f'🛑 CONFLICT {cmd.name}: installed AND in kDeliberateNoOps -- '
              f'an op that cannot act still counts as ported')
    print('\nstubbed call sites by domain:')
    by_domain = collections.Counter(c.domain for c in todo)
    for domain, count in by_domain.most_common():
        calls = sum(c.calls for c in todo if c.domain == domain)
        print(f'  {domain:<14} {count:>4} command(s)  {calls:>6} call(s)')


def write_tsv(path, commands, real, noops):
    """Writes the whole table, ported and stubbed alike, as TSV."""
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('domain\tname\tret\targs\tpushed\tcalls\tstatus\n')
        for cmd in _ranked(commands):
            fh.write(f'{cmd.domain}\t{cmd.name}\t{cmd.ret}\t{cmd.args}\t'
                     f'{cmd.pushed()}\t{cmd.calls}\t'
                     f'{_status(cmd, real, noops)}\n')
    print(f'\nwrote {path}')


def _rows(fh, rows, header):
    """One markdown table, or a line saying the section is empty."""
    if not rows:
        fh.write('_None._\n\n')
        return
    fh.write(f'| {header} | Domain | Signature | Calls |\n')
    fh.write('|---|---|---|---:|\n')
    for cmd in rows:
        sig = f'`{cmd.args}`' if cmd.args else '—'
        ret = f' → `{cmd.ret}`' if cmd.ret != '-' else ''
        fh.write(f'| `{cmd.name}` | {cmd.domain} | {sig}{ret} | '
                 f'{cmd.calls or ""} |\n')
    fh.write('\n')


#: Commands the RUNTIME cannot fix alone, and the data each one waits on.
DATA_BLOCKED = (
    ('addspell removespell getspell hasspell', 'SPEL',
     'no `SPEL.txt` is exported, so a spell id resolves to nothing'),
    ('cast explodespell getspelleffects geteffect removeeffects', 'MGEF/ENCH',
     'no `MGEF.txt` or `ENCH.txt`; an effect has no FormID to name'),
    ('positioncell placeitemcell aifollowcell getpccell', 'CELL',
     '`CELL.txt` IS exported but no cell id -> FormID table is staged'),
    ('aiwander aitravel aifollow aiescort getaipackagedone', 'PACK',
     '`PACK.txt` IS exported but no package is staged, and Skyrim needs a '
     'real PACK record rather than a runtime call'),
    ('addsoulgem removesoulgem hassoulgem dropsoulgem', 'SLGM',
     'TES3 soul gems export as MISC, so they are clutter in Skyrim'),
)


def _write_data_blocked(fh, live):
    """The section naming which gaps need EXPORT or IMPORT work, not runtime.

    Ranked by the call sites the audit just measured, so the cost of each is
    the tool's own number rather than a recollection.
    """
    calls = {c.key: c.calls for c in live}
    rows = []
    for names, record, why in DATA_BLOCKED:
        listed = names.split()
        total = sum(calls.get(n, 0) for n in listed)
        if total:
            rows.append((total, listed, record, why))
    if not rows:
        return
    fh.write('## Blocked on EXPORT or IMPORT, not on the runtime\n\n')
    fh.write('Porting the opcode alone cannot fix these: the data it would '
             'name is not converted yet.\n\n')
    fh.write('| Calls | Needs | Commands | Why |\n|---:|---|---|---|\n')
    for total, listed, record, why in sorted(rows, reverse=True):
        shown = ' '.join(f'`{n}`' for n in listed)
        fh.write(f'| {total} | {record} | {shown} | {why} |\n')
    fh.write('\n🛑 **A TES3 soul gem carries NO soul field.** OpenMW decides '
             'by id prefix -- `mwclass/misc.cpp:isSoulGem` is '
             '`getRefId().startsWith("misc_soulgem")` -- and the trapped soul '
             'lives on the CellRef, not the base record. Measured over '
             'TR_Mainland plus the Morroblivion patch: 6 MISC records match '
             'that prefix and 3 more merely contain "soulgem", so those 3 are '
             'NOT soul gems in Morrowind either. Converting them to SLGM '
             'means matching the prefix, never the name.\n\n')


def write_markdown(path, commands, real, noops, export):
    """Writes the human-readable audit: the work that is left, then the rest.

    Split by whether anything CALLS a command, because the registration table
    is OpenMW's console as well as MWScript -- most unported commands cannot
    be reached from a result script at all.
    """
    buckets = collections.defaultdict(list)
    for cmd in commands.values():
        buckets[_status(cmd, real, noops)].append(cmd)
    live = [c for c in _ranked(buckets['STUB']) if c.calls]
    dead = sorted(buckets['STUB'], key=lambda c: c.name)
    dead = [c for c in dead if not c.calls]
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('# MWScript opcodes in MorrowindRuntime\n\n')
        fh.write(f'**Tool:** `python tools/script/mwscript_opcode_audit.py '
                 f'--export "{export}" --markdown <this file>`\n\n')
        fh.write(f'Measured over `{export}`: {len(commands)} registered '
                 f'command(s), {sum(c.calls for c in commands.values())} '
                 'call site(s) across BOTH corpora -- the INFO result scripts '
                 'in `MWIN.txt` and the object scripts in `SCPT.txt`.\n\n')
        fh.write(f'| Status | Commands | Call sites |\n|---|---:|---:|\n')
        for label in ('ported', 'no-op', 'STUB'):
            fh.write(f'| {label} | {len(buckets[label])} | '
                     f'{sum(c.calls for c in buckets[label])} |\n')
        fh.write(f'\n🛑 **{len(dead)} of the {len(buckets["STUB"])} stubbed '
                 'commands have ZERO call sites in either corpus** — OpenMW\'s '
                 'console (`tgm`, `coc`, every `toggle*`), the chargen menu '
                 'toggles, the Bloodmoon werewolf commands and OpenMW\'s own '
                 'hooks (`reloadlua`, `setnavmeshnumber`). The real remaining '
                 f'work is the {len(live)} command(s) below.\n\n')
        fh.write('## Stubbed, and something calls it\n\n')
        _rows(fh, live, 'Command')
        _write_data_blocked(fh, live)
        fh.write('## Ported\n\n')
        _rows(fh, _ranked(buckets['ported']), 'Command')
        fh.write('## Deliberate no-ops\n\n')
        fh.write('Nothing to port: Morrowind\'s own presentation, or state '
                 'this runtime does not keep.\n\n')
        _rows(fh, _ranked(buckets['no-op']), 'Command')
        fh.write('## Stubbed, but nothing calls it\n\n')
        fh.write('Console, debug and chargen commands. Listed for '
                 'completeness; none is reachable from dialogue.\n\n')
        fh.write(', '.join(f'`{c.name}`' for c in dead) + '\n')
    print(f'\nwrote {path}')


#: `Hooks().name` is a use; `hooks.name =` is the game side supplying it.
_HOOK_USE = re.compile(r'\bHooks\(\)\.(\w+)')
_HOOK_SET = re.compile(r'\bhooks\.(\w+)\s*=')
#: A DialogueState method's declaration, and any call of a method by name.
_STATE_DECL = re.compile(r'^\s+[\w:<>,\s\*&]+?\b(\w+)\([^;{]*\)\s*(?:const)?;',
                         re.M)
_STATE_CLASS = re.compile(r'class DialogueState \{(.*?)\n\};', re.S)
#: The struct field a setter writes: `mFactions[k].reputation = value`.
_STATE_WRITE = re.compile(r'\]\.(\w+)\s*=')
#: `void DialogueState::SetX(...) { ... }` -- the whole body, to see inside.
_STATE_SETTER = re.compile(r'\bDialogueState::(Set\w+|\w*Change\w*)\s*\('
                           r'[^)]*\)\s*\{(.*?)\n\}', re.S)


def _plugin_sources(root):
    """`{file name: text}` for the runtime's sources, tests left out: a test
    calling a method is not the game calling it."""
    folder = os.path.join(root, os.path.dirname(RUNNER))
    out = {}
    for name in sorted(os.listdir(folder)):
        if name.endswith(('.cpp', '.h')) and not name.endswith('_test.cpp'):
            with open(os.path.join(folder, name), encoding='utf-8',
                      errors='replace') as fh:
                out[name] = fh.read()
    return out


def inert_setters(sources):
    """State setters whose value never reaches the game.

    A `Set*` that touches no hook and whose value no file outside the OPCODES
    reads is written and read back and acted on by nobody. The opcode files
    are the writers, so a read there is the same loop closing. A consumer
    counts whether it calls a getter of that name or reads the struct field
    one returns -- `SetExpelled` is read as `Faction(f).expelled`.
    See: docs/commentary/morrowind_runtime.md#ported-is-not-wired
    """
    impl = sources.get('dialogue_state.cpp', '')
    consumers = '\n'.join(text for name, text in sources.items()
                          if not name.startswith(('dialogue_state.',
                                                  'script_ops')))
    out = []
    for m in _STATE_SETTER.finditer(impl):
        name, body = m.group(1), m.group(2)
        if 'Hooks()' in body:
            continue
        stem = name[3:] if name.startswith('Set') else name
        reads = [r'\b' + stem + r'\w*\s*\(']
        reads += [r'\.' + f + r'\b' for f in _STATE_WRITE.findall(body)]
        if any(re.search(r, consumers) for r in reads):
            continue
        out.append(name)
    return out


def unwired(root):
    """`(hooks never supplied, idle methods, setters the game never sees)`.

    A ported opcode whose hook the game never sets, whose state no code ever
    feeds, or whose setter writes a field nothing acts on, answers with a
    default forever and still reads as ported.
    See: docs/commentary/morrowind_runtime.md#ported-is-not-wired
    """
    sources = _plugin_sources(root)
    everything = '\n'.join(sources.values())
    hooks = set(_HOOK_USE.findall(everything)) - set(
        _HOOK_SET.findall(everything))
    body = _STATE_CLASS.search(sources.get('dialogue_state.h', ''))
    declared = set(_STATE_DECL.findall(body.group(1))) if body else set()
    callers = '\n'.join(text for name, text in sources.items()
                        if not name.startswith('dialogue_state.'))
    idle = {name for name in declared
            if not re.search(r'[.>]' + name + r'\(', callers)}
    return sorted(hooks), sorted(idle), sorted(inert_setters(sources))


def report_unwired(root):
    """Prints what `unwired` found; returns how many items that is."""
    hooks, idle, inert = unwired(root)
    for name in hooks:
        print(f'UNWIRED hook      {name}: used, never supplied by the game')
    for name in idle:
        print(f'UNWIRED state     {name}: declared, nothing outside tests '
              f'calls it')
    for name in inert:
        print(f'INERT setter      {name}: writes state no hook and no other '
              f'file acts on')
    total = len(hooks) + len(idle) + len(inert)
    print(f'{total} unwired item(s)')
    return total


def main():
    """Parses arguments, builds the table and prints it."""
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', default='.', help='repository root')
    ap.add_argument('--export', help='an export directory holding MWIN.txt')
    ap.add_argument('--todo', action='store_true',
                    help='list only the commands that are still stubbed')
    ap.add_argument('--top', type=int, help='show only the first N rows')
    ap.add_argument('--tsv', help='also write the full table here')
    ap.add_argument('--markdown', help='write the readable audit here')
    ap.add_argument('--wiring', action='store_true',
                    help='list hooks and state nothing feeds, then exit')
    args = ap.parse_args()
    if args.wiring:
        sys.exit(1 if report_unwired(args.root) else 0)

    commands = registrations(args.root)
    real, noops = installed(args.root)
    print(f'{len(commands)} registration(s), '
          f'{len(real)} installed opcode constant(s)', file=sys.stderr)
    if args.export:
        total = count_calls(args.export, commands)
        print(f'{total} call site(s) in {args.export}', file=sys.stderr)
    report(commands, real, noops, args)
    summarize(commands, real, noops)
    if args.tsv:
        write_tsv(args.tsv, commands, real, noops)
    if args.markdown:
        write_markdown(args.markdown, commands, real, noops,
                       args.export or '(no export)')


if __name__ == '__main__':
    main()

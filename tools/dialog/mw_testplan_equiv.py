#!/usr/bin/env python3
"""Which MWScript commands share ONE handler, so testing one tests them all.

`Enable` and `Disable` are both `OpSetEnabled<R, bool>`; all 177 attribute,
skill and magic-effect commands are one `OpStat` differing by a table row and
a `Verb`. Where the class is the same and only a constant differs, the second
command exercises no code the first did, so one representative per class is
enough.

The class comes from the `Real<...>` registrations, never from the name:
`ModHealth` and `ModCurrentHealth` look distinct and share a class, while
`GetScale` and `SetScale` look like a pair and do not.

See: docs/commentary/morrowind_runtime.md#equivalence-classes
"""

import os
import re

#: `Real<OpFoo<Implicit, true>>(M::opcodeEnable)` -- handler class and opcode.
INSTALL = re.compile(
    r'\bReal3?\s*<\s*(\w+)[^(]*?\(\s*\n?\s*([A-Za-z0-9_:]+)', re.S)

#: `InstallPair<OpFoo>(into, A::x, A::y)` -- one class, two opcodes.
PAIR = re.compile(r'\bInstallPair\s*<\s*(\w+)\s*>\s*\((.*?)\)\s*;', re.S)

#: `InstallFamily(into, kSkills, ...)` -- the whole OpStat family.
FAMILY = re.compile(r'\bInstallFamily\s*\((.*?)\)\s*;', re.S)

OPCODE = re.compile(r'\bopcode\w+')

#: A `constexpr Stat kFoo[] = {{"name", "SkyrimAV"}, ...}` table.
STAT_TABLE = re.compile(r'constexpr Stat k\w+\[[^\]]*\]\s*=\s*\{(.*?)\};',
                        re.S)
STAT_ROW = re.compile(r'\{\s*"([^"]+)"\s*,\s*(nullptr|"[^"]*")\s*\}')

#: The verbs `OpStat` serves, longest first so `mod` beats nothing.
VERBS = ('get', 'set', 'mod')


def _plugin_dir(root):
    """Where the runtime's opcode sources live."""
    return os.path.join(root, 'tes_runtime', 'morrowind_runtime', 'plugin')


def _sources(root):
    """Every runtime source that installs opcodes, concatenated."""
    folder = _plugin_dir(root)
    names = [n for n in sorted(os.listdir(folder))
             if n.startswith('script_ops_') or n == 'script_runner.cpp']
    text = ''
    for name in names:
        with open(os.path.join(folder, name), encoding='utf-8',
                  errors='replace') as handle:
            text += handle.read()
    return text


def handler_by_opcode(root):
    """`{opcode constant: handler class}` over every real registration."""
    text = _sources(root)
    out = {}
    for match in INSTALL.finditer(text):
        out.setdefault(match.group(2).rsplit('::', 1)[-1], match.group(1))
    for match in PAIR.finditer(text):
        for code in OPCODE.findall(match.group(2)):
            out.setdefault(code, match.group(1))
    for match in FAMILY.finditer(text):
        for code in OPCODE.findall(match.group(1)):
            out.setdefault(code, 'OpStat')
    return out


def skyrim_backed(root):
    """`{stat name: bool}` -- does the stat map to a Skyrim actor value?

    The branch `OpStat` takes, so two stat commands are equivalent only when
    they agree here.
    """
    path = os.path.join(_plugin_dir(root), 'script_ops_stats.cpp')
    with open(path, encoding='utf-8', errors='replace') as handle:
        text = handle.read()
    out = {}
    for table in STAT_TABLE.finditer(text):
        for row in STAT_ROW.finditer(table.group(1)):
            out[row.group(1).lower()] = row.group(2) != 'nullptr'
    return out


def _stat_class(name, backed):
    """The class of a stat command, or None when it is not one.

    Split by VERB as well as by backing: a getter only reads, so it proves
    nothing about the writer that shares its table row.
    """
    for verb in VERBS:
        if not name.startswith(verb):
            continue
        stat = name[len(verb):]
        if stat.startswith('current'):
            stat = stat[len('current'):]
        if stat in backed:
            return 'OpStat/%s/%s' % (
                'read' if verb == 'get' else 'write',
                'skyrim' if backed[stat] else 'own')
    return None


def classes(root, opcodes):
    """`{command: class}` for every command whose handler is known.

    `opcodes` maps a lowercased command name to its opcode constant, as the
    audit's registrations give it.
    """
    handlers = handler_by_opcode(root)
    backed = skyrim_backed(root)
    out = {}
    for name, opcode in opcodes.items():
        stat = _stat_class(name, backed)
        if stat:
            out[name] = stat
            continue
        base = opcode.rsplit('::', 1)[-1].split('+', 1)[0]
        klass = handlers.get(base) or handlers.get(base + 'Explicit')
        if klass:
            out[name] = klass
    return out


def fold(by_class, wanted, reachable=None):
    """`(keep, folded)` -- one command per class, and what each stands for.

    A command with no known class is always kept: an unrecognised handler is
    not evidence that something else covers it.

    🛑 The representative must be one a quest can REACH. Picking
    alphabetically chose commands no quest calls, stranding their whole class
    and claiming coverage the plan never delivers.
    See: docs/commentary/morrowind_runtime.md#equivalence-classes
    """
    groups = {}
    for name in sorted(wanted):
        klass = by_class.get(name)
        if klass:
            groups.setdefault(klass, []).append(name)
    keep = {c for c in wanted if c not in by_class}
    folded = {}
    for members in groups.values():
        usable = [m for m in members
                  if reachable is None or m in reachable] or members
        keep.add(usable[0])
        for other in members:
            if other != usable[0]:
                folded[other] = usable[0]
    return keep, folded

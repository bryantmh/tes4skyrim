"""Fragment-body surgery for POLLED CONVERSATIONS.

A polled Oblivion conversation is a sequencer: each INFO is gated on an exact
counter and its result script steps that counter to hand off to the next line.
TES4's `Say` was SYNCHRONOUS, so the whole body ran in the frame the line
STARTED; Skyrim's is async. These helpers split such a body into the part that
must run at OnBegin, the part that stays gated at OnEnd, and the stage advances
that must survive a rejected turn.

See: docs/commentary/script_convert.md#sequenced-fragment-surgery
"""

import itertools
import re
import struct

from script_convert.constants import REMOTE_ADD_PREFIX, REMOTE_HOLD, REMOTE_RELEASE, safe_property_name
from script_convert.tes5.blocks import (
    Kind,
    classify,
    hoist_quest_start_above_writes,
    scan,
)

#: TES4 condition function index; its param2 is the script-local var index.
_GET_QUEST_VARIABLE = 79

#: Statement opening a SetStage call (or its TES4SetStage wrapper), which runs that fragment INLINE.
_SETSTAGE_RE = re.compile(r'^\s*\w[\w.]*\.(?:TES4)?SetStage\s*\(', re.IGNORECASE)

#: CTDA type bit: this condition is OR-ed with the next one.
_OR_FLAG = 0x01

#: A bare literal assignment, or a counter step on the counter ITSELF.
_STATE_WRITE_RE = re.compile(
    r'^\s*(?P<lhs>\w[\w.]*)\s*=\s*'
    r'(?:[-+]?[\d.]+|(?P<base>\w[\w.]*)\s*[-+]\s*[\d.]+)\s*(;.*)?$')

#: A top-level `TES4Polyfill.SetStage(<quest>, <literal>)` or TES4SetStage form, the advance that must survive.
_STAGE_ADVANCE_RE = re.compile(
    r'^(\s*)(?P<call>(?:TES4Polyfill\.SetStage\((?P<quest>[A-Za-z_]\w*), '
    r'|\w+\.TES4SetStage\((?P<wrapped>[A-Za-z_]\w*) as \w+, )(?P<stage>\d+)\))'
    r'\s*(?P<comment>;.*)?$', re.IGNORECASE)

#: A write to another script's variable with no call on its right-hand side, or that script's add call.
_REMOTE_WRITE_RE = re.compile(
    r'^(\s*)([A-Za-z_]\w*)\.(?:\w+\s*=(?!=)[^(;]*|' + REMOTE_ADD_PREFIX + r'\w+\([^()]*\))\s*(;.*)?$')

#: A literal write to a field whose name selects the next talker.
_HANDOFF_WRITE_RE = re.compile(
    r'^\s*\w[\w.]*\.(?:speaker|target)\s*=\s*'
    r'(?:-?[\d.]+|[A-Za-z_]\w*)\s*(;.*)?$', re.IGNORECASE)


def _equality_gate(raw: str):
    """(quest_fid24, var_index, int_value) for a `GetQuestVariable == N` CTDA.

    None unless the condition is that function, compares for EQUALITY, and
    tests a whole number: a `>=`/`<` gate is not a sequencer.
    """
    try:
        d = bytes.fromhex(raw)
    except ValueError:
        return None
    if len(d) < 20 or struct.unpack_from('<H', d, 8)[0] != _GET_QUEST_VARIABLE:
        return None
    if (d[0] >> 5) != 0:
        return None
    comp = struct.unpack_from('<f', d, 4)[0]
    if comp != int(comp):
        return None
    return (struct.unpack_from('<I', d, 12)[0] & 0x00FFFFFF,
            struct.unpack_from('<I', d, 16)[0], int(comp))


def _or_run(rec: dict, i: int, first) -> list:
    """The counter values condition `i` allows: its own, plus each following one OR-ed onto the same variable."""
    values = [first[2]]
    while int(rec.get(f'Condition[{i}].Raw')[:2], 16) & _OR_FLAG:
        i += 1
        hit = _equality_gate(rec.get(f'Condition[{i}].Raw') or '')
        if hit is None or hit[:2] != first[:2]:
            break
        values.append(hit[2])
    return values


def seq_counter_condition(rec: dict, script_vars: dict, names: dict):
    """(quest_edid, var_name, [int_value, ...]) for this INFO's counter gate, or None.

    `script_vars` maps quest fid -> {SLSD index: name} and `names` maps quest
    fid -> EditorID; a TES4 condition stores only the index. A line valid at
    several counts (`== 0 OR == 3`) yields each.
    """
    i = -1
    while True:
        i += 1
        raw = rec.get(f'Condition[{i}].Raw')
        if raw is None:
            return None
        hit = _equality_gate(raw)
        if hit is None:
            continue
        name = script_vars.get(hit[0], {}).get(hit[1])
        quest = names.get(hit[0], '')
        if name and quest:
            return quest, name, _or_run(rec, i, hit)


def sequence_gate(rec: dict, script_vars: dict, names: dict) -> str:
    """`<quest>.<var> == <n>` guard for a polled-conversation INFO, else ''.

    Only meaningful when the body itself steps that counter; the caller checks.
    See: docs/commentary/script_convert.md#sequence-gate
    """
    var = seq_counter_condition(rec, script_vars, names)
    if not var:
        return ''
    quest, name, values = var
    counter = f'{safe_property_name(quest)}.{safe_property_name(name)}'
    return ' || '.join(f'{counter} == {value}' for value in values)


def _quest_var_tests(rec: dict, script_vars: dict, names: dict) -> set:
    """`<quest>.<var>` (Papyrus names) of every GetQuestVariable condition of `rec`."""
    tested = set()
    i = 0
    while (raw := rec.get(f'Condition[{i}].Raw')) is not None:
        i += 1
        try:
            d = bytes.fromhex(raw)
        except ValueError:
            continue
        if len(d) < 20 or struct.unpack_from('<H', d, 8)[0] != _GET_QUEST_VARIABLE:
            continue
        quest = struct.unpack_from('<I', d, 12)[0] & 0x00FFFFFF
        name = script_vars.get(quest, {}).get(struct.unpack_from('<I', d, 16)[0])
        if name and names.get(quest):
            tested.add(f'{safe_property_name(names[quest])}.{safe_property_name(name)}')
    return tested


def choice_tested_vars(infos: list, script_vars: dict, names: dict) -> dict:
    """INFO fid24 -> the quest variables its choices' lines test, for each INFO with choices.

    A choice names a topic; what it tests is what any of that topic's INFOs test.
    See: docs/commentary/script_convert.md#choice-gate-writes-run-at-begin
    """
    tests = {}
    for rec in infos:
        if rec.get('ParentDIAL'):
            topic = int(rec['ParentDIAL'], 16) & 0xFFFFFF
            tests.setdefault(topic, set()).update(_quest_var_tests(rec, script_vars, names))
    out = {}
    for rec in infos:
        choices = [rec[k] for k in rec if k.startswith('Choice[') and rec[k]]
        tested = set().union(*(tests.get(int(c, 16) & 0xFFFFFF, set()) for c in choices)) if choices else set()
        if tested:
            out[int(rec['FormID'], 16) & 0xFFFFFF] = tested
    return out


def _writes_tested(line: str, tested) -> bool:
    """Whether `line` is a literal write to, or a counter step of, a variable in `tested`."""
    found = _STATE_WRITE_RE.match(line)
    if found and found.group('base') is None and found.group('lhs') in tested:
        return True
    return any(_step_delta(line, var) is not None for var in tested)


def _movable(line: str, top: str, tested, before: str) -> bool:
    """Whether a top-level `line` writes a `tested` variable that no line `before` it mentions."""
    if re.match(r'\s*', line).group() != top or not _writes_tested(line, tested):
        return False
    var = next(v for v in tested if v.lower() in line.lower() or v.split('.')[1].lower() in line.lower())
    return var.split('.')[1].lower() not in before


def split_choice_writes(lines: list, tested) -> tuple:
    """(writes, rest): `lines` split by whether a top-level line writes a `tested` variable.

    A write moves only when nothing before it in the body reads the variable or returns.
    See: docs/commentary/script_convert.md#choice-gate-writes-run-at-begin
    """
    top = re.match(r'\s*', lines[0]).group() if lines else ''
    early, rest = [], []
    for i, line in enumerate(lines):
        before = '\n'.join(lines[:i]).lower()
        moves = 'return' not in before and _movable(line, top, tested, before)
        (early if moves else rest).append(line)
    return early, rest


def _is_state_write(line: str) -> bool:
    """A bare literal assignment, or a counter step `x = x + n` on ITSELF."""
    m = _STATE_WRITE_RE.match(line)
    if not m:
        return False
    base = m.group('base')
    return base is None or base.lower() == m.group('lhs').lower()


def split_counter_step(lines: list, seq_gate: str) -> tuple:
    """Split off the `<counter> = <counter> + n` step the gate tests.

    Returns (counter_lines, rest) preserving order; ([], lines) when the body
    has no such step, meaning this INFO is not a sequencer.
    See: docs/commentary/script_convert.md#sequenced-fragment-surgery
    """
    m = re.match(r'\s*(\S+)\s*==', seq_gate or '')
    if not m:
        return [], list(lines)
    counter = m.group(1)
    idx = next((i for i, ln in enumerate(lines)
                if _step_delta(ln, counter) is not None), None)
    if idx is None:
        return [], list(lines)
    return [lines[idx]], lines[:idx] + lines[idx + 1:]


def _step_delta(line: str, counter: str):
    """How far `line` steps `counter` (`c = c + n`, or its owner's TES4Add_ call), else None."""
    owner, _, var = counter.rpartition('.')
    name = re.escape(counter)
    plain = re.match(rf'^\s*{name}\s*=\s*{name}\s*([-+])\s*([\d.]+)\s*(;.*)?$',
                     line, re.IGNORECASE)
    call = plain or re.match(
        rf'^\s*{re.escape(owner)}\.{REMOTE_ADD_PREFIX}{re.escape(var)}'
        r'\(\s*(0\s*-)?\s*([\d.]+)\s*\)\s*(;.*)?$', line, re.IGNORECASE)
    if not call:
        return None
    sign = -1.0 if (call.group(1) or '+').strip().endswith('-') else 1.0
    return sign * float(call.group(2))


def _held_owner(line: str, holds):
    """(indent, owner) of a plain write to a holding script, else None."""
    found = _REMOTE_WRITE_RE.match(line)
    return found.group(1, 2) if found and holds(found.group(2)) else None


def hold_runs(lines: list, holds) -> list:
    """`lines` with each run of two or more plain writes to one holding script between its hold and release.

    `holds(owner)` says whether the owner carries the pair. Two scripts'
    runs then cannot interleave; a single write needs no hold.
    See: docs/commentary/script_convert.md#remote-write-runs
    """
    out = []
    for key, group in itertools.groupby(lines, lambda line: _held_owner(line, holds)):
        run = list(group)
        if key and len(run) > 1:
            run = [f'{key[0]}{key[1]}.{REMOTE_HOLD}()', *run, f'{key[0]}{key[1]}.{REMOTE_RELEASE}()']
        out += run
    return out


def split_turn_handoff(counter_step, gated_rest):
    """Split the turn handoff out of an End-fragment body.

    Returns (handoff, remainder): the counter step plus any speaker/target
    literal writes, and everything else in original order.
    See: docs/commentary/script_convert.md#turn-handoff
    """
    handoff = list(counter_step)
    rest = []
    for line in gated_rest:
        if _HANDOFF_WRITE_RE.match(line):
            handoff.append(line)
        else:
            rest.append(line)
    return handoff, rest


def stepped_gate(seq_gate, counter_step):
    """The sequence gate rewritten for AFTER the counter step has applied.

    See: docs/commentary/script_convert.md#stepped-gate
    """
    parts = [re.match(r'\s*(\S+)(\s*==\s*)(-?[\d.]+)\s*$', part) for part in (seq_gate or '').split(' || ')]
    if not all(parts) or not counter_step:
        return seq_gate
    delta = _step_delta(counter_step[0], parts[0].group(1))
    if delta is None:
        return seq_gate
    return ' || '.join(m.group(1) + m.group(2) + '%g' % (float(m.group(3)) + delta) for m in parts)


def split_stage_advances(body: list) -> tuple:
    """Split a sequenced fragment body into (gated writes, stage advances).

    Advances are re-emitted behind a `!GetStageDone(N)` guard so they
    survive a rejected turn. Only TOP-LEVEL advances are lifted; `scan` is fed
    a synthetic header so `not stack` means the top level of the body.
    See: docs/commentary/script_convert.md#stage-advances-survive
    """
    gated, advances = [], []
    for ln in scan(['Function _()'] + list(body)):
        if ln.kind is Kind.HEADER:
            continue
        m = _STAGE_ADVANCE_RE.match(ln.text) if not ln.stack else None
        if not m:
            gated.append(ln.text)
            continue
        indent, comment = m.group(1), m.group('comment')
        quest = m.group('quest') or m.group('wrapped')
        advances.append(f'{indent}If !{quest}.GetStageDone({m.group("stage")})'
                        '  ; advance survives a rejected turn')
        advances.append(f'{indent}  {m.group("call")}'
                        + (f'  {comment}' if comment else ''))
        advances.append(f'{indent}EndIf')
    return gated, advances


def state_writes_before_setstage(lines: list) -> list:
    """Move plain state assignments ahead of the first SetStage call.

    Only literal assignments, and only from a FLAT tail: `classify` is the
    shared barrier for a nested block that may depend on what the stage did.
    The result is re-fixed so no `Start()` sits below a write to its own quest.
    See: docs/commentary/script_convert.md#writes-before-setstage
    """
    first = next((i for i, ln in enumerate(lines) if _SETSTAGE_RE.match(ln)),
                 None)
    if first is None:
        return lines
    tail = lines[first + 1:]
    if any(classify(ln) is not Kind.OTHER for ln in tail):
        return lines
    hoist = [ln for ln in tail if _is_state_write(ln)]
    if not hoist:
        return lines
    rest = [ln for ln in tail if not _is_state_write(ln)]
    out = lines[:first] + hoist + [lines[first]] + rest
    return hoist_quest_start_above_writes(out)

"""Fragment-body surgery for POLLED CONVERSATIONS.

A polled Oblivion conversation is a sequencer: each INFO is gated on an exact
counter and its result script steps that counter to hand off to the next line.
TES4's `Say` was SYNCHRONOUS, so the whole body ran in the frame the line
STARTED; Skyrim's is async. These helpers split such a body into the part that
must run at OnBegin, the part that stays gated at OnEnd, and the stage advances
that must survive a rejected turn.

See: docs/commentary/script_convert.md#sequenced-fragment-surgery
"""

import re
import struct

from script_convert.constants import safe_property_name
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

#: A bare literal assignment, or a counter step on the counter ITSELF.
_STATE_WRITE_RE = re.compile(
    r'^\s*(?P<lhs>\w[\w.]*)\s*=\s*'
    r'(?:[-+]?[\d.]+|(?P<base>\w[\w.]*)\s*[-+]\s*[\d.]+)\s*(;.*)?$')

#: A top-level `<quest>.SetStage(<literal>)` or its TES4SetStage form, the advance that must survive.
_STAGE_ADVANCE_RE = re.compile(
    r'^(\s*)(?P<call>(?:(?P<quest>[A-Za-z_]\w*)\.SetStage\('
    r'|\w+\.TES4SetStage\((?P<wrapped>[A-Za-z_]\w*) as \w+, )(?P<stage>\d+)\))'
    r'\s*(?P<comment>;.*)?$', re.IGNORECASE)

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


def seq_counter_condition(rec: dict, script_vars: dict, names: dict):
    """(quest_edid, var_name, int_value) for this INFO's counter gate, or None.

    `script_vars` maps quest fid -> {SLSD index: name} and `names` maps quest
    fid -> EditorID; a TES4 condition stores only the index.
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
        quest_fid, var_idx, value = hit
        name = script_vars.get(quest_fid, {}).get(var_idx)
        quest = names.get(quest_fid, '')
        if name and quest:
            return quest, name, value


def sequence_gate(rec: dict, script_vars: dict, names: dict) -> str:
    """`<quest>.<var> == <n>` guard for a polled-conversation INFO, else ''.

    Only meaningful when the body itself steps that counter; the caller checks.
    See: docs/commentary/script_convert.md#sequence-gate
    """
    var = seq_counter_condition(rec, script_vars, names)
    if not var:
        return ''
    quest, name, value = var
    return (f'{safe_property_name(quest)}.'
            f'{safe_property_name(name)} == {value}')


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
    step = re.compile(
        r'^\s*' + re.escape(counter) + r'\s*=\s*' + re.escape(counter)
        + r'\s*[-+]\s*[\d.]+\s*(;.*)?$', re.IGNORECASE)
    idx = next((i for i, ln in enumerate(lines) if step.match(ln)), None)
    if idx is None:
        return [], list(lines)
    return [lines[idx]], lines[:idx] + lines[idx + 1:]


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
    m = re.match(r'(.*?==\s*)(-?[\d.]+)\s*$', seq_gate or '')
    if not m or not counter_step:
        return seq_gate
    step = re.search(r'([-+])\s*([\d.]+)', counter_step[0].split('=', 1)[1])
    if not step:
        return seq_gate
    delta = float(step.group(2))
    if step.group(1) == '-':
        delta = -delta
    val = float(m.group(2)) + delta
    txt = str(int(val)) if val == int(val) else ('%g' % val)
    return m.group(1) + txt


def split_stage_advances(body: list) -> tuple:
    """Split a sequenced fragment body into (gated writes, stage advances).

    Advances are re-emitted behind a monotonic `GetStage() < N` guard so they
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
        advances.append(f'{indent}If {quest}.GetStage() < {m.group("stage")}'
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

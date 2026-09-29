"""SCRO tables -> converter property refs, and stale-name recovery.

A TES4 script's SCRO table is the set of forms the COMPILED script binds;
the source text can spell them under names no record carries any more.
`preload_scro_refs` registers every SCRO as a typed property before a body
converts, and `resolve_scro_aliases` maps an unresolvable body name onto the
one SCRO the body never spells.

See: docs/commentary/script_convert.md#scro-table-outranks-script-text
"""

import re

from script_convert.command_rows import KNOWN_COMMANDS
from script_convert.constants import record_type_to_papyrus, safe_property_name
from script_convert.cross_ref import CrossRefGraph
from script_convert.resolve_name import script_type_binds

#: PlayerRef 0x14 and the player base NPC_ 0x07: both are the `player` keyword, never a bound property.
_PLAYER_FORMIDS = frozenset({'00000014', '00000007'})

_SCRO_WALK_SKIP_KEYWORDS = frozenset({
    'begin', 'end', 'if', 'elseif', 'else', 'endif', 'while', 'loop',
    'endwhile', 'return', 'set', 'to', 'short', 'long', 'float', 'ref',
    'int', 'string_var', 'array_var', 'scn', 'scriptname', 'let', 'eval',
    'foreach', 'break', 'continue',
})


def _scro_body_tokens(body: str) -> list:
    """Return the candidate form-reference names in a TES4 script body.

    A dotted `NDLathonREF.disable` contributes only its RECEIVER, which is the
    part that names a form.  Comments are stripped, quotes around a name are
    dropped but the name kept, numeric literals never match, and command names
    are skipped (a command is not a form).  The result is a superset: locals and
    unknown OBSE commands survive, which resolve_scro_aliases handles by
    construction.
    """
    lines = []
    for raw in body.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        lines.append(raw.split(';', 1)[0])
    text = re.sub(r'"([^"]*)"', r' \1 ', '\n'.join(lines))
    out = []
    for m in re.finditer(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?',
                         text):
        head = m.group(0).split('.', 1)[0]
        low = head.lower()
        if low in _SCRO_WALK_SKIP_KEYWORDS or low in KNOWN_COMMANDS:
            continue
        out.append(head)
    return out


def resolve_scro_aliases(body: str, scros: list, xref: CrossRefGraph) -> dict:
    """Map a name in `body` that resolves to NO record onto its SCRO EditorID.

    The binding is a set difference and is made only when exactly one SCRO is
    unspelled by the body and exactly one body name resolves to nothing; a
    SCRO this export cannot name (master-owned) makes nothing certain, and
    the player is a keyword, never an unspelled SCRO.
    See: docs/commentary/script_convert.md#scro-table-outranks-script-text
    """
    if not scros:
        return {}
    tokens = _scro_body_tokens(body)
    if not tokens:
        return {}
    spelled = {t.lower() for t in tokens}

    unspelled = []
    for fid in scros:
        if fid in _PLAYER_FORMIDS:
            continue
        edid = xref.formid_to_edid.get(fid)
        if not edid:
            return {}
        if edid.lower() not in spelled:
            unspelled.append(edid)
    if len(unspelled) != 1:
        return {}

    unresolved = []
    for tok in dict.fromkeys(tokens):
        low = tok.lower()
        if low in ('player', 'playerref'):
            continue
        if xref.edid_to_formid.get(low):
            continue
        unresolved.append(tok)
    if len(unresolved) != 1:
        return {}
    return {unresolved[0].lower(): unspelled[0]}


def scro_list(rec: dict, prefix: str = '') -> list:
    """Return a record's SCRO FormIDs in table order (optionally a stage log's)."""
    out = []
    i = 0
    while True:
        fid = rec.get(f'{prefix}SCRO[{i}]')
        if fid is None:
            break
        out.append(fid)
        i += 1
    return out


def preload_scro_refs(conv, rec: dict, xref: CrossRefGraph) -> None:
    """Pre-populate converter property_refs from SCRO entries in a record."""
    for fid in scro_list(rec):
        add_scro_ref(conv, fid, xref)


def preload_stage_scro_refs(conv, rec: dict, xref: CrossRefGraph,
                            stage_arr_idx: int, log_arr_idx: int) -> None:
    """Pre-populate converter property_refs from per-stage/log SCRO entries."""
    for fid in scro_list(rec, f'Stage[{stage_arr_idx}].Log[{log_arr_idx}].'):
        add_scro_ref(conv, fid, xref)


def add_scro_ref(conv, fid: str, xref: CrossRefGraph) -> None:
    """Add a single SCRO FormID as a property ref on the converter.

    A QUST stays the generic `Quest` (the body promotes it on dot access);
    other records take their attached script's type.  A property already
    typed more specifically than `Quest`, or typed `ActorBase`, is kept.
    See: docs/commentary/script_convert.md#property-type-merge
    """
    if fid in _PLAYER_FORMIDS:
        return
    edid = xref.formid_to_edid.get(fid)
    if not edid:
        return
    rtype = xref.record_type.get(fid, '')
    ptype = record_type_to_papyrus(rtype)
    if rtype != 'QUST':
        # Prefer the attached script class ONLY where it can actually bind to
        # this record -- the same guard the body-resolver applies
        # (resolve_name.script_type_binds, converter.py:1643).  A base-object
        # item (KEYM/BOOK/ARMO/WEAP/INGR/...) keeps its base Papyrus type: the
        # VM refuses an ObjectReference-derived script class on a base form and
        # the property then reads None all session.  An NPC_/CREA (Actor) or a
        # unique-placed ACTI/LIGH still keeps its script type.
        script_type = xref.get_record_script_type(edid)
        if script_type and script_type_binds(xref, ptype, fid):
            ptype = script_type
    key = safe_property_name(edid)
    cur = conv.sc.property_refs.get(key, '')
    if cur == 'ActorBase' or (cur and cur != 'Quest' and ptype == 'Quest'):
        return
    conv.sc.property_refs[key] = ptype

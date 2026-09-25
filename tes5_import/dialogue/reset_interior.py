"""The references a TES4 `ResetInterior` sends home.

Oblivion's ResetInterior expires a cell, and its cleanup returns the
references scripts had moved into it -- the Arena's combatants live in
ArenaCombatantsHolding, are moved into ArenaMatchCell for a match, and
`ResetInterior ArenaMatchCell` clears the loser's corpse.  Skyrim's
`Cell.Reset()` resets only the references whose editor location is that cell,
so a moved-in corpse stays.

The authored indicator is a script's `X.MoveTo M` where the marker M is placed
in a cell some script resets and X is placed elsewhere.  Each such cell gets a
FormList `TES4Movers_<cell>` of those X, and `TES4Polyfill.ResetInterior`
sends each one still in the cell back to its editor location.
See: docs/commentary/script_convert.md#resetinterior-sends-moved-refs-home
"""

import re
from collections import defaultdict

from ..base.text_reader import get_formid, get_str, remap_formid
from ..base.writer import pack_formid_subrecord, pack_record, pack_string_subrecord
from .say_topics import collect_script_texts

_MOVETO_RE = re.compile(r'\b(\w+)[ \t]*\.[ \t]*moveto[ \t]+(\w+)', re.IGNORECASE)
_RESET_RE = re.compile(r'\bresetinterior[ \t]+(\w+)', re.IGNORECASE)


def movers_list_name(cell_edid: str) -> str:
    """The FormList property script_convert names for one reset cell."""
    return f'TES4Movers_{cell_edid.lower()}'


def _placed_by_edid(by_type: dict) -> dict:
    """EditorID (lower) -> (FormID, parent CELL FormID) of every placed reference."""
    out = {}
    for sig in ('REFR', 'ACHR', 'ACRE'):
        for rec in by_type.get(sig, []):
            edid = (get_str(rec, 'EditorID') or '').lower()
            if edid:
                out.setdefault(edid, (get_formid(rec, 'FormID'),
                                      get_formid(rec, 'ParentCELL')))
    return out


def scan_reset_movers(by_type: dict) -> dict:
    """Reset cell EditorID (lower) -> FormIDs of the references moved into it."""
    texts = collect_script_texts(by_type)
    reset = {m.group(1).lower() for t in texts for m in _RESET_RE.finditer(t)}
    cells = {get_formid(c, 'FormID'): (get_str(c, 'EditorID') or '').lower()
             for c in by_type.get('CELL', [])}
    placed = _placed_by_edid(by_type)
    movers = defaultdict(set)
    for text in texts:
        for m in _MOVETO_RE.finditer(text):
            mover, marker = placed.get(m.group(1).lower()), placed.get(m.group(2).lower())
            if not mover or not marker or mover[1] == marker[1]:
                continue
            cell = cells.get(marker[1], '')
            if cell in reset:
                movers[cell].add(mover[0])
    return movers


def build_reset_movers(by_type: dict, writer, offset: int) -> dict:
    """Write one `TES4Movers_<cell>` FormList per reset cell; {property name: FLST FormID}."""
    out = {}
    for cell, refs in sorted(scan_reset_movers(by_type).items()):
        name = movers_list_name(cell)
        fid = writer.derive_formid('RESET_MOVERS', cell)
        subs = pack_string_subrecord('EDID', name)
        for ref in sorted(refs):
            subs += pack_formid_subrecord('LNAM', remap_formid(ref, offset))
        writer.add_record('FLST', pack_record('FLST', fid, 0, subs))
        out[name] = fid
    return out

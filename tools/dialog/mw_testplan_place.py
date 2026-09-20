#!/usr/bin/env python3
"""Where a quest giver stands, as something the console can actually reach.

The test plan names an actor; the user needs a `coc`. An interior is reached
by its own EditorID, an exterior only by its worldspace cell grid, so the two
resolve differently and an exterior giver reports the grid square its
placement falls in.

🛑 The join runs on the CONVERTED export, so it is FormID-keyed: an actor's
`ACHR.NAME` is the NPC_ FormID and its `ParentCELL` the CELL FormID. Matching
the sidecar's actor NAME to an NPC_ `EditorID` is what bridges the two, and
TES3 ids are case-insensitive, so both sides lowercase.

See: docs/commentary/morrowind_runtime.md#opcode-test-plan
"""

import os

from asset_convert.sources import source_registry
from tools.dialog.morrowind_quest_trace import records

#: `DATA.Flags` bit 0 is an interior cell; bit 1 is an exterior one.
INTERIOR = 1


def master_dirs(record_dir, export_root='export'):
    """`record_dir` plus every master's export dir that exists on disk.

    🛑 TR places its quest givers, but INHERITS vanilla's -- Caius Cosades and
    Mehra Milo are Morrowind.esm's. Reading only the plugin's own export left
    711 of 2086 quests with a giver and no `coc` target.
    See: docs/commentary/morrowind_runtime.md#opcode-test-plan
    """
    out = [record_dir]
    header = os.path.join(record_dir, '_HEADER.txt')
    if not os.path.isfile(header):
        return out
    with open(header, encoding='utf-8', errors='replace') as handle:
        names = [line.split('=', 1)[1].strip() for line in handle
                 if line.startswith('Master[')]
    for name in names:
        try:
            other = str(source_registry.record_dir(export_root, name))
        except Exception:
            continue
        if os.path.isdir(other) and other not in out:
            out.append(other)
    return out


def _read(record_dir, name):
    """Every record of one export dump, or nothing when it is absent."""
    path = os.path.join(record_dir, name)
    return records(path) if os.path.isfile(path) else ()


def _cells(dirs):
    """`{formid: cell dict}` for every CELL, interior and exterior."""
    out = {}
    for record_dir in dirs:
        for rec in _read(record_dir, 'CELL.txt'):
            flags = int(rec.get('DATA.Flags', '0') or 0)
            out[rec.get('FormID', '')] = {
                'editor': rec.get('EditorID', ''),
                'name': rec.get('FULL', '') or rec.get('EditorID', ''),
                'interior': bool(flags & INTERIOR),
                'grid': (rec.get('XCLC.X'), rec.get('XCLC.Y'))}
    return out


def _actor_ids(dirs):
    """`{npc formid: [name]}` -- every name an actor answers to.

    🛑 Both the EditorID AND the display name, because Morroblivion MANGLES
    the id it imports (`caius cosades` becomes `0caiusScosades`) while
    leaving `FULL` alone, and the sidecar names actors the TES3 way. Keying
    on the id alone left every master-owned giver unplaced.
    See: docs/commentary/morrowind_runtime.md#opcode-test-plan
    """
    by_form = {}
    for record_dir in dirs:
        for kind in ('NPC_.txt', 'CREA.txt'):
            for rec in _read(record_dir, kind):
                form = rec.get('FormID', '')
                names = {rec.get('EditorID', ''), rec.get('FULL', '')}
                names = {n for n in names if n}
                if form and names:
                    by_form[form] = names
    return by_form


def _placements(dirs, cells, by_form):
    """`{lowercased actor editorid: [place dict]}`, from ACHR and ACRE."""
    out = {}
    for record_dir in dirs:
        for kind in ('ACHR.txt', 'ACRE.txt'):
            for rec in _read(record_dir, kind):
                names = by_form.get(rec.get('NAME', ''))
                cell = cells.get(rec.get('ParentCELL', ''))
                if not names or not cell:
                    continue
                for name in names:
                    out.setdefault(name.lower(), []).append(cell)
    return out


def describe(places):
    """`(coc target, kind)` for an actor: its interior, else its grid square.

    An interior is preferred when the actor has both, because `coc <EditorID>`
    lands the player in the room with them; an exterior placement can only be
    named by the cell its coordinates fall in.
    """
    if not places:
        return '', ''
    interiors = [p for p in places if p['interior']]
    cell = (interiors or places)[0]
    if cell['interior']:
        return cell['editor'], 'interior'
    label = cell['name'] or cell['editor']
    grid = '%s, %s' % cell['grid']
    return ('%s (%s)' % (label, grid) if label else grid), 'exterior'


def locate(record_dir, plan, export_root='export'):
    """`{quest: (giver, coc target, kind)}` for every quest in `plan`.

    The giver is the actor whose dialogue sets the EARLIEST stage -- the one
    the player has to find to start the quest at all. Resolved across the
    plugin's masters, whose actors it inherits.
    """
    dirs = master_dirs(record_dir, export_root)
    cells = _cells(dirs)
    placements = _placements(dirs, cells, _actor_ids(dirs))
    out = {}
    for quest, row in plan.items():
        givers = sorted(row['givers'].items(), key=lambda kv: (kv[1], kv[0]))
        if givers:
            giver = givers[0][0]
            target, kind = describe(placements.get(giver.lower(), []))
            out[quest] = (giver, target, kind)
            continue
        scripts = sorted(row.get('scripts', {}))
        out[quest] = (('script: ' + scripts[0]) if scripts else '',
                      '', 'script')
    return out

"""Bases a script removes at runtime: navmesh obstacles, not walls.

A base whose own script calls `Disable` on itself and never `Enable` only ever
goes away during play (TES3's `ColonyMineBoulder2` boulders, which clear a mine
passage for quest CO_10). A navmesh baked with it as a wall stays cut after it
leaves. Skyrim's own answer is the base record's Obstacle flag: the engine cuts
the navmesh under the object while it is enabled. So the navmesh is generated
as if the object were gone and the base carries the flag. A base its script
also enables is built up during play and stays baked as standing.

See: docs/commentary/tes5_import_navmesh.md#scripted-blockers-are-obstacles
"""

import re

from .master_export import records_of
from .text_reader import get_str

#: Record flag bit 25, "Obstacle" on ACTI/CONT/FURN/MSTT/DOOR (xEdit wbDefinitionsTES5).
OBSTACLE_FLAG = 1 << 25

#: The base types whose Skyrim record carries the Obstacle flag and whose source record carries a script.
OBSTACLE_SIGS = frozenset({'ACTI', 'CONT', 'FURN'})

_TOGGLE_RE = re.compile(r'(?:("[^"]*"|\w+)[ \t]*(?:\.|->)[ \t]*)?\b(disable|enable)\b', re.IGNORECASE)

#: SCPT FormIDs (export hex, upper) whose script only removes its own object; built by index_obstacles.
_REMOVING_SCRIPTS = set()


def _code_lines(text: str):
    """The script source's lines, escaped or real breaks, with `;` comments cut off."""
    text = text.replace('\\r', '').replace('\\t', ' ').replace('\\n', '\n')
    for line in text.splitlines():
        yield line.split(';', 1)[0]


def removes_itself(text: str) -> bool:
    """True when the script disables its own object and never enables it."""
    verbs = {m.group(2).lower() for line in _code_lines(text or '')
             for m in _TOGGLE_RE.finditer(line) if not m.group(1)}
    return verbs == {'disable'}


def index_obstacles(by_type: dict, master_export: dict) -> int:
    """Record every SCPT that only removes its own object, masters' too; returns the count."""
    _REMOVING_SCRIPTS.clear()
    scpts = [(get_str(r, 'FormID'), r) for r in by_type.get('SCPT', [])]
    scpts += records_of(master_export, 'SCPT')
    _REMOVING_SCRIPTS.update(fid.upper() for fid, r in scpts
                             if fid and removes_itself(get_str(r, 'SCTX')))
    return len(_REMOVING_SCRIPTS)


def is_obstacle(rec: dict, sig: str) -> bool:
    """True when this `sig` base's script only removes its own object."""
    return sig in OBSTACLE_SIGS and get_str(rec, 'SCRI').upper() in _REMOVING_SCRIPTS

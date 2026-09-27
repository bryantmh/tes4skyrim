"""Marker factions that stand in for plugin-authored races.

An actor whose race no vanilla Skyrim race carries converts onto a stand-in
vanilla race, so a `GetIsRace` naming that race cannot become a race test
without catching every actor of the stand-in.  Each such race gets a FACT its
actors join, and `GetIsRace` becomes `GetInFaction` on it.

See: docs/commentary/tes5_import_conditions.md#plugin-authored-races
"""

import struct

from .equivalents import TES4_RACE_FID_TO_EDID
from .text_reader import get_formid, get_str, remap_formid
from .writer import pack_record, pack_string_subrecord, pack_subrecord

#: Output-space RACE FormID -> the marker FACT its actors join.
_faction_by_race: dict = {}

#: EditorID prefix of a race's marker FACT; a dependent adopts its master's by it.
_EDID_PREFIX = 'TES4RaceFaction_'


def is_authored_race(race_fid: int) -> bool:
    """Whether actors of this race convert onto a stand-in vanilla race."""
    return (race_fid & 0x00FFFFFF) not in TES4_RACE_FID_TO_EDID


def race_faction(race_fid: int) -> int:
    """The marker FACT of an output-space RACE FormID, or 0."""
    return _faction_by_race.get(race_fid, 0)


def _races(by_type: dict, master_export: dict) -> dict:
    """{output-space FormID: RACE record}, this plugin's own overriding its masters'."""
    races = {remap_formid(int(k, 16), is_own_id=True): r
             for k, r in (master_export or {}).items()
             if r.get('Signature') == 'RACE'}
    races.update((get_formid(r, 'FormID'), r) for r in by_type.get('RACE', ()))
    return races


def _fact_record(fid: int, edid: str) -> bytes:
    """A bare marker FACT."""
    subs = pack_string_subrecord('EDID', edid) + pack_subrecord('DATA', struct.pack('<I', 0))
    return pack_record('FACT', fid, 0, subs)


def _adopt_or_create(fact_edid: str, master_index, writer) -> int:
    """The converted master's FACT of this EditorID, else a new one in this plugin."""
    fid = master_index.find_by_edid(b'FACT', fact_edid) if master_index is not None else 0
    if not fid:
        fid = writer.derive_formid('FACT', fact_edid)
        writer.add_record('FACT', _fact_record(fid, fact_edid))
    return fid


def build_race_factions(by_type: dict, ctx, writer) -> None:
    """Adopt or create the marker FACT of every plugin-authored race in reach.

    See: docs/commentary/tes5_import_conditions.md#plugin-authored-races
    """
    _faction_by_race.clear()
    master_index = getattr(ctx, 'master_index', None)
    by_edid = {}
    for race_fid, rec in sorted(_races(by_type, getattr(ctx, 'master_export', None)).items()):
        edid = get_str(rec, 'EditorID')
        if not edid or not is_authored_race(race_fid):
            continue
        fact_edid = _EDID_PREFIX + edid
        if fact_edid not in by_edid:
            by_edid[fact_edid] = _adopt_or_create(fact_edid, master_index, writer)
        _faction_by_race[race_fid] = by_edid[fact_edid]
    print(f"  Plugin-authored race factions: {len(_faction_by_race)}")

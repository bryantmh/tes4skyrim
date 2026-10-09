"""
Adopting a converted master's synthesized support records.

The `TES4*` globals and the voice types have no TES4 source
FormID, so the companion manifest cannot name them and a dependent plugin finds
them in its masters by EditorID. A plugin whose masters supply none of them --
a root master, or the Morroblivion patch, whose only master borrows them from a
file the patch does not list -- creates its own.

See: docs/commentary/tes5_import_pipeline.md#phase-0-dependent-skips-support-records
"""

from asset_convert.audio.voice_races import (load_race_voices, vtyp_edid,
                                            master_voice_exports)

from ..packages.escort_when_near import (ESCORT_WHEN_NEAR_EDID,
                                         set_escort_template_fid)
from .equivalents import CUSTOM_VTYP_EDIDS, VTYP_EDID_BY_FID, set_voice_type
from .owned_records import PLAYER_ATTRIBUTE_GLOBALS, WELL_KNOWN_PROPERTIES

#: Synthesized stand-in records -> signature; a mastered plugin adopts the master's.
_TES4_SPECIAL_RECORD_SIGS = {
    'TES4Fame': b'GLOB',
    'TES4Infamy': b'GLOB',
    'TES4GoldFenced': b'GLOB',
    'TES4ControlsDisabled': b'GLOB',
    **dict.fromkeys(PLAYER_ATTRIBUTE_GLOBALS, b'GLOB'),
}


def _adopt_well_known(index) -> int:
    """Bind each `TES4*` script property to the master's record; how many bound."""
    found = 0
    for edid, sig in _TES4_SPECIAL_RECORD_SIGS.items():
        fid = index.find_by_edid(sig, edid)
        if fid:
            WELL_KNOWN_PROPERTIES[edid] = fid
            found += 1
    return found


def _adopt_voice(index, voice_edid: str, race_edid: str, gender: str) -> bool:
    """Register one master VTYP for a race and gender; False when none has it."""
    fid = index.find_by_edid(b'VTYP', voice_edid)
    if fid:
        set_voice_type(race_edid, gender, fid)
        VTYP_EDID_BY_FID[fid] = voice_edid
    return bool(fid)


def _adopt_race_voices(index, master_dirs, output_root=None) -> int:
    """Adopt the VTYP of every race the masters' own RACE records define."""
    adopted = 0
    for folder, _ in master_voice_exports(master_dirs, output_root):
        try:
            races = load_race_voices(folder, include_masters=False)
        except OSError:
            continue
        for race_edid, key in sorted(races.by_race_edid.items()):
            for gender in ('Male', 'Female'):
                adopted += _adopt_voice(index, vtyp_edid(key, gender),
                                        race_edid, gender)
    return adopted


def adopt_master_special_records(ctx, master_dirs) -> int:
    """Adopt the masters' support records; how many, 0 when they supply none.

    `master_dirs` are the masters' export folders, whose RACE records name the
    voice types to look for.
    """
    index = getattr(ctx, 'master_index', None)
    if index is None:
        return 0
    found = _adopt_well_known(index)
    set_escort_template_fid(index.find_by_edid(b'PACK', ESCORT_WHEN_NEAR_EDID))
    if found:
        print(f"  Adopted {found} synthesized master records "
              f"(TES4ControlsDisabled, TES4Fame, ...)")
    voices = sum(_adopt_voice(index, voice_edid, race_edid, gender)
                 for voice_edid, (race_edid, gender)
                 in CUSTOM_VTYP_EDIDS.items())
    derived = _adopt_race_voices(index, master_dirs,
                                getattr(ctx, 'output_root', None))
    if voices or derived:
        extra = f"; {derived} from the master's own races" if derived else ''
        print(f"  Adopted {voices + derived} master voice types (VTYP){extra}")
    return found + voices + derived

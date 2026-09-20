"""
The sounds and bark recordings a Morrowind plugin ships.

Morrowind keeps neither in its BSA: sound effects sit loose under `Data
Files\\Sound\\` and the voice recordings under `Sound\\Vo\\`, so the ordinary
archive walk reaches none of them. Both are therefore copied by name, and both
are scoped to what THIS plugin owns -- a plugin ships a file when one of its
own records names it and no master already does -- so an expansion never
re-copies its master's tree.

See: docs/commentary/asset_convert_audio.md#morrowind-barks
See: docs/commentary/tes4_export_morrowind.md#which-sounds-a-plugin-ships
"""

import os
from pathlib import Path

from asset_convert.audio.morrowind_voice import (stage_bark_voices,
                                                 stage_say_sounds)
from asset_convert.sources.bsa_extract_morrowind import copy_loose_sounds

#: The staging bucket of a scripted `Say` line; the importer's voicemap names its real folders.
_SAY_VOICE_FOLDER = 'Imperial'


def collect_bark_voices(extract_dir, source_file) -> list:
    """`(info_fid, race_edid, sex, named_file)` for each exported bark.

    Reads what the export already wrote, so the FormID naming the staged file
    is the one the importer converts. A scripted `Say` line carries no race --
    it is spoken by whoever runs the script -- and falls back to the generic
    voice folder rather than being skipped.
    See: docs/commentary/asset_convert_audio.md#morrowind-barks
    """
    from output_layout import record_dir
    from tes5_import.base.equivalents import TES4_RACE_FID_TO_EDID
    from tes5_import.base.text_reader import parse_export_file
    own = str(record_dir(str(extract_dir), source_file))
    barks = []
    for rec in parse_export_file(os.path.join(own, 'INFO.txt')):
        named = rec.get('MorrowindVoice')
        if not named:
            continue
        race = rec.get('BarkRace')
        edid = (TES4_RACE_FID_TO_EDID.get(int(race, 16) & 0x00FFFFFF)
                if race else _SAY_VOICE_FOLDER)
        barks.append((int(rec.get('FormID', '0'), 16), edid,
                      rec.get('BarkSex', '0'), named))
    return barks


def collect_say_sounds(extract_dir, source_file) -> list:
    """The recording each mouthless `Say` SOUN of this plugin names.

    See: docs/commentary/morrowind_runtime.md#scripted-say
    """
    from output_layout import record_dir
    from tes5_import.base.text_reader import parse_export_file
    own = str(record_dir(str(extract_dir), source_file))
    return [rec['FNAM.Filename']
            for rec in parse_export_file(os.path.join(own, 'SOUN.txt'))
            if rec.get('MorrowindSay') and rec.get('FNAM.Filename')]


def stage_voices(extract_dir, source_file, sound_dir, asset_dir) -> int:
    """Stage every bark and `Say` recording this plugin's export names.

    See: docs/commentary/asset_convert_audio.md#morrowind-barks
    """
    return (stage_bark_voices(collect_bark_voices(extract_dir, source_file),
                              sound_dir, asset_dir,
                              os.path.basename(source_file))
            + stage_say_sounds(collect_say_sounds(extract_dir, source_file),
                               sound_dir, asset_dir))


def owned_sounds(extract_dir, source_file) -> set:
    """The loose sounds this plugin ships: what it names, less what a master names.

    Replaces the masterless gate loose music still uses: music is folder-scanned
    by the engine and has no per-file owner, but every sound is named by a SOUN,
    so an expansion ships exactly the files it adds.
    See: docs/commentary/tes4_export_morrowind.md#which-sounds-a-plugin-ships
    """
    from asset_convert.lod.terrain_lod import master_names
    from asset_convert.sources.morrowind_sound_scope import owned_files
    from output_layout import record_dir
    own = str(record_dir(str(extract_dir), source_file))
    masters = [str(record_dir(str(extract_dir), name))
               for name in master_names(own)]
    return owned_files(own, masters)


def extract_loose_audio(data_path, extract_dir, asset_dir_name,
                        source_file) -> dict:
    """Copy this plugin's loose sounds and stage its bark voices.

    See: docs/commentary/asset_convert_audio.md#morrowind-barks
    """
    asset_dir = Path(extract_dir) / asset_dir_name
    owned = owned_sounds(extract_dir, source_file)
    sounds = copy_loose_sounds(data_path, asset_dir, owned)
    print(f"Loose Morrowind sounds: {sounds} of {len(owned)} "
          f"owned files copied")
    voices = stage_voices(extract_dir, source_file,
                          Path(data_path) / 'Sound', asset_dir)
    print(f"Morrowind bark voices staged: {voices}")
    return {'sounds': sounds, 'voices': voices}

"""
Stage Morrowind's voiced barks where the TES4 voice pipeline expects them.

Morrowind names each bark's audio outright, while `organize_voice_files`
matches `<prefix>_<infoFID>_<n>.<ext>` under
`sound/Voice/<plugin>/<Race>/<Gender>/`. The two differ only in the FILENAME,
so each referenced file is copied into that layout under the name its INFO
gives it, and the ordinary pipeline does the rest -- lip track, xWMA, fuz.

The bark's own authored race and sex pick the folder, so a line keeps the
voice it was recorded for even when the file sits elsewhere in the tree.

See: docs/commentary/asset_convert_audio.md#morrowind-barks
"""

import os
import shutil
from pathlib import Path

#: Where the staged tree goes, relative to a source's asset root.
_VOICE_ROOT = ('sound', 'Voice')

#: The audio extensions a source line may carry.
_AUDIO_EXTS = ('.mp3', '.wav', '.xwm')

#: Morrowind's sex folders, as the TES4 walker reads them (first letter).
_SEX_FOLDER = {'0': 'M', '1': 'F'}


def source_file(sound_dir, named: str):
    """The file a bark names, trying each audio extension for its stem.

    A script or record may say `.wav` where the tree ships `.mp3`; Morrowind
    resolves by stem, so the extension is advisory.
    """
    rel = named.replace('/', chr(92)).lstrip(chr(92))
    direct = Path(sound_dir).joinpath(*rel.split(chr(92)))
    if direct.is_file():
        return direct
    stem = os.path.splitext(str(direct))[0]
    for ext in _AUDIO_EXTS:
        candidate = Path(stem + ext)
        if candidate.is_file():
            return candidate
    return None


def find_sound_dir(export_dir) -> 'Path | None':
    """The registered Data folder holding Morrowind's loose `Sound` tree.

    A plugin imported from a mod archive never names a Data folder of its own,
    but its barks still play Morrowind's audio, so the tree is found by asking
    the registry rather than by the plugin's own source.
    """
    from asset_convert.sources import source_registry
    for row in source_registry.directories(export_dir):
        candidate = Path(row.get('path', '')) / 'Sound'
        if (candidate / 'Vo').is_dir():
            return candidate
    return None


def stage_say_sounds(named_files, sound_dir, asset_dir) -> int:
    """Copy each mouthless `Say` line's recording under `sound/`; how many were new.

    The copy keeps the SOURCE's extension under the name the SOUN states, so
    the sound stage transcodes an `.mp3` a script called `.wav`.
    See: docs/commentary/morrowind_runtime.md#scripted-say
    """
    staged = 0
    for named in named_files:
        source = source_file(sound_dir, named)
        if source is None:
            continue
        rel = named.replace('/', chr(92)).lstrip(chr(92)).split(chr(92))
        dest = Path(asset_dir).joinpath('sound', *rel).with_suffix(
            source.suffix.lower())
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        staged += 1
    return staged


def stage_bark_voices(barks, sound_dir, asset_dir, plugin: str) -> int:
    """Copy each bark's audio into the TES4 voice layout; how many were staged.

    `barks` yields `(info_fid, race_edid, sex, named_file)`. The take number is
    always 1 -- a TES3 bark names ONE file, where Oblivion holds several takes
    per line -- and the prefix is supplied by the importer's voice map.
    """
    root = Path(asset_dir).joinpath(*_VOICE_ROOT, plugin)
    staged = 0
    for info_fid, race, sex, named in barks:
        source = source_file(sound_dir, named)
        if source is None or not race:
            continue
        folder = root / race / _SEX_FOLDER.get(str(sex), 'M')
        dest = folder / f'mw_{info_fid & 0xFFFFFF:08x}_1{source.suffix.lower()}'
        if dest.exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        staged += 1
    return staged

"""Voice-type identity for a plugin's own races.

Oblivion names a voice folder after the race's DISPLAY name (the RACE record's
FULL), not its EditorID:

    sound\\voice\\<plugin>\\<FULL lowercased>\\<gender>\\<topic>_<fid>_<n>.mp3

In the English game the two strings are identical, so the distinction never
shows and a table keyed on EditorIDs works by accident.  In a localised plugin
they diverge and everything keyed on the EditorID stops matching the folders on
disk.  Measured on Nehrim.esm:

    RACE EditorID                     FULL (= folder name)
    Alemanne1, Alemanne2, Alemanne1Gabor, ...   Alemanne
    HighElf                                     Hochelf
    DarkElf                                     Eraterna
    Argonian                                    Argonier

Both halves of the voice pipeline resolve identity through this module so they
cannot disagree:

  * the importer creates one VTYP record per (voice key, gender) and points
    EVERY race EditorID sharing that FULL at it, so the seven Alemanne races
    share one voice type;
  * the sound stage maps each source race folder to the same VTYP EditorID, so
    generic per-race lines land in the folder the speaker's VTYP names.

For Oblivion this reproduces the previously hardcoded names exactly — "High
Elf" -> HighElf, "Dark Elf" -> DarkElf, "Golden Saint" -> GoldenSaint — so
every race keeps the voice type it already had.

A race with no FULL is SKIPPED rather than falling back to its EditorID: the
folder on disk is the display name, so a race that has no display name has no
folder, and giving it an identity here would point it at an empty directory.

See: docs/commentary/asset_convert_audio.md#race-identity-spans-the-masters
"""

from pathlib import Path

__all__ = ['voice_key', 'vtyp_edid', 'load_race_voices', 'RaceVoices',
           'master_race_dirs']


def voice_key(name: str) -> str:
    """Collapse a race display name into the EditorID fragment used for VTYPs.

    Keeps letters and digits, drops everything else, preserves case:
    "High Elf" -> "HighElf", "Halb-Aeterna" -> "HalbAeterna".
    """
    return ''.join(c for c in (name or '') if c.isalnum())


def vtyp_edid(key: str, gender: str) -> str:
    """VTYP EditorID for a voice key. *gender* is 'Male'/'Female' or 'M'/'F'."""
    g = 'Female' if gender.upper().startswith('F') else 'Male'
    return f'TES4{g}{key}'


class RaceVoices:
    """Resolved race identity for one plugin's export.

    by_race_edid: RACE EditorID -> voice key
    by_folder:    lowercased voice-folder name -> voice key
    keys:         every distinct voice key, sorted (deterministic FormIDs)
    """

    __slots__ = ('by_race_edid', 'by_folder', 'keys')

    def __init__(self, by_race_edid, by_folder, keys):
        self.by_race_edid = by_race_edid
        self.by_folder = by_folder
        self.keys = keys

    def folder_key(self, folder: str) -> 'str | None':
        """Voice key for a source race folder, or None if the plugin has no
        race by that display name."""
        return self.by_folder.get((folder or '').strip().lower())

    def __bool__(self):
        return bool(self.keys)


def iter_records(txt: Path):
    """Same export-text idiom as wearable_plan._iter_records."""
    if not txt.is_file():
        return
    body = txt.read_text(encoding='utf-8', errors='replace')
    for chunk in body.split('---RECORD_BEGIN---')[1:]:
        rec = {}
        for line in chunk.split('---RECORD_END---')[0].splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            rec[k] = v
        if rec:
            yield rec


def master_race_dirs(export_dir: Path) -> list:
    """Sibling export directories of each master named in `_HEADER.txt`.

    Mirrors how the importer resolves masters, so both halves of the voice
    pipeline read the same RACE.txt and cannot disagree on a voice type.
    """
    header = export_dir / '_HEADER.txt'
    if not header.is_file():
        return []
    try:
        lines = header.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []
    names = [ln.partition('=')[2].strip() for ln in lines
             if ln.startswith('Master[')]
    dirs = [export_dir.parent / n for n in names]
    return [d for d in dirs if d.is_dir() and d != export_dir]


def _iter_race_records(export_dir: Path):
    """Every RACE record affecting this plugin: masters first, then its own."""
    for d in master_race_dirs(export_dir) + [export_dir]:
        yield from iter_records(d / 'RACE.txt')


def load_race_voices(export_dir) -> RaceVoices:
    """Voice identity for a plugin's races, its masters' races included.

    A plugin that declares masters voices its actors out of THEIR race folders
    (an Oblivion mod's `high elf/` recordings are Oblivion.esm's race), so the
    masters are read first and the plugin's own RACE records override them.
    A plugin with no RACE.txt anywhere yields an empty result. A race with no
    FULL is skipped: the folder on disk IS the display name.

    See: docs/commentary/asset_convert_audio.md#race-identity-spans-the-masters
    """
    by_race_edid: dict = {}
    by_folder: dict = {}

    for rec in _iter_race_records(Path(export_dir)):
        edid = (rec.get('EditorID') or '').strip()
        full = (rec.get('FULL') or '').strip()
        key = voice_key(full)
        if not edid or not key:
            continue
        by_race_edid[edid] = key
        by_folder.setdefault(full.lower(), key)
        by_folder.setdefault(edid.lower(), key)

    return RaceVoices(by_race_edid, by_folder, sorted(set(by_race_edid.values())))

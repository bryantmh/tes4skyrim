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

from core.plugin_masters import export_encoding, master_dirs

__all__ = ['voice_key', 'vtyp_edid', 'load_race_voices', 'RaceVoices',
           'master_race_dirs']


def voice_key(name: str) -> str:
    """A display name as a VTYP key: letters and digits kept, case kept."""
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
        """Store the resolved race, folder and key maps."""
        self.by_race_edid = by_race_edid
        self.by_folder = by_folder
        self.keys = keys

    def folder_key(self, folder: str) -> 'str | None':
        """Voice key for a source race folder, or None when unknown.

        Falls back to the alnum-collapsed spelling: the BSA spells some
        folders with spaces the records do not have ('dark seducer' vs
        'DarkSeducer'), and those must resolve to the same voice key
        instead of synthesising a duplicate voice type.
        """
        lowered = (folder or '').strip().lower()
        return self.by_folder.get(lowered) or self.by_folder.get(
            voice_key(lowered))

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
    """Each exported master's folder, resolved as the importer resolves it."""
    return [Path(d) for d in master_dirs(export_dir)
            if Path(d) != Path(export_dir)]


def _iter_race_records(export_dir: Path):
    """Every RACE record affecting this plugin: masters first, then its own."""
    for d in master_race_dirs(export_dir) + [export_dir]:
        yield from iter_records(d / 'RACE.txt')


def _export_codec(export_dir: Path) -> str:
    """The text codec of this export tree.

    The caller's folder is the shared asset root, which owns no `_HEADER.txt`
    when the mod ships several plugins; masters are the same install's game,
    so their header answers identically.
    """
    dirs = [export_dir] + master_race_dirs(export_dir)
    for folder in dirs:
        codec = export_encoding(str(folder))
        if (folder / "_HEADER.txt").is_file() and codec != "cp1252":
            return codec
    return export_encoding(str(export_dir))


def _bsa_spelling(name: str, codec: str) -> str:
    """`name` as the BSA extractor spells it on disk, or '' when identical.

    Archive names decode as latin-1, so re-encoding the export-text name in
    the export's codec and decoding those bytes as latin-1 reproduces the
    extracted folder exactly. ASCII names spell the same either way.
    """
    try:
        alias = name.encode(codec).decode("latin-1")
    except (LookupError, UnicodeError):
        return ""
    return "" if alias == name else alias


def load_race_voices(export_dir) -> RaceVoices:
    """Voice identity for a plugin's races, its masters' races included.

    Masters' races are read first (a mod voices its actors out of their
    folders) and the plugin's own RACE records override them. A plugin with
    no RACE.txt anywhere yields an empty result. A race with no FULL is
    skipped: the folder on disk IS the display name. Each folder is also
    indexed under its BSA-extracted latin-1 spelling (see _bsa_spelling).

    See: docs/commentary/asset_convert_audio.md#race-identity-spans-the-masters
    """
    by_race_edid: dict = {}
    by_folder: dict = {}
    codec = _export_codec(Path(export_dir))

    for rec in _iter_race_records(Path(export_dir)):
        edid = (rec.get('EditorID') or '').strip()
        full = (rec.get('FULL') or '').strip()
        key = voice_key(full)
        if not edid or not key:
            continue
        by_race_edid[edid] = key
        by_folder.setdefault(full.lower(), key)
        by_folder.setdefault(edid.lower(), key)
        collapsed = voice_key(full).lower()
        if collapsed != full.lower():
            by_folder.setdefault(collapsed, key)
        alias = _bsa_spelling(full, codec)
        if alias:
            by_folder.setdefault(alias.lower(), key)

    return RaceVoices(by_race_edid, by_folder, sorted(set(by_race_edid.values())))

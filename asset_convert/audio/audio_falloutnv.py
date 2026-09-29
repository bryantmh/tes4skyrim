"""FO3/FNV voice layout: no gender level, Ogg Vorbis, VTYP-named folders.

Oblivion records a voice line under a race folder split by gender, as MP3:

    sound/voice/<plugin>/<race FULL>/<m|f>/<topic>_<fid>_<n>.mp3

FO3/FNV names the folder after the VOICE TYPE itself and carries the gender in
that name, so there is no gender level to descend and no race to map:

    sound/voice/<plugin>/femaleadult01default/<topic>_<fid>_<n>.ogg

The folder name is the VTYP EditorID lowercased, so it needs no translation --
the importer stamps NPC_.VTCK straight through and the two sides agree by
construction. Gender is read off the name only to satisfy callers that still
want it; a folder matching neither prefix is left as male, which is what the
robot and creature voices are.

See: docs/commentary/tes4_export_falloutnv.md#voice-files
"""

import re
from pathlib import Path

#: FO3/FNV ships Ogg Vorbis where Oblivion ships MP3; ffmpeg reads both.
FALLOUT_VOICE_EXT = 'ogg'

_FEMALE_PREFIX = re.compile(r'^(female|playervoicefemale)', re.IGNORECASE)


def is_fallout_voice_root(plugin_dir) -> bool:
    """True when this plugin's voice tree has no gender level.

    Decided by looking for a real file one level under a voice folder, which is
    where FO3/FNV puts its recordings and where Oblivion has only directories.
    """
    for race_dir in plugin_dir.iterdir():
        if not race_dir.is_dir():
            continue
        for entry in race_dir.iterdir():
            if entry.is_file() and entry.suffix.lower() == '.' + FALLOUT_VOICE_EXT:
                return True
    return False


def folder_gender(folder: str) -> str:
    """'F' for a female-named voice folder, else 'M' (robots and creatures)."""
    return 'F' if _FEMALE_PREFIX.match(folder or '') else 'M'


def load_voice_type_edids(export_dir) -> dict:
    """lowercased VTYP EditorID -> the EditorID as authored, from VTYP.txt.

    The folder on disk is lowercase while the record keeps its authored case,
    and the engine matches the folder to the actor's VTCK by EditorID, so both
    halves must spell it the way the record does.

    `export_dir` may be a nested mod's ASSET folder: every plugin record dir it
    serves is read (`output_layout.record_dirs_for_assets`), the first wins.
    See: docs/commentary/tes5_import_mod_merge.md#export-root-resolution
    """
    from output_layout import record_dirs_for_assets
    out = {}
    for rec_dir in record_dirs_for_assets(export_dir):
        txt = Path(rec_dir) / 'VTYP.txt'
        if not txt.is_file():
            continue
        own = {}
        for line in txt.read_text(encoding='utf-8',
                                  errors='replace').splitlines():
            if line.startswith('EditorID='):
                edid = line[9:].strip()
                if edid:
                    own[edid.lower()] = edid
        for low, edid in own.items():
            out.setdefault(low, edid)
    return out


def voice_type_edid(folder: str, edids: dict = None) -> str:
    """VTYP EditorID a FO3/FNV voice folder names (the folder IS the type)."""
    name = (folder or '').strip()
    return (edids or {}).get(name.lower(), name)

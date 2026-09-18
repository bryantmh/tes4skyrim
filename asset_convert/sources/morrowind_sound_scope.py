"""
Which loose Morrowind sounds a plugin ships.

Morrowind's audio is LOOSE under a Data folder every plugin in the install
shares, so "what is on disk" says nothing about who owns it. A plugin ships a
sound file when it defines a record naming that file and no master of it
already does -- the ownership question `_is_masterless` asks of music, asked
per FILE so an expansion ships only what it adds.

See: docs/commentary/tes4_export_morrowind.md#which-sounds-a-plugin-ships
"""

import os

from tes5_import.base.text_reader import parse_export_file

#: The SOUN key holding the path, relative to `Sound\`.
_FILENAME_KEY = 'FNAM.Filename'


def normalize(path: str) -> str:
    """One sound path as a comparison key: lowercase, backslashes, unrooted."""
    return path.replace('/', chr(92)).lstrip(chr(92)).lower()


def referenced_files(export_dir: str) -> set:
    """Every sound file the records in `export_dir` name, as comparison keys.

    SOUN.FNAM is the only record-side path: DOOR/LIGH/ACTI name a SOUN by ID,
    which resolves to that SOUN's own file and needs no separate entry.
    """
    found = set()
    for rec in parse_export_file(os.path.join(export_dir, 'SOUN.txt')):
        path = rec.get(_FILENAME_KEY)
        if path:
            found.add(normalize(path))
    return found


def owned_files(export_dir: str, master_dirs) -> set:
    """The sound files this plugin ships: the ones it names that no master names.

    An unconverted master contributes nothing, exactly as a missing master
    contributes no records -- its sounds then belong to whoever names them
    next, which keeps a standalone plugin self-sufficient.
    """
    mine = referenced_files(export_dir)
    for folder in master_dirs:
        mine -= referenced_files(folder)
    return mine

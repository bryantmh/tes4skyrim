"""Tools > Patch Quest Journal: patch the journal movies the game really loads.

For each journal movie name the copy the game would load is found -- a loose
file in `Data/Interface` beats every archive, and among archives the one whose
plugin loads last wins -- patched, and written back as a LOOSE file, so it wins
over whatever it was read from. A loose original that is not already patched
is kept beside it as `<name>.mwrt-backup`; a movie read from an archive has no
loose original, and deleting the written file undoes the patch.

The write replaces the directory entry instead of writing through it, so a
mod manager's hard-linked copy of the original is left untouched.

See: docs/commentary/morrowind_runtime.md#journal-stage-text
"""

import glob
import os
import struct
import threading

from asset_convert.sources.bsa_extract import read_bsa_files
from asset_convert.sources.skyrim_assets import find_skyrim_data
from asset_convert.ui.journal_patch import is_patched, patch_movie
from core.gui.config import CLR, scan_skyrim_load_order

#: The journal movies: vanilla's and SkyUI's, and Quest Journal Overhaul's.
MOVIE_NAMES = ('quest_journal.swf', 'questjournal.swf')
BACKUP_SUFFIX = '.mwrt-backup'
TITLE = 'Patch Quest Journal'

#: What happened to one movie, which picks the dialog's headline.
PATCHED, SKIPPED, FAILED = 'patched', 'skipped', 'failed'


def _plugin_ranks(data_dir: str) -> dict:
    """{active plugin stem: load position}, lowercased."""
    ordered, active = scan_skyrim_load_order(data_dir)
    return {os.path.splitext(name)[0].lower(): i
            for i, name in enumerate(ordered) if name in active}


def _archive_rank(bsa_name: str, ranks: dict) -> int:
    """The load position of the plugin that loads `bsa_name`, or -1.

    `X.bsa` and `X - Anything.bsa` both belong to plugin `X`.
    """
    stem = os.path.splitext(bsa_name)[0].lower()
    for key in (stem, stem.split(' - ', 1)[0]):
        if key in ranks:
            return ranks[key]
    return -1


def _archived_movie(data_dir: str, name: str) -> tuple:
    """(bytes, archive name) from the winning archive holding the movie."""
    ranks = _plugin_ranks(data_dir)
    best, best_name, best_rank = None, None, -2
    for bsa in sorted(glob.glob(os.path.join(data_dir, '*.bsa'))):
        try:
            found = read_bsa_files(bsa, ['interface\\' + name])
        except (OSError, ValueError, struct.error):
            continue
        rank = _archive_rank(os.path.basename(bsa), ranks)
        if found and rank > best_rank:
            best = next(iter(found.values()))
            best_name, best_rank = os.path.basename(bsa), rank
    return best, best_name


def _write_replacing(path: str, data: bytes) -> None:
    """Write `data` to a new file and swap it in for `path`."""
    temp = path + '.tmp'
    with open(temp, 'wb') as fh:
        fh.write(data)
    os.replace(temp, path)


def patch_one(data_dir: str, name: str) -> tuple:
    """Patch the effective copy of one movie: (outcome, report line).

    The outcome is PATCHED or SKIPPED; a failure raises instead.
    """
    target = os.path.join(data_dir, 'Interface', name)
    if os.path.isfile(target):
        with open(target, 'rb') as fh:
            raw = fh.read()
        source = 'loose file'
    else:
        raw, source = _archived_movie(data_dir, name)
        if raw is None:
            return SKIPPED, f'{name}: not installed -- skipped'
    patched, kinds = patch_movie(raw)
    if patched is None:
        return SKIPPED, (f'{name} (from {source}): not a journal this patch '
                         'knows -- skipped')
    if source == 'loose file' and not is_patched(raw):
        _write_replacing(target + BACKUP_SUFFIX, raw)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    _write_replacing(target, patched)
    return PATCHED, f'{name} (from {source}): patched {", ".join(kinds)}'


def patch_installed_journals(data_dir: str) -> list:
    """Patch every journal movie the game would load: [(outcome, line)]."""
    results = []
    for name in MOVIE_NAMES:
        try:
            results.append(patch_one(data_dir, name))
        except (OSError, ValueError, struct.error) as exc:
            results.append((FAILED, f'{name}: FAILED -- {exc}'))
    return results


def report_status(outcomes: list) -> tuple:
    """The dialog's (headline, color): any failure wins, then any patch."""
    if FAILED in outcomes:
        return 'Patch Failed', CLR['red']
    if PATCHED in outcomes:
        return 'Patched Successfully', CLR['green']
    return 'Nothing Patched', CLR['yellow']


def run_patch(app) -> None:
    """The menu command: patch on a worker, then report on the UI thread."""
    data_dir = app.tes5_var.get().strip() or find_skyrim_data()
    if not data_dir or not os.path.isdir(data_dir):
        app.info(TITLE, 'Could not find the Skyrim Special Edition Data '
                        'folder. Set the Skyrim SE Data Directory first.')
        return

    def _worker():
        """Patch, then hop back: tkinter is not thread-safe."""
        results = patch_installed_journals(data_dir)
        status = report_status([outcome for outcome, _ in results])
        body = ('\n'.join(line for _, line in results)
                + '\n\nClicking an objective now shows the journal text its '
                'stage had; click it again for the current text. Needs '
                'MorrowindRuntime.dll, and works for objectives shown after '
                'it was installed.')
        app.root.after(0, lambda: app.info(TITLE, body, status=status))

    threading.Thread(target=_worker, daemon=True).start()

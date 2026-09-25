"""Build > Quest Journal Stage Text: the journal movies the game loads, patched
into an installable mod zip.

For each journal movie name the copy the game would load is found -- a loose
file in `Data/Interface` beats every archive, and among archives the one whose
plugin loads last wins -- patched, and zipped into `Finished Mods` with the
archive root as the Data folder. Nothing in the game folder is written;
uninstalling the mod is the undo.

The patch replaces any earlier copy of itself, so a movie that is already this
mod's output patches cleanly, but it carries the journal it was first built
from: picking up a Quest Journal Overhaul or SkyUI update needs this mod
disabled while it is rebuilt.

See: docs/commentary/tes_runtime_journal.md#journal-stage-text
"""

import glob
import os
import struct
import threading

from asset_convert.sources.bsa_extract import read_bsa_files
from asset_convert.sources.skyrim_assets import find_skyrim_data
from asset_convert.ui.journal_patch import is_patched, patch_movie
from core.gui.config import CLR, scan_skyrim_load_order
from output_layout import finished_dir, write_mod_zip

#: The journal movies: vanilla's and SkyUI's, and Quest Journal Overhaul's.
MOVIE_NAMES = ('quest_journal.swf', 'questjournal.swf')
MOD_NAME = 'Quest Journal Stage Text'
TIP = ("Make quest objectives clickable in Skyrim's journal: clicking one shows "
       "the journal text its stage had, clicking it again the current text. "
       "Patches the journal your game loads (vanilla, SkyUI or Quest Journal "
       f"Overhaul) into output/Finished Mods/{MOD_NAME}.zip; install it after "
       "that UI mod. Needs TESRuntime.dll")

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


def _loaded_movie(data_dir: str, name: str) -> tuple:
    """(bytes, where it came from) for the copy the game loads, or (None, None)."""
    loose = os.path.join(data_dir, 'Interface', name)
    if os.path.isfile(loose):
        with open(loose, 'rb') as fh:
            return fh.read(), 'loose file'
    return _archived_movie(data_dir, name)


def patch_one(data_dir: str, name: str) -> tuple:
    """(outcome, report line, patched bytes or None) for one movie name.

    The outcome is PATCHED or SKIPPED; a failure raises instead.
    """
    raw, source = _loaded_movie(data_dir, name)
    if raw is None:
        return SKIPPED, f'{name}: not installed -- skipped', None
    patched, kinds = patch_movie(raw)
    if patched is None:
        return SKIPPED, (f'{name} (from {source}): not a journal this patch '
                         'knows -- skipped'), None
    line = f'{name} (from {source}): patched {", ".join(kinds)}'
    if is_patched(raw):
        line += (f' -- read from an earlier {MOD_NAME}; disable it and build '
                 'again to pick up a UI mod update')
    return PATCHED, line, patched


def build_journal_mod(data_dir: str, out_root) -> tuple:
    """Patch every loaded journal movie and zip them: ([(outcome, line)], zip).

    The zip path is None when nothing was patched or anything failed, so a
    partial mod is never written over a good one.
    """
    results, members = [], []
    for name in MOVIE_NAMES:
        try:
            outcome, line, data = patch_one(data_dir, name)
        except (OSError, ValueError, struct.error) as exc:
            outcome, line, data = FAILED, f'{name}: FAILED -- {exc}', None
        results.append((outcome, line))
        if data is not None:
            members.append(('Interface/' + name, data))
    if not members or any(outcome == FAILED for outcome, _ in results):
        return results, None
    zip_path = finished_dir(out_root) / f'{MOD_NAME}.zip'
    write_mod_zip(zip_path, members)
    return results, zip_path


def report_status(outcomes: list) -> tuple:
    """The dialog's (headline, color): any failure wins, then any patch."""
    if FAILED in outcomes:
        return 'Build Failed', CLR['red']
    if PATCHED in outcomes:
        return 'Built Successfully', CLR['green']
    return 'Nothing Patched', CLR['yellow']


def _report_body(results: list, zip_path) -> str:
    """The dialog text: one line per movie, then where the mod went."""
    body = '\n'.join(line for _, line in results)
    if zip_path is None:
        return body
    return (body + f'\n\nWrote {zip_path}\n\nInstall it with your mod manager '
            'and load it after your UI mod (SkyUI or Quest Journal Overhaul). '
            'Clicking an objective then shows the journal text its stage had; '
            'click it again for the current text. Needs TESRuntime.dll, '
            'and works for objectives shown after it was installed.')


def run_patch(app) -> None:
    """The menu command: build on a worker, then report on the UI thread."""
    data_dir = app.tes5_var.get().strip() or find_skyrim_data()
    if not data_dir or not os.path.isdir(data_dir):
        app.info(MOD_NAME, 'Could not find the Skyrim Special Edition Data '
                           'folder. Set the Skyrim SE Data Directory first.')
        return
    out_root = app.out_root()

    def _worker():
        """Build, then hop back: tkinter is not thread-safe."""
        results, zip_path = build_journal_mod(data_dir, out_root)
        status = report_status([outcome for outcome, _ in results])
        body = _report_body(results, zip_path)
        app.root.after(0, lambda: app.info(MOD_NAME, body, status=status))

    threading.Thread(target=_worker, daemon=True).start()

"""SKSE sidecars must ship LOOSE: never in a BSA, always in the zip.

A fragment packed into a BSA is invisible to the DLL, which lists the folder
with FindFirstFileA, so the mod registers no animation projects and every
converted creature stands still.
See: docs/reference/tes_runtime_fragments.md#never-packed
"""

import json
import os
import pathlib
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import convert
from asset_convert.sources.bsa_pack import LOOSE_ONLY_DIRS, _KNOWN_DIRS


def _mod_tree(root: pathlib.Path) -> pathlib.Path:
    """A converted plugin's output folder: plugin, BSA and a fragment."""
    src = root / 'output' / 'Oblivion.esm'
    frag = src / 'SKSE' / 'Plugins' / 'CreatureRuntime' / 'animation'
    frag.mkdir(parents=True)
    (root / 'output' / 'Finished Mods').mkdir(parents=True)
    (src / 'Oblivion.esm').touch()
    (src / 'Oblivion.bsa').touch()
    (src / 'sound').mkdir()
    (src / 'sound' / 'a.wav').touch()
    frag.joinpath('Oblivion.json').write_text(json.dumps({'version': 1}))
    return src


def test_skse_is_never_a_packed_misc_dir(tmp_path):
    """pack_bsas auto-discovers misc dirs; SKSE must not be one of them."""
    src = _mod_tree(tmp_path)
    misc = sorted(d.name for d in src.iterdir()
                  if d.is_dir() and d.name.lower() not in _KNOWN_DIRS
                  and not d.name.startswith('_bsa_staging_'))
    assert 'sound' in misc
    assert not [m for m in misc if m.lower() in LOOSE_ONLY_DIRS]


def test_zip_carries_the_fragment_loose(tmp_path):
    """The zip is the delivered mod, so an unpacked tree has to ride in it."""
    src = _mod_tree(tmp_path)
    out = str(tmp_path / 'output')
    assert convert.phase_pack_zip('Oblivion.esm', {}, output_dir=out)
    names = zipfile.ZipFile(
        tmp_path / 'output' / 'Finished Mods' / 'Oblivion.esm.zip').namelist()
    assert 'SKSE/Plugins/CreatureRuntime/animation/Oblivion.json' in names
    assert {'Oblivion.esm', 'Oblivion.bsa'} <= set(names)
    assert src.is_dir()

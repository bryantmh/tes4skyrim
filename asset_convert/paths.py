"""Filesystem anchors for asset_convert, independent of package depth.

Every module resolves the repo root and the bundled data directory from here,
so moving a module into a subpackage cannot silently repoint it: deriving them
per-module from `__file__` breaks the moment the module's depth changes, and
one `generated/` site created an empty directory rather than raising.
"""

from pathlib import Path

from output_layout import DATA_ROOT

#: Repository root: the directory holding `convert.py` and `external/`.
REPO = Path(__file__).resolve().parent.parent

#: Bundled data shipped inside the package (skeleton bones, body-wrap caches).
GENERATED = Path(__file__).resolve().parent / 'generated'

#: Default export and output trees, overridden by an explicit root (beside TESRACT.exe when frozen).
EXPORT = DATA_ROOT / 'export'
OUTPUT = DATA_ROOT / 'output'

#: Third-party executables committed under `external/`.
EXTERNAL = REPO / 'external'
HKXCMD = REPO / 'external' / 'hkxcmd' / 'hkxcmd.exe'
HKXCONV = REPO / 'external' / 'hkxconv' / 'hkxconv.exe'
LODGEN = REPO / 'external' / 'lodgen' / 'LODGenx64.exe'
MOPP_BRIDGE = REPO / 'external' / 'mopp_bridge' / 'dovah_hkp_mesh_mopp_bridge.exe'
#: xWMAEncode.exe, which the user supplies: kept with their data so a new TESRACT.exe still finds it.
XWMAENCODE = DATA_ROOT / 'external' / 'xwmaencode' / 'xWMAEncode.exe'

#: Native helper programs built by `python native/build.py --programs`.
SPT_ENGINE_DUMP = REPO / 'native' / 'dist' / 'spt_engine_dump.exe'

#: Compiled native extension modules built by `python native/build.py`.
NATIVE_DIST = REPO / 'native' / 'dist'

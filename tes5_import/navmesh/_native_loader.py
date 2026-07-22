"""Locate and import the compiled navmesh extension from native/dist/.

The .pyd lives outside the package (in `native/dist/`, alongside its sources and
build script, and committed there) rather than inside `tes5_import/navmesh/`, so
the Python tree holds no build artifacts.  That means a plain `from . import
_navmesh_native` cannot find it and the module has to be loaded by path.

The extension is REQUIRED.  Falling back to a Python implementation when it is
missing would make navmesh output depend on whether a build artifact happened to
be present, which breaks the pipeline's byte-reproducibility contract -- so a
missing or mismatched .pyd raises with instructions instead.
"""

import importlib.machinery
import importlib.util
import os
import sys
import sysconfig

_MOD = '_navmesh_native'
_DIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'native', 'dist')

_cached = None


def load_native():
    """Import and return the extension module (cached per process)."""
    global _cached
    if _cached is not None:
        return _cached

    # Already imported (e.g. a worker inherited it) -- reuse.
    if _MOD in sys.modules:
        _cached = sys.modules[_MOD]
        return _cached

    suffix = sysconfig.get_config_var('EXT_SUFFIX') or '.pyd'
    path = os.path.join(_DIST, _MOD + suffix)

    if not os.path.exists(path):
        # A .pyd built for a DIFFERENT Python version is the common case here
        # (the ABI tag is in the filename), so say which one was expected and
        # what is actually there rather than just "not found".
        have = []
        if os.path.isdir(_DIST):
            have = sorted(f for f in os.listdir(_DIST)
                          if f.startswith(_MOD) and f.endswith(('.pyd', '.so')))
        raise ImportError(
            'navmesh native extension not found.\n'
            '  expected: %s\n'
            '  present : %s\n'
            '  build it: python native/build.py'
            % (path, ', '.join(have) if have else '(none)'))

    loader = importlib.machinery.ExtensionFileLoader(_MOD, path)
    spec = importlib.util.spec_from_loader(_MOD, loader, origin=path)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: extension init can look itself up in sys.modules,
    # and process-pool workers then reuse this entry instead of re-loading.
    sys.modules[_MOD] = mod
    loader.exec_module(mod)
    _cached = mod
    return mod

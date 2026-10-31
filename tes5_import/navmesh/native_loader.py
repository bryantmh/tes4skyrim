"""Locate and import the compiled navmesh extension from native/dist/.

The .pyd lives outside the package (in `native/dist/`, alongside its sources and
build script, and committed there) rather than inside `tes5_import/navmesh/`, so
the Python tree holds no build artifacts.  That means a plain `from . import
_navgrow_native` cannot find it and the module has to be loaded by path.

The extension is REQUIRED.  Falling back to a Python implementation when it is
missing would make navmesh output depend on whether a build artifact happened to
be present, which breaks the pipeline's byte-reproducibility contract -- so a
missing or mismatched .pyd raises with instructions instead.  `NATIVE` is an
explicit switch, never a fallback: both paths produce identical output.
"""

import importlib.machinery
import importlib.util
import os
import sys
import sysconfig

_MOD = '_navgrow_native'
_DIST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'native', 'dist')

_cached = {}

#: True runs the native navmesh kernels, False the Python reference they mirror (TES_NAVMESH_IMPL=python).
NATIVE = os.environ.get('TES_NAVMESH_IMPL', 'native').strip().lower() != 'python'


def _not_found(mod, path):
    """The ImportError for a missing .pyd, naming what IS present.

    A .pyd built for a DIFFERENT Python version is the common case (the ABI
    tag is in the filename), so the message says which one was expected.
    """
    have = []
    if os.path.isdir(_DIST):
        have = sorted(f for f in os.listdir(_DIST)
                      if f.startswith(mod) and f.endswith(('.pyd', '.so')))
    return ImportError(
        'navmesh native extension not found.\n'
        '  expected: %s\n'
        '  present : %s\n'
        '  build it: python native/build.py'
        % (path, ', '.join(have) if have else '(none)'))


def load_native(mod=_MOD):
    """Import and return an extension module by name (cached per process).

    A module already in sys.modules (a worker inherited it) is reused.  It is
    registered there BEFORE exec: extension init can look itself up, and pool
    workers then reuse the entry instead of re-loading.
    """
    if mod in _cached:
        return _cached[mod]
    if mod in sys.modules:
        _cached[mod] = sys.modules[mod]
        return _cached[mod]
    path = os.path.join(_DIST, mod + (sysconfig.get_config_var('EXT_SUFFIX')
                                      or '.pyd'))
    if not os.path.exists(path):
        raise _not_found(mod, path)
    loader = importlib.machinery.ExtensionFileLoader(mod, path)
    m = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(mod, loader, origin=path))
    sys.modules[mod] = m
    loader.exec_module(m)
    _cached[mod] = m
    return m


def native_geos():
    """The navgrow extension with GEOS bound to shapely's own loaded library."""
    m = load_native(_MOD)
    m.bind_geos()
    return m

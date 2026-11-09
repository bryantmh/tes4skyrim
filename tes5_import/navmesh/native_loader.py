"""Locate and import the compiled navmesh extension from native/dist/.

The .pyd lives outside the package (in `native/dist/`, alongside its sources and
build script, and committed there) rather than inside `tes5_import/navmesh/`, so
the Python tree holds no build artifacts.  That means a plain `from . import
_navgrow_native` cannot find it and the module has to be loaded by path.

The extension is REQUIRED: the navmesh kernels exist only in C++, so a missing
or mismatched .pyd raises with build instructions.
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


#: Module-level constants of the corridor modules the native stages read, beside params.py's.
_MODULE_PARAMS = (('clean_validate', ('LIP_STANDOFF',)),
                  ('seeding', ('WALK_STEP', 'LATTICE_SPACING')),
                  ('door_reach', ('DOOR_REACH_SPACING', 'DOOR_REACH_MAX_NODES',
                                  'DOOR_SEED_SEARCH', 'DOOR_SEED_DROP', 'DOOR_REACH_THIN_SPACING')))


def native_params():
    """{name: float} of every tunable the native stages read.

    Imports the corridor modules inside the call: they import this module, so
    a module-scope import here would be a cycle.
    """
    from . import params
    out = {k: float(v) for k, v in vars(params).items()
           if k.isupper() and isinstance(v, (int, float))}
    for mod, names in _MODULE_PARAMS:
        module = importlib.import_module('.' + mod, __package__)
        out.update((name, float(getattr(module, name))) for name in names)
    return out


def native_core():
    """The navgrow extension with GEOS bound and the tunables set (once per process)."""
    m = native_geos()
    if not _cached.get('params'):
        m.set_params(native_params())
        _cached['params'] = True
    return m

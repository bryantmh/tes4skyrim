"""Per-phase dependency preflight.

Every pipeline phase declares the Python packages, native extensions and
external executables it cannot run without.  ``check_phases()`` resolves them
using the SAME lookup the phase itself uses (so a check can never pass while
the phase then fails to find the tool), and ``convert.py`` calls it before any
phase runs.

A missing dependency ABORTS the run: the failing phase and every phase after it
are skipped, and the reason plus its install instructions are printed at the
very bottom of the console, where the GUI log pane leaves them visible.  A
half-finished conversion is worse than one that never started -- the Sounds
phase without ffmpeg, for instance, silently ships a plugin whose every voice
line is missing, and nothing downstream reports it.

Phases whose dependency is genuinely optional are not listed here; the phase
degrades and says so itself.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

# convert.py exits with this when a phase is missing a dependency.  Distinct
# from 1 ("ran, but something failed") so the GUI can tell an aborted run from
# a failed one and stop the remaining steps instead of running them anyway.
RC_MISSING_DEP = 2

# The version the project is developed and validated against, and the version
# the committed navmesh .pyd is built for.
SUPPORTED_PYTHON = (3, 14)

# Stop the pipeline from Windows error dialogs / console flashes while probing.
_POPEN_FLAGS = {}
if sys.platform == 'win32':
    _POPEN_FLAGS['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class Missing:
    """One unmet dependency: what it is, why the phase needs it, how to get it."""

    __slots__ = ('name', 'purpose', 'install', 'detail')

    def __init__(self, name, purpose, install, detail=''):
        self.name = name
        self.purpose = purpose
        self.install = install
        self.detail = detail


# ---------------------------------------------------------------------------
#  Probes
# ---------------------------------------------------------------------------

def python_version_warning() -> 'str | None':
    """Warn when the interpreter is not the version this project is built for.

    Deliberately a WARNING, not a hard requirement: the pipeline does run on
    another 3.x, but two things stop being free.

      * The committed navmesh extension carries a CPython ABI tag in its
        filename, so it is not importable here and has to be rebuilt -- which
        needs a C++ toolchain most machines running a conversion do not have.
        That is the part people get stuck on, so it is stated up front rather
        than left to the ImportError at the start of the Import phase.
      * Nothing else is validated on other versions.

    Returns the warning text, or None when the interpreter matches.
    """
    have = sys.version_info[:2]
    if have == SUPPORTED_PYTHON:
        return None
    want = '.'.join(str(n) for n in SUPPORTED_PYTHON)
    got = '.'.join(str(n) for n in have)
    older = have < SUPPORTED_PYTHON
    suffix = sysconfig.get_config_var('EXT_SUFFIX') or '.pyd'
    if sys.platform == 'win32':
        toolchain_note = (
            '  That needs "Build Tools for Visual Studio" with the C++ workload\n'
            '  (a full Visual Studio install is not required).\n')
    else:
        toolchain_note = '  That needs g++ or clang++ on PATH.\n'
    return (
        f'Running on Python {got}; this project is built and validated against '
        f'Python {want}{"+" if older else ""}.\n'
        f'You may run into issues.\n'
        f'\n'
        f'  The prebuilt navmesh extension in native/dist/ is tagged for the\n'
        f'  interpreter that built it, so it will not load here and must be\n'
        f'  rebuilt before the Import phase can generate navmesh:\n'
        f'\n'
        f'      python native/build.py\n'
        f'\n'
        f'{toolchain_note}'
        f'  This interpreter expects: _navgrow_native{suffix}'
    )


def format_python_warning(text: str) -> str:
    """Render the version warning as a banner the GUI log can colour."""
    bar = '-' * 70
    return '\n'.join(['', f'WARNING: Python version', bar, text, bar, ''])


def _have_module(mod: str) -> bool:
    """True if `mod` is importable, without importing it."""
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _pip(mod: str, pkg: str, purpose: str, extra: str = '') -> 'Missing | None':
    if _have_module(mod):
        return None
    return Missing(pkg, purpose, f'pip install {pkg}', extra)


def _have_native(mod: str) -> bool:
    """True if the compiled navmesh extension for THIS interpreter is present.

    Mirrors ``tes5_import/navmesh/_native_loader.py``: the ABI tag is in the
    filename, so a .pyd built for another Python version does not count.
    """
    suffix = sysconfig.get_config_var('EXT_SUFFIX') or '.pyd'
    return (SCRIPT_DIR / 'native' / 'dist' / (mod + suffix)).is_file()


def _native_missing(mod: str, purpose: str) -> 'Missing | None':
    if _have_native(mod):
        return None
    dist = SCRIPT_DIR / 'native' / 'dist'
    have = []
    if dist.is_dir():
        have = sorted(f.name for f in dist.iterdir()
                      if f.name.startswith(mod) and f.suffix in ('.pyd', '.so'))
    suffix = sysconfig.get_config_var('EXT_SUFFIX') or '.pyd'
    return Missing(
        f'{mod}{suffix} (compiled navmesh extension)',
        purpose,
        'python native/build.py\n'
        '(needs "Build Tools for Visual Studio" with the C++ workload; a full\n'
        'Visual Studio install is not required)',
        f'expected {dist / (mod + suffix)}; present: '
        f'{", ".join(have) if have else "(none)"}',
    )


def _bundled_exe(rel: str, name: str, purpose: str,
                 extra: str = '') -> 'Missing | None':
    """An executable COMMITTED to this repo under external/.

    These are not user-installed dependencies -- if one is absent the checkout
    is incomplete (a partial clone, an antivirus quarantine, or a manual
    delete), so the instruction is to restore it from git, not to go and find
    it somewhere.

    Off Windows these bundled .exe files run under Wine (subprocess_flags.
    windows_cmd wraps every invocation) -- verified by hand against BSArch,
    hkxcmd, papyrus.exe, LODGenx64.exe and the mopp bridge under Wine 11.0, all
    running correctly with ordinary Linux paths as arguments. So the exe itself
    being present is not sufficient there; wine has to be on PATH too.
    """
    path = SCRIPT_DIR / rel
    if not path.is_file():
        install = (f'This binary ships with the repo. Restore it:\n'
                   f'git checkout -- {rel}\n'
                   f'(if that does not restore it, your antivirus may have '
                   f'quarantined it)')
        if extra:
            install += f'\n{extra}'
        return Missing(name, purpose, install, f'expected at {path}')
    if sys.platform != 'win32' and not shutil.which('wine'):
        return Missing(
            'wine',
            f'Running the bundled Windows tool {name} on this platform',
            'Install wine (e.g. `apt install wine` / `pacman -S wine` / '
            '`brew install --cask wine-stable`)',
            f'{name} is present at {path}, but off Windows it needs wine to run')
    return None


def _ffmpeg() -> 'Missing | None':
    """ffmpeg ships with the repo (external/ffmpeg/), so absence is a broken
    checkout rather than a missing user install -- same as the other bundled
    exes.  PATH is still searched as a fallback, so a user who deleted the
    bundled copy but has their own ffmpeg keeps working."""
    from asset_convert.audio_converter import find_ffmpeg
    if find_ffmpeg():
        return None
    return Missing(
        'ffmpeg',
        'Decoding Oblivion MP3/WAV audio before re-encoding',
        'This binary ships with the repo. Restore it:\n'
        'git checkout -- external/ffmpeg/ffmpeg.exe\n'
        '(if that does not restore it, your antivirus may have quarantined '
        'it)\n'
        'Or rebuild it: python tools/build_ffmpeg.py\n'
        'Or install any ffmpeg on your PATH: winget install Gyan.FFmpeg',
    )


def _xwmaencode() -> 'Missing | None':
    from asset_convert.audio_converter import find_xwmaencode
    if find_xwmaencode():
        return None
    return Missing(
        'xWMAEncode.exe',
        'Compressing converted voice lines to Skyrim xWMA',
        'Install the Microsoft DirectX SDK (June 2010) from\n'
        'https://www.microsoft.com/en-us/download/details.aspx?id=6812\n'
        'then copy Utilities\\bin\\x86\\xWMAEncode.exe to external/xwmaencode/\n'
        '(Microsoft does not allow it to be redistributed, so it is not bundled)',
    )


def _lipgenerator() -> 'Missing | None':
    from asset_convert.audio_converter import find_lipgenerator
    if find_lipgenerator():
        return None
    return Missing(
        'LipGenerator.exe',
        'Generating .lip lip-sync tracks for every voiced line',
        'Install the Skyrim SE Creation Kit (free on Steam) -- the tool lands in\n'
        '<Skyrim SE>\\Tools\\LipGen\\LipGenerator\\\n'
        'or copy it (with its FonixData.cdf) to external/lipgen/',
    )


def _load_config() -> dict:
    """Best-effort read of conversion_config.json (empty dict on any failure).

    winreg is Windows-only, so registry-based game-path detection returns
    nothing off Windows; conversion_config.json's tes4DataPath/tes5DataPath
    are the Linux/Mac equivalent. Reading it here lets these preflight checks
    match what convert.py's own find_game_path() would resolve, instead of
    reporting Skyrim SE as missing when the user has simply pointed the config
    at it.
    """
    from convert import load_config
    try:
        return load_config()
    except Exception:
        return {}


def _papyrus_headers() -> 'Missing | None':
    """Skyrim's own .psc headers, which every converted script compiles against."""
    from convert import find_game_path
    data = find_game_path('skyrimse', _load_config())
    if data:
        src = Path(data) / 'Source' / 'Scripts'
        if src.is_dir() and (src / 'Debug.psc').is_file():
            return None
        # Older CK layouts put them in Data\Scripts\Source.
        alt = Path(data) / 'Scripts' / 'Source'
        if alt.is_dir() and (alt / 'Debug.psc').is_file():
            return None
    return Missing(
        'Skyrim Papyrus source headers (Debug.psc and friends)',
        'Compiling the converted scripts -- they define every native type',
        'Install the Skyrim SE Creation Kit (free on Steam), which unpacks them to\n'
        '<Skyrim SE>\\Data\\Source\\Scripts\\',
        f'looked under {data or "(no Skyrim SE install found in the registry)"}',
    )


def _creation_kit_installed() -> bool:
    from convert import find_game_path
    return bool(find_game_path('skyrimse', _load_config()))


# ---------------------------------------------------------------------------
#  Phase requirements
# ---------------------------------------------------------------------------
#
# Keyed by the `do_*` phase name convert.py uses. Each value is a list of
# zero-argument callables returning a Missing (or None when satisfied); they
# run lazily so a phase that is not selected costs nothing.

_REQUIREMENTS = {
    'export': [],

    'extract': [
        lambda: _pip('lz4', 'lz4', 'Reading LZ4-compressed Skyrim SE BSAs when '
                                   'fetching vanilla assets'),
    ],

    'meshes': [
        lambda: _pip('pyffi', 'PyFFI', 'Reading and writing NIF meshes'),
        lambda: _pip('numpy', 'numpy', 'Mesh, skin and collision math'),
        lambda: _pip('scipy', 'scipy', 'Collision convex hulls and skin retargeting'),
        lambda: _pip('lz4', 'lz4', 'Reading vanilla Skyrim SE BSAs for reference assets'),
        lambda: _bundled_exe(
            'external/mopp_bridge/dovah_hkp_mesh_mopp_bridge.exe',
            'dovah_hkp_mesh_mopp_bridge.exe',
            'Generating Havok MOPP collision data'),
    ],

    'speedtrees': [
        lambda: _pip('pyffi', 'PyFFI', 'Writing the generated flora NIFs'),
        lambda: _pip('numpy', 'numpy', 'Branch and leaf geometry generation'),
        lambda: _pip('scipy', 'scipy', 'Tree collision hull generation'),
    ],

    'creatures': [
        lambda: _pip('pyffi', 'PyFFI', 'Reading creature meshes and skeletons'),
        lambda: _pip('numpy', 'numpy', 'Skeleton, ragdoll and animation math'),
        lambda: _pip('scipy', 'scipy', 'Ragdoll body fitting'),
        lambda: _bundled_exe(
            'external/hkxcmd/hkxcmd.exe', 'hkxcmd.exe',
            'Compiling creature skeleton, behavior and animation .hkx files'),
    ],

    'import': [
        lambda: _pip('numpy', 'numpy', 'Navmesh geometry'),
        lambda: _pip('scipy', 'scipy', 'Navmesh triangulation'),
        lambda: _pip('shapely', 'shapely', 'Navmesh corridor boolean union'),
        lambda: _pip('mapbox_earcut', 'mapbox_earcut',
                     'Navmesh triangulation fallback when Delaunay fails'),
        lambda: _pip('pyffi', 'PyFFI', 'Reading converted meshes for collision '
                                       'and OBND bounds'),
        lambda: _native_missing('_navgrow_native',
                                'Navmesh corridor growth (required -- there is no '
                                'Python fallback, by design)'),
    ],

    'sounds': [
        _ffmpeg,
        _xwmaencode,
        _lipgenerator,
    ],

    'scripts': [
        lambda: _bundled_exe(
            'external/papyrus-compiler/papyrus.exe', 'papyrus.exe',
            'Compiling the converted .psc scripts to .pex'),
        _papyrus_headers,
    ],

    'lod': [
        # Import name is PIL, pip name is Pillow -- probe the former, tell the
        # user the latter.
        lambda: _pip('PIL', 'Pillow', 'Compositing terrain-LOD textures and the '
                                      'object-LOD normal atlas -- terrain LOD '
                                      'produces no tiles at all without it'),
        lambda: _pip('pyffi', 'PyFFI', 'Building the _far.nif LOD meshes'),
        lambda: _pip('numpy', 'numpy', 'Terrain mesh generation'),
        lambda: _bundled_exe(
            'external/lodgen/LODGenx64.exe', 'LODGenx64.exe',
            'Baking object LOD meshes'),
    ],

    'skyrim_patch': [
        lambda: _pip('pyffi', 'PyFFI', 'Editing the body meshes'),
        lambda: _pip('numpy', 'numpy', 'Mesh math'),
    ],

    'pack_bsa': [
        lambda: _bundled_exe(
            'external/bsarch/BSArch.exe', 'BSArch.exe',
            'Packing the converted assets into Skyrim BSA archives',
            'Or set "bsarchPath" in conversion_config.json to a copy elsewhere.'),
    ],

    'pack_zip': [],
}

# Human-readable phase names, matching the GUI's step labels.
PHASE_LABELS = {
    'export':       'Export',
    'extract':      'Extract',
    'meshes':       'Meshes',
    'speedtrees':   'SpeedTrees',
    'creatures':    'Creatures',
    'import':       'Import',
    'sounds':       'Sounds',
    'scripts':      'Scripts',
    'lod':          'LOD',
    'skyrim_patch': 'Patch Skyrim',
    'pack_bsa':     'Pack BSAs',
    'pack_zip':     'Pack Mod Zip',
}


# ---------------------------------------------------------------------------
#  Checking and reporting
# ---------------------------------------------------------------------------

def check_phase(phase: str) -> list:
    """Return the list of unmet dependencies for one phase (empty if fine)."""
    out = []
    for probe in _REQUIREMENTS.get(phase, ()):
        try:
            m = probe()
        except Exception as exc:              # a probe must never kill the run
            print(f"  WARNING: dependency check for {phase} raised {exc!r}; "
                  f"assuming it is satisfied")
            continue
        if m is not None:
            out.append(m)
    return out


def check_phases(phases: list) -> 'tuple[str, list] | None':
    """Check `phases` in run order; return (phase, missing) for the FIRST that fails.

    Returns None when every selected phase has what it needs.  Checking stops at
    the first failure because that phase and everything after it will be skipped
    anyway -- reporting later phases' gaps too would bury the one that matters.
    """
    for phase in phases:
        missing = check_phase(phase)
        if missing:
            return phase, missing
    return None


def format_report(phase: str, missing: list, skipped: list) -> str:
    """Render the abort banner printed at the bottom of the console."""
    label = PHASE_LABELS.get(phase, phase)
    bar = '=' * 70
    # The header comes FIRST, before any rule: the GUI log colours the banner by
    # spotting that line and staying red until the closing rule, so a leading
    # rule would be styled as an ordinary section heading and break the block.
    lines = [
        '',
        f'MISSING DEPENDENCY -- pipeline stopped before the {label} phase',
        bar,
        '',
        f'  The {label} phase cannot run without the following:',
        '',
    ]
    for m in missing:
        lines.append(f'    * {m.name}')
        lines.append(f'        needed for: {m.purpose}')
        if m.detail:
            lines.append(f'        {m.detail}')
        # Continuation lines line up under the text after "install it: ",
        # not under the label, so multi-line instructions read as one block.
        pad = ' ' * len('        install it: ')
        install = m.install.replace('\n', '\n' + pad)
        lines.append(f'        install it: {install}')
        lines.append('')

    if skipped:
        names = ', '.join(PHASE_LABELS.get(p, p) for p in skipped)
        lines.append(f'  Also skipped (they run after {label}): {names}')
        lines.append('')
    lines.append('  Install what is listed above, then run the pipeline again.')
    lines.append(bar)
    return '\n'.join(lines)


def main(argv=None) -> int:
    """`python preflight.py [phase ...]` -- check phases without running them."""
    import argparse
    p = argparse.ArgumentParser(
        description='Check pipeline phase dependencies without running anything.')
    p.add_argument('phases', nargs='*', default=None,
                   help=f'Phases to check (default: all). '
                        f'One of: {", ".join(_REQUIREMENTS)}')
    a = p.parse_args(argv)

    phases = a.phases or list(_REQUIREMENTS)
    unknown = [x for x in phases if x not in _REQUIREMENTS]
    if unknown:
        p.error(f'unknown phase(s): {", ".join(unknown)}')

    pywarn = python_version_warning()
    if pywarn:
        print(format_python_warning(pywarn))

    bad = 0
    for phase in phases:
        missing = check_phase(phase)
        label = PHASE_LABELS.get(phase, phase)
        if not missing:
            print(f'  OK      {label}')
            continue
        bad += 1
        print(f'  MISSING {label}')
        for m in missing:
            print(f'            * {m.name} -- {m.purpose}')
            print(f'              install: {m.install.splitlines()[0]}')
    print()
    print('All checked phases have their dependencies.' if not bad
          else f'{bad} phase(s) are missing dependencies.')
    return 0 if not bad else 1


if __name__ == '__main__':
    sys.exit(main())

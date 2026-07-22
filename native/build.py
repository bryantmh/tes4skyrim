"""Build the native navmesh extension.

    python native/build.py            # build in place
    python native/build.py --force    # rebuild even if up to date

The compiled module lands next to the sources as
`tes5_import/navmesh/_navmesh_native*.pyd`, which spanmesh imports opportunis-
tically -- if it is missing, the pure-Python path runs instead, so the pipeline
never hard-depends on a compiler being installed.

MSVC is located through vswhere (Build Tools are enough; a full Visual Studio
install is not required), so this works on a machine that has never opened an
IDE.  Nothing here is invoked by the conversion pipeline itself; the .pyd is a
build artifact you produce once.
"""

import argparse
import os
import subprocess
import sys
import sysconfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'native', 'src', 'decimate.cpp')

# The built .pyd is COMMITTED (see native/dist/README.md) so the pipeline runs
# on a machine without a compiler.  Only the .pyd is needed at runtime; the .lib
# and .exp MSVC emits alongside it are link-time artifacts for callers that
# link against the DLL, which nothing does.  They stay in native/build/.
OUT_DIR = os.path.join(ROOT, 'native', 'dist')

_VSWHERE = (r'C:\Program Files (x86)\Microsoft Visual Studio\Installer'
            r'\vswhere.exe')


def find_vcvars():
    """Path to vcvars64.bat, or None."""
    if not os.path.exists(_VSWHERE):
        return None
    try:
        out = subprocess.check_output(
            [_VSWHERE, '-all', '-products', '*', '-property',
             'installationPath'],
            text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        cand = os.path.join(line, 'VC', 'Auxiliary', 'Build', 'vcvars64.bat')
        if os.path.exists(cand):
            return cand
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()

    import numpy
    ext_suffix = sysconfig.get_config_var('EXT_SUFFIX') or '.pyd'
    os.makedirs(OUT_DIR, exist_ok=True)
    target = os.path.join(OUT_DIR, '_navmesh_native' + ext_suffix)

    if (not a.force and os.path.exists(target)
            and os.path.getmtime(target) > os.path.getmtime(SRC)):
        print('up to date:', target)
        return 0

    vcvars = find_vcvars()
    if not vcvars:
        print('ERROR: MSVC not found (looked via vswhere).\n'
              'Install "Build Tools for Visual Studio" with the C++ workload.',
              file=sys.stderr)
        return 1

    py_inc = sysconfig.get_paths()['include']
    np_inc = numpy.get_include()
    py_lib = os.path.join(sys.base_prefix, 'libs')
    obj_dir = os.path.join(ROOT, 'native', 'build')
    os.makedirs(obj_dir, exist_ok=True)

    # /O2 optimise, /GL whole-program, /fp:precise so the compiler may NOT
    # reassociate float ops -- the geometry predicates compare against exact
    # thresholds and reassociation would change which branches are taken.
    # Written to a .bat rather than passed to `cmd /c`: the command line mixes
    # quoted paths containing spaces with `&&`, and cmd's own quote handling
    # mangles that combination.  A script file has no such ambiguity.
    bat = os.path.join(obj_dir, '_build.bat')
    with open(bat, 'w') as fh:
        fh.write('@echo off\n')
        # vcvars64.bat shells out to vswhere.exe by BARE NAME, so the VS
        # Installer directory has to be on PATH or it fails with "not
        # recognized" and leaves the environment uninitialised (cl then reports
        # a bogus "missing source filename").
        fh.write(f'set "PATH=%PATH%;{os.path.dirname(_VSWHERE)}"\n')
        fh.write(f'call "{vcvars}" >nul\n')
        fh.write('if errorlevel 1 exit /b 1\n')
        # /Fo needs a TRAILING BACKSLASH to mean "a directory", but a backslash
        # immediately before the closing quote escapes that quote and cl then
        # eats the rest of the command line ("missing source filename").
        # Doubling it keeps the separator and terminates the argument.
        # /IMPLIB keeps the .lib/.exp out of dist/ -- only the .pyd is a runtime
        # artifact and only it is committed.
        implib = os.path.join(obj_dir, '_navmesh_native.lib')
        fh.write(
            f'cl /nologo /LD /O2 /GL /EHsc /std:c++17 /fp:precise /W3 '
            f'/I"{py_inc}" /I"{np_inc}" '
            f'/Fo"{obj_dir}\\\\" /Fe:"{target}" "{SRC}" '
            f'/link /LIBPATH:"{py_lib}" /IMPLIB:"{implib}" '
            f'/OPT:REF /OPT:ICF\n')
    print('building', target)
    r = subprocess.run([bat], cwd=ROOT, shell=True)
    if r.returncode != 0:
        print('BUILD FAILED', file=sys.stderr)
        return r.returncode
    print('ok:', target)
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""Build the native navmesh extension.

    python native/build.py            # build in place
    python native/build.py --force    # rebuild even if up to date

The compiled module lands in `native/dist/` and is loaded by path (see
`tes5_import/navmesh/_native_loader.py`).  It is REQUIRED at runtime -- falling
back to Python would make output depend on whether a build artifact happened to
be present, which breaks byte-reproducibility -- so the .pyd/.so is committed.

On Windows, MSVC is located through vswhere (Build Tools are enough; a full
Visual Studio install is not required), so this works on a machine that has
never opened an IDE. Off Windows, g++/clang++ from PATH build a .so instead --
grow.cpp is portable C++17 (only Python.h/numpy's C API), so it compiles
unmodified; verified by hand with g++ 14 producing a working
_navgrow_native.cpython-3xx-x86_64-linux-gnu.so that imports and exposes
grow_strips/levels_at. Nothing here is invoked by the conversion pipeline
itself; the .pyd/.so is a build artifact you produce once.
"""

import argparse
import os
import shutil
import subprocess
import sys
import sysconfig

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# module name -> source file.  Each builds to its own .pyd; they share no code,
# so a failure in one does not block the other.
MODULES = {
    '_navgrow_native': os.path.join(ROOT, 'native', 'src', 'grow.cpp'),
}

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


def find_unix_compiler():
    """Path to a C++ compiler for the non-Windows build, or None."""
    for name in ('g++', 'clang++', 'c++'):
        p = shutil.which(name)
        if p:
            return p
    return None


def _build_one_msvc(name, src, toolchain, py_inc, np_inc, py_lib, target, obj_dir):
    vcvars = toolchain
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
        implib = os.path.join(obj_dir, name + '.lib')
        fh.write(
            f'cl /nologo /LD /O2 /GL /EHsc /std:c++17 /fp:precise /W3 '
            f'/I"{py_inc}" /I"{np_inc}" '
            f'/Fo"{obj_dir}\\\\" /Fe:"{target}" "{src}" '
            f'/link /LIBPATH:"{py_lib}" /IMPLIB:"{implib}" '
            f'/OPT:REF /OPT:ICF\n')
    print('building', target)
    r = subprocess.run([bat], cwd=ROOT, shell=True)
    return r.returncode


def _build_one_unix(name, src, toolchain, py_inc, np_inc, target, obj_dir):
    compiler = toolchain
    # -fPIC -shared for a loadable extension module. -O2 (no -ffast-math, so
    # this stays strict-IEEE like MSVC's /fp:precise above -- same reasoning:
    # the geometry predicates compare against exact thresholds). Extension
    # modules resolve their Python/numpy C-API symbols against the interpreter
    # that dlopen()s them, so -- unlike the MSVC path -- there is no
    # link-time Python library to point at.
    cmd = [
        compiler, '-shared', '-fPIC', '-O2', '-std=c++17', '-Wall',
        '-I', py_inc, '-I', np_inc,
        '-o', target, src,
    ]
    print('building', target)
    print('  ' + ' '.join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    return r.returncode


def build_one(name, src, toolchain, py_inc, np_inc, py_lib, ext_suffix, force):
    """Compile one module; returns a shell return code (0 = ok)."""
    target = os.path.join(OUT_DIR, name + ext_suffix)
    if (not force and os.path.exists(target)
            and os.path.getmtime(target) > os.path.getmtime(src)):
        print('up to date:', target)
        return 0

    # Per-module object dir: two modules compiled into ONE directory overwrite
    # each other's .obj when a source basename repeats, and /GL then links stale
    # code into the wrong .pyd.
    obj_dir = os.path.join(ROOT, 'native', 'build', name)
    os.makedirs(obj_dir, exist_ok=True)

    if sys.platform == 'win32':
        rc = _build_one_msvc(name, src, toolchain, py_inc, np_inc, py_lib,
                             target, obj_dir)
    else:
        rc = _build_one_unix(name, src, toolchain, py_inc, np_inc,
                             target, obj_dir)
    if rc != 0:
        print('BUILD FAILED:', name, file=sys.stderr)
        return rc
    print('ok:', target)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--module', help='build only this module')
    a = ap.parse_args()

    import numpy
    ext_suffix = sysconfig.get_config_var('EXT_SUFFIX') or '.pyd'
    os.makedirs(OUT_DIR, exist_ok=True)

    py_lib = None
    if sys.platform == 'win32':
        toolchain = find_vcvars()
        if not toolchain:
            print('ERROR: MSVC not found (looked via vswhere).\n'
                  'Install "Build Tools for Visual Studio" with the C++ workload.',
                  file=sys.stderr)
            return 1
        py_lib = os.path.join(sys.base_prefix, 'libs')
    else:
        toolchain = find_unix_compiler()
        if not toolchain:
            print('ERROR: no C++ compiler found (looked for g++, clang++, c++ '
                  'on PATH).\n'
                  'Install one (e.g. `apt install g++` / `pacman -S gcc` / '
                  '`xcode-select --install`).',
                  file=sys.stderr)
            return 1

    py_inc = sysconfig.get_paths()['include']
    np_inc = numpy.get_include()

    todo = MODULES if not a.module else {a.module: MODULES[a.module]}
    for name, src in todo.items():
        rc = build_one(name, src, toolchain, py_inc, np_inc, py_lib,
                       ext_suffix, a.force)
        if rc != 0:
            return rc
    return 0


if __name__ == '__main__':
    sys.exit(main())

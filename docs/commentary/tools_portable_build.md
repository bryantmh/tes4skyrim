# tools/release/build_portable.py - the portable Windows package

**Code:** `tools/release/build_portable.py`, `requirements.txt`

## Contents

- [Design](#design)
- [The ABI decides the Python version](#abi)
- [Why the embeddable distribution is not used](#why-not-embeddable)
- [The version stamp](#version-stamp)

The package lets a user run the converter without installing Python, running
pip, or typing commands: unzip, double-click `TES Auto-Convert.cmd`.

## <a id="design"></a>Design

```
TESAutoConvert/
    TES Auto-Convert.cmd     <- start "" python\pythonw.exe gui.py
    python/                  <- a CPython install, packages from requirements.txt
    gui.py, convert.py, ...  <- every tracked file except tests/ and dev folders
```

- **The interpreter is a copy of a full CPython install**, by default the one
  running the build (`sys.base_prefix`), minus `Doc`, `include`, `libs`,
  `Scripts`, `Lib/test`, `Lib/idlelib` and its whole `site-packages`.
- **Packages come only from `requirements.txt`,** installed into the copy with
  `PYTHONNOUSERSITE=1` and `PYTHONPATH` cleared, so the build machine's own
  packages can never leak in. pip stays in the package, for future in-app
  dependency repair.
- **App files are `git ls-files`,** minus `tests/`, `.github/`, `.claude/` and
  `.vscode/`. Untracked working data (`export/`, `output/`, `references/`,
  `conversion_config.json`) is therefore never shipped.
- **`VERSION` is stamped** with the release tag HEAD sits on; see
  [the version stamp](#version-stamp).
- **The launcher is a `.cmd`** that `start`s `pythonw.exe gui.py` and exits, so
  its console closes at once. `gui.py` only relaunches itself under pythonw when
  started by `python.exe`, so under pythonw it opens the window directly.
- **Upgrading** is unzipping over the old folder, as the README already says:
  `export/`, `output/` and `conversion_config.json` are not in the archive.

Everything the pipeline spawns uses `sys.executable`; nothing looks for Python
on PATH, so the bundled interpreter runs every stage and worker.

## <a id="abi"></a>The ABI decides the Python version

`native/dist/` commits compiled extensions (`_navgrow_native`,
`_nifgeom_native`) whose file names carry the ABI tag, for example
`cp314-win_amd64`. A `.pyd` imports only into a matching interpreter, so the
build reads the tag from those files and refuses an interpreter whose
`EXT_SUFFIX` differs. Moving to a new Python means rebuilding the extensions
first (`python native/build.py`); the package then follows automatically.

## <a id="why-not-embeddable"></a>Why the embeddable distribution is not used

python.org's embeddable zip would be the obvious base, but it ships without
tkinter and Tcl/Tk, and the converter's GUI is tkinter. Grafting them in from
another install is exactly as dependent on a local CPython as copying one, and
more fragile, so the build copies a full install and strips it instead.

## <a id="version-stamp"></a>The version stamp

In a checkout, `VERSION` holds git's unexpanded `$Format:...$` placeholder, and
the package has no `.git` to fall back on. Without a stamp the app would call
itself `0.0-dev` and its Upgrade check would lose track of what changed.
`version.py` accepts only a bare release tag from `VERSION`, so the stamp must
be exactly that tag.

The build asks git (`git describe --tags --exact-match HEAD`) rather than
`version.current_version()`. `version.py` reads `.git` without spawning git,
and it can only confirm HEAD is ON a release by opening the annotated tag
object, which it reads only when stored loose. A fresh clone, such as a CI
checkout, packs its objects, so there `current_version()` answers
`<tag>+g<sha>` even for a tagged commit (measured: `0.665+g4667310` for the
commit tagged `0.665`). Stamped into `VERSION`, that would make the package
report `0.0-dev`. The builder is not the GUI, so spawning git costs nothing.

When HEAD is not on a tag, the build falls back to `current_version()`,
stamps its describe form, and prints a note: the package then reports
`0.0-dev`, exactly like GitHub's zip of an untagged commit. Release packages
must be built from a tagged commit.

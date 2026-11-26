"""How one TESRACT process starts another Python program.

From source it is `python -u <script>`. Inside TESRACT.exe there is no script file and no python.exe:
the exe itself is started again with `--tesract-run <module>`, and `run_from_argv` (called first by the
exe's entry point) imports that module and exits with its `main()`: Nuitka's compiled modules cannot
be re-run as `__main__` (`runpy` needs their source), so every module started this way keeps its work
in `main()`. Every place that starts a script goes through `python_cmd`, so the two cannot drift.
See: docs/commentary/core_gui.md#folder-build
"""

import importlib
import sys
from pathlib import Path

#: The argument that makes TESRACT.exe run a module instead of opening the window.
RUN_FLAG = "--tesract-run"
#: The code root: scripts are named by their path under it.
CODE_ROOT = Path(__file__).resolve().parent.parent


def frozen() -> bool:
    """True inside TESRACT.exe (compiled by Nuitka)."""
    return "__compiled__" in globals()


def module_name(script) -> str:
    """`tools/release/pack_lod.py` -> `tools.release.pack_lod`."""
    rel = Path(script).resolve().relative_to(CODE_ROOT).with_suffix("")
    return ".".join(rel.parts)


def python_cmd(script, *args) -> list:
    """The argv that runs `script` (a .py path under the code root) with `args`, output unbuffered."""
    if frozen():
        return [sys.executable, RUN_FLAG, module_name(script), *map(str, args)]
    return [sys.executable, "-u", str(script), *map(str, args)]


def run_from_argv(argv) -> bool:
    """Run the module `argv` names after RUN_FLAG and exit with its `main()`; False when there is no RUN_FLAG."""
    if len(argv) < 3 or argv[1] != RUN_FLAG:
        return False
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(line_buffering=True)
    sys.argv = [str(CODE_ROOT.joinpath(*argv[2].split("."))) + ".py", *argv[3:]]
    sys.exit(importlib.import_module(argv[2]).main())

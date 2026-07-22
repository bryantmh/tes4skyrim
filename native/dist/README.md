# Prebuilt navmesh extension

`_navmesh_native.<abi>.pyd` is the compiled mesh decimator
(`../src/decimate.cpp`). It is **committed on purpose**: `tes5_import.navmesh.
spanmesh` requires it, and most machines running the conversion have no C++
compiler.

Only the `.pyd` belongs here. The `.lib` / `.exp` MSVC emits are link-time
artifacts for callers that link against the DLL — nothing does — so they are
written to `../build/` and gitignored.

## The ABI tag matters

The filename carries a Python ABI tag (`cp314-win_amd64` = CPython 3.14,
64-bit Windows). A `.pyd` is only importable by a matching interpreter, so the
committed binary works for whoever runs the same Python version and platform.
On anything else the loader raises with the expected filename and what it found
instead — rebuild with:

    python native/build.py

That needs "Build Tools for Visual Studio" with the C++ workload (a full Visual
Studio install is not required); the build script locates MSVC through
`vswhere`.

## Why it is not optional

The extension is imported unconditionally. A silent fallback to a Python
implementation would make navmesh output depend on whether a build artifact
happened to be present, and the pipeline's output must be byte-reproducible.

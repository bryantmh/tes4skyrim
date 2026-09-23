# Prebuilt native artifacts

**Committed on purpose** — the conversion needs them and most machines running
it have no C++ compiler:

| Artifact | Kind | Source | Used by |
|---|---|---|---|
| `_navgrow_native.<abi>.pyd` | Python extension (64-bit) | `../src/navgrow/grow.cpp` | `navmesh.corridor_grow` (Phase-2 width march) and `navmesh.corridor_union` (per-vertex surface levels) |
| `spt_engine_dump.exe` | standalone program (**32-bit**) | `../src/spt_engine/` | `asset_convert.speedtree.spt_engine_geom` — the SpeedTree engine-branch path (`--engine-branches`) |

Build everything with:

    python native/build.py --programs      # .pyd + the standalone programs
    python native/build.py --only-programs # just the .exe

(`_navmesh_native` / `decimate.cpp` served the old `spanmesh` generator and was
deleted with it — see
[performance_notes.md](../../docs/notes/performance.md#L588).)

`grow.cpp` exists because those two kernels dominated generation: a single
dense interior cell (Wendir02, 938 edges) spent ~150s in the width march alone
(~890k wall probes at ~170us each) and 29.3s of a 31.9s build in the level
lookup. Both are batched so the Python/C boundary is crossed once per CELL
rather than once per probe, which took that cell to ~2.6s.

Only runtime artifacts belong here. The `.lib` / `.exp` MSVC emits are
link-time artifacts for callers that link against the DLL — nothing does — so
they are written to `../build/` and gitignored, along with every `.obj`.

## `spt_engine_dump.exe` is 32-bit, and must be

It maps the user's own `Oblivion.exe` and calls the SpeedTreeRT 4.x code
statically linked into it. That image is i386 with its relocations **stripped**,
so it can only load at its fixed base `0x400000` — which only a 32-bit host can
address. By the time any host code runs, Windows has already put its own
startup data in that range, so the harness claims it through its own image:
it is linked `/BASE:0x200000 /FIXED /DYNAMICBASE:NO /SAFESEH:NO` with an empty
`.oblimg` section (no file bytes) spanning `0x400000–0xD00000`, and copies
Oblivion.exe into it.

### Antivirus

Defender quarantined the earlier build as `Trojan:Win32/Wacatac.B!ml` (a
machine-learning verdict, not a signature). That build relaunched itself
suspended and reserved `0x400000` in the child with `VirtualAllocEx` — the
process-hollowing pattern — and mapped the image read-write-execute. Both are
gone: the range comes from `.oblimg`, and each mapped section gets its own
header's protection. The exe also carries a version resource and an
`asInvoker` manifest.

The verdict was attached to that exact file: a fresh rebuild of the same old
source scanned clean with `MpCmdRun -Scan -ScanType 3 -File`, so a clean local
scan of a new build proves nothing on its own. If a build is flagged again,
submit it as a false positive at microsoft.com/wdsi/filesubmission.

**No Bethesda code is redistributed.** The harness contains none of
Oblivion.exe; it maps the copy the user already owns, at runtime, as data (the
game is never launched). Verified by scanning 4,000 random 32-byte windows of
Oblivion.exe against the built binary: the only 3 hits are generic MSVC CRT
character tables (ASCII, case-conversion, a codepage ramp), and the only
"Oblivion"/"SpeedTree" strings present are the harness's own usage text.

Unlike the `.pyd`, this one is **optional at runtime**: if it or a configured
Oblivion install is missing, `spt_engine_geom` raises `EngineUnavailable` and
the caller falls back to the pure-Python generator, which needs no executable.

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

## Why the `.pyd` is not optional

The navmesh extension is imported unconditionally. A silent fallback to a Python
implementation would make navmesh output depend on whether a build artifact
happened to be present, and the pipeline's output must be byte-reproducible.

The native kernels are verified against the Python originals they replaced —
`python tools/navmesh/grow_verify.py` marches both implementations over
synthetic geometry chosen to exercise every stop rule (wall + bisect, floor
edge, neighbour cap, hard cap) and reports the worst disagreement.

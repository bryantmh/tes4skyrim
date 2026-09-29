# tools/release/verify_build.py - the release gate

**Code:** `tools/release/verify_build.py`, `tools/release/gate_terrain.py`

## Contents

- [What the gate is for](#purpose)
- [Denominators, and why a zero refuses](#denominators)
- [The two modes](#modes)
- [The checks](#checks)
- [The terrain checks](#terrain-checks)
- [Reading the result](#result)

## What the gate is for
<a id="purpose"></a>

A converted build can look finished while a whole class of output is silently
missing: grey terrain LOD, generic spell art, zero race skin tones, a LOD bake
that died half way. Each of those was found by a person looking, days later.
The gate turns every one into a number measured against something the
converter did not produce itself (the TES4 source, vanilla archives, the
bytes on disk), run once after each rebuild and again after each deploy.

It reads the rebuild's own log (`--log`, the `.out` the rebuild script tees),
never a fixed path, and splits it at the script's `=== <time> <step>` stamps:
the first stamp is the run start, and the LOD checks read only the LAST
`create_lod` step, because a default convert also bakes LOD (four times, each
before its dependents exist) and those earlier bakes are not what ships.

## Denominators, and why a zero refuses
<a id="denominators"></a>

Every check prints what it inspected: files per tree, meshes read, plugin
lines, quadrants, cells. A check whose denominator is 0 REFUSES: it looked at
nothing, so its silence is not evidence (a log that was never written, a
glob that matched nothing, a renamed log line). REFUSE fails the gate like
FAIL does. A check that raises is a FAIL naming the exception.

## The two modes
<a id="modes"></a>

- `--pre-deploy` (after the rebuild, before anything is copied): G1-G9, G11,
  G12.
- `--post-deploy` (after the zips are unpacked into the mod folders): G1 over
  the deploy folders as well, and G10. `--deploy ZIP=MODDIR` names each pair.

Each run writes `gate_<stamp>.json` beside the log (or `--json`), with the
verdict and every check. A redeploy script must read the pre-deploy JSON and
require `"verdict": "PASS"` before it copies anything. Exit status is 0 only
on PASS.

## The checks
<a id="checks"></a>

| id | what | passes when |
|---|---|---|
| G1 | case census of every output tree, zip and deploy folder (`case_paths.census`) | no two files share one lowercase archive path; folder twins only warn |
| G2 | texture paths inside the packed mesh BSAs and the LOD mod's tiles vs every table (our BSAs, the LOD mod's loose textures, vanilla Skyrim BSAs) | all found except `KNOWN_ABSENT`, and the control `anmiddlehouselod01.dds` is referenced and found |
| G3 | `Magic effect phase meshes` line; ARTO/EXPL records in the converted Oblivion.esm | every model converted, both record types present |
| G4 | `Race skin tones: N races resolved`, one line per plugin | every N > 0 |
| G5 | each plugin's `door_panel_axis_cache.json` | exists; FR's is written by this run and holds at least 37 doors |
| G6 | ATXT-only land quadrants (see below) | all keep an alpha layer |
| G7 | terrain-LOD colour (see below) | MAE within bound, control worse, not grey |
| G8 | tree-card tiles vs tiles carrying NiAlphaProperty | informational: vanilla carries none |
| G9 | each worldspace's LODGen input: rows on disk vs the printed `LODGen input` count vs the `Object-LOD selection` line | all equal; a mismatch after a NullReference retry (which rewrites the file) warns; a file older than the run fails |
| G10 | CRC-32 of every zip member vs the deployed file | all equal |
| G11 | final `create_lod` step: `NullReferenceException`, empty bakes; `.bto` per worldspace | none; at least `--min-bto` (TES4Tamriel 997) |
| G12 | `[plugin] Compilation: ok/total succeeded, N failed` | every plugin present, N = 0 |

G2 reads mesh bytes and scans for `.dds` paths with the LOD fill's own
pattern (`lod_gen.TEXTURE_PATH_RE`); a mesh's sized strings are preceded by
NUL bytes, so a match cannot swallow its length prefix.

## The terrain checks
<a id="terrain-checks"></a>

Both are ported from the LOD probe's adversary scripts and both compare
against the TES4 source.

- **G6** walks the SOURCE Oblivion.esm (`--source-esm`, else the home in
  `export/sources.json`) for Tamriel quadrants painted by an ATXT layer (any
  VTXT opacity above 0.01) with no BTXT, and counts how many of them carry an
  alpha layer in the converted TES4Tamriel. The importer used to drop alpha
  on such quadrants; the expected count for Tamriel is 1,463 of 1,463.
- **G7** compares per-cell mean colour of our level-32 terrain diffuse with
  Oblivion's own baked tile (`landscapelod/generated/60.<x>.<y>.32.dds`) over
  cells that have LAND, are fully painted and are dry. Oblivion's image is
  stored south-up, so it is flipped first; the unflipped image is the control
  and must score worse, which proves the orientation rather than assuming it.
  Passes at a cell-weighted MAE of 18 or less with a mean channel spread of at
  least 2 (grey terrain has none).

## Reading the result
<a id="result"></a>

Statuses: PASS, WARN (does not fail), INFO (never fails), FAIL, REFUSE.
The JSON repeats each line with its data (per-tree file counts, missing
textures, per-worldspace row counts), so a failure names what to look at.

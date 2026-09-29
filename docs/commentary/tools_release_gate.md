# tools/release/verify_build.py - the release gate

**Code:** `tools/release/verify_build.py`, `tools/release/gate_terrain.py`

## Contents

- [What the gate is for](#purpose)
- [The log stamp contract](#stamps)
- [What the gate reads, and from where](#sources)
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

## The log stamp contract
<a id="stamps"></a>

The gate reads any log that marks each step with one line of exactly this
form, at the start of the line:

    === YYYY-MM-DD HH:MM:SS <step>

- The time is local wall-clock time, the same clock the files' mtimes use.
- `<step>` is free text; the gate reads `convert <plugin>` (the plugins, in
  load order), `create_lod` (the bake whose output ships: the LAST one) and
  nothing else by name.
- Everything after a stamp up to the next stamp belongs to that step, so a
  step's window is its stamp to the next stamp (or the end of the log).
- The first stamp is the run start. `--since 'YYYY-MM-DD HH:MM:SS'` drops
  every section stamped earlier, for a log that several runs appended to.

A rebuild script that tees `convert.py` output under such stamps is all the
gate needs; nothing about the machine or the log's location is assumed.

## What the gate reads, and from where
<a id="sources"></a>

- **Plugins**: the `convert <plugin>` stamps (or `--plugins`).
- **Worldspaces**: the `LODGen input: <file> (<n> references)` lines of the
  final `create_lod` step; the worldspace is the file name after `LODGen `.
  Source-side names go through `core.worldspace_names` (the converted
  Tamriel is `TES4Tamriel`), with the log's plugins as the active chain.
- **Output root**: `--output`, else the install config's `outputDir`
  (`output_layout.configured_output`), which is what `convert.py` writes to.
  **Export root**: `--export`, else the install's `export/`.
- **Per-plugin paths** come from `output_layout` (`plugin_esm`,
  `record_dir`, `assets_for`), so an imported mod's grouped folders resolve
  the same way the converter wrote them.

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
| G2 | texture paths inside the packed mesh BSAs and the LOD mod's tiles vs every table (our BSAs, the LOD mod's loose textures, vanilla Skyrim BSAs) | all found except full keys listed in `--allow-missing FILE`, and the control (the texture the most meshes name) is found |
| G3 | `Magic effect phase meshes` line; ARTO/EXPL records in the converted Oblivion.esm | every model converted, both record types present |
| G4 | `Race skin tones: N races resolved`, one line per plugin | every N > 0 |
| G5 | each plugin's `door_panel_axis_cache.json`, for plugins with DOOR models that resolve and classify (see below) | current schema, holding every such model; plugins with none are N/A |
| G6 | ATXT-only land quadrants (see below) | all keep an alpha layer |
| G7 | terrain-LOD colour (see below) | MAE within bound, control worse, not grey |
| G8 | tree-card tiles vs tiles carrying NiAlphaProperty, over every baked worldspace | informational: vanilla carries none |
| G9 | each worldspace's LODGen input: rows on disk vs the printed `LODGen input` count vs the `Object-LOD selection` line | all equal; a mismatch after a NullReference retry (which rewrites the file) warns; a file not written inside the final `create_lod` step's window (its stamp to the next stamp, or to the log's last write, plus one second for whole-second stamps) fails: older is another run's, newer is a LATER run's |
| G10 | CRC-32 of every zip member vs the deployed file | all equal |
| G11 | final `create_lod` step: `NullReferenceException`, empty bakes; `.bto` per worldspace | none; at least `--min-bto` (TES4Tamriel 997) |
| G12 | `[plugin] Compilation: ok/total succeeded, N failed` | every plugin present, N = 0 |

<a id="g2"></a>
G2 reads mesh bytes and scans for `.dds` paths with its own pattern
(`MESH_TEXTURE_RE`), not the LOD fill's, because it must see what the engine
would be asked for:

- A match must contain a path separator. A NIF's header string table also
  holds node NAMES such as `CPStone01.dds.b:0`, which a bare `*.dds` pattern
  reads as a texture; no texture slot holds a separator-free name.
- `:` is allowed, so an authoring path left in a mesh
  (`Textures\tes4\f:\gogames\...\x.dds`) is reported whole, as the key the
  engine would look up, not cut at the colon.
- There is no trailing lookahead: the next sized string's length byte often
  follows `.dds` directly, and requiring a non-word byte there drops real
  references.
- A mesh's sized strings are preceded by NUL bytes, so a match cannot
  swallow its length prefix.

A header string that no block references (a stale string-table entry) is
still read; the scan does not parse blocks. Such keys, and textures that are
absent upstream too, belong in the `--allow-missing` file: one FULL key per
line, exactly as G2 prints it (`tes4\architecture\anvil\lorgenburn.dds`),
`#` for comments. Matching is by full key, never by file name, so allowing
one `black.dds` cannot hide another folder's. Entries that are no longer
missing are counted as stale. The list is data about one build's sources and
lives with that build, not in the code.

The control is picked from the run: the texture the most meshes name. If it
is in no table, the key normalisation or the table read is broken and the
check fails as blind.

<a id="g5"></a>
G5's expectation is derived from the plugin, never configured. The gate
reruns the converter's own door resolution (`_door_axis_jobs`: every DOOR
base's model that resolves in the export meshes) and classifier
(`door_closed_geometry` on the original mesh) and requires the cache to be at
`DOOR_AXIS_SCHEMA_VERSION` and to hold each model that classifies. Keys are
compared with the namespace dropped. A model that does not classify (bad
geometry) is not expected, because the scan cannot write it either. The
check does not require the cache to be written by this run: the pipeline
rescans only a missing or older-schema cache, so a current cache kept from
the last run is valid. Its freshness is reported in the JSON.
A stale cache is what this catches: `scan_door_axes` writes nothing for a
plugin with no jobs, and a cache from an older schema survives until the
next rescan.

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

Statuses: PASS, WARN (does not fail), INFO (never fails), N/A (the build has
nothing this check judges, e.g. no plugin has scripts; never fails), FAIL,
REFUSE. N/A is not REFUSE: REFUSE means the check could not see what it
should have seen (a missing log line, an empty glob), N/A means the build
itself holds nothing of that kind.
The JSON repeats each line with its data (per-tree file counts, missing
textures, per-worldspace row counts), so a failure names what to look at.

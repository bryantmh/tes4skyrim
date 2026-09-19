# tes5_import/overrides/manifest.py - merging a mod stack

**Code:** `tes5_import/overrides/manifest.py`, `tes5_import/overrides/nested.py`, `asset_convert/sources/base_plugins.py`

## Contents

- [The problem](#problem)
- [Part 1 — the ordered import](#part-1-ordered-import)
- [Part 2 — base resolution (asset_convert/sources/base_plugins.py)](#part-2-base-resolution)
- [Part 3 — what is still missing](#part-3-what-still-missing)
- [Interaction worth knowing](#interaction-worth-knowing)

Two problems that look unrelated and are the same one: a conversion decides
things per shape, but the inputs those decisions need live in *other* mods.

Measured 2026-08-19/20 on the author's Nehrim setup (base game + a mesh-fix
mod + a 4K retexture + a parallax mod).

## The problem
<a id="problem"></a>

Conversion decisions are **cross-mod**:

| decision | needs |
|---|---|
| does this shape get specular | whichever `_n.dds` **wins** in the load order |
| does it get parallax | whichever diffuse wins |
| is this mesh worn (`_0`/`_1` pair) | the plugin's ARMO/CLOT records |

Convert each mod alone and every one of those is answered against that mod's
own slice. A mesh-fix mod cannot see the retexture that will sit above it, so
its meshes ship with `specular_strength 0.25` even though the winning normal
map carries a real mask. MO2 layers **files**; by then the decision is already
baked into the NIF.

Measured, converting base Nehrim alone versus the same content as an ordered
merge — same conversion logic in both runs:

| | Nehrim alone | as a merged stack |
|---|---|---|
| shapes with a specular mask | 21245 (46.6%) | **43754 (91.9%)** |
| …normal map has no alpha at all | 21180 | **33** |
| parallax shapes | 1495 | **31872** |
| …texture unresolved | 7 | **2** |
| diffuse stripped to BC1 | 38 | **1898** (11 GB saved) |

The merge has 47610 shapes to Nehrim's 45592 (the mods add 374 meshes), so the
percentages are the honest comparison, not the raw counts.

## Part 1 — the ordered import
<a id="part-1-ordered-import"></a>

`convert.py --import-mod A B C --as NAME [--fresh]`

Sources apply **in order**, later overwriting earlier — the precedence a mod
manager applies, resolved once at import time. Everything downstream keeps
seeing a single coherent tree and needed no changes.

A source may be an archive, a mod folder, **or the name of an export tree that
already exists**. That last form is how the base game joins the stack instead
of sitting beside it, and it is hard-linked, so seeding a multi-GB base costs
no disk and takes seconds.

🔴 **`--fresh` matters.** A merge is defined by its FULL source list. Re-running
with a different list without it layers on top of the old one, and the index
then reports something that is no longer true.

### The index is the point, not a nicety

Each run prints who contributed what and which files a later source
overwrote. Real output:

```
  Nehrim.esm                       44277 placed,  22491 winning (50.2%)
  Nehrim UOP Meshes and Tex Fixes   1900 placed,    369 winning ( 0.8%)
  NTATU - Optimized Meshes          1731 placed,    651 winning ( 1.5%)
  NTATU 4x 2x                      12618 placed,  10236 winning (22.8%)
  NTATU and Qarl Parallax Maps 4x  11078 placed,  11078 winning (24.7%)
  TOTAL                            44825 files
```

A source reading `contributed nothing that survived` is the failure this
catches. Before the index existed, one forgotten folder cost a full analysis
pass over 8665 meshes to diagnose — the symptom was 5139 shapes with no height
map, and nothing pointed at the cause.

### Plugins are never pooled

Only assets merge. Each plugin still registers under its own name, so **one
ordered list yields both orderings by projection**: filter to entries that ship
assets for the asset order, to entries that ship plugins for the load order. A
plugin-only mod simply drops out of the first projection.

The one case a single list cannot express is when the two orders genuinely
*differ* (not merely "some entries missing") — a compatibility patch whose
assets must win early but whose plugin must load late. Rare; if it ever comes
up, an optional per-entry field is additive and costs nothing today.

## Part 2 — base resolution (`asset_convert/sources/base_plugins.py`)
<a id="part-2-base-resolution"></a>

Master-export blindness, the asset half. A mod ships only what it changes;
everything else lives in its base's export tree, which nothing reached.

Two carriers, because a mod declares its base two different ways:

* a **plugin** mod names its masters in `_HEADER.txt`;
* an **asset-only** mod has no plugin and no header, so the base is recorded at
  import time — `--base Nehrim.esm`, into `_source/.base_plugins`.

Four consumers, all previously blind:

| consumer | symptom when blind | measured |
|---|---|---|
| `resolve_source_texture` | no height map, no specular verdict | 1602 of 3357 referenced texture paths existed only in Nehrim.esm |
| `wearable_plan.build_plan` | no mesh is WORN → no `_0`/`_1` pair | 0 instead of 1412 entries |
| `grass_profile` | grass models lose the vanilla shader profile | 0 instead of 94 |
| `book_inam` | no book inventory art | 0 instead of 43 |

🔴 `build_plan` returns a nested `BIPED_FLAGS_KEY` map. Inheriting it with a
plain `dict.update()` **replaces** the base's flags instead of merging them —
merge per entry, own flags winning.

## Part 3 — what is still missing
<a id="part-3-what-still-missing"></a>

### Loose files in the Data folder are ignored

`bsa_extract.extract_assets_for_file` iterates BSAs **only** — verified in code
2026-08-20, and recorded in CLAUDE.md. There is no loose-file overlay.

This matters more than it looks. Bryant's suggested workflow is "let a mod
manager install everything, then point the converter at the resulting Data
folder". That is a clean answer — and for Oblivion it is especially apt,
because Wrye Bash installs **really**, not virtually, so the merged folder
genuinely exists on disk. But most Oblivion mods ship **loose** assets (against
the Skyrim norm of shipping BSAs), so today that folder would be skipped almost
entirely.

Adding the overlay would serve both workflows, and it also fixes the ordinary
case of a few loose replacers dropped into a normal Nehrim install.

### The two approaches are complementary, not competing

| | mod-manager route | ordered import |
|---|---|---|
| assumes | a finished, installed Oblivion setup | only the archives |
| conflicts resolved by | the mod manager | the order in the command |
| missing today | the loose-file overlay | — |

The ordered import exists for people whose goal is to play the content **in
Skyrim** and who do not want to build and maintain a second game install for
it. It also decouples the two lists: changing the Skyrim side otherwise means
rebuilding the Oblivion side.

## Interaction worth knowing
<a id="interaction-worth-knowing"></a>

`textures/…/landscape` serves both terrain (from `LAND`, which has no mesh and
so no shader property to set) and object meshes such as rocks.
`landscape_normals` rewrites DXT1 normals there with a dark alpha (32/255);
the mesh stage independently gives a maskless normal `specular_strength 0.25`.
A rock using such a texture therefore gets 0.125 × 0.25 ≈ 0.03 — **doubly
damped**. Left in place pending an in-game look; the precise fix is to narrow
`landscape_normals` to the textures `LTEX` records actually name.

Nehrim's 135 landscape normals classify as 58 `mask`, 73 `no_alpha`, 4
`binary`. The masked ones already resolve correctly with no extra work, and the
fix is mod-safe by construction: it bails on anything that is not DXT1, so a
mod's real specular map can never be overwritten.

---

## Resolving a master's export directory
<a id="master-export-resolution"></a>

**Code:** `tes5_import/overrides/nested.py` — `export_root`,
`master_export_dir`.

🛑 **Never derive the export root as `dirname(export_dir)` + the master's
name.** `export_dir` is a RECORD directory, and an **imported mod nests its
plugins inside the mod's own folder**, so the parent is the mod rather than
the export root and the join finds nothing.

Every consumer resolves through these two helpers instead, so the master's
own run and the dependent plugin's adoption of it read the SAME `RACE.txt`.

Three call sites relearned this the hard way, each with a silent failure:

| Caller | Symptom when the join failed |
|---|---|
| `import_main._master_export_dirs` | Returned `[]` for every grouped plugin, so the adoption loop never ran and every actor fell through to the Imperial default voice. |
| `actors/creature_races` | No master projects were inherited, so every CREA reusing the master's creature folder shipped as a **base Skyrim creature**. |
| `base/artifact_schema` | Preflight could not see the master's artifacts. |

### Projects are keyed on the model folder's LEAF name

A plugin shipping its own folder under a master's leaf name shadows the
master's project **even when its CREA records point at the MASTER's path**.

Morroblivion ships `meshes/morroblivion/creatures/daedra/scamp` — dead assets
no CREA references, only a `mesh.nif` that is in no NIFZ set — while its
`0Scamp` record points at Oblivion's `Creatures/Scamp`. The empty own project
won, `_bodies_of` found no body for the record's NIFZ set, no race was
generated, and **the scamp shipped as a vanilla SkeletonRace actor**.

Own projects win on conflict — this plugin's own conversion of a folder is
authoritative for the records it ships — EXCEPT when the own project ships no
body mesh at all, which is never authoritative for anything (see `_usable`).

Without the master's projects at all, CREA records fall through to
`resolve_creature_race` and ship as BASE SKYRIM creatures: a Skyrim frostbite
spider or a Nord standing in for the converted Oblivion actor. Morrowind_ob.esm
places **86** CREA records on Oblivion.esm's rat/skeleton/goblin meshes, which
its own BSA never ships.

### <a id="inherited-creature-folders"></a>An inherited folder's MOVT + IDLE set is written ONCE, by the master

**Code:** `tes5_import/actors/creature_projects.py` — `folders_built_by_master`.

A plugin whose CREA records sit on a master's creature folder needs its own
RACE / skin chain, but NOT a second copy of the folder's MOVT and IDLE
records. Nothing references either by FormID: the engine binds a MOVT by
`MNAM` against the graph's `iState_*` names and an IDLE tree by `DNAM` against
the behavior path. A second copy only competes with the master's, and goes
stale whenever the master's creature build changes and the dependent is not
re-imported.

Measured: after Tamriel_Data.esm was rebuilt with ragdolls its
`TES4tr_vermaiDeathWait` sent `DeathAnimation`, while TR_Mainland.esm's older
copy of the same record (same `DNAM`) still sent `deathStart` — the plain
death clip, no ragdoll. Not yet confirmed in game as the cause.

The master wrote the set exactly when one of ITS OWN CREA records uses the
folder with a body, and the project is the master's own (`owner_slot` from
`load_projects` equals the CREA's master slot). Anything else — a master that
ships the meshes but no creature on them — still gets the set from the
dependent.

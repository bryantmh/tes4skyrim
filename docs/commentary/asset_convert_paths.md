# asset_convert/case_paths.py — case-blind asset paths

**Code:** `asset_convert/case_paths.py`

## Contents

- [The case resolver](#case-resolver)
- [The write rule](#write-rule)
- [The case census](#census)
- [The pack gate](#pack-gate)

## The case resolver
<a id="case-resolver"></a>

**Code:** `resolve`, `exists`, `variants`, `rglob`, `list_prefix` in
`asset_convert/case_paths.py`

TES4 records and NIFs keep their author's mixed case (`Textures\Landscape\
Dirt02.DDS`, `MagicEffects\Fireball.NIF`), BSArch extraction writes lowercase,
and a mod's loose files keep whatever case it shipped. Windows resolves all of
them; a case-sensitive filesystem (the Linux/Wine build) resolves none, and
each site that joined a record path onto a root silently fell back: grey
terrain LOD, generic spell art, missing FR door axes and sounds. The LOD probe
(`port-test/reports-20260928/probe_lod/`) counted about 20 live sites.

`resolve(roots, rel, site)` is the one lookup every site uses:

- **Roots are searched fully, in order.** For each root: the exact path first,
  then a case-blind walk. Root 1 beats root 2 even when root 1 needs the
  case-blind walk. Windows (and any already-correct path) hits the exact test
  and never lists a folder.
- **The walk branches into every case variant.** Oblivion's own output holds
  twin folders (an empty `Dementia/` beside `dementia/x.dds`); the two private
  `_join_nocase` copies this module replaced kept one spelling per folder, so
  whichever the listing order picked decided hit or miss (143/166 terrain
  textures found by the shaders copy).
- **Listings are cached per folder and re-read on a miss when the folder may
  have changed**: its mtime moved, or the listing was taken within 2 s of the
  folder's mtime (a create in the same timestamp tick). A file written after
  the cache was built is still found; a hit is re-checked with `is_file`, so a
  deleted file is never returned. A per-folder cache (not a whole-tree index)
  keeps root order, stays fresh and costs a worker nothing it does not visit.
- **Two spellings of one file in a root** answer with the all-lowercase one,
  else the smallest path string, and log `CASE COLLISION` once per key.
- **Counts** (`exact / resolved / missed / collisions` per site) are per
  process; a mesh worker returns `snapshot_counts()` with each result and the
  parent merges them, so `report()` covers the pool.

## The write rule
<a id="write-rule"></a>

**Code:** `write_path` in `asset_convert/case_paths.py`; its writers are
`ensure_ltex_normals` (`asset_convert/texture/landscape_normals.py`) and
`_texture_out_path` (`asset_convert/nif/nif_converter.py`: flipbook atlases and
parallax height maps)

A converter-written file goes to `write_path(root, rel)`: each segment reuses
the one spelling already on disk (a live listing, never the cache), is created
lowercase when none exists, and takes the lowercase spelling (logging a
collision) when several do. New names are lowercase, which is what BSArch
stores (every folder name in the shipped archives is lowercase), and no write
opens a second spelling of a folder that already exists.

Mirror copiers (`nif_batch` destination, `asset_pipeline._copy_tree`,
`mod_ingest._place_payload`) keep the SOURCE case on purpose: lowercasing them
against today's output would create about 930 FR twins. Tripwire: before
lowercasing mirror writers, require a clean output tree and a census of 0.

## The case census
<a id="census"></a>

**Code:** `census`, `collisions`, `census_line` in `asset_convert/case_paths.py`

`census(root, subdirs)` reports folder twins (siblings differing only by case)
and file collisions (two files with one lowercase archive key). Folder twins
are a warning: BSArch lowercases every name, so twins merge harmlessly in the
archive (the 09-28 pack succeeded with them, and `Oblivion/` holds the only
copy of `TerrainHDOblivionEvilSymbol01_n.dds`, so they are never deleted). A
file collision is a failure: one of the two files is silently lost. With
`subdirs`, every spelling of each top folder is walked as the one tree the
packer merges it into, so `Textures/` beside `textures/` is not itself a twin.

## The pack gate
<a id="pack-gate"></a>

**Code:** `_collect_files`, `case_gate` in `asset_convert/sources/bsa_pack.py`;
`phase_pack_zip`, `_run_steps` in `convert.py`; the census line at the end of
`convert_meshes` in `asset_convert/asset_pipeline.py`

- **Every case spelling of a top folder is packed.** The packer used to read
  only `plugin_dir/textures` and `plugin_dir/meshes`, and the misc-folder scan
  excluded any spelling of those names, so a `Textures/` beside `textures/`
  (a mod's own casing, or a writer that joined a record path as-is) never
  reached the BSA. Now each spelling is collected under the lowercase archive
  top, and misc folders are deduplicated by case the same way.
- **The gate fails only on file collisions**, measured on the lowercase
  archive path AFTER `texture_prune`, i.e. on exactly what would be packed:
  two files there means BSArch keeps one and silently drops the other. The
  plugin is then not packed at all and the collision groups are printed.
  Folder twins print a `WARN` census line and pack normally.
- **The zip refuses a failed pack.** `convert._run_steps` runs every step
  whatever an earlier one returned, so a gated (or otherwise failed) BSA pack
  used to be followed by a zip of whatever `.bsa` files were on disk: stale
  archives from an earlier run, shipped as the finished mod. `phase_pack_zip`
  now refuses when this run's `pack_bsa` failed for that plugin, and says so;
  an existing zip from an earlier run is left in place and named.
- `convert_meshes` ends with a census line for the plugin's output folder and
  the per-site `Case paths:` counts, so a case problem shows up at conversion
  time rather than at pack time.

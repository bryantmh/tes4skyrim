# asset_convert/case_paths.py — case-blind asset paths

**Code:** `asset_convert/case_paths.py`

## Contents

- [The case resolver](#case-resolver)
- [The write rule](#write-rule)
- [The case census](#census)

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

**Code:** `write_path` in `asset_convert/case_paths.py`

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

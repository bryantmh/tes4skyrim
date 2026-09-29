# asset_convert/case_paths.py — case-blind asset paths

**Code:** `asset_convert/case_paths.py`

## Contents

- [The case resolver](#case-resolver)
- [The write rule](#write-rule)
- [The case census](#census)
- [The pack gate](#pack-gate)
- [Plugin names in any case](#plugin-names)

## The case resolver
<a id="case-resolver"></a>

**Code:** `resolve`, `exists`, `variants`, `rglob`, `list_prefix` in
`asset_convert/case_paths.py`

TES4 records and NIFs keep their author's mixed case (`Textures\Landscape\
Dirt02.DDS`, `MagicEffects\Fireball.NIF`), BSArch extraction writes lowercase,
and a mod's loose files keep whatever case it shipped. Windows resolves all of
them; a case-sensitive filesystem (Linux, or a case-sensitive folder on
macOS or Windows) resolves none, and each site that joined a record path onto
a root silently fell back: grey terrain LOD, generic spell art, missing
Frostcrag Reborn door axes and sounds. A sweep of the converter counted about
20 such sites.

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

A test that builds case twins takes the `case_twins` fixture
(`tests/conftest.py`): it probes the tmp filesystem and skips where names
fold case (Windows, a default macOS volume), since there the second spelling
lands in the first and the setup itself is impossible.

## The write rule
<a id="write-rule"></a>

**Code:** `write_path` in `asset_convert/case_paths.py`; its writers are
`ensure_ltex_normals` (`asset_convert/texture/landscape_normals.py`) and
`_texture_out_path` (`asset_convert/nif/nif_converter.py`: flipbook atlases and
parallax height maps)

A converter-written file goes to `write_path(root, rel)`: each segment reuses
the one spelling already on disk, is created lowercase when none exists, and
takes the lowercase spelling (logging a collision) when several do. No write
opens a second spelling of a folder that already exists.

**Lowercasing new names is intended, not a side effect.** A caller's `rel`
carries whatever case the record or NIF authored, so honouring it would let
two records spelling one folder differently open twins on a case-sensitive
filesystem. Lowercase is also what BSArch stores (every folder name in the
shipped archives is lowercase), and the game looks paths up case-blind, so the
spelling of a new file never changes what loads.

The listings are the resolver's cache. The cached listing answers first; only
a miss re-reads the folder (the stale check above), so an unchanged ancestor
is listed once per process rather than once per write, which matters on
Windows, where listing a large folder is slow. A folder that gains a new entry
through `write_path` is dropped from the cache; an overwrite leaves it cached.
`root` is normalised, so `out/` and `out` are one cache key.

Mirror copiers (`nif_batch` destination, `asset_pipeline._copy_tree`,
`mod_ingest._place_payload`) keep the SOURCE case on purpose: lowercasing them
against an existing Frostcrag Reborn output would create about 930 case
twins. Tripwire: before
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

## Plugin names in any case
<a id="plugin-names"></a>

**Code:** `canonical_plugin_name`, `asset_root_name`, `record_dir`,
`directory_for`, `copies` in `asset_convert/sources/source_registry.py`;
`plugin_name`, `plugin_esm`, `PluginPaths.esm` in `output_layout.py`;
`resolve_plugin_path` in `source_paths.py`; `_export_dirs_with_masters` in
`script_convert/cross_ref.py`; `topological_order` in `core/plugin_masters.py`;
`_select_plugins` in `tools/release/create_lod.py`

A plugin's header can name its master in any case (`oblivion.esm`), and so can
a `-f` or `--plugins` argument. Every folder and file built from the name
resolves it first: `canonical_plugin_name(export_dir, name)` answers the
registered name for an imported mod, else the one folder of `export/` spelling
it in any case (the exact spelling wins), else the name unchanged; two folders
differing only by case raise. It lists the folder on every platform, so
Windows answers the same spelling. `record_dir`, `master_record_dir`,
`asset_root_name`, `plugin_out_root` and `plugin_esm` all go through it, and
so does everything built on them: `nested.master_export_dir`,
`pipeline.master_export_dirs`, `creature_projects`, `papyrus_compile` and
`master_index`'s `paths(name).esm`. A plugin binary in a Data folder is found
through `case_paths`, and so is the file after `directory_for` answers its
folder (`morrowind_sidecar_source.source_binary`, `morrowind_patch.source_paths`,
`morrowind_body._master_records`): joining the asked spelling there named a
file that does not exist. Name comparisons (`topological_order`, `_select_plugins`)
lowercase both sides and keep the listed spelling.

Not yet routed: `asset_convert/lod/sibling_lod.py` (`master_chain`,
`_master_rank`, `dependents_of`) compares header master names to the plugin
list case-sensitively, so a lowercase master is dropped from the LOD order.
Tripwire: fix it with the object-LOD stream that owns that file.

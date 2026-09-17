# tes5_import/overrides/nested.py - plugins with masters

**Code:** `tes5_import/overrides/diff.py`, `tes5_import/overrides/manifest.py`, `tes5_import/overrides/builder.py`, `tes5_import/overrides/master_index.py`

## Contents

- [Authorship: what counts as a change](#authorship-what-counts-as-change)
- [FormIDs and master indices](#formids-master-indices)
- [GRUP nesting and record ordering](#grup-nesting-record-ordering)
- [Cells, buckets and ONAM](#cells-buckets-onam)
- [Scripts: the masters' export is part of the identifier namespace](#scripts-masters-export-part-identifier)
- [One TES4 field can feed TWO output subrecord runs](#one-tes4-field-can-feed)
- [A PGRD is never an override: it converts to a NEW NAVM](#pgrd-never-override-converts-new)
- [Deleting a master's record: the three shapes](#deleting-masters-record-three-shapes)
- [A quest-owned package must never be in an NPC's PKID list](#quest-owned-package-must-never)
- [The nested-override emission passes](#nested-override-emission-passes)

Linked from [CLAUDE.md](../../CLAUDE.md).

Converting a plugin that has TES4 masters (Nehrim's `Translation.esp` is ~100%
overrides) follows xEdit's "copy as override" model. Modules:
`overrides.py` (OverrideContext + nested-GRUP emission — the only override
code import_main touches), `export_diff.py`, `master_manifest.py`,
`override_builder.py` (field application), `override_merge.py` (master index).
Audit coverage anytime with `python tools/audit/override_audit.py
export/<Plugin>` — it reports, per record type, what the override path does
with every record and every authored field with no output mapping.

**The rule:** a NEW record takes the normal conversion path. An OVERRIDE is the
master's converted record bytes EXACTLY, with only the fields the author changed
substituted in — and authorship comes from diffing the two TES4 EXPORTS, never
from comparing two conversion runs.


## Authorship: what counts as a change
<a id="authorship-what-counts-as-change"></a>

- **Never diff two conversions.** It conflates "the author changed this" with
  "our pass re-derived it differently", and the converter cannot tell them
  apart. That produced 1821 NPC_ `RNAM` races rewritten to vanilla Skyrim ones
  (the authors changed ZERO) and hung the game on load. Diffing the exports
  answers the question directly: a field neither export touches is never
  rewritten, so it cannot drift. No heuristics, no guessing.
- **List diffs must be ORDER-INSENSITIVE.** Oblivion does not preserve list
  order between a master and an overriding plugin: 1166 of 1264 Nehrim NPC_
  inventories differ positionally while only 5 differ as a set.
- **Export every record; never filter by load-order index.** A record whose
  FormID carries a master's index is an OVERRIDE. The old `source_filter`
  dropped 13,890 of Translation.esp's 13,892 records.

## <a id="re-keying-the-masters-ids"></a>Re-keying the masters' ids

**Code:** `overrides/nested.py:load_master_export`.

A record is named by its index byte, which is the master's slot in *this*
plugin's master list — NOT the slot it uses in its own file. Merging the
exports on the raw id instead collapses every master's id space into one, so
the last-loaded master silently wins ids belonging to an earlier one, and the
override is then diffed against a record of a COMPLETELY DIFFERENT TYPE.

Measured on TWMP Valenwood/Elsweyr (masters Oblivion.esm, Tamriel.esp,
ElsweyrAnequina.esp): 119,443 of 121,505 shared ids resolved to the wrong
record type — 59,770 LAND and 59,668 CELL clobbered, mostly by ANQ's REFRs.
`0102DDE5` is a LAND in Tamriel.esp and the creature ANQCORPantherCaged
("Black Panther Cub") in ElsweyrAnequina.esp, so the terrain override was
diffed against a creature and the builder spliced that creature's FULL (and
DESC) into the LAND. xEdit reported "record LAND contains unexpected (or out
of order) subrecord FULL", and the engine hung forever on the main menu.

A master's OWN records carry the index byte equal to its own master count.
Ids BELOW that belong to the master's own masters and are translated by NAME
through this plugin's list — the two orders usually agree, but relying on
that is the same unchecked assumption this function exists to remove.

## FormIDs and master indices
<a id="formids-master-indices"></a>

- **The FormID shift is the count of NEWLY PREPENDED masters, not
  `len(masters)`.** Using the latter moves overrides onto the plugin's own
  index, turning all 12,177 into duplicate new records.
- **Companion pairings come from a MANIFEST, not inference.** Converting one
  record generates companions (ARMO->ARMA, NPC_->OTFT/VTYP, AMMO->PROJ) whose
  ids come from a bare sequential counter. `writer.converting(source_fid)`
  records the pairing AT CREATION into `<Plugin>.manifest.json`; the plugin's
  run reads it. Re-deriving is impossible — the plugin converts a few thousand
  records, not the master's ~700k, so the counter lands elsewhere.
- **A cell override ships ONLY the references it CHANGES, never the master's
  whole child list.** Verified against BS_DLC_patch.esp: 54 cell overrides, ~5
  REFRs each (278 total), not the master's contents. Copying the full list
  bloated Translation.esp to 34 MB and duplicated 265k records for no benefit.
- **ONAM is what keeps the master's other refs visible.** The header's ONAM
  array lists every record this file overrides in a master's TEMPORARY cell-
  children group (type 9); the engine loads those on demand and, per xEdit's
  docs, "will ignore the override that is missing from ONAM". Persistent refs
  (type 8) are always resident and excluded. BS_DLC_patch's 216 ONAM entries
  are exactly its 216 temporary-group overrides — `writer.py` builds the list
  the same way at save time. WITHOUT ONAM a cell override suppressed the
  master's temporary children and interiors rendered black.

## GRUP nesting and record ordering
<a id="grup-nesting-record-ordering"></a>

- **A record's children group must directly FOLLOW that record.** The engine
  reads `CELL, GRUP(6,cell), CELL, GRUP(6,cell), ...`; emitting all the records
  first and their groups afterwards pairs each group with the wrong cell.
- **A type-1/6/7 GRUP is bound to its owner ONLY by physical adjacency, so an
  unchanged owner must be pulled in as an ANCHOR.** xEdit states the rule
  exactly (`TwbGroupRecord.InformPrevMainRecord`, wbImplementation.pas ~18023):

      if (grStruct.grsGroupType in [1, 6, 7]) and Assigned(aPrevMainRecord)
         and (aPrevMainRecord.FixedFormID.ToCardinal = GetGroupLabel) then

  1 = world children (under WRLD), 6 = cell children (under CELL), 7 = topic
  children (under DIAL). A group of one of those types that is NOT immediately
  preceded by its owning record attaches to NOTHING, and every record inside it
  is unreachable — **while the file still loads cleanly and still looks correct
  in xEdit**, which is what made this silent. A plugin hits it whenever it
  changes a container's CONTENTS but not the container itself: DLCBattlehornCastle
  overrides Tamriel's exterior cells without touching `WRLD 0000003C`, so its
  type-1 group stood alone and all 473 exterior cell/REFR overrides were dead;
  Translation.esp had 958 orphans, 945 of them type-7 (it retitles INFOs under
  DIALs it never edits). The fix is generic in `emit_nested_overrides`: for any
  owned-type group whose owner this plugin does not emit at that level, the
  owner's converted bytes are pulled from the master VERBATIM and written
  immediately before the group — the same thing xEdit's copy-as-override does.
  Anchoring only the type-6 case (the original code) leaves the WRLD case
  broken. Gate every override plugin with `tools/validate/esm_group_anchors.py`.
- **The plugin's TES4 master NAMES come from the export `_HEADER.txt`, not the
  source binary.** `convert.py` derives its master list from the binary in the
  configured Oblivion data folder, but that file may not be there at all (a
  Nehrim plugin against a Steam Oblivion install), so the list came back as
  just `['Skyrim.esm']` while `_HEADER.txt` still correctly reported 1 TES4
  master. Taking the count from one source and the names from the other made
  `masters[len(masters) - count:]` slice the tail off `['Skyrim.esm']` and
  demand a converted `output/Skyrim.esm/Skyrim.esm` — Translation.esp aborted
  with "convert the master first: Skyrim.esm" and could not be built at all.
  `import_main` now reconciles the two and trusts the header.
- **NEVER synthesize an empty children group.** xEdit deletes them:
  `if Assigned(ChildGroup) and (ChildGroup.ElementCount < 1) then
  ChildGroup.Remove` (wbImplementation.pas ~5607).

## Cells, buckets and ONAM
<a id="cells-buckets-onam"></a>

- **Interior cells bucket by the last two DECIMAL digits of the OBJECTID**
  (`fid & 0xFFFFFF`): block = ones digit, sub-block = tens digit (xEdit
  wbImplementation CheckPosition; verified against Skyrim.esm). Bucketing by
  the full FormID (master-index byte included) put every Nehrim interior in
  the wrong block/sub-block. The master ALONE still played fine — the engine
  reads a winning cell's children from the offset recorded at load — but when
  a plugin overrides the CELL record, the engine re-locates the MASTER's copy
  via the bucket walk to demand-load its temporary children; wrong bucket =
  refs silently missing, only the eagerly-loaded persistent refs survive
  (renamed Translation.esp cells showed only Nehrim.esm persistent refs, and
  an xEdit copy-as-override reproduced it because xEdit buckets correctly
  while the master didn't). Exterior labels were verified CORRECT as written:
  `('<hh', Y, X)` with FLOOR division (Skyrim.esm grid x=7,y=-41 sits in
  sub-block low=-6=y//8, so Pascal-style truncation would be wrong).
- **Persistent-flagged refs inside temporary groups are vanilla-legal** —
  Skyrim.esm itself has 0x400-flagged REFR/ACHRs in type-9 groups
  (DragonBridgeFarm). DLC ESMs keep it clean (persistent overrides in type 8),
  which our source-flag routing already matches. Don't "fix" this.
- **But the record must still sit in the master's GRUP NESTING.** Interior:
  `CELL -> type 2 -> type 3`. Exterior: `WRLD -> type 1 -> type 4 -> type 5`.
  INFO: `DIAL -> type 7`. A record written flat under its top-level group is
  never indexed by the engine — as invisible as a missing one, and the second
  cause of black cells. Copy the nesting from `MasterIndex.group_path()`
  instead of recomputing it; there is then no block-number formula to get wrong.
  (Note the GRUP header layout: label is at offset 8, type at 12.)
- **Skip support-record creation for a plugin with masters.** VTYP/LCTN/vendor
  factions/TES4 globals are created by the master's run; recreating them in a
  dependent plugin duplicates master content (27 VTYP, 35 GLOB, 27 FACT, 265
  LCTN) and the duplicates compete with the originals the overrides reference.
- **INJECTED records** (Oblivion let a plugin ADD records at a master's index;
  Translation.esp does it 3x) move into OUR index. Detect against the master's
  **export**, never its converted output: conversion re-keys DIAL/INFO and skips
  whole types, so judging by output called 1693 records injected instead of 3.
- An export key `override_builder` cannot map is REPORTED, never approximated —
  the master's value stays and the run prints a summary. Mapped changes are
  applied three ways: translated-string substitution, SUBRECORD REBUILD (the
  converter's own builder — `_npc_acbs`, `build_cell_xcll`, `build_armo_bod2`,
  … — re-run against the PLUGIN's export and swapped in whole; drift-free
  because unchanged fields are identical in both exports), and run rebuilds
  for repeated-subrecord families (CNTO/SPLO/PKID) that preserve
  converter-ADDED entries (vendor gold, quest-package filtering) by deriving
  them from master-export-vs-master-output. Effect-list changes (SPEL/ENCH)
  RECONVERT the whole record instead — clone companions can't be spliced.
- **TES4 QSTA 'Flags' is a u8 + 3 bytes of uninitialized CS garbage** — diff
  it masked (`export_diff._LIST_FIELD_NORMALIZERS`) or 58 quests report
  phantom Target[] changes. Expect more TES4 fields like this; the fix
  belongs in the DIFF, never the export (which stays a pure dump).
- **LAND terrain overrides: VNML/VHGT/VCLR are hex blobs with IDENTICAL TES4
  and TES5 layout**, so `convert_LAND` copies them straight through and the
  authored value is directly substitutable — no re-derivation, no drift. They
  had no `_REBUILDERS` entry at all, so every terrain edit was reported
  "no output mapping" and kept the master's terrain: DLCBattlehornCastle
  authored `VHGT` on all 16 of its LAND overrides (plus VNML×10, VCLR×8,
  Layer[]×5) and the castle sat on Oblivion's untouched hillside. `Layer[]`
  needs a `_RUN_REBUILDERS` entry instead — the whole BTXT/ATXT/VTXT run is
  replaced through `world.build_land_layers` (extracted from `convert_LAND` for
  exactly this). **Reuse that builder, never reimplement it**: the mapping is
  lossy and order-dependent (same-texture merge, coverage sort, 6-alpha-per-
  quadrant cap), so a second implementation would disagree with the master's
  for layers the author never touched. Verified the extraction is pure —
  4,000/4,000 master LAND records still convert byte-identically.
  - **The last 3 bytes of VHGT are `wbUnused(3)`** (wbDefinitionsCommon.pas
    `wbLandHeights`: `wbFloat Offset` + 33×33 `itS8` + `wbUnused(3)`). Vanilla
    settles it: a census of 15,410 Skyrim.esm LAND records finds arbitrary junk
    there — `000000` is merely the most common of many values (`3e9e23`,
    `b57086`, `ea5b25` … each in the hundreds-to-thousands). Comparing them
    reported phantom VHGT changes on 6 of the 16 whose real terrain was
    identical, emitting override records with zero authored content. Normalized
    in `export_diff._SCALAR_NORMALIZERS`, and `_Rebuild(keep_tail=3)` preserves
    the MASTER's pad when the terrain genuinely did change, so an override
    diverges only where the author sculpted.
- **A NEW record nested in a master's GRUP tree** (Translation.esp injects a
  map-marker REFR into a Nehrim cell) is converted normally and placed under
  the master parent's children group; if the parent record isn't already
  overridden, its converted bytes are pulled in VERBATIM as the anchor —
  the engine pairs a children GRUP with the record preceding it, so a group
  can never stand alone (same as xEdit's copy-as-override of a reference).
- <a id="anchor-per-worldspace"></a>**A plugin can add NEW exterior cells to a
  MASTER's worldspace while ALSO owning worldspaces of its own — anchoring is
  decided PER WORLDSPACE, never per plugin.** Such a plugin ships no WRLD for
  the master's worldspace (its only WRLD record there is an override, already
  emitted by the override path), so `_build_world_groups` must pull the
  master's converted WRLD in as an anchor for those cells. Gating that on
  "this plugin has no worldspaces at all" fits a pure heightmap
  (Tamriel.esp: 1 WRLD, itself an override of `0000003C`) but silently drops
  every new cell of a plugin that does both: ElsweyrAnequina.esp owns 9
  worldspaces, so the anchor was skipped and all **831** of its new Tamriel
  cells (x −32..0, y −64..−30) vanished while its 1,054 plain overrides
  survived — a 100%-new / 0%-override split that is the signature of this
  defect. In-game the player stood on whatever OTHER plugin filled that
  coordinate (onra's unmatched placeholder heightmap, several thousand units
  off), which reads as a terrain discontinuity underfoot rather than as
  missing land. Subtract the plugin's own WRLD FormIDs from the set of
  worldspaces its cells name and anchor the remainder; the two job lists stay
  disjoint so no FormID is emitted twice.
- <a id="sibling-lod-order"></a>**Sibling LOD: an UNLISTED plugin must be the
  LOWEST priority, never the highest.** The merged-tile bake stacks every
  sibling as an overlay and the LAST one applied wins every FormID it shares,
  so `sibling_lod._load_order` IS the conflict resolution. It ranked plugins
  by `plugins.txt` (correct) and then appended anything absent from that file
  **after** the ranked ones — handing the final word to exactly the plugins
  the user never positioned. DLCBattlehornCastle.esp (14 changed cells, not in
  plugins.txt) thereby outranked ElsweyrAnequina.esp (1,855) and Tamriel.esp
  (99,910) and won every tile the three shared, so merged tiles contradicted
  the load order the game itself obeys. Sort unlisted plugins FIRST: the
  engine's own treatment of a plugin missing from plugins.txt is not to load
  it at all, so lowest priority is the faithful analogue. The same inversion
  existed in `gui.py::_default_lod_order`, which seeded the drag-to-reorder
  list — the list the user sees must mirror the order that actually runs, or
  the panel misreports the winner and feeds a wrong `explicit` order straight
  back in. Guarded by `tests/test_sibling_lod_order.py`.
- <a id="terrain-overlay-scoping"></a>🛑 **An OVERLAY that does not define the
  worldspace must contribute NOTHING unscoped — never "every LAND record".**
  `terrain_lod._scan_land_file` resolves the target worldspace's FormID from the
  file it is scanning and, failing that, fell back to collecting every LAND
  record in the file. That fallback is a defensible last resort for the file a
  worldspace is SOURCED from. For an overlay it is silent data corruption, and
  the one-bake LOD model made it fire routinely because it stacks every
  dependent plugin as an overlay at once.
  Measured on the real output: Morrowind_ob.esm ships no TES4Tamriel WRLD record
  (it overrides cells through Oblivion.esm's GRUPs), so the fallback collected
  **all 5,796 of its Vvardenfell cells into Cyrodiil's heightmap** — 5,787 of
  them on coordinates Oblivion.esm legitimately owns, stamping Vvardenfell
  across central Cyrodiil's distant terrain. Scoped: 5,796 → **0**.
  The fix is not "return nothing when the WRLD is absent", which would throw
  away the edits that must survive: DLCBattlehornCastle regrades 10 Tamriel
  cells while shipping no WRLD either, and its records sit under a type-1 GRUP
  labelled with the MASTER's WRLD FormID. So `parse_land_records` resolves the
  FormID once from the file that DEFINES the worldspace and passes it to every
  overlay as `known_wrld_fid`; only the base file keeps `allow_unscoped=True`.
  The object-LOD path (`lod_gen`) was never affected — it resolves `wrld_fid`
  once from the MERGED worldspace table and filters every ref on `parent_wrld`,
  so an overlay's own worldspaces keep their own FormID and are excluded.
  Guarded by `tests/test_terrain_lod_scoping.py`, which builds synthetic ESMs so
  both branches stay distinguishable.
- <a id="lod-formid-normalization"></a>**🔴 THE LOD MERGE RESOLVES FORMIDS
  THROUGH EACH FILE'S OWN MASTER LIST — RAW IDS ARE NOT COMPARABLE.**
  (found 2026-08-12)

  This is the same defect the importer fixed in four places (see the
  master-index routing entry above), and it was still live in the LOD stage.
  `generate_lod` merges overlays into one reference pool keyed by FormID, and
  `scan_land_file`/`_scan_cell_coords` accumulate several files into one
  cell table — all on the RAW id. But the index byte is an offset into the
  file's OWN `MAST` list, so the same integer names different records in
  different plugins, and one record has different integers in different
  plugins. Both directions broke:

  - **False merges.** Morrowind_ob.esm and Tamriel.esp both declare
    `[Skyrim, Oblivion]` and are therefore both `02` = self. They collide on
    **4 CELL FormIDs** — Morrowind *interiors* against Tamriel *exteriors* —
    which pulled **183 Morrowind interior objects into Cyrodiil's distant
    terrain**. Another **1,278** references collided between plugins that are
    not each other's masters.
  - **Missed merges, the expensive half.** `ElsweyrAnequina.esp` is its own
    `02`, but in `TWMP_Valenwood_Elsweyr.esp` slot `02` is `Tamriel.esp` and
    ANQ sits at `03`. VE places **52,100** references onto ANQ base objects,
    written `03xxxxxx`; looked up raw against ANQ's `02xxxxxx` stats they
    resolved **0 of 52,100**, so every one of those objects silently had no LOD
    mesh. Measured A/B: `0` before, `52,100` after. VE also went from
    `+401,589 added / ~0 replaced` to `+399,110 / ~2,479` — records it was
    duplicating are now correctly recognised as overrides.

  `lod_gen._formid_remap_table` rewrites each file's index bytes to a
  process-wide byte that names the same FILE in every plugin, so
  `(global_byte << 24) | local_id` is comparable across the load order and
  still fits an int (a local id is only 24 bits). Every id crossing a file
  boundary is normalized: record ids, GRUP labels, `REFR.NAME` base pointers,
  `known_wrld_fid`, and `_scan_cell_coords` keys. **A blanket +N shift is
  wrong** for the same reason it was wrong in the importer — files share their
  low masters, so only bytes that actually move may be rewritten, matched BY
  NAME.

  Guarded by `tests/test_lod_overlay_scope.py` (the ANQ slot-shift case
  included). Synthetic test plugins must declare a `MAST` list or every id
  resolves to the file itself.
- <a id="create-lod-order"></a>**`create_lod_order` deliberately differs from
  `load_order`, and the difference is CONSENT.** LOD is generated for the
  whole load order in one pass (`tools/release/create_lod.py`, the GUI's *Create LOD*
  button) and that dialog SHOWS the order, lets the user drag it, and does
  nothing until they press Generate. So the rule is the one the user asked
  for: everything `plugins.txt` lists comes FIRST in its own order, and
  everything else is appended at the BOTTOM. `load_order`'s
  unlisted-plugins-first rule is right for a merge nobody looked at — an
  unpositioned plugin must not silently outrank a positioned one — but wrong
  once the list is on screen and confirmed.
  The append is alphabetical **but constrained by masters**: a plugin never
  sorts before one of its own masters, because a dependent's tiles are baked as
  "master + dependent" and already contain the master's terrain, while the
  master's own tiles contain nothing of the dependent's. Applying the master
  last would therefore undo the dependent. On the real load order this is what
  keeps `Translation.esp` after `Nehrim.esm` and would keep a hypothetical
  `AAAPatch.esp` after the `Tamriel.esp` it patches, despite the alphabet.
  Depth counts only masters that are actually in the selection, so deselecting
  a master does not push its dependent to the bottom. Guarded by
  `tests/test_create_lod_order.py`.
- **The merged-LOD folder must be CLEARED per worldspace before rebaking.**
  The bake writes only the tiles it produces this run, so anything an earlier
  run left behind survives as an orphan and still ships — and since the folder
  exists specifically to win the overwrite, an orphan beats the correct tile at
  that path. After the load-order fix above, 5 `.btr` + 10 `.dds` at tile `0.0`
  remained from a previous run while the corrected bake produced no `0.0` tile
  at all. Clear by the worldspace's own tile glob (`<edid>.*` under
  `meshes/terrain/<edid>` and `textures/terrain/<edid>`), never the whole
  folder: a run covering several worldspaces would otherwise delete a sibling
  worldspace's fresh output, and the merged object `.nif`s under `Objects/`
  are shared and coordinate-free, so they must survive.
- <a id="land-first-in-type-9"></a>🛑 **LAND MUST BE THE FIRST RECORD IN A
  CELL'S TEMPORARY (type-9) CHILDREN GROUP.** Census of real `Skyrim.esm`:
  **15,564 of 15,564** type-9 groups containing a LAND have it at index 0 — no
  exceptions. Emit it after the references and the engine does not draw the
  terrain at all. Tamriel cell (-7,-32) had its LAND at index 150 behind 150
  REFRs and rendered as a hole in the world with its clutter still floating in
  place. **This is what "the land is simply missing and blank, placed refs
  still appear" means — look here first.** The defect masquerades as "a few
  isolated cells" because a cell with no references gets LAND at index 0 by
  accident and renders fine; only ref-heavy cells break, so it looks random.
  A record being PRESENT and byte-identical to the original proves nothing —
  (-7,-32)'s VHGT matched the source exactly while the cell rendered empty.
  Three sites must all agree: both builders in `import_main` (`temporary` is
  built LAND → REFR → ACHR, never the reverse) and the bucket sort in
  `emit_nested_overrides`. Guarded by `tests/test_land_group_order.py`.
- <a id="null-formid-records"></a>**A FormID of all zeros is NOT an identity —
  never deduplicate or cache on it.** Real plugins ship records with one:
  ElsweyrAnequina.esp has **7 LAND records whose FormID is literally
  0x00000000**, confirmed by reading the original Oblivion `.esp`, so it is
  the mod's own data and not an export defect (Oblivion.esm and Tamriel.esp
  have none). Oblivion never noticed because it reaches a cell's landscape
  through the cell's children group rather than by id. Two places in our
  import did:
  - `parse_export_directory` deduplicates by FormID, and all 7 collapsed onto
    the single key `'00000000'` — **6 of 7 silently discarded** before
    anything else ran. Key null ids by identity instead.
  - the LAND conversion cache is `{FormID: bytes}` and is *popped* by that
    key, so even the survivors would have fought over one entry.

  `_repair_null_land_formids` then takes ids from the top of the reserved gap
  above the plugin's highest real FormID, walking down in export order. These
  are the one positionally-assigned ids left in the converter (see
  [FormIDs are hashed](../../CLAUDE.md#formid-drift)); they are stable run to run
  and unique per cell, but inserting a null-id LAND renumbers the later ones. **Keep the parent's load-order index byte and vary only
  the low 24 bits.** These are export-space ids that `remap_formid` shifts
  later, and the index byte says which plugin owns the record: a first attempt
  OR'd in `0x0F000000` — picked to sit outside every plugin's index range so it
  "could not collide" — and that is exactly backwards. The records came out at
  index `0x10`, no such plugin is loaded, the engine could not resolve them,
  and the land was still missing in-game. Symptom throughout: blank, missing
  terrain with the cell's placed references still rendering, reported at cell
  (-7,-32). Guarded by `tests/test_null_formid_land.py`.
- <a id="unchanged-land-shadowing"></a>**An emitted children group must carry
  the master's LAND when the plugin ships none of its own.** A cell's child
  GRUP REPLACES the master's rather than merging, so overriding a single REFR
  in an otherwise-untouched cell DELETES the terrain under it. The land is
  dropped precisely *because* it is unchanged — `diff_records` correctly
  reports no authored difference once the VHGT pad is normalized — so nothing
  upstream knows the cell still needs it. Measured on ElsweyrAnequina.esp: 8
  cells emitted a type-9 group holding one REFR or ACHR and no LAND.
  `emit_nested_overrides` now pulls the master's LAND in via
  `MasterIndex.land(cell_fid)`, a structural lookup (a cell owns at most one
  LAND, and the type-6 GRUP label names the cell) — necessary because a LAND's
  own FormID is NOT recoverable by arithmetic: the master's conversion
  reallocates land ids, and **0 of 3,999 sampled Oblivion.esm source ids
  resolve to a real output LAND** by the load-order shift.
- <a id="master-index-routing"></a>**WITH TWO OR MORE TES4 MASTERS, A FORMID
  MUST BE ROUTED BY ITS INDEX BYTE — never by "which master file contains that
  integer".** Every converted master renumbers into its OWN FormID space, so
  two masters' id ranges overlap almost completely and a first-match scan
  silently answers from the wrong file. `ChainedMasterIndex` used to search
  `reversed(indices)` and take the first hit. TWMP Valenwood/Elsweyr (masters
  Oblivion.esm, Tamriel.esp, ElsweyrAnequina.esp) hit it exactly: `0202E438`
  is an exterior **CELL** in Tamriel.esp *and* a **WRLD**
  (`ANQVerkarthHillsWorld`) in ElsweyrAnequina.esp. ANQ was searched first, so
  every lookup for that Tamriel cell returned ANQ's worldspace record and
  group path. Measured damage in the shipped ESP: a **phantom worldspace**
  `0202E438` written into the file, **4,992 duplicate FormIDs** (4,552 REFR,
  237 CELL, 178 LAND, plus KEYM/FACT/CONT/LIGH/WEAP/SPEL/INGR/REGN/ACHR/PACK),
  and 4,734 of 7,489 exterior cells lost. **The game hung on the main menu with
  no crash and no log** — the engine builds its FormID table while parsing the
  plugin, before any cell loads, so the same id appearing twice with
  conflicting record types and group nesting deadlocks it there.
  - The index byte is exactly the information the old scan discarded: the
    child's TES5 master list puts the *k*-th TES4 master at slot
    `base_slot + k`, where `base_slot = len(masters) - tes4_master_count`.
  - A master's own records carry **its own** index (its MAST count), which is
    NOT the slot it occupies in the child — both files here used `02`
    internally while ANQ needed `03` in the child. `ChainedMasterIndex`
    translates both directions so callers never handle raw master-space ids.
  - **Single-master plugins cannot hit this**, which is why it stayed hidden;
    it appears the moment a plugin declares a second TES4 master.
  - Guarded by `tests/test_master_index_routing.py`.
  - **The same bug existed in FOUR places** — fixing one is not enough:
    1. `override_merge.ChainedMasterIndex._find` — first-match scan.
    2. `overrides.load_master_export` — merged every master's export on the raw
       id, so 119,443 of 121,505 shared ids resolved to the WRONG RECORD TYPE
       (59,770 LAND and 59,668 CELL clobbered). `0102DDE5` is a Tamriel LAND
       and ANQ's creature `ANQCORPantherCaged`, so the terrain override was
       diffed against a creature and the builder spliced that creature's FULL
       and DESC into the LAND (xEdit: "record LAND contains unexpected (or out
       of order) subrecord FULL").
    3. `master_manifest.MasterManifest.load` — merged manifests on the raw id.
       BOTH sides need re-keying: the KEYS are in the master's TES4 source
       space, `fid`/`companions` in its converted OUTPUT space.
    4. `ChainedMasterIndex.record` / `group_path` — returned the master's bytes
       VERBATIM, so the record's own FormID, its references and its GRUP labels
       still named the master's slot. 2,553 records were written at ids another
       master owns as a different type ("Record [CELL:0201C4B3] in Tamriel.esp
       is being overridden by record [REFR:0201C4B3]").
  - **A blanket +N shift is WRONG.** A master and its child usually share their
    low masters (Skyrim.esm 0, Oblivion.esm 1), so only the index bytes that
    actually move may be rewritten — remap per byte, matching the master's own
    masters BY NAME against the child's list. A uniform +1 turned every
    Oblivion.esm reference into a Tamriel.esp one.
- <a id="exterior-block-order"></a>**🔴 EXTERIOR BLOCKS ASCEND BY UNSIGNED
  (X, Y), X MAJOR — AND THE TWO PASSES MUST MERGE INTO ONE RUN.**
  (found 2026-08-12, confirmed in-game)

  The block/sub-block GRUP label packs the grid as `struct.pack('<hh', Y, X)`
  — **Y in the LOW word**. Sorting on the label's own word order therefore
  yields `(Y, X)`, the TRANSPOSE of what the engine wants, and that is what
  shipped for months.

  The engine walks a worldspace's type-4 block list to build its cell grid
  **while PARSING the file**, before any cell loads. A list where X descends
  and re-ascends never terminates: the game hangs on the main menu with **no
  crash and no log**, and **xEdit reports the file as completely clean**.

  Symptom trail on `TWMP_ValenwoodImproved.esp`, whose Tamriel blocks came out
  `(-1,0), (-2,-3), (-2,-2), (-1,-2), (-2,-1), (-1,-1)` — three ascending runs:
  - Deleting exterior blocks in xEdit made it load; the bisect was
    **cumulative**, because each deletion shortens the list until what remains
    happens to be monotonic. This is why no single block is "the" culprit and
    why hunting for a bad record inside them finds nothing.
  - **Resaving in xEdit did NOT fix it** — xEdit re-sorts on its own key and
    writes the same order back.

  Two separate defects had to be fixed:
  1. `import_main._grid_sort_key` / `overrides._group_sort_key` returned
     `(y, x)`. Both now return `(x, y)`.
  2. The override pass and the WRLD builder each emit a `GRUP World Children`
     for the same worldspace, **each internally sorted**. `_merge_owned_groups`
     folded them by concatenating bodies, producing one group with **two
     ascending runs** plus a duplicate type-4 group per block both passes
     touched (Tamriel.esp: 121 blocks in 15 runs, 14 duplicate labels;
     ElsweyrAnequina.esp: 8 in 2 runs, 3 duplicates). `writer._merge_grid_groups`
     now folds duplicate block/sub-block groups recursively and restores a
     single ascending run, leaving the persistent cell's type-6 group first.

  **Authority is the real `Skyrim.esm`**: all 168 blocks of worldspace
  `0000003C` are sorted by unsigned (X, Y) and by **no other** candidate key,
  as are every sub-block and all 37 worldspaces. **Never census our own
  converted output for this** — it carried the same bug, which is exactly how
  the transposed key was mistaken for vanilla's in the original docstring.

  Guarded by `tests/test_exterior_block_order.py` and
  `tests/test_merge_grid_groups.py`; `tools/validate/plugin_load_audit.py` check #10
  reports both `grid-groups-out-of-order` and `duplicate-grid-group`.

  **Ruled out along the way** (all measured clean — don't re-chase): duplicate
  FormIDs, dangling refs, NaN/Inf floats, cell grid-vs-block filing, owned-group
  anchoring, LAND-per-cell and LAND FormID-vs-master-cell agreement, NAVI/NVMI
  consistency (301/301), CELL override content (only 170 XCLW deltas, no flag
  changes), and `XESP -> 00000014` (a parent no file defines — a real
  conformance bug, but present in *working* plugins and absent from one of the
  failing blocks, so not this).
- <a id="reference-handle-cap"></a>**🔴 A NON-ESM PLUGIN MAKES EVERY REFERENCE
  PERSISTENT — AND THE ENGINE CAPS HANDLES AT 2²⁰ = 1,048,576.**
  (found 2026-08-12; the cause of the TWMP_Valenwood_Elsweyr main-menu hang)

  **This is a SECOND, independent cause of the no-crash/no-log main-menu hang**,
  and it is not a malformed record — every structural check passes and xEdit
  reports the file clean, exactly like the block-order bug above. The difference
  is that here *nothing is wrong with the file at all*: it is the load order as
  a whole that exceeds an engine limit.

  The rule: a plugin **not flagged ESM** has every reference it contains treated
  as **persistent — always active, regardless of where the player is**. Temporary
  refs, which an ESM would load on demand per cell, are permanently resident and
  each occupies one of the engine's 1,048,576 reference handles. The handle table
  is built while the files are parsed, before any cell loads, so overrunning it
  hangs at the main menu with **no crash and no Papyrus log** (Papyrus does not
  start until past the menu, so those logs cannot see this class of failure).

  Measured on the four plugins that reproduced it:

  | Plugin | REFR | ACHR |
  |---|---|---|
  | `TWMP_Valenwood_Elsweyr.esp` | 818,294 | 11 |
  | `ElsweyrAnequina.esp` | 161,541 | 1,306 |
  | `ElsweyrPelletine.esp` | 45,066 | 281 |
  | `Tamriel.esp` (terrain only) | 554 | 0 |
  | **Total** | **1,025,455** | **1,598** |

  **1,027,053 references = 97.9% of the cap from four plugins alone**, before
  `Skyrim.esm` contributes a single handle. Valenwood_Elsweyr alone is 80% of
  the total, which is why it is the plugin that appears to "refuse to load": it
  is simply the one that tips the sum over.

  **The fix is the ESM flag, and the flag must go on the plugin that HOLDS the
  references.** Flagging only its masters does nothing for it — the
  persistent-ref rule is per plugin, applied to that plugin's own references,
  and master-ness is not inherited by dependents. Conversely **every plugin in
  the dependency chain must be flagged together**, because a master must load
  before its dependents and an ESM may not master a plain ESP. Here the chain is
  `Tamriel` / `ElsweyrAnequina` → `TWMP_Valenwood_Elsweyr` → `ElsweyrPelletine`.

  **Filenames stay `.esp`.** The engine keys master-ness off the header flag,
  not the extension, while every dependent names its masters by exact filename
  in `MAST` — renaming `Tamriel.esp` → `Tamriel.esm` would invalidate the master
  list of all three plugins that depend on it. An ESM-flagged `.esp` is legal and
  loads as a master.

  Applied with `python tools/esm/make_master.py <chain, lowest first>`, which sets
  bit `0x00000001` at byte 8 of the TES4 header **in place** — 4 bytes rewritten,
  no record reserialized, so file size is unchanged and **no FormID drift**. It
  reads each `MAST` list and refuses with exit 2, naming the missing files and
  printing a correctly ordered fix command, rather than producing an invalid
  load order.

  **Caveat, unverified in-game:** refs are no longer force-persistent, so
  anything relying on a ref being reachable while its cell is unloaded — script
  targets on distant objects, quest aliases, cross-cell enable parents — may now
  fail where it previously worked by accident. Refs that genuinely must stay
  loaded need their persistent flag set explicitly.
- <a id="override-type-guard"></a>**AN OVERRIDE MUST RESOLVE TO A MASTER RECORD
  OF THE SAME TYPE.** A plugin's source id can convert to an id that already
  belongs to an unrelated record in the master's own space: ElsweyrAnequina's
  NPC_ `0100110C` converts to `0200110C`, a REFR in Oblivion.esm. Adopting that
  record's bytes and nesting shipped the NPC_ as a "REFR" inside a fabricated
  top-level group (xEdit: "File contains top level group without known sort
  order: GRUP Top 'REFR'") carrying a full NPC_ body. `OverrideContext.build`
  now compares the base record's signature against `TYPE_MAP`'s expected output
  signature (CREA→NPC_, CLOT→ARMO, ACRE→ACHR … are legal renames) and treats a
  mismatch as "no master record", so the caller converts it as a new record.
  Guarded by `tests/test_override_type_guard.py`.
- <a id="interleaved-subrecords"></a>**INTERLEAVED SUBRECORD FAMILIES MUST KEEP
  THEIR PAIRING.** `_apply_generic` replaces each signature as a unit at the
  position of its first occurrence — correct for a repeating single-signature
  run (CNAM, NAM1), fatal for a repeating STRUCT whose members each have their
  own signature. REGN Region Areas are `RPLI RPLD` repeated
  (`wbDefinitionsCommon.wbRegionAreas`), and the unit rule turned
  AnvilCoastline into `RPLI RPLD RPLD RPLD RPLD RPLI RPLI RPLI` (xEdit:
  "unexpected (or out of order) subrecord RPLD"). Such families are listed in
  `_INTERLEAVED_FAMILIES` and substituted one occurrence at a time, in place.
  Guarded by `tests/test_interleaved_subrecords.py`.
- <a id="achr-base-must-be-an-actor"></a>🛑 **AN ACHR'S BASE MUST BE AN NPC_,
  NEVER A LEVELLED LIST — THIS CRASHES THE GAME ON STARTUP.** A TES4 REFR that
  places an LVLC becomes `ACHR → shell NPC_ → LVLN` (see `leveled_actors`), and
  the shell is minted by the run that owns the LVLC. `build_leveled_actor_shells`
  only sees the plugin's OWN `by_type['LVLC']`, so when a DEPENDENT plugin places
  one of its master's leveled creatures it never mints or finds the shell: the
  generic override path re-converts the record standalone, resolves NAME to the
  raw LVLN, and substitutes that over the master's correct value. The engine
  loads the reference as a `Character*`, dereferences a null base and dies —
  `EXCEPTION_ACCESS_VIOLATION ... mov eax, [rax+0x108]` inside
  `BGSLoadFormBuffer`, ~40 s into startup, with the offending record named in
  the crash log's `RCX`. Fixed by registering every reachable LVLC (the
  plugin's AND its masters', `register_leveled_bases`) and having
  `generic_substitutions` drop NAME for such a REFR so the master's shell
  pointer survives. Measured on TWMP Valenwood/Elsweyr: `0301A56B` pointed at
  ANQ's LVLN `0306B333`; it now points at ANQ's shell NPC_ `030E118E`.
- <a id="one-owned-group"></a>**A WORLDSPACE/CELL/TOPIC MAY OWN ONLY ONE
  CHILDREN GROUP PER FILE.** Two passes append to the same top-level group —
  `emit_nested_overrides` writes a worldspace's children, then
  `_build_world_groups` writes the cells this plugin adds to it — so the file
  shipped two `GRUP World Children of 0100003C` in a row (xEdit: "Found
  additional GRUP World Children of ... Skipped Load: Merged N elements from
  duplicate group"). The engine indexes a worldspace's children once, so the
  second group's cells may never load. `writer._merge_owned_groups` folds
  repeated type-1/6/7 groups with the same label into the first at save time,
  which fixes it for every producer at once rather than at each call site.
  All three converted plugins were affected. Guarded by
  `tests/test_owned_group_merge.py`.
- <a id="anchor-once"></a>**A MASTER RECORD MAY BE ANCHORED ONLY ONCE.**
  `emit_nested_overrides` pulls an unchanged parent from the master to anchor
  its children group; `_build_world_groups` runs afterwards and did the same
  for the same worldspace, shipping the FormID twice (xEdit: "Skipped Load:
  Duplicate FormID [0100003C]" from ElsweyrAnequina, which both overrides
  Tamriel's WRLD and adds cells to it). The first pass records what it anchored
  in `ctx.anchored_wrld` and the second emits the children group alone.
- **LOD: a plugin can ship LOD ASSETS for a worldspace it does not DEFINE.**
  The GOTY `DLCShiveringIsles.esp` is an 85-byte header-only stub — every SI
  record was merged into `Oblivion.esm` — yet its BSA carries all of SEWorld's
  LOD tiles, so the export has `meshes\landscape\lod\40728.*` and no
  `WRLD.txt` at all. Two consequences, both in `phase_lod`:
  - `shipped_lod_worldspaces` must resolve the decimal-FormID prefix to an
    EditorID through the MASTERS' `WRLD.txt` as well as the plugin's own, or
    it falls back to a raw hex id (`00009F18`) that no downstream EDID match
    can resolve and every stage reports "worldspace not found".
  - The generators read WRLD/CELL/LAND out of ONE esm, so point them at the
    master's converted output when the plugin doesn't define the worldspace.
    Records move; **assets and generated output stay in the plugin's own dir**
    — writing them into the master's tree makes the master ship content that
    belongs to the plugin.
- **An override plugin must not re-bake its masters' LOD.** Pass the masters'
  output dirs as `generate_lod(master_dirs=...)`: any model whose `_far.nif`
  a master already ships is dropped from both billboard generation and the
  LODGen input. Without it, SI would regenerate all of Oblivion's object LOD
  to gain the ~370 models it actually introduces. Note this filters by MODEL,
  not by worldspace — SEWorld's terrain tiles are still generated in full,
  because the master's LOD run never covered SEWorld at all.
- **Only list meshes that exist under the single `PathData` root.** LODGen
  resolves every path in its input against that one directory and aborts with
  `file not found` / exit 404 — baking NO tiles — if one resolves only in
  another plugin's tree. A cross-tree search may decide whether a mesh needs
  GENERATING; it must never widen what gets LISTED.
- Verify with: zero non-text diffs vs the master (only FULL/NAM1/DESC/CNAM/NNAM
  should differ), zero dangling refs, zero records at undefined master ids, and
  every override nested exactly as the master nests it.

## Scripts: the masters' export is part of the identifier namespace
<a id="scripts-masters-export-part-identifier"></a>

An override plugin's own export holds **only the records it authors**. Every
EditorID it merely *references* — the master's GLOBs, quests, refs and SCPTs —
is absent, so anything that resolves names against a single export directory
silently fails to find them. Three places need the masters, and all three are
last-wins merges that must scan **masters FIRST** so the plugin's own version of
an overridden record wins:

- `CrossRefGraph.load_from_export()` walks `_HEADER.txt` `Master[n]=` entries
  transitively (`_export_dirs_with_masters`). Without it an unresolved name
  falls out of `_convert_ref` as a bare identifier with no property declared,
  and the compiler rejects it: `undefined identifier \`SetGewitter\``. This was
  **430 of Translation.esp's 981 scripts** — Nehrim owns every `Set*`/`Var*`
  global they read.
- `build_ref_as_int_map()` scans the masters' `SCPT.txt` alongside the plugin's,
  so a script reaching into a master's script variable can be typed. (Its
  INFO/QUST cross-access scan stays plugin-only — that one is a pure union of
  names that must become Properties, and the master's own fragments are emitted
  by the master's own run.)
- **`import_main`'s hand-built `CrossRefGraph` must mirror the CLI scan**, which
  is why it now folds in `ctx.master_export`. If the two graphs disagree the
  converter takes a different branch inside the import than it did when the
  `.psc` was written, and the VMAD ends up missing exactly the properties the
  compiled script reads.

Separately, `phase_compile` passes each master's
`output/<Master>/scripts/source` as an extra `-h` header directory. An override
plugin's scripts declare properties typed as the **master's** converted scripts
(`TES4_NQ16Script Property ...`) because the record they name carries that
master's SCRI; those `.psc` live in the master's output. Header-only — the
master's own run compiles and ships the `.pex`. This was the remaining 198
failures, all `undefined type`.

Net effect on Translation.esp: 551/981 compiled → **981/981**. FormIDs need no
special handling: `remap_formid` already gives an override its master's shifted
index, so `SetGewitter` binds to `01020A0F` (Nehrim's `00020A0F` at index 01).

## One TES4 field can feed TWO output subrecord runs
<a id="one-tes4-field-can-feed"></a>

`Stage[].Log[].Text` is the only journal text a TES4 quest has, and
`convert_QUST` writes it **twice**: once as the stage log entry (`CNAM`) and
once as the quest objective (`NNAM`, via `quest_objective_texts`). **Skyrim's
journal displays the OBJECTIVE**, so an override spec that maps the key to
`CNAM` alone produces a record whose visible text is still the master's — for a
translation plugin, quests that read German in-game while the export, the diff
and the CNAM run were all correctly English. 83 of Translation.esp's 84 quests.

The trap is that nothing looks wrong at any single checkpoint: the plugin's
export has the translation, `diff_records` reports `Stage[]`, the nested spec
fires, and `CNAM` is substituted correctly. Only the *second* derived run is
missing. When auditing an override spec, ask what ELSE the converter derives
from that field, not just where the field is copied.

`_DERIVED_INDEXED_SUBRECORD` covers this: it maps `(sig, key)` to an output
signature plus **the converter's own deriving function**, so the objective
sequence cannot drift from the one the master's record was built with. The
count must match the master's run exactly (verified: 84/84) or the per-
occurrence substitution misaligns.

Note a legitimately unchanged entry is not a bug — Nehrim's NQ09 is still
German in Translation.esp itself, so keeping the master's value there is
correct.

## A PGRD is never an override: it converts to a NEW NAVM
<a id="pgrd-never-override-converts-new"></a>

**Symptom: a plugin's landmass looks complete but nothing in it can path — no
navmesh anywhere the plugin edited one of the master's cells.**

The override rule is "take the master's converted record and substitute the
author's changed fields." That rule has no meaning for a pathgrid: a PGRD
converts to a **NAVM**, a brand-new record carrying its own freshly allocated
FormID. There is nothing of the master's to patch, so a PGRD is routed as a
NEW record ALWAYS — even when the plugin edits a pathgrid the master also
defines. `OverrideContext.build` returns `None` for `PGRD` before any other
check, and `_attach_new_records` nests the generated navmesh under the master's
cell in the type-9 temporary group, exactly like a new LAND.

PGRD used to sit in `OVERRIDE_UNMAPPABLE_TYPES` (with ROAD) and was counted as
"inexpressible" and dropped. Because the drop happened in the override pass,
the navmesh generator was never even asked for those cells:

| Plugin | NAVM before | NAVM after |
|---|---|---|
| ElsweyrAnequina.esp | 547 | 1,409 |
| ElsweyrPelletine.esp | 152 | 1,227 |
| TWMP_Valenwood_Elsweyr.esp | 0 | 40 |

Anequina exports 1,351 pathgrids; 863 of them edit Oblivion.esm's Tamriel
cells, and every one lost its navmesh. Coverage went from 487 to 1,349 of
1,351 cells. The two remaining are correctly declined by `convert_PGRD` (one
node with no PGRI link, and a 2-node grid that yields no triangles).

Two details that make this work and are easy to get wrong:

* **Do NOT restamp the NAVM to a master FormID.** A new LAND *is* restamped —
  a cell owns at most one LAND, so replacing the master's terrain must reuse
  its id or the engine sees two. A navmesh is the opposite: it is additional,
  the master has no counterpart to replace, and restamping would collide with
  the master's own navmesh for that cell.
* **Register the meta in `navm_metas`.** NAVI is a singleton index of every
  navmesh in the file; a NAVM that ships correctly but is missing from NAVI is
  invisible to the pathing engine. The group builders register their own cells,
  so anything nested into the master's hierarchy must register itself
  (`ctx.navm_metas`, set by `import_plugin` alongside `ctx.land_cache`).

The geometry itself is reused, not recomputed: `_navm_of` looks the cell up in
the `_precompute_navmeshes` cache by `(ParentCELL, PGRD)` — the same key
`_gather_navm_jobs` builds — so this costs no extra generation time.

ROAD remains genuinely unmappable: it converts to nothing at all.

## Deleting a master's record: the three shapes
<a id="deleting-masters-record-three-shapes"></a>

`overrides.make_deleted_record` restates a master record as a deletion. The
shape is **type-dependent**, measured 2026-08-25 across Update.esm,
Dawnguard.esm, HearthFires.esm and Dragonborn.esm (279 deleted records):

| Type | Body | Count |
|---|---|---|
| REFR / ACHR / ACRE | **NAME only** (dataSize 10) — the base object it placed | 193 |
| NAVM | **empty** (dataSize 0) | 68 |
| everything else | **the master's full body, unchanged** | 18 |

Only the Deleted flag (`0x20`) is added; a compressed record stays compressed.

**Zero-size is a NAVM-only shape.** Every zero-size deleted record in the four
vanilla masters is a NAVM, and every deleted NAVM is zero-size. Vanilla keeps
the complete body on every other type — measured: `NPC_ 0007932F` 220 bytes
(EDID VMAD OBND ACBS TPLT RNAM AIDT and all six PKIDs), `STAT 000BD6A5` 198,
`SPEL 0010E38C` 307, `IDLE 000FDC30` 210, `IDLE 000F6CBB` 192,
`SMQN 000F2199` 147, `EXPL 000F3A8C` 163, `INFO 000CEFBE` 20,
`STAT 0006CD7C` 153.

An earlier revision emptied every non-REFR body on the strength of a census
that did not hold. That shipped `PACK 01069B12` in Knights.esp as a 0-byte
deleted record — the only zero-size non-NAVM record in the whole output tree,
and a shape vanilla never writes. The engine reads a record's own fields while
unlinking it, so emptying the body throws away exactly what it needs.

## A quest-owned package must never be in an NPC's PKID list
<a id="quest-owned-package-must-never"></a>

**Symptom.** Infinite load at the MAIN MENU (not on a save). One thread pegged
at 100%, private memory perfectly flat, no CTD, no crash log, Papyrus log just
stops. Every structural audit reports the plugin CLEAN.

**xEdit names it outright** — always check there first for a rejected record:

```
NDAnvilListenProphetGogan8x4 [PACK:02002D7C]
error: package is owned by quest ND00 and cannot be assigned to an npc record
```

A quest-owned package reaches its actor through the quest's reference alias
(ALPC), never through the actor's own PKID run.

**Cause.** `packages.npc_packages` filters quest packages out of PKID on the
normal conversion path. The OVERRIDE path
(`override_builder._rebuild_packages`) instead *derived* its exclusion set by
comparing the master's export against the master's converted PKID run. That
covers packages the master already quest-owned, but a package **the plugin
itself newly quest-owns** was never in the master's export, cannot be derived,
and passed straight through.

Knights.esp shipped `PACK 02002D7C` (owned by its own quest ND00) in the PKID
list of `NPC_ 0103AF03` (Gogan), an override of an Oblivion.esm NPC.

**Fix.** `packages.is_quest_package()` exposes the live filter set that
`set_quest_packages` populates in phase 0g, before any override is built; both
paths consult it. Guarded by `tests/test_quest_package_not_in_pkid.py`.
Confirmed in-game 2026-08-26.

Gogan's illegal PKID was his only authored change, so with it filtered the
override became identical to the master and was dropped — he reverts to the
master's record and the package still reaches him through ND00's alias.

**Diagnostic note.** `tools/live/hang_capture.py` (dump first, analyse second)
identifies the wedged record: take two dumps minutes apart and compare the loop
variables. Identical values across both means wedged, not slow — that is what
pinned `0x02002D7C`. But the record's *identity* is only the start; xEdit's
error on that record is what names the rule being broken, and is far cheaper
than reverse-engineering the engine's resolution path.

## Cross-plugin FormID identity
<a id="cross-plugin-formid-identity"></a>

Cross-plugin FormID identity

A raw FormID is meaningful only INSIDE the file it came from. Its top byte is
an index into that file's OWN master list, so the same number names unrelated
records in different plugins: Morrowind_ob.esm and Tamriel.esp both use 02 for
THEMSELVES, and they collide on 4 CELL FormIDs. Merging overlays on the raw id
therefore invents overrides out of numeric coincidence — those 4 collisions
dragged 183 Morrowind INTERIOR objects into Cyrodiil's distant terrain, and
among the plugins that genuinely share TES4Tamriel another 1,278 references
collide between plugins that are not each other's masters.

The fix is the one the importer already applies when writing overrides (see
docs/commentary/tes5_import_override.md, "the same bug existed in FOUR places"): resolve
every id to (owning FILE, local id) by looking the index byte up in the file's
master list BY NAME, and merge on that pair instead.

## GRUP path caching
<a id="group-path-caching"></a>

`MasterIndex._scan` records the GRUP nesting of every record, so `group_path`
can reproduce a master's exact hierarchy in an override (a CELL written flat is
never indexed by the engine — see "group_path" above).

The nesting is a property of the GROUP, not the record, so building it per
record is pure waste: `output/Oblivion.esm` holds 1,185,504 records under
74,297 GRUPs, i.e. ~16 records share every path tuple. `GroupStack.path()`
therefore memoises on the stack and drops the cache in `_walk`'s push/pop,
which is the only place the stack changes.

`Record` carries its own `size` for the same reason: `_scan` needs each
record's total byte extent, and re-reading the size field with a second
`struct.unpack_from` duplicates what `read_record` already did.

### The walk is flat and inline
<a id="the-walk-is-flat-and-inline"></a>

`_walk` is one iterative generator that unpacks both headers with a
module-level `struct.Struct` and never calls a per-record helper. It looks
less tidy than a recursive walk delegating to `read_record`/`read_group`, and
that shape was measured and rejected.

Measured on `output/Oblivion.esm` (1,185,504 records), walking with no body
reads:

| | time |
|---|---:|
| raw inline loop, no object per record | 0.23s |
| + a `Record` dataclass per record — **the floor for this API** | 0.36s |
| + `__slots__` instead of a dataclass | 0.35s |
| recursive generator calling `read_record`/`read_group` | 0.72s |
| **flat inline generator (shipped)** | **0.46s** |

So the object costs 0.13s and is worth it, `__slots__` buys nothing over a
dataclass, and the recursive-with-helpers shape cost **0.37s of pure
overhead** — more than the parsing and the object combined. Two causes, both
per record: a recursive generator's `yield from` chain re-enters once per
enclosing GRUP (**7.6M frames for 1.19M records**), and each record paid two
Python-level calls whose bodies are three lines of `unpack_from`.

The shipped walk is 0.10s over the floor, so there is no further win here
worth chasing: a caller still slower than the old hand-rolled loop it replaced
is spending the difference in ITS OWN loop, not in the reader.

`read_record` and `read_group` remain as the public single-record entry
points; they are simply not on the hot path any more.

Two related measurements, so they are not re-derived:

- **One combined `struct.Struct` beats three `unpack_from` calls by 2.3x**
  (0.10s vs 0.23s over 1.19M headers). `_REC` is `<4s4IHH` — note the FOUR
  u32s: `vcs1` is a full u32 at offset 16, and `form_version` is the u16 at
  offset 20. Getting that wrong reads vcs1 as the form version and every
  record reports version 0.
- **`GroupStack` memoises `path()` and `of_type()`**, dropped in `_walk` at
  every push and pop. Both answer questions about the GRUP chain, which
  changes only at a group boundary, so an uncached lookup rescans the stack
  per record: `of_type` alone was called 2.37M times (worldspace + topic, plus
  `cell` scanning again) for 1.19M records.
- **`cell` scans the stack ONCE inward, it does not call `of_type` per type.**
  Asking `of_type` for each of the four cell-children types looks tidier and
  reuses the cache, but it costs four calls per record — 4.3M over a 717k
  record plugin, 0.81s of a 1.58s parse. The single reversed scan is 0.4s
  cheaper and returns the same group.

### The signatures you ask for drive everything
<a id="the-signatures-drive-everything"></a>

`walk(raw, *sigs)` takes WHAT you want, not how to get it. An earlier shape
had three independent knobs — `sigs=` to filter, `bodies=` to control
decompression, `prune=` to skip subtrees — and every one of them was added
after a call site regressed. They encode the same fact three times and can
silently disagree: filtering to CELL while still decompressing all 1,185,504
bodies cost **4.00s against the old reader's 2.13s**, an 88% regression that
looked like the shared reader being slow when it was really the caller holding
two knobs out of step.

With one input the walk derives all three:

- **which records yield** — the signature test,
- **whose body is decompressed** — only a record that will be yielded, so an
  unwanted record never pays for `zlib`,
- **which top-level GRUPs are entered** — a top-level GRUP's label IS a record
  signature, so a search for `DOBJ` skips every other block outright without
  reading a single record header inside it.

The last one is why `top_group()` is gone: scoping a search to one block was a
separate call the caller had to remember, and is now just what asking for a
signature means. `WRLD`, `CELL` and `DIAL` are exempt from the skip because
their blocks nest records of other types (a REFR lives under WRLD, an INFO
under DIAL).

`bodies=` is the one genuinely separate axis — "I need to SEE every record but
not all their contents" — because it cannot be derived from a signature list.

**Filtering OUTSIDE the walk is the mistake this shape exists to prevent.**
`PluginWriter._collect_overridden_temporary` first walked everything and
tested `rec.sig not in ONAM_SIGNATURES` in Python: **0.58s against the old
hand-rolled walk's 0.31s**. Naming those signatures instead — `walk(blob,
*ONAM_SIGNATURES, bodies=())` — took it to **0.34s** for a byte-identical
648,592-FormID result, because the walk then skips the non-matching records
without building a `Record` for each. Prefer `stack.of_type()` over
`has_type()` in the same loop: `of_type` memoises per stack, `has_type`
rescans it per record.

### Benchmarking this scan: build ONE index per process
<a id="benchmark-one-index-per-process"></a>

**A second 1.19M-entry index built in the same process pays for the first
one's heap, and the effect swamps the thing being measured.** Timing HEAD's
scan and the shared reader's back-to-back showed a 3.7x "regression" that did
not exist; running HEAD's own scan twice in one process measured **2.03s then
9.75s for identical work**. Whichever implementation runs second loses.

Two wrong diagnoses came out of that polluted comparison before the cause was
found — a callback walk instead of the generator (measured 1.5% faster, not
worth a second public API, and was reverted) and the cost of `stack.path()`
(removing it made the run *slower*). cProfile misleads the same way here: it
inflates per-call cost, so a reader with one call per record looks far worse
under it than it is.

Measured one-per-process on `output/Oblivion.esm`, `MasterIndex` costs
**2.61s against the old hand-rolled scan's 2.13s** (+23%). The traversal
itself is **0.46s** against a 0.36s floor, so the reader is not where that
goes: it is the caller's own per-record work, and it buys a scan that reads
XXXX payloads correctly and cannot leak a parent id.

## The nested-override emission passes
<a id="nested-override-emission-passes"></a>

`emit_nested_overrides` runs five ordered passes over `by_path`, split into
`_sort_land_first`, `_build_emitted_at` and `_emit_paths`. **The split is
mechanical: every pass keeps the iteration order, sort key and dict insertion
order it had, because those decide emission order and FormID allocation.**

- **Bucketing.** Records with no known nesting are counted as orphans and
  skipped rather than emitted flat, where the engine would never index them.
  `by_path`'s insertion order comes from the caller's record order and feeds
  the top-level `sorted({p[0] for p in by_path})` walk.
- **`_sort_land_first`.** LAND leads every type-9 group — see
  [LAND must be first](#land-first-in-type-9) for the 15,564/15,564 census and
  the Tamriel (-7,-32) symptom. The sort is **stable**: only the LAND moves,
  every other body keeps its relative order.
- **`_build_emitted_at`.** A record already being emitted at a path serves as
  its own anchor, so it is never pulled from the master a second time. The same
  pass backfills the master's LAND into a type-9 group this plugin emits
  without one — measured on ElsweyrAnequina.esp, **8 cells** emitted a type-9
  group holding one REFR or ACHR and no LAND and rendered as blank ground with
  the references still floating. The backfill `insert(0, ...)`s so the LAND
  still leads. Full reasoning: [unchanged LAND shadowing](#unchanged-land-shadowing).
- **`_emit_paths`.** The anchoring walk. A record's own children group must
  directly FOLLOW that record (`CELL, GRUP(6, cell), CELL, GRUP(6, cell)`), so
  the records and their owned groups are interleaved rather than emitted in two
  runs. An owned group whose owner this plugin does not override gets that
  owner's bytes pulled from the master immediately before the group; each such
  FormID is recorded in `anchored_fids` so the WRLD builder, which runs
  afterwards, cannot anchor it again — see [anchor once](#anchor-once).
  ElsweyrAnequina wrote Tamriel's WRLD twice for exactly that reason (xEdit:
  "Skipped Load: Duplicate FormID [0100003C]").

# tes5_import/pipeline*.py — phase ordering and the finalize pass

**Code:** `tes5_import/pipeline.py`, `pipeline_records.py`, `pipeline_finalize.py`

The importer runs as ordered phases over one shared `ImportState`. The split is
by job: `pipeline.py` drives and builds the pre-scan indexes, `pipeline_records.py`
converts and groups records (phases 1-4), and `pipeline_finalize.py` emits phase 5,
the side files and the write.

The layer rules and the decision procedure are in
[tes5_import_architecture.md](../reference/tes5_import_architecture.md).

## Contents

- [Phase 0 — speak-as topics need a voiced stand-in](#speak-as-needs-a-voiced-standin)
- [Phase 0 — reserved ids and the artifact preflight](#reserved-ids-and-preflight)
- [Phase 0 — what each pre-scan must precede](#phase-0-ordering-constraints)
- [Phase 0 — a dependent plugin does not re-create support records](#phase-0-dependent-skips-support-records)
- [Phase 0 — the voice map reads the MASTERS' races and actors](#phase-0-voice-map-reads-masters)
- [Phase 0 — chargen menus sit at fixed ids in the reserved gap](#phase-0-chargen-menu-ids)
- [Phase 0 — a master's id must be keyed on its export KEY](#phase-0-master-key-not-formid)
- [Phase 0 — the cross-ref graph mirrors the CLI scan](#phase-0-xref-mirrors-cli-scan)
- [Phase 0 — magic effects, and what must exist before items convert](#phase-0-magic-effect-prerequisites)
- [Phase 0 — a stale bounds cache reads as all-zeroes](#phase-0-stale-bounds-cache)
- [A cache bump strands the MASTERS, not the plugin](#stale-master-asset-caches)
- [Phase 0 — hunt chains and script-forced packages](#phase-0-hunt-chains-and-script-packages)
- [Phase 0 — hair lengths and race skin tones index the masters](#phase-0-hair-and-skin-index-masters)
- [Phase 0c — combat music is reachable only through DOBJ BTMS](#phase-0c-dobj-btms)
- [Phase 1 is serial on purpose](#phase-1-is-serial-on-purpose)
- [A master-dependent plugin may own an entire world](#master-dependent-plugin-owns-a-world)
- [Phase 3c — LCTN, and why a pure patch plugin skips it](#phase-3c-locations)
- [Phase 4 — the three navmesh post-passes, and their order](#phase-4-navmesh-post-passes)
- [The CELL and WRLD group builders](#cell-world-group-builders)
- [Phase 5 — a master's dialogue is overridden, not re-derived](#phase-5-dialogue-overrides)
- [`build_dialog_groups` reads far more than DIAL/INFO](#build-dialog-groups-reads-more)
- [Why the patch pass runs after the records are written](#patch-pass-runs-last)
- [Sound-slot records carry TES4 SOUN ids until patched](#sound-slot-patching)
- [The manifest names the PLUGIN, not the export folder](#manifest-names-the-plugin)
- [The ESM flag comes from the source header, not the extension](#the-esm-flag-comes-from-the-source-header)

## <a id="speak-as-needs-a-voiced-standin"></a>Phase 0 — speak-as topics need gates dropped AND a voiced stand-in

TES4's `Say <topic> <flag> <speak-as NPC> <flag>` names the identity a line
belongs to, separately from the reference that emits it. **Skyrim's `Say` has no
such argument.** Inside those topics the speaker is the emitting marker, so every
actor-shaped gate on the line — `GetIsID`, the injected `GetIsVoiceType`, an
inherited `GetIsPlayableRace` — is unsatisfiable: no INFO is selected and the
line plays SILENT while the caller's timers run on. The Arena announcer said
nothing yet the gates still opened.

Topics are registered **by TOPIC, never by NPC**: a real actor can lend his
identity to a marker-spoken line without his own dialogue being affected.

**Dropping the gates is only half of it.** It lets the INFO be selected; it does
not give the engine a voice folder to read audio from, because voice lookup is
keyed on the SPEAKER's voice type and an XMarker STAT has none. That is why the
announcer showed subtitles with no sound, and why they never timed out — no
audio means no duration. The fix mints the vanilla answer: a **TACT carrying the
speak-as NPC's VTYP, placed at the emitter's own position**. It must run BEFORE
the CELL/WRLD builders, which read `by_type['REFR']`.

Each speaker is registered under the SAME property name `script_convert` emits
for that call site, so `TES4Voice_<emitter>_<voice>.Say(topic)` binds. Those refs
are synthesized, so they are absent from the TES4 export and
`resolve_property_formid()` cannot find them — the well-known registry is exactly
the channel for that.

## <a id="reserved-ids-and-preflight"></a>Phase 0 — reserved ids and the artifact preflight

**Stale stage artifacts fail NOW, not 15 phase-0 steps in.** Their consumers
(creature projects, the music manifest) sit deep in the run, so without the
preflight the user waits through the master load, fid maps, cross-ref graph and
script plans before being told to re-run a stage.

**The synthesized NPC-conversation head topics** (`converter.CONV_FAKE_FID_BASE`)
take ids from a FIXED high base rather than from `derive_formid`, so nothing else
knows about them. Without reserving that span, the header is written below them
and a derived id can hash straight onto one.

**AddTopic unlock globals** are created before the QUST pass so quest VMADs can
bind them as properties: gated topics get `GetGlobalValue` conditions and one
GLOB each, which revealer INFO/stage fragments set.

## <a id="phase-0-ordering-constraints"></a>Phase 0 — what each pre-scan must precede

The phase-0 steps look independent but several carry a real ordering
constraint. Each of these must run **before** the phase named:

| Step | Builds | Must precede | Why |
|---|---|---|---|
| 0d mesh bounds | `mesh_bounds_cache.json` | every OBND | The cache normally comes from `convert.py`'s mesh-bounds phase, which runs after mesh+speedtree conversion so it includes speedtree NIFs. If it is missing — e.g. running the import step standalone — it is scanned here from the already-converted output meshes, so callers never need a separate step. It lives in `export/<plugin>/`. |
| 0f objective text | the short NNAM table | QUST | Skyrim's objective HUD shows a SHORT imperative line, not TES4's long journal entry. The curated table ships with the repo rather than being derived from the export, so this is a plain load. |
| 0g package plan | alias indices | **both** QUST and PACK | A quest-owned package must hang off a QUST reference alias (ALPC) to outrank the actor's standing schedule, so the alias indices must be decided before either record is written — both read this one plan. |
| 0h leveled actors | shell NPC_ per placed LVLC | the CELL/WRLD builders | Skyrim only spawns actors from ACHR→NPC_, so each placed LVLC becomes an ACHR aimed at a generated shell NPC_ whose TPLT points at the LVLN. Needs the generated creature races from 0f, and the builders read `by_type['REFR']`/`['ACHR']`. |
| 0i item index | record type + biped slot per item | Phase 1 actors | A TES4 actor equips out of one mixed CNTO inventory; TES5 needs wearables moved to a DOFT outfit and the rest left in CNTO. **A dependent plugin dresses its actors out of its MASTER's wardrobe**, so the master's item records must be in the index too, or every master-owned wearable classifies as non-wearable and the actor gets no outfit at all. |

Phase 0h indexes **every LVLC reachable from here — this plugin's and its
masters'** — because a dependent plugin can place a master's leveled creature.

**0c MUSC before Phase 1.** Regions convert in Phase 1, *before* WRLD, so each
worldspace's authored SNAM is indexed beforehand: `convert_REGN` needs it to
tell an authored music type from the CS's unset default (see its RDMD note).

**Chargen-identity conditions** are routed through the choice globals the
converted menus write: `GetIsPlayerBirthsign` (224) and `GetPCIsClass` (129)
become `GetGlobalValue == index+1`. See
[tes5_import_conditions.md](tes5_import_conditions.md#chargen-identity-to-menu-globals).

## <a id="phase-0-dependent-skips-support-records"></a>Phase 0 — a dependent plugin does not re-create support records

VTYPs, the TES4 globals/factions, the vendor/trainer factions and the locations
are SUPPORT records the master's conversion already created. Re-creating them in
a dependent plugin duplicates master content — **27 spurious VTYP, 35 GLOB, 27
FACT** — and the duplicates then compete with the originals the overrides use.
So creation is gated on `not ctx`, and a dependent plugin ADOPTS the master's
FormIDs into the well-known registry instead (`_adopt_master_special_records`).

Its own converted scripts still reference those records by name (Morroblivion's
chargen writes `TES4ControlsDisabled`), and an unbound property is None, which
aborts the whole Papyrus function on first use.

**The plugin-origin marker faction is ROOT MASTERS ONLY** (no TES4 masters of
their own): every actor the file defines joins it, and dialogue naming no
plugin-scoped audience is gated on it, so two converted plugins loaded together
(Oblivion.esm + Nehrim.esm) cannot trade guard/crime/directions/rumour lines. A
plugin WITH masters deliberately skips it — it must stay free to extend and
override its master's dialogue the way an Oblivion DLC does, and its actors are
already members through the master records it inherits.

## <a id="phase-0-voice-map-reads-masters"></a>Phase 0 — the voice map reads the MASTERS' races and actors

The VNAM voice-race routing lives on the RACE records, and a dependent plugin's
actors overwhelmingly use its MASTERS' races — **all 3,607 of Morroblivion's
do**. Without them the mapper falls back to the literal race and hands Jiub
`TES4MaleDarkElf`, a folder that does not exist, because Oblivion routes DarkElf
males to HighElf. The master's RACEs are fed in so the routing resolves to the
folder the recordings really live in; the plugin's own RACE records still win
(applied second).

The masters' ACTORS go in as well: this plugin writes dialogue for its master's
NPCs, and a speaker with no VTYP falls back to a default voice.

The map is registered so `convert_NPC_`/`convert_CREA` stamp the SAME
VNAM-resolved voice on VTCK — an actor whose VTCK disagrees with the dialogue
pass's `GetIsVoiceType` gates never plays its lines.

## <a id="phase-0-chargen-menu-ids"></a>Phase 0 — chargen menus sit at fixed ids in the reserved gap

`ShowBirthsignMenu` / `ShowClassMenu` become modal Message pages under the same
shared-plan contract as the button menus. Their records live at FIXED ids in the
reserved FormID gap (`writer.chargen_fid_base`) because the page/button block
must be contiguous and ordered.

## <a id="phase-0-master-key-not-formid"></a>Phase 0 — a master's id must be keyed on its export KEY

🛑 **Key a master's record on its `master_export` KEY, not on `rec['FormID']`.**
The record was parsed from the master's OWN export, so its `FormID` field is in
THAT file's index space; `load_master_export` re-keys the dict into this
plugin's space precisely because the two disagree whenever a master has a
different master count than we do. Everything downstream feeds these ids to
`get_formid`, which applies OUR blanket +offset — valid only for an id already
in our space.

**Measured (2026-08-12, ElsweyrPelletine.esp):** FACT `ANQCORCorintheFaction` is
`010247E2` in ElsweyrAnequina.esp (1 master, so `01` is Anequina itself) and
re-keyed to `020247E2` for Pelletine (4 masters, `02` = Anequina). Keying on the
raw field registered `010247E2`; +1 made `020247E2` in TES5 space, which is
**Tamriel.esp** — a LAND record. The VM bound the Faction property to that LAND,
so `GetCrimeGoldViolent()` returned None and
`TES4_PELCornitheRepDetectScript`'s `OnUpdate` aborted every 0.5s forever —
**864 stack frames in one 3-minute session**. No `cannot be bound` line is
logged for this: the id resolves to a REAL record, just the wrong one.

**Masters FIRST, then the plugin's own records**, so an OVERRIDDEN record
reports the overriding EditorID. `fid_to_edid` and the cross-ref graph must
agree on this precedence or the importer resolves a different property set than
the `.psc` was generated against.

An override plugin's SCROs point at its MASTERS' records as freely as at its own
(Morroblivion's chargen stage 1 calls `stopquest tutorials`, a quest that exists
only in Oblivion.esm). A master-owned FormID missing from the map resolves to no
EditorID, so `_collect_scro_properties` drops it and the VMAD binds NOTHING for
that property — the `.psc` still declares it, so it is None at runtime and the
FIRST call aborts the whole function. That killed the Morroblivion intro: stage
1's opening `tutorials.Stop()` threw, so the fragment never reached
`JiubSpeak = 1` and the prison-ship sequence never started while the player sat
with controls disabled.

## <a id="phase-0-xref-mirrors-cli-scan"></a>Phase 0 — the cross-ref graph mirrors the CLI scan

INFO result scripts reference factions/quests/etc. as bare EditorID tokens.
`ScriptConverter` needs `xref.edid_to_formid` + `xref.record_type` to classify
them and register them as property refs; without it properties like
`FightersGuild` stay unbound (None at runtime).

An OVERRIDE plugin's own export holds only the records it authors, so the
EditorIDs it merely REFERENCES (a master's GLOBs, quests and refs) are absent.
The CLI scan (`load_from_export`) walks the masters' exports for exactly this
reason, and **this graph is hand-built rather than loaded**, so anything the CLI
scan collects must be mirrored here or the converter takes a DIFFERENT branch
inside the import than it did when the `.psc` was written — and the VMAD ends up
missing exactly the properties the compiled script reads. `AIPackage` lists and
`PKDT.Type` back the reconstruction of TES4's `GetCurrentAIPackage == <type>`
(`cross_ref.pack_type`).

**A master record's id FIELDS must be re-keyed too**, not just the outer key.
The graph chains them: `record_scri[fid] -> script_formid_to_edid[scri]`,
`record_base[fid] -> the base's own entry`. Re-keying only the outer key would
leave those chains pointing at ids no key in the graph uses, so every
master-owned SCRI/base lookup would miss. `unique_placed_ref` is a live example —
it feeds `raw_hex` straight into `remap_formid`, so a master's actor would
rebind to whatever record our offset lands on. The correction applied is the
index-byte shift the record's own key already carries.

## <a id="phase-0-magic-effect-prerequisites"></a>Phase 0 — magic effects, and what must exist before items convert

MGEF is a converted record type, so every SPEL/ENCH/ALCH/INGR/SGST effect points
at an effect of OUR OWN rather than at a vanilla Skyrim lookalike. Three things
must exist before those records convert:

- **{effect code -> our MGEF FormID}**, so an effect can find its record and a
  counter effect (ESCE) can resolve a 4-char code to a FormID;
- **the AssocItem index** — Skyrim's Summon Creature takes an NPC_ and Bound
  Weapon a WEAP/ARMO, so the converter must know what each TES4 AssocItem
  FormID actually points at (and resolve an LVLC summon to a concrete creature,
  which the archetype cannot reference);
- **the per-actor-value and per-script MGEF variants**, because Oblivion
  parameterises one effect record by data the ITEM carries (Damage Attribute's
  attribute, a script effect's script) while Skyrim keeps both on the MGEF.

A DEPENDENT plugin usually defines no MGEF at all — **Morrowind_ob.esm has none,
yet 109 of its items carry script effects** — so the effect table comes from the
MASTER's export the same way the wardrobe index does (`outfits.load_item_index`).
Without it every effect on every item in such a plugin resolves to nothing and
the item converts to filler.

**An enchanted TES4 BOOK is a scroll**, and Skyrim's SCRL carries its effects
directly rather than through an enchantment link, so `convert_BOOK` reads the
ENCH its ENAM names. The masters' enchantments are indexed too: a dependent
plugin's scrolls usually name one of theirs.

## <a id="phase-0-stale-bounds-cache"></a>Phase 0 — a stale bounds cache reads as all-zeroes

One scan produces BOTH the mesh-bounds and collision caches: the two analyses
share a NIF parse costing far more than either of them (`scan_mesh_data`). It
runs when EITHER cache is missing, since a single pass fills both anyway.

**A bounds cache that EXISTS but predates the current entry schema counts as
missing.** Entries are plain lists, so a cache written before a field was added
parses cleanly and reads as all-zeroes for that field — which is how Nehrim
served flag-less entries for every mesh long after the HELD bit shipped, leaving
breakaway planks and traps unreleased.

**Being current includes being READABLE, not just carrying the right magic.**
`collision_cache_is_current` originally compared only the 8 magic bytes. A local
`collision_cache.bin` written at a superseded header layout — magic, then a
*second* u32 before the entry table, so entries begin at offset 16 rather than
12 — still matched `TESCOL04` and passed the gate, then failed in `_deserialize`
on entry 0 (`'utf-8' codec can't decode byte 0xdc`). `load_collision` catches
that and returns 0, so `get_collision` answered `None` for all 5,951 meshes and
every cell voxelized an empty world. Measured on Oblivion.esm: the blob decodes
cleanly from offset 16 and consumes all 68,464,197 bytes exactly, confirming the
extra header field rather than corruption.

The gate therefore walks the entry table's lengths and requires it to consume the
blob EXACTLY (`_entry_table_is_intact`); reading lengths only, it decodes no
floats. A cache that cannot be read is rescanned instead of silently half-read.
The unit tests build their fixtures through `_serialize`, so a writer/reader pair
that is self-consistent but disagrees with an on-disk file passes all of them —
which is exactly how this shipped.

**`TESCOL07` / schema 4.** Bumped when TREE bases began keying to their
converted SpeedTree NIF (`<ns>/speedtrees/<name>.nif`) instead of the
unresolvable `<name>.spt.nif`. Which meshes a cell carves changed, so every
cache written before the bump is regenerated rather than trusted — the magic
and `COLLISION_SCHEMA_VERSION` move together, and the old blob is
byte-compatible, so nothing but the version distinguishes them.

## <a id="stale-master-asset-caches"></a>A cache bump strands the MASTERS, not the plugin

**Code:** `pipeline._refresh_master_mesh_caches`

The rescan gate above only ever looked at the plugin's OWN caches, but
`load_collision` is handed a masters-first CHAIN and skips an unreadable entry
per-path rather than failing. So a format bump splits a load: re-run the
plugin and its own cache is rewritten at the new magic, while every master not
re-run since stays at the old one and is silently dropped.

Measured after `TESCOL06 -> TESCOL07`, on `TR_Mainland.esm` (masters
`Morrowind_ob.esm`, the Morroblivion compat patch, `Tamriel_Data.esm`):

```
Collision: could not load cache (bad collision cache magic)   x3
Collision: loaded 95 entries from 1 cache(s)
```

95 meshes instead of 28,349 — the three rejects held 3,954 and 24,300 keys.
`WrldMorrowind (-17,-51)` places 104 refs of which **103 are master-owned**, so
every one resolved to no collision, nothing carved, and the cell came out as an
unbroken terrain sheet. The symptom reads as "the navmesh ignores all collision
objects", which points at the carver; the cause is one rejected file three
directories away.

The masters' meshes are already converted under `output/<master>/meshes`, so
the fix rescans them in place before the chain loads rather than failing with a
list of manual re-runs. The plugin's own rescan and the masters' share
`_rescan_mesh_caches`, so the staleness test cannot drift between them.
See: [tes5_import_navmesh.md](tes5_import_navmesh.md#speedtree-model-keys).

FURN MNAM/FNPR must index the converted NIF's clustered seat positions, and
REFRs of re-origined furniture models need z compensation (shared algorithm in
`asset_convert/nif/furniture_markers.py`).

## <a id="producer-emitted-mesh-entries"></a>Phase 0 — the mesh scan is a second parse of what the mesh stage just wrote

Measured on Oblivion.esm's 10,628 converted NIFs, per mesh:

| | ms/mesh | share |
|---|---|---|
| NIF parse | 176.8 | **96%** |
| bounds + physics flags | 2.2 | 1% |
| collision extract | 6.1 | 3% |

~1,343 s serial, ~112 s at 12 workers — **0.57x the entire mesh stage**, which
measures 324 ms/mesh. Nearly all of it is re-reading files the mesh stage parsed
moments earlier.

**The converted graph already holds everything the scan wants.** `_convert_collision`
builds a real `bhkCompressedMeshShape` via `build_cms_collision`, and
`hoist_collision` has already moved it to the root — the exact shape
`collision_from_data` decodes. So a producer computes the same entries for
**+8.3 ms on a parse already paid**.

**The hook must sit AFTER `_run_post_passes`.** Hooking before them matched a
re-parse on only 24 of 25 sampled meshes: `convert_flame_nodes` re-converts flame
sub-NIFs and mutates the tree, so `middlebowlredcandles01.nif` reported bounds
`(-1,-1,-1,1,2,0)` and no collision against the written file's
`(-13,-13,-8,13,13,8)` and 216/216 triangles. Hooking at the write point gives
**25/25 exact** on bounds, collision and physics flags.

Producers emit per-process JSONL fragments (`mesh_scan_fragments`) rather than
returning soups through the pool pickle, because collision soups are large and
most producers' workers return only small tuples today. The scan merges the
fragments and parses **only** the meshes no producer claimed, so it stays correct
whichever stages ran — `--import-only` on an older tree still scans everything.

Three in-scope writes never hold a parsed graph (the already-Skyrim `shutil.copy2`,
the grass `landscape/grass` copy, the creature merge-failure copy); copies alias the
source key, and anything left over falls to the backfill. `_write_weight_variants`
can also *delete* the base file, so a fragment can carry a removal.

**A pool worker must write each record as it is produced — buffering loses them.**
`mp.Pool` TERMINATES its children rather than letting them exit, so an `atexit`
hook registered in the pool initializer mostly never runs: measured, a 4-worker
pool over 8 tasks fired the hook in **1** process, and a full `--meshes-only` over
10,627 meshes produced **0** fragment files. (With `maxtasksperchild` the recycled
generations do flush — 7 files — but the final generation is still terminated.)

Draining via extra pool tasks does not work either: workers are not round-robined,
so 64 drain tasks over an 8-worker pool reached only **3** of them, and calling
`pool.map` while the `imap_unordered` generator was still open DEADLOCKED the run.

So each process appends to its own `w_<pid>.jsonl` and flushes per record.
Measured cost 0.17 ms/mesh against 324 ms of conversion (0.05%), and nothing is
lost when the pool kills the worker. After the rebuild: **29 fragment files,
9,421 entries**, leaving the scan **1,207 of 10,628 meshes to parse — 11.4% of
the previous work.** The remainder are the `actors/` creature meshes
`batch_convert` skips, which the `os.walk` backfill still covers.

## <a id="phase-0-hunt-chains-and-script-packages"></a>Phase 0 — hunt chains and script-forced packages

The MASTERS' packages, quests, actors and placements are indexed: a dependent
plugin's actors mostly run THEIR MASTER'S packages, and an unresolved
package/actor/quest silently drops the actor back to its standing Sandbox
schedule (`PackagePlan.build`).

`AddScriptPackage` forces a package onto an actor that does not list it, and
Skyrim has no equivalent call — so those packages must reach the actor's quest
alias as ALPCs or arbitration can never pick them. Quest packages live on a QUST
alias (ALPC), not in the actor's PKID list.

**A Find at an actor BASE with several placements** ("hunt any FGC06Goblin")
becomes a CHAIN of Follow packages, one per placed target, that runs ahead of the
source package in every list carrying it — the alias ALPC list (`PackagePlan`)
and the actor's own PKID list. The seek ids are derived from authored ids
(source PACK + target ref), so adding or reordering targets never moves an
existing id.

Everything the converter asks about targets/locations (ref and base kinds,
placements, interior cells, where each package's runners stand) is built in
`pack_indexes` so `tools/pack_audit.py` measures the SAME routing this import
performs. Master records go in first.

## <a id="phase-0-hair-and-skin-index-masters"></a>Phase 0 — hair lengths and race skin tones index the masters

**Hair.** Skyrim has no per-NPC hair length, so `NPC_.LNAM` is resolved by baking
the hair `.tri`'s HairMorph into a mesh per distinct length and giving each its
own HDPT (`asset_convert.character.hair_pipeline`). `convert_HAIR` needs to know
which lengths to emit before it runs in Phase 1, and `NPC_` needs it to point
PNAM at the right variant.

**Skin.** Skyrim colors body skin from a per-NPC "Skin Tone" tint layer, and
Oblivion authors that color in the RACE record: its body/face part textures plus
its own FGTS vector, which recolors a SHARED texture for the races that do not
ship their own (High Elf gold, Redguard brown, Nord pale all share
`Characters\Imperial\HeadHuman.dds`).

Master exports are indexed for BOTH — a dependent plugin's actors wear their
MASTER's hair and use their MASTER's races, so a plugin-only scan would register
no lengths and silently give every actor the bucket-0 mesh regardless of its
authored length, and would leave every one of them on the skin fallback.

## <a id="phase-0c-dobj-btms"></a>Phase 0c — combat music is reachable only through DOBJ BTMS

**0c MUSC must precede Phase 1:** `convert_REGN` reads the enum->MUSC table to
emit RDMO, and `convert_CELL`/`convert_WRLD` later read it for XCMO/ZNAM. The
manifest is normally written by the SOUND stage, which runs AFTER this one, so
`load_music_manifest` scans the extracted music folder itself when the file is
not there yet. `scan_music` resolves `<export-root>/<plugin>/music` itself, so it
needs the export ROOT — `export_dir` in the driver is already the per-plugin
subdir.

The engine reaches combat music **ONLY** through DOBJ's BTMS default object
(hardcoded to vanilla `MUSCombat`), so a Battle MUSC that nothing points at can
never play. The master's DOBJ is overridden, copying every other entry unchanged
— the same full-array override each official DLC ships.

## <a id="phase-1-is-serial-on-purpose"></a>Phase 1 is serial on purpose

🛑 **Do not "optimize" the Phase 1 loop into a thread pool.** The whole phase is
~1.5s of GIL-bound Python, so a pool adds no speed — but it **did** make the
output nondeterministic: converters that emit companion records (ARMA,
aimed-MGEF clones, …) added them in thread-completion order, so record order
shuffled between runs. A plain loop keeps the ESM byte-reproducible. The
genuinely heavy work — text parse, LAND, navmeshes — runs in pools elsewhere.

**An OVERRIDE is the master's converted record with only the author's changes
substituted.** Nothing is re-derived, so nothing can drift: this is what stops
**1,821 NPC_ races** being rewritten to vanilla Skyrim ones when the author
changed none. A `reconvert` result (an authored effect-list change) falls
through to the normal path, and its FormID still lands on the master's.

**A converter may retarget PER RECORD, not just per signature.** A TES4 BOOK
carrying an enchantment is a scroll, and Skyrim's BOOK has no field for an object
effect, so `convert_BOOK` emits a SCRL for those and a BOOK for the rest. The
packed record's own 4-byte signature is the authority on which group it belongs
in — **filing a SCRL under the BOOK group makes the engine read it as a BOOK and
the scroll silently reverts to unusable paper.** The same rule covers a master's
STAT retyped to MSTT.

A converter returning None chose to emit nothing for that record:
`convert_REGN` skips regions with no weather list, because their
object/grass/sound data drives TES4-side generators with no equivalent here.

**Volume and falloff live on the SNDR companion, not on the SOUN**, so an
authored attenuation change must override the MASTER's SNDR as well or the sound
keeps the master's loudness.

## <a id="master-dependent-plugin-owns-a-world"></a>A master-dependent plugin may own an entire world

Morroblivion declares Oblivion.esm as a master purely to reference its records,
yet **all 387,813 of its CELL/REFR/LAND/PGRD records are NEW** — they override
nothing. The override path cannot express them, since there is no master record
to patch, so they were being dropped and **the output shipped with no game world
at all**. They are built exactly as a master-less plugin's would be.

The same applies to navmeshes: a PGRD that edits one of the master's cells still
produces a brand-new NAVM, nested under the master's cell by the override pass
rather than by the group builders. It needs the precomputed geometry (keyed by
`(cell_fid, pgrd_fid)`) and somewhere to register its meta so the NAVI singleton
lists it — **a navmesh missing from NAVI is invisible to the pathing engine** even
when the NAVM record itself ships correctly.

**A world children group's label must be the FormID of the WRLD record that
precedes it.** For an ANCHORED worldspace that is the record read out of the
master — or, when the anchor bytes are empty, the override already emitted at
that FormID — never this plugin's source id.

**`XTEL.Door` is tested first** when scanning for teleport pairs: it is absent on
all but ~0.5% of a master's records, so it rejects the 1.6M-record scan far more
cheaply than the signature does — measured on the Chargen mod's masters,
**0.28s vs 0.73s** for the same 8,087 rows.

## <a id="phase-3c-locations"></a>Phase 3c — LCTN, and why a pure patch plugin skips it

Skyrim only reveals a map marker when the player discovers the **Location** it
belongs to, and it reads an exterior cell's displayed name off that same
Location. Oblivion has neither, so LCTNs are built here — before the cells are
written, since every cell needs an XLCN pointing at one.

**A pure PATCH plugin skips this.** The master already built a LCTN for every
marker and cell, and its CELL records already point at them; building our own
would duplicate all **265** and leave the overrides' inherited XLCN pointing at
the master's anyway.

**But a master-dependent plugin that ships its OWN worldspace** (Morroblivion)
has cells the master never saw. Those need their own LCTNs or every exterior
cell is unnamed and its map markers never become discoverable — so locations are
built whenever this plugin owns any new CELL/WRLD.

Ownership compares the **RAW (unshifted) index byte**: `get_formid()` would
apply the load-order offset and put every plugin-owned record one index too high.

## <a id="phase-4-navmesh-post-passes"></a>Phase 4 — the three navmesh post-passes, and their order

The order of these is load-bearing; each note records a real shipped bug.

**1. Edge links** stitch adjacent exterior cell navmeshes together. Without
them every cell mesh is an island and **no actor can path across a cell
boundary**, so any AI package with an out-of-cell destination starts — the actor
stands up — and never moves. Must run after every mesh exists (it needs
neighbour NAVM FormIDs and final triangle indices) and before the group builders
serialise them.

**2. Interior splits** break a multi-component interior mesh into one NAVM per
component. The engine **only joins navmeshes through a door when the two sides
are DIFFERENT NAVM records** — vanilla: all 5 same-cell teleport-door pairs with
XNDP live on two meshes. A door pair inside one mesh is never a portal, so the
CharacterGen assassins could not leave their holding room however correct
XNDP/NVNM/NVMI were. Must run after edge links (final triangle indices) and
before XNDP collection, which it moves doors onto the component meshes for.

The split is gated by a `door REFR -> XTEL target` map, so it only runs on cells
that need it. **The MASTERS' doors are indexed too**, exactly as
`_build_teleport_grid` does: an override plugin re-places a master's cell while
the teleport pair inside it stays master-owned, so a `by_type`-only scan sees no
pair, skips the split, and the CharacterGen bug returns for precisely the plugins
that inherit their interiors. Measured on *Morrowind_ob - Chargen and Transport
Mod.esp*: **8,087 master REFRs carry XTEL** that this dict could not otherwise
see. 🛑 Key a master's record on its **`master_export` KEY, not `rec['FormID']`** —
the key is already in THIS plugin's index space while the record's own field is
in the master's, and `get_formid` applies our blanket offset, valid only for the
former.

**3. XNDP** is the REFR side of every navmesh door link. NVNM (door triangles)
and NAVI (NVMI door links) both point navmesh → door; **XNDP is the only thing
that points door → navmesh triangle**, and that is the direction the engine needs
to build a PathingDoor. Without it a teleport door is not a pathing node at all:
an actor whose package destination lies on the far side has no route and never
leaves the room, though the package, the alias and the conditions are all
correct. This is CharacterGen's Ambush A — the assassins' holding room connects
to the ambush floor through the teleport-door pair `0004F795`/`0004F7A2`, and
with no XNDP the four `CGAssassinsAmbushA1-A4` Travel packages had a destination
they could not path to. Must run after every navmesh exists and before the group
builders convert any REFR.

**Debug hook:** `TESCONV_DUMP_NAVM_CACHE=<path>` pickles the precomputed cache so
the edge-link and split post-passes can be profiled in isolation without paying
for a full import each iteration.

**LAND is converted up front** — it is the heaviest per-record converter. Land
extents must be registered BEFORE the override pass, and the plugin's own records
go LAST so an override wins the FormID.

## <a id="cell-world-group-builders"></a>The CELL and WRLD group builders

### Anchoring is decided PER WORLDSPACE

A plugin can add a whole landscape to a worldspace **the master owns**, in which
case it ships NO new WRLD of its own for it — its only WRLD record is an
override, already emitted by the override path — while `cells` holds tens of
thousands of brand-new exterior cells.

Returning early dropped every one of them. **Tamriel.esp**, a heightmap plugin
over Oblivion.esm's Tamriel, exports **99,946 CELL and 99,914 LAND** records, of
which only the **4,501** that override the master's survived: **95% of the
terrain silently vanished** and the output was 38 MB instead of ~1 GB. The map
looked unchanged in-game for exactly that reason.

Gating the block on `not worlds` was right for Tamriel.esp (a pure heightmap with
zero own worldspaces) but broke every plugin that does **both** — adds land to
the master's Tamriel *and* ships its own worldspaces. So anchoring is decided per
worldspace, never by whether the plugin happens to own any.

### <a id="cell-straddling-refs-not-persistent"></a>A cell-edge-straddling ref is NOT force-persisted

The CK warning "Ref … should be persistent but is not" was read as a rule and
applied to **74,467 refs**. The vanilla census refutes it outright: Skyrim.esm
ships **67,751 cell-edge-straddling STAT/TREE refs and only 842 (1.2%) are
persistent** — *lower* than the 2.3% among refs that straddle nothing.

Forcing the flag made objects **vanish in game while the CK kept showing them**:
`ICMarketBlock03House01` (forced persistent) was invisible where its neighbour
`ICMarketBlock03House02` (untouched) rendered — same cell, same mesh family. See
[ck_vs_game_missing_objects.md](ck_vs_game_missing_objects.md).

### Misplaced exterior refs are re-homed

Oblivion.esm ships refs attached to a grid cell that does not match their
coordinates — the TES4 CS tolerated it; **11 in Tamriel**. The Skyrim CK does
not: on load it warns "Reference attached to wrong cell for its location" for
each, relocates them, **and hangs there**. Each such ref is attached to the cell
its position actually falls in.

Refs in a worldspace's persistent (dummy) cell keep their cell — the engine loads
those by worldspace, not by grid.

🛑 **A ref being individually flagged Persistent is NOT a reason to skip this.**
It still lives in a normal grid-tiled cell, just in that cell's Persistent child
group instead of Temporary, and the CK checks its placement identically. This was
gated on `not persistent`, which stayed harmless only while almost nothing was
persistent.

### <a id="exterior-cell-with-no-xclc"></a>A non-persistent cell with no XCLC is a real cell at (0,0)

The persistent worldspace cell has `RecordFlags & 0x400` and sits directly under
the WRLD type-1 group with no block/sub-block wrapping. It often has
`XCLC=(0,0)`, so an empty-string check for it is wrong, and some worldspaces have
several (IC districts, Oblivion planes).

A **non-persistent** cell with no XCLC is a genuine exterior cell at grid (0,0)
whose coords Oblivion simply omitted, 0 being the default for an absent
subrecord. **30 such cells exist** — OblivionMQKvatchBridge (60 refs),
MQ14OblivionGate (34), CheydinhalOblivion (19), DABoethiaStatue (21), every IC
district. Verified: **100% of each one's refs floor to grid (0,0)**
(`floor(pos / 4096)`), so the coordinate is not a guess. They must stay in the
block tree **with** an XCLC written for them; removing them instead punched a
hole in the world.

### Serialisation details

- **LAND FIRST** in a cell's temporary children group — vanilla puts the
  landscape at index 0. See
  [project_land_first_in_type9_group](tes5_import_navmesh.md).
- Group contents accumulate in **lists joined at each wrap point** rather than
  `bytes +=`; Tamriel's children total enough bytes that repeated concatenation
  dominates the phase.
- A CELL's child GRUP **replaces** the master's — it is not merged.

## <a id="phase-5-dialogue-overrides"></a>Phase 5 — a master's dialogue is overridden, not re-derived

Same reasoning as CELL: a DIAL's child GRUP of INFOs **replaces** the master's.
Re-running the dialogue pipeline over a master's topics would also re-key them,
regenerate DLBR/DLVW branches the master already has, and re-decide which of the
71 skipped topics to drop. A translation plugin only rewrites response text, so
the override pass emits flat overrides carrying exactly that.

A plugin with a master can still add **whole topics of its own** — Morroblivion
brings 4,035 new DIALs and their INFOs. Those have no master DIAL to override, so
the real dialogue pipeline runs over them rather than discarding them.

## <a id="build-dialog-groups-reads-more"></a>`build_dialog_groups` reads far more than DIAL/INFO

Every one of these reads **silently no-ops on an empty list** rather than
failing, so the own-records dict must be back-filled from the plugin's full
record set:

| Signature | What it supplies | Failure when missing |
|---|---|---|
| `QUST` | quest-level CTDAs, which the engine has no equivalent for and which MUST be copied onto every INFO the quest owns | `fbmwChargen` carries `GetIsPlayableRace`, so Morroblivion's whole chargen conversation converted with that gate **missing** — 1 such condition in the output vs **12,858** in Oblivion.esm |
| `SCPT`, `QUST`, `INFO` | the Say/SayTo/StartConversation call sites `build_say_topic_dispositions` needs, to decide how a script-driven topic's RunOn=Target conditions convert | "say-driven topics: 0" in the build log |
| `ACHR`, `ACRE`, `REFR` | the refs those call sites name | targets unresolved |

Everything EXCEPT the DIAL/INFO the override pass already emitted is passed
through.

## <a id="patch-pass-runs-last"></a>Why the patch pass runs after the records are written

Several bindings can only be made once every record exists, so they patch the
already-written bytes instead of reordering phases:

- **ForceGreet topics (PDTO).** A ForceGreet package names the topic it opens,
  but the per-quest GREETING topics only exist in Phase 5 — long after PACK was
  written in Phase 3b2. PACK must stay after QUST for its alias indices, so the
  placeholder is patched rather than the phases reordered.
- **A player-base script's quest.** A TES4 script on the PLAYER BASE record
  needs a start-game quest with a PlayerRef alias to run from. It is built here,
  not inside the dialogue pass: it is independent of DIAL/INFO, and
  `build_dialog_groups` returns early for a plugin with no dialogue at all.
  (See [script_convert.md](script_convert.md#player-base-script-needs-quest-alias).)
- **Creature voice types, footsteps and body part data** are allocated LAST so
  no other generated FormID moves, then patched into the already-written actors,
  ARMAs and races. Footsteps hang an IPCT/IPDS/FSTP/FSTS chain off the body
  ARMA's SNDD, which is where Skyrim reads locomotion audio — not the actor and
  not animationdata; CSDT slots 0-3 were being dropped, so no converted creature
  ever made a footstep. The creature BPTD carries THIS skeleton's ragdoll part
  nodes: the vanilla-canine GNAM matched no bones, so corpses could not be
  havok-grabbed and fell through the floor.
- **The lava surface mesh** is generated here rather than in the asset stage so
  `--import-only` alone produces a working result — the REFRs written above name
  it, and a placed reference whose model is missing renders as nothing at all.

## <a id="sound-slot-patching"></a>Sound-slot records carry TES4 SOUN ids until patched

ACTI/CONT/DOOR/LIGH hold TES4 SOUN ids because Phase 1 runs before the sound
descriptors exist. TES5 wants the SNDR there, and **a SOUN id left in one of
these slots crashes the audio thread** (`items._SNDR_SLOTS`). Weather is patched
separately: its SNAM is an 8-byte struct, not a bare FormID.

A **master-owned** TES4 SOUN id resolves through the master's converted SOUN,
whose SDSC names the companion — read back rather than re-derived, which would
mint an id in THIS plugin's index space. This is required, not optional:
Morrowind_ob's containers and torches point at Oblivion.esm sounds it never
overrides, so the master manifest carries no entry for them and every one of
those **~2,400 slots** would otherwise be left wrong-typed.

## <a id="manifest-names-the-plugin"></a>The manifest names the PLUGIN, not the export folder

The companion manifest records which generated records belong to which source
record, so a plugin that overrides them can reuse ours instead of minting
duplicates. It is written for every conversion — any file can be a master to
something.

Its name comes from **the file being written**, not from the export folder. A
single-plugin imported mod keeps its records in the mod's folder
(`export/Black Marsh/`), so `basename(export_dir)` yielded the MOD label, and the
GUI then offered a plugin called "Black Marsh" that does not exist and cannot be
re-run.

Derived-FormID collisions are expected to be a fraction of a percent; a spike
means the derived region is filling up or a key has gone non-unique, both worth
seeing before shipping.

## <a id="the-esm-flag-comes-from-the-source-header"></a>The ESM flag comes from the source header, not the extension

`convert.py` `phase_import` sets `is_esm` from bit 0 of the `Flags=` line in the
export's `_HEADER.txt` (`core/plugin_masters.py:is_master_export`), never from
the file name. It used to test `.endswith('.esm')`, which cleared the flag on
`Morrowind-Morroblivion-Compatibility.esp` whenever the patch went through a
normal `-f` import. `build_patch` sets the flag itself, so the patch was a
master only when that function was the last thing to write it. An ESM-flagged
`.esp` is legal and loads as a master (see `tools/esm/make_master.py`).

Each exporter writes the real flag:

- TES4/FO3/FNV: `export_header` copies the TES4 record flags.
- Morrowind: `write_header` takes the HEDR `type` field (0 = esp, 1 = esm;
  OpenMW `loadtes3.hpp`). Across the 32 plugins in a real Morrowind Data Files
  folder, every `.esm` is 1 and every `.esp` is 0. Before this change the
  exporter always wrote `Flags=1`, so any Morrowind `.esp` exported earlier must
  be re-exported to lose the flag.
- The compatibility patch always writes `Flags=1`.

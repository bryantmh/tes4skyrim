# asset_convert/sources/bsa_pack.py — BSA packing

**Code:** `asset_convert/sources/bsa_pack.py`, `asset_convert/sources/bsa_write.py`,
`asset_convert/sources/bsa_extract.py` (`tes4_hash`, `bsa_has`), `asset_convert/sources/output_files.py`

## Contents

- [Our own archive writer](#bsa-writer)
- [Repacking only what changed](#incremental-repack)
- [What a pack writes](#pack-layout)
- [Why textures need no archive of their own](#no-textures-archive)
- [One archive set per imported mod](#one-archive-set-per-mod)
- [Reading past the Windows path limit](#staging-past-the-path-limit)
- [Finding one path by its hash](#bsa-name-hash)
- [Parallel extraction](#parallel-extraction)
- [Archive-only output](#archive-only-output)
- [Script sources in the archives](#archived-script-sources)
- [Repacking over deleted loose files](#repacking-over-deleted-loose-files)

## <a id="bsa-writer"></a>Our own archive writer

**Code:** `asset_convert/sources/bsa_write.py`

Archives are written by our own Skyrim SE (v105) writer; BSArch is no longer
called. Layout (cross-checked against xEdit `wbBSArchive.pas` and vanilla
`Skyrim - Misc.bsa` / `Skyrim - Textures0.bsa`): a 36-byte header; 24-byte
folder records (hash, count, 0, offset) sorted by `tes4_hash` of the folder;
per folder a bstring name plus 16-byte file records sorted by file-name hash; the
file-name block; then data. A folder record's offset is the position of its
name bstring plus the total file-name length, as vanilla stores it. Folder and
file name lengths in the header count each name plus its terminator. The
game binary-searches these tables, so the hash order is required; vanilla's two
checked archives have 0 unsorted folders and 0 folder-hash mismatches against
`tes4_hash`.

**What is compressed follows vanilla.** Census of the SSE Data folder's
Bethesda archives (header flags, and each file's effective compression):

| archive | archive flags | file flags | compressed |
|---|---|---|---|
| Meshes0/1 | 0x87 | 0x1 | all (`meshes\`, `lodsettings\`) |
| Animations | 0x07 | 0x5201FF | all (`meshes\`) |
| Textures0-8 | 0x107 (embedded names) | 0x2 | all |
| Sounds (`sound\`, `music\`) | 0x13 | 0x18 | none (.wav, .xwm) |
| Voices_en0 | 0x03 | 0x18 | none (.fuz) |
| Misc (`scripts\`, `seq\`) | 0x13 | 0x0 | none |
| Interface (`interface\`, `strings\`) | 0x03 | 0x0 | none |
| Creation Club (ccBGSSSE001 etc.) | 0x83 | 0x0 | none, even meshes/textures |

So files under `meshes\`, `textures\` and `lodsettings\` are LZ4-compressed and
everything else (sound, voice, music, scripts, seq, interface) is stored raw.
Vanilla never mixes the two in one archive, but ours must (meshes, textures
and sound share `<stem>.bsa`), so it sets the archive's compressed flag and
marks each raw file with the size bit 0x40000000 that inverts it. This is what
xEdit/BSArch's `-z` does (`DoNotCompress` keeps sound, voice, music and strings
raw, though it does compress `.fuz`; vanilla doesn't, and we follow vanilla).
An archive that holds only textures copies vanilla's embedded names (flag
0x100, each payload prefixed by its full path as a bstring), as xEdit does for
a textures-only archive; a mixed one has none. Other flags follow xEdit's `DetectFlags`: 0x80 where meshes are present,
0x10 (retain file names) for sounds or scripts; file flags 0x1 meshes, 0x2
textures, 0x8 sounds, 0x10 voices, 0x40 trees.

**Payload.** A compressed file is a u32 original size and then an LZ4 frame
shaped like vanilla's: independent blocks, no checksums, no stored content
size, and the smallest block size that holds the file (FLG 0x60, BD 0x40-0x70,
as in vanilla's frames). Vanilla's payloads match LZ4 HC levels 9-12 when
recompressed. We use HC level 3 (`LZ4_LEVEL`), measured on 400 Nehrim NIFs and
200 DDS:

| level | NIF ratio | NIF MB/s/core | DDS ratio | DDS MB/s/core |
|---|---|---|---|---|
| 0 (fast) | 0.676 | 1225 | 0.680 | 958 |
| 3 | 0.624 | 106 | 0.606 | 114 |
| 6 | 0.622 | 87 | 0.600 | 78-83 |
| 9 | 0.622 | 64 | 0.598 | 54-58 |

Level 3 is within 1% of level 9's size at about twice the speed.

**In game** (2026-11-24, headless instance, Tribunal with every loose Tribunal file
unlinked and its two archives in Data): Mournhold renders with its meshes and
textures, and the 95 `ex_mh` meshes exist only in the archive. The quest case
`0110_ms_adulterer` ran Tribunal's `MORROWIND_tarenScript.pex` from the archive
(raw, behind the 0x40000000 bit) to journal 20; its later failure at 110 is
identical with loose files. Sound playback from the archive was not checked.

**Parallel.** Compression runs in a process pool of `worker_count()`: tasks are
lists of source paths, each worker reads its files and writes the payloads into
its own chunk file under `<output>/_bsa_work/`, returning only (offset, length)
pairs. Tasks are sized to give each worker about four (1-32 MB raw each); under
32 MB of new work no pool is started. Then each archive is written in one
sequential pass that copies payloads from chunk files, old archives and raw
sources. Raw sources are read ahead on `worker_count()` threads: opening
Nehrim's 11,648 sound/music/script files one at a time took 57 s of an 86 s
pack; reading ahead brought the whole pack to 31 s. Since payload sizes are known
before writing, archives are split at their exact final size.

## <a id="incremental-repack"></a>Repacking only what changed

**Code:** `_pack`, `_write_plan` in `bsa_pack.py`; `reuse_index` in `bsa_write.py`

`output/<mod>/.bsa_pack_manifest.json` records each source file's [size,
mtime_ns], and for each archive its [size, mtime_ns] on disk and a digest of
its planned contents (path, signature and compressed flag for every file, plus
`SCHEME`). On the next pack:

- An old archive counts as a source only when it is still exactly as recorded
  (size and mtime match). An archive another tool wrote or rewrote is ignored.
- A compressed file whose size and mtime match the manifest is copied from the
  old archive's stored bytes (embedded name skipped), not compressed again. Raw
  files are always copied from their source, since that costs the same.
- A file with no loose copy left is kept from the old archives; see
  [Repacking over deleted loose files](#repacking-over-deleted-loose-files).
- An archive whose new digest equals the recorded one is kept untouched.
- Every new archive goes to `<name>.tmp`; once all are written, each replaces
  its old one with `os.replace`. A failure leaves the old set and manifest
  intact, and the next pack sweeps the stray `.tmp`.

Change detection uses size plus mtime, not a content hash: a hash means
reading every source on every pack (Nehrim: 7.7 GB) just to find the one file
that changed, while stat costs nothing. The cost of a false "changed" (a file
rewritten with identical bytes) is recompressing that one file. Bump `SCHEME`
whenever the payload format or level changes, so no older archive is reused.

Because bins fill in path order, a change near the start of a full archive
moves files across the boundary into Part N. Those archives are rewritten, but
only by copying stored bytes.

## <a id="pack-layout"></a>What a pack writes

**Code:** `pack_bsas` and its helpers in `asset_convert/sources/bsa_pack.py`

- **Where.** The output folder is resolved from the export ROOT
  (`plugin_out_root`), never a plugin's record dir. A record dir holds no
  `sources.json`, so the registry reads as empty and the folder resolves to
  the pre-group `output/<plugin>/`, which does not exist for an imported mod.
  The pack then aborted with "output directory not found".
- **Only outputs that ship a plugin.** A folder is packed only when it holds
  `<stem>.esm`, `.esp` or `.esl` (`ships_plugin`). Everything else stays loose,
  by the user's decision: the LOD mod (`AutoConvertLOD`), asset-only mods that
  `--import-mod` registers as pseudo-plugins, the UI reskin. The pack logs one
  `SKIP` line and sweeps any archives an older build left there.
- **Which archives.** Every top-level folder except `SKSE/` and `Video/`
  (which stay loose: SKSE never sees an archived file, nor does BinkOpen) goes
  into one path-sorted list, split at the size limit: `<stem>.bsa`, then
  `<stem> - Part 2.bsa`, `Part 3`, ... Textures are not separated: they land
  wherever the split puts them (path order puts them near the end, after
  meshes, scripts and sound). `<stem> - Textures.bsa` is no longer written; see
  [why textures need no archive of their own](#no-textures-archive).
- **Overflow.** Each archive is at most 2 GiB (`BSA_SIZE_LIMIT`), its exact
  size. File-data offsets are 32-bit, and 2 GiB is the limit the old
  documentation and the community both work to; no source showing the game
  reading past it was found, so it was kept. Skyrim mounts only `<stem>.bsa`
  (and `<stem> - Textures.bsa`, which we no longer write) by itself; the Part
  archives are mounted by `ArchiveLoader.dll` in the runtime package, so any
  plugin over one archive needs it. The dummy `<stem>_loader[_N].esl` plugins
  earlier builds wrote for this are gone.
- **Flags of a mixed archive.** Flags follow the content of each archive
  (`archive_flags`): names are embedded (0x100) only when an archive holds
  nothing but textures, as xEdit's `DetectFlags` does; xEdit warns that
  embedded names on other files, or on uncompressed files, crash SSE. `split`
  counts each texture's embedded-name bytes, so an all-texture Part archive
  still fits the limit (a mixed one ends a little under it).
- **Migrating an older output.** `LEGACY_SUFFIXES` (`Textures`) are read as
  payload sources like the stem's own archives, then swept as stale once the
  new set is written. Measured on the user's outputs (one pack each, no
  compression needed): Morrowind.esm 2 archives to `Morrowind.bsa` (1,423 MB),
  17,348 payloads reused, 2.9 s; Tribunal.esm to `Tribunal.bsa` (114 MB), 4.3 s;
  Nehrim.esm 4 archives to 3 (`Nehrim.bsa` kept byte-unchanged, `Part 2`
  2,047 MB, `Part 3` 265 MB), 31,041 payloads reused, 4.9 s (21.8 s with the
  loose drop of 42,454 files); FalloutNV.esm 5 to 4 (`FalloutNV.bsa` kept,
  Parts 2-4 2,048 + 2,046 + 721 MB), 35,516 reused, 28.0 s (85 s with the drop of
  129,874 files).

### <a id="no-textures-archive"></a>Why textures need no archive of their own

The engine's per-plugin mount (`MountPluginArchives`, [tes_runtime_archives.md](tes_runtime_archives.md#the-engine-mount))
loads `<stem>.bsa` and `<stem> - Textures.bsa` by name only; nothing in it checks content. The one texture
rule found that depends on a name is the cubemap test: xEdit says a texture loads as a cubemap when its
*embedded* name contains `_e.dd`, so archived cubemaps need embedded names. In 1.6.1170 the only reader of
the string `_e.dd` (RVA 0x19cfe20) is a `strstr` wrapper at 0xe11180, called from three places in the
texture manager's load functions (0xe0e406, 0xe0e5c6, 0xe0e7e6). Each passes the *requested* path, built
from the caller's name plus the `textures\` prefix, before any archive lookup, so the cubemap flag comes
from the path asked for, not from the archive. Vanilla's Creation Club archives (flags 0x83) also mix meshes
and textures without embedded names. In game (2026-11-24, headless instance 2, Tribunal packed with a
85,000,000-byte limit into `Tribunal.bsa` and `Tribunal - Part 2.bsa`; Part 2 held all 495 textures plus
388 voice files, flags 0x7, no embedded names): ArchiveLoader logged `mounted Tribunal - Part 2.bsa`, and
the Royal Guard helm (`a_helsethguard_helm.nif`, whose two diffuse textures exist only in Part 2) rendered
textured in Mournhold, Godsreach; with Part 2 removed from Data it rendered white. Not checked in game: a
cubemap (`_e.dds`) read from a mixed archive.
- **Stale sweep.** After a pack, anything an older or larger run left is
  deleted: `<stem>_loader*` ESL/BSA pairs and `oblivion_loader*` from the
  loader era, Part archives past the ones written, a group's archive that now
  has no content, `*.bsa.tmp`, and BSArch's `_bsa_staging_*` folders. The stem is
  `glob.escape`d because a plugin name may hold `[` or `?`.
- **Measured** (32 logical cores, 29 workers; warm disk cache unless noted).
  BSArch is 0.4.x from `external/bsarch`, run with `-sse -mt -z` on the same
  files, staged as hardlinks the way the old packer did:

  | plugin | raw input | archives | full pack | no change | 1 changed + 1 added + 1 removed | BSArch `-z` |
  |---|---|---|---|---|---|---|
  | Tribunal.esm | 3,375 files, 0.18 GB | 2 (114 MB) | 1.2-2.1 s (12.8 s cold cache) | 0.06 s | 0.22 s | 2.5 s (+1.5 s staging), 117 MB |
  | Nehrim.esm | 42,454 files, 7.57 GB | 4 (4.57 GB) | 31.4 s (75 s cold, before read-ahead) | 1.9 s | 4.2 s | 35 s (+20 s staging), 2 archives, 4.55 GB |
  | FalloutNV.esm | 129,874 files, 9.52 GB | 5 (7.20 GB) | 46.7 s | 2.5 s | 5.5 s | 56 s (+45 s staging), 6.92 GB; its 4.5 GB main archive is past the 32-bit limit |

  An update costs the copy of the archives it touches, not compression: Nehrim's
  4.2 s rewrites the 2 GB main archive. BSArch's archives are smaller for
  FalloutNV because it compresses `.fuz` voice files, which vanilla stores raw.
  Every Nehrim file read back byte-identical through `iter_bsa` and through
  BSArch's own `unpack` (42,454 of 42,454, 0 missing, 0 extra, 0 differing).

## <a id="one-archive-set-per-mod"></a>One archive set per imported mod

**Code:** `archive_stem` in `asset_convert/sources/bsa_pack.py`

An imported mod's plugins share one `output/<Mod>/` folder, and a pack reads the
whole folder, so each plugin's pack held the same assets. The archives were
named for the plugin being packed. Morrowind_ob.esp therefore wrote
`Morrowind_ob.bsa` / `Morrowind_ob - Textures.bsa` over the ESM's (same stem).
Plugins with different stems got a second, duplicate set that Skyrim mounts
twice. The archives are now named by following the packed plugin's masters
while they belong to the same mod, up to the member that masters no other
member. Every dependent packs the same set under that member's name, which is
always loaded when they are. Archives and `_loader` plugins named for a
dependent's own stem are deleted, since older builds wrote them. A member
that masters no other member keeps its own name. Aesthesia registers many
alternative grass plugins and converts one: a "root of the whole mod" rule
named that one's archive after `Grass Bloodmoon.esp`, a plugin the user never
loads, so it would never mount. Skyrim mounts `<stem>.bsa` for whichever
extension is loaded (`Morrowind_ob.esm` or `.esp`).

## Reading past the Windows path limit
<a id="staging-past-the-path-limit"></a>

**Code:** `long_path` in `asset_convert/sources/bsa_write.py`

Our writer opens each source directly, with no staging tree, and lists and opens
every path through `long_path`, so a source past 260 characters still packs
(`tests/test_bsa_long_paths.py`). The rest of this section is the history from
the BSArch packer, where the problem was found.

Each archive was staged into `output/<plugin>/_bsa_staging_<type>/` as a tree of
hardlinks, and BSArch is pointed at that root. The staged path is therefore
longer than the source path it mirrors, by the whole
`_bsa_staging_<type>\` segment.

For a plugin with a long folder name that is enough to cross Windows'
260-character `MAX_PATH`. Measured over the 15 plugins in `output/`, the
longest staged paths are:

```
_bsa_staging_misc = 266   Unique Landscapes Compilation v2.2.0   <-- FAILS
                    228   Oblivion.esm, Nehrim.esm
                    215   Morrowind_ob.esm
```

Only creature animdata reaches these lengths -- the offender is
`meshes\animationsetdata\tes4<plugin>_clear stream fishprojectData\
tes4<plugin>_clear stream fishproject.txt`, where the plugin name appears
TWICE below the staging root.

The failure is badly disguised. Under `-mt` BSArch reports only
`EAggregateException: One or more errors occurred` with no path, and the
pipeline truncates its output at 200 characters, so the message that reaches
the log is the tool's copyright banner. Dropping `-mt` produces the real
diagnosis: `"...evilspritecharacter.hkx". The system cannot find the path
specified`. The file is present; the path is simply too long to open.

`long_path` prefixes `\\?\`, which raises the limit to ~32,767 characters. It
is applied in two places, and both are needed:

1. `_link_or_copy`, so `os.link` can CREATE the staged path.
2. BSArch's INPUT root, so BSArch can WALK it.

Measured against a synthetic 381-character staging tree:

| input | output | result |
|---|---|---|
| plain | plain | `EAggregateException` |
| `\\?\` | plain | **159.2 MB packed** |
| plain | `\\?\` | `EAggregateException` |
| `\\?\` | `\\?\` | 159.2 MB packed |

Only the input root matters -- the archive being written sits directly in the
plugin dir and is never near the limit -- but prefixing both is harmless and
leaves nothing to rediscover.

The prefix is Windows-only and requires a normalized absolute path: `\\?\`
disables all path parsing, so a `/` separator or a `..` segment inside one is
passed through to the filesystem verbatim and fails. `long_path` returns its
argument unchanged off Windows, on a relative path, and on a path that already
carries the prefix.

### Why not just shorten the staging directory

`_bsa_misc` clears the limit by 2 characters and `_bsm` by 7, against a path
whose length the USER controls through both the repo location and the plugin
folder name. That is a reprieve, not a fix: the next long plugin name fails
again, and the failure mode is the disguised one above.

## <a id="bsa-name-hash"></a>Finding one path by its hash

`bsa_has` answers "does this TES4-family BSA (v103/104/105) store this path" without reading the
archive's directory: it hashes the folder, finds it in the folder table, skips to that folder's file
records, and confirms a file-hash hit against the stored folder and file names (so a hash collision can
never answer yes). The window's menu-art check (`menu_art.available`) asks this of every BSA in every
registered install; building the full index instead read 47 archives' 288,000 entries and took 0.8 s
of every start.

`tes4_hash` is the archive's own name hash: the low word packs the last two and first characters of
the name without its extension, its length, and a flag for `.kf`/`.nif`/`.dds`/`.wav`; the high word is
a 0x1003F-multiplier hash of the middle characters plus one of the extension. A folder is hashed whole,
with no extension: `textures\faces\oblivion.esm` is a folder. Verified 2026-11-23 against the stored
hashes of every folder and file in five archives (Oblivion, Shivering Isles, Fallout 3, New Vegas;
v103 and v104): 0 mismatches over 55,000 files.


## <a id="parallel-extraction"></a>Parallel extraction

**Code:** `asset_convert/sources/bsa_extract.py`, `bsa_extract_worker.py`. Each archive's files are decompressed
and written by a process pool instead of one core (the old pass kept about 0.8 cores busy: Nehrim's 3.9 GB in
49 s, FalloutNV's 8.7 GB in 87 s).

Design. `bsa_extract.extract_archives` plans every archive up front (directory read in the parent, lip files
dropped, output paths built), then cuts each archive's entries, sorted by offset, into chunks of about 24 MB
(1,500 files at most). A process pool of `worker_count()` runs `bsa_extract_worker.extract_chunk`: each worker
opens the archive itself, reads its own byte ranges, decompresses (zlib for v103/104, LZ4 frame for v105,
nothing for Morrowind) and writes, unlinking first. Only paths go to a worker and only counts come back, never
file contents. Chunks of every archive are queued together, so small archives overlap with big ones. Manifest
entries, "cached" skips and stats are written in archive order as before. A run with under 16 MB stored skips
the pool and runs in-process (a pool start costs about a second). Output paths that two entries share, in one
archive or across archives, are resolved up front: the last one wins, as in the serial pass, and the earlier
ones are not written, so the result does not depend on worker timing. A corrupt compressed entry is skipped
without being counted, as before; a corrupt `.lip` is now skipped unread and counted as skipped.

Measured (scratch directories, extracted with the old and new code, every file and the manifest compared by
MD5, plus names and case): Nehrim.esm 53.0 s to 14.1 s (44,279 files, identical); Knights.esp 1.6 s to 0.6 s
(2,536 files, identical); Tribunal.esm 0.7 s to 0.4 s (1,175 files, identical). Busy cores come from system-wide
CPU time and include antivirus scanning of the new files, so they are only indicative (old 10-17, new 9-21 on
this machine). The remaining time is mostly the disk and Windows file creation, not decompression.

## <a id="archive-only-output"></a>Archive-only output

**Code:** `asset_convert/sources/output_files.py` (the reader), `drop_loose` / `_carried` in
`asset_convert/sources/bsa_pack.py`, `phase_pack` in `convert.py`

Every run that writes archived output packs: `selected_steps` adds `pack_bsa` whenever a
`PACKED_OUTPUT_STEPS` step (meshes, speedtrees, creatures, import, sounds, scripts) is selected, so
`--meshes-only` runs meshes then the pack. Before this, a `--*-only` run ran that step alone and left its
whole tree loose beside the archives (Morrowind.esm after one `--meshes-only`: 10,542 loose files).

After a successful pack, `phase_pack` deletes the loose copies of every file the archives now hold
(`drop_loose`), and logs `Loose copies of packed files deleted: N files, X GB freed`. It also deletes the
files the archives never take (`_unshipped`: OS junk such as `thumbs.db`, and textures `texture_prune`
excludes), so nothing is left loose. It is on by default; `"deleteLooseAfterPack": false` in the config,
or `--keep-loose`, keeps them. Only an output folder that ships its plugin is ever packed (`ships_plugin`),
so the LOD mod, asset-only mods and the UI reskin stay loose. Never deleted: the plugins and other root
files (archives hold only subfolders), `SKSE/` and `Video/` (`LOOSE_ONLY_DIRS`, never packed), a name a
BSA cannot store (warned at pack), and any packed file whose size or mtime changed since the pack read it.
Nothing is deleted unless every archive the manifest records is on disk exactly as written.

Measured on scratch copies of the user's output (2026-11-24): Morrowind.esm 22,805 files, 2.09 GB freed
(2 archives, 1.42 GB); Tribunal.esm 3,124 files, 0.16 GB freed.

**Reading packed output.** `output_files` answers `exists`, `read_bytes`, `open_binary`, `signature`,
`files_under`, `is_dir` and `copy_to` for an absolute path inside an output folder: loose first, then the
folder's archives, ignoring case. A folder counts as packed when it holds the pack manifest; each archive's
directory is read once per process, and the packer calls `forget` after it rewrites archives or the
manifest. `source(path)` returns a picklable str or `Member`, so pool workers read members themselves.
Readers converted, each because it reads another plugin's or an earlier run's output:

- the importer's bounds/collision scan (`collision_extract._list_nifs`, `read_nif_data`,
  `rescan_mesh_caches`), and master caches now resolve against the run's output root, not `DEFAULT_OUTPUT`;
- death-pile bounds (`creature_races._pile_mesh_bounds`) search the run's output root, not `<repo>/output`;
- master grass meshes (`grass_profile`), LTEX normals across output trees (`ensure_ltex_normals`), the
  creature fragment's project manifests (`manifests_under`);
- up-to-date checks: sounds, voice files, music, flipbook atlases and height maps count an archived file as
  present, so a rerun does not reconvert everything; the voice prune also sweeps archived files;
- LOD: `lod_gen` (mesh existence, root screening, LOD textures), `lod_far_gen`, `terrain_lod_textures`,
  `tree_billboard`, and `create_lod._all_plugin_dirs`.

Steps that write and then read back their own files in the same run (creature merge, hair, magic art,
texture passes, weight variants) still work on the loose files they just wrote: every one of them reruns
over a tree its own step rewrote in full. Verified A/B (Morrowind loose vs packed and deleted, Tribunal's
meshes, creatures, import, sounds, scripts and pack steps run over each): Tribunal.esm and its hybrid ESP
byte-identical; the two runs' archives hold the same 3,375 paths with identical bytes except the 251 `.pex`
(the compiler stamps its compile time). A forced bounds/collision scan of Morrowind's 7,529 NIFs from the
archives writes both caches byte-identical to the loose scan (about 13 s either way), once the NIF list is
sorted (`_list_nifs`); before, its order followed the directory walk.

**LOD.** LODGenx64 reads archives itself (`Resource=<archive>` header lines: loose under `PathData` first,
then the archives, a later one winning; decompiled `LODGeneratorCMD/Program.cs`, `BSAArchive.cs`). A
supplier mesh that only an archive holds is no longer staged into the LOD tree: `_archive_resources` lists
every packed supplier's archives, last supplier first, so LODGen's later-wins order equals the first-hit
order Python resolves and stages loose meshes in. LODGen reads no DDS for TES5 object LOD (only billboard
dimension files, through the same lookup). Textures the tiles need are copied into the LOD mod through
`output_files.copy_to`.

Measured A/B on WrldMorrowind (Morrowind.esm + Tribunal.esm, loose vs packed and deleted): both bakes list
70,668 references and derive 715 `_far.nif`; the loose bake staged 708 supplier meshes, the archived one
none. The LOD mod trees hold the same 4,644 paths; every `.btr`, `.dds`, `.nif`, marker and `.lod` is
byte-identical, and all 439 `.bto` have the same sizes, 424 of them with different bytes. LODGenx64 itself
does that: rerunning it twice on one unchanged input left 385 of 439 `.bto` byte-different, 0 of them a
different size.

**Test instances.** `test_instance.link_mod` copies the root archives (skipped when size and mtime match;
the game holds them open, as it does plugins) and removes files in the instance Data at paths the archives
hold that this build did not link: links an earlier build made to loose copies since deleted, which would
otherwise override the archives. `build --mod` also takes an absolute output folder.

**Left alone, and why.** Plugins and every sidecar beside them (`<plugin>.manifest.json`, music
tracks, crime and voice maps, `world_extents.json`) sit at a folder's root or in `SKSE/`, which are never
packed; the runtime's `GetFileAttributesA` probes all look under `SKSE/`. Script sources are packed; the
compiler reads them through [mirrored copies](#archived-script-sources). `phase_pack_zip` zips the plugin, the archives and the loose-only folders.

**Installs.** `core/gui/install_mods` lists every file in the folder, so archives install and deleted loose
files leave the install's file list (its `tesract.json` diff removes their links). The pack manifest and
the discard list are converter-only (`SIDECAR_NAMES`).

### <a id="archived-script-sources"></a>Script sources in the archives

`scripts/source/*.psc` is packed and its loose copies dropped like every other file (it was kept loose
before; tiny files deploy slowly in Vortex). The Papyrus compiler reads imports only from folders, so
`papyrus_compile._archived_copies` mirrors the `.psc` an output folder's archives hold, and that have no
loose copy, into `<data root>/temp/papyrus_imports/<output folder>/` (`output_files.mirror_archived`) and
passes it as an `-h` import right after the loose folder: for every master, and for the plugin's own folder
(a shared mod folder keeps siblings' sources only in the archives). Each copy gets the size and mtime the
pack recorded, so a later run rewrites only what changed and deletes what the archives no longer hold. The
root's static-script check (`_root_statics_current`) reads through `output_files`.

The script stage's wipe (`context_setup.prepare_output_dir`) marks the archived `.psc`/`.pex` it would have
deleted loose (`output_files.discard_archived`), so a script that is no longer generated leaves the
archives at the next pack instead of being carried. `output_files` also hides discarded paths when it loads a
folder, so a later process does not see them either. The per-plugin ownership lists
(`scripts/<owner>.owned.txt`) are packed too and read through `output_files`; before this they were read
loose only, so after a pack had dropped them a shared mod folder looked unshared.

Dev tools that read a `scripts/` folder loose call `output_files.unpack`, which writes the archived-only
files back to their loose paths with the recorded size and mtime: the next pack reuses them unchanged and
drops them again. Converted: `compile_papyrus`, `script_debug`, `papyrus_log_bindings`, `wiring_audit`.
Not converted (each would need its oversized `main`/`__init__` split first): `ck_compile_check`,
`property_type_audit`, `vmad_property_typecheck`, `script_pair`, `quest_walkthrough_tes5`,
`psc_corpus_diff`, `psc_semantic_diff`; run them on a `--keep-loose` build or after `output_files.unpack`.

Measured (Tribunal.esm over an archive-only Morrowind.esm, 647 master sources mirrored): 251/251 scripts
compiled, 0 failures, as with loose sources; a rerun of the script stage then pack marked 502 archived
`.psc`/`.pex`, rewrote `Tribunal.bsa` in 0.2 s and left no loose script file.

### <a id="repacking-over-deleted-loose-files"></a>Repacking over deleted loose files

The archive is the source of truth for every path the current run did not write loose. `_carried` keeps a
file from the old archives (any archive named for the stem or a replaced stem, trusted or not, since it may
be the only copy) when it has no loose copy, unless:

1. a step removed it through `output_files.discard`, which deletes a loose copy and appends the archive path
   to `.bsa_pack_discarded.txt` (cleared after the next pack); the voice prune does this;
2. `texture_prune` now rejects it; or
3. the last pack read it loose (it is in the manifest's `files` but not its `gone` list) and it has since
   been deleted: a step removed it while loose copies were kept; or
4. it sits under an asset namespace none of the folder's plugins writes (`_foreign_namespaces`: another
   family's, `tes4` for a non-Oblivion chain, or the hybrid shared mod's `morroblivion`). The namespace is the
   folder after the top one (`meshes\<ns>\`), or after `meshes\actors\`. A plugin that changes family (Tamriel
   Data moved from Morroblivion mode, `tes4`, to hybrid, `morrowind`) otherwise carried the whole old tree
   forever: measured 7.74 GB of 15.6 GB in Tamriel Data's archives, 0.11 GB in TR_Mainland's.

The manifest's `gone` lists every path with no loose copy: those carried at the pack, plus those
`drop_loose` deleted. A carried file keeps its recorded signature, so an archive whose contents did not
change keeps its digest and is not rewritten. A compressed carried payload is copied as stored; a raw one
(sound, scripts) is read from its span in the old archive (`bsa_write.stored_index`), since there is no
source file left. Consequence: a file no step writes any more, and that no step discards, stays in the
archives until the plugin is converted into a fresh output folder.

Measured: rerunning Tribunal's meshes step and pack over its packed output (no loose copies) rewrote 1,326
files, kept 1,798 from the archives with no loose copy, and produced archives holding the same 3,375 paths
with identical bytes (0 missing, 0 extra, 0 changed); the drop afterwards freed the 1,326 rewritten files
again (0.10 GB).

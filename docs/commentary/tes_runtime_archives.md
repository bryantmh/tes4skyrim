# ArchiveLoader: extra archives per plugin ("Part" archives)

**Code:** `tes_runtime/archive_loader/` (`ArchiveLoader.dll`: `plugin.cpp`, `archives.cpp`, `ids.h`,
`pre_ae_ids.txt` and the generated `ids_pre_ae.h`), `tes_runtime/common/hook.cpp` (`FindCallsTo`).

It is its own SKSE plugin, not a TESRuntime feature: it touches only the archive mount, so it can load on every
build without TESRuntime's AE-only features. It ships in the same runtime package (the `TESRuntime` mod).

## Part archives
<a id="part-archives"></a>

Skyrim mounts two archives per active plugin: `<stem>.bsa` and `<stem> - Textures.bsa`. The converter writes
no Textures archive; a plugin whose assets do not fit in `<stem>.bsa` ships `<stem> - Part 2.bsa`,
`<stem> - Part 3.bsa`, ... (numbered without gaps from 2, any content, textures included;
[pack layout](asset_convert_bsa.md#pack-layout)). ArchiveLoader mounts them inside the engine's own per-plugin
mount, right after the engine's `<stem> - Textures.bsa` call (made whether or not that file exists), so a Part archive sits exactly where that plugin's own archives sit in file priority:
a later plugin's files win over an earlier plugin's, and loose files still win over everything. Before this, an
overflow archive needed a record-free dummy ESL "loader" plugin per archive (`asset_convert/sources/bsa_pack.py`).

- Only ACTIVE plugins get their Part archives, because the engine calls the mount only for those.
- The lookup stops at the first missing number (`GetFileAttributesA("Data\<name>")`). A plugin with no Part
  archives costs one failed file lookup and logs nothing.
- Each Part archive is mounted at most once per session (a case-insensitive name set), however often the engine
  re-mounts its plugin (see the callers below).
- The log (`ArchiveLoader.log`) prints `archives: mounted <name>` for each Part archive, and
  `hooks: Part archives installed` at load.
- It works on AE 1.6.x and 1.7.x through the Address Library, and on SE 1.5.97 and VR 1.4.15 through a
  generated table ([pre-AE builds](#pre-ae-builds)).

## The engine mount
<a id="the-engine-mount"></a>

Found in 1.6.1170 (the build the user plays). Ids are Address Library ids.

| What | 1.6.1170 RVA | Id |
|---|---|---|
| `MountPluginArchives(const char* fileName)` | 0x1bf710 (632 bytes) | 13786 |
| `LoadArchive(const char* name, void*, void*)` | 0xd0a920 | 69852 |
| startup archive init | 0x64b450 | 36639 |
| suffix table `{".bsa", " - Textures.bsa"}` | 0x1fd4c88 | 358971 (the second entry) |

**How it was found.** The string ` - Textures.bsa` has no code reference. Code reaches it only through a pointer
table at 0x1fd4c88, and the table's users are 0x1bddd0 (adds up a plugin's file sizes) and 0x1bf710 (the mount).
The other `.bsa` strings are red herrings:

- `%s - textures.bsa` (lowercase) at 0x5f7dd0 is the Creations uninstaller: it hands each name to `DeleteFileA`.
- 0xcefd80 reads `Plugins.txt` from the game folder, swaps `.esp`/`.esm`/`.esl` for `.bsa`, and adds the result
  to `sResourceArchiveList`. It is an old path: SSE's real `Plugins.txt` lives in AppData and its active lines
  start with `*`. The old BSArch-era `bsa_pack.py` (its `loader_stem` helper, now removed) cited the 1.5.97
  copy of this routine, not the real mount.

**`MountPluginArchives`** splits the file name (0xcfc720) and returns unless the extension is
`.ESP`/`.ESM`/`.ESL` (`_stricmp`). It then registers a temporary sink (vtable 0x177cf68) on the archive registry
(0xd0a880 adds it, 0xd0a8a0 removes it). The sink's handler (0x1c2d90) records each archive registered while
it is attached into the plugin-archive map at 0x1fd4e38. For `"%s%s"` of the stem plus each suffix, the mount
calls `LoadArchive(name, 0, 0)` unless the map already holds the name: `.bsa` at 0x1bf8a8, ` - Textures.bsa` at
0x1bf958. `LoadArchive` builds a 0x50-byte archive, opens it (0xd01330), and registers it only when it opened
(0xd01c70), so a missing file is a silent no-op.

**Callers of the mount** (every `E8` to 0x1bf710):

- 0x64b557: the startup archive init, for each Creations plugin that 0xd0a5f0 accepts.
- 0x64b5e3: the startup archive init, for each active plugin in load order (list from 0x1bb410). This init
  first mounts `sResourceArchiveList`, then `sResourceArchiveList2`, then `Update.bsa`, then the plugins, then
  registers loose `data\`. SKSE plugins load before it, so the hook is in place in time.
- 0x64d13b: the same active-plugin loop on its own (a re-mount).
- 0x1b9885 and 0x1b98dd: TESDataHandler's file load, for files on the Creations list (0x152d900).

**The hook** repoints the second `LoadArchive` call (the ` - Textures.bsa` one) at
`MountTexturesThenParts`. That function makes the original call, then mounts each Part archive with the same
arguments. The Part archives therefore go in while the plugin-archive sink is still attached, so the engine
records them in its plugin-archive map exactly like the plugin's own two. The install refuses unless the mount
makes exactly two `LoadArchive` calls inside the mount, whose bounds come from the exe's own unwind table
(`RtlLookupFunctionEntry`), so no per-build length is hardcoded.

**Cross-build check.** Id 13786 is the same 632-byte function on 1.6.640 (0x180600), 1.6.659 (0x180400) and
1.7.104 (0x1c4cb0). Each copy checks the same three extensions, has the same `%s%s` pair, and makes the same two
calls to id 69852. `tools/validate/stable_id_check.py --header tes_runtime/archive_loader/ids.h` resolves every id on every
build.

**Priority, measured in game.** Headless instance 2, 1.6.1170. Two record-free plugins each ship only a
`<stem> - Part 2.bsa` that replaces all 57 vanilla Landscape TXST diffuse textures: PartTestA's are solid red,
PartTestB's solid green. At `coc Riverwood`:

| DLL | Active plugins | Ground | Log |
|---|---|---|---|
| without the feature | A, B | vanilla | no `archives:` lines |
| with it | A, B | green (B wins) | mounted A's Part 2, then B's |
| with it | B, A | red (A wins) | mounted B's Part 2, then A's |
| with it | A only (B's Part 2 still in Data) | red | mounted A's Part 2 only |

So a later-mounted archive wins, the Part archives follow load order, and an inactive plugin's Part archive is
never mounted.

**A dead end:** the main-menu logo (`meshes\interface\logo\logo.nif`, texture
`textures\interface\objects\logo02_d.dds`) did not change even with a LOOSE replacement, so the AE main menu
does not draw that texture. It cannot show whether the archives mount before the main menu.

## Pre-AE builds
<a id="pre-ae-builds"></a>

SE 1.5.97 and VR 1.4.15 run the same mount and the same startup order.

| Build | Mount | Size | LoadArchive | `.bsa` call | ` - Textures.bsa` call (the one hooked) | Active-plugin loop |
|---|---|---|---|---|---|---|
| AE 1.6.1170 | 0x1bf710 | 632 | 0xd0a920 | 0x1bf8a8 | 0x1bf958 | 0x64b5e3, in the init (0x64b450) that loads Update.bsa |
| SE 1.5.97 | 0x173bd0 | 632 | 0xc453e0 | 0x173d68 | 0x173e18 | 0x5b9083 (fn 0x5b8fe0), called from the init that loads Update.bsa at 0x5b769d |
| VR 1.4.15 | 0x184360 | 600 | 0xc8a490 | 0x1844d8 | 0x184588 | 0x5c10f3 (fn 0x5c1050), called right after Update.bsa at 0x5bf5e8 |

- SE checks `.ESP`, `.ESM` and `.ESL`. VR checks only `.ESP` and `.ESM`, because VR 1.4.15 predates light
  plugins. VR has no Creations loop, so the active-plugin loop is the mount's only caller.
- In each build, a byte scan shows the second `LoadArchive` call is the first call after the load of the
  ` - Textures.bsa` table entry, and the `.bsa` entry's first call is the first `LoadArchive` call.
  The same scan passes on all five unpacked exes (AE 1.6.659, 1.6.1170, 1.7.104, SE 1.5.97, VR 1.4.15).
- The startup init has the same order on SE and VR as on AE: the two ini archive lists, then `Update.bsa`, then
  the plugin loops, then loose `data\`.

**Second source.** `python -m tools.disasm.pre_ae_map --plugin tes_runtime/archive_loader` derived the same two addresses
on both builds on its own by matching function bytes: mount SE `0x173bd0` (similarity 1.00) and VR `0x184360`
(0.97); LoadArchive SE `0xc453e0` (0.99) and VR `0xc8a490` (1.00). The SE Address Library names the SE pair
13673 and 68497.

**How it resolves.** `pre_ae_ids.txt` lists `kMountPluginArchives` and `kLoadArchive`, the generated
`ids_pre_ae.h` holds those two rows, and `SKSEPlugin_Load` loads that table with `VersionDb::LoadPreAe` when
the runtime has no Address Library database, as MorrowindRuntime does. The DLL exports both `SKSEPlugin_Version`
(AE SKSE) and `SKSEPlugin_Query` (SKSE64 2.0.x and SKSEVR), so the same DLL loads on all three.

**Not run in game on SE or VR.** The headless instances are built for 1.6.1170 only. They inject
`skse64_1_6_1170.dll` directly, and the bridge that drives `coc` and screenshots resolves AE ids. No SKSE64
2.0.20 (for 1.5.97) or SKSEVR is on this machine. VR cannot run headless at all.

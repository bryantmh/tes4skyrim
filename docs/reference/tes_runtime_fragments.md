# CreatureRuntime animation cache fragments

Skyrim registers every animation project in two global text databases,
`meshes\animationdatasinglefile.txt` and `meshes\animationsetdatasinglefile.txt`,
each parsed exactly once per process. A mod that adds projects cannot ship
those files without racing every other mod for the same path. Instead it ships
a **fragment**, and the `CreatureRuntime.dll` SKSE plugin composes
`vanilla base + every fragment` in memory at the moment the engine parses each
file. Any mod can emit a fragment; nothing here is specific to this converter.

## Location

```
Data\SKSE\Plugins\CreatureRuntime\animation\<anything>.json
```

Every `*.json` in that folder is read, sorted by filename (ordinal, case-
insensitive), so composition is deterministic. One fragment per contributing
mod is the convention; the converter names it after the plugin
(`Oblivion.esm.json` → `Oblivion.json`).

## <a id="never-packed"></a>Fragments ship LOOSE, never in a BSA

The DLL discovers fragments by listing the folder above with `FindFirstFileA`,
which sees loose files only. A fragment packed into a BSA is therefore
invisible, and the mod registers nothing: its creatures keep their meshes,
skeletons and behavior graphs but have no clips, so they stand still. The
symptom in `CreatureRuntime.log` is `compose: 0 fragment(s)`.

Listing the directory is the only workable discovery method, because the whole
point of a fragment is that any number of unknown mods can each ship one — so
there is no fixed path to open and no name to derive. The engine's resource
layer resolves a path but cannot enumerate a directory, which rules out asking
it instead.

So `SKSE/` is excluded from BSA packing (`bsa_pack.LOOSE_ONLY_DIRS`) and stays
loose in the packaged mod, next to `CreatureRuntime.dll` — which SKSE already
requires to be loose for the same reason. Everything else a plugin converts is
packed as usual.

## Schema (version 1)

```json
{
  "version": 1,
  "source": "Oblivion.esm",
  "animdata": [
    { "project": "tes4oblivion_ratproject.txt",
      "clip_block":   ["1", "3", "Behaviors\\...", "..."],
      "motion_block": ["0", "1.4", "1", "1.4 0 0 0", "1", "1.4 0 0 0 1", ""] }
  ],
  "animsetdata": [
    { "entry": "tes4oblivion_ratprojectData\\tes4oblivion_ratproject.txt",
      "block": ["1", "FullCharacter.txt", "V3", "0", "0", "0", "3", "..."] }
  ],
  "animdata_append": [
    { "project": "DefaultMale.txt",
      "clips":   ["<clip block lines>"],
      "motions": ["<motion block lines>"] }
  ],
  "animsetdata_append": [
    { "entry": "DefaultMaleData\\DefaultMale.txt",
      "set_file": "TES4Guns.txt",
      "block": ["V3", "4", "MagicWeap_ForceEquip", "...", "3", "iLeftHandType", "13", "13", "..."] }
  ]
}
```

Every block is a list of **already-formatted lines** in the singlefile's own
grammar (see the module docstring of `asset_convert/havok/animation_data.py`
for the line-exact grammar). The composer does no float formatting, no CRC and
no root-motion simplification; it only splices lines and rewrites counts.

| Key | Meaning |
|---|---|
| `version` | schema version; the DLL rejects fragments it does not understand |
| `source` | free text naming the contributor, for logs |
| `animdata[].project` | the project file name registered in the name list (`<x>project.txt`) |
| `animdata[].clip_block` | the project's animationdata block, WITHOUT its leading line count |
| `animdata[].motion_block` | the boundanims block, without its count; `null` when the project has no clip data (`HasMotionData == 0`), in which case no count+block pair is emitted |
| `animsetdata[].entry` | the `<Project>Data\<Project>.txt` name registered in the setdata name list |
| `animsetdata[].block` | the project's whole setdata section: set-file count, set-file names, then one V3 block per set file |
| `animdata_append[]` | clip blocks (and motion blocks) spliced INTO an existing project's block; the wrapper counts are rewritten. The FO3/FNV gun clips ride on `DefaultMale.txt` / `DefaultFemale.txt` this way, their animation indices being the positions the build appended to each character file's `animationNames` ([asset_convert_falloutnv.md#gun-graph](../commentary/asset_convert_falloutnv.md#gun-graph)) |
| `animsetdata_append[]` | one set file (name + V3 block) added to an existing project's section; its set-file count is rewritten |

## Composition rules

1. The base is whatever the engine would have opened for the vanilla path:
   the file in `Skyrim - Animations.bsa`, or a loose override such as the
   output of Nemesis or Pandora. Those tools' output is therefore an input,
   not a competitor.
2. `animdata` and `animsetdata` entries are appended: the name onto the end
   of the name list, the block onto the end of the body. Every block is
   preceded by its line count exactly as the wrapper grammar requires. Order
   never matters to the engine — both parsers register each project in a
   hash map keyed by its name (see [registry keys](#registry-keys)).
3. A name the base (or an earlier fragment) already registers is **skipped**.
   One extra name with no matching block desyncs every later project, so the
   first registration always wins and nothing is ever merged into it.
4. `*_append` entries splice into the named existing project. Missing targets
   are ignored.
5. The composed text is CRLF, latin-1 — what the vanilla files use.
6. With no fragments present the DLL hands the engine the original stream
   untouched.

`asset_convert.havok.animation_data.compose_animationdata` /
`compose_animationsetdata` are the reference implementation of these rules;
`tools/validate/animcache_validate.py --plugin X` runs them offline and
validates the result. Why the runtime design exists and the engine facts it
rests on: [asset_convert_creature.md#runtime-animation-cache-composition](../commentary/asset_convert_creature.md#runtime-animation-cache-composition).

## <a id="registry-keys"></a>Registry keys — by name, not by index

Vanilla's animationsetdata name list is exactly the first 49 of the 429
animationdata names in the same order, which looks like index pairing. It is
not: the AnimData parser (`0x4f7280` on 1.6.659) reads the name list into a
BSTArray, parses each block in order, and inserts every project into a
name-keyed hash map at `AnimationClipDataSingleton+0x18`; the AnimSetData
parser (`0x4fb3c0`) does the same into its own map. Nothing pairs the two
arrays by position, and the caller (`0x501f40`) only invokes the parsers.
A splice-at-the-boundary composer was built on the index theory and
removed once this was read; appending is correct.

## <a id="the-runtime-composer"></a>The runtime composer

`tes_runtime/creature/build.bat` builds `tes_runtime/dist/CreatureRuntime.dll` (standalone MSVC,
no SKSE source tree). One copy serves every converted mod, so it ships as its
own SKSE mod (`tools/release/package_runtime_dll.py`) rather than per plugin;
it is never packed into a BSA because SKSE loads DLLs from loose files only —
the same constraint that keeps fragments loose ([never packed](#never-packed)). It resolves every engine
address through the Address Library (`versionlib-*.bin`) and refuses to hook
when a stable ID is missing, so a game update degrades to "no TES4 projects
registered" rather than a crash. Its log is
`Documents\My Games\Skyrim Special Edition\SKSE\CreatureRuntime.log`.

It reads `Data\SKSE\Plugins\CreatureRuntime\animation\*.json`, and the
pre-split folder ([below](#legacy-sidecar-paths)).

## <a id="legacy-sidecar-paths"></a>Pre-split sidecar paths (deprecated, to be removed)

Users install new runtime DLLs over converted output of any age, so every
sidecar the runtime split moved is still read from where an older converter
wrote it. The current location always wins; each fallback that is used logs a
`DEPRECATED` line naming the file and what to rebuild. Remove these once no
supported release writes the old layout.

| Runtime | Current | Also read (deprecated) | Code |
|---|---|---|---|
| CreatureRuntime | `CreatureRuntime\animation\<plugin>.json` | `TESRuntime\animation\<plugin>.json`, when no current fragment has the same `source` | `creature/plugin.cpp` `AddLegacyFragments` |
| FalloutRuntime | `FalloutRuntime\<plugin>.guns.json` | `TESRuntime\<plugin>.guns.json`, when the current folder lacks that file | `fallout/guns.cpp`, `common/engine.cpp` `ForEachLegacySidecar` |
| FalloutRuntime | `FalloutRuntime\FalloutRuntime.ini` | `TESRuntime\TESRuntime.ini` (keys a player already set), when the current one is absent | `fallout/fire.cpp` `IniPath` |
| TESRuntime | `TESRuntime\<plugin>.apparatus.json` | `MorrowindRuntime\<plugin>\APPA.txt`, with that folder's `GMST.txt` and `NPC_.txt` for the settings and the player's Intelligence and Luck, when the plugin has no `apparatus.json` | `tes/alchemy_hooks.cpp` `LoadLegacySidecars` |
| TESRuntime | a patched journal movie's `_root.TESRT_Patched` / `_root.TESRT_Runtime` | `_root.MWRT_Patched` / `_root.MWRT_Runtime`, a movie patched before the split | `tes/journal_objectives.cpp` `InstallInto` |

Body parts (`bodyparts.json`) moved too, but limb severing is dormant, so no
fallback is read. The old journal co-save record (`MWJL` under `'MWRT'`) is not
carried over: journal history from before the split is lost.

Rebuilding a plugin removes its old copies: the import's
[sidecar sweep](../commentary/tes5_import_pipeline.md#stale-runtime-sidecars)
deletes `TESRuntime\<plugin>.guns.json` and `MorrowindRuntime\<plugin>\APPA.txt`
once they are no longer written, and the creature stage deletes the old
fragment when it writes or drops the current one.

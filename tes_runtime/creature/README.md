# CreatureRuntime

**Lets converted creatures actually move.** Goblins, scamps, cliff racers,
deathclaws — every converted creature brings its own skeleton and animation
set, and CreatureRuntime is what introduces them to Skyrim.

## What it does for you

Skyrim keeps a list of every creature type's animations in two shared files.
Normally a mod that adds creatures has to ship its own copy of those files —
and only one copy can win. Install two creature mods that do it and one
mod's creatures stand frozen in a T-pose or slide around without animating.

CreatureRuntime removes that fight. Each converted game ships only a small
note listing its own creatures, and when Skyrim starts, CreatureRuntime adds
every installed note to Skyrim's own list in memory. Oblivion, Nehrim,
Morrowind and Fallout creatures, and any other mod's creatures, all work side
by side, and no game file is ever overwritten.

## Should I keep it enabled?

Yes, if you play any converted game with creatures (all of them have some).
Without it, converted creatures don't animate: they stand frozen, glide, or
can't attack. It only reads files; it changes no records and nothing in your
save.

---

## For developers

The converter writes one fragment per plugin to
`Data\SKSE\Plugins\CreatureRuntime\animation\<plugin>.json`. When the engine
parses `meshes\animationdatasinglefile.txt` and
`meshes\animationsetdatasinglefile.txt`, this DLL composes the vanilla base plus
every fragment in memory and hands the parser the result. It redirects the one
call each parser makes to the shared resource-open helper; everything else is
the engine's own code. It resolves three addresses and touches no form, native
or co-save.

- Schema: [tes_runtime_fragments.md](../../docs/reference/tes_runtime_fragments.md)
- Why: [asset_convert_creature.md](../../docs/commentary/asset_convert_creature.md#runtime-animation-cache-composition)

Log: `CreatureRuntime.log`.

`build.bat` → `..\dist\CreatureRuntime.dll` and `compose_test.exe`:
`compose_test.exe <base_dir> <fragment_dir> <out_dir>` runs the composer
offline, and `tests/test_creature_anim.py` diffs it against the Python
reference (`asset_convert/havok/animation_data.py`), byte for byte.

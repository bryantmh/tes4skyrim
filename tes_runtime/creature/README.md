# CreatureRuntime

Registers converted creatures' animation projects with the game, so any mod can
ship creatures without shipping (and fighting over) the two global animation
databases.

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

## Building

`build.bat` → `..\dist\CreatureRuntime.dll` and `compose_test.exe`:
`compose_test.exe <base_dir> <fragment_dir> <out_dir>` runs the composer
offline, and `tests/test_creature_anim.py` diffs it against the Python
reference (`asset_convert/havok/animation_data.py`), byte for byte.

# common — source every runtime compiles in

Not a DLL. Each project's `build.bat` compiles the files it needs from here
with `/I..\common`, so a header here is included by name (`#include "log.h"`).
Everything is in namespace `tesruntime`; MorrowindRuntime's own code is in
`tesruntime::mw`.

| File | What |
|---|---|
| `skse_abi.h` | the slice of the SKSE plugin ABI the runtimes use, plus the Scaleform `GFxValue` types |
| `addresses.*` | Address Library id → address, signature fallback, `VCall`/`At`, vtable slot swap |
| `hook.*` | call-site and vtable-slot patching through a near trampoline |
| `paths.*` | the plugin's name, its sidecar folder `Data\SKSE\Plugins\<name>\`, the SKSE log folder |
| `log.*` | `<name>.log`; `LogVerbose` only while `TESRUNTIME_VERBOSE` is set |
| `json.*` | a small JSON reader for the sidecars |
| `engine.*`, `engine_ids.h` | fixed strings, `FormFromFile`, sidecar walking, main-thread tasks and the shared main-thread timer (`StartMainThreadTick`) |
| `msvc.bat` | puts MSVC x64 on the environment; every `build.bat` calls it |

A plugin calls `SetPluginName` first thing in `SKSEPlugin_Load`, then
`OpenLog`; the log file and `SidecarDir()` both take that name.

This code is MIT. MorrowindRuntime compiles it into its GPL-3.0 binary, which
MIT allows; nothing here may ever include code from `external/openmw/`
([licensing](../../docs/commentary/morrowind_runtime.md#licensing)).

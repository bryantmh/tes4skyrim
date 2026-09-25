# HavokWorldSize

Widens Skyrim's Havok broad-phase world AABB past its vanilla ±64 cells, which
is what breaks physics and interactions far from the world origin. Useful to any
large worldspace, converted or not, so it stands alone: it shares no code with
the other runtimes and needs no Address Library.

The limit is a single `.rdata` float (`3745.38232421875` havok m = 262,144 game
units = 64 × 4096) that the `hkpWorldCinfo` setup loads as the broad-phase
extent; objects outside it clamp to `hkpBroadPhaseBorder`. It is found **by
value** (a 16-byte-aligned broadcast quad), not by address, so no build is
hardcoded — verified to occur exactly once in SSE GOG/AE, SSE Steam and the
**unpacked** Skyrim VR binary. Exports `SKSEPlugin_Query` as well as
`SKSEPlugin_Version`, so one DLL is discoverable on SE, AE and VR.

`HavokWorldSize.ini` sets `fWorldCells` (default 128; use the SMALLEST value
covering your worldspace — the broad-phase key step doubles with it) and
`bDryRun=1` to log the site without writing. Log:
`Documents\My Games\Skyrim Special Edition\SKSE\HavokWorldSize.log`.
Runtime-only: no record data, no FormIDs, so removing it fully reverts.
Analysis: [worldspace_havok_range.md](../../docs/audits/worldspace_havok_range.md).

## Building

`build.bat` → `..\dist\HavokWorldSize.dll`. Needs only SKSE at runtime.

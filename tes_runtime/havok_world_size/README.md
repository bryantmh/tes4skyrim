# HavokWorldSize

**Keeps physics working at the far edges of big worlds.** Useful with any
large worldspace, converted or not.

## What it does for you

Skyrim's physics only reaches about 64 cells from the center of a world. That
is plenty for Skyrim itself, but large worlds such as Tamriel Rebuilt stretch
well past it. Out there, things quietly break: NPCs bob in and
out of the ground, doors won't open, arrows and spells pass through targets,
and dropped items fall through the floor.

HavokWorldSize widens that limit so the whole world plays like the middle of
it.

## Should I keep it enabled?

Yes, if you play in any large worldspace. It's harmless everywhere else. It
changes no records and nothing in your save, so removing it simply puts the
old limit back.

### Settings

`Data\SKSE\Plugins\HavokWorldSize.ini`:

- `fWorldCells` — how far physics reaches, in cells (default **128**). Use the
  smallest value that covers your world: larger values make physics slightly
  coarser everywhere.
- `bDryRun=1` — only log what it would change, without changing it.

It needs only SKSE (not the Address Library) and works on Skyrim SE, AE and VR.

---

## For developers

It widens Skyrim's Havok broad-phase world AABB past its vanilla ±64 cells. The
limit is a single `.rdata` float (`3745.38232421875` havok m = 262,144 game
units = 64 × 4096) that the `hkpWorldCinfo` setup loads as the broad-phase
extent; objects outside it clamp to `hkpBroadPhaseBorder`. It is found **by
value** (a 16-byte-aligned broadcast quad), not by address, so no build is
hardcoded. It was checked to occur exactly once in SSE GOG/AE, SSE Steam and the
**unpacked** Skyrim VR binary. Exports `SKSEPlugin_Query` as well as
`SKSEPlugin_Version`, so one DLL is discoverable on SE, AE and VR. The
broad-phase key step doubles with `fWorldCells`, which is why the smallest
covering value is best.

Shares no code with the other runtimes. Log:
`Documents\My Games\Skyrim Special Edition\SKSE\HavokWorldSize.log`.
Analysis: [worldspace_havok_range.md](../../docs/audits/worldspace_havok_range.md).

`build.bat` → `..\dist\HavokWorldSize.dll`.

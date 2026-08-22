# CK "Initializing References" hang — investigation log (2026-08-22)

## ✅ DIAGNOSED (2026-08-22, second session) — it is a CRASH, not a deadlock

`tools/win_stackwalk.py --watch` captured the live process at
`phase at capture: 'Initializing References...'`. Three facts settled it:

1. **The main thread is in `Sleep`, not a kernel wait.** Its innermost frames
   are `ZwDelayExecution ← SleepEx ← CreationKit.exe+0x1212b40 ←
   +0x12139d0 ← +0x12131b4`. `+0x12139d0` is a backoff helper —
   `if (*n < 10000) { ++*n; Sleep(0); } else Sleep(1);` — and `+0x1213060` is
   `BSSpinLock::Lock` (it asserts with the literal path
   `e:\_skyrimhd\code\gamesln\bscore\BSSpinLock.h`). Frame `+0x12131b4` is
   inside its unbounded slow-path loop. `Sleep(1)` forever is why CPU reads
   0% and why this looked like a deadlock. A second thread (15524) is
   spinning on the same lock at the same call site.
2. **A third thread crashed.** Thread 21808's stack is
   `KiUserExceptionDispatcher → … → UnhandledExceptionFilter →
   CKPE.Common.dll+0x248ef → SleepEx`: CKPE's handler caught a fault and
   parked the faulting thread, which never released the spin lock.
3. **`CreationKitPlatformExtendedCrashReport.log`** (written 46 s before the
   capture) names it exactly:
   ```
   EXCEPTION_ACCESS_VIOLATION at CreationKit.exe+134BEDA  mov eax, [rax+0x10]
   Access Violation: Tried to read memory at 0x0000000000000010
   R9 = "..\Shared\TESForms\World\TESObjectCELL_Reference.cpp"
   ```
   `+0x134bed0` is a three-instruction leaf getter, `return this->[0x10]`,
   called with `this == nullptr`.

**Where the null comes from.** Call chain, all in `CreationKit.exe` 1.5.73:
`TESObjectREFR` init (`+0x1bfea70`) → ExtraDataList init (`+0x15a15e0`,
`ExtraDataList.cpp`) → **teleport-data init (`+0x15f0580`)** → a
`TESObjectCELL_Reference.cpp` routine (`+0x1bf4260`) → the null getter.

`+0x15f0580` null-checks generously — it logs `Could not find linked door`,
`Linked door … points to invalid object`, `Linked door … has no parent cell` —
and then, for a door standing in an **exterior** cell, re-derives the
destination cell from the worldspace grid using **the XTEL's own coordinates**
(read from the teleport extra at `arg0+8`, *not* from where the door stands).
That lookup's result goes straight into the next call at `+0x15f0893` with **no
null check**. Empty grid square ⇒ null ⇒ `[this+0x10]` ⇒ access violation.

**The offending data — exactly one reference in Oblivion.esm.**
`DAPeryiteDoorTEMPREF` (`0001ECC6`) stores an XTEL position of
(2036.7, 1785.9, 7207.7) → grid **(0,0)**, while its partner
`DAPeryiteDoorREFX` (`000E7A92`) stands at (67326.7, 67976.7) → grid
**(16,16)** in `DAPeryiteRealm`, whose cells span x[4,36] y[5,26]. Nothing
occupies (0,0). The XTEL coordinates are **authored Oblivion data**, carried
through faithfully; Oblivion never noticed because it teleports to the target
door's parent cell and never uses those coordinates to find a cell. The
editor's name for the ref — `TEMPREF` — says what it is.

**Fix** (`tes5_import/record_types/world.py`, `convert_REFR`): when an XTEL
names a door in an exterior cell and the XTEL's own coordinates fall on a grid
square with no cell, write the target door's own position instead — the
authored indicator, and where Oblivion would have put the player anyway. The
index behind it is built master-aware in `_build_teleport_grid`
(`import_main.py`) and plumbed to the conversion pool in `convert_worker.py`.

**Guard:** `python tools/cell_grid_check.py <esm> --teleport-cells` — 1 hit
before the fix, 0 after; exits non-zero so it can gate a build.

🛑 **Not yet confirmed in the CK.** The rebuilt ESM passes both new gates, but
only a CK run proves the hang is gone.



Loading the full converted `output/Oblivion.esm/Oblivion.esm` into Creation Kit
(alongside Skyrim.esm+DLC) deadlocks during the "Initializing References"
loading phase. **As of this writing the hang is NOT resolved.** This doc is
the full record so the investigation doesn't restart from zero.

## 🛑 Read this first (2026-08-22, second session)

Two things changed since the section below was written.

0. 🛑 **The Creation Kit in use lives in the SkyrimVR folder.**
   `D:\SteamLibrary\steamapps\common\SkyrimVR\` — `CreationKit.exe`
   **1.5.73.0**, its CKPE, its `ckpe.log`, its
   `CreationKitPlatformExtended.toml`. There are at least five other
   `CreationKit.exe` copies on these drives; the one under
   `Skyrim Special Edition\` (1.7.99.0) is **not** the one being run, and
   analysing it produced a confident but WRONG conclusion — that CKPE did not
   support the editor — because 1.7.99 is genuinely absent from CKPE's version
   table while 1.5.73 is present. The real log says
   `Launch: Skyrim Special Edition [v1.5.73]`: CKPE recognises this build
   fully, so **there is no version mismatch**. Every CK RVA must be taken from
   the SkyrimVR 1.5.73 binary; RVAs differ per build, so the map further down
   this file (derived from 1.7.99) needs re-deriving before it is trusted.
1. **A real, measured defect came out of that log: 1,852 references deleted
   at load.** See "Top-level group order" below. Independent of the hang, and
   fixed.
2. **Stack scanning has been replaced by real unwinding.**
   `python tools/win_stackwalk.py --watch` walks every thread through
   DbgHelp `StackWalk64` (`.pdata` unwind tables, the same mechanism Windows
   SEH uses), so its frames are facts rather than candidates, and exported
   names resolve without PDBs. `--watch` is unattended: start it, launch CK,
   walk away; it follows the loading-phase text, and the moment CPU stays
   flat for `--idle-seconds` it dumps every thread plus the full window tree
   to `temp/hang_stacks.txt`. It also lists windows, which is how an
   invisible modal dialog — a hang that no stack walk describes as such —
   would show up. **Do this before diagnosing anything else.** The
   stack-scanning recipe in "Live-hang diagnostic methodology" below is kept
   only as history; do not repeat it.

Ordered plan from here:

Superseded by the diagnosis at the top of this file. Kept only as the record
of what was tried: `bThreads=false` and disabling the CKPE log window were
never needed.

## Top-level group order — 1,852 references silently deleted (FIXED)

Found by reading the CK warning log of the actual failing run
(`SkyrimVR\ckpe.log`, 2026-08-22 17:35), which nobody had looked at before:

```
[MASTERFILE] Missing/Invalid base object (0106035D) for reference (010A87E2)     ×1852
[MASTERFILE] Missing base object for ref '' (0105E279) in interior cell '…'.
             Ref will be deleted.                                               ×1850
```

Only **18 distinct base objects** account for all 1,852. All 18 exist in the
output — as **MSTT** records — and all 18 are Oblivion's skinned/havok statics:
`RootHavok01`–`07`, `ChainDoll01`/`02`, `ChainDollArena01`, `ArenaDummy1`,
`ArenaHeavyBag01`, `TargetHeavy01`, `ArenaColumn01`, `PrisonCellChains01`,
`NecroTapestrySkinned01`, `MDTapestrySkinned01`, `IronOutdoorLampHangingFake`,
`IronOutdoorLampHangingOff` — i.e. exactly the set `items.convert_STAT`
promotes from STAT to MSTT.

The records were fine; their **group was in the wrong place**. A REFR resolves
its `NAME` while the CELL group is being parsed, so a base-object type written
after CELL is not in the form map yet and the reference is dropped. Our
`Writer._group_order()` had no entry for `MSTT`, so it fell through to the
"append any groups not in the canonical order" tail and landed at index 65,
eleven groups past CELL. `PROJ` (#64) and `LTEX` (#67) were in the same tail —
`LTEX` matters because LAND names it in `ATXT`/`BTXT`.

Ground truth, measured from the real `Skyrim.esm` and `Dawnguard.esm` (they
agree): `…STAT SCOL MSTT PWAT GRAS TREE…`, `LTEX` right after `MGEF`/`SCPT`,
`PROJ` just before `HAZD`/`SLGM` — all comfortably before `CELL` — while
`WATR`, `FLST` and `MOVT` genuinely do come after it.

Fix: `MSTT`, `LTEX` and `PROJ` pinned at their vanilla slots in
`tes5_import/writer.py`'s `_group_order()`; `WATR`, `FLST`, `MOVT` pinned too
so the layout no longer depends on the order groups happened to be added in.

Guard: `python tools/esm_group_anchors.py <esm> --order-only` fails the build
on any signature vanilla places before `CELL` that we place after it. It
reproduced all three on the pre-fix output and passes on vanilla.

**This is not the hang** — CK logs these and keeps going — but it silently cost
1,852 placed objects in every build so far.

## What "Initializing References" actually is (decompiled 2026-08-22)

🛑 The RVAs below came from the **wrong binary** (`Skyrim Special
Edition\CreationKit.exe` 1.7.99.0). The *structure* they describe is right and
worth keeping, but re-derive the addresses against
`SkyrimVR\CreationKit.exe` 1.5.73.0 before using any of them — the entry
points are found the same way, via
`python tools/ck_strref.py --exe <ck> --pattern 'Initializing References'`.

RVAs are `CreationKit.exe` 1.7.99.0, imagebase `0x140000000`. Dump any of
these with `python tools/skyrim_disasm.py --exe <CreationKit.exe> --func <rva>`
— it prints the whole function from its `.pdata` bounds and annotates every
`lea reg,[rip+X]` with the string it points at.

- `0x1672a30` — the load driver. It walks form types in order, logging each;
  types `0x13` (SCPT), `0x47` (WRLD) and `0x5b` (FLST) are handled specially.
  Directly before this phase it initialises `0x2b` (NPC_) and `0x2c` (LVLN).
  At `0x1672eb8` it sets the dialog text to `"Initializing References..."`,
  then: **(a)** for every WRLD, call virtual `+0xE0`; **(b)** for every cell
  in a DataHandler array, call virtual `+0xE0`.
- `0x1cb4d70` — `TESWorldSpace` virtual `+0xE0`
  (`shared\tesforms\world\tesworldspace.cpp`). Pure FormID resolution:
  climate, encounter zone, location, water type, LOD water type, music type,
  parent landscape world. No loops. Not a hang site.
- `0x1bd30d0` — `TESObjectCELL` virtual `+0xE0`
  (`shared\tesforms\world\tesobjectcell.cpp`). This is the heavy one:
  - name-length check (>33 chars warns),
  - **dangling-cell check**: for each of the 9 cells in the 3×3 around it,
    look the neighbour up in the worldspace; >5 missing ⇒
    `"Cell at (%i, %i) in world %s (%08X) may be a dangling cell."`,
  - **invalid-coord check**: `abs(X) > 100 || abs(Y) > 100` ⇒ mark the cell
    deleted (record flag `|= 0x20`) or log
    `"Unable to delete invalid coord cell (%i, %i)"`,
  - iterate the cell's ref list, calling `TESObjectREFR` virtual `+0xE0`,
  - a second pass that re-homes refs whose cell lookup fails:
    `"Bad cell value. Attempting to force ref to (0, 0)"`, then
    `"-Unable to fix reference."`,
  - `"Cell '%s' (%08X) has a hand tagged location and an encounter zone
    applied location..."`.
- `0x1c31ac0` — `TESObjectREFR` virtual `+0xE0`, run once per reference
  (1,029,315 times for our plugin). Source of `"Ref (...) should be
  persistent but is not"` (the warning fix #1 below chased), plus
  `"Activate parent causes loop"`, `"Corrupt location/angle found on
  reference"`, `"Random teleport doors cannot have preexisting teleport
  data"`, `"Mapmarker ref does not have map marker data"` and friends.

Nothing in that path spins or blocks by itself, which is consistent with the
hang being a wait rather than a runaway loop — see item 1 above.

**New data finding (real, but almost certainly not the hang):** 3 exterior
cells sit at grid `(-345,-3)`, outside CK's ±100 range — one each in
`E3Kvatch`, `KvatchPlaza` and `KvatchEntrance`. `XCLC.X=-345` is in the TES4
export as well, so this is authored Bethesda leftover data carried through
faithfully, not a conversion defect; CK logs and moves on. Find them again
with `python tools/cell_grid_check.py <esm> --extents`.

## Status

- 4 real, verified data bugs found and fixed (below) — all confirmed correct
  independent of the hang, keep them regardless of what fixes the hang.
- The hang **persists unchanged** after all 4 fixes. Live-process stack
  traces (procdump + minidump analysis) are **byte-identical** across every
  attempt — same addresses in `CreationKit.exe`/`CKPE.SkyrimSE.dll`/
  `CKPE.Common.dll`, before and after each fix. This means none of the 4
  fixes touch the actual hang's trigger.
- Current leading theory (**unconfirmed**): the installed CKPE build
  (`CKPE.dll` FileVersion `0.6.267.0`) predates a "fully rewritten" engine in
  CKPE v0.6 build 1, and earlier CKPE changelogs (0.4 build 951) mention a
  "COM multithread patch" and "Fixed render locking issues" — the same class
  of bug (thread parked forever waiting on a lock/event that never signals)
  as what we're observing. **User is updating CKPE to v0.6 build 3 next —
  check back for that result before re-diagnosing from scratch.**
- If the CKPE update does NOT fix it: the hang is very likely NOT in our
  data at all (see "Why not our data" below) — stop looking for a 5th data
  bug and pivot to disassembling the actual stuck code path (methodology
  below, already set up and working).

## How to reproduce

```
python convert.py -f Oblivion.esm --import-only
```
Then open the output in CK (Skyrim.esm + DLC + our Oblivion.esm as active).
Hangs at "Initializing References" (visible in the Loading dialog's static
text), CPU/IO both flatline at ~0 (genuine deadlock, not slow processing).

## Live-hang diagnostic methodology (SUPERSEDED — history only)

🛑 Do not repeat this. `tools/win_stackwalk.py` does the same job with real
unwinding instead of stack scanning; see the top of this file. Kept because
the window-text and CPU/IO sampling tricks below are still how you tell a
hang from slow work.



The user added two tools to `references/` specifically for this:
`references/procdump/procdump64.exe` (Sysinternals) and `references/xEdit/`
(Pascal source — turned out **not** to contain CK's own "Finalize" logic;
xEdit is a separate tool. Useful for TES4/5 binary format questions only.)

1. **Confirm it's a true deadlock, not slow work** — sample CPU twice a few
   seconds apart:
   ```powershell
   $c1=(Get-Process -Id <pid>).CPU; Start-Sleep -Seconds 6
   $c2=(Get-Process -Id <pid>).CPU; "$($c2-$c1)"
   ```
   A real hang reads ~0 every time. Also check IO counters
   (`GetProcessIoCounters` via a small C# P/Invoke snippet) — both flatline.
2. **Read the Loading dialog's phase text** directly via Win32 (no debugger
   needed): `EnumWindows` for the process's `#32770` window titled
   `Loading...`, then `EnumChildWindows` for its `Static` child's text.
3. **Capture a full dump**: `procdump64.exe -accepteula -ma <pid> out.dmp`
   (~6 GB for this process; takes ~5s).
4. **Analyze it in Python** — `pip install minidump pefile capstone` (into
   whichever Python resolves on PATH; this machine has two, 3.11 and 3.14 —
   installed into both since the CLI tools ended up split across them).
   `minidump` parses the dump's thread list + module list; for each thread,
   read ~2000 stack slots from `Rsp` and flag any 8-byte value landing
   inside a "interesting" module's address range (`CreationKit.exe`, every
   `CKPE.*.dll`) — this is a **stack scan**, not real unwinding (no
   PDBs/unwind-info parsing was implemented), so treat hits as *candidates*:
   the closest ones to `Rsp` are most likely genuine live frames, distant
   ones can be stale leftover stack data from earlier unrelated calls (one
   candidate resolved to `GetWindowLongPtrA(hwnd, GWL_USERDATA)` — clearly
   stale, discarded).
   - Of CK's 147 threads, only **2** ever touch CK/CKPE code — the rest are
     generic parked thread-pool workers (`Sleep(INFINITE)` after
     `SetEvent()`, a normal idle-park pattern, not the hang).
   - One of the two is the main/UI thread — identify it via
     `GetWindowThreadProcessId` on the process's top-level windows (the one
     owning `Loading...`/`Object Window`/`Render Window` etc.).
5. **Resolve `call qword ptr [rip+disp]` (IAT) targets to import names**:
   `pefile` gives `DIRECTORY_ENTRY_IMPORT`; compute
   `target_rva = insn_rva + insn_len + disp` (stays in RVA-space throughout,
   the imagebase cancels out — don't re-subtract it, that was a bug in the
   first draft of this) and match against `imp.address - ImageBase`.
6. **Find CK's own message strings**: `tools/ck_strref.py --exe <CreationKit.exe>
   --pattern <regex>` — indexes every rip-relative `.text` reference to a
   matching `.rdata` string in one pass. This is how the exact source of the
   "should be refinalized" message (and 3 sibling "Bad portal navmesh
   ID/triangle index... needs to be refinalized" messages) was found. The
   printed hex is the **referencing instruction's** RVA, not the string's
   own address.
7. **Disassemble at an RVA**: `tools/skyrim_disasm.py --exe <path> --disasm
   <rva> --count N` (works against any PE, not just SkyrimSE.exe despite the
   name — pass `--exe` to override). `CreationKit.exe` is **not**
   DRM-packed (unlike the Steam SkyrimSE.exe), disassembles fine statically.

The stuck main-thread stack (RVAs, `CreationKit.exe` imagebase `0x140000000`):
closest frames are tiny CRT/atomic thunks (`GetCurrentThreadId`,
`lock cmpxchg`/`lock xadd` wrappers — C++ `<atomic>`/`<thread>` plumbing, not
application code); the second CK/CKPE-touching thread's closest frame is
`CKPE.Common.dll+0x2403f`, which is `SetEvent` immediately followed by
`Sleep(INFINITE)` — i.e. **that thread finished and parked itself
correctly**; it is not the blocker either. Neither thread's *closest* frames
explain the deadlock — the real wait is presumably in a frame stack-scanning
can't reach reliably (proper unwinding via `.pdata`/`RtlVirtualUnwind` was
not implemented; would be the next step if disassembly work resumes).

## The 4 confirmed data bugs (fixed, keep regardless of hang outcome)

All four follow the same shape: Oblivion's engine tolerates something
Skyrim's doesn't; CK's own load-time validator detects it, tries to
auto-fix/revalidate it, and — at large-enough scale — CK's own auto-fix
hangs instead of completing. (`import_main.py`'s pre-existing "Re-home
misplaced exterior refs" comment already documented one instance of exactly
this pattern before this session; it's a recurring category in this project,
not a one-off.)

1. **Cross-cell-boundary statics not persistent** (`tes5_import/import_main.py`,
   in `_build_world_groups`, right after `achr_by_cell` is built). Oblivion
   never needs a placed reference to be Persistent just because its bounds
   cross into a neighbouring cell; Skyrim's cross-cell rendering does. CK
   warns `Ref (...) ... should be persistent but is not` for these (matches
   the literal warning text at `CreationKit.exe` RVA `0x1dd245e`, found via
   `ck_strref.py`). Fix: for every exterior REFR, compute a worst-case
   rotation-invariant radius from its base STAT/TREE's OBND (`_resolve_obnd`
   — the same source `convert_STAT`/`convert_TREE` use for their own
   OBND/LOD-flag decisions) and force the Persistent flag when the ref sits
   within that radius of a cell edge. **74,444 refs forced persistent.**
   Verified structurally sound (`tools/verify_ck_fixes.py` — XLCN-vs-location
   check still passes).

2. **Re-homing didn't cover persistent refs, and didn't fix `ParentCELL`**
   (same function, "Re-home misplaced exterior refs" block, right after #1).
   Two bugs in the pre-existing re-homing logic, both exposed by #1:
   - It skipped any ref with the Persistent flag set, on the (wrong)
     assumption that persistent refs "load by worldspace, not by grid" — true
     only for a worldspace's own dummy/persistent CELL (already excluded
     structurally, since such cells aren't in the `cell_grid` map at all);
     false for a ref that's merely *flagged* persistent while still living in
     an ordinary grid-tiled cell (exactly what #1 creates in bulk). Fix:
     dropped the `not persistent` condition — the outer `cell_grid` filter
     already does the right exclusion on its own.
   - Moving a ref between `by_cell` buckets never updated its `ParentCELL`
     field. Harmless while only non-persistent refs were re-homed (nothing
     downstream reads `ParentCELL` for temp refs), but persistent refs get an
     XLCN via `_reference_location()` in `tes5_import/record_types/world.py`,
     which reads `ParentCELL` directly — stale, it resolves the WRONG
     cell's Location, producing CK's `Ref is not in its persistence
     location` warning. Fix: write back the **raw TES4-space** FormID string
     (not the already-remapped output int — `get_formid()` remaps on every
     read, so storing the remapped value double-remaps it next read) via a
     parallel `grid_cell_raw` map. **38 refs re-homed** (both fixes needed
     together — 8 of the 38 weren't persistent and were already being
     correctly re-homed before; the other 30 needed both changes).

3. **One asymmetric ledge portal link** (`tes5_import/pgrd_to_navm.py`,
   `_pack_nvnm`'s ledge-linking loop). `_open_edge_towards` is evaluated
   independently for each side of a hi/lo ledge-drop pair; geometry can make
   one side resolve an open facing edge while the other doesn't, producing a
   ONE-SIDED link. CK's validator explicitly rejects this (`ck_strref.py`
   found 3 sibling format strings: "Bad portal navmesh ID/triangle index...
   the cell needs to be refinalized", RVAs `0x2a427da`/`0x2a42858`/
   `0x2a42a51`). Measured: **1 of 234,612** portal links in a full
   Oblivion.esm conversion was one-sided (`FortRayles`/`0x1015e31`). Fix:
   both directions must resolve before either is committed; a half-resolved
   pair is dropped entirely (that lip just gets no fall-through, same as any
   other unlinked ledge). Verified via a full portal-reciprocity scan of the
   output (script logic below) — 0/234,536 asymmetric after the fix.

4. **`navm_split.py` split every disconnected interior, not just the ones
   that need it.** `split_disconnected_interiors` exists to fix a real,
   confirmed bug (CharacterGen assassins' holding-room/balcony teleport door
   pair, both ends in one NAVM — the engine can only portal a same-cell door
   pair across TWO different NAVM records). It was splitting on
   `ncomp > 1` alone, with no check that a door pair actually spans the
   split. Measured: of 352 interior cells with >1 connected navmesh
   component, only **18** have a same-cell door pair whose two ends land in
   different components; the other 334 are just cells with disconnected
   walkable areas that never interact via a teleport door, needlessly split.
   Fix: pass `door_xtel_target` (REFR FormID → its XTEL door-target FormID,
   built once in `import_main.py` from `by_type['REFR']`) into
   `split_disconnected_interiors`; skip the split unless some door on the
   navmesh has its XTEL target on a *different* component of the same
   navmesh. **352 → 18 splits.** This did NOT change the hang (see Status) —
   kept anyway because it's strictly more correct (matches the feature's own
   stated purpose) and reduces unnecessary CK/engine work regardless.

## Ruled out (don't re-try without new evidence)

- **CKPE `bRefLinkGeometryHangWorkaround`** (its own named fix for
  bookshelf/`Select Enable State Parent` hangs) — set `true`, retested live,
  identical hang. Currently `false`.
- **CKPE `bBSPointerHandleExtremly`** ("increase max refs to 8,388,608 ...
  for synchronization between threads") — set `true`, retested live,
  identical hang; also has a documented downside (reduces a per-ref
  duplicate-tracking limit to 255) that could hurt a landscape this dense
  with reused STAT bases, so left it `false` rather than keep it "just in
  case". Total refs in our output alone: **1,029,315** (REFR 1,017,647 +
  ACHR 11,668) — big, but the flag made no measurable difference either way.
- **Structural corruption in our own output**: `tools/verify_ck_fixes.py`
  passes (only known-benign fail: `NAVI` at `0x00012fb4`, a deliberate
  low-FormID singleton per `navi_builder.py`, not a real override). Checked
  and ruled out separately: XESP/XLKR/LCTN-PNAM cycles or dangling targets
  (none), NaN/Inf or degenerate REFR/ACHR positions and scales (none), every
  NAVM has exactly one matching NVMI in NAVI (8787/8787, 1:1).
- **The vanilla `MilitaryCampFalkreathImperial03` "should be persistent"
  warning the user originally pointed at**: 100% vanilla FormIDs (master
  index `00` = Skyrim.esm). It is deterministically the LAST line of the
  persistence-check pass (regardless of what else is loaded) — a coincidence
  of iteration order, not the hang site. Loading Skyrim.esm+DLC alone (no
  our plugin) does NOT hang — confirms the hang needs our plugin present,
  but this specific line is a red herring for *where*.

## Why not our data (the case for a CKPE-side bug)

The live-thread stack signature is **byte-identical** across 4 builds with
substantively different content (before any fix; after fix #1 alone — which
got further, printing *additional* CK warnings about our data, before
hanging later — see next paragraph; after fixes #1+#2 together, which
regressed to hanging at the same point as before any fix; after #1+#2+#3;
after #1+#2+#3+#4). If the trigger were IN the data these fixes touch, the
stack should have moved. It never did, except for one data point that looks
like noise: the #1-only build (before #2) printed several of our own
`Reference attached to wrong cell for its location` / `Ref is not in its
persistence location` warnings that don't appear in any other build — almost
certainly because #2 fixed the specific refs that caused them, not because
that build got structurally further. Total warning-log length only ever
grew (5023 → 5163 → more) as fixes landed, consistent with "more of our
data now passes validation cleanly", never with "the hang moved".

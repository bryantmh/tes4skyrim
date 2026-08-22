---
name: port-ck-load-fixes
description: >-
  Port the 2026-08-22 Creation Kit load fixes into another checkout of this
  TES4→TES5 converter. Covers the "Initializing References" hang (a null-deref
  crash CKPE turns into a 0%-CPU spin-lock stall, caused by an unchecked XTEL
  destination grid lookup), the top-level GRUP-order bug that made CK delete
  1,852 references, cross-cell persistence, exterior ref re-homing, one-sided
  ledge portal links, over-broad navmesh splitting, and the WATR DATA/DNAM
  swap. Use when someone's converted plugin hangs or crashes the CK on load,
  when placed objects go missing from a converted plugin, or when merging an
  older fork of this project up to the fixed pipeline. Also use when asked to
  diagnose ANY hang of Creation Kit, Skyrim, or Oblivion — it carries the
  stack-walk-first methodology that solved this one.
---

# Porting the CK load fixes

**This skill folder is self-contained.** Everything it needs — the updated
tools, the patches, the write-ups — ships in `references/`. Copy the folder
into the target checkout's `.claude/skills/` and nothing else has to travel
with it.

Seven independent defects, each verifiable on its own. **Check before you
patch** — a fork may already have some. Every check runs offline against a
built `.esm`; none needs the Creation Kit.

## What is in `references/`

| Path | What it is |
|---|---|
| `apply_patches.py` | **Git-free patcher.** `--check` dry-runs, `--apply` writes (keeping `.orig` backups), `--tools` drops the bundled `tools/` and `docs/` into place. The converter is not kept in a git repository, so `git apply` is not available everywhere. |
| `*.diff` | Unified diffs of the six pipeline files, against a pristine 0.606. `__` stands for a path separator. These are the source of truth for the exact edits. |
| `tools/*.py` | **Whole files, ready to copy over the target's `tools/`.** Standalone scripts — no imports from the project — so they work regardless of how far the fork has drifted. |
| `docs/*.md` | The full investigation and the EN/RU changelog. Copy into the target's `docs/` so the next person inherits the reasoning. |

---

## Step 0 — install the tools FIRST

Do this before anything else. The two gates below live in `tools/`, and on an
unpatched fork they exist but lack the new flags (verified on 0.606:
`error: unrecognized arguments: --teleport-cells`, `--order-only`), while
`win_stackwalk.py` is absent entirely.

From the target checkout's root:

```bash
python .claude/skills/port-ck-load-fixes/references/apply_patches.py --tools
```

`cell_grid_check.py`, `esm_group_anchors.py` and `ck_strref.py` are supersets
of the old versions — copying over them loses nothing. `win_stackwalk.py` and
the new `skyrim_disasm.py` flags are additive.

`skyrim_disasm.py` needs `pefile` and `capstone`; `win_stackwalk.py` needs
nothing but Windows. The two gates need neither.

---

## Order of work

1. Step 0 above.
2. Run the two gates (§1) against a plugin the fork has already built.
3. Apply the patches (§5) — the gates say which defects are present, but apply
   all of them: the five in §4 are silent and every one is correct on its own.
4. Rebuild: `python convert.py -f <plugin> --import-only`.
5. Re-run the gates; both must exit 0.
6. Confirm no FormID drift: `python -m pytest tests/test_formid_determinism.py`.

`_group_order()` and XTEL contents do **not** feed `derive_formid`, so none of
this moves a FormID. Verify anyway — the save-game contract is not negotiable.

If the fork has no built `.esm` to test against, apply the fixes from the
diffs, build once, then gate.

---

## 1. The two gates

```bash
python tools/cell_grid_check.py <esm> --teleport-cells   # defect A — the hang
python tools/esm_group_anchors.py <esm> --order-only     # defect B — lost refs
```

Both exit non-zero on failure and are cheap enough to gate a build.

---

## 2. Defect A — the hang: unchecked XTEL destination grid lookup

**Symptom.** CK freezes forever at "Initializing References" at ~0% CPU. Looks
exactly like a deadlock. It is not: a thread crashed, CKPE's handler parked
it, and the threads that wanted its `BSSpinLock` spin on `Sleep(1)` forever.

**Proof it is this defect.** `<game>\CreationKitPlatformExtendedCrashReport.log`
shows:

```
EXCEPTION_ACCESS_VIOLATION at CreationKit.exe+134BEDA   mov eax, [rax+0x10]
Tried to read memory at 0x0000000000000010
R9 = "..\Shared\TESForms\World\TESObjectCELL_Reference.cpp"
```

**Mechanism.** CK's teleport-data init (`CreationKit.exe` 1.5.73 `+0x15f0580`)
null-checks the linked door and the door's parent cell, then — for a door in
an **exterior** cell — re-derives the destination cell from the worldspace
grid using **the XTEL's own coordinates**, and passes the result on at
`+0x15f0893` with **no null check**. Empty grid square ⇒ null ⇒ crash.

**Patches:** `references/tes5_import__record_types__world.py.diff`,
`references/tes5_import__import_main.py.diff`,
`references/tes5_import__convert_worker.py.diff`.

`tes5_import/record_types/world.py` — module state and setter:

```python
_WORLD_GRID_CELLS: set = set()
_DOOR_PLACEMENT: dict = {}

def set_teleport_grid(grid_cells, door_placement: dict):
    _WORLD_GRID_CELLS.clear()
    _WORLD_GRID_CELLS.update(grid_cells or ())
    _DOOR_PLACEMENT.clear()
    _DOOR_PLACEMENT.update(door_placement or {})
```

…and in `convert_REFR`, inside the `if xtel_door:` block, after `rz` is
computed and **before** the `pack_subrecord('XTEL', …)` call:

```python
dest = _DOOR_PLACEMENT.get(xtel_door)
if dest:
    dw, dx, dy, dz = dest
    if dw and (dw, _ref_grid(px), _ref_grid(py)) not in _WORLD_GRID_CELLS:
        px, py, pz = dx, dy, dz
```

`tes5_import/import_main.py` — build the index and call the setter just after
`set_cell_locations(...)`, before the CELL/WRLD phase. The builder **must**
consult `ctx.master_export` as well as `by_type`: a dependent plugin's door
routinely teleports into a master-owned worldspace, and a door missing from
the map silently disables the check. Index every CELL with a `ParentWRLD` that
is not flagged `0x400`, keyed `(wrld, XCLC.X, XCLC.Y)`; index every REFR
carrying `XTEL.Door` as `fid -> (ParentWRLD, PosX, PosY, PosZ)`.

`tes5_import/convert_worker.py` — add `teleport_grid` / `door_placement`
parameters to `init_worker` and call `set_teleport_grid` there, and append
`set(world_mod._WORLD_GRID_CELLS)` and `dict(world_mod._DOOR_PLACEMENT)` to
the pool's `initargs` in `import_main.py`. **Skipping this makes the fix work
in the parent and silently do nothing in worker processes** — the same class
of bug as a master-blind index.

**Rationale for the fallback value.** The target door's own position is the
authored indicator. Oblivion teleports to the target door's parent cell and
never uses the XTEL coordinates to find a cell, which is why its data can hold
pairs that disagree — `DAPeryiteDoorTEMPREF` stores grid (0,0) while its
partner stands at grid (16,16) in a worldspace spanning x[4,36] y[5,26].

---

## 3. Defect B — a base-object GRUP written after `CELL`

**Symptom.** No crash. `ckpe.log` fills with
`Missing/Invalid base object (…) for reference (…)` and
`Ref will be deleted`, and the objects are simply absent in the editor.

**Mechanism.** A REFR resolves its `NAME` *while the CELL group is parsed*, so
a base-object type whose top-level GRUP comes later is not in the form map yet
and the reference is dropped. `Writer._group_order()` ends with "append any
groups not in the canonical order" — anything in that tail lands after
`CELL`/`WRLD`.

**Patch:** `references/tes5_import__writer.py.diff`.

In `_group_order`, give every placeable type an explicit slot. At minimum
`MSTT` (right after `STAT`), `LTEX` (right after `MGEF`), `PROJ` (just before
`SLGM`). Pin `WATR`, `FLST`, `MOVT` too — they legitimately come after `CELL`,
but pinning stops the layout depending on the order groups happened to be
added in.

Canonical order is measured from the real `Skyrim.esm` and `Dawnguard.esm`,
which agree: `…STAT SCOL MSTT PWAT GRAS TREE…`, `LTEX` after `MGEF`/`SCPT`,
`PROJ` before `HAZD`/`SLGM`, all before `CELL`. Re-derive it any time with
`tools/esm_group_anchors.py --reference <Skyrim.esm>`.

**Never let a placeable type reach the leftover tail.**

---

## 4. The five quieter fixes

Apply these too; each is correct on its own and none of them was the hang.

| # | File / patch | Fix |
|---|---|---|
| C | `import_main.py`, `_build_world_groups` | Force Persistent on exterior REFRs whose base STAT/TREE OBND radius (`_resolve_obnd`, `hypot(rx, ry)`) reaches past a cell edge. Skyrim's cross-cell rendering needs it; Oblivion never set it. **74,444 refs.** |
| D | `import_main.py`, "Re-home misplaced exterior refs" | Drop the `not persistent` gate (the `cell_grid` filter already excludes worldspace dummy cells), and write `ParentCELL` back as the **raw TES4-space** FormID string — `get_formid()` remaps on every read, so storing the remapped int double-remaps it. Both halves are needed. **38 refs.** |
| E | `tes5_import__pgrd_to_navm.py.diff` | Commit a ledge pair only when **both** directions resolve an open edge; drop a half-resolved pair. CK rejects one-sided links by name ("Bad portal navmesh ID/triangle index … needs to be refinalized"). **1 of 234,612.** |
| F | `tes5_import__navm_split.py.diff` | `split_disconnected_interiors` takes `door_xtel_target` and skips the split unless a door's XTEL target lands on a *different* component of the same navmesh. **352 → 18 splits.** |
| G | `tes5_import__record_types__world.py.diff` | `convert_WATR`: `DATA` is a 2-byte Damage value; the 228-byte visuals struct belongs in `DNAM`. The old code had the big struct under `DATA` plus a bogus 196-byte `DNAM`, which SSEEdit flagged on every WATR record. |

C and D are both in `references/tes5_import__import_main.py.diff`, alongside
the defect-A hunks.

---

## 5. Applying the patches

From the target checkout's root:

```bash
P=.claude/skills/port-ck-load-fixes/references/apply_patches.py
python $P --check     # dry run: does every patch still apply here?
python $P --apply     # write them, keeping a .orig backup beside each file
```

Verified end to end: applied to a pristine 0.606 all six patches reproduce the
fixed files **byte for byte**. Re-running is safe — a file that is already
patched is reported `SKIP (already patched)`, not failed — and `--check` exits
non-zero if anything would fail, so it can gate a merge.

A `FAIL` names the exact line and what it expected there. That means the fork
has drifted. Do **not** force it — open the diff, read the hunk, and make the
same edit by hand at the right place. Every fix above is described in prose
precisely so that stays possible. Then rebuild and let the gates decide.

(`git apply` works too where the checkout is a git repo; this project's is
usually not, which is why the patcher ships with the skill.)

---

## 6. Methodology — for the next hang, whatever it is

This one cost four rounds of confident wrong conclusions before the method
changed. Do these in order.

1. **Walk the stack for real.** `python tools/win_stackwalk.py --watch`, then
   launch the app. It waits for the process, follows the loading-phase text,
   and dumps every thread plus the window tree to `temp/hang_stacks.txt` once
   CPU stays flat. Frames come from DbgHelp `StackWalk64` — the module's own
   `.pdata` unwind tables — so they are facts.
   🛑 **Never go back to scanning a minidump's stack bytes for values that
   land inside a module.** It cannot distinguish a live return address from
   stale leftovers, and it is what produced every wrong answer here.
2. **Read the crash log.** `CreationKitPlatformExtendedCrashReport.log` and
   `ckpe.log` sat unread for a day and each held a decisive fact. **A CKPE
   "hang" at 0% CPU is a crash until proven otherwise** — CKPE's handler parks
   the faulting thread, so the symptom is a stall, not a crash dialog.
3. **`Sleep` in the stack ≠ a kernel wait.** `BSSpinLock::Lock` degrades to
   `Sleep(1)` after 10,000 spins, which reads as 0% CPU. Two threads at the
   same call site means a third one died holding the lock.
4. **Turn RVAs into meaning.**
   `python tools/skyrim_disasm.py --exe <exe> --func 0x<rva>` disassembles the
   whole enclosing function using `.pdata` bounds and annotates every
   `lea reg,[rip+X]` with the string it points at — the engine's own error
   text becomes a map of what each branch tests. Assert paths like
   `..\Shared\TESForms\World\TESObjectCELL_Reference.cpp` name the subsystem
   outright. `tools/ck_strref.py --exe <pe> --pattern <regex>` goes the other
   way: which code references a given message.
5. **Confirm you are reading the right binary.** Several `CreationKit.exe`
   copies usually exist. Check the one actually launched, and check what CKPE
   says in `Logs\CKPE\CreationKitPlatformExtended.log` (`Launch: …`). Reading
   the wrong exe here produced a confident, entirely wrong "CKPE doesn't
   support this build" conclusion.
6. **Check the window tree.** `--windows` in `win_stackwalk.py`. A "hang" is
   often an invisible modal dialog, and no stack says that as plainly.

---

## 7. Do not copy between checkouts

`conversion_config.json`, `.conversion_state.json`,
`.claude/settings.local.json` and any `*.log` hold machine-local paths and
state.

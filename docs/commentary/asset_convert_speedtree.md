# asset_convert/speedtree/spt_generator.py - SpeedTree conversion

**Code:** `asset_convert/speedtree/spt_generator.py`, `asset_convert/speedtree/spt_engine_geom.py`, `asset_convert/speedtree/spt_parser.py`, `tools/disasm/oblivion_disasm.py`

## Contents

- [1. Locating SpeedTree inside Oblivion.exe](#1-locating-speedtree-inside-oblivionexe)
- [2. The CSpeedTreeRT API surface (all 29 markers mapped)](#2-cspeedtreert-api-surface)
- [3. VERIFIED: the parser is correct](#3)
- [4. VERIFIED: leaves are single quads in the engine](#4)
- [5. DEFECTS in spt_generator.py (measured, 139 Oblivion.esm SPTs)](#5-defects-sptgeneratorpy)
- [6. Growth model — status](#6-growth-model-status)
- [6b. THE RANDOM NUMBER GENERATOR — fully recovered](#6b-random-number-generator-fully)
- [6c. Placement rules recovered from the generation sites](#6c-placement-rules-recovered-from)
- [6d. SPT parse-stage map (error string → code site)](#6d-spt-parse-stage-map)
- [6e. BezierSpline evaluation — fully recovered (0x784210)](#6e-bezierspline-evaluation-fully-recovered)
- [6f. The branch builder — 0x7925b0](#6f-branch-builder-0x7925b0)
- [6g. The leaf system — parser + placement](#6g-leaf-system-parser-placement)
- [6h. Remaining parse blocks verified against the engine](#6h-remaining-parse-blocks)
- [6i. 🛑 PARSER BUG FOUND: section 7000 is a COUNT, not a bare marker](#6i-parser-bug)
- [6j. The Compute() pipeline — full stage order](#6j-compute-pipeline-full-stage)
- [6k. Leaf attachment: branch arc-length accumulation (0x79a000)](#6k-leaf-attachment-branch-arc)
- [6l. 🛑 Position-along-branch is ARC-LENGTH PARAMETRIC, not index-linear (0x78f720)](#6l-position-along-branch-arc)
- [6m. BRANCH SHAPE — the per-child evaluation sequence (0x7925b0)](#6m-branch-shape-per-child)
- [6n. 🛑 THE RING LOOP — seg_pack warps ring spacing (ACTIONABLE)](#6n-ring-loop-segpack-warps)
- [6o. 0x78feb0 is the TUBE CROSS-SECTION EMITTER — not a bend integrator](#6o-0x78feb0-tube-cross-section)
- [6p. 🛑 THE REAL BEND CODE — FOUND (0x793206 → 0x78f160 → 0x78edd0)](#6p-real-bend-code)
- [6q. 🛑 THE COMPLETE GRAVITY BEND MODEL — CLOSED](#6q-complete-gravity-bend-model)
- [6r. Implemented so far (2026-08-21) — ENGINE-DERIVED ONLY](#6r)
- [6s. 🛑 FLARES — FULLY RECOVERED (0x791e80, 0x78f2c0, 0x790138)](#6s-flares-fully-recovered)
- [6t. 🛑 LEAF MAP RECORD + CARD SIZE — RECOVERED](#6t-leaf-map-record-card)
- [6u. 🛑 LEAF MAP SELECTION IS UNIFORM — blossom sections are DEAD](#6u-leaf-map-selection-uniform)
- [6v. Child count re-verified through the argument frame — count = freq * stored_length](#6v-child-count-re)
- [6x. LEAF GEOMETRY IS EXTRACTABLE — the leaf sub-struct (MEASURED)](#6x-leaf-geometry-extractable-leaf)
- [6y. 🛑 LEAF CARD SIZE — formula proven, BASE TERM STILL OPEN](#6y-leaf-card-size-formula)
- [6z. 🛑 THE LOD-0 STRIP LIST IS INCOMPLETE — orphan vertex blocks](#6z-lod-0-strip-list)
- [6aa. 🛑 "NO LEAF CHUNK" vs "ZERO LEAVES" — an ambiguity that grew foliage](#6aa-no-leaf-chunk-vs)
- [6w. 🛑 LEAF ORIENTATION — RECOVERED (0x79a301-0x79a36c)](#6w-leaf-orientation-recovered)
- [8. 🛑 DRIVING THE REAL ENGINE — the ground-truth harness](#8-driving-real-engine-ground)
- [7. Decomp progress / next targets](#7-decomp-progress-next-targets)

**Goal**: reproduce an Oblivion tree's geometry exactly as the engine builds it,
minus the leaf BILLBOARDS (camera-facing quads, impossible in Skyrim — we
substitute crossed quads). Everything else — trunk, branches, leaf placement,
UVs, LOD — should be a faithful port of the engine's own algorithm, not a
heuristic fitted to billboard renders.

**Ground truth is `Oblivion.exe`**, which statically links SpeedTreeRT 4.x with
symbols intact. This outranks `references/spttools-master/FORMAT` (a
reverse-engineered GPL doc), the CAD notes, and ck-cmd. Where they disagree,
the exe wins.

> **The billboard renders are NOT ground truth for geometry.** They are a 2D
> projection, and the current generator was already fitted to them by image
> comparison — so an image A/B can never reveal a 3D structural error. Use the
> exe. (This supersedes the "ALWAYS compare against the billboards" advice in
> [nif_conversion_notes.md](asset_convert_nif.md).)

---

## 1. Locating SpeedTree inside Oblivion.exe
<a id="1-locating-speedtree-inside-oblivionexe"></a>

Binary: `D:\Other Games\Nehrim At Fate's Edge\Oblivion.exe` (7,549,440 bytes,
x86-32, image base `0x400000`).

| Section | VA range | Raw range |
|---|---|---|
| `.text` | `0x401000`–`0xa28000` | `0x400`–`0x627200` |
| `.rdata` | `0xa28000`–`0xb02000` | `0x627200`–`0x700c00` |
| `.data` | `0xb02000`–`0xbac000` | `0x700c00`–`0x731800` |

Markers proving the static link:

- `__IdvSpt_02_` at file `0x68ba24` (VA `0xa8c824`) — the SPT magic.
- `malformed SpeedTree SPT file` (VA `0xa8c8f4`), `not a valid SpeedTree SPT file`
  (VA `0xa8c914`).
- 29 `CSpeedTreeRT::*` `__FUNCTION__` markers (see §2).
- RTTI: `.?AVCTreeEngine@@`, `.?AVCBillboardLeaf@@`, `.?AVCIdvCamera@@`,
  `.?AVIdvFileError@@`.

The SpeedTree code occupies roughly **VA `0x788000`–`0x7b8000`**.

### Section-ID dispatch uses CHAINED SUBTRACTION, not immediates

Searching for section ids as `cmp` immediates finds nothing. MSVC compiled the
switches as successive `sub`/`add` + jump tables. This is why a naive constant
scan appears to show the ids are absent.

Top-level SPT loader at **`0x7a4f81`**:

```
007a4f83  call 0x78eb40          ; read_int32()  -> section id
007a4f88  sub  eax, 0x3ea        ; 1002 -> trunk/branches block
007a4f8d  je   0x7a4fb9          ;   handler 0x7a4950
007a4f8f  sub  eax, 2            ; 1004 -> leaves block
007a4f92  je   0x7a4fab          ;   handler 0x7a6020
007a4f94  sub  eax, 7            ; 1011 -> section-5000 block
007a4f97  jne  0x7a5074          ;   else -> "malformed SPT" throw
...
007a4fc8  cmp  eax, 0x3e9        ; loop until 1001 (end of 1000)
007a4fe6  cmp  eax, 0x1b58       ; then optional 7000 block -> 0x7a42d0
```

### Reader primitives (call these to identify field types)

| Address | Meaning |
|---|---|
| `0x78eb40` | `read_int32()` — returns in `eax` |
| `0x78eb10` | `read_float32()` — returns on FPU stack (`fstp`) |
| `0x78ea30` / `0x78ea00` | RNG seed / range helpers |
| `0x7909d0` | `read_intstring()` (length-prefixed string, used for BezierSplines) |
| `0x78ebf0` | peek/lookahead id |

---

## 2. The CSpeedTreeRT API surface (all 29 markers mapped)
<a id="2-cspeedtreert-api-surface"></a>

Each marker string is `push`ed inside the function that implements it, so the
referencing address is inside that function.

| Function | Ref VA |
|---|---|
| `Compute` | `0x78d0a1` (body starts `0x78cca0`) |
| `LoadTree` | `0x78e290` |
| `GetGeometry` | `0x78c7a0` |
| `ComputeLodLevel` | `0x78c075` |
| `ComputeLeafStaticLighting` | `0x78c43e` |
| `GetLeafBillboardTable` | `0x78c2e5` |
| `GetLeafLodSizeAdjustments` | `0x78a7f5` |
| `SetTreeSize` | `0x78b18c`, `0x78b294` |
| `SetCamera` | `0x78d357` |
| `SetTime` | `0x78d453` |
| `SetLeafRockingState` | `0x78b791` |
| `SetBranchWindMethod` / `SetLeafWindMethod` / `SetFrondWindMethod` | `0x78ba04` / `0x78b8b2` / `0x78bb54` |
| `SetBranchLightingMethod` / `SetLeafLightingMethod` / `SetFrondLightingMethod` | `0x78b3b1` / `0x78b4d1` / `0x78b5f2` |
| `SetWindStrength` / `SetNumWindMatrices` / `SetLocalMatrices` | `0x78bcff` / `0x78bdde` / `0x78bf15` |
| `DeleteBranchGeometry` / `DeleteFrondGeometry` | `0x78c1dc` / `0x789f40` |
| `MakeInstance` / `NotifyAllTreesOfEvent` / `GetTextures` | `0x78dc8d` / `0x78c64e` / `0x78aa5e` |

`Compute` body (`0x78cca0`) calls, in order:
`0x7a24f0` (seed setup) → `0x7a45f0` (size, passes `[ebx+0x24]`) →
**`0x7a1cd0` (tree build)** → `0x7a5740` → `0x793c00` → …

---

## 3. VERIFIED: the parser is correct
<a id="3"></a>

### 3.1 Per-level 6000–6017 block — exact match

Level parser at **`0x7a7900`**, reached from the 1016 container (`0x7a4190`)
which loops `call 0x7a7900` once per level:

```
007a79be  call 0x78eb40           ; section id
007a79c5  add  eax, 0xffffe890    ; -6000
007a79ca  cmp  eax, 0x11          ; 18 entries (6000..6017)
007a79cd  ja   0x7a7c68
007a79d3  jmp  dword ptr [eax*4 + 0x7a7cbc]
```

Jump table at `0x7a7cbc` decodes to **exactly 6000–6017 in order**:

| sid | handler | sid | handler |
|---|---|---|---|
| 6000 | `0x7a79da` | 6009 | `0x7a7b98` |
| 6001 | `0x7a7a0a` | 6010 | `0x7a7ba7` |
| 6002 | `0x7a7a3a` | 6011 | `0x7a7bb6` |
| 6003 | `0x7a7a6a` | 6012 | `0x7a7bc2` |
| 6004 | `0x7a7aca` | 6013 | `0x7a7bce` |
| 6005 | `0x7a7afa` | 6014 | `0x7a7bda` |
| 6006 | `0x7a7b2a` | 6015 | `0x7a7be6` |
| 6007 | `0x7a7b5a` | 6016 | `0x7a7c11` |
| 6008 | `0x7a7b8a` | 6017 | `0x7a7a9a` |

Field types + struct offsets confirmed by reading the handlers:

| sid | code | type | level-struct offset |
|---|---|---|---|
| 6008 cross_segments | `call 0x78eb40; mov [edi], eax` | int32 | `+0x00` |
| 6009 length_segments | `call 0x78eb40; mov [edi+4], eax` | int32 | `+0x04` |
| 6010 child_first | `call 0x78eb10; fstp [edi+8]` | float | `+0x08` |
| 6011 child_last | `call 0x78eb10; fstp [edi+0xc]` | float | `+0x0c` |
| 6012 child_freq | `call 0x78eb10; fstp [edi+0x10]` | float | `+0x10` |
| 6013 u_tile | `call 0x78eb10; fstp [edi+0x14]` | float | `+0x14` |
| 6014 v_tile | `call 0x78eb10; fstp [edi+0x18]` | float | `+0x18` |
| 6015/6016 u_abs/v_abs | byte path | byte | — |
| 6000-6007, 6017 | `call 0x7909d0` (intstring) | BezierSpline | — |

Each level object is allocated with `push 0x74; call malloc` at `0x7a792d` —
**level struct is 0x74 = 116 bytes**.

**Conclusion: `spt_parser.py` matches the engine on the entire 6xxx block.**

### 3.2 Section 2001–2007 — exact match

In `0x7a4950`, `add eax, 0xfffff82f` (= −2001), `cmp eax, 6`, jump table at
`0x7a4b5c`:

| sid | handler | | sid | handler |
|---|---|---|---|---|
| 2001 | `0x7a4a9c` | | 2005 | `0x7a4a7f` |
| 2002 | `0x7a4ab1` | | 2006 | `0x7a4acf` |
| 2003 | `0x7a4a90` | | 2007 | `0x7a4adb` |
| 2004 | `0x7a4aa8` | | | |

Also in the same function: `cmp eax, 0x7d0` (2000, bark texture intstring) and
`cmp eax, 0x3f6` (1014, level count → loops `0x7a4190`).

### 3.3 Seed handling (section 2005) — `0x7a24f0`

```
if (seed == 0) { seed = rand_range(2.0, 1000000.0); }   // 0x7a2508 loads 1e6
else if (seed != 1) { this->seed = seed; }
```
Stored at `[esi+0x48]`. Seed 1 is a sentinel meaning "leave current".

**Seed 0 is not reproducible under `spt_engine_dump.exe`.** `rand_range` first
calls `0x78ea30(-1)`, which divides an uninitialized stack slot (`fild [esp]`
at `0x78ea91` — the `push ecx` at entry, i.e. whatever `ecx` held) by
`time(NULL)` and seeds the RNG from that. The result depends on heap addresses
and the clock. Measured 2026-09-23 on the 2 seed-0 dumps in
`export/Oblivion.esm/trees/spt_engine_dumps/` (`shrubvinemaplesu_0`,
`treesugarmapleforestsu_0`): neither the old nor the rebuilt harness reproduces
the cached file. The old build repeated itself run to run; the rebuilt one
does not. The 143 dumps with a non-zero seed are byte-identical across both
builds.

### 3.4 Scale — `WORLD_SCALE = 10.0` CONFIRMED

ck-cmd (`references/ck-cmd-master/src/spt/sptconvert.cpp:2446`):
```cpp
speedTree.GetTreeSize(fSize, fVariance);
speedTree.SetTreeSize(fSize * (10), fVariance);   // feet -> Oblivion units
speedTree.SetTextureFlip(true);                    // confirms v_dds = 1 - v_tga
speedTree.SetDropToBillboard(true);
```
Our `WORLD_SCALE=10` is right. **Note `fVariance` (section 2007) is passed
too — we drop it** (see §5, Finding G).

---

## 4. VERIFIED: leaves are single quads in the engine
<a id="4"></a>

`references/ck-cmd-master/src/spt/sptconvert.cpp:1479-1503` reads
`leaves.m_pLeafMapCoords[cluster_index]` as **4 corners (stride 4 floats)** per
leaf, one quad per `m_usLeafCount` entry, positioned at
`m_pCenterCoords + i*3`. SpeedTreeRT leaves are camera-facing BILLBOARDS.

**This is the one sanctioned deviation**: Skyrim cannot do per-leaf camera
billboards, so we emit 2 crossed quads per leaf instead. Everything downstream
of that substitution should otherwise follow the engine.

Engine leaf constant colors (per-corner, from ck-cmd): alpha ramp
`{0.0, 0.5, 0.5, 1.0}` across the 4 corners — a wind-weight gradient we do not
currently reproduce (we write alpha 1.0 on all 4).

---

## 5. DEFECTS in `spt_generator.py` (measured, 139 Oblivion.esm SPTs)
<a id="5-defects-sptgeneratorpy"></a>

Ranked by effect on silhouette / distribution.

### Finding A — level-1 stem cap destroys conifer silhouette (WORST)

`MAX_STEMS_PER_LEVEL = {1: 64, 2: 260, 3: 320}`
([spt_generator.py:90](../../asset_convert/speedtree/spt_generator.py#L90)).

**31/139 trees exceed the level-1 cap; 53/117 exceed level 2; 6/6 exceed level 3.**
Level 1 IS the silhouette on a conifer (many short whorled boughs):

| tree | authored boughs | kept |
|---|---|---|
| treejuniper01 | 240 | **27%** |
| treewhitepinefree | 212 | **30%** |
| treewhitepineyoung / _fa | 180 | **36%** |
| treescotchpineforest / _snow | 154 | **42%** |
| treeeasthemlock / _fa / _snow | 154 | **42%** |
| treewhitepineforest / _fa / _snow | 140 | **46%** |

Median tree authors only 31 boughs and is unaffected — this is concentrated
damage on exactly the species whose shape is bough-count-defined.

### Finding B — crown is interior-heavy, not a shell

Fraction of leaf verts outside half the crown radius (uniform solid ≈ 0.75;
a real crown is higher):

| tree | outer-50% mass |
|---|---|
| shrubazaleapinksu | **0.06** |
| treeweepingwillowsu | 0.22 |
| treesugarmapleforest01su | 0.25 |
| treewhitepineforest01 | 0.30 |
| treecottonwoodsu | 0.31 |
| dbush03 | 0.46 |

#### 🛑 REVISED — the cause is the BRANCHES, not the leaf passes

My first reading blamed the clump and cull passes. **Measured, that is wrong.**
Comparing leaf geometry against the bark skeleton it hangs on, within the crown
band, both normalized to the same radius:

| tree | bark outer-50% | leaf outer-50% | leaf/bark |
|---|---|---|---|
| shrubazaleapinksu | **0.00** | 0.06 | huge |
| treewhitepineforest01 | **0.01** | 0.29 | 22.0× |
| treesugarmapleforest01su | **0.03** | 0.24 | 7.6× |
| treeweepingwillowsu | **0.08** | 0.21 | 2.5× |
| treequakingaspenyoungsu | 0.18 | 0.36 | 2.0× |
| treecottonwoodsu | 0.22 | 0.32 | 1.4× |
| dbush03 | 0.34 | 0.47 | 1.4× |

**Leaves are already pushed outward relative to their branches on every tree**
(ratio 1.4–22×). And leaves, not bark, define the silhouette everywhere —
`leaf_rmax / bark_rmax` is 1.13–2.03 across the same set.

So the leaf passes are not eroding an outer shell. **The branch skeleton simply
does not reach outward** — bark outer-50% is 0.00–0.34, i.e. the boughs
themselves are clustered near the trunk. The leaves then do most of the work of
filling the crown, which is why they read as an interior mass: they are hanging
off branches that all end too close to the axis.

**This makes Finding A the root cause of the crown shape too**, not just of
conifer bough count: with 27–46% of level-1 boughs discarded, and the survivors
capped again at level 2, there is nothing left to carry foliage out to the
crown edge. Fix the stem budget first, then re-measure before touching the
clump or cull passes.

Vertical distribution is more defensible (deciles peak mid-crown), but the
**top decile is only 0.02–0.06 on every tree** — crowns lose their apex.

### Finding C — Placement Distance applied at a random azimuth

Leaves-level 6004 (Placement/Distance) IS read
([spt_generator.py:771](../../asset_convert/speedtree/spt_generator.py#L771)) but offsets the
card at `rng.uniform(0, 2pi)` around the twig, so it puffs symmetrically rather
than directionally.

**Downgraded in importance by the Finding B revision.** Since leaves already sit
further out than their branches on every measured tree, a symmetric puff is not
what is starving the crown edge. Worth matching to the engine for fidelity, but
it is not the silhouette defect it looked like — and note the engine's own leaf
orientation is *also* a random draw (§6g), so symmetric scatter may well be
correct behaviour.

### Finding D — leaf window silently clamped off the twig base

`lx0 = np.clip(carrier_lv.child_first, 0.02, 0.95)`
([spt_generator.py:731](../../asset_convert/speedtree/spt_generator.py#L731)).
**4 of 7 sampled trees author `child_first = 0.00`** (white pine, quaking
aspen, willow, azalea) and get 0.02. `lx1 = max(lx1, lx0+0.05)` is a second
unauthored widening of narrow windows.

### Finding E — golden-angle azimuth has NO basis in the engine

`golden = pi*(3-sqrt(5))`, used as `az = golden*ci + rng.uniform(-0.35,0.35)`
([spt_generator.py:582](../../asset_convert/speedtree/spt_generator.py#L582),
[:638](../../asset_convert/speedtree/spt_generator.py#L638)).

Scanned the whole binary for the phyllotaxis constants:

| constant (f32) | occurrences in Oblivion.exe |
|---|---|
| golden angle 2.39996 rad | **0** |
| 137.5077° | **0** |
| golden ratio 1.61803 | **0** |

SpeedTree places children by **seeded RNG** (`0x7a24f0`), not a deterministic
spiral. Our spiral imposes a regular helical rhythm the engine never produces.

### Finding F — `cnt = max(cnt, 4)` fabricates foliage

[spt_generator.py:789](../../asset_convert/speedtree/spt_generator.py#L789) forces ≥4 leaf
attachments per carrier even when `child_freq * stored_length` rounds to 0.
dbush03 generates 2.2× its authored leaf count as a result.

### Finding G — authored fields read but never applied

| field | set in | consequence |
|---|---|---|
| `size_variance` (2007) | **132/139** (median 10) | every instance of a tree type is identically sized; the engine varies them (ck-cmd passes `fVariance`) |
| `orientation_var` (4002) | **138/139** (median 0.20) | used, but scaled by an invented `*90.0` at [spt_generator.py:1093](../../asset_convert/speedtree/spt_generator.py#L1093); spec says percent |
| `seg_pack` (16002) | **80/139** | vertex packing along length ignored |
| `blossom_depth` (3001) | **57/139** | blossom depth rule unimplemented |

Confirmed **unused in all 139 vanilla trees** (safe to keep ignoring, but must
be handled for third-party plugins): `gen_dist` (26007), `gen_depth` (26008),
`hang` (72005), `rotate` (72006), `use_mesh` (72001), `floor_*` (27002-27006),
`fork_*` (26009-26012), `gnarl` (26021), `rough_amount` (26004),
`v_offset`/`u_offset` (50010/50017), `first_visible_level` (29002),
`orientation_angle` (74002), fronds (13xxx/14xxx).

> **Floor (27000) matters**: our crown-floor percentile heuristic
> ([spt_generator.py:934-956](../../asset_convert/speedtree/spt_generator.py#L934-L956))
> duplicates an authored mechanism that **0/139 vanilla trees enable**. The
> authored answer is "no floor" — so that cull removes foliage Oblivion keeps.

### Finding H — leaf-collision mode 2 modelled as poisson-disk thinning

[spt_generator.py:837-868](../../asset_convert/speedtree/spt_generator.py#L837-L868) affects
**124/139 trees** (mode 2; 15 are mode 1). Blue-noise spacing is not what the
engine does — it prunes leaves that intersect geometry. Tolerance (3007)
median 0.32, range 0.00–0.60.

### NON-defects (checked, do not "fix")

- **Radius-profile `*1.05` monotone clamp**
  ([spt_generator.py:404](../../asset_convert/speedtree/spt_generator.py#L404)): every
  sampled `radius_profile` (6006) is monotone-decreasing (max increase negative
  on all 7 trees), so the clamp never fires. Not a defect.
- **Segment caps** `_CROSS_CAP`/`_RING_CAP`: mostly already under the caps.
  Only real clips are cottonwood L0 cross 20→16 and L1 length 13→10.
- **`4006` vs `4005*Size`**: deriving from 4005 is correct. Scope correction —
  the doc says "~15 shrubs"; measured **202/465 leaf maps** disagree.
- **Bark seam duplication, v_dds = 1 − v_tga flip**: both confirmed correct
  (the flip by ck-cmd's `SetTextureFlip(true)`).

---

## 6. Growth model — status
<a id="6-growth-model-status"></a>

`GRAVITY_RESPONSE = 4.5`, the disturbance sine-snake
(`d_turns = rng.uniform(0.5,1.4)`), and the peak-normalized angle profile
(`/ ap_max`) are all **fitted to billboard images**, not derived from the exe.
They are the least-verified part of the pipeline.

The FORMAT doc and CAD notes (`speedtreecadnotesv4`) agree that 6017 is
**Angle Profile** (v3 UI name), NOT "Gravity Profile" (v4 UI name) —
`references/spttools-master/NOTES:660-690` reasons this out from which curves
have the `c0 c100 var curve` form. Our generator already treats it as an angle
profile, which is right.

Remaining decomp target: the branch-growing routine itself (see §7).

---

## 6b. THE RANDOM NUMBER GENERATOR — fully recovered
<a id="6b-random-number-generator-fully"></a>

This is the single most important result for bit-exact replication: **every**
random decision the engine makes (branch position, azimuth, every `*_var`
spread) comes from one deterministic stream seeded by SPT section 2005.

SpeedTree uses the **Newran** library (strings `Uniform` @ `0xa8ca08`,
`PosGen` @ `0xa8ca10`, `Newran: seed out of range` @ `0xa8ca5c`).

### Algorithm — Lewis–Goodman–Miller + Bays–Durham shuffle

Core generator `0x7a6cd0`:
```
cur = (int)cur * 16807                 ; 0x41a7
cur = cur - (cur / 2147483647)*2147483647   ; modulus 0x7fffffff
if (cur <= 0) cur += 2147483647
return (float)cur * 4.6566128730773926e-10  ; 1/(2^31-1), const @ 0xa89c50
```
The running value is a **double at `0xb42c90`**.

Shuffled draw `0x7a6fd0` (Bays–Durham):
```
idx = (int)( base() * 128.0 )          ; 128.0 stored as f64 @ 0xa3f428
out = buf[idx]                          ; buf = 128 floats @ 0xb42a90
buf[idx] = base()
return out
```
Table spans `0xb42a90`–`0xb42c90` = 512 bytes = **128 float entries**
(confirmed by the seeding loop's `cmp esi, 0xb42c90` at `0x7a6d87`).

Seeding `0x7a6d30`: sets `cur = seed`, then fills all 128 slots with
`base()*NORM`, leaving the last raw value as `cur`.

### Engine-level API

| VA | Meaning |
|---|---|
| `0x78e990` | `Random01()` — tail-jump to the shuffled draw |
| `0x78ea00` | **`RandomRange(lo, hi)`** = `lo + Random01()*(hi-lo)` |
| `0x78ea30` | `Seed(n)`; `n == -1` → default seed **12345** (`0x3039`) |
| `0x78ead0` | seed clamp: `seed = max(seed, 1)`, sets init flag `0xb42994` |

Global RNG object lives at **`0xb429c9`** (passed in `ecx` at every site).

**A faithful port must reproduce this generator and consume draws in the
engine's order.** Python reimplementation verified: mean 0.50055 / stdev
0.28738 over 20,000 draws (expect 0.5 / 0.2887).

## 6c. Placement rules recovered from the generation sites
<a id="6c-placement-rules-recovered-from"></a>

19 call sites consume `RandomRange` (`0x78ea00`). Decoded ones:

### Child position along the parent — `0x793664`
```
fld [esi+0x08]      ; child_first  (6010, verified offset +0x08)
fld [esi+0x0c]      ; child_last   (6011, verified offset +0x0c)
call RandomRange    ; x = uniform(child_first, child_last)
```
**Children are placed by a UNIFORM RANDOM DRAW in `[first, last]`**, not by
even subdivision with jitter. A preceding branch (`0x7935fe`–`0x79363d`) builds
a narrowed sub-range by lerping the window with constants **0.95**
(`0xa77838`) and **0.85** (`0xa563d8`) for the first child of a run.

Note `0x7935e2`: `edi += 3; Seed(edi)` — **the engine RE-SEEDS per branch**
with a derived counter, which is how a given tree is reproducible per-limb.

### Child azimuth — `0x7921ef` and `0x7926ce`
```
fld [0xa8c694]   ; -180.0
fld [0xa3f420]   ; +180.0
call RandomRange ; azimuth = uniform(-180, +180) degrees
```
followed immediately by 3-vector rotation math.

**This kills Finding E definitively**: the azimuth is a uniform random angle
over the full circle. Our `golden*ci + jitter` spiral is wrong in kind, not
just in constant. (Consistent with the zero golden-angle constants in §5-E.)

### Variance draws — `0x791e19`
```
fld [edi+0x10] ; fchs   -> -v
fld [edi+0x10]          -> +v
call RandomRange        ; uniform(-v, +v)
```
Symmetric ± spread — confirms `BezierSpline.eval_var`'s convention (and that
`abs()` on a negative stored variance is the right fix).

## 6d. SPT parse-stage map (error string → code site)
<a id="6d-spt-parse-stage-map"></a>

Every parse stage located, giving a complete cross-check surface for the parser:

| Stage | Error string VA | Code site |
|---|---|---|
| begin-file token (1000) | `0xa8c934` | `0x7a4e47` |
| general tree info (2000s) | `0xa8c8d0` | `0x7a4b16` |
| branch data (6000s) | `0xa8c678` | `0x7a4274`, `0x7a7978` |
| single leaf info (4000s) | `0xa8c980` | `0x7a6578` |
| billboard leaf | `0xa8cabc` | `0x7a8408` |
| flare info (16002-16012) | `0xa8c878` | `0x7a2f02`…`0x7a306b` (6 sites) |
| texture controls | `0xa8c85c` | `0x7a2b4f`, `0x7a2b95`, `0x7a2bf2` |
| texture coord info (50000) | `0xa8bafc` | `0x789cde`, `0x78ad7e` |
| leaf LOD data | `0xa8c89c` | `0x7a4501`, `0x7a4550` |
| LOD info (9000s) | `0xa8ba68` | `0x789721` |
| new wind info | `0xa8bae4` | `0x78983f` |
| collision object (12000s) | `0xa8c4d8` / `0xa8c4b8` | `0x78de9f` / `0x78deeb` |
| BezierSpline parse | `0xa8ba30` | `0x786b2a` |

`0x7a1cd0` — previously assumed to be the tree builder — is actually
**`CFrondEngine::Compute()`** (proved by `default reached in
CFrondEngine::Compute()` @ `0xa8c7f8` and `frond vertices exceed %d`
@ `0xa8c7dc`). Fronds are disabled in all 139 vanilla trees, so it is not on
our critical path. It does reveal the engine's **65535-vertex (`0xffff`) budget
check** per geometry type.

Also note `0xa8c740` holds a **default BezierSpline literal**
(`"BezierSpline 0.0 1.0 0.0 { 3 0 0.00138887 … }"`) used when a curve is
absent — a fallback our parser does not implement.

## 6e. BezierSpline evaluation — fully recovered (`0x784210`)
<a id="6e-bezierspline-evaluation-fully-recovered"></a>

The engine does **not** evaluate Bezier segments at query time. It precomputes
a **500-entry lookup table** and interpolates:

```
CBezierSpline::Evaluate(float x):        ; this = edi, curve data at edi+0x3c
    if (table_size != 0x1f4)             ; 500 entries required
        goto fallback
    fx  = x * 499.0                      ; f64 499.0 @ 0xa8ba00
    i   = (int)fx                        ; truncate
    if (i == 499)  y = table[499].value
    else:
        frac = (fx - i*0.0020040080416947603) / ...   ; 1/499 @ 0xa8b9f8
        y = table[i].value + frac * (table[i+1].value - table[i].value)
    ; final scaling — offsets on the spline object itself:
    out = [edi+0x00] + y * ([edi+0x04] - [edi+0x00])   ; lo + y*(hi-lo)
    out = out + RandomRange(-[edi+0x08], +[edi+0x08])  ; ± variance
    return out
```

Spline object layout: **`+0x00` = lo, `+0x04` = hi, `+0x08` = variance**,
`+0x3c` = control-point vector, `+0x40` = table pointer.

Three confirmations for our implementation:
1. `eval()` = `lo + curve_y(x)*(hi-lo)` — **matches** `BezierSpline.eval`.
2. Variance is applied **inside** evaluation as `uniform(-v, +v)` (site
   `0x784341`) — matches `eval_var`, and confirms the `abs()` fix.
3. Sampling is **500 uniform points with linear interpolation**, not adaptive
   Bezier subdivision. Our `_sample_curve` uses 24 samples *per segment* with
   `np.interp` — a different discretisation, but **measured equivalent**:
   across **all 3,577 non-constant curves in the 139 Oblivion SPTs**, max
   deviation is **0.196% of curve range**, median **0.000%**, and **0/3577
   curves exceed 1%**. Worst case `shrubvinemaplesu` L3 length at 0.00196.
   **Not worth changing** — this is not a source of visible error.

A default curve literal lives at `0xa8c740`:
`"BezierSpline 0.0 1.0 0.0 { 3 0 0.00138887 0.337009 0.941501 0.132767
0.493215 0.998903 1 0.00102074 0.23702 1 -6.24607e-008 0.307222 -0.951638
0.126974 }"` — used when a curve is missing. We do not implement this fallback.

## 6f. The branch builder — `0x7925b0`
<a id="6f-branch-builder-0x7925b0"></a>

`0x7925b0` (5200 bytes) is the branch generation routine (it encloses the child
spawn site `0x793664` found above). Structure recovered so far:

- `0x792639` → `call 0x791870` when level index == 0 (trunk special-case).
- `0x792665`: level params fetched as `levels[idx]` from the vector at
  `0xb429e0` — 4-byte pointer stride.
- `0x7926a3`, `0x7926e5`, `0x7926f9`: repeated
  `Evaluate(curve_at [esi+NN], position)` calls — the per-child shape draws.
  Curve offsets seen on the level object: **`+0x54`, `+0x60`, `+0x6c`**.
- `0x7926ce`: azimuth `uniform(-180, +180)` (see §6c).
- `0x7935e2`: `Seed(counter + 3)` — **per-branch reseed**, then child position
  `uniform(child_first, child_last)`.
- Global RNG instance `0xb429c9`; a separate spline-local RNG at `0xb4295d`.

Level-object curve offsets still need to be matched to section ids (6000-6007,
6017). The three seen (`+0x54/+0x60/+0x6c`, stride 12) suggest curve objects are
inlined 12 bytes apart (lo/hi/variance), i.e. the level struct embeds the
splines rather than pointing at them.

### Verified level-struct layout (0x74 = 116 bytes)

Cross-checked **both** from the parser handlers (§3.1) and from the consumer
code that reads the same offsets — a two-sided confirmation:

| Offset | Field | sid | Parser write | Consumer read |
|---|---|---|---|---|
| `+0x00` | cross_segments | 6008 | `mov [edi], eax` | — |
| `+0x04` | length_segments | 6009 | `mov [edi+4], eax` | — |
| `+0x08` | child_first | 6010 | `fstp [edi+8]` | `0x7935fe` child pos |
| `+0x0c` | child_last | 6011 | `fstp [edi+0xc]` | `0x793608` child pos |
| `+0x10` | child_freq | 6012 | `fstp [edi+0x10]` | `0x793559` child COUNT |
| `+0x14` | u_tile | 6013 | `fstp [edi+0x14]` | `0x7927b6` bark U |
| `+0x18` | v_tile | 6014 | `fstp [edi+0x18]` | `0x79279f` bark V |
| `+0x1c` | u_abs | 6015 | `mov byte [edi+0x1c], cl` | `0x7927b2` bark U |
| `+0x1d` | v_abs | 6016 | `mov byte [edi+0x1d], cl` | `0x792795` bark V |
| `+0x20` | (twist / 15003) | — | — | `0x792789`, sign-flipped by `cl & 1` |

### THE CHILD-COUNT FORMULA — `0x793559`

```
fld  [esi+0x10]        ; child_freq   (6012)
fdiv [esp+0x108]       ; / <incoming param>
fmul [esp+0x68]        ; * <stem length term, computed at 0x7927a4>
call 0x9828c0          ; ftol -> int count
```

So **count = child_freq * length / D**, proportional to `freq × length` —
the shape our generator already uses
(`cnt = parent_lv.child_freq * pstem.stored_length`).

**The divisor `D` is arg1**, resolved by frame math. The prologue consumes
`12 (SEH) + 0xe0 (locals) + 16 (4 regs) + 4 (cookie) = 0x100` bytes, so
`[esp+0x100]` is the return address and `[esp+0x104+4n]` is arg *n*. Therefore
`[esp+0x108]` = **arg1**, and `[esp+0x68]` is the local stem-length term
computed at `0x7927a4`.

**⚠️ CORRECTION — `D` at the trunk is NOT 1.0.** An earlier draft of this
section claimed arg1 came from the `fld1` at `0x7a488c`. That was a
mis-assignment of the push order. Counting the pushes backward from the call at
`0x7a48f9` (12 dword args total; `sub esp,8` at `0x7a48dd` supplies *two*):

```
arg0  0x7a48f8  push eax
arg1  0x7a48f1  push ecx ; fstp [esp]     <- value = fld [esp+0x3c] @ 0x7a48eb
arg2  0x7a48ef  push 0                    <- trunk level index
arg3  0x7a48ea  push edx (&esi+4)
arg4/5 0x7a48dd sub esp,8 ; fst/fstp      <- THIS is where the fld1 lands
arg6..arg11     ...
```

So the `fld1` is arg4/arg5. **arg1's source is now traced: it is the RANDOMISED
TREE SIZE.**

Accounting for the 0x24 bytes of pushes between `0x7a48c9` and `0x7a48eb`,
`[esp+0x3c]` there resolves to the frame slot written at `0x7a47bf`, and the
value in it is produced at `0x7a4791`–`0x7a47b7` in the enclosing tree-build
function `0x7a45f0`:

```
007a4792  fadd [esi+0x4c]          ; Size + variance   -> hi
007a47a6  fld  [esi+0x4c]
007a47a9  fsub [esi+0x50]          ; Size - variance   -> lo
007a47b7  call 0x78ea00            ; RandomRange(lo, hi)
007a47bf  fstp [esp+0x14]          ; the instance's actual size
```

Offsets confirmed straight from the parser handlers: **section 2006 (Size) →
`[ebp+0x4c]`** (`0x7a4ad6`) and **section 2007 (Size Variance) → `[ebp+0x50]`**
(`0x7a4ae2`).

### 🛑 RESOLVED: `count = child_freq * stored_length` — WE ARE ALREADY CORRECT

The units are now reconciled, and the answer kills the "port the engine
formula" plan. The key is that **the same ratio appears in two places**:

```
R = [esp+0x68] / [esp+0x108]

bark V, when v_abs == 0:   v     = R * v_tile     ; 0x7927a4-0x7927af
child count:              count = freq * R        ; 0x793559-0x793567
                                   (fld freq; fdiv [0x108]; fmul [0x68])
```

The bark-V rule is **already verified against real trees** (dogwood trunk
12 x 0.8 stored = 9.6 repeats, landing square texels against its U density —
recorded in `nif_conversion_notes.md` and reconfirmed from the engine in the
Bark UV section below). That rule is stated as *"v_abs=0 means the repeat count
scales with the stem's STORED length."*

Therefore **R is the stem's stored (pre-scale) length**:
- `[esp+0x68]` = the stem's absolute/world length
- `[esp+0x108]` = arg1 = the randomised tree size
- `R = world_length / size = stored_length`

So the engine computes:

```
count = child_freq * stored_length
```

which is **exactly** what `spt_generator.py` already does:

```python
cnt = parent_lv.child_freq * pstem.stored_length   # spt_generator.py:595
```

**There is no `/D` normalization and no recursive density falloff to port.**
The `w + t*(1-w)` blend at `0x79387a` propagates the *size* argument down the
recursion (so nested levels normalize against a size that drifts toward 1.0);
it is not a count attenuator. My earlier framing of it as a "density falloff"
was wrong twice over — first with D=1.0 (provably a no-op), then as a count
mechanism (it is a units divisor).

**Consequence: Finding A cannot be fixed by porting the count formula.** Our
counts already match the engine. The gap is purely the artificial
`MAX_STEMS_PER_LEVEL` ceiling clamping a correct count. Removing/raising that
cap is a budget decision, NOT an algorithm port — there is no engine-verified
alternative rule waiting to be implemented.

`size_variance` (2007) remains genuinely unimplemented and engine-verified
(it feeds `[esp+0x108]`), but note it *cancels* out of the count entirely —
its real effect is on absolute geometry scale, not on branch counts.

### Bark UV rule — CONFIRMED CORRECT

`0x792795`–`0x7927bf` is the bark UV setup and it matches our implementation:

```
if (v_abs != 0)   v = v_tile                          ; 0x79279f
else              v = (length / K) * v_tile           ; 0x7927a4-0x7927af
if (u_abs == 0)   u = u_tile * <run> * [0xb2b714]     ; 0x7927bb
else              u = u_tile
twist  = [esi+0x20], negated when (index & 1)         ; 0x792789-0x792793
```

**"v_abs means exact count, otherwise it scales with stem length"** — exactly
the rule recorded in `nif_conversion_notes.md`, now proven from the engine.


## 6g. The leaf system — parser + placement
<a id="6g-leaf-system-parser-placement"></a>

### Leaf parse block `0x7a6020` (reached from the 1004 dispatch)

Two jump tables, both matching the spec exactly:

**`0x7a61de`**: `add eax, 0xfffff060` (= −4000), `cmp eax, 7` → table `0x7a6628`

| sid | handler | | sid | handler |
|---|---|---|---|---|
| 4000 | `0x7a61e5` | | 4004 | `0x7a6345` |
| 4001 | `0x7a6216` | | 4005 | `0x7a6373` |
| 4002 | `0x7a6247` | | 4006 | `0x7a63a1` |
| 4003 | `0x7a625a` | | 4007 | `0x7a63cf` |

**`0x7a6499`**: `add eax, 0xfffff447` (= −3001), `cmp eax, 9` → table `0x7a6648`,
covering 3001–3010 (3004 and 3005 share handler `0x7a64d9`).

Reader primitive discovered: **`0x78eba0` = `read_vec3()`** (returns a pointer to
3 floats). 4004 / 4005 / 4006 all use it — confirming all three are XYZ triples.

**🛑 SECTION 4007 IS READ AND DISCARDED**: handler `0x7a63cf` is
`call read_float32; fstp st(0)` — popped straight off the FPU stack, never
stored. The engine ignores it. `FORMAT` marks it `???? % XXXX`; it is
genuinely unused. Our parser keeps it as `unknown7`, which is harmless.

Leaf-globals struct offsets (object in `ebp`):

| sid | field | offset |
|---|---|---|
| 3002 | blossom weight | `+0x28` |
| 3007 | placement tolerance | `+0x20` |
| 3008 | collision detection mode | `+0x0c` |

Parsed leaf-map records are **0x54 (84) bytes** each (`add [esp+0x14], 0x54`
at `0x7a6456`, copy-ctor `0x7a3470`). The runtime leaf-map array used during
generation has a **0x2c (44) byte** stride.

### Leaf map selection — `0x79a000`, sites `0x79a1fe` / `0x79a31b`

```
n     = leaf_map_count                       ; from [edi+0x44]/[edi+0x48] span
r     = RandomRange(0.0, 100000.0)           ; 1e5 const @ 0xa3f3d8
idx   = ((int64)r) % n                       ; div ebx -> remainder in dl
[esi+0x18] = idx                             ; chosen map index, stored as BYTE
```

**The leaf map is chosen by uniform random index per leaf** — a plain modulo of
a 0..100000 draw. This confirms our uniform pick over `leaf_maps` is the right
shape. (Note: the blossom weighting must therefore be applied elsewhere, or via
duplicate map entries — worth checking before trusting our blossom model.)

### Leaf card size — `0x79a25b`–`0x79a2a6`

```
size  = map[idx].f_1c * [esi+0x10] * 0.5      ; 0.5 = f64 @ 0xa2faa0
size  = size * map[idx].f_20
[esi+0x14] = size
```
then an orientation draw:
```
[esi+0x1c] = RandomRange(map[idx].f_24, map[idx].f_28)
```
i.e. **the leaf's orientation is a uniform draw between two authored per-map
bounds**, not a `±(var*90°)` tilt as we currently model it
([spt_generator.py:1093](../../asset_convert/speedtree/spt_generator.py#L1093)).

Then, at `0x79a348`:
```
if (leaf_index & 1)  orientation *= -1.0     ; f64 -1.0 @ 0xa3d360
```
**Alternating leaves are MIRRORED.** Odd-indexed leaves get their orientation
negated — a cheap way to break up repetition that our generator (which draws a
fresh `rng.uniform` yaw per card) does not reproduce. Finally:
```
[esi+0x20] = [esp+0x34] * [esi+0x10]         ; a second size-scaled term
```

Two more constants pinned while here:
- `0xa3ddd8` = **255.0** — the vertex-color byte scale.
- `0xb2b714` = **6.28318548 (2π)** — the bark-U wrap factor used at `0x7927bb`
  when `u_abs == 0`, i.e. `u = u_tile * run * 2π`. This is the circumference
  term in the bark UV rule.

### 4005 vs 4006 — settled with data

Measured over all **465 textured leaf maps** in the 139 Oblivion SPTs:

- ratio `4006 / (4005 × Size)`: median **1.0000**, but only **263/465 (57%)**
  are within 1% of 1.0.
- The mismatches are not a consistent alternate factor (ratios 0.475, 0.594,
  0.667, 0.713, 0.75, 0.95 …), and crucially **`dtree01leaves` stores 4006 =
  7.6 for three different maps whose 4005 values are 0.08 / 0.10 / 0.05** —
  4006 is a stale cached product that was not re-written when 4005 changed.

**Deriving size from 4005 × Size is correct.** Corrects the existing note in
`nif_conversion_notes.md`, which says 4006 is stale in "~15 shrubs" — the real
figure is **202/465 leaf maps**.

## 6h. Remaining parse blocks verified against the engine
<a id="6h-remaining-parse-blocks"></a>

### LOD block (`0x78965e`): `add eax, 0xffffdcd6` (= −9002), 8 entries → `0x789764`

| sid | handler | note |
|---|---|---|
| 9002 | `0x789665` | |
| 9003 | `0x789671` | |
| 9004 | `0x78967d` | |
| 9005 | `0x789695` | sub-block opener |
| 9006 / 9007 / 9008 | `0x78971c` | **all route to the error/skip target** |
| 9009 | `0x789689` | |

### Composite/quad block (`0x78997e`): `add eax, 0xffffd8ee` (= −10002), 6 entries → `0x789d28`

| sid | handler | payload |
|---|---|---|
| 10002 | `0x789985` | leaf quads |
| 10003 | `0x789a11` | billboard quads |
| 10004 | `0x789ac7` | frond quads |
| **10005** | `0x789b55` | **intstring — UNDOCUMENTED** |
| **10006** | `0x789bf0` | **byte — UNDOCUMENTED** |
| **10007** | `0x789c1b` | **byte — UNDOCUMENTED** |

**10005–10007 are absent from `references/spttools-master/FORMAT` entirely.**
Scanned all **636 exported SPTs**: these ids occur **0 times**, so no authored
tree uses them and our strict parser will never trip on them. Documented here
so a future third-party plugin that *does* use them is recognised rather than
treated as format drift.

**10002 payload confirmed**: `<int32 N>` followed by **N × 8 floats**
(`shl ebp, 5` = 32-byte stride, inner loop counter `ebx = 8`). Matches our
parser exactly.

### Collision objects (`0x78dd10`)

Type dispatch is a chained `sub eax, 1`:
```
type 0 -> sphere    : read XYZ, then 1 float  (radius)
type 1 -> capsule   : read XYZ, then 2 floats (radius, length)
type 2 -> box       : read XYZ, then 3 floats (extent 2)
```
All three read the XYZ triple first (`0x78ddc1`–`0x78dddc`), then the
type-specific tail. **Matches our parser** (12002 = 4 floats, 12003 = 5,
12004 = 6). `unknown collision object type` (`0xa8c4b8`) is thrown at
`0x78deeb` for anything else.

## 6i. 🛑 PARSER BUG FOUND: section 7000 is a COUNT, not a bare marker
<a id="6i-parser-bug"></a>

**2 of 636 exported SPTs fail to parse today** — both Nehrim:

```
export\Nehrim.esm\meshes\trees\tree1.spt      : unknown section 4 at offset 4846
export\Nehrim.esm\meshes\trees\treetest4.spt  : unknown section 2 at offset 5379
```

`spt_parser.py` lists `7000`/`7001` in `_MARKERS` (payload-less). **The engine
reads an int32 immediately after 7000.** At `0x7a42d0` (the handler the main
loop calls when it sees `cmp eax, 0x1b58` = 7000 at `0x7a4fe6`):

```
007a430f  call 0x78eb40                  ; read_int32()
007a4314  mov  [esi+0xc0], eax           ; = number of billboard-leaf groups
...                                       ; then loop that many times:
007a43d0  cmp  eax, 0x1b5a               ; 7002 = group begin
007a43e2  cmp  eax, 0x1b5b               ; 7003 = group end
007a43f0  cmp  eax, 0x1b5c               ; 7004 = billboard leaf -> 0x7a8250
007a4488  cmp  eax, 0x1b59               ; 7001 = block end
```

Hex confirmation from `tree1.spt` at offset 4842:
```
581b0000 04000000 5a1b0000 5c1b0000 5e1b0000 ...
  7000       4       7002     7004     7006
```
The `4` is the group count — our parser reads it as a section id and dies.

### The 7000 block is the BILLBOARD-LEAF system (`0x7a8250`)

`add eax, 0xffffe4a2` (= −7006), 11 entries, table `0x7a8454`. **Sections
7002–7016 are entirely undocumented in `FORMAT`**, which lists only 7000/7001
as unexplained markers.

| sid | handler | payload |
|---|---|---|
| 7006 | `0x7a82c8` | int (`0x78eb70`) → `[edi+0x14]` |
| 7007 | `0x7a82d7` | byte |
| 7008 | `0x7a8301` | byte |
| 7009 | `0x7a8405` | *(error/skip — unused)* |
| 7010 | `0x7a832b` | vec3 (`0x78eba0`) |
| 7011 | `0x7a834d` | byte |
| 7012 | `0x7a8374` | byte |
| 7013 | `0x7a83aa` | int → `[edi+0x48]` |
| 7014 | `0x7a8405` | *(error/skip — unused)* |
| 7015 | `0x7a83b6` | vec3 |
| 7016 | `0x7a83d5` | float, **read and DISCARDED** (`fstp st(0)`) |

Each billboard-leaf object is `0x4c` (76) bytes (`push 0x4c; call malloc` at
`0x7a8276`).

### Measured scope and impact — LOW, but fix it anyway

- **Exactly 2 of 636 SPTs** contain a real 7000 block (scanned for an aligned
  `7000` followed by a plausible count followed by `7002`):
  `tree1.spt` (4 groups) and `treetest4.spt` (2 groups), both Nehrim.
- **Neither is referenced by any TREE record in any plugin** — checked every
  `export/*/TREE.txt`. They are leftover authoring files.

So nothing in the shipped output is broken by this today. It is still a genuine
format gap: the block is legal SPT that a third-party plugin could use, and the
parser is *supposed* to be strict-but-complete. The fix is to make the parser
CONSUME the block (read the count, then the 7002…7003 groups) — the payload
itself is *billboard* leaf data, the LOD card representation we deliberately do
not port, so it can be stored and ignored.

Why the existing "parses 547/547" claim in
[nif_conversion_notes.md](asset_convert_nif.md) never caught this: the two
files are the **only** SPTs living under `export/Nehrim.esm/meshes/trees/`.
Every other tree in every plugin sits under a `…/trees/` path
(139 Oblivion, 118 Nehrim, 69×4 TamRes, 58 TWMP, …). So whatever enumeration
produced the 547 figure did not walk `meshes/trees`, and these two have
probably never been parsed.

## 6j. The `Compute()` pipeline — full stage order
<a id="6j-compute-pipeline-full-stage"></a>

Every call `CSpeedTreeRT::Compute` (`0x78cca0`) makes, in order. This is the
authoritative build sequence:

| Call | Address | Stage |
|---|---|---|
| 1 | `0x7a24f0` | seed setup (section 2005) |
| 2 | `0x7a45f0` | tree size (`[ebx+0x24]`) |
| 3 | `0x7a1cd0` | **`CFrondEngine::Compute()`** |
| 4 | `0x7a5740` | (LOD level count setup) |
| 5 | `0x78c370` | conditional, when `[edx+0x38] == 1` |
| 6 | `0x793c00` | scale/derived-value setup (writes `+0x18/0x1c/0x20`) |
| 7 | `0x799320` | — |
| 8 | `0x798550` | **per-leaf-map quad setup**, looped over `[ebx+0x4c]` (stride 0x20) |
| 9 | `0x79a810` | per-billboard-quad setup, looped over `[eax+0x10]`/`[eax+0x14]` |
| 10 | `0x7977d0` | ×2 |
| 11 | `0x7a66b0` | |
| 12 | `0x7948c0`, `0x798360`, `0x7948c0` | |
| 13 | `0x7a6bb0`, `0x7947a0` | |
| 14 | `0x7997f0` | bounding-box accumulation |
| 15 | `0x787480`, `0x7875d0` | finalise |

### 🛑 TEXTURE FLIP — proven from the engine (`0x798550`)

The leaf quad copy at `0x79857d`–`0x79860d` copies the 8 authored floats into
the runtime quad, and multiplies **every odd (v) component** by a factor:

```
0079856a  call 0x787680          ; GetTextureFlip()  -> mov al,[0xb4297d]; ret
0079856f  test al, al
00798571  je   0x79857b
00798573  fld  [0xa30634]        ; -1.0   <- flip ON
00798579  jmp  0x79857d
0079857b  fld1                   ;  1.0   <- flip OFF
```
Then u components are stored unscaled (`fstp` direct) while v components go
through `fmul st(1)` — e.g. `[esi+0x04]`, `[esi+0x0c]`, `[esi+0x14]`,
`[esi+0x1c]` are all multiplied, `[esi+0x00]`, `[esi+0x08]`, `[esi+0x10]`,
`[esi+0x18]` are not.

**This is direct engine confirmation of the `v_dds = 1 − v_tga` rule** already
recorded in `nif_conversion_notes.md` from ck-cmd's `SetTextureFlip(true)`.
The flip state is a single global byte at **`0xb4297d`**.

Quad stride in the runtime buffer is **0x40 (64) bytes** (`shl ecx, 6`) — two
8-float sets per map (the second written at `+0x20`, with u/v **swapped**:
`[esi+8]`→first slot, `[esi+4]`→later — i.e. the mirrored/rotated variant).

## 6k. Leaf attachment: branch arc-length accumulation (`0x79a000`)
<a id="6k-leaf-attachment-branch-arc"></a>

Before any leaf is placed, the leaf engine walks the branch's ring array
(**stride 0x38 = 56 bytes per ring**) accumulating true arc length:

```
0079a0e9  fld  [ecx+ebp+0x3c] ; fsub [eax+ecx+4]    ; dz
0079a0f6  fld  [ecx+ebp+0x38] ; fsub [eax]          ; dx
0079a102  fld  [ecx+ebp+8]    ; fsub [eax+8]        ; dy
          ... dx*dx + dy*dy + dz*dz
0079a11d  sar  edx, 1
0079a123  add  edx, 0x1fc00000                       ; fast sqrt approximation
0079a135  fadd [esi+0x10]
0079a13a  fstp [esi+0x10]                            ; running branch length
```

`[esi+0x10]` — the accumulated branch length — is exactly the term the leaf
sizing at `0x79a263` multiplies by. So **leaf card size scales with the host
branch's real arc length**, not with the tree Size alone.

Note the engine uses a *fast approximate* sqrt (the `sar 1 / add 0x1fc00000`
bit-trick), so its lengths differ slightly from an exact `sqrt`. Irrelevant for
silhouette, noted for bit-exactness.

## 6l. 🛑 Position-along-branch is ARC-LENGTH PARAMETRIC, not index-linear (`0x78f720`)
<a id="6l-position-along-branch-arc"></a>

This is the most important structural finding for placement fidelity.

After drawing `x = uniform(child_first, child_last)`, the engine does **not**
map `x` onto the ring array by simple index scaling. It calls a bracket search:

```
0078f720  cmp  [ecx+0x18], 0            ; ring array base
0078f724  je   ret
0078f726  cmp  [ecx+0x1c], 2            ; ring count; needs >= 2
0078f72a  jl   ret
          edi = base + 0x88             ; = &ring[1].param
0078f752  fld  [edi]                    ; ring[i+1].param
0078f754  fcomp st(1)                   ; compare against x
0078f758  test ah, 0x41                 ; while (ring[i+1].param <= x)
0078f75d  add  edx, 1
0078f760  add  edi, 0x48                ;   advance one ring (stride 0x48 = 72)
          ...
0078f76c  [ebx]   = index               ; OUT: bracketing ring index
0078f777  fsub [ecx+eax*8+0x40]         ; x - ring[i].param
0078f785  fsub [eax+0x40]               ; ring[i+1].param - ring[i].param
0078f788  fdivp
0078f78a  [ebx+4] = frac                ; OUT: fraction within that segment
```

`0x88 − 0x40 = 0x48` = exactly one stride, confirming **field `+0x40` of each
72-byte ring is that ring's cumulative parameter**, and the search returns
`(segment_index, fraction_within_segment)`.

### …but we are ALREADY equivalent here — measured, NOT a defect

My first reading of this was that our index-linear interpolation
(`fi = x * (len(points)-1)` at
[spt_generator.py:764](../../asset_convert/speedtree/spt_generator.py#L764) and
[:626](../../asset_convert/speedtree/spt_generator.py#L626)) would drift from the engine's
arc-length parametrisation on curved stems. **That is wrong.**

`_grow_stem` advances by a **fixed step**:
```python
seg = world_len / n_rings        # spt_generator.py:311
...
p = p + d * seg                  # spt_generator.py:394
```
Every ring is therefore already equally spaced *in arc length*, so index-linear
and arc-length-parametric mappings are the same function.

Measured over reconstructed trunks for 7 trees (cottonwood, willow, sugar
maple, white pine, deadbush, azalea, quaking aspen), sampling 201 positions
each: **max drift 0.00%, mean 0.00%** of branch length. Zero on every tree.

**Not a defect. Do not "fix" it.** The one caveat: if `seg_pack` (16002) or
`seg_keep_*` (26005/26006) are ever implemented — they control non-uniform ring
packing and are set in 80/139 trees — then the two parametrisations diverge and
the bracket search above becomes the correct model. Until then this is settled.

## 6m. BRANCH SHAPE — the per-child evaluation sequence (`0x7925b0`)
<a id="6m-branch-shape-per-child"></a>

### Complete spline slot map (parser side, from the 6xxx handlers)

| sid | field | level-struct offset |
|---|---|---|
| 6000 | disturbance | `+0x50` |
| 6001 | gravity | `+0x54` |
| 6002 | flexibility | `+0x58` |
| 6003 | flex_profile | `+0x5c` |
| 6004 | length | `+0x60` |
| 6005 | radius | `+0x64` |
| 6006 | radius_profile | `+0x68` |
| 6007 | start_angle | `+0x6c` |
| 6017 | angle_profile | `+0x70` |

### The order the engine evaluates them, per child

All reads use the same parameter `[esp+0x114]` (= arg4):

```
0x792695  Evaluate(6004 LENGTH      [esi+0x60], x)
0x7926a8    * size                                  -> world length
0x7926ce  azimuth = RandomRange(-180, +180)
0x7926e5  Evaluate(6007 START_ANGLE [esi+0x6c], x)
0x7926fc  Evaluate(6001 GRAVITY     [esi+0x54], x)
0x792716  Evaluate(6005 RADIUS      [esi+0x64], x)  * size
0x792734  Evaluate(6002 FLEXIBILITY [esi+0x58], x)
```

**This confirms our generator's per-child parameter set is right** — length,
start angle, gravity, radius, flexibility, each drawn once per child from its
own curve, with length and radius scaled by tree size. It also fixes the
**draw order**, which matters for bit-exact RNG replay: the azimuth draw
happens *between* the length and start-angle evaluations, and each `Evaluate`
itself consumes a variance draw (§6e).

### ⚠️ UNRESOLVED: is `x` the absolute or window-normalized position?

At the recursive call (`0x79390f`), arg4/arg5 are written by
`sub esp,8 ; fstp [esp+4] ; fstp [esp]` fed from `[esp+0x30]` (= `t`, the
window-normalized position, computed at `0x7937c0`) and a second value. **Which
of the pair lands in arg4 cannot be resolved by static FPU tracing** — the
`fst`/`fstp` interleave with integer pushes and, at the trunk call, `fst` +
`fstp` write the *same* value to both slots (`0x7a48e0`/`0x7a48e7`), so the
trunk gives no discrimination either.

Measured discrimination: **44 of 139 trees** have a trunk window narrow enough
that the two readings differ, but only by **5–12° of start angle** (deadbush
40→30 vs 40→20; dtree01 50→42 vs 50→30).

**Our existing choice is window-normalized `x_rel`**, and that was settled
empirically before this decomp — cottonwood forks its whole fan inside the
trunk's `[0, 0.1]` window, which only works with normalization
([nif_conversion_notes.md](asset_convert_nif.md) generation-model note).
The engine passing `t` into the recursion is consistent with that. **Leave it
as is**; this is not an actionable defect, and the ambiguity is recorded only
so nobody "fixes" it in the wrong direction.

## 6n. 🛑 THE RING LOOP — `seg_pack` warps ring spacing (ACTIONABLE)
<a id="6n-ring-loop-segpack-warps"></a>

The per-ring loop of the branch builder starts at `0x792f20`:

```
00792f24  fild [esp+0x48]              ; i   (ring index)
00792f28  mov  ecx, [eax+0x1c]
00792f2b  sub  ecx, 1                  ; n-1
00792f32  fidiv [esp+0x1c]             ; i / (n-1)
00792f45  fld  [esi+0x24]              ; seg_pack  (section 16002)
00792f54  call 0x985b70                ; CRT pow()
                                       ; t = pow(i/(n-1), seg_pack)
00792f61  mov  ecx, [esi+0x68]         ; 6006 radius_profile
00792f7e  call 0x784210                ; Evaluate(radius_profile, ...)
00792f83  fmul [esp+0x6c]              ;   * base radius
00792f87  fstp [ebp+0x18]              ;   -> ring radius
00792f8a  mov  ecx, [esi+0x5c]         ; 6003 flex_profile
00792f95  call 0x784210                ; Evaluate(flex_profile, t)
00792f9a  fmul [esp+0x80]              ;   * flexibility  (from 0x792734)
```

**Section 16002 storage confirmed**: the flare-block parser writes it at
`0x7a2cfe` — `fstp [eax+0x24]` — the exact slot the `pow` exponent reads.
(The block is a chained `cmp eax, 0x3e82…0x3e8c` = 16002…16012 at
`0x7a2cc4`–`0x7a2ed6`.)

### What it does

The ring parameter is **`t = (i/(n-1)) ^ seg_pack`**, not `i/(n-1)`. Rings are
therefore NOT evenly spaced along the stem — with `seg_pack > 1` they bunch
toward the base and stretch toward the tip:

```
pack=1.00   t: 0.00 0.14 0.29 0.43 0.57 0.71 0.86 1.00   (uniform)
pack=1.50   t: 0.00 0.05 0.15 0.28 0.43 0.60 0.79 1.00
pack=2.00   t: 0.00 0.02 0.08 0.18 0.33 0.51 0.73 1.00
pack=3.00   t: 0.00 0.00 0.02 0.08 0.19 0.36 0.63 1.00
pack=4.00   t: 0.00 0.00 0.01 0.03 0.11 0.26 0.54 1.00
```

### Measured authored usage

Across all 139 Oblivion trees, 401 branch levels:

- **92/401 levels (23%) store `seg_pack != 1.0`** — median 1.0, range 0.0–4.0.
- Extremes: `treecamoranparadise04` L0 = **4.0**; `dtree01leaves` /
  `dtree02` / `dtree04` (+ their `leaves` variants) L0 = **3.0** with 30
  length segments.
- `seg_pack = 0.0` also occurs, which collapses `t` to 1.0 for every ring —
  needs a guard.

### Why this matters for silhouette and branch shape

`t` feeds **both** the radius profile (6006) and the flexibility profile
(6003). So `seg_pack` simultaneously controls:

1. **Where the stem tapers** — a high pack keeps the base thick far up the
   limb, then narrows sharply.
2. **Where the stem bends** — flex_profile gates the gravity response along
   the stem, so packing shifts the bend toward the tip.

Our `_grow_stem` uses a **uniform** `t = i / n_rings`
([spt_generator.py:357](../../asset_convert/speedtree/spt_generator.py#L357)) and ignores
`seg_pack` entirely. On the 23% of levels that author it, both the taper
profile and the bend distribution are placed at the wrong points along the
limb.

**This is the first engine-verified, directly actionable branch-shape defect
found.** It is a small, contained change (one exponent in the ring parameter),
it is authored data rather than a heuristic, and it affects taper and curvature
together.

Related but NOT yet decoded: `seg_keep_length` (26005) / `seg_keep_cross`
(26006) also modulate tessellation and are read by our parser but unused.

## 6o. `0x78feb0` is the TUBE CROSS-SECTION EMITTER — not a bend integrator
<a id="6o-0x78feb0-tube-cross-section"></a>

> **🛑 CORRECTION.** Two earlier drafts of this section called `0x78feb0` the
> "bend integrator" and claimed it proved a constant-curvature gravity arc.
> **That was wrong.** The error was an unnormalized stack offset: the `lea edx,
> [esp+0x84]` at `0x793477` executes *after* 16 bytes of argument pushes, so the
> pointer is `esp0+0x74`, not `esp0+0x84`. `esp0+0x84` is gravity; `esp0+0x74`
> is the **texture-coordinate block**. Everything downstream of that mistake was
> a misreading.

### What it actually does

`arg5` points at `esp0+0x74`, whose three floats are written at
`0x7927c8`/`0x7927cd`/`0x7927d5`, immediately before the call:

```
0x7927b6  fld  [esi+0x14]      ; 6013 u_tile
0x7927b9  jne  0x7927c5        ; if (u_abs != 0) skip scaling
0x7927bb  fmul [esp+0x6c]      ;   * run length
0x7927bf  fmul [0xb2b714]      ;   * 2*pi
0x7927c8  fstp [esp+0x74]      ; P[0] = U tiling term
0x7927cd  fstp [esp+0x7c]      ; P[1] = V tiling term
0x7927d5  fstp [esp+0x80]      ; P[2]
```

The loop then sweeps `u` from 0 to 1 in `1/arg2` steps and builds, per step:

```
a  = u * P[0]                       ; 0x78ff87  = u * 2pi * u_tile * run
v' = row0*sin(a)  + row1*cos(a)     ; 0x78fff7-0x790033
a2 = a + pi/2                       ; 0x79003d
w' = row0*sin(a2) + row1*cos(a2)    ; 0x79008d-
```

`row0`/`row1` (`esi+0x28…0x3c`) are the ring's **radial basis vectors**, so
`v'` sweeps a circle and `w'` is its perpendicular. This is a textbook tube
cross-section: `arg3 = word[esi+0x20]` is the vertex count per ring, `v'` gives
positions/normals, `w'` gives tangents, and `P[0]`/`P[1]` carry the bark UVs.

Five independent checks agree: the `2*pi * u` term at `0x78ff7c`; `step =
1/count`; the `+pi/2` perpendicular; the radial basis rows; and the caller
writing U/V tiling into `P` on the immediately preceding lines.

### Consequences — what this retracts

- **There is no engine evidence here for a constant-curvature gravity arc.**
  The `nif_conversion_notes.md` claim (linear-rate arc; an exponential approach
  "left every branch a straight stick") stands on its original billboard
  iteration and is **NOT** engine-verified. Do not cite §6o for it.
- **"The bend is computed absolutely from `u`, not accumulated"** — retracted.
  That described ring vertices, not stem curvature.
- **"Planar bending in a fixed plane"** — retracted; that is the ring plane.
- The prologue's `V = V + (1-V)*W` / square / `(1-G) + G*V` blend is real, but
  it conditions a **tessellation** parameter, not a bend.

## 6p. 🛑 THE REAL BEND CODE — FOUND (`0x793206` → `0x78f160` → `0x78edd0`)
<a id="6p-real-bend-code"></a>

Located by following the flexibility product instead of the argument block.

### The chain

In the ring loop, `0x792f95`-`0x792fab` computes and parks the flex term:
```
flex_term = Evaluate(6003 flex_profile, t) * flexibility   ; 0x792f9a
[esp+0x38] = flex_term                                     ; 0x792fab
```
Then at `0x7931e0`-`0x793219` the **bend angle** is assembled:
```
angle = (X * -1.0)            ; 0xa3d360 = -1.0
      * [esp+0x94]
      * [esp+0x38]            ; flex_profile * flexibility
      * [esp+0x30]            ; the crown-start remap value (§6o, 0x793414)
0x793219  call 0x78f160(angle)
```

### `0x78f160` — build an axis-angle rotation matrix

```
0078f168  fdiv qword [0xa8ba48]     ; f64 = 57.2957802  => DEGREES -> RADIANS
0078f178  call 0x986300             ; c = cos(theta)
0078f18d  call 0x986000             ; s = sin(theta)
0078f1a4  fld1 ; fsubrp             ; k = 1 - c
... products of axis components with k, plus s cross-terms ...
0078f1c0  fstp [esi+0x00]           ; m00
0078f1e0  fstp [esi+0x04]           ; m01
0078f1f3  fstp [esi+0x08]           ; m02
0078f1fe  fstp [esi+0x0c]           ; m10   ... 3x3 matrix at esi
```
This is the standard **Rodrigues rotation matrix** for an arbitrary axis.

### `0x78edd0` — 3x3 matrix × vector

```
0078edd4  [edx+0x0c]*[ecx+0x04] + [edx+0x00]*[ecx+0x00] + [edx+0x18]*[ecx+0x08]  -> [eax+0]
0078edee  [edx+0x10]*[ecx+0x04] + [edx+0x04]*[ecx+0x00] + ...                    -> [eax+4]
```
Applied at `0x793230` to rotate the stem's direction/frame.

### What this establishes

1. **The bend angle is in DEGREES** (`/57.2957802`) — matching the SPT's
   authored units for gravity/start-angle, no radian conversion needed.
2. **The bend magnitude is a PRODUCT**:
   `angle ∝ flex_profile(t) * flexibility * crown_remap * (-1)`.
   So flexibility gates the bend exactly as our generator assumes, and the
   flex **profile** (6003) shapes it along the stem — also as we assume.
3. **The rotation is a true axis-angle (Rodrigues) rotation of the frame**,
   not a planar 2-D sweep.
4. The crown-start remap (§6o, `0x793414`) multiplies directly into the bend
   angle — confirming it is a **bend** term, which is what the §6o priority
   entry assumed.

### 🛑 THE BEND ANGLE — FULLY RESOLVED

```
0x79317c  mov  ecx, [edx+0x70]        ; 6017 ANGLE PROFILE   (offset verified §3.1)
0x7931ba  call 0x784210               ; v = Evaluate(angle_profile, t)
0x7931bf  fsub 0.5                    ; v - 0.5              (f64 0.5 @ 0xa2faa0)
0x7931d6  fadd st0,st0                ; * 2      -> signed, range [-1,+1]
0x7931e3  fmul -1.0                   ; negate               (f64 -1 @ 0xa3d360)
0x7931ff  fmul [esp+0x94]
0x793206  fmul [esp+0x38]             ; flex_profile(t) * flexibility
0x79320a  fmul [esp+0x30]             ; crown-start remap (§6o, 0x793414)
0x793219  call 0x78f160(angle)        ; -> Rodrigues matrix, DEGREES
0x793230  call 0x78edd0               ; -> 3x3 matrix * vector
```

So:
```
angle_deg = -2 * (angle_profile(t) - 0.5)
          * <factor at esp+0x94>
          * flex_profile(t) * flexibility
          * crown_remap
```

**The `(y - 0.5) * 2` remap is the key semantic**: section 6017 is a *signed*
deflection curve centered on 0.5. Below 0.5 bends one way, above 0.5 the other,
and **a flat 0.5 profile produces no bend at all**.

**Corroborated against the corpus** (all 401 branch levels of the 139 Oblivion
trees): only **22/401 (5%)** are flat at 0.5, median max-deviation is **0.500**,
and the strong ones start at exactly `y(0) = 0.50` and diverge along the stem —
i.e. no bend at the base, increasing toward the tip, which is what the formula
predicts and what real limbs do:

| tree | level | y(0) | y(1) | max dev |
|---|---|---|---|---|
| treesugarmaplefreesu / treejapanesemaple | 1 | 0.50 | 1.75 | 1.25 |
| shrubazalea{su,pinksu,fa} | 1 | 0.50 | 1.30 | 0.80 |
| treeenglishoakunique01su | 1 | 0.37 | 1.11 | 0.61 |
| treescotchpineforest{,snow} | 2 | 0.96 | −0.03 | 0.53 |
| treedogwoodsu | 0 | 0.00 | 1.01 | 0.51 |

Note `treescotchpineforest` runs 0.96 → −0.03 (bends the *opposite* way toward
the tip) and `treedogwoodsu` L0 runs 0.00 → 1.01 (full sweep) — behaviour a
magnitude-only model cannot express.

**This also settles the 6017 naming question.** `FORMAT` calls it "Gravity
Profile" (CAD 4 UI) while `spttools/NOTES` argues for "Angle Profile" (v3 UI).
The engine uses it as a **signed bend-deflection profile**, which is the
"Angle Profile" reading. Our parser already stores it as `gravity_profile` and
the generator already treats it as an angle profile — the naming is cosmetic,
the semantics are right.

### The SECOND bend term — DISTURBANCE (`0x793248`-`0x793280`)

Immediately after the angle-profile rotation, the loop runs a second,
independent rotation driven by **6000 disturbance** (`[esi+0x50]`, offset
verified §3.1):

```
0x793248  mov  ecx, [esi+0x50]      ; 6000 DISTURBANCE
0x79324f  call 0x784370             ; d1 = EvaluateVar(disturbance, <param>)
0x793254  mov  ecx, [esi+0x50]      ; same curve again
0x79325b  fld  [esp+0x50]           ; the ring parameter t
0x793263  call 0x784370             ; d2 = EvaluateVar(disturbance, t)
                                    ;      param verified = E-176 = the
                                    ;      pow(seg_pack) ring parameter
0x793280  call 0x78ef60(d1, d2)     ; TWO-ANGLE rotation, both in DEGREES
```

`0x78ef60` divides by `57.2957802` and takes `sin`/`cos` of **two** angles —
a two-axis (yaw+pitch style) deflection, not a single-plane bend.

`0x784370` is a **variance-aware** spline evaluator: like `0x784210` it ends in
`RandomRange` (`0x78ea00`), so each disturbance sample carries its own random
draw. Two *separate* draws (`d1`, `d2`) per ring give the deflection a
direction as well as a magnitude.

This is the engine's disturbance model: **two independent variance-carrying
samples of curve 6000 per ring, applied as a two-angle rotation**. Our
generator instead uses a single deterministic sine "snake" with a per-stem
random phase ([spt_generator.py:346-350](../../asset_convert/speedtree/spt_generator.py#L346-L350)),
which is a different construction.

## 6q. 🛑 THE COMPLETE GRAVITY BEND MODEL — CLOSED
<a id="6q-complete-gravity-bend-model"></a>

All slots resolved with a real **esp tracker** (`tools/disasm/oblivion_disasm.py
--esp`), not per-block hand counting. That matters: the branch builder
addresses the same frame slot with different `[esp+N]` offsets depending on how
many pushes are live, and the earlier per-block naming produced wrong
identifications twice.

Frame-entry-relative (E) addresses:

| write site | operand | E-rel | meaning |
|---|---|---|---|
| `0x792704` | `fstp [esp+0x84]` | **E−124** | GRAVITY, `Evaluate(6001)` |
| `0x79273c` | `fstp [esp+0x80]` | E−128 | FLEXIBILITY, `Evaluate(6002)` |
| `0x792fab` | `fstp [esp+0x38]` | E−200 | `flex_profile(t) * flexibility` |
| `0x793194` | `fstp [esp+0x98]` | E−108 | axis.x |
| `0x7930cb` | `fstp [esp+0x28]` | **E−216** | `theta_deg` |
| `0x7930f5` | `fstp [esp+0x20]` | **E−224** | torque falloff |

and the three reads in the angle product all resolve cleanly:

| read site | operand | E-rel | resolves to |
|---|---|---|---|
| `0x7931ff` | `fmul [esp+0x94]` | **E−124** | **GRAVITY** ✔ |
| `0x793206` | `fmul [esp+0x38]` | **E−216** | **theta_deg** |
| `0x79320a` | `fmul [esp+0x30]` | **E−224** | **torque falloff** |

### The formula

```
theta_deg = acos(dot) * 57.295780               ; 0x986130=acos, 0xa8ba48
torque    = 1 - |theta_deg - 90| / 90           ; 0xa65a18=90, 0xa8c698=1/90
axis      = normalize( dir × (0,0,-1) )         ; 0x7930f9-0x793141

angle_deg = -2 * (angle_profile(t) - 0.5)       ; 6017, signed about 0.5
          * gravity                              ; 6001
          * theta_deg
          * torque

frame = Rodrigues(axis, angle_deg) * frame      ; 0x78f160 -> 0x78edd0
```

### What each piece means

- **`axis = dir × (0,0,-1)`** — the horizontal axis perpendicular to the stem.
  Rotating about it tilts the limb toward or away from the ground. The
  reference vector `(0,0,-1)` is the global at `0xb2b724`-`0xb2b72c`
  (components read as 0, 0, −1); all three cross-product terms verified
  individually.
- **`torque = 1 - |theta - 90|/90`** — the classic gravity moment: **1 when the
  limb is horizontal, 0 when it points straight up or straight down.** Note it
  is a **LINEAR falloff in degrees**, not the `sin(theta)` our generator uses
  ([spt_generator.py:369-376](../../asset_convert/speedtree/spt_generator.py#L369-L376)).
- **`theta_deg`** appears as a *second*, independent factor — so the bend scales
  with the angle from the pole **and** with the torque falloff.
- **Everything is in DEGREES**; `0x78f160` divides by 57.2957802 internally.

### Consequences for `spt_generator.py`

1. `GRAVITY_RESPONSE = 4.5` has **no engine counterpart**. The magnitude comes
   entirely from `gravity * theta_deg * torque * 2*(angle_profile-0.5)`.
2. Our `sin(theta)` torque is the wrong curve — the engine uses
   `1 - |theta-90|/90`. Both peak horizontally, but the engine's is linear and
   reaches exactly 0 at both poles.
3. Our tropism rotates toward a **pole vector**; the engine rotates about
   `dir × (0,0,-1)`. These differ once a stem is not in the vertical plane.
4. The **angle profile (6017) is a signed multiplier on the whole bend**, so a
   flat-0.5 profile disables gravity bending entirely for that level.

### Rotation is CUMULATIVE — confirmed

```
0x792fa5  lea ebx, [ebp+0x1c]        ; ebx = the stem's RUNNING frame
0x792fb6  rep movsd (9 dwords)       ; seed it from the previous ring
...
0x793230  call 0x78edd0              ; ecx = ebx  -> rotate the running frame
0x793242  rep movsd (9 dwords)       ; write the result BACK into ebx
```

`ebx` points at `[ebp+0x1c]` — a persistent 36-byte (3x3) frame on the stem
object, not a scratch copy. Each ring's rotation is applied to it and the
result stored back, so **curvature accumulates ring over ring**. The
disturbance rotation at `0x793280` operates on the same `ebx`.

This matches our `_grow_stem`, which also rotates a running direction
([spt_generator.py:356-396](../../asset_convert/speedtree/spt_generator.py#L356-L396)) —
that structural choice is correct; only the torque curve, the axis, and the
magnitude terms differ (§6q).


### Where the real bend code is

**Found — see §6p and §6q.** The bend lives in the ring loop of the branch
builder at `0x7930f9`-`0x793280`, not in `0x78feb0`:

- `0x7930f9`-`0x793141` builds `axis = dir x (0,0,-1)` and normalizes it.
- `0x7930ac`-`0x7930f5` computes `theta_deg` and the torque falloff.
- `0x7931ba`-`0x793219` assembles the angle and calls `0x78f160` (Rodrigues).
- `0x793230` applies it via `0x78edd0` (3x3 x vector), storing back to the
  running frame at `[ebp+0x1c]` — cumulative per ring.
- `0x793248`-`0x793280` adds the two-sample disturbance rotation.

## 6r. Implemented so far (2026-08-21) — ENGINE-DERIVED ONLY
<a id="6r"></a>

Four changes are in `spt_generator.py`.  Each cites the disassembly; none rests
on image comparison or on a silhouette score.

| change | engine basis |
|---|---|
| Bend: no `/n_rings` division | The angle product at `0x7931ff`-`0x793219` has **no ring-count term**; the full angle is applied at every ring. |
| Leaf card `* 0.5` | `0x79a266  fmul qword [0xa2faa0]`, that f64 is **0.5**. Full expression `0x79a25b`-`0x79a2a6`. |
| One card per attachment | SpeedTreeRT emits ONE quad per leaf (`m_pLeafMapCoords`, 4 corners) — `references/ck-cmd-master/src/spt/sptconvert.cpp:1479-1503`. |
| Bare-tree leaf gate | Child count is `freq * stored_length` (`0x793559`), so `child_freq == 0` yields zero leaves. dtree01/04/10 are bare dead trees. |

### 🛑 A methodology correction

An earlier draft of this section reported a **"combined error 0.4210 → 0.3158
(−25%)"** from a sweep of ~40 constants scored against Oblivion's billboard
alpha silhouettes, and used those deltas to justify changes.

**That was curve-fitting, and the conclusions from it have been reverted.**
A billboard is a 2-D projection of the engine's output, not the engine's
algorithm; optimising against it reproduces the same billboard-fitting that
produced the original generator. Two changes rested on the score alone and are
now backed out:

- `_RING_CAP` → 999 (the authored 6009 counts are probably right, but that must
  be shown from the ring loop, not from a score delta).
- `cards_per` 3 → 1.

Sweep results are **not** evidence and must not be cited as such. In particular
the claim that *"stem caps are irrelevant — 2x/4x/uncapped all score flat"* is
withdrawn: it measured a proxy.

**Rule for this work: change the generator only where the disassembly says what
the engine does.** Previews are for the user's final check, not an input to
diagnosis.

### Flare decomp — RESOLVED

Superseded by **§6s**, which recovers the whole flare model. The offset
table that previously appeared here was wrong (16003 is an **int32 count**
stored with `mov`, which is why a float-read scan for `+0x28` found nothing).
The corrected map and the algorithm are in §6s.

### Still guessed — decompile before touching

1. **Leaf orientation angle source.** The engine draws
   `RandomRange(map[+0x24], map[+0x28])` and negates it on odd branch-level
   counts (section 6w, fully decompiled). What is NOT known is which authored
   section reaches those two slots — three candidate mappings were tested and
   all three falsified (see 6w). Our `orientation_var * 90` plus the
   `uniform(-0.3, 0.3)` / `uniform(-0.25, 0.25)` fudges in `leaf_card` remain
   **invention** and are deliberately left unchanged rather than replaced by a
   fourth guess.
2. **Leaf quad CORNER construction.** Card *dimensions* are settled (6t) and
   `origin` (4004) is the pivot. How the engine lays the four corners out in
   3-D is not decompiled — but it builds camera-facing billboards there, which
   we deliberately replace with crossed quads, so this is largely moot by
   design.

~~Trunk radius / flares~~ — **RESOLVED, section 6s.**
~~Leaf card size / aspect~~ — **RESOLVED, section 6t.**
~~Leaf attachment distribution~~ — **RESOLVED, section 6v.**
~~Blossom weighting~~ — **RESOLVED, section 6u** (the sections are dead).
~~Child-count formula~~ — **RESOLVED, section 6v** (proved through the
argument frame; our formula was already right).

## 6s. 🛑 FLARES — FULLY RECOVERED (`0x791e80`, `0x78f2c0`, `0x790138`)
<a id="6s-flares-fully-recovered"></a>

This CLOSES the largest remaining unknown.  `_flare_multiplier` in
`spt_generator.py` was invented; the engine's model is below and is exact.

### The parse-side field map — CORRECTED

The offsets recorded in the previous draft of §6r were WRONG (they were read
from a mis-stepped walk).  Disassembling the compare chain at
`0x7a2cc4`-`0x7a2ef4` gives the stores directly.  Every handler ends in
`fstp dword ptr [<reg> + N]` on the level object fetched from a vector, so the
fields are **contiguous 4-byte slots**, not the gapped map previously recorded:

| sid | store site | level offset | type | meaning |
|---|---|---|---|---|
| 16002 | `0x7a2cfe` | `+0x24` | f32 | seg_pack |
| 16003 | `0x7a2d3e` | `+0x28` | **int32** (`mov`, not `fstp`) | **flare count** |
| 16004 | `0x7a2d82` | `+0x2c` | f32 | flare radial angle fraction |
| 16005 | `0x7a2dc4` | `+0x30` | f32 | radial influence |
| 16006 | `0x7a2e04` | `+0x34` | f32 | radial influence variance |
| 16007 | `0x7a2e2e` | `+0x38` | f32 | radial exponent |
| 16008 | `0x7a2e56` | `+0x3c` | f32 | length distance |
| 16009 | `0x7a2e7c` | `+0x40` | f32 | length distance variance |
| 16010 | `0x7a2ea6` | `+0x44` | f32 | radial distance |
| 16011 | `0x7a2ece` | `+0x48` | f32 | radial distance variance |
| 16012 | `0x7a2ef4` | `+0x4c` | f32 | length exponent |

Note 16003 stores with `mov dword ptr [ecx+0x28], eax` — it is an **integer
count**, which is why the earlier float-read scan for `+0x28` found only
unrelated matrix code.  That mistake is what stalled the previous session.

### Stage 1 — flare creation, `0x791e80` (one call, from `0x792690`)

Called from inside the branch builder `0x7925b0` immediately before the
spline eval and the +/-180 azimuth draw.  It builds a
`std::vector<Flare>` whose element size is **0x18 = 24 bytes = 6 floats**
(proved by the `push_back` at `0x7916d0`: `add edi, 0x18`).

```
if (level->flare_count == 0) return;              # 0x791e89

base_angle = rand01() * 2*PI                      # 0x791ea0-0x791ee0
angle_step = 2*PI / flare_count                   # 0x791ee4  fidiv

for i in range(flare_count):                      # 0x792082-0x79208c
    # --- angular position -------------------------------------------
    spread = angle_step * level->f16004           # 0x791f00-0x791f07
    ang    = uniform(spread, 2*PI - spread)       # 0x791f0b-0x791f40
    ang    = ang * i + base_angle                 # 0x791f48-0x791f4c  (fimul by loop counter)
    if ang > 2*PI: ang -= 2*PI                    # 0x791f54-0x791f69

    # --- the five stored fields -------------------------------------
    f.angle      = ang                            # slot +0x00
    f.radial_exp = level->f16007                  # 0x791f73 -> +0x38
    f.len_exp    = level->f16012                  # 0x791f7a -> +0x4c
    f.radial_infl= uniform(r05 - r06, r06 + r05) / 57.29578   # 0x791f81-0x791fd8  (DEGREES -> RADIANS)
    f.radial_dist= uniform(r10 - r11, r11 + r10)  # 0x791fdc-0x792025
    f.len_dist   = uniform(r08 - r09, r09 + r08)  # 0x792029-...
    push_back(f)                                  # 0x79207d
```

`uniform(a,b)` here is **CRT `rand()`**, not the Newran RNG of §6b:
`0x9859dd` is the MSVC LCG (`*0x343fd + 0x269ec3`, `>>16 & 0x7fff`) and every
draw is `rand()/32767.0` (`fdiv qword [0xa3d5a8]`, f64 = 32767).  The idiom
compiles to `lo + t*(hi-lo)` with `lo`/`hi` spilled first — note the operands
are stored as `(mean - var)` and `(var + mean)`, so **variance is symmetric**.

Resulting 24-byte Flare struct:

| offset | field |
|---|---|
| `+0x00` | angle (radians) |
| `+0x04` | radial influence (radians) |
| `+0x08` | radial exponent |
| `+0x0c` | length distance |
| `+0x10` | length exponent |
| `+0x14` | radial distance |

(Confirmed by the consumer's reads at `0x78f2cc`, `0x78f33e`, `0x78f34e`,
`0x78f366`, `0x78f375`, `0x78f385`, `0x78f390`, `0x78f39b`.)

### Stage 2 — per-vertex application, `0x790138`-`0x7901d5`

Inside the tube cross-section emitter `0x78feb0`, for **every ring vertex**:

```
scale = 0.0
for f in flares:                       # 0x790138, size = (edi+0x38 - edi+0x34)/0x18
    scale += flare_weight(f, vertex_angle, t)      # call 0x78f2c0 at 0x7901a5
    # accumulate: 0x7901aa  fadd [esp+0x9c]
scale += 1.0                           # 0x7901c1-0x7901c8  (fadd qword [0xa2f928] = 1.0)

radius' = radius * scale               # 0x7901d5-0x790213 via [esi+0x18]
```

So the flare factor is **`1 + sum(weights)`**, applied multiplicatively to the
ring radius.  With no flares the sum is 0 and the factor is exactly 1.0 —
which is the correctness check for any implementation.

`0x7901b4  add dword ptr [esp+0x80], 0x18` confirms the 24-byte stride.

### Stage 3 — the per-flare weight, `0x78f2c0`

```
def flare_weight(f, vert_angle, t):        # t = position along the branch, 0..1
    d = vert_angle - f.angle               # 0x78f2d2-0x78f2e2
    d = abs(d)                             # 0x78f2ea
    if d > PI:                             # 0x78f2f4  (f64 [0xa3d5b8] = PI)
        if vert_angle < f.angle: d += 2*PI # 0x78f30e / 0x78f3bc
        else:                   d -= 2*PI
        d = abs(d)                         # 0x78f32c
    if d >= f.radial_infl:  return 0.0     # 0x78f33e-0x78f348 -> 0x78f3cf
    if t   >  f.len_dist:   return 0.0     # 0x78f34e-0x78f364 -> 0x78f3d1

    a = 1.0 - d / f.radial_infl            # 0x78f366-0x78f36d
    a = pow(a, f.radial_exp)               # 0x78f375-0x78f378  (0x985b70 = pow)
    a = a * f.radial_dist                  # 0x78f385

    b = (f.len_dist - t) / f.len_dist      # 0x78f34e + 0x78f390
    b = pow(b, f.len_exp)                  # 0x78f39b-0x78f39e

    return a * b                           # 0x78f3ac
```

Both gates return **0.0**, so a flare outside its angular window or past its
length reach contributes nothing.

### 🛑 Negative flare distance is LEGAL — do not clamp it

`0x791fdc`-`0x792025` stores `radial_dist` raw: **no `fabs`, no clamp, no
`fldz`/`fcom` guard**.  When the authored variance exceeds the mean the draw
goes negative and the flare becomes an inward *notch* (trunk fluting) rather
than a buttress.

`treecpswampcypressforest01` authors `r_dist = 0.300 +/- 0.500`, so
`uniform(-0.2, 0.8)` legitimately yields negatives — measured `-0.1254` for
one of its flares.

Measured over all 249 flare-bearing levels in the export corpus
(36 angles x 41 positions each):

* minimum `1 + sum(weights)` = **0.9349** (`treecpswampcypressforest01`)
* samples with scale <= 0 (which would invert the ring): **0**

So the factor dips slightly below 1.0 on a handful of trees and never
approaches zero.  A `max(..., 0)` clamp would be a **deviation from the
engine**, not a safety fix — do not add one.

### What this means for `spt_generator.py`

`_flare_multiplier` must be **deleted** and replaced with the above.  The
engine's flare is:

* **angularly localised** — it only thickens a wedge of the trunk around
  `f.angle` of half-width `f.radial_infl`, decaying as
  `(1 - d/infl) ** radial_exp`.  Our version thickened the whole ring
  uniformly, which is why `treeenglishoakforest01su` came out at **64.8**
  world units against an authored **55.0**: we applied a full-ring bulge where
  the engine applies a directional buttress.
* **root-limited** — it dies at `t >= len_dist`, with profile
  `((len_dist - t)/len_dist) ** len_exp`.
* **additive across flares**, then `1 +` the sum.

Because it is per-vertex and angular, flares cannot be reproduced by any
radius-only scalar; the ring must be emitted with per-vertex radii.

## 6t. 🛑 LEAF MAP RECORD + CARD SIZE — RECOVERED
<a id="6t-leaf-map-record-card"></a>

### The authored leaf-map record is 0x54 (84) bytes — parse block `0x7a6020`

The 4000-4012 handlers are dispatched through the jump table at **`0x7a6628`**
(13 entries, base id 4000; the second table at `0x7a6648` covers 4013+).  Each
handler writes into a stack scratch record at `[esp+0x138]` which is then
copy-constructed into the map vector by **`0x7a3470`** (`0x7a6428`), and the
array stride is **`0x54`** (`0x7a6456  add dword ptr [esp+0x14], 0x54`).

Mapping the handler stores through the copy constructor gives the record:

| sid | parse store | record offset | field |
|---|---|---|---|
| 4000 | `0x7a620a` | `+0x00` | blossom flag (byte) |
| 4001 | `0x7a6227`/`0x7a6231`/`0x7a623b` | `+0x04`,`+0x08`,`+0x0c` | color (vec3) |
| 4002 | `0x7a624e` | `+0x10` | orientation variance |
| 4003 | `0x7a625a` | `+0x14` | texture name (std::string, 0x1c bytes) |
| 4004 | `0x7a6356`/`0x7a6360`/`0x7a636a` | `+0x30`,`+0x34`,`+0x38` | **origin** (pivot on the card) |
| 4005 | `0x7a6384`/`0x7a638e`/`0x7a6398` | `+0x3c`,`+0x40`,`+0x44` | **size** (fraction of tree Size) |
| 4006 | `0x7a63b2`/`0x7a63bc`/`0x7a63c6` | `+0x48`,`+0x4c`,`+0x50` | **world size** (pre-multiplied) |
| 4007 | `0x7a63cf` | *discarded* (`fstp st(0)`) | read and thrown away |

**4007 is parsed and DISCARDED** (`0x7a63d6  fstp st(0)`) — it has no effect.

### 🛑 4006 is DERIVED, not authored — `0x7a4839`

```
0x7a4839  fld  dword ptr [eax + ebx + 0x3c]   ; size.x
0x7a483f  fmul dword ptr [esi + 0x4c]         ; * tree size
0x7a4848  fstp dword ptr [eax + 0x48]         ; -> world_size.x
0x7a484b  fld  dword ptr [eax + 0x40]         ; size.y
0x7a484e  fmul dword ptr [esi + 0x4c]         ; * tree size
0x7a4851  fstp dword ptr [eax + 0x4c]         ; -> world_size.y
0x7a4845  add  ebx, 0x54                      ; next map
```

The engine **overwrites** the authored 4006 with `4005 * Size`.  This
independently confirms the generator's existing comment that 4006 is stale in
~15 shrubs and that 4005 must always be used — that behaviour is correct and
should be kept.

### The runtime leaf-map struct is 0x2c (44) bytes

The placement code (`0x79a000`) indexes a *different*, condensed array at
`[edi+0x44]` with stride **`0x2c`** (`imul ebx, ebx, 0x2c` at `0x79a258`;
divisor magic `0x2e8ba2e9`+`sar 3` = /44).  Fields read there:

| offset | read at | use |
|---|---|---|
| `+0x1c` | `0x79a25b` | size **X** |
| `+0x20` | `0x79a29b` | size **Y** |
| `+0x24` | `0x79a315` | orientation variance (feeds `RandomRange`) |
| `+0x28` | `0x79a301` | (read into the leaf record) |

### 🛑 THE CARD SIZE EXPRESSION — settled

```
0x79a25b  fld   [map + 0x1c]        ; size.x
0x79a263  fmul  [leaf + 0x10]       ; * stem/attachment scale
0x79a266  fmul  qword [0xa2faa0]    ; * 0.5
0x79a26c  fstp  [leaf + 0x14]       ; -> half-width

0x79a29b  fld   [map + 0x20]        ; size.y
0x79a2a3  fmul  [leaf + 0x14]
0x79a2a6  fstp  [leaf + 0x14]
```

So the card's two dimensions come from **`size.x` and `size.y` separately**.
The `* 0.5` is the half-extent conversion (already implemented, section 6r).

**This retires the UV-crop aspect rule as invention.**  `spt_generator.py`
currently computes the card aspect from the texture crop
(`crop_ar = du/dv`) and then solves `w`/`h` from an area — a construction with
no counterpart in the engine.  The authored data confirms the engine's reading:
every sampled map stores an explicit `size = (x, y)` pair
(`dbush03` = 0.03/0.03, second map 0.04/0.04) plus an `origin` that is
**not** centered (0.539, 0.493 / 0.475, 0.562), i.e. the pivot is authored per
map — exactly what `origin` at `+0x30` is for.

### Consequence for the generator

- Card width/height must be `size.x * K` and `size.y * K` (times the
  attachment scale and the existing 0.5), NOT derived from the crop aspect.
- `origin` is the attachment pivot **on the card** and is already applied at
  `spt_generator.py` line ~1183.
- 4007 must be ignored (the engine discards it).

## 6u. 🛑 LEAF MAP SELECTION IS UNIFORM — blossom sections are DEAD
<a id="6u-leaf-map-selection-uniform"></a>

### The selection rule — `0x79a1e8`-`0x79a229`

```
r         = RandomRange(0.0, 100000.0)   ; 0x79a1e8 (f32 [0xa3f3d8] = 100000)
                                         ; 0x79a1fe -> 0x78ea00, the Newran RNG
n         = (map_vector_end - map_vector_begin) / 0x2c    ; 0x79a1d2-0x79a1e6
i64       = (long long)r                 ; 0x79a21b  fistp qword
map_index = i64 % n                      ; 0x79a223  div ebx  -> remainder in edx
                                         ; 0x79a229  mov [esi+0x18], dl
```

It is **uniform over the maps**.  There is no weighting of any kind.

### Sections 3000-3010: what the engine actually does with them

Dispatched from the second table at **`0x7a6648`** (base id 3001; 3000 is the
`je` at `0x7a607c`).  `ebp` is restored to the leaf-system `this` at
`0x7a6473` before these run (it was set at `0x7a6062  mov ebp, ecx`).

| sid | handler | what happens |
|---|---|---|
| 3000 | `0x7a647c` | `fstp [ebp+0x24]` — stored |
| 3001 | `0x7a64a0` | `mov [ebp+0x2c], eax` — stored |
| 3002 | `0x7a64af` | `fstp [ebp+0x28]` — stored |
| 3003 | `0x7a64bb` | cursor advance only — **discarded** |
| 3004 | `0x7a64d9` | `fstp st(0)` — **discarded** |
| 3005 | `0x7a64d9` | `fstp st(0)` — **discarded** |
| 3006 | `0x7a651a` | cursor advance only — **discarded** |
| 3007 | `0x7a6523` | `fstp [ebp+0x20]` — stored |
| 3008 | `0x7a652f` | `mov [ebp+0xc], eax` — stored |
| 3009 | `0x7a64e4` | reads a byte, stores to `[ebp]` |
| 3010 | `0x7a650e` | `fstp [ebp+4]` — stored |

### 🛑 3000 / 3001 / 3002 are STORED AND NEVER READ

The leaf-system object holds its map vector at `+0x44`/`+0x48`.  Leaf placement
(`0x79a000`) reads **only `[edi+0x0c]`** (the branch-level vector) — never
`+0x20`, `+0x24`, `+0x28` or `+0x2c`.  A recursive-descent scan of the whole
SpeedTree band (`0x788000`-`0x7b8000`, 35,168 instructions reached from 358
candidate entries) found **no function that reads `+0x44` as a vector base and
any of `+0x24`/`+0x28`/`+0x2c` on the same register**.

So blossom distance (3000), depth (3001) and weight (3002) are parsed into the
object and then dead.

### Verified against the engine's own billboard

This is a pixel *count*, not a shape judgement, so the billboard is admissible
here — it is the engine's own render of its own output.

| tree | blossom maps | authored 3002 weight | uniform prediction | measured billboard non-green |
|---|---|---|---|---|
| `treedogwoodsu` | 1 of 3 | 0.23 | **33.3%** | **34.0%** |
| `shrubhydrangeabluesu` | 2 of 5 | 0.10 | 40.0% | 20.9% * |

\* hydrangea's flowers are blue-white and its foliage is blue-green, so the
green/non-green split under-counts them; dogwood (white flowers, green leaves)
is the clean measurement and it lands on the uniform prediction, **not** on the
authored weight.

### Consequence

`spt_generator.py`'s blossom scheme — blossom maps gated by
`leaf_blossom_distance` and taking `leaf_blossom_weight` of the picks — was
**invention** and is removed.  Selection is now `rng.integers(0, len(maps))`,
cached per attachment.

## 6v. Child count re-verified through the argument frame — `count = freq * stored_length`
<a id="6v-child-count-re"></a>

§6f asserted this from data.  It is now proved from the call frame.

```
0x793559  fld   [esi+0x10]        ; level.child_freq
0x79355c  fdiv  [esp+0x108]       ; / arg#1
0x793563  fmul  [esp+0x68]        ; * this stem's stored length
0x793567  call  0x9828c0          ; -> int
```

`[esp+0x68]` is the stem length local (written at `0x79281d` as
`spline_eval * length`, optionally clamped to `0.85 *` arg#9 via
`[0xa563d8]` = 0.85).  The same ratio idiom appears at `0x7927a4`
(`[esp+0x68] / [esp+0x108] * [esi+0x18]`), gated by the `u_abs`/`v_abs` flags —
this is the *relative length* the bark-V rule already uses.

**arg#1 is 1.0.**  At the top-level call (`0x7a48f9`) arg#1 is the `fld1` at
`0x7a488c` (verified by FPU trace: `esp+0x2c`/`0x30`/`0x40` all = 1.0), and the
recursive call at `0x79390f` passes `[esp+0x130]` = **arg#8**, which is never
written anywhere inside the function (only read, at `0x792ead`, `0x793499`,
`0x7937cb`, `0x7938c8`).  So the divisor stays 1.0 at every recursion depth.

Therefore `count = child_freq * stored_length` — **our formula is correct at
every level**, not just empirically for the trunk.

### The leaf-vs-branch switch — `[esp+0x1b]`

```
0x793597  cmp   ebx, eax          ; ebx = depth+1, eax = num_levels-1
0x793599  setge al
0x79359e  mov   [esp+0x1b], al
...
0x793515  cmp   byte [esp+0x1b], 0
0x79351a  je    0x793535          ; -> tube geometry (0x78f7a0)
0x79352a  call  0x79a000          ; -> LEAVES
```

The **last level emits leaves instead of a tube**, and `0x79a000` is called
**once per terminal branch** with one float argument.  That is the authored
indicator for leaf placement: leaves are not scattered along a twig by a
separate density rule — each terminal child *is* one leaf attachment.

## 6x. LEAF GEOMETRY IS EXTRACTABLE — the leaf sub-struct (MEASURED)
<a id="6x-leaf-geometry-extractable-leaf"></a>

`GetGeometry`'s dispatch at `0x78c6f0` is four independent `test bl,N` gates:

| bit | handler | what |
|---|---|---|
| 1 | `0x789fe0` | branches |
| 2 | `0x78a390` | fronds |
| **4** | **`0x788120`** | **leaves** |
| 8 | `0x7887a0` / `0x788430` | billboards |

The leaf getter `0x788120` does `lea ebx,[edi+0x78]` — the leaf sub-struct
occupies **`+0x78` of the SAME out-struct** the branch fields live in — and
hands it to the per-group filler `0x7989b0`, called once per billboard-leaf
group (`[tree+0xc0]`, read at `0x788133`).

### Layout, measured (NOT read off the getter's stores)

The branch table was originally derived from getter stores and had coords and
texcoords **transposed**; that mistake cost a round, so this layout was
established by probing the live buffers at several strides and keeping only
what is 100% finite:

| off | type | meaning |
|---|---|---|
| `+0x78` | byte | flag, set at `0x788219` / `0x7882e5` |
| `+0x7c` | float | tree height (84.0; stored `0x788310`) |
| **`+0x84`** | **uint16** | **leaf count** |
| `+0x8c` | float* | `count*N` in `[0, 0.863]` — normalized (size/wind) |
| **`+0x90`** | **float*** | **`count*3` leaf CENTERS**, same world space as the branch coords |

🛑 **`+0x84` is a UINT16, not a dword.** Ginkgo reads `0x000001d6`
(470) and looks like a clean dword; english oak reads `0x15ec023c`, whose low
word `0x023c` = **572** is the real count — the high word belongs to the next
member. Reading the full dword yields **367,788,604** and the dump silently
produces nothing. Measured counts: ginkgo 470, dogwood 1203, oak 572,
dbush03 100.

### The centers sit ON the branches — measured

Distance from each engine leaf center to the nearest branch vertex, as a
percentage of the tree's bounding diagonal:

| tree | leaves | median | p90 |
|---|---|---|---|
| dbush03 | 100 | **0.94%** | 2.16% |
| treeginkgo | 470 | **2.52%** | 4.19% |
| treeenglishoakforest01su | 572 | **4.15%** | 11.26% |
| treedogwoodsu | 1203 | **5.91%** | 16.03% |

This is the authored indicator that section 6k predicted: each terminal branch
child IS one leaf attachment (`0x793597` sets the leaf-vs-tube switch,
`0x79352a` calls the leaf path instead of the tube path), so foliage follows
the branch skeleton by construction — no placement heuristic involved.

The dogwood/oak p90 tails are **authored 6004 Placement Distance**, not error:
dogwood stores ~0.08, so its leaves genuinely puff off the twigs. Section 6b's
revised finding already established that the leaves are not an eroded outer
shell.

### What we take, and the one deviation

Positions, count, and map selection come from the engine. The engine emits ONE
camera-facing billboard per leaf, which Skyrim cannot render, so each center
becomes **two crossed quads** — the sanctioned deviation. Card size follows
section 6t (`size.x/size.y * K * 0.5`); map choice follows 6g (uniform modulo,
no blossom weighting). Implemented in
`asset_convert/speedtree/spt_engine_geom.py::_leaf_groups_from_centers`.

Shipped output verified: leaf card count is exactly 2x the engine leaf count
(ginkgo 940 = 2x470, dogwood 2406 = 2x1203, dbush03 200 = 2x100).


## 6y. 🛑 LEAF CARD SIZE — formula proven, BASE TERM STILL OPEN
<a id="6y-leaf-card-size-formula"></a>

### What is now PROVEN (disassembly + FPU simulation)

`0x79a258`-`0x79a2a6`, with `tools/disasm/oblivion_disasm.py --fpu` resolving the x87
stack:

```
0x79a258  imul ebx, ebx, 0x2c        ; map index * 44 (runtime leaf-map stride)
0x79a25b  fld  [ebx+eax+0x1c]        ; map[idx].size.x
0x79a263  fmul [esi+0x10]            ; * ACCUMULATED BRANCH ARC LENGTH
0x79a266  fmul qword [0xa2faa0]      ; * 0.5      (verified f64 = 0.5)
0x79a26c  fstp [esi+0x14]
0x79a29b  fld  [ebx+ecx+0x20]        ; map[idx].size.y
0x79a2a3  fmul [esi+0x14]
0x79a2a6  fstp [esi+0x14]            ; -> ONE scalar
```

The FPU simulator independently confirms `[esi+0x10]` is the running arc-length
sum (it prints the store as the accumulated `dx²+dy²+dz²` chain plus its own
previous value) and that `[esi+0x14] = [esi+0x10] * 0.5`.

Two facts follow that our implementation currently gets wrong:

1. **`size.x` and `size.y` MULTIPLY into a single scalar** — they are not
   width and height. Our code uses them as separate w/h.
2. **The scale term is the HOST BRANCH's arc length, not the whole-tree `K`.**

### ⚠ Why it was NOT switched over

The proven expression does not by itself yield a card dimension. Computed with
real per-strip arc lengths from the engine's own branch geometry:

| tree | arc len (med) | `size.x * arc * 0.5 * size.y` | x WORLD_SCALE |
|---|---|---|---|
| dbush03 | 7.09 | 0.0032 | **0.03** |
| treeginkgo | 133.04 | 0.4606 | 4.61 |
| oak | 22.85 | 0.0202 | 0.20 |

dbush03 is a ~30-world-unit bush; a 0.03-unit card is three orders of magnitude
too small. So `[esi+0x14]` is a **dimensionless scale factor**, and the base
dimension it multiplies has not been located. Shipping this would have replaced
a working approximation with invisible leaves.

### The per-leaf size array is NOT `+0x8c`

Read as one float per leaf it gives `-5.6e29 .. 1.3e11` and NaN means on
ginkgo, and pure garbage on dbush03. A full stride survey (1/2/3/4/6/8/12/16
across `+0x88`..`+0xa4`) found only `+0x88` fully finite at any stride, and its
values are **normalized** (dbush03 `0 .. 0.022`, ginkgo `-0.074 .. 0.982`), not
world-unit sizes. Candidate for the wind/size weight, not the card dimension.

### Measured: card size is NOT the main sparseness cause

Card width vs the engine's own nearest-neighbour leaf spacing:

| tree | NN spacing (med) | current card w | card/spacing |
|---|---|---|---|
| treedogwoodsu | 11.71 | 60.00 | **5.12x** |
| oak | 31.22 | 75.62 | **2.42x** |
| treeginkgo | 134.92 | 151.25 | 1.12x |
| dbush03 | 8.36 | 6.75 | **0.81x** |

Oak and dogwood cards already overlap heavily; only dbush03 is under-covered.
Uniformly enlarging cards to fix perceived sparseness would therefore be
fitting to appearance, not to the engine.

### Current state

Card size keeps the section 6t form (`size * K * 0.5`) pending identification
of the base dimension. This is DOCUMENTED APPROXIMATION, not a claim of
exactness: `K` stands in for the arc-length term, and `size.y` is used as a
height rather than multiplied in.


## 6z. 🛑 THE LOD-0 STRIP LIST IS INCOMPLETE — orphan vertex blocks
<a id="6z-lod-0-strip-list"></a>

### Symptom

`treecottonwoodsu` rendered with **no flared trunk base** (the tree juts out at
the bottom in its billboard, and did not in ours), plus 6 huge triangles
sheeting through the canopy.

### Two separate defects, both ours

**1. Strip lengths were WALKED, not read.** `pStripLengths` (+0x08) really is
the length array — stored as **UINT16**. Read as dwords it looks like garbage
pointers (`0x01040104`), which is simply two packed lengths: `0x0104` = 260.
Entry 3 reads `0x003c` = 60, matching the short branch strips.

The old code instead walked each index array "while the value stays in vertex
range", which cannot see a strip boundary: the words after a strip belong to
the next tube and are still valid indices. Every cottonwood strip read **272
where 260 is real**, and the 12 junk words stitched canopy (z≈950) to trunk
(z≈13) — max edge **74% of the tree diagonal**.

**2. The strip list does not cover every vertex.** Even with correct lengths,
560 of cottonwood's 2,044 vertices are referenced by NO strip, in 11 contiguous
blocks — including block **[0..167]** (z 0..7.3, radius to 8.5): the flare.
`flare_count = 3` and `flare_length_dist = 8.0` are authored on the trunk
level, so the flare ends at z≈8, exactly that block.

These blocks are plain tubes (consecutive rings), so
`spt_engine_geom._orphan_ring_triangles` stitches them back.

### Measured, do NOT retry

| attempt | result |
|---|---|
| count from `[self+4]+0x4c..+0x50` | 2 entries of unrelated pointers; emitted 2 strips whose "indices" were heap addresses |
| `*(void**)self + 0x4c` | empty vector, 0 strips |
| extend past `pStrips[32]` | entry 32 fails validation — the array really holds 32 |
| terminate a strip at its first doubled index | repeats are PER-RING, not per-strip; truncated strip 0 to 20 indices (2,484 tris → 284) |

### Ring inference is heuristic — and is BOUNDED, not trusted

Block lengths admit several plausible ring sizes (dogwood's are 24/48/72/96
with gcd 24, but its real ring is 12). Three successive scoring heuristics all
mis-chose for some blocks, yielding **118–361 triangles spanning up to 46% of
the tree**; one attempt regressed 118 → 155.

Rather than keep tuning a guess, the repair **discards any triangle longer than
10% of the tree diagonal**. A real tube quad is tiny next to the tree, so a
long one is by definition a mis-stitch. That bounds the damage whatever the
inference does — measured 0 oversized triangles across every tree tested.

Two further rules that were each wrong once:

* **Do not wrap the ring closed.** Vertices 588 and 615 in block [560..643] are
  one ring apart, so joining last-to-first bridged the tube's open seam. Open
  quad runs reproduce the engine exactly.
* **Derive the winding, never assume it.** The first version wound the repair
  quads BACKWARDS, giving inward-facing normals (an inside-out trunk, reported
  from the render). Each quad is now oriented so its face normal points away
  from the tube centerline. Ground truth is the engine's own per-vertex
  normals: repair triangles agree **100%** (reversed: 0%).

### Result (shipped)

| tree | verts | tris | normals agree | max edge | base radius | oversized |
|---|---|---|---|---|---|---|
| treecottonwoodsu | 2044 | 3202 | 92.7% | 7.0% | **84.8** (was 3.1) | 0 |
| treeginkgo | 2035 | 3072 | 90.4% | 9.8% | 134.3 | 0 |
| treedogwoodsu | 1851 | 2457 | 95.6% | 10.8% | 14.6 | 0 |
| oak | 1634 | 2619 | 91.1% | 7.8% | 69.0 | 0 |
| dbush03 | 558 | 845 | 86.6% | 7.6% | 4.4 | 0 |

Cottonwood's repair triangles agree with the engine normals *better* than its
own strips do (92.7% vs 90.3%) — the engine's figure is lower only because
smooth normals on curved tubes disagree with flat face normals.

Guarded by `tests/test_spt_convert.py::TestEngineBranchPath`
(`test_orphan_repair_winding_faces_outward`,
`test_orphan_repair_emits_no_oversized_triangles`).


## 6aa. 🛑 "NO LEAF CHUNK" vs "ZERO LEAVES" — an ambiguity that grew foliage
<a id="6aa-no-leaf-chunk-vs"></a>

`dementiatree01` shipped with leaves that floated visibly off the tree
(median card-to-bark distance **17.6% of the diagonal**, p90 **36%**, against
2–5% on a healthy tree) and wore `mtreeleaves02.dds` — a **mania** atlas on a
**dementia** tree.

### The texture is NOT a bug

`dementiatree01` is built from `dtree01.spt`, whose only leaf map authors
`C:\Hope\SE	rees\MTreeLeaves02c.tga`, and its TREE record has an EMPTY
`ICON`, so nothing overrides it. Bethesda reused a mania atlas here. Verified
before changing anything.

### The real defect: an ambiguous dump

`dtree01` is a bare dead tree — its leaf level stores `child_freq = 0`, the
section 6t gate — so the engine correctly generates **zero** leaves. The
harness then wrote **no `SPTL` chunk at all**, and the reader could not tell
that apart from "this dump predates leaf support", so it fell back to the
Python foliage.

Those cards were placed against **Python** branches and pasted onto **engine**
bark they were never fitted to -- **264 cards** on `dtree01`, floating up to
**36% of the tree diagonal** off the bark. Hence the drift: the leaf bbox
reached x=+641 where the bark stops at x=+53.

### Fix

The dump now ALWAYS writes the chunk, with an explicit zero when there are no
leaves, and `read_leaf_centers` returns an **empty array** (never `None`) for
that case. `None` now means only one thing: no leaf data in this dump.

Affects the 5 trees whose leaf level gates off: `dtree01`, `dtree02`,
`dtree04`, `dtree10`, `treekvatchburnt`. All now ship bark with **0 leaf
triangles**; normal trees are untouched (cottonwood 1,000 leaf tris on
`treecottonwoodleavessu.dds`, ginkgo 1,880 on `treeginkgoleaves.dds`).

Guarded by `TestEngineBranchPath::test_bare_tree_gets_no_leaves`.


## 6w. 🛑 LEAF ORIENTATION — RECOVERED (`0x79a301`-`0x79a36c`)
<a id="6w-leaf-orientation-recovered"></a>

```
0x79a301  fld   [ebp+0x28]        ; map angle HI  -> pushed as arg2 ([esp+4])
0x79a315  fld   [ebx+0x24]        ; map angle LO  -> pushed as arg1 ([esp+0])
0x79a31b  call  0x78ea00          ; RandomRange(lo, hi)  = lo + t*(hi-lo)
0x79a328  fst   [esi+0x1c]        ; leaf.angle = that

0x79a32b  mov   eax, [edi+0x0c]   ; branch-level vector
0x79a337  ...   /0x18             ; its element count
0x79a348  test  al, 1             ; COUNT PARITY
0x79a34c  fmul  qword [0xa3d360]  ; that f64 is -1.0
0x79a352  fstp  [esi+0x1c]        ; leaf.angle = -leaf.angle on ODD counts

0x79a359  fld   [esp+0x34]        ; the function's float argument
0x79a361  fmul  [esi+0x10]        ; * the attachment scale
0x79a36c  fstp  [esi+0x20]        ; leaf.size factor
```

`0x78ea00` is `RandomRange`: it draws from the **Newran** RNG (`0x7a6fd0`) and
computes `lo + t*(hi - lo)` with `lo = [esp+4]`, `hi = [esp+8]` (i.e. the first
pushed value is the low bound).  Verified at `0x78ea0a`-`0x78ea1a`.

So:

```
angle = RandomRange(map.angle_lo, map.angle_hi)      # DEGREES
if (num_branch_levels & 1):
    angle = -angle                                   # mirror on odd counts
```

The mirror is keyed on the **branch-level count parity**, not the leaf index —
an earlier note said "odd-index mirroring", which was wrong.

### ⚠️ WHICH authored sections feed the angle pair is NOT yet established

What IS proven: the two `RandomRange` bounds are the **leaf-map** struct's
`+0x24` and `+0x28`, where the struct has stride `0x2c` (44) and
`+0x1c`/`+0x20` are `size.x`/`size.y`.

What is NOT proven: which authored section reaches those two slots.  Three
candidate mappings were tested and **all three were falsified**:

1. *Sections 3000/3002.* Falsified — those store to the **leaf-system**
   object (`ebp` = `this`, set `0x7a6062`, restored `0x7a6473`), a different
   struct that merely happens to also have `+0x24`/`+0x28`. Measured over the
   corpus they are always in `[0,1]` (3000: 0.75/0.20/0.25; 3002:
   0.80/0.50/1.00) — fractions, not degrees.
2. *Sections 72005/72006 (`hang`/`rotate`).* Falsified — the 72xxx ids do
   **not appear anywhere in Oblivion.exe** (searched `0x780000`-`0x7c0000`);
   this engine build never parses that group.
3. *A flat `0x20` shift of the 0x54 authored record* (which would explain
   `size.x`/`size.y` landing at `+0x1c`/`+0x20` exactly). Falsified — it
   predicts `angle_lo = size.z` (always 0.0 in all 1,599 authored maps) and
   `angle_hi = world_size.x`, whose values are 6.0 / 10.0 / 16.0 — not angles,
   and a field the engine *overwrites* anyway (`0x7a4839`, section 6t).

The 44-byte runtime struct is therefore a **distinct type**, not a shifted
copy of the 0x54 authored record, and the loop that fills it has not been
located. Its layout is pinned only by the reads:

| offset | evidence |
|---|---|
| `+0x00` | 4 bytes |
| `+0x04` | `std::string` texture name (`[+0x18]` cmp `0x10` = capacity, `0x78aa30`) |
| `+0x1c` | size.x (`0x79a25b`) |
| `+0x20` | size.y (`0x79a29b`) |
| `+0x24` | angle LO (`0x79a315`) |
| `+0x28` | angle HI (`0x79a301`) |

**Do not guess this.** `spt_generator.py`'s current orientation code
(`orientation_var * 90`, plus `uniform(-0.3, 0.3)` / `uniform(-0.25, 0.25)`
fudges) is left in place *and still flagged as invention* rather than being
replaced by another guess. Section 4002 `orientation_var` is the most likely
source — it defaults to **0.2**, matching the authored-record constructor's
`+0x10` default (`0x7a5925`, f32 = 0.2), and its corpus range is 0.0-0.65 —
but the derivation from that fraction to a degree pair is unproven.

### Note on 14005 / 14006

Sections 14005/14006 (`min_angle`/`max_angle`) DO hold real degrees in the
corpus (0, 45, 75, 90, +/-180), but they belong to **frond** maps, not leaf
maps, so they are not the pair read at `0x79a301`/`0x79a315`.

The leaf-system object and the leaf-map struct BOTH have fields at `+0x24` and
`+0x28`; conflating them is what made 3000/3002 look like angles.  They are
different objects:

* `ebp`/`ebx` at `0x79a301`/`0x79a315` are `map_vector + index*0x2c`
  (`0x79a2d5`, `0x79a307`) — the **leaf map**.
* `ebp` in the parser at `0x7a6483`/`0x7a64b6` is the leaf-system `this`
  (set `0x7a6062`, restored `0x7a6473`) — a **different struct**.

Measured over the export corpus, sections 3000/3002 are always in `[0, 1]` —
fractions, never angles.

## 8. 🛑 DRIVING THE REAL ENGINE — the ground-truth harness
<a id="8-driving-real-engine-ground"></a>

Decompiling tells us what the engine *does*; it cannot prove our Python
reproduces it. The only proof is the engine's own vertex buffers. Oblivion.exe
has SpeedTreeRT 4.x statically linked, so we can **call it directly**.

**The game is never launched.** The harness maps the executable's bytes into
its own 32-bit process as data, resolves the import table, and calls three
functions. The entry point is never executed: no window, no game loop, no
injection, no hooking. This is the same category of act as disassembling the
file, except the code runs instead of being decoded.

### Both retail builds are byte-identical here — measured

| build | file size | `.text` vsz | extra |
|---|---|---|---|
| Nehrim (GOG) `D:\Other Games\Nehrim At Fate's Edge\Oblivion.exe` | 7,549,440 | `0x627000` | — |
| Steam `...\steamapps\common\Oblivion\Oblivion.exe` | 7,898,624 | `0x626c39` | `.bind` (SecuROM) |

The files differ, but:

* **the whole `.text` section (`0x401000`, `0x626c39` bytes) is byte-identical**;
* the SpeedTree band `0x788000`-`0x7b8000` is identical
  (sha1 `a1ff0fb467bd...`);
* both have ImageBase `0x400000`, relocs stripped, and identical section VAs.

So **every address in this document is valid for both builds**, and the harness
accepts either. Steam's only difference is the appended `.bind` SecuROM
section, which we never touch.

### Loading constraints

`RELOCS_STRIPPED` is set and there is no relocation directory, so the image
**must** map at exactly `0x400000` — hence a 32-bit host process and
`VirtualAlloc(0x400000, ...)`.

### The three calls

| function | VA | convention |
|---|---|---|
| `CSpeedTreeRT::LoadTree` | `0x78df90` | `__thiscall(void* buf, unsigned len)` — takes the SPT **from memory**, no file I/O (proved at `0x78e39a`-`0x78e39f`) |
| `CSpeedTreeRT::Compute` | `0x78cca0` | `__thiscall` |
| `CSpeedTreeRT::GetGeometry` | `0x78c6f0` | `__thiscall(SGeometry* out, unsigned flags, ...)` |

`GetGeometry` flag bits (`0x78c720`-`0x78c76e`):

| bit | selects | getter |
|---|---|---|
| `1` | **branches** | `0x789fe0` |
| `2` | fronds | `0x78a390` |
| `4` | leaves | `0x788120` |
| `8` | billboards | `0x7887a0` (also requires `[this+0x6c]` and NOT bit `0x10`) |

### `SGeometry::SBranchGeometry` — recovered from `0x789fe0`

Every store the branch getter makes into the caller's out-struct (`edi`):

| offset | field | store site | source (tree object) |
|---|---|---|---|
| `+0x00` | LOD level | `0x78a240` | — |
| `+0x04` | num LOD levels (u16) | `0x78a24b` | `0x7886c0` |
| `+0x08` | strips | `0x78a258` | `0x788720` |
| `+0x0c` | strip lengths | `0x78a264` | `0x7945b0` |
| `+0x10` | **vertex count** (u16) | `0x78a013` | `(len/6) >> 1` |
| `+0x14` | **coords** | `0x78a045` | `+0x5c` |
| `+0x18` | normals | `0x78a0b3` | `+0x8c` |
| `+0x1c` | binormals | `0x78a0f0` | `+0x9c` |
| `+0x20` | tangents | `0x78a12d` | `+0xac` |
| `+0x24` | texcoords 0 | `0x78a076` | `+0x6c` |
| `+0x28` | texcoords 1 | `0x78a16a` | `+0xbc` |
| `+0x2c` | wind weights | `0x78a1a7` | `+0xec` |
| `+0x30` | wind matrices | `0x78a1e4` | `+0xfc` |
| `+0x34` | LOD fade | `0x78a224` | `+0x10c` |
| `+0x38` | float | `0x78a310` | — |

### ✅ WORKING: the engine generates geometry and we write it to a NIF

`spt_engine_dump.exe` now runs the full pipeline and
`tools/lod/spt_engine_to_nif.py` turns the result into a Skyrim NIF.
The harness now lives in `native/src/spt_engine/` and its built .exe is
committed to `native/dist/` (see that README) so end users need no C++
toolchain; `asset_convert/speedtree/spt_engine_geom.py` loads it from there.
The engine path is the **DEFAULT** for SpeedTree conversion; the Python
generator in `spt_generator.py` is the per-tree FALLBACK for anyone with no
Oblivion install or no harness. Force it with `--no-engine-branches`.

| tree | verts | strips | triangles | height |
|---|---|---|---|---|
| `treeenglishoakforest01su` | 10,019 | 33 | 2,362 | 118.4 |
| `treedogwoodsu` | 2,403 | 34 | 1,257 | 45.9 |
| `dbush03` | 558 | 18 | 845 | 23.7 |
| `treeginkgo` | 2,035 | 66 | 3,072 | 230.8 |

Output verified by reading it back: NIF `0x14020007`, user `12/83`, one
`NiTriShape`, normals + UVs present, sane bounding sphere.

#### What it took — state the image needs that startup normally provides

| global / site | value | symptom without it |
|---|---|---|
| `0xb4296c` / `0xb42970` | rebuild allocator list (mirrors static init `0xa10c00`) | read of `0x4` at `0x784935` |
| `0xba9d94` + `0xbaa2ac` | the SAME heap handle | `_get_heap_handle` -> `_invalid_parameter`; R6030 dialog; then a bogus handle into `HeapSize` |
| `0xbaabc0` | 1 (plain heap strategy) | free walks CRT bookkeeping that does not exist |
| `0xb310ac` / `0xb310b0` | cookie `-1`, fresh TLS index | `_decode_pointer` returns a different garbage address every run |
| `0x99cc19` | jump to `0x99cc92` | window-station probe calls a decoded garbage pointer |
| `0x99ccdb` | `xor eax,eax` | error reporter dispatches through an encoded handler |
| `0x98c8fb`/`0x98c910`/`0x98c9d3` | `ret` | `RtlAllocateHeap` faults reading `0x9` |
| `0xb32b80` / `0xb32c00` | `InitializeCriticalSection` | `EnterCriticalSection` writes `+0x14` of a zeroed struct |
| `0xb02020` | run static init `0xa16400` (vtable `0xa2f810`, ctor `0x401750`) | free dispatches through a NULL vtable |
| `_getptd` `0x98c072` | naked stub returning a `0x214` block | `call eax` with eax = 0 |

🛑 **The decisive fix was ALLOCATOR UNIFICATION.** The engine reaches FOUR
allocation entry points -- the game's `operator new`/`delete`
(`0x401f00`/`0x401f20`) **and** the CRT's `malloc`/`free`
(`0x9816f9`/`0x9817bc`) -- plus `_msize` (`0x981e9c`) and `_recalloc`
(`0x981f78`), which inspect a block's header. Redirecting only some of them
meant blocks created by one allocator were measured or released by another;
that, not any engine bug, was the `STATUS_HEAP_CORRUPTION` in `Compute`. All
six now route to the host CRT.

#### 🛑 SGeometry layout — CORRECTED by probing live buffers

The table derived from the getter's stores had fields transposed. Probing each
pointer (finite-ness and value range over 3,000 floats) settled it:

| offset | probe result | field |
|---|---|---|
| `+0x00` | `0` | LOD level |
| `+0x04` | `33` | **strip count** (from `0x7886c0`), NOT a LOD count |
| `+0x08` | pointer | strip LENGTHS |
| `+0x0c` | pointer | strip index arrays |
| `+0x10` | `0x2723` = 10019 | vertex count (u16) |
| `+0x14` | all NaN | allocated, unused |
| `+0x18` | `[-0.9998, 0.9986]` | normals |
| `+0x1c` | `[-0.9995, 0.9838]` | binormals |
| `+0x20` | `[-0.9999, 0.9999]` | tangents |
| `+0x24` | `[-24.4, 135.4]` | **positions** |
| `+0x28` | `[-2, 1706]` | texcoords 0 |
| `+0x2c` | constant `4.4e-22` | uninitialised |
| `+0x38` | `84.0` | tree height |

Both `+0x08` and `+0x0c` hold POINTERS at runtime, so strip lengths are
recovered by walking each index array while indices stay `< vertexCount`.

⚠️ Only vertices referenced by a strip are initialised; the tail is left NaN.
Any consumer must re-index to the referenced set (english oak: 1,456 of
10,019).

#### Call signatures (from `ret <n>` and the prologue)

| function | signature |
|---|---|
| `LoadTree` `0x78df90` | `bool (void* buf, unsigned len)` -- returns TRUE on real SPTs |
| `Compute` `0x78cca0` | `bool (const float* transform, unsigned seed, bool bInstance)` -- `ret 0xc`; the prologue `lea ebp,[esp-0x3c]` puts ebp **0x40 below entry esp**, so the seed at `[ebp+0x48]` is **arg #1**; matches the game's own call at `0x560882` |
| `GetGeometry` `0x78c6f0` | `(SGeometry* out, unsigned flags, short lodBranch, short, short)` -- `ret 0x14` |
| `SetTreeSize` `0x7871d0` | `(float size, float variance)` -- the game calls it before Compute |

Seed value **1 is special-cased and ignored** by the seeder (`0x7a2532`).

### What this changes

With the engine generating geometry, **trunk and branch vertices are taken
verbatim** — byte-identical by construction, not by approximation. Only the
leaves need substitution, because the engine emits camera-facing billboards
that Skyrim cannot render; we take the engine's leaf attachment points, sizes,
orientations and map indices and build crossed quads from them.

This retires every remaining open question in section 6's "still guessed" list
(leaf orientation angle source, quad corners, `seg_pack` ring warping, and the
Newran-vs-numpy RNG mismatch): none of it needs decompiling if the engine hands
us the vertices.

## 7. Decomp progress / next targets
<a id="7-decomp-progress-next-targets"></a>

Function-size census of the SpeedTree band (`0x788000`–`0x7b8000`), largest
first — candidates for the geometry builder:

| VA | size (bytes) | notes |
|---|---|---|
| `0x7b4900` | 10096 | largest; disassembles as data at offset 0 — needs a real entry point |
| `0x7ad270` | 6576 | unexamined |
| `0x79c540` | 5328 | valid prologue; only float consts are `4.29e9` unsigned-convert artifacts |
| `0x7925b0` | 5200 | valid prologue, `sub esp,0xe0` |
| `0x7b2aa0` | 3520 | unexamined |
| `0x7a1660` | 3488 | build-init, called first by `0x7a1cd0` |
| `0x79fd10` | 3392 | unexamined |
| `0x7977d0` | 2240 | unexamined |

**Known-good anchors**: `0x78cca0` = `CSpeedTreeRT::Compute`;
`0x7a1cd0` = `CFrondEngine::Compute` (NOT the branch builder);
`0x7a1660` = build init; `0x7a24f0` = seed setup; `0x7a45f0` = size;
`0x7a6cd0`/`0x7a6fd0`/`0x78ea00` = RNG core/shuffle/range;
`0x793640`± = child spawn (position + per-branch reseed);
`0x7921ef`, `0x7926ce` = child azimuth.

**RESOLVED (leaf/parse pass):**
- ✅ Leaf parse tables 4000-4007 and 3001-3010 — match spec exactly.
- ✅ `read_vec3` primitive `0x78eba0`; **4007 and 7016 are read then DISCARDED**.
- ✅ Leaf map selection: `RandomRange(0,100000) % n` — uniform index.
- ✅ Leaf size: `map.f_1c * scale * 0.5 * map.f_20`.
- ✅ Leaf orientation: `RandomRange(min,max)` per map, **negated on odd
  leaf indices** (alternating mirror) — we model neither.
- ✅ 4005 vs 4006 settled with data (202/465 stale) — 4005 is correct.
- ✅ LOD (9002-9009) and composite (10002-10007) tables; **10005/10006/10007
  undocumented but unused in all 636 SPTs**.
- ✅ 10002 payload = `<int32 N>` + N×8 floats — matches our parser.
- ✅ Collision object layout (sphere/capsule/box) — matches our parser.
- ✅ **PARSER BUG (§6i): section 7000 carries an int32 count; 2/636 SPTs fail.**
- ✅ Billboard-leaf block 7002-7016 decoded (undocumented in FORMAT).
- ✅ SpeedTree INI overrides all default to −1 ("use the SPT value").

**RESOLVED (earlier pass):**
- ✅ RNG algorithm, seeding, and API (§6b) — the basis for bit-exact output.
- ✅ Child position rule: `uniform(child_first, child_last)`, per-branch reseed.
- ✅ Child azimuth rule: `uniform(-180, +180)` — golden-angle spiral disproved.
- ✅ Variance draw: symmetric `uniform(-v, +v)`, applied inside spline eval.
- ✅ BezierSpline evaluation (§6e) — 500-entry LUT; ours measured equivalent
  (0/3577 curves deviate >1%).
- ✅ Level-struct layout, confirmed from BOTH parser and consumer sides.
- ✅ Child-count formula: `freq * length / D`, where **D starts at the
  RANDOMISED TREE SIZE** (2006 ± 2007) and lerps toward 1.0 each level,
  squared past level 1 (§6f). We model neither the normalization nor the
  blend, and we drop 2007 entirely.
- ✅ Bark UV rule (v_abs, u_abs, twist sign) — proven correct as implemented.
- ✅ Full parse-stage map (§6d) for parser cross-checking.

**What still needs to be recovered from the exe:**
1. The exact relationship between the engine's post-scale `length` and our
   pre-scale `stored_length` in `count = freq*length/D` (§6f). Both the blend
   factor (= x_rel) and D's seed (= randomised size) are now traced; what
   remains is reconciling the units before porting the formula.
2. How `gen_profile` (26019) weights the child count.
3. Leaf placement along the twig + how 6004 Placement Distance is applied
   (Findings B/C). **Demoted**: the B revision shows leaves already sit further
   out than their branches, so this is a fidelity item, not the silhouette fix.
4. Leaf collision pruning (3007/3008) — the real test (Finding H).
5. Gravity/flexibility integration — validate or replace `GRAVITY_RESPONSE`.
   Start at `0x7925b0`'s per-child `Evaluate` calls (`+0x54/+0x60/+0x6c`).
6. `seg_pack` (16002) and `seg_keep_*` (26005/26006) tessellation rules.
7. The order in which draws are consumed (required for bit-exactness even once
   every individual rule is known). Note the **per-branch reseed** at
   `0x7935e2` (`Seed(counter+3)`) makes this tractable: each branch's stream is
   independent, so draw order only has to be right *within* one branch.
8. Whether blossom weighting (3000/3002) is applied at selection time — the
   selection at `0x79a1fe` is a *plain* uniform modulo with no weighting, so
   either it happens elsewhere or blossom maps are duplicated in the array.
   **Our current blossom model is unverified against the engine.**
9. The identity of the leaf-map runtime fields `+0x1c`/`+0x20` (size terms) and
   `+0x24`/`+0x28` (orientation bounds) in terms of SPT section ids. The
   runtime struct is 0x2c bytes; the parsed record is 0x54.

**Priority — ACTIONABLE and engine-verified, in order:**

1. **The gravity bend model (§6q).** Fully closed. Replace the fitted tropism
   with the engine's formula:
   ```
   theta_deg = acos(dot(dir, pole)) * 57.295780
   torque    = 1 - |theta_deg - 90| / 90          # 1 horizontal, 0 at poles
   axis      = normalize(dir x (0,0,-1))
   angle_deg = -2*(angle_profile(t) - 0.5) * gravity * theta_deg * torque
   frame     = Rodrigues(axis, angle_deg) * frame # CUMULATIVE per ring
   ```
   Deletes `GRAVITY_RESPONSE = 4.5` (no engine counterpart). Fixes three
   separate errors at once: the torque curve (`sin` -> linear), the rotation
   axis (pole-directed -> `dir x down`), and the magnitude terms.
2. **Disturbance (§6p).** Two independent variance-carrying samples of curve
   6000 per ring, applied as a two-angle rotation via `0x78ef60` (both in
   degrees); the second is evaluated at the ring parameter `t`. Replaces our
   single deterministic sine-snake.
3. **`seg_pack` (16002) ring spacing (§6n).** `t = pow(i/(n-1), seg_pack)`.
   **92/401 levels (23%)**, values 0.0-4.0. Feeds the radius profile (taper),
   the flex profile (bend placement) AND the disturbance parameter — so it
   compounds with #1 and #2. Guard `seg_pack == 0`.
4. **The trunk crown-start remap (§6o).** `p = 1.0` above `child_first`, else
   `-child_first/(1-child_first)`. **91/139 trees**, median clear trunk 30%.
5. **`size_variance` (2007) (§6f).** `RandomRange(Size-var, Size+var)`.
   **132/139 trees**, median 10. Scale only; cancels out of the child count.
6. **The leaf-window clamp** — `np.clip(child_first, 0.02, 0.95)` overrides an
   authored 0.00 on 4/7 sampled trees (Finding D).
7. **Leaf orientation (§6g)** — `RandomRange(min,max)` per map, with
   **alternating leaves mirrored** (`orientation *= -1` on odd indices).

**Confirmed CORRECT — do not "fix":**

- `count = child_freq * stored_length` (§6f) — matches the engine exactly.
- Cumulative per-ring frame rotation (§6q) — the engine does the same.
- Arc-length ring spacing — measured 0.00% drift (§6l).
- Bark UV rule, `WORLD_SCALE=10`, spline eval (0/3577 curves >1% off),
  4005-over-4006 for leaf size, collision layout, 10002 quad payload.

**Explicitly NOT actionable:**

- **The stem caps (Finding A).** Our counts already match the engine; the caps
  are an arbitrary ceiling on a correct number. Raising them is a perf/budget
  decision, NOT an algorithm port.
- **The leaf clump/cull passes.** Measurement shows leaves already sit further
  out than their branches on every tree (Finding B revision); the branches are
  what fail to reach outward.

**Remaining unknowns (none block the list above):**

- Whether blossom weighting (3000/3002) is applied at selection time — the
  engine's leaf pick is a plain uniform modulo (§6g).
- Leaf collision pruning (3007/3008), 124/139 trees (Finding H).
- `seg_keep_length` (26005) / `seg_keep_cross` (26006).
- The `P[2]` component of the tube-ring texcoord block (§6o).

**Tooling** (scratchpad, not committed): PE section mapper + VA/file
converters, capstone linear disassembler, jump-table decoder, rel32 xref
scanner, function-start finder (int3-padding scan), float-constant scanner.
Linear disassembly desyncs badly across the band — always start from a known
prologue.

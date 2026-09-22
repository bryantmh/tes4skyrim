# asset_convert/nif/nif_converter.py — NIF conversion

**Code:** `asset_convert/nif/nif_converter.py`, `asset_convert/nif/sse_nif.py`, `asset_convert/nif/pyffi_monkey_patch.py`, `asset_convert/sources/skyrim_assets.py`

## Contents

- [Asset Conversion Notes](#asset-conversion-notes)
- [Asset Pipeline](#asset-pipeline)
- [DOOR conversion notes](#door-conversion-notes)
- [NIF mesh rotation](#nif-mesh-rotation)
- [NIF particle system conversion](#nif-particle-system-conversion)
- [NIF FlameNode → grafted converted flame (rewritten 2026-07-05, replaces the MPS/AddonNode substitution)](#nif-flamenode-grafted-converted-flame)
- [NIF NiGeomMorpherController (dead in Skyrim, fixed 2026-07-05)](#nif-nigeommorphercontroller)
- [NIF embedded Ni*Light blocks (dead in Skyrim, fixed 2026-07-18)](#nif-embedded-nilight-blocks)
- [Early-Oblivion NIF versions (10.0.1.0 / 10.0.1.2 / 10.1.0.106) — the [RD] read failures (SOLVED 2026-07-15)](#early-oblivion-nif-versions-rd)
- [Orphaned blocks in data.roots — the [EXC] '<block>' object has no attribute 'controller' failures (SOLVED 2026-07-20)](#orphaned-blocks-dataroots-exc-block)
- [NIF NiDefaultAVObjectPalette fixup](#nif-nidefaultavobjectpalette-fixup)
- [Skinned shape = red triangle: NiSkinPartition still in STRIP format (SOLVED 2026-08-01)](#skinned-shape-red-triangle-niskinpartition)
- [Dangling back-references to old_root after NiNode→BSFadeNode (skin case SOLVED 2026-08-01)](#dangling-back-references-oldroot-after)
- [NIF furniture marker conversion (rewritten 2026-07 — fixed backwards/floating NPCs)](#nif-furniture-marker-conversion)
- [NIF analyzer tools](#nif-analyzer-tools)
- [SpeedTree (.spt) conversion](#speedtree-conversion)
- [Book inventory art (INAM reading rigs) — books were invisible with no text when opened (SOLVED 2026-07-18)](#book-inventory-art-books-were)
- [SSE-format NIF read support + BSA auto-extraction (2026-07-19)](#sse-format-nif-read-support)
- [Two vanilla divergences investigated and DELIBERATELY NOT FIXED (2026-08-22)](#two-vanilla-divergences-investigated-deliberately)
- [bhkPackedNiTriStripsShape reaching the output — the 2 GB memcpy / heap-wide 0x100000001 (SOLVED 2026-08-22, confirmed in-game)](#bhkpackednitristripsshape-reaching-output-2-gb)
- [NiBlendInterpolator: the manager-controlled flag and the Vilverin CTD](#blend-interp-flags)
- [NiFlipController → frame-strip atlas](#niflipcontroller-atlas)
- [Morrowind-era legacy block types](#legacy-block-types)
- [Morrowind legacy particle emitters](#morrowind-particle-systems)

Linked from [CLAUDE.md](../../CLAUDE.md). Deep narrative notes from debugging the
Oblivion→Skyrim mesh/collision/particle/animation pipeline. For creature-specific
(behavior graphs, HKX, ragdoll) notes see [creature_conversion.md](asset_convert_creature.md).
For record-level mapping tables see [record_mapping.md](../reference/record_mapping.md).

## Asset Conversion Notes
<a id="asset-conversion-notes"></a>

- **NIF meshes**: Oblivion uses NIF version 20.0.0.4/20.0.0.5 (NetImmerse). Skyrim uses 20.2.0.7 (Gamebryo/BSTriShape). The external NIFConverter subfolder has reference tools.
- **NIF full conversion** (`mesh_convert` package): Performs complete Oblivion→Skyrim NIF conversion:
  1. NiTriStrips → NiTriShape (SE can't render strips)
  2. NiTexturingProperty + NiMaterialProperty → BSLightingShaderProperty + BSShaderTextureSet (Skyrim shader system)
  3. Texture path rewriting (prepend `tes4\` to keep separate from Skyrim assets)
  4. Bone name remapping (Oblivion Bip01 → Skyrim NPC skeleton)
  5. NiNode root → BSFadeNode root (Skyrim's standard root type)
  6. Geometry data finalization (`unknown_int_2 = 8`)
  7. NIF version upgrade (20.0.0.4 → 20.2.0.7, BSStream 83)
  8. bhk block format conversion (Oblivion UV2=11 → Skyrim UV2=83):
     - bhkRigidBody/T: +14 bytes (UnknownInt2 field swap at [44:52], TimeFactor, GravityFactor, RollingFrictionMult, UnknownBytes2, BodyFlags u32→u16)
     - bhkMoppBvTreeShape: +1 byte (BuildType insertion at offset 40)
  9. Orphan block removal (NiMaterialProperty, NiTexturingProperty, etc.)
  10. Oblivion-only block types force-removed (NiVertexColorProperty, NiSpecularProperty, etc.)
  Run: `python -m asset_convert.nif.nif_converter <src_dir> <dst_dir>` (worker pool is automatic: cpu_count-3; there is NO --workers flag).
- **NIF conversion stats**: 8032 source NIFs from Oblivion BSAs. 7380 v20 files converted (91.9%). 650 v10/v4 files copied as-is. 2 remaining parse errors (magic effect particle NIFs).
- **NIF bhk conversion details** (Session 19+):
  - bhkRigidBody/T: Oblivion=236+n*4, Skyrim=250+n*4. Key: two `Unknown Int 2` fields with different vercond (UV2>34 vs UV2≤34). Bytes [44:52] need rearrangement, not just passthrough. Translation/Mass/Friction are at fixed offsets (52, 180, 192 in Oblivion, 52, 180, 200 in Skyrim)
  - Crash signature: SkyrimSE.exe+0A882E6 reading from 0xFFFF* addresses = corrupted bhkRigidBody pointers from misaligned fields
  - bhkNiTriStripsShape: Collision NiTriStripsData must NOT be renamed to NiTriShapeData (template type mismatch). Writer must write strips format, not triangulated.
  - Constraint descriptors (RagdollDescriptor, LimitedHingeDescriptor, HingeDescriptor, MalleableDescriptor): UV2≤16 vs UV2>16 field REORDERING is handled by PyFFI's ver1/ver2-guarded duplicate attrs (same attr names in both layouts, so values carry over automatically on read-Oblivion/write-Skyrim).
  - **Constraint conversion (rewritten 2026-07-04, `collision.py::scale_constraint_pivots`)**: the old code only fixed bhkLimitedHingeConstraint; every other descriptor shipped UNSCALED pivots (10× too far, e.g. UpperScales01 ragdoll pivot 3.57 vs vanilla-range 0.36) and zeroed Skyrim-only basis fields. Now for ALL descriptor types: pivot_a/pivot_b ×0.1 (stiff-spring `length` and prismatic min/max_distance too — they're lengths); RagdollDescriptor `motor_a/motor_b` = twist × plane (they are the 3rd column of the constraint's orthonormal basis, NOT motor params — zero = singular basis; handedness verified on vanilla desecratedimperial.nif); HingeDescriptor Skyrim-only `axle_a` = perp_a1 × perp_a2 and `perp_2_axle_in_b_1/2` = Gram-Schmidt complement of axle_b (plain hinge has no limits so any orthonormal complement is valid); inertia ×0.1 rescale deduped per body (the scales crossbar sits in 3 constraints — was being triple-scaled). Vanilla Skyrim constraint census (17,216 meshes): LimitedHinge 158, Ragdoll 59, Hinge 3, StiffSpring 2, **Malleable 0, Prismatic 0** → bhkMalleableConstraint is demoted to a plain constraint of its inner SubConstraint type (`_demote_malleable_constraints`; strength/tau/damping dropped); bhkPrismaticConstraint (Oblivion arrows only) is kept best-effort with a note that vanilla never ships it. Oblivion source census: LimitedHinge 278, Ragdoll 60, Malleable 21, Prismatic 10, Hinge 4, StiffSpring 3.
  - **KNOWN REMAINING bhkRigidBodyT+CMS violations (2026-07-04)**: 5 converted ANIMATED meshes still ship the forbidden pair (dungeons\ayleidruins\interior\traps\artrapspikepit01, dungeons\caves\cdoor03, dungeons\sewers\sewertunneldoor01, oblivion\clutter\traps\citadelhall3wayspiketrapbroken, oblivion\gate\obliviongate_simple) — keyframed child-node collision can't be demoted by the static bake pass; needs its own fix. ~100 speedtrees/ shrub+tree NIFs also contain the pair but are pre-made Skyblivion assets copied verbatim (not produced by our converter). Find them with `python tools/nif/nif_block_scan.py <dir> --has bhkRigidBodyT --has bhkCompressedMeshShape`.
  - `asset_convert/collision/mopp.py::walk_mopp()` is a full MOPP VM symbolic walker (PyFFI's parse_mopp opcode table + Skyrim-era opcodes: 0x52 TERM24, 0x29-0x2B DOUBLE_CUT24, 0x70 CHUNK_JUMP32), validated clean against 400 vanilla meshes. CLI: `python tools/validate/mopp_validator.py <nif_or_dir> [--verbose|--summary|--histogram|--workers N]` (validates walk cleanliness AND exact terminal-key-set == shape-key decode). Vanilla opcode set observed: 0x01-0x06, 0x09-0x0B, 0x10-0x1C, 0x20-0x28 (0x29-0x2B rare), 0x30-0x53 — never 0x07/0x08/0x70; emit only these.
  - **MOPP_RL.exe is GONE (2026-07-03): all mesh collision is built by `asset_convert/collision/cms_builder.py`**. History: MOPP_RL's chunked bytecode (0x70 chunk jumps, PC engine mis-executes) was first dechunked (`mopp.py::dechunk_mopp`), then its bytecode was replaced wholesale with Havok-bridge output — and the intermittent CTD STILL persisted (crash `SkyrimSE.exe+07D4C4B` fn 43870, runaway `hkpAllCdPointTempCollector` scan → EXCEPTION_STACK_OVERFLOW; Collision Sentinel: `CULPRIT ... key=0xFFFFFFFF` on the same meshes). Root cause was never the bytecode (see bhkRigidBodyT bullet below). MOPP_RL, its template.nif, and the dechunk fallback are all removed from the pipeline; `dechunk_mopp` remains in mopp.py for forensics only.
  - **CMS collision is built in pure Python + real Havok (2026-07-03)**: `cms_builder.py::build_cms_collision(tris_hu, sk_material_crc, NifFormat)` builds the whole bhkMoppBvTreeShape→bhkCompressedMeshShape→bhkCompressedMeshShapeData chain from a triangle soup: bpi=17/bpw=18, error=0.001, one identity bhkCMSDTransform, chunk = spatial bucket (split until extent <60 hu, ≤2000 tris), chunk translation = bucket min corner, u16 offsets = (v−min)×1000, triples-only indices (num_strips=0 — engine key decode identical to strips, `key=(ci+1)<<18|offset`), tris larger than the u16 span → big tris. MOPP bytecode + TWO_SIDED welding come from `external/mopp_bridge/dovah_hkp_mesh_mopp_bridge.exe` (Havok's real `hkpMoppUtility::buildCode`, chunk subdivision off, terminal keys self-validated by Havok's find-all-keys VM) — bridge input is `decode_cms()` of the freshly built block so MOPP/welding are computed over the exact quantized geometry the engine will decode. Welding u16 goes at the tri's first-index slot in chunk `indices_2` (= key offset); big-tri welding in `unknown_short_1`. Output re-verified in Python (walk clean + keys == `predict_keys`). Constants mirrored from vanilla: CMS radius=0.005, unknown_float_1=0.005, scale vec (1,1,1,0), data unknown_int_3=1, chunk unknown_short_1=0xFFFF, material layer=1. Wired in `collision.py::_rebuild_mesh_collision` (handles strips/packed/stale-Oblivion-MOPP sources; strips verts are GAME units → ÷70 to Skyrim hu; packed verts ×0.1). Fallback when the bridge fails: bare `_packed_from_tris` (no MOPP; packed data verts are stored ×10 hu = 1/7 game scale). NaN-vert tris are filtered before building.
  - **The MOPP bridge exe** came from inside `tools/DovahNifWorkbench_v6_47.exe` (PyInstaller onefile; payload `backend_exact_mopp\dovah_hkp_mesh_mopp_bridge.exe` + full C++ source `native_hkp_mesh_mopp_bridge/`, re-extractable by parsing the CArchive TOC at the `MEI\014\013\012\013\016` cookie). CLI: `--input in.json [--output report.json] [--no-stdout]`; input JSON `{"vertices":[x,y,z,...], "triangles":[a,b,c,...], "shape_keys":[k,...]}` (keys optional, must be unique); report has `mopp_origin`, `mopp_scale`, `mopp_data_hex`, `welding_info` (TWO_SIDED, per source tri), `mopp_keys_match_shape_keys`. GUI batch mode is NOT needed — the exe is called per-shape by cms_builder.py (`run_mopp_bridge`).
  - **CMS shape-key encoding (validated 200/200 vanilla meshes: walked MOPP key set == predicted set — `asset_convert/collision/cms.py::decode_cms/predict_keys`)**: chunk tri key = `(chunk_idx+1) << bitsPerWIndex | winding << bitsPerIndex | first_index_offset` where the offset is the tri's first index position in the chunk's indices array; strips yield sliding-window tris (winding = window ordinal parity within the strip), then remaining indices are independent triples (winding 0, stride 3); big tris = part 0, key = big-tri index. Chunk vertex = chunk.translation + transform.translation + u16/1000 (rotate by transform quat if non-identity). PyFFI 2.2.3 field quirks: chunk welding array = `indices_2`, big-tri welding = `unknown_short_1`, big-tri fields `triangle_1/2/3` index into `big_verts`.
  - **PyFFI parse_mopp 0x0B (TERM_REOFFSET32) is WRONG** ("unsure about first two arguments" — reads only operand bytes 3-4): the operand is a full 32-bit big-endian value that SETS the terminal offset, and Skyrim CMS keys carry the chunk part in the HIGH bytes (0x00040000 = chunk 0). With the 2-byte read, every terminal after a 0x0B loses its chunk part — this made valid keys look like out-of-range "big tri" keys (a red herring chased for hours; vanilla showed the identical false pattern, which is what exposed the walker bug). Fixed in `walk_mopp`. Welding values legitimately span the full u16 range incl. ≥0x8000 and 0xffff — NOT a corruption signal (vanilla does the same).
  - `bhkCompressedMeshShape.target` must point to the BSFadeNode root (identity transform). Static collision MUST be on the root BSFadeNode — having bhkCollisionObject on a child NiNode causes STACK_OVERFLOW in Skyrim's `hkpCollisionDispatcher`.
  - **bhkRigidBodyT + CMS/MOPP = intermittent CTD — THE AnvilCastleGreatHall root cause (2026-07-03)**: vanilla Skyrim NEVER pairs a transformed rigid body with CompressedMesh collision — **0 of 6,341 vanilla CMS meshes contain bhkRigidBodyT** (checked by binary grep — block type names are plaintext in NIF headers). Shipping one exercises an engine path Bethesda never tested: queries intermittently resolve to HK_INVALID_SHAPE_KEY (Collision Sentinel `key=0xFFFFFFFF`) → runaway `hkpAllCdPointTempCollector` scan (Sentinel EVENT `b=129` vs the 128-slot stack collector) → EXCEPTION_STACK_OVERFLOW at `SkyrimSE.exe+07D4C4B`. Every Sentinel CULPRIT was a rotated-root mesh whose wrap pass produced bhkRigidBodyT+CMS ("diagonal/curved architecture" pattern). This explains all earlier observations: identity-body configs never crashed (only had rotated collision); transformed-body configs (bodyT OR collision on rotated child node) crashed ~50%. Replacing the MOPP bytecode alone did NOT fix it — the bytecode was never the problem.
  - **Root rotation wrap + collision (final design 2026-07-03)**: when the wrap pass zeroes the root transform L=(R,T), `bake_node_transform_into_body()` still composes bodyT' = L ∘ bodyT (in Oblivion hu; PyFFI `m_ij` names are the TRANSPOSE of the engine's column-vector matrix; rotation is QuaternionXYZW; ×0.1 rescale happens in `_convert_collision`). But for MESH collision the transform never reaches the file: `_bake_body_transform_into_tris()` applies the final bodyT to the triangle soup and DEMOTES the body back to a plain identity bhkRigidBody (class swap) before `build_cms_collision` runs — the output matches vanilla exactly (identity plain body, geometry in the world frame). Collision stays on the root BSFadeNode; CMS target = root. Regression test: `TestCollisionTargetPointsToRoot::test_static_collision_stays_on_root_when_wrapped` (asserts plain identity body + decoded CMS centroids match the source collision in the L∘bodyT frame within quantization — catches conjugate/transpose convention errors). Primitive shapes (convex/box/capsule, incl. constrained sign bodies) legitimately keep bhkRigidBodyT — vanilla does too.
  - **NaN geometry = silent cell-load CTD, NO crash log (2026-07-04, the AnvilMagesGuild/AnvilCastlePrivateQuarters root cause)**: some Oblivion source meshes ship non-finite floats in RENDER geometry (anvildooruc02.nif: 9 NaN UVs; middlecandlestickfloor03fake.nif: 2 NaN UVs — exactly one such mesh in each crashing cell, found by intersecting `tools/esm/cell_meshes.py` output with a `tools/nif/collision_sanity.py --geometry` sweep). Oblivion's renderer tolerated them; SSE dies at cell load WITHOUT writing a crash log (fail-fast, not a loggable exception) — collision was never involved. Fixed by `sanitize_geometry_data()` in nif_converter.py (runs right after `_resolve_palette_strings`, BEFORE tangent computation/skin retarget so NaNs can't propagate): NaN UVs→0, NaN verts→finite centroid (+ bound-sphere recompute), NaN normals/tangents→+Z, NaN vertex colors→1. NOTE: the PyFFI warning summary from a full conversion run showed `nan_in_vertices: 155` — other meshes in the tree carry NaN too and previously shipped unsanitized; a full mesh reconversion (pipeline now sanitizes) or a `collision_sanity.py --geometry` sweep of output finds/fixes the rest.
  - NiParticleSystem: NiGeometry body needs format conversion (MaterialData→NumMaterials, Properties removed for UV2>34, FarBegin/End added for UV2≥83). IMPLEMENTED — `_convert_particle_system()` creates fresh NiPSysData with `bs_max_vertices = max(old_num_vertices, 75)`, keeps all modifiers, sets `base_scale=1.0` on NiPSysGrowFadeModifier.
- **PyFFI 2.2.3 version-condition bugs**: PyFFI's nif.xml has WRONG version conditions for some fields. Must monkey-patch at import time:
  - `NiPSysGrowFadeModifier.base_scale`: PyFFI has `userver="11"` (exact match on user_version=11). Correct condition per newer nif.xml: `User Version 2 >= 34`. Since we write `user_version=12` (Skyrim), PyFFI silently skips the field. Fix: set `_attrs[base_scale].userver = None` in monkey-patch.
  - Without the fix, `base_scale` defaults to 0.0 → particles invisible (scale = 0 × grow = 0).
  - The `bhkMoppBvTreeShape.build_type` field's vercond (`user_version >= 12`) is correct and does NOT need patching.
- **NIF reference docs**: NifSkope nif.xml at `external/NifSkope Built/nif.xml`, NifSkope HTML docs at `external/NifSkope Built/doc/`, NifSkope source at `external/nifskope-2.0.dev7/src/`
- **NIF BSStream versions**: 83 = Skyrim LE, 100 = Skyrim SE optimized. SE can load BSStream 83 files with NiTriShape geometry.
- **DDS textures**: Oblivion uses DXT1/DXT3/DXT5. Skyrim SE uses BC7/BC5/BC1 compression. May need re-export.
- **BSA archives**: Oblivion BSA format differs from Skyrim BSA. Need re-packing.
- **File paths**: The export prepends `tes4\` to all asset paths to avoid conflicts with Skyrim's own assets.

## Asset Pipeline
<a id="asset-pipeline"></a>

The `-ExtractAssets` flag triggers BSA extraction and mesh conversion:

1. **BSA Extraction** — Uses `bsab.exe` (from external/fnv-to-fo4/bin/bsab/) to extract meshes and textures from Oblivion BSA archives
2. **Mesh Conversion** — Uses PyFFI-based NIFConverter (from external/NIFConverter/) to convert Oblivion NIF 20.0.0.4/5 → Skyrim NIF 20.2.0.7
3. **Texture Copy** — DXT textures from Oblivion are compatible with Skyrim; copied as-is under `tes4\` namespace
   - **Path rewriting (`rewrite_tex_path`) must normalize separators FIRST** (fixed 2026-07-27). Oblivion NIFs mix `/` and `\`, sometimes in one file. Testing only for a backslash `'textures\'` prefix let `textures/lowres/foo.dds` fall through and come out as `Textures\tes4\textures/lowres/foo.dds` — a path resolving to nothing, so the mesh renders untextured and the LOD tiles built from it reference 100 nonexistent textures. 96 Morrowind_ob source NIFs hit this; **zero Oblivion.esm ones**, which is why it stayed hidden.
   - `textures\lowres\` is an Oblivion **_far.nif authoring convention** for low-res LOD copies (pyffi ships a `modify_texturepathlowres` spell writing exactly this prefix, documented "used mainly for making _far.nifs"). We ship no lowres tree — converted textures live at the normal path — so the segment is **dropped**, resolving the reference to the real texture. The rewrite is idempotent on already-correct `Textures\tes4\…` paths.
4. **BSA Repacking** — Not yet automated. Use BSArch.exe or Skyrim CK Archive tool.

### Prerequisites for mesh conversion
- Python 3.x
- PyFFI (`pip install PyFFI`)
- `external/mopp_bridge/dovah_hkp_mesh_mopp_bridge.exe` (checked in — Havok MOPP/welding compiler)

### BSA naming conventions (Oblivion)
- `Oblivion - Meshes.bsa`, `Oblivion - Textures - Compressed.bsa`
- `DLCShiveringIsles - Meshes.bsa`, `DLCShiveringIsles - Textures.bsa`
- `Knights.bsa` (single BSA for smaller DLCs)

## DOOR conversion notes
<a id="door-conversion-notes"></a>
- TES4 FNAM bit 0 = "Oblivion gate" — **clear this bit** when writing TES5 FNAM (no TES5 equivalent, may corrupt flags)
- TES4 bits 1-3 (Automatic, Hidden, Minimal Use) map directly to TES5 bits 1-3
- XTEL Door FormID is remapped via get_formid() — both sides of a teleport pair must be in the output
- TES4 XTEL = 28 bytes (no flags field); TES5 XTEL = 32 bytes — must append 4 bytes of flags (0x00000000 = default) when writing TES5 XTEL
- Doors without XTEL are correctly treated as open/close doors

## NIF mesh rotation
<a id="nif-mesh-rotation"></a>
- Some Oblivion architecture/static NIFs have a non-identity rotation on their root NiNode (from 3ds Max exporter)
- Skyrim's BSFadeNode ignores the root node's local rotation matrix for static placement (Oblivion's NiNode applied it); this means statics appear rotated in Skyrim
- **Fix (in nif_converter.py Pass 6c)**: For non-skinned NIFs, bake the root rotation into each direct child's local transform (R_child = R_root × R_child, T_child = R_root × T_child), then zero the root rotation. Skinned meshes excluded (need skeleton bone alignment).
- Simple zero-only reset (prior approach) does NOT fix the issue — the geometry is still in the rotated coordinate space; baking into children is required.

## NIF particle system conversion
<a id="nif-particle-system-conversion"></a>
- **🛑 `NiFlipController` INSIDE A SEQUENCE was the actual `oblivionarchgate01` red triangle (fixed 2026-08-09, THIRD red-triangle cause)**: the property-side flip handler only sees a `NiFlipController` hanging off a geometry's `NiTexturingProperty`; one referenced from a **`NiControllerSequence` controlled block** never reaches it, so the block stayed in the file and kept **121 `NiSourceTexture` frames** alive with it. A sequence stores its controller type as a **string the engine instantiates BY NAME**, so an Oblivion-only type there rejects the whole NIF. Vanilla census (~8,300 meshes): `NiFlipController` and `NiSourceTexture` appear **ZERO** times. Affected exactly 9 of 11,693 output meshes — all four Oblivion gates, three magiceffects, `creatures/endgame/battle`, `health_bar01`. **DROP the entry, don't retarget it**: the flip-book is already fully converted geometry-side into a `*_flip.dds` frame-strip atlas driven by a `BSEffectShaderPropertyFloatController` stepping U Offset (verified: all 5 flip nodes keep their atlas + 16/75/30 stepped keys), so the sequence entry is a pure duplicate. Also added `_VANILLA_SEQ_CONTROLLERS`, a whitelist backstop dropping **any** controller type vanilla never puts in a sequence — every other handler there is type-by-type, so the next Oblivion-only controller would have shipped broken the same way. **Diagnosis method that actually worked**: pyffi parses these files happily (it is far more tolerant than the engine) — use the header/block-size verifier (`_verify_block_structure` in `tests/test_asset_convert.py`) or `tools/nif/nif_block_scan.py --has NiSourceTexture`, which showed 121 structural errors where pyffi showed none. **Caveat when sweeping with that verifier: the 112-byte `BSLightingShaderProperty` variant is VANILLA-LEGAL** (1,876 occurrences in `references/Skyrim Meshes`) and is a gap in the verifier, not a defect — exclude it or you get 340 false positives.
- **A lighting shader over UV-LESS geometry (fixed 2026-08-09, same session — real defect, but NOT the red-triangle cause)**: this was fixed first on the theory that it caused the red triangle; it did not (the user re-tested and the triangle remained — the mesh was failing to LOAD, which is the `NiFlipController` bug above, and a malformed shader would garble a shape rather than replace it with the placeholder). Keep the fix — the state is still vanilla-impossible — but do not credit it with the red triangle. 12 shapes shipped a `BSLightingShaderProperty` over geometry with `num_uv_sets == 0` and no tangents. That shader *always* samples a diffuse texcoord and reads the tangent basis for its normal map, so with neither stream present it reads past the vertex buffer and renders as an untextured red shard. These are Oblivion **helper volumes** (particle emitter sources, spawn/effect proxies) that Oblivion hides with **bit 0 of the node flags**, but `process_geometry` did `ts.flags = NIF_FLAGS`, clobbering the authored hidden bit and un-hiding every one of them. Two earlier narrow workarounds existed for this same clobber — the `EditorMarker` name-prefix strip and the `NiPSysMeshEmitter.emitter_meshes` hiding pass — and **neither reaches a helper the emitter does not link**, which is why these survived. Three fixes: (1) preserve the authored hidden bit in `process_geometry`; (2) `_apply_rest_visibility` only read *keyframed* interpolators and so skipped every **data-less `NiBoolInterpolator`**, whose constant `bool_value` IS the rest state — this mesh drives all 30+ vis-controlled nodes that way, so the meteors/tendrils rendered from cell load; (3) a final safety net that strips the shader and hides any shape still left lit-but-UV-less. **Vanilla census (373 shapes, `references/Skyrim Meshes`): ZERO pair a lighting shader with 0 UV sets** — the 54 UV-less vanilla shapes are either `BSEffectShaderProperty` (45; that shader needs no tangents) or carry no shader at all (9, 8 of them hidden). Detect with a converted-output scan for `num_uv_sets == 0` + `BSLightingShaderProperty`; 0 after the fix. The same fix also cleared `obliviongate_simple` (10), `obliviongate_forming` (9) and `oblivionwargateani02` (6) untargeted, so treat any lit UV-less shape as this bug rather than patching the mesh. **Lesson for the next session: "renders as a red triangle" means the engine REJECTED THE FILE AT LOAD — go straight to block-level structural validation, not to shader/geometry inspection.** A shader or vertex-stream defect garbles a shape; only a load failure substitutes the placeholder.
- **`NiPSysMeshEmitter.emitter_meshes` is a SECOND link to geometry and must be remapped when NiTriStrips→NiTriShape (fixed 2026-07-27, the RED-TRIANGLE bug)**: `se11sheopooffx.nif`, `se01waitingroomwalls.nif` and `palacefont01.nif` rendered as Skyrim's red missing-mesh placeholder — the engine failed the whole NIF at load. `walk_node` converts strips by writing the replacement back into the **parent's `children` array**, but a mesh emitter references its source geometry through `emitter_meshes`, which is not a children array and was never rewritten. The orphaned `NiTriStrips` stayed reachable through that link, so PyFFI happily re-serialized it, leaving raw Oblivion strips in a Skyrim file. **Skyrim has no NiTriStrips renderer** (vanilla census: **107/107** emitter meshes across all 256 `NiPSysMeshEmitter` blocks are `NiTriShape`; the 21 stray NiTriStrips in the whole vanilla tree are all `bhkNiTriStripsShape` collision), so the load fails outright. Fix: extend the existing `_block_map` fixup (which already repaired `NiDefaultAVObjectPalette`) to walk every `NiPSysMeshEmitter` and remap each `emitter_meshes[i]`. Detect it with a converted-output scan for surviving `NiTriStrips`, or for emitter meshes whose class is not NiTriShape — both are 0 after the fix. **The particle CONVERSION itself was never at fault here** — fire and other psys meshes were always fine; this is purely a dangling-reference bug in the strips rewrite, so look for the second link rather than re-auditing the modifier chain.
- **BSXFlags bit 0 (Animated) is REQUIRED or particles NEVER TICK — THE final fire-invisibility root cause (fixed 2026-07-05)**: without BSX bit 0x01 on the root, the engine never updates the mesh's time controllers, so emitters never fire — the file is perfectly valid but the fire is invisible. Census: **399/400 vanilla particle meshes set bit 0** (sole exception: a trailer camera rig); collisionless particle meshes use plain BSX=1 (also 0x201/0x221 with external-emit/editor bits). Two converter gaps caused this: `_add_bsx_flags` (a) early-returned when the root had NO collision (fireopensmall loses its collision → no BSXFlags at all), and (b) detected "animated" only via NiControllerManager on the ROOT — particle controllers live on the NiParticleSystem, so even collision-bearing fire got 0x82 static. Fix: `_tree_is_animated()` (any NiParticleSystem, or any block with a controller, anywhere in the tree) → collisionless+animated gets BSX=1; collision values get bit 0 OR'd in (0x82→0x83, 0xC2→0xC3 — both appear in vanilla census); `_convert_flame_nodes` now CREATES a BSXFlags(=0x10) when the root has none (fake candles without collision previously lost the AddonNode bit). All the fixes below were necessary too, but this was the last blocker: the earlier gravity_object fix repaired the SIM, this makes the engine RUN it. (fixed 2026-07-05, `_skyrimize_modifiers`)**: the SSE particle engine does NOT drive Oblivion-era `NiPSysGrowFadeModifier` (scale) or `NiPSysColorModifier` (color) even though they're valid block types — particles spawn at scale 0 / alpha 0 = invisible. Convert them to the BS* equivalents the engine actually processes, matching a working vanilla fire (`references\Skyrim Meshes\...\slighthousefire.nif` Fireball): **NiPSysGrowFadeModifier → BSPSysScaleModifier** (60-entry scale ramp, grow-in/hold/fade-out, peak ~1.0 taper to 0.1); **NiPSysColorModifier → BSPSysSimpleColorModifier** (fade_in/out % + 3 Color4s). **Inject BSPSysLODModifier** — it's in 498/498 vanilla particle meshes (LOD begin/end/emit-scale/size = 0.033/0.233/0.2/1.0); without it the system culls at all distances. Keep emitter/spawn/rotation/gravity/position/bound-update/age-death as-is. Set NiPSysModifier `order` to vanilla bands: AgeDeath=0, LOD=1, Emitter/Spawn=1000, SimpleColor/Rotation/SubTex/Scale=3000, Gravity=4000, Position=6000, BoundUpdate=7000 (engine processes in ascending order). Set Name/Target(=the NiParticleSystem)/Active on every modifier.
- **Particle shader** (BSEffectShaderProperty): flags1 = `z_buffer_test` + `soft_effect` (see the FX brightness/soft-fade entry below — the earlier "NOT soft_effect" note was drawn from fire meshes only and did not hold for the blended FX population), flags2 = `vertex_colors` ONLY, `emissive_multiple`=**1.0** with emissive_color taken from the source `NiMaterialProperty` (was a blanket 1.5, which over-brightened every non-fire system), texture_clamp_mode=**0xFF03** (u32 packs clamp 3 in the low byte + Lighting Influence 0xFF in byte 1 — every vanilla fire uses 65283, not 3). Always attach a NiAlphaProperty flags=0x100d (additive SRC_ALPHA/ONE) — vanilla particles always have one (campfire01burning uses 0x10ed/threshold 128 standard blending; source alpha is passed through when present).
- **NiBillboardNode root scrambles particle emission → invisible (fixed 2026-07-05; quad re-billboarding added same day)**: Oblivion fire/effect NIFs have a `NiBillboardNode` ROOT (to face the 2D fire quads at the camera) with the particle-system emitters nested UNDER it. A NiBillboardNode re-orients its entire subtree to face the camera every frame; a world-space emitter under it emits into a spinning frame → particles fly off-screen / the system renders nowhere. Vanilla Skyrim keeps particle emitters under a PLAIN NiNode (`slighthousefire.nif`: BSFadeNode→NiNode "Fireball-Emitter"→NiParticleSystem). Fix in nif_converter Pass (root handling): if a NiBillboardNode root's subtree contains any NiParticleSystem, DEMOTE the root to a plain NiNode (copy name/transform/children/extradata/controller) — **but wrap each direct GEOMETRY child (the flat fire quads) in a fresh child NiBillboardNode carrying the source root's billboard_mode** (vanilla campfire01burning pattern: BSFadeNode → NiBillboardNode "Plane05" → NiTriShape). A plain demote leaves the quads fixed-facing = edge-on/backfacing from most in-game angles = fires look invisible (while NifSkope's default camera happens to face them). Emitter/marker child nodes stay unwrapped. Non-particle billboard roots keep the whole-root wrap. **BILLBOARD AXIS CONVENTION (the final fire-quad invisibility fix, 2026-07-05)**: Oblivion mode-1 (ROTATE_ABOUT_UP) keeps local **+Y up / +Z at camera**; Skyrim mode-1 keeps local **+Z up / ±Y at camera**. Fire quads are authored flat in local XY (height along +Y) with IDENTITY transforms — correct under Oblivion's convention, but under Skyrim's an identity-rotation billboard leaves the quad LYING FLAT spinning about Z (edge-on from every standing viewpoint = invisible). The wrapper NiBillboardNode must carry vanilla's **−90°-about-X** static rotation `[[1,0,0],[0,0,1],[0,−1,0]]` (maps local Y→world Z; byte-identical to vanilla campfire01burning "Plane05"). Diagnosed by comparing vanilla billboard-node rotations (non-identity!) vs quad vert planes (both games author quads flat in XY).
- **EditorMarker geometry must be STRIPPED** (`walk_node`): Oblivion hides its editor-marker meshes (the pyramid in fire NIFs) via the node hidden flag, which our conversion clobbers with NIF_FLAGS (visible) — the marker then renders in game as an untextured BLACK PYRAMID (this was the mysterious "black pyramid" at placeatme'd fires; at world-placed fires it sat underground). Vanilla Skyrim ships no editor-marker geometry in these objects.
- **NiAlphaProperty must NOT be shared between particle systems**: Oblivion sources share one alpha block across several PS; vanilla Skyrim always pairs each PS with its own shader+alpha. `_convert_particle_system` clones the source alpha per PS.
- **NifSkope's "animate" option is NOT a valid diagnostic for Skyrim particle chains**: NifSkope 2.0.dev7 only registers the OLD `NiParticleSystemController`/`NiBSPArrayController` for particles (glparticles.cpp) and `BSEffectShaderPropertyFloat/ColorController` for effect shaders — it completely ignores `NiPSysEmitterCtlr`/`NiPSysUpdateCtlr`. A perfectly-authored Skyrim PSys NIF shows "No Animations in this NIF"; vanilla campfire only gets an animate option from its shader controllers on the glow quads.
- **UV SCALE (0,0) = INVISIBLE — THE fire-invisibility ENDGAME bug (fixed 2026-07-05)**: PyFFI's fresh `BSEffectShaderProperty` defaults `uv_scale` to **(0,0)** (vanilla: offset (0,0), scale **(1,1)**). Scale 0 collapses EVERY UV to the texture's top-left texel — transparent on flame textures — so all effect-shader geometry (particles AND quads) rendered fully transparent while being structurally perfect: sim ran (crash proved it), every block census-clean, texture valid. Diagnosed via A/B matrix: vanilla-structure+our-texture visible, our-structure+vanilla-texture invisible → field-by-field shader diff caught the one field never printed. ALWAYS set uv_offset(0,0)+uv_scale(1,1) on any PyFFI-created shader property; regression test asserts non-zero scale on every effect shader.
- **NiFlipController is dead in Skyrim (0/17,216 vanilla) — converted to atlas + float controller (2026-07-05, `asset_convert/nif/flipbook.py`)**: Oblivion animates fire quads by flipping the diffuse per frame. Conversion: decode the N frame DDSes (DXT1/3/5 → BGRA), compose a horizontal strip atlas padded to POT frame count (uncompressed BGRA32 DDS, written into the output textures tree beside `\meshes\`), set `uv_scale.u = 1/N_pad`, and drive `BSEffectShaderPropertyFloatController` (flags 0x48, var **6 = U Offset**, `NiFloatData` keys mode **5 = CONST** at k·delta → k/N_pad; delta from `NiFlipController.delta`, fallback cycle/N or 1/15s). Planned in `process_geometry` (validates source frames via `resolve_source_texture` — maps the rewritten tes4 path back to the export textures tree), built in `convert_nif` (knows dst tree). NifSkope animates it too (its EffectFloatController is supported — NifSkope "no animate option" on PSys-only NIFs is normal, but flip-book quads DO animate there now). Fallback on unresolvable frames: static first frame.
- **NiTextureTransformController → BS*ShaderPropertyFloatController (2026-07-21, `_collect_tex_transform_ctrls`/`_attach_tex_transform_ctrls`)**: Oblivion scrolls/scales UVs (waterfalls, lava, Oblivion gates, sunbeams — 61 Nehrim meshes, 127 controllers) with a `NiTextureTransformController` hosted on `NiTexturingProperty`. Conversion DELETES `NiTexturingProperty`, so the animation was silently lost and e.g. `landscapewaterfall02.nif` rendered as a frozen texture. Skyrim's equivalent is a shader float controller on the UV offset/scale, chained via `next_controller` — vanilla `fxwaterfallbodytall.nif` drives **V Offset with the same 2-key ramp**, and `fxwaterfallthin512x128.nif` chains U Scale + V Offset + U Offset on one shader. Harvest BEFORE properties are cleared in both `process_geometry` and `_convert_particle_system`, then re-attach to whichever shader was built (Lighting *or* Effect), preserving any flip-book controller already on it at the tail of the chain. The `NiFloatData` is reused as-is — both engines read the curve as a UV offset/scale over time. Mapping (`TransformMember` → Lighting/Effect enum): TRANSLATE_U 0→**20**/**6**, TRANSLATE_V 1→**22**/**8**, SCALE_U 3→**21**/**7**, SCALE_V 4→**23**/**9**. Flags: OR in **0x48** and keep the source cycle bits (0x06) — Oblivion's 0x08 lacks Compute-Scaled-Time so the curve never advances. **Dropped, not faked**: `TT_ROTATE` (2) — neither Skyrim shader exposes a UV rotation float, so 50/127 Nehrim controllers have no equivalent; `NiBlendFloatInterpolator` (46/127, all skull/fireball meshes) — driven by a `NiControllerManager` sequence, no inline keys to translate; and single-key curves (constants, not animation).
- **`BS*ShaderProperty*Controller.Target` must name its shader block — a NULL target is a CTD on cell load (2026-08-01, `_bind_shader_ctrl_target` / `_drop_unbound_shader_controllers`)**: `NiTimeController.Target` is a non-optional back-pointer for this controller family. Census: **15/15 vanilla `BS*ShaderProperty{Color,Float}Controller`s name their own shader block** (Lighting controllers → `BSLightingShaderProperty`, Effect → `BSEffectShaderProperty`); **0 nulls in 150 meshes sampled**. The Oblivion sources we rebuild these from (`NiMaterialColorController`, `NiAlphaController`, `NiTextureTransformController`) target the NiTriShape's *property list*, which has no Skyrim counterpart, so the rebuilt controller was written with `target = None`. Skyrim dereferences it while loading the shader property → `EXCEPTION_ACCESS_VIOLATION`. Crash signature: the faulting frame is the engine's own `BSLightingShaderProperty::LoadBinary` (reached via `LooseFileStream` → `BSResourceNiBinaryStream` → `NiStream`); with Community Shaders installed the return address lands in its `TruePBR.cpp` `BSLightingShaderProperty_LoadBinary` hook, which is a **red herring** — the hook simply calls the original, and the fault is inside it. Fix binds the target in `_match_seq_shader_types` (which already resolves each node's real shader for the Lighting-vs-Effect re-stamp) and drops any entry that still cannot be bound.
- **`"<node>:<index>"` in a sequence string palette means GEOMETRY, not a missing node (2026-08-01, `_retarget_geometry_suffix_entries`)** — the reason a shader-controller target can look unbindable. Oblivion's exporter names a node's geometry children two different ways, and `morroblivionchandilier01.nif`'s `Idle` sequence uses **both at once**:
  ```
  node='CandleSkinny01:0'          NiMaterialColorController   (emissive flicker)
  node='CandleSkinny01'            NiTransformController
  node='CandleSkinny01 NonAccum'   NiTransformController
  ```
  The last two name real `NiNode`s, so **the palette is not stale** — only the `:0` form needs translating. It means "geometry child 0 of `CandleSkinny01`", which after conversion is the shape carrying the `BSLightingShaderProperty` (block-named `Tri Tri Light_Com_Chandelier_01 2 0` under the other convention, `Tri <parent> <index>`). Resolve it by walking the named node's subtree, collecting shader-bearing geometry in tree order, and taking the Nth; then rewrite the entry's `node_name` to that real block name so the engine can re-bind at run time. The source `node_name` bytes are empty — the name lives only in the palette — so none of this is visible unless you resolve the offsets.
  **Do NOT "fix" this by deleting the entry.** That was tried first and is wrong twice over: it silently drops the chandelier's emissive flicker (a faithful conversion must keep it — the curve is 5 keys, 0→3s, and survives byte-identical), and emptying the sequence strands its `NiControllerManager` with **0 sequences**, which the engine dereferences exactly the same way — crash log named `RCX/RDI = NiControllerManager*`, `RAX = 0`, on `BSFadeNode "CandleSkinny01"`. Vanilla census: **0/8 managers have 0 sequences; 0/17 sequences are empty.** This was the Seyda Neen Census & Excise Office CTD — the chandelier is placed **7×** in that one room. *(Pre-existing and NOT part of this fix: 24 empty `Forward`/`Backward` sequences on `morroblivion\flora\*anim.nif` — separate issue, no manager involved.)*
- **Skyrim reads ONE UV set — a second one overruns the engine's vertex buffer (2026-08-01, `_clamp_uv_sets`)**: this was the **Seyda Neen Census & Excise Office CTD**. On disk the u16 **`BS Data Flags`** is a bitfield (`references/nif [version].xml` → `BSGeometryDataFlags`): **low 6 bits = UV-set COUNT** (mask 0x003F), bits 6–11 = Havok Material, **bit 12 (0x1000) = Has Tangents**. PyFFI splits that one field into `num_uv_sets` + `extra_vectors_flags`, which is why `extra_vectors_flags = 16` writes bit 12 — the converter's comment calling it an enum ("0=none, 16=has binormal+tangent") is wrong, it is a bitfield. The count is the **only** thing telling the engine how many `TexCoord` arrays follow the vertex colors, so a mesh that stores **2** sets while `BSLightingShaderProperty` binds 1 leaves the vertex buffer a whole array short: the copy runs past the end of the allocation and faults on a **non-temporal store** — `vmovntdq [rcx+N], ymm` where `rcx` is 32-byte aligned and `rcx+N` is exactly the first byte past a 64 KB page. That alignment signature (`memcpy` ≥4 KB, destination landing precisely on the page boundary) is the tell for a short destination buffer, **not** a bad pointer. Oblivion authors the extra set for detail/overlay passes Skyrim has no slot for; set 0 is the diffuse UVs every shader samples, so the surplus is dropped. Census: **2,233 vanilla shapes carry 0 or 1 UV sets, NEVER 2**; we shipped 2 on 5 meshes, including `morro\f\furnucomutableu05.nif` — the file the crash log named in its `inputFilePath`. Also note `bhkCompressedMeshShapeData` blocks legitimately dwarf these (500 KB+), so "big block" alone is not a signal.
- **A block type with no RTTI in SkyrimSE.exe is a hard CTD — audit with `tools/validate/nif_block_type_audit.py` (2026-08-01)**: `NiStream` constructs each block by looking its type NAME up in a factory registry. If the engine has no such class the slot is never built, and a link to it hands `NiPointer::operator=` a non-NiObject pointer; the engine runs `lock cmpxchg [ptr-0x10]` on the "refcount", which lands in **read-only `.rdata`** → `EXCEPTION_ACCESS_VIOLATION` while loading the mesh. No Papyrus trace, and **invisible to PyFFI**, which reads and writes the dead block happily. Diagnosis route (all three tools were essential): `tools/disasm/address_lib.py --log <crash>` to translate the Steam-build stack into GOG RVAs, `tools/disasm/skyrim_disasm.py --disasm` to read the faulting function, and `--find <ClassName>` to check RTTI. **`NiUVController` was the only such type** across 3000 converted meshes — searching RTTI for `NiUV` returns *only* `NiUVData`. It hit 8 Ghostfence meshes (`morro\x\exuggufence*`, `morroblivion\architecture\ghostgate\fence01*`). Note the whole Oblivion controller family is likewise absent from the exe (`NiFlipController`, `NiMaterialColorController`, `NiTextureTransformController`, `NiAlphaController` — all already converted elsewhere); `NiUVController` was simply missed. Run the audit after any converter change that can emit a new block type.
- **`NiUVController` → `BS*ShaderPropertyFloatController` (2026-08-01, `_collect_uv_ctrls`)**: it is Oblivion's UV-scroll animation carried on the **geometry** controller chain rather than on `NiTexturingProperty`. `NiUVData.uv_groups` is a fixed 4-entry array — **[U offset, V offset, U scale, V scale]** — holding the same curves a `NiTextureTransformController` would, so each populated group (≥2 keys; a single key is a constant) becomes one shader float controller through the existing `_attach_tex_transform_ctrls` path and `_TEX_TRANSFORM_VARS` mapping. Harvest must run **before** `_strip_dead_geometry_controllers`, which now also unlinks `NiUVController`. Ghostfence emits 6 controllers per mesh (U Offset 20 + V Offset 22 × 3 shapes). Shapes used purely as `NiPSysMeshEmitter` sources (`fence01.nif`'s `ForceField2`) legitimately end up with no shader and therefore no controller — that is correct, not a regression.
- **Emitter controller flags** (`NiPSysEmitterCtlr`/`NiPSysUpdateCtlr`/`NiPSysModifierActiveCtlr`): Oblivion ships flags=0x08 (Active only); **OR in 0x48** (Active | Compute-Scaled-Time, bit 0x40 default-true in Skyrim) — do NOT overwrite, because Oblivion's NiPSysUpdateCtlr carries CLAMP cycle bits (0x0c) that vanilla keeps (campfire01burning UpdateCtlr = 0x4c, EmitterCtlr = 0x48). Without Compute-Scaled-Time the birth-rate interpolator can evaluate to 0 (no particles).
- **Dangling gravity_object → broken particle sim → invisible (fixed 2026-07-05; necessary but NOT sufficient — the BSX Animated bit above was the final blocker)**: `collision.py::remove_empty_collision_nodes` deletes EVERY bare empty NiNode child of the root (0 children, no collision). Oblivion fire NIFs have empty marker nodes named `Gravity`/`SparkGravity` that the `NiPSysGravityModifier.gravity_object` points at — deleting them dangles the reference (PyFFI writes "NiNode block is missing from the nif tree: omitting reference"), and the engine's particle physics then fails → particles never render. Vanilla campfire01burning.nif KEEPS its `Gravity` node (block [2], referenced by the gravity modifier). Fix: `remove_empty_collision_nodes` now protects nodes whose id() is in `_collect_psys_referenced_nodes(root)` (gravity_object + every *Emitter.emitter_object). Detect the symptom: convert with pyffi logging at WARNING and grep for "missing from the nif tree", or check `id(gravity_object) in tree` after conversion.
- **NiParticleSystem block size sanity**: at BSStream 83 an empty-modifier-list particle system is ~142 bytes, +8 per extra modifier band; vanilla fire particle systems are 150 (10 modifiers). Compare header block_size across many vanilla meshes — a size that's LOWER than the vanilla floor for the same modifier count means a dropped field/ref. The 4 Far/Near Begin/End ushorts (PyFFI `unknown_short_2`/`unknown_short_3`/`unknown_int_1`, only when user_version≥12) are all 0 in vanilla fire — not a culprit.
- Diagnosing invisibility: read a WORKING vanilla particle mesh and diff the modifier chain (needs `NiPSysData.read` from pyffi_monkey_patch Patch 4 — stock PyFFI can't read Skyrim NiPSysData). The reference NIFConverter (`references/NIFConverter/copyover_legacy_nif_animations.py:915`) just DELETES NiParticleSystem (`replace_global_node(node, None)`) — do NOT copy that; convert to the visible BS* vocabulary instead.
- NiPSysGrowFadeModifier base_scale patch (Patch 2) still needed for any GrowFade that survives; makes the block 29 bytes = correct Skyrim size (NiPSysModifier parent 13 + own 16).
- NiPSysData: preserve original max particle count (`max(num_vertices, 75)` → bs_max_vertices). num_vertices and bs_max_vertices ALIAS the same PyFFI field slot.
- **CRITICAL — PyFFI 2.2.3 NiPSysData layout is STRUCTURALLY WRONG for Skyrim; hand-rolled in `pyffi_monkey_patch.py` Patch 4 (fixed 2026-07-05, the AnvilCastleGreatHall CTD)**: PyFFI's NiPSysData attribute list is the wrong (older Bethesda) field arrangement — it is MISSING Material CRC (4), Consistency Flags (2), Additional Data ref (4), Has Texture Indices (1), Aspect Flags (2), and invents spurious unknown_byte_1/unknown_link/unknown_short_3/unknown_byte_4. Net: an empty block writes 66 bytes where real Skyrim is **70**, and the FIELD ORDER is wrong regardless of size, so the SSE engine (which trusts the header block_size to seek to the next block) misaligns EVERY following block → it builds a BSEffectShaderMaterial from garbage → `vmovntdq [rcx+0xA0/0xC0], ymm` non-temporal store past a page end → CTD (crash logs named `BSEffectShaderProperty "DamageSphere"/"CandleFat02Fake"`). The correct 70-byte #BS202# layout (from `references/nif 0.10.0.0.xml`, verified == 70 on a census of 27 vanilla empty NiPSysData blocks) is emitted by overriding `NiPSysData.get_size`/`write` to pack the bytes directly: GroupID(i) BSMaxVertices(H) KeepFlags(B) CompressFlags(B) HasVertices(B) BSDataFlags(H) MaterialCRC(I) HasNormals(B) BoundCenter(3f) BoundRadius(f) HasVColors(B) ConsistencyFlags(H) AdditionalData(i) HasRadii(B) NumActive(H) HasSizes(B) HasRotations(B) HasRotAngles(B) HasRotAxes(B) HasTexIndices(B) NumSubtexOffsets(I) AspectRatio(f) AspectFlags(H) SpeedToAspect×3(f) HasRotSpeeds(B). **Field values (raw-byte census of ALL 837 NiPSysData blocks in 400 vanilla particle meshes, 2026-07-05 — supersedes the earlier 27-block census which was read through PyFFI's MISALIGNED layout and got the flags wrong)**: HasVertices=1, BSDataFlags=0, MaterialCRC=0, HasNormals=0, **HasVColors=1** (810/837), Consistency=0, **AdditionalData=-1** (837/837 — NULL ref; writing 0 references BLOCK 0 = the root!), **HasRadii=1** (837/837), NumActive=0, HasSizes=1, HasRots=0, HasRotAngles=1|0, HasRotAxes=0, **HasTexIndices=0 whenever NumSubtexOffsets=0** — the engine does `rand % NumSubtexOffsets` for atlas frame selection when the flag is set, so flag=1+count=0 = **EXCEPTION_INT_DIVIDE_BY_ZERO in the emitter update** (`div [rsp+...]`, crash names NiPSysCylinderEmitter+NiPSysData+NiPSysEmitterCtlr; 0/837 vanilla blocks pair flag=1 with count=0; atlas blocks have count 1..128 and block size 70+16×count — all 837 satisfy that size equation, fully validating the layout). AspectRatio=1.0 for non-atlas (0.0 on atlas blocks), AspectFlags=0, s2a floats=0, HasRotSpeeds=0. This crash only SURFACED once the BSX Animated bit made emitters actually run. `read` is NOT overridden for Oblivion sources — the converter only reads Oblivion-version sources (PyFFI's Oblivion layout is separately correct); our Skyrim output is never re-read by the pipeline. **PyFFI can no longer parse our Skyrim particle output — verify via the HEADER block_size table (inspect-only), NOT a PyFFI struct re-read.** Sweep: `NiPSysData` block_size must be 70 for empty pools.
- **Diagnostic method for "which field is wrong" (data-driven, per user directive — never compare against a single mesh)**: census MANY vanilla meshes (`references\Skyrim Meshes`, ~400 particle NIFs) reading only the header block_size table + field values; the value that is uniform across all vanilla but differs in ours is the bug (e.g. `has_subtexture_offset_u_vs`=True in 27/27 vanilla). When PyFFI can't even READ vanilla (`Skipping -4092 bytes`), that itself proves PyFFI's layout ≠ the real engine layout → hand-roll from nif.xml.
- The self-consistency trap: `block.get_size()` (fills header block_size) and `block.write()` can DISAGREE for a mis-conditioned PyFFI struct (get_size=66, write=70) → header says 66 but 70 bytes are written → engine seeks 4 short. A read→write round-trip inside a test masks this (re-read reconstructs arrays). Check `get_size()==len(write())` on the freshly-converted in-memory block, or the deployed file's header block_size vs vanilla census.
- **CRITICAL — `pyffi_monkey_patch.py` NiPSysData vercond precedence bug (fixed 2026-07-05)**: the added-particles shorts vercond was written as `'! version >= X && user_version >= 11'`. PyFFI's Expression parser binds `!` to `version` FIRST → `((!version) >= X) && ...` = ALWAYS FALSE → the two shorts were dropped from OBLIVION reads too, misaligning every source NIF containing NiPSysData by 4 bytes → read abort. This is why the ENTIRE `fire\`, `effects\`, `magiceffects\`, `dungeons\misc\fx\`, `landscape\waterfall*` etc. list in TODO.txt §7 failed with [RD] (123 of 151 recovered by the one-line fix). MUST parenthesize: `'!((version >= 335675399) && (user_version >= 11))'`. Verify with `Expression(expr).eval(ctx)` against Oblivion (v=0x14000004,uv=11 → present=True) and Skyrim (v=0x14020007,uv=12 → present=False). The "Skipping N bytes in NiPSysData/NiPSysGrowFadeModifier" messages when a converted file is re-read by STOCK (unpatched) PyFFI are expected — stock PyFFI has the buggy layout; the game engine follows the real nif.xml (matches our output). Confirm real correctness via a patched-reader round-trip, not stock-PyFFI block-size checks.
- **Fire/effect QUAD emissive (`process_geometry`, flip_ctrl path)**: BSEffectShaderProperty.emissive_multiple defaults to 0.0 → the flame quad renders BLACK. Fire is self-illuminated: set emissive_multiple=1.0. emissive_color is taken from the source `NiMaterialProperty`, falling back to (1,1,1) only when the source declares no emissive at all (see the next entry).
- **FX BRIGHTNESS + THE RECTANGULAR BOUNDING BOX (2026-08-07, `_apply_fx_soft_effect` + the `is_additive_fx` route)** — user report: "smoke effects such as in Vilverin are incredibly bright… way brighter than in Oblivion and difficult to see through, and many transparent effects have what appears to be a rectangular bounding box around them". Three separate defects, all in the FX shader path:
  1. **Authored emissive was discarded.** Both the quad and particle paths hardcoded `emissive_color=(1,1,1,1)`, throwing away Oblivion's own `NiMaterialProperty.emissive_color` — which is precisely how Oblivion dims an FX surface. `dungeons/misc/fx/fxmist01` ships (0.47,0.47,0.47) and `fxmistgroundeffect01` ships (0.13,0.16,0.17); both were being promoted to full white. Under **additive** blending (dst=ONE) the excess accumulates per overlapping layer, so a multi-plane mist reads as blinding and opaque instead of translucent. Now carried across verbatim; white only when the source emissive is pure black. `NiMaterialProperty.alpha` (previously dropped on the effect path entirely) goes to `emissive_color.a`.
  2. **`emissive_multiple` was a blanket 1.5 on every particle system.** That is a *fire* value, but the same code path converts smoke, mist, steam and dust. Vanilla census of 1,164 blended FX shapes: **1.0 in 852**; the brighter values are authored per-effect, never applied wholesale. Now 1.0, with the authored color doing the dimming.
  3. **`slsf_1_soft_effect` was never set anywhere.** Without it a blended FX quad intersecting solid geometry is hard-cut along the intersection line, so the billboard shows **its own quad edge** — the reported rectangle. Vanilla census (1,198 BSEffectShaderProperty shapes across meshes/effects + meshes/dungeons): additive `0x100d` → soft_effect=1 in **417/470**, blended `0x10ed` → **224/362**, *no* NiAlphaProperty → soft_effect=0 in **322/332**. So the rule is **blended FX gets the fade, unblended does not**; `soft_falloff_depth` = **100.0** (the commonest value, 250/521 on mist/smoke/fog geometry, and what vanilla uses for ambient room fog).
- **`lighting_mode == 0` is NOT the only unlit indicator — ADDITIVE BLENDING IS THE SECOND (same fix)**: the FX/lit discriminator was `NiVertexColorProperty.lighting_mode == LIGHTING_E`, but **many Oblivion FX meshes ship no `NiVertexColorProperty` at all**, so the mode defaulted to "lit" and genuine FX geometry took `BSLightingShaderProperty` — lit, normal-mapped, no soft fade. `fxmistgroundeffect01` (the Ayleid-ruin ground mist the user saw in Vilverin) is exactly this: additively-blended AtmosphereCloud01 planes with no vertex-color property, so **all 30 shapes** were misrouted. Across Oblivion's own FX directories **76 of 179** blended shapes declare no lighting_mode. A surface whose NiAlphaProperty sets **dst=ONE** adds its color to the framebuffer and therefore cannot be lit geometry (lighting it double-counts the light it already contributes). Vanilla agrees without exception: of 64 additively-blended shapes sampled, **64/64 use BSEffectShaderProperty, 0 use the lighting shader**. **Plain alpha blending is deliberately excluded** — the same census shows 3 legitimate BSLightingShaderProperty cases (glass/ice), so widening the rule to all blending would misroute real lit geometry. Blast radius measured before shipping: across a 250-mesh sample of architecture/clutter/dungeons only 10 shapes newly reroute, all `textures\effects\` blood decals and FlameTower quads.

## Rewriting the particle modifier chain
<a id="psys-modifier-vocabulary"></a>

**Code:** `_skyrimize_modifiers`, `_psys_order_for` in
`asset_convert/nif/particles.py`

The SSE particle engine drives only its own modifier vocabulary; an Oblivion
chain left as authored leaves the particles INVISIBLE. Four rules:

| Oblivion | Skyrim |
|---|---|
| `NiPSysGrowFadeModifier` | `BSPSysScaleModifier` (60-entry scale ramp) |
| `NiPSysColorModifier` | `BSPSysSimpleColorModifier` |
| — | `BSPSysLODModifier` injected when absent (universal in vanilla) |
| emitter / spawn / rotation / gravity / position / bound-update / age-death | kept as-is |

`NiPSysModifier`'s Name/Order/Target/Active are set on every modifier, and the
list is sorted by vanilla's processing `order` bands (census of
`slighthousefire.nif`). The engine processes modifiers in ascending order, so the
BS* rewrites and the injected LOD must slot into the same bands or the system
misbehaves.

**The scale ramp.** `grow_time`/`fade_time` are absolute seconds, but without the
emitter's life span they are treated as fractions of a unit lifetime — Oblivion's
fire values are small (grow 0.0, fade 0.2), and vanilla ramps peak ~1.0 and taper
to ~0.1.

### <a id="authored-particle-color"></a>The particle color is AUTHORED, never a palette

Skyrim's `BSPSysSimpleColorModifier` holds exactly three colors plus the
percentages at which each is reached, while Oblivion's `NiPSysColorModifier`
points at a `NiColorData` curve of arbitrary length — so that curve is sampled at
its start, middle and end.

**This used to write a fixed warm-orange "fire palette" for every particle system
in every plugin**, which is why the ghost's ectoplasm smoke came out orange/black
instead of the pale green its `NiColorData` actually specifies
(0.70, 0.83, 0.75 → 0.51, 0.65, 0.56). With no authored curve at all, a neutral
white ramp with an alpha envelope is the honest default — it tints nothing rather
than inventing a hue.

### <a id="alpha-envelope-vs-color-curve"></a>An alpha envelope is not a color curve

Oblivion uses `NiPSysColorModifier` for two unrelated jobs, and they need
opposite handling when deciding the shader tint:

- **an ALPHA ENVELOPE** — an achromatic ramp, R==G==B at every key, whose only
  real content is the alpha fade. `fxcloudthick01`, `fxcloudthin01` and
  `fxdustcloud01` all ship exactly (0,0,0,0) → (1,1,1,1) → (0,0,0,0). It
  contributes NO color, so the material's `emissive_color` is the only
  brightness the effect has.
- **a real COLOR CURVE** — chromatic keys, R≠G≠B. `creatures/ghost`'s `PArray*`
  systems ramp (0.702, 0.831, 0.745) → (0.514, 0.647, 0.561), the ghost's pale
  green, against a near-black 0.039 material. Here the CURVE is the authored
  color and the material is just a carrier, so deferring to the curve is right —
  carrying 0.039 through would multiply the green down to ~0.027 and render the
  ghost black.

Measured over **778** particle systems in `meshes/`: of the **190** that author a
dim (<0.5) emissive, **120** have an achromatic curve and **70** a chromatic one.
Telling them apart by whether the keys carry chroma is the authored test; "has a
modifier at all" conflates the two and whitens both.

Keys that are essentially black are ignored — they are the endpoints of an alpha
envelope — and chroma is called only on a real spread (`hi > 0.02` and
`hi - lo > 0.03`).

### <a id="psys-shader-values"></a>The particle shader's values

Flags match vanilla fire (`slighthousefire.nif` "Fireball"): `flags1` is
`z_buffer_test` only, `flags2` is `vertex_colors` only — particles do not write
depth, and they modulate color per-vertex.

**UV scale must be set explicitly.** PyFFI defaults it to (0,0), which collapses
EVERY particle UV to the texture's top-left texel — transparent on flame textures
— giving invisible particles. This was the fire-invisibility endgame bug
(2026-07-05). Vanilla is offset (0,0), scale (1,1).

`texture_clamp_mode` is **0xFF03**: a u32 packing clamp mode in the low byte
(3 = WRAP_S|WRAP_T) with lighting influence in byte 1 (0xFF). Every vanilla fire
effect shader uses this value.

**`emissive_multiple` stays at the neutral 1.0.** 1.5 was once applied to EVERY
particle system regardless of what it emits. It is a fire value (vanilla flame
shaders sit at 1.25–1.5), but the same code path converts smoke, mist, steam and
dust, and a 50% over-brighten on an additively-blended smoke plume makes it
glaring and opaque instead of translucent. Vanilla's overwhelming default is 1.0
(**852/1164** blended FX shapes); brighter values are authored per effect, not
applied blanket. Oblivion states the intended brightness in
`NiMaterialProperty.emissive_color`, so the multiple stays neutral and the
authored color does the dimming.

Whitening the shader when the curve is merely an alpha envelope is what made
Ayleid-ruin fog blinding: `fxcloudthick01` authors (0.078, 0.078, 0.078) against
a plain (0,0,0,0)→(1,1,1,1)→(0,0,0,0) ramp, so whitening over-brightened it
**12.8×** on additively blended geometry that Belda layers several planes deep. A
`NiVertexColorProperty` alone is likewise not a color source — every one of those
fog meshes carries one — so it does not force the tint either.

**Every particle system gets its OWN NiAlphaProperty.** Vanilla particles always
have one (additive: src=SRC_ALPHA dst=ONE, flags `0x100d`), and without it they
do not alpha-blend. Oblivion sources often SHARE one across several systems;
vanilla Skyrim never does, so the block is cloned per system.

**The emitter/update controller flags are OR'd to 0x48.** Oblivion ships
`flags=0x08` (Active only); vanilla Skyrim uses 0x48/0x4c (Active | Compute
Scaled Time, cycle bits preserved). The Compute-Scaled-Time bit (0x40) is
default-true in Skyrim and drives the emitter's time base — without it the
birth-rate interpolator can evaluate to 0. The bit is OR'd rather than
overwritten because Oblivion's `NiPSysUpdateCtlr` carries CLAMP cycle bits (0x0c)
that vanilla keeps (`campfire01burning` UpdateCtlr = 0x4c).

**The NiFlipController is NOT attached to the particle system.** It targets
`NiTexturingProperty`, which is gone by then, and attaching it to a
`NiParticleSystem` causes an invalid-target crash. The static first-frame texture
is used instead.

**`bs_max_vertices` must be non-zero.** At UV2≥34 (BS202) `NiPSysData`'s
per-particle arrays are NOT serialized — only boolean flags and
`bs_max_vertices`. The particle pool size moves from `num_vertices` (Oblivion) to
`bs_max_vertices` (Skyrim), and an empty pool crashes emitters trying to allocate
into it. The Skyrim `NiPSysData` layout is hand-rolled by `pyffi_monkey_patch`
Patch 4, since PyFFI's own layout is structurally wrong for `#BS202#`; that
serializer always emits an empty inline pool with
`BS Max Vertices = max(num_vertices, bs_max_vertices, 75)`.

Every vanilla Skyrim particle system also carries a `BSPSysLODModifier`
(**498/498** census), without which the system culls at all distances.

### <a id="billboard-axis-fix"></a>Oblivion and Skyrim disagree on the billboard axis

Oblivion mode-1 billboards keep local +Y up and +Z at the camera; Skyrim keeps
local +Z up and −Y at the camera. Oblivion-authored flat-XY quads therefore need
a −90° about-X rotation on their billboard node — byte-identical to vanilla
`campfire01burning` "Plane05".

**A wrapper this converter builds carries NO axis correction.** These meshes are
authored +Y-up and their PLACED REFERENCES carry the stand-up rotation: censused
across Oblivion.esm, **494** REFRs of the `Fire\*.nif` lights use RotX = ±90°
(10/10 for FireTorchLargeSmoke, 188+51 of 395 for FireOpenSmall). The whole model
— quads AND emitter markers — shares that one +Y-up frame, and the REFR rotates
all of it together. Pre-rotating the quad to +Z-up made it the ONLY part in a
different frame, so the REFR's −90° then laid it flat: the "third flame component
on its side", with the smoke and flame beside it looking correct. Such wrappers
are tagged so the later pass leaves them alone.

### <a id="billboard-demotion"></a>Demoting a billboard that contains particles

A billboard whose subtree holds a particle system is DEMOTED to a plain `NiNode`
— a billboarding ancestor would spin the emitters — and its direct geometry
children are wrapped in fresh billboard nodes instead.

**The demoted node does NOT inherit the billboard's rotation.** A
`NiBillboardNode` DISCARDS its own rotation at runtime and substitutes identity in
view space — NifSkope's `BillboardNode::viewTrans` (`glnode.cpp`):
`t = parent->viewTrans() * local; t.rotation = Matrix();`. So the authored
rotation was never used for orientation, and copying it onto the plain
replacement RESURRECTS a dead value: `firetorchsmall`'s "Sparks-Emitter" and
`firecandleflame`'s "FlameParticles-Emitter" are billboards carrying
+Z=(0,−1,0), and reviving that aims the emitter sideways — the horizontal jet
beside the upright flame.

**EXCEPT when the node is an EMITTER MARKER.** Rotation-is-discarded applies to
how a billboard DRAWS its subtree; a `NiPSysEmitter` reads its `emitter_object`
node's orientation as the emission DIRECTION, and that is live data.
`firecandleflame` authors quad and emitter in one +Y-up frame — quad identity with
extent [1.3, 2.6, 0.0] (tall in Y), emitter [1,0,0][0,0,−1][0,1,0] whose local +Z
maps to model +Y. Zeroing the emitter makes it +Z-up while the quad stays +Y-up,
so the flame splits into an upright quad and a sideways particle jet, visible once
the FlameNode marker rotates the pair into a +Z-up host.

Particle modifiers in the subtree may reference the OLD billboard node
(`emitter_object` / `gravity_object`), so those are remapped to the replacement or
the reference dangles ("block is missing from the nif tree") and the sim breaks.

## NIF FlameNode → grafted converted flame (rewritten 2026-07-05, replaces the MPS/AddonNode substitution)
<a id="nif-flamenode-grafted-converted-flame"></a>
- Oblivion marks where a flame burns with an empty `FlameNode*` NiNode (a bare marker: name + transform, no children) and attaches a flame NIF there at RUNTIME (`fire\firecandleflame.nif` for candles/sconces/lamps/etc., torch flame for torches). 108 Oblivion meshes have them.
- **Conversion (`_convert_flame_nodes` + `_load_converted_flame` in nif_converter.py)**: the flame NIF for each marker's socket (see the FlameNode STAT table below) is run through the FULL converter once per worker (cached as serialized bytes; deep copies by re-reading — requires the patched-PyFFI NiPSysData `read`), and the converted root's children are grafted under each empty FlameNode marker. Marker keeps TRANSLATION, SCALE **and ROTATION** — all three are authored. The rotation is the hook-up between two model frames: the flame NIFs are +Y-up, and a +Z-up host carries the −90°X correction on its marker (`uppersilverplatecandles01`'s FlameNode0 is `[1,0,0][0,0,1][0,-1,0]`, i.e. `_BB_AXIS_FIX` itself — that host is a flat plate, extent X=23 Y=23 Z=2, and all 121 of its REFRs use RotX=0, so nothing else would stand the flame up). Zeroing it laid the candle flames on their side; +Y-up hosts author an identity marker and are unaffected. Host root gets BSX bit 0 OR'd in (grafted controllers must tick); the flame's flip-book atlas jobs are merged into the host stats so `convert_nif` writes the atlas into every output tree that needs it. Graft runs in `convert_nif` BEFORE the atlas build step.
- **The earlier "embedding crashes the engine" lesson is OBSOLETE**: that crash (`vmovntdq` past page end, `BSEffectShaderProperty "CandleFat02Fake"`) was actually the PyFFI NiPSysData 66-vs-70-byte misalignment (+ uv_scale=(0,0)) — both long fixed. The interim `BSValueNode`/`AddOnNode` MPS substitution (`_ADDN_CANDLE_FLAME`=49 / `_ADDN_TORCH_FIRE`=46 / BSX bit 0x10) is deleted per user directive: convert, don't substitute.
- **Billboard handling is now GENERAL (any tree depth, `_skyrimize_billboard`)**: firecandleflame.nif nests its particle emitter under TWO levels of NiBillboardNode, so root-only handling was insufficient. Every non-root NiBillboardNode on the walk (and root's direct children — they use a separate loop in `_convert_nif` that needs the same hook): contains a NiParticleSystem anywhere in its subtree → DEMOTE to plain NiNode + wrap its direct geometry children via `_wrap_in_billboard` (fresh NiBillboardNode, source mode, `_BB_AXIS_FIX` −90°X rotation); pure-geometry billboard → keep but COMPOSE the axis fix into its rotation (Oblivion billboards are authored identity over flat-XY quads). **When demoting, remap `emitter_object`/`gravity_object` refs that pointed at the old billboard node to the replacement** — else they dangle ("block is missing from the nif tree") and the particle sim breaks.

- **FLAME QUADS STAY IN THE MODEL FRAME — no axis fix on the wrapper (fixed 2026-08-20)**: `_wrap_in_billboard` used to compose `_BB_AXIS_FIX` (−90°X) into every wrapper it built. That is wrong for these meshes: they are authored **+Y-up and their PLACED REFERENCES carry the stand-up rotation** — censused across `Oblivion.esm`, **494 REFRs** of the `Fire\*.nif` lights use `RotX = ±90°` (10/10 for `FireTorchLargeSmoke`, 188+51 of 395 for `FireOpenSmall`). The whole model — quads AND emitter markers — shares that one frame and the REFR rotates all of it together. Pre-rotating only the quad made it the sole part in a different frame, so the REFR's −90° then laid it flat: reported in game as "a third flame component on its side" beside a correct-looking flame and smoke. `_wrap_in_billboard` now applies NO fix and only tags `bb._axis_fixed = True`, so the later `_skyrimize_billboard` pass leaves its wrappers alone (that guard still fires — measured 27 times over 81 billboard meshes — and without it the pure-geometry branch would compose the fix back in). `_compose_axis_fix` remains live for genuinely Oblivion-authored pure-geometry billboards (249 calls over the same 81 meshes). Guarded by `test_flame_keeps_the_authored_model_frame`.
- **A DEMOTED BILLBOARD INHERITS IDENTITY — except emitter markers**: a `NiBillboardNode` DISCARDS its own rotation at runtime and substitutes identity in view space (NifSkope `BillboardNode::viewTrans`, glnode.cpp: `t = parent->viewTrans() * local; t.rotation = Matrix();`). Copying that dead rotation onto the plain replacement resurrects a value the engine never used. **But a `NiPSysEmitter` reads its `emitter_object` node's orientation as the emission DIRECTION**, which is live data — `firecandleflame` authors quad and emitter in one +Y-up frame (quad identity, local extent `[1.3, 2.6, 0.0]`; emitter `[1,0,0][0,0,-1][0,1,0]`, local +Z → model +Y), and zeroing the emitter made it +Z-up while the quad stayed +Y-up: an upright flame with a second, sideways particle jet, most visible once a FlameNode marker rotated the mismatched pair into a +Z-up host. `_is_emitter_marker()` keeps the rotation for nodes referenced as `emitter_object`/`gravity_object`; every other demoted billboard still gets identity. Guarded by `test_emitter_and_quad_agree_on_up`.
- **WHICH FLAME BURNS AT A SOCKET IS AUTHORED — read the FlameNode STATs**: Oblivion ships one STAT per socket (WorldObjects/Static, EditorID `FlameNode<N>`) whose MODL is the flame to attach: `FlameNode0` `0x1E` FireCandleFlame, `1` `0x1F` FireTorchSmall, `2` `0x20` FireTorchLarge, `3` `0x21` FireTorchLargeSmoke, `4` `0x22` FireOpenSmall, `5` `0x23` FireOpenSmallSmoke, `6` `0x24` FireOpenMedium, `7` `0x25` FireOpenMediumSmoke, `8` `0x26` FireOpenLarge, `9` `0x27` FireOpenLargeSmoke. Those FormIDs are the keys `Oblivion.exe` hardcodes — the socket-name table at `0xB06818` is walked in lockstep with `0xB067C0` holding `0x1E..0x32`, looked up in the form map at `0xB0613C` — so the **plugin owns the mapping and a mod may repoint it**; `flame_socket_map()` parses it from the export's `STAT.txt` (cached per export root). Keying on the host FILENAME instead ('torch' in the name) put the 1.3×2.6-unit candle flame on every lamp in the game: `castlelight02` is a 105-unit fixture on socket 2, i.e. FireTorchLarge (32×64). Resolution is **per marker** — `lecternworkstation1` mixes FlameNode0 candles with a FlameNode1 torch. Guarded by `test_flame_comes_from_the_flamenode_stat`.
- **A ZERO-PADDED SOCKET BURNS NOTHING**: the engine matches socket names EXACTLY, and its table holds only unpadded `FlameNode<N>` — `Oblivion.exe` contains `FlameNode7` and `FlameNode1` but neither `FlameNode07` nor `FlameNode01`, and the STATs are likewise unpadded. Two vanilla meshes are authored with padded markers and show **no flame in the original game**: `clutter/metalsmith/forgeopen01.nif` (`FlameNode07`) and `clutter/lecternworkstation1.nif` (`FlameNode01`). Matching them loosely put a 468-unit FireOpenMediumSmoke on the forge. `_FLAME_SOCKET_RE` is `^FlameNode(0|[1-9][0-9]*)(?![0-9])` and an unmatched socket grafts NOTHING — there is no default-flame fallback. Guarded by `test_zero_padded_socket_burns_nothing`.
- **FX BRIGHTNESS IS THE AUTHORED VALUE, NEVER THE FILENAME (2026-08-27)**: a 2026-08-20 revision classified flames by the diffuse PATH — `_is_fire_fx()` matched `fire`/`flame`/`torch` minus a `smoke`/`mist`/`fog`/`dust`/`steam`/`cloud` veto — and forced `soft_effect=0, emissive_multiple=1.5` on every hit. **That was wrong and is removed.** It misfired on `textures\lights\torch02.dds`, the WOODEN HANDLE whose host `lights\torch02noflame.nif` contains no flame at all, and it could only ever work for meshes following Bethesda's naming — never Nehrim, Morroblivion or any third-party plugin. Oblivion states brightness per SHAPE in `NiMaterialProperty.emissive_color`, and across all 778 particle systems under `meshes/` the populations do not overlap: **flames author full white 1.0** (`firetorchlarge` "Fire", `crtfirelogs` "PCloud08BigFlame"; 227 systems at 1.0) while **fog/dust author 0.047–0.337** (`fxcloudthick01` 0.078, `fxcloudthin01` 0.047, `fxdustcloud01` (0.337,0.337,0.294); 190 systems below 0.5). Better than 12x separation, and per-shape — which matters because `firetorchlargesmoke.nif` holds a flame AND a smoke plume in one file, so any per-file test must give them the same answer. Rule: **carry `emissive_color` through verbatim and hold `emissive_multiple` at the vanilla-neutral 1.0.** A flame authored full white is already at full emission and needs no boost. Guarded by `test_fx_emissive_is_the_authored_value`.
- **A SELF-LIT FLAME MUST NOT TAKE THE SOFT DEPTH FADE (2026-08-27)** — user report: candles like `lights\uppersilverplatecandles01.nif` "glow red instead of the flames". **The red was never a new light**: the candle WAX authors `NiMaterialProperty.emissive_color` (0.953, 0.910, 0.678) on the LIGHTING path (`slsf_1_own_emit`), code unchanged since the initial commit. What regressed is that the FLAME in front of it disappeared. Removing the filename classifier (entry above) dropped BOTH halves of that commit's behaviour, but only the brightness half was given an authored replacement — flames then started taking `slsf_1_soft_effect`, and the depth fade attenuates a quad against whatever it intersects. A candle flame sits directly on its own wax and a sconce flame against its own bracket, so the fade dimmed each flame into its own holder, leaving only the wax glow visible. **Vanilla authors the split inside ONE mesh**: `mps\mpscandleflame01.nif` — both particle systems, both additive `0x100d`, both `emissive_multiple` 1.0 — has `CandleFlame01` **soft=0** (falloff 2.0) and `CandleGlow01` **soft=1** (falloff 6.0); likewise every mounted fire core (`slighthousefire` Fireball, `torchsconce01` pFireballCore04, `giantcampfire01burning` PFireball — 49 such particle systems across 281 vanilla fire meshes). **Skyrim's value is NOT reconstructible from structure**: over 511 vanilla FX shaders neither block type (particle 119/168 soft=1 vs geometry 159/343), nor alpha flags (`0x100d` splits 163/74), nor `double_sided` (78% vs 44%) predicts it — it is authored per effect, and Oblivion has no equivalent field to carry across. So key it on the one authored quantity that DOES separate the populations, the same emissive that drives brightness: **full-white (>=0.999) = self-lit light source -> soft=0**; anything dimmer = ambient haze -> soft=1 (fog 0.047-0.078, dust 0.337, mist 0.310). The asymmetry justifies the cut: a missing fade only omits a Skyrim-era nicety from a flame, while a wrongly-applied one ERASES the flame. Guarded by the soft_effect assertion in `test_fx_emissive_is_the_authored_value`.
- **OBLIVION'S GLOW MAPS ARE L8 AND SKYRIM RENDERS THEM PURE RED (2026-08-27, `asset_convert/texture/luminance_textures.py`)** — user report: candles such as `lights\uppersilverplatecandles01.nif` "glow red instead of the flames". Oblivion ships glow maps as **8-bit DDPF_LUMINANCE** (`pf flags 0x20000`, `bitcount 8`, masks **R 0xFF / G 0x00 / B 0x00**): one channel, sitting under the RED mask. Oblivion's shader replicates that channel across RGB. Skyrim's glow shader (`skyrim_shader_type` 2, slot 2) samples slot 2 as an ordinary RGB texture and does **not** replicate, so green and blue read zero and the surface glows **pure red**. Census of Oblivion's whole texture tree: **469 files are L8, and every single one is a `_g` glow map** — no other suffix uses the format and no glow map uses another format (the rest: 8481 DXT5, 5981 DXT1, 5762 DXT3, 124 uncompressed RGB). Vanilla Skyrim never ships L8: its own glow maps are RGB textures whose CONTENT is grey (`spriggan_g.dds` is DXT1 with R==G==B==17.2 mean). **Fix: expand L into R=G=B as uncompressed BGRA8**, every mip level, alpha opaque — lossless, no DXT encoder needed, and these files are small. Runs in `asset_pipeline` AFTER the texture copy (same placement and reason as `landscape_normals.run`, so a re-copy cannot resurrect the L8 originals) and is idempotent, because a converted file is no longer DDPF_LUMINANCE. Keyed on the FORMAT, not the `_g` suffix, so a plugin shipping an L8 diffuse is handled too. **This is what `_apply_glow` (d6aa341) exposed**: that commit routes a derived `<diffuse>_g.dds` into the Skyrim glow slot even when the source NIF names no glow texture at all — `uppersilverplatecandles01`'s wax has only a base texture — so meshes that never had a glow shader in Oblivion acquired one, pointed at an unreadable format. The authored emissive on that wax IS real, however: Oblivion marks it (0.953, 0.910, 0.678) warm cream, which is what should show. Guarded by `TestLuminanceGlowMapsBecomeRGB`.
- **A COLOR MODIFIER IS NOT AUTOMATICALLY A COLOR — chroma is the test (2026-08-27, `_color_curve_carries_hue`)**: the 2026-08-26 ghost work made a `NiPSysColorModifier` *or* a `NiVertexColorProperty` suppress the authored emissive and force the shader tint to white, on the theory that the per-particle curve supplies the color. That is only half true, and it **re-broke Ayleid-ruin fog** (user report: Belda). Oblivion uses the modifier for two unrelated jobs: an **alpha envelope** — achromatic, R==G==B, `(0,0,0,0)→(1,1,1,1)→(0,0,0,0)`, which is what `fxcloudthick01`/`fxcloudthin01`/`fxdustcloud01` ship and which contributes NO color — versus a real **color curve**, chromatic, which is what `creatures\ghost\skeleton.nif` ships (pale green (0.702,0.831,0.745)→(0.514,0.647,0.561)) against a near-black 0.039 carrier material. Whitening on the mere PRESENCE of a modifier conflates them: fog's authored 0.078 became 1.0, a **12.8x over-brighten** on additively-blended planes that Belda layers several deep. Of the 190 dim (<0.5) particle systems, **120 have an achromatic curve** and only 70 a chromatic one. Rule: defer to the curve **only when its keys carry actual chroma** (`hi>0.02 and hi-lo>0.03`, ignoring the near-black envelope endpoints); otherwise the material's emissive is the only brightness the effect has. Sample the curve BEFORE `_skyrimize_modifiers` rewrites `NiPSysColorModifier` into `BSPSysSimpleColorModifier`. Guarded by `test_chromatic_color_curve_still_defers_to_the_curve`.
- **NifSkope striping on flip-book quads is COSMETIC**: NifSkope's GLSL path (`sk_effectshader.frag`) applies `uvScale`, but its fixed-function fallback maps raw UVs — the whole N-frame atlas strip shows across the quad ("texture, blank, texture"). Vanilla meshes use scale (1,1) so the fallback looks right for them; in-game the engine always applies the scale.

## NIF NiGeomMorpherController (dead in Skyrim, fixed 2026-07-05)
<a id="nif-nigeommorphercontroller"></a>
- **0 of 17,216 vanilla Skyrim meshes use NiGeomMorpherController** — it's Oblivion's bow flex/morph system; Skyrim bows are `*skinned.nif` and flex via skeletal animation. Strip it (and NiMaterialColorController) from geometry controller chains: `_strip_dead_geometry_controllers()` walks `geom.controller.next_controller` and unlinks them. This also lets NiTriStrips that were only kept as strips (because of the morpher) convert to NiTriShape.
- Why it mattered: PyFFI mis-serializes NiGeomMorpherController across the 20.0→20.2 bump — `interpolator_weights` is populated under the Oblivion layout but EMPTY under the Skyrim layout, so `data.write` aborts with `array size (0) different from field describing number of elements (N)`. This was the entire `weapons\*\bow.nif` [WR] failure list in TODO.txt §7.

## NIF embedded Ni*Light blocks (dead in Skyrim, fixed 2026-07-18)
<a id="nif-embedded-nilight-blocks"></a>
- **0 vanilla Skyrim meshes contain any NiAmbientLight/NiDirectionalLight/NiPointLight/NiSpotLight block** (nif_block_scan). They are 3ds Max export leftovers in a handful of Oblivion assets (11 meshes: statuegodszenithar01, sanguine statue/shrine, priory doors/cabinets, vine01/02, countess clothes _gnd). SSE fails to load a static carrying one — statuegodszenithar01.nif (NiAmbientLight child of the root) rendered as the missing-model red triangle (TODO §26).
- Skyrim lighting comes from placed LIGH references, never from mesh-embedded light nodes, so there is nothing to convert them into. `walk_node`'s NiDynamicEffect branch now strips ALL dynamic-effect subtypes (it previously kept Ambient/Point/Spot believing them valid; NiNode `effects` arrays were already cleared, but a light in the `children` array survived).

## Early-Oblivion NIF versions (10.0.1.0 / 10.0.1.2 / 10.1.0.106) — the [RD] read failures (SOLVED 2026-07-15)
<a id="early-oblivion-nif-versions-rd"></a>
Oblivion's BSAs contain dev-era leftovers in older NIF versions that PyFFI 2.2.3 can't parse (floorplane01, handscythe01, oar01, stonepedastellarge01, ungrdltraphingedoor, kvatch castle int hallway01, arwelkydclusterfx01, scampswitch01). Fixed with monkey patches 5-7 in `asset_convert/nif/pyffi_monkey_patch.py` (field-presence guards verified against `references/nif 0.10.0.0.xml` + byte-level decode):
- ≤10.0.1.2: extra uint after bhkWorldObject.Shape and at the start of HavokMaterial; bhkRigidBody CInfo lacks the 16-byte filter-copy header and max-velocity trio; bhkMoppBvTreeShape lacks the offset vector; bhkNiTriStripsShape lacks the scale Vector4; 10.0.1.0 mopp data is FULL size (pyffi's "size-1" convention is pre-Bethesda).
- 10.1.0.106: NiSingleInterpController.Interpolator exists since 10.1.0.104 (pyffi said 10.2); NiInterpController has a Manager Controlled byte (10.1.0.104-108); NiPSysEmitterCtlr.VisibilityInterpolator since 10.1.0.104; NiBlendInterpolator uses the full runtime-state layout (item array + per-subclass value snapshot: Transform 35B, Point3 12B) — hand-rolled consume-only reader.
- `bhkConvexSweepShape` (10.0.1.0 clutter) registered as a class at runtime; `_convert_shape` unwraps it to its inner shape (Skyrim never ships it).

## Morrowind legacy particle emitters — the red-triangle candles/torches/fires (SOLVED 2026-09-12)
<a id="morrowind-particle-systems"></a>
**Code:** `asset_convert/nif/particles_morrowind.py`

Morrowind predates the `NiPSys*` vocabulary entirely. An emitter is a `NiParticles` subclass — `NiRotatingParticles` (165 files) or `NiAutoNormalParticles` (9) — driven by a `NiParticleSystemController`, with affectors on that controller's `particle_extra` linked list rather than in a modifier array. Skyrim has RTTI for none of them: **0 occurrences across 17,216 vanilla meshes**, against 599 files using `NiPSysData`. The engine rejects the file and draws the missing-model red triangle. 174 meshes in Tamriel Data / Tamriel Rebuilt are affected — every candle, torch, brazier and fire.

The blocks READ fine (they inherit `NiParticles` → `NiGeometry`, so PyFFI parses them), and the geometry walk never touched them because it dispatches on `NiTriShape`/`NiTriStrips`. So they passed straight through conversion into the output unchanged — a file that converts "successfully" and still cannot load.

- `upgrade_legacy_particles` runs in the Morrowind pre-pass (`run_morrowind_fixups`), BEFORE the version upgrade and the geometry walk. It rewrites each legacy emitter as a `NiParticleSystem`, so the normal Oblivion path (`particles.py: convert_particle_system`) then finishes the job unchanged — no parallel conversion path.
- Every emission parameter is AUTHORED on the controller and is mapped verbatim: `speed`/`speed_random` → speed, `vertical_angle` → declination_variation, `horizontal_angle` → planar_angle_variation, `lifetime`/`lifetime_random` → life_span, `size` → initial_radius, `emitter` → emitter_object, and `emit_rate` → the `NiPSysEmitterCtlr`'s NiFloatInterpolator value (birth rate lives on the CONTROLLER in Skyrim, not on the emitter).
- The `particle_extra` chain maps to the modifier array: `NiParticleGrowFade` (139 files) → `NiPSysGrowFadeModifier`, `NiGravity` (4) → `NiPSysGravityModifier`, `NiParticleRotation` (1) → `NiPSysRotationModifier`. `NiParticleColorModifier` (3) is left to the Oblivion path, which builds `BSPSysSimpleColorModifier` from the authored material.
- **A freshly built controller MUST carry `target`, or the game crashes.** `NiTimeController::m_pTarget` (x64 offset 0x38) is loaded and dereferenced with NO null check: `mov rax,[rcx+0x38]` then `mov rbx,[rax+0x18]` (stable ID 74716 +0x3c; 1.6.659 RVA 0xd517fc) — the engine then walks `[rbx+0x40]` down the target's children calling a vtable method, so it wants the owning NiParticleSystem. A null there is an instant EXCEPTION_ACCESS_VIOLATION reading 0x18 the moment the system updates. This shipped once and crashed on a Morrowind candle; the Oblivion path never hit it because authored sources already carry `target`, and it is invisible to every structural check. Both controllers get `target = psys`, and `visibility_interpolator` gets a true NiBoolInterpolator (40/40 vanilla emitter controllers carry one; 0 are null). Update-controller flags are 0x4c, not the emitter's 0x48.
- Modifier `order` is not set here: `_skyrimize_modifiers` sorts and stamps the whole chain against `_PSYS_ORDER` on the second pass, which is the single source of that vocabulary.
- The parent `NiBSParticleNode` and `NiLODNode` are separately handled by `_REWRITE_AS_NINODE` — see [legacy block types](#legacy-block-types); an emitter fix alone is not enough for these files.
- Vanilla Skyrim candles use a `BSValueNode`/AddonNode pointing at a shared flame effect instead of an in-mesh system. That was rejected: it needs new ADDN records and would DISCARD the authored rate/cone/lifetime each mesh carries. In-mesh `NiParticleSystem` is equally vanilla-legal (599 files).

## Morrowind-era legacy block types — the [RD] "Unknown block type" failures (SOLVED 2026-09-12)
<a id="legacy-block-types"></a>
PyFFI creates a block with `getattr(NifFormat, block_type)` and raises `ValueError: Unknown block type` for any name its 0.7.1.1 nif.xml never declared, failing the **whole file** on the first occurrence. Tamriel Data (Morrowind-port assets, all version 4.0.0.2) uses `NiCollisionSwitch`, which PyFFI omits even though it ships every other Morrowind legacy node (`AvoidNode`, `RootCollisionNode`, `NiBSParticleNode`). This cost 42 of the 43 mesh failures on `tamriel_data.esm` — every TR water plane, window, fountain, ship interior and incense burner.
- `_install_legacy_block_types` (patch 15) registers each name in `_LEGACY_NINODE_BLOCKS` as a runtime `NiNode` subclass. Both `references/nif 0.10.0.0.xml` and `references/nif 0.9.2.0.xml` declare it `<niobject name="NiCollisionSwitch" inherit="NiNode">` with **no fields of its own**, so the NiNode layout reads it byte-for-byte; OpenMW agrees (`niffile.cpp`: `construct<NiNode, RC_NiCollisionSwitch>`).
- The node's purpose — toggling collision on its subtree — rides in the standard NiNode flags (bit 0x20 = collision disabled), so nothing extra is parsed. It converts as the NiNode it is; Skyrim takes collision from the bhk tree, not from this node.
- Add a name to the tuple, not a new function, when another bare-NiNode legacy type turns up.

## Orphaned blocks in `data.roots` — the [EXC] `'<block>' object has no attribute 'controller'` failures (SOLVED 2026-07-20)
<a id="orphaned-blocks-dataroots-exc-block"></a>
PyFFI reports **every unreferenced block** as a root, not just scene-graph roots. Many Nehrim meshes (all of `castle\*_far.nif`, `artilleryduell\flamecannonballnew.nif`, the `nehrim\zahnrad*` gear set, ~60 files) were authored by tools that leave dangling `NiTriShapeData` / `NiTriStripsData` / `NiBinaryExtraData` / `bhkCollisionObject` / `Ni*Property` blocks behind, so `data.roots` comes back as `[NiNode, NiTriShapeData, ...]`. Every pass in `_convert_nif` assumes a root is a node and reads `root.controller` / `root.children` → `AttributeError` (the varying class name in the error is just whichever orphan landed in the list).
- `_prune_orphan_roots(data)` runs first in `_convert_nif`: keeps `NiAVObject` roots, plus any non-node root still reachable from them (never drop something a kept root references). No-ops when there are <2 roots or no node root at all, so it can't empty `roots`.
- The orphans are unreachable from the real root — dead weight, so dropping them also shrinks output. PyFFI's "block is missing from the nif tree: omitting reference" notice on write is the expected, benign confirmation.
- **Files whose ONLY root is a non-node** are standalone animation files (`creatures/*/idleanims/*.nif` → a lone `NiControllerSequence`). There is no geometry to convert; `convert_nif` returns `error='NOGEO'` and skips instead of crashing.
- Related trap: **never trust `num_vertices`/`has_normals` over the actual array length.** `leyawiinhouselower01_far.nif` has a shape with `num_vertices=16` but an empty `vertices` array (stale count, `has_vertices` unset), which made `np.array([...])` a `(0,)` array and blew up the matmul in `inv_marker._gather_area_normals`. Guard with `len(gd.vertices)` and `len(gd.normals) == len(gd.vertices)`.

## NIF NiDefaultAVObjectPalette fixup
<a id="nif-nidefaultavobjectpalette-fixup"></a>
- After converting NiTriStrips→NiTriShape, NiDefaultAVObjectPalette entries still reference old blocks. Must update `av_object` references using a block_map (old id → new block). Without this fix, PyFFI writes "NiTriStrips block is missing from the nif tree" warnings and the animation palette has stale references.

## Skinned shape = red triangle: NiSkinPartition still in STRIP format (SOLVED 2026-08-01)
<a id="skinned-shape-red-triangle-niskinpartition"></a>
**This is the actual cause of the `ropebucket01.nif` red triangle.** (The
`skeleton_root` fix below is a real defect and was fixed in the same pass, but
it did NOT fix the red triangle — don't stop there again.)

- A `NiSkinPartition` stores geometry as **either strips or triangles**.
  Oblivion writes strips. Skyrim's renderer draws a skinned shape from the
  **partition**, not from `NiTriShapeData` — a strip-format partition hands it
  no triangles and the shape renders as the red missing-geometry marker.
- **Census: 678/678 vanilla skin partitions across 350 sampled meshes store
  TRIANGLES. Zero store strips.**
- The strips→triangles pass in `walk_node` rebuilds `NiTriShapeData` but
  **does not touch the partition**. The two `regen_skin_partition` passes that
  would fix it are gated on mesh **category**: `creature and has_skin`, and
  worn armor (`_in_armor_dir`). Anything else that happens to be skinned kept
  its Oblivion strip partition — self-skinned clutter (rope, chain, banner,
  hanging bucket), effect meshes, creature parts outside the creature path.
- **Not one file:** a sweep of 500 converted meshes found **93 strip-format
  partitions across 6+ unrelated meshes** (`roothavok05`, `parachuteclosed`,
  `refractioneffect`, `thornelemental`, `sloftarantulafuzzyredknee`,
  `handrberskir`).
- **Fix:** a category-independent safety net after all the category passes —
  regenerate any partition still reporting `num_strips > 0`. Existing passes
  are untouched (they already emit triangles). Counter:
  `stats['skin_partitions_destripified']`.
- **Diagnostic:** `pb.num_strips > 0` / `len(pb.triangles) == 0` on any
  `skin_partition_block`. Checking the shape's `NiTriShapeData` is NOT enough —
  it looks perfectly healthy while the partition is broken.

## Dangling back-references to `old_root` after NiNode→BSFadeNode (skin case SOLVED 2026-08-01)
<a id="dangling-back-references-oldroot-after"></a>
The root swap in `nif_converter.py` builds a **new** BSFadeNode and drops the
original NiNode out of the tree. Every block still pointing at `old_root` is
then unreachable, and PyFFI silently writes that link as null (-1). The fixup
block after the swap must retarget *all* of them — it already handled
`NiTimeController.target`, `.extra_targets`, and `NiDefaultAVObjectPalette`, but
**not `NiSkinInstance.skeleton_root`**.

- **Symptom:** none observed in-game on its own. This was initially blamed for
  the ropebucket red triangle; fixing it changed nothing, and the real cause was
  the strip-format skin partition above. It is still a genuine broken link
  (source `skeleton_root = RopeBucket01`, output `None`) and worth fixing, but
  do not treat a dangling `skeleton_root` as an explanation for a red triangle.
- **Who it hits:** self-skinned *clutter*, i.e. a mesh whose bones live in its
  own tree rather than on the character skeleton — rope, chain, banner, hanging
  bucket. Found on `dungeons\chargen\ropebucket01.nif`, whose two `BucketRope:*`
  shapes are skinned to the internal `c_BucketBone00..07` chain with
  `skeleton_root` = the root node. Worn armor is immune because it keeps a
  NiNode root (no swap happens).
- **Detection:** dump source vs output and compare — source has
  `skeleton_root = RopeBucket01`, broken output has `None`.
- Note the shapes here are already `NiTriShape` in the source, so
  `get_interchangeable_tri_shape()` is *not* involved. (That method does
  `deepcopy` the skin instance, which would orphan the same links for a skinned
  *NiTriStrips* — no such mesh has been observed yet, but it is the same trap.)

## NIF furniture marker conversion (rewritten 2026-07 — fixed backwards/floating NPCs)
<a id="nif-furniture-marker-conversion"></a>
- Oblivion: `BSFurnitureMarker` (NiExtraData) with FurniturePosition using `orientation` (ushort, milliradians), `position_ref_1`/`position_ref_2` (byte, always equal in practice)
- Skyrim: `BSFurnitureMarkerNode` (inherits BSFurnitureMarker) with FurniturePosition using `heading` (float, radians), `animation_type` (ushort: 1=Sit, 2=Sleep, 4=Lean), `entry_properties` (bitflags: front, behind, right, left, up)
- **CRITICAL SEMANTIC DIFFERENCE**: Oblivion positions are ENTRY POINTS — where the NPC stands on the floor ~51-106 units AWAY from the furniture, one marker per approach direction (a single chair has 3-4). Skyrim positions are the actual SIT/SLEEP spots (hip position), one per physical seat. A 1:1 position copy produces N duplicate seats with inconsistent headings (NPCs sit sideways/backwards) at the wrong place.
- **Conversion** (`_convert_furniture_markers` in nif_converter.py): compute a seat candidate per entry, cluster candidates within 20 units, emit ONE Skyrim position per cluster. Verified to reproduce vanilla marker topology exactly (chair→1 pos front|right|left; bench→3 pos; bed→1 sleep pos right|left).
- **Seat candidate**: sit entries stand a FIXED distance from their seat — 51.5 (side refs 11/12) / 55.0 (front/behind refs 13/14) — walk that far along the approach direction (handles curved benches like anviltreebenchseat01; a bench's side entry is 51.5 from the END seat so it clusters correctly). Sleep entry distances vary per bed (67-106), so instead project the geometry-bbox center onto the approach ray (entries always point across the hip line).
- **Heading** (= direction occupant faces; for sleep = head→feet direction): `heading = orientation/1000 + offset[ref]` where offset = {1: −π/2, 2: +π/2, 3: −π/2, 4: 0, 11: −π/2, 12: +π/2, 13: 0, 14: +π}. 100% consistent across all 48 marker-bearing Oblivion.esm furniture NIFs. The old blanket `+π` rule was only right for ref 14. Ref semantics: 1/11 = occupant's left side, 2/12 = right side, 13 = behind occupant (step over / sit without turning), 14 = in front (approach facing seat, turn, sit), 3 = mat side entry, 4 = mat head-end crawl entry (3/4 verified against sleepingmat01's pillow bump; pillow end = taller z bump, calibrated on Skyrim bedroll01 where the marker proves head=+Y).
- **Z**: entry markers stand ON THE FLOOR in mesh coords (Oblivion furniture origins are at mid-height, so entry z is negative). Skyrim marker z = entry_z + 34.0 (sit) or + 37.0931 (sleep) — the vanilla floor-relative hip heights. All 24 Oblivion bed mattress surfaces lie 36.5-42 above their entry z, so floor+37.09 lands on the mattress. The old `z = -src.z` rule floated NPCs ~34 units in the air (it looked right on chairs only because origin-at-mid-height makes |−z| ≈ seat height by coincidence).
- **Entry flags** are relative to the final heading: flag = side of the seat the entry point lies on (front if (entry−seat)·facing > 0.5, etc.) — NOT a fixed per-ref mapping.
- Oblivion double beds get ONE centered sleep pos (entries converge mid-bed; single and double beds have identical entry spacing ~±91-94 so they cannot be distinguished, and Oblivion's fixed-travel sleep anim landed center-ish too).
- Marker-bearing NIFs live outside meshes/furniture too: clutter/castleinterior (castle beds/thrones), architecture (cathedral pews, tents/sleepingmat, ships/sibed, anvil tree bench), dungeons (benches, thrones, sacrifice altar), oblivion/architecture/citadel. Find them with a binary grep for the ASCII string `BSFurnitureMarker` (block type names are plaintext in NIF headers).
- BSFurnitureMarker lives in root NiNode's extra_data_list. During NiNode→BSFadeNode conversion, it must be explicitly converted and transferred (bulk extra_data_list copy breaks animated objects). Marker offsets are model-space and stay valid under the root-rotation wrap pass.
- **FURN record linkage (CRITICAL)**: TES5 FURN `MNAM` bits 0-23 enable NIF marker POSITION index 0-23. TES4 MNAM bits indexed the Oblivion NIF's ENTRY list — passing the bitmask through after seat clustering leaves dangling bits and the engine seats NPCs at garbage positions FAR from the mesh. The shared algorithm lives in `asset_convert/nif/furniture_markers.py`; `tes5_import` (items.py `load_furniture_seats`, called in import Phase 0e) recomputes the same seat list from the source NIF and writes MNAM=(1<<n_seats)−1 + preserved high bits (0x40000000 sit-type / 0x80000000 bed-type, same in both games; beds add 0x08000000 MustExitToTalk like all vanilla beds) + WBDT(0,-1) + one FNPR per seat.
- **Oblivion entry-restriction variants**: many TES4 FURN records share one NIF and enable different entry-marker subsets (SEChair01F/R/L, 19 LCBench01* variants like `Fall`=front row only, `RL`=ends only). Conversion carries this into per-seat FNPR entry flags: only the entry directions whose TES4 entry bit was enabled are allowed (seats with no enabled entries fall back to all their entries). Verified vs vanilla: converted bench = 0x40000007 + 3×FNPR like CommonBench01; converted bed = 0x88000001 + FNPR 0x000C0002 byte-identical to CommonBed01; LCBed02L keeps right-entry-only (FNPR 0x00040002).
- FURN models whose NIF is missing from the export (SI furniture, palace thrones) get a conservative fallback: MNAM bit 0 + high flags, FNPR all entries. NIFs with NO markers get MNAM high flags only (no active positions — never enable bits beyond the NIF's position count).

## NiControllerSequence conversion
<a id="nif-controller-sequences"></a>

**Code:** `asset_convert/nif/sequences.py`

Oblivion drives in-NIF animation through a `NiControllerManager` holding named
`NiControllerSequence`s. Skyrim keeps the same structure but accepts a much
narrower set of controller types and stores its strings differently, so every
sequence has to be rewritten rather than copied.

The module owns the whole animation half of a NIF: the controller-manager
rewrite, root-accumulation handling, the string-palette resolution, shader
controller binding and retargeting, morph emulation, and the blend-interpolator
normalizing that must follow all of them.

## Accum-root classification
<a id="accum-root-classification"></a>

**Code:** `_accum_root_mode` in `asset_convert/nif/sequences.py`

Oblivion's exporter writes the accum root's controlled block as the
**root-motion placeholder** — an IDENTITY pose — and moves the node's real
transform onto the `<accum> NonAccum` child.

Census of all **464** Oblivion non-creature NIFs with sequences: **853**
accum-root entries, **815** data-less identity poses, **38** with never-varying
keys, and **0 that move**. The real transform turns up on NonAccum either as a
pose (doors) or as key 0 (keyed nodes): `sesacellumgate01`'s NonAccum keys start
at MetalGate's authored (-7, -16.2, 37.9); `bravilloaddoorlowerint01`'s NonAccum
pose is (0, -42.7, 12) with rotation keys starting at the root's 90°.

Both engines apply the identity and NonAccum restores the world pose, so playing
the identity is CORRECT for these — mode **`transferred`** — and the entry must
be left exactly as authored. Sentinelling its rotation, as the generic
data-less rule would, DOUBLES the door's authored rotation.

**The arena spectators are exactly this case.** `Bip01` (the actor rig's 82.5°
Z rotation, 64 units up) has the identity pose, and `Bip01 NonAccum` key 0 is
(-0.34, -1.64, 64.07) at 82.6°. Sentinelling Bip01's rotation left the authored
82.5° in place while NonAccum re-applied its own, so the crowd faced **165° off**
whenever the sequence played — the "rotated 90 degrees" report. Patched in the
live engine (2026-08-18): with Bip01's pose left as the valid identity, Bip01
read back as identity, NonAccum as (-0.34, -1.64, ~64) / 82.5°, and the crowd
sat at its authored pose.

Every one of the **195** non-identity accum roots in Oblivion.esm is
`transferred`. Mode **`orphan`** — nothing carries the transform, so applying
the identity would collapse the node, and every channel is sentinelled instead —
is defensive, for plugins whose exporter did not follow the convention.

`None` means no accum root, or one whose authored transform is identity, where
the pose is a no-op either way.

## Sequence controller retargeting
<a id="sequence-controller-retargeting"></a>

**Code:** `process_controller_manager` in `asset_convert/nif/sequences.py`

A `NiControllerSequence` names its controller TYPE as a string and the engine
instantiates it by name when the sequence loads, so a single Oblivion-only type
fails the WHOLE NIF — the red missing-mesh triangle (`se11sheopooffx`,
`palacefont01`, `se01waitingroomwalls`, `OblivionArchGate01`).

Census of ~8,300 vanilla Skyrim meshes: `NiTextureTransformController` and
`NiAlphaController` appear **zero** times. The types vanilla does use in a
controlled block are `BS*ShaderPropertyFloatController`, `NiPSys*Ctlr`,
`NiTransformController` and `NiVisController`.

Three types are RETARGETED rather than dropped, because the animation CURVE
lives on the sequence entry's own interpolator while the controller block holds
only a keyless blend interpolator:

| Oblivion type | Becomes | Why not drop it |
|---|---|---|
| `NiMaterialColorController` (target_color 3 = emissive) | `BSLightingShaderPropertyColorController` | Deleting it froze the animation at its first key — for `se11sheopooffx`'s Cone01 that is emissive (0,0,0), a large PITCH BLACK cone where an orange force-ripple should pulse over ~13s. |
| `NiTextureTransformController` | `BSLightingShaderPropertyFloatController` | `palacefont01`'s scrolling water is 3 × `NiFloatInterpolator`, 2 keys, V 0 → -2/-4/-1 over 2s. `TT_ROTATE` has no Skyrim equivalent and IS dropped. |
| `NiAlphaController` | `BSLightingShaderPropertyFloatController` | Dropping it froze the fade and left the surface static (`se11sheopooffx`'s GlowPlane pulses 0 → 1 → 0 over 13s). Enum per `references/nif 0.10.0.0.xml`: Lighting var 12 "Alpha", Effect var 5 "Alpha Transparency". |

Each is stamped provisionally as the **Lighting** variant;
`match_seq_shader_types` re-stamps nodes that ended up on the Effect shader
after the geometry walk. The block the entry POINTS AT must be replaced too —
rewriting only the type string leaves the Oblivion block in the file's
block-type table, which is what the engine rejects.

`NiFlipController` is DROPPED: the flip-book is already fully converted
geometry-side into a frame-strip atlas driven by a
`BSEffectShaderPropertyFloatController` on the shader itself (verified on all 5
of `OblivionArchGate01`'s flip nodes), so the sequence entry is a pure
duplicate. A **backstop** drops any other type outside the vanilla set: every
handler is type-by-type, so the next Oblivion-only controller would otherwise
ship broken exactly as `NiFlipController` did. Dropping costs at most one
animation channel; leaving it costs the entire mesh.

### <a id="controlled-block-names"></a>Controlled-block names may live in the palette

Names live EITHER in the bytes field OR, when the sequence carries a
`NiStringPalette`, at an offset into it. Oblivion NIFs written with a palette
leave the bytes field EMPTY, so reading only the bytes returned `''` for every
entry — and the "drop blocks with an empty node name" rule then deleted the
ENTIRE sequence. **16 of 108** sampled animated meshes lost 100% of their
animation this way (candles, light sconces, the gnarl spawner, Cameron's
Paradise bricks). Prefer the bytes, fall back to the palette offset.

### <a id="root-named-blocks-and-mttc-targets"></a>Root-named blocks, and the MTTC target list

A block naming the file root is dropped, AND that node is removed from every
`NiMultiTargetTransformController` extra-target list at the same time. Both
halves are required. Census of 141 sequences across 43 animated vanilla meshes:
**0** controlled blocks target their own root node, and **0** MTTC extra targets
lack a driving controlled block.

`extra_targets` is POSITIONAL — the engine pairs slot N with the entry that
drives it. Leaving the target while removing the block gives that slot a null
interpolator, which `BGSGamebryoSequenceGenerator` dereferences as soon as the
object animates: `movdqu xmm2,[rax]`, rax=0, in VCRUNTIME140
(crash-2026-08-10-00-42-35, `spiddalcloudplant.nif`, whose root `spiddalplant`
is also extra-target #1). Keeping the block instead is equally wrong — it
produces a root-targeting entry vanilla never ships, and crashed in the same
place (crash-2026-08-10-00-51-26).

### <a id="dataless-transform-interpolators"></a>A dataless transform interpolator is sentinelled, not deleted

Deleting these was wrong. The snapping concern is real (a dataless interpolator
whose stored transform is a real value would yank the node there), but vanilla's
answer is a SENTINEL: `volunruudleftswordanimated`'s LeftLockDoor /
LeftRingParentDoor entries keep a real translation and store rotation + scale as
**-FLT_MAX** (-3.4028235e38), telling the engine the channel has no value.

Census of vanilla animated doors and traps: **12** dataless transform entries
are KEPT (96 have data), and **123/123** sequences have at least one controlled
block — vanilla ships NO empty sequence.

Removal emptied whole sequences: `ctrapswingmacelong01` and
`ctrapswingmaceshort01`'s `Unequip` went 2 entries → 0. A sequence with nothing
to bind never runs, so its TEXT KEYS never fire — which is why the swinging
traps made no sound (their visible motion is Havok, not the clip, so the empty
sequence was invisible until the sound went missing). `ctraplogs01` 3→1,
`ctrigpressureplate01` 3→1, `ctrigtripwire01` 6→3.

The sequence's ACCUM ROOT is the exception both ways. `transferred` (NonAccum
carries the node's transform): the exporter's identity pose is what both engines
play, so the entry is left exactly as authored — sentinelling its rotation would
double the door's authored rotation, the arena crowd's "rotated 90 degrees".
`orphan` (nothing carries the transform): sentinel EVERY channel so the node
keeps its authored transform, dropping a keyless `NiTransformData` first so the
sentinel applies.

### <a id="morph-swap-block-mechanics"></a>How the swap is built

The morph target is baked into a hidden sibling shape and the sequence gains
`NiVisController` entries swapping base → target as the weight curve crosses 0.5.
The base shape is visible exactly while NO target weight is at or above 0.5.

**Bool keys MUST be CONST_KEY (5).** **3449/3449** vanilla and **1296/1296**
Oblivion `NiBoolData` blocks store it, and LINEAR crashed the engine in
`NiBoolData::Load`.

**A synthesized `NiVisController` copies the vanilla pattern** from
`sldjailwallcollapse01`: flags 108 (ACTIVE | CLAMP | Compute Scaled Time) over a
`NiBlendBoolInterpolator` whose `bool_value` 2 is vanilla's "no authored value"
sentinel.

**Cloning a block copies fields in declaration order.** `_attrs` is per-class
only — `NiTriStripsData._attrs` holds just `num_strips`/`strip_lengths`/`points`,
while the vertices live on `NiTriBasedGeomData` — so the MRO is walked base-first
and counts still precede their arrays. A single in-order pass with
`update_size()` at each array therefore keeps dimensions valid. Reference-typed
fields are copied as POINTERS (shared blocks); the caller overrides the ones the
clone must own (data, controller, collision).

### <a id="shared-property-fan-out"></a>A shared property drives several shapes

Oblivion shares one `NiTexturingProperty` / `NiMaterialProperty` block between
several shapes, and a sequence entry names only ONE of them. `palacefont01`'s
`Water` entry drives `NiTexturingProperty` #71, which `Water03`, `PalaceWaterL2`
and `PalaceWaterR02` also wear, so in TES4 that one entry scrolls all four — the
fountain's upper tier.

Skyrim gives every converted shape its own `BS*ShaderProperty`, so the retargeted
entry must be FANNED OUT to one entry (and one controller) per sharing shape, or
the siblings stay frozen. That was the upper tier of the Font of Madness after
the first conversion.

The controller and interpolator are cloned per sibling — each shape's own shader
gets its own controller, as vanilla does — while the key DATA is shared. The
index is built once per manager in a single tree walk and looked up per entry;
the private re-stamp markers travel with the clone so `match_seq_shader_types`
can still re-stamp the copies.

### <a id="morph-emulation"></a>Morph controllers are harvested, then dropped

`NiGeomMorpherController` does not exist in Skyrim — the SSE exe has no RTTI
class for it and vanilla ships 0 — so the entry must go. But the morph IS the
visible effect for a whole family of Oblivion meshes (`ctrigtripwire01`'s wire
snap, `se01waitingroomwalls`, the forming Oblivion gate), so everything
`emulate_morphs` needs to rebuild it as a baked target shape plus a
wrapper-node scale swap is harvested first.

## The controlled block is resolved BY STRING at activation
<a id="controlled-block-id-strings"></a>

**Code:** `_normalize_shader_cb_strings`, `match_seq_shader_types`,
`_bind_shader_ctrl_target` in `asset_convert/nif/sequences.py`

The engine resolves a sequence's controlled block at activation time by string:
on node `<node_name>` find the property whose class is `<property_type>`, then its
controller of class `<controller_type>`, using `<variable_1>` (Controller ID) to
pick the channel.

The retargeting rewrites swap the controller block and `controller_type` but the
entry otherwise keeps Oblivion's strings — `property_type` `NiTexturingProperty`
(a class that no longer exists in the file) and controller IDs like
`0-0-TT_TRANSLATE_V`. The lookup fails SILENTLY and the interpolator is never
applied: `palacefont01`'s fountain water shipped with a correct V-Offset curve
that never played.

Vanilla convention (`beehive01`, `blackpool`, `dweastrolabehub01` — every
`BS*ShaderProperty*Controller` entry sampled):

| field | value |
|---|---|
| `property_type` | the shader property's class name |
| `variable_1` | `str(type_of_controlled_variable/color)`, e.g. `'8'`, `'11'` |
| `variable_2` | `''` |

**Effect-vs-Lighting must be reconciled after the geometry walk.**
`process_controller_manager` rewrites the Oblivion entries before the shaders
exist, so it can only assume the Lighting variant. Unlit surfaces
(`lighting_mode` 0, e.g. `palacefont01`'s fountain water) end up on
`BSEffectShaderProperty`, and the two shaders number their controlled variables
differently — V Offset is 22 on Lighting but 8 on Effect — so an unreconciled
entry leaves the engine unable to bind the animation. A Lighting controller is
never bound to an Effect shader or vice versa, for the same reason.

**`NiTimeController.Target` is NOT optional for this family.** Every vanilla
`BS*ShaderProperty{Color,Float}Controller` sampled names its own shader block
(Lighting → `BSLightingShaderProperty`, Effect → `BSEffectShaderProperty`;
**15/15**, 0 nulls). The Oblivion source controllers target the NiTriShape's
*property list*, which has no Skyrim counterpart, so the rebuilt controller was
left with a NULL target — the engine dereferences it while loading the shader
property and faults.

### <a id="geometry-suffix-entries"></a>Entries naming geometry as `<node>:<index>`

Oblivion's exporter names a node's geometry children either as
`Tri <parent> <index>` (a real block name) or, inside a `NiControllerSequence`
string palette, as `<parent>:<index>`. `morroblivionchandilier01`'s Idle sequence
uses BOTH conventions at once:

| node name | controller |
|---|---|
| `CandleSkinny01:0` | `NiMaterialColorController` (emissive) |
| `CandleSkinny01` | `NiTransformController` |
| `CandleSkinny01 NonAccum` | `NiTransformController` |

The last two name real `NiNode`s, so the palette is NOT stale — only the `:0` form
needs translating. It means "geometry child 0 of CandleSkinny01", which after
conversion is the shape carrying the `BSLightingShaderProperty`.

Binding matters because Skyrim dereferences `NiTimeController.Target` while
loading the shader property, so an unbound shader controller is a CTD. Deleting
the entry is NOT an acceptable fix: it costs the chandelier its emissive flicker,
and emptying a sequence strands its `NiControllerManager` with zero sequences —
which the engine also dereferences (vanilla ships no manager with 0 sequences).
The entry is rewritten to name the real block, or the engine cannot re-bind it at
run time.

## The three flags that select a conversion path
<a id="convert-nif-path-flags"></a>

**Code:** `_convert_nif` in `asset_convert/nif/nif_converter.py`

**`worn=True`** marks the NIF as body-worn gear on the plugin's own authority —
an ARMO/CLOT record names it as a biped model, see `wearable_plan.is_worn`. It
only ever WIDENS the armor path: the folder-name guess still applies on its own
for meshes no record references.

**`parallax=True`** carries Oblivion's `APPLY_HILIGHT2` height field across as a
Skyrim slot-3 height map. Off by default — the result needs Community Shaders or
ENB and renders wrong without one.
See [asset_convert_shader.md](asset_convert_shader.md#hilight2-alpha-dropped).

**`creature=True`** selects the creature-asset rules for `skeleton.nif` and the
skinned body parts under `meshes/creatures/`:

- skinned bodies keep a plain `NiNode` root and their plain `NiSkinInstance`
  with ORIGINAL Oblivion bone names — the faithful-port strategy keeps the
  Oblivion skeleton, so there is no retarget and no bone renaming;
- `skeleton.nif` becomes a `BSFadeNode` with BSX=198, the vanilla
  creature-skeleton value, and its ragdoll bhk tree is converted in place on the
  bone nodes, never hoisted to the root.

## Rest visibility: what a node looks like before its animation runs
<a id="rest-visibility"></a>

**Code:** `apply_rest_visibility` in `asset_convert/nif/sequences.py`

Oblivion drives per-node visibility from a `NiVisController` inside a
`NiControllerSequence`. Where that sequence is script-triggered, Skyrim leaves it
unplayed until the script fires — but the NODE still renders, because the engine
only applies the sequence's keys while it is playing. So geometry Oblivion keeps
hidden until mid-effect is visible from the moment the cell loads.

`se11sheopooffx` is the case in point: its `Forward` sequence holds `Cone01` at
visibility 0 until t=0.3 and hides it again at 12.93, and nothing ever plays the
sequence (the STAT has no script), so the cone renders permanently — a large
black cone over the effect. Applying the t=0 value as the node's authored rest
state matches what the object looks like before its animation is triggered, which
is the correct resting appearance in both engines. A node visible at t=0 is
untouched.

**AutoPlay/AutoLoop sequences are skipped.** They RUN from cell load, so their
own keys restore the node's visibility; baking the t=0 value in would hide
geometry the animation is about to show.

**A dataless `NiBoolInterpolator` is a CONSTANT**, not an absence: its
`bool_value` IS the rest state and there are no keys to read.
`OblivionArchGate01` drives every one of its 30+ vis-controlled nodes this way,
so a keys-only path skipped all of them and the meteors and tendrils rendered
from cell load.

**Only scene-graph objects have a "hidden" bit.** `root.tree()` also yields
properties, and bit 0 of `NiAlphaProperty.flags` is ALPHA BLEND ENABLE — setting
it there turned opaque surfaces into additive blends (0x1042 → 0x1043, src=ONE
dst=SRC_COLOR) and they rendered as blown-out green and red. Those blocks also
have an empty name, so a name-only match hits every one of them at once. The
match is therefore gated on `NiAVObject`.

## A sequence entry does not connect its own controller
<a id="seq-shader-controller-attach"></a>

**Code:** `attach_seq_shader_controllers` in `asset_convert/nif/sequences.py`

A `NiControllerSequence` entry only says "while this sequence plays, drive node
N's controller of type T". It does not itself connect the controller to the
property, and Skyrim resolves T against the controllers ALREADY hanging off the
target — so a controller that exists only as a sequence entry drives nothing and
the surface renders frozen. That was `palacefont01`'s fountain water, converted
from Oblivion's `NiTextureTransformController`.

Vanilla never leaves one dangling: across **80** meshes carrying shader float
controllers, **481/481** are reachable from `shader.controller`. The pass mirrors
that — the controller goes on the shader's chain, targeted at the shader.

## Ambient animation: Oblivion's Idle becomes vanilla's AutoPlay pair
<a id="autoplay-ambient-sequences"></a>

**Code:** `autoplay_ambient_sequences`, `_clone_sequence_as` in
`asset_convert/nif/sequences.py`

Oblivion auto-plays a sequence named `Idle` as soon as the object loads. Skyrim
has NO such convention: a `NiControllerSequence` sits idle until something starts
it (a script's `PlayGamebryoAnimation`, an engine-native name like Open/Close, or
the behaviour graph). So converted ambient animation — `palacefont01`'s fountain
water, `se01waitingroomwalls`' light ripples, the arena crowds — simply never ran,
and the surface rendered as a frozen first frame.

Vanilla's self-playing meshes (**63** in Skyrim.esm's BSAs) all point their BGED
at `GenericBehaviors/Autoplay.hkx`, whose state machine STARTS on a state playing
sequence `AutoPlay` (a CLAMP intro; **53/54** vanilla) and, on that sequence's End
event, hands off to a state playing `AutoLoop` (the real motion, cycle type LOOP;
**39/53** vanilla). Looping is the SEQUENCE's own cycle type —
`BGSGamebryoSequenceGenerator` has no looping field (`bLooping` is
SERIALIZE_IGNORED) and the AutoLoop state has no self-transition.

So the authored `Idle` becomes `AutoLoop` and KEEPS its authored cycle type (all
**116** Oblivion `Idle` sequences are `CYCLE_LOOP` = 0), and a CLAMP clone named
`AutoPlay` is added for the start state. Read out of the running engine
(2026-08-18, arena spectator, `tools/live/game_bridge.py`): with AutoLoop written
as CLAMP the graph reached AutoLoopState and froze on the last frame; flipping the
loaded sequence's `cycleType` to LOOP in memory and `sae AutoReset` made it loop
indefinitely.

Reading cycle type 2 (CLAMP) as "loop" is what left every converted ambient mesh
playing exactly one cycle and freezing.

Script-driven names (`Forward`, `SpecialIdle`, …) are left alone — those are
started through the behaviour graph BY NAME, and renaming them would break the
`PlayAnimation()` call that drives them.

**The clone REUSES the original's controlled-block interpolators** rather than
deep-copying the key data: a `NiControllerSequence` only references its
interpolators, two sequences may reference the same ones (verified in the live
engine — both sequences bind the same interpolator pointers and play), and the
keys are by far the largest part of the block. Every declared member is copied by
`_get_names()`, pyffi's own declaration order, because `ControllerLink`'s field
set differs across NIF versions (20.0.0.4 has `variable_1`/`variable_2` where
later ones have `controller_id`/`interpolator_id`), so naming them explicitly
breaks on the next version.

### <a id="script-driven-sequence-names"></a>Which group names a script can drive

TES4 animation GROUP names a script can drive with `playgroup`, which converts to
`ObjectReference.PlayAnimation()`. Census of the converted output (**18,566**
scripts): Forward 418, Backward 192, Unequip 45, Equip 27, SpecialIdle 10,
FastForward 8, Left 6, FastBackward 6, Right 5, Stagger 1.

`Open`/`Close` are deliberately ABSENT. They are the engine's own DOOR group
names, driven natively through the NIF's `NiControllerManager` — no script ever
names them, and giving such a mesh a behaviour graph is what CTD'd
`prisonCellGate01` on cell load (2026-07-26). Vanilla agrees: the graph-driven
`NocturnalsSecretDoor01` uses `AnimIdle01`/`AnimPlay01`, never Open/Close.

## Which sequences earn a behaviour graph
<a id="which-sequences-earn-a-graph"></a>

**Code:** `collect_sequence_names` in `asset_convert/nif/nif_converter.py`

The names collected here become both the graph's state names and the events
that select them, so `PlayAnimation("Forward")` reaches the right sequence.
Four rules decide what qualifies:

- **Only SCRIPT-DRIVEN group names.** A mesh whose sequences are all
  engine-native (`Open`/`Close` on doors) is already animated by the engine and
  must NOT get a graph — attaching one makes the engine bind the sequence
  through the graph instead, and it crashes.
- **`AutoPlay` / `AutoLoop` are kept even though they are not script-driven**,
  because it is the behaviour graph that starts them: **63/63** vanilla AutoPlay
  meshes carry a BGED, confirmed against the arena crowd's graph read out of the
  live engine.
- **A sequence stripped to nothing by `process_controller_manager` is skipped.**
  It animates no node, and giving it a state would make `PlayAnimation()`
  succeed on a dead sequence.
- **No controller manager means no names at all.** A static mesh needs no graph
  and must not get a BGED.

## Post-walk animation passes
<a id="post-walk-animation-passes"></a>

**Code:** `_run_animation_passes` in `asset_convert/nif/nif_converter.py`

Six passes run after the geometry walk, and the ORDER is a contract:

| # | Pass | Why it sits here |
|---|---|---|
| 1 | `match_seq_shader_types` | Reconciles retargeted UV controllers with the shader each target node actually received; Lighting and Effect number their variables differently. Must follow the walk. |
| 2 | `emulate_morphs` | Rebuilds dropped `NiGeomMorpherController` animation (Skyrim has no morph class) as baked target shapes plus `NiVisController` swaps. Clones copy CONVERTED shapes and shaders, so it follows the walk and precedes rest visibility and sequence-name collection. |
| 3 | `autoplay_ambient_sequences` | Oblivion's auto-started `Idle` becomes vanilla's `AutoPlay` (CLAMP intro) + `AutoLoop` (the authored loop) pair, or the animation never starts. Precedes `collect_sequence_names` so the behaviour graph is built from the final names. |
| 4 | `apply_rest_visibility` | Nodes a sequence keeps invisible at t=0 must ship hidden: Skyrim applies a sequence's keys only while it plays, so mid-effect-only geometry otherwise renders from cell load — `se11sheopooffx`'s black cone. |
| 5 | `attach_seq_shader_controllers` | A shader controller that lives ONLY as a sequence entry drives nothing; vanilla always hangs it off the shader too (**481/481**). Runs after pass 1 so the final controller object is the one attached. |
| 6 | `normalize_blend_interpolators` | Stamps vanilla's manager-driven header onto every blend interpolator, synthesized or copied. Must follow every pass that can create or replace one. |

## The root transform wrapper
<a id="root-rotation-wrapper"></a>

**Code:** `_wrap_root_transform` in `asset_convert/nif/nif_converter.py`

Skyrim **ignores BSFadeNode root-node rotation** for static placement but
applies a child NiNode's rotation correctly. So a non-skinned model whose root
carries a rotation gets an inner NiNode holding that rotation and translation,
and the root's own transform is zeroed.

The collision object **stays on the root**: a `bhkCollisionObject` on a child
NiNode intermittently crashes `hkpCollisionDispatcher`. Instead the rigid body
absorbs the transform that is about to vanish, because the engine places a root
collision body at `REFR ∘ bodyT` while Oblivion applied `REFR ∘ L ∘ bodyT`.
Without that composition the collision is rotated relative to the mesh —
measured on `stackhallentrance01`, which came out 90° off.

Furniture re-origin rides the same wrapper: marker-bearing models are
translated by `furn_shift` so the floor plane sits at z=0, the vanilla origin
convention (the engine anchors seated actors to the REFR z). The importer lowers
the REFRs of every base record using the model by the same amount, so
world-space visuals are unchanged. The shift is absorbed into the rigid body
along with the rotation.

#### <a id="master-owned-furniture"></a>The REFR half must index the MASTERS

The mesh half and the REFR half are one contract: lift the model, lower the
refs. `load_furniture_models` originally scanned only the plugin's own
`meshes/` and keyed `_BASE_ORIGIN_SHIFT` off its own `by_type`, so a plugin
that merely PLACES a master's furniture got neither — the mesh rose and the
refs stayed. Measured on Tamriel Rebuilt (masters Morrowind_ob.esm,
Morrowind-Morroblivion-Compatibility.esp, Tamriel_Data.esm): TR owns 0 marker
models and 0 FURN records, while Morroblivion owns 87 marker models. Three
placed refs floated by exactly their model's shift — `furnucomustoolu02`
+16.19, `furnucomubenchu02` +18.80, `activeudeubedu03` +55.00.

So both halves index the masters: `master_mesh_dirs(ctx)` adds each master's
source mesh tree (the plugin's own copy of a shared path wins), and the base
sweep walks `ctx.master_export` before `by_type`. 🛑 A master's record is keyed
on its **`master_export` KEY**, never `rec['FormID']` — that field is in the
master's own index space
(see [pipeline](tes5_import_pipeline.md#phase-0-master-key-not-formid)).

#### <a id="furniture-shift-third-consumer"></a>The navmesh is the THIRD consumer of the shift

The furniture contract has three sides, not two. `f1194fe` fixed the first two
— lift the model in the NIF, lower the REFR in the ESM — but
`tes5_import/navmesh/world.py` gathered collision at the **raw `PosZ`** and
never called `get_base_origin_shift`. The navmesh was therefore built against
furniture floating above where the engine actually places it.

Measured over `export/Oblivion.esm` (186 FURN + 6,014 STAT records, 85 marker
models): **221 base records carry a shift**, range **−47.99 .. +126.76**,
median **36.18**. Affected placements in the navmesh test cells:

| cell | floating refs | max shift |
|---|---|---|
| BrumaCastleGreatHall | 23 / 335 | 61.02 |
| BrumaChapelHall | 14 / 212 | 47.43 |
| AnvilFightersGuild | 5 / 125 | 33.91 |
| ImperialDungeon01 | 1 / 453 | 17.78 |
| BrumaChapelUndercroft | 0 / 101 | — |

A bench whose collision floats 47u is wrong twice over: its walkable top is a
surface the generator sees at head height, and its blocking volume is missing
from where the real bench stands.

The shift is along the model's **local** Z, so under rotation it is a vector on
all three axes — which is why both consumers call the one
`record_types/world.py::shifted_position` rather than each subtracting from Z.

Two coupling notes:

- The shift table lives in `record_types/items._BASE_ORIGIN_SHIFT` and is
  populated by `load_furniture_models`. Anything gathering collision outside
  the pipeline (`tools/navmesh/index.py`) has to build it too, or it silently
  sees the old floating geometry.
- Changing navmesh output invalidates the shared cache tag (a SHA-1 over
  `tes5_import/navmesh/*.py`), so the cache needs republishing.

### <a id="root-named-controlled-blocks"></a>Root-named controlled blocks are stripped

A NiControllerManager on the BSFadeNode root may hold controlled blocks
targeting the root by name (`X`, `X NonAccum`). In Oblivion that drives the
accumulation system for characters and is a no-op for statics. In Skyrim the
blocks are applied **literally**, so a rotation animation on the root spins the
whole object in world space — the `stonewallgatedoor01` "spinning" bug.

`process_controller_manager` strips blocks named after the node, drops
`NiMaterialColorController` / `NiGeomMorpherController`, and handles
zero-interpolator data.

## Second links to replaced geometry
<a id="second-links-to-replaced-geometry"></a>

**Code:** `_remap_replaced_blocks` in `asset_convert/nif/nif_converter.py`

Two structures reference geometry through a link OUTSIDE the children arrays
the tree walk rewrites, so both still name the orphaned `NiTriStrips` after a
shape is converted:

- `NiDefaultAVObjectPalette` entries, whose `av_object` names the old block.
- `NiPSysMeshEmitter.emitter_meshes`, which reaches its source geometry through
  a second link.

pyffi then re-serialises the orphan, because it is still reachable, leaving raw
Oblivion `NiTriStrips` in a Skyrim file. Skyrim has no NiTriStrips renderer —
vanilla is **107/107 NiTriShape** across all **256** `NiPSysMeshEmitter` meshes
— so the engine fails the whole NIF and draws the red missing-mesh triangle
(`se11sheopooffx`, `se01waitingroomwalls`, `palacefont01`).

## Helper geometry must not draw
<a id="helper-geometry-must-not-draw"></a>

**Code:** `_hide_helper_geometry` in `asset_convert/nif/nif_converter.py`

Oblivion ships invisible helper volumes — particle emitter sources, spawn
volumes, effect proxies — that Skyrim would otherwise render as solid
untextured boxes over the effect. Two passes catch them.

**Emitter source shapes** exist only to define where particles spawn. Oblivion
hides them with `NiMaterialProperty.alpha = 0.0`; Skyrim has no material
property, and the conversion forces `NIF_FLAGS` (visible) onto every node, so
they came through as `se11sheopooffx`'s white blobs. Vanilla census of **119**
emitter-source shapes across 80 particle meshes: **114** set the node's HIDDEN
flag (bit 0) **and** carry NO shader property at all — so both are matched,
which also drops the pointless texture payload.

**Lit geometry with no UVs is unrenderable.** `BSLightingShaderProperty` ALWAYS
samples a diffuse texcoord and reads the tangent basis for its normal map, but
geometry with `num_uv_sets == 0` ships neither stream, so the shader samples
whatever follows the vertex buffer — `OblivionArchGate01`'s red triangle.
Vanilla census (373 shapes): **ZERO** pair a lighting shader with 0 UV sets; the
54 UV-less vanilla shapes are either `BSEffectShaderProperty` (45 — that shader
needs no tangents) or carry no shader at all (9).

These are the helpers the emitter pass cannot see: they reach the shape through
some path other than `NiPSysMeshEmitter`, or nothing references them at all.
Geometry genuinely meant to be drawn always has UVs, so the rule can only ever
catch helpers.

## Strip-format skin partitions
<a id="strip-format-skin-partitions"></a>

**Code:** `_destripify_skin_partitions` in
`asset_convert/nif/nif_converter.py`

A `NiSkinPartition` can store its geometry as either STRIPS or TRIANGLES.
Oblivion writes strips; Skyrim's renderer reads the **partition**, not the
`NiTriShapeData`, to draw a skinned shape, so a strip-format partition gives it
no triangles at all and the shape renders as the red missing-geometry marker.
Census: **678/678** vanilla skin partitions across 350 sampled meshes store
triangles, **zero** store strips.

The strips→triangles conversion in the tree walk rebuilds `NiTriShapeData` but
does NOT touch the partition, and the two regeneration passes that would fix it
are gated on mesh CATEGORY (creature, worn armor). Anything else that happens to
be skinned — self-skinned clutter such as rope, chain, banner and hanging
bucket, effect meshes, odd creature parts outside the creature path — kept its
Oblivion strip partition and broke. Found via
`dungeons\chargen\ropebucket01.nif` (red triangle in game); a sweep of 500
converted meshes found **93** such partitions across 6+ unrelated meshes, so
this is a general class rather than one file.

It runs after every category-specific pass — those set up bones and bind poses
and regenerate correctly on their own — and only rewrites what is still in strip
format, leaving their triangle partitions alone.

## Inventory-marker orientation
<a id="inventory-marker-orientation"></a>

**Code:** `finalise_inv_markers` in `asset_convert/character/equipment_rig.py`

Weapons and shields sit in Skyrim's normalized attachment frames — the Prn node
convention and the SHIELD attach transform — so the vanilla-derived constant
markers written earlier are already exact and are left alone.

Everything else that can appear in the inventory (armor and clothes `_gnd`
models, clutter, books, ingredients, keys, soul gems) is still in an arbitrary
Oblivion modelling frame, where a fixed rotation shows a random side. Those get
a rotation computed from the FINISHED geometry — after root wrapping and any
furniture shift — so the side showing the most mesh faces the inventory camera.
Meshes never viewed in an inventory simply carry an inert extra-data block.

A skinned non-equipment mesh is skipped: it poses through its bones rather than
its node transforms, so geometry analysis would misjudge it.

### No source game authors this — computing it is not a fallback
<a id="no-authored-inventory-orientation"></a>

`BSInvMarker` is `versions="#SKY_AND_LATER#"` in `references/nifxml/nif.xml`: the
block does not exist in the TES4 or FO3/FNV formats, so there is no authored
rotation to prefer for ANY source game. Measured: 0 of 15,013
`export/FalloutNV.esm/meshes` NIFs contain the string `BSInvMarker`, against
1,499 of 17,216 in `references/Skyrim Meshes` — the same scan, so the zero is
real and not a broken query. Neither does the record side carry one: the FNV
`WEAP`/`ARMO` definitions in `references/xEdit/Core/wbDefinitionsFNV.pas` have
no rotation, zoom, pitch or yaw field anywhere.

The reason is that Fallout 3 and New Vegas have no 3D inventory viewer at all —
the Pip-Boy lists items as flat `MICN`/`ICON` 2D icons, which need no
orientation. Skyrim's rotating 3D preview is a new feature with no predecessor
data, so geometry analysis is the ONLY source for the value. Do not add an
"honor the authored marker" branch for FNV; there is nothing for it to read.

## Dangling back-references after the root swap
<a id="dangling-root-back-references"></a>

**Code:** `_repoint_root_refs` in `asset_convert/nif/nif_converter.py`

When a NiNode root becomes a `BSFadeNode`, the old node leaves `data.roots` and
is no longer reachable — so pyffi writes every surviving reference to it as
null (-1), and Skyrim null-derefs on load. Three kinds of link point backwards
at a root and all must be moved:

- **Controller targets.** `NiControllerManager` and
  `NiMultiTargetTransformController` both store a back-reference to their
  controlled node in `.target`, and Skyrim uses the manager's target as the root
  for animated-node lookup. `NiMultiTargetTransformController` additionally keeps
  an `extra_targets` array that may name the old root as well.
- **`NiDefaultAVObjectPalette` entries**, whose `av_object` may be the old root.
- **`NiSkinInstance.skeleton_root`.** A skinned shape names the node its bone
  transforms are relative to, and on a self-skinned clutter mesh — rope, chain,
  banner, hanging bucket — that node IS the root. Left dangling, Skyrim cannot
  resolve the skin's frame of reference and the shape renders as the red
  missing-geometry marker; seen on `dungeons\chargen\ropebucket01.nif`, whose
  two BucketRope shapes are skinned to the `c_BucketBone` chain.

Extra data is copied SELECTIVELY onto the new root rather than in bulk: a bulk
copy breaks animated objects (the throne NIF's controller refs). What is carried
across is `BSBound`, converted furniture markers, and `Prn`.

`BSBound` is "Bethesda-specific collision bounding box for skeletons" (nif.xml).
The engine uses it as the actor's physical bounds, so a creature skeleton
without one has nothing for the ragdoll/death handoff to land on. Oblivion
creature skeletons ship one (named `BBX`) and **35/39** vanilla Skyrim creature
skeletons have one — but the selective copy originally omitted it, so all **44**
converted creature skeletons lost it at the swap (2026-08-08). Its values are
already in NIF object space, not Havok space, so they carry over verbatim.

## Billboard and geometry roots
<a id="billboard-roots"></a>

**Code:** `_normalize_billboard_root`, `_wrap_geometry_root` in
`asset_convert/nif/nif_converter.py`

**A `NiBillboardNode` root re-orients its ENTIRE subtree to face the camera
every frame.** For a pure billboard sprite that is fine, but Oblivion's
fire and effect NIFs put the particle-system emitters under the billboard root
too, and the spinning transform scrambles world-space particle emission — the
system renders nowhere, which is the invisible-flames bug. Vanilla Skyrim keeps
particle emitters under a PLAIN node.

So a billboard root whose subtree contains any `NiParticleSystem` is demoted to
a plain NiNode (individual particles self-billboard, and static effect quads
keep a fixed orientation, which is acceptable). Any other billboard root is
simply wrapped so it can become a `BSFadeNode`.

Two details of the demotion are load-bearing:

- **The replacement's rotation is IDENTITY, not the billboard's.** A
  `NiBillboardNode` discards its own rotation at runtime (NifSkope
  `BillboardNode::viewTrans`), so copying it onto the plain replacement revives
  a value the engine never used and skews the whole subtree.
- **Direct geometry children are re-wrapped in child billboards.** The root must
  not billboard, but the flat fire QUADS still need to face the camera — a
  fixed-facing quad is edge-on or backfacing from most angles, which is why
  fires looked invisible. Vanilla does the same:
  `campfire01burning` is `BSFadeNode → NiBillboardNode "Plane05" → NiTriShape`.

**A bare geometry root is wrapped in a NiNode.** A few Oblivion-era meshes are
authored with a `NiTriShape`/`NiTriStrips` as the ROOT block. Skyrim never ships
one — a 400-mesh vanilla census found **0** geometry roots (BSFadeNode 340,
NiNode 55, BSMasterParticleSystem 2, BSLeafAnimNode 3) — and anything walking
the tree as a node scene graph breaks: LODGenx64 hard-crashes with "Unable to
cast NiTriShape to NiNode" and abandons the **entire worldspace's** object LOD,
not just the offending mesh. The geometry keeps its own transform, so the wrap
is visually identity.

## Animated-object behaviour graphs
<a id="animated-object-graphs"></a>

**Code:** `_build_animobject_graph` in `asset_convert/nif/nif_converter.py`

Skyrim will not drive an in-NIF `NiControllerSequence` from
`ObjectReference.PlayAnimation()`. That call needs an animation graph manager,
which exists only when the root carries a `BSBehaviorGraphExtraData` naming an
hkx project. So an activator, door or lever gets a four-file
project/character/skeleton/behavior tree written beside the mesh, with one
`BGSGamebryoSequenceGenerator` state per surviving sequence, and the BGED
points at it.

Ordering matters twice: the pass runs **after** the conversion, so sequences
stripped to nothing cannot become dead states that `PlayAnimation` would
happily select, and **before** the write, so the BGED ships inside the file.

**A graph-bound mesh must ship no empty text keys.** The generator `strchr()`s
every key value on activation and an empty `NiString` loads as a NULL pointer —
the Spiddal Stick / Harrada crash. They are stripped before project generation
so the rule holds even if hkxcmd later fails and the BGED is skipped: a
graph-less mesh with fewer dead keys loses nothing.

A missing or failing hkxcmd never loses the mesh. The object still converts and
renders; it just stays unanimated, and the error is recorded in the result.

### <a id="bged-clears-bsx-bit-80"></a>A BGED forces BSXFlags bit 0x80 CLEAR

Attaching the graph also rewrites the root's BSXFlags: the Animated bit goes ON
(or the engine never ticks the graph) and **bit 0x80 goes OFF**.

0x80 marks the object as articulated / ragdoll-driven. Paired with a BGED the
engine waits on a physics rig that a Gamebryo-sequence graph never provides, and
**NEVER DRAWS THE MESH** — invisible in game, perfect in NifSkope, which does not
load the hkx at all.

Census of all **217** vanilla animated-object meshes that carry a BGED: **0 set
bit 0x80** (values 0x4–0x20; the graph-driven `NocturnalsSecretDoor01` is 0x0B).
The converter's longstanding `BSX_FLAGS_ANIMATED` is 0x8B, which is correct for a
mesh with NO graph — `prisonCellGate01` renders fine with it — so the illegal
combination only ever appears where the BGED is added.

`controls_base_skeleton` is 0: the graph drives this object only, not a shared
base skeleton.

## Nodes the walk drops
<a id="nodes-stripped-by-name"></a>

**Code:** `walk_node` in `asset_convert/nif/nif_converter.py`

Four kinds of node never reach the output.

**`SecretBigger*` / `Secret Bigger*`.** Oblivion artists placed tiny 3-vertex
triangles far below the model origin (e.g. Z = -1725) to artificially expand the
bounding sphere so the mesh loads from further away. Skyrim's `BSFadeNode` uses
a different LOD system and does not need the trick; in converted output those
triangles appear as visible floating geometry underground — the "mispositioned"
visual bug.

**`EditorMarker*`.** Editor-only marker meshes, such as the pyramid inside fire
NIFs, hidden at runtime through the node's hidden flag. Conversion clobbers node
flags with `NIF_FLAGS` (visible), so the marker would show in game as an
untextured black shape. Vanilla Skyrim NIFs carry no editor markers in these
objects.

**Every `NiDynamicEffect` subtype.** `NiTextureEffect` (projected-texture
environment mapping) has a completely different rendering path in Skyrim, and
`Ni*Light` blocks — Ambient, Directional, Point, Spot — are 3ds Max export
leftovers: **zero** vanilla Skyrim meshes contain any `Ni*Light` block
(`nif_block_scan`, 2026-07-18), and SSE fails to load a static that carries one.
`statuegodszenithar01.nif`, with a `NiAmbientLight` child, rendered as the
missing-model red triangle. Skyrim lighting comes from placed LIGH references,
never from mesh-embedded light nodes, so there is nothing to convert these into.
The root's own effects array is cleared during the NiNode→BSFadeNode conversion;
this branch handles dynamic effects sitting in a children array.

**Shapes with no reconstructible topology.** Dev-era Oblivion shapes with no
triangle data in the file at all (minotaur `hair01`, `hornsa`, `minotaurold` —
`has_triangles=False` with a non-grass UV layout). Nothing can render them, so
the shape is dropped rather than failing the whole file or creature.

`NiParticleSystem` is converted rather than dropped: `NiPSysData`'s binary
layout differs between UV2=11 and UV2=83, so the data block is replaced with a
fresh instance and the shader properties converted, which is what avoids
"Block size check failed" on load.

## BSXFlags value selection
<a id="bsxflags-value-selection"></a>

**Code:** `_bsx_value` in `asset_convert/nif/nif_converter.py`

A root gets BSXFlags when the tree has collision anywhere, or is animated
(particles or time controllers). The value is chosen in priority order:

| Case | Value | Constant |
|---|---|---|
| constrained dynamic (signs) | 0xCA | `BSX_FLAGS_CONSTRAINED` |
| animated (doors, activators) | 0x8B | `BSX_FLAGS_ANIMATED` |
| dynamic clutter (mass > 0) | 0xC2 | `BSX_FLAGS_DYNAMIC` |
| static | 0x82 | `BSX_FLAGS_STATIC` |

Bit 0 (Animated) is OR'd in whenever the tree has particle systems or time
controllers, so the engine ticks them: 0x82→0x83 and 0xC2→0xC3 both appear in
the vanilla census.

With no collision at all, an animated tree gets plain **0x01**, the most common
vanilla value for collisionless particle meshes — except an ambient
AutoPlay/AutoLoop mesh (the arena crowd, the fountain), which takes the
animated-object value like a converted door.

**The DYNAMIC bit (0x40) is critical for any object with mass > 0.** Without it
Skyrim uses a coarse bounding sphere for the activation and grab shell instead
of the actual collision shape, and applies extra drag while the object is
carried.

A `BSInvMarker` must stay first in the extra-data list, so BSXFlags is inserted
immediately after it.

## Geometry preparation
<a id="geometry-preparation"></a>

**Code:** `_prepare_geometry_data` in `asset_convert/nif/geometry_shader.py`

Four repairs run on the mesh data before any shader exists.

**The AUTHORED hidden bit is carried across.** Oblivion hides helper geometry —
particle emitter sources, spawn volumes, effect proxies — with bit 0 of the node
flags. Overwriting flags wholesale with `NIF_FLAGS` un-hides all of it, so the
helper renders in game as an untextured shard; that geometry carries no UVs, so
a lighting shader over it samples an absent texcoord stream, which is the
`OblivionArchGate01` "red triangle". Bit 0 means the same thing in both games,
so it is copied rather than re-derived.

**Unflagged triangle arrays are raised; absent ones are rebuilt.**
<a id="absent-triangle-arrays"></a> Some vanilla Oblivion meshes — grass blades
in particular — ship `NiTriShapeData` with `has_triangles=False`. The earlier
belief that the index array was missing was wrong: in
`plants/groundcovermediumgrass01.nif` the 10 triangles sit in the file right
after the clear flag (`0a00 1e000000 00` then `0,1,2, 3,4,5, ...`), pyffi reads
them, and Oblivion renders them. What is missing is the FLAG, and a mesh
written with it clear ships no index array, which Skyrim's grass planter
dereferences and CTDs on. `fix_missing_triangles` therefore raises the flag
when the array is populated and reconstructs blades from the UV roles only when
it is genuinely empty. The Morrowind refactor had switched the test from the
flag to array emptiness (the flag reads False on every pre-10.1 mesh), which
silently stopped both paths for these Oblivion meshes: `has_triangles` stayed
False, the writer dropped the triangles, and the grass CTD returned on every
fresh build (caught by `tests/test_grass_landscape.py::test_triangle_reconstruction`).
Legacy vertex match groups likewise appear on several Oblivion meshes and on
no vanilla Skyrim mesh, so they are dropped.

**`ExtraVectorsFlags` is reset to 0.** Skyrim accepts only 0 (none) or 16 (has
binormal + tangent). Oblivion NIFs may store 1, binormals-only, which is invalid
in Skyrim and triggers a pyffi enum warning that can corrupt the tangent data.
`_set_tangents` raises it to 16 when real tangent data is available.

### <a id="one-uv-set"></a>Skyrim reads exactly ONE UV set

On disk the UV-set count shares a u16 "BS Data Flags" whose low 6 bits hold it
(pyffi splits this into `num_uv_sets` + `extra_vectors_flags`). That count is
the ONLY thing telling the engine how many TexCoord arrays follow, so a file
storing 2 sets while the shader binds 1 **overruns the vertex buffer it sized** —
a non-temporal memcpy off the end of the allocation (`vmovntdq`, CTD on cell
load).

Oblivion authors a second set for detail and overlay passes that Skyrim has no
slot for. Census: **2,233** vanilla shapes are 0 or 1 UV sets, **never 2**.

## Pre-upgrade source fixups
<a id="pre-upgrade-source-fixups"></a>

**Code:** `_run_source_fixups` in `asset_convert/nif/nif_converter.py`

Four repairs run on the source tree before `data.version` is raised, because
raising it changes how pyffi reads the file.

**String-palette offsets MUST resolve first.** In Oblivion format (UV2=11) a
`NiControllerSequence`'s controlled blocks store `node_name` and friends as
integer offsets into a `NiStringPalette`. Once the version is the Skyrim one,
pyffi switches to direct-string mode and ignores the offsets, leaving every
`node_name` as `b''`. Skyrim uses `node_name` to look up animation targets, so
empty names become null and the NIF crashes on load.

**Oblivion `sound: X` text keys are NATIVE in Skyrim** and must survive
verbatim; rewriting them to `SoundPlay.*` silenced every animated gate.

**Oblivion never sets `NiTimeController` "Compute Scaled Time" (0x40)**, which
Skyrim requires — without it a sequence started by `PlayAnimation()` binds but
never advances.

**Non-finite geometry is fixed before anything can propagate it** into a tangent
computation or a skin retarget. Orphaned non-scene-graph roots are dropped first
of all, so nothing later walks them.

## Geometry sanitising
<a id="geometry-sanitising"></a>

**Code:** `asset_convert/nif/geometry_sanitize.py`

Two authored defects that Oblivion tolerates and the Skyrim-side tools do not.
Both are repaired before the mesh ships, and the pass returns how many
components it touched.

**Non-finite (NaN) mesh data.** A handful of Oblivion sources ship it:
`anvildooruc02.nif` has 9 NaN UVs and `middlecandlestickfloor03fake.nif` has 2
— one mesh in each of the AnvilMagesGuild / AnvilCastlePrivateQuarters cells,
whose loads crashed with **no crash log**. Oblivion's renderer tolerated
non-finite mesh data; Skyrim SE dies at cell load.

The repairs are chosen so the mesh degrades locally rather than globally:

| Component | Repair |
|---|---|
| UV | zeroed |
| vertex | moved to the mesh's finite centroid |
| normal / tangent / bitangent | +Z |
| vertex color channel | 1.0 |
| bound sphere | recomputed after the vertices are fixed |

A bad vertex goes to the centroid rather than the origin because that
*collapses* the offending triangle instead of stretching it across the model.

### <a id="shapes-that-declare-no-vertices"></a>A shape that declares vertices and ships none

`LeyawiinLowerDoor01` in `leyawiinhouselower01.nif` is the measured case:
`num_vertices=16`, `has_vertices=False`, yet normals, colors, UVs and 6
triangles all still index 16 of them. Oblivion tolerates it — there is nothing
to draw, so it draws nothing — but anything that walks the faces and reaches
for a vertex does not.

LODGen is what found it: `RemoveUnseenFaces` indexes straight into the empty
list, throws `ArgumentOutOfRangeException`, and the run ends with **exit 548 and
no .bto tiles** — one broken shape costs an entire worldspace its object LOD.
Measured at **1 of Nehrim's 1552 `_far.nif`**.

The shape is cleared rather than repaired: without vertex positions the
triangles have no geometry to describe, and the shape already drew nothing, so
it loses nothing.

**Both geometry layouts must be cleared, and the strips are the one that
bites.** The measured case is `NiTriStripsData`, which has no `triangles` array
at all — it stores STRIPS. A first version cleared only `triangles`, so the
strips survived, the strips-to-triangles conversion downstream turned them back
into 6 triangles, and the shape shipped with zero vertices and six faces
indexing vertex 15. LODGen happened to tolerate that; the next tool would not.

## NIF analyzer tools
<a id="nif-analyzer-tools"></a>
- `python tools/nif/nif_analyzer.py <nif_or_dir> [--outdir temp/analysis] [--max N]` — Dumps NIF structure to human-readable text (includes furniture marker positions/refs/orientations)
- `python tools/nif/nif_analyzer.py <nif_or_dir> --bbox` — Prints world-space geometry bounding boxes (per-block + total, all transforms applied) to stdout; use to find mesh origins, floor levels, pillow bumps, etc.
- `tools/nif/nif_analyzer.py` handles BOTH versions (PyFFI dispatches on version); the `tes5_` re-export shim was removed 2026-08-25
- Useful for diff-based comparison between Oblivion, converted, and Skyrim reference NIFs

## SpeedTree (.spt) conversion
<a id="speedtree-conversion"></a>

> 🛑 **GROUND TRUTH IS `Oblivion.exe`, NOT the billboards.** The game statically
> links SpeedTreeRT 4.x with symbols intact — the RNG, the child-placement
> rules, the spline evaluator and the level struct are all decompiled in
> **[speedtree_engine_decomp.md](asset_convert_speedtree.md)**. Read that
> before changing `spt_generator.py`. The "compare against the billboards"
> advice below is SUPERSEDED for anything structural: the generator was already
> fitted to those images, so an A/B can never reveal a 3D error.
> Known-wrong today: golden-angle azimuth (engine uses `uniform(-180,180)`),
> the `MAX_STEMS_PER_LEVEL` caps (engine uses a smooth per-level density
> falloff), and the crown-shell culls.

**Real procedural, rewritten 2026-07-05 — replaces the asset-matching hack**: `asset_convert/speedtree/spt_parser.py` + `spt_generator.py` + `spt_converter.py` decode the Oblivion SpeedTreeCAD-4.x `.spt` binary and bake procedural tree geometry directly into a Skyrim NIF that matches the Oblivion tree's silhouette. `python -m asset_convert.speedtree.spt_converter <trees_src> <nif_dst> [--export-dir <dir>]`. The old `assets/speedtrees/` asset-matching + `_spt_to_skyblivion` is GONE (those were custom Skyblivion creations, not real conversions).

- **`.spt` format** is documented in `references/spttools-master/FORMAT` (GPL sptparser reference). It's a flat stream of `<int32 section_id><payload>` chunks. `spt_parser.py::parse_spt` consumes EVERY section (strict — unknown id raises) into an `SptTree`: levels (trunk=0, branch levels, leaves=last; count in section 1014), shape curves as ASCII "BezierSpline" strings (section 6000-6017), leaf maps (4003 texture / 4005 size / 4004 origin), composite-map UV quads (section 10002), collision primitives (12002/3/4), floor, flares, roughness. Parses 113/113 Oblivion.esm SPTs byte-exact, and 547/547 across every exported plugin (see the newer-CAD note below).
- **BezierSpline** (`spt_parser.BezierSpline`): header `lo hi variance`, then control points `x y tan_u tan_v tan_weight`. `eval(x)` = `lo + curve_y(x)*(hi-lo)` where x∈[0,1] is position along the parent. Constant params have lo==hi. `eval_var` adds the stored ±variance.
- **Scale**: world_units = `stored_value * Size * 10` (`WORLD_SCALE=10`). Verified against the TREE records' billboard heights (`textures/trees/billboards/<stem>.dds` are the ENGINE'S OWN renders — the definitive ground truth; decode them for A/B comparison) — median generated/actual height ratio ≈ 1.0.
- **Generation model** (`spt_generator.build_tree`): recursive stems. Child count per parent = `parent.child_freq * parent.stored_length` (250*0.05=12 on deadbush, 80*0.6=48 on oak). Children spawn in the `[child_first, child_last]` window; SHAPE curves (length/radius/start-angle/gravity/flexibility) evaluate at `x_rel` = position WITHIN the window (NOT absolute parent position — cottonwood forks its whole fan inside the trunk's [0,0.1] window). Start angle = degrees from parent axis. Azimuth = golden-angle spiral + jitter.
- **Gravity semantics** (revised 2026-07-10 after in-game feedback — an earlier "target pitch = 90°−|g−1|·90°" model bent cottonwood's fork limbs DOWN toward horizontal into a wide "wing" the billboard doesn't show): the value sets a bend DIRECTION and RATE — **0<g≤1 bends toward straight UP at rate g** (limbs spread at their start angle near the base then grow back vertical — cottonwood forks g 0.2-0.4, dogwood g 0.25-0.6; every normal trunk stores g=1 = stay vertical), **g>1 wraps over and bends toward the GROUND at rate g−1** (forsythia canes g=3 flop; willow branches store 2..4), g=0 = no influence (redwood, Camoran-paradise trunks — they wander on disturbance alone). The rate is scaled by the FLEXIBILITY value (6002) × GRAVITY PROFILE (6017 — starts at 0.5 at the base, so limbs curve from the moment they fork). Do NOT gate it by the flexibility PROFILE (6003): that ramp is 0 at the base, which left cottonwood's 60°-spread forks lying on their sides for their whole lower half. Willow branches (gravity 2-4, flex 0) HOLD their start angle — the weeping look is the leaf curtains, not the branches.
- **Weeping willow drape**: leaf-LEVEL gravity (section 6001 on the last level) = 90 means leaves hang straight down as long curtains. Modelled as vertical STRANDS of 4 stacked leaf cards reaching ~32% of tree height below each attachment — the only way to reproduce the solid teardrop crown that hangs far below the branches. Ordinary leaves (leaf gravity 0) get one card.
- **Leaf cards**: size = section 4005 * Size (NOT section 4006 — that's the pre-multiplied product but it's STALE in ~15 shrubs, e.g. buckthorn stores 0.08 where 4005*Size=3.6). Two crossed quads. UVs come from the composite-map quad (section 10002) cropping the shipped composite leaf DDS — which is the TREE record's ICON field, resolved at convert time (`_resolve_leaf_tex`).
- **EVERY leaf texture reference must be resolved through `tex_idx` — the SPT names the artist's .tga, not the shipped .dds (fixed 2026-07-27, `dementiatree10` missing leaves)**: `build_tree_nif` had two paths. The composite path (`g['texture'] == '__composite__'`) went through `_resolve_leaf_tex`, which validates `stem in tex_idx` and so can only ever emit a real file. The **per-map else-branch built `LEAF_TEX_DIR + stem + '.dds'` straight from the SPT string with no validation**, happily writing a path to a file that does not exist → leaves render untextured. Measured scope on the converted tree set: **137/143 leaf refs resolved, 6 broken across 4 NIFs** (`dementiatree01/04/10` + `treems14canvasfreesu`) — small, but invisible until you look, because the composite path masks it everywhere else. Two renamings account for all 6, both handled by the shared `match_tex_stem` (literal stem → trailing composite `c` → leaf-map variant number): `MTreeLeaves02c.tga` → `mtreeleaves02.dds`, and `TreeMS14CanvasLeaves01SU.tga` → `treems14canvasleavessu.dds` (three per-map variants collapse onto ONE shipped atlas). Anchor the variant-number strip on `leaves|needles` and not on "first 2-digit run", or `TreeMS14…` loses its model number instead. Audit it with a scan that checks each converted tree NIF's `textures[0]` against the filesystem — the count should be 0 missing.
- **Newer-CAD trees: the roots twist pair and the 50000 texture-coordinate block (fixed 2026-08-20, Tamriel Landscape Pack)**: 183 of 547 exported SPTs (all TamRes / Tamriel Landscape Pack; **no vanilla Oblivion tree is affected**) were authored by a later SpeedTreeCAD that writes two things the parser did not handle. Both are now supported, and the fix is provably additive — all 364 previously-parsing trees are value-identical and their generated geometry is bit-identical.
  1. **The roots block carries its own 15003/15002 pair, bare and REVERSED.** Outside the roots block the sections come in `15000`-opened `15002,15003` pairs, one per level. Inside `40000..40001` the pair appears once with no opener and in the order `15003,15002`, and it belongs to the roots level. The handler ignored `in_roots`, so `twist_idx` ran one past the last level, `_level_by_seq` returned `None`, and the parse died with `'NoneType' object has no attribute 'random_v_offset'`. Guard it exactly like the `16002` flare and `26002` roughness groups: when `in_roots`, target `tree.roots_level` and **do not advance the counter**.
  2. **Section 50000 is the per-layer texture-coordinate block** (`FORMAT` lines 367-387 + `sptparser.c` case 50004-50018). It holds `50002..50003` groups, each one texture layer: `50004` U tile, `50005` V tile, `50006/50007` U/V absolute, `50008` twist, `50009` random V offset, `50010` V offset, `50011/50012` clamp, `50013-50016` left/right/bottom/top crop, `50017` U offset, `50018` sync-to-diffuse. There are **7 layers per level** (diffuse, detail, normal, height, specular, user1, user2 — the same seven filenames as `70002..70008`), and the block covers **trunk + branch levels + leaves + roots**, so the group count is always **`(num_levels + 1) * 7`** — verified 28/35/42 for 3/4/5 levels across all 183 files, 0 exceptions, matching the FORMAT note "count occurrences of 50002: 28 35 42 49, the difference is 7". Layer 0 is diffuse and duplicates the older per-level `6013-6016`/`15002`/`15003` values. Stored on `SptTree.tex_layers` as `TexLayer`; empty for older-CAD trees, which omit the block entirely. `70000/70001` also had to become markers (their `70002..70008` payloads were already in `_PATTERNS`).
- **Leaf textures: the ICON is the AUTHORED source; the stem-collapse rule is a narrow patch, do NOT widen it** (audited 2026-08-20, `tools/lod/spt_leaf_tex_audit.py`). The SPT's `4003` leaf-map string and the `70002` diffuse filename under `60003` both store the ARTIST'S path (`C:\Hope\IDV\GreyPoplar\TreeGreyPoplarLeavesSU.tga`) — they agree with each other and neither is what shipped. The authored answer is the **TREE record's ICON**, which resolves **literally in 354/354 records** across Oblivion + Nehrim; `match_tex_stem`'s collapse regex is needed for **0** of them. (The ICON is the *composite* texture and the per-group fallback — a per-map leaf group whose OWN stem ships still wins, e.g. Nehrim's `treecottonwoodsu` groups keep `treecottonwoodleavessu` rather than the record's `Nehrim_Southshrub_SU01`. Verified 2026-08-20: modelling `build_tree_nif`'s exact per-group selection reproduces every shipped NIF's texture set, 146 variants sampled across Oblivion/Nehrim/TamRes, 0 mismatches.) Measured over all 662 tree variants, dropping the collapse rule changes the shipped texture for only **8** — exactly the documented `dementiatree01/04/10`, `treems14canvasfreesu`, and 4 Nehrim stems. So the rule earns its place at that width and nothing more: **widening it (trailing letter `leaves01a`, underscore `leaves_1`) buys only trees that have no TREE record at all** and is pure heuristic — CLAUDE.md "look for the AUTHORED indicator".
- **A resource pack's SPTs have NO TREE records and are never placed.** TamRes/Tamriel Landscape Pack ship 69 SPTs with **0** TREE records (Oblivion: 139/139 have one). Their trees are raw art for other plugins to reference, so an unresolved leaf texture there is invisible in-game. Of the 42 trees that resolve to no leaf texture, only **7 are placed**, and all 7 are legitimately leafless — `shrubdeadbush`, `treekvatchburnt`, `dtree02` (no leaf maps), and Anequina's two cacti, whose leaf maps are literally `FileLoadError.tga`. **No in-game tree is missing foliage art**; do not "fix" this by inventing a name-matching rule.
- **Auditing leaf textures per-plugin is MASTER-BLIND.** `tex_index` over one plugin's `textures/trees` reported Valenwood as 0/58 resolved and Oblivion as 67/139; merging the masters' tree-texture dirs (as `convert_spt_directory` already does) gives 58/58 and 136/139. Any tree-texture audit must merge master dirs or every dependent plugin looks catastrophically broken.
- **Spline variance is a MAGNITUDE — take `abs()`** (fixed 2026-08-20). `BezierSpline.eval_var` called `rng.uniform(-variance, variance)`, which raises `ValueError: high - low < 0` on a negative stored variance. `reddeliciousappletree.spt` level 3 stores `length` as `lo=-0.03 hi=0.08 variance=-0.007` — 3 occurrences across all 547 trees (one tree × 3 plugin copies). The sign carries no meaning; the flare code already used `abs()` on its `*_var` fields, so this just makes the spline path agree.
- **Composite quad convention (2026-07-10)**: section 10002 quads are 4 corner pairs in order **TC0..TC3 = TR, TL, BL, BR in TGA space where v runs UP** (corner layout per the FORMAT doc's embedded-texcoords dialog). Sampling the shipped DDS requires **v_dds = 1 − v_tga** — the SpeedTreeRT texture flip that ck-cmd enables (`SetTextureFlip(true)` in `references/ck-cmd-master/src/spt/sptconvert.cpp`). Using quad v directly as DDS v swaps vertically-stacked atlas crops: dogwood rendered ONLY flowers because its leaves crop (TGA bottom half) sampled the DDS top half where the flowers live. Leaf-map 4004 origin (card pivot) is in the same TGA v-up space.
- **Blossom rules** (sections 3000/3002 + per-map 4000 flag): maps flagged blossom (dogwood flowers, azalea/hydrangea/rhododendron blooms — 6 SPTs total) are placed only at branch positions x ≥ blossom_distance (3000) and take blossom_weight (3002, e.g. dogwood 0.23) of the eligible picks; ordinary leaf maps share the rest uniformly.
- **Bark UVs** (sections 6013-6016 + 15002/15003, semantics per `references/spttools-master/speedtreecadnotesv4`): U = u_tile repeats around the circumference plus a Twist (15003) spiral along the length; V = v_tile repeats where the **v_abs flag (6016) means the count is exact; otherwise it scales with the stem's STORED length** (dogwood trunk 12 × 0.8 = 9.6 repeats — lands square texels against its U density on every sampled tree); random_v_offset (15002) de-syncs bark phase per stem. The tube seam column must be DUPLICATED (n_az+1 columns, last u = u_tile) — a modulo wrap swept the whole texture backwards across one face of every trunk (the "bad trunk UV" stripe).
- **Branch curvature**: gravity bend is a **linear-rate arc** (constant curvature, `min(gap, step)` per ring toward straight up/down per the gravity semantics above), `GRAVITY_RESPONSE = 8.0` rad capacity at rate×flex = 1. An exponential approach (rotate by gap×frac) slows near the target and left every branch a straight stick. Ring caps must stay near the STORED segment counts (`_RING_CAP` 16/10/6 — oak trunk stores 18, cottonwood limbs 13); crushing them to 3-6 rings flattens every curve. **Disturbance** (6000, variance 15-50° in real trees) is a **ZERO-MEAN snake**: the bend direction oscillates along the stem (sine, random phase, 1.2-2.6 turns) about a slightly-drifting azimuth, so stems curve in-out-in with no net flop. Two failed models: a fresh random direction per ring averages into fuzz (stem reads straight); a persistent one-way azimuth accumulates the variance as NET drift and lays branches over on their sides.
- **Fork limb sizing**: the radius curve over the spawn window IS the limb-size variation (cottonwood forks store 0.03→0.01, a 3× spread; its "trunk" is a 72-unit stub — the level-1 limbs are the visible trunks). Cap child radius only at the parent's radius at the attach point; capping at 0.85×prad flattened the forks to near-identical thickness. Start-angle curves over the window matter the same way (cottonwood: 60°→0° — early limbs spread, later ones vertical).
- **Tube winding**: front faces MUST wind so the geometric normal aligns with the radial vertex normals (>80% positive dot vs vanilla), else the trunk renders visible only from INSIDE (the "U-shaped view inside the tree" bug).
- **NIF structure** = vanilla flora (verified vs `references/Skyrim Meshes/meshes/plants/florasnowberry01.nif` and `landscape/trees/wrtempletree01.nif` Gildergreen): `BSLeafAnimNode` root (flags 14) + `BSXFlags=130` + one bark `NiTriShape` (BSLightingShaderProperty, vertex colors) + leaf `NiTriShape`s (composite texture, `NiAlphaProperty` flags 0x92EC thr 128, shader SLSF2_Tree_Anim + Double_Sided + Vertex_Colors, SLSF1_Vertex_Alpha). ALWAYS set `uv_scale=(1,1)` on PyFFI-created shaders (defaults to (0,0)=invisible).
- **Collision = EXACT trunk mesh** (not a fat capsule): the generator collects the trunk + thick-limb (base radius ≥ `COLLISION_MIN_RADIUS`=5hu) tube triangles into a soup; `spt_converter._make_collision` builds `bhkMoppBvTreeShape→bhkCompressedMeshShape` from it via `cms_builder.build_cms_collision` (the real Havok MOPP bridge). Plain identity static bhkRigidBody, CMS target = root BSLeafAnimNode, wood material, layer 1. Matches Gildergreen exactly. Falls back to a trunk capsule only if the bridge fails. A capsule sized to the whole trunk AABB is ~2× too fat — use the mesh.
- **One NIF PER TREE RECORD** (named `<editorid>.nif`): Oblivion resolves each TREE record's leaf composite texture from its ICON field and seeds the generator from its SNAM seed, so records sharing one `.spt` (e.g. ShrubVineMapleSU + TestToddTree03) genuinely differ. Manifest read from `<export>/TREE.txt` by `load_tree_manifest`.
- **TREE record import** (`tes5_import/record_types/items.py::convert_TREE`): MODL → `tes4\speedtrees\<editorid>.nif`; OBND derived from the TES4 billboard dims (real world size); adds PFPC (0) + CNAM (12 wind floats — the BSLeafAnimNode params TES4 has no source for).
- **Preview/iteration tool**: `python tools/lod/spt_preview.py <spt_or_dir> [--views 0,90] [--out dir]` renders the generated geometry to PNG with real leaf textures AND pastes Oblivion's own billboard render beside it for A/B comparison. This is how the generator semantics were validated — ALWAYS compare against the billboards, never guess.
- Stats: 113 Oblivion.esm SPTs → 116 tree-record NIFs, 0 fail, all 116 collision-sane + MOPP-clean. Tests: `tests/test_spt_convert.py` (19 tests: parser, generator, NIF builder, TREE import).

## Book inventory art (INAM reading rigs) — books were invisible with no text when opened (SOLVED 2026-07-18)
<a id="nif-texture-path-basename"></a>
NIF texture paths are ALWAYS backslash-separated, whatever the host OS.
`os.path.basename` splits only on `/` off Windows, so any code comparing a NIF
texture path's leaf must normalize TO forward slashes before calling it -- that
direction works on both, since Windows accepts `/` too. Normalizing to
backslashes instead silently never matches on Linux.
`asset_convert/ui/book_inam.py:_tex_basename` is the shared helper.

<a id="book-inventory-art-books-were"></a>

- **Why books failed**: Skyrim's BookMenu renders the BOOK record's INAM inventory-art mesh, never the world MODL. The vanilla INAM meshes (`clutter\books\book02\character assets\bookskyrim01.nif`, `clutter\books\note01\note02.nif`) are rigged: skinned page-turn bone chains ("Book CoverPage Turn1-6", "Book TurnPage1-10", "Note Fold1-3"), a `BSBehaviorGraphExtraData` pointing at `Clutter\Books\Book01\Book01Project.hkx` that drives the open/page-turn animation, and a 4-vert `PageText` NiTriShape (with `NiStringExtraData 'Keep' = "NiHide"`) the engine swaps for the rendered page text. A static (converted Oblivion) mesh as INAM opens invisible with no text. INAM must always be present — BookMenu null-derefs without it.
- **Solution** (`asset_convert/ui/book_inam.py`): keep the vanilla rig byte-for-byte (UVs, skin, BGED untouched — animation guaranteed) and instead **bake the Oblivion book's textures into the template's texture layout**, then point the template's cover `BSShaderTextureSet` at the baked atlas. One NIF+DDS pair per distinct TES4 book model (38 for Oblivion.esm) → `meshes\tes4\clutter\books\inv\<model basename>.nif` + `textures\tes4\clutter\books\inv\<base>.dds`/`_n.dds`.
- **Calibration is per-mesh UV-island fitting, not hardcoded rects**: both sides decompose into the same semantic regions — front cover (largest flat +Z island above the midplane), spine (tall |n_x| island on the same texture), page-edge strips (side-facing islands spanning the page block; Oblivion maps these to `bookpages01.dds`). A least-squares affine fit (normalized in-plane coords → uv) per region on each side, composed dst-uv → coords → src-uv, handles every Oblivion layout automatically: Octavo has the spine on the left edge of the texture, Quarto/Folio have it in the middle with separate front/back art. The Skyrim cover uses ONE art rect for both covers (u∈[0.24,0.96], spine u<0.22, page strips v>0.97, with wrapped UVs at u±1/v+1 reusing regions), so the Oblivion FRONT cover art is used for both sides — same limitation as vanilla.
- **Flat sheets (notes/parchment/posters/broadsheets) bake as a plain UV-space rect copy**, NOT through mesh coords: sheet art is authored upright in texture space while the world mesh may lie in any orientation (a flat-lying broadsheet arrived rotated 90° on the portrait Note02 template until this was changed). Unfittable sources (rolled scrolls, crumpled paper — UV not affine in position, fit rms > 0.08) fall back to an identity full-texture copy; scroll textures are actually flat sealed-parchment art, so they read fine on the note rig.
- **Atlas output**: 512² uncompressed BGRA DDS with a full box-filtered mip chain (writer in book_inam, decode via PIL). Normal maps baked the same way from the `_n` siblings (flat normal fallback). Pages/paper shapes keep the vanilla `LargeBookPaper01.dds` (loaded from Skyrim's own BSAs — nothing redistributed; templates come from the user's `Skyrim - Meshes*.bsa` via `skyrim_assets` auto-extraction, read through `sse_nif`).
- **pyffi round-trips Skyrim rigs safely**: re-writing bookskyrim01.nif only reorders the header string table with indices remapped consistently (verified byte-level); skin partitions/BGED survive.
- **Record side** (`tes5_import/record_types/equipment.py::convert_BOOK`): one `InvArt_<base>` STAT per distinct model (cached on the writer — BOOKs convert serially), INAM → that STAT; vanilla `HighPolySkyrimBook` only for model-less books. DATA.Type is ALWAYS 0: the CK lists 255 = Note/Scroll but vanilla Skyrim.esm types all 821 BOOKs (notes included) as 0, so 255 is engine-untested; TES4 scroll-ness survives via the vendor keyword + note-rig INAM.
- Pipeline: runs inside `convert.py phase_assets` after `convert_meshes`; standalone CLI `python -m asset_convert.ui.book_inam Oblivion.esm [--extract-dir export] [--output-dir output] [--templates-dir <explicit meshes tree>] [--skyrim-data <SSE Data>] [--workers N]`. Tests: `tests/test_book_inam.py`.
- **Basename uniqueness (fixed 2026-07-28)**: `inv_basename()` keyed on the MODL leaf filename only, so plugins that merge several asset trees collided — Morroblivion ships both `Clutter\Books\Note01.NIF` and `Morroblivion\Clutter\Paper\Note01.nif`, and the guard `raise ValueError('INAM basename collision')` **aborted the entire asset stage** (book INAM + everything after it) for the whole plugin. Now names outside the conventional `clutter\books` tree are qualified by their parent directory (`paper_note01`), leaving vanilla-layout names untouched so existing generated assets stay stable; a residual clash logs and skips that one model instead of killing the stage. `equipment.py::convert_BOOK` **imports `inv_basename` instead of re-deriving it** — the rule had been duplicated in both files, which is exactly how the STAT target and the generated mesh could drift apart.
- **`i.shape in page_shapes` was a numpy trap (fixed 2026-07-28)**: shapes are dicts holding numpy arrays, so `in` runs dict `__eq__` → element-wise array comparison, raising `ValueError: operands could not be broadcast together` the moment two shapes have different vertex counts. Books whose shapes happened to share a vertex count worked; mixed ones failed to bake (35 failures on Morroblivion, now 0). Compare islands to shapes by `id()`, never by `in`/`==`.

## SSE-format NIF read support + BSA auto-extraction (2026-07-19)
<a id="sse-format-nif-read-support"></a>

- **Rule: the pipeline never resolves runtime assets through `references/`** (that tree is comparison-only and may not exist). Vanilla Skyrim files are fetched via `asset_convert/sources/skyrim_assets.py`: `export/skyrim_assets/` cache first, else extracted on demand from the registry-detected SSE install's BSAs (atomic cache writes — pool workers race). `set_skyrim_data()` overrides detection.
- **SSE meshes (BSTriShape) are readable via pyffi Patch 8** (`pyffi_monkey_patch._install_sse_layouts`): registers `BSTriShape`/`BSDynamicTriShape` (fixed prefix as declared pyffi attrs so name/Refs use the generic link machinery; variable vertex/triangle/particle payload hand-read into numpy `sse_*` arrays) and an SSE-layout `NiSkinPartition` read (`sse_partitions` dicts + shared vertex buffer). READ-ONLY by design — writes raise.
- **`asset_convert/sse_nif.read_nif(path_or_bytes)`** converts any SSE graph to LE in-memory: BSTriShape → NiTriShape+NiTriShapeData (verts/normals/uvs/colors/tangents; skinned shapes pull geometry from the partition's shared vertex buffer), skin partitions rebuilt faithfully (preserving the vanilla body's semantic 32/34/38 dismember split — do NOT regenerate from scratch), `user_version_2` set to 83 so writes are LE. Validated field-identical against the LE reference body.
- **SSE partition gotchas**: BOTH triangle arrays in an SSE NiSkinPartition ("Triangles" and "Triangles Copy") hold GLOBAL shape-vertex indices — LE wants partition-LOCAL indices into the vertex map, so remap via inverted vertex_map. Vertex-data bone indices are partition-local. Vanilla SSE `NiSkinData` still carries LE-style per-bone weights (`has_vertex_weights=1`), so binds/weights come straight from it; `_ensure_skin_weights` rebuilds them from partition data only if absent.
- **Consumers**: `skin_replacement.load_body_geom` (modified body in `output/` → BSA body), `book_inam.load_templates` (BSA book/note rigs; emit re-writes them as LE), `modify_body_meshes` (BSA body → split → LE output), `body_wrap._load_sk_surface`, `extract_skeleton_bones`. A missing body source now prints a loud `[skin_replacement] WARNING` instead of silently skipping the splice, and `generate_book_inams` validates templates in the parent before spawning workers (a worker-initializer crash surfaces only as an opaque BrokenProcessPool, with stderr hidden under pythonw).


## Two vanilla divergences investigated and DELIBERATELY NOT FIXED (2026-08-22)
<a id="two-vanilla-divergences-investigated-deliberately"></a>

Both were found while chasing the ElsweyrAnequina load crash, both were briefly
believed to be its cause, and both turned out not to be: that crash was an
unsupported collision shape (next section), confirmed fixed in-game.

Fixes for both were written, measured, tested — and then **reverted and not
shipped**, because neither has a demonstrated in-game benefit and both carry
real downside. Recorded here so the measurements are not re-derived, and so a
future session does not "fix" them again without new evidence.

**Do not re-fix either one on the strength of the census alone.** Ship only if
an actual in-game symptom is traced to it.

### 1. Object LOD carries `slsf_2_double_sided`; vanilla never does

Oblivion marks foliage and other cutout geometry two-sided with a
`NiStencilProperty` (`draw_mode` 3 = DRAW_BOTH). `nif_converter` carries that
across as `slsf_2_double_sided` — correct for the full-size mesh — and LODGen
then copies the flag into the shader it writes for each baked tile.

Measured:

| population | tiles | shader props | `double_sided` |
|---|---|---|---|
| vanilla `meshes/terrain` (mixed) | 120 | 141 | **0** |
| vanilla `terrain/tamriel/objects` | 40 | 74 | **0** |
| our output | 2,582 | — | **21,899** |

Vanilla object LOD is uniformly `f2=0x00000005` on Tamriel tree LOD. So the
flag is a genuine divergence.

**Why it was not shipped:** no in-game symptom was ever traced to it, and no
back-face rendering cost was measured. The fix had to be a byte patch (parsing
~15,700 tiles with pyffi costs ~2.3s each, i.e. ten hours), and an in-place
byte patch on shipped artifacts is a standing corruption risk if a future
LODGen output shifts the anchored layout — a bad trade for an unmeasured gain.

For the record the patch did validate cleanly: anchoring on the 32-byte window
(controller/extra-data refs `-1,0,-1` before the flag pair; UV offset/scale
`0,0,1,1` after) matched the true `BSLightingShaderProperty` count **exactly on
453 of 453 tiles**, was idempotent over repeated passes, and ran the full
output in 31s.

### 2. One `NiAlphaProperty` shared between shapes; vanilla never shares

Measured: **10 of 400** Oblivion source meshes share one block between shapes,
up to **14 shapes on the single block** in `benirusdoor01.nif`, and the sharing
survives conversion (5 of 300 converted architecture meshes). Vanilla Skyrim:
**300 meshes, 250 carrying an alpha property, 0 sharing one.**

The particle path in `nif_converter` already clones for exactly this reason;
the geometry path does not.

**Why it was not shipped:** the mechanism originally claimed for it — that
these properties are refcounted per render pass, so a block reached through N
shapes is released N times — was **never measured**. It was a theory invented
while this looked like the crash cause. With that removed, what remains is
"vanilla does not do this", with no observed misbehaviour to fix.

## `bhkPackedNiTriStripsShape` reaching the output — the 2 GB memcpy / heap-wide `0x100000001` (SOLVED 2026-08-22, confirmed in-game)
<a id="bhkpackednitristripsshape-reaching-output-2-gb"></a>

**Symptom.** Reproducible crash-or-freeze near `tes4tamriel 0 -30` in
ElsweyrAnequina.  The access violation moved around between runs -- the shadow
renderer, `BSXAudio2GameSound`, a `ScrapHeap` path, `bhkListShape` during a mesh
load, and finally inside tbbmalloc's own
`rml::internal::MemoryPool::getTLS` -- but the faulting value was **always
`0x0000000100000001`**.  Sometimes CrashLoggerSSE itself deadlocked in its
handler (`MSVCP140!_Mtx_lock` -> `RtlpAcquireSRWLockExclusiveContended` ->
`NtWaitForAlertByThreadId`), so the game "hung" with no crash log written at
all.

**Same family as the Seyda Neen UV-set CTD** (see the `_clamp_uv_sets` entry
above): both are a short destination buffer, both fault inside a `memcpy`
on a non-temporal store, and in both the crash log blames whatever the
corrupted memory reached next rather than the mesh that caused it.

**Do not chase the subsystem in the log.**  Four different crash logs blamed
four different subsystems and all four were victims, not causes.

**Diagnosis (from a live dump).**  Attach cdb, `sxe av`, `.dump /ma` at the
fault (`tools/live/hang_capture.py` does exactly this).  In the captured dump:

* the faulting thread was inside `VCRUNTIME140!memcpy` with **`r8 =
  0x7EF225F0` -- a 2.03 GB copy length** (a second crash log showed
  `0x7F436BE0`, the same thing);
* the memcpy SOURCE was a **2.25 GB** committed block whose tbbmalloc header
  read `totalSize=0x90010000`, `objectSize=0x90000000`, owner pointer into
  `EngineFixes.dll`, and whose contents were **entirely
  `01 00 00 00 01 00 00 00 ...`**;
* the same allocator list held a **36 GB** block; total commit was **49.2 GB**
  against a normal ~8 GB;
* the loader stack carried the asset path as a plain string --
  `data\MESHES\tes4\anequina\architecture\huts\domehut01.nif`.

So the `(1,1)` fill is not something corrupting memory: it is uninitialised
content being **copied around by the gigabyte**, landing in whatever allocates
next.  That is why one bad mesh looks like four unrelated crashes.

Finding the path on the stack is the step that matters -- search the loader
thread's stack for `"nif"` (`s -a <stack range> "nif"`) and read the string
back.

**Cause.** `_convert_shape` had:

```python
if isinstance(shape, NifFormat.bhkNiTriStripsShape):
    packed = _ni_strips_to_packed(shape)
    return packed if packed is not None else shape      # <-- returns too early
```

A `bhkNiTriStripsShape` nested inside a `bhkListShape` was converted to a
`bhkPackedNiTriStripsShape` and returned **directly**, never reaching the
`bhkPackedNiTriStripsShape` branch a few lines below that rebuilds it as
MOPP + `bhkCompressedMeshShape`.

Skyrim does not support that shape.  Census: **0 of 17,216 vanilla Skyrim
meshes** contain `bhkPackedNiTriStripsShape` or `hkPackedNiTriStripsData`.  The
engine mis-sizes its sub-part allocation and then memcpys the payload with a
garbage 32-bit length -- the loader's grow step is
`imul edx, r15d` / `imul esi, r15d`, both 32-bit, feeding the allocator and the
memcpy.

**Scope.** Exactly **10 meshes** in the whole output still shipped the type:
1 in ElsweyrAnequina (`domehut01.nif` -- the only one of 1,837 in that plugin,
and the one the dump named), 1 in Oblivion.esm
(`dungeons\root\interior\misc\gnarlspawner.nif`), 8 in Tamriel Resource
Pack Full 2.0.

**Fix.** Route the converted shape back through `_convert_shape` so the
existing MOPP/CMS rebuild runs.  The rebuild succeeds for these meshes -- it was
simply never attempted.  Verified on `domehut01.nif`: before, `bhkListShape` ->
`bhkPackedNiTriStripsShape` + `hkPackedNiTriStripsData`; after, `bhkListShape`
-> `bhkMoppBvTreeShape` -> `bhkCompressedMeshShape` with one chunk of 1,935
verts / 3,711 indices = **1,237 triangles, exactly the source count**, and a
10,587-byte MOPP tree.

**Note on a red herring:** pyffi prints the Skyrim stone material
(`3741512247` = `SKY_HAV_MAT_STONE`) as `<INVALID>` because its enum table is
incomplete.  That value is correct output, not a corruption sentinel.

**Also note:** `bhkPackedNiTriStripsShape.Num Sub Shapes` is `until="20.0.0.5"`
and `hkPackedNiTriStripsData.Num Sub Shapes` is `since="20.2.0.7"` (nif.xml
3195/3968), so at our output version the shape-side count is not serialised and
a `0` there is cosmetic.  It is not the bug -- the unsupported *shape type* is.

### Three defects, not one (2026-08-22)

The unsupported shape reached the output by three separate routes.  All three
are fixed; the first is the one confirmed in-game.

1. **Nested strips never rebuilt.**  `_convert_shape`'s
   `bhkNiTriStripsShape` branch converted to a packed shape and returned it
   directly, skipping the `bhkPackedNiTriStripsShape` branch below that
   rebuilds as MOPP+CMS.  Fixed by recursing:
   `return _convert_shape(packed, root_node)`.
   (`anequina/architecture/huts/domehut01.nif` — confirmed fixed in-game.)

2. **Collision on non-NiNode geometry never converted at all.**
   `convert_all_collisions` opened with
   `if node is None or not isinstance(node, NifFormat.NiNode): return`, so a
   `bhkCollisionObject` hanging off a **NiTriShape** was skipped *and* its
   subtree was never walked.  Oblivion does exactly that:
   `obmkmeadhallmaindoor.nif` puts a `bhkPackedNiTriStripsShape` on the
   NiTriShape `'Scene Root:5'`.  Those objects passed through completely
   unconverted — Oblivion-format shape, Oblivion-format filter values and all.
   Fixed by converting whatever any node owns and always continuing the walk.

3. **Unsafe fallbacks when MOPP failed.**  Two sites shipped a packed shape
   when `build_cms_collision` returned None (`_rebuild_mesh_collision`, and the
   `bhkPackedNiTriStripsShape` branch of `_convert_shape`, which also had a
   "repair the sub-shape count and return `shape`" path).  An unsupported shape
   is never safer than no collision, so both now drop instead.
   `_packed_from_tris` had no callers left and was deleted.

   In practice MOPP only fails on geometry that is not a surface:
   `romanhanginglamp01.nif`'s collision is 8 vertices with X=Y=0 — a bare line
   segment on the Z axis, zero area, quantising to two distinct points.

### Measured blast radius (2026-08-22)

762 Oblivion.esm **source** meshes contain a packed shape, but the converter
already handled 761 of them: scanning all **44,856 converted meshes** across
every plugin found only **5** carrying `bhkPackedNiTriStripsShape` /
`hkPackedNiTriStripsData` after the first fix —

| plugin | leaked / scanned |
|---|---|
| Oblivion.esm | 1 / 11,575 (`dungeons/root/interior/misc/gnarlspawner.nif`) |
| Tamriel Resource Pack Full 2.0 | 4 / 6,129 (3 oblivimonk architecture + romanhanginglamp01) |
| ElsweyrAnequina.esp | 0 / 1,837 (was `domehut01.nif`) |
| Nehrim.esm | 0 / 14,609 |
| Morrowind_ob.esm | 0 / 8,444 |
| everything else | 0 |

So the crash needed a rare combination, which is why it reproduced at one spot
rather than everywhere.  Re-run the census with:

```bash
python tools/nif/nif_block_scan.py output/<plugin>/meshes \
    --any bhkPackedNiTriStripsShape hkPackedNiTriStripsData
```

Guarded by `tests/test_collision_packed_strips.py`.

## FO3/FNV shader properties

**Code:** `_bs_pp_texture_slots`, the property loops in `process_geometry` and
`_convert_particle_system`.

Oblivion keeps texture paths on `NiTexturingProperty`. FO3/FNV keep them in a
`BSShaderTextureSet` hanging off `BSShaderPPLightingProperty` — nif.xml gates
that block `versions="#FO3#"`, and `Lighting30ShaderProperty` inherits from it,
so an isinstance check covers both.

The property loops read only the Oblivion vocabulary, so for FO3/FNV meshes
`diffuse_path` stayed empty and slot 0 took the `Textures\white.dds` neutral
fallback — every converted mesh rendered purple in NifSkope. Measured before the
fix: of the texture references in a 40-mesh output sample, **3 resolved and 107
did not**, while **149/149** sampled *source* shapes carried a real diffuse path.
The data was always present and simply never read.

Slot order is identical to Skyrim's (0 diffuse, 1 normal, 2 glow), so the paths
need no remapping — only `rewrite_tex_path`, which already normalizes
separators and strips the stray `data\` prefix that FO3 LOD meshes carry.

**The normal map is authored, not derived.** Oblivion rarely ships an `_n`
beside its diffuse, so the Oblivion path guesses `<base>_n.dds` and falls back to
a shared flat normal via `_resolve_normal_for`. FO3/FNV name slot 1 outright, so
an authored value is taken verbatim and the guess is skipped entirely — this is
the AUTHORED indicator, and it must win over the heuristic.

## Flame attachment: FlameNode sockets

**Code:** `asset_convert/nif/nif_flames.py`

Oblivion marks where a flame should burn with an empty `FlameNode*` NiNode and
attaches a flame NIF there dynamically at runtime (`firecandleflame.nif` for
candles/sconces/lamps, the torch flame for torches). Skyrim has no such runtime
attachment, so we **convert**: the matching Oblivion flame NIF is run through the
full converter once (cached per worker) and its converted subtree is grafted
under each `FlameNode` marker. This ships Oblivion's own flame visuals —
flip-book quads plus particle systems — rather than substituting Skyrim's
Master-Particle-System flames.

A much earlier graft attempt crashed the engine. That crash was actually the
PyFFI `NiPSysData` 66-vs-70-byte misalignment plus `uv_scale=(0,0)`, both long
fixed; the interim `BSValueNode`/`AddonNode` substitution has been removed.

### The socket map is AUTHORED data

Oblivion ships one STAT under WorldObjects/Static per socket, EditorID
`FlameNode<N>`, whose MODL is the flame NIF the engine attaches:

| Socket | FormID | Model |
|---|---|---|
| FlameNode0 | 0x0000001E | Fire/FireCandleFlame.NIF |
| FlameNode1 | 0x0000001F | Fire/FireTorchSmall.nif |
| FlameNode2 | 0x00000020 | Fire/FireTorchLarge.nif |
| FlameNode3 | 0x00000021 | Fire/FireTorchLargeSmoke.nif |
| FlameNode4 | 0x00000022 | Fire/FireOpenSmall.nif |
| FlameNode5 | 0x00000023 | Fire/FireOpenSmallSmoke.nif |
| FlameNode6 | 0x00000024 | Fire/FireOpenMedium.nif |
| FlameNode7 | 0x00000025 | Fire/FireOpenMediumSmoke.nif |
| FlameNode8 | 0x00000026 | Fire/FireOpenLarge.nif |
| FlameNode9 | 0x00000027 | Fire/FireOpenLargeSmoke.nif |

Those FormIDs are the keys Oblivion.exe hardcodes: the socket-name table at
`0xB06818` is walked in lockstep with a parallel table at `0xB067C0` holding
`0x1E..0x32`, looked up in the form map at `0xB0613C`. The engine resolves a
socket to a STAT and draws that STAT's model — **the plugin owns the mapping,
and a mod may repoint it**. FlameNode10-20 exist for custom use and ship no STAT
in vanilla.

Reading it beats any heuristic. Keying on the host FILENAME ("torch" in the name)
put the 1.3x2.6-unit candle flame on every lamp in the game — `castlelight02` is
`FlameNode2`, i.e. FireTorchLarge (32x64).

### Socket names match exactly, never zero-padded

The engine matches socket names EXACTLY against its own table, which holds only
unpadded `FlameNode<N>`. A zero-padded marker therefore matches nothing and no
flame is attached — verified against Oblivion.exe, which contains `FlameNode7`
and `FlameNode1` but neither `FlameNode07` nor `FlameNode01`. Two vanilla meshes
are authored that way and burn nothing in game:
`clutter/metalsmith/forgeopen01.nif` (FlameNode07) and
`clutter/lecternworkstation1.nif` (FlameNode01). Matching them loosely put a
468-unit FireOpenMediumSmoke on the forge that Oblivion never shows.

### Grafting details

The socket is resolved **per marker**: one mesh can mix socket families
(`lecternworkstation1` carries both a FlameNode0 candle and a FlameNode1 torch).
The marker's authored rotation is kept — it is the host-frame to flame-frame
hook-up, and on a +Z-up host it *is* the axis correction the flame needs.

Grafted particle systems need per-frame controller updates, so the host root
gains the `BSX` Animated bit. Flip-book atlas jobs from the flame's own
conversion propagate to the host's stats so `convert_nif` builds them into this
host's output tree too (idempotent, exists-checked).

### The marker rotation must not be zeroed

Translation, scale **and rotation** are all kept from the marker (Oblivion
authored FlameNodes with ~2x scale that the attached flame NIF expects). The
rotation is the authored hook-up between two DIFFERENT model frames: the flame
NIFs are authored +Y-up, and a host authored +Z-up carries exactly the −90°X
correction on its marker. `uppersilverplatecandles01`'s FlameNode0 is
`[1,0,0][0,0,1][0,-1,0]` — `_BB_AXIS_FIX` itself, mapping the flame's +Y onto the
plate's +Z. That host is a flat plate (extent X=23 Y=23 Z=2) and all 121 of its
REFRs use `RotX=0`, so nothing else would stand the flame up. Zeroing it laid the
candle flames on their side. Hosts that are themselves +Y-up author an identity
marker and are unaffected.

## NiBlendInterpolator: the manager-controlled flag and the Vilverin CTD
<a id="blend-interp-flags"></a>

**Code:** `BLEND_INTERP_FLAGS_ARRAYSIZE`, `normalize_blend_interpolators` in
`asset_convert/nif/nif_converter.py`.

`NiBlendInterpolator.Flags` bit 0 is "Manager Controlled". `nif.xml` makes the
next **seven** fields (Interp Count, Single Index, High Priority, Next High
Priority, Single Time, High Weights Sum, Next High Weights Sum) conditional on
that bit being **clear** — so a manager-driven interpolator is 7 bytes on disk
and a free-standing one is 15.

Vanilla is unanimous that these are manager-driven: **8779/8779
`NiBlend*Interpolator` blocks across Skyrim's meshes store `Flags=1` and
`Array Size=2`** (2688 bool, 5520 float, 571 point3).

### PyFFI 2.2.3 models this block wrong

PyFFI declares `unknown_short` + `unknown_int` + `bool_value` where the real
layout is `byte Flags`, `byte Array Size`, `float Weight Threshold`,
`byte Value`. These are **not padding** — the usual reason to leave `unknown_*`
alone — they are real named fields PyFFI failed to describe, so they must be
written explicitly:

```
unknown_short = 0x0201   ->  Flags = 0x01 (low byte), Array Size = 0x02 (high)
unknown_int   = 0.0f     ->  Weight Threshold
```

Leaving them 0 emits `Flags=0`, which tells the engine to read 15 bytes out of
a 7-byte block: it runs into the next block and `AddRef`s whatever it finds.
That is the `lock inc [rax+0x08]` CTD on entering **Vilverin**, where `rax`'s
high half was `0xBF800000` — the `-1.0f` default of the Single Time field the
block never had.

## NiFlipController → frame-strip atlas
<a id="niflipcontroller-atlas"></a>

**Code:** `asset_convert/nif/flipbook.py`, called from `process_geometry`.

Fire and effect quads in Oblivion animate through several discrete textures
using `NiFlipController` on the `NiTexturingProperty`. That block is **dead in
Skyrim: 0 of 17,216 vanilla meshes use it.**

The Skyrim equivalent is a frame-strip atlas texture plus a
`BSEffectShaderPropertyFloatController` on "U Offset" (var 6) with stepped
(`CONST`) keys. The converter composes the source frames into a
horizontal-strip DDS and emits that controller, so the animation survives with
no `NiFlipController` in the output.

<a id="morrowind-num-uv-sets"></a>

## Morrowind (4.0.0.2): the duplicated `Num UV Sets`

**Code:** `asset_convert/nif/pyffi_monkey_patch.py` Patch 11.

pyffi read **0 of 60** Morrowind meshes before this patch. The cause is not the
version gate and not the boolean width -- 4.0.0.2 booleans genuinely are four
bytes, and forcing them to one only moves the failure.

`nif.xml` declares `Num UV Sets` on `NiGeometryData` **twice**, with different
widths *and* different positions:

- a `byte` before `Has Normals`, since 10.0.1.0
- a `ushort` after `Vertex Colors`, until 4.2.2.0

`StructBase.__init__` keeps one value object per attribute NAME and skips
duplicates -- its own comment requires duplicates to share a type, which these
do not -- so the stored object is always the `UByte`. At 4.0.0.2 the version
filter correctly yields the *ushort* attribute, but the read goes through the
byte object and consumes one byte where the file has two. Measured: pyffi read
the count at 2489->2490 where the file ends it at 2491, shifting every later
field by one and overrunning the block.

The fix gives BOTH declarations one shared version-aware type rather than
renaming either. Renaming is not viable: two candidate patches that renamed a
declaration read Morrowind 60/60 but broke **80/80** Oblivion and Skyrim files.

Measured after the patch: **60/60** sampled Morrowind meshes read (5798/5798
over the full corpus), 29,852 `NiTriShapeData` blocks structurally consistent,
and Oblivion/Skyrim round-trips byte-identical to before.

<a id="bethesda-geometry-data-flags"></a>

## Bethesda 20.2: `num_uv_sets` is a FLAG BIT, not a 6-bit count

**Code:** `_is_bs_geom_flags` / `_num_uv_sets_type` in
`asset_convert/nif/pyffi_monkey_patch.py`.

pyffi's bundled `nif.xml` sizes `UV Sets` as `(Num UV Sets & 63)`. For Bethesda
20.2 files that mask is **wrong**, and `references/nifxml/nif.xml` says why: the
u16 splits into two mutually exclusive types, selected by the `#BS202#`
predicate (`version == 20.2.0.7 && BSVER > 0`, so FO3/FNV/Skyrim/SSE):

| | `NiGeometryDataFlags` (non-Bethesda) | `BSGeometryDataFlags` (`#BS202#`) |
|---|---|---|
| bits 0-5 | Num UV Sets (0x003F) | **bit 0 only** = Has UV (bool) |
| bits 6-11 | Havok Material | Havok Material |
| bit 12 | NBT Method | Has Tangents |

The authoritative length expression is
`((Data Flags & 63) | (BS Data Flags & 1))` — one bit on the Bethesda side.
pyffi masks the low byte with `& 63` unconditionally, so **Havok Material's low
two bits (6 and 7) fold into the count**.

That is silent whenever those bits are clear, which is why it went unnoticed:
Havok material 5 gives `0x41 & 63 == 1`, the correct answer by accident. It
breaks the moment a mesh uses a material whose low bits are set. Measured on
FalloutNV: **Havok material 63** (`flags = 0x1FE1`) yields `0xE1 & 63 == 33`,
so pyffi demanded 33 UV arrays where the file has 1 and read past EOF —
`struct.error: unpack requires a buffer of 4 bytes` inside `NiTriStripsData`,
surfacing as the `RD` skip code.

Census over `export/FalloutNV.esm/meshes/clutter/hiddenvalley` (34 files):
**25 fail, 9 read** — exactly the 25 whose Havok material has a low bit set.
The 9 that read all carry `0x1141` (material 5). Tree-wide this was **32**
unconvertible FalloutNV meshes: the 25 `nv_hv_graffiti*` plus 7 under
`architecture/helios_one`.

The fix masks the value to bit 0 on read for `#BS202#` files and stashes the
Havok-material bits, restoring them on write so a round-trip is byte-identical.
Non-Bethesda and Morrowind files keep pyffi's own semantics.

**Note the correction:** the `_clamp_uv_sets` notes above (and
[one UV set](#one-uv-set)) describe the low 6 bits as the UV count. That is
true of `NiGeometryDataFlags` only. For every Bethesda file we read or write it
is a single bool, so a count of 2 was never representable there in the first
place; the clamp is still correct, its stated reason is not.

<a id="morrowind-collision"></a>

## Morrowind collision: `RootCollisionNode` (2026-09-01)

**Code:** `asset_convert/nif/nif_converter_morrowind.py`;
`attach_morrowind_collision` is called from `_convert_roots` AFTER each
root's Oblivion collision pass.

### Attached after conversion, not before

The first pass built the collision in `_run_source_fixups`. Two things then
happened to it: `collision.py::_convert_shape` unwrapped the
`bhkMoppBvTreeShape` as "stale Oblivion MOPP data" (the block list of every
built mesh had a bare `bhkCompressedMeshShape` under the body, which no
vanilla mesh ever has), and `_to_fade_node` swapped the root so the CMS
`target` pointed at a NiNode no longer in the tree (pyffi: *"NiNode block is
missing from the nif tree: omitting reference"*). Attaching after
`_convert_one_root` fixes both, and also means the triangles are read from
the FINAL geometry, so a root whose rotation `_wrap_root_transform` baked
into a child needs no separate frame correction. The BSXFlags the
collision-less tree earned are dropped and recomputed by `add_bsx_flags`.

One consequence of attaching late: when `_wrap_root_transform` HAS baked a
rotation (84 of 5,798 Morrowind meshes), the source root's children -- the
RootCollisionNode among them -- now sit under one inner NiNode carrying the
root's name, and the engine's direct-child search run on the BSFadeNode finds
nothing. Measured on a 308-mesh sample of the first full build: 4 meshes
(`in_dwrv_wall00`, `in_r_l_int_lcorner_03`, `in_r_s_int_wall_01`,
`ex_strongholdruin_wall02`) still rendered their collision node and had no
collision. `source_children_owner` recognises the wrapper and searches (and
strips) there instead.

Morrowind ships no Havok data whatsoever. Collision is an ordinary triangle
mesh parked under a `RootCollisionNode`, which the engine consumes and never
draws. Left alone that node ships to Skyrim as *visible* geometry and the
object gets no collision at all -- both halves wrong at once.

One exception replaces the node with the render geometry: a single-box node
the plugin places items inside, a stand-in that would eject them. See
[stand-in boxes](asset_convert_collision.md#morrowind-stand-in-boxes).

### Detection is by BLOCK TYPE, never by name

The 217 collision nodes found in a strided sample of the corpus **all carry an
empty name string**, so the `_STRIPPED_NODE_PREFIXES` name idiom silently
matches nothing -- a name-based census returned 0 and looked like proof that
Morrowind does not use the node at all. pyffi reads it as its own
`RootCollisionNode` class, exactly as the engine registers it
(`references/openmw/components/nif/niffile.cpp:68`), so `type(b).__name__` is
the reliable test.

### What the engine actually does

`references/openmw/components/nifbullet/bulletnifloader.cpp:170-177`:

- a `RootCollisionNode` is found -> its triangles ARE the collision
- **no** node -> `mGenerateCollision = true`; collision comes from the RENDER
  mesh
- an EMPTY node -> camera collision only, generated from render geometry

`findRootCollisionNode` (`node.cpp:209`) searches **direct children only, in
reverse**; recursion is opt-in through an `RCN` string extra.

### Measured corpus split (strided sample, 484 of 5,798 meshes)

| | count |
|---|---|
| has `RootCollisionNode` | 217 (45%) |
| ...as a direct child of the root | 217 (100%) |
| ...holding real geometry | 217 (100%) |
| no node -> engine builds from render mesh | 267 (55%) |

Collision meshes are small: 2 triangles minimum, **48 median**, 2,219 maximum.

With render-mesh generation in place, a 308-mesh stride of the full 5,834-mesh
output has collision on 291 (94%), every one a `bhkMoppBvTreeShape` + CMS with
0 bare CMS, 0 leftover `RootCollisionNode`s and 0 specular flags on a
Morrowind-sourced shader (the 4 that remain are the vanilla book reading rigs
`book_inam` generates). The 17 without collision are the skinned meshes
(banners, creature parts) plus the `NC`-flagged ones, exactly the set the
engine itself does not collide with.

### The scale is `1 / 69.9904`, not `0.1`

`collision.py`'s `_HAVOK_SCALE` (0.1) converts OBLIVION havok units (7 game
units each) into Skyrim havok units (69.9904 each). Morrowind authors its
collision mesh in plain render units, so the factor is `1 / GAME_UNITS_PER_HAVOK`
-- the same `_HAVOK_SCALE / 7.0` that `_visual_tri_soup` applies to Oblivion
render geometry. The first pass applied 0.1 to render units and every
collision shape came out **7.0x too large**: `ex_hlaalu_b_12.nif` renders
+/-536 units wide and its CMS spanned +/-53.9 havok units (37,700 game units)
instead of +/-7.66. Measured after the fix on the same mesh: CMS bounds
`(-7.70, -4.91, -4.55)..(7.70, 4.91, 7.71)` against render bounds
`(-539, -344, -318)..(539, 343, 540)` / 69.9904.

The rigid body is the vanilla static block already established by the
SpeedTree generator -- identity transform, mass 0, layer 1 (`SKYL_STATIC`),
motion system 5 -- and the triangles go through the real Havok bridge
(`cms_builder.build_cms_collision`), so the result is a genuine
`bhkMoppBvTreeShape` + CMS rather than an approximation. The material comes
from the mesh's own texture names ([surface materials](#morrowind-surface-materials)),
falling back to `SKY_HAV_MAT_STONE` — the same fallback `convert_materials`
uses for an unknown Oblivion material.

The node is stripped whether or not a shape was built -- it must never render.

### No node: the render mesh IS the collision

`bulletnifloader.cpp:170-230` is followed branch for branch by
`collision_source`:

| Source | Collision |
|---|---|
| root has an `NC`/`NCC` string extra | none (`NCC` is camera-only, which Skyrim lacks) |
| `RootCollisionNode` with children | its triangles |
| `RootCollisionNode` with none | none (camera-only) |
| no node | the RENDER geometry |

Generated collision skips what the engine skips: `AvoidNode` subtrees (AI
hints), skinned shapes (actors), every child but the first of a
`NiSwitchNode`/`NiFltAnimationNode`, and shapes named `Tri EditorMarker*` when
the root carries an `MRK` extra. Triangles are taken in the ROOT frame
(`get_transform(root)`), not the collision node's, so a transformed node no
longer offsets the shape. Every mesh goes through the same Havok bridge, so
the generated case is a real MOPP + CMS too.

### The flag extras must be READ before the root swap (2026-09-21)

Both rows that depend on a string extra read it off the root that
`attach_morrowind_collision` is handed, which is the POST-swap
`BSFadeNode`. `_to_fade_node` rebuilds the root and carries extra data
selectively — a bulk copy breaks animated objects — and `_copy_root_frame`
forwards name, flags, transform, havok material, collision object, children
and controller, but not `extra_data_list`. `NC`/`NCC`/`NCO` and `MRK` stayed
on the discarded `NiNode`:

```
BEFORE swap: old root NiNode     ['nco']
AFTER  swap: new root BSFadeNode []
```

So every flagged mesh fell through to the "no node" row and collided with its
own render geometry. `Arktwend/lichtstrahl.NIF`, a light shaft, shipped a MOPP
+ CMS over its alpha-blended plane and read in-game as an invisible wall
(`mw_collision_generated = 1`). A byte scan of Arktwend's 9,705 NIFs found
**510** roots carrying an `NC` variant and **16** carrying `MRK`, all losing
the flag.

**Latch them, do NOT copy them.** The obvious fix — appending those blocks to
the new root beside `_carry_bsbound` / `_carry_furniture_markers` — ships a
Morrowind flag into a Skyrim mesh. Morrowind identifies these extras by their
VALUE and leaves `name` empty; Skyrim identifies an extra by `name`
(`equipment_rig.py:232` finds `Prn` that way), and of 4,002 vanilla meshes
scanned, 117 carry a `NiStringExtraData` and every one is `Prn` or
`AnimObjectR` — none is `NC`/`NCC`/`MRK`. Copying them put an unreadable block
on 519 shipped NIFs, clothing and castle pieces among them.

So `latch_root_flags` records the flags off the SOURCE root before any
replacement, and `collision_source` / `collision_triangles` read
`source_root_flags()`. The latch sits before the whole root-normalization
branch, so worn armor — which skips the swap entirely — is covered too. The
blocks themselves are simply dropped with the old root, and nothing
Morrowind-specific reaches the output.

### `NC` means "actors pass through", NOT "no physics"

Carrying the flag is only half the rule, because honoring it everywhere
breaks item pickup. OpenMW never skips shape generation for `NC`: it builds
the collision and then downgrades the object to `CollisionType_VisualOnly`
(`mwphysics/physicssystem.cpp:423`), which `collisiontype.hpp:20` leaves out
of `CollisionType_AnyPhysical`. The body exists; actors and projectiles just
do not test against it.

Skyrim has no equivalent collision class, so the closest translation —
shipping no `bhkCollisionObject` — costs more than it buys. Morrowind
activates an item through its REFERENCE, Skyrim through its collision, so a
bodyless MISC/APPA is unlootable. Of Arktwend's 526 flagged meshes, **20 are
named by item records** (every alembic/retort/calcinator, a kwama egg, a
redware pot, a muck shovel).

The gate is therefore a WHITELIST of record types that are placed scenery —
`fixture_plan.FIXTURE_TYPES` = STAT/ACTI/LIGH/CONT/DOOR, latched per mesh
exactly as `door_plan` latches doors. The record type is the authored answer
to "is this scenery?"; a mesh no fixture record names keeps its body whatever
extras it carries. Counts by type over the flagged set:

| Type | Flagged meshes | Honors `NC` |
|---|---|---|
| STAT | 178 | yes |
| ACTI | 115 | yes |
| LIGH | 98 | yes |
| CONT | 73 | yes |
| DOOR | 1 | yes |
| APPA / MISC / INGR | 20 | **no** — keeps its body |

Guarded by `tests/test_morrowind_nc_flag_survives_root_swap.py`.



<a id="morrowind-door-animation"></a>

## Morrowind doors: the swing lives in the ENGINE, not the mesh (2026-09-21)

**Code:** `asset_convert/nif/door_anim_morrowind.py`, `asset_convert/nif/door_plan.py`;
`animate_morrowind_door` is called from `_convert_roots` after
`attach_morrowind_collision`.

Converted Morrowind doors behaved as statics: activating one played the sound
and swapped the lock state, but the panel never moved.

### The two games disagree about where a door's motion is stored

Morrowind stores none of it in the mesh. `World::rotateDoor`
(`references/openmw/apps/openmw/mwworld/worldimp.cpp:1419`) rotates the
REFERENCE itself:

```cpp
float minRot = door.getCellRef().getPosition().rot[2];
float maxRot = minRot + osg::DegreesToRadians(90.f);
float diff = duration * osg::DegreesToRadians(90.f) * (state == Opening ? 1 : -1);
```

90 degrees about Z, over one second (`duration` is seconds and the rate is
90 deg/s), applied to every non-teleport door uniformly. A teleport door never
swings at all — `Door::activate` returns an `ActionTeleport` before reaching
the `// animated door` branch (`mwclass/door.cpp:184-200`).

Skyrim has no equivalent. Its doors carry `Open` and `Close`
`NiControllerSequence` blocks inside the NIF and the engine plays them by name.
Measured on the corpus: **0 of 76** converted Morrowind door meshes contain any
`NiControllerManager`, `NiControllerSequence`, `NiKeyframeController` or
`NiTransformController` block, and a byte scan of the 76 *source* TES3 meshes
finds no `NiKeyframeController` either — the animation was never there to lose.
So the sequences have to be synthesised.

### The sign of the rotation

Morrowind's `rot[2]` turns about the NEGATIVE Z axis. OpenMW builds the node
attitude as `osg::Quat(rot.z(), osg::Vec3f(0, 0, -1))`
(`mwrender/objectpaging.cpp:820`), so Morrowind's +90 deg is **-90 deg about
NIF +Z**. Vanilla Skyrim agrees independently: `farmhouseanimdoor01.nif` ends
its `Open` sequence at Z = -1.6057 rad (-92 deg), and `mrkdoor01.nif`'s two
leaves likewise open negative. The synthesised key is therefore -pi/2.

### The contract, read off vanilla

`farmhouseanimdoor01.nif` and `mrkdoor01.nif`:

| Piece | Value |
|---|---|
| Root controller | `NiControllerManager`, flags 76 (0x4C), `cumulative` false |
| Object palette | `NiDefaultAVObjectPalette` naming the hinge node and its geometry |
| Sequences | `Open`, `Close`; `start_time` 0.0, `stop_time` 1.0, `cycle_type` 2 (CLAMP), `frequency` 1.0, `weight` 1.0 |
| Text keys | `start` at 0.0, `end` at 1.0 |
| Controlled block | `controller_type` `NiTransformController`, `priority` 0, driven by one shared `NiMultiTargetTransformController` |
| Interpolator | `NiTransformInterpolator` whose `NiTransformData` has `rotation_type` 4 (XYZ_ROTATION_KEY) and Z keys only |
| Hinge node | `NiNode`, flags 142 (0x8E) — the physics-sync bit the keyframed body needs |
| Hinge body | `bhkCollisionObject` flags 137, `bhkRigidBodyT` motion system 4 (KEYFRAMED), layer 2 (SKYL_ANIMSTATIC), quality 1, solver deactivation 1, mass 0 |
| Root BSXFlags | animated + havok + complex |

Sound is NOT carried as a `Sound:` text key. Vanilla writes one, but Skyrim
also honours the record channel, and the converted DOOR records already carry
`SNAM`/`ANAM` resolved to sound descriptors, so the mesh channel would only
double it.

### The hinge is the mesh origin, because Morrowind's is

Morrowind rotates the reference, so the pivot is the REFR origin and the
authored hinge is wherever the mesh meets it. The converter re-pivots nothing:
turning about the mesh origin is exactly what Morrowind does.

<a id="a-door-turns-about-its-edge"></a>

### A door turns about its EDGE, so a centred panel gets no swing

Not every door mesh can be swung about that origin. A hinged door's panel lies
entirely to one side of its pivot; a panel wrapped AROUND the pivot would sweep
through its own frame, which is a revolving door, and neither game ships one.
Such a mesh has no hinge, and synthesising a rotation for it invents motion
that the source never had — in Morrowind a load door does not rotate at all,
because `Door::activate` returns its `ActionTeleport` first.

`hinge_offset` measures the panel's centre against its own half-width, **in the
ROOT's frame**, because that is where the synthesised `Door01` hinge is inserted
and where its sequences turn.

The frame is the whole point. `In_CI_door_01`'s shapes sit at translation
(59.2, −2.7) carrying geometry centred at (−59.2, 0.0): measured in each shape's
own space that is a ratio of 0.532, and an earlier version animated it — but the
two cancel at the root, where the ratio is 0.015. Synthesis re-parents every
child under one hinge at the mesh origin, so a swing built there sweeps the panel
through its own middle. 13 Morrowind doors were spinning in game for exactly
this reason.

| Mesh | Ratio at root | Verdict |
|---|---|---|
| `In_impsmall_door_01` | 1.005 | hinged |
| `Ex_t_door_01` | 0.641 | hinged |
| `Ex_nord_door_01` | 0.180 | no hinge |
| `Ex_common_door_01` | 0.181 | no hinge |
| `In_CI_door_01` | 0.015 | no hinge |

`strip_hingeless_swing` measures per DRIVEN NODE instead, since an authored
sequence turns a node the root cannot see: `DEMdoorAnim01Door` sits at (48.8,
2.9) with its panel beneath it, invisible from a root at the origin.

### The verdict is latched on the SOURCE mesh

`latch_source_hinge` runs in `convert_nif` immediately after `_read_source`,
before any pass alters the tree, and `animate_morrowind_door` reads that latch
rather than re-measuring. Measuring later disagrees with the authored geometry:
`strip_collision_nodes` runs first, and `Ex_nord_door_01`'s collision node is
half the mesh's width, so dropping it took the measured total from 195.4 to
114.5 and flipped the door from hingeless to hinged. Any measure relative to
the tree is unstable while passes add and remove siblings.

`strip_spinning_doors` runs only on the non-Morrowind branch, so it can never
remove the swing synthesis has just built.

Measured across the corpus at a 0.25 cutoff, **every door that swings is
hinged, with no exception**: 8/8 Morrowind, 18/18 Morroblivion, 66/66 Oblivion.
The 15 Oblivion non-teleport meshes that score below it are portcullises and
iron gates, which lift rather than turn and carry translation sequences, not
rotations. On the other side, 30 Morrowind and 53 Morroblivion door models are
centred on their pivot and get no synthesised swing.

Morroblivion re-exported those same centred Morrowind meshes with a 92 degree Z
rotation baked in, so its load doors visibly spin about their middle in game.
`Ex_common_door_01` scores 0.181 from either plugin — the identical mesh, the
identical verdict — so the authored geometry settles it without reference to
which plugin supplied it.

### Every hinged door mesh gets the sequences, including the load doors

Among hinged meshes a base is not reliably one kind or the other. Splitting the
139 DOOR bases by whether their REFRs carry `XTEL`: 27 bases are swing-only, 81
teleport-only and **23 are BOTH** — `In_velothismall_ndoor_01` alone has 438
plain and 516 teleport references. The mesh cannot encode that distinction, and
it does not need to: a Skyrim teleport door ignores its `Open`/`Close`
sequences, exactly as vanilla does. `orcdoorload01.nif` — a vanilla Skyrim LOAD
door — ships the sequences anyway, which settles it.



<a id="teleport-door-static-twin"></a>

### REJECTED: a per-reference static twin keyed on XTEL

An earlier fix read the swing as a property of the REFERENCE, since both source
games gate it there — `Door::activate` returns its `ActionTeleport` before
reaching the `// animated door` branch (`mwclass/door.cpp:183`), so an `XTEL`
reference never rotates, and Skyrim has no such gate. Because the same base
routinely serves both uses (`in_velothismall_ndoor_01`: 438 plain against 516
teleport), the decision could not sit on the base, so the build minted a twin
DOOR base per teleport-used model, naming a `*Static.nif` with the sequences
stripped, and `_refr_base_formid` retargeted teleport references to it.

It was removed. Rewriting REFR base pointers modifies Oblivion by construction
— 287 bases and 162 meshes — and Oblivion's doors were already correct. The
mechanism answered "which references must not swing" when the defect was
narrower: **which meshes cannot swing at all.** That is a property of the
geometry, so it is settled in the mesh by
[the hinge measurement](#a-door-turns-about-its-edge), no record-level
machinery and no `XTEL` read. A hinged door that also teleports keeps its
swing, which is what Oblivion ships: `AnvilDoorMCAnim01` is a hand-authored
teleport swing, as are 16 teleport-only and 18 mixed bases, with durations from
1.43s to 5.67s, multi-node drives, overshoot and settle, and double doors
turning opposite ways (`CDoor03`: −86.7 and +91.9 degrees).


## Morrowind surface materials
<a id="morrowind-surface-materials"></a>

**Code:** `asset_convert/nif/nif_materials_morrowind.py`

Morrowind authors no havok material, so every converted mesh collided as stone
— a wooden crate thudded like rock. The classification itself, and the proof
that no better signal exists, is in
[surface materials](tes4_export_morrowind.md#surface-materials); this section
covers only the two mesh-side traps.

### Sample BEFORE the upgrade

`attach_morrowind_collision` runs after `_convert_one_root`, which has already
replaced `NiTexturingProperty` with a `BSLightingShaderProperty`. Reading the
diffuse there would mean reading converted data, so `sample_materials` runs in
`run_morrowind_fixups` — pre-upgrade, on real TES3 blocks — and stashes the
result on the root as `_mw_havok_material`, the same pattern
`_mw_no_collision` already uses.

### Sample the RENDER geometry, never the collision shapes

A mesh with an authored `RootCollisionNode` collides with an **untextured
proxy**: its shapes carry no `NiTexturingProperty` at all, and the texture
lives only on the render geometry.

```
ex_common_plat_cent.nif
  collision shapes (RootCollisionNode)
     tris=10   <no NiTexturingProperty, 0 props>
  render shapes
     Tri Ex_common_plat_Cent 0   tris=4    Tx_wood_siding.tga
     Tri Ex_common_plat_Cent 3   tris=80   Tx_wood_docks_01.tga
```

That is 190 of 447 collidable vanilla meshes (43%). Sampling the collision
shapes scores those at 0% and drags whole static folders to 2-10%; sampling
render geometry instead takes them to 83.2%, ABOVE the auto-generated case
(74.3%), because a mesh that earned a hand-authored proxy is usually
substantial architecture with clear textures.

The proxy decides WHERE collision is, never what it is made of.

Measured over collidable meshes, area-weighted vote per root:

| Corpus | collidable | resolved |
|---|---|---|
| Morrowind (1,011 nifs) | 447 | 349 = 78.1% |
| Tamriel Data (HD) | 490 | 322 = 65.7% |

Shapes vote by triangle area so a one-triangle decal cannot outvote a wall.


## Morrowind specular
<a id="morrowind-specular"></a>

**Code:** `asset_convert/nif/nif_converter_morrowind.py::disable_specular`,
called from `_convert_nif` after the roots are converted.

Converted Morrowind meshes rendered "super shiny". Every one takes the shared
flat `default_n.dds` (Morrowind ships no normal maps), whose constant 64/255
specular mask is applied under `_set_material_defaults` (glossiness 80,
strength 1.0) with `SLSF1_Specular` set -- a coherent hard highlight on every
flat wall.

The authored indicator is the source engine itself: *"While NetImmerse and
Gamebryo support specular lighting, Morrowind has its support disabled"*
(`references/openmw/components/nifosg/nifloader.cpp:2892-2895`, which forces
the material specular to black regardless of `NiSpecularProperty`). Measured
on `ex_hlaalu_b_12.nif` / `ex_hlaalu_b_01.nif`: `NiMaterialProperty`
glossiness 0.0, specular (0,0,0), no `NiSpecularProperty`. So every
`BSLightingShaderProperty` on a 4.0.0.2 source gets `SLSF1_Specular` cleared;
glossiness and strength keep the vanilla defaults, exactly as vanilla's own
non-specular shapes do.


## Morrowind triangle flag
<a id="morrowind-triangle-flag"></a>

**Code:** `asset_convert/nif/nif_converter_morrowind.py::raise_triangle_flags`

`Has Triangles` does not exist in NIF 4.0.0.2. Morrowind writes the index array
unconditionally, so pyffi reads the triangles correctly but leaves
`has_triangles = False` on every shape. The field DOES exist at the Skyrim version
we upgrade to, where pyffi writes the array only when the flag is set — so the
converted mesh kept `Num Triangles` and shipped no indices at all.

Measured on the first build: **294 of 300 output meshes (98%)** had at least one
shape with `num_triangles > 0` and `len(triangles) == 0`. `base_anim.nif` alone had
26. The same scanner reported 0 mismatches on vanilla Skyrim meshes and on
Oblivion-converted meshes, so the reader was never in question.

The engine aborts the process on this. Captured with
`tools/live/crash_capture.py`: `c0000409` / `FAST_FAIL_INVALID_ARG`, subcode 5, from
`ucrtbase!invalid_parameter` — an `errno = 0x22` (EINVAL) bounds check in
`SkyrimSE+0x109a4e`. The caller copies `count * 2` bytes (u16 indices) into a
buffer sized from the real data, with a sibling `count * 3` stride for triangle
points: `rbx = 0x30` (48 bytes declared) against `r12 = 0x18` (24 vertices). No
CrashLogger log is produced, because a fast-fail bypasses the exception filter.

Emptiness is judged on the ARRAY, never on the flag — the same rule
`tri_reconstruct.py` already states in its docstring. A shape whose array is
genuinely empty is left to that module to rebuild or drop.


## Morrowind skin partitions
<a id="morrowind-skin-partitions"></a>

**Code:** `asset_convert/nif/nif_converter_morrowind.py::build_skin_partitions`

`NiSkinPartition` postdates NIF 4.0.0.2, so no Morrowind mesh has one. Skyrim's
renderer walks the partition for its bone and vertex mapping and dereferences
the null when it is absent:

`EXCEPTION_ACCESS_VIOLATION` at `SkyrimSE+0E552FA`, `mov r13, [rax+0x18]` with
`rax = 0`, on `furn_bannerd_wa_shop_01.nif` (an `NiSkinInstance` over `Bone02` /
`Bone03`). The call chain is the SAME one the earlier triangle-flag fast-fail
took — `+0E561B3`, `+0E53D52`, `+0E03602`, `+0206790` — so fixing the indices
simply moved the failure one step deeper into the same mesh load.

Measured, at the point of the crash:

| corpus | skinned instances | with partition |
|---|---|---|
| vanilla Skyrim | 246 | 246 (100%) |
| Oblivion output (works in game) | 1,630 | 1,630 (100%) |
| Morrowind output | 325 | **0** |

Oblivion meshes already ship partitions, which is why nothing in
`asset_convert/` ever built one.

**Two contracts, both measured, both easy to get wrong:**

1. **The partition hangs off the `NiSkinInstance`, not the `NiSkinData`.** In 146
   vanilla skinned instances it is on the instance and in 0 on the data.
   pyffi's `update_skin_partition` writes it to the DATA block, so it is moved.
2. **It must be built AFTER the version upgrade.** `Data.write` rebuilds
   `self.blocks` from the roots via `_makeBlockList`, following `get_refs()`;
   at 4.0.0.2 the schema has no `skin_partition` ref on the instance, so a
   partition built pre-upgrade is unreachable and silently never written.
   Appending it to `data.blocks` by hand does not help — the writer discards
   that list. `num_weights_per_vertex = 4` in 300 of 300 vanilla partitions.

## Morrowind quadratic UV keys
<a id="morrowind-quadratic-uv-keys"></a>

**Code:** `asset_convert/nif/shaders.py::_uv_group_to_float_data`

A `NiUVData` key group copied straight into a `NiFloatData` shipped a block the
engine could not parse, and the mesh loaded as the missing-model red triangle.

`nif.xml` makes a `Key`'s `Forward`/`Backward` fields conditional on `#ARG#`,
supplied by `KeyGroup`'s `arg="Interpolation"`. pyffi fixes the element layout
when `Array.update_size()` allocates and never re-evaluates that arg, so the
keys stay LINEAR-shaped (8 bytes) whatever `interpolation` is set to, and in
whatever order. Measured on a freshly built `NiFloatData` with `num_keys = 9`,
`interpolation = 2`:

| order of assignment | `keys[0].arg` | `get_size` | bytes written |
|---|---|---|---|
| interpolation before `update_size` | 1 | 80 | 80 |
| interpolation after `update_size` | 1 | 80 | 80 |
| interpolation, then re-`update_size` | 1 | 80 | 80 |

So the block was written 80 bytes with `interp = 2` in its own payload. The
engine sizes a quadratic float key at 24 bytes and computes `8 + 9*24 = 224`,
then reads 144 bytes past the block and rejects the file. Confirmed on the
shipped output: `declared=80 num_keys=9 interp=2 implied=224` in
`tr_ex_velothi_temple03.nif` and `tr_ex_nec_w_01.nif` (block 21 in both).

The tangents are therefore unrepresentable through this path, and the copy loop
that tried to carry them was dead — `hasattr(dst, 'forward')` is never true.
Writing the group as LINEAR makes the declared type match the bytes that are
actually emitted. The curve keeps every key's time and value; only the
tangents, which were already being dropped silently, are gone.

## Morrowind legacy node types
<a id="morrowind-legacy-node-types"></a>

**Code:** `asset_convert/nif/nif_converter_morrowind.py::convert_legacy_nodes`

Three node types reached the output that Skyrim's engine has no RTTI for. A
block type the engine cannot instantiate rejects the whole file, which is the
missing-model red triangle. Census over `references/Skyrim Meshes` (17,216
files) against the Morrowind output tree:

| block type | vanilla Skyrim | Morrowind output | disposition |
|---|---|---|---|
| `NiBSAnimationNode` | **0** | 512 | rewrite as `NiNode` |
| `NiLODNode` | **0** | 499 | collapse to nearest LOD level |
| `RootCollisionNode` | **0** | 208 | strip (collision already consumed) |
| `NiSwitchNode` | 88 | 1,413 | **legal — keep** |

`NiSwitchNode` is the control: it is the same family and it survives, so the
rule is per-type RTTI, not "legacy nodes are bad".

`nif.xml` corroborates each: `NiBSAnimationNode` is `module="BSLegacy"`
`until="V10_0_1_0"` and inherits `NiNode` adding no fields, so a plain `NiNode`
is lossless. `RootCollisionNode` is `versions="V4_0_0_2"` — Morrowind only.
`NiLODNode` inherits `NiSwitchNode` but stores its levels differently per
version: `LOD Center` plus inline `LOD Levels` `until="10.0.1.0"`, and a
`NiLODData` ref `since="10.1.0.0"`. Stamping version 20.2.0.7 over the 4.0.0.2
form leaves that ref unwritten, and there are **0** `NiLODData` blocks in
vanilla Skyrim, so the ref can never be satisfied. The authored children are
the LOD levels — `tr_flora_sh_bush_06.nif` carries `blend` over 0–500 and
`test` over 500–∞ — so keeping child 0 keeps the level that renders nearest.

### The `RootCollisionNode` strip missed nested nodes

`attach_morrowind_collision` already stripped a `RootCollisionNode`, but only
via `find_collision_node`, which searches DIRECT children of
`source_children_owner(root)`. 208 output files kept one because it sits
deeper. Measured on the source meshes:

| mesh | `RootCollisionNode` depth | found by the direct-child search |
|---|---|---|
| `tr_ex_velothi_temple03.nif` | 1 | yes |
| `tr_ex_nec_w_01.nif` | 1 | yes |
| `tr_ex_HM_blc_rail_02.nif` | 2 | **no** |
| `tr_flora_drumpear_02.nif` | 3 | **no** |

A nested one loses collision as well as shipping the illegal block, since
`collision_source` reports "no collision node" and falls back to colliding with
the render mesh. The strip is therefore by block type over the whole tree,
matching how the node is detected everywhere else in this file, while the
engine-faithful direct-child search still decides what collision is BUILT.

### 🛑 `data.blocks` is DERIVED — never sweep a tree edit over it

A first pass over `data.blocks` cleared most of the corpus but left a
measured residue: 54 `NiBSAnimationNode`, 98 `NiLODNode`, 27
`RootCollisionNode` and 1 `NiBSParticleNode` files still shipped one after a
full `--meshes-only` rebuild. `data.blocks` is recomputed from the root graph,
so it is a SNAPSHOT, and it breaks a sweep in two distinct ways:

1. **A replacement is not in it.** `convert_legacy_nodes` rewrites a node by
   building a new `NiNode` and repointing every link with pyffi's own
   `replace_global_node`. When a legacy node's PARENT is itself replaced, the
   parent's new copy holds the old child, and it is not in the stale list — so
   the child's own swap never reaches it. `tr_f_js_ventshroom_01.nif` is the
   case: its ROOT is a `NiBSAnimationNode` and its `NiLODNode` survived.
   Fix: apply the swaps over `data.blocks` PLUS the new nodes.
2. **A detached node's sibling is skipped.** `tr_ex_ind_build01.nif` and
   `tr_ex_mh_building06.nif` carry TWO `RootCollisionNode` siblings under one
   root. `attach_morrowind_collision` detaches one; the recomputed
   `data.blocks` then omits it, and the parent is never revisited for the
   second. Fix: `strip_collision_nodes` walks the live root graph.

The general rule: a pass that MUTATES the graph must walk the graph it is
mutating, not a list derived from it. Verified by converting each named mesh
and asserting zero illegal block types, and by
`TestMorrowindLegacyNodes::test_no_dangling_reference_to_the_rewritten_node`,
which fails on the exact residue.

### Source census (`export/Tamriel Data (HD)/meshes`, 30,309 files)

`nif_block_scan.py` only parses the Skyrim header — it reports 30,309
header-parse failures on a 4.0.0.2 tree, so its zero counts there are
meaningless. Counted by raw block-type-table scan instead:

| block type | blocks | files |
|---|---|---|
| `RootCollisionNode` | 14,096 | 14,089 |
| `NiSwitchNode` | 1,361 | 1,361 |
| `NiBSAnimationNode` | 796 | 492 |
| `NiBSParticleNode` | 623 | 427 |
| `NiLODNode` | 489 | 381 |
| `NiFltAnimationNode` | 0 | 0 |

`NiBSParticleNode` is why the rewrite list is not just the three types the
reported meshes happened to carry.

### Collapsing `NiLODNode` is lossless at the near level

Over the 120 source files sampled, every one of the 154 LOD nodes has
`lod_levels[0].near_extent == 0.0`, so child 0 is always the level that renders
at the camera. Child-count histogram: 77 nodes have 1 child (nothing to drop),
76 have 2, 1 has 3.

## <a id="fo3fnv-furniture-position-refs"></a>FO3/FNV furniture position refs

**Code:** `asset_convert/nif/furniture_markers_falloutnv.py`

`BSFurnitureMarker.position_ref` names a `furnituremarkerNN.nif`; Oblivion
ships 8 of them, FO3/FNV ship 22. The extra refs fell through the TES4 heading
table's default, and refs 5 and 6, being under 10, were read as beds although
they seat. `FALLOUT_REF_HEADING` carries each FO3-only ref's heading, taken
from the marker NIF's own lateral offset, and `FALLOUT_SIT_REFS` lists the
refs below 10 that seat rather than sleep.

## <a id="already-a-bsfadenode"></a>Roots that are already a BSFadeNode

**Code:** `_normalize_fade_root` in `asset_convert/nif/nif_converter.py`

The furniture-marker carry, superseded-marker drop and PRN conversion ran
inside the NiNode-to-BSFadeNode root swap, so a source whose root is already a
BSFadeNode (every FO3/FNV mesh) skipped all three. `_normalize_fade_root`
applies the same passes to such a root.

## <a id="pyffi-log-capture"></a>PyFFI log capture: the toaster resets the level

**Code:** `_PyFFICapture` / `pyffi_capture_init` in `asset_convert/nif/nif_batch.py`,
`_add_tangent_space` in `asset_convert/nif/nif_converter.py`

A full Oblivion.esm mesh run captured **188,858** messages, of which **183,929
(97.4%)** were PyFFI progress chatter and only **4,929 (2.6%)** were real.

The chatter is emitted at INFO, not WARNING. It reached a handler installed at
WARNING because `Toaster.__init__` calls `_update_options`, which with the
default `verbose=1` mutates the **shared** logger:

```python
# pyffi/spells/__init__.py
elif self.options["verbose"] == 1:
    logging.getLogger("pyffi").setLevel(logging.INFO)
```

`_add_tangent_space` builds a toaster per NIF, so every mesh silently undid the
`setLevel(WARNING)` that `pyffi_capture_init` had just set. Measured against
the real call path:

```
after pyffi_capture_init: pyffi.level = 30 (WARNING)
after _NifToaster():      pyffi.level = 20 (INFO)
```

`Toaster.msg()` logs via `logger.info`, and `msgblockbegin` brackets every
visited branch in `~~~ ... ~~~`, which is why `type_~~~` (93,992) and
`type_adding` (47,777) dominated the pile: they are recursion markers from
`SpellAddTangentSpace.recurse()`, not diagnostics.

Two defences, both applied: the toaster is constructed with `verbose=0`, and
`_PyFFICapture` carries its own WARNING level so no future PyFFI call can let
INFO records through by lowering the logger.

Consequently the categoriser only ever sees WARNING and above. The
`spell_marker_*`, `tangent_space_added` and six `skin_part_*` categories keyed
off INFO-only strings and were removed. `nan_in_vertices` required both `nan`
and `vert` in one message; the only NaN sources are the three `float_to_int
converted ...` lines in `pyffi/utils/mathutils.py`, none of which say "vert",
so every NaN lands in `nan_generic` and the unreachable bucket was removed.

Note that `block_size_check` and `End of file not reached` are emitted at
ERROR, not WARNING -- they are the highest-signal messages for `nif.xml`
mismatches, and they are captured because ERROR outranks WARNING.

## <a id="patch-16-sse-havok-layouts"></a>Patch 16: SSE (BSVER 100) Havok collision layouts

**Code:** `asset_convert/nif/pyffi_monkey_patch.py::_install_sse_havok_layouts`.

PyFFI 2.2.3 predates Skyrim SE and has no BSVER 100 concept at all. That single
fact produced three separate-looking crashes in the Havok blocks, so they are
one patch, not three. `references/nifxml/nif.xml` is authoritative for every
layout below — none of this was inferred from a stack trace.

### The enumeration defect

`HavokFilter` and `HavokMaterial` each declare three mutually exclusive
game-gated variants:

```xml
<field name="Layer" suffix="SK" type="SkyrimLayer" vercond="(#VER# == 20.2.0.7) #AND# #BS_GT_FO3#" />
<field name="Material" suffix="SK" ... vercond="(#VER# == 20.2.0.7) #AND# #BS_GT_FO3#" />
```

PyFFI flattens the Skyrim variant to a literal `user_version_2 == 83`, which
**enumerates** the version instead of bounding it. SSE files carry BSVER 100, so
no variant matches and the field reads **zero bytes**. A sweep over every
`_attrs` entry in raw PyFFI, evaluating each `vercond` at BSVER 83 and again at
100, finds exactly two fields live at 83 and dead at 100 —
`HavokColFilter.layer` and `HavokMaterial.material`. The fix bounds all three
variants (`< 16`, `== 34`, `>= 83`) rather than enumerating them.

PyFFI's `Expression` evaluates only a SINGLE comparison; a multi-term `&&`/`||`
condition silently returns False. That is why the Oblivion variant does not
catch the fallthrough, and why each replacement must stay one comparison.

### The unconditional-width defect

The same field declared at two widths is the defect inverted:

```xml
<field name="Body Flags" type="uint"   vercond="#BSVER# #LT#  76" />
<field name="Body Flags" type="ushort" vercond="#BSVER# #GTE# 76" />
```

PyFFI flattens that into two **unconditional** attributes, `unknown_int_9`
(UInt) then `unknown_int_91` (UShort) — both carry `ver1=None ver2=None
cond=None`. Every SSE mesh therefore reads 6 bytes where it should read 2.

### Why the errors name the wrong block

A short or long read shifts every later field, and the failure surfaces
downstream:

```
Reading <struct 'bhkRigidBody'> failed ... unpack requires a buffer of 4 bytes
Reading <struct 'bhkConvexVerticesShape'> failed ... array too long (2147483648)
```

`0x80000000` is the documented default of `bhkWorldObjCInfoProperty`'s
`Capacity and Flags`; a misaligned read lands on it and reports it as an array
count. **That constant is the signature of this whole class of bug** — it names
the array, never the field upstream that actually shifted.

### Verifying a layout instead of guessing it

The arithmetic is checkable against the file's own block table.
`bhkRigidBody` in `CasExFreeSmDoor02.nif` declares **250 bytes**; summing the
`nif.xml` chain gives the same 250:

| part | bytes |
|---|---|
| `bhkWorldObject`: Shape ref + `HavokFilter` + `bhkWorldObjectCInfo` | 28 |
| `bhkEntityCInfo` | 4 |
| `bhkRigidBodyCInfo2010` | 212 |
| `Num Constraints` + `Body Flags` (ushort at BSVER ≥ 76) | 6 |
| **total** | **250** |

`bhkWorldObject`'s `Unknown Int` is `until="10.0.1.2"` and must NOT be counted;
including it gives 254 and is the easy way to be four bytes wrong. PyFFI's own
live-attribute walk totalled 457 for the same block, which is how far its
flattened view had drifted.

A flattened list holding each attribute twice is NOT a defect: 51 classes
inherit `_attrs` from a `_`-prefixed customizer base, so `flat = 2 x own` is
normal and those classes parse correctly. Chasing that duplication is a dead
end.

### Measured

Before the patch, six vanilla Skyrim clutter meshes placed in Bruma interiors
yielded **no collision at all**, silently — the extractor returned `None` and
the objects simply had no walls: `Clutter\Barrel01.NIF`,
`Clutter\Common\StrongBox01.nif`,
`Clutter\Containers\MiscSackLargeFlat01.nif` / `03`,
`Clutter\Upperclass\UpperChest01.nif`, `Furniture\Noble\NobleWardrobe01.nif`.

Over the 104 meshes placed by the Bruma navmesh corpus cells, parsing the same
set before and after the `HavokFilter` fix moved **103 -> 104 with nothing
lost**; the mesh gained was
`DLC01\Dungeons\Castle\LgHalls\CasExFreeSmDoor02.nif`, the last un-parseable
mesh in the corpus.

This is distinct from Patch 8, which adds SSE *geometry* (`BSTriShape`) read
support and leaves the Havok blocks alone — a mesh can parse its geometry
perfectly and still lose every collision shape here.

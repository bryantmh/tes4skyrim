# asset_convert/nif_converter.py — NIF conversion

**Code:** `asset_convert/nif_converter.py`, `asset_convert/sse_nif.py`, `asset_convert/pyffi_monkey_patch.py`, `asset_convert/skyrim_assets.py`

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
  Run: `python -m asset_convert.nif_converter <src_dir> <dst_dir>` (worker pool is automatic: cpu_count-3; there is NO --workers flag).
- **NIF conversion stats**: 8032 source NIFs from Oblivion BSAs. 7380 v20 files converted (91.9%). 650 v10/v4 files copied as-is. 2 remaining parse errors (magic effect particle NIFs).
- **NIF bhk conversion details** (Session 19+):
  - bhkRigidBody/T: Oblivion=236+n*4, Skyrim=250+n*4. Key: two `Unknown Int 2` fields with different vercond (UV2>34 vs UV2≤34). Bytes [44:52] need rearrangement, not just passthrough. Translation/Mass/Friction are at fixed offsets (52, 180, 192 in Oblivion, 52, 180, 200 in Skyrim)
  - Crash signature: SkyrimSE.exe+0A882E6 reading from 0xFFFF* addresses = corrupted bhkRigidBody pointers from misaligned fields
  - bhkNiTriStripsShape: Collision NiTriStripsData must NOT be renamed to NiTriShapeData (template type mismatch). Writer must write strips format, not triangulated.
  - Constraint descriptors (RagdollDescriptor, LimitedHingeDescriptor, HingeDescriptor, MalleableDescriptor): UV2≤16 vs UV2>16 field REORDERING is handled by PyFFI's ver1/ver2-guarded duplicate attrs (same attr names in both layouts, so values carry over automatically on read-Oblivion/write-Skyrim).
  - **Constraint conversion (rewritten 2026-07-04, `collision.py::scale_constraint_pivots`)**: the old code only fixed bhkLimitedHingeConstraint; every other descriptor shipped UNSCALED pivots (10× too far, e.g. UpperScales01 ragdoll pivot 3.57 vs vanilla-range 0.36) and zeroed Skyrim-only basis fields. Now for ALL descriptor types: pivot_a/pivot_b ×0.1 (stiff-spring `length` and prismatic min/max_distance too — they're lengths); RagdollDescriptor `motor_a/motor_b` = twist × plane (they are the 3rd column of the constraint's orthonormal basis, NOT motor params — zero = singular basis; handedness verified on vanilla desecratedimperial.nif); HingeDescriptor Skyrim-only `axle_a` = perp_a1 × perp_a2 and `perp_2_axle_in_b_1/2` = Gram-Schmidt complement of axle_b (plain hinge has no limits so any orthonormal complement is valid); inertia ×0.1 rescale deduped per body (the scales crossbar sits in 3 constraints — was being triple-scaled). Vanilla Skyrim constraint census (17,216 meshes): LimitedHinge 158, Ragdoll 59, Hinge 3, StiffSpring 2, **Malleable 0, Prismatic 0** → bhkMalleableConstraint is demoted to a plain constraint of its inner SubConstraint type (`_demote_malleable_constraints`; strength/tau/damping dropped); bhkPrismaticConstraint (Oblivion arrows only) is kept best-effort with a note that vanilla never ships it. Oblivion source census: LimitedHinge 278, Ragdoll 60, Malleable 21, Prismatic 10, Hinge 4, StiffSpring 3.
  - **KNOWN REMAINING bhkRigidBodyT+CMS violations (2026-07-04)**: 5 converted ANIMATED meshes still ship the forbidden pair (dungeons\ayleidruins\interior\traps\artrapspikepit01, dungeons\caves\cdoor03, dungeons\sewers\sewertunneldoor01, oblivion\clutter\traps\citadelhall3wayspiketrapbroken, oblivion\gate\obliviongate_simple) — keyframed child-node collision can't be demoted by the static bake pass; needs its own fix. ~100 speedtrees/ shrub+tree NIFs also contain the pair but are pre-made Skyblivion assets copied verbatim (not produced by our converter). Find them with `python tools/nif/nif_block_scan.py <dir> --has bhkRigidBodyT --has bhkCompressedMeshShape`.
  - `asset_convert/mopp.py::walk_mopp()` is a full MOPP VM symbolic walker (PyFFI's parse_mopp opcode table + Skyrim-era opcodes: 0x52 TERM24, 0x29-0x2B DOUBLE_CUT24, 0x70 CHUNK_JUMP32), validated clean against 400 vanilla meshes. CLI: `python tools/validate/mopp_validator.py <nif_or_dir> [--verbose|--summary|--histogram|--workers N]` (validates walk cleanliness AND exact terminal-key-set == shape-key decode). Vanilla opcode set observed: 0x01-0x06, 0x09-0x0B, 0x10-0x1C, 0x20-0x28 (0x29-0x2B rare), 0x30-0x53 — never 0x07/0x08/0x70; emit only these.
  - **MOPP_RL.exe is GONE (2026-07-03): all mesh collision is built by `asset_convert/cms_builder.py`**. History: MOPP_RL's chunked bytecode (0x70 chunk jumps, PC engine mis-executes) was first dechunked (`mopp.py::dechunk_mopp`), then its bytecode was replaced wholesale with Havok-bridge output — and the intermittent CTD STILL persisted (crash `SkyrimSE.exe+07D4C4B` fn 43870, runaway `hkpAllCdPointTempCollector` scan → EXCEPTION_STACK_OVERFLOW; Collision Sentinel: `CULPRIT ... key=0xFFFFFFFF` on the same meshes). Root cause was never the bytecode (see bhkRigidBodyT bullet below). MOPP_RL, its template.nif, and the dechunk fallback are all removed from the pipeline; `dechunk_mopp` remains in mopp.py for forensics only.
  - **CMS collision is built in pure Python + real Havok (2026-07-03)**: `cms_builder.py::build_cms_collision(tris_hu, sk_material_crc, NifFormat)` builds the whole bhkMoppBvTreeShape→bhkCompressedMeshShape→bhkCompressedMeshShapeData chain from a triangle soup: bpi=17/bpw=18, error=0.001, one identity bhkCMSDTransform, chunk = spatial bucket (split until extent <60 hu, ≤2000 tris), chunk translation = bucket min corner, u16 offsets = (v−min)×1000, triples-only indices (num_strips=0 — engine key decode identical to strips, `key=(ci+1)<<18|offset`), tris larger than the u16 span → big tris. MOPP bytecode + TWO_SIDED welding come from `external/mopp_bridge/dovah_hkp_mesh_mopp_bridge.exe` (Havok's real `hkpMoppUtility::buildCode`, chunk subdivision off, terminal keys self-validated by Havok's find-all-keys VM) — bridge input is `decode_cms()` of the freshly built block so MOPP/welding are computed over the exact quantized geometry the engine will decode. Welding u16 goes at the tri's first-index slot in chunk `indices_2` (= key offset); big-tri welding in `unknown_short_1`. Output re-verified in Python (walk clean + keys == `predict_keys`). Constants mirrored from vanilla: CMS radius=0.005, unknown_float_1=0.005, scale vec (1,1,1,0), data unknown_int_3=1, chunk unknown_short_1=0xFFFF, material layer=1. Wired in `collision.py::_rebuild_mesh_collision` (handles strips/packed/stale-Oblivion-MOPP sources; strips verts are GAME units → ÷70 to Skyrim hu; packed verts ×0.1). Fallback when the bridge fails: bare `_packed_from_tris` (no MOPP; packed data verts are stored ×10 hu = 1/7 game scale). NaN-vert tris are filtered before building.
  - **The MOPP bridge exe** came from inside `tools/DovahNifWorkbench_v6_47.exe` (PyInstaller onefile; payload `backend_exact_mopp\dovah_hkp_mesh_mopp_bridge.exe` + full C++ source `native_hkp_mesh_mopp_bridge/`, re-extractable by parsing the CArchive TOC at the `MEI\014\013\012\013\016` cookie). CLI: `--input in.json [--output report.json] [--no-stdout]`; input JSON `{"vertices":[x,y,z,...], "triangles":[a,b,c,...], "shape_keys":[k,...]}` (keys optional, must be unique); report has `mopp_origin`, `mopp_scale`, `mopp_data_hex`, `welding_info` (TWO_SIDED, per source tri), `mopp_keys_match_shape_keys`. GUI batch mode is NOT needed — the exe is called per-shape by cms_builder.py (`run_mopp_bridge`).
  - **CMS shape-key encoding (validated 200/200 vanilla meshes: walked MOPP key set == predicted set — `asset_convert/cms.py::decode_cms/predict_keys`)**: chunk tri key = `(chunk_idx+1) << bitsPerWIndex | winding << bitsPerIndex | first_index_offset` where the offset is the tri's first index position in the chunk's indices array; strips yield sliding-window tris (winding = window ordinal parity within the strip), then remaining indices are independent triples (winding 0, stride 3); big tris = part 0, key = big-tri index. Chunk vertex = chunk.translation + transform.translation + u16/1000 (rotate by transform quat if non-identity). PyFFI 2.2.3 field quirks: chunk welding array = `indices_2`, big-tri welding = `unknown_short_1`, big-tri fields `triangle_1/2/3` index into `big_verts`.
  - **PyFFI parse_mopp 0x0B (TERM_REOFFSET32) is WRONG** ("unsure about first two arguments" — reads only operand bytes 3-4): the operand is a full 32-bit big-endian value that SETS the terminal offset, and Skyrim CMS keys carry the chunk part in the HIGH bytes (0x00040000 = chunk 0). With the 2-byte read, every terminal after a 0x0B loses its chunk part — this made valid keys look like out-of-range "big tri" keys (a red herring chased for hours; vanilla showed the identical false pattern, which is what exposed the walker bug). Fixed in `walk_mopp`. Welding values legitimately span the full u16 range incl. ≥0x8000 and 0xffff — NOT a corruption signal (vanilla does the same).
  - `bhkCompressedMeshShape.target` must point to the BSFadeNode root (identity transform). Static collision MUST be on the root BSFadeNode — having bhkCollisionObject on a child NiNode causes STACK_OVERFLOW in Skyrim's `hkpCollisionDispatcher`.
  - **bhkRigidBodyT + CMS/MOPP = intermittent CTD — THE AnvilCastleGreatHall root cause (2026-07-03)**: vanilla Skyrim NEVER pairs a transformed rigid body with CompressedMesh collision — **0 of 6,341 vanilla CMS meshes contain bhkRigidBodyT** (checked by binary grep — block type names are plaintext in NIF headers). Shipping one exercises an engine path Bethesda never tested: queries intermittently resolve to HK_INVALID_SHAPE_KEY (Collision Sentinel `key=0xFFFFFFFF`) → runaway `hkpAllCdPointTempCollector` scan (Sentinel EVENT `b=129` vs the 128-slot stack collector) → EXCEPTION_STACK_OVERFLOW at `SkyrimSE.exe+07D4C4B`. Every Sentinel CULPRIT was a rotated-root mesh whose wrap pass produced bhkRigidBodyT+CMS ("diagonal/curved architecture" pattern). This explains all earlier observations: identity-body configs never crashed (only had rotated collision); transformed-body configs (bodyT OR collision on rotated child node) crashed ~50%. Replacing the MOPP bytecode alone did NOT fix it — the bytecode was never the problem.
  - **Root rotation wrap + collision (final design 2026-07-03)**: when the wrap pass zeroes the root transform L=(R,T), `bake_node_transform_into_body()` still composes bodyT' = L ∘ bodyT (in Oblivion hu; PyFFI `m_ij` names are the TRANSPOSE of the engine's column-vector matrix; rotation is QuaternionXYZW; ×0.1 rescale happens in `_convert_collision`). But for MESH collision the transform never reaches the file: `_bake_body_transform_into_tris()` applies the final bodyT to the triangle soup and DEMOTES the body back to a plain identity bhkRigidBody (class swap) before `build_cms_collision` runs — the output matches vanilla exactly (identity plain body, geometry in the world frame). Collision stays on the root BSFadeNode; CMS target = root. Regression test: `TestCollisionTargetPointsToRoot::test_static_collision_stays_on_root_when_wrapped` (asserts plain identity body + decoded CMS centroids match the source collision in the L∘bodyT frame within quantization — catches conjugate/transpose convention errors). Primitive shapes (convex/box/capsule, incl. constrained sign bodies) legitimately keep bhkRigidBodyT — vanilla does too.
  - **NaN geometry = silent cell-load CTD, NO crash log (2026-07-04, the AnvilMagesGuild/AnvilCastlePrivateQuarters root cause)**: some Oblivion source meshes ship non-finite floats in RENDER geometry (anvildooruc02.nif: 9 NaN UVs; middlecandlestickfloor03fake.nif: 2 NaN UVs — exactly one such mesh in each crashing cell, found by intersecting `tools/esm/cell_meshes.py` output with a `tools/nif/collision_sanity.py --geometry` sweep). Oblivion's renderer tolerated them; SSE dies at cell load WITHOUT writing a crash log (fail-fast, not a loggable exception) — collision was never involved. Fixed by `_sanitize_geometry_data()` in nif_converter.py (runs right after `_resolve_palette_strings`, BEFORE tangent computation/skin retarget so NaNs can't propagate): NaN UVs→0, NaN verts→finite centroid (+ bound-sphere recompute), NaN normals/tangents→+Z, NaN vertex colors→1. NOTE: the PyFFI warning summary from a full conversion run showed `nan_in_vertices: 155` — other meshes in the tree carry NaN too and previously shipped unsanitized; a full mesh reconversion (pipeline now sanitizes) or a `collision_sanity.py --geometry` sweep of output finds/fixes the rest.
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
   - **Path rewriting (`_rewrite_tex_path`) must normalise separators FIRST** (fixed 2026-07-27). Oblivion NIFs mix `/` and `\`, sometimes in one file. Testing only for a backslash `'textures\'` prefix let `textures/lowres/foo.dds` fall through and come out as `Textures\tes4\textures/lowres/foo.dds` — a path resolving to nothing, so the mesh renders untextured and the LOD tiles built from it reference 100 nonexistent textures. 96 Morrowind_ob source NIFs hit this; **zero Oblivion.esm ones**, which is why it stayed hidden.
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
- **A lighting shader over UV-LESS geometry (fixed 2026-08-09, same session — real defect, but NOT the red-triangle cause)**: this was fixed first on the theory that it caused the red triangle; it did not (the user re-tested and the triangle remained — the mesh was failing to LOAD, which is the `NiFlipController` bug above, and a malformed shader would garble a shape rather than replace it with the placeholder). Keep the fix — the state is still vanilla-impossible — but do not credit it with the red triangle. 12 shapes shipped a `BSLightingShaderProperty` over geometry with `num_uv_sets == 0` and no tangents. That shader *always* samples a diffuse texcoord and reads the tangent basis for its normal map, so with neither stream present it reads past the vertex buffer and renders as an untextured red shard. These are Oblivion **helper volumes** (particle emitter sources, spawn/effect proxies) that Oblivion hides with **bit 0 of the node flags**, but `_process_geometry` did `ts.flags = NIF_FLAGS`, clobbering the authored hidden bit and un-hiding every one of them. Two earlier narrow workarounds existed for this same clobber — the `EditorMarker` name-prefix strip and the `NiPSysMeshEmitter.emitter_meshes` hiding pass — and **neither reaches a helper the emitter does not link**, which is why these survived. Three fixes: (1) preserve the authored hidden bit in `_process_geometry`; (2) `_apply_rest_visibility` only read *keyframed* interpolators and so skipped every **data-less `NiBoolInterpolator`**, whose constant `bool_value` IS the rest state — this mesh drives all 30+ vis-controlled nodes that way, so the meteors/tendrils rendered from cell load; (3) a final safety net that strips the shader and hides any shape still left lit-but-UV-less. **Vanilla census (373 shapes, `references/Skyrim Meshes`): ZERO pair a lighting shader with 0 UV sets** — the 54 UV-less vanilla shapes are either `BSEffectShaderProperty` (45; that shader needs no tangents) or carry no shader at all (9, 8 of them hidden). Detect with a converted-output scan for `num_uv_sets == 0` + `BSLightingShaderProperty`; 0 after the fix. The same fix also cleared `obliviongate_simple` (10), `obliviongate_forming` (9) and `oblivionwargateani02` (6) untargeted, so treat any lit UV-less shape as this bug rather than patching the mesh. **Lesson for the next session: "renders as a red triangle" means the engine REJECTED THE FILE AT LOAD — go straight to block-level structural validation, not to shader/geometry inspection.** A shader or vertex-stream defect garbles a shape; only a load failure substitutes the placeholder.
- **`NiPSysMeshEmitter.emitter_meshes` is a SECOND link to geometry and must be remapped when NiTriStrips→NiTriShape (fixed 2026-07-27, the RED-TRIANGLE bug)**: `se11sheopooffx.nif`, `se01waitingroomwalls.nif` and `palacefont01.nif` rendered as Skyrim's red missing-mesh placeholder — the engine failed the whole NIF at load. `_walk_node` converts strips by writing the replacement back into the **parent's `children` array**, but a mesh emitter references its source geometry through `emitter_meshes`, which is not a children array and was never rewritten. The orphaned `NiTriStrips` stayed reachable through that link, so PyFFI happily re-serialized it, leaving raw Oblivion strips in a Skyrim file. **Skyrim has no NiTriStrips renderer** (vanilla census: **107/107** emitter meshes across all 256 `NiPSysMeshEmitter` blocks are `NiTriShape`; the 21 stray NiTriStrips in the whole vanilla tree are all `bhkNiTriStripsShape` collision), so the load fails outright. Fix: extend the existing `_block_map` fixup (which already repaired `NiDefaultAVObjectPalette`) to walk every `NiPSysMeshEmitter` and remap each `emitter_meshes[i]`. Detect it with a converted-output scan for surviving `NiTriStrips`, or for emitter meshes whose class is not NiTriShape — both are 0 after the fix. **The particle CONVERSION itself was never at fault here** — fire and other psys meshes were always fine; this is purely a dangling-reference bug in the strips rewrite, so look for the second link rather than re-auditing the modifier chain.
- **BSXFlags bit 0 (Animated) is REQUIRED or particles NEVER TICK — THE final fire-invisibility root cause (fixed 2026-07-05)**: without BSX bit 0x01 on the root, the engine never updates the mesh's time controllers, so emitters never fire — the file is perfectly valid but the fire is invisible. Census: **399/400 vanilla particle meshes set bit 0** (sole exception: a trailer camera rig); collisionless particle meshes use plain BSX=1 (also 0x201/0x221 with external-emit/editor bits). Two converter gaps caused this: `_add_bsx_flags` (a) early-returned when the root had NO collision (fireopensmall loses its collision → no BSXFlags at all), and (b) detected "animated" only via NiControllerManager on the ROOT — particle controllers live on the NiParticleSystem, so even collision-bearing fire got 0x82 static. Fix: `_tree_is_animated()` (any NiParticleSystem, or any block with a controller, anywhere in the tree) → collisionless+animated gets BSX=1; collision values get bit 0 OR'd in (0x82→0x83, 0xC2→0xC3 — both appear in vanilla census); `_convert_flame_nodes` now CREATES a BSXFlags(=0x10) when the root has none (fake candles without collision previously lost the AddonNode bit). All the fixes below were necessary too, but this was the last blocker: the earlier gravity_object fix repaired the SIM, this makes the engine RUN it. (fixed 2026-07-05, `_skyrimize_modifiers`)**: the SSE particle engine does NOT drive Oblivion-era `NiPSysGrowFadeModifier` (scale) or `NiPSysColorModifier` (color) even though they're valid block types — particles spawn at scale 0 / alpha 0 = invisible. Convert them to the BS* equivalents the engine actually processes, matching a working vanilla fire (`references\Skyrim Meshes\...\slighthousefire.nif` Fireball): **NiPSysGrowFadeModifier → BSPSysScaleModifier** (60-entry scale ramp, grow-in/hold/fade-out, peak ~1.0 taper to 0.1); **NiPSysColorModifier → BSPSysSimpleColorModifier** (fade_in/out % + 3 Color4s). **Inject BSPSysLODModifier** — it's in 498/498 vanilla particle meshes (LOD begin/end/emit-scale/size = 0.033/0.233/0.2/1.0); without it the system culls at all distances. Keep emitter/spawn/rotation/gravity/position/bound-update/age-death as-is. Set NiPSysModifier `order` to vanilla bands: AgeDeath=0, LOD=1, Emitter/Spawn=1000, SimpleColor/Rotation/SubTex/Scale=3000, Gravity=4000, Position=6000, BoundUpdate=7000 (engine processes in ascending order). Set Name/Target(=the NiParticleSystem)/Active on every modifier.
- **Particle shader** (BSEffectShaderProperty): flags1 = `z_buffer_test` + `soft_effect` (see the FX brightness/soft-fade entry below — the earlier "NOT soft_effect" note was drawn from fire meshes only and did not hold for the blended FX population), flags2 = `vertex_colors` ONLY, `emissive_multiple`=**1.0** with emissive_color taken from the source `NiMaterialProperty` (was a blanket 1.5, which over-brightened every non-fire system), texture_clamp_mode=**0xFF03** (u32 packs clamp 3 in the low byte + Lighting Influence 0xFF in byte 1 — every vanilla fire uses 65283, not 3). Always attach a NiAlphaProperty flags=0x100d (additive SRC_ALPHA/ONE) — vanilla particles always have one (campfire01burning uses 0x10ed/threshold 128 standard blending; source alpha is passed through when present).
- **NiBillboardNode root scrambles particle emission → invisible (fixed 2026-07-05; quad re-billboarding added same day)**: Oblivion fire/effect NIFs have a `NiBillboardNode` ROOT (to face the 2D fire quads at the camera) with the particle-system emitters nested UNDER it. A NiBillboardNode re-orients its entire subtree to face the camera every frame; a world-space emitter under it emits into a spinning frame → particles fly off-screen / the system renders nowhere. Vanilla Skyrim keeps particle emitters under a PLAIN NiNode (`slighthousefire.nif`: BSFadeNode→NiNode "Fireball-Emitter"→NiParticleSystem). Fix in nif_converter Pass (root handling): if a NiBillboardNode root's subtree contains any NiParticleSystem, DEMOTE the root to a plain NiNode (copy name/transform/children/extradata/controller) — **but wrap each direct GEOMETRY child (the flat fire quads) in a fresh child NiBillboardNode carrying the source root's billboard_mode** (vanilla campfire01burning pattern: BSFadeNode → NiBillboardNode "Plane05" → NiTriShape). A plain demote leaves the quads fixed-facing = edge-on/backfacing from most in-game angles = fires look invisible (while NifSkope's default camera happens to face them). Emitter/marker child nodes stay unwrapped. Non-particle billboard roots keep the whole-root wrap. **BILLBOARD AXIS CONVENTION (the final fire-quad invisibility fix, 2026-07-05)**: Oblivion mode-1 (ROTATE_ABOUT_UP) keeps local **+Y up / +Z at camera**; Skyrim mode-1 keeps local **+Z up / ±Y at camera**. Fire quads are authored flat in local XY (height along +Y) with IDENTITY transforms — correct under Oblivion's convention, but under Skyrim's an identity-rotation billboard leaves the quad LYING FLAT spinning about Z (edge-on from every standing viewpoint = invisible). The wrapper NiBillboardNode must carry vanilla's **−90°-about-X** static rotation `[[1,0,0],[0,0,1],[0,−1,0]]` (maps local Y→world Z; byte-identical to vanilla campfire01burning "Plane05"). Diagnosed by comparing vanilla billboard-node rotations (non-identity!) vs quad vert planes (both games author quads flat in XY).
- **EditorMarker geometry must be STRIPPED** (`_walk_node`): Oblivion hides its editor-marker meshes (the pyramid in fire NIFs) via the node hidden flag, which our conversion clobbers with NIF_FLAGS (visible) — the marker then renders in game as an untextured BLACK PYRAMID (this was the mysterious "black pyramid" at placeatme'd fires; at world-placed fires it sat underground). Vanilla Skyrim ships no editor-marker geometry in these objects.
- **NiAlphaProperty must NOT be shared between particle systems**: Oblivion sources share one alpha block across several PS; vanilla Skyrim always pairs each PS with its own shader+alpha. `_convert_particle_system` clones the source alpha per PS.
- **NifSkope's "animate" option is NOT a valid diagnostic for Skyrim particle chains**: NifSkope 2.0.dev7 only registers the OLD `NiParticleSystemController`/`NiBSPArrayController` for particles (glparticles.cpp) and `BSEffectShaderPropertyFloat/ColorController` for effect shaders — it completely ignores `NiPSysEmitterCtlr`/`NiPSysUpdateCtlr`. A perfectly-authored Skyrim PSys NIF shows "No Animations in this NIF"; vanilla campfire only gets an animate option from its shader controllers on the glow quads.
- **UV SCALE (0,0) = INVISIBLE — THE fire-invisibility ENDGAME bug (fixed 2026-07-05)**: PyFFI's fresh `BSEffectShaderProperty` defaults `uv_scale` to **(0,0)** (vanilla: offset (0,0), scale **(1,1)**). Scale 0 collapses EVERY UV to the texture's top-left texel — transparent on flame textures — so all effect-shader geometry (particles AND quads) rendered fully transparent while being structurally perfect: sim ran (crash proved it), every block census-clean, texture valid. Diagnosed via A/B matrix: vanilla-structure+our-texture visible, our-structure+vanilla-texture invisible → field-by-field shader diff caught the one field never printed. ALWAYS set uv_offset(0,0)+uv_scale(1,1) on any PyFFI-created shader property; regression test asserts non-zero scale on every effect shader.
- **NiFlipController is dead in Skyrim (0/17,216 vanilla) — converted to atlas + float controller (2026-07-05, `asset_convert/flipbook.py`)**: Oblivion animates fire quads by flipping the diffuse per frame. Conversion: decode the N frame DDSes (DXT1/3/5 → BGRA), compose a horizontal strip atlas padded to POT frame count (uncompressed BGRA32 DDS, written into the output textures tree beside `\meshes\`), set `uv_scale.u = 1/N_pad`, and drive `BSEffectShaderPropertyFloatController` (flags 0x48, var **6 = U Offset**, `NiFloatData` keys mode **5 = CONST** at k·delta → k/N_pad; delta from `NiFlipController.delta`, fallback cycle/N or 1/15s). Planned in `_process_geometry` (validates source frames via `_resolve_source_texture` — maps the rewritten tes4 path back to the export textures tree), built in `convert_nif` (knows dst tree). NifSkope animates it too (its EffectFloatController is supported — NifSkope "no animate option" on PSys-only NIFs is normal, but flip-book quads DO animate there now). Fallback on unresolvable frames: static first frame.
- **NiTextureTransformController → BS*ShaderPropertyFloatController (2026-07-21, `_collect_tex_transform_ctrls`/`_attach_tex_transform_ctrls`)**: Oblivion scrolls/scales UVs (waterfalls, lava, Oblivion gates, sunbeams — 61 Nehrim meshes, 127 controllers) with a `NiTextureTransformController` hosted on `NiTexturingProperty`. Conversion DELETES `NiTexturingProperty`, so the animation was silently lost and e.g. `landscapewaterfall02.nif` rendered as a frozen texture. Skyrim's equivalent is a shader float controller on the UV offset/scale, chained via `next_controller` — vanilla `fxwaterfallbodytall.nif` drives **V Offset with the same 2-key ramp**, and `fxwaterfallthin512x128.nif` chains U Scale + V Offset + U Offset on one shader. Harvest BEFORE properties are cleared in both `_process_geometry` and `_convert_particle_system`, then re-attach to whichever shader was built (Lighting *or* Effect), preserving any flip-book controller already on it at the tail of the chain. The `NiFloatData` is reused as-is — both engines read the curve as a UV offset/scale over time. Mapping (`TransformMember` → Lighting/Effect enum): TRANSLATE_U 0→**20**/**6**, TRANSLATE_V 1→**22**/**8**, SCALE_U 3→**21**/**7**, SCALE_V 4→**23**/**9**. Flags: OR in **0x48** and keep the source cycle bits (0x06) — Oblivion's 0x08 lacks Compute-Scaled-Time so the curve never advances. **Dropped, not faked**: `TT_ROTATE` (2) — neither Skyrim shader exposes a UV rotation float, so 50/127 Nehrim controllers have no equivalent; `NiBlendFloatInterpolator` (46/127, all skull/fireball meshes) — driven by a `NiControllerManager` sequence, no inline keys to translate; and single-key curves (constants, not animation).
- **`BS*ShaderProperty*Controller.Target` must name its shader block — a NULL target is a CTD on cell load (2026-08-01, `_bind_shader_ctrl_target` / `_drop_unbound_shader_controllers`)**: `NiTimeController.Target` is a non-optional back-pointer for this controller family. Census: **15/15 vanilla `BS*ShaderProperty{Color,Float}Controller`s name their own shader block** (Lighting controllers → `BSLightingShaderProperty`, Effect → `BSEffectShaderProperty`); **0 nulls in 150 meshes sampled**. The Oblivion sources we rebuild these from (`NiMaterialColorController`, `NiAlphaController`, `NiTextureTransformController`) target the NiTriShape's *property list*, which has no Skyrim counterpart, so the rebuilt controller was written with `target = None`. Skyrim dereferences it while loading the shader property → `EXCEPTION_ACCESS_VIOLATION`. Crash signature: the faulting frame is the engine's own `BSLightingShaderProperty::LoadBinary` (reached via `LooseFileStream` → `BSResourceNiBinaryStream` → `NiStream`); with Community Shaders installed the return address lands in its `TruePBR.cpp` `BSLightingShaderProperty_LoadBinary` hook, which is a **red herring** — the hook simply calls the original, and the fault is inside it. Fix binds the target in `_match_seq_shader_types` (which already resolves each node's real shader for the Lighting-vs-Effect re-stamp) and drops any entry that still cannot be bound.
- **`"<node>:<index>"` in a sequence string palette means GEOMETRY, not a missing node (2026-08-01, `_retarget_geometry_suffix_entries`)** — the reason a shader-controller target can look unbindable. Oblivion's exporter names a node's geometry children two different ways, and `morroblivionchandilier01.nif`'s `Idle` sequence uses **both at once**:
  ```
  node='CandleSkinny01:0'          NiMaterialColorController   (emissive flicker)
  node='CandleSkinny01'            NiTransformController
  node='CandleSkinny01 NonAccum'   NiTransformController
  ```
  The last two name real `NiNode`s, so **the palette is not stale** — only the `:0` form needs translating. It means "geometry child 0 of `CandleSkinny01`", which after conversion is the shape carrying the `BSLightingShaderProperty` (block-named `Tri Tri Light_Com_Chandelier_01 2 0` under the other convention, `Tri <parent> <index>`). Resolve it by walking the named node's subtree, collecting shader-bearing geometry in tree order, and taking the Nth; then rewrite the entry's `node_name` to that real block name so the engine can re-bind at run time. The source `node_name` bytes are empty — the name lives only in the palette — so none of this is visible unless you resolve the offsets.
  **Do NOT "fix" this by deleting the entry.** That was tried first and is wrong twice over: it silently drops the chandelier's emissive flicker (a faithful conversion must keep it — the curve is 5 keys, 0→3s, and survives byte-identical), and emptying the sequence strands its `NiControllerManager` with **0 sequences**, which the engine dereferences exactly the same way — crash log named `RCX/RDI = NiControllerManager*`, `RAX = 0`, on `BSFadeNode "CandleSkinny01"`. Vanilla census: **0/8 managers have 0 sequences; 0/17 sequences are empty.** This was the Seyda Neen Census & Excise Office CTD — the chandelier is placed **7×** in that one room. *(Pre-existing and NOT part of this fix: 24 empty `Forward`/`Backward` sequences on `morroblivion\flora\*anim.nif` — separate issue, no manager involved.)*
- **Skyrim reads ONE UV set — a second one overruns the engine's vertex buffer (2026-08-01, `_clamp_uv_sets`)**: this was the **Seyda Neen Census & Excise Office CTD**. On disk the u16 **`BS Data Flags`** is a bitfield (`references/nif [version].xml` → `BSGeometryDataFlags`): **low 6 bits = UV-set COUNT** (mask 0x003F), bits 6–11 = Havok Material, **bit 12 (0x1000) = Has Tangents**. PyFFI splits that one field into `num_uv_sets` + `extra_vectors_flags`, which is why `extra_vectors_flags = 16` writes bit 12 — the converter's comment calling it an enum ("0=none, 16=has binormal+tangent") is wrong, it is a bitfield. The count is the **only** thing telling the engine how many `TexCoord` arrays follow the vertex colours, so a mesh that stores **2** sets while `BSLightingShaderProperty` binds 1 leaves the vertex buffer a whole array short: the copy runs past the end of the allocation and faults on a **non-temporal store** — `vmovntdq [rcx+N], ymm` where `rcx` is 32-byte aligned and `rcx+N` is exactly the first byte past a 64 KB page. That alignment signature (`memcpy` ≥4 KB, destination landing precisely on the page boundary) is the tell for a short destination buffer, **not** a bad pointer. Oblivion authors the extra set for detail/overlay passes Skyrim has no slot for; set 0 is the diffuse UVs every shader samples, so the surplus is dropped. Census: **2,233 vanilla shapes carry 0 or 1 UV sets, NEVER 2**; we shipped 2 on 5 meshes, including `morro\f\furnucomutableu05.nif` — the file the crash log named in its `inputFilePath`. Also note `bhkCompressedMeshShapeData` blocks legitimately dwarf these (500 KB+), so "big block" alone is not a signal.
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
- **Fire/effect QUAD emissive (`_process_geometry`, flip_ctrl path)**: BSEffectShaderProperty.emissive_multiple defaults to 0.0 → the flame quad renders BLACK. Fire is self-illuminated: set emissive_multiple=1.0. emissive_color is taken from the source `NiMaterialProperty`, falling back to (1,1,1) only when the source declares no emissive at all (see the next entry).
- **FX BRIGHTNESS + THE RECTANGULAR BOUNDING BOX (2026-08-07, `_apply_fx_soft_effect` + the `is_additive_fx` route)** — user report: "smoke effects such as in Vilverin are incredibly bright… way brighter than in Oblivion and difficult to see through, and many transparent effects have what appears to be a rectangular bounding box around them". Three separate defects, all in the FX shader path:
  1. **Authored emissive was discarded.** Both the quad and particle paths hardcoded `emissive_color=(1,1,1,1)`, throwing away Oblivion's own `NiMaterialProperty.emissive_color` — which is precisely how Oblivion dims an FX surface. `dungeons/misc/fx/fxmist01` ships (0.47,0.47,0.47) and `fxmistgroundeffect01` ships (0.13,0.16,0.17); both were being promoted to full white. Under **additive** blending (dst=ONE) the excess accumulates per overlapping layer, so a multi-plane mist reads as blinding and opaque instead of translucent. Now carried across verbatim; white only when the source emissive is pure black. `NiMaterialProperty.alpha` (previously dropped on the effect path entirely) goes to `emissive_color.a`.
  2. **`emissive_multiple` was a blanket 1.5 on every particle system.** That is a *fire* value, but the same code path converts smoke, mist, steam and dust. Vanilla census of 1,164 blended FX shapes: **1.0 in 852**; the brighter values are authored per-effect, never applied wholesale. Now 1.0, with the authored colour doing the dimming.
  3. **`slsf_1_soft_effect` was never set anywhere.** Without it a blended FX quad intersecting solid geometry is hard-cut along the intersection line, so the billboard shows **its own quad edge** — the reported rectangle. Vanilla census (1,198 BSEffectShaderProperty shapes across meshes/effects + meshes/dungeons): additive `0x100d` → soft_effect=1 in **417/470**, blended `0x10ed` → **224/362**, *no* NiAlphaProperty → soft_effect=0 in **322/332**. So the rule is **blended FX gets the fade, unblended does not**; `soft_falloff_depth` = **100.0** (the commonest value, 250/521 on mist/smoke/fog geometry, and what vanilla uses for ambient room fog).
- **`lighting_mode == 0` is NOT the only unlit indicator — ADDITIVE BLENDING IS THE SECOND (same fix)**: the FX/lit discriminator was `NiVertexColorProperty.lighting_mode == LIGHTING_E`, but **many Oblivion FX meshes ship no `NiVertexColorProperty` at all**, so the mode defaulted to "lit" and genuine FX geometry took `BSLightingShaderProperty` — lit, normal-mapped, no soft fade. `fxmistgroundeffect01` (the Ayleid-ruin ground mist the user saw in Vilverin) is exactly this: additively-blended AtmosphereCloud01 planes with no vertex-colour property, so **all 30 shapes** were misrouted. Across Oblivion's own FX directories **76 of 179** blended shapes declare no lighting_mode. A surface whose NiAlphaProperty sets **dst=ONE** adds its colour to the framebuffer and therefore cannot be lit geometry (lighting it double-counts the light it already contributes). Vanilla agrees without exception: of 64 additively-blended shapes sampled, **64/64 use BSEffectShaderProperty, 0 use the lighting shader**. **Plain alpha blending is deliberately excluded** — the same census shows 3 legitimate BSLightingShaderProperty cases (glass/ice), so widening the rule to all blending would misroute real lit geometry. Blast radius measured before shipping: across a 250-mesh sample of architecture/clutter/dungeons only 10 shapes newly reroute, all `textures\effects\` blood decals and FlameTower quads.

## NIF FlameNode → grafted converted flame (rewritten 2026-07-05, replaces the MPS/AddonNode substitution)
<a id="nif-flamenode-grafted-converted-flame"></a>
- Oblivion marks where a flame burns with an empty `FlameNode*` NiNode (a bare marker: name + transform, no children) and attaches a flame NIF there at RUNTIME (`fire\firecandleflame.nif` for candles/sconces/lamps/etc., torch flame for torches). 108 Oblivion meshes have them.
- **Conversion (`_convert_flame_nodes` + `_load_converted_flame` in nif_converter.py)**: the flame NIF for each marker's socket (see the FlameNode STAT table below) is run through the FULL converter once per worker (cached as serialized bytes; deep copies by re-reading — requires the patched-PyFFI NiPSysData `read`), and the converted root's children are grafted under each empty FlameNode marker. Marker keeps TRANSLATION, SCALE **and ROTATION** — all three are authored. The rotation is the hook-up between two model frames: the flame NIFs are +Y-up, and a +Z-up host carries the −90°X correction on its marker (`uppersilverplatecandles01`'s FlameNode0 is `[1,0,0][0,0,1][0,-1,0]`, i.e. `_BB_AXIS_FIX` itself — that host is a flat plate, extent X=23 Y=23 Z=2, and all 121 of its REFRs use RotX=0, so nothing else would stand the flame up). Zeroing it laid the candle flames on their side; +Y-up hosts author an identity marker and are unaffected. Host root gets BSX bit 0 OR'd in (grafted controllers must tick); the flame's flip-book atlas jobs are merged into the host stats so `convert_nif` writes the atlas into every output tree that needs it. Graft runs in `convert_nif` BEFORE the atlas build step.
- **The earlier "embedding crashes the engine" lesson is OBSOLETE**: that crash (`vmovntdq` past page end, `BSEffectShaderProperty "CandleFat02Fake"`) was actually the PyFFI NiPSysData 66-vs-70-byte misalignment (+ uv_scale=(0,0)) — both long fixed. The interim `BSValueNode`/`AddOnNode` MPS substitution (`_ADDN_CANDLE_FLAME`=49 / `_ADDN_TORCH_FIRE`=46 / BSX bit 0x10) is deleted per user directive: convert, don't substitute.
- **Billboard handling is now GENERAL (any tree depth, `_skyrimize_billboard`)**: firecandleflame.nif nests its particle emitter under TWO levels of NiBillboardNode, so root-only handling was insufficient. Every non-root NiBillboardNode on the walk (and root's direct children — they use a separate loop in `_convert_nif` that needs the same hook): contains a NiParticleSystem anywhere in its subtree → DEMOTE to plain NiNode + wrap its direct geometry children via `_wrap_in_billboard` (fresh NiBillboardNode, source mode, `_BB_AXIS_FIX` −90°X rotation); pure-geometry billboard → keep but COMPOSE the axis fix into its rotation (Oblivion billboards are authored identity over flat-XY quads). **When demoting, remap `emitter_object`/`gravity_object` refs that pointed at the old billboard node to the replacement** — else they dangle ("block is missing from the nif tree") and the particle sim breaks.

- **FLAME QUADS STAY IN THE MODEL FRAME — no axis fix on the wrapper (fixed 2026-08-20)**: `_wrap_in_billboard` used to compose `_BB_AXIS_FIX` (−90°X) into every wrapper it built. That is wrong for these meshes: they are authored **+Y-up and their PLACED REFERENCES carry the stand-up rotation** — censused across `Oblivion.esm`, **494 REFRs** of the `Fire\*.nif` lights use `RotX = ±90°` (10/10 for `FireTorchLargeSmoke`, 188+51 of 395 for `FireOpenSmall`). The whole model — quads AND emitter markers — shares that one frame and the REFR rotates all of it together. Pre-rotating only the quad made it the sole part in a different frame, so the REFR's −90° then laid it flat: reported in game as "a third flame component on its side" beside a correct-looking flame and smoke. `_wrap_in_billboard` now applies NO fix and only tags `bb._axis_fixed = True`, so the later `_skyrimize_billboard` pass leaves its wrappers alone (that guard still fires — measured 27 times over 81 billboard meshes — and without it the pure-geometry branch would compose the fix back in). `_compose_axis_fix` remains live for genuinely Oblivion-authored pure-geometry billboards (249 calls over the same 81 meshes). Guarded by `test_flame_keeps_the_authored_model_frame`.
- **A DEMOTED BILLBOARD INHERITS IDENTITY — except emitter markers**: a `NiBillboardNode` DISCARDS its own rotation at runtime and substitutes identity in view space (NifSkope `BillboardNode::viewTrans`, glnode.cpp: `t = parent->viewTrans() * local; t.rotation = Matrix();`). Copying that dead rotation onto the plain replacement resurrects a value the engine never used. **But a `NiPSysEmitter` reads its `emitter_object` node's orientation as the emission DIRECTION**, which is live data — `firecandleflame` authors quad and emitter in one +Y-up frame (quad identity, local extent `[1.3, 2.6, 0.0]`; emitter `[1,0,0][0,0,-1][0,1,0]`, local +Z → model +Y), and zeroing the emitter made it +Z-up while the quad stayed +Y-up: an upright flame with a second, sideways particle jet, most visible once a FlameNode marker rotated the mismatched pair into a +Z-up host. `_is_emitter_marker()` keeps the rotation for nodes referenced as `emitter_object`/`gravity_object`; every other demoted billboard still gets identity. Guarded by `test_emitter_and_quad_agree_on_up`.
- **WHICH FLAME BURNS AT A SOCKET IS AUTHORED — read the FlameNode STATs**: Oblivion ships one STAT per socket (WorldObjects/Static, EditorID `FlameNode<N>`) whose MODL is the flame to attach: `FlameNode0` `0x1E` FireCandleFlame, `1` `0x1F` FireTorchSmall, `2` `0x20` FireTorchLarge, `3` `0x21` FireTorchLargeSmoke, `4` `0x22` FireOpenSmall, `5` `0x23` FireOpenSmallSmoke, `6` `0x24` FireOpenMedium, `7` `0x25` FireOpenMediumSmoke, `8` `0x26` FireOpenLarge, `9` `0x27` FireOpenLargeSmoke. Those FormIDs are the keys `Oblivion.exe` hardcodes — the socket-name table at `0xB06818` is walked in lockstep with `0xB067C0` holding `0x1E..0x32`, looked up in the form map at `0xB0613C` — so the **plugin owns the mapping and a mod may repoint it**; `_flame_socket_map()` parses it from the export's `STAT.txt` (cached per export root). Keying on the host FILENAME instead ('torch' in the name) put the 1.3×2.6-unit candle flame on every lamp in the game: `castlelight02` is a 105-unit fixture on socket 2, i.e. FireTorchLarge (32×64). Resolution is **per marker** — `lecternworkstation1` mixes FlameNode0 candles with a FlameNode1 torch. Guarded by `test_flame_comes_from_the_flamenode_stat`.
- **A ZERO-PADDED SOCKET BURNS NOTHING**: the engine matches socket names EXACTLY, and its table holds only unpadded `FlameNode<N>` — `Oblivion.exe` contains `FlameNode7` and `FlameNode1` but neither `FlameNode07` nor `FlameNode01`, and the STATs are likewise unpadded. Two vanilla meshes are authored with padded markers and show **no flame in the original game**: `clutter/metalsmith/forgeopen01.nif` (`FlameNode07`) and `clutter/lecternworkstation1.nif` (`FlameNode01`). Matching them loosely put a 468-unit FireOpenMediumSmoke on the forge. `_FLAME_SOCKET_RE` is `^FlameNode(0|[1-9][0-9]*)(?![0-9])` and an unmatched socket grafts NOTHING — there is no default-flame fallback. Guarded by `test_zero_padded_socket_burns_nothing`.
- **FX BRIGHTNESS IS THE AUTHORED VALUE, NEVER THE FILENAME (2026-08-27)**: a 2026-08-20 revision classified flames by the diffuse PATH — `_is_fire_fx()` matched `fire`/`flame`/`torch` minus a `smoke`/`mist`/`fog`/`dust`/`steam`/`cloud` veto — and forced `soft_effect=0, emissive_multiple=1.5` on every hit. **That was wrong and is removed.** It misfired on `textures\lights\torch02.dds`, the WOODEN HANDLE whose host `lights\torch02noflame.nif` contains no flame at all, and it could only ever work for meshes following Bethesda's naming — never Nehrim, Morroblivion or any third-party plugin. Oblivion states brightness per SHAPE in `NiMaterialProperty.emissive_color`, and across all 778 particle systems under `meshes/` the populations do not overlap: **flames author full white 1.0** (`firetorchlarge` "Fire", `crtfirelogs` "PCloud08BigFlame"; 227 systems at 1.0) while **fog/dust author 0.047–0.337** (`fxcloudthick01` 0.078, `fxcloudthin01` 0.047, `fxdustcloud01` (0.337,0.337,0.294); 190 systems below 0.5). Better than 12x separation, and per-shape — which matters because `firetorchlargesmoke.nif` holds a flame AND a smoke plume in one file, so any per-file test must give them the same answer. Rule: **carry `emissive_color` through verbatim and hold `emissive_multiple` at the vanilla-neutral 1.0.** A flame authored full white is already at full emission and needs no boost. Guarded by `test_fx_emissive_is_the_authored_value`.
- **A SELF-LIT FLAME MUST NOT TAKE THE SOFT DEPTH FADE (2026-08-27)** — user report: candles like `lights\uppersilverplatecandles01.nif` "glow red instead of the flames". **The red was never a new light**: the candle WAX authors `NiMaterialProperty.emissive_color` (0.953, 0.910, 0.678) on the LIGHTING path (`slsf_1_own_emit`), code unchanged since the initial commit. What regressed is that the FLAME in front of it disappeared. Removing the filename classifier (entry above) dropped BOTH halves of that commit's behaviour, but only the brightness half was given an authored replacement — flames then started taking `slsf_1_soft_effect`, and the depth fade attenuates a quad against whatever it intersects. A candle flame sits directly on its own wax and a sconce flame against its own bracket, so the fade dimmed each flame into its own holder, leaving only the wax glow visible. **Vanilla authors the split inside ONE mesh**: `mps\mpscandleflame01.nif` — both particle systems, both additive `0x100d`, both `emissive_multiple` 1.0 — has `CandleFlame01` **soft=0** (falloff 2.0) and `CandleGlow01` **soft=1** (falloff 6.0); likewise every mounted fire core (`slighthousefire` Fireball, `torchsconce01` pFireballCore04, `giantcampfire01burning` PFireball — 49 such particle systems across 281 vanilla fire meshes). **Skyrim's value is NOT reconstructible from structure**: over 511 vanilla FX shaders neither block type (particle 119/168 soft=1 vs geometry 159/343), nor alpha flags (`0x100d` splits 163/74), nor `double_sided` (78% vs 44%) predicts it — it is authored per effect, and Oblivion has no equivalent field to carry across. So key it on the one authored quantity that DOES separate the populations, the same emissive that drives brightness: **full-white (>=0.999) = self-lit light source -> soft=0**; anything dimmer = ambient haze -> soft=1 (fog 0.047-0.078, dust 0.337, mist 0.310). The asymmetry justifies the cut: a missing fade only omits a Skyrim-era nicety from a flame, while a wrongly-applied one ERASES the flame. Guarded by the soft_effect assertion in `test_fx_emissive_is_the_authored_value`.
- **OBLIVION'S GLOW MAPS ARE L8 AND SKYRIM RENDERS THEM PURE RED (2026-08-27, `asset_convert/luminance_textures.py`)** — user report: candles such as `lights\uppersilverplatecandles01.nif` "glow red instead of the flames". Oblivion ships glow maps as **8-bit DDPF_LUMINANCE** (`pf flags 0x20000`, `bitcount 8`, masks **R 0xFF / G 0x00 / B 0x00**): one channel, sitting under the RED mask. Oblivion's shader replicates that channel across RGB. Skyrim's glow shader (`skyrim_shader_type` 2, slot 2) samples slot 2 as an ordinary RGB texture and does **not** replicate, so green and blue read zero and the surface glows **pure red**. Census of Oblivion's whole texture tree: **469 files are L8, and every single one is a `_g` glow map** — no other suffix uses the format and no glow map uses another format (the rest: 8481 DXT5, 5981 DXT1, 5762 DXT3, 124 uncompressed RGB). Vanilla Skyrim never ships L8: its own glow maps are RGB textures whose CONTENT is grey (`spriggan_g.dds` is DXT1 with R==G==B==17.2 mean). **Fix: expand L into R=G=B as uncompressed BGRA8**, every mip level, alpha opaque — lossless, no DXT encoder needed, and these files are small. Runs in `asset_pipeline` AFTER the texture copy (same placement and reason as `landscape_normals.run`, so a re-copy cannot resurrect the L8 originals) and is idempotent, because a converted file is no longer DDPF_LUMINANCE. Keyed on the FORMAT, not the `_g` suffix, so a plugin shipping an L8 diffuse is handled too. **This is what `_apply_glow` (d6aa341) exposed**: that commit routes a derived `<diffuse>_g.dds` into the Skyrim glow slot even when the source NIF names no glow texture at all — `uppersilverplatecandles01`'s wax has only a base texture — so meshes that never had a glow shader in Oblivion acquired one, pointed at an unreadable format. The authored emissive on that wax IS real, however: Oblivion marks it (0.953, 0.910, 0.678) warm cream, which is what should show. Guarded by `TestLuminanceGlowMapsBecomeRGB`.
- **A COLOR MODIFIER IS NOT AUTOMATICALLY A COLOR — chroma is the test (2026-08-27, `_color_curve_carries_hue`)**: the 2026-08-26 ghost work made a `NiPSysColorModifier` *or* a `NiVertexColorProperty` suppress the authored emissive and force the shader tint to white, on the theory that the per-particle curve supplies the color. That is only half true, and it **re-broke Ayleid-ruin fog** (user report: Belda). Oblivion uses the modifier for two unrelated jobs: an **alpha envelope** — achromatic, R==G==B, `(0,0,0,0)→(1,1,1,1)→(0,0,0,0)`, which is what `fxcloudthick01`/`fxcloudthin01`/`fxdustcloud01` ship and which contributes NO color — versus a real **color curve**, chromatic, which is what `creatures\ghost\skeleton.nif` ships (pale green (0.702,0.831,0.745)→(0.514,0.647,0.561)) against a near-black 0.039 carrier material. Whitening on the mere PRESENCE of a modifier conflates them: fog's authored 0.078 became 1.0, a **12.8x over-brighten** on additively-blended planes that Belda layers several deep. Of the 190 dim (<0.5) particle systems, **120 have an achromatic curve** and only 70 a chromatic one. Rule: defer to the curve **only when its keys carry actual chroma** (`hi>0.02 and hi-lo>0.03`, ignoring the near-black envelope endpoints); otherwise the material's emissive is the only brightness the effect has. Sample the curve BEFORE `_skyrimize_modifiers` rewrites `NiPSysColorModifier` into `BSPSysSimpleColorModifier`. Guarded by `test_chromatic_color_curve_still_defers_to_the_curve`.
- **NifSkope striping on flip-book quads is COSMETIC**: NifSkope's GLSL path (`sk_effectshader.frag`) applies `uvScale`, but its fixed-function fallback maps raw UVs — the whole N-frame atlas strip shows across the quad ("texture, blank, texture"). Vanilla meshes use scale (1,1) so the fallback looks right for them; in-game the engine always applies the scale.

## NIF NiGeomMorpherController (dead in Skyrim, fixed 2026-07-05)
<a id="nif-nigeommorphercontroller"></a>
- **0 of 17,216 vanilla Skyrim meshes use NiGeomMorpherController** — it's Oblivion's bow flex/morph system; Skyrim bows are `*skinned.nif` and flex via skeletal animation. Strip it (and NiMaterialColorController) from geometry controller chains: `_strip_dead_geometry_controllers()` walks `geom.controller.next_controller` and unlinks them. This also lets NiTriStrips that were only kept as strips (because of the morpher) convert to NiTriShape.
- Why it mattered: PyFFI mis-serializes NiGeomMorpherController across the 20.0→20.2 bump — `interpolator_weights` is populated under the Oblivion layout but EMPTY under the Skyrim layout, so `data.write` aborts with `array size (0) different from field describing number of elements (N)`. This was the entire `weapons\*\bow.nif` [WR] failure list in TODO.txt §7.

## NIF embedded Ni*Light blocks (dead in Skyrim, fixed 2026-07-18)
<a id="nif-embedded-nilight-blocks"></a>
- **0 vanilla Skyrim meshes contain any NiAmbientLight/NiDirectionalLight/NiPointLight/NiSpotLight block** (nif_block_scan). They are 3ds Max export leftovers in a handful of Oblivion assets (11 meshes: statuegodszenithar01, sanguine statue/shrine, priory doors/cabinets, vine01/02, countess clothes _gnd). SSE fails to load a static carrying one — statuegodszenithar01.nif (NiAmbientLight child of the root) rendered as the missing-model red triangle (TODO §26).
- Skyrim lighting comes from placed LIGH references, never from mesh-embedded light nodes, so there is nothing to convert them into. `_walk_node`'s NiDynamicEffect branch now strips ALL dynamic-effect subtypes (it previously kept Ambient/Point/Spot believing them valid; NiNode `effects` arrays were already cleared, but a light in the `children` array survived).

## Early-Oblivion NIF versions (10.0.1.0 / 10.0.1.2 / 10.1.0.106) — the [RD] read failures (SOLVED 2026-07-15)
<a id="early-oblivion-nif-versions-rd"></a>
Oblivion's BSAs contain dev-era leftovers in older NIF versions that PyFFI 2.2.3 can't parse (floorplane01, handscythe01, oar01, stonepedastellarge01, ungrdltraphingedoor, kvatch castle int hallway01, arwelkydclusterfx01, scampswitch01). Fixed with monkey patches 5-7 in `asset_convert/pyffi_monkey_patch.py` (field-presence guards verified against `references/nif 0.10.0.0.xml` + byte-level decode):
- ≤10.0.1.2: extra uint after bhkWorldObject.Shape and at the start of HavokMaterial; bhkRigidBody CInfo lacks the 16-byte filter-copy header and max-velocity trio; bhkMoppBvTreeShape lacks the offset vector; bhkNiTriStripsShape lacks the scale Vector4; 10.0.1.0 mopp data is FULL size (pyffi's "size-1" convention is pre-Bethesda).
- 10.1.0.106: NiSingleInterpController.Interpolator exists since 10.1.0.104 (pyffi said 10.2); NiInterpController has a Manager Controlled byte (10.1.0.104-108); NiPSysEmitterCtlr.VisibilityInterpolator since 10.1.0.104; NiBlendInterpolator uses the full runtime-state layout (item array + per-subclass value snapshot: Transform 35B, Point3 12B) — hand-rolled consume-only reader.
- `bhkConvexSweepShape` (10.0.1.0 clutter) registered as a class at runtime; `_convert_shape` unwraps it to its inner shape (Skyrim never ships it).

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
- The strips→triangles pass in `_walk_node` rebuilds `NiTriShapeData` but
  **does not touch the partition**. The two `_regen_skin_partition` passes that
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
- **Seat candidate**: sit entries stand a FIXED distance from their seat — 51.5 (side refs 11/12) / 55.0 (front/behind refs 13/14) — walk that far along the approach direction (handles curved benches like anviltreebenchseat01; a bench's side entry is 51.5 from the END seat so it clusters correctly). Sleep entry distances vary per bed (67-106), so instead project the geometry-bbox centre onto the approach ray (entries always point across the hip line).
- **Heading** (= direction occupant faces; for sleep = head→feet direction): `heading = orientation/1000 + offset[ref]` where offset = {1: −π/2, 2: +π/2, 3: −π/2, 4: 0, 11: −π/2, 12: +π/2, 13: 0, 14: +π}. 100% consistent across all 48 marker-bearing Oblivion.esm furniture NIFs. The old blanket `+π` rule was only right for ref 14. Ref semantics: 1/11 = occupant's left side, 2/12 = right side, 13 = behind occupant (step over / sit without turning), 14 = in front (approach facing seat, turn, sit), 3 = mat side entry, 4 = mat head-end crawl entry (3/4 verified against sleepingmat01's pillow bump; pillow end = taller z bump, calibrated on Skyrim bedroll01 where the marker proves head=+Y).
- **Z**: entry markers stand ON THE FLOOR in mesh coords (Oblivion furniture origins are at mid-height, so entry z is negative). Skyrim marker z = entry_z + 34.0 (sit) or + 37.0931 (sleep) — the vanilla floor-relative hip heights. All 24 Oblivion bed mattress surfaces lie 36.5-42 above their entry z, so floor+37.09 lands on the mattress. The old `z = -src.z` rule floated NPCs ~34 units in the air (it looked right on chairs only because origin-at-mid-height makes |−z| ≈ seat height by coincidence).
- **Entry flags** are relative to the final heading: flag = side of the seat the entry point lies on (front if (entry−seat)·facing > 0.5, etc.) — NOT a fixed per-ref mapping.
- Oblivion double beds get ONE centered sleep pos (entries converge mid-bed; single and double beds have identical entry spacing ~±91-94 so they cannot be distinguished, and Oblivion's fixed-travel sleep anim landed center-ish too).
- Marker-bearing NIFs live outside meshes/furniture too: clutter/castleinterior (castle beds/thrones), architecture (cathedral pews, tents/sleepingmat, ships/sibed, anvil tree bench), dungeons (benches, thrones, sacrifice altar), oblivion/architecture/citadel. Find them with a binary grep for the ASCII string `BSFurnitureMarker` (block type names are plaintext in NIF headers).
- BSFurnitureMarker lives in root NiNode's extra_data_list. During NiNode→BSFadeNode conversion, it must be explicitly converted and transferred (bulk extra_data_list copy breaks animated objects). Marker offsets are model-space and stay valid under the root-rotation wrap pass.
- **FURN record linkage (CRITICAL)**: TES5 FURN `MNAM` bits 0-23 enable NIF marker POSITION index 0-23. TES4 MNAM bits indexed the Oblivion NIF's ENTRY list — passing the bitmask through after seat clustering leaves dangling bits and the engine seats NPCs at garbage positions FAR from the mesh. The shared algorithm lives in `asset_convert/furniture_markers.py`; `tes5_import` (items.py `load_furniture_seats`, called in import Phase 0e) recomputes the same seat list from the source NIF and writes MNAM=(1<<n_seats)−1 + preserved high bits (0x40000000 sit-type / 0x80000000 bed-type, same in both games; beds add 0x08000000 MustExitToTalk like all vanilla beds) + WBDT(0,-1) + one FNPR per seat.
- **Oblivion entry-restriction variants**: many TES4 FURN records share one NIF and enable different entry-marker subsets (SEChair01F/R/L, 19 LCBench01* variants like `Fall`=front row only, `RL`=ends only). Conversion carries this into per-seat FNPR entry flags: only the entry directions whose TES4 entry bit was enabled are allowed (seats with no enabled entries fall back to all their entries). Verified vs vanilla: converted bench = 0x40000007 + 3×FNPR like CommonBench01; converted bed = 0x88000001 + FNPR 0x000C0002 byte-identical to CommonBed01; LCBed02L keeps right-entry-only (FNPR 0x00040002).
- FURN models whose NIF is missing from the export (SI furniture, palace thrones) get a conservative fallback: MNAM bit 0 + high flags, FNPR all entries. NIFs with NO markers get MNAM high flags only (no active positions — never enable bits beyond the NIF's position count).

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

**Real procedural, rewritten 2026-07-05 — replaces the asset-matching hack**: `asset_convert/spt_parser.py` + `spt_generator.py` + `spt_converter.py` decode the Oblivion SpeedTreeCAD-4.x `.spt` binary and bake procedural tree geometry directly into a Skyrim NIF that matches the Oblivion tree's silhouette. `python -m asset_convert.spt_converter <trees_src> <nif_dst> [--export-dir <dir>]`. The old `assets/speedtrees/` asset-matching + `_spt_to_skyblivion` is GONE (those were custom Skyblivion creations, not real conversions).

- **`.spt` format** is documented in `references/spttools-master/FORMAT` (GPL sptparser reference). It's a flat stream of `<int32 section_id><payload>` chunks. `spt_parser.py::parse_spt` consumes EVERY section (strict — unknown id raises) into an `SptTree`: levels (trunk=0, branch levels, leaves=last; count in section 1014), shape curves as ASCII "BezierSpline" strings (section 6000-6017), leaf maps (4003 texture / 4005 size / 4004 origin), composite-map UV quads (section 10002), collision primitives (12002/3/4), floor, flares, roughness. Parses 113/113 Oblivion.esm SPTs byte-exact, and 547/547 across every exported plugin (see the newer-CAD note below).
- **BezierSpline** (`spt_parser.BezierSpline`): header `lo hi variance`, then control points `x y tan_u tan_v tan_weight`. `eval(x)` = `lo + curve_y(x)*(hi-lo)` where x∈[0,1] is position along the parent. Constant params have lo==hi. `eval_var` adds the stored ±variance.
- **Scale**: world_units = `stored_value * Size * 10` (`WORLD_SCALE=10`). Verified against the TREE records' billboard heights (`textures/trees/billboards/<stem>.dds` are the ENGINE'S OWN renders — the definitive ground truth; decode them for A/B comparison) — median generated/actual height ratio ≈ 1.0.
- **Generation model** (`spt_generator.build_tree`): recursive stems. Child count per parent = `parent.child_freq * parent.stored_length` (250*0.05=12 on deadbush, 80*0.6=48 on oak). Children spawn in the `[child_first, child_last]` window; SHAPE curves (length/radius/start-angle/gravity/flexibility) evaluate at `x_rel` = position WITHIN the window (NOT absolute parent position — cottonwood forks its whole fan inside the trunk's [0,0.1] window). Start angle = degrees from parent axis. Azimuth = golden-angle spiral + jitter.
- **Gravity semantics** (revised 2026-07-10 after in-game feedback — an earlier "target pitch = 90°−|g−1|·90°" model bent cottonwood's fork limbs DOWN toward horizontal into a wide "wing" the billboard doesn't show): the value sets a bend DIRECTION and RATE — **0<g≤1 bends toward straight UP at rate g** (limbs spread at their start angle near the base then grow back vertical — cottonwood forks g 0.2-0.4, dogwood g 0.25-0.6; every normal trunk stores g=1 = stay vertical), **g>1 wraps over and bends toward the GROUND at rate g−1** (forsythia canes g=3 flop; willow branches store 2..4), g=0 = no influence (redwood, Camoran-paradise trunks — they wander on disturbance alone). The rate is scaled by the FLEXIBILITY value (6002) × GRAVITY PROFILE (6017 — starts at 0.5 at the base, so limbs curve from the moment they fork). Do NOT gate it by the flexibility PROFILE (6003): that ramp is 0 at the base, which left cottonwood's 60°-spread forks lying on their sides for their whole lower half. Willow branches (gravity 2-4, flex 0) HOLD their start angle — the weeping look is the leaf curtains, not the branches.
- **Weeping willow drape**: leaf-LEVEL gravity (section 6001 on the last level) = 90 means leaves hang straight down as long curtains. Modelled as vertical STRANDS of 4 stacked leaf cards reaching ~32% of tree height below each attachment — the only way to reproduce the solid teardrop crown that hangs far below the branches. Ordinary leaves (leaf gravity 0) get one card.
- **Leaf cards**: size = section 4005 * Size (NOT section 4006 — that's the pre-multiplied product but it's STALE in ~15 shrubs, e.g. buckthorn stores 0.08 where 4005*Size=3.6). Two crossed quads. UVs come from the composite-map quad (section 10002) cropping the shipped composite leaf DDS — which is the TREE record's ICON field, resolved at convert time (`_resolve_leaf_tex`).
- **EVERY leaf texture reference must be resolved through `tex_idx` — the SPT names the artist's .tga, not the shipped .dds (fixed 2026-07-27, `dementiatree10` missing leaves)**: `build_tree_nif` had two paths. The composite path (`g['texture'] == '__composite__'`) went through `_resolve_leaf_tex`, which validates `stem in tex_idx` and so can only ever emit a real file. The **per-map else-branch built `LEAF_TEX_DIR + stem + '.dds'` straight from the SPT string with no validation**, happily writing a path to a file that does not exist → leaves render untextured. Measured scope on the converted tree set: **137/143 leaf refs resolved, 6 broken across 4 NIFs** (`dementiatree01/04/10` + `treems14canvasfreesu`) — small, but invisible until you look, because the composite path masks it everywhere else. Two renamings account for all 6, both handled by the shared `_match_tex_stem` (literal stem → trailing composite `c` → leaf-map variant number): `MTreeLeaves02c.tga` → `mtreeleaves02.dds`, and `TreeMS14CanvasLeaves01SU.tga` → `treems14canvasleavessu.dds` (three per-map variants collapse onto ONE shipped atlas). Anchor the variant-number strip on `leaves|needles` and not on "first 2-digit run", or `TreeMS14…` loses its model number instead. Audit it with a scan that checks each converted tree NIF's `textures[0]` against the filesystem — the count should be 0 missing.
- **Newer-CAD trees: the roots twist pair and the 50000 texture-coordinate block (fixed 2026-08-20, Tamriel Landscape Pack)**: 183 of 547 exported SPTs (all TamRes / Tamriel Landscape Pack; **no vanilla Oblivion tree is affected**) were authored by a later SpeedTreeCAD that writes two things the parser did not handle. Both are now supported, and the fix is provably additive — all 364 previously-parsing trees are value-identical and their generated geometry is bit-identical.
  1. **The roots block carries its own 15003/15002 pair, bare and REVERSED.** Outside the roots block the sections come in `15000`-opened `15002,15003` pairs, one per level. Inside `40000..40001` the pair appears once with no opener and in the order `15003,15002`, and it belongs to the roots level. The handler ignored `in_roots`, so `twist_idx` ran one past the last level, `_level_by_seq` returned `None`, and the parse died with `'NoneType' object has no attribute 'random_v_offset'`. Guard it exactly like the `16002` flare and `26002` roughness groups: when `in_roots`, target `tree.roots_level` and **do not advance the counter**.
  2. **Section 50000 is the per-layer texture-coordinate block** (`FORMAT` lines 367-387 + `sptparser.c` case 50004-50018). It holds `50002..50003` groups, each one texture layer: `50004` U tile, `50005` V tile, `50006/50007` U/V absolute, `50008` twist, `50009` random V offset, `50010` V offset, `50011/50012` clamp, `50013-50016` left/right/bottom/top crop, `50017` U offset, `50018` sync-to-diffuse. There are **7 layers per level** (diffuse, detail, normal, height, specular, user1, user2 — the same seven filenames as `70002..70008`), and the block covers **trunk + branch levels + leaves + roots**, so the group count is always **`(num_levels + 1) * 7`** — verified 28/35/42 for 3/4/5 levels across all 183 files, 0 exceptions, matching the FORMAT note "count occurrences of 50002: 28 35 42 49, the difference is 7". Layer 0 is diffuse and duplicates the older per-level `6013-6016`/`15002`/`15003` values. Stored on `SptTree.tex_layers` as `TexLayer`; empty for older-CAD trees, which omit the block entirely. `70000/70001` also had to become markers (their `70002..70008` payloads were already in `_PATTERNS`).
- **Leaf textures: the ICON is the AUTHORED source; the stem-collapse rule is a narrow patch, do NOT widen it** (audited 2026-08-20, `tools/lod/spt_leaf_tex_audit.py`). The SPT's `4003` leaf-map string and the `70002` diffuse filename under `60003` both store the ARTIST'S path (`C:\Hope\IDV\GreyPoplar\TreeGreyPoplarLeavesSU.tga`) — they agree with each other and neither is what shipped. The authored answer is the **TREE record's ICON**, which resolves **literally in 354/354 records** across Oblivion + Nehrim; `_match_tex_stem`'s collapse regex is needed for **0** of them. (The ICON is the *composite* texture and the per-group fallback — a per-map leaf group whose OWN stem ships still wins, e.g. Nehrim's `treecottonwoodsu` groups keep `treecottonwoodleavessu` rather than the record's `Nehrim_Southshrub_SU01`. Verified 2026-08-20: modelling `build_tree_nif`'s exact per-group selection reproduces every shipped NIF's texture set, 146 variants sampled across Oblivion/Nehrim/TamRes, 0 mismatches.) Measured over all 662 tree variants, dropping the collapse rule changes the shipped texture for only **8** — exactly the documented `dementiatree01/04/10`, `treems14canvasfreesu`, and 4 Nehrim stems. So the rule earns its place at that width and nothing more: **widening it (trailing letter `leaves01a`, underscore `leaves_1`) buys only trees that have no TREE record at all** and is pure heuristic — CLAUDE.md "look for the AUTHORED indicator".
- **A resource pack's SPTs have NO TREE records and are never placed.** TamRes/Tamriel Landscape Pack ship 69 SPTs with **0** TREE records (Oblivion: 139/139 have one). Their trees are raw art for other plugins to reference, so an unresolved leaf texture there is invisible in-game. Of the 42 trees that resolve to no leaf texture, only **7 are placed**, and all 7 are legitimately leafless — `shrubdeadbush`, `treekvatchburnt`, `dtree02` (no leaf maps), and Anequina's two cacti, whose leaf maps are literally `FileLoadError.tga`. **No in-game tree is missing foliage art**; do not "fix" this by inventing a name-matching rule.
- **Auditing leaf textures per-plugin is MASTER-BLIND.** `_tex_index` over one plugin's `textures/trees` reported Valenwood as 0/58 resolved and Oblivion as 67/139; merging the masters' tree-texture dirs (as `convert_spt_directory` already does) gives 58/58 and 136/139. Any tree-texture audit must merge master dirs or every dependent plugin looks catastrophically broken.
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
<a id="book-inventory-art-books-were"></a>

- **Why books failed**: Skyrim's BookMenu renders the BOOK record's INAM inventory-art mesh, never the world MODL. The vanilla INAM meshes (`clutter\books\book02\character assets\bookskyrim01.nif`, `clutter\books\note01\note02.nif`) are rigged: skinned page-turn bone chains ("Book CoverPage Turn1-6", "Book TurnPage1-10", "Note Fold1-3"), a `BSBehaviorGraphExtraData` pointing at `Clutter\Books\Book01\Book01Project.hkx` that drives the open/page-turn animation, and a 4-vert `PageText` NiTriShape (with `NiStringExtraData 'Keep' = "NiHide"`) the engine swaps for the rendered page text. A static (converted Oblivion) mesh as INAM opens invisible with no text. INAM must always be present — BookMenu null-derefs without it.
- **Solution** (`asset_convert/book_inam.py`): keep the vanilla rig byte-for-byte (UVs, skin, BGED untouched — animation guaranteed) and instead **bake the Oblivion book's textures into the template's texture layout**, then point the template's cover `BSShaderTextureSet` at the baked atlas. One NIF+DDS pair per distinct TES4 book model (38 for Oblivion.esm) → `meshes\tes4\clutter\books\inv\<model basename>.nif` + `textures\tes4\clutter\books\inv\<base>.dds`/`_n.dds`.
- **Calibration is per-mesh UV-island fitting, not hardcoded rects**: both sides decompose into the same semantic regions — front cover (largest flat +Z island above the midplane), spine (tall |n_x| island on the same texture), page-edge strips (side-facing islands spanning the page block; Oblivion maps these to `bookpages01.dds`). A least-squares affine fit (normalized in-plane coords → uv) per region on each side, composed dst-uv → coords → src-uv, handles every Oblivion layout automatically: Octavo has the spine on the left edge of the texture, Quarto/Folio have it in the middle with separate front/back art. The Skyrim cover uses ONE art rect for both covers (u∈[0.24,0.96], spine u<0.22, page strips v>0.97, with wrapped UVs at u±1/v+1 reusing regions), so the Oblivion FRONT cover art is used for both sides — same limitation as vanilla.
- **Flat sheets (notes/parchment/posters/broadsheets) bake as a plain UV-space rect copy**, NOT through mesh coords: sheet art is authored upright in texture space while the world mesh may lie in any orientation (a flat-lying broadsheet arrived rotated 90° on the portrait Note02 template until this was changed). Unfittable sources (rolled scrolls, crumpled paper — UV not affine in position, fit rms > 0.08) fall back to an identity full-texture copy; scroll textures are actually flat sealed-parchment art, so they read fine on the note rig.
- **Atlas output**: 512² uncompressed BGRA DDS with a full box-filtered mip chain (writer in book_inam, decode via PIL). Normal maps baked the same way from the `_n` siblings (flat normal fallback). Pages/paper shapes keep the vanilla `LargeBookPaper01.dds` (loaded from Skyrim's own BSAs — nothing redistributed; templates come from the user's `Skyrim - Meshes*.bsa` via `skyrim_assets` auto-extraction, read through `sse_nif`).
- **pyffi round-trips Skyrim rigs safely**: re-writing bookskyrim01.nif only reorders the header string table with indices remapped consistently (verified byte-level); skin partitions/BGED survive.
- **Record side** (`tes5_import/record_types/equipment.py::convert_BOOK`): one `InvArt_<base>` STAT per distinct model (cached on the writer — BOOKs convert serially), INAM → that STAT; vanilla `HighPolySkyrimBook` only for model-less books. DATA.Type is ALWAYS 0: the CK lists 255 = Note/Scroll but vanilla Skyrim.esm types all 821 BOOKs (notes included) as 0, so 255 is engine-untested; TES4 scroll-ness survives via the vendor keyword + note-rig INAM.
- Pipeline: runs inside `convert.py phase_assets` after `convert_meshes`; standalone CLI `python -m asset_convert.book_inam Oblivion.esm [--extract-dir export] [--output-dir output] [--templates-dir <explicit meshes tree>] [--skyrim-data <SSE Data>] [--workers N]`. Tests: `tests/test_book_inam.py`.
- **Basename uniqueness (fixed 2026-07-28)**: `inv_basename()` keyed on the MODL leaf filename only, so plugins that merge several asset trees collided — Morroblivion ships both `Clutter\Books\Note01.NIF` and `Morroblivion\Clutter\Paper\Note01.nif`, and the guard `raise ValueError('INAM basename collision')` **aborted the entire asset stage** (book INAM + everything after it) for the whole plugin. Now names outside the conventional `clutter\books` tree are qualified by their parent directory (`paper_note01`), leaving vanilla-layout names untouched so existing generated assets stay stable; a residual clash logs and skips that one model instead of killing the stage. `equipment.py::convert_BOOK` **imports `inv_basename` instead of re-deriving it** — the rule had been duplicated in both files, which is exactly how the STAT target and the generated mesh could drift apart.
- **`i.shape in page_shapes` was a numpy trap (fixed 2026-07-28)**: shapes are dicts holding numpy arrays, so `in` runs dict `__eq__` → element-wise array comparison, raising `ValueError: operands could not be broadcast together` the moment two shapes have different vertex counts. Books whose shapes happened to share a vertex count worked; mixed ones failed to bake (35 failures on Morroblivion, now 0). Compare islands to shapes by `id()`, never by `in`/`==`.

## SSE-format NIF read support + BSA auto-extraction (2026-07-19)
<a id="sse-format-nif-read-support"></a>

- **Rule: the pipeline never resolves runtime assets through `references/`** (that tree is comparison-only and may not exist). Vanilla Skyrim files are fetched via `asset_convert/skyrim_assets.py`: `export/skyrim_assets/` cache first, else extracted on demand from the registry-detected SSE install's BSAs (atomic cache writes — pool workers race). `set_skyrim_data()` overrides detection.
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

**Code:** `_bs_pp_texture_slots`, the property loops in `_process_geometry` and
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
need no remapping — only `_rewrite_tex_path`, which already normalises
separators and strips the stray `data\` prefix that FO3 LOD meshes carry.

**The normal map is authored, not derived.** Oblivion rarely ships an `_n`
beside its diffuse, so the Oblivion path guesses `<base>_n.dds` and falls back to
a shared flat normal via `_resolve_normal_for`. FO3/FNV name slot 1 outright, so
an authored value is taken verbatim and the guess is skipped entirely — this is
the AUTHORED indicator, and it must win over the heuristic.

## Flame attachment: FlameNode sockets

**Code:** `asset_convert/nif_flames.py`

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

## Morroblivion centered door pivots

Generated Morrowind-to-Oblivion doors often author `Open` and `Close` as
opposite two-key Z rotations while leaving the animated node in the middle of
the leaf. Skyrim plays that data literally, so the leaf spins around its
center. The controller conversion recognizes that authored Open/Close pair,
infers the hinge edge from the leaf geometry and optional handle geometry, and
adds `T(t)=T0+Rclosed*P-R(t)*P` translation keys so the hinge point is fixed.

The fingerprint is deliberately narrow: a single two-key Euler track in both
sequences, zero X/Y rotation, a door-sized Z swing, inverse Open/Close keys,
vertical door-like geometry, and a pivot well inside the leaf. Existing
edge-pivot doors, horizontal trapdoors and unrelated controller sequences are
unchanged. `--mesh-subdirs` accepts full relative prefixes such as `morro/d`,
so the door family can be rebuilt without traversing every mesh.

PyFFI toaster traversal, tangent generation and skin-partition progress are
logged at WARNING internally but are not data defects. They are filtered from
the conversion summary after whitespace normalization; real malformed-tree,
stream, collision and value warnings remain categorized and visible.

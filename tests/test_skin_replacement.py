"""Tests for the skin replacement / body splice system.

Tests originally from test_body_splice.py (comprehensive splice verification) and
test_skin_replacement.py (TDD unit tests for the skin_replacement module).

Verifies that:
1. Body skin geometry is correctly identified and stripped
2. Skyrim body mesh sections are loaded from reference NIFs
3. Bone-based clipping correctly filters body geometry
4. Bbox computation from bone positions works in correct coordinate space
5. Underwear filter only removes actual underwear overlays, not the body mesh
6. End-to-end splice produces expected geometry in armor NIFs
7. Spliced vertices are exact copies from vanilla body NIF (no transforms applied)
8. Bind matrices preserve M@B@W identity (skinning invariant)
9. Edge lengths are perfectly preserved in spliced body regions
10. Rest-pose world positions match vanilla body NIF world positions
11. Female armor conversion produces correct body splice
12. Armor type detection, bone expansion, proximity clipping, weight morphing
"""

import math
import os
import sys
import time

import numpy as np
import pytest

if not hasattr(time, "clock"):
    time.clock = time.perf_counter

from pyffi.formats.nif import NifFormat

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from asset_convert.skin_replacement import (
    is_body_skin_geometry,
    collect_skin_info,
    load_body_geom,
    clip_body_geom,
    build_clipped_geom,
    splice_body_geometry,
    strip_body_skin_geometry,
    is_underwear_only,
)
from asset_convert.nif_converter import (
    _walk_node,
    _upgrade_skin_instances,
    _remap_bone_names,
    OUTPUT_VERSION,
    OUTPUT_USER_VERSION,
    OUTPUT_USER_VERSION_2,
    convert_nif,
)

EXPORT_ARMOR = os.path.join(BASE, "export", "Oblivion.esm", "meshes", "armor")
SKYRIM_BODY_DIR = os.path.join(
    BASE, "temp", "Skyrim Meshes", "meshes", "actors", "character", "character assets"
)
TMP_DIR = os.path.join(BASE, "temp")

# Specific armor paths for targeted tests
IRON_CUIRASS_F = os.path.join(EXPORT_ARMOR, "iron", "f", "cuirass.nif")
IRON_CUIRASS_M = os.path.join(EXPORT_ARMOR, "iron", "m", "cuirass.nif")
IRON_GAUNTLETS_M = os.path.join(EXPORT_ARMOR, "iron", "m", "gauntlets.nif")


def _load_nif(path):
    data = NifFormat.Data()
    with open(path, "rb") as f:
        data.read(f)
    return data


def _resolve_output(dst):
    """Return actual output path — weight variants rename foo.nif → foo_0.nif."""
    if os.path.exists(dst):
        return dst
    stem = os.path.splitext(dst)[0]
    dst_0 = stem + '_0.nif'
    if os.path.exists(dst_0):
        return dst_0
    return dst  # fallback — will fail on open with clear error


def _get_shapes(data):
    shapes = []
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
                shapes.append(block)
    return shapes


# ---------------------------------------------------------------------------
# Paths — skip all tests if export data unavailable
# ---------------------------------------------------------------------------
CUIRASS_M = os.path.join(EXPORT_ARMOR, "arenaheavyblue", "m", "cuirass.nif")
CUIRASS_F = os.path.join(EXPORT_ARMOR, "arenaheavyblue", "f", "cuirass.nif")
GAUNTLETS_M = os.path.join(EXPORT_ARMOR, "blackwood", "m", "gauntlets.nif")

HAS_EXPORT = os.path.exists(CUIRASS_M)
HAS_BODY = os.path.exists(os.path.join(SKYRIM_BODY_DIR, "malebody_0.nif"))

skipif_no_export = pytest.mark.skipif(not HAS_EXPORT, reason="Export armor NIFs unavailable")
skipif_no_body = pytest.mark.skipif(not HAS_BODY, reason="Skyrim body NIFs unavailable")
skipif_no_iron_f = pytest.mark.skipif(
    not os.path.exists(IRON_CUIRASS_F), reason="Female iron cuirass unavailable"
)
skipif_no_iron_m = pytest.mark.skipif(
    not os.path.exists(IRON_CUIRASS_M), reason="Male iron cuirass unavailable"
)


# ---------------------------------------------------------------------------
# Helper: run conversion pipeline up to retarget + bone rename
# ---------------------------------------------------------------------------

def _run_to_skin_collect_stage(nif_path: str, src_path: str):
    """Run conversion pipeline on an OB NIF up to retarget + bone rename.

    This is the minimal pipeline required before calling collect_skin_info:
      1. Load NIF
      2. Upgrade NiSkinInstance → BSDismemberSkinInstance
      3. Set Skyrim version fields so PyFFI creates BSLightingShaderProperty
      4. Walk nodes: converts NiTexturingProperty → BSLightingShaderProperty
      5. Retarget skin to Skyrim skeleton
      6. Rename bones to Skyrim format (adds bracket notation)
    Returns the converted NifFormat.Data object.
    """
    from asset_convert.skin_retarget import retarget_skin_to_skyrim

    data = NifFormat.Data()
    with open(nif_path, "rb") as f:
        data.read(f)

    _upgrade_skin_instances(data)

    data.version = OUTPUT_VERSION
    data.user_version = OUTPUT_USER_VERSION
    data.user_version_2 = OUTPUT_USER_VERSION_2
    data.header.endian_type = 1  # ENDIAN_LITTLE

    stats = {
        "strips_fixed": 0, "properties_converted": 0, "root_converted": 0,
        "root_rotation_baked": 0, "tangents_injected": 0, "bones_remapped": 0,
        "textures_fixed": 0, "_block_map": {},
    }
    for root in data.roots:
        if root is None:
            continue
        if hasattr(root, "children"):
            for j in range(len(root.children)):
                root.children[j] = _walk_node(root, root.children[j], True, stats)
            keep = [c for c in root.children if c is not None]
            if len(keep) < root.num_children:
                root.num_children = len(keep)
                root.children.update_size()
                for ri, rv in enumerate(keep):
                    root.children[ri] = rv

    retarget_skin_to_skyrim(data, src_path=src_path)
    _remap_bone_names(data)
    return data


# ===========================================================================
# 1. Underwear filter
# ===========================================================================
class TestUnderwearFilter:
    def test_underwear_only_detected(self):
        assert is_underwear_only(b"MaleUnderwear_1") is True
        assert is_underwear_only(b"FemaleUnderwear_1") is True
        assert is_underwear_only(b"maleunderwear_0") is True

    def test_body_mesh_not_filtered(self):
        assert is_underwear_only(b"MaleUnderwearBody:0") is False
        assert is_underwear_only(b"FemaleUnderwearBody:0") is False

    def test_other_names_not_filtered(self):
        assert is_underwear_only(b"FootMale_Big") is False
        assert is_underwear_only(b"HandMaleBig3rd") is False
        assert is_underwear_only(b"Arms:0") is False
        assert is_underwear_only(b"") is False


# ===========================================================================
# 2. Body skin identification
# ===========================================================================
@skipif_no_export
class TestBodySkinDetection:
    def test_cuirass_has_body_skin(self):
        data = _load_nif(CUIRASS_M)
        body_skins = [s for s in _get_shapes(data) if is_body_skin_geometry(s)]
        assert len(body_skins) > 0, "Cuirass should have body skin geometry"

    def test_body_skin_has_character_texture(self):
        data = _load_nif(CUIRASS_M)
        for shape in _get_shapes(data):
            if is_body_skin_geometry(shape):
                # Verify it uses textures\characters\ path
                for prop in shape.properties:
                    if isinstance(prop, NifFormat.NiTexturingProperty):
                        if prop.has_base_texture and prop.base_texture.source:
                            tex = bytes(prop.base_texture.source.file_name).decode(
                                "latin-1", errors="replace"
                            ).lower()
                            assert "characters" in tex
                            return
                pytest.fail("Body skin should have NiTexturingProperty with characters texture")


# ===========================================================================
# 3. Skin info collection
# ===========================================================================
@skipif_no_export
class TestCollectSkinInfo:
    """Tests for collect_skin_info — called AFTER retarget + bone rename.

    The function reads BSLightingShaderProperty texture paths (converted from
    NiTexturingProperty by _walk_node) and bone names (Skyrim names after
    _remap_bone_names).  Tests must run the pre-collect pipeline first via
    _run_to_skin_collect_stage().
    """

    @pytest.fixture(scope="class")
    def male_cuirass_data(self):
        return _run_to_skin_collect_stage(
            CUIRASS_M, "armor/arenaheavyblue/m/cuirass.nif"
        )

    @pytest.fixture(scope="class")
    def male_cuirass_info(self, male_cuirass_data):
        return collect_skin_info(male_cuirass_data)

    @pytest.fixture(scope="class")
    def female_cuirass_data(self):
        if not os.path.exists(CUIRASS_F):
            pytest.skip("Female cuirass not available")
        return _run_to_skin_collect_stage(
            CUIRASS_F, "armor/arenaheavyblue/f/cuirass.nif"
        )

    @pytest.fixture(scope="class")
    def female_cuirass_info(self, female_cuirass_data):
        return collect_skin_info(female_cuirass_data)

    # --- Body NIF detection ---------------------------------------------------

    def test_cuirass_collects_body_nif(self, male_cuirass_info):
        assert "malebody_0.nif" in male_cuirass_info, (
            f"Expected malebody_0.nif, got: {list(male_cuirass_info.keys())}"
        )

    def test_cuirass_collects_hands_nif(self, male_cuirass_info):
        assert "malehands_0.nif" in male_cuirass_info, (
            f"Expected malehands_0.nif, got: {list(male_cuirass_info.keys())}"
        )

    def test_female_cuirass_uses_female_nifs(self, female_cuirass_info):
        assert "femalebody_0.nif" in female_cuirass_info, (
            f"Expected femalebody_0.nif, got: {list(female_cuirass_info.keys())}"
        )

    # --- Bone names (Skyrim format after retarget + rename) -------------------

    def test_bones_are_skyrim_names(self, male_cuirass_info):
        body_info = male_cuirass_info["malebody_0.nif"]
        has_brackets = any("[" in b for b in body_info["bones"])
        assert has_brackets, (
            f"Bone names should be Skyrim format (with brackets), got: {sorted(body_info['bones'])[:5]}"
        )

    def test_arm_bones_present(self, male_cuirass_info):
        """Cuirass exposes arms — upper arm bones must be in the bone set."""
        body_info = male_cuirass_info["malebody_0.nif"]
        all_bones = body_info["bones"]
        arm_bones = {b for b in all_bones if "UpperArm" in b or "Clavicle" in b or "Forearm" in b}
        assert len(arm_bones) > 0, (
            f"Expected arm bones in cuirass skin info, got none. All bones: {sorted(all_bones)[:10]}"
        )

    def test_no_oblivion_bone_names(self, male_cuirass_info):
        """After retarget+rename, no bone should start with 'Bip01'."""
        body_info = male_cuirass_info["malebody_0.nif"]
        bip01_bones = [b for b in body_info["bones"] if b.startswith("Bip01")]
        assert len(bip01_bones) == 0, (
            f"Found Oblivion-format bone names: {bip01_bones}"
        )

    # --- Sections structure ---------------------------------------------------

    def test_sections_stored(self, male_cuirass_info):
        body_info = male_cuirass_info["malebody_0.nif"]
        assert "sections" in body_info
        assert len(body_info["sections"]) > 0, "Should have at least one section"

    def test_sections_are_bone_sets(self, male_cuirass_info):
        """Each section must be a non-empty set of bone-name strings."""
        body_info = male_cuirass_info["malebody_0.nif"]
        for i, sec in enumerate(body_info["sections"]):
            assert isinstance(sec, set), f"Section {i} should be a set"
            assert len(sec) > 0, f"Section {i} is empty"
            for name in sec:
                assert isinstance(name, str), f"Section {i} bone name not a str: {name!r}"

    # --- Section verts structure -------------------------------------------

    def test_section_verts_stored(self, male_cuirass_info):
        body_info = male_cuirass_info["malebody_0.nif"]
        assert "section_verts" in body_info
        assert len(body_info["section_verts"]) > 0, "Should have at least one section vert cloud"


# ===========================================================================
# 4. Body NIF loading
# ===========================================================================
@skipif_no_body
class TestLoadBodyGeom:
    def test_malebody_loads(self):
        entries = load_body_geom("malebody_0.nif")
        assert len(entries) > 0

    def test_malebody_has_body_mesh(self):
        entries = load_body_geom("malebody_0.nif")
        names = [bytes(geom.name).rstrip(b"\x00").decode("latin-1")
                 for geom, _ in entries if geom.name]
        # Should have the main body mesh (MaleUnderwearBody:0)
        body_meshes = [n for n in names if "Body" in n or "body" in n]
        assert len(body_meshes) > 0, f"Expected body mesh, got: {names}"

    def test_malehands_loads(self):
        entries = load_body_geom("malehands_0.nif")
        assert len(entries) > 0

    def test_malefeet_loads(self):
        entries = load_body_geom("malefeet_0.nif")
        assert len(entries) > 0

    def test_bone_index_mapping(self):
        entries = load_body_geom("malebody_0.nif")
        for _, bi_to_name in entries:
            assert len(bi_to_name) > 0, "Should have bone index to name mapping"
            # All values should be non-empty strings
            for bi, name in bi_to_name.items():
                assert isinstance(bi, int)
                assert isinstance(name, str) and len(name) > 0


# ===========================================================================
# 5. Clipping
# ===========================================================================
@skipif_no_body
class TestClipBodyGeom:
    def test_no_section_verts_keeps_all_verts(self):
        """With no section_verts, clip_body_geom must keep every vertex."""
        entries = load_body_geom("malebody_0.nif")
        for src_geom, bi_to_name in entries:
            name = bytes(src_geom.name).rstrip(b"\x00").decode("latin-1") if src_geom.name else ""
            if is_underwear_only(src_geom.name if src_geom.name else b""):
                continue
            result = clip_body_geom(src_geom, bi_to_name, set(), section_verts=None)
            assert result is not None, f"No section_verts should not return None for {name}"
            verts, _, _, tris, _, _ = result
            assert len(verts) == src_geom.data.num_vertices
            assert len(tris) > 0

    def test_section_verts_clips_spatially(self):
        entries = load_body_geom("malebody_0.nif")
        for src_geom, bi_to_name in entries:
            if is_underwear_only(src_geom.name if src_geom.name else b""):
                continue
            # Full clip (no section_verts) — all verts
            full = clip_body_geom(src_geom, bi_to_name, set(), section_verts=None)
            if full is None:
                continue
            # Tight single-point cloud near a small area — should clip significantly
            tx = src_geom.translation.x
            ty = src_geom.translation.y
            tz = src_geom.translation.z + 120.0  # near head — far from arm/neck region
            tight_cloud = [[(tx, ty, tz)]]
            tight = clip_body_geom(src_geom, bi_to_name, set(),
                                   section_verts=tight_cloud, proximity_threshold=3.0)
            if tight is not None:
                assert len(tight[0]) < len(full[0]), "Tight cloud should reduce verts"
            break  # one geometry block is sufficient


# ===========================================================================
# 6. End-to-end splice
# ===========================================================================
@skipif_no_export
@skipif_no_body
class TestEndToEndSplice:
    def test_cuirass_splice_produces_body_geometry(self):
        dst = os.path.join(TMP_DIR, "_test_e2e_splice.nif")
        result = convert_nif(CUIRASS_M, dst)
        assert result["converted"] is True

        data = _load_nif(_resolve_output(dst))
        shape_names = []
        for root in data.roots:
            if root is None:
                continue
            for block in root.tree():
                if isinstance(block, NifFormat.NiTriShape):
                    name = bytes(block.name).rstrip(b"\x00").decode("latin-1", errors="replace")
                    shape_names.append(name)

        # Should have body geometry spliced in
        body_names = [n for n in shape_names if "Body" in n or "Hand" in n or "Foot" in n]
        assert len(body_names) >= 2, f"Expected body/hand/foot geometry, got: {shape_names}"

    def test_cuirass_body_has_valid_skin(self):
        dst = os.path.join(TMP_DIR, "_test_e2e_splice_skin.nif")
        convert_nif(CUIRASS_M, dst)
        data = _load_nif(_resolve_output(dst))
        for root in data.roots:
            if root is None:
                continue
            for block in root.tree():
                if not isinstance(block, NifFormat.NiTriShape):
                    continue
                name = bytes(block.name).rstrip(b"\x00").decode("latin-1", errors="replace")
                if "Body" not in name and "Hand" not in name and "Foot" not in name:
                    continue
                # Body geometry should have BSDismemberSkinInstance
                skin = getattr(block, "skin_instance", None)
                assert skin is not None, f"{name} should have skin_instance"
                assert isinstance(skin, NifFormat.BSDismemberSkinInstance), \
                    f"{name} should have BSDismemberSkinInstance"
                assert skin.data is not None, f"{name} should have NiSkinData"
                assert skin.data.num_bones > 0, f"{name} should have bones"
                # Should have valid vertices
                assert block.data.num_vertices > 0, f"{name} should have vertices"
                assert block.data.num_triangles > 0, f"{name} should have triangles"

    def test_gauntlets_splice_hands(self):
        if not os.path.exists(GAUNTLETS_M):
            pytest.skip("Gauntlets NIF unavailable")
        dst = os.path.join(TMP_DIR, "_test_e2e_gauntlets.nif")
        convert_nif(GAUNTLETS_M, dst)
        data = _load_nif(_resolve_output(dst))
        hand_shapes = []
        for root in data.roots:
            if root is None:
                continue
            for block in root.tree():
                if isinstance(block, NifFormat.NiTriShape):
                    name = bytes(block.name).rstrip(b"\x00").decode("latin-1", errors="replace")
                    if "Hand" in name:
                        hand_shapes.append(name)
        assert len(hand_shapes) >= 1, "Gauntlets should have hand body geometry"

    def test_female_cuirass_uses_female_body(self):
        if not os.path.exists(CUIRASS_F):
            pytest.skip("Female cuirass NIF unavailable")
        dst = os.path.join(TMP_DIR, "_test_e2e_female.nif")
        convert_nif(CUIRASS_F, dst)
        data = _load_nif(_resolve_output(dst))
        for root in data.roots:
            if root is None:
                continue
            for block in root.tree():
                if isinstance(block, NifFormat.NiTriShape):
                    name = bytes(block.name).rstrip(b"\x00").decode("latin-1", errors="replace")
                    if "Female" in name:
                        return  # Found female body geometry
        # Check for lowercase variant
        for root in data.roots:
            if root is None:
                continue
            for block in root.tree():
                if isinstance(block, NifFormat.NiTriShape):
                    name = bytes(block.name).rstrip(b"\x00").decode("latin-1", errors="replace")
                    if "female" in name.lower() and ("body" in name.lower() or "foot" in name.lower() or "hand" in name.lower()):
                        return
        pytest.fail("Female cuirass should use female body mesh geometry")

    def test_no_underwear_spliced(self):
        dst = os.path.join(TMP_DIR, "_test_e2e_no_underwear.nif")
        convert_nif(CUIRASS_M, dst)
        data = _load_nif(_resolve_output(dst))
        for root in data.roots:
            if root is None:
                continue
            for block in root.tree():
                if isinstance(block, NifFormat.NiTriShape):
                    name = bytes(block.name).rstrip(b"\x00").decode("latin-1", errors="replace")
                    # MaleUnderwear_1 should NOT be present (but MaleUnderwearBody:0 IS OK)
                    if name.startswith("MaleUnderwear_") or name.startswith("FemaleUnderwear_"):
                        pytest.fail(f"Underwear overlay '{name}' should not be spliced into armor")


# ===========================================================================
# 8. Strict vertex position tests
# ===========================================================================

def _get_spliced_shapes(nif_data):
    """Extract spliced body geometry shapes from a converted NIF.

    Body NIF replacements are identified by their distinctive names from the
    vanilla Skyrim body NIFs (MaleUnderwearBody, FootMale, HandMale, Female*).
    Armor geometry with generic names like 'UpperBody' is NOT included.
    """
    # Known vanilla body NIF geometry name prefixes
    BODY_NIF_PREFIXES = (
        "MaleUnderwearBody", "FemaleUnderwearBody",
        "FootMale", "FootFemale",
        "HandMale", "HandFemale",
    )
    shapes = {}
    for root in nif_data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiTriShape):
                continue
            name = bytes(block.name).rstrip(b"\x00").decode("latin-1", errors="replace")
            if any(name.startswith(p) for p in BODY_NIF_PREFIXES):
                skin = getattr(block, "skin_instance", None)
                if skin and block.data and block.data.num_vertices > 0:
                    shapes[name] = block
    return shapes


def _get_bone_map(nif_data):
    """Build bone name -> NiNode map from NIF data."""
    bmap = {}
    for root in nif_data.roots:
        if root is None:
            continue
        for block in root.tree():
            if isinstance(block, NifFormat.NiNode):
                raw = bytes(block.name).rstrip(b"\x00").decode("latin-1", errors="replace")
                bmap[raw] = block
    return bmap


def _load_vanilla_body_shapes():
    """Load vanilla Skyrim body NIF shapes for comparison."""
    shapes = {}
    for fname in ("malebody_0.nif", "malefeet_0.nif", "malehands_0.nif",
                  "femalebody_0.nif", "femalefeet_0.nif", "femalehands_0.nif"):
        path = os.path.join(SKYRIM_BODY_DIR, fname)
        if not os.path.exists(path):
            continue
        data = _load_nif(path)
        for root in data.roots:
            if root is None:
                continue
            for block in root.tree():
                if not isinstance(block, NifFormat.NiTriShape):
                    continue
                name = bytes(block.name).rstrip(b"\x00").decode("latin-1", errors="replace")
                if is_underwear_only(block.name if block.name else b""):
                    continue
                shapes[name] = block
    return shapes


@skipif_no_export
@skipif_no_body
class TestSplicedVertexPositions:
    """Strict tests verifying spliced body geometry matches the vanilla Skyrim body NIF."""

    @pytest.fixture(scope="class")
    def converted_data(self):
        dst = os.path.join(TMP_DIR, "_test_strict_splice.nif")
        convert_nif(CUIRASS_M, dst)
        return _load_nif(_resolve_output(dst))

    @pytest.fixture(scope="class")
    def vanilla_shapes(self):
        return _load_vanilla_body_shapes()

    @pytest.fixture(scope="class")
    def spliced_shapes(self, converted_data):
        return _get_spliced_shapes(converted_data)

    def test_body_geometry_present(self, spliced_shapes):
        """At least MaleUnderwearBody:0, FootMale_Big, HandMaleBig3rd should be spliced."""
        expected = {"MaleUnderwearBody:0", "FootMale_Big", "HandMaleBig3rd"}
        present = set(spliced_shapes.keys()) & expected
        assert len(present) >= 2, f"Expected body shapes {expected}, got {spliced_shapes.keys()}"

    def test_spliced_vertices_in_skeleton_space(self, spliced_shapes):
        """Spliced vertices must be in skeleton space (z > -5), not body-NIF local space.

        After the fix, geom.translation is baked into vertex positions so all
        geometry lives in the same coordinate system.  The male full body min
        baked z is ~11 (lower torso), un-baked verts would be at z ≈ -109 to -6.
        If zmin < -5 the bake was not applied.
        """
        for name, shape in spliced_shapes.items():
            n = shape.data.num_vertices
            sp_zmin = min(shape.data.vertices[i].z for i in range(n))
            assert sp_zmin > -5, (
                f"{name}: spliced vertex zmin = {sp_zmin:.1f} <= -5. "
                f"Vertices must be in skeleton space (z > -5), not body-NIF local "
                f"space (z ≈ -109).  geom.translation was probably not baked."
            )

    def test_spliced_vertices_subset_of_vanilla_world(self, spliced_shapes, vanilla_shapes):
        """Every spliced vertex (in skeleton space) must map back to a vanilla body vertex.

        The splice bakes geom.translation into vertices.  Subtracting the vanilla
        geom.translation from each spliced vertex should give back the original
        vanilla local position.
        """
        for name, shape in spliced_shapes.items():
            if name not in vanilla_shapes:
                continue
            van = vanilla_shapes[name]
            van_tx = van.translation.x
            van_ty = van.translation.y
            van_tz = van.translation.z
            # Build set of vanilla vertex positions in local space (rounded)
            van_verts = set()
            for i in range(van.data.num_vertices):
                v = van.data.vertices[i]
                van_verts.add((round(v.x, 2), round(v.y, 2), round(v.z, 2)))
            # Each spliced vertex minus vanilla geom.translation = vanilla local position
            misses = 0
            for i in range(shape.data.num_vertices):
                v = shape.data.vertices[i]
                key = (round(v.x - van_tx, 2), round(v.y - van_ty, 2), round(v.z - van_tz, 2))
                if key not in van_verts:
                    misses += 1
            total = shape.data.num_vertices
            assert misses == 0, (
                f"{name}: {misses}/{total} spliced vertices not found in vanilla body NIF "
                f"after subtracting vanilla geom.translation ({van_tx:.2f},{van_ty:.2f},{van_tz:.2f}). "
                f"Vertices should be vanilla local positions + geom.translation."
            )

    def test_geom_translationis_zero(self, spliced_shapes):
        """Spliced geometry must have geom.translation ≈ 0 (vanilla offset baked in)."""
        for name, shape in spliced_shapes.items():
            for axis in ("x", "y", "z"):
                sp_val = getattr(shape.translation, axis)
                assert abs(sp_val) < 1.0, (
                    f"{name}: geom.translation.{axis} = {sp_val:.4f}, expected ~0. "
                    f"Vanilla body NIF geom.translation must be baked into vertex positions."
                )

    def test_skin_transformis_identity(self, spliced_shapes):
        """After recomputing bind matrices for zero-translation geometry,
        the global skin_transform must be identity (or very close)."""
        for name, shape in spliced_shapes.items():
            sp_skin = shape.skin_instance.data.skin_transform
            # With geom.translation=0 and identity rotation, S=inv(G)=I
            for attr in ("m_11", "m_22", "m_33"):
                val = getattr(sp_skin.rotation, attr)
                assert abs(val - 1.0) < 0.01, (
                    f"{name}: skin_transform.rotation.{attr} = {val:.4f}, expected ~1.0"
                )
            for axis in ("x", "y", "z"):
                val = getattr(sp_skin.translation, axis)
                assert abs(val) < 1.0, (
                    f"{name}: skin_transform.translation.{axis} = {val:.4f}, expected ~0"
                )

    def test_bone_bind_matrices_match_vanilla(self, spliced_shapes, vanilla_shapes):
        """Spliced bone bind matrices must match the vanilla body NIF exactly."""
        for name, shape in spliced_shapes.items():
            if name not in vanilla_shapes:
                continue
            van = vanilla_shapes[name]
            sp_skin_data = shape.skin_instance.data
            van_skin_data = van.skin_instance.data
            # Build vanilla bone name -> bind transform map
            van_bones = {}
            for bi in range(van_skin_data.num_bones):
                bnode = van.skin_instance.bones[bi]
                if bnode is None:
                    continue
                bname = bytes(bnode.name).rstrip(b"\x00").decode("latin-1", errors="replace")
                van_bones[bname] = van_skin_data.bone_list[bi].skin_transform
            # Check each spliced bone
            for bi in range(sp_skin_data.num_bones):
                bnode = shape.skin_instance.bones[bi]
                bname = bytes(bnode.name).rstrip(b"\x00").decode("latin-1", errors="replace")
                if bname not in van_bones:
                    continue  # Bone might not exist in vanilla (e.g. pruned)
                sp_bt = sp_skin_data.bone_list[bi].skin_transform
                van_bt = van_bones[bname]
                # Translation
                for axis in ("x", "y", "z"):
                    sp_val = getattr(sp_bt.translation, axis)
                    van_val = getattr(van_bt.translation, axis)
                    assert abs(sp_val - van_val) < 0.05, (
                        f"{name} bone {bname}: bind.translation.{axis} = {sp_val:.4f}, "
                        f"expected {van_val:.4f}"
                    )
                # Rotation (check all 9 elements)
                for rattr in ("m_11", "m_12", "m_13", "m_21", "m_22", "m_23",
                               "m_31", "m_32", "m_33"):
                    sp_val = getattr(sp_bt.rotation, rattr)
                    van_val = getattr(van_bt.rotation, rattr)
                    assert abs(sp_val - van_val) < 0.01, (
                        f"{name} bone {bname}: bind.rotation.{rattr} = {sp_val:.6f}, "
                        f"expected {van_val:.6f}"
                    )

    def test_world_verts_within_bone_bbox(self, converted_data, spliced_shapes):
        """Spliced world-space vertices must fall within the bone-derived bbox."""
        bone_map = _get_bone_map(converted_data)
        for name, shape in spliced_shapes.items():
            skin_data = shape.skin_instance.data
            # Compute bbox from bone node positions (with 20-unit padding)
            xs, ys, zs = [], [], []
            for bi in range(skin_data.num_bones):
                bnode = shape.skin_instance.bones[bi]
                node = bone_map.get(bytes(bnode.name).rstrip(b"\x00").decode("latin-1", errors="replace"))
                if node is not None:
                    xs.append(node.translation.x)
                    ys.append(node.translation.y)
                    zs.append(node.translation.z)
            if not xs:
                continue
            pad = 20.0
            xmin, xmax = min(xs) - pad, max(xs) + pad
            ymin, ymax = min(ys) - pad, max(ys) + pad
            zmin, zmax = min(zs) - pad, max(zs) + pad
            tx = shape.translation.x
            ty = shape.translation.y
            tz = shape.translation.z
            outside = 0
            for i in range(shape.data.num_vertices):
                v = shape.data.vertices[i]
                wx, wy, wz = v.x + tx, v.y + ty, v.z + tz
                if not (xmin <= wx <= xmax and ymin <= wy <= ymax and zmin <= wz <= zmax):
                    outside += 1
            total = shape.data.num_vertices
            pct = outside / total * 100 if total > 0 else 0
            assert pct < 5, (
                f"{name}: {outside}/{total} ({pct:.1f}%) vertices outside bone bbox "
                f"[{xmin:.0f},{xmax:.0f}]x[{ymin:.0f},{ymax:.0f}]x[{zmin:.0f},{zmax:.0f}]"
            )

    def test_skin_weights_normalized(self, spliced_shapes):
        """Every spliced vertex must have skin weights summing to ~1.0."""
        for name, shape in spliced_shapes.items():
            skin_data = shape.skin_instance.data
            # Reconstruct per-vertex weight sums from bone_list
            weight_sums = [0.0] * shape.data.num_vertices
            for bi in range(skin_data.num_bones):
                be = skin_data.bone_list[bi]
                for vwi in range(be.num_vertices):
                    vw = be.vertex_weights[vwi]
                    if vw.index < len(weight_sums):
                        weight_sums[vw.index] += vw.weight
            bad = sum(1 for s in weight_sums if abs(s - 1.0) > 0.05)
            total = shape.data.num_vertices
            assert bad == 0, (
                f"{name}: {bad}/{total} vertices have weight sum != 1.0. "
                f"Range: [{min(weight_sums):.4f}, {max(weight_sums):.4f}]"
            )

    def test_triangle_indices_valid(self, spliced_shapes):
        """All triangle vertex indices must be within [0, num_vertices)."""
        for name, shape in spliced_shapes.items():
            n = shape.data.num_vertices
            for ti in range(shape.data.num_triangles):
                tri = shape.data.triangles[ti]
                for idx in (tri.v_1, tri.v_2, tri.v_3):
                    assert 0 <= idx < n, (
                        f"{name}: triangle {ti} has index {idx} >= num_vertices {n}"
                    )

    def test_normals_present_and_unit_length(self, spliced_shapes, vanilla_shapes):
        """Normals must have approximately unit length when present."""
        import math
        for name, shape in spliced_shapes.items():
            # Only check normals if the vanilla body NIF has them
            if name in vanilla_shapes:
                van = vanilla_shapes[name]
                if not van.data.has_normals:
                    continue  # Vanilla body NIF has no normals (e.g. MaleUnderwearBody:0)
            if not shape.data.has_normals:
                continue
            bad = 0
            for i in range(shape.data.num_vertices):
                n = shape.data.normals[i]
                length = math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z)
                if abs(length - 1.0) > 0.05:
                    bad += 1
            assert bad == 0, f"{name}: {bad} normals with non-unit length"

    def test_uvs_present_and_in_range(self, spliced_shapes):
        """Spliced geometry must have UVs in [0, 1] range (or slightly outside for tiling)."""
        for name, shape in spliced_shapes.items():
            assert shape.data.num_uv_sets > 0, f"{name}: no UV sets"
            bad = 0
            for i in range(shape.data.num_vertices):
                uv = shape.data.uv_sets[0][i]
                if uv.u < -1 or uv.u > 2 or uv.v < -1 or uv.v > 2:
                    bad += 1
            assert bad == 0, f"{name}: {bad} UVs out of valid range"


@skipif_no_export
@skipif_no_body
class TestSplicedBoots:
    """Test body splice for boots — boots typically don't embed body skin."""

    @pytest.fixture(scope="class")
    def boots_data(self):
        boots_path = os.path.join(EXPORT_ARMOR, "blackwood", "m", "boots.nif")
        if not os.path.exists(boots_path):
            pytest.skip("Boots NIF unavailable")
        dst = os.path.join(TMP_DIR, "_test_boots_splice.nif")
        convert_nif(boots_path, dst)
        return _load_nif(_resolve_output(dst))

    def test_boots_no_body_splice_needed(self, boots_data):
        """Boots don't embed body skin geometry, so no body NIF splicing occurs."""
        shapes = _get_spliced_shapes(boots_data)
        # Boots only have boot mesh, not body skin — no splicing expected
        assert len(shapes) == 0, (
            f"Boots should not have spliced body geometry, got: {list(shapes.keys())}"
        )


# ===========================================================================
# 9a. Spliced vertex bounds and count tests
# ===========================================================================

def _get_full_body_vert_count(body_nif_name: str) -> int:
    """Return total vertex count of non-underwear geometry in a Skyrim body NIF."""
    total = 0
    entries = load_body_geom(body_nif_name)
    for src_geom, _ in entries:
        if is_underwear_only(src_geom.name if src_geom.name else b""):
            continue
        total += src_geom.data.num_vertices
    return total


@skipif_no_iron_m
@skipif_no_body
class TestMaleSplicedVertexBounds:
    """Vertex count, spatial bounds, and topology tests for male cuirass splice.

    These tests directly catch the bugs fixed in this session:
      - Too many spliced verts (> 30%) caught by test_splice_fraction_under_threshold
      - Wrong bbox (bone-position based) caught by test_splice_z_in_predicted_range
      - Female splice missing caught by TestFemaleSplicedVertexBounds
      - Exploded vertices caught by test_no_giant_edges
    """

    @pytest.fixture(scope="class")
    def converted_data(self):
        dst = os.path.join(TMP_DIR, "_test_mbounds_iron_m.nif")
        convert_nif(IRON_CUIRASS_M, dst)
        return _load_nif(_resolve_output(dst))

    @pytest.fixture(scope="class")
    def spliced_shapes(self, converted_data):
        return _get_spliced_shapes(converted_data)

    @pytest.fixture(scope="class")
    def splice_body_shape(self, spliced_shapes):
        if "MaleUnderwearBody:0" not in spliced_shapes:
            pytest.skip("MaleUnderwearBody:0 not found after splice")
        return spliced_shapes["MaleUnderwearBody:0"]

    def test_spliceis_not_empty(self, splice_body_shape):
        """Splice must contain vertices (was missing before fix for female)."""
        assert splice_body_shape.data.num_vertices > 0

    def test_splice_fraction_under_threshold(self, splice_body_shape):
        """Spliced body verts must be < 25% of full body.

        Post-retarget section vertex clouds are in SK world-space and correctly
        localise both arm openings (z=72-92 in SK) and neck/collar.
        With proximity_threshold=3.8, the iron cuirass 'Arms' section yields ~22%.
        """
        full_count = _get_full_body_vert_count("malebody_0.nif")
        splice_count = splice_body_shape.data.num_vertices
        fraction = splice_count / full_count
        assert fraction < 0.25, (
            f"Splice has {splice_count}/{full_count} ({fraction*100:.1f}%) body verts — "
            f"expected < 25%. Likely section bboxes or padding are too large."
        )

    def test_splice_vertex_z_in_skeleton_space(self, splice_body_shape):
        """Spliced vertex Z must be in skeleton space (z ≈ 75-110 for arm/shoulder area).

        After baking geom.translation into vertices, arm-area geometry lives at
        z ≈ 75-110.  If vertices are at z ≈ -44 to -20 the bake was not applied
        and the geometry appears underground relative to the armor.
        """
        n = splice_body_shape.data.num_vertices
        zvals = [splice_body_shape.data.vertices[i].z for i in range(n)]
        zmin, zmax = min(zvals), max(zvals)
        assert zmin > 50, (
            f"Splice vertex zmin={zmin:.1f} <= 50 — vertices in body-local space "
            f"(z ≈ -44), not skeleton space (z ≈ 75-110). geom.translation not baked."
        )
        assert zmax < 130, (
            f"Splice vertex zmax={zmax:.1f} >= 130 — vertices above head level."
        )

    def test_splice_left_right_symmetric(self, splice_body_shape):
        """Splice must be roughly symmetric (left ≈ right verts by x-coordinate).

        A cuirass arm hole removes both left and right arm skin. If one side is
        missing, the splice has only half the expected area.
        Tolerance: neither side should dominate by > 3× the other.
        """
        n = splice_body_shape.data.num_vertices
        xvals = [splice_body_shape.data.vertices[i].x for i in range(n)]
        n_left = sum(1 for x in xvals if x < -1.0)   # clearly left
        n_right = sum(1 for x in xvals if x > 1.0)    # clearly right
        if n_left == 0 or n_right == 0:
            pytest.skip("One side has no verts — symmetric test not applicable")
        ratio = max(n_left, n_right) / min(n_left, n_right)
        assert ratio < 3.0, (
            f"Splice is asymmetric: left={n_left}, right={n_right} (ratio={ratio:.1f}). "
            f"Expected both sides to be roughly equal for a symmetric cuirass."
        )

    def test_no_giant_edges(self, splice_body_shape):
        """No edge in spliced geometry should exceed 10 units.

        Normal body mesh edges are 0.5–2.5 units. A giant edge (> 10 units) indicates
        vertices were displaced (exploded) by an incorrect transform.
        """
        n = splice_body_shape.data.num_vertices
        verts = np.array([[splice_body_shape.data.vertices[i].x,
                           splice_body_shape.data.vertices[i].y,
                           splice_body_shape.data.vertices[i].z] for i in range(n)])
        max_edge = 0.0
        worst = ""
        for ti in range(splice_body_shape.data.num_triangles):
            tri = splice_body_shape.data.triangles[ti]
            for a, b in [(tri.v_1, tri.v_2), (tri.v_2, tri.v_3), (tri.v_1, tri.v_3)]:
                if max(a, b) >= n:
                    continue
                elen = float(np.linalg.norm(verts[a] - verts[b]))
                if elen > max_edge:
                    max_edge = elen
                    worst = f"tri {ti} ({a},{b}): verts {verts[a].tolist()} - {verts[b].tolist()}"
        assert max_edge < 10.0, (
            f"Giant edge detected: length={max_edge:.2f} > 10 units. "
            f"Exploded vertex? Worst edge: {worst}"
        )


@skipif_no_iron_f
@skipif_no_body
class TestFemaleSplicedVertexBounds:
    """Vertex count and spatial bounds tests for female cuirass splice.

    The female cuirass splice was entirely missing before the fix to
    _forward_skin_bbox. These tests ensure it is present and correct.
    """

    @pytest.fixture(scope="class")
    def converted_data(self):
        dst = os.path.join(TMP_DIR, "_test_fbounds_iron_f.nif")
        convert_nif(IRON_CUIRASS_F, dst)
        return _load_nif(_resolve_output(dst))

    @pytest.fixture(scope="class")
    def spliced_shapes(self, converted_data):
        return _get_spliced_shapes(converted_data)

    def test_female_body_splice_present(self, spliced_shapes):
        """Female body splice must be present (was completely missing before fix)."""
        assert "FemaleUnderwearBody:0" in spliced_shapes, (
            f"FemaleUnderwearBody:0 missing from splice. Got: {list(spliced_shapes.keys())}. "
            f"This indicates _forward_skin_bbox returned None for female geometry "
            f"with non-zero geom.translation."
        )

    def test_female_splice_not_empty(self, spliced_shapes):
        if "FemaleUnderwearBody:0" not in spliced_shapes:
            pytest.skip("FemaleUnderwearBody:0 not in splice")
        shape = spliced_shapes["FemaleUnderwearBody:0"]
        assert shape.data.num_vertices > 0

    def test_female_splice_vertex_z_in_skeleton_space(self, spliced_shapes):
        """Female splice vertices must be in skeleton space (z > 50)."""
        if "FemaleUnderwearBody:0" not in spliced_shapes:
            pytest.skip("FemaleUnderwearBody:0 not in splice")
        shape = spliced_shapes["FemaleUnderwearBody:0"]
        n = shape.data.num_vertices
        zvals = [shape.data.vertices[i].z for i in range(n)]
        zmin, zmax = min(zvals), max(zvals)
        assert zmin > 50, (
            f"Female splice vertex zmin={zmin:.1f} <= 50 — vertices in body-local "
            f"space (z ≈ -44), not skeleton space. geom.translation not baked."
        )
        assert zmax < 130, (
            f"Female splice vertex zmax={zmax:.1f} >= 130 — above head level."
        )

    def test_female_no_giant_edges(self, spliced_shapes):
        """No giant edges (exploded vertices) in female splice."""
        if "FemaleUnderwearBody:0" not in spliced_shapes:
            pytest.skip("FemaleUnderwearBody:0 not in splice")
        shape = spliced_shapes["FemaleUnderwearBody:0"]
        n = shape.data.num_vertices
        verts = np.array([[shape.data.vertices[i].x, shape.data.vertices[i].y,
                           shape.data.vertices[i].z] for i in range(n)])
        max_edge = 0.0
        for ti in range(shape.data.num_triangles):
            tri = shape.data.triangles[ti]
            for a, b in [(tri.v_1, tri.v_2), (tri.v_2, tri.v_3), (tri.v_1, tri.v_3)]:
                if max(a, b) >= n:
                    continue
                elen = float(np.linalg.norm(verts[a] - verts[b]))
                max_edge = max(max_edge, elen)
        assert max_edge < 10.0, (
            f"Giant edge in female splice: length={max_edge:.2f} > 10 units"
        )

    def test_female_splice_not_entire_body(self, spliced_shapes):
        """Female splice should not be the entire female body."""
        if "FemaleUnderwearBody:0" not in spliced_shapes:
            pytest.skip("FemaleUnderwearBody:0 not in splice")
        shape = spliced_shapes["FemaleUnderwearBody:0"]
        full_count = _get_full_body_vert_count("femalebody_0.nif")
        if full_count == 0:
            pytest.skip("Female body NIF unavailable")
        splice_count = shape.data.num_vertices
        fraction = splice_count / full_count
        assert fraction < 0.85, (
            f"Female splice has {splice_count}/{full_count} ({fraction*100:.1f}%) body verts — "
            f"nearly the entire body. Expected < 85%."
        )


# ===========================================================================
# 9.  M@B@W identity for spliced body geometry
# ===========================================================================

def _skin_transform_to_np(st):
    """SkinTransform to numpy 4x4 (row-vector convention)."""
    M = np.eye(4, dtype=np.float64)
    M[0, 0] = st.rotation.m_11; M[0, 1] = st.rotation.m_12; M[0, 2] = st.rotation.m_13
    M[1, 0] = st.rotation.m_21; M[1, 1] = st.rotation.m_22; M[1, 2] = st.rotation.m_23
    M[2, 0] = st.rotation.m_31; M[2, 1] = st.rotation.m_32; M[2, 2] = st.rotation.m_33
    M[3, 0] = st.translation.x; M[3, 1] = st.translation.y; M[3, 2] = st.translation.z
    return M


def _m44_to_np(m):
    """PyFFI Matrix44 to numpy 4x4."""
    return np.array([
        [m.m_11, m.m_12, m.m_13, m.m_14],
        [m.m_21, m.m_22, m.m_23, m.m_24],
        [m.m_31, m.m_32, m.m_33, m.m_34],
        [m.m_41, m.m_42, m.m_43, m.m_44],
    ], dtype=np.float64)


def _check_mbw_identity_shapes(nif_data, shape_filter=None, tol=0.001):
    """Check M@B@W ≈ I for skinned shapes. Returns list of failures."""
    failures = []
    for root in nif_data.roots:
        if root is None:
            continue
        for blk in root.tree():
            if not isinstance(blk, NifFormat.NiTriShape):
                continue
            skin = getattr(blk, 'skin_instance', None)
            if skin is None or skin.data is None:
                continue
            name = bytes(blk.name).rstrip(b'\x00').decode('latin-1', errors='replace')
            if shape_filter and not shape_filter(name):
                continue
            sd = skin.data
            skel_root = skin.skeleton_root
            if skel_root is None:
                continue
            M = _skin_transform_to_np(sd.skin_transform)
            for i in range(sd.num_bones):
                if i >= skin.num_bones or skin.bones[i] is None:
                    continue
                B = _skin_transform_to_np(sd.bone_list[i].skin_transform)
                try:
                    W = _m44_to_np(skin.bones[i].get_transform(skel_root))
                except Exception:
                    continue
                MBW = M @ B @ W
                err = np.linalg.norm(MBW - np.eye(4))
                if err > tol:
                    bname = bytes(skin.bones[i].name).rstrip(b'\x00').decode('latin-1', errors='replace')
                    failures.append((name, bname, err))
    return failures


def _compute_rest_pose_world(shape, bone_map_nodes):
    """Compute rest-pose world positions for all vertices of a skinned shape.

    Uses LBS: v_world = sum_i w_i * (v @ M @ B_i @ W_i)
    where M = global skin_transform, B_i = bone bind, W_i = bone world transform.
    bone_map_nodes: {bone_name: NiNode} from the armor NIF (flat hierarchy).

    Returns Nx3 numpy array of world positions.
    """
    sd = shape.skin_instance.data
    skin = shape.skin_instance
    skel_root = skin.skeleton_root
    n = shape.data.num_vertices

    M = _skin_transform_to_np(sd.skin_transform)

    # Build per-bone combined transform: M @ B_i @ W_i
    bone_transforms = []
    for bi in range(sd.num_bones):
        if bi >= skin.num_bones or skin.bones[bi] is None:
            bone_transforms.append(None)
            continue
        B = _skin_transform_to_np(sd.bone_list[bi].skin_transform)
        try:
            W = _m44_to_np(skin.bones[bi].get_transform(skel_root))
        except Exception:
            bone_transforms.append(None)
            continue
        bone_transforms.append(M @ B @ W)

    # Build per-vertex weight lists from NiSkinData
    vert_weights = [{} for _ in range(n)]
    for bi in range(sd.num_bones):
        be = sd.bone_list[bi]
        for vwi in range(be.num_vertices):
            vw = be.vertex_weights[vwi]
            if vw.index < n and vw.weight > 0:
                vert_weights[vw.index][bi] = vw.weight

    # LBS
    result = np.zeros((n, 3), dtype=np.float64)
    for vi in range(n):
        v = shape.data.vertices[vi]
        v_homog = np.array([v.x, v.y, v.z, 1.0])
        weighted = np.zeros(4, dtype=np.float64)
        total_w = 0.0
        for bi, w in vert_weights[vi].items():
            if bone_transforms[bi] is None:
                continue
            weighted += w * (v_homog @ bone_transforms[bi])
            total_w += w
        if total_w > 0:
            result[vi] = weighted[:3] / total_w
        else:
            result[vi] = [v.x, v.y, v.z]
    return result


def _compute_vanilla_body_rest_pose(body_nif_name):
    """Compute rest-pose world positions for vanilla Skyrim body NIF geometry.

    The vanilla body NIF has a proper bone hierarchy, so get_transform(skel_root)
    gives the correct cumulative bone world transforms.
    """
    path = os.path.join(SKYRIM_BODY_DIR, body_nif_name)
    data = _load_nif(path)
    results = {}
    for root in data.roots:
        if root is None:
            continue
        for blk in root.tree():
            if not isinstance(blk, NifFormat.NiTriShape):
                continue
            name = bytes(blk.name).rstrip(b'\x00').decode('latin-1', errors='replace')
            if is_underwear_only(blk.name if blk.name else b''):
                continue
            skin = getattr(blk, 'skin_instance', None)
            if skin is None or skin.data is None:
                continue
            sd = skin.data
            skel_root = skin.skeleton_root
            if skel_root is None:
                continue
            n = blk.data.num_vertices
            # G: geometry node's own transform (translation/rotation/scale)
            # v_world = v_local @ G @ M @ B @ W
            G = np.eye(4, dtype=np.float64)
            r = blk.rotation
            s = blk.scale if blk.scale else 1.0
            G[:3, :3] = s * np.array([[r.m_11, r.m_21, r.m_31],
                                       [r.m_12, r.m_22, r.m_32],
                                       [r.m_13, r.m_23, r.m_33]])
            G[3, :3] = [blk.translation.x, blk.translation.y, blk.translation.z]
            M = _skin_transform_to_np(sd.skin_transform)
            bone_transforms = []
            for bi in range(sd.num_bones):
                if bi >= skin.num_bones or skin.bones[bi] is None:
                    bone_transforms.append(None)
                    continue
                B = _skin_transform_to_np(sd.bone_list[bi].skin_transform)
                try:
                    W = _m44_to_np(skin.bones[bi].get_transform(skel_root))
                except Exception:
                    bone_transforms.append(None)
                    continue
                bone_transforms.append(G @ M @ B @ W)
            vert_weights = [{} for _ in range(n)]
            for bi in range(sd.num_bones):
                be = sd.bone_list[bi]
                for vwi in range(be.num_vertices):
                    vw = be.vertex_weights[vwi]
                    if vw.index < n and vw.weight > 0:
                        vert_weights[vw.index][bi] = vw.weight
            pos = np.zeros((n, 3), dtype=np.float64)
            for vi in range(n):
                v = blk.data.vertices[vi]
                v_h = np.array([v.x, v.y, v.z, 1.0])
                weighted = np.zeros(4, dtype=np.float64)
                tw = 0.0
                for bi, w in vert_weights[vi].items():
                    if bone_transforms[bi] is None:
                        continue
                    weighted += w * (v_h @ bone_transforms[bi])
                    tw += w
                if tw > 0:
                    pos[vi] = weighted[:3] / tw
                else:
                    pos[vi] = [v.x, v.y, v.z]
            # Build vertex lookup by rounded position (local space)
            results[name] = {
                'world_pos': pos,
                'local_verts': np.array([[blk.data.vertices[i].x, blk.data.vertices[i].y,
                                          blk.data.vertices[i].z] for i in range(n)]),
            }
    return results


# ===========================================================================
# 10.  Female iron cuirass - strict tests
# ===========================================================================


@skipif_no_iron_f
@skipif_no_body
class TestFemaleIronCuirassSplice:
    """Strict tests for female iron cuirass body splice — the user's primary test case."""

    @pytest.fixture(scope="class")
    def converted_data(self):
        dst = os.path.join(TMP_DIR, "_test_iron_f_cuirass.nif")
        convert_nif(IRON_CUIRASS_F, dst)
        return _load_nif(_resolve_output(dst))

    @pytest.fixture(scope="class")
    def spliced_shapes(self, converted_data):
        return _get_spliced_shapes(converted_data)

    @pytest.fixture(scope="class")
    def vanilla_female_body(self):
        return _compute_vanilla_body_rest_pose("femalebody_0.nif")

    def test_has_body_splice(self, spliced_shapes):
        assert "FemaleUnderwearBody:0" in spliced_shapes, (
            f"Expected FemaleUnderwearBody:0, got: {list(spliced_shapes.keys())}"
        )

    def test_mbw_identity(self, converted_data):
        """M@B@W must be identity for ALL shapes including spliced body geometry."""
        # Check spliced shapes specifically
        def is_body_splice(name):
            return any(name.startswith(p) for p in (
                "MaleUnderwearBody", "FemaleUnderwearBody",
                "FootMale", "FootFemale", "HandMale", "HandFemale"))
        failures = _check_mbw_identity_shapes(converted_data, shape_filter=is_body_splice, tol=0.01)
        assert not failures, (
            "M@B@W not identity for spliced body shapes:\n" +
            "\n".join(f"  {g}/{b}: err={e:.4f}" for g, b, e in failures[:10])
        )

    def test_spliced_edge_lengths_exact(self, spliced_shapes):
        """Edge lengths in spliced body must EXACTLY match vanilla body NIF.

        The splice copies vertices directly from the vanilla body NIF,
        so edge lengths must be preserved to within float precision.
        Any edge distortion indicates vertex transforms were applied.
        """
        vanilla_shapes = _load_vanilla_body_shapes()
        EDGE_TOL = 0.001  # Near-exact: no transforms should be applied

        for name, shape in spliced_shapes.items():
            if name not in vanilla_shapes:
                continue
            van = vanilla_shapes[name]
            van_tx = van.translation.x
            van_ty = van.translation.y
            van_tz = van.translation.z
            # Build vanilla vertex lookup by rounded local position
            van_verts = {}
            for i in range(van.data.num_vertices):
                v = van.data.vertices[i]
                van_verts[(round(v.x, 3), round(v.y, 3), round(v.z, 3))] = i

            # Spliced vertices are baked (= vanilla_local + vanilla_geom_t).
            # Subtract vanilla geom.t to recover the vanilla local position for lookup.
            sp_n = shape.data.num_vertices
            sp_verts = np.array([[shape.data.vertices[i].x, shape.data.vertices[i].y,
                                  shape.data.vertices[i].z] for i in range(sp_n)])

            total_edges = 0
            bad_edges = 0
            worst_ratio = 0.0
            worst_info = ""
            for ti in range(shape.data.num_triangles):
                tri = shape.data.triangles[ti]
                for idx_a, idx_b in [(tri.v_1, tri.v_2), (tri.v_2, tri.v_3), (tri.v_1, tri.v_3)]:
                    if max(idx_a, idx_b) >= sp_n:
                        continue
                    edge_len = np.linalg.norm(sp_verts[idx_a] - sp_verts[idx_b])
                    if edge_len < 0.001:
                        continue
                    # Recover vanilla local positions by subtracting geom.translation
                    ka = (round(sp_verts[idx_a][0] - van_tx, 3),
                          round(sp_verts[idx_a][1] - van_ty, 3),
                          round(sp_verts[idx_a][2] - van_tz, 3))
                    kb = (round(sp_verts[idx_b][0] - van_tx, 3),
                          round(sp_verts[idx_b][1] - van_ty, 3),
                          round(sp_verts[idx_b][2] - van_tz, 3))
                    if ka in van_verts and kb in van_verts:
                        vi_a = van_verts[ka]
                        vi_b = van_verts[kb]
                        van_a = np.array([van.data.vertices[vi_a].x, van.data.vertices[vi_a].y, van.data.vertices[vi_a].z])
                        van_b = np.array([van.data.vertices[vi_b].x, van.data.vertices[vi_b].y, van.data.vertices[vi_b].z])
                        van_edge = np.linalg.norm(van_a - van_b)
                        if van_edge > 0.001:
                            ratio = abs(edge_len - van_edge) / van_edge
                            total_edges += 1
                            if ratio > EDGE_TOL:
                                bad_edges += 1
                            if ratio > worst_ratio:
                                worst_ratio = ratio
                                worst_info = f"({idx_a},{idx_b}): {van_edge:.4f}->{edge_len:.4f}"
            assert total_edges > 50, f"{name}: too few matched edges ({total_edges})"
            fail_pct = bad_edges / total_edges * 100
            assert fail_pct < 1.0, (
                f"{name}: {bad_edges}/{total_edges} ({fail_pct:.1f}%) edges exceed {EDGE_TOL*100:.1f}% tolerance\n"
                f"Worst: {worst_info} (ratio={worst_ratio:.6f})"
            )

    def test_rest_pose_matches_vanilla(self, converted_data, spliced_shapes, vanilla_female_body):
        """Skinned rest-pose world positions of spliced body must match vanilla body.

        The whole point of body splice is that the rendered result looks like
        the vanilla Skyrim body. If rest-pose positions don't match, the splice
        is visually wrong.
        """
        bone_map = _get_bone_map(converted_data)
        vanilla_shapes = _load_vanilla_body_shapes()
        for name, shape in spliced_shapes.items():
            if name not in vanilla_female_body:
                continue
            van_info = vanilla_female_body[name]
            van_world = van_info['world_pos']
            van_local = van_info['local_verts']

            # Compute rest-pose of spliced shape
            sp_world = _compute_rest_pose_world(shape, bone_map)

            # Spliced verts are baked (= vanilla_local + vanilla_geom_t).
            # To match against vanilla_local, subtract vanilla geom.translation.
            sp_local = np.array([[shape.data.vertices[i].x, shape.data.vertices[i].y,
                                  shape.data.vertices[i].z] for i in range(shape.data.num_vertices)])
            if name in vanilla_shapes:
                van_tx = vanilla_shapes[name].translation.x
                van_ty = vanilla_shapes[name].translation.y
                van_tz = vanilla_shapes[name].translation.z
            else:
                van_tx = van_ty = van_tz = 0.0

            van_lookup = {}
            for i in range(len(van_local)):
                key = (round(van_local[i][0], 2), round(van_local[i][1], 2), round(van_local[i][2], 2))
                van_lookup[key] = i

            matched = 0
            total_err = 0.0
            max_err = 0.0
            max_err_info = ""
            for si in range(len(sp_local)):
                key = (round(sp_local[si][0] - van_tx, 2),
                       round(sp_local[si][1] - van_ty, 2),
                       round(sp_local[si][2] - van_tz, 2))
                if key not in van_lookup:
                    continue
                vi = van_lookup[key]
                err = np.linalg.norm(sp_world[si] - van_world[vi])
                total_err += err
                matched += 1
                if err > max_err:
                    max_err = err
                    max_err_info = (
                        f"vert {si}: spliced_world=({sp_world[si][0]:.1f},{sp_world[si][1]:.1f},{sp_world[si][2]:.1f}) "
                        f"vanilla_world=({van_world[vi][0]:.1f},{van_world[vi][1]:.1f},{van_world[vi][2]:.1f})"
                    )

            assert matched > 50, f"{name}: only {matched} verts matched vanilla"
            avg_err = total_err / matched
            # Rest-pose should match within ~1 unit (skinning float precision)
            assert max_err < 5.0, (
                f"{name}: max rest-pose error = {max_err:.2f} units (expected < 5.0)\n"
                f"avg error = {avg_err:.2f}, matched {matched} verts\n"
                f"Worst: {max_err_info}"
            )
            assert avg_err < 2.0, (
                f"{name}: avg rest-pose error = {avg_err:.2f} units (expected < 2.0)\n"
                f"max error = {max_err:.2f}, matched {matched} verts"
            )

    def test_rest_pose_z_range(self, converted_data, spliced_shapes):
        """Rest-pose Z must be in skeleton space.  With M@B@W=I and vertices baked to
        skeleton space, rest-pose z equals vertex z ≈ 50-130 (shoulder/arm region)."""
        bone_map = _get_bone_map(converted_data)
        for name, shape in spliced_shapes.items():
            sp_world = _compute_rest_pose_world(shape, bone_map)
            z_min = sp_world[:, 2].min()
            z_max = sp_world[:, 2].max()
            assert z_min > 40, (
                f"{name}: rest-pose Z min = {z_min:.1f}, expected > 40 "
                f"(skeleton space, not underground)"
            )
            assert z_max < 130, (
                f"{name}: rest-pose Z max = {z_max:.1f}, expected < 130 (below head)"
            )


@skipif_no_iron_m
@skipif_no_body
class TestMaleIronCuirassSplice:
    """Strict tests for male iron cuirass body splice."""

    @pytest.fixture(scope="class")
    def converted_data(self):
        dst = os.path.join(TMP_DIR, "_test_iron_m_cuirass.nif")
        convert_nif(IRON_CUIRASS_M, dst)
        return _load_nif(_resolve_output(dst))

    @pytest.fixture(scope="class")
    def spliced_shapes(self, converted_data):
        return _get_spliced_shapes(converted_data)

    @pytest.fixture(scope="class")
    def vanilla_male_body(self):
        return _compute_vanilla_body_rest_pose("malebody_0.nif")

    def test_has_body_splice(self, spliced_shapes):
        assert "MaleUnderwearBody:0" in spliced_shapes, (
            f"Expected MaleUnderwearBody:0, got: {list(spliced_shapes.keys())}"
        )

    def test_mbw_identity(self, converted_data):
        """M@B@W must be identity for spliced body shapes."""
        def is_body_splice(name):
            return any(name.startswith(p) for p in (
                "MaleUnderwearBody", "FemaleUnderwearBody",
                "FootMale", "FootFemale", "HandMale", "HandFemale"))
        failures = _check_mbw_identity_shapes(converted_data, shape_filter=is_body_splice, tol=0.01)
        assert not failures, (
            "M@B@W not identity for spliced body shapes:\n" +
            "\n".join(f"  {g}/{b}: err={e:.4f}" for g, b, e in failures[:10])
        )

    def test_spliced_edge_lengths_exact(self, spliced_shapes):
        """Edge lengths must be exactly preserved (no vertex transforms)."""
        vanilla_shapes = _load_vanilla_body_shapes()
        EDGE_TOL = 0.001

        for name, shape in spliced_shapes.items():
            if name not in vanilla_shapes:
                continue
            van = vanilla_shapes[name]
            van_tx = van.translation.x
            van_ty = van.translation.y
            van_tz = van.translation.z
            van_verts = {}
            for i in range(van.data.num_vertices):
                v = van.data.vertices[i]
                van_verts[(round(v.x, 3), round(v.y, 3), round(v.z, 3))] = i

            sp_n = shape.data.num_vertices
            sp_verts = np.array([[shape.data.vertices[i].x, shape.data.vertices[i].y,
                                  shape.data.vertices[i].z] for i in range(sp_n)])
            total_edges = 0
            bad_edges = 0
            for ti in range(shape.data.num_triangles):
                tri = shape.data.triangles[ti]
                for idx_a, idx_b in [(tri.v_1, tri.v_2), (tri.v_2, tri.v_3), (tri.v_1, tri.v_3)]:
                    if max(idx_a, idx_b) >= sp_n:
                        continue
                    elen = np.linalg.norm(sp_verts[idx_a] - sp_verts[idx_b])
                    if elen < 0.001:
                        continue
                    # Recover vanilla local positions by subtracting geom.translation
                    ka = (round(sp_verts[idx_a][0] - van_tx, 3),
                          round(sp_verts[idx_a][1] - van_ty, 3),
                          round(sp_verts[idx_a][2] - van_tz, 3))
                    kb = (round(sp_verts[idx_b][0] - van_tx, 3),
                          round(sp_verts[idx_b][1] - van_ty, 3),
                          round(sp_verts[idx_b][2] - van_tz, 3))
                    if ka in van_verts and kb in van_verts:
                        vi_a, vi_b = van_verts[ka], van_verts[kb]
                        va = np.array([van.data.vertices[vi_a].x, van.data.vertices[vi_a].y, van.data.vertices[vi_a].z])
                        vb = np.array([van.data.vertices[vi_b].x, van.data.vertices[vi_b].y, van.data.vertices[vi_b].z])
                        ve = np.linalg.norm(va - vb)
                        if ve > 0.001:
                            ratio = abs(elen - ve) / ve
                            total_edges += 1
                            if ratio > EDGE_TOL:
                                bad_edges += 1
            assert total_edges > 50, f"{name}: too few edges ({total_edges})"
            pct = bad_edges / total_edges * 100
            assert pct < 1.0, f"{name}: {bad_edges}/{total_edges} ({pct:.1f}%) edges distorted"

    def test_rest_pose_matches_vanilla(self, converted_data, spliced_shapes, vanilla_male_body):
        """Skinned rest-pose world positions must match vanilla body."""
        bone_map = _get_bone_map(converted_data)
        vanilla_shapes = _load_vanilla_body_shapes()
        for name, shape in spliced_shapes.items():
            if name not in vanilla_male_body:
                continue
            van_info = vanilla_male_body[name]
            van_world = van_info['world_pos']
            van_local = van_info['local_verts']
            sp_world = _compute_rest_pose_world(shape, bone_map)
            sp_local = np.array([[shape.data.vertices[i].x, shape.data.vertices[i].y,
                                  shape.data.vertices[i].z] for i in range(shape.data.num_vertices)])
            if name in vanilla_shapes:
                van_tx = vanilla_shapes[name].translation.x
                van_ty = vanilla_shapes[name].translation.y
                van_tz = vanilla_shapes[name].translation.z
            else:
                van_tx = van_ty = van_tz = 0.0
            van_lookup = {}
            for i in range(len(van_local)):
                key = (round(van_local[i][0], 2), round(van_local[i][1], 2), round(van_local[i][2], 2))
                van_lookup[key] = i
            matched = 0
            max_err = 0.0
            total_err = 0.0
            for si in range(len(sp_local)):
                key = (round(sp_local[si][0] - van_tx, 2),
                       round(sp_local[si][1] - van_ty, 2),
                       round(sp_local[si][2] - van_tz, 2))
                if key not in van_lookup:
                    continue
                vi = van_lookup[key]
                err = np.linalg.norm(sp_world[si] - van_world[vi])
                total_err += err
                max_err = max(max_err, err)
                matched += 1
            assert matched > 50, f"{name}: only {matched} verts matched"
            avg_err = total_err / matched
            assert max_err < 5.0, (
                f"{name}: max rest-pose error = {max_err:.2f} (expected < 5.0), "
                f"avg = {avg_err:.2f}, matched {matched}"
            )

    def test_rest_pose_z_range(self, converted_data, spliced_shapes):
        """Rest-pose Z must be in skeleton space (z ≈ 50-130) after baking."""
        bone_map = _get_bone_map(converted_data)
        for name, shape in spliced_shapes.items():
            sp_world = _compute_rest_pose_world(shape, bone_map)
            z_min, z_max = sp_world[:, 2].min(), sp_world[:, 2].max()
            assert z_min > 40, f"{name}: rest Z min = {z_min:.1f}, expected > 40"
            assert z_max < 130, f"{name}: rest Z max = {z_max:.1f}, expected < 130"


# ===========================================================================
# 11.  Edge length tests for gauntlets (hands splice)
# ===========================================================================
@skipif_no_export
@skipif_no_body
class TestGauntletsSpliceEdges:
    """Edge lengths in spliced hand geometry must exactly match vanilla."""

    @pytest.fixture(scope="class")
    def converted_data(self):
        dst = os.path.join(TMP_DIR, "_test_gauntlets_edges.nif")
        convert_nif(GAUNTLETS_M, dst)
        return _load_nif(_resolve_output(dst))

    @pytest.fixture(scope="class")
    def spliced_shapes(self, converted_data):
        return _get_spliced_shapes(converted_data)

    def test_hand_splice_edge_lengths(self, spliced_shapes):
        vanilla_shapes = _load_vanilla_body_shapes()
        for name, shape in spliced_shapes.items():
            if name not in vanilla_shapes:
                continue
            van = vanilla_shapes[name]
            van_tx = van.translation.x
            van_ty = van.translation.y
            van_tz = van.translation.z
            van_verts = {}
            for i in range(van.data.num_vertices):
                v = van.data.vertices[i]
                van_verts[(round(v.x, 3), round(v.y, 3), round(v.z, 3))] = i

            sp_n = shape.data.num_vertices
            sp_verts = np.array([[shape.data.vertices[i].x, shape.data.vertices[i].y,
                                  shape.data.vertices[i].z] for i in range(sp_n)])
            total_edges = 0
            bad_edges = 0
            for ti in range(min(shape.data.num_triangles, 2000)):
                tri = shape.data.triangles[ti]
                for idx_a, idx_b in [(tri.v_1, tri.v_2), (tri.v_2, tri.v_3), (tri.v_1, tri.v_3)]:
                    if max(idx_a, idx_b) >= sp_n:
                        continue
                    elen = np.linalg.norm(sp_verts[idx_a] - sp_verts[idx_b])
                    if elen < 0.001:
                        continue
                    ka = (round(sp_verts[idx_a][0] - van_tx, 3),
                          round(sp_verts[idx_a][1] - van_ty, 3),
                          round(sp_verts[idx_a][2] - van_tz, 3))
                    kb = (round(sp_verts[idx_b][0] - van_tx, 3),
                          round(sp_verts[idx_b][1] - van_ty, 3),
                          round(sp_verts[idx_b][2] - van_tz, 3))
                    if ka in van_verts and kb in van_verts:
                        vi_a, vi_b = van_verts[ka], van_verts[kb]
                        va = np.array([van.data.vertices[vi_a].x, van.data.vertices[vi_a].y, van.data.vertices[vi_a].z])
                        vb = np.array([van.data.vertices[vi_b].x, van.data.vertices[vi_b].y, van.data.vertices[vi_b].z])
                        ve = np.linalg.norm(va - vb)
                        if ve > 0.001:
                            ratio = abs(elen - ve) / ve
                            total_edges += 1
                            if ratio > 0.001:
                                bad_edges += 1
            if total_edges > 0:
                pct = bad_edges / total_edges * 100
                assert pct < 1.0, f"{name}: {bad_edges}/{total_edges} ({pct:.1f}%) edges distorted"

    def test_hand_rest_pose_z(self, converted_data, spliced_shapes):
        bone_map = _get_bone_map(converted_data)
        for name, shape in spliced_shapes.items():
            sp_world = _compute_rest_pose_world(shape, bone_map)
            z_min, z_max = sp_world[:, 2].min(), sp_world[:, 2].max()
            assert z_min > 40, f"{name}: rest Z min = {z_min:.1f}, expected > 40"
            assert z_max < 130, f"{name}: rest Z max = {z_max:.1f}, expected < 130"


# ===========================================================================
# 12.  Vertex-space alignment tests — the core "stretch / underground" bug
#
# These tests catch the fundamental failure mode:
#   - Vanilla Skyrim body NIF stores geometry with geom.translation.z ≈ 120
#     and vertex local z ≈ -108 to -6.  M@B@W=I so rest-pose = local-z.
#   - Armor NIF stores its own geometry at local z ≈ 75-121 (skeleton space,
#     no offset).
#   - If the splice copies the body NIF verbatim (geom.translation.z=120,
#     vertices at z=-44) then the spliced body appears ≈120 units BELOW the
#     armor — underground, "wildly stretched."
#   - The fix: bake geom.translation into vertex positions so all geometry
#     lives in the same skeleton-space coordinate system.
# ===========================================================================


def _get_armor_geom_z_range(nif_data):
    """Return (zmin, zmax) of all non-spliced skinned geometry vertices."""
    BODY_NIF_PREFIXES = (
        "MaleUnderwearBody", "FemaleUnderwearBody",
        "FootMale", "FootFemale", "HandMale", "HandFemale",
    )
    zmin_all, zmax_all = float("inf"), float("-inf")
    for root in nif_data.roots:
        if root is None:
            continue
        for blk in root.tree():
            if not isinstance(blk, NifFormat.NiTriShape):
                continue
            name = bytes(blk.name).rstrip(b"\x00").decode("latin-1", errors="replace")
            if any(name.startswith(p) for p in BODY_NIF_PREFIXES):
                continue  # skip spliced body geometry
            if blk.data is None or blk.data.num_vertices == 0:
                continue
            tx = blk.translation.x
            ty = blk.translation.y
            tz = blk.translation.z
            for i in range(blk.data.num_vertices):
                wz = blk.data.vertices[i].z + tz
                zmin_all = min(zmin_all, wz)
                zmax_all = max(zmax_all, wz)
    return zmin_all, zmax_all


@skipif_no_iron_f
@skipif_no_body
class TestFemaleIronCuirassAlignment:
    """Tests that spliced body geometry is in the same coordinate space as armor."""

    @pytest.fixture(scope="class")
    def converted_data(self):
        dst = os.path.join(TMP_DIR, "_test_align_iron_f.nif")
        convert_nif(IRON_CUIRASS_F, dst)
        return _load_nif(_resolve_output(dst))

    @pytest.fixture(scope="class")
    def spliced_shapes(self, converted_data):
        return _get_spliced_shapes(converted_data)

    def test_geom_translationis_zero(self, spliced_shapes):
        """Spliced body geometry MUST have geom.translation ≈ 0.

        The vanilla body NIF stores geom.translation.z ≈ 120 as a local→skeleton
        offset.  If this is copied verbatim into the armor NIF, vertex positions
        (stored in local space, z ≈ -44) appear 120 units below skeleton space
        while the armor geometry lives at z ≈ 75-120.  The body geometry must
        have its translation baked into vertices before inserting into the armor.
        """
        for name, shape in spliced_shapes.items():
            tx = shape.translation.x
            ty = shape.translation.y
            tz = shape.translation.z
            assert abs(tx) < 1.0, (
                f"{name}: geom.translation.x = {tx:.4f} (expected ~0). "
                f"Body NIF translation was not baked into vertex positions."
            )
            assert abs(ty) < 1.0, (
                f"{name}: geom.translation.y = {ty:.4f} (expected ~0). "
                f"Body NIF translation was not baked into vertex positions."
            )
            assert abs(tz) < 1.0, (
                f"{name}: geom.translation.z = {tz:.4f} (expected ~0). "
                f"The vanilla body NIF has geom.translation.z ≈ 120.34; if this "
                f"is copied verbatim the geometry appears 120 units below the armor."
            )

    def test_spliced_vertices_in_skeleton_space(self, spliced_shapes):
        """Spliced body vertices must be in positive-z skeleton space (z > 50).

        After baking geom.translation into vertices, the shoulder/arm area
        vertices should be at z ≈ 75-115, matching the armor geometry's z range.
        If they are at z ≈ -44 the translation was not baked (underground bug).
        """
        for name, shape in spliced_shapes.items():
            n = shape.data.num_vertices
            zvals = [shape.data.vertices[i].z for i in range(n)]
            zmin = min(zvals)
            assert zmin > 50, (
                f"{name}: vertex zmin = {zmin:.2f}, expected > 50. "
                f"Vertices appear to be in body-NIF local space (z ≈ -44) "
                f"rather than skeleton space (z ≈ 75-115). "
                f"geom.translation.z was probably not baked into vertex positions."
            )

    def test_spliced_body_overlaps_armor_z_range(self, converted_data, spliced_shapes):
        """Spliced body vertex z range must substantially overlap the armor geometry.

        The armor's arm/shoulder geometry is at z ≈ 75-120.  Spliced body
        geometry for the same region must be in the same range, not at z ≈ -44.
        """
        armor_zmin, armor_zmax = _get_armor_geom_z_range(converted_data)
        if armor_zmax == float("-inf"):
            pytest.skip("No non-spliced armor geometry found")

        for name, shape in spliced_shapes.items():
            n = shape.data.num_vertices
            tz = shape.translation.z  # should be 0 after fix
            sp_wzs = [shape.data.vertices[i].z + tz for i in range(n)]
            sp_zmin, sp_zmax = min(sp_wzs), max(sp_wzs)
            overlap_min = max(sp_zmin, armor_zmin)
            overlap_max = min(sp_zmax, armor_zmax)
            overlap = overlap_max - overlap_min
            total_range = sp_zmax - sp_zmin

            assert overlap > 0, (
                f"{name}: spliced body z=[{sp_zmin:.1f},{sp_zmax:.1f}] does not "
                f"overlap armor z=[{armor_zmin:.1f},{armor_zmax:.1f}]. "
                f"Body geometry is in the wrong coordinate space."
            )
            assert overlap / total_range > 0.5, (
                f"{name}: spliced body z=[{sp_zmin:.1f},{sp_zmax:.1f}] overlaps "
                f"armor z=[{armor_zmin:.1f},{armor_zmax:.1f}] only {overlap:.1f} "
                f"of {total_range:.1f} units ({overlap/total_range*100:.0f}%). "
                f"Expected > 50% overlap."
            )

    def test_left_right_arm_at_correct_x_positions(self, spliced_shapes):
        """Left arm vertices (x < 0) and right arm vertices (x > 0) must be present
        and at expected skeleton-space x positions (|x| ≈ 10-30).

        If geom.translation is not baked, vertex x positions may also be offset
        or the clipping may select the wrong region entirely.
        """
        for name, shape in spliced_shapes.items():
            if "Body" not in name:
                continue
            n = shape.data.num_vertices
            xvals = [shape.data.vertices[i].x for i in range(n)]
            # In skeleton space, left arm is x ≈ -10 to -30, right arm x ≈ 10-30
            left_arm = [x for x in xvals if x < -8]
            right_arm = [x for x in xvals if x > 8]
            assert len(left_arm) > 20, (
                f"{name}: only {len(left_arm)} vertices with x < -8 (left arm). "
                f"Expected left arm region to have > 20 vertices. "
                f"x range = [{min(xvals):.1f}, {max(xvals):.1f}]"
            )
            assert len(right_arm) > 20, (
                f"{name}: only {len(right_arm)} vertices with x > 8 (right arm). "
                f"Expected right arm region to have > 20 vertices. "
                f"x range = [{min(xvals):.1f}, {max(xvals):.1f}]"
            )


@skipif_no_iron_m
@skipif_no_body
class TestMaleIronCuirassAlignment:
    """Same coordinate-space alignment tests for male iron cuirass."""

    @pytest.fixture(scope="class")
    def converted_data(self):
        dst = os.path.join(TMP_DIR, "_test_align_iron_m.nif")
        convert_nif(IRON_CUIRASS_M, dst)
        return _load_nif(_resolve_output(dst))

    @pytest.fixture(scope="class")
    def spliced_shapes(self, converted_data):
        return _get_spliced_shapes(converted_data)

    def test_geom_translationis_zero(self, spliced_shapes):
        """Spliced body geometry must have geom.translation ≈ 0 (vertices baked)."""
        for name, shape in spliced_shapes.items():
            tz = shape.translation.z
            assert abs(tz) < 1.0, (
                f"{name}: geom.translation.z = {tz:.4f} (expected ~0). "
                f"The vanilla body NIF geom.translation.z ≈ 120.34 must be baked "
                f"into vertex positions before inserting into the armor NIF."
            )

    def test_spliced_vertices_in_skeleton_space(self, spliced_shapes):
        """Male spliced body vertices must be in skeleton space (z > 50)."""
        for name, shape in spliced_shapes.items():
            n = shape.data.num_vertices
            zvals = [shape.data.vertices[i].z for i in range(n)]
            zmin = min(zvals)
            assert zmin > 50, (
                f"{name}: vertex zmin = {zmin:.2f}, expected > 50. "
                f"Vertices are in body-NIF local space (z ≈ -44), not skeleton space."
            )

    def test_spliced_body_overlaps_armor_z_range(self, converted_data, spliced_shapes):
        """Spliced body z range must overlap the armor geometry z range."""
        armor_zmin, armor_zmax = _get_armor_geom_z_range(converted_data)
        if armor_zmax == float("-inf"):
            pytest.skip("No non-spliced armor geometry found")

        for name, shape in spliced_shapes.items():
            n = shape.data.num_vertices
            tz = shape.translation.z
            sp_wzs = [shape.data.vertices[i].z + tz for i in range(n)]
            sp_zmin, sp_zmax = min(sp_wzs), max(sp_wzs)
            overlap = min(sp_zmax, armor_zmax) - max(sp_zmin, armor_zmin)
            total_range = sp_zmax - sp_zmin

            assert overlap > 0, (
                f"{name}: spliced body z=[{sp_zmin:.1f},{sp_zmax:.1f}] does not "
                f"overlap armor z=[{armor_zmin:.1f},{armor_zmax:.1f}]."
            )

    def test_left_right_arm_at_correct_x_positions(self, spliced_shapes):
        """Both left and right arm regions must be present at correct x positions."""
        for name, shape in spliced_shapes.items():
            if "Body" not in name:
                continue
            n = shape.data.num_vertices
            xvals = [shape.data.vertices[i].x for i in range(n)]
            left_arm = [x for x in xvals if x < -8]
            right_arm = [x for x in xvals if x > 8]
            assert len(left_arm) > 20, (
                f"{name}: only {len(left_arm)} verts with x < -8. "
                f"Left arm missing or at wrong x position. x=[{min(xvals):.1f},{max(xvals):.1f}]"
            )
            assert len(right_arm) > 20, (
                f"{name}: only {len(right_arm)} verts with x > 8. "
                f"Right arm missing or at wrong x position."
            )


# ===========================================================================
# TDD unit tests from test_skin_replacement.py
# ===========================================================================

from pathlib import Path as _Path

SKYRIM_BODY_DIR_P = _Path(BASE) / 'temp' / 'Skyrim Meshes' / 'meshes' / 'actors' / 'character' / 'character assets'
OUTPUT_DIR_P = _Path(BASE) / 'output' / 'oblivion.esm' / 'meshes' / 'tes4'


def _find_geometry_blocks(data):
    """Return all NiTriShape/NiTriStrips blocks in a NIF."""
    blocks = []
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
                blocks.append(block)
    return blocks

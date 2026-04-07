"""Read Oblivion NIF binary files into a structured representation.

Handles NIF versions 20.0.0.4/20.0.0.5 (Oblivion) and older versions
(0x0A020000, 0x0A01006A, 0x0A000100, 0x0A000102, 0x0A010065,
 0x04000002, 0x04020100, 0x0303000D) found in Oblivion game files.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Supported NIF versions
# ---------------------------------------------------------------------------
OBV_VERSION = 0x14000004   # 20.0.0.4
OBV_VERSION_2 = 0x14000005  # 20.0.0.5 (some Oblivion files)
SKY_VERSION = 0x14020007   # 20.2.0.7

# Older NIF versions found in Oblivion game data
_OLDER_VERSIONS = frozenset({
    0x0A020000,  # 10.2.0.0
    0x0A01006A,  # 10.1.0.106
    0x0A000100,  # 10.0.1.0
    0x0A000102,  # 10.0.1.2
    0x0A010065,  # 10.1.0.101
    0x04000002,  # 4.0.0.2
    0x04020100,  # 4.2.1.0
    0x0303000D,  # 3.3.0.13
})


# ---------------------------------------------------------------------------
# Binary reader helper
# ---------------------------------------------------------------------------

class NifReader:
    """Wraps a bytes object with sequential read helpers."""

    __slots__ = ('_data', '_pos', '_track_refs', '_tracked_ref_offsets')

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self._track_refs = False
        self._tracked_ref_offsets: list[int] = []

    @property
    def pos(self) -> int:
        return self._pos

    @pos.setter
    def pos(self, v: int) -> None:
        self._pos = v

    def remaining(self) -> int:
        return len(self._data) - self._pos

    def read(self, n: int) -> bytes:
        b = self._data[self._pos:self._pos + n]
        self._pos += n
        return b

    def u8(self) -> int:
        v = self._data[self._pos]
        self._pos += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from('<H', self._data, self._pos)[0]
        self._pos += 2
        return v

    def i16(self) -> int:
        v = struct.unpack_from('<h', self._data, self._pos)[0]
        self._pos += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from('<I', self._data, self._pos)[0]
        self._pos += 4
        return v

    def i32(self) -> int:
        v = struct.unpack_from('<i', self._data, self._pos)[0]
        self._pos += 4
        return v

    def f32(self) -> float:
        v = struct.unpack_from('<f', self._data, self._pos)[0]
        self._pos += 4
        return v

    def bool8(self) -> bool:
        return bool(self.u8())

    def vec3(self) -> tuple[float, float, float]:
        x, y, z = struct.unpack_from('<3f', self._data, self._pos)
        self._pos += 12
        return (x, y, z)

    def mat33(self) -> tuple:
        vals = struct.unpack_from('<9f', self._data, self._pos)
        self._pos += 36
        return vals

    def rgba(self) -> tuple[float, float, float, float]:
        r, g, b, a = struct.unpack_from('<4f', self._data, self._pos)
        self._pos += 16
        return (r, g, b, a)

    def rgb(self) -> tuple[float, float, float]:
        r, g, b = struct.unpack_from('<3f', self._data, self._pos)
        self._pos += 12
        return (r, g, b)

    def string(self) -> str:
        """SizedString: 4-byte length + chars."""
        n = self.u32()
        raw = self.read(n)
        return raw.decode('utf-8', errors='replace')

    def short_string(self) -> str:
        """ShortString: 1-byte length (incl. null) + chars."""
        n = self.u8()
        raw = self.read(n)
        return raw.rstrip(b'\x00').decode('utf-8', errors='replace')

    def ref(self) -> int:
        """Ref / Ptr: signed 32-bit block index (-1 = null)."""
        if self._track_refs:
            self._tracked_ref_offsets.append(self._pos)
        return self.i32()


# ---------------------------------------------------------------------------
# NIF block data model
# ---------------------------------------------------------------------------

@dataclass
class NifBlock:
    """Parsed representation of one NIF block."""
    type_name: str

    # NiObjectNET fields
    name: str = ''
    extra_data: list = field(default_factory=list)
    controller: int = -1

    # NiAVObject fields
    flags: int = 0
    translation: tuple = (0.0, 0.0, 0.0)
    rotation: tuple = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    scale: float = 1.0
    properties: list = field(default_factory=list)
    collision_object: int = -1

    # NiNode children / effects
    children: list = field(default_factory=list)
    effects: list = field(default_factory=list)

    # NiGeometry
    data_ref: int = -1
    skin_instance: int = -1
    bs_shader_ref: int = -1
    bs_alpha_ref: int = -1

    # NiGeometryData
    num_vertices: int = 0
    has_vertices: bool = False
    vertices: list = field(default_factory=list)
    num_uv_sets: int = 0
    has_normals: bool = False
    normals: list = field(default_factory=list)
    tangents: list = field(default_factory=list)
    bitangents: list = field(default_factory=list)
    center: tuple = (0.0, 0.0, 0.0)
    radius: float = 0.0
    has_vertex_colors: bool = False
    vertex_colors: list = field(default_factory=list)
    uv_sets: list = field(default_factory=list)
    consistency_flags: int = 0
    additional_data: int = -1

    # NiTriShapeData
    triangles: list = field(default_factory=list)

    # NiTriStripsData
    strip_points: list = field(default_factory=list)

    # NiMaterialProperty
    mat_ambient: tuple = (1.0, 1.0, 1.0)
    mat_diffuse: tuple = (1.0, 1.0, 1.0)
    mat_specular: tuple = (0.0, 0.0, 0.0)
    mat_emissive: tuple = (0.0, 0.0, 0.0)
    mat_glossiness: float = 80.0
    mat_alpha: float = 1.0

    # NiTexturingProperty
    tex_flags: int = 0
    tex_apply_mode: int = 2
    diffuse_path: Any = None
    normal_path: Any = None
    glow_path: Any = None

    # NiAlphaProperty
    alpha_flags: int = 0
    alpha_threshold: int = 0

    # NiStencilProperty
    has_stencil: bool = False

    # BSLightingShaderProperty / BSShaderTextureSet
    shader_type: int = 0
    shader_flags1: int = 0x82400303
    shader_flags2: int = 0x00008021
    uv_offset: tuple = (0.0, 0.0)
    uv_scale: tuple = (1.0, 1.0)
    texture_set_ref: int = -1
    emissive_color: tuple = (0.0, 0.0, 0.0)
    emissive_multiple: float = 1.0
    texture_clamp_mode: int = 3
    shader_alpha: float = 1.0
    refraction_strength: float = 0.0
    glossiness: float = 80.0
    specular_color: tuple = (1.0, 1.0, 1.0)
    specular_strength: float = 1.0
    lighting_effect1: float = 0.3
    lighting_effect2: float = 2.0

    # BSShaderTextureSet
    textures: list = field(default_factory=lambda: [''] * 9)

    # NiSkinInstance / BSDismemberSkinInstance
    skin_data: int = -1
    skin_partition: int = -1
    skeleton_root: int = -1
    bone_refs: list = field(default_factory=list)
    n_skin_partitions: int = 0
    dismember_partitions: list = field(default_factory=list)

    # NiStringExtraData / NiBinaryExtraData
    extra_string: str = ''
    extra_bytes: bytes = b''

    # BSXFlags / NiIntegerExtraData
    extra_integer: int = 0

    # NiSourceTexture
    use_external: int = 1
    file_name: str = ''

    # NiBillboardNode
    billboard_mode: int = 0

    # BSFurnitureMarker — (offset_vec3_bytes, heading_u16, posref1_u8, posref2_u8)
    furniture_positions: list = field(default_factory=list)

    # NiTextKeyExtraData — (time_f32, string)
    text_keys: list = field(default_factory=list)

    # BSBound — raw 24 bytes (center + dimensions)
    bound_data: bytes = b''

    # Offset within raw_bytes where unparsed body data starts (after any
    # parsed NiObjectNET/NiAVObject/NiPSysModifier header).  Used by the
    # writer to reconstruct blocks with Skyrim string format.
    raw_body_offset: int = 0

    # Raw bytes for blocks we don't fully parse
    raw_bytes: bytes = b''

    # Tracked (offset_in_raw, ref_value) pairs for block refs in raw_bytes.
    # Used by orphan removal for precise remapping instead of blind byte scan.
    raw_refs: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsed NIF file
# ---------------------------------------------------------------------------

@dataclass
class OblNif:
    """Parsed Oblivion NIF file."""
    version: int
    user_version: int
    user_version_2: int
    creator: str
    process_script: str
    export_script: str
    block_types: list
    block_type_indices: list
    blocks: list
    root_indices: list


# ---------------------------------------------------------------------------
# Block types we understand
# ---------------------------------------------------------------------------

FULLY_PARSED = frozenset({
    'NiNode', 'NiTriShape', 'NiTriStrips', 'BSFadeNode',
    'NiTriShapeData', 'NiTriStripsData',
    'NiMaterialProperty', 'NiTexturingProperty', 'NiAlphaProperty',
    'NiStencilProperty', 'NiSpecularProperty',
    'NiSourceTexture',
    'NiSkinInstance',
    'NiStringExtraData', 'NiBinaryExtraData',
})

PARSE_AS_RAW = frozenset({
    'BSXFlags', 'NiIntegerExtraData',
    'NiVertexColorProperty',
    'bhkCollisionObject', 'bhkPCollisionObject', 'bhkSPCollisionObject', 'bhkBlendCollisionObject',
    'bhkRigidBody', 'bhkRigidBodyT',
    'bhkMoppBvTreeShape',
    'bhkNiTriStripsShape',
    'bhkConvexVerticesShape',
    'bhkBoxShape', 'bhkCapsuleShape', 'bhkSphereShape',
    'bhkListShape',
    'bhkConvexTransformShape', 'bhkTransformShape',
    'bhkPackedNiTriStripsShape',
    'hkPackedNiTriStripsData',
    'NiSkinData', 'NiSkinPartition',
    'NiTextKeyExtraData',
    'BSFurnitureMarker',
    'NiControllerManager', 'NiUVController',
    'NiTransformController', 'NiKeyframeController',
    'NiMaterialColorController', 'NiTextureTransformController',
    'NiMultiTargetTransformController', 'NiFlipController',
    'NiFloatInterpolator', 'NiTransformInterpolator', 'NiPoint3Interpolator',
    'NiControllerSequence',
    'NiBoneLODController', 'NiBSBoneLODController',
    'NiUVData',
    'NiParticleSystem', 'NiMeshParticleSystem',
    'bhkRagdollConstraint', 'bhkLimitedHingeConstraint', 'bhkPrismaticConstraint',
    'bhkHingeConstraint', 'bhkStiffSpringConstraint', 'bhkMalleableConstraint',
    'bhkMultiSphereShape',
    'bhkSimpleShapePhantom',
    'NiBillboardNode', 'BSBound', 'NiZBufferProperty', 'NiFogProperty',
    'NiTransformData', 'NiPosData', 'NiFloatData', 'NiBoolData', 'NiStringPalette',
    'NiBlendTransformInterpolator', 'NiBoolInterpolator', 'NiBoolTimelineInterpolator',
    'NiPathInterpolator',
    'NiBlendBoolInterpolator', 'NiBlendFloatInterpolator', 'NiBlendPoint3Interpolator',
    'bhkBlendController', 'NiGeomMorpherController',
    'NiPSysEmitterCtlr', 'NiPSysModifierActiveCtlr',
    'NiAlphaController', 'NiVisController',
    'NiPSysUpdateCtlr',
    'NiPSysEmitterInitialRadiusCtlr', 'NiPSysEmitterSpeedCtlr',
    'NiPSysEmitterLifeSpanCtlr', 'NiPSysGravityStrengthCtlr',
    'NiPSysEmitterDeclinationCtlr', 'NiPSysEmitterDeclinationVarCtlr',
    'NiDirectionalLight', 'NiAmbientLight',
    'NiDefaultAVObjectPalette',
    'NiMorphData',
    'NiPSysData', 'NiMeshPSysData',
    'NiPSysResetOnLoopCtlr',
    'NiDitherProperty',
    'NiCamera',
    'NiPSysAgeDeathModifier', 'NiPSysSpawnModifier', 'NiPSysGravityModifier',
    'NiPSysGrowFadeModifier', 'NiPSysColorModifier', 'NiPSysPositionModifier',
    'NiPSysRotationModifier', 'NiPSysBoundUpdateModifier', 'NiPSysBombModifier',
    'NiPSysDragModifier', 'NiPSysColliderManager', 'NiPSysMeshUpdateModifier',
    'BSParentVelocityModifier', 'BSWindModifier',
    'BSPSysInheritVelocityModifier', 'BSPSysHavokUpdateModifier',
    'BSPSysRecycleBoundModifier', 'BSPSysSubTexModifier',
    'BSPSysLODModifier', 'BSPSysScaleModifier', 'BSPSysSimpleColorModifier',
    'BSPSysStripUpdateModifier',
    'NiPSysVortexFieldModifier', 'NiPSysGravityFieldModifier',
    'NiPSysDragFieldModifier', 'NiPSysTurbulenceFieldModifier',
    'NiPSysAirFieldModifier', 'NiPSysRadialFieldModifier',
    'NiPSysBoxEmitter', 'NiPSysCylinderEmitter', 'NiPSysSphereEmitter',
    'BSPSysArrayEmitter', 'NiPSysMeshEmitter',
    'NiPSysPlanarCollider', 'NiPSysSphericalCollider',
    'NiColorData', 'NiPSysEmitterCtlrData',
})


# ---------------------------------------------------------------------------
# Shared parsing helpers
# ---------------------------------------------------------------------------

def _skip_key_group_float(r: NifReader) -> None:
    n = r.u32()
    if n == 0:
        return
    interp = r.u32()
    if interp == 1:
        r.read(n * 8)
    elif interp == 2:
        r.read(n * 16)
    elif interp == 3:
        r.read(n * 20)
    else:
        r.read(n * 8)


def _skip_key_group_vec3(r: NifReader) -> None:
    n = r.u32()
    if n == 0:
        return
    interp = r.u32()
    if interp == 1:
        r.read(n * 16)
    elif interp == 2:
        r.read(n * 40)
    elif interp == 3:
        r.read(n * 28)
    else:
        r.read(n * 16)


def _skip_key_group_byte(r: NifReader) -> None:
    n = r.u32()
    if n == 0:
        return
    interp = r.u32()
    if interp == 1:
        r.read(n * 5)
    elif interp == 2:
        r.read(n * 7)
    elif interp == 3:
        r.read(n * 17)
    else:
        r.read(n * 5)


def _skip_key_group_color4(r: NifReader) -> None:
    n = r.u32()
    if n == 0:
        return
    interp = r.u32()
    if interp == 1:
        r.read(n * 20)
    elif interp == 2:
        r.read(n * 52)
    elif interp == 3:
        r.read(n * 32)
    else:
        r.read(n * 20)


def _skip_quat_rotation_keys(r: NifReader) -> None:
    n = r.u32()
    if n == 0:
        return
    rot_type = r.u32()
    if rot_type == 4:
        for _ in range(3):
            _skip_key_group_float(r)
    else:
        if rot_type == 3:
            r.read(n * 32)
        else:
            r.read(n * 20)


def _parse_obv_geometry_data(r: NifReader) -> int:
    """Parse NiGeometryData fields for Oblivion NIFs. Returns num_vertices."""
    r.u32()  # Unknown Int
    n_verts = r.u16()
    r.u8()   # Keep Flags
    r.u8()   # Compress Flags
    has_verts = r.bool8()
    if has_verts:
        r.read(n_verts * 12)
    num_uv_sets = r.u16()
    has_normals = r.bool8()
    if has_normals:
        r.read(n_verts * 12)
        if num_uv_sets & 0xF000:
            r.read(n_verts * 12)
            r.read(n_verts * 12)
    r.read(12)  # Center
    r.f32()     # Radius
    has_colors = r.bool8()
    if has_colors:
        r.read(n_verts * 16)
    n_uv = num_uv_sets & 63
    if n_uv:
        r.read(n_uv * n_verts * 8)
    r.u16()  # Consistency Flags
    r.ref()  # Additional Data
    return n_verts


def _parse_obv_psys_modifier(r: NifReader, blk: NifBlock | None = None, block_start: int = 0) -> None:
    name = r.string()
    if blk is not None:
        blk.name = name
    r.u32()
    r.ref()
    r.bool8()
    if blk is not None:
        blk.raw_body_offset = r.pos - block_start


def _parse_obv_psys_emitter(r: NifReader, blk: NifBlock | None = None, block_start: int = 0) -> None:
    _parse_obv_psys_modifier(r, blk, block_start)
    r.f32(); r.f32(); r.f32(); r.f32(); r.f32(); r.f32()
    r.read(16); r.f32(); r.f32(); r.f32(); r.f32()
    if blk is not None:
        blk.raw_body_offset = r.pos - block_start


def _parse_obv_psys_collider(r: NifReader) -> None:
    r.f32(); r.bool8(); r.bool8(); r.ref(); r.ref(); r.ref(); r.ref()


def _parse_obv_object_net(r: NifReader, blk: NifBlock) -> None:
    blk.name = r.string()
    n_extra = r.u32()
    blk.extra_data = [r.ref() for _ in range(n_extra)]
    blk.controller = r.ref()


def _parse_obv_av_object(r: NifReader, blk: NifBlock) -> None:
    blk.flags = r.u16()
    blk.translation = r.vec3()
    blk.rotation = r.mat33()
    blk.scale = r.f32()
    n_props = r.u32()
    blk.properties = [r.ref() for _ in range(n_props)]
    blk.collision_object = r.ref()


def _parse_obv_geometry_data_common(r: NifReader, blk: NifBlock) -> None:
    r.u32()
    blk.num_vertices = r.u16()
    r.u8(); r.u8()
    blk.has_vertices = r.bool8()
    if blk.has_vertices:
        blk.vertices = [r.vec3() for _ in range(blk.num_vertices)]
    blk.num_uv_sets = r.u16()
    blk.has_normals = r.bool8()
    if blk.has_normals:
        blk.normals = [r.vec3() for _ in range(blk.num_vertices)]
        if blk.num_uv_sets & 0x1000:
            blk.tangents = [r.vec3() for _ in range(blk.num_vertices)]
            blk.bitangents = [r.vec3() for _ in range(blk.num_vertices)]
    blk.center = r.vec3()
    blk.radius = r.f32()
    blk.has_vertex_colors = r.bool8()
    if blk.has_vertex_colors:
        blk.vertex_colors = [r.rgba() for _ in range(blk.num_vertices)]
    num_uv = blk.num_uv_sets & 0x3F
    blk.uv_sets = []
    for _ in range(num_uv):
        uvs = [(r.f32(), r.f32()) for _ in range(blk.num_vertices)]
        blk.uv_sets.append(uvs)
    blk.consistency_flags = r.u16()
    blk.additional_data = r.ref()


def _strips_to_triangles(strip_points: list) -> list:
    tris = []
    for strip in strip_points:
        for i in range(len(strip) - 2):
            v1, v2, v3 = strip[i], strip[i + 1], strip[i + 2]
            if v1 == v2 or v2 == v3 or v1 == v3:
                continue
            if i % 2 == 0:
                tris.append((v1, v2, v3))
            else:
                tris.append((v1, v3, v2))
    return tris


# ---------------------------------------------------------------------------
# Block parser â€” Oblivion version 20.0.0.4 / 20.0.0.5
# ---------------------------------------------------------------------------

def _parse_block_oblivion(r: NifReader, type_name: str, block_start: int = 0) -> NifBlock:
    """Parse one Oblivion NIF block. Returns a NifBlock."""
    blk = NifBlock(type_name=type_name)

    if type_name not in FULLY_PARSED and type_name not in PARSE_AS_RAW:
        raise ValueError(f'Unknown NIF block type: {type_name}')

    try:
        if type_name in ('NiNode', 'BSFadeNode'):
            _parse_obv_object_net(r, blk)
            _parse_obv_av_object(r, blk)
            n_children = r.u32()
            blk.children = [r.ref() for _ in range(n_children)]
            n_effects = r.u32()
            blk.effects = [r.ref() for _ in range(n_effects)]

        elif type_name in ('NiTriShape', 'NiTriStrips'):
            _parse_obv_object_net(r, blk)
            _parse_obv_av_object(r, blk)
            blk.data_ref = r.ref()
            blk.skin_instance = r.ref()
            has_shader = r.u8()
            if has_shader:
                r.string()
                r.i32()

        elif type_name == 'NiTriShapeData':
            _parse_obv_geometry_data_common(r, blk)
            num_tris = r.u16()
            r.u32()
            has_tris = r.bool8()
            if has_tris:
                blk.triangles = [(r.u16(), r.u16(), r.u16()) for _ in range(num_tris)]
            num_mg = r.u16()
            for _ in range(num_mg):
                n_mg_verts = r.u16()
                for _ in range(n_mg_verts):
                    r.u16()

        elif type_name == 'NiTriStripsData':
            _parse_obv_geometry_data_common(r, blk)
            r.u16()
            num_strips = r.u16()
            strip_lengths = [r.u16() for _ in range(num_strips)]
            has_points = r.bool8()
            if has_points:
                blk.strip_points = [
                    [r.u16() for _ in range(strip_lengths[s])]
                    for s in range(num_strips)
                ]
            blk.triangles = _strips_to_triangles(blk.strip_points)

        elif type_name == 'NiMaterialProperty':
            _parse_obv_object_net(r, blk)
            blk.mat_ambient = r.rgb()
            blk.mat_diffuse = r.rgb()
            blk.mat_specular = r.rgb()
            blk.mat_emissive = r.rgb()
            blk.mat_glossiness = r.f32()
            blk.mat_alpha = r.f32()

        elif type_name == 'NiTexturingProperty':
            _parse_obv_object_net(r, blk)
            blk.tex_apply_mode = r.u32()
            tex_count = r.u32()

            def _read_tex_desc() -> int:
                src = r.ref()
                r.u32(); r.u32(); r.u32()
                has_transform = r.bool8()
                if has_transform:
                    r.f32(); r.f32(); r.f32(); r.f32()
                    r.f32(); r.u32(); r.f32(); r.f32()
                return src

            if r.bool8():
                blk.diffuse_path = _read_tex_desc()
            if r.bool8():
                _read_tex_desc()
            if r.bool8():
                _read_tex_desc()
            if r.bool8():
                _read_tex_desc()
            if r.bool8():
                blk.glow_path = _read_tex_desc()
            if r.bool8():
                _read_tex_desc()
                r.f32(); r.f32()
                r.f32(); r.f32(); r.f32(); r.f32()
            if r.bool8():
                _read_tex_desc()
            if tex_count >= 8 and r.bool8():
                _read_tex_desc()
            if tex_count >= 9 and r.bool8():
                _read_tex_desc()
            if tex_count >= 10 and r.bool8():
                _read_tex_desc()
            n_shader = r.u32()
            for _ in range(n_shader):
                is_used = r.bool8()
                if is_used:
                    _read_tex_desc()
                    r.u32()

        elif type_name == 'NiAlphaProperty':
            _parse_obv_object_net(r, blk)
            blk.alpha_flags = r.u16()
            blk.alpha_threshold = r.u8()

        elif type_name == 'NiSpecularProperty':
            _parse_obv_object_net(r, blk)
            blk.flags = r.u16()

        elif type_name == 'NiStencilProperty':
            _parse_obv_object_net(r, blk)
            r.u8(); r.u32(); r.u32(); r.u32(); r.u32(); r.u32(); r.u32(); r.u32()
            blk.has_stencil = True

        elif type_name == 'NiVertexColorProperty':
            _parse_obv_object_net(r, blk)
            blk.flags = r.u16()
            r.u32(); r.u32()

        elif type_name == 'NiSourceTexture':
            _parse_obv_object_net(r, blk)
            blk.use_external = r.u8()
            if blk.use_external:
                blk.file_name = r.string()
                r.ref()
                blk.pixel_layout = r.u32()
                blk.use_mipmaps = r.u32()
                blk.alpha_format = r.u32()
                r.u8(); r.u8()
            else:
                r.string(); r.ref()
                blk.pixel_layout = r.u32()
                blk.use_mipmaps = r.u32()
                blk.alpha_format = r.u32()
                r.u8(); r.u8()

        elif type_name == 'NiSkinInstance':
            blk.skin_data = r.ref()
            blk.skin_partition = r.ref()
            blk.skeleton_root = r.ref()
            n_bones = r.u32()
            blk.bone_refs = [r.ref() for _ in range(n_bones)]

        elif type_name in ('BSXFlags', 'NiIntegerExtraData'):
            blk.name = r.string()
            blk.extra_integer = r.u32()

        elif type_name == 'NiStringExtraData':
            blk.name = r.string()
            blk.extra_string = r.string()

        elif type_name == 'NiBinaryExtraData':
            blk.name = r.string()
            n = r.u32()
            blk.extra_bytes = r.read(n)

        elif type_name in ('bhkCollisionObject', 'bhkPCollisionObject', 'bhkSPCollisionObject'):
            r.ref(); r.u16(); r.ref()

        elif type_name == 'bhkBlendCollisionObject':
            r.ref(); r.u16(); r.ref(); r.f32(); r.f32()

        elif type_name in ('bhkRigidBody', 'bhkRigidBodyT'):
            r.ref(); r.u8(); r.u8(); r.u16()
            r.i32(); r.i32(); r.read(12)
            r.u8(); r.u8(); r.u16(); r.read(4)
            r.u8(); r.u8(); r.read(14)
            r.read(16); r.read(16); r.read(16); r.read(16)
            r.read(48); r.read(16)
            r.f32(); r.f32(); r.f32(); r.f32(); r.f32()
            r.f32(); r.f32(); r.f32()
            r.u8(); r.u8(); r.u8(); r.u8()
            r.u32(); r.u32(); r.u32()
            n_constraints = r.u32()
            for _ in range(n_constraints):
                r.ref()
            r.u32()

        elif type_name == 'bhkMoppBvTreeShape':
            r.ref(); r.u32(); r.read(8); r.f32()
            mopp_size = r.u32()
            r.read(12); r.f32(); r.read(mopp_size)

        elif type_name == 'bhkNiTriStripsShape':
            r.u32(); r.f32(); r.u32(); r.read(16); r.u32()
            r.read(12); r.u32()
            n_strips = r.u32()
            for _ in range(n_strips):
                r.ref()
            n_layers = r.u32()
            r.read(n_layers * 4)

        elif type_name == 'bhkConvexVerticesShape':
            r.u32(); r.f32(); r.read(24)
            n_verts = r.u32()
            r.read(n_verts * 16)
            n_normals = r.u32()
            r.read(n_normals * 16)

        elif type_name == 'bhkBoxShape':
            r.u32(); r.f32(); r.read(8); r.read(12); r.f32()

        elif type_name == 'bhkCapsuleShape':
            r.u32(); r.f32(); r.read(8); r.read(12); r.f32(); r.read(12); r.f32()

        elif type_name == 'bhkSphereShape':
            r.u32(); r.f32()

        elif type_name == 'bhkListShape':
            n_sub = r.u32()
            for _ in range(n_sub):
                r.ref()
            r.u32(); r.read(24)
            n_unk_ints = r.u32()
            r.read(n_unk_ints * 4)

        elif type_name in ('bhkConvexTransformShape', 'bhkTransformShape'):
            r.ref(); r.u32(); r.f32(); r.read(8); r.read(64)

        elif type_name == 'bhkPackedNiTriStripsShape':
            n_sub = r.u16()
            r.read(n_sub * 12)
            r.u32(); r.u32(); r.f32(); r.u32(); r.read(12)
            r.f32(); r.f32(); r.read(12); r.f32(); r.ref()

        elif type_name == 'hkPackedNiTriStripsData':
            n_tris = r.u32()
            r.read(n_tris * 20)
            n_verts = r.u32()
            r.read(n_verts * 12)

        elif type_name == 'NiSkinData':
            r.read(52)
            n_bones = r.u32()
            has_vw = r.u8()
            for _ in range(n_bones):
                r.read(52); r.read(16)
                if has_vw:
                    n_weights = r.u16()
                    r.read(n_weights * 6)

        elif type_name == 'NiSkinPartition':
            n_parts = r.u32()
            blk.n_skin_partitions = n_parts
            for _ in range(n_parts):
                n_verts = r.u16(); n_tris = r.u16(); n_bones = r.u16()
                n_strips = r.u16(); n_weights_per_vert = r.u16()
                r.read(n_bones * 2)
                if r.bool8():
                    r.read(n_verts * 2)
                if r.bool8():
                    r.read(n_verts * n_weights_per_vert * 4)
                if n_strips:
                    strip_lengths = [r.u16() for _ in range(n_strips)]
                else:
                    strip_lengths = []
                has_faces = r.bool8()
                if has_faces:
                    if n_strips:
                        for sl in strip_lengths:
                            r.read(sl * 2)
                    else:
                        r.read(n_tris * 6)
                if r.bool8():
                    r.read(n_verts * n_weights_per_vert)

        elif type_name == 'NiTextKeyExtraData':
            blk.name = r.string()
            n_keys = r.u32()
            blk.text_keys = []
            for _ in range(n_keys):
                t = r.f32()
                s = r.string()
                blk.text_keys.append((t, s))

        elif type_name == 'BSFurnitureMarker':
            blk.name = r.string()
            n_positions = r.u32()
            blk.furniture_positions = []
            for _ in range(n_positions):
                off_bytes = r.read(12)
                heading = r.u16()
                pr1 = r.u8()
                pr2 = r.u8()
                blk.furniture_positions.append((off_bytes, heading, pr1, pr2))

        elif type_name == 'BSBound':
            blk.name = r.string()
            blk.bound_data = r.read(24)

        elif type_name == 'NiBillboardNode':
            _parse_obv_object_net(r, blk)
            _parse_obv_av_object(r, blk)
            n_children = r.u32()
            blk.children = [r.ref() for _ in range(n_children)]
            n_effects = r.u32()
            blk.effects = [r.ref() for _ in range(n_effects)]
            blk.billboard_mode = r.u16()

        elif type_name == 'NiZBufferProperty':
            _parse_obv_object_net(r, blk)
            blk.raw_body_offset = r.pos - block_start
            r.u16(); r.u32()

        elif type_name in ('NiTransformController', 'NiKeyframeController'):
            r.read(26); r.ref()

        elif type_name == 'NiUVController':
            r.read(26); r.u16(); r.ref()

        elif type_name == 'NiMaterialColorController':
            r.read(26); r.ref(); r.u16()

        elif type_name == 'NiTextureTransformController':
            r.read(26); r.ref(); r.u8(); r.u32(); r.u32()

        elif type_name == 'NiControllerManager':
            r.read(26); r.u8()
            n_seqs = r.u32()
            for _ in range(n_seqs):
                r.ref()
            r.ref()

        elif type_name == 'bhkRagdollConstraint':
            n_ent = r.u32()
            for _ in range(n_ent):
                r.ref()
            r.u32(); r.read(120)

        elif type_name == 'bhkLimitedHingeConstraint':
            n_ent = r.u32()
            for _ in range(n_ent):
                r.ref()
            r.u32(); r.read(124)

        elif type_name == 'bhkPrismaticConstraint':
            n_ent = r.u32()
            for _ in range(n_ent):
                r.ref()
            r.u32()
            r.read(16); r.read(64); r.read(16); r.read(16); r.read(16); r.read(12)

        elif type_name == 'NiMultiTargetTransformController':
            r.read(26)
            n_targets = r.u16()
            for _ in range(n_targets):
                r.ref()

        elif type_name == 'NiUVData':
            for _ in range(4):
                _skip_key_group_float(r)

        elif type_name == 'NiFloatInterpolator':
            r.f32(); r.ref()

        elif type_name == 'NiPoint3Interpolator':
            r.read(12); r.ref()

        elif type_name == 'NiTransformInterpolator':
            r.read(12); r.read(16); r.f32(); r.ref()

        elif type_name == 'NiFlipController':
            r.read(26); r.ref(); r.u32()
            n_src = r.u32()
            for _ in range(n_src):
                r.ref()

        elif type_name in ('NiBoneLODController', 'NiBSBoneLODController'):
            r.read(26); r.u32()
            n_lods = r.u32()
            r.u32()
            for _ in range(n_lods):
                n_nodes = r.u32()
                for _ in range(n_nodes):
                    r.ref()

        elif type_name in ('NiParticleSystem', 'NiMeshParticleSystem'):
            _parse_obv_object_net(r, blk)
            _parse_obv_av_object(r, blk)
            blk.raw_body_offset = r.pos - block_start
            r.ref(); r.ref()
            has_shader = r.bool8()
            if has_shader:
                r.string(); r.i32()
            r.bool8()
            n_mod = r.u32()
            for _ in range(n_mod):
                r.ref()

        elif type_name == 'NiControllerSequence':
            r.string()                                      # Name
            n_blocks = r.u32()                              # NumControlledBlocks
            r.u32()                                         # ArrayGrowBy
            for _ in range(n_blocks):                       # ControlledBlocks
                r.ref(); r.ref(); r.u8(); r.ref()           # Interp, Ctrl, Priority, StrPalette
                r.i32(); r.i32(); r.i32(); r.i32(); r.i32() # 5 string offsets
            r.f32(); r.ref(); r.u32()                       # Weight, TextKeys, CycleType
            r.f32(); r.f32(); r.f32()                       # Frequency, StartTime, StopTime
            r.ref(); r.string(); r.ref()                    # Manager, AccumRootName, StringPalette

        elif type_name == 'NiTransformData':
            _skip_quat_rotation_keys(r)
            _skip_key_group_vec3(r)
            _skip_key_group_float(r)

        elif type_name == 'NiPosData':
            _skip_key_group_vec3(r)

        elif type_name == 'NiFloatData':
            _skip_key_group_float(r)

        elif type_name == 'NiBoolData':
            _skip_key_group_byte(r)

        elif type_name == 'NiStringPalette':
            r.string(); r.u32()

        elif type_name in ('NiBoolInterpolator', 'NiBoolTimelineInterpolator'):
            r.u8(); r.ref()

        elif type_name == 'NiBlendTransformInterpolator':
            r.u16(); r.u32()

        elif type_name == 'NiPathInterpolator':
            r.u16(); r.u32(); r.f32(); r.f32(); r.u16(); r.ref(); r.ref()

        elif type_name == 'bhkBlendController':
            r.read(26); r.u32()

        elif type_name == 'NiGeomMorpherController':
            r.read(26); r.u16(); r.ref(); r.u8()
            n_interp = r.u32()
            for _ in range(n_interp):
                r.ref()
            n_unk = r.u32()
            r.read(n_unk * 4)

        elif type_name == 'NiPSysEmitterCtlr':
            r.read(26); r.ref(); r.string(); r.ref()

        elif type_name == 'NiPSysModifierActiveCtlr':
            r.read(26); r.ref(); r.string()

        elif type_name in ('NiAlphaController', 'NiVisController'):
            r.read(26); r.ref()

        elif type_name == 'NiFogProperty':
            _parse_obv_object_net(r, blk)
            blk.raw_body_offset = r.pos - block_start
            r.u16(); r.f32(); r.read(12)

        elif type_name in ('NiDirectionalLight', 'NiAmbientLight'):
            _parse_obv_object_net(r, blk)
            _parse_obv_av_object(r, blk)
            blk.raw_body_offset = r.pos - block_start
            r.bool8()
            n_aff = r.u32()
            for _ in range(n_aff):
                r.ref()
            r.f32(); r.read(12); r.read(12); r.read(12)

        elif type_name == 'bhkHingeConstraint':
            n_ent = r.u32()
            for _ in range(n_ent):
                r.ref()
            r.u32(); r.read(80)

        elif type_name == 'bhkStiffSpringConstraint':
            n_ent = r.u32()
            for _ in range(n_ent):
                r.ref()
            r.u32(); r.read(16); r.read(16); r.f32()

        elif type_name == 'bhkMalleableConstraint':
            n_ent = r.u32()
            for _ in range(n_ent):
                r.ref()
            r.u32()
            ctype = r.u32()
            r.u32(); r.ref(); r.ref(); r.u32()
            if ctype == 1:
                r.read(80)
            elif ctype == 7:
                r.read(120)
            elif ctype == 2:
                r.read(124)
            r.f32(); r.f32()

        elif type_name == 'bhkMultiSphereShape':
            r.u32(); r.f32(); r.f32(); r.f32()
            n_sph = r.u32()
            r.read(n_sph * 16)

        elif type_name == 'bhkSimpleShapePhantom':
            r.ref(); r.read(4); r.read(20); r.read(8); r.read(64)

        elif type_name == 'NiDefaultAVObjectPalette':
            r.u32()
            n_objs = r.u32()
            for _ in range(n_objs):
                r.string(); r.ref()

        elif type_name == 'NiMorphData':
            n_morphs = r.u32()
            n_verts = r.u32()
            r.u8()
            for _ in range(n_morphs):
                r.string()
                r.read(n_verts * 12)

        elif type_name == 'NiPSysUpdateCtlr':
            r.read(26)

        elif type_name == 'NiBlendBoolInterpolator':
            r.u16(); r.u32(); r.u8()

        elif type_name == 'NiBlendFloatInterpolator':
            r.u16(); r.u32(); r.f32()

        elif type_name == 'NiBlendPoint3Interpolator':
            r.u16(); r.u32(); r.read(12)

        elif type_name in ('NiPSysEmitterInitialRadiusCtlr', 'NiPSysEmitterSpeedCtlr',
                           'NiPSysEmitterLifeSpanCtlr', 'NiPSysGravityStrengthCtlr',
                           'NiPSysEmitterDeclinationCtlr', 'NiPSysEmitterDeclinationVarCtlr'):
            r.read(26); r.ref(); r.string()

        elif type_name in ('NiPSysData', 'NiMeshPSysData'):
            n_verts = _parse_obv_geometry_data(r)
            has_radii = r.bool8()
            if has_radii:
                r.read(n_verts * 4)
            r.u16()
            has_sizes = r.bool8()
            if has_sizes:
                r.read(n_verts * 4)
            has_rotations = r.bool8()
            if has_rotations:
                r.read(n_verts * 16)
            has_rot_angles = r.bool8()
            if has_rot_angles:
                r.read(n_verts * 4)
            has_rot_axes = r.bool8()
            if has_rot_axes:
                r.read(n_verts * 12)
            r.read(n_verts * 28)
            has_unk_f3 = r.bool8()  # ver >= 20.0.0.4
            if has_unk_f3:
                r.read(n_verts * 4)  # Unknown Floats 3
            r.u16()  # Unknown Short 1
            r.u16()  # Unknown Short 2
            # NiMeshPSysData additional fields (ver >= 10.2.0.0):
            if type_name == 'NiMeshPSysData':
                r.u32()    # Unknown Int 2
                r.u8()     # Unknown Byte 3
                n_unk = r.u32()  # Num Unknown Ints 1
                r.read(n_unk * 4)  # Unknown Ints 1
                r.ref()    # Unknown Node (Ref)

        elif type_name == 'NiPSysResetOnLoopCtlr':
            r.read(26)

        elif type_name == 'NiDitherProperty':
            _parse_obv_object_net(r, blk)
            r.u16()  # Flags

        elif type_name == 'NiCamera':
            _parse_obv_object_net(r, blk)
            _parse_obv_av_object(r, blk)
            blk.raw_body_offset = r.pos - block_start
            r.u16()     # Unknown Short
            r.read(24)  # Frustum Left/Right/Top/Bottom/Near/Far (6Ã— f32)
            r.bool8()   # Use Orthographic Projection
            r.read(16)  # Viewport Left/Right/Top/Bottom (4Ã— f32)
            r.f32()     # LOD Adjust
            r.ref()     # Scene (Ref)
            n_sp = r.u32()  # Num Screen Polygons
            for _ in range(n_sp): r.ref()

        # --- Particle modifier types (NiPSysModifier subtypes) ---

        elif type_name == 'NiPSysAgeDeathModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.bool8()   # Spawn on Death
            r.ref()     # Spawn Modifier (Ref)

        elif type_name == 'NiPSysSpawnModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.u16()     # Num Spawn Generations
            r.f32()     # Percentage Spawned
            r.u16()     # Min Num to Spawn
            r.u16()     # Max Num to Spawn
            r.f32()     # Spawn Speed Chaos
            r.f32()     # Spawn Dir Chaos
            r.f32()     # Life Span
            r.f32()     # Life Span Variation

        elif type_name == 'NiPSysGravityModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.ref()      # Gravity Object (Ptr)
            r.read(12)   # Gravity Axis (Vec3)
            r.f32()      # Decay
            r.f32()      # Strength
            r.u32()      # Force Type
            r.f32()      # Turbulence
            r.f32()      # Turbulence Scale

        elif type_name == 'NiPSysGrowFadeModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.f32()     # Grow Time
            r.u16()     # Grow Generation
            r.f32()     # Fade Time
            r.u16()     # Fade Generation

        elif type_name == 'NiPSysColorModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.ref()     # Data (Ref to NiColorData)

        elif type_name == 'NiPSysPositionModifier':
            _parse_obv_psys_modifier(r, blk, block_start)

        elif type_name == 'NiPSysRotationModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.f32()     # Initial Rotation Speed
            r.f32()     # Initial Rotation Speed Variation
            r.f32()     # Initial Rotation Angle
            r.f32()     # Initial Rotation Angle Variation
            r.bool8()   # Random Rot Speed Sign
            r.bool8()   # Random Initial Axis
            r.read(12)  # Initial Axis (Vec3)

        elif type_name == 'NiPSysBoundUpdateModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.u16()     # Update Skip

        elif type_name == 'NiPSysBombModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.ref()      # Bomb Object (Ptr)
            r.read(12)   # Bomb Axis (Vec3)
            r.f32()      # Decay
            r.f32()      # Delta V
            r.u32()      # Decay Type
            r.u32()      # Symmetry Type

        elif type_name == 'NiPSysDragModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.ref()      # Parent (Ptr)
            r.read(12)   # Drag Axis (Vec3)
            r.f32()      # Percentage
            r.f32()      # Range
            r.f32()      # Range Falloff

        elif type_name == 'NiPSysColliderManager':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.ref()     # Collider (Ref)

        elif type_name == 'NiPSysMeshUpdateModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            n_meshes = r.u32()
            for _ in range(n_meshes): r.ref()

        elif type_name == 'BSParentVelocityModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.f32()     # Damping

        elif type_name == 'BSWindModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.f32()     # Strength

        elif type_name == 'BSPSysInheritVelocityModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.u32(); r.f32(); r.f32(); r.f32()

        elif type_name == 'BSPSysHavokUpdateModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            n_nodes = r.u32()
            for _ in range(n_nodes): r.ref()
            r.ref()     # Modifier (Ref)

        elif type_name == 'BSPSysRecycleBoundModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.read(24)  # 6Ã— float
            r.u32()

        elif type_name == 'BSPSysSubTexModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.u32(); r.f32(); r.f32(); r.f32(); r.f32(); r.f32(); r.f32()

        elif type_name == 'BSPSysLODModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.read(16)  # 4Ã— float

        elif type_name == 'BSPSysScaleModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            n_fl = r.u32()
            r.read(n_fl * 4)

        elif type_name == 'BSPSysSimpleColorModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.read(24)  # 6Ã— float
            r.read(48)  # 3Ã— Color4

        elif type_name == 'BSPSysStripUpdateModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.f32()     # Update Delta Time

        # --- Particle field modifiers ---

        elif type_name in ('NiPSysVortexFieldModifier', 'NiPSysGravityFieldModifier'):
            _parse_obv_psys_modifier(r, blk, block_start)
            r.ref(); r.f32(); r.f32(); r.bool8(); r.f32()  # NiPSysFieldModifier base
            r.read(12)   # Direction (Vec3)

        elif type_name == 'NiPSysDragFieldModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.ref(); r.f32(); r.f32(); r.bool8(); r.f32()
            r.bool8()    # Use Direction
            r.read(12)   # Direction (Vec3)

        elif type_name == 'NiPSysTurbulenceFieldModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.ref(); r.f32(); r.f32(); r.bool8(); r.f32()
            r.f32()      # Frequency

        elif type_name == 'NiPSysAirFieldModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.ref(); r.f32(); r.f32(); r.bool8(); r.f32()
            r.read(12)   # Direction (Vec3)
            r.f32(); r.f32()
            r.bool8(); r.bool8(); r.bool8()
            r.f32()

        elif type_name == 'NiPSysRadialFieldModifier':
            _parse_obv_psys_modifier(r, blk, block_start)
            r.ref(); r.f32(); r.f32(); r.bool8(); r.f32()
            r.i32()      # Radial Type

        # --- Particle emitter types ---

        elif type_name in ('NiPSysBoxEmitter', 'NiPSysCylinderEmitter',
                           'NiPSysSphereEmitter', 'BSPSysArrayEmitter'):
            _parse_obv_psys_emitter(r, blk, block_start)
            r.ref()      # Emitter Object (Ptr)
            if type_name == 'NiPSysBoxEmitter':
                r.f32(); r.f32(); r.f32()
            elif type_name == 'NiPSysCylinderEmitter':
                r.f32(); r.f32()
            elif type_name == 'NiPSysSphereEmitter':
                r.f32()

        elif type_name == 'NiPSysMeshEmitter':
            _parse_obv_psys_emitter(r, blk, block_start)
            n_meshes = r.u32()
            for _ in range(n_meshes): r.ref()
            r.u32()      # Initial Velocity Type
            r.u32()      # Emission Type
            r.read(12)   # Emission Axis (Vec3)

        # --- Particle collider types ---

        elif type_name == 'NiPSysPlanarCollider':
            _parse_obv_psys_collider(r)
            r.f32(); r.f32()
            r.read(12); r.read(12)

        elif type_name == 'NiPSysSphericalCollider':
            _parse_obv_psys_collider(r)
            r.f32()

        # --- Particle data types ---

        elif type_name == 'NiColorData':
            _skip_key_group_color4(r)

        elif type_name == 'NiPSysEmitterCtlrData':
            _skip_key_group_float(r)
            n_vis = r.u32()
            r.read(n_vis * 5)

    except (struct.error, IndexError):
        pass  # Partial parse OK; raw_bytes filled by caller

    return blk


# ---------------------------------------------------------------------------
# Oblivion NIF file parser
# ---------------------------------------------------------------------------

def parse_oblivion_nif(data: bytes) -> OblNif:
    """Parse a complete Oblivion NIF file from raw bytes."""
    r = NifReader(data)

    # Header string is newline-terminated
    nul = data.index(b'\n')
    r.pos = nul + 1

    version = r.u32()
    r.u8()                   # Endian byte (1 = LE)
    user_version = r.u32()
    num_blocks = r.u32()
    user_version_2 = r.u32()

    # ExportInfo (3 ShortStrings)
    creator = r.short_string()
    process_script = r.short_string()
    export_script = r.short_string()

    # Block types table
    num_block_types = r.u16()
    block_types = [r.string() for _ in range(num_block_types)]

    # Block type index per block
    block_type_indices = [r.u16() for _ in range(num_blocks)]

    # NOTE: Oblivion (ver 20.0.0.4) has NO Block Size array and NO String Table.
    # Unknown Int 2 / num groups (always 0)
    r.u32()

    # Block data
    blocks: list[NifBlock] = []
    for bi in range(num_blocks):
        type_name = block_types[block_type_indices[bi]]
        blk_start = r.pos
        # Enable ref tracking for raw blocks so orphan removal can remap precisely
        is_raw = type_name not in FULLY_PARSED and type_name in PARSE_AS_RAW
        r._track_refs = is_raw
        r._tracked_ref_offsets = []
        blk = _parse_block_oblivion(r, type_name, blk_start)
        if not blk.raw_bytes and type_name not in FULLY_PARSED:
            blk.raw_bytes = data[blk_start:r.pos]
            if is_raw:
                blk.raw_refs = [
                    (off - blk_start,
                     struct.unpack_from('<i', data, off)[0])
                    for off in r._tracked_ref_offsets
                    if off >= blk_start
                ]
        r._track_refs = False
        blocks.append(blk)

    # Footer
    num_roots = r.u32()
    root_indices = [r.i32() for _ in range(num_roots)]

    return OblNif(
        version=version,
        user_version=user_version,
        user_version_2=user_version_2,
        creator=creator,
        process_script=process_script,
        export_script=export_script,
        block_types=block_types,
        block_type_indices=block_type_indices,
        blocks=blocks,
        root_indices=root_indices,
    )


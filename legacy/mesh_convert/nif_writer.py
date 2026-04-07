"""Write Skyrim NIF binary files from the parsed representation.

Produces NIF version 20.2.0.7, BSStream 83 (Skyrim LE format, loadable by SE).

Key format differences vs Oblivion handled here:
  - String table: all NiObjectNET names become string-table indices
  - Block size array in header
  - NiAVObject: adds Unknown Short 1 (u16=8), removes Properties array
  - NiGeometry: MaterialData compound (Num Materials + Active Material +
    Material Needs Update) + BS Properties [shader, alpha]
  - NiGeometryData: Material CRC (u32) between UV sets field and Has Normals
  - BSLightingShaderProperty NiObjectNET: Shader Type (u32) precedes Name
  - NiSkinInstance inherits NiObject (no NiObjectNET header)
  - NiSourceTexture: Pixel Data ref present, Persist Render Data added
"""
from __future__ import annotations

import io
import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .nif_reader import NifBlock, OblNif

SKY_VERSION = 0x14020007  # 20.2.0.7
SKY_UV = 12
SKY_UV2 = 83


# ---------------------------------------------------------------------------
# Binary writer helper
# ---------------------------------------------------------------------------

class NifWriter:
    """Wraps a BytesIO object with sequential write helpers."""

    __slots__ = ('_buf',)

    def __init__(self) -> None:
        self._buf = io.BytesIO()

    def getvalue(self) -> bytes:
        return self._buf.getvalue()

    def write(self, data: bytes) -> None:
        self._buf.write(data)

    def u8(self, v: int) -> None:
        self._buf.write(struct.pack('<B', v))

    def u16(self, v: int) -> None:
        self._buf.write(struct.pack('<H', v))

    def i16(self, v: int) -> None:
        self._buf.write(struct.pack('<h', v))

    def u32(self, v: int) -> None:
        self._buf.write(struct.pack('<I', v))

    def i32(self, v: int) -> None:
        self._buf.write(struct.pack('<i', v))

    def f32(self, v: float) -> None:
        self._buf.write(struct.pack('<f', v))

    def bool8(self, v: bool) -> None:
        self.u8(1 if v else 0)

    def vec3(self, xyz: tuple) -> None:
        self._buf.write(struct.pack('<3f', *xyz))

    def mat33(self, m: tuple) -> None:
        self._buf.write(struct.pack('<9f', *m))

    def rgba(self, c: tuple) -> None:
        self._buf.write(struct.pack('<4f', *c))

    def rgb(self, c: tuple) -> None:
        self._buf.write(struct.pack('<3f', *c))

    def string(self, s: str) -> None:
        b = s.encode('utf-8')
        self.u32(len(b))
        self.write(b)

    def short_string(self, s: str, *, null_term: bool = True) -> None:
        b = s.encode('utf-8')
        if null_term:
            b += b'\x00'
        assert len(b) <= 255
        self.u8(len(b))
        self.write(b)

    def string_idx(self, idx: int) -> None:
        self.u32(idx)

    def ref(self, idx: int) -> None:
        self.i32(idx)


# ---------------------------------------------------------------------------
# String table builder
# ---------------------------------------------------------------------------

class StringTable:
    """Collects unique strings for the Skyrim NIF header string table."""

    def __init__(self) -> None:
        self._strings: list[str] = []
        self._index: dict[str, int] = {}

    def add(self, s: str) -> int:
        if not s:
            return 0xFFFFFFFF
        if s not in self._index:
            self._index[s] = len(self._strings)
            self._strings.append(s)
        return self._index[s]

    def strings(self) -> list[str]:
        return self._strings

    def max_len(self) -> int:
        if not self._strings:
            return 0
        return max(len(s.encode('utf-8')) for s in self._strings)


# ---------------------------------------------------------------------------
# Block writers — Skyrim format
# ---------------------------------------------------------------------------

def _write_sky_object_net(w: NifWriter, blk: NifBlock, st: StringTable, *, shader_type: int | None = None) -> None:
    """Write NiObjectNET fields in Skyrim format.

    BSLightingShaderProperty blocks in BSStream 83 have an extra Shader Type
    field before the Name — pass shader_type=<int> for those.
    """
    if shader_type is not None:
        w.u32(shader_type)
    w.string_idx(st.add(blk.name))
    w.u32(len(blk.extra_data))
    for ref in blk.extra_data:
        w.ref(ref)
    w.ref(blk.controller)


def _write_sky_av_object(w: NifWriter, blk: NifBlock) -> None:
    """Write NiAVObject fields in Skyrim format."""
    w.u16(blk.flags)
    w.u16(8)  # Unknown Short 1
    w.vec3(blk.translation)
    w.mat33(blk.rotation)
    w.f32(blk.scale)
    w.ref(blk.collision_object)


def _write_sky_geom_data_common(w: NifWriter, blk: NifBlock) -> None:
    """Write NiGeometryData common fields in Skyrim format."""
    w.u32(0)  # Group ID
    w.u16(blk.num_vertices)
    w.u8(0); w.u8(0)  # Keep/Compress Flags
    w.bool8(blk.has_vertices)
    if blk.has_vertices:
        for v in blk.vertices:
            w.vec3(v)
    bs_uv = blk.num_uv_sets
    if blk.tangents:
        bs_uv |= 0x1000  # Bit 12: Skyrim inline tangents present
    w.u16(bs_uv)  # BS Num UV Sets
    w.u32(0)  # Material CRC (Skyrim-only field)
    w.bool8(blk.has_normals)
    if blk.has_normals:
        for n in blk.normals:
            w.vec3(n)
        if blk.tangents:
            for t in blk.tangents:
                w.vec3(t)
            for b in blk.bitangents:
                w.vec3(b)
    w.vec3(blk.center)
    w.f32(blk.radius)
    w.bool8(blk.has_vertex_colors)
    if blk.has_vertex_colors:
        for c in blk.vertex_colors:
            w.rgba(c)
    num_uv = blk.num_uv_sets & 0x3F
    for ui in range(num_uv):
        uv_list = blk.uv_sets[ui] if ui < len(blk.uv_sets) else []
        uv_len = len(uv_list)
        for vi in range(blk.num_vertices):
            if vi < uv_len:
                w.f32(uv_list[vi][0])
                w.f32(uv_list[vi][1])
            else:
                w.f32(0.0)
                w.f32(0.0)
    w.u16(blk.consistency_flags)
    w.ref(blk.additional_data)


def write_sky_block(w: NifWriter, blk: NifBlock, st: StringTable) -> None:
    """Serialize one block to Skyrim NIF binary format."""
    tn = blk.type_name

    if tn in ('NiNode', 'BSFadeNode'):
        _write_sky_object_net(w, blk, st)
        _write_sky_av_object(w, blk)
        w.u32(len(blk.children))
        for ref in blk.children:
            w.ref(ref)
        w.u32(0)  # Num Effects

    elif tn == 'NiTriShape':
        _write_sky_object_net(w, blk, st)
        _write_sky_av_object(w, blk)
        w.ref(blk.data_ref)
        w.ref(blk.skin_instance)
        # MaterialData (ver >= 20.2.0.5)
        w.u32(0)     # Num Materials
        w.i32(-1)    # Active Material (FIX: was missing)
        w.bool8(False)  # Material Needs Update
        # BS Properties (BSStream > 0 && < 100)
        w.ref(blk.bs_shader_ref)
        w.ref(blk.bs_alpha_ref)

    elif tn == 'NiTriShapeData':
        _write_sky_geom_data_common(w, blk)
        num_tris = len(blk.triangles)
        w.u16(num_tris)
        w.u32(num_tris * 3)
        w.bool8(num_tris > 0)
        if num_tris > 0:
            for tri in blk.triangles:
                w.u16(tri[0]); w.u16(tri[1]); w.u16(tri[2])
        w.u16(0)  # Num Match Groups

    elif tn == 'NiTriStripsData':
        # Collision mesh data — write in strip format (not triangulated)
        _write_sky_geom_data_common(w, blk)
        num_tris = len(blk.triangles)
        w.u16(num_tris)
        num_strips = len(blk.strip_points)
        w.u16(num_strips)
        for strip in blk.strip_points:
            w.u16(len(strip))
        has_points = num_strips > 0
        w.bool8(has_points)
        if has_points:
            for strip in blk.strip_points:
                for idx in strip:
                    w.u16(idx)

    elif tn == 'NiAlphaProperty':
        _write_sky_object_net(w, blk, st)
        w.u16(blk.alpha_flags)
        w.u8(blk.alpha_threshold)

    elif tn == 'NiSourceTexture':
        _write_sky_object_net(w, blk, st)
        w.u8(blk.use_external)
        if blk.use_external:
            w.string(blk.file_name)
            w.ref(-1)  # Pixel Data ref (FIX: was missing)
            w.u32(getattr(blk, 'pixel_layout', 6))
            w.u32(getattr(blk, 'use_mipmaps', 2))
            w.u32(getattr(blk, 'alpha_format', 3))
            w.u8(1)  # Is static
            w.u8(0)  # Direct Render
            w.u8(0)  # Persist Render Data (Skyrim)
        else:
            w.ref(-1)  # Pixel Data ref
            w.u32(getattr(blk, 'pixel_layout', 6))
            w.u32(getattr(blk, 'use_mipmaps', 2))
            w.u32(getattr(blk, 'alpha_format', 3))
            w.u8(1)
            w.u8(0)
            w.u8(0)

    elif tn == 'NiSkinInstance':
        # NiSkinInstance inherits NiObject — NO NiObjectNET header (FIX)
        w.ref(blk.skin_data)
        w.ref(blk.skin_partition)
        w.ref(blk.skeleton_root)
        w.u32(len(blk.bone_refs))
        for ref in blk.bone_refs:
            w.ref(ref)

    elif tn == 'BSDismemberSkinInstance':
        # BSDismemberSkinInstance inherits NiSkinInstance — same base + partition table
        w.ref(blk.skin_data)
        w.ref(blk.skin_partition)
        w.ref(blk.skeleton_root)
        w.u32(len(blk.bone_refs))
        for ref in blk.bone_refs:
            w.ref(ref)
        parts = blk.dismember_partitions
        w.u32(len(parts))
        for part_flag, part_id in parts:
            w.u16(part_flag)
            w.u16(part_id)

    elif tn == 'NiStringExtraData':
        # NiExtraData inherits NiObject (NOT NiObjectNET) — just Name StringIdx
        w.string_idx(st.add(blk.name))
        w.string_idx(st.add(blk.extra_string))

    elif tn == 'NiBinaryExtraData':
        # NiExtraData inherits NiObject — just Name StringIdx
        w.string_idx(st.add(blk.name))
        w.u32(len(blk.extra_bytes))
        w.write(blk.extra_bytes)

    elif tn in ('BSXFlags', 'NiIntegerExtraData'):
        # NiIntegerExtraData -> NiExtraData -> NiObject — just Name StringIdx
        w.string_idx(st.add(blk.name))
        w.u32(blk.extra_integer)

    elif tn == 'BSLightingShaderProperty':
        # NiObjectNET with Shader Type prefix (BSStream 83)
        _write_sky_object_net(w, blk, st, shader_type=blk.shader_type)
        w.u32(blk.shader_flags1)
        w.u32(blk.shader_flags2)
        w.f32(blk.uv_offset[0]); w.f32(blk.uv_offset[1])
        w.f32(blk.uv_scale[0]);  w.f32(blk.uv_scale[1])
        w.ref(blk.texture_set_ref)
        w.rgb(blk.emissive_color)
        w.f32(blk.emissive_multiple)
        w.u32(blk.texture_clamp_mode)
        w.f32(blk.shader_alpha)
        w.f32(blk.refraction_strength)
        w.f32(blk.glossiness)
        w.rgb(blk.specular_color)
        w.f32(blk.specular_strength)
        w.f32(blk.lighting_effect1)
        w.f32(blk.lighting_effect2)

    elif tn == 'BSShaderTextureSet':
        # BSShaderTextureSet does NOT inherit NiObjectNET
        num_tex = len(blk.textures)
        w.i32(num_tex)
        for t in blk.textures:
            w.string(t)

    elif tn in ('NiStencilProperty', 'NiSpecularProperty'):
        _write_sky_object_net(w, blk, st)
        w.u16(blk.flags)

    elif tn == 'NiBillboardNode':
        _write_sky_object_net(w, blk, st)
        _write_sky_av_object(w, blk)
        w.u32(len(blk.children))
        for ref in blk.children:
            w.ref(ref)
        w.u32(0)  # Num Effects
        w.u16(blk.billboard_mode)

    elif tn == 'BSFurnitureMarker':
        # NiExtraData: Name StringIdx
        w.string_idx(st.add(blk.name))
        w.u32(len(blk.furniture_positions))
        for off_bytes, heading, pr1, pr2 in blk.furniture_positions:
            w.write(off_bytes)          # Offset (Vector3, 12 bytes)
            w.f32(float(heading))       # Heading: u16 → float in Skyrim
            w.u16(pr1)                  # Animation Type (u16 in Skyrim, was u8)
            w.u16(pr2)                  # Entry Properties (u16 in Skyrim, was u8)

    elif tn == 'NiTextKeyExtraData':
        # NiExtraData: Name StringIdx
        w.string_idx(st.add(blk.name))
        w.u32(len(blk.text_keys))
        for t, s in blk.text_keys:
            w.f32(t)
            w.string_idx(st.add(s))     # Key string → StringIdx in Skyrim

    elif tn == 'BSBound':
        # NiExtraData: Name StringIdx
        w.string_idx(st.add(blk.name))
        w.write(blk.bound_data)         # Center + Dimensions (24 bytes)

    elif blk.raw_body_offset > 0 and blk.raw_bytes:
        # Generic handler for raw blocks with parsed header:
        # The reader parsed an NiObjectNET, NiAVObject, or NiPSysModifier
        # header (with SizedStrings) and recorded where the body data starts.
        # We reconstruct: Skyrim header (StringIdx format) + raw body bytes.
        _HAS_AV_OBJECT = frozenset({
            'NiBillboardNode', 'NiDirectionalLight', 'NiAmbientLight',
            'NiParticleSystem', 'NiMeshParticleSystem', 'NiCamera',
        })
        _HAS_OBJECTNET_ONLY = frozenset({
            'NiZBufferProperty', 'NiFogProperty', 'NiDitherProperty',
        })
        if tn in _HAS_AV_OBJECT:
            _write_sky_object_net(w, blk, st)
            _write_sky_av_object(w, blk)
        elif tn in _HAS_OBJECTNET_ONLY:
            _write_sky_object_net(w, blk, st)
        else:
            # NiPSysModifier/NiPSysEmitter: Name as StringIdx + rest of
            # the modifier header (Order+Target+Active) as raw bytes.
            # The modifier header after Name is fixed-size (4+4+1=9 bytes).
            # For emitters, the emitter fields follow (also fixed-size).
            w.string_idx(st.add(blk.name))
            # Compute where name ends in Oblivion raw data:
            # SizedString = 4 + name_len bytes
            name_bytes = blk.name.encode('utf-8')
            obv_name_end = 4 + len(name_bytes)
            w.write(blk.raw_bytes[obv_name_end:blk.raw_body_offset])
        # Write the remaining type-specific body bytes unchanged
        w.write(blk.raw_bytes[blk.raw_body_offset:])

    else:
        # Raw bytes passthrough for blocks we don't convert
        w.write(blk.raw_bytes)


# ---------------------------------------------------------------------------
# Full NIF file builder
# ---------------------------------------------------------------------------

def build_skyrim_nif(nif: OblNif, *, creator: str = 'TESConversion') -> bytes:
    """Serialize the (transformed) NIF to Skyrim binary format."""

    def _serialize(string_table: StringTable) -> list:
        result = []
        for blk in nif.blocks:
            bw = NifWriter()
            write_sky_block(bw, blk, string_table)
            result.append(bw.getvalue())
        return result

    # Pass 1: populate string table
    st1 = StringTable()
    _serialize(st1)

    # Pass 2: serialize with finalized string table
    st2 = StringTable()
    for s in st1.strings():
        st2.add(s)
    block_bytes = _serialize(st2)
    strings = st2.strings()

    # Build type name table
    all_type_names: list[str] = []
    type_name_index: dict[str, int] = {}
    for blk in nif.blocks:
        tn = blk.type_name
        if tn not in type_name_index:
            type_name_index[tn] = len(all_type_names)
            all_type_names.append(tn)
    block_type_indices = [type_name_index[blk.type_name] for blk in nif.blocks]

    w = NifWriter()

    # Header
    w.write(b'Gamebryo File Format, Version 20.2.0.7\n')
    w.u32(SKY_VERSION)
    w.u8(1)  # Endian (LE)
    w.u32(SKY_UV)
    w.u32(len(nif.blocks))
    w.u32(SKY_UV2)

    # ExportInfo
    w.short_string(creator)
    w.short_string('TriStrip Process Script')
    w.short_string('Default Export Script')

    # Block types
    w.u16(len(all_type_names))
    for tn in all_type_names:
        w.string(tn)

    # Block type indices
    for idx in block_type_indices:
        w.u16(idx)

    # Block sizes (Skyrim only)
    for bb in block_bytes:
        w.u32(len(bb))

    # String table (Skyrim only)
    w.u32(len(strings))
    max_len = max((len(s.encode('utf-8')) for s in strings), default=0)
    w.u32(max_len)
    for s in strings:
        w.string(s)

    # Num groups
    w.u32(0)

    # Block data
    for bb in block_bytes:
        w.write(bb)

    # Footer
    w.u32(len(nif.root_indices))
    for ri in nif.root_indices:
        w.i32(ri)

    return w.getvalue()

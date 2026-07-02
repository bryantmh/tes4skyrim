#!/usr/bin/env python3
"""Dump Oblivion (TES4) NIF files to a human-readable text representation.

Usage:
    python tools/tes4_nif_analyzer.py <nif_or_dir> [--outdir references/export] [--max N]

Each NIF is written as a .txt file in the output directory preserving the
relative path structure.  The text format shows block hierarchy, types, flags,
transform data, and collision details — everything needed to debug conversion
issues without re-reading the binary each time.
"""

import argparse
import os
import sys
import time
from pathlib import Path

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

from pyffi.formats.nif import NifFormat


def _fmt_vec3(v):
    return f"({v.x:.4f}, {v.y:.4f}, {v.z:.4f})"


def _fmt_vec4(v):
    return f"({v.x:.4f}, {v.y:.4f}, {v.z:.4f}, {v.w:.4f})"


def _fmt_mat33(m):
    return (f"[{m.m_11:.4f} {m.m_12:.4f} {m.m_13:.4f}]"
            f"[{m.m_21:.4f} {m.m_22:.4f} {m.m_23:.4f}]"
            f"[{m.m_31:.4f} {m.m_32:.4f} {m.m_33:.4f}]")


def _fmt_flags(flags):
    return f"0x{flags:04X} ({flags})"


# Set by main() when --skin is passed: dump full skin/shader detail
DETAIL_SKIN = False


def _safe_name(block):
    n = getattr(block, 'name', None)
    if n is None:
        return ''
    if isinstance(n, bytes):
        return n.decode('latin-1', errors='replace').rstrip('\x00')
    return str(n)


def _block_index(data, block):
    """Return block index in the file's block list, or '?' if not found."""
    try:
        blocks = list(data.roots[0].tree()) if data.roots else []
        for i, b in enumerate(blocks):
            if b is block:
                return str(i)
    except Exception:
        pass
    return '?'


def dump_block(block, data, indent=0, lines=None):
    """Recursively dump a NIF block and its children to lines list."""
    if lines is None:
        lines = []
    if block is None:
        lines.append(f"{'  ' * indent}(null)")
        return lines

    prefix = '  ' * indent
    cls_name = block.__class__.__name__
    name = _safe_name(block)
    idx = _block_index(data, block)
    header = f"{prefix}[{idx}] {cls_name}"
    if name:
        header += f' "{name}"'
    lines.append(header)

    # Common NiAVObject fields
    if hasattr(block, 'flags'):
        lines.append(f"{prefix}  Flags: {_fmt_flags(block.flags)}")
    if hasattr(block, 'translation') and hasattr(block.translation, 'x'):
        lines.append(f"{prefix}  Translation: {_fmt_vec3(block.translation)}")
    if hasattr(block, 'rotation') and hasattr(block.rotation, 'm_11'):
        lines.append(f"{prefix}  Rotation: {_fmt_mat33(block.rotation)}")
    if hasattr(block, 'scale') and not isinstance(block.scale, type):
        lines.append(f"{prefix}  Scale: {block.scale:.4f}")

    # Extra data list
    if hasattr(block, 'extra_data_list') and block.num_extra_data_list > 0:
        lines.append(f"{prefix}  ExtraData ({block.num_extra_data_list}):")
        for ed in block.extra_data_list:
            if ed is None:
                continue
            ed_cls = ed.__class__.__name__
            ed_name = _safe_name(ed)
            if isinstance(ed, NifFormat.NiStringExtraData):
                val = bytes(ed.string_data).decode('latin-1', errors='replace').rstrip('\x00')
                lines.append(f"{prefix}    {ed_cls} '{ed_name}' = \"{val}\"")
            elif isinstance(ed, NifFormat.BSXFlags):
                lines.append(f"{prefix}    {ed_cls} '{ed_name}' = {ed.integer_data}")
            elif isinstance(ed, NifFormat.NiBinaryExtraData):
                sz = len(bytes(ed.binary_data))
                lines.append(f"{prefix}    {ed_cls} '{ed_name}' ({sz} bytes)")
            elif isinstance(ed, NifFormat.BSInvMarker):
                lines.append(f"{prefix}    BSInvMarker rotX={ed.rotation_x} rotY={ed.rotation_y} rotZ={ed.rotation_z} zoom={ed.zoom}")
            elif isinstance(ed, NifFormat.BSFurnitureMarker):
                lines.append(f"{prefix}    {ed_cls} '{ed_name}' positions={ed.num_positions}")
                for fpi in range(ed.num_positions):
                    fp = ed.positions[fpi]
                    parts = [f"offset={_fmt_vec3(fp.offset)}"]
                    if hasattr(fp, 'orientation'):
                        parts.append(f"orientation={fp.orientation}")
                    if hasattr(fp, 'position_ref_1'):
                        parts.append(f"ref1={fp.position_ref_1} ref2={fp.position_ref_2}")
                    if hasattr(fp, 'heading'):
                        parts.append(f"heading={fp.heading:.4f}")
                    if hasattr(fp, 'animation_type'):
                        parts.append(f"animType={fp.animation_type}")
                    if hasattr(fp, 'entry_properties'):
                        parts.append(f"entry={fp.entry_properties}")
                    lines.append(f"{prefix}      Pos[{fpi}]: {' '.join(parts)}")
            else:
                lines.append(f"{prefix}    {ed_cls} '{ed_name}'")

    # Properties
    if hasattr(block, 'properties') and hasattr(block, 'num_properties'):
        for prop in block.properties:
            if prop is None:
                continue
            p_cls = prop.__class__.__name__
            p_name = _safe_name(prop)
            lines.append(f"{prefix}  Property: {p_cls} '{p_name}'")
            if isinstance(prop, NifFormat.NiTexturingProperty):
                if prop.has_base_texture and prop.base_texture.source:
                    tex_path = bytes(prop.base_texture.source.file_name).decode('latin-1', errors='replace')
                    lines.append(f"{prefix}    BaseTexture: {tex_path}")
                ctrl = prop.controller
                while ctrl is not None:
                    lines.append(f"{prefix}    Controller: {ctrl.__class__.__name__}")
                    if isinstance(ctrl, NifFormat.NiFlipController):
                        lines.append(f"{prefix}      Sources: {len([s for s in ctrl.sources if s])}")
                    ctrl = getattr(ctrl, 'next_controller', None)
            elif isinstance(prop, NifFormat.NiMaterialProperty):
                ec = prop.emissive_color
                lines.append(f"{prefix}    Emissive: ({ec.r:.2f}, {ec.g:.2f}, {ec.b:.2f})")
                lines.append(f"{prefix}    Alpha: {prop.alpha:.2f}")
            elif isinstance(prop, NifFormat.NiAlphaProperty):
                lines.append(f"{prefix}    Flags: {_fmt_flags(prop.flags)}")
            elif isinstance(prop, NifFormat.NiStencilProperty):
                lines.append(f"{prefix}    (double-sided)")

    # bs_properties (Skyrim)
    if hasattr(block, 'bs_properties'):
        for bp in block.bs_properties:
            if bp is None:
                continue
            bp_cls = bp.__class__.__name__
            lines.append(f"{prefix}  BSProperty: {bp_cls}")
            if isinstance(bp, NifFormat.BSLightingShaderProperty):
                if DETAIL_SKIN:
                    lines.append(f"{prefix}    shaderType={getattr(bp, 'skyrim_shader_type', '?')} "
                                 f"SF1=0x{int(getattr(bp, 'shader_flags_1', 0)):08X} "
                                 f"SF2=0x{int(getattr(bp, 'shader_flags_2', 0)):08X}")
                    lines.append(f"{prefix}    alpha={getattr(bp, 'alpha', 0):.2f} "
                                 f"glossiness={getattr(bp, 'glossiness', 0):.2f} "
                                 f"uvScale=({bp.uv_scale.u:.2f},{bp.uv_scale.v:.2f})"
                                 if hasattr(bp, 'uv_scale') else f"{prefix}    (no uv_scale)")
                if hasattr(bp, 'texture_set') and bp.texture_set:
                    for ti in range(min(bp.texture_set.num_textures, 9)):
                        t = bp.texture_set.textures[ti]
                        if t:
                            lines.append(f"{prefix}    Tex[{ti}]: {t.decode('latin-1', errors='replace') if isinstance(t, bytes) else t}")
            elif isinstance(bp, NifFormat.BSEffectShaderProperty):
                st = getattr(bp, 'source_texture', b'')
                if st:
                    lines.append(f"{prefix}    SourceTex: {st.decode('latin-1', errors='replace') if isinstance(st, bytes) else st}")

    # Geometry data
    if isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
        d = block.data
        if d is not None:
            lines.append(f"{prefix}  Data: {d.__class__.__name__} "
                         f"verts={d.num_vertices} "
                         f"{'tris=' + str(d.num_triangles) if hasattr(d, 'num_triangles') else ''}"
                         f"{'strips=' + str(d.num_strips) if hasattr(d, 'num_strips') else ''} "
                         f"hasVC={d.has_vertex_colors} hasNormals={d.has_normals}")
            if hasattr(d, 'center') and hasattr(d.center, 'x'):
                lines.append(f"{prefix}    BoundSphere: center={_fmt_vec3(d.center)} radius={d.radius:.4f}")
            if hasattr(d, 'extra_vectors_flags'):
                lines.append(f"{prefix}    ExtraVectorsFlags: {d.extra_vectors_flags}")
        skin = getattr(block, 'skin_instance', None)
        if skin is not None:
            s_cls = skin.__class__.__name__
            n_bones = skin.num_bones if hasattr(skin, 'num_bones') else 0
            lines.append(f"{prefix}  Skin: {s_cls} bones={n_bones}")
            if isinstance(skin, NifFormat.BSDismemberSkinInstance):
                for pi in range(skin.num_partitions):
                    p = skin.partitions[pi]
                    lines.append(f"{prefix}    Partition[{pi}]: bodyPart={p.body_part} flags={p.part_flag}")
            if DETAIL_SKIN:
                _dump_skin_detail(skin, prefix + '    ', lines)

    # NiParticleSystem
    if isinstance(block, NifFormat.NiParticleSystem):
        d = block.data
        if d is not None:
            lines.append(f"{prefix}  Data: {d.__class__.__name__} verts={d.num_vertices}")
        lines.append(f"{prefix}  Modifiers ({block.num_modifiers}):")
        for m in block.modifiers:
            if m is not None:
                lines.append(f"{prefix}    {m.__class__.__name__} '{_safe_name(m)}'")
        ctrl = block.controller
        while ctrl is not None:
            lines.append(f"{prefix}  Controller: {ctrl.__class__.__name__}")
            ctrl = getattr(ctrl, 'next_controller', None)

    # Collision
    co = getattr(block, 'collision_object', None)
    if co is not None:
        lines.append(f"{prefix}  CollisionObject: {co.__class__.__name__} flags={co.flags}")
        rb = getattr(co, 'body', None)
        if rb is not None:
            lines.append(f"{prefix}    Body: {rb.__class__.__name__}")
            lines.append(f"{prefix}      mass={rb.mass:.2f} friction={rb.friction:.2f} "
                         f"restitution={rb.restitution:.2f}")
            lines.append(f"{prefix}      motionSystem={rb.motion_system} "
                         f"qualityType={rb.quality_type} "
                         f"deactivatorType={rb.deactivator_type}")
            lines.append(f"{prefix}      translation: {_fmt_vec4(rb.translation)}")
            lines.append(f"{prefix}      center: {_fmt_vec4(rb.center)}")
            lines.append(f"{prefix}      linearDamping={rb.linear_damping:.4f} "
                         f"angularDamping={rb.angular_damping:.4f}")
            lines.append(f"{prefix}      maxLinVel={rb.max_linear_velocity:.2f} "
                         f"maxAngVel={rb.max_angular_velocity:.2f}")
            if hasattr(rb, 'unknown_byte'):
                lines.append(f"{prefix}      broadphaseType={rb.unknown_byte}")
            if hasattr(rb, 'unknown_6_shorts'):
                vals = [rb.unknown_6_shorts[i] for i in range(6)]
                lines.append(f"{prefix}      unknown6shorts={vals}")
            if hasattr(rb, 'unknown_2_shorts'):
                vals = [rb.unknown_2_shorts[i] for i in range(2)]
                lines.append(f"{prefix}      unknown2shorts={vals}")
            shape = getattr(rb, 'shape', None)
            if shape is not None:
                _dump_collision_shape(shape, prefix + '      ', lines)
            # Constraints
            if hasattr(rb, 'num_constraints') and rb.num_constraints > 0:
                lines.append(f"{prefix}      Constraints ({rb.num_constraints}):")
                for ci in range(rb.num_constraints):
                    c = rb.constraints[ci]
                    if c is not None:
                        lines.append(f"{prefix}        {c.__class__.__name__}")

    # NiControllerManager / animation
    ctrl = getattr(block, 'controller', None)
    if ctrl is not None and isinstance(ctrl, NifFormat.NiControllerManager):
        lines.append(f"{prefix}  ControllerManager:")
        lines.append(f"{prefix}    Sequences ({ctrl.num_controller_sequences}):")
        for seq in ctrl.controller_sequences:
            if seq is None:
                continue
            seq_name = _safe_name(seq)
            lines.append(f"{prefix}      Sequence '{seq_name}' freq={seq.frequency:.2f} "
                         f"start={seq.start_time:.4f} stop={seq.stop_time:.4f} "
                         f"cycleType={seq.cycle_type}")
            for cb in seq.controlled_blocks:
                nn = getattr(cb, 'node_name', b'')
                if isinstance(nn, int):
                    nn = f"offset:{nn}"
                elif isinstance(nn, bytes):
                    nn = nn.decode('latin-1', errors='replace')
                ct = getattr(cb, 'controller_type', b'')
                if isinstance(ct, bytes):
                    ct = ct.decode('latin-1', errors='replace')
                interp = cb.interpolator
                i_cls = interp.__class__.__name__ if interp else 'None'
                lines.append(f"{prefix}        CB node='{nn}' type='{ct}' interp={i_cls}")
                if interp is not None and isinstance(interp, NifFormat.NiTransformInterpolator):
                    has_data = interp.data is not None
                    lines.append(f"{prefix}          hasData={has_data} "
                                 f"trans={_fmt_vec3(interp.translation)}")
        # Object palette
        pal = getattr(ctrl, 'object_palette', None)
        if pal is not None and hasattr(pal, 'num_objs'):
            lines.append(f"{prefix}    ObjectPalette ({pal.num_objs} entries)")

    # NiNode children (recurse)
    if hasattr(block, 'children') and hasattr(block, 'num_children'):
        for child in block.children:
            dump_block(child, data, indent + 1, lines)

    # Effects
    if hasattr(block, 'effects') and hasattr(block, 'num_effects') and block.num_effects > 0:
        for eff in block.effects:
            if eff is not None:
                lines.append(f"{prefix}  Effect: {eff.__class__.__name__} '{_safe_name(eff)}'")

    return lines


def _dump_skin_detail(skin, prefix, lines):
    """Dump skin instance internals: bones, skin data transforms, partitions."""
    root = getattr(skin, 'skeleton_root', None)
    lines.append(f"{prefix}SkeletonRoot: {_safe_name(root) if root is not None else '(null)'}")
    bones = list(getattr(skin, 'bones', []) or [])
    for bi, b in enumerate(bones):
        lines.append(f"{prefix}Bone[{bi}]: {_safe_name(b) if b is not None else '(null)'}")
    sd = getattr(skin, 'data', None)
    if sd is not None:
        st = sd.skin_transform
        lines.append(f"{prefix}SkinData transform: T={_fmt_vec3(st.translation)} "
                     f"R={_fmt_mat33(st.rotation)} S={st.scale:.4f}")
        for bi in range(min(sd.num_bones, len(bones) if bones else sd.num_bones)):
            bd = sd.bone_list[bi]
            bt = bd.skin_transform
            lines.append(f"{prefix}Bind[{bi}] ({_safe_name(bones[bi]) if bi < len(bones) and bones[bi] is not None else '?'}): "
                         f"T={_fmt_vec3(bt.translation)} S={bt.scale:.4f}")
            lines.append(f"{prefix}  R={_fmt_mat33(bt.rotation)}")
            lines.append(f"{prefix}  boundSphere: c={_fmt_vec3(bd.bounding_sphere_offset)} "
                         f"r={bd.bounding_sphere_radius:.4f} numVerts={bd.num_vertices}")
    part = getattr(skin, 'skin_partition', None)
    if part is None and sd is not None:
        part = getattr(sd, 'skin_partition', None)
    if part is not None:
        lines.append(f"{prefix}SkinPartition: {part.num_skin_partition_blocks} blocks")
        for pi in range(part.num_skin_partition_blocks):
            pb = part.skin_partition_blocks[pi]
            bone_idx = [pb.bones[i] for i in range(pb.num_bones)]
            lines.append(f"{prefix}  Block[{pi}]: verts={pb.num_vertices} tris={pb.num_triangles} "
                         f"strips={pb.num_strips} weightsPerVert={pb.num_weights_per_vertex} "
                         f"hasVertMap={pb.has_vertex_map} hasWeights={pb.has_vertex_weights} "
                         f"hasBoneIdx={pb.has_bone_indices} bones={bone_idx}")


def _dump_collision_shape(shape, prefix, lines):
    """Dump collision shape hierarchy."""
    if shape is None:
        lines.append(f"{prefix}Shape: (null)")
        return
    cls = shape.__class__.__name__
    lines.append(f"{prefix}Shape: {cls}")
    if isinstance(shape, NifFormat.bhkBoxShape):
        lines.append(f"{prefix}  dims=({shape.dimensions.x:.4f}, {shape.dimensions.y:.4f}, {shape.dimensions.z:.4f})")
        lines.append(f"{prefix}  radius={shape.radius:.4f} material={shape.material}")
    elif isinstance(shape, NifFormat.bhkSphereShape):
        lines.append(f"{prefix}  radius={shape.radius:.4f} material={shape.material}")
    elif isinstance(shape, NifFormat.bhkCapsuleShape):
        lines.append(f"{prefix}  radius={shape.radius:.4f} r1={shape.radius_1:.4f} r2={shape.radius_2:.4f}")
        lines.append(f"{prefix}  pt1={_fmt_vec3(shape.first_point)} pt2={_fmt_vec3(shape.second_point)}")
    elif isinstance(shape, NifFormat.bhkConvexVerticesShape):
        lines.append(f"{prefix}  verts={len(shape.vertices)} normals={len(shape.normals)} "
                     f"radius={shape.radius:.4f} material={shape.material}")
        if len(shape.vertices):
            xs = [v.x for v in shape.vertices]
            ys = [v.y for v in shape.vertices]
            zs = [v.z for v in shape.vertices]
            lines.append(f"{prefix}  extents: min=({min(xs):.4f}, {min(ys):.4f}, {min(zs):.4f}) "
                         f"max=({max(xs):.4f}, {max(ys):.4f}, {max(zs):.4f})")
    elif isinstance(shape, NifFormat.bhkNiTriStripsShape):
        lines.append(f"{prefix}  strips_data={len(list(shape.strips_data))} material={shape.material}")
    elif isinstance(shape, NifFormat.bhkPackedNiTriStripsShape):
        lines.append(f"{prefix}  subShapes={shape.num_sub_shapes}")
        if shape.data:
            lines.append(f"{prefix}  data: verts={shape.data.num_vertices} tris={shape.data.num_triangles}")
    elif isinstance(shape, NifFormat.bhkMoppBvTreeShape):
        lines.append(f"{prefix}  moppDataSize={shape.mopp_data_size}")
        _dump_collision_shape(shape.shape, prefix + '  ', lines)
    elif isinstance(shape, NifFormat.bhkListShape):
        lines.append(f"{prefix}  subShapes ({shape.num_sub_shapes}):")
        for s in shape.sub_shapes:
            _dump_collision_shape(s, prefix + '    ', lines)
    elif isinstance(shape, (NifFormat.bhkConvexTransformShape, NifFormat.bhkTransformShape)):
        _dump_collision_shape(shape.shape, prefix + '  ', lines)


def _world_transform(parents):
    """Compose local transforms root→block. parents = list from root to block (inclusive)."""
    import numpy as np
    M = np.eye(4)
    for b in parents:
        if not (hasattr(b, 'translation') and hasattr(b.translation, 'x')):
            continue
        L = np.eye(4)
        r = b.rotation
        L[0, :3] = [r.m_11, r.m_12, r.m_13]
        L[1, :3] = [r.m_21, r.m_22, r.m_23]
        L[2, :3] = [r.m_31, r.m_32, r.m_33]
        L[:3, :3] *= b.scale
        L[3, :3] = [b.translation.x, b.translation.y, b.translation.z]
        M = L @ M  # NIF row-vector convention: child-local applied first
    return M


def dump_bbox(nif_path, lines=None):
    """Print world-space bounding box per geometry block and aggregate."""
    import numpy as np
    if lines is None:
        lines = []
    data = NifFormat.Data()
    with open(nif_path, 'rb') as f:
        data.inspect(f)
        data.read(f)
    lines.append(f"NIF: {nif_path}")
    total_min = np.array([np.inf] * 3)
    total_max = np.array([-np.inf] * 3)

    def walk(block, chain):
        nonlocal total_min, total_max
        if block is None:
            return
        chain = chain + [block]
        d = getattr(block, 'data', None)
        if d is not None and hasattr(d, 'vertices') and getattr(d, 'num_vertices', 0) > 0:
            verts = np.array([[v.x, v.y, v.z] for v in d.vertices])
            M = _world_transform(chain)
            world = verts @ M[:3, :3] + M[3, :3]
            lo, hi = world.min(axis=0), world.max(axis=0)
            total_min = np.minimum(total_min, lo)
            total_max = np.maximum(total_max, hi)
            lines.append(f"  {block.__class__.__name__} '{_safe_name(block)}': "
                         f"min=({lo[0]:.2f}, {lo[1]:.2f}, {lo[2]:.2f}) "
                         f"max=({hi[0]:.2f}, {hi[1]:.2f}, {hi[2]:.2f})")
        if hasattr(block, 'children'):
            for c in block.children:
                walk(c, chain)

    for root in data.roots:
        walk(root, [])
    if np.isfinite(total_min).all():
        lines.append(f"  TOTAL: min=({total_min[0]:.2f}, {total_min[1]:.2f}, {total_min[2]:.2f}) "
                     f"max=({total_max[0]:.2f}, {total_max[1]:.2f}, {total_max[2]:.2f})")
    return lines


def analyze_nif(nif_path):
    """Read a NIF file and return a list of text lines describing its structure."""
    data = NifFormat.Data()
    lines = []
    try:
        with open(nif_path, 'rb') as f:
            data.inspect(f)
        lines.append(f"NIF: {nif_path}")
        lines.append(f"Version: 0x{data.version:08X} UserVer: {data.user_version} UV2: {data.user_version_2}")

        with open(nif_path, 'rb') as f:
            data.inspect(f)
            data.read(f)

        for root in data.roots:
            dump_block(root, data, indent=0, lines=lines)
    except Exception as e:
        lines.append(f"ERROR reading {nif_path}: {e}")

    return lines


def main():
    parser = argparse.ArgumentParser(description='Dump Oblivion NIF files to text')
    parser.add_argument('src', help='NIF file or directory to analyze')
    parser.add_argument('--outdir', default='references/export', help='Output directory for text dumps')
    parser.add_argument('--max', type=int, default=0, help='Max files to process (0=all)')
    parser.add_argument('--bbox', action='store_true',
                        help='Print world-space geometry bounding boxes to stdout instead of dumping')
    parser.add_argument('--skin', action='store_true',
                        help='Dump full skin detail (bones, bind matrices, partitions, shader flags)')
    args = parser.parse_args()

    if args.skin:
        global DETAIL_SKIN
        DETAIL_SKIN = True

    src = Path(args.src)
    outdir = Path(args.outdir)

    if src.is_file():
        nifs = [src]
        base_dir = src.parent
    else:
        nifs = sorted(src.rglob('*.nif'))
        base_dir = src

    if args.max > 0:
        nifs = nifs[:args.max]

    if args.bbox:
        for nif_path in nifs:
            try:
                print('\n'.join(dump_bbox(str(nif_path))))
            except Exception as e:
                print(f"ERROR {nif_path}: {e}")
        return

    print(f"Analyzing {len(nifs)} NIF files...")
    for nif_path in nifs:
        rel = nif_path.relative_to(base_dir) if base_dir != nif_path else nif_path.name
        out_path = outdir / str(rel).replace('.nif', '.txt')
        out_path.parent.mkdir(parents=True, exist_ok=True)

        lines = analyze_nif(str(nif_path))
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print(f"  {rel}")

    print(f"Done. Output in {outdir}/")


if __name__ == '__main__':
    main()

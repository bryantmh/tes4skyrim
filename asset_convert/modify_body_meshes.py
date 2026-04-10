"""Modify vanilla Skyrim character body meshes to add a body-part 44
(SBP_44_LOWERBODY / upper leg / greaves) partition.

The vanilla body mesh only has three body-part partitions:
  32 = Body   (whole torso + upper legs)
  34 = Forearms
  38 = Calves

This means equipping armor at biped slot 44 (greaves) hides nothing on the
character body, causing the greaves to clip with the visible body geometry.

This script splits the part-32 partition in half:
  32 = Torso  (spine/chest/pelvis bones drive the vertices)
  44 = Upper legs  (thigh bones drive the vertices)

Both malebody_0.nif and femalebody_0.nif are processed and written to
output/oblivion.esm/meshes/actors/character/character assets/

Usage:
    python tools/modify_body_meshes.py [--skyrim-mesh-root <path>]
                                       [--output-dir <path>]
"""
import argparse, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asset_convert.pyffi_monkey_patch  # noqa: F401 — must precede NifFormat
from pyffi.formats.nif import NifFormat

# Body-part slot for upper legs
SBP_44_LOWERBODY = 44
SBP_32_BODY      = 32

# Bones whose primary influence marks a vertex as "upper leg"
THIGH_BONES = {
    'NPC L Thigh [LThg]',
    'NPC R Thigh [RThg]',
}

# ──────────────────────────────────────────────────────────────────────────────

def _bone_list_from_instance(skin_instance):
    """Return a list of bone names from a NiSkinInstance (indexed by slot)."""
    names = []
    for i in range(skin_instance.num_bones):
        node = skin_instance.bones[i]
        if node is None:
            names.append('')
        else:
            names.append(
                bytes(node.name).rstrip(b'\x00').decode('latin-1', errors='replace'))
    return names


def _extract_block_data(blk):
    """Dump a SkinPartition block to plain Python structures (no PyFFI refs)."""
    nv   = blk.num_vertices
    nb   = blk.num_bones
    nt   = blk.num_triangles
    nwpv = blk.num_weights_per_vertex
    return {
        'num_bones': nb,
        'nwpv': nwpv,
        'has_vertex_map':     blk.has_vertex_map,
        'has_vertex_weights': blk.has_vertex_weights,
        'has_bone_indices':   blk.has_bone_indices,
        'has_faces':          blk.has_faces,
        'bones':   [int(blk.bones[i]) for i in range(nb)],
        'vmap':    [int(blk.vertex_map[i]) for i in range(nv)]
                   if blk.has_vertex_map else [],
        'vweights':[[float(blk.vertex_weights[i][wi]) for wi in range(nwpv)]
                    for i in range(nv)] if blk.has_vertex_weights else [],
        'bindices':[[int(blk.bone_indices[i][wi])    for wi in range(nwpv)]
                    for i in range(nv)] if blk.has_bone_indices else [],
        'triangles':[(int(blk.triangles[i].v_1),
                      int(blk.triangles[i].v_2),
                      int(blk.triangles[i].v_3))
                     for i in range(nt)] if blk.has_faces else [],
    }


def _fill_block(dst_blk, bd, tri_list):
    """Fill dst_blk in-place from block-data dict 'bd', limited to tri_list."""
    nwpv = bd['nwpv']
    used = sorted({v for t in tri_list for v in t})
    remap = {old: new_i for new_i, old in enumerate(used)}
    nv = len(used)
    nt = len(tri_list)

    dst_blk.num_vertices          = nv
    dst_blk.num_triangles         = nt
    dst_blk.num_bones             = bd['num_bones']
    dst_blk.num_strips            = 0
    dst_blk.num_weights_per_vertex = nwpv
    dst_blk.has_vertex_map        = bd['has_vertex_map']
    dst_blk.has_vertex_weights    = bd['has_vertex_weights']
    dst_blk.has_bone_indices      = bd['has_bone_indices']
    dst_blk.has_faces             = bd['has_faces']

    dst_blk.bones.update_size()
    for ib, b in enumerate(bd['bones']):
        dst_blk.bones[ib] = b

    if bd['has_vertex_map']:
        dst_blk.vertex_map.update_size()
        for ni, oi in enumerate(used):
            dst_blk.vertex_map[ni] = bd['vmap'][oi]

    if bd['has_vertex_weights'] and bd['vweights']:
        dst_blk.vertex_weights.update_size()
        for ni, oi in enumerate(used):
            for wi in range(nwpv):
                dst_blk.vertex_weights[ni][wi] = bd['vweights'][oi][wi]

    if bd['has_bone_indices'] and bd['bindices']:
        dst_blk.bone_indices.update_size()
        for ni, oi in enumerate(used):
            for wi in range(nwpv):
                dst_blk.bone_indices[ni][wi] = bd['bindices'][oi][wi]

    if bd['has_faces']:
        dst_blk.triangles.update_size()
        for ti, (v0, v1, v2) in enumerate(tri_list):
            dst_blk.triangles[ti].v_1 = remap[v0]
            dst_blk.triangles[ti].v_2 = remap[v1]
            dst_blk.triangles[ti].v_3 = remap[v2]


def _split_body_partition(nif_path, out_path):
    """Read nif_path, split body-part 32 into 32+44, write to out_path.

    For each NiTriShape that has a BSDismemberSkinInstance with body-part 32:
      - For each NiSkinPartition block assigned to body-part 32:
        1. Classify every vertex by primary bone influence.
        2. If vertex is primarily a thigh bone → mark as 'upper-leg'.
        3. If ANY vertex of a triangle is upper-leg → classify whole tri as upper-leg.
           (This avoids split vertices with mismatched partitions.)
        4. Build two new partition blocks: torso (32) and upper-leg (44).
      - Replace the single part-32 NiSkinPartition block with the two new ones.

    Returns True on success, False on any error.
    """
    data = NifFormat.Data()
    try:
        with open(nif_path, 'rb') as f:
            data.read(f)
    except Exception as e:
        print(f'  ERROR reading {nif_path}: {e}')
        return False

    modified = False

    for block in list(data.blocks):
        if not isinstance(block, NifFormat.NiTriShape):
            continue
        skin = getattr(block, 'skin_instance', None)
        if not isinstance(skin, NifFormat.BSDismemberSkinInstance):
            continue
        skin_data = getattr(skin, 'data', None)
        if skin_data is None:
            continue

        # Check if this skin instance has any body-part 32 partition
        has_body32 = any(
            skin.partitions[i].body_part == SBP_32_BODY
            for i in range(skin.num_partitions)
        )
        if not has_body32:
            continue

        # Build a mapping: bone slot index → bone name
        bone_names = _bone_list_from_instance(skin)

        # Get the NiSkinPartition
        sp = skin.skin_partition
        if sp is None:
            continue

        # ── Phase 1: Gather split decisions (before touching any arrays) ──
        # splits_info[bi] = (torso_tris, upper_tris) for body-part 32 blocks
        # that actually need splitting (upper_tris non-empty).
        # We extract ALL block data to Python before any in-place mutations.

        splits_needed = []   # (bi, torso_tris, upper_tris, block_data_dict)
        for bi in range(sp.num_skin_partition_blocks):
            blk    = sp.skin_partition_blocks[bi]
            bsd    = skin.partitions[bi]
            if bsd.body_part != SBP_32_BODY:
                continue

            nv = blk.num_vertices
            nwpv = blk.num_weights_per_vertex
            bone_slots = [int(blk.bones[i]) for i in range(blk.num_bones)]

            is_thigh = [False] * nv
            for lv in range(nv):
                if blk.has_vertex_weights and blk.has_bone_indices:
                    best_w, bi_local = -1.0, 0
                    for wi in range(nwpv):
                        w = blk.vertex_weights[lv][wi]
                        if w > best_w:
                            best_w, bi_local = w, wi
                    local_bi  = blk.bone_indices[lv][bi_local]
                    glob_slot = bone_slots[local_bi] if local_bi < len(bone_slots) else -1
                    if 0 <= glob_slot < len(bone_names):
                        is_thigh[lv] = bone_names[glob_slot] in THIGH_BONES

            torso_tris, upper_tris = [], []
            for ti in range(blk.num_triangles):
                tri = blk.triangles[ti]
                v0, v1, v2 = tri.v_1, tri.v_2, tri.v_3
                if any(is_thigh[vv] for vv in (v0, v1, v2)):
                    upper_tris.append((v0, v1, v2))
                else:
                    torso_tris.append((v0, v1, v2))

            if not upper_tris:
                print(f'    WARNING: no thigh vertices in part-32 block {bi} — keeping as-is')
                continue

            print(f'    Splitting part-32 block {bi}: '
                  f'{len(torso_tris)} torso tris + {len(upper_tris)} upper-leg tris')

            # Snapshot block data to Python before we touch anything
            bd = _extract_block_data(blk)
            splits_needed.append((bi, torso_tris, upper_tris, bd, bsd_part_flags(bsd)))

        if not splits_needed:
            continue

        # ── Phase 2: Modify each part-32 block in-place → torso data ──
        for bi, torso_tris, upper_tris, bd, _ in splits_needed:
            if torso_tris:
                _fill_block(sp.skin_partition_blocks[bi], bd, torso_tris)
            # (If torso_tris is empty, we still do the append but leave this block unchanged)

        # ── Phase 3: Append new upper-leg blocks ──
        for bi, torso_tris, upper_tris, bd, bsd_flags in splits_needed:
            new_sp_idx = int(sp.num_skin_partition_blocks)
            sp.num_skin_partition_blocks = new_sp_idx + 1
            sp.skin_partition_blocks.update_size()
            _fill_block(sp.skin_partition_blocks[new_sp_idx], bd, upper_tris)

            new_bsd_idx = int(skin.num_partitions)
            skin.num_partitions = new_bsd_idx + 1
            skin.partitions.update_size()
            new_bsd = skin.partitions[new_bsd_idx]
            new_bsd.body_part = SBP_44_LOWERBODY
            new_bsd.part_flag.pf_editor_visible    = bsd_flags['editor_visible']
            new_bsd.part_flag.pf_start_net_boneset = False  # shares bones with parent partition

        modified = True

    if not modified:
        print(f'  No part-32 upper-leg split needed (no thigh vertices found)')
        return False

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    try:
        with open(out_path, 'wb') as f:
            data.write(f)
        print(f'  Written: {out_path}')
        return True
    except Exception as e:
        print(f'  ERROR writing {out_path}: {e}')
        return False


def bsd_part_flags(bsd_part):
    """Snapshot BSD partition flags to a plain dict."""
    return {
        'editor_visible':    bool(bsd_part.part_flag.pf_editor_visible),
        'start_net_boneset': bool(bsd_part.part_flag.pf_start_net_boneset),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skyrim-mesh-root',
                        default=os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                             'references', 'Skyrim Meshes'),
                        help='Path to extracted Skyrim meshes (default: references/Skyrim Meshes)')
    parser.add_argument('--output-dir',
                        default=os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                             'output', 'oblivion.esm', 'meshes',
                                             'actors', 'character',
                                             'character assets'),
                        help='Output directory for modified body meshes')
    args = parser.parse_args()

    src_base = os.path.join(args.skyrim_mesh_root,
                            'meshes', 'actors', 'character', 'character assets')
    meshes = [
        ('malebody_0.nif',   'male body (low-weight)'),
        ('malebody_1.nif',   'male body (high-weight)'),
        ('femalebody_0.nif', 'female body (low-weight)'),
        ('femalebody_1.nif', 'female body (high-weight)'),
        ('malehands_0.nif',  'male hands'),
        ('malehands_1.nif',  'male hands high-weight'),
        ('femalehands_0.nif','female hands'),
        ('femalehands_1.nif','female hands high-weight'),
    ]

    ok = 0
    for fname, label in meshes:
        src = os.path.join(src_base, fname)
        if not os.path.exists(src):
            print(f'Skip {fname}: not found')
            continue
        dst = os.path.join(args.output_dir, fname)
        print(f'\nProcessing {fname} ({label}):')
        if _split_body_partition(src, dst):
            ok += 1

    print(f'\nDone — {ok} file(s) modified and written.')


if __name__ == '__main__':
    main()

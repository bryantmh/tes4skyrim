"""Modify vanilla Skyrim character body meshes to add a body-part 44
(SBP_44_LOWERBODY / lower body / greaves) partition.

The vanilla body mesh only has three body-part partitions:
  32 = Body   (whole torso + pelvis + upper legs, including the underwear)
  34 = Forearms
  38 = Calves

This means equipping armor at biped slot 44 (greaves) hides nothing on the
character body, causing the greaves to clip with the visible body geometry.

This script splits every part-32 partition at the waist:
  32 = Torso  (spine/chest bones drive the vertices)
  44 = Lower body  (pelvis + thigh bones drive the vertices)

The pelvis region is deliberately part of 44: Oblivion LowerBody items
(greaves/pants) cover hips + legs, so they must hide the hip skin AND the
underwear overlay (MaleUnderwear/FemaleUnderwear — separate shapes weighted
to pelvis/thighs, which this split reassigns to 44 as well).  The female
bra region stays in 32 (chest/clavicle-weighted) so shirts hide it instead.

IMPORTANT: partition 44 only renders in-game if the wearing ARMA also claims
biped slot 44.  The vanilla NakedTorso ARMA claims 32/34/38 only, so the
thighs would be INVISIBLE on a naked character.  tools/patch_body_slots.py
generates the companion plugin that adds slot 44 to every slot-32 ARMO/ARMA
(NakedTorso and all vanilla body armor) — it must be run whenever these
meshes are deployed.

All four body meshes (male/female, _0/_1 weight) are processed and written
to output/oblivion.esm/meshes/actors/character/character assets/

Usage:
    python asset_convert/modify_body_meshes.py [--skyrim-mesh-root <path>]
                                               [--output-dir <path>]
"""
import argparse, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asset_convert.pyffi_monkey_patch  # noqa: F401 — must precede NifFormat
from pyffi.formats.nif import NifFormat

# Body-part slot for lower body (hips + upper legs)
SBP_44_LOWERBODY = 44
SBP_32_BODY      = 32

# Bones whose primary influence marks a vertex as "lower body".  Pelvis is
# included so the hip/underwear region moves to part 44 with the thighs —
# pants (slot 44) must hide the underwear, not clip through it.  The waist
# boundary falls where NPC Spine [Spn0] overtakes NPC Pelvis [Pelv] as the
# dominant weight, which matches where Oblivion LowerBody clothing starts.
LOWERBODY_BONES = {
    'NPC L Thigh [LThg]',
    'NPC R Thigh [RThg]',
    'NPC Pelvis [Pelv]',
}

# For the underwear OVERLAY shapes (MaleUnderwear / FemaleUnderwear — not the
# *UnderwearBody* main body shapes) the classification is inverted: everything
# is lower-body (part 44) EXCEPT the female bra region, which is driven by
# these chest bones and must stay in part 32 so shirts hide it.  Without this
# the spine-weighted waistband of the male briefs stayed in part 32 and got
# clipped out by equipped shirts.
UNDERWEAR_TORSO_BONES = {
    'NPC Spine2 [Spn2]',
    'NPC R Clavicle [RClv]',
    'NPC L Clavicle [LClv]',
    'NPC R UpperarmTwist1 [RUt1]',
    'NPC R UpperarmTwist2 [RUt2]',
    'NPC L UpperarmTwist1 [LUt1]',
    'NPC L UpperarmTwist2 [LUt2]',
}


def _is_underwear_overlay(shape_name: str) -> bool:
    low = shape_name.lower()
    return 'underwear' in low and 'body' not in low

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
        2. If vertex is primarily a pelvis/thigh bone → mark as 'lower-body'.
        3. If ANY vertex of a triangle is lower-body → classify whole tri as
           lower-body.  (Avoids split vertices with mismatched partitions and
           biases the waist boundary into part 44, which pants always cover.)
        4. Build two new partition blocks: torso (32) and lower-body (44).
      - Replace the single part-32 NiSkinPartition block with the two new ones.

    Returns True on success, False on any error.
    """
    try:
        # sse_nif accepts a path or raw bytes (BSA-extracted SSE meshes) and
        # returns an LE graph either way.
        from asset_convert.sse_nif import read_nif
        data = read_nif(nif_path)
    except Exception as e:
        src_label = '<BSA bytes>' if isinstance(nif_path, (bytes, bytearray)) else nif_path
        print(f'  ERROR reading {src_label}: {e}')
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

        shape_name = bytes(block.name).rstrip(b'\x00').decode('latin-1', errors='replace')
        underwear_overlay = _is_underwear_overlay(shape_name)

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

            is_lower = [False] * nv
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
                        if underwear_overlay:
                            # Underwear overlay: lower-body unless bra region
                            is_lower[lv] = (bone_names[glob_slot]
                                            not in UNDERWEAR_TORSO_BONES)
                        else:
                            is_lower[lv] = bone_names[glob_slot] in LOWERBODY_BONES

            torso_tris, lower_tris = [], []
            for ti in range(blk.num_triangles):
                tri = blk.triangles[ti]
                v0, v1, v2 = tri.v_1, tri.v_2, tri.v_3
                if underwear_overlay:
                    # Bias boundary tris toward the bra (32): a bra edge tri in
                    # 44 would leave a notch when pants hide slot 44.
                    lower = all(is_lower[vv] for vv in (v0, v1, v2))
                else:
                    # Bias boundary tris toward 44: pants always cover the
                    # waistline, so overlapping into the torso is invisible.
                    lower = any(is_lower[vv] for vv in (v0, v1, v2))
                if lower:
                    lower_tris.append((v0, v1, v2))
                else:
                    torso_tris.append((v0, v1, v2))

            if not lower_tris:
                print(f'    WARNING: no lower-body vertices in part-32 block {bi} — keeping as-is')
                continue

            print(f'    Splitting part-32 block {bi}: '
                  f'{len(torso_tris)} torso tris + {len(lower_tris)} lower-body tris')

            # Snapshot block data to Python before we touch anything
            bd = _extract_block_data(blk)
            splits_needed.append((bi, torso_tris, lower_tris, bd, bsd_part_flags(bsd)))

        if not splits_needed:
            continue

        # ── Phase 2: Modify each part-32 block in-place → torso data ──
        # A block with NO torso triangles (e.g. the male underwear overlay is
        # 100% pelvis/thigh-weighted) is simply relabelled to part 44 in place;
        # appending would duplicate its geometry.
        for bi, torso_tris, lower_tris, bd, _ in splits_needed:
            if torso_tris:
                _fill_block(sp.skin_partition_blocks[bi], bd, torso_tris)
            else:
                skin.partitions[bi].body_part = SBP_44_LOWERBODY
                print(f'    Block {bi} is entirely lower-body - relabelled 32 to 44')

        # ── Phase 3: Append new lower-body blocks (for blocks actually split) ──
        for bi, torso_tris, lower_tris, bd, bsd_flags in splits_needed:
            if not torso_tris:
                continue  # relabelled in place above
            new_sp_idx = int(sp.num_skin_partition_blocks)
            sp.num_skin_partition_blocks = new_sp_idx + 1
            sp.skin_partition_blocks.update_size()
            _fill_block(sp.skin_partition_blocks[new_sp_idx], bd, lower_tris)

            new_bsd_idx = int(skin.num_partitions)
            skin.num_partitions = new_bsd_idx + 1
            skin.partitions.update_size()
            new_bsd = skin.partitions[new_bsd_idx]
            new_bsd.body_part = SBP_44_LOWERBODY
            new_bsd.part_flag.pf_editor_visible    = bsd_flags['editor_visible']
            new_bsd.part_flag.pf_start_net_boneset = False  # shares bones with parent partition

        modified = True

    if not modified:
        print(f'  No part-32 lower-body split needed (no pelvis/thigh vertices found)')
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
    parser.add_argument('--skyrim-mesh-root', default=None,
                        help='Explicit extracted Skyrim meshes tree '
                             '(default: auto-extract from the SSE BSAs)')
    parser.add_argument('--output-dir',
                        default=os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                             'output', 'oblivion.esm', 'meshes',
                                             'actors', 'character',
                                             'character assets'),
                        help='Output directory for modified body meshes')
    args = parser.parse_args()

    # Only the body meshes carry a part-32 partition; hands/feet meshes use
    # parts 33/34/37/38 and are left vanilla.
    meshes = [
        ('malebody_0.nif',   'male body (low-weight)'),
        ('malebody_1.nif',   'male body (high-weight)'),
        ('femalebody_0.nif', 'female body (low-weight)'),
        ('femalebody_1.nif', 'female body (high-weight)'),
    ]

    ok = 0
    for fname, label in meshes:
        if args.skyrim_mesh_root:
            src = os.path.join(args.skyrim_mesh_root, 'meshes', 'actors',
                               'character', 'character assets', fname)
            if not os.path.exists(src):
                print(f'Skip {fname}: not found in --skyrim-mesh-root')
                continue
        else:
            from asset_convert.skyrim_assets import get_body_nif_bytes
            src = get_body_nif_bytes(fname)
            if src is None:
                print(f'Skip {fname}: no SSE install detected for BSA extraction')
                continue
        dst = os.path.join(args.output_dir, fname)
        print(f'\nProcessing {fname} ({label}):')
        if _split_body_partition(src, dst):
            ok += 1

    print(f'\nDone — {ok} file(s) modified and written.')


if __name__ == '__main__':
    main()

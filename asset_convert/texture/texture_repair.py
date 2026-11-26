"""
The repairs a copied texture tree needs before Skyrim reads it the same way the source game did.

See: docs/commentary/asset_convert_texture.md#landscape-normal-maps-dxt1-shiny
"""

import os

from asset_convert.texture import image_transcode, landscape_normals, luminance_textures
from asset_convert.texture import parallax as _parallax


def repair_texture_tree(tex_dst, stats: dict, write_default: bool) -> None:
    """Transcode, expand and mask `tex_dst` (one namespace's textures) in place; re-running is a no-op.

    L8 glow maps become BGRA, DXT1 landscape normals gain an alpha mask, and a
    diffuse whose alpha became a `_p` height map drops to DXT1, except the ones
    `stats['mesh_conversion']['alpha_opacity_diffuse']` reads as opacity.
    `write_default` writes the shared `default_n.dds` beside the tree.
    """
    tc_found, tc_written, tc_failed = image_transcode.run(tex_dst)
    stats['tga_transcoded'] = tc_written
    if tc_found:
        print(f"  Loose TGA/BMP: {tc_found} found, {tc_written} transcoded to DDS"
              + (f", {tc_failed} failed" if tc_failed else ""))
    lum_checked, lum_fixed = luminance_textures.run(tex_dst)
    stats['luminance_textures_fixed'] = lum_fixed
    print(f"  Luminance textures: {lum_checked} L8 found, {lum_fixed} expanded to BGRA")
    checked, fixed = landscape_normals.run(tex_dst / 'landscape')
    stats['landscape_normals_fixed'] = fixed
    print(f"  Landscape normals: {checked} checked, {fixed} given the mask")
    _normalize_specular(tex_dst, stats, write_default)
    _strip_diffuse_alpha(tex_dst, stats)


def _normalize_specular(tex_dst, stats: dict, write_default: bool) -> None:
    """Give normal maps a constant specular mask; write the default normal when asked."""
    n_checked, n_fixed, n_kinds = landscape_normals.normalize_specular_alpha(
        tex_dst, skip=(os.sep + 'landscape' + os.sep,))
    if write_default:
        landscape_normals.write_default_normal(tex_dst.parent)
    stats['spec_alpha_fixed'] = n_fixed
    print(f"  Specular masks: {n_checked} normal maps checked, {n_fixed} given a constant mask "
          f"(alpha {landscape_normals.DEFAULT_MASK_ALPHA}/255)")
    if n_kinds:
        print('    ' + ', '.join(f'{k}={v}' for k, v in sorted(n_kinds.items())))


def _strip_diffuse_alpha(tex_dst, stats: dict) -> None:
    """Drop a diffuse whose alpha is a height map to DXT1, keeping the ones read as opacity."""
    n, skip, kept, saved = _parallax.strip_diffuse_alpha(
        tex_dst, keep=stats.get('mesh_conversion', {}).get('alpha_opacity_diffuse', ()))
    if n or skip or kept:
        stats['parallax_diffuse_bc1'] = n
        print(f"  Parallax diffuse: {n} DXT5->DXT1 ({saved / (1024 * 1024):.1f} MB saved)"
              + (f", {kept} kept (read as opacity)" if kept else "")
              + (f", {skip} already stripped" if skip else ""))

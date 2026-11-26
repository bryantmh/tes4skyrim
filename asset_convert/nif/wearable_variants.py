"""The extra files a converted worn mesh ships beside itself: weight and first-person variants."""

import io
import os

from asset_convert.character import wearable_plan as wp
from asset_convert.character.body_wrap import morph_converted_to_weight1
from asset_convert.character.first_person import first_person_copy, first_person_path
from asset_convert.character.head_gear import is_ground_model
from asset_convert.character.wearable_plan import mesh_is_female
from asset_convert.nif.mesh_scan_emit import (record_scan_alias, record_scan_entry,
                                              record_scan_removal)


def output_root(dst_path):
    """(tree root, meshes root, model path) around `dst_path`'s meshes/.

    Only convert_nif knows where the output tree is, so the passes that write
    beside it resolve their roots here. All three are None when the
    destination is not inside a meshes/ tree.
    """
    dstn = str(dst_path).replace('/', os.sep).replace('\\', os.sep)
    key = os.sep + 'meshes' + os.sep
    i = dstn.lower().rfind(key)
    if i < 0:
        return None, None, None
    return dstn[:i] + os.sep, dstn[:i + len(key)], dstn[i + len(key):]


def write_weight_variants(data, buf, src_path, dst_path, src_meshes_dir,
                          wearable_plan, creature, race):
    """Write the _0/_1 weight-slider pair and first-person copies the plugin references.

    The _1 file is NEVER a second conversion: the engine lerps the pair
    per-vertex and that REQUIRES identical topology, so it is the finished
    weight-0 mesh post-morphed by the fitted body morph.
    See: docs/commentary/asset_convert_armor.md#weight-slider-variants
    """
    srcl = str(src_path).lower().replace('\\', '/')
    wearable = (not creature and race is None
                and not is_ground_model(srcl.rsplit('/', 1)[-1]))
    if not (wearable and wearable_plan is not None):
        return

    want = wp.variants_for(wearable_plan, src_path, src_meshes_dir)
    root, ext = os.path.splitext(str(dst_path))

    if not want & wp.BASE:
        try:
            os.remove(dst_path)
        except OSError:
            pass
        record_scan_removal(output_root(dst_path)[2])
    if want & wp.W0:
        with open(root + '_0' + ext, 'wb') as f:
            f.write(buf.getvalue())
        record_scan_alias(output_root(root + '_0' + ext)[2],
                          output_root(dst_path)[2])
    worn = [('', buf.getvalue())]
    if want & wp.W1:
        worn = [('_0', buf.getvalue()), ('_1', _write_weight1(data, buf, src_path, root, ext))]
    if want & wp.WORN:
        _write_first_person(root, ext, worn)


def _write_weight1(data, buf, src_path, root, ext) -> bytes:
    """Write <name>_1 (the weight-0 mesh body-morphed) and return its bytes."""
    w1_bytes = None
    try:
        if morph_converted_to_weight1(data, mesh_is_female(src_path)):
            buf1 = io.BytesIO()
            data.write(buf1)
            w1_bytes = buf1.getvalue()
    except Exception:
        w1_bytes = None
    path = root + '_1' + ext
    with open(path, 'wb') as f:
        f.write(w1_bytes if w1_bytes is not None else buf.getvalue())
    if w1_bytes is None:
        record_scan_alias(output_root(path)[2], output_root(root + ext)[2])
        return buf.getvalue()
    record_scan_entry(data, output_root(path)[2])
    return w1_bytes


def _write_first_person(root, ext, worn) -> None:
    """Write `1stPerson<name>` beside each (suffix, bytes) worn variant.

    See: docs/commentary/asset_convert_armor.md#first-person-meshes
    """
    for suffix, nif_bytes in worn:
        path = first_person_path(root + suffix + ext)
        fp_data = first_person_copy(nif_bytes)
        out = io.BytesIO()
        fp_data.write(out)
        with open(path, 'wb') as f:
            f.write(out.getvalue())
        record_scan_entry(fp_data, output_root(path)[2])

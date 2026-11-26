"""First-person copies of worn meshes: only the geometry on the forearms and hands.

See: docs/commentary/asset_convert_armor.md#first-person-meshes
"""

import io

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert.character.skin_retarget import regen_skin_partition

#: File-name prefix of a first-person mesh, as vanilla names them (1stPersontorsom_1.nif).
FIRST_PERSON_PREFIX = '1stPerson'

#: Bone-name words that put geometry on the forearms or hands; the shield node hangs on the forearm.
_SHOWN_BONE_WORDS = ('forearm', 'hand', 'finger', 'shield')


def first_person_path(path: str) -> str:
    """`path` with its file name prefixed `1stPerson`; either slash separates folders."""
    cut = max(path.rfind('/'), path.rfind('\\')) + 1
    return path[:cut] + FIRST_PERSON_PREFIX + path[cut:]


def _shown(bone_name: str) -> bool:
    """True when geometry on `bone_name` belongs in first person."""
    name = bone_name.lower()
    return any(word in name for word in _SHOWN_BONE_WORDS)


def _vertex_bones(skin, num_vertices: int) -> list:
    """Each vertex's heaviest bone name ('' for an unweighted vertex)."""
    best = [(0.0, '')] * num_vertices
    for bone, bone_data in zip(skin.bones, skin.data.bone_list):
        name = bytes(bone.name).decode('latin-1') if bone else ''
        for vw in bone_data.vertex_weights:
            if vw.index < num_vertices and vw.weight > best[vw.index][0]:
                best[vw.index] = (vw.weight, name)
    return [name for _, name in best]


def _trim_skinned(shape) -> bool:
    """Keep the triangles whose three vertices hang mainly on shown bones; False when none remain."""
    skin = shape.skin_instance
    shown = [_shown(name) for name in _vertex_bones(skin, shape.data.num_vertices)]
    triangles = list(shape.data.get_triangles())
    kept = [t for t in triangles if all(shown[i] for i in t)]
    if not kept or len(kept) == len(triangles):
        return bool(kept)
    parts = getattr(skin, 'partitions', None)
    body_part = int(parts[0].body_part) if parts else None
    shape.data.set_triangles(kept)
    regen_skin_partition(shape, skin, bytes(shape.name).decode('latin-1'),
                         authored_body_part=body_part)
    return True


def _prn(data) -> str:
    """The mesh's Prn attach node ('' when it names none)."""
    for root in data.roots:
        for extra in root.get_extra_datas() if isinstance(root, NifFormat.NiObjectNET) else ():
            if isinstance(extra, NifFormat.NiStringExtraData) and bytes(extra.name) == b'Prn':
                return bytes(extra.string_data).decode('latin-1')
    return ''


def first_person_copy(nif_bytes: bytes):
    """A copy of the worn mesh `nif_bytes` holding only what first person shows.

    Skinned shapes keep the triangles on forearm and hand bones; a rigid shape
    stays only when the mesh's Prn hangs it there. Empty shapes are removed.
    """
    data = NifFormat.Data()
    data.read(io.BytesIO(nif_bytes))
    rigid_shown = _shown(_prn(data))
    parents = {id(b): b for root in data.roots for b in root.tree()
               if isinstance(b, NifFormat.NiNode)}
    for parent in parents.values():
        for child in list(parent.children):
            if not isinstance(child, NifFormat.NiTriBasedGeom):
                continue
            keep = _trim_skinned(child) if child.skin_instance else rigid_shown
            if not keep:
                parent.remove_child(child)
    return data

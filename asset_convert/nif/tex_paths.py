"""Texture path normalizing, shared by the converter and the shader builder.

Both are pure string/slot readers with no NIF state, so they sit below every
module that needs them.
"""
from asset_convert.game_paths import current_namespace


def bs_pp_texture_slots(prop):
    """Diffuse, normal and glow paths from an FO3/FNV BSShaderPPLightingProperty.

    FO3/FNV keep their paths in a BSShaderTextureSet on this property rather
    than on NiTexturingProperty, in the same slot order Skyrim uses: 0 diffuse,
    1 normal, 2 glow. All three are AUTHORED, so the normal is taken verbatim
    instead of being derived from the diffuse name.

    See: docs/commentary/asset_convert_nif.md#fo3fnv-shader-properties
    """
    tex_set = getattr(prop, 'texture_set', None)
    if tex_set is None:
        return b'', b'', b''
    slots = list(getattr(tex_set, 'textures', ()) or ())

    def slot(i):
        """The i-th texture path, or empty when absent or blank."""
        return slots[i] if i < len(slots) and slots[i] else b''

    return slot(0), slot(1), slot(2)


#: Source extensions that always ship as DDS, whatever the mesh calls them.
IMAGE_EXTS = ('tga', 'bmp')


def rewrite_tex_path(raw_bytes):
    """Prepend the game's namespace to a texture path that lacks it.

    Separators are normalized FIRST; a leading 'data\\' and a 'lowres\\'
    segment are dropped, and a .tga/.bmp name becomes .dds. The idempotence
    check keys on the ACTIVE namespace, so a path already carrying ANOTHER
    game's prefix is still namespaced rather than passed through unchanged.
    See: docs/commentary/asset_convert_shader.md#rewrite-tex-path
    See: docs/commentary/asset_convert_texture.md#per-game-asset-namespace
    """
    ns = current_namespace()
    path = raw_bytes.decode('utf-8', errors='replace').replace('/', '\\')
    if path.lower().startswith('data\\'):
        path = path[len('data\\'):]
    low = path.lower()

    if low.startswith('textures\\'):
        rest = path[len('textures\\'):]
    else:
        rest = path
    if rest.lower().startswith('lowres\\'):
        rest = rest[len('lowres\\'):]
    rest = as_dds(rest)

    if rest.lower().startswith(ns + '\\'):
        return 'Textures\\' + rest
    return 'Textures\\' + ns + '\\' + rest


def as_dds(path: str) -> str:
    """A texture path with a .tga or .bmp extension changed to .dds.

    Morrowind names textures .tga but its archives ship .dds, substituting at
    load; without this every converted path names a file that does not exist.
    """
    stem, dot, ext = path.rpartition('.')
    if dot and ext.lower() in IMAGE_EXTS:
        return stem + '.dds'
    return path

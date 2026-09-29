"""Texture path normalizing, shared by the converter and the shader builder.

Both are pure string/slot readers with no NIF state, so they sit below every
module that needs them.
"""
import re

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


#: A leading drive letter: `f:\...`, `F:/...` or drive-relative `f:...`.
_DRIVE = re.compile(r'^[A-Za-z]:')

#: A doubled dot before the extension, `name..dds`.
_DOUBLE_DOT_EXT = re.compile(r'\.\.([A-Za-z0-9]+)$')


def authored_rel(path: str, anchor: str = 'textures') -> tuple:
    """(path relative to the `anchor` folder, repairs made) for an AUTHORED path.

    Separators become `\\`; a drive letter and leading separators go. The path
    is cut after its LAST `anchor` folder only when that prefix is absolute
    (drive, rooted, UNC) or holds a `data` or `..` segment; otherwise one
    leading `data\\` then `anchor\\` is dropped. `name..ext` becomes
    `name.ext`. A clean path comes back unchanged with no repairs.
    See: docs/commentary/asset_convert_shader.md#authored-rel
    """
    s = (path or '').replace('/', '\\')
    fixes = []
    drive = _DRIVE.match(s)
    if drive:
        s = s[drive.end():].lstrip('\\')
        fixes.append('drive')
    elif s.startswith('\\'):
        fixes.append('rooted')
    if '\\\\' in s.strip('\\'):
        fixes.append('separator')
    segs, cut = _drop_prefix([x for x in s.split('\\') if x], anchor,
                             'drive' in fixes or 'rooted' in fixes)
    if cut:
        fixes.append('authoring_prefix')
    rel, dots = _DOUBLE_DOT_EXT.subn(r'.\1', '\\'.join(segs))
    if dots:
        fixes.append('double_dot')
    return rel, tuple(fixes)


#: Data-root folder an asset path is relative to, by lowercase extension.
_ANCHOR_BY_EXT = {
    **dict.fromkeys(('dds', 'tga', 'bmp'), 'textures'),
    **dict.fromkeys(('nif', 'kf', 'tri', 'egm', 'spt'), 'meshes'),
    **dict.fromkeys(('wav', 'mp3', 'ogg', 'xwm', 'fuz', 'lip'), 'sound'),
}


def asset_anchor(path: str) -> str:
    """The data-root folder (`textures`/`meshes`/`sound`) `path`'s extension names."""
    return _ANCHOR_BY_EXT.get(path.rpartition('.')[2].lower(), 'textures')


def names_anchor(path: str, anchor: str = 'textures') -> bool:
    """True when `authored_rel` drops an `anchor` folder from `path`."""
    return authored_rel(path, anchor)[0] != authored_rel(path, '')[0]


def _drop_prefix(segs, anchor, absolute):
    """(`segs` below the anchor folder, whether an authoring prefix was cut).

    See: docs/commentary/asset_convert_shader.md#authored-rel
    """
    low = [x.lower() for x in segs]
    hits = [i for i, x in enumerate(low[:-1]) if x == anchor]
    if hits:
        prefix = low[:hits[-1]]
        if absolute or 'data' in prefix or '..' in prefix:
            return segs[hits[-1] + 1:], True
    data = low[:1] == ['data']
    if data:
        segs, low = segs[1:], low[1:]
    if low[:1] == [anchor]:
        segs = segs[1:]
    return segs, data


def rewrite_tex_path(raw_bytes):
    """The namespaced `authored_rel` of an AUTHORED texture path, as DDS.

    See: docs/commentary/asset_convert_shader.md#rewrite-tex-path
    """
    rel, _fixes = authored_rel(raw_bytes.decode('utf-8', errors='replace'))
    return 'Textures\\' + current_namespace() + '\\' + as_dds(rel)


def full_res_twin(tex):
    """The full-resolution path a rewritten 'lowres\\' texture mirrors, or None.

    See: docs/commentary/asset_convert_shader.md#lowres-textures
    """
    prefix = 'Textures\\' + current_namespace() + '\\'
    lowres = prefix + 'lowres\\'
    if tex.lower().startswith(lowres.lower()):
        return prefix + tex[len(lowres):]
    return None


def as_dds(path: str) -> str:
    """A texture path with a .tga or .bmp extension changed to .dds.

    Morrowind names textures .tga but its archives ship .dds, substituting at
    load; without this every converted path names a file that does not exist.
    """
    stem, dot, ext = path.rpartition('.')
    if dot and ext.lower() in IMAGE_EXTS:
        return stem + '.dds'
    return path

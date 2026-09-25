"""Decide which output textures are excluded from the shipped archive.

Oblivion's BSAs carry textures for content the conversion never emits, and
copying the texture tree wholesale ships all of it.  `bsa_pack` applies
`is_excluded` while staging the textures archive; nothing here deletes, and
`output/` always keeps the full loose tree for testing.

This is a BLACKLIST of asset categories Skyrim can never load, not a keep-set
reconstructed from references: a blacklist can only ship a file nothing needs,
where a keep-set can withhold one something does.  Every rule drops zero of
the paths named by 36,128 converted meshes.

`MANIFEST_NAME` lives in the EXPORT dir because output/<plugin>/ is a Data
root, where every plugin would write the same filename and collide on install.
See: docs/commentary/asset_convert_texture.md#the-blacklist-prune
"""

from pathlib import Path

#: Second path segment (below the game namespace) whose whole subtree is cut.
_EXCLUDED_DIRS = frozenset({
    'faces',          # FaceGen bakes keyed <formid>_0.dds; Skyrim regenerates
    'menus',          # Oblivion UI atlases; Skyrim's interface is unrelated
    'menus80',
    'menus50',
    'landscapelod',   # superseded by our own LOD bake into AutoConvertLOD
    'distantlod',
})

#: The only extensions the engine loads from a textures archive.
_TEXTURE_SUFFIXES = frozenset({'.dds', '.tga'})


def is_excluded(key: str) -> bool:
    """True if this textures-root-relative key is kept out of the archive.

    `key` is lowercased and posix-separated, as `bsa_pack` derives it:
    `tes4/faces/oblivion.esm/0001a2b3_0.dds`.  Anything that is not a texture
    is excluded too -- mods ship build junk under `textures/`, one of them an
    entire 1.1 GB Oblivion `Data` folder nested under an architecture path.
    """
    dot = key.rfind('.')
    if dot < 0 or key[dot:] not in _TEXTURE_SUFFIXES:
        return True
    parts = key.split('/')
    return len(parts) > 1 and parts[1] in _EXCLUDED_DIRS


#: Texture set harvested by mesh conversion, read back by a later phase.
MANIFEST_NAME = 'textures_used.txt'

#: Of those, the APPLY_HILIGHT2 detail overlays whose alpha is a blend weight.
OVERLAY_MANIFEST_NAME = 'overlay_diffuses.txt'

#: Bytes that may appear in a texture path embedded in a binary asset.
_TEX_PATH_BYTES = frozenset(
    c for c in range(256)
    if bytes([c]).isalnum() or bytes([c]) in b'_\\/ .()&+-'
)

#: Longest run of path bytes before the '.dds', bounding the backwards walk.
_TEX_PATH_MAX = 200


def write_manifest(export_dir, refs, name: str = MANIFEST_NAME) -> Path:
    """Record a set of texture keys for a later phase to read back."""
    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    out = export_dir / name   # noqa: plugin-path (record/manifest filename)
    out.write_text('\n'.join(sorted(refs)), encoding='utf-8')
    return out


def read_manifest(export_dir, name: str = MANIFEST_NAME) -> set:
    """Read back a manifest written by write_manifest (empty if never run)."""
    f = Path(export_dir) / name   # noqa: plugin-path (record/manifest filename)
    if not f.is_file():
        return set()
    return {ln.strip() for ln in
            f.read_text(encoding='utf-8').splitlines() if ln.strip()}


def texture_refs_in(raw: bytes) -> list:
    """Every texture path in one binary asset.

    Locates each `.dds` with `bytes.find` (a C-level memchr scan), then walks
    backwards over the legal path bytes, which is 22.8x faster than the
    equivalent lazy regex over multi-GB LOD tiles.
    See: docs/commentary/asset_convert_texture.md#the-blacklist-prune
    """
    low = raw.lower()
    out = []
    end = 0                          # finditer is non-overlapping; so are we
    i = low.find(b'.dds')
    while i != -1:
        stop = i + 4
        start = i
        limit = max(end, i - _TEX_PATH_MAX)
        while start > limit and raw[start - 1] in _TEX_PATH_BYTES:
            start -= 1
        if i - start >= 3:           # the regex demanded 3+ chars before .dds
            out.append(raw[start:stop])
            end = stop
        i = low.find(b'.dds', stop)
    return out

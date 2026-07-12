"""Drop output textures that nothing we ship references.

Oblivion's BSAs carry textures for content the conversion never emits — the
character/face/body art whose meshes are skipped outright being the biggest
block — and copying the texture tree wholesale ships all of it.

The reference set is assembled from every producer of a texture reference,
without re-reading the (multi-GB) output tree:

  * meshes  — nif_converter harvests each mesh's texture paths as it writes it
              (batch_convert's stats['textures_used']), so this costs nothing
  * records — the plugin's own texture fields (EYES/HAIR icons, BOOK, LTEX...),
              read from the export text
  * late assets — speedtree NIFs, LOD .bto/.btr and _far meshes are generated
              after mesh conversion, so they are scanned from disk; there are
              few of them and they are small

Anything under textures/ that no reference names is deleted.
"""

import os
import re
from pathlib import Path

# A texture reference embedded in a binary asset.
_TEX_BYTES_RE = re.compile(rb'[A-Za-z0-9_\\/ .()&+-]{3,200}?\.dds', re.IGNORECASE)
# A texture path in the KEY=VALUE export text.
_TEX_TEXT_RE = re.compile(r'[a-z0-9_\\/ .()&+-]*?\.dds')

# Binary assets that can name a texture and are produced after mesh conversion.
_LATE_ASSET_SUFFIXES = ('.nif', '.bto', '.btr')


# Where mesh conversion leaves the texture set it harvested, for the prune
# phase to pick up later.
MANIFEST_NAME = 'textures_used.txt'


def write_manifest(plugin_dir, refs) -> Path:
    """Record the textures the converted meshes reference."""
    plugin_dir = Path(plugin_dir)
    plugin_dir.mkdir(parents=True, exist_ok=True)
    out = plugin_dir / MANIFEST_NAME
    out.write_text('\n'.join(sorted(refs)), encoding='utf-8')
    return out


def read_manifest(plugin_dir) -> set:
    """Read back the mesh-conversion texture set (empty if it was never run)."""
    f = Path(plugin_dir) / MANIFEST_NAME
    if not f.is_file():
        return set()
    return {ln.strip() for ln in
            f.read_text(encoding='utf-8').splitlines() if ln.strip()}


def _norm(raw) -> str:
    """Normalise a texture reference to a key relative to the textures root."""
    if isinstance(raw, bytes):
        raw = raw.decode('latin-1', errors='replace')
    p = raw.strip().lower().replace('\\', '/')
    while '//' in p:       # the export escapes its backslashes
        p = p.replace('//', '/')
    p = p.lstrip('/')
    if not p.endswith('.dds'):
        return ''
    if p.startswith('data/'):
        p = p[len('data/'):]
    if p.startswith('textures/'):
        p = p[len('textures/'):]
    return p


def refs_from_records(export_dir) -> set:
    """Texture paths named by the plugin's records (icons, LTEX, ...)."""
    refs = set()
    for txt in Path(export_dir).glob('*.txt'):
        body = txt.read_text(encoding='utf-8', errors='replace').lower()
        for m in _TEX_TEXT_RE.finditer(body):
            p = _norm(m.group(0))     # collapses the export's escaped slashes
            if p:
                refs.add(p)
                # records name the path as Oblivion wrote it; the importer
                # prefixes it with tes4\ on the way into the plugin.
                refs.add('tes4/' + p)
    return refs


def refs_from_assets(paths) -> set:
    """Texture paths embedded in binary assets (generated meshes, LOD tiles)."""
    refs = set()
    for p in paths:
        try:
            raw = Path(p).read_bytes()
        except OSError:
            continue
        for m in _TEX_BYTES_RE.finditer(raw):
            key = _norm(m.group(0))
            if key:
                refs.add(key)
    return refs


def _companions(refs: set) -> set:
    """Maps the engine loads implicitly beside a referenced diffuse.

    A mesh names its diffuse and normal, but Skyrim's shader also reaches for
    the environment-mask/glow/specular siblings when the shader flags call for
    them, and those are never spelled out in the NIF.  Keeping them costs a few
    MB and avoids stripping a map some shader silently wants.
    """
    extra = set()
    for r in refs:
        stem = r[:-4]
        for suffix in ('_n', '_g', '_m', '_s', '_e', '_em', '_p', '_sk', '_msn'):
            if stem.endswith(suffix):
                continue
            extra.add(stem + suffix + '.dds')
    return extra


def build_refs(plugin_dir, export_dir, mesh_texture_refs=None) -> set:
    """Every texture the shipped plugin can ask for, as textures-root keys."""
    plugin_dir = Path(plugin_dir)

    if mesh_texture_refs is None:
        mesh_texture_refs = read_manifest(plugin_dir)
    if not mesh_texture_refs:
        raise RuntimeError(
            f'no mesh texture manifest in {plugin_dir} — run mesh conversion '
            f'first; pruning without it would delete textures that are in use')

    refs = {_norm(r) for r in mesh_texture_refs}
    refs.discard('')
    refs |= refs_from_records(export_dir)

    # Meshes generated after mesh conversion (speedtrees, _far, LOD/terrain
    # tiles, the grass copies) — no converter harvested these, so read them.
    late = [p for p in (plugin_dir / 'meshes').rglob('*')
            if p.suffix.lower() in _LATE_ASSET_SUFFIXES]
    refs |= refs_from_assets(late)

    refs |= _companions(refs)
    return refs


def prune(plugin_dir, export_dir, mesh_texture_refs=None,
          dry_run: bool = False) -> tuple:
    """Delete every texture under *plugin_dir* that nothing references.

    mesh_texture_refs: the set nif_converter harvested while writing the meshes.
    Defaults to the manifest mesh conversion left behind.
    Returns (kept, removed, bytes_freed).
    """
    plugin_dir = Path(plugin_dir)
    tex_root = plugin_dir / 'textures'
    if not tex_root.is_dir():
        return 0, 0, 0

    refs = build_refs(plugin_dir, export_dir, mesh_texture_refs)

    kept = removed = 0
    freed = 0
    for f in tex_root.rglob('*'):
        if not f.is_file():
            continue
        key = f.relative_to(tex_root).as_posix().lower()
        if key in refs:
            kept += 1
            continue
        size = f.stat().st_size
        if not dry_run:
            try:
                f.unlink()
            except OSError:
                kept += 1
                continue
        removed += 1
        freed += size

    if not dry_run:
        # Remove the directories the deletions emptied out.
        for d in sorted((p for p in tex_root.rglob('*') if p.is_dir()),
                        key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass   # not empty

    return kept, removed, freed

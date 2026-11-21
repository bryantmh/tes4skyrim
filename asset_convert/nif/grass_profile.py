"""Grass model placement + shader profile for GRAS model NIFs.

Two engine-facing contracts for grass, discovered by surveying every
working grass plugin (vanilla Skyrim.esm, USSEP, BSHeartland.esm,
Skyrim Extended Cut, Legacy Orsinium):

**1. Path contract — grass models live under ``meshes\\landscape\\grass\\``.**
All 45 distinct GRAS MODL paths across those plugins contain
``landscape\\grass\\`` (44 directly under ``meshes\\landscape\\grass``).
No working GRAS record anywhere points outside it — the same kind of
hardcoded naming contract as the ``NPC Root [Root]`` skeleton bone.
Converted grass NIFs are therefore COPIED (sources shared with FLOR/STAT
stay put) to ``meshes\\landscape\\grass\\tes4_<basename>.nif`` and
convert_GRAS writes the matching MODL via grass_model_dest().

**2. Shader profile.**  Skyrim's grass renderer instances GRAS model
geometry itself instead of drawing the NIF like a placed object, and it
is far pickier about shader state than the static renderer.  Every
vanilla grass mesh (LE `references/Skyrim Meshes/meshes/landscape/grass/`)
shares one shader profile that differs from what generic Oblivion mesh
conversion produces:

  NiAlphaProperty        vanilla: alpha TEST only (0x12EC-style, blend bit
                         clear).  Oblivion grass has blending enabled
                         (0x12ED); the grass instancer cannot depth-sort
                         blades, so blended grass is unreliable.
  BSLightingShaderProperty
    SLSF1                vanilla 0x82400308: Vertex_Alpha(0x8) and
                         Own_Emit(0x400000) SET, Specular(0x1) CLEAR.
                         Generic conversion emits Specular with
                         glossiness 0 → pow(NdotH, 0) = 1.0 = blinding.
    emissive             black x1.0 (generic conversion: x0.0)
    glossiness           80.0
    specular             white, strength 1.0
    lighting effects     0.3 / 2.0
    texture clamp        0 (vanilla grass; generic meshes use 3)

Geometry, UVs, vertex colors (alpha = wind weight) and texture paths are
left untouched.  Output stays LE-format (stream 83) like the rest of the
pipeline.

Grass NIFs are identified from the export's GRAS.txt Model.MODL fields.

CLI:
    python -m asset_convert.nif.grass_profile <export_dir> <output_meshes_root>
    # e.g. python -m asset_convert.nif.grass_profile export/Oblivion.esm \
    #          output/Oblivion.esm/meshes
"""
from asset_convert.game_paths import current_namespace
import shutil
import struct
import sys
from pathlib import Path

from asset_convert.game_paths import win_join
from asset_convert.nif.grass_profile_morrowind import add_wind_weights


def _nif_format():
    """Lazy pyffi import so tes5_import can use grass_model_dest() without
    pulling pyffi in."""
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    return NifFormat


def _f32(v):
    """Round to float32 so comparisons match values read back from a NIF."""
    return struct.unpack('<f', struct.pack('<f', v))[0]


# Vanilla grass BSLightingShaderProperty profile (all 29 vanilla grass NIFs)
GRASS_EMISSIVE_MULT = _f32(1.0)
GRASS_GLOSSINESS = _f32(80.0)
GRASS_SPECULAR_STRENGTH = _f32(1.0)
GRASS_LIGHTING_EFFECT_1 = _f32(0.3)
GRASS_LIGHTING_EFFECT_2 = _f32(2.0)
GRASS_TEXTURE_CLAMP = 0
ALPHA_BLEND_BIT = 0x0001        # NiAlphaProperty flags bit 0: blending enable
# Vanilla grass alpha-test thresholds span 40-100; Oblivion uses up to 128.
GRASS_MAX_ALPHA_THRESHOLD = 100


def grass_model_dest(model_path):
    """Map a TES4 GRAS Model.MODL path to its Skyrim location.

    Flattened into the one shared grass folder under a ``<game>_`` prefix,
    backslash-separated because convert_GRAS writes this value verbatim.
    See: docs/commentary/asset_convert_terrain.md#grass-conversion-record-invariants-shader
    """
    base = model_path.replace('/', '\\').rsplit('\\', 1)[-1].lower()
    return 'landscape\\grass\\' + current_namespace() + '_' + base


def load_grass_model_paths(export_dir, _seen=None):
    """Return the set of GRAS Model.MODL paths (lowercase, backslash form)
    from an export directory's GRAS.txt.

    The BASE's records count too.  An asset-only tree -- a retexture stack or
    an ordered merge -- ships no GRAS record of its own, so on its own evidence
    the plugin places no grass at all and every grass model silently loses its
    vanilla shader profile.  See asset_convert/base_plugins.
    """
    from asset_convert.sources import base_plugins
    paths = set()
    # `_seen` closes a base CYCLE; comparing against export_dir alone
    # catches only A->A, and A->B->A recursed until the interpreter died.
    _seen = set(_seen or ())
    _seen.add(Path(export_dir).resolve())
    for base in base_plugins.export_dirs(export_dir):
        if Path(base).resolve() in _seen:
            continue
        paths |= load_grass_model_paths(base, _seen)
    paths |= _own_grass_model_paths(export_dir)
    return paths


def _own_grass_model_paths(export_dir):
    gras_txt = Path(export_dir) / 'GRAS.txt'
    paths = set()
    if not gras_txt.exists():
        return paths
    with open(gras_txt, encoding='utf-8') as f:
        for line in f:
            if line.startswith('Model.MODL='):
                p = line.strip().split('=', 1)[1].replace('\\\\', '\\')
                paths.add(p.lower())
    return paths


def _bake_geometry(geo, full):
    """Fold a local-to-root transform into one shape's vertices and normals."""
    gd = geo.data
    rot = full.get_matrix_33()
    if gd is not None:
        for v in gd.vertices:
            nv = v * full
            v.x, v.y, v.z = nv.x, nv.y, nv.z
        if getattr(gd, 'has_normals', 0):
            for n in gd.normals:
                nn = n * rot
                n.x, n.y, n.z = nn.x, nn.y, nn.z
        gd.update_center_radius()
    geo.rotation.set_identity()
    geo.translation.x = geo.translation.y = geo.translation.z = 0.0
    geo.scale = 1.0


def _grass_root(data):
    """The BSFadeNode a grass NIF hangs from, or None."""
    NifFormat = _nif_format()
    root = data.roots[0] if data.roots else None
    if root is None or not isinstance(root, NifFormat.BSFadeNode):
        return None
    return root if hasattr(root, 'children') else None


def _is_plain_wrapper(node):
    """A bare NiNode holding only geometry, safe to bake away."""
    NifFormat = _nif_format()
    if type(node).__name__ != 'NiNode':
        return False
    if node.collision_object is not None or node.controller is not None:
        return False
    if node.num_extra_data_list or not node.num_children:
        return False
    return all(isinstance(c, (NifFormat.NiTriShape, NifFormat.NiTriStrips))
               for c in node.children)


def _flatten_grass_root(data):
    """Collapse intermediate NiNodes so all geometry sits directly under the
    BSFadeNode root, the flat structure every working grass NIF uses.

    Skyrim's grass instancer expects grass geometry as a DIRECT child of the
    fade-node root; the generic converter wraps geometry in an inner NiNode
    whenever the source root is rotated, and the grass path dereferences a
    garbage pointer on that nesting. Each wrapper's transform is baked into
    its geometry and the empty node dropped. Returns True if the tree changed.
    See: docs/commentary/asset_convert_terrain.md#grass-conversion-record-invariants-shader
    """
    root = _grass_root(data)
    if root is None:
        return False
    new_children = []
    changed = False
    for child in list(root.children):
        if child is None:
            continue
        if not _is_plain_wrapper(child):
            new_children.append(child)
            continue
        wrap_m = child.get_transform()
        for geo in child.children:
            _bake_geometry(geo, geo.get_transform() * wrap_m)
            new_children.append(geo)
        changed = True
    if changed:
        root.num_children = len(new_children)
        root.children.update_size()
        for j, c in enumerate(new_children):
            root.children[j] = c
    return changed


def _plain_name(node):
    """A node's name without its padding."""
    return bytes(node.name).rstrip(b'\x00')


def _controllers_back_on_root(data):
    """Put the controllers `move_root_controller` moved back on a grass root.

    That rule leaves the geometry under an inner node, and the flattening
    keeps a node that carries a controller. The node is the one it added,
    `<root> Anim`, or on a turned root the wrapper's node: the root's only
    child, named like the root. Returns True if any went back.
    See: docs/commentary/asset_convert_nif.md#root-transform-controller
    """
    from asset_convert.nif.nif_passes import ROOT_MOVER_SUFFIX
    root = _grass_root(data)
    kids = [n for n in (root.children if root is not None else ()) if n is not None]
    names = {_plain_name(root) + ROOT_MOVER_SUFFIX} if kids else set()
    if len(kids) == 1:
        names.add(_plain_name(root))
    movers = [n for n in kids
              if type(n).__name__ == 'NiNode' and n.controller is not None
              and _plain_name(n) in names]
    for node in movers:
        chain, node.controller = node.controller, None
        last = root.controller
        while last is not None and last.next_controller is not None:
            last = last.next_controller
        if last is None:
            root.controller = chain
        else:
            last.next_controller = chain
        while chain is not None:
            chain.target, chain = root, chain.next_controller
    return bool(movers)


def _bake_shape_transforms(data):
    """Bake every root-level shape's own transform into its vertices.

    All 26 vanilla grass NIFs carry identity rotation and scale 1 on the shape;
    the instancer never applies a shape transform, so a rotated or scaled
    shape renders flat and oversized. Returns True if any shape changed.
    See: docs/commentary/asset_convert_terrain.md#grass-shape-transforms
    """
    NifFormat = _nif_format()
    root = _grass_root(data)
    if root is None:
        return False
    changed = False
    for geo in root.children:
        if not isinstance(geo, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            continue
        t = geo.translation
        if geo.rotation.is_identity() and geo.scale == 1.0                 and not (t.x or t.y or t.z):
            continue
        _bake_geometry(geo, geo.get_transform())
        changed = True
    return changed


def _strip_collision(data):
    """Drop the root's collision and BSXFlags; no vanilla grass carries either.

    A generic static conversion earns both, and the grass instancer draws the
    geometry itself. Returns True if anything was removed.
    See: docs/commentary/asset_convert_terrain.md#grass-shape-transforms
    """
    NifFormat = _nif_format()
    root = _grass_root(data)
    if root is None:
        return False
    changed = root.collision_object is not None
    root.collision_object = None
    keep = [e for e in root.extra_data_list
            if e is not None and not isinstance(e, NifFormat.BSXFlags)]
    if len(keep) != root.num_extra_data_list:
        root.num_extra_data_list = len(keep)
        root.extra_data_list.update_size()
        for i, e in enumerate(keep):
            root.extra_data_list[i] = e
        changed = True
    return changed


def apply_grass_profile(nif_path):
    """Apply the vanilla grass shape and shader profile to one converted NIF.

    Transforms are baked BEFORE the wind weights so the ramp runs up the
    blade's world height. Returns True if the file was modified.
    """
    NifFormat = _nif_format()
    data = NifFormat.Data()
    with open(nif_path, 'rb') as f:
        data.read(f)

    changed = _controllers_back_on_root(data)
    changed = _flatten_grass_root(data) or changed
    changed = _bake_shape_transforms(data) or changed
    changed = _strip_collision(data) or changed
    changed = add_wind_weights(data, NifFormat) or changed
    changed = _apply_shader_profile(data) or changed
    if changed:
        with open(nif_path, 'wb') as f:
            data.write(f)
    return changed


def _shader_scalars():
    """The vanilla grass scalar values, as (attribute, value) pairs."""
    return (('emissive_multiple', GRASS_EMISSIVE_MULT),
            ('glossiness', GRASS_GLOSSINESS),
            ('specular_strength', GRASS_SPECULAR_STRENGTH),
            ('lighting_effect_1', GRASS_LIGHTING_EFFECT_1),
            ('lighting_effect_2', GRASS_LIGHTING_EFFECT_2),
            ('texture_clamp_mode', GRASS_TEXTURE_CLAMP))


def _apply_lighting_shader(block):
    """Own_Emit, Vertex_Alpha and Vertex_Colors set, Specular clear.

    Gloss 0 plus the specular flag is pow(NdotH, 0) = 1.0, a white-out.
    Without Vertex_Colors the shader never reads the wind weight in alpha.
    """
    changed = False
    sf1 = block.shader_flags_1
    for flag, want in (('slsf_1_own_emit', 1), ('slsf_1_vertex_alpha', 1),
                       ('slsf_1_specular', 0)):
        if getattr(sf1, flag) != want:
            setattr(sf1, flag, want)
            changed = True
    sf2 = block.shader_flags_2
    if not sf2.slsf_2_vertex_colors:
        sf2.slsf_2_vertex_colors = 1
        changed = True
    for attr, value in _shader_scalars():
        if getattr(block, attr) != value:
            setattr(block, attr, value)
            changed = True
    spec = block.specular_color
    if (spec.r, spec.g, spec.b) != (1.0, 1.0, 1.0):
        spec.r = spec.g = spec.b = 1.0
        changed = True
    return changed


def _apply_alpha_profile(block):
    """Alpha-test only, thresholded into the vanilla grass envelope."""
    changed = False
    flags = int(block.flags)
    if flags & ALPHA_BLEND_BIT:
        block.flags = flags & ~ALPHA_BLEND_BIT
        changed = True
    if block.threshold > GRASS_MAX_ALPHA_THRESHOLD:
        block.threshold = GRASS_MAX_ALPHA_THRESHOLD
        changed = True
    return changed


def _apply_shader_profile(data):
    """Set the vanilla grass shader and alpha values on every block."""
    NifFormat = _nif_format()
    changed = False
    for block in data.blocks:
        if isinstance(block, NifFormat.BSLightingShaderProperty):
            changed = _apply_lighting_shader(block) or changed
        elif isinstance(block, NifFormat.NiAlphaProperty):
            changed = _apply_alpha_profile(block) or changed
    return changed


def _grass_source(rel, output_meshes_root, master_meshes_roots):
    """(converted NIF for `rel`, whether this plugin owns it), or (None, False)."""
    roots = [Path(output_meshes_root)] + [Path(r) for r in master_meshes_roots]
    for i, root in enumerate(roots):
        nif = win_join(root / current_namespace(), rel)
        if nif.exists():
            return nif, i == 0
    return None, False


def run(export_dir, output_meshes_root, master_meshes_roots=()):
    """Profile + place every GRAS model NIF; (processed, modified, missing).

    output_meshes_root is the plugin meshes root (e.g.
    output/Oblivion.esm/meshes): converted sources live under its namespace
    subtree, and profiled COPIES are placed at meshes\\landscape\\grass\\
    per grass_model_dest().  A model only a master converted is copied from
    its `master_meshes_roots` entry and profiled as the copy, leaving the
    master's mesh untouched; one a master already places is left to it.
    """
    output_meshes_root = Path(output_meshes_root)
    paths = load_grass_model_paths(export_dir)
    processed = modified = missing = 0
    for rel in sorted(paths):
        if any(win_join(Path(r), grass_model_dest(rel)).exists()
               for r in master_meshes_roots):
            continue
        nif, owned = _grass_source(rel, output_meshes_root, master_meshes_roots)
        if nif is None:
            missing += 1
            continue
        processed += 1
        # grass_model_dest() returns backslash-form (it doubles as the GRAS
        # MODL value written into the binary record -- see its docstring).
        dest = win_join(output_meshes_root, grass_model_dest(rel))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if owned and apply_grass_profile(nif):
            modified += 1
        shutil.copy2(nif, dest)
        if not owned and apply_grass_profile(dest):
            modified += 1
    return processed, modified, missing


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 1
    processed, modified, missing = run(argv[0], argv[1])
    print(f"Grass profile: {processed} NIFs processed, {modified} modified, "
          f"{missing} missing")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))

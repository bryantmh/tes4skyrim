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
    python -m asset_convert.grass_profile <export_dir> <output_meshes_root>
    # e.g. python -m asset_convert.grass_profile export/Oblivion.esm \
    #          output/Oblivion.esm/meshes
"""
import shutil
import struct
import sys
from pathlib import Path


def _nif_format():
    """Lazy pyffi import so tes5_import can use grass_model_dest() without
    pulling pyffi in."""
    from . import pyffi_monkey_patch as _patch  # noqa: F401 (precedes pyffi)
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

    Every working GRAS record on record (vanilla, USSEP, BSHeartland,
    Skyrim Extended Cut, Legacy Orsinium — 45/45 surveyed paths) keeps its
    model under ``meshes\\landscape\\grass\\``; nothing outside it is known
    to work.  Basenames are unique across all 97 TES4 grass models, so the
    tree is flattened with a ``tes4_`` prefix.
    """
    base = model_path.replace('/', '\\').rsplit('\\', 1)[-1].lower()
    return 'landscape\\grass\\tes4_' + base


def load_grass_model_paths(export_dir):
    """Return the set of GRAS Model.MODL paths (lowercase, backslash form)
    from an export directory's GRAS.txt."""
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


def apply_grass_profile(nif_path):
    """Apply the vanilla grass shader profile to one converted (LE) NIF.

    Returns True if the file was modified.
    """
    NifFormat = _nif_format()
    data = NifFormat.Data()
    with open(nif_path, 'rb') as f:
        data.read(f)

    changed = False
    for block in data.blocks:
        if isinstance(block, NifFormat.BSLightingShaderProperty):
            sf1 = block.shader_flags_1
            # Own_Emit + Vertex_Alpha set, Specular clear (gloss 0 +
            # specular flag = pow(NdotH, 0) = 1.0 white-out)
            if not sf1.slsf_1_own_emit:
                sf1.slsf_1_own_emit = 1
                changed = True
            if not sf1.slsf_1_vertex_alpha:
                sf1.slsf_1_vertex_alpha = 1
                changed = True
            if sf1.slsf_1_specular:
                sf1.slsf_1_specular = 0
                changed = True
            if block.emissive_multiple != GRASS_EMISSIVE_MULT:
                block.emissive_multiple = GRASS_EMISSIVE_MULT
                changed = True
            if block.glossiness != GRASS_GLOSSINESS:
                block.glossiness = GRASS_GLOSSINESS
                changed = True
            spec = block.specular_color
            if (spec.r, spec.g, spec.b) != (1.0, 1.0, 1.0):
                spec.r = spec.g = spec.b = 1.0
                changed = True
            if block.specular_strength != GRASS_SPECULAR_STRENGTH:
                block.specular_strength = GRASS_SPECULAR_STRENGTH
                changed = True
            if block.lighting_effect_1 != GRASS_LIGHTING_EFFECT_1:
                block.lighting_effect_1 = GRASS_LIGHTING_EFFECT_1
                changed = True
            if block.lighting_effect_2 != GRASS_LIGHTING_EFFECT_2:
                block.lighting_effect_2 = GRASS_LIGHTING_EFFECT_2
                changed = True
            if block.texture_clamp_mode != GRASS_TEXTURE_CLAMP:
                block.texture_clamp_mode = GRASS_TEXTURE_CLAMP
                changed = True
        elif isinstance(block, NifFormat.NiAlphaProperty):
            flags = int(block.flags)
            if flags & ALPHA_BLEND_BIT:
                block.flags = flags & ~ALPHA_BLEND_BIT
                changed = True
            if block.threshold > GRASS_MAX_ALPHA_THRESHOLD:
                block.threshold = GRASS_MAX_ALPHA_THRESHOLD
                changed = True

    if changed:
        with open(nif_path, 'wb') as f:
            data.write(f)
    return changed


def run(export_dir, output_meshes_root):
    """Profile + place every GRAS model NIF.

    output_meshes_root is the plugin meshes root (e.g.
    output/Oblivion.esm/meshes): converted sources live under its tes4/
    subtree, and profiled COPIES are placed at meshes\\landscape\\grass\\
    per grass_model_dest() (sources stay put — FLOR/STAT records may share
    them).  Returns (processed, modified, missing) counts.
    """
    output_meshes_root = Path(output_meshes_root)
    paths = load_grass_model_paths(export_dir)
    processed = modified = missing = 0
    for rel in sorted(paths):
        nif = output_meshes_root / 'tes4' / rel
        if not nif.exists():
            missing += 1
            continue
        processed += 1
        if apply_grass_profile(nif):
            modified += 1
        dest = output_meshes_root / grass_model_dest(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(nif, dest)
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

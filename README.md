# TES4-to-TES5 Conversion Project

Converts TES4 (Oblivion) master/plugin files to TES5 (Skyrim SE) format, including record data and game assets (meshes, textures, sounds).

## Requirements

- **Python 3.8+**
- **PyFFI** — NIF mesh reading/writing (`pip install PyFFI`)
- **numpy** — Numerical operations for skin retargeting (`pip install numpy`)
- **pytest** — Test runner (`pip install pytest`)

## Additional Credits

xEdit and all its contributors for esm record definitions
Nifscope for its information on nif formats
Zilav's Oblivion -> Skyrim xEdit conversion scripts for the original inspiration and lots of useful information
https://github.com/Ormin/skyblivion-NIFConverter for useful information and mopp_rl.exe
Sjors Boomschors for manual speed tree conversion models https://w.morroblivion.com/forums/conversion-to-skyrim/conversion-to-skyrim/2617
All the wonderful people I used to know on the Morroblivion forum, and those still working hard on Skyblivion and Skywind all these years later. You are an inspiration.

### Install all dependencies

```bash
pip install PyFFI numpy pytest
```

## Quick Start

### Full pipeline (all steps)

```bash
python convert.py -f Oblivion.esm
```

### Run only a specific step

```bash
python convert.py -f Oblivion.esm --export-only         # Export only
python convert.py -f Oblivion.esm --import-only         # Import only
python convert.py -f Oblivion.esm --lod-only            # LOD generation only
python convert.py --assets-only                         # Asset conversion only
python convert.py --modify-body-meshes                  # Run modify_body_meshes script only
python convert.py --verify-plugin                       # Run verify_plugin.py on output plugins only
```

### Mesh conversion only

```bash
python -m asset_convert.nif_converter path/to/meshes/ path/to/output/
```

### BSA extraction only

```bash
python -m asset_convert.bsa_extract Oblivion.esm --data-path "C:/path/to/Oblivion/Data"
```

### Run tests

```bash
python -m pytest tests/ -v
```

## Project Structure

```
TESConversion/
  convert.py            # Pipeline orchestrator (all phases, CLI)
  tes4_export/          # TES4 binary → KEY=VALUE text export
  tes5_import/          # KEY=VALUE text → TES5 binary import
  asset_convert/        # Asset conversion pipeline
    nif_converter.py    # NIF mesh conversion (strips→shapes, textures, bones, collision, skin retarget)
    collision.py        # Havok collision conversion (bhkNiTriStrips→bhkPackedNiTriStrips via MOPP_RL)
    skin_retarget.py    # Skeleton retargeting (Oblivion Bip01 → Skyrim NPC bones)
    bsa_extract.py      # BSA archive extraction with caching
    asset_pipeline.py   # Full extract → convert → output orchestrator
    MOPP_RL.exe         # Havok MOPP generation tool (self-contained)
    template.nif        # Template NIF required by MOPP_RL.exe
  tools/                # Debug/analysis utilities
  verify_plugin.py      # Plugin validation and integrity checker
  gui.py                # GUI frontend for the pipeline
  ...
```

## Pipeline Phases and CLI Arguments

- `--export-only`         Run only the export phase (TES4 binary → text)
- `--import-only`         Run only the import phase (text → TES5 binary)
- `--lod-only`            Run only the LOD generation phase
- `--assets-only`         Run only the asset conversion phase
- `--modify-body-meshes`  Run the modify_body_meshes script only
- `--verify-plugin`       Run verify_plugin.py on output plugins only
- `--workers N`           Number of worker processes for parallel steps

If no `--*-only` argument is given, the full pipeline is run.

## Tools and Scripts

- `asset_convert/nif_converter.py`      NIF mesh conversion (Oblivion → Skyrim)
- `asset_convert/bsa_extract.py`        BSA archive extraction
- `asset_convert/skin_retarget.py`      Skeleton retargeting for armor/clothes
- `asset_convert/modify_body_meshes.py` Add greaves partition to body mesh
- `verify_plugin.py`                    Plugin validation and integrity checker
- `tools/tes4_nif_analyzer.py`          NIF structure dump (Oblivion)
- `tools/tes5_nif_analyzer.py`          NIF structure dump (Skyrim)
- `tools/tes5_esm_reader.py`            TES5 ESM/ESP reader and dumper

## GUI

Run the GUI with:

```bash
python gui.py
```

The GUI allows you to select files, phases, and options interactively.

    tes4_nif_analyzer.py  # Dump NIF structure to text (python tools/tes4_nif_analyzer.py <nif_or_dir>)
    tes5_nif_analyzer.py  # Same for Skyrim NIFs
    tes5_esm_reader.py    # TES5 ESM/ESP reader and KEY=VALUE dumper
  tests/                # Test suite (pytest)
  export/               # Cached exports (gitignored)
  output/               # Final converted files (gitignored)
```

## What the mesh converter does

1. **NiTriStrips → NiTriShape** — Skyrim SE cannot render NiTriStrips geometry
2. **NiTexturingProperty → BSLightingShaderProperty** — Skyrim shader system conversion
3. **NiNode → BSFadeNode** — Skyrim's standard root node type
4. **Texture path rewriting** — Prepends `tes4\` to avoid conflicts with Skyrim's own textures
5. **Bone name remapping** — Renames Oblivion `Bip01` skeleton bones to Skyrim `NPC` naming
6. **Skin retargeting** — Deforms armor vertices from Oblivion T-pose to Skyrim rest pose using spatial Gaussian blending
7. **Havok collision conversion** — Converts bhkNiTriStripsShape to bhkPackedNiTriStripsShape with MOPP regeneration
8. **Root rotation baking** — Bakes non-identity root rotations into child transforms
9. **Furniture marker conversion** — BSFurnitureMarker → BSFurnitureMarkerNode
10. **NiParticleSystem conversion** — Updates particle data format for Skyrim

### Path filtering

The `SKIP_PATHS` set in `asset_convert/nif_converter.py` controls which path segments are skipped during batch conversion. By default `menus` and `creatures` are skipped.

## BSA extraction caching

Extracted BSAs are tracked via a manifest file (`.bsa_extract_manifest.json`). Rerunning the pipeline skips already-extracted archives unless `--force-extract` is used.

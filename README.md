# TES4-to-TES5 Conversion Project

Converts TES4 (Oblivion) master/plugin files to TES5 (Skyrim SE) format, including record data and game assets (meshes, textures, sounds).

## Requirements

- **Python 3.8+**
- **[PyFFI](https://pyffi.sourceforge.net/)** — NIF mesh reading/writing (`pip install PyFFI`)
- **[numpy](https://numpy.org/)** — Numerical operations for skin retargeting (`pip install numpy`)
- **[pytest](https://pytest.org/)** — Test runner (`pip install pytest`)
- **ffmpeg** — Used for voice audio conversion
- **xWMAEncode.exe** — Used for xWMA voice compression (see below)

> **xWMAEncode.exe** is part of the [Microsoft DirectX SDK (June 2010)](https://www.microsoft.com/en-us/download/details.aspx?id=6812) and cannot be redistributed. After installing the SDK, find it in `Utilities\bin\x86\` and place it in the project root directory. It can also be extracted from the SDK installer using 7-Zip without a full install.

## Credits

| Contributor | Contribution |
|-------------|-------------|
| [xEdit and all contributors](https://github.com/TES5Edit/TES5Edit) | ESM record definitions,  `BSArch.exe` for BSA packing, and `LODGenx64.exe` for object LOD generation |
| Zilav's Oblivion -> Skyrim xEdit conversion scripts for the original inspiration and lots of useful information |
| [NifSkope](https://github.com/niftools/nifskope) contributors | NIF format documentation |
| [Ormin](https://github.com/Ormin/skyblivion-NIFConverter) | Mesh conversion reference |
| [Ormin](https://github.com/Ormin/skyblivion-ScriptConverter) | Script converter reference for OBScript→Papyrus transpilation |
| [russo-2025](https://github.com/russo-2025/papyrus-compiler) | Papyrus Compiler |
| All the wonderful people I used to know on the Morroblivion forum, and those still working hard on Skyblivion and Skywind all these years later. You are an inspiration. |

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
python convert.py -f Oblivion.esm --export-only         # Export TES4 binary → text cache
python convert.py -f Oblivion.esm --import-only         # Build TES5 ESM/ESP from text cache
python convert.py -f Oblivion.esm --extract-only        # Pull assets from BSA archives
python convert.py -f Oblivion.esm --assets-only         # Convert NIFs/SPTs, copy textures & sounds
python convert.py -f Oblivion.esm --lod-only            # LOD mesh generation (slow)
python convert.py --modify-body-meshes                  # Add greaves partition to character body NIFs
```

### Custom output directory

```bash
python convert.py -f Oblivion.esm --output-dir C:/MyMods/Oblivion
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
  tools/                # Debug/analysis utilities
  gui.py                # GUI frontend for the pipeline
  ...
```

## Pipeline Phases and CLI Arguments

| Flag | Phase | Description |
|------|-------|-------------|
| `--export-only` | 1. Export | Parse TES4 binary → per-type text cache |
| `--import-only` | 2. Import | Build TES5 ESM/ESP from text cache |
| `--extract-only` | 3. Extract | Pull meshes/textures/sounds from BSA archives |
| `--assets-only` | 4. Assets | Convert NIFs/SPTs, copy textures & sounds |
| `--lod-only` | 5. LOD | Generate object & terrain LOD meshes |
| `--modify-body-meshes` | 6. Body | Add greaves partition to character body NIFs |

Other flags:
- `-f PLUGIN`            Plugin filename (e.g. `Oblivion.esm`, `Knights.esp`)
- `--output-dir PATH`    Override the output directory (default: `output/`)

If no `--*-only` argument is given, the default pipeline runs: **Export → Import → Extract → Assets**.

## Tools and Scripts

- `asset_convert/nif_converter.py`      NIF mesh conversion (Oblivion → Skyrim)
- `asset_convert/bsa_extract.py`        BSA archive extraction
- `asset_convert/skin_retarget.py`      Skeleton retargeting for armor/clothes
- `asset_convert/modify_body_meshes.py` Add greaves partition to body mesh
- `tools/tes4_nif_analyzer.py`          NIF structure dump (Oblivion)
- `tools/tes5_nif_analyzer.py`          NIF structure dump (Skyrim)
- `tools/tes5_esm_reader.py`            TES5 ESM/ESP reader and dumper

## GUI

Run the GUI with:

```bash
python gui.py
```

Features:
- Auto-detects Oblivion data directory from the Windows registry
- Scans the configured data directory for all `.esm` / `.esp` plugins
- Configurable output directory (saved to `conversion_config.json`)
- Per-step checkboxes with **All** / **Default** shortcuts
- Real-time streaming log output

## Packaging (Standalone Executable)

Build a single self-contained executable with:

```bash
python compile.py
```

Outputs `dist/TESConverter/TESConverter.exe` (plus bundled dependencies). Requires [PyInstaller](https://pyinstaller.org) (`pip install pyinstaller`).

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

Extracted BSAs are tracked via a manifest file (`.bsa_extract_manifest.json`). Rerunning the pipeline skips already-extracted archives unless the extract step is forced by deleting the manifest.

## License

This project is released under the MIT License.

The following components have separate licensing:

| Component | License | Notes |
|-----------|---------|-------|
| `xWMAEncode.exe` | Microsoft (not redistributed) | Obtain separately from the DirectX SDK |

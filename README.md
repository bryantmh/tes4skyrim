<p align="center">
  <img src="docs/banner.svg" alt="TES4 Skyrim — Oblivion to Skyrim Conversion" width="720">
</p>

<p align="center">
  A complete pipeline for converting <b>TES4 (Oblivion)</b> master and plugin files into
  <b>TES5 (Skyrim)</b> format — records, meshes, textures, collision,
  animations, sounds, dialogue, and scripts.
</p>

---

## What it does

This is a full data-conversion pipeline. It takes an
Oblivion `.esm`/`.esp` (plus its BSA assets) and produces a Skyrim plugin with its
assets ready to drop into your `Data` folder. Currently in early alpha stage with many bugs to work out, but it is already more fully featured than any other tools in existence.

- **Record conversion** — Every TES4 record type is remapped to its TES5 equivalent
  (`CREA`→`NPC_`, `CLOT`→`ARMO`, `LVLC`→`LVLN`, …), with all the structural fixups Skyrim
  requires,
  and companion records (`ARMA`, `TXST`, `SNDR`, `VTYP`, …).
- **Navmesh** — Oblivion Pathgrids (`PGRD`), along with cell and mesh information are used to automatically generate Navmeshes to allow NPC navigation are triangulated into Skyrim navmeshes. This still needs a little refinement, but its a good start
- **Mesh conversion** — Oblivion NIFs → Skyrim NIFs (v20.2.0.7):
  NiTriStrips→NiTriShape, shader system upgrade, texture path rewriting, bone remapping,
  and root-node conversion. Only a few base game meshes are currently unsupported
- **Havok collision** — Full rigid-body, constraint, and mesh-collision conversion with
  real MOPP generation via the bundled Havok bridge. And no crash-causing collision like the original Skyblivion collision generator
- **Skeleton retargeting** — Armor and clothing meshes are re-posed from the Oblivion
  skeleton to the Skyrim skeleton using an animation-corpus + optimization solver. WIP, but all weapons, armor, and clothing are fully functional, including pants/greaves. Due to the differences between skeletons, legs will currently turn invisible with a torso equipped, and there is quite a bit of clipping that needs to be solved. But this is the first automated converter of its kind
- **Particles, fire & animated objects** — Particle systems, flame nodes, flip-book
  fire, and keyframed collision are all converted to their Skyrim equivalents. Also a first for automated conversion
- **SpeedTree conversion** — Oblivion `.spt` trees are procedurally rebuilt as Skyrim
  flora NIFs, one per `TREE` record. They aren't exactly 1-1, but they are fairly convincing replicas. No conversion process for this existed before this tool
- **Dialogue & quests** — `DIAL`/`INFO`/`QUST` converted into Skyrim's branch/voice-type
  architecture (`DLBR`, `DLVW`, `VTYP`), including voice-file renaming. WIP, with some things like greetings missing
- **Scripts** — Oblivion scripts are transpiled to Papyrus (`.psc`) source and compiled. WIP, but this along with dialog means some quests are at least partially functional.
- **Sounds** — Voice and sound files are converted (via ffmpeg + xWMAEncode). You may run into oddities like rats speaking like Wes Johnson, but you have access to most dialog topics and the correct voices. Lip syncing not included yet

In short, this project aims to be nothing less than comprehensive and has a laundry list of bugs, but its getting better all the time. I'd love to accept any contributions as PRs. See TODO.txt for known issues / roadmap

---

## Requirements

| Dependency | Purpose | Install |
|------------|---------|---------|
| **Python 3.8+** | Runs the whole pipeline | — |
| **[PyFFI](https://pyffi.sourceforge.net/)** | NIF mesh reading/writing | `pip install PyFFI` |
| **[numpy](https://numpy.org/)** | Skin retargeting math | `pip install numpy` |
| **[scipy](https://scipy.org/)** | Navmesh triangulation, collision hulls, trees | `pip install scipy` |
| **[pytest](https://pytest.org/)** | Test runner | `pip install pytest` |
| **ffmpeg** | Voice/sound audio conversion | On `PATH` |
| **xWMAEncode.exe** | xWMA voice compression | See note below |

```bash
pip install PyFFI numpy scipy pytest
```

> **xWMAEncode.exe** ships with the [Microsoft DirectX SDK (June 2010)](https://www.microsoft.com/en-us/download/details.aspx?id=6812)
> and cannot be redistributed. After installing the SDK, find it in `Utilities\bin\x86\`
> and copy it to the project root. (You can also extract it from the SDK installer with
> 7-Zip without a full install.)

---

## Quick start

The easiest way to run a conversion is the GUI. Either double click gui.pyw or in the terminal:

```bash
python gui.py
```

The GUI:

- Auto-detects your Oblivion data directory from the Windows registry
- Scans it for all `.esm` / `.esp` plugins
- Lets you pick an output directory (saved to `conversion_config.json`)
- Offers per-step checkboxes with **All** / **Default** shortcuts
- Streams the pipeline log live

Or run the full pipeline from the command line:

```bash
python convert.py -f Oblivion.esm
```

The output plugin and assets are written to `output/` (override with `--output-dir`).

---

## Command line

### Run the full pipeline

```bash
python convert.py -f Oblivion.esm
```

With no `--*-only` flag, the default pipeline runs:

> **Export → Extract → Meshes → SpeedTrees → Import → Sounds → Scripts**

### Run a single step

Each `--*-only` flag runs exactly that step and nothing else:

```bash
python convert.py -f Oblivion.esm --export-only        # Parse TES4 binary → text cache
python convert.py -f Oblivion.esm --import-only        # Build TES5 ESM/ESP from text cache
python convert.py -f Oblivion.esm --extract-only       # Extract assets from BSA archives
python convert.py -f Oblivion.esm --meshes-only        # Convert NIFs + copy textures
python convert.py -f Oblivion.esm --speedtrees-only    # Convert SpeedTree (.spt) files
python convert.py -f Oblivion.esm --sounds-only        # Copy/convert sound files
python convert.py -f Oblivion.esm --scripts-only       # Transpile scripts → Papyrus
python convert.py -f Oblivion.esm --lod-only           # Generate object & terrain LOD (slow)
python convert.py -f Oblivion.esm --pack-only          # Pack output assets into Skyrim BSAs
python convert.py -f Oblivion.esm --modify-body-meshes # Add greaves partition to body NIFs
python convert.py -f Oblivion.esm --mesh-bounds-only   # Rescan mesh bounds → OBND cache
```

### Common options

| Flag | Description |
|------|-------------|
| `-f, --files FILE…` | Plugin(s) to process (default: all listed in the config) |
| `--output-dir PATH` | Output directory (default: `output/`) |
| `--config PATH` | Path to `conversion_config.json` |
| `--mesh-subdirs SUB…` | Limit mesh conversion to specific root subfolders (e.g. `architecture clutter`) |

### Running individual tools directly

```bash
# Mesh conversion only
python -m asset_convert.nif_converter path/to/meshes/ path/to/output/

# BSA extraction only
python -m asset_convert.bsa_extract Oblivion.esm --data-path "C:/path/to/Oblivion/Data"

# Tests
python -m pytest tests/ -v
```

---

## Pipeline phases

| # | Phase | What happens |
|---|-------|--------------|
| 1 | **Export** | Parse the TES4 binary into a per-record-type KEY=VALUE text cache (`export/<name>/`). A pure dump — no transformation. |
| 2 | **Extract** | Pull meshes, textures, and sounds out of the Oblivion BSA archives (cached via a manifest). |
| 3 | **Meshes** | Convert Oblivion NIFs → Skyrim NIFs and copy textures. |
| 4 | **SpeedTrees** | Procedurally rebuild `.spt` trees as Skyrim flora NIFs. |
| 5 | **Import** | Read the text cache and write the TES5 binary ESM/ESP — all record transformations happen here. |
| 6 | **Sounds** | Convert voice and sound files to Skyrim formats. |
| 7 | **Scripts** | Transpile Oblivion scripts to Papyrus and compile. |
| — | **LOD** | *(opt-in)* Generate object and terrain LOD meshes. |
| — | **Pack** | *(opt-in)* Pack the converted assets into Skyrim BSA archives. |

> **Design principle:** the export is a *pure* dump of TES4 data — no type mapping, no path
> prefixing, no derived fields. **All** transformations live in the import and asset steps.

---

## Project structure

```
TESConversion/
├── convert.py            # Pipeline orchestrator (all phases, CLI)
├── gui.py                # GUI frontend
├── tes4_export/          # TES4 binary → KEY=VALUE text export
├── tes5_import/          # KEY=VALUE text → TES5 binary import (all record transforms)
├── asset_convert/        # Asset conversion pipeline
│   ├── nif_converter.py  #   NIF mesh conversion (strips, shaders, bones, collision, skin)
│   ├── collision.py      #   Havok collision conversion
│   ├── cms_builder.py    #   Compressed-mesh collision + MOPP generation
│   ├── skin_retarget.py  #   Oblivion → Skyrim skeleton retargeting
│   ├── spt_converter.py  #   SpeedTree (.spt) → Skyrim flora NIF
│   ├── bsa_extract.py    #   BSA extraction with caching
│   └── asset_pipeline.py #   Extract → convert → output orchestrator
├── tools/                # Debug/analysis utilities (NIF/ESM dumpers, sanity checkers)
├── tests/                # Pytest suite
├── docs/                 # Format notes and reference docs
└── conversion_config.json
```

---

## Credits

| Contributor | Contribution |
|-------------|--------------|
| [xEdit and all contributors](https://github.com/TES5Edit/TES5Edit) | ESM record definitions; `BSArch.exe` (BSA packing) and `LODGenx64.exe` (object LOD) |
| Zilav's Oblivion → Skyrim xEdit conversion scripts | The original inspiration and the information I needed to get started |
| [NifSkope](https://github.com/niftools/nifskope) contributors | NIF format documentation |
| [Ormin — NIFConverter](https://github.com/Ormin/skyblivion-NIFConverter) | Mesh conversion reference |
| [Ormin — ScriptConverter](https://github.com/Ormin/skyblivion-ScriptConverter) | OBScript → Papyrus transpilation reference |
| [russo-2025](https://github.com/russo-2025/papyrus-compiler) | Papyrus compiler |
| [LvxMagick](https://www.nexusmods.com/skyrimspecialedition/mods/183399) | Mopp Bridge |

And finally to all the wonderful people I used to know on the Morroblivion forum, and those still working hard on Skyblivion and Skywind all these years later. You are an inspiration
---

## License

This project is released under the **MIT License**.

Some components carry separate licensing:

| Component | License / Terms |
|-----------|-----------------|
| `xWMAEncode.exe` | Microsoft (not redistributed) — obtain from the DirectX SDK |
| Oblivion banner font | The [Oblivion font](https://www.dafont.com/oblivion.font) by mistic100 is *free for personal use only* and based on Bethesda's trademarked logo. It is **not** bundled in this repo; the banner ships as pre-rendered vector outlines. |

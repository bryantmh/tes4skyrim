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

This is a full data-conversion pipeline. It takes an Oblivion `.esm`/`.esp` (plus its BSA or lose file assets) and produces a working Skyrim mod. Plugin, meshes, animations, everything. Ready to drop into your `Data` folder. It's in early alpha with a long bug list, but each individual part is already more fully featured than most equivalent tools and is self-contained all in one package.

- **Record conversion** — Every TES4 record type is remapped to its TES5 equivalent
  (`CREA`→`NPC_`, `CLOT`→`ARMO`, `LVLC`→`LVLN`, …), with all the structural fixups Skyrim
  requires and the companion records it expects alongside them (`ARMA`, `TXST`, `SNDR`, `VTYP`, …).
- **Creatures** — Oblivion's `CREA` records become real Skyrim actors: skeletons and skinned meshes are ported, animations are recompiled to Havok `.hkx`, and, since Oblivion has no equivalent, a full Havok **behavior graph is generated from scratch** per creature (locomotion state machine, ragdoll, attack events, foot IK) based on Oblivion data instead of reusing a donor from Skyrim. There are a lot of bugs here, but nothing like this has been done before.
- **Mesh conversion** — Oblivion NIFs → Skyrim NIFs (v20.2.0.7): NiTriStrips→NiTriShape, shader system upgrade, texture path rewriting, bone remapping, and root-node conversion. Only a handful of base-game meshes are currently unsupported.
- **Havok collision** — Full rigid-body, constraint, and mesh-collision conversion with real MOPP generation via a bundled Havok bridge. No crash-prone collision like the original Skyblivion generator.
- **Skeleton retargeting** — Armor and clothing meshes are re-posed from the Oblivion
  skeleton onto the Skyrim skeleton using an animation-corpus + optimization solver. Weapons, armor, and clothing (including pants/greaves) are fully functional and wearable alongside your existin Skyrim outfits without major clipping — a few meshes still clip slightly, and the torso/legs can currently go invisible when only one is equipped.
- **Navmesh generation** — Skyrim has no equivalent to Oblivion's pathgrids, so navmeshes are built from scratch:  Collision is voxelized and triangulated per-cell into Skyrim `NAVM`/`NAVI` data using the original Oblivion pathgrid as a guide so NPCs can actually path around the world. Still needs a lot of refinement, but it mostly works.
- **Particles, fire & animated objects** — Particle systems, flame nodes, flip-book fire, and keyframed collision are all converted to their Skyrim equivalents.
- **SpeedTree conversion** — Oblivion `.spt` trees are procedurally rebuilt as Skyrim flora NIFs, one per `TREE` record. They aren't exactly 1-1, but they are fairly convincing replicas with proper collision and wind sway.
- **Dialogue & quests** — `DIAL`/`INFO`/`QUST` converted into Skyrim's branch/voice-type architecture (`DLBR`, `DLVW`, `VTYP`), including voice-file renaming, topic/quest restructuring so NPCs greet and respond correctly, and barter/training menu hookup. Like everything else, it's still a work in progress.
- **Scripts** — Oblivion scripts are transpiled to Papyrus (`.psc`) source and compiled,
  which combined with the dialogue work means some quests are already partially playable.
- **Sounds** — Voice and sound files are converted (via ffmpeg + xWMAEncode), and lip-sync
  tracks are generated for every transcribed line using the Creation Kit's LipGenerator
  (voice ships as `.fuz`). Expect the occasional silent line.

In short, this project aims to be nothing less than a complete, faithful Oblivion→Skyrim
conversion, and it's getting closer all the time. Contributions are very welcome — see
`TODO.txt` for the current bug list and roadmap.

<p align="center">
  <img src="docs/readme_img.png" alt="An Oblivion Vista" width="720">
</p>

---

## Requirements

A decent PC. The more cores and ram the better. The more cores, the more ram it uses. Validated with a 7950X3D and 32GB of system ram (uses a peak of ~11 GB RAM converting Oblivion.esm)

| Dependency | Purpose | Install |
|------------|---------|---------|
| **Python 3.8+** | Runs the whole pipeline | — |
| **[PyFFI](https://pyffi.sourceforge.net/)** | NIF mesh reading/writing | `pip install PyFFI` |
| **[numpy](https://numpy.org/)** | Skin retargeting math | `pip install numpy` |
| **[scipy](https://scipy.org/)** | Navmesh triangulation, collision hulls, trees | `pip install scipy` |
| **[pytest](https://pytest.org/)** | Test runner | `pip install pytest` |
| **ffmpeg** | Voice/sound audio conversion | On `PATH` |
| **xWMAEncode.exe** | xWMA voice compression | See note below |
| **LipGenerator.exe** | Lip sync generation | Install the Creation Kit |

```bash
pip install PyFFI numpy scipy pytest
```

> **xWMAEncode.exe** ships with the [Microsoft DirectX SDK (June 2010)](https://www.microsoft.com/en-us/download/details.aspx?id=6812)
> and cannot be redistributed. After installing the SDK, find it in `Utilities\bin\x86\`
> and copy it to `external/xwmaencode/`. (You can also extract it from the SDK installer with
> 7-Zip without a full install.)

You also need to install the Skyrim SE Creation Kit from Steam (free)

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


Once installed and loaded up ingame, begin "Oblivion" with 

```bash
setstage Charactergen 5
```
or simply teleport to the worldspace with a command like

```bash
cow tes4tamriel 20 20
```

If you'd like to use any modded Oblivion assets such as models or textures, first complete the "extract" step and then place your modded assets in the export/"plugin you are trying to convert" directory and overwrite

---

## Command line

### Run the full pipeline

```bash
python convert.py -f Oblivion.esm
```

With no `--*-only` flag, the default pipeline runs:

> **Export → Extract → Meshes → SpeedTrees → Creatures → Import → Sounds → Scripts**

### Run a single step

Each `--*-only` flag runs exactly that step and nothing else:

```bash
python convert.py -f Oblivion.esm --export-only        # Parse TES4 binary → text cache
python convert.py -f Oblivion.esm --import-only        # Build TES5 ESM/ESP from text cache
python convert.py -f Oblivion.esm --extract-only       # Extract assets from BSA archives
python convert.py -f Oblivion.esm --meshes-only        # Convert NIFs + copy textures
python convert.py -f Oblivion.esm --speedtrees-only    # Convert SpeedTree (.spt) files
python convert.py -f Oblivion.esm --creatures-only     # Convert creature models & animations
python convert.py -f Oblivion.esm --sounds-only        # Copy/convert sound files
python convert.py -f Oblivion.esm --scripts-only       # Transpile scripts → Papyrus
python convert.py -f Oblivion.esm --lod-only           # Generate object & terrain LOD (slow)
python convert.py -f Oblivion.esm --pack-only          # Pack output assets into Skyrim BSAs
python convert.py -f Oblivion.esm --modify-body-meshes # Build ARMA slot-44 patch for your load order
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

These are the steps as presented (and run) by the GUI, in order:

| # | Phase | What happens |
|---|-------|--------------|
| 1 | **Export** | Parse the TES4 binary into a per-record-type KEY=VALUE text cache (`export/<name>/`). A pure dump — no transformation. |
| 2 | **Extract** | Pull meshes, textures, and sounds out of the Oblivion BSA archives (cached via a manifest). |
| 3 | **Meshes** | Convert Oblivion NIFs → Skyrim NIFs and copy textures. |
| 4 | **SpeedTrees** | Procedurally rebuild `.spt` trees as Skyrim flora NIFs. |
| 5 | **Creatures** | Convert creature models and animations (skeletons, ragdolls, behavior graphs). |
| 6 | **Import** | Read the text cache and write the TES5 binary ESM/ESP — all record transformations happen here. |
| 7 | **Sounds** | Convert voice files to XWM and copy sound files. |
| 8 | **Scripts** | Transpile Oblivion scripts to Papyrus and compile. |
| 9 | **LOD** | *(opt-in, off by default)* Generate object and terrain LOD meshes. |
| 10 | **Pack BSAs** | *(opt-in, off by default)* Pack the converted assets into Skyrim BSA archives. |
| 11 | **Patch Skyrim** | Build the ARMA slot-44 body patch for your load order. |
| 12 | **Pack Mod Zip** | Zip the plugin(s) and BSAs into a single archive for installation. |

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
├── external/             # Third-party binaries & vendored code (see License below)
├── tools/                # Debug/analysis utilities (NIF/ESM dumpers, sanity checkers)
├── tests/                # Pytest suite
├── docs/                 # Format notes and reference docs
└── conversion_config.json
```

---

## Credits & License

First things first, I want to give credit to all the wonderful people I used to know on the Morroblivion forum, and those still working hard on Skyblivion and Skywind all these years later. You are an inspiration.

This project's own code is released under the **MIT License**. Everything under
`external/` is third-party and carries its own licensing — nothing in that folder is
covered by this project's MIT license.

| Contributor / Component | Used for | License / Terms |
|--------------------------|----------|-----------------|
| [xEdit and all contributors](https://github.com/TES5Edit/TES5Edit) | ESM record definitions; `BSArch.exe` (BSA packing) and `LODGenx64.exe` (object LOD) in `external/` | MPL-2.0 / GPL-2.0 (xEdit dual license) — redistributed |
| Zilav's Oblivion → Skyrim xEdit conversion scripts | The original inspiration and the information needed to get started | — (reference only, not redistributed) |
| [NifSkope](https://github.com/niftools/nifskope) contributors | NIF format documentation | — (reference only, not redistributed) |
| [Ormin — NIFConverter](https://github.com/Ormin/skyblivion-NIFConverter) | Mesh conversion reference | — (reference only, not redistributed) |
| [Ormin — ScriptConverter](https://github.com/Ormin/skyblivion-ScriptConverter) | OBScript → Papyrus transpilation reference | — (reference only, not redistributed) |
| [russo-2025 — papyrus-compiler](https://github.com/russo-2025/papyrus-compiler) | Papyrus compiler, `external/papyrus-compiler/papyrus.exe` | **MIT** (© 2025 russo-2025) — redistributed; license text in `external/papyrus-compiler/LICENSE` |
| [LvxMagick — DovahNifWorkbench](https://www.nexusmods.com/skyrimspecialedition/mods/183399) | Mopp Bridge, `external/mopp_bridge/dovah_hkp_mesh_mopp_bridge.exe` | No stated license (Nexus-only, no public source). **Statically links Havok** — see the Havok note below |
| [Bad Dog — PyNifly](https://github.com/BadDogSkyrim/PyNifly) | Pure-Python Havok hk_2010 packfile reader + hkaSplineCompressedAnimation codec (vendored in `external/pynifly_hkx/`) — the heart of creature animation conversion | **GPL-3.0** (vendored from PyNifly 27.4.0; local changes marked `# TESConversion:`) — see the GPL note below |
| [figment — hkxcmd](https://github.com/figment/hkxcmd) | Havok packfile XML↔binary compiler, `external/hkxcmd/hkxcmd.exe`, used to build skeleton/behavior/animation `.hkx` | **BSD-3-Clause** for hkxcmd's own sources (© 2011; text in `external/hkxcmd/LICENSE.TXT`). **Statically links Havok** — see the Havok note below |
| [Microsoft DirectX SDK (June 2010)](https://www.microsoft.com/en-us/download/details.aspx?id=6812) | `external/xwmaencode/xWMAEncode.exe`, xWMA voice compression | Microsoft — **not redistributed**; obtain from the SDK (see [Requirements](#requirements)) |
| Oblivion banner font ([dafont](https://www.dafont.com/oblivion.font)) | Project banner | *Free for personal use only*, based on Bethesda's trademarked logo. **Not** bundled in this repo; the banner ships as pre-rendered vector outlines. |

> **Note on GPL-3.0:** `external/pynifly_hkx/` is GPL-3.0. It is used by the creature
> animation conversion path (`asset_convert/hkx_anim.py`). If you redistribute a build
> that includes it, the GPL's terms apply to that distribution.

> ### ⚠️ Note on Havok
>
> Two bundled binaries — `hkxcmd.exe` and `dovah_hkp_mesh_mopp_bridge.exe` — statically
> link the **proprietary Havok SDK**. Both embed the notice:
>
> > *Copyright 1999-2011 Havok.com Inc. (and its Licensors). All Rights Reserved.
> > See www.havok.com for details.*
>
> **Neither author's license grants any rights to the Havok code inside.** These two
> binaries are redistributed here as-is.


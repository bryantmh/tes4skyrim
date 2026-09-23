<p align="center">
  <img src="docs/assets/banner.svg" alt="TES Auto-Convert — Gamebryo to Skyrim Conversion" width="720">
</p>

<p align="center">
  A complete pipeline for converting <b>Morrowind</b>, <b>Oblivion</b>,
  <b>Fallout 3</b> and <b>Fallout: New Vegas</b> into
  <b>Skyrim Special Edition</b>: records, meshes, textures, collision,
  animations, sounds, dialogue, and scripts.
</p>

---

**This project is pre-alpha. Expect issues.** Conversions are incomplete, and some features are only half implemented. Bugs are common across every stage, and output can change or break between releases. Updates will likely break your saves from converted games, so expect to start a new game after updating. Back up your saves before installing converted content.

---

> [!IMPORTANT]
> **No Bethesda assets are included in this repository.** This project ships only
> code. It reads the game data you already own, on your own machine,
> and performs every conversion locally. **Do not redistribute converted Bethesda
> assets.** If you want the converted mod, download this project and run it against
> your own legally-owned copies of the games.

---

## What it does

This is a full data-conversion pipeline. It takes an `.esm`/`.esp` (plus its BSA or loose file assets) and produces a working Skyrim mod. Plugin, meshes, animations, everything. Ready to drop into your `Data` folder. It's in early alpha with a long bug list, but every part of the conversion is handled in one package.

### Which games

Four source games are supported: **Morrowind**, **Oblivion**, **Fallout 3** and **Fallout: New Vegas**. Total conversions such as **Nehrim** are also supported.

Morrowind is the odd one out and has [its own section](#morrowind) below.

- **Record conversion** — Every record type is remapped to its Skyrim equivalent, with all the structural fixups Skyrim
  requires and the companion records it expects alongside them.
- **Creatures** — Creature records become real Skyrim actors: skeletons and skinned meshes are ported, animations are recompiled to Havok `.hkx`, and, since the source games have no equivalent, a full Havok **behavior graph is generated from scratch** per creature (locomotion state machine, ragdoll, attack events, foot IK) based on the original data instead of reusing a donor from Skyrim. There are still a lot of bugs here.
- **Mesh conversion** — Source NIFs → Skyrim NIFs (v20.2.0.7): NiTriStrips→NiTriShape, shader system upgrade, texture path rewriting, bone remapping, and root-node conversion. Only a handful of meshes are currently unsupported or buggy.
- **Havok collision** — Full rigid-body, constraint, and mesh-collision conversion with real MOPP generation via a bundled Havok bridge. No crash-prone collision like the original Skyblivion generator.
- **Skeleton retargeting** — Armor and clothing meshes are re-posed from the source
  skeleton onto the Skyrim skeleton using an animation-corpus + optimization solver. Weapons, armor, and clothing (including pants/greaves) are fully functional and wearable alongside your existing Skyrim outfits without major clipping — a few meshes still clip slightly, and the torso/legs can currently go invisible when only one is equipped.
- **Navmesh generation** — Skyrim has no equivalent to the older games' pathgrids, so navmeshes are built from scratch:  Collision is voxelized and triangulated per-cell into Skyrim `NAVM`/`NAVI` data using the original pathgrid as a guide so NPCs can actually path around the world. Could use a bit more refinement, but it mostly works well.
- **Particles, fire & animated objects** — Particle systems, flame nodes, flip-book fire, and keyframed collision are all converted to their Skyrim equivalents.
- **SpeedTree conversion** — `.spt` trees are procedurally rebuilt as Skyrim flora NIFs. The converter hooks directly into your Oblivion.exe so they are faithful replicas, but with leaves in an X shape instead of billboards.
- **Dialogue & quests** — Converted into Skyrim's branch/voice-type architecture, including voice-file renaming, topic/quest restructuring so NPCs greet and respond correctly, and barter/training menu hookup.
- **Scripts** — Source scripts are transpiled to Papyrus (`.psc`) source and compiled,
  which combined with the dialogue work means many quests are already playable.
- **Guns (Fallout)** — Guns get a purpose-built animation graph using faithfully converted animations.
- **Sounds** — Voice and sound files are converted (via ffmpeg + xWMAEncode), and lip-sync
  tracks are generated for every transcribed line using the Creation Kit's LipGenerator
  (voice ships as `.fuz`). Expect the occasional silent line.

In short, this project aims to be nothing less than a complete, faithful conversion of these
games into Skyrim, and it's getting closer all the time. Contributions are very welcome, see
`TODO.txt` for the current bug list and roadmap.

---

## Morrowind

Morrowind converts from its own files like any other game here, with two differences.

**Dialogue runs natively.** Morrowind's conversation system is a searchable topic list with
its own filtering, journal and scripting language, none of which Skyrim can represent.
Rather than flatten it and lose most of it, this project runs the real thing: a port of the
[OpenMW](https://openmw.org/) dialogue engine and script interpreter, shipped as a game
plugin (`MorrowindRuntime.dll`) with a Morrowind-style menu. It is set up automatically.

**Morroblivion mode.** The converter can be made to use Morroblivion as a base instead of Morrowind so you can use all of its upgraded assets and worldspace.

| Mode | Objects come from |
|---|---|
| **Vanilla** | Your own converted `Morrowind.esm`, `Tribunal.esm` and `Bloodmoon.esm`. |
| **Morroblivion + patch** | [Morroblivion](https://morroblivion.com/), the fan remake of Morrowind in Oblivion, converted through the normal Oblivion path. |

Morroblivion mode needs a compatibility patch, which the same menu builds for you from your Morrowind `Data Files`.

On the command line:

```bash
python convert.py --build-morrowind-patch "C:\path\to\Morrowind\Data Files"
```
<p align="center">
  <img src="docs/assets/readme_img.png" alt="An Oblivion Vista" width="720">
</p>

---

## Requirements

A decent PC. More cores make it faster, but each core also uses more RAM. Tested on a 7950X3D with 32 GB of RAM (converting Oblivion.esm peaks at about 16 GB).

You need four things:

1. **Python 3.14.** Use exactly 3.14. Other versions need you to compile the navmesh module yourself (see `native/dist/README.md`).
2. **The Python packages.** Open PowerShell and paste:

   ```bash
   pip install PyFFI numpy scipy shapely Pillow lz4 mapbox_earcut setuptools
   ```

   Optionally add `tkinterdnd2` to drag mod archives onto the GUI.
3. **The Skyrim SE Creation Kit**, free on Steam. It provides lip sync and the files scripts are compiled against.
4. **xWMAEncode.exe**, for voice files. See the note below.

> **xWMAEncode.exe** ships with the [Microsoft DirectX SDK (June 2010)](https://www.microsoft.com/en-us/download/details.aspx?id=6812)
> and cannot be redistributed. You should extract it from the SDK installer using 7-zip to avoid having to do a full install.
> Then, copy it to `external/xwmaencode/`. (If you choose to install the SDK instead, find it in `Utilities\bin\x86\`.)

If anything is missing, the conversion stops and tells you what to install.
Run `python preflight.py` to check without starting a conversion.

---

## Quick start

The easiest way to run a conversion is the GUI. Either double click gui.pyw or in the terminal:

```bash
python gui.py
```

Or run the full conversion from the command line:

```bash
python convert.py -f Oblivion.esm
```

In the GUI:

- Your Oblivion install is found automatically. Add Morrowind and Fallout installs
  with `+` under **Source**.
- Pick a plugin, tick the steps you want (**Default** is the usual choice), and run.
- **Tools ▸ Check Dependencies** tells you what's missing before you start.
- **Tools ▸ Patch Quest Journal** makes a quest's objectives clickable in Skyrim's journal: clicking one
  shows the journal text the quest had when that objective appeared, and clicking it again returns to the
  current text. It works for every quest, converted or not, and patches whichever journal you run (vanilla,
  SkyUI or Quest Journal Overhaul), keeping the original beside it as `.mwrt-backup`. Needs
  **MorrowindRuntime.dll**, and covers objectives that appear after it is installed.

If you'd like to use modded source-game models or textures, run the **Extract** step
first, then copy your modded files into `export/<plugin name>/`, overwriting what's there.

The Import step downloads a [prebuilt navmesh cache](https://github.com/bryantmh/tes4skyrim/releases)
automatically, which turns minutes of work into seconds. It only saves time and
never changes the result. To skip the download, untick **Settings ▸ Download
navmesh cache**. Offline, drop the release's `.zip` into `navmesh_cache/`.

### Converting a downloaded mod

Drag a `.zip` / `.7z` / `.rar` onto the left panel, or use **Mods ▸ Import Mod
Archive…**, and it becomes a source like any folder in the list. Loose files,
BSAs and nested archives are all handled; nothing is written to your game
install. A mod with no plugin (a texture pack) imports too — the steps that need
one are greyed out.

A copy of the archive is kept under `export/<plugin>/_source/` so steps can be
re-run after you delete the download. **Mods ▸ Manage Imported Mods…** removes
them. If a master has not been converted yet, the import says so.

```bash
python convert.py --import-mod "C:\Downloads\SomeMod.rar"
python convert.py -f SomeMod.esp          # then convert it normally
python convert.py --list-mods
python convert.py --remove-mod SomeMod.esp
```

### What to install

Everything you install ends up in **`output/Finished Mods/`**. Install these with
your mod manager:

- One `.zip` per converted game or mod
- `TESRuntime.zip`, required (see [TESRuntime](#tesruntime-skse-plugin))
- `TESGameSelect.zip`, the new-game menu (see [Starting a converted game](#starting-a-converted-game))
- `AutoConvertLOD.zip`, the distant-view LOD. Install it after the mods it covers.
- `Slot44 Patch.esp`

The rest of `output/` is working space. Don't install anything from it directly.

### Upgrading

Paste a new download over your existing folder. The **Upgrade** button ticks only
the steps that changed since your last conversion. If you're already current it
says **Up to date**. If it can't tell, it selects every step.

### Starting a converted game

The intended way in is the **TESGameSelect** plugin (*Threads of Prophecy*), a
small standalone Skyrim SE plugin built separately from the conversion itself.
It takes over Skyrim's opening quest so that starting a **new game** shows a
menu asking which world to begin in: Skyrim, Cyrodiil, Vvardenfell, Nehrim,
the Mojave or Morrowind.
Choosing Skyrim runs the vanilla Helgen opening untouched; choosing a converted
game hands off to that game's own character generation, with its real starting
equipment and start location. Games whose plugin is not in your load order are
detected at runtime and simply never appear in the menu, so any subset works.

Build it with the **Pack Start Mod** button (or `python
tools/release/package_start_mod.py`) to get
`output/Finished Mods/TESGameSelect.zip`, then install it like any other
converted mod and enable `TESGameSelect.esp`.
Because it overrides `MQ101`, it conflicts with other alternate-start mods
(Live Another Life, Skyrim Unbound, Alternate Perspective) — use one at a time.
See [TESGameSelect/README.md](TESGameSelect/README.md) for how it works,
configuring it, and rebuilding.

Without the plugin, you can start a converted game from the console instead:

```bash
setstage Charactergen 5
```
or simply teleport to the worldspace with a command like

```bash
cow tes4tamriel 20 20
```

### TESRuntime (SKSE plugin)

`TESRuntime.dll` is the converter's own required SKSE plugin. It currently composes the animation cache singleton for converted creatures, so that multiple mods can edit it and add creatures as well as runs the fallout gun handling that Skyrim has no equivalent for. Its log is
`Documents\My Games\Skyrim Special Edition\SKSE\TESRuntime.log`.
See [tes_runtime/README.md](tes_runtime/README.md) for the details and for
building it yourself.

Two more plugins install alongside it from the same zip: **HavokWorldSize.dll** (below) and
**MorrowindRuntime.dll**, which runs Morrowind's dialogue and scripts. Build the zip with the
**Package SKSE Mod** button, or `python tools/release/package_runtime_dll.py`.

#### HavokWorldSize

`HavokWorldSize.dll` installs alongside TESRuntime as a separate plugin. Skyrim's
physics only works within 64 cells of the world center — past that, NPCs bounce
in and out of the ground and you can't open doors or hit anything. Big
worldspaces like Tamriel Rebuilt go well beyond that, so this widens the limit.
Edit `Data\SKSE\Plugins\HavokWorldSize.ini` to change it (128 by default; use the
smallest that fits your worldspace).

#### Changing the gun reload key

The reload key defaults to **mouse button 4** and the iron-sight (zoom) key to
**right mouse**. To change either, create
`Data\SKSE\Plugins\TESRuntime\TESRuntime.ini` and give it a `[Guns]`
section with the [virtual-key code](https://learn.microsoft.com/windows/win32/inputdev/virtual-key-codes)
of the key you want, in decimal:

```ini
[Guns]
ReloadKey=82
ZoomKey=2
```

`82` is 0x52, so that example moves reload onto the **R** key and
leaves zoom on right mouse. The file is read once at startup, so restart the
game after editing it. Omit a key, or the file entirely, to keep the default.

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
python convert.py -f Oblivion.esm --pack-only          # Pack output assets into Skyrim BSAs
python convert.py -f Oblivion.esm --pack-zip-only      # Zip plugin + BSAs → output/Finished Mods/
python convert.py --modify-body-meshes                 # Build ARMA slot-44 patch (takes no -f)
python tools/release/create_lod.py                             # Bake ALL LOD once (takes no -f)
python tools/release/pack_lod.py                               # Zip the baked LOD mod
python tools/release/package_start_mod.py                      # Zip the TESGameSelect starter mod
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
python -m asset_convert.nif.nif_converter path/to/meshes/ path/to/output/

# BSA extraction only
python -m asset_convert.sources.bsa_extract Oblivion.esm --data-path "C:/path/to/Oblivion/Data"

# Tests
python -m pytest tests/ -v
```

### Development dependencies

Only needed to run the test suite — a conversion never uses these, so end users
can skip this entirely.

| Dependency | Purpose | Install |
|------------|---------|---------|
| **[pytest](https://pytest.org/)** | Test runner | `pip install pytest` |

A few `tools/` analysis scripts need extras nothing else does: `pefile` and
`capstone` (exe disassembly), and `pywin32` for `game_bridge/test_protocol.py`.
Install those only if you run those scripts.

---

## Pipeline phases

These are the steps as presented (and run) by the GUI, in order:

| # | Phase | What happens |
|---|-------|--------------|
| 1 | **Export** | Parse the source binary into a per-record-type KEY=VALUE text cache (`export/<name>/`). A pure dump — no transformation. |
| 2 | **Extract** | Pull meshes, textures, and sounds out of the source game's BSA archives (cached via a manifest). |
| 3 | **Meshes** | Convert source NIFs → Skyrim NIFs and copy textures. |
| 4 | **SpeedTrees** | Procedurally rebuild `.spt` trees as Skyrim flora NIFs. |
| 5 | **Creatures** | Convert creature models and animations (skeletons, ragdolls, behavior graphs). |
| 6 | **Import** | Read the text cache and write the Skyrim binary ESM/ESP — all record transformations happen here. |
| 7 | **Sounds** | Convert voice files to XWM and copy sound files. |
| 8 | **Scripts** | Transpile source scripts to Papyrus and compile. |
| 9 | **Pack BSAs** | *(opt-in, off by default)* Pack the converted assets into Skyrim BSA archives. |
| 10 | **Pack Mod Zip** | Zip the plugin(s) and BSAs into a single archive for installation. |

LOD is **not** in that list. LOD tiles sit on a fixed grid shared by every plugin
that edits a worldspace, so baking them per plugin would generate each contested
tile once per plugin and then throw all but one away. It is a Global action
instead, baked once for the whole load order.

Some actions belong to **no single plugin**, so they are buttons under
**Global** in the sidebar rather than numbered steps. Each runs once and covers
everything you have converted:

| Action | What happens |
|--------|--------------|
| **Create LOD** | Generate all object and terrain LOD in one pass, into a standalone `output/AutoConvertLOD/` mod. The button opens a panel choosing which plugins and worldspaces to include; the plugin order decides which one wins a contested tile (defaults to your `plugins.txt` order). **Install it after the mods it covers.** |
| **Pack LOD** | Zip `output/AutoConvertLOD/` into `output/Finished Mods/AutoConvertLOD.zip` for installation. |
| **Patch Skyrim** | Build the ARMA slot-44 body patch for your whole Skyrim load order (*select plugins...* chooses which), as `output/Finished Mods/Slot44 Patch.esp`. |
| **Start Mod** | Zip the prebuilt TESGameSelect starter mod (see *Starting a converted game*) to `output/Finished Mods/TESGameSelect.zip`. |
| **Package SKSE Mod** | Zip the three runtime plugins (see *TESRuntime*) to `output/Finished Mods/TESRuntime.zip`. |
| **Convert to Master** | Flag converted plugins as masters. A non-master plugin has every reference treated as always-active, and the engine hangs on the main menu past about a million of them. Applies to a whole master chain at once. |
| **Convert UI** | *(Tools menu)* Build a standalone mod that reskins Skyrim's message boxes and cursor with Oblivion's artwork, read from your Oblivion install. |

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
│   ├── skin_retarget.py  #   Source → Skyrim skeleton retargeting
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
| [OpenMW](https://openmw.org/) | The Morrowind dialogue engine and MWScript interpreter, vendored into `external/openmw/` and linked into `MorrowindRuntime.dll` | **GPL-3.0** — see the GPL note below. Vendored by `tools/generators/vendor_openmw.py`; license text in `external/openmw/LICENSE` |
| [figment — hkxcmd](https://github.com/figment/hkxcmd) | Havok packfile XML↔binary compiler, `external/hkxcmd/hkxcmd.exe`, used to build skeleton/behavior/animation `.hkx` | **BSD-3-Clause** for hkxcmd's own sources (© 2011; text in `external/hkxcmd/LICENSE.TXT`). **Statically links Havok** — see the Havok note below |
| [Monitor221hz — HKX2-Enhanced-Library](https://github.com/Monitor221hz/HKX2-Enhanced-Library) | Skyrim SE 64-bit Havok packfile ↔ XML, `external/hkxconv/hkxconv.exe`, used to patch the vanilla humanoid behavior graphs (`hkxcmd` cannot read 64-bit files). Forked from [ret2end](https://github.com/ret2end/HKX2Library), in turn from [katalash](https://github.com/katalash/DSMapStudio) / [krenyy](https://gitlab.com/HKX2/HKX2Library); bundles `BinaryReaderEx` from [JKAnderson — SoulsFormats](https://github.com/JKAnderson/SoulsFormats) | **No stated license** anywhere in that chain (no LICENSE file upstream) — community project, publicly distributed and freely forked; redistributed here on that basis. Full chain and rebuild steps in `external/hkxconv/README.md` |
| [Microsoft DirectX SDK (June 2010)](https://www.microsoft.com/en-us/download/details.aspx?id=6812) | `external/xwmaencode/xWMAEncode.exe`, xWMA voice compression | Microsoft — **not redistributed**; obtain from the SDK (see [Requirements](#requirements)) |
| [FFmpeg](https://ffmpeg.org/) | `external/ffmpeg/ffmpeg.exe`, decoding MP3/WAV voice and sound files | **LGPL v2.1 or later** — redistributed; license text in `external/ffmpeg/COPYING.LGPLv2.1`. See the LGPL note below |
| Oblivion banner font ([dafont](https://www.dafont.com/oblivion.font)) | Project banner | *Free for personal use only*, based on Bethesda's trademarked logo. **Not** bundled in this repo; the banner ships as pre-rendered vector outlines. |

> **Note on GPL-3.0:** `external/pynifly_hkx/` (creature animation conversion,
> `asset_convert/havok/hkx_anim.py`) and `external/openmw/` (linked into
> `MorrowindRuntime.dll`) are both GPL-3.0. If you redistribute a build that includes
> either, the GPL's terms apply to that distribution. `MorrowindRuntime.dll` is kept a
> separate DLL for this reason.

> **Note on LGPL (FFmpeg):** `external/ffmpeg/ffmpeg.exe` is built from **unmodified**
> FFmpeg 7.1.2 sources under LGPL v2.1, with no GPL components (`--enable-gpl` and
> `--enable-version3` are both off). Because it is statically linked, LGPL §6 requires
> that recipients be able to relink it against a modified FFmpeg — satisfied by
> `tools/generators/build_ffmpeg.py`, which pins the exact upstream tarball and records every
> configure flag used to produce the shipped binary.

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


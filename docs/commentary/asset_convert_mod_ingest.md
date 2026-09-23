# asset_convert/sources/mod_ingest.py - mod archive ingest

**Code:** `asset_convert/sources/bsa_extract.py`, `asset_convert/sources/mod_ingest.py`, `asset_convert/sources/source_registry.py`, `tes5_import/overrides/nested.py`

## Contents

- [0. Validated against three real mod archives](#0-validated-against-three-real)
- [1. Dependencies — nothing may be assumed present](#1-dependencies-nothing-may-be)
- [2. What the current pipeline assumes](#2-what-current-pipeline-assumes)
- [3. Design](#3-design)
- [4. GUI ([gui.py](../../gui.py))](#4-gui)
- [5. CLI ([convert.py](../../convert.py))](#5-cli)
- [6. Files touched](#6-files-touched)
- [7. Tests](#7-tests)
- [8. Risks and decisions](#8-risks-decisions)
- [9. Build order](#9-build-order)

**Status:** IMPLEMENTED 2026-08-14 (FOMOD deferred — see §4).

Shipped: `asset_convert/{archive,source_registry,mod_ingest}.py`,
`convert.py::resolve_plugin_path` + `--import-mod`/`--list-mods`/`--remove-mod`,
the GUI `Mods` menu, source-scope selector and sidebar drop zone,
`external/7zip/`, and `tests/test_mod_ingest.py` (43 tests).
FOMOD option dialogs are NOT built: an archive carrying `fomod/ModuleConfig.xml`
imports its whole payload root, which is correct for every mod that does not
gate files behind installer options.

Today the pipeline can only convert a plugin that already sits in the Oblivion
`Data` directory, and it only sources that plugin's assets from `.bsa` archives
found next to it. Most Nexus-style mods ship as a `.zip` / `.7z` / `.rar`
containing loose `Meshes\` / `Textures\` folders, or BSAs, or both, plus one or
more `.esp`/`.esm`.

This feature makes such an archive a first-class conversion source. It is
**purely additive**: the existing "pick a plugin from the Oblivion Data
directory" path is untouched, and ingest is a *new source* for the extract
phase, never a replacement for it.

---

## 0. Validated against three real mod archives
<a id="0-validated-against-three-real"></a>

Checked against the archives in the user's Downloads folder. They corrected
several assumptions — this is what mods actually look like:

| Archive | Structure | What it proves |
|---|---|---|
| **Elsweyr Anequina** (399 MB) | Loose `Meshes\`, `Textures\`, `DistantLOD\` at **root** + 3 BSAs + `.esp` at root + readme folders | Root layout, **no Data folder**; loose **and** BSA together; a top-level dir (`DistantLOD`) that is neither |
| **TWMP High Rock** (70 MB) | `TWMP_HighRock\Data\` containing `.bsa` + **two** `.esp` | **Nested** Data folder; multiple plugins |
| **TWMP Skyrim** (58 MB) | `TWMP_Skyrim\Data\` containing **two** `.esp`, no assets | Nested Data; plugin-only archive |

Three archives, three layouts, and **two of three ship multiple plugins**.
Layout detection and plugin selection are the core of this feature, not edge
cases. All three are `.rar`, which drives the dependency decision in §1.

---

## 1. Dependencies — nothing may be assumed present
<a id="1-dependencies-nothing-may-be"></a>

**Requirement: end users install nothing manually, and every new dependency is
documented in the README.** Nothing may depend on 7-Zip, WinRAR, or any tool
that happens to be on a developer's machine.

### Bundle 7-Zip — the existing pattern

**`.zip`, `.7z`, and `.rar` are all handled by one bundled `7za.exe`.** This
project already ships eight executables under `external/` — `ffmpeg.exe`,
`BSArch.exe`, `hkxcmd.exe`, the MOPP bridge — with `_bundled_exe()`
([preflight.py:150](../../preflight.py#L150)) built for exactly this: binaries
committed to the repo, where absence means an incomplete checkout, not a
missing user install.

7-Zip is open source (LGPL, with the unRAR decoder under its own
extraction-only licence), redistributable, and extracts all three formats.
`ffmpeg` is already bundled on the same LGPL basis, with its `COPYING.LGPLv2.1`
committed beside it ([external/ffmpeg/](../../external/ffmpeg/)) — so this adds no new
licensing question, just another entry beside it.

**Ship `7z.exe` + `7z.dll`, NOT `7za.exe`.** Measured against 7-Zip 26.02
(2026-06-25 build) and verified by listing a real `.rar`:

| Candidate | Size | RAR support |
|---|---|---|
| `7za.exe` (standalone, from `7zXXXX-extra.7z`) | 1.27 MB | **NO** — its format list is 7z/bzip2/gzip/zip only; fails with `Cannot open the file as archive` |
| **`7z.exe` + `7z.dll`** | **2.37 MB** (0.55 + 1.82) | **YES** — Rar, Rar5, and codecs Rar1/2/3/5 |

The standalone `7za.exe` looks like the obvious pick and is the wrong one: RAR
lives in `7z.dll`, so the pair is mandatory. The two files were copied to an
isolated directory and used to list `Skyrim esp-40005-0-1.rar` correctly, so the
pair genuinely is self-contained — no registry keys, no install, no `Codecs/`
subfolder.

```
external/7zip/
  7z.exe                  # 0.55 MB — console front end
  7z.dll                  # 1.82 MB — format/codec library, INCLUDING RAR + RAR5
  License.txt             # 7-Zip LGPL + unRAR extraction-only licence
  BUILD.md                # version + provenance, matching external/ffmpeg/
```

**2.37 MB total**, which is modest next to what the repo already commits:
`hkxcmd.exe` alone is 10.0 MB, `BSArch.exe` 1.86 MB, `ffmpeg.exe` 1.05 MB.

The `.rar` support this buys is why no Python RAR wrapper is needed. Every one
of them (`rarfile`, `unrar`, `libarchive-c`, `patool`) is a *shim* that loads a
DLL or shells out to a binary it does not ship — verified by inspecting the
wheels — so each would reintroduce the exact user-install requirement this rule
forbids. Bundling the binary ourselves removes the problem instead of relocating
it.

| Format | Handler | Needs |
|---|---|---|
| `.zip` | stdlib `zipfile` (fast path), `7z.exe` fallback | nothing |
| `.7z` | bundled `7z.exe` | nothing |
| `.rar` | bundled `7z.exe` | nothing |
| `.bsa` | existing `extract_bsa()` | nothing |

Stdlib `zipfile` stays the `.zip` path because it streams members natively and
needs no subprocess. `7z.exe` is invoked with `l -slt` to list and `x` to
extract, through the existing `subprocess_flags.POPEN_FLAGS` so no console
window appears under `pythonw`.

**One consequence to respect:** `7z.exe` extracts to a directory rather than
streaming member-by-member. Extraction targets a temp dir under `export/`,
then files are routed into place, so the layout rule and `_safe_join` still
apply to every path — the archive is never trusted to write where it likes.

### The only new pip dependency

| Package | Purpose | Install |
|---|---|---|
| `tkinterdnd2` | Drag-and-drop onto the GUI. **Optional** — without it the window still opens and **Mods ▸ Import…** still works | `pip install tkinterdnd2` |

Registered via the existing `_pip` helper ([preflight.py:114](../../preflight.py#L114))
and `7za.exe` via `_bundled_exe`, so **Tools ▸ Check Dependencies** reports both
like everything else. Add the `tkinterdnd2` row to the README's optional table
([README.md:78-85](../../README.md#L78-L85)) and its `pip install` line
([README.md:70](../../README.md#L70)).

---

## 2. What the current pipeline assumes
<a id="2-what-current-pipeline-assumes"></a>

| # | Assumption | Where | Why an archive breaks it |
|---|---|---|---|
| A1 | Plugin binary is at `<tes4DataPath>/<file_name>` | `phase_export` ([convert.py:209](../../convert.py#L209)), `topological_order` ([convert.py:173](../../convert.py#L173)) | An archive's ESP is not in `Data`, and must never be copied there |
| A2 | Assets come from BSAs found by plugin name | `get_bsa_files` ([asset_convert/sources/bsa_extract.py:150](../../asset_convert/sources/bsa_extract.py#L150)) | Loose-file mods have no BSA; Elsweyr's BSAs live in an archive, not `Data` |
| A3 | `tes4DataPath` is one global path per run | `get_paths` ([convert.py:129](../../convert.py#L129)) | Mixing `Oblivion.esm` with an imported mod needs two sources |

Everything downstream is already safe. The BSA extractor writes to
`export/<plugin>/{meshes,textures,sound,trees,misc}`
([bsa_extract.py:378-390](../../asset_convert/sources/bsa_extract.py#L378-L390)) — exactly the
shape a loose-file mod has. Masters resolve as **sibling directories under
`export/`** ([overrides.py:73-80](../../tes5_import/overrides/nested.py#L73-L80)).

**The architectural win: ingest only has to produce the same `export/<plugin>/`
tree the BSA extractor already produces. Nothing after Phase 2 changes.**

---

## 3. Design
<a id="3-design"></a>

### 3.1 Layout rule

Per spec, exactly two shapes:

> **If a `Data` folder exists anywhere in the archive, the payload is that
> folder's contents (however deeply nested). Otherwise the payload is the
> archive root.**

Shallowest `Data` wins, so a mod shipping `Docs\Data\` beside a real `Data\`
resolves deterministically. Equal-depth ties are reported, never guessed —
unless every tied folder is an installer sub-package, which is the case below.
Everything outside the payload root is ignored — readmes, screenshots,
Elsweyr's `00 …ReadMe-Quests-Guide\`.

#### <a id="payload-roots"></a>BAIN sub-packages

A BAIN "complex" package has no folder named `Data` at all. It ships each
installable option in its own top-level folder, and the installer merges the
ones the user picks:

    00 Data Files\                  meshes, textures, icons, sound...
    01 Data Files - Normal Maps\    textures

**The numeric prefixes are cosmetic.** Wrye Bash never parses them — it sorts
sub-packages by name and nothing more. The real rule is structural, taken from
`Installer._reset_cache` in Wrye Bash's `Mopy/bash/bosh/bain.py`:

* **Type 1 (simple)** — a loose file of an installable type at the archive
  root, or a top-level folder that is itself a recognised data dir. The payload
  is the archive root and there are no sub-packages. This check WINS: one loose
  `.esp` at the root demotes the whole archive.
* **Type 2 (complex)** — otherwise, every top-level folder whose own second
  level is a recognised data dir, or which holds a top-level installable file.
  All of them are payload roots.

`DATA_DIRS` is Wrye Bash's `GameInfo.Bain.data_dirs`, the union of its Oblivion
and Morrowind sets, so this works for both games.

One deliberate divergence: Wrye Bash also accepts a docs-only folder as a
sub-package, because it skips the docs afterwards. We drop docs outright, so a
`ReadMe-Quests-Guide\` folder that qualified here would install as an empty
sub-package — `_is_subpackage` requires a real asset or plugin.

Because several roots can be active, `payload_root` is a LIST; a single `Data`
folder is the one-element case, `[]` means the archive root, and an ordinary
equal-depth `Data` tie is still reported as ambiguous rather than merged.

Measured on Tamriel Data (HD) (2.3 GB, 46,864 files): with the old exact-`Data`
rule no root matched and the whole tree landed in `misc/00 Data Files/…`, where
no asset stage could see it. Now it reads as 30,930 meshes, 9,246 textures,
1,565 sound, 5,122 misc.

### 3.2 Nested archives

- **`.bsa` inside** — streamed to a temp file, run through the existing
  `extract_bsa()`. Elsweyr makes this mandatory: skipping it loses ~815 MB.
- **`.zip`/`.7z`/`.rar` inside** — recursively ingested into the same payload
  tree, **depth-capped (default 3)** with a cumulative uncompressed-size cap
  against zip bombs. Depth exceeded is a logged skip, never a crash. The layout
  rule recurses too, relative to the nested archive's own contents.
- **Loose beats BSA** — the engine's own rule. BSAs extract first, loose files
  overlay. Outer archive beats nested, matching mod-manager install order.

### 3.3 The retained archive — `export/<plugin>/_source/`

The imported archive is **copied into the export folder** and kept:

```
export/ElsweyrAnequina.esp/
  _source/
    ElsweyrAnequina.esp                  <- the plugin binary
    Elsweyr Anequina-…rar                <- the original archive, retained
    .mod_ingest_manifest.json
  meshes/  textures/  sound/  misc/      <- ingested payload
```

Retaining the archive is what makes re-running work forever (§4.3): the
original download can be deleted or moved and every step still re-runs. It also
makes re-ingest exact rather than approximate.

**Cost is real and must be surfaced**: Elsweyr adds 399 MB on top of ~980 MB
extracted. So the import dialog shows the archive size with a **"Keep a copy of
the archive (enables re-import later)"** checkbox, default **on**. Unchecked, the
registry records the original path and re-import falls back to it if still
present, warning when it is gone. `Manage Imported Mods…` shows the retained
size and offers "Free space (drop archive copy)".

### 3.4 Folder import — first class

`Mods ▸ Import Mod Folder…` takes an already-extracted mod directory. Same
layout rule, same routing, no archive dependency at all. This is the
`.rar`-without-`unrar` answer and the manual-install answer, so it is a real
feature, not a fallback. `kind: "folder"` records the source path; there is no
archive to retain.

### 3.5 The source registry — `export/sources.json`

Relaxes A3 to a per-plugin override and records provenance.

```jsonc
{
  "version": 1,
  "sources": {
    "ElsweyrAnequina.esp": {
      "kind": "archive",                       // "archive" | "folder"
      "archive_original": "C:/Users/Bryant/Downloads/Elsweyr…rar",
      "archive_retained": "export/ElsweyrAnequina.esp/_source/Elsweyr…rar",
      "archive_sha1": "3f2a…",
      "archive_size": 418100265,
      "plugin_member": "ElsweyrAnequina.esp",
      "payload_root": "",                      // "" = archive root
      "plugin_path": "export/ElsweyrAnequina.esp/_source/ElsweyrAnequina.esp",
      "group_id": "elsweyr-anequina-3f2a",     // shared by all plugins in one import
      "group_plugins": ["ElsweyrAnequina.esp"],
      "fomod_choices": {"Textures": "2K"},     // replayed on re-import
      "bsas_ingested": ["ElsweyrAnequina - Meshes.bsa"],
      "ingested_utc": "2026-08-11T18:04:11Z",
      "counts": {"meshes": 812, "textures": 940, "sound": 12, "misc": 3}
    }
  }
}
```

Anything **not** listed keeps today's behaviour exactly. The file may be absent
entirely and nothing changes — that is the additive guarantee, and test 9
asserts it.

### 3.6 Plugin binary resolution

```python
def resolve_plugin_path(file_name: str, tes4_data: str,
                        export_dir: str) -> str | None:
    """Absolute path to a plugin's TES4 binary.

    Registry-registered plugins keep their binary at
    export/<plugin>/_source/<plugin>; everything else lives in the Oblivion
    Data directory, as before.
    """
```

Route **every** `os.path.join(tes4_data, name)` through it — all of them, or the
feature half-works:

| Call site | File | Purpose |
|---|---|---|
| `topological_order` | [convert.py:173](../../convert.py#L173) | master graph from headers |
| `phase_export` | [convert.py:209](../../convert.py#L209) | the export source |
| dependency preflight | `convert.py` / `preflight.py` | existence check |
| GUI plugin validation | [gui.py:2553](../../gui.py#L2553) | the "not a plugin in the Oblivion data directory" guard |

`_source/` cannot collide with an asset category, and keeps
`parse_export_directory` from ever seeing a stray `.esp`.

### 3.7 Ingest module

**`asset_convert/sources/mod_ingest.py`**

```python
def inspect(path, *, max_depth=3) -> ArchiveManifest   # read-only, fast
def ingest(path, export_dir, *, plugin_members, fomod_choices=None,
           keep_archive=True, force=False) -> IngestResult
def reingest(plugin, export_dir) -> IngestResult       # from retained archive
```

`inspect` lists members, applies the layout rule, finds plugins/BSAs/FOMOD, and
returns a manifest for the dialog. **Nothing is written until the user
confirms.**

`ingest` then: extract contained BSAs → overlay loose payload (category-routed,
loose wins) → recurse nested archives → copy plugin(s) to `_source/` → retain
the archive → write `sources.json` + `.mod_ingest_manifest.json`, keyed on
SHA-1 + size so a re-run is a cached no-op, mirroring
[bsa_extract.py:346-353](../../asset_convert/sources/bsa_extract.py#L346-L353).

**Path safety is mandatory.** Member names are attacker-controlled and these
files come off the internet. Every write goes through one `_safe_join`
rejecting `..`, absolute paths, drive letters, and link entries — a test per
case. `extractall` is never used.

### 3.8 Multiple plugins in one archive

TWMP ships two ESPs. The dialog lists every plugin found with checkboxes,
defaulting to all. The asset payload is ingested **once** and shared: each
selected plugin gets its own `export/<plugin>/` entry, with assets **hard-linked**
where the filesystem allows (same volume, NTFS) and copied otherwise, so a
400 MB mod is not duplicated per plugin. All plugins from one import share a
`group_id`, which drives the plugin-selector scoping in §4.

### 3.9 `phase_extract` — additive branch

```python
def phase_extract(file_name, tes4_data, config, output_dir=None):
    src = source_registry.get(file_name)
    if src:                                   # imported mod
        return mod_ingest.reingest(file_name, ...)    # cached no-op if unchanged
    return extract_bsas(...)                  # UNCHANGED existing path
```

A Data-directory plugin never reaches the new branch. `--extract-only` re-runs
ingest for an imported mod, so the step name users know works for both kinds.

### 3.10 Masters

An imported mod almost always masters `Oblivion.esm`. Per CLAUDE.md's
master-blindness rule, a missing master export silently produces a broken
plugin. So ingest reads MAST entries via `get_masters_from_binary`, and any
master lacking `export/<master>/` **hard-blocks** the run with a message naming
it and offering to queue its conversion first. A warning is not enough — this is
the project's most common way to ship something that looks converted and is
dead.

---

## 4. GUI ([gui.py](../../gui.py))
<a id="4-gui"></a>

### 4.1 Plugin selector — scoped to the last import

Per spec, after an import the selector shows **that mod's plugins**, with a way
back to the Data directory. Implemented as a **scope selector** directly above
the plugin combobox:

```
Source:  [ Oblivion Data Directory        ▾ ]     <- new, one row
Plugin:  [ ElsweyrAnequina.esp            ▾ ]
```

The Source dropdown lists:

- `Oblivion Data Directory  (247 plugins)` — the existing behaviour, always first
- one entry per imported mod group, newest first —
  `Elsweyr Anequina  (1 plugin)`, `TWMP High Rock  (2 plugins)`

Selecting a scope repopulates `all_plugins` from that scope alone. After an
import the scope **switches automatically to the new mod**, which is the
requested behaviour, and one click returns to the Data directory. Because scope
is explicit, `_on_tes4_change` ([gui.py:1255](../../gui.py#L1255)) keeps replacing
`all_plugins` exactly as it does now — it just only fires while the Data scope
is active, so an imported mod can no longer be wiped by touching the data dir.

The validation guard at [gui.py:2553](../../gui.py#L2553) widens to accept registry
plugins, and its message becomes scope-aware.

### 4.2 Whole-sidebar drop zone

The drop target is the **entire sidebar** ([gui.py:1099](../../gui.py#L1099)).

- Register the sidebar frame; let events bubble from children so dropping
  anywhere works, including on the combobox.
- **Cursor change on drag-enter** to a copy/hand cursor, plus an accent border
  and a "Drop mod archive" overlay, reverted on leave/drop. Tk cursors are
  per-widget: set on the sidebar, inherited by children, with explicit
  save/restore for children that override it (Entry's `xterm`).
- **Drag-leave is unreliable** in tkdnd across child boundaries — guard with an
  enter/leave counter plus a safety reset on `<Drop>` and window focus-out, or
  the sidebar sticks in highlight state.
- Non-archive drops are rejected with a brief inline message, not a modal.
- A dropped **folder** routes to folder import.

`tkinterdnd2` requires `TkinterDnD.Tk()` as the root class — the one change
touching GUI startup, guarded to fall back to plain `tk.Tk()`. If it is absent
the window still opens, drag-and-drop is inert, **Mods ▸ Import…** still works,
and a one-line hint names the package.

### 4.3 Toolbar — `Mods` menu, and re-running imported plugins

New menubutton beside `Settings` / `Converted`
([gui.py:716-732](../../gui.py#L716-L732)):

```
Mods ▸ Import Mod Archive…       (.zip / .7z / .rar)
     ▸ Import Mod Folder…
     ▸ Manage Imported Mods…     (list, retained size, re-import, remove)
```

**The `Converted` menu must keep working for imported plugins** — this is the
re-run requirement. Today `_select_converted` ([gui.py:767](../../gui.py#L767))
restores the plugin's Data directory via `version_info.source_path_for`
([version.py:460](../../version.py#L460)) and then fails the "is this a real plugin"
check if it is not found there. For an imported plugin it instead:

1. looks the name up in `sources.json`;
2. switches the **Source scope** to that mod's group rather than a data dir;
3. proceeds to the existing `_commit(name)` + step-planning path unchanged.

Because the archive is retained (§3.3) and the payload stays under `export/`,
every step re-runs indefinitely — including `--extract-only`, which re-ingests
from the retained copy. That closes the gap where an imported plugin appeared in
`Converted` but could not actually be re-selected.

`scan_converted` ([gui.py:207](../../gui.py#L207)) needs no change: it keys on
`<name>.manifest.json`, which `phase_import` writes for imported plugins too.

### 4.4 Import dialog

Both entry points converge on one dialog in the existing `_dialog` card style:

1. `inspect` runs on a **worker thread** — a 400 MB archive must not freeze the
   UI — with a spinner.
2. Shows detected layout (`Data folder: TWMP_HighRock\Data` or `archive root`),
   plugin checkboxes, asset counts by category, BSAs found, nested archives,
   detected masters flagged red if missing, the archive-size + keep-a-copy
   checkbox, and FOMOD/BAIN sections if present.
3. OK ingests with progress into the existing log pane, then switches the
   Source scope to the new mod.

### 4.5 FOMOD dialog

A FOMOD is `fomod/ModuleConfig.xml`, a declarative XML installer. The subset
covering nearly every real mod is small:

| Element | Meaning | Widget |
|---|---|---|
| `<requiredInstallFiles>` | always installed | none — just apply |
| `<installStep>` | a wizard page | a section |
| `<group type="SelectExactlyOne">` | pick one | radio buttons |
| `<group type="SelectAny">` | pick any | checkboxes |
| `<group type="SelectAtLeastOne">` | pick ≥1 | checkboxes + validation |
| `<plugin>` | one option: name, description | a labelled row |
| `<files>` `<file>`/`<folder>` | source→destination mapping | applied on OK |

**Roughly a day**, because we need only the *file mapping*, not a faithful
installer UX: every `installStep` renders as **one scrolling page with a section
per group** instead of a Next/Back wizard. Parse is ~150 lines of stdlib
`ElementTree`; apply walks the selected plugins' `<files>` honoring `priority`,
feeding the same copy routine.

Out of scope (and why a real mod manager is much harder): cross-step
`flagDependency` flags, and `<conditionalFileInstalls>` / `<dependencies>`
predicated on *other mods* or game version — we have no load order to evaluate
against. Any unevaluable condition is skipped and **logged by name**, never
silently dropped; if a mod uses flags, fall back to required-files-plus-defaults
and say so in the log.

**Nearly free addition:** the BAIN convention of numbered top-level folders as
sub-packages — Elsweyr's `00 ElsweyrAnequina-ReadMe-Quests-Guide\` and the
Nehrim install's `10 Optional - …` / `20 Patch - …`. A filename-pattern check
plus the same checkbox list, ~1 hour on top.

Choices are stored in `sources.json` (`fomod_choices`) and **replayed on
re-import**, so a re-run never re-asks. **Never fail the ingest over FOMOD**:
any parse error falls back to plain payload-root copy with a logged reason.

---

## 5. CLI ([convert.py](../../convert.py))
<a id="5-cli"></a>

```
python convert.py --import-mod <archive|folder> [--plugin-member <path>]…
                  [--fomod-defaults | --fomod-option "Group=Choice"]…
                  [--no-keep-archive]
python convert.py --list-mods
python convert.py --remove-mod <plugin.esp>
python convert.py -f ElsweyrAnequina.esp        # converts like any plugin
```

`--import-mod` ingests and exits, so it composes with everything else.

---

## 6. Files touched
<a id="6-files-touched"></a>

| File | Change |
|---|---|
| `asset_convert/sources/mod_ingest.py` | **New.** Inspect, layout rule, nested archives, safe extraction, routing, archive retention, manifest |
| `asset_convert/fomod.py` | **New.** `ModuleConfig.xml` → option tree → file mapping; BAIN numbered folders |
| `asset_convert/sources/source_registry.py` | **New.** Read/write `export/sources.json` |
| `convert.py` | `resolve_plugin_path`; route `topological_order` + `phase_export`; `phase_extract` branch; `--import-mod` / `--list-mods` / `--remove-mod` |
| `gui.py` | Source-scope selector, sidebar drop zone + cursor, optional `tkinterdnd2` root, `Mods` menu, import + FOMOD dialogs, `_select_converted` registry branch, validation widening |
| `asset_convert/sources/bsa_extract.py` | Extract `should_extract_file` + the category-routing split into reusable helpers (currently inline in `extract_bsa`) so ingest cannot drift from it |
| `preflight.py` | `_pip` entries for `py7zr` / `rarfile` / `tkinterdnd2` |
| `README.md` | Optional-dependency table rows + the `pip install` line |
| `docs/reference/python_tools.md` | New modules + flags (**required same pass**, per CLAUDE.md) |
| `docs/reference/pipeline.md` | Ingest as an additional extract-phase source |
| `tests/test_mod_ingest.py`, `tests/test_fomod.py` | **New** — §7 |

Untouched by design: `tes5_import/`, `script_convert/`, every asset stage after
Phase 2. If ingest is correct they cannot tell the difference.

---

## 7. Tests
<a id="7-tests"></a>

Synthetic in-memory zips, well under the 120 s limit.

1. **Layout rule** — root payload (Elsweyr shape), nested `X\Data\` (TWMP
   shape), deeply nested `A\B\Data\`; shallowest `Data` wins.
2. **Path traversal** — `../../evil.dll`, `C:/evil.dll`,
   `foo/../../evil.dll` rejected; nothing written outside the destination.
3. **Category routing parity** — a zip and a BSA with identical file lists
   produce identical `export/<plugin>/` trees (guards the drift this design
   depends on).
4. **Loose-over-BSA precedence** — loose bytes win.
5. **Nested archive** — zip-in-zip ingested; depth cap enforced.
6. **Multiple plugins** — a two-ESP archive registers both against one shared
   payload with a common `group_id`.
7. **`.lip` exclusion** parity with `should_extract_file`.
8. **Idempotence** — re-ingest of an unchanged archive writes nothing.
9. **Additive guarantee** — with an empty registry, `resolve_plugin_path` and
   `phase_extract` reproduce today's behaviour exactly.
10. **Re-import from retained archive** — deleting the *original* download
    still allows a full re-ingest.
11. **Master detection** — a plugin mastering `Oblivion.esm` with no
    `export/Oblivion.esm/` is reported blocked.
12. **FOMOD** — required files always install; `SelectExactlyOne` /
    `SelectAny` produce the right mapping; stored choices replay; an
    unparseable config falls back to plain copy without raising.

---

## 8. Risks and decisions
<a id="8-risks-decisions"></a>

- **FormID drift: none.** Ingest mints no records at all; it only moves bytes
  into `export/`.
- **Determinism.** Members iterate in **sorted order**, not archive order, so
  equivalent archives produce identical trees.
- **Case.** Elsweyr ships `Meshes\`/`Textures\` capitalised; all matching is
  lower-cased, as the BSA path already does.
- **Disk.** Elsweyr costs ~980 MB extracted + 399 MB retained. Surfaced in the
  dialog, with an opt-out and a "free space" action in Manage.
- **Memory.** Members stream, never fully buffered — hard project rule.
  `inspect` reads only the member list.
- **Nothing is written outside `export/`.** The source archive is never
  modified and the Oblivion `Data` directory is never written to.
- **`.rar` is honestly conditional** (§1) — all three sample archives are
  `.rar`, so the folder-import path must be genuinely good, not a token
  fallback.
- **`tkinterdnd2` changes the Tk root class** — the one startup-touching change;
  guarded.

---

## 9. Build order
<a id="9-build-order"></a>

Steps 1–3 make the feature real CLI-first; each step leaves the tree working.

1. `source_registry.py` + `resolve_plugin_path` + call-site routing. No
   behaviour change (empty registry); `--list-mods` works.
2. `mod_ingest.py`: `.zip`, layout rule, path safety, routing, BSA extraction,
   multi-plugin, archive retention (tests 1–4, 6, 7, 9).
3. `phase_extract` branch + idempotence + master block + re-import (tests 8,
   10, 11). Full CLI conversion of an imported mod, end to end.
4. Folder import + `.7z`, nested archives (test 5); `.rar` behind its guard.
5. GUI: `Mods` menu, import dialog, **Source-scope selector**, `Converted`
   re-run branch, manage/remove.
6. GUI: whole-sidebar drop zone + cursor feedback.
7. `fomod.py` + FOMOD dialog + BAIN numbered folders (test 12).
8. README + docs pass.

Step 6 lands after step 5 deliberately: the toolbar makes the feature usable,
and the drop zone is presentation over an already-working import.


## BSA naming per game

**Code:** `get_bsa_files` and `_EXTRA_BSA_BASES` in
`asset_convert/sources/bsa_extract.py`

Archive discovery builds candidate filenames from the plugin stem, which holds
for Oblivion but not for every game:

| Plugin | Archives |
|---|---|
| `Oblivion.esm` | `Oblivion - Meshes/Textures - Compressed/Sounds/Misc.bsa` |
| `Knights.esp` | `Knights.bsa` (one archive, smaller DLCs) |
| `Nehrim.esm` | `N - Meshes/Textures1/Textures2/Sounds/Misc.bsa`, `L - Voices/Misc.bsa` |
| `FalloutNV.esm` | `Fallout - Meshes/Meshes2/Misc/Sound/Textures/Textures2/Voices1.bsa`, `Update.bsa` |
| `Fallout3.esm` | `Fallout - Meshes/Textures/Sound/Voices/Misc/MenuVoices.bsa` (a TTW copy in the New Vegas folder also has `Fallout3 - *.bsa`) |

**FO3/FNV name their archives after the GAME, not the plugin.** The stem
`FalloutNV` matches nothing, so discovery returned **0 of the 47 BSAs** in a
Tale of Two Wastelands install and every asset silently went missing --
`architecture\Strip\Lucky38.NIF` lives in `Fallout - Misc.bsa`, so
the Lucky 38 exterior had no mesh at all while all 314 of its interior pieces
converted normally.

`_EXTRA_BSA_BASES` is the fix for exactly this shape of problem: it maps a
plugin stem to the extra prefixes its archives really use, and FalloutNV
registers `Fallout` the way Nehrim registers `N` and `L`. Fallout3 registers
`Fallout` too: without it a real Fallout 3 GOTY install matched **0** archives
(its eight are all `Fallout - *` or `Anchorage - *`), and the only Fallout 3
assets ever extracted were Tale of Two Wastelands' `Fallout3 - *.bsa` from the
New Vegas folder, which leave out every file New Vegas already ships.

### <a id="same-named-plugins"></a>Same-named plugins from different Data folders

**Code:** `home_directory`, `claim_home`, `directory_for`, `variant_folder`,
`copies` in `asset_convert/sources/source_registry.py`; `_announce` and
`_list_sources` in `convert.py`; `extracted_from` in `bsa_extract.py`

A plugin name is not unique across installs. Tale of Two Wastelands puts its
own `Fallout3.esm` in the New Vegas Data folder: 347 MB, HEDR 1.34, 792,785
records, against the real Fallout 3 GOTY file's 289 MB, HEDR 0.94, 808,699
records. They are different plugins that share a name.

- **Which copy converts** is the selected Data folder (`--data-dir`, else
  `tes4DataPath`, which the GUI's Source dropdown sets). `convert.py` exports
  it as `TESCONV_SOURCE_DIR` so every phase process and pool worker resolves the
  same copy. A master not in the selected folder resolves to its home.
- **The home** is the first folder a run read the plugin from, pinned in
  `sources.json` under `homes`. It keeps the plain `export/<plugin>/` and
  `output/<plugin>/` folders, so pinning never moves an existing tree.
- **Any other copy** gets `export/<plugin> (<install>)/` and the matching
  `output/` folder, through `asset_root_name`, so the export, extraction,
  caches and output of each copy stay apart. Dots are removed from the install
  label so the folder's `Path.stem` is still the plugin stem, which keeps the
  asset namespace the same for both copies.
- **Listing:** each run prints every plugin's Data folder and export folder;
  `--list-mods` lists each Data folder and every plugin found in more than one,
  with the folder each copy converts into.
- **Extraction** records each archive's full path in
  `.bsa_extract_manifest.json`, prints the folder it reads from, re-extracts an
  archive of the same name from a different folder, and warns when the target
  already holds files from another folder. It never deletes them; clearing the
  folder is left to the user.

A tool run outside `convert.py` has no selected folder, so it resolves every
plugin to its home copy.

<a id="update-bsa"></a>**`Update.bsa` is FNV's official patch archive** and
the last entry of the game's `SArchiveList`; its 86 files override the base
archives. It holds `characters\_1stperson\1hpaim.kf`, the only
first-person pistol aim clip the game has (the base `Fallout - Meshes.bsa`
has none), so the first-person gun graph had no pose to build on until
`Update` joined the FalloutNV bases. Candidates are probed in base order and
a later archive overwrites, which matches the engine's load order.

Two pattern notes. FNV uses `Sound.bsa` (singular) where Oblivion and Nehrim
use `Sounds.bsa`, so both spellings are probed. And the base is deliberately
NOT extended to the DLC or Tale of Two Wastelands archives (`Anchorage -
Main.bsa`, `TaleOfTwoWastelands - Main.bsa`, `TTWInteriors_Core -
Meshes.bsa`, 41 in all): only FalloutNV.esm is being converted, so only its own
six archives are claimed.

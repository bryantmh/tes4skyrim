# TES4-to-TES5 Conversion Project — AI Context

> # 🛑 READ BEFORE ENDING ANY TURN 🛑
>
> **Asked a QUESTION? Answer it. Don't write code.** Me proposing an approach
> is not permission to build it. Build only on "do it" / "implement" / "fix".
>
> **Given a TASK? Finish it — fixed, built, verified.** No mid-task status,
> no "want me to X?", no diagnosis without the fix. Low confidence and being
> wrong repeatedly oblige you to keep going; they never earn a check-in.
> A stop dressed as honesty is still a stop. Report uncertainty at the END.
> Ask only if proceeding is unsafe or destructive — never "is my fix right?".
>
> Details: [Working with the user](#no-stopping) · `feedback_never_stop_until_done`

Convert TES4 (Oblivion) master/plugin files to TES5 (Skyrim) format.

| Stage | Package | Responsibility |
|---|---|---|
| Export | `tes4_export` | Reads TES4 binary, dumps every record to KEY=VALUE text. **Pure dump — no transformations.** |
| Import | `tes5_import` | Reads the text, writes a binary TES5 ESM/ESP. **All TES4→TES5 transformations live here.** |
| Assets | `asset_convert` | Meshes, textures, SpeedTree, collision, sound, LOD, BSA packing |
| Scripts | `script_convert` | TES4 script → Papyrus |

`convert.py` orchestrates all stages. Quick start:

```bash
python convert.py -f Oblivion.esm      # full pipeline for one file
python -m pytest tests/test_import.py -v
```

See [docs/pipeline_reference.md](docs/pipeline_reference.md) for all commands,
caching, skipped record types, the export text format, and the directory layout.

---

## Critical Rules

### Process

- **Do one bug at a time.** Make the edits before moving to the next. If you find
  another bug while FIXING, fix it too — but if the task was a question, a plan,
  an analysis, or a doc edit, REPORT it and do not write code.
- **Work in the order the prompt presents.** Highest priority first.
- **Never stop mid-task to report or ask.** Finish everything, verify, then reply.
- **All fixes must be generic.** Never patch to satisfy a single record or file.
  Oblivion and Nehrim are only the test files — we never know what plugin this
  runs on.
- **The goal is COMPLETE conversion.** Don't strip things out because the
  conversion would be complicated.
- **If you don't see the problem described, the test data is not stale** — there
  is always a REAL problem to find.
- **Census vanilla before calling something wrong.** If Skyrim.esm or the DLCs do
  the same thing at scale, it is legal and is not your bug — several docs record
  "verified vanilla-legal, don't fix this" for exactly the things that looked
  broken. Conversely, "all 3,740 vanilla records write 0 here" is the strongest
  possible evidence for what to write.
- **Prefer the engine's own mechanism over a Papyrus/script approximation.** Force
  greet is a package, not a function call; `SetAlert` is native, not
  `DrawWeapon()`. Check for a real equivalent before declaring one absent — the
  wikis under-document both games.
- **A symptom's cause is often several layers from the symptom.** Frozen NPCs have
  traced to navmesh, condition params, package data, and behavior graphs in turn.
  Confirm the mechanism before fixing; a plausible story that explains the symptom
  is not yet a diagnosis.
- <a id="master-blindness"></a>**IF THE PLUGIN HAS MASTERS, SUSPECT MASTER-EXPORT
  BLINDNESS FIRST.** Morrowind_ob and the ESPs depend on Oblivion.esm (Nehrim and
  Oblivion are standalone). The recurring defect: an import phase indexes only
  `by_type` — the CURRENT plugin's export — and never consults
  `ctx.master_export`, so an actor's master-owned packages, items, scripts or
  refs resolve to nothing and the feature silently dies.
- Don't preserve backwards compatibility. Delete code that is no longer used.
- Keep files under ~1000 lines; split by responsibility when one grows.
- <a id="tools-first"></a>**CHECK `tools/` BEFORE BUILDING ANYTHING BESPOKE.**
  ~95 tools already exist and one probably answers your question — the full
  catalogue is [docs/python_tools_reference.md](docs/python_tools_reference.md).
  The order is:
  1. **Use** the existing tool.
  2. If it *almost* fits, **extend or fix it** — new flags, wider output. Never
     write a parallel script that duplicates a tool's job, and never leave a
     broken tool in place while working around it.
  3. Only if nothing is close, write a new one — and **add its entry to
     `python_tools_reference.md` in the same pass**, before you report back. An
     undocumented tool is one the next session will rebuild from scratch.
- Put throwaway files in `temp/`. Don't write one-off scripts with hardcoded
  output — `tools/` scripts take arguments and produce general output, so they are
  reusable next time.
- **Always record new learnings** in this file or, more likely, the relevant `docs/` file.
- **Findings go in `docs/`, NOT just memory.** Memory is per-machine; a doc is
  the only copy another computer sees.
- **KEEP THIS FILE EXTREMELY TERSE.** Every rule is the shortest sentence that
  states it. No rationale, history, worked examples, or "rewritten on <date>"
  notes — those go in `docs/`, linked. Editing a rule means it gets SHORTER.
- Docs can be wrong: they sometimes describe fixes that were never implemented.
  Grep the source before claiming a mechanism exists, and fix the doc.
- Test scripts must print as they go, so a 120s timeout still yields output.
- **LISTEN CAREFULLY to EXACTLY what the user's prompt says**. Seek to understand any implementation ideas instead of using your pre-conceived notions
- **A MECHANISM THE USER NAMES IS THE LEAD. BUILD IT TO COMPLETION.**
  Never abandon it for a cheaper substitute, and never because it is
  "invasive" / "touches too much" / "needs new records" — **COST IS NEVER A
  REASON, ONLY CORRECTNESS IS.** If it truly cannot work, PROVE it with a
  measurement and say so; never silently swap in something smaller.
- If you need to continue iterating on an idea with only marginal improvments in some areas and regressions in another, your idea is likely incorrect and you need to find another one
- **Look for the AUTHORED indicator** If you need to resort to heuristics, your approach is most likely incorrect. Remember, everything in the original plugin works for a reason
- **Pay close attention to performance** This needs to run quickly on a modest PC. If your new code makes a step run significantly slower than it did before you **MUST optimize**. Python first optimizations, and then native C++ if necessary.
- If the user gives multiple constraints to a bug, your fix MUST satisfy ALL of them
- If the user asks for a plan or analysis. DO NOT BUILD until you have the goahead. Overeager code development is the opposite of helpful

### <a id="regression-read-the-commits"></a>🛑 IF IT IS A REGRESSION, READ THE COMMITS

**"This used to work" means the cause is ON A `+` LINE IN A RECENT DIFF.**

```bash
git log --oneline --since=3.days                 # candidates
git show <sha> -- script_convert/ tes5_import/   # READ THE + LINES
```

Read every candidate diff BEFORE rebuilding at an old commit, writing a probe
script, or re-examining the same record again — those all waste the cycle and
the probe is usually wrong. "Nothing changed in this window" is a broken tool,
not proof.

A generated `.psc`/`.pex` is an artifact too: `--import-only` does NOT
regenerate scripts, so a behavioural regression means reading
`script_convert/` diffs and running `--scripts-only`.

### Verifying your work

**Always check theories against several of these** before acting:

1. The Skyrim exe at `D:\Other Games\Skyrim Anniversary Edition\SkyrimSE.exe`
   — GOG/AE, **not DRM-packed**, so it disassembles statically (the *Steam*
   copy is encrypted). Crash logs map across via the Address Library.
   Disassembly is a first resort, not a last one.
2. The Oblivion/Nehrim install at `D:\Other Games\Nehrim At Fate's Edge\Data`.
3. xEdit source at `references/xEdit` — `Core/` documents the binary structure of
   every record type. This is the first stop for any format question. Or if working with meshes, go to the Nifskope source at `references/Nifskope`
4. The Skyrim.esm dump at `references/Skyrim.esm`, real Skyrim.esm, and
   `references/Skyrim Meshes`. **Verify binary layout against BOTH the xEdit
   definition AND a real Skyrim.esm dump — never skip either.**
5. UESP / CK wiki via `python tools/uesp_lookup.py`. **Never WebSearch or
   WebFetch for these** (they 403). An empty result means fix the query.
6. A web search for other authoritative sources.
7. The Papyrus logs from the last in-game run — read them to diagnose a runtime
   symptom (see the directory-purpose table under Hard prohibitions).
8. <a id="attach-to-the-live-game"></a>**The LIVE game process — for any hang,
   ask the user to leave it running and attach.** Beats everything above when
   there is no crash log; the live Steam process disassembles (decrypted in
   memory) with RVAs matching the running build. Recipe:
   `project_refr_angle_normalize_hang`.
9. Failing all the above, add thorough logging for the user's next run — one
   wasted round trip costs them a full build-and-play cycle.

Never attribute a bug to LE-vs-SSE mesh format differences — verify engine
theories externally first.

**A "CLEAN" audit is not an alibi** — if every check passes and the symptom is
real, suspect a VALUE the engine chokes on, not a STRUCTURE it rejects.

### <a id="code-review"></a>🛑 Code review: RUN THE CLAIM, DON'T READ IT

**An unexecuted finding is a GUESS. Delete it — never soften it** to "possible
issue" / "may not handle". Ship it only with a reproduction, a query against
real data, or a failing-then-passing test.

- **Every number is measured or absent.** Never write a count you did not
  compute this session.
- **Read the code, don't infer** Otherwise you will produce confident nonsense.
- Mark verified vs suspected differently
- **Don't nitpick** = no theoretical edge cases, style, or naming.

### Hard prohibitions

- **NEVER `git stash` / `git stash pop`** in this repository.
- **NEVER `git commit` or `git push`.** The user commits after in-game testing.
- **NEVER `git add` / `git rm`** (staging, including staged deletions). Use plain
  `rm`. `git reset` destroys the user's own staging.

  <a id="staging-is-single-use"></a>**Authorization to stage is SINGLE-USE and
  CHUNK-SCOPED** — it covers that one action, not your next edit or turn. Stage
  HUNKS via `git apply --cached`, never whole files (they carry the user's own
  work). **NEVER `git add -A` / `-u` / `.`** If unsure whether authorization
  still applies, it does not.
- **NEVER go snooping in the live, heavily-modded SSE install.** It is full of
  other mods' assets, so nothing you find there tells you anything about this
  converter. In particular: **never inspect it to check whether your changes were
  deployed or installed correctly** — trust the user's deployment statements, and
  never argue with an in-game result by reading their setup.
  Each external directory has ONE sanctioned purpose:
  | Path | Use it for | Not for |
  |---|---|---|
  | `D:\Other Games\Skyrim Anniversary Edition\` (GOG/AE) | exe decompilation | assets, deployment checks |
  | Oblivion / Nehrim LE install | BSA files and NIFs | anything Skyrim-side |
  | The modded SSE install | **Papyrus logs, and reading `Skyrim.esm`** | everything else, especially verifying deployment |
- **Never run the full pytest suite** — only the tests for files you changed.
- **KEEP EVERY TEST COMMAND / SCRIPT UNDER 120 SECONDS. Never set a long
  timeout.** Narrow the scope instead: one cell, not a worldspace; 2-3 NIFs,
  not a tree; one record type, not the whole plugin. Most tools take `--cell` /
  `--max N` / `--workers` for exactly this. If something genuinely cannot be
  scoped down, say so instead of waiting on it. **Does NOT apply to real
  pipeline runs** (`convert.py --import-only` etc.), which take as long as they
  take — see [BUILD EVERY FILE](#build-every-file).
  **Write each result as you compute it; on timeout use what it wrote. Never
  re-run the same sweep at a smaller scope.**
- **NEVER stop mid-task for a status update** — see [no stopping](#no-stopping).

### Working with the user

- **NEVER STOP TO GIVE A MID-SESSION STATUS REPORT.** Not "here's where I am",
  not "should I continue?", not a summary of progress so far. Finish the whole task, then report once. A status update mid-task is a failure, not politeness. If something the user has asked for remains unsolved, YOU ARE NOT DONE!

  <a id="no-stopping"></a>**Low confidence is NOT an exception.** The tradeoff
  is already decided: **the user would rather you finish and be wrong than stop
  and ask.** Being wrong repeatedly obligates you to keep going — go back to
  "Verifying your work", find a DIFFERENT mechanism, and build it.

  All stops, however phrased: "which do you want?" (pick one), "want me to X?"
  (do X), findings + a question instead of findings + a fix, "I found the cause
  but haven't built it" (build it), and <a id="honesty-costume"></a>**confessing
  a bad track record as the reason to stop — a stop in the costume of honesty
  is still a stop.**

  **Uncertainty is reported, never resolved by asking.** State the assumption in
  the FINAL REPORT, having done the work under it. Finish every unblocked part
  and say what was blocked. A question is only ever permitted when proceeding
  would be UNSAFE or DESTRUCTIVE (deleting data, force-pushing,
  [causing FormID drift](#formid-drift)) — never because you are unsure
  whether your fix is right. Asking the user to DO something is a request, not
  a stop — but **only when they are the ONLY one who can do it** (leave the
  game running, play a build). Anything you could do yourself, DO. The user's
  time is worth far more than yours.
- **Measure the invariant the user asked for, not a proxy for it.**
- **Trust the user's in-game test results as ground truth.** Never question
  whether they tested something, and never rebut a reported result with file
  timestamps or a reconstructed timeline. (Reading Papyrus logs to *diagnose* is
  encouraged — using them to dispute the user's report is not.)
- **On a hang, ask EARLY for the game to be left running with the bug onscreen**
  ("don't close it, I can attach to it") — nearly free for the user, and it pins
  the exact faulting state. See [the live game process](#attach-to-the-live-game).
- <a id="build-every-file"></a>**BUILD EVERY FILE YOUR CHANGES TOUCH, before reporting back.** The user should
  be able to launch the game and verify immediately — never leave them to work out
  which stage to re-run, and never hand back a change that only compiles in
  theory. Map the files you edited to stages and run each one into `output/`:

  | Changed | Run |
  |---|---|
  | `tes4_export/` | `python convert.py -f <plugin> --export-only` |
  | `tes5_import/` (records, navmesh, packages, dialogue) | `--import-only` |
  | `script_convert/` | `--scripts-only` (compiles .psc → .pex) |
  | `asset_convert/nif_converter.py`, collision, skin | `--meshes-only` |
  | `spt_*` | `--speedtrees-only` |
  | sound conversion | `--sounds-only` |
  | LOD | `--lod-only` |
  | BSA packing | `--pack-only` |

  Touching several areas means running several stages — import *and* scripts if
  you changed both. Other flags: `--creatures-only`, `--extract-only`,
  `--prune-textures-only`, `--pack-zip-only`. Report what you built and any
  failures verbatim; if a stage genuinely cannot be run, say which and why rather
  than staying silent.

  **An asset-only mod (no ESP/ESM) is still a `-f` target.** `--import-mod`
  registers a pseudo-plugin for it, so its asset stages run exactly like any
  other plugin's: `python convert.py -f "Tamriel Landscape Pack"
  --speedtrees-only`. Only the record stages (export/import/scripts/creatures)
  are skipped. `python convert.py --list-mods` shows them.
- **NEVER START A BUILD UNTIL YOU ARE SURE THE FIX IS CORRECT.** Finish every
  edit, run the targeted tests, and re-read your own diff FIRST.
- **A FULL `--meshes-only` REBUILD IS LONG AND EXPENSIVE (~20,000 meshes, many
  minutes at 100% CPU). Never launch one lightly.** Rebuild ONLY the meshes your
  change affects. Reserve the full stage for changes that genuinely touch every
  mesh, and say so when you run one.
- **Build the mesh the user named, in the PLUGIN the user named** If they say a mesh is a Nehrim issue, rebuild it under `Nehrim.esm` even if there is a same-named mesh under `Oblivion.esm`
- **Never run two CPU-saturating jobs at once.** The order is **targeted tests
  first, then builds, one at a time.** While one runs, do not start pytest, a
  mesh sweep, or a second build — wait for the completion notification, then
  run the next. While waiting, WAIT — don't burn tokens on filler work.
- **While iterating on a repeated failure, don't write tests, update docs, or ANYTHING until
  the fix is CONFIRMED in-game.** Each round trip costs the user a full
  build-and-play cycle, so spend it on the diagnosis and the candidate fix only.
  Tests and docs written against an unconfirmed theory usually just encode the
  wrong theory and have to be rewritten. Once the user confirms, then add the
  regression test and the doc note.
- **When a fix doesn't work, don't continue to re-apply a variant of the same theory without new evidence.** Two
  failed attempts on one theory likely means the theory is wrong — go back to the
  sources in "Verifying your work" and find a *different* mechanism. Say plainly
  that the previous explanation was wrong rather than layering another guess on
  top of it.
- **Report honestly.** If something is untested, say so; if you skipped part of
  the scope, say which part and why. Never describe an unverified change as
  working.

### Assets and references

- **`references/` is for comparison/analysis ONLY — the pipeline must NEVER
  resolve runtime assets through it.** Vanilla Skyrim files are fetched via
  `asset_convert/skyrim_assets.py` (cache in `export/skyrim_assets/`, else
  auto-extracted from the SSE BSAs via registry-detected install).
- `references/` subfolders (`NIFConverter/`, `xEdit/`, `UESP/`, `nifskope`) are
  other projects — reference only.
- **LE assets are SSE-compatible.** Never dig through SSE-format assets/BSAs.
  BSA meshes are SSE-format; read them with `asset_convert/sse_nif.py`
  (`read_nif` converts BSTriShape graphs to LE NiTriShape graphs in-memory;
  pyffi Patch 8 supplies the SSE read layouts). Output is always written LE
  (uv2=83), which SSE loads natively.
- **The LE-compatibility rule above does NOT extend to `.hkx`: every hkx we ship
  is 64-bit.** `convert_hkx_to_amd64()` is the mandatory final step
- Use `references/nif [version].xml` for valid Skyrim NIF behavior — newer and
  more correct than pyffi 2.2.3's bundled version. Use pyffi with the clock
  monkey patch when analyzing.
- **Never batch-test many NIFs.** Test 2-3 specific to the bug. If a batch is
  genuinely required, use full workers (`cpu_count() - 1`) — single-threaded runs
  cap at 10 NIFs. Compare an `output/` mesh against the `export/` mesh and a few
  similar Skyrim meshes.

### Performance and memory

- Use multiprocessing, not threads, for pure-Python work; **ThreadPoolExecutor is
  only for I/O and subprocesses.** The output ESM must stay byte-reproducible.
  Rules and measured results: [docs/performance_notes.md](docs/performance_notes.md).
- **Never exhaust memory**: some pool tools load the ~2.1 GB export index per
  worker. Cap `--workers` or run single-process.
- **<a id="formid-drift"></a>FORMIDS ARE HASHED, NOT COUNTED.**
  `derive_formid(site, key)`. Allocation order is irrelevant — add generators
  anywhere.
  - **`key` must be AUTHORED data** (source FormID, EditorID, TES4 model path),
    never a value we compute — that moves ids and breaks saves.
  - 🛑 **NEVER SHIP DRIFT WITHOUT ASKING FIRST.** Moving even one existing id
    breaks saves. Measure the count, STOP, and ask — this is the unsafe-action
    exception to [never stopping](#no-stopping), not a report-it-afterwards.
    Changing the hash input, region, or `FORMID_SCHEME_VERSION` renumbers
    everything.
  Guarded by `tests/test_formid_determinism.py`; details:
  [performance_notes.md](docs/performance_notes.md#formid-determinism--the-save-game-contract-rewritten-2026-08-17).

### Output paths

`output/Oblivion.esm` is a **FOLDER**, not a file — the .esm goes in
`output/Oblivion.esm/Oblivion.esm`. A write failure there means you are trying to
overwrite a folder with a file, not that a file is locked.

### <a id="shared-navmesh-cache"></a>The shared navmesh cache

Navmesh generation is the slowest import stage; per-cell results are cached and
published as a GitHub Release asset.

```bash
python tools/navmesh_cache.py verify  --plugin Oblivion.esm   # publishable?
python tools/navmesh_cache.py install --plugin Oblivion.esm   # get the cache
python tools/navmesh_cache_hook.py --install                  # gate pushes
python tools/navmesh_cache_hook.py --run                      # publish manually
```

- **NEVER ship `collision_cache.bin`** — it holds Bethesda's Havok triangles
  keyed by asset path. Only our own `navmesh_geom_cache` pickles go in.
- **Never put mtime, absolute paths, or worker counts in a cache key** — they
  are machine-local, so every downloader misses.

Why, and the invalidation/tag contracts:
[world_land_navmesh_notes.md](docs/world_land_navmesh_notes.md#the-shared-navmesh-cache--design-rationale).

---

## Documentation Map

Deep reference material lives in `docs/` so this file stays short. Load the
relevant doc when working in that area.

### Pipeline & architecture
| Doc | Covers |
|---|---|
| [pipeline_reference.md](docs/pipeline_reference.md) | Orchestrator commands, stages, caching, SKIP_TYPES, export text format, directory layout, SSEEdit verification, running off Windows (Wine + native build) |
| [python_tools_reference.md](docs/python_tools_reference.md) | Per-module and `tools/` debug utility command reference |
| [performance_notes.md](docs/performance_notes.md) | Parallelism rules, determinism contract, navmesh optimisation results |
| [override_conversion.md](docs/override_conversion.md) | Converting plugins with TES4 masters: export-diff authorship, GRUP nesting, ONAM, cell buckets, injected records |
| [TES5_Binary_Format.md](docs/TES5_Binary_Format.md) | TES5 binary structure reference |
| [TES4_Record_Definitions.md](docs/TES4_Record_Definitions.md) | TES4 record structure reference |
| [xedit_scripting_reference.md](docs/xedit_scripting_reference.md) | xEdit Pascal API + globals (historical — the pipeline is pure Python now; kept for ad-hoc verification scripts) |

### Records & data
| Doc | Covers |
|---|---|
| [record_mapping_reference.md](docs/record_mapping_reference.md) | Full TES4→TES5 record type mapping, OBND/structural requirements, skipped/problem records, skill/weapon/biped-slot/enchantment tables, Skyblivion conversion rules |
| [magic_conversion_plan.md](docs/magic_conversion_plan.md) | SPEL/ENCH/MGEF: dropped effect families, phantom effect codes, archetype mapping, ARTO/PROJ/SEFF |
| [weather_climate_conversion.md](docs/weather_climate_conversion.md) | WTHR/CLMT: the WRLD→CNAM→CLMT→WLST chain, NAM0 slot remap, cloud-speed units, DALC weights |

### Actors, AI & dialogue
| Doc | Covers |
|---|---|
| [package_ai_contracts.md](docs/package_ai_contracts.md) | CTDA param remapping (the crash rule), PTDA Distance, Ambush→approach, force-greet packages, quest priority band |
| [package_conversion_plan.md](docs/package_conversion_plan.md) | PACK template model + vanilla census (implemented — the design behind `pack_converter.py`) |
| [package_conversion_audit.md](docs/package_conversion_audit.md) | Full PACK audit (2026-08-17): 5 measured gaps (PTDT type-1 unhandled, master-blind PackagePlan, 48 ungated packages) + what is verified correct |
| [dialogue_conversion_notes.md](docs/dialogue_conversion_notes.md) | DIAL/INFO/QUST/DLBR/DLVW implementation, voice type routing, AddTopic unlocks, GetIsID injection |
| [dialogue_engine_contracts.md](docs/dialogue_engine_contracts.md) | Verified engine rules for dialogue routing; **speak-as lines = `Say(topic, None, inHead)` on a voiced TACT stand-in** |
| [dialogue_transfer_gaps.md](docs/dialogue_transfer_gaps.md) | Measured gaps: what Oblivion dialogue does NOT survive conversion, with counts from both emulators |
| [ambient_dialogue_channel_plan.md](docs/ambient_dialogue_channel_plan.md) | Oblivion's 3 delivery channels vs Skyrim's 2; constant quipping, NPC-to-NPC topics in the player menu; **the NPC-to-NPC conversation scheduler Skyrim lacks** and the driver quest that replays quest-advancing chains |
| [QUEST_AUDIT.md](docs/QUEST_AUDIT.md) | Quest completability audit via the walkthrough emulator (2026-07-17, all 390 QUSTs) |
| [creature_conversion.md](docs/creature_conversion.md) | CREA→actor: behavior graphs, HKX skeleton/animation/ragdoll, creature records |
| [creature_race_equivalence.md](docs/creature_race_equivalence.md) | Oblivion creature ↔ vanilla Skyrim race map (exact/near tiers) for a possible "use the vanilla creature" option; which creatures have NO equivalent |
| [vanilla_creature_swap_plan.md](docs/vanilla_creature_swap_plan.md) | PLAN (unimplemented): override-ESP + GUI to swap exact-match creatures to vanilla; race identity = (folder, NIFZ body set), NOT folder |
| [vanilla_item_swap_plan.md](docs/vanilla_item_swap_plan.md) | PLAN (unimplemented): item/ingredient/clutter **and WEATHER** swap; model-swap vs full-reference modes, OBND size+orientation gate, PIL preview renderer |
| [item_swap_table.md](docs/item_swap_table.md) | Per-item MISC/INGR swap recommendations with measured size ratios and verdicts (OK/SCALE/ROT/REJECT) |
| [horse_rideability_plan.md](docs/horse_rideability_plan.md) | Rideable horses: RACE Mount Data, horse/rider graph pair, rider-animation sourcing |

### Scripts
| Doc | Covers |
|---|---|
| [papyrus_conversion_notes.md](docs/papyrus_conversion_notes.md) | TES4→Papyrus mapping, paired on/off soft-lock trap, **Say() timers = `TES4Polyfill.SayLine` (engine-reported line length; fragments never write timers)**, **StopQuest = `Stop()` (a run-bit global was tried and REVERTED)**, syntax traps, OBSE constructs |
| [Script_Conversion_Plan.md](docs/Script_Conversion_Plan.md) | Script conversion scope, counts, block/variable distributions |
| [quest_script_conversion_audit.md](docs/quest_script_conversion_audit.md) | Which quest scripts have been read against their originals (don't re-audit), defects found, and verified-correct behaviours not to "fix" |
| [skse_conversion_audit.md](docs/skse_conversion_audit.md) | SKSE/OBSE function coverage audit |
| [skyrim_commands.md](docs/skyrim_commands.md) | Raw table of Skyrim script command IDs, names, and argument types |
| [php_scriptconverter_analysis.md](docs/php_scriptconverter_analysis.md) | How Skyblivion's AST-based PHP converter works vs our regex approach — prior art, not a dependency |

### World, meshes & navmesh
| Doc | Covers |
|---|---|
| [nif_conversion_notes.md](docs/nif_conversion_notes.md) | NIF deep-dive: bhk collision/MOPP/CMS, particles, FlameNode grafting, worn armor/shields/furniture markers, skin retargeting, clutter physics, terrain LOD, SpeedTree |
| [speedtree_engine_decomp.md](docs/speedtree_engine_decomp.md) | SpeedTreeRT decompiled from Oblivion.exe: RNG, child placement/count, spline eval, level struct, parse-stage map. |
| [world_land_navmesh_notes.md](docs/world_land_navmesh_notes.md) | PGRD→NAVM/NAVI algorithm, LAND record structure, landscape TXST, world-map cloud banks (WRLD MODL) |
| [navmesh_corridor_redesign.md](docs/navmesh_corridor_redesign.md) | The corridor-ribbon navmesh model |
| [ck_navmesh_generation.md](docs/ck_navmesh_generation.md) | How the CK generates navmesh (Recast), defaults, the voxel-vs-world units trap |
| [ck_reference_init_hang.md](docs/ck_reference_init_hang.md) | The "Initializing References" hang: unchecked XTEL destination grid lookup, the GRUP-order ref deletion, and the stack-walk-first hang methodology |
| [CHANGES_since_0.606.en.md](docs/CHANGES_since_0.606.en.md) | Changelog for the seven CK load fixes (RU: [CHANGES_since_0.606.ru.md](docs/CHANGES_since_0.606.ru.md)) |

### Skills
| Skill | Covers |
|---|---|
| `oblivion-dialog-system` | Vanilla TES4 dialogue/voice/quest records |
| `skyrim-dialog-system` | Vanilla TES5 dialogue/voice/quest records |
| `oblivion-to-skyrim-dialog` | TES4→TES5 dialogue/quest/voice mapping |

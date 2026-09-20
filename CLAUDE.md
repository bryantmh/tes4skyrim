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

See [docs/reference/pipeline.md](docs/reference/pipeline.md) for all commands,
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
- 🛑 **FO3/FNV-SPECIFIC CODE GOES IN ITS OWN FILE.** Put it in `<stage>_<game>.py` beside the stage — `tes4_export/export_falloutnv.py` — and leave the main file a call site.
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
  ~147 tools already exist and one probably answers your question — the full
  catalogue is [docs/reference/python_tools.md](docs/reference/python_tools.md).
  They live in `tools/<folder>/`: `generators` (code imports their output —
  never delete blind), `release`, `validate`, `audit`, `live`, `disasm`, `nif`,
  `creature`, `dialog`, `script`, `lod`, `esm`, `navmesh`, `misc`.
  The order is:
  1. **Use** the existing tool.
  2. If it *almost* fits, **extend or fix it** — new flags, wider output. Never
     write a parallel script that duplicates a tool's job, and never leave a
     broken tool in place while working around it.
  3. Only if nothing is close, write a new one — and **add its entry to
     `python_tools.md` in the same pass**, before you report back. An
     undocumented tool is one the next session will rebuild from scratch.
- <a id="one-off-goes-in-temp"></a>**A SCRIPT THAT CHASES ONE BUG GOES IN
  `temp/` or your scratchpad, NOT `tools/`.** A tool re-answers its question on NEW input; if
  nothing new would change its output, it is a one-off. A/B and bisect
  harnesses, censuses whose answer ships as a constant, and anything naming one
  plugin/mesh/creature in code are one-offs — finding to `docs/`, script to
  `temp/`. A good docstring does not make one reusable.
- Put throwaway files in `temp/` or your scratchpad. Don't write one-off scripts with hardcoded
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
- Deletion and Simplification is an IMPORTANT part of the process. A change that removes or only adds a few total lines of code is FAR SUPERIOR to one that adds many lines. More does not equal better. Strive to have as low of a cyclomatic complexity as possible
- If the hook triggers for old debt, don't try to get around it as fast as possible or line golf. Take the time to examine the entire surrounding file and fix it properly. You have the time. If you need to separate out a file, pull out the RIGHT thing, not necessaryily the thing you are working on.
- Docstrings should contain real and important function information. Story content only and always belongs in a see: tag. See tags should ALWAYS have an anchor.
- Duplicated code is a big no-no. Check if something has been built first and if it has either point to that code instead or pull it out into a shared function
- Avoid the chicken and egg problem when updating files. For example, adding an import without also adding its call in the same write will trigger the hook and prevent the write
- If you want to use subagents, ASK first
- If you do something that would cause a cache, such as the collision cache to generate differently, you MUST iterate its version
- Morrowind conversion has TWO modes. One that uses its authored masters (Morrowind, Tribunal, Bloodmoon) and Morroblivion mode, which uses Morrowind_ob.esm and the Morroblivion-Morrowind compat patch. BOTH paths MUST be tested and verified when doing Morrowind work.

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

1. **Unpacked Skyrim exes — one per build**, in
   `C:\Program Files (x86)\Steam\steamapps\content\app_489830\depot_489833`:
   `SkyrimSE.<version>.unpacked.exe` (1.5.97, 1.6.659, 1.6.1170, 1.7.104) and
   `SkyrimVR.exe.unpacked.exe` (1.4.15). All disassemble statically; a *retail*
   Steam copy is encrypted. Crash logs map across via the Address Library.
   Disassembly is a first resort, not a last one.
   **For initial work, pick 1.6.1170, which is what the user plays** Use the others for additional verification. See
   [address_library_formats.md](docs/reference/address_library_formats.md#two-id-generations).
2. <a id="ck-is-a-source"></a>**`CreationKit.exe` (Steam) — NOT DRM-packed, and
   the BEST source for why a record is REJECTED.** Asserts carry file+line, and
   it keeps 1,114 Bethesda source paths, 17k diagnostic strings, and 433 record
   editor dialogs the game strips. `tools/disasm/ck_srcpaths.py`, `ck_strref.py`,
   `skyrim_disasm.py --exe <ck>`. Runtime behavior still comes from item 1;
   the CK can disagree with the game ([ck_vs_game_missing_objects.md](docs/commentary/ck_vs_game_missing_objects.md)).
   Details: [ck_exe_as_a_source.md](docs/commentary/ck_exe_disassembly.md).
3. The Oblivion/Nehrim install at `D:\Other Games\Nehrim At Fate's Edge\Data`.
4. xEdit source at `references/xEdit` — `Core/` documents the binary structure of
   every record type. This is the first stop for any format question. Or if working with meshes, go to the Nifskope source at `references/Nifskope`
5. The Skyrim.esm dump at `references/Skyrim.esm`, real Skyrim.esm, and
   `references/Skyrim Meshes`. **Verify binary layout against BOTH the xEdit
   definition AND a real Skyrim.esm dump — never skip either.**
6. UESP / CK wiki via `python tools/misc/uesp_lookup.py`. **Never WebSearch or
   WebFetch for these** (they 403). An empty result means fix the query.
7. A web search for other authoritative sources.
8. The Papyrus logs from the last in-game run — read them to diagnose a runtime
   symptom (see the directory-purpose table under Hard prohibitions).
9. <a id="attach-to-the-live-game"></a>**The LIVE game process — for any hang,
   ask the user to leave it running and attach.** Beats everything above when
   there is no crash log; the live Steam process disassembles (decrypted in
   memory) with RVAs matching the running build. Recipe:
   `project_refr_angle_normalize_hang`.
10. Failing all the above, add thorough logging for the user's next run — one
   wasted round trip costs them a full build-and-play cycle. so this must be done sparingly and thoroghly to be worth it
11. When working on Morrowind code, the FULL OpenMW source code is in the references folder

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

- 🛑 **NEVER RUN A BARE SHELL COMMAND.** Every one goes through
  `python tools/validate/safe_run.py <command>`, which gates the `.py` files
  the command wrote. The hook refuses anything else. See [the wrapper](#safe-run).
- 🛑 **UNLINK EVERY JUNCTION BEFORE REMOVING A WORKTREE.** Linking live
  `export/`/`output/` into one is fine; the TEARDOWN is what kills — both
  `git worktree remove --force` and `rmtree` FOLLOW junctions and delete the
  real tree. Order: `os.rmdir()` each link, verify each target still has its
  files, then remove. `is_junction()` misses them — track links as you create
  them. Both dirs are gitignored, so nothing restores them.
- 🛑 **NEVER OVERWRITE AN ARTIFACT TO SET UP AN A/B OR COMPARISON.** Work on
  scratchpad copies and point the tool at them. Regenerating wastes the user's
  time, and it destroys the baseline the comparison needed.
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
  | `...\content\app_489830\depot_489833\` (unpacked exes) | exe decompilation | assets, deployment checks |
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
  | `asset_convert/nif/nif_converter.py`, collision, skin | `--meshes-only` |
  | `spt_*` | `--speedtrees-only` |
  | sound conversion | `--sounds-only` |
  | LOD | `tools/release/create_lod.py --worldspaces <EDID>`, NEVER `--lod-only` |
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
- 🛑 **A `--*-only` FLAG IS A STAGE, NOT A SCOPE** — `--lod-only` bakes every
  qualifying worldspace, masters' included. Confirm the target from the first
  output lines before calling a build running; a banner is not progress.
- 🛑 **LOD BUILDS ONLY VIA `tools/release/create_lod.py --worldspaces <EDID>`,
  NEVER `--lod-only`** — a worldspace bakes once from its owner plus every
  plugin as an overlay. `--dry-run` first; the plan names owner and overlays.
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
- 🛑 **REPLY IN PLAIN, ORDINARY WORDS** — the way you would say it out loud.
  "I keep my own list", not "the queue is DLL-owned state". Jargon in a short
  sentence is not concise, just unreadable.
- We aren't British. No "colour", "centre" or the like
- No allowlist or blocklist, it's whitelist and blacklist
- Never tell the user you can't look another session's transcript. They are files on disc. Yes you can.

### Assets and references

- **`references/` is for comparison/analysis ONLY — the pipeline must NEVER
  resolve runtime assets through it.** Vanilla Skyrim files are fetched via
  `asset_convert/sources/skyrim_assets.py` (cache in `export/skyrim_assets/`, else
  auto-extracted from the SSE BSAs via registry-detected install).
- `references/` subfolders (`NIFConverter/`, `xEdit/`, `UESP/`, `nifskope`, `openmw`, etc.) are
  other projects — reference only. Note that these are not the ONLY references in that folder. Check before guessing or assuming a reference doesn't exist
- <a id="ck-wiki-offline"></a>**What a Papyrus native DOES: `references/SkyrimCKWiki_210522/skyrim/<Func>_-_<Script>.html`.
  Grep it before describing one — never invent semantics.** Oblivion:
  `references/cs_wiki/` (.txt).
- **LE assets are SSE-compatible.** Never dig through SSE-format assets/BSAs.
  BSA meshes are SSE-format; read them with `asset_convert/nif/sse_nif.py`
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
  Rules and measured results: [docs/commentary/performance.md](docs/commentary/performance.md).
- 🛑 **EVERY `subprocess` CALL IN THE PIPELINE PASSES `**POPEN_FLAGS`**
  (`subprocess_flags.py`). Without it a per-file stage opens one console window
  per file under `pythonw`. Loose assets in a shared Data folder belong to the
  MASTERLESS plugin only — gate on `_is_masterless`, or every expansion
  re-copies and re-transcodes its master's tree.
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
  [performance_notes.md](docs/commentary/performance.md#formid-determinism--the-save-game-contract-rewritten-2026-08-17).

### Output paths

`output/Oblivion.esm` is a **FOLDER**, not a file — the .esm goes in
`output/Oblivion.esm/Oblivion.esm`. A write failure there means you are trying to
overwrite a folder with a file, not that a file is locked.

### <a id="shared-navmesh-cache"></a>The shared navmesh cache

Navmesh generation is the slowest import stage; per-cell results are cached and
published as a GitHub Release asset.

```bash
python tools/navmesh/navmesh_cache.py verify  --plugin Oblivion.esm   # publishable?
python tools/navmesh/navmesh_cache.py install --plugin Oblivion.esm   # get the cache
python tools/navmesh/navmesh_cache_hook.py --install                  # gate pushes
python tools/navmesh/navmesh_cache_hook.py --run                      # publish manually
```

- **NEVER ship `collision_cache.bin`** — it holds Bethesda's Havok triangles
  keyed by asset path. Only our own `navmesh_geom_cache` pickles go in.
- **Never put mtime, absolute paths, or worker counts in a cache key** — they
  are machine-local, so every downloader misses.
- **The cache tag is a SHA-1 over the BYTES of every `tes5_import/navmesh/*.py`**
  (`navmesh/pool.py:navmesh_geom_cache`). Any edit there — whitespace included —
  invalidates every downloader's cache, so republish when you touch it.
  `NAVMESH_PATHS`/`NAVMESH_FUNCS` in `navmesh_cache_hook.py` gate the push; a
  function added directly below a gated one reads as a navmesh change because
  git's `-U0` hunk header names the function ABOVE an insertion. Check with
  `navmesh_cache_hook.py --check`.

Why, and the invalidation/tag contracts:
[world_land_navmesh_notes.md](docs/commentary/tes5_import_navmesh.md#the-shared-navmesh-cache--design-rationale).

---

## Documentation Map

Deep reference material lives in `docs/`, sorted by KIND.
**[docs/README.md](docs/README.md) is the index and says where a NEW document goes.**

| Folder | Holds |
|---|---|
| [reference/](docs/reference/) | What a format or contract IS — stable, no dates |
| [commentary/](docs/commentary/) | Why the SHIPPED code is the way it is — named `<package>_<subsystem>.md` after the code it explains, opens with `**Code:**`. Measurements, engine behaviour, reverted attempts. The DEFAULT |
| [plans/](docs/plans/) | Designed, NOT yet built |
| [audits/](docs/audits/) | A dated sweep over a corpus, with counts |
| [assets/](docs/assets/) | Images and icons; `banner.png`/`favicon.ico` load at RUNTIME |

### Code rules — EVERY `.py` in the repo

🛑 <a id="safe-run"></a>**RUN EVERY SHELL COMMAND THROUGH THE WRAPPER** —
`python tools/validate/safe_run.py <command>`. It runs in your shell, streams
live, returns the child's exit code, and gates the `.py` files the command
WROTE. A bare command is refused: a heredoc writes a `.py` no gate ever sees.

🛑 **SEARCH WITH THE GREP TOOL, NEVER `grep -r`.** It is ripgrep: honors
`.gitignore`, so it skips `export/`/`output/`/`references/` (tens of GB) and
finishes the whole repo in ~1s

**`grep "a\|b"` silently finds NOTHING** — bash eats the backslash, so grep
gets a literal `|` and still exits 0. Use `grep -E "a|b"` or `-e a -e b`. Zero
matches is a broken query, never evidence about the tree.

🛑 **A BASH HEREDOC EATS BACKSLASHES** — `\a`, `\1`, `\_` become bell, byte 1,
`_`. Never write a Windows path, regex or doc text through `cat <<'EOF'` /
`python - <<'EOF'`. Use the Write tool for the file, and build paths with
`chr(92)` or forward slashes. Applies to the doc-gate hook too.

🛑 <a id="doc-rules"></a>**THE CODE RULES ARE A REQUIREMENT, NOT A GUIDELINE.**
`.claude/hooks/doc_rules_gate.py` runs `--gate-diff` BEFORE an Edit lands and
REFUSES it, charging the lines you changed plus the comments above them.
`--gate-file` scores a WHOLE file, including debt you did not write; use it only
to audit a file before refactoring it. `oversized-files` is a RATCHET: it fires
only when your edit RAISES the count, never for merely being over. **It counts
CODE lines — trimming docstrings or comments CANNOT clear it.** Clear it by
removing or relocating CODE.

- **Prose:** only a docstring, a ONE-line 120-char `#:` attribute doc, or a
  `# ----` heading. A docstring states the CONTRACT, never the why; rationale
  and measurements go in `docs/`, cited by `See: docs/<file>.md#anchor` (the
  gate checks path and anchor).
- **A comment is prose wherever it sits** — the scanner tokenizes, so moving it
  to the end of a line hides nothing, and `# noqa`/`# pragma`/`# type:` score
  like any other comment.
- **Shape:** per function ≤35 statements (NOT lines — reflow moves nothing),
  complexity ≤25, nesting ≤4, ≤10 returns; ≤1000 lines per file; no
  class-level `dict`/`list`/`set`.
- **No dead code:** no unused import or variable, no undefined name, nothing
  unreachable. `code_rules.py --dead-code` is the whole-program sweep.
- 🛑 **IMPORTS GO AT MODULE SCOPE.** A function-local import is a code smell —
  hoist it. Keep one inside a function ONLY to break a real import cycle, and
  say which one in the docstring.
- **Compress, never delete:** keep every measured count, script name and
  mechanism; drop the narration. **No dates.**
- An inline comment means the code cannot state its own intent — fix the code.

### Scripts

🛑 **Before writing ANY `script_convert/` code, run the decision procedure in [script_convert_architecture.md](docs/reference/script_convert_architecture.md) §3** and score the change with `python tools/script/arch_fitness.py --fail-on-regression`.

### World, meshes & navmesh

### Skills
| Skill | Covers |
|---|---|
| `oblivion-dialog-system` | Vanilla TES4 dialogue/voice/quest records |
| `skyrim-dialog-system` | Vanilla TES5 dialogue/voice/quest records |
| `oblivion-to-skyrim-dialog` | TES4→TES5 dialogue/quest/voice mapping |

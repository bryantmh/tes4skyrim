# TES4-to-TES5 Conversion Project — AI Context

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

## <a id="no-stopping"></a>Questions vs. tasks

- **A question gets an answer, not code.** A plan, an analysis, a doc edit, or
  the user thinking out loud about an approach is not permission to build. Build
  only on "do it" / "implement" / "fix". Bugs you notice along the way get
  reported, not fixed.
- **A task gets finished: fixed, built, verified — then one full report.** Don't
  end the turn to give a status update, offer options ("which do you want?" —
  pick one), ask "want me to X?" (do X), or hand back a diagnosis without the
  fix. A one-line note between tool calls is fine. If something asked for is
  still unsolved, you are not done.
- **Low confidence is not a reason to stop.** The user would rather you finish
  and be wrong than stop and ask. Being wrong repeatedly means go back to
  [Verifying](#verifying-your-work) and find a different mechanism. Confessing
  a bad track record as the reason to stop is still a stop.
- **Report uncertainty at the end.** State the assumption you worked under,
  finish every unblocked part, and say what was blocked.
- **You may stop to ask only when:**
  - proceeding is unsafe or destructive (deleting data, force-pushing,
    [FormID drift](#formid-drift));
  - you want to use the `Agent` tool (always ask first, including `Explore`);
  - you need the user to do something only they can do (leave the game running,
    play a build). Anything you could do yourself, do.

## Safety rules

These protect things that are hard or impossible to get back.

- 🛑 <a id="safe-run"></a>**Run every shell command through
  `python tools/validate/safe_run.py <command>`.** It streams output, returns the
  child's exit code, and gates the `.py` files the command wrote. The hook refuses
  bare commands, because a heredoc can write a `.py` no gate ever sees.
- 🛑 **Unlink every junction before removing a worktree.** Linking live
  `export/`/`output/` into a worktree is fine; `git worktree remove --force` and
  `rmtree` both follow junctions and delete the real tree, and both dirs are
  gitignored so nothing restores them. Order: `os.rmdir()` each link, verify each
  target still has its files, then remove. `is_junction()` misses them — track
  links as you create them.
- 🛑 **Never overwrite an artifact to set up an A/B or comparison.** Work on
  scratchpad copies and point the tool at them — regenerating wastes the user's
  time and destroys the baseline.
- 🛑 <a id="formid-drift"></a>**Never ship FormID drift without asking first.**
  Moving even one existing id breaks the user's saves. Measure the count, stop, and
  ask. FormIDs are hashed, not counted — `derive_formid(site, key)` — so allocation
  order is irrelevant and generators can go anywhere, but:
  - `key` must be **authored** data (source FormID, EditorID, TES4 model path),
    never a value we compute.
  - Changing the hash input, region, or `FORMID_SCHEME_VERSION` renumbers
    everything.
  Guarded by `tests/test_formid_determinism.py`; details:
  [performance.md](docs/commentary/performance.md#formid-determinism--the-save-game-contract-rewritten-2026-08-17).
- **Git: the user commits after in-game testing.** Never `git commit`, `git push`,
  `git stash`/`stash pop`, `git add`, or `git rm` (use plain `rm`). Never
  `git reset` — it destroys the user's own staging.
  <a id="staging-is-single-use"></a>If the user authorizes staging, it covers that
  one action only, not your next edit or turn. Stage hunks via
  `git apply --cached`, never whole files (they carry the user's own work), and
  never `git add -A` / `-u` / `.`. If unsure whether authorization still applies,
  it does not.
- **Stay out of the live, heavily modded SSE install** except for its sanctioned
  use. It is full of other mods' assets, so it tells you nothing about this
  converter. Never inspect it to check whether your changes were deployed —
  trust the user's deployment statements.

  | Path | Use it for | Not for |
  |---|---|---|
  | `...\content\app_489830\depot_489833\` (unpacked exes) | exe decompilation | assets, deployment checks |
  | Oblivion / Nehrim LE install | BSA files and NIFs | anything Skyrim-side |
  | The modded SSE install | Papyrus logs, and reading `Skyrim.esm` | everything else, especially verifying deployment |

## Working with the user

- **A fact the user states is ground truth — don't search to confirm it.** Search
  only for what they didn't tell you. If the code later contradicts it, say so in
  the final report.
- **A mechanism the user names is the lead — build it to completion.** Never swap
  in a cheaper substitute, and never drop it because it is "invasive" or "needs new
  records". Cost is never a reason, only correctness. If it truly cannot work,
  prove it with a measurement and say so.
- Build the implementation idea the user describes, not your own preconception
  of it.
- **Do the task asked, at the size asked.** Size the work by the request, never by
  what you don't know: a UI change is a UI change, not an investigation of the
  subsystem behind it. Feeling a task is too big is the signal you've inflated it.
- If the user gives multiple constraints for a bug, the fix must satisfy all of
  them. Work in the order the prompt presents.
- Measure the invariant the user asked for, not a proxy for it.
- **Trust the user's in-game results.** Never question whether they tested
  something or rebut a result with file timestamps. Reading Papyrus logs to
  diagnose is encouraged; using them to dispute the user's report is not.
- **A user report is about their build, not yours.** Diagnose from the log they
  gave plus the source (`tes_runtime/`, `tes5_import/`, `external/`) — "absent
  from my `export/`/`output/`" says nothing about their bug.
- **On a hang, ask early for the game to be left running** with the bug onscreen
  so you can [attach](#attach-to-the-live-game). It's nearly free for the user and
  pins the exact faulting state.
- **Report honestly.** Say what is untested or skipped and why. Never describe an
  unverified change as working.
- **Reply in plain, ordinary words** — "I keep my own list", not "the queue is
  DLL-owned state". American spelling ("color", "center"). Say whitelist and
  blacklist, not allowlist/blocklist.
- Other sessions' transcripts are files on disk — you can read them.

## Approaching a bug

- **Fix one bug at a time**, edits included, before moving on. A second bug found
  while fixing gets fixed too.
- **All fixes must be generic.** Oblivion and Nehrim are only test files — we
  never know what plugin this runs on. Never patch for a single record or file.
- **The goal is complete conversion.** Don't strip something out because
  converting it is complicated.
- If you don't see the described problem, the test data is not stale — there is
  always a real problem to find.
- **Look for the authored indicator.** Everything in the original plugin works for
  a reason; needing a heuristic usually means the approach is wrong.
- **Census vanilla before calling something wrong.** If Skyrim.esm or the DLCs do
  the same thing at scale, it is legal — several docs record "verified
  vanilla-legal, don't fix this". Conversely, "all 3,740 vanilla records write 0
  here" is the strongest evidence for what to write.
- **Prefer the engine's own mechanism over a Papyrus approximation** (force greet
  is a package; `SetAlert` is native, not `DrawWeapon()`). The wikis
  under-document both games, so check for a real equivalent before declaring one
  absent.
- **A symptom's cause is often several layers away.** Frozen NPCs have traced to
  navmesh, condition params, package data, and behavior graphs in turn. A
  plausible story is not a diagnosis — confirm the mechanism before fixing.
- <a id="master-blindness"></a>**If the plugin has masters, suspect master-export
  blindness first.** Morrowind_ob and the ESPs depend on Oblivion.esm (Nehrim and
  Oblivion are standalone). The recurring defect: an import phase indexes only
  `by_type` — the current plugin's export — and never consults
  `ctx.master_export`, so master-owned packages, items, scripts or refs resolve to
  nothing and the feature silently dies.
- **Morrowind has two modes, and both must be tested:** authored masters
  (Morrowind, Tribunal, Bloodmoon), and Morroblivion (Morrowind_ob.esm plus the
  Morroblivion-Morrowind compat patch). The full OpenMW source is in `references/`.

### When an idea isn't working

- **Two failed attempts on one theory means the theory is probably wrong.** Don't
  re-apply a variant without new evidence. Go back to the sources below, find a
  different mechanism, and say plainly that the earlier explanation was wrong.
- **Marginal gains in one place with regressions in another mean the idea is
  wrong** — it will not reach the goal with more iteration. Drop it and find
  another one.
- **While iterating on a repeated in-game failure, write no tests or docs** until
  the user confirms the fix in-game. Each round trip costs them a full
  build-and-play cycle, and tests written against an unconfirmed theory encode the
  wrong theory. After confirmation, add the regression test and the doc note.

### <a id="regression-read-the-commits"></a>Regressions: read the commits

"This used to work" means the cause is on a `+` line in a recent diff.

```bash
git log --oneline --since=3.days                 # candidates
git show <sha> -- script_convert/ tes5_import/   # read the + lines
```

Read every candidate diff before rebuilding at an old commit, writing a probe, or
re-examining the same record. "Nothing changed in this window" means the tool is
broken, not that nothing changed. Generated `.psc`/`.pex` are artifacts too:
`--import-only` does not regenerate scripts, so a behavioral regression means
reading `script_convert/` diffs and running `--scripts-only`.

## <a id="verifying-your-work"></a>Verifying your work

Check theories against several of these before acting:

1. **Unpacked Skyrim exes** in
   `C:\Program Files (x86)\Steam\steamapps\content\app_489830\depot_489833`:
   `SkyrimSE.<version>.unpacked.exe` (1.5.97, 1.6.659, 1.6.1170, 1.7.104) and
   `SkyrimVR.exe.unpacked.exe` (1.4.15). All disassemble statically (a retail
   Steam copy is encrypted); crash logs map across via the Address Library.
   Disassembly is a first resort, not a last one. **Start with 1.6.1170 — the
   build the user plays**; use the others to confirm. See
   [address_library_formats.md](docs/reference/address_library_formats.md#two-id-generations).
2. <a id="ck-is-a-source"></a>**`CreationKit.exe` (Steam)** — not DRM-packed, and
   the best source for why a record is rejected. Asserts carry file+line; it keeps
   1,114 Bethesda source paths, 17k diagnostic strings, and 433 record editor
   dialogs the game strips. Tools: `tools/disasm/ck_srcpaths.py`, `ck_strref.py`,
   `skyrim_disasm.py --exe <ck>`. Runtime behavior still comes from item 1 — the
   CK can disagree with the game
   ([ck_vs_game_missing_objects.md](docs/commentary/ck_vs_game_missing_objects.md)).
   Details: [ck_exe_disassembly.md](docs/commentary/ck_exe_disassembly.md).
3. The Oblivion/Nehrim install at `D:\Other Games\Nehrim At Fate's Edge\Data`.
4. xEdit source at `references/xEdit` — `Core/` documents every record type's
   binary structure; first stop for any format question. For meshes, the NifSkope
   source at `references/Nifskope`.
5. The Skyrim.esm dump at `references/Skyrim.esm`, the real Skyrim.esm, and
   `references/Skyrim Meshes`. **Verify binary layout against both the xEdit
   definition and a real Skyrim.esm dump.**
6. UESP / CK wiki via `python tools/misc/uesp_lookup.py` — never WebSearch or
   WebFetch them (they 403). An empty result means fix the query.
7. A web search for other authoritative sources.
8. Papyrus logs from the last in-game run, to diagnose a runtime symptom.
9. <a id="attach-to-the-live-game"></a>**The live game process.** For a hang with
   no crash log, this beats everything above: the live Steam process disassembles
   (decrypted in memory) with RVAs matching the running build. Recipe:
   `project_refr_angle_normalize_hang`.
10. Failing all the above, add logging for the user's next run. Each round trip
    costs them a full build-and-play cycle, so do it rarely and thoroughly.

- Never blame a bug on LE-vs-SSE mesh format differences — verify engine theories
  externally first.
- **A clean audit is not an alibi.** If every check passes and the symptom is
  real, suspect a value the engine chokes on, not a structure it rejects.
- Docs can be wrong — some describe fixes that were never implemented. Grep the
  source before claiming a mechanism exists, and fix the doc.

## Testing and building

- **Targeted tests only** — the tests for files you changed, never the full suite.
- **Keep every test command or script under 120 seconds**; never set a long
  timeout. Narrow the scope instead: one cell, 2–3 NIFs, one record type (most
  tools take `--cell` / `--max N` / `--workers`). If something can't be scoped
  down, say so. Test scripts print as they go and write each result as computed;
  on timeout, use what was written — never re-run the same sweep smaller. This
  limit does not apply to real pipeline runs.
- **Never run two CPU-saturating jobs at once.** Targeted tests first, then
  builds, one at a time. While one runs, wait for its completion notification —
  don't start pytest, a mesh sweep, or a second build, and don't fill the wait
  with busywork.
- **Don't start a build until you're sure the fix is correct:** edits finished,
  targeted tests passed, your own diff re-read.
- <a id="build-every-file"></a>**Build every stage your changes touch before
  reporting back**, into `output/`, so the user can launch the game immediately:

  | Changed | Run |
  |---|---|
  | `tes4_export/` | `python convert.py -f <plugin> --export-only` |
  | `tes5_import/` (records, navmesh, packages, dialogue) | `--import-only` |
  | `script_convert/` | `--scripts-only` (compiles .psc → .pex) |
  | `asset_convert/nif/nif_converter.py`, collision, skin | `--meshes-only` |
  | `spt_*` | `--speedtrees-only` |
  | sound conversion | `--sounds-only` |
  | LOD | `tools/release/create_lod.py --worldspaces <EDID>` |
  | BSA packing | `--pack-only` |

  Several areas means several stages. Other flags: `--creatures-only`,
  `--extract-only`, `--prune-textures-only`, `--pack-zip-only`. Report what you
  built and any failures verbatim; if a stage can't be run, say which and why.
- An asset-only mod (no ESP/ESM) is still a `-f` target: `--import-mod` registers
  a pseudo-plugin, so its asset stages run normally (`python convert.py -f
  "Tamriel Landscape Pack" --speedtrees-only`); only record stages are skipped.
  `--list-mods` shows them.
- **A `--*-only` flag is a stage, not a scope** — `--lod-only` bakes every
  qualifying worldspace, masters' included. Confirm the target from the first
  output lines; a banner is not progress.
- **LOD builds only via `create_lod.py --worldspaces <EDID>`, never
  `--lod-only`.** A worldspace bakes once from its owner plus every plugin as an
  overlay. Run `--dry-run` first; the plan names owner and overlays.
- **A full `--meshes-only` rebuild is expensive** (~20,000 meshes, many minutes at
  100% CPU). Rebuild only the meshes your change affects; if a change genuinely
  touches every mesh, say so when you run the full stage.
- **Build the mesh in the plugin the user named.** A Nehrim issue rebuilds under
  `Nehrim.esm` even if a same-named mesh exists under `Oblivion.esm`.
- **Never batch-test many NIFs** — test 2–3 specific to the bug. If a batch is
  genuinely required, use `cpu_count() - 1` workers (single-threaded runs cap at 10
  NIFs). Compare the `output/` mesh against the `export/` mesh and a few similar
  Skyrim meshes.
- If a change makes a cache (e.g. the collision cache) generate differently, bump
  its version.

## Writing code

- **Simpler is better.** Deletion and simplification are core to the work: a
  change that removes lines, or adds only a few, beats one that adds many. Keep
  cyclomatic complexity low.
- **No duplicated code.** Check whether it's already built; point to it or pull
  it into a shared function.
- Don't preserve backwards compatibility in code — delete what is no longer used.
  (Save-game compatibility is different: see [FormID drift](#formid-drift).)
- **Performance matters** — this must run quickly on a modest PC. If your change
  makes a step significantly slower, optimize: Python first, then native C++ if
  needed.
- FO3/FNV-specific code goes in its own `<stage>_<game>.py` beside the stage
  (e.g. `tes4_export/export_falloutnv.py`); the main file stays a call site.
- Keep files under ~1000 lines; split by responsibility when one grows.
- <a id="tools-first"></a>**Check `tools/` before building anything bespoke.**
  ~147 tools exist — catalogue in
  [docs/reference/python_tools.md](docs/reference/python_tools.md). Folders:
  `generators` (code imports their output — never delete blind), `release`,
  `validate`, `audit`, `live`, `disasm`, `nif`, `creature`, `dialog`, `script`,
  `lod`, `esm`, `navmesh`, `misc`. Order:
  1. Use the existing tool.
  2. If it almost fits, extend or fix it. Never write a parallel script, and never
     work around a broken tool while leaving it broken.
  3. Only if nothing is close, write a new one — and add it to `python_tools.md`
     in the same pass, or the next session will rebuild it.
- <a id="one-off-goes-in-temp"></a>**A script that chases one bug goes in `temp/`
  or your scratchpad, not `tools/`.** A tool re-answers its question on new input;
  if nothing new would change its output, it's a one-off. A/B and bisect harnesses,
  censuses whose answer ships as a constant, and anything naming one
  plugin/mesh/creature are one-offs — finding to `docs/`, script to `temp/`.
  `tools/` scripts take arguments and produce general output.

### Performance and memory

- Multiprocessing, not threads, for pure-Python work; `ThreadPoolExecutor` only for
  I/O and subprocesses. The output ESM must stay byte-reproducible. Rules and
  measurements: [docs/commentary/performance.md](docs/commentary/performance.md).
- Every `subprocess` call in the pipeline passes `**POPEN_FLAGS`
  (`subprocess_flags.py`) — otherwise a per-file stage opens a console window per
  file under `pythonw`.
- Loose assets in a shared Data folder belong to the masterless plugin only — gate
  on `_is_masterless`, or every expansion re-copies and re-transcodes its master's
  tree.
- Don't exhaust memory: some pool tools load the ~2.1 GB export index per worker.
  Cap `--workers` or run single-process.

### <a id="doc-rules"></a>Code rules (every `.py`) — enforced by a hook

`.claude/hooks/doc_rules_gate.py` runs `--gate-diff` before an Edit lands and
refuses it, charging the lines you changed plus the comments above them.
`--gate-file` scores a whole file including old debt; use it only to audit before
refactoring.

- **When the hook fires on old debt, fix it properly** — examine the whole file,
  no line golf. If a file needs splitting, pull out the right thing, not
  necessarily the thing you're working on.
- `oversized-files` is a ratchet: it fires only when your edit raises the count.
  It counts code lines, so trimming comments can't clear it — remove or relocate
  code.
- Avoid chicken-and-egg writes: e.g. adding an import without its call in the same
  write trips the hook.
- **Prose:** only a docstring, a one-line 120-char `#:` attribute doc, or a
  `# ----` heading. A docstring states the contract, never the story; rationale
  and measurements go in `docs/`, cited by `See: docs/<file>.md#anchor` (the gate
  checks path and anchor — always include the anchor).
- A comment is prose wherever it sits — the scanner tokenizes, so end-of-line
  placement hides nothing, and `# noqa`/`# pragma`/`# type:` count too. An inline
  comment means the code can't state its intent — fix the code.
- **Shape:** per function ≤35 statements (not lines), complexity ≤25, nesting ≤4,
  ≤10 returns; ≤1000 lines per file; no class-level `dict`/`list`/`set`.
- **No dead code:** no unused import or variable, no undefined name, nothing
  unreachable. `code_rules.py --dead-code` is the whole-program sweep.
- **Imports go at module scope.** Keep one inside a function only to break a real
  import cycle, and name the cycle in the docstring.
- When compressing text, keep every measured count, script name and mechanism;
  drop the narration and dates.

### Shell and search gotchas

- **Search with the Grep tool, never `grep -r`.** It honors `.gitignore`, so it
  skips `export/`/`output/`/`references/` (tens of GB) and covers the repo in ~1s.
  Never grep whole data directories — scope it.
- `grep "a\|b"` silently finds nothing (bash eats the backslash). Use
  `grep -E "a|b"` or `-e a -e b`. Zero matches is a broken query, not evidence.
- A bash heredoc eats backslashes (`\a`, `\1`, `\_`). Never write a Windows path,
  regex or doc text through `cat <<'EOF'` / `python - <<'EOF'` — use the Write
  tool, and build paths with `chr(92)` or forward slashes.

### Scripts

Before writing any `script_convert/` code, run the decision procedure in
[script_convert_architecture.md](docs/reference/script_convert_architecture.md) §3
and score the change with `python tools/script/arch_fitness.py --fail-on-regression`.

## Assets and references

- **`references/` is for comparison only — the pipeline must never resolve
  runtime assets through it.** Vanilla Skyrim files come via
  `asset_convert/sources/skyrim_assets.py` (cache in `export/skyrim_assets/`, else
  auto-extracted from the SSE BSAs via the registry-detected install).
- `references/` holds other projects (`NIFConverter/`, `xEdit/`, `UESP/`,
  `nifskope`, `openmw`, and more) — look before assuming a reference doesn't exist.
- <a id="ck-wiki-offline"></a>**What a Papyrus native does:**
  `references/SkyrimCKWiki_210522/skyrim/<Func>_-_<Script>.html`. Grep it before
  describing one — never invent semantics. Oblivion: `references/cs_wiki/` (.txt).
- **LE assets are SSE-compatible** — never dig through SSE-format assets. BSA
  meshes are SSE-format; read them with `asset_convert/nif/sse_nif.py` (`read_nif`
  converts BSTriShape to LE NiTriShape in memory; pyffi Patch 8 supplies the SSE
  read layouts). Output is always written LE (uv2=83), which SSE loads natively.
- **Except `.hkx`: every hkx we ship is 64-bit.** `convert_hkx_to_amd64()` is the
  mandatory final step.
- Use `references/nif [version].xml` for valid Skyrim NIF behavior — newer and more
  correct than pyffi 2.2.3's bundled version. Use pyffi with the clock monkey patch
  when analyzing.
- `output/Oblivion.esm` is a folder; the .esm goes in
  `output/Oblivion.esm/Oblivion.esm`. A write failure there means you're
  overwriting a folder with a file, not that a file is locked.

## <a id="shared-navmesh-cache"></a>The shared navmesh cache

Navmesh generation is the slowest import stage; per-cell results are cached and
published as a GitHub Release asset.

```bash
python tools/navmesh/navmesh_cache.py verify  --plugin Oblivion.esm   # publishable?
python tools/navmesh/navmesh_cache.py install --plugin Oblivion.esm   # get the cache
python tools/navmesh/navmesh_cache_hook.py --install                  # gate pushes
python tools/navmesh/navmesh_cache_hook.py --run                      # publish manually
```

- **Never ship `collision_cache.bin`** — it holds Bethesda's Havok triangles keyed
  by asset path. Only our own `navmesh_geom_cache` pickles go in.
- Never put mtime, absolute paths, or worker counts in a cache key — they're
  machine-local, so every downloader misses.
- **The cache tag is a SHA-1 over the bytes of every `tes5_import/navmesh/*.py`**
  (`navmesh/pool.py:navmesh_geom_cache`). Any edit there, whitespace included,
  invalidates everyone's cache — republish when you touch it.
  `NAVMESH_PATHS`/`NAVMESH_FUNCS` in `navmesh_cache_hook.py` gate the push; a
  function added directly below a gated one reads as a navmesh change (git's `-U0`
  hunk header names the function above an insertion). Check with
  `navmesh_cache_hook.py --check`.

Rationale and contracts:
[tes5_import_navmesh.md](docs/commentary/tes5_import_navmesh.md#the-shared-navmesh-cache--design-rationale).

## Documentation

- **Findings go in `docs/`, not just memory** — memory is per-machine; a doc is
  the only copy another computer sees. Record new learnings once confirmed (see
  [When an idea isn't working](#when-an-idea-isnt-working) for in-game fixes).
- Keep this file short: each rule in one place, with at most a one-clause reason.
  Worked examples, history and measurements go in `docs/`, linked.

**[docs/README.md](docs/README.md) is the index and says where a new document goes.**

| Folder | Holds |
|---|---|
| [reference/](docs/reference/) | What a format or contract IS — stable, no dates |
| [commentary/](docs/commentary/) | Why the SHIPPED code is the way it is — named `<package>_<subsystem>.md` after the code it explains, opens with `**Code:**`. Measurements, engine behavior, reverted attempts. The default |
| [plans/](docs/plans/) | Designed, not yet built |
| [audits/](docs/audits/) | A dated sweep over a corpus, with counts |
| [assets/](docs/assets/) | Images and icons; `banner.png`/`favicon.ico` load at runtime |

## <a id="code-review"></a>Code review: run the claim, don't read it

An unexecuted finding is a guess — delete it; never soften it to "possible issue".
Ship a finding only with a reproduction, a query against real data, or a
failing-then-passing test.

- Every number is measured this session or absent.
- Read the code; don't infer — inference produces confident nonsense.
- Mark verified and suspected findings differently.
- Don't nitpick: no theoretical edge cases, style, or naming.

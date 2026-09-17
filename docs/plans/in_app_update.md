# In-app update: download only what changed — design plan

**Status: PLAN, unimplemented.** No code has been written.

Goal: **Check for Updates** should *apply* the update in place, transferring
**only the files that changed**, instead of sending the user to a downloads page
to paste a 45 MB zip over their folder by hand.

Git is *one* way to get delta transfer, but not the only one and — measured
below — not the cheapest here. The recommended design uses **the GitHub compare
API plus per-file raw downloads**, needs no `git` executable, and works on the
install shape users actually have.

**Delta vs full download is invisible to the user** (§5). Both paths end in the
same staged-and-swapped tree, so the choice is an implementation detail, not a
question to put to anyone. The user sees one **Update Now** button and a size.

**Development code is protected by refusing to run at all on a dev tree**
(§4, "Preconditions and refusals"). `is_dev_version()` is already True for any
tree that is not exactly a release, so the gate is one check and it fails safe.

---

## 1. What exists today

| Piece | Where | Behaviour |
|---|---|---|
| Menu button | [gui.py:1269](../../gui.py#L1269) | `Check for Updates`, a `Menubutton` used as a plain click target |
| Check worker | [gui.py:1301](../../gui.py#L1301) | Daemon thread, hops back via `root.after` (tkinter is not thread-safe) |
| Result dialog | [gui.py:1279](../../gui.py#L1279) | On "available", offers **Open Page** → `RELEASES_URL` |
| Remote query | [version.py:994](../../version.py#L994) `check_for_update()` | Anonymous GitHub tags API → `{current, latest, available, reachable}` |
| Version identity | [version.py:290](../../version.py#L290) `current_version()` | `VERSION` file (archive-stamped) → git refs → `0.0-dev` |
| Post-upgrade steps | [version.py:840](../../version.py#L840) `upgrade_plan()` | Which pipeline steps the new version invalidates |

The update path stops at "open the page". Everything after that is manual, and
the README's instruction is literally "Paste a new download over your existing
folder" ([README.md:172](../../README.md#L172)).

---

## 2. Measurements that decide the design

All figures measured on this tree, 2026-08-25.

### 2a. The delta is tiny; the tree is not

| From → to | Changed files | Bytes of changed files |
|---|---|---|
| 0.605 → 0.606 | 12 | 1.2 MB |
| 0.606 → 0.607 | 20 | 0.8 MB |
| 0.607 → 0.608 | 21 | 2.0 MB |
| 0.608 → 0.609 | 25 | 0.8 MB |
| 0.609 → 0.610 | 47 | 3.4 MB |
| 0.610 → 0.611 | 13 | 0.8 MB |
| **0.605 → 0.611 (6 releases at once)** | **111** | **6.2 MB** |

Against a **45 MB** full tree (590 tracked files). A single-release update moves
**0.8–3.5 MB — a 15–55× saving**, and even skipping six releases costs 6.2 MB.

### 2b. Why the full tree is so much bigger than any delta

The bulk is bundled tooling and images that essentially never change:

```
10.3 MB  external/hkxcmd/hkxcmd.exe
 7.1 MB  docs/assets/readme_img.png
 2.0 MB  external/papyrus-compiler/papyrus.exe
 1.9 MB  external/bsarch/BSArch.exe
 1.8 MB  external/7zip/7z.dll
 1.8 MB  external/lodgen/LODGenx64.exe
```

Across the whole 0.605 → 0.611 span, **no file under `external/` changed at
all** (one `docs/*.png` did). A delta updater never re-sends any of it; a zip
re-paste sends all 45 MB every time.

### 2c. The GitHub compare API gives the delta in ONE anonymous request

`GET /repos/{owner}/{repo}/compare/{base}...{head}` with tags as refs:

- `0.610...0.611` → **200, 0.77 s**, `files` = 13 entries.
- `0.605...0.611` → **200, 1.38 s**, `files` = **111** — matching `git diff
  --name-only` **exactly**.
- Each entry carries `filename`, `status`, `sha`, `raw_url`, `contents_url`.
- Statuses observed over that span: `modified` 75, `added` 34, `removed` 1,
  `renamed` 1 — so **deletions and renames are reported**, which is precisely
  what pasting a zip cannot handle.
- Raw per-file download over `raw_url` works **anonymously** (verified, 200).
- Costs **one** call against the 60/hour anonymous limit, same budget
  `version.py` already lives within.

### 2d. Truncation — a real limit, measured

The compare API returns **at most 300 files**, silently. Verified: `0.01...0.611`
(the oldest release to newest) returns exactly **300** entries where git reports
**495**. It does not set an error; it just returns fewer.

Distance from 0.611 to each recent tag: 0.610 → 13 files, 0.605 → 111,
0.600 → 188, 0.595 → 243, **0.592 → 269**. So installs roughly **20+ releases
behind approach the cap**, and ancient ones exceed it.

**Consequence:** the updater must count the returned files and, if it hits 300
(or the byte total approaches the full tree), **fall back to a full download**
rather than silently applying a partial update. A partial update is the one
outcome worse than a manual re-paste.

### 2e. Line endings — a trap for `sha` verification

Measured, and it changes how verification must work:

- The release zip (`git archive`) ships **CRLF** for text files.
- `raw.githubusercontent.com` serves the same file as **LF**.
- The two are byte-different; neither matches the other's hash.

Cause: [.gitattributes](../../.gitattributes) sets no `text` / `eol` directives, so
working-tree line endings depend on the *user's* git config. This checkout has
`core.autocrlf=true`, and `git ls-files --eol` reports **386 files as CRLF in
the working tree against LF in the repository**.

Consequences for the design:

1. **The API's `sha` is the git blob hash of the LF content.** Verified: SHA-1 of
   `"blob %d\0" + LF-normalized bytes` reproduces `git hash-object` exactly.
   Since `raw_url` serves LF, a downloaded file can be verified directly — but
   the hash must be computed on **the downloaded bytes as received**, never on
   the file after it lands on disk.
2. **Never compare local bytes to remote to decide "already up to date."** A
   CRLF working file will differ from LF remote content for every text file,
   making an unchanged tree look entirely stale. Trust the version tags and the
   compare API, not byte equality.
3. Python reads both line endings transparently (universal newlines), so a tree
   that ends up mixed still runs correctly. This is a *verification* concern
   only, not a runtime one.

### 2f. Why not git

Git would also deliver deltas, but on this repo:

- **`.git` is 291 MB against 45 MB of tracked files.** Enabling updates would
  cost a user more than six full-tree downloads up front.
- **Users' installs have no `.git` at all.** `VERSION` is a
  `$Format:%(describe:tags)$` template marked `export-subst`
  ([.gitattributes](../../.gitattributes)); users download the source zip GitHub
  generates *from a tag* (the converter is released as a **tag**, not a GitHub
  Release with assets — [tools/navmesh/navmesh_cache.py:565](../../tools/navmesh/navmesh_cache.py#L565)).
  Confirmed by listing a `git archive` of HEAD: the only `.git*` entries are the
  `.gitignore` / `.gitattributes` **files**; there is no `.git` **directory**.
  [version.py:173](../../version.py#L173) states it outright — `_git_version()` is a
  developer fallback because "end users install by pasting a source drop, which
  has no `.git`".
- It requires the `git` executable, which is not a current dependency and is not
  in the README's `pip install` line.

A shallow `--depth 1` clone would shrink the first cost, but still needs git
installed and a graft onto the existing tree. **Keep it as the §7 alternative,
not the primary design.**

---

## 3. The other blocker: `conversion_config.json`

`conversion_config.json` is **tracked** (`git ls-files` confirms) and is the
**only tracked file the running application mutates**. The GUI writes it at four
sites ([gui.py:337](../../gui.py#L337) `save_config`, from lines 997, 1011, 1033,
2100 — navmesh-cache toggle, packing default, collision-winding mode, and the
data/output paths + worker count). `preflight.py` reads it too
([preflight.py:249](../../preflight.py#L249)) for `tes4DataPath` / `tes5DataPath` /
`bsarchPath`.

On this machine it is already modified against HEAD (7 insertions, 4 deletions):
the committed copy blanks both data paths and omits `outputDir`, `workers` and
`packStepsDefaultOn`. **Any updater that overwrites it wipes the user's real
paths and worker count** — and the current "paste a zip over the folder"
instruction has exactly this bug today.

### Decision: untrack it; defaults live in code

**No `conversion_config.default.json` is shipped.** The current committed
contents become the in-code defaults, and the app writes a real
`conversion_config.json` on first startup if none exists. Steps:

1. Add `conversion_config.json` to [.gitignore](../../.gitignore), next to
   `.conversion_state.json`, reusing its rationale — committing per-install
   state "would ship one machine's conversion state to every user".
2. `git rm --cached conversion_config.json` (tracked → untracked; the working
   file stays put, so no existing install loses anything).
3. Put the committed values in a `DEFAULT_CONFIG` dict in `gui.py` beside
   `CONFIG_FILE` ([gui.py:23](../../gui.py#L23)).
4. On startup, if the file is absent, write it from `DEFAULT_CONFIG` via the
   existing `save_config` ([gui.py:337](../../gui.py#L337)).

The committed file is only four real settings plus three `//`-prefixed
instruction strings:

```json
"tes4DataPath": "",  "tes5DataPath": "",
"files": ["Oblivion.esm"],  "logRunsKept": 3
```

The `// INSTRUCTIONS` / `// SKIP_TYPES` / `// logRunsKept` keys are documentation
for someone hand-editing the file. Keep writing them on creation so a
first-run file is still self-describing — they are inert to every reader.

**DONE.** Implemented: `conversion_config.json` is gitignored, `DEFAULT_CONFIG`
lives in `core/gui/config.py`, and `write_default_config()` seeds it from
`create_window()`. See
[pipeline.md](../reference/pipeline.md#the-config-file-is-per-install).

**One claim below was wrong.** `source_paths.load_config()` did a bare `open()`,
so a missing file raised `FileNotFoundError` and killed the whole CLI (the
loader `convert.py` uses, reached also by `preflight.py`, `compile_papyrus.py`,
`ck_compile_check.py`, `make_game_select_esp.py`). It now returns `{}` for the
default path and still raises for an explicit `--config`. The line numbers below
also predate the `gui.py` -> `core/gui/` split.

**Every OTHER reader already tolerates a missing file or key.**
`load_config()` returns `{}` when the file is absent
([gui.py:329](../../gui.py#L329)); `preflight._load_config()` is
best-effort-empty-dict ([preflight.py:249](../../preflight.py#L249));
`skyrim_assets` catches `FileNotFoundError`
([asset_convert/sources/skyrim_assets.py:59](../../asset_convert/sources/skyrim_assets.py#L59));
and each key read has an inline fallback — `cfg.get("tes4DataPath", "") or
_find_game_path("oblivion")` ([gui.py:725](../../gui.py#L725)),
`cfg.get("navmeshCacheDownload") is not False` ([gui.py:742](../../gui.py#L742)),
`config.get("outputDir") or str(SCRIPT_DIR / "output")`
([convert.py:1368](../../convert.py#L1368)). So creating the file on first run is a
convenience, not a correctness requirement — nothing breaks in the window before
it exists.

**Also keep a never-overwrite list in the updater.** Independent of the above:
`apply_update` refuses to write `conversion_config.json` whatever the compare
API returns. Untracking means it should never appear in a delta; the guard costs
one line and removes the possibility entirely.

Everything else the user owns is already ignored — `export/`, `output/`,
`logs/`, `temp/`, `.conversion_state.json` — so nothing else is at risk.

---

## 4. Design

### New module: `updater.py`

One new root module; `version.py` keeps its job (identity + step planning) and
`gui.py` gains no update logic.

```
can_update()                   -> (bool, reason)  # THE GATE — see Preconditions
plan_update(from_tag, to_tag)  -> {files, bytes, truncated, deletions}
download_update(plan, progress)-> stage every changed file into temp
apply_update(staged)           -> atomically swap into the install
```

`can_update()` is checked by **both** entry points — the GUI before it offers
the button, and `apply_update()` itself before it writes anything. Gating only
at the UI layer would leave the destructive function callable; re-checking
inside the applier means no future caller can bypass it.

Reuse, do not duplicate: `latest_release()`
([version.py:965](../../version.py#L965)) already picks the newest release tag and
filters the **14 `navmesh-cache-*` tags** out of the 103 total — those are a
different series and must never be offered as an update. `_UA` and the
`urllib`/`zipfile` download idiom already exist
([tools/navmesh/navmesh_cache.py:330](../../tools/navmesh/navmesh_cache.py#L330)).

### Download → stage → apply

Never write into the install tree while downloading. A half-applied update is
the worst outcome, so:

1. **Plan** — one compare call. If `len(files) >= 300`, mark `truncated` and
   switch to the full-tree path (§6).
2. **Stage** — download each non-`removed` file into a temp dir mirroring the
   layout. Verify each against the `sha` the API supplied. Any failure aborts
   with the install untouched.
3. **Apply** — only once every file is staged and verified: move into place,
   then process `removed` / `renamed` deletions. `os.replace` is atomic
   per-file on Windows; `version.py` already uses the tmp+`os.replace` idiom
   ([version.py:412](../../version.py#L412)).
4. **Stamp `VERSION`** — write the tag literally. A delta-updated tree has no
   `.git`, so `_read_version_file()` is what reports the version afterwards, and
   it accepts a bare release tag ([version.py:117](../../version.py#L117)). Getting
   this wrong makes every later update check compare against the wrong number.
5. Delete the temp dir.

**Deletions matter.** They are the reason a zip re-paste is unreliable: it
leaves removed modules behind to be imported by stale code. The compare API
reports `removed` and `renamed`, so this design fixes a bug the current
instructions cannot.

**Never-touch list**, enforced in `apply_update` regardless of what the API
returns: `conversion_config.json` (§3), plus anything under the gitignored user
dirs. Belt-and-braces against a mis-specified compare.

### Preconditions and refusals — protecting development code

An updater that overwrites source files is, by construction, capable of
destroying uncommitted work. **The safeguard is layered, and the outermost layer
alone is sufficient**; the rest are defence in depth.

#### Layer 1 (primary): refuse unless the tree is exactly a release

`is_dev_version()` ([version.py:308](../../version.py#L308)) already answers "is
this a development tree?" and is **True for anything that is not exactly a
release tag**. Verified on this checkout:

```
current_version()   -> '0.611+ga475c44'
is_dev_version()    -> True
_read_version_file() -> None        (VERSION holds the raw $Format:...$)
```

| Tree | `current_version()` | `is_dev_version()` |
|---|---|---|
| Release zip install | `0.611` | **False** — updatable |
| Checkout on a tag | `0.611` | **False** — updatable |
| Checkout past a tag | `0.611+ga475c44` | **True** — refuse |
| Checkout, tag packed away | `0.611+g<sha>` | **True** — refuse |
| No `.git`, no `VERSION` | `0.0-dev` | **True** — refuse |

This is the whole safeguard in one line: **`if is_dev_version(): refuse`.** A
developer's tree cannot report a bare release tag *unless* it is sitting exactly
on a release with no local commits — in which case its tracked files match that
release anyway, and Layer 2 still guards the uncommitted ones.

It needs no `.git` parsing, no subprocess, and it fails **safe**: any tree whose
identity is unclear resolves to `DEV_VERSION` and is refused. That covers the
case `.git`-based detection would miss entirely — a developer running from a
tree with no `.git` at all (an extracted zip they have since edited).

One defensive note: `is_dev_version('')` returns `False`. Empty is not reachable
through `current_version()` (the chain ends at `DEV_VERSION`, and
`_read_version_file` validates through `version_key`), but the updater should
treat a falsy version as dev regardless rather than depend on that.

#### Layer 2: refuse on a dirty or non-standard checkout

When `.git` *is* present, check it — still **without spawning git**, the way
[version.py:173](../../version.py#L173) already reads refs as files (the GUI
resolves versions under console-less `pythonw.exe`, where every spawn risks a
console window). All of the following were verified readable file-only:

- **Presence/kind** — `.git` as dir (checkout) or file (worktree/submodule).
- **Branch** — `.git/HEAD` → `ref: refs/heads/master`.
- **Extra remotes** — `.git/config` is plain INI; this tree shows
  `[remote "origin"]` *and* `[remote "Hortophyll"]`, a fork setup no end user has.
- **Dirty tracked files** — parse `.git/index` (DIRC v2, 583 entries here) and
  compare each entry's cached `size` against `os.stat`. Verified: this correctly
  reported exactly one modified file (`CLAUDE.md`), matching `git status`.
  The index caches **working-tree** stats, so the CRLF/LF split of §2e does not
  confuse it (`version.py` index size 45735 == on-disk size).

Refuse if the tree is dirty, off `master`, or carries an unexpected remote.

Index parsing is ~30 lines and is **optional**: Layer 1 already refuses every
tree Layer 2 would catch, because a dirty checkout is necessarily past its tag
or on it with edits. Implement Layer 2 only if a belt-and-braces check is wanted
for the "clean checkout exactly on a release" case, where a developer could
still have **untracked** files.

#### Layer 3: never delete anything not in the manifest

The applier only ever touches paths the compare API named, plus the never-touch
list (§3). It **never** walks the install tree deleting "unknown" files.
Untracked developer scratch files — `temp/`, probe scripts, an unfinished module
— are invisible to it by construction. This is why the delta path is *safer*
for a developer than a zip re-paste, which happily overwrites whatever it lands on.

#### Layer 4: staged and reversible

Nothing is written into the install until every file is downloaded and verified
(§4). A refusal, a failed download, or a `sha` mismatch leaves the tree
bit-identical. There is no partially-updated state to recover from.

#### Also refuse while a conversion is running

Swapping pipeline modules mid-run produces failures with no coherent
explanation.

#### An escape hatch, not a prompt

A developer who genuinely wants to self-update can pass
`python updater.py --force`. The **GUI never offers it** — surfacing "update
anyway?" to someone with uncommitted work is exactly the prompt that gets
click-throughed. Keeping it CLI-only means the destructive path requires
deliberately typing a flag.

---

## 5. UI — delta vs full is invisible

**Yes: the delta/full choice is entirely an implementation detail and the user
never sees it.** The mechanism is not a decision they can act on. They asked for
"update"; delta-vs-full is only *how* the bytes arrive, and both paths end in
the identical staged-and-swapped tree (§4). Making it a prompt would be asking
the user to answer a question they have no basis to answer.

What makes this safe to hide is that **both paths share one applier**. The delta
path is a pre-filter that decides *which files to fetch*; staging, `sha`
verification, the never-touch list, the atomic swap, and the `VERSION` stamp are
the same code either way. A silent fallback is therefore not "a different
update", just a different source for the same bytes.

Two states only:

| State | Dialog offers |
|---|---|
| Update available | **Update Now** (`0.611 → now`) / Open Page / Not Now |
| Conversion running, or dev checkout | Open Page only, with the reason stated |

### What the user does see

Hiding the *mechanism* is not the same as hiding the *cost*. A 45 MB download
and a 0.8 MB one feel different, so the size is still worth showing — as a plain
number, with no mechanism attached:

- Dialog: "Update to 0.611 — about 0.8 MB to download."
- On fallback, that number is simply larger ("about 45 MB"); the wording does
  not change and no choice is offered.
- Progress goes to the existing log pane, where "downloaded 7 of 13" and
  "downloading full package" are informative but not decisions.

The one case that must **not** be silent is failure. If the delta path fails and
the full path also fails, say so plainly and fall back to Open Page — never
report success, and never leave a partly-updated tree (§4 makes that
structurally impossible by staging first).

### Deciding without asking

The fallback triggers on measured conditions, evaluated in `plan_update`:

1. `len(files) >= 300` — truncation (§2d), the API silently caps there.
2. Delta byte total ≳ the full-tree size — no saving, so prefer the simpler path.
3. Any per-file download or `sha` mismatch — abandon the delta, restart as full.
4. Base tag not resolvable on the remote (a version never published, e.g. a
   hand-edited `VERSION`) — compare has no base, so full is the only option.

Because staging happens in temp, condition 3 can be discovered *mid-download*
and still fall back cleanly: nothing has touched the install yet.

`_confirm` ([gui.py:1409](../../gui.py#L1409)) is hardcoded to two buttons, but is a
two-line wrapper over `_dialog` ([gui.py:1340](../../gui.py#L1340)):

```python
def _dialog(title, message, buttons=("OK",), default: int = 0, links=()) -> str
```

`_dialog` returns the clicked button's **label** and takes an arbitrary `buttons`
tuple, so this needs no new widget code — call it directly and branch on the
label. Buttons render right-to-left (`reversed(buttons)`), `default` is an index
that gets the accent style, and Escape answers `buttons[-1]` — so `Not Now`
goes last.

`_confirm` ([gui.py:1409](../../gui.py#L1409)) is hardcoded to two buttons, but is a
two-line wrapper over `_dialog` ([gui.py:1340](../../gui.py#L1340)):

```python
def _dialog(title, message, buttons=("OK",), default: int = 0, links=()) -> str
```

`_dialog` returns the clicked button's **label** and takes an arbitrary `buttons`
tuple, so this needs no new widget code — call it directly and
branch on the label. Buttons render right-to-left (`reversed(buttons)`),
`default` is an index that gets the accent style, and Escape answers
`buttons[-1]` — so `Not Now` goes last.

### Progress

The download must not run on the UI thread. Two established idioms:

- **One-shot** — worker thread + `root.after(0, lambda: ui(result))`, exactly
  what `_check_for_updates` already does ([gui.py:1301](../../gui.py#L1301)).
- **Streaming into the log pane** — `queue.Queue` + worker + `root.after(50,
  _drain)` (`gui.py:4470`-`4493`). Better here: a per-file "downloaded X of N"
  line is genuinely useful. Two documented traps — **schedule the first drain,
  never call it inline** (`running` is set on the worker thread, so an inline
  call sees it clear and strands the queue), and the worker must never touch
  tkinter, only `q.put`.

The indeterminate `prog_bar` (`gui.py:3385`) is the existing spinner; there is
no percentage widget, though with a known file count one could be added.

### Restart

Python has already imported the old modules and the update changes them on disk.
After a successful apply, tell the user and **close the GUI**, prompting a
relaunch. Auto-relaunch (`os.execv` / detached spawn) is a separate feature — do
the simple thing first.

---

## 5b. Checking automatically at launch

### The existing decision this overturns

[gui.py:1283](../../gui.py#L1283) states it outright:

> `# Never automatic: the check is a network call, and a GUI that phones home`
> `# on launch would both stall startup and do it without being asked.`

Two objections. **Measured, the first is wrong and the second is already
settled elsewhere in this codebase** — so a launch check is viable, but only
built the way below.

**Objection 1 — "would stall startup". It does not.** The check already runs on
a worker thread and hops back with `root.after`
([gui.py:1301-1329](../../gui.py#L1301)); it never touches the mainloop. Measured
cost of the tags call: **0.70 s cold, 0.15 s warm** (42 KB, 100 tags). Even the
pathological case is bounded — `latest_release()` passes `timeout=8`
([version.py:965](../../version.py#L965)), and a thread blocked for 8 s on a dead
connection delays *nothing*, because the window is already up and interactive.
The original comment is true only of a *synchronous* check, which is not what
would be built.

**Objection 2 — "without being asked". This project already does exactly
this, deliberately.** The navmesh cache **downloads automatically by default**,
with an opt-out toggle whose comment reads: "Persisted, so a metered connection
stays opted out across sessions rather than having to be re-set every launch"
([gui.py:908](../../gui.py#L908)), defaulting on via `cfg.get("navmeshCacheDownload")
is not False` ([gui.py:747](../../gui.py#L747)). So "network by default, with a
persisted user opt-out" is an accepted pattern here — and an update check is a
far smaller network action than a cache download.

The honest framing: the comment records a reasonable default from before there
was anything to *do* about an available update. Now that the update can be
applied in-app, the value of noticing it goes up. **Update the comment when this
ships** rather than leaving the code contradicting itself.

### Design

Copy the navmesh-cache toggle exactly — same shape, same persistence:

- **Config key `autoCheckUpdates`, read as `is not False`** so it defaults ON and
  a config written before the option existed (or a corrupt value) still gets the
  check. Same idiom as `navmeshCacheDownload` ([gui.py:747](../../gui.py#L747)).
- **A Settings checkbox**, persisted through `save_config` like the others
  ([gui.py:1007](../../gui.py#L1007)).
- **Fire after the window is up**, not during construction: `root.after(1500,
  _auto_check)` then the existing worker-thread path. Deferring past first paint
  means startup is untouched even if DNS hangs.
- **Reuse `_check_for_updates()` wholesale.** The only difference is what
  happens on the "no update" and "unreachable" branches (below).

### Silence unless there is something to say

The manual check is a *question* and always deserves an answer. The automatic
check is not, so it must not produce dialogs for non-events:

| Result | Manual check | Automatic check |
|---|---|---|
| Update available | Dialog | **Dialog** — the one thing worth interrupting for |
| Already up to date | "Up to Date" dialog | **Nothing** |
| Unreachable | "Update Check Failed" dialog | **Nothing** — silent, log line at most |
| Dev tree (§4 Layer 1) | Refusal explained | **Skip the check entirely** |

An automatic check that pops "could not reach GitHub" on every offline launch
would be the single most annoying thing this feature could do. It must fail
completely silently — the user did not ask.

Skipping on a dev tree is free: `is_dev_version()` is a local string check with
no network cost, and a developer never wants this.

### Rate limit and caching

The anonymous limit is **60 requests/hour per source IP** (verified live), shared
by everyone behind that IP. The budget is **not** tight — measured costs:

| Action | API calls |
|---|---|
| Update check (tags) | 1 |
| Delta plan (compare) | 1 |
| Downloading every changed file | **0** — `raw.githubusercontent.com` is not the API |
| Navmesh cache install, per plugin | 1 |
| Downloading a 114 MB cache asset | **0** — redirects to `release-assets.githubusercontent.com` |

So a launch check plus a full update costs **2 calls**, and a user converting all
three plugins spends 3 more. Exhausting 60/hour needs roughly 20 first-time users
behind one NAT within the hour.

Caching is therefore about **politeness and a clean failure mode**, not scarcity:
a developer relaunching in a loop is the only realistic way to burn it, and a 429
is indistinguishable from being offline.

So **cache the result and check at most once per interval** (24 h is
reasonable). Store `{"lastUpdateCheck": <unix>, "lastSeenLatest": "0.611"}` in
`.conversion_state.json` — it is gitignored, per-install, already has a
load/save path (`_load_state` / `_save_state`,
[version.py:361](../../version.py#L361)), and survives updates untouched. The
manual menu check must **ignore the interval** and always hit the network;
"Check for Updates" means now.

### What it must not do

- **Never auto-*apply*.** The launch check surfaces availability; installing
  stays an explicit click. Silently swapping code under a user who launched to
  run a conversion is the opposite of the §5 invisibility argument — there, the
  user had already chosen to update.
- **Never block the run button** or gate any workflow behind it.
- **Never re-prompt for a version the user dismissed.** Persist
  `lastSeenLatest`; if the newest release still equals it, stay silent. Without
  this, declining an update means being asked again every single launch.

---

## 6. Full-tree fallback

When the delta is truncated (§2d), or its byte total approaches the full tree,
download the tag's source zip — the same anonymous `urllib` + `zipfile` approach
`tools/navmesh/navmesh_cache.py` already uses — unpack to temp, and apply through the
**same** staged path as §4 so the never-touch list and atomic swap still hold.

Because we know the full file list from the zip, deletions can still be computed
(anything tracked-looking in the install that the zip lacks), but that is a
refinement; the safe v1 is to unpack over the tree and accept that stale removed
files persist, exactly as today's manual instructions do.

---

## 7. Alternative: shallow git checkout

Kept as a documented option, not the plan. `git clone --depth 1 --branch master
--single-branch` into temp, move `.git` into the install root, `git reset
--mixed HEAD` to adopt the pasted files without touching them, then
`git fetch --depth 1 origin tag <latest>` and check out the **tag** (never
`master`'s tip — users must land on a release).

Advantages: git computes deltas natively and handles deletions perfectly.
Disadvantages: needs the `git` executable, a first-time clone cost to measure,
and the graft is fiddly. Revisit only if the compare-API path proves
insufficient.

---

## 8. Post-update: reuse the step plan

Nothing new is needed for "what do I re-run now". `upgrade_plan()`
([version.py:840](../../version.py#L840)) already answers it from the per-step
`.conversion_state.json`, which is gitignored and untouched by the update. After
relaunch the existing **Upgrade** button selects exactly the steps the new
version invalidated. Say so in the success dialog — it is the payoff.

**Dependencies:** there is **no `requirements.txt`** and `pyproject.toml`
declares none; the README lists a single `pip install` line. A release can add a
dependency the user lacks. `preflight.py` already detects this and stops the
affected phase with a "pip install X" message, so let it do its job. The updater
must **not** run `pip` — a surprising side effect of "update".

---

## 9. Risks

| Risk | Handling |
|---|---|
| Silent 300-file truncation → partial update | Count returned files; ≥300 ⇒ silent full-tree fallback (§6) |
| Fallback confusing the user | It is invisible (§5): one applier, one dialog, size shown as a number |
| Silent fallback hiding a real failure | Failure is the one thing never hidden — report it and offer Open Page |
| `sha` verified against the wrong bytes | Hash the **downloaded** bytes (LF, as `raw_url` serves them), not the on-disk file (§2e) |
| Byte-comparing local vs remote | Never do it — CRLF working trees differ from LF blobs for 386 files (§2e) |
| User loses settings | §3 — untrack the config, defaults in code **and** a never-touch list |
| Half-applied update | Stage everything to temp, verify `sha`, then atomic swap |
| Stale removed modules | Apply `removed`/`renamed` deletions — the delta path fixes this |
| Wrong version after update | Stamp `VERSION` with the bare tag (§4 step 4) |
| Update mid-conversion | Refuse while a job is running |
| Stale imported modules | Close the GUI after applying |
| **Overwriting uncommitted development work** | **Layer 1: refuse when `is_dev_version()` — covers every dev tree, `.git` or not** |
| Dev tree with no `.git` at all | Resolves to `DEV_VERSION` ⇒ refused by Layer 1; `.git` checks would miss it |
| Clean checkout on a tag, but untracked scratch files | Layer 3 — applier only touches manifest paths, never walks/deletes |
| Dirty checkout, extra remote, wrong branch | Layer 2 — read `.git/HEAD`, `.git/config`, parse `.git/index` (no subprocess) |
| CRLF confusing dirty detection | Index caches **working-tree** stats — verified size matches on disk |
| Developer click-throughs an "update anyway?" prompt | Never offered in the GUI; `--force` is CLI-only |
| Offering a `navmesh-cache-*` tag | Reuse `latest_release()`'s filter |
| Anonymous rate limit (60/hr, per IP) | Not tight: check+update = 2 calls, file/asset downloads cost 0. Cached anyway (§5b) |
| Competing with the navmesh cache for quota | Same 60/hr pool, but the cache spends 1 call per plugin and its 114 MB asset costs 0 |
| Launch check stalling startup | Worker thread + `root.after(1500, …)`; measured 0.15–0.70 s, `timeout=8` bounds the worst case (§5b) |
| Launch check nagging offline users | Silent on "up to date" **and** on unreachable — dialogs only when an update exists (§5b) |
| Repeated launches burning the rate limit | Cache in `.conversion_state.json`, once per 24 h; manual check always bypasses it (§5b) |
| Re-prompting a declined version | Persist `lastSeenLatest`; stay silent while it matches (§5b) |
| Auto-check surprising a developer | Skipped entirely when `is_dev_version()` — no network call at all |
| Console window under `pythonw.exe` | Primary path spawns nothing; §7 would need `POPEN_FLAGS` |

---

## 10. Suggested order

1. **Untrack `conversion_config.json`; defaults move into code** (§3) —
   independent of the updater, and fixes a live bug in the current manual
   upgrade path.
2. **`can_update()` — the Layer 1 refusal gate — FIRST, before any code that can
   write a file.** One `is_dev_version()` check plus the falsy-version guard. On
   a developer's machine every later step is then inert by default, so the
   destructive paths are built behind a gate that is already closed.
3. `updater.py`: `plan_update()` + a CLI entry (`python updater.py --status`,
   `--plan <from> <to>`) so the delta is testable without the GUI.
4. `download_update()` + `apply_update()` with staging, `sha` verification on the
   downloaded bytes (§2e), the never-touch list, and `VERSION` stamping.
5. **Full-tree fallback (§6) — before the UI, not after.** It shares the applier
   from step 4, and building it early is what lets the dialog stay
   mechanism-free from the start rather than being retrofitted.
6. Two-state dialog in `gui.py`, wired to the streaming log pane.
6b. **Launch check (§5b) — last, and only once the manual path is proven.** It
   is the same code plus a deferred trigger, a persisted `autoCheckUpdates`
   toggle, an interval cache, and silence on every non-event. Update the
   "Never automatic" comment at [gui.py:1283](../../gui.py#L1283) in the same pass.
7. Tests: `plan_update` against recorded compare-API JSON fixtures (**no network
   in tests**) — truncation at 300, `removed`/`renamed` handling, never-touch
   enforcement, `VERSION` stamping, and LF/CRLF `sha` verification. Plus the
   refusal gate: `is_dev_version` inputs (`0.611`, `0.611+g<sha>`, `0.0-dev`,
   `''`) map to the right allow/refuse verdict. Keep under the 120 s rule.
8. *Optional* — Layer 2 `.git` inspection (index parse, branch, remotes). Only
   worth it as belt-and-braces; Layer 1 already refuses everything it catches.

"""Installed-version identity and upgrade-aware step selection.

Two questions this answers, both for a user who installs by pasting a new
source drop over the old folder:

  1. "What version am I running?"  -> `current_version()`
  2. "I just upgraded -- what do I have to re-run?"  -> `upgrade_plan()`

The version a user is *running* and the version they last *converted with* are
different facts, and only the second one tells you what is stale in `output/`.
Both are tracked:

  * `VERSION` (repo root) names the release the source tree IS.  A source drop
    carries it; git does not have to be present, and usually is not -- the
    release zip GitHub generates has no `.git` at all, so `git describe` is not
    a fallback that works where it matters.

    It is NOT a committed number.  The file holds a literal
    `$Format:%(describe:tags)$` and is marked `export-subst` in
    `.gitattributes`, so git expands it to the tag name while building the
    archive.  The zip a user downloads for tag 0.581 therefore says `0.581`
    though no commit ever contained that text.  A working checkout reads the
    unexpanded placeholder, which `_read_version_file` treats as absent so git
    tags take over and a developer still sees a real number.

    This replaced a CI commit that wrote the number into VERSION and tagged
    itself.  That commit could not reach protected master, so it existed only
    on the tag -- which made master's VERSION permanently wrong and, worse,
    meant the whole scheme depended on a direct push and could not work for a
    PR merge.
  * `.conversion_state.json` (repo root, gitignored) records the version that
    last completed each pipeline step.  It is per-step and per-plugin because
    an upgrade that only touches meshes should not invalidate an Import the
    user ran an hour ago, and converting Nehrim should not mark Oblivion's
    steps fresh.

Pasting a new drop over the old install therefore leaves `VERSION` new and
`.conversion_state.json` old, and the difference between them is exactly the
set of commits whose changed paths decide which steps must re-run.  That path
-> step mapping is NOT reimplemented here: it is `tools/release/release_notes.py`,
which the tag workflow already uses to write the same answer into every
release's notes.  One mapping, two consumers.

The version -> steps table is FETCHED OVER HTTPS from the GitHub RELEASES, and
there is no file anywhere -- nothing committed, nothing published.  Each
release's body is its annotated tag's message verbatim, which already contains
the "Steps to re-run in the GUI" checklist `tools/release/release_notes.py` wrote at
tag time.  The releases ARE the table; `steps_table()` just reads them back.

Deriving it rather than committing it is what lets a PR merge cut a release at
all.  A committed table has to be WRITTEN by the release job, and a write means
a commit on master -- which branch protection refuses, and which no PR-merge
release could ever perform.

ONE request, anonymous: `releases?per_page=100` returns every body at once.
The obvious alternative -- list tag refs, then fetch each tag object for its
message -- costs one request PER TAG, and with 59 annotated tags against an
anonymous limit of 60/hour a single refresh exhausted the quota and everything
afterwards got a 429.  (GraphQL also answers in one query but needs a token,
which no end user has.)  This is why the release job must create a Release for
every tag, not merely push the tag.

When the fetch fails the answer is "unknown", never "nothing owed".
`steps_between` returns None, `upgrade_plan` reports `offline`, and the GUI
disables the Upgrade button rather than showing a plan built from no data --
an empty list would tell a user with stale output that they are current.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from core.gui.config import GLOBAL_ACTIONS, STEPS

SCRIPT_DIR = Path(__file__).resolve().parent

VERSION_FILE = SCRIPT_DIR / "VERSION"
STATE_FILE   = SCRIPT_DIR / ".conversion_state.json"

# What VERSION reads in a working checkout, where the tree sits between tags.
DEV_VERSION = "0.0-dev"

# ── Remote endpoints ────────────────────────────────────────────────────────
# Where releases are published.  A source drop has no git remote to derive this
# from, so it is a constant.
REPO = "bryantmh/tes4skyrim"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases"
_TAGS_API = f"https://api.github.com/repos/{REPO}/tags?per_page=100"

# Every release, WITH ITS BODY, in ONE anonymous request.  The body is the
# annotated tag's message (the release job passes it through verbatim), so it
# already carries the "Steps to re-run in the GUI" checklist -- the releases
# ARE the table.  Nothing is published, committed, or kept in sync.
#
# One request matters: the anonymous API allows 60 an hour, and the obvious
# alternative -- list tag refs, then fetch each tag object's message -- costs
# one request PER TAG.  Measured on this repo (60 tags) that exhausted the
# entire quota on a single refresh and every later call got a 429.  GraphQL can
# also do it in one query but requires a token, which no end user has.
_RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=100"

# Anonymous: reading a public repo's tags and assets needs no credentials, and
# gating this on the GitHub CLI would exclude essentially every end user (the
# same reasoning tools/navmesh/navmesh_cache.py records for its download path).
_UA = {"User-Agent": "tes4skyrim-update-check",
       "Accept": "application/vnd.github+json"}


# ---------------------------------------------------------------------------
# Version identity
# ---------------------------------------------------------------------------

def _read_version_file() -> str | None:
    """The release stamped into VERSION by `git archive`, or None.

    VERSION is an `export-subst` template (`.gitattributes`).  In a real
    checkout it still holds the raw `$Format:...$` placeholder, and in an
    archive built from an UNTAGGED commit `%(describe:tags)` expands to a
    describe string like `0.581-4-gaaef46e` rather than a bare tag.

    Both are rejected here as "not a release stamp":

      * The placeholder is not a version at all -- returning it would put
        `$Format:%(describe:tags)$` in the title bar and make every comparison
        fail.  `_git_version()` answers correctly for a checkout anyway.
      * A describe string means the archive was cut from a commit that is not a
        release.  `current_version` falls through to git (absent here) and then
        to DEV_VERSION, which reads as a development build -- true, and better
        than presenting an unreleased commit as its nearest tag.
    """
    try:
        text = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text or text.startswith("$Format:"):
        return None
    # Only a bare release tag counts; `_base_tag` strips describe suffixes, so
    # a value that survives it unchanged is exactly a tag.
    return text if text == _base_tag(text) and version_key(text) else None


def _head_commit(git_dir: Path) -> str | None:
    """HEAD's commit sha, following one level of symbolic ref."""
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head or None                       # detached HEAD
    ref = head.partition(":")[2].strip()
    try:
        return (git_dir / ref).read_text(encoding="utf-8").strip() or None
    except OSError:
        pass
    # Ref not loose -> it was packed by `git gc`.
    try:
        for line in (git_dir / "packed-refs").read_text(
                encoding="utf-8").splitlines():
            if line.startswith(("#", "^")):
                continue
            sha, _, name = line.partition(" ")
            if name.strip() == ref:
                return sha.strip()
    except OSError:
        pass
    return None


def _git_version() -> str | None:
    """What this checkout is, read straight from `.git`.  None if unavailable.

    Only a *fallback*: end users install by pasting a source drop, which has no
    `.git`, so this exists so a DEVELOPER's title bar shows something true
    rather than the `0.0-dev` placeholder in VERSION.

    Deliberately does NOT shell out to `git describe`.  The GUI resolves the
    version while building its window, and under `gui.pyw` the parent is
    console-less `pythonw.exe`, where every spawn is a potential console
    window.  The GUI is ONE window; the safe number of subprocesses on that
    path is zero, not "some, but flagged".  Reading the ref files directly is
    also ~50x faster than the spawn it replaces.

    Returns the exact tag when HEAD is on one, else `<tag>+g<short-sha>` --
    git describe's own shape minus the commit count, which cannot be had
    without walking the object graph (packfiles included) and is not worth that
    machinery for a title-bar string.  The sha is the part that actually
    identifies the build.
    """
    git_dir = SCRIPT_DIR / ".git"
    if git_dir.is_file():
        # A worktree/submodule: ".git" is a file pointing at the real dir.
        try:
            pointer = git_dir.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not pointer.startswith("gitdir:"):
            return None
        git_dir = Path(pointer.partition(":")[2].strip())
        if not git_dir.is_absolute():
            git_dir = (SCRIPT_DIR / git_dir).resolve()
    if not git_dir.is_dir():
        return None

    # Map tag -> sha for release tags only, from both loose and packed refs.
    tags: dict[str, str] = {}
    tag_root = git_dir / "refs" / "tags"
    try:
        for entry in tag_root.iterdir():
            if entry.is_file():
                try:
                    tags[entry.name] = entry.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
    except OSError:
        pass
    try:
        for line in (git_dir / "packed-refs").read_text(
                encoding="utf-8").splitlines():
            if line.startswith(("#", "^")):
                continue
            sha, _, name = line.partition(" ")
            name = name.strip()
            if name.startswith("refs/tags/"):
                tags.setdefault(name[len("refs/tags/"):], sha.strip())
    except OSError:
        pass

    releases = {t: k for t in tags if (k := version_key(t)) and t == _base_tag(t)}
    if not releases:
        return None
    latest = max(releases, key=lambda t: releases[t])

    head = _head_commit(git_dir)
    if not head:
        return latest
    # Annotated tags point at a tag OBJECT, not the commit, so comparing the ref
    # to HEAD directly never matches for one -- every release tag in this repo is
    # annotated, so a checkout sitting exactly ON a release reported
    # `<tag>+g<sha>` and was treated as a dev build ahead of it.  That is not
    # cosmetic: record_step_run stamps whatever this returns into the state file,
    # so a step run at release 0.586 was recorded as `0.585+g<sha>` (the newest
    # tag KNOWN LOCALLY at the time, plus a suffix), which ranks as 0.585 and
    # left the step looking permanently stale.  Peel the tag to its commit.
    if _peel_tag(git_dir, tags.get(latest, "")) == head:
        return latest
    return f"{latest}+g{head[:7]}"


def _peel_tag(git_dir: Path, sha: str) -> str:
    """The commit *sha* names, following an annotated tag object to its target.

    A lightweight tag already points at the commit and is returned unchanged.
    For an annotated tag the ref names a tag object whose payload begins with
    `object <sha>`; that line is what identifies the commit.

    Reads loose objects only, and never shells out -- `_git_version`'s whole
    contract is that it spawns nothing (the GUI resolves the version under
    console-less pythonw.exe).  A tag packed away by `git gc` simply fails to
    peel, which falls back to the `+g<sha>` form: the pre-existing behaviour,
    never something worse.
    """
    if not sha:
        return ""
    path = git_dir / "objects" / sha[:2] / sha[2:]
    try:
        import zlib
        raw = zlib.decompress(path.read_bytes())
    except Exception:
        return sha
    header, _, body = raw.partition(b"\x00")
    if not header.startswith(b"tag "):
        return sha                      # a commit: lightweight tag
    for line in body.splitlines():
        if line.startswith(b"object "):
            return line.split(b" ", 1)[1].decode("ascii", "ignore").strip()
        if not line.strip():
            break
    return sha


# current_version() is called on every plugin selection and on each GUI
# refresh; the answer cannot change while the process runs, so resolve it once.
_CURRENT: list[str | None] = [None]


def current_version() -> str:
    """The release this source tree is, e.g. "0.583".

    `VERSION` wins because it is the only source that survives the way users
    actually install.  A dev checkout (VERSION absent or `0.0-dev`) falls back
    to the nearest git tag so the GUI shows a real number instead of "dev".
    """
    if _CURRENT[0] is not None:
        return _CURRENT[0]
    stamped = _read_version_file()
    if stamped and stamped != DEV_VERSION:
        resolved = stamped
    else:
        resolved = _git_version() or stamped or DEV_VERSION
    _CURRENT[0] = resolved
    return resolved


def is_dev_version(version: str | None = None) -> bool:
    """True when this tree is not exactly a release.

    A `git describe` string carries a `-<n>-g<sha>` and/or `-dirty` suffix
    whenever the checkout has moved past its tag, so anything beyond a bare
    `MAJOR.MINOR` is a development build.
    """
    v = current_version() if version is None else version
    return v == DEV_VERSION or v != _base_tag(v)


def _base_tag(version: str) -> str:
    """The release tag inside a version string.

    `0.58` -> `0.58`;  `0.58-3-g6c7a351-dirty` -> `0.58`.  Everything ranks and
    compares on the tag, so a developer three commits past 0.58 is treated as
    0.58 for "what does this upgrade owe" purposes.
    """
    return version.strip().split("-", 1)[0].split("+", 1)[0]


def version_key(tag: str) -> tuple[int, int] | None:
    """Sortable key for a release tag, in thousandths.

    Tags through 0.58 are MAJOR.MM (hundredths); 0.581 onward are MAJOR.MMM
    (thousandths).  Both must rank on ONE scale or 0.59 sorts above 0.580 --
    the same trap `tools/navmesh/navmesh_cache.py` and the tag workflow each document.
    A 2-digit minor is worth ten of the new units: 0.58 IS 0.580.

    The minor field is a FRACTION written without its point, so scale by its
    own WIDTH rather than special-casing one width.  Scaling only the 2-digit
    case ranked the legacy 1-digit tag '0.9' as nine THOUSANDTHS, i.e. below
    '0.10' -- precisely the mis-ordering this docstring warns about, in the one
    spelling it forgot.  It also disagreed with navmesh_cache._version_key(),
    which scales by width, so the shared-cache range check compared keys built
    on two different scales.  This is THE version comparator for the program;
    navmesh_cache._local_version_key() defers to it.

    Accepts a raw `git describe` string so a dev checkout ranks as its tag.
    """
    base = _base_tag(tag)
    major, sep, minor = base.partition(".")
    if not sep or not major.isdigit() or not minor.isdigit():
        return None
    # Integer division keeps a field wider than thousandths usable rather than
    # unrankable; no published tag has ever used sub-thousandth precision.
    return (int(major), int(minor) * 1000 // (10 ** len(minor)))


# ---------------------------------------------------------------------------
# Conversion state: which version last ran each step
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    return _lift_global_steps(state)


def _lift_global_steps(state: dict) -> dict:
    """Move a legacy per-plugin record of a GLOBAL step to the shared key.

    Earlier builds stamped "10. Patch Skyrim" onto every plugin in the run, so a
    user who patched while converting Oblivion has it under `oblivion.esm` and
    nowhere else -- leaving every other plugin re-ticking it forever.  The step
    produces ONE shared artifact, so the newest such record is the truth for all
    plugins; lift it once, in memory, on read.

    Read-only: this never writes.  The next successful run records under the
    shared key anyway, and rewriting the file here would mean a plain status
    query mutates the user's state.
    """
    steps = state.get("steps")
    if not isinstance(steps, dict):
        return state
    for key in GLOBAL_STEPS:
        best = None
        for plugin, entry in steps.items():
            if plugin == GLOBAL_PLUGIN_KEY or not isinstance(entry, dict):
                continue
            at = entry.get(key)
            if isinstance(at, str) and (
                    best is None
                    or (version_key(at) or (0, 0)) > (version_key(best) or (0, 0))):
                best = at
        if best is None:
            continue
        shared = steps.get(GLOBAL_PLUGIN_KEY)
        if not isinstance(shared, dict):
            shared = {}
            steps[GLOBAL_PLUGIN_KEY] = shared
        have = shared.get(key)
        if not isinstance(have, str) or (version_key(best) or (0, 0)) > (
                version_key(have) or (0, 0)):
            shared[key] = best
    return state


def _save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, STATE_FILE)
    except OSError:
        # Losing the record costs an over-broad re-run suggestion, never
        # correctness -- never let it take the conversion down with it.
        try:
            os.unlink(tmp)
        except OSError:
            pass


def record_step_run(step_key: str, plugin: str | None,
                    version: str | None = None,
                    data_path: str | None = None) -> None:
    """Note that *step_key* completed for *plugin* at the current version.

    Called on a step's SUCCESS only.  A failed step must stay stale, or the
    next upgrade check would report clean and the user would ship half-built
    output.

    `data_path` is the TES4 data directory the plugin was converted FROM.
    Plugins do not share one: Nehrim and Morrowind_ob live in their own
    installs, so re-running a converted plugin means restoring its own source
    directory, not assuming whichever one is currently configured.

    A step in `GLOBAL_STEPS` is recorded ONCE, under the shared key, because it
    produces one artifact for the whole load order rather than per-plugin
    output.  Stamping it onto whichever plugins were in that run left every
    other plugin looking like it had never run the step, so it was selected
    again forever.
    """
    version = current_version() if version is None else version
    state = _load_state()
    steps = state.setdefault("steps", {})
    if step_key in GLOBAL_STEPS:
        key = GLOBAL_PLUGIN_KEY
    else:
        # A shared-asset step converts the whole mod's payload at once, so it
        # is recorded against the MOD, falling back to the plugin when this is
        # not an imported mod -- which is every game-Data plugin.
        key = _group_key(plugin) if step_key in GROUP_STEPS else None
        key = key or _plugin_key(plugin)
    entry = steps.setdefault(key, {})
    entry[step_key] = version
    # Never record a source directory against the shared key: it belongs to no
    # plugin, and `source_path_for` would then hand one plugin's install to
    # another.
    if data_path and key != GLOBAL_PLUGIN_KEY:
        state.setdefault("sources", {})[_plugin_key(plugin)] = data_path
    _save_state(state)


def source_path_for(plugin: str | None) -> str | None:
    """The TES4 data directory *plugin* was last converted from, if recorded."""
    sources = _load_state().get("sources", {})
    if not isinstance(sources, dict):
        return None
    got = sources.get(_plugin_key(plugin))
    return got if isinstance(got, str) and got.strip() else None


def _plugin_key(plugin: str | None) -> str:
    # Case-insensitive: the GUI's combo and the CLI's -f differ in case for the
    # same file often enough that keying on the raw string splits one plugin's
    # history in two.
    return (plugin or "").strip().lower() or "*"


def _merge_newer(merged: dict, entry, keys) -> None:
    """Fold `entry`'s records for `keys` into `merged`, newest version winning.

    The same three-line "is this stamp newer than the one I have" dance was
    written out once per tier (mod, sibling, global); one copy is enough.
    """
    if not isinstance(entry, dict):
        return
    for key in keys:
        at = entry.get(key)
        if not isinstance(at, str):
            continue
        have = merged.get(key)
        if have is None or (version_key(at) or (0, 0)) > (
                version_key(have) or (0, 0)):
            merged[key] = at


def steps_run_at(plugin: str | None) -> dict[str, str]:
    """{step_key: version} for every step recorded for *plugin*.

    Three tiers of ownership are merged in, newest stamp winning:

    * the plugin's own key -- steps that convert THAT plugin's records;
    * its MOD's key (`GROUP_STEPS`) -- an imported mod's plugins share one
      asset tree, so converting it once counts for every plugin that reads it;
    * the shared key (`GLOBAL_STEPS`) -- one artifact for the whole load order,
      so running it while converting Oblivion counts for Nehrim too.

    Without the last two merges every plugin but the one a step happened to run
    alongside sees a step that "never ran" and re-selects it on every check.

    A per-plugin entry left by an older build still counts -- the newer of the
    two wins, so history recorded under the old scheme is not thrown away and
    does not make the step look stale again.
    """
    steps = _load_state().get("steps", {})
    merged: dict[str, str] = {}
    got = steps.get(_plugin_key(plugin))
    if isinstance(got, dict):
        merged.update({k: v for k, v in got.items() if isinstance(v, str)})

    # Shared-asset steps recorded against this plugin's MOD count for it: one
    # conversion of the group payload covers every plugin that reads it, so
    # running Meshes for one plugin of a resource pack must not leave its
    # siblings looking like they still owe the step.
    gkey = _group_key(plugin)
    if gkey:
        _merge_newer(merged, steps.get(gkey), GROUP_STEPS)
        # History written before shared-asset steps were group-scoped sits
        # under a SIBLING's own key. Read it across the group so an existing
        # conversion is not reported as still owed. Read-only, like the
        # GLOBAL_STEPS lift: the next successful run records it properly.
        for sib in _group_siblings(plugin):
            _merge_newer(merged, steps.get(_plugin_key(sib)), GROUP_STEPS)

    if _plugin_key(plugin) != GLOBAL_PLUGIN_KEY:
        _merge_newer(merged, steps.get(GLOBAL_PLUGIN_KEY), GLOBAL_STEPS)
    return merged


def installed_version_for(plugin: str | None) -> str | None:
    """Oldest version among the steps recorded for *plugin*.

    OLDEST, not newest: the upgrade owed is whatever the most out-of-date step
    owes.  Taking the newest would silently forgive every step that has not run
    since.
    """
    versions = [v for v in steps_run_at(plugin).values() if version_key(v)]
    if not versions:
        return None
    return min(versions, key=lambda v: version_key(v) or (0, 0))


# ---------------------------------------------------------------------------
# Upgrade plan
# ---------------------------------------------------------------------------

# (table, fetched_ok).  Cached for the process: the GUI asks for a plan on
# every plugin selection and must not re-hit the network each time.  A FAILED
# fetch is cached too -- an offline user would otherwise pay the full timeout on
# every combo-box change, freezing the UI repeatedly.  `refresh=True` is the
# escape hatch for a user who reconnects and asks again.
_TABLE: list[tuple[dict[str, list[str]], bool] | None] = [None]




def _get_json(url: str, timeout: int):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return json.load(fh)


#: One checklist line of a tag message: `[x] 3. Meshes`, or a global `[x] Create LOD`.
_STEP_LINE = re.compile(r"^\s*\[[xX ]\]\s*(.+?)\s*$")

# Proves the tag was cut by the release workflow and its checklist is therefore
# authoritative -- including when the checklist is empty.  Must stay in step
# with release_notes.build_notes; a test asserts they agree.
_CHECKLIST_HEADING = "Steps to re-run in the GUI:"


def steps_from_tag_message(message: str) -> list[str]:
    """The step labels checked off in an annotated tag's message.

    The tag message is ALREADY the authoritative record -- release_notes.py
    writes the same checklist into it that it prints in the release notes -- so
    reading it back needs no published file, nothing committed, and nothing
    kept in sync.  A tag cut by hand simply has no checklist, which reads as a
    hole and selects everything.
    """
    steps = [m.group(1) for line in message.splitlines()
             if (m := _STEP_LINE.match(line))]
    # Keep only labels we recognise, in run order, so a typo in a hand-written
    # tag cannot inject a step key the GUI has no checkbox for.
    known = [label for _key, label in STEP_KEYS]
    return [label for label in known if label in steps]


def steps_table(timeout: int = 8, refresh: bool = False
                ) -> tuple[dict[str, list[str]], bool]:
    """({version: [step labels]}, reachable), read from the git TAGS.

    NETWORK CALL -- never invoke this on the UI thread.

    No file is involved, published or committed.  Each release's annotated tag
    message already carries its "Steps to re-run in the GUI" checklist, so the
    tags ARE the table: nothing to write at release time, nothing that can go
    stale against them, and no commit on master -- which is what lets a
    PR-merge release work at all.

    `reachable` answers "was GITHUB reachable", NOT "did we get a table".  An
    HTTP error response is a REACHABLE server, which is a hole in our data
    rather than a problem with the user's connection; reporting that as offline
    told users with working internet to go and fix their connection.  Only a
    transport failure -- DNS, TLS, refused, timeout -- is genuinely offline.

    Either way an empty table makes the caller select every step; the flag only
    decides which explanation the user is given.
    """
    if _TABLE[0] is not None and not refresh:
        return _TABLE[0]

    table: dict[str, list[str]] = {}
    reachable = False
    try:
        got = _get_json(_RELEASES_API, timeout)
        reachable = True
        for rel in got if isinstance(got, list) else []:
            name = str(rel.get("tag_name", ""))
            # Release tags only: navmesh-cache-* is a different series and
            # would otherwise be read as a version.
            if not version_key(name) or name != _base_tag(name):
                continue
            body = str(rel.get("body") or "")
            # An empty checklist is a REAL entry, not a missing one: a docs-only
            # release genuinely owes nothing.  Dropping it would leave a hole,
            # and a hole selects all twelve steps forever.  Only a body with no
            # checklist at all (a release cut by hand) is absent.
            if _CHECKLIST_HEADING in body:
                table[name] = steps_from_tag_message(body)
    except urllib.error.HTTPError:
        reachable = True          # the server answered; we just have no data
    except Exception:
        # Transport failure, or a body that would not parse.  Never propagates:
        # the caller's fallback (select everything) is always safe, whereas an
        # exception on a plugin-selection handler would take the GUI down.
        reachable = False

    _TABLE[0] = (table, reachable)
    return _TABLE[0]




#: (key, label) per-plugin then global. See: docs/commentary/version_upgrade_planning.md#one-table-not-four
STEP_KEYS: list[tuple[str, str]] = (
    [(k, label) for k, _flag, label, *_rest in STEPS]
    + [(k, label) for k, label, *_rest in GLOBAL_ACTIONS])

_LABEL_TO_KEY = {label: key for key, label in STEP_KEYS}
_LABEL_OF     = {key: label for key, label in STEP_KEYS}

#: One artifact for the load order. See: docs/commentary/version_upgrade_planning.md#steps-that-belong-to-no-plugin
GLOBAL_STEPS: frozenset[str] = frozenset(k for k, *_rest in GLOBAL_ACTIONS)

# The state-file key those steps are recorded under.  `_plugin_key(None)`
# already collapses to "*", which is exactly "belongs to no plugin".
GLOBAL_PLUGIN_KEY = "*"


# Steps that consume the SHARED asset tree of an imported mod. Plugins from one
# archive read the same meshes/textures/sound/trees and write into one output
# folder, so running "Meshes" for any one of them converts the payload for all
# of them -- it is the same work over the same files, and re-running it for a
# sibling would redo identical work and produce identical bytes.
#
# Recorded once per MOD (key "mod:<group_id>") rather than per plugin, exactly
# as GLOBAL_STEPS is recorded once for the whole load order. Without this, a
# three-plugin resource pack showed Meshes as still owed for two of its plugins
# after it had already been converted.
#
# The record-driven steps are deliberately absent: export, import_, scripts and
# creatures each read THAT plugin's own .txt dump and produce that plugin's own
# ESM, so they remain per-plugin.
#
# "Sounds" is absent too, and it is the interesting case. Its non-voice half
# (sound/fx/) really is shared, but VOICE is per plugin end to end: the source
# tree is partitioned as `sound/voice/<plugin>/`, the output goes to
# `Sound/Voice/<plugin>/`, FormIDs are shifted by THAT plugin's load-order
# index, and the filename prefixes come from that plugin's own
# `<esm>.voicemap.txt` / `.liptext.txt`. Marking it group-wide would tell the
# user a sibling's voice lines were converted when they were never touched.
GROUP_STEPS: frozenset[str] = frozenset({"extract", "meshes", "speedtrees"})


def _group_siblings(plugin: str | None) -> list:
    """Other plugins sharing `plugin`'s imported-mod asset tree."""
    if not plugin:
        return []
    try:
        from asset_convert.sources import source_registry
    except ImportError:
        return []
    try:
        members = source_registry.group_members(SCRIPT_DIR / 'export', plugin)
    except Exception:
        return []
    return [n for n in members if _plugin_key(n) != _plugin_key(plugin)]


def _group_key(plugin: str | None) -> str | None:
    """`mod:<group_id>` when `plugin` belongs to an imported mod, else None.

    The registry lives in asset_convert, which version.py must not hard-depend
    on -- it is imported by tools that never load the pipeline. An unavailable
    registry simply means "no group", and the step falls back to per-plugin
    recording, which is the pre-existing behaviour.
    """
    if not plugin:
        return None
    try:
        from asset_convert.sources import source_registry
    except ImportError:
        return None
    try:
        entry = source_registry.get(SCRIPT_DIR / 'export', plugin)
    except Exception:
        return None
    if not entry:
        return None
    gid = entry.get("group_id")
    return f"mod:{gid}" if gid else None


def label_to_key(label: str) -> str | None:
    return _LABEL_TO_KEY.get(label)


def steps_between(from_version: str, to_version: str) -> list[str] | None:
    """Step labels owed by upgrading from_version -> to_version.

    Unions every table entry in (from, to] -- an upgrade that skips four
    releases owes the union of all four, not just the newest one's steps.

    Returns None when the table cannot answer honestly: an unreachable or
    missing table, or a gap where some intervening version has no entry.  None
    means "unknown", which the caller must render as "re-run everything" rather
    than "nothing" -- a silent empty list would tell the user their stale output
    is current.

    NETWORK CALL (via `steps_table` / `steps_for_tag`) -- never invoke this on
    the UI thread.

    Only the tags INSIDE the range have their message fetched.  Reading all of
    them cost one request per tag and the anonymous API allows 60 an hour, so a
    single refresh exhausted the quota and everything afterwards got a 429.
    """
    lo, hi = version_key(from_version), version_key(to_version)
    if not lo or not hi or lo >= hi:
        return []

    table, _reachable = steps_table()
    if not table:
        return None

    covered = {v: k for v in table if (k := version_key(v))}
    # Every release in the range must be present, or the union has a hole.
    in_range = {v: k for v, k in covered.items() if lo < k <= hi}
    if not in_range or max(in_range.values()) != hi:
        return None

    owed: set[str] = set()
    for version in in_range:
        owed.update(table[version])

    order = [label for _key, label in STEP_KEYS]
    return [label for label in order if label in owed]


def upgrade_plan(plugin: str | None) -> dict:
    """What the user must re-run for *plugin* after pasting in this version.

    NETWORK CALL (the steps table is fetched) -- run this on a worker thread.

    Returns a dict the GUI and CLI both render:
      current      -- version of this source tree
      installed    -- oldest version any recorded step ran at (None if never)
      upgraded     -- True when installed < current
      steps        -- step KEYS to re-tick, run-ordered
      unknown      -- True when the range could not be resolved and `steps`
                      is a conservative everything-list rather than a
                      measured one
      offline      -- True when `unknown` is due to the table being
                      unreachable, as opposed to a genuine hole in it.  The
                      distinction is the user's to act on: a hole is ours to
                      fix, no connection is theirs.
      never_run    -- True when nothing has ever been converted for `plugin`
    """
    current   = current_version()
    installed = installed_version_for(plugin)
    all_keys  = [key for key, _ in STEP_KEYS]
    ran       = steps_run_at(plugin)

    # "Never converted" is about THIS plugin, so it is decided only by steps
    # that belong to it. A shared step (the mod's asset tree, or a load-order
    # wide artifact) can be recorded before this plugin has been converted at
    # all -- converting a sibling of the same resource pack is enough -- and
    # counting those made a plugin with no export and no ESM claim it had been
    # run. That is the exact inversion the "Not converted" state exists to
    # prevent.
    own = {k: v for k, v in ran.items()
           if k not in GROUP_STEPS and k not in GLOBAL_STEPS}

    if not own:
        # Nothing recorded: a first run, so everything is owed, but this is a
        # fresh install rather than an upgrade -- the GUI says so differently.
        return {"current": current, "installed": None, "upgraded": False,
                "steps": [], "unknown": False, "offline": False,
                "never_run": True}

    # Each step is judged against ITS OWN recorded version, never against one
    # number for the whole plugin.  The state file is per-step precisely because
    # the steps drift apart: a user who re-runs Import today and leaves Export
    # at last week's release has one step current and one stale.
    #
    # Collapsing them -- asking steps_between(oldest, current) once and applying
    # that single union to all twelve -- is what made the shortcut tick steps the
    # user had ALREADY run at this exact version.  The oldest step drags the
    # range down, the range's union names every step that release touched, and
    # steps sitting at `current` get swept in with the stale ones.  The union is
    # right for the step that is actually at `oldest`; it says nothing about a
    # step that is already up to date.
    keys: list[str] = []
    unknown = False
    for key in all_keys:
        at = ran.get(key)
        if at is None or version_key(at) is None:
            # Never run (or an unparseable stamp): owed regardless of the table.
            keys.append(key)
            continue
        labels = steps_between(at, current)
        if labels is None:
            # The range for THIS step could not be resolved.  Unknown fails
            # toward re-running, never toward skipping.
            unknown = True
            keys.append(key)
            continue
        if _LABEL_OF.get(key) in labels:
            keys.append(key)

    if unknown:
        # Distinguish "could not ask" from "asked, and the table has a hole".
        # Both select conservatively; only the first is worth telling the user
        # to reconnect over.
        _table, reachable = steps_table()
        return {"current": current, "installed": installed, "upgraded": True,
                "steps": [k for k in all_keys if k in keys], "unknown": True,
                "offline": not reachable, "never_run": False}

    return {"current": current,
            "installed": installed,
            "upgraded": version_key(installed) != version_key(current),
            "steps": [k for k in all_keys if k in keys],
            "unknown": False,
            "offline": False,
            "never_run": False}


def describe_plan(plan: dict) -> str:
    """One-line human summary of `upgrade_plan`, for the CLI and the GUI log.

    Plain ASCII: this is printed to a Windows console, which is cp1252 by
    default and raises UnicodeEncodeError on an em dash or an arrow.  The GUI's
    own banner is a Tk widget and can use nicer punctuation.
    """
    cur = plan["current"]
    if plan["never_run"]:
        return f"Version {cur} - no previous conversion recorded."
    # An empty step list IS up to date, whatever `upgraded` says.  Those two can
    # disagree: `installed` is the oldest recorded step and now includes the
    # shared plugin-independent ones, so a plugin whose every step is current
    # can still show an old `installed` -- which read as "upgraded, re-run:
    # nothing".
    if not plan["steps"]:
        return f"Version {cur} - up to date, nothing needs re-running."

    label_of = dict(STEP_KEYS)
    names = ", ".join(label_of.get(k, k) for k in plan["steps"]) or "nothing"
    if plan["unknown"]:
        why = ("could not reach GitHub for the step list"
               if plan.get("offline") else "cannot tell which steps changed")
        # Not always every step: steps already run at this version stay
        # unselected even when another step's range is unresolvable.
        return (f"Version {cur} (was {plan['installed']}) - {why}, "
                f"so these are selected to be safe: {names}")
    if not plan["upgraded"]:
        return f"Version {cur} - steps never run: {names}."
    return (f"Version {cur} (was {plan['installed']}) - re-run: {names}")


# ---------------------------------------------------------------------------
# Update check
# ---------------------------------------------------------------------------

def latest_release(timeout: int = 8) -> str | None:
    """Newest release tag on the remote, or None if it cannot be determined.

    NETWORK CALL -- never invoke this on the UI thread.
    """
    try:
        req = urllib.request.Request(_TAGS_API, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            payload = json.load(fh)
    except Exception:
        return None
    if not isinstance(payload, list):
        return None

    best, best_key = None, None
    for row in payload:
        name = row.get("name") if isinstance(row, dict) else None
        if not isinstance(name, str):
            continue
        # Release tags only: the repo also carries navmesh-cache-* tags, which
        # are a different series and must never be offered as an update.
        if name != _base_tag(name):
            continue
        key = version_key(name)
        if key and (best_key is None or key > best_key):
            best, best_key = name, key
    return best


def check_for_update(timeout: int = 8) -> dict:
    """Compare this install against the newest published release.

    NETWORK CALL -- run on a worker thread.  Returns:
      current   -- this tree's version
      latest    -- newest release tag, or None if the check failed
      available -- True when `latest` is strictly newer than `current`
      reachable -- False when the remote could not be queried
    """
    current = current_version()
    latest = latest_release(timeout=timeout)
    if latest is None:
        return {"current": current, "latest": None,
                "available": False, "reachable": False}

    here, there = version_key(current), version_key(latest)
    # A dev tree sitting on the newest tag with local commits ('0.58+') is not
    # behind, so compare on the base tag and treat equal as up to date.
    available = bool(here and there and there > here)
    return {"current": current, "latest": latest,
            "available": available, "reachable": True}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Report the installed version and "
                                             "which pipeline steps an upgrade "
                                             "requires re-running.")
    ap.add_argument("-f", "--file", default=None,
                    help="Plugin to report on (default: any)")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    args = ap.parse_args()

    plan = upgrade_plan(args.file)
    if args.json:
        json.dump(plan, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(describe_plan(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

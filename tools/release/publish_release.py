#!/usr/bin/env python
"""Release the commit a push moves master to: build TESRACT from it, tag it, publish the GitHub release.

    python tools/release/publish_release.py --install              # add the step to .git/hooks/pre-push
    python tools/release/publish_release.py --sha HEAD --dry-run   # build and write the notes; nothing published
    python tools/release/publish_release.py --sha <commit>         # release by hand (a push that skipped the hook)

The pre-push hook runs it with git's ref lines on stdin. The commit is checked out in the worktree .release/src (so
uncommitted edits never ship) and built there by build_exe.py; then it is tagged with the next version, the tag is
pushed, and the release is created with TESRACT-<tag>.zip and the source zip and announced on Discord. A failure
blocks the push. Master's push then finds its commit tagged, so tag-on-push.yml does nothing.
See: docs/commentary/core_gui.md#releases
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import version as version_info
from core.gui import exe_update
from output_layout import DATA_ROOT_ENV, REPO_ROOT
from tools.navmesh import navmesh_cache as nc
from tools.navmesh.navmesh_cache_hook import install_hook as install_navmesh_hook
from tools.navmesh.navmesh_cache_hook import next_tag
from tools.release import discord_release_notify, release_notes

#: Where releases are built: the worktree SRC and the notes and source zip beside it (gitignored).
WORK = REPO_ROOT / ".release"
#: The worktree each released commit is checked out in; its ignored build/ keeps Nuitka's output between releases.
SRC = WORK / "src"
#: The sha git's pre-push lines give a ref being deleted.
NULL_SHA = "0" * 40
#: Variables git exports to hooks that would point a git command in SRC at the user's own tree.
GIT_LOCATION_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_QUARANTINE_PATH")
#: The environment every command here runs in: no git location, and no data root inherited from this checkout.
CLEAN_ENV = {k: v for k, v in os.environ.items() if k not in GIT_LOCATION_VARS and k != DATA_ROOT_ENV}
HOOK_MARKER = "# >>> tesract-release >>>"
HOOK_END = "# <<< tesract-release <<<"
#: The pre-push step; $navmesh_stdin is git's ref list, captured by the navmesh gate's prelude.
HOOK_SNIPPET = f"""{HOOK_MARKER}
# Added by tools/release/publish_release.py --install
# A push that moves master first releases that commit (TESRACT build, tag, GitHub release); a failure blocks it.
echo "$navmesh_stdin" | python tools/release/publish_release.py --pre-push "$1" || exit 1
{HOOK_END}
"""


def git(*args: str, cwd: Path = REPO_ROOT) -> str:
    """Stdout of `git <args>` in `cwd`, stripped; raises CalledProcessError on failure."""
    return subprocess.run(["git", *args], cwd=cwd, env=CLEAN_ENV, check=True, capture_output=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def master_update(lines: list) -> str | None:
    """The commit a pre-push ref list moves master to; None when it leaves master alone or deletes it."""
    for line in lines:
        parts = line.split()
        if len(parts) == 4 and parts[2] == "refs/heads/master" and parts[1] != NULL_SHA:
            return parts[1]
    return None


def code_tag_at(sha: str) -> str | None:
    """The release tag already on `sha` (a release whose push failed after tagging), if any."""
    return next((t for t in git("tag", "--points-at", sha).split() if version_info.version_key(t)), None)


def checkout(sha: str) -> None:
    """Check `sha` out in the worktree SRC with nothing the commit lacks (ignored build output stays)."""
    if not (SRC / ".git").exists():
        WORK.mkdir(exist_ok=True)
        git("worktree", "add", "--detach", str(SRC), sha)
        return
    if Path(git("rev-parse", "--show-toplevel", cwd=SRC)).resolve() != SRC.resolve():
        raise RuntimeError(f"{SRC} is not its own git worktree; delete it and run again.")
    git("checkout", "--detach", "--force", sha, cwd=SRC)
    git("clean", "-ffd", cwd=SRC)


def build(tag: str) -> Path:
    """Compile SRC with build_exe.py stamped `tag`; returns the release zip. Raises when the build fails."""
    subprocess.run([sys.executable, "tools/release/build_exe.py", "--zip", "--version", tag], cwd=SRC,
                   env=CLEAN_ENV, check=True)
    return SRC / "build" / "exe" / exe_update.asset_name(tag)


def write_notes(tag: str, sha: str) -> Path:
    """The tag message and release body: release_notes.py's for `sha`, or the message `tag` already has."""
    path = WORK / "notes.txt"
    if code_tag_at(sha) == tag:
        text = git("tag", "-l", "--format=%(contents)", tag) + "\n"
    else:
        text = release_notes.build_notes(tag, release_notes.previous_tag(), sha)
    path.write_text(text, encoding="utf-8")
    return path


def source_zip(tag: str) -> Path:
    """`git archive` of `tag` as `<repo>-<tag>/`, VERSION expanded to the tag."""
    name = f"{version_info.REPO.split('/')[1]}-{tag}"
    path = WORK / f"{name}.zip"
    git("archive", "--format=zip", f"--prefix={name}/", "-o", str(path), tag)
    return path


def publish(remote: str, tag: str, assets: list, notes: Path) -> None:
    """Push `tag`, then create its GitHub release with `assets` (or re-upload them to an existing one)."""
    git("push", remote, f"refs/tags/{tag}")
    repo = nc.gh_repo()
    exists = subprocess.run(["gh", "release", "view", *repo, tag], cwd=REPO_ROOT, env=CLEAN_ENV,
                            capture_output=True).returncode == 0
    if exists:
        cmd = ["gh", "release", "upload", *repo, tag, *assets, "--clobber"]
    else:
        cmd = ["gh", "release", "create", *repo, tag, *assets, "--title", tag, "--notes-file", str(notes),
               "--latest", "--verify-tag"]
    subprocess.run(cmd, cwd=REPO_ROOT, env=CLEAN_ENV, check=True)


def release(sha: str, remote: str, dry_run: bool = False) -> int:
    """Build, tag, publish and announce `sha`; with `dry_run`, only build it and write its notes."""
    fresh = next_tag()
    tag = code_tag_at(sha) or fresh
    print(f"release: {sha[:8]} is {tag}; building TESRACT in {SRC}", flush=True)
    checkout(sha)
    windows = build(tag)
    notes = write_notes(tag, sha)
    if dry_run:
        print(f"release: --dry-run, nothing published. Built {windows}; notes in {notes}")
        return 0
    if tag == fresh:
        git("tag", "-a", tag, sha, "-F", str(notes))
    assets = [f"{windows}#TESRACT {tag} (Windows)", f"{source_zip(tag)}#TES Auto-Convert {tag} (source zip)"]
    publish(remote, tag, assets, notes)
    print(f"release: published {tag}", flush=True)
    discord_release_notify.announce(notes.read_text(encoding="utf-8"), tag, version_info.REPO)
    return 0


def pre_push(remote: str) -> int:
    """The hook: release the commit this push moves master to; returns 1 (blocking the push) on failure."""
    sha = master_update(sys.stdin.read().splitlines())
    if sha is None:
        return 0
    try:
        return release(sha, remote)
    except (subprocess.CalledProcessError, RuntimeError, OSError) as exc:
        detail = getattr(exc, "stderr", None) or exc
        print(f"\nPUSH BLOCKED: the release of {sha[:8]} failed: {detail}")
        print("Fix it and push again. `git push --no-verify` pushes without a release; tag-on-push.yml then "
              "tags the commit with no TESRACT zip (publish one with --sha afterwards).")
        return 1


def install() -> int:
    """Add the release step to .git/hooks/pre-push, below the navmesh gate whose stdin capture it reads."""
    install_navmesh_hook()
    path = REPO_ROOT / ".git" / "hooks" / "pre-push"
    text = path.read_text(encoding="utf-8")
    block = HOOK_SNIPPET.strip("\n")
    if HOOK_MARKER in text:
        start = text.index(HOOK_MARKER)
        text = text[:start] + block + text[text.index(HOOK_END, start) + len(HOOK_END):]
    else:
        text = text.rstrip("\n") + "\n\n" + HOOK_SNIPPET
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"Installed the release step in {path}")
    return 0


def main() -> int:
    """CLI: --install, --pre-push <remote> (the hook), or --sha <commit> [--dry-run]."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", action="store_true", help="add the release step to .git/hooks/pre-push")
    group.add_argument("--pre-push", metavar="REMOTE", help=argparse.SUPPRESS)
    group.add_argument("--sha", help="release this commit by hand")
    ap.add_argument("--remote", default="origin", help="with --sha: the remote to push the tag to")
    ap.add_argument("--dry-run", action="store_true", help="with --sha: build and write the notes only")
    args = ap.parse_args()
    if args.install:
        return install()
    if args.pre_push:
        return pre_push(args.pre_push)
    return release(git("rev-parse", args.sha), args.remote, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

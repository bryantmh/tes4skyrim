#!/usr/bin/env python
"""Post a release changelog to a Discord channel via an incoming webhook.

Used by tools/release/publish_release.py (and the fallback in
.github/workflows/tag-on-push.yml) right after the GitHub Release is created,
but it runs standalone against any notes file:

    python tools/release/discord_release_notify.py --notes notes.txt --tag 0.591 \
                                           --repo bryantmh/tes4skyrim
    python tools/release/discord_release_notify.py --notes notes.txt --tag 0.591 --dry-run

The webhook URL comes from $DISCORD_WEBHOOK_URL (a repo secret in CI), else
the gitignored file .discord_webhook in the repo root, and is
never accepted on the command line -- an argv secret is visible to every other
process on the machine via the process table, and CI would echo it into the log
with `set -x`.

ONLY code releases go through here.  The navmesh cache publishes its own
`navmesh-cache-*` releases from tools/navmesh/navmesh_cache.py on a developer machine,
which no workflow drives, so a cache publish never reaches this script.

Written against urllib, not requests: the workflow's runner has a bare Python
with no pip install step, and this needs exactly one POST.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Discord rejects the whole payload (HTTP 400) if an embed description exceeds
# 4096 characters, so a big release would silently lose its announcement
# entirely rather than degrade.  Budget below the cap: the code fence and the
# truncation marker are part of the description too, and a multi-byte subject
# line costs more than one character in Discord's count.
DESCRIPTION_LIMIT = 4096
BODY_BUDGET = 3800

TRUNCATION_MARKER = "... (truncated -- read the full notes on the release page)"

# GitHub's release-green, so the embed stripe matches the source of the notes.
EMBED_COLOR = 0x2EA043

#: The webhook URL on a developer machine when $DISCORD_WEBHOOK_URL is unset (gitignored).
WEBHOOK_FILE = Path(__file__).resolve().parents[2] / ".discord_webhook"


def truncate(body: str, budget: int = BODY_BUDGET) -> str:
    """Trim `body` to `budget` characters on a LINE boundary.

    Cutting mid-line would split a commit hash or a `[x]` checkbox and read as
    corruption rather than as omission; cutting on a newline leaves every line
    shown intact and obviously incomplete.
    """
    if len(body) <= budget:
        return body
    head = body[:budget]
    # rsplit gives the whole head back when there is no newline in it (one
    # pathologically long line) -- take the hard cut in that case.
    if "\n" in head:
        head = head.rsplit("\n", 1)[0]
    return head.rstrip() + "\n" + TRUNCATION_MARKER


def strip_title_line(notes: str, tag: str) -> str:
    """Drop the leading `Release <tag>` line, which the embed title repeats.

    release_notes.py opens its notes with that line, and it has to stay there:
    the notes ARE the annotated tag message and the GitHub release body, and
    version.py parses those bodies back.  So the de-duplication happens here,
    at the point of display, rather than upstream where it would rewrite what
    the tags and releases say.

    Matched against the tag we were actually given, so a notes file whose first
    line is something else is passed through untouched instead of losing a real
    line of content.
    """
    lines = notes.split("\n")
    if lines and lines[0].strip() == f"Release {tag}":
        del lines[0]
        # The blank line that separated the title from the body would now be a
        # leading blank inside the code fence.
        while lines and not lines[0].strip():
            del lines[0]
    return "\n".join(lines)


def build_payload(notes: str, tag: str, repo: str | None) -> dict:
    """Discord webhook JSON for one release.

    The notes go in a code fence: release_notes.py emits plain text aligned
    with two-space indents, and Discord's markdown would eat that layout --
    `[x]` becomes an empty link, and the indented commit lines reflow.
    """
    body = truncate(strip_title_line(notes, tag).strip())
    embed = {
        "title": f"Release {tag}",
        "description": "```\n" + body + "\n```",
        "color": EMBED_COLOR,
    }
    if repo:
        embed["url"] = f"https://github.com/{repo}/releases/tag/{tag}"
    return {"embeds": [embed]}


def post(webhook_url: str, payload: dict, timeout: int = 20) -> None:
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            # Discord 403s a default urllib agent on some edge nodes.
            "User-Agent": "tes4skyrim-release-notifier/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        # A webhook POST answers 204 No Content on success.
        if response.status not in (200, 204):
            raise RuntimeError(f"Discord returned HTTP {response.status}")


def main() -> int:
    """CLI: post (or with --dry-run, print) the notes file's announcement."""
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--notes", required=True,
                    help="Path to the release notes file to post")
    ap.add_argument("--tag", required=True,
                    help="Release tag name, e.g. 0.591")
    ap.add_argument("--repo", default=None,
                    help="owner/name, used to link the embed at the release")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the payload instead of posting it")
    args = ap.parse_args()

    notes_path = Path(args.notes)
    if not notes_path.is_file():
        print(f"ERROR: no such notes file: {notes_path}", file=sys.stderr)
        return 1
    notes = notes_path.read_text(encoding="utf-8")

    if args.dry_run:
        print(json.dumps(build_payload(notes, args.tag, args.repo), indent=2))
        return 0
    return announce(notes, args.tag, args.repo)


def webhook_url() -> str:
    """$DISCORD_WEBHOOK_URL, else WEBHOOK_FILE's text; empty when neither is set."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url and WEBHOOK_FILE.is_file():
        url = WEBHOOK_FILE.read_text(encoding="utf-8").strip()
    return url


def announce(notes: str, tag: str, repo: str | None) -> int:
    """Post release `tag`'s notes; 0 when posted, 1 (with the reason, Discord's HTTP error body included) otherwise."""
    url = webhook_url()
    if not url:
        print("ERROR: no Discord webhook (DISCORD_WEBHOOK_URL or .discord_webhook).", file=sys.stderr)
        return 1
    payload = build_payload(notes, tag, repo)
    try:
        post(url, payload)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        print(f"ERROR: Discord rejected the post: HTTP {exc.code} {detail}",
              file=sys.stderr)
        return 1
    except (urllib.error.URLError, RuntimeError, OSError) as exc:
        print(f"ERROR: could not reach Discord: {exc}", file=sys.stderr)
        return 1

    print(f"Announced {tag} on Discord.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

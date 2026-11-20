# What agents hit at the code-rules gate — 2026-09-26

**Corpus:** every Claude Code transcript with a tool result after the current
gate landed (87db5215, 2026-09-23 14:17 local), through 2026-09-26: about 2.8
days of sessions. Refusals were parsed from the gate's own messages. Earlier
transcripts are excluded because 68a51ee1, that morning, moved import/use and
citation checks to turn end; before it those were the largest bucket (772
sites), after it they almost vanish from Edit refusals.

## Refusals under the current gate

| Kind | Refusals | What is behind it |
|---|---|---|
| Shell command refused before running (`shell_route`) | 208 | 90 were only read-only helpers (`ls`, `cat`, `grep`, `head`), refused because nothing was wrapped. ~35 were read-only `git`, `sed -n`, `find` or PowerShell `Get-*`. ~30 put a bare `python` beside the wrapper. 16 were quoting or `$(...)` the splitter refuses |
| Edit/Write refused (`--gate-diff`) | 222 | See the next table |
| Shell command wrote rule-breaking code (`safe_run`) | 98 | 57 blamed only files the session never named in any tool call: `safe_run` gates every `.py` that changed while it ran, including other sessions' concurrent edits. It also gates import/use and citation pairs at once, where the Edit hook defers them |

Edit refusals, each counted once per rule it names:

| Rule | Refusals | Notes |
|---|---|---|
| bloated-docstrings | 93 | Mostly the 80-char limit on a 1–2 line body; median overshoot 18 chars |
| long-functions | 35 | 34 were functions already over the limit before the edit |
| missing-docstrings | 33 | 23 were test functions |
| stray-comments / inline-comments | 39 | 82 of 113 flagged comments were old ones above the changed lines |
| dead-imports | 16 | |
| fat-attr-docs, private-imports, unsectioned-defs | 20 | The last two were not mentioned in CLAUDE.md |
| deep-nesting, god-functions, broken-syntax, multi-return-fns | 16 | |

313 of 344 flagged sites cleared on the next attempt: the cost is round trips
(~530 in 2.8 days), not agents stuck.

## Changes made

- `shell_route.escape` passes a line of `READ_ONLY` helpers alone. A no-op wrapper
  call appended to such a line always passed, so requiring one closed nothing.
- CLAUDE.md now states the docstring budget (480, or 80 on a 1–2 line body),
  that tests need docstrings too, the section-heading and private-import rules,
  and to run `--gate-file` before editing a large function.

### <a id="holes-closed"></a>Holes closed

Probing `escape()` found four routes that wrote a `.py` outside the wrapper and
passed beside a no-op wrapper call: `sort -o a.py`, `uniq IN a.py`, process
substitution `cat <(python …)`, and a PowerShell sub-expression
`ls (Set-Content a.py 1)` (also as a wrapper argument). `escape` now refuses any
unquoted `(` or `{` other than `${`, `sort -o`/`--output`, and `uniq` with two
file operands. Each is a case in `tests/test_code_rules.py`.

Not done, because each has a hole: letting `git`/`sed`/`find`/PowerShell
cmdlets run bare (`git --output=`, `GIT_EXTERNAL_DIFF=`, `sed w`/`e`,
`find -exec`, script blocks); `safe_run` skipping files in other sessions'
turn records (a crashed session's record would exempt its files forever unless
limited to records written during the command); `safe_run` deferring pairs
(if the handoff to the turn-end check fails, the check never runs).

## Replay of the change

Every Bash/PowerShell command in the corpus (7,101) was re-run through the old
and new `escape()`:

| | New: allowed | New: refused |
|---|---|---|
| Old: allowed | 6,893 | 0 |
| Old: refused | 94 | 114 |

Shell refusals drop from 208 to 114, and no command that passed before is
refused now. The instruction changes cannot be replayed; they address the
refusals naming bloated-docstrings, missing-docstrings, private-imports and
unsectioned-defs (137 rule mentions).

# Token cost of the gate — 2026-11-19

**Corpus:** the 84 transcripts kept for this project (main sessions and
subagents), 49,080 API calls. Each refusal was charged its message, the
rejected tool input (output thrown away) and the usage of the next API call,
the retry. Tokens are weighted to input price: output 5x, cache write 1.25x,
cache read 0.1x. Total: 1,851M input-equivalent tokens.

| Kind | Refusals | Share of all spend |
|---|---|---|
| Shell command refused (`shell_route`) | 1,136 | 1.75% |
| Edit/Write refused (`--gate-diff`) | 288 | 0.64% |
| Doc links, turn end, other | 12 | 0.01% |

Nearly all of it is the retry round trip, not the message or the lost edit.
Of the Edit refusals, 74 (26%) caught real defects: 34 dead imports, 37 dead
citations, 3 syntax errors. 178 (62%) named `bloated-docstrings`.

### <a id="docstring-limits-split"></a>Docstring limits, split

Distinct sites across every report in the corpus (refusals, `safe_run`,
`--gate-file`), by the limit that applied:

| Limit | Sites | Over by ≤10 | 11–30 | 31–100 | >100 |
|---|---|---|---|---|---|
| 80 (1–2 line body) | 156 | 63 | 53 | 16 | 24 |
| 480 | 114 | 12 | 19 | 23 | 60 |

The 480 limit catches real narration: over half its sites are more than 100
over, many 700–2,000 characters. The 80 limit mostly charged half a sentence.
`TINY_DOC_CHARS` is now 110, which clears 116 of its 156 sites.

### <a id="shell-refusals-become-rewrites"></a>Shell refusals become rewrites

A refused shell command always came back as the same command wrapped by hand,
so for Bash the hook now returns that wrapped command through `updatedInput`
and the call runs at once (support confirmed in Claude Code 2.1.327's hook
schema: "Modified tool input (PreToolUse only)"). `safe_run.py` still gates
every write, so nothing is less strict. PowerShell, 165 of the raw refusal
markers (Bash: 1,929), is still refused; the reason is in
[the architecture doc](../reference/script_convert_architecture.md#a-command-outside-the-wrapper-is-wrapped-not-refused). Still open: `safe_run` blames a command for
any `.py` that changed while it ran, including another session's edit; this
census hit one such false charge itself.

**Replay under both changes** (same corpus, agents still told 80 in CLAUDE.md):
refusals fall from 1,443 to 429 and their share of all spend from 2.41% to
0.76%. Bash refusals go from 1,036 to 122; the 122 ran outside the repo root,
where the rewrite's absolute path misses the allow rule and auto mode may
bounce it once. Edit refusals go from 293 to 193: 100 named only a 1–2 line
docstring of 81–110 chars. PowerShell (84) is unchanged.

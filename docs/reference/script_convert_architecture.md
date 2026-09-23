# `script_convert/` architecture — read this BEFORE writing any code here

> **The decision procedure is §3. If you are about to add or change something,
> start there — it routes any change to exactly one file.**
>
> Score every change: `python tools/script/arch_fitness.py --fail-on-regression`
> (per-file rules: `python tools/validate/code_rules.py --gate-file <path>`)
> (0.6s). Correctness is `psc_semantic_diff.py`, never pytest — see §6.

`script_convert/` converts TES4 (Oblivion) script source to Papyrus. It grew as
a line-by-line regex rewriter: it never built a representation of the script, so
every question needing structure — *is this expression balanced? is this
identifier an Actor? does this `If` have its `EndIf`?* — was re-answered by
pattern-matching **text the converter itself had just emitted**. Each new script
exposed a shape the rewriter mangled, the fix was another repair pass, and the
passes began to interact.

A parse tree now exists and is live. This document is the target it is being
rewritten toward, and the rules that keep it there.

---

## 1. The layers

**The pattern is an AST compiler.** Source becomes tokens, tokens become a tree,
the tree is resolved against a symbol table, and only then is Papyrus emitted.
Text flows one way. Nothing ever reads back what was emitted.

```
    TES4 source
        |  tes4/lexer.py          characters -> tokens
        v
     tokens
        |  tes4/parser.py         tokens -> AST   (never raises; degrades to Raw)
        v
      AST  (tes4/nodes.py)
        |  facts.py               tree -> ScriptFacts   (what this script needs)
        |  symbols.py             tree -> types, UDF signatures, ref classes
        v
    resolved tree
        |  emit/expr.py           Expr -> Value
        |  emit/stmt.py           Stmt -> Value
        |  emit/commands.py       Call -> Value   (COMMAND_ROWS, then commands/)
        |  emit/script.py         bodies -> laid-out lines
        v
    assemble/                     events, properties, the .psc file
        v
    pipeline.py                   jobs, batching, VMAD, writing
```

### Layer table — a module may import only from strictly LOWER layers

```
L0  naming.py                      stdlib only
L1  constants.py                   L0
L2  tes4/{lexer,parser,nodes}      stdlib only
    tes5/blocks.py                 stdlib only
    cross_ref.py                   L0, L1
L3  symbols.py, facts.py           L0-L2
    context.py                     L0, L1          (dataclasses only)
L4  resolve.py, emit/api.py        L0-L3
L5  emit/*, commands/*             L0-L4  (commands reach L4 via api ONLY)
L6  assemble/*, udf.py             L0-L5
L7  converter.py                   L0-L6
L8  pipeline.py                    L0-L7
```

🛑 **This includes function-local imports.** A deferred
`from script_convert.emit import expr` inside a method is still an import, and
that pattern is exactly how the current layering violation survives (local-imports counts
them; there are 60 today). If you need a lower layer to reach a higher one, the
design is wrong — pass a value in, do not import upward.

### File responsibilities

| File | Owns |
|---|---|
| `naming.py` | Pure name/type functions: `papyrus_script_name`, `_safe_property_name`, record-type maps. No package imports. |
| `constants.py` | **Data only.** `COMMAND_ROWS` and the TES4/Papyrus vocabulary tables. |
| `tes4/lexer.py` | TES4 source → tokens. Column fidelity is load-bearing (see §2). |
| `tes4/parser.py` | Tokens → AST. **Never raises**; degrades to `Raw`. |
| `tes4/nodes.py` | The AST dataclasses — the contract every emitter reads. |
| `tes5/blocks.py` | The single Papyrus-surface classifier (`classify`/`Kind`/`scan`). |
| `cross_ref.py` | The FormID/EditorID/script graph over exported records. |
| `symbols.py` | Node-based type inference. **Pure; the model to copy.** |
| `facts.py` | `ScriptFacts` derived from the **tree** — never from raw source. |
| `context.py` | `ScriptContext` (per-script state, explicit) + `PluginFacts` (per-run). |
| `resolve.py` | `Resolver`: name → meaning. Owns `xref` and the property registry. |
| `udf.py` | OBSE user-function signatures, built from trees during the run. |
| `emit/api.py` | `EmitCtx` — the **only** surface `emit/` and `commands/` may touch. |
| `emit/value.py` | `Value`: text + Papyrus type + is_comparison + notes. |
| `emit/expr.py` | Expression node → `Value`. TES4→TES5 semantics only. |
| `emit/stmt.py` | Statement node → `Value`. |
| `emit/script.py` | Body walk + layout: indentation, closers, comment re-attach. |
| `emit/commands.py` | The row engine and the one command dispatcher. |
| `commands/*.py` | ~35 handlers that cannot be rows, by domain. |
| `assemble/*.py` | `script`, `fragment`, `events`, `properties` — the .psc file. |
| `converter.py` | **The compatibility façade only** (§5). |
| `pipeline.py` | Jobs, workers, batching, VMAD packing, writing files. |

---

## 2. What the tree already answers — do not re-derive it

The parser owns these. Asking them again in text is the defect this whole
architecture exists to remove.

- **Nesting and balance.** An `If` node owns its body, its elseif chain and its
  else. Emitted blocks are closed by construction; there is nothing to repair.
- **Comment attachment.** A trailing `; ...` rides on `Stmt.comment`, so it can
  never be emitted mid-expression and comment out the rest of the line.
- **The author's parentheses.** `Expr.parenthesised` is recorded so output can
  echo them. Recorded, never interpreted — the tree already encodes precedence.
- **Receiver vs argument.** `Call.leading_comma` says the argument list opened
  with a comma, which is the only thing distinguishing `StopCombat, Player`
  (Player's combat) from an argument. Dropping it acted on the wrong actor.
- **Argument adjacency.** `SetFactionRank X -1` (a negative argument) versus
  `GetPos Y - 10000` (subtraction) is decided by **token column adjacency**.
  This is why lexer column fidelity is load-bearing and why source text must not
  be reflowed before parsing.

### The typed emission contract

An emitter returns a **`Value`**, never a bare `str`:

```python
@dataclass(frozen=True)
class Value:
    text: str                    # Papyrus source
    ptype: str = ''              # Bool | Int | Float | ObjectReference | ...
    is_comparison: bool = False  # a top-level ==/!=/</>/&&/||
    notes: tuple[str, ...] = ()  # ;NE: / ;TODO: markers
```

This exists because a `Call` node's emission can **become** a comparison —
`GetInCell X` is one node that emits `GetParentCell() == X` — and nothing on the
node records that. Without `Value`, the emitter has to re-read its own output to
find out what it just produced.

`is_comparison` is derived from the row template **at import** (443 templates,
once), never from output, so it cannot drift.

🛑 **`notes` is the ONLY channel for `;NE:` / `;TODO:` markers.** Never a mutable
list on the converter that a caller clears and reads back.

---

## 3. The decision procedure

Run this before writing code. It terminates at exactly one file.

```
0. Is the change a repair to EMITTED PAPYRUS TEXT?
     -> FORBIDDEN. Ask instead: what NODE produced that text? Fix it there.
        Missing information -> add a Value field or a ScriptFacts field.
        NEVER a pass over lines.

1. A TES4 COMMAND?
   1a. Fits ONE Papyrus template over the placeholders
       {ref} {aN} {sN} {bN} {pN} {iN} {cN} {fN} {fmt} {?n}?
         -> constants.COMMAND_ROWS.  Add a ROW.  WRITE NO CODE.
   1b. Needs real logic (branches on argument VALUES, emits several lines,
       consults PluginFacts, synthesises a helper)?
         -> commands/<domain>.py, one @command handler, (ctx, call) -> Value.
            Domains: quest dialogue actor faction world ui av anim obse misc.
            If none fits it is misc.  NEVER an 11th module for one command.
   1c. No Papyrus equivalent?  -> a row with note=.  Still 1a.

2. A new STATEMENT kind the parser must recognise?
     -> tes4/nodes.py + tes4/parser.py + emit/stmt.py.
        EXACTLY three files.  A fourth means you got it wrong.

3. An EXPRESSION rule (operators, comparisons, casts, null-tests)?
     -> emit/expr.py.  A comparison rewrite is a row in _CMP_RULES,
        never a new `if` in _binop.

4. "What does this BARE NAME mean?" -- local vs form vs command vs global
   vs actor value vs cross-script member?
     -> resolve.py.  Nowhere else may ask.

5. A RECORD or TYPE lookup?
     -> cross_ref.py   a graph query over exported data
        naming.py      a pure string/type mapping
        resolve.py     needs the current script's context to decide
        NEVER constants.py.

6. A new PER-SCRIPT fact ("this script uses X, so emit Y")?
     -> facts.py, derived from the PARSE TREE.
        NEVER a regex over `source`.  If the tree cannot answer it, the parser
        is missing a node -> go to 2.

7. A new PER-RUN fact (something the pipeline scans once)?
     -> pipeline builds it, context.PluginFacts carries it, emit reads
        ctx.plugin.  NEVER a mutable class attribute.

8. A CONSTANT -- a set of names, a map, a threshold, a template?
     -> constants.py  TES4/Papyrus vocabulary
        naming.py     a naming rule
        A module-private tuning value used by exactly ONE function may sit
        beside it.  Used twice = a constants.py entry.
        NEVER rebuilt inside a function body.
        EXCEPTION, and the layer table outranks this step: `tes4/*` is
        stdlib-only, so a table it shares with a HIGHER layer goes in the
        LOWEST module both legally reach -- never in constants.py.
        `lexer.PRECEDENCE` is the worked case (bugs ledger 18).

9. File layout, event headers, property declarations, the poll loop?
     -> assemble/{script,fragment,events,properties}.py

10. Jobs, workers, batching, VMAD, writing files?
     -> pipeline.py

11. Changes ScriptConverter's public shape, _property_refs type strings,
    emitted property/fragment/script NAMES, or a derive_formid key?
     -> STOP.  That is the compatibility boundary (§5).  Find another way.
```

### Self-check — where misplaced work belongs

| Symptom | Wrong home | Right home |
|---|---|---|
| "`GetInCell` emits a comparison, so `== 1` collapses" | reading emitted text | `Value.is_comparison` |
| "this script uses a timer" | regex over `source_low` | `facts.py` |
| "two `Say` calls duplicate" | a post-pass over lines | `commands/dialogue.py` — don't emit the second |
| "a Float went into an Int variable" | `_coerce_*` over lines | `Value.ptype` at the assignment |
| "a ref compared to 0" | `_fix_ref_zero` over lines | `emit/expr.py` `_binop` |
| "UDF arg types are wrong" | re-reading `.psc` files | `udf.py` |
| "a condition got commented out" | a repair pass | impossible — the comment rides on the node |

---

## 4. Invariants

Measured by `tools/script/arch_fitness.py`; the metric id is in brackets.

1. **Imports point strictly downward**, at any nesting depth. [local-imports]
2. **No function in `emit/`, `commands/`, `assemble/` or `converter.py` takes
   emitted Papyrus.** No parameter named `line`/`lines`/`text`/`emitted`/`psc`;
   no `_fix_*`, `_repair_*`, `_coerce_*`, `_postprocess*`. [text-repair-fns]
3. **One parse.** Exactly one non-test call site of `parser.parse`; zero
   references to `emit_source`, `_convert_expression`, `_tree_expression`,
   `USE_TREE_EXPRESSIONS`. [reparse-round-trip]
4. **`constants.py` is data**: no parameter named `xref`/`conv`/`ctx`, no
   `open(`, at most a handful of pure `def`s. [logic-in-constants]
5. **No module-level I/O at import.** [logic-in-constants]
6. **No mutable class-level `dict`/`list`/`set`.** [mutable-class-state]
7. **Zero private reach-in**: `conv._` / `ctx._` absent from `emit/`,
   `commands/`, `assemble/`. [private-reach-ins]
8. **One out-channel**: `_line_comments` appears nowhere; emitters return
   `Value`. [text-repair-fns]
9. **Per-script state is declared**: `ScriptContext` is a dataclass and there is
   **no `_reset`** — a new script gets a new instance. [mutable-class-state]
10. **No source re-scanning after parse** in `assemble/`, `emit/`, `commands/`,
    `converter.py`. [source-rescans]
11. **Command knowledge in one table**: `set(COMMAND_ROWS) & set(registry)` is
    empty; their union defines "known command". [satellite-cmd-sets]
12. **No satellite per-command flag sets** — flags are fields on the row. [satellite-cmd-sets]
13. **No `.py` over 1,200 code lines.** [oversized-files]
14. **The compatibility surface is frozen** (§5).
15. **Comments compress, knowledge does not.** Prose that must survive moves
    to a `docs/` file cited by `See:`. [`stray-comments`, `inline-comments`,
    `dead-citations`, gated per file by `tools/validate/code_rules.py`]

---

## 5. The compatibility boundary — do NOT change

Measured from every importer, tool and test that touches this package.

- `ScriptConverter(xref)` with `convert_standalone(name, source, extends,
  editor_id)` and `convert_fragment(source, extends) -> list[str]`, which
  **must preserve `_property_refs` across calls**; `get_property_refs()`;
  `get_cell_family_helpers()`; `set_scro_aliases()`; `set_music_cues()`.
- **`._property_refs` is a LIVE attribute.** `tes5_import/dialogue/converter.py`
  reads the private directly (`:222`, `:1352`) and `pipeline._add_scro_ref`
  both reads and writes it (`:1652`, `:1661`). Implement it as a property over
  `Resolver.property_refs`.
- Class attributes `say_durations`, `say_topics`, `topic_unlock_globals`,
  `message_menus`, `chargen_menus`; module constant `SAY_START_WAIT`;
  `sctx_onactivate_consumes`.
- **The `_property_refs` type-string vocabulary, verbatim.** Consumers compare
  literals (`== 'ActorBase'`, `.startswith('TES4_')`) to pick base-vs-reference
  FormIDs.
- Emitted shapes: `Type Property Name Auto` one per line (parsed back by
  `tools/validate/vmad_property_typecheck.py`); **both** `Fragment_0` and
  `Fragment_1` per INFO; `Fragment_Stage_NNNN_Item_M`; `TES4Polyfill.<Fn>`
  matching the hand-written polyfill; `papyrus_script_name(edid)` as ScriptName
  **and** filename **and** VMAD name.
- `pipeline.py`'s surface used by `tes5_import/` and
  `tools/script/convert_scripts_subset.py` (which also reaches six private
  names).

🛑 **FormID drift is a hard stop.** `derive_formid(site, key)` keys on authored
data. Changing which properties a command registers can move ids, and moving one
id breaks every existing save. Measure before shipping; if even one moves, stop
and ask.

---

## 6. Verification

**The semantic diff is the test of correctness. Not pytest.**

```bash
# every edit -- 0.6s, no build
python tools/script/arch_fitness.py --fail-on-regression

# at every stage boundary -- one build at a time
python convert.py -f Oblivion.esm     --scripts-only
python convert.py -f Nehrim.esm       --scripts-only     # standalone, OBSE-heavy
python convert.py -f Knights.esp      --scripts-only
python convert.py -f Morrowind_ob.esm --scripts-only     # masters, largest corpus

python tools/script/psc_semantic_diff.py compare \
    -f Oblivion.esm -f Nehrim.esm -f Morrowind_ob.esm -f Knights.esp --show 0
python tools/validate/vmad_property_typecheck.py
```

`psc_semantic_diff` compares what each script **does** — properties, locals,
events, calls-with-arity, literals, writes, `extends` — and ignores spacing,
temp names, declaration order and parenthesisation. That is what makes it usable
as the safety net for a rewrite: text is expected to change, behaviour is not.

### Why `long-functions` is 60, not 80

Measured across 7,320 first-party functions: **p50 = 12 lines, p75 = 25,
p90 = 49, p95 = 75.** A 60-line cap fires on 534 (7.3%); 80 fires on 320
(4.4%) and 40 on 978 (13.4%).

The deciding argument is not the percentile but the OVERLAP with
`god-functions`. Median complexity by length runs **10 at 40-59, 14.5 at
60-79, 19.5 at 80-99 and 25 at 100-119** — so a complexity-25 rule does not
fire until roughly 100 lines. At an 80-line cap the two rules nearly coincide
and the length rule adds little; at 60 it catches the shape complexity cannot
see, a **long straight-line function** (70 lines of sequential assignment at
complexity 3 — what `measure` was before it was split into phases).

### Why `oversized-files` does not judge `tests/`

Measured across 465 first-party files: **median = 150 code lines**, and only
26 (5.6%) exceed 1000 — so the cap is not fighting the tree. Six of those 26
are test files, and they are the extreme tail: `test_import.py` is 4,433 lines
over **459 functions — 9.7 lines per test**.

The cap exists because ~1000 lines is 25-50 functions at the 20-45 line/function
density of the source violators, which is where a module stops having one
describable responsibility. A test file has no such responsibility to split
along: its length is a COUNT of independent cases, each read by grepping one
test name, never reasoned about whole. Applying a cognitive-load rule to a flat
list of 10-line functions charges a cost that is not being paid.

Excluding `tests/` drops the violator count 26 → 20 and leaves the pressure on
the files where splitting genuinely helps.

Every other rule still judges `tests/` — only `oversized-files` is lifted.

### Why the file cap is 1200, not 1000

A census of 633 non-test files on 2026-09-23 found the cap was shaping the tree
rather than describing it: **7 files sat at 900-999 code lines and none at
1000-1099**, a gap that only forced splits produce. Only 4 files were over 1000
(`quest_labtest.py` 1,319, `converter.py` 1,169, `dialog_emulator.py` 1,168,
`tes5_esm_reader.py` 1,114). At 1200 three of those pass and the 14 files at
800-999 get room to grow without splitting coherent modules like `convert.py`
(989) and `collision.py` (975). The cap still counts code lines only, so 1200 is
a real limit.

### A tiny function needs no docstring

`missing-docstrings` skips a function of at most `TINY_BODY_LINES` (2)
statements. For those, the name and the one or two lines below it are the whole
contract, and the required docstring came out as boilerplate like "The command
line.". A tiny function that has a docstring is still held to
`TINY_DOC_CHARS`.

### Splitting a legacy file: sparingly

`oversized-files` is charged only when a file GROWS or CROSSES 1200 (the site
detail keys `_worsened` by name, so a shrink or a same-size edit is absolved).
That is deliberate: a file already far over is meant to be chipped at, not
rewritten to clear the gate.

**Do not split an oversized legacy file as a side effect of an unrelated fix.**
A wholesale reorganization buries the one-line behavioral change inside a
thousand lines of movement, so the diff can no longer be reviewed and a
regression cannot be bisected to it. Land the fix; leave the file long.

Split when the split IS the task, or when the responsibility you are adding
genuinely belongs in its own module. Otherwise the correct response to a
1,200-line file you had to touch is to leave it no longer than you found it.

### Length is counted in statements

`long-functions` scores **statements, not physical lines**. A line count is
gameable by reflow: joining two lines with a `\`, or folding a temp into the
`return`, drops the number without removing any work — and the rule then
rewards the less readable edit. One transcript shows a single condition
rewritten five times (continuation backslash, re-escape, `(motion or {})`,
revert, folded return) purely to move a line count.

Statements are immune: `ast.walk` counts `ast.stmt` nodes, so every reflow of
the same code yields the same number and the only way down is to delete work.

**Threshold 35.** Measured over 7,791 first-party functions — p50=7, p75=15,
p90=30, p95=45. `>35 statements` fires on 599 (7.7%), matching the 562 (7.7%)
the old 60-physical-line cap fired on, so the pressure is unchanged while the
metric stops being gameable. 60 statements would have dropped it to 225 (2.9%).

This also stops charging data tables as complex code: `_init_dispatch` in
`tes5_import/base/constants.py` is 185 physical lines but **12 statements**.

`oversized-files` still counts `code_lines()`: line and statement counts
already agree there (20 files vs 22), so it needs no re-baseline.

### A literal element may carry its decoding

A trailing comment on one element of a `dict`/`set`/`list`/`tuple` literal is
exempt from `inline-comments` and `stray-comments`:

```python
_SUPPORTED_VERSIONS = {
    0x14000004,  # Gamebryo v20.0.0.4 — primary Oblivion format
    0x0a01006a,  # Gamebryo v10.1.0.106
}
```

That comment is not prose, it is the **decoding of an opaque literal** — the
only place `0x14000004` can be tied to a version name, since the value has no
name of its own. A docstring cannot hold it: listing the seven constants in
parallel prose puts the label somewhere it can silently drift when an eighth is
added, which is strictly worse than the comment beside the value.

The exemption is structural, not textual, so it cannot be used to smuggle
narration into a set literal:

- The comment must be **trailing** — a comment on its own line inside the
  literal is a heading in disguise, and is still charged.
- The element must be **one line**. A multi-line element's comment has already
  drifted from what it labels.
- It is capped at `MAX_SEE_CHARS` (80). A label is a label; past that it is an
  argument, and belongs in the docstring.

Assignments are unaffected — `x = compute()  # this is slow because…` is prose
and stays a violation.

### A citation must name an anchor

A citation naming only a `.md` path, with no `#anchor`, is a violation in a
function or class docstring. `dead-citations` already checks that an anchor
*resolves*; `anchorless-citations` requires that one is *present*.

A bare path points at a whole file. `tes5_import_weather.md` is 2,346 lines, so
a citation to it names no fact and the reader is no better off than with no
citation at all. This rule exists because a bulk docstring trim once replaced 25
contracts with a one-line summary plus a bare path, and the loss was invisible
at review time.

Module docstrings are exempt. A module-wide `See:` genuinely means "this whole
document", and forcing an anchor there would only invent a fake one.

**Measured debt: 1 site.** Anchored citations already outnumber bare ones 166
to 1 in function and class docstrings, so this lands as an error with no
migration.

Not adopted: a **minimum docstring length** before a citation is allowed. It
does not separate the cases. The broken docstring from that incident was ~62
chars and its accepted repair was 68 — the fix is *shorter* than the defect,
because `0x7F = 0, max 254` replaced a sentence of narration. Measured over the
173 citing docstrings, a floor at 80 charges 74 of them (43%) and 80/90/100 all
charge the identical 74, because the wall in the distribution is just the width
of one line of English. Every such floor charges good terse writing, and its
cheapest remedy is padding — the same gameable-metric failure that
`long-functions` escaped by counting statements.

### A private name belongs to its file

`_helper()` may only be used in the file that defines it. Importing an
underscore-prefixed name from another module is `private-imports`.

The leading underscore is the author's statement that a name is not an
interface: it can be renamed, resplit or deleted without looking outside the
file. An importer voids that guarantee silently — the definer has no way to see
the dependency, so the name is now load-bearing while still being spelled as
though it is not. Either it is an interface (drop the underscore) or it is not
(do not import it).

**Measured: 537 import statements, 449 distinct `(file, module, name)`, 350
symbols leaked across 136 files.** 301 of the 537 sit inside a function body.
The concentration says what it is: `corridor_union.py` takes 41 private names
from `union_geom`/`union_cdt`, which is a file split that moved code without
promoting an interface, and `script_convert.constants` leaks 53, which is a
module that has simply mislabelled its whole public surface.

`tests/` is exempt, for the same reason it is exempt from `oversized-files`: a
test is allowed to know more than a caller, and testing a helper directly is
legitimate. That exemption alone removes the largest importers
(`test_import.py` at 39, `test_asset_convert.py` at 19).

Being `--gate-diff` scoped, none of this debt is payable until someone touches
the line.

### A rename is not an edit

Writing a **new** file gets `touched = None`, which `_blame` reads as "all of
it is yours" — right for original code, wrong for a **rename**, where the
author moved a file and wrote nothing. Moving the same content into an
*existing* file is free, because that file has a git baseline, so the cost
depended on how the move was performed.

A rename is recognised by an **exact whole-file match against the donor as it
now stands in the working tree** — not against its baseline. A file that was
edited and *then* moved carries those edits, so matching the committed copy
would miss the rename and bill the mover for the donor's in-flight work. On a
hit the pair is recorded in `.claude/renames.json`, blame is diffed against the
donor's current content (normally zero lines), and scoring uses the donor's
baseline, so its existing violations stay its own and only what the move adds
is charged.

Two traps, both found by running it:

- **The candidate must not be its own donor.** `gate_pending()` stages the
  candidate as `.gate_candidate_<pid>.py` *beside* the target so git and the
  rules see the same package — and `repo_files()` walks that directory, so
  every new file matched its own temp copy perfectly and was absolved. `_donor`
  takes the staged path and excludes it.
- **Compare decoded text, not bytes.** Sources here are CRLF; `read_text()`
  collapses line endings, so re-encoding a candidate yields ~2.5% fewer bytes
  than the file holds and a byte comparison never matches on Windows. The size
  filter that prunes candidates therefore accepts a window (`size` to `2*size`)
  rather than requiring equality.

Exact equality is the whole safety argument. There is no threshold to tune: a
single added line breaks the match, and the file is then blamed whole exactly
as before. **Do not replace it with a similarity score.** Two such attempts
were built and reverted here; both let the judged file choose its own baseline,
so the more of the original an author kept, the more new debt they could add
under it. A 60% overlap rule lets `foo_v2.py` inherit `foo.py` and absolve the
violation that `foo.py` had just been blocked for.

The map is untracked and machine-local: it only bridges a local write and a
local stage, after which git's own rename detection takes over. It is never
pruned — that exact rename cannot happen twice.

A **split** is not covered. A part of a file never equals the whole, so it gets
no record and is blamed whole. That is the known cost of dividing an oversized
file, and it is paid once, visibly.

### What the gate must see

`.claude/hooks/doc_rules_gate.py` blocks a write that would leave a violation on
the code it owns. Every hole below was reached by running the hook against a
probe, not by reading it, and each one let a real violation onto disk:

| Hole | Why it passed |
|---|---|
| A Write creating a NEW `.py` | `judged()` required `os.path.isfile()`, false before the file exists |
| `foo.PY` on Windows | `endswith('.py')` is case-sensitive; NTFS is not, so the write lands on `foo.py` |
| Syntactically invalid Python | `_parse()` returned an empty module on `SyntaxError`, so the file scored zero violations |
| A report containing `ModuleNotFoundError` | a *substring* test on stderr turned exit 2 into a pass |
| Non-cp1252 bytes in git output | bare `text=True` decoded with the locale codec; the crash fell into an `except` that returned 0 |
| `MultiEdit` / `NotebookEdit` | absent from the matcher, or unhandled by `pending_text()` |

Two rules follow from these. **A gate that cannot run must fail closed** — every
`except` that returned 0 was a silent pass, which is indistinguishable from a
clean file. And **the gate must never be the write mechanism**: `gate_pending()`
used to write the candidate over the real file and restore it in `finally`, so
being killed mid-write (the harness caps the hook at 120s) left unvalidated,
possibly truncated code on disk. It scores a temp copy instead.

**An import and its use are judged at the end of the turn, not per edit.**
F401 (unused import), F811 (redefinition) and F821 (undefined name) are about
two sites that can sit hundreds of lines apart. The agent's `Edit` changes one
contiguous span, so adding the import first charges F401 to that edit, and
adding the call first charges F821 to it. Both orders were refused, and the only
edit that passed covered the whole span between them, so the agent avoided the
change altogether. Now the Pre and Post hooks pass `--defer-paired`, and the
Post hook records each `.py` it saw in a per-session list under the system temp
dir. A `Stop` hook re-gates those files with every rule and exits 2 if a pair is
still open. The rule is as strict as before, since no half-pair survives a
turn, but the two halves may land in separate edits. The Stop hook ignores
`stop_hook_active`, because the fix is always one edit away. `safe_run.py`
still gates shell writes in full, since they have no turn-end check.

`dead-citations` is deferred the same way. A docstring's `See: docs/...#anchor`
and the doc section it names are a pair in two files, and gating per edit forced
the doc to be written before the code that cites it.

Bash cannot be gated before it runs, because a shell command's effect is not
predictable. `tools/validate/safe_run.py` is the one allowed entry point: it
hashes tracked `.py` files, runs the command with the streams inherited,
re-hashes, and gates whatever changed.

#### The wrapper never re-quotes

`safe_run.py <program> args...` runs `sys.argv[1:]` directly, with no inner
shell. The caller's shell has already done the shell's work (quoting, `$`,
pipes, redirects, heredocs), so its argv is exactly what was meant.

The wrapper used to read the raw Windows command line (`GetCommandLineW`) and
pass it to `bash -c`. That line is not the text the caller typed: Git Bash
rebuilds it from argv, adding double quotes only around arguments that contain
a space. The inner shell then re-read what the outer one had already unquoted:

| Argument | Arrived as |
|---|---|
| `'(a\|b)'` | bash syntax error near `(`, exit 2 |
| `'$HOME x'` | `C:/Users/<user> x`, silently expanded |

A pipe, chain or builtin that must run inside the gated region goes through
`safe_run.py -c '<command>'`. That one string reaches the caller's shell
unchanged, and its author owns the quoting, as with `bash -c`. A program that
is not on PATH (`cd`, `for`) exits 127 with a hint to use `-c`.

#### The command runs in the caller's directory

The wrapper does not change directory. It used to run every command in the
repo root, which silently undid a leading `cd`: `cd <scratchpad> && python
<repo>/tools/validate/safe_run.py -c '...'` unpacked a download into the repo
instead of the scratchpad. Gating does not need the repo root, because it hashes
files by absolute path. A command that `cd`s away and still uses repo-relative
paths now fails with "file not found" instead of writing into the repo.

#### Every command runs inside the wrapper

The hook used to pass any command whose text contained the wrapper's path, so in
`safe_run.py true && python - <<'PY' ...` everything after `&&` ran unwrapped
and its writes were never gated. `.claude/hooks/shell_route.py` now splits the
line on `&&`, `||`, `;`, `|`, `&` and newlines. Every piece must be a wrapper
call or a `READ_ONLY` helper (`cd`, `echo`, `tail`, `grep`, `Select-Object`
and a few more) that cannot write code. It also refuses:

- `$(...)` and backticks outside single quotes, which run before the wrapper
  starts, so their writes land before its first hash;
- subshells and loops (`(`, `for`, `while`), which are not helpers;
- a helper redirecting into a `.py` (`echo x > a.py`).

Heredoc bodies are skipped as data. A redirect on the wrapper call itself is
fine, since the shell truncates the file before the wrapper's first hash and the
change is gated. Anything else goes inside `safe_run.py -c '...'`.

**The routing rule lives in the HOOK, not in `permissions`.** Rules evaluate
deny, then ask, then allow, and the first match wins — specificity never
reorders them. So a `deny` cannot carry a whitelist exception: `deny: ["Bash"]`
with `allow: ["Bash(python tools/validate/safe_run.py:*)"]` blocks the wrapper
too, and so does `Bash(*)` or even `Bash(python *)`. A hook returning
`permissionDecision: "allow"` does not lift a deny either. What does work is a
hook **exit 2**, which is evaluated BEFORE the permission rules — so the hook
blocks any command that does not name the wrapper, and no deny rule is needed.

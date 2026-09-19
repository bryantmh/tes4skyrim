# Morrowind runtime

**Code:** `tes_runtime/morrowind_runtime/`, `external/openmw/`, `tools/generators/vendor_openmw.py`

A **submodule of TESRuntime**, beside `havok_world_size`: its own source folder
and its own DLL, shipping in the same `TESRuntime.zip`.

Morrowind's dialogue, topics and journal run natively in Skyrim by porting
OpenMW's own dialogue engine and MWScript VM into an SKSE plugin, rather than
reshaping TES3 dialogue into TES5 DIAL/INFO records.

## <a id="why-not-record-conversion"></a>Why not convert the records

Morrowind's model is a topic list with keyword discovery, resolved by an ordered
first-match scan over 85 condition functions, with results in an uncompiled
script language and a journal that is an integer index per quest. Skyrim's is a
branch/view tree of authored, quest-owned lines.

The Oblivion→Skyrim path already pays heavily for a much smaller mismatch:
`tes5_import/dialogue/` is ~200 KB across 8 modules, and `conversations.py`
exists solely to re-implement a scheduler Skyrim lacks.

Measured against real `Morrowind.esm`, the records this would have to reshape:
**23,693 INFO and 2,358 DIAL**. None of them are exported today —
`tes4_export/export_morrowind.py` handles 20 signatures and `DIAL`/`INFO` are
not among them, so `export_record()` silently returns `[]` for all of them.

## <a id="why-portable"></a>The seam that makes this portable

`Interpreter::Context` (`components/interpreter/context.hpp`) is a pure abstract
interface whose only dependency is `ESM::RefId`. The VM, the compiler and
`KeywordSearch` have no engine coupling at all, so the work splits cleanly:

| Vendored unmodified | Rewritten against Skyrim |
|---|---|
| `components/interpreter/` — the VM | `mwscript/*extensions.cpp` — 458 opcode installs |
| `components/compiler/` — 315 registrations | `mwscript/interpretercontext.cpp` |
| `components/esm3/` — TES3 records | `mwdialogue/`'s engine calls |
| `mwdialogue/keywordsearch` — a trie | |

## <a id="vendoring"></a>Vendoring

`tools/generators/vendor_openmw.py` copies the subset the runtime needs from the
read-only checkout under `references/` into `external/openmw/`. The pipeline must
never build against `references/`, which is comparison-only.

The closure is **resolved, not listed**: the tool follows
`#include <components/...>` transitively from the interpreter and compiler
directories, pulling each header's companion implementation file. A hand-written
file list goes stale silently and fails at link time; a resolved closure fails
loudly and immediately when an upstream include changes.

`apps/openmw/mwdialogue/` is copied wholesale instead of followed. Measured:
adding it as a closure root resolves **1,604 files** — `mwworld`, `mwmechanics`,
`mwrender`, `mwlua`, all 168 of `mwgui`, plus MyGUI, OSG, Bullet and SQLite
support. Those reaches are exactly what the adapters replace, so following them
would vendor the engine we are replacing.

The TES3 records it reads are therefore named explicitly in `ESM3_RECORDS` and
seeded as their own bounded closure: records reference only other records, so
that closure terminates. **Naming them is deliberate** — it is the one hand-kept
list in the tool, and the compile fails loudly if it is short.

Deliberately **not** vendored: `mwgui/` (168 MyGUI files — a new SWF replaces
it), `mwworld/`, `mwmechanics/`, `mwrender/`, and `userextensions.cpp`
(console-only, 4 commands).

### <a id="closure-follows-quoted-includes"></a>The closure must follow QUOTED includes, both ways

Angle-bracket `<components/...>` includes alone resolve **95 files, and the
build fails**. Two kinds of quoted include are load-bearing:

| Form | Example | Resolves against |
|---|---|---|
| sibling | `refid.hpp` → `"esm3exteriorcellrefid.hpp"` | the including file's directory |
| root | `stringrefid.cpp` → `"components/misc/guarded.hpp"` | the source root |

A resolver that tries only one of the two silently drops files. Following both
takes the interpreter/compiler closure 95 → 111 → **113**, which is what
compiles; `components/esm` grows from 2 files to 16 this way, entirely through
`refid.hpp`'s siblings. With the TES3 records seeded alongside it, the vendored
tree is **193 files**.

`components/esm4` and `components/misc/strings` land in the closure but are
**header-only**; naming them in a `cl *.cpp` line fails the build with "cannot
open source file".

### <a id="phase-0-gate"></a>Phase 0 gate: it compiles standalone

Verified — `tes_runtime\morrowind_runtime\build.bat openmw` compiles **34 translation
units** (the whole MWScript compiler and interpreter, plus the `RefId` and
logging support they pull in) to 7.9 MB of objects with:

```
/std:c++20 /permissive- /Zc:__cplusplus /EHsc /O2 /MD /W3
```

C++20 is required — upstream sets `CMAKE_CXX_STANDARD 20` and
`Interpreter::installSegment` takes an `auto&` parameter. **No OSG, MyGUI,
Bullet or SDL**, and the closure contains no third-party include at all, which
is what makes the VM portable in the first place.

## <a id="store"></a>The store, and how it is tested

`plugin/store.{h,cpp}` reads the sidecars into topics and responses. It is pure
text parsing against no game memory, so `build.bat test` builds
`store_test.exe`, which runs the same loader headless:

```bash
tes_runtime\morrowind_runtime\build.bat test
tes_runtime\morrowind_runtime\store_test.exe "export\Tamriel Rebuilt 25.08.12"   # one export dir
tes_runtime\morrowind_runtime\store_test.exe --sidecar <staged root>             # the deployed layout
```

Measured, with every count agreeing with an independent Python read of the
source ESM:

| | TR_Mainland | Morrowind.esm |
|---|---:|---:|
| topics | 4,405 | 2,358 |
| responses | 69,270 | 23,693 |
| conditions | 95,386 | 24,835 |
| result scripts | 26,820 | 12,799 |
| float condition values | 65 | 16 |
| `Choice` conditions | 11,244 | 1,748 |
| multi-line responses | 182 | 48 |

The `--sidecar` run stages both plugins side by side and loads **2 plugins,
6,763 topics, 92,963 responses** — the exact sum, which is what proves one
plugin's dialogue is not being attributed to another's.

### <a id="choice-zero"></a>Why the corpus gate counts `Choice`

The first export decoded every SCVR rule with one slicing. That is wrong for
rule kind `1`, whose two-digit function index sits in the bytes a variable rule
uses for its type — so `01500` (function 50, `Choice`, compared `=`) parsed as
`VarType='5'` with an empty variable. No error, no missing field, every other
count still correct, and **every branching conversation silently unreadable**.

The gate caught it by reporting **0 `Choice` conditions where 1,748 were
measured**, which is why that count is asserted rather than printed.

## <a id="the-seam"></a>The seam: `ActorView`

`MWDialogue::Filter` reaches the world through `MWWorld::Ptr` and
`MWBase::Environment`. `plugin/actor.h` replaces both with one abstract
interface, which is what lets the ported rules compile without `mwworld/` or
`mwmechanics/` — following those resolves the whole engine.

Two implementations: one reads the running game, and the test harnesses answer
from literal values, so every rule is checkable with no Skyrim running.

### <a id="ported-is-not-wired"></a>Ported is not wired

An opcode can be fully ported and still be dead: its hook is never set, or the
`DialogueState` it reads is never fed. Either way it answers with a default
forever while every audit that counts *ported* opcodes reads it as done, so the
gap is invisible from the port side alone.

`tools/script/mwscript_opcode_audit.py unwired` reports both halves — hooks
used but never supplied, and `DialogueState` methods nothing calls.

## <a id="the-filter"></a>The filter

`plugin/filter.{h,cpp}` ports the TES3 selection rules onto `ActorView`. The
rules are not reinterpreted — the answer has to match what OpenMW would choose
for the same state. Semantics worth stating because they are easy to get
backwards:

| Rule | Behaviour |
|---|---|
| order | **First match wins**, never best match. `Ordinal` is precedence. |
| creature | Answers only topics naming it directly; a generic topic is rejected. |
| gender | `mGender` is `0` male / `1` female and the test is for the **opposite**. |
| cell | Matches as a **prefix**: `Balmora` catches `Balmora, Guild of Fighters`. |
| rank, no faction | Uses the **speaker's own** faction. |
| disposition | Gates a topic response, never a journal entry. |
| `Choice` outside a choice | **Every** choice condition fails, which is what hides a branch's answers until it is entered. |
| missing global | **Ignored** — the filter passes. |
| missing local | **Rejects** — the script cannot answer. |
| unimplemented function | **Passes.** A function we have not ported must not mute a line. |

Rejections are attributed (`Reject::Actor`, `::Cell`, `::Condition` + index)
because a wrong filter shows the **wrong line** rather than failing, and 69,270
responses cannot be diffed by reading them.

Sweeping TR_Mainland's whole corpus through it, with a permissive actor:

```
responses 69270, passing 9606
  actor 48889   cell 4038   class 3046   faction 2711
  condition 547 race 282    pcFaction 128  disposition 13  gender 10
```

The 9,606 that pass are exactly the journal entries, which carry no actor
filter — the same count the source probe measured.

## <a id="the-session"></a>The session, and what it already does

`plugin/session.{h,cpp}` turns the filter into what a menu renders: the
greeting, the offered topic list (alphabetical, as vanilla reads), one topic's
answer, and Morrowind's **keyword discovery** — topics named in a reply's text,
whole-word, longest first, restricted to topics the actor can actually answer.

`session_test.exe` runs all of it headless against a real export:

```bash
tes_runtime\morrowind_runtime\session_test.exe <sidecar root> TR_m4_Shei
tes_runtime\morrowind_runtime\session_test.exe <sidecar root> TR_m7_Felms --faction Temple --rank 3
```

Measured on TR_Mainland: `TR_m4_Shei` offers 15 topics, all Thieves Guild
material; `TR_m7_Felms` offers 9, all Arena material, and his greeting
("Did you put on a good show fighting beasts, gladiator?") surfaces the
`fighting beasts` topic through keyword discovery. Different actors get
different, contextually correct lists out of the same 69,270 responses, which
is the evidence that the filters discriminate rather than merely run.

## <a id="the-swf-gate"></a>The SWF gate

**Code:** `tools/generators/gen_morrowind_menu_swf.py`

`docs/commentary/asset_convert_ui.md` records that across rounds 2–4 of the
message-box work, **every character that conversion ADDS failed to render**, and
the cause was never identified — byte-exact tag lengths, definition order,
placement flags, matrices and depth order were each ruled out or fixed without
fixing it. The AVM1 assembler written for that path was deleted, and the doc
says plainly that whether added characters render "is a question the file cannot
answer".

That failure was **splicing new tags into a vanilla movie the engine had already
parsed**. This is a different operation: a standalone `.swf` file loaded by
`GFxLoader::LoadMovie` through SKSE's `CustomMenu`, which the engine parses from
scratch exactly as it parses its own. Skyrim's own movies are untouched, which
is also what keeps vanilla dialogue working.

It should therefore work — but "should" is not "does", and everything else in
Phase 3 sits on top of it. So `--hello` writes the smallest thing that can
answer the question: one bordered panel, one dynamic text field, no
ActionScript, no dialogue logic.

```bash
python tools/generators/gen_morrowind_menu_swf.py --hello
```

### ✅ CONFIRMED IN GAME: a standalone authored SWF DRAWS

The probe renders: panel, border and text, opened with `showmenu
MorrowindDialogueMenu`. **The `asset_convert/ui` failure does not generalize** —
it was specific to adding characters to a movie the engine had already parsed,
and says nothing about a movie parsed from scratch. A movie this project
authors end to end is drawn like any of Bethesda's own.

That settles the single biggest unknown in the plan, and it took three fixes to
get there, none of them about the SWF container: the menu never called
`Render` (slot 6), `flags` went to the wrong offset, and the text field carried
mis-ordered flags and no font. Each is written up below, because every one of
them fails SILENTLY — `LoadMovie` succeeds, the menu takes focus, the game
pauses, and nothing appears.

The probe's text renders through `$EverywhereMediumFont` imported from the
shared `gfxfontlib.swf`. The real menu embeds its own face instead — see
[dynamic text](#dynamic-text).

## <a id="dynamic-text"></a>Dynamic text: a real embedded font

**Code:** `asset_convert/ui/swf.py` (`define_font2`),
`tools/generators/gen_morrowind_menu_swf.py`

The first real menu baked its text **into the window bitmap** — a fixed
greeting, a fixed topic list, a fixed name. That is not a menu, it is a
screenshot: the plugin had no way to say anything. Text has to come from
`DefineEditText` fields the plugin fills through
`GFxMovieView::SetVariable`, which is the same mechanism `tes_runtime`'s
`hud.cpp` already drives (`SetVariable` at vtable slot `0x10`).

A `DefineEditText` needs a FONT CHARACTER. That is what forced the font
question, because **Morrowind's own face is a bitmap atlas**, not outlines:
`century_gothic_font_regular.fnt` plus a 256×256 `.tex`. Three things follow
from that, all measured:

| Fact | Consequence |
|---|---|
| Glyphs are 11×13 px with **26% antialiased midtones** | a 1-bit contour trace loses what makes them legible |
| The face is **proportional** — 12 distinct advances over 94 glyphs, 3 px (`i`) to 14 px (`W`) | per-glyph placement needs a shipped metrics table and a layout engine |
| Rule 2 is **per shape**, and vanilla `hudmenu.swf` carries 207 bitmap-filled shapes | 94 glyph shapes IS legal, just expensive |

### 🛑 OpenMW's `MysticCards` is its DEFAULT UI font

`components/fontloader/fontloader.cpp` does
`loadFont(defaultFontId, "MysticCards")` as the fallback for `Fonts_Font_0`,
and `docs/source/reference/modding/font.rst` states it outright. The set:

| OpenMW TTF | Role | Vanilla `.fnt` |
|---|---|---|
| `MysticCards` | default UI face (`Fonts_Font_0`) | `magic_cards_regular` |
| `DemonicLetters` | Daedric, scrolls (`Fonts_Font_2`) | `daedric_font` |
| `DejaVuLGCSansMono` | console and debug, not configurable | — |

They carry unfamiliar names **because they are open-source reimplementations** —
OpenMW cannot ship Bethesda's font. MysticCards derives from **Pelagiad** (Isak
Larborn), a face built as a Morrowind UI font, under **SIL OFL 1.1**: embedding
and redistribution are explicitly permitted so long as the license travels with
it. Unlike the vanilla atlas, it can actually SHIP.

So the menu embeds MysticCards as a real `DefineFont2` and the text fields are
ordinary dynamic fields — Scaleform does wrapping, alignment and scaling, and
no glyph layout code exists on our side at all.

TrueType outlines are **quadratic**, and so are SWF's curve records, so
`qCurveTo` maps across with no approximation. The one axis flip is that a
font's Y runs up and SWF's runs down.

### `DefineFont2`, and the four ways it fails silently

`define_font2` writes the tag; these are the parts that are wrong if guessed:

| Part | Rule |
|---|---|
| **CodeTable order** | ASCENDING by character code. It is binary-searched, so an unsorted table draws the WRONG LETTERS rather than failing |
| **OffsetTable base** | offsets count from the START of the offset table, and that table is `4 * (count + 1)` bytes because `CodeTableOffset` follows the per-glyph entries |
| **Fill state per contour** | fill style 1 is restated after every `moveTo`, as vanilla's own glyph shapes do; a contour that never states a fill renders as nothing |
| **FontBoundsTable** | one EMPTY rect per glyph. Scaleform measures from the outlines, and vanilla's own font library ships them empty |

The tag is emitted **wide** (u32 offsets, u16 codes) unconditionally. The
narrow form saves a few hundred bytes in a file that is already mostly bitmap,
and is one more thing to get wrong.

### <a id="shape-record-flags"></a>🛑 StyleChangeRecord flags are consumed LOW BIT FIRST

The five flags of a StyleChangeRecord are one 5-bit field written high bit
first, but their VALUES run the other way:

| Flag | Bit |
|---|---|
| `StateMoveTo` | `0x01` |
| `StateFillStyle0` | `0x02` |
| `StateFillStyle1` | `0x04` |
| `StateLineStyle` | `0x08` |
| `StateNewStyles` | `0x10` |

Writing them as five sequential 1-bit calls reverses them. Measured on the
first embedded font: a record meant to be "move the pen" was read as
`StateNewStyles`, and "set fill 1" as `StateFillStyle0`, after which every
later record desynchronized — one glyph decoded a 28-bit move to
`(-104030208, 35435399)`.

**It parses.** The tag length is right, the offset table is consistent, and
nothing errors; the glyphs simply never draw. In game that is a window whose
chrome renders perfectly and whose text is invisible — indistinguishable from
`SetVariable` never having run.

This is the SAME failure as [DefineEditText's flags](#edit-text-flags), one
layer down: build the flag byte by OR-ing named constants and write it once,
never as a sequence of single bits.

🛑 **This is a lookalike, not vanilla's exact face.** Rendering the authored
atlas remains the higher-fidelity option and is a later pass; it is recorded
here so the substitution is a decision rather than drift.

## <a id="the-real-menu"></a>The real menu, from Morrowind's own art

**Code:** `asset_convert/ui/morrowind_menu_art.py`,
`tools/generators/gen_morrowind_menu_swf.py`

🛑 **No Bethesda pixels are committed.** The textures are read from the
registered Morrowind install at build time and composed into the generated
`.swf`, which is itself a build artifact. The repo carries the LAYOUT — which
texture goes where, at what size — and never the art.

Art is taken **as shipped**, from the archives, ignoring loose replacers, for
the same reason `find_archived_mesh` does: a user's texture pack must not change
what the converter builds, or two installs produce different menus from one
source. `find_archived_file` is the generalization of that helper to any stored
path, since the UI art lives under `textures\` rather than `meshes\`.

### Layout, from OpenMW's own

`openmw_dialogue_window.layout` is the authority, not a reconstruction. The
window is **588 × 433**:

| Widget | Position (x y w h) | Holds |
|---|---|---|
| History | `15 15 364 370` | the response text, with keyword links |
| VScroll | `370 13 14 371` | the response scrollbar |
| Disposition | `398 8 166 18` | the disposition bar |
| TopicsList | `398 31 166 328` | the topic list |
| ByeButton | `398 366 166 23` | Goodbye |

Persuasion is a separate `220 × 192` modal
(`openmw_persuasion_dialog.layout`): Admire, Intimidate, Taunt and three
bribes at 18 px pitch, a gold label, and Cancel.

### The art, and the colors

The frame is `menu_thick_border_*` — eight textures, **4 px** edges with 4 × 4
corners. `MW_Box`, the inset the panes sit in, is `menu_thin_border_*` at
**2 px**. Both decode from `Morrowind.bsa`; the edges are 512 px long and
resample along their run, the corners never do.

Colors are read from the install's own `Morrowind.ini` `[FontColor]` section
rather than sampled or guessed:

| Key | RGB | Used for |
|---|---|---|
| `color_background` | `0,0,0` | the panel |
| `color_normal` | `202,165,96` | body text |
| `color_link` | `112,126,207` | a clickable topic keyword |
| `color_link_over` | `143,155,218` | hover |
| `color_header` | `223,201,159` | the NPC's name |
| `color_answer` | `150,50,30` | a chosen answer, echoed back |

### <a id="the-font"></a>The font is Morrowind's own

**Code:** `asset_convert/ui/morrowind_font.py`

Skyrim's `$EverywhereMediumFont` was the probe's expedient; the real menu uses
Morrowind's own face, which is what makes the window read as Morrowind's.

`Data Files/Fonts/century_gothic_font_regular.fnt` is the UI face — metrics
plus a 256 × 256 `.tex` atlas whose glyphs are white with the shape in alpha,
so tinting is a solid fill wearing the glyph's alpha. `daedric_font` and
`century_gothic_big` sit beside it for Daedric text and titles.

The layout is **OpenMW's `components/fontloader/fontloader.cpp`**, not
reverse-engineering: a 296-byte header (`float fontSize`, two `int 1`,
`char[284]` atlas name) then 256 × 56-byte `GlyphInfo` of
`{unknown, 4 corner Points, width, height, kerningLeft, kerningRight, ascent}`.

Three rules from that file that are wrong if guessed:

| Quantity | Value |
|---|---|
| advance | `mWidth + mKerningRight` — **not** `mWidth` |
| bearing | `(mKerningLeft, fontSize - mAscent)` — the vertical offset |
| glyph rect | `TopLeft * size`, extent from `TopRight.x` and `BottomLeft.y` |

The rect uses **three** of the four corners — origin from `TopLeft`, width from
`TopRight.x`, height from `BottomLeft.y`. Verified over
`century_gothic_font_regular`: that formula reproduces the stated `mWidth` and
`mHeight` for every glyph, and the cut glyphs read correctly. Guessing the
field order instead yields rects that are non-empty, correctly sized, and cut
from the wrong place — the text renders as scrambled letters, not as nothing.

🛑 **Advance is one pixel UNDER width.** Measured over that face: all **94
printable glyphs carry `mKerningRight = -1.0`**, uniform authored tracking that
tucks each glyph a pixel left. It looks like an off-by-one and is not — dropping
the kern sets every line a pixel per glyph too wide. `tests/
test_morrowind_menu_art.py` asserts the relation so it cannot be "fixed" back.

### <a id="one-bitmap"></a>🛑 One bitmap, one fill

The frame composes into **ONE** image rather than nine placed clips. That is the
rule `asset_convert/ui/ui_menus.py` established across five in-game rounds:
**this engine draws a shape's FIRST bitmap fill across the whole shape and
ignores the rest.** Nine rects would render as one stretched corner.

Composing offline also keeps the corners' authored pixels exact — only the four
edges resample, along the one axis they run.

## <a id="activation"></a>Activation: which NPCs get this menu

**Code:** `tes_runtime/morrowind_runtime/plugin/activation.cpp`,
`tes5_import/dialogue/morrowind_sidecar.py`

Routing is **one bit test on the FormID's load-order index byte**, the rule
this project already uses everywhere (`project_master_index_routing`). At load
the DLL asks the engine for each converted plugin's [current
index](#load-order) and sets that bit; a plugin the user has not installed
never sets one.

```
idx = formId >> 24
if !mwPluginMask.test(idx):              return false   # vanilla, untouched
if !speakers.count(formId & 0xFFFFFF):   return false   # a mute Morrowind actor
OpenMorrowindDialogue(formId);           return true
```

The first test rejects every vanilla Skyrim NPC — and every Oblivion-converted
one — before any map is touched, which is what keeps the hook off the hot path
for content this runtime has nothing to do with. 🛑 Never route by EditorID or
file name (`feedback_never_classify_by_filename`).

### <a id="load-order"></a>🛑 The index byte in the sidecar is NOT the runtime one

**Measured in-game: `TR_Mainland` converts at index `0x03` and the player's
load order gives it `0x22`.** An NPC the log called `22C553BA` is stored in
`NPC__index.txt` as `03C553BA`. Comparing whole FormIDs matched nothing, every actor
fell through to vanilla, and — because a converted Morrowind NPC has no Skyrim
dialogue either — activating one did nothing at all.

This is `project_master_index_routing` again: **a raw FormID is meaningless
across plugins.** The fix has two halves:

- `NPC__index.txt` is keyed by the **local** id (`formId & 0x00FFFFFF`); the stored
  index byte is discarded on load, because it records only where the plugin sat
  on the converting machine.
- The runtime index is resolved **once per plugin at load** by
  `Game.GetFormFromFile` (id 55465) against any one of that plugin's own forms,
  then that bit is set in the mask. The hot path stays a single bit test.

`ResolveIndex` tries `.esm` then `.esp`, and a plugin that is not installed
resolves to nothing and simply never sets a bit — which is also how the runtime
stays inert for a load order that has no Morrowind content.

The log now prints the resolved index per plugin
(`TR_Mainland -> 8522 actor(s) at load-order index 22`), so this class of
failure names itself rather than presenting as silence.

### The actor index, and why import writes it

The runtime routes by **FormID** but filters dialogue by **TES3 id**, so
without a map between them it can tell an actor is Morrowind's and still not
know who they are. `NPC__index.txt` is that map, `FormID=EditorID` per line, written
into the sidecar at import because the FormID is minted during import and does
not exist at export time. A Morrowind NPC's `EditorID` *is* its TES3 string id,
which is what `INFO.Actor` names.

### <a id="why-not-the-menu"></a>🛑 The dialogue menu is the WRONG hook

The first attempt sank `MenuOpenCloseEvent` and diverted when Skyrim's
"Dialogue Menu" opened on a Morrowind speaker. **It cannot work**, and the
reason is structural rather than a bug: a converted Morrowind NPC has no Skyrim
dialogue at all, so that menu never opens and the sink never fires. Waiting for
a signal the content by construction never emits.

`TESObjectREFR::ActivateRef` (id 19796) is the real activation entry — both the
player's activate and Papyrus's `ObjectReference.Activate` reach it. But it
**cannot be detoured**: its prologue opens `48 8B C4`, `mov rax, rsp`, which
`AnalyzePrologue` refuses outright because a relocated copy captures the
*trampoline's* stack pointer. That exact instruction crashed the game on
2026-08-14, and the refusal is documented in `game_bridge/plugin/detour.cpp`.

### The interception point: a vtable swap

`ActivateRef` ends by dispatching through the **base form's** vtable:

```asm
mov  rcx, [rsi + 0x40]     ; the base form (TESNPC)
mov  rax, [rcx]
call qword ptr [rax + 0x1b8]   ; TESNPC::Activate(this, ref, activator, ...)
```

So `TESNPC` vtable slot **`0x1b8`** is swapped. A vtable steals no bytes, so the
prologue hazard does not arise at all — and the slot fires for every NPC
activation, dialogue or not.

Checking the **base form's** FormID rather than the ref's is what makes one
indexed NPC match all of its placed references.

Returning `true` without calling the original is what suppresses Skyrim's own
activation, so no vanilla menu appears behind ours. Anything not claimed calls
straight through.

| What | ID | 1.6.1170 | How it was found |
|---|---:|---|---|
| `TESNPC` vtable | 195816 | `0x17e4d50` | RTTI; slot `0x1b8` verified to hold `TESNPC::Activate` on the running build |
| `TESNPC::Activate` | 24715 | `0x3b9500` | the `[rax+0x1b8]` dispatch at the end of `ActivateRef` |
| `UIManager::AddMessage` | 13631 | `0x1af260` | pool arithmetic: `[rcx+0x378]` vs `0x40`, `(n+0x1c)<<5` → `messagePool` at `0x380` |
| UIManager singleton | 400445 | `0x20f8950` | loaded beside the name table at the `AddMessage` call sites |
| `BSFixedString` ctor | 69161 | `0xcec5d0` | ~20 consecutive menu-name internings |

The last three invert to exactly the RVAs SKSE hardcodes for 1.5.97, which is
what confirms them.

🛑 **The install REFUSES to swap** a slot not already holding
`TESNPC::Activate`. Writing the wrong slot would hand the engine our function
for an unrelated virtual — a failure no log would explain.

### <a id="opening-a-menu"></a>Opening a menu: post a UIMessage

A menu opens by posting `kMessage_Open` (**1**; close is **3**) through
`UIManager::AddMessage`, which is what the console's `showmenu` and every
engine call site do.

🛑 **The name must be an INTERNED `BSFixedString`.** `AddMessage` dereferences
its second argument and the queue compares by pointer, so a plain `const char*`
never matches a registered menu — it fails silently, with no menu and no error.
Every name therefore goes through the interning constructor first.

`ReceiveEvent` returns `kEvent_Continue` (0) always: the event is never
consumed, so every other sink still sees it and vanilla behaviour is untouched
for anyone we do not divert. It runs on the main thread inside the dispatcher's
lock, so it does one bit test, one hash lookup and at most two posted messages
— which the engine drains on its own schedule rather than inside our callback.

### <a id="the-menu-must-render-itself"></a>🛑 A menu DRAWS ITSELF: vtable slot 6

First in-game result: the menu registered, `LoadMovie` returned a non-null
`GFxMovieView*`, `showmenu MorrowindDialogueMenu` took mouse focus away from the
game world — **and nothing appeared on screen.**

The engine renders no menu on its owner's behalf. `IMenu::Render` is **vtable
slot 6**, and our vtable filled slots 6–15 with a no-op, so the movie was
loaded, on the stack, holding focus, and never drawn. `MessageBoxMenu::Render`
(`0x539ac0` on 1.6.659, stable id **33632**) is the whole of it:

```asm
mov  rcx, [rcx + 0x10]      ; this->view
test rcx, rcx
je   done
mov  rax, [rcx]
jmp  qword ptr [rax + 0x130]   ; GFxMovieView::Render
```

So `GFxMovieView::Render` is at vtable byte offset **`0x130`**, and a menu that
does not make this call is invisible by construction. SKSE's `CustomMenu`
overrides `Render()` for exactly this reason; the vtable-by-hand approach has to
supply it explicitly.

### <a id="imenu-layout"></a>🛑 `IMenu` field offsets, from a constructor

The first attempt put `flags` at `0x20`, which is a different field, so the flag
word was written where the engine keeps something else and `flags` stayed zero.
`MessageBoxMenu`'s constructor (`0x8ec1cc` on 1.6.659) is the authority — it is
the simplest single-vtable modal panel the engine ships, and the one SKSE's
`CustomMenu` was itself modeled on:

```asm
lea   r8,   [rbx + 0x10]        ; &view            -> view    at 0x10
mov   dword [rsp + 0x20], 3     ; scaleMode = 3, NOT 2
call  0xf22f80                  ; GFxLoader::LoadMovie  (id 82325)
mov   byte  [rbx + 0x18], 0xa   ; context          -> 0x18
mov   dword [rbx + 0x1c], 0x11  ; flags            -> 0x1C, not 0x20
mov   dword [rbx + 0x20], 1     ; depth            -> 0x20
call  0xc4d690                  ; IsGamepadEnabled (id 68622)
test  al, al
jne   skip
or    dword [rbx + 0x1c], 0x404 ; |= UsesCursor | UpdateUsesCursor
```

| Offset | Field | Value a plain modal panel uses |
|---|---|---|
| `0x10` | `view` | filled by `LoadMovie` |
| `0x18` | context | `0xA` |
| `0x1C` | **`flags`** | `0x11` = `kPausesGame \| kModal`, `\| 0x404` for the cursor |
| `0x20` | depth | `1` |

Flags are set **after** `LoadMovie`, not before. `scaleMode` is **3**
(`kNoBorder`) — every vanilla menu pushes 3, and the `2` first used here was a
guess. Id 68622 (`IsGamepadEnabled`) does not exist in 1.6.1170, so the cursor
bits are set unconditionally rather than branching on it.

### <a id="edit-text-flags"></a>🛑 `DefineEditText` flags gate the fields after them

The probe's text field could never have rendered a glyph, for two compounding
reasons — and neither raises an error.

**The flag bits were LSB-first; SWF packs them MSB-first.** Decoding what the
first probe actually wrote:

| Intended | Actually set |
|---|---|
| `HasText HasTextColor ReadOnly NoSelect` | `HasText Multiline ReadOnly` **`HasMaxLength`** |
| `UseOutlines Multiline WordWrap` | **`HasFontClass`** `AutoSize NoSelect UseOutlines` |

`HasTextColor` was never set, yet four color bytes were written anyway;
`HasMaxLength` and `HasFontClass` were set with no field behind either. Each
flag gates the field that follows it, so every later field slid and the variable
name was read out of the middle of the color — a tag that parses without error
into nonsense.

**There was no font.** Skyrim's menus import `$EverywhereMediumFont` from the
shared `gfxfontlib.swf` rather than embedding glyphs
(`asset_convert/ui/ui_menus.py`). A `DefineEditText` with neither `HasFont` nor
`HasFontClass` has no glyph source at all. The movie now emits `ImportAssets2`
for that face and the field names its character id.

The field order, which is positional and unforgiving:

```
CharacterID  Bounds  Flags1 Flags2
  [HasFont]      -> FontID u16, FontHeight u16
  [HasTextColor] -> RGBA
  [HasLayout]    -> align u8 + 4 x u16   (NINE bytes, not ten)
VariableName\0  InitialText\0
```

`tests/test_morrowind_menu_swf.py` asserts each flag against the field actually
written, and that nothing trails the text — a slid field always leaves bytes
behind.

### <a id="menu-registration"></a>Registering the menu, derived from the live game

Found by attaching to the running process (`skyrim_disasm.py --live
--save-image`), because the Steam build's on-disk `.text` is DRM-encrypted.
Menu names are plain strings in `.rdata`, so the registration site is whatever
call the most distinct menu-name LEAs reach:

```
call targets reached from menu-name LEAs, outside the ctor:
  0x00cec5d0   10 distinct menus   <- BSFixedString ctor, interning names
  0x00fa5480    (the jmp target)   <- MenuManager::Register
```

A registration site, in full (`BarterMenu`):

```asm
lea  rcx, [rip + ...]        ; the MenuManager singleton
call 0x00fa32f0              ; GetSingleton()
mov  qword ptr [...], rax
lea  r8,  [rip + 0x83c866]   ; 0x8ef2b0  the CREATOR function
mov  rcx, rax                ; MenuManager*
lea  rdx, [rip + 0x1f647ec]  ; "BarterMenu"
add  rsp, 0x28
jmp  0x00fa5480              ; Register(this, name, creator)
```

So the contract is `Register(MenuManager*, const char* name, IMenu* (*)())`,
which is what SKSE's `CustomMenu` assumes. Independently derived here and
identical to the RVA SKSE hardcodes (`0x00FA5480`).

| What | Address Library id | RVA on 1.6.1170 |
|---|---:|---|
| `MenuManager::GetSingleton` | **82072** | `0x00fa32f0` |
| `MenuManager::Register` | **82086** | `0x00fa5480` |
| a vanilla menu creator (shape reference) | 51015 | `0x008ef2b0` |

Both ids exist in all 12 shipped versionlibs and keep a constant `0x2190` gap,
so nothing here is a raw RVA and a game update does not take the menu offline.

🛑 **The engine's own name for the dialogue menu is `"Dialogue Menu"`, with a
space** — not `DialogueMenu`, which is the SWF's filename and what SKSE's
header suggests. Both strings exist in the image; only the spaced one is what
`Register` is called with.

### <a id="singleton-is-not-a-getter"></a>🛑 `0xfa32f0` is a CONSTRUCTOR, not a getter

The first build crashed on load: `EXCEPTION_ACCESS_VIOLATION` at
`SkyrimSE.exe+0xFA40CC`, `mov r10, [r9]`. The plugin log ended right after
resolving its four addresses, so the fault was inside `InstallMenu`.

Reading the registration site more carefully shows the shape I had missed:

```asm
mov  rax, [0x20f6a00]   ; the singleton POINTER
test rax, rax
jne  .have_it           ; already built -> use it
lea  rcx, [0x315ceb0]   ; else placement memory
call 0x00fa32f0         ; the CONSTRUCTOR
mov  [0x20f6a00], rax   ; cache it
.have_it:
mov  rcx, rax
jmp  0x00fa5480         ; Register
```

`0x00fa32f0` takes `rcx` (`mov rsi, rcx` at +0x1e, then stores it). Calling it
as `GetSingleton()` passed garbage as placement memory and corrupted the
manager. **Read `0x20f6a00` (id 400327) instead; never call the constructor.**
By `kMessage_DataLoaded` it is always already built.

### <a id="scaleform-heap"></a>A menu must come from the Scaleform heap

Vanilla creators allocate through the allocator singleton at `0x3292490`
(id 412058), vtable slot `0x50`, as `Alloc(this, 0xa8, 0)`.

🛑 **The engine frees a menu through that same allocator**, so a menu from
`HeapAlloc` is a crash when the menu closes rather than when it opens — the
worst kind, because the open looks like it worked.

## <a id="sidecar"></a>The sidecar: how dialogue reaches the runtime

The runtime is an SKSE plugin in the player's Skyrim install. **It never sees
this repo's `export/`.** So the import stage copies each plugin's `DIAL.txt`
and `INFO.txt` into that plugin's own output at

```
<plugin output>/SKSE/Plugins/MorrowindRuntime/<plugin stem>/
```

which installs alongside every other converted asset, and which the DLL walks
at `kMessage_DataLoaded` — one subfolder per plugin, so one plugin's dialogue
can never be attributed to another's.

Each file is named after the GRUP it holds — `DIAL.txt`, `INFO.txt`,
`NPC__index.txt` (the [actor index](#activation)), `GLOB.txt`, `FACT.txt`,
`GMST.txt`, `SKIL.txt`, `NPC_.txt`, `SCPT_source.txt`, `SCPT_locals.txt`,
`SCPT_objects.txt`, `SCPT_instances.txt` — with `items_formid.txt`,
`refs_formid.txt` and `quests_formid.txt` for the three id→FormID maps, which
belong to no single record type.

🛑 **A sidecar holds its OWN plugin's records only.** The DLL reads every
sidecar into one set of tables, so a master's globals and scripts resolve from
the MASTER's folder rather than being copied into each dependent.
See: [../plans/morrowind_object_scripts.md#masters-stage-themselves](../plans/morrowind_object_scripts.md#masters-stage-themselves)

### 🛑 The root comes from THIS MODULE, not the host process

`SidecarDir()` resolves `GetModuleHandleEx(FROM_ADDRESS)` on one of its own
functions and appends `MorrowindRuntime\`. An SKSE plugin is always loaded from
`Data\SKSE\Plugins\`, which is exactly the folder holding the sidecars, so this
needs no assumption at all.

Deriving it from `GetModuleFileNameA(nullptr)` and appending
`Data\SKSE\Plugins\...` assumes the host process sits beside the Data folder
this plugin was loaded from. That has no upside over asking the module itself,
and when it is wrong the failure is silent: `0 sidecar(s)`, no dialogue, and
every activation falling through to vanilla.

The loader logs the resolved root and a per-file result, so a miss names the
path it looked in rather than only its own disappointment.

The files are **copied, not re-serialized**. The exporter already writes the
format the runtime parses (`docs/reference/morrowind_dialogue_format.md`), so a
second writer here would be a second thing to keep in step with it.

Load order inside the DLL is DIAL for every plugin first, then INFO: an INFO
whose topic is unknown is dropped, and a topic may be defined by one plugin and
extended by another.

## <a id="the-conversation"></a>The window's behaviour lives in C++

**Code:** `plugin/menu.cpp`, `plugin/conversation.cpp`,
`tools/generators/gen_morrowind_menu_swf.py` (writes `plugin/menu_layout.h`)

The movie carries **no ActionScript**. The generator writes every hit rect, the
path of every field and sprite, the ini colors and the font's advances into
`menu_layout.h`, and `conversation.cpp` resolves input against those numbers —
which is what OpenMW's `DialogueWindow` does over MyGUI. Four engine facts make
that possible, each read off the GOG build's `IMenu` vtable (`0x18a4e90`):

| Fact | Evidence |
|---|---|
| `GFxMovieView` slots are INDICES: SetVariable `0x10`, GetVariable `0x11`, Invoke `0x16`, Advance `0x25`, Render `0x26`, HandleEvent `0x2d` | base `IMenu::NextFrame` writes `CurrentTime` through `[vt+0x80]` and advances through `[vt+0x128]`; base `ProcessMessage` forwards through `[vt+0x168]` |
| `ProcessMessage` must forward type **6** (`BSUIScaleformData`, event at `+0x10`) to `HandleEvent` | base returns 0 after forwarding, 2 otherwise |
| `NextFrame(this, float dt, u32)` must call `Advance` | a menu that skips it never processes the events it was handed |
| The wheel arrives as user events `Zoom In` / `Zoom Out` (type **7**, name at `+0x18`); `Cancel` is Tab/Escape | logged in game |

🛑 The first build added `0x10` to the vtable as a BYTE offset — slot 2 — so
`SetVariable` never ran and every field stayed empty with nothing logged.

### <a id="scale-mode"></a>🛑 `kNoBorder` crops; a modal panel wants `kShowAll`

Vanilla menus pass scale mode 3. Measured on 3440x1440: the 1280x720 stage
scaled by 2.69 to the width and lost 247 px top and bottom, giving a 1580 px
window running off the screen. Mode 1 fits the whole stage. Clicks confirm the
mapping: event `(2029, 567)` arrived as stage `(795, 283)` = `((x-440)/2, y/2)`.

### <a id="one-movie-per-session"></a>🛑 The movie is never torn down

Calling `IMenu`'s own destructor on close crashed inside the movie's teardown:
Scaleform's heap `Free` (id 84520, a page-table lookup by address) on a 0x38-byte
object — a `GString` and three refcounted members — the heap never held. The
allocator is not the cause: the deleting destructor frees through
`[0x30c6900]->vt[0x60]`, the same singleton (id 412058) the menu is allocated
from at `vt[0x50]`. The double release was not identified, so slot 0 keeps the
menu and `MenuCreator` hands the same one back on every later open.

### <a id="open-and-close-come-from-slot-4"></a>🛑 Open and close are MESSAGES, not slot 0

**Slot 0 is called EVERY FRAME, not once at close.** Nulling `g_menu` there is
what broke the second conversation of every session: from frame 1 of the reopen
`g_menu` was null, so `LiveView()` was null, so `ApplyText`, `SetMenuNumber` and
`GetMenuNumber` all returned early — every field write and every mouse-position
lookup silently dropped. `MousePosition` returned false, `HandleScaleformEvent`
never called `g_input.click`, and the window drew, advanced and held focus with
nothing clickable and no way out but `hidemenu`.

Restoring `g_menu` from slot 4/5 instead made it worse, because slot 0 kept
nulling it: `MenuInput::opened` re-fired **7,645 times in one conversation**,
`PushAll` rebuilt the pane every frame, and the menu showed no text at all.
That count is what identified the per-frame call.

The lifecycle therefore comes from the two UIMessages the engine delivers to
slot 4 — `kMessage_Open` (**1**) and `kMessage_Close` (**3**) — guarded by one
`g_open` bool so each fires once. `MenuCreator` only CONSTRUCTS (the manager
skips it whenever it still holds an instance), and slot 0 does nothing at all.

Measured on the frozen process through the bridge: the menu was on the render
stack at depth 1 with `flags=00000455` and its movie still advancing, which is
what ruled out the earlier stale-flags and occlusion theories.

### <a id="the-engine-may-skip-the-creator"></a>The creator is NOT the open notification

Keeping the menu means `MenuManager` still holds an instance under the name, and
**a reopen then never calls `MenuCreator` at all**. Binding `g_menu` inside the
creator therefore left it null across such a reopen, and with it `LiveView()`:
`ApplyText`, `SetMenuNumber` and `GetMenuNumber` all return early on a null
view, so every field write and every mouse-position lookup was silently dropped.
`HandleScaleformEvent` got `known == false` from `MousePosition` and never
called `g_input.click`, so the window drew, advanced and held focus with nothing
in it clickable, and Tab could not leave. Only `hidemenu` recovered it.

Read off the frozen process through the bridge, which is what settled it:

| Menu | flags | depth | on the render stack |
|---|---|---|---|
| `HUD Menu` | `00018942` | 19 | yes |
| **`MorrowindDialogueMenu`** | **`00000455`** | **1** | **yes, top interactive** |
| `Cursor Menu` | `00008840` | 19 | yes |

The flags were correct (`0x415` plus `0x40` the engine sets itself), the movie
was still advancing, and nothing was stacked above it — so the earlier
flags/occlusion theories were both wrong. The tell was in the log: the frozen
open printed `posted open` and then **no creator line of any kind**.

`AdoptMenu` therefore binds whatever menu the engine passes to `ProcessMessage`
or `NextFrame`, and firing `MenuInput::opened` is its job, not the creator's.
Both `MenuCreator` paths route through it.

### What a bare SWF cannot do

| Trap | Rule |
|---|---|
| A named bare SHAPE is not scriptable | only a MovieClip answers to `_x`, `_visible`, `_width`; every moving part is a one-frame `DefineSprite` |
| `SetVariable` on a field's bound variable sets PLAIN text | HTML goes to `<field>.htmlText` |
| `DefineEditText` align | 0 left, **1 right**, 2 center |
| Line pitch | ascent + descent, NOT + leading; then calibrated from `textHeight / numLines` |
| `TextField.getCharIndexAtPoint` | absent in this Scaleform; keyword links are hit-tested with the plugin's own word wrap over the font's advances, cross-checked against `numLines` |
| `DefineFont2`'s 1024 em | rounds a 2048-unit face and lets near-touching edges cross (the v's tip, the k's foot); `DefineFont3` stores 20x and converts exactly. Contours close explicitly |
| Arrow textures | 32x32 with the arrow in the top-left 20x20; crop to the alpha bbox before scaling |

Layout is the skins', not invented: `MW_Window`'s caption at `4 4 W-8 20`, a
second thick frame at inset 4 below it, client at `(8, 28)`; `MWList` rows of
font + 2 = 18 px with 3 px padding, an 18 px separator; `MW_VScroll` 14 px wide.

## <a id="result-scripts"></a>Result scripts run on OpenMW's own compiler

**Code:** `plugin/script_runner.cpp`, `plugin/script_context.cpp`,
`plugin/dialogue_state.cpp`, `plugin/script_test.cpp`

`RunResultScript` is `DialogueManager::executeScript`: the vendored scanner,
`ScriptParser` and interpreter, with `Compiler::registerExtensions` used WHOLE
so every command parses. What a command DOES is the port's progress:

| Kind | How |
|---|---|
| Real | journal, topics, `Choice`, `Goodbye`, disposition, reputation, faction reactions, the player's factions and crime level, `AddItem`/`RemoveItem`/`GetItemCount`, `StartScript`/`StopScript`/`ScriptRunning` |
| Deliberate no-op | `ShowMap`, `FadeIn/Out/To`, `ClearInfoActor` |
| Stub | pops exactly the arguments its signature pushes (letters `Sclsf` before `/`, plus one for an explicit reference, plus the optional count on segment 3), returns zero, logs its name once |

The extension table keeps its opcodes private, so each stub's opcode is
recovered by asking the table to GENERATE the command and reading the word.

<a id="the-context"></a>`DialogueContext` is the `Interpreter::Context` both the
scripts and `fixDefinesDialog` (`%name`, `%PCName`) run under.
<a id="dialogue-state"></a>`DialogueState` is what scripts write and the filter
reads: journal, disposition, globals, locals by owner, the player's factions.

### <a id="script-tables"></a>🛑 The parser asks "global?" BEFORE "id?"

So a compiler context that answers "global" for an unknown name turns
`player->...` and `Script.member` into syntax errors. Measured over all 26,820
authored Tamriel Rebuilt result scripts (`script_test --sweep`):

| Context | Failures |
|---|---|
| every unknown name is a float global | 1,428 |
| globals and script locals from authored tables | 415 |
| + every scripted object type, each INFO's own speaker | 265 |
| + master scripts resolved through `_formid_here` | 165 |

The remainder use a speaker's locals where the INFO names no actor, which the
sweep cannot know and the game does. An unknown `set` target is a WARNING that
skips the line (`lineparser.cpp`), by OpenMW's own rule.

Reach of the commands the content calls most: `AddItem` 4,492, `RemoveItem`
2,355, `ShowMap` 1,728, `SetFight` 1,159, `StartCombat` 1,148, `ModPCFacRep`
1,129, `StartScript` 855, `GetItemCount` 841.

### 🛑 Dialogue is cumulative, and the filter compares NAMES

`tes5_import/dialogue/morrowind_sidecar_source.py` builds the sidecar's
dialogue and actor table from the TES3 BINARIES of the plugin and every master
it can find, merged as OpenMW's `InfoOrder` merges them (replace in place, else
after `PNAM`, else before `NNAM`, else first when it names no predecessor).
Measured on TR_Mainland: 69,270 responses alone, **106,958** merged over
Morrowind, Tribunal, Bloodmoon and Tamriel_Data, in 15 s. "join the Fighters
Guild" went from 2 responses to 29 — and still needed the speaker's faction,
which the text export holds only as a minted FormID. `NPC_.txt` carries each
NPC's race, class, faction, rank, base disposition, gender and name as authored.

### <a id="unknown-functions"></a>🛑 A rule the runtime cannot judge REJECTS

`SCVR` function 1 is a NUMBERED function, 0..73, and the filter first answered
an index it had no case for by PASSING, on the reasoning that an unimplemented
check should not hide a response. Measured in game: **every NPC** opened with
"Get away from me, vampire!" and said goodbye. `Function_PcVampire` is index
**59** and had no case, so `PCVampire == 1` passed, and that greeting sits
early in the list where Morrowind takes the first match.

Passing is the wrong default for the same reason the order matters: an
unanswerable rule that passes decides FOR the response it guards, while one
that rejects simply lets the next INFO answer. `filter.h` now names all 74
indices, so an index with no case is a rule genuinely outside this runtime,
and `PlainValue` answers the rest: the 27 skills and 8 attributes through the
stat map, and a flat NO for vampirism, lycanthropy, corprus, disease and the
weather.

### <a id="rank-requirements"></a>`RankRequirement` is a BITMASK, and it judges the PLAYER

Function index 2 reads as "what rank does the speaker hold", and the filter
first answered it that way. It is the opposite: the faction is the SPEAKER's,
but the rank measured is the PLAYER's, and the answer is two bits — **1** for
the skills and attributes, **2** for the faction reputation. So `== 3` means
every requirement for the next rank is met, and the 0 returned at rank 9 is
indistinguishable from the 0 of an unqualified character.

A non-member is rank -1, so `rank + 1` is 0 and joining tests row 0. Measured
from `Morrowind.esm`'s own FACT record, the Fighters Guild's row 0 is **not**
all zeros:

| Rank | Attributes | Primary skill | Favoured | Reputation |
|---|---|---|---|---|
| 0 (join) | 30 / 30 | 0 | 0 | 0 |
| 1 | 30 / 30 | 10 | 0 | 5 |
| 9 | 35 / 35 | 90 | 35 | 125 |

The judged attributes are Strength and Endurance, so **joining needs 30 in
both** and no skill at all. Returning the speaker's rank instead — 8 for
Sharnoga gra-Mal — never equals 3, so the join offer was never reachable and
the flat "you don't meet our requirements" answer won every time.

The skill test is not per-named-skill. `NpcStats::hasSkillsForRank` sorts the
player's values for the faction's seven skills and measures the best three:
one at `mPrimarySkill`, two more at `mFavouredSkill`. The requirement rows
reach the runtime as `FACT.txt`, staged from the FACT records of the plugin
and its masters.

### <a id="chargen-topics"></a>🛑 The universal topics come from ONE result script

A topic is listed only when the speaker can answer it AND the player has heard
of it, and `mKnownTopics` starts empty — OpenMW has no seeding mechanism, no
hardcoded list, and no always-known flag. Vanilla seeds it from **game data**:
the `duties` INFO spoken by `chargen captain` in the Seyda Neen census office
ends with nine `AddTopic` calls.

```
addtopic "specific place"    addtopic "someone in particular"
addtopic "services"          addtopic "my trade"
addtopic "little secret"     addtopic "latest rumors"
addtopic "little advice"     addtopic "Caius Cosades"  addtopic "South Wall"
```

Every playthrough passes through that conversation in its first minutes. **A
converted world is entered somewhere else entirely**, so the bootstrap never
runs and the known set stays empty forever — which is why an ordinary NPC came
up with a short list or none at all, while an NPC whose greeting happens to
name its own topics still worked.

The fix keeps the gate and supplies the bootstrap the same way the data does:
the runtime reads the `AddTopic` calls out of the chargen actor's own result
scripts and hands them over at the first conversation. Nothing is named in
C++, so a total conversion with a different opening scene seeds from its own
chargen INFO. Measured over the merged TR_Mainland chain: **9 topics**, and
152 INFOs seed four or more.

### <a id="rank-names"></a>🛑 A quest with no OBJECTIVES never shows, and `%PCRank` needs the FACT names

Two in-game faults from one round, both invisible offline until the harness
was taught to look:

**"You are now Prisoner the ␣ in the Fighters Guild."** `%PCRank` and
`%NextPCRank` resolve through `Interpreter::Context`, and all three rank
getters returned `""`. The names are the FACT record's ten `RNAM`
subrecords, which the sidecar was not staging; they now ride in `FACT.txt`
beside the requirement rows. A **non-member reads rank 0**, not "no rank" —
Morrowind's own quirk, and exactly what makes the line read correctly in the
INFO that admits the player. Only dialogue text goes through
`fixDefinesDialog`; `MessageBox` still does not, which is a separate gap.

**The journal stayed empty although `SetStage` returned ok.** `SetStage` is
not the problem: the CK wiki is explicit that it *starts the quest itself*
("Is latent and will wait for the quest to start if it has to start the
quest"), so `Quest.Start()` was never needed. The cause is that
`Quest_Data_Tab` states a quest **with no objectives never displays its name
anywhere** — and the generator emitted none. Vanilla `DA13` carries 8
`QOBJ`/`NNAM` pairs against this runtime's 0. One objective per stage is now
derived from the page's first sentence, capped at `OBJECTIVE_MAX_CHARS` (71),
so nothing has to be hand-authored.

### <a id="one-quest-writer"></a>The journal QUST is written here, not by `convert_QUST`

`quest_morrowind.py` writes its own QUST record. The shared `convert_QUST`
reads a TES4 export shape this plugin has no source for -- a TES3 journal is
DIAL/INFO, there is no QUST export, and these records are synthesized during
import -- so routing through it meant building a fake TES4 record and then
opting out of the parts that do not apply.

Two things the bespoke writer has to get right, both of which a first attempt
got wrong and cost several rounds:

- **QOBJ/FNAM/NNAM per page.** A QUST with no objectives never displays its
  name however its stages are set, so the journal stayed empty.
- **The FormID is written VERBATIM.** `derive_formid` already returns this
  plugin's final id; `get_formid` remaps ids that predate the new masters, so
  routing a final id through it added the load-order offset a second time and
  pushed `0418E9F6` to `0518E9F6` -- one past the last master, resolving to
  nothing. `pack_record` takes the id as given.

Verified on the built ESM: all 2,079 advertised ids in `quests_formid.txt` resolve to a
real QUST, none missing, each with an objective per journal page.

### <a id="objectives-must-be-displayed"></a>🛑 A journal stage is the CONSOLE's `setstage`, then a wait for `IsRunning`

**Code:** `game_calls.cpp` (`StartQuest`, `SetStage`, `StageOnceRunning`,
`ShowObjective`). Confirmed in game: quest started, journal text and
objective shown, quest listed as active.

A generated QUST has no stage fragments, so nothing displays its objectives
and a quest can be running at the right stage with its journal empty. The
runtime sets the stage and displays the objective itself, and three engine
facts fix HOW, each read from the GOG 1.6.659 disassembly and measured live:

**1. The Papyrus native only QUEUES a stopped quest.** `Quest.SetCurrentStageID`
(`0x9e7f90`) calls `TESQuest::EnsureQuestStarted(quest, bool* justStarted,
bool startNow)` (`0x38a020`, id 25003) with `startNow = 0`, which pushes the
quest onto BGSStoryTeller's promotion queue (`0x4ec040`) and defers the stage
through `0x951640`. `sqv` reports `Waiting For Promotion`. The console's
`setstage` handler (`0x30df30`, from the SCRIPT_FUNCTION table) passes
`startNow = 1`, which runs `TESQuest::Start` (`0x38d080`) on the spot, then
calls `TESQuest::GetStage` (`0x38ae70`, id 25028) and `TESQuest::SetStage`
(`0x38a130`, id 25004) directly, skipping a start-up stage the start already
ran. The dialogue menu has `kFlagPausesGame`, so a queued promotion never
happens while it is open; the runtime ports the console's sequence.

**2. The objective goes through the console's pair, not the Papyrus native.**
`setobjectivedisplayed` (`0x31b5b0`) calls `TESQuest::GetObjective`
(`0x389300`, id 24981) and `BGSQuestObjective::SetState` (`0x354870`,
id 23933). A hook on `Quest.SetObjectiveDisplayed` (id 56682) recorded zero
hits while that command changed the state. States, from the console
handlers: 0 dormant, 1 displayed, 2/3 completed (3 = was displayed),
4/5 failed. The previous step is COMPLETED, never hidden: a Morrowind journal
is a running log the player reads back.

**3. Stage and objective wait for the start to FINISH.** Both are filed under
the quest's current instance (`TESQuest+0x50`), and so is the stage's log
entry. Right after the synchronous start the quest still holds a pending
start at `+0x248` and its instance is 0; the StoryTeller finishes the start
on a later unpaused frame and the instance becomes 1. Set in the same trip,
the player's `BGSInstancedQuestObjective` (`PlayerCharacter+0x588`, `{objective*,
u32 instance, u32 state}`) records instance 0 and the log entry lands on the
wrong instance: the journal builder (`0x92c3bf`) lists a quest whose
instances differ as finished, and shows no text. The console's own
`setstage` + `setobjectivedisplayed` batch reproduces the mismatch; vanilla
never sees it because a fragment's call runs from the Papyrus VM afterwards.
`Quest.IsRunning` (id 56727) is false exactly until the finish, so the
runtime polls it once per ~50 ms from a detached sleeping thread that posts
one task. 🛑 Never a self-reposting SKSE task: the pump drains reposts in the
same sweep, so 120 retries expired inside one second and a 15 s wall-time
bound froze the game for 15 s.

Theories disproved on the way, each by measurement: the argument layout, the
VM pointer (`nullptr` works too), the record's DNAM flags (280 of 396 vanilla
objective-bearing quests clear bit 0 as well), a missing objective node, and
"needs its own pump tick". An unconditional "objective displayed" log line
hid the failure for three rounds; every log line now reads the state back.

### <a id="quest-trace"></a>Asking whether a quest can be FINISHED

**Tool:** `python -m tools.dialog.morrowind_quest_trace --plugin <esm> --quest <id>`

A TES3 quest advances by `Journal <id> <index>`, reached from an INFO's result
script — which this runtime runs — or from an object script, which still goes
down the Papyrus path. So a stage is one of four things, and the difference is
what makes a quest finishable:

| Verdict | Meaning |
|---|---|
| `OK` | dialogue sets it and every command it uses is implemented |
| `DEGRADED` | dialogue sets it, but some command in the script does nothing |
| `BLOCKED` | only an object script sets it |
| `UNREACHABLE` | nothing sets it at all |

Measured over TR_Mainland's 2,086 journal quests and 11,726 stages:

| | Quests | Stages |
|---|---:|---:|
| `OK` | 792 | 8,270 |
| `DEGRADED` | 186 | 1,143 |
| `BLOCKED` | 699 | 1,463 |
| `UNREACHABLE` | 409 | 850 |

Both Old Ebonheart Fighters Guild quests are `BLOCKED`: "More Rats?" at stages
30 and 80, "Cursing Like a Witch" at stage 40, each on an object script
(`TR_m3_OE_FG_cr_Velkscr`, `TR_m3_OE_FG_q_VermaiScr`) needing `OnDeath`,
`CellChanged`, `GetDistance` or `GetDisabled`. **Object scripts, not missing
commands, are what stop quests finishing** — `--blockers` ranks both, and the
worst single script blocks 20 stages. That is what
[the object-script plan](../plans/morrowind_object_scripts.md) exists to fix.

🛑 Two parsing traps, both of which silently UNDER-report. The export escapes
tabs as a literal `\t`, and scripts indent their bodies with them, so
unescaping only newlines hides every indented statement — that alone had
"Cursing Like a Witch" reading as fully `OK`. And `ref->Command` puts the
target first, so a naive leading-word match blames the reference
(`TR_m3_q_Gerardus`) instead of the command.

### <a id="ai-settings"></a>The AI settings and `GetDeadCount` are the DLL's own

`SetFight` / `SetHello` / `SetAlarm` / `SetFlee`, their `Mod` and `Get` forms,
and `GetDeadCount` all name state Skyrim has no field for. They are worth
porting anyway because **dialogue both writes and reads them**: a result script
raises Fight and a later INFO filters on `Fight >= 90`, so leaving the filter
answering a flat 0 made those responses unreachable even though nothing
crashed. They live beside disposition, in the co-save.

`GetDeadCount` gates a great deal of quest dialogue — Old Ebonheart's "Cursing
Like a Witch" branches its ending on `getDeadCount TR_m3_Margia_Sycora > 0`,
choosing between the player having killed the witch and having lied about it.

🛑 **`GetDeadCount` is the ENGINE's count, not ours.** `ActorBase.GetDeadCount`
(55987, 0x9c5370, the only native of that name) counts every death of a base
actor and saves it. The DLL's own counter was only ever fed by a test, so every
kill check read 0; it is deleted, and the TES3 id resolves through
`bases_formid.txt`.

🛑 **An unset AI setting reads the authored AIDT**, not 0. `NPC_.txt` carries
`hello|fight|flee|alarm` as its last four columns; before that a filter on
`Fight >= 90` was false for every NPC no script had touched.

🛑 **A written setting also moves the actor value the engine acts on**
(`game_calls.cpp:ApplyAiSetting`), or `SetFight 100` changes a number and
nobody attacks:

| TES3 | Skyrim actor value | Rule |
|---|---|---|
| Fight | Aggression | 2 when OpenMW's `fight + (50 - disposition) * fFightDispMult + iFightDistanceBase >= 100` (the on-sight test at distance 0, GMSTs from `GMST.txt`), else 1, else 0 for Fight 0 |
| Flee | Confidence | the import's tiers on `100 - flee`: >=100 4, >=70 3, >=40 2, >=15 1 |
| Alarm | Assistance, Morality | the import's Responsibility mapping: >=30 assists; morality >=80 3, >=50 2, >=30 1 |
| Hello | none | the number is kept for the filter only |

Aggression 2 attacks neutrals, which the player is; 1 attacks enemies only.
NOT in-game verified. Disposition moving later does not re-run the rule.

Measured over the 44,950 authored result scripts, porting these moved the
unported call total from **6,505 to 4,538** and the command count from 119 to
108 — the largest single reduction available without an object reference,
because `SetFight` alone is 1,405 calls.

🛑 Those figures, and every other opcode count taken before 2026-09-17, cover
the INFO result scripts ONLY. Object scripts (`SCPT.SCTX`) are the larger
corpus and use a different command set, so counting both raises the total from
54,189 call sites to **91,581**. A second undercount sat beside it: whole
command families register in a LOOP over a name array with a COMPUTED name
(`get + dynamics[i]`), so a literal-only scan of `extensions0.cpp` saw 298
registrations where there are **487**, and called every dynamic-stat command
unregistered while it was ported. `tools/script/mwscript_opcode_audit.py` now
reads both corpora and expands the loops;
[mwscript_opcodes.md](../audits/mwscript_opcodes.md) is the current table.

### <a id="ported-is-not-wired"></a>🛑 Ported is not wired

**Code:** `tools/script/mwscript_opcode_audit.py:unwired`

Three commands read as ported while answering a default forever:

- `StartScript`/`StopScript` (1,104 and 695 call sites) flipped a flag and no
  code ran a global script.
- `GetDeadCount` (1,074) read a counter only a test fed.
- `GameHour`, `Day`, `Month`, `Year`, `DaysPassed`, `TimeScale` were declared
  and never written.

`--wiring` lists every `Hooks().x` the game never supplies and every
`DialogueState` method nothing outside the tests calls. Run against the
pre-fix sources it reports `AddDeath`; it reports nothing now. It cannot see a
flag that is written and read but acted on by nobody, which is what
`StartScript` was.

### <a id="global-scripts"></a>Global scripts tick

**Code:** `plugin/object_script.cpp:RunGlobalScripts`, `plugin/object_tick.cpp`

A running global script is an `ObjectScript` with no placement: its locals
live under the SCRIPT's name, which is how dialogue reads `ScriptName.var`, and
its bare commands act on the target `StartScript` named (the speaker, for the
bare form). They run after the local scripts, once per tick. The running set
and each target are in the co-save (`S` rows), so a timer survives a save.

NOT done: TES3 start scripts (`SSCR`) are not exported, so nothing starts at
new game.

### <a id="the-clock"></a>The clock globals are Skyrim's

**Code:** `plugin/game_calls.cpp:SyncClock`

Each tick copies Skyrim.esm's `GameHour` (0x38), `GameDay` (0x37), `GameMonth`
(0x36), `GameYear` (0x35), `GameDaysPassed` (0x39) and `TimeScale` (0x3A) into
the TES3 globals of the same meaning. The value is the float at `+0x34`, which
is all `GlobalVariable.GetValue` (0x9c2b30) reads. Month is 0-based and Day
1-based in both games. The year is Skyrim's.

### <a id="run-on-game-thread"></a>A write a script reads back runs NOW

**Code:** `plugin/main_thread.cpp:RunOnGameThread`

Object scripts tick on the game thread, so a POSTED `SetPos` landed a frame
after the `GetPos` that followed it. `RunOnGameThread` runs at once when the
caller is already on the game thread (learned from the first task the game
runs) and posts otherwise. It carries the writes with a getter: enable, lock,
the dynamic stats, equip, position, move, rotate, scale, and `AddItem` /
`RemoveItem`, which were called DIRECTLY from whatever thread the menu was on.
Everything that opens a menu, stages a quest, deletes, spawns or fills an
alias stays posted on purpose.

### <a id="one-queued-tick"></a>At most one tick is queued

The tick thread posts only while no posted tick is waiting. Alt-tabbing stops
the task pump, and an unconditional post queued 30 ticks a second that all ran
at once on return.

### <a id="a-script-acts-on-its-own-reference"></a>🛑 A script's own id is the reference RUNNING it

**Code:** `plugin/game_calls.cpp:OwnerRef`

A bare command names its target by BASE id, and `refs_formid.txt` holds one
placement per id -- so every copy of a base placed many times (one is placed
116 times) acted on the same reference. While an instance runs, its own base
id resolves to its runtime FormID.

### <a id="reference-index-unlocked"></a>The reference index is what unblocked the object commands

`refs_formid.txt` ([placed references](#placed-references)) was the one missing piece
under a whole tier of commands, because almost every one of them names a
reference rather than a base record. Porting it plus the commands behind it
moved the stubbed total from 20,489 call sites to **16,453**:

| Command | Calls |
|---|---:|
| `Enable` / `Disable` / `GetDisabled` | 6,031 |
| `StartCombat` / `StopCombat` | 1,764 |
| `MenuMode` | 1,111 |
| `GetDistance` | 782 |
| `Unlock` / `Lock` / `GetLocked` | 608 |
| `ForceGreeting` | 579 |
| `Activate` | 504 |
| `GetPCCell` / `GetInterior` | 403 |
| `GetRace` | 238 |
| `SetDelete` | 133 |
| `Equip` | 127 |
| Health / Magicka / Fatigue `Get`/`Set`/`Mod`/`ModCurrent` | — |

What remains is dominated by commands that need a TICK rather than a
reference — `GetSecondsPassed`, `CellChanged`, `OnDeath`, `OnActivate`, and
the `GetPos`/`SetPos`/`Rotate`/`MoveWorld` family — which is
[the object-script plan](../plans/morrowind_object_scripts.md).

### <a id="placed-references"></a>`id->Command` resolves through a placement table

**Code:** `morrowind_sidecar.py:_ref_lines`, `refs_formid.txt`, `plugin/game_calls.cpp`

`Disable`, `StartCombat` and the Transformation commands act on a PLACED
reference named by its base id — `"TR_m3_Yak gro-Yam"->Enable`. The runtime
already mapped FormID→id for speakers (so a click finds the NPC); this is the
other direction, and it needs its own table because a base record is not a
thing in the world.

`refs_formid.txt` is `id=Plugin|FormID` where the id is the BASE record's EditorID
and the FormID is the PLACEMENT's, resolved through the running load order by
`Game.GetFormFromFile` exactly as `items_formid.txt` and `quests_formid.txt` are.

🛑 **First placement wins, and that is very nearly unambiguous.** OpenMW's
`searchPtr` tries active cells first, then every cell, taking the first match
in each — and it searches exteriors in REVERSE, with a comment naming the
vanilla `chargen_plank` that is placed twice. We cannot replicate
cell-activity ordering because Skyrim owns which cells are loaded, so the
question is how much that costs. Measured over Tamriel Rebuilt, counting only
the ids result scripts actually target:

| Placements | ids | call sites |
|---|---:|---:|
| exactly one | 870 | 2,835 |
| several | 2 | 2 |
| none | 14 | 7,469 |

So the tie-break decides **2 call sites**, and first-match is right. The
"none" row is `player` (7,442 sites, answered by `OwnerRef` and never in this
table) plus 13 ids from Morrowind proper or authored typos (`agronian guy`)
that this plugin does not place — those correctly report and do nothing.

### <a id="positioncell-needs-an-anchor"></a>`PositionCell` aims at a REFERENCE, not a cell

**Code:** `morrowind_sidecar.py:_cell_lines`, `cells_formid.txt`,
`plugin/game_calls.cpp:MoveRefToCell`

`PositionCell x y z zRot "cell"` is the most-called stubbed command (1,232
sites). Skyrim's mover is `ObjectReference.MoveTo(target)` — it crosses cells
freely but takes another REFERENCE, never a cell. Disassembling the native
(`0x9cec80`) shows why: it builds a queued move task holding the TARGET's cell
handle, so the cell is only ever reached through something standing in it.

`Cell.GetNthRef` looked like the way to find that something at runtime, and is
not: on an UNLOADED cell it returns only persistent references, and a TES3
interior generally has none. So the anchor is staged instead —
`cells_formid.txt` maps the cell's authored name to any one reference the cell
contains, and the runtime does `MoveTo(anchor)` then `SetPosition(x, y, z)`.

🛑 **No marker record is minted, because the measurement said none was
needed.** Over Tamriel Rebuilt's two corpora, of the cells its scripts name:

| Named cells | Resolved in `CELL.txt` | ...holding a placement |
|---:|---:|---:|
| 379 | 332 | **331** |

That is 677 of 678 resolvable call sites served by a reference that already
exists. The 47 unresolved names are exterior REGIONS (where `PositionCell`
means the worldspace, and vanilla Morrowind falls back to the exterior) plus
cells belonging to masters this plugin does not re-export.

Both the `EditorID` and the `FULL` name are staged as keys: an interior
repeats its own name in both, and an exterior's `FULL` is its region, which is
the name a script uses for it.

### <a id="forceactive-is-a-weather-call"></a>🛑 `ForceActive` is a WEATHER call: two natives share one name

**Code:** `plugin/game_calls.cpp:AiQuestForm`, `plugin/ids.h`

The first AI command crashed the game one frame later: `mov rcx,[rbx]` in
Address Library 26327, reached from `Sky` (26243 -> 26246), with the AI quest
on the stack and a mesh path where an object should be.

- 26246 reads `Sky+0x48` (the current weather), then `weather+0x8a0`, and 26327
  walks the array inside it. The "weather" was the AI QUEST, so `+0x8a0` was
  whatever heap followed it.
- `kQuestForceActive` (56773, 0x9ec1b0) loads the Sky singleton and tail-calls
  the force-weather routine. It is `Weather.ForceActive`. The CK wiki strikes
  `Quest.ForceActive` out; it does not exist.
- `kAliasClear` (55188, 0x99fd50) was `LocationAlias.Clear`. The reference form
  is 55286 (0x9a46f0).

🛑 **`papyrus_native_locate.py` finds a native by NAME, and names repeat across
scripts** (`Clear`, `ForceActive`, `IsRunning`). It now prints the script each
registration site names, and `stable_id_check.py --identity` checks every
`Native<>("Script.Function", id)` against it: replaying the two ids above
reports `Weather` and `LocationAlias` as the real owners, and the current
source reports 0.

The quest is started by `StartQuest`, the same call the journal uses.

🛑 **The aliases must be Optional (FNAM 0x02).** An alias with no fill type
that is not Optional fails the quest start, and `ReferenceAlias.Clear` tests
that same bit and refuses otherwise. Allow Reuse (0x08) lets the player sit in
several target aliases at once.

### <a id="forcerefto-must-be-posted"></a>`ForceRefTo` is POSTED, and one alias holds ONE actor

**Code:** `plugin/game_calls.cpp:RunAiPackage`

`ForceRefTo` re-evaluates the actor's packages synchronously and a result
script runs on the menu's callback thread, so the fills and clears are posted
like every other engine call. This was NOT the cause of the crash above; an
earlier version of this section said it was.

🛑 **One alias holds ONE reference, so each package kind gets a POOL of
slots.** A script that gave two actors `AiFollow` filled one `followActor`
twice and only the second followed. The import now writes 8 slots per kind
(`follow0`..`follow7`): an actor alias, a target alias, and a PACK aimed at
that slot's own aliases -- 80 aliases and 40 PACKs on the one quest, with
`slots=8` in `ai_aliases.txt`.

- A command first takes the actor out of EVERY slot it sits in, then fills
  the first empty slot of its kind, so a new command replaces the old one.
- Which slot is empty is read off the engine with
  `ReferenceAlias.GetReference` (55287, 0x9a4740), not tracked, so fills that
  came back with a loaded save count.
- A full pool logs `ai: no free <kind> slot` and the command is dropped.

### <a id="the-query-commands"></a>The query commands are one native each

**Code:** `plugin/script_ops_query.cpp`, `plugin/game_calls.cpp`

`GetLOS`, `GetDetected`, `GetTarget`, `GetWeaponDrawn`, `GetPCSneaking`,
`GetPCRunning`, `Resurrect`, `Drop`, `GetCurrentWeather`, `GetSquareRoot` and
`Fall` share one property: each is a single Skyrim native, so there is no
mechanism to explain and they live together rather than beside the commands
they resemble.

Three of them relate two actors, and the direction matters:

| TES3 | Skyrim | Direction |
|---|---|---|
| `x->GetLOS y` | `x.HasLOS(y)` | same |
| `x->GetDetected y` | `y.IsDetectedBy(x)` | **SWAPPED** |
| `x->GetTarget y` | `x.GetCombatTarget() == y` | a comparison, not a lookup |

🛑 **`GetDetected` swaps its arguments.** OpenMW's
`isActorDetected(actor, observer)` takes the command's TARGET as the observer
and its string argument as the actor, while Skyrim's
`self.IsDetectedBy(other)` asks whether SELF is detected. Getting this
backwards answers a different question and reads as a sneaking bug.

🛑 **`GetTarget` is a comparison.** It asks whether the actor's combat target
is one named reference, not what the target is, so the native's return value
is compared rather than returned.

`GetCurrentWeather` needs a mapping, not a cast. TES3 returns a weather index
(0 Clear, 1 Cloudy, 2 Foggy, 3 Overcast, 4 Rain, 5 Thunderstorm, 6 Ashstorm,
7 Blight, 8 Snow, 9 Blizzard, from `weather.cpp`'s own registration order);
Skyrim reports a CLASSIFICATION of -1..3 (none/pleasant/cloudy/rainy/snow).
Ash and blight have no Skyrim equivalent and answer Cloudy, the nearest thing
the classification can say.

`Fall` is a **no-op in OpenMW too** — its opcode body is empty — so it is
ported to stop the 44 call sites counting as unported, not to do anything.
`GetSquareRoot` touches no game at all.

`ChangeWeather` is NOT ported: it names a REGION, and the conversion has no
region equivalent to hand it.

### <a id="the-angle-getters-have-no-id"></a>🛑 The angle getters have NO stable id on a current build

**Code:** `plugin/game_calls.cpp:RefAngle`, `plugin/ids.h`

`ObjectReference.GetAngleX/Y/Z` are Address Library ids 56162-56164 on
**1.6.659** and **absent from 1.6.1170**, the build being played. Measured
against both versionlibs, and confirmed by the live log:

```
addresses: UNRESOLVED ObjectReference.GetAngleX (id 56162)
```

Each is a three-instruction leaf (`movss xmm0,[r8+off]; mulss xmm0,[180/pi];
ret`), small enough that the database stopped covering it. `Resolve` returns 0,
the hook is never called, and **every rotation silently reads 0** — `Rotate`,
`RotateWorld`, `PositionCell`'s zRot and `Face` all depend on the getters.

So the field is read directly: rotation x/y/z are floats at `+0x48/0x4c/0x50`
on `TESObjectREFR`, immediately before the position triple at `+0x54`, taken
from the getters' own disassembly.

🛑 **The field holds RADIANS; the natives return DEGREES.** The getters exist
only to multiply by 180/pi, and `SetAngle` takes degrees back — so a
get/set round trip through the natives needs no conversion, and reading the
field directly DOES.

🛑 **A versionlib check on new ids is not enough.** The pre-existing ids were
the broken ones, and a sweep that only asked about ids added this session
would not have found it. Check every id a change DEPENDS on, against the build
the user plays.

### <a id="move-and-rotate-are-rates"></a>`Move` and `Rotate` are RATES, and that set the tick rate

**Code:** `plugin/script_ops_move.cpp`, `plugin/object_tick.cpp`

OpenMW multiplies both by the frame duration (`transformationextensions.cpp`,
`OpMove` / `OpRotate`), so `rotate z -110` means **110 degrees per second** and
the authoring convention is to call it every frame from a `GameMode` block. The
runtime ticks at a fixed rate instead of per frame, so the factor here is
`TickDelta()` — authored motion then plays at its authored speed whatever the
frame rate.

🛑 **That is why the tick left 15 Hz.** `object_tick.h` already carried the
warning: at 15 Hz a rate command runs at half its authored speed unless it is
delta-scaled, and both are now scaled and the rate is 30 Hz. Measured over both
corpora — 326 `move`/`moveworld` sites and 86 `rotate`/`rotateworld` sites.

`MoveWorld` and `Move` differ properly: the world form adds along a world axis,
the plain form along the object's own, which is what `abMatchRotation` and a
rotated offset give. `RotateWorld` and `Rotate` are the SAME call here, because
Skyrim's `SetAngle` takes Euler degrees with no world-composed form. They agree
on any single axis; of the 17 scripts that rotate anything, **2** turn more than
one (`TR_m1_lud_cogspinner`, `TR_m7_HH_Alvynu_7_ShipSink_sc`) and are the only
places the approximation can show.

### <a id="ai-packages-are-real-packages"></a>The AI commands are real Skyrim packages

**Code:** `tes5_import/dialogue/ai_packages_morrowind.py`,
`plugin/script_ops_ai.cpp`, `plugin/game_calls.cpp`

Skyrim has **no Papyrus call that gives an actor a package**. A package is a
record the engine picks off a stack, and the stack is built from lists — on the
actor, or on a quest alias. The CK's own documented best practice is the alias:
put the packages on a quest alias's package list, point the alias at an actor
with `ForceRefTo`, and the engine runs them ranked by quest priority.
`ForceRefTo` re-evaluates the actor's packages by itself.

Vanilla does this at scale — measured over `references/Skyrim.esm`: **365 of
1,811 quests carry alias packages, 4,125 `ALPC` entries in total**, and **585
of 6,838 `PLDT` locations are alias-typed** (type 8).

So the import mints one quest per plugin, with two aliases per package kind
(the actor running it, and what it aims at) and one `PACK` instance per kind
hung off the actor's alias. Every package's location and target are
alias-typed, so ONE record serves every call site: the destination is whatever
reference the runtime dropped in the alias.

🛑 **A travel destination cannot be raw coordinates.** The `PLDT` enum
(`wbDefinitionsTES5.pas:3065`) offers reference, cell, object, keyword and
alias — there is no XYZ form. `AiTravel x y z` therefore spawns an XMarker at
the point and fills the destination alias with it, the same trick
[the cell anchor](#positioncell-needs-an-anchor) uses. The actor then WALKS
there, because it is a real travel package.

🛑 **`Actor.PathToReference` is not the alternative.** It is latent — it
suspends its caller until the path ends — and neither a script hook nor the
tick may block.

### <a id="ai-packages"></a>REVERTED: the hand-rolled package queue

A first attempt kept a package stack in the DLL and drove it from the object
tick, because no Papyrus call queues a package. That reasoning stopped one step
short: packages are RECORDS on an alias list, which is
[the mechanism above](#ai-packages-are-real-packages).

What it cost, kept because each is a live hazard if the queue ever returns:
`AiTravel` became a bare `SetPosition`, so the actor TELEPORTED in full view
instead of walking; `AiWander` only cleared a follow offset and never idled;
and `GetCurrentAiPackage` answered our own bookkeeping, which says what a
script last asked for rather than what the engine is running -- a different
question the moment a package is dropped.

`Actor.KeepOffsetFromActor` was the one good part (radii 256/384, not the
native's 5/20 -- the CK wiki notes the defaults make a follower run into its
target). The Follow PACK supersedes it: it handles doors, combat breaks and
repathing, which an offset does not.

### <a id="a-spawn-is-not-loaded-on-its-first-frame"></a>Unloading is a TRANSITION, not a state

**Code:** `plugin/object_tick.cpp`, `plugin/object_script.h`

The tick drops an instance whose `ObjectReference.Is3DLoaded()` is false, so a
script stops running when its object leaves the world. Written as a bare test of
the current state, that gate also fires on an object which has not loaded *yet*.

A spawn binds on the frame `PlaceAtMe` returns the reference, several frames
before its 3D exists. The first tick therefore read "not loaded" and unbound it
— and **nothing ever rebinds a spawn**: `BindSpawnedInstance` is called once, at
placement. A staged placement recovers when its cell reloads and a hook touches
it again; a spawned creature has no such path, so the unbind is permanent.

Measured 2026-09-18 on `TR_m3_OE_FG_q_VermaiScr` (Cursing Like a Witch): the
creature spawned and its script never ran a single tick — no combat, no
`doonce`, no `died`, and the quest stayed at stage 30. The run before the gate
shipped shows all four.

So the instance remembers whether it has ever been seen loaded
(`ObjectScript::WasLoaded`). Not-loaded-yet is skipped for that tick and kept
bound; only a false *after* a true is an unload and unbinds. The regression
test is the three-phase sequence: never-loaded survives, loaded runs, then
unloaded drops.

🛑 The earlier theory here — that `Game.GetForm` cannot resolve a `0xFF`
reference because the CK wiki says it retrieves neither a temporary nor an id
with the MSB set — is WRONG for this case, and a fix built on it did not work.
`GetForm` (ID 55566) does resolve these: the `spawn:FF0017D8|0017D8.doonce`
measurement in
[the plan](../plans/morrowind_object_scripts.md#spawned-refs-need-getform) is a
spawned body running through exactly that lookup.

## <a id="journal-quests"></a>The journal is Skyrim quests

**Code:** `tes5_import/dialogue/quest_morrowind.py`, `plugin/game_calls.cpp`

Each TES3 Journal topic becomes one QUST: a stage per journal index, the page
as the stage's log entry, the `QuestStatus=Name` page as FULL, `Finished` as
the completes-quest bit. No objectives. `quests_formid.txt` maps the authored id to
`Plugin|FormID`; `Journal` and `SetJournalIndex` call the
`Quest.SetCurrentStageID` native. `AddJournalEntry` stages the ENTRY's index
even when the quest's own index does not rise — a lower page added late is
still a new page.

🛑 The quests are generated for every journal topic in the MERGED sidecar,
masters' included, into the plugin being imported. Two TES3 plugins sharing a
master would each mint that master's quests.

### <a id="game-calls"></a>Natives, found at their registrations

`lea rdx, ["SetCurrentStageID"]` is followed by `lea rax, [callback]`; the
callback inverts to a stable id.

| Native | 1.6.659 | id |
|---|---|---|
| `Quest.SetCurrentStageID` | `0x9e7f90` | 56684 |
| `ObjectReference.AddItem` | `0x9cd4b0` | 56145 |
| `ObjectReference.RemoveItem` | `0x9d0b30` | 56218 |
| `ObjectReference.GetItemCount` | `0x9ce530` | 56173 |
| `Game.GetPlayer` | `0x9adf00` | 55469 |

The NPC's display name is `TESFullName` at `TESNPC+0xd8` (string at `+0xe0`),
read off the destructor's vtable writes; the player's is form `0x7`.

### <a id="co-save"></a>The co-save

One SKSE record `MWST` v1 holding `DialogueState::Serialize()`: a format line
then one tab-separated record per line (`J` journal, `E` entry, `D`
disposition, `G` global, `L` local, `F` faction, `X` reaction, `S` running
script, `R`, `C`). An unknown line is skipped; a foreign header is refused.

The `D` line is the NPC's BASE disposition, which is what `ModDisposition`,
`SetDisposition` and `GetDisposition` (opcodes `Stats::opcode*Disposition`,
bare and explicit) read and write. A persuasion's TEMPORARY change lives only
inside the open conversation and is folded into the base when it ends (below),
so a save never carries it.

## <a id="npc-stats"></a>The speaker's stats: `NPC_.txt` carries what OpenMW derives

**Code:** `tes5_import/dialogue/morrowind_autocalc.py`,
`morrowind_sidecar_source.py`, `plugin/script_tables.cpp`

Persuasion reads the speaker's Personality, Luck, Speechcraft, Mercantile,
level, reputation and fatigue; barter needs its service flags. The 52-byte
NPDT authors them, but most NPCs carry the 12-byte autocalc form and the
numbers exist only once `MWClass::Npc::autoCalculateAttributes/Skills` has
run over the RACE, CLAS and SKIL records. That port runs at import, so the
actor line grows to

```
id=race|class|faction|rank|disposition|female|name|level|reputation|personality|luck|speechcraft|mercantile|services|gold
```

with `services` from AIDT, or from the CLASS when the NPC is autocalc, as
`Npc::getServices` chooses. Two more tables ride beside it: `GMST.txt`, every
GMST of the chain as `name=type,value` (`s`/`i`/`f`), because the persuasion
formula is nine GMSTs deep and none may be guessed; and `SKIL.txt`, the SKIL
rows `index=attribute|specialization|use0,use1,use2,use3`, for the skill-use
credit a persuasion pays.

The `player` NPC_ record -- Morrowind's own chargen actor -- is in the table
too, and it is where the PLAYER's Personality and Luck come from: Skyrim has
neither attribute, and that record is the only authored value a TES3 player
ever starts with. Speechcraft and Mercantile both read Skyrim's `Speechcraft`
actor value (the importer folds TES4 Mercantile onto it), level reads
`Actor.GetLevel`, and the fatigue term reads `GetActorValuePercentage("Stamina")`
for both sides.

## <a id="persuasion"></a>Persuasion is OpenMW's own formula

**Code:** `plugin/persuasion.cpp`, `plugin/conversation_persuasion.cpp`

`getPersuasionRatings` and `getPersuasionDispositionChange` are ported line
for line, with `roll0to99` as the one injected input so the headless gate can
pin every branch. The modal is `openmw_persuasion_dialog.layout` drawn from the
same art as the window -- Admire, Intimidate, Taunt, three bribes at the row
pitch, the gold label, Cancel -- with a bribe row disabled when the player
cannot pay it, as `PersuasionDialog::onOpen` does.

Disposition bookkeeping is `DialogueManager`'s: the conversation remembers the
base it opened on, applies each persuasion's TEMPORARY change to the base the
filter and the bar read, accumulates the PERMANENT part, and on goodbye writes
`clamp(original + permanent, 0, 100)` back. A script that moves disposition
mid-conversation resets the baseline (`updateOriginalDisposition`). A success
also moves gold, credits Speechcraft through `Game.AdvanceSkill` with the
SKIL use value, and Intimidate/Taunt shift the Fight and Flee settings. The
reply is the `Admire Success` / `Bribe Fail` topic under the `s<Topic>` GMST
title, delivered like any other topic so its result script runs.

## <a id="barter"></a>Barter is Skyrim's own menu

`Barter` is listed when the speaker's services include any item class, as
`DialogueWindow::updateTopics` lists it. Choosing it first asks `Service
Refusal` with the choice set to `Barter` (1) and the disposition test
INVERTED, as `checkServiceRefused` does; a refusal is delivered as a reply and
nothing opens. Otherwise the dialogue CLOSES and the `Actor.ShowBarterMenu`
native runs on the speaker from the game thread: the close is posted before
the barter open, so the two never stack. The importer already gives every
actor with services a vendor faction, so the menu shows their stock and gold.

OpenMW's per-trade disposition change (`applyBarterDispositionChange`) has no
hook inside Skyrim's menu and is not applied.

| Native | 1.6.659 | id |
|---|---|---|
| `Actor.ShowBarterMenu` | `0x98bef0` | 54765 |
| `Actor.GetLevel` | `0x996650` | 54927 |
| `Actor.GetActorValuePercentage` | `0x989740` | 54677 |
| `Game.AdvanceSkill` | `0x9ace40` | 55449 |

Each was found at its registration (`lea r9,[callback]; lea r8,"Actor"; lea
rdx,"<name>"`) and inverted through the Address Library.

## <a id="sound-opcodes"></a>The sound commands

TES3 scripts name a **SOUN id**; the record Skyrim plays is the **SNDR** the
import minted, which `SOUN.txt` maps (see
[tes5_import_sound.md](tes5_import_sound.md#the-runtime-sound-table)). Measured
over the Tamriel Rebuilt chain: 9 of the 11 sound commands are ported, covering
**1,536 call sites** — `playsound` 804, `playsound3d` 280, `getsoundplaying`
120, `playsoundvp` 97, `playsound3dvp` 85, `stopsound` 72, `playloopsound3dvp`
59, `playloopsound3d` 19.

| Native | 1.6.659 | id |
|---|---|---|
| `Sound.Play` | `0x9eaad0` | 56740 |
| `Sound.PlayAndWait` | `0x9eabf0` | 56741 |
| `Sound.StopInstance` | `0x9ead70` | 56742 |
| `Sound.SetInstanceVolume` | `0x9eadc0` | 56743 |

All four register against the class string `'Sound'` in one function at
`0x9eaeb0`, and each id exists in all 12 shipped versionlibs. `Play` and
`PlayAndWait` are MEMBER functions, so the SNDR form is `self`; the other two
are global and take a tag.

🛑 **`Sound.Play` RETURNS the playback instance id, and that id is the whole
mechanism.** Skyrim has no "is this instance playing" native, so `StopSound`
and `GetSoundPlaying` answer from what this session started, keyed by
`(reference, sound)` — `StopSound` stops what THIS reference started, and a
stopped sound is forgotten in the same step so it stops reporting as playing.
That matches the authored use: of 243 `GetSoundPlaying` sites, 157 test `== 0`
immediately before starting a loop.

🛑 **A `cXX` command is segment 5, not segment 3.** The segment is chosen by
whether the argument string holds a `/` (`Extensions::registerInstruction`),
and `X` is consumed by the COMPILER, which pushes nothing for it. Installing
`playsound`/`playloopsound3d`/`stopsound` with `Real3` left all three as
logging stubs while the `cff` forms worked, because the stub pass decodes a
segment-3 word as `(word >> 8) & 0x3ffff` and the opcode constants are far
larger than that field. The volume a script writes after a `cXX` command never
reaches the stack; only the `VP` forms carry one.

`say`, `saydone` and `streammusic` remain stubs: `Say` names a file under
`Sound\Vo\`, which is not converted yet
([the plan](../plans/morrowind_voice_tree.md)).

## <a id="licensing"></a>Licensing

OpenMW is **GPL-3.0**, vendored from 0.52.0 (`b4b1c5ae`). This follows the
pattern `external/pynifly_hkx/` already established: the project's own code is
MIT, everything under `external/` carries its own license.

It is a **submodule of TESRuntime, not part of its binary** — the same
arrangement as `havok_world_size`: its own source folder, its own DLL, built by
the parent's `build.bat` and shipped in the same `TESRuntime.zip`. That boundary
is what keeps the license contained. `TESRuntime.dll` and `TESGameBridge.dll`
never link OpenMW code, so they are unaffected; linking it into `TESRuntime.dll`
instead would make that whole binary GPL-3.0, `fire.cpp`/`guns.cpp`/`sever.cpp`/
`hud.cpp` included.

A few hundred lines (`skse_abi.h`, `log.*`, `json.*`) are **copied** rather than
shared with `tes_runtime/` for the same reason: a shared library linked into both
would put MIT and GPL code in one dependency graph.

# Morrowind runtime

**Code:** `tes_runtime/morrowind/`, `external/openmw/`, `tools/generators/vendor_openmw.py`

MorrowindRuntime: its own source folder and its own DLL, beside the other
runtimes under `tes_runtime/`, shipping in the same `TESRuntime.zip`. Journal
stage text, which used to live here, works on every quest and moved to
TESRuntime: [tes_runtime_journal.md](tes_runtime_journal.md#journal-stage-text).

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

Verified — `tes_runtime\morrowind\build.bat openmw` compiles **34 translation
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
tes_runtime\morrowind\build.bat test
tes_runtime\morrowind\store_test.exe "export\Tamriel Rebuilt 25.08.12"   # one export dir
tes_runtime\morrowind\store_test.exe --sidecar <staged root>             # the deployed layout
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

### <a id="wired-not-just-ported"></a>Ported is not wired

An opcode can be fully ported and still be dead: its hook is never set, or the
`DialogueState` it reads is never fed. Either way it answers with a default
forever while every audit that counts *ported* opcodes reads it as done, so the
gap is invisible from the port side alone.

`mwscript_opcode_audit.py --wiring` reports all four halves — hooks used but
never supplied, `DialogueState` methods nothing calls, setters writing state
nothing acts on, and [event flags nothing raises](#engine-written-locals).

## <a id="the-filter"></a>The filter

`plugin/filter.{h,cpp}` ports the TES3 selection rules onto `ActorView`. The
rules are not reinterpreted — the answer has to match what OpenMW would choose
for the same state. Semantics worth stating because they are easy to get
backwards:

| Rule | Behaviour |
|---|---|
| order | **First match wins**, never best match. OpenMW's `InfoOrder` over PNAM is precedence. |
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
restricted to topics the actor can actually answer.

Keywords are found by the vendored OpenMW `MWDialogue::KeywordSearch`, never a
hand-written matcher. A topic needs a separator (`\n \r \t ' " ( [`) only
BEFORE it; its end may fall mid-word, so `cave rat` matches inside "cave rats"
(the Fighters Guild's first contract) and `Shal` inside "shall". Overlaps go to
the longest. Learning runs `DialogueManager::addTopicsFromText`'s search over
every visible dialogue after the result script; the window seeds its own from
the topic list and re-links the whole history each redraw, as
`DialogueWindow::updateTopics` does. A hand-written whole-word check here once
missed 6,177 topic mentions across 352 Morrowind.esm topics.

`session_test.exe` runs all of it headless against a real export:

```bash
tes_runtime\morrowind\session_test.exe <sidecar root> TR_m4_Shei
tes_runtime\morrowind\session_test.exe <sidecar root> TR_m7_Felms --faction Temple --rank 3
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

That includes the built movie: it embeds the composed art, so it is never
committed either. `tools/release/package_runtime_dll.py` composes it from the
player's own install when `TESRuntime.zip` is packaged and writes it straight
into the archive (no install, no menu); the generator's default output folder,
`tes_runtime/morrowind/interface/`, is git-ignored.
`tests/test_morrowind_menu_art.py` checks git tracks nothing there, because a
file-type check cannot see art inside a `.swf` (a built copy was once committed
that way, 9 embedded bitmaps). The GUI's Package SKSE Mod stamp includes
whether a Morrowind install is registered, so registering one re-arms it.

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

**Code:** `tes_runtime/morrowind/plugin/activation.cpp`,
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

**A sidecar folder whose plugin is not loaded is skipped whole**
(`SidecarPluginLoaded`, asked once per folder). The proof is one of the
folder's own base records failing to resolve: an actor from `NPC__index.txt`,
else the first row of the quest, base, item, faction or GLOB tables
that names the folder's OWN file. A row naming a master proves nothing and is
passed over. The check once read the actor index alone, so a folder without one
(an incomplete sidecar, a leftover Oblivion folder from an old release) counted
as loaded. Its staged placements each then cost a failing `GetFormFromFile`,
which the engine reports to the Papyrus log. A folder that names no record of
its own still counts as loaded, since nothing can rule it out.

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
mov   byte  [rbx + 0x18], 0xa   ; depth            -> 0x18
mov   dword [rbx + 0x1c], 0x11  ; flags            -> 0x1C, not 0x20
mov   dword [rbx + 0x20], 1     ; input context    -> 0x20
call  0xc4d690                  ; IsGamepadEnabled (id 68622)
test  al, al
jne   skip
or    dword [rbx + 0x1c], 0x404 ; |= UsesCursor | UpdateUsesCursor
```

| Offset | Field | Value a plain modal panel uses |
|---|---|---|
| `0x10` | `view` | filled by `LoadMovie` |
| `0x18` | depth (a byte) | `0xA` |
| `0x1C` | **`flags`** | `0x11` = `kPausesGame \| kModal`, `\| 0x404` for the cursor |
| `0x20` | input context | `1` (MenuMode) |

These two were first named the other way round. The 1.6.1170 dispatcher
settles it: it orders the menu stack by the `0x18` byte and picks the "top"
menu by comparing it (`movzx eax, byte [rdx+0x18]` at `0xfa3f06`), and IMenu's
own constructor (`0xfaecc0`) defaults it to 3 and `0x20` to `0x13`, the
no-context value the close path skips (`0xfa43ca`). See the
[perks button](#perks-button) for what the depth decides.

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

### <a id="a-corpse-is-looted-not-talked-to"></a>A corpse is looted, not talked to

`ActivateHook` claims an activation from the BASE form's identity —
`IsMorrowindSpeaker(baseId)`, one bit on the plugin index plus a map lookup. A
base form has no life state, so a corpse matched exactly as well as a living
NPC, and the `return true` that suppresses Skyrim's own `Activate` also
suppressed the loot container. Activating any dead Morrowind speaker opened the
dialogue menu.

The guard has to read the REFERENCE, because that is where life lives and one
base serves every copy of that NPC.

🛑 **It cannot go through `Hooks().isDead`.** That is `IsDeadRef`, which takes a
RUNTIME FormID and resolves it back to a pointer through `Game.GetForm` — a
Papyrus VM call. `PollDeath` may do that because the object tick runs at a safe
point on the game thread; `ActivateHook` runs INSIDE the engine's own `Activate`
vtable dispatch, and re-entering the VM there answered for nobody. Measured: the
first build of this guard suppressed the menu for every speaker, alive or dead,
with no `activation:` line logged at all.

Call the native directly on the pointer the hook already holds. `Actor.IsDead`
(id 54705) needs neither the VM nor the stack — it is a four-instruction thunk
that tail-calls the reference's own virtual:

```asm
mov rax, qword ptr [r8]       ; r8 = the reference; rax = its vtable
mov dl, 1                     ; the actor flag SKSE's header documents
mov rcx, r8                   ; this = the reference
jmp qword ptr [rax + 0x4c8]   ; IsDead's slot
```

Byte-identical at 1.6.659 (`0x989ff0`) and 1.6.1170 (`0x9e8ca0`), so passing a
null VM and a zero stack is not a trick — those arguments are never read.

Unknown counts as ALIVE. A hook that cannot reach the VM keeps the dialogue it
has always opened rather than silently losing every speaker.

This is Morrowind's own rule, not a Skyrim convenience: OpenMW's
`Npc::activate` short-circuits on `stats.isDead()` into `ActionOpen` and only
reaches `ActionTalk` in the live-actor `else` branch (`mwclass/npc.cpp:846`,
same shape in `creature.cpp:451`).

### <a id="onactivate-claims-the-activation"></a>Reading `OnActivate` claims the activation

Confirmed in-game 2026-09-24. Before this, a Morrowind speaker opened dialogue
in combat and throughout vanilla `Morrowind.esm` chargen. The hook opened the
menu for any living speaker once `playercontrols` was on, and `CharGenWalkNPC`
turns controls back on at state 40, so the player could talk to Jiub and every
boat and dock guard.

**Vanilla makes those NPCs mute through their scripts, not through the control
switch.** Seven of the eight chargen NPCs run a script that reads `OnActivate`.
Jiub (`CharGenNameNPC`), the three boat guards and the dock guard
(`CharGenRaceNPC`) all open with `if ( OnActivate == 1 ) return`. The class
officer (`CharGenClassNPC`) plays his speech on `OnActivate`, and only at state
−1 calls `Activate` to let you talk. The captain's script never reads it, so he
talks.

That works through OpenMW's `RefData` flags (`mwworld/refdata.cpp`):

- `onActivate()`, the `OnActivate` opcode, sets `Flag_SuppressActivate` the
  first time a script reads it.
- `activate()` (from `_runStandardActivationAction`) refuses while that flag is
  set, and buffers an `OnActivate` for the script instead.
- `activateByScript()`, the `Activate` opcode, clears the flag, so the
  script's own `Activate` performs the default action. The flag is not saved.

The runtime mirrors it:

- `TakeEvent` (`script_ops_events.cpp`) calls `ObjectScript::ClaimActivation`
  whenever `OnActivate` is read.
- `ActivateHook` checks first. While the placement is claimed, it only raises
  `OnActivate` and returns true.
- The runtime's `Activate` wraps `ObjectReference.Activate` in a
  `ScriptActivationScope`. That call reaches `ActivateRef` (0x2eac20) directly
  (id 56139 is a nine-instruction wrapper), so the hook sees the mark before
  the scope ends. The scope also lets a script's activation past the
  `playercontrols` gate, as OpenMW's `executeActivation` does.
- 🛑 The claim applies only to the **player's** activation. Skyrim NPCs
  activate every door they path through, and TES3 has nothing like that. If an
  NPC's activation were claimed, a door's `if ( OnActivate == 1 )` would fire
  for that NPC, and the script's `Activate` would then act on the player.

The rest of OpenMW's `Npc::activate`, in its order, for a living speaker:

| Case | OpenMW | Here |
|---|---|---|
| Combat target is the player | `FailedAction("#{sActorInCombat}")` | Notification with the `sActorInCombat` GMST, claimed |
| Knocked down, not fighting the player | `ActionOpen` | Container menu opener (id 51140), mode 0, the loot mode `TESNPC::Activate` itself uses |
| Player sneaking, not being fought | `ActionOpen` (pickpocket) | Falls through to Skyrim's `TESNPC::Activate`, which pickpockets |
| Otherwise | `ActionTalk` | The Morrowind conversation |

`GetCombatTarget` (id 54680) and `IsSneaking` (id 54953) read only r8 on
1.6.1170, as `IsDead` does, so they are safe to call inside the vtable call.
Down means knock state ≠ 0 or life state 3 (unconscious), read from
actorState1 at `actor+0xC8`. `IsBleedingOut` masks `0x1E00000` there, which
places the life state at bits 21–24; the knock state is the three bits above.

Open gaps:

- Looting a knocked-down NPC is not theft here. OpenMW records a crime if you
  are seen, but no engine caller opens an actor's inventory in steal mode.
- Werewolf refusals are skipped, because the runtime never models TES3
  werewolf state (`Fn_Werewolf` always answers 0).

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

### <a id="the-patch-stages-a-sidecar"></a>🛑 The patch stages a sidecar although it has no TES3 dialogue

The staging gate used to be "this plugin exported `MWDI.txt`/`MWIN.txt`". The
compatibility patch exports neither — its dialogue is already converted
DIAL/INFO — so it staged nothing, and **the 143 globals and 1,204 script
bodies only it carries reached the runtime nowhere.**

The gate is therefore dialogue **or** `GLOB.txt`. The patch is the sole source
of vanilla Morrowind's globals in Morroblivion mode: Morroblivion itself is an
Oblivion plugin holding no TES3 data, so its same-named GLOB is an Oblivion
record the Morrowind runtime never reads.

A global no sidecar carries does not read as 0 — it makes the filter IGNORE
the condition testing it, matching OpenMW's `filter.cpp`, which returns true
when `getGlobalVariableType == ' '`. So `Greeting 0` ordinal 25 —
"The armor you wear is sacred to our Order" — passed its only condition for
every player, and Ordinators set fight 100 and attacked on sight. A `set`
cannot repair it either: the compiler's `getGlobalType` returns `' '` for an
unknown global, so `OrdinatorUniform` fails to compile.

### <a id="master-placements"></a>🛑 A placement belongs to the plugin that PLACES it

**Code:** `master_scripted`, `rebased_formid`, `runtime_masters` in
`tes5_import/dialogue/morrowind_placements.py`; `_scripted_bases`,
`_script_tables` in `morrowind_sidecar.py`.

A master stages its own script *bodies*, but not the references other plugins
make to its objects. `SCPT_instances` used to list only references whose base
the plugin itself defines, so a Tamriel Rebuilt cell holding a Tamriel_Data
or patch banner ran no script: the runtime never knew the reference was
there. Each plugin now also stages its own placements of the scripted bases
its MWScript masters define. TR gained 10,232 instance rows (15,546 → 25,778:
5,467 on compat-patch bases, 4,380 on Tamriel_Data bases, 385 on TR's own) and
385 `SCPT_objects` rows; Tamriel_Data gained 39; nothing was removed.

None of them is on a `Morrowind_ob.esm` base. A Morroblivion master's scripts
are Oblivion script, converted to Papyrus, so `runtime_masters` skips it;
staging its objects here would run a second script on them. The patch's
object scripts are authored MWScript (its export ships no Papyrus for them),
and Tamriel_Data and TR have no script stage, so every new row gives an
object the only script it has.

The same pass fixed a name collision in `_script_tables`. The first export to
declare a script name claimed it, even when this plugin could not spell that
export's FormID. The authored `Morrowind.esm` export claimed `OutsideBanner`,
so the patch's copy (the one the banners name) was never mapped. A dir now
claims a name only when its FormID rebases into this plugin's master list.

### <a id="short-globals-truncate"></a>🛑 A `short`/`long` global TRUNCATES its FLTV

TES3 stores every global's value as a float whatever its FNAM type, and
vanilla leaves junk in several. `WearingOrdinatorUni` holds `7.1e-31` and
`wearingHelmHHDA` holds `2.29e+20` — authored bytes, reproduced faithfully by
the export.

The engine does not read them as floats. OpenMW's `readESMVariantValue` runs a
`short`/`long` global's FLTV through `floatCast`, which truncates toward zero
and clamps NaN or anything outside int32 to int32's lowest. `_global_value`
matches that, so the sidecar writes `WearingOrdinatorUni=s,0`. Writing the raw
float instead would leave the global nonzero at load.

### 🛑 The root comes from the GAME EXE, not this module

`PluginsDir()` (`tes_runtime/common/paths.cpp`) is the game exe's folder plus
`Data\SKSE\Plugins\`; `SidecarDir()` appends the runtime's own name. Every
runtime DLL and the Address Library lookup share it.

Asking the DLL for its own path (`GetModuleHandleEx(FROM_ADDRESS)`) breaks
under Mod Organizer 2: the DLL's real path is `MO2\mods\<its mod>\SKSE\Plugins\`,
and MO2 merges mods only under the game's `Data` folder. A search of the mod's
real folder sees only that one mod's files, so sidecars shipped with the
converted plugin's mod are invisible. The runtime split shipped that way, and
CreatureRuntime logged `compose: 0 fragment(s) under
...\MO2\mods\TESRuntime\SKSE\Plugins\CreatureRuntime\animation` with
`Oblivion.json` installed in another mod. The failure is silent:
`0 sidecar(s)`, no dialogue, no converted creature animations.

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

`tes5_import/dialogue/morrowind_sidecar_source.py` builds each sidecar's
dialogue and actor table from the plugin's own TES3 BINARY; the runtime merges
every loaded plugin's responses with the vendored OpenMW `InfoOrder`
([per-owner staging](../plans/morrowind_object_scripts.md#cumulative-gather-must-go)).
Measured on TR_Mainland: 69,270 responses alone, **106,958** merged over
Morrowind, Tribunal, Bloodmoon and Tamriel_Data. "join the Fighters
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

### <a id="opcode-test-plan"></a>The fewest quests that exercise every opcode

**Tool:** `python -m tools.dialog.morrowind_opcode_testplan --plugin <esm> --stubs`

`morrowind_quest_trace` answers "can this quest finish?". The inverse question
is the play-testing one: **which quests must be run to see every opcode fire
at least once?** That is a set-cover over quests, because a quest exercises the
union of every command its stage setters call — its INFO result scripts and its
actors' object scripts alike.

**The cost of a quest is its STAGE COUNT, not one "run".** A 30-stage quest is
far longer to play than a 5-stage one, so the greedy rank is *new commands per
stage*. Ranking per run put the longest quests first, since a long quest
naturally touches more commands.

**A prerequisite is neither free nor forbidden.** Its stages are added to the
cost and its own commands to the gain, so a chain that tests plenty on the way
in competes fairly with a short standalone quest, and every quest the plan
implies appears as its own row.

🛑 **A quest with no `coc` target is never picked** — a row the tester cannot
travel to is not a test. Resolving one takes the object as well as the actor:
a quest advanced only by an object script is placed by reversing the sidecar's
`SCPT_objects.txt` to find the object the script sits on, then that object's
REFR placement, because the converted export drops the base record's script
link and nothing else says which boulder a boulder script is on. That, plus
scanning REFR alongside ACHR/ACRE, took the plan's untargeted rows from 11 to
0. A prerequisite pulled in by a chain is exempt: it is reached by playing the
questline, not by console.

🛑 **Greedy commits irrevocably, so the result needs a backward PRUNE.** It
buys dozens of one-command quests before meeting a chain that covers them all
at once; measured on TR, one chain covered 195 of the 202 reachable commands
and left **61 of the 65 earlier picks (328 stages) entirely redundant**.
Walking the finished cover backwards and dropping any pick that still
contributes nothing is what removes that duplicated work.

Which quest gates which is [its own problem](#prerequisites-are-conditions).

Two reads are **not** prerequisites and both produced quests listing
themselves. A script reading its OWN journal index is just asking "how far
along am I?", which every stage-guarded script does. And TR reuses one DISPLAY
NAME across a questline's parts — `Mages Guild: Mystic Erratum` is four
journal ids — so the self-check has to compare names as well as ids, or the
plan prints the same quest as its own prerequisite three times over.

🛑 **A command is counted from EVERY word on a line, not the leading one.** A
query is almost always an argument (`if ( GetHealth < 50 )`,
`player->AddItem`), so the leading-word scan `morrowind_quest_trace` uses to
find unported *statements* saw only 141 of the 326 ported commands here and
reported the other 185 as untestable. This is the same trap the audit
documents under [a quoted string is prose](#opcode-audit-strings).

Two covers are produced, because they answer different questions. The
**ported** cover proves the 326 implemented commands really work. The
**stubbed** cover is the opposite: it deliberately routes the user through the
45 commands that are registered but do nothing, so the log shows exactly which
silent no-op broke which stage. See [the audit](../audits/mwscript_opcodes.md#ported).

What the run's log has to carry for any of this to be diagnosable is
[the next section](#logging-names-the-script).

### <a id="prerequisites-are-conditions"></a>A prerequisite is a CONDITION, not a script read

🛑 **A TES3 quest states its prerequisite in its entry INFO's CONDITIONS,
where `VarType` is `J`** — "this line is only reachable once that quest has
reached that index". TR_Mainland has **51,825** such conditions. A result
script's `GetJournalIndex` says the same thing in script form and is also
read, but it is the RARE form and cannot be the only one consulted.

Reading only `GetJournalIndex` reported **Caught Off-Guard**
(`TR_m3_Bo_Burglar2`) as having no prerequisite, and the plan sent the player
to Relamus Saravyne — who offers *The Company We Keep*
(`TR_m3_Bo_Burglar1`) instead, exactly as an in-game check found. The 33
INFOs that set `Burglar2` read no journal at all in script.

The real chain is subtler than a journal condition alone, and is worth stating
because it generalises: Burglar2's entry INFO is on the topic **"cunning
plan"**, which no `AddTopic` ever grants. It is learned the way Morrowind
learns most topics — by being MENTIONED in a response — and the response that
mentions it is on `burglaries` at `Burglar1 = 100`, the predecessor's
COMPLETION stage. So the gate is "finish Burglar1, hear the phrase, get the
topic".

**Topic reachability, not just journal state, is what decides whether a quest
can be started**, so a plan that models only the journal will keep proposing
quests whose opening line the player can never see. Never infer a chain from a
name: TR's prefixes group by guild and questline, not order.

🛑 **Match a topic as WHOLE WORDS, and ignore one that too many quests teach.**
Morrowind topic names include bare words — `rat`, `good`, `little secret`. A
substring match made every quest whose response contains "good" a prerequisite
of *Krieps the Weak*, giving it **551** of them and a **7,047-stage** chain;
*Fighters Guild: More Rats?* collected 405 the same way off `rat`. Both
numbers are artifacts, and they dominated the cover until the match was fixed:
the plan spent its picks on ~50 one-command quests and then met a chain that
subsumed 61 of them. A topic taught by more than one quest is a common word,
not a gate.

🛑 **Count QUESTS, not speakers.** Filtering on how many speakers mention a
topic looks like the same rule and is not: the single response teaching
`cunning plan` shares its speaker with the rest of that questline, so a
speaker threshold of 1 silently dropped the Burglar1→Burglar2 gate — the case
this whole mechanism exists to find.

### <a id="equivalence-classes"></a>Commands that share a handler test as one

The runtime does not implement 328 commands 328 times. `Enable` and `Disable`
are one class, `OpSetEnabled<R, bool>`, differing by a template argument;
every attribute, skill and magic-effect command — 177 of them — is one
`OpStat` differing by a table row and a `Verb`. Where the class is the same
and only a constant differs, the second command runs no code the first did
not, so **one representative per class proves the class works**.

The class is read out of the `Real<...>` registrations rather than guessed
from names, because names mislead in both directions: `GetScale` and
`SetScale` look like a pair and are separate classes. `ModHealth` and
`ModCurrentHealth` share `OpModDynamic` but differ by its `Base` flag (the
first moves the maximum too, as OpenMW does), so each needs its own case.

🛑 **`OpStat` must be split further, and the split is not cosmetic.** It
branches on whether the stat maps to a Skyrim actor value: `SetBlock` writes
through `setActorValue` into the engine, while `SetPersonality` writes the
DLL's own number, because Skyrim has no personality. Those are different code
paths, and a plan that tested only one would claim coverage it does not have.
The verb splits too — `Get` only reads, so it proves nothing about a writer.
And a Skyrim-backed magic-effect stat writes through `modActorValue`, a third
path ([the stat commands](#stat-commands)).

🛑 **The representative must be one a quest can REACH.** Choosing
alphabetically picked commands no quest calls, stranding their whole class and
claiming coverage the plan never delivers.

Measured on TR_Mainland: 328 registered commands fold to **119
representatives**, and the plan drops from 104 quests / 834 stages to **54
quests / 347 stages** while still covering all 202 reachable commands. The
largest classes are `OpStat/write/skyrim` (27 commands),
`OpStat/read/skyrim` (16) and `OpStat/write/own` (14). `--every-command`
turns the folding off.

### <a id="logging-names-the-script"></a>A log line names the script it came from

A play-test log is only useful if an agent reading it afterwards can fix what
it reports, and three gaps made that impossible. All three are about CONTEXT,
not volume — the runtime already logged plenty, just nothing that said *where*.

**`Journal` logged nothing at all.** It is the one statement that advances a
quest, so without it no log can distinguish "the quest advanced" from "the
quest silently stalled" — the single most important line in a quest test.

**The unported-command warning fired once per command, globally**
(`++g_reported[name] == 1`), naming the command but not the script. The second
occurrence, in a different quest, was silent — so a log could say `say` did
nothing without saying which of TR's 184 `Say` call sites it was. It is now
deduplicated per *site* (command + script), and carries the script.

**Nothing recorded which result script ran.** A script that runs but takes no
branch produced no output, which reads exactly like one that never ran.

🛑 **The script name is a scoped global, not a parameter.** The interpreter
hands an opcode only its `Runtime`, so naming the script at the call site
would mean threading it through all 326 handlers. `RunningScript` is an RAII
guard that sets it for the duration of a run and restores the previous value,
because a result script can `StartScript` another one; scripts run one at a
time on the game thread.

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
| Fight | Aggression | 2 when OpenMW's `fight + (50 - disposition) * fFightDispMult + iFightDistanceBase >= 100` (the on-sight test at distance 0, GMSTs from `GMST.txt`), else 1, else 0 for Fight <= 5 as the import has it |
| Flee | Confidence | the import's tiers on `100 - flee`: >=100 4, >=70 3, >=40 2, >=15 1 |
| Alarm | Assistance, Morality | the import's Responsibility mapping: >=30 assists; morality >=80 3, >=50 2, >=30 1 |
| Hello | none | the number is kept for the filter only |

Aggression 2 attacks neutrals, which the player is; 1 attacks enemies only.
NOT in-game verified. `SetDisposition` re-applies Fight, because the on-sight
test reads both: `ModDisposition -100` provokes as surely as `SetFight 100`.

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

### <a id="opcode-audit-strings"></a>🛑 A quoted string is PROSE, not a call

The counter stripped `;` comments but not string literals, so any command whose
name is an ordinary English word scored every line of dialogue that used it.
`Help` — an OpenMW console command registered beside `ReloadLua` and
`ToggleRecastMesh`, which no Morrowind script can call — topped the unported
list on BOTH corpora, at 398 calls over TR_Mainland and 38 over Morrowind.esm,
every one of them prose like `Choice "I will help you." 1`. Stripping quoted
text drops the top stubs to their true counts (Morrowind.esm):

| command | counted | real |
|---|---|---|
| `Help` | 38 | **0** |
| `Show` | 11 | **0** |
| `Say` | 7 | **0** |
| `Ra` | 1 | **0** |
| `PayFineThief` | 10 | 10 |

Stubbed call sites fall from 73 to **16** on Morrowind.esm and from 1,706 to
**1,137** on TR_Mainland; `Say` alone was 292 of TR's and is really 184. The
real leader is `PayFineThief`. The lesson generalizes past this tool: the INFO
corpus is majority prose, so ANY word-frequency scan over `ResultScript` must
drop quoted text first or it measures the English language.

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

#### Three ways the install scan under-reported

Each of these made a command read as ported that was not, and each was found
by a command whose status was known independently:

1. **The opcode had to be the only argument.** `_INSTALL` ended at `\)`, so
   `Real<Op>(base + i, i, true)` — an install whose handler takes constructor
   arguments — matched nothing. It now ends at `[,)]`.
2. **The namespace was discarded.** Install sites write aliases (`C::`, `M::`)
   whose meaning is per-file, so the scan kept only the tail. But
   `opcodeEnable`, `opcodeDisable` and `opcodeGetDisabled` exist in **both**
   `Control` and `Misc` — the only three bases in the whole table that two
   domains declare. Installing `Misc::opcodeEnable` (object `Enable`) marked
   every `Control` switch ported. Each file's `namespace X = Compiler::Y;`
   now resolves the alias, and `AMBIGUOUS_BASES` qualifies just those three.
3. **A family was all-or-nothing.** The `+N` offset was stripped before
   matching, so installing `opcodeEnable + 0` marked all seven switches
   ported. A loop over a literal list — `for (int i : {kPlayerControls, ...})`
   — is now read as a PARTIAL family and recorded per member as `base+N`;
   a loop over `0..count` still records the base alone and covers the family
   whole. `_subset_offsets` **raises** rather than falling back if an
   enumerator in that list has no explicit `= <int>`, because a silent
   fallback reads the loop as whole and restores defect 3 unnoticed.

Measured: fixing all three moved exactly 9 commands (the control switches
Skyrim has no flag for) from ported to STUB, and nothing else.

### <a id="engine-written-locals"></a>🛑 The engine-written locals an opcode audit CANNOT see

**Code:** `plugin/object_script.cpp`, `plugin/equip.cpp`

`OnPCEquip`, `PCSkipEquip`, `OnPCAdd`, `OnPCDrop` and `OnPCHitMe` are **not
opcodes**. A script declares `short OnPCEquip` and reads the variable the
engine writes, so there is no registration and no call site — an opcode audit
scans for command invocations and cannot see any of them. `OnActivate` looks
like the same thing but IS a registered function, which is what made the
family read as covered.

Measured over TR_Mainland, Tamriel_Data and the patch: 152 scripts declare
`OnPCEquip`, 193 `OnPCHitMe`, 169 `PCSkipEquip`, 48 `OnPCAdd`, 7 `OnPCDrop`.

`--wiring` now reports any `ObjectEvents` flag no shipped file raises, which
is how this was found: four of seven flags were declared, read and cleared,
and nothing ever set them. The symptom was an Ordinator greeting whose only
condition is `WearingOrdinatorUni == 1` never firing, because the armor's
`OrdinatorUniform` script could not observe being equipped.

`OnPCEquip` is a poll, not a hook: `PollEquipped` asks `Actor.IsEquipped` once
per tick for each of the 147 carried objects whose script declares the local.
A hook would be needed only for `PCSkipEquip`, which lets a script REFUSE an
equip (143 scripts set it) — that is checked at the inventory-USE layer, not
inside `EquipObject`, and is still unimplemented. `kEquipObject` /
`kUnequipObject` are recorded in `ids.h` for when it is built.

🛑 **The id is a PARAMETER of the poll.** Locals belong to the SCRIPT, so one
instance serves every item record sharing it — `OrdinatorUniform` is worn by
three. Testing only the instance's own `mBaseId` asked about whichever record
was seen first (`T_De_AlmaRula_Helm_UNI`), so wearing the Necrom cuirass
answered 0 and the greeting never fired. The tick polls every record and runs
the body once, with `mWornId` stopping a sibling record from clearing the flag
another one set.

🛑 **`SetGlobal` and `SetVar` log only on a CHANGE.** An object script re-runs
its whole body every tick, so an unconditional line is ~30 writes a second of
a value that already held: measured, 2,300 lines of `wearingordinatoruni = 1`
from one equipped cuirass, burying the rest of the log. `SetGlobal` compares
against what a READER would get, since an absent entry falls back to the GLOB's
declared value.

### <a id="setatstart"></a>The authored placement: `SetAtStart` and its readers

**Code:** `plugin/script_ops_world.cpp:OpSetAtStart`, `script_tables.cpp:FindPlacement`

`SetAtStart` puts an object back where the CELL RECORD placed it, position and
rotation both -- OpenMW's `transformationextensions.cpp:OpSetAtStart` reads
`getCellRef().getPosition()`, not anything runtime. `GetStartingPos` and
`GetStartingAngle` read the same values without moving anything, which is how a
script measures how far its own mechanism has travelled.

The runtime had no authored placement at all, so `SCPT_instances.txt` gained a
fourth column: `x,y,z,rx,ry,rz`. 🛑 **The angles are written in DEGREES.** The
export stores the record's radians, `_placement` converts, and the SetAngle
hook takes degrees -- the getters convert the other way, so a raw radian would
be off by 57x with nothing to say so. A row from an older sidecar has no fourth
column and answers null rather than claiming the origin as its home.

`ResetActors` is the same restore over every LOADED placed actor, standing in
for OpenMW's sweep of the active cells. It reaches only SCRIPTED placements,
which is what the instance table holds -- measured, all 13 placed actors of the
two cells that call it are scripted, so the limit costs nothing on the real
corpus.

`fixme` joins `kDeliberateNoOps`: it nudges the PLAYER up to 128 units by
probing collision for a free spot, it is a console command, and no corpus calls
it.

`DontSaveObject` (5 sites) is a no-op too, and OpenMW's own implementation is
the reason: an empty body whose comment says the incompatibility from ignoring
it is marginal at most. It asks that an object be left out of the save, which
is not a request Skyrim's save format can carry.

`GetCurrentTime` reads the `gamehour` global rather than a number of its own.
That global is Skyrim's, refreshed by `SyncClock` every tick, so the hour this
answers cannot drift from the one a clock CONDITION sees.
See: [the clock globals](#the-clock).

Guarded by `script_test.cpp:PlacementCases`.

### <a id="npc-rank"></a>`RaiseRank` / `LowerRank`: the NPC's own rank

**Code:** `plugin/script_ops_stats.cpp:OpChangeRank`, `DialogueState::ActorRank`

These move the TARGET's rank, not the player's -- `PCRaiseRank` is the player's
and was already ported. The distinction is in the signature: `raiserank` takes
`x` (no argument) because an NPC_ record carries exactly ONE faction, so there
is no faction to name; `pcraiserank` takes `/S`, the faction.

Ported from OpenMW's `statsextensions.cpp:OpRaiseRank`, which is the contract:

- no faction on the record -> no-op;
- a rank already moved this session -> move it again from there;
- otherwise start from the record's AUTHORED rank and move from that.

`LowerRank` additionally stops at 0 rather than resigning the NPC.

The state is `mActorRank`, keyed by actor and falling back to `ActorDef::rank`
-- the same shape as `mDisposition`. The player needs no special case: it has
no NPC_ record, so `FindActor` answers null and the faction guard returns.

🛑 **`GameActor::PrimaryFactionRank` reads it**, which is what makes a
promotion visible: that is the one function the dialogue filter asks for a
`Rank` condition, so a script that promotes an NPC changes which lines it
offers. Reading `ActorDef::rank` directly there, as the first version did,
would have left the promotion invisible to the only thing that consumes it.

Persisted in the cosave as the `N` record; guarded by
`script_test.cpp:ActorRankCases`.

### <a id="messagebox-buttons"></a>`MessageBox` buttons and `GetButtonPressed`

**Code:** `plugin/game_calls_message.cpp`, `plugin/script_ops_world.cpp:OpGetButtonPressed`

`MessageBox` is a compiler BUILTIN (`opMessageBox`, `segment3(0, buttons)`),
so it never appears in `extensions0.cpp` and the opcode audit cannot see it.
The interpreter pops the text, then `arg0` button names, reverses them, runs
`formatMessage` for the `%d`/`%s` specifiers, and calls `Context::messageBox`.
Both our contexts took that call and DISCARDED the buttons into an unnamed
parameter, so all 131 `GetButtonPressed` call sites polled a value nothing
ever set.

**`Debug.MessageBox` cannot carry buttons.** It is documented single-button OK,
and the disassembly agrees: 0xa072f0 is a WRAPPER that passes the literal
`"OK"` and tail-calls 0x94b280, the real builder. It is stable id 52269, with
an identical prologue on 1.6.1170 (0x94b280) and 1.6.659 (0x8ec2f0).

🛑 **The buttons are VARIADIC arguments, not an array.** The signature is
`(text, callback, bool, kind, arg5, button...)` with a null after the last
name: argument 6 is the FIRST button's `const char*` -- the wrapper's
0x1ad18f0 is the bytes `"OK"` themselves, not a pointer to them -- and the
callee walks the stack upward from argument 7 (`[rbp+0x7f]`, which is
`rsp+0x30` at entry). Measured on a real two-button caller at 0x93cd51:
`[rsp+0x28]` holds `"Ok"` and `[rsp+0x30]` holds a SECOND string
("You cannot change shouts while shouting."), where a terminator would sit if
the argument were an array.

**Passing an array instead draws ONE garbled button**, because the engine reads
the pointer VALUES as text. That was the first shipped attempt; the log showed
the correct text and a correct `button = 0` readback, which is what localizes
the fault to the call rather than to the script or the poll.

`Message.Show` is NOT the mechanism here, for the reason already recorded for
the button-less case: `Show` needs an authored MESG per string and the corpus
passes arbitrary runtime text.

🛑 **The callback is a PLAIN FUNCTION POINTER, not an object.** The builder's
second argument looks like it needs an `IMessageBoxCallback`: non-null makes it
allocate 0x18 bytes, store the vtable 0x18fdab0 at +0, a refcount at +8 and the
argument at +0x10. But that adapter's own handler is five instructions --
`mov rax,[rcx+0x10] / test / movzx ecx,dl / jmp rax` -- so the engine wraps a
bare `void(*)(unsigned int button)` for us and tail-calls it with the clicked
index. Its vtable has exactly TWO slots (0x94cec0 destructor, 0x94cdd0
handler); slots past that read as string data, which is how a blind vtable dump
overstates it.

The click lands in `buttonPressed`, and `GetButtonPressed` hands it out ONCE.
That matters because the command is a POLL, not a wait: 131 of its 132 call
sites are `set <var> to GetButtonPressed` read from a per-frame body, so a
value left set would re-fire the branch on the next tick. Guarded by
`script_test.cpp:ButtonPressedCases`.

No locking: the engine reports the click on the main thread, and object scripts
tick on the main thread too (`main_thread.h:RunOnGameThread`), so the write and
the poll never race.

#### <a id="action-keys-in-text"></a>`^ActionForward` names the player's key

**Code:** `plugin/script_context.cpp:getActionBinding`, `plugin/menu.cpp:BoundKeyName`

OpenMW's `fixDefinesMsgBox` turns `^ActionForward` into `#{sForward}` and asks
the context's `getActionBinding` for the key; OpenMW's own context answers the
bound key's name, or "None". Ours returned the placeholder, so every chargen
tutorial read `#{sForward} and #{sBack} move forward and back`. Now each
binding maps to the Skyrim gameplay user event that does the same (`sForward`
Forward, `sUse` Right Attack/Block, `sInventory` Tween Menu, `sRestKey` Wait;
the weapon and spell switches are Favorites), and `BoundKeyName` reads the
player's key off the ControlMap (gameplay context at `+0x60`, keyboard then
mouse mappings; SKSE `GameInput.h`) and names it with `GetKeyNameTextA`.
Confirmed headless: the boat guard's tip reads "W and S move forward and back.
A and D move side to side".

#### <a id="book-text-defines"></a>Book text fills in `%PCName`

**Code:** `plugin/game_calls_message.cpp:OpenBookHook`

10 Morrowind.esm books carry `%PCName`, `%PCRace` or `%PCClass` (33 tokens):
the chargen papers, Caius's directions, the decoded package, the Nerevarine
notice and the stronghold deeds among them. OpenMW fills them as the book opens
(`fixDefinesBook`); Skyrim showed them raw. Every call to
`BookMenu::OpenBookMenu` (id 51053) is repointed at a wrapper that runs the
same `fixDefinesBook` with the player as the context, for a book owned by a
Morrowind plugin whose text has a `%`, and hands the engine a BSString over
the filled text. The function reads only that string's data pointer and copies
it into the menu's own, so a string on the wrapper's stack is enough. AE only:
the id is not in `pre_ae_ids.txt`, so SE 1.5.97 and VR show the raw text.

#### <a id="player-race-class-sex"></a>The player's race, class and sex are the game's

**Code:** `plugin/game_actor.cpp:PlayerRace`, `PlayerClass`, `PlayerIsFemale`;
`plugin/script_context.cpp:getPCRace`

All three were stubs answering empty and male, so the chargen papers read
"Race: / Class:", every `SameRace` condition failed, and a female player
passed male-gated lines. Now the race is the TES3 id the race table gives the
player's Skyrim race (the table the starting attributes read), the class is the
one chosen at chargen, and the sex is the engine's. `%PCRace` shows the race's
NAME, as OpenMW's `getPCRace` does. Morrowind.esm reach: 109 `SameRace`, 110
`SameSex` and 77 `PcGender` conditions, and 117 responses naming `%PCRace` or
`%PCClass`. Confirmed headless: the papers read "Race: Nord, Class: Warrior".

### <a id="vanilla-morrowind-chargen"></a>🛑 Vanilla Morrowind's opening is a GLOBAL, not a quest

**Code:** `plugin/game_calls_query.cpp:SyncMirroredGlobals`,
`tes5_import/dialogue/morrowind_sidecar.py:_global_lines`,
`TESGameSelect/scripts/source/TESGameSelectQuest.psc:BeginMorrowind`

Morrowind has no chargen quest — `quests_formid.txt` carries no row for one,
unlike Oblivion's `Charactergen` and Morroblivion's `fbmwChargen`. The whole
opening is object scripts on placed references, and the thing that sets them
running is one global.

The `Main` start script polls it, and its own comment names the mechanism:

```
;start character generation
if  ( CharGenState == 1 )    ;the game sets CharGenState to 1 when NEW GAME is selected
    StartScript Startup
    StartScript VampireCheck
    if ( ScriptRunning, CharGen == 0 )
        StartScript CharGen
    endif
endif
```

`CharGen` then does everything itself — disables controls, `Player->PositionCell
61,-135, 24, 340, "Imperial Prison Ship"`, sets the Bitter Coast weather, sets
`CharGenState` to 10 and stops itself. So there is **no marker to move the
player to and no stage to set**: placing the player is the opening's own job.
`CharGenDoorExitCaptain` ends chargen with `set CharGenState to -1`, which is
what the tutorial scripts test to fall silent.

The one thing missing under Skyrim is the sentence in that comment: the TES3
engine set `CharGenState` to 1, and Skyrim's engine never does. `Main` is
already running — start scripts start themselves on a new game
([start scripts](#global-scripts)) — so it is polling a global nothing ever
sets, and the opening simply never begins.

Verified against OpenMW, which does the same write from the same moment:
`World::startNewGame` runs `mGlobalVariables[Globals::sCharGenState].setInteger(1)`
for a normal new game and `-1` for `bypass`, the skip-chargen path that
`CharGenDoorExitCaptain` also writes when the census office is done
(`references/openmw/apps/openmw/mwworld/worldimp.cpp:289`). The name is
lowercase `"chargenstate"` (`mwworld/globals.hpp:47`), which is the key
`SetGlobal` lowercases to, and the value is read back with `getGlobalFloat`
(`mwinput/actionmanager.cpp:246`) — an ESM `Variant` converts between the two,
so a float-typed GLOB holding 1.0 is what OpenMW produces as well. Morrowind's
own `GLOB.txt` declares it `f`.

🛑 `CharGen`'s `ChangeWeather "Bitter Coast Region" 1` is UNPORTED, so it
stubs out and the opening starts under whatever weather is up. Only cosmetic:
`PositionCell` and `set CharGenState to 10` are on the following lines and
still run. OpenMW rebuilds its WeatherManager just before chargen for exactly
this call's sake, which is why the line exists at all.

`CharGenState` is a REAL GLOB in the converted plugin, so Papyrus sets it
directly -- `TESGameSelectQuest.BeginMorrowind` resolves it with
`GetFormFromFile` and calls `SetValue(1.0)`. There is no handshake, no message
and no runtime entry point.

The one piece the runtime owes: it keeps TES3 globals in its own map and would
never see that write. `SyncMirroredGlobals` reads the listed globals back out
of their converted GLOB each tick, beside `SyncClock`, which is why `GLOB.txt`
now carries `name=type,value,plugin|formid`. Only globals a script READS as an
input belong in `kMirrored`; a write-back would fight the object scripts, which
own every other global's value.

🛑 An earlier design had the runtime read the CHOICE from TESGameSelect.esp on
`kMessage_NewGame` and set `CharGenState` itself. Two reasons it could not
work, both measured 2026-09-21. `kMessage_NewGame` fires BEFORE the selector's
menu exists -- the runtime logged `chargen: selector chose game 0` at 11:40:08
and Papyrus logged `detected 3 game(s), mask 17` at 11:40:14, six seconds
later. And the runtime is SIDECAR-DRIVEN: it enumerates sidecar folders and
resolves each plugin's load-order index from a sample FormID inside that
sidecar, so TESGameSelect.esp -- which has no sidecar -- is outside its world
model entirely.

Verified against OpenMW, which does the same write from the same moment:
`World::startNewGame` runs `mGlobalVariables[Globals::sCharGenState].setInteger(1)`
for a normal new game and `-1` for `bypass`, the skip-chargen path that
`CharGenDoorExitCaptain` also writes when the census office is done
(`references/openmw/apps/openmw/mwworld/worldimp.cpp:289`). The name is
lowercase `"chargenstate"` (`mwworld/globals.hpp:47`), which is the key
`SetGlobal` lowercases to, and the value is read back with `getGlobalFloat`
(`mwinput/actionmanager.cpp:246`) — an ESM `Variant` converts between the two,
so a float-typed GLOB holding 1.0 is what OpenMW produces as well. Morrowind's
own `GLOB.txt` declares it `f`.

🛑 `CharGen`'s `ChangeWeather "Bitter Coast Region" 1` is UNPORTED, so it
stubs out and the opening starts under whatever weather is up. Only cosmetic:
`PositionCell` and `set CharGenState to 10` are on the following lines and
still run. OpenMW rebuilds its WeatherManager just before chargen for exactly
this call's sake, which is why the line exists at all.

🛑 `kMessage_NewGame` fires BEFORE the selector's menu exists — measured
2026-09-21, the runtime logged `chargen: selector chose game 0` at 11:40:08 and
Papyrus logged `detected 3 game(s), mask 17` at 11:40:14, six seconds later. So
the choice cannot be READ there. `ArmChargenWatch` only arms; `PollChargenChoice`
runs from the object tick (registered with `SetPreTick`, before the bodies, so
`Main` sees the write on the same tick) and re-reads the global until it is
non-zero. Zero cannot settle it: it is both the unset value and `GAME_SKYRIM`,
and waiting through it costs nothing, since choosing Skyrim leaves
`CharGenState` alone exactly as an unanswered watch does.

It reads TESGameSelect.esp's `TESGS_Chosen` global through `FormFromFile` and
`kOffGlobalValue` (the `SyncClock` pattern), and sets `CharGenState` to 1 only
when that names Morrowind. Reading a GLOB out of another plugin is what keeps
the gate one-directional: the runtime registers no Papyrus natives, so the
selector cannot call into it, but any plugin's globals are readable. An absent
TESGameSelect.esp resolves to null, which is the honest no-op — Morrowind
launched on its own is a new game whose only game IS Morrowind, so chargen runs.

### <a id="global-scripts"></a>Global scripts tick

**Code:** `plugin/object_script.cpp:RunGlobalScripts`, `plugin/object_tick.cpp`

A running global script is an `ObjectScript` with no placement: its locals
live under the SCRIPT's name, which is how dialogue reads `ScriptName.var`, and
its bare commands act on the target `StartScript` named (the speaker, for the
bare form). They run after the local scripts, once per tick. The running set
and each target are in the co-save (`S` rows), so a timer survives a save.

🛑 **Start scripts (`SSCR`) start by themselves, at new game AND on every
load** (`DialogueState::StartStartupScripts`, from the co-save's revert and
load callbacks). TES3 does the same, so a start script that stopped itself
runs again after a reload. `SSCR.txt` is read from the TES3 binary by the
sidecar's `gather`, not from the export, so no record type was added and no
FormID moved. It lists the LAST plugin's own SSCR only: a master stages its own
bodies, and a start script with no staged body cannot run. TR_Mainland's two
(`TR_ScStart_Installed`, `TR_NecMQ_MainScript`) both compile headlessly with
no unported command; the chain's other four belong to Tribunal, Bloodmoon and
Tamriel_Data.

### <a id="one-locals-key"></a>🛑 One locals key per reference

**Code:** `plugin/object_script.cpp:LocalsOwner`

Dialogue keyed a speaker's locals by its BASE id and the object script keyed
them by PLACEMENT, so `set knockedout to 1` in a result script and the NPC's
own script reading `knockedout` were two variables. Every reader and writer
now goes through `LocalsOwner(id)`:

1. the conversation's speaker, by the runtime FormID activation handed over,
   so a base placed many times resolves to the copy being spoken to;
2. the instance running right now, for a script naming its own id;
3. the placement `refs_formid.txt` holds for that id, when it runs a script;
4. otherwise the id itself -- a global script, or an actor with no script.

🛑 Locals a save already holds under a scripted NPC's base id are orphaned.

### <a id="the-clock"></a>The clock globals are Skyrim's

**Code:** `plugin/game_calls_query.cpp:SyncClock`, `SetClockGlobal`

Each tick copies Skyrim.esm's `GameHour` (0x38), `GameDay` (0x37), `GameMonth`
(0x36), `GameYear` (0x35), `GameDaysPassed` (0x39) and `TimeScale` (0x3A) into
the TES3 globals of the same meaning. The value is the float at `+0x34`, which
is all `GlobalVariable.GetValue` (0x9c2b30) reads. Month is 0-based and Day
1-based in both games. The year is Skyrim's.

The copy runs the other way too. A script's changed value of one of these
(`set GameHour to 13`) is stored into the Skyrim GLOB at once, through the
`setClockGlobal` hook in `DialogueState::SetGlobal`. Before that, the next tick's
copy put the old hour back, so a TES3 script could not move the time of day.
The headless tester found this (`mwtest`, case `global`).

### <a id="scale"></a>Scale is the reference's own

**Code:** `plugin/game_calls_move.cpp:RefScale`, `SetRefScale`

`GetScale` reads `TESObjectREFR`'s own scale, a uint16 percent at 0x98 on
1.6.1170 (0x90 pre-AE, through `ActorField`), as OpenMW's returns the
reference's scale. Papyrus `GetScale` was wrong twice: it multiplies in the
race height (1.03 after `SetScale 1` on a Nord male), and `SetScale` is
queued, so it lagged a set made the same frame. A scale a script set is
answered until the field shows it, so `ModScale` right after `SetScale`
adds to the new value. Found by the headless tester (`scale_one`,
`scale_mod`). The offset was measured: `player.setscale` 1.5 then 0.75 left
150 then 75 at 0x98 and nowhere else in 0x80-0x110.

### <a id="place-at"></a>An absolute move is ONE engine move

**Code:** `plugin/game_calls_move.cpp:PlaceAt`, `plugin/game_calls.cpp:PlaceOf`

`Position`, `PlaceItem`, and the `AiTravel`/`AiEscort` destination markers
all go through `PlaceAt`, which calls `MoveInto` (`TESObjectREFR::MoveTo_Impl`)
with the reference's own cell indoors or its worldspace outdoors, position and
Z angle together. It used to call Papyrus `SetPosition` then `SetAngle`. Both
are queued, and `SetAngle` re-placed the reference at the position it read
before the first landed, so nothing moved. Measured by the headless tester:
`player->Position 32 48 0 0` left x and y unchanged, and moves the player to
(32.1, 48.7) now. Morrowind.esm calls `Position` 5 times and
`AiTravel`/`AiEscort` 20 times. The markers took the same two calls, so they
were most likely left where they spawned, beside the actor. That was not
measured on the old code. Case `aitravel_far` now checks that a travel 3000
units away is not done after 15 ticks.

### <a id="published-state"></a>What a converted bark tests, the runtime writes into a GLOB

**Code:** `plugin/game_calls_state.cpp:PublishState`,
`tes4_export/record_types/morrowind_bark_conditions.py`

A voiced bark is a real Skyrim INFO, so its conditions are evaluated by the
engine, which cannot see anything this DLL owns. Each tick the runtime copies
that state out, writing only a value that changed:

* every TES3 global into the converted GLOB `GLOB.txt` names for it;
* each row of `state_formid.txt` -- GLOBs the export MINTED for the barks --
  `journal:<id>` (the journal index), `cell:<prefix>` (1 while the player's
  cell name starts with it, TES3's prefix match), `reputation`, and
  `weather` (`Tes3Weather` of Skyrim's classification, used only where
  Morroblivion's `mw*` WTHRs are not loaded).

A journal is published rather than read off its QUST because every dependent
plugin writes its own copy of a master's journal quests and only the first
sidecar folder's copy is staged.

### <a id="player-factions"></a>The player's factions are real Skyrim factions

**Code:** `plugin/game_calls_state.cpp:ApplyPlayerFaction`

`PCJoinFaction`, `PCRaiseRank`/`PCLowerRank` and `PCExpell`/`PCClearExpelled`
also call `Actor.SetFactionRank` (id 54750, 1.6.1170 0x9ea340) on the player
and `Faction.SetPlayerExpelled` (id 55843, 0xa1dc80) on the converted FACT
that `factions_formid.txt` names, so `GetFactionRank`, `GetPCExpelled`,
`SameFactionAsPC` and `GetFactionRankDifference` answer natively. Rank -1
(left the faction) is written as -1, which Skyrim reads as not a member.
Every membership is pushed again after a co-save load.

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

### <a id="the-tick-sleeps-whole-milliseconds"></a>🛑 The tick thread sleeps in WHOLE milliseconds

The thread used to wait with `sleep_for(duration<float>(1/30))`. MSVC's
`_To_absolute_time` builds the deadline as `decltype(now + rel)`, and
`nanoseconds + duration<float>` is `duration<float, nano>`: a float count of
nanoseconds since boot. At 2^49 ns (about 156 hours of uptime, which Windows
Fast Startup does not reset) a float's step is 67 ms, so `now + 33 ms` rounds
back to `now` and the sleep returns at once. Computed with the STL's own
types: 33.5 ms at 150 h, 0 ms at 157 h and every uptime after.

The spinning thread then re-posted the next tick the moment the running one
cleared `g_queued`, and SKSE drains a task posted mid-drain in the same sweep.
The frame ended only when Windows happened to deschedule the thread. Frame time
became one tick's cost times the ticks that slipped in:

| State | Tick cost | Reported |
|---|---|---|
| A pausing menu, the console, chargen | a few µs (the tick is gated) | 60 fps |
| Oblivion only, nothing staged | small | 30-50 fps, erratic |
| Morrowind, Tribunal, Bloodmoon | the sweep, 40 carried polls, 7 globals | 1-3 fps |

It was never the tick's own work. A headless bench over the real three
sidecars (10,640 placements, the same 7 globals) measured 0.03 ms a tick with
the engine stubbed and no C++ throws, and every engine call in it is a list walk
or a hash lookup (`GetFormFromFile` 1.6.1170 `0xa0c7e0`, `GetItemCount`
`0xa3e420`). The float sleep shipped in 0.658; a heavier 0.664 tick only made
each spin cost more. Every spun tick also advanced the runtime clock 1/30 s, so
Morrowind timers ran fast while it lasted.

`kTickSleep` is `milliseconds(33)`, which holds at every uptime. The shared
main-thread timer (`common/engine.cpp` `StartMainThreadTick`) sleeps in
milliseconds too.

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

<a id="positioncell-needs-an-anchor"></a>
### <a id="positioncell-moves-into-the-cell"></a>`PositionCell` moves into the CELL itself

**Code:** `morrowind_sidecar.py:_cell_lines`, `cells_formid.txt`,
`plugin/game_calls_move.cpp:MoveInto` / `SendToCell`, `ids::kRefMoveToCell`

`PositionCell x y z zRot "cell"` is the most-called command (1,232 sites).
It goes through `TESObjectREFR::MoveTo_Impl` (Address Library 56626,
`0xa447f0` on 1.6.1170), which takes the destination CELL or WORLDSPACE, the
position and the rotation (radians) in one call. Read from its body: `rcx` is
tested for form type `0x3E` and against the player singleton, `[r8+0x40] & 1`
is the cell's interior flag, and the two stack arguments are dereferenced as
vectors. `cells_formid.txt` maps each authored cell name to the interior's
CELL or the exterior's WORLDSPACE; an exterior is NOT named by its CELL,
because an unloaded exterior cell is not a live form, and the position picks
the cell.

🛑 **REVERTED: an anchor reference plus `ObjectReference.MoveTo`.** The table
staged "any one reference the cell contains" and the runtime did
`MoveTo(anchor)` then `SetPosition`. A NON-persistent reference does not
resolve while its cell is unloaded, so the move silently did nothing: vanilla
Morrowind's `CharGen` never reached the Imperial Prison Ship, whose staged
anchor `00BC6402` is non-persistent. Measured over Morrowind.esm
(`temp` probe over `REFR`/`ACHR` `RecordFlags & 0x400`): 5,635 cells hold a
placement and only **1,133** hold a persistent one. `Cell.GetNthRef` fails the
same way. The earlier "331 of 332 named cells hold a placement" measurement
counted placements, not PERSISTENT placements, which is why it looked served.

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

### <a id="forced-movement-is-a-latch"></a>Forced movement is a LATCH — and only SNEAK can be applied

**Code:** `plugin/script_ops_query.cpp`, `plugin/game_calls_query.cpp`

OpenMW implements all twelve `Force*` / `ClearForce*` / `GetForce*` commands as
one movement FLAG on the actor's stats, and reads the STANCE as

```
Stance_Run   = Flag_Run   || Flag_ForceRun        (CreatureStats::getStance)
Stance_Sneak = Flag_Sneak || Flag_ForceSneak
```

so the latch forces a stance ON TOP of what the AI is doing rather than
replacing it. The DLL owns the flag the way it owns the AI settings.

**Only `ForceSneak`/`ClearForceSneak`/`GetForceSneak` are ported**, and the
mechanism is a **flag write, not a call**: `[actor + 0xCC] |= 4` to set,
`&= ~4` to clear.

🛑 **`Actor.StartSneaking` is the wrong native and cost three build-and-play
rounds.** Its first instructions compare the target against the player
singleton and take a do-nothing branch for anyone else, so it silently fails
for every NPC — the CK wiki's "has no effect on the player" says the opposite
of what the code does. What works is the CONSOLE's `SetForceSneak`, whose
handler (0x3552b0, reached from the command-table row at 0x1fdfc00) does
nothing but set that bit and echo `SetForceSneak >> %0.2f`.

Confirmed in game: `setforcesneak 1` on the hunter makes her sneak; the native
never did. The lesson generalizes — when a Papyrus native and a console
command share a name and a purpose, the console command is often the one the
AI actually uses, and the exe settles it in a way the wiki does not.

🛑 **Run, Jump and MoveJump are `kDeliberateNoOps`, not latches.** Skyrim
exposes no way to force a gait. Porting them would store a value nothing ever
reads — a command that counts as **ported** in the audit, logs a plausible
`move:` line, and moves nothing. That hides far better than a stub, which at
least says "not ported yet". A command with no engine mechanism belongs in
`kDeliberateNoOps`; `_status` now returns **CONFLICT** if one is installed
anyway, so the two claims can never both stand.

The sneak flag persists through the co-save under tag `M`, and only when set —
a cleared latch writes nothing.

🛑 **`WakeUpPC` has NO Skyrim native.** OpenMW implements it as
`WindowManager::wakeUpPlayer()` — it interrupts the wait/sleep MENU, which
Papyrus cannot reach. Its 9 call sites stay stubbed; the hook was removed
rather than left declared and unbound.

### <a id="a-bad-statement-is-skipped"></a>A statement with misplaced values is skipped, not the script

**Code:** `CompileSkippingBadLines` in `plugin/script_runner.cpp`

Morrowind's Construction Set compiled past a statement whose values were out of
place, and the engine ran the rest of the script. Bloodmoon's `ColonySeler`
(Bar Brawl) writes `PositionCell "Raven Rock, Bar" 4480 3968 15820 0`, with the
cell first. The vendored OpenMW compiler rejects the float after the cell name,
and the whole script, which drives Seler's fight and greetings, never ran. UESP
records the original behavior: MPP 1.6.5 "fixes a syntax error that the
Morrowind game engine and the Construction Set usually ignored", and that fix is
what makes Seler vanish to (4480, 3968, 15820). The bar's own placements span
x -509..1112, so the line was never meant to run.

A compile that fails on a number is retried with that line blanked, up to four
times. Only a plain statement is dropped: an `if`/`else`/`endif`/`while`/`begin`/`end`
line would reshape the script. A failure on a NAME is never dropped. That is a
member or variable our tables cannot resolve, so skipping it would hide a data
gap. Each skip is logged as `skips line N, as Morrowind's compiler did`.

Measured with `script_test --sweep-objects`, the line is the only one skipped in
both modes:

| Sidecars | Bodies | Failed before | Failed after |
|---|---|---|---|
| Morrowind + Bloodmoon (authored) | 1,644 | 5 | 4 |
| Patch + Tamriel_Data + TR_Mainland | 5,520 | 133 | 132 |

A first version that skipped on any error recovered 35 more TR scripts. Every
line it dropped was a cross-script member write, for example
`set "Athyn Sarethi".duelOndresNerano to 2`. Those fail because the patch
sidecar maps no script to the vanilla actor, so dropping them would have
silenced real writes.

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

**`GetWeaponType` answers the authored TES3 type.** OpenMW returns the
right-hand weapon's WPDT type (0 short blade to 13 bolt), or -1 for an empty
hand. The export's weapon type is TES4's, which folds Morrowind's axes, spears
and blunt kinds together, so the sidecar reads WPDT from the TES3 binaries
(`weapon_kinds`) and adds it as the third field of each weapon's
`items_formid.txt` row. That covers both modes: in Morroblivion mode the TES3 id
still resolves to Morrowind_ob's form. The runtime maps each row to its runtime
FormID once and looks up `GetEquippedWeapon(false)`. A weapon no TES3 plugin
defines (Skyrim's own), and OpenMW's -2 lockpick and -3 probe, which Skyrim
never puts in a hand, answer -1. Baro Egnatius's script (Raven Rock's CO_5)
uses it.

<a id="two-registration-shapes"></a>**A native's id: TWO registration shapes,
and reading only one finds nothing.** The ids for `IsEquipped` (54707),
`GetSleepState` (54715), `GetEquippedItemType` (54685) and `GetEquippedSpell`
(54683) were read out of `temp/skyrim_live.img`, the saved decrypted 1.6.1170
image, by finding the name string, then the `lea rdx` that loads it, then
inverting the function address through `tools/disasm/address_lib.py --rva`.
All four exist in all 12 shipped versionlibs. The method was validated against
`IsSneaking`, whose recovered id matched the 54953 already in `ids.h`.

| shape | where the function is |
|---|---|
| `lea r9, <func>` beside the name | `IsEquipped` |
| `xor r9d, r9d`, then `lea rax, <func>` → `[rbx+0x50]` AFTER the call | the other three |

Reading only the `r9` form reports "no such native" for three of the four.

🛑 **`GetSpellReadied` is not a draw-state read.** Skyrim exposes no drawstate
getter, but a readied spell IS an equipped spell, so the question becomes
whether either hand holds one — `GetEquippedSpell(1) || GetEquippedSpell(0)`.
`GetSleepState` is likewise an ENUM (0 awake, 2 about to sleep, 3 asleep,
4 waking) folded to TES3's single yes/no at state **3**.

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
Skyrim's `TranslateTo` takes Euler degrees with no world-composed form. They agree
on any single axis; of the 17 scripts that rotate anything, **2** turn more than
one (`TR_m1_lud_cogspinner`, `TR_m7_HH_Alvynu_7_ShipSink_sc`) and are the only
places the approximation can show.

🛑 **Each step is a `TranslateTo` glide, never `SetPosition`/`SetAngle`.**
Those two reload the reference's 3D, which fades back in; called every tick,
the object never finishes fading and reads as nearly invisible (Arktwend's
`ex_de_constr_06` intro door). `game_calls_move.cpp:GlideTo` instead aims
`TranslateTo` (id 56237, `0x9d1f70` on 1.6.659) at the tick's goal with
speed = distance / `TickDelta()`, so it arrives as the next tick starts —
confirmed smooth in-game. Details that matter:

- The goal CHAINS from the previous goal while calls come every tick, and
  same-tick calls add into it, so a two-axis rotate is one glide, not the last
  axis alone. `Position` drops the chain; `SetPos`/`SetAngle` join it (below).
- A rotation cannot finish before the position does, so a pure rotate adds a
  0.01-unit Z nudge (alternating up/down) to give the glide a duration.
- Target angles are taken the short way from the current ones, since the
  reference stores normalized radians.

The absolute setters (`SetPos`, `SetAngle`, `Position`, `PositionCell`) stay
on the instant natives — they are single teleports — with one exception:
`SetPos`/`SetAngle` on a reference whose glide chain is live (it asked last
tick or this one) become that glide's goal instead. Scripts close a
`Rotate` loop with an absolute reset — the shop-sign `SignRotate` swings
+2/−4/+2 degrees and then runs `SetAngle, y, GetStartingAngle, y` — and the
native there faded the sign out and back in once per loop. Confirmed in-game.

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

A plugin authoring no SCPT, DIAL, INFO, NPC_ or CREA (a grass or landscape
plugin) mints no pool and deletes any `ai_aliases.txt` an older build left: no
script of its layer can call an AI command, and in the merged view its pool,
being the deepest, would otherwise shadow the one a real master owns.

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

#### <a id="a-package-finishes-when-it-arrives"></a>A package finishes when it arrives

**Code:** `plugin/game_calls_ai.cpp:AiPackageDoneFor`, `ReachedAiDestination`

`GetAiPackageDone` asks whether the package has FINISHED; Skyrim's
`GetCurrentPackage` only says what is assigned, and our own pack keeps winning
while its alias holds. So arrival is our test: within 128 units of the
destination marker, after which the actor is released.

🛑 **Height does not count.** OpenMW's `AiTravel::execute` ends on
`distanceIgnoreZ` (`apps/openmw/mwmechanics/aitravel.cpp`), and authored
targets rely on it: chargen's `CharGenWalkNPC` sends the boat guard to
`(90, -90, -88)`, about 240 units under the deck he walks on. Measured in 3D he
stood at `(110, -101, 154)` for good, `GetAiPackageDone` never went true, and
the movement tip and the rest of chargen never came (headless run from a New
Game save, 2026-10-24).

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

### <a id="the-tick-is-gated-on-a-loaded-game"></a>The tick is gated on a loaded GAME, not on a named cell

**Code:** `plugin/object_tick.cpp` (`SessionLive`), `plugin/game_calls.cpp`
(`PlayerInWorld`)

`RunOneTick` must do nothing before a game is loaded: the main menu still runs
the task pump, and an ungated tick popped script MessageBoxes over the title
screen (measured 2026-09-18, three "You pry open the lock" boxes).

That gate was written as "the player's cell has a name":

```cpp
return Hooks().playerCell && !Hooks().playerCell().empty();
```

`PlayerCellName` reads `cell + kOffCellFullName`. Interiors have a name.
**Unnamed exterior wilderness cells return `""`** — so the gate was false across
most of the world, and `RunOneTick` returned at its first line. No object script
ticked outdoors at all: no `OnDeath`, no `OnPCHitMe`, no proximity poll, no
discovery sweep.

Measured 2026-09-19 over the game bridge, with the player beside Ga'Nahiru in
the Armun Ashlands:

- `player.getdistance 2157B5CA` → **511.54**, so the reference resolves and is
  loaded.
- `getav health` → **383.00**, alive and addressable.
- The log shows `object: tick started at 30 Hz` and then **zero** object-script
  output across 84 seconds — in a session that logged an activation.

The activation line comes from the Activate *hook*, which is outside the tick,
so it printed while the tick itself was inert. That is the discriminator: the
hook fires, the tick does not.

The gate now asks `PlayerCell() != nullptr` — the player is in *some* cell. It
is not a worldspace or distance test and never looks at the cell's name; it
separates "a game is loaded" from "the main menu", which is all it was ever
meant to do.

`PlayerCellChanged` carried the same family of bug: `moved` was
`!g_lastCell.empty()`, so the first transition *out of* an unnamed exterior was
swallowed and re-armed on the way back in. It now tracks "have we sampled yet"
explicitly.

### <a id="the-tick-stops-while-the-game-is-paused"></a>The tick stops while the game is PAUSED, and the runtime clock stops with it

**Code:** `plugin/object_tick.cpp` (`GameHeldByMenu`, `GameSeconds`),
`plugin/game_calls.cpp` (`GamePaused`, `Now`), `plugin/menu.cpp`
(`PausingMenuCount`)

The tick is driven by a detached thread that sleeps one delta and posts a task,
so it is paced by WALL time and nothing about a paused game stops it. The SKSE
task pump keeps draining while a menu holds the game — that is the same fact
the objective wait relies on, and why it sleeps off-thread rather than
reposting. `SessionLive` separates the main menu from a loaded game and says
nothing about a pause.

So through an open inventory the tick kept running: timers integrated,
`rotate`/`move` stepped, and `Say` lines aged out on `steady_clock` behind an
engine that had stopped playing them.

The Say case is the one that is visible. `ObjectReference.Say` (id 56220,
`0xa2fb40` on 1.6.1170) does not play a sound — it installs the line into the
actor's high process (`mov qword ptr [rsi+0x128], rbp` at `0x6d72e9`, reached
through `0x6d71e0` with the process from `[rdi+0xf8]`) and stamps it with a
counter read from `0x20f699c`. A frame-driven update retires it. Pause before
the line ends and that update never runs, so the subtitle stays on screen; our
own bookkeeping meanwhile aged past its deadline, freed the speaker and let the
script issue the next `Say` — which Skyrim DROPS at an actor it still has
mid-line. Stranded subtitle, and the line after it never spoken.

**The pause flag is the engine's own.** `MenuManager+0x160` (SKSE's
`numPauseGame`) counts the OPEN menus carrying `IMenu` flag `0x1`. Verified in
1.6.1170: of 275 sites that load the MenuManager singleton (`0x20f6a00`,
Address Library id 400327, already resolved for menu registration), **75 read
it as `cmp dword ptr [rax+0x160], 0` and branch past their work**.

**Our own dialogue menu sets that flag** (`kMenuFlags` includes
`kFlagPausesGame`), so it counts itself. TES3 runs scripts through a
conversation — `MenuMode` exists for them to branch on, and the opcode census
counts 1,111 uses — so `GamePaused` discounts the conversation's own
contribution rather than testing for zero:

```cpp
return PausingMenuCount() > (ConversationOpen() ? 1u : 0u);
```

Comparing against a COUNT, not a boolean, is what makes a menu opened *over* a
conversation still stop the tick.

**Events are not lost to the gate.** `activated`, `died` and `cellChanged` are
sticky until a body reads them, so anything raised during a pause is delivered
on the first tick after it. `PollDeath` still runs before the loaded gate for
the separate reason recorded in `object_tick.cpp`.

**The clock had to move with the tick.** Gating alone does not fix the Say bug:
`Now()` was `steady_clock`, which runs through a pause whether or not we tick,
so the claim would still age past its deadline and be erased on the first tick
back. `GameSeconds()` accumulates one delta per tick that actually RUNS, and
`Now()` reads it. Every runtime timer derives from that one clock.

🛑 This is stricter than Morrowind. OpenMW runs local and global scripts while
paused (`engine.cpp`, gated only on `GM_MainMenu`) and does not treat the
inventory as a pause at all (`DateTimeManager::updateIsPaused` counts only Lua
pause tags, the console, the post-processor HUD and interactive message boxes).
Its audio does not pause either — `SoundManager::update` checks only
`mPlaybackPaused`, and `sayDone` asks the stream. We diverge because Skyrim's
engine freezes the world around the script either way.

Guarded by `PausedTickCases` in `script_test.cpp`.

### <a id="a-load-resets-the-instances"></a>A load resets the INSTANCES, not just the state

**Code:** `plugin/cosave.cpp` (`OnRevert`)

`OnRevert` is what SKSE calls before a new game or a load, and its comment said
"nothing from the last game survives". It reset `DialogueState` and nothing
else, so every `ObjectScript` instance lived on across the load, carrying:

- `mDeathSeen`, the latched `OnDeath`
- `mLifeSampled`, which side of life the actor started on
- `mWasLoaded`, and the binding to a FormID from the torn-down session

Script *locals* were never the leak — they live in `DialogueState`, which
`Reset()` clears and `Deserialize` repopulates. The per-life flags were.

Measured 2026-09-19: a save where Ga'Nahiru had already been killed, then a
**new game**. The log shows `cosave: state reverted`, and eleven seconds later
`TR_Mainland.esm|2863BF.tr_map = 4` — a local written by an instance that
should not have existed yet. No `bound from the world` line appeared for the
kagouti, because it was still bound from the previous session with
`mDeathSeen` set, so `PollDeath` returned on its first guard forever. The quest
softlocked at stage 10.

The same applies to reloading an earlier save on one character: kill the
creature, reload to before the kill, and it can never raise `OnDeath` again.

`OnRevert` now calls `ClearInstances()` and `ResetTickState()`. Instances
rebuild from the world through the discovery sweep, which is what makes
throwing them away cheap. `StartStartupScripts` is safe after the clear: it
only records names in `DialogueState`, and `RunGlobalScripts` builds those
instances on the next tick.

### <a id="instances-bind-from-the-world"></a>An instance binds from the WORLD, not from a click

**Code:** `plugin/object_tick.cpp` (`DiscoverLoaded`), `plugin/game_calls.cpp`
(`LoadedRef`)

`BindInstances()` deliberately resolves nothing at load: `Game.GetFormFromFile`
only answers for a form the engine has loaded, and 139 of TR_Mainland's 15,540
placements are persistent. Instances therefore bind lazily. The defect was that
the only lazy path was the **Activate hook** — so a placement's script ran only
if the player had clicked it.

Everything that does not involve clicking was silently dead: `OnDeath`,
`OnPCHitMe`, and every proximity test a script polls for itself.

Measured 2026-09-19 from `MorrowindRuntime.log`, on `TR_m4_wil_GaNahiru`
(Tamriel Rebuilt). `TR_m4_AA_Ganahiru_Script` guards its whole body on
`GetJournalIndex == 10` and advances the quest only inside `if ( OnDeath )`.
The journal reached 10 (`journal: tr_m4_wil_ganahiru = 10`), the player killed
Ga'Nahiru, and the log holds **no `object: ... died` line at all** — the poll
never ran, because `PollDeath` returns on `!mRuntimeFormId` and nothing had
bound the instance. The same unbound instance is why the creature never turned
aggressive (`GetDistance player < 800` → `SetFight 90`) and why Shara-Ahhe
never force-greeted at 2300 units: those are polls in a body that never ticked.
The one line naming that script, `object: ... activated`, came from a player
click and is the exception that proves the rule.

So the tick sweeps the staged table and binds any placement whose reference
`GetFormFromFile` now resolves *and* whose 3D is loaded. The null answer is the
"not here yet" signal rather than a failure, which is exactly the window a
local script should run in.

The sweep is sliced at 256 rows per tick, and **rests between laps**. The table
is 15,639 rows and each row costs a `GetFormFromFile` plus an `Is3DLoaded`, so
sweeping continuously is 7,680 engine calls a second to learn nothing: almost
every row is in a cell nowhere near the player, and only a cell LOADING changes
an answer. A lap therefore starts when the player changes cell, and otherwise on
a 5s heartbeat — exteriors stream neighbours in without a cell change, which the
heartbeat covers. Once started a lap always finishes, so a placement that
appears mid-lap is not missed.

Guarded by counting calls into the `loadedRef` hook (`20 ticks cost about one
lap, not twenty`): behaviour is identical with and without the rest, so only a
call count catches a regression here.

`PollDeath` also moved ABOVE the `Is3DLoaded` gate. TES3 gives `OnDeath` one
tick and the poll latches it once, so a gate that skips the instance on the tick
the death is seen discards the event permanently. A just-died instance now
unbinds *and* still runs its body once.

🛑 Not because corpses vanish — they ragdoll and stay. The gate reads false
whenever `Game.GetForm` stops answering for the reference, which includes the
engine freeing a dead one (measured 2026-09-18: a cached pointer to a dead
`PlaceAtPC` creature crashed the poll 41s later, `RefByRuntimeId`). Polling
first is free and does not depend on which case applies.

<a id="a-disabled-reference-is-still-in-the-world"></a>**A disabled reference
is still in the world (2026-10-07, PENDING in-game confirmation).** `Is3DLoaded`
answers false for a disabled reference, so a script that disabled its own
object was unbound as "unloaded" and never ran again. Arktwend's tavern
messenger (`ANFTaverneBoteScript`) starts with `if ( Storyvar < 2 ) Disable`
and enables himself once the story reaches him: the log shows him bound, run
once, `world: ANFTaverneBote disabled`, and then nothing, and the player was
left with no way out of the scene. 260 of Arktwend's 753 scripts hold a bare
`Disable`. OpenMW's `executeLocalScripts` (`apps/openmw/engine.cpp`) runs every
local script of the active cells with no enabled test. So `LoadedRef` and
`Load3DStateRef` (`game_calls.cpp` `InWorld`) now count a reference with no 3D
as loaded while its parent cell is attached: `Cell.IsAttached`, id 56055,
whose whole body is `cmp byte ptr [cell+0x44], 7`. The cell rather than
`IsDisabled`, because enabling queues the 3D: for the frames after `Enable`
the reference is neither disabled nor loaded, and a test on the flag would
unbind the script on the tick it woke up.

The first build of this unblocked the scene (in-game 2026-10-07: the messenger
enabled, walked over and force-greeted) and brought two faults with it, both
read out of that run's log:

- **An interior must also be the player's cell.** The engine keeps the interior
  just left attached, so the chargen cell's scripts ran on after the player was
  moved to the tavern: `a_Ring Erscheint_Licht_02 enabled` was logged 1,457
  more times, 48 seconds' worth.
- **`Enable` and `Disable` reach the engine only on a CHANGE**
  (`script_runner.cpp` `OpSetEnabled`). A TES3 script states what it wants
  every frame and OpenMW's calls do nothing when the state holds; the Skyrim
  natives were called thirty times a second per script, 90,760 log lines in
  eight minutes, and the player saw the tavern's NPCs pop in slowly. That the
  native calls caused the slow loading is inferred from the timing, not proven.

Two things the sweep must carry that a click already had:

- **The BASE id, from the staged row.** `BindInstance` passed `""`, so every
  instance the sweep created had no base — and `Implicit::Target` resolves a
  bare command through exactly that. Measured in-game as `combat:  attacks
  tr_m4_armungreatkagouti` with an empty attacker: `StartCombat` with no `->`
  is the object itself, and it was targeting nothing. `InstanceFor` now also
  fills a missing base in, so whichever path reaches a placement first, bare
  commands act on the object.
- **`OnDeath` is a TRANSITION, not a state.** TES3 raises it on the tick an
  actor dies. `PollDeath` latched on `IsDead` alone, so any body already dead
  when first bound raised it — every pre-placed corpse on load, and every
  corpse again whenever its cell reloads. The first poll now only records which
  side the actor started on.

Two consequences worth keeping:

- The test must ask whether the **placement is bound**, not whether its instance
  exists. An instance outlives its binding by design (locals survive an unload),
  so an existence test would let a re-entered cell never rebind —
  `IsPlacementBound`, not `FindInstance`.
- `SetRuntimeFormId` clears `mWasLoaded`. A rebind is a fresh appearance in the
  world, and a stale flag would let the gate below unbind it again before its
  3D exists.

### <a id="a-deleted-reference-unbinds"></a>A deleted reference unbinds, and only actors die

**Code:** `plugin/game_calls.cpp` (`DeleteRef`, `IsDead`), `plugin/object_script.cpp` (`PollDeath`)

- **`SetDelete 1` unbinds the instance before the engine deletes the
  reference.** Bloodmoon's `sprigganeffect` deletes its own spawned object.
  The tick kept polling it, and `Game.GetForm` on the deleted spawn's id
  (`FF000D73`) returned the freed reference. A captured crash shows an access
  violation inside `Game.GetForm` (`SkyrimSE+0xa2d560`), called from the runtime
  with that id.
- **`Actor.IsDead` is asked only of actors.** Asked of the effect activator, it
  read past the object and logged a false "sprigganeffect died".
- **`OnDeath` re-arms when the actor is seen alive again.** `ColonySpriggan1`
  `Resurrect`s its body twice and counts three deaths. A latch set once per
  instance counted only the first.

### <a id="a-spawn-is-not-loaded-on-its-first-frame"></a>Unloading is a TRANSITION, not a state

**Code:** `plugin/object_tick.cpp`, `plugin/object_script.h`

The tick drops an instance whose `ObjectReference.Is3DLoaded()` is false, so a
script stops running when its object leaves the world. Written as a bare test of
the current state, that gate also fires on an object which has not loaded *yet*.

A spawn binds on the frame `PlaceAtMe` returns the reference, several frames
before its 3D exists. The first tick therefore read "not loaded" and unbound it
— and **nothing ever rebinds a spawn**: `BindSpawnedInstance` is called once, at
placement. A staged placement recovers when the discovery sweep next sees its
reference loaded ([above](#instances-bind-from-the-world)); a spawned creature
has no authored placement to rediscover, so its unbind is permanent.

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
the completes-quest bit, and an objective per page (see
[objectives-must-be-displayed](#objectives-must-be-displayed)). The QUST is the originating plugin's; a
dependent that adds pages overrides it with the chain's pages
([per-owner staging](../plans/morrowind_object_scripts.md#cumulative-gather-must-go)).
The owner's `quests_formid.txt` maps the authored id to `Plugin|FormID`; `Journal` and `SetJournalIndex` call the
`Quest.SetCurrentStageID` native. `AddJournalEntry` stages the ENTRY's index
even when the quest's own index does not rise — a lower page added late is
still a new page.

🛑 The quests are generated for every journal topic in the MERGED sidecar,
masters' included, into the plugin being imported. Two TES3 plugins sharing a
master would each mint that master's quests.

### <a id="quest-names"></a>A journal with no QSTN takes its name from UESP

**Code:** `quest_morrowind.py:quest_name`, `tools/generators/gen_morrowind_quest_names.py`

A TES3 journal's display name is the INFO flagged `QSTN`
(`QuestStatus=Name`), which Tribunal introduced. Morrowind.esm authors none:
all 629 of its journals with pages are nameless, and a Morrowind.esm-only
build showed `A1_1_FindSpymaster` in the quest list. Tribunal and Bloodmoon
re-edit Morrowind.esm's journals to add most names, so a merged chain is
mostly named -- TR_Mainland's chain (MW, TR, BM, Tamriel_Data, TR_Mainland)
names 1,970 of 2,079 -- but trackers (`IC0_*_token`, `MT_S_*`,
`11111 test journal`) and a few quests that put their title in page 0
instead (`VA_VampChild` = "Blood Ties", Bloodmoon's `CO_*`) stay nameless.
OpenMW's `Quest::getName()` reads only the QSTN INFO, so this is faithful
data, not an export bug.

FULL is, in order: the chain's QSTN name, `morrowind_quest_names_authored.json`
(hand-written), `morrowind_quest_names.json` (UESP), the raw id. An authored
QSTN always wins. The UESP table pairs each `{{Quest Header}}` page's `|ID=`
journal ids with the page title (minus namespace and a trailing
`(quest)`-style disambiguator), over the Morrowind, Tribunal, Bloodmoon,
Morrowind Mod, Tamriel Rebuilt and Project Tamriel namespaces. An id a page
lists FIRST beats the same id in another page's `also` list.

The authored file covers only what UESP misses and a player would see -- it
is hand-written and has no generator. Arktwend (English) is the extreme case:
it kept its German journal ids and authors QSTN names for only 31 of its 123
journals, so 89 journals with pages showed raw ids like `A_I_TAVERNE`. Their
names live in the authored file, taken from a title the first page carries
where it has one ("The Patriarch Inn"), else composed from the pages.

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
too, and it is where the PLAYER's Personality and Luck START: Skyrim has
neither attribute, and that record is the only authored value a TES3 player
ever starts with. Both sides read them through the stat store
(`ActorAttribute`), so a script's `SetPersonality`/`ModLuck` moves persuasion
too; the actor line's own column answers only when the store reads 0, which
is a sidecar older than its attribute column. See
[the character sheet plan](../plans/character_sheet.md#bug-persuasion). Speechcraft and Mercantile both read Skyrim's `Speechcraft`
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

## <a id="stat-commands"></a>The stat commands: Skyrim's value where one exists

**Code:** `plugin/script_ops_stats.cpp`

`Get`/`Set`/`Mod` for the 8 attributes, the 27 skills and the 24 magic-effect
magnitudes, plus `GetLevel`, are one class (`OpStat`) installed over OpenMW's
six opcode bases per family. The audit over TR_Mainland went from 129 ported
commands to 307, and the stubbed call sites from 4,045 to 3,581.

- A stat Skyrim HAS reads and writes that actor value, so what a script sets
  is what the engine acts on. `Get` reads the current value. An attribute's
  or skill's `Set` writes the base, and `Mod` adds to the BASE
  (`GetBaseActorValue`), so an active buff is not baked in.
- A magic-effect stat moves Skyrim's permanent modifier (`ModActorValue`,
  id 54721), as OpenMW's `OpSetMagicEffect`/`OpModMagicEffect` move the effect
  magnitude: `Set n` adds `n - current`, so the total becomes `n`, and `Mod`
  adds its operand. A base write does not take for every one of them:
  measured, `setav damageresist 0` left the player's 65 (armor derives it),
  while `modav damageresist 10` gave 75. A Nord's racial 50 FrostResist made
  `SetResistFrost 20` read 70 under the old base write.
- A stat Skyrim lacks -- every attribute, Athletics, Acrobatics, and the
  effects with no actor value -- is the DLL's own number under the owner
  `stat|<locals owner>`, in the co-save, starting at the NPC_ record's
  authored value. `NPC_.txt` carries those as its last two columns, 8
  attributes and 27 skills comma-joined in TES3's own order.
- The weapon and armor folds are the import's (`MW_SKILL_TO_TES4` then
  `TES4_SKILL_TO_TES5`), so a command reads the value the converted NPC was
  given; Enchant is Skyrim's Enchanting on both sides.
- A skill Skyrim split in two reads the **higher** of both and writes the
  first: Long Blade, Axe and Blunt Weapon read One-Handed/Two-Handed, Medium
  Armor Heavy/Light Armor, and Spear reads Two-Handed (spears export as
  two-handed blades). Mysticism reads Alteration, where its spells convert. See
  [the character sheet plan](../plans/character_sheet.md#bug-blade-blunt).
- The magic-effect family is typed `long` by the compiler, so it pushes and
  pops integers.
- **A health write that leaves current health at 0 or below kills**
  (`KillIfDrained` in `game_calls.cpp`, through `Actor.Kill`, id 54720). In
  OpenMW, `Actors::update` kills any actor whose current health is at or below
  0. Skyrim kills only through damage, so `SetActorValue` left the actor
  standing at 0 health. The headless tester saw it: after `arrille->SetHealth
  0`, `GetDeadCount arrille` stayed 0. Vanilla relies on the OpenMW rule:
  `girasScript` kills Dagoth Ur and the Ash Vampires with `SetHealth 0` once the
  Heart is destroyed, and `dreamerWorkerScript`, `boneScript` and
  `mastriusScript` use it too (12 uses in 4 scripts). `Kill` takes essential
  actors too, as OpenMW's death does.

NOT done: `SetLevel` (Skyrim has no setter).

### <a id="the-player-stats-are-real"></a>🛑 The FILTER reads those same stats

`ActorSkill` / `ActorAttribute` expose that one read by TES3 index, and
`GameActor::PlayerSkill` / `PlayerAttribute` answer the dialogue filter
through them. There is no second stat path and no second table.

**Both returned a constant `100` before this.** That is not a small gap: the
filter answers `PCSkill`/`PCAttribute` conditions with it, and
`Filter::HasSkillsForRank` sorts the faction's skills and measures the best
three against the rank row — so a flat 100 showed every skill-gated line and
passed **every faction rank requirement in the game**, promoting anyone who
asked. The neutral-answer rule that chose 100 (a stub should not HIDE
dialogue) is right for a fact the runtime cannot know; it was wrong here,
because the stat is knowable and the same file already read it.

An index outside its family answers 0 rather than reading past the table: the
filter passes an index straight off a condition record.

## <a id="show-rest-menu"></a>`ShowRestMenu` is a Skyrim bed

A TES3 bed is an activator whose script calls `ShowRestMenu` on activation
(`Bed_Standard`). OpenMW's `OpShowRestMenu` asks `sleepInBed` first and opens
the rest menu only if that did not refuse. The runtime does what activating a
Skyrim bed does (1.6.1170 id 17420 `+0x16a`): PlayerCharacter's can-sleep-here
check (`0x731350`, id 40443), which given a bed also refuses one the player does
not own, with the engine's own message, then the Sleep/Wait menu toggle
(`0x95e0d0`, id 52490) with `sleeping` set. The wait key calls the same toggle
with false. Where Morrowind would charge `iCrimeTresspass` for a witnessed
sleep in an owned bed, Skyrim refuses the bed.

## <a id="startcombat-between-allies"></a>A scripted fight lasts until a death or StopCombat

**Code:** `plugin/game_calls_combat.cpp`

OpenMW's `StartCombat` gives the actor an AiCombat package that lasts until
the target dies or `StopCombat` removes it, whatever their factions say.
Skyrim's combat gives up sooner, in two measured ways:

- **Between allies:** on Arktwend's bridge three Thieves Guild shadows walked
  away from Tate, their guild brother, within seconds. Between two NPCs that
  are not already enemies, the attacker's relationship to its target is set
  to archnemesis for the fight. The rank it replaced is kept in the co-save
  and put back once either side is dead or the attacker gets `StopCombat`.
- **A target it cannot find:** in Barenziah's Chambers (Tribunal's
  assassination attempt), one of the two assassins left behind the screens
  dropped the player within a second, a different one each run. Archnemesis
  did not keep it on the player. Each live fight whose attacker has no combat
  target (and has its 3D) is started again every 10 ticks.

A fight on the player is recorded but changes no relationship: Skyrim keeps
relationships per BASE record, and `ordinator wander`, placed 81 times, has
three `StartCombat Player` rows, so one ordinator would turn them all.

## <a id="region-weather"></a>ChangeWeather and ModRegion

**Code:** `plugin/game_calls_weather.cpp`, `plugin/script_ops_query.cpp`,
`tes5_import/dialogue/morrowind_travel.py` (`cell_regions.txt`)

Skyrim rolls each region's weather itself, from the REGN records the import
writes (see
[tes5_import_weather.md](tes5_import_weather.md#morrowind-weather-types)).
The two script commands that change it follow OpenMW (`mwworld/weather.cpp`):

- `ChangeWeather "region" type` sets the region's weather. It shows at once
  when the player is in that region, otherwise when they next enter it.
- `ModRegion "region" chances...` replaces the region's chances for good, up
  to ten, clamped to 0..100; a missing one is 0. If the player is in that
  region and its current weather is still allowed, the weather stays; if not,
  a new one is rolled.
- Every set weather expires together every 20 game hours (OpenMW's
  `Weather_Hours_Between_Weather_Changes`, read off `GameDaysPassed`). A
  region with chances then rolls again: 1..100 against the running sum,
  Clear when the chances fall short (`RegionWeather::chooseNewWeather`).

What the scripts set is kept in the co-save (`weather|chances`,
`weather|set`, `weather|clock`). Every 10 ticks the runtime finds the
player's TES3 region from `cell_regions.txt`: the exterior's TES3 grid, or the
name of a quasi exterior (Mournhold's districts). It then applies what is set
there with `Weather.SetActive` (1.6.1170 id 56774).

A region `ModRegion` changed is held with SetActive's override, because the
engine would otherwise roll the REGN's own chances. The override is a global
lock that outlives whatever set it (see
[tes5_import_weather.md](tes5_import_weather.md#scripted-weather)), so it is
released (`Weather.ReleaseOverride`, id 56807) on the way out of the region,
and once after every load in case the save carried one.

`GetCurrentWeather` answers the sky's weather by identity: one of the ten
forms the sidecar's `weather0`..`weather9` rows name is its own type, so an
ash storm answers 6. Any other weather falls back to Skyrim's
classification.

## <a id="travel"></a>Travel is OpenMW's TravelWindow

**Code:** `plugin/conversation_travel.cpp`, `tes5_import/dialogue/morrowind_travel.py`

An NPC_ lists up to four destinations as `DODT` (position, rotation in
radians), each optionally followed by a `DNAM` naming an INTERIOR cell. The
sidecar stages them as `NPC_travel.txt`, read from the TES3 binaries of the
whole chain so a TR NPC's line and a master's exterior names both resolve:

`npc id=name|interior|x|y|z|zRot degrees;...`

- A destination with no `DNAM` is outside and is NAMED after the exterior cell
  its position falls in (8192-unit grid): the cell's own name, else its
  region's `FNAM`. One that resolves to no name is dropped, as OpenMW drops a
  destination whose cell it cannot find.
- `Travel` is listed when the speaker has a line, which is OpenMW's own test
  (`getTransport()` not empty); there is no service bit for it.
- The fare is `TravelWindow::addDestination`: `fMagesGuildTravel` when the
  SPEAKER stands in an interior, else 3D distance from the player divided by
  `fTravelMult`; times one plus the player's followers; at least 1; then
  `getBarterOffer` (`persuasion.cpp:BarterOffer`, buying). A fare the player
  cannot pay is listed greyed.
- Paying moves Skyrim gold to the NPC, advances `GameHour` by
  `distance2D / fTravelTimeMult` whole hours when the speaker is outside, then
  moves the player and every actor in a follow slot aimed at the player.
  World positions are carried across unchanged by the export, so the authored
  coordinates are used as they are.

🛑 **Adding to `GameHour` is the whole time skip.** The engine's calendar
update (0x5d9420 on 1.6.659) loops `while (hour > 24)` subtracting a day and
rolling the day, month and year globals, then RECOMPUTES `GameDaysPassed` as
`hour / 24` plus its own whole-day counter. So a value past 24 is rolled over
on the next frame and days-passed follows; writing `GameDaysPassed` too would
be overwritten.

🛑 **One `MoveTo` with an offset, not a move then a reposition.** The player's
cell change is a load, and a `SetPosition` issued in the same frame can land
before it. `MoveTo`'s offsets are world-axis (the CK wiki's own example builds
them from sin and cos), so the offset from the cell's anchor to the authored
spot puts the player there in one call.

NOT done: OpenMW also rests the player for the hours travelled and fades the
screen; the cell change shows Skyrim's own loading screen instead.

### <a id="travel-markers"></a>🛑 A destination is a PERSISTENT marker, not a cell anchor

**Code:** `tes4_export/morrowind_travel.py`, `plugin/game_calls_move.cpp:SendToMarker`

The first build paid the fare, moved the clock and left the player standing
there. Travel aimed at the cell's ANCHOR from `cells_formid.txt`, which is
just the first thing placed in the cell -- for `Vivec, Foreign Quarter` an
ACHR with `RecordFlags=0`. A non-persistent reference does not exist until its
cell loads, `GetFormFromFile` answered null, and the move returned without a
word.

So the export mints one XMarker (0x3B in Oblivion.esm and Skyrim.esm alike)
per destination, `RecordFlags=1024`, standing on the authored position and
rotation: in the interior cell for a `DNAM` destination, in the worldspace's
persistent cell otherwise, exactly as the map markers are. Its FormID is
`derive('travelmarker:<npc id>:<index>')`, hashed from authored data, so no
existing id moves. The sidecar finds it by EditorID
(`TES3Travel<npc><index>`) and stages it as the destination's last field, and
the runtime does one `MoveTo(marker)` matching its rotation.

A destination whose cell no plugin of the load order defines gets no marker,
as a teleport door into one gets no link. Without a marker the runtime falls
back to the anchor and now LOGS when that does not resolve.

### <a id="the-list-modal"></a>Persuasion and Travel are one modal

**Code:** `plugin/conversation_modal.cpp`

Both are a title, the gold label, up to six rows and Cancel, so both drive
the same SWF fields; rows past the ones in use are hidden. TES3 allows four
destinations, which fits.

## <a id="sound-opcodes"></a>The sound commands

TES3 scripts name a **SOUN id**; the record Skyrim plays is the **SNDR** the
import minted, which `SOUN.txt` maps (see
[tes5_import_sound.md](tes5_import_sound.md#the-runtime-sound-table)). Measured
over the Tamriel Rebuilt chain: 10 of the 11 sound commands are ported,
covering **1,764 call sites** — `playsound` 804, `playsound3d` 280, `say` 184,
`saydone` 44, `getsoundplaying`
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

`say` and `saydone` are ported through their own hooks rather than through a
SNDR: a voice line moves the actor's mouth only when the engine plays it as
DIALOGUE. See [scripted Say](#scripted-say).

`streammusic` (1 call site) is the only sound command left stubbed.

### <a id="scripted-say"></a>`Say` names a FILE; the engine needs a TOPIC

**Code:** `tes4_export/record_types/morrowind_say.py`,
`tes5_import/dialogue/say_morrowind.py`, `say_topics.py`,
`game_calls.cpp:SayLine`.

`say "Vo\Misc\x.mp3" "text"` names a recording. `ObjectReference.Say` takes a
**Topic** and lets the engine pick the INFO, so:

* **Each distinct line gets a topic of its own holding one INFO.** Saying the
  topic can then only say that line. The first version hung every line under
  one shared topic and handed `Say` the INFO's FormID: a `TESTopicInfo` where
  the native reads a `TESTopic`, and had it been the topic the engine would
  have picked the same first-passing line every time.
* **The topic is a Conversation topic marked `MorrowindSay`**, which
  `say_topics.build_say_topic_dispositions` files with Oblivion's script-driven
  topics: kept, `CUST`, on a Normal (non-top-level) branch so it never reaches
  the player's menu. The first version used the `Idle` bark EditorID, which put
  all 134 vanilla scripted lines on the ambient idle channel.
* **The INFO is gated by `GetIsID` on its authored speakers** -- the actors
  carrying the script, the reference a call names with `->`, or a result
  script's `ONAM`. `_record_voice_entry` reads those ids to file the recording
  under each speaker's OWN voice-type folder, which is where the engine looks.
* **A speaker with no mouth plays the file as a sound.** Doors, activators, a
  script no actor carries and `Player->Say` cannot lip-sync, so the export also
  mints a SOUN (`MWSaySound<id>`) for such a line and `SayLine` falls back to
  `Sound.Play` at the reference. Measured in the exports: Morrowind.esm mints
  92 Say topics and 3 Say sounds, TR_Mainland 144 and 3.

`say_formid.txt` carries the way back:
`path=Plugin.esm|TOPIC FormID|seconds|SOUN id`, keyed by the path lowercased
with `/` folded to `\`. The topic is `00000000` for a line no actor speaks.

`SayDone` has no engine counterpart, so it is answered from `seconds` -- the
recording's real length, read at import from the staged file
(`asset_convert/audio/mp3_length.py`, or `wave` for a PCM wav) -- and only
falls back to a subtitle-length estimate when that is 0.

🛑 **An actor must not greet while a `Say` is in flight** -- the CK wiki records
a crash to desktop for exactly that overlap, which is why `sayDone` is answered
from the line actually started rather than assumed finished.

A voiced line on a TOPIC, greeting or persuasion INFO is not played: 0 of
78,096 such lines carry a recording across Morrowind.esm, Tamriel Rebuilt and
Tamriel Data, and OpenMW leaves the same spot a `// TODO play sound`.

## <a id="spell-commands"></a>The spell commands

**Code:** `tes_runtime/morrowind/plugin/script_ops_spell.cpp`,
`game_calls_spell.cpp`, and `tes5_import/dialogue/morrowind_sidecar.py`.

A TES3 script names a **SPEL id**; the record Skyrim casts is the SPEL the
import minted, which `SPEL.txt` maps the way `SOUN.txt` maps a sound:
`id=Plugin|FormID|i,j,k`, where the trailing list is every TES3 effect INDEX
the spell carries (TES3 allows at most 8; the measured corpus maximum is 8).
`RemoveEffects` and `GetSpellEffects` both read that list.

`MGEF.txt` is `index=Plugin|FormID|Name`, and it carries **both** keys on
purpose: `GetEffect` names an effect (`sEffectRecall` is the export's
`MW038Recall` with the `MW` and the three index digits taken off) while
`RemoveEffects` names the TES3 **index**. One direction would leave the other
command unable to resolve its argument.

🛑 **A row is staged by the plugin that OWNS the record, never the plugin
reading it.** A FormID's index byte is a position in *that* plugin's master
list, so the same MGEF is `00986913` in the Morroblivion compatibility patch
and `01986913` as TR_Mainland sees it. Pairing TR's own name with the id it
reads sends `Game.GetFormFromFile` to the wrong file. The owner is the export
whose master count equals the id's index byte — measured over the TR chain,
all 143 effects belong to the compat patch, and none to TR_Mainland.

Measured over the Tamriel Rebuilt chain (`export/Tamriel Rebuilt 25.08.12`):
2,103 spell ids resolve to a SPEL and 143 effects to an MGEF. The built
`TR_Mainland.esm` carries 425 SPEL, 114 MGEF and 617 ENCH records of its own;
the rest resolve to `Morrowind_ob.esm`, which is why the table is built through
the LOADED plugins and never from this plugin's export alone.

🛑 **The earlier audit said this was blocked on the export, and it was wrong.**
`docs/audits/mwscript_opcodes.md` listed 978 `addspell`/`removespell`/
`getspell`/`hasspell` sites and 673 effect sites as "no `SPEL.txt` is
exported". `tes4_export/record_types/morrowind_magic.py` has exported all
three types for as long as the file has existed. Nothing needed building on
the export side; only the sidecar table and the opcodes were missing.

| Native | 1.6.659 | id |
|---|---|---|
| `Actor.AddSpell` | `0x988ad0` | 54652 |
| `Actor.RemoveSpell` | `0x988940` | 54647 |
| `Actor.HasSpell` | `0x988bc0` | 54653 |
| `Actor.HasMagicEffect` | `0x988a00` | 54649 |
| `Actor.DispelSpell` | `0x9889a0` | 54648 |
| `Spell.Cast` | `0x9bb750` | 55747 |

Each was read off its registration site's `lea` beside the name string, the
class confirmed from the `lea r8` string (`Actor` for five, `Spell` for
`Cast`), and inverted through the Address Library. All six exist in all 12
shipped versionlibs — the check the angle getters failed
([above](#the-angle-getters-have-no-id)).

`Spell.Cast(caster, target)` is a MEMBER function on the SPEL, so the spell
form is `self` and both actors ride as arguments. `ExplodeSpell` passes the
same reference for both, which is exactly what it means: the object casts the
spell at itself.

🛑 **`addspell` is `"cz"`, and the `z` pushes NOTHING.** OpenMW's `'z'` runs a
`DiscardParser`, so the optional second argument is consumed at compile time
and never reaches the stack. Popping it would take the spell id off instead
and every `AddSpell` would name the wrong form. `script_test.cpp` runs both
`AddSpell "x"` and `AddSpell "x" 1` followed by a `ModDisposition`, so a
handler that pops the wrong number of arguments fails the test rather than
the play session.

🛑 **The FIRST argument pops FIRST.** `ExprParser::parseArguments` parses left
to right onto a `std::stack` and then emits it LIFO, so the last argument's
code is written first and the first argument ends up on top at runtime. For
`Cast spell target` that means the SPELL pops before the target — the reverse
of the reading that "a stack reverses things" suggests. The same unwind is
what puts an explicit `id->` target on top, which `Explicit::Target` relies on.

### <a id="removeeffects-is-per-spell"></a>`RemoveEffects` removes SPELLS, not one effect

`RemoveEffects index` is not "remove this one effect". UESP is explicit:
it *"removes all spells currently affecting the calling actor that include
the given effect"* — a set of SPELLS, selected by an effect they contain.
`RemoveSpellEffects spell` is the same operation narrowed to one spell.

Both map onto the engine exactly, with no widening. The sidecar stages every
TES3 effect index a spell carries, so the runtime knows which spells qualify;
each is then `DispelSpell`ed and `RemoveSpell`ed. The removal matters as much
as the dispel: a TES3 ability re-applies itself while it is on the spell
list, which is why `player.removespell` is the console's own way to end one.

🛑 **An earlier build called `DispelAllSpells` here and claimed the engine
could not dispel one effect. That was wrong** — `Actor.DispelSpell`,
`Actor.RemoveSpell` and `ActiveMagicEffect.Dispel` all remove a single thing,
and the console's `player.removespell` does it interactively. The native is
no longer resolved at all.

`GetEffect` and `GetSpellEffects` answer through `Actor.HasMagicEffect`:
`GetEffect` on the named effect, `GetSpellEffects` on ANY effect the spell
carries. A spell whose owner exported no effect data (every Morroblivion-owned
one) answers false rather than guessing.

## <a id="soul-gems"></a>The soul gem commands

**Code:** `plugin/script_ops_spell.cpp`, `game_calls_spell.cpp`,
`tes4_export/record_types/morrowind.py` (`filled_soulgems`).

`AddSoulGem creature gem`, `RemoveSoulGem creature`, `HasSoulGem creature` and
`DropSoulGem creature` all key on the **trapped creature**, never on the gem
tier. Measured over the TR corpus, every one of the 215 call sites passes a
creature id — `HasSoulGem "atronach_storm"`, `AddSoulGem "TR_m3_ATMG_terror"`.

Two sidecar tables carry what that needs:

- `CREA_soul.txt` — `creature id=soul size`, straight from the export's
  `DATA.Soul`, which is already Skyrim's 1..5 enum.
- `SLGM.txt` — `gem id_Filled<n>=Plugin|FormID`, the synthesized filled
  variants ([why they are synthesized](tes4_export_morrowind.md#filled-soul-gems)).

`AddSoulGem creature gem` therefore resolves the creature's soul size, picks
that gem's `_Filled<n>` record, and adds it. The query and removal commands
scan the actor's inventory for any filled gem whose `SOUL` equals that size.

🛑 **A filled gem's id is an item too.** `FindItem` falls back to the
`SLGM.txt` table, so `AddItem`, `GetItemCount` and `Drop` resolve
`Misc_SoulGem_Petty_Filled1`. Without that fallback, `AddSoulGem` handed
`AddItem` an id it could not resolve (`item 'misc_soulgem_petty_filled1' does
not resolve`), and the headless tester's `HasSoulGem` read 0 straight after.

Measured limit: `GetItemCount` does not see an `AddItem` made in the same frame.
So a `DropSoulGem` or `RemoveSoulGem` in the same script as the `AddSoulGem`
finds no gem, while one a frame later works. Vanilla Morrowind.esm never uses
`AddSoulGem` or `DropSoulGem`. Its 42 `RemoveSoulGem` calls take a gem the
player has long carried.

🛑 **A soul gem is recognised by its ID PREFIX**, `misc_soulgem`, exactly as
OpenMW's `mwclass/misc.cpp:isSoulGem` does it — TES3 has no soul-gem flag.
Measured over TR_Mainland plus the Morroblivion patch: 6 MISC records match
that prefix and 3 more merely contain "soulgem", so matching the NAME would
convert three things that are not soul gems in Morrowind either.

🛑 **`CREA_soul.txt` is keyed by every spelling a script may write.** In
Morroblivion mode the export's id is ESCAPED — `ogrim` is `0Ogrim` — so each
raw TES3 creature id is ENCODED and looked up, never inverted: the escape maps
`_` and ` ` onto `U` and `S`, so a real `U` is indistinguishable from an
escaped `_`. This is the same rule `stage_sound_table` follows.

Measured over the TR chain: 1,529 creatures carry a soul, and 37 of the 46
creatures the corpus names resolve. **The other 9 resolve to nothing, and no
table can fix it** — `atronach_storm`, `golden saint`, `winged twilight` and
the rest are among the 79 meshes `MORROBLIVION_CREATURES` pairs to vanilla
Oblivion creatures, so the gap patch deliberately supplies no CREA record for
them and they have no `DATA.Soul` anywhere. A `HasSoulGem` on one answers 0.

## <a id="teleport-effects"></a>Mark, Recall and the Interventions

**Code:** `plugin/game_calls_teleport.cpp`, `tes5_import/dialogue/morrowind_teleport.py`,
`record_types/crime.py:anchor_rows`, `record_types/magic_morrowind.py:MW_TELEPORTS`.

**Status: built, NOT yet confirmed in game.**

TES3 effects 60–63 have no Skyrim archetype. Each converts as a **Script
effect with no VMAD**, which is vanilla's own inert effect: Skyrim.esm has 57
Script MGEFs with no script and **0** Value Modifiers whose actor value is -1.
The runtime does the work, following OpenMW's `spelleffects.cpp`: only when the
player is the TARGET, and only while `isTeleportingEnabled`. Otherwise the caster,
if it is the player, sees `sTeleportDisabled`.

**Seeing the effect land: the VM's own OnMagicEffectApply sink.** `SkyrimVM`
is a `BSTEventSink<TESMagicEffectApplyEvent>`. Its subobject sits at +0xA8,
with the vtable at id 217097 (`0x19127c8` on 1.6.1170, `0x17bc0d0` on
1.6.659), found by matching each SkyrimVM vtable's RTTI locator offset to that
base class. Slot 1 is `ProcessEvent` (id 53978), which loads the
`OnMagicEffectApply` string. The event layout comes from the VM's argument
functor (vtable `0x1912ba8`, slot 1): the target is at +0, the caster at +8, and
the MGEF's runtime FormID at +0x10, which the functor looks up and checks for
form type 0x12. The runtime swaps slot 1, forwards every event, and posts a
teleport that lands on the player to the next frame. 🛑 A `HasMagicEffect` poll
cannot replace this: all four effects are NoDuration and are gone before a
tick sees them.

**Mark and Recall.** Mark stores the runtime FormID of the interior's CELL or
the exterior's WORLDSPACE, plus the position and Z angle. It is saved as a
`P` co-save line and re-resolved through SKSE's `ResolveFormId` on load.
Recall is one `MoveTo_Impl` into that place
([why a cell or worldspace](#positioncell-moves-into-the-cell)).
`DisableTeleporting` and `EnableTeleporting` set `DialogueState::teleporting`,
saved as a `T` line.

**The Interventions are `World::getClosestMarker`.**

- Outdoors, OpenMW takes the Divine or Temple markers in the player's
  worldspace on the smallest square ring of 8192-unit cells around the player.
  A marker in the player's own cell wins at once. Otherwise ties go to the
  first marker met walking the ring's edge SW → SE → NE → NW.
- Indoors, a marker in the player's own cell wins. Otherwise the search starts
  from where the interior opens onto the world. That is the crime pass's door
  walk, the same `anchors` TESRuntime's jail search reads, staged as
  `anchors_formid.txt`.
- Two differences from OpenMW remain. OpenMW's breadth-first search also finds
  a marker in an INTERMEDIATE interior before reaching a door outside. And the
  anchor walk starts from every exterior door at once, so the hop count is the
  same but a tie can resolve to a different door.

**No marker has to be persistent.** The move goes into the WORLDSPACE at the
marker's authored position, so the reference itself is never resolved. That
matters, because none of them is persistent. Measured:

- Morrowind.esm places 11 markers, 6 Divine and 5 Temple, all exterior and
  all non-persistent.
- The TR chain places 37: TR's own 23 plus Morrowind_ob's 14. Two of
  Morrowind_ob's markers stand in other worldspaces.

`markers_formid.txt` is `plugin|ref=kind|place plugin|place|x|y|z|zRot`.

**The runtime knows only Morrowind's own four effects.** `teleports_formid.txt`
(`plugin|MGEF=index`) lists the chain's `MW060`–`MW063` and every delivery
clone a plugin's conversion made of them (`TES4MW060MarkFFSelf` and kin).
Morroblivion's scripted stand-ins are not recognized. The compat patch
overrides Morroblivion's spells, enchantments and potions with vanilla's
effects instead
([restored magic](tes4_export_morrowind.md#restored-magic)).

**The refusal is vanilla's own.** `TribunalMain` is Tribunal's start script. It
calls `DisableTeleporting` in "Sotha Sil," cells and names no record, so the
patch stages it as a start script. That covers what Morroblivion's scripts
refused by hand: the `SothaSSil` cell, and `fbmwTRSothaSil` stage 100. Stage
100 is Almalexia's death, which happens inside those same cells.
`BloodmoonMain` (Mortrag Glacier) is started by Hircine's quest script, not an
SSCR, so Morroblivion's own quest would have to start it.

### <a id="divine-intervention-in-any-world"></a>Divine Intervention in any world

`markers_formid.txt` gathers four sources:

- the chain's own Divine and Temple markers;
- the places a restored Intervention's replaced script sent the player. The
  patch writes them on the override as `InterventionTargets`. Morroblivion's
  Divine Intervention names 8 Cyrodiil chapels and 8 shrines, and its Almsivi
  Intervention names 6 temples. They resolve by EditorID in the converted
  masters;
- Skyrim.esm's temples (`morrowind_temples.py`). A temple is an `LCTN` tagged
  `LocTypeTemple`: 10 locations, 11 door exits. The landing spot is each door's
  own `XTEL` arrival point, in the worldspace of the door it leads to. Only
  plugins whose loaded masters include no TES3 plugin and not the patch stage
  these rows, so they appear once per load order;
- `worlds_formid.txt` (`plugin|child=plugin|parent`, 34 child worldspaces in
  Skyrim.esm, plus each export's `WNAM.Parent`). A walled city shares its
  parent's coordinates, so the search compares ROOT worldspaces. A cast in
  Tamriel can land in Whiterun's temple, and a cast in Anvil can land in
  Anvil's chapel.

Almsivi Intervention stays Temple markers only, so outside Morrowind it does
nothing, as in vanilla.

### <a id="adding-a-runtime-effect"></a>Adding a runtime effect (the remaining missing ones)

Still inert: the `NATIVE_NONE` entries of `MW_EFFECT_ARCHETYPES`. Those are
Disintegrate Weapon/Armor 37/38, Detect Enchantment/Key 65/66 and ExtraSpell
126. Each converts as an inert Value Modifier (the teleports as a script-less
Script effect), and every effect record it lands as already reaches the
runtime's apply sink. Adding one takes these steps, in this order:

1. **Runtime.** An instant effect is handled by index in
   `game_calls_teleport.cpp:OnTeleportEffect`, following OpenMW's
   `spelleffects.cpp` for that effect. It runs on the game thread, posted from
   the `OnMagicEffectApply` sink, for an effect landing on the PLAYER only;
   widen the `target == PlayerRef()` test if OpenMW applies it to actors too.
   🛑 The sink fires only when the effect STARTS. A duration effect doesn't
   use it: the tick reads its magnitude off the active-effect list, the way
   [Levitate and SlowFall](#levitate-and-slowfall) do, and never needs an end
   signal.
2. **Import.** Add the index to `MW_RUNTIME_EFFECTS` in
   `tes5_import/record_types/magic_morrowind.py`. That one set drives
   everything downstream:
   - `teleports_formid.txt` (`teleport_lines`) lists the effect's MGEF and each
     delivery and Ability clone (`copy_editor_ids`), so the runtime recognizes
     it. The file keeps its old name.
   - `mw_converts` now counts the effect as working, so a record carrying it
     can be restored.
3. **Morroblivion mode needs no new code, but it needs the patch rebuilt.**
   `morroblivion_magic.restored_magic` overrides each Morroblivion record
   whose vanilla effects the runtime now carries: a scripted stand-in that
   passes the dependency check, or Oblivion effects approximating the
   runtime-only one (Buoyancy and Swimmer's Blessing fake SwiftSwim with
   Water Breathing + Feather or Fortify Athletics). Adding SwiftSwim and
   Levitate flipped 18 SPEL, 11 ALCH and 8 ENCH, among them all six
   `JDLevitation*Script` spells: their helper `JDLevitate` is `Call`ed only
   by them, so it is dropped with them.

   🛑 Rebuild in this order, or nothing flips:
   `--build-morrowind-patch "<Morrowind>/Data Files"` (or, once built,
   `-f Morrowind-Morroblivion-Compatibility.esp --export-only --import-only`), then `--import-only` for Tamriel_Data.esm and TR_Mainland.esm, and
   Morrowind.esm for the authored path. The patch now writes the delivery
   copies its new overrides need, so the dependents adopt them: that moved 23
   of Tamriel_Data's copies and 1 of TR_Mainland's, which a save only feels
   as a running effect on one of them dropping.
4. **Check what flipped.** `restored_magic` returns only the records it
   overrides, so compare that list with the census in
   [restored magic](tes4_export_morrowind.md#restored-magic). If a record you
   expected stays, read its script before loosening the check: a stand-in that
   advances a quest, or keeps a global other scripts read, must stay.

### <a id="levitate-and-slowfall"></a>Levitate and SlowFall

**Code:** `plugin/game_calls_flight.cpp`, `plugin/ids.h`, `OpSetLevitation` in `plugin/script_ops_control.cpp`

Levitate and SlowFall are confirmed in game. 🛑 **SwiftSwim is NOT: re-check
it** (the open lead is at the end of this section).

SwiftSwim, Levitate and SlowFall are script-less Script effects, runtime-carried like the
teleports, so `teleports_formid.txt` lists every copy of them
([what the records carry](tes5_import_magic.md#runtime-effects-read-the-active-effect-list)).
No vanilla actor value is borrowed: a first build that parked them on the limb
conditions broke the player's jump.

The object tick (`Hooks().effectTick`, 30 Hz, unpaused play only) sums each
one's magnitude off the player's active-effect list (`ActiveMagnitude`).
That's the walk `Actor.HasMagicEffect` does: MagicTarget at `actor+0xA0`,
list from its vtable slot 7, the MGEF at `[[effect+0x48]+0x10]`, magnitude
`+0x78`, and flag `0x8000` at `+0x7C` for an effect its conditions switched
off. The tick also:

- reads the player's character controller;
- computes OpenMW's fly speed: `fMinFlySpeed + 0.01 × (Speed + magnitude) ×
  (fMaxFlySpeed − fMinFlySpeed)`, reduced by `fEncumberedMoveEffect × load`,
  and 0 when overloaded.

All of it goes into atomics.

The physics runs through Bethesda's character states. Each state's vtable
slot 8 is `simulate(state, controller)`:

- InAir's (`0xf01f40` on 1.6.1170) adds gravity to the controller's velocity
  at `+0x90`, scaled by the step's seconds at `+0x88`. It also raises the
  fall start at `+0x240` to the highest z reached, which is what landing
  charges damage from.
- Flying's is an empty `ret`.

The runtime swaps InAir's and OnGround's slot 8 for a wrapper. The wrapper
calls the original, then, for the player's controller only:

- **Levitate** replaces the velocity with the one OpenMW's flight moves at:
  the move keys (PlayerControls `+0x24` strafe, `+0x28` forward), turned by
  the player's yaw, with forward following the pitch. No key means hover. On
  the ground it acts only when the velocity points up, which is liftoff;
  level movement there is the ground state's walk. While levitation is
  disabled the player drops, and `sLevitateDisabled` shows once, as in
  OpenMW's `shouldRemoveEffect`.
- **SlowFall** is OpenMW's `movementsolver.cpp` recurrence. After gravity, a
  falling z and the x/y drift each keep `(1 − 0.005 × magnitude)` per
  60 Hz step. The runtime raises that to the step's length, so the frame
  rate doesn't change it.
- Both reset the fall start to the current height each step. OpenMW calls
  `land()` every step while flying or slow-falling, so a fall only counts
  from where the effect ended.

How each address was found, and that 1.7.104 reads the same offsets, is in
the comments in `ids.h`. PlayerControls' data block sits at `+0x24` because
its input sink zeroes that block, then hands `this+0x24` to every handler.
MovementHandler writes strafe at `+0` and forward at `+4` of it.

**SwiftSwim** wraps the Swimming state's slot 8 (`0xf02b30`, id 80110; vtable
id 240836). That simulate rotates the stroke at controller `+0x70` into the
velocity at `+0x90` (via `0xf3d670`), then adds buoyancy. For the player the
wrapper scales the stroke by OpenMW's `1 + 0.01 × magnitude` for the
original's read and restores it after, so buoyancy is untouched and nothing
compounds. The log shows `flight: SwiftSwim now N`, then one
`flight: swim stroke (…) ×S -> velocity (…)` on the next moving stroke.

🛑 **Open: SwiftSwim feels no faster.** In game, 60 points logged
`SwiftSwim now 60` and a ×1.60 stroke, yet swimming felt unchanged. Every
logged stroke is a UNIT vector (`(0.852, 0.523, 0)`), and `0xf3d670` opens
with a cross product and its squared length, which looks like building a
direction frame. If it keeps only the stroke's direction, scaling it cannot
change the speed, and the speed comes from another controller field. Unproven:
compare the logged velocity's length with and without the effect, then find
the field the speed comes from.

Limits: the player only (OpenMW flies and swims any actor with the effect),
and the player keeps Skyrim's falling animation while levitating.

`DisableLevitation` / `EnableLevitation` set `State().levitation`, which is
saved in the co-save as a `V` line. `TribunalMain` disables levitation in
Mournhold and Sotha Sil.

### <a id="sanctuary"></a>Sanctuary

**Code:** `plugin/game_calls_sanctuary.cpp`, `ApplyHook` in `plugin/game_calls_teleport.cpp`

Untested in game.

The dodge itself is a native perk: `MWSanctuaryPerk`'s entry `k` zeroes a
weapon hit `k`% of the time on an actor ranked `k` in `MWSanctuaryFaction`
([the records](tes5_import_magic.md#runtime-effects-read-the-active-effect-list)).
The runtime keeps the rank: each tick it sets every holder's rank to its
summed Sanctuary, rounded and capped at 100, calling `Actor.SetFactionRank`
only when the rank changes.

The holders are the player, every tick, and every actor the apply sink has
seen a Sanctuary land on. An actor leaves the list when its Sanctuary is gone,
at rank 0. The list is saved in the co-save as `Y` lines, so a holder loaded
from a save is still ranked. An id that no longer names an actor (form type
`0x3E`) is never read. `effects_formid.txt` (`sanctuary=plugin|FormID`) names
the faction; a dependent plugin names its master's.

🛑 **Every TES3 plugin stages its whole loaded chain's markers and anchors.**
Morrowind_ob.esm is a TES4 plugin and stages no Morrowind sidecar, so its
markers reach the runtime only through a dependent. Rows repeated across
sidecars are harmless. Measured anchors: 1,128 for Morrowind.esm and 4,623
over the TR chain.

### <a id="brawls"></a>Brawls

**Code:** `plugin/game_calls_brawl.cpp`, `IsBrawler` in `plugin/object_script.cpp`,
`NoteHit` in `plugin/game_calls_query.cpp`; `tes5_import/actors/brawl.py`

**Switched off** (`kBrawlsOn`): Skyrim's Stamina regeneration outruns the punch drain, and
the drain and regeneration a brawler gets are not chosen yet
([run log](../audits/headless_quest_runs.md#raven-rock-open)).

A TES3 punch drains fatigue and leaves health alone (OpenMW
`getHandToHandDamage`); a Skyrim punch takes health. Scripts that stage a
fistfight count on the first. A brawler is an object script that declares
`OnPCHitMe` and reads `GetFatigue`. Three scripts match across Morrowind,
Bloodmoon, Tamriel_Data and TR_Mainland, all fistfights:
- `HroldarScript`: beaten at fatigue 150, then 50. Hroldar has 40 health and
  300 fatigue.
- `TR_m4_NPC_Anbarnag`
- `TR_m4_NPC_Sarus_Savrethi`

Only those actors change. Every other actor keeps Skyrim's punch, and a
weapon or spell still wounds a brawler.

- The import writes `TES3BrawlAbility` for a TES3 source. Its perk is a Mod
  Incoming Damage entry ×0 whose attacker is `GetIsID Player` and whose
  weapon is `GetIsID Unarmed` (`0x1F4`). Both tabs follow vanilla's
  `DeftMovement` and its weapon-tab `GetIsID`. `effects_formid.txt`
  (`brawl=plugin|FormID`) names it.
- The tick gives the ability to a brawler's reference when it first loads.
- `NoteHit` sees each player punch on a brawler (hit source `0x1F4`) and takes
  the player's `UnarmedDamage` from the brawler's Stamina, which `GetFatigue`
  reads.

## <a id="the-control-switches"></a>The player-control switches

**Code:** `plugin/script_ops_control.cpp`, `plugin/game_calls_control.cpp`,
`DialogueState::SetControlEnabled`.

TES3 keeps seven booleans and a script flips each by name: `playercontrols`,
`playerfighting`, `playerjumping`, `playerlooking`, `playermagic`,
`playerviewswitch`, `vanitymode`. All seven start **enabled** — OpenMW's
`ControlSwitch::clear()` — and they persist across a save, so they live in
`DialogueState` and round-trip through the co-save as `W` records.

The compiler registers all 21 commands (enable, disable, getdisabled) in a
**loop** over its own `controls[]` table, `opcodeEnable + i` and its two
siblings. The install mirrors that with a runtime loop over the same indices,
because an index that drifts lands a handler on a neighbouring switch and
fails silently — the command runs and the wrong boolean moves. A
`static_assert` pins `kControlSwitchCount` to `Compiler::Control::numberOfControls`.

### Skyrim takes them eight at a time

`Game.DisablePlayerControls` and `Game.EnablePlayerControls` each take eight
bools and a POV int, so a change pushes the WHOLE set rather than one flag.
The mapping, from the callback's own stack reads at `[rsp+0x50]`..`[rsp+0x80]`:

A switch is mapped ONLY where the engine flag does the same thing:

| TES3 switch | Skyrim flag |
|---|---|
| `playercontrols` | `abMovement` |
| `playerfighting` | `abFighting` |
| `playerlooking` | `abLooking` |
| `playerviewswitch` | `abCamSwitch` |
| `playermagic`, `playerjumping`, `vanitymode` | none — NOT INSTALLED |

🛑 Those three reach no flag, so they are not installed at all. `playermagic`
is TES3's SPELL-drawing switch — OpenMW writes it to `mSpellDrawingDisabled`,
beside `playerfighting` as `mWeaponDrawingDisabled` — while the CK wiki
defines `abFighting` as "the player's combat controls", one flag for all of
it, so honouring magic through it would block melee too.

A flag reaches only the call that ACTS on it — enable takes the flags that are
on, disable the flags that are off — so the pair never overwrites what the
other just wrote. `abSneaking`, `abActivate`, `abMenu` and `abJournalTabs` are
passed false to both, which leaves them exactly as the player had them.
`abActivate` is not passed even with `abMovement`: the wiki records that
disabling movement already disables activation, and our own hook gates on the
switch directly.

### `playercontrols` gates our own activation hook

🛑 `ActivateHook` answers **before** the engine sees the activation, so
Skyrim's `abActivate` never gets the chance to stop one we have already
claimed. Without an explicit gate, a Morrowind speaker opens the dialogue menu
during a tutorial that has taken the player's controls away — which is exactly
what Arktwend's monastery chargen does. The hook tests the switch and returns
true without opening, claiming the activation either way so the speaker cannot
fall through to Skyrim's own dialogue menu. OpenMW gates identically, in
`ActionManager::activate()`. That gate belongs to the player's input, so a
script's own `Activate` passes it
([`ScriptActivationScope`](#onactivate-claims-the-activation)).

### Only `EnableRaceMenu` has an equivalent

`Game.ShowRaceMenu()` is the one chargen menu Skyrim can open. Name, class,
birthsign, the stat review and the levelup menu have none, so they stay stubs
that say so. Address Library ids, verified on 1.6.1170 and 1.6.659: 55580
(`ShowRaceMenu`), 55454 (`DisablePlayerControls`), 55455
(`EnablePlayerControls`), each the registration callback under script `Game`.

### The audit could not see any of this

`mwscript_opcode_audit.py` read only `extensions0.cpp` for name arrays, but
`controls[]` is declared in `opcodes.hpp` as `inline constexpr`, and the
getter's name carries a **suffix** (`get + controls[i] + "disabled"`) the loop
patterns did not model. All 21 commands were absent from the registration list
entirely, so the audit reported 487 commands rather than 508 and could not
report the family either way. Fixed by widening `_ARRAY` to the
`inline constexpr` form, threading the header in beside `extensions0.cpp`, and
letting a loop name carry an inline prefix and a trailing suffix.

## <a id="npc-animation-files"></a>An NPC's own animation file (2026-10-07, PENDING in-game confirmation)

**Code:** `plugin/npc_anim.cpp`, `plugin/game_calls_anim.cpp`
(`InstallActorAnimCalls`); the clips and the table come from
`asset_convert/havok/npc_anim_morrowind.py`
([asset_convert_animation.md](asset_convert_animation.md#morrowind-npc-animation-files)).

The converter stages `SKSE/Plugins/MorrowindRuntime/<plugin>.anims.txt`, BESIDE
the plugin's sidecar folder and not inside it: the import sweeps every file in
that folder it did not write itself, and this one is written by the creature
stage. Three row kinds: `clip.<slug>.<group>=event|seconds`,
`actor.<npc id>=slug|c2,..,c9|distance` (the wander package's eight idle
chances and its distance; no chances and 0 without one) and
`place.<placement FormID>=plugin|npc id|x,y,z,rx,ry,rz` (the authored
placement, angles in degrees).

Each tick the driver finds the bound placements now in the world (the same
cell-change-or-heartbeat sweep the script instances use), and ten times a
second runs OpenMW's `AiWander` choice for each: `getRandomIdle` fails outright
above `fIdleChanceMultiplier`, then each idle rolls against its chance and the
highest passing roll wins. A group the file defines is played by sending its
graph event; with none chosen the file's own `idle` group plays, which is the
default pose Morrowind shows between idles. Every clip loops in the graph and
the driver times its end, so an NPC whose only idle is its seat never stands
between cycles; an empty choice has to last half a second before the actor
leaves the clip state.

- **A playing actor is HELD on a TranslateTo glide.** In game (2026-10-07) the
  clips played but the NPCs turned, drifted, and sat in the air or away from
  their tables. Morrowind seats an NPC by standing it inside the chair
  (`ANFTaverneNpc11` is 28 units from `furn_com_rm_chair_03`'s origin, at floor
  height), Skyrim's character controller pushes it out and up, and the
  converted wander package is a Sandbox that walks it off. A reference under
  translation is moved by the translation alone, so while a clip plays the
  actor is slid to its goal in 0.25 s whenever it is more than 3 units or 2
  degrees off, then kept on a glide that never arrives (1 unit over 10,000 s,
  renewed every 2 s). The goal is the authored placement (the `place.` row's
  pose) when the wander distance is 0, else where the actor stood when the
  clip began. Leaving glides to where the actor already is, which ends it.
- **A wanderer walks when its roll gives no idle** (`Wander_MoveNow`): an NPC
  whose package has a distance is released for 8 seconds.
- **Fighting lets go at once**, and so does moving for an actor not yet held:
  the clip state is a root state of the humanoid graph with no locomotion.
- **The table is searched every 5 ticks for 5 seconds after a cell change.** A
  cell's references resolve some frames after the player's cell changes; one
  search on that tick found nothing and the next was the heartbeat, so the
  NPCs stood for five seconds and then dropped into their seats.
- **The event is sent through `NotifyAnimationGraph` itself**, slot 1 of the
  holder at `ref+0x38`, which is the whole body of `Debug.SendAnimationEvent`
  (id 55380). The native throws the result away, and "no graph took it" is what
  an actor whose 3D is still loading answers; the driver asks again on the next
  evaluation.
- **The playing event is repeated every 2 seconds.** The engine sends its own
  `IdleStop` and nothing reports it; a repeat while the state is active is a
  self-transition the machine ignores.
- `PlayGroup` and `LoopGroup` on such an NPC play the named group the same way
  for `loops + 1` cycles, ahead of the object path.

Not carried: the base animation's own `idle2`..`idle9` for NPCs with no file of
their own, a group's Loop Start/Loop Stop split, `Sound:` cues inside a group,
and `SkipAnim` on an actor. An actor knocked into ragdoll while seated is pulled
back by the next repeat.

## <a id="object-animation"></a>`PlayGroup`, `LoopGroup` and `SkipAnim` on objects

**Code:** `plugin/script_ops_anim.cpp`, `plugin/game_calls_anim.cpp`,
`common/gamebryo_sequence.*`; the sequences come from
`asset_convert/nif/object_anim_morrowind.py`
([asset_convert_animation.md](asset_convert_animation.md#morrowind-object-animation)).

The queue is OpenMW's `CharacterController::playGroup`/`updateAnimQueue` for a
non-actor: one group plays and one waits. Mode 0 queues behind the current
group and modes 1/2 start at once; `PlayGroup` loops forever and `LoopGroup n`
plays n+1 cycles. A queued group lets a looping one finish the cycle it is in.
The same looping group asked for again keeps playing, which is how a banner
script calls `PlayGroup Idle2` every frame. A one-shot holds its last pose,
and `Idle` is the mesh's `AutoPlay`/`AutoLoop` pair. A looping group plays
`<Group> Intro` once, then its loop, then `<Group> Outro` once.

🛑 **Starting a sequence is not enough: the manager must be flagged active.**
`ObjectReference.PlayGamebryoAnimation` (1.6.1170 `0xa2ead0`) does three
things after `NiControllerSequence::Activate`:

- `manager.flags |= 0x8` (NiTimeController active, +0x10);
- `MarkChanged(0x10000000)`, the reference's animation change flag (vtable +0x50);
- `TESForm` slot 0x24 with `true`, which sets form flag 0x2 (vtable +0x120).

Only the first matters while playing. The engine clears the active bit on
load: the banners shipped flags `0x4C` and read `0x0044` in game before a play,
`0x004C` after. Without the flag, every sequence started `active` and the
banners stood still with nothing logged. Setting it is what made them blow
(confirmed in game, Bal Foyen). The manager is the 3D root's first controller
(+0x18), as the native reads it.

`SkipAnim` holds the ambient pair still for each tick a script calls it, and
never a scripted group (OpenMW's `isScriptedAnimPlaying`). It stops the
sequences, which leaves the pose where it was, and resumes them with
`Activate` without start-over. The sequence's time is `time + offset` (+0x6c)
fed through ComputeScaledTime (`0xd93be0`, last time +0x50, weighted time
+0x54). Deactivate folds the time played into the offset, so a resume
subtracts the time spent held, or the animation jumps ahead. The object tick
runs at 30 Hz against a faster frame rate, so nudging the offset every tick
would jitter. Only TR's Necrom undercroft fan uses it on an object.

NPCs and creatures are not handled: they are Skyrim actors with Skyrim
behavior graphs, not Morrowind animation groups.

## <a id="crime-is-the-engines"></a>Crime is the engine's: bounty, fines, jail, arrest

**Code:** `plugin/game_calls_crime.cpp`, `plugin/script_runner.cpp`
(`OpSetPCCrimeLevel`, `OpPayFine`, `OpGoToJail`), `plugin/object_tick.cpp`.
Not yet confirmed in game.

PC Crime Level is the crime gold Skyrim keeps on a faction -- the speaker's
crime faction (`Actor.GetCrimeFaction`), else the realm's from
`crime_formid.txt` -- so a bounty the engine records for a witnessed crime is
the one the guards' `Greeting 0` lines test. `SetPCCrimeLevel`/`ModPCCrimeLevel`
write it (violent part cleared, the rest set). The `DialogueState` copy only
stands in when no faction resolves.

The writes go through `RunOnGameThread`, so a script on the main thread
writes at once. When they were always POSTED, `SetPCCrimeLevel 100` then
`ModPCCrimeLevel 50` read the engine's old 0 and left 50; the headless tester
found it (`mwtest`, case `crimelevel`).

The guards' lines also test and print five globals the engine keeps
(`PCHasCrimeGold`, `PCHasGoldDiscount`, `CrimeGoldDiscount`,
`CrimeGoldTurnIn`, `PCHasTurnIn`). `UpdateCrimeGlobals` in `conversation.cpp`
refreshes them the way OpenMW's `World::updateDialogueGlobals` does: when
dialogue opens and after every answer. `%Global` in a line looks through every
global the speaker's plugin declares, not only the ones written so far. The
vendored `defines.cpp` no longer caches that list, because which globals are
visible depends on the speaker's plugin.

The fine and jail opcodes follow OpenMW (`miscextensions.cpp`): `PayFine`
clears the bounty and confiscates stolen goods -- the dialogue has already
taken the gold with `Player->RemoveItem Gold_001`, so `PlayerPayCrimeGold(true,
false)` pays nothing and only confiscates -- `PayFineThief` only clears the
bounty, and `GoToJail` is `SendPlayerToJail(false, true)`: TES3's jail keeps
the player's inventory. The jail itself is TESRuntime's nearest one
([tes_runtime_crime.md](tes_runtime_crime.md#nearest-jail)).

As in OpenMW's `World::goToJail`, `GoToJail` only marks the player for jail,
and the crime tick sends them once the conversation has closed, so the
guard's line can be read first. TES3 then serves the whole sentence at once
(OpenMW's `JailScreen`), so the tick calls `Game.ServeTime` as soon as
PlayerCharacter `+0x720` names the faction. TESRuntime then keeps the stolen goods
([tes_runtime_crime.md](tes_runtime_crime.md#serve-time)).

| Native | id |
|---|---:|
| `Game.ServeTime` | 55573 |
| `Actor.GetCrimeFaction` | 54921 |
| `Faction.GetCrimeGold` | 55794 |
| `Faction.SetCrimeGold` | 55809 |
| `Faction.SetCrimeGoldViolent` | 55810 |
| `Faction.PlayerPayCrimeGold` | 55805 |
| `Faction.SendPlayerToJail` | 55807 |

### The arrest force-greet is diverted

[The dialogue menu is the wrong hook](#why-not-the-menu) for activation, because
a Morrowind NPC has no Skyrim dialogue. A pursuing guard is the exception: the
import gives every Morrowind guard one blank ForceGreet (`PFGT`) line gated on
`IsGuard`, so the engine does open "Dialogue Menu". The tick asks
`MenuTopicManager::GetSpeaker` (id 35293, 1.6.1170 `0x5e1dd0`; it releases its
own temporary handle and returns a raw pointer) and, when the speaker's base is
a Morrowind speaker, closes Skyrim's menu and opens the Morrowind conversation
with that guard, whose crime greeting now passes.

## <a id="licensing"></a>Licensing

OpenMW is **GPL-3.0**, vendored from 0.52.0 (`b4b1c5ae`). This follows the
pattern `external/pynifly_hkx/` already established: the project's own code is
MIT, everything under `external/` carries its own license.

MorrowindRuntime is **its own DLL**, like every runtime under `tes_runtime/`:
its own source folder, built by `tes_runtime/build.bat` into `tes_runtime/dist/`
and shipped in the same `TESRuntime.zip`. That boundary is what keeps the
license contained. `CreatureRuntime.dll`, `FalloutRuntime.dll`,
`HavokWorldSize.dll` and `TESGameBridge.dll` never link OpenMW code, so they
stay MIT; linking OpenMW into any of them would make that whole binary
GPL-3.0. `TESRuntime.dll` links none of the vendored tree, but its
`tes/alchemy.cpp` ports OpenMW's `Alchemy::applyTools` line for line, so
that file is GPL-3.0-derived and the TESRuntime binary with it
([tes_runtime_alchemy.md](tes_runtime_alchemy.md#alchemy-apparatus)).

The boundary runs one way only. MorrowindRuntime compiles the MIT sources in
`tes_runtime/common/` (`skse_abi.h`, `addresses.*`, `log.*`, `paths.*`) like
every other runtime: MIT code may be distributed inside a GPL binary, and those
files stay MIT in the source tree. What must never happen is the reverse, a
`common/` file including anything under `external/openmw/`.

## <a id="character-sheet"></a>The character sheet: stats window and level-up dialog (2026-09-29, unconfirmed in game)

**Code:** `plugin/stats_sheet.cpp`, `plugin/levelup_menu.cpp`,
`plugin/leveling.cpp`, `plugin/menu.cpp` (`CustomMenu`),
`plugin/menu_widgets.cpp`; the movies and `plugin/stats_layout.h` from
`tools/generators/gen_morrowind_stats_swf.py`. The Morrowind-only first step of
[the character sheet plan](../plans/character_sheet.md).

Two more windows, built and registered exactly as the dialogue window is:

| Menu name | Movie | Layout |
|---|---|---|
| `MorrowindStatsMenu` | `Interface/morrowind_stats.swf` | `openmw_stats_window.layout`, 500 x 342 |
| `MorrowindLevelUpMenu` | `Interface/morrowind_levelup.swf` | `openmw_levelup_dialog.layout`, 440 x 496 |

**More than one menu.** `menu.cpp` now keeps each window's state in a
`CustomMenu` (up to four). MenuManager calls a creator with no argument, so each
slot has its own creator function; every window's engine object carries one
vtable, and the object keeps a pointer to its window at `+0x40`, past the base
`IMenu` (0x30) in the part of the 0xa8 allocation only a MessageBoxMenu's own
code would touch. The dialogue window's free functions (`OpenMenu`,
`SetMenuText`, ...) are its `CustomMenu`'s, unchanged.

**Only with Morrowind content.** `plugin.cpp` installs the sheet only when the
store loaded a sidecar, so an Oblivion or Fallout game never sees the key or
the level-up step.

**On by default, one switch.** `[CharacterSheet]` in
`SKSE\Plugins\MorrowindRuntime\MorrowindRuntime.ini` (source
`tes_runtime/morrowind/MorrowindRuntime.ini`, packaged by
`package_runtime_dll.py` as `HavokWorldSize.ini` is) ships `Enabled=1` and
`SkillCap=1`; a missing ini or key also means on. `Enabled=0` turns off the
windows, the level-up step, the buffs and the cap together
([sheet off](#sheet-off)); `SkillCap=0` drops only the [cap](#skill-cap).
`Hotkey` is a decimal virtual-key code, as `FalloutRuntime.ini`'s keys are, 67
(C) when absent.

**The default hotkey is C, and it works in the perks menu.** The sheet opens
only from Skyrim's perks menu ([perks button](#perks-button)), so the key need
only be free there. Skyrim's own `interface/controls/pc/controlmap.txt` binds C
(scan code `0x2E`) in two contexts, Main Gameplay (Auto-Move) and Item Menus
(Item Zoom); the perks menu's context, Stats, binds only `Rotate` (the left
stick) beside Menu Mode's keys. The first build used K, which the same file
binds nowhere, while the sheet still opened in the world. The key is read with
`GetAsyncKeyState` on the shared fixed tick (`main_tick`, every 33 ms, paused
or not), only while this process owns the foreground window; the key again,
the button or Tab closes the window.

**What the stats window shows.** Health, Magicka and Fatigue (Skyrim's Stamina)
as `current/maximum`, the maximum being the current value over
`GetActorValuePercentage`, drawn as `menu_bar_gray` tinted with
`Morrowind.ini`'s `color_health`, `color_magic` and `color_fatigue` and covered
past the value as the disposition bar is. Then Level, Reputation and Bounty; the
eight attributes and 27 skills from the stat store (`ActorAttribute`,
`ActorSkill`), so what the window shows is what persuasion, the filter and
scripts read; the skills under their specialization, by name; and the player's
factions with the rank name. Every label is a GMST (`sHealth`, `sSkillLongblade`,
`sSpecializationCombat`, ...).

**Rows are fields the plugin places.** A list's rows are single-line fields at
18 px, OpenMW's row, moved by `_y` as the dialogue window's topic list is. The
embedded face's own line pitch is 16-17.4 px, so a multi-line field would drift
from the layout.

**The level-up dialog** is OpenMW's `LevelupDialog`: the class image for the
level's combat/magic/stealth increases (`getLevelupClassImage`, all 21
`textures\levelup` images in the movie, one shown), `sLevelUpMenu1` with the
level, `sLevelUpMenu2`, three gold coins (`icons\tx_goldicon.dds`), and the eight
attributes in two columns with `xN` beside any that would rise by more than 1.
A click spends a coin (the last one moves once all are spent), the value shows
the result, and OK stays disabled until the coins are spent. It has no cancel,
as Morrowind's own has none.

### <a id="perks-button"></a>The perks menu's Character button (confirmed in game 2026-10-03)

**Code:** `plugin/perks_button.cpp`, `plugin/stats_sheet.cpp` (`Tick`),
`plugin/menu.cpp` (overlay menus, `EngineMenuOpen`, `VisibleFrame`);
`perks_button` in `tools/generators/gen_morrowind_stats_swf.py`.

The sheet opens from Skyrim's perks menu (`StatsMenu`) and nowhere else. While
that menu is open a key hint, **[C] CHARACTER**, sits in the screen's
bottom-left corner, in the empty end of vanilla's bottom bar. Clicking it or
pressing the key opens the sheet over the perks; the hint then reads **BACK TO
PERKS**, and the click, the key or Tab returns to them. The key cap shows the
configured `Hotkey`, drawn at 4x so it stays sharp up to a 4K screen.

Measured in game on the user's 3440×1440 screen (log, 2026-10-03): the
button placed at stage (-202, 666), so the visible stage starts 202 px left
of the stage's own edge, and a click on it closed the sheet.

**How the engine hands input to stacked menus** (1.6.1170):

| Path | Who gets it | Rule |
|---|---|---|
| Mouse down/up | `ClickHandler` (`0x9499c0`) posts to the pseudo-name "Top Menu" | the dispatcher (`0xfa4191`) starts at the topmost menu whose depth is **below 14** (`0xfa3dc0`, cutoff `0xe`), then walks DOWN the stack while each menu returns 2 ("pass on"); a menu with flag `0x10` (modal) stops the walk (`0xfa4525`) |
| The perks menu's rotation and perk clicks | its own `MenuEventHandler` (`CanProcess` `0x961030`) | runs only while the topmost menu with depth **below 6** is the perks menu itself |
| The perks menu's movie | `StatsMenu::ProcessMessage` type 6 (`0x95fb39`) | only key events; mouse events never reach its movie, so a button cannot live inside it |
| Whether the cursor shows | the cursor routine (`0xfa5fd9`-`0xfa60f5`) | the topmost menu with depth **below 11** and without flag `0x10000` decides: flag `0x4` (uses cursor) opens the Cursor Menu, anything else closes it |

IMenu's constructor defaults the depth to 3, and `StatsMenu`'s never changes
it; the Cursor Menu sits at 13 (`0x913d25`). Hence the two depths:

- **The button is an overlay at depth 12**: flags 0 (no pause, no cursor, not
  modal), input context `0x13` (none), and it returns "pass on" for every
  event. It gets clicks (the log shows mouse types 1-3 arriving), yet both
  the perks menu's below-6 input test and the below-11 cursor rule skip it,
  so rotating, buying perks and the cursor all behave as in vanilla. The first
  build put it at 10 with no cursor flag: it became the cursor rule's menu and
  the cursor vanished from the perks menu, except while the sheet (which has
  `0x4`) was open.
- **The sheet opens at depth 4 over the perks.** That makes it the below-6 top
  menu, so the perks stop reacting to clicks meant for the sheet, and being
  modal it keeps Tab for itself. At 10 the perks menu would pick a perk under
  the sheet's every click.

**A click on the button stops there.** Reported in game: clicking the button
with the mouse also started closing the perks menu. Passing events on is not
what leaked: the perks menu's mouse buttons come through its own
`MenuEventHandler`, and `MenuControls` (`0x947db0`) offers every input event to
every registered handler whose `CanProcess` agrees -- it records the result
and moves on, so no handler can consume an event for the rest. So the plugin
swaps that handler's `CanProcess` (slot 1 of `StatsMenu`'s second vtable, id
215975; the original is id 52518): while the button shows and the movie's
own mouse is on it, a mouse BUTTON event (device 1 at `+0x8`, type 0 at
`+0xC`) is refused. Moves, the keyboard and the controller still reach the
perks.

**Always the bottom-left corner.** The movie loads with show-all scaling,
which centers the 1280×720 stage, so on an ultrawide or 16:10 screen the
stage's corner is not the screen's. On every open the plugin reads
`GFxMovieView::GetVisibleFrameRect` (slot `0x1F`, skse64's
`ScaleformMovie.h`) and places the button at its left edge plus 18, centered
in the 72 px bar above its bottom edge. Show-all fits one side of the stage
exactly, which says whether the rectangle came in pixels or twips; the
placement is logged once.

**The perks menu is found by name**, `MenuManager::IsMenuOpen` (id 82074,
`0xfa37b0`, SKSE's own address), with the name interned once. SE 1.5.97 and VR
have it too (`0xebe150`, `0xf1a3b0`): they inline the table lookup that AE
calls, so its body matches nothing there and `pre_ae_map` finds no anchor, but
the sleep/wait toggle calls it with the menu manager on all three builds, and
it ends in the same `test byte [menu+0x1c], 0x40`
([hand-proven](../reference/address_library_formats.md#hand-proven)). The
input gate below resolves on SE too (its `CanProcess` by masked bytes, the
vtable by its class); VR's perks menu is a different one, so it has neither.

### <a id="capped-skills"></a>A capped skill is named red in the perks menu (2026-10-03, unconfirmed in game)

**Code:** `plugin/perks_skills.cpp`.

While the perks menu is open, every quarter second, each skill the cap holds
([skill cap](#skill-cap): at or past its governing attribute) has its name
turned red in the menu's own movie, and turned back when it no longer is.

- **Which label is which skill.** The perks menu keeps its skills' actor
  values in label order at `+0x50` (count `+0x60`). Its fill (`0x962450` on
  1.6.1170, `0x8c20c0` on SE) hands entry *n*'s level, name and color to
  `UpdateSkillList`, which builds
  `_root.StatsMenuBaseInstance.AnimatingSkillTextInstance.SkillText<n>.LabelInstance`
  as `NAME <font … color='…'>LEVEL</font>`.
- **Only the name changes.** The name carries no color of its own, so the first
  color in the label's `htmlText`, read back, is the name's. That one value is
  replaced and the rest written back as read, so the level keeps vanilla's
  green or red. The red is vanilla's own for a lowered skill, `#FF0000`
  (`0x97b0b0` picks `#FF0000`, `#189515` or `#FFFFFF`).
- **Rebuilds.** The game rewrites a label when it rebuilds the list; the next
  pass reads the name's own color again and paints it again.
- **A string read back is the movie's**, so it is released through
  `ReleaseManaged` (id 82270, `0xfac750`), as the fill releases its own.
- Not on VR: its perks menu has no `UpdateSkillList` and no such labels.

### <a id="controller"></a>Controllers: the engine's own stick cursor (confirmed in game 2026-10-03)

**Code:** `plugin/menu.cpp` (`kMenuFlags`, `OnUserEvent`'s Accept),
`plugin/perks_button.cpp` (`PollPad`).

Our menus are driven by the mouse, and Skyrim already turns a controller into
one. `controlmap.txt`'s **Cursor** context (9) binds the `Cursor` event to the
mouse (`0xa`) AND the right stick (`0x000c`), and `Click` to the left mouse
button AND A (`0x1000`). The Cursor Menu pushes that context when it refreshes
(`0x913f30`: pop every 9, push one unless the current context binds `Cursor`
itself, as the Map Menu's does) and handles `Cursor` in its own
`MenuEventHandler` (`CanProcess` `0x913ec0` compares the event with `Cursor`,
the input string at `+0x180`; the thumbstick slot calls `0xfba6c0`, which moves
the cursor). `ClickHandler` turns `Click` into a mouse press for the top menu.
The input lookup (`0xcd5020`) walks DOWN the context stack (ControlMap `+0x108`,
count `+0x118`), so a binding lower down still applies when nothing above
binds that input: B stays Menu Mode's `Cancel`.

Vanilla menus set the cursor flag only without a gamepad (`StatsMenu`:
`0xcd9100` on the input device manager, then `or [menu+0x1c], 4`); ours set it
always (`kMenuFlags`), so the cursor and its context come up with a controller
too. Hence, with no controller code of their own:

| Controller | Does |
|---|---|
| Right stick | moves the cursor |
| A | clicks (`Click`); where Menu Mode is higher on the stack it is `Accept` instead, which our menus also take as a click at the cursor -- except the class menu, which takes typed text and whose Enter is also `Accept` |
| B | `Cancel`: closes the window, as Tab does |

**Into the sheet: Y in the perks menu.** With a controller the perks menu has
no cursor (its own gamepad check above), so the button cannot be clicked. Y
opens and closes the sheet there instead: `controlmap.txt` binds `YButton`
only in Item Menus, and the perks menu's Stats context binds only `Rotate`.
It is read through XInput (`xinput1_4`, then `1_3`, then `9_1_0`), the API
the game's own gamepad device uses, only while the button shows; the key cap
reads **Y** while a controller is connected. The engine's own gamepad check
was not used for the label: its id (443396) exists only on 1.6.1170, and its
body matches nothing on 1.6.659 and twice on 1.7.104.

### <a id="leveling"></a>Leveling: Skyrim levels, Morrowind raises the attributes

Skyrim keeps its skills and decides when the player levels (the skills menu,
+10 Health/Magicka/Stamina, a perk). The runtime adds Morrowind's step:

- **Skill increases.** Every ~330 ms out in the world, `SampleLeveling` reads
  the BASE of Skyrim's 18 skills (`Actor.GetBaseActorValue`, id 54678). A rise
  is credited to the governing attribute and specialization of the skill's
  namesake Morrowind skill, read from the SKIL table: One- and Two-Handed are
  Long Blade (Strength), Archery Marksman, Pickpocket and Sneak Sneak
  (Agility), Speech Speechcraft (Personality), Smithing Armorer, and so on
  (`kTracked` in `leveling.cpp`). A fortify never moves a base, so it is never
  counted.
- **What is not an increase.** The first read of a game only records. A change
  of race (`Actor.GetRace`, id 54930, compared by pointer) re-records without
  crediting, because the race menu's skill bonuses move bases. A skill that
  drops (Legendary) is followed from its new value.
- **Level-ups.** A level gained is a pending step. Once the skills menu and
  everything else is closed, the dialog opens for the oldest pending level.
- **Gains.** `iLevelUpNNMult` for the attribute's increases, at most 10
  counted (Morrowind.esm: 2, 2, 2, 2, 3, 3, 3, 4, 4, 5), 1 with none, never
  past 100: OpenMW's `getLevelupAttributeMultiplier` and `onOkButtonClicked`.
  Luck governs nothing, so it always rises by 1. Taking the step resets the
  credits, as `NpcStats::levelUp` clears `mSkillIncreases`.
- **Storage.** DialogueState variables under `leveling|player`, so the progress
  rides the co-save; the attributes are the stat store's, which `SetStrength`
  and its kin already write.

Not yet decided or known: a trainer, a skill book or a script's
`SetLongBlade` in Skyrim moves a base too, so it counts as an increase; and the
player's attributes start at the `player` record's, whatever race was chosen.

### <a id="attribute-buffs"></a>What the attributes do (2026-10-02, unconfirmed in game)

**Code:** `plugin/attribute_buffs.cpp`. One rule: a buff is its normal amount
times (0.5 + attribute / 100), so 50 plays like vanilla.

| Attribute | Buff | When |
|---|---|---|
| Intelligence | Magicka from each Magicka pick | at the step, from the base attribute |
| Endurance | Health from each Health pick | at the step |
| Agility | Stamina from each Stamina pick | at the step |
| Strength | carry weight from each Stamina pick | at the step |
| Willpower | `MagickaRateMult` + (Willpower − 50) | always |
| Speed | `StaminaRateMult` + (Speed − 50) | always |
| Luck | `CritChance` + (Luck − 50) / 10 | always |
| Personality | nothing: Speech owns prices | |

- **The pick's amount** is Skyrim.esm's: `iAVDhmsLevelUp` 10, and
  `fLevelUpCarryWeightMod` 5 with Stamina. The step counts a pool's picks as
  its rise in base since the last step, divided by 10, and pays them at the
  attributes the step just set: OpenMW's `NpcStats::levelUp` reads the raised
  base Endurance ("If you increased Endurance this level, the Health increase
  is calculated from the increased Endurance", `npcstats.cpp:230`). Never
  retroactive.
- **The regen values** are the multipliers vanilla's Fortify Regenerate
  effects move (Skyrim.esm MGEF actor values 156 and 157, 10 effects each).
  No vanilla MGEF or script writes `CritChance`. **Unverified:** whether the
  engine's critical roll reads it, and the player's base critical chance.
- **Magic on a pick buff** lasts as long as the effect: each point is 1/100 of
  a pick for every pick of that pool, so Fortify Intelligence 10 with 20
  Magicka picks is +20 Magicka.
- **Held, not written once.** Each buffed value's base is moved by what its
  target changed since the last hold, and what is held is kept under
  `buffs|player`. A pool's raw base (base less what is held) is what picks
  are counted from, so the buff's own points are never a pick.

### <a id="attribute-tooltips"></a>Attribute tooltips (2026-10-02, unconfirmed in game)

**Code:** `plugin/stat_tip.cpp`; `gen_morrowind_stats_swf.tip_tags`. OpenMW's `AttributeToolTip`
(`openmw_tooltips.layout`), in both the stats window and the level-up dialog,
as OpenMW gives both: a `HUD_Box_NoTransp` box, 8 px padding, the attribute's
`icons\k` icon at 32 px with its name beside it, and the description wrapped
below. Both movies carry the pieces at the stage origin; the box is three
sprites (top, a 1 px middle stretched to the description's measured
`textHeight`, bottom), so it fits the text as OpenMW's auto-sized box does.
Placement is `ToolTips::position`: 32 px under the pointer, shifted left by the
pointer's share of the screen width, kept on screen, and above the pointer at
the bottom edge.

The description is Morrowind's own, cut down to what the attribute does here:

| Attribute | Morrowind | Here |
|---|---|---|
| Strength | starting Health, carrying, max Fatigue, melee damage | Affects how much you can carry. |
| Intelligence | Determines your maximum amount of Magicka. | Affects your maximum amount of Magicka. |
| Willpower | resist magic, max Fatigue | Affects how quickly your Magicka returns. |
| Agility | dodge, hit in melee, max Fatigue | Affects your maximum Stamina. |
| Speed | Determines how fast you can move. | Affects how quickly your Stamina returns. |
| Endurance | starting Health, Health per level, max Fatigue | Affects your Health gain per level. |
| Personality | `sPerDesc`, unchanged | |
| Luck | Affects every action you do in a small way. | Affects your chance of a critical hit, and every other action in a small way. |

Personality reads its GMST, so a translated Morrowind keeps its language; the
changed lines are English.

### <a id="skyrim-skill-list"></a>The skill list is Skyrim's (2026-10-02, unconfirmed in game)

**Code:** `plugin/stats_sheet.cpp` `BuildRows` / `kSkillGroups`;
`asset_convert/ui/skyrim_skills.py`.

The player's skills ARE Skyrim's, so the stats window lists Skyrim's 18, in
the three groups Skyrim's own skills menu draws (warrior, mage, thief) under
Morrowind's `sSpecializationCombat/Magic/Stealth` headings, each group by
name, with the actor value's current value. (The first sheet copied OpenMW's
list of Morrowind's 27 skills, against [the plan](../plans/character_sheet.md#m2-swf);
that was wrong.) The kept legacy skills join the list when they are built.

The names and descriptions are Skyrim's own, in the install's language:
`package_runtime_dll.py` reads the 18 skill AVIFs from the player's Skyrim.esm
(FormIDs 0x44C..0x45D, actor value 6 first; Illusion's is `AVMysticism`), looks
their FULL and DESC string ids up in `strings\skyrim_<sLanguage>.strings` /
`.dlstrings` from the Skyrim BSAs, and ships `skyrim_skills.txt`
(`av=name|description`) beside the menus. Nothing of Bethesda's is committed;
without the table the runtime shows English names and no description.

### <a id="skill-tooltips"></a>Skill tooltips (2026-10-02, unconfirmed in game)

**Code:** `plugin/stat_tip.cpp` (`StatTip`, which also shows the attribute
tooltips); `gen_morrowind_stats_swf.tip_tags`. OpenMW's `SkillToolTip`
(`openmw_tooltips.layout`) laid over a Skyrim skill: its icon (the nearest
Morrowind or Oblivion skill picture, `SKILL_ICONS` / `OB_SKILLS`), Skyrim's
name, `sGoverningAttribute`: "Governing Attribute: X" (the attribute the cap
and the level-up credit use, `GoverningAttribute`), Skyrim's description, then
`sSkillProgress` ("Progress towards skill increase") over a red bar reading
`NN/100`, or `sSkillMaxReached` alone at 100. Hovering a skill row shows it;
headings show nothing, and faction rows show the
[faction tooltip](#faction-and-level-tooltips).

**The progress** is `PlayerSkills` data's `points / pointsMax` for the skill.
The pointer's offset in PlayerCharacter differs by build (0x9b8 on 1.6.1170,
0x9c0 on 1.7.104), so it is read from `PlayerCharacter::AdvanceSkill`'s own
`mov rcx,[rcx+disp32]`; the 12-byte `{level, points, pointsMax}` entries from
+0x08 are what `PlayerSkills::AdvanceSkill` adds to and compares (`ids.h`, read
on both builds).

### <a id="faction-and-level-tooltips"></a>Faction and level tooltips (2026-10-03, unconfirmed in game)

**Code:** `plugin/stat_tip.cpp` (`FillFaction`, `FillLevel`);
`stats_sheet.cpp` `HoverTip`. Both are OpenMW's (`StatsWindow::updateSkillArea`
and `onFrame`, `FactionToolTip` and `LevelToolTip` in `openmw_tooltips.layout`),
in the same box as the attribute and skill tooltips, with no icon.

- **A faction row**: the faction in the header color, the rank under it, then
  while a next rank exists `sNextRank` and its name, the rank's two attribute
  requirements, `sFavoriteSkills`, and `sNeedOneSkill` / `sand` /
  `sNeedTwoSkills` with the rank's skill levels. The favored skills are named
  as the **Skyrim** skills the player's value is read from
  (`SkyrimSkillsOf`, the same table `ActorSkill` and the rank filter use), so
  a split skill such as Long Blade lists One-handed and Two-handed, each once.
  The text needs two colors, so the movie's `TipText` is an HTML field and
  every tooltip writes it through `htmlText`.
- **The Level row**: `sLevelProgress` over the red bar, then one centered
  "Attribute xN" line per multiplier above 1 (`AttributeGain`). OpenMW's bar
  counts major and minor skill increases toward `iLevelUpTotal`; here Skyrim
  decides the level, so the bar is Skyrim's experience: PlayerSkills data
  opens with `{levelPoints, levelPointsMax}` at +0x00 and +0x04 (skse64's
  `PlayerSkills::Data`), before the per-skill entries at +0x08.

### <a id="race-attributes"></a>Race starting attributes, retroactive (2026-10-02, unconfirmed in game)

**Code:** `plugin/leveling.cpp` `ApplyRaceStart`; `attribute_buffs.cpp`
`RescorePickBuffs`; `morrowind_sidecar_source.race_lines` → `RACE.txt`.

The player starts from the race's own attributes (TES3 `RADT`, by sex), as in
Morrowind. The player picks a race in Skyrim's RaceMenu, so `RACE.txt` is keyed
by the SKYRIM race a player of each TES3 race wears, through the converter's
own chain (`RACE_FORMIDS` → `TES4_RACE_FID_TO_EDID` → `RACE_MAP`), and again by
that race's vampire race (Skyrim.esm `<Race>RaceVampire`), so turning vampire
does not reset anything. A race no playable Skyrim race stands for has no row.
The sex is `TESNPC::GetSex`'s own read (bit 0 of +0x38, both builds).

Every sample compares the start the race and sex now give with the start the
player's base was last built on (`start0..7` in the leveling state; before
any, the `player` record's own). A difference moves the BASE by exactly that
much, so every level-up gain is kept, and **re-scores every pick buff that
attribute already earned** as if it had always stood there:
`picks × perPick × delta / 100`, the same linear rule a Fortify uses. A chosen
class's favored attributes move the same way ([chargen menus](#chargen-menus)).
These are the only retroactive changes: a level-up or a script's
`SetStrength` never re-scores.

### <a id="menu-styles"></a>Menu styles: Morrowind's look or Skyrim's (2026-10-02, unconfirmed in game)

**Code:** `asset_convert/ui/menu_art.py`, `morrowind_menu_art.MorrowindArt`,
`skyrim_menu_art.SkyrimArt`; the three generators take the art object;
`plugin/menu_widgets.cpp` `Colors`.

Every menu movie (dialogue, stats, level-up) is built from ONE art object, and
the two objects have the same members: `compose_frame`, `compose_box`,
`compose_head`, `compose_cap`, `compose_button`, `compose_scrollbar`,
`compose_thumb`, `compose_line`, `compose_stat_bar`, `compose_bar`, plus
`colors`, `background`, `cover` and `icons`. Changing a menu's look is changing
which object it is built with; the layout, the field names and the plugin's
hit rects do not move.

Two choices, made apart, both in `conversion_config.json` and in Settings ▸
Menu style:

- **The look** (`menuStyle`): `morrowind` uses Morrowind's frame art (never
  committed); `skyrim` draws everything: thin light rules that fade at their
  ends (the caption's rule parts round the title), translucent black panels,
  shaded meters, white text that greys when disabled.
- **The icons** (`menuIcons`): `morrowind` (`icons\k`, `textures\levelup`, the
  gold coin) or `oblivion` (`textures\menus`: `level_up\attributes_icons`,
  `class\attributes\load_image_*_small`, `level_up\class_creation`, whose tall
  portraits on a transparent 512 px square are cropped to the painted part
  and fitted whole into the 2:1 picture box). Each Skyrim skill shows its
  nearest picture (Two-handed as Axe or Blunt, Pickpocket as Sneak,
  Enchanting as Enchant or Mysticism).

**Where the art comes from: the installs, by content.** Every registered game
install (`source_registry.directories`, never an imported mod) is indexed once
(`menu_art.GameFiles`: its loose files, its Morrowind-format archives and its
Oblivion-format v103 ones; Fallout's and Skyrim's are skipped by version), and
an install supplies a game's art when it holds that game's marker texture
(`menu_thick_border_top.dds`, `attributes_icon_strength.dds`) -- whatever its
plugins are called. So Arktwend's Data Files supply Morrowind's art and
Nehrim's supply Oblivion's; the scan takes about a second here. The GUI greys
out a choice no install can supply, and fills its cascade on first open so
startup pays nothing. An unset choice (or one whose art is missing on this
machine) builds from what is there: Morrowind's look and icons when found,
else Skyrim's look and Oblivion's icons. `package_runtime_dll.py` reads both
keys (`--menu-style`, `--menu-icons` override them).

**The plugin's colors follow the movie.** The runtime colors names, topics and
buttons itself (hover, disabled, headers), so both palettes are generated into
`menu_layout.h` as `kMorrowindColors` / `kSkyrimColors`. A Skyrim-style movie
carries an empty `SkyrimStyle` sprite; each menu checks for it when it opens
(`_root.SkyrimStyle._x` reads only when the sprite exists) and colors with the
matching palette. A movie and its palette therefore always agree, whichever
style the user packaged.

**The font stays MysticCards in both styles.** The plugin lays text out with
that face's advance table (`menu_layout.h` `kAdvance`: dialogue page breaks,
the level-up values beside their names), so a second face would need a second
table chosen at runtime. Skyrim's own `$EverywhereMediumFont` (imported from
`gfxfontlib.swf`, which the first probe proved draws) is the candidate if the
look needs it.

#### <a id="menu-previews"></a>Previews without the game

`tools/generators/menu_preview.py --out DIR [--style skyrim] [--icons oblivion]`
renders every menu to PNGs: the dialogue window, the stats window bare and with
an attribute and a skill tooltip, and the level-up dialog mid-step. It draws
the MOVIE ITSELF -- it reads back the tags the generators wrote (bitmaps, the
bitmap- and solid-rect shapes, sprites, placements, text fields) -- so a
preview cannot drift from what ships. What the plugin would set at runtime
(texts, row positions, covers, which class image shows, where a tooltip sits)
is a sample STATE per instance name; the tooltip's state follows
`StatTip::Place`. Text is drawn in the same MysticCards face the movie embeds.

### <a id="sheet-off"></a>The sheet off

`Enabled=0`: the player's eight attributes read 100, so no TES3 gate shuts on
one, and Personality reads Skyrim's Speech, which persuasion and barter weigh
beside Speechcraft. The tick keeps running only to hand every held buff back,
so a save made with the sheet on plays as vanilla Skyrim. NPCs keep their
authored attributes either way.

### <a id="attribute-effects"></a>Attribute effects (2026-10-02, unconfirmed in game)

**Code:** `plugin/game_calls_attributes.cpp`; import
`magic_morrowind.MW_ATTRIBUTE_EFFECTS`, `magic_variants.build_av_variants`,
`morrowind_teleport.teleport_lines`.

Drain (17), Damage (22), Restore (74), Fortify (79) and Absorb (85) Attribute
convert as script-less Script effects, still one variant MGEF per attribute
(`TES4MW079FortifyAttributeLuck`). `teleports_formid.txt` names each variant
and its delivery and Ability clones as `index:attribute`. Before this they
landed on stand-in actor values (Fortify Willpower on `MagickaRate`, Fortify
Endurance on Health), which the buffs would have counted twice.

Each tick every actor's active effects are summed per attribute, as OpenMW's
`MagicEffects` does: Fortify adds and Drain takes away while they last; Damage
lowers the attribute by its magnitude every second until Restore gives it back
(`attrdamage|<id>`, so it survives a save). The stat reads add the result to
the base, so scripts, dialogue, persuasion and the buffs see it; a `Set`/`Mod`
writes the base. The player is summed every tick; an NPC from the moment the
`OnMagicEffectApply` sink sees an attribute effect land on it until nothing is
left, under its base NPC_'s TES3 id.

**Absorb** takes from its target and gives to its caster while the target
carries it. The apply event names the caster; the active effect has no caster
field we read, so the pair is kept by (target, attribute), and two casters
absorbing the same attribute of one target pay the later one.

The same table change fixed a gap in the older runtime effects: an Ability
clone (`ability_variant`) of Sanctuary or SwiftSwim was never listed, so a
constant ability carrying one did nothing.

### <a id="skill-cap"></a>The skill cap (2026-10-02, unconfirmed in game)

Morrowind's trainers refuse a skill at its governing attribute
(`sNotifyMessage17`, OpenMW `trainingwindow.cpp:186`, which reads the
fortified attribute). With `SkillCap=1` the runtime extends that to skill use:

- **Use:** PlayerCharacter vtable slot 247 (`AdvanceSkill`, id 40488) is
  swapped. Every use experience reaches it virtually, and so does
  `Game.AdvanceSkill`; no direct call to it exists on 1.6.1170. A capped skill
  gains nothing.
- **Trainers:** the training menu's train step (id 52667) is reached through
  its one call in id 52662. A capped skill is refused with Morrowind's line
  before any gold changes hands.
- **Books** (id 17842) and `Game.IncrementSkill` (id 55616) increment through
  other callers and still raise a capped skill, as in Morrowind.

The governing attribute is the namesake skill's, as the level-up credits use
([leveling](#leveling)).

**Gated on the whole chain.** A cap with no way to raise the attribute
blocks the skill forever, so it holds only when all of these are true:
- the sheet is on and `SkillCap` is not 0;
- the stats window and the level-up menu both installed, and the tick that
  opens the step is running (`InstallCharacterSheet` sets the cap last);
- the player's attributes have an authored start, either the sidecar's
  `player` NPC_ row or a race start from `RACE.txt`
  (`PlayerAttributesKnown`). Without one every attribute reads 0, which
  would cap every skill;
- the skill's namesake has a SKIL row naming its governing attribute.

### <a id="tes4-tables"></a>Every converted game with attributes (2026-10-02, unconfirmed in game)

**Code:** `tes5_import/actors/attribute_tables.py`,
`plugin/script_tables.cpp` (`AttributeGlobals`, `SkillCount`),
`plugin/game_calls_attributes.cpp` (`SyncAttributeGlobals`),
`plugin/leveling.cpp` (`SettleAttributeGlobal`)

The sheet installs whenever any loaded sidecar staged a SKIL table, not
only when a Morrowind sidecar staged dialogue. Every game uses the same
attributes, the same 18 Skyrim skills and the same formulas for now, so the
menus are unchanged.

**What a TES4 plugin stages.** The import writes these into
`SKSE/Plugins/MorrowindRuntime/<plugin>/`, in the Morrowind tables' own
formats, from the plugin's own records:

| File | Rows | Key |
|---|---|---|
| `SKIL.txt` | governing attribute, specialization, the two use values | the namesake TES3 skill (Blade is Long Blade's 5) through `TES4_SKILL_TO_MW`, the inverse of `MW_SKILL_TO_TES4`. Oblivion has no Enchant, so with no Morrowind installed Enchanting has no governing attribute: no credit, no cap |
| `RACE.txt` | male and female `ATTR` | the Skyrim race a playable race becomes, and its vampire race; a race Oblivion knows wins over a face-part stand-in |
| `NPC_.txt` | identity, level, the 8 attributes, the 27 TES3 skill slots | EditorID. A slot reads the TES4 skill it folds into; a creature's reads its Combat, Magic or Stealth skill by that skill's specialization, as OpenMW's creature does. Oblivion's `Player` row is the start a race moves |
| `attributes_formid.txt` | `strength=Plugin.esm\|FormID` | the player attribute globals this plugin itself holds |

**No actor index.** A TES4 sidecar must never write `NPC__index.txt`.
That table is what routes an NPC's activation to Morrowind dialogue.

**No GMST table.** Oblivion's ESM authors no `iLevelUp##Mult`; the exe
holds them. The runtime's fallback is Morrowind.esm's values
(`kLevelUpMult`: 2, 2, 2, 2, 3, 3, 3, 4, 4, 5), so the step gains the same
with or without Morrowind installed. It used to fall back to +1.

**Layering.** A master's folder sits below its dependents, so in
Morroblivion mode the compat patch's Morrowind rows win wherever both
games have a row. Oblivion's rows only fill gaps.

**The attribute globals.** Each sample tick (about every 330 ms in
gameplay), `SyncAttributeGlobals` does this for every listed global:
1. Reads the global.
2. If a script changed it since the runtime last wrote it, moves the
   player's BASE attribute by the difference.
3. Writes the attribute back into the global.

The last written value is kept in the co-save. After a load it matches the
global the save restored, so only a real script write registers. With the
sheet off, the attribute reads 100 and so does the global.
[Scripts and conditions](script_convert.md#player-attributes) read the
globals.

### <a id="tes4-attribute-magic"></a>TES4 attribute magic runs as Morrowind's (2026-10-02, unconfirmed in game)

**Code:** `magic_morrowind.TES4_ATTRIBUTE_EFFECTS`, `runtime_attribute_index`,
`magic_variants.build_av_variants`, `morrowind_teleport.copy_rows`

Oblivion's five attribute effects work by Morrowind's rules (UESP: Damage
Attribute takes its magnitude every second for the duration and holds until
restored). Each one becomes the same runtime effect:

| TES4 | TES3 |
|---|---|
| DRAT Drain | 17 |
| DGAT Damage | 22 |
| REAT Restore | 74 |
| FOAT Fortify | 79 |
| ABAT Absorb | 85 |

**Same records, same ids.** Each per-attribute variant (`TES4FOATStrength`)
keeps its EditorID and its hash key `('MGEF_AV', (code, av))`, so its FormID
does not move. Only its body changes: it becomes a script-less Script effect
with no actor value, as Morrowind's variants are. The plugin's sidecar lists
each variant and its delivery and Ability clones in `teleports_formid.txt`
as `index:attribute`.

**What changes in play.** Before, each attribute acted on a Skyrim stand-in:
Strength on CarryWeight, Endurance on Health, and so on
(`magic.ATTRIBUTE_TO_AV`).
- **On the player,** an effect now moves the sheet's attribute, and the buffs
  follow it.
- **On an Oblivion NPC,** an effect moves its stat faction rank while it
  lasts ([NPC attributes](#npc-attributes)). An actor in no stat faction
  (a vanilla Skyrim NPC) has none to move, and the tick drops it from its
  watch list.

Skill effects (FOSK and the rest) keep their Skyrim skills.

### <a id="pre-ae-builds"></a>SE 1.5.97 and VR 1.4.15 (2026-10-02, unconfirmed in game)

On these two builds the runtime loads a generated table of addresses
(`ids_pre_ae.h`, [pre-AE tables](../reference/address_library_formats.md#pre-ae-tables)).
It holds only the ids in `pre_ae_ids.txt`, the ones whose struct offsets were
checked on both builds. These features run:
- the character sheet, its Statistics tab and the level-up step;
- the perks menu's button (and on SE its input gate and red capped skills);
- the class and birthsign menus;
- the attribute globals that converted TES4 scripts and conditions read;
- the skill cap, on skill use and at trainers;
- attribute magic, Morrowind's and TES4's, on the player and (through
  `Actor.Get/SetFactionRank`) on TES4 NPCs. The same two natives let the
  Sanctuary tick hold its faction rank there too.

Everything else stays unresolved and off on those builds: Morrowind
dialogue and activation, object scripts' world commands, flight, crime and
travel. Per-build differences the code handles:
- `ActorField` puts Actor fields 8 bytes lower than on AE.
- VR arms the custom menu the way its own MessageBoxMenu is armed.
- The AdvanceSkill hook finds its vtable slot itself: 247, or 249 on VR.
- VR's TrainingMenu skill is at `+0x50`.
- The Scaleform load log is AE only.

VR menus take controller pointer input, and nothing here has been seen
working in VR yet.

### <a id="npc-attributes"></a>NPC attributes are faction ranks (2026-10-02, unconfirmed in game)

**Code:** `tes5_import/actors/stat_factions.py`; `TES4_Attributes.psc`;
`conditions._stat_faction_rank`; `plugin/game_calls_attributes.cpp`.

Skyrim has no attribute actor values, so each TES4 attribute, and each kept
skill (Athletics, Hand-to-Hand, Acrobatics), is a hidden conversion-owned
faction (`TES4AttributeStrength`, `TES4SkillAthletics`, ...). Every converted
TES4 NPC_ joins all eleven at its authored value; a creature joins the eight
attribute factions. The value is the data in the record, so it needs no DLL:
- a subject (NPC) condition on an attribute or kept skill is
  `GetFactionRank` on its faction; the player's run-on-target ones keep the
  attribute globals (and the kept skills their Skyrim stand-ins);
- a script reads and writes the rank (`TES4_Attributes.ReadActor`,
  `WriteActor`, `ModifyActor`, and `...Skill` for the kept skills). These
  decide at run time, since a reference variable can hold the player, who
  keeps the attribute globals and whose kept skills still read Skyrim's
  stand-ins (Stamina, UnarmedDamage). An actor in none of the factions (a
  vanilla Skyrim NPC) reads the old stub, 100, and a write joins it.
  `SetFactionRank` joins a non-member itself (ids.h).

**Magic on an NPC.** The runtime's attribute tick (attribute effects above)
sums a TES4 NPC's attribute effects as it does a Morrowind NPC's and holds
them on its ranks: each rank moves by what its effect changed since the last
tick, clamped to 0..127, and what is held is kept under `statfx|<FormID>`,
so the authored base is always the rank less it and a save carries both.
Damage is capped by that base. Speed writes still go through the walk
formula (`SpeedMult`), as the player's do.

**Membership makes nobody allies.** 200 of Skyrim.esm's 1,084 factions list
themselves as Ally (BanditFaction among them), which they would not need to
if sharing a faction did it.

**A rank is a signed byte**, so a value above 127 is stored as 127. About 60
Oblivion, Nehrim and Morroblivion creatures are authored above it (up to
255); every authored gate tests 100 or less.

### <a id="chargen-menus"></a>Class and birthsign menus (2026-10-02, unconfirmed in game)

**Code:** `plugin/chargen_menu.cpp`, `plugin/chargen_tables.cpp`;
`core/chargen_source.py`, `script_convert/context_setup.chargen_records`,
`message_menus.build_chargen_menus`; `tes5_import/actors/attribute_tables.chargen_lines`;
`tools/release/package_runtime_dll.chargen_table`; `core/gui/morrowind.add_chargen_menu`;
`tools/generators/gen_morrowind_chargen_swf.py`.

Two windows of our own, built like the level-up dialog: a class menu
(OpenMW's `PickClassDialog` and `CreateClassDialog` in one window, without
major and minor skills) and a birthsign menu (`BirthDialog`). The console
opens them with `showmenu MorrowindClassMenu` / `showmenu MorrowindBirthMenu`.

**One menu for every game.** A player can have several converted games
installed, and there is ONE class menu and ONE birthsign menu. Their rows
are the shared `SKSE/Plugins/MorrowindRuntime/chargen.txt`, which the
packaged runtime carries: `package_runtime_dll` builds it from the exported
game that `chargenSource` in conversion_config.json names (Settings ▸
Classes and birthsigns in the GUI, listing every exported plugin whose own
records hold a playable class or a birthsign, Fallout excluded). Unset, it is
the first such game whose table is complete: birthsigns, and every class
naming its favored attributes (an export from before the TES3 CLAS export
carried them reads -1). A menu that game has none of (Nehrim has no playable
classes) comes from the next game that has it. Each plugin's own `chargen.txt` names only its request and
choice globals and its own entries in menu order (`class.<i>=name`), so the
runtime can answer a script in that plugin's terms.

**One plan, masters included.** The importer, the script converter and the
shared table all read one plan, built from the plugin's own BSGN, CLAS and SPEL and
its direct masters', one row per record (a dependent's copy overrides). It
used to read the plugin alone, so Morroblivion, whose birthsigns and
playable classes are all Oblivion.esm's, converted `ShowClassMenu` and
`ShowBirthsignMenu` to nothing. A menu built only from the masters' records
adopts the master's MESG pages and choice globals by EditorID and does not
write them again, so no FormID moves; a TES3 source writes no birthsign
pages, which would come first and move its class pages' fixed ids.

**Who opens them.**
- A TES3 script's `EnableClassMenu` / `EnableBirthMenu` asks for the menu
  directly.
- A converted TES4 script calls `TES4_Chargen.Ask(TES4ChargenRequest, kind,
  choice)`. Each plugin owns its own `TES4ChargenRequest` global, so the
  runtime knows who asked. The script writes the kind (1 class, 2
  birthsign); the runtime answers by writing minus the kind, opens the menu,
  writes the choice global and clears the request. The choice is the asking
  plugin's own index of the pick's NAME + 1, so its `GetIsPlayerBirthsign`
  conditions still match; a pick the plugin does not list is -1, which `Ask`
  turns into 0 and returns -2. A request no runtime answers within a second
  returns -1 and falls back to the message pages, so the game plays without
  the DLL or with the sheet off; only then does the script grant the spells
  and write the choice itself. A request still taken after a load (the menu
  is gone, the script still waits) is opened again.
- Either menu waits until no other menu pauses the game, closing Skyrim's
  dialogue menu first, as `Message.Show()` did.
- A script often asks only while nothing is chosen yet: Morroblivion's
  `fbmwChargen` stage 2 runs `ShowClassMenu` under `if GetPCIsClass
  CharactergenClass` (the class its player record, `0x7` `CNAM`, starts in)
  and stage 3 `ShowBirthsignMenu` under `if GetPlayerBirthsign == 0`. The
  Skyrim player never has a TES4 class and OBSE's function had no conversion,
  so both menus were skipped. The player's class is the class choice global
  (`commands._player_class_test`: a listed class is its row; the record's
  starting class is "no choice yet", 0), and `GetPlayerBirthsign` is the
  birthsign choice global, 0 until a pick.

**Spells.** The runtime grants a chosen sign's spells for every game and
takes the last sign's back, as OpenMW rebuilds them. A row names each spell by
its TES3 id, or for a TES4 sign as `Owner.esm@FormID`, the owner read off the
sign record's own master list (a converted TES4 spell keeps its local FormID).

**The class menu.** The list holds the shared classes (by display name) and a
last "custom class" row (`sCustomClassName`). It opens on the player's class,
else the first row, as OpenMW's dialogs do. A class shows its
specialization, its two favored attributes, its description and its
picture: the level-up picture of its name, else its specialization's. A
custom class shows, where the description was, its name in an edit box
(OpenMW's `CreateClassDialog`: the `sName` label, the box, a caret) and the
eight attributes, of which two are picked as the level-up dialog's coins are.
Its specialization follows the picks: the one most of the skills they govern
belong to (the SKIL table), a tie going to the first pick's. OK needs both
picks and a name. The list scrolls by the wheel over its rows or its
scrollbar, by the arrows and track, and by dragging the thumb. Each window
keeps an 8 px margin on every side.

**Typing a name.** Typed through key-down events alone (key code at `+4`,
ASCII at `+8`, modifiers at `+0x10`, CommonLibSSE `GFxKeyEvent`), a name took
capitals only by rule, at each word's start; the case typed did not come
through. While the class menu is open, the runtime raises the game's text
input (`ControlMap::AllowTextInput`, a counter byte at `+0x128` on 1.6.1170,
`+0x120` on 1.5.97, `+0x140` on VR; see
[address_library_formats.md](../reference/address_library_formats.md#hand-proven)),
and the game then sends Scaleform character events (type 13, the character
at `+4`) with its case. Backspace and Enter stay on key events. Until the
first character event arrives, a key still types its own letter, so typing
works if character events never come. A name is limited to what fits beside
its label in the stats window. The co-save keeps the name as typed (`Q`
row); the chargen variables hold it lowercased, which older saves answer.

**The birthsign menu's spell list** is OpenMW's `BirthDialog::updateSpells`:
Abilities, Powers and Spells (`sBirthsignmenu1`, `sPowers`,
`sBirthsignmenu2`) each over its spells' names, each spell's effects under it
beside the effect's icon, written by `MWSpellEffect::updateWidgets`: an
ability's constant effects have no duration or range ("Fortify Personality 25
pts"), a power's and a spell's do ("Restore Health 2 pts for 30 secs on
Self"). `core/birthsign_text.py` writes the lines when the runtime is
packaged, into the shared table (`kind~icon~text`). A Morrowind sign's
spells come from the TES3 binaries with the game's own settings (the
export averages an effect's magnitude range); the effect names, units and
magnitude display types are OpenMW's (`getMagicEffectString`,
`getMagnitudeDisplayType`, the hard-coded effect flags). A TES4 sign's come
from its export: the MGEF's name, its last word replaced by the attribute or
skill it uses. Each effect icon is read from whichever install holds it
(Morrowind's `icons\`, Oblivion's `textures\menus\icons\`); the movie holds
each once per line, off the stage until the runtime moves one in. The movie
has 9 lines; the most any sign shows is 8 (Morrowind's Tower).

**What a class does.** Each favored attribute starts 10 higher (OpenMW's
`MechanicsManager::buildPlayer`), held apart from the race start so either
can change alone, and re-scored retroactively like a race. Major and minor
skills are not built. The specialization is kept for the sheet. The
choice rides the co-save under `chargen|player`.

**Pictures.** The class pictures are the level-up dialog's. Birthsign
pictures are every texture in the icon set's birthsign folder (Morrowind's
`textures\birthsigns`, or Oblivion's and Nehrim's `textures\menus\birthsign`),
keyed by sign with one rule shared by the movie and the sidecar
(`message_menus.birthsign_key`: `tx_birth_apprent` and
`birthsign_the apprentice` are both `apprentice`). The movie parks them off
the stage, and the runtime moves the chosen one in, since which pictures a
movie holds depends on the art installed.

### <a id="statistics-tab"></a>The Statistics tab (2026-10-02, unconfirmed in game)

**Code:** `plugin/stats_sheet.cpp`, `plugin/stat_rows.cpp`;
`tes5_import/actors/misc_stats.py`, `record_types/crime.bounty_rows`;
`script_convert/misc_stats.py`, `commands.pc_misc_stat`, `converter._mirror_page_stat`.

The stats window's right pane has two tabs above it: Skills (Skyrim's 18 and
the player's factions, as before) and Statistics. The box on the left holds
Level, Race and Class, as OpenMW's does (the race's name is TESRace's
`TESFullName`, `+0x28`). The Statistics tab lists the birthsign, then under
each converted game's name: its bounty per realm, its Fame and Infamy, its
general statistics and the rows of its own statistics page. Morrowind_ob's
heading is Morroblivion. Reputation is Morrowind's own stat, so it leads the
Morrowind or Morroblivion group (a group of its own when neither game stages
a `stats.txt`) and shows for no other game. No row is cut: a label is short
enough to fit, and a custom class's name is kept short enough at entry.

**Bounty per game.** Each plugin's `stats.txt` lists the crime realms it owns
(`bounty.<i>=name|Plugin|FormID`, main realm first, see
[tes_runtime_crime.md](tes_runtime_crime.md#bounty-realms)). The main realm's
row is `sMiscBounty`, the second's `sMiscSEBounty`, the two bounties
Oblivion's own page showed ("Shivering Isles Bounty"; the realm's own name,
"Realm of Sheogorath Bounty", did not fit). A game with neither setting
names its realm. The value is that faction's crime gold. Morroblivion, TR
and the compat patch share Morrowind_ob's realm, so it shows once.

**Which statistics.** Oblivion's own content writes only stats 14, 15, 16,
19 and 27. Its engine keeps the rest. Four of those five map to Skyrim's
stats. A conversion-owned global, `TES4MiscStat<NN>`, holds every other
index a plugin's scripts read or write, and the tab lists each one something
writes:
- a script's `ModPCMiscStat`;
- a command whose engine code counted it (`misc_stats.COMMAND_STATS`):
  Oblivion.exe's `CloseCurrentOblivionGate` (`0x515d20`) adds 1 to Oblivion
  Gates Shut (13, the player's `+0x68c`) whenever it closes a gate, which no
  script does; the polyfill now does the same, for Oblivion's 16 calls.
- Nothing writes Picks Broken (9) or Jokes Told (25): Oblivion's lockpicking
  and persuasion minigames counted them, and Skyrim has neither, so they are
  not listed.

`ModPCMiscStat` writes the global with `GlobalVariable.Mod`. `GetPCMiscStat`
reads it, plus Skyrim's own stat of that name where one exists. Before this,
those writes were dropped. Nehrim writes 3, 13, 18, 22 and 24 (22 and 24 are
its experience and learning points).

**Labels** are the `sMisc*` settings Oblivion.exe reads (its strings, matched
to xEdit's `wbMiscStatEnum` order; Fame and Infamy are `sMiscFame` and
`sMiscInfamy`). A game's rows take the deepest of its own plugins' labels
(its master and what is built on it, `LayersRelated`), so Translation.esp's
English beats Nehrim's German and neither renames Oblivion's rows; xEdit's
name otherwise. Nehrim's labels for stats 22 and 24 ("Overall amount of
experience points", "Current amount of learning points", and the German) are
too long for a row, so `misc_stats.SHORT_LABELS` shortens them to "Total XP"
and "Learning Points" ("Gesamt-EP", "Lernpunkte"). Each TES4 plugin's
`stats.txt` carries its standing globals, `misc.<index>=setting|default|
Plugin|FormID` for each stat its scripts write, and `label.<setting>=text`
for the settings it authors. Each global is listed once, under the
earliest-loaded plugin that lists it: Translation.esp's scripts write
Nehrim's stats 22 and 24 too, and they show under Nehrim.

**A game's own page.** Nehrim's journal (`GlobaltagebuchScript`, step 50)
showed the bank balance, `ErothinBankQuest.PlayerKontostand`, and the
interest percent, which it set to 2 before MQ14 stage 20, 1 before MQ19
stage 70, and 3 after. The runtime cannot read a Papyrus variable (the VM's
variable lookup has no Address Library id), so the converter follows every
write of a variable `misc_stats.PAGE_VARIABLES` names with a write of its
mirror global, `TES4PageStat_<Quest>_<Variable>`; all 22 writes are the
remote `Set ErothinBankQuest.PlayerKontostand to` form. The interest is a
`PAGE_STAGE_RULES` row (`page.<i>=label|Quest@FormID,stage,value;...|otherwise`)
that the runtime evaluates against each quest's current stage, the u16 at
TESQuest `+0x228` that `Quest.GetCurrentStageID` returns on 1.6.1170, 1.5.97
and VR alike. Both labels are English.

## <a id="disease-queries"></a>GetCommonDisease, GetBlightDisease and GetAttacked

**Code:** `script_ops_query.cpp`, `game_calls_spell.cpp`, `game_calls_flight.cpp`,
`game_calls_query.cpp`, `tes5_import/dialogue/morrowind_sidecar_source.py`

Not yet tested in game; the headless `script_test.exe` cases use fake hooks.

**Disease and blight.** Follows OpenMW `Spells::hasCommonDisease` /
`hasBlightDisease` (`hasSpellType` of `ST_Disease` / `ST_Blight`), answering 0 for a
non-actor as `OpGetCommonDisease` does. The export folds both TES3 types into TES4
`SPIT.Type` 1 (`_SPELL_TYPES` in `morrowind_magic.py`), so Skyrim's record cannot tell them
apart. `spell_kinds` reads the TES3 SPDT type from the binaries and `SPEL.txt` rows gain a
fourth field: `id=Plugin|FormID|effects|type`. The runtime asks whether an active effect on
the actor came from a staged spell of type 3 (disease) or 2 (blight). That is the walk the
engine's own `GetDisease` condition makes (handler 0x32a830 on 1.6.1170 calls 0x5c29a0,
whose visitor 0x5c59d0 loads the spell at effect+0x40, asks its vtable slot 0x298 for the
type and compares it to Disease, skipping effects with flag 0x40000); we read the same two
fields. Skyrim keeps a contracted disease as an active effect, not in the spell list, so
`Actor.HasSpell` would miss it. Old `SPEL.txt` files without the field read as type -1.

**GetAttacked.** Follows `CreatureStats::mAttacked`, set in `Npc::onHit` / `Creature::onHit`
when the attacker is an actor the victim is not already fighting. The DLL sets it from the
same OnHit sink `HitOnMe` uses, comparing the victim's combat target to the attacker. Two
differences from OpenMW: it is kept for the session and not saved, and nothing clears it
(OpenMW clears it when a witnessed crime is paid off). Skyrim's own `GetAttacked`
condition (0x32cb80) clears when combat ends, so it was not used.

**Not ported:** `HurtStandingActor`, `GetStandingPC` and `GetStandingActor` (OpenMW
`PhysicsSystem::getActorsStandingOn`, each actor's `mStandingOnPtr`). The condition table
(xEdit `wbConditionFunctions`) and the CK wiki hold no standing-on query, and in the
1.6.1170 character controller code read so far (`bhkCharProxyController` vtable, the state
`Simulate` functions, `bhkCharacterPointCollector::addCdPoint`) no stored supporting body
was found.

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

**If that does not draw, the UI approach is wrong and the plan stops there**
rather than after the menu has been built on it.

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

The files are **copied, not re-serialized**. The exporter already writes the
format the runtime parses (`docs/reference/morrowind_dialogue_format.md`), so a
second writer here would be a second thing to keep in step with it.

Load order inside the DLL is DIAL for every plugin first, then INFO: an INFO
whose topic is unknown is dropped, and a topic may be defined by one plugin and
extended by another.

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

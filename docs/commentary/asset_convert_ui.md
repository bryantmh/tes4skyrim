# asset_convert/ui/ui_menus.py - Oblivion UI in Skyrim

**Code:** `asset_convert/ui/swf.py`, `asset_convert/ui/ui_cursor.py`, `asset_convert/ui/ui_menus.py`, `tests/test_ui_convert.py`

## Contents

- [The two technologies](#two-technologies)
- [The approach: reskin, never rebuild](#approach-reskin-never-rebuild)
- [What vanilla messagebox.swf is made of](#what-vanilla-messageboxswf-made)
- [What Oblivion's message box is made of](#what-oblivions-message-box-made)
- [The port](#port)
- [The menu cursor](#menu-cursor)
- [The HUD stat bars, attempted and reverted](#hud-stat-bars-attempted-reverted)
- [Known gaps](#known-gaps)
- [Why only the message box](#why-only-message-box)

What is implemented, what the two engines actually do, and what cannot be
ported. Code: `asset_convert/ui/swf.py`, `asset_convert/ui/ui_menus.py`,
`asset_convert/ui/ui_cursor.py`, `tools/misc/convert_ui.py`. Tests:
`tests/test_ui_convert.py`, `tests/test_ui_cursor.py`.

**Implemented: the message box and the menu cursor.** The HUD stat bars were
built
and then REVERTED — see [The HUD stat bars, attempted and
reverted](#the-hud-stat-bars-attempted-and-reverted). See
[Why only the message box](#why-only-the-message-box) for what blocks each
other menu — most of them on missing DATA, not on missing art.

This is a SILOED pipeline. It takes no `-f` plugin, because Oblivion's UI lives
in loose menu files and BSAs rather than inside any ESM, so there is nothing
per-plugin to convert. It runs once as a global action — the GUI's "Convert Oblivion UI"
button, next to "Create LOD" and "Pack Start Mod" — and writes its own mod:

```
output/Oblivion UI/Interface/messagebox.swf
output/Oblivion UI/Interface/cursormenu.swf
output/Finished Mods/Oblivion UI.zip
```

---

## The two technologies
<a id="two-technologies"></a>

|  | Oblivion | Skyrim |
|---|---|---|
| Tech | Gamebryo XML "tiles" | Scaleform GFx 4.x |
| Files | `Data/Menus/*.xml` + `Textures/Menus/*.dds` | `Interface/*.swf` |
| Format | Hot-editable XML | Compiled SWF, ActionScript 2 |
| Layout | Traits (`<x>`, `<width>`, `user0..n`) with `copy`/`add`/`mul` operators | Flash display list driven by AS2 |
| Data binding | The engine writes traits directly | C++ `IMenu` subclasses push `GFxValue`s into the movie |
| Fonts | `.fnt` bitmap fonts | Named fonts via `Interface/fontconfig.txt` |

There is **no translation** between them, and none is attempted. Oblivion's UI
is not in any ESM, so no part of the export/import pipeline applies.

## The approach: reskin, never rebuild
<a id="approach-reskin-never-rebuild"></a>

`ui_menus.patch_message_box` opens the user's own vanilla `messagebox.swf` and
replaces **character definitions only**. Every AS2 class, every
`GameDelegate` callback and the whole timeline are copied through untouched.

That is the entire safety argument. `asset_convert/ui/swf.py` round-trips an
unmodified movie **byte-identically** (including Bethesda's use of the 6-byte
tag header for short tags — `Tag.force_long`), so any difference in the output
is a change the patch made deliberately, and the test asserts exactly which
characters may differ. The engine's interface contract with the movie cannot
drift: a bad reskin looks wrong, it never stops responding.

No Bethesda asset is redistributed — every input is read off the machine the
tool runs on, like the rest of the pipeline.

---

## What vanilla `messagebox.swf` is made of
<a id="what-vanilla-messageboxswf-made"></a>

Measured from the shipped SSE file (`Skyrim - Interface.bsa`), CWS/v15,
16,972 bytes:

| Character | What |
|---|---|
| 1 | `DiamondMarker` — 20×30 `DefineBitsLossless2`, the selection diamond |
| 9 | `MessageBoxButton` sprite (up/over/down/disabled frames) |
| **10** | the panel art — a `DefineShape` of two solid fills, black + `#999999` |
| **11** | `Background_mc` — the sprite holding shape 10, **with a `DefineScalingGrid`** |
| 12 | `Divider` — a 395×2.3 px hairline between message and buttons |
| 15 | `MessageBox` sprite: `Background_mc` at depth 1, `Divider` at depth 3, the message `DefineEditText` at depth 5 |
| 18 | `__Packages.MessageBox` — the AS2 class, in a `DoInitAction` |

Text comes from `$EverywhereMediumFont`, **imported from `gfxfontlib.swf`**
(`ImportAssets2`).

### `MessageBox.PositionElements`, disassembled

The layout maths, recovered from the AVM1 bytecode:

```
Background_mc._width  = Math.max(widestLine + 60,
                                 ButtonContainer._width + WIDTH_MARGIN * 2)
Background_mc._height = Message._height + ButtonContainer._height
                        + HEIGHT_MARGIN * 2 + MESSAGE_TO_BUTTON_SPACER
Message._y            = -Background_mc._height / 2 + HEIGHT_MARGIN
ButtonContainer._y    =  Background_mc._height / 2 - HEIGHT_MARGIN
                        - ButtonContainer._height / 2
ButtonContainer._x    = -ButtonContainer._width / 2
Divider._width        = Background_mc._width - WIDTH_MARGIN * 2
```

`widestLine` is `max(Message.getLineMetrics(i).width)` over `Message.numLines`.

So **`Background_mc` is the whole panel, centered on the origin**, and every
margin is measured from the panel edge inward. Vanilla values, initialised in
the class as `Push [reg1, '<NAME>', <int>]`:

| Constant | Vanilla | Ported |
|---|---|---|
| `WIDTH_MARGIN` | 20 | 56 |
| `HEIGHT_MARGIN` | 30 | 81 |
| `MESSAGE_TO_BUTTON_SPACER` | 10 | 30 |
| the unnamed `60` | 60 | 112 |

---

## What Oblivion's message box is made of
<a id="what-oblivions-message-box-made"></a>

`menus/message_menu.xml` + `menus/prefabs/generic_background.xml`:

* The box is `user0` = **700** px wide (the CONTENT area).
* `message_text` is left-justified at `y` = **15**, `wrapwidth` = `user0 - 24`
  — i.e. a **12 px** inset per side.
* `button_1.y` = `message_text.height + ` **30**; each later button is
  `+10` below the last. Up to ten.
* **The buttons are plain centered text.** Their image is
  `Menus\Shared\shared_empty.dds` at `alpha 0` — there is no button art to
  port, and no divider line anywhere.
* `generic_background` draws a `_border_thickness` = **44** px border from
  three textures around a stretched center.

### The border is drawn OUTSIDE the content box

`Background_TopLeft` sits at `x = -44, y = -44`; `Background_Top` spans
`x = 0..user0` at `y = -44`. So a 700-wide box is 788 px of frame.

That is why each ported margin is **border + Oblivion's own inset**: Skyrim
measures from the panel edge, Oblivion from inside its border.
`WIDTH_MARGIN = 44 + 12 = 56`. `HEIGHT_MARGIN` additionally carries the selected focus box’s downward overhang so the LAST option clears the constant scale9 border: `44 + 15 + (17//2 + 14) = 81` (border + inset + focus overhang).

### 🛑 `cropx`/`cropy` is a source OFFSET with a 1:1 crop — not a scaled tile

The single most important fact for getting the frame right, and the one that
was initially got wrong.

The art is packed as tiles: `edge_corners.dds` is 128×128 (a 2×2 grid of 64 px
corners), `edge_horizontal.dds` is 1024×128 (two 64 px strips),
`edge_vertical.dds` is 128×1024. `generic_background.xml` selects among them
with `cropx`/`cropy` of 0 or 64.

The tempting reading — "crop the whole 64 px tile, resample it to
`_border_thickness`" — is **wrong**. The region taken is
`_border_thickness` px across, **1:1**. The textures settle it: within each
64 px tile the art is anchored at the tile origin and runs 45–52 px, i.e. the
border thickness, with the rest transparent padding.

| | Result |
|---|---|
| Whole tile resampled 64→44 | art shrinks to ~31 px; a visible gap opens between the border and the parchment on all four sides, and corners no longer meet the edges |
| 44 px cropped 1:1 | tight, continuous frame — what the game draws |

Rendered both ways to confirm before choosing. `tests/test_ui_convert.py::
test_slices_are_one_to_one_crops_not_scaled_tiles` pins it with a gradient
fixture that makes the two readings numerically distinguishable.

---

## The port
<a id="port"></a>

1. **The whole frame** — center and all eight border pieces — is composed
   offline into ONE bitmap and put on `Background_mc`'s shape (character 10).
2. The four AS2 layout literals are rewritten from Oblivion's authored insets.
3. `IsVertical` is pinned true; the `Divider` is retired.
4. The selection arrows become Oblivion's focus box; the text takes Oblivion's
   authored color.

Getting to step 1 took five in-game rounds, and the two rules it obeys are the
whole story.

### 🛑 Rule 1 rewritten: 9-slice over a bitmap fill DOES work

This entry used to say the opposite, and the correction matters more than the
original claim did.

**What it used to say:** "Skyrim's Scaleform does not 9-slice a bitmap-filled
shape — across 53 vanilla Interface movies, 353 shapes use bitmap fills, 101
characters carry a scaling grid, and the intersection is zero."

**Why that was wrong:** `DefineScalingGrid` names a **sprite**; a bitmap fill
lives on a **shape**. They are disjoint character kinds, so the intersection of
those two sets is zero no matter what the engine does. The number was
arithmetic, not evidence.

**The question asked correctly** — does any grid's sprite CONTAIN bitmap art,
one level down? — finds vanilla doing it: `magicmenu.swf` (char 25),
`containermenu.swf` (char 24) and `craftingmenu.swf` (char 25) all 9-slice a
sprite whose descendants are bitmap-filled. Scale9 over bitmap art is
supported and shipped.

**What was really happening.** `Background_mc` already carries a grid — vanilla
9-slices it — and we were carefully leaving it untouched. But its splitter is
authored for vanilla's panel art:

| | shape 10 box | grid inner rect | fixed border |
|---|---|---|---|
| vanilla | 432×155 | x[−187.6, 186.2] y[−46.2, 48.0] | ~29-31 px |
| ours (before) | 560×800 | *inherited, unchanged* | 44 / 44 / **354** / **352** |

Against our 560×800 shape that same splitter leaves fixed rows about **354 px
tall top and bottom** — more than an entire 379 px panel. The slice
degenerates, and the engine falls back to a plain stretch. That fallback is the
artifact: the frame scaled about 0.99 wide by 0.47 tall, so the side borders
came out twice the thickness of the top and bottom and the carving on them was
compressed to under half density.

**The fix is one tag.** Re-cut the grid to our own border, so the corners hold
their size at any panel size and only the middle stretches:

```
define_scaling_grid(BACKGROUND_SPRITE_ID, border, border, border, border,
                    width, height)     # -> 44 px fixed on all four sides
```

`patch_message_box(scale9=False)` and `--no-scale9` keep the old behavior,
because this engine has refused things that looked equally safe.

Round 1's "magnified smear" was never evidence for the old rule either. It is
fully explained by Rule 2 below — nine disjoint rects, of which the engine drew
only the first, and the first was `top_left`.

**The lesson worth keeping:** a census that returns zero is only evidence if
the two sets it compares COULD have intersected. Check that before believing
the number — this one survived five in-game rounds and shaped the whole design.

### 🛑 Rule 2: one bitmap fill per shape

Rounds 2–4 chased the border as separate clips — a container sprite positioned
by emitted ActionScript, then plain named children of vanilla's own sprite with
authored positions needing no code at all. It never appeared. Round 5 put the
nine rects back on shape 10 *without* a scaling grid, and the panel filled with
**bare parchment**.

That last result decoded the first one. Both screenshots are the same bug:

> **The engine draws a shape's FIRST bitmap fill across the whole shape and
> ignores the rest.**

Round 1's first rect was `top_left`, so the panel filled with magnified corner
knotwork. Round 5's first rect was the center, so it filled with parchment.

Vanilla agrees: of the 207 bitmap-filled shapes, **202 declare exactly one
fill**, and the five exceptions (all in `hudmenu.swf`) are a *single continuous
path* switching fill along its edges — never disjoint filled rectangles like
these.

So the nine slices are composed into one image at build time, and the shape
carries a single fill. `compose_frame` does the assembly;
`test_background_shape_carries_exactly_one_bitmap_fill` pins the rule.

### What that costs, and the base size

The composed frame IS 9-sliced -- `patch_message_box` re-cuts `Background_mc`'s
own scaling grid to our 44 px border (see
[Rule 1](#-rule-1-rewritten-9-slice-over-a-bitmap-fill-does-work)) -- so the
border holds a constant 44 px at any panel size and the base size is no longer
the thing that keeps the carving from squashing. What the base size still
decides is DETAIL: the composed bitmap is `CENTER_BASE_* + 2*border` =
**560x800**, and a panel much larger than that shows the border upscaled (soft),
while a much smaller one wastes pixels. 560x800 sits near a real ten-choice
menu (~515x722 observed in game), so the upscale stays mild.

Supersampling was the obvious alternative for the blur and is the wrong trade
-- doubling the pixels roughly quadruples the file and fixes only blur, where
sizing the base near a real panel fixes blur for a fraction of that.
`FRAME_SUPERSAMPLE` is therefore 1.

### The separate-clip path, and why it is gone

Rounds 2–4 also established, without ever explaining it, that **characters this
conversion ADDS did not render** while edits to existing characters did. What
was ruled out along the way, each by measurement:

* **byte-exact tag lengths**, including inside every nested sprite, with the
  file's declared length matching — a slip would desync a strict parser and
  drop everything after it, and it does not happen;
* **definition order** — every new character defined before its referent, and
  vanilla never forward-references (0 cases);
* **placement flags** — `0x26` (Char|Matrix|Name) is vanilla's *most common*
  named placement, 941 of 27,364 tags;
* **the placement matrix** — optional in the spec, present in every vanilla
  `PlaceObject2`, and missing from round 1's; adding it is round 2's finding;
* **depth order** — vanilla places depths ascending within a frame in **all**
  34,720 sprite frames. Round 3 placed depth 2 after 1/3/5, descending, and
  that was almost certainly its fault. (An earlier census that said 156 sprites
  break the rule was wrong: it never reset at `ShowFrame`, so it measured
  across frames.)
* **`PlaceObject2` after `PlaceObject3` in one frame** — 155 vanilla cases;
* **sprite byte shape**, identical to vanilla's; **clip depth**, absent;
  bitmap format, shape winding, fill type, `DoAction`-in-sprite (1,286 in
  `map.swf` alone).

Since the composed frame does not need them, that path and the small AVM1
assembler written to drive it were deleted rather than left dormant. If a
future change needs to add characters, this list is where to start — but it is
a question the file cannot answer.

### The selector: Oblivion's own focus box

Oblivion rings the focused choice with a bordered box —
`menus/prefabs/focus_box.xml`, included by `message_menu.xml` as
`message_focus_box`. Skyrim flanks it with two arrows instead.

The first attempt blanked character 1, `DiamondMarker`, and nothing changed.
That bitmap is **exported but never placed in this movie**; what is actually on
screen is a vector shape reached through
`MessageBoxButton → 6 (SelectionIndicator) → 5 → 4`. Character **4** is the
one to swap.

The swap is small, because the AS2 already treats the indicator as a wrapper:

```
SelectionIndicator._width = ButtonText._width + SELECTION_INDICATOR_WIDTH
SelectionIndicator._y     = ButtonText._y + ButtonText._height / 2
```

It is stretched to the label's width and centered on it. `_height` is never
assigned, so the authored height survives.

`compose_focus_box` builds the box from Oblivion's own nine pieces — the same
1:1-crop rule as the frame, confirmed by the alpha bounds (`focus_top`'s art is
9 px of a 16 px texture, `focus_right`'s 12 of 16) — and the edges **tile**
rather than stretch, as `<tile> &true; </tile>` specifies.

Two details matter:

* **The content is centered, not the image.** Oblivion's border is asymmetric —
  8 left, 9 top, 12 right, 14 bottom, hanging a soft shadow below and right —
  and Skyrim positions the indicator by its own origin. Centering the image
  would sit the box low on the label, so `compose_focus_box` returns the offset
  that centers the *content* box instead.
* **The natural width matters**, for the same reason the frame's base size
  does: only `_width` is set, so the horizontal scale is
  `(textWidth + 25) / natural`, and that factor thickens the vertical edges. At
  vanilla's 28.5 px a typical label scales about 6x. The composed box is 200 px
  wide (180 content + 20 border), keeping the factor near 1.

### Text color: (117, 59, 33), authored — in TWO places

`message_menu.xml` sets no color of its own, so the message box inherits the
house default. Rather than sample a screenshot, the whole of Oblivion's menu
XML was counted: **(117, 59, 33) appears 226 times**, more than five times the
next most common triple.

Applying it takes two edits per field, not one.

**1. The record's TextColor.** Characters 14 (message) and 8 (choices). Alpha
is kept — vanilla runs the message at 255 and the unfocused choices at 204, and
that difference is what dims an unselected option.

**2. 🛑 The color baked into the field's initial HTML.** Both fields ship an
authoring placeholder:

```html
<p align="center"><font face="$EverywhereMediumFont" size="22"
     color="#ffffff" letterSpacing="0.800000" kerning="0">&lt;message text&gt;</font></p>
```

and the class captures its format rather than the record's:

```
DefaultTextFormat = Message.getTextFormat()      // constructor
Message.setTextFormat(DefaultTextFormat)         // SetMessage, every time
```

`getTextFormat()` reports the format of the text **currently in the field** —
that placeholder — so the captured format is white whatever TextColor says, and
`SetMessage` paints it back over every message. That is why the header stayed
white in game after round 6 while the choices went brown: the choices assign
`.text` through `SetText` and pick up TextColor normally, and only the message
runs through the captured format.

Six hex digits replace six, so the record does not change length.

### 🛑 The edges are TILED, though Oblivion stretches them

`generic_background.xml` has no `<tile>` trait anywhere — every edge and corner
is stretched to its trait size, and only the center is `zoom`ed. Copying that
mechanism here squashed the carving badly enough to read as "the border gets
squished in the middle".

Oblivion can afford to stretch because its box is a **fixed 700 px wide**, so
the 1024 px motif compresses to 0.68x and that mild squeeze *is* the look. Ours
compresses **twice** — once composing 1024 into the base, then again when the
base scales to the panel:

| | Oblivion | composed+stretched | composed+tiled |
|---|---|---|---|
| horizontal motif at a 422 px panel | 0.68x | **0.35x** | 0.75x |
| vertical motif at a 762 px panel | ~0.59x | 0.66x | 0.95x |

So `build_frame_slices` returns the edges at their **source length** and
`compose_frame` tiles them into the band, giving full motif density at the base
size. On a panel that differs from the base the scaling grid then stretches
those edge strips (one-dimensionally -- corners stay fixed), which is a mild
stretch because the base sits near real panels, and is the same direction
Oblivion stretches. So the tiling sets the density and the grid handles the
panel-size difference; between them the carving no longer compresses to half
its weight the way a single compose-then-uniform-scale did.

It also made the file smaller: a crop compresses better than a resampled
stretch (a measured 580 KB → 367 KB for that change at the time; the finished
movie is ~391 KB after later base and margin changes).

The center still stretches, which is right — it is a soft parchment wash and
Oblivion `zoom`s it across the whole box.

### The base size, and the HISTORICAL layout constraint

Under scale9 the base size is a QUALITY choice (detail vs file size) and nothing
more. It used to be a hard layout constraint, and that history is why the
numbers are large:

> **(HISTORICAL, pre-scale9.)** Before the grid was re-cut, the frame rode
> `Background_mc`'s uniform scale, so a panel taller than the base drew the
> border THICKER than its authored 44 px while the fixed-pixel margins stayed
> put -- past a point the border grew into the text. A ten-choice menu was
> 762 px tall against a 488 px base, scaling the border to 69 px against a
> `HEIGHT_MARGIN` of 59, and "More ..." rendered on top of it. The base was
> then enlarged so `border * panel / base < margin` held across the envelope
> (`MAX_PANEL_W`/`MAX_PANEL_H` = 700 x 1040). scale9 makes the border constant,
> so that invariant no longer bites -- but the enlarged base was kept, because
> a bigger base is also sharper.

The one live cost is file size, which is why the **center** is composed at
1/`CENTER_DETAIL_DIVISOR` (currently 1/6) resolution and scaled back up. It is
the only slice with photographic detail, so it dominates the file -- and it is
also the slice Oblivion itself stretches hardest (a 1024x1024 texture across the
whole box), so softening it is faithful rather than a compromise. The border,
which is what the eye reads, keeps full detail. The finished movie is ~391 KB,
nearly all of it this frame bitmap.

### 🛑 The dead space is the field's RUNTIME height, and it is not reachable

`MessageText` is authored 121 px tall for a single 22 px line, so the obvious
read was that `PositionElements` folds that straight into the panel:

```
height = Message._height + buttons._height + HEIGHT_MARGIN*2 + MESSAGE_TO_BUTTON_SPACER
```

**That read is wrong, and the game disproved it.** Shrinking the authored
bounds from 121 px to 66 px left the panel height *identical* at 762 px for a
ten-choice menu — verified in the shipped file, which really does carry 66.
`Message._height` is therefore computed at runtime and the authored RECT does
not control it; it reports roughly 120 px for one line whatever the record
says.

What that leaves reachable is `MESSAGE_TO_BUTTON_SPACER`, which is set to **0**
rather than Oblivion's authored 30. Oblivion's spacer is measured for a message
tile exactly as tall as its text; here the field already supplies ~95 px of
empty space below the header, so adding the spacer on top only widens a gap
Oblivion never had.

**The residual gap is unresolved.** Closing it needs to know why a one-line
field measures ~120 px — the candidates not yet ruled out are the `<p>` block
in the HTML the engine assigns, `Multiline`/`WordWrap` interacting with
`autoSize = "center"`, and Scaleform's `textAutoSize`. None of them can be
settled from the file.

### The original (wrong) reading, kept for the record

`MessageText` is authored **121 px tall for a single 22 px line**, and
`PositionElements` folds that straight into the panel:

```
height = Message._height + buttons._height + HEIGHT_MARGIN*2 + MESSAGE_TO_BUTTON_SPACER
```

so roughly 100 px of empty field was baked into every message box. That is why
the gap measured the same (~130 px) on a four-choice box as on a ten-choice one
— it never depended on the choices at all.

`set_edit_text_height` shrinks the authored bounds to `MESSAGE_TEXT_HEIGHT`
(66 px, three lines). WordWrap and Multiline are untouched.

**The trade-off is real**: vanilla's 121 px holds about five lines, and the
evidence that `autoSize` never *shrank* the field suggests it may not *grow* it
either — in which case a message longer than three lines could clip where it
did not before. Raise the constant if that shows up; `message_height=0` leaves
the field alone entirely.

### 🛑 The panel's translucency and the header's shadow live on the PLACEMENT

Neither is in the artwork, which is why neither could be fixed by changing a
bitmap. Both sit on `MessageBox`'s child placements inside its sprite body, and
both are rewritten in place at their existing widths, so the sprite keeps its
exact length (133 bytes, verified before and after).

| What | Where | Vanilla | Ported |
|---|---|---|---|
| See-through panel | `Background_mc`'s `PlaceObject2` color transform | alpha **205/256** (80%) | 256/256 |
| Header outline | `MessageText`'s `PlaceObject3` filter list | DropShadow, black, 2 px blur, 45°, **strength 1.0** | strength 0 |

The color transform multiplies the whole clip regardless of how opaque the
bitmap inside it is — Skyrim's message panel is deliberately translucent, and
that is what showed the dungeon wall through Oblivion's parchment. The
multiplier is bit-packed, so `swf.poke_bits` rewrites it at its existing bit
width; that width is also the cap, though vanilla's 10-bit field holds the full
256.

The shadow is muted by zeroing its **strength** rather than dropping the filter
list, which would change the tag's length and mean re-headering it inside the
sprite body. `--keep-transparency` and `--keep-shadow` opt out of either.

Note the `Divider`'s own transform (alpha 179/256) is left alone — that clip is
already retired.

### Patching AS2 literals safely

Each margin is initialised as a three-item `ActionPush` —
`(register, constant-pool name, INTEGER)` — before a `SetMember`. Push type 7
is a fixed 4-byte signed int, so the new value is written **over** the old
one: the action keeps its length, every relative jump offset in the block
stays valid, and nothing else can shift. A test asserts the block length is
unchanged, because a resized action would corrupt control flow rather than
fail to parse.

The unnamed `60` has no constant-pool name to anchor on, so it is located by
its exact push shape (`Push [reg3, 60]`). Both patchers **raise unless there
is exactly one match** — a half-applied layout is harder to diagnose than a
hard failure.

### Everything else is refused, loudly

`patch_message_box` re-checks every character id and export name before
touching anything, and raises `UiConvertError` if the movie is not the one the
patch was written against. A future Skyrim update that renumbers characters
fails visibly instead of shipping a broken menu.

### Loose files beat BSAs

`tools/misc/convert_ui.py::read_game_file` reads a loose file before any archive —
the games' own order. A user running DarNified UI has edited `menus\*.xml`
sitting loose in `Data`, and reading the BSA instead would convert vanilla's
layout while their screen showed something else. Any value that cannot be read
as a literal falls back to `VANILLA_LAYOUT` **with a named warning**, never
silently.

---

## The menu cursor
<a id="menu-cursor"></a>

The cleanest port in this mod -- smaller than the message box and on the same
proven path. Skyrim's `Interface/cursormenu.swf` (949 bytes) draws the menu
cursor as a SINGLE vector shape: character 1, a 42x42 shape placed at the movie
origin. A cursor's hotspot -- the click point -- is the movie's (0,0), and this
shape's box is x[0,42] y[0,42], so its **top-left corner is the hotspot**,
exactly where a pointer's tip belongs.

Oblivion's pointer is `textures/menus/misc/cursor.dds`, a 64x64 texture whose
arrow art sits at the top-left with the tip in the corner (measured: the
topmost opaque pixel is at x=1, the leftmost at y=2, inside a 33x34 opaque
region). Cropping to the art's alpha bounds puts the tip at the crop's
top-left, and placing that crop at the shape origin lands the tip on the
hotspot -- the one thing a cursor port has to get right, settled by geometry
rather than tuning.

**The size is not the 42 px shape box.** cursormenu.swf's root places the
cursor sprite at scale ~0.63 (measured), so a 42 px shape draws at ~26 px, and
Oblivion's chunkier pointer filling that whole box read noticeably larger than
Skyrim's thin arrow. The art is therefore sized DOWN inside the shape --
`CURSOR_HEIGHT` = 30 px, ~19 px on screen -- with Oblivion's aspect preserved
(29x30). No ActionScript is touched -- the one `DoAction` and the timeline are
byte-identical -- so the cursor tracks the mouse and hides in gameplay exactly
as vanilla. The movie goes 949 -> ~3.5 KB.

This is the port the reverted HUD taught: a new `DefineBitsLossless2` referenced
by an EXISTING replaced shape renders (the HUD proved that in game, broken only
in its geometry), and here the geometry is trivial -- one rectangle, tip at the
origin -- with no mask, no scale9, no layout maths. It affects only the MENU
cursor (inventory, map, message boxes, favorites); there is no cursor during
normal gameplay.

**Not yet confirmed in game.** Offline the arrow composes correctly with the
tip within ~1 px of the hotspot, the shape carries one bitmap fill, and every
other character and AS2 block is byte-identical to vanilla. What a look would
confirm: that the hotspot feels right (a 1-2 px tip offset is expected and
should be imperceptible) and the size reads well against Skyrim's UI.

## The HUD stat bars, attempted and reverted
<a id="hud-stat-bars-attempted-reverted"></a>

Built, unit-tested, correct in an offline compositor -- and **it DID render in
game, just BROKEN**. That distinction matters: the engine ACCEPTED our art and
drew it (so this is not the "added characters do not render" wall the message
box hit in rounds 2-4; a new `DefineBitsLossless2` referenced by an existing
shape draws here exactly as it does for the frame). What was wrong was the
GEOMETRY, not acceptance. The code (`asset_convert/ui_hud.py`,
`tests/test_ui_hud.py`, the `--no-hud` flag and the HUD half of `convert_ui.py`)
is reverted; what follows is kept so nobody re-derives it.

**Do not restart from these notes assuming they are sufficient.** They made the
bars correct in an offline compositor, and that was not enough to make them
correct in game.

### What was measured (all verified against the shipped `hudmenu.swf`)

* **The containment tree.** `Health` (776) → `HealthMeter_mc` (767) → backdrop
  756 + `HealthLeft` (766); `Magicka` (784) → `MagickaMeter_mc` (779);
  `Stamina` (792) → `StaminaMeter_mc` (787). Each also carries a
  `…PenaltyMeter_mc` and a `…FlashInstance`. There is no `HealthRight`.
  Note the asymmetry: magicka and stamina place the backdrop INSIDE their meter
  clip, health's sits one level up.
* **The reveal is done two different ways.** Magicka and stamina slide the fill
  behind a static clip-depth mask — fill `scaleX` has exactly ONE distinct
  value against 200 distinct translations, +184.15 (frame 1, full) to −181.70
  (frame 200, empty). `HealthLeft` does the opposite: the fill is placed once
  and never moves, and the MASK is scaled, 0.9866 down to 0.0014. Generalizing
  one to the other is a mistake this made once already.
* **🛑 The shapes are NOT centered on their origin.** 756 draws in
  x[−28.30, 395.85] y[−55.60, −26.20]; 757 in x[−0.10, 369.60]
  y[−51.45, −30.25]; 760 in x[−182.10, 182.60] y[−8.70, 8.30]; 768/774/777/785
  all in x[−183.20, 183.20] y[−8.65, 8.65]. Centering a replacement put the
  backdrop 184 px left and 41 px low. A replacement has to go back into the
  SAME rectangle.
* **🛑 `HealthLeft`'s fill is MIRRORED** — negative `scaleX` (−0.674). A
  negative scale makes the transformed corners come out x0 > x1, and a
  `max(1, x1 - x0)` silently collapses the bar to one pixel. Invisible in the
  art itself: the full ribbon differs from its own mirror by a mean of
  6.46/255. (The empty ribbon is far less symmetric — 17.08/255 — but the
  backdrop is never mirrored.)
* **The art.** Oblivion's ribbons are 256×16 textures whose art occupies
  189×16 (empty) / 163×11 (full) at the top-left, by their alpha bounds.
* **The character map used.** 756 backdrop; 760/777/785 the health/magicka/
  stamina fills; 768 + 774 the penalty overlays; 765/773 gloss; 762/770 flash;
  757 the mask and 766/779/787 the meter clips, both left untouched.

### What was verified about the OUTPUT, and still did not help

Every one of these was checked on the built file, not assumed: all four
replaced boxes identical to vanilla to 0.00 px; mask 757 byte-identical; all
three meter clips byte-identical; all 39 AS2 blocks byte-identical; one bitmap
fill per replaced shape. An offline compositor reading the shipped movie's own
bitmaps, matrices and mask drew all three bars correctly at 100/75/50/25/0%.

Because it RENDERED, one thing once suspected is now RULED OUT: the engine draws
our bitmaps -- a `DefineBitsLossless2` on a replaced shape is not rejected, the
same as the message-box frame. (What "broken" showed is not recorded per-part,
so whether the MASKED fills specifically drew, versus only the unmasked backdrop
756, is not settled -- see the mask question below.)

So the bug is geometric, not acceptance. The leading suspect is the CENTERING error above -- the
first build placed the backdrop 184 px off, and a bar drawn 184 px from where
the mask sits reveals wrong or not at all. The shelved code fixes that (writes
each replacement back into the vanilla box via `EXPECTED_BOX`), **but a build
with that fix was never re-confirmed in game.** The next attempt should install
the shelved (box-corrected) build first and look, rather than re-deriving from
these notes. If it is still wrong with the boxes correct, the open question is
whether the clip-depth mask REVEALS a bitmap-filled child the same way it
reveals the GRADIENT it clipped in vanilla -- that is the one mask-interaction
question the render does not settle. (The health fill's mirrored placement is
NOT a suspect: the engine handles a negative `scaleX` fine and the ribbon art is
near-symmetric; the 1 px collapse it caused was a bug in the offline compositor,
not in game.)

---

## Known gaps
<a id="known-gaps"></a>

* **Fonts are not ported.** The message text stays in Skyrim's face.
  `$EverywhereMediumFont` lives in `gfxfontlib.swf`, which every menu shares,
  so replacing it is a whole-UI change rather than a message-box one; and
  Oblivion's own faces are `.fnt` bitmap fonts, which do not scale and would
  have to be revectorised. The frame is the dominant visual signature and it
  does port.
* **The panel is not a fixed 700 px.** Oblivion's box is `user0` wide whatever
  the text says; Skyrim's grows to fit it, and adding a minimum-width term
  would mean recompiling AS2 rather than patching a literal. `box_width` is
  read and reported so the difference is visible, but it is not applied.
* **The options are LEFT-ALIGNED, not centered.** Only the widest one looks
  centered; the rest hang off its left edge. Nothing about the text is at
  fault — char 8 is `align=2 (CENTER)` with `<p align="center">` in its initial
  HTML, and the class sets `ButtonText.autoSize = "center"`. It is the button
  CLIP that is placed left: `MessageBoxButton` is registered on its center
  (char 8 sits at x[−50, +50] inside it), and the vertical branch of
  `setupButtons` does `button._x = button._width / 2`, which puts every
  button's LEFT EDGE at 0 in the container. `PositionElements` then centers
  the block as a whole with `ButtonContainer._x = -ButtonContainer._width / 2`.
  The correct value is `maxWidth / 2` per button, which cannot be had inside
  that loop — `ButtonContainer._width` is only "widest so far" until it ends —
  so fixing it means a SECOND PASS in `PositionElements`, iterating
  `MessageButtons` (populated by `MessageButtons.push(button)`). That is new
  bytecode rather than a length-preserving edit, which is why it is a gap and
  not a fix.
* **Conflicts** with any other mod replacing `Interface\messagebox.swf` or
  `Interface\cursormenu.swf`. It replaces exactly those two files, so nothing
  else; `--no-cursor` / `--no-messagebox` narrow it to one.
* Output is ~391 KB, up from 17 KB; the frame art is nearly all of it.

### Verification status

The frame geometry and the patch surface are both covered offline by
`tests/test_ui_convert.py`. **No ActionScript is emitted** — see
[Patching AS2 literals safely](#patching-as2-literals-safely) for the only
kind of code change this makes.

**The message box is confirmed in game.** Frame, brown text, Oblivion focus
box, vertical buttons, opaque panel, no header shadow, correct motif density.
Every 🛑 above is a finding from a round that was wrong on screen first.

The HUD bars are **reverted** — they passed every offline check and rendered
broken in game. See [the section above](#the-hud-stat-bars-attempted-and-reverted).

---

## Why only the message box
<a id="why-only-message-box"></a>

Tiering the rest, since the blocker is usually missing DATA rather than
missing art:

**Portable in principle** (a reskin, no new game systems): the HUD
(health/magicka/**fatigue** → health/magicka/**stamina** maps exactly, though
the first attempt at the bars did not survive contact with the engine), the
main menu, loading screens, and the book menu — whose Oblivion art already
ships, since `tes5_import.record_types.equipment._fix_book_html` rewrites book
images to `img://textures/tes4/menus/…`.

**Reskinnable but not 1:1**: inventory/container/barter (Oblivion has item
condition and repair, Skyrim has neither) and the map (flat painted parchment
vs a 3D terrain render).

**Not portable at any effort** — Skyrim has no data to put in them:

* The **stats sheet** (8 attributes, Major/Minor skills, class, birthsign,
  fame/infamy). This converter deliberately does not create any of it — `SKIL`
  and `BSGN` are in `SKIP_TYPES`, and `message_menus.build_chargen_menus`
  states the class menu is "choice-and-pacing only".
* The **level-up menu** (pick three attributes with multipliers).
* **Spellmaking** and **enchanting** altars — `ShowSpellMaking` /
  `ShowEnchantment` convert to no-ops because the systems do not exist.
* **Lockpicking** and the **persuasion wheel** — different minigames,
  engine-side.

Anything past a reskin needs SKSE, which
[skse_conversion.md](../audits/skse_conversion.md) deliberately declines as
a hard dependency.

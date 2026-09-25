"""
Author the Morrowind dialogue menu SWF, and the hello-world that gates it.

This is a STANDALONE movie loaded by GFxLoader::LoadMovie through SKSE's
CustomMenu, not characters spliced into a vanilla one -- the engine parses it
from scratch exactly as it parses its own. Skyrim's menus are untouched.

`--hello` writes the minimal probe: one filled rectangle and one text field,
the gate that proved an authored movie draws at all. Without it the real menu
is written: Morrowind's own frame, caption, scrollbars and disposition bar at
the layout `openmw_dialogue_window.layout` and `openmw_windows.skin.xml`
author, with every string a DYNAMIC field the plugin fills at runtime.

The movie carries NO ActionScript. Every hit rect, the moving parts' paths and
the font's metrics are also written to a C++ header the plugin includes, so
mouse input and keyword links are resolved against the numbers the movie was
drawn from.

🛑 The art is NOT committed -- it is read from the registered Morrowind install
at build time, so this generator needs a machine that has one. The FONT is
OpenMW's own OFL-licensed face, vendored, so text needs no install.

See: docs/commentary/morrowind_runtime.md#the-real-menu
"""

import argparse
import functools
import os
import struct

from asset_convert.ui import ttf_glyphs
from asset_convert.ui.morrowind_menu_art import (BOX_BORDER, SCROLL_END,
                                                 SCROLL_TRACK, SCROLL_W,
                                                 compose_bar, compose_box,
                                                 compose_button, compose_cap,
                                                 compose_frame, compose_head,
                                                 compose_line,
                                                 compose_scrollbar,
                                                 compose_thumb)
from asset_convert.ui.swf import (Swf, Tag, define_bits_lossless2,
                                  define_font2, define_shape3_bitmap_rects,
                                  define_shape3_solid_rects, define_sprite,
                                  pack_rect, place_object2)
from asset_convert.ui.ui_menus import premultiplied_argb

#: Twips per pixel; every SWF coordinate is in twips.
TWIP = 20

#: Stage size, matching Skyrim's own menu coordinate space.
STAGE_W = 1280
STAGE_H = 720

#: Tag codes this generator emits.
TAG_END = 0
TAG_SHOW_FRAME = 1
TAG_SET_BACKGROUND_COLOR = 9
TAG_DEFINE_EDIT_TEXT = 37
TAG_IMPORT_ASSETS2 = 71
TAG_FILE_ATTRIBUTES = 69

#: Character ids for the probe. Low and contiguous, as vanilla movies number.
CHAR_BORDER = 1
CHAR_PANEL = 2
CHAR_TEXT = 3

#: DefineEditText byte 1 bits, MSB-first, as SWF packs them.
_HAS_TEXT, _WORD_WRAP, _MULTILINE = 0x80, 0x40, 0x20

#: Byte 1, continued: ReadOnly, HasTextColor, HasFont.
_READ_ONLY, _HAS_TEXT_COLOR, _HAS_FONT = 0x08, 0x04, 0x01

#: DefineEditText byte 2 bits: HasLayout and NoSelect.
_HAS_LAYOUT, _NO_SELECT = 0x20, 0x10

#: Byte 2, continued: HTML markup in the text, and glyphs from the font's OWN outlines.
_HTML, _USE_OUTLINES = 0x02, 0x01

#: The shared font library the PROBE imports its face from.
FONT_LIB = 'gfxfontlib.swf'

#: The face Skyrim's own message text uses; a field with no font draws nothing.
FONT_NAME = '$EverywhereMediumFont'

#: Character id for the imported font, and the probe's text height in twips.
CHAR_FONT = 10
TEXT_HEIGHT_TWIPS = 17 * TWIP

#: Morrowind's parchment palette, sampled from its own UI art (probe only).
PANEL_RGBA = (38, 30, 22, 235)
BORDER_RGBA = (120, 100, 66, 255)
TEXT_RGB = (220, 208, 180)

#: `[FontColor]` from Morrowind.ini, under its own key names; the plugin gets this same table.
FONT_COLORS = {
    'normal': (202, 165, 96), 'normal_over': (223, 201, 159),
    'normal_pressed': (243, 237, 221), 'link': (112, 126, 207),
    'link_over': (143, 155, 218), 'link_pressed': (175, 184, 228),
    'answer': (150, 50, 30), 'answer_over': (223, 201, 159),
    'answer_pressed': (243, 237, 221), 'header': (223, 201, 159),
    'notify': (223, 201, 159), 'disabled': (179, 168, 135),
}

#: `[FontColor] color_background`, at the window's own alpha.
COLOR_BACKGROUND = (0, 0, 0, 245)

#: The dialogue window, at `openmw_dialogue_window.layout`'s authored size.
WINDOW_W = 588
WINDOW_H = 433

#: Pixels below stage CENTER, so the window sits low as Morrowind's does.
WINDOW_DROP = 60

#: MW_Window: the caption strip, where the client area starts, and the inner frame's rect.
CAPTION = (4, 4, WINDOW_W - 8, 20)
CLIENT = (8, 28)
INNER_FRAME = (4, 24, WINDOW_W - 8, WINDOW_H - 28)

#: The name box extends this far beyond the caption text on each side.
CAPTION_PAD = 8

#: Widgets in CLIENT space, as (x, y, w, h), from `openmw_dialogue_window.layout`.
HISTORY_BOX = (8, 8, 381, 381)
HISTORY_PAGE = (15, 15, 364, 370)
HISTORY_SCROLL = (370, 13, 14, 371)
DISPOSITION = (398, 8, 166, 18)

#: The disposition bar's fill, inside its box border; the plugin covers the part past the value.
DISPOSITION_FILL = (DISPOSITION[0] + BOX_BORDER, DISPOSITION[1] + BOX_BORDER,
                    DISPOSITION[2] - 2 * BOX_BORDER,
                    DISPOSITION[3] - 2 * BOX_BORDER)
TOPICS = (398, 31, 166, 328)
BYE_BUTTON = (398, 366, 166, 23)

#: The persuasion modal, from `openmw_persuasion_dialog.layout`: 220 x 192, centered on the screen.
MODAL_W = 220
MODAL_H = 192

#: Its parts in MODAL space: the title strip, the actions box, the gold label, Cancel (right edge at 204).
MODAL_TITLE = (0, 4, MODAL_W, 24)
MODAL_BOX = (8, 32, 196, 114)
MODAL_GOLD = (8, 158, 102, 24)
MODAL_CANCEL_W = 64
MODAL_CANCEL = (204 - MODAL_CANCEL_W, 154, MODAL_CANCEL_W, 24)

#: Six action rows at the list pitch, 4 px in from the box's left and 3 px down from its top.
MODAL_ROWS = 6
MODAL_ROW_INSET = (4, 3)

#: MW_ScrollTrackV, the thumb: 9 px wide at x=2, at its skin default height.
THUMB_W = 9
THUMB_X = 2
THUMB_H = 30

#: MWList items: an 18 px text row with 3 px above and below; a separator is a bare 18 px.
ROW_H = 18
ROW_PAD = 3
SEPARATOR_H = 18

#: MW_SimpleList's client sits 3 px inside its box; MW_ListLine's text 2 px further in.
LIST_INSET = 3
LINE_INSET = 2

#: MWList reserves this much for its scrollbar when one shows.
LIST_SCROLL_W = 20

#: Text fields kept for list rows; the plugin positions each at runtime.
TOPIC_FIELDS = 14

#: MW_Button's caption box: `offset="4 3 128 16"` inside a 136x24 button.
BUTTON_INSET = (4, 3)

#: Captions from GMSTs `sGoodbye` and `sPersuasion`.
GOODBYE = 'Goodbye'
PERSUASION = 'Persuasion'

#: Character ids for the real menu's composed art and the two covers (shape, then its sprite).
CHAR_WINDOW_BMP, CHAR_WINDOW_SHAPE = 20, 21
CHAR_COVER = 22
CHAR_BAR_COVER = 26

#: Sprites take character ids from here up, three per sprite (bitmap, shape, sprite).
CHAR_SPRITE_FIRST = 60

#: The embedded face, and the fixed fields the plugin fills at runtime.
CHAR_MW_FONT = 25
CHAR_NAME, CHAR_HISTORY, CHAR_DISPOSITION, CHAR_BYE = 30, 31, 32, 33

#: Topic rows take character ids from here up, one per row.
CHAR_TOPIC_FIRST = 40

#: The persuasion modal: its art (bitmap, shape, sprite), then its title, six rows, gold label and Cancel.
CHAR_MODAL_BMP = 100
CHAR_MODAL_TITLE = 103
CHAR_MODAL_ROW_FIRST = 104
CHAR_MODAL_GOLD = 110
CHAR_MODAL_CANCEL = 111

#: The modal's instance names; rows are `PersuadeRow0` .. `PersuadeRow5`.
SPRITE_MODAL = 'Persuade'
FIELD_MODAL_TITLE = 'PersuadeTitle'
FIELD_MODAL_ROW = 'PersuadeRow'
FIELD_MODAL_GOLD = 'PersuadeGold'
FIELD_MODAL_CANCEL = 'PersuadeCancel'

#: Instance names, which is how the plugin reaches every field and sprite.
FIELD_NAME = 'Name'
FIELD_HISTORY = 'History'
FIELD_DISPOSITION = 'Disposition'
FIELD_BYE = 'Bye'
FIELD_TOPIC = 'Topic'
SPRITE_COVER = 'Cover'
SPRITE_BAR_COVER = 'BarCover'
SPRITE_CAP_LEFT = 'CapLeft'
SPRITE_CAP_RIGHT = 'CapRight'
SPRITE_HISTORY_SCROLL = 'HistoryScroll'
SPRITE_HISTORY_THUMB = 'HistoryThumb'
SPRITE_TOPIC_SCROLL = 'TopicScroll'
SPRITE_TOPIC_THUMB = 'TopicThumb'
SPRITE_TOPIC_LINE = 'TopicLine'

#: OpenMW's default UI face, vendored beside its OFL license.
MW_FONT_PATH = 'external/openmw/files/data/fonts/MysticCards.ttf'

#: The name the embedded font registers under.
MW_FONT_NAME = 'MysticCards'

#: OpenMW's `font size = 16` (settings-default.cfg), in pixels and twips.
FONT_PX = 16
BODY_HEIGHT_TWIPS = FONT_PX * TWIP

#: A Flash text field draws its text this far inside its bounds.
TEXT_GUTTER = 2

#: DefineEditText layout block alignments: 0 left, 1 right, 2 center.
_ALIGN_LEFT, _ALIGN_CENTER = 0, 2

#: Where the plugin's copy of the layout is written.
HEADER_PATH = 'tes_runtime/morrowind/plugin/menu_layout.h'


def _layout(align: int) -> bytes:
    """The 9-byte layout block: align plus four zeroed u16 metrics."""
    return bytes([align]) + struct.pack('<HHHh', 0, 0, 0, 0)


def define_edit_text(character_id: int, x: int, y: int, w: int, h: int,
                     var_name: str, initial: str, font_id: int = CHAR_FONT,
                     rgb: tuple = TEXT_RGB, height: int = TEXT_HEIGHT_TWIPS,
                     align: int = _ALIGN_CENTER, html: bool = False) -> Tag:
    """A dynamic text field, optionally bound to `var_name`.

    UseOutlines is always set: the glyphs come from the font character named
    here, never from a device font this engine does not have.

    Every flag gates the field that follows it, so a flag without its field
    slides all the later ones and the tag parses into nonsense.
    See: docs/commentary/morrowind_runtime.md#edit-text-flags
    """
    flags1 = (_HAS_TEXT | _WORD_WRAP | _MULTILINE | _READ_ONLY |
              _HAS_TEXT_COLOR | _HAS_FONT)
    flags2 = _HAS_LAYOUT | _NO_SELECT | _USE_OUTLINES | (_HTML if html else 0)
    body = bytearray()
    body += struct.pack('<H', character_id)
    body += pack_rect(x * TWIP, (x + w) * TWIP, y * TWIP, (y + h) * TWIP)
    body += bytes([flags1, flags2])
    body += struct.pack('<HH', font_id, height)
    body += bytes([rgb[0], rgb[1], rgb[2], 0xFF])
    body += _layout(align)
    body += var_name.encode('ascii') + b'\x00'
    body += initial.encode('ascii') + b'\x00'
    return Tag(TAG_DEFINE_EDIT_TEXT, bytes(body))


def import_font() -> Tag:
    """ImportAssets2 pulling the shared face in under `CHAR_FONT`.

    The PROBE's font. The real menu EMBEDS OpenMW's own face instead, so it
    imports nothing.
    """
    body = bytearray()
    body += FONT_LIB.encode('ascii') + b'\x00'
    body += bytes([1, 0])
    body += struct.pack('<HH', 1, CHAR_FONT)
    body += FONT_NAME.encode('ascii') + b'\x00'
    return Tag(TAG_IMPORT_ASSETS2, bytes(body))


def hello_world() -> Swf:
    """The gate: a bordered panel and one text field, nothing else.

    Deliberately minimal. Its only question is whether a movie this project
    authored draws at all when the engine loads it as a menu of its own.

    The border is a second character behind the panel rather than one shape:
    `define_shape3_solid_rects` takes PIXELS and scales them itself, and one
    shape carries one fill.
    """
    panel_w, panel_h = 600, 260
    x = (STAGE_W - panel_w) // 2
    y = (STAGE_H - panel_h) // 2
    border = 4

    frame = define_shape3_solid_rects(
        CHAR_BORDER, [(x, y, panel_w, panel_h)], BORDER_RGBA)
    inner = define_shape3_solid_rects(
        CHAR_PANEL,
        [(x + border, y + border, panel_w - 2 * border,
          panel_h - 2 * border)], PANEL_RGBA)
    text = define_edit_text(CHAR_TEXT, x + 24, y + 24, panel_w - 48,
                            panel_h - 48, 'probeText',
                            'MorrowindRuntime: the menu drew.')

    tags = [
        Tag(TAG_FILE_ATTRIBUTES, struct.pack('<I', 0)),
        Tag(TAG_SET_BACKGROUND_COLOR, bytes([0, 0, 0])),
        import_font(),
        frame,
        place_object2(depth=1, character_id=CHAR_BORDER, name='Border_mc'),
        inner,
        place_object2(depth=2, character_id=CHAR_PANEL, name='Panel_mc'),
        text,
        place_object2(depth=3, character_id=CHAR_TEXT, name='ProbeText'),
        Tag(TAG_SHOW_FRAME, b''),
        Tag(TAG_END, b''),
    ]
    return Swf(version=9,
               frame_size=pack_rect(0, STAGE_W * TWIP, 0, STAGE_H * TWIP),
               framerate=(24 << 8), framecount=1, tags=tags)


def window_origin() -> tuple:
    """Where the window's top-left lands on the stage."""
    return ((STAGE_W - WINDOW_W) // 2, (STAGE_H - WINDOW_H) // 2 + WINDOW_DROP)


def client_rect(rect: tuple) -> tuple:
    """A CLIENT-space rect moved into WINDOW space."""
    return (rect[0] + CLIENT[0], rect[1] + CLIENT[1], rect[2], rect[3])


def stage_rect(rect: tuple) -> tuple:
    """A WINDOW-space rect moved onto the stage."""
    ox, oy = window_origin()
    return (rect[0] + ox, rect[1] + oy, rect[2], rect[3])


def text_top(box_y: int, box_h: int) -> int:
    """A field's top so its FONT_PX line, drawn a gutter down, centers in a box."""
    return box_y + (box_h - FONT_PX) // 2 - TEXT_GUTTER


def centered_field(box: tuple) -> tuple:
    """A field rect whose one line is vertically centered in `box`."""
    x, y, w, h = box
    return (x, text_top(y, h), w, h)


def history_text_rect() -> tuple:
    """The history page, narrowed by the scrollbar it may need, in WINDOW space."""
    x, y, w, h = HISTORY_PAGE
    return client_rect((x, y, w - SCROLL_W, h))


def topic_scroll_rect() -> tuple:
    """MW_SimpleList's scrollbar, at its client's right edge, in WINDOW space."""
    x, y, w, h = TOPICS
    return client_rect((x + w - LIST_INSET - SCROLL_W, y + LIST_INSET, SCROLL_W,
                        h - 2 * LIST_INSET))


def topic_row_rect(index: int) -> tuple:
    """Row `index`'s text field before the plugin moves it, in WINDOW space:
    its line centered in the row as MW_ListLine's VCenter does."""
    x, y, w, _h = TOPICS
    top = y + LIST_INSET + ROW_PAD + index * (ROW_H + 2 * ROW_PAD)
    return client_rect((x + LIST_INSET + LINE_INSET, text_top(top, ROW_H),
                        w - 2 * LIST_INSET - LIST_SCROLL_W - LINE_INSET, ROW_H))


def topic_line_width() -> int:
    """MW_HLine's width inside the list: 2 px in from each side of the rows."""
    return TOPICS[2] - 2 * LIST_INSET - LIST_SCROLL_W - 4


def bye_text_rect() -> tuple:
    """The Goodbye caption box, in WINDOW space."""
    x, y, w, h = client_rect(BYE_BUTTON)
    return centered_field((x + BUTTON_INSET[0], y + BUTTON_INSET[1],
                           w - 2 * BUTTON_INSET[0], h - 2 * BUTTON_INSET[1]))


def modal_origin() -> tuple:
    """The persuasion modal's top-left: screen-centered, as WindowModal is."""
    return ((STAGE_W - MODAL_W) // 2, (STAGE_H - MODAL_H) // 2)


def modal_stage_rect(rect: tuple) -> tuple:
    """A MODAL-space rect moved onto the stage."""
    ox, oy = modal_origin()
    return (rect[0] + ox, rect[1] + oy, rect[2], rect[3])


def modal_row_rect(index: int) -> tuple:
    """Action row `index`'s hit box, in MODAL space, at the list pitch."""
    x, y, w, _h = MODAL_BOX
    return (x + MODAL_ROW_INSET[0], y + MODAL_ROW_INSET[1] + index * ROW_H,
            w - 2 * MODAL_ROW_INSET[0], ROW_H)


def modal_cancel_text_rect() -> tuple:
    """The Cancel caption box, in MODAL space."""
    x, y, w, h = MODAL_CANCEL
    return centered_field((x + BUTTON_INSET[0], y + BUTTON_INSET[1],
                           w - 2 * BUTTON_INSET[0], h - 2 * BUTTON_INSET[1]))


def compose_modal(export_root):
    """The persuasion modal's chrome as one image: frame, the actions box,
    the Cancel button. Textless, like the window.
    See: docs/commentary/morrowind_runtime.md#persuasion
    """
    panel = compose_frame(export_root, MODAL_W, MODAL_H,
                          fill=COLOR_BACKGROUND)
    bx, by, bw, bh = MODAL_BOX
    panel.alpha_composite(compose_box(export_root, bw, bh), (bx, by))
    cx, cy, cw, ch = MODAL_CANCEL
    panel.alpha_composite(compose_button(export_root, cw, ch), (cx, cy))
    return panel


def _modal_field(character_id: int, rect: tuple, color: str = 'normal',
                 align: int = _ALIGN_LEFT) -> Tag:
    """A field at a MODAL-space rect, in the embedded face."""
    return define_edit_text(character_id, *modal_stage_rect(rect), '', '',
                            font_id=CHAR_MW_FONT, rgb=FONT_COLORS[color],
                            height=BODY_HEIGHT_TWIPS, align=align)


def _modal_fields() -> list:
    """The modal's dynamic text fields as `(tag, instance_name)`."""
    out = [(_modal_field(CHAR_MODAL_TITLE, centered_field(MODAL_TITLE),
                         color='header', align=_ALIGN_CENTER),
            FIELD_MODAL_TITLE)]
    for row in range(MODAL_ROWS):
        out.append((_modal_field(CHAR_MODAL_ROW_FIRST + row,
                                 centered_field(modal_row_rect(row))),
                    f'{FIELD_MODAL_ROW}{row}'))
    out.append((_modal_field(CHAR_MODAL_GOLD, centered_field(MODAL_GOLD)),
                FIELD_MODAL_GOLD))
    out.append((_modal_field(CHAR_MODAL_CANCEL, modal_cancel_text_rect(),
                             align=_ALIGN_CENTER), FIELD_MODAL_CANCEL))
    return out


def _modal_tags(export_root, depth: int) -> list:
    """The modal's art sprite and fields, placed ABOVE the window from
    `depth` up. The plugin hides them until Persuasion is chosen."""
    art = compose_modal(export_root)
    tags = _sprite(CHAR_MODAL_BMP, art, SPRITE_MODAL, (0, 0, MODAL_W, MODAL_H))
    bitmap, shape, sprite, (char_id, name, _at) = tags
    out = [bitmap, shape, sprite,
           place_object2(depth=depth, character_id=char_id, name=name,
                         translate=modal_origin())]
    for field, name in _modal_fields():
        depth += 1
        out += [field, place_object2(depth=depth,
                                     character_id=field.character_id,
                                     name=name)]
    return out


def compose_window(export_root):
    """The window CHROME as one image: both frames, caption plate, panes, a
    FULL disposition bar and the Goodbye button, every part where MW_Window
    and the layout put it.

    Deliberately textless. Every string the player reads is a DefineEditText
    field the plugin fills at runtime, so nothing here is baked but the art.

    One bitmap because this engine draws a shape's FIRST bitmap fill across the
    whole shape and ignores the rest.
    See: docs/commentary/morrowind_runtime.md#one-bitmap
    """
    panel = compose_frame(export_root, WINDOW_W, WINDOW_H,
                          fill=COLOR_BACKGROUND)
    ix, iy, iw, ih = INNER_FRAME
    panel.alpha_composite(compose_frame(export_root, iw, ih), (ix, iy))
    cx, cy, cw, ch = CAPTION
    panel.alpha_composite(compose_head(export_root, cw, ch), (cx, cy))
    hx, hy, hw, hh = client_rect(HISTORY_BOX)
    panel.alpha_composite(compose_box(export_root, hw, hh), (hx, hy))
    tx, ty, tw, th = client_rect(TOPICS)
    panel.alpha_composite(compose_box(export_root, tw, th), (tx, ty))
    dx, dy, dw, dh = client_rect(DISPOSITION)
    panel.alpha_composite(compose_bar(export_root, dw, dh, 1.0), (dx, dy))
    bx, by, bw, bh = client_rect(BYE_BUTTON)
    panel.alpha_composite(compose_button(export_root, bw, bh), (bx, by))
    return panel


def _body_field(character_id: int, rect: tuple, initial: str = '',
                color: str = 'normal', align: int = _ALIGN_LEFT,
                html: bool = False) -> Tag:
    """A field in the embedded face at the body size, in one of the ini
    colors, placed at a WINDOW-space rect."""
    return define_edit_text(character_id, *stage_rect(rect), '', initial,
                            font_id=CHAR_MW_FONT, rgb=FONT_COLORS[color],
                            height=BODY_HEIGHT_TWIPS, align=align, html=html)


def _fields() -> list:
    """Every dynamic text field as `(tag, instance_name)`."""
    out = [
        (_body_field(CHAR_NAME, centered_field(CAPTION), color='header',
                     align=_ALIGN_CENTER), FIELD_NAME),
        (_body_field(CHAR_HISTORY, history_text_rect(), html=True),
         FIELD_HISTORY),
        (_body_field(CHAR_DISPOSITION, centered_field(client_rect(DISPOSITION)),
                     align=_ALIGN_CENTER), FIELD_DISPOSITION),
        (_body_field(CHAR_BYE, bye_text_rect(), GOODBYE, align=_ALIGN_CENTER),
         FIELD_BYE),
    ]
    for row in range(TOPIC_FIELDS):
        out.append((_body_field(CHAR_TOPIC_FIRST + row, topic_row_rect(row)),
                    f'{FIELD_TOPIC}{row}'))
    return out


def _sprite(char_id: int, image, name: str, rect: tuple) -> list:
    """A bitmap in a one-frame SPRITE at its own origin, placed at a
    WINDOW-space rect: `[bitmap, shape, sprite, (sprite_id, name, at)]`.

    A named bare shape is not scriptable -- only a MovieClip answers to
    `_x`, `_visible` or `_width` -- so every moving part is wrapped.
    """
    x, y, _w, _h = stage_rect(rect)
    w, h = image.size
    return [
        define_bits_lossless2(char_id, w, h, premultiplied_argb(image)),
        define_shape3_bitmap_rects(char_id + 1, [(char_id, 0, 0, w, h)]),
        define_sprite(char_id + 2,
                      [place_object2(depth=1, character_id=char_id + 1)]),
        (char_id + 2, name, (x, y)),
    ]


def _cover(char_id: int, name: str, rect: tuple, depth: int) -> list:
    """A 1 px wide opaque black sprite `rect[3]` tall at a WINDOW-space rect's
    top-left, placed at `depth`; the plugin sets its `_x` and `_width`."""
    x, y, _w, h = stage_rect(rect)
    return [
        define_shape3_solid_rects(char_id, [(0, 0, 1, h)], (0, 0, 0, 255)),
        define_sprite(char_id + 1, [place_object2(depth=1, character_id=char_id)]),
        place_object2(depth=depth, character_id=char_id + 1, name=name,
                      translate=(x, y)),
    ]


def _sprites(export_root) -> list:
    """The moving parts, each `[bitmap, shape, sprite, placement]`, in draw
    order: the caption caps, both scrollbars and thumbs, the list rule."""
    hs = client_rect(HISTORY_SCROLL)
    ts = topic_scroll_rect()
    thumb = compose_thumb(export_root, THUMB_W, THUMB_H)
    parts = [
        (compose_cap(export_root, 'right'), SPRITE_CAP_LEFT, CAPTION),
        (compose_cap(export_root, 'left'), SPRITE_CAP_RIGHT, CAPTION),
        (compose_scrollbar(export_root, hs[3]), SPRITE_HISTORY_SCROLL, hs),
        (thumb, SPRITE_HISTORY_THUMB,
         (hs[0] + THUMB_X, hs[1] + SCROLL_TRACK[0], THUMB_W, THUMB_H)),
        (compose_scrollbar(export_root, ts[3]), SPRITE_TOPIC_SCROLL, ts),
        (thumb, SPRITE_TOPIC_THUMB,
         (ts[0] + THUMB_X, ts[1] + SCROLL_TRACK[0], THUMB_W, THUMB_H)),
        (compose_line(export_root, topic_line_width()), SPRITE_TOPIC_LINE,
         client_rect((TOPICS[0] + LIST_INSET + 2, TOPICS[1] + LIST_INSET,
                      topic_line_width(), 2))),
    ]
    out = []
    for index, (image, name, rect) in enumerate(parts):
        out.append(_sprite(CHAR_SPRITE_FIRST + 3 * index, image, name, rect))
    return out


@functools.lru_cache(maxsize=None)
def font_metrics() -> tuple:
    """`(glyphs, ascent, descent, leading)` of the vendored face, read once."""
    return ttf_glyphs.load(MW_FONT_PATH)


def embed_font() -> Tag:
    """The vendored MysticCards face as a real DefineFont2.

    See: docs/commentary/morrowind_runtime.md#dynamic-text
    """
    glyphs, ascent, descent, leading = font_metrics()
    return define_font2(CHAR_MW_FONT, MW_FONT_NAME, glyphs, ascent, descent,
                        leading)


def dialogue_window(export_root) -> Swf:
    """The real menu: Morrowind's art, at OpenMW's layout, with live text.

    The chrome is one composed bitmap; the moving parts are sprites the plugin
    positions; every string is a field the plugin writes.
    See: docs/commentary/morrowind_runtime.md#the-real-menu
    """
    window = compose_window(export_root)
    ox, oy = window_origin()
    tags = [
        Tag(TAG_FILE_ATTRIBUTES, struct.pack('<I', 0)),
        Tag(TAG_SET_BACKGROUND_COLOR, bytes([0, 0, 0])),
        embed_font(),
        define_bits_lossless2(CHAR_WINDOW_BMP, WINDOW_W, WINDOW_H,
                              premultiplied_argb(window)),
        define_shape3_bitmap_rects(
            CHAR_WINDOW_SHAPE,
            [(CHAR_WINDOW_BMP, ox, oy, WINDOW_W, WINDOW_H)]),
        place_object2(depth=1, character_id=CHAR_WINDOW_SHAPE,
                      name='Window_mc'),
    ]
    tags += _cover(CHAR_COVER, SPRITE_COVER, CAPTION, 2)
    tags += _cover(CHAR_BAR_COVER, SPRITE_BAR_COVER,
                   client_rect(DISPOSITION_FILL), 3)
    depth = 4
    for bitmap, shape, sprite, (char_id, name, at) in _sprites(export_root):
        tags += [bitmap, shape, sprite,
                 place_object2(depth=depth, character_id=char_id, name=name,
                               translate=at)]
        depth += 1
    for field, name in _fields():
        tags.append(field)
        tags.append(place_object2(depth=depth,
                                  character_id=field.character_id, name=name))
        depth += 1
    tags += _modal_tags(export_root, depth)
    tags += [Tag(TAG_SHOW_FRAME, b''), Tag(TAG_END, b'')]
    return Swf(version=9,
               frame_size=pack_rect(0, STAGE_W * TWIP, 0, STAGE_H * TWIP),
               framerate=(24 << 8), framecount=1, tags=tags)


def _rect_lines(prefix: str, rect: tuple) -> list:
    """Four `constexpr int` lines for one STAGE-space rect."""
    return [f'constexpr int k{prefix}{axis} = {value};'
            for axis, value in zip('XYWH', stage_rect(rect))]


def _camel(key: str) -> str:
    """`normal_over` -> `NormalOver`."""
    return ''.join(part.capitalize() for part in key.split('_'))


def _path_lines() -> list:
    """The `_root.` path of every field and sprite, plus the two captions."""
    names = {
        'FieldName': FIELD_NAME, 'FieldHistory': FIELD_HISTORY,
        'FieldDisposition': FIELD_DISPOSITION, 'FieldBye': FIELD_BYE,
        'FieldTopic': FIELD_TOPIC, 'SpriteCover': SPRITE_COVER,
        'SpriteBarCover': SPRITE_BAR_COVER,
        'SpriteCapLeft': SPRITE_CAP_LEFT, 'SpriteCapRight': SPRITE_CAP_RIGHT,
        'SpriteHistoryScroll': SPRITE_HISTORY_SCROLL,
        'SpriteHistoryThumb': SPRITE_HISTORY_THUMB,
        'SpriteTopicScroll': SPRITE_TOPIC_SCROLL,
        'SpriteTopicThumb': SPRITE_TOPIC_THUMB,
        'SpriteTopicLine': SPRITE_TOPIC_LINE,
    }
    names.update({
        'SpriteModal': SPRITE_MODAL, 'FieldModalTitle': FIELD_MODAL_TITLE,
        'FieldModalRow': FIELD_MODAL_ROW, 'FieldModalGold': FIELD_MODAL_GOLD,
        'FieldModalCancel': FIELD_MODAL_CANCEL,
    })
    out = [f'constexpr const char* k{key} = "_root.{value}";'
           for key, value in names.items()]
    out.append(f'constexpr const char* kGoodbye = "{GOODBYE}";')
    out.append(f'constexpr const char* kPersuasion = "{PERSUASION}";')
    return out


def _modal_lines() -> list:
    """The modal's hit rects in STAGE pixels: the panel, row 0 (the rest
    follow at kRowHeight), Cancel; and how many rows there are."""
    lines = []
    for prefix, rect in (('Modal', (0, 0, MODAL_W, MODAL_H)),
                         ('ModalRow', modal_row_rect(0)),
                         ('ModalCancel', MODAL_CANCEL)):
        lines += [f'constexpr int k{prefix}{axis} = {value};'
                  for axis, value in zip('XYWH', modal_stage_rect(rect))]
    lines.append(f'constexpr int kModalRows = {MODAL_ROWS};')
    return lines


def _metric_lines() -> list:
    """The face's vertical metrics and one advance per printable ASCII code,
    in FONT_EM units, so the plugin can lay text out exactly as the movie."""
    glyphs, ascent, descent, leading = font_metrics()
    advances = {code: adv for code, adv, _contours in glyphs}
    table = ', '.join(str(advances.get(code, 0))
                      for code in range(ttf_glyphs.FIRST_CODE,
                                        ttf_glyphs.LAST_CODE + 1))
    return [f'constexpr int kFontEm = {ttf_glyphs.FONT_EM};',
            f'constexpr int kFontAscent = {ascent};',
            f'constexpr int kFontDescent = {descent};',
            f'constexpr int kFontLeading = {leading};',
            f'constexpr int kFirstCode = {ttf_glyphs.FIRST_CODE};',
            f'constexpr int kAdvance[] = {{{table}}};']


def _scalar_lines() -> list:
    """Every non-rect number the plugin needs, named as the skins name them."""
    return [f'constexpr int kCaptionPad = {CAPTION_PAD};',
            f'constexpr int kScrollEnd = {SCROLL_END};',
            f'constexpr int kScrollTrackTop = {SCROLL_TRACK[0]};',
            f'constexpr int kScrollTrackBottom = {SCROLL_TRACK[1]};',
            f'constexpr int kThumbH = {THUMB_H};',
            f'constexpr int kTopicFields = {TOPIC_FIELDS};',
            f'constexpr int kRowHeight = {ROW_H};',
            f'constexpr int kRowPad = {ROW_PAD};',
            f'constexpr int kRowTextShift = {text_top(0, ROW_H)};',
            f'constexpr int kSeparatorHeight = {SEPARATOR_H};',
            f'constexpr int kFontPx = {FONT_PX};',
            f'constexpr int kTextGutter = {TEXT_GUTTER};']


def layout_header() -> str:
    """The plugin's copy of the layout: hit rects in STAGE pixels, the ini
    colors, the paths the parts answer to, and the font's metrics.

    Generated so the movie and the plugin can never disagree about where
    anything is.
    """
    tx, ty, tw, th = client_rect(TOPICS)
    lines = ['// GENERATED by tools/generators/gen_morrowind_menu_swf.py.',
             '// Edit the generator, not this file.', '', '#pragma once', '',
             'namespace tesruntime::mw::layout {', '']
    lines += _rect_lines('Caption', CAPTION)
    lines += _rect_lines('History', history_text_rect())
    lines += _rect_lines('HistoryScroll', client_rect(HISTORY_SCROLL))
    lines += _rect_lines('Topics', (tx + LIST_INSET, ty + LIST_INSET,
                                    tw - 2 * LIST_INSET, th - 2 * LIST_INSET))
    lines += _rect_lines('TopicRow', topic_row_rect(0))
    lines += _rect_lines('TopicScroll', topic_scroll_rect())
    lines += _rect_lines('Bye', client_rect(BYE_BUTTON))
    lines += _rect_lines('DispositionFill', client_rect(DISPOSITION_FILL))
    lines += _modal_lines()
    lines += [''] + _scalar_lines() + ['']
    for key, (r, g, b) in FONT_COLORS.items():
        lines.append(f'constexpr unsigned kColor{_camel(key)} = '
                     f'0x{r:02X}{g:02X}{b:02X};')
    lines += [''] + _path_lines() + [''] + _metric_lines()
    lines += ['', '}  // namespace tesruntime::mw::layout', '']
    return '\n'.join(lines)


def main() -> None:
    """CLI: write the probe, or the real menu plus the plugin's layout header."""
    ap = argparse.ArgumentParser(description='Author the Morrowind menu SWF.')
    ap.add_argument('--hello', action='store_true',
                    help='write the minimal draw probe instead of the menu')
    ap.add_argument('--out',
                    default='tes_runtime/morrowind/interface')
    ap.add_argument('--header', default=HEADER_PATH,
                    help='where the C++ layout header goes')
    ap.add_argument('--export-root', default='export',
                    help='where the Morrowind install is registered')
    ap.add_argument('--preview',
                    help='also write the composed window to this PNG')
    ap.add_argument('--uncompressed', action='store_true',
                    help='FWS rather than CWS, so the bytes can be read')
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, 'morrowind_dialogue.swf')
    movie = hello_world() if args.hello else dialogue_window(args.export_root)
    data = movie.serialize(compress=not args.uncompressed)
    with open(path, 'wb') as fh:
        fh.write(data)
    if not args.hello:
        with open(args.header, 'w', encoding='ascii') as fh:
            fh.write(layout_header())
        print(f'layout -> {args.header}')
    if args.preview and not args.hello:
        compose_window(args.export_root).convert('RGB').save(args.preview)
        print(f'preview -> {args.preview}')
    print(f'wrote {path} ({len(data)} bytes, '
          f'{"FWS" if args.uncompressed else "CWS"}, '
          f'{"probe" if args.hello else "real menu"})')


if __name__ == '__main__':
    main()

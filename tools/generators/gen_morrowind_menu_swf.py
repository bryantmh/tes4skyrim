"""
Author the Morrowind dialogue menu SWF, and the hello-world that gates it.

This is a STANDALONE movie loaded by GFxLoader::LoadMovie through SKSE's
CustomMenu, not characters spliced into a vanilla one -- the engine parses it
from scratch exactly as it parses its own. Skyrim's menus are untouched.

`--hello` writes the minimal probe first: one filled rectangle and one text
field. If that does not draw in game, no amount of dialogue logic on top will,
so it ships and is checked before the real menu is built.

See: docs/commentary/morrowind_runtime.md#the-swf-gate
"""

import argparse
import os
import struct

from asset_convert.ui.swf import (Swf, Tag, define_shape3_solid_rects,
                                  pack_rect, place_object2)

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
TAG_FILE_ATTRIBUTES = 69

#: Character ids. Low and contiguous, as vanilla movies number theirs.
CHAR_BORDER = 1
CHAR_PANEL = 2
CHAR_TEXT = 3

#: DefineEditText flags 1: HasText, HasTextColor, ReadOnly, NoSelect.
_EDIT_FLAGS1 = 0x80 | 0x20 | 0x08 | 0x02

#: Flags 2: UseOutlines, Multiline, WordWrap. HasFont off -- no embedded font.
_EDIT_FLAGS2 = 0x80 | 0x40 | 0x10 | 0x01

#: Morrowind's parchment palette, sampled from its own UI art.
PANEL_RGBA = (38, 30, 22, 235)
BORDER_RGBA = (120, 100, 66, 255)
TEXT_RGB = (220, 208, 180)


def define_edit_text(character_id: int, x: int, y: int, w: int, h: int,
                     var_name: str, initial: str) -> Tag:
    """A dynamic text field bound to `var_name`, which AS2 and C++ can set.

    Bound by VARIABLE NAME rather than instance path, so the field is reachable
    through GFxMovieView::SetVariable without walking the display list.
    """
    body = bytearray()
    body += struct.pack('<H', character_id)
    body += pack_rect(x * TWIP, (x + w) * TWIP, y * TWIP, (y + h) * TWIP)
    body += bytes([_EDIT_FLAGS1, _EDIT_FLAGS2])
    body += bytes([TEXT_RGB[0], TEXT_RGB[1], TEXT_RGB[2], 0xFF])
    body += var_name.encode('ascii') + b'\x00'
    body += initial.encode('ascii') + b'\x00'
    return Tag(TAG_DEFINE_EDIT_TEXT, bytes(body))


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


def main() -> None:
    """CLI: write the probe or the menu to `--out`."""
    ap = argparse.ArgumentParser(description='Author the Morrowind menu SWF.')
    ap.add_argument('--hello', action='store_true',
                    help='write the minimal draw probe instead of the menu')
    ap.add_argument('--out', default='morrowind_runtime/interface')
    ap.add_argument('--uncompressed', action='store_true',
                    help='FWS rather than CWS, so the bytes can be read')
    args = ap.parse_args()

    if not args.hello:
        raise SystemExit('only --hello is implemented; the menu follows once '
                         'the probe is confirmed in game')

    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, 'morrowind_dialogue.swf')
    data = hello_world().serialize(compress=not args.uncompressed)
    with open(path, 'wb') as fh:
        fh.write(data)
    print(f'wrote {path} ({len(data)} bytes, '
          f'{"FWS" if args.uncompressed else "CWS"})')


if __name__ == '__main__':
    main()

"""The Morrowind menu movie parses back into exactly what was authored.

Every DefineEditText flag gates the field after it, so a wrong flag does not
raise -- it slides every later field along and the variable name is read out of
the middle of the color. The first probe set HasMaxLength and HasFontClass by
accident, wrote no font at all, and produced a tag that parsed without error and
could never render a glyph.
See: docs/commentary/morrowind_runtime.md#the-swf-gate
"""

import struct

from asset_convert.ui.swf import Swf
from tools.generators.gen_morrowind_menu_swf import (CHAR_FONT, FONT_LIB,
                                                     FONT_NAME, STAGE_H,
                                                     STAGE_W, TEXT_RGB, TWIP,
                                                     hello_world)

#: DefineEditText, ImportAssets2, DefineShape3 and PlaceObject2 tag codes.
_EDIT_TEXT, _IMPORT, _SHAPE, _PLACE = 37, 71, 32, 26

#: Byte-1 flags that must be set, and those that must not.
_REQUIRED1 = {'HasText': 0x80, 'WordWrap': 0x40, 'Multiline': 0x20,
              'ReadOnly': 0x08, 'HasTextColor': 0x04, 'HasFont': 0x01}
_FORBIDDEN1 = {'Password': 0x10, 'HasMaxLength': 0x02}

#: Byte-2 flags: HasFontClass would make the parser read a name we never wrote.
_REQUIRED2 = {'HasLayout': 0x20, 'NoSelect': 0x10}
_FORBIDDEN2 = {'HasFontClass': 0x80, 'AutoSize': 0x40}

#: The layout block is align(1) + 4 x u16 = 9 bytes, NOT 10.
_LAYOUT_LEN = 9


def _rect_end(data: bytes, offset: int) -> int:
    """Byte offset just past the RECT at `offset`."""
    return offset + (5 + (data[offset] >> 3) * 4 + 7) // 8


def _parse_edit_text(data: bytes) -> dict:
    """Decode a DefineEditText payload the way a SWF parser does."""
    out = {'character_id': struct.unpack_from('<H', data, 0)[0]}
    at = _rect_end(data, 2)
    flags1, flags2 = data[at], data[at + 1]
    at += 2
    out['flags1'], out['flags2'] = flags1, flags2
    if flags1 & _REQUIRED1['HasFont']:
        out['font_id'], out['height'] = struct.unpack_from('<HH', data, at)
        at += 4
    if flags1 & _REQUIRED1['HasTextColor']:
        out['rgba'] = tuple(data[at:at + 4])
        at += 4
    if flags2 & _REQUIRED2['HasLayout']:
        out['align'] = data[at]
        at += _LAYOUT_LEN
    end = data.index(b'\x00', at)
    out['variable'] = data[at:end].decode('ascii')
    at = end + 1
    end = data.index(b'\x00', at)
    out['text'] = data[at:end].decode('ascii')
    out['trailing'] = len(data) - (end + 1)
    return out


def _movie() -> Swf:
    """The probe, round-tripped through serialize/parse."""
    return Swf.parse(hello_world().serialize(compress=True))


def test_stage_is_the_menu_coordinate_space():
    """The frame rect is 1280x720 in twips, matching Skyrim's own menus."""
    swf = _movie()
    rect = swf.frame_size
    nbits = rect[0] >> 3
    value = int.from_bytes(rect, 'big')
    total = len(rect) * 8
    bounds = [(value >> (total - 5 - nbits * (i + 1))) & ((1 << nbits) - 1)
              for i in range(4)]
    assert bounds == [0, STAGE_W * TWIP, 0, STAGE_H * TWIP]


def test_edit_text_flags_gate_only_fields_that_are_written():
    """Set flags match written fields; the misleading ones stay clear."""
    tag = next(t for t in _movie().tags if t.code == _EDIT_TEXT)
    parsed = _parse_edit_text(tag.data)
    for name, bit in _REQUIRED1.items():
        assert parsed['flags1'] & bit, f'byte 1 {name} must be set'
    for name, bit in _FORBIDDEN1.items():
        assert not parsed['flags1'] & bit, f'byte 1 {name} must be clear'
    for name, bit in _REQUIRED2.items():
        assert parsed['flags2'] & bit, f'byte 2 {name} must be set'
    for name, bit in _FORBIDDEN2.items():
        assert not parsed['flags2'] & bit, f'byte 2 {name} must be clear'


def test_edit_text_fields_survive_the_round_trip():
    """The variable name and text read back whole, with nothing left over.

    `trailing == 0` is the real check: a slid field leaves bytes behind.
    """
    tag = next(t for t in _movie().tags if t.code == _EDIT_TEXT)
    parsed = _parse_edit_text(tag.data)
    assert parsed['variable'] == 'probeText'
    assert parsed['text'].startswith('MorrowindRuntime')
    assert parsed['rgba'] == (*TEXT_RGB, 0xFF)
    assert parsed['font_id'] == CHAR_FONT
    assert parsed['trailing'] == 0


def test_the_font_the_field_names_is_imported():
    """A field naming a font id nothing defines has no glyph source."""
    swf = _movie()
    imports = [t for t in swf.tags if t.code == _IMPORT]
    assert len(imports) == 1
    body = imports[0].data
    assert body.startswith(FONT_LIB.encode('ascii') + b'\x00')
    assert FONT_NAME.encode('ascii') + b'\x00' in body
    character = struct.unpack_from('<H', body, len(FONT_LIB) + 1 + 2 + 2)[0]
    assert character == CHAR_FONT


def test_every_placed_character_is_defined_first():
    """PlaceObject2 naming an undefined character draws nothing."""
    swf = _movie()
    defined = set()
    for tag in swf.tags:
        if tag.code in (_SHAPE, _EDIT_TEXT):
            defined.add(struct.unpack_from('<H', tag.data, 0)[0])
        elif tag.code == _IMPORT:
            defined.add(CHAR_FONT)
        elif tag.code == _PLACE and tag.data[0] & 0x02:
            placed = struct.unpack_from('<H', tag.data, 3)[0]
            assert placed in defined, f'character {placed} placed before define'

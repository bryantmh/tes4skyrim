"""
Morrowind's bitmap fonts: `Fonts/*.fnt` metrics plus their `.tex` glyph atlas.

🛑 NOT COMMITTED. Read from the registered Morrowind install at build time and
rasterized into the generated SWF, like the rest of the menu art.

The layout is OpenMW's `components/fontloader/fontloader.cpp`, not a guess: a
296-byte header (`float fontSize`, two `int 1`, `char[284]` atlas name) then
256 x 56-byte `GlyphInfo`.

See: docs/commentary/morrowind_runtime.md#the-font
"""

import struct

from asset_convert.sources.morrowind_assets import find_archived_file

#: `float fontSize` + `int 1` + `int 1` + `char[284]`.
HEADER = 296

#: One GlyphInfo: unknown, 4 corner points, width, height, kerns, ascent.
GLYPH = 56

#: A bitmap font covers exactly the 256 single-byte codes.
GLYPHS = 256

#: Morrowind's UI face. `century_gothic_big` is the same face at title size.
UI_FONT = 'century_gothic_font_regular'

#: Where the loose font files live, relative to the install root.
_FONT_DIR = 'Fonts'


class Glyph:
    """One glyph: where it is in the atlas, and how to place it on a line."""

    __slots__ = ('x', 'y', 'width', 'height', 'advance', 'bearing_x',
                 'bearing_y')

    def __init__(self, x, y, width, height, advance, bearing_x, bearing_y):
        """Store the atlas rect and the pen offsets, all in pixels."""
        self.x, self.y = x, y
        self.width, self.height = width, height
        self.advance = advance
        self.bearing_x, self.bearing_y = bearing_x, bearing_y


class BitmapFont:
    """A parsed `.fnt` and its atlas image."""

    def __init__(self, size: float, glyphs: list, atlas):
        """Hold the authored point size, the 256 glyphs, and the atlas."""
        self.size = size
        self.glyphs = glyphs
        self.atlas = atlas

    def glyph(self, char: str):
        """The glyph for a single-byte character, or None when absent."""
        code = ord(char)
        return self.glyphs[code] if code < GLYPHS else None

    def measure(self, text: str) -> float:
        """Advance width of `text` in pixels."""
        return sum(g.advance for g in map(self.glyph, text) if g is not None)

    def line_height(self) -> int:
        """Pixels between baselines, as MyGUI derives it: font size + 2."""
        return int(self.size) + 2


def _read_glyphs(raw: bytes, size: float, width: int, height: int) -> list:
    """Every GlyphInfo, as atlas pixels and layout offsets.

    The rect is fontloader.cpp's: origin at `TopLeft * (width, height)`, extent
    from `TopRight.x` and `BottomLeft.y`. Advance is `mWidth + mKerningRight`
    and bearing `(mKerningLeft, fontSize - mAscent)`, likewise.
    """
    glyphs = []
    for code in range(GLYPHS):
        off = HEADER + code * GLYPH
        if off + GLYPH > len(raw):
            glyphs.append(None)
            continue
        v = struct.unpack_from('<14f', raw, off)
        left, top, right, bottom = v[1], v[2], v[3], v[6]
        glyph_w, kern_left, kern_right, ascent = v[9], v[11], v[12], v[13]
        x, y = left * width, top * height
        glyphs.append(Glyph(
            x=round(x), y=round(y),
            width=round(right * width - x), height=round(bottom * height - y),
            advance=glyph_w + kern_right,
            bearing_x=kern_left, bearing_y=size - ascent))
    return glyphs


def load(export_root, name: str = UI_FONT) -> BitmapFont:
    """Parse `<name>.fnt` and its atlas from the registered install."""
    from PIL import Image

    metrics = find_archived_file(export_root,
                                 _FONT_DIR + chr(92) + name + '.fnt')
    if metrics is None:
        raise FileNotFoundError(f'{name}.fnt not found in the Morrowind '
                                f'install')
    raw = metrics.read_bytes()
    size = struct.unpack_from('<f', raw, 0)[0]
    atlas_name = raw[12:HEADER].split(b'\x00')[0].decode('latin1')

    texture = find_archived_file(export_root,
                                 _FONT_DIR + chr(92) + atlas_name + '.tex')
    if texture is None:
        raise FileNotFoundError(f'{atlas_name}.tex not found beside {name}')
    blob = texture.read_bytes()
    width, height = struct.unpack_from('<II', blob, 0)
    image = Image.frombytes('RGBA', (width, height),
                            blob[8:8 + width * height * 4])
    blue, green, red, alpha = image.split()
    atlas = Image.merge('RGBA', (red, green, blue, alpha))
    return BitmapFont(size, _read_glyphs(raw, size, width, height), atlas)


def draw(font: BitmapFont, target, text: str, origin: tuple, rgb: tuple):
    """Blit `text` onto `target` at `origin`, tinted `rgb`. Returns the end x.

    Glyphs are white with the shape in alpha, so a tint is a solid fill
    wearing the glyph's own alpha.
    """
    from PIL import Image

    x, y = origin
    for char in text:
        glyph = font.glyph(char)
        if glyph is None:
            continue
        if glyph.width > 0 and glyph.height > 0:
            cut = font.atlas.crop((glyph.x, glyph.y, glyph.x + glyph.width,
                                   glyph.y + glyph.height))
            tint = Image.new('RGBA', cut.size, (*rgb, 255))
            tint.putalpha(cut.split()[3])
            target.alpha_composite(
                tint, (round(x + glyph.bearing_x), round(y + glyph.bearing_y)))
        x += glyph.advance
    return x


def wrap(font: BitmapFont, text: str, width: int) -> list:
    """`text` split into lines that each fit `width` pixels."""
    lines = []
    for paragraph in text.split('\n'):
        current = ''
        for word in paragraph.split(' '):
            candidate = f'{current} {word}'.strip()
            if current and font.measure(candidate) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines

"""Minimal SWF container read/write, plus the few tag builders the UI
conversion needs.

Scope is deliberately narrow: enough of the SWF spec to open a Scaleform GFx
movie, swap CHARACTER DEFINITIONS in it, and write it back.  It is NOT a Flash
authoring library -- there is no ActionScript compiler here and no attempt to
understand the timeline.  That narrowness is the whole safety argument for
`ui_menus.py`: the AS2 classes Skyrim's engine talks to are copied through
byte-for-byte, so the C++/movie interface contract cannot drift.

Container layout (SWF File Format Spec v19, sections 1-2):

    'FWS'|'CWS'|'ZWS'  u8 version  u32 uncompressed_length
    <body>                          # zlib-deflated after byte 8 for CWS
      RECT frame_size
      u16 framerate (8.8 fixed)
      u16 framecount
      tag*                          # u16 (code<<6 | len), len==0x3F -> u32

Everything downstream measures in TWIPS (1/20 px), which is why every public
helper here takes pixels and converts at the boundary -- mixing the two units
is the classic way to emit a shape that parses fine and renders at 5% scale.
"""

import struct
import zlib

TWIPS = 20          # twips per pixel

# Tag codes this module names. Anything else round-trips as opaque bytes.
TAG_END = 0
TAG_SHOW_FRAME = 1
TAG_DEFINE_BITS_LOSSLESS_2 = 36
TAG_DEFINE_SHAPE_3 = 32
TAG_DO_ACTION = 12
TAG_PLACE_OBJECT_2 = 26
TAG_DEFINE_SPRITE = 39
TAG_DO_INIT_ACTION = 59
TAG_EXPORT_ASSETS = 56
TAG_DEFINE_SCALING_GRID = 78
TAG_DEFINE_FONT_2 = 48

#: The em square DefineFont2 glyph outlines are stored in, whatever the source.
FONT_EM = 1024

# Character-defining tags, keyed by code -> whether the id is the first u16.
# Used only to answer "which tag defines character N?", so every entry here
# puts the CharacterID at offset 0, which is true of every tag listed.
_DEFINES_CHARACTER = frozenset({
    2, 4, 6, 7, 10, 11, 13, 14, 20, 21, 22, 32, 33, 34, 35, 36, 37, 39,
    46, 48, 60, 75, 83, 84,
})


class Tag:
    """One SWF tag: a code and its opaque payload.

    Payload is kept as raw bytes rather than a parsed structure so that a tag
    this module does not understand survives a load/save round trip exactly.

    `force_long` remembers that the source used the 6-byte header form for a
    payload short enough for the 2-byte one. Both are legal and mean the same
    thing, but Bethesda's exporter emits the long form for some small tags, and
    re-encoding them shorter makes a "patched one shape" diff 160 bytes wide
    for no reason. Preserving the form keeps an untouched movie byte-identical,
    which is what lets the tests assert that ONLY the intended tags changed.
    """

    __slots__ = ('code', 'data', 'force_long')

    def __init__(self, code: int, data: bytes, force_long: bool = False):
        self.code = code
        self.data = bytes(data)
        self.force_long = force_long

    @property
    def character_id(self):
        """The CharacterID this tag defines, or None if it defines none."""
        if self.code in _DEFINES_CHARACTER and len(self.data) >= 2:
            return struct.unpack_from('<H', self.data, 0)[0]
        return None

    def __repr__(self):
        cid = self.character_id
        return (f'<Tag code={self.code} len={len(self.data)}'
                + (f' id={cid}' if cid is not None else '') + '>')


class Swf:
    """A parsed SWF: the stage header plus a flat list of top-level tags."""

    def __init__(self, version, frame_size, framerate, framecount, tags):
        self.version = version
        self.frame_size = frame_size      # raw RECT bytes, passed through
        self.framerate = framerate        # 8.8 fixed, as stored
        self.framecount = framecount
        self.tags = tags

    # -- reading ----------------------------------------------------------
    @classmethod
    def parse(cls, raw: bytes) -> 'Swf':
        sig = raw[:3]
        version = raw[3]
        if sig in (b'CWS', b'CFX'):
            body = zlib.decompress(raw[8:])
        elif sig == b'ZWS':
            import lzma
            # LZMA SWF stores a 4-byte compressed length then a raw 5-byte
            # LZMA props header; rebuild the .lzma framing decompress wants.
            props = raw[12:17]
            body = lzma.LZMADecompressor(
                format=lzma.FORMAT_ALONE,
                filters=None).decompress(props + b'\xff' * 8 + raw[17:])
        elif sig in (b'FWS', b'GFX'):
            body = raw[8:]
        else:
            raise ValueError(f'not a SWF (signature {sig!r})')

        off = _rect_end(body, 0)
        framerate, framecount = struct.unpack_from('<HH', body, off)
        off += 4
        frame_size = body[:_rect_end(body, 0)]

        tags = []
        while off < len(body):
            (packed,) = struct.unpack_from('<H', body, off)
            off += 2
            code = packed >> 6
            length = packed & 0x3F
            long_form = length == 0x3F
            if long_form:
                (length,) = struct.unpack_from('<I', body, off)
                off += 4
            tags.append(Tag(code, body[off:off + length],
                            force_long=long_form and length < 0x3F))
            off += length
            if code == TAG_END:
                break
        return cls(version, frame_size, framerate, framecount, tags)

    # -- writing ----------------------------------------------------------
    def serialize(self, compress: bool = True) -> bytes:
        body = bytearray(self.frame_size)
        body += struct.pack('<HH', self.framerate, self.framecount)
        for tag in self.tags:
            n = len(tag.data)
            # The long form is legal at ANY length; prefer the short one unless
            # the source used the long form here (see Tag.force_long).
            if n < 0x3F and not tag.force_long:
                body += struct.pack('<H', (tag.code << 6) | n)
            else:
                body += struct.pack('<HI', (tag.code << 6) | 0x3F, n)
            body += tag.data

        total = 8 + len(body)
        if compress:
            return b'CWS' + bytes([self.version]) + struct.pack('<I', total) \
                + zlib.compress(bytes(body), 9)
        return b'FWS' + bytes([self.version]) + struct.pack('<I', total) \
            + bytes(body)

    # -- lookup -----------------------------------------------------------
    def index_of_character(self, character_id: int) -> int:
        """Index into self.tags of the tag defining `character_id`.

        Raises rather than returning -1: every caller here is patching a
        character it has already established exists, so a miss means the input
        movie is not the one the patch was written against, and continuing
        would silently produce an unmodified file.
        """
        for i, tag in enumerate(self.tags):
            if tag.character_id == character_id:
                return i
        raise KeyError(f'no tag defines character {character_id}')

    def exports(self) -> dict:
        """{export_name: character_id} over every ExportAssets tag."""
        out = {}
        for tag in self.tags:
            if tag.code != TAG_EXPORT_ASSETS:
                continue
            (count,) = struct.unpack_from('<H', tag.data, 0)
            off = 2
            for _ in range(count):
                (cid,) = struct.unpack_from('<H', tag.data, off)
                off += 2
                end = tag.data.index(b'\x00', off)
                out[tag.data[off:end].decode('latin1')] = cid
                off = end + 1
        return out

    def scaling_grid_index(self, character_id: int):
        """Index of the DefineScalingGrid for `character_id`, or None."""
        for i, tag in enumerate(self.tags):
            if (tag.code == TAG_DEFINE_SCALING_GRID and len(tag.data) >= 2
                    and struct.unpack_from('<H', tag.data, 0)[0] == character_id):
                return i
        return None

    def next_character_id(self) -> int:
        used = [t.character_id for t in self.tags]
        return max([c for c in used if c is not None], default=0) + 1


# ---------------------------------------------------------------------------
# Bit-level writers
# ---------------------------------------------------------------------------

class BitWriter:
    """MSB-first bit packer. SWF fills bytes from the high bit down."""

    def __init__(self):
        self.buf = bytearray()
        self._cur = 0
        self._n = 0

    def ub(self, value: int, bits: int):
        for i in range(bits - 1, -1, -1):
            self._cur = (self._cur << 1) | ((value >> i) & 1)
            self._n += 1
            if self._n == 8:
                self.buf.append(self._cur)
                self._cur = 0
                self._n = 0

    def sb(self, value: int, bits: int):
        self.ub(value & ((1 << bits) - 1), bits)

    def align(self):
        if self._n:
            self.buf.append(self._cur << (8 - self._n))
            self._cur = 0
            self._n = 0

    def bytes(self) -> bytes:
        self.align()
        return bytes(self.buf)


def _sbits_needed(*values) -> int:
    """Minimum bit width that holds every value as a SIGNED field.

    Zero still needs one bit: a 0-width signed field is legal in the spec but
    several parsers (and Scaleform's own) treat nbits==0 as malformed, so the
    floor here is 1.
    """
    n = 1
    for v in values:
        v = int(v)
        need = 1
        # Grow until v fits in `need` bits two's-complement.
        while not (-(1 << (need - 1)) <= v < (1 << (need - 1))):
            need += 1
        n = max(n, need)
    return n


def pack_rect(x_min: int, x_max: int, y_min: int, y_max: int) -> bytes:
    """A SWF RECT from twip bounds."""
    nbits = _sbits_needed(x_min, x_max, y_min, y_max)
    w = BitWriter()
    w.ub(nbits, 5)
    for v in (x_min, x_max, y_min, y_max):
        w.sb(v, nbits)
    return w.bytes()


def _rect_end(body: bytes, off: int) -> int:
    """Byte offset just past the RECT starting at `off`."""
    nbits = body[off] >> 3
    return off + (5 + nbits * 4 + 7) // 8


def pack_matrix(scale_x: float, scale_y: float,
                translate_x: int, translate_y: int) -> bytes:
    """A SWF MATRIX with scale and translation (no rotation/skew).

    Scale is 16.16 fixed; translation is twips. A bitmap fill matrix maps
    BITMAP PIXELS to shape twips, so the identity-size fill is scale 20.
    """
    w = BitWriter()
    # A scale of exactly 1 is the identity, and vanilla omits the field rather
    # than storing 0x00010000 twice -- which is what makes its identity matrix
    # a single 0x00 byte. Zero translation likewise takes a 0-bit field.
    if (scale_x, scale_y) == (1.0, 1.0):
        w.ub(0, 1)                              # HasScale = 0
    else:
        w.ub(1, 1)
        sx = int(round(scale_x * 65536))
        sy = int(round(scale_y * 65536))
        nbits = _sbits_needed(sx, sy)
        w.ub(nbits, 5)
        w.sb(sx, nbits)
        w.sb(sy, nbits)
    w.ub(0, 1)                                  # HasRotate
    if (translate_x, translate_y) == (0, 0):
        w.ub(0, 5)                              # NTranslateBits = 0
    else:
        nbits = _sbits_needed(translate_x, translate_y)
        w.ub(nbits, 5)
        w.sb(translate_x, nbits)
        w.sb(translate_y, nbits)
    return w.bytes()


# ---------------------------------------------------------------------------
# Tag builders
# ---------------------------------------------------------------------------

def define_bits_lossless2(character_id: int, width: int, height: int,
                          argb: bytes) -> Tag:
    """DefineBitsLossless2 (format 5, 32-bit ARGB).

    `argb` must be width*height*4 bytes, row-major, and **premultiplied by
    alpha** -- that is the spec's requirement and what vanilla Skyrim's own
    bitmaps in messagebox.swf are (verified: 0/600 pixels violate it). Passing
    straight (unpremultiplied) alpha renders as a bright halo around every
    soft edge rather than failing outright, so it is worth asserting.
    """
    expect = width * height * 4
    if len(argb) != expect:
        raise ValueError(f'ARGB data is {len(argb)} bytes, expected {expect}')
    data = struct.pack('<HBHH', character_id, 5, width, height)
    data += zlib.compress(bytes(argb), 9)
    return Tag(TAG_DEFINE_BITS_LOSSLESS_2, data)


def define_scaling_grid(character_id: int, left: float, top: float,
                        right: float, bottom: float,
                        width: float, height: float) -> Tag:
    """DefineScalingGrid: the 9-slice center rect, in PIXELS.

    `left/top/right/bottom` are the fixed border thicknesses and
    `width/height` the character's full size; the tag stores the INNER rect,
    in the character's own coordinate space. The shape this applies to is
    centered on its origin (that is how the character being patched is built),
    so the inner rect is centered too.
    """
    x0 = -width / 2 + left
    x1 = width / 2 - right
    y0 = -height / 2 + top
    y1 = height / 2 - bottom
    data = struct.pack('<H', character_id) + pack_rect(
        int(round(x0 * TWIPS)), int(round(x1 * TWIPS)),
        int(round(y0 * TWIPS)), int(round(y1 * TWIPS)))
    return Tag(TAG_DEFINE_SCALING_GRID, data)


def define_shape3_bitmap_rects(character_id: int, pieces: list) -> Tag:
    """DefineShape3 drawing one clipped-bitmap rectangle per piece.

    pieces: [(bitmap_id, x, y, w, h)] in PIXELS, in the shape's own space. A
    sixth element `(pixel_w, pixel_h)` gives the bitmap's own pixel size when it
    differs from the rect -- the fill then SCALES the bitmap into the rect.

    Each piece gets its own fill style whose matrix places that bitmap so it
    exactly covers its rectangle -- so a piece is a 1:1 blit at the base size,
    and any scaling of the parent clip stretches it. Under a scaling grid whose
    lines coincide with the piece boundaries, that gives the classic 9-slice:
    corners keep their size, edges stretch one way, the center stretches both.

    Fill style 0x41 is CLIPPED (clamp) rather than 0x40 (repeat): a stretched
    border must not wrap around and show its opposite edge.
    """
    if not pieces:
        raise ValueError('no pieces')

    # Bounds are the union of every piece, in twips.
    xs = [p[1] for p in pieces] + [p[1] + p[3] for p in pieces]
    ys = [p[2] for p in pieces] + [p[2] + p[4] for p in pieces]
    bounds = pack_rect(int(round(min(xs) * TWIPS)), int(round(max(xs) * TWIPS)),
                       int(round(min(ys) * TWIPS)), int(round(max(ys) * TWIPS)))

    # -- fill style array: one clipped bitmap fill per piece.
    fills = bytearray()
    n = len(pieces)
    if n >= 0xFF:
        fills += b'\xff' + struct.pack('<H', n)
    else:
        fills.append(n)
    for piece in pieces:
        bitmap_id, x, y, w, h = piece[:5]
        # A piece may carry its own bitmap PIXEL size, which is how a
        # supersampled image maps into a smaller rect: the per-pixel scale is
        # TWIPS * (rect / bitmap), so twice the pixels means half the scale.
        pixel_w, pixel_h = piece[5] if len(piece) > 5 else (w, h)
        fills.append(0x41)                       # clipped bitmap fill
        fills += struct.pack('<H', bitmap_id)
        fills += pack_matrix(TWIPS * w / pixel_w, TWIPS * h / pixel_h,
                             int(round(x * TWIPS)), int(round(y * TWIPS)))
    fills.append(0)                              # empty line style array

    # -- shape records.
    fill_bits = max(1, n.bit_length())
    w = BitWriter()
    w.ub(fill_bits, 4)                           # NumFillBits
    w.ub(0, 4)                                   # NumLineBits

    pen_x = pen_y = 0
    for i, piece in enumerate(pieces):
        _bid, px, py, pw, ph = piece[:5]
        x0 = int(round(px * TWIPS))
        y0 = int(round(py * TWIPS))
        dx = int(round(pw * TWIPS))
        dy = int(round(ph * TWIPS))

        # StyleChangeRecord: move to the rect's top-left and select its fill.
        move_bits = _sbits_needed(x0, y0)
        w.ub(0, 1)                               # TypeFlag = non-edge
        w.ub(0, 1)                               # StateNewStyles
        w.ub(0, 1)                               # StateLineStyle
        w.ub(1, 1)                               # StateFillStyle1
        w.ub(0, 1)                               # StateFillStyle0
        w.ub(1, 1)                               # StateMoveTo
        w.ub(move_bits, 5)
        w.sb(x0, move_bits)
        w.sb(y0, move_bits)
        w.ub(i + 1, fill_bits)                   # FillStyle1 (1-based)
        pen_x, pen_y = x0, y0

        # Four straight edges, clockwise. Each edge is written as a general
        # line (both deltas present) -- the horizontal/vertical special cases
        # would save a few bits and cost clarity.
        for ddx, ddy in ((dx, 0), (0, dy), (-dx, 0), (0, -dy)):
            bits = max(2, _sbits_needed(ddx, ddy))
            w.ub(1, 1)                           # TypeFlag = edge
            w.ub(1, 1)                           # StraightFlag
            w.ub(bits - 2, 4)                    # NumBits
            w.ub(1, 1)                           # GeneralLineFlag
            w.sb(ddx, bits)
            w.sb(ddy, bits)
            pen_x += ddx
            pen_y += ddy

    w.ub(0, 1)                                   # EndShapeRecord
    w.ub(0, 5)

    data = struct.pack('<H', character_id) + bounds + bytes(fills) + w.bytes()
    return Tag(TAG_DEFINE_SHAPE_3, data)


def define_shape3_solid_rects(character_id: int, rects: list,
                              rgba: tuple) -> Tag:
    """DefineShape3 filling each `(x, y, w, h)` rectangle with one RGBA color.

    Rectangles are wound the way vanilla's own bitmap rects are (clockwise from
    the top-left), and share a single fill style.
    """
    if not rects:
        raise ValueError('no rects')
    xs = [r[0] for r in rects] + [r[0] + r[2] for r in rects]
    ys = [r[1] for r in rects] + [r[1] + r[3] for r in rects]
    bounds = pack_rect(int(round(min(xs) * TWIPS)), int(round(max(xs) * TWIPS)),
                       int(round(min(ys) * TWIPS)), int(round(max(ys) * TWIPS)))
    fills = bytes([1, 0x00]) + bytes(rgba) + bytes([0])   # 1 solid fill, 0 lines

    w = BitWriter()
    w.ub(1, 4)                                   # NumFillBits
    w.ub(0, 4)                                   # NumLineBits
    for x, y, width, height in rects:
        x0 = int(round(x * TWIPS))
        y0 = int(round(y * TWIPS))
        dx = int(round(width * TWIPS))
        dy = int(round(height * TWIPS))
        move_bits = _sbits_needed(x0, y0)
        w.ub(0, 1)                               # non-edge record
        w.ub(0, 1); w.ub(0, 1)                   # StateNewStyles, StateLineStyle
        w.ub(1, 1); w.ub(0, 1)                   # StateFillStyle1, ...0
        w.ub(1, 1)                               # StateMoveTo
        w.ub(move_bits, 5)
        w.sb(x0, move_bits)
        w.sb(y0, move_bits)
        w.ub(1, 1)                               # FillStyle1 = 1
        for ddx, ddy in ((dx, 0), (0, dy), (-dx, 0), (0, -dy)):
            bits = max(2, _sbits_needed(ddx, ddy))
            w.ub(1, 1); w.ub(1, 1)               # edge, straight
            w.ub(bits - 2, 4)
            w.ub(1, 1)                           # general line
            w.sb(ddx, bits)
            w.sb(ddy, bits)
    w.ub(0, 1)
    w.ub(0, 5)                                   # EndShapeRecord
    return Tag(TAG_DEFINE_SHAPE_3,
               struct.pack('<H', character_id) + bounds + fills + w.bytes())


def place_object2(depth: int, character_id: int = None, name: str = None,
                  translate: tuple = None) -> Tag:
    """PlaceObject2 for a new character at `depth`.

    A NAME is what makes the child addressable from ActionScript, which is the
    whole reason the frame's pieces are separate clips rather than one shape.

    🛑 The MATRIX IS ALWAYS WRITTEN, even when it is the identity. The spec
    makes `PlaceFlagHasMatrix` optional, but **every** PlaceObject2 in vanilla
    Skyrim's interface movies sets it and spends the one byte an identity
    matrix costs (`06 <depth> <char> 00`). Omitting it produced a tag this
    module and every desktop SWF parser read back correctly while the game
    rendered nothing at all -- the frame's eight border clips simply never
    appeared. Match vanilla; the byte is free.
    """
    flags = 0x04                      # HasMatrix, always (see above)
    if character_id is not None:
        flags |= 0x02
    if name is not None:
        flags |= 0x20
    out = bytearray([flags])
    out += struct.pack('<H', depth)
    if character_id is not None:
        out += struct.pack('<H', character_id)
    tx, ty = translate or (0, 0)
    out += pack_matrix(1.0, 1.0,
                       int(round(tx * TWIPS)), int(round(ty * TWIPS)))
    if name is not None:
        out += name.encode('latin1') + b'\x00'
    return Tag(TAG_PLACE_OBJECT_2, bytes(out))


class BitReader:
    """MSB-first bit reader over a byte buffer, tracking its bit position."""

    def __init__(self, data, offset=0):
        self.data = data
        self.offset = offset
        self.bit = 0

    @property
    def position(self) -> int:
        """Absolute BIT position, which is what a targeted rewrite needs."""
        return self.offset * 8 + self.bit

    def ub(self, n: int) -> int:
        value = 0
        for _ in range(n):
            value = (value << 1) | ((self.data[self.offset] >> (7 - self.bit)) & 1)
            self.bit += 1
            if self.bit == 8:
                self.bit = 0
                self.offset += 1
        return value

    def sb(self, n: int) -> int:
        value = self.ub(n)
        return value - (1 << n) if n and value & (1 << (n - 1)) else value

    def align(self):
        if self.bit:
            self.bit = 0
            self.offset += 1

    def skip_matrix(self):
        if self.ub(1):
            n = self.ub(5); self.sb(n); self.sb(n)      # scale
        if self.ub(1):
            n = self.ub(5); self.sb(n); self.sb(n)      # rotate/skew
        n = self.ub(5); self.sb(n); self.sb(n)          # translate
        self.align()


def poke_bits(data: bytearray, bit_position: int, width: int, value: int):
    """Overwrite `width` bits at an absolute bit position, MSB-first.

    In-place and width-preserving, so a bit-packed structure keeps its length
    and everything after it stays where it is.
    """
    for i in range(width):
        bit = (value >> (width - 1 - i)) & 1
        index = (bit_position + i) // 8
        shift = 7 - ((bit_position + i) % 8)
        data[index] = (data[index] & ~(1 << shift)) | (bit << shift)


def find_cxform_alpha(data, offset: int, length: int):
    """(bit_position, width, current_value) of a PlaceObject2's color-transform
    ALPHA multiplier, or None when the tag has no multiplier terms.

    `offset`/`length` bound one PlaceObject2 payload.
    """
    flags = data[offset]
    if not flags & 0x08:                       # HasCxform
        return None
    at = offset + 1 + 2                        # flags, depth
    if flags & 0x02:
        at += 2                                # character id
    reader = BitReader(data, at)
    if flags & 0x04:
        reader.skip_matrix()
    has_add = reader.ub(1)
    has_mult = reader.ub(1)
    nbits = reader.ub(4)
    if not has_mult or not nbits:
        return None
    # Multiplier terms run red, green, blue, ALPHA.
    for _ in range(3):
        reader.sb(nbits)
    position = reader.position
    return position, nbits, reader.sb(nbits)


def define_sprite(character_id: int, tags: list) -> Tag:
    """A one-frame sprite wrapping `tags` (which must not include ShowFrame or
    End -- both are appended here)."""
    body = bytearray(struct.pack('<HH', character_id, 1))
    for tag in list(tags) + [Tag(TAG_SHOW_FRAME, b''), Tag(TAG_END, b'')]:
        n = len(tag.data)
        if n < 0x3F and not tag.force_long:
            body += struct.pack('<H', (tag.code << 6) | n)
        else:
            body += struct.pack('<HI', (tag.code << 6) | 0x3F, n)
        body += tag.data
    return Tag(TAG_DEFINE_SPRITE, bytes(body))


def do_action(block: bytes) -> Tag:
    return Tag(TAG_DO_ACTION, block)


#: StyleChangeRecord flag bits, by the VALUE each one carries.
_STATE_MOVE, _STATE_FILL1 = 0x01, 0x04


def _style_change(w: BitWriter, move=None, fill1: bool = False,
                  fill_bits: int = 1):
    """One StyleChangeRecord: optionally move the pen, optionally set fill 1.

    🛑 The five flags are ONE 5-bit field, so they are OR-ed and written once.
    Writing them as five sequential single bits reverses them, and every later
    record desynchronizes SILENTLY.
    See: docs/commentary/morrowind_runtime.md#shape-record-flags
    """
    flags = (_STATE_MOVE if move else 0) | (_STATE_FILL1 if fill1 else 0)
    w.ub(0, 1)
    w.ub(flags, 5)
    if move:
        bits = _sbits_needed(move[0], move[1])
        w.ub(bits, 5)
        w.sb(move[0], bits)
        w.sb(move[1], bits)
    if fill1:
        w.ub(1, fill_bits)


def _shape_line(w: BitWriter, dx: int, dy: int):
    """A StraightEdgeRecord: edge=1, straight=1, then a general (x,y) line."""
    bits = max(2, _sbits_needed(dx, dy))
    w.ub(1, 1); w.ub(1, 1)
    w.ub(bits - 2, 4)
    w.ub(1, 1)
    w.sb(dx, bits); w.sb(dy, bits)


def _shape_curve(w: BitWriter, cx: int, cy: int, ax: int, ay: int):
    """A CurvedEdgeRecord (edge=1, straight=0): control delta, anchor delta.

    SWF curves are QUADRATIC, which is exactly what a TrueType outline already
    is -- so a `qCurveTo` maps across with no approximation.
    """
    bits = max(2, _sbits_needed(cx, cy, ax, ay))
    w.ub(1, 1); w.ub(0, 1)
    w.ub(bits - 2, 4)
    for v in (cx, cy, ax, ay):
        w.sb(v, bits)


def _glyph_shape(contours: list) -> bytes:
    """One glyph's SHAPE record: one fill style, no lines, closed contours.

    `contours` is a list of `(start, segments)` where a segment is
    `('l', x, y)` or `('q', cx, cy, x, y)`, in absolute font units with Y
    ALREADY FLIPPED -- SWF's Y axis runs down where a font's runs up.

    The first record moves AND sets the fill together; later contours only
    move, because the fill persists across them.
    """
    w = BitWriter()
    w.ub(1, 4)
    w.ub(0, 4)
    first = True
    for start, segments in contours:
        px, py = start
        _style_change(w, move=(px, py), fill1=first)
        first = False
        for seg in segments:
            if seg[0] == 'l':
                dx, dy = seg[1] - px, seg[2] - py
                if dx or dy:
                    _shape_line(w, dx, dy)
                px, py = seg[1], seg[2]
            else:
                _, cx, cy, ax, ay = seg
                _shape_curve(w, cx - px, cy - py, ax - cx, ay - cy)
                px, py = ax, ay
    w.ub(0, 6)
    return w.bytes()


#: DefineFont2 flags: wide offsets, wide codes, and a layout block.
_FONT_WIDE_OFFSETS, _FONT_WIDE_CODES, _FONT_HAS_LAYOUT = 0x08, 0x04, 0x80


def define_font2(character_id: int, name: str, glyphs: list,
                 ascent: int, descent: int, leading: int) -> Tag:
    """DefineFont2 carrying real outlines, so DefineEditText can use it.

    `glyphs` is `(code, advance, contours)` in FONT_EM units, ordered by code.
    Order, offset base, per-contour fill and the empty bounds table each fail
    SILENTLY if changed.
    See: docs/commentary/morrowind_runtime.md#dynamic-text
    """
    shapes = [_glyph_shape(contours) for _code, _adv, contours in glyphs]
    count = len(glyphs)
    running = 4 * (count + 1)
    offsets = []
    for shape in shapes:
        offsets.append(running)
        running += len(shape)

    body = bytearray(struct.pack('<H', character_id))
    body += bytes([_FONT_WIDE_OFFSETS | _FONT_WIDE_CODES | _FONT_HAS_LAYOUT, 0])
    encoded = name.encode('ascii')
    body += bytes([len(encoded)]) + encoded
    body += struct.pack('<H', count)
    for off in offsets:
        body += struct.pack('<I', off)
    body += struct.pack('<I', running)
    for shape in shapes:
        body += shape
    for code, _adv, _contours in glyphs:
        body += struct.pack('<H', code)
    body += struct.pack('<hhh', ascent, descent, leading)
    for _code, adv, _contours in glyphs:
        body += struct.pack('<h', adv)
    body += bytes(count)
    body += struct.pack('<H', 0)
    return Tag(TAG_DEFINE_FONT_2, bytes(body))

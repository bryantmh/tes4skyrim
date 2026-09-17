"""
TrueType outlines as SWF `DefineFont2` glyph contours.

The source is OpenMW's own `MysticCards.ttf` -- its DEFAULT UI font, an
OFL-licensed Morrowind face derived from Pelagiad. Unlike Bethesda's bitmap
atlas it is redistributable, and being outlines it is what lets the menu use
ordinary `DefineEditText` fields: Scaleform then does wrapping, alignment and
scaling, and no glyph layout code exists on our side.

TrueType curves are quadratic and so are SWF's, so nothing is approximated
here; the only conversions are the em scale and the Y flip.

See: docs/commentary/morrowind_runtime.md#dynamic-text
"""

from asset_convert.ui.swf import FONT_EM

#: The printable ASCII range the menu needs; TES3 text is single-byte.
FIRST_CODE, LAST_CODE = 32, 126


class _ContourPen:
    """Collects a glyph's contours as absolute, Y-flipped integer segments."""

    def __init__(self, scale: float):
        """Scale font units to FONT_EM and flip Y, which SWF runs downward."""
        self.scale = scale
        self.contours = []
        self._start = None
        self._segments = None

    def _pt(self, p):
        """One point, scaled into the em square with Y inverted."""
        return (round(p[0] * self.scale), -round(p[1] * self.scale))

    def moveTo(self, p):
        """Begin a contour at `p`, closing any contour still open."""
        self._flush()
        self._start = self._pt(p)
        self._segments = []

    def lineTo(self, p):
        """Append a straight segment to `p`."""
        x, y = self._pt(p)
        self._segments.append(('l', x, y))

    def qCurveTo(self, *points):
        """Append TrueType's on/off-curve run as explicit quadratics.

        Every point but the last is a control point. Between two CONSECUTIVE
        control points TrueType implies an on-curve anchor at their midpoint,
        so a run of n controls becomes n quadratics.

        A trailing None is the ALL-OFF-CURVE contour: it has no on-curve point
        at all, so it both starts and ends at the midpoint of the last and
        first controls, and arrives here with no preceding `moveTo`.
        """
        pts = [self._pt(p) if p is not None else None for p in points]
        if pts[-1] is None:
            first, last = pts[0], pts[-2]
            pts[-1] = ((first[0] + last[0]) // 2, (first[1] + last[1]) // 2)
            if self._segments is None:
                self._start = pts[-1]
                self._segments = []
        controls, end = pts[:-1], pts[-1]
        for i, (cx, cy) in enumerate(controls):
            if i + 1 < len(controls):
                nx, ny = controls[i + 1]
                anchor = ((cx + nx) // 2, (cy + ny) // 2)
            else:
                anchor = end
            self._segments.append(('q', cx, cy, anchor[0], anchor[1]))

    def curveTo(self, *points):
        """Refuse cubics: a TrueType source never produces them."""
        raise ValueError('cubic outlines are not supported')

    def addComponent(self, name, transform):
        """Composites are flattened by DecomposingPen before reaching here."""
        raise ValueError(f'undecomposed component {name}')

    def closePath(self):
        """Close the contour in progress."""
        self._flush()

    def endPath(self):
        """End an unclosed contour, which is closed implicitly anyway."""
        self._flush()

    def _flush(self):
        """Emit the contour in progress, CLOSED: a last point that is not the
        start gets the straight edge back that TrueType leaves implied."""
        if self._start is not None and self._segments:
            if tuple(self._segments[-1][-2:]) != self._start:
                self._segments.append(('l', *self._start))
            self.contours.append((self._start, self._segments))
        self._start = None
        self._segments = None

    def done(self):
        """Every contour, with any final one flushed."""
        self._flush()
        return self.contours


def load(path, first: int = FIRST_CODE, last: int = LAST_CODE):
    """`(glyphs, ascent, descent, leading)` ready for `define_font2`.

    Glyphs come back ordered by character code, which the CodeTable requires.
    A code the font does not cover is skipped rather than faked. Composite
    glyphs are flattened through `DecomposingPen`, so accented letters arrive
    as plain contours.
    """
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    from fontTools.ttLib import TTFont

    font = TTFont(str(path))
    scale = FONT_EM / font['head'].unitsPerEm
    cmap = font.getBestCmap()
    glyph_set = font.getGlyphSet()

    glyphs = []
    for code in range(first, last + 1):
        name = cmap.get(code)
        if name is None:
            continue
        flat = DecomposingRecordingPen(glyph_set)
        glyph_set[name].draw(flat)
        pen = _ContourPen(scale)
        flat.replay(pen)
        advance = round(glyph_set[name].width * scale)
        glyphs.append((code, advance, pen.done()))

    hhea = font['hhea']
    return (glyphs,
            round(hhea.ascent * scale),
            round(abs(hhea.descent) * scale),
            round(hhea.lineGap * scale))

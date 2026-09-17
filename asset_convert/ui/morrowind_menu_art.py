"""
Morrowind's own menu art, read from the player's install at build time.

🛑 NOTHING HERE IS COMMITTED. The textures are Bethesda's, so they are read
from the registered Morrowind install and composed into the generated SWF,
which is itself a build artifact. The repo carries the LAYOUT (which texture
goes where, at what size) and never the pixels.

See: docs/commentary/morrowind_runtime.md#the-real-menu
"""

from asset_convert.sources.morrowind_assets import find_archived_file
from asset_convert.ui.ui_menus import to_image

#: Border thickness in pixels; every `menu_thick_border_*` edge is 4 px.
BORDER = 4

#: MW_Box's thin border is 2 px, not 4.
BOX_BORDER = 2

#: Stored-path prefix for every UI texture.
_TEX = 'textures' + chr(92)

#: The 9-slice window frame, keyed by the corner or edge each texture fills.
FRAME = {
    'top': 'menu_thick_border_top',
    'bottom': 'menu_thick_border_bottom',
    'left': 'menu_thick_border_left',
    'right': 'menu_thick_border_right',
    'tl': 'menu_thick_border_top_left_corner',
    'tr': 'menu_thick_border_top_right_corner',
    'bl': 'menu_thick_border_bottom_left_corner',
    'br': 'menu_thick_border_bottom_right_corner',
}

#: The thinner frame MW_Box uses for the inset panes.
BOX = {
    'top': 'menu_thin_border_top',
    'bottom': 'menu_thin_border_bottom',
    'left': 'menu_thin_border_left',
    'right': 'menu_thin_border_right',
    'tl': 'menu_thin_border_top_left_corner',
    'tr': 'menu_thin_border_top_right_corner',
    'bl': 'menu_thin_border_bottom_left_corner',
    'br': 'menu_thin_border_bottom_right_corner',
}

#: HB_ALL, the patterned caption plate: the title bar and the Goodbye button.
HEAD = {
    'top': 'menu_head_block_top',
    'bottom': 'menu_head_block_bottom',
    'left': 'menu_head_block_left',
    'right': 'menu_head_block_right',
    'tl': 'menu_head_block_top_left_corner',
    'tr': 'menu_head_block_top_right_corner',
    'bl': 'menu_head_block_bottom_left_corner',
    'br': 'menu_head_block_bottom_right_corner',
}

#: HB_ALL's edges are 2 px, and the plate is 20 px tall.
HEAD_BORDER = 2
HEAD_HEIGHT = 20

#: MW_Button's own frame, a 4 px border distinct from the window's.
BUTTON = {
    'top': 'menu_button_frame_top',
    'bottom': 'menu_button_frame_bottom',
    'left': 'menu_button_frame_left',
    'right': 'menu_button_frame_right',
    'tl': 'menu_button_frame_top_left_corner',
    'tr': 'menu_button_frame_top_right_corner',
    'bl': 'menu_button_frame_bottom_left_corner',
    'br': 'menu_button_frame_bottom_right_corner',
}


class MissingArtError(RuntimeError):
    """The Morrowind install has no such texture, or is not registered."""


def load(export_root, name: str):
    """One UI texture as a Pillow RGBA image, by bare name (no dir, no ext)."""
    path = find_archived_file(export_root, _TEX + name + '.dds')
    if path is None:
        raise MissingArtError(
            f'{name}.dds not found -- register a Morrowind install, or the '
            f'menu cannot be built')
    return to_image(path.read_bytes())


def _edge(img, width: int, height: int):
    """An edge texture resampled to the run it has to cover."""
    from PIL import Image
    return img.resize((max(1, width), max(1, height)), Image.LANCZOS)


def compose_frame(export_root, width: int, height: int, parts=None,
                  border: int = BORDER, fill=None):
    """Morrowind's bordered panel, composed into ONE `width` x `height` image.

    `fill` is the interior RGBA; None leaves it transparent so a pane can sit
    over the window's own background. Corners keep their authored pixels; only
    the four edges resample, along the one axis they run.
    See: docs/commentary/morrowind_runtime.md#one-bitmap
    """
    from PIL import Image

    parts = parts or FRAME
    art = {key: load(export_root, name) for key, name in parts.items()}
    panel = Image.new('RGBA', (width, height), fill or (0, 0, 0, 0))

    inner_w = max(1, width - 2 * border)
    inner_h = max(1, height - 2 * border)
    panel.paste(_edge(art['top'], inner_w, border), (border, 0))
    panel.paste(_edge(art['bottom'], inner_w, border), (border, height - border))
    panel.paste(_edge(art['left'], border, inner_h), (0, border))
    panel.paste(_edge(art['right'], border, inner_h), (width - border, border))
    panel.paste(art['tl'].resize((border, border)), (0, 0))
    panel.paste(art['tr'].resize((border, border)), (width - border, 0))
    panel.paste(art['bl'].resize((border, border)), (0, height - border))
    panel.paste(art['br'].resize((border, border)),
                (width - border, height - border))
    return panel


def compose_box(export_root, width: int, height: int, fill=None):
    """MW_Box: the thin-bordered inset the topic list and history sit in."""
    return compose_frame(export_root, width, height, parts=BOX,
                         border=BOX_BORDER, fill=fill)


def _tile(img, width: int, height: int):
    """`img` repeated across `width`, resampled only in height, so a pattern
    keeps its pitch however long the run."""
    from PIL import Image

    out = Image.new('RGBA', (max(1, width), max(1, height)), (0, 0, 0, 0))
    strip = img.resize((img.width, max(1, height)), Image.LANCZOS)
    for x in range(0, width, img.width):
        out.paste(strip, (x, 0))
    return out


def compose_head(export_root, width: int, height: int = HEAD_HEIGHT):
    """HB_ALL: the patterned plate behind the title and the Goodbye button.

    Its middle is `menu_head_block_middle` TILED rather than stretched -- that
    is the woven pattern running across the caption bar.
    """
    from PIL import Image

    panel = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    middle = load(export_root, 'menu_head_block_middle')
    panel.paste(_tile(middle, width - 2 * HEAD_BORDER, height - 2 * HEAD_BORDER),
                (HEAD_BORDER, HEAD_BORDER))
    panel.alpha_composite(compose_frame(export_root, width, height,
                                        parts=HEAD, border=HEAD_BORDER))
    return panel


def compose_button(export_root, width: int, height: int):
    """MW_Button: its own 4 px frame, over the window's dark background."""
    return compose_frame(export_root, width, height, parts=BUTTON,
                         border=BORDER)


#: MW_VScroll: 14 px wide, a 15 px arrow box at each end, the track box inset 18 px top and 17 bottom.
SCROLL_W = 14
SCROLL_END = 15
SCROLL_TRACK = (18, 17)

#: MW_ArrowUp/Down: the arrow (the drawn part of a 32 px texture) at 10x10, centered in its end box.
ARROW = 10

#: Opaque black, the fill behind every scroll part.
_BLACK = (0, 0, 0, 255)


def compose_scrollbar(export_root, height: int):
    """MW_VScroll without its thumb: arrow boxes at both ends, track between.

    The thumb is composed separately (`compose_thumb`) because it moves.
    """
    from PIL import Image

    panel = Image.new('RGBA', (SCROLL_W, height), (0, 0, 0, 0))
    track_h = height - SCROLL_TRACK[0] - SCROLL_TRACK[1]
    panel.alpha_composite(compose_box(export_root, SCROLL_W, track_h,
                                      fill=_BLACK), (0, SCROLL_TRACK[0]))
    for name, top in (('menu_scroll_up', 0),
                      ('menu_scroll_down', height - SCROLL_END)):
        panel.alpha_composite(compose_box(export_root, SCROLL_W, SCROLL_END,
                                          fill=_BLACK), (0, top))
        art = load(export_root, name)
        arrow = art.crop(art.split()[-1].getbbox()).resize((ARROW, ARROW),
                                                           Image.LANCZOS)
        panel.alpha_composite(arrow, ((SCROLL_W - ARROW) // 2,
                                      top + (SCROLL_END - ARROW) // 2))
    return panel


def compose_thumb(export_root, width: int, height: int):
    """MW_ScrollTrackV: the thumb, a thin-bordered black block."""
    return compose_box(export_root, width, height, fill=_BLACK)


def compose_line(export_root, width: int):
    """MW_HLine: the 2 px rule that separates services from topics."""
    return _edge(load(export_root, 'menu_thin_border_top'), width, 2)


def compose_cap(export_root, side: str, height: int = HEAD_HEIGHT):
    """One 2 px end cap of HB_ALL; `side` is 'left' or 'right'."""
    return _edge(load(export_root, HEAD[side]), HEAD_BORDER, height)


def compose_bar(export_root, width: int, height: int, fraction: float):
    """The disposition bar: `menu_bar_blue` filled to `fraction`, in a box.

    The fill is dimmed so the value printed over it stays readable; Morrowind
    prints the number across the whole bar, not just the filled part.
    """
    from PIL import Image

    panel = Image.new('RGBA', (width, height), (0, 0, 0, 255))
    inner = width - 2 * BOX_BORDER
    filled = max(0, min(inner, round(inner * fraction)))
    if filled:
        bar = load(export_root, 'menu_bar_blue').convert('RGBA')
        dim = Image.new('RGBA', (filled, height - 2 * BOX_BORDER),
                        (0, 0, 0, 90))
        piece = _edge(bar, filled, height - 2 * BOX_BORDER)
        piece.alpha_composite(dim)
        panel.paste(piece, (BOX_BORDER, BOX_BORDER))
    panel.alpha_composite(compose_box(export_root, width, height))
    return panel

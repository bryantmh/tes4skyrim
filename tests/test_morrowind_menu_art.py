"""The real menu composes from Morrowind's own art, and ships none of it.

These need a registered Morrowind install and skip without one, because the
art is deliberately NOT in the repo: the layout is ours, the pixels are
Bethesda's and are read at build time.
See: docs/commentary/morrowind_runtime.md#the-real-menu
"""

import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from asset_convert.ui import morrowind_font as mwfont
from asset_convert.ui.morrowind_menu_art import (BORDER, BOX_BORDER,
                                                 HEAD_HEIGHT, compose_bar,
                                                 compose_box, compose_button,
                                                 compose_frame, compose_head)
from tools.release import package_runtime_dll as pkg

#: Where the pipeline keeps its exports, and so the source registry.
EXPORT_ROOT = 'export'

#: A panel big enough that its edges are longer than one corner.
_W, _H = 240, 120

#: Where the generator writes the built movie by default.
_SHIPPED = 'tes_runtime/morrowind/interface'

ROOT = Path(__file__).resolve().parent.parent


def _have_install() -> bool:
    """True when a Morrowind install is registered to read art from."""
    try:
        from asset_convert.sources import source_registry
        return bool(source_registry.directory_for(EXPORT_ROOT,
                                                  'Morrowind.esm'))
    except Exception:
        return False


needs_install = pytest.mark.skipif(not _have_install(),
                                   reason='no Morrowind install registered')


def test_no_morrowind_art_is_committed():
    """🛑 The repo ships the LAYOUT, never Bethesda's pixels.

    Runs without an install, because it is the one check that must never be
    skipped: a `.dds`, `.tex` or `.fnt` appearing here is art in the repo.
    The built movie is left out of the walk: it holds the art, and is
    ignored rather than committed (see the next test).
    """
    for root, _dirs, files in os.walk('tes_runtime/morrowind'):
        if Path(root).as_posix().startswith(_SHIPPED):
            continue
        for name in files:
            assert not name.lower().endswith(('.dds', '.tex', '.fnt')), (
                f'{os.path.join(root, name)} is Morrowind art -- it must be '
                f'read from the install at build time, never committed')


def test_the_built_menu_is_never_tracked():
    """🛑 The movie embeds the composed art, so git must not carry it.

    A file-type check cannot see art inside a `.swf`; asking git what it
    tracks under the build folder can. Skips outside a git checkout.
    """
    got = subprocess.run(['git', 'ls-files', '--', _SHIPPED], cwd=ROOT,
                         capture_output=True, text=True)
    if got.returncode != 0:
        pytest.skip('not a git checkout')
    assert got.stdout.split() == [], (
        f'{got.stdout.split()} is tracked -- the menu is composed from the '
        f'player\'s own install when TESRuntime.zip is packaged')


def test_the_build_folder_holds_only_the_movie():
    """The generator writes the movie and nothing else there."""
    if not os.path.isdir(_SHIPPED):
        pytest.skip('menu not built yet')
    assert set(os.listdir(_SHIPPED)) <= {'morrowind_dialogue.swf'}


# ---------------------------------------------------------------------------
# Packaging composes the menu from the player's install
# ---------------------------------------------------------------------------


def _packaged(tmp_path) -> list:
    """Package TESRuntime into `tmp_path` and list the archive's members."""
    assert pkg.package(tmp_path, export_root=tmp_path / 'export') == 0
    with zipfile.ZipFile(tmp_path / 'Finished Mods' / 'TESRuntime.zip') as zf:
        return zf.namelist()


def test_packaging_skips_the_menu_without_an_install(tmp_path):
    """No registered Morrowind install: everything else still packages."""
    names = _packaged(tmp_path)
    assert 'SKSE/Plugins/TESRuntime.dll' in names
    assert pkg.MENU_ARC.as_posix() not in names


def test_packaging_adds_the_composed_menu(tmp_path, monkeypatch):
    """With an install, the movie goes straight into the archive."""
    monkeypatch.setattr(pkg, 'morrowind_menu', lambda _root: b'FWS-menu')
    names = _packaged(tmp_path)
    assert pkg.MENU_ARC.as_posix() in names
    with zipfile.ZipFile(tmp_path / 'Finished Mods' / 'TESRuntime.zip') as zf:
        assert zf.read(pkg.MENU_ARC.as_posix()) == b'FWS-menu'


@needs_install
def test_frame_keeps_corners_and_stretches_only_edges():
    """A corner pixel is authored art; the interior is the fill we asked for."""
    fill = (0, 0, 0, 235)
    panel = compose_frame(EXPORT_ROOT, _W, _H, fill=fill)
    assert panel.size == (_W, _H)
    assert panel.getpixel((_W // 2, _H // 2)) == fill
    assert panel.getpixel((0, 0))[3] == 255
    assert panel.getpixel((_W - 1, _H - 1))[3] == 255


@needs_install
def test_box_leaves_its_interior_transparent():
    """MW_Box sits OVER the window, so it must not repaint the background."""
    box = compose_box(EXPORT_ROOT, _W, _H)
    assert box.getpixel((_W // 2, _H // 2))[3] == 0
    assert box.getpixel((0, 0))[3] == 255


@needs_install
def test_borders_are_the_authored_thickness():
    """The thin box border is thinner than the thick window one."""
    assert BOX_BORDER < BORDER
    thick = compose_frame(EXPORT_ROOT, _W, _H)
    thin = compose_box(EXPORT_ROOT, _W, _H)
    assert thick.getpixel((BORDER - 1, _H // 2))[3] == 255
    assert thin.getpixel((BOX_BORDER - 1, _H // 2))[3] == 255
    assert thin.getpixel((BORDER + 2, _H // 2))[3] == 0


@needs_install
def test_head_and_button_are_opaque_plates():
    """Both are backing plates, so neither may leave holes for text to fall in."""
    head = compose_head(EXPORT_ROOT, _W)
    assert head.size == (_W, HEAD_HEIGHT)
    assert head.getpixel((_W // 2, HEAD_HEIGHT // 2))[3] == 255
    button = compose_button(EXPORT_ROOT, _W, 24)
    assert button.getpixel((0, 0))[3] == 255


@needs_install
def test_disposition_bar_fills_to_its_fraction():
    """The bar is blue up to `fraction` and black after it."""
    bar = compose_bar(EXPORT_ROOT, 200, 18, 0.5)
    left = bar.getpixel((40, 9))
    right = bar.getpixel((170, 9))
    assert left[2] > left[0], 'the filled part should read blue'
    assert right[:3] == (0, 0, 0), 'the empty part should stay black'


@needs_install
def test_empty_and_full_bars_do_not_crash():
    """0 and 100 disposition are real values, not edge cases to guard against."""
    assert compose_bar(EXPORT_ROOT, 200, 18, 0.0).getpixel((40, 9))[:3] == (0, 0, 0)
    full = compose_bar(EXPORT_ROOT, 200, 18, 1.0)
    assert full.getpixel((170, 9))[2] > full.getpixel((170, 9))[0]


@needs_install
def test_font_metrics_match_the_authored_values():
    """Glyph rects follow fontloader.cpp, and advance includes right kerning.

    Measured over century_gothic_font_regular: all 94 printable glyphs carry
    `mKerningRight = -1.0`, so advance is one pixel UNDER width -- uniform
    authored tracking, not a parse error. Dropping the kern sets text a pixel
    per glyph too wide.
    """
    font = mwfont.load(EXPORT_ROOT)
    assert font.size == 16.0
    wide = font.glyph('M')
    narrow = font.glyph('i')
    assert wide.width > narrow.width, 'the face is proportional'
    assert font.measure('Goodbye') > font.measure('bye')
    assert wide.advance == wide.width - 1, 'advance carries the -1 kern'


@needs_install
def test_text_wraps_inside_the_width_it_is_given():
    """Every wrapped line fits, so the history pane never overflows its box."""
    font = mwfont.load(EXPORT_ROOT)
    text = ('Come rain or storm, outlander, the Frost-Ghost stands ready to '
            'sail. What is your destination? The winds are strong, at least.')
    for line in mwfont.wrap(font, text, 300):
        assert font.measure(line) <= 300

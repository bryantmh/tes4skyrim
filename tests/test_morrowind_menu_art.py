"""The real menu composes from Morrowind's own art, and ships none of it.

These need a registered Morrowind install and skip without one, because the
art is deliberately NOT in the repo: the layout is ours, the pixels are
Bethesda's and are read at build time.
See: docs/commentary/morrowind_runtime.md#the-real-menu
"""

import os
import subprocess
from pathlib import Path

import pytest

from asset_convert.ui import morrowind_font as mwfont
from asset_convert.ui.menu_art import (ICONS_MORROWIND, ICONS_OBLIVION, STYLE_MORROWIND,
                                       STYLE_SKYRIM, NoIcons, art_sources, resolve)
from asset_convert.ui import menu_art
from asset_convert.ui.skyrim_menu_art import SkyrimArt
from asset_convert.ui.morrowind_menu_art import (BORDER, BOX_BORDER,
                                                 HEAD_HEIGHT, MorrowindArt,
                                                 compose_bar, compose_box,
                                                 compose_button, compose_frame,
                                                 compose_head, compose_stat_bar)
from tools.generators import gen_morrowind_chargen_swf as chargen_swf
from tools.generators import gen_morrowind_stats_swf as stats_swf
from tools.generators import menu_preview
from tools.generators.gen_morrowind_menu_swf import STYLE_MARKER, dialogue_window
from tools.release import package_runtime_dll as pkg

#: Where the pipeline keeps its exports, and so the source registry.
EXPORT_ROOT = 'export'

#: A panel big enough that its edges are longer than one corner.
_W, _H = 240, 120

#: Where the generator writes the built movie by default.
_SHIPPED = 'tes_runtime/morrowind/interface'

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# The repo carries the layout, never the art
# ---------------------------------------------------------------------------


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


def _morrowind_art():
    """The registered installs that hold Morrowind's menu art."""
    return art_sources(EXPORT_ROOT)[0]


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
        f'player\'s own install when TESRuntime is packaged')


def _menu_paths() -> list:
    """Every movie the package composes, the birthsign menu's included."""
    return [arc for arc, _build in pkg.MENUS] + [pkg.BIRTH_MENU]


def test_the_build_folder_holds_only_the_movies():
    """The generators write their movies and nothing else there."""
    if not os.path.isdir(_SHIPPED):
        pytest.skip('menu not built yet')
    assert set(os.listdir(_SHIPPED)) <= {arc.name for arc in _menu_paths()}


# ---------------------------------------------------------------------------
# Packaging composes the menu from the player's install
# ---------------------------------------------------------------------------


def _packaged(tmp_path, choice: tuple = (None, None)) -> list:
    """TESRuntime packaged into `tmp_path` in menu `style`: the files of its mod folder, /-separated."""
    assert pkg.package(tmp_path, export_root=tmp_path / 'export', choice=choice) == 0
    folder = tmp_path / 'TESRuntime'
    return [p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()]


def test_packaging_without_an_install_ships_the_skyrim_style(tmp_path):
    """No registered Morrowind install: auto packages the drawn Skyrim-style menus.

    See: docs/commentary/morrowind_runtime.md#menu-styles
    """
    names = _packaged(tmp_path)
    assert 'SKSE/Plugins/TESRuntime.dll' in names
    assert all(arc.as_posix() in names for arc in _menu_paths())


def test_a_forced_morrowind_style_without_an_install_skips_the_menus(tmp_path):
    """Morrowind's look asked for with no install: everything else still packages."""
    names = _packaged(tmp_path, (STYLE_MORROWIND, None))
    assert 'SKSE/Plugins/TESRuntime.dll' in names
    assert not any(arc.as_posix() in names for arc in _menu_paths())


def test_packaging_adds_every_composed_menu(tmp_path, monkeypatch):
    """With an install, the dialogue, stats and level-up movies go straight in."""
    fake = [(arc, arc.name.encode()) for arc in _menu_paths()]
    monkeypatch.setattr(pkg, 'morrowind_menus', lambda _root, _choice, _chargen=None: fake)
    monkeypatch.setattr(pkg, 'resolve', lambda _root, *_choice: (STYLE_SKYRIM, None))
    names = _packaged(tmp_path)
    for arc, data in fake:
        assert arc.as_posix() in names and (tmp_path / 'TESRuntime' / arc).read_bytes() == data


def test_the_character_sheet_ships_turned_on(tmp_path):
    """The TESRuntime mod carries MorrowindRuntime.ini: the sheet and its skill cap on, on C."""
    arc = 'SKSE/Plugins/MorrowindRuntime/MorrowindRuntime.ini'
    assert arc in _packaged(tmp_path)
    lines = (tmp_path / 'TESRuntime' / arc).read_text(encoding='ascii').splitlines()
    assert '[CharacterSheet]' in lines and 'Hotkey=67' in lines
    assert 'Enabled=1' in lines and 'SkillCap=1' in lines


def test_stats_layout_header_is_the_generators():
    """The committed header is what the generator writes, so plugin and movie agree."""
    committed = (ROOT / stats_swf.HEADER_PATH).read_text(encoding='ascii')
    assert committed == stats_swf.layout_header()


def test_chargen_layout_header_is_the_generators():
    """The class and birthsign menus' header is what their generator writes."""
    committed = (ROOT / chargen_swf.HEADER_PATH).read_text(encoding='ascii')
    assert committed == chargen_swf.layout_header()


def test_every_class_image_is_named_once():
    """The level-up dialog can show every image getLevelupClassImage names."""
    assert len(set(stats_swf.CLASSES)) == len(stats_swf.CLASSES) == 21


@needs_install
def test_frame_keeps_corners_and_stretches_only_edges():
    """A corner pixel is authored art; the interior is the fill we asked for."""
    fill = (0, 0, 0, 235)
    panel = compose_frame(_morrowind_art(), _W, _H, fill=fill)
    assert panel.size == (_W, _H)
    assert panel.getpixel((_W // 2, _H // 2)) == fill
    assert panel.getpixel((0, 0))[3] == 255
    assert panel.getpixel((_W - 1, _H - 1))[3] == 255


@needs_install
def test_box_leaves_its_interior_transparent():
    """MW_Box sits OVER the window, so it must not repaint the background."""
    box = compose_box(_morrowind_art(), _W, _H)
    assert box.getpixel((_W // 2, _H // 2))[3] == 0
    assert box.getpixel((0, 0))[3] == 255


@needs_install
def test_borders_are_the_authored_thickness():
    """The thin box border is thinner than the thick window one."""
    assert BOX_BORDER < BORDER
    thick = compose_frame(_morrowind_art(), _W, _H)
    thin = compose_box(_morrowind_art(), _W, _H)
    assert thick.getpixel((BORDER - 1, _H // 2))[3] == 255
    assert thin.getpixel((BOX_BORDER - 1, _H // 2))[3] == 255
    assert thin.getpixel((BORDER + 2, _H // 2))[3] == 0


@needs_install
def test_head_and_button_are_opaque_plates():
    """Both are backing plates, so neither may leave holes for text to fall in."""
    head = compose_head(_morrowind_art(), _W)
    assert head.size == (_W, HEAD_HEIGHT)
    assert head.getpixel((_W // 2, HEAD_HEIGHT // 2))[3] == 255
    button = compose_button(_morrowind_art(), _W, 24)
    assert button.getpixel((0, 0))[3] == 255


@needs_install
def test_disposition_bar_fills_to_its_fraction():
    """The bar is blue up to `fraction` and black after it."""
    bar = compose_bar(_morrowind_art(), 200, 18, 0.5)
    left = bar.getpixel((40, 9))
    right = bar.getpixel((170, 9))
    assert left[2] > left[0], 'the filled part should read blue'
    assert right[:3] == (0, 0, 0), 'the empty part should stay black'


@needs_install
def test_empty_and_full_bars_do_not_crash():
    """0 and 100 disposition are real values, not edge cases to guard against."""
    assert compose_bar(_morrowind_art(), 200, 18, 0.0).getpixel((40, 9))[:3] == (0, 0, 0)
    full = compose_bar(_morrowind_art(), 200, 18, 1.0)
    assert full.getpixel((170, 9))[2] > full.getpixel((170, 9))[0]


@needs_install
def test_stat_bars_take_the_ini_tints():
    """Health reads red, magicka blue, fatigue green, inside a dark box border."""
    red, blue, green = (compose_stat_bar(_morrowind_art(), 130, 18, rgb).getpixel((60, 9))
                        for rgb in stats_swf.BAR_COLORS)
    assert red[0] > red[2] and blue[2] > blue[0] and green[1] > green[0]


@needs_install
def test_stats_and_levelup_movies_build(tmp_path):
    """Both movies compose from the install and parse as SWF."""
    for path in stats_swf.write_movies(MorrowindArt(_morrowind_art()), str(tmp_path)):
        assert Path(path).read_bytes()[:3] == b'CWS'


def test_perks_button_holds_what_the_plugin_drives():
    """The perks button movie: a `Button` sprite holding the `Glow`, `Cap`, `Key` and
    `Label` perks_button.cpp writes, its key cap and glow drawn at 4x.

    See: docs/commentary/morrowind_runtime.md#perks-button
    """
    chars = menu_preview.characters(stats_swf.perks_button(None).tags)
    children = [sorted(p['name'] for p in body) for kind, body in chars.values() if kind == 'sprite']
    assert ['Cap', 'Glow', 'Key', 'Label'] in children
    scale = stats_swf.BUTTON_SUPERSAMPLE
    sizes = {body.size for kind, body in chars.values() if kind == 'image'}
    assert sizes == {(rect[2] * scale, rect[3] * scale)
                     for rect in (stats_swf.BUTTON_KEY, stats_swf.BUTTON_GLOW)}


# ---------------------------------------------------------------------------
# The Skyrim style: the same menus, drawn, with no install at all
# ---------------------------------------------------------------------------


def test_both_styles_offer_the_same_parts():
    """Swapping a menu's look is swapping its art object, so both have every member.

    See: docs/commentary/morrowind_runtime.md#menu-styles
    """
    parts = {name for name in dir(MorrowindArt) if name.startswith('compose_')}
    assert parts == {name for name in dir(SkyrimArt) if name.startswith('compose_')}
    assert set(MorrowindArt.colors) == set(SkyrimArt.colors)


def test_skyrim_style_builds_every_menu_without_an_install():
    """No game art at all: every movie still builds, marked for the plugin's palette."""
    art = SkyrimArt(NoIcons())
    for build in (dialogue_window, stats_swf.stats_window, stats_swf.levelup_dialog,
                  chargen_swf.class_window, chargen_swf.birth_window):
        movie = build(art)
        assert movie.serialize(compress=True)[:3] == b'CWS'
        assert any(tag.code == 26 and STYLE_MARKER.encode() in tag.data for tag in movie.tags)


def _with_art(monkeypatch, morrowind: bool, oblivion: bool) -> None:
    """Pretend the registered installs hold only the named games' menu art."""
    monkeypatch.setattr(menu_art, 'available', lambda _root: {
        ICONS_MORROWIND: morrowind, ICONS_OBLIVION: oblivion})


def test_unset_choices_follow_the_installed_art(monkeypatch):
    """Morrowind's look and icons when its art is installed, else Skyrim's and Oblivion's.

    See: docs/commentary/morrowind_runtime.md#menu-styles
    """
    _with_art(monkeypatch, True, True)
    assert resolve(EXPORT_ROOT) == (STYLE_MORROWIND, ICONS_MORROWIND)
    _with_art(monkeypatch, False, True)
    assert resolve(EXPORT_ROOT) == (STYLE_SKYRIM, ICONS_OBLIVION)
    _with_art(monkeypatch, False, False)
    assert resolve(EXPORT_ROOT) == (STYLE_SKYRIM, None)


def test_icons_no_install_has_fall_to_the_other_set(monkeypatch):
    """Oblivion's icons asked for with only Morrowind's installed: Morrowind's."""
    _with_art(monkeypatch, True, False)
    assert resolve(EXPORT_ROOT, STYLE_SKYRIM, ICONS_OBLIVION) == (STYLE_SKYRIM, ICONS_MORROWIND)
    assert resolve(EXPORT_ROOT, STYLE_SKYRIM, ICONS_MORROWIND) == (STYLE_SKYRIM, ICONS_MORROWIND)


def test_skyrim_rules_fade_at_their_ends():
    """The frame's rule is solid in the middle and gone at its ends, over the fill."""
    panel = SkyrimArt.compose_frame(_W, _H, fill=(0, 0, 0, 230))
    middle, end = panel.getpixel((_W // 2 + 20, 3)), panel.getpixel((0, 3))
    assert middle[0] > 150 and end[0] < 20 and end[3] >= 230


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

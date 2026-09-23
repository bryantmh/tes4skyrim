"""Build the standalone Oblivion-UI mod: Oblivion's message box + menu cursor.

Reads the user's own Oblivion menu XML and art plus their own vanilla
`Interface/messagebox.swf` and `Interface/cursormenu.swf`, reskins those movies
in place, and writes an installable mod. Nothing Bethesda ships is redistributed -- every input comes
off the machine this runs on, exactly like the rest of the pipeline.

Unlike every other stage this takes NO `-f` plugin: Oblivion's UI lives in
loose menu files and BSAs, not inside any ESM, so there is nothing per-plugin
to convert. It is a GLOBAL action (the GUI's "Convert Oblivion UI" button), run once,
producing one shared artefact.

    output/Oblivion UI/Interface/messagebox.swf     working copy, loose
    output/Oblivion UI/Interface/cursormenu.swf     working copy, loose
    output/Finished Mods/Oblivion UI.zip            installable, Data-rooted

Usage:
  python tools/misc/convert_ui.py
  python tools/misc/convert_ui.py --output-dir PATH
  python tools/misc/convert_ui.py --keep-divider --keep-marker
  python tools/misc/convert_ui.py --no-cursor       # message box only
  python tools/misc/convert_ui.py --oblivion-data PATH --skyrim-data PATH
  python tools/misc/convert_ui.py --preview            # + a PNG of the frame

What lands in the archive is one movie per feature and nothing else. That is
the point: a UI mod that replaces two named movies conflicts with exactly the
mods that replace those same movies -- and `--no-cursor` narrows it to one.

The HUD stat bars were built here too and REVERTED: they rendered broken in
game. See docs/commentary/asset_convert_ui.md for what was measured before that was undone.
"""

import argparse
import os
import sys
from pathlib import Path

# tools/misc/<this file> -> three levels up is the repo root, the same reach
# every other relocated tool uses.
SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from asset_convert.ui import ui_cursor
from asset_convert.ui import ui_menus
from asset_convert.sources.bsa_extract import read_bsa_files
from output_layout import finished_dir, write_mod_zip

MOD_NAME = "Oblivion UI"
MESSAGE_BOX_SWF = r'interface\messagebox.swf'
CURSOR_SWF = r'interface\cursormenu.swf'

# BSA search order per file category. Oblivion splits menu XML and menu art
# across two archives, and Skyrim keeps every movie in one; listing the likely
# archive first means the common case opens one file instead of seventeen.
_BSA_ORDER = {
    'menus': ('Oblivion - Misc.bsa',),
    'textures': ('Oblivion - Textures - Compressed.bsa',),
    'interface': ('Skyrim - Interface.bsa',),
}


def read_game_file(data_dir: Path, rel: str) -> bytes:
    """`rel` from a game's Data folder: LOOSE FILE FIRST, then its archives.

    Loose-first is the games' own resolution order, and it is what makes this
    honor a UI replacer: a user running DarNified UI has edited `menus\\*.xml`
    sitting loose in Data, and reading the BSA instead would silently convert
    vanilla's layout while their screen showed something else.
    """
    loose = data_dir / Path(*rel.split('\\'))
    if loose.is_file():
        return loose.read_bytes()

    top = rel.split('\\', 1)[0].lower()
    preferred = _BSA_ORDER.get(top, ())
    archives = [data_dir / n for n in preferred if (data_dir / n).is_file()]
    archives += [p for p in sorted(data_dir.glob('*.bsa'))
                 if p not in archives]
    for bsa in archives:
        try:
            found = read_bsa_files(str(bsa), [rel])
        except Exception:
            continue                    # a DLC archive we cannot read is not fatal
        raw = found.get(rel.lower())
        if raw is not None:
            return raw
    raise FileNotFoundError(f'{rel} not found loose or in any BSA under {data_dir}')


def find_data_dirs(oblivion=None, skyrim=None):
    """(oblivion Data, skyrim Data) -- explicit paths win over auto-detection."""
    import json
    config = {}
    config_path = SCRIPT_DIR / 'conversion_config.json'
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            config = {}
    from source_paths import find_game_path
    ob = oblivion or find_game_path('oblivion', config)
    sk = skyrim or find_game_path('skyrimse', config)
    return (Path(ob) if ob else None), (Path(sk) if sk else None)


def convert(out_root: Path, oblivion_data=None, skyrim_data=None,
            hide_divider=True, hide_marker=True, opaque=True,
            mute_shadow=True, scale9=True, messagebox=True, cursor=True,
            preview=False) -> int:
    if not messagebox and not cursor:
        print('ERROR: nothing selected to convert (both message box and cursor '
              'are off).')
        return 1
    feats = ' + '.join(f for f, on in (('message boxes', messagebox),
                                       ('cursor', cursor)) if on)
    print('=' * 54)
    print(f'  CONVERT UI  ({feats})')
    print('=' * 54)

    ob_dir, sk_dir = find_data_dirs(oblivion_data, skyrim_data)
    if not ob_dir or not ob_dir.is_dir():
        print('ERROR: Oblivion Data folder not found. Pass --oblivion-data, or '
              'set tes4DataPath in conversion_config.json.')
        return 1
    if not sk_dir or not sk_dir.is_dir():
        print('ERROR: Skyrim SE Data folder not found. Pass --skyrim-data, or '
              'set tes5DataPath in conversion_config.json.')
        return 1
    print(f'  Oblivion: {ob_dir}')
    print(f'  Skyrim:   {sk_dir}')
    print()

    # -- Oblivion's authored layout (only the message box needs it).
    layout = None
    if messagebox:
        try:
            menu_xml = read_game_file(ob_dir, ui_menus.MESSAGE_MENU_XML).decode(
                'latin1')
            prefab_xml = read_game_file(
                ob_dir, ui_menus.GENERIC_BACKGROUND_XML).decode('latin1')
        except FileNotFoundError as exc:
            print(f'ERROR: {exc}')
            return 1
        layout, warnings = ui_menus.read_oblivion_layout(menu_xml, prefab_xml)
        print('  Oblivion layout (read from the menu XML):')
        for key in sorted(layout):
            print(f'    {key:<14} {layout[key]:g}')
        for warning in warnings:
            print(f'    WARNING: {warning}')
        print()

    # -- Oblivion's frame art (only what the selected features need).
    art_sets = []
    if messagebox:
        art_sets += [(ui_menus.BACKGROUND_TEXTURE_DIR,
                      ui_menus.BACKGROUND_TEXTURES),
                     (ui_menus.FOCUS_TEXTURE_DIR, ui_menus.FOCUS_TEXTURES)]
    if cursor:
        art_sets.append((ui_cursor.CURSOR_TEXTURE_DIR,
                         ui_cursor.CURSOR_TEXTURES))
    textures = {}
    for directory, names in art_sets:
        for name in names:
            try:
                textures[name] = read_game_file(
                    ob_dir, os.path.join(directory, name))
            except FileNotFoundError as exc:
                print(f'ERROR: {exc}')
                return 1
    print(f'  Menu art: {len(textures)} textures '
          f'({sum(len(v) for v in textures.values()):,} bytes)')

    built = {}

    # -- the message box.
    if messagebox:
        try:
            movie = read_game_file(sk_dir, MESSAGE_BOX_SWF)
        except FileNotFoundError as exc:
            print(f'ERROR: {exc}')
            return 1
        print(f'  Vanilla movie: {len(movie):,} bytes')
        print()
        try:
            patched, report = ui_menus.patch_message_box(
                movie, textures, layout,
                hide_divider=hide_divider, hide_marker=hide_marker,
                opaque=opaque, mute_shadow=mute_shadow, scale9=scale9)
        except ui_menus.UiConvertError as exc:
            print(f'ERROR: {exc}')
            print('The installed messagebox.swf is not the one this reskin was '
                  'written against, so nothing was written.')
            return 1
        built['messagebox.swf'] = patched
        _print_messagebox_report(report, len(movie), len(patched))

    # -- the menu cursor.
    if cursor:
        try:
            cur_movie = read_game_file(sk_dir, CURSOR_SWF)
        except FileNotFoundError as exc:
            print(f'ERROR: {exc}')
            return 1
        try:
            cur_patched, cur_report = ui_cursor.patch_cursor(
                cur_movie, textures[ui_cursor.CURSOR_TEXTURE])
        except ui_cursor.CursorConvertError as exc:
            print(f'ERROR: {exc}')
            print('The installed cursormenu.swf is not the one the cursor '
                  'reskin was written against, so nothing was written.')
            return 1
        built['cursormenu.swf'] = cur_patched
        print('  Reskinned cursormenu.swf:')
        print(f'    cursor       shape {cur_report["shape"]}, '
              f'{cur_report["size"]} -- {cur_report["note"]}')
        print(f'    size         {len(cur_movie):,} -> {len(cur_patched):,} '
              f'bytes')
        print()

    # -- write the working copies, then the installable archive.
    mod_root = Path(out_root) / MOD_NAME
    interface = mod_root / 'Interface'
    interface.mkdir(parents=True, exist_ok=True)
    for name, data in built.items():
        (interface / name).write_bytes(data)

    zip_path = finished_dir(out_root) / f'{MOD_NAME}.zip'
    write_mod_zip(zip_path, [(str(Path('Interface') / name), interface / name)
                             for name in built])

    if preview and messagebox:
        png = mod_root / 'preview.png'
        _write_preview(png, textures, layout)
        print(f'  Preview: {png}')

    for name in sorted(built):
        print(f'  Loose:  {interface / name}')
    print(f'  Zipped: {zip_path} ({zip_path.stat().st_size:,} bytes)')
    print()
    print('Install it like any other converted mod: the archive root is the '
          'Data folder.')
    replaced = ', '.join('Interface\\' + n for n in sorted(built))
    print(f'It replaces {len(built)} file(s) -- {replaced} -- so it conflicts '
          'only with other mods that replace those same movies.')
    return 0


def _print_messagebox_report(report: dict, old_size: int, new_size: int):
    """The per-field summary of what the message-box reskin changed."""
    frame = report['frame']
    print('  Reskinned messagebox.swf:')
    print(f'    frame        {frame["base"]} shape from a {frame["bitmap"]} '
          f'bitmap, {frame["border"]}px border')
    print(f'                 {frame["note"]}')
    for name, (old, new) in sorted(report['constants'].items()):
        print(f'    {name:<24} {old} -> {new}')
    for name, (old, new) in sorted(report['literals'].items()):
        print(f'    {name:<24} {old} -> {new}')
    for label, (old, new, html) in sorted(report.get('text_color', {}).items()):
        extra = f', {html} html color(s)' if html else ''
        print(f'    text ({label}){"":<10} {old} -> {new}{extra}')
    for key in ('is_vertical', 'divider', 'marker', 'opacity',
                'shadow', 'scale9', 'message_field'):
        if key in report:
            print(f'    {key:<24} {report[key]}')
    print(f'    size         {old_size:,} -> {new_size:,} bytes')
    print()


def _write_preview(path: Path, textures: dict, layout: dict,
                   width: int = 788, height: int = 268):
    """A PNG of the frame at a representative size, for eyeballing the result
    without launching the game.

    🛑 Composed by `ui_menus.compose_frame` -- the SAME function that builds
    the bitmap actually written into the movie -- then 9-sliced to the preview
    size exactly as the re-cut scaling grid slices it in game: corners at their
    own size, edges stretched along one axis only, the center along both.

    It must not re-implement the composition. An earlier version resized each
    slice into its cell, which STRETCHED the edge motifs where compose_frame
    TILES them (measured: the 1024 px top slice squeezed to 0.68x here against
    no squeeze in the real output). A preview whose whole job is to show the
    result without launching the game is worse than none if it renders the
    border at a different density than the movie does.
    """
    from PIL import Image
    border = int(round(layout['border']))
    frame = ui_menus.compose_frame(
        dict(ui_menus.build_frame_slices(textures, border)), border,
        ui_menus.FRAME_SUPERSAMPLE)

    inner_w = max(1, width - 2 * border)
    inner_h = max(1, height - 2 * border)
    src_inner_w = max(1, frame.width - 2 * border)
    src_inner_h = max(1, frame.height - 2 * border)
    # (source box, destination box) per 9-slice cell.
    cols = ((0, border, 0, border),
            (border, src_inner_w, border, inner_w),
            (frame.width - border, border, border + inner_w, border))
    rows = ((0, border, 0, border),
            (border, src_inner_h, border, inner_h),
            (frame.height - border, border, border + inner_h, border))

    out = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    for sy, sh, dy, dh in rows:
        for sx, sw, dx, dw in cols:
            cell = frame.crop((sx, sy, sx + sw, sy + sh))
            if (sw, sh) != (dw, dh):
                cell = cell.resize((dw, dh), Image.LANCZOS)
            out.alpha_composite(cell, (dx, dy))

    backdrop = Image.new('RGBA', out.size, (28, 24, 20, 255))
    backdrop.alpha_composite(out)
    backdrop.convert('RGB').save(path)


def main() -> int:
    ap = argparse.ArgumentParser(
        description='Build the standalone Oblivion-UI mod (message boxes).')
    ap.add_argument('--output-dir', metavar='PATH',
                    help='Output directory (default: output/ in project root)')
    ap.add_argument('--oblivion-data', metavar='PATH',
                    help="Oblivion's Data folder (default: auto-detected)")
    ap.add_argument('--skyrim-data', metavar='PATH',
                    help="Skyrim SE's Data folder (default: auto-detected)")
    ap.add_argument('--keep-divider', action='store_true',
                    help='Keep Skyrim\'s hairline between the message and the '
                         'buttons (Oblivion has none)')
    ap.add_argument('--keep-marker', action='store_true',
                    help="Keep Skyrim's selection arrows instead of Oblivion's "
                         'focus box')
    ap.add_argument('--keep-transparency', action='store_true',
                    help="Keep Skyrim's translucent panel (it places the "
                         'background at 205/256 alpha)')
    ap.add_argument('--keep-shadow', action='store_true',
                    help="Keep the drop shadow Skyrim hangs on the header text")
    ap.add_argument('--no-scale9', action='store_true',
                    help="Do not re-cut Background_mc's scaling grid to the "
                         "border; the frame then rides the panel's uniform "
                         'scale, which stretches the carving on tall or wide '
                         'boxes')
    ap.add_argument('--no-messagebox', action='store_true',
                    help="Skip the message box and convert only the cursor")
    ap.add_argument('--no-cursor', action='store_true',
                    help="Skip the menu cursor and convert only the message box")
    ap.add_argument('--preview', action='store_true',
                    help='Also write a PNG of the frame next to the mod')
    args = ap.parse_args()

    out_root = (Path(args.output_dir) if args.output_dir
                else SCRIPT_DIR / 'output')
    return convert(out_root,
                   oblivion_data=args.oblivion_data,
                   skyrim_data=args.skyrim_data,
                   hide_divider=not args.keep_divider,
                   hide_marker=not args.keep_marker,
                   opaque=not args.keep_transparency,
                   mute_shadow=not args.keep_shadow,
                   scale9=not args.no_scale9,
                   messagebox=not args.no_messagebox,
                   cursor=not args.no_cursor,
                   preview=args.preview)


if __name__ == '__main__':
    sys.exit(main())

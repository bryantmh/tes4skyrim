"""
Which look the runtime's menus are built in, and whose icons they carry.

Two choices, made apart:

- the LOOK: `morrowind` (Morrowind's own frame art) or `skyrim` (drawn here,
  needing nothing);
- the ICONS: `morrowind` (`icons\\k`, `textures\\levelup`, the gold coin) or
  `oblivion` (`textures\\menus`).

Both come from the registered GAME INSTALLS (`source_registry.directories`,
never imported mods), each indexed once for the file that marks its art --
whatever the plugin is called, so Arktwend's Data Files supply Morrowind's and
Nehrim's supply Oblivion's. A choice left unset, or naming art no install
has, falls to what is there: Morrowind's when found, else the other. The
choices are `menuStyle` and `menuIcons` in conversion_config.json.

See: docs/commentary/morrowind_runtime.md#menu-styles
"""

import functools
import struct
from pathlib import Path

from PIL import Image, ImageDraw

from asset_convert.sources import source_registry
from asset_convert.sources.bsa_extract import bsa_has, read_bsa_directory, read_bsa_files
from asset_convert.sources.bsa_extract_morrowind import (is_morrowind_bsa,
                                                         read_entry, read_index)
from asset_convert.ui.morrowind_menu_art import (FRAME, TEXTURES, MorrowindArt,
                                                 MorrowindIcons)
from asset_convert.ui.skyrim_menu_art import SkyrimArt
from asset_convert.ui.ui_menus import to_image
from script_convert.message_menus import birthsign_key

#: conversion_config.json keys for the two choices, and the values each takes.
MENU_STYLE_KEY, MENU_ICONS_KEY = 'menuStyle', 'menuIcons'
STYLE_MORROWIND, STYLE_SKYRIM = 'morrowind', 'skyrim'
STYLES = (STYLE_MORROWIND, STYLE_SKYRIM)
ICONS_MORROWIND, ICONS_OBLIVION = 'morrowind', 'oblivion'
ICON_SETS = (ICONS_MORROWIND, ICONS_OBLIVION)

#: The file that marks each game's menu art in an install.
MORROWIND_MARK = TEXTURES + chr(92) + FRAME['top'] + '.dds'
OBLIVION_MARK = chr(92).join(('textures', 'menus', 'level_up', 'attributes_icons',
                              'attributes_icon_strength.dds'))

#: Oblivion's archive version; later ones (Fallout's, Skyrim's) hold neither game's art.
OBLIVION_BSA = 103

#: Oblivion's attribute icons (textures\menus\level_up\attributes_icons), in TES3 order.
OB_ATTRIBUTES = ('strength', 'intelligence', 'willpower', 'agility', 'speed',
                 'endurance', 'personality', 'luck')

#: Each Skyrim skill (actor value 6..23) as Oblivion pictures it (textures\menus\class\attributes).
OB_SKILLS = {6: 'blade', 7: 'blunt', 8: 'marksman', 9: 'block', 10: 'armorer',
             11: 'heavy_armor', 12: 'light_armor', 13: 'sneak', 14: 'security',
             15: 'sneak', 16: 'alchemy', 17: 'speechcraft', 18: 'alteration',
             19: 'conjuration', 20: 'destruction', 21: 'illusion',
             22: 'restoration', 23: 'mysticism'}

#: The level-up picture's own size (Morrowind's 256x128), which Oblivion's tall paintings are fitted into.
CLASS_PIXELS = (256, 128)

#: A drawn coin for a build with no game's gold icon: gold, 32 px.
COIN_PIXELS = 32
COIN_RGBA = (214, 170, 60, 255)


# ---------------------------------------------------------------------------
# The installs' files
# ---------------------------------------------------------------------------

def _norm(rel: str) -> str:
    """A stored path's archive form: lower case, backslashes."""
    return rel.replace('/', chr(92)).strip(chr(92)).lower()


def _oblivion_entries(bsa: Path) -> list:
    """The paths of an Oblivion-format archive; [] for any other kind."""
    try:
        with open(bsa, 'rb') as fh:
            head = fh.read(8)
            if head[:4] != b'BSA\0' or struct.unpack_from('<I', head, 4)[0] != OBLIVION_BSA:
                return []
            fh.seek(0)
            return [entry[0] for entry in read_bsa_directory(fh)[2]]
    except (OSError, ValueError, struct.error):
        return []


class GameFiles:
    """One install's files by stored path: its Morrowind- and Oblivion-format
    archives (the shipped copy wins, as for every vanilla asset), then loose."""

    def __init__(self, data_dir):
        """Indexes every archive in `data_dir`; a later archive wins a path."""
        self.root = Path(data_dir)
        self.archives = {}
        for bsa in sorted(self.root.glob('*.bsa')):
            if is_morrowind_bsa(bsa):
                for path, (start, size) in read_index(bsa).items():
                    self.archives[path] = (bsa, start, size)
                continue
            for path in _oblivion_entries(bsa):
                self.archives[path.lower()] = (bsa, None, None)

    def has(self, rel: str) -> bool:
        """Whether the install ships `rel`."""
        rel = _norm(rel)
        return rel in self.archives or (self.root / rel).is_file()

    def under(self, folder: str) -> set:
        """Every stored path directly in `folder`, archived or loose."""
        folder = _norm(folder) + chr(92)
        found = {p for p in self.archives if p.startswith(folder) and chr(92) not in p[len(folder):]}
        loose = self.root / folder
        if loose.is_dir():
            found |= {folder + f.name.lower() for f in loose.iterdir() if f.is_file()}
        return found

    def read(self, rel: str):
        """`rel`'s bytes, or None."""
        rel = _norm(rel)
        hit = self.archives.get(rel)
        if hit is None:
            loose = self.root / rel
            return loose.read_bytes() if loose.is_file() else None
        bsa, start, size = hit
        if start is not None:
            return read_entry(bsa, start, size)
        return read_bsa_files(str(bsa), [rel]).get(rel)


class FileChain:
    """Several installs read as one: the first that has a file supplies it."""

    def __init__(self, sources: list):
        self.sources = sources

    def __bool__(self) -> bool:
        """Whether any install is in the chain."""
        return bool(self.sources)

    def under(self, folder: str) -> set:
        """Every stored path directly in `folder` in any install."""
        return set().union(*(source.under(folder) for source in self.sources))

    def read(self, rel: str):
        """`rel`'s bytes from the first install holding it, or None."""
        for source in self.sources:
            data = source.read(rel)
            if data is not None:
                return data
        return None


@functools.lru_cache(maxsize=None)
def art_sources(export_root: str) -> tuple:
    """`(Morrowind art, Oblivion art)`: a FileChain each over the registered
    installs that hold that game's menu art, in registry order. Read once per run."""
    morrowind, oblivion = [], []
    for row in source_registry.directories(str(export_root)):
        if not Path(row['path']).is_dir():
            continue
        files = GameFiles(row['path'])
        if files.has(MORROWIND_MARK):
            morrowind.append(files)
        if files.has(OBLIVION_MARK):
            oblivion.append(files)
    return FileChain(morrowind), FileChain(oblivion)


def effect_icons(export_root, paths: dict) -> dict:
    """`{key: image}` for each magic effect icon (`{key: install path}`) an install
    holds: Morrowind's under `icons\\`, Oblivion's under `textures\\menus\\icons\\`."""
    chains = art_sources(str(export_root))
    out = {}
    for key, path in paths.items():
        data = next((d for d in (chain.read(path) for chain in chains) if d), None)
        if data:
            out[key] = to_image(data)
    return out


def install_has(data_dir, rel: str) -> bool:
    """Whether the install at `data_dir` ships `rel`, loose or in any archive, without indexing them all.

    See: docs/commentary/asset_convert_bsa.md#bsa-name-hash
    """
    root = Path(data_dir)
    if (root / _norm(rel)).is_file():
        return True
    for bsa in sorted(root.glob('*.bsa')):
        if is_morrowind_bsa(bsa):
            if _norm(rel) in read_index(bsa):
                return True
        elif bsa_has(bsa, _norm(rel)):
            return True
    return False


@functools.lru_cache(maxsize=None)
def _available(export_root: str) -> tuple:
    """(Morrowind art installed, Oblivion art installed) over the registered installs. Read once per run."""
    dirs = [row['path'] for row in source_registry.directories(export_root) if Path(row['path']).is_dir()]
    return (any(install_has(d, MORROWIND_MARK) for d in dirs), any(install_has(d, OBLIVION_MARK) for d in dirs))


def available(export_root) -> dict:
    """`{'morrowind': bool, 'oblivion': bool}`: whose menu art an install supplies."""
    morrowind, oblivion = _available(str(export_root))
    return {ICONS_MORROWIND: morrowind, ICONS_OBLIVION: oblivion}


# ---------------------------------------------------------------------------
# Oblivion's icons, and none
# ---------------------------------------------------------------------------

def _drawn_coin():
    """A plain gold disc."""
    image = Image.new('RGBA', (COIN_PIXELS, COIN_PIXELS), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((2, 2, COIN_PIXELS - 3, COIN_PIXELS - 3),
                                  fill=COIN_RGBA, outline=(120, 90, 20, 255))
    return image


def _fit(image, size: tuple):
    """`image`'s painted part, whole, centered in a transparent `size`."""
    painted = image.crop(image.getbbox() or (0, 0, *image.size))
    scale = min(size[0] / painted.width, size[1] / painted.height)
    painted = painted.resize((max(1, round(painted.width * scale)),
                              max(1, round(painted.height * scale))), Image.LANCZOS)
    out = Image.new('RGBA', size, (0, 0, 0, 0))
    out.alpha_composite(painted, ((size[0] - painted.width) // 2,
                                  (size[1] - painted.height) // 2))
    return out


def sign_pictures(files, folder: str) -> dict:
    """{sign key: picture fitted into the class picture's shape} for every texture in `folder`."""
    out = {}
    for path in sorted(files.under(folder)):
        data = files.read(path) if path.endswith('.dds') else None
        if data:
            out.setdefault(birthsign_key(path), _fit(to_image(data), CLASS_PIXELS))
    return out


class OblivionIcons:
    """Icons from Oblivion-format menu art (`textures\\menus`); None where it has none."""

    def __init__(self, files):
        self.files = files

    def _load(self, *parts: str):
        """One texture under textures\\menus, or None."""
        data = self.files.read(chr(92).join(('textures', 'menus') + parts))
        return to_image(data) if data else None

    def attribute(self, index: int):
        """Attribute `index`'s icon (TES3 order)."""
        return self._load('level_up', 'attributes_icons',
                          f'attributes_icon_{OB_ATTRIBUTES[index]}.dds')

    def skill(self, av: int):
        """Skyrim skill `av`'s nearest Oblivion skill picture."""
        return self._load('class', 'attributes', f'load_image_{OB_SKILLS[av]}_small.dds')

    def class_image(self, name: str):
        """The class painting for `name`, fitted whole into the level-up picture's shape."""
        image = self._load('level_up', 'class_creation', f'class_creation_{name}.dds')
        return _fit(image, CLASS_PIXELS) if image else None

    def coin(self):
        """The gold icon, or a drawn coin."""
        return self._load('icons', 'clutter', 'icongold.dds') or _drawn_coin()

    def birthsigns(self) -> dict:
        """{sign key: painting} for every sign an install paints."""
        return sign_pictures(self.files, chr(92).join(('textures', 'menus', 'birthsign')))


class NoIcons:
    """No install supplies icons: none, and a drawn coin."""

    def attribute(self, _index: int):
        """None."""
        return None

    def skill(self, _av: int):
        """None."""
        return None

    def class_image(self, _name: str):
        """None."""
        return None

    def coin(self):
        """A drawn coin."""
        return _drawn_coin()

    def birthsigns(self) -> dict:
        """None."""
        return {}


# ---------------------------------------------------------------------------
# The choice
# ---------------------------------------------------------------------------

def resolve(export_root, style=None, icons=None) -> tuple:
    """`(style, icons)` as built: an unset style is Morrowind's when its art is
    installed, else Skyrim's; unset or missing icons are Morrowind's, else
    Oblivion's, else None."""
    have = available(export_root)
    if style not in STYLES:
        style = STYLE_MORROWIND if have[ICONS_MORROWIND] else STYLE_SKYRIM
    if icons not in ICON_SETS or not have[icons]:
        icons = next((name for name in ICON_SETS if have[name]), None)
    return style, icons


def menu_art(export_root, style=None, icons=None):
    """The art object for the chosen look and icons. A Morrowind look with no
    install holding its art raises `MissingArtError` at the first texture."""
    style, icons = resolve(export_root, style, icons)
    morrowind, oblivion = art_sources(str(export_root))
    icon_set = (MorrowindIcons(morrowind) if icons == ICONS_MORROWIND
                else OblivionIcons(oblivion) if icons == ICONS_OBLIVION else NoIcons())
    if style == STYLE_MORROWIND:
        return MorrowindArt(morrowind, icon_set)
    return SkyrimArt(icon_set)

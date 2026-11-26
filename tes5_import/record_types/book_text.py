"""Book text for Skyrim: HTML the BookMenu renders, and plain text for an item card.

Skyrim's BookMenu knows fonts only by their `$` alias, so a numeric
`<font face=N>` renders no text at all. The inventory item card renders no
HTML: it drops the angle brackets and quoted values and prints the rest.

See: docs/commentary/tes5_import_magic.md#scroll-text
"""

import html
import re

from asset_convert.game_paths import current_namespace

#: Oblivion numeric font face (SFontFile_N in Oblivion.ini) -> Skyrim font alias (Interface\fontconfig.txt).
_FACE_TO_FONT = {
    '1': '$SkyrimBooks',
    '2': '$SkyrimBooks',
    '3': '$SkyrimBooks',
    '4': '$DaedricFont',
    '5': '$HandwrittenFont',
}

#: A tag that ends a line or block: a line break, paragraph or division.
_BREAK_TAG = re.compile(r'<\s*br\s*/?\s*>|<\s*/?\s*(?:p|div)\b[^>]*>', re.IGNORECASE)

#: Any other tag.
_TAG = re.compile(r'<[^>]*>')


def _remap_font_tag(m: re.Match) -> str:
    """An Oblivion `<font ...>` tag as `<font face='$Alias'>`, other attributes dropped; `</font>` kept."""
    if m.group(1):
        return '</font>'
    face_m = re.search(r'face\s*=\s*["\']?(\d)', m.group(2) or '', flags=re.IGNORECASE)
    font = _FACE_TO_FONT.get(face_m.group(1) if face_m else '1', '$SkyrimBooks')
    return f"<font face='{font}'>"


def _prefix_img(m: re.Match) -> str:
    """An `<IMG src=...>` path relative to Textures\\Menus as an img:// Data path."""
    path = m.group(2).replace('\\', '/')
    if not path.lower().startswith('img://'):
        path = 'img://textures/' + current_namespace() + '/menus/' + path.lstrip('/')
    return f"<img src='{path}'"


def fix_book_html(text: str) -> str:
    """TES4 book HTML for the BookMenu: named fonts, img:// paths, CRLF as <br>."""
    text = re.sub(r'<(/?)[Ff][Oo][Nn][Tt](\s[^>]*)?>', _remap_font_tag, text)
    text = re.sub(r'<IMG\s+src=(["\']?)([^"\'>\s]+)\1', _prefix_img, text, flags=re.IGNORECASE)
    return re.sub(r'(?<!>)\r\n', '<br>\r\n', text)


def plain_text(text: str) -> str:
    """Book HTML as the one-paragraph plain text an item card shows: tags gone, entities decoded."""
    text = html.unescape(_TAG.sub('', _BREAK_TAG.sub(' ', text)))
    return ' '.join(text.split())

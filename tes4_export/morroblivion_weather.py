"""
Morroblivion's Morrowind weathers and climate, copied into a hybrid root.

Morrowind authors no weather records, so an authored-mode plugin falls back to
Skyrim's. Hybrid mode copies Morroblivion's ten `mw*` weathers (with its
second ash storm) and `mwMorrowindClimate` into the masterless plugin, and its
regions and worldspace name them; a dependent finds them through its master.
The sky comes from Morroblivion; each weather's sounds are the ones
`Morrowind.ini` names for it, Morrowind's own SOUN records.

See: docs/commentary/tes4_export_morrowind.md#hybrid-weathers
"""

import os

from output_layout import record_dir
from tes5_import.base.text_reader import parse_export_file

from .morrowind_region import WEATHER_EDITOR_IDS
from .record_types.common import escape_value
from .record_types.morrowind_quiz import ini_path

#: Morroblivion's weathers -> the Morrowind.ini section naming their sounds.
INI_SECTIONS = {
    'mwClear': 'Weather Clear', 'mwCloudy': 'Weather Cloudy', 'mwFoggy': 'Weather Foggy',
    'mwOvercast': 'Weather Overcast', 'mwRain': 'Weather Rain', 'mwThunder': 'Weather Thunderstorm',
    'mwAsh': 'Weather Ashstorm', 'mwWeatherAshstorm': 'Weather Ashstorm',
    'mwWeatherAshstormBlight': 'Weather Blight', 'mwSnow': 'Weather Snow', 'mwBlizzard': 'Weather Blizzard'}

#: Morroblivion's Morrowind climate, which names the weathers.
CLIMATE_EDID = 'mwMorrowindClimate'

#: Morrowind.ini sound keys -> the TES4 weather sound type (0 default, 1 precipitation, 3 thunder).
SOUND_KEYS = (('Ambient Loop Sound ID', 0), ('Rain Loop Sound ID', 1), ('Thunder Sound ID 0', 3),
              ('Thunder Sound ID 1', 3), ('Thunder Sound ID 2', 3), ('Thunder Sound ID 3', 3))

#: Morrowind.ini's text encoding (the game's Western code page).
INI_CODEC = 'cp1252'

#: Lines the copy rewrites itself rather than carrying over.
_DROPPED = ('FormID', 'Signature', 'SoundCount', 'Sound[')

#: The texture and model lines of a copied weather or climate.
TEXTURE_KEYS = ('CNAM.LowerCloudLayer', 'DNAM.UpperCloudLayer', 'FNAM.SunTexture', 'GNAM.GlareTexture')


def copy_weathers(out: dict, ctx, source_path: str, exports) -> list:
    """Copy the weathers and climate into a masterless plugin's `out`; the texture paths they borrow.

    Regions naming a bare TES3 type and the worldspace's climate are pointed at the copies.
    """
    if ctx.master_names or ctx.index.lookup_editor_id(CLIMATE_EDID):
        return []
    found = _morroblivion_records(ctx.export_root, exports)
    if CLIMATE_EDID not in found:
        return []
    ini = _ini_sounds(ini_path(source_path))
    sounds = _own_editor_ids(out, 'SOUN')
    ids = {edid: ctx.derive('morroblivionweather:' + edid) for edid in found}
    out.setdefault('WTHR', []).extend(
        (ids[edid], _weather_lines(found[edid], ini.get(INI_SECTIONS.get(edid, ''), []), sounds))
        for edid in sorted(found) if edid != CLIMATE_EDID)
    by_old = {rec['FormID'].upper(): ids[edid] for edid, rec in found.items()}
    out.setdefault('CLMT', []).append((ids[CLIMATE_EDID], _climate_lines(found[CLIMATE_EDID], by_old)))
    _point_regions(out, ids)
    _point_world(out, ids[CLIMATE_EDID])
    return sorted({rec[k] for rec in found.values() for k in TEXTURE_KEYS if rec.get(k)})


def _morroblivion_records(export_root: str, exports) -> dict:
    """{EditorID: export record} of Morroblivion's copied weathers and its climate."""
    wanted = set(INI_SECTIONS) | {CLIMATE_EDID}
    found = {}
    for name in exports:
        for sig in ('WTHR', 'CLMT'):
            path = os.path.join(str(record_dir(export_root, name)), sig + '.txt')
            for rec in parse_export_file(path) if os.path.isfile(path) else ():
                if rec.get('EditorID') in wanted:
                    found.setdefault(rec['EditorID'], rec)
    return found


def _ini_sounds(path: str) -> dict:
    """{ini section: [(sound EditorID, TES4 sound type)]} of every `[Weather ...]` section."""
    out, section = {}, None
    if not os.path.isfile(path):
        return out
    with open(path, encoding=INI_CODEC, errors='replace') as handle:
        for line in handle:
            text = line.strip()
            if text.startswith('['):
                section = text[1:-1]
                continue
            key, equals, value = text.partition('=')
            kind = dict(SOUND_KEYS).get(key)
            if section and equals and kind is not None and value and value.lower() != 'none':
                out.setdefault(section, []).append((value, kind))
    return out


def _own_editor_ids(out: dict, sig: str) -> dict:
    """{EditorID, lower case: FormID} of the plugin's own `sig` records."""
    found = {}
    for form_id, lines in out.get(sig, ()):
        for line in lines:
            if line.startswith('EditorID='):
                found[line[len('EditorID='):].lower()] = form_id
    return found


def _weather_lines(rec: dict, sounds: list, own: dict) -> list:
    """A copied weather's lines: Morroblivion's sky, Morrowind's own sounds (those this plugin holds)."""
    lines = [f'{k}={escape_value(v)}' for k, v in rec.items() if not k.startswith(_DROPPED)]
    named = [(own[s.lower()], kind) for s, kind in sounds if s.lower() in own]
    lines.append(f'SoundCount={len(named)}')
    for i, (form_id, kind) in enumerate(named):
        lines += [f'Sound[{i}].FormID={form_id}', f'Sound[{i}].Type={kind}']
    return lines


def _climate_lines(rec: dict, by_old: dict) -> list:
    """The copied climate's lines, each weather it lists pointed at its copy (one not copied is left out)."""
    kept = [i for i in range(int(rec.get('WeatherCount', 0)))
            if rec.get(f'Weather[{i}].FormID', '').upper() in by_old]
    lines = [f'{k}={escape_value(v)}' for k, v in rec.items()
             if not k.startswith(('FormID', 'Signature', 'WeatherCount', 'Weather['))]
    lines.append(f'WeatherCount={len(kept)}')
    for n, i in enumerate(kept):
        lines += [f'Weather[{n}].FormID={by_old[rec[f"Weather[{i}].FormID"].upper()]}',
                  f'Weather[{n}].Chance={rec.get(f"Weather[{i}].Chance", "0")}']
    return lines


def _point_regions(out: dict, ids: dict) -> None:
    """Name each copied weather where a region names its bare TES3 type."""
    for _form_id, lines in out.get('REGN', ()):
        for i, line in enumerate(lines):
            key, _sep, value = line.partition('.MorrowindType=')
            if _sep and value.isdigit() and int(value) < len(WEATHER_EDITOR_IDS):
                lines[i] = f'{key}.FormID={ids[WEATHER_EDITOR_IDS[int(value)]]}'


def _point_world(out: dict, climate: str) -> None:
    """Make each worldspace that falls back to Skyrim's climate name the copied one."""
    for _form_id, lines in out.get('WRLD', ()):
        for i, line in enumerate(lines):
            if line.startswith('CNAM.Vanilla='):
                lines[i] = f'CNAM.Climate={climate}'

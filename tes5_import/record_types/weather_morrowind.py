"""Morrowind's ten hardcoded weather types as Skyrim WTHR records.

TES3 authors no weather records: a region's WEAT is one chance per type.
Morroblivion converted all ten, and the export names those when it can; a
region it could not name carries the bare type, which stands on the vanilla
weather a census of Skyrim.esm and Dragonborn.esm picked.

See: docs/commentary/tes5_import_weather.md#morrowind-weather-types
"""

import os
import re

from tes4_export.morrowind_region import WEATHER_EDITOR_IDS

from ..base.owned_records import owner_row
from ..base.vanilla_quests import VANILLA_MASTERS

#: Per TES3 weather type, in OpenMW's order: (plugin, local id, EditorID) of the vanilla WTHR standing in for it.
VANILLA_WEATHERS = (
    ('Skyrim.esm', 0x00081A, 'SkyrimClear'),
    ('Skyrim.esm', 0x012F89, 'SkyrimCloudy'),
    ('Skyrim.esm', 0x0C821E, 'SkyrimFog'),
    ('Skyrim.esm', 0x0D299E, 'SkyrimOvercastWar'),
    ('Skyrim.esm', 0x0C821F, 'SkyrimOvercastRain'),
    ('Skyrim.esm', 0x0C8220, 'SkyrimStormRain'),
    ('Dragonborn.esm', 0x032336, 'DLC02VolcanicAshStorm01'),
    ('Dragonborn.esm', 0x032336, 'DLC02VolcanicAshStorm01'),
    ('Skyrim.esm', 0x04D7FB, 'SkyrimOvercastSnow'),
    ('Skyrim.esm', 0x0C8221, 'SkyrimStormSnow'),
)

#: A region weather the export could name only by its TES3 type.
_TYPE_LINE = re.compile(r'^RegionData\[\d+\]\.Weather\[\d+\]\.MorrowindType=(\d+)$', re.M)

#: The output plugin's master list, which a vanilla weather's index byte is read from.
_MASTERS = []


def _named_types(export_dir: str) -> set:
    """The TES3 weather types the export's regions name by type alone."""
    path = os.path.join(export_dir, 'REGN.txt')
    if not os.path.isfile(path):
        return set()
    with open(path, encoding='utf-8') as handle:
        return {int(kind) for kind in _TYPE_LINE.findall(handle.read())
                if int(kind) < len(VANILLA_WEATHERS)}


def with_weather_masters(masters: list, export_dir: str) -> list:
    """`masters` with each vanilla plugin past Skyrim.esm that a region weather names, after Skyrim.esm."""
    listed = {name.lower() for name in masters}
    named = {VANILLA_WEATHERS[kind][0] for kind in _named_types(export_dir)}
    extra = [name for name in VANILLA_MASTERS[1:]
             if name in named and name.lower() not in listed]
    return masters[:1] + extra + masters[1:]


def set_weather_masters(masters: list) -> None:
    """Record the output plugin's master list for `vanilla_weather`."""
    _MASTERS[:] = masters


def vanilla_weather(kind: int) -> int:
    """The output-space FormID of TES3 weather type `kind`'s stand-in, or 0 when its plugin is no master."""
    if not 0 <= kind < len(VANILLA_WEATHERS):
        return 0
    plugin, local, _edid = VANILLA_WEATHERS[kind]
    names = [name.lower() for name in _MASTERS]
    if plugin.lower() not in names:
        return 0
    return (names.index(plugin.lower()) << 24) | local


def weather_rows(plugin: str, masters: list, master_index) -> list:
    """`weather<type>=Plugin|FormID` per TES3 weather type, for MorrowindRuntime.

    Morroblivion's WTHR when a converted master holds it, else the vanilla
    stand-in, which the runtime finds by name whether or not it is a master.
    """
    rows = []
    for kind, (vanilla, local, _edid) in enumerate(VANILLA_WEATHERS):
        found = (master_index.find_by_edid(b'WTHR', WEATHER_EDITOR_IDS[kind])
                 if master_index is not None else 0)
        rows.append(owner_row(f'weather{kind}', found, plugin, masters) if found
                    else f'weather{kind}={vanilla}|{local:08X}')
    return rows

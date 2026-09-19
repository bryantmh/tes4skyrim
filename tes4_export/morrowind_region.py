"""Morrowind REGN -> the TES4 region vocabulary, for weather only.

Morrowind has no weather records: the engine hardcodes ten weather types and a
region's WEAT subrecord is one chance byte per type. Morroblivion already
authored all ten as real WTHR records, so a region converts by naming those.

See: docs/commentary/tes4_export_morrowind.md#region-weather
"""

import struct

from .morrowind_ids import encode_editor_id
from .morrowind_world import TES4_CELL_SIZE

#: Morrowind's hardcoded weather order -> the Morroblivion WTHR EditorID.
WEATHER_EDITOR_IDS = (
    'mwClear', 'mwCloudy', 'mwFoggy', 'mwOvercast', 'mwRain', 'mwThunder',
    'mwAsh', 'mwWeatherAshstormBlight', 'mwSnow', 'mwBlizzard')

#: RDAT entry type for a weather list (xEdit wbDefinitionsTES4).
_REGION_DATA_WEATHER = 3

#: Region weather layers over the climate, as vanilla Skyrim's own regions do.
_OVERRIDE = 1
_PRIORITY = 95

#: A region's polygon is grown this far past its cells so neighbours abut.
_EDGE_FALLOFF = 1024


def _weather_list(weat: bytes, ctx) -> list:
    """(FormID, chance) for every weather type this region authors.

    A chance of zero is dropped rather than emitted: the entry would claim a
    weather the region never rolls.
    """
    entries = []
    for index, chance in enumerate(weat[:len(WEATHER_EDITOR_IDS)]):
        if not chance:
            continue
        form_id = ctx.index.lookup_editor_id(WEATHER_EDITOR_IDS[index])
        if form_id:
            entries.append((form_id, chance))
    return entries


def _polygon(grids) -> str:
    """The RPLD hex for the bounding rectangle of these grid squares.

    Morrowind assigns a region per CELL and authors no polygon; the rectangle
    is what makes the record legal, while the per-cell XCLR list is what
    applies the weather.
    """
    xs = [g[0] for g in grids]
    ys = [g[1] for g in grids]
    x0 = float(min(xs) * TES4_CELL_SIZE)
    y0 = float(min(ys) * TES4_CELL_SIZE)
    x1 = float((max(xs) + 1) * TES4_CELL_SIZE)
    y1 = float((max(ys) + 1) * TES4_CELL_SIZE)
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    return b''.join(struct.pack('<ff', x, y)
                    for x, y in corners).hex().upper()


def _weather_lines(weathers: list) -> list:
    """The RegionData[0] weather entries, as KEY=VALUE lines."""
    lines = [f'RegionData[0].WeatherCount={len(weathers)}']
    for i, (form_id, chance) in enumerate(weathers):
        lines.append(f'RegionData[0].Weather[{i}].FormID={form_id}')
        lines.append(f'RegionData[0].Weather[{i}].Chance={chance}')
    return lines


def _region_record(record_id: str, weat: bytes, ctx) -> tuple:
    """One region as (FormID, lines), or None when nothing applies it here.

    A region a master already converted is left alone: its record carries the
    master's own EditorID and weather list, and re-emitting it here under our
    escaped spelling would override it with a worse copy.
    """
    if ctx.index.lookup_region(record_id):
        return None
    grids = ctx.region_grids(record_id)
    weathers = _weather_list(weat, ctx) if weat else []
    if not grids or not weathers:
        return None
    return (ctx.region_id(record_id), [
        f'EditorID={encode_editor_id(record_id)}',
        'RCLR.R=0', 'RCLR.G=0', 'RCLR.B=0',
        f'WNAM.Worldspace={ctx.worldspace_id()}',
        'AreaCount=1',
        f'Area[0].EdgeFalloff={_EDGE_FALLOFF}',
        f'Area[0].PointsHex={_polygon(grids)}',
        'RegionDataCount=1',
        f'RegionData[0].Type={_REGION_DATA_WEATHER}',
        f'RegionData[0].Override={_OVERRIDE}',
        f'RegionData[0].Priority={_PRIORITY}']
        + _weather_lines(weathers))


def region_records(records, ctx) -> list:
    """(FormID, lines) for every region THIS plugin's cells actually name.

    The weather may be authored by a master: Tamriel_Data holds 80 regions and
    13 cells, while the cells naming them are Tamriel Rebuilt's. So the cells
    decide what emits, and a region absent from this plugin is read from its
    master's source ([master blindness](../CLAUDE.md#master-blindness)).
    """
    weat_by_id = {rec.record_id.lower(): ctx.region_weather(rec)
                  for rec in records
                  if rec.type == 'REGN' and not rec.deleted}
    weat_by_id.update({k: v for k, v in ctx.master_region_weather().items()
                       if k not in weat_by_id})
    out = []
    for key in sorted(ctx.region_cells):
        record = _region_record(
            ctx.region_name(key), weat_by_id.get(key), ctx)
        if record:
            out.append(record)
    return out

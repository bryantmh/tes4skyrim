"""Landscape dressing converters: REGN, LSCR, WATR.

Regions, water types and load screens are worldspace-scoped decoration rather
than placement geometry, so they live apart from the CELL/WRLD/REFR converters
in world.py.
"""

import os
import struct

from ..base.master_export import values_of
from ..base.tes5_reader import records
from ..base.text_reader import get_hex_bytes, remap_formid
from .common import (
    TES4_DEFAULT_MUSIC_ENUM,
    get_float,
    get_formid,
    get_int,
    get_str,
    music_for_enum,
    note_emitted_region,
    pack_formid_subrecord,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint8_subrecord,
    world_music_enum,
)


#: REGN data-entry type enum, shared verbatim by TES4 and TES5 (xEdit wbDefinitions*): 3 Weather, 7 Sound.
_REGN_DATA_WEATHER = 3
_REGN_DATA_SOUND = 7


#: DNAM bytes 56-227 as (offset, value): properties TES4 lacks, taking Skyrim's DefaultWater values.
_WATR_DEFAULT_SHADER_FIELDS = (
    (100, 270.0),
    (104, 210.0), (108, 225.0), (112, 0.019),
    (116, 0.013), (120, 0.096), (124, 6200.0),
    (128, 0.2),
    (132, 0.93), (136, 900.0),
    (140, 0.9), (144, -500.0), (148, 1600.0),
    (152, 9.0),
    (156, 500.0), (160, 0.0), (164, 10000.0), (168, 10.0),
    (172, 1920.0), (176, 6703.0), (180, 488.0),
    (184, 0.6957), (188, 0.6304), (192, 0.4746),
    (196, 0.34),
    (200, 1.7), (204, 3.2),
    (208, 0.9), (212, 0.5), (216, 0.1), (220, 0.2),
    (224, 2200.0),
)


def _regn_music_type(rec: dict, i: int):
    """The RDMD music enum for data entry `i`, or None when it must not win.

    A DEFAULT-valued RDMD yields to an authored worldspace SNAM: 126 of
    Oblivion's 127 values are 0 (Explore) and only WaitingRoomRegion authors
    anything else, while 21 city regions say 0 against a worldspace SNAM of 1
    (Public).  Honouring the region there pins the whole city to Explore.
    """
    mus = get_int(rec, f'RegionData[{i}].MusicType', None)
    if (mus == TES4_DEFAULT_MUSIC_ENUM
            and world_music_enum(get_formid(rec, 'WNAM.Worldspace'))
            not in (None, TES4_DEFAULT_MUSIC_ENUM)):
        return None
    return mus


def _regn_weather_list(rec: dict, i: int) -> bytes:
    """RDWT entries for data entry `i`, widened 8 -> 12 bytes by a null Global."""
    wlist = b''
    for j in range(get_int(rec, f'RegionData[{i}].WeatherCount')):
        wfid = get_formid(rec, f'RegionData[{i}].Weather[{j}].FormID')
        if not wfid:
            continue
        chance = get_int(rec, f'RegionData[{i}].Weather[{j}].Chance')
        wlist += struct.pack('<III', wfid, chance, 0)
    return wlist


def _regn_data_entries(rec: dict) -> tuple:
    """(weather, music) entry lists, each (override, priority, payload)."""
    weather_entries = []
    music_entries = []
    for i in range(get_int(rec, 'RegionDataCount')):
        override = get_int(rec, f'RegionData[{i}].Override')
        priority = get_int(rec, f'RegionData[{i}].Priority')
        mus = _regn_music_type(rec, i)
        if mus is not None:
            music_entries.append((override, priority, mus))
        if get_int(rec, f'RegionData[{i}].Type') != _REGN_DATA_WEATHER:
            continue
        wlist = _regn_weather_list(rec, i)
        if wlist:
            weather_entries.append((override, priority, wlist))
    return weather_entries, music_entries


def _regn_areas(rec: dict) -> bytes:
    """Packed RPLI/RPLD area polygons; the engine applies data only inside these."""
    areas = b''
    for i in range(get_int(rec, 'AreaCount')):
        points = get_hex_bytes(rec, f'Area[{i}].PointsHex')
        if not points:
            continue
        areas += pack_subrecord(
            'RPLI', struct.pack('<I', get_int(rec, f'Area[{i}].EdgeFalloff')))
        areas += pack_subrecord('RPLD', points)
    return areas


def convert_REGN(rec: dict):
    """REGN — Region, converted for its WEATHER and MUSIC entries only.

    Returns packed bytes, or None when the region has no weather list, no
    music type, or no area polygon to apply them in.  Object/grass/sound/map
    data types drive TES4-side generators with no equivalent here.

    TES5 subrecord order (wbDefinitionsTES5): EDID, RCLR, WNAM,
    [RPLI, RPLD]*, [RDAT, RDWT|RDMO]*.

    See: docs/commentary/tes5_import_weather.md#regn-weather-where-cyrodiils-weather
    See: docs/commentary/asset_convert_audio.md#exterior-music-comes-from-regnrdmo
    """
    weather_entries, music_entries = _regn_data_entries(rec)
    areas = _regn_areas(rec)

    if not areas or (not weather_entries and not music_entries):
        return None

    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    subs += pack_subrecord('RCLR', struct.pack(
        '<BBBB', get_int(rec, 'RCLR.R'), get_int(rec, 'RCLR.G'),
        get_int(rec, 'RCLR.B'), 0))

    wnam = get_formid(rec, 'WNAM.Worldspace')
    if wnam:
        subs += pack_formid_subrecord('WNAM', wnam)

    subs += areas

    for override, priority, mus in music_entries:
        musc = music_for_enum(mus)
        if not musc:
            continue
        subs += pack_subrecord('RDAT', struct.pack(
            '<IBBxx', _REGN_DATA_SOUND, 1 if override else 0,
            min(255, priority)))
        subs += pack_formid_subrecord('RDMO', musc)

    for override, priority, wlist in weather_entries:
        subs += pack_subrecord('RDAT', struct.pack(
            '<IBBxx', _REGN_DATA_WEATHER, 1 if override else 0,
            min(255, priority)))
        subs += pack_subrecord('RDWT', wlist)

    fid = get_formid(rec, 'FormID')
    note_emitted_region(fid)
    return pack_record('REGN', fid, get_int(rec, 'RecordFlags'), subs)


def convert_LSCR(rec: dict) -> bytes:
    """LSCR — Loading Screen. No OBND per xEdit.

    TES5 order: EDID ICON DESC CTDA NNAM SNAM RNAM ONAM XNAM MOD2.  NNAM is a
    required FormID -> STAT (the 3D model TES5 loading screens use); TES4 has
    no model reference, so NULL is written.  ICON is omitted for the same
    reason -- it is the 2D path TES5 no longer uses.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    desc = get_str(rec, 'DESC')
    if desc:
        subs += pack_string_subrecord('DESC', desc)
    subs += pack_formid_subrecord('NNAM', 0)
    return pack_record('LSCR', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def _watr_dnam(rec: dict) -> bytes:
    """The 228-byte TES5 water-visuals struct for `rec`.

    0-15    wind/wave constants, mirrored from vanilla (TES5 marks unused)
    16-27   surface response; Sun Power renormalized TES4 0-50 -> TES5 ~1000
    32-39   above-water fog distance, straight across from TES4
    40-52   color block (RGB only; vanilla alpha is always 0)
    56-227  noise/fog-under/specular/depth, from Skyrim's DefaultWater

    See: docs/commentary/tes5_import_landscape.md#watr-dnam-offset-trap
    """
    dnam = bytearray(228)

    def _f(off, value):
        """Write `value` as a little-endian float at byte `off`."""
        struct.pack_into('<f', dnam, off, value)

    _f(0, 0.1)
    _f(4, 90.0)
    _f(8, 0.5)
    _f(12, 1.0)

    sun_power = get_float(rec, 'DATA.SunPower', 15.0)
    _f(16, max(0.0, min(4000.0, sun_power * (1021.0 / 15.0))))
    _f(20, get_float(rec, 'DATA.ReflectivityAmount', 1.0))
    _f(24, get_float(rec, 'DATA.FresnelAmount', 0.05))
    _f(28, 0.0)
    _f(32, get_float(rec, 'DATA.FogNear', 0.0))
    _f(36, get_float(rec, 'DATA.FogFar', 110.0))

    for off, key, fallback in ((40, 'ShallowColor', (37, 52, 37)),
                               (44, 'DeepColor', (5, 16, 5)),
                               (48, 'ReflectionColor', (103, 122, 117))):
        for i, chan in enumerate(('R', 'G', 'B')):
            dnam[off + i] = max(0, min(255, get_int(
                rec, f'DATA.{key}{chan}', fallback[i])))
    dnam[52] = max(0, min(255, get_int(rec, 'DATA.TextureBlend', 50)))

    for off, value in _WATR_DEFAULT_SHADER_FIELDS:
        _f(off, value)
    return bytes(dnam)


#: DefaultWater's id in Skyrim.esm and, raw, in every TES4-family master that authors one.
DEFAULT_WATER = 0x00000018

#: The water a worldspace naming none uses; record_default_water points it at a still one.
_FALLBACK_WATER = [DEFAULT_WATER]


def default_water() -> int:
    """The water a worldspace that names none is written with."""
    return _FALLBACK_WATER[0]


def still_default_water(skyrim_esm: str, formid: int) -> bytes:
    """Skyrim's DefaultWater at `formid` with its current zeroed; b'' when unreadable."""
    try:
        with open(skyrim_esm, 'rb') as handle:
            data = handle.read()
    except OSError:
        return b''
    for rec in records(data, b'WATR'):
        if rec.form_id != DEFAULT_WATER:
            continue
        subs = pack_string_subrecord('EDID', 'TES4DefaultWaterStill')
        for tag, payload in rec.subs():
            if tag in (b'NAM0', b'NAM1'):
                payload = b'\x00' * len(payload)
            if tag != b'EDID':
                subs += pack_subrecord(tag.decode('ascii'), payload)
        return pack_record('WATR', formid, 0, subs)
    return b''


def record_default_water(by_type: dict, master_export, writer, skyrim_data: str) -> None:
    """Point every worldspace naming no water at the source's own DefaultWater, else a still copy.

    TES4 masters author DefaultWater at a fixed id, which converts with no current. A source with
    none, Morrowind's, gets Skyrim's copied once with its current zeroed.
    See: docs/commentary/tes5_import_landscape.md#still-default-water
    """
    _FALLBACK_WATER[0] = DEFAULT_WATER
    waters = (*values_of(master_export, 'WATR'), *by_type.get('WATR', []))
    if any(int(rec.get('FormID') or '0', 16) == DEFAULT_WATER for rec in waters):
        _FALLBACK_WATER[0] = remap_formid(DEFAULT_WATER)
        return
    if all(get_formid(w, 'NAM2.Water') for w in by_type.get('WRLD', [])):
        return
    fid = writer.derive_formid('WATR_STILL', 'DefaultWater')
    still = still_default_water(os.path.join(skyrim_data or '', 'Skyrim.esm'), fid)
    if still:
        writer.add_record('WATR', still)
        _FALLBACK_WATER[0] = fid


def convert_WATR(rec: dict) -> bytes:
    """WATR — Water Type conversion.

    TES5 order: EDID FULL NNAM*3 ANAM FNAM MNAM TNAM SNAM XNAM INAM DATA DNAM
    GNAM NAM0 NAM1.  DATA is Damage-per-second, honoured only behind FNAM bit
    0 the way TES4 gates it.  NNAM is always DefaultWater.dds; only FNAM bit 0
    carries; MNAM/TNAM are never emitted; GNAM/NAM0/NAM1 are required-but-zero.

    See: docs/commentary/tes5_import_landscape.md#watr-fields-not-carried
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    for _ in range(3):
        subs += pack_string_subrecord('NNAM', r'Data\Textures\Water\DefaultWater.dds')

    opacity = get_int(rec, 'ANAM.Opacity', 128)
    subs += pack_uint8_subrecord('ANAM', max(0, min(255, opacity)))

    flags = get_int(rec, 'FNAM.Flags') & 0x01
    subs += pack_uint8_subrecord('FNAM', flags)

    sound_fid = get_formid(rec, 'SNAM.Sound')
    if sound_fid:
        subs += pack_formid_subrecord('SNAM', sound_fid)

    damage = get_int(rec, 'DATA.Damage', 0) if (flags & 0x01) else 0
    subs += pack_subrecord('DATA', struct.pack('<H', max(0, min(0xFFFF, damage))))

    subs += pack_subrecord('DNAM', _watr_dnam(rec))

    subs += pack_subrecord('GNAM', b'\x00' * 12)

    subs += pack_subrecord('NAM0', b'\x00' * 12)
    subs += pack_subrecord('NAM1', b'\x00' * 12)

    return pack_record('WATR', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)

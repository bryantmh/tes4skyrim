"""
Morrowind base-object exporters: items, statics, lights, sounds, globals.

Each emits the TES4 KEY=VALUE vocabulary the tes5_import converters read, so
no new import code is needed; Morrowind's own enums are translated to TES4's
where the two differ. Every exporter takes the record and the conversion
context, which resolves string IDs to FormIDs.

See: docs/commentary/tes4_export_morrowind.md#tes4-vocabulary
"""

import math
import struct

from ..morrowind_armor import emit_worn_models
from ..record_types.common import escape_value
from ..record_types.morrowind_materials import material_of, matt_formid
from ..tes3_reader import Tes3Record, get_string, get_subrecord

#: Source extensions that always ship as DDS, whatever a record calls them.
_IMAGE_EXTS = ('tga', 'bmp')

#: Morrowind record types whose TES4 signature differs.
_SIGNATURES = {'LEVI': 'LVLI', 'LEVC': 'LVLC', 'REPA': 'MISC', 'PROB': 'MISC',
               'LOCK': 'MISC'}

#: WPDT type -> TES4 DATA.Type: 0 blade 1H, 1 blade 2H, 2 blunt 1H, 3 blunt 2H, 5 bow.
_WEAPON_TYPES = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 3, 6: 1, 7: 2, 8: 3, 9: 5,
                 10: 5, 11: 0}

#: WPDT types that are ammunition (arrow, bolt) rather than weapons.
_AMMO_TYPES = (12, 13)

#: AODT armor type -> TES4 BMDT biped bits. See: docs/commentary/tes4_export_morrowind.md#equipment-slots
_ARMOR_SLOTS = {0: 0x3, 1: 0x4, 2: 0, 3: 0, 4: 0x8, 5: 0x20, 6: 0x10, 7: 0x10,
                8: 0x2000, 9: 0x10, 10: 0x10}

#: AODT armor type -> the GMST weight Morrowind classes the piece against.
_ARMOR_CLASS_WEIGHT = {0: 5.0, 1: 30.0, 2: 10.0, 3: 10.0, 4: 15.0, 5: 20.0,
                       6: 5.0, 7: 5.0, 8: 15.0, 9: 5.0, 10: 5.0}

#: fLightMaxMod: armor at most this fraction of its class weight is light.
_LIGHT_MAX_MOD = 0.6

#: TES4 BMDT.GeneralFlags heavy-armor bit.
_HEAVY_ARMOR = 0x80

#: CTDT clothing type -> TES4 BMDT biped bits.
_CLOTHING_SLOTS = {0: 0x8, 1: 0x20, 2: 0x4, 3: 0, 4: 0xC, 5: 0x10, 6: 0x10,
                   7: 0x8, 8: 0x40, 9: 0x100}

#: Morrowind skill index -> TES4 skill index; shared with the actor exporters.
MW_SKILL_TO_TES4 = {0: 3, 1: 0, 2: 6, 3: 6, 4: 4, 5: 2, 6: 4, 7: 4, 8: 1, 9: 12,
                    10: 10, 11: 8, 12: 11, 13: 9, 14: 12, 15: 13, 16: 7, 17: 15,
                    18: 18, 19: 19, 20: 14, 21: 15, 22: 2, 23: 16, 24: 17,
                    25: 20, 26: 5}

#: MCDT flag marking a key, which TES4 stores as its own KEYM type.
_MISC_KEY = 0x1

#: ID prefix marking a soul gem, the same test OpenMW's `isSoulGem` makes.
_SOULGEM_PREFIX = 'misc_soulgem'

#: Soul gem ID suffix -> TES5 SLCP capacity (1 petty .. 5 grand).
_SOULGEM_CAPACITY = {'petty': 1, 'lesser': 2, 'common': 3, 'greater': 4,
                     'grand': 5, 'azura': 5}

#: CONT FLAG respawn bit; TES4 DATA.Flags puts it at 0x01.
_CONT_RESPAWN = 0x2

#: ALDT autocalc bit; TES4 ENIT 0x01 means "no auto-calc", the inverse.
_ALCH_AUTOCALC = 0x1

#: Sound range multipliers and defaults (fAudio* GMSTs), in game units.
_SOUND_MIN_MULT = 20.0
_SOUND_MAX_MULT = 50.0
_SOUND_DEFAULT_MIN = 5
_SOUND_DEFAULT_MAX = 40

#: TES4 SNDX stores the minimum range in units of 5 and the maximum in 100.
_TES4_MIN_UNIT = 5.0
_TES4_MAX_UNIT = 100.0
_FULL_VOLUME = 255

#: Skyrim's LIGH defaults for fields Morrowind does not author.
_LIGHT_FALLOFF = 1.0
_LIGHT_FOV = 90.0
_LIGHT_FADE = 1.0

#: WPDT stores enchant capacity multiplied by ten.
_ENCHANT_POINT_SCALE = 10

#: Armor rating is stored in hundredths in TES4 and Skyrim alike.
_RATING_SCALE = 100


def as_dds(path: str) -> str:
    """A texture path with a .tga or .bmp extension changed to .dds.

    Morrowind names textures .tga but its archives ship .dds, substituting at
    load; without this every icon path names a file that does not exist.
    """
    stem, dot, ext = path.rpartition('.')
    if dot and ext.lower() in _IMAGE_EXTS:
        return stem + '.dds'
    return path


def unpack(rec: Tes3Record, sig: str, fmt: str):
    """The fields of one fixed-layout subrecord, or None when absent/short."""
    sub = get_subrecord(rec, sig)
    if sub is None or len(sub.data) < struct.calcsize(fmt):
        return None
    return struct.unpack_from(fmt, sub.data, 0)


def emit_str(lines: list, key: str, rec: Tes3Record, sig: str) -> None:
    """Emit a string subrecord under the TES4 key name."""
    sub = get_subrecord(rec, sig)
    if sub:
        text = get_string(sub)
        if text:
            lines.append(f'{key}={escape_value(text)}')


def emit_model(lines: list, rec: Tes3Record, key: str = 'Model.MODL') -> None:
    """Emit the mesh path, normalized the way the asset stages expect."""
    sub = get_subrecord(rec, 'MODL')
    if sub:
        path = get_string(sub).replace('/', chr(92))
        if path:
            lines.append(f'{key}={escape_value(path)}')


def emit_common(lines: list, rec: Tes3Record) -> None:
    """EditorID, display name and mesh -- shared by most types."""
    lines.append(f'EditorID={escape_value(rec.record_id)}')
    emit_str(lines, 'FULL', rec, 'FNAM')
    emit_model(lines, rec)


def emit_icon(lines: list, rec: Tes3Record, sig: str = 'ITEX',
              key: str = 'ICON', prefix: str = '') -> None:
    """Emit the inventory icon path, renamed to the file that ships."""
    sub = get_subrecord(rec, sig)
    if sub is None:
        return
    path = get_string(sub).replace('/', chr(92))
    if path:
        lines.append(f'{key}={escape_value(prefix + as_dds(path))}')


def emit_ref(lines: list, key: str, rec: Tes3Record, sig: str, ctx,
             target: str = '') -> None:
    """Emit the FormID of the record a string subrecord names, if it converts.

    `target` is the TES4 type the subrecord names, which disambiguates an ID
    that two Morrowind record types share.
    See: docs/commentary/tes4_export_morrowind.md#per-type-id-namespaces
    """
    sub = get_subrecord(rec, sig)
    form_id = ctx.resolve(get_string(sub), target) if sub else ''
    if form_id:
        lines.append(f'{key}={form_id}')


def emit_inventory(lines: list, rec: Tes3Record, ctx) -> None:
    """Emit NPCO entries -- a 4-byte count then a fixed 32-byte item ID.

    An item this pass does not convert is left out rather than written as a
    null FormID.
    """
    items = []
    for sub in rec.subrecords:
        if sub.type != 'NPCO' or len(sub.data) < 36:
            continue
        count = struct.unpack_from('<i', sub.data, 0)[0]
        item = sub.data[4:36].split(b'\x00', 1)[0].decode('cp1252', 'replace')
        form_id = ctx.resolve(item)
        if form_id:
            items.append((form_id, count))
    lines.append(f'ItemCount={len(items)}')
    for index, (form_id, count) in enumerate(items):
        lines.append(f'Item[{index}].FormID={form_id}')
        lines.append(f'Item[{index}].Count={count}')


def _weapon_type(rec: Tes3Record) -> int:
    """The WPDT weapon type, or -1 without one."""
    data = unpack(rec, 'WPDT', '<fih')
    return data[2] if data else -1


def _misc_flags(rec: Tes3Record) -> int:
    """The MCDT flags, or 0 without them."""
    data = unpack(rec, 'MCDT', '<fii')
    return data[2] if data else 0


def _soulgem_capacity(rec: Tes3Record) -> int:
    """This MISC record's soul capacity, or 0 when it is not a soul gem.

    TES3 has no soul-gem flag: the engine tests the ID prefix, and so does
    OpenMW (`mwclass/misc.cpp` `isSoulGem`). An unrecognised tier holds a
    grand soul, which is what Azura's Star does.
    See: docs/commentary/tes4_export_morrowind.md#tes4-vocabulary
    """
    name = (rec.record_id or '').lower()
    if not name.startswith(_SOULGEM_PREFIX):
        return 0
    tier = name[len(_SOULGEM_PREFIX):].strip('_')
    return _SOULGEM_CAPACITY.get(tier, _SOULGEM_CAPACITY['grand'])


def filled_soulgem_id(gem_id: str, soul: int) -> str:
    """The EditorID of one gem's FILLED variant, keyed on the gem's own id.
    See: docs/commentary/tes4_export_morrowind.md#filled-soul-gems
    """
    return f'{gem_id}_Filled{soul}'


def filled_soulgems(records) -> list:
    """`(gem id, soul, lines)` per filled variant the chain's gems can hold.

    🛑 TES3 keeps a captured soul on the INVENTORY STACK and authors no filled
    record; Skyrim makes one a separate base record whose `SOUL` is the
    trapped size, so `AddSoulGem` has nothing to add until these exist.
    See: docs/commentary/tes4_export_morrowind.md#filled-soul-gems
    """
    out = []
    for rec in records:
        capacity = _soulgem_capacity(rec) if rec.type == 'MISC' else 0
        if not capacity or rec.deleted:
            continue
        for soul in range(1, capacity + 1):
            edid = filled_soulgem_id(rec.record_id, soul)
            lines = [f'EditorID={escape_value(edid)}' if
                     line.startswith('EditorID=') else line
                     for line in export_MISC(rec, None)
                     if not line.startswith('SOUL=')]
            lines.append(f'SOUL={soul}')
            out.append((rec.record_id, soul, lines))
    return out


def tes4_signature(rec: Tes3Record) -> str:
    """The TES4 record type this Morrowind record is exported as.

    See: docs/commentary/tes4_export_morrowind.md#tes4-vocabulary
    """
    if rec.type == 'MISC' and _soulgem_capacity(rec):
        return 'SLGM'
    if rec.type == 'MISC' and _misc_flags(rec) & _MISC_KEY:
        return 'KEYM'
    if rec.type == 'WEAP' and _weapon_type(rec) in _AMMO_TYPES:
        return 'AMMO'
    return _SIGNATURES.get(rec.type, rec.type)


def export_STAT(rec: Tes3Record, ctx) -> list:
    """A static: a mesh and nothing else."""
    lines = [f'EditorID={escape_value(rec.record_id)}']
    emit_model(lines, rec)
    return lines


def export_ACTI(rec: Tes3Record, ctx) -> list:
    """An activator."""
    lines = []
    emit_common(lines, rec)
    return lines


def export_DOOR(rec: Tes3Record, ctx) -> list:
    """A door, with its open and close sounds and the flags TES5 requires.

    TES3 has no door-flags field at all -- its `FNAM` is the display name,
    which `emit_common` already writes as FULL -- but TES5's is required and
    every vanilla door carries one, 185 of 235 of them zero.
    See: docs/commentary/tes4_export_morrowind.md#teleport-doors
    """
    lines = ['FNAM.Flags=0']
    emit_common(lines, rec)
    emit_ref(lines, 'SNAM.Open', rec, 'SNAM', ctx, 'SOUN')
    emit_ref(lines, 'ANAM.Close', rec, 'ANAM', ctx, 'SOUN')
    return lines


def export_CONT(rec: Tes3Record, ctx) -> list:
    """A container: respawn flag, capacity and its inventory list."""
    lines = []
    emit_common(lines, rec)
    weight = unpack(rec, 'CNDT', '<f')
    flags = unpack(rec, 'FLAG', '<I')
    respawns = bool(flags and flags[0] & _CONT_RESPAWN)
    lines.append(f'DATA.Flags={int(respawns)}')
    lines.append(f'DATA.Weight={weight[0] if weight else 0.0}')
    emit_inventory(lines, rec, ctx)
    return lines


def export_LIGH(rec: Tes3Record, ctx) -> list:
    """A light: LHDT carries weight, value, time, radius, color and flags.

    The flag bits agree with TES4's; Morrowind's 0x10 (Fire) is the bit the
    importer masks as unused.
    """
    lines = []
    emit_common(lines, rec)
    emit_icon(lines, rec)
    data = unpack(rec, 'LHDT', '<fiiiIi')
    if data:
        weight, value, time, radius, color, flags = data
        lines.extend([f'DATA.Time={time}', f'DATA.Radius={radius}',
                      f'DATA.Color.R={color & 0xFF}',
                      f'DATA.Color.G={(color >> 8) & 0xFF}',
                      f'DATA.Color.B={(color >> 16) & 0xFF}',
                      f'DATA.Flags={flags}',
                      f'DATA.FalloffExponent={_LIGHT_FALLOFF}',
                      f'DATA.FOV={_LIGHT_FOV}', f'DATA.Value={value}',
                      f'DATA.Weight={weight}', f'FNAM.Fade={_LIGHT_FADE}'])
    emit_ref(lines, 'SNAM.Sound', rec, 'SNAM', ctx, 'SOUN')
    return lines


def emit_value_weight(lines: list, value: int, weight: float) -> None:
    """The DATA.Value / DATA.Weight pair every carriable item has."""
    lines.append(f'DATA.Value={value}')
    lines.append(f'DATA.Weight={weight}')


def export_MISC(rec: Tes3Record, ctx) -> list:
    """Miscellaneous clutter, a key when MCDT says so, or a soul gem.

    A soul gem ships EMPTY: TES3 stores a captured soul on the inventory
    stack, not on the base record, so there is no filled variant to carry.
    """
    lines = []
    emit_common(lines, rec)
    emit_icon(lines, rec)
    data = unpack(rec, 'MCDT', '<fii')
    if data:
        emit_value_weight(lines, data[1], data[0])
    capacity = _soulgem_capacity(rec)
    if capacity:
        lines.append(f'SLCP.Capacity={capacity}')
        lines.append('SOUL=0')
    return lines


def export_WEAP(rec: Tes3Record, ctx) -> list:
    """A weapon, or ammunition when the WPDT type is arrow or bolt.

    Damage is the largest of the three attack maxima; TES4 has one number.
    See: docs/commentary/tes4_export_morrowind.md#tes4-vocabulary
    """
    lines = []
    emit_common(lines, rec)
    emit_icon(lines, rec)
    data = unpack(rec, 'WPDT', '<fihHffH6Bi')
    if data is None:
        return lines
    weight, value, wtype, health, speed, reach = data[:6]
    damage = max(data[8], data[10], data[12])
    if wtype in _AMMO_TYPES:
        lines.extend([f'DATA.Speed={speed}', 'DATA.Flags=0'])
    else:
        lines.extend([f'DATA.Type={_WEAPON_TYPES.get(wtype, 0)}',
                      f'DATA.Speed={speed}', f'DATA.Reach={reach}',
                      'DATA.Flags=0', f'MorrowindWeaponType={wtype}'])
    lines.append(f'DATA.Value={value}')
    if wtype not in _AMMO_TYPES:
        lines.append(f'DATA.Health={health}')
    lines.append(f'DATA.Weight={weight}')
    lines.append(f'DATA.Damage={damage}')
    lines.append(f'ANAM={data[6] // _ENCHANT_POINT_SCALE}')
    emit_ref(lines, 'EITM', rec, 'ENAM', ctx, 'ENCH')
    return lines


def _emit_wearable(lines: list, rec: Tes3Record, biped: int,
                   general: int, ctx) -> None:
    """The TES4 wearable header: models, icon and biped flags.

    See: docs/commentary/tes4_export_morrowind.md#worn-models
    """
    lines.append(f'EditorID={escape_value(rec.record_id)}')
    emit_str(lines, 'FULL', rec, 'FNAM')
    lines.append(f'BMDT.GeneralFlags={general}')
    emit_model(lines, rec, 'Male.WorldModel.MODL')
    emit_worn_models(lines, rec, biped, ctx)
    emit_icon(lines, rec, key='Male.Icon')
    emit_ref(lines, 'EITM', rec, 'ENAM', ctx, 'ENCH')


def export_ARMO(rec: Tes3Record, ctx) -> list:
    """Armour: slot from the AODT type, weight class from weight."""
    lines = []
    data = unpack(rec, 'AODT', '<ifiiii')
    if data is None:
        _emit_wearable(lines, rec, 0, 0, ctx)
        return lines
    atype, weight, value, health, enchant_points, armor = data
    heavy = weight > _ARMOR_CLASS_WEIGHT.get(atype, 0.0) * _LIGHT_MAX_MOD
    _emit_wearable(lines, rec, _ARMOR_SLOTS.get(atype, 0),
                   _HEAVY_ARMOR if heavy else 0, ctx)
    lines.append(f'MorrowindWearableType={atype}')
    lines.append(f'DATA.ArmorRating={armor * _RATING_SCALE}')
    lines.append(f'ANAM={enchant_points}')
    lines.append(f'DATA.Value={value}')
    lines.append(f'DATA.Health={health}')
    lines.append(f'DATA.Weight={weight}')
    return lines


def export_CLOT(rec: Tes3Record, ctx) -> list:
    """Clothing: slot from the CTDT type; value and enchant points are u16."""
    lines = []
    data = unpack(rec, 'CTDT', '<ifHH')
    if data is None:
        _emit_wearable(lines, rec, 0, 0, ctx)
        return lines
    ctype, weight, value, enchant_points = data
    _emit_wearable(lines, rec, _CLOTHING_SLOTS.get(ctype, 0), 0, ctx)
    lines.append(f'MorrowindWearableType={ctype}')
    emit_value_weight(lines, value, weight)
    lines.append(f'ANAM={enchant_points}')
    return lines


def export_LTEX(rec: Tes3Record, ctx) -> list:
    """A landscape texture and the index LAND's VTEX refers to it by.

    The ICON carries the full `textures\\` path: Morrowind stores a bare file
    name and ships it flat, where Oblivion's is relative to a Landscape
    subfolder the import stage prepends.

    `HNAM.Material` is inferred, because TES3 LTEX has no material field; the
    authored EditorID ("Road Dirt") beats the file name, which is the tiebreak
    for the records whose EditorID is itself a filename.
    See: docs/commentary/tes4_export_morrowind.md#land-terrain
    """
    lines = [f'EditorID={escape_value(rec.record_id)}']
    emit_icon(lines, rec, 'DATA', prefix='textures' + chr(92))
    texture = get_subrecord(rec, 'DATA')
    material = material_of(
        rec.record_id, get_string(texture) if texture is not None else '')
    matt = matt_formid(material)
    lines.append(f'HNAM.Material={max(material, 0)}')
    if matt:
        lines.append(f'HNAM.MaterialFormID={matt:08X}')
    intv = unpack(rec, 'INTV', '<I')
    if intv:
        lines.append(f'TextureIndex={intv[0]}')
    return lines


def export_BOOK(rec: Tes3Record, ctx) -> list:
    """A book or scroll; the skill it teaches is mapped onto TES4's enum."""
    lines = []
    emit_common(lines, rec)
    emit_icon(lines, rec)
    emit_str(lines, 'DESC', rec, 'TEXT')
    data = unpack(rec, 'BKDT', '<fiiii')
    if data:
        weight, value, scroll, skill, enchant_points = data
        lines.append(f'DATA.Flags={int(bool(scroll))}')
        lines.append(f'DATA.Teaches={MW_SKILL_TO_TES4.get(skill, -1)}')
        emit_value_weight(lines, value, weight)
        lines.append(f'ANAM={enchant_points}')
    emit_ref(lines, 'EITM', rec, 'ENAM', ctx, 'ENCH')
    return lines


def export_APPA(rec: Tes3Record, ctx) -> list:
    """An alchemy apparatus: type, quality, weight and value."""
    lines = []
    emit_common(lines, rec)
    emit_icon(lines, rec)
    data = unpack(rec, 'AADT', '<iffi')
    if data:
        atype, quality, weight, value = data
        lines.append(f'DATA.Type={atype}')
        lines.append(f'DATA.Quality={quality}')
        emit_value_weight(lines, value, weight)
    return lines


def export_TOOL(rec: Tes3Record, ctx) -> list:
    """A repair hammer, probe or lockpick, exported as MISC clutter.

    Skyrim has no repair or probe items and the importer has no dispatch for
    these signatures; every one leads with weight then value.
    """
    lines = []
    emit_common(lines, rec)
    emit_icon(lines, rec)
    sig = {'REPA': 'RIDT', 'PROB': 'PBDT', 'LOCK': 'LKDT'}[rec.type]
    data = unpack(rec, sig, '<fi')
    if data:
        emit_value_weight(lines, data[1], data[0])
    return lines


def _attenuation(volume: int) -> int:
    """TES4 static attenuation (hundredths of a dB) for a 0-255 volume."""
    if volume >= _FULL_VOLUME:
        return 0
    return min(65535, round(-2000.0 * math.log10(max(volume, 1) / _FULL_VOLUME)))


def export_SOUN(rec: Tes3Record, ctx) -> list:
    """A sound: file relative to Sound\\, plus volume and 3D ranges.

    See: docs/commentary/tes4_export_morrowind.md#sounds
    """
    lines = [f'EditorID={escape_value(rec.record_id)}']
    sub = get_subrecord(rec, 'FNAM')
    if sub:
        path = get_string(sub).replace('/', chr(92))
        lines.append(f'FNAM.Filename={escape_value(path)}')
    data = unpack(rec, 'DATA', '<BBB')
    if data is None:
        return lines
    volume, min_range, max_range = data
    if not min_range and not max_range:
        min_range, max_range = _SOUND_DEFAULT_MIN, _SOUND_DEFAULT_MAX
    min_att = min(255, round(min_range * _SOUND_MIN_MULT / _TES4_MIN_UNIT))
    max_att = min(255, max(1, round(max_range * _SOUND_MAX_MULT / _TES4_MAX_UNIT)))
    lines.extend([f'SNDX.MinAttDist={min_att}', f'SNDX.MaxAttDist={max_att}',
                  'SNDX.FreqAdj=0', 'SNDX.Flags=0',
                  f'SNDX.StaticAttenuation={_attenuation(volume)}'])
    return lines


def export_GLOB(rec: Tes3Record, ctx) -> list:
    """A global variable: FNAM is the type letter, FLTV always a float."""
    lines = [f'EditorID={escape_value(rec.record_id)}']
    fnam = get_subrecord(rec, 'FNAM')
    if fnam and fnam.data:
        lines.append(f'FNAM.Type={chr(fnam.data[0])}')
    value = unpack(rec, 'FLTV', '<f')
    if value:
        lines.append(f'FLTV.Value={value[0]}')
    return lines


#: TES3 signature -> exporter for every non-actor base record.
MORROWIND_ITEM_EXPORTERS = {
    'STAT': export_STAT,
    'ACTI': export_ACTI,
    'DOOR': export_DOOR,
    'CONT': export_CONT,
    'LIGH': export_LIGH,
    'MISC': export_MISC,
    'WEAP': export_WEAP,
    'ARMO': export_ARMO,
    'LTEX': export_LTEX,
    'BOOK': export_BOOK,
    'CLOT': export_CLOT,
    'APPA': export_APPA,
    'REPA': export_TOOL,
    'PROB': export_TOOL,
    'LOCK': export_TOOL,
    'SOUN': export_SOUN,
    'GLOB': export_GLOB,
}

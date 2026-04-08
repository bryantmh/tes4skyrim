#!/usr/bin/env python3
"""
TES5 (Skyrim SE) ESM/ESP reader and per-type KEY=VALUE exporter for debugging.

Reads a TES5 binary file and produces one .txt per record type in
    references/<basename>/
using the same ---RECORD_BEGIN--- / KEY=VALUE / ---RECORD_END--- format as
the project's tes4_export package.

TES5 vs TES4 header differences:
  Record header:  24 bytes (TES4: 20)  — adds timestamp(2) + form_version(2) at bytes 16-19
  GRUP header:    24 bytes (TES4: 20)  — same extra 4 bytes at the end
  Subrecord header: 6 bytes (unchanged)
  Compressed flag: 0x00040000 (same as TES4)
  Localized flag:  0x00000080 on TES4/file header → FULL/DESC are 4-byte LString indices

Usage:
    python tools/tes5_esm_reader.py
    python tools/tes5_esm_reader.py "C:/path/to/Skyrim.esm"
    python tools/tes5_esm_reader.py "C:/path/to/Skyrim.esm" --outdir references/Skyrim.esm
    python tools/tes5_esm_reader.py "C:/path/to/Skyrim.esm" --types WEAP NPC_ HDPT CLFM
    python tools/tes5_esm_reader.py "C:/path/to/Skyrim.esm" --list-types
"""

import argparse
import mmap
import os
import struct
import sys
import time
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REC_HDR = 24        # record header size in TES5
GRP_HDR = 24        # GRUP header size in TES5
SUB_HDR = 6         # subrecord header size (unchanged from TES4)

FLAG_COMPRESSED = 0x00040000
FLAG_LOCALIZED  = 0x00000080   # file-level flag in the TES4/file header record

DEFAULT_ESM = r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data\Skyrim.esm"

# Subrecords that are always null-terminated strings regardless of record type
_STRING_SUBS = frozenset({
    'EDID', 'MODL', 'MOD2', 'MOD3', 'MOD4', 'MODS', 'MO2S', 'MO3S', 'MO4S',
    'ICON', 'ICO2', 'MICO', 'ANAM', 'BNAM', 'CNAM', 'FNAM', 'GNAM', 'HNAM',
    'KNAM', 'LNAM', 'MNAM', 'NNAM', 'ONAM', 'PPFD', 'SNAM', 'TNAM', 'UNAM',
    'VNAM', 'WNAM', 'XNAM', 'YNAM', 'ZNAM',
})
# Exceptions: these look string-like in the set above but are really FormIDs —
# we resolve them by (rec_type, sub) in the per-type table; the generic decoder
# treats size-4 data as FormID first.

# Subrecords that are localized strings (4-byte index when flag is set)
_LSTRING_SUBS = frozenset({'FULL', 'DESC', 'NNAM', 'SHRT', 'DNAM', 'RNAM'})
# Note: DNAM and RNAM are only LStrings in specific record types (BOOK, RACE etc.);
# per-type override takes precedence.

# Subrecords that are always exactly one FormID (4 bytes)
_FORMID4_SUBS = frozenset({
    'RACE', 'VTCK', 'TPLT', 'RNAM', 'INAM', 'EITM', 'HCLF', 'DOFT', 'SOFT',
    'DLCK', 'DRMO', 'EAMT', 'WNAM', 'ETYP', 'BAMT', 'BIDS', 'BIPL', 'YNAM',
    'PKID', 'COCT',  # COCT is count, not FID — but small enough to be safe
    'LCSR', 'XLKR', 'XCAS', 'XCMO', 'XCIM', 'LTMP', 'XLRM', 'XCCM', 'XNDP',
    'XLOD', 'XLRL', 'XCLR', 'XCLL', 'LNAM', 'XLOC', 'ZCNA', 'XPRD',
    'SCDA', 'SCRV', 'SDSC', 'SNDD', 'SNDC', 'VMAD',
})
# Note: many of these are more complex than 4 bytes in reality, but for display
# purposes we format 4-byte subrecords as FormIDs.

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Sub:
    """A single subrecord."""
    type: str
    data: bytes


@dataclass
class TES5Record:
    """A parsed TES5 record with all subrecords."""
    type:         str
    data_size:    int
    flags:        int
    form_id:      int
    form_version: int       # bytes 20-21 of the 24-byte header
    subrecords:   list = field(default_factory=list)
    # Hierarchy tracking
    parent_wrld:  int = 0
    parent_cell:  int = 0
    parent_dial:  int = 0


# ---------------------------------------------------------------------------
# Low-level parsing
# ---------------------------------------------------------------------------

def _parse_subrecords(data: bytes) -> list:
    """Parse 6-byte-header subrecords from raw record data."""
    subs = []
    pos = 0
    n = len(data)
    while pos + SUB_HDR <= n:
        tag  = data[pos:pos + 4].decode('ascii', errors='replace')
        size = struct.unpack_from('<H', data, pos + 4)[0]
        pos += SUB_HDR
        if pos + size > n:
            break
        subs.append(Sub(type=tag, data=data[pos:pos + size]))
        pos += size
    return subs


def _read_record(mm, pos: int, file_size: int):
    """Read one TES5 record (24-byte header + subrecords). Returns None on error."""
    if pos + REC_HDR > file_size:
        return None

    sig          = mm[pos:pos + 4].decode('ascii', errors='replace')
    data_size    = struct.unpack_from('<I', mm, pos + 4)[0]
    flags        = struct.unpack_from('<I', mm, pos + 8)[0]
    form_id      = struct.unpack_from('<I', mm, pos + 12)[0]
    form_version = struct.unpack_from('<H', mm, pos + 20)[0]

    rec = TES5Record(type=sig, data_size=data_size, flags=flags,
                     form_id=form_id, form_version=form_version)

    data_start = pos + REC_HDR
    data_end   = data_start + data_size
    if data_end > file_size:
        return rec

    raw = bytes(mm[data_start:data_end])

    if flags & FLAG_COMPRESSED and len(raw) >= 4:
        try:
            raw = zlib.decompress(raw[4:])
        except zlib.error:
            return rec

    rec.subrecords = _parse_subrecords(raw)
    return rec


def _parse_group(mm, start: int, end: int, file_size: int, records: list,
                 parent_wrld: int, parent_cell: int, parent_dial: int):
    """Recursively parse records within a GRUP block."""
    pos        = start + GRP_HDR
    group_type = struct.unpack_from('<I', mm, start + 12)[0]
    label      = mm[start + 8:start + 12]

    # Propagate hierarchy from group label
    if group_type == 1:                       # World children
        parent_wrld = struct.unpack_from('<I', label, 0)[0]
    elif group_type in (6, 8, 9, 10):         # Cell children / persistent / temporary / VWD
        parent_cell = struct.unpack_from('<I', label, 0)[0]
    elif group_type == 7:                     # Topic children
        parent_dial = struct.unpack_from('<I', label, 0)[0]

    while pos < end and pos < file_size:
        if pos + 4 > file_size:
            break

        sig = mm[pos:pos + 4]

        if sig == b'GRUP':
            if pos + GRP_HDR > file_size:
                break
            sub_size = struct.unpack_from('<I', mm, pos + 4)[0]
            sub_end  = pos + sub_size
            _parse_group(mm, pos, sub_end, file_size, records,
                         parent_wrld, parent_cell, parent_dial)
            pos = sub_end
        else:
            rec = _read_record(mm, pos, file_size)
            if rec is None:
                break
            rec.parent_wrld = parent_wrld
            rec.parent_cell = parent_cell
            rec.parent_dial = parent_dial

            if rec.type == 'CELL':
                parent_cell = rec.form_id
            elif rec.type == 'WRLD':
                parent_wrld = rec.form_id
            elif rec.type == 'DIAL':
                parent_dial = rec.form_id

            records.append(rec)
            pos += REC_HDR + rec.data_size


def read_tes5_file(filepath: str):
    """
    Read a TES5 ESM/ESP.

    Returns:
        header_rec: TES5Record (the TES4/file header)
        all_records: list[TES5Record]
        is_localized: bool
    """
    with open(filepath, 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            return _parse_file(mm)
        finally:
            mm.close()


def _parse_file(mm):
    file_size = len(mm)
    pos = 0

    # First record is the TES4/file header
    header = _read_record(mm, pos, file_size)
    if header is None:
        raise ValueError('Could not read file header')
    is_localized = bool(header.flags & FLAG_LOCALIZED)
    pos += REC_HDR + header.data_size

    all_records = []
    while pos < file_size:
        if pos + 4 > file_size:
            break
        sig = mm[pos:pos + 4]
        if sig != b'GRUP':
            break
        if pos + GRP_HDR > file_size:
            break
        group_size = struct.unpack_from('<I', mm, pos + 4)[0]
        group_end  = pos + group_size
        _parse_group(mm, pos, group_end, file_size, all_records, 0, 0, 0)
        pos = group_end

    return header, all_records, is_localized


# ---------------------------------------------------------------------------
# Subrecord helpers
# ---------------------------------------------------------------------------

def _get(rec: TES5Record, sig: str):
    """First subrecord matching sig, or None."""
    for s in rec.subrecords:
        if s.type == sig:
            return s
    return None


def _all(rec: TES5Record, sig: str) -> list:
    return [s for s in rec.subrecords if s.type == sig]


def _zstring(data: bytes) -> str:
    return data.rstrip(b'\x00').decode('utf-8', errors='replace')


def _is_zstring(data: bytes) -> bool:
    """Heuristic: data looks like a null-terminated printable string."""
    if not data:
        return False
    # If last byte is \x00 and all prior bytes are printable ASCII or common unicode
    payload = data[:-1] if data[-1:] == b'\x00' else data
    if not payload:
        return False
    try:
        decoded = payload.decode('utf-8')
    except UnicodeDecodeError:
        return False
    # At least 2 chars and mostly printable
    printable = sum(1 for c in decoded if c.isprintable())
    return len(decoded) >= 2 and printable / len(decoded) >= 0.90


def _escape(s: str) -> str:
    return s.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')


def _fid(data: bytes, offset: int = 0) -> str:
    if len(data) >= offset + 4:
        return f"{struct.unpack_from('<I', data, offset)[0]:08X}"
    return '????????'


# ---------------------------------------------------------------------------
# Per-type subrecord decoders
# ---------------------------------------------------------------------------

def _dec_hedr(data: bytes) -> list:
    if len(data) < 12:
        return [f'HEDR.hex={data.hex()}']
    ver   = struct.unpack_from('<f', data, 0)[0]
    count = struct.unpack_from('<I', data, 4)[0]
    nxt   = struct.unpack_from('<I', data, 8)[0]
    return [f'HEDR.Version={ver:.4f}', f'HEDR.RecordCount={count}',
            f'HEDR.NextID={nxt:08X}']


def _dec_obnd(data: bytes) -> list:
    if len(data) < 12:
        return [f'OBND.hex={data.hex()}']
    x1, y1, z1, x2, y2, z2 = struct.unpack_from('<hhhhhh', data)
    return [f'OBND.X1={x1}', f'OBND.Y1={y1}', f'OBND.Z1={z1}',
            f'OBND.X2={x2}', f'OBND.Y2={y2}', f'OBND.Z2={z2}']


def _dec_kwda(data: bytes, ksiz: int) -> list:
    n = ksiz if ksiz else len(data) // 4
    lines = []
    for i in range(min(n, len(data) // 4)):
        fid = struct.unpack_from('<I', data, i * 4)[0]
        lines.append(f'KWDA[{i}]={fid:08X}')
    return lines


def _dec_weap_data(data: bytes) -> list:
    if len(data) < 10:
        return [f'DATA.hex={data.hex()}']
    val    = struct.unpack_from('<I', data, 0)[0]
    weight = struct.unpack_from('<f', data, 4)[0]
    damage = struct.unpack_from('<H', data, 8)[0]
    return [f'DATA.Value={val}', f'DATA.Weight={weight:.4f}', f'DATA.Damage={damage}']


_WEAP_ANIM = {0:'HandToHand',1:'Sword',2:'Dagger',3:'WarAxe',4:'Mace',
              5:'Battleaxe',6:'Bow',7:'Crossbow',8:'Staff',9:'Greatsword',
              10:'TwoHandMelee',11:'Shield'}
_WEAP_STAGGER = {0:'None',1:'Small',2:'Medium',3:'Large',4:'ExtraLarge',5:'Knockdown',6:'Ragdoll'}


def _dec_weap_dnam(data: bytes) -> list:
    """WEAP DNAM — 100 bytes in SSE."""
    if len(data) < 100:
        return [f'DNAM.hex={data.hex()}']
    lines = []
    atype = struct.unpack_from('<I', data, 0)[0]
    lines.append(f'DNAM.AnimationType={atype} ({_WEAP_ANIM.get(atype, "?")})')
    lines.append(f'DNAM.AnimationMult={struct.unpack_from("<f", data, 4)[0]:.4f}')
    lines.append(f'DNAM.Reach={struct.unpack_from("<f", data, 8)[0]:.4f}')
    lines.append(f'DNAM.Flags=0x{struct.unpack_from("<I", data, 12)[0]:08X}')
    raw24 = struct.unpack_from('<I', data, 24)[0]
    lines.append(f'DNAM.SightFOV={(raw24 >> 8) & 0xFF}')
    lines.append(f'DNAM.NumProjectiles={raw24 & 0xFF}')
    raw40 = struct.unpack_from('<I', data, 40)[0]
    lines.append(f'DNAM.Offset40=0x{raw40:08X}  (loU16={raw40&0xFFFF}  hiU16={raw40>>16})')
    lines.append(f'DNAM.Speed={struct.unpack_from("<f", data, 44)[0]:.4f}')
    lines.append(f'DNAM.RumbleLeft={struct.unpack_from("<f", data, 48)[0]:.4f}')
    crit_fid = struct.unpack_from('<I', data, 52)[0]
    lines.append(f'DNAM.CritEffectFID={crit_fid:08X}')
    stagger = struct.unpack_from('<I', data, 76)[0]
    lines.append(f'DNAM.Stagger={stagger} ({_WEAP_STAGGER.get(stagger, "?")})')
    lines.append(f'DNAM.ColorRemapIdx={struct.unpack_from("<f", data, 96)[0]:.4f}')
    return lines


def _dec_weap_crdt(data: bytes) -> list:
    """WEAP CRDT — 24 bytes."""
    if len(data) < 12:
        return [f'CRDT.hex={data.hex()}']
    lines = []
    lines.append(f'CRDT.CritDamage={struct.unpack_from("<H", data, 0)[0]}')
    lines.append(f'CRDT.CritPct={struct.unpack_from("<f", data, 4)[0]:.4f}')
    lines.append(f'CRDT.Flags=0x{data[8]:02X}')
    if len(data) >= 16:
        fid = struct.unpack_from('<I', data, 12)[0]
        lines.append(f'CRDT.SpellFormID={fid:08X}')
    return lines


def _dec_fid4(data: bytes) -> list:
    """Generic 4-byte FormID decoder (used for WEAP-specific subs)."""
    return [f'{struct.unpack_from("<I", data, 0)[0]:08X}'] if len(data) == 4 else [data.hex()]


def _dec_npc_acbs(data: bytes) -> list:
    """NPC_ ACBS — 24 bytes in TES5."""
    if len(data) < 24:
        return [f'ACBS.hex={data.hex()}']
    flags         = struct.unpack_from('<I', data,  0)[0]
    magicka_off   = struct.unpack_from('<h', data,  4)[0]
    stamina_off   = struct.unpack_from('<h', data,  6)[0]
    level         = struct.unpack_from('<h', data,  8)[0]
    calc_min      = struct.unpack_from('<H', data, 10)[0]
    calc_max      = struct.unpack_from('<H', data, 12)[0]
    speed_mult    = struct.unpack_from('<H', data, 14)[0]
    disp_base     = struct.unpack_from('<h', data, 16)[0]
    health_off    = struct.unpack_from('<H', data, 18)[0]
    bleedout_ovr  = struct.unpack_from('<H', data, 20)[0]
    outfit_item   = struct.unpack_from('<H', data, 22)[0]
    return [
        f'ACBS.Flags=0x{flags:08X}',
        f'ACBS.MagickaOffset={magicka_off}',
        f'ACBS.StaminaOffset={stamina_off}',
        f'ACBS.Level={level}',
        f'ACBS.CalcMin={calc_min}',
        f'ACBS.CalcMax={calc_max}',
        f'ACBS.SpeedMult={speed_mult}',
        f'ACBS.DispositionBase={disp_base}',
        f'ACBS.HealthOffset={health_off}',
        f'ACBS.BleedoutOverride={bleedout_ovr}',
        f'ACBS.OutfitItem={outfit_item}',
    ]


_SKILL_NAMES = [
    'OneHanded','TwoHanded','Archery','Block','Smithing','HeavyArmor','LightArmor',
    'Pickpocket','Lockpicking','Sneak','Alchemy','Speech','Alteration','Conjuration',
    'Destruction','Illusion','Restoration','Enchanting',
]


def _dec_npc_dnam(data: bytes) -> list:
    """NPC_ DNAM — starts with 18 skill values (u8 each), then offsets."""
    if len(data) < 52:
        return [f'DNAM.hex={data.hex()}']
    lines = []
    for i, name in enumerate(_SKILL_NAMES):
        lines.append(f'DNAM.Skills.{name}={data[i]}')
    for i, name in enumerate(_SKILL_NAMES):
        offset_val = data[18 + i]
        if offset_val:
            lines.append(f'DNAM.SkillOffsets.{name}={offset_val}')
    if len(data) >= 52:
        health  = struct.unpack_from('<H', data, 36)[0]
        magicka = struct.unpack_from('<H', data, 38)[0]
        stamina = struct.unpack_from('<H', data, 40)[0]
        lines += [f'DNAM.Health={health}', f'DNAM.Magicka={magicka}', f'DNAM.Stamina={stamina}']
    return lines


_BIPED_SLOTS_TES5 = {
    0:'Head', 1:'Hair', 2:'Body', 3:'Hands', 4:'Forearms', 5:'Amulet',
    6:'Ring', 7:'Feet', 8:'Calves', 9:'Shield', 10:'Tail', 11:'LongHair',
    12:'Circlet', 13:'Ears', 17:'DecapHead', 20:'ChestPrimary', 21:'Back',
    22:'Misc01', 23:'Pelvis', 24:'DecapHeadMini', 25:'LegPrimary',
    26:'LegSecondary', 27:'PelvisSecondary', 28:'TorsoSecondary',
    29:'ForearmSecondary', 30:'ArmSecondary', 31:'ShieldSheath',
}


def _dec_bod2(data: bytes) -> list:
    """ARMO/CLOT BOD2 — 8 bytes: u32 slot_flags + u32 armor_type."""
    if len(data) < 8:
        return [f'BOD2.hex={data.hex()}']
    slots = struct.unpack_from('<I', data, 0)[0]
    atype = struct.unpack_from('<I', data, 4)[0]
    atype_str = {0:'Light', 1:'Heavy', 2:'Clothing'}.get(atype, str(atype))
    active = [_BIPED_SLOTS_TES5.get(i, str(i)) for i in range(32) if slots & (1 << i)]
    return [f'BOD2.Slots={slots:#010x} ({", ".join(active) or "none"})',
            f'BOD2.ArmorType={atype} ({atype_str})']


def _dec_armo_data(data: bytes) -> list:
    if len(data) < 8:
        return [f'DATA.hex={data.hex()}']
    val    = struct.unpack_from('<I', data, 0)[0]
    weight = struct.unpack_from('<f', data, 4)[0]
    return [f'DATA.Value={val}', f'DATA.Weight={weight:.4f}']


def _dec_armo_dnam(data: bytes) -> list:
    """ARMO DNAM — 4 bytes: f32 armor rating."""
    if len(data) < 4:
        return [f'DNAM.hex={data.hex()}']
    return [f'DNAM.ArmorRating={struct.unpack_from("<f", data, 0)[0]:.2f}']


def _dec_misc_data(data: bytes) -> list:
    if len(data) < 8:
        return [f'DATA.hex={data.hex()}']
    val    = struct.unpack_from('<I', data, 0)[0]
    weight = struct.unpack_from('<f', data, 4)[0]
    return [f'DATA.Value={val}', f'DATA.Weight={weight:.4f}']


def _dec_cell_data(data: bytes) -> list:
    """CELL DATA — 2-byte flags in TES5 (was 1 byte in TES4)."""
    if len(data) == 1:
        return [f'DATA.Flags=0x{data[0]:02X}']
    if len(data) >= 2:
        flags = struct.unpack_from('<H', data, 0)[0]
        names = []
        if flags & 0x0001: names.append('IsInterior')
        if flags & 0x0002: names.append('HasWater')
        if flags & 0x0004: names.append('CantTravelFromHere')
        if flags & 0x0008: names.append('NoLODWater')
        if flags & 0x0010: names.append('HandChanged')
        if flags & 0x0020: names.append('ShowSky')
        if flags & 0x0040: names.append('UseSkyLighting')
        return [f'DATA.Flags=0x{flags:04X} ({", ".join(names) or "none"})']
    return [f'DATA.hex={data.hex()}']


def _dec_wrld_dnam(data: bytes) -> list:
    if len(data) < 8:
        return [f'DNAM.hex={data.hex()}']
    land_h = struct.unpack_from('<f', data, 0)[0]
    water_h = struct.unpack_from('<f', data, 4)[0]
    return [f'DNAM.DefaultLandHeight={land_h:.4f}', f'DNAM.DefaultWaterHeight={water_h:.4f}']


def _dec_refr_xtel(data: bytes) -> list:
    if len(data) < 28:
        return [f'XTEL.hex={data.hex()}']
    fid = struct.unpack_from('<I', data, 0)[0]
    px, py, pz = struct.unpack_from('<fff', data, 4)
    rx, ry, rz = struct.unpack_from('<fff', data, 16)
    lines = [f'XTEL.Door={fid:08X}',
             f'XTEL.Pos=({px:.3f},{py:.3f},{pz:.3f})',
             f'XTEL.Rot=({rx:.4f},{ry:.4f},{rz:.4f})']
    if len(data) >= 32:
        flags = struct.unpack_from('<I', data, 28)[0]
        lines.append(f'XTEL.Flags=0x{flags:08X}')
    return lines


def _dec_refr_data(data: bytes) -> list:
    """REFR/ACHR DATA — position + rotation (24 bytes)."""
    if len(data) < 24:
        return [f'DATA.hex={data.hex()}']
    px, py, pz = struct.unpack_from('<fff', data,  0)
    rx, ry, rz = struct.unpack_from('<fff', data, 12)
    return [f'DATA.Pos=({px:.3f},{py:.3f},{pz:.3f})',
            f'DATA.Rot=({rx:.4f},{ry:.4f},{rz:.4f})']


def _dec_refr_xloc(data: bytes) -> list:
    """REFR XLOC — lock data (16 bytes)."""
    if len(data) < 16:
        return [f'XLOC.hex={data.hex()}']
    level = struct.unpack_from('<I', data, 0)[0]
    key   = struct.unpack_from('<I', data, 4)[0]
    flags = struct.unpack_from('<I', data, 8)[0]
    unkn  = struct.unpack_from('<I', data, 12)[0]
    return [f'XLOC.Level={level}', f'XLOC.KeyFID={key:08X}',
            f'XLOC.Flags=0x{flags:08X}', f'XLOC.Unknown={unkn}']


def _dec_spit(data: bytes) -> list:
    """SPEL SPIT — 36 bytes in TES5."""
    if len(data) < 36:
        return [f'SPIT.hex={data.hex()}']
    cost      = struct.unpack_from('<I', data,  0)[0]
    flags     = struct.unpack_from('<I', data,  4)[0]
    spell_type= struct.unpack_from('<I', data,  8)[0]
    charge    = struct.unpack_from('<f', data, 12)[0]
    cast_type = struct.unpack_from('<I', data, 16)[0]
    effect_sz = struct.unpack_from('<I', data, 20)[0]
    range_    = struct.unpack_from('<I', data, 24)[0]
    half_perk = struct.unpack_from('<I', data, 28)[0]
    menu_disp = struct.unpack_from('<I', data, 32)[0]
    cast_names  = {0:'Constant',1:'FireForget',2:'Concentration',3:'Scroll'}
    stype_names = {0:'Spell',3:'Power',8:'LesserPower'}
    return [
        f'SPIT.BaseCost={cost}', f'SPIT.Flags=0x{flags:08X}',
        f'SPIT.Type={spell_type} ({stype_names.get(spell_type, "?")})',
        f'SPIT.ChargeTime={charge:.4f}',
        f'SPIT.CastType={cast_type} ({cast_names.get(cast_type, "?")})',
        f'SPIT.EffectType={effect_sz}', f'SPIT.CastRange={range_}',
        f'SPIT.HalfCostPerk={half_perk:08X}', f'SPIT.MenuDispObject={menu_disp:08X}',
    ]


def _dec_enit_ench(data: bytes) -> list:
    """ENCH ENIT — 36 bytes in TES5."""
    if len(data) < 36:
        return [f'ENIT.hex={data.hex()}']
    cost       = struct.unpack_from('<I', data,  0)[0]
    flags      = struct.unpack_from('<I', data,  4)[0]
    cast_type  = struct.unpack_from('<I', data,  8)[0]
    amt        = struct.unpack_from('<I', data, 12)[0]
    target     = struct.unpack_from('<I', data, 16)[0]
    charge_t   = struct.unpack_from('<f', data, 20)[0]
    base_enc   = struct.unpack_from('<I', data, 24)[0]
    worn_hit   = struct.unpack_from('<I', data, 28)[0]
    menu_disp  = struct.unpack_from('<I', data, 32)[0]
    return [
        f'ENIT.BaseCost={cost}', f'ENIT.Flags=0x{flags:08X}',
        f'ENIT.CastType={cast_type}', f'ENIT.Amount={amt}',
        f'ENIT.TargetType={target}', f'ENIT.ChargeTime={charge_t:.4f}',
        f'ENIT.BaseEnchantment={base_enc:08X}', f'ENIT.WornRestrictions={worn_hit:08X}',
        f'ENIT.MenuDisplayObject={menu_disp:08X}',
    ]


def _dec_hdpt_data(data: bytes) -> list:
    """HDPT DATA — 1 byte type, then flags."""
    if not data:
        return ['HDPT.hex=']
    htype = data[0]
    type_names = {0:'Misc', 1:'Face', 2:'Eyes', 3:'Hair', 4:'FacialHair',
                  5:'Scar', 6:'Brows'}
    lines = [f'HDPT.Type={htype} ({type_names.get(htype, "?")})']
    if len(data) >= 5:
        flags = struct.unpack_from('<I', data, 1)[0]
        lines.append(f'HDPT.Flags=0x{flags:08X}')
    return lines


def _dec_clfm_cnam(data: bytes) -> list:
    """CLFM CNAM — RGBA color (4 bytes)."""
    if len(data) < 4:
        return [f'CNAM.hex={data.hex()}']
    r, g, b, a = data[0], data[1], data[2], data[3]
    return [f'CNAM.R={r}', f'CNAM.G={g}', f'CNAM.B={b}', f'CNAM.A={a}']


def _dec_ltex_hnam(data: bytes) -> list:
    """LTEX HNAM — material type FormID (4 bytes)."""
    if len(data) < 4:
        return [f'HNAM.hex={data.hex()}']
    return [f'HNAM.MaterialType={_fid(data)}']


def _dec_ltex_snam(data: bytes) -> list:
    """LTEX SNAM — 1-byte specular exponent."""
    if data:
        return [f'SNAM.SpecularExp={data[0]}']
    return []


def _dec_clfm_fnam(data: bytes) -> list:
    """CLFM FNAM — playable flag (u32)."""
    if len(data) >= 4:
        val = struct.unpack_from('<I', data, 0)[0]
        return [f'FNAM.Playable={bool(val)}']
    if data:
        return [f'FNAM.Playable={bool(data[0])}']
    return []


def _dec_magic_effect(lines: list, subs: list, idx: int, prefix: str):
    """Decode one EFID+EFIT entry from a spell/ench effects list."""
    if idx < len(subs):
        sub = subs[idx]
        if sub.type == 'EFID' and len(sub.data) == 4:
            lines.append(f'{prefix}.EFID={_fid(sub.data)}')
        elif sub.type == 'EFIT' and len(sub.data) >= 12:
            mag = struct.unpack_from('<f', sub.data, 0)[0]
            dur = struct.unpack_from('<I', sub.data, 4)[0]
            area= struct.unpack_from('<I', sub.data, 8)[0]
            lines.append(f'{prefix}.Magnitude={mag:.4f}')
            lines.append(f'{prefix}.Duration={dur}')
            lines.append(f'{prefix}.Area={area}')


def _dec_fact_data(data: bytes) -> list:
    if len(data) < 4:
        return [f'DATA.hex={data.hex()}']
    flags = struct.unpack_from('<I', data, 0)[0]
    names = []
    if flags & 0x01: names.append('Hidden')
    if flags & 0x02: names.append('SpecialCombat')
    if flags & 0x40: names.append('TrackCrime')
    if flags & 0x80: names.append('IgnoreKills')
    return [f'DATA.Flags=0x{flags:08X} ({", ".join(names) or "none"})']


def _dec_book_data(data: bytes) -> list:
    """BOOK DATA — 16 bytes in TES5."""
    if len(data) < 16:
        return [f'DATA.hex={data.hex()}']
    flags  = data[0]
    btype  = data[1]
    teach  = struct.unpack_from('<I', data, 4)[0]
    val    = struct.unpack_from('<I', data, 8)[0]
    weight = struct.unpack_from('<f', data, 12)[0]
    skill_map = {6:'OneHanded',7:'TwoHanded',8:'Archery',9:'Block',10:'Smithing',
                 11:'HeavyArmor',12:'LightArmor',13:'Pickpocket',14:'Lockpicking',
                 15:'Sneak',16:'Alchemy',17:'Speech',18:'Alteration',19:'Conjuration',
                 20:'Destruction',21:'Illusion',22:'Restoration',23:'Enchanting'}
    return [
        f'DATA.Flags=0x{flags:02X}', f'DATA.Type={btype}',
        f'DATA.Teaches={skill_map.get(teach, str(teach))}',
        f'DATA.Value={val}', f'DATA.Weight={weight:.4f}',
    ]


def _dec_ingr_enit(data: bytes) -> list:
    if len(data) < 8:
        return [f'ENIT.hex={data.hex()}']
    val   = struct.unpack_from('<I', data, 0)[0]
    flags = struct.unpack_from('<I', data, 4)[0]
    return [f'ENIT.Value={val}', f'ENIT.Flags=0x{flags:08X}']


def _dec_ligh_data(data: bytes) -> list:
    if len(data) < 32:
        return [f'DATA.hex={data.hex()}']
    time_   = struct.unpack_from('<i', data,  0)[0]
    radius  = struct.unpack_from('<I', data,  4)[0]
    r, g, b, a = data[8], data[9], data[10], data[11]
    flags   = struct.unpack_from('<I', data, 12)[0]
    falloff = struct.unpack_from('<f', data, 16)[0]
    fov     = struct.unpack_from('<f', data, 20)[0]
    near    = struct.unpack_from('<f', data, 24)[0]
    val     = struct.unpack_from('<I', data, 28)[0]
    return [
        f'DATA.Time={time_}', f'DATA.Radius={radius}',
        f'DATA.Color=({r},{g},{b},{a})', f'DATA.Flags=0x{flags:08X}',
        f'DATA.FalloffExp={falloff:.4f}', f'DATA.FOV={fov:.4f}',
        f'DATA.NearClip={near:.4f}', f'DATA.Value={val}',
    ]


# ---------------------------------------------------------------------------
# Dispatch: (rec_type, sub_type) -> decode_fn(data) -> list[str]
# ---------------------------------------------------------------------------

# Keyed by (rec_type, sub_type). If rec_type is '', applies to all types.
_TYPED_DECODERS: dict = {
    # File header
    ('TES4', 'HEDR'): _dec_hedr,

    # Weapons
    ('WEAP', 'DATA'): _dec_weap_data,
    ('WEAP', 'DNAM'): _dec_weap_dnam,
    ('WEAP', 'CRDT'): _dec_weap_crdt,
    ('WEAP', 'TNAM'): lambda d: [f'TNAM={struct.unpack_from("<I",d,0)[0]:08X}'] if len(d)==4 else [f'TNAM.hex={d.hex()}'],
    ('WEAP', 'NAM8'): lambda d: [f'NAM8={struct.unpack_from("<I",d,0)[0]:08X}'] if len(d)==4 else [f'NAM8.hex={d.hex()}'],
    ('WEAP', 'NAM9'): lambda d: [f'NAM9={struct.unpack_from("<I",d,0)[0]:08X}'] if len(d)==4 else [f'NAM9.hex={d.hex()}'],

    # NPCs
    ('NPC_', 'ACBS'): _dec_npc_acbs,
    ('NPC_', 'DNAM'): _dec_npc_dnam,

    # Armor
    ('ARMO', 'BOD2'): _dec_bod2,
    ('CLOT', 'BOD2'): _dec_bod2,
    ('ARMO', 'DATA'): _dec_armo_data,
    ('ARMO', 'DNAM'): _dec_armo_dnam,

    # Misc items
    ('MISC', 'DATA'): _dec_misc_data,
    ('KEYM', 'DATA'): _dec_misc_data,
    ('SLGM', 'DATA'): _dec_misc_data,
    ('AMMO', 'DATA'): _dec_misc_data,

    # Cell
    ('CELL', 'DATA'): _dec_cell_data,

    # Worldspace
    ('WRLD', 'DNAM'): _dec_wrld_dnam,

    # References
    ('REFR', 'DATA'): _dec_refr_data,
    ('REFR', 'XTEL'): _dec_refr_xtel,
    ('REFR', 'XLOC'): _dec_refr_xloc,
    ('ACHR', 'DATA'): _dec_refr_data,

    # Spells / Enchantments
    ('SPEL', 'SPIT'): _dec_spit,
    ('ENCH', 'ENIT'): _dec_enit_ench,
    ('INGR', 'ENIT'): _dec_ingr_enit,
    ('ALCH', 'ENIT'): _dec_ingr_enit,

    # Head Parts / Colors
    ('HDPT', 'DATA'): _dec_hdpt_data,
    ('CLFM', 'CNAM'): _dec_clfm_cnam,
    ('CLFM', 'FNAM'): _dec_clfm_fnam,

    # Landscape textures
    ('LTEX', 'HNAM'): _dec_ltex_hnam,
    ('LTEX', 'SNAM'): _dec_ltex_snam,

    # Factions
    ('FACT', 'DATA'): _dec_fact_data,

    # Books
    ('BOOK', 'DATA'): _dec_book_data,

    # Lights
    ('LIGH', 'DATA'): _dec_ligh_data,
}


# ---------------------------------------------------------------------------
# Generic subrecord decoder
# ---------------------------------------------------------------------------

def decode_subrecord(rec_type: str, sub: Sub, is_localized: bool,
                     context: dict) -> list:
    """
    Return a list of KEY=VALUE strings for a single subrecord.
    context is a mutable dict used to pass info between consecutive subrecords
    (e.g. KSIZ before KWDA).
    """
    sig  = sub.type
    data = sub.data

    # 1. Per-(rec_type, sub) typed decoder
    fn = _TYPED_DECODERS.get((rec_type, sig))
    if fn:
        return fn(data)

    # 2. OBND (universal)
    if sig == 'OBND':
        return _dec_obnd(data)

    # 3. KSIZ — keyword count
    if sig == 'KSIZ' and len(data) == 4:
        n = struct.unpack_from('<I', data, 0)[0]
        context['KSIZ'] = n
        return [f'KSIZ={n}']

    # 4. KWDA — keyword array (uses KSIZ from context)
    if sig == 'KWDA':
        return _dec_kwda(data, context.get('KSIZ', 0))

    # 5. COCT — count (item containers)
    if sig == 'COCT' and len(data) == 4:
        return [f'COCT={struct.unpack_from("<I", data, 0)[0]}']

    # 6. Empty subrecords
    if not data:
        return [f'{sig}=[empty]']

    # 7. Known string subrecords
    if sig == 'EDID':
        return [f'{sig}={_escape(_zstring(data))}']
    if sig == 'MODL' or sig.startswith('MOD') or sig in ('ICON', 'ICO2', 'MICO'):
        if _is_zstring(data):
            return [f'{sig}={_escape(_zstring(data))}']

    # 8. FULL / DESC — may be LStrings
    if sig in ('FULL', 'DESC', 'NNAM') and is_localized and len(data) == 4:
        idx = struct.unpack_from('<I', data, 0)[0]
        return [f'{sig}=LSTRING:{idx:08X}']
    if sig in ('FULL', 'DESC', 'NNAM'):
        if _is_zstring(data):
            return [f'{sig}={_escape(_zstring(data))}']

    # 9. MAST / DATA in file header
    if rec_type == 'TES4':
        if sig == 'MAST':
            return [f'MAST={_escape(_zstring(data))}']
        if sig == 'DATA' and len(data) == 8:
            sz = struct.unpack_from('<Q', data, 0)[0]
            return [f'DATA.MasterSize={sz}']

    # 10. Exact 4-byte data — try FormID / uint / float interpretation
    if len(data) == 4:
        uint_val  = struct.unpack_from('<I', data, 0)[0]
        float_val = struct.unpack_from('<f', data, 0)[0]
        fid_str   = f'{uint_val:08X}'
        # Known FormID-only subs: suppress float interpretation noise
        if sig in _FORMID4_SUBS:
            return [f'{sig}={fid_str}']
        # Heuristic: if it looks like a FormID (float value nonsensical)
        if abs(float_val) > 1e7 or float_val != float_val:  # big or NaN
            return [f'{sig}={fid_str}  (uint={uint_val})']
        return [f'{sig}={fid_str}  (uint={uint_val}  float={float_val:.5g})']

    # 11. Small primitives
    if len(data) == 2:
        val = struct.unpack_from('<H', data, 0)[0]
        return [f'{sig}={val}']
    if len(data) == 1:
        return [f'{sig}={data[0]}']

    # 12. Try as string if heuristic passes
    if _is_zstring(data):
        return [f'{sig}={_escape(_zstring(data))}']

    # 13. Fallback: hex dump (first 96 bytes)
    hex_str = data.hex().upper()
    if len(data) > 96:
        hex_str = hex_str[:192] + f'... ({len(data)} bytes total)'
    return [f'{sig}.size={len(data)}', f'{sig}.hex={hex_str}']


# ---------------------------------------------------------------------------
# Effects (EFID/EFIT pairs shared by SPEL, ENCH, INGR, ALCH)
# ---------------------------------------------------------------------------

def _decode_effects(rec: TES5Record) -> list:
    """Collect all EFID+EFIT pairs into indexed Effect[i].* lines."""
    lines = []
    i = 0
    idx = 0
    subs = rec.subrecords
    while i < len(subs):
        s = subs[i]
        if s.type == 'EFID':
            prefix = f'Effect[{idx}]'
            lines.append(f'{prefix}.EFID={_fid(s.data)}')
            # Next should be EFIT
            if i + 1 < len(subs) and subs[i + 1].type == 'EFIT':
                efit = subs[i + 1].data
                if len(efit) >= 12:
                    mag  = struct.unpack_from('<f', efit,  0)[0]
                    dur  = struct.unpack_from('<I', efit,  4)[0]
                    area = struct.unpack_from('<I', efit,  8)[0]
                    lines += [f'{prefix}.Magnitude={mag:.4f}',
                               f'{prefix}.Duration={dur}', f'{prefix}.Area={area}']
                i += 2
            else:
                i += 1
            idx += 1
        else:
            i += 1
    return lines


# ---------------------------------------------------------------------------
# PNAM arrays (NPC_ head parts — one PNAM sub per head part)
# ---------------------------------------------------------------------------

def _decode_pnam_array(rec: TES5Record) -> list:
    lines = []
    pnam_subs = _all(rec, 'PNAM')
    for i, s in enumerate(pnam_subs):
        if len(s.data) == 4:
            lines.append(f'PNAM[{i}]={_fid(s.data)}')
    return lines


# ---------------------------------------------------------------------------
# Level-list entry decoder (LVLO)
# ---------------------------------------------------------------------------

def _decode_lvlo_array(rec: TES5Record) -> list:
    lines = []
    lvlo_subs = _all(rec, 'LVLO')
    for i, s in enumerate(lvlo_subs):
        if len(s.data) >= 8:
            lv   = struct.unpack_from('<H', s.data, 0)[0]
            pad  = struct.unpack_from('<H', s.data, 2)[0]
            fid  = struct.unpack_from('<I', s.data, 4)[0]
            cnt  = struct.unpack_from('<H', s.data, 8)[0] if len(s.data) >= 10 else 1
            lines.append(f'LVLO[{i}].Level={lv}')
            lines.append(f'LVLO[{i}].FormID={fid:08X}')
            lines.append(f'LVLO[{i}].Count={cnt}')
    return lines


# ---------------------------------------------------------------------------
# Container item decoder (CNTO)
# ---------------------------------------------------------------------------

def _decode_cnto_array(rec: TES5Record) -> list:
    lines = []
    cnto_subs = _all(rec, 'CNTO')
    for i, s in enumerate(cnto_subs):
        if len(s.data) >= 8:
            fid = struct.unpack_from('<I', s.data, 0)[0]
            cnt = struct.unpack_from('<I', s.data, 4)[0]
            lines.append(f'CNTO[{i}].FormID={fid:08X}')
            lines.append(f'CNTO[{i}].Count={cnt}')
    return lines


# ---------------------------------------------------------------------------
# Special-case whole-record formatters
# ---------------------------------------------------------------------------

# Record types where we handle certain array subs manually
_ARRAY_SUBS = frozenset({'PNAM', 'LVLO', 'CNTO', 'EFID', 'EFIT', 'KWDA', 'KSIZ'})

# For PNAM: NPC_ has an array; other records have a single PNAM
_NPC_ONLY_ARRAY_PNAM = frozenset({'NPC_', 'HDPT'})

# Effect-bearing types (EFID+EFIT pairs)
_EFFECT_TYPES = frozenset({'SPEL', 'ENCH', 'INGR', 'ALCH'})

# Level-list types
_LVLLIST_TYPES = frozenset({'LVLI', 'LVLN', 'LVSP'})

# Container types
_CONTAINER_TYPES = frozenset({'CONT'})


# ---------------------------------------------------------------------------
# Record formatter
# ---------------------------------------------------------------------------

def format_record(rec: TES5Record, is_localized: bool) -> str:
    lines = ['---RECORD_BEGIN---']
    lines.append(f'Signature={rec.type}')
    lines.append(f'FormID={rec.form_id:08X}')

    edid_sub = _get(rec, 'EDID')
    if edid_sub:
        lines.append(f'EditorID={_escape(_zstring(edid_sub.data))}')

    lines.append(f'RecordFlags={rec.flags}')
    lines.append(f'FormVersion={rec.form_version}')

    if rec.parent_wrld:
        lines.append(f'ParentWRLD={rec.parent_wrld:08X}')
    if rec.parent_cell:
        lines.append(f'ParentCELL={rec.parent_cell:08X}')
    if rec.parent_dial:
        lines.append(f'ParentDIAL={rec.parent_dial:08X}')

    # Collect subs we'll skip (handled elsewhere)
    skip_sigs: set = {'EDID'}

    # Pre-decode arrays so we can skip those subs in the main loop
    extra_lines: list = []

    if rec.type in _NPC_ONLY_ARRAY_PNAM:
        extra_lines += _decode_pnam_array(rec)
        skip_sigs.add('PNAM')

    if rec.type in _EFFECT_TYPES:
        extra_lines += _decode_effects(rec)
        skip_sigs.update(('EFID', 'EFIT'))

    if rec.type in _LVLLIST_TYPES:
        data_sub = _get(rec, 'DATA')
        if data_sub and data_sub.data:
            lines.append(f'DATA.Flags={data_sub.data[0]}')
            lines.append(f'DATA.LvlChance={data_sub.data[1] if len(data_sub.data) > 1 else 0}')
            skip_sigs.add('DATA')
        extra_lines += _decode_lvlo_array(rec)
        skip_sigs.add('LVLO')

    if rec.type in _CONTAINER_TYPES:
        extra_lines += _decode_cnto_array(rec)
        skip_sigs.add('CNTO')

    context: dict = {}
    prev_ksiz: int = 0

    for sub in rec.subrecords:
        if sub.type in skip_sigs:
            continue
        if sub.type == 'KSIZ':
            # Track KSIZ for next KWDA — pass through generic decoder which sets context
            decoded = decode_subrecord(rec.type, sub, is_localized, context)
            lines.extend(decoded)
            continue
        if sub.type == 'KWDA':
            lines.extend(_dec_kwda(sub.data, context.get('KSIZ', 0)))
            continue
        lines.extend(decode_subrecord(rec.type, sub, is_localized, context))

    lines.extend(extra_lines)
    lines.append('---RECORD_END---')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Export driver
# ---------------------------------------------------------------------------

def export_all(all_records: list, output_dir: str, is_localized: bool,
               type_filter: set = None):
    by_type = defaultdict(list)
    for rec in all_records:
        if type_filter and rec.type not in type_filter:
            continue
        by_type[rec.type].append(rec)

    total = sum(len(v) for v in by_type.values())
    print(f'  Exporting {total} records across {len(by_type)} types')
    for sig in sorted(by_type):
        print(f'    {sig}: {len(by_type[sig])}')

    os.makedirs(output_dir, exist_ok=True)
    t0 = time.time()

    for sig in sorted(by_type):
        records = by_type[sig]
        blocks = [format_record(r, is_localized) for r in records]
        text = '\n\n'.join(blocks) + '\n'
        out_path = os.path.join(output_dir, f'{sig}.txt')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f'    Wrote {out_path} ({len(records)} records)')

    print(f'  Done in {time.time() - t0:.2f}s')


def export_header_file(header: TES5Record, output_dir: str, is_localized: bool):
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, '_HEADER.txt')
    lines = ['---FILE_HEADER---']
    lines.append(f'IsLocalized={is_localized}')
    lines.append(f'Flags=0x{header.flags:08X}')
    context: dict = {}
    for sub in header.subrecords:
        lines.extend(decode_subrecord('TES4', sub, is_localized, context))
    lines.append('---FILE_HEADER_END---')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f'    Wrote {out_path}')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Read a TES5 ESM/ESP and dump to KEY=VALUE text (one file per record type).')
    parser.add_argument('esm', nargs='?', default=DEFAULT_ESM,
                        help='Path to TES5 ESM/ESP (default: Skyrim SE Skyrim.esm)')
    parser.add_argument('--outdir', default=None,
                        help='Output directory (default: references/<basename>)')
    parser.add_argument('--types', nargs='+', metavar='SIG',
                        help='Only export these record types (e.g. WEAP NPC_ HDPT)')
    parser.add_argument('--list-types', action='store_true',
                        help='Just list record types and counts, then exit')
    args = parser.parse_args()

    esm_path = args.esm
    if not os.path.isfile(esm_path):
        print(f'ERROR: File not found: {esm_path}', file=sys.stderr)
        sys.exit(1)

    basename    = os.path.basename(esm_path)
    output_dir  = args.outdir or os.path.join('references', basename)
    type_filter = set(args.types) if args.types else None

    print(f'Reading {esm_path} ...')
    t0 = time.time()
    header, all_records, is_localized = read_tes5_file(esm_path)
    print(f'  Parsed {len(all_records):,} records in {time.time() - t0:.2f}s')
    print(f'  Localized strings: {is_localized}')

    if args.list_types:
        by_type: dict = defaultdict(int)
        for r in all_records:
            by_type[r.type] += 1
        print(f'\n{"Type":6s}  {"Count":>8s}')
        print('-' * 18)
        for sig in sorted(by_type):
            print(f'{sig:6s}  {by_type[sig]:>8,}')
        return

    print(f'Output directory: {output_dir}')
    export_header_file(header, output_dir, is_localized)
    export_all(all_records, output_dir, is_localized, type_filter)
    print('Done.')


if __name__ == '__main__':
    main()

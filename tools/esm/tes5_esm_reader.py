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
    python tools/esm/tes5_esm_reader.py
    python tools/esm/tes5_esm_reader.py "C:/path/to/Skyrim.esm"
    python tools/esm/tes5_esm_reader.py "C:/path/to/Skyrim.esm" --outdir references/Skyrim.esm
    python tools/esm/tes5_esm_reader.py "C:/path/to/Skyrim.esm" --types WEAP NPC_ HDPT CLFM
    python tools/esm/tes5_esm_reader.py "C:/path/to/Skyrim.esm" --list-types
"""

import argparse
import os
import struct
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tes5_import.base.tes5_reader import REC_HDR, subrecords, walk

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: File-level flag in the TES4/file header record.
FLAG_LOCALIZED = 0x00000080

#: GRUP type 7 -- topic children, labelled with the owning DIAL's FormID.
GRP_TOPIC_CHILDREN = 7

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
    # Dialog: FormID references
    'TCLT', 'TCLF', 'QNAM', 'TIFC',
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
    return [Sub(type=t.decode('ascii', errors='replace'), data=d)
            for t, d in subrecords(data)]


def _shape(rec, wrld: int, cell: int, dial: int, parse: bool) -> TES5Record:
    """One shared-reader Record restated as this tool's TES5Record."""
    out = TES5Record(type=rec.sig.decode('ascii', errors='replace'),
                     data_size=rec.size, flags=rec.flags,
                     form_id=rec.form_id, form_version=rec.form_version,
                     parent_wrld=wrld, parent_cell=cell, parent_dial=dial)
    if parse:
        out.subrecords = _parse_subrecords(rec.body)
    return out


def read_tes5_file(filepath: str, parse_types=None):
    """
    Read a TES5 ESM/ESP.

    parse_types: optional set of record signatures to fully parse; records of
    other types are returned with header fields (type/form_id/flags) only.

    Returns:
        header_rec: TES5Record (the TES4/file header)
        all_records: list[TES5Record]
        is_localized: bool

    parent_wrld/cell/dial come from the enclosing GRUP's label, which is scoped
    by the walk. A record that labels a group sits OUTSIDE it and correctly
    reports 0.
    See: docs/reference/python_tools.md#tes5-record-parent-fields
    """
    raw = Path(filepath).read_bytes()
    size = struct.unpack_from('<I', raw, 4)[0]
    header = TES5Record(type='TES4', data_size=size,
                        flags=struct.unpack_from('<I', raw, 8)[0],
                        form_id=struct.unpack_from('<I', raw, 12)[0],
                        form_version=struct.unpack_from('<H', raw, 20)[0])
    header.subrecords = _parse_subrecords(raw[REC_HDR:REC_HDR + size])
    is_localized = bool(header.flags & FLAG_LOCALIZED)

    want = None if parse_types is None else frozenset(
        s.encode('ascii') for s in parse_types)
    records = []
    for rec, stack in walk(raw, bodies=want):
        topic = stack.of_type(GRP_TOPIC_CHILDREN)
        records.append(_shape(
            rec, stack.worldspace or 0, stack.cell or 0,
            0 if topic is None else topic.label_fid,
            want is None or rec.sig in want))
    return header, records, is_localized


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


def master_edids(esm_path, header, sig: str) -> dict:
    """{FormID in THIS plugin's index space -> EditorID} for one record type,
    read from every converted master in the plugin's own MAST list.

    A dependent plugin's records freely reference its masters (Morroblivion's
    topics name Oblivion.esm quests; its actors use Oblivion.esm voice types),
    so any tool that resolves a FormID to an EditorID from THIS plugin's
    records alone silently reports the reference as missing — and then agrees
    with whatever bug it was written to catch. Masters are enumerated in MAST
    order, which is exactly the load-order index their records carry here.
    """
    from pathlib import Path as _Path
    out = {}
    masters = [_zstring(s.data) for s in header.subrecords if s.type == 'MAST']
    for idx, name in enumerate(masters):
        # Only converted TES4 masters have a build beside ours; Skyrim.esm is
        # vanilla and owns nothing we convert.
        path = _Path(esm_path).parent.parent / name / name
        if not path.is_file():
            continue
        try:
            _h, mrecs, _l = read_tes5_file(str(path),
                                           parse_types=frozenset({sig}))
        except Exception:
            continue
        for rec in mrecs:
            if rec.type != sig:
                continue
            edid = _get(rec, 'EDID')
            if edid:
                out[(idx << 24) | (rec.form_id & 0xFFFFFF)] = _zstring(edid.data)
    return out


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


#: wbWeaponAnimTypeEnum (xEdit wbDefinitionsTES5), verified on Skyrim.esm HuntingBow = 7.
_WEAP_ANIM = {0:'HandToHand', 1:'OneHandSword', 2:'OneHandDagger', 3:'OneHandAxe',
              4:'OneHandMace', 5:'TwoHandSword', 6:'TwoHandAxe', 7:'Bow',
              8:'Staff', 9:'Crossbow'}
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
    template_flgs = struct.unpack_from('<H', data, 18)[0]
    health_off    = struct.unpack_from('<h', data, 20)[0]
    bleedout_ovr  = struct.unpack_from('<H', data, 22)[0]
    return [
        f'ACBS.Flags=0x{flags:08X}',
        f'ACBS.MagickaOffset={magicka_off}',
        f'ACBS.StaminaOffset={stamina_off}',
        f'ACBS.Level={level}',
        f'ACBS.CalcMin={calc_min}',
        f'ACBS.CalcMax={calc_max}',
        f'ACBS.SpeedMult={speed_mult}',
        f'ACBS.DispositionBase={disp_base}',
        f'ACBS.TemplateFlags=0x{template_flgs:04X}',
        f'ACBS.HealthOffset={health_off}',
        f'ACBS.BleedoutOverride={bleedout_ovr}',
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
    """ARMO DNAM — 4 bytes: S32 armor rating stored x100.

    NOT a float: xEdit's TES5 ARMO definition is
    `wbInteger(DNAM, 'Armor Rating', itS32, wbDiv(100))`
    (wbDefinitionsTES5.pas:4176). Decoding it as f32 made every real rating
    print as 0.00 (1000 read as a float is ~1.4e-42), which reads as "Skyrim
    stores no armor rating here" and hides the x100 scale.
    """
    if len(data) < 4:
        return [f'DNAM.hex={data.hex()}']
    raw = struct.unpack_from('<i', data, 0)[0]
    return [f'DNAM.ArmorRating={raw / 100:.2f}', f'DNAM.ArmorRatingRaw={raw}']


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


# MGEF DATA archetypes (xEdit wbDefinitionsTES5.pas wbMGEFType).
_MGEF_ARCHETYPES = {
    0: 'ValueModifier', 1: 'Script', 2: 'Dispel', 3: 'CureDisease',
    4: 'Absorb', 5: 'DualValueModifier', 6: 'Calm', 7: 'Demoralize',
    8: 'Frenzy', 9: 'Disarm', 10: 'CommandSummoned', 11: 'Invisibility',
    12: 'Light', 15: 'Lock', 16: 'Open', 17: 'BoundWeapon',
    18: 'SummonCreature', 19: 'DetectLife', 20: 'Telekinesis',
    21: 'Paralysis', 22: 'Reanimate', 23: 'SoulTrap', 24: 'TurnUndead',
    25: 'Guide', 26: 'WerewolfFeed', 27: 'CureParalysis', 28: 'CureAddiction',
    29: 'CurePoison', 30: 'Concussion', 31: 'ValueAndParts',
    32: 'AccumulateMagnitude', 33: 'Stagger', 34: 'PeakValueModifier',
    35: 'Cloak', 36: 'Werewolf', 37: 'SlowTime', 38: 'Rally',
    39: 'EnhanceWeapon', 40: 'SpawnHazard', 41: 'Etherealize', 42: 'Banish',
    43: 'SpawnScriptedRef', 44: 'Disguise', 45: 'GrabActor', 46: 'VampireLord',
}

# (offset, name, kind) for every field of the 152-byte TES5 MGEF DATA struct.
# Layout from xEdit wbDefinitionsTES5.pas wbMGEFData; verified field-by-field
# against references/Skyrim.esm MGEF records (e.g. SummonFlameAtronach carries
# Archetype=18 SummonCreature with AssocItem = a real NPC_).
_MGEF_DATA_FIELDS = (
    (0, 'Flags', 'x'), (4, 'BaseCost', 'f'), (8, 'AssocItem', 'fid'),
    (12, 'MagicSkill', 'i'), (16, 'ResistValue', 'i'),
    (20, 'CounterEffectCount', 'h'), (24, 'CastingLight', 'fid'),
    (28, 'TaperWeight', 'f'), (32, 'HitShader', 'fid'),
    (36, 'EnchantShader', 'fid'), (40, 'MinimumSkillLevel', 'u'),
    (44, 'SpellmakingArea', 'u'), (48, 'SpellmakingCastingTime', 'f'),
    (52, 'TaperCurve', 'f'), (56, 'TaperDuration', 'f'),
    (60, 'SecondAVWeight', 'f'), (64, 'Archetype', 'arch'),
    (68, 'ActorValue', 'i'), (72, 'Projectile', 'fid'),
    (76, 'Explosion', 'fid'), (80, 'CastingType', 'u'), (84, 'Delivery', 'u'),
    (88, 'SecondActorValue', 'i'), (92, 'CastingArt', 'fid'),
    (96, 'HitEffectArt', 'fid'), (100, 'ImpactData', 'fid'),
    (104, 'SkillUsageMultiplier', 'f'), (108, 'DualCastArt', 'fid'),
    (112, 'DualCastScale', 'f'), (116, 'EnchantArt', 'fid'),
    (120, 'HitVisuals', 'fid'), (124, 'EnchantVisuals', 'fid'),
    (128, 'EquipAbility', 'fid'), (132, 'ImageSpaceModifier', 'fid'),
    (136, 'PerkToApply', 'fid'), (140, 'CastingSoundLevel', 'u'),
    (144, 'ScriptEffectAIScore', 'f'), (148, 'ScriptEffectAIDelayTime', 'f'),
)


def _dec_mgef_data(data: bytes) -> list:
    """MGEF DATA — 152 bytes in TES5.

    Emits the full hex UNTRUNCATED: tools/generators/gen_vanilla_mgef_table.py bakes these
    blobs into a committed table, and the generic hex fallback used to cut them
    at 96 bytes, silently dropping the last 14 fields (HitEffectArt..AI delay).
    """
    lines = [f'DATA.size={len(data)}', f'DATA.hex={data.hex().upper()}']
    if len(data) < 152:
        lines.append(f'DATA.TRUNCATED=expected 152 bytes, got {len(data)}')
        return lines
    for off, name, kind in _MGEF_DATA_FIELDS:
        if kind == 'f':
            lines.append(f'DATA.{name}={struct.unpack_from("<f", data, off)[0]:.6g}')
        elif kind == 'fid':
            lines.append(f'DATA.{name}={struct.unpack_from("<I", data, off)[0]:08X}')
        elif kind == 'i':
            lines.append(f'DATA.{name}={struct.unpack_from("<i", data, off)[0]}')
        elif kind == 'h':
            lines.append(f'DATA.{name}={struct.unpack_from("<H", data, off)[0]}')
        elif kind == 'x':
            lines.append(f'DATA.{name}=0x{struct.unpack_from("<I", data, off)[0]:08X}')
        elif kind == 'arch':
            v = struct.unpack_from('<I', data, off)[0]
            lines.append(f'DATA.{name}={v} ({_MGEF_ARCHETYPES.get(v, "?")})')
        else:
            lines.append(f'DATA.{name}={struct.unpack_from("<I", data, off)[0]}')
    return lines


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
            # TES5 EFIT is Magnitude, AREA, DURATION (xEdit wbEFIT).  These two
            # were labelled the other way round, which made every dump of a
            # converted spell look like its duration and area were swapped.
            # Settled by census: all 427 vanilla ALCH effects write 0 at
            # offset 4 and 30/60/300/720 at offset 8 — potion durations in
            # seconds, and potions have no area.
            mag = struct.unpack_from('<f', sub.data, 0)[0]
            area = struct.unpack_from('<I', sub.data, 4)[0]
            dur = struct.unpack_from('<I', sub.data, 8)[0]
            lines.append(f'{prefix}.Magnitude={mag:.4f}')
            lines.append(f'{prefix}.Area={area}')
            lines.append(f'{prefix}.Duration={dur}')


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
# Condition (CTDA) function name map — TES5 condition functions
# Generated from xEdit wbDefinitionsTES5.pas wbCTDAFunctions
# ---------------------------------------------------------------------------

_CTDA_FUNC_NAMES: dict = {
    0: 'GetWantBlocking', 1: 'GetDistance', 5: 'GetLocked', 6: 'GetPos',
    8: 'GetAngle', 10: 'GetStartingPos', 11: 'GetStartingAngle',
    12: 'GetSecondsPassed', 14: 'GetActorValue', 18: 'GetCurrentTime',
    24: 'GetScale', 25: 'IsMoving', 26: 'IsTurning', 27: 'GetLineOfSight',
    32: 'GetInSameCell', 35: 'GetDisabled', 36: 'MenuMode', 39: 'GetDisease',
    41: 'GetClothingValue', 42: 'SameFaction', 43: 'SameRace', 44: 'SameSex',
    45: 'GetDetected', 46: 'GetDead', 47: 'GetItemCount', 48: 'GetGold',
    49: 'GetSleeping', 50: 'GetTalkedToPC', 53: 'GetScriptVariable',
    56: 'GetQuestRunning', 58: 'GetStage', 59: 'GetStageDone',
    60: 'GetFactionRankDifference', 61: 'GetAlarmed', 62: 'IsRaining',
    63: 'GetAttacked', 64: 'GetIsCreature', 65: 'GetLockLevel',
    66: 'GetShouldAttack', 67: 'GetInCell', 68: 'GetIsClass',
    69: 'GetIsRace', 70: 'GetIsSex', 71: 'GetInFaction', 72: 'GetIsID',
    73: 'GetFactionRank', 74: 'GetGlobalValue', 75: 'IsSnowing',
    77: 'GetRandomPercent', 79: 'GetQuestVariable', 80: 'GetLevel',
    81: 'IsRotating', 84: 'GetDeadCount', 91: 'GetIsAlerted',
    98: 'GetPlayerControlsDisabled', 99: 'GetHeadingAngle',
    101: 'IsWeaponMagicOut', 102: 'IsTorchOut', 103: 'IsShieldOut',
    106: 'IsFacingUp', 107: 'GetKnockedState', 108: 'GetWeaponAnimType',
    109: 'IsWeaponSkillType', 110: 'GetCurrentAIPackage', 111: 'IsWaiting',
    112: 'IsIdlePlaying', 116: 'IsIntimidatedbyPlayer',
    117: 'IsPlayerInRegion', 118: 'GetActorAggroRadiusViolated',
    122: 'GetCrime', 123: 'IsGreetingPlayer', 125: 'IsGuard',
    127: 'HasBeenEaten', 128: 'GetStaminaPercentage', 129: 'GetPCIsClass',
    130: 'GetPCIsRace', 131: 'GetPCIsSex', 132: 'GetPCInFaction',
    133: 'SameFactionAsPC', 134: 'SameRaceAsPC', 135: 'SameSexAsPC',
    136: 'GetIsReference', 141: 'IsTalking', 142: 'GetWalkSpeed',
    143: 'GetCurrentAIProcedure', 144: 'GetTrespassWarningLevel',
    145: 'IsTrespassing', 146: 'IsInMyOwnedCell', 147: 'GetWindSpeed',
    148: 'GetCurrentWeatherPercent', 149: 'GetIsCurrentWeather',
    150: 'IsContinuingPackagePCNear', 152: 'GetIsCrimeFaction',
    153: 'CanHaveFlames', 154: 'HasFlames', 157: 'GetOpenState',
    159: 'GetSitting', 161: 'GetIsCurrentPackage',
    162: 'IsCurrentFurnitureRef', 163: 'IsCurrentFurnitureObj',
    170: 'GetDayOfWeek', 172: 'GetTalkedToPCParam', 175: 'IsPCSleeping',
    176: 'IsPCAMurderer', 180: 'HasSameEditorLocAsRef',
    181: 'HasSameEditorLocAsRefAlias', 182: 'GetEquipped',
    185: 'IsSwimming', 190: 'GetAmountSoldStolen', 192: 'GetIgnoreCrime',
    193: 'GetPCExpelled', 195: 'GetPCFactionMurder',
    197: 'GetPCEnemyofFaction', 199: 'GetPCFactionAttack',
    203: 'GetDestroyed', 214: 'HasMagicEffect', 215: 'GetDefaultOpen',
    219: 'GetAnimAction', 223: 'IsSpellTarget', 224: 'GetVATSMode',
    225: 'GetPersuasionNumber', 226: 'GetVampireFeed', 227: 'GetCannibal',
    228: 'GetIsClassDefault', 229: 'GetClassDefaultMatch',
    230: 'GetInCellParam', 235: 'GetVatsTargetHeight', 237: 'GetIsGhost',
    242: 'GetUnconscious', 244: 'GetRestrained', 246: 'GetIsUsedItem',
    247: 'GetIsUsedItemType', 248: 'IsScenePlaying',
    249: 'IsInDialogueWithPlayer', 250: 'GetLocationCleared',
    254: 'GetIsPlayableRace', 255: 'GetOffersServicesNow',
    258: 'HasAssociationType', 259: 'HasFamilyRelationship',
    261: 'HasParentRelationship', 262: 'IsWarningAbout',
    263: 'IsWeaponOut', 264: 'HasSpell', 265: 'IsTimePassing',
    266: 'IsPleasant', 267: 'IsCloudy', 274: 'IsSmallBump',
    277: 'GetBaseActorValue', 278: 'IsOwner', 280: 'IsCellOwner',
    282: 'IsHorseStolen', 285: 'IsLeftUp', 286: 'IsSneaking',
    287: 'IsRunning', 288: 'GetFriendHit', 289: 'IsInCombat',
    300: 'IsInInterior', 304: 'IsWaterObject', 305: 'GetPlayerAction',
    306: 'IsActorUsingATorch', 309: 'IsXBox', 310: 'GetInWorldspace',
    312: 'GetPCMiscStat', 313: 'GetPairedAnimation',
    314: 'IsActorAVictim', 315: 'GetTotalPersuasionNumber',
    318: 'GetIdleDoneOnce', 320: 'GetNoRumors', 323: 'GetCombatState',
    325: 'GetWithinPackageLocation', 327: 'IsRidingMount', 329: 'IsFleeing',
    332: 'IsInDangerousWater', 338: 'GetIgnoreFriendlyHits',
    339: 'IsPlayersLastRiddenMount', 353: 'IsActor', 354: 'IsEssential',
    358: 'IsPlayerMovingIntoNewSpace', 359: 'GetInCurrentLoc',
    360: 'GetInCurrentLocAlias', 361: 'GetTimeDead', 362: 'HasLinkedRef',
    365: 'IsChild', 366: 'GetStolenItemValueNoCrime',
    367: 'GetLastPlayerAction', 368: 'IsPlayerActionActive',
    370: 'IsTalkingActivatorActor', 372: 'IsInList',
    373: 'GetStolenItemValue', 375: 'GetCrimeGoldViolent',
    376: 'GetCrimeGoldNonviolent', 378: 'HasShout', 381: 'GetHasNote',
    390: 'GetHitLocation', 391: 'IsPC1stPerson', 396: 'GetCauseofDeath',
    397: 'IsLimbGone', 398: 'IsWeaponInList', 402: 'IsBribedbyPlayer',
    403: 'GetRelationshipRank', 407: 'GetVATSValue', 408: 'IsKiller',
    409: 'IsKillerObject', 410: 'GetFactionCombatReaction', 414: 'Exists',
    415: 'GetGroupMemberCount', 416: 'GetGroupTargetCount',
    426: 'GetIsVoiceType', 427: 'GetPlantedExplosive',
    429: 'IsScenePackageRunning', 430: 'GetHealthPercentage',
    432: 'GetIsObjectType', 434: 'GetDialogueEmotion',
    435: 'GetDialogueEmotionValue', 437: 'GetIsCreatureType',
    444: 'GetInCurrentLocFormList', 445: 'GetInZone', 446: 'GetVelocity',
    447: 'GetGraphVariableFloat', 448: 'HasPerk', 449: 'GetFactionRelation',
    450: 'IsLastIdlePlayed', 453: 'GetPlayerTeammate',
    454: 'GetPlayerTeammateCount', 458: 'GetActorCrimePlayerEnemy',
    459: 'GetCrimeGold', 463: 'IsPlayerGrabbedRef',
    465: 'GetKeywordItemCount', 470: 'GetDestructionStage',
    473: 'GetIsAlignment', 476: 'IsProtected', 477: 'GetThreatRatio',
    479: 'GetIsUsedItemEquipType', 487: 'IsCarryable', 488: 'GetConcussed',
    491: 'GetMapMarkerVisible', 493: 'PlayerKnows',
    494: 'GetPermanentActorValue', 495: 'GetKillingBlowLimb',
    497: 'CanPayCrimeGold', 499: 'GetDaysInJail',
    500: 'EPAlchemyGetMakingPoison', 501: 'EPAlchemyEffectHasKeyword',
    503: 'GetAllowWorldInteractions', 508: 'GetLastHitCritical',
    513: 'IsCombatTarget', 515: 'GetVATSRightAreaFree',
    516: 'GetVATSLeftAreaFree', 517: 'GetVATSBackAreaFree',
    518: 'GetVATSFrontAreaFree', 519: 'GetLockIsBroken',
    522: 'GetVATSRightTargetVisible', 523: 'GetVATSLeftTargetVisible',
    524: 'GetVATSBackTargetVisible', 525: 'GetVATSFrontTargetVisible',
    528: 'IsInCriticalStage', 530: 'GetXPForNextLevel',
    533: 'GetInfamy', 534: 'GetInfamyViolent', 535: 'GetInfamyNonViolent',
    543: 'GetQuestCompleted', 547: 'IsGoreDisabled',
    550: 'IsSceneActionComplete', 552: 'GetSpellUsageNum',
    554: 'GetActorsInHigh', 555: 'HasLoaded3D', 560: 'HasKeyword',
    561: 'HasRefType', 562: 'LocationHasKeyword', 563: 'LocationHasRefType',
    565: 'GetIsEditorLocation', 566: 'GetIsAliasRef',
    567: 'GetIsEditorLocAlias', 568: 'IsSprinting', 569: 'IsBlocking',
    570: 'HasEquippedSpell', 571: 'GetCurrentCastingType',
    572: 'GetCurrentDeliveryType', 574: 'GetAttackState',
    576: 'GetEventData', 577: 'IsCloserToAThanB', 579: 'GetEquippedShout',
    580: 'IsBleedingOut', 584: 'GetRelativeAngle',
    589: 'GetMovementDirection', 590: 'IsInScene',
    591: 'GetRefTypeDeadCount', 592: 'GetRefTypeAliveCount',
    594: 'GetIsFlying', 595: 'IsCurrentSpell', 596: 'SpellHasKeyword',
    597: 'GetEquippedItemType', 598: 'GetLocationAliasCleared',
    600: 'GetLocAliasRefTypeDeadCount', 601: 'GetLocAliasRefTypeAliveCount',
    602: 'IsWardState', 603: 'IsInSameCurrentLocAsRef',
    604: 'IsInSameCurrentLocAsRefAlias', 605: 'LocAliasIsLocation',
    606: 'GetKeywordDataForLocation', 608: 'GetKeywordDataForAlias',
    610: 'LocAliasHasKeyword', 611: 'IsNullPackageData',
    612: 'GetNumericPackageData', 613: 'IsFurnitureAnimType',
    614: 'IsFurnitureEntryType', 615: 'GetHighestRelationshipRank',
    616: 'GetLowestRelationshipRank', 617: 'HasAssociationTypeAny',
    618: 'HasFamilyRelationshipAny', 619: 'GetPathingTargetOffset',
    620: 'GetPathingTargetAngleOffset', 621: 'GetPathingTargetSpeed',
    622: 'GetPathingTargetSpeedAngle', 623: 'GetMovementSpeed',
    624: 'GetInContainer', 625: 'IsLocationLoaded',
    626: 'IsLocAliasLoaded', 627: 'IsDualCasting',
    629: 'GetVMQuestVariable', 630: 'GetVMScriptVariable',
    631: 'IsEnteringInteractionQuick', 632: 'IsCasting',
    633: 'GetFlyingState', 635: 'IsInFavorState',
    636: 'HasTwoHandedWeaponEquipped', 637: 'IsExitingInstant',
    638: 'IsInFriendStateWithPlayer', 639: 'GetWithinDistance',
    640: 'GetActorValuePercent', 641: 'IsUnique',
    642: 'GetLastBumpDirection', 644: 'IsInFurnitureState',
    645: 'GetIsInjured', 646: 'GetIsCrashLandRequest',
    647: 'GetIsHastyLandRequest', 650: 'IsLinkedTo',
    651: 'GetKeywordDataForCurrentLocation', 652: 'GetInSharedCrimeFaction',
    654: 'GetBribeSuccess', 655: 'GetIntimidateSuccess',
    656: 'GetArrestedState', 657: 'GetArrestingActor',
    659: 'EPTemperingItemIsEnchanted', 660: 'EPTemperingItemHasKeyword',
    664: 'GetReplacedItemType', 672: 'IsAttacking',
    673: 'IsPowerAttacking', 674: 'IsLastHostileActor',
    675: 'GetGraphVariableInt', 676: 'GetCurrentShoutVariation',
    678: 'ShouldAttackKill', 680: 'GetActivatorHeight',
    681: 'EPMagic_IsAdvanceSkill', 682: 'WornHasKeyword',
    683: 'GetPathingCurrentSpeed', 684: 'GetPathingCurrentSpeedAngle',
    691: 'EPModSkillUsage_AdvanceObjectHasKeyword',
    692: 'EPModSkillUsage_IsAdvanceAction', 693: 'EPMagic_SpellHasKeyword',
    694: 'GetNoBleedoutRecovery', 696: 'EPMagic_SpellHasSkill',
    697: 'IsAttackType', 698: 'IsAllowedToFly',
    699: 'HasMagicEffectKeyword', 700: 'IsCommandedActor',
    701: 'IsStaggered', 702: 'IsRecoiling',
    703: 'IsExitingInteractionQuick', 704: 'IsPathing',
    705: 'GetShouldHelp', 706: 'HasBoundWeaponEquipped',
    707: 'GetCombatTargetHasKeyword', 709: 'GetCombatGroupMemberCount',
    710: 'IsIgnoringCombat', 711: 'GetLightLevel',
    713: 'SpellHasCastingPerk', 714: 'IsBeingRidden', 715: 'IsUndead',
    716: 'GetRealHoursPassed', 718: 'IsUnlockedDoor',
    719: 'IsHostileToActor', 720: 'GetTargetHeight', 721: 'IsPoison',
    722: 'WornApparelHasKeywordCount', 723: 'GetItemHealthPercent',
    724: 'EffectWasDualCast', 725: 'GetKnockedStateEnum',
    726: 'DoesNotExist', 730: 'IsOnFlyingMount', 731: 'CanFlyHere',
    732: 'IsFlyingMountPatrolQueud', 733: 'IsFlyingMountFastTravelling',
    734: 'IsOverEncumbered', 735: 'GetActorWarmth',
}

# Run-on types
_CTDA_RUNON_NAMES = {
    0: 'Subject', 1: 'Target', 2: 'Reference', 3: 'CombatTarget',
    4: 'LinkedRef', 5: 'QuestAlias', 6: 'PackageData', 7: 'EventData',
}


def ctda_func_name(idx: int) -> str:
    """The TES5 condition function name for a CTDA function index."""
    return _CTDA_FUNC_NAMES.get(idx, f'Func{idx}')


# ---------------------------------------------------------------------------
# Dialog / Quest / Condition decoders
# ---------------------------------------------------------------------------

def _dec_ctda(data: bytes) -> list:
    """Decode a 32-byte CTDA (condition data)."""
    if len(data) < 32:
        return [f'CTDA.hex={data.hex()}']
    type_byte = data[0]
    comp_type = (type_byte >> 5) & 0x07
    is_or = bool(type_byte & 0x01)
    use_global = bool(type_byte & 0x04)
    comp_names = {0: '==', 1: '!=', 2: '>', 3: '>=', 4: '<', 5: '<='}
    comp_str = comp_names.get(comp_type, f'?{comp_type}')

    comp_val = struct.unpack_from('<f', data, 4)[0]
    func_idx = struct.unpack_from('<H', data, 8)[0]
    _pad = struct.unpack_from('<H', data, 10)[0]
    param1 = struct.unpack_from('<I', data, 12)[0]
    param2 = struct.unpack_from('<I', data, 16)[0]
    run_on = struct.unpack_from('<I', data, 20)[0]
    ref = struct.unpack_from('<I', data, 24)[0]
    _unk = struct.unpack_from('<I', data, 28)[0]

    func_name = _CTDA_FUNC_NAMES.get(func_idx, f'Func{func_idx}')
    run_on_str = _CTDA_RUNON_NAMES.get(run_on, f'RunOn{run_on}')

    parts = [f'CTDA: {func_name}({param1:08X}']
    if param2:
        parts[0] += f',{param2:08X}'
    parts[0] += f') {comp_str} {comp_val}'

    flags = []
    if is_or:
        flags.append('OR')
    if use_global:
        flags.append('UseGlobal')
    if run_on != 0:
        flags.append(f'[{run_on_str}]')
    if ref:
        flags.append(f'ref={ref:08X}')
    if flags:
        parts[0] += ' ' + ' '.join(flags)

    return parts


def _dec_dial_data(data: bytes) -> list:
    """DIAL DATA: TopicFlags(U8) + Category(U8) + Subtype(U16)."""
    if len(data) < 4:
        return [f'DATA.hex={data.hex()}']
    flags, cat, subtype = struct.unpack_from('<BBH', data, 0)
    cat_names = {0: 'Topic', 1: 'Favor', 2: 'Scene', 3: 'Combat',
                 4: 'Favors', 5: 'Detection', 6: 'Service', 7: 'Misc'}
    return [
        f'DATA.TopicFlags=0x{flags:02X}', f'DATA.Category={cat} ({cat_names.get(cat, "?")})',
        f'DATA.Subtype={subtype}',
    ]


def _dec_dial_snam(data: bytes) -> list:
    """DIAL SNAM: 4-char subtype code."""
    if len(data) < 4:
        return [f'SNAM.hex={data.hex()}']
    code = data[:4].decode('ascii', errors='replace')
    return [f'SNAM={code} ({data.hex().upper()})']


def _dec_dial_pnam(data: bytes) -> list:
    """DIAL PNAM: float priority."""
    if len(data) < 4:
        return [f'PNAM.hex={data.hex()}']
    val = struct.unpack_from('<f', data, 0)[0]
    return [f'PNAM.Priority={val}']


def _dec_info_enam(data: bytes) -> list:
    """INFO ENAM: Flags(U16) + ResetHours(U16)."""
    if len(data) < 4:
        return [f'ENAM.hex={data.hex()}']
    flags, reset = struct.unpack_from('<HH', data, 0)
    flag_names = []
    if flags & 0x01: flag_names.append('Goodbye')
    if flags & 0x02: flag_names.append('Random')
    if flags & 0x04: flag_names.append('SayOnce')
    if flags & 0x10: flag_names.append('InfoRefusal')
    if flags & 0x20: flag_names.append('RandomEnd')
    flag_str = '|'.join(flag_names) if flag_names else 'None'
    return [f'ENAM.Flags=0x{flags:04X} ({flag_str})', f'ENAM.ResetHours={reset}']


def _dec_info_trdt(data: bytes) -> list:
    """INFO TRDT: 24-byte response data."""
    if len(data) < 24:
        return [f'TRDT.hex={data.hex()}']
    emo_type, emo_val = struct.unpack_from('<II', data, 0)
    resp_num = data[12]
    sound_fid = struct.unpack_from('<I', data, 16)[0]
    resp_flags = data[20]
    emo_names = {0: 'Neutral', 1: 'Anger', 2: 'Disgust', 3: 'Fear',
                 4: 'Sad', 5: 'Happy', 6: 'Surprise', 7: 'Puzzled'}
    return [
        f'TRDT.Emotion={emo_type} ({emo_names.get(emo_type, "?")})',
        f'TRDT.EmotionValue={emo_val}', f'TRDT.ResponseNum={resp_num}',
        f'TRDT.Sound={sound_fid:08X}', f'TRDT.Flags=0x{resp_flags:02X}',
    ]


def _dec_qust_dnam(data: bytes) -> list:
    """QUST DNAM: 12 bytes — Flags(U16) + Priority(U8) + FormVer(U8) + Unknown(4B) + Type(U32)."""
    if len(data) < 12:
        return [f'DNAM.hex={data.hex()}']
    flags, priority, formver = struct.unpack_from('<HBB', data, 0)
    qtype = struct.unpack_from('<I', data, 8)[0]
    flag_names = []
    if flags & 0x0001: flag_names.append('StartGameEnabled')
    if flags & 0x0002: flag_names.append('Completed')
    if flags & 0x0004: flag_names.append('AddIdleToHello')
    if flags & 0x0008: flag_names.append('AllowRepeatStages')
    if flags & 0x0010: flag_names.append('StartsEnabled')
    if flags & 0x0020: flag_names.append('DisplayedInHUD')
    if flags & 0x0040: flag_names.append('Failed')
    if flags & 0x0100: flag_names.append('RunOnce')
    if flags & 0x0200: flag_names.append('ExcludeFromDialogExport')
    if flags & 0x0400: flag_names.append('WarnOnAliasFillFailure')
    if flags & 0x8000: flag_names.append('HasDialogueData')
    flag_str = '|'.join(flag_names) if flag_names else 'None'
    type_names = {0: 'None', 1: 'MainQuest', 2: 'MagesGuild', 3: 'ThievesGuild',
                  4: 'DarkBrotherhood', 5: 'CompanionsQuest', 6: 'Miscellaneous',
                  7: 'Daedric', 8: 'SideQuest', 9: 'CivilWar', 10: 'DLC01Vampire',
                  11: 'DLC02Dragonborn'}
    return [
        f'DNAM.Flags=0x{flags:04X} ({flag_str})',
        f'DNAM.Priority={priority}', f'DNAM.FormVer={formver}',
        f'DNAM.Type={qtype} ({type_names.get(qtype, "?")})',
    ]


def _dec_qust_indx(data: bytes) -> list:
    """QUST INDX: StageIndex(U16) + StageFlags(U8) + Unknown(U8)."""
    if len(data) < 4:
        return [f'INDX.hex={data.hex()}']
    idx, flags, unk = struct.unpack_from('<HBB', data, 0)
    flag_names = []
    if flags & 0x01: flag_names.append('Unknown')
    if flags & 0x02: flag_names.append('StartUpStage')
    if flags & 0x04: flag_names.append('ShutDownStage')
    if flags & 0x08: flag_names.append('KeepInstanceDataFromHereOn')
    flag_str = '|'.join(flag_names) if flag_names else 'None'
    return [f'INDX.StageIndex={idx}', f'INDX.Flags=0x{flags:02X} ({flag_str})']


def _dec_dlbr_dnam(data: bytes) -> list:
    """DLBR DNAM: branch flags."""
    if len(data) < 4:
        return [f'DNAM.hex={data.hex()}']
    val = struct.unpack_from('<I', data, 0)[0]
    names = {0: 'Normal', 1: 'TopLevel', 2: 'Blocking', 4: 'Exclusive'}
    return [f'DNAM={val} ({names.get(val, "?")})']


def _dec_dlbr_tnam(data: bytes) -> list:
    """DLBR TNAM: category (0=Player, 1=Command)."""
    if len(data) < 4:
        return [f'TNAM.hex={data.hex()}']
    val = struct.unpack_from('<I', data, 0)[0]
    names = {0: 'Player', 1: 'Command'}
    return [f'TNAM={val} ({names.get(val, "?")})']


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

    # Magic effects
    ('MGEF', 'DATA'): _dec_mgef_data,

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

    # Dialog topics (DIAL)
    ('DIAL', 'DATA'): _dec_dial_data,
    ('DIAL', 'SNAM'): _dec_dial_snam,
    ('DIAL', 'PNAM'): _dec_dial_pnam,

    # Dialog responses (INFO)
    ('INFO', 'ENAM'): _dec_info_enam,
    ('INFO', 'TRDT'): _dec_info_trdt,

    # Quests (QUST)
    ('QUST', 'DNAM'): _dec_qust_dnam,
    ('QUST', 'INDX'): _dec_qust_indx,

    # Dialog branches (DLBR)
    ('DLBR', 'DNAM'): _dec_dlbr_dnam,
    ('DLBR', 'TNAM'): _dec_dlbr_tnam,
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

    # 1b. CTDA — universal condition decoder (applies to all record types)
    if sig == 'CTDA':
        return _dec_ctda(data)

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
                    # Magnitude, AREA, DURATION — see _dec_magic_effect.
                    mag  = struct.unpack_from('<f', efit,  0)[0]
                    area = struct.unpack_from('<I', efit,  4)[0]
                    dur  = struct.unpack_from('<I', efit,  8)[0]
                    lines += [f'{prefix}.Magnitude={mag:.4f}',
                               f'{prefix}.Area={area}', f'{prefix}.Duration={dur}']
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
    parser.add_argument('--formid', nargs='+', metavar='FID',
                        help='Print the record(s) with these FormIDs (hex, load-order '
                             'index ignored) to stdout and exit')
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

    if args.formid:
        # Match on the low 24 bits so crash-log FormIDs (load-order prefixed)
        # can be pasted directly.
        wanted = {int(f, 16) & 0x00FFFFFF for f in args.formid}
        found = 0
        for rec in all_records:
            if rec.form_id & 0x00FFFFFF in wanted:
                print(format_record(rec, is_localized))
                print()
                found += 1
        if not found:
            print('No records matched.')
        return

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

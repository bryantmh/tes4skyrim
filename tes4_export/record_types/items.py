"""
Simple item/object record types: STAT, ACTI, MISC, KEYM, DOOR, FLOR, FURN,
GRAS, TREE, LIGH, SLGM, ANIO, SBSP.
"""

import struct

from ..tes4_reader import Record, get_subrecord
from .common import (
    emit_float,
    emit_formid,
    emit_icon,
    emit_model,
    emit_script,
    emit_string,
    emit_u8,
)


def export_STAT(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_model(lines, "Model", rec)
    return lines


def export_ACTI(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_script(lines, rec)
    emit_formid(lines, "SNAM", get_subrecord(rec, "SNAM"))
    return lines


def export_MISC(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_icon(lines, "ICON", rec)
    emit_script(lines, rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 8:
        lines.append(f"DATA.Value={struct.unpack_from('<I', data.data, 0)[0]}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', data.data, 4)[0]}")
    return lines


def export_KEYM(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_icon(lines, "ICON", rec)
    emit_script(lines, rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 8:
        lines.append(f"DATA.Value={struct.unpack_from('<I', data.data, 0)[0]}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', data.data, 4)[0]}")
    return lines


def export_DOOR(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_script(lines, rec)
    emit_formid(lines, "SNAM.Open", get_subrecord(rec, "SNAM"))
    emit_formid(lines, "ANAM.Close", get_subrecord(rec, "ANAM"))
    emit_formid(lines, "BNAM.Loop", get_subrecord(rec, "BNAM"))
    fnam = get_subrecord(rec, "FNAM")
    emit_u8(lines, "FNAM.Flags", fnam)
    # TNAM - random teleport destinations
    tnams = [s for s in rec.subrecords if s.type == "TNAM"]
    if tnams:
        lines.append(f"TeleportCount={len(tnams)}")
        for i, t in enumerate(tnams):
            emit_formid(lines, f"TNAM[{i}]", t)
    return lines


def export_FLOR(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_script(lines, rec)
    emit_formid(lines, "PFIG", get_subrecord(rec, "PFIG"))
    emit_formid(lines, "PFPC", get_subrecord(rec, "PFPC"))  # actually u32
    return lines


def export_FURN(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_script(lines, rec)
    mnam = get_subrecord(rec, "MNAM")
    if mnam and len(mnam.data) >= 4:
        flags = struct.unpack_from("<I", mnam.data, 0)[0]
        lines.append(f"MNAM.Flags={flags}")
    return lines


def export_GRAS(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_model(lines, "Model", rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 32:
        d = data.data
        lines.append(f"DATA.Density={d[0]}")
        lines.append(f"DATA.MinSlope={d[1]}")
        lines.append(f"DATA.MaxSlope={d[2]}")
        lines.append(f"DATA.UnitFromWaterAmount={struct.unpack_from('<H', d, 4)[0]}")
        lines.append(f"DATA.UnitFromWaterType={struct.unpack_from('<I', d, 8)[0]}")
        lines.append(f"DATA.PositionRange={struct.unpack_from('<f', d, 12)[0]}")
        lines.append(f"DATA.HeightRange={struct.unpack_from('<f', d, 16)[0]}")
        lines.append(f"DATA.ColorRange={struct.unpack_from('<f', d, 20)[0]}")
        lines.append(f"DATA.WavePeriod={struct.unpack_from('<f', d, 24)[0]}")
        lines.append(f"DATA.Flags={d[28]}")
    return lines


def export_TREE(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_model(lines, "Model", rec)
    emit_icon(lines, "ICON", rec)
    # Speed tree seeds
    snam = get_subrecord(rec, "SNAM")
    if snam:
        count = len(snam.data) // 4
        lines.append(f"SeedCount={count}")
        for i in range(count):
            lines.append(f"Seed[{i}]={struct.unpack_from('<I', snam.data, i*4)[0]}")
    cnam = get_subrecord(rec, "CNAM")
    if cnam and len(cnam.data) >= 36:
        d = cnam.data
        lines.append(f"CNAM.CurvatureX={struct.unpack_from('<f', d, 0)[0]}")
        lines.append(f"CNAM.CurvatureY={struct.unpack_from('<f', d, 4)[0]}")
        lines.append(f"CNAM.CurvatureZ={struct.unpack_from('<f', d, 8)[0]}")
        lines.append(f"CNAM.MinAngle={struct.unpack_from('<f', d, 12)[0]}")
        lines.append(f"CNAM.MaxAngle={struct.unpack_from('<f', d, 16)[0]}")
        lines.append(f"CNAM.BranchDim={struct.unpack_from('<f', d, 20)[0]}")
        lines.append(f"CNAM.LeafDim={struct.unpack_from('<f', d, 24)[0]}")
        lines.append(f"CNAM.ShadowRadius={struct.unpack_from('<i', d, 28)[0]}")
        lines.append(f"CNAM.RockSpeed={struct.unpack_from('<f', d, 32)[0]}")
    bnam = get_subrecord(rec, "BNAM")
    if bnam and len(bnam.data) >= 4:
        lines.append(f"BNAM.BillboardWidth={struct.unpack_from('<f', bnam.data, 0)[0]}")
        if len(bnam.data) >= 8:
            lines.append(f"BNAM.BillboardHeight={struct.unpack_from('<f', bnam.data, 4)[0]}")
    return lines


def export_LIGH(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_icon(lines, "ICON", rec)
    emit_script(lines, rec)
    data = get_subrecord(rec, "DATA")
    # DATA is 32 bytes, or 24 in an older variant that omits Value/Weight
    # (Time, Radius, Color, Flags, Falloff, FOV are always present).
    if data and len(data.data) >= 24:
        d = data.data
        lines.append(f"DATA.Time={struct.unpack_from('<i', d, 0)[0]}")
        lines.append(f"DATA.Radius={struct.unpack_from('<I', d, 4)[0]}")
        lines.append(f"DATA.Color.R={d[8]}")
        lines.append(f"DATA.Color.G={d[9]}")
        lines.append(f"DATA.Color.B={d[10]}")
        lines.append(f"DATA.Flags={struct.unpack_from('<I', d, 12)[0]}")
        lines.append(f"DATA.FalloffExponent={struct.unpack_from('<f', d, 16)[0]}")
        lines.append(f"DATA.FOV={struct.unpack_from('<f', d, 20)[0]}")
        if len(d) >= 32:
            lines.append(f"DATA.Value={struct.unpack_from('<I', d, 24)[0]}")
            lines.append(f"DATA.Weight={struct.unpack_from('<f', d, 28)[0]}")
    emit_float(lines, "FNAM.Fade", get_subrecord(rec, "FNAM"))
    emit_formid(lines, "SNAM.Sound", get_subrecord(rec, "SNAM"))
    return lines


def export_SLGM(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_icon(lines, "ICON", rec)
    emit_script(lines, rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 8:
        lines.append(f"DATA.Value={struct.unpack_from('<I', data.data, 0)[0]}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', data.data, 4)[0]}")
    soul = get_subrecord(rec, "SOUL")
    emit_u8(lines, "SOUL", soul)
    slcp = get_subrecord(rec, "SLCP")
    emit_u8(lines, "SLCP.Capacity", slcp)
    return lines


def export_ANIO(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_model(lines, "Model", rec)
    emit_formid(lines, "DATA.Idle", get_subrecord(rec, "DATA"))
    return lines


def export_SBSP(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    dnam = get_subrecord(rec, "DNAM")
    if dnam and len(dnam.data) >= 12:
        lines.append(f"DNAM.X={struct.unpack_from('<f', dnam.data, 0)[0]}")
        lines.append(f"DNAM.Y={struct.unpack_from('<f', dnam.data, 4)[0]}")
        lines.append(f"DNAM.Z={struct.unpack_from('<f', dnam.data, 8)[0]}")
    return lines

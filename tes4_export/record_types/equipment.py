"""
Equipment and magic record types: WEAP, ARMO, CLOT, AMMO, BOOK,
ENCH, SPEL, ALCH, INGR, MGEF, SGST, APPA.

Pure TES4 data dump - no transformations.
"""

import struct

from ..tes4_reader import Record, get_all_subrecords, get_formid_str, get_string, get_subrecord
from .common import (
    emit_effects,
    emit_enchantment,
    emit_float,
    emit_icon,
    emit_model,
    emit_script,
    emit_string,
    escape_value,
)


def export_WEAP(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_icon(lines, "ICON", rec)
    emit_script(lines, rec)
    emit_enchantment(lines, rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 30:
        d = data.data
        lines.append(f"DATA.Type={struct.unpack_from('<I', d, 0)[0]}")
        lines.append(f"DATA.Speed={struct.unpack_from('<f', d, 4)[0]}")
        lines.append(f"DATA.Reach={struct.unpack_from('<f', d, 8)[0]}")
        lines.append(f"DATA.Flags={struct.unpack_from('<I', d, 12)[0]}")
        lines.append(f"DATA.Value={struct.unpack_from('<I', d, 16)[0]}")
        lines.append(f"DATA.Health={struct.unpack_from('<I', d, 20)[0]}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', d, 24)[0]}")
        lines.append(f"DATA.Damage={struct.unpack_from('<H', d, 28)[0]}")
    return lines


def export_ARMO(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_script(lines, rec)
    emit_enchantment(lines, rec)
    bmdt = get_subrecord(rec, "BMDT")
    if bmdt and len(bmdt.data) >= 4:
        lines.append(f"BMDT.BipedFlags={struct.unpack_from('<H', bmdt.data, 0)[0]}")
        lines.append(f"BMDT.GeneralFlags={bmdt.data[2]}")
    # Male biped model (MODL/MODB/MODT)
    emit_model(lines, "Male.BipedModel", rec, "MODL", "MODB", "MODT")
    # Male world model (MOD2/MO2B/MO2T)
    modl2 = get_subrecord(rec, "MOD2")
    if modl2:
        lines.append(f"Male.WorldModel.MODL={escape_value(get_string(modl2))}")
        mo2b = get_subrecord(rec, "MO2B")
        if mo2b and len(mo2b.data) >= 4:
            lines.append(f"Male.WorldModel.MODB={struct.unpack_from('<f', mo2b.data, 0)[0]}")
    emit_icon(lines, "Male.Icon", rec, "ICON")
    # Female biped model (MOD3/MO3B/MO3T)
    modl3 = get_subrecord(rec, "MOD3")
    if modl3:
        lines.append(f"Female.BipedModel.MODL={escape_value(get_string(modl3))}")
        mo3b = get_subrecord(rec, "MO3B")
        if mo3b and len(mo3b.data) >= 4:
            lines.append(f"Female.BipedModel.MODB={struct.unpack_from('<f', mo3b.data, 0)[0]}")
    # Female world model (MOD4/MO4B/MO4T)
    modl4 = get_subrecord(rec, "MOD4")
    if modl4:
        lines.append(f"Female.WorldModel.MODL={escape_value(get_string(modl4))}")
    emit_icon(lines, "Female.Icon", rec, "ICO2")
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 14:
        d = data.data
        lines.append(f"DATA.ArmorRating={struct.unpack_from('<H', d, 0)[0]}")
        lines.append(f"DATA.Value={struct.unpack_from('<I', d, 2)[0]}")
        lines.append(f"DATA.Health={struct.unpack_from('<I', d, 6)[0]}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', d, 10)[0]}")
    return lines


def export_CLOT(rec: Record) -> list:
    """Clothing - same structure as ARMO but no armor rating."""
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_script(lines, rec)
    emit_enchantment(lines, rec)
    bmdt = get_subrecord(rec, "BMDT")
    if bmdt and len(bmdt.data) >= 4:
        lines.append(f"BMDT.BipedFlags={struct.unpack_from('<H', bmdt.data, 0)[0]}")
        lines.append(f"BMDT.GeneralFlags={bmdt.data[2]}")
    emit_model(lines, "Male.BipedModel", rec, "MODL", "MODB", "MODT")
    modl2 = get_subrecord(rec, "MOD2")
    if modl2:
        lines.append(f"Male.WorldModel.MODL={escape_value(get_string(modl2))}")
    emit_icon(lines, "Male.Icon", rec, "ICON")
    modl3 = get_subrecord(rec, "MOD3")
    if modl3:
        lines.append(f"Female.BipedModel.MODL={escape_value(get_string(modl3))}")
    modl4 = get_subrecord(rec, "MOD4")
    if modl4:
        lines.append(f"Female.WorldModel.MODL={escape_value(get_string(modl4))}")
    emit_icon(lines, "Female.Icon", rec, "ICO2")
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 8:
        d = data.data
        lines.append(f"DATA.Value={struct.unpack_from('<I', d, 0)[0]}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', d, 4)[0]}")
    return lines


def export_AMMO(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_icon(lines, "ICON", rec)
    emit_enchantment(lines, rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 18:
        d = data.data
        lines.append(f"DATA.Speed={struct.unpack_from('<f', d, 0)[0]}")
        lines.append(f"DATA.Flags={d[4]}")
        lines.append(f"DATA.Value={struct.unpack_from('<I', d, 8)[0]}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', d, 12)[0]}")
        lines.append(f"DATA.Damage={struct.unpack_from('<H', d, 16)[0]}")
    return lines


def export_BOOK(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_icon(lines, "ICON", rec)
    emit_string(lines, "DESC", get_subrecord(rec, "DESC"))
    emit_script(lines, rec)
    emit_enchantment(lines, rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 10:
        d = data.data
        lines.append(f"DATA.Flags={d[0]}")
        lines.append(f"DATA.Teaches={d[1]}")
        lines.append(f"DATA.Value={struct.unpack_from('<I', d, 2)[0]}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', d, 6)[0]}")
    return lines


def export_ENCH(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    enit = get_subrecord(rec, "ENIT")
    if enit and len(enit.data) >= 16:
        d = enit.data
        lines.append(f"ENIT.Type={struct.unpack_from('<I', d, 0)[0]}")
        lines.append(f"ENIT.Charge={struct.unpack_from('<I', d, 4)[0]}")
        lines.append(f"ENIT.Cost={struct.unpack_from('<I', d, 8)[0]}")
        lines.append(f"ENIT.Flags={d[12]}")  # u8 + 3 unused
    emit_effects(lines, rec)
    return lines


def export_SPEL(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    spit = get_subrecord(rec, "SPIT")
    if spit and len(spit.data) >= 16:
        d = spit.data
        lines.append(f"SPIT.Type={struct.unpack_from('<I', d, 0)[0]}")
        lines.append(f"SPIT.Cost={struct.unpack_from('<I', d, 4)[0]}")
        lines.append(f"SPIT.Level={struct.unpack_from('<I', d, 8)[0]}")
        lines.append(f"SPIT.Flags={d[12]}")  # u8 + 3 unused
    emit_effects(lines, rec)
    return lines


def export_ALCH(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_icon(lines, "ICON", rec)
    emit_script(lines, rec)
    emit_float(lines, "DATA.Weight", get_subrecord(rec, "DATA"))
    enit = get_subrecord(rec, "ENIT")
    if enit and len(enit.data) >= 8:
        lines.append(f"ENIT.Value={struct.unpack_from('<i', enit.data, 0)[0]}")
        lines.append(f"ENIT.Flags={enit.data[4]}")
    emit_effects(lines, rec)
    return lines


def export_INGR(rec: Record) -> list:
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
    enit = get_subrecord(rec, "ENIT")
    if enit and len(enit.data) >= 4:
        lines.append(f"ENIT.Flags={struct.unpack_from('<I', enit.data, 0)[0]}")
    emit_effects(lines, rec)
    return lines


def export_MGEF(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_string(lines, "DESC", get_subrecord(rec, "DESC"))
    emit_icon(lines, "ICON", rec)
    emit_model(lines, "Model", rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 64:
        d = data.data
        lines.append(f"DATA.Flags={struct.unpack_from('<I', d, 0)[0]}")
        lines.append(f"DATA.BaseCost={struct.unpack_from('<f', d, 4)[0]}")
        lines.append(f"DATA.AssocItem={get_formid_str(struct.unpack_from('<I', d, 8)[0])}")
        lines.append(f"DATA.School={struct.unpack_from('<I', d, 12)[0]}")
        lines.append(f"DATA.ResistValue={struct.unpack_from('<I', d, 16)[0]}")
        lines.append(f"DATA.CounterEffectCount={struct.unpack_from('<H', d, 20)[0]}")
        lines.append(f"DATA.Light={get_formid_str(struct.unpack_from('<I', d, 24)[0])}")
        lines.append(f"DATA.ProjectileSpeed={struct.unpack_from('<f', d, 28)[0]}")
        lines.append(f"DATA.EffectShader={get_formid_str(struct.unpack_from('<I', d, 32)[0])}")
        lines.append(f"DATA.EnchantEffect={get_formid_str(struct.unpack_from('<I', d, 36)[0])}")
        lines.append(f"DATA.CastingSound={get_formid_str(struct.unpack_from('<I', d, 40)[0])}")
        lines.append(f"DATA.BoltSound={get_formid_str(struct.unpack_from('<I', d, 44)[0])}")
        lines.append(f"DATA.HitSound={get_formid_str(struct.unpack_from('<I', d, 48)[0])}")
        lines.append(f"DATA.AreaSound={get_formid_str(struct.unpack_from('<I', d, 52)[0])}")
        lines.append(f"DATA.CEEnchantFactor={struct.unpack_from('<f', d, 56)[0]}")
        lines.append(f"DATA.CEBarterFactor={struct.unpack_from('<f', d, 60)[0]}")
    # Counter effects
    esce = get_all_subrecords(rec, "ESCE")
    if esce:
        lines.append(f"CounterEffects={len(esce)}")
        for i, e in enumerate(esce):
            code = e.data[:4].decode("ascii", errors="replace") if len(e.data) >= 4 else ""
            lines.append(f"ESCE[{i}]={code}")
    return lines


def export_SGST(rec: Record) -> list:
    """Sigil Stone."""
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_icon(lines, "ICON", rec)
    emit_script(lines, rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 12:
        d = data.data
        lines.append(f"DATA.Uses={d[0]}")
        lines.append(f"DATA.Value={struct.unpack_from('<I', d, 4)[0]}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', d, 8)[0]}")
    emit_effects(lines, rec)
    return lines


def export_APPA(rec: Record) -> list:
    """Alchemical Apparatus."""
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_icon(lines, "ICON", rec)
    emit_script(lines, rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 13:
        d = data.data
        lines.append(f"DATA.Type={d[0]}")
        lines.append(f"DATA.Value={struct.unpack_from('<I', d, 1)[0]}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', d, 5)[0]}")
        lines.append(f"DATA.Quality={struct.unpack_from('<f', d, 9)[0]}")
    return lines

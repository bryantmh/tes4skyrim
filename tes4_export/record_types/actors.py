"""
Actor-related record types: NPC_, CREA, CONT, FACT, RACE, CLAS, EYES, HAIR,
BSGN, SKIL, CSTY, IDLE.

Pure TES4 data dump - no transformations.
"""

import struct

from ..tes4_reader import Record, get_all_subrecords, get_formid_str, get_subrecord
from .common import (
    emit_conditions,
    emit_float,
    emit_formid,
    emit_icon,
    emit_model,
    emit_script,
    emit_string,
    emit_u8,
)


def _emit_items(lines: list, rec: Record):
    """Emit CNTO item entries."""
    cntos = get_all_subrecords(rec, "CNTO")
    lines.append(f"ItemCount={len(cntos)}")
    for i, cnto in enumerate(cntos):
        if len(cnto.data) >= 8:
            fid = struct.unpack_from("<I", cnto.data, 0)[0]
            count = struct.unpack_from("<i", cnto.data, 4)[0]
            lines.append(f"Item[{i}].FormID={get_formid_str(fid)}")
            lines.append(f"Item[{i}].Count={count}")


def _emit_factions(lines: list, rec: Record):
    """Emit SNAM faction membership entries (FormID + u8 rank + 3 unused)."""
    snams = get_all_subrecords(rec, "SNAM")
    lines.append(f"FactionCount={len(snams)}")
    for i, snam in enumerate(snams):
        if len(snam.data) >= 5:
            fid = struct.unpack_from("<I", snam.data, 0)[0]
            rank = struct.unpack_from("<b", snam.data, 4)[0]
            lines.append(f"Faction[{i}].FormID={get_formid_str(fid)}")
            lines.append(f"Faction[{i}].Rank={rank}")


def _emit_aidt(lines: list, rec: Record):
    """Emit AIDT AI data."""
    aidt = get_subrecord(rec, "AIDT")
    if aidt and len(aidt.data) >= 12:
        d = aidt.data
        lines.append(f"AIDT.Aggression={d[0]}")
        lines.append(f"AIDT.Confidence={d[1]}")
        lines.append(f"AIDT.EnergyLevel={d[2]}")
        lines.append(f"AIDT.Responsibility={d[3]}")
        lines.append(f"AIDT.Services={struct.unpack_from('<I', d, 4)[0]}")
        lines.append(f"AIDT.Teaches={d[8]}")
        lines.append(f"AIDT.MaxTraining={d[9]}")


def _emit_ai_packages(lines: list, rec: Record):
    """Emit PKID AI package references."""
    pkids = get_all_subrecords(rec, "PKID")
    lines.append(f"AIPackageCount={len(pkids)}")
    for i, pkid in enumerate(pkids):
        if len(pkid.data) >= 4:
            lines.append(f"AIPackage[{i}]={get_formid_str(struct.unpack_from('<I', pkid.data, 0)[0])}")


def _emit_spells(lines: list, rec: Record):
    """Emit SPLO spell references."""
    splos = get_all_subrecords(rec, "SPLO")
    if splos:
        lines.append(f"SpellCount={len(splos)}")
        for i, splo in enumerate(splos):
            if len(splo.data) >= 4:
                lines.append(f"Spell[{i}]={get_formid_str(struct.unpack_from('<I', splo.data, 0)[0])}")


def export_NPC_(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)

    # ACBS - base stats
    acbs = get_subrecord(rec, "ACBS")
    if acbs and len(acbs.data) >= 16:
        d = acbs.data
        lines.append(f"ACBS.Flags={struct.unpack_from('<I', d, 0)[0]}")
        lines.append(f"ACBS.SpellPoints={struct.unpack_from('<H', d, 4)[0]}")
        lines.append(f"ACBS.Fatigue={struct.unpack_from('<H', d, 6)[0]}")
        lines.append(f"ACBS.BarterGold={struct.unpack_from('<H', d, 8)[0]}")
        lines.append(f"ACBS.Level={struct.unpack_from('<h', d, 10)[0]}")
        lines.append(f"ACBS.CalcMin={struct.unpack_from('<H', d, 12)[0]}")
        lines.append(f"ACBS.CalcMax={struct.unpack_from('<H', d, 14)[0]}")

    _emit_factions(lines, rec)
    emit_formid(lines, "INAM.DeathItem", get_subrecord(rec, "INAM"))
    emit_formid(lines, "RNAM.Race", get_subrecord(rec, "RNAM"))
    _emit_spells(lines, rec)
    emit_script(lines, rec)
    _emit_items(lines, rec)
    _emit_aidt(lines, rec)
    _emit_ai_packages(lines, rec)
    emit_formid(lines, "CNAM.Class", get_subrecord(rec, "CNAM"))
    emit_formid(lines, "HNAM.Hair", get_subrecord(rec, "HNAM"))

    # LNAM - Hair Length
    emit_float(lines, "LNAM.HairLength", get_subrecord(rec, "LNAM"))

    # ENAM - Eyes
    emit_formid(lines, "ENAM.Eyes", get_subrecord(rec, "ENAM"))

    # HCLR - Hair Color
    hclr = get_subrecord(rec, "HCLR")
    if hclr and len(hclr.data) >= 4:
        lines.append(f"HCLR.R={hclr.data[0]}")
        lines.append(f"HCLR.G={hclr.data[1]}")
        lines.append(f"HCLR.B={hclr.data[2]}")

    emit_formid(lines, "ZNAM.CombatStyle", get_subrecord(rec, "ZNAM"))

    # FGGS, FGGA, FGTS - FaceGen data (raw bytes as hex for morph mapping)
    fggs = get_subrecord(rec, "FGGS")
    if fggs:
        lines.append(f"FGGS={fggs.data.hex()}")
    fgga = get_subrecord(rec, "FGGA")
    if fgga:
        lines.append(f"FGGA={fgga.data.hex()}")
    fgts = get_subrecord(rec, "FGTS")
    if fgts:
        lines.append(f"FGTS={fgts.data.hex()}")

    # DATA - 33 bytes: 21 skills + health(u32) + 8 attributes
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 33:
        d = data.data
        skill_names = [
            "Armorer", "Athletics", "Blade", "Block", "Blunt",
            "HandToHand", "HeavyArmor", "Alchemy", "Alteration",
            "Conjuration", "Destruction", "Illusion", "Mysticism",
            "Restoration", "Acrobatics", "LightArmor", "Marksman",
            "Mercantile", "Security", "Sneak", "Speechcraft"
        ]
        for i, name in enumerate(skill_names):
            lines.append(f"DATA.{name}={d[i]}")
        lines.append(f"DATA.Health={struct.unpack_from('<I', d, 21)[0]}")
        attr_names = ["Strength", "Intelligence", "Willpower", "Agility",
                      "Speed", "Endurance", "Personality", "Luck"]
        for i, name in enumerate(attr_names):
            lines.append(f"DATA.{name}={d[25 + i]}")

    return lines


def export_CREA(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    _emit_items(lines, rec)
    _emit_spells(lines, rec)

    # ACBS
    acbs = get_subrecord(rec, "ACBS")
    if acbs and len(acbs.data) >= 16:
        d = acbs.data
        lines.append(f"ACBS.Flags={struct.unpack_from('<I', d, 0)[0]}")
        lines.append(f"ACBS.SpellPoints={struct.unpack_from('<H', d, 4)[0]}")
        lines.append(f"ACBS.Fatigue={struct.unpack_from('<H', d, 6)[0]}")
        lines.append(f"ACBS.BarterGold={struct.unpack_from('<H', d, 8)[0]}")
        lines.append(f"ACBS.Level={struct.unpack_from('<h', d, 10)[0]}")
        lines.append(f"ACBS.CalcMin={struct.unpack_from('<H', d, 12)[0]}")
        lines.append(f"ACBS.CalcMax={struct.unpack_from('<H', d, 14)[0]}")

    _emit_factions(lines, rec)
    emit_formid(lines, "INAM.DeathItem", get_subrecord(rec, "INAM"))
    emit_script(lines, rec)
    _emit_aidt(lines, rec)
    _emit_ai_packages(lines, rec)

    # DATA - Creature stats (20 bytes)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 20:
        d = data.data
        lines.append(f"DATA.Type={d[0]}")
        lines.append(f"DATA.CombatSkill={d[1]}")
        lines.append(f"DATA.MagicSkill={d[2]}")
        lines.append(f"DATA.StealthSkill={d[3]}")
        lines.append(f"DATA.Soul={struct.unpack_from('<H', d, 4)[0]}")
        lines.append(f"DATA.Health={struct.unpack_from('<H', d, 6)[0]}")
        lines.append(f"DATA.AttackDamage={struct.unpack_from('<H', d, 10)[0]}")
        lines.append(f"DATA.Strength={d[12]}")
        lines.append(f"DATA.Intelligence={d[13]}")
        lines.append(f"DATA.Willpower={d[14]}")
        lines.append(f"DATA.Agility={d[15]}")
        lines.append(f"DATA.Speed={d[16]}")
        lines.append(f"DATA.Endurance={d[17]}")
        lines.append(f"DATA.Personality={d[18]}")
        lines.append(f"DATA.Luck={d[19]}")

    emit_u8(lines, "RNAM.AttackReach", get_subrecord(rec, "RNAM"))
    emit_formid(lines, "ZNAM.CombatStyle", get_subrecord(rec, "ZNAM"))
    emit_float(lines, "TNAM.TurningSpeed", get_subrecord(rec, "TNAM"))
    emit_float(lines, "BNAM.BaseScale", get_subrecord(rec, "BNAM"))
    emit_float(lines, "WNAM.FootWeight", get_subrecord(rec, "WNAM"))
    emit_formid(lines, "CSCR.InheritSound", get_subrecord(rec, "CSCR"))

    # Creature models
    nift = get_subrecord(rec, "NIFT")
    if nift:
        lines.append(f"NIFT.Size={len(nift.data)}")

    # Sound entries (CSDT/CSDI/CSDC chains)
    csdts = get_all_subrecords(rec, "CSDT")
    csdis = get_all_subrecords(rec, "CSDI")
    if csdts:
        lines.append(f"SoundTypeCount={len(csdts)}")
        for i, csdt in enumerate(csdts):
            if len(csdt.data) >= 4:
                lines.append(f"SoundType[{i}].Type={struct.unpack_from('<I', csdt.data, 0)[0]}")
            if i < len(csdis) and len(csdis[i].data) >= 4:
                lines.append(f"SoundType[{i}].Sound={get_formid_str(struct.unpack_from('<I', csdis[i].data, 0)[0])}")

    return lines


def export_CONT(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_script(lines, rec)
    _emit_items(lines, rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 5:
        lines.append(f"DATA.Flags={data.data[0]}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', data.data, 1)[0]}")
    emit_formid(lines, "SNAM.OpenSound", get_subrecord(rec, "SNAM"))
    emit_formid(lines, "QNAM.CloseSound", get_subrecord(rec, "QNAM"))
    return lines


def export_FACT(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))

    # XNAM - inter-faction relations
    xnams = get_all_subrecords(rec, "XNAM")
    if xnams:
        lines.append(f"RelationCount={len(xnams)}")
        for i, xnam in enumerate(xnams):
            if len(xnam.data) >= 8:
                fid = struct.unpack_from("<I", xnam.data, 0)[0]
                disp = struct.unpack_from("<i", xnam.data, 4)[0]
                lines.append(f"Relation[{i}].Faction={get_formid_str(fid)}")
                lines.append(f"Relation[{i}].Disposition={disp}")

    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 4:
        lines.append(f"DATA.Flags={struct.unpack_from('<I', data.data, 0)[0]}")

    # CNAM - Crime Gold Multiplier
    emit_float(lines, "CNAM.CrimeGold", get_subrecord(rec, "CNAM"))

    # Ranks (RNAM subrecords)
    rnams = get_all_subrecords(rec, "RNAM")
    if rnams:
        lines.append(f"RankCount={len(rnams)}")
        for i, rnam in enumerate(rnams):
            if len(rnam.data) >= 4:
                lines.append(f"Rank[{i}].Index={struct.unpack_from('<I', rnam.data, 0)[0]}")

    return lines


def export_RACE(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_string(lines, "DESC", get_subrecord(rec, "DESC"))
    _emit_spells(lines, rec)

    # XNAM - faction relations
    xnams = get_all_subrecords(rec, "XNAM")
    if xnams:
        lines.append(f"RelationCount={len(xnams)}")
        for i, xnam in enumerate(xnams):
            if len(xnam.data) >= 8:
                fid = struct.unpack_from("<I", xnam.data, 0)[0]
                disp = struct.unpack_from("<i", xnam.data, 4)[0]
                lines.append(f"Relation[{i}].Faction={get_formid_str(fid)}")
                lines.append(f"Relation[{i}].Disposition={disp}")

    # DATA - Race stats
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 36:
        d = data.data
        # 8 skill boosts (2 bytes each: skill + bonus)
        for i in range(7):
            lines.append(f"DATA.SkillBoost[{i}].Skill={d[i*2]}")
            lines.append(f"DATA.SkillBoost[{i}].Bonus={d[i*2+1]}")
        lines.append(f"DATA.MaleHeight={struct.unpack_from('<f', d, 16)[0]}")
        lines.append(f"DATA.FemaleHeight={struct.unpack_from('<f', d, 20)[0]}")
        lines.append(f"DATA.MaleWeight={struct.unpack_from('<f', d, 24)[0]}")
        lines.append(f"DATA.FemaleWeight={struct.unpack_from('<f', d, 28)[0]}")
        lines.append(f"DATA.Flags={struct.unpack_from('<I', d, 32)[0]}")

    # VNAM - Voices (male/female)
    vnam = get_subrecord(rec, "VNAM")
    if vnam and len(vnam.data) >= 8:
        lines.append(f"VNAM.MaleVoice={get_formid_str(struct.unpack_from('<I', vnam.data, 0)[0])}")
        lines.append(f"VNAM.FemaleVoice={get_formid_str(struct.unpack_from('<I', vnam.data, 4)[0])}")

    # DNAM - Default Hair
    dnam = get_subrecord(rec, "DNAM")
    if dnam and len(dnam.data) >= 8:
        lines.append(f"DNAM.MaleHair={get_formid_str(struct.unpack_from('<I', dnam.data, 0)[0])}")
        lines.append(f"DNAM.FemaleHair={get_formid_str(struct.unpack_from('<I', dnam.data, 4)[0])}")

    # CNAM - Default Hair Color
    cnam = get_subrecord(rec, "CNAM")
    if cnam and len(cnam.data) >= 1:
        lines.append(f"CNAM.DefaultHairColor={cnam.data[0]}")

    # PNAM - FaceGen Main/Tint Clamps
    pnam = get_subrecord(rec, "PNAM")
    if pnam and len(pnam.data) >= 4:
        lines.append(f"PNAM.FaceGenMainClamp={struct.unpack_from('<f', pnam.data, 0)[0]}")
    unam = get_subrecord(rec, "UNAM")
    if unam and len(unam.data) >= 4:
        lines.append(f"UNAM.FaceGenFaceClamp={struct.unpack_from('<f', unam.data, 0)[0]}")

    # ATTR - Attributes (male 8 + female 8)
    attr = get_subrecord(rec, "ATTR")
    if attr and len(attr.data) >= 16:
        attr_names = ["Strength", "Intelligence", "Willpower", "Agility",
                      "Speed", "Endurance", "Personality", "Luck"]
        for i, name in enumerate(attr_names):
            lines.append(f"ATTR.Male.{name}={attr.data[i]}")
        for i, name in enumerate(attr_names):
            lines.append(f"ATTR.Female.{name}={attr.data[8+i]}")

    # HNAM - Hair list
    hnams = get_all_subrecords(rec, "HNAM")
    for hnam in hnams:
        count = len(hnam.data) // 4
        if count > 0:
            lines.append(f"HairCount={count}")
            for i in range(count):
                lines.append(f"Hair[{i}]={get_formid_str(struct.unpack_from('<I', hnam.data, i*4)[0])}")

    # ENAM - Eyes list
    enams = get_all_subrecords(rec, "ENAM")
    for enam in enams:
        count = len(enam.data) // 4
        if count > 0:
            lines.append(f"EyesCount={count}")
            for i in range(count):
                lines.append(f"Eyes[{i}]={get_formid_str(struct.unpack_from('<I', enam.data, i*4)[0])}")

    return lines


def export_CLAS(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_string(lines, "DESC", get_subrecord(rec, "DESC"))
    emit_icon(lines, "ICON", rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 50:
        d = data.data
        lines.append(f"DATA.PrimaryAttribute1={struct.unpack_from('<I', d, 0)[0]}")
        lines.append(f"DATA.PrimaryAttribute2={struct.unpack_from('<I', d, 4)[0]}")
        lines.append(f"DATA.Specialization={struct.unpack_from('<I', d, 8)[0]}")
        for i in range(7):
            lines.append(f"DATA.MajorSkill[{i}]={struct.unpack_from('<I', d, 12 + i*4)[0]}")
        lines.append(f"DATA.Flags={struct.unpack_from('<I', d, 40)[0]}")
        lines.append(f"DATA.Services={struct.unpack_from('<I', d, 44)[0]}")
        lines.append(f"DATA.Teaches={d[48]}")
        lines.append(f"DATA.MaxTraining={d[49]}")
    return lines


def export_EYES(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_icon(lines, "ICON", rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 1:
        lines.append(f"DATA.Flags={data.data[0]}")
    return lines


def export_HAIR(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_icon(lines, "ICON", rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 1:
        lines.append(f"DATA.Flags={data.data[0]}")
    return lines


def export_BSGN(rec: Record) -> list:
    """Birthsign."""
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_icon(lines, "ICON", rec)
    emit_string(lines, "DESC", get_subrecord(rec, "DESC"))
    _emit_spells(lines, rec)
    return lines


def export_SKIL(rec: Record) -> list:
    """Skill."""
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 16:
        d = data.data
        lines.append(f"DATA.Attribute={struct.unpack_from('<i', d, 0)[0]}")
        lines.append(f"DATA.Specialization={struct.unpack_from('<I', d, 4)[0]}")
        lines.append(f"DATA.UseValue1={struct.unpack_from('<f', d, 8)[0]}")
        lines.append(f"DATA.UseValue2={struct.unpack_from('<f', d, 12)[0]}")
    emit_string(lines, "DESC", get_subrecord(rec, "DESC"))
    return lines


def export_CSTY(rec: Record) -> list:
    """Combat Style."""
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    cstd = get_subrecord(rec, "CSTD")
    if cstd and len(cstd.data) >= 112:
        d = cstd.data
        lines.append(f"CSTD.DodgeChance={d[0]}")
        lines.append(f"CSTD.DodgeLRChance={d[1]}")
        lines.append(f"CSTD.DodgeFWTimer={struct.unpack_from('<f', d, 4)[0]}")
        lines.append(f"CSTD.DodgeBackTimer={struct.unpack_from('<f', d, 8)[0]}")
        lines.append(f"CSTD.IdleTimer={struct.unpack_from('<f', d, 12)[0]}")
        lines.append(f"CSTD.BlockChance={d[16]}")
        lines.append(f"CSTD.AttackChance={d[17]}")
        lines.append(f"CSTD.StaggerRecoilTimer={struct.unpack_from('<f', d, 20)[0]}")
        lines.append(f"CSTD.AcrobaticDodge={struct.unpack_from('<f', d, 24)[0]}")
        lines.append(f"CSTD.RangeMultOptimal={struct.unpack_from('<f', d, 28)[0]}")
        lines.append(f"CSTD.RangeMultMax={struct.unpack_from('<f', d, 32)[0]}")
        lines.append(f"CSTD.SwitchDist={struct.unpack_from('<f', d, 36)[0]}")
        lines.append(f"CSTD.BuffStandoff={struct.unpack_from('<f', d, 40)[0]}")
        lines.append(f"CSTD.GroupStandoff={struct.unpack_from('<f', d, 48)[0]}")
        lines.append(f"CSTD.RushAttackChance={d[56]}")
        lines.append(f"CSTD.RushAttackDist={struct.unpack_from('<f', d, 60)[0]}")
    csad = get_subrecord(rec, "CSAD")
    if csad and len(csad.data) >= 20:
        d = csad.data
        lines.append(f"CSAD.DodgeFatigueModMul={struct.unpack_from('<f', d, 0)[0]}")
        lines.append(f"CSAD.DodgeFatigueModBase={struct.unpack_from('<f', d, 4)[0]}")
        lines.append(f"CSAD.EncMultiplier={struct.unpack_from('<f', d, 8)[0]}")
        lines.append(f"CSAD.EncBase={struct.unpack_from('<f', d, 12)[0]}")
        lines.append(f"CSAD.DodgeUnder={struct.unpack_from('<f', d, 16)[0]}")
    return lines


def export_IDLE(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_model(lines, "Model", rec)
    emit_conditions(lines, rec)
    anam = get_subrecord(rec, "ANAM")
    if anam and len(anam.data) >= 4:
        lines.append(f"ANAM.AnimGroupSection={struct.unpack_from('<H', anam.data, 0)[0]}")
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 8:
        lines.append(f"DATA.IdleParent={get_formid_str(struct.unpack_from('<I', data.data, 0)[0])}")
        lines.append(f"DATA.IdlePrev={get_formid_str(struct.unpack_from('<I', data.data, 4)[0])}")
    return lines

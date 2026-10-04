"""
Actor-related record types: NPC_, CREA, CONT, FACT, RACE, CLAS, EYES, HAIR,
BSGN, SKIL, CSTY, IDLE.

Pure TES4 data dump - no transformations.
"""

import struct

from ..tes4_reader import Record, get_all_subrecords, get_formid_str, get_subrecord
from core.tes4_encoding import decode
from .common import (
    emit_conditions,
    emit_float,
    escape_value,
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


def _emit_appearance(lines: list, rec) -> None:
    """Hair, eyes, combat style and the three FaceGen PCA blobs (raw hex)."""
    emit_formid(lines, "HNAM.Hair", get_subrecord(rec, "HNAM"))
    emit_float(lines, "LNAM.HairLength", get_subrecord(rec, "LNAM"))
    emit_formid(lines, "ENAM.Eyes", get_subrecord(rec, "ENAM"))
    hclr = get_subrecord(rec, "HCLR")
    if hclr and len(hclr.data) >= 4:
        lines.append(f"HCLR.R={hclr.data[0]}")
        lines.append(f"HCLR.G={hclr.data[1]}")
        lines.append(f"HCLR.B={hclr.data[2]}")
    emit_formid(lines, "ZNAM.CombatStyle", get_subrecord(rec, "ZNAM"))
    for sig in ("FGGS", "FGGA", "FGTS"):
        sub = get_subrecord(rec, sig)
        if sub:
            lines.append(f"{sig}={sub.data.hex()}")


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
    emit_formid(lines, "VTCK.Voice", get_subrecord(rec, "VTCK"))
    _emit_spells(lines, rec)
    emit_script(lines, rec)
    _emit_items(lines, rec)
    _emit_aidt(lines, rec)
    _emit_ai_packages(lines, rec)
    emit_formid(lines, "CNAM.Class", get_subrecord(rec, "CNAM"))
    _emit_appearance(lines, rec)

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

    # NIFZ - body part model file list (null-separated string block)
    nifz = get_subrecord(rec, "NIFZ")
    if nifz:
        parts = [decode(p) for p in nifz.data.split(b"\x00") if p]
        lines.append(f"NIFZCount={len(parts)}")
        for i, p in enumerate(parts):
            lines.append(f"NIFZ[{i}]={escape_value(p)}")

    # KFFZ - animation .kf file list (null-separated string block)
    kffz = get_subrecord(rec, "KFFZ")
    if kffz:
        parts = [decode(p) for p in kffz.data.split(b"\x00") if p]
        lines.append(f"KFFZCount={len(parts)}")
        for i, p in enumerate(parts):
            lines.append(f"KFFZ[{i}]={escape_value(p)}")

    # Sound entries: each CSDT (type) is followed IN STREAM ORDER by its
    # CSDI/CSDC (sound + chance) pairs — a type may list several sounds, so
    # positional zipping of the flat CSDT/CSDI lists mispairs them. CSDC is
    # the authored play chance (wbSoundTypeSounds, shared TES4/TES5 struct).
    entries = []                    # (type, [[sound_fid, chance], ...])
    cur = pending = None
    for sub in rec.subrecords:
        if sub.type == "CSDT" and len(sub.data) >= 4:
            cur = (struct.unpack_from('<I', sub.data, 0)[0], [])
            entries.append(cur)
            pending = None
        elif sub.type == "CSDI" and cur is not None and len(sub.data) >= 4:
            pending = [struct.unpack_from('<I', sub.data, 0)[0], None]
            cur[1].append(pending)
        elif sub.type == "CSDC" and pending is not None and sub.data:
            pending[1] = sub.data[0]
            pending = None
    if entries:
        lines.append(f"SoundTypeCount={len(entries)}")
        for i, (stype, sounds) in enumerate(entries):
            lines.append(f"SoundType[{i}].Type={stype}")
            for j, (fid, chance) in enumerate(sounds):
                # first pair keeps the historical un-indexed names
                key = (f"SoundType[{i}].Sound" if j == 0
                       else f"SoundType[{i}].Sound[{j}]")
                lines.append(f"{key}={get_formid_str(fid)}")
                if chance is not None:
                    lines.append(f"{key}.Chance={chance}")

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

    # TES4 FACT DATA is a single U8 (xEdit wbDefinitionsTES4: Hidden from
    # Player / Evil / Special Combat) — measured at exactly 1 byte in all 204
    # Nehrim.esm factions.  The old `>= 4` guard with a U32 unpack therefore
    # never fired, silently dropping the flags for every faction in every
    # plugin.
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 1:
        lines.append(f"DATA.Flags={data.data[0]}")

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

    _emit_race_parts(lines, rec)

    # FGGS/FGGA/FGTS - race-level FaceGen vectors.  A race either ships its
    # own skin textures (FGTS all zero) or shares another race's textures and
    # recolors them with a non-zero FGTS.  This is the authored source for
    # the skin tone of every shared-texture race (High Elf gold, Redguard
    # brown, Nord pale...), so it must survive export.
    for sig in ("FGGS", "FGGA", "FGTS"):
        sub = get_subrecord(rec, sig)
        if sub and sub.data:
            lines.append(f"{sig}={sub.data.hex()}")

    return lines


def _emit_race_parts(lines: list, rec: Record):
    """Emit the RACE face-part and body-part model/texture lists.

    The layout is positional, not keyed: NAM0 opens the face-part block and
    NAM1 the body-part block, the latter split into MNAM (male) and FNAM
    (female) sections.  Within a block each part is INDX followed by its
    MODL/ICON, so the subrecords must be walked in order.
    """
    section = None   # 'face' | 'male' | 'female'
    index = None
    for sub in rec.subrecords:
        t = sub.type
        if t == "NAM0":
            section, index = "face", None
            continue
        if t == "NAM1":
            section, index = None, None
            continue
        if t == "MNAM":
            section, index = "male", None
            continue
        if t == "FNAM":
            section, index = "female", None
            continue
        if section is None:
            continue
        if t == "INDX":
            index = struct.unpack_from("<I", sub.data, 0)[0] if len(sub.data) >= 4 else None
            continue
        if index is None:
            continue
        if t in ("MODL", "ICON"):
            val = decode(sub.data.rstrip(b"\x00"))
            if val:
                key = "Model" if t == "MODL" else "Texture"
                lines.append(f"{section.capitalize()}Part[{index}].{key}"
                             f"={escape_value(val)}")


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
    """Skill: DATA is Action, governing Attribute, Specialization, two use values (20 bytes).

    See: docs/plans/character_sheet.md#bug-skil-shift
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 20:
        action, attribute, spec, use1, use2 = struct.unpack_from('<iIIff', data.data)
        lines.extend([f"DATA.Action={action}", f"DATA.Attribute={attribute}",
                      f"DATA.Specialization={spec}", f"DATA.UseValue1={use1}",
                      f"DATA.UseValue2={use2}"])
    emit_string(lines, "DESC", get_subrecord(rec, "DESC"))
    return lines


#: TES4 CSTD fields as (offset, struct format, key), per xEdit wbDefinitionsTES4.
_CSTD_FIELDS = (
    (0, 'B', 'DodgeChance'), (1, 'B', 'LeftRightChance'),
    (4, 'f', 'DodgeLRTimerMin'), (8, 'f', 'DodgeLRTimerMax'),
    (12, 'f', 'DodgeForwardTimerMin'), (16, 'f', 'DodgeForwardTimerMax'),
    (20, 'f', 'DodgeBackTimerMin'), (24, 'f', 'DodgeBackTimerMax'),
    (28, 'f', 'IdleTimerMin'), (32, 'f', 'IdleTimerMax'),
    (36, 'B', 'BlockChance'), (37, 'B', 'AttackChance'),
    (40, 'f', 'RecoilStaggerBonusToAttack'), (44, 'f', 'UnconsciousBonusToAttack'),
    (48, 'f', 'HandToHandBonusToAttack'), (52, 'B', 'PowerAttackChance'),
    (56, 'f', 'RecoilStaggerBonusToPowerAttack'), (60, 'f', 'UnconsciousBonusToPowerAttack'),
    (64, 'B', 'PowerAttackNormal'), (65, 'B', 'PowerAttackForward'),
    (66, 'B', 'PowerAttackBack'), (67, 'B', 'PowerAttackLeft'), (68, 'B', 'PowerAttackRight'),
    (72, 'f', 'HoldTimerMin'), (76, 'f', 'HoldTimerMax'),
    (80, 'B', 'Flags'), (81, 'B', 'AcrobaticDodgeChance'),
    (84, 'f', 'RangeMultOptimal'), (88, 'f', 'RangeMultMax'),
    (92, 'f', 'SwitchDistanceMelee'), (96, 'f', 'SwitchDistanceRanged'),
    (100, 'f', 'BuffStandoffDistance'), (104, 'f', 'RangedStandoffDistance'),
    (108, 'f', 'GroupStandoffDistance'), (112, 'B', 'RushingAttackChance'),
    (116, 'f', 'RushingAttackDistanceMult'), (120, 'I', 'DoNotAcquire'),
)

#: TES4 CSAD (Advanced) fields, all floats, in record order.
_CSAD_FIELDS = (
    'DodgeFatigueModMult', 'DodgeFatigueModBase', 'EncumberedSpeedModBase',
    'EncumberedSpeedModMult', 'DodgeWhileUnderAttackMult', 'DodgeNotUnderAttackMult',
    'DodgeBackWhileUnderAttackMult', 'DodgeBackNotUnderAttackMult',
    'DodgeForwardWhileAttackingMult', 'DodgeForwardNotAttackingMult',
    'BlockSkillModifierMult', 'BlockSkillModifierBase', 'BlockWhileUnderAttackMult',
    'BlockNotUnderAttackMult', 'AttackSkillModifierMult', 'AttackSkillModifierBase',
    'AttackWhileUnderAttackMult', 'AttackNotUnderAttackMult', 'AttackDuringBlockMult',
    'PowerAttackFatigueModBase', 'PowerAttackFatigueModMult',
)


def _emit_fields(lines: list, prefix: str, data: bytes, fields) -> None:
    """Emit each (offset, format, key) field that fits inside `data`."""
    for off, fmt, key in fields:
        if off + struct.calcsize(fmt) <= len(data):
            lines.append(f"{prefix}.{key}={struct.unpack_from('<' + fmt, data, off)[0]}")


def export_CSTY(rec: Record) -> list:
    """Combat Style: every CSTD and CSAD field present in the record."""
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    cstd = get_subrecord(rec, "CSTD")
    if cstd:
        _emit_fields(lines, "CSTD", cstd.data, _CSTD_FIELDS)
    csad = get_subrecord(rec, "CSAD")
    if csad:
        _emit_fields(lines, "CSAD", csad.data,
                     [(4 * i, 'f', key) for i, key in enumerate(_CSAD_FIELDS)])
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

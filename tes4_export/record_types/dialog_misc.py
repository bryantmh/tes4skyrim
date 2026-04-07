"""
Dialog, quest, and miscellaneous record types: DIAL, INFO, QUST, PACK, SCPT,
GLOB, GMST, SOUN, CLMT, WATR, EFSH, LSCR, LVLI, LVLC, LVSP, WTHR.

Pure TES4 data dump - no transformations.
"""

import struct

from ..tes4_reader import Record, get_all_subrecords, get_formid_str, get_string, get_subrecord
from .common import (
    emit_conditions,
    emit_float,
    emit_formid,
    emit_icon,
    emit_script,
    emit_string,
    emit_u8,
    escape_value,
)


def export_DIAL(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    # QSTI - quest associations
    qstis = get_all_subrecords(rec, "QSTI")
    if qstis:
        lines.append(f"QuestCount={len(qstis)}")
        for i, q in enumerate(qstis):
            if len(q.data) >= 4:
                lines.append(f"Quest[{i}]={get_formid_str(struct.unpack_from('<I', q.data, 0)[0])}")
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 1:
        lines.append(f"DATA.Type={data.data[0]}")
    return lines


def export_INFO(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 3:
        d = data.data
        lines.append(f"DATA.DialogType={d[0]}")
        lines.append(f"DATA.NextSpeaker={d[1]}")
        lines.append(f"DATA.Flags={d[2]}")

    emit_formid(lines, "QSTI.Quest", get_subrecord(rec, "QSTI"))
    emit_formid(lines, "TPIC.Topic", get_subrecord(rec, "TPIC"))
    emit_formid(lines, "PNAM.PrevInfo", get_subrecord(rec, "PNAM"))

    # NAME - added topics
    names = get_all_subrecords(rec, "NAME")
    if names:
        lines.append(f"AddTopicCount={len(names)}")
        for i, n in enumerate(names):
            if len(n.data) >= 4:
                lines.append(f"AddTopic[{i}]={get_formid_str(struct.unpack_from('<I', n.data, 0)[0])}")

    # Responses (TRDT + NAM1 + NAM2)
    trdts = get_all_subrecords(rec, "TRDT")
    nam1s = get_all_subrecords(rec, "NAM1")
    nam2s = get_all_subrecords(rec, "NAM2")
    if trdts:
        lines.append(f"ResponseCount={len(trdts)}")
        for i, trdt in enumerate(trdts):
            pfx = f"Response[{i}]"
            if len(trdt.data) >= 16:
                lines.append(f"{pfx}.EmotionType={struct.unpack_from('<I', trdt.data, 0)[0]}")
                lines.append(f"{pfx}.EmotionValue={struct.unpack_from('<i', trdt.data, 4)[0]}")
                lines.append(f"{pfx}.ResponseNumber={trdt.data[12]}")
            if i < len(nam1s):
                lines.append(f"{pfx}.ResponseText={escape_value(get_string(nam1s[i]))}")
            if i < len(nam2s):
                lines.append(f"{pfx}.ActorNotes={escape_value(get_string(nam2s[i]))}")

    emit_conditions(lines, rec)

    # TCLT - choices (multiple, as indexed array)
    tclts = get_all_subrecords(rec, "TCLT")
    if tclts:
        lines.append(f"ChoiceCount={len(tclts)}")
        for i, tclt in enumerate(tclts):
            if len(tclt.data) >= 4:
                lines.append(f"Choice[{i}]={get_formid_str(struct.unpack_from('<I', tclt.data, 0)[0])}")

    # TCLF - link-from topics (multiple, as indexed array)
    tclfs = get_all_subrecords(rec, "TCLF")
    if tclfs:
        lines.append(f"LinkFromCount={len(tclfs)}")
        for i, tclf in enumerate(tclfs):
            if len(tclf.data) >= 4:
                lines.append(f"LinkFrom[{i}]={get_formid_str(struct.unpack_from('<I', tclf.data, 0)[0])}")

    # Result script (SCHR/SCDA/SCTX)
    sctx = get_subrecord(rec, "SCTX")
    if sctx:
        lines.append(f"ResultScript={escape_value(get_string(sctx))}")

    return lines


def export_QUST(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_script(lines, rec)
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_icon(lines, "ICON", rec)

    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 2:
        lines.append(f"DATA.Flags={data.data[0]}")
        lines.append(f"DATA.Priority={data.data[1]}")

    emit_conditions(lines, rec)

    # Stages — iterate subrecords in order to capture QSDT + CNAM per log entry
    stages = []  # list of (index, [{'flags': int, 'text': str}, ...])
    current_idx = None
    current_logs = []
    for sub in rec.subrecords:
        if sub.type == "INDX":
            if current_idx is not None:
                stages.append((current_idx, current_logs))
            current_idx = struct.unpack_from('<h', sub.data, 0)[0] if len(sub.data) >= 2 else 0
            current_logs = []
        elif sub.type == "QSDT" and current_idx is not None:
            qsdt_flags = sub.data[0] if sub.data else 0
            current_logs.append({'flags': qsdt_flags, 'text': ''})
        elif sub.type == "CNAM" and current_idx is not None and current_logs:
            current_logs[-1]['text'] = get_string(sub)
    if current_idx is not None:
        stages.append((current_idx, current_logs))

    if stages:
        lines.append(f"StageCount={len(stages)}")
        for i, (stage_idx, log_entries) in enumerate(stages):
            lines.append(f"Stage[{i}].Index={stage_idx}")
            log_entries = log_entries or [{'flags': 0, 'text': ''}]
            lines.append(f"Stage[{i}].LogCount={len(log_entries)}")
            for j, entry in enumerate(log_entries):
                lines.append(f"Stage[{i}].Log[{j}].Flags={entry['flags']}")
                if entry['text']:
                    lines.append(f"Stage[{i}].Log[{j}].Text={escape_value(entry['text'])}")

    # Targets (QSTA)
    qstas = get_all_subrecords(rec, "QSTA")
    if qstas:
        lines.append(f"TargetCount={len(qstas)}")
        for i, qsta in enumerate(qstas):
            if len(qsta.data) >= 8:
                lines.append(f"Target[{i}].FormID={get_formid_str(struct.unpack_from('<I', qsta.data, 0)[0])}")
                lines.append(f"Target[{i}].Flags={struct.unpack_from('<I', qsta.data, 4)[0]}")

    return lines


def export_PACK(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))

    # PKDT — Package Data.
    # Two formats exist in TES4:
    #   Old (length=4): Flags U16, Type U8, Unused U8
    #   New (length=8): Flags U32, Type U8, Unused U8 U8 U8
    # Emit PKDT.Format so the importer knows which flag width to expect.
    pkdt = get_subrecord(rec, "PKDT")
    if pkdt and len(pkdt.data) >= 4:
        d = pkdt.data
        if len(d) >= 8:
            lines.append(f"PKDT.Format=new")
            lines.append(f"PKDT.Flags={struct.unpack_from('<I', d, 0)[0]}")
            lines.append(f"PKDT.Type={d[4]}")
        else:
            lines.append(f"PKDT.Format=old")
            lines.append(f"PKDT.Flags={struct.unpack_from('<H', d, 0)[0]}")
            lines.append(f"PKDT.Type={d[2]}")

    # PLDT — Location data (12 bytes: Type S32, Value 4 bytes, Radius S32)
    # Type 0 = Near reference (Value = FormID)
    # Type 1 = In cell        (Value = FormID)
    # Type 2 = Near current location (Value = ignored)
    # Type 3 = Near editor location  (Value = ignored)
    # Type 4 = Object ID      (Value = FormID)
    # Type 5 = Object type    (Value = U32 type enum)
    pldt = get_subrecord(rec, "PLDT")
    if pldt and len(pldt.data) >= 12:
        d = pldt.data
        pldt_type = struct.unpack_from('<i', d, 0)[0]
        lines.append(f"PLDT.Type={pldt_type}")
        if pldt_type in (0, 1, 4):
            lines.append(f"PLDT.Location={get_formid_str(struct.unpack_from('<I', d, 4)[0])}")
        else:
            lines.append(f"PLDT.Location={struct.unpack_from('<I', d, 4)[0]}")
        lines.append(f"PLDT.Radius={struct.unpack_from('<i', d, 8)[0]}")

    # PSDT — Schedule (Month S8, DayOfWeek S8, Date U8, Time S8, Duration S32)
    # Note: TES4 PSDT is 8 bytes (Time at offset 3 is S8, Duration at offset 4).
    # TES5 adds a Minute field (offset 4, S8) before Duration (offset 8, S32).
    psdt = get_subrecord(rec, "PSDT")
    if psdt and len(psdt.data) >= 8:
        d = psdt.data
        lines.append(f"PSDT.Month={struct.unpack_from('<b', d, 0)[0]}")
        lines.append(f"PSDT.DayOfWeek={struct.unpack_from('<b', d, 1)[0]}")
        lines.append(f"PSDT.Date={d[2]}")
        lines.append(f"PSDT.Time={struct.unpack_from('<b', d, 3)[0]}")
        lines.append(f"PSDT.Duration={struct.unpack_from('<i', d, 4)[0]}")

    # PTDT — Target data (12 bytes: Type S32, Target 4 bytes, Count S32)
    # Type 0 = Specific reference (Target = FormID)
    # Type 1 = Object ID          (Target = FormID)
    # Type 2 = Object type        (Target = U32 type enum)
    ptdt = get_subrecord(rec, "PTDT")
    if ptdt and len(ptdt.data) >= 12:
        d = ptdt.data
        ptdt_type = struct.unpack_from('<i', d, 0)[0]
        lines.append(f"PTDT.Type={ptdt_type}")
        if ptdt_type in (0, 1):
            lines.append(f"PTDT.Target={get_formid_str(struct.unpack_from('<I', d, 4)[0])}")
        else:
            lines.append(f"PTDT.Target={struct.unpack_from('<I', d, 4)[0]}")
        lines.append(f"PTDT.Count={struct.unpack_from('<i', d, 8)[0]}")

    # Conditions (CTDAs) — needed for proper package behaviour in TES5
    emit_conditions(lines, rec)
    return lines


def export_SCPT(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    schr = get_subrecord(rec, "SCHR")
    if schr and len(schr.data) >= 20:
        d = schr.data
        lines.append(f"SCHR.RefCount={struct.unpack_from('<I', d, 4)[0]}")
        lines.append(f"SCHR.CompiledSize={struct.unpack_from('<I', d, 8)[0]}")
        lines.append(f"SCHR.VariableCount={struct.unpack_from('<I', d, 12)[0]}")
        lines.append(f"SCHR.Type={struct.unpack_from('<H', d, 16)[0]}")
    sctx = get_subrecord(rec, "SCTX")
    if sctx:
        lines.append(f"SCTX={escape_value(get_string(sctx))}")
    return lines


def export_GLOB(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    fnam = get_subrecord(rec, "FNAM")
    if fnam and len(fnam.data) >= 1:
        type_char = chr(fnam.data[0]) if fnam.data[0] < 128 else str(fnam.data[0])
        lines.append(f"FNAM.Type={type_char}")
    emit_float(lines, "FLTV.Value", get_subrecord(rec, "FLTV"))
    return lines


def export_GMST(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    data = get_subrecord(rec, "DATA")
    edid = get_string(get_subrecord(rec, "EDID")) if get_subrecord(rec, "EDID") else ""
    if data and len(data.data) >= 4:
        # Type determined by first char of EditorID: s=string, f=float, i=int
        if edid.startswith("s"):
            lines.append(f"DATA.Value={escape_value(get_string(data))}")
        elif edid.startswith("f"):
            lines.append(f"DATA.Value={struct.unpack_from('<f', data.data, 0)[0]}")
        else:
            lines.append(f"DATA.Value={struct.unpack_from('<I', data.data, 0)[0]}")
    return lines


def export_SOUN(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FNAM.Filename", get_subrecord(rec, "FNAM"))
    # SNDD or SNDX (older format)
    sndd = get_subrecord(rec, "SNDD")
    if sndd and len(sndd.data) >= 8:
        d = sndd.data
        lines.append(f"SNDD.MinAttDist={d[0]}")
        lines.append(f"SNDD.MaxAttDist={d[1]}")
        lines.append(f"SNDD.FreqAdj={struct.unpack_from('<b', d, 2)[0]}")
        lines.append(f"SNDD.Flags={struct.unpack_from('<I', d, 4)[0]}")
        if len(d) >= 12:
            lines.append(f"SNDD.Attenuation={struct.unpack_from('<H', d, 8)[0]}")
            lines.append(f"SNDD.StopTime={d[10]}")
            lines.append(f"SNDD.StartTime={d[11]}")
    else:
        sndx = get_subrecord(rec, "SNDX")
        if sndx and len(sndx.data) >= 8:
            d = sndx.data
            lines.append(f"SNDX.MinAttDist={d[0]}")
            lines.append(f"SNDX.MaxAttDist={d[1]}")
            lines.append(f"SNDX.FreqAdj={struct.unpack_from('<b', d, 2)[0]}")
            lines.append(f"SNDX.Flags={struct.unpack_from('<I', d, 4)[0]}")
    return lines


def export_CLMT(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    # WLST - weather list
    wlst = get_subrecord(rec, "WLST")
    if wlst:
        count = len(wlst.data) // 8
        lines.append(f"WeatherCount={count}")
        for i in range(count):
            off = i * 8
            if off + 8 <= len(wlst.data):
                fid = struct.unpack_from("<I", wlst.data, off)[0]
                chance = struct.unpack_from("<I", wlst.data, off + 4)[0]
                lines.append(f"Weather[{i}].FormID={get_formid_str(fid)}")
                lines.append(f"Weather[{i}].Chance={chance}")
    emit_string(lines, "FNAM.SunTexture", get_subrecord(rec, "FNAM"))
    emit_string(lines, "GNAM.GlareTexture", get_subrecord(rec, "GNAM"))
    tnam = get_subrecord(rec, "TNAM")
    if tnam and len(tnam.data) >= 6:
        d = tnam.data
        lines.append(f"TNAM.SunriseBegin={d[0]}")
        lines.append(f"TNAM.SunriseEnd={d[1]}")
        lines.append(f"TNAM.SunsetBegin={d[2]}")
        lines.append(f"TNAM.SunsetEnd={d[3]}")
        lines.append(f"TNAM.Volatility={d[4]}")
        lines.append(f"TNAM.MoonsPhaseLength={d[5]}")
    return lines


def export_WATR(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "TNAM.Texture", get_subrecord(rec, "TNAM"))
    emit_u8(lines, "ANAM.Opacity", get_subrecord(rec, "ANAM"))
    emit_u8(lines, "FNAM.Flags", get_subrecord(rec, "FNAM"))
    emit_string(lines, "MNAM.MaterialID", get_subrecord(rec, "MNAM"))
    emit_formid(lines, "SNAM.Sound", get_subrecord(rec, "SNAM"))
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 2:
        # DATA is a large struct (~186 bytes), dump key fields
        lines.append(f"DATA.Size={len(data.data)}")
        if len(data.data) >= 8:
            lines.append(f"DATA.WindVelocity={struct.unpack_from('<f', data.data, 0)[0]}")
            lines.append(f"DATA.WindDirection={struct.unpack_from('<f', data.data, 4)[0]}")
    return lines


def export_EFSH(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_icon(lines, "ICON", rec, "ICON")
    emit_icon(lines, "ICO2", rec, "ICO2")
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 96:
        d = data.data
        lines.append(f"DATA.Flags={d[0]}")
        lines.append(f"DATA.MemSBlend={struct.unpack_from('<I', d, 4)[0]}")
        lines.append(f"DATA.MemBlendOp={struct.unpack_from('<I', d, 8)[0]}")
        lines.append(f"DATA.MemZFunc={struct.unpack_from('<I', d, 12)[0]}")
        lines.append(f"DATA.FillColorR={d[16]}")
        lines.append(f"DATA.FillColorG={d[17]}")
        lines.append(f"DATA.FillColorB={d[18]}")
        lines.append(f"DATA.FillAlphaFadeInTime={struct.unpack_from('<f', d, 20)[0]}")
        lines.append(f"DATA.FillAlphaFull={struct.unpack_from('<f', d, 24)[0]}")
        lines.append(f"DATA.FillAlphaFadeOutTime={struct.unpack_from('<f', d, 28)[0]}")
        lines.append(f"DATA.FillAlphaPersistPercent={struct.unpack_from('<f', d, 32)[0]}")
        lines.append(f"DATA.FillAlphaPulseAmp={struct.unpack_from('<f', d, 36)[0]}")
        lines.append(f"DATA.FillAlphaPulseFreq={struct.unpack_from('<f', d, 40)[0]}")
        lines.append(f"DATA.FillTextureAnimSpeedU={struct.unpack_from('<f', d, 44)[0]}")
        lines.append(f"DATA.FillTextureAnimSpeedV={struct.unpack_from('<f', d, 48)[0]}")
        lines.append(f"DATA.EdgeEffectWidth={struct.unpack_from('<f', d, 52)[0]}")
        lines.append(f"DATA.EdgeColorR={d[56]}")
        lines.append(f"DATA.EdgeColorG={d[57]}")
        lines.append(f"DATA.EdgeColorB={d[58]}")
    return lines


def export_LSCR(rec: Record) -> list:
    """Loading Screen."""
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_icon(lines, "ICON", rec)
    emit_string(lines, "DESC", get_subrecord(rec, "DESC"))
    # LNAM - locations
    lnams = get_all_subrecords(rec, "LNAM")
    if lnams:
        lines.append(f"LocationCount={len(lnams)}")
        for i, lnam in enumerate(lnams):
            if len(lnam.data) >= 12:
                lines.append(f"Location[{i}].Direct={get_formid_str(struct.unpack_from('<I', lnam.data, 0)[0])}")
                lines.append(f"Location[{i}].Indirect={get_formid_str(struct.unpack_from('<I', lnam.data, 4)[0])}")
                lines.append(f"Location[{i}].GridX={struct.unpack_from('<h', lnam.data, 8)[0]}")
                lines.append(f"Location[{i}].GridY={struct.unpack_from('<h', lnam.data, 10)[0]}")
    return lines


def _emit_leveled_entries(lines: list, rec: Record, sig: str = "LVLO"):
    """Emit entries for leveled lists."""
    lvlos = get_all_subrecords(rec, sig)
    lines.append(f"EntryCount={len(lvlos)}")
    for i, lvlo in enumerate(lvlos):
        if len(lvlo.data) >= 12:
            d = lvlo.data
            lines.append(f"Entry[{i}].Level={struct.unpack_from('<H', d, 0)[0]}")
            lines.append(f"Entry[{i}].FormID={get_formid_str(struct.unpack_from('<I', d, 4)[0])}")
            lines.append(f"Entry[{i}].Count={struct.unpack_from('<H', d, 8)[0]}")


def export_LVLI(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_u8(lines, "LVLD.ChanceNone", get_subrecord(rec, "LVLD"))
    emit_u8(lines, "LVLF.Flags", get_subrecord(rec, "LVLF"))
    _emit_leveled_entries(lines, rec)
    return lines


def export_LVLC(rec: Record) -> list:
    """Leveled Creature."""
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_u8(lines, "LVLD.ChanceNone", get_subrecord(rec, "LVLD"))
    emit_u8(lines, "LVLF.Flags", get_subrecord(rec, "LVLF"))
    emit_script(lines, rec)
    emit_formid(lines, "TNAM.Template", get_subrecord(rec, "TNAM"))
    _emit_leveled_entries(lines, rec)
    return lines


def export_LVSP(rec: Record) -> list:
    """Leveled Spell."""
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_u8(lines, "LVLD.ChanceNone", get_subrecord(rec, "LVLD"))
    emit_u8(lines, "LVLF.Flags", get_subrecord(rec, "LVLF"))
    _emit_leveled_entries(lines, rec)
    return lines


def export_WTHR(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    # Cloud textures CNAM/DNAM
    emit_string(lines, "CNAM.LowerCloudLayer", get_subrecord(rec, "CNAM"))
    emit_string(lines, "DNAM.UpperCloudLayer", get_subrecord(rec, "DNAM"))
    # NAM0 - Colors by time of day (huge struct)
    nam0 = get_subrecord(rec, "NAM0")
    if nam0:
        lines.append(f"NAM0.Size={len(nam0.data)}")
    # FNAM - Fog distances
    fnam = get_subrecord(rec, "FNAM")
    if fnam and len(fnam.data) >= 16:
        d = fnam.data
        lines.append(f"FNAM.FogDayNear={struct.unpack_from('<f', d, 0)[0]}")
        lines.append(f"FNAM.FogDayFar={struct.unpack_from('<f', d, 4)[0]}")
        lines.append(f"FNAM.FogNightNear={struct.unpack_from('<f', d, 8)[0]}")
        lines.append(f"FNAM.FogNightFar={struct.unpack_from('<f', d, 12)[0]}")
    # HNAM - HDR data
    hnam = get_subrecord(rec, "HNAM")
    if hnam and len(hnam.data) >= 56:
        d = hnam.data
        fields = ["EyeAdaptSpeed", "BlurRadius", "BlurPasses", "EmissiveMult",
                   "TargetLum", "UpperLumClamp", "BrightScale", "BrightClamp",
                   "LumRampNoTex", "LumRampMin", "LumRampMax", "SunlightDimmer",
                   "GrassDimmer", "TreeDimmer"]
        for i, name in enumerate(fields):
            if i * 4 + 4 <= len(d):
                lines.append(f"HNAM.{name}={struct.unpack_from('<f', d, i*4)[0]}")
    # DATA - Wind speed, cloud speeds, trans delta, sun glare, sun damage
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 15:
        d = data.data
        lines.append(f"DATA.WindSpeed={d[0]}")
        lines.append(f"DATA.CloudSpeedLower={d[1]}")
        lines.append(f"DATA.CloudSpeedUpper={d[2]}")
        lines.append(f"DATA.TransDelta={d[3]}")
        lines.append(f"DATA.SunGlare={d[4]}")
        lines.append(f"DATA.SunDamage={d[5]}")
    # Sound references
    snams = get_all_subrecords(rec, "SNAM")
    if snams:
        lines.append(f"SoundCount={len(snams)}")
        for i, snam in enumerate(snams):
            if len(snam.data) >= 8:
                lines.append(f"Sound[{i}].FormID={get_formid_str(struct.unpack_from('<I', snam.data, 0)[0])}")
                lines.append(f"Sound[{i}].Type={struct.unpack_from('<I', snam.data, 4)[0]}")
    return lines

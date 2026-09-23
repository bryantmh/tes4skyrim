"""
FO3/FNV export deltas: the subrecords Oblivion never emits.

Only fields whose FO3/FNV layout differs from TES4, or that TES4 lacks
entirely, live here. Everything the two games share is dumped by the
regular per-type exporters in this package.

See: docs/commentary/tes4_export_falloutnv.md
"""

import struct

from ..tes4_reader import (Record, get_all_subrecords,
                           get_formid_str, get_string,
                           get_subrecord)
from .quest_falloutnv import emit_quest_deltas
from .common import (emit_float, emit_formid, emit_model, emit_raw_hex,
                     emit_script, emit_string, emit_u8, emit_u16, emit_u32)

#: HEDR.Version reported by FO3/FNV plugins; Oblivion reports 0.8 or 1.0.
FALLOUT_HEDR_MIN = 1.2


def info_result_scripts(rec: Record) -> tuple:
    """(Begin, End) result-script text of an INFO.

    FO3/FNV embed TWO scripts in an INFO, Begin then End, split by the NEXT
    marker; TES4 embeds one, which counts as Begin.
    See: docs/commentary/tes4_export_falloutnv.md#info-end-script
    """
    texts = ['', '']
    slot = 0
    for sub in rec.subrecords:
        if sub.type == 'NEXT':
            slot = 1
        elif sub.type == 'SCTX':
            texts[slot] = get_string(sub)
    return tuple(texts)


def is_fallout(hedr_version: float) -> bool:
    """True when a plugin's HEDR version marks it FO3/FNV rather than TES4."""
    return hedr_version >= FALLOUT_HEDR_MIN


def _emit_obnd(lines: list, rec: Record):
    """OBND (12B) -> six s16 bounds; Skyrim-native, so it passes through."""
    obnd = get_subrecord(rec, "OBND")
    if obnd and len(obnd.data) >= 12:
        x1, y1, z1, x2, y2, z2 = struct.unpack_from("<6h", obnd.data, 0)
        lines.append(f"OBND.X1={x1}")
        lines.append(f"OBND.Y1={y1}")
        lines.append(f"OBND.Z1={z1}")
        lines.append(f"OBND.X2={x2}")
        lines.append(f"OBND.Y2={y2}")
        lines.append(f"OBND.Z2={z2}")


#: CELL FormID pointers FO3/FNV and TES5 define identically -> the type each names.
CELL_POINTERS = (("LTMP", "LightingTemplate"), ("XCIM", "Imagespace"),
                 ("XCMO", "Music"), ("XEZN", "EncounterZone"))


def _emit_cell_deltas(lines: list, rec: Record):
    """CELL's FO3/FNV-only fields: land flags, water noise and its pointers.

    See: docs/commentary/tes4_export_falloutnv.md#reference-only-types
    """
    for sig, key in CELL_POINTERS:
        emit_formid(lines, f"{sig}.{key}", get_subrecord(rec, sig))

    xclc = get_subrecord(rec, "XCLC")
    if xclc and len(xclc.data) >= 12:
        lines.append(f"XCLC.LandFlags={xclc.data[8]}")

    xcll = get_subrecord(rec, "XCLL")
    if xcll and len(xcll.data) >= 40:
        lines.append(f"XCLL.FogPower={struct.unpack_from('<f', xcll.data, 36)[0]}")

    xnam = get_subrecord(rec, "XNAM")
    if xnam and len(xnam.data) > 1:
        lines.append(f"XNAM.WaterNoiseTexture={get_string(xnam)}")


def _emit_refr_deltas(lines: list, rec: Record):
    """REFR's FO3/FNV-only placement fields that map onto Skyrim equivalents."""
    xprm = get_subrecord(rec, "XPRM")
    if xprm and len(xprm.data) >= 32:
        bx, by, bz = struct.unpack_from("<3f", xprm.data, 0)
        lines.append(f"XPRM.BoundX={bx}")
        lines.append(f"XPRM.BoundY={by}")
        lines.append(f"XPRM.BoundZ={bz}")
        emit_raw_hex(lines, "XPRM.Raw", xprm)

    xrds = get_subrecord(rec, "XRDS")
    if xrds and len(xrds.data) >= 4:
        lines.append(f"XRDS.Radius={struct.unpack_from('<f', xrds.data, 0)[0]}")

    xemi = get_subrecord(rec, "XEMI")
    if xemi and len(xemi.data) >= 4:
        lines.append(f"XEMI.Emittance={get_formid_str(struct.unpack_from('<I', xemi.data, 0)[0])}")

    xlkr = get_subrecord(rec, "XLKR")
    if xlkr and len(xlkr.data) >= 4:
        lines.append(f"XLKR.LinkedRef={get_formid_str(struct.unpack_from('<I', xlkr.data, 0)[0])}")


#: FO3/FNV weapon anim type -> the equivalent TES4 DATA.Type.
_FALLOUT_WEAPON_TYPE = {
    0: 0, 1: 0, 2: 1, 3: 5, 4: 5, 5: 5, 6: 5,
    7: 5, 8: 1, 9: 5, 10: 5, 11: 5, 12: 5, 13: 5,
}

#: FO3/FNV anim types that are firearms rather than melee, by hand count.
FALLOUT_PISTOL_TYPES = frozenset({3, 4, 10, 11, 12, 13})
FALLOUT_LONGARM_TYPES = frozenset({5, 6, 7, 9})


def _emit_weap_deltas(lines: list, rec: Record):
    """WEAP's FO3/FNV layout: DATA carries the economy, DNAM the animation.

    TES4 packs both into one DATA; the keys emitted here are the TES4 ones so
    the importer needs no new vocabulary for the shared fields.

    DNAM offsets (wbDefinitionsFNV): 12 Flags1, 15 Reload Animation,
    41 Attack Animation -- the gun graph keys its clips on all three.

    See: docs/commentary/tes4_export_falloutnv.md#weapons-guns-become-crossbows
    """
    dnam = get_subrecord(rec, "DNAM")
    if dnam and len(dnam.data) >= 12:
        anim = struct.unpack_from("<I", dnam.data, 0)[0]
        lines.append(f"DATA.Type={_FALLOUT_WEAPON_TYPE.get(anim, 0)}")
        lines.append(f"DNAM.FalloutAnimType={anim}")
        lines.append(f"DATA.Speed={struct.unpack_from('<f', dnam.data, 4)[0]}")
        lines.append(f"DATA.Reach={struct.unpack_from('<f', dnam.data, 8)[0]}")
    if dnam and len(dnam.data) >= 16:
        lines.append(f"DNAM.Flags1={dnam.data[12]}")
        lines.append(f"DNAM.ReloadAnim={dnam.data[15]}")
    if dnam and len(dnam.data) >= 42:
        lines.append(f"DNAM.AttackAnim={dnam.data[41]}")

    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 15:
        d = data.data
        lines.append(f"DATA.Value={struct.unpack_from('<i', d, 0)[0]}")
        lines.append(f"DATA.Health={struct.unpack_from('<i', d, 4)[0]}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', d, 8)[0]}")
        lines.append(f"DATA.Damage={struct.unpack_from('<h', d, 12)[0]}")
        lines.append(f"DATA.ClipSize={d[14]}")
    _emit_weap_fire(lines, rec, dnam)


#: FO3/FNV WEAP sound and link subrecords, emitted under their own signature.
_WEAP_FORMIDS = ("SNAM", "XNAM", "NAM7", "TNAM", "UNAM", "NAM9", "NAM8",
                 "NAM0", "WNAM", "INAM")


def _emit_weap_fire(lines: list, rec: Record, dnam):
    """The gun's projectile, ammo use, rate fields and sound links.

    DNAM offsets (wbDefinitionsFNV): 14 Ammo Use, 28 Sight FOV, 36
    Projectile, 42 Projectile Count, 60 Animation Attack Multiplier, 64
    Fire Rate, 88 Attack Shots/Sec. SNAM appears twice (shoot 3D, shoot
    distant); the first is the shot.
    See: docs/commentary/tes4_export_falloutnv.md#projectiles
    """
    if dnam and len(dnam.data) >= 44:
        d = dnam.data
        lines.append(f"DNAM.AmmoUse={d[14]}")
        lines.append(f"DNAM.Projectile="
                     f"{get_formid_str(struct.unpack_from('<I', d, 36)[0])}")
        lines.append(f"DNAM.ProjectileCount={d[42]}")
    if dnam:
        emit_float(lines, "DNAM.SightFOV", dnam, 28)
        emit_float(lines, "DNAM.AnimAttackMult", dnam, 60)
        emit_float(lines, "DNAM.FireRate", dnam, 64)
        emit_float(lines, "DNAM.ShotsPerSec", dnam, 88)
    for sig in _WEAP_FORMIDS:
        emit_formid(lines, sig, get_subrecord(rec, sig))


def _emit_proj_deltas(lines: list, rec: Record):
    """PROJ, a type TES4 lacks: model, the 84-byte DATA, muzzle flash, level.

    DATA offsets (wbDefinitionsFNV): 0 flags u16, 2 type u16, 4 gravity,
    8 speed, 12 range, 16 light, 20 muzzle flash light, 24 tracer chance,
    28/32 alt-trigger proximity/timer, 36 explosion, 40 sound, 44 muzzle
    flash duration, 48 fade duration, 52 impact force, 64 default weapon.
    See: docs/commentary/tes4_export_falloutnv.md#projectiles
    """
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 68:
        d = data.data
        lines.append(f"DATA.Flags={struct.unpack_from('<H', d, 0)[0]}")
        lines.append(f"DATA.Type={struct.unpack_from('<H', d, 2)[0]}")
        for key, off in (("Gravity", 4), ("Speed", 8), ("Range", 12),
                         ("TracerChance", 24), ("AltTriggerProximity", 28),
                         ("AltTriggerTimer", 32), ("MuzzleFlashDuration", 44),
                         ("FadeDuration", 48), ("ImpactForce", 52)):
            emit_float(lines, f"DATA.{key}", data, off)
        for key, off in (("Light", 16), ("MuzzleFlashLight", 20),
                         ("Explosion", 36), ("Sound", 40),
                         ("DefaultWeapon", 64)):
            fid = struct.unpack_from("<I", d, off)[0]
            lines.append(f"DATA.{key}={get_formid_str(fid)}")
    emit_string(lines, "NAM1", get_subrecord(rec, "NAM1"))
    emit_u32(lines, "VNAM", get_subrecord(rec, "VNAM"))


def _emit_ammo_deltas(lines: list, rec: Record):
    """AMMO's FO3/FNV layout: a 13-byte DATA and the weight in DAT2.

    DATA: speed f32, flags u8 (bit 0 Ignores Normal Weapon Resistance, bit 1
    Non-Playable), 3 unused, value s32, clip rounds u8. DAT2: projectiles
    per shot u32, projectile FormID, weight f32, consumed ammo, percentage.
    The TES4 keys are emitted for the shared fields.

    See: docs/commentary/tes4_export_falloutnv.md#ammo-is-a-bolt
    """
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 13:
        d = data.data
        lines.append(f"DATA.Speed={struct.unpack_from('<f', d, 0)[0]}")
        lines.append(f"DATA.Flags={d[4]}")
        lines.append(f"DATA.Value={struct.unpack_from('<i', d, 8)[0]}")
        lines.append(f"DATA.ClipRounds={d[12]}")
    dat2 = get_subrecord(rec, "DAT2")
    if dat2 and len(dat2.data) >= 12:
        d = dat2.data
        lines.append(f"DAT2.ProjPerShot={struct.unpack_from('<I', d, 0)[0]}")
        lines.append(f"DAT2.Projectile="
                     f"{get_formid_str(struct.unpack_from('<I', d, 4)[0])}")
        lines.append(f"DATA.Weight={struct.unpack_from('<f', d, 8)[0]}")


def _emit_wrld_deltas(lines: list, rec: Record):
    """WRLD fields TES4 lacks: parent-use flags, map offset, LOD water, defaults.

    See: docs/commentary/tes4_export_falloutnv.md#child-worldspaces
    """
    emit_u16(lines, "PNAM.Flags", get_subrecord(rec, "PNAM"))
    onam = get_subrecord(rec, "ONAM")
    if onam and len(onam.data) >= 12:
        emit_float(lines, "ONAM.Scale", onam, 0)
        emit_float(lines, "ONAM.CellXOffset", onam, 4)
        emit_float(lines, "ONAM.CellYOffset", onam, 8)
    emit_float(lines, "NAM4.LODWaterHeight", get_subrecord(rec, "NAM4"))
    dnam = get_subrecord(rec, "DNAM")
    if dnam and len(dnam.data) >= 8:
        emit_float(lines, "DNAM.DefaultLandHeight", dnam, 0)
        emit_float(lines, "DNAM.DefaultWaterHeight", dnam, 4)


def _emit_navm_deltas(lines: list, rec: Record):
    """NAVM's authored geometry: the cell it covers, its vertices and triangles.

    FO3/FNV ship real navmeshes where TES4 has only pathgrids, so this is
    authored data to repack rather than geometry to generate. NVVX/NVTR/NVDP
    are dumped verbatim; the importer reinterprets them into TES5's NVNM blob.

    See: docs/commentary/tes4_export_falloutnv.md#navmesh-authored-not-generated
    """
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 24:
        cell, nvert, ntri, nedge, ncover, ndoor = struct.unpack_from(
            "<6I", data.data, 0)
        lines.append(f"DATA.Cell={get_formid_str(cell)}")
        lines.append(f"DATA.VertexCount={nvert}")
        lines.append(f"DATA.TriangleCount={ntri}")
        lines.append(f"DATA.EdgeLinkCount={nedge}")
        lines.append(f"DATA.CoverTriangleCount={ncover}")
        lines.append(f"DATA.DoorLinkCount={ndoor}")

    for sig in ("NVVX", "NVTR", "NVDP"):
        emit_raw_hex(lines, sig, get_subrecord(rec, sig))


def _emit_navi_deltas(lines: list, rec: Record):
    """NAVI's per-navmesh info entries, one NVMI blob per line.

    NVMI repeats thousands of times in a single record, so each is emitted
    with its ordinal plus a total count.

    See: docs/commentary/tes4_export_falloutnv.md#navmesh-authored-not-generated
    """
    index = 0
    for sub_rec in rec.subrecords:
        if sub_rec.type != "NVMI":
            continue
        lines.append(f"NVMI[{index}]={sub_rec.data.hex().upper()}")
        index += 1
    lines.append(f"NVMI.Count={index}")


#: TES4-layout actor keys the FO3/FNV ACBS/AIDT/DATA emitters below supersede.
SUPERSEDED_ACTOR_KEYS = (
    "ACBS.SpellPoints=", "ACBS.Fatigue=", "ACBS.BarterGold=", "ACBS.Level=",
    "ACBS.CalcMin=", "ACBS.CalcMax=",
    "AIDT.Services=", "AIDT.Teaches=", "AIDT.MaxTraining=",
    "DATA.Soul=", "DATA.Health=", "DATA.AttackDamage=",
)

#: FO3/FNV ACBS is 24 bytes and drops TES4's SpellPoints; TES4's is 16.
_FALLOUT_ACBS_SIZE = 24

#: Template Flags bit 6, 'Model/Animation' (wbDefinitionsCommon.pas:7715).
TEMPLATE_USE_MODEL = 1 << 6

#: FO3/FNV creature DATA attribute order; TES4's CREA DATA has none of these.
_CREA_ATTRIBUTES = ("Strength", "Perception", "Endurance", "Charisma",
                    "Intelligence", "Agility", "Luck")


def _emit_actor_acbs(lines: list, rec: Record):
    """FO3/FNV ACBS, whose fields sit two bytes earlier than TES4's.

    TES4 spends bytes 4-5 on SpellPoints; FO3/FNV has no such field and puts
    Fatigue there, shifting everything after it. Reading the TES4 layout
    yields a plausible-looking record made entirely of neighbouring fields.
    Template Flags at 22 has no TES4 counterpart at all.

    See: docs/commentary/tes4_export_falloutnv.md#acbs-lost-its-spellpoints
    """
    acbs = get_subrecord(rec, "ACBS")
    if not acbs or len(acbs.data) < _FALLOUT_ACBS_SIZE:
        return
    d = acbs.data
    fatigue, gold, level, calc_min, calc_max, speed = struct.unpack_from(
        "<6H", d, 4)
    disposition, template_flags = struct.unpack_from("<hH", d, 20)
    lines.append(f"ACBS.Fatigue={fatigue}")
    lines.append(f"ACBS.BarterGold={gold}")
    lines.append(f"ACBS.Level={level}")
    lines.append(f"ACBS.CalcMin={calc_min}")
    lines.append(f"ACBS.CalcMax={calc_max}")
    lines.append(f"ACBS.SpeedMultiplier={speed}")
    lines.append(f"ACBS.Karma={struct.unpack_from('<f', d, 16)[0]}")
    lines.append(f"ACBS.Disposition={disposition}")
    lines.append(f"ACBS.TemplateFlags={template_flags}")


#: FO3/FNV AIDT is 20 bytes: Mood at 4 pushes services to 8; TES4's is 12.
_FALLOUT_AIDT_SIZE = 20


def _emit_actor_aidt(lines: list, rec: Record):
    """FO3/FNV AIDT, whose services and trainer fields sit four bytes later.

    Offsets 0-3 are shared with TES4 and stay with the shared exporter; this
    emits Mood (4) and everything after the padding it introduces.

    See: docs/commentary/tes4_export_falloutnv.md#aidt-gained-a-mood-byte
    """
    aidt = get_subrecord(rec, "AIDT")
    if not aidt or len(aidt.data) < _FALLOUT_AIDT_SIZE:
        return
    d = aidt.data
    teaches, max_training, assistance = struct.unpack_from("<bBb", d, 12)
    lines.append(f"AIDT.Mood={d[4]}")
    lines.append(f"AIDT.Services={struct.unpack_from('<I', d, 8)[0]}")
    lines.append(f"AIDT.Teaches={teaches}")
    lines.append(f"AIDT.MaxTraining={max_training}")
    lines.append(f"AIDT.Assistance={assistance}")
    lines.append(f"AIDT.AggroRadiusBehavior={d[15]}")
    lines.append(f"AIDT.AggroRadius={struct.unpack_from('<i', d, 16)[0]}")


def _emit_actor_template(lines: list, rec: Record):
    """TPLT, the actor this record inherits its unowned categories from.

    See: docs/commentary/tes4_export_falloutnv.md#tplt-carries-the-whole-actor
    """
    tplt = get_subrecord(rec, "TPLT")
    if tplt and len(tplt.data) >= 4:
        fid = struct.unpack_from("<I", tplt.data, 0)[0]
        lines.append(f"TPLT.Template={get_formid_str(fid)}")


def _emit_crea_deltas(lines: list, rec: Record):
    """FO3/FNV CREA: the shifted ACBS, its template, and a 17-byte DATA.

    TES4's CREA DATA is 20 bytes with Soul and 8 attributes; FO3/FNV's is 17
    with neither, so the shared exporter's >= 20 guard silently emits nothing.
    """
    _emit_actor_acbs(lines, rec)
    _emit_actor_aidt(lines, rec)
    _emit_actor_template(lines, rec)
    data = get_subrecord(rec, "DATA")
    if not data or len(data.data) < 17:
        return
    d = data.data
    lines.append(f"DATA.Health={struct.unpack_from('<h', d, 4)[0]}")
    lines.append(f"DATA.AttackDamage={struct.unpack_from('<h', d, 8)[0]}")
    for i, name in enumerate(_CREA_ATTRIBUTES):
        lines.append(f"DATA.{name}={d[10 + i]}")


def _emit_npc_deltas(lines: list, rec: Record):
    """FO3/FNV NPC_: the same shifted ACBS, AIDT and template pointer as CREA."""
    _emit_actor_acbs(lines, rec)
    _emit_actor_aidt(lines, rec)
    _emit_actor_template(lines, rec)


#: MGEF FormID -> EditorID, rebuilt per source file by export_falloutnv.
MGEF_EDITOR_IDS = {}

#: FO3/FNV EFIT is 20 bytes; TES4's is 24 and repeats the 4-char code first.
_FALLOUT_EFIT_SIZE = 20

#: TES4 effect-type enum, matching common.emit_effects.
_EFFECT_TYPE_NAMES = {0: "Self", 1: "Touch", 2: "Target"}


def _add_effect_subrecord(groups: list, sub):
    """Attach one subrecord to the effect group it belongs to, by POSITION.

    A CTDA guards the effect it FOLLOWS, which is how a FO3/FNV consumable
    gates itself on hardcore mode; gathering per signature loses that pairing.
    """
    if sub.type == "EFID" and len(sub.data) >= 4:
        groups.append([struct.unpack_from("<I", sub.data, 0)[0], b"", []])
    elif not groups:
        return
    elif sub.type == "EFIT":
        groups[-1][1] = sub.data
    elif sub.type == "CTDA":
        groups[-1][2].append(sub.data)


def _emit_effect_deltas(lines: list, rec: Record):
    """FO3/FNV effects: EFID as a FormID, the 20-byte EFIT, and per-effect CTDA.

    The EFID resolves to its MGEF EditorID, which is what the import keys its
    effect registry on; a raw FormID would match nothing there.

    See: docs/commentary/tes4_export_falloutnv.md#effects-are-a-different-shape
    """
    groups = []
    for sub in rec.subrecords:
        _add_effect_subrecord(groups, sub)
    if not groups:
        return
    lines.append(f"EffectCount={len(groups)}")
    for i, (fid, efit, ctdas) in enumerate(groups):
        pfx = f"Effect[{i}]"
        lines.append(f"{pfx}.EFID={MGEF_EDITOR_IDS.get(fid, '')}")
        if len(efit) >= _FALLOUT_EFIT_SIZE:
            mag, area, dur, etype, av = struct.unpack_from("<IIIIi", efit, 0)
            lines.append(f"{pfx}.Magnitude={mag}")
            lines.append(f"{pfx}.Area={area}")
            lines.append(f"{pfx}.Duration={dur}")
            lines.append(f"{pfx}.Type={_EFFECT_TYPE_NAMES.get(etype, etype)}")
            lines.append(f"{pfx}.ActorValue={av}")
        lines.append(f"{pfx}.ConditionCount={len(ctdas)}")
        for j, raw in enumerate(ctdas):
            lines.append(f"{pfx}.Condition[{j}].Raw={raw.hex().upper()}")


#: Effect lines the TES4 emit_effects writes from a layout FO3/FNV does not use.
SUPERSEDED_EFFECT_KEYS = ("EffectCount=", "Effect[")

#: Effect-bearing types FO3/FNV lays out differently; SGST is Oblivion-only.
EFFECT_TYPES = frozenset({"SPEL", "ALCH", "ENCH", "INGR"})


#: Per-type delta emitters, consulted by format_record only for FO3/FNV sources.
_DELTA_DISPATCH = {
    "CELL": _emit_cell_deltas,
    "REFR": _emit_refr_deltas,
    "WEAP": _emit_weap_deltas,
    "AMMO": _emit_ammo_deltas,
    "NAVM": _emit_navm_deltas,
    "NAVI": _emit_navi_deltas,
    "WRLD": _emit_wrld_deltas,
    "CREA": _emit_crea_deltas,
    "NPC_": _emit_npc_deltas,
    "SPEL": _emit_effect_deltas,
    "ALCH": _emit_effect_deltas,
    "ENCH": _emit_effect_deltas,
    "INGR": _emit_effect_deltas,
    "QUST": emit_quest_deltas,
}

#: Types carrying an OBND that TES4 has no field for; Skyrim reads it natively.
_OBND_TYPES = frozenset({
    "STAT", "DOOR", "ACTI", "CONT", "FURN", "LIGH", "MISC", "KEYM",
    "BOOK", "TREE", "GRAS", "FLOR", "ALCH", "AMMO", "ARMO", "WEAP",
})


def export_deltas(rec: Record) -> list:
    """The FO3/FNV-only lines for one record, appended after its TES4 export."""
    lines = []
    if rec.type in _OBND_TYPES:
        _emit_obnd(lines, rec)
    handler = _DELTA_DISPATCH.get(rec.type)
    if handler:
        handler(lines, rec)
    return lines


def export_STATIC_BASE(rec: Record) -> list:
    """A model-only FO3/FNV base object, converted as a Skyrim STAT.

    MSTT, SCOL, PWAT and IDLM all reduce to a model plus bounds. They are the
    base objects of 10,000+ placed references; without them those REFRs have a
    null base and the engine faults promoting them into their location.

    See: docs/commentary/tes4_export_falloutnv.md#fallout-only-base-objects
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_model(lines, "Model", rec)
    _emit_obnd(lines, rec)
    return lines


def export_ACTIVATOR_BASE(rec: Record) -> list:
    """A named, scriptable FO3/FNV base object, converted as a Skyrim ACTI.

    TERM, NOTE and TACT are activators in all but signature: each carries a
    model, a display name and (for TACT/TERM) a script.

    See: docs/commentary/tes4_export_falloutnv.md#fallout-only-base-objects
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    emit_script(lines, rec)
    _emit_obnd(lines, rec)
    return lines


#: FO3/FNV base-object types Oblivion lacks -> the exporter that reduces them.
def export_NAVMESH(rec: Record) -> list:
    """A navmesh record's identity; export_deltas emits its geometry."""
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    return lines


def export_MESSAGE(rec: Record) -> list:
    """A FO3/FNV MESG, which Skyrim carries as the same record type.

    DESC/INAM/DNAM are required on the TES5 side; ITXT repeats once per button.

    See: docs/commentary/tes4_export_falloutnv.md#mesg-export
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "DESC", get_subrecord(rec, "DESC"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_u32(lines, "DNAM", get_subrecord(rec, "DNAM"))
    for i, sub in enumerate(get_all_subrecords(rec, "ITXT")):
        emit_string(lines, f"Button[{i}].Text", sub)
    return lines


def export_PROJECTILE(rec: Record) -> list:
    """A PROJ base record: its EditorID plus the projectile fields."""
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    _emit_proj_deltas(lines, rec)
    return lines


def export_FORMLIST(rec: Record) -> list:
    """A FLST: its EditorID and one `LNAM[i]` FormID per member, in order.
    A gun's `NAM0` names one of these (its ammo list), not an AMMO.
    See: docs/commentary/tes4_export_falloutnv.md#formlists
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    for i, sub in enumerate(get_all_subrecords(rec, "LNAM")):
        emit_formid(lines, f"LNAM[{i}]", sub)
    return lines


#: FNV TXST texture slots; TES5 adds TX06/TX07, which FO3/FNV never author.
TEXTURE_SLOTS = ("TX00", "TX01", "TX02", "TX03", "TX04", "TX05")


def export_TEXTURESET(rec: Record) -> list:
    """A TXST: its six texture paths, bounds and DNAM flags.

    See: docs/commentary/tes4_export_falloutnv.md#texture-sets
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    for slot in TEXTURE_SLOTS:
        emit_string(lines, slot, get_subrecord(rec, slot))
    emit_u16(lines, "DNAM.Flags", get_subrecord(rec, "DNAM"))
    _emit_obnd(lines, rec)
    return lines


def export_IMAGESPACE(rec: Record) -> list:
    """An IMGS: its 152-byte DNAM tone-mapping block, verbatim.

    See: docs/commentary/tes4_export_falloutnv.md#imagespaces
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_raw_hex(lines, "DNAM", get_subrecord(rec, "DNAM"))
    return lines


def export_LIGHTINGTEMPLATE(rec: Record) -> list:
    """An LGTM: the 40-byte DATA lighting block, verbatim.

    See: docs/commentary/tes4_export_falloutnv.md#lighting-templates
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_raw_hex(lines, "DATA", get_subrecord(rec, "DATA"))
    return lines


def export_ENCOUNTERZONE(rec: Record) -> list:
    """An ECZN: owner, rank, minimum level and reset flags.

    See: docs/commentary/tes4_export_falloutnv.md#encounter-zones
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 8:
        emit_formid(lines, "DATA.Owner", data)
        rank, min_level = struct.unpack_from("<2b", data.data, 4)
        lines.append(f"DATA.Rank={rank}")
        lines.append(f"DATA.MinLevel={min_level}")
        lines.append(f"DATA.Flags={data.data[6]}")
    return lines


def export_MUSICTYPE(rec: Record) -> list:
    """A MUSC: the track file it names and its dB gain.

    See: docs/commentary/tes4_export_falloutnv.md#music-types
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FNAM.FileName", get_subrecord(rec, "FNAM"))
    emit_float(lines, "ANAM.DB", get_subrecord(rec, "ANAM"))
    return lines


#: FNV IPDS DATA: twelve IPCT FormIDs in this material order (wbDefinitionsFNV).
IMPACT_MATERIALS = ("Stone", "Dirt", "Grass", "Glass", "Metal", "Wood",
                    "Organic", "Cloth", "Water", "HollowMetal", "OrganicBug",
                    "OrganicGlow")


def export_IMPACT(rec: Record) -> list:
    """An IPCT: model, the 24-byte DATA and 36-byte DODT decal data as hex,
    its decal TXST (`DNAM`) and its two SOUN links.
    See: docs/commentary/tes4_export_falloutnv.md#impacts
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_model(lines, "Model", rec)
    emit_raw_hex(lines, "DATA", get_subrecord(rec, "DATA"))
    emit_raw_hex(lines, "DODT", get_subrecord(rec, "DODT"))
    emit_formid(lines, "DNAM", get_subrecord(rec, "DNAM"))
    emit_formid(lines, "SNAM", get_subrecord(rec, "SNAM"))
    emit_formid(lines, "NAM1", get_subrecord(rec, "NAM1"))
    return lines


def export_IMPACTSET(rec: Record) -> list:
    """An IPDS: one `DATA.<material>` IPCT FormID per IMPACT_MATERIALS slot.
    See: docs/commentary/tes4_export_falloutnv.md#impacts
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    data = get_subrecord(rec, "DATA")
    if data:
        for i, key in enumerate(IMPACT_MATERIALS[:len(data.data) // 4]):
            fid = struct.unpack_from("<I", data.data, 4 * i)[0]
            lines.append(f"DATA.{key}={get_formid_str(fid)}")
    return lines


#: FNV EXPL DATA (wbDefinitionsFNV): (key, offset, kind) for every field TES5 keeps.
EXPLOSION_FIELDS = (("Force", 0, "f"), ("Damage", 4, "f"), ("Radius", 8, "f"),
                    ("Light", 12, "id"), ("Sound1", 16, "id"), ("Flags", 20, "u"),
                    ("ISRadius", 24, "f"), ("ImpactDataSet", 28, "id"),
                    ("Sound2", 32, "id"), ("SoundLevel", 48, "u"))


def export_EXPLOSION(rec: Record) -> list:
    """An EXPL: bounds, name, model, enchantment (`EITM`), image-space
    modifier (`MNAM`), the DATA fields TES5 shares and the placed impact
    object (`INAM`). FNV's radiation block has no TES5 field.
    See: docs/commentary/tes4_export_falloutnv.md#explosions
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    _emit_obnd(lines, rec)
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_model(lines, "Model", rec)
    for sig in ("EITM", "MNAM", "INAM"):
        emit_formid(lines, sig, get_subrecord(rec, sig))
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 52:
        for key, off, kind in EXPLOSION_FIELDS:
            val = struct.unpack_from("<f" if kind == "f" else "<I", data.data, off)[0]
            lines.append(f"DATA.{key}={get_formid_str(val) if kind == 'id' else val}")
    return lines


def export_ADDON(rec: Record) -> list:
    """An ADDN: bounds, model, node index (`DATA`), sound and the 4-byte DNAM.
    See: docs/commentary/tes4_export_falloutnv.md#addon-nodes
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    _emit_obnd(lines, rec)
    emit_model(lines, "Model", rec)
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 4:
        lines.append(f"DATA.Index={struct.unpack_from('<i', data.data)[0]}")
    emit_formid(lines, "SNAM", get_subrecord(rec, "SNAM"))
    emit_raw_hex(lines, "DNAM", get_subrecord(rec, "DNAM"))
    return lines


def export_LEVELED_NPC(rec: Record) -> list:
    """A FO3/FNV LVLN, which Skyrim carries as the same record type.

    Structurally LVLI: chance-none, flags and an LVLO array whose entries are
    Level(u16) pad(2) FormID(4) Count(s16), the count tail optional.

    See: docs/commentary/tes4_export_falloutnv.md#lvln-is-a-native-type
    """
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_u8(lines, "LVLD.ChanceNone", get_subrecord(rec, "LVLD"))
    emit_u8(lines, "LVLF.Flags", get_subrecord(rec, "LVLF"))
    lvlos = get_all_subrecords(rec, "LVLO")
    lines.append(f"EntryCount={len(lvlos)}")
    for i, lvlo in enumerate(lvlos):
        d = lvlo.data
        if len(d) < 8:
            continue
        lines.append(f"Entry[{i}].Level={struct.unpack_from('<H', d, 0)[0]}")
        lines.append(
            f"Entry[{i}].FormID={get_formid_str(struct.unpack_from('<I', d, 4)[0])}")
        count = struct.unpack_from('<h', d, 8)[0] if len(d) >= 10 else 1
        lines.append(f"Entry[{i}].Count={count}")
    return lines


FALLOUT_BASE_EXPORTERS = {
    "LVLN": export_LEVELED_NPC,
    "MESG": export_MESSAGE,
    "PROJ": export_PROJECTILE,
    "FLST": export_FORMLIST,
    "TXST": export_TEXTURESET,
    "IMGS": export_IMAGESPACE,
    "LGTM": export_LIGHTINGTEMPLATE,
    "ECZN": export_ENCOUNTERZONE,
    "MUSC": export_MUSICTYPE,
    "IPCT": export_IMPACT,
    "IPDS": export_IMPACTSET,
    "EXPL": export_EXPLOSION,
    "ADDN": export_ADDON,
    "NAVM": export_NAVMESH,
    "NAVI": export_NAVMESH,
    "MSTT": export_STATIC_BASE,
    "SCOL": export_STATIC_BASE,
    "PWAT": export_STATIC_BASE,
    "IDLM": export_STATIC_BASE,
    "ASPC": export_STATIC_BASE,
    "TERM": export_ACTIVATOR_BASE,
    "NOTE": export_ACTIVATOR_BASE,
    "TACT": export_ACTIVATOR_BASE,
}

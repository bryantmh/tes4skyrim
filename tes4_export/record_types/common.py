"""
Common subrecord handling shared across record types.

These are subrecords that appear in many different record types with the
same structure (EDID, FULL, MODL, ICON, etc.). Handles model data,
string extraction, and other commonly-reused patterns.
"""

import struct

from ..tes4_reader import Record, Subrecord, get_all_subrecords, get_formid_str, get_string, get_subrecord


def escape_value(value: str) -> str:
    """Escape special characters in an export value string."""
    return (value
            .replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t"))


def emit_string(lines: list, key: str, sub: Subrecord):
    """Emit a string subrecord if present."""
    if sub:
        lines.append(f"{key}={escape_value(get_string(sub))}")


def emit_formid(lines: list, key: str, sub: Subrecord):
    """Emit a FormID subrecord if present."""
    if sub and len(sub.data) >= 4:
        fid = struct.unpack_from("<I", sub.data, 0)[0]
        lines.append(f"{key}={get_formid_str(fid)}")


def emit_float(lines: list, key: str, sub: Subrecord, offset: int = 0):
    """Emit a float value from a subrecord."""
    if sub and len(sub.data) >= offset + 4:
        val = struct.unpack_from("<f", sub.data, offset)[0]
        lines.append(f"{key}={val}")


def emit_u8(lines: list, key: str, sub: Subrecord, offset: int = 0):
    if sub and len(sub.data) > offset:
        lines.append(f"{key}={sub.data[offset]}")


def emit_u16(lines: list, key: str, sub: Subrecord, offset: int = 0):
    if sub and len(sub.data) >= offset + 2:
        lines.append(f"{key}={struct.unpack_from('<H', sub.data, offset)[0]}")


def emit_u32(lines: list, key: str, sub: Subrecord, offset: int = 0):
    if sub and len(sub.data) >= offset + 4:
        lines.append(f"{key}={struct.unpack_from('<I', sub.data, offset)[0]}")


def emit_s16(lines: list, key: str, sub: Subrecord, offset: int = 0):
    if sub and len(sub.data) >= offset + 2:
        lines.append(f"{key}={struct.unpack_from('<h', sub.data, offset)[0]}")


def emit_s32(lines: list, key: str, sub: Subrecord, offset: int = 0):
    if sub and len(sub.data) >= offset + 4:
        lines.append(f"{key}={struct.unpack_from('<i', sub.data, offset)[0]}")


def emit_raw_hex(lines: list, key: str, sub: Subrecord):
    """Emit raw bytes as a hex string."""
    if sub:
        lines.append(f"{key}={sub.data.hex().upper()}")


def emit_model(lines: list, prefix: str, rec: Record, sig_modl: str = "MODL",
               sig_modb: str = "MODB", sig_modt: str = "MODT"):
    """Emit model subrecords (MODL + MODB + MODT)."""
    modl = get_subrecord(rec, sig_modl)
    if modl:
        path = get_string(modl)
        lines.append(f"{prefix}.MODL={escape_value(path)}")
    modb = get_subrecord(rec, sig_modb)
    if modb and len(modb.data) >= 4:
        val = struct.unpack_from("<f", modb.data, 0)[0]
        lines.append(f"{prefix}.MODB={val}")


def emit_icon(lines: list, key: str, rec: Record, sig: str = "ICON"):
    """Emit icon path subrecord."""
    icon = get_subrecord(rec, sig)
    if icon:
        lines.append(f"{key}={escape_value(get_string(icon))}")


def emit_script(lines: list, rec: Record):
    """Emit SCRI (script FormID) if present."""
    emit_formid(lines, "SCRI", get_subrecord(rec, "SCRI"))


def emit_enchantment(lines: list, rec: Record):
    """Emit ENAM (enchantment FormID) and ANAM (enchant points)."""
    emit_formid(lines, "ENAM", get_subrecord(rec, "ENAM"))
    emit_u16(lines, "ANAM", get_subrecord(rec, "ANAM"))


def emit_effects(lines: list, rec: Record):
    """Emit magic effects (EFID/EFIT/SCIT chain).

    TES4 effect structure: repeating groups of EFID + EFIT [+ SCIT] [+ FULL].
    EFID = 4-char effect code
    EFIT = struct(12): u32 magnitude, u32 area, u32 duration, u32 type, u32 actorValue
    SCIT = struct(12): formid scriptEffect, u32 school, 4-char visualEffect
    """
    efids = get_all_subrecords(rec, "EFID")
    efits = get_all_subrecords(rec, "EFIT")

    count = min(len(efids), len(efits))
    lines.append(f"EffectCount={count}")

    # TES4 effect type enum
    effect_type_names = {0: "Self", 1: "Touch", 2: "Target"}

    for i in range(count):
        pfx = f"Effect[{i}]"
        # EFID = 4-char code
        efid_code = efids[i].data[:4].decode("ascii", errors="replace") if len(efids[i].data) >= 4 else ""
        lines.append(f"{pfx}.EFID={efid_code}")

        # EFIT (24 bytes): EffectCode[4] + Magnitude[4] + Area[4] + Duration[4] + Type[4] + ActorValue[4]
        efit_data = efits[i].data
        if len(efit_data) >= 24:
            magnitude = struct.unpack_from("<I", efit_data, 4)[0]
            area = struct.unpack_from("<I", efit_data, 8)[0]
            duration = struct.unpack_from("<I", efit_data, 12)[0]
            etype = struct.unpack_from("<I", efit_data, 16)[0]
            actor_val = struct.unpack_from("<i", efit_data, 20)[0]
            lines.append(f"{pfx}.Magnitude={magnitude}")
            lines.append(f"{pfx}.Area={area}")
            lines.append(f"{pfx}.Duration={duration}")
            lines.append(f"{pfx}.Type={effect_type_names.get(etype, str(etype))}")
            lines.append(f"{pfx}.ActorValue={actor_val}")

    # SCIT entries (script effects)
    scits = get_all_subrecords(rec, "SCIT")
    for i, scit in enumerate(scits):
        pfx = f"ScriptEffect[{i}]"
        if len(scit.data) >= 12:
            se_formid = struct.unpack_from("<I", scit.data, 0)[0]
            se_school = struct.unpack_from("<I", scit.data, 4)[0]
            se_visual = scit.data[8:12].decode("ascii", errors="replace")
            lines.append(f"{pfx}.FormID={get_formid_str(se_formid)}")
            lines.append(f"{pfx}.School={se_school}")
            lines.append(f"{pfx}.Visual={se_visual}")


def emit_conditions(lines: list, rec: Record, prefix: str = "Condition"):
    """Emit CTDA condition subrecords."""
    ctdas = get_all_subrecords(rec, "CTDA")
    if ctdas:
        lines.append(f"{prefix}Count={len(ctdas)}")
    for i, ctda in enumerate(ctdas):
        pfx = f"{prefix}[{i}]"
        if len(ctda.data) >= 20:  # TES4 CTDA minimum 20 bytes (typically 24)
            lines.append(f"{pfx}.Raw={ctda.data.hex()}")

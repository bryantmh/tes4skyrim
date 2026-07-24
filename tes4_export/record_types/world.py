"""
World and placement record types: CELL, WRLD, REFR, ACHR, ACRE, LAND, LTEX,
REGN, ROAD, PGRD.

Pure TES4 data dump - no transformations.
"""

import struct

from ..tes4_reader import Record, get_all_subrecords, get_formid_str, get_subrecord
from .common import (
    emit_float,
    emit_formid,
    emit_icon,
    emit_s32,
    emit_string,
    emit_u8,
)


def export_CELL(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))

    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 1:
        lines.append(f"DATA.Flags={data.data[0]}")

    xclc = get_subrecord(rec, "XCLC")
    if xclc and len(xclc.data) >= 8:
        lines.append(f"XCLC.X={struct.unpack_from('<i', xclc.data, 0)[0]}")
        lines.append(f"XCLC.Y={struct.unpack_from('<i', xclc.data, 4)[0]}")

    # XCLR — region FormIDs (array of 4-byte FormIDs)
    xclr_subs = get_all_subrecords(rec, "XCLR")
    if xclr_subs:
        reg_idx = 0
        for xclr in xclr_subs:
            for off in range(0, len(xclr.data), 4):
                if off + 4 <= len(xclr.data):
                    fid = struct.unpack_from('<I', xclr.data, off)[0]
                    lines.append(f"Region[{reg_idx}]={get_formid_str(fid)}")
                    reg_idx += 1

    # Interior lighting (XCLL)
    xcll = get_subrecord(rec, "XCLL")
    if xcll and len(xcll.data) >= 36:
        d = xcll.data
        lines.append(f"XCLL.AmbientR={d[0]}")
        lines.append(f"XCLL.AmbientG={d[1]}")
        lines.append(f"XCLL.AmbientB={d[2]}")
        lines.append(f"XCLL.DirectionalR={d[4]}")
        lines.append(f"XCLL.DirectionalG={d[5]}")
        lines.append(f"XCLL.DirectionalB={d[6]}")
        lines.append(f"XCLL.FogR={d[8]}")
        lines.append(f"XCLL.FogG={d[9]}")
        lines.append(f"XCLL.FogB={d[10]}")
        lines.append(f"XCLL.FogNear={struct.unpack_from('<f', d, 12)[0]}")
        lines.append(f"XCLL.FogFar={struct.unpack_from('<f', d, 16)[0]}")
        lines.append(f"XCLL.DirectionalRotXY={struct.unpack_from('<i', d, 20)[0]}")
        lines.append(f"XCLL.DirectionalRotZ={struct.unpack_from('<i', d, 24)[0]}")
        lines.append(f"XCLL.DirectionalFade={struct.unpack_from('<f', d, 28)[0]}")
        lines.append(f"XCLL.FogClipDist={struct.unpack_from('<f', d, 32)[0]}")

    emit_formid(lines, "XOWN.Owner", get_subrecord(rec, "XOWN"))
    emit_formid(lines, "XGLB.Global", get_subrecord(rec, "XGLB"))
    emit_s32(lines, "XRNK.Rank", get_subrecord(rec, "XRNK"))
    emit_formid(lines, "XCCM.Climate", get_subrecord(rec, "XCCM"))
    emit_formid(lines, "XCWT.Water", get_subrecord(rec, "XCWT"))
    xclw = get_subrecord(rec, "XCLW")
    if xclw and len(xclw.data) >= 4:
        lines.append(f"XCLW.WaterHeight={struct.unpack_from('<f', xclw.data, 0)[0]}")
    emit_u8(lines, "XCMT.MusicType", get_subrecord(rec, "XCMT"))

    return lines


def export_WRLD(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_string(lines, "FULL", get_subrecord(rec, "FULL"))
    emit_formid(lines, "WNAM.Parent", get_subrecord(rec, "WNAM"))
    emit_formid(lines, "CNAM.Climate", get_subrecord(rec, "CNAM"))
    emit_formid(lines, "NAM2.Water", get_subrecord(rec, "NAM2"))
    emit_icon(lines, "ICON", rec)

    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 1:
        lines.append(f"DATA.Flags={data.data[0]}")

    mnam = get_subrecord(rec, "MNAM")
    if mnam and len(mnam.data) >= 16:
        d = mnam.data
        lines.append(f"MNAM.UsableDimX={struct.unpack_from('<i', d, 0)[0]}")
        lines.append(f"MNAM.UsableDimY={struct.unpack_from('<i', d, 4)[0]}")
        lines.append(f"MNAM.NWCellX={struct.unpack_from('<h', d, 8)[0]}")
        lines.append(f"MNAM.NWCellY={struct.unpack_from('<h', d, 10)[0]}")
        lines.append(f"MNAM.SECellX={struct.unpack_from('<h', d, 12)[0]}")
        lines.append(f"MNAM.SECellY={struct.unpack_from('<h', d, 14)[0]}")

    snam = get_subrecord(rec, "SNAM")
    if snam and len(snam.data) >= 4:
        lines.append(f"SNAM.Music={struct.unpack_from('<I', snam.data, 0)[0]}")

    nam0 = get_subrecord(rec, "NAM0")
    if nam0 and len(nam0.data) >= 8:
        lines.append(f"NAM0.MinX={struct.unpack_from('<f', nam0.data, 0)[0]}")
        lines.append(f"NAM0.MinY={struct.unpack_from('<f', nam0.data, 4)[0]}")
    nam9 = get_subrecord(rec, "NAM9")
    if nam9 and len(nam9.data) >= 8:
        lines.append(f"NAM9.MaxX={struct.unpack_from('<f', nam9.data, 0)[0]}")
        lines.append(f"NAM9.MaxY={struct.unpack_from('<f', nam9.data, 4)[0]}")

    return lines


def _emit_placement(lines: list, rec: Record):
    """Emit position/rotation DATA for placed references."""
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 24:
        d = data.data
        lines.append(f"PosX={struct.unpack_from('<f', d, 0)[0]}")
        lines.append(f"PosY={struct.unpack_from('<f', d, 4)[0]}")
        lines.append(f"PosZ={struct.unpack_from('<f', d, 8)[0]}")
        lines.append(f"RotX={struct.unpack_from('<f', d, 12)[0]}")
        lines.append(f"RotY={struct.unpack_from('<f', d, 16)[0]}")
        lines.append(f"RotZ={struct.unpack_from('<f', d, 20)[0]}")


def export_REFR(rec: Record) -> list:
    lines = []
    # VWD (Visible When Distant) flag — used for LOD
    if getattr(rec, 'is_vwd', False):
        lines.append("VWD=1")
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_formid(lines, "NAME", get_subrecord(rec, "NAME"))

    # Ownership
    emit_formid(lines, "XOWN.Owner", get_subrecord(rec, "XOWN"))
    emit_s32(lines, "XRNK.Rank", get_subrecord(rec, "XRNK"))
    emit_formid(lines, "XGLB.Global", get_subrecord(rec, "XGLB"))

    # Enable parent
    xesp = get_subrecord(rec, "XESP")
    if xesp and len(xesp.data) >= 8:
        lines.append(f"XESP.Reference={get_formid_str(struct.unpack_from('<I', xesp.data, 0)[0])}")
        lines.append(f"XESP.Flags={xesp.data[4]}")

    # Teleport door
    xtel = get_subrecord(rec, "XTEL")
    if xtel and len(xtel.data) >= 28:
        d = xtel.data
        lines.append(f"XTEL.Door={get_formid_str(struct.unpack_from('<I', d, 0)[0])}")
        lines.append(f"XTEL.PosX={struct.unpack_from('<f', d, 4)[0]}")
        lines.append(f"XTEL.PosY={struct.unpack_from('<f', d, 8)[0]}")
        lines.append(f"XTEL.PosZ={struct.unpack_from('<f', d, 12)[0]}")
        lines.append(f"XTEL.RotX={struct.unpack_from('<f', d, 16)[0]}")
        lines.append(f"XTEL.RotY={struct.unpack_from('<f', d, 20)[0]}")
        lines.append(f"XTEL.RotZ={struct.unpack_from('<f', d, 24)[0]}")

    # Lock — XLOC is 12 bytes (Level+Unused3+Key) or 16 bytes (+4-byte filler)
    # before the trailing Flags+Unused3. Flags sits right after Key either way.
    xloc = get_subrecord(rec, "XLOC")
    if xloc and len(xloc.data) >= 9:
        lines.append(f"XLOC.Level={xloc.data[0]}")
        lines.append(f"XLOC.Key={get_formid_str(struct.unpack_from('<I', xloc.data, 4)[0])}")
        flags_offset = 12 if len(xloc.data) >= 13 else 8
        lines.append(f"XLOC.Flags={xloc.data[flags_offset]}")

    # Map Marker
    xmrk = get_subrecord(rec, "XMRK")
    if xmrk:
        lines.append("MapMarker=1")
        # FNAM map flags: 0x01 Visible, 0x02 Can Travel To
        fnam = get_subrecord(rec, "FNAM")
        if fnam and fnam.data:
            lines.append(f"MapMarker.Flags={fnam.data[0]}")
        emit_string(lines, "MapMarker.FULL", get_subrecord(rec, "FULL"))
        tnam = get_subrecord(rec, "TNAM")
        if tnam and len(tnam.data) >= 2:
            lines.append(f"MapMarker.Type={tnam.data[0]}")

    emit_float(lines, "XSCL.Scale", get_subrecord(rec, "XSCL"))
    emit_formid(lines, "XTRG.Target", get_subrecord(rec, "XTRG"))

    # Primitive data
    xprd = get_subrecord(rec, "XPRD")
    if xprd and len(xprd.data) >= 4:
        lines.append(f"XPRD.IdleTime={struct.unpack_from('<f', xprd.data, 0)[0]}")

    _emit_placement(lines, rec)
    return lines


def export_ACHR(rec: Record) -> list:
    lines = []
    # VWD (Visible When Distant) flag — used for LOD
    if getattr(rec, 'is_vwd', False):
        lines.append("VWD=1")
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_formid(lines, "NAME", get_subrecord(rec, "NAME"))

    xesp = get_subrecord(rec, "XESP")
    if xesp and len(xesp.data) >= 8:
        lines.append(f"XESP.Reference={get_formid_str(struct.unpack_from('<I', xesp.data, 0)[0])}")
        lines.append(f"XESP.Flags={xesp.data[4]}")

    emit_formid(lines, "XMRC.MerchantContainer", get_subrecord(rec, "XMRC"))
    emit_formid(lines, "XHRS.Horse", get_subrecord(rec, "XHRS"))
    emit_float(lines, "XSCL.Scale", get_subrecord(rec, "XSCL"))
    _emit_placement(lines, rec)
    return lines


def export_ACRE(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_formid(lines, "NAME", get_subrecord(rec, "NAME"))

    emit_formid(lines, "XOWN.Owner", get_subrecord(rec, "XOWN"))
    emit_s32(lines, "XRNK.Rank", get_subrecord(rec, "XRNK"))
    emit_formid(lines, "XGLB.Global", get_subrecord(rec, "XGLB"))

    xesp = get_subrecord(rec, "XESP")
    if xesp and len(xesp.data) >= 8:
        lines.append(f"XESP.Reference={get_formid_str(struct.unpack_from('<I', xesp.data, 0)[0])}")
        lines.append(f"XESP.Flags={xesp.data[4]}")

    emit_float(lines, "XSCL.Scale", get_subrecord(rec, "XSCL"))
    _emit_placement(lines, rec)
    return lines


def export_LAND(rec: Record) -> list:
    """Export LAND record (landscape) - height/texture data."""
    lines = []
    data = get_subrecord(rec, "DATA")
    if data and len(data.data) >= 4:
        lines.append(f"DATA.Flags={struct.unpack_from('<I', data.data, 0)[0]}")

    # VNML - Vertex normals (full binary, hex-encoded)
    vnml = get_subrecord(rec, "VNML")
    if vnml:
        lines.append(f"VNML={vnml.data.hex().upper()}")

    # VHGT - Vertex heights (full binary, hex-encoded)
    # Format: float HeightOffset (4 bytes) + 33 rows × 33 S8 deltas + 3 pad bytes.
    # Decode: row_base = HeightOffset; for each row, col_base = row_base; for each
    # cell, height += delta[cell]; absolute_height = col_base * 8.0.  The 33×33
    # grid covers one cell (4096 units), so vertex spacing is 128 units.
    # NOTE: the navmesh converter (pgrd_to_navm.py) needs this to sample real Z.
    vhgt = get_subrecord(rec, "VHGT")
    if vhgt:
        lines.append(f"VHGT={vhgt.data.hex().upper()}")

    # VCLR - Vertex colors (full binary, hex-encoded)
    vclr = get_subrecord(rec, "VCLR")
    if vclr:
        lines.append(f"VCLR={vclr.data.hex().upper()}")

    # Layers: BTXT (base) and ATXT/VTXT (alpha) in a flat array
    _emit_layers(lines, rec)

    # VTEX - texture FormID list
    vtex = get_all_subrecords(rec, "VTEX")
    total_vtex = sum(len(v.data) // 4 for v in vtex)
    if total_vtex > 0:
        lines.append(f"VTEXCount={total_vtex}")
        idx = 0
        for v in vtex:
            for off in range(0, len(v.data), 4):
                if off + 4 <= len(v.data):
                    fid = struct.unpack_from("<I", v.data, off)[0]
                    lines.append(f"VTEX[{idx}]={get_formid_str(fid)}")
                    idx += 1

    return lines


def _emit_layers(lines: list, rec: Record):
    """Emit LAND landscape layers as a flat array of Base (BTXT) and Alpha (ATXT+VTXT)."""
    layers = []
    # Walk subrecords in order; BTXT starts a base layer, ATXT starts an alpha layer
    i = 0
    subs = rec.subrecords
    while i < len(subs):
        sub = subs[i]
        if sub.type == "BTXT" and len(sub.data) >= 8:
            tex = struct.unpack_from("<I", sub.data, 0)[0]
            quad = sub.data[4]
            layers.append({
                "type": "BASE",
                "texture": tex,
                "quadrant": quad,
            })
            i += 1
        elif sub.type == "ATXT" and len(sub.data) >= 8:
            tex = struct.unpack_from("<I", sub.data, 0)[0]
            quad = sub.data[4]
            layer_num = struct.unpack_from("<H", sub.data, 6)[0]
            vtxt_entries = []
            i += 1
            # Collect following VTXT
            if i < len(subs) and subs[i].type == "VTXT":
                vtxt_data = subs[i].data
                for off in range(0, len(vtxt_data), 8):
                    if off + 8 <= len(vtxt_data):
                        pos_val = struct.unpack_from("<H", vtxt_data, off)[0]
                        vtxt_data[off + 4]
                        opacity = struct.unpack_from("<f", vtxt_data, off + 4)[0]
                        vtxt_entries.append((pos_val, opacity))
                i += 1
            layers.append({
                "type": "ALPHA",
                "texture": tex,
                "quadrant": quad,
                "layer": layer_num,
                "vtxt": vtxt_entries,
            })
        else:
            i += 1

    if layers:
        lines.append(f"LayerCount={len(layers)}")
        for idx, lay in enumerate(layers):
            pfx = f"Layer[{idx}]"
            lines.append(f"{pfx}.Type={lay['type']}")
            if lay["type"] == "BASE":
                lines.append(f"{pfx}.BTXT.Texture={get_formid_str(lay['texture'])}")
                lines.append(f"{pfx}.BTXT.Quadrant={lay['quadrant']}")
            else:
                lines.append(f"{pfx}.ATXT.Texture={get_formid_str(lay['texture'])}")
                lines.append(f"{pfx}.ATXT.Quadrant={lay['quadrant']}")
                lines.append(f"{pfx}.ATXT.Layer={lay['layer']}")
                lines.append(f"{pfx}.VTXTCount={len(lay['vtxt'])}")
                for vi, (vpos, vop) in enumerate(lay["vtxt"]):
                    lines.append(f"{pfx}.VT[{vi}].Pos={vpos}")
                    lines.append(f"{pfx}.VT[{vi}].Opacity={vop}")


def export_LTEX(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_icon(lines, "ICON", rec)
    hnam = get_subrecord(rec, "HNAM")
    if hnam and len(hnam.data) >= 3:
        lines.append(f"HNAM.Material={hnam.data[0]}")
        lines.append(f"HNAM.Friction={hnam.data[1]}")
        lines.append(f"HNAM.Restitution={hnam.data[2]}")
    snam = get_subrecord(rec, "SNAM")
    if snam and len(snam.data) >= 1:
        lines.append(f"SNAM.Specular={snam.data[0]}")
    gnam = get_all_subrecords(rec, "GNAM")
    if gnam:
        lines.append(f"GrassCount={len(gnam)}")
        for i, g in enumerate(gnam):
            if len(g.data) >= 4:
                lines.append(f"Grass[{i}]={get_formid_str(struct.unpack_from('<I', g.data, 0)[0])}")
    return lines


def export_REGN(rec: Record) -> list:
    lines = []
    emit_string(lines, "EditorID", get_subrecord(rec, "EDID"))
    emit_icon(lines, "ICON", rec)
    rclr = get_subrecord(rec, "RCLR")
    if rclr and len(rclr.data) >= 3:
        lines.append(f"RCLR.R={rclr.data[0]}")
        lines.append(f"RCLR.G={rclr.data[1]}")
        lines.append(f"RCLR.B={rclr.data[2]}")
    emit_formid(lines, "WNAM.Worldspace", get_subrecord(rec, "WNAM"))
    # Region data entries (RDAT/RDOT/RDMP/RDGS/RDMD/RDSD/RDWT)
    rdats = get_all_subrecords(rec, "RDAT")
    if rdats:
        lines.append(f"RegionDataCount={len(rdats)}")
        for i, rdat in enumerate(rdats):
            if len(rdat.data) >= 8:
                lines.append(f"RegionData[{i}].Type={struct.unpack_from('<I', rdat.data, 0)[0]}")
                lines.append(f"RegionData[{i}].Flags={rdat.data[4]}")
    return lines


def export_ROAD(rec: Record) -> list:
    lines = []
    # PGRP — road points (each 16 bytes: X(f), Y(f), Z(f), Connections(u8), pad(3))
    pgrp = get_subrecord(rec, "PGRP")
    if pgrp:
        count = len(pgrp.data) // 16
        lines.append(f"PointCount={count}")
        for i in range(count):
            off = i * 16
            if off + 16 <= len(pgrp.data):
                x, y, z = struct.unpack_from('<fff', pgrp.data, off)
                conn = pgrp.data[off + 12]
                lines.append(f"Point[{i}].X={x}")
                lines.append(f"Point[{i}].Y={y}")
                lines.append(f"Point[{i}].Z={z}")
                lines.append(f"Point[{i}].Connections={conn}")
    # PGRR — road connections (variable size, point indices)
    pgrr = get_subrecord(rec, "PGRR")
    if pgrr:
        lines.append(f"PGRR.Hex={pgrr.data.hex()}")
    return lines


def export_PGRD(rec: Record) -> list:
    lines = []
    data = get_subrecord(rec, "DATA")
    point_count = 0
    if data and len(data.data) >= 2:
        point_count = struct.unpack_from('<H', data.data, 0)[0]
        lines.append(f"DATA.PointCount={point_count}")

    # PGRP — pathgrid points (each 16 bytes: X(f), Y(f), Z(f), Connections(u8), pad(3))
    # Collect connection counts so we can parse PGRR correctly
    conn_counts = []
    pgrp = get_subrecord(rec, "PGRP")
    if pgrp:
        actual_count = len(pgrp.data) // 16
        for i in range(actual_count):
            off = i * 16
            if off + 16 <= len(pgrp.data):
                x, y, z = struct.unpack_from('<fff', pgrp.data, off)
                conn = pgrp.data[off + 12]
                conn_counts.append(conn)
                lines.append(f"Point[{i}].X={x}")
                lines.append(f"Point[{i}].Y={y}")
                lines.append(f"Point[{i}].Z={z}")
                lines.append(f"Point[{i}].Connections={conn}")

    # PGRR — point-to-point connections (flat S16 array, grouped by PGRP.Connections count)
    # For point i, PGRR contains conn_counts[i] consecutive S16 neighbour indices.
    pgrr = get_subrecord(rec, "PGRR")
    if pgrr and conn_counts:
        total_available = len(pgrr.data) // 2
        s16_values = list(struct.unpack_from(f'<{total_available}h', pgrr.data))
        flat_idx = 0
        for i, count in enumerate(conn_counts):
            for j in range(count):
                if flat_idx < total_available:
                    target = s16_values[flat_idx]
                    lines.append(f"Point[{i}].Edge[{j}]={target}")
                    flat_idx += 1

    # PGRI — inter-cell connections (16 bytes each; UESP TES4 PGRD ref).
    # Offset 0: U32 local node number, offset 4/8/12: float X/Y/Z of the FOREIGN
    # node (world coords in the neighbouring cell).  The earlier 14-byte /
    # U16-local reading was WRONG on both count and field type: it misaligned
    # every entry after the first into uninitialised memory (denormal floats,
    # out-of-range node indices).  Used to build edge links between cell-border
    # navmeshes.
    pgri = get_subrecord(rec, "PGRI")
    if pgri:
        entry_count = len(pgri.data) // 16
        if entry_count > 0:
            lines.append(f"InterCellCount={entry_count}")
            for i in range(entry_count):
                off = i * 16
                if off + 16 <= len(pgri.data):
                    local_pt = struct.unpack_from('<I', pgri.data, off)[0]
                    x, y, z = struct.unpack_from('<fff', pgri.data, off + 4)
                    lines.append(f"InterCell[{i}].LocalPoint={local_pt}")
                    lines.append(f"InterCell[{i}].X={x}")
                    lines.append(f"InterCell[{i}].Y={y}")
                    lines.append(f"InterCell[{i}].Z={z}")

    # PGRL — point-to-reference mappings (FormID + array of U32 point indices)
    # Maps placed object references to the pathgrid points near them.
    # Used to populate NAVM.ONAM and to identify door-adjacent nodes.
    pgrl_subs = get_all_subrecords(rec, "PGRL")
    if pgrl_subs:
        lines.append(f"RefMapCount={len(pgrl_subs)}")
        for i, pgrl in enumerate(pgrl_subs):
            if len(pgrl.data) >= 4:
                ref_fid = struct.unpack_from('<I', pgrl.data, 0)[0]
                lines.append(f"RefMap[{i}].Reference={get_formid_str(ref_fid)}")
                pt_count = (len(pgrl.data) - 4) // 4
                for j in range(pt_count):
                    pt_idx = struct.unpack_from('<I', pgrl.data, 4 + j * 4)[0]
                    lines.append(f"RefMap[{i}].Point[{j}]={pt_idx}")

    return lines

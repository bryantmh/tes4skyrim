#!/usr/bin/env python3
"""
TES5 Plugin Verification & Dump Tool

Reads a TES5 .esp/.esm plugin in binary and produces a human-readable dump
of all records for verification. Checks for common conversion issues.

Usage:
  python verify_plugin.py <plugin_path> [options]

Options:
  --summary     Show only summary counts (default)
  --dump        Dump all records with subrecord details
  --type TYPE   Only show records of a specific type (e.g., NPC_, ARMO)
  --check       Run integrity checks for common TES4→TES5 conversion issues
  --formid ID   Dump a specific record by hex FormID (e.g., 00012345)
  --edid NAME   Find record by EditorID substring
"""

import argparse
import struct
import sys
import zlib
from collections import defaultdict
from pathlib import Path


# TES5 record header: type(4) + dataSize(4) + flags(4) + formID(4) + vc1(4) + formVersion(2) + vc2(2)
RECORD_HEADER_SIZE = 24
# Group header: type(4) + groupSize(4) + label(4) + groupType(4) + stamp(4) + vc(4)
GROUP_HEADER_SIZE = 24
# Subrecord header: type(4) + size(2)
SUBRECORD_HEADER_SIZE = 6

# Flag bits
FLAG_COMPRESSED = 0x00040000
FLAG_ESM = 0x00000001

# Record types that require OBND
OBND_REQUIRED = {
    "ACTI", "ALCH", "AMMO", "ARMO", "BOOK", "CONT", "DOOR", "ENCH",
    "FLOR", "FURN", "GRAS", "INGR", "KEYM", "LIGH", "LSCR", "LTEX",
    "MISC", "NPC_", "SCRL", "SLGM", "SOUN", "SPEL", "STAT", "TREE",
    "WEAP",
}


class Record:
    """Represents a parsed TES5 record."""
    __slots__ = ('type', 'data_size', 'flags', 'form_id', 'form_version',
                 'subrecords', 'editor_id', 'offset')

    def __init__(self):
        self.type = ""
        self.data_size = 0
        self.flags = 0
        self.form_id = 0
        self.form_version = 0
        self.subrecords = []  # list of (type, data_bytes)
        self.editor_id = ""
        self.offset = 0


class PluginReader:
    """Reads and parses a TES5 plugin file."""

    def __init__(self, filepath):
        self.filepath = Path(filepath)
        self.records = []
        self.groups = []
        self.tes4_record = None

    def read(self):
        """Read the entire plugin."""
        with open(self.filepath, "rb") as f:
            data = f.read()

        pos = 0
        file_size = len(data)

        while pos < file_size:
            if pos + 4 > file_size:
                break

            rec_type = data[pos:pos + 4].decode("ascii", errors="replace")

            if rec_type == "GRUP":
                if pos + GROUP_HEADER_SIZE > file_size:
                    break
                group_size = struct.unpack_from("<I", data, pos + 4)[0]
                label = data[pos + 8:pos + 12]
                group_type = struct.unpack_from("<I", data, pos + 12)[0]

                if group_type == 0:  # Top-level group
                    label_str = label.decode("ascii", errors="replace")
                    self.groups.append((label_str, group_size, pos))

                # Parse records inside the group
                inner_pos = pos + GROUP_HEADER_SIZE
                group_end = pos + group_size
                while inner_pos < group_end:
                    if inner_pos + 4 > file_size:
                        break
                    inner_type = data[inner_pos:inner_pos + 4].decode("ascii", errors="replace")
                    if inner_type == "GRUP":
                        if inner_pos + GROUP_HEADER_SIZE > file_size:
                            break
                        inner_group_size = struct.unpack_from("<I", data, inner_pos + 4)[0]
                        # Recursively parse sub-groups
                        self._parse_group(data, inner_pos, inner_pos + inner_group_size, file_size)
                        inner_pos += inner_group_size
                    else:
                        rec = self._parse_record(data, inner_pos, file_size)
                        if rec:
                            self.records.append(rec)
                            inner_pos += RECORD_HEADER_SIZE + rec.data_size
                        else:
                            break

                pos = group_end
            else:
                # Top-level record (TES4 header)
                rec = self._parse_record(data, pos, file_size)
                if rec:
                    if rec.type == "TES4":
                        self.tes4_record = rec
                    else:
                        self.records.append(rec)
                    pos += RECORD_HEADER_SIZE + rec.data_size
                else:
                    break

    def _parse_group(self, data, start, end, file_size):
        """Parse records within a group (recursive for nested groups)."""
        pos = start + GROUP_HEADER_SIZE
        while pos < end and pos < file_size:
            rec_type = data[pos:pos + 4].decode("ascii", errors="replace")
            if rec_type == "GRUP":
                if pos + GROUP_HEADER_SIZE > file_size:
                    break
                sub_size = struct.unpack_from("<I", data, pos + 4)[0]
                self._parse_group(data, pos, pos + sub_size, file_size)
                pos += sub_size
            else:
                rec = self._parse_record(data, pos, file_size)
                if rec:
                    self.records.append(rec)
                    pos += RECORD_HEADER_SIZE + rec.data_size
                else:
                    break

    def _parse_record(self, data, pos, file_size):
        """Parse a single record at the given position."""
        if pos + RECORD_HEADER_SIZE > file_size:
            return None

        rec = Record()
        rec.offset = pos
        rec.type = data[pos:pos + 4].decode("ascii", errors="replace")
        rec.data_size = struct.unpack_from("<I", data, pos + 4)[0]
        rec.flags = struct.unpack_from("<I", data, pos + 8)[0]
        rec.form_id = struct.unpack_from("<I", data, pos + 12)[0]
        rec.form_version = struct.unpack_from("<H", data, pos + 20)[0]

        # Parse subrecords
        rec_data_start = pos + RECORD_HEADER_SIZE
        rec_data_end = rec_data_start + rec.data_size

        if rec_data_end > file_size:
            return None

        raw_data = data[rec_data_start:rec_data_end]

        # Handle compressed records
        if rec.flags & FLAG_COMPRESSED:
            if len(raw_data) < 4:
                return rec
            decompressed_size = struct.unpack_from("<I", raw_data, 0)[0]
            try:
                raw_data = zlib.decompress(raw_data[4:])
            except zlib.error:
                return rec  # Return with no subrecords if decompression fails

        # Parse subrecords from raw_data
        sub_pos = 0
        while sub_pos + SUBRECORD_HEADER_SIZE <= len(raw_data):
            sub_type = raw_data[sub_pos:sub_pos + 4].decode("ascii", errors="replace")
            sub_size = struct.unpack_from("<H", raw_data, sub_pos + 4)[0]

            # Handle XXXX extended size
            if sub_type == "XXXX" and sub_size == 4:
                extended_size = struct.unpack_from("<I", raw_data, sub_pos + 6)[0]
                sub_pos += 10  # Skip XXXX header
                if sub_pos + 4 > len(raw_data):
                    break
                real_type = raw_data[sub_pos:sub_pos + 4].decode("ascii", errors="replace")
                # Skip the 2-byte size field of the real subrecord
                sub_pos += 6
                if sub_pos + extended_size <= len(raw_data):
                    rec.subrecords.append((real_type, raw_data[sub_pos:sub_pos + extended_size]))
                    sub_pos += extended_size
                continue

            sub_data_start = sub_pos + SUBRECORD_HEADER_SIZE
            sub_data_end = sub_data_start + sub_size
            if sub_data_end > len(raw_data):
                break

            sub_data = raw_data[sub_data_start:sub_data_end]
            rec.subrecords.append((sub_type, sub_data))

            # Extract EditorID
            if sub_type == "EDID":
                rec.editor_id = sub_data.rstrip(b"\x00").decode("utf-8", errors="replace")

            sub_pos = sub_data_end

        return rec


def format_bytes(data, max_bytes=32):
    """Format bytes as hex string with ASCII preview."""
    hex_str = " ".join(f"{b:02X}" for b in data[:max_bytes])
    if len(data) > max_bytes:
        hex_str += " ..."
    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data[:max_bytes])
    return f"{hex_str}  |{ascii_str}|"


def format_formid(fid):
    """Format a FormID as hex string."""
    return f"{fid:08X}"


def get_subrecord_types(rec):
    """Get set of subrecord types in a record."""
    return {s[0] for s in rec.subrecords}


def get_subrecord_data(rec, sig):
    """Get the first subrecord data matching the signature."""
    for s_type, s_data in rec.subrecords:
        if s_type == sig:
            return s_data
    return None


def print_summary(reader):
    """Print a summary of the plugin."""
    print(f"Plugin: {reader.filepath.name}")
    print(f"File size: {reader.filepath.stat().st_size:,} bytes")

    # TES4 header info
    if reader.tes4_record:
        hedr = get_subrecord_data(reader.tes4_record, "HEDR")
        if hedr and len(hedr) >= 12:
            version = struct.unpack_from("<f", hedr, 0)[0]
            num_records = struct.unpack_from("<I", hedr, 4)[0]
            next_id = struct.unpack_from("<I", hedr, 8)[0]
            print(f"Version: {version:.2f}")
            print(f"Records (HEDR): {num_records}")
            print(f"Next FormID: {format_formid(next_id)}")
            print(f"Form Version: {reader.tes4_record.form_version}")
            print(f"Flags: 0x{reader.tes4_record.flags:08X}", end="")
            if reader.tes4_record.flags & FLAG_ESM:
                print(" [ESM]", end="")
            print()

        # Masters
        masters = []
        for s_type, s_data in reader.tes4_record.subrecords:
            if s_type == "MAST":
                masters.append(s_data.rstrip(b"\x00").decode("utf-8", errors="replace"))
        if masters:
            print(f"Masters: {', '.join(masters)}")

    print()

    # Group summary
    if reader.groups:
        print("Top-level groups:")
        for label, size, offset in sorted(reader.groups, key=lambda g: g[0]):
            print(f"  {label:6s}  size={size:>10,}  offset=0x{offset:08X}")
        print()

    # Record type counts
    type_counts = defaultdict(int)
    for rec in reader.records:
        type_counts[rec.type] += 1

    print(f"Total records: {len(reader.records)}")
    print()
    print("Records by type:")
    for rtype in sorted(type_counts.keys()):
        print(f"  {rtype:6s}  {type_counts[rtype]:>6,}")


def print_record(rec, verbose=False):
    """Print a single record."""
    compressed = " [COMPRESSED]" if rec.flags & FLAG_COMPRESSED else ""
    edid = f" ({rec.editor_id})" if rec.editor_id else ""
    print(f"  [{rec.type}] FormID={format_formid(rec.form_id)} "
          f"FormVer={rec.form_version} Flags=0x{rec.flags:08X}{compressed}{edid}")
    print(f"    Offset=0x{rec.offset:08X}  DataSize={rec.data_size}")

    if verbose:
        for s_type, s_data in rec.subrecords:
            print(f"    {s_type} ({len(s_data)} bytes): {format_bytes(s_data)}")


def run_checks(reader):
    """Run integrity checks for common TES4→TES5 conversion issues."""
    issues = []
    warnings = []

    # Check TES4 header
    if reader.tes4_record:
        hedr = get_subrecord_data(reader.tes4_record, "HEDR")
        if hedr and len(hedr) >= 4:
            version = struct.unpack_from("<f", hedr, 0)[0]
            if version < 1.69 or version > 1.72:
                issues.append(f"TES4 HEDR version is {version:.2f} (expected 1.70-1.71)")

        if reader.tes4_record.form_version not in (0, 43, 44):
            issues.append(f"TES4 form version is {reader.tes4_record.form_version} (expected 43 or 44)")

    # Check each record
    missing_obnd = defaultdict(list)
    wrong_form_version = defaultdict(list)
    empty_edid = 0
    zero_formid = 0

    for rec in reader.records:
        # Check form version
        if rec.form_version not in (43, 44):
            wrong_form_version[rec.type].append(rec)

        # Check OBND on records that need it
        if rec.type in OBND_REQUIRED:
            sub_types = get_subrecord_types(rec)
            if "OBND" not in sub_types:
                missing_obnd[rec.type].append(rec)

        # Check for zero FormID (should never happen on non-header records)
        if rec.form_id == 0:
            zero_formid += 1

        # Check for empty EDID on records that typically need one
        if rec.type not in ("REFR", "ACHR", "LAND", "PGRD", "NAVM", "INFO", "CELL"):
            if not rec.editor_id:
                empty_edid += 1

    # Report
    print("\n" + "=" * 60)
    print("  INTEGRITY CHECK RESULTS")
    print("=" * 60)

    if not issues and not missing_obnd and not wrong_form_version and zero_formid == 0:
        print("\n  All checks passed!")
    else:
        if issues:
            print("\n  CRITICAL ISSUES:")
            for issue in issues:
                print(f"    [!] {issue}")

        if missing_obnd:
            print("\n  MISSING OBND (will cause engine crashes):")
            for rtype, recs in sorted(missing_obnd.items()):
                examples = ", ".join(
                    format_formid(r.form_id) for r in recs[:3]
                )
                more = f" (+{len(recs)-3} more)" if len(recs) > 3 else ""
                print(f"    {rtype}: {len(recs)} records  (e.g., {examples}{more})")

        if wrong_form_version:
            print("\n  WRONG FORM VERSION (should be 43 or 44):")
            for rtype, recs in sorted(wrong_form_version.items()):
                versions = set(r.form_version for r in recs)
                print(f"    {rtype}: {len(recs)} records with version(s) {versions}")

        if zero_formid > 0:
            print(f"\n  ZERO FORMID: {zero_formid} records have FormID 00000000")

    if empty_edid > 0:
        warnings.append(f"{empty_edid} records without EditorID (may be intentional)")

    if warnings:
        print("\n  WARNINGS:")
        for w in warnings:
            print(f"    [~] {w}")

    # NPC_ specific checks
    npc_records = [r for r in reader.records if r.type == "NPC_"]
    if npc_records:
        print(f"\n  NPC_ CHECKS ({len(npc_records)} records):")
        no_race = 0
        no_acbs = 0
        for rec in npc_records:
            sub_types = get_subrecord_types(rec)
            if "RNAM" not in sub_types:
                no_race += 1
            if "ACBS" not in sub_types:
                no_acbs += 1
        if no_race:
            print(f"    Missing RNAM (race): {no_race}")
        if no_acbs:
            print(f"    Missing ACBS (base stats): {no_acbs}")
        if not no_race and not no_acbs:
            print(f"    All NPC_ records have RNAM and ACBS")

    # ARMO specific checks
    armo_records = [r for r in reader.records if r.type == "ARMO"]
    if armo_records:
        print(f"\n  ARMO CHECKS ({len(armo_records)} records):")
        no_bod2 = 0
        for rec in armo_records:
            sub_types = get_subrecord_types(rec)
            if "BOD2" not in sub_types and "BODT" not in sub_types:
                no_bod2 += 1
        if no_bod2:
            print(f"    Missing BOD2/BODT (body template): {no_bod2}")
        else:
            print(f"    All ARMO records have body template data")

    # CELL specific checks
    cell_records = [r for r in reader.records if r.type == "CELL"]
    if cell_records:
        print(f"\n  CELL CHECKS ({len(cell_records)} records):")
        data_issues = 0
        for rec in cell_records:
            cell_data = get_subrecord_data(rec, "DATA")
            if cell_data and len(cell_data) == 1:
                data_issues += 1
        if data_issues:
            print(f"    DATA is 1 byte (should be 2 for TES5): {data_issues}")
        else:
            print(f"    All CELL records have correct DATA size")

    print()


def main():
    parser = argparse.ArgumentParser(description="TES5 Plugin Verification & Dump Tool")
    parser.add_argument("plugin", help="Path to .esp/.esm plugin file")
    parser.add_argument("--summary", action="store_true", default=True,
                        help="Show summary (default)")
    parser.add_argument("--dump", action="store_true", help="Dump all records")
    parser.add_argument("--type", "-t", help="Filter by record type (e.g., NPC_)")
    parser.add_argument("--check", "-c", action="store_true",
                        help="Run integrity checks")
    parser.add_argument("--formid", help="Find record by FormID (hex, e.g., 00012345)")
    parser.add_argument("--edid", help="Find record by EditorID substring")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show subrecord hex data")

    args = parser.parse_args()

    plugin_path = Path(args.plugin)
    if not plugin_path.exists():
        print(f"ERROR: File not found: {plugin_path}")
        return 1

    print(f"Reading {plugin_path.name}...")
    reader = PluginReader(plugin_path)
    reader.read()
    print(f"Parsed {len(reader.records)} records.\n")

    # Filter by FormID
    if args.formid:
        target_id = int(args.formid, 16)
        found = [r for r in reader.records if r.form_id == target_id]
        if not found:
            print(f"No record found with FormID {format_formid(target_id)}")
        for rec in found:
            print_record(rec, verbose=True)
        return 0

    # Filter by EditorID
    if args.edid:
        search = args.edid.lower()
        found = [r for r in reader.records if search in r.editor_id.lower()]
        print(f"Found {len(found)} records matching '{args.edid}':")
        for rec in found:
            print_record(rec, verbose=args.verbose)
        return 0

    # Filter by type
    if args.type:
        filtered = [r for r in reader.records if r.type == args.type.upper()]
        print(f"{len(filtered)} {args.type.upper()} records:")
        for rec in filtered:
            print_record(rec, verbose=args.verbose or args.dump)
        return 0

    # Summary (default)
    print_summary(reader)

    # Dump all records
    if args.dump:
        print("\n" + "=" * 60)
        print("  FULL RECORD DUMP")
        print("=" * 60)
        for rec in reader.records:
            print_record(rec, verbose=args.verbose)

    # Run checks
    if args.check:
        run_checks(reader)

    return 0


if __name__ == "__main__":
    sys.exit(main())

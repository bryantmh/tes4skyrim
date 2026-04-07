"""
TES4 Export — Pure binary dump of Oblivion ESM/ESP files to KEY=VALUE text.

Reads TES4 binary files directly (no xEdit dependency) and outputs one text
file per record group. No transformations are applied — this is a faithful
representation of the TES4 data.

Usage:
    python -m tes4_export.export <input_file> [--outdir export/<name>] [--types STAT NPC_ ...]
    python -m tes4_export.export "C:/path/to/Oblivion.esm"
    python -m tes4_export.export "C:/path/to/Knights.esp" --types WEAP ARMO
"""

import argparse
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import as_completed

from .record_types.actors import (
    export_BSGN,
    export_CLAS,
    export_CONT,
    export_CREA,
    export_CSTY,
    export_EYES,
    export_FACT,
    export_HAIR,
    export_IDLE,
    export_NPC_,
    export_RACE,
    export_SKIL,
)
from .record_types.dialog_misc import (
    export_CLMT,
    export_DIAL,
    export_EFSH,
    export_GLOB,
    export_GMST,
    export_INFO,
    export_LSCR,
    export_LVLC,
    export_LVLI,
    export_LVSP,
    export_PACK,
    export_QUST,
    export_SCPT,
    export_SOUN,
    export_WATR,
    export_WTHR,
)
from .record_types.equipment import (
    export_ALCH,
    export_AMMO,
    export_APPA,
    export_ARMO,
    export_BOOK,
    export_CLOT,
    export_ENCH,
    export_INGR,
    export_MGEF,
    export_SGST,
    export_SPEL,
    export_WEAP,
)

# --- Record type dispatch table ---
from .record_types.items import (
    export_ACTI,
    export_ANIO,
    export_DOOR,
    export_FLOR,
    export_FURN,
    export_GRAS,
    export_KEYM,
    export_LIGH,
    export_MISC,
    export_SBSP,
    export_SLGM,
    export_STAT,
    export_TREE,
)
from .record_types.world import (
    export_ACHR,
    export_ACRE,
    export_CELL,
    export_LAND,
    export_LTEX,
    export_PGRD,
    export_REFR,
    export_REGN,
    export_ROAD,
    export_WRLD,
)
from .tes4_reader import Record, get_formid_str, get_string, get_subrecord, read_file

EXPORT_DISPATCH = {
    # Items / Objects
    "STAT": export_STAT, "ACTI": export_ACTI, "MISC": export_MISC,
    "KEYM": export_KEYM, "DOOR": export_DOOR, "FLOR": export_FLOR,
    "FURN": export_FURN, "GRAS": export_GRAS, "TREE": export_TREE,
    "LIGH": export_LIGH, "SLGM": export_SLGM, "ANIO": export_ANIO,
    "SBSP": export_SBSP,
    # Equipment / Magic
    "WEAP": export_WEAP, "ARMO": export_ARMO, "CLOT": export_CLOT,
    "AMMO": export_AMMO, "BOOK": export_BOOK, "ENCH": export_ENCH,
    "SPEL": export_SPEL, "ALCH": export_ALCH, "INGR": export_INGR,
    "MGEF": export_MGEF, "SGST": export_SGST, "APPA": export_APPA,
    # Actors
    "NPC_": export_NPC_, "CREA": export_CREA, "CONT": export_CONT,
    "FACT": export_FACT, "RACE": export_RACE, "CLAS": export_CLAS,
    "EYES": export_EYES, "HAIR": export_HAIR, "BSGN": export_BSGN,
    "SKIL": export_SKIL, "CSTY": export_CSTY, "IDLE": export_IDLE,
    # World / Placement
    "CELL": export_CELL, "WRLD": export_WRLD, "REFR": export_REFR,
    "ACHR": export_ACHR, "ACRE": export_ACRE, "LAND": export_LAND,
    "LTEX": export_LTEX, "REGN": export_REGN, "ROAD": export_ROAD,
    "PGRD": export_PGRD,
    # Dialog / Quest / Misc
    "DIAL": export_DIAL, "INFO": export_INFO, "QUST": export_QUST,
    "PACK": export_PACK, "SCPT": export_SCPT, "GLOB": export_GLOB,
    "GMST": export_GMST, "SOUN": export_SOUN, "CLMT": export_CLMT,
    "WATR": export_WATR, "EFSH": export_EFSH, "LSCR": export_LSCR,
    "LVLI": export_LVLI, "LVLC": export_LVLC, "LVSP": export_LVSP,
    "WTHR": export_WTHR,
}

# Types that can't be meaningfully exported (skipped with note)
SKIP_TYPES = set()  # All types now exported

_WORKER_COUNT = max(1, (os.cpu_count() or 4) - 3)


def format_record(rec: Record) -> str:
    """Format a single record as a ---RECORD_BEGIN---...---RECORD_END--- text block."""
    lines = ["---RECORD_BEGIN---"]
    lines.append(f"Signature={rec.type}")
    lines.append(f"FormID={get_formid_str(rec.form_id)}")

    # EditorID in header for quick reference
    edid_sub = get_subrecord(rec, "EDID")
    if edid_sub:
        from .record_types.common import escape_value
        lines.append(f"EditorID={escape_value(get_string(edid_sub))}")

    lines.append(f"RecordFlags={rec.flags}")

    # Hierarchy context
    if rec.parent_wrld:
        lines.append(f"ParentWRLD={get_formid_str(rec.parent_wrld)}")
    if rec.parent_cell:
        lines.append(f"ParentCELL={get_formid_str(rec.parent_cell)}")
    if rec.parent_dial:
        lines.append(f"ParentDIAL={get_formid_str(rec.parent_dial)}")

    # Type-specific fields
    export_fn = EXPORT_DISPATCH.get(rec.type)
    if export_fn:
        type_lines = export_fn(rec)
        # Remove duplicate EditorID if present
        type_lines = [l for l in type_lines if not l.startswith("EditorID=")]
        lines.extend(type_lines)
    else:
        # Unknown type - dump subrecord signatures and sizes
        lines.append(f"# Unknown record type: {rec.type}")
        for sub in rec.subrecords:
            if sub.type != "EDID":
                lines.append(f"{sub.type}.Size={len(sub.data)}")

    lines.append("---RECORD_END---")
    return "\n".join(lines)


def export_records_for_type(records: list) -> str:
    """Export all records of a given type to a text string."""
    blocks = []
    for rec in records:
        blocks.append(format_record(rec))
    return "\n\n".join(blocks) + "\n"


def export_file(all_records: list, output_dir: str, type_filter: set = None, source_filter: str = None):
    """
    Export parsed TES4 records to text format.

    Args:
        all_records: List of Record objects from read_file()
        output_dir: Directory for output files
        type_filter: If set, only export these record types
        source_filter: If set, only export records from a specific source file
                       (by load-order prefix matching)
    """

    # Group records by type
    by_type = defaultdict(list)
    source_prefix = None
    if source_filter:
        # Determine which load-order index corresponds to the source file
        # For now, source_filter is used as a simple prefix mask (e.g., "01" for index 1)
        source_prefix = source_filter

    for rec in all_records:
        if source_prefix:
            # Check if the record's FormID has the expected load-order prefix
            rec_prefix = f"{(rec.form_id >> 24) & 0xFF:02X}"
            if rec_prefix != source_prefix:
                continue
        if type_filter and rec.type not in type_filter:
            continue
        by_type[rec.type].append(rec)

    # Report counts
    total = sum(len(recs) for recs in by_type.values())
    print(f"  Exporting {total} records across {len(by_type)} types")
    for sig in sorted(by_type.keys()):
        count = len(by_type[sig])
        skip = " (SKIP)" if sig in SKIP_TYPES else ""
        print(f"    {sig}: {count}{skip}")

    os.makedirs(output_dir, exist_ok=True)

    t_start = time.time()
    _export_per_type_parallel(by_type, output_dir)

    t_end = time.time()
    print(f"  Export formatting/write took {t_end-t_start:.2f}s")


def _export_per_type_parallel(by_type: dict, output_dir: str):
    """Write per-type files using parallel workers for formatting."""
    # Formatting is CPU-bound; use ProcessPoolExecutor
    # But records contain complex objects — serialize them first
    # Actually, since Python multiprocessing needs pickling, and our Record
    # dataclass is simple, we can pass the records.
    # However, the export functions import from modules which need to be
    # importable — this should work fine with ProcessPoolExecutor.
    from concurrent.futures import ThreadPoolExecutor

    # Use threads (I/O + formatting) since GIL isn't terrible for this
    # and avoids pickling overhead
    def process_type(sig_records):
        sig, records = sig_records
        suffix = "_SKIP" if sig in SKIP_TYPES else ""
        filename = f"{sig}{suffix}.txt"
        filepath = os.path.join(output_dir, filename)
        text = export_records_for_type(records)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(text)
        return sig, len(records), filepath

    items = list(by_type.items())
    with ThreadPoolExecutor(max_workers=_WORKER_COUNT) as executor:
        futures = {executor.submit(process_type, item): item[0] for item in items}
        for future in as_completed(futures):
            sig, count, filepath = future.result()
            print(f"    Wrote {filepath} ({count} records)")


def export_header(header: Record, output_dir: str):
    """Write the TES4 file header info."""
    filepath = os.path.join(output_dir, "_HEADER.txt")
    lines = []
    hedr = get_subrecord(header, "HEDR")
    if hedr and len(hedr.data) >= 12:
        import struct
        lines.append(f"HEDR.Version={struct.unpack_from('<f', hedr.data, 0)[0]}")
        lines.append(f"HEDR.NumRecords={struct.unpack_from('<I', hedr.data, 4)[0]}")
        lines.append(f"HEDR.NextObjectID={struct.unpack_from('<I', hedr.data, 8)[0]}")

    cnam = get_subrecord(header, "CNAM")
    if cnam:
        lines.append(f"CNAM.Author={get_string(cnam)}")

    snam = get_subrecord(header, "SNAM")
    if snam:
        lines.append(f"SNAM.Description={get_string(snam)}")

    # Master files
    masts = [s for s in header.subrecords if s.type == "MAST"]
    for i, mast in enumerate(masts):
        lines.append(f"Master[{i}]={get_string(mast)}")

    lines.append(f"Flags={header.flags}")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"    Wrote {filepath}")


def main():
    parser = argparse.ArgumentParser(
        description="TES4 Export — Pure binary dump of Oblivion ESM/ESP to text"
    )
    parser.add_argument("input", help="Path to TES4 ESM/ESP file")
    parser.add_argument("--outdir", "-o", help="Output directory (default: export/<filename>/)")
    parser.add_argument("--types", "-t", nargs="+", help="Only export these record types")
    parser.add_argument("--source-index", "-s",
                        help="Only export records from this load-order index (hex, e.g. '00' or '01')")
    parser.add_argument("--list-types", action="store_true",
                        help="Just list record types and counts, don't export")

    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"Error: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    basename = os.path.basename(args.input)

    if args.outdir:
        output_dir = args.outdir
    else:
        output_dir = os.path.join("export", basename)

    type_filter = set(args.types) if args.types else None

    if args.list_types:
        header, all_records = read_file(args.input)
        by_type = defaultdict(int)
        for rec in all_records:
            by_type[rec.type] += 1
        print(f"Record types in {basename}:")
        for sig in sorted(by_type.keys()):
            handler = "✓" if sig in EXPORT_DISPATCH else "?"
            print(f"  {handler} {sig}: {by_type[sig]}")
        print(f"\nTotal: {len(all_records)} records, {len(by_type)} types")
        print(f"Handled: {sum(1 for s in by_type if s in EXPORT_DISPATCH)}/{len(by_type)} types")
        return

    # Full export
    print(f"Reading {basename}...")
    t0 = time.time()
    header, all_records = read_file(args.input)
    t1 = time.time()
    print(f"  Parsed {len(all_records)} records in {t1-t0:.2f}s")

    os.makedirs(output_dir, exist_ok=True)
    export_header(header, output_dir)
    export_file(all_records, output_dir, type_filter, args.source_index)


if __name__ == "__main__":
    main()

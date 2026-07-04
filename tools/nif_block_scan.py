"""Binary block-type scanner for NIF trees.

NIF headers store block type names as plaintext ASCII, so a raw byte search
finds which block types a file contains without parsing. Useful for
"0 vanilla files pair X with Y" style diagnostics.

Usage:
    # List files containing ALL of the given block types (co-occurrence test)
    python tools/nif_block_scan.py <dir_or_nif> --has bhkRigidBodyT --has bhkCompressedMeshShape

    # List files containing ANY of the given block types
    python tools/nif_block_scan.py <dir> --any NiTriStrips bhkMultiSphereShape

    # Histogram of block types across a tree (from the header block-type table)
    python tools/nif_block_scan.py <dir> --histogram

Notes:
    - `--has X` matches X as an exact block-type-table entry (length-prefixed),
      so bhkRigidBody does NOT match bhkRigidBodyT.
    - Only the header is read (first 64KB), so scanning thousands of files is fast.
"""
import argparse
import os
import struct
import sys
from concurrent.futures import ThreadPoolExecutor

HEADER_BYTES = 65536


def read_block_types(path):
    """Return the header's block-type name list (exact strings), or None on parse failure."""
    try:
        with open(path, "rb") as f:
            data = f.read(HEADER_BYTES)
    except OSError:
        return None
    # Header: "Gamebryo File Format, Version ...\n" then binary fields.
    nl = data.find(b"\x0a")
    if nl < 0 or not data.startswith((b"Gamebryo", b"NetImmerse")):
        return None
    pos = nl + 1
    try:
        version = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if version >= 0x14000003:
            pos += 1  # endian
        if version >= 0x0A000108:  # user version (actually 10.0.1.8+? keep simple)
            pos += 4
        num_blocks = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if version >= 0x0A000102:  # BS header (user_version present above)
            uv = struct.unpack_from("<I", data, pos - 8 - 4)  # not used
        # BSStreamHeader when user version >= 3 (Bethesda): BS version u32 + 3 strings + u32
        # Detect Bethesda stream: peek u32; Skyrim=83/100, Oblivion=11
        bs_ver = struct.unpack_from("<I", data, pos)[0]
        if bs_ver in (11, 34, 83, 100, 130, 155):
            pos += 4
            for _ in range(3):  # author, processScript, exportScript (byte-len prefixed)
                slen = data[pos]
                pos += 1 + slen
            if bs_ver >= 130:
                pos += 4  # max filepath? (FO4) — not relevant here
        num_types = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        types = []
        for _ in range(num_types):
            slen = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            types.append(data[pos:pos + slen].decode("ascii", "replace"))
            pos += slen
        return types
    except (struct.error, IndexError):
        return None


def scan_file(path, has, anyof):
    types = read_block_types(path)
    if types is None:
        return (path, None, "parse-failed")
    tset = set(types)
    ok = True
    if has:
        ok = all(t in tset for t in has)
    if ok and anyof:
        ok = any(t in tset for t in anyof)
    return (path, types, "match" if ok else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("--has", action="append", default=[], help="block type that must be present (repeatable, ANDed)")
    ap.add_argument("--any", nargs="+", default=[], help="block types, at least one present")
    ap.add_argument("--histogram", action="store_true", help="print block-type histogram over the tree")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = ap.parse_args()

    if os.path.isfile(args.root):
        files = [args.root]
    else:
        files = [os.path.join(dp, f) for dp, _, fs in os.walk(args.root)
                 for f in fs if f.lower().endswith(".nif")]
    print(f"Scanning {len(files)} NIF files...", file=sys.stderr)

    if args.histogram:
        from collections import Counter
        hist = Counter()
        failed = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for types in ex.map(read_block_types, files):
                if types is None:
                    failed += 1
                else:
                    hist.update(set(types))
        for name, count in hist.most_common():
            print(f"{count:7d}  {name}")
        if failed:
            print(f"({failed} files failed header parse)", file=sys.stderr)
        return

    if not args.has and not args.any:
        ap.error("give --has/--any or --histogram")

    matches, failed = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for path, types, status in ex.map(lambda p: scan_file(p, args.has, args.any), files):
            if status == "match":
                matches.append(path)
            elif status == "parse-failed":
                failed.append(path)
    for m in sorted(matches):
        print(m)
    print(f"{len(matches)} matching files; {len(failed)} header-parse failures", file=sys.stderr)
    if failed:
        for p in failed[:20]:
            print(f"  parse-failed: {p}", file=sys.stderr)


if __name__ == "__main__":
    main()

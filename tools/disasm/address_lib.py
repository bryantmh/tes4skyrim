"""Address Library (versionlib) reader: map SKSE stable IDs <-> per-build RVAs.

Why this exists: a crash log names RVAs in whichever build the user plays, and
a retail Steam SkyrimSE.exe is DRM-packed.  A crash frame like

    SkyrimSE.exe+14E0460 -> 107327+0x3A0

names an Address Library *stable ID* (107327) plus an offset, so the same code
can be located in an unpacked build by translating 107327 through both builds'
databases.  Without that translation the raw RVA lands in unrelated code and any
conclusion drawn from it is fiction.

Formats: 1 (SE 1.5.x), 2 (SE/AE 1.6.x) and 5 (AE 1.7.x), little-endian.
See: docs/reference/address_library_formats.md#address-library-database-formats

Usage:
    # translate an ENTIRE crash log's call stack in one shot (start here)
    python tools/disasm/address_lib.py --log "path/to/crash-....log"

    # translate a single crash-log frame to the build being disassembled
    python tools/disasm/address_lib.py --id 107327 --to 1.6.659 --offset 0x3A0

    # which stable ID owns a raw RVA in a given build?
    python tools/disasm/address_lib.py --rva 0x14E0460 --from 1.6.1170

    # RVA of an ID in one build
    python tools/disasm/address_lib.py --id 107327 --from 1.6.1170
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

DEFAULT_DIRS = [
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition")
    / "Data"
    / "SKSE"
    / "Plugins",
]

#: Where the unpacked per-build exes live, one per version.
DEPOT = Path(r"C:\Program Files (x86)\Steam\steamapps\content\app_489830"
             r"\depot_489833")

#: The build disassembly examples point at.
DISASM_EXE = DEPOT / "SkyrimSE.1.6.659.unpacked.exe"


def find_versionlib(version: str, extra_dir: str | None = None) -> Path:
    """Locate the database for a dotted version string.

    1.5.x ships as `version-<a>-<b>-<c>-0.bin`, 1.6+ as `versionlib-...`.
    """
    stems = [p + version.replace(".", "-") + "-0" for p in ("versionlib-", "version-")]
    dirs = [Path(extra_dir)] if extra_dir else []
    dirs += DEFAULT_DIRS
    for d in dirs:
        for stem in stems:
            cand = d / (stem + ".bin")
            if cand.is_file():
                return cand
    searched = ", ".join(str(d) for d in dirs)
    raise SystemExit(f"no {' or '.join(s + '.bin' for s in stems)} found in: {searched}")


class Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0

    def u8(self) -> int:
        v = self.d[self.p]
        self.p += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.d, self.p)[0]
        self.p += 2
        return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.d, self.p)[0]
        self.p += 4
        return v

    def u64(self) -> int:
        v = struct.unpack_from("<Q", self.d, self.p)[0]
        self.p += 8
        return v

    def string(self, n: int) -> str:
        v = self.d[self.p : self.p + n].decode("utf-8", "replace")
        self.p += n
        return v

    def skip(self, n: int) -> None:
        """Advance the cursor over n bytes without decoding them."""
        self.p += n


def _read_kind(r: Reader, kind: int, prev: int) -> int:
    """One delta-coded field. Kinds 6 and 7 are u16/u32, NOT u64.

    See: docs/reference/address_library_formats.md#formats-1-and-2--delta-coded
    """
    if kind == 0:
        return r.u64()
    if kind == 1:
        return prev + 1
    if kind == 2:
        return prev + r.u8()
    if kind == 3:
        return prev - r.u8()
    if kind == 4:
        return prev + r.u16()
    if kind == 5:
        return prev - r.u16()
    if kind == 6:
        return r.u16()
    if kind == 7:
        return r.u32()
    raise ValueError(f"bad kind {kind}")


def _load_delta(r: Reader, path: Path) -> dict[int, int]:
    """Format 1/2 body: length-prefixed name, then one control byte per entry.

    Bit 3 of the high nibble divides the PREVIOUS offset by ptr_size before the
    delta and multiplies the result back after.
    See: docs/reference/address_library_formats.md#formats-1-and-2--delta-coded
    """
    r.string(r.i32())
    ptr_size = r.i32()
    count = r.i32()
    out: dict[int, int] = {}
    prev_id = 0
    prev_off = 0
    for _ in range(count):
        ctl = r.u8()
        lo = ctl & 0x0F
        hi = (ctl >> 4) & 0x0F
        cur_id = _read_kind(r, lo, prev_id)
        scaled = bool(hi & 0x08)
        base = (prev_off // ptr_size) if scaled else prev_off
        cur_off = _read_kind(r, hi & 0x07, base)
        if scaled:
            cur_off *= ptr_size
        out[cur_id] = cur_off
        prev_id = cur_id
        prev_off = cur_off
    if r.p != len(r.d):
        raise SystemExit(
            f"{path.name}: parsed {len(out)} entries but consumed {r.p} of "
            f"{len(r.d)} bytes -- the stream desynced, results are unusable"
        )
    return out


def _load_flat(r: Reader, path: Path) -> dict[int, int]:
    """Format 5 body: fixed 64-byte name, pointer size, pad, then u32[count].

    The array is indexed by stable id. Id 0 and an id this build does not cover
    both store 0, so both stay absent.
    See: docs/reference/address_library_formats.md#format-5--flat-array
    """
    r.skip(64)
    ptr_size = r.i32()
    r.i32()
    count = r.u32()
    if ptr_size <= 0:
        raise SystemExit(f"{path.name}: bad pointer size {ptr_size}")
    if len(r.d) - r.p != count * 4:
        raise SystemExit(
            f"{path.name}: {len(r.d) - r.p} body bytes for {count} entries "
            f"-- expected {count * 4}, the header is not format 5"
        )
    rvas = struct.unpack_from(f"<{count}I", r.d, r.p)
    return {i: off for i, off in enumerate(rvas) if off}


def load(path: Path) -> dict[int, int]:
    """Parse an Address Library database into {stable_id: rva}.

    Formats 1 (SE 1.5.x) and 2 (SE/AE 1.6.x) are delta-coded; 5 (AE 1.7.x) is a
    flat u32[count] indexed by id. The four i32s after the format are the build
    version quad. Raises SystemExit on an unknown format, or on a body that does
    not consume the file exactly.
    See: docs/reference/address_library_formats.md#address-library-database-formats
    """
    r = Reader(path.read_bytes())
    fmt = r.i32()
    if fmt not in (1, 2, 5):
        raise SystemExit(f"{path.name}: unsupported database format {fmt} (expected 1, 2 or 5)")
    [r.i32() for _ in range(4)]
    return _load_flat(r, path) if fmt == 5 else _load_delta(r, path)


FRAME_RE = re.compile(r"->\s*(\d+)\+0x([0-9A-Fa-f]+)")


def translate_log(path: Path, src: str, dst: str, extra_dir: str | None) -> int:
    """Translate every '-> <id>+0x<off>' frame in a crash log into dst-build RVAs.

    Crash logs come from the Steam build; this prints the matching GOG RVAs so the
    whole call stack can be disassembled without hand-translating each frame.
    """
    text = path.read_text(errors="replace")
    frames: list[tuple[int, int]] = []
    seen = set()
    for m in FRAME_RE.finditer(text):
        key = (int(m.group(1)), int(m.group(2), 16))
        if key not in seen:
            seen.add(key)
            frames.append(key)

    if not frames:
        print(f"no '-> <id>+0x<offset>' frames found in {path.name}")
        return 1

    src_db = load(find_versionlib(src, extra_dir))
    dst_db = load(find_versionlib(dst, extra_dir))

    print(f"{path.name}: {len(frames)} unique frames  ({src} -> {dst})\n")
    print(f"{'ID':>8}  {'offset':>8}  {src + ' RVA':>14}  {dst + ' RVA':>14}")
    for sid, off in frames:
        s = src_db.get(sid)
        d = dst_db.get(sid)
        s_txt = f"{s + off:#x}" if s is not None else "-"
        d_txt = f"{d + off:#x}" if d is not None else "(not in build)"
        print(f"{sid:>8}  {off:>#8x}  {s_txt:>14}  {d_txt:>14}")

    print(
        "\ndisassemble any of the above with:\n"
        "  python tools/disasm/skyrim_disasm.py "
        f'--exe "{DISASM_EXE}" '
        "--disasm <RVA> --count 200"
    )
    return 0


def owning_id(db: dict[int, int], rva: int) -> tuple[int, int] | None:
    """Return (stable_id, delta) for the entry whose RVA is the closest <= rva."""
    best = None
    for sid, off in db.items():
        if off <= rva and (best is None or off > best[1]):
            best = (sid, off)
    if best is None:
        return None
    return best[0], rva - best[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--id", type=lambda s: int(s, 0), help="stable ID to look up")
    ap.add_argument("--rva", type=lambda s: int(s, 0), help="RVA to reverse-look-up")
    ap.add_argument(
        "--from",
        dest="src",
        default="1.6.1170",
        help="build the ID/RVA comes from (default 1.6.1170, the Steam crash-log build)",
    )
    ap.add_argument(
        "--to",
        dest="dst",
        default="1.6.659",
        help="build to translate into (default 1.6.659, the disassemblable GOG build)",
    )
    ap.add_argument(
        "--offset",
        type=lambda s: int(s, 0),
        default=0,
        help="byte offset inside the function (from a crash frame's '+0xNNN')",
    )
    ap.add_argument("--dir", help="extra directory to search for versionlib bins")
    ap.add_argument(
        "--log",
        help="translate every frame in a CrashLoggerSSE .log into --to build RVAs",
    )
    args = ap.parse_args()

    if args.log:
        return translate_log(Path(args.log), args.src, args.dst, args.dir)

    if args.id is None and args.rva is None:
        ap.error("need --id, --rva, or --log")

    src_db = load(find_versionlib(args.src, args.dir))
    sid = args.id

    if sid is None:
        hit = owning_id(src_db, args.rva)
        if hit is None:
            print(f"no entry <= {args.rva:#x} in {args.src}")
            return 1
        sid, delta = hit
        print(f"{args.src}  RVA {args.rva:#x}  ->  ID {sid} + {delta:#x}")
        args.offset = args.offset or delta
    else:
        if sid not in src_db:
            print(f"ID {sid} not present in {args.src}")
            return 1
        print(f"{args.src}  ID {sid} -> RVA {src_db[sid]:#x} (+{args.offset:#x} = "
              f"{src_db[sid] + args.offset:#x})")

    if args.dst and args.dst != args.src:
        dst_db = load(find_versionlib(args.dst, args.dir))
        if sid not in dst_db:
            print(f"ID {sid} not present in {args.dst} (signature changed between builds)")
            return 1
        base = dst_db[sid]
        print(f"{args.dst}  ID {sid} -> RVA {base:#x} (+{args.offset:#x} = "
              f"{base + args.offset:#x})")
        print(f"\ndisassemble with:\n  python tools/disasm/skyrim_disasm.py "
              f'--exe "{DISASM_EXE}" '
              f"--disasm {base:#x} --count 200")

    return 0


if __name__ == "__main__":
    sys.exit(main())

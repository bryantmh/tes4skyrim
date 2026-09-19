#!/usr/bin/env python3
"""Locate a Papyrus NATIVE function's engine implementation and disassemble it.

Why: the CK wiki under-documents natives ("Gets this actor's current dialogue
target" says nothing about WHICH actor's state is read, or when it clears).
The engine registers every native by its Papyrus name, so the string is the
anchor: find "GetDialogueTarget", find the `lea` that loads it for the
registration call, and the neighbouring `lea` of a CODE address is the native's
callback.  Disassembling that (and following its calls) shows what the native
really reads.

    python tools/script/papyrus_native_locate.py GetDialogueTarget --live
    python tools/script/papyrus_native_locate.py IsInDialogueWithPlayer --live --count 120
    python tools/script/papyrus_native_locate.py IsInMenuMode --live --follow 2

Prefer --live (the Steam exe on disk is DRM-packed; see skyrim_disasm.py).
Read-only analysis; nothing is written to the process.
"""

import argparse
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.disasm.skyrim_disasm import Binary, LiveBinary, DEFAULT_EXE


def find_strings(binary, name: str) -> list:
    """RVAs of NUL-terminated ASCII occurrences of `name` (exact, whole)."""
    pat = re.compile(rb'(?<![A-Za-z0-9_])' + re.escape(name.encode()) + rb'\0')
    out = []
    for m in re.finditer(pat, binary.data):
        rva = binary.off_to_rva(m.start())
        if rva is not None:
            out.append(rva)
    return out


def rip_lea_sites(binary, target_rva: int, lo: int = 0, hi: int = None) -> list:
    """RVAs of `lea r64, [rip+disp32]` instructions resolving to target_rva.

    Encoded as REX.W(0x48/0x4C) 8D modrm(mod=00, rm=101) disp32; the target is
    next_ip + disp32.  A brute scan over the image is cheap (tens of MB) and
    avoids needing a full linear sweep disassembly.
    """
    data = binary.data
    hi = len(data) if hi is None else hi
    out = []
    for m in re.finditer(rb'[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]', data[lo:hi]):
        off = lo + m.start()
        disp = struct.unpack_from('<i', data, off + 3)[0]
        rva = binary.off_to_rva(off)
        if rva is None:
            continue
        if rva + 7 + disp == target_rva:
            out.append(rva)
    return out


def code_leas_near(binary, site_rva: int, window: int = 96) -> list:
    """(rva, target) of rip-relative `lea`s within `window` bytes of a site
    whose target disassembles as code (heuristic: not inside a string/data
    run).  The registration call groups its `lea`s tightly, so the callback
    is one of these."""
    out = []
    data = binary.data
    lo = max(0, binary.rva_to_off(site_rva) - window)
    hi = binary.rva_to_off(site_rva) + window
    for m in re.finditer(rb'[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]', data[lo:hi]):
        off = lo + m.start()
        disp = struct.unpack_from('<i', data, off + 3)[0]
        rva = binary.off_to_rva(off)
        tgt = rva + 7 + disp
        b = binary.read(tgt, 4)
        if len(b) < 4:
            continue
        if 0x40 <= b[0] <= 0x4f or b[0] in (
                0x55, 0x53, 0x56, 0x57, 0xe9, 0x8b, 0x33, 0x0f, 0xf3, 0x66,
                0xb8, 0xb0, 0xe8, 0x89, 0x85, 0x80, 0xc3):
            out.append((rva, tgt))
    return out


def class_at(binary, site_rva: int) -> str:
    """The Papyrus SCRIPT a registration site names: the `lea r8, [rip+x]`
    loaded immediately before the function-name `lea`, or '' when absent.

    Several scripts register one function name (`Clear`, `ForceActive`), so
    the name alone does not identify a native.
    See: docs/commentary/morrowind_runtime.md#forceactive-is-a-weather-call
    """
    raw = binary.read(site_rva - 7, 7)
    if len(raw) < 7 or raw[:3] != b'\x4c\x8d\x05':
        return ''
    target = site_rva + struct.unpack_from('<i', raw, 3)[0]
    text = binary.read(target, 64)
    return text.split(b'\0', 1)[0].decode('ascii', 'replace')


def lea_sites_for(binary, targets: set) -> dict:
    """`{target rva: [lea site rvas]}` for every target in `targets`, in ONE
    pass over the image -- `rip_lea_sites` per target is a full scan each."""
    data = binary.data
    out = {target: [] for target in targets}
    for m in re.finditer(rb'[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]', data):
        rva = binary.off_to_rva(m.start())
        if rva is None or m.start() + 7 > len(data):
            continue
        target = rva + 7 + struct.unpack_from('<i', data, m.start() + 3)[0]
        if target in out:
            out[target].append(rva)
    return out


def registrations(binary, name: str, sites_by_target: dict = None) -> list:
    """`(script, site rva, [callback candidate rvas])` per registration of the
    native called `name`. `sites_by_target` is a `lea_sites_for` result that
    covers this name's strings, for a caller checking many names."""
    out = []
    for string_rva in find_strings(binary, name):
        sites = (sites_by_target[string_rva] if sites_by_target is not None
                 else rip_lea_sites(binary, string_rva))
        for site in sites:
            cands = sorted({t for _r, t in code_leas_near(binary, site)
                            if t != string_rva})
            out.append((class_at(binary, site), site, cands))
    return out


def dump(binary, rva: int, count: int, follow: int, seen: set, depth=0):
    if rva in seen or depth > follow:
        return
    seen.add(rva)
    print(f'--- disasm {rva:#x} (depth {depth}) ---')
    targets = []
    for ins in binary.disasm(rva, count):
        r = ins.address - binary.base
        print(f'  {r:#010x}  {ins.mnemonic:<7} {ins.op_str}')
        if ins.mnemonic in ('call', 'jmp') and ins.op_str.startswith('0x'):
            try:
                targets.append(int(ins.op_str, 16) - binary.base)
            except ValueError:
                pass
        if ins.mnemonic == 'ret' and depth > 0:
            break
    for t in targets:
        if binary.rva_to_off(t) is not None:
            dump(binary, t, count, follow, seen, depth + 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('name', help='Papyrus native function name, e.g. GetDialogueTarget')
    ap.add_argument('--exe', default=DEFAULT_EXE)
    ap.add_argument('--live', action='store_true')
    ap.add_argument('--pid', type=int, default=0)
    ap.add_argument('--count', type=int, default=80)
    ap.add_argument('--follow', type=int, default=1,
                    help='how many call levels to follow from the callback')
    args = ap.parse_args()

    binary = LiveBinary(args.pid) if args.live else Binary(args.exe)
    strs = find_strings(binary, args.name)
    print(f'string "{args.name}": {len(strs)} occurrence(s): '
          + ', '.join(f'{s:#x}' for s in strs))
    for s in strs:
        sites = rip_lea_sites(binary, s)
        print(f'  lea sites -> {s:#x}: ' + ', '.join(f'{x:#x}' for x in sites))
        for site in sites:
            print(f'  === registration site {site:#x}  '
                  f'script: {class_at(binary, site) or "?"} ===')
            for ins in binary.disasm(site - 32, 14):
                r = ins.address - binary.base
                print(f'    {r:#010x}  {ins.mnemonic:<7} {ins.op_str}')
            cands = [t for (r, t) in code_leas_near(binary, site) if t != s]
            cands = sorted(set(cands))
            print(f'  code lea targets near site: '
                  + ', '.join(f'{c:#x}' for c in cands))
            seen = set()
            for c in cands:
                # skip other strings' neighbours: only follow targets whose
                # first bytes look like a function prologue
                dump(binary, c, args.count, args.follow, seen)


if __name__ == '__main__':
    main()

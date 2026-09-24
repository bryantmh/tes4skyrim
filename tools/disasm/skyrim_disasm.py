#!/usr/bin/env python3
"""Static analysis helper for SkyrimSE.exe — RTTI class/vtable lookup + disassembly.

Why this exists: some TES4->TES5 contracts (which PLDT location types the engine
actually resolves, what a procedure needs before it will move an actor) cannot be
settled by comparing records against Skyrim.esm — vanilla only shows what
Bethesda *used*, not what the engine *accepts*.  SkyrimSE.exe ships with RTTI
type names intact (BGSProcedureEscort, BGSPackageDataLocation, BGSLocAlias, ...),
so the real behaviour is readable.

Read-only interoperability analysis: this never patches or redistributes anything.

Usage:
    # list RTTI classes matching a substring
    python tools/disasm/skyrim_disasm.py --find Procedure
    python tools/disasm/skyrim_disasm.py --find PackageDataLocation

    # vtable for a class (RVA + the function pointers it holds)
    python tools/disasm/skyrim_disasm.py --vtable BGSPackageDataLocation

    # disassemble at an RVA (or a vtable slot: --vtable X --slot N)
    python tools/disasm/skyrim_disasm.py --disasm 0x1234567 --count 80
    python tools/disasm/skyrim_disasm.py --vtable BGSProcedureEscort --slot 3 --count 120

    # follow calls/jumps found while disassembling
    python tools/disasm/skyrim_disasm.py --disasm 0x1234567 --count 200 --show-targets

    # the SAME analysis against the RUNNING game (--live)
    python tools/disasm/skyrim_disasm.py --live --find TopicInfo
    python tools/disasm/skyrim_disasm.py --live --vtable MenuTopicManager

Default exe path is auto-detected from the registry-installed SSE, override with
--exe.

🛑 THE STEAM EXE ON DISK IS DRM-PACKED — its `.text` is encrypted (entropy 8.00)
and static analysis of it yields garbage.  The GOG/AE copy disassembles
statically, but its RVAs do NOT match the build being played.  `--live` solves
both: the running process has `.text` DECRYPTED in memory, and its RVAs are by
construction the ones the running build uses, so an address found here can be
handed straight to the bridge (`hook`, `call`, `resolve`).  Prefer `--live`
whenever the game is up.
"""

import argparse
import os
import re
import struct
import sys

try:
    import pefile
except ImportError:
    sys.exit('pefile required: pip install pefile')
try:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64
    from capstone.x86 import X86_OP_MEM as _X86_OP_MEM
    from capstone.x86 import X86_REG_RIP as _X86_REG_RIP
except ImportError:
    sys.exit('capstone required: pip install capstone')


DEFAULT_EXE = (r'C:\Program Files (x86)\Steam\steamapps\common'
               r'\Skyrim Special Edition\SkyrimSE.exe')


class Binary:
    def __init__(self, path: str):
        self.path = path
        self.pe = pefile.PE(path, fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        with open(path, 'rb') as f:
            self.data = f.read()
        self._sections = [(s.VirtualAddress,
                           s.VirtualAddress + max(s.Misc_VirtualSize,
                                                  s.SizeOfRawData),
                           s.PointerToRawData, s.Name.rstrip(b'\0').decode())
                          for s in self.pe.sections]
        self.md = Cs(CS_ARCH_X86, CS_MODE_64)
        self.md.detail = True

    # -- address conversion ------------------------------------------------

    def rva_to_off(self, rva: int):
        for va, vend, praw, _ in self._sections:
            if va <= rva < vend:
                return praw + (rva - va)
        return None

    def off_to_rva(self, off: int):
        for va, vend, praw, _ in self._sections:
            size = vend - va
            if praw <= off < praw + size:
                return va + (off - praw)
        return None

    def va_to_rva(self, va: int) -> int:
        return va - self.base if va >= self.base else va

    def read(self, rva: int, n: int) -> bytes:
        off = self.rva_to_off(rva)
        if off is None:
            return b''
        return self.data[off:off + n]

    def u64(self, rva: int):
        b = self.read(rva, 8)
        return struct.unpack('<Q', b)[0] if len(b) == 8 else None

    def u32(self, rva: int):
        b = self.read(rva, 4)
        return struct.unpack('<I', b)[0] if len(b) == 4 else None

    # -- RTTI --------------------------------------------------------------

    def find_rtti_names(self, needle: str = '') -> list:
        """[(mangled, type_descriptor_rva)] for RTTI type names matching needle.

        An MSVC type descriptor is: vftable ptr (8) + spare (8) + name bytes.
        The name we match sits at descriptor+16.
        """
        out = []
        for m in re.finditer(rb'\.\?AV[A-Za-z0-9_@]{2,120}?@@', self.data):
            name = m.group(0).decode('latin1')
            if needle and needle.lower() not in name.lower():
                continue
            name_rva = self.off_to_rva(m.start())
            if name_rva is None:
                continue
            out.append((name, name_rva - 16))   # descriptor start
        return sorted(set(out))

    def vtables_for(self, class_name: str) -> list:
        """RVAs of vtables whose COL names `class_name`.

        Layout: [.. COL ptr][vtable start]. A complete object locator (COL) for
        x64 holds a *relative* pointer to the type descriptor at +12.
        """
        descs = [rva for nm, rva in self.find_rtti_names(class_name)
                 if nm == f'.?AV{class_name}@@']
        if not descs:
            return []
        desc = descs[0]
        cols = []
        # find COLs pointing at this descriptor (field at +12 is desc RVA)
        target = struct.pack('<I', desc)
        for m in re.finditer(re.escape(target), self.data):
            off = m.start()
            col_off = off - 12
            col_rva = self.off_to_rva(col_off)
            if col_rva is None:
                continue
            sig = self.u32(col_rva)
            if sig not in (0, 1):        # COL signature: 1 on x64
                continue
            cols.append(col_rva)
        # a vtable begins right after a pointer to its COL
        vts = []
        for col in cols:
            ptr = struct.pack('<Q', self.base + col)
            for m in re.finditer(re.escape(ptr), self.data):
                vt_rva = self.off_to_rva(m.start() + 8)
                if vt_rva is not None:
                    vts.append(vt_rva)
        return sorted(set(vts))

    def vtable_slots(self, vt_rva: int, n: int = 24) -> list:
        """[(slot, func_rva)] — stops at the first non-code pointer."""
        out = []
        for i in range(n):
            va = self.u64(vt_rva + i * 8)
            if not va or va < self.base:
                break
            rva = va - self.base
            if self.rva_to_off(rva) is None:
                break
            out.append((i, rva))
        return out

    # -- .pdata function bounds -------------------------------------------

    def runtime_functions(self):
        """Sorted [(begin_rva, end_rva, unwind_rva)] from the .pdata table.

        x64 PE images carry a complete function table for SEH unwinding, so
        function START and END are recorded facts, not guesses -- that is what
        makes "disassemble the whole function containing this address" exact
        even with no PDBs.
        """
        if getattr(self, '_pdata', None) is not None:
            return self._pdata
        d = self.pe.OPTIONAL_HEADER.DATA_DIRECTORY[3]      # EXCEPTION table
        rva, size = d.VirtualAddress, d.Size
        raw = self.read(rva, size)
        out = []
        for i in range(0, len(raw) - 11, 12):
            b, e, u = struct.unpack_from('<III', raw, i)
            if b == 0 and e == 0:
                continue
            out.append((b, e, u))
        out.sort()
        self._pdata = out
        return out

    def func_bounds(self, rva: int):
        """(begin, end) of the function containing `rva`, or None."""
        import bisect
        tbl = self.runtime_functions()
        i = bisect.bisect_right([t[0] for t in tbl], rva) - 1
        if i < 0:
            return None
        b, e, _ = tbl[i]
        return (b, e) if b <= rva < e else None

    # -- strings -----------------------------------------------------------

    def strings_at(self, rva: int, count: int = 20, maxlen: int = 400):
        """[(rva, text)] for `count` consecutive NUL-terminated strings."""
        out = []
        cur = rva
        for _ in range(count):
            blk = self.read(cur, maxlen)
            if not blk:
                break
            end = blk.find(b'\0')
            if end < 0:
                break
            out.append((cur, blk[:end].decode('latin1')))
            cur += end + 1
        return out

    # -- disassembly -------------------------------------------------------

    def disasm(self, rva: int, count: int = 60):
        off = self.rva_to_off(rva)
        if off is None:
            return []
        code = self.data[off:off + count * 16]
        return list(self.md.disasm(code, self.base + rva))[:count]


class LiveBinary(Binary):
    """`Binary`'s analysis surface, backed by the RUNNING process.

    The Steam build on disk is DRM-packed: its `.text` is encrypted, so every
    RTTI/vtable answer read from the file is garbage.  The loader decrypts it
    in memory, so the live process is both READABLE and — unlike the GOG copy —
    at the RVAs the running build actually uses.  That means an address found
    here can be handed straight to the bridge with no translation.

    The image is materialised ONCE into a flat, RVA-indexed buffer (offset ==
    RVA, so `rva_to_off`/`off_to_rva` are the identity).  Every inherited
    method reads `self.data`, so all of `Binary`'s RTTI and vtable logic works
    verbatim.  Regions that are not committed read back as zeros rather than
    failing the whole dump — a scan must not die because one page is unmapped.

    Read via ReadProcessMemory, NOT through the bridge's `readmem`: that caps
    at 4 KB per request, and a 30 MB image would be ~8,000 pipe round trips.
    """

    PROCESS_VM_READ = 0x0010
    PROCESS_QUERY_INFORMATION = 0x0400

    def __init__(self, pid: int = 0, chunk: int = 1 << 20):
        import ctypes
        self._ct = ctypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self._k32 = k32
        pid = pid or self._find_pid()
        self.pid = pid
        h = k32.OpenProcess(self.PROCESS_VM_READ | self.PROCESS_QUERY_INFORMATION,
                            False, pid)
        if not h:
            raise SystemExit(f'OpenProcess({pid}) failed: '
                             f'{ctypes.get_last_error()} (run as the same user)')
        self._h = h
        self.base, size = self._module_range()
        self.path = f'<live pid {pid}>'
        self.data = self._dump(self.base, size, chunk)
        # offset == rva for a virtual image
        self._sections = [(0, len(self.data), 0, '.live')]
        self.md = Cs(CS_ARCH_X86, CS_MODE_64)
        self.md.detail = True

    @staticmethod
    def _find_pid() -> int:
        import subprocess
        out = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "(Get-Process -Name SkyrimSE,SkyrimVR -ErrorAction SilentlyContinue |"
             " Select-Object -First 1).Id"],
            capture_output=True, text=True).stdout.strip()
        if not out.isdigit():
            raise SystemExit('neither SkyrimSE nor SkyrimVR is running (needed for --live)')
        return int(out)

    def _module_range(self):
        """(base, size) of the SkyrimSE.exe image in the target."""
        ctypes = self._ct
        from ctypes import wintypes
        psapi = ctypes.WinDLL('psapi', use_last_error=True)

        class MODULEINFO(ctypes.Structure):
            _fields_ = [('lpBaseOfDll', ctypes.c_void_p),
                        ('SizeOfImage', wintypes.DWORD),
                        ('EntryPoint', ctypes.c_void_p)]

        # Explicit argtypes: without them ctypes narrows the 64-bit HMODULE to
        # an int and OverflowErrors on a high image base.
        psapi.EnumProcessModules.argtypes = [wintypes.HANDLE,
                                             ctypes.POINTER(ctypes.c_void_p),
                                             wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]
        psapi.GetModuleInformation.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                               ctypes.c_void_p, wintypes.DWORD]
        mods = (ctypes.c_void_p * 1024)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcessModules(self._h, mods,
                                        ctypes.sizeof(mods),
                                        ctypes.byref(needed)):
            raise SystemExit('EnumProcessModules failed')
        # Module 0 is always the executable image itself.
        mi = MODULEINFO()
        if not psapi.GetModuleInformation(self._h, mods[0], ctypes.byref(mi),
                                          ctypes.sizeof(mi)):
            raise SystemExit('GetModuleInformation failed')
        return int(mi.lpBaseOfDll), int(mi.SizeOfImage)

    def _dump(self, base: int, size: int, chunk: int) -> bytes:
        ctypes = self._ct
        buf = ctypes.create_string_buffer(chunk)
        read = ctypes.c_size_t(0)
        out = bytearray()
        addr = base
        end = base + size
        while addr < end:
            n = min(chunk, end - addr)
            ok = self._k32.ReadProcessMemory(
                self._h, ctypes.c_void_p(addr), buf, n, ctypes.byref(read))
            if ok and read.value:
                out += buf.raw[:read.value]
                if read.value < n:          # partial: pad the hole
                    out += b'\0' * (n - read.value)
            else:
                # Uncommitted / guarded region -- zero-fill and keep going, or
                # one bad page would cost the entire scan.
                out += b'\0' * n
            addr += n
        return bytes(out)

    def __del__(self):
        try:
            if getattr(self, '_h', None):
                self._k32.CloseHandle(self._h)
        except Exception:
            pass

    # -- address conversion (offset == rva for a virtual image) -------------

    def rva_to_off(self, rva: int):
        return rva if 0 <= rva < len(self.data) else None

    def off_to_rva(self, off: int):
        return off if 0 <= off < len(self.data) else None

    def save_image(self, path: str):
        """Writes the decrypted virtual image for `--image` (offset == RVA)."""
        with open(path, 'wb') as f:
            f.write(self.data)


class ImageBinary(LiveBinary):
    """`Binary` over a virtual image saved by `--save-image`: the PE headers
    at offset 0 still describe the sections and the .pdata table, and every
    offset equals its RVA. `base` is the header's ImageBase, so printed
    addresses match a GOG-style static listing rather than one ASLR run."""

    def __init__(self, path: str):
        self.path = path
        with open(path, 'rb') as f:
            self.data = f.read()
        self.pe = pefile.PE(data=self.data[:0x1000], fast_load=True)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self._sections = [(0, len(self.data), 0, '.image')]
        self.md = Cs(CS_ARCH_X86, CS_MODE_64)
        self.md.detail = True

    def __del__(self):
        pass


_PRINTABLE = set(range(0x20, 0x7f)) | {9}


def _string_at(binary: Binary, rva: int, maxlen: int = 200):
    """The C string at `rva` if it looks like readable text, else None.

    `lea reg, [rip+disp]` is how every literal reaches a call site, so
    resolving those operands turns the engine's own error messages into an
    inline map of what each branch is checking -- which is the whole reason
    this annotation exists.
    """
    blk = binary.read(rva, maxlen)
    if not blk:
        return None
    end = blk.find(b'\0')
    if end < 4:
        return None
    s = blk[:end]
    if not all(c in _PRINTABLE for c in s):
        return None
    return s.decode('latin1')


def _fmt(binary: Binary, insns, show_targets=False):
    lines = []
    targets = []
    for ins in insns:
        rva = ins.address - binary.base
        note = ''
        if 'rip +' in ins.op_str or 'rip -' in ins.op_str:
            try:
                for op in ins.operands:
                    if op.type == _X86_OP_MEM and op.mem.base == _X86_REG_RIP:
                        tgt = ins.address + ins.size + op.mem.disp - binary.base
                        text = _string_at(binary, tgt)
                        note = (f'   ; {tgt:#x} {text!r}' if text
                                else f'   ; {tgt:#x}')
                        break
            except Exception:
                pass
        lines.append(f'  {rva:#010x}  {ins.mnemonic:<7} {ins.op_str}{note}')
        if show_targets and ins.mnemonic in ('call', 'jmp') and \
                ins.op_str.startswith('0x'):
            try:
                targets.append(int(ins.op_str, 16) - binary.base)
            except ValueError:
                pass
    return lines, targets


#: The help text and RTTI names carry non-cp1252 characters; keep them printable.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')


def _parse_args():
    """The command line as an argparse namespace."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--exe', default=DEFAULT_EXE)
    ap.add_argument('--find', help='list RTTI class names containing this')
    ap.add_argument('--vtable', help='show vtable(s) for this class name')
    ap.add_argument('--slot', type=int, help='with --vtable: disassemble this slot')
    ap.add_argument('--slots', type=int, default=24,
                    help='with --vtable: how many slots to list (default 24)')
    ap.add_argument('--disasm', help='disassemble at this RVA (hex ok)')
    ap.add_argument('--func',
                    help='disassemble the WHOLE function containing this RVA '
                         '(bounds come from the .pdata unwind table, so they '
                         'are exact without PDBs)')
    ap.add_argument('--strings',
                    help='dump consecutive NUL-terminated strings at this RVA')
    ap.add_argument('--count', type=int, default=60)
    ap.add_argument('--live', action='store_true',
                    help='analyse the RUNNING process instead of the file on '
                         'disk (REQUIRED for the Steam build, whose on-disk '
                         '.text is DRM-encrypted; also gives RVAs that match '
                         'the running build exactly)')
    ap.add_argument('--pid', type=int, default=0,
                    help='with --live: target pid (default: find SkyrimSE or SkyrimVR)')
    ap.add_argument('--save-image',
                    help='with --live: write the decrypted virtual image here '
                         'for later --image analysis with the game closed')
    ap.add_argument('--image',
                    help='analyse an image saved by --save-image instead of an exe')
    ap.add_argument('--show-targets', action='store_true',
                    help='list call/jmp targets found')
    return ap.parse_args()


def _load(args) -> Binary:
    """The Binary the flags select: live process, saved image, or exe."""
    if args.live:
        b = LiveBinary(args.pid)
        print(f'LIVE pid={b.pid}  imagebase={b.base:#x}  '
              f'image={len(b.data) / (1 << 20):.1f} MB')
        if args.save_image:
            b.save_image(args.save_image)
            print(f'saved image -> {args.save_image}')
        return b
    if args.image:
        b = ImageBinary(args.image)
        print(f'{os.path.basename(args.image)} (saved image)  imagebase={b.base:#x}')
        return b
    if not os.path.exists(args.exe):
        sys.exit(f'not found: {args.exe}')
    b = Binary(args.exe)
    print(f'{os.path.basename(args.exe)}  imagebase={b.base:#x}')
    return b


def _rva(b: Binary, text: str) -> int:
    """`text` (hex) as an RVA; a full VA is rebased first."""
    rva = int(text, 16)
    return rva - b.base if rva > b.base else rva


def _print_listing(b: Binary, insns, show_targets: bool):
    """Prints a formatted listing plus its call/jmp targets."""
    lines, tg = _fmt(b, insns, show_targets)
    print('\n'.join(lines))
    if tg:
        print('  targets: ' + ', '.join(hex(t) for t in sorted(set(tg))))


def _report_rtti(b: Binary, args):
    """Prints the --find and --vtable reports."""
    if args.find:
        names = b.find_rtti_names(args.find)
        print(f'\n{len(names)} RTTI names matching {args.find!r}:')
        for nm, rva in names:
            print(f'  {rva:#010x}  {nm}')
    if not args.vtable:
        return
    vts = b.vtables_for(args.vtable)
    print(f'\nvtables for {args.vtable}: '
          f'{", ".join(hex(v) for v in vts) or "none"}')
    for vt in vts:
        print(f'\n  vtable {vt:#x}:')
        for slot, frva in b.vtable_slots(vt, args.slots):
            print(f'    [{slot:2d}] {frva:#010x}')
    if args.slot is not None and vts:
        slots = dict(b.vtable_slots(vts[0], max(args.slots, args.slot + 1)))
        if args.slot in slots:
            rva = slots[args.slot]
            print(f'\n  disasm slot {args.slot} @ {rva:#x}:')
            _print_listing(b, b.disasm(rva, args.count), args.show_targets)


def _report_code(b: Binary, args):
    """Prints the --strings, --func and --disasm reports."""
    if args.strings:
        rva = _rva(b, args.strings)
        print(f'\nstrings @ {rva:#x}:')
        for srva, text in b.strings_at(rva, args.count):
            print(f'  {srva:#010x}  {text!r}')
    if args.func:
        rva = _rva(b, args.func)
        bounds = b.func_bounds(rva)
        if not bounds:
            print(f'\nno .pdata entry covers {rva:#x}')
        else:
            start, end = bounds
            print(f'\nfunction {start:#x}..{end:#x} ({end - start} bytes) contains '
                  f'{rva:#x}:')
            insns = [i for i in b.disasm(start, end - start)
                     if i.address - b.base < end]
            _print_listing(b, insns, args.show_targets)
    if args.disasm:
        rva = _rva(b, args.disasm)
        print(f'\ndisasm @ {rva:#x}:')
        _print_listing(b, b.disasm(rva, args.count), args.show_targets)


def main():
    args = _parse_args()
    b = _load(args)
    _report_rtti(b, args)
    _report_code(b, args)


if __name__ == '__main__':
    main()

"""Extract compiled-in default values for CreationKit.exe INI settings.

Bethesda registers each setting with a dynamic initializer thunk of the form:
    sub rsp,0x28
    movss xmm2, [rip+X]        ; float default   (or mov r8d,imm / xor r8d,r8d for int)
    lea   rdx, [rip+name]      ; "fFoo:Section"
    lea   rcx, [rip+object]    ; the Setting object
    call  Setting::ctor
So the default is recoverable by locating the single lea that references the
name string and walking back to the thunk head.

Usage:  python temp/ck_settings_dump.py --filter NavMeshGeneration
"""
import argparse, re, struct, sys
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64

DEFAULT_EXE = (r'C:\Program Files (x86)\Steam\steamapps\common'
               r'\Skyrim Special Edition\CreationKit.exe')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', default=DEFAULT_EXE)
    ap.add_argument('--filter', default='', help='substring of the setting name')
    a = ap.parse_args()

    pe = pefile.PE(a.exe, fast_load=True)
    d = open(a.exe, 'rb').read()
    secs = [(s.VirtualAddress, s.Misc_VirtualSize, s.PointerToRawData,
             s.SizeOfRawData, s.Name.rstrip(b'\0').decode()) for s in pe.sections]

    def r2o(rva):
        for va, vs, ra, rs, n in secs:
            if va <= rva < va + max(vs, rs):
                o = ra + (rva - va)
                if o < ra + rs:
                    return o
        return None

    def o2r(off):
        for va, vs, ra, rs, n in secs:
            if ra <= off < ra + rs:
                return va + (off - ra)
        return None

    tva, _, tra, trs, _ = [s for s in secs if s[4] == '.text' and s[3] > 0x1000000][0]
    code = d[tra:tra + trs]
    md = Cs(CS_ARCH_X86, CS_MODE_64)

    # names we care about
    pat = re.compile((r'[!-~][ -~]{2,80}:[A-Za-z]+\x00').encode())
    names = {}
    for m in pat.finditer(d):
        s = m.group(0)[:-1].decode('latin1')
        if a.filter and a.filter not in s:
            continue
        rva = o2r(m.start())
        if rva is not None:
            names[rva] = s
    if not names:
        sys.exit('no matching setting names')

    # ONE pass over .text: every 4-byte window that could be a rel32 landing on a
    # wanted string.  target = tva + off + 4 + disp
    wanted = set(names)
    hits = {}
    for off in range(0, trs - 4):
        disp = struct.unpack_from('<i', code, off)[0]
        tgt = tva + off + 4 + disp
        if tgt in wanted:
            hits.setdefault(tgt, []).append(tva + off)

    out = []
    for rva, nm in names.items():
        for reloc in hits.get(rva, []):
            start = None
            for back in range(4, 0x30):
                o = r2o(reloc - back)
                if o is not None and d[o:o + 4] == b'\x48\x83\xec\x28':
                    start = reloc - back
                    break
            if start is None:
                out.append((nm, None, 'no-thunk', hex(reloc)))
                continue
            o = r2o(start)
            val, kind = None, '?'
            for ins in md.disasm(d[o:o + 0x60], start):
                ops = ins.op_str
                if ins.mnemonic in ('movss', 'movsd') and ops.startswith('xmm2') and 'rip' in ops:
                    disp = int(ops.split('rip + ')[1].rstrip(']'), 16)
                    oo = r2o(ins.address + ins.size + disp)
                    val = struct.unpack_from('<f', d, oo)[0]
                    kind = 'float'
                elif ins.mnemonic == 'mov' and re.match(r'r8[db]?, 0x', ops):
                    val = int(ops.split(',')[1].strip(), 16); kind = 'int'
                elif ins.mnemonic == 'xor' and re.match(r'r8[db]?, r8[db]?', ops):
                    val = 0; kind = 'int'
                if ins.mnemonic == 'call':
                    break
            out.append((nm, val, kind, hex(start)))

    for nm, val, kind, addr in sorted(out):
        print('%-64s %-14s %-6s %s' % (nm, val, kind, addr))

main()

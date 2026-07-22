"""Index every rip-relative reference from .text to a set of strings, one pass."""
import argparse, re, struct, sys
import pefile

DEFAULT_EXE = (r'C:\Program Files (x86)\Steam\steamapps\common'
               r'\Skyrim Special Edition\CreationKit.exe')

ap = argparse.ArgumentParser()
ap.add_argument('--exe', default=DEFAULT_EXE)
ap.add_argument('--pattern', required=True, help='regex matched against strings')
a = ap.parse_args()

pe = pefile.PE(a.exe, fast_load=True)
d = open(a.exe, 'rb').read()
secs = [(s.VirtualAddress, s.Misc_VirtualSize, s.PointerToRawData,
         s.SizeOfRawData, s.Name.rstrip(b'\0').decode()) for s in pe.sections]
def o2r(off):
    for va, vs, ra, rs, n in secs:
        if ra <= off < ra + rs:
            return va + (off - ra)
    return None
tva, _, tra, trs, _ = [s for s in secs if s[4] == '.text' and s[3] > 0x1000000][0]
code = d[tra:tra + trs]

rx = re.compile(a.pattern)
names = {}
for m in re.finditer(rb'[ -~]{5,250}\x00', d):
    s = m.group(0)[:-1].decode('latin1')
    if rx.search(s):
        rva = o2r(m.start())
        if rva is not None:
            names[rva] = s
wanted = set(names)
hits = {}
for off in range(0, trs - 4):
    disp = struct.unpack_from('<i', code, off)[0]
    tgt = tva + off + 4 + disp
    if tgt in wanted:
        hits.setdefault(tgt, []).append(tva + off)
rows = []
for rva, nm in names.items():
    for h in hits.get(rva, []):
        rows.append((h, nm))
for h, nm in sorted(rows):
    print('%08x  %s' % (h, nm))

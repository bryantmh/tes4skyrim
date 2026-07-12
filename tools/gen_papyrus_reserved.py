"""Regenerate script_convert/papyrus_reserved.txt from Skyrim's Scripts.zip.

The Papyrus compiler rejects a variable or property named the same as ANY script
it can see on the import path ("cannot name a variable or property the same as a
known type or script"), and then every use of that name fails too ("Door is not a
variable") — one bad name takes its whole dependency chain down with it.

The reserved set is therefore every script name Skyrim ships.  Read it from
Data/Scripts.zip (Bethesda's pristine source archive) rather than
Data/Source/Scripts, which on a modded install also contains the user's mod
scripts and would make conversion output depend on their load order.

Usage:
  python tools/gen_papyrus_reserved.py
"""
import os
import sys
import zipfile

SSE = r'C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition'
ZIP = os.path.join(SSE, 'Data', 'Scripts.zip')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'script_convert', 'papyrus_reserved.txt')


def main():
    if not os.path.isfile(ZIP):
        sys.exit(f'Not found: {ZIP}')
    with zipfile.ZipFile(ZIP) as z:
        names = {os.path.splitext(os.path.basename(n))[0]
                 for n in z.namelist() if n.lower().endswith('.psc')}
    names = sorted(n for n in names if n)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('# Every script name Skyrim ships (from Data/Scripts.zip).\n')
        f.write('# A converted property/variable may not reuse any of these.\n')
        f.write('# Regenerate with: python tools/gen_papyrus_reserved.py\n')
        for n in names:
            f.write(n + '\n')
    print(f'Wrote {len(names)} names -> {OUT}')


if __name__ == '__main__':
    main()

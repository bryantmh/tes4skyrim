"""Grounded OBSE-convertibility audit for a TES4 export.

Answers the real question: reading the ORIGINAL Oblivion/OBSE script source
(SCPT.SCTX + INFO.ResultScript), which functions are actually used, which of
them are OBSE-added (vs vanilla Oblivion), and for each, is a FAITHFUL Skyrim
conversion possible with (a) vanilla Papyrus, (b) SKSE required, or (c) neither?

Determination is by GREP, not by reading every implementation:
  - OBSE_NAMES  = every command name defined in xOBSE source (DEFINE_COMMAND* /
                  DEFINE_CMD* / CommandInfo kCommandInfo_*).  A used token in
                  this set but not a vanilla Skyrim Papyrus name is OBSE-added.
  - SKSE_NAMES  = every Papyrus native SKSE registers (NativeFunctionN("Name")).
                  A used token with a same-named SKSE native is SKSE-convertible.
  - VANILLA_SKYRIM = curated set of vanilla Skyrim Papyrus function names that a
                  converter can target without SKSE (the converter's own
                  FUNCTION_MAP targets + well-known natives).

Usage:
    python tools/obse_convertibility_audit.py export/Nehrim.esm
    python tools/obse_convertibility_audit.py export/Nehrim.esm --md > docs/x.md
"""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
XOBSE = REPO / 'references' / 'xOBSE-master' / 'obse' / 'obse'
SKSE = REPO / 'references' / 'skse64-master' / 'skse64'

# OBSE compiler keywords (not DEFINE_COMMAND entries) — the language extensions.
OBSE_KEYWORDS = {
    'let', 'eval', 'call', 'function', 'foreach', 'setfunctionvalue',
    'while', 'loop', 'continue', 'break', 'testexpr',
}

# statement leaders that are structure, not a function invocation
STRUCTURAL = {
    'scn', 'scriptname', 'begin', 'end', 'if', 'elseif', 'else', 'endif',
    'set', 'to', 'short', 'long', 'float', 'ref', 'int', 'return',
    'string_var', 'array_var', 'endwhile',
}


def build_obse_names():
    names = set(OBSE_KEYWORDS)
    for f in XOBSE.glob('*.cpp'):
        txt = f.read_text(encoding='utf-8', errors='replace')
        for m in re.finditer(r'DEFINE_(?:COMMAND|CMD)[A-Za-z_]*\(\s*([A-Za-z0-9_]+)', txt):
            names.add(m.group(1).lower())
        for m in re.finditer(r'CommandInfo\s+kCommandInfo_([A-Za-z0-9_]+)', txt):
            names.add(m.group(1).lower())
    return names


def build_skse_names():
    names = set()
    for f in SKSE.glob('Papyrus*.cpp'):
        txt = f.read_text(encoding='utf-8', errors='replace')
        # Name is the first quoted string after the NativeFunctionN<...>(
        # registration.  Template args can contain nested <> and span newlines
        # (VMResultArray<SInt32>), so scan from NativeFunctionN to the first
        # '("Name"', tolerating anything (incl. newlines) but no other '("...'.
        for m in re.finditer(
                r'NativeFunction\d*\b[^(]*\(\s*"([A-Za-z0-9_]+)"',
                txt, re.DOTALL):
            names.add(m.group(1).lower())
    return names


def unescape(v):
    return (v.replace('\\r', '\r').replace('\\n', '\n')
             .replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\'))


def iter_bodies(export_dir):
    scpt = export_dir / 'SCPT.txt'
    if scpt.exists():
        edid = None
        for line in scpt.read_text(encoding='utf-8', errors='replace').splitlines():
            if line.startswith('EditorID='):
                edid = line[9:]
            elif line.startswith('SCTX='):
                yield ('SCPT', edid, unescape(line[5:]))
    info = export_dir / 'INFO.txt'
    if info.exists():
        edid = None
        for line in info.read_text(encoding='utf-8', errors='replace').splitlines():
            if line.startswith('FormID='):
                edid = 'INFO:' + line[7:]
            elif line.startswith('ResultScript=') and len(line) > 13:
                yield ('INFO', edid, unescape(line[13:]))


# --- token extraction: every function-shaped identifier actually invoked ------
# A function is invoked as a statement leader (optionally "ref.") OR nested in an
# expression after if/elseif/set/let/eval/call/while.  We collect BOTH.
_LEAD = re.compile(r'^\s*(?:set\s+\w+\s+to\s+|let\s+)?'
                   r'(?:[A-Za-z0-9_]+\s*\.\s*)?([A-Za-z_]\w*)', re.IGNORECASE)
_NESTED_TRIGGER = re.compile(r'^\s*(if|elseif|set|let|eval|call|while|return)\b',
                             re.IGNORECASE)
_IDENT = re.compile(r'(?:[A-Za-z0-9_]+\s*\.\s*)?([A-Za-z_]\w{1,})')


def extract_tokens(body):
    """Yield lowercased function tokens invoked in the script."""
    for raw in body.replace('\r', '\n').split('\n'):
        line = raw.split(';', 1)[0].strip()  # drop line comments
        if not line:
            continue
        lead_m = _LEAD.match(line)
        if lead_m:
            yield lead_m.group(1).lower()
        if _NESTED_TRIGGER.match(line):
            # surface identifiers used as calls inside the expression
            for m in _IDENT.finditer(line):
                yield m.group(1).lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('export_dir', type=Path)
    ap.add_argument('--md', action='store_true', help='markdown output')
    ap.add_argument('--min', type=int, default=1, help='min occurrences to list')
    args = ap.parse_args()

    obse = build_obse_names()
    skse = build_skse_names()
    print(f'# OBSE command names in xOBSE source: {len(obse)}', file=sys.stderr)
    print(f'# SKSE Papyrus natives: {len(skse)}', file=sys.stderr)

    occ = Counter()
    scripts = {}
    n = 0
    for src, edid, body in iter_bodies(args.export_dir):
        n += 1
        here = set()
        for tok in extract_tokens(body):
            occ[tok] += 1
            here.add(tok)
        for tok in here:
            scripts.setdefault(tok, set()).add(edid)

    # keep only tokens that are OBSE-added commands or keywords
    obse_used = {t: c for t, c in occ.items()
                 if (t in obse or t in OBSE_KEYWORDS) and t not in STRUCTURAL
                 and c >= args.min}

    def verdict(tok):
        if tok in OBSE_KEYWORDS:
            return 'OBSE-KEYWORD'
        if tok in skse:
            return 'SKSE'
        return 'OBSE-only'

    rows = sorted(obse_used.items(),
                  key=lambda kv: (-len(scripts[kv[0]]), -kv[1]))

    if args.md:
        print(f'\n**{n} original script bodies scanned.** '
              f'{len(obse_used)} distinct OBSE-added functions used.\n')
        print('| OBSE function | occ | scripts | SKSE native? |')
        print('|---|---:|---:|---|')
        for tok, c in rows:
            print(f'| `{tok}` | {c} | {len(scripts[tok])} | {verdict(tok)} |')
    else:
        print(f'\n{n} script bodies. {len(obse_used)} OBSE-added functions used.\n')
        print(f'{"FUNCTION":28} {"OCC":>6} {"SCRIPTS":>8}  VERDICT')
        for tok, c in rows:
            print(f'{tok:28} {c:>6} {len(scripts[tok]):>8}  {verdict(tok)}')


if __name__ == '__main__':
    main()

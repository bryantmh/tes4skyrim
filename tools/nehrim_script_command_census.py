"""Census of Oblivion/OBSE script commands actually used in an export.

Extracts every script body (SCPT.SCTX + INFO.ResultScript) from a TES4 export
directory, tokenises the leading command of each statement, and reports raw
usage counts.  Optionally buckets commands by an SKSE-unlock verdict table so
the audit can cite CONCRETE numbers instead of "may use".

Usage:
    python tools/nehrim_script_command_census.py export/Nehrim.esm
    python tools/nehrim_script_command_census.py export/Nehrim.esm --grep ar_ sv_ StreamMusic
    python tools/nehrim_script_command_census.py export/Nehrim.esm --verdict
"""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

# --- statement-leading tokens that are control flow / structure, not commands
STRUCTURAL = {
    'scn', 'scriptname', 'begin', 'end', 'if', 'elseif', 'else', 'endif',
    'while', 'loop', 'endwhile', 'foreach', 'return', 'set', 'let', 'to',
    'short', 'long', 'float', 'ref', 'int', 'string_var', 'array_var',
    'setfunctionvalue', 'eval', 'call', 'continue', 'break',
}

# member-call form: "ref.Command args" or "Command args"
_TOKEN_RE = re.compile(r'^\s*(?:(?:set|let)\s+)?'
                       r'(?:[A-Za-z0-9_]+\s*\.\s*)?'   # optional "ref." prefix
                       r'([A-Za-z_][A-Za-z0-9_]*)')


def unescape(v: str) -> str:
    return (v.replace('\\r', '\r').replace('\\n', '\n')
             .replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\'))


def iter_script_bodies(export_dir: Path):
    """Yield (source_file, editor_id, body_text) for every script body."""
    # SCPT.SCTX
    scpt = export_dir / 'SCPT.txt'
    if scpt.exists():
        edid, body = None, None
        for line in scpt.read_text(encoding='utf-8', errors='replace').splitlines():
            if line.startswith('EditorID='):
                edid = line[9:]
            elif line.startswith('SCTX='):
                yield ('SCPT', edid, unescape(line[5:]))
    # INFO.ResultScript
    info = export_dir / 'INFO.txt'
    if info.exists():
        edid = None
        for line in info.read_text(encoding='utf-8', errors='replace').splitlines():
            if line.startswith('FormID='):
                edid = 'INFO:' + line[7:]
            elif line.startswith('ResultScript=') and len(line) > 13:
                yield ('INFO', edid, unescape(line[13:]))


def tokenise(body: str):
    """Yield every command token used in the script (lowercased).

    A statement's leading token is the command it invokes.  Commands also
    appear nested inside `if`/`elseif`/`set`/`let`/`eval` expressions
    (`if Player.HasSpell x`), so for those structural leaders we additionally
    scan the expression for known-command-shaped identifiers.  To avoid
    double-counting, the leading token is emitted once; nested identifiers are
    emitted only when the leader is structural (a container for other calls).
    """
    for raw in body.replace('\r', '\n').split('\n'):
        line = raw.strip()
        if not line or line.startswith(';'):
            continue
        m = _TOKEN_RE.match(line)
        if not m:
            continue
        lead = m.group(1).lower()
        if lead in STRUCTURAL:
            # a container line — surface the commands nested in its expression
            for inner in re.findall(r'\b([A-Za-z_][A-Za-z0-9_]{2,})\b', line):
                il = inner.lower()
                if il not in STRUCTURAL:
                    yield il
        else:
            yield lead


def command_census(export_dir: Path):
    per_command = Counter()          # command -> occurrence count
    scripts_with = {}                # command -> set of script edids
    n_scripts = 0
    for src, edid, body in iter_script_bodies(export_dir):
        n_scripts += 1
        seen_here = set()
        for tok in tokenise(body):
            per_command[tok] += 1
            seen_here.add(tok)
        for tok in seen_here:
            scripts_with.setdefault(tok, set()).add(edid)
    return per_command, scripts_with, n_scripts


# --- SKSE unlock verdict table (command family -> verdict) -------------------
# Keys are matched as: exact lowercase, or prefix if ending in '*'.
VERDICT = {
    # UNLOCKED by SKSE
    'setactorfullname': 'UNLOCKED', 'setdisplayname': 'UNLOCKED',
    'setname': 'UNLOCKED', 'setcellfullname': 'UNLOCKED',
    'setitemvalue': 'UNLOCKED', 'isswimming': 'UNLOCKED',
    'getignorefriendlyhits': 'UNLOCKED',
    'getspellcount': 'UNLOCKED', 'getnthspell': 'UNLOCKED',
    'iskeypressed': 'UNLOCKED', 'iskeypressed2': 'UNLOCKED',
    'tapkey': 'UNLOCKED', 'holdkey': 'UNLOCKED', 'releasekey': 'UNLOCKED',
    'ismodloaded': 'UNLOCKED', 'getmodindex': 'UNLOCKED',
    'getstringinisetting': 'UNLOCKED', 'getnumericinisetting': 'UNLOCKED',
    'getfirstref': 'UNLOCKED', 'getnextref': 'UNLOCKED', 'getnumrefs': 'UNLOCKED',
    'getinventoryobject': 'UNLOCKED', 'getnumitems': 'UNLOCKED',
    # OBSE arrays / strings — real SKSE arrays/strings, but restructuring needed
    'ar_*': 'PARTIAL', 'sv_*': 'PARTIAL',
    'stringtoactorvalue': 'PARTIAL',
    'getnthdetectedactor': 'PARTIAL', 'setlevel': 'PARTIAL',
    'setcombatstyle': 'PARTIAL', 'gettalkedtopc': 'PARTIAL',
    # STILL BLOCKED
    'forceweather': 'BLOCKED', 'setweather': 'BLOCKED', 'sw': 'BLOCKED',
    'fw': 'BLOCKED', 'getweatherpercent': 'BLOCKED',
    'getiscurrentweather': 'BLOCKED', 'releaseweatheroverride': 'BLOCKED',
    'streammusic': 'BLOCKED', 'emc*': 'BLOCKED',
    'getcurrentpackage': 'BLOCKED', 'getiscurrentpackage': 'BLOCKED',
    'getcurrentaiprocedure': 'BLOCKED', 'getcurrentaipackage': 'BLOCKED',
    'getdisposition': 'BLOCKED', 'moddisposition': 'BLOCKED',
    'getplayerhaslastriddenhorse': 'BLOCKED',
    'hasflames': 'BLOCKED', 'addflames': 'BLOCKED', 'removeflames': 'BLOCKED',
    'flameson': 'BLOCKED', 'flamesoff': 'BLOCKED',
    'positioncell': 'BLOCKED', 'closeobliviongate': 'BLOCKED',
    'setrigidbodymass': 'BLOCKED', 'resetfalldamagetimer': 'BLOCKED',
    'setforcesneak': 'BLOCKED', 'forceflee': 'BLOCKED', 'setactorsai': 'BLOCKED',
}


def verdict_for(cmd: str):
    if cmd in VERDICT:
        return VERDICT[cmd]
    for k, v in VERDICT.items():
        if k.endswith('*') and cmd.startswith(k[:-1]):
            return v
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('export_dir', type=Path)
    ap.add_argument('--grep', nargs='*', help='only show commands matching these prefixes')
    ap.add_argument('--verdict', action='store_true', help='bucket by SKSE unlock verdict')
    ap.add_argument('--top', type=int, default=60)
    args = ap.parse_args()

    per_cmd, scripts_with, n_scripts = command_census(args.export_dir)
    print(f'# {n_scripts} script bodies scanned in {args.export_dir}\n')

    if args.grep:
        pats = [p.lower() for p in args.grep]
        print(f'{"COMMAND":32} {"OCCURS":>8} {"SCRIPTS":>8}')
        for cmd in sorted(per_cmd, key=lambda c: -per_cmd[c]):
            if any(cmd.startswith(p) for p in pats):
                print(f'{cmd:32} {per_cmd[cmd]:>8} {len(scripts_with[cmd]):>8}')
        return

    if args.verdict:
        buckets = {'UNLOCKED': Counter(), 'PARTIAL': Counter(), 'BLOCKED': Counter()}
        bucket_scripts = {'UNLOCKED': set(), 'PARTIAL': set(), 'BLOCKED': set()}
        for cmd, n in per_cmd.items():
            v = verdict_for(cmd)
            if v:
                buckets[v][cmd] = n
                bucket_scripts[v] |= scripts_with[cmd]
        for v in ('UNLOCKED', 'PARTIAL', 'BLOCKED'):
            tot = sum(buckets[v].values())
            print(f'\n=== {v}: {tot} occurrences across '
                  f'{len(bucket_scripts[v])} scripts ===')
            for cmd, n in buckets[v].most_common():
                print(f'  {cmd:30} {n:>6} occ  {len(scripts_with[cmd]):>5} scripts')
        return

    print(f'{"COMMAND":32} {"OCCURS":>8} {"SCRIPTS":>8}')
    for cmd, n in per_cmd.most_common(args.top):
        if cmd in STRUCTURAL:
            continue
        print(f'{cmd:32} {n:>8} {len(scripts_with[cmd]):>8}')


if __name__ == '__main__':
    main()

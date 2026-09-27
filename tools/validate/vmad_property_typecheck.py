"""Predict VMAD property binding failures WITHOUT running the game.

The engine binds a VMAD object property only when the target record's type is
compatible with the type the script DECLARES. When it is not, the property
reads None for the whole session and silently aborts every function that
touches it -- and the only evidence is a line in the Papyrus log, which costs a
full play session to obtain.

This does the same check statically: for every `<Type> Property <Name>` in the
converted sources, resolve <Name> to its record in the output plugin and report
the pairs that cannot bind. Run it after `--scripts-only` to catch a whole class
of runtime failures before shipping.

Usage:
  python tools/validate/vmad_property_typecheck.py --plugin Oblivion.esm
  python tools/validate/vmad_property_typecheck.py --plugin Morrowind_ob.esm --verbose
  python tools/validate/vmad_property_typecheck.py --plugin ElsweyrPelletine.esp --cross-master

The NAME pass above resolves a property name against this plugin only, so a
property bound to a MASTER's record is reported against whatever same-named
record this plugin happens to own. Ambiguity inside one plugin is filtered.

`--cross-master` closes that gap from the other direction, and finds a defect
the name pass structurally cannot see: it reads each property's ACTUAL FormID
out of the VMAD, splits off the index byte, resolves that byte through THIS
plugin's MAST list by name, and looks the local id up in that master's own
output. A FormID copied verbatim from a master keeps the index byte that master
used, and the same byte means a different file here -- so the property silently
binds to whatever unrelated record happens to occupy that id.

Measured (2026-08-12, ElsweyrPelletine.esp): `Faction ANQCORCorintheFaction`
carried 020247E2, where 02 = Tamriel.esp in Pelletine's MAST list, resolving to
a LAND record; the intended FACT of the same local id lives in
ElsweyrAnequina.esp at index 03. The type pass reported 0 failures because no
record named ANQCORCorintheFaction exists in Pelletine at all. In game the
property read None, GetCrimeGoldViolent() aborted every call, and the script's
unconditional RegisterForSingleUpdate(0.5) re-armed forever.
See project_raw_formid_meaningless_across_plugins / project_master_index_routing.
"""
import argparse
import os
import re
import struct
import sys
import zlib
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from output_layout import paths

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# Papyrus type -> record signatures that satisfy it. Only the types the
# converter actually emits need an entry; anything else is reported as unknown
# rather than guessed at.
_ACCEPTS = {
    'Armor': {'ARMO'}, 'Weapon': {'WEAP'}, 'Book': {'BOOK'},
    'Potion': {'ALCH'}, 'Ingredient': {'INGR'}, 'Light': {'LIGH'},
    'MiscObject': {'MISC'}, 'Key': {'KEYM'}, 'Ammo': {'AMMO'},
    'SoulGem': {'SLGM'}, 'Scroll': {'SCRL'}, 'Activator': {'ACTI'},
    'Flora': {'FLOR'}, 'Furniture': {'FURN'}, 'Static': {'STAT'},
    'Container': {'CONT'}, 'Door': {'DOOR'},
    'LeveledItem': {'LVLI'}, 'LeveledActor': {'LVLN'},
    'LeveledSpell': {'LVSP'},
    'Quest': {'QUST'}, 'Faction': {'FACT'}, 'GlobalVariable': {'GLOB'},
    'Spell': {'SPEL'}, 'Enchantment': {'ENCH'}, 'MagicEffect': {'MGEF'},
    'Race': {'RACE'}, 'Class': {'CLAS'}, 'Package': {'PACK'},
    'Sound': {'SOUN', 'SNDR'}, 'Topic': {'DIAL'}, 'FormList': {'FLST'},
    'Keyword': {'KYWD'}, 'EffectShader': {'EFSH'}, 'Weather': {'WTHR'},
    'WorldSpace': {'WRLD'}, 'Message': {'MESG'}, 'Outfit': {'OTFT'},
    'VoiceType': {'VTYP'}, 'ImageSpaceModifier': {'IMAD'},
    'Explosion': {'EXPL'}, 'Projectile': {'PROJ'}, 'Hazard': {'HAZD'},
    'ImpactDataSet': {'IPDS'}, 'Idle': {'IDLE'}, 'Shout': {'SHOU'},
    'ActorBase': {'NPC_'}, 'Location': {'LCTN'},
    # A Cell property binds ONLY to an interior cell -- an exterior grid cell
    # is not addressable this way (verified: all 43 vanilla Skyrim Cell
    # properties name interiors). Checked specially below.
    'Cell': {'CELL'},
    # Reference types: a placed ref, or anything for the permissive ones.
    'ObjectReference': {'REFR', 'ACHR', 'ACRE'},
    'Actor': {'ACHR', 'ACRE', 'REFR'},
}
# These accept anything; never report them.
_PERMISSIVE = {'Form', 'ScriptObject', 'Alias', 'ReferenceAlias'}


def _read_vmad_bindings(path):
    """script name (lower) -> set of property names actually bound in any VMAD.

    Walks the raw file rather than the record parser: a VMAD can hang off any
    record type, and only the property NAMES are needed here.
    """
    data = open(path, 'rb').read()
    bound = defaultdict(set)
    pos = 0
    while True:
        i = data.find(b'VMAD', pos)
        if i < 0:
            break
        pos = i + 4
        size = struct.unpack_from('<H', data, i + 4)[0]
        v = data[i + 6:i + 6 + size]
        if len(v) < 8:
            continue
        try:
            ver, fmt, nscripts = struct.unpack_from('<hhH', v, 0)
            # Guard against a false 'VMAD' hit inside arbitrary record data.
            if ver != 5 or fmt != 2 or not 0 <= nscripts <= 50:
                continue
            p = 6
            for _ in range(nscripts):
                ln = struct.unpack_from('<H', v, p)[0]
                p += 2
                sname = v[p:p + ln].decode('ascii', 'replace')
                p += ln + 1                      # + flags byte
                nprops = struct.unpack_from('<H', v, p)[0]
                p += 2
                for _ in range(nprops):
                    pl = struct.unpack_from('<H', v, p)[0]
                    p += 2
                    pname = v[p:p + pl].decode('ascii', 'replace')
                    p += pl
                    ptype = v[p]
                    p += 2                       # type + status
                    bound[sname.lower()].add(pname)
                    if ptype == 1:               # object: unused+alias+formID
                        p += 8
                    elif ptype in (2, 3, 4, 5):  # string handled below
                        p += 4
                    elif ptype == 11:            # array of objects
                        n = struct.unpack_from('<I', v, p)[0]
                        p += 4 + n * 8
                    else:
                        raise ValueError('unhandled property type')
        except Exception:
            continue
    return bound


def _masters(path):
    """MAST names in load order; the index byte offsets into this list."""
    with open(path, 'rb') as fh:
        head = fh.read(24)
        if len(head) < 24 or head[:4] != b'TES4':
            return []
        body = fh.read(struct.unpack_from('<I', head, 4)[0])
    out, pos = [], 0
    while pos + 6 <= len(body):
        sig = body[pos:pos + 4]
        size = struct.unpack_from('<H', body, pos + 4)[0]
        pos += 6
        if sig == b'MAST':
            out.append(body[pos:pos + size].rstrip(b'\0').decode('latin-1'))
        pos += size
    return out


def _record_index(path):
    """local formid -> [(signature, editorid), ...] for every record.

    A list, not a single entry: a converted plugin can carry the SAME local id
    under two signatures (ElsweyrAnequina 075923 is both a REFR and the QUST
    ANQHuntersGuild01). Keeping only the first hit reports whichever the walk
    reached first and invents mis-routings that do not exist, so the caller is
    given every candidate and accepts the property if ANY of them fits.
    """
    data = open(path, 'rb').read()
    out = defaultdict(list)
    pos, n = 0, len(data)
    while pos + 24 <= n:
        sig = data[pos:pos + 4]
        if sig == b'GRUP':
            pos += 24
            continue
        size, flags, fid = struct.unpack_from('<III', data, pos + 4)
        body = data[pos + 24:pos + 24 + size]
        if flags & 0x00040000:
            try:
                body = zlib.decompress(body[4:])
            except zlib.error:
                body = b''
        edid, q = None, 0
        while q + 6 <= len(body):
            s2 = body[q:q + 4]
            sz2 = struct.unpack_from('<H', body, q + 4)[0]
            q += 6
            if s2 == b'EDID':
                edid = body[q:q + sz2].split(b'\0')[0].decode('latin-1')
                break
            q += sz2
        out[fid & 0xFFFFFF].append((sig.decode('latin-1'), edid))
        pos += 24 + size
    return out


def _vmad_property_formids(path):
    """(script, property, formid) for every OBJECT property bound in a VMAD.

    Same walk as _read_vmad_bindings, but keeps the FormID instead of
    discarding it -- that id is the whole subject of the cross-master check.
    """
    data = open(path, 'rb').read()
    out, pos = [], 0
    while True:
        i = data.find(b'VMAD', pos)
        if i < 0:
            break
        pos = i + 4
        size = struct.unpack_from('<H', data, i + 4)[0]
        v = data[i + 6:i + 6 + size]
        if len(v) < 8:
            continue
        found = []
        try:
            ver, fmt, nscripts = struct.unpack_from('<hhH', v, 0)
            if ver != 5 or fmt != 2 or not 0 <= nscripts <= 50:
                continue
            p = 6
            for _ in range(nscripts):
                ln = struct.unpack_from('<H', v, p)[0]
                p += 2
                sname = v[p:p + ln].decode('ascii', 'replace')
                p += ln + 1
                nprops = struct.unpack_from('<H', v, p)[0]
                p += 2
                for _ in range(nprops):
                    pl = struct.unpack_from('<H', v, p)[0]
                    p += 2
                    pname = v[p:p + pl].decode('ascii', 'replace')
                    p += pl
                    ptype = v[p]
                    p += 2
                    if ptype == 1:
                        fid = struct.unpack_from('<I', v, p + 4)[0]
                        found.append((sname, pname, fid))
                        p += 8
                    elif ptype in (2, 3, 4, 5):
                        p += 4
                    elif ptype == 11:
                        n = struct.unpack_from('<I', v, p)[0]
                        p += 4 + n * 8
                    else:
                        raise ValueError('unhandled property type')
        except Exception:
            continue
        out.extend(found)
    return out


def _declared_types(src):
    """(script lower, property) -> declared Papyrus type, from the converted sources."""
    decl = re.compile(r'^\s*([A-Za-z_]\w*)\s+Property\s+(\w+)', re.M)
    declared = {}
    for fn in sorted(os.listdir(src)):
        if not fn.endswith('.psc'):
            continue
        text = open(os.path.join(src, fn), encoding='utf-8',
                    errors='replace').read()
        for m in decl.finditer(text):
            declared[(fn[:-4].lower(), m.group(2))] = m.group(1)
    return declared


def _accepted(ptype, script_types):
    """Signatures a property of `ptype` binds; () for a script type (any record); None to skip.

    A property typed as a converted script binds any record carrying that
    script, so only the record's existence is checked (an OBSE function's host quest).
    """
    if ptype is None or ptype in _PERMISSIVE:
        return None
    if ptype.lower() in script_types:
        return ()
    return _ACCEPTS.get(ptype)


class _MasterTables:
    """Record indexes of this plugin and its masters, each read from its OWN converted output."""

    def __init__(self, plugin, esm, out_dir):
        """Index this plugin now; masters load on first use."""
        self.plugin, self.out_dir = plugin, out_dir
        self.masters = _masters(esm)
        self.own = _record_index(esm)
        self.cache = {}

    def table_for(self, idx):
        """(file name, record index or None) for an index byte; (None, None) past the MAST list.

        Index == len(masters) is this file itself: its records sit right after its masters.
        """
        if idx == len(self.masters):
            return self.plugin, self.own
        if idx > len(self.masters):
            return None, None
        name = self.masters[idx]
        if name not in self.cache:
            path = str(paths(name, out_root=self.out_dir).esm)
            self.cache[name] = _record_index(path) if os.path.exists(path) else None
        return name, self.cache[name]


def _binding_problem(hits, accepts):
    """(signature, EditorID) describing why the id cannot bind, or None when ANY hit fits."""
    if not hits:
        return '<no such record>', None
    if not accepts or any(sig in accepts for sig, _ in hits):
        return None
    sig, edid = hits[0]
    if len(hits) > 1:
        sig = '/'.join(sorted({s for s, _ in hits}))
    return sig, edid


def _report_cross_master(plugin, src, limit, verbose):
    """Resolve each bound property's real FormID through the MAST list.

    A mis-routed index byte points the property at an unrelated record, which
    the name-based pass cannot see because the intended record is not in this
    plugin at all.
    """
    out_dir = os.path.join(ROOT, 'output')
    esm = str(paths(plugin, out_root=out_dir).esm)
    tables = _MasterTables(plugin, esm, out_dir)
    declared = _declared_types(src)
    script_types = {s for s, _p in declared}
    checked = 0
    bad = []
    unresolved = Counter()
    for sname, pname, fid in _vmad_property_formids(esm):
        ptype = declared.get((sname.lower(), pname))
        accepts = _accepted(ptype, script_types)
        if fid == 0 or accepts is None:
            continue
        src_name, table = tables.table_for(fid >> 24)
        if src_name is None:
            bad.append((sname, pname, ptype, f'{fid:08X}',
                        f'<index {fid >> 24:02X} out of range>', '<unresolvable>', None))
            continue
        if table is None:
            unresolved[src_name] += 1
            continue
        checked += 1
        problem = _binding_problem(table.get(fid & 0xFFFFFF), accepts)
        if problem:
            bad.append((sname, pname, ptype, f'{fid:08X}', src_name, *problem))
    _print_cross_master(checked, bad, unresolved, limit, verbose)


def _print_cross_master(checked, bad, unresolved, limit, verbose):
    """The cross-master report: counts, unbuilt masters, then each bad property."""
    print()
    print(f'cross-master: object properties resolved: {checked}')
    print(f'cross-master: mis-routed / unbindable:    {len(bad)}')
    if unresolved:
        print()
        print('masters not built, skipped:')
        for name, n in unresolved.most_common():
            print(f'  {name:<40} {n:>6} properties')
    if bad:
        print()
        print(f'{"script":<44} {"property":<28} {"declared":<14} '
              f'{"formid":<9} {"resolves in":<28} {"actual"}')
        print('-' * 145)
        for sname, pname, ptype, fid, src_name, sig, edid in bad[:limit]:
            act = sig if edid is None else f'{sig} {edid}'
            print(f'{sname:<44} {pname:<28} {ptype:<14} {fid:<9} '
                  f'{src_name:<28} {act}')
        if len(bad) > limit and not verbose:
            print(f'... {len(bad) - limit} more (use --max/-v)')


def _report_unbound(plugin, src, limit, verbose):
    """Declared-but-UNBOUND object properties -- the other way a property Nones.

    A property the .psc declares but no VMAD binds reads None for the whole
    session, and the first use aborts the entire Papyrus function. That is a
    different defect from a type mismatch (which this tool's main pass finds)
    with an identical runtime symptom, and it is invisible to the type pass
    because there is nothing to compare types against.

    This is how the CharacterGen Emperor broke: `Player` is in no registry, so
    when the QUST VMAD builder moved from merging the whole well-known registry
    to a per-declared-name lookup, `Player` stopped being bound in 18 QF_
    scripts. `UrielSeptimRef.SetLookAt(Player)` then aborted Charactergen stage
    12 before it unlocked CGEmperor01-24.

    Names a TES4 script legitimately references but that exist in NO record
    (TG02Taxes, SE11A -- dead names in Oblivion's own sources) are unbound
    correctly, so a residue here is expected; the signal is a name that names a
    real record, or an engine-hardcoded one like Player/GameHour.
    """
    esm = os.path.join(ROOT, 'output', plugin, plugin)
    bound = _read_vmad_bindings(esm)
    decl = re.compile(r'^\s*([A-Za-z_]\w*)\s+Property\s+(\w+)\s+Auto', re.M)
    per_name = Counter()
    per_script = defaultdict(list)
    for fn in sorted(os.listdir(src)):
        if not fn.endswith('.psc'):
            continue
        sname = fn[:-4].lower()
        if sname not in bound:
            continue          # script attached to nothing -- not this check
        text = open(os.path.join(src, fn), encoding='utf-8',
                    errors='replace').read()
        for m in decl.finditer(text):
            ptype, pname = m.group(1), m.group(2)
            if ptype.lower() in ('int', 'float', 'bool', 'string'):
                continue
            if pname not in bound[sname]:
                per_name[pname] += 1
                per_script[fn].append((ptype, pname))
    print()
    print(f'scripts with >=1 unbound declared property: {len(per_script)}')
    print(f'unbound declared object properties: {sum(per_name.values())}')
    if per_name:
        print()
        print(f'{"property":<32} {"scripts":>7}')
        print('-' * 41)
        for name, n in per_name.most_common(limit):
            print(f'{name:<32} {n:>7}')
    if verbose:
        for fn, items in sorted(per_script.items())[:limit]:
            print()
            print(f'== {fn} ==')
            for ptype, pname in items:
                print(f'  {ptype} {pname}')


def main():
    ap = argparse.ArgumentParser(
        description='Statically predict VMAD property binding failures')
    ap.add_argument('--plugin', default='Oblivion.esm')
    ap.add_argument('--verbose', '-v', action='store_true',
                    help='list offending properties, not just counts')
    ap.add_argument('--unbound', action='store_true',
                    help='also report properties the .psc DECLARES but no '
                         'VMAD binds (they read None and abort the function)')
    ap.add_argument('--cross-master', action='store_true',
                    help="resolve each bound property's real FormID through "
                         'this plugin MAST list, catching an index byte '
                         'copied verbatim from a master')
    ap.add_argument('--max', type=int, default=25)
    args = ap.parse_args()

    from tools.dialog.dialog_emulator import read_tes5_file
    path = os.path.join(ROOT, 'output', args.plugin, args.plugin)
    hdr, recs, _loc = read_tes5_file(path)

    rec_type, interior, ambiguous = {}, {}, {}
    for r in recs:
        ed = [s for s in r.subrecords if s.type == 'EDID']
        if not ed:
            continue
        n = ed[0].data.split(b'\x00')[0].decode('latin-1').lower()
        if n in rec_type and rec_type[n] != r.type:
            ambiguous[n] = True
        rec_type.setdefault(n, r.type)
        # A leading-digit EditorID is emitted with the digits stripped, so it
        # competes for the stripped name too (DIAL `1DagothSUr` -> DagothSUr).
        stripped = n.lstrip('0123456789')
        if stripped and stripped != n:
            if stripped in rec_type and rec_type[stripped] != r.type:
                ambiguous[stripped] = True
            rec_type.setdefault(stripped, r.type)
        if r.type == 'CELL':
            fl = None
            for s in r.subrecords:
                if s.type == 'DATA':
                    fl = s.data[0]
            if fl is not None:
                interior[n] = bool(fl & 1)

    src = os.path.join(ROOT, 'output', args.plugin, 'scripts', 'source')
    decl = re.compile(r'^([A-Za-z_]\w*)\s+Property\s+(\w+)', re.M)
    bad = Counter()
    detail = defaultdict(list)
    checked = 0
    for fn in sorted(os.listdir(src)):
        if not fn.endswith('.psc'):
            continue
        text = open(os.path.join(src, fn), encoding='utf-8',
                    errors='replace').read()
        for m in decl.finditer(text):
            ptype, pname = m.group(1), m.group(2)
            if ptype in _PERMISSIVE or ptype.startswith('TES4_'):
                continue
            accepts = _ACCEPTS.get(ptype)
            if accepts is None:
                continue
            # `Player` is bound to PlayerRef (0x14), a REFR, never to the base
            # NPC_ record that shares the EditorID -- so the base record's
            # signature says nothing about whether it binds.
            if pname.lower() in ('player', 'playerref'):
                continue
            # Resolving the property NAME to a record is a guess: TES4
            # EditorIDs are not unique across types, and the binder may have
            # picked a different one (the DIAL `1DagothSUr` sanitizes to
            # `DagothSUr`, which also names a CELL). Only trust the name when
            # exactly one record answers to it.
            if ambiguous.get(pname.lower()):
                continue
            actual = rec_type.get(pname.lower())
            if actual is None:
                continue
            checked += 1
            if actual not in accepts:
                bad[(ptype, actual)] += 1
                detail[(ptype, actual)].append((fn, pname))
            elif ptype == 'Cell' and interior.get(pname.lower()) is False:
                bad[('Cell', 'CELL(exterior)')] += 1
                detail[('Cell', 'CELL(exterior)')].append((fn, pname))

    print(f'plugin: {args.plugin}')
    print(f'resolvable object properties checked: {checked}')
    print(f'predicted binding failures: {sum(bad.values())}')
    if bad:
        print()
        print(f'{"declared":<20} {"actual":<16} {"count":>7}')
        print('-' * 46)
        for (p, a), n in bad.most_common(args.max):
            print(f'{p:<20} {a:<16} {n:>7}')
    if args.verbose:
        for (p, a), n in bad.most_common(args.max):
            print()
            print(f'== {p} <- {a} ({n}) ==')
            for fn, pname in detail[(p, a)][:args.max]:
                print(f'  {fn}: {pname}')

    if args.cross_master:
        _report_cross_master(args.plugin, src, args.max, args.verbose)

    if args.unbound:
        _report_unbound(args.plugin, src, args.max, args.verbose)


if __name__ == '__main__':
    main()

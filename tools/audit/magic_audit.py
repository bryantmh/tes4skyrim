"""Audit TES4->TES5 magic conversion coverage.

Reports, for an export directory:
  * every MGEF the source defines, whether the converter maps it, and to what
  * which MGEF assets (model / effect shader / sounds / lights) are discarded
  * per-effect-record (SPEL/ENCH/ALCH/INGR/SGST/LVSP) fallout: how many effects
    are dropped, how many records lose ALL effects, how many go to filler
  * the AssocItem loss (summons/bound weapons whose target creature/item the
    flat code table cannot express)

Usage:
    python tools/audit/magic_audit.py export/Oblivion.esm
    python tools/audit/magic_audit.py export/Oblivion.esm --by-record
    python tools/audit/magic_audit.py export/Nehrim.esm --unmapped-only
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Nehrim's strings are German; a cp1252 console cannot encode every character.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from tes5_import.base.text_reader import parse_export_file
from tes5_import.base.equivalents import (
    MGEF_AV_CODE_TO_SKYRIM,
    MGEF_CODE_TO_SKYRIM,
)
from tes5_import.record_types.magic import (
    ARCHETYPE_ASSOC_KIND,
    EFFECT_ARCHETYPES,
    get_archetype,
)
from tes5_import.record_types.magic_morrowind import MW_EFFECT_ARCHETYPES
from tes5_import.generated.vanilla_mgef_data import VANILLA_MGEF_DATA

EFFECT_RECORD_TYPES = ('SPEL', 'ENCH', 'ALCH', 'INGR', 'SGST', 'LVSP')

# MGEF DATA.Flags bits that carry behaviour the flat table cannot preserve.
MGEF_FLAGS = {
    0x00000001: 'Hostile',
    0x00000002: 'Recover',
    0x00000004: 'Detrimental',
    0x00000008: 'MagnitudePercent',
    0x00000010: 'Self',
    0x00000020: 'Touch',
    0x00000040: 'Target',
    0x00000080: 'NoDuration',
    0x00000100: 'NoMagnitude',
    0x00000200: 'NoArea',
    0x00000400: 'FXPersist',
    0x00000800: 'Spellmaking',
    0x00001000: 'Enchanting',
    0x00002000: 'NoIngredient',
    0x00010000: 'UseWeapon',
    0x00020000: 'UseArmor',
    0x00040000: 'UseCreature',
    0x00080000: 'UseSkill',
    0x00100000: 'UseAttribute',
    0x01000000: 'UseActorValue',
    0x02000000: 'SprayProjectile',
    0x04000000: 'BoltProjectile',
    0x08000000: 'NoHitEffect',
    0x10000000: 'PersistOnDeath',
    0x20000000: 'Unknown29',
    0x40000000: 'FogProjectile',
}

SCHOOLS = {0: 'Alteration', 1: 'Conjuration', 2: 'Destruction',
           3: 'Illusion', 4: 'Mysticism', 5: 'Restoration'}

# TES5 MGEF archetypes (xEdit wbDefinitionsTES5.pas wbMGEFType) — only the ones
# this converter emits are named; the rest print as their number.
ARCHETYPE_NAMES = {
    0: 'ValueModifier', 1: 'Script', 2: 'Dispel', 3: 'CureDisease',
    4: 'Absorb', 5: 'DualValueModifier', 6: 'Calm', 7: 'Demoralize',
    8: 'Frenzy', 10: 'CommandSummoned', 11: 'Invisibility', 12: 'Light',
    15: 'Lock', 16: 'Open', 17: 'BoundWeapon', 18: 'SummonCreature',
    19: 'DetectLife', 20: 'Telekinesis', 21: 'Paralysis', 22: 'Reanimate',
    23: 'SoulTrap', 24: 'TurnUndead', 27: 'CureParalysis', 29: 'CurePoison',
    34: 'PeakValueModifier', 35: 'Cloak', 38: 'Rally',
}


def _load(export_dir, sig):
    path = os.path.join(export_dir, f'{sig}.txt')
    if not os.path.exists(path):
        return []
    return parse_export_file(path)


def _get(rec, key, default=''):
    return rec.get(key, default)


def _fid(rec, key):
    v = rec.get(key, '')
    try:
        return int(v, 16)
    except (ValueError, TypeError):
        return 0


def resolve(code, av=-1, index=-1):
    """Non-zero when the converter has an MGEF for this effect.

    Since MGEF became a converted record type the plugin emits its OWN magic
    effect for every code its export defines, so coverage is "the archetype
    table names this code" — the flat vanilla-alias table only still answers
    for a plugin whose MGEFs were never exported.  (It used to be the only
    path, which is why 71 of 145 Oblivion effects were dropped.)
    """
    if index >= 0:
        return 1 if index in MW_EFFECT_ARCHETYPES else 0
    if code in EFFECT_ARCHETYPES:
        return 1
    per_av = MGEF_AV_CODE_TO_SKYRIM.get(code)
    if per_av is not None:
        fid = per_av.get(av)
        if fid:
            return fid
    return MGEF_CODE_TO_SKYRIM.get(code, 0)


def _all_export_codes():
    """Every effect code ANY export defines — the real test for a dead key."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'export')
    codes = set()
    if not os.path.isdir(root):
        return codes
    # Every RECORD dir under export/, not every top-level folder: an imported
    # mod's group folder holds its plugins' records one level down.
    for name in os.listdir(root):
        d = os.path.join(root, name)   # noqa: plugin-path (listdir entry, not a plugin name)
        if not os.path.isdir(d):
            continue
        subs = [os.path.join(d, q) for q in os.listdir(d)
                if os.path.isfile(os.path.join(d, q, 'MGEF.txt'))]
        for rec_dir in ([d] + subs):
            codes |= {_get(r, 'EditorID') for r in _load(rec_dir, 'MGEF')}
    codes.discard('')
    return codes


def phantom_keys(export_dir):
    """Table keys matching no MGEF in the export — coverage that cannot fire.

    17 of the old alias table's 100 entries were codes no Oblivion or Nehrim
    record uses (BACT, SMFL, TNUN...), so the summons looked mapped while the
    real codes fell through to 0.
    """
    src = {_get(r, 'EditorID') for r in _load(export_dir, 'MGEF')}
    if not src:
        return set(), set()
    tables = set(EFFECT_ARCHETYPES) | set(MGEF_CODE_TO_SKYRIM) \
        | set(MGEF_AV_CODE_TO_SKYRIM)
    return tables - src, src - set(EFFECT_ARCHETYPES)


def audit_mgef(export_dir):
    """Per-MGEF coverage + discarded-asset census."""
    mgefs = _load(export_dir, 'MGEF')
    rows = []
    for rec in mgefs:
        code = _get(rec, 'EditorID')
        mw_index = int(_get(rec, 'MorrowindEffectIndex', '-1') or -1)
        flags = int(_get(rec, 'DATA.Flags', '0') or 0)
        rows.append({
            'code': code,
            'name': _get(rec, 'FULL'),
            'fid': _get(rec, 'FormID'),
            'school': SCHOOLS.get(int(_get(rec, 'DATA.School', '0') or 0), '?'),
            'flags': flags,
            'flag_names': [n for b, n in MGEF_FLAGS.items() if flags & b],
            'assoc': _fid(rec, 'DATA.AssocItem'),
            'model': _get(rec, 'Model.MODL'),
            'icon': _get(rec, 'ICON'),
            'shader': _fid(rec, 'DATA.EffectShader'),
            'ench_shader': _fid(rec, 'DATA.EnchantEffect'),
            'light': _fid(rec, 'DATA.Light'),
            'proj_speed': float(_get(rec, 'DATA.ProjectileSpeed', '0') or 0),
            'cast_snd': _fid(rec, 'DATA.CastingSound'),
            'bolt_snd': _fid(rec, 'DATA.BoltSound'),
            'hit_snd': _fid(rec, 'DATA.HitSound'),
            'area_snd': _fid(rec, 'DATA.AreaSound'),
            'counters': int(_get(rec, 'CounterEffects', '0') or 0),
            'base_cost': float(_get(rec, 'DATA.BaseCost', '0') or 0),
            'mapped': resolve(code, index=mw_index),
            'per_av': code in MGEF_AV_CODE_TO_SKYRIM,
            'archetype': (MW_EFFECT_ARCHETYPES[mw_index][0]
                          if mw_index in MW_EFFECT_ARCHETYPES
                          else get_archetype(code)
                          if code in EFFECT_ARCHETYPES else None),
            # An AssocItem the chosen archetype does not read is DISCARDED:
            # wbMGEFAssocItemDecider only reads it for Light/Bound/Summon/
            # Guide/Cloak/Werewolf/EnhanceWeapon/Hazard/PeakMod.
            'assoc_kept': bool(
                _fid(rec, 'DATA.AssocItem')
                and get_archetype(code) in ARCHETYPE_ASSOC_KIND
                and (code in EFFECT_ARCHETYPES
                     or mw_index in MW_EFFECT_ARCHETYPES)),
        })
    return rows


def audit_effect_records(export_dir):
    """Per-record effect fallout across all effect-bearing record types."""
    out = {}
    for sig in EFFECT_RECORD_TYPES:
        recs = _load(export_dir, sig)
        if not recs:
            continue
        stats = {'total': len(recs), 'effects': 0, 'dropped': 0,
                 'all_dropped': 0, 'partial': 0, 'clean': 0,
                 'dropped_codes': Counter(), 'examples': defaultdict(list)}
        for rec in recs:
            n = int(_get(rec, 'EffectCount', '0') or 0)
            if not n:
                continue
            kept = drop = 0
            for i in range(n):
                code = _get(rec, f'Effect[{i}].EFID')
                if not code:
                    continue
                av = int(_get(rec, f'Effect[{i}].ActorValue', '-1') or -1)
                mw = int(_get(rec, f'Effect[{i}].MorrowindIndex', '-1') or -1)
                stats['effects'] += 1
                if resolve(code, av, mw):
                    kept += 1
                else:
                    drop += 1
                    stats['dropped'] += 1
                    stats['dropped_codes'][code] += 1
                    if len(stats['examples'][code]) < 3:
                        stats['examples'][code].append(
                            _get(rec, 'EditorID') or _get(rec, 'FormID'))
            if drop and not kept:
                stats['all_dropped'] += 1
            elif drop:
                stats['partial'] += 1
            else:
                stats['clean'] += 1
        out[sig] = stats
    return out


def _vanilla_name(form_id) -> str:
    """The vanilla MGEF's editor id, or its FormID when it has no entry."""
    row = VANILLA_MGEF_DATA.get(form_id)
    return row[0] if row else hex(form_id)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('export_dir', help='e.g. export/Oblivion.esm')
    ap.add_argument('--by-record', action='store_true',
                    help='list every MGEF with its mapping')
    ap.add_argument('--unmapped-only', action='store_true',
                    help='only list MGEFs that resolve to nothing')
    ap.add_argument('--assets', action='store_true',
                    help='detail the per-MGEF assets that conversion discards')
    args = ap.parse_args()

    rows = audit_mgef(args.export_dir)
    if not rows:
        print(f'No MGEF.txt in {args.export_dir}')
        return 1

    mapped = [r for r in rows if r['mapped'] or r['per_av']]
    unmapped = [r for r in rows if not (r['mapped'] or r['per_av'])]
    own = [r for r in rows if r['archetype'] is not None]

    print(f'=== MGEF coverage: {args.export_dir} ===')
    print(f'  source MGEF records          : {len(rows)}')
    print(f'  converted as our OWN MGEF    : {len(own)}')
    print(f'  falls back to a vanilla alias: {len(mapped) - len(own)}')
    print(f'  unmapped (effect DROPPED)    : {len(unmapped)}')
    print(f'  vanilla DATA blobs available : {len(VANILLA_MGEF_DATA)}')

    # A key no plugin uses is dead coverage.  Judge that across ALL exports,
    # not this one: the table is shared, and Nehrim authors 16 effect codes
    # (BA01-BA10, BW09/BW10, DISE, DUMY, RSWD, Z020) that Oblivion never uses.
    phantom, no_archetype = phantom_keys(args.export_dir)
    global_phantom = phantom - _all_export_codes()
    print(f'  keys unused by THIS plugin   : {len(phantom)}')
    print(f'  PHANTOM keys (no export uses them): {len(global_phantom)}'
          + (f'  {" ".join(sorted(global_phantom))}' if global_phantom else ''))
    if no_archetype:
        print(f'  codes with NO archetype entry: {len(no_archetype)}'
              f'  {" ".join(sorted(no_archetype))}')

    # Per-archetype breakdown: what the source effects actually became.
    by_arch = Counter(r['archetype'] for r in own)
    print('\n=== archetypes emitted ===')
    for arch, n in sorted(by_arch.items()):
        codes = sorted(r['code'] for r in own if r['archetype'] == arch)
        print(f'  {arch:2d} {ARCHETYPE_NAMES.get(arch, "?"):22s} {n:3d}  '
              f'{" ".join(codes[:10])}{" ..." if len(codes) > 10 else ""}')

    # Source data and whether it now has a destination.
    with_model = [r for r in rows if r['model']]
    with_shader = [r for r in rows if r['shader'] or r['ench_shader']]
    with_snd = [r for r in rows if r['cast_snd'] or r['bolt_snd']
                or r['hit_snd'] or r['area_snd']]
    with_assoc = [r for r in rows if r['assoc']]
    kept_assoc = [r for r in rows if r['assoc_kept']]
    with_light = [r for r in rows if r['light']]
    with_counters = [r for r in rows if r['counters']]
    print('\n=== per-MGEF source data ===')
    print(f'  Model.MODL (cast art)        : {len(with_model):3d}  '
          f'DROPPED (needs the ARTO writer, Phase 2)')
    print(f'  EffectShader/EnchantEffect   : {len(with_shader):3d}  '
          f'converted (EFSH -> Hit/Enchant Shader)')
    print(f'  sounds (cast/bolt/hit/area)  : {len(with_snd):3d}  '
          f'DROPPED (needs SNDD, Phase 2)')
    print(f'  AssocItem (summon/bound tgt) : {len(with_assoc):3d}  '
          f'{len(kept_assoc)} kept (the rest name an archetype that ignores it)')
    print(f'  Light                        : {len(with_light):3d}  converted')
    print(f'  counter effects (ESCE)       : {len(with_counters):3d}  converted')

    if unmapped:
        print(f'\n=== unmapped MGEFs ({len(unmapped)}) — effects silently dropped ===')
        for r in sorted(unmapped, key=lambda r: r['code']):
            assoc = f" assoc={r['assoc']:08X}" if r['assoc'] else ''
            print(f"  {r['code']:6s} {r['name'][:38]:38s} {r['school']:12s}{assoc}")

    if args.assets:
        print('\n=== per-MGEF discarded assets ===')
        for r in sorted(rows, key=lambda r: r['code']):
            bits = []
            if r['model']:
                bits.append(f"model={r['model']}")
            if r['shader']:
                bits.append(f"shader={r['shader']:08X}")
            if r['ench_shader']:
                bits.append(f"ench={r['ench_shader']:08X}")
            if r['light']:
                bits.append(f"light={r['light']:08X}")
            if r['assoc']:
                bits.append(f"assoc={r['assoc']:08X}")
            if r['counters']:
                bits.append(f"counters={r['counters']}")
            if bits:
                print(f"  {r['code']:6s} {r['name'][:30]:30s} {' '.join(bits)}")

    if args.by_record or args.unmapped_only:
        print('\n=== per-MGEF mapping ===')
        for r in sorted(rows, key=lambda r: (r['school'], r['code'])):
            if args.unmapped_only and (r['mapped'] or r['per_av']):
                continue
            tgt = ('per-ActorValue' if r['per_av']
                   else _vanilla_name(r['mapped'])
                   if r['mapped'] else '*** DROPPED ***')
            print(f"  {r['school']:12s} {r['code']:6s} {r['name'][:34]:34s} -> {tgt}")

    print('\n=== effect-bearing records ===')
    per = audit_effect_records(args.export_dir)
    tot_all_dropped = 0
    for sig, s in per.items():
        pct = 100.0 * s['dropped'] / s['effects'] if s['effects'] else 0
        tot_all_dropped += s['all_dropped']
        print(f"  {sig}: {s['total']:5d} records, {s['effects']:5d} effects, "
              f"{s['dropped']:5d} dropped ({pct:4.1f}%), "
              f"{s['all_dropped']:4d} lost ALL effects -> filler, "
              f"{s['partial']:4d} partial, {s['clean']:5d} clean")
    print(f'  TOTAL records reduced to filler effects: {tot_all_dropped}')

    drops = Counter()
    ex = defaultdict(list)
    for s in per.values():
        drops.update(s['dropped_codes'])
        for c, names in s['examples'].items():
            ex[c].extend(names)
    if drops:
        print('\n=== most-dropped effect codes (by use count) ===')
        by_code = {r['code']: r for r in rows}
        for code, n in drops.most_common(25):
            name = by_code.get(code, {}).get('name', '?')
            print(f'  {code:6s} {name[:34]:34s} {n:5d} uses   '
                  f'e.g. {", ".join(ex[code][:3])}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

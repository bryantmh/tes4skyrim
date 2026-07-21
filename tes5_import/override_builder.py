"""Build an override record: the master's converted bytes + authored changes.

The model is xEdit's "copy as override": the record that goes into the plugin
IS the master's converted record. Only the fields the plugin's AUTHOR changed
are then substituted, and authorship comes from diffing the two TES4 exports
(see export_diff) — never from comparing two conversion runs.

Everything the plugin does not explicitly change is therefore byte-identical to
the master. That is the property that makes this robust: a field we never touch
cannot drift, so there is no class of "our pass re-derived it differently" bug
to guard against with heuristics.

Authored changes are applied three ways, in order of preference:

1. String substitution (_STRING_SUBRECORD & friends) — translated text copied
   straight into the corresponding subrecord.
2. Subrecord rebuild (_REBUILDERS) — the changed field feeds a TES5 subrecord
   the converter derives (XCLL, ACBS, DNAM, BOD2, ...). The SAME builder
   function the converter uses is re-run against the PLUGIN's export record
   and the whole subrecord is substituted. This cannot drift: fields the
   author didn't touch are identical in both exports, so the rebuilt bytes
   match the master's for everything but the authored change.
3. Explicitly inexpressible (_INEXPRESSIBLE) — the converter provably drops
   the TES4 field (TES5 has no counterpart), so the change is a no-op and is
   counted as applied rather than reported as noise.

An export key none of those cover is reported by the caller rather than
guessed at. The master's value stays, and the run prints a summary, so a
missing mapping is visible instead of silent.
"""

import struct

from .text_reader import get_formid, get_int

_HEADER_SIZE = 24
_COMPRESSED_FLAG = 0x00040000

# Export key -> the output subrecord it writes, for keys whose value is a
# null-terminated string copied straight through. These cover the overwhelming
# majority of real override content in a translation.
_STRING_SUBRECORD = {
    'FULL': b'FULL',
    'DESC': b'DESC',
    'MapMarker.FULL': b'FULL',
}

# Indexed export lists whose Nth entry writes the Nth occurrence of a
# subrecord in the output record. INFO responses are the big one: a translated
# INFO repeats `TRDT NAM1 NAM2 NAM3` per response, and the translation lives in
# NAM1. Substituting per occurrence keeps the master's response structure
# (emotion, notes, ordering) and changes only the spoken line.
_INDEXED_STRING_SUBRECORD = {
    'Response[]': ('Response', 'ResponseText', b'NAM1'),
}

# Nested indexed lists: `Stage[i].Log[j].Text`. A quest's journal entries are
# the record's CNAM run, flattened in stage-then-log order — the same order the
# converter emits them, so the Nth flattened entry is the Nth CNAM.
_NESTED_STRING_SUBRECORD = {
    'Stage[]': ('Stage', 'Log', 'Text', b'CNAM'),
}

# Keys that are genuinely not representable in the output record and are
# deliberately ignored rather than reported as unmapped. These describe the
# TES4 script system, which the converter re-implements as Papyrus VMAD from
# the plugin's own SCPT records — the override body has nowhere to put them.
_IGNORED_CHANGES = frozenset({
    'SCTX',                 # TES4 script source; becomes Papyrus separately
    'SCHR.CompiledSize',    # TES4 bytecode bookkeeping
    'SCHR.RefCount',
    'SCHR.VariableCount',
    'SCHR.DataSize',
    'SCHR.Type',
    'SCDA',
    'SCRO[]',               # script reference list, follows SCTX
    'SLSD[]',
    'SCVR[]',
    'ResultScript',         # INFO result script; re-emitted as a VMAD fragment
    'ParentDIAL',           # grouping metadata, not a field on the record
    'ParentCELL',
    'ParentWRLD',
    'RecordFlags',          # the master's flags are authoritative for an override
})

# (sig, key) — or ('*', key) for any signature — whose TES4 field the
# converter PROVABLY DROPS: TES5 has no counterpart and no derived subrecord
# reads it. The authored change is a no-op on the converted record, so it is
# counted as applied instead of polluting the unmapped report. Each entry
# names why.
_INEXPRESSIBLE = frozenset({
    ('*', 'Model.MODB'),          # TES4 bound radius; TES5 has no MODB
    ('*', 'ZNAM.CombatStyle'),    # CSTY is a skipped type; ref would dangle
    ('*', 'LNAM.HairLength'),     # TES5 NPC_ has no hair length field
    ('*', 'ACBS.Fatigue'),        # TES5 derives stamina; converter drops it
    ('*', 'ACBS.SpellPoints'),    # TES5 derives magicka; converter drops it
    ('CREA', 'RNAM.AttackReach'), # attack reach is race-level in Skyrim
    ('CREA', 'NIFZ[]'),           # creature geometry is converted asset-side
    ('CELL', 'XCMT.MusicType'),   # convert_CELL emits no music subrecord
    ('SPEL', 'SPIT.Level'),       # TES5 SPIT has no spell level
    # TES4 attributes with no TES5 field and no derived subrecord: _npc_acbs
    # reads Endurance/Intelligence/Strength, _npc_skills_dnam reads
    # Intelligence/Strength — the rest are dropped.
    ('NPC_', 'DATA.Luck'),
    ('NPC_', 'DATA.Willpower'),
    ('NPC_', 'DATA.Speed'),
    ('NPC_', 'DATA.Agility'),
    ('NPC_', 'DATA.Personality'),
})

# TES4 NPC_ skill fields — all feed the DNAM rebuild (_npc_skills_dnam).
_NPC_SKILL_KEYS = tuple(
    f'DATA.{name}' for name in (
        'Armorer', 'Athletics', 'Blade', 'Block', 'Blunt', 'HandToHand',
        'HeavyArmor', 'Alchemy', 'Alteration', 'Conjuration', 'Destruction',
        'Illusion', 'Mysticism', 'Restoration', 'Acrobatics', 'LightArmor',
        'Marksman', 'Mercantile', 'Security', 'Sneak', 'Speechcraft'))


def split_subrecords(record: bytes) -> list:
    """[(signature, payload), ...] in file order; [] if unparseable."""
    if len(record) < _HEADER_SIZE:
        return []
    if struct.unpack_from('<I', record, 8)[0] & _COMPRESSED_FLAG:
        return []
    out = []
    off = _HEADER_SIZE
    end = len(record)
    while off + 6 <= end:
        sig = record[off:off + 4]
        size = struct.unpack_from('<H', record, off + 4)[0]
        payload = record[off + 6:off + 6 + size]
        if len(payload) != size:
            return []
        out.append((sig, payload))
        off += 6 + size
    return out


def join_subrecords(header: bytes, subs: list) -> bytes:
    """Rebuild a record from its 24-byte header and subrecord list."""
    body = b''.join(sig + struct.pack('<H', len(p)) + p for sig, p in subs)
    return (header[:4] + struct.pack('<I', len(body))
            + header[8:_HEADER_SIZE] + body)


def _encode_string(value: str) -> bytes:
    return (value or '').encode('utf-8') + b'\x00'


# --------------------------------------------------------------------------
# Subrecord rebuilders
#
# Each spec regenerates ONE output subrecord from the PLUGIN's export record
# using the converter's own builder function, then substitutes it wholesale.
# `anchors` places the subrecord when the master's record lacks it: a list of
# ('before'|'after', sig) tried in order, falling back to after-EDID.
# --------------------------------------------------------------------------

def _build_npc_dnam(rec):
    from .record_types.actors import _npc_skills_dnam
    return _npc_skills_dnam(rec)


def _build_npc_acbs(rec):
    from .record_types.actors import _npc_acbs
    return _npc_acbs(rec)


def _build_crea_acbs(rec):
    from .record_types.actors import _crea_acbs
    return _crea_acbs(rec)


def _build_bod2(rec):
    from .record_types.equipment import build_armo_bod2
    return build_armo_bod2(rec, is_clothing=rec.get('Signature') == 'CLOT')


def _build_xcll(rec):
    from .record_types.world import build_cell_xcll
    return build_cell_xcll(rec)


def _build_xclw(rec):
    from .record_types.world import build_cell_xclw
    return build_cell_xclw(rec)


def _build_mnam(rec):
    from .record_types.world import build_wrld_mnam
    return build_wrld_mnam(rec)


def _build_xown(rec):
    owner = get_formid(rec, 'XOWN.Owner')
    if not owner:
        return None
    return struct.pack('<I', owner)


# Sentinel a builder returns to leave the master's subrecord untouched AND
# have the change reported as unmapped (used when the plugin-side state needed
# to regenerate the subrecord isn't available).
KEEP = object()


def _build_scri_vmad(rec):
    """VMAD for an authored SCRI (attached script) change.

    The object-script plan (object_scripts.py, built in phase 0b from the
    PLUGIN's own export) already computed the VMAD for every scripted record,
    including overrides — so an authored script swap is just that plan's
    output for this record. If the plan has nothing but the plugin still
    declares a script, the script didn't convert; keep the master's VMAD and
    report, rather than silently detaching.
    """
    from .object_scripts import get_object_vmad
    vmad = get_object_vmad(get_formid(rec, 'FormID'))
    if vmad:
        return vmad[6:]                    # payload of the packed subrecord
    if not get_formid(rec, 'SCRI'):
        return None                        # author detached the script
    return KEEP


def _build_fltv(rec):
    from .text_reader import get_float
    return struct.pack('<f', get_float(rec, 'FLTV.Value'))


class _Rebuild:
    def __init__(self, sig: bytes, builder, anchors: tuple = ()):
        self.sig = sig
        self.builder = builder
        self.anchors = anchors


_RB_NPC_DNAM = _Rebuild(b'DNAM', _build_npc_dnam)
_RB_NPC_ACBS = _Rebuild(b'ACBS', _build_npc_acbs)
_RB_CREA_ACBS = _Rebuild(b'ACBS', _build_crea_acbs)
_RB_BOD2 = _Rebuild(b'BOD2', _build_bod2)
_RB_XCLL = _Rebuild(b'XCLL', _build_xcll, (('before', b'LTMP'),))
_RB_XCLW = _Rebuild(b'XCLW', _build_xclw,
                    (('after', b'XOWN'), ('after', b'LTMP')))
_RB_XOWN = _Rebuild(b'XOWN', _build_xown, (('after', b'LTMP'),))
_RB_REFR_XOWN = _Rebuild(b'XOWN', _build_xown, (('after', b'XESP'),))
_RB_MNAM = _Rebuild(b'MNAM', _build_mnam, (('after', b'DNAM'),))
_RB_FLTV = _Rebuild(b'FLTV', _build_fltv, (('after', b'FNAM'),))
_RB_SCRI_VMAD = _Rebuild(b'VMAD', _build_scri_vmad)

_XCLL_KEYS = tuple(
    f'XCLL.{f}' for f in (
        'AmbientR', 'AmbientG', 'AmbientB', 'DirectionalR', 'DirectionalG',
        'DirectionalB', 'FogR', 'FogG', 'FogB', 'FogNear', 'FogFar',
        'DirectionalRotXY', 'DirectionalRotZ', 'DirectionalFade',
        'FogClipDist'))

# (sig, export_key) -> [_Rebuild, ...]. One authored key can feed several
# output subrecords (DATA.Intelligence lands in both DNAM and ACBS).
_REBUILDERS = {}


def _reg(sig, keys, *rebuilds):
    for key in keys if isinstance(keys, (tuple, list)) else (keys,):
        _REBUILDERS.setdefault((sig, key), []).extend(rebuilds)


_reg('NPC_', _NPC_SKILL_KEYS, _RB_NPC_DNAM)
_reg('NPC_', ('DATA.Health', 'DATA.Intelligence', 'DATA.Strength'),
     _RB_NPC_DNAM, _RB_NPC_ACBS)
_reg('NPC_', ('DATA.Endurance', 'ACBS.Flags', 'ACBS.Level', 'ACBS.CalcMin',
              'ACBS.CalcMax'), _RB_NPC_ACBS)
_reg('CREA', ('ACBS.Flags', 'ACBS.Level', 'ACBS.CalcMin', 'ACBS.CalcMax'),
     _RB_CREA_ACBS)
_reg('ARMO', ('BMDT.GeneralFlags', 'BMDT.BipedFlags'), _RB_BOD2)
_reg('CLOT', ('BMDT.GeneralFlags', 'BMDT.BipedFlags'), _RB_BOD2)
_reg('CELL', _XCLL_KEYS, _RB_XCLL)
_reg('CELL', 'XCLW.WaterHeight', _RB_XCLW)
_reg('CELL', 'XOWN.Owner', _RB_XOWN)
_reg('REFR', 'XOWN.Owner', _RB_REFR_XOWN)
_reg('ACHR', 'XOWN.Owner', _RB_REFR_XOWN)
_reg('ACRE', 'XOWN.Owner', _RB_REFR_XOWN)
_reg('WRLD', ('MNAM.UsableDimX', 'MNAM.UsableDimY', 'MNAM.NWCellX',
              'MNAM.NWCellY', 'MNAM.SECellX', 'MNAM.SECellY'), _RB_MNAM)
_reg('GLOB', 'FLTV.Value', _RB_FLTV)
# Every type whose converter attaches object scripts via get_object_vmad
# (record_types/common._common_header_subs + NPC_/CREA/STAT paths).
for _scripted in ('ACTI', 'ALCH', 'APPA', 'ARMO', 'BOOK', 'CLOT', 'CONT',
                  'CREA', 'DOOR', 'FLOR', 'FURN', 'INGR', 'KEYM', 'LIGH',
                  'MISC', 'NPC_', 'SGST', 'SLGM', 'STAT', 'WEAP'):
    _reg(_scripted, 'SCRI', _RB_SCRI_VMAD)

# Authored changes to a spell's EFFECT LIST cannot be spliced into the
# master's converted bytes: effect conversion may synthesize aimed-MGEF clone
# companions, which an override must not silently inherit or re-mint. The
# author rewrote the record's magic payload, so the record is RECONVERTED
# from the plugin's export through the normal path instead (its FormID still
# lands on the master's, so it stays an override). The handful of clone
# companions a reconversion mints are ordinary new records in the plugin.
RECONVERT_KEYS = frozenset({
    ('SPEL', 'Effect[]'),
    ('SPEL', 'ScriptEffect[]'),
    ('ENCH', 'Effect[]'),
    ('ENCH', 'ScriptEffect[]'),
})


# --------------------------------------------------------------------------
# In-place patchers: change specific bytes of an EXISTING subrecord, for
# fields whose siblings in the same subrecord the converter derives from
# state this run doesn't have (placement shifts, vendor gold, ...).
# (sig, key) -> (out_sig, patcher(old_payload, plugin_rec) -> payload)
# --------------------------------------------------------------------------

def _patch_float_at(offset, export_key):
    def patch(old, rec):
        from .text_reader import get_float
        if len(old) < offset + 4:
            return old
        buf = bytearray(old)
        struct.pack_into('<f', buf, offset, get_float(rec, export_key))
        return bytes(buf)
    return patch


def _patch_spit_cost(old, rec):
    if len(old) < 4:
        return old
    buf = bytearray(old)
    struct.pack_into('<I', buf, 0, get_int(rec, 'SPIT.Cost'))
    return bytes(buf)


# REFR/ACHR DATA: 6 floats (pos xyz, rot xyz). Only the CHANGED coordinate is
# patched so the master's furniture-origin Z compensation survives on the
# untouched axes. (A changed Z on a marker-bearing model would lose the shift;
# none of Nehrim's overrides hits that case.)
_PLACEMENT_PATCHERS = {
    'PosX': ('DATA', _patch_float_at(0, 'PosX')),
    'PosY': ('DATA', _patch_float_at(4, 'PosY')),
    'PosZ': ('DATA', _patch_float_at(8, 'PosZ')),
    'RotX': ('DATA', _patch_float_at(12, 'RotX')),
    'RotY': ('DATA', _patch_float_at(16, 'RotY')),
    'RotZ': ('DATA', _patch_float_at(20, 'RotZ')),
}

_PATCHERS = {}
for _sig in ('REFR', 'ACHR', 'ACRE'):
    for _key, (_out, _fn) in _PLACEMENT_PATCHERS.items():
        _PATCHERS[(_sig, _key)] = (_out.encode(), _fn)
_PATCHERS[('SPEL', 'SPIT.Cost')] = (b'SPIT', _patch_spit_cost)


# --------------------------------------------------------------------------
# Run rebuilders: replace a whole FAMILY of repeated subrecords (inventory
# CNTO run, spell SPLO run, package PKID run) plus its count subrecord.
#
# The preserve-extras rule: entries in the master's converted run that do NOT
# derive from the master's own export list were ADDED by the converter
# (vendor gold, filtered quest packages' complement, ...). They are kept, and
# only the author-controlled part is regenerated from the plugin's list.
# --------------------------------------------------------------------------

def _read_export_items(rec):
    from .record_types.actors import _read_items
    return _read_items(rec)


def _rebuild_inventory(plugin_rec, master_rec, old_subs):
    """New COCT+CNTO run for an authored Item[] change."""
    sig = plugin_rec.get('Signature')
    if sig in ('NPC_', 'CREA'):
        # Actors split wearables into the OTFT companion; only the carried
        # part lives in CNTO. The outfit companion is the master's and is
        # never re-minted, so an authored change to a WORN item cannot be
        # expressed — the carried part still applies.
        from .outfits import split_inventory
        _, carried = split_inventory(_read_export_items(plugin_rec))
        _, m_carried = split_inventory(_read_export_items(master_rec))
    else:
        carried = _read_export_items(plugin_rec)
        m_carried = _read_export_items(master_rec)

    expected = {fid for fid, _ in m_carried}
    extras = [payload for s, payload in old_subs
              if s == b'CNTO'
              and struct.unpack_from('<I', payload)[0] not in expected]

    entries = [struct.pack('<Ii', fid, count) for fid, count in carried]
    entries += extras
    if not entries:
        return []
    return ([(b'COCT', struct.pack('<I', len(entries)))]
            + [(b'CNTO', p) for p in entries])


def _rebuild_spells(plugin_rec, master_rec, old_subs):
    """New SPCT+SPLO run for an authored Spell[] change."""
    def export_fids(rec):
        return [f for f in (get_formid(rec, f'Spell[{i}]')
                            for i in range(get_int(rec, 'SpellCount')))
                if f]

    expected = set(export_fids(master_rec))
    extras = [payload for s, payload in old_subs
              if s == b'SPLO'
              and struct.unpack_from('<I', payload)[0] not in expected]
    entries = [struct.pack('<I', f) for f in export_fids(plugin_rec)] + extras
    if not entries:
        return []
    return ([(b'SPCT', struct.pack('<I', len(entries)))]
            + [(b'SPLO', p) for p in entries])


def _rebuild_packages(plugin_rec, master_rec, old_subs):
    """New PKID run for an authored AIPackage[] change.

    The converter filters quest packages out of PKID (they reach the actor
    through a QUST alias). That filter's state isn't rebuilt in a plugin run,
    so it is DERIVED: whatever the master's export listed but its converted
    PKID run omitted was filtered, and stays filtered here.
    """
    def export_fids(rec):
        return [f for f in (get_formid(rec, f'AIPackage[{i}]')
                            for i in range(get_int(rec, 'AIPackageCount')))
                if f]

    old_pkids = [struct.unpack_from('<I', payload)[0]
                 for s, payload in old_subs if s == b'PKID']
    excluded = set(export_fids(master_rec)) - set(old_pkids)
    new = [f for f in export_fids(plugin_rec) if f not in excluded]
    new += [f for f in old_pkids if f not in set(export_fids(master_rec))]
    return [(b'PKID', struct.pack('<I', f)) for f in new]


def _rebuild_barter_gold(plugin_rec, master_rec, old_subs):
    """Patch the vendor-gold CNTO for an authored ACBS.BarterGold change.

    TES5 has no barter-gold field; the converter turns it into carried Gold001
    (see convert_NPC_). Whether the actor IS a vendor was decided by the
    master's run (vendor factions), so only an EXISTING gold entry is patched
    — the rest of the run passes through unchanged.
    """
    from .record_types.actors import GOLD001_FID
    gold = get_int(plugin_rec, 'ACBS.BarterGold')
    out = []
    for s, payload in old_subs:
        if (s == b'CNTO' and gold > 0
                and struct.unpack_from('<I', payload)[0] == GOLD001_FID):
            payload = struct.pack('<Ii', GOLD001_FID, gold)
        out.append((s, payload))
    return out


class _RunRebuild:
    def __init__(self, family: tuple, builder, anchors: tuple):
        self.family = family        # sigs replaced as a unit
        self.builder = builder
        self.anchors = anchors


_RUN_INVENTORY = _RunRebuild((b'COCT', b'CNTO'), _rebuild_inventory,
                             (('before', b'AIDT'), ('after', b'QNAM'),
                              ('after', b'SNAM'), ('after', b'DATA')))
_RUN_SPELLS = _RunRebuild((b'SPCT', b'SPLO'), _rebuild_spells,
                          (('after', b'RNAM'),))
_RUN_PACKAGES = _RunRebuild((b'PKID',), _rebuild_packages,
                            (('after', b'AIDT'),))

_RUN_BARTER_GOLD = _RunRebuild((b'COCT', b'CNTO'), _rebuild_barter_gold, ())

_RUN_REBUILDERS = {
    ('NPC_', 'Item[]'): _RUN_INVENTORY,
    ('CREA', 'Item[]'): _RUN_INVENTORY,
    ('CONT', 'Item[]'): _RUN_INVENTORY,
    ('NPC_', 'Spell[]'): _RUN_SPELLS,
    ('CREA', 'Spell[]'): _RUN_SPELLS,
    ('NPC_', 'AIPackage[]'): _RUN_PACKAGES,
    ('CREA', 'AIPackage[]'): _RUN_PACKAGES,
    ('NPC_', 'ACBS.BarterGold'): _RUN_BARTER_GOLD,
    ('CREA', 'ACBS.BarterGold'): _RUN_BARTER_GOLD,
}


def _insert_at_anchor(out: list, anchors: tuple, items: list):
    """Insert subrecords at the first matching anchor, else after EDID."""
    for mode, sig in anchors:
        for i, (s, _p) in enumerate(out):
            if s == sig:
                pos = i if mode == 'before' else i + 1
                out[pos:pos] = items
                return
    pos = 1 if out and out[0][0] == b'EDID' else 0
    out[pos:pos] = items


def apply_changes(master_record: bytes, changes: dict,
                  plugin_export: dict = None,
                  master_export: dict = None) -> tuple:
    """Master's converted record with the author's changes substituted in.

    `changes` is export_diff.diff_records() output: {export_key: plugin_value}.
    `plugin_export` is the plugin's raw export record, needed whenever a
    subrecord is regenerated (the diff only reports THAT a field changed).
    `master_export` is the master's raw export record, needed by run
    rebuilders to tell converter-added entries from author-controlled ones.

    Returns (record_bytes, applied_keys, unmapped_keys). A key this module
    cannot express leaves the master's value untouched and is returned in
    `unmapped_keys` — never approximated.
    """
    applied = set()
    unmapped = set()
    plugin_export = plugin_export or {}
    master_export = master_export or {}
    sig_name = plugin_export.get('Signature', '')

    pending = {}
    indexed = {}
    rebuilds = []      # unique _Rebuild specs to run
    rebuild_keys = {}  # spec -> originating export keys (for KEEP reporting)
    patchers = []      # (out_sig, fn, key)
    runs = []          # unique _RunRebuild specs
    for key, value in changes.items():
        if key in _IGNORED_CHANGES:
            applied.add(key)
            continue
        if ((sig_name, key) in _INEXPRESSIBLE
                or ('*', key) in _INEXPRESSIBLE):
            applied.add(key)
            continue
        specs = _REBUILDERS.get((sig_name, key))
        if specs is not None:
            for spec in specs:
                if spec not in rebuilds:
                    rebuilds.append(spec)
                rebuild_keys.setdefault(spec, []).append(key)
            applied.add(key)
            continue
        patch = _PATCHERS.get((sig_name, key))
        if patch is not None:
            patchers.append((patch[0], patch[1], key))
            continue
        run = _RUN_REBUILDERS.get((sig_name, key))
        if run is not None:
            if run not in runs:
                runs.append(run)
            applied.add(key)
            continue
        nested = _NESTED_STRING_SUBRECORD.get(key)
        if nested is not None:
            outer, inner, field, sub_sig = nested
            values = []
            i = 0
            while any(k.startswith(f'{outer}[{i}].') for k in plugin_export):
                j = 0
                while f'{outer}[{i}].{inner}[{j}].{field}' in plugin_export:
                    values.append(
                        plugin_export[f'{outer}[{i}].{inner}[{j}].{field}'])
                    j += 1
                i += 1
            if values:
                indexed[sub_sig] = values
                applied.add(key)
            else:
                unmapped.add(key)
            continue
        spec = _INDEXED_STRING_SUBRECORD.get(key)
        if spec is not None:
            name, field, sub_sig = spec
            values = []
            i = 0
            while f'{name}[{i}].{field}' in plugin_export:
                values.append(plugin_export[f'{name}[{i}].{field}'])
                i += 1
            if values:
                indexed[sub_sig] = values
                applied.add(key)
            else:
                unmapped.add(key)
            continue
        sub_sig = _STRING_SUBRECORD.get(key)
        if sub_sig is None:
            unmapped.add(key)
            continue
        pending[sub_sig] = _encode_string(value)

    if not (pending or indexed or rebuilds or patchers or runs):
        return master_record, applied, unmapped

    subs = split_subrecords(master_record)
    if not subs:
        # Compressed or malformed: never rewrite blind.
        return (master_record, applied,
                unmapped | set(changes) - applied)

    out = []
    replaced = set()
    seen = {}
    for sub_sig, payload in subs:
        if sub_sig in indexed:
            n = seen.get(sub_sig, 0)
            seen[sub_sig] = n + 1
            values = indexed[sub_sig]
            # Only substitute positions the plugin actually has. Extra
            # occurrences in the master keep their value, so the record's
            # response structure is never truncated.
            if n < len(values):
                out.append((sub_sig, _encode_string(values[n])))
            else:
                out.append((sub_sig, payload))
            replaced.add(sub_sig)
        elif sub_sig in pending and sub_sig not in replaced:
            out.append((sub_sig, pending[sub_sig]))
            replaced.add(sub_sig)
        else:
            out.append((sub_sig, payload))

    # A string the master's record does not carry at all (an unnamed record the
    # plugin names). Insert after EDID, which every record leads with, so the
    # field lands in a valid position rather than after the trailing fields.
    for sub_sig, payload in pending.items():
        if sub_sig in replaced:
            continue
        pos = 1 if out and out[0][0] == b'EDID' else 0
        out.insert(pos, (sub_sig, payload))
        replaced.add(sub_sig)

    # Subrecord rebuilds: regenerate from the plugin's record with the
    # converter's own builder; replace in place, insert at the spec's anchor,
    # or remove when the plugin's record no longer produces the subrecord.
    for spec in rebuilds:
        payload = spec.builder(plugin_export)
        if payload is KEEP:
            # The plugin-side state to regenerate this subrecord isn't
            # available; keep the master's bytes and surface the keys.
            for key in rebuild_keys.get(spec, ()):
                applied.discard(key)
                unmapped.add(key)
            continue
        idx = next((i for i, (s, _p) in enumerate(out) if s == spec.sig), None)
        if payload is None:
            if idx is not None:
                del out[idx]
        elif idx is not None:
            out[idx] = (spec.sig, payload)
        else:
            _insert_at_anchor(out, spec.anchors, [(spec.sig, payload)])

    # In-place patchers.
    for out_sig, fn, key in patchers:
        idx = next((i for i, (s, _p) in enumerate(out) if s == out_sig), None)
        if idx is None:
            unmapped.add(key)
            continue
        out[idx] = (out_sig, fn(out[idx][1], plugin_export))
        applied.add(key)

    # Run rebuilds: replace the whole family with the regenerated run.
    for run in runs:
        old_run = [(s, p) for s, p in out if s in run.family]
        new_run = run.builder(plugin_export, master_export, old_run)
        idx = next((i for i, (s, _p) in enumerate(out)
                    if s in run.family), None)
        out = [(s, p) for s, p in out if s not in run.family]
        if new_run:
            if idx is not None:
                out[idx:idx] = new_run
            else:
                _insert_at_anchor(out, run.anchors, new_run)

    return join_subrecords(master_record[:_HEADER_SIZE], out), applied, unmapped

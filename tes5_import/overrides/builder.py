"""Build an override record: the master's converted bytes + authored changes.

The model is xEdit's "copy as override": the record that goes into the plugin
IS the master's converted record. Only the fields the plugin's AUTHOR changed
are then substituted, and authorship comes from diffing the two TES4 exports
(see export_diff) — never from comparing two conversion runs.

Everything the plugin does not explicitly change is therefore byte-identical to
the master. That is the property that makes this robust: a field we never touch
cannot drift, so there is no class of "our pass re-derived it differently" bug
to guard against with heuristics.

Authored changes are applied by running the record's OWN converter over the
plugin's export and substituting the subrecords that differ from the same
converter's output for the master (generic_substitutions). That is the general
case and it is exact — the bytes are what a full conversion of the plugin's
record would have produced, because it is that conversion.

Four narrower mechanisms take precedence where they apply:

1. String substitution (_STRING_SUBRECORD & friends) — translated text copied
   straight into the corresponding subrecord.
2. Subrecord rebuild (_REBUILDERS) and in-place patchers (_PATCHERS) — for
   fields whose output subrecord depends on state a PLUGIN run does not have:
   alias indices, vendor factions, the object-script plan. Re-converting the
   whole record there would discard what only the master's run knew, so just
   the authored field is rebuilt or patched.
3. Run rebuilds (_RUN_REBUILDERS) — a whole family of repeated subrecords
   (inventory, spells, packages, quest objectives) regenerated as a unit.
4. Explicitly inexpressible (_INEXPRESSIBLE) — TES5 has no counterpart for
   the TES4 field, verified against the xEdit record definition, so the change
   is genuinely a no-op rather than something we failed to carry.

A record DELETED by the author is not a field change at all and is handled
before any of this — see overrides.make_deleted_record.

An export key none of these can express is reported by the caller rather than
guessed at, and the run prints a summary, so a gap is visible instead of
silent.
"""

import struct

from ..base.text_reader import get_float, get_formid, get_int
from ..actors.outfits import split_inventory
from .vmad_swap import SCRIPT_SWAP_KEY, SCRIPTED_TYPES, swap_vmad_script

from ..base.writer import RECORD_HEADER_SIZE
_COMPRESSED_FLAG = 0x00040000

# Export key -> the output subrecord it writes, for keys whose value is a
# null-terminated string copied straight through. These cover the overwhelming
# majority of real override content in a translation.
_STRING_SUBRECORD = {
    'FULL': b'FULL',
    'DESC': b'DESC',
    'MapMarker.FULL': b'FULL',
}

# Which record types may have a _STRING_SUBRECORD field INSERTED when the
# master's record does not already carry one. Derived from the xEdit TES5
# record definitions (wbDefinitionsTES5.pas: the types whose wbRecord block
# lists wbFULL / wbDESC) — 41 types take FULL, 8 take DESC, and LAND takes
# NEITHER. Substituting into a field the master already has is always fine;
# only INVENTING one needs this gate.
_FULL_TYPES = frozenset(
    b'ACTI ALCH AMMO APPA ARMO AVIF BOOK CELL CLFM CONT DIAL DOOR ENCH EXPL '
    b'FACT FURN HAZD HDPT INGR LCTN LIGH MESG MGEF MISC MSTT NPC_ PERK PROJ '
    b'QUST RACE SCRL SHOU SLGM SNCT SPEL TACT TREE WATR WEAP WOOP WRLD'.split())
_DESC_TYPES = frozenset(
    b'ALCH AMMO APPA ARMO BOOK SCRL SHOU WEAP'.split())

_INSERTABLE_SUBRECORDS = {}
for _sig in _FULL_TYPES:
    _INSERTABLE_SUBRECORDS.setdefault(_sig, set()).add(b'FULL')
for _sig in _DESC_TYPES:
    _INSERTABLE_SUBRECORDS.setdefault(_sig, set()).add(b'DESC')
_INSERTABLE_SUBRECORDS = {k: frozenset(v)
                          for k, v in _INSERTABLE_SUBRECORDS.items()}

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

# Indexed subrecord runs whose values are DERIVED from the plugin's export
# rather than read straight out of a field, keyed by the export key that
# changed. `Stage[].Log[].Text` feeds TWO output runs: the stage log entries
# (CNAM, handled above) and the quest OBJECTIVES (NNAM) — and Skyrim's journal
# displays the objective, not the log entry. Mapping the key to CNAM alone left
# every objective holding the master's text, so a translation plugin's quests
# still read in the master's language in-game: 83 of Translation.esp's 84
# quests had English CNAM and German NNAM.
#
# The deriving function is convert_QUST's own, so the objective sequence here
# cannot drift from the one the master's record was built with.
_DERIVED_INDEXED_SUBRECORD = {
    ('QUST', 'Stage[]'): (b'NNAM', 'quest_objective_texts'),
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
    ('SPEL', 'SPIT.Level'),       # TES5 SPIT has no spell level
    ('NPC_', 'DATA.Luck'),
    ('NPC_', 'DATA.Willpower'),
    ('NPC_', 'DATA.Speed'),
    ('NPC_', 'DATA.Agility'),
    ('NPC_', 'DATA.Personality'),
    # A TES4 DIAL's QSTI list is a DERIVED back-reference — the set of quests
    # owning an INFO under this topic — which the CS recomputes per file. TES5
    # has no counterpart: its DIAL carries a single QNAM quest owner, and
    # dialog_converter keys topic ownership off each INFO's OWN QSTI.Quest
    # (never the DIAL's list). So a plugin adding its own quest's INFOs to
    # GREETING rewrites this list without authoring anything the output can
    # hold; the INFOs it added carry the real change.
    ('DIAL', 'Quest[]'),
    # TES4 chains a topic's responses with PNAM ('Previous INFO'). TES5's INFO
    # has NO such field (wbDefinitionsTES5 INFO: EDID VMAD DATA ENAM ... — no
    # PNAM anywhere); Skyrim orders responses by their physical position in the
    # topic's GRUP instead, which the override path already preserves by
    # keeping the master's nesting. So re-pointing the chain is a no-op.
    ('INFO', 'PNAM.PrevInfo'),
})

#: TES4 NPC_ skill fields; all feed the DNAM rebuild (npc_skills_dnam).
_NPC_SKILL_KEYS = tuple(
    f'DATA.{name}' for name in (
        'Armorer', 'Athletics', 'Blade', 'Block', 'Blunt', 'HandToHand',
        'HeavyArmor', 'Alchemy', 'Alteration', 'Conjuration', 'Destruction',
        'Illusion', 'Mysticism', 'Restoration', 'Acrobatics', 'LightArmor',
        'Marksman', 'Mercantile', 'Security', 'Sneak', 'Speechcraft'))


def split_subrecords(record: bytes) -> list:
    """[(signature, payload), ...] in file order; [] if unparseable."""
    if len(record) < RECORD_HEADER_SIZE:
        return []
    if struct.unpack_from('<I', record, 8)[0] & _COMPRESSED_FLAG:
        return []
    out = []
    off = RECORD_HEADER_SIZE
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
            + header[8:RECORD_HEADER_SIZE] + body)


def _encode_string(value: str) -> bytes:
    return (value or '').encode('cp1252', errors='replace') + b'\x00'


# --------------------------------------------------------------------------
# Subrecord rebuilders
#
# Each spec regenerates ONE output subrecord from the PLUGIN's export record
# using the converter's own builder function, then substitutes it wholesale.
# `anchors` places the subrecord when the master's record lacks it: a list of
# ('before'|'after', sig) tried in order, falling back to after-EDID.
# --------------------------------------------------------------------------

def _build_npc_dnam(rec):
    from ..record_types.npc import npc_skills_dnam
    return npc_skills_dnam(rec)


def _build_npc_acbs(rec):
    from ..record_types.npc import npc_acbs
    return npc_acbs(rec)


def _build_crea_acbs(rec):
    from ..record_types.creature import crea_acbs
    return crea_acbs(rec)


def _build_crea_nam6(rec):
    from ..record_types.creature import crea_nam6
    return crea_nam6(rec)


def _build_bod2(rec):
    from ..record_types.equipment import build_armo_bod2
    return build_armo_bod2(rec, is_clothing=rec.get('Signature') == 'CLOT')


def _build_xcll(rec):
    from ..record_types.world import build_cell_xcll
    return build_cell_xcll(rec)


def _build_xclw(rec):
    from ..record_types.world import build_cell_xclw
    return build_cell_xclw(rec)


def _build_mnam(rec):
    from ..record_types.world import build_wrld_mnam
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
    from ..base.object_scripts import get_object_vmad
    vmad = get_object_vmad(get_formid(rec, 'FormID'))
    if vmad:
        return vmad[6:]                    # payload of the packed subrecord
    if not get_formid(rec, 'SCRI'):
        return None                        # author detached the script
    return KEEP


def _build_fltv(rec):
    from ..base.text_reader import get_float
    return struct.pack('<f', get_float(rec, 'FLTV.Value'))


def _build_land_hex(key):
    """A LAND vertex-data subrecord: the export's raw hex, copied through.

    VNML (normals), VHGT (heights) and VCLR (colors) have IDENTICAL layout in
    TES4 and TES5, so convert_LAND copies the hex blob straight across. That
    makes the authored value directly substitutable — no re-derivation, so no
    drift. Terrain edits are the whole point of a castle mod regrading the
    ground it stands on: DLCBattlehornCastle authors VHGT on all 16 of its LAND
    overrides, and dropping them left the castle on the master's terrain.
    """
    def build(rec):
        from ..base.text_reader import get_str
        hex_str = get_str(rec, key)
        if not hex_str:
            # The author cleared the subrecord (rare). Removing it is a real
            # authored state, distinct from "we could not express it".
            return None
        try:
            return bytes.fromhex(hex_str)
        except ValueError:
            return KEEP
    return build


class _Rebuild:
    def __init__(self, sig: bytes, builder, anchors: tuple = (),
                 keep_tail: int = 0):
        self.sig = sig
        self.builder = builder
        self.anchors = anchors
        # Trailing bytes of the MASTER's subrecord to preserve verbatim, for
        # payloads that end in uninitialised CS memory (LAND VHGT's
        # wbUnused(3)). The diff already ignores those bytes, so rewriting them
        # would make the override differ from the master for no authored
        # reason. Only applied when both sides are long enough.
        self.keep_tail = keep_tail


_RB_NPC_DNAM = _Rebuild(b'DNAM', _build_npc_dnam)
_RB_NPC_ACBS = _Rebuild(b'ACBS', _build_npc_acbs)
_RB_CREA_ACBS = _Rebuild(b'ACBS', _build_crea_acbs)
# CREA base scale -> NPC_ height. NAM6 sits between NAM5 and NAM7 in the
# TES5 NPC_ order; anchor off NAM5 so an override that ADDS the subrecord
# puts it in the right place.
_RB_CREA_NAM6 = _Rebuild(b'NAM6', _build_crea_nam6, (('after', b'NAM5'),))
_RB_BOD2 = _Rebuild(b'BOD2', _build_bod2)
_RB_XCLL = _Rebuild(b'XCLL', _build_xcll, (('before', b'LTMP'),))
_RB_XCLW = _Rebuild(b'XCLW', _build_xclw,
                    (('after', b'XOWN'), ('after', b'LTMP')))
_RB_XOWN = _Rebuild(b'XOWN', _build_xown, (('after', b'LTMP'),))
_RB_REFR_XOWN = _Rebuild(b'XOWN', _build_xown, (('after', b'XESP'),))
_RB_MNAM = _Rebuild(b'MNAM', _build_mnam, (('after', b'DNAM'),))
# WRLD world-object bounds. A plugin that ADDS land outside the master's
# worldspace must widen these or the engine clips everything beyond them: the
# world map is rendered over exactly this rectangle, so terrain outside it is
# simply not drawn. Tamriel.esp triples Cyrodiil's extent
# (-262144 -> -786432) and none of it showed on the map because the override
# kept the master's rectangle. Raw world units, same scale in TES4 and TES5 —
# written exactly as convert_WRLD writes them.
_RB_NAM0 = _Rebuild(b'NAM0', lambda rec: struct.pack(
    '<ff', get_float(rec, 'NAM0.MinX'), get_float(rec, 'NAM0.MinY')),
    (('after', b'NAMA'), ('after', b'ONAM'), ('after', b'MNAM')))
_RB_NAM9 = _Rebuild(b'NAM9', lambda rec: struct.pack(
    '<ff', get_float(rec, 'NAM9.MaxX'), get_float(rec, 'NAM9.MaxY')),
    (('after', b'NAM0'), ('after', b'NAMA'), ('after', b'ONAM')))


def _build_wrld_modl(rec):
    """WRLD MODL — the world-map cloud bank, scaled to this worldspace.

    A plugin that widens a worldspace's NAM0/NAM9 rectangle changes the size
    the cloud bank has to cover, so the bank is regenerated alongside the
    bounds (Tamriel.esp triples Cyrodiil's extent).  Same generator the
    from-scratch path uses, so both produce byte-identical meshes.

    Returns KEEP when the bank cannot be generated, leaving whatever the master
    had rather than writing a dangling model reference.
    """
    from ..record_types.world import build_wrld_cloud_modl
    rel = build_wrld_cloud_modl(rec)
    if rel is None:
        return KEEP
    return rel.encode('utf-8') + b'\x00'


# Anchored before the map data, matching convert_WRLD and xEdit's field order.
_RB_WRLD_MODL = _Rebuild(b'MODL', _build_wrld_modl,
                         (('before', b'MNAM'), ('before', b'ONAM'),
                          ('before', b'NAMA')))
_RB_FLTV = _Rebuild(b'FLTV', _build_fltv, (('after', b'FNAM'),))
_RB_SCRI_VMAD = _Rebuild(b'VMAD', _build_scri_vmad)
# LAND vertex data, in the order convert_LAND emits it: DATA VNML VHGT VCLR,
# then the BTXT/ATXT/VTXT layer run. Each anchors after the subrecord that
# precedes it so a subrecord the master lacks is INSERTED in the right place
# rather than appended past the layer run.
_RB_LAND_VNML = _Rebuild(b'VNML', _build_land_hex('VNML'),
                         (('after', b'DATA'),))
_RB_LAND_VHGT = _Rebuild(b'VHGT', _build_land_hex('VHGT'),
                         (('after', b'VNML'), ('after', b'DATA')),
                         keep_tail=3)
_RB_LAND_VCLR = _Rebuild(b'VCLR', _build_land_hex('VCLR'),
                         (('after', b'VHGT'), ('after', b'VNML'),
                          ('after', b'DATA')))
_RB_LAND_DATA = _Rebuild(
    b'DATA', lambda rec: struct.pack('<I', get_int(rec, 'DATA.Flags')))

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
_reg('CREA', 'BNAM.BaseScale', _RB_CREA_NAM6)
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
# MNAM (the map camera's pan rectangle) rides along with the bounds change: a
# plugin that adds land routinely leaves its authored MNAM untouched, so
# keying the rebuild on an MNAM field having CHANGED would never fire on the
# case that needs it. Widening NAM0/NAM9 is the signal that land was added.
_reg('WRLD', ('NAM0.MinX', 'NAM0.MinY'), _RB_NAM0, _RB_WRLD_MODL, _RB_MNAM)
_reg('WRLD', ('NAM9.MaxX', 'NAM9.MaxY'), _RB_NAM9, _RB_WRLD_MODL, _RB_MNAM)
_reg('GLOB', 'FLTV.Value', _RB_FLTV)
_reg('LAND', 'VNML', _RB_LAND_VNML)
_reg('LAND', 'VHGT', _RB_LAND_VHGT)
_reg('LAND', 'VCLR', _RB_LAND_VCLR)
_reg('LAND', 'DATA.Flags', _RB_LAND_DATA)
for _scripted in SCRIPTED_TYPES:
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
    *((sig, f'{gender}.BipedModel.MODL') for sig in ('ARMO', 'CLOT')
      for gender in ('Male', 'Female')),
})


# --------------------------------------------------------------------------
# The PRIMARY path: convert BOTH exports and substitute what differs.
#
# This is how an authored change SHOULD be applied. Run the record's OWN
# converter — the very function that produced the master's bytes — over the
# master's export and over the plugin's, diff the two results
# subrecord-by-subrecord, and substitute exactly the ones that differ. The
# result is what a full conversion of the plugin's record would have produced
# for that field, because it IS that conversion.
#
# It is exact, not approximate, for the same reason the whole override model
# is: both sides run the SAME converter in the SAME process, so a field the
# author did not touch produces identical bytes and is never substituted. Only
# genuinely authored differences survive the diff. It also self-maintains — a
# converter that starts emitting a new subrecord is covered automatically,
# with no registry entry to forget.
#
# The hand-written specs above are the EXCEPTIONS, and they take precedence
# only where this cannot run: a converter needing state a plugin pass does not
# have (alias indices, vendor factions, the object-script plan). Those specs
# are second implementations of a converter's logic and can drift from it;
# this cannot.
# --------------------------------------------------------------------------

# Signatures whose converter takes extra run-state (a writer to mint companion
# records, an alias/package context). Calling them with that state absent would
# either crash or silently emit a record shorn of its companions, so they are
# excluded from the generic path and rely on the explicit specs above.
_NO_GENERIC_CONVERT = frozenset({
    'SOUN',   # attenuation/volume live in the companion SNDR, needs writer
    'PACK',   # PTDA/PLDT alias indices come from PackContext
    'QUST',   # alias indices + VMAD bindings are allocated per run
    'DIAL',   # topic keying is derived across the whole INFO set
    'INFO',
    'CELL',   # emitted as pre-built GRUP bytes, not via the dispatch table
    'WRLD',
    'LAND',
})


def _converted_subrecords(rec: dict):
    """{sig: [payloads]} from running this record's own converter, or None."""
    from ..registry import IMPORT_DISPATCH
    sig = rec.get('Signature')
    fn = IMPORT_DISPATCH.get(sig)
    if fn is None:
        return None
    try:
        out = fn(rec)
    except Exception:
        # A converter that cannot run on this record tells us nothing; the
        # caller falls back to reporting the key as unmapped.
        return None
    if isinstance(out, tuple):
        out = out[0]
    if not isinstance(out, (bytes, bytearray)):
        return None
    subs = split_subrecords(bytes(out))
    if not subs:
        return None
    grouped = {}
    for s, payload in subs:
        grouped.setdefault(s, []).append(payload)
    return grouped


# SOUN's volume/falloff data does NOT live on the SOUN record — it lives on
# the companion SNDR (BNAM static attenuation) and the SOPM that SNDR's ONAM
# points at (min/max distance). An override that reuses the master's SOUN
# therefore has to override the master's SNDR too, or an authored attenuation
# change silently keeps the master's loudness.
_SOUN_COMPANION_KEYS = frozenset({
    'SNDX.StaticAttenuation', 'SNDD.StaticAttenuation',
    'SNDX.MinAttDist', 'SNDD.MinAttDist',
    'SNDX.MaxAttDist', 'SNDD.MaxAttDist',
    'SNDX.FreqAdj', 'SNDD.FreqAdj', 'SNDX.Flags', 'SNDD.Flags',
})


def soun_companion_changes(changes: dict) -> bool:
    """True when a SOUN's authored change belongs on its SNDR companion."""
    return any(k in _SOUN_COMPANION_KEYS for k in changes)


def rebuild_sndr_override(master_sndr: bytes, plugin_rec: dict,
                          writer) -> bytes:
    """The master's SNDR with this plugin's authored sound values applied.

    convert_SOUN is re-run on the PLUGIN's export to get correctly-derived
    BNAM/LNAM/ONAM bytes, and only those are substituted into the master's
    SNDR — the record keeps the master's FormID and EDID so everything already
    pointing at it still resolves. ONAM is included because the falloff
    distances live on the SOPM it references; re-running the converter mints
    (or reuses, via the writer's SOPM cache) a model for the authored
    distances the same way a fresh conversion would.
    """
    from ..record_types.sound import convert_SOUN
    _soun, sndr_bytes, _fid = convert_SOUN(plugin_rec, writer)
    if not sndr_bytes:
        return b''
    fresh = {sig: payload for sig, payload in split_subrecords(sndr_bytes)}
    out = []
    for sig, payload in split_subrecords(master_sndr):
        if sig in (b'BNAM', b'LNAM', b'ONAM') and sig in fresh:
            payload = fresh[sig]
        out.append((sig, payload))
    return join_subrecords(master_sndr[:RECORD_HEADER_SIZE], out)


def generic_substitutions(plugin_rec: dict, master_rec: dict):
    """Subrecords that differ between the two exports' own conversions.

    Returns {sig: [payloads]} to substitute wholesale (the full run of that
    signature, so repeated subrecords stay consistent), or None when the
    record's converter cannot be run standalone.
    """
    if plugin_rec.get('Signature') in _NO_GENERIC_CONVERT:
        return None
    p_subs = _converted_subrecords(plugin_rec)
    if p_subs is None:
        return None
    m_subs = _converted_subrecords(master_rec)
    if m_subs is None:
        return None
    # sorted: this dict's order can reach the emitted subrecord order, and
    # set order over strings varies per process (see diff_records).
    out = {sig: p_subs.get(sig, [])
           for sig in sorted(set(p_subs) | set(m_subs))
           if p_subs.get(sig) != m_subs.get(sig)}
    # A REFR that places a LEVELLED CREATURE becomes an ACHR aimed at a SHELL
    # NPC_ the MASTER's run minted (see leveled_actors) — an index a plugin run
    # does not have, so its standalone conversion resolves NAME to the raw
    # LVLN instead. Substituting that ships an ACHR whose base is not an actor;
    # the engine loads it as a Character*, dereferences a null base and CRASHES
    # on startup (TWMP 0301A56B -> ANQ's LVLN 0306B333, crash log
    # "EXCEPTION_ACCESS_VIOLATION ... mov eax, [rax+0x108]" in
    # BGSLoadFormBuffer). The master's NAME already points at the right shell,
    # so keep it.
    if out.get(b'NAME') and _places_leveled_actor(plugin_rec):
        out.pop(b'NAME', None)
    return out


def _places_leveled_actor(rec: dict) -> bool:
    """True when this REFR's base object is a TES4 leveled creature (LVLC)."""
    from ..actors.leveled_actors import is_leveled_creature_base
    return is_leveled_creature_base(rec)


# --------------------------------------------------------------------------
# In-place patchers: change specific bytes of an EXISTING subrecord, for
# fields whose siblings in the same subrecord the converter derives from
# state this run doesn't have (placement shifts, vendor gold, ...).
# (sig, key) -> (out_sig, patcher(old_payload, plugin_rec) -> payload)
# --------------------------------------------------------------------------

def _patch_float_at(offset, export_key):
    def patch(old, rec):
        from ..base.text_reader import get_float
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


def _patch_pkdt_flags(old, rec):
    """Authored PKDT.Flags, through the converter's own flag mapping.

    Only the flag u32 and the preferred-speed byte are rewritten. The rest of
    the master's PKDT (package type, interrupt override/flags) is left alone
    because those come from run state a plugin conversion does not have —
    convert_PACK picks them from PackContext (is this package quest-gated?
    what signature is its target?) and from the force-greet/activate special
    cases. Re-deriving the whole subrecord here would silently downgrade a
    force-greet's 0xFEFF interrupts to the 0x0000 default.
    """
    from ..packages.converter import convert_flags, T5_PREFERRED_SPEED
    if len(old) < 8:
        return old
    buf = bytearray(old)
    # Preserve the master's quest-gating decision: it cleared Once Per Day iff
    # the package is quest-gated, which is state this run cannot recompute.
    old_flags = struct.unpack_from('<I', old, 0)[0]
    flags, speed = convert_flags(get_int(rec, 'PKDT.Flags'),
                                 get_int(rec, 'PKDT.Type', -1))
    from ..packages.converter import T5_ONCE_PER_DAY
    if not (old_flags & T5_ONCE_PER_DAY):
        flags &= ~T5_ONCE_PER_DAY
    # The master's speed opt-in wins when the author did not change the
    # always-run bit, so a force-greet keeps its vanilla run speed.
    if not (flags & T5_PREFERRED_SPEED):
        speed = old[6]
        flags |= old_flags & T5_PREFERRED_SPEED
    struct.pack_into('<I', buf, 0, flags)
    buf[6] = speed
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
_PATCHERS[('PACK', 'PKDT.Flags')] = (b'PKDT', _patch_pkdt_flags)
for _sig in SCRIPTED_TYPES + ('INFO', 'REFR', 'ACHR', 'ACRE'):
    _PATCHERS[(_sig, SCRIPT_SWAP_KEY)] = (b'VMAD', swap_vmad_script)


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
    from ..record_types.actor_common import read_items
    return read_items(rec)


def _rebuild_inventory(plugin_rec, master_rec, old_subs):
    """New COCT+CNTO run for an authored Item[] change.

    Actors split wearables into the OTFT companion, so only the carried
    part lives in CNTO; a change to what an actor WEARS overrides the
    master's OTFT instead (`OverrideContext.build_outfit_companion`).
    """
    sig = plugin_rec.get('Signature')
    if sig in ('NPC_', 'CREA'):
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
    through a QUST alias). Two things must be filtered here:

    1. What the MASTER's conversion already filtered, derived by comparing the
       master's export against its converted PKID run.
    2. **Packages THIS PLUGIN newly quest-owns.** These cannot be derived from
       the master -- the master never saw them -- so the live filter set
       (`packages._QUEST_PACKAGES`, populated in phase 0g before any override
       is built) is consulted directly.

    Missing (2) is what shipped Knights.esp's `PACK 02002D7C`
    (`NDAnvilListenProphetGogan8x4`, owned by the plugin's own quest ND00) in
    the PKID list of `NPC_ 0103AF03` (Gogan). xEdit states the rule outright:
    "package is owned by quest ND00 and cannot be assigned to an npc record".
    The engine wedges at the main menu resolving it.
    """
    from ..packages.actor_wiring import is_quest_package

    def export_fids(rec):
        return [f for f in (get_formid(rec, f'AIPackage[{i}]')
                            for i in range(get_int(rec, 'AIPackageCount')))
                if f]

    old_pkids = [struct.unpack_from('<I', payload)[0]
                 for s, payload in old_subs if s == b'PKID']
    excluded = set(export_fids(master_rec)) - set(old_pkids)
    new = [f for f in export_fids(plugin_rec)
           if f not in excluded and not is_quest_package(f)]
    new += [f for f in old_pkids
            if f not in set(export_fids(master_rec))
            and not is_quest_package(f)]
    return [(b'PKID', struct.pack('<I', f)) for f in new]


def _rebuild_barter_gold(plugin_rec, master_rec, old_subs):
    """Patch the vendor-gold CNTO for an authored ACBS.BarterGold change.

    TES5 has no barter-gold field; the converter turns it into carried Gold001
    (see convert_NPC_). Whether the actor IS a vendor was decided by the
    master's run (vendor factions), so only an EXISTING gold entry is patched
    — the rest of the run passes through unchanged.
    """
    from ..record_types.actor_common import GOLD001_FID
    gold = get_int(plugin_rec, 'ACBS.BarterGold')
    out = []
    for s, payload in old_subs:
        if (s == b'CNTO' and gold > 0
                and struct.unpack_from('<I', payload)[0] == GOLD001_FID):
            payload = struct.pack('<Ii', GOLD001_FID, gold)
        out.append((s, payload))
    return out


def _rebuild_land_layers(plugin_rec, master_rec, old_subs):
    """Replace the LAND texture-layer run from the plugin's own export.

    Unlike the actor run rebuilders, there is nothing converter-ADDED to
    preserve here: every BTXT/ATXT/VTXT comes from the export's Layer[] list,
    so the author's list is the whole truth. The run is rebuilt wholesale
    through convert_LAND's OWN builder (build_land_layers) because the mapping
    is lossy — same-texture layers merge, alpha layers sort by coverage and cap
    at 6 per quadrant — and a second implementation would disagree with the
    master's for unchanged layers.
    """
    from ..record_types.world import build_land_layers
    blob = build_land_layers(plugin_rec)
    out = []
    off = 0
    while off + 6 <= len(blob):
        sig = blob[off:off + 4]
        size = struct.unpack_from('<H', blob, off + 4)[0]
        out.append((sig, blob[off + 6:off + 6 + size]))
        off += 6 + size
    return out


def _rebuild_qust_targets(plugin_rec, master_rec, old_subs):
    """New QOBJ/FNAM/NNAM/QSTA run for an authored QUST Target[] change.

    Oblivion's QSTA conditions are GetStage gates saying WHEN a marker is live;
    convert_QUST resolves them at build time and hangs each target on the
    objectives where the gate holds (see target_live_at_stage). Changing a
    target's conditions therefore changes which objective carries which
    marker — nothing a byte patch can express.

    The whole objective run is regenerated from the plugin's export, and it is
    deterministic to do so: an alias id is just the ordinal of the target's
    first appearance in the Target list, a pure function of the export, and the
    journal text comes from the same Stage[] entries. The reference ALIASes
    those ids point at are the master's, and this rebuild does not touch them —
    only which objective references which alias.
    """
    from ..dialogue.quest import target_live_at_stage, stage_objective_text
    from ..dialogue.objective_text import short_objective

    alias_by_fid = {}
    targets = []
    t = 0
    while f'Target[{t}].FormID' in plugin_rec:
        tfid = get_formid(plugin_rec, f'Target[{t}].FormID')
        if tfid:
            alias_id = alias_by_fid.setdefault(tfid, len(alias_by_fid))
            tflags = get_int(plugin_rec, f'Target[{t}].Flags') & 0x01
            raws = []
            k = 0
            while True:
                raw = plugin_rec.get(f'Target[{t}].Condition[{k}].Raw')
                if raw is None:
                    break
                raws.append(raw)
                k += 1
            targets.append((alias_id, tflags, raws))
        t += 1

    # The master's alias ids must stay authoritative: this plugin's target list
    # may order refs differently, and the ALIAS records it points at are the
    # master's. Re-map through the MASTER's ordering wherever the ref is known.
    master_alias = {}
    t = 0
    while f'Target[{t}].FormID' in master_rec:
        tfid = get_formid(master_rec, f'Target[{t}].FormID')
        if tfid:
            master_alias.setdefault(tfid, len(master_alias))
        t += 1
    plugin_fids = {aid: fid for fid, aid in alias_by_fid.items()}
    remapped = []
    for alias_id, tflags, raws in targets:
        fid = plugin_fids.get(alias_id)
        mapped = master_alias.get(fid)
        if mapped is None:
            # A target the master does not have has no alias to point at;
            # emitting an out-of-range index would dangle.
            continue
        remapped.append((mapped, tflags, raws))

    out = []
    seen_stages = set()
    i = 0
    while f'Stage[{i}].Index' in plugin_rec:
        stage_idx = get_int(plugin_rec, f'Stage[{i}].Index')
        if stage_idx in seen_stages:
            i += 1
            continue
        txt = stage_objective_text(plugin_rec, i)
        if not txt:
            i += 1
            continue
        seen_stages.add(stage_idx)
        out.append((b'QOBJ', struct.pack('<H', stage_idx)))
        out.append((b'FNAM', struct.pack('<I', 0)))
        # The HUD objective is the SHORT line, not the long log entry — the
        # same swap convert_QUST and quest_objective_texts make. This is the
        # THIRD site deriving NNAM from Stage[].Log[].Text; all three must
        # agree or a translation plugin's objectives diverge from its master's.
        out.append((b'NNAM', _encode_string(short_objective(txt))))
        emitted = set()
        for alias_id, tflags, raws in remapped:
            if alias_id in emitted:
                continue
            if not target_live_at_stage(raws, stage_idx):
                continue
            emitted.add(alias_id)
            out.append((b'QSTA', struct.pack('<iB3x', alias_id, tflags)))
        i += 1
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

def _rebuild_ltex_grasses(plugin_rec, master_rec, old_subs):
    """New GNAM run for an authored Grass[] change on a land texture.

    A groundcover plugin adds grasses to a texture the MASTER defines, so the
    override must carry the master's record untouched except for this run --
    replacing the whole record drops its TNAM and the landscape shader reads a
    null texture set.
    See: docs/commentary/tes4_export_morrowind.md#groundcover-as-grass
    """
    out = []
    i = 0
    while f'Grass[{i}]' in plugin_rec:
        fid = get_formid(plugin_rec, f'Grass[{i}]')
        i += 1
        if fid:
            out.append((b'GNAM', struct.pack('<I', fid)))
    return out


_RUN_LTEX_GRASSES = _RunRebuild((b'GNAM',), _rebuild_ltex_grasses,
                                (('after', b'SNAM'), ('after', b'HNAM'),
                                 ('after', b'MNAM'), ('after', b'TNAM')))

_RUN_LAND_LAYERS = _RunRebuild(
    (b'BTXT', b'ATXT', b'VTXT'), _rebuild_land_layers,
    (('after', b'VCLR'), ('after', b'VHGT'), ('after', b'VNML'),
     ('after', b'DATA')))

# The objective run sits after the quest's stage data (INDX/QSDT/CNAM) and
# before the ALIAS block convert_QUST writes next.
_RUN_QUST_TARGETS = _RunRebuild(
    (b'QOBJ', b'FNAM', b'NNAM', b'QSTA'), _rebuild_qust_targets,
    (('after', b'CNAM'), ('after', b'DNAM')))

_RUN_REBUILDERS = {
    ('QUST', 'Target[]'): _RUN_QUST_TARGETS,
    ('NPC_', 'Item[]'): _RUN_INVENTORY,
    ('CREA', 'Item[]'): _RUN_INVENTORY,
    ('CONT', 'Item[]'): _RUN_INVENTORY,
    ('NPC_', 'Spell[]'): _RUN_SPELLS,
    ('CREA', 'Spell[]'): _RUN_SPELLS,
    ('NPC_', 'AIPackage[]'): _RUN_PACKAGES,
    ('CREA', 'AIPackage[]'): _RUN_PACKAGES,
    ('NPC_', 'ACBS.BarterGold'): _RUN_BARTER_GOLD,
    ('CREA', 'ACBS.BarterGold'): _RUN_BARTER_GOLD,
    ('LTEX', 'Grass[]'): _RUN_LTEX_GRASSES,
    ('LTEX', 'GrassCount'): _RUN_LTEX_GRASSES,
    ('LAND', 'Layer[]'): _RUN_LAND_LAYERS,
    ('LAND', 'LayerCount'): _RUN_LAND_LAYERS,
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


class _Buckets:
    """Per-key dispatch result: which mechanism applies to each changed key.

    `pending` {sig: payload} string substitutions, `indexed` {sig: [values]}
    positional ones, `generic` the keys left to convert-and-diff, `rebuilds`
    the unique _Rebuild specs with `rebuild_keys` {spec: [key]} for KEEP
    reporting, `patchers` [(out_sig, fn, key)], `runs` the unique _RunRebuild
    specs. Order of every list is the order the keys were seen.
    """

    def __init__(self):
        """Empty buckets: no key has been classified yet."""
        self.pending = {}
        self.indexed = {}
        self.generic = set()
        self.rebuilds = []
        self.rebuild_keys = {}
        self.patchers = []
        self.runs = []

    def any_edit(self) -> bool:
        """True when some mechanism would rewrite the record."""
        return bool(self.pending or self.indexed or self.rebuilds
                    or self.patchers or self.runs)

    def claimed(self) -> set:
        """Signatures an explicit spec already rewrote, for _apply_generic."""
        return ({spec.sig for spec in self.rebuilds}
                | {s for s, _fn, _k in self.patchers}
                | {s for run in self.runs for s in run.family}
                | set(self.pending) | set(self.indexed))


def _nested_values(plugin_export: dict, nested: tuple) -> list:
    """Values of a doubly-indexed export field, in outer-then-inner order."""
    outer, inner, field, _sub_sig = nested
    values = []
    i = 0
    while any(k.startswith(f'{outer}[{i}].') for k in plugin_export):
        j = 0
        while f'{outer}[{i}].{inner}[{j}].{field}' in plugin_export:
            values.append(
                plugin_export[f'{outer}[{i}].{inner}[{j}].{field}'])
            j += 1
        i += 1
    return values


def _indexed_values(plugin_export: dict, name: str, field: str) -> list:
    """Values of `name[i].field` for consecutive i from 0."""
    values = []
    i = 0
    while f'{name}[{i}].{field}' in plugin_export:
        values.append(plugin_export[f'{name}[{i}].{field}'])
        i += 1
    return values


def _classify_nested(key, sig_name, plugin_export, nested, buckets,
                     applied: set, unmapped: set):
    """Route a nested string key to `indexed`, plus any DERIVED run it feeds.

    The same source field may drive a second subrecord (Stage[] text writes
    the objectives' NNAM as well as the log's CNAM), so the derived run is
    filled first and independently of whether the direct values exist.
    """
    values = _nested_values(plugin_export, nested)
    derived = _DERIVED_INDEXED_SUBRECORD.get((sig_name, key))
    if derived is not None:
        d_sig, fn_name = derived
        from ..dialogue import quest as quest_converter
        d_values = getattr(quest_converter, fn_name)(plugin_export)
        if d_values:
            buckets.indexed[d_sig] = d_values
    if values:
        buckets.indexed[nested[3]] = values
        applied.add(key)
    else:
        unmapped.add(key)


def _preapplied(key, sig_name) -> bool:
    """True for a key that needs no edit here: ignored, inexpressible, or a
    SOUN field applied to the master's SNDR companion instead of this record
    (see OverrideContext.build_soun_companion).
    """
    return (key in _IGNORED_CHANGES
            or (sig_name, key) in _INEXPRESSIBLE
            or ('*', key) in _INEXPRESSIBLE
            or (sig_name == 'SOUN' and key in _SOUN_COMPANION_KEYS))


def _classify_spec(key, sig_name, buckets, applied: set) -> bool:
    """Route a key to its rebuild/patch/run spec; True when one claimed it.

    Precedence is _REBUILDERS, then _PATCHERS, then _RUN_REBUILDERS. A
    patcher does not mark the key applied here: that waits until the
    subrecord it patches is found in the master's record.
    """
    specs = _REBUILDERS.get((sig_name, key))
    if specs is not None:
        for spec in specs:
            if spec not in buckets.rebuilds:
                buckets.rebuilds.append(spec)
            buckets.rebuild_keys.setdefault(spec, []).append(key)
        applied.add(key)
        return True
    patch = _PATCHERS.get((sig_name, key))
    if patch is not None:
        buckets.patchers.append((patch[0], patch[1], key))
        return True
    run = _RUN_REBUILDERS.get((sig_name, key))
    if run is not None:
        if run not in buckets.runs:
            buckets.runs.append(run)
        applied.add(key)
        return True
    return False


def _classify_changes(changes: dict, sig_name: str, plugin_export: dict,
                      applied: set, unmapped: set) -> _Buckets:
    """Sort each changed export key into the mechanism that can express it.

    Precedence, highest first: pre-applied keys, the rebuild/patch/run specs
    (_classify_spec), nested/indexed string lists, plain _STRING_SUBRECORD;
    anything left falls through to `generic`. Mutates `applied`/`unmapped`
    as each key is decided.
    """
    buckets = _Buckets()
    for key, value in changes.items():
        if _preapplied(key, sig_name):
            applied.add(key)
            continue
        if _classify_spec(key, sig_name, buckets, applied):
            continue
        nested = _NESTED_STRING_SUBRECORD.get(key)
        if nested is not None:
            _classify_nested(key, sig_name, plugin_export, nested, buckets,
                             applied, unmapped)
            continue
        spec = _INDEXED_STRING_SUBRECORD.get(key)
        if spec is not None:
            name, field, sub_sig = spec
            values = _indexed_values(plugin_export, name, field)
            if values:
                buckets.indexed[sub_sig] = values
                applied.add(key)
            else:
                unmapped.add(key)
            continue
        sub_sig = _STRING_SUBRECORD.get(key)
        if sub_sig is None:
            buckets.generic.add(key)
            continue
        buckets.pending[sub_sig] = _encode_string(value)
    return buckets


def _substitute_strings(subs: list, buckets: _Buckets) -> tuple:
    """Copy the master's subrecords, swapping in the string substitutions.

    An `indexed` signature takes the plugin's Nth value at its Nth occurrence;
    occurrences past the end of the plugin's list keep the master's payload so
    the record's structure is never truncated. Returns (out, replaced).
    """
    out = []
    replaced = set()
    seen = {}
    for sub_sig, payload in subs:
        if sub_sig in buckets.indexed:
            n = seen.get(sub_sig, 0)
            seen[sub_sig] = n + 1
            values = buckets.indexed[sub_sig]
            if n < len(values):
                out.append((sub_sig, _encode_string(values[n])))
            else:
                out.append((sub_sig, payload))
            replaced.add(sub_sig)
        elif sub_sig in buckets.pending and sub_sig not in replaced:
            out.append((sub_sig, buckets.pending[sub_sig]))
            replaced.add(sub_sig)
        else:
            out.append((sub_sig, payload))
    return out, replaced


def _insert_missing_strings(out: list, base_sig: bytes, buckets: _Buckets,
                            replaced: set):
    """Insert string fields the master's record lacks, after EDID, as a BLOCK.

    Only signatures _INSERTABLE_SUBRECORDS allows for this record type: a
    mis-paired diff otherwise splices another type's fields in, which xEdit
    rejects and the engine hangs on. Inserting as one block keeps `pending`
    order rather than reversing it.
    """
    insertable = [(sig, payload) for sig, payload in buckets.pending.items()
                  if sig not in replaced
                  and sig in _INSERTABLE_SUBRECORDS.get(base_sig, frozenset())]
    if insertable:
        pos = 1 if out and out[0][0] == b'EDID' else 0
        out[pos:pos] = insertable
        replaced.update(sig for sig, _ in insertable)


def _apply_rebuilds(out: list, buckets: _Buckets, plugin_export: dict,
                    applied: set, unmapped: set):
    """Regenerate single subrecords with the converter's own builder.

    Replaces in place, inserts at the spec's anchor, or removes the subrecord
    when the plugin's record no longer produces one. KEEP means the
    plugin-side state is unavailable: the master's bytes stay and the
    originating keys move to `unmapped`. `keep_tail` preserves the master's
    uninitialised trailing bytes (see _Rebuild.keep_tail).
    """
    for spec in buckets.rebuilds:
        payload = spec.builder(plugin_export)
        if payload is KEEP:
            for key in buckets.rebuild_keys.get(spec, ()):
                applied.discard(key)
                unmapped.add(key)
            continue
        idx = next((i for i, (s, _p) in enumerate(out) if s == spec.sig), None)
        if payload is None:
            if idx is not None:
                del out[idx]
        elif idx is not None:
            tail = spec.keep_tail
            if tail and len(payload) > tail and len(out[idx][1]) >= tail:
                payload = payload[:-tail] + out[idx][1][-tail:]
            out[idx] = (spec.sig, payload)
        else:
            _insert_at_anchor(out, spec.anchors, [(spec.sig, payload)])


def _apply_patchers(out: list, buckets: _Buckets, plugin_export: dict,
                    applied: set, unmapped: set):
    """Patch subrecords in place; a signature the master lacks is unmapped."""
    for out_sig, fn, key in buckets.patchers:
        idx = next((i for i, (s, _p) in enumerate(out) if s == out_sig), None)
        if idx is None:
            unmapped.add(key)
            continue
        out[idx] = (out_sig, fn(out[idx][1], plugin_export))
        applied.add(key)


def _apply_runs(out: list, buckets: _Buckets, plugin_export: dict,
                master_export: dict) -> list:
    """Replace each whole subrecord family with its regenerated run.

    The new run lands at the first occurrence of the old family, or at the
    spec's anchor when the master carried none. Returns the new list.
    """
    for run in buckets.runs:
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
    return out


def _apply_subrecord_edits(subs: list, base_sig: bytes, buckets: _Buckets,
                           plugin_export: dict, master_export: dict,
                           applied: set, unmapped: set) -> list:
    """Run every subrecord mechanism over the master's subrecords, in order.

    String substitution, then insertion of missing strings, then single
    subrecord rebuilds, then in-place patchers, then whole-run rebuilds. The
    order is load-bearing: each phase sees what the previous one emitted.
    """
    out, replaced = _substitute_strings(subs, buckets)
    _insert_missing_strings(out, base_sig, buckets, replaced)
    _apply_rebuilds(out, buckets, plugin_export, applied, unmapped)
    _apply_patchers(out, buckets, plugin_export, applied, unmapped)
    return _apply_runs(out, buckets, plugin_export, master_export)


def apply_changes(master_record: bytes, changes: dict,
                  plugin_export: dict = None,
                  master_export: dict = None) -> tuple:
    """Master's converted record with the author's changes substituted in.

    `changes` is export_diff.diff_records() output: {export_key: plugin_value}.
    `plugin_export` and `master_export` are the raw TES4 exports, needed to
    regenerate a subrecord and to tell converter-added entries apart.

    Returns (record_bytes, applied_keys, unmapped_keys). A key this module
    cannot express leaves the master's value untouched and is returned in
    `unmapped_keys` — never approximated.
    """
    applied = set()
    unmapped = set()
    plugin_export = plugin_export or {}
    master_export = master_export or {}
    sig_name = plugin_export.get('Signature', '')

    buckets = _classify_changes(changes, sig_name, plugin_export,
                                applied, unmapped)

    substitutions = {}
    if buckets.generic:
        substitutions = generic_substitutions(plugin_export, master_export)
        if substitutions is None:
            unmapped |= buckets.generic
            substitutions = {}
        else:
            applied |= buckets.generic

    if not (buckets.any_edit() or substitutions):
        return master_record, applied, unmapped

    subs = split_subrecords(master_record)
    if not subs:
        return (master_record, applied,
                unmapped | set(changes) - applied)

    out = _apply_subrecord_edits(subs, master_record[:4], buckets,
                                 plugin_export, master_export,
                                 applied, unmapped)

    if substitutions:
        out = _apply_generic(out, substitutions, buckets.claimed())

    return join_subrecords(master_record[:RECORD_HEADER_SIZE], out), applied, unmapped


# Subrecord families that INTERLEAVE: the record is a repeating struct whose
# members each have their own signature, so the run is A B A B A B, not AAA BBB.
# Collapsing each signature to the position of its first occurrence (the rule
# for genuinely repeating single-signature runs) destroys the pairing:
# REGN AnvilCoastline came out `RPLI RPLD RPLD RPLD RPLD RPLI RPLI RPLI`
# against the master's correct `RPLI RPLD` x4, and xEdit rejects it with
# "record REGN contains unexpected (or out of order) subrecord RPLD".
# Sourced from the xEdit definitions (wbDefinitionsCommon wbRegionAreas:
# wbRArray of wbRStruct[RPLI, RPLD]).
_INTERLEAVED_FAMILIES = (
    frozenset({b'RPLI', b'RPLD'}),      # REGN Region Areas
)


def _apply_generic(out: list, substitutions: dict, claimed: set) -> list:
    """Substitute whole subrecord runs from a convert-and-diff result.

    Each signature is replaced as a UNIT (all occurrences at the position of
    the first) so repeated subrecords cannot end up half-master, half-plugin.
    A signature the plugin's conversion drops entirely is removed; one the
    master's lacks is appended in the converter's own emission order.

    An INTERLEAVED family (see _INTERLEAVED_FAMILIES) is exempt: its members
    are substituted one occurrence at a time, in place, so the A B A B pairing
    the engine reads the record by is preserved.
    """
    interleaved = set()
    for family in _INTERLEAVED_FAMILIES:
        present = family & set(substitutions)
        if len(present) > 1 or (present and len(family & {s for s, _ in out}) > 1):
            interleaved |= family

    result = []
    done = set()
    taken = {}
    for sig, payload in out:
        if sig in claimed or sig not in substitutions:
            result.append((sig, payload))
            continue
        if sig in interleaved:
            # One-for-one, positionally: keep this occurrence's own slot.
            n = taken.get(sig, 0)
            taken[sig] = n + 1
            values = substitutions[sig]
            if n < len(values):
                result.append((sig, values[n]))
            # Past the end: the plugin has fewer entries, so drop this one.
            done.add(sig)
            continue
        if sig in done:
            continue          # folded into the run written at first occurrence
        done.add(sig)
        result.extend((sig, p) for p in substitutions[sig])
    # Interleaved members the master had fewer of than the plugin: append the
    # remainder so no authored entry is silently lost.
    for sig in interleaved & set(substitutions):
        extra = substitutions[sig][taken.get(sig, 0):]
        if extra and sig not in claimed:
            result.extend((sig, p) for p in extra)
    for sig, payloads in substitutions.items():
        if sig not in done and sig not in claimed:
            result.extend((sig, p) for p in payloads)
    return result

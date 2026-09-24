"""MGEF variants: one registry of emitted magic effects, and the clones items need.

Skyrim keeps on the MGEF four things Oblivion keeps on each item's effect
entry -- the actor value, the script, the path a bound item takes, and the
casting type plus delivery -- so one TES4 effect code becomes a base record
and a family of variants.  Every emitted record is registered here as parts,
so a variant can itself be cloned: a script effect on an ability is the
script variant, then its Constant clone.
"""

import struct
import threading

from ..base.text_reader import get_int, get_str
from ..base.writer import (pack_formid_subrecord, pack_record,
                           pack_string_subrecord, pack_subrecord)
from ..generated.vanilla_mgef_data import VANILLA_MGEF_DATA
from .magic import (
    A_BOUND_WEAPON, A_SCRIPT, AV_NONE, MENU_ART_GENERIC, O_ARCHETYPE, O_ASSOC_ITEM,
    O_CASTING_TYPE, O_COUNTER_COUNT, O_EXPLOSION, build_data, code_to_fid,
    fit_delivery, get_archetype, is_derived, known_sigs, menu_display_object,
    mgef_parts, mgef_tail, resolve_actor_value, source_record)
from .magic_art import explosion, projectile, sound_set

#: {output MGEF FormID: (EditorID, subrecords before DATA, DATA, subrecords after)} of every emitted MGEF.
_parts: dict = {}
#: {(FormID site, key): output FormID} of every clone already written.
_clones: dict = {}
#: {(TES4 code, TES4 actor value): output FormID} of the per-actor-value variants.
_av_variants: dict = {}
#: {(TES4 SCPT FormID, TES4 range): output FormID} of the script-effect variants.
_seff_variants: dict = {}
#: {output MGEF FormID: export MGEF record} for every variant, so a clone knows its model.
_recs: dict = {}
_lock = threading.RLock()

#: TES4 effect range -> TES5 delivery (0 Self, 1 Contact, 2 Aimed).
RANGE_DELIVERY = {'Self': 0, 'Touch': 1, 'Target': 2}
#: Owner casting type -> the casting type its effects carry; a Scroll (3) casts Fire and Forget effects.
MGEF_CAST_FOR_OWNER = {0: 0, 1: 1, 2: 2, 3: 1}
_CAST_NAMES = ('Constant', 'FF', 'Conc')
_DELIVERY_NAMES = ('Self', 'Contact', 'Aimed', 'TargetActor', 'TargetLocation')


# ---------------------------------------------------------------------------
# Registry and clones
# ---------------------------------------------------------------------------

def reset() -> None:
    """Forget every variant; runs once per plugin, before any is built."""
    with _lock:
        for table in (_parts, _clones, _av_variants, _seff_variants, _recs):
            table.clear()


def _parts_of(fid: int):
    """The parts of an MGEF a clone can start from, built on first use.

    A base record of this plugin, a variant already emitted, or a vanilla
    effect the fallback tables name (its DATA only, from vanilla_mgef_data).
    """
    got = _parts.get(fid)
    if got is None:
        rec = source_record(fid)
        if rec is not None:
            got = _parts[fid] = mgef_parts(rec)
        elif fid in VANILLA_MGEF_DATA:
            edid, data_hex = VANILLA_MGEF_DATA[fid]
            got = _parts[fid] = (edid, b'', bytes.fromhex(data_hex), b'')
    return got


def _source_rec(fid: int):
    """The export MGEF record an emitted MGEF was built from, or None."""
    return _recs.get(fid) or source_record(fid)


def _emit(writer, fid: int, edid: str, head: bytes, data: bytes,
          tail: bytes = b'', rec: dict = None) -> None:
    """Write one variant MGEF of source ``rec`` and register it for later clones."""
    subs = pack_string_subrecord('EDID', edid) + head
    subs += pack_subrecord('DATA', data) + tail
    writer.add_record('MGEF', pack_record('MGEF', fid, 0, subs))
    _parts[fid] = (edid, head, data, tail)
    if rec is not None:
        _recs[fid] = rec


def clone(src_fid: int, site: str, key, edid: str, patch, writer,
          head: bytes = None) -> int:
    """FormID of a clone of ``src_fid`` whose DATA ``patch`` rewrote, written on first use.

    The clone keeps the source's other subrecords (VMAD, FULL, DNAM) unless
    ``head`` replaces the ones before DATA, and carries no ESCE.  Returns 0
    when there is no source to clone (see _parts_of).
    """
    if writer is None:
        return 0
    with _lock:
        cached = _clones.get((site, key))
        if cached:
            return cached
        src = _parts_of(src_fid)
        if src is None:
            return 0
        _, src_head, src_data, tail = src
        data = bytearray(src_data)
        struct.pack_into('<H', data, O_COUNTER_COUNT, 0)
        patch(data)
        fid = writer.derive_formid(site, key)
        _emit(writer, fid, edid, src_head if head is None else head, bytes(data),
              tail, _source_rec(src_fid))
        _clones[(site, key)] = fid
        return fid


# ---------------------------------------------------------------------------
# Casting type and delivery follow the owner
# ---------------------------------------------------------------------------

def owner_delivery(rec: dict) -> int:
    """A spell's, scroll's or enchantment's delivery: its farthest range."""
    ranges = [RANGE_DELIVERY.get(get_str(rec, f'Effect[{i}].Type'), 0)
              for i in range(get_int(rec, 'EffectCount'))]
    return max(ranges, default=0)


def menu_object(first_effect: int) -> int:
    """A spell's MDOB: the vanilla menu art its first effect's MGEF calls for."""
    parts = _parts_of(first_effect)
    return menu_display_object(parts[2]) if parts else MENU_ART_GENERIC


def delivery_variant(fid: int, cast: int, delivery: int, writer,
                     area: bool = False) -> int:
    """``fid`` itself when it already matches its slot, else a matching clone.

    A slot needs its owner's casting type and delivery (vanilla matches them
    on 2138 of 2146 slots; an ability whose effects are Fire and Forget
    applies them once and lets them expire), and a bolt with an area needs
    the model's area burst, which no other use of the effect may play.
    """
    src = _parts_of(fid)
    if src is None:
        return fid
    rec = _source_rec(fid)
    own_art = delivery in (2, 4) and rec is not None
    burst = explosion(rec) if area and own_art else 0
    if (struct.unpack_from('<II', src[2], O_CASTING_TYPE) == (cast, delivery)
            and struct.unpack_from('<I', src[2], O_EXPLOSION)[0] == burst):
        return fid

    def patch(data):
        """Set the pair, the projectile and impact set that pair needs, and the burst."""
        fit_delivery(data, cast, delivery, projectile(rec) if own_art else 0)
        struct.pack_into('<I', data, O_EXPLOSION, burst)

    edid = (f'TES4{src[0].removeprefix("TES4")}{_CAST_NAMES[cast]}'
            f'{_DELIVERY_NAMES[delivery]}{"Area" if burst else ""}')
    return clone(fid, 'MGEF_DELIVERY', (fid, cast, delivery, burst), edid,
                 patch, writer) or fid


# ---------------------------------------------------------------------------
# Per-actor-value variants
# ---------------------------------------------------------------------------

_ATTR_NAMES = {
    0: 'Strength', 1: 'Intelligence', 2: 'Willpower', 3: 'Agility',
    4: 'Speed', 5: 'Endurance', 6: 'Personality', 7: 'Luck',
}
_SKILL_NAMES = {
    12: 'Armorer', 13: 'Athletics', 14: 'Blade', 15: 'Block', 16: 'Blunt',
    17: 'HandToHand', 18: 'HeavyArmor', 19: 'Alchemy', 20: 'Alteration',
    21: 'Conjuration', 22: 'Destruction', 23: 'Illusion', 24: 'Mysticism',
    25: 'Restoration', 26: 'Acrobatics', 27: 'LightArmor', 28: 'Marksman',
    29: 'Mercantile', 30: 'Security', 31: 'Sneak', 32: 'Speechcraft',
}


def _wanted_av_pairs(by_code: dict, effect_records: list) -> list:
    """Every (code, TES4 actor value) an item uses, sorted so FormIDs stay reproducible."""
    wanted = set()
    for rec in effect_records:
        for i in range(get_int(rec, 'EffectCount')):
            code = get_str(rec, f'Effect[{i}].EFID')
            if code in by_code:
                wanted.add((code, get_int(rec, f'Effect[{i}].ActorValue', -1)))
    return sorted(wanted)


def build_av_variants(mgef_records: list, effect_records: list, writer) -> int:
    """Emit one MGEF per (actor-value-derived code, actor value) the plugin uses.

    Oblivion names the attribute or skill on each item's effect entry and
    Skyrim on the MGEF, so one DGAT becomes Damage Strength, Damage
    Endurance, and so on.  Returns the number written.
    """
    _av_variants.clear()
    if writer is None:
        return 0
    by_code = {get_str(r, 'EditorID'): r for r in mgef_records
               if get_str(r, 'EditorID') and is_derived(get_str(r, 'EditorID'), r)}
    written = 0
    for code, av in _wanted_av_pairs(by_code, effect_records):
        src = by_code[code]
        tes5_av = resolve_actor_value(code, av, src)
        name = _ATTR_NAMES.get(av) or _SKILL_NAMES.get(av)
        if tes5_av == AV_NONE or not name:
            continue
        fid = writer.derive_formid('MGEF_AV', (code, av))
        full = get_str(src, 'FULL')
        head = pack_string_subrecord('FULL', _variant_name(full, name)) if full else b''
        data = build_data(src, code, get_archetype(code, src), tes5_av, 0)
        head += pack_formid_subrecord('MDOB', menu_display_object(data))
        _emit(writer, fid, f'TES4{code}{name}', head, data, mgef_tail(src), src)
        _av_variants[(code, av)] = fid
        written += 1
    return written


def _variant_name(base_full: str, stat_name: str) -> str:
    """"Damage Attribute" + "Strength" -> "Damage Strength"."""
    for tail in (' Attribute', ' Skill'):
        if base_full.endswith(tail):
            return f'{base_full[:-len(tail)]} {stat_name}'
    return f'{base_full} ({stat_name})'


def get_mgef_formid(code: str, effect_av: int = -1) -> int:
    """One effect's per-actor-value variant, else its base MGEF."""
    return _av_variants.get((code, effect_av)) or code_to_fid.get(code, 0)


# ---------------------------------------------------------------------------
# Script-effect variants
# ---------------------------------------------------------------------------

def build_seff_variants(mgef_records: list, effect_records: list, writer,
                        fid_to_edid: dict = None) -> int:
    """Emit one Script-archetype MGEF per distinct (TES4 script, range); returns the count.

    TES4 names a script effect's script on each item's effect entry, Skyrim on
    the MGEF, so each distinct script needs its own record.
    """
    from ..base.object_scripts import get_magic_effect_vmad

    _seff_variants.clear()
    seff = next((r for r in mgef_records if get_str(r, 'EditorID') == 'SEFF'), None)
    if writer is None or seff is None:
        return 0
    wanted = sorted({(get_str(rec, f'ScriptEffect[{i}].FormID'),
                      get_str(rec, f'Effect[{i}].Type'))
                     for rec in effect_records
                     for i in range(get_int(rec, 'EffectCount'))
                     if get_str(rec, f'Effect[{i}].EFID') == 'SEFF'
                     and get_str(rec, f'ScriptEffect[{i}].FormID')})
    fid_to_edid = fid_to_edid or {}
    full = get_str(seff, 'FULL')
    written = 0
    for scpt, etype in wanted:
        vmad = get_magic_effect_vmad(scpt)
        if not vmad:
            continue
        data = bytearray(build_data(seff, 'SEFF', A_SCRIPT, AV_NONE, 0))
        fit_delivery(data, 1, RANGE_DELIVERY.get(etype, 0))
        fid = writer.derive_formid('MGEF_SEFF', (scpt, etype))
        head = vmad + (pack_string_subrecord('FULL', full) if full else b'')
        head += pack_formid_subrecord('MDOB', menu_display_object(data))
        name = fid_to_edid.get(scpt, scpt)
        _emit(writer, fid, f'TES4SEFF{name}{etype or "Self"}', head, bytes(data),
              sound_set(seff), seff)
        _seff_variants[(scpt, etype)] = fid
        written += 1
    return written


def get_seff_variant(scpt_fid: str, effect_type: str) -> int:
    """FormID of the Script MGEF carrying one effect's script (0 if none)."""
    return _seff_variants.get((scpt_fid, effect_type), 0)


# ---------------------------------------------------------------------------
# Scripted bound items
# ---------------------------------------------------------------------------

BOUND_ITEM_SCRIPT = 'TES4_BoundItemEffect'

#: TES4 spell types the engine applies rather than casts: 3 Lesser Power, 4 Ability.
UNCASTABLE_SPELL_TYPES = frozenset({3, 4})


def bound_item_assoc(mgef_fid: int) -> int:
    """Output WEAP/ARMO an emitted bound-item MGEF equips (0 if not one)."""
    src = _parts_of(mgef_fid)
    if src is None or struct.unpack_from('<I', src[2], O_ARCHETYPE)[0] != A_BOUND_WEAPON:
        return 0
    return struct.unpack_from('<I', src[2], O_ASSOC_ITEM)[0]


def bound_assoc_is_armor(mgef_fid: int) -> bool:
    """True when a bound effect equips armor, which archetype 17 cannot."""
    return known_sigs.get(bound_item_assoc(mgef_fid)) in ('ARMO', 'CLOT')


def bound_script_variant(mgef_fid: int, assoc_item: int, writer) -> int:
    """FormID of the Script clone of a bound effect carrying TES4_BoundItemEffect (0 if impossible).

    Skyrim has no bound armor and archetype 17 fires only on a cast, so bound
    armor, and any bound item on an ability or lesser power, runs as a script.
    See: docs/commentary/tes5_import_magic.md#bound-items
    """
    from script_convert.pipeline import build_vmad_object_script

    src = _parts_of(mgef_fid)
    if not mgef_fid or not assoc_item or src is None:
        return 0

    def patch(data):
        """Script archetype, no Assoc. Item, Fire and Forget on Self."""
        struct.pack_into('<I', data, O_ARCHETYPE, A_SCRIPT)
        struct.pack_into('<I', data, O_ASSOC_ITEM, 0)
        fit_delivery(data, 1, 0)

    vmad = pack_subrecord('VMAD', build_vmad_object_script(
        BOUND_ITEM_SCRIPT, {'BoundItem': assoc_item}))
    return clone(mgef_fid, 'MGEF_BOUND', (mgef_fid, assoc_item),
                 f'TES4{src[0]}Scripted', patch, writer, head=vmad)

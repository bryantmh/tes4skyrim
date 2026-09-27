"""TES4 script block types -> the Papyrus events they become.

`assemble` drops a block whose type is absent from `BLOCK_MAP`, body and all,
so this table is the vocabulary of what survives conversion at all. FO3/FNV
add block types Oblivion never emits; those live in `constants_falloutnv`.

`BLOCK_FILTER_PARAM` names the event parameter an Oblivion block filter
(`begin OnEquip player`) has to become a guard on, since Papyrus has no filter;
a block type absent from it has no parameter to filter on and its filter is
dropped with a TODO. `COMBAT_STATE_GUARDS` carries the blocks that merge into
the one OnCombatStateChanged event, each with the state it tests.

See: docs/commentary/script_convert.md#block-type-mapping
"""

import re

from script_convert.constants import (
    is_base_object_type, is_generated_script_type,
    record_type_to_papyrus, safe_property_name,
)
from script_convert.constants_falloutnv import (
    FALLOUT_BLOCK_FILTER_PARAM,
    FALLOUT_BLOCK_MAP,
    FALLOUT_COMBAT_STATE_GUARDS,
)

BLOCK_MAP = {
    'gamemode':           ('Event OnUpdate()', 'EndEvent'),
    'menumode':           ('Event OnUpdate()', 'EndEvent'),
    'onactivate':         ('Event OnActivate(ObjectReference akActionRef)', 'EndEvent'),
    'onadd':              ('Event OnContainerChanged(ObjectReference akNewContainer, ObjectReference akOldContainer)', 'EndEvent'),
    'ondrop':             ('Event OnContainerChanged(ObjectReference akNewContainer, ObjectReference akOldContainer)', 'EndEvent'),
    'onequip':            ('Event OnEquipped(Actor akActor)', 'EndEvent'),
    'onunequip':          ('Event OnUnequipped(Actor akActor)', 'EndEvent'),
    'ondeath':            ('Event OnDeath(Actor akKiller)', 'EndEvent'),
    'onhit':              ('Event OnHit(ObjectReference akAggressor, Form akSource, Projectile akProjectile, bool abPowerAttack, bool abSneakAttack, bool abBashAttack, bool abHitBlocked)', 'EndEvent'),
    'onhitwith':          ('Event OnHit(ObjectReference akAggressor, Form akSource, Projectile akProjectile, bool abPowerAttack, bool abSneakAttack, bool abBashAttack, bool abHitBlocked)', 'EndEvent'),
    'onload':             ('Event OnLoad()', 'EndEvent'),
    'onreset':            ('Event OnReset()', 'EndEvent'),
    'onsell':             ('Event OnSell(Actor akSeller)', 'EndEvent'),
    'ontrigger':          ('Event OnTrigger(ObjectReference akActionRef)', 'EndEvent'),
    'ontriggerenter':     ('Event OnTriggerEnter(ObjectReference akActionRef)', 'EndEvent'),
    'ontriggerleave':     ('Event OnTriggerLeave(ObjectReference akActionRef)', 'EndEvent'),
    'onmagiceffectapply': ('Event OnMagicEffectApply(ObjectReference akCaster, MagicEffect akEffect)', 'EndEvent'),
    'oninit':             ('Event OnInit()', 'EndEvent'),
    'onpackagestart':     ('Event OnPackageStart(Package akNewPackage)', 'EndEvent'),
    'onpackagedone':      ('Event OnPackageEnd(Package akOldPackage)', 'EndEvent'),
    'onpackageend':       ('Event OnPackageEnd(Package akOldPackage)', 'EndEvent'),
    'onpackagechange':    ('Event OnPackageChange(Package akOldPackage)', 'EndEvent'),
    'ontriggeractor':     ('Event OnTrigger(ObjectReference akActionRef)', 'EndEvent'),
    'ontriggermob':       ('Event OnTrigger(ObjectReference akActionRef)', 'EndEvent'),
    'onmagiceffecthit':   ('Event OnMagicEffectApply(ObjectReference akCaster, MagicEffect akEffect)', 'EndEvent'),
    'onactorequip':       ('Event OnEquipped(Actor akActor)', 'EndEvent'),
    'onalarm':            ('Event OnCombatStateChanged(Actor akTarget, int aeCombatState)', 'EndEvent'),
    'onstartcombat':      ('Event OnCombatStateChanged(Actor akTarget, int aeCombatState)', 'EndEvent'),
    'scripteffectstart':  ('Event OnEffectStart(Actor akTarget, Actor akCaster)', 'EndEvent'),
    'scripteffectfinish': ('Event OnEffectFinish(Actor akTarget, Actor akCaster)', 'EndEvent'),
    'scripteffectupdate': ('Event OnUpdate()', 'EndEvent'),
    **FALLOUT_BLOCK_MAP,
}

BLOCK_FILTER_PARAM = {
    'onactivate':         ('akActionRef', 'ObjectReference'),
    'onadd':              ('akNewContainer', 'ObjectReference'),
    'ondrop':             ('akOldContainer', 'ObjectReference'),
    'onequip':            ('akActor', 'Actor'),
    'onactorequip':       ('akActor', 'Actor'),
    'onunequip':          ('akActor', 'Actor'),
    'onsell':             ('akSeller', 'Actor'),
    'ontrigger':          ('akActionRef', 'ObjectReference'),
    'ontriggerenter':     ('akActionRef', 'ObjectReference'),
    'ontriggerleave':     ('akActionRef', 'ObjectReference'),
    'ontriggeractor':     ('akActionRef', 'ObjectReference'),
    'ontriggermob':       ('akActionRef', 'ObjectReference'),
    'onhit':              ('akAggressor', 'ObjectReference'),
    'onhitwith':          ('akSource', 'Form'),
    'ondeath':            ('akKiller', 'Actor'),
    'onstartcombat':      ('akTarget', 'Actor'),
    'onmagiceffecthit':   ('akEffect', 'MagicEffect'),
    'onmagiceffectapply': ('akEffect', 'MagicEffect'),
    'onpackagestart':     ('akNewPackage', 'Package'),
    'onpackagedone':      ('akOldPackage', 'Package'),
    'onpackageend':       ('akOldPackage', 'Package'),
    'onpackagechange':    ('akOldPackage', 'Package'),
    **FALLOUT_BLOCK_FILTER_PARAM,
}

#: TES4 block type -> the Papyrus combat-state test its filter stood for.
COMBAT_STATE_GUARDS = {'onalarm': 'aeCombatState != 0',
                       'onstartcombat': 'aeCombatState == 1',
                       **FALLOUT_COMBAT_STATE_GUARDS}


def _filter_property_type(param_type: str, rtype: str) -> 'str | None':
    """The Papyrus type to bind the filter's property at, or None if unusable.

    None means the event parameter cannot carry what the filter names -- an
    ACTOR parameter against an item filter.
    See: docs/commentary/script_convert.md#block-filter-guards
    """
    filter_is_actor = rtype in ('Actor', 'ObjectReference')
    if param_type == 'Actor' and not filter_is_actor:
        return None
    if param_type in ('ObjectReference', 'Actor', 'Form'):
        return rtype if filter_is_actor else param_type
    return param_type


def _rebind_existing(param: str, safe: str, param_type: str,
                     existing: str) -> 'str | None':
    """The guard for a property already bound at a different type, or None.

    None means the two types are genuinely incomparable, so the caller must
    keep the body but not run it.
    See: docs/commentary/script_convert.md#block-filter-guards
    """
    if (is_generated_script_type(existing)
            and param_type in ('Actor', 'ObjectReference', 'Form')):
        return f'{param} == {safe}'
    if param_type == 'Form' and is_base_object_type(existing):
        return f'{param} == {safe}'
    return None


def _filter_record_type(conv, name: str) -> str:
    """The Papyrus type of the record `name` names, or '' if unresolvable."""
    if not re.match(r'^\w+$', name) or not conv.xref:
        return ''
    fid = conv.xref.edid_to_formid.get(name.lower(), '')
    if not fid:
        return ''
    return record_type_to_papyrus(conv.xref.record_type.get(fid, ''))


def _ammo_hit_guard(conv, name: str) -> str:
    """The OnHit guard for `begin OnHitWith <ammo>`, read off the shooter.

    See: docs/commentary/script_convert.md#onhitwith-ammo
    """
    safe = safe_property_name(name)
    conv.sc.property_refs[safe] = 'Ammo'
    return f'TES4Polyfill.HitWithAmmo(akAggressor, {safe})'


def block_filter_guard(conv, block_type: str,
                       block_filter: str) -> 'str | None':
    """Compile a TES4 block filter into a Papyrus condition, or '' if none.

    Returns None when a real filter exists but CANNOT be expressed, so the
    caller keeps the body commented out rather than running it for every event.
    See: docs/commentary/script_convert.md#block-filter-guards
    """
    if not block_filter:
        return ''
    target = BLOCK_FILTER_PARAM.get(block_type)
    if not target:
        return ''
    param, param_type = target

    name = block_filter.strip()
    if name.lower() == 'player':
        return f'{param} == Game.GetPlayer()'

    rtype = _filter_record_type(conv, name)
    if not rtype:
        return ''
    if block_type == 'onhitwith' and rtype == 'Ammo':
        return _ammo_hit_guard(conv, name)
    ptype = _filter_property_type(param_type, rtype)
    if ptype is None:
        return ''

    safe = safe_property_name(name)
    existing = conv.sc.property_refs.get(safe)
    if existing and existing != ptype:
        return _rebind_existing(param, safe, param_type, existing)
    conv.sc.property_refs[safe] = ptype
    return f'{param} == {safe}'

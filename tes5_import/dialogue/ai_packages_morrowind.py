"""
The TES3 AI commands as real Skyrim packages.

`AiTravel`, `AiWander`, `AiFollow`, `AiEscort` and `AiActivate` name no record
in Morrowind -- the parameters sit inline in the script. Skyrim has no call
that hands an actor a package, so the only faithful route is the one the CK
itself uses: the packages live on a QUEST ALIAS, and the runtime points that
alias at whichever actor a script named.

One quest carries a POOL of slots per package kind: an alias for the actor,
an alias for what the package aims at, and the PACK joining them. An alias
holds one reference, so a slot is what lets several actors run one kind at
once. Every package's location and target are ALIAS-typed, so a slot serves
every call site.

See: docs/commentary/morrowind_runtime.md#ai-packages-are-real-packages
"""

import os
import struct

from ..packages.converter import (build_alias_location, build_alias_target,
                                  build_pkdt, DEFAULT_INTERRUPT, Inputs,
                                  SPEED_WALK, T5_MUST_COMPLETE)
from ..packages.templates import ACTIVATE, ESCORT, FOLLOW, SANDBOX, TRAVEL
from ..record_types.common import (pack_formid_subrecord, pack_record,
                                   pack_string_subrecord, pack_subrecord,
                                   pack_uint32_subrecord)
from .quest import QUST_ALLOW_REPEATED_STAGES

#: The quest, `slots=N`, a PACK per slot and an alias index per line.
ALIASES_TABLE = 'ai_aliases.txt'

#: derive_formid sites, keyed by slot name so the ids never move.
_QUEST_SITE = 'MW_AI_QUEST'
_PACK_SITE = 'MW_AI_PACK'

#: The one quest per plugin that owns the AI aliases.
_QUEST_EDID = 'MWAIPackages'

#: High enough that an AI command beats a standing schedule, below a scene.
_PRIORITY = 90

#: Optional | Allow Reuse in Quest: starts EMPTY, clearable, one ref in several.
_ALIAS_FNAM = 0x0000000A

#: An alias location's radius: 0 means "at the reference".
_AT_THE_REF = 0

#: How far a wander package sandboxes around its marker, in units.
_WANDER_RADIUS = 512

#: Each package kind and the vanilla template it instances.
_KINDS = (
    ('travel', TRAVEL),
    ('wander', SANDBOX),
    ('follow', FOLLOW),
    ('escort', ESCORT),
    ('activate', ACTIVATE),
)

#: How many actors can run one package kind at the same time.
_SLOTS = 8

#: Every slot name, `<kind><n>`, in ALST order.
_SLOT_NAMES = tuple(f'{kind}{n}' for kind, _t in _KINDS for n in range(_SLOTS))

#: Two aliases per slot: who RUNS the package, and what it aims AT.
_ALIAS_NAMES = tuple(name for slot in _SLOT_NAMES
                     for name in (f'{slot}Actor', f'{slot}Target'))


def _alias_index(name: str) -> int:
    """The ALST index of an alias, which is what a PLDT/PTDA names it by."""
    return _ALIAS_NAMES.index(name)


def _aliases(pack_ids: dict) -> bytes:
    """ALST ALID FNAM [ALPC] ALED per alias.

    🛑 No ALFR: a forced-reference alias names its reference at AUTHOR time,
    and these are filled by `ForceRefTo` at runtime, which is the mechanism.
    The package hangs off the ACTOR's alias, whose stack it joins.
    """
    subs = b''
    for index, name in enumerate(_ALIAS_NAMES):
        subs += pack_uint32_subrecord('ALST', index)
        subs += pack_string_subrecord('ALID', name)
        subs += pack_uint32_subrecord('FNAM', _ALIAS_FNAM)
        if name.endswith('Actor'):
            subs += pack_formid_subrecord('ALPC', pack_ids[name[:-5]])
        subs += pack_subrecord('ALED', b'')
    return subs


def quest_record(formid: int, pack_ids: dict) -> bytes:
    """The QUST carrying every AI alias, at exactly `formid`.

    Order: EDID FULL DNAM NEXT ANAM [aliases]. No stages or objectives -- it
    never shows in the journal and exists only to own the aliases.
    """
    subs = pack_string_subrecord('EDID', _QUEST_EDID)
    subs += pack_string_subrecord('FULL', 'AI')
    subs += pack_subrecord('DNAM', struct.pack(
        '<HBBII', QUST_ALLOW_REPEATED_STAGES, _PRIORITY, 0, 0, 0))
    subs += pack_subrecord('NEXT', b'')
    subs += pack_uint32_subrecord('ANAM', len(_ALIAS_NAMES))
    subs += _aliases(pack_ids)
    return pack_record('QUST', formid, 0, subs)


def _inputs(kind: str, slot: str, template) -> Inputs:
    """The template's vanilla defaults with its alias inputs pointed at this
    slot's own aliases."""
    inputs = Inputs(template)
    target = _alias_index(f'{slot}Target')
    if kind == 'travel':
        inputs.set('location', build_alias_location(target, _AT_THE_REF))
    elif kind == 'wander':
        inputs.set('location', build_alias_location(target, _WANDER_RADIUS))
        inputs.set('allow_wandering', 1)
    elif kind == 'escort':
        inputs.set('target', build_alias_target(target))
        inputs.set('location', build_alias_location(
            _alias_index(f'{slot}Actor'), _AT_THE_REF))
    else:
        inputs.set('target', build_alias_target(target))
    return inputs


def _any_time() -> bytes:
    """PSDT for any month, day and hour with no duration."""
    return struct.pack('<bbBbb3xi', -1, -1, 0, -1, -1, 0)


def pack_record_for(kind: str, slot: str, template, formid: int,
                    quest_fid: int) -> bytes:
    """One PACK instance for one slot of a package kind, at exactly `formid`.

    Order: EDID PKDT PSDT QNAM PKCU <inputs> POBA/POEA/POCA. MustComplete is
    set so the package survives to completion rather than being dropped at the
    actor's next evaluation, which is what a TES3 script expects.
    """
    subs = pack_string_subrecord('EDID', f'MWAI{slot.capitalize()}')
    subs += pack_subrecord('PKDT', build_pkdt(T5_MUST_COMPLETE, SPEED_WALK,
                                              interrupt=DEFAULT_INTERRUPT))
    subs += pack_subrecord('PSDT', _any_time())
    subs += pack_formid_subrecord('QNAM', quest_fid)
    subs += pack_subrecord('PKCU', struct.pack('<III', len(template.inputs),
                                               template.formid,
                                               template.version))
    subs += _inputs(kind, slot, template).emit()
    for marker in ('POBA', 'POEA', 'POCA'):
        subs += pack_subrecord(marker, b'')
        subs += pack_formid_subrecord('INAM', 0)
        subs += pack_subrecord('PDTO', struct.pack('<II', 0, 0))
    return pack_record('PACK', formid, 0, subs)


def write_ai_packages(writer, side_dir: str, plugin_name: str) -> int:
    """Mint the AI quest, its aliases and one PACK per slot, and stage the
    alias table the runtime fills. Returns how many packages were written."""
    quest_fid = writer.derive_formid(_QUEST_SITE, _QUEST_EDID)
    pack_ids = {slot: writer.derive_formid(_PACK_SITE, slot)
                for slot in _SLOT_NAMES}
    for kind, template in _KINDS:
        for n in range(_SLOTS):
            slot = f'{kind}{n}'
            writer.add_record('PACK', pack_record_for(
                kind, slot, template, pack_ids[slot], quest_fid))
    writer.add_record('QUST', quest_record(quest_fid, pack_ids))
    lines = [f'quest={plugin_name}|{quest_fid:08X}', f'slots={_SLOTS}']
    lines += [f'pack.{slot}={plugin_name}|{pack_ids[slot]:08X}'
              for slot in _SLOT_NAMES]
    lines += [f'{name}={index}' for index, name in enumerate(_ALIAS_NAMES)]
    with open(os.path.join(side_dir, ALIASES_TABLE), 'w',
              encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    return len(pack_ids)

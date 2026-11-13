"""Diseases caught from a carrier's melee hits, carried by Skyrim's own mechanism.

Vanilla races name their disease as the ATKD Attack Spell of their attacks (SkeeverRace casts
DiseaseAtaxia), and each disease effect's own condition is the chance (GetRandomPercent <= 2.0),
rolled once when the effect lands. A carrier here is any actor whose spell list holds a disease:
its attacks override its race's and cast its contact spells in turn, the way ChaurusRace splits
its claws (Rattles) from its bite (poison).

See: docs/commentary/tes5_import_actors.md#disease-contact
"""

import struct

from ..base.conditions import build_ctda
from ..base.master_export import values_of
from ..base.text_reader import get_formid, get_int, get_str
from ..base.writer import pack_formid_subrecord, pack_string_subrecord, pack_subrecord
from ..record_types.magic import O_FLAGS, O_MAGIC_SKILL
from ..record_types.magic_variants import clone
from ..record_types.npc_morrowind import tes3_gmst
from ..record_types.world_falloutnv import is_fallout_source
from ..record_types.world_morrowind import is_tes3_source
from .vampirism_morrowind import SPELL_TYPE_DISEASE

#: TES3 fDiseaseXferChance (OpenMW defaultgmsts): the percent chance a hit passes each disease on.
TES3_CONTACT_CHANCE = 2.5

#: Oblivion.exe's own fMagicDiseaseTransferMult and fMagicDiseaseTransferBase, for a chain setting neither.
_TES4_TRANSFER_DEFAULTS = {'fMagicDiseaseTransferMult': 0.25, 'fMagicDiseaseTransferBase': 0.0}

#: This run's TES4 transfer GMSTs (load_contact_settings).
_tes4_settings: dict = {}

#: ATKD Attack Flags bit: the entry replaces its attack race's (vanilla's per-NPC EncVampire attacks).
OVERRIDE_DATA = 0x80000000

#: Skyrim.esm's disease effects' DATA flags (No Duration/Area, Recover, Detrimental) and skill (Restoration).
_DISEASE_FLAGS, _DISEASE_SKILL = 0x00400A06, 22

#: Condition functions GetRandomPercent and HasMagicEffect; the <= operator.
_GET_RANDOM_PERCENT, _HAS_MAGIC_EFFECT, _OP_LE = 77, 214, 0xA0

#: Skyrim.esm NordRace's attacks: (event, damage mult, chance, flags, strike angle, stagger, attack type, stamina).
_HUMANOID_ATTACKS = (
    ('attackPowerStartDualWield', 1.5, 1.0, 4, 35.0, 1.0, 0x914E5, 0.5),
    ('attackStartSprintLeftHand', 1.0, 0.0, 8, 35.0, 0.0, 0, 1.0),
    ('attackPowerStartForwardLeftHand', 2.0, 1.0, 12, 35.0, 1.0, 0x914E6, 1.0),
    ('attackStartLeftHand', 1.0, 1.0, 8, 35.0, 0.0, 0, 1.0),
    ('attackPowerStartForwardH2HRightHand', 2.0, 1.0, 4, 35.0, 0.5, 0x914E6, 1.0),
    ('attackPowerStartInPlace', 2.0, 0.2, 4, 35.0, 1.0, 0x914E5, 1.0),
    ('attackPowerStartLeftLeftHand', 2.0, 1.0, 12, 35.0, 1.0, 0x914E7, 1.0),
    ('attackPowerStartBackward', 2.0, 0.1, 4, 35.0, 1.0, 0x914E8, 1.0),
    ('attackPowerStartForwardH2HLeftHand', 2.0, 1.0, 12, 35.0, 0.5, 0x914E6, 1.0),
    ('attackPowerStart_2HMSprint', 2.0, 0.0, 4, 35.0, 1.0, 0x914E6, 1.0),
    ('AttackStartH2HLeft', 1.0, 1.0, 8, 35.0, 0.0, 0, 1.0),
    ('attackPowerStartInPlaceLeftHand', 2.0, 1.0, 12, 35.0, 1.0, 0x914E5, 1.0),
    ('attackPowerStartForward', 2.0, 0.5, 4, 35.0, 1.0, 0x914E6, 1.0),
    ('attackPowerStart_Sprint', 2.0, 0.0, 4, 35.0, 1.0, 0x914E6, 1.0),
    ('bashPowerStart', 1.5, 0.33, 6, 35.0, 1.5, 0xF54, 1.0),
    ('attackPowerStartBackLeftHand', 2.0, 1.0, 12, 35.0, 1.0, 0x914E8, 1.0),
    ('attackPowerStart_2HWSprint', 2.0, 0.0, 4, 35.0, 1.0, 0x914E6, 1.0),
    ('attackPowerStart_SprintLeftHand', 2.0, 0.0, 12, 35.0, 1.0, 0x914E6, 1.0),
    ('attackStartSprint', 1.0, 0.0, 0, 35.0, 0.0, 0, 1.0),
    ('attackPowerStartH2HCombo', 1.0, 1.0, 4, 35.0, 0.0, 0x914E5, 1.0),
    ('attackStart', 1.0, 1.0, 0, 50.0, 0.0, 0, 1.0),
    ('AttackStartH2HRight', 1.0, 1.0, 0, 35.0, 0.0, 0, 1.0),
    ('attackPowerStartRight', 2.0, 0.2, 4, 35.0, 1.0, 0x914E7, 1.0),
    ('bashStart', 0.5, 0.5, 2, 35.0, 0.5, 0, 1.0),
    ('attackPowerStartRightLeftHand', 2.0, 1.0, 12, 35.0, 1.0, 0x914E7, 1.0),
    ('attackStartDualWield', 1.0, 1.0, 0, 35.0, 0.0, 0, 1.0),
    ('attackPowerStartLeft', 2.0, 0.2, 4, 35.0, 1.0, 0x914E7, 1.0),
)

#: Each humanoid attack as (event, 44-byte ATKD casting no spell).
HUMANOID_ATTACKS = tuple(
    (event, struct.pack('<ffIIfffIfff', mult, chance, 0, flags, 0.0, strike, stagger, kind, 0.0, 0.0, stamina))
    for event, mult, chance, flags, strike, stagger, kind, stamina in _HUMANOID_ATTACKS)


def load_contact_settings(by_type: dict, master_export) -> None:
    """Read the chain's TES4 disease transfer GMSTs, the plugin's own winning."""
    _tes4_settings.clear()
    _tes4_settings.update(_TES4_TRANSFER_DEFAULTS)
    for rec in [*values_of(master_export, 'GMST'), *by_type.get('GMST', [])]:
        name = get_str(rec, 'EditorID')
        if name in _tes4_settings:
            _tes4_settings[name] = float(get_str(rec, 'DATA.Value'))


def contact_chance() -> float:
    """Percent chance a hit passes each disease on, before Skyrim's own Disease Resist roll; 0 where none.

    TES3 rolls fDiseaseXferChance (OpenMW diseaseContact); TES4 rolls fMagicDiseaseTransferMult x 100
    + fMagicDiseaseTransferBase (Oblivion.exe 0x5490c0). Both scale by the victim's resist as Skyrim does.
    """
    if is_fallout_source():
        return 0.0
    if is_tes3_source():
        return tes3_gmst('fdiseasexferchance', TES3_CONTACT_CHANCE)
    mult, base = (_tes4_settings.get(name, value) for name, value in _TES4_TRANSFER_DEFAULTS.items())
    return 100.0 * mult + base


def contact_effect(fid: int, disease: str, index: int, gate: int, writer) -> int:
    """Effect ``index`` of the disease spell ``disease``: a clone of ``fid`` whose condition is the contraction.

    The first effect rolls contact_chance() once, when it lands; each later one lands only on an
    actor the first (``gate``) is on, so the disease comes whole. ``fid`` itself where no chance applies.
    """
    chance = contact_chance()
    if not chance:
        return fid
    ctda = (build_ctda(_GET_RANDOM_PERCENT, comp_value=chance, operator=_OP_LE) if index == 0
            else build_ctda(_HAS_MAGIC_EFFECT, param1=gate))
    name = ''.join(part.capitalize() for part in disease.replace('-', ' ').split() if part.isalnum())
    return clone(fid, 'MGEF_DISEASE', (disease.lower(), index), f'TES4Disease{name}{index}',
                 _as_disease, writer, conditions=pack_subrecord('CTDA', ctda)) or fid


def _as_disease(data: bytearray) -> None:
    """Give the effect vanilla's disease flags and skill."""
    struct.pack_into('<I', data, O_FLAGS, _DISEASE_FLAGS)
    struct.pack_into('<i', data, O_MAGIC_SKILL, _DISEASE_SKILL)


def actor_diseases(rec: dict, spell_record) -> list:
    """The output FormIDs of the diseases in the actor's spell list; [] where no chance applies."""
    if not contact_chance():
        return []
    fids = [get_formid(rec, f'Spell[{i}]') for i in range(get_int(rec, 'SpellCount'))]
    return [fid for fid in fids
            if fid and get_int(spell_record(fid) or {}, 'SPIT.Type', -1) == SPELL_TYPE_DISEASE]


def attack_subs(race: int, attacks, spells: list) -> bytes:
    """ATKR + one override per race attack, each casting the next of ``spells`` or its own spell in turn.

    ``attacks`` is the race's (event, ATKD) list; b'' when ``spells`` is empty.
    """
    if not spells or not attacks:
        return b''
    subs = pack_formid_subrecord('ATKR', race)
    for i, (event, atkd) in enumerate(attacks):
        own = struct.unpack_from('<I', atkd, 8)[0]
        turn = spells + ([own] if own else [])
        data = bytearray(atkd)
        struct.pack_into('<II', data, 8, turn[i % len(turn)], struct.unpack_from('<I', atkd, 12)[0] | OVERRIDE_DATA)
        subs += pack_subrecord('ATKD', bytes(data)) + pack_string_subrecord('ATKE', event)
    return subs

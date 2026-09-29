"""Quest VMAD property binding: SCRO-sourced reference redirect.

A `wants_placed_reference` property (Actor / ObjectReference / generated
`TES4_*` script class) that enters a QUST only through its SCRO table -- never
through a result-script body -- must still be redirected from the actor BASE to
its placed ACHR/ACRE.  The result-script pass never registers such a name
(`SetQuestObject Hridi 1` emits a comment and binds no ref property), so the
redirect landed nowhere and the property read None all session, aborting the
first quest fragment that method-called it (TG08 Hridi/Hjar/Holger; the
79-line actor tail of the quest property-binding class).
"""

from script_convert.cross_ref import CrossRefGraph
from tes5_import.dialogue.quest import (
    _bind_placed_references,
    _placed_ref_carrying_script,
    _quest_vmad_properties,
)


def _hridi_xref():
    """NPC_ base Hridi with exactly one placed ACHR carrying HridiScript."""
    x = CrossRefGraph()
    x.formid_to_edid['000982FD'] = 'Hridi'
    x.edid_to_formid['hridi'] = '000982FD'
    x.record_type['000982FD'] = 'NPC_'
    x.record_scri['000982FD'] = '000C0001'
    x.script_formid_to_edid['000C0001'] = 'HridiScript'
    x.script_formid_to_type['000C0001'] = 1
    # The single placed reference of the base.
    x.record_base['00098331'] = '000982FD'
    x.record_type['00098331'] = 'ACHR'
    return x


def test_scro_only_actor_redirects_base_to_placed_ref():
    """The end-to-end path: a SCRO-only actor absent from `declared` is still
    redirected. Fails on the pre-fix code (declared-only iteration)."""
    x = _hridi_xref()
    rec = {'SCRO[0]': '000982FD', 'StageCount': '0'}
    fid_to_edid = {0x000982FD: 'Hridi'}
    prop_vals = _quest_vmad_properties(
        rec, 'TG08Blind', fid_to_edid, well_known_props={},
        unlock_plan=None, unlock_globals=None, xref=x)
    # Offset is 0 in unit context, so remapped == raw.
    assert prop_vals['Hridi'] == 0x00098331, (
        'Hridi must bind its placed ACHR, not the base NPC_')


def test_declared_actor_still_redirects():
    """Control: the existing declared (result-script) redirect is unchanged."""
    x = _hridi_xref()
    prop_vals = {'Hridi': 0x000982FD}
    _bind_placed_references(prop_vals, {'Hridi': 'TES4_HridiScript'}, x)
    assert prop_vals['Hridi'] == 0x00098331


def test_disambiguate_multi_placement_by_carried_script():
    """>1 placements: redirect to the ONE ref that carries the declared script."""
    x = CrossRefGraph()
    x.formid_to_edid['00001000'] = 'Guard'
    x.edid_to_formid['guard'] = '00001000'
    x.record_type['00001000'] = 'NPC_'
    # Two placements; only the second carries its own scripted ACHR.
    x.record_base['00002000'] = '00001000'
    x.record_base['00002001'] = '00001000'
    x.record_scri['00002001'] = '000C0002'
    x.script_formid_to_edid['000C0002'] = 'GuardScript'
    assert _placed_ref_carrying_script(x, '00001000', 'TES4_GuardScript') \
        == '00002001'
    prop_vals = {'Guard': 0x00001000}
    _bind_placed_references(prop_vals, {'Guard': 'TES4_GuardScript'}, x)
    assert prop_vals['Guard'] == 0x00002001


def test_identical_copies_are_residue_not_mistyped():
    """Identical copies sharing a BASE script (no own SCRI) stay as-is: the
    ambiguous base bind is left untouched, counted as residue -- never guessed."""
    x = CrossRefGraph()
    x.formid_to_edid['00003000'] = 'Sheep'
    x.edid_to_formid['sheep'] = '00003000'
    x.record_type['00003000'] = 'CREA'
    x.record_scri['00003000'] = '000C0003'  # script on the BASE
    x.script_formid_to_edid['000C0003'] = 'SheepScript'
    for ref in ('00004000', '00004001', '00004002'):
        x.record_base[ref] = '00003000'      # copies, none with own SCRI
    assert _placed_ref_carrying_script(x, '00003000', 'TES4_SheepScript') == ''
    prop_vals = {'Sheep': 0x00003000}
    _bind_placed_references(prop_vals, {'Sheep': 'TES4_SheepScript'}, x)
    assert prop_vals['Sheep'] == 0x00003000  # unchanged (residue)

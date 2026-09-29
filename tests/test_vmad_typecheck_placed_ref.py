"""vmad_property_typecheck must catch the base-vs-reference class.

A VMAD property typed as a converted script that `extends ObjectReference` or
`extends Actor` must bind a placed reference (REFR/ACHR/ACRE).  Before this
guard the cross-master pass returned () for every script type -- "any record
that exists" -- so a script property bound to a BASE record (item base or actor
BASE) passed silently, and the whole 276-line quest property-binding class was
invisible to the tool (INV_propbind.md).
"""

from tools.validate.vmad_property_typecheck import (
    _accepted,
    _binding_problem,
    _PLACED_SIGS,
)

_SCRIPT_TYPES = {'tes4_hridiscript', 'tes4_jearlsordersscript',
                 'tes4_myquestscript'}
_PARENTS = {
    'tes4_hridiscript': 'Actor',
    'tes4_jearlsordersscript': 'ObjectReference',
    'tes4_myquestscript': 'Quest',
}


def test_reference_script_requires_placed_ref():
    # Actor/ObjectReference-extending scripts must bind a placed reference.
    assert _accepted('TES4_HridiScript', _SCRIPT_TYPES, _PARENTS) == _PLACED_SIGS
    assert _accepted('TES4_JearlsOrdersScript', _SCRIPT_TYPES, _PARENTS) \
        == _PLACED_SIGS


def test_quest_script_still_accepts_any_record():
    # A Quest-extending script binds its own base; existence is all that matters.
    assert _accepted('TES4_MyQuestScript', _SCRIPT_TYPES, _PARENTS) == ()


def test_base_bind_is_flagged_for_reference_script():
    # An actor BASE (NPC_) bound to an Actor-script property is a failure...
    accepts = _accepted('TES4_HridiScript', _SCRIPT_TYPES, _PARENTS)
    problem = _binding_problem([('NPC_', 'Hridi')], accepts)
    assert problem == ('NPC_', 'Hridi')
    # ...an item base (BOOK) bound to an ObjectReference-script property too.
    accepts = _accepted('TES4_JearlsOrdersScript', _SCRIPT_TYPES, _PARENTS)
    assert _binding_problem([('BOOK', 'MQ07BrumaEnemyPlans')], accepts) \
        == ('BOOK', 'MQ07BrumaEnemyPlans')


def test_placed_ref_bind_passes():
    # The same property bound to a placed ACHR is fine.
    accepts = _accepted('TES4_HridiScript', _SCRIPT_TYPES, _PARENTS)
    assert _binding_problem([('ACHR', 'HridiRef')], accepts) is None


def test_old_behaviour_would_not_flag():
    """Fail-on-old control: without the parents map (the pre-fix call), a script
    type accepts any record, so the base bind is NOT flagged."""
    accepts = _accepted('TES4_HridiScript', _SCRIPT_TYPES)  # no parents
    assert accepts == ()
    assert _binding_problem([('NPC_', 'Hridi')], accepts) is None

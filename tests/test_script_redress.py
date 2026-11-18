"""A run of item calls that moves armor on an NPC re-dresses it once (Baurus, MQ05 / MQ06).

See: docs/commentary/script_convert.md#redress-after-inventory-change
"""
from script_convert.redress import armor_names, redress_runs

ARMORS = armor_names({'BladesCuirass': 'Armor', 'BladesGreaves': 'Armor', 'Gold001': 'MiscObject'})


def test_a_run_on_one_actor_redresses_once_after_it():
    """MQ05 stage 20's six removals end in one Redress, before the next statement."""
    lines = ['  BaurusRef.RemoveItem(BladesCuirass, 1)', '  BaurusRef.RemoveItem(BladesGreaves, 1)',
             '  BaurusRef.SetFactionRank(Blades, 1)']
    assert redress_runs(lines, ARMORS) == [lines[0], lines[1], '  TES4Polyfill.Redress(BaurusRef)', lines[2]]


def test_the_player_and_non_armor_items_are_left_alone():
    """TES4 never re-dressed the player, and gold changes no clothes."""
    lines = ['  Game.GetPlayer().AddItem(BladesCuirass, 1)', '  AmuseiRef.AddItem(Gold001, 4)']
    assert redress_runs(lines, ARMORS) == lines


def test_remove_all_items_redresses_inside_its_block():
    """MQ15 strips Raven Camoran: one Redress, at the block's indent."""
    lines = ['  If x', '    MQ15RavenRef.RemoveAllItems()', '    MQ15RavenRef.AddItem(Gold001, 1)', '  EndIf']
    assert redress_runs(lines, ARMORS)[3] == '    TES4Polyfill.Redress(MQ15RavenRef)'

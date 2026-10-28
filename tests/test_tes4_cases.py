"""The harness's half of a TES4 quest run: names, console lines and judged conditions."""
import pytest

from tools.live import tes4_cases


class FakeBridge:
    """Answers console commands from a table, recording what it was sent."""

    def __init__(self, answers):
        """`answers`: {command text: output}."""
        self.answers = answers
        self.sent = []

    def console(self, command, ref=None):
        """The table's output for `command`, with `ref` noted."""
        self.sent.append((command, ref))
        return self.answers.get(command, 'Script command "%s" not found.' % command.split()[0])


def names():
    """An index of a quest, a unique NPC placed once, a ref and an item."""
    index = tes4_cases.Names()
    index.ids = {'baurus': 0x05000202, 'baurusref': 0x05000303,
                 'cell_door': 0x05000404, 'amuletofkings': 0x05000505, 'guard': 0x05000606}
    index.quests = {'tutorial': 'Tutorial', 'tes4mq00': 'TES4MQ00'}
    index.placed = {0x05000202: [0x05000303], 0x05000606: [0x05000707, 0x05000808]}
    index.tes4_slots = [5]
    return index


def test_a_target_is_a_reference_or_the_single_placement_of_a_base():
    """talk/activate/take take a ref EditorID, a uniquely placed base, or a TES4 FormID."""
    index = names()
    assert index.target('Cell_Door') == '05000404'
    assert index.target('Baurus') == '05000303'
    assert index.target('0001a2b3') == '0501A2B3'
    with pytest.raises(SystemExit, match='placed 2 times'):
        index.target('Guard')
    with pytest.raises(SystemExit, match='no converted record'):
        index.target('Nobody')


def test_prepare_rewrites_only_the_step_targets(tmp_path):
    """A case file's talk/activate/take targets become hex; bodies keep their names."""
    cases = tmp_path / 'in.tes4test'
    cases.write_text('case c\ntalk Baurus\nrun\nplayer.moveto Cell_Door\nactivate "Cell_Door"\nend\n')
    out = tes4_cases.prepare(str(cases), names(), str(tmp_path / 'out.tes4test'))
    assert open(out).read() == ('case c\ntalk 05000303\nrun\nplayer.moveto Cell_Door\n'
                                'activate 05000404\nend\n')


def test_walk_and_hear_name_references_too():
    """A walk to a reference and a hear's speaker become hex; a point and `any` stay."""
    index = names()
    assert tes4_cases.prepared_line('walk Cell_Door\n', index) == 'walk 05000404\n'
    assert tes4_cases.prepared_line('walk 100 -20.5 3\n', index) == 'walk 100 -20.5 3\n'
    assert tes4_cases.prepared_line('hear 300 Baurus Stay close\n', index) == 'hear 300 05000303 Stay close\n'
    assert tes4_cases.prepared_line('hear 300 any\n', index) == 'hear 300 any\n'
    assert index.name_of('05000303') == 'baurusref'


def test_a_console_line_targets_its_reference_and_resolves_its_arguments():
    """`<name>.cmd` runs on that ref; arguments that are names become hex, a quest keeps its name."""
    assert tes4_cases.resolved('BaurusRef.GetDead', names()) == ('05000303', 'GetDead')
    assert tes4_cases.resolved('player.GetItemCount AmuletOfKings', names()) == (
        None, 'player.GetItemCount 05000505')
    assert tes4_cases.resolved('GetStage Tutorial', names()) == (None, 'GetStage Tutorial')
    assert tes4_cases.resolved('GetStage MQ00', names()) == (None, 'GetStage TES4MQ00')
    assert tes4_cases.resolved('player.moveto 00097A3B', names()) == (None, 'player.moveto 05097A3B')
    assert tes4_cases.resolved('GetStageDone Tutorial 10', names()) == (None, 'GetStageDone Tutorial 10')


def test_judge_reads_the_printed_value_and_compares():
    """A condition function's `>> value` is compared; a rejected command does not evaluate."""
    bridge = FakeBridge({'GetStage Tutorial': 'GetStage >> 10.00'})
    assert tes4_cases.judge(bridge, 'GetStage Tutorial >= 10', names()) == '1 10'
    assert tes4_cases.judge(bridge, 'GetStage Tutorial == 20', names()) == '0 10'
    assert tes4_cases.judge(bridge, 'GetBogus Tutorial == 1', names()) == '-1 null'


def test_judge_reads_a_quest_variable_through_sqv():
    """`Quest.var` reads the quest script's variable, a Papyrus bool as 0 or 1."""
    bridge = FakeBridge({'sqv Tutorial': 'Current stage: 10\n::DoorOpen_var = True\n::Count_var = 3\n'})
    assert tes4_cases.judge(bridge, 'Tutorial.DoorOpen', names()) == '1 1'
    assert tes4_cases.judge(bridge, 'Tutorial.Count == 3', names()) == '1 3'
    assert tes4_cases.judge(bridge, 'Tutorial.Missing == 3', names()) == '-1 null'


def test_run_body_stops_at_the_first_rejected_line():
    """A run answers 1, or 0 with the failing line and what the console printed."""
    bridge = FakeBridge({'player.moveto 05000404': ''})
    assert tes4_cases.run_body(bridge, 'player.moveto Cell_Door\n', names()) == '1'
    reply = tes4_cases.run_body(bridge, 'player.moveto Cell_Door\nbogus thing\n', names())
    assert reply.startswith('0 "bogus thing: Script command')

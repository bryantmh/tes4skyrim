"""A converted movie plays after the rest of its block, as TES4's queued PlayBink did."""
from script_convert.cross_ref import CrossRefGraph
from script_convert.movie_last import movie_last
from script_convert.quest_fragments import quest_fragment_psc


def test_a_stage_result_sets_its_timer_before_the_movie():
    """MQ16 stage 65: the timer write after `playbink` runs first, the movie last.

    See: docs/commentary/script_convert.md#playbink-halts-the-game
    """
    rec = {'StageCount': '1', 'Stage[0].Index': '65', 'Stage[0].LogCount': '1',
           'Stage[0].Log[0].ResultScript': 'playbink "OblivionOutro.bik"\nset fQuestDelayTime to 1\n'}
    frags = [(65, 0, '', rec['Stage[0].Log[0].ResultScript'], False, 0, 0)]
    _name, src = quest_fragment_psc(rec, 'MQ16', CrossRefGraph(), frags, {})
    body = src.split('Fragment_Stage_0065_Item_0()')[1].split('EndFunction')[0]
    code = [line.strip() for line in body.split('\n') if line.strip()]
    assert code[-1].startswith('Game.PlayBink("OblivionOutro.bik"')
    assert len(code) > 1


def test_a_movie_before_a_return_or_inside_a_branch_stays():
    """A return after the movie, or a movie inside an If, keeps its place."""
    returning = ['Function F()', '  Game.PlayBink("a.bik")', '  If x', '    Return', '  EndIf', 'EndFunction']
    branched = ['Function F()', '  If x', '    Game.PlayBink("a.bik")', '  EndIf', '  y = 1', 'EndFunction']
    assert movie_last(returning) == returning
    assert movie_last(branched) == branched


def test_a_movie_in_a_function_moves_before_its_end():
    """The movie lands on the block's last line, inside the function."""
    lines = ['Function F()', '  Game.PlayBink("a.bik")', '  y = 1', 'EndFunction', 'Function G()', 'EndFunction']
    assert movie_last(lines) == ['Function F()', '  y = 1', '  Game.PlayBink("a.bik")', 'EndFunction',
                                 'Function G()', 'EndFunction']

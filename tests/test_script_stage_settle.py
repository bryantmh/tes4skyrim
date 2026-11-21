"""A quest's own poll never reads a stage whose fragment is still running (MQ16 stage 65's timer)."""
from script_convert.converter import ScriptConverter
from script_convert.cross_ref import CrossRefGraph
from script_convert.quest_fragments import quest_fragment_psc

QUEST_SCRIPT = """Scriptname MQXScript
float timer
begin gamemode
if getstage MQX == 65 && timer <= 0
    setstage MQX 80
endif
end
"""


def _graph() -> CrossRefGraph:
    """A graph where quest MQX (own script MQXScript) has fragments on stages 65 and 80."""
    xref = CrossRefGraph()
    xref.edid_to_formid['mqx'] = '00000100'
    xref.record_scri['00000100'] = '00000200'
    xref.script_formid_to_edid['00000200'] = 'MQXScript'
    xref.own_scripts = {'tes4_mqxscript'}
    xref.fragment_stages['mqx'] = {65, 80}
    return xref


def test_set_stage_marks_the_fragment_and_the_poll_waits_for_it():
    """TES4SetStage notes stage 65 or 80 as settling; the poll skips its pass meanwhile.

    See: docs/commentary/script_convert.md#a-stage-fragment-runs-before-its-poll
    """
    psc = ScriptConverter(_graph()).convert_standalone('MQXScript', QUEST_SCRIPT, 'Quest', 'MQXScript')
    assert '  If aiStage == 65 || aiStage == 80' in psc
    assert 'akQuest.TES4StagePending(aiStage)' in psc
    assert 'TES4_settling >= 0 && TES4_passNow - TES4_settlingAt < 1.0' in psc


def test_the_stage_fragment_ends_by_clearing_its_mark():
    """Stage 65's fragment ends with the quest script's TES4StageRan(65)."""
    rec = {'StageCount': '1', 'Stage[0].Index': '65', 'Stage[0].LogCount': '1',
           'Stage[0].Log[0].ResultScript': 'set MQX.timer to 15\n'}
    frags = [(65, 0, '', rec['Stage[0].Log[0].ResultScript'], False, 0, 0)]
    _name, src = quest_fragment_psc(rec, 'MQX', _graph(), frags, {})
    body = src.split('Fragment_Stage_0065_Item_0()')[1].split('EndFunction')[0]
    assert body.strip().split('\n')[-1].strip() == '((Self as Quest) as TES4_MQXScript).TES4StageRan(65)'


def test_a_pass_skipped_for_the_fragment_rearms_at_the_poll_interval():
    """The skip re-arms the poll's own interval; only the 5 s insurance arm left the line's timer to start late.

    See: docs/commentary/script_convert.md#a-stage-fragment-runs-before-its-poll
    """
    psc = ScriptConverter(_graph()).convert_standalone('MQXScript', QUEST_SCRIPT, 'Quest', 'MQXScript')
    lines = [line.strip() for line in psc.split('\n')]
    skip = next(i for i, line in enumerate(lines) if 'TES4_settling >= 0' in line)
    end = lines.index('EndEvent', skip)
    interval = next(line for line in reversed(lines[:end]) if line.startswith('RegisterForSingleUpdate('))
    assert interval != 'RegisterForSingleUpdate(5.0)'
    assert lines[skip + 1:skip + 3] == [interval, 'Return']
    running = next(i for i, line in enumerate(lines) if line.startswith('If TES4_PassAt > 0.0'))
    assert lines[running + 1] == 'Return'


def test_a_finished_fragment_restarts_the_polls_seconds():
    """A poll counting real seconds restarts its count when the stage fragment ends; one without a count does not.

    See: docs/commentary/script_convert.md#a-stage-fragment-runs-before-its-poll
    """
    timed = QUEST_SCRIPT.replace('begin gamemode\n', 'begin gamemode\nset timer to timer - getsecondspassed\n')
    clocked = ScriptConverter(_graph()).convert_standalone('MQXScript', timed, 'Quest', 'MQXScript')
    plain = ScriptConverter(_graph()).convert_standalone('MQXScript', QUEST_SCRIPT, 'Quest', 'MQXScript')
    ran = clocked.split('Function TES4StageRan')[1].split('EndFunction')[0]
    assert 'TES4_LastTick = Utility.GetCurrentRealTime()' in ran
    assert 'TES4_LastTick' not in plain.split('Function TES4StageRan')[1].split('EndFunction')[0]

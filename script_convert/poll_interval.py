"""The interval a converted GameMode poll re-arms at.

An authored quest delay (FO3/FNV `DATA.Delay`) wins.  A TES4 quest script
runs at its own `fQuestDelayTime`, 5 s while that is 0; every other script
follows what its body does with time.

See: docs/commentary/script_convert.md#poll-interval
"""

#: Fastest Papyrus poll the converter emits; an authored delay never goes below it.
MIN_INTERVAL = 0.1

#: TES4's quest-script cadence while `fQuestDelayTime` is 0 (the default).
QUEST_DEFAULT_INTERVAL = 5.0

#: The poll interval of a quest script that declares `fQuestDelayTime`.
QUEST_DELAY_CALL = 'TES4_QuestDelay()'


def update_interval(sc, extends: str = '') -> str:
    """The RegisterForSingleUpdate argument for one script's poll.

    Oblivion ran quest scripts every 5 s unless the script set
    fQuestDelayTime; polling them at the body-derived 0.1-0.5 s ran the
    start-game quests about 14x as often and starved the VM.
    """
    if sc.quest_delay > 0:
        return float_literal(max(sc.quest_delay, MIN_INTERVAL))
    if extends == 'Quest':
        return (QUEST_DELAY_CALL if sc.declares_quest_delay
                else float_literal(QUEST_DEFAULT_INTERVAL))
    return active_interval(sc)


def active_interval(sc) -> str:
    """The fine poll interval the body's own work wants, ignoring the Quest 5 s cap.

    This is what a NON-quest script polls at, and what a Quest re-arms to WHILE a
    countdown timer is running (assemble._adaptive_arm): the interval its body was
    written to run at, capped at MIN_INTERVAL.
    """
    if sc.uses_getsecondspassed or sc.moves_in_poll:
        return '0.1'
    if sc.uses_say_timer:
        return '0.15'
    if sc.uses_timer:
        return '0.25'
    return '0.5'


def interval_literal(interval: str) -> str:
    """`interval` where Papyrus needs a constant (a variable initializer)."""
    return (float_literal(QUEST_DEFAULT_INTERVAL)
            if interval == QUEST_DELAY_CALL else interval)


def quest_delay_helper() -> list:
    """TES4_QuestDelay(): the script's own fQuestDelayTime as a poll interval."""
    return ['Float Function TES4_QuestDelay()',
            '  ; TES4: 0 means the default cadence',
            '  If fQuestDelayTime <= 0.0',
            f'    Return {float_literal(QUEST_DEFAULT_INTERVAL)}',
            f'  ElseIf fQuestDelayTime < {float_literal(MIN_INTERVAL)}',
            f'    Return {float_literal(MIN_INTERVAL)}',
            '  EndIf',
            '  Return fQuestDelayTime',
            'EndFunction',
            '']


def float_literal(value: float) -> str:
    """A Papyrus Float literal: never an integer spelling, which the checker rejects."""
    text = f'{value:g}'
    return text if ('.' in text or 'e' in text) else text + '.0'


def quest_script_delays(by_type: dict) -> dict:
    """SCPT FormID string -> authored quest delay in seconds, where one is written."""
    out = {}
    for rec in by_type.get('QUST', []):
        delay = float(rec.get('DATA.Delay') or 0)
        scri = rec.get('SCRI') or ''
        if delay > 0 and scri:
            out[scri.upper()] = delay
    return out

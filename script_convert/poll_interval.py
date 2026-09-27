"""The interval a converted GameMode poll re-arms at.

An authored quest delay (FO3/FNV `DATA.Delay`) wins; otherwise the interval
follows what the script's body does with time.

See: docs/commentary/script_convert.md#poll-interval
"""

#: Fastest Papyrus poll the converter emits; an authored delay never goes below it.
MIN_INTERVAL = 0.1


def update_interval(sc) -> str:
    """The RegisterForSingleUpdate literal for one script's poll."""
    if sc.quest_delay > 0:
        return float_literal(max(sc.quest_delay, MIN_INTERVAL))
    if sc.uses_getsecondspassed or sc.moves_in_poll:
        return '0.1'
    if sc.uses_say_timer:
        return '0.15'
    if sc.uses_timer:
        return '0.25'
    return '0.5'


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

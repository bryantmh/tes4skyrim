"""Papyrus events for a converted `begin OnTrigger` body.

See: docs/commentary/script_convert.md#one-trigger-at-a-time
"""


def trigger_events(opener: str, closer: str, body: list, authored_entry: bool) -> list:
    """The events that run a merged OnTrigger body every frame, as TES4 did.

    See: docs/commentary/script_convert.md#one-trigger-at-a-time
    """
    return _one_trigger_at_a_time(opener, closer, body) + ([] if authored_entry else _entry_delegates())


def _one_trigger_at_a_time(opener: str, closer: str, body: list) -> list:
    """OnTrigger that skips an event while the previous one still runs.

    The body is a function so a TES4 `return` inside it still clears the busy flag.
    See: docs/commentary/script_convert.md#one-trigger-at-a-time
    """
    return (['Bool TES4_TriggerBusy = False', '', opener,
             '  If TES4_TriggerBusy', '    Return', '  EndIf',
             '  TES4_TriggerBusy = True', '  TES4_OnTriggerBody(akActionRef)',
             '  TES4_TriggerBusy = False', closer, '',
             'Function TES4_OnTriggerBody(ObjectReference akActionRef)']
            + body + ['EndFunction', ''])


def _entry_delegates() -> list:
    """OnTriggerEnter running the OnTrigger body: Skyrim sends no OnTrigger for a fast crossing."""
    return ['Event OnTriggerEnter(ObjectReference akActionRef)',
            '  OnTrigger(akActionRef)',
            'EndEvent', '']

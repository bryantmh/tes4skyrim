"""A run of writes into another script lands as one step, and that script's poll never reads half of it."""
import pytest

from script_convert.converter import ScriptConverter
from script_convert.cross_ref import CrossRefGraph

OWNER = """Scriptname Q
short n
ref target
begin gamemode
if target != 0
    set n to 2
endif
end
"""

WRITER = """Scriptname W
begin OnDeath
set Q.n to 1
set Q.done to 1
end
"""


@pytest.fixture
def converter():
    """A converter whose graph knows Q as a script another script adds to (so Q carries the hold pair)."""
    xref = CrossRefGraph()
    xref.script_formid_to_edid['00001111'] = 'Q'
    xref.remote_adds.add(('q', 'n'))
    return ScriptConverter(xref)


def test_a_run_of_writes_into_a_holding_script_is_held(converter):
    """The skeleton's OnDeath shape (MQ09GhostBladeSpawn01): its writes into Q sit between Q's hold and release."""
    out = converter.convert_standalone('W', WRITER, 'Actor', 'W').split('\n')
    lines = [line.strip() for line in out]
    hold, release = lines.index('Q.TES4Hold()'), lines.index('Q.TES4Release()')
    assert [line for line in lines[hold + 1:release] if line] == ['Q.n = 1', 'Q.done = 1']


def test_the_holding_scripts_poll_skips_a_pass_while_a_fresh_hold_is_taken(converter):
    """Q's poll returns before reading anything while a hold under a second old is taken."""
    out = converter.convert_standalone('Q', OWNER, 'Quest', 'Q')
    body = out.split('Event OnUpdate()')[1]
    guard = body.index('TES4_held && TES4_passNow - TES4_heldAt < 1.0')
    assert guard < body.index('Return') < body.index('target')
    assert 'TES4_heldAt = Utility.GetCurrentRealTime()' in out

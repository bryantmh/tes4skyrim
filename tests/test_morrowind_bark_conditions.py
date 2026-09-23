"""
Morrowind bark rules beyond the speaker: each becomes a condition, or the line
goes. Records are built by hand, so no Morrowind install is needed.

See: docs/commentary/tes4_export_morrowind.md#bark-conditions
"""

import collections
import struct

from tes4_export.record_types.morrowind_dialog import dialogue_records
from tes5_import.base.conditions import convert_ctda_list_with_strings
from tests.test_morrowind_dialog import _Ctx, _cstr, _dial, _kv, _sub, _voiced

#: The TES3 functions and TES4/TES5 condition indices the tests read back.
_GET_DISEASE = 39
_GET_FACTION_RANK = 73
_GET_GLOBAL_VALUE = 74
_GET_SCRIPT_VARIABLE = 53
_GET_CLOTHING_VALUE = 41
_HAS_MAGIC_EFFECT = 214
_PLAYER_REF = 0x14


class _Resolving(_Ctx):
    """A context that resolves the ids a fixture names, and counts drops."""

    def __init__(self, ids: dict):
        """`ids` maps a lowercased TES3 id to the FormID it resolves to."""
        super().__init__()
        self.ids = ids
        self.unresolved = collections.Counter()

    def resolve(self, record_id, signature=''):
        """The FormID a fixture gave this id, else nothing."""
        return self.ids.get(record_id.lower(), '')


def _hello(response: str, rules=(), **kw):
    """A voiced Hello carrying `rules`: (SCVR, INTV value) pairs."""
    bark = _voiced('Hello', 'Vo\\d\\m\\Hlo.mp3', response=response, **kw)
    for rule, value in rules:
        bark.subrecords.append(_sub('SCVR', _cstr(rule)))
        bark.subrecords.append(_sub('INTV', struct.pack('<i', value)))
    return bark


def _export(bark, ctx) -> dict:
    """The one exported bark INFO as KEY=VALUE, or {} when it was dropped."""
    out = dialogue_records([_dial('Hello', 1), bark], ctx)
    return _kv(out['INFO'][0][1]) if out['INFO'] else {}


def _functions(kv: dict) -> list:
    """`(function, param1, value, run_on)` for every exported condition."""
    out = []
    for i in range(int(kv.get('ConditionCount', 0))):
        raw = bytes.fromhex(kv[f'Condition[{i}].Raw'])
        value = struct.unpack_from('<f', raw, 4)[0]
        out.append((raw[8] | raw[9] << 8, struct.unpack_from('<I', raw, 12)[0],
                    value, kv.get(f'Condition[{i}].RunOn', '')))
    return out


def test_a_disease_greeting_asks_whether_the_PLAYER_is_diseased():
    """"Diseased! Go away." played to healthy players when the rule dropped."""
    kv = _export(_hello('Diseased! Go away.', [('01400', 1)]), _Ctx())
    assert (_GET_DISEASE, 0, 1.0, 'Player') in _functions(kv)


def test_a_vampire_greeting_needs_the_vampirism_effect_or_goes():
    """PcVampire is HasMagicEffect(Vampirism) on the player; with no such
    effect in the chain the line can never play, so it is dropped."""
    kv = _export(_hello('Hiss!', [('01600', 1)]),
                 _Resolving({'mw133vampirism': '00ABC133'}))
    assert (_HAS_MAGIC_EFFECT, 0xABC133, 1.0, 'Player') in _functions(kv)
    ctx = _Resolving({})
    assert _export(_hello('Hiss!', [('01600', 1)]), ctx) == {}
    assert ctx.unresolved['bark rule'] == 1


def test_a_guild_rank_greeting_needs_the_players_rank():
    """DNAM + PC rank: the player's rank in that faction, at least the rank."""
    bark = _hello('Greetings, Grandmaster.', pcrank=9)
    bark.subrecords.append(_sub('DNAM', _cstr('Fighters Guild')))
    kv = _export(bark, _Resolving({'fighters guild': '0000FACE'}))
    assert (_GET_FACTION_RANK, 0xFACE, 9.0, 'Player') in _functions(kv)
    assert _export(bark, _Resolving({})) == {}, (
        'a faction nothing defines is one the player cannot hold')


def test_a_journal_rule_reads_a_runtime_published_glob():
    """The journal lives in the runtime; the bark reads the GLOB it writes."""
    ctx = _Resolving({})
    out = dialogue_records(
        [_dial('Hello', 1), _hello('The lands are whole.',
                                   [('04JX3BM_WildHunt', 100)])], ctx)
    glob_id, glob = out['GLOB'][0]
    assert _kv(glob)['MorrowindState'] == 'journal:bm_wildhunt'
    kv = _kv(out['INFO'][0][1])
    assert (_GET_GLOBAL_VALUE, int(glob_id, 16), 100.0, '') in _functions(kv)


def test_not_local_is_the_local_rule_negated():
    """`C` (NotLocal) holds exactly when the local rule does not."""
    kv = _export(_hello('Speak.', [('1CsX3TR_Map', 1)]), _Ctx())
    raw = bytes.fromhex(kv['Condition[0].Raw'])
    assert raw[8] == _GET_SCRIPT_VARIABLE and raw[0] >> 5 == 4, (
        'NOT (TR_Map >= 1) is TR_Map < 1')
    assert kv['Condition[0].Variable'] == 'TR_Map'


def test_only_naked_or_dressed_crosses_from_clothing_value():
    """TES3 sums gold, Skyrim scores 0..100: a gold threshold drops the line."""
    kv = _export(_hello('Cover yourself.', [('01425', 0)]), _Ctx())
    assert (_GET_CLOTHING_VALUE, 0, 0.0, 'Player') in _functions(kv)
    ctx = _Resolving({})
    assert _export(_hello('Fine clothes.', [('01423', 1000)]), ctx) == {}


def test_run_on_player_points_the_condition_at_playerref():
    """`Condition[i].RunOn=Player` becomes Run On Reference, PlayerRef."""
    kv = _export(_hello('Diseased! Go away.', [('01400', 1)]), _Ctx())
    pairs = convert_ctda_list_with_strings(kv, {}, offset=0)
    ctda = next(c for c, _cis2 in pairs if struct.unpack_from('<H', c, 8)[0]
                == _GET_DISEASE)
    run_on, reference = struct.unpack_from('<II', ctda, 20)
    assert (run_on, reference) == (2, _PLAYER_REF)

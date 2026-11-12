"""An amphibian's swim attacks live in its swim state, its land attacks on land.

The graphs are built from folders of empty .kf files, so nothing here needs
game data or hkxcmd: the classifier reads file names, the graph builder clip
names.
See: docs/commentary/asset_convert_creature.md#swim-attacks
"""

import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from asset_convert.havok.behavior_clips import (SWIM_ATTACK_FIRST_ID,
                                                build_attack_events,
                                                classify_clips, state_defs,
                                                swim_attacks)
from asset_convert.havok.behavior_nodes import F_LOCAL, GraphBuilder
from asset_convert.havok.behavior_vocabulary import (graph_events,
                                                     movement_type_names)
from asset_convert.havok.hkx_behavior import build_behavior_xml

LAND = ('idle', 'forward', 'backward', 'turnleft', 'turnright')
SWIM = ('swimforward', 'swimidle', 'swimbackward', 'swimturnleft',
        'swimturnright')
LAND_ATTACKS = ('handtohandattackleft', 'handtohandattackpower')
SWIM_ATTACKS = ('swimhandtohandattackleft', 'swimhandtohandattackright',
                'swimhandtohandattackpower')
AMPHIBIAN = LAND + SWIM + LAND_ATTACKS + SWIM_ATTACKS

TO_ACTION, FROM_ACTION = 'BlendToAnimationDriven', 'BlendFromAnimationDriven'
#: Transition flags as the packfile spells them: local, wildcard, global wildcard.
LOCAL = 'FLAG_DISABLE_CONDITION'
WILD = 'FLAG_IS_LOCAL_WILDCARD|FLAG_DISABLE_CONDITION'
GLOBAL = 'FLAG_IS_LOCAL_WILDCARD|FLAG_IS_GLOBAL_WILDCARD|FLAG_DISABLE_CONDITION'


def _event(stem):
    """The attack event a clip stem gets."""
    return f'attackStart_TES4_{stem}'


def _clips(folder, stems):
    """Classify a folder holding one empty .kf per stem."""
    for stem in stems:
        open(os.path.join(folder, stem + '.kf'), 'wb').close()
    return classify_clips(str(folder))


def _param(body, name):
    """The text of one hkparam of a generated object."""
    m = re.search(rf'<hkparam name="{name}"[^>]*>(.*?)</hkparam>', body, re.S)
    return m.group(1).strip() if m else None


def _count(body):
    """The element count an array object announces."""
    return int(re.search(r'numelements="(\d+)"', body).group(1))


class _Graph:
    """A generated behaviour XML, read back with regular expressions."""

    def __init__(self, xml):
        """Index the objects and the event names."""
        self.objs = {m.group(1): (m.group(2), m.group(3)) for m in re.finditer(
            r'<hkobject name="(#\d+)" class="(\w+)"[^>]*>(.*?)\n\t</hkobject>',
            xml, re.S)}
        strings = next(b for c, b in self.objs.values()
                       if c == 'hkbBehaviorGraphStringData')
        self.events = re.findall(r'<hkcstring>(.*?)</hkcstring>',
                                 _param(strings, 'eventNames'), re.S)

    def _ids(self, ref):
        """Event names of an event array."""
        if ref in (None, 'null'):
            return []
        body = self.objs[ref][1]
        ids = [int(i) for i in re.findall(
            r'<hkparam name="id">(-?\d+)</hkparam>', body)]
        assert len(ids) == _count(body) and min(ids) >= 0
        return [self.events[i] for i in ids]

    def _transitions(self, ref):
        """(event, target state, effect name, flags) of a transition array."""
        if ref in (None, 'null'):
            return []
        body = self.objs[ref][1]
        rows = re.findall(
            r'<hkparam name="transition">(#\d+|null)</hkparam>.*?'
            r'<hkparam name="eventId">(\d+)</hkparam>\s*'
            r'<hkparam name="toStateId">(\d+)</hkparam>.*?'
            r'<hkparam name="flags">(.*?)</hkparam>', body, re.S)
        assert len(rows) == _count(body)
        return [(self.events[int(e)], int(to),
                 _param(self.objs[fx][1], 'name') if fx in self.objs else fx,
                 flags) for fx, e, to, flags in rows]

    def _triggers(self, ref):
        """(event, local time, relative to the clip's end) of a trigger array."""
        if ref in (None, 'null'):
            return []
        body = self.objs[ref][1]
        rows = re.findall(
            r'<hkparam name="localTime">(.*?)</hkparam>.*?'
            r'<hkparam name="id">(\d+)</hkparam>.*?'
            r'<hkparam name="relativeToEndOfClip">(\w+)</hkparam>', body, re.S)
        assert len(rows) == _count(body)
        return [(self.events[int(e)], float(t), rel == 'true')
                for t, e, rel in rows]

    def _generator(self, ref):
        """What a state plays: the wrapper, its modifier, and the clip."""
        kind, body = self.objs[ref]
        out = {'wrapper': None, 'modifier': None, 'clip': None, 'file': None,
               'mode': None, 'triggers': []}
        if kind == 'hkbModifierGenerator':
            out = self._generator(_param(body, 'generator'))
            out['wrapper'] = _param(body, 'name')
            out['modifier'] = _param(self.objs[_param(body, 'modifier')][1],
                                     'name')
        elif kind == 'hkbClipGenerator':
            out['clip'] = _param(body, 'name')
            out['file'] = _param(body, 'animationName')
            out['mode'] = _param(body, 'mode')
            out['triggers'] = self._triggers(_param(body, 'triggers'))
        return out

    def _machine(self, name):
        """The body of the state machine called `name`."""
        return next(b for c, b in self.objs.values()
                    if c == 'hkbStateMachine' and _param(b, 'name') == name)

    def machine(self, name):
        """{state name: state} of one state machine."""
        out = {}
        for ref in _param(self._machine(name), 'states').split():
            st = self.objs[ref][1]
            rows = self._transitions(_param(st, 'transitions'))
            out[_param(st, 'name')] = dict(
                self._generator(_param(st, 'generator')),
                id=int(_param(st, 'stateId')),
                transitions=[row[:3] for row in rows],
                flags={row[3] for row in rows},
                enter=self._ids(_param(st, 'enterNotifyEvents')),
                exit=self._ids(_param(st, 'exitNotifyEvents')))
        return out

    def wildcards(self, name):
        """(event, target state, effect, flags) of a machine's wildcards."""
        return self._transitions(_param(self._machine(name),
                                        'wildcardTransitions'))

    def names(self):
        """The name of every state machine of the graph."""
        return [_param(b, 'name') for c, b in self.objs.values()
                if c == 'hkbStateMachine']

    def start(self, name):
        """The id of the state a machine starts in."""
        return int(_param(self._machine(name), 'startStateId'))

    def effect(self, name):
        """(duration, flags) of the blend effect called `name`."""
        body = next(b for c, b in self.objs.values()
                    if c == 'hkbBlendingTransitionEffect'
                    and _param(b, 'name') == name)
        return float(_param(body, 'duration')), _param(body, 'flags')


def _graph(clips, hit_times=None):
    """The graph of a classified clip set, with a swim movement type."""
    has_swim = bool(clips.get('swim', {}).get('forward'))
    return _Graph(build_behavior_xml(
        'TBehavior', clips, hit_times=hit_times,
        movement_types=movement_type_names('t', has_swim=has_swim),
        speeds={'walk': 100.0, 'swim': 120.0}))


def _leaving_default(graph):
    """The events `DefaultState` answers."""
    return {row[0] for row in
            graph.machine('TBehaviorRoot')['DefaultState']['transitions']}


@pytest.fixture
def amphibian(tmp_path):
    """A walker and swimmer with two land and three swim attacks."""
    return _clips(tmp_path, AMPHIBIAN)


class TestSwimAttackClips:
    def test_the_swim_named_attacks_are_listed_in_clip_order(self, amphibian):
        """The rule returns the three swim-named clips, sorted as the attack list is."""
        assert [os.path.basename(p) for p in swim_attacks(amphibian)] == [
            'swimhandtohandattackleft.kf', 'swimhandtohandattackpower.kf',
            'swimhandtohandattackright.kf']

    def test_every_attack_keeps_its_event(self, amphibian):
        """RACE and the graph still name all five events: the split is in the states."""
        events = build_attack_events(amphibian)
        assert events == [_event(s) for s in sorted(LAND_ATTACKS + SWIM_ATTACKS)]
        assert set(events) <= set(graph_events(amphibian))

    def test_a_swim_attack_clip_ends_on_attack_stop(self, amphibian):
        """The cache row of a swim attack names the trigger its clip fires."""
        ends = {row[0]: row[4] for row in state_defs(amphibian)
                if row[0].startswith('Attack_')}
        assert ends == {
            'Attack_handtohandattackleft': 'returnToDefault',
            'Attack_handtohandattackpower': 'returnToDefault',
            'Attack_swimhandtohandattackleft': 'attackStop',
            'Attack_swimhandtohandattackpower': 'attackStop',
            'Attack_swimhandtohandattackright': 'attackStop'}

    def test_file_names_are_read_without_regard_to_case(self, tmp_path):
        """A mod's `SwimHandToHandAttackLeft.KF` is a swim attack like any other."""
        _clips(tmp_path, LAND + SWIM + LAND_ATTACKS)
        for name in ('SwimHandToHandAttackLeft.KF',
                     'SWIMHANDTOHANDATTACKPOWER.kf'):
            open(os.path.join(tmp_path, name), 'wb').close()
        clips = classify_clips(str(tmp_path))
        assert sorted(os.path.basename(p) for p in swim_attacks(clips)) == [
            'SWIMHANDTOHANDATTACKPOWER.kf', 'SwimHandToHandAttackLeft.KF']

    def test_swim_must_lead_the_name(self, tmp_path):
        """`attackswimleft` only holds the word: it stays a land attack."""
        clips = _clips(tmp_path, AMPHIBIAN + ('attackswimleft',))
        assert len(swim_attacks(clips)) == 3
        assert _event('attackswimleft') in _leaving_default(_graph(clips))

    def test_one_land_attack_is_enough(self, tmp_path):
        """One land attack beside the swim ones: the rule applies."""
        clips = _clips(tmp_path, LAND + SWIM + LAND_ATTACKS[:1] + SWIM_ATTACKS)
        assert len(swim_attacks(clips)) == 3
        assert _leaving_default(_graph(clips)) == {_event(LAND_ATTACKS[0])}


class TestLandSide:
    def test_only_land_attacks_leave_the_default_state(self, amphibian):
        """On land the two land events lead to their own states and no swim event leads anywhere."""
        root = _graph(amphibian).machine('TBehaviorRoot')
        leaving = {e: to for e, to, _fx in root['DefaultState']['transitions']}
        assert set(leaving) == {_event(s) for s in LAND_ATTACKS}
        for stem in LAND_ATTACKS:
            assert leaving[_event(stem)] == root[f'Attack_{stem}State']['id']

    def test_no_root_state_plays_a_swim_attack(self, amphibian):
        """The root holds no swim attack state; SwimState still leaves on swimStop alone."""
        root = _graph(amphibian).machine('TBehaviorRoot')
        assert not [n for n in root if 'swim' in n.lower() and n != 'SwimState']
        assert root['SwimState']['transitions'] == [('swimStop', 0, FROM_ACTION)]

    def test_a_land_attack_still_returns_through_the_root(self, amphibian):
        """A land attack is built as before: returnToDefault at its end, attackStop on exit."""
        state = _graph(amphibian).machine('TBehaviorRoot')[
            'Attack_handtohandattackleftState']
        assert state['triggers'] == [('returnToDefault', 0.0, True)]
        assert state['exit'] == ['attackStop'] and state['enter'] == []
        assert state['transitions'] == []

    def test_the_root_ids_are_distinct_and_the_swim_state_is_reachable(
            self, amphibian):
        """Four root states, four ids, and the swimStart wildcard names SwimState's."""
        graph = _graph(amphibian)
        root = graph.machine('TBehaviorRoot')
        assert sorted(s['id'] for s in root.values()) == list(range(len(root)))
        wild = {row[0]: row for row in graph.wildcards('TBehaviorRoot')}
        assert wild['swimStart'] == ('swimStart', root['SwimState']['id'],
                                     TO_ACTION, WILD)
        assert wild['returnToDefault'][1::2] == (0, GLOBAL)
        assert root['DefaultState']['flags'] == {LOCAL}


class TestSwimSide:
    def test_one_swim_state_per_swim_attack(self, amphibian):
        """Three states after the five gait ids, no id used twice."""
        swim = _graph(amphibian).machine('SwimBehavior')
        ids = {n: s['id'] for n, s in swim.items() if n.startswith('Attack_')}
        assert SWIM_ATTACK_FIRST_ID == 5
        assert ids == {'Attack_swimhandtohandattackleftState': 5,
                       'Attack_swimhandtohandattackpowerState': 6,
                       'Attack_swimhandtohandattackrightState': 7}
        assert sorted(s['id'] for s in swim.values()) == list(range(8))

    def test_the_attack_ends_into_the_swim_idle(self, amphibian):
        """The clip fires attackStop at its end, which leads to state 0 by a local transition."""
        swim = _graph(amphibian).machine('SwimBehavior')
        assert swim['SwimIdleState']['id'] == 0
        for stem in SWIM_ATTACKS:
            state = swim[f'Attack_{stem}State']
            assert state['triggers'] == [('attackStop', 0.0, True)]
            assert state['transitions'] == [('attackStop', 0, FROM_ACTION)]
            assert state['flags'] == {LOCAL}

    def test_an_attack_cut_short_still_clears(self, amphibian):
        """Like a land attack, the state sends attackStop on exit, and nothing on entry."""
        swim = _graph(amphibian).machine('SwimBehavior')
        for stem in SWIM_ATTACKS:
            state = swim[f'Attack_{stem}State']
            assert state['exit'] == ['attackStop'] and state['enter'] == []

    def test_only_the_swim_attacks_answer_attack_stop(self, amphibian):
        """No other state and no wildcard of any machine has a transition on attackStop."""
        graph = _graph(amphibian)
        answering = set()
        for name in graph.names():
            assert not [w for w in graph.wildcards(name) if w[0] == 'attackStop']
            answering |= {(name, st) for st, s in graph.machine(name).items()
                          if any(e == 'attackStop' for e, _to, _fx
                                 in s['transitions'])}
        assert answering == {('SwimBehavior', f'Attack_{s}State')
                             for s in SWIM_ATTACKS}

    def test_only_attack_states_send_a_notify(self, amphibian):
        """The gait states, SwimState and DefaultState send nothing on entry or exit."""
        graph = _graph(amphibian)
        for machine in ('SwimBehavior', 'TBehaviorRoot'):
            states = graph.machine(machine)
            assert not [n for n, s in states.items() if s['enter']]
            assert {n: s['exit'] for n, s in states.items() if s['exit']} == {
                n: ['attackStop'] for n in states if n.startswith('Attack_')}

    def test_the_swim_machine_starts_in_its_idle(self, amphibian):
        """SwimState opens on the swim idle, state 0, not on an attack."""
        assert _graph(amphibian).start('SwimBehavior') == 0

    def test_the_action_blends_are_short_and_keep_one_motion(self, amphibian):
        """0.3 s each; into an attack the attack's motion counts, out of it the attack's still."""
        graph = _graph(amphibian)
        assert graph.effect(TO_ACTION) == (
            0.3, 'FLAG_IGNORE_FROM_WORLD_FROM_MODEL')
        assert graph.effect(FROM_ACTION) == (
            0.3, 'FLAG_IGNORE_TO_WORLD_FROM_MODEL')

    def test_each_state_plays_its_own_clip_once(self, amphibian):
        """Its own animation file, single play, inside the attack modifier list."""
        swim = _graph(amphibian).machine('SwimBehavior')
        for stem in SWIM_ATTACKS:
            state = swim[f'Attack_{stem}State']
            assert state['clip'] == f'Attack_{stem}'
            assert state['file'] == f'Animations\\{stem}.hkx'
            assert state['mode'] == 'MODE_SINGLE_PLAY'
            assert state['wrapper'] == f'Attack_{stem}_MG'
            assert state['modifier'] == 'AttackModifierList'

    @pytest.mark.parametrize('gait', ['SwimIdleState', 'SwimMoveState',
                                      'SwimBackState', 'SwimTurnLeftState',
                                      'SwimTurnRightState'])
    def test_every_swim_gait_leads_into_every_swim_attack(self, amphibian,
                                                         gait):
        """Idle, move, back and both turns each carry the three entries, with the action effect."""
        swim = _graph(amphibian).machine('SwimBehavior')
        into = {e: (to, fx) for e, to, fx in swim[gait]['transitions']
                if e.startswith('attackStart_')}
        assert into == {_event(s): (swim[f'Attack_{s}State']['id'], TO_ACTION)
                        for s in SWIM_ATTACKS}
        assert swim[gait]['flags'] == {LOCAL}

    def test_the_gait_transitions_keep_their_blend(self, amphibian):
        """The entries are appended; the gait transitions before them are untouched."""
        swim = _graph(amphibian).machine('SwimBehavior')
        entries = [(_event(s), 5 + i, TO_ACTION)
                   for i, s in enumerate(sorted(SWIM_ATTACKS))]
        assert swim['SwimIdleState']['transitions'] == [
            ('moveStart', 1, 'BlendSmooth'), ('turnLeft', 3, 'BlendSmooth'),
            ('turnRight', 4, 'BlendSmooth')] + entries
        assert swim['SwimTurnLeftState']['transitions'] == [
            ('turnStop', 0, 'BlendSmooth')] + entries

    def test_no_land_attack_is_reachable_while_swimming(self, amphibian):
        """No state of the swim machine answers a land attack event."""
        swim = _graph(amphibian).machine('SwimBehavior')
        events = {e for s in swim.values() for e, _to, _fx in s['transitions']}
        assert not events & {_event(s) for s in LAND_ATTACKS}

    def test_the_swim_attack_keeps_its_hit(self, amphibian):
        """The hit triggers sit at their times, before the end-relative attackStop."""
        hits = {'Attack_swimhandtohandattackleft': [0.5]}
        state = _graph(amphibian, hits).machine('SwimBehavior')[
            'Attack_swimhandtohandattackleftState']
        assert [(e, rel) for e, _t, rel in state['triggers']] == [
            ('weaponSwing', False), ('preHitFrame', False),
            ('HitFrame', False), ('attackStop', True)]
        assert [round(t, 3) for _e, t, _rel in state['triggers']] == [
            0.2, 0.4, 0.5, 0.0]


class TestOneStance:
    def test_a_creature_with_one_weapon_stance_is_covered(self, tmp_path):
        """One equip stance is the common case (the murkdweller): the rule applies."""
        clips = _clips(tmp_path, AMPHIBIAN + ('handtohandequip',
                                              'handtohandunequip'))
        assert list(clips['equip']) == ['H2H']
        assert len(swim_attacks(clips)) == 3
        graph = _graph(clips)
        assert len(graph.machine('SwimBehavior')) == 8
        assert _leaving_default(graph) == {_event(s) for s in LAND_ATTACKS}


class TestLeftAsItWas:
    """Creatures the rule does not cover keep the wiring they had."""

    def test_swim_attacks_and_no_swim_gait(self, tmp_path):
        """No swimforward, so no SwimState: every attack stays at the root."""
        clips = _clips(tmp_path, LAND + LAND_ATTACKS + SWIM_ATTACKS)
        assert clips['swim'] == {} and swim_attacks(clips) == []
        graph = _graph(clips)
        assert 'SwimState' not in graph.machine('TBehaviorRoot')
        assert _leaving_default(graph) == {
            _event(s) for s in LAND_ATTACKS + SWIM_ATTACKS}
        ends = [row[4] for row in state_defs(clips)
                if row[0].startswith('Attack_')]
        assert ends == ['returnToDefault'] * 5

    def test_a_swim_idle_alone_makes_no_swim_state(self, tmp_path):
        """`swimidle` without `swimforward`: nothing to nest in, every event answered on land."""
        clips = _clips(tmp_path, LAND + ('swimidle',) + LAND_ATTACKS
                       + SWIM_ATTACKS)
        assert swim_attacks(clips) == []
        graph = _graph(clips)
        assert 'SwimState' not in graph.machine('TBehaviorRoot')
        assert _leaving_default(graph) == {
            _event(s) for s in LAND_ATTACKS + SWIM_ATTACKS}

    def test_a_water_native_with_a_mixed_attack_set(self, tmp_path):
        """Promoted to its swim gait, it has no SwimState to hold a swim attack."""
        clips = _clips(tmp_path, SWIM + LAND_ATTACKS + SWIM_ATTACKS)
        assert clips['swim'] == {} and swim_attacks(clips) == []
        assert _leaving_default(_graph(clips)) == {
            _event(s) for s in LAND_ATTACKS + SWIM_ATTACKS}

    def test_a_swimmer_without_swim_attacks(self, tmp_path):
        """Land attacks only: the swim machine keeps its five states and their transitions."""
        clips = _clips(tmp_path, LAND + SWIM + LAND_ATTACKS)
        assert swim_attacks(clips) == []
        graph = _graph(clips)
        swim = graph.machine('SwimBehavior')
        assert sorted(s['id'] for s in swim.values()) == [0, 1, 2, 3, 4]
        assert swim['SwimMoveState']['transitions'] == [
            ('moveStop', 0, 'BlendSmooth'), ('moveBackward', 2, 'BlendSmooth')]
        assert _leaving_default(graph) == {_event(s) for s in LAND_ATTACKS}
        assert [row[4] for row in state_defs(clips)
                if row[0].startswith('Attack_')] == ['returnToDefault'] * 2

    def test_a_water_native(self, tmp_path):
        """Swim clips only: promoted, so no SwimState and the attacks stay at the root."""
        clips = _clips(tmp_path, SWIM + SWIM_ATTACKS)
        assert clips['swim'] == {} and swim_attacks(clips) == []
        graph = _graph(clips)
        assert 'SwimState' not in graph.machine('TBehaviorRoot')
        assert _leaving_default(graph) == {_event(s) for s in SWIM_ATTACKS}

    def test_swim_attacks_and_no_land_attack(self, tmp_path):
        """With nothing to play on land the swim attacks stay at the root, ending as before."""
        clips = _clips(tmp_path, LAND + SWIM + SWIM_ATTACKS)
        assert swim_attacks(clips) == []
        assert _leaving_default(_graph(clips)) == {
            _event(s) for s in SWIM_ATTACKS}
        ends = [row[4] for row in state_defs(clips)
                if row[0].startswith('Attack_')]
        assert ends == ['returnToDefault'] * 3

    def test_a_creature_with_weapon_stances(self, tmp_path):
        """Two stances: the rule stands back and the swim events still leave DefaultState."""
        stanced = ('handtohandequip', 'handtohandunequip', 'onehandequip',
                   'onehandunequip', 'onehandattackleft')
        clips = _clips(tmp_path, AMPHIBIAN + stanced)
        assert len(clips['equip']) == 2 and swim_attacks(clips) == []
        graph = _graph(clips)
        assert sorted(s['id'] for s in graph.machine('SwimBehavior').values()
                      ) == [0, 1, 2, 3, 4]
        assert {_event(s) for s in SWIM_ATTACKS} <= _leaving_default(graph)

    def test_a_creature_that_never_swims(self, tmp_path):
        """No swim clips at all: no swim attack, every attack ends on returnToDefault."""
        clips = _clips(tmp_path, LAND + LAND_ATTACKS)
        assert swim_attacks(clips) == []
        ends = [row[4] for row in state_defs(clips)
                if row[0].startswith('Attack_')]
        assert ends == ['returnToDefault'] * 2


class TestTransitionEffect:
    def _effects(self, items, root=False):
        """The effect name each transition of an array gets."""
        gb = GraphBuilder(['a', 'b'], [])
        xml = gb.render(gb.trans_array(items, root))
        names = dict(re.findall(
            r'<hkobject name="(#\d+)" class="hkbBlendingTransitionEffect".*?'
            r'<hkparam name="name">(\w+)</hkparam>', xml, re.S))
        return [names[ref] for ref in re.findall(
            r'<hkparam name="transition">(#\d+)</hkparam>', xml)]

    def test_a_plain_item_takes_the_array_s_layer(self):
        """Three-element items: nested arrays blend smoothly, root arrays take the action pair."""
        assert self._effects([(0, 1, F_LOCAL), (1, 0, F_LOCAL)]) == [
            'BlendSmooth', 'BlendSmooth']
        assert self._effects([(0, 1, F_LOCAL), (1, 0, F_LOCAL)], root=True) == [
            TO_ACTION, FROM_ACTION]

    def test_a_marked_item_takes_the_action_effect(self):
        """A true fourth element switches that one item; false or absent leaves it."""
        assert self._effects([(0, 1, F_LOCAL), (1, 5, F_LOCAL, True),
                              (1, 0, F_LOCAL, True),
                              (0, 2, F_LOCAL, False)]) == [
            'BlendSmooth', TO_ACTION, FROM_ACTION, 'BlendSmooth']

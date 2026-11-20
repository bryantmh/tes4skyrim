"""The mount layer of a rideable creature's graph: vanilla horsebehavior's MountBehavior.

Vanilla's horse graph has no separate "ridden" branch.  Its root is one state
machine whose RootState holds the ordinary graph, plus a synced state per paired
clip: the engine sends `pa_HorseEnter` / `pa_HorseExit` to the mount when the
rider starts mounting or dismounting, a BSSynchronizedClipGenerator plays the
paired clip in step with the rider, and `PairEnd` returns to RootState.
`bIsSynced` is held true while a paired clip plays, as vanilla does.
See: docs/commentary/asset_convert_creature.md#rideable-mounts
"""

from asset_convert.havok import hkx_xml
from asset_convert.havok.behavior_nodes import F_GLOBAL, F_LOCAL
from asset_convert.havok.mount import PAIRED_CLIPS, PAIRED_PREFIX

hkx_xml.SIGNATURES['BSSynchronizedClipGenerator'] = '0xd83bea64'

#: Engine events of the mount layer: (role -> start event), and the end event.
ENTER_EVENTS = {'enter': 'pa_HorseEnter', 'exit': 'pa_HorseExit'}
#: Sent when a paired clip finishes.
PAIR_END = 'PairEnd'
#: The Mount/Dismount IDLE events; the engine also sends them to the mount, whose graph must declare them.
IDLE_EVENTS = ['HorseEnter', 'HorseExit']
#: Events a mount graph declares, appended after every other event.
MOUNT_EVENTS = [*ENTER_EVENTS.values(), PAIR_END, *IDLE_EVENTS]
#: Variables a mount graph declares, appended after every other variable.
MOUNT_VARIABLES = [('bIsSynced', 'BOOL', 0)]
#: Vanilla horse fMarkErrorThreshold per role (mount 1.0, dismount 0.1).
_MARK_ERROR = {'enter': 1.0, 'exit': 0.1}


def _synced_clip(gb, role):
    """BSSynchronizedClipGenerator over `role`'s paired clip, vanilla horse values."""
    name, stem = PAIRED_CLIPS[role]
    clip = gb.clip(name, None, False, anim=stem)
    sync = gb.add('BSSynchronizedClipGenerator')
    sync.param('variableBindingSet', 'null')
    sync.param('userData', 0)
    sync.param('name', ENTER_EVENTS[role])
    sync.param('pClipGenerator', clip.ref)
    sync.param('SyncAnimPrefix', PAIRED_PREFIX)
    sync.param('bSyncClipIgnoreMarkPlacement', False)
    sync.param('fGetToMarkTime', '0.000000')
    sync.param('fMarkErrorThreshold', f'{_MARK_ERROR[role]:.6f}')
    sync.param('bLeadCharacter', True)
    sync.param('bReorientSupportChar', True)
    sync.param('bApplyMotionFromRoot', False)
    sync.param('sAnimationBindingIndex', -1)
    return sync


def mount_machine(gb, behavior_sm):
    """The MountBehavior state machine wrapping `behavior_sm` (state 0)."""
    eid = gb.eid
    states = [gb.state(0, 'RootState', behavior_sm.ref)]
    wilds = []
    for state_id, role in enumerate(('enter', 'exit'), 1):
        held = gb.holding(f'{PAIRED_CLIPS[role][0]}Synced',
                          _synced_clip(gb, role).ref, ['bIsSynced'])
        states.append(gb.state(state_id, f'{PAIRED_CLIPS[role][0]}Synced',
                               held.ref,
                               transitions=[(eid[PAIR_END], 0, F_LOCAL)]))
        wilds.append((eid[ENTER_EVENTS[role]], state_id, F_GLOBAL))
    return gb.state_machine('MountBehavior', states, start_id=0,
                            wildcard_ref=gb.trans_array(wilds).ref)

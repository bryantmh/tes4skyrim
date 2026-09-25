"""Getting up from a knockdown: which clips play it and when the feet take the weight.

The graph half (the pose-matching selector and the GetUpFromRagdoll state)
lives in behavior_ragdoll; this is the clip half, shared by the graph and the
animationdata cache so both channels carry the same triggers.

Vanilla dogbehavior plays every getup clip twice over, under a Reanimate
matcher and a GetUp matcher that differ only in the event the clip raises
once the feet land; `iGetUpType` picks between them.
See: docs/commentary/asset_convert_creature.md#getup-from-ragdoll
"""

from asset_convert.havok.behavior_clips import clip_state_name
from asset_convert.havok.hkx_anim import decode_clip, parse_kf_events
from asset_convert.havok.kf_decode import reset_accum_roots

#: Events the getup path adds to the graph's table.
GETUP_EVENTS = ['GetUpStart', 'GetUpEnd', 'Getup', 'Reanimated',
                'AddCharacterControllerToWorld']

#: (clip-generator prefix, event raised when the feet land), in vanilla iGetUpType order.
GETUP_MATCHERS = (('Reanimate', 'Reanimated'), ('GetUp', 'Getup'))

#: The fallback role for a ragdolling creature that ships no getup clip.
FALLBACK_ROLE = 'Idle'


def _decode_getup(kf: str, fps: float):
    """The clip with its NonAccum rise kept and only the engine-owned root reset."""
    clip, _motion = decode_clip(kf, fps, extract_motion=False)
    reset_accum_roots(clip)
    return clip


def plan_getup(clips: dict, decoded: dict, enum_map: dict, fps: float,
               failures: list) -> list:
    """[{role, stem, duration, feet, sounds}] for each getup clip; fills `decoded`.

    A clip that fails to decode is appended to `failures` and skipped.  A
    creature with a ragdoll pose but no getup clip rises through its
    annotation-free idle copy.  Called only for a ragdolling creature.
    """
    plan = []
    for role, kf in clips.get('getup', ()):
        stem = clip_state_name(kf)
        try:
            clip = _decode_getup(kf, fps)
        except Exception as e:
            failures.append((kf, f'{type(e).__name__}: {e}'))
            continue
        decoded[stem] = (clip, None)
        events = parse_kf_events(clip.text_keys, enum_map)
        plan.append({'role': role, 'stem': stem,
                     'duration': float(clip.duration),
                     'feet': events['feet'], 'sounds': events['sounds']})
    if not plan and 'ragdollpose' in decoded:
        plan.append({'role': FALLBACK_ROLE, 'stem': 'ragdollpose',
                     'duration': float(decoded['ragdollpose'][0].duration),
                     'feet': [], 'sounds': []})
    return plan


def landing_time(entry: dict) -> float:
    """The first authored footfall, else the clip's end."""
    return min((t for t, _n in entry['feet']), default=entry['duration'])


def getup_clip_meta(plan: list) -> list:
    """The animationdata entries for every getup clip generator the graph names."""
    out = []
    for prefix, event in GETUP_MATCHERS:
        for entry in plan:
            t = landing_time(entry)
            out.append({
                'name': prefix + entry['role'], 'stem': entry['stem'],
                'anim': 'Animations\\' + entry['stem'] + '.hkx', 'rate': 1.0,
                'duration': entry['duration'], 'looping': False,
                'end_event': 'GetUpEnd', 'sounds': entry['sounds'],
                'feet': entry['feet'], 'hits': [],
                'events': [(t, event), (t, 'AddCharacterControllerToWorld')],
            })
    return out

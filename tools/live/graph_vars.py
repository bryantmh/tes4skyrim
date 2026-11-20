#!/usr/bin/env python3
"""Read a LIVE actor's behavior-graph variables through the game bridge.

Why: "the creature will not cast / floats / never attacks" is usually a
variable the engine did or did not write (bWantCastLeft, IsCasting, Direction,
Speed...). The console has no safe readback -- `GetGraphVariableInt/Float`
HUNG the game (memory: never use them) -- but the values sit in plain memory
inside the graph's hkbVariableValueSet, so read them there.

Usage:
    python tools/live/graph_vars.py 12033475 --creature export/Oblivion.esm/meshes/creatures/scamp
    python tools/live/graph_vars.py 12033475 120d3aeb --creature ... --watch 5
    python tools/live/graph_vars.py 12033475 --creature ... --only Direction,Speed,IsCasting
    python tools/live/graph_vars.py 14 --graph-xml export/skyrim_assets/humanoid_graph/0_master.xml --only iGunShots

Chain (verified live 2026-08-23 for the project DB; extended here to values):
    TESForm::LookupByID(formid)            -> Actor*         (stable id 14617)
    actor+0x38                             -> IAnimationGraphManagerHolder
    [holder+0xC0] AIProcess -> [+0x8] MiddleHigh -> [+0x1A8] manager (pure reads)
    mgr+0x40 BSTSmallArray (inline slot @+0x48) -> BShkbAnimationGraph*
    graph+0x208                            -> hkbBehaviorGraph*
    hkbBehaviorGraph -> hkbVariableValueSet: found by SIGNATURE, not offset --
        a pointer within the first 0x180 bytes whose target holds an
        hkArray<int32> whose size equals the graph's declared variable count
        (hkbBehaviorGraphData +0x20 variableInfos.size, at graph+0x80).
    Names: our own generator's declaration order (hkx_behavior.graph_variables)
    for the --creature export folder -- the shipped graph declares them in
    exactly that order.

Read-only: readmem plus one TESForm::LookupByID call per sample. Never
steals focus.
"""
from __future__ import annotations

import argparse
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.live import test_instance
from tools.live.game_bridge import Bridge, BridgeError

LOOKUP_BY_ID = 14617


def _bytes(r: dict) -> bytes:
    if 'bytes' in r:
        return bytes(r['bytes'])
    if 'hex' in r:
        return bytes.fromhex(r['hex'])
    if 'data' in r:
        d = r['data']
        return bytes(d) if isinstance(d, list) else bytes.fromhex(d)
    raise BridgeError(f'unexpected readmem reply keys {list(r)}')


class Mem:
    def __init__(self, b: Bridge):
        self.b = b

    def read(self, addr: int, n: int) -> bytes:
        return _bytes(self.b.readmem(address=addr, length=n))

    def u64(self, addr: int) -> int:
        return struct.unpack('<Q', self.read(addr, 8))[0]

    def i32(self, addr: int) -> int:
        return struct.unpack('<i', self.read(addr, 4))[0]

    def cstr(self, addr: int, limit: int = 64) -> str:
        raw = self.read(addr, limit)
        return raw.split(b'\x00', 1)[0].decode('ascii', 'replace')


def hkarray(m: Mem, addr: int) -> tuple[int, int]:
    """(data ptr, size) of an hkArray at addr (ptr, i32 size, i32 cap|flags)."""
    data = m.u64(addr)
    size = m.i32(addr + 8)
    return data, size


def actor_ptr(b: Bridge, formid: int) -> int:
    return int(b.call(id=LOOKUP_BY_ID, args_=[formid])['result'])


def behavior_graph(b: Bridge, m: Mem, actor: int) -> int:
    # NO engine call: Actor::GetAnimationGraphManager (stable id 38046,
    # disassembled from the GOG exe) is `[holder+0xC0]` = AIProcess ->
    # `[+0x8]` = MiddleHighProcess -> `[+0x1A8]` = BSAnimationGraphManager.
    # The earlier version CALLED it from the bridge thread ~100 times per
    # session and the game crashed (2026-08-26, CommunityShaders
    # GrassCollision on the render thread, the sampled scamp in RBX);
    # never proven related, but a read-only chain removes the question.
    holder = actor + 0x38
    process = m.u64(holder + 0xC0)
    if not process:
        raise BridgeError('actor has no AIProcess (not loaded / not an actor)')
    middle_high = m.u64(process + 0x8)
    if not middle_high:
        raise BridgeError('actor has no MiddleHighProcess (unloaded)')
    mgr = m.u64(middle_high + 0x1A8)
    if not mgr:
        raise BridgeError('no BSAnimationGraphManager on this actor')
    # BSTSmallArray<BSTSmartPointer<BShkbAnimationGraph>, 1> at +0x40:
    # capacity|0x80000000 (LOCAL flag) @+0x40, the element INLINE @+0x48
    # when local, else a heap pointer there; size @+0x50 (read live).
    cap = m.i32(mgr + 0x40) & 0xFFFFFFFF
    n = m.i32(mgr + 0x50)
    if n < 1:
        raise BridgeError('animation graph manager holds no graphs')
    slot = m.u64(mgr + 0x48)
    bsgraph = slot if cap & 0x80000000 else m.u64(slot)
    hkb = m.u64(bsgraph + 0x208)
    if not hkb:
        raise BridgeError('BShkbAnimationGraph+0x208 is null (layout?)')
    return hkb


def variable_names(creature_dir: str) -> list[str]:
    """Declaration order from OUR generator (hkx_behavior.graph_variables),
    which is exactly the order the shipped graph declares them in."""
    from asset_convert.havok.hkx_behavior import (classify_clips, graph_variables,
                                            movement_type_names)
    clips = classify_clips(creature_dir)
    name = Path(creature_dir).name
    has_swim = bool(clips.get('swim', {}).get('forward'))
    mts = movement_type_names(name, has_swim=has_swim)
    return [n for n, _t, _iv in graph_variables(clips, mts)]


def variable_names_xml(xml_path: str) -> list[str]:
    """Declaration order read from a decompiled graph XML's `variableNames`
    (the root graph an actor plays, e.g. the patched humanoid 0_master)."""
    import re
    text = Path(xml_path).read_text(encoding='utf-8', errors='replace')
    m = re.search(r'<hkparam name="variableNames" numelements="(\d+)">(.*?)</hkparam>',
                  text, re.S)
    if not m:
        raise SystemExit(f'{xml_path}: no variableNames array')
    names = re.findall(r'<hkcstring>([^<]*)</hkcstring>', m.group(2))
    assert len(names) == int(m.group(1)), (len(names), m.group(1))
    return names


def value_set(m: Mem, hkb: int, n: int) -> int:
    """Find the hkbVariableValueSet by signature: a pointer inside the
    hkbBehaviorGraph whose target is (refobj 0x10) + hkArray<int32> of at
    least n words @+0x10 (exactly n for a standalone graph; a root graph
    linking referenced behaviors appends their variables after its own),
    then two more hkArrays (quad @+0x20, variant @+0x30) that are small."""
    best = None
    for off in range(0x40, 0x180, 8):
        try:
            p = m.u64(hkb + off)
            if p < 0x10000:
                continue
            data, size = hkarray(m, p + 0x10)
            _q, qn = hkarray(m, p + 0x20)
            _v, vn = hkarray(m, p + 0x30)
        except BridgeError:
            continue
        if size >= n and data > 0x10000 and 0 <= qn < 64 and 0 <= vn < 64:
            if size == n:
                return p
            if best is None or size < best[0]:
                best = (size, p)
    if best is None:
        raise BridgeError(f'hkbVariableValueSet with {n}+ words not found')
    return best[1]


def snapshot(b: Bridge, m: Mem, formid: int, names: list, only=None) -> dict:
    actor = actor_ptr(b, formid)
    if not actor:
        raise BridgeError(f'{formid:08X}: LookupByID returned null')
    hkb = behavior_graph(b, m, actor)
    vs = value_set(m, hkb, len(names))
    words, _n = hkarray(m, vs + 0x10)
    raw = m.read(words, 4 * len(names))
    out = {}
    for i, nm in enumerate(names):
        if only and nm not in only:
            continue
        word = raw[4 * i:4 * i + 4]
        as_int = struct.unpack('<i', word)[0]
        as_f = struct.unpack('<f', word)[0]
        # REAL variables carry IEEE bits; ints/bools are tiny integers
        if nm[:1].islower() and nm[:1] in 'if' or nm in ('Speed', 'Direction',
                                                        'TurnDelta',
                                                        'TurnDeltaDamped',
                                                        'SpeedSampled',
                                                        'staggerMagnitude',
                                                        'staggerDirection'):
            pass
        out[nm] = (as_int, as_f)
    return {'actor': actor, 'graph': hkb, 'values': out}


def fmt(nm: str, v: tuple[int, float]) -> str:
    i, f = v
    reals = {'Speed', 'Direction', 'TurnDelta', 'TurnDeltaDamped',
             'SpeedSampled', 'staggerMagnitude', 'staggerDirection'}
    if nm in reals or (abs(i) > 100000 and -1e6 < f < 1e6):
        return f'{f:.3f}'
    return str(i)


def _names(ap, a) -> list[str]:
    """The variable names from whichever source the arguments name."""
    if not (a.creature or a.graph_xml):
        ap.error('one of --creature or --graph-xml is required')
    return (variable_names_xml(a.graph_xml) if a.graph_xml
            else variable_names(a.creature))


def _sample(b: Bridge, m: Mem, fid: int, names, only, changes: bool,
            last: dict, k: int, t0: float) -> None:
    """Take and print one snapshot of one actor (or only its changes)."""
    try:
        s = snapshot(b, m, fid, names, only)
    except BridgeError as e:
        print(f'{fid:08X}: ERROR {e}')
        return
    cur = {n: fmt(n, v) for n, v in s['values'].items()}
    if not changes:
        print(f'[{k}] {fid:08X} actor={s["actor"]:#x} graph={s["graph"]:#x}')
        print('     ' + ' '.join(f'{n}={v}' for n, v in cur.items()))
        return
    prev = last.get(fid)
    diff = cur if prev is None else {n: v for n, v in cur.items() if prev.get(n) != v}
    if diff:
        print(f'[{time.time()-t0:6.1f}s] {fid:08X} '
              + ' '.join(f'{n}={v}' for n, v in diff.items()), flush=True)
    last[fid] = cur


def main(argv=None) -> int:
    """Parse arguments and print each actor's variables; the exit code."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('formids', nargs='+', help='actor reference FormIDs (hex)')
    ap.add_argument('--instance', type=int, default=1, help='test instance slot (1 = the first)')
    ap.add_argument('--watch', type=int, default=1, help='snapshots per actor')
    ap.add_argument('--interval', type=float, default=1.0)
    ap.add_argument('--only', help='comma-separated variable names')
    ap.add_argument('--changes', action='store_true',
                    help='print only variables that changed since the last sample')
    ap.add_argument('--creature',
                    help='EXPORT creature folder, e.g. '
                         'export/Oblivion.esm/meshes/creatures/scamp')
    ap.add_argument('--graph-xml',
                    help='decompiled root graph XML to take variable names '
                         'from instead (humanoids: the patched 0_master.xml)')
    a = ap.parse_args(argv)
    names = _names(ap, a)
    only = set(a.only.split(',')) if a.only else None
    ids = [int(x, 16) for x in a.formids]
    last = {}
    t0 = time.time()
    test_instance.use_instance(a.instance)
    with Bridge(test_instance.PIPE) as b:
        m = Mem(b)
        for k in range(a.watch):
            for fid in ids:
                _sample(b, m, fid, names, only, a.changes, last, k, t0)
            if k + 1 < a.watch:
                time.sleep(a.interval)
    return 0


if __name__ == '__main__':
    sys.exit(main())

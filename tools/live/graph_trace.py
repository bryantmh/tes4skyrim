#!/usr/bin/env python3
"""Record one LIVE actor's behavior graph frame by frame, then decode a timeline.

    python tools/live/graph_trace.py record --ref 1A00ABCD --behavior <behavior.hkx> \
        --seconds 30 --out trace.bin
    python tools/live/graph_trace.py decode trace.bin --behavior <behavior.hkx> [--all]

`record` makes the game bridge (game_bridge/plugin/trace.cpp) sample, at ~120 Hz:
graph variables, the graph's ACTIVE NODE list (each node's bytes: state machine
current state, clip local time and extracted motion), the actor's position and
heading, and the movement controller mode; and hooks, with timestamps and
results, every event sent INTO a graph, every event a graph EMITS, and every
action the engine performs. All addresses are discovered by signature at run
time. `decode` prints what changed, in time order, for this one actor.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from asset_convert.havok.humanoid_graph import hkxconv
from tools.hkx.behavior_dump import parse, string_table, text
from tools.live.game_bridge import Bridge, BridgeError
from tools.live.graph_vars import Mem, actor_ptr, behavior_graph, value_set

#: Hook targets in SkyrimSE 1.6.1170: rva -> (label, captured arg, bytes captured).
HOOKS = {0xBA4260: ('graph_notify', 2, 8),
         0x69F450: ('graph_event', 2, 0x18),
         0x6CD0D0: ('perform_action', 2, 0x60)}
#: Prologue bytes at each hook rva in the 1.6.1170 exe; a mismatch means another build.
PROLOGUES = {0xBA4260: '4056574156', 0x69F450: '40574883ec60', 0x6CD0D0: '40574883ec30'}
#: MovementControllerNPC primary vtable rva (1.6.1170); its +0x1C0 is the motion mode.
MOVEMENT_CONTROLLER_VTABLE = 0x18B1388
#: Bytes captured from each active node: vtable, name (+0x38), state id (+0x80), clip time.
NODE_CAPTURE = 0x110
NODE_NAME_OFF, SM_CURRENT_STATE_OFF = 0x38, 0x80
CLIP_MOTION_OFF, CLIP_LOCAL_TIME_OFF = 0xB0, 0xF0
#: hkbBehaviorGraph+0x98 -> hkArray of inline 0x90-byte node infos; clone at +0x58, weights +0x40.
ACTIVE_NODES_OFF, NODE_INFO_STRIDE, NODE_INFO_CLONE_OFF = 0x98, 0x90, 0x58
#: hkbBehaviorGraph+0x80: the root generator (the behavior's top state machine).
ROOT_GENERATOR_OFF = 0x80


# ---------------------------------------------------------------------------
# The graph as authored (from the shipped .hkx)
# ---------------------------------------------------------------------------

def graph_model(behavior_hkx: str) -> dict:
    """Names the decoder needs: variables (+types), events, node names, state ids."""
    xml = Path(tempfile.gettempdir()) / (Path(behavior_hkx).stem + '_trace.xml')
    hkxconv('toxml', behavior_hkx, str(xml))
    objs = parse(str(xml))
    events, variables = string_table(objs)
    types = []
    states = {}
    nodes = set()
    for cls, p in objs.values():
        if cls == 'hkbBehaviorGraphData':
            types = [text({q.get('name'): q for q in o.findall('hkparam')}, 'type')
                     for o in p['variableInfos'].findall('hkobject')]
        if 'name' in p and text(p, 'name'):
            nodes.add(text(p, 'name'))
        if cls == 'hkbStateMachine':
            states[text(p, 'name')] = {
                int(text(objs[s][1], 'stateId')): text(objs[s][1], 'name')
                for s in p['states'].text.split() if s in objs}
    return {'events': events, 'variables': variables, 'types': types,
            'states': states, 'nodes': sorted(nodes)}


# ---------------------------------------------------------------------------
# Live discovery
# ---------------------------------------------------------------------------

class Live:
    """Bridge + memory reader with string and RTTI caches."""

    def __init__(self, bridge: Bridge):
        """Bind the bridge and read the module base."""
        self.b, self.m = bridge, Mem(bridge)
        self.base = int(bridge.resolve(rva=0)['address'])
        self._str, self._rtti = {}, {}

    def string(self, addr: int) -> str:
        """NUL-terminated ASCII at addr (cached); '' when unreadable."""
        if addr not in self._str:
            try:
                self._str[addr] = self.m.cstr(addr, 96) if addr > 0x10000 else ''
            except BridgeError:
                self._str[addr] = ''
        return self._str[addr]

    def rtti(self, vtable: int) -> str:
        """Class name behind a vtable pointer (cached); '' when not a vtable."""
        if vtable not in self._rtti:
            try:
                col = self.m.u64(vtable - 8)
                td = struct.unpack('<I', self.m.read(col + 12, 4))[0]
                self._rtti[vtable] = self.string(self.base + td + 16)
            except (BridgeError, struct.error):
                self._rtti[vtable] = ''
        return self._rtti[vtable]


def _safe_u64(live: Live, addr: int) -> int:
    """u64 at addr, 0 when unreadable."""
    try:
        return live.m.u64(addr)
    except BridgeError:
        return 0


def active_node_names(live: Live, header: int) -> list:
    """Names of the nodes currently listed in an active-node hkArray."""
    data, size = _safe_u64(live, header), _safe_u64(live, header + 8) & 0xFFFFFFFF
    clones = [_safe_u64(live, data + i * NODE_INFO_STRIDE + NODE_INFO_CLONE_OFF)
              for i in range(min(size, 256))]
    return [live.string(_safe_u64(live, c + NODE_NAME_OFF)) for c in clones]


def find_active_nodes(live: Live, hkb: int, names: set) -> dict:
    """The graph's active-node hkArray (hkbBehaviorGraph+0x98), checked against `names`."""
    header = _safe_u64(live, hkb + ACTIVE_NODES_OFF)
    hits = sum(1 for n in active_node_names(live, header) if n in names)
    if not hits:
        raise BridgeError('hkbBehaviorGraph+0x98 lists no node of this graph')
    return {'header': header, 'stride': NODE_INFO_STRIDE,
            'elem_ptr_off': NODE_INFO_CLONE_OFF, 'hits': hits}


def find_movement_controller(live: Live, actor: int) -> int:
    """The actor's MovementControllerNPC (vtable match), 0 when not found."""
    want = live.base + MOVEMENT_CONTROLLER_VTABLE
    for off in range(0, 0x400, 8):
        p = _safe_u64(live, actor + off)
        if p > 0x10000 and _safe_u64(live, p) == want:
            return p
    return 0


def install_hooks(live: Live) -> dict:
    """Install the trace hooks: 1.6.1170 prologues, or targets this plugin already holds."""
    out = {}
    for rva, (label, cap_arg, cap_len) in HOOKS.items():
        got = live.m.read(live.base + rva, len(PROLOGUES[rva]) // 2).hex()
        if got != PROLOGUES[rva] and not got.startswith('48b8'):
            print(f'  hook {label}: prologue {got} != {PROLOGUES[rva]}; skipped')
            continue
        try:
            r = live.b.request('trace', action='hook', rva=str(rva), cap_arg=cap_arg,
                               cap_len=cap_len, label=label)
        except BridgeError as e:
            print(f'  hook {label}: prologue {got} (want {PROLOGUES[rva]}): {e}; skipped')
            continue
        out[str(r['index'])] = label
        print(f'  hook {label} -> slot {r["index"]}')
    return out


def regions_for(live: Live, actor: int, hkb: int, n_vars: int, nodes: dict,
                ctrl: int) -> list:
    """The sampler's region list, in the order parse_sample() reads them."""
    vs = value_set(live.m, hkb, n_vars)
    regions = [{'kind': 1, 'addr': str(vs + 0x10), 'off': 0, 'len': 4 * n_vars},
               {'kind': 0, 'addr': str(actor + 0x48), 'len': 0x18},
               {'kind': 2, 'addr': str(nodes['header']), 'len': NODE_CAPTURE,
                'stride': nodes['stride'], 'elem_ptr_off': nodes['elem_ptr_off'],
                'max': 512}]
    if ctrl:
        regions.append({'kind': 0, 'addr': str(ctrl + 0x1C0), 'len': 8})
    return regions


def _plays_graph(live: Live, actor: int, root_names: set) -> bool:
    """True when the actor's graph root generator (hkbBehaviorGraph+0x80) is this behavior's."""
    try:
        hkb = behavior_graph(live.b, live.m, actor)
    except BridgeError:
        return False
    root = _safe_u64(live, hkb + ROOT_GENERATOR_OFF)
    return live.string(_safe_u64(live, root + NODE_NAME_OFF)) in root_names


def find_actor_auto(live: Live, model: dict, labels: dict, seconds: float) -> int:
    """The loaded actor playing this graph, found from who emits graph events."""
    probe = str(Path(tempfile.gettempdir()) / 'graph_trace_probe.bin')
    live.b.request('trace', action='start', path=probe, interval_ms=50, meta='{}',
                   regions=[])
    time.sleep(seconds)
    live.b.request('trace', action='stop')
    sinks = set()
    for typ, payload in read_records(probe):
        if typ == 2 and labels.get(str(payload[0])) == 'graph_event':
            sinks.add(struct.unpack_from('<Q', payload, 21)[0])
    roots = {n for n in model['states'] if n.endswith('Root') or n.endswith('RootBehavior')}
    hits = [s - 0x30 for s in sinks if _plays_graph(live, s - 0x30, roots)]
    print(f'auto: {len(sinks)} actors emitted graph events, {len(hits)} play this graph')
    if not hits:
        raise BridgeError('no loaded actor plays this graph; pass --ref')
    return hits[0]


def record(args) -> int:
    """Discover, hook, sample for --seconds, stop; the file decodes on its own."""
    model = graph_model(args.behavior)
    with Bridge() as b:
        live = Live(b)
        hooks = install_hooks(live)
        actor = (actor_ptr(b, int(args.ref, 16)) if args.ref
                 else find_actor_auto(live, model, hooks, 5.0))
        hkb = behavior_graph(b, live.m, actor)
        mgr = live.m.u64(live.m.u64(live.m.u64(actor + 0x38 + 0xC0) + 8) + 0x1A8)
        nodes = find_active_nodes(live, hkb, set(model['nodes']))
        ctrl = find_movement_controller(live, actor)
        print(f'actor {actor:#x} graph {hkb:#x} manager {mgr:#x} controller {ctrl:#x}'
              f'\nactive nodes: {nodes}')
        meta = {'actor': actor, 'hkb': hkb, 'manager': mgr, 'controller': ctrl,
                'nodes': nodes, 'hooks': hooks, 'n_vars': len(model['variables']),
                'has_ctrl': bool(ctrl), 'base': live.base}
        out = str(Path(args.out).resolve())
        b.request('trace', action='start', path=out, interval_ms=args.interval_ms,
                  meta=json.dumps(meta),
                  regions=regions_for(live, actor, hkb, len(model['variables']), nodes, ctrl))
        for _ in range(int(args.seconds)):
            time.sleep(1)
            print(b.request('trace', action='status'), flush=True)
        print(b.request('trace', action='stop'))
        decode_file(live, out, model, args.all)
    return 0


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def read_records(path: str):
    """Yield (type, payload) framed records."""
    data = Path(path).read_bytes()
    i = 0
    while i + 5 <= len(data):
        typ, n = data[i], struct.unpack_from('<I', data, i + 1)[0]
        yield typ, data[i + 5:i + 5 + n]
        i += 5 + n


def parse_sample(payload: bytes, meta: dict) -> dict:
    """One SAMPLE record -> {'t', 'vars', 'refr', 'nodes': [(ok, elem, node bytes)], 'mode'}."""
    t = struct.unpack_from('<Q', payload, 0)[0]
    i = 8
    nv = 4 * meta['n_vars']
    out = {'t': t, 'vars': payload[i + 1:i + 1 + nv]}
    i += 1 + nv
    out['refr'] = struct.unpack_from('<6f', payload, i + 1)
    i += 1 + 0x18
    count = struct.unpack_from('<I', payload, i)[0]
    i += 4
    stride = meta['nodes']['stride']
    nodes = []
    for _ in range(count):
        nodes.append((payload[i], payload[i + 1:i + 1 + stride],
                      payload[i + 1 + stride:i + 1 + stride + NODE_CAPTURE]))
        i += 1 + stride + NODE_CAPTURE
    out['nodes'] = nodes
    out['mode'] = struct.unpack_from('<i', payload, i + 1)[0] if meta['has_ctrl'] else None
    return out


def var_values(raw: bytes, model: dict) -> dict:
    """Variable words -> {name: formatted value} using the declared types."""
    out = {}
    for k, name in enumerate(model['variables']):
        word = raw[4 * k:4 * k + 4]
        real = k < len(model['types']) and model['types'][k] == 'VARIABLE_TYPE_REAL'
        out[name] = (f'{struct.unpack("<f", word)[0]:.3f}' if real
                     else str(struct.unpack('<i', word)[0]))
    return out


def _node_desc(cls: str, name: str, cap: bytes, model: dict) -> str:
    """One active node's class plus its state id or clip time and motion."""
    desc = cls.replace('.?AV', '').rstrip('@')
    if 'hkbStateMachine' in cls:
        sid = struct.unpack_from('<i', cap, SM_CURRENT_STATE_OFF)[0]
        return desc + f' state={sid}:{model["states"].get(name, {}).get(sid, "?")}'
    if 'hkbClipGenerator' in cls:
        lt = struct.unpack_from('<f', cap, CLIP_LOCAL_TIME_OFF)[0]
        mx, my = struct.unpack_from('<2f', cap, CLIP_MOTION_OFF)
        return desc + f' t={lt:.3f} motion=({mx:.2f},{my:.2f})'
    return desc


def node_view(live: Live, nodes: list, model: dict) -> dict:
    """{node name: description} for one sample's active nodes."""
    view = {}
    for _ok, elem, cap in nodes:
        name = live.string(struct.unpack_from('<Q', cap, NODE_NAME_OFF)[0])
        if name:
            cls = live.rtti(struct.unpack_from('<Q', cap, 0)[0])
            w = struct.unpack_from('<3f', elem, 0x40) if len(elem) >= 0x4C else ()
            view[name] = (_node_desc(cls, name, cap, model)
                          + ' w=' + ','.join(f'{x:.2f}' for x in w))
    return view


def hook_line(live: Live, payload: bytes, meta: dict):
    """One HOOK record -> (t_enter, text) when it concerns the traced actor, else None."""
    idx = payload[0]
    t0 = struct.unpack_from('<Q', payload, 1)[0]
    a1, _a2, _a3, _a4, ret = struct.unpack_from('<5Q', payload, 21)
    cap_len = struct.unpack_from('<H', payload, 61)[0]
    cap = payload[63:63 + cap_len]
    label = meta['hooks'].get(str(idx), str(idx))
    if label == 'graph_notify' and a1 == meta['manager']:
        name = live.string(struct.unpack_from('<Q', cap, 0)[0])
        return t0, f'-> graph  {name}  accepted={ret & 0xFF}'
    if label == 'graph_event' and a1 == meta['actor'] + 0x30:
        tag, _holder, pay = struct.unpack_from('<3Q', cap, 0)
        return t0, f'<- graph  {live.string(tag)} {live.string(pay)}'.rstrip()
    if (label == 'perform_action' and cap_len >= 0x20
            and struct.unpack_from('<Q', cap, 8)[0] == meta['actor']):
        action = struct.unpack_from('<Q', cap, 0x18)[0]
        fid = struct.unpack_from('<I', live.m.read(action + 0x14, 4))[0] if action else 0
        return t0, f'ACTION {fid:08X} performed={ret & 0xFF}'
    return None


def _diff_lines(prev: dict, cur: dict) -> list:
    """Readable changes between two decoded samples."""
    lines = [f'+ {n}: {cur["view"][n]}' for n in sorted(set(cur['view']) - set(prev['view']))]
    lines += [f'- {n}' for n in sorted(set(prev['view']) - set(cur['view']))]
    lines += [f'~ {n}: {d}' for n, d in cur['view'].items()
              if prev['view'].get(n) not in (None, d) and 'state=' in d]
    lines += [f'  {n}={v}' for n, v in cur['vars'].items() if prev['vars'].get(n) != v]
    if cur['mode'] != prev['mode']:
        lines.append(f'  movement mode={cur["mode"]}')
    return lines


def _sample_row(live: Live, s: dict, prev, model: dict, show_all: bool):
    """(new prev state, timeline row or None) for one decoded sample."""
    cur = {'view': node_view(live, s['nodes'], model),
           'vars': var_values(s['vars'], model), 'mode': s['mode'],
           'xy': (s['refr'][3], s['refr'][4], s['t'])}
    if prev is None:
        return cur, None
    (px, py, pt), (x, y, t) = prev['xy'], cur['xy']
    speed = ((x - px) ** 2 + (y - py) ** 2) ** 0.5 / (max(t - pt, 1) / 1e6)
    lines = _diff_lines(prev, cur)
    if not (lines or show_all):
        return cur, None
    return cur, (t, f'[speed {speed:7.1f} u/s heading {s["refr"][2]:.3f}] ' + ' | '.join(lines))


def decode_file(live: Live, path: str, model: dict, show_all: bool) -> None:
    """Print the merged timeline: sample diffs and the actor's hook events."""
    meta, rows, prev = None, [], None
    for typ, payload in read_records(path):
        if typ == 0:
            meta = json.loads(payload.decode())
            continue
        if meta is None:
            continue
        row = (hook_line(live, payload, meta) if typ == 2 else None)
        if typ == 1:
            prev, row = _sample_row(live, parse_sample(payload, meta), prev, model, show_all)
        if row:
            rows.append(row)
    t_start = min((t for t, _ in rows), default=0)
    for t, line in sorted(rows, key=lambda r: r[0]):
        print(f'{(t - t_start) / 1e6:9.4f}  {line}')


def decode(args) -> int:
    """Decode a recorded file (needs the bridge for string and RTTI lookups)."""
    model = graph_model(args.behavior)
    with Bridge() as b:
        decode_file(Live(b), args.file, model, args.all)
    return 0


def main(argv=None) -> int:
    """Parse the sub-command and run it."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    rec = sub.add_parser('record')
    rec.add_argument('--ref', help='actor reference FormID (hex); default: find the '
                                    'loaded actor playing --behavior')
    rec.add_argument('--behavior', required=True, help='the actor graph .hkx as shipped')
    rec.add_argument('--seconds', type=float, default=30)
    rec.add_argument('--interval-ms', type=int, default=8)
    rec.add_argument('--out', default='graph_trace.bin')
    rec.add_argument('--all', action='store_true', help='print every sample')
    dec = sub.add_parser('decode')
    dec.add_argument('file')
    dec.add_argument('--behavior', required=True)
    dec.add_argument('--all', action='store_true')
    a = ap.parse_args(argv)
    return record(a) if a.cmd == 'record' else decode(a)


if __name__ == '__main__':
    sys.exit(main())

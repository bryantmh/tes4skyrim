#!/usr/bin/env python3
"""Read a loaded reference's 3D and Gamebryo animation state out of the RUNNING game.

Answers, without a relaunch, the questions that decided the arena-spectator
bug (2026-08-18):

  * what is each node's CURRENT local transform (is Bip01 identity or authored?)
  * is it changing frame to frame (animating) or frozen?
  * which NiControllerSequences does the NiControllerManager hold, what are
    their cycle types, and which are ANIMATING / INACTIVE right now?
  * and at what RATE is one actually playing (`sequences --watch`)?  Samples
    `seq+0x50` lastTime against the wall clock: 1.0x is correct, N means the
    sequence advances N seconds of animation per real second.  This is the
    measurement that separates "the sequence data is wrong" from "the time fed
    into it is inflated" -- the engine's only divisor is the sequence's own
    `frequency` (duration getter, GOG/AE exe 0x5050a0, computes
    (end-begin)/freq), so a high rate with freq==1.0 means inflated input time.
  * and, to prove a fix before rebuilding: flip a loaded sequence's cycle type
    or a controlled block's pose in memory, `sae AutoReset`, and watch.

Everything is read with the bridge's `readmem`/`call`/`writemem` primitives
(tools/live/game_bridge.py) -- no plugin rebuild, no thread suspension.

Usage:
    python tools/live/nif_live.py nodes 1318B6C5 --names "Bip01,Bip01 NonAccum" --samples 6
    python tools/live/nif_live.py tree 1318B6C5 [--depth 3]
    python tools/live/nif_live.py sequences 1318B6C5
    python tools/live/nif_live.py sequences 1209D838 --watch --sequence Forward
    python tools/live/nif_live.py set-cycle 1318B6C5 AutoLoop 0        # 0 LOOP 1 REVERSE 2 CLAMP
    python tools/live/nif_live.py set-pose 1318B6C5 AutoPlay Bip01 --identity   # or --sentinel
    python tools/live/nif_live.py sae 1318B6C5 AutoReset

Verified layouts (SSE 1.6.1170, read back and cross-checked against the NIF):
    TESObjectREFR +0x68 -> LOADED_REF_DATA, whose +0x68 -> root NiAVObject
    NiObjectNET   +0x10 name (BSFixedString char*), +0x18 first controller
    NiAVObject    +0x48 local rotate (3x3 row-major), +0x6C local translate,
                  +0x78 local scale, +0x7C world rotate, +0xA0 world translate,
                  +0xF4 flags (bit 0 APP_CULLED; NiNode::UpdateDownwardPass 0xd1dad0)
    NiNode        +0x110 children NiTArray (data +0x8, capacity/free/size u16)
    NiControllerManager (first controller on the root):
                  +0x48 sequences NiTArray (data +0x50, cap/free/size @+0x58)
    NiControllerSequence: +0x10 name, +0x18 arraySize, +0x20 InterpArrayItem*,
                  +0x28 IDTag* (0x28 each, avObjectName first), +0x40 cycleType,
                  +0x44 frequency, +0x48 beginKeyTime, +0x4C endKeyTime,
                  +0x50 lastTime, +0x68 state (0 INACTIVE 1 ANIMATING 2 EASEIN
                  3 EASEOUT 4 TRANSSOURCE 5 TRANSDEST 6 MORPHSOURCE),
                  +0x88 accumRootName, +0x90 accumRoot
    InterpArrayItem 0x20: +0 interpolator, +8 controller, +0x10 blendInterp
    NiTransformInterpolator: +0x18 translate, +0x24 quat (w,x,y,z), +0x34 scale,
                  +0x38 NiTransformData*
"""

import argparse
import math
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tools.live.game_bridge import Bridge

LOOKUP_BY_ID = 14617           # TESForm::LookupByID stable id
#: TESObjectREFR vtable slot of Get3D(bool firstPerson).
GET3D_SLOT = 0x6F
NO_VALUE = -3.4028234663852886e+38
STATES = {0: 'INACTIVE', 1: 'ANIMATING', 2: 'EASEIN', 3: 'EASEOUT',
          4: 'TRANSSOURCE', 5: 'TRANSDEST', 6: 'MORPHSOURCE'}
CYCLES = {0: 'LOOP', 1: 'REVERSE', 2: 'CLAMP'}


class Live:
    def __init__(self, bridge: Bridge):
        self.b = bridge

    # -- raw ---------------------------------------------------------------
    def rd(self, addr, n):
        return bytes.fromhex(self.b.readmem(address=addr, length=n)['hex'])

    def u64(self, addr):
        return struct.unpack('<Q', self.rd(addr, 8))[0]

    def cstr(self, p, n=96):
        if not p or p < 0x10000:
            return ''
        try:
            s = self.b.readmem(address=p, length=n, as_string=True)
            return s.get('string') or ''
        except Exception:
            return ''

    def root_3d(self, formid, first_person=False):
        ptr = int(self.b.call(id=LOOKUP_BY_ID, args_=[formid])['result'])
        if not ptr:
            raise SystemExit(f'form {formid:08X} not found')
        if first_person:
            fn = self.u64(self.u64(ptr) + 8 * GET3D_SLOT)
            root = int(self.b.call(address=fn, args_=[ptr, 1])['result'])
            if not root:
                raise SystemExit('reference has no first-person 3D')
            return root
        loaded = self.u64(ptr + 0x68)
        if not loaded:
            raise SystemExit('reference has no loaded data (not in a loaded cell?)')
        root = self.u64(loaded + 0x68)
        if not root:
            raise SystemExit('reference has no 3D')
        return root

    def name_of(self, node):
        return self.cstr(self.u64(node + 0x10))

    def children(self, node):
        hdr = self.rd(node + 0x110, 0x18)
        data = struct.unpack_from('<Q', hdr, 0x08)[0]
        cap, free, size = struct.unpack_from('<3H', hdr, 0x10)
        if not data or cap > 1024 or free > cap:
            return []
        return [p for p in struct.unpack(f'<{free}Q', self.rd(data, 8 * free)) if p]

    def local(self, node):
        d = self.rd(node + 0x48, 0x34)
        rot = struct.unpack_from('<9f', d, 0)
        tr = struct.unpack_from('<3f', d, 0x24)
        sc = struct.unpack_from('<f', d, 0x30)[0]
        return rot, tr, sc

    def av_flags(self, node):
        """NiAVObject flags (+0xF4); bit 0 is APP_CULLED (hidden)."""
        return struct.unpack('<I', self.rd(node + 0xF4, 4))[0]

    def world_translate(self, node):
        return struct.unpack_from('<3f', self.rd(node + 0xA0, 12), 0)

    def walk(self, node, depth=0, max_depth=32, parent=''):
        nm = self.name_of(node)
        yield node, nm, depth, parent
        if depth >= max_depth:
            return
        try:
            kids = self.children(node)
        except Exception:
            kids = []          # NiTriShape etc. -- no children array
        for c in kids:
            yield from self.walk(c, depth + 1, max_depth, nm)

    def find_nodes(self, root, names):
        want = set(names)
        found = {}
        for node, nm, _, _ in self.walk(root):
            if nm in want and nm not in found:
                found[nm] = node
        return found

    # -- controller manager / sequences ------------------------------------
    def manager(self, root):
        mgr = self.u64(root + 0x18)
        if not mgr:
            raise SystemExit('root has no controller (no NiControllerManager)')
        return mgr

    def sequences(self, mgr):
        data = self.u64(mgr + 0x50)
        cap, free, size = struct.unpack_from('<3H', self.rd(mgr + 0x58, 6), 0)
        if not data or free > cap:
            return []
        return [p for p in struct.unpack(f'<{free}Q', self.rd(data, 8 * free)) if p]

    def seq_info(self, seq):
        d = self.rd(seq, 0xA0)
        return {
            'addr': seq,
            'name': self.cstr(struct.unpack_from('<Q', d, 0x10)[0]),
            'n': struct.unpack_from('<I', d, 0x18)[0],
            'items': struct.unpack_from('<Q', d, 0x20)[0],
            'tags': struct.unpack_from('<Q', d, 0x28)[0],
            'cycle': struct.unpack_from('<I', d, 0x40)[0],
            'freq': struct.unpack_from('<f', d, 0x44)[0],
            'begin': struct.unpack_from('<f', d, 0x48)[0],
            'end': struct.unpack_from('<f', d, 0x4C)[0],
            'lastTime': struct.unpack_from('<f', d, 0x50)[0],
            # raw floats +0x54..+0x64: the remaining NiControllerSequence
            # timing fields (offset / weighted last time / ease in-out) --
            # printed by --watch so the REAL playback position shows itself
            # (lastTime is the app clock and always advances at 1.0x)
            'raw': struct.unpack_from('<5f', d, 0x54),
            'state': struct.unpack_from('<I', d, 0x68)[0],
            'accum': self.cstr(struct.unpack_from('<Q', d, 0x88)[0]),
            'accumRoot': struct.unpack_from('<Q', d, 0x90)[0],
        }

    def seq_items(self, info):
        """[(node_name, interpolator_ptr)] in controlled-block order."""
        n = info['n']
        if not n or not info['items'] or not info['tags']:
            return []
        tags = self.rd(info['tags'], 0x28 * n)
        items = self.rd(info['items'], 0x20 * n)
        out = []
        for i in range(n):
            nm = self.cstr(struct.unpack_from('<Q', tags, i * 0x28)[0])
            interp = struct.unpack_from('<Q', items, i * 0x20)[0]
            out.append((nm, interp))
        return out

    def interp_pose(self, interp):
        d = self.rd(interp, 0x40)
        return (struct.unpack_from('<3f', d, 0x18), struct.unpack_from('<4f', d, 0x24),
                struct.unpack_from('<f', d, 0x34)[0], struct.unpack_from('<Q', d, 0x38)[0])


def _fmt_rot(rot):
    yaw = math.degrees(math.atan2(rot[3], rot[0]))
    return f'row0=({rot[0]:.3f},{rot[1]:.3f},{rot[2]:.3f}) yaw~{yaw:.1f}'


def cmd_tree(a):
    with Bridge() as b:
        L = Live(b)
        root = L.root_3d(int(a.ref, 16), a.first_person)
        for node, nm, depth, _ in L.walk(root, max_depth=a.depth):
            rot, tr, sc = L.local(node)
            flags = L.av_flags(node)
            print(f"{'  ' * depth}{nm!r} t=({tr[0]:.2f},{tr[1]:.2f},{tr[2]:.2f}) "
                  f"{_fmt_rot(rot)} s={sc:.2f} flags={flags:#x} hidden={flags & 1}")
    return 0


def cmd_nodes(a):
    names = [n.strip() for n in a.names.split(',') if n.strip()]
    with Bridge() as b:
        L = Live(b)
        root = L.root_3d(int(a.ref, 16), a.first_person)
        found = L.find_nodes(root, names)
        missing = [n for n in names if n not in found]
        if missing:
            print('not found:', missing)
        for i in range(a.samples):
            parts = []
            for nm in names:
                if nm not in found:
                    continue
                rot, tr, sc = L.local(found[nm])
                parts.append(f"{nm}: t=({tr[0]:.2f},{tr[1]:.2f},{tr[2]:.2f}) {_fmt_rot(rot)}")
            print(f'[{i}] ' + ' | '.join(parts), flush=True)
            if i + 1 < a.samples:
                time.sleep(a.interval)
    return 0


def cmd_sequences(a):
    with Bridge() as b:
        L = Live(b)
        root = L.root_3d(int(a.ref, 16))
        mgr = L.manager(root)
        seqs = L.sequences(mgr)
        if a.watch:
            return _watch_rate(L, seqs, a)
        print(f'manager {mgr:#x}: {len(seqs)} sequence(s)')
        for s in seqs:
            i = L.seq_info(s)
            print(f"  {i['name']!r}: cycle={CYCLES.get(i['cycle'], i['cycle'])} "
                  f"state={STATES.get(i['state'], i['state'])} keys[{i['begin']:.3f},{i['end']:.3f}] "
                  f"freq={i['freq']:.4f} "
                  f"lastTime={i['lastTime']:.3f} blocks={i['n']} accumRoot={i['accum']!r}"
                  f"{'' if i['accumRoot'] else ' (unbound)'}")
            if a.blocks:
                for nm, interp in L.seq_items(i):
                    if not interp:
                        print(f'      {nm!r}: (no interpolator)')
                        continue
                    try:
                        tr, q, sc, data = L.interp_pose(interp)
                    except Exception:
                        print(f'      {nm!r}: interp {interp:#x}')
                        continue
                    def f(v):
                        return 'NONE' if v <= NO_VALUE else f'{v:.3g}'
                    print(f"      {nm!r}: pose t=({f(tr[0])},{f(tr[1])},{f(tr[2])}) "
                          f"q=({f(q[0])},{f(q[1])},{f(q[2])},{f(q[3])}) s={f(sc)} "
                          f"data={'yes' if data else 'no'}")
    return 0


# NiParticleSystem vtable RVA (GOG/AE build; used only as a HINT -- the
# command falls back to name matching, and prints the live vtable so a build
# mismatch is obvious rather than silent).
_PSYS_VTABLE_RVA = 0x1869880


def cmd_particles(a):
    """Sample the LIVE particle systems under a reference.

    Answers the question static analysis kept getting wrong: when a converted
    effect "plays for a split second and then nothing", are particles still
    being BORN (emitter running, they die too fast / are invisible) or does
    the emitter STOP (nothing to draw)?

    Prints, per system and per sample: the node's flags (bit 0 = hidden), its
    world position, and a window of raw u16/u32 counters near the data block.
    The counter that tracks the visible spray is the one that rises while the
    effect is on screen -- reading it live is the only reliable way to pin the
    runtime field, since NiPSysData's in-memory layout is not the file layout.
    """
    names = [n.strip() for n in a.names.split(',')] if a.names else None
    with Bridge() as b:
        L = Live(b)
        root = L.root_3d(int(a.ref, 16))
        systems = []
        for node, nm, _, parent in L.walk(root):
            if names is not None:
                if nm not in names:
                    continue
            else:
                try:
                    vt = L.u64(node) - 0x140000000
                except Exception:
                    continue
                if vt != _PSYS_VTABLE_RVA:
                    continue
            systems.append((nm, node))
        if not systems:
            print('no particle systems found; pass --names to select by node name')
            return 1
        for nm, node in systems:
            try:
                vt = L.u64(node) - 0x140000000
            except Exception:
                vt = 0
            print(f'{nm!r} @ {node:#x} vtable_rva={vt:#x}')

        for k in range(a.samples):
            for nm, node in systems:
                d = L.rd(node, 0x160)
                flags = struct.unpack_from('<I', d, 0x2C)[0]
                wt = struct.unpack_from('<3f', d, 0xA0)
                # NiGeometry data pointer sits after the NiAVObject block; scan
                # a window of plausible pointers and report the small integers
                # each one leads to, so the live count field reveals itself by
                # CHANGING while the effect plays.
                counters = []
                for off in range(0x110, 0x160, 8):
                    p = struct.unpack_from('<Q', d, off)[0]
                    if p < 0x10000 or p > 0x7FFFFFFFFFFF:
                        continue
                    try:
                        blob = L.rd(p, 0x60)
                    except Exception:
                        continue
                    u16 = struct.unpack_from('<8H', blob, 0x10)
                    counters.append((off, [v for v in u16 if v < 5000]))
                print(f'  [{k}] {nm!r} flags={flags} (hidden={flags & 1}) '
                      f'pos=({wt[0]:.1f},{wt[1]:.1f},{wt[2]:.1f}) '
                      f'counters={counters}', flush=True)
            if k + 1 < a.samples:
                time.sleep(a.interval)
    return 0


def _watch_rate(L, seqs, a):
    """Sample seq+0x50 `lastTime` against the wall clock to get the REAL rate.

    Answers "the animation plays, but far too fast": rate = d(lastTime)/d(wall).
    1.0 means the sequence advances one second of animation per second of real
    time (correct).  N>1 means it is running N times too fast -- and because
    the engine's only divisor is the sequence's own `frequency` (the duration
    getter at exe 0x5050a0 computes (end-begin)/freq), a rate of N with
    freq==1.0 means the TIME BEING FED IN is inflated, not the sequence data.

    Prints per-sample deltas so a mid-run restart (lastTime jumping backwards,
    i.e. a loop wrap) is visible rather than averaged away.
    """
    infos = [L.seq_info(s) for s in seqs]
    if a.sequence:
        infos = [i for i in infos if i['name'].lower() == a.sequence.lower()]
        if not infos:
            raise SystemExit(f'no sequence named {a.sequence!r} on this reference')
    for i in infos:
        span = i['end'] - i['begin']
        print(f"{i['name']!r}: keys[{i['begin']:.3f},{i['end']:.3f}] span={span:.3f}s "
              f"freq={i['freq']:.4f} cycle={CYCLES.get(i['cycle'], i['cycle'])}")
    print(f'sampling {a.samples}x every {a.interval}s '
          f'(rate 1.0 = correct; N = N times too fast)', flush=True)

    prev = {i['addr']: (time.perf_counter(), i['lastTime']) for i in infos}
    totals = {i['addr']: [0.0, 0.0] for i in infos}   # [anim, wall]
    for k in range(a.samples):
        time.sleep(a.interval)
        for i in infos:
            cur = L.seq_info(i['addr'])
            t1, w1 = time.perf_counter(), cur['lastTime']
            t0, w0 = prev[i['addr']]
            dt, dw = t1 - t0, w1 - w0
            prev[i['addr']] = (t1, w1)
            state = STATES.get(cur['state'], cur['state'])
            if dw < 0:
                note = ' (wrapped/restarted)'
            else:
                totals[i['addr']][0] += dw
                totals[i['addr']][1] += dt
                note = ''
            rate = (dw / dt) if dt > 0 else float('nan')
            raw = ' '.join(f'{v:.3f}' for v in cur['raw'])
            print(f"  [{k}] {cur['name']!r} state={state} lastTime={w1:.4f} "
                  f"d_anim={dw:+.4f}s d_wall={dt:.4f}s rate={rate:.2f}x{note} "
                  f"raw+54..64=[{raw}]",
                  flush=True)
    print()
    for i in infos:
        anim, wall = totals[i['addr']]
        if wall > 0:
            print(f"{i['name']!r}: mean rate {anim / wall:.2f}x over {wall:.2f}s "
                  f"(1.00x = correct)")
    return 0


def _find_seq(L, root, name):
    for s in L.sequences(L.manager(root)):
        i = L.seq_info(s)
        if i['name'].lower() == name.lower():
            return i
    raise SystemExit(f'no sequence named {name!r} on this reference')


def cmd_set_cycle(a):
    with Bridge() as b:
        L = Live(b)
        root = L.root_3d(int(a.ref, 16))
        i = _find_seq(L, root, a.sequence)
        b.writemem(i['addr'] + 0x40, struct.pack('<I', a.cycle))
        after = struct.unpack('<I', L.rd(i['addr'] + 0x40, 4))[0]
        print(f"{i['name']}: cycleType {CYCLES.get(i['cycle'])} -> {CYCLES.get(after)}")
    return 0


def cmd_set_pose(a):
    with Bridge() as b:
        L = Live(b)
        root = L.root_3d(int(a.ref, 16))
        i = _find_seq(L, root, a.sequence)
        hit = [(nm, ip) for nm, ip in L.seq_items(i) if nm == a.node and ip]
        if not hit:
            raise SystemExit(f'{a.node!r} is not a controlled block of {i["name"]!r}')
        for nm, ip in hit:
            tr, q, sc, data = L.interp_pose(ip)
            if data:
                raise SystemExit(f'{nm!r} has key data; only pose (data-less) interpolators are patched')
            if a.identity:
                payload = struct.pack('<3f', 0, 0, 0) + struct.pack('<4f', 1, 0, 0, 0) + struct.pack('<f', 1.0)
            else:
                payload = struct.pack('<8f', *([NO_VALUE] * 8))
            b.writemem(ip + 0x18, payload)
            tr2, q2, sc2, _ = L.interp_pose(ip)
            print(f'{nm!r} interp {ip:#x}: t={tr}->{tr2} q={q}->{q2} s={sc:.3g}->{sc2:.3g}')
    return 0


def cmd_sae(a):
    with Bridge() as b:
        out = b.console(f'sae {a.event}', ref=int(a.ref, 16))
        print(out if out else '(accepted: empty reply)')
    return 0


def _ref_parser(sub, name, fn, first_person=False):
    """A subcommand taking a reference FormID; `--first-person` optional."""
    s = sub.add_parser(name)
    s.add_argument('ref')
    if first_person:
        s.add_argument('--first-person', action='store_true',
                       help="the player's first-person 3D (Get3D(true))")
    s.set_defaults(fn=fn)
    return s


def _sampled(s, samples, interval=0.25):
    """Add the --samples/--interval pair to a subcommand."""
    s.add_argument('--samples', type=int, default=samples)
    s.add_argument('--interval', type=float, default=interval)


def _parser():
    """The command-line parser."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    _ref_parser(sub, 'tree', cmd_tree, True).add_argument('--depth', type=int, default=4)
    s = _ref_parser(sub, 'nodes', cmd_nodes, True)
    s.add_argument('--names', required=True)
    _sampled(s, 1, 0.4)
    s = _ref_parser(sub, 'sequences', cmd_sequences)
    s.add_argument('--blocks', action='store_true')
    s.add_argument('--watch', action='store_true',
                   help='sample lastTime over time and report the REAL playback '
                        'rate (1.0 = correct, N = N times too fast)')
    s.add_argument('--sequence', help='with --watch: only this sequence')
    _sampled(s, 20)
    s = _ref_parser(sub, 'particles', cmd_particles)
    s.add_argument('--names', help='comma-separated particle-system node names '
                                   '(default: find by NiParticleSystem vtable)')
    _sampled(s, 20)
    s = _ref_parser(sub, 'set-cycle', cmd_set_cycle)
    s.add_argument('sequence')
    s.add_argument('cycle', type=int, choices=(0, 1, 2))
    s = _ref_parser(sub, 'set-pose', cmd_set_pose)
    s.add_argument('sequence')
    s.add_argument('node')
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument('--identity', action='store_true')
    g.add_argument('--sentinel', action='store_true')
    _ref_parser(sub, 'sae', cmd_sae).add_argument('event')
    return p


def main(argv=None):
    """Run the subcommand named on the command line; its exit code."""
    a = _parser().parse_args(argv)
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())

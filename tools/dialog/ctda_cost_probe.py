#!/usr/bin/env python3
"""Measure what CONDITION EVALUATION actually costs in the running game.

WHY THIS EXISTS
---------------
NPC activation and a scripted Say() share exactly one expensive engine step:
the engine walks the candidate INFOs of a topic and evaluates each one's CTDA
stack until one qualifies.  Everything else about the two paths differs, so a
stutter common to BOTH points here.  Papyrus-side timing cannot see it (the
walk is native), and stack sampling cannot be used -- taking thousands of
`~* k` stack walks suspends every thread and FREEZES the game (measured
2026-08-16; the attach is non-invasive, the sampling is not).

So measure it where it happens: hook the condition evaluator and count.

THE TARGET
----------
`TESConditionItem::IsTrue` -- Address Library id **29924** (1.6.1170 rva
0x4a05e0), called once per CTDA actually evaluated.  It is the one function
that dispatches through the condition handler column (+0x40) of the script
function table (1.6.1170 rva 0x1fdba10, stride 0x50); its only list-walking
caller is `TESCondition::IsTrue` (id 29895).

The earlier target, id 21971, also indexes that table but is the script
COMPILER ("Syntax Error.  Undefined function '%s'."), so every count it
produced was meaningless.
See: docs/commentary/tes5_import_conditions.md#engine-evaluation

USE
---
Bridge must be up (game under skse64_loader.exe with TESGameBridge.dll).

    # 1. verify the target resolves and is safe to hook (DRY RUN, no hook)
    python tools/dialog/ctda_cost_probe.py --analyze

    # 2. arm it, then play until the stutter happens
    python tools/dialog/ctda_cost_probe.py --arm

    # 3. read the counter -- how many condition evaluations since arming
    python tools/dialog/ctda_cost_probe.py --read

    # bracket ONE action (activate an NPC, trigger a Say) and count it:
    python tools/dialog/ctda_cost_probe.py --bracket 8
    #   -> "press activate now", counts evaluations over 8 seconds

    # WHICH conditions, on WHOM, and how long the main thread froze meanwhile
    python tools/dialog/ctda_cost_probe.py --attribute 40

    # 4. always remove the hook when done
    python tools/dialog/ctda_cost_probe.py --remove

WHAT THE NUMBER MEANS
---------------------
The converted Oblivion.esm carries 311,739 INFO CTDAs (mean 18, max 753);
vanilla Skyrim.esm carries 55,641 across 31,465 INFOs (mean 1.8).  The count
is per CTDA evaluated, so short-circuiting (first failed AND, first passed OR)
is already reflected in it.

  * ~thousands of evaluations per activation  -> the walk IS the stutter, and
    the fix is to cut the per-topic condition volume.
  * ~tens                                     -> the engine rejects topics
    cheaply before the walk; condition volume is NOT the cause and this whole
    line of investigation is dead.  Say so and look elsewhere.

That is a decidable question, which is the point.

🛑 A hook on a HOT function can crash the game (a compile-finalizer hook did,
2026-08-14).  --analyze first, ALWAYS, and --remove when finished.
"""

from __future__ import annotations

import argparse
import ctypes
import struct
import sys
import threading
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.esm.tes5_esm_reader import ctda_func_name
from tools.live.game_bridge import Bridge, BridgeError
from tools.live.win_stackwalk import (dbghelp, find_pid, k32, module_map, open_proc,
                                      resolve, walk_thread, window_tree)

#: TESConditionItem::IsTrue -- see the module docstring for how it was identified.
CTDA_EVAL_ID = 29924
LABEL = "ctda_eval"
#: Condition run-context builder (1.6.1170 rva 0x49f130): (ctx, subject ref, target ref, owning form).
CTDA_CTX_ID = 29871
CTX_LABEL = "ctda_ctx"
#: A main-thread round trip slower than this is reported as a stall.
STALL_MS = 100.0


def _hits(b: Bridge, label: str = LABEL) -> int:
    """Total recorded calls for our hook, 0 if not installed."""
    entry = _entry(b, label)
    return int(entry.get("hits", 0)) if entry else 0


def _entry(b: Bridge, label: str = LABEL) -> dict | None:
    """Our row in the generic-hook listing; `hookstats` never lists generic hooks."""
    try:
        listing = b.request("hook")
    except BridgeError:
        return None
    for entry in (listing.get("hooks") or []):
        if entry.get("label") == label:
            return entry
    return None


def _unhook(b: Bridge, label: str = LABEL) -> bool:
    """Remove our hook if installed; True when one was removed."""
    entry = _entry(b, label)
    if entry is None:
        return False
    print(b.hook(remove=entry.get("index")))
    return True


def cmd_analyze(b: Bridge) -> int:
    r = b.resolve(id=CTDA_EVAL_ID)
    print(f"id {CTDA_EVAL_ID} -> {r}")
    a = b.hook(id=CTDA_EVAL_ID, label=LABEL, analyze=True)
    print(f"analyze: {a}")
    ok = a.get("hookable")
    print("\nSAFE to hook" if ok else
          "\nNOT hookable -- do not force it; the blocking instruction is above")
    return 0 if ok else 1


def cmd_arm(b: Bridge) -> int:
    a = b.hook(id=CTDA_EVAL_ID, label=LABEL, analyze=True)
    if not a.get("hookable"):
        print(f"refusing to hook: {a}")
        return 1
    r = b.hook(id=CTDA_EVAL_ID, label=LABEL, keep=0)
    print(f"armed: {r}")
    print(f"baseline hits: {_hits(b)}")
    print("\nPlay now. Then: --read (or --bracket) ... and --remove when done.")
    return 0


def cmd_read(b: Bridge) -> int:
    print(f"condition evaluations recorded: {_hits(b)}")
    return 0


def cmd_bracket(b: Bridge, seconds: float) -> int:
    """Count evaluations across one deliberate action."""
    if _hits(b) == 0:
        try:
            b.hook(id=CTDA_EVAL_ID, label=LABEL, keep=0)
        except BridgeError as e:
            print(f"could not arm: {e}")
            return 1
    before = _hits(b)
    print(f"\n>>> DO IT NOW -- activate the NPC / trigger the line "
          f"({seconds:.0f}s window) <<<", flush=True)
    time.sleep(seconds)
    after = _hits(b)
    delta = after - before
    print(f"\ncondition evaluations during window: {delta}")
    if delta > 2000:
        print("  -> THOUSANDS: the condition walk is the cost. Cutting "
              "per-topic\n     CTDA volume is the fix.")
    elif delta > 0:
        print("  -> modest: the engine is NOT walking every INFO. Condition "
              "volume\n     is not the stutter; look elsewhere.")
    else:
        print("  -> ZERO: hook never fired. Wrong function, or nothing "
              "happened in\n     the window. Do not interpret this as 'cheap'.")
    return 0


def cmd_remove(b: Bridge) -> int:
    """Remove the counter hook."""
    if not _unhook(b):
        print("no hook installed under this label")
    return 0


def cmd_measure(b: Bridge, window: float, samples: int) -> int:
    """Whole measurement on ONE connection: analyze, arm, sample, remove.

    🛑 ONE CONNECTION, NOT FIVE.  The plugin's pipe server is strictly serial
    (pipe_server.cpp): on disconnect it does DisconnectNamedPipe / CloseHandle
    and only THEN loops round to CreateNamedPipeA, so between those two points
    no pipe instance exists and a client connecting in that window fails with
    error 3 / errno 22.  Running analyze/arm/read as separate processes hit
    exactly that race -- --analyze succeeded and the --arm seconds later
    "could not connect", which looks like the game is down when it is fine.
    Retrying is the wrong fix; not disconnecting is the right one.
    """
    r = b.resolve(id=CTDA_EVAL_ID)
    if not r.get("found"):
        print(f"target id {CTDA_EVAL_ID} did not resolve: {r}")
        return 1
    a = b.hook(id=CTDA_EVAL_ID, label=LABEL, analyze=True)
    if not a.get("hookable"):
        print(f"refusing to hook (not relocatable): {a}")
        return 1
    print(f"target ok: rva 0x{r['rva']:x}, stolen {a.get('stolen')} bytes")

    print(b.hook(id=CTDA_EVAL_ID, label=LABEL, keep=0).get("status", "armed"))
    try:
        base = _hits(b)
        print(f"\n>>> PLAY NOW -- sampling {samples} windows of {window:.0f}s "
              f"<<<\n", flush=True)
        prev = base
        peak = 0
        for i in range(samples):
            time.sleep(window)
            now = _hits(b)
            delta = now - prev
            prev = now
            peak = max(peak, delta)
            print(f"  [{(i+1)*window:5.0f}s] +{delta:>9,} evals "
                  f"({delta/window:>10,.0f}/s)", flush=True)
        total = prev - base
        print(f"\ntotal condition evaluations: {total:,} over "
              f"{samples*window:.0f}s")
        print(f"peak window rate: {peak/window:,.0f}/s")
    finally:
        print("hook removed" if _unhook(b) else "HOOK NOT FOUND -- run --remove")
    return 0


class _ProcReader:
    """ReadProcessMemory on the running game from outside it; never suspends a thread."""

    def __init__(self):
        self.pid = find_pid("SkyrimSE")
        self._h = open_proc(self.pid)

    def read(self, addr: int, n: int) -> bytes | None:
        """n bytes at addr, or None when the page is not readable."""
        buf = ctypes.create_string_buffer(n)
        got = ctypes.c_size_t()
        ok = k32.ReadProcessMemory(ctypes.c_void_p(self._h), ctypes.c_void_p(addr),
                                   buf, ctypes.c_size_t(n), ctypes.byref(got))
        return buf.raw if ok and got.value == n else None

    def qword(self, addr: int) -> int:
        """The pointer-sized value at addr, 0 when unreadable."""
        raw = self.read(addr, 8) if addr > 0x10000 else None
        return struct.unpack("<Q", raw)[0] if raw else 0

    def form_id(self, form: int) -> str | None:
        """FormID (TESForm+0x14) of a form pointer, None when it is not one."""
        raw = self.read(form + 0x14, 4) if form > 0x10000 else None
        return f"{struct.unpack('<I', raw)[0]:08X}" if raw else None


class _StallCatcher(threading.Thread):
    """One main-thread stack snapshot per main-thread round trip that runs 200ms overdue.

    The thread is already frozen when this fires, so the single suspend costs
    nothing visible; it is never a sampling loop over a running game.
    """

    def __init__(self, p: _ProcReader):
        """Target the thread that owns the game window (the main thread)."""
        super().__init__(daemon=True)
        self._h = p._h
        self._mods = module_map(p._h)
        self._tid = next(row[2] for row in window_tree(p.pid))
        dbghelp.SymInitialize(p._h, None, True)
        self.pending = 0.0
        self.stack: list[str] = []
        self.stop = False

    def run(self) -> None:
        """Watch `pending` (the start time of the in-flight round trip)."""
        while not self.stop:
            started = self.pending
            if started and not self.stack and time.perf_counter() - started > 0.2:
                frames = walk_thread(self._h, self._tid, self._mods, 40) or []
                self.stack = [f"{m}+{rva:#x}" if m else hex(pc)
                              for pc, (m, rva) in ((pc, resolve(self._mods, pc)) for pc in frames)]
            time.sleep(0.02)


def _decode(p: _ProcReader, item: int) -> str | None:
    """'Func(param1)' for one sampled IsTrue call (item: function u16 at +0x18, param 1 at +0x20)."""
    raw = p.read(item + 0x18, 0x10)
    if raw is None:
        return None
    func = struct.unpack_from("<H", raw, 0)[0]
    param = struct.unpack_from("<Q", raw, 8)[0]
    return f"{ctda_func_name(func)}({p.form_id(param) or hex(param & 0xFFFFFFFF)})"


def _decode_ctx(p: _ProcReader, subject: int, form: int) -> tuple[str, str]:
    """('ref/base' of the subject, 'type:FormID' of the owning form) for one context build."""
    base = p.qword(subject + 0x40) if subject else 0
    kind = p.read(form + 0x1A, 1) if form > 0x10000 else None
    owner = f"type{kind[0]}:{p.form_id(form)}" if kind else "-"
    return f"{p.form_id(subject) or '-'}/{p.form_id(base) or '-'}", owner


def _ring(b: Bridge, label: str) -> tuple[int, list]:
    """(hits, sampled argument sets) of one hook, thinned to at most 400 calls."""
    entry = _entry(b, label) or {}
    calls = entry.get("calls") or []
    return int(entry.get("hits", 0)), calls[::max(1, len(calls) // 400)]


def _sample_window(b: Bridge, p: _ProcReader, last_hits: int, catcher: _StallCatcher) -> dict:
    """One poll: main-thread round trip, eval count since last poll, decoded ring samples."""
    catcher.stack = []
    t0 = catcher.pending = time.perf_counter()
    try:
        b.status()
    finally:
        catcher.pending = 0.0
    stall = (time.perf_counter() - t0) * 1000.0
    hits, calls = _ring(b, LABEL)
    conds = Counter(filter(None, (_decode(p, int(c[0])) for c in calls)))
    subjects, owners = Counter(), Counter()
    for c in _ring(b, CTX_LABEL)[1]:
        who, owner = _decode_ctx(p, int(c[1]), int(c[3]))
        subjects[who] += 1
        owners[owner] += 1
    return {"stall": stall, "hits": hits, "evals": hits - last_hits, "stack": catcher.stack,
            "conds": conds, "subjects": subjects, "owners": owners}


def _print_top(title: str, windows: list[dict], key: str, n: int) -> None:
    """The n most frequent sampled entries of `key` across windows."""
    total = Counter()
    for w in windows:
        total.update(w[key])
    seen = sum(total.values()) or 1
    print(f"\n{title} ({len(windows)} windows, {seen} samples)")
    for name, count in total.most_common(n):
        print(f"  {100.0 * count / seen:5.1f}%  {name}")


def _arm_hooks(b: Bridge, keep: int) -> bool:
    """Install the evaluator hook (required) and the context hook (optional)."""
    for sid, label in ((CTDA_EVAL_ID, LABEL), (CTDA_CTX_ID, CTX_LABEL)):
        a = b.hook(id=sid, label=label, analyze=True)
        if not a.get("hookable"):
            print(f"refusing to hook {label} (not relocatable): {a}")
            if label == LABEL:
                return False
            continue
        b.hook(id=sid, label=label, keep=keep)
    return True


def _print_window(w: dict) -> None:
    """One poll line; a stall also gets its top conditions, owners, subjects and stack."""
    flag = "  <-- STALL" if w["stall"] > STALL_MS else ""
    print(f"{time.strftime('%H:%M:%S')} main thread {w['stall']:7.0f} ms"
          f"   +{w['evals']:>9,} evals{flag}", flush=True)
    if flag:
        for key in ("conds", "owners", "subjects"):
            print(f"    {key}: {w[key].most_common(6)}", flush=True)
        print(f"    main-thread stack: {' < '.join(w['stack'][:30])}", flush=True)


def cmd_attribute(b: Bridge, seconds: float, keep: int) -> int:
    """Which conditions run, on which subject, while the main thread stalls.

    Polls every 0.25s: a `status` round trip runs on the main thread, so its
    latency is how long the game was frozen.  The evaluator's argument ring says
    which conditions ran; the context builder's ring (its args are registers,
    not a stack struct that is gone by the time it is read) says on which
    subject and for which owning form.
    """
    if not _arm_hooks(b, keep):
        return 1
    p = _ProcReader()
    catcher = _StallCatcher(p)
    catcher.start()
    windows = []
    try:
        hits = _hits(b)
        print(f"\n>>> PLAY NOW -- {seconds:.0f}s: talk to an NPC, trigger a scripted line <<<\n",
              flush=True)
        end = time.time() + seconds
        while time.time() < end:
            time.sleep(0.25)
            try:
                w = _sample_window(b, p, hits, catcher)
            except BridgeError as e:
                print(f"  poll failed: {e}", flush=True)
                continue
            hits = w["hits"]
            windows.append(w)
            _print_window(w)
    finally:
        catcher.stop = True
        for label in (LABEL, CTX_LABEL):
            print(f"{label} removed" if _unhook(b, label) else f"{label} NOT FOUND -- remove it")
    busy = sorted(windows, key=lambda w: -w["evals"])[:max(1, len(windows) // 10)]
    for key, n in (("conds", 25), ("owners", 15), ("subjects", 12)):
        _print_top(f"BUSIEST 10% of windows: {key}", busy, key, n)
        _print_top(f"ALL windows: {key}", windows, key, n)
    return 0


def _connect_when_loaded() -> Bridge:
    """A bridge connection, opened once the game is up and past the main menu."""
    print("waiting for the game to load a save / start a new game ...", flush=True)
    while True:
        try:
            b = Bridge().connect(retries=1)
        except BridgeError:
            time.sleep(5.0)
            continue
        try:
            if b.status().get("game_loaded"):
                return b
        except BridgeError:
            pass
        b.close()
        time.sleep(5.0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--analyze", action="store_true",
                   help="dry run: resolve the target and check hookability")
    g.add_argument("--arm", action="store_true", help="install the counter hook")
    g.add_argument("--read", action="store_true", help="read the counter")
    g.add_argument("--bracket", type=float, metavar="SECONDS",
                   help="count evaluations across one action")
    g.add_argument("--remove", action="store_true", help="remove the hook")
    g.add_argument("--measure", action="store_true",
                   help="★ the whole measurement on ONE connection: analyze, "
                        "arm, sample per window, unhook. Use this.")
    g.add_argument("--attribute", type=float, metavar="SECONDS",
                   help="sample WHICH conditions run and on WHOM, with main-thread "
                        "stall per 0.25s poll; ranks the busiest windows")
    ap.add_argument("--keep", type=int, default=4000,
                    help="argument-ring size for --attribute")
    ap.add_argument("--window", type=float, default=5.0,
                    help="seconds per sample window (--measure)")
    ap.add_argument("--samples", type=int, default=12,
                    help="number of windows (--measure)")
    ap.add_argument("--wait", action="store_true",
                    help="block until the game is running and loaded, then start")
    args = ap.parse_args(argv)

    try:
        with (_connect_when_loaded() if args.wait else Bridge().connect(retries=2)) as b:
            if args.analyze:
                return cmd_analyze(b)
            if args.arm:
                return cmd_arm(b)
            if args.read:
                return cmd_read(b)
            if args.bracket is not None:
                return cmd_bracket(b, args.bracket)
            if args.remove:
                return cmd_remove(b)
            if args.measure:
                return cmd_measure(b, args.window, args.samples)
            if args.attribute is not None:
                return cmd_attribute(b, args.attribute, args.keep)
    except BridgeError as e:
        print(f"bridge unavailable: {e}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

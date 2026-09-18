#!/usr/bin/env python3
"""Client for the in-game bridge (game_bridge/ SKSE plugin).

Drives a RUNNING SkyrimSE.exe over a named pipe: run console commands, inspect
what the engine actually built, and reload assets without relaunching. The point
is to collapse the build-and-play round trip for creature/ragdoll work, where
the defect is usually a *silent binding failure* -- valid file, offline
validator passes, actor spawns, engine binds nothing. Only the engine can
report that, so we ask it directly.

Setup (once):
    game_bridge\\build.bat deploy      # build + copy the DLL into Data/SKSE/Plugins
    (start the game through skse64_loader.exe)

Usage:
    # is the bridge alive?
    python tools/live/game_bridge.py ping

    # what resolved on this runtime?
    python tools/live/game_bridge.py capabilities

    # run a console command
    python tools/live/game_bridge.py console "player.placeatme 0x00023ABC 1"

    # with a selected reference
    python tools/live/game_bridge.py console "getpos z" --ref 0x0001A2B3

    # several commands in ONE main-thread trip (the game does not advance
    # between them, so select/mutate/read stays coherent)
    python tools/live/game_bridge.py batch "prid 1a2b3c" "getav health" "getpos z"

    # make the game write to the Papyrus log, then read it back
    python tools/live/game_bridge.py dumpstacks
    python tools/script/papyrus_tail.py since --cursor <n>

    # where are we?
    python tools/live/game_bridge.py status

    # raw JSON, for scripting
    python tools/live/game_bridge.py --json status

As a library:
    from tools.live.game_bridge import Bridge
    with Bridge() as b:
        b.console("coc BridgeTestCell")
        print(b.status())
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PIPE_NAME = r"\\.\pipe\tes_game_bridge"
DEFAULT_TIMEOUT = 10.0


class BridgeError(RuntimeError):
    """A structured error returned by the plugin, or a transport failure."""

    def __init__(self, message: str, code: str = "E_CLIENT"):
        super().__init__(message)
        self.code = code


class Bridge:
    """Connection to the in-game bridge.

    One client at a time: the plugin refuses a second connection rather than
    queueing it, so two sessions cannot interleave state mutations.
    """

    def __init__(self, pipe: str = PIPE_NAME, timeout: float = DEFAULT_TIMEOUT,
                 loading_retries: int = 3, loading_retry_delay: float = 1.0):
        self.pipe_name = pipe
        self.timeout = timeout
        self.loading_retries = loading_retries
        self.loading_retry_delay = loading_retry_delay
        self._f = None
        self._next_id = 1

    # ------------------------------------------------------------- connect --

    def connect(self, retries: int = 1, retry_delay: float = 0.5) -> "Bridge":
        last = None
        for attempt in range(max(1, retries)):
            try:
                # A named pipe behaves like a file; buffering=0 keeps request
                # and response strictly paired.
                self._f = open(self.pipe_name, "r+b", buffering=0)
                return self
            except OSError as exc:  # pipe missing (game not running / no plugin)
                last = exc
                if attempt + 1 < retries:
                    time.sleep(retry_delay)
        raise BridgeError(
            f"could not connect to {self.pipe_name}: {last}. "
            "Is the game running under skse64_loader.exe with TESGameBridge.dll "
            "in Data/SKSE/Plugins?",
            "E_NO_PIPE",
        )

    def close(self) -> None:
        if self._f:
            try:
                self._f.close()
            finally:
                self._f = None

    def __enter__(self) -> "Bridge":
        if not self._f:
            self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------ requests --

    def request(self, cmd: str, **args) -> dict:
        """Send one command, return its `result` dict. Raises on error.

        E_LOADING is retried rather than raised. Windows throttles a
        background window, so while the game does not have focus its main
        thread stops draining SKSE's task queue and every marshalled command
        times out -- which is the normal state whenever the user alt-tabs to
        talk to us. Retrying turns a spurious failure into a short wait.
        """
        last: BridgeError | None = None
        for attempt in range(self.loading_retries):
            try:
                return self._request_once(cmd, **args)
            except BridgeError as exc:
                if exc.code != "E_LOADING":
                    raise
                last = exc
                if attempt + 1 < self.loading_retries:
                    time.sleep(self.loading_retry_delay)
        raise last if last else BridgeError("request failed", "E_INTERNAL")

    def _request_once(self, cmd: str, **args) -> dict:
        if not self._f:
            self.connect()

        req = {"id": self._next_id, "cmd": cmd}
        self._next_id += 1
        if args:
            req["args"] = args

        try:
            self._f.write((json.dumps(req) + "\n").encode("utf-8"))
            line = self._readline()
        except OSError as exc:
            self.close()
            raise BridgeError(f"transport failure: {exc}", "E_TRANSPORT") from exc

        try:
            resp = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BridgeError(f"malformed response: {line[:200]!r}", "E_PROTOCOL") from exc

        if not resp.get("ok"):
            raise BridgeError(
                resp.get("error", "unknown error"), resp.get("code", "E_INTERNAL")
            )
        return resp.get("result", {})

    def _readline(self) -> str:
        chunks = []
        while True:
            b = self._f.read(1)
            if not b:
                raise OSError("pipe closed by the game")
            if b == b"\n":
                break
            chunks.append(b)
        return b"".join(chunks).decode("utf-8", "replace")

    # ------------------------------------------------------------ commands --

    def ping(self) -> dict:
        return self.request("ping")

    def capabilities(self) -> dict:
        return self.request("capabilities")

    def status(self) -> dict:
        return self.request("status")

    def console(self, command: str, ref: str | int | None = None) -> str:
        args = {"command": command}
        if ref is not None:
            args["ref"] = ref
        return self.request("console", **args).get("output", "")

    def batch(
        self,
        commands: list[str],
        ref: str | int | None = None,
        stop_on_error: bool = True,
    ) -> dict:
        """Run several console commands in ONE main-thread trip.

        The game does not advance between them, so a select/mutate/read
        sequence stays coherent. Only the first command applies `ref`; the
        console's selection persists through the rest.
        """
        args: dict = {"commands": list(commands), "stop_on_error": stop_on_error}
        if ref is not None:
            args["ref"] = ref
        return self.request("batch", **args)

    def inject(
        self,
        script: str,
        ref: str | int | None = None,
        stop_on_error: bool = True,
        settle_ms: int = 250,
    ) -> dict:
        """Compile and run a multi-statement script body inside the game.

        The text goes through the engine's own script compiler, so this is real
        injection rather than command dispatch. Statements (newline- or
        ';'-separated) run in order against one selected reference, inside a
        single main-thread trip -- the game cannot advance between them.

        The returned dict carries a "papyrus" list: everything the VM emitted
        during the run, captured from the logger sink. `settle_ms` is how long
        to wait for the VM to flush before collecting it (Papyrus is
        asynchronous, so a script's output can land a frame or two late).
        """
        args: dict = {"script": script, "stop_on_error": stop_on_error,
                      "settle_ms": settle_ms}
        if ref is not None:
            args["ref"] = ref
        return self.request("inject", **args)

    # ------------------------------------------------ clean-room testing --
    # These back tools/live/quest_labtest.py. See docs/commentary/ingame_testing.md.

    def spawn(self, form_id: str | int, count: int = 1) -> dict:
        """Spawn copies of a base form, for clean-room tests.

        NOTE: `placeatme` does not report the reference it creates, so the
        caller must record what appeared if it wants `cleanup` to remove it.
        The response says so explicitly rather than implying the spawn is
        tracked -- a spawn you cannot delete silently bloats the save.
        """
        return self.request("spawn", form_id=form_id, count=count)

    def cleanup(self, refs: list[str | int] | None = None) -> dict:
        """Disable + markfordelete the given (or tracked) references."""
        return self.request("cleanup", refs=list(refs or []))

    def moveref(self, ref: str | int, x: str | None = None,
                y: str | None = None, z: str | None = None) -> dict:
        """Move a reference to the player, or back to an explicit position.

        Reads the CURRENT position and moves in one main-thread trip, so the
        recorded `before` is the position the ref actually had at the moment it
        moved -- which is what makes a later restore an undo rather than a
        guess.
        """
        args: dict = {"ref": ref}
        for k, v in (("x", x), ("y", y), ("z", z)):
            if v is not None:
                args[k] = v
        return self.request("moveref", **args)

    def wait_ready(self, timeout_ms: int = 20000) -> dict:
        """Block until the game answers again after a load screen.

        `coc` starts a load and every command issued during it fails with
        E_LOADING, so anything following a cell change must wait on this.
        """
        return self.request("wait_ready", timeout_ms=timeout_ms)

    # -------------------------------------------------- raw probing --------
    # These let a theory about engine internals be tested from Python against
    # the LIVE process, with no rebuild and no game restart. That round trip
    # is the whole cost this bridge exists to remove, so anything that would
    # otherwise need "add a log line, rebuild, relaunch" belongs here instead.

    def resolve(self, id: int | None = None, rva: int | None = None) -> dict:
        """Resolve an Address Library stable id (or an rva) to a live address."""
        args = {}
        if id is not None:
            args["id"] = id
        if rva is not None:
            args["rva"] = rva
        return self.request("resolve", **args)

    def readmem(self, address: int | None = None, rva: int | None = None,
                id: int | None = None, length: int = 64,
                as_string: bool = False) -> dict:
        """Read bytes (or a NUL-terminated string) from the live process."""
        args: dict = {"len": length, "as_string": as_string}
        if address is not None:
            args["address"] = str(address)
        if rva is not None:
            args["rva"] = str(rva)
        if id is not None:
            args["id"] = id
        return self.request("readmem", **args)

    def call(self, address: int | None = None, rva: int | None = None,
             id: int | None = None, args_: list[int] | None = None,
             float_args: int = 0, float_result: bool = False) -> dict:
        """Call a function with up to 4 integer/pointer args; returns RAX.

        A wrong signature can corrupt the game -- it is SEH-guarded so it kills
        the command rather than the session, but the risk is real. That is the
        deliberate trade for not needing a rebuild per experiment.
        """
        args: dict = {"args": [str(a) for a in (args_ or [])],
                      "float_args": float_args, "float_result": float_result}
        if address is not None:
            args["address"] = str(address)
        if rva is not None:
            args["rva"] = str(rva)
        if id is not None:
            args["id"] = id
        return self.request("call", **args)

    def writemem(self, address: int, data: bytes | str) -> dict:
        """Write bytes (or a NUL-terminated string) into the live process."""
        args: dict = {"address": str(address)}
        if isinstance(data, str):
            args["string"] = data
        else:
            args["bytes"] = list(data)
        return self.request("writemem", **args)

    def alloc(self, length: int = 256) -> int:
        """Allocate scratch memory inside the game; returns its address."""
        return int(self.request("alloc", len=length)["address"])

    def put_string(self, text: str) -> int:
        """Allocate a C string in the game and return its address.

        Engine functions take `const char*` into the game's own memory, so any
        command text we want to hand them has to live there first.
        """
        p = self.alloc(len(text.encode()) + 1)
        self.writemem(p, text)
        return p

    def find_literal(self, text: bytes | str, start: int = 0x1000000,
                     end: int = 0x2000000, step: int = 4096) -> int:
        """Find a NUL-terminated literal in the LOADED image; 0 if absent.

        RVAs move between game builds, so a offset read out of a disassembly of
        one build must never be trusted against another. Scanning the running
        image is the build-independent way to get a pointer to a string the
        engine already owns -- which matters because there is no write
        primitive, so any string handed to an engine function has to be one
        that already exists in its memory.
        """
        if isinstance(text, str):
            text = text.encode()
        needle = text + b"\x00"
        base = int(self.resolve(rva=0)["address"])
        for off in range(start, end, step):
            try:
                chunk = self.readmem(address=base + off, length=step)
            except BridgeError:
                continue  # unmapped hole; keep scanning
            hexs = chunk.get("hex")
            if not hexs:
                continue
            raw = bytes(int(x, 16) for x in hexs.split())
            i = raw.find(needle)
            if i >= 0:
                return base + off + i
        return 0

    # Cached across calls: locating Script::ctor costs a few dozen reads.
    _script_ctor: int | None = None

    def _find_script_ctor(self) -> int:
        """Locate Script::ctor by signature, scanning back from SetText.

        Its stable id (21874) is absent from the 1.6.1170 database, so it has
        to be found by shape. Cached because the scan is not free.
        """
        if self._script_ctor:
            return self._script_ctor
        set_text = int(self.resolve(id=21883)["address"])
        pat = bytes([0x40, 0x53, 0x48, 0x83, 0xEC, 0x20, 0x48, 0x8B, 0xD9, 0xE8])
        for off in range(0x400, 0x4000, 0x10):
            a = set_text - off
            try:
                raw = bytes(int(x, 16)
                            for x in self.readmem(address=a, length=16)["hex"].split())
            except BridgeError:
                continue
            if raw.startswith(pat):
                self._script_ctor = a
                return a
        raise BridgeError("could not locate Script::ctor by signature", "E_NOT_FOUND")

    def raw_console(self, command: str, context: int | None = None) -> dict:
        """Run a console command built ENTIRELY from raw primitives.

        Independent of the plugin's own console path, so a bug there can be
        diagnosed (or bypassed) without rebuilding the DLL -- which is the
        whole reason the primitives exist.

        `context` is ConsoleExecute's arg1. Passing 0/None makes the engine
        COMPILE the script and return success while never running it, so the
        captured execution context is used by default.
        """
        if context is None:
            context = int(self.hookstats().get("exec_context", 0) or 0)

        ctor = self._find_script_ctor()
        set_text = int(self.resolve(id=21883)["address"])
        exec_fn = int(self.resolve(id=21954)["address"])

        text = self.put_string(command)
        script = int(self.call(id=68115, args_=[0, 0x80, 0, 0])["result"])
        if not script:
            raise BridgeError("MemAlloc returned null", "E_INTERNAL")
        # NOTE: no memset -- zeroing an engine allocation breaks execution.
        self.call(address=ctor, args_=[script])
        self.call(address=set_text, args_=[script, text])

        before = self.hookstats()["console_print_hits"]
        r = self.call(address=exec_fn, args_=[context, script, 0, 1])
        after = self.hookstats()["console_print_hits"]

        return {
            "command": command,
            "returned": int(r["result"]),
            "print_hits": after - before,
            "context": context,
            "script": script,
        }

    def hook(self, address: int | None = None, rva: int | None = None,
             id: int | None = None, label: str = "hook",
             keep: int = 16, analyze: bool = False,
             index: int | None = None, remove: int | None = None) -> dict:
        """Install (or inspect) a generic hook at any address, from Python.

        This is what makes "does this function even run, and with what
        arguments?" answerable WITHOUT rebuilding the DLL and restarting the
        game -- the question that repeatedly cost a full cycle to guess at.

        analyze=True is a dry run: reports whether the target can be hooked
        safely and, if not, exactly which instruction blocks it. Relocating a
        position-dependent instruction has already crashed the game once, so
        the plugin refuses rather than trying.

        With no arguments, returns every installed hook and its recorded calls.
        Pass remove=<index> to restore a target's original bytes -- a hook you
        cannot take back is one you cannot safely experiment with.
        """
        args: dict = {"label": label, "keep": keep}
        if address is not None:
            args["address"] = str(address)
        if rva is not None:
            args["rva"] = str(rva)
        if id is not None:
            args["id"] = id
        if analyze:
            args["analyze"] = True
        if index is not None:
            args["index"] = index
        if remove is not None:
            args["remove"] = remove
        return self.request("hook", **args)

    def console_log(self, limit: int = 100) -> dict:
        """Recent console output, INCLUDING output the game produced itself.

        `console()` only returns what its own command printed. That makes an
        empty result ambiguous -- the hook might not be firing at all. This
        always-on ring settles it: if a command typed in-game shows up here,
        the capture path is healthy and the problem is elsewhere.
        """
        r = self.request("console_log", limit=limit)
        r.setdefault("lines", [])
        return r

    def console_capture(self, command: str, ref: str | int | None = None,
                        settle: float = 0.4, limit: int = 400) -> list[str]:
        """Run a command and return the lines IT produced.

        Diffs the ring's monotonic `seq` across the call rather than trusting
        the buffered count: once the ring is full that count stops growing, so
        a large result (an `sqv` on a real quest emits one line per alias --
        64 for CharacterGen) reads as no output at all.

        Falls back to `console()`'s own scoped capture when the plugin is too
        old to report a sequence.
        """
        try:
            before = self.console_log(limit=1).get("seq")
        except BridgeError:
            before = None

        out = self.console(command, ref)
        if before is None:
            return out.splitlines()

        time.sleep(settle)
        after = self.console_log(limit=limit)
        produced = int(after.get("seq", 0)) - int(before)
        lines = after.get("lines", [])
        if produced <= 0:
            return out.splitlines()
        return lines[-produced:] if produced <= len(lines) else lines

    @staticmethod
    def is_unknown_command(lines: list[str]) -> bool:
        """Did the engine reject the command name?

        The console reports a typo by PRINTING `Script command "x" not found.`
        -- the dispatcher's return value does not carry it (a good `getgs`
        returns 0, a typo returns 1). Without this check a misspelled command
        reads as a success, which makes every result untrustworthy rather than
        just the bad one.
        """
        return any('Script command "' in l and '" not found' in l for l in lines)

    def hookstats(self) -> dict:
        """How many times each hook has fired.

        Turns "capture is empty" into a decidable question without a rebuild:
        0 hits means the detour is on the wrong function; non-zero means the
        plumbing after it is at fault.
        """
        return self.request("hookstats")

    def vmlog(self, arm: bool = False, limit: int = 100,
              take: bool = False) -> dict:
        """Read Papyrus VM output captured straight from the logger sink.

        arm=True starts a fresh slice; a later call without arm returns
        everything the VM emitted in between. With nothing armed, returns the
        most recent lines from the always-on ring buffer.

        This is exact and unbuffered, unlike tailing Papyrus.0.log -- it can
        attribute output to one injection instead of guessing which lines in a
        shared file are yours.

        Reads are NON-DESTRUCTIVE by default: calling vmlog twice returns data
        both times. Pass take=True to consume (and end) an armed slice.

        Always returns a dict with a "lines" list, even when empty, so callers
        never have to guard for a missing key.
        """
        r = self.request("vmlog", arm=arm, limit=limit, take=take)
        r.setdefault("lines", [])
        r.setdefault("count", len(r["lines"]))
        return r


# ----------------------------------------------------------------------- cli --


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Drive a running Skyrim SE through the in-game bridge.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:", 1)[-1],
    )
    ap.add_argument("--json", action="store_true", help="emit raw JSON")
    ap.add_argument("--pipe", default=PIPE_NAME, help="override the pipe name")
    ap.add_argument("--retries", type=int, default=1,
                    help="connection attempts before giving up")

    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping", help="check the bridge is alive")
    sub.add_parser("capabilities", help="what resolved on this runtime")
    sub.add_parser("status", help="game / session state")

    c = sub.add_parser("console", help="run a console command")
    c.add_argument("command")
    c.add_argument("--ref", help="select this reference first (form id)")

    b = sub.add_parser(
        "batch",
        help="run several console commands in one main-thread trip",
    )
    b.add_argument("commands", nargs="+", help="commands, in order")
    b.add_argument("--ref", help="select this reference before the first command")
    b.add_argument("--keep-going", action="store_true",
                   help="continue after a failing command (default: stop)")

    inj = sub.add_parser(
        "inject",
        help="compile and run a multi-statement script inside the game",
    )
    g = inj.add_mutually_exclusive_group(required=True)
    g.add_argument("--script", help="script body; newline- or ';'-separated")
    g.add_argument("--file", help="read the script body from this file")
    inj.add_argument("--ref", help="run against this reference (form id or editor id)")
    inj.add_argument("--keep-going", action="store_true",
                     help="continue past a failing statement (default: stop)")
    inj.add_argument("--settle-ms", type=int, default=250,
                     help="how long to wait for the VM to flush (default 250)")

    v = sub.add_parser(
        "vmlog",
        help="read Papyrus VM output captured from the logger sink",
    )
    v.add_argument("--arm", action="store_true",
                   help="start a fresh slice and return immediately")
    v.add_argument("--limit", type=int, default=100,
                   help="max lines when reading the recent ring (default 100)")
    v.add_argument("--take", action="store_true",
                   help="consume an armed slice (default: non-destructive read)")

    rs = sub.add_parser("resolve", help="stable id / rva -> live address")
    rs.add_argument("--id", type=int)
    rs.add_argument("--rva", type=lambda s: int(s, 0))

    rm = sub.add_parser("readmem", help="read bytes from the live process")
    rm.add_argument("--address", type=lambda s: int(s, 0))
    rm.add_argument("--rva", type=lambda s: int(s, 0))
    rm.add_argument("--id", type=int)
    rm.add_argument("--len", type=int, default=64, dest="length")
    rm.add_argument("--string", action="store_true", dest="as_string")

    cl = sub.add_parser("call", help="call a function (up to 4 int/ptr args)")
    cl.add_argument("--address", type=lambda s: int(s, 0))
    cl.add_argument("--rva", type=lambda s: int(s, 0))
    cl.add_argument("--id", type=int)
    cl.add_argument("--arg", type=lambda s: int(s, 0), action="append", default=[])
    cl.add_argument("--float-args", type=int, default=0,
                    help="bitmask: bit N means arg N is a float")
    cl.add_argument("--float-result", action="store_true",
                    help="read the return value from XMM0 instead of RAX")

    hk = sub.add_parser("hook", help="install/inspect a generic hook at any address")
    hk.add_argument("--address", type=lambda s: int(s, 0))
    hk.add_argument("--rva", type=lambda s: int(s, 0))
    hk.add_argument("--id", type=int)
    hk.add_argument("--label", default="hook")
    hk.add_argument("--keep", type=int, default=16)
    hk.add_argument("--analyze", action="store_true",
                    help="dry run: can this be hooked safely, and if not why")
    hk.add_argument("--index", type=int, help="inspect one installed hook")
    hk.add_argument("--remove", type=int, help="remove the hook in this slot")

    sub.add_parser("hookstats", help="how many times each hook has fired")

    clg = sub.add_parser(
        "console_log",
        help="recent console output, including what the GAME printed itself",
    )
    clg.add_argument("--limit", type=int, default=100)

    sub.add_parser(
        "dumpstacks",
        help="dump live Papyrus VM stacks INTO the Papyrus log (read with "
             "papyrus_tail.py) -- shows what scripts are actually running",
    )

    args = ap.parse_args(argv)

    try:
        with Bridge(args.pipe).connect(retries=args.retries) as b:
            if args.cmd == "ping":
                out = b.ping()
            elif args.cmd == "capabilities":
                out = b.capabilities()
            elif args.cmd == "status":
                out = b.status()
            elif args.cmd == "console":
                out = {"output": b.console(args.command, args.ref)}
            elif args.cmd == "batch":
                out = b.batch(args.commands, args.ref,
                              stop_on_error=not args.keep_going)
            elif args.cmd == "inject":
                body = (Path(args.file).read_text(encoding="utf-8")
                        if args.file else args.script)
                out = b.inject(body, args.ref, stop_on_error=not args.keep_going,
                               settle_ms=args.settle_ms)
            elif args.cmd == "vmlog":
                out = b.vmlog(arm=args.arm, limit=args.limit, take=args.take)
            elif args.cmd == "resolve":
                out = b.resolve(id=args.id, rva=args.rva)
            elif args.cmd == "readmem":
                out = b.readmem(address=args.address, rva=args.rva, id=args.id,
                                length=args.length, as_string=args.as_string)
            elif args.cmd == "call":
                out = b.call(address=args.address, rva=args.rva, id=args.id,
                             args_=args.arg, float_args=args.float_args,
                             float_result=args.float_result)
            elif args.cmd == "hook":
                out = b.hook(address=args.address, rva=args.rva, id=args.id,
                             label=args.label, keep=args.keep,
                             analyze=args.analyze, index=args.index,
                             remove=args.remove)
            elif args.cmd == "hookstats":
                out = b.hookstats()
            elif args.cmd == "console_log":
                out = b.console_log(limit=args.limit)
            elif args.cmd == "dumpstacks":
                # `dumppapyrusstacks` writes every live VM stack INTO THE
                # PAPYRUS LOG, which papyrus_tail.py can read -- and, since the
                # VM logger is hooked in-process, `vmlog` sees the same output
                # without touching the file.
                #
                # NOT `cgf "Debug.Trace"`: `CallGlobalFunction` is a ConsoleUtil
                # command, not a vanilla or SKSE one. Verified 2026-08-14 -- the
                # string is absent from SkyrimSE.exe AND skse64_1_6_1170.dll,
                # and ConsoleUtil is not installed here, so a cgf-based marker
                # would fail silently.
                out = {"output": b.console("dumppapyrusstacks")}
            else:  # unreachable; argparse enforces the choices
                ap.error(f"unhandled command {args.cmd}")
    except BridgeError as exc:
        if args.json:
            print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)}))
        else:
            print(f"error [{exc.code}]: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(out, indent=2))
        # A batch that ran but had failures is not a transport error, so the
        # exit code has to carry the verdict or a caller cannot branch on it.
        return 1 if out.get("failed") else 0

    if args.cmd in ("console", "dumpstacks"):
        text = out.get("output", "")
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
        if args.cmd == "dumpstacks":
            print("(stacks written to the Papyrus log; read them with "
                  "`python tools/script/papyrus_tail.py since --cursor <n>`)",
                  file=sys.stderr)
    elif args.cmd == "inject":
        for i, s in enumerate(out.get("statements", [])):
            flag = "ok  " if s.get("ok") else "FAIL"
            print(f"[{i}] {flag} {s.get('text','')}")
            body = (s.get("output") or "").rstrip()
            for line in body.splitlines():
                print(f"        {line}")
            if not s.get("ok") and s.get("error"):
                print(f"        error: {s['error']}")
        if out.get("ok"):
            print("\ninjected and ran cleanly")
        else:
            print(f"\nFAILED at statement {out.get('failed_statement')}: "
                  f"{out.get('detail', '')}")
        pap = out.get("papyrus") or []
        if pap:
            print(f"\n-- Papyrus VM output ({len(pap)} line(s)) --")
            for line in pap:
                print(f"  {line}")
        return 0 if out.get("ok") else 1
    elif args.cmd == "console_log":
        for line in out.get("lines", []):
            print(line)
        print(f"\n-- {out.get('count', 0)} of {out.get('total', 0)} buffered line(s)",
              file=sys.stderr)
    elif args.cmd == "vmlog":
        if out.get("armed"):
            print("armed; run something, then `vmlog` again to collect")
        else:
            for line in out.get("lines", []):
                print(line)
            print(f"\n-- {out.get('count', 0)} line(s) from {out.get('source')}",
                  file=sys.stderr)
    elif args.cmd == "batch":
        for i, r in enumerate(out.get("results", []), 1):
            flag = "ok  " if r.get("ok") else "FAIL"
            print(f"[{i}] {flag} {r['command']}")
            body = (r.get("output") or "").rstrip()
            if body:
                for line in body.splitlines():
                    print(f"        {line}")
            if not r.get("ok"):
                print(f"        error: {r.get('error', '')}")
        print(f"\n-- ran {out.get('ran', 0)}/{out.get('total', 0)}, "
              f"{out.get('failed', 0)} failed", file=sys.stderr)
        return 1 if out.get("failed") else 0
    else:
        for k, v in out.items():
            _print_value(k, v)
    return 0


def _print_value(key: str, value) -> None:
    """Print one response field, escaping bytes the console cannot encode."""
    text = f"{key}: {value}"
    encoding = sys.stdout.encoding or "utf-8"
    print(text.encode(encoding, "backslashreplace").decode(encoding))


if __name__ == "__main__":
    sys.exit(main())

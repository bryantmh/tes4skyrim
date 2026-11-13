# Finding and hooking SkyrimSE engine functions

Contracts for locating an engine function, detouring it, and driving the console
from the game bridge. Tools: `tools/disasm/skyrim_disasm.py`, `tools/live/`,
`game_bridge/`. Stable ids and builds: [address_library_formats.md](address_library_formats.md).

## Find a function through RTTI, not caller counts

Real engine functions are reached INDIRECTLY (vtables, registration tables), so
ranking candidates by direct `call rel32` count finds almost nothing however hot the
function is. Walk RTTI to the class vtable instead:

1. Find the RTTI type name, e.g. `.?AVConsole@@`, `.?AVLogger@SkyrimScript@@`.
2. The TypeDescriptor starts 0x10 BEFORE the name string.
3. 4-byte RVA references to the TypeDescriptor in `.rdata` are CompleteObjectLocators
   (check `signature` is 0 or 1).
4. The 8-byte absolute pointer (imagebase + COL rva) marks the vtable, which begins
   at the NEXT qword.
5. Walk the entries; identify the one you want by the string literals it references
   (`lea rXX,[rip+...]` to a readable string). A referenced literal is the proof of
   identity: `Console::Print` is the one whose thunk references `"> %s"`. Never ship a
   hook identified only by a plausible-looking prologue.

Found this way on 1.6.1170: `Console::Print` id 51105 (a thunk that tail-jumps to the
real printer) and `SkyrimScript::Logger::Log` id 53551 (every Papyrus message). Invert
to a stable id by building `{rva: id}` from the 1.6.659 versionlib
(`tools/disasm/address_lib.py`); an id beats a signature when one exists. A missing
stable id is not a reason to hand-roll: `Script::ctor` (21874) is absent from 1.6.1170,
and a `memset` plus vtable store skipped the base-class ctor and the
`mov byte [rbx+0x1A],0x13` the real ctor writes, so the engine faulted. Resolve by
signature instead. The Steam exe's on-disk `.text` is encrypted; read the live process
(`--live`) or an unpacked exe.

## A matching prologue does not mean the bytes can be moved

Byte-equality of a prologue proves the BUILD is right, not that those bytes are
relocatable. A detour of `ConsoleExecute` (id 21954, 12 bytes stolen) matched its
expected prologue, installed, and crashed with an access violation WRITING into `.text`
at `21954+0x14`:

    +00  48 8B C4               mov rax,rsp          captures rsp
    +04  48 81 EC C0 00 00 00   sub rsp,0xC0
    +0B  48 C7 44 24 20 FE FF FF FF   mov qword [rsp+20],-2   (9 bytes, cut by a 12-byte steal)
    +14  48 89 58 08            mov [rax+8],rbx      uses that rax

`mov rax,rsp` records the trampoline's stack pointer, so the later store lands at a
stale address; the 12-byte steal also cut a 9-byte instruction in half. A longer steal
does not fix an unrelocatable instruction. `game_bridge/plugin/detour.cpp` rejects any
stolen range containing `mov r64,rsp` (`48 8B /r`, modrm `& 0xC7 == 0xC4`),
`lea r64,[rip+disp32]`, `E8`/`E9` rel call/jmp, `EB` rel8 jmp, `7x` jcc rel8 and
`0F 8x` jcc rel32. A thunk starts with a rip-relative instruction that must never be
relocated: follow its tail `jmp rel32` and hook the real function, whose prologue is
plain stack stores. Before ANY detour read the prologue in a disassembler and confirm
every stolen instruction is position-independent and the cut lands on an instruction
boundary.

Consequence: `ConsoleExecute` is not hookable, so its arg1 (the console's execution
context, built in the console's own stack frame) cannot be captured that way. With
arg1 null the engine compiles the script and returns 1 without running it, a false
success that made four earlier "fixes" look plausible. The return value is never proof
a command ran; check an observable effect or the console-print hook counter.

## Ref-targeted console commands need the dispatcher's target argument

`dispatch(rcx = Script, rdx = execContext, r8d = compilerType, r9 = target)` threads
`r9` into both halves (`mov r14,r9`, then `mov r8,r14` as ConsoleExecute arg3 to
compile and `mov rdx,r14` as the runner's arg2 to run). A separate `prid <id>` through
the bridge does NOT carry: each `ExecOne` is its own compile and execute, so the
selection is gone by the next call, and the failure is silent and reports SUCCESS
(`moveto player` on a quest actor returned ok and moved nothing; after `prid 00000014`
a bare `getpos x` printed nothing while `player.getpos x` printed a value). Pass the
resolved `TESForm*` as `r9`. `TESForm::LookupByID` is stable id 14617 (1.6.1170 rva
`0x1e01a0`), found without guessing an id: the Papyrus native name string `"GetForm"`
is referenced by its registration site, whose bound implementation is the thunk
`mov ecx,r9d ; jmp <LookupByID>`; it is self-validating (real pointers for 0x14,
0x7, 0 for a bogus id).

Never verify a ref-targeted command by its return value. Measure
`player.getdistance <ref>` (player-side, needs no selection; a ref in an unloaded cell
reads `FLT_MAX`), and require BOTH "close" and "distance actually changed": an actor
already 338 units away reads as near without having moved. Quest scripts bind
properties to particular placed refs, so a `placeatme` copy is a different FormID the
quest cannot see; testing a converted quest means moving the REAL ref.

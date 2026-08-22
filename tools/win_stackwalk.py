#!/usr/bin/env python3
r"""Real call stacks (and the window tree) of a LIVE hung Win32 process.

Why this exists: the CK "Initializing References" hang was diagnosed by
*scanning* a 6 GB minidump's stack memory for values that happened to land
inside `CreationKit.exe` -- which cannot tell a live return address from stale
leftover stack bytes, so every conclusion drawn from it is a candidate, not a
fact (see docs/ck_reference_init_hang.md).  This walks the stack for real,
through DbgHelp's `StackWalk64`, which unwinds via each module's `.pdata`
table exactly the way Windows SEH does.  No PDBs are needed for correct
frames; exported names (`ntdll!NtWaitForAlertByThreadId`,
`KERNELBASE!WaitForSingleObjectEx`) resolve from the export tables, which is
usually enough to name the blocking primitive outright.

Every frame is printed as `module+0xRVA`, ready to paste straight into
`tools/skyrim_disasm.py --exe <module> --func 0x<rva>`.

`--windows` dumps the process's whole window tree.  A CK "hang" is often a
modal dialog that never became visible (a warning MessageBox behind the
loading dialog): the main thread is then legitimately blocked in a message
loop nobody can answer, and no stack walk on earth will say "a dialog is
waiting for a click" as plainly as seeing the dialog in the list.

Usage:
    # unattended: start this, then launch CK and walk away.  It waits for the
    # process, reports the loading phase as it changes, and the moment the
    # process stops burning CPU for --idle-seconds it captures every stack.
    python tools/win_stackwalk.py --watch

    # one-shot against something already hung
    python tools/win_stackwalk.py --name CreationKit
    python tools/win_stackwalk.py --pid 1234 --windows
    python tools/win_stackwalk.py --name CreationKit --all-threads --modules

By default only threads whose stack touches a NON-system module are printed
(the interesting ones); `--all-threads` prints every thread.

Read-only: nothing is written to the target, threads are suspended only for
the microseconds it takes to copy their context.
"""

import argparse
import ctypes
import os
import sys
from ctypes import wintypes

if sys.platform != 'win32':
    sys.exit('win_stackwalk.py is Windows-only')

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
psapi = ctypes.WinDLL('psapi', use_last_error=True)
user32 = ctypes.WinDLL('user32', use_last_error=True)
dbghelp = ctypes.WinDLL('dbghelp', use_last_error=True)

# ------------------------------------------------------------------ constants

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
THREAD_GET_CONTEXT = 0x0008
THREAD_SUSPEND_RESUME = 0x0002
THREAD_QUERY_INFORMATION = 0x0040

TH32CS_SNAPTHREAD = 0x00000004

CONTEXT_AMD64 = 0x00100000
CONTEXT_CONTROL = CONTEXT_AMD64 | 0x1
CONTEXT_INTEGER = CONTEXT_AMD64 | 0x2
CONTEXT_SEGMENTS = CONTEXT_AMD64 | 0x4
CONTEXT_FLOATING_POINT = CONTEXT_AMD64 | 0x8
CONTEXT_FULL = CONTEXT_CONTROL | CONTEXT_INTEGER | CONTEXT_FLOATING_POINT

IMAGE_FILE_MACHINE_AMD64 = 0x8664

SYMOPT_UNDNAME = 0x00000002
SYMOPT_DEFERRED_LOADS = 0x00000004
SYMOPT_NO_PROMPTS = 0x00080000
SYMOPT_FAIL_CRITICAL_ERRORS = 0x00000200

# Modules whose frames are plumbing, not application logic.  A thread parked
# entirely inside these is an idle worker, not the hang.
SYSTEM_MODULES = {
    'ntdll.dll', 'kernel32.dll', 'kernelbase.dll', 'ucrtbase.dll',
    'msvcrt.dll', 'user32.dll', 'win32u.dll', 'gdi32.dll', 'gdi32full.dll',
    'combase.dll', 'rpcrt4.dll', 'sechost.dll', 'advapi32.dll',
    'msvcp_win.dll', 'shcore.dll', 'shell32.dll', 'ole32.dll', 'oleaut32.dll',
    'imm32.dll', 'comctl32.dll', 'uxtheme.dll', 'dbghelp.dll',
}

# ------------------------------------------------------------------ structs


class M128A(ctypes.Structure):
    _fields_ = [('Low', ctypes.c_ulonglong), ('High', ctypes.c_longlong)]
    _pack_ = 1


class CONTEXT64(ctypes.Structure):
    """AMD64 CONTEXT.  Must be 16-byte aligned when passed to the kernel."""
    _pack_ = 1
    _fields_ = [
        ('P1Home', ctypes.c_ulonglong), ('P2Home', ctypes.c_ulonglong),
        ('P3Home', ctypes.c_ulonglong), ('P4Home', ctypes.c_ulonglong),
        ('P5Home', ctypes.c_ulonglong), ('P6Home', ctypes.c_ulonglong),
        ('ContextFlags', wintypes.DWORD), ('MxCsr', wintypes.DWORD),
        ('SegCs', wintypes.WORD), ('SegDs', wintypes.WORD),
        ('SegEs', wintypes.WORD), ('SegFs', wintypes.WORD),
        ('SegGs', wintypes.WORD), ('SegSs', wintypes.WORD),
        ('EFlags', wintypes.DWORD),
        ('Dr0', ctypes.c_ulonglong), ('Dr1', ctypes.c_ulonglong),
        ('Dr2', ctypes.c_ulonglong), ('Dr3', ctypes.c_ulonglong),
        ('Dr6', ctypes.c_ulonglong), ('Dr7', ctypes.c_ulonglong),
        ('Rax', ctypes.c_ulonglong), ('Rcx', ctypes.c_ulonglong),
        ('Rdx', ctypes.c_ulonglong), ('Rbx', ctypes.c_ulonglong),
        ('Rsp', ctypes.c_ulonglong), ('Rbp', ctypes.c_ulonglong),
        ('Rsi', ctypes.c_ulonglong), ('Rdi', ctypes.c_ulonglong),
        ('R8', ctypes.c_ulonglong), ('R9', ctypes.c_ulonglong),
        ('R10', ctypes.c_ulonglong), ('R11', ctypes.c_ulonglong),
        ('R12', ctypes.c_ulonglong), ('R13', ctypes.c_ulonglong),
        ('R14', ctypes.c_ulonglong), ('R15', ctypes.c_ulonglong),
        ('Rip', ctypes.c_ulonglong),
        ('FltSave', ctypes.c_ubyte * 512),
        ('VectorRegister', M128A * 26),
        ('VectorControl', ctypes.c_ulonglong),
        ('DebugControl', ctypes.c_ulonglong),
        ('LastBranchToRip', ctypes.c_ulonglong),
        ('LastBranchFromRip', ctypes.c_ulonglong),
        ('LastExceptionToRip', ctypes.c_ulonglong),
        ('LastExceptionFromRip', ctypes.c_ulonglong),
    ]


class ADDRESS64(ctypes.Structure):
    _fields_ = [('Offset', ctypes.c_ulonglong),
                ('Segment', wintypes.WORD),
                ('Mode', ctypes.c_uint)]


class STACKFRAME64(ctypes.Structure):
    # KdHelp is deliberately oversized: dbghelp writes only as far as its own
    # KDHELP64 reaches, so a larger tail is safe while a smaller one corrupts
    # the stack.
    _fields_ = [('AddrPC', ADDRESS64), ('AddrReturn', ADDRESS64),
                ('AddrFrame', ADDRESS64), ('AddrStack', ADDRESS64),
                ('AddrBStore', ADDRESS64),
                ('FuncTableEntry', ctypes.c_void_p),
                ('Params', ctypes.c_ulonglong * 4),
                ('Far', wintypes.BOOL), ('Virtual', wintypes.BOOL),
                ('Reserved', ctypes.c_ulonglong * 3),
                ('KdHelp', ctypes.c_ubyte * 256)]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                ('th32ThreadID', wintypes.DWORD),
                ('th32OwnerProcessID', wintypes.DWORD),
                ('tpBasePri', ctypes.c_long), ('tpDeltaPri', ctypes.c_long),
                ('dwFlags', wintypes.DWORD)]


class SYMBOL_INFO(ctypes.Structure):
    _fields_ = [('SizeOfStruct', wintypes.DWORD), ('TypeIndex', wintypes.DWORD),
                ('Reserved', ctypes.c_ulonglong * 2),
                ('Index', wintypes.DWORD), ('Size', wintypes.DWORD),
                ('ModBase', ctypes.c_ulonglong), ('Flags', wintypes.DWORD),
                ('Value', ctypes.c_ulonglong), ('Address', ctypes.c_ulonglong),
                ('Register', wintypes.DWORD), ('Scope', wintypes.DWORD),
                ('Tag', wintypes.DWORD), ('NameLen', wintypes.DWORD),
                ('MaxNameLen', wintypes.DWORD), ('Name', ctypes.c_char * 1024)]


class MODULEINFO(ctypes.Structure):
    _fields_ = [('lpBaseOfDll', ctypes.c_void_p),
                ('SizeOfImage', wintypes.DWORD),
                ('EntryPoint', ctypes.c_void_p)]


# ------------------------------------------------------------------ prototypes

k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenThread.restype = wintypes.HANDLE
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.GetThreadContext.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.SuspendThread.argtypes = [wintypes.HANDLE]
k32.ResumeThread.argtypes = [wintypes.HANDLE]
k32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_void_p]
k32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.CloseHandle.argtypes = [wintypes.HANDLE]

psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE,
                                       ctypes.POINTER(ctypes.c_void_p),
                                       wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD),
                                       wintypes.DWORD]
psapi.GetModuleInformation.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                       ctypes.c_void_p, wintypes.DWORD]
psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                       wintypes.LPWSTR, wintypes.DWORD]

dbghelp.SymInitialize.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                  wintypes.BOOL]
dbghelp.SymFunctionTableAccess64.restype = ctypes.c_void_p
dbghelp.SymFunctionTableAccess64.argtypes = [wintypes.HANDLE,
                                             ctypes.c_ulonglong]
dbghelp.SymGetModuleBase64.restype = ctypes.c_ulonglong
dbghelp.SymGetModuleBase64.argtypes = [wintypes.HANDLE, ctypes.c_ulonglong]
dbghelp.SymFromAddr.argtypes = [wintypes.HANDLE, ctypes.c_ulonglong,
                                ctypes.POINTER(ctypes.c_ulonglong),
                                ctypes.POINTER(SYMBOL_INFO)]
dbghelp.StackWalk64.argtypes = [wintypes.DWORD, wintypes.HANDLE,
                                wintypes.HANDLE, ctypes.POINTER(STACKFRAME64),
                                ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_void_p,
                                ctypes.c_void_p]
dbghelp.StackWalk64.restype = wintypes.BOOL

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                 wintypes.LPARAM)


# ------------------------------------------------------------------ helpers


def find_pid(name: str, required=True):
    import subprocess
    out = subprocess.run(
        ['powershell', '-NoProfile', '-Command',
         f"(Get-Process -Name '{name}' -ErrorAction SilentlyContinue |"
         " Select-Object -First 1).Id"],
        capture_output=True, text=True).stdout.strip()
    if not out.isdigit():
        if required:
            sys.exit(f'no running process named {name!r}')
        return None
    return int(out)


def cpu_seconds(hproc):
    """Total kernel+user CPU seconds the process has consumed, or None."""
    ft = (wintypes.FILETIME * 4)()
    if not k32.GetProcessTimes(hproc, ctypes.byref(ft[0]), ctypes.byref(ft[1]),
                               ctypes.byref(ft[2]), ctypes.byref(ft[3])):
        return None
    def as_int(f):
        return (f.dwHighDateTime << 32) | f.dwLowDateTime
    return (as_int(ft[2]) + as_int(ft[3])) / 1e7


def loading_phase(pid):
    """The text of the Loading dialog's static child, if one exists.

    CK writes the current load phase there ("Initializing References..."),
    which is the only progress signal the process gives without a debugger.
    """
    rows = window_tree(pid)
    inside = False
    texts = []
    for depth, hwnd, tid, cls, txt, vis, en in rows:
        if depth == 0:
            inside = 'loading' in txt.lower()
            continue
        if inside and cls == 'Static' and txt.strip():
            texts.append(txt.strip())
    return ' | '.join(texts) if texts else None


def module_map(hproc):
    """[(base, end, filename)] sorted by base."""
    mods = (ctypes.c_void_p * 4096)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcessModulesEx(hproc, mods, ctypes.sizeof(mods),
                                      ctypes.byref(needed), 0x03):
        return []
    n = min(needed.value // ctypes.sizeof(ctypes.c_void_p), 4096)
    out = []
    buf = ctypes.create_unicode_buffer(1024)
    for i in range(n):
        mi = MODULEINFO()
        if not psapi.GetModuleInformation(hproc, mods[i], ctypes.byref(mi),
                                          ctypes.sizeof(mi)):
            continue
        psapi.GetModuleFileNameExW(hproc, mods[i], buf, 1024)
        base = int(mi.lpBaseOfDll)
        out.append((base, base + mi.SizeOfImage, buf.value))
    out.sort()
    return out


def resolve(mods, addr):
    """(basename, rva) for an address, or (None, addr)."""
    for base, end, path in mods:
        if base <= addr < end:
            return os.path.basename(path), addr - base
    return None, addr


def thread_ids(pid):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap == wintypes.HANDLE(-1).value:
        sys.exit('CreateToolhelp32Snapshot failed')
    te = THREADENTRY32()
    te.dwSize = ctypes.sizeof(te)
    out = []
    ok = k32.Thread32First(snap, ctypes.byref(te))
    while ok:
        if te.th32OwnerProcessID == pid:
            out.append(te.th32ThreadID)
        ok = k32.Thread32Next(snap, ctypes.byref(te))
    k32.CloseHandle(snap)
    return out


def aligned_context():
    """A CONTEXT64 whose address is 16-byte aligned (the kernel requires it)."""
    size = ctypes.sizeof(CONTEXT64)
    raw = ctypes.create_string_buffer(size + 16)
    addr = ctypes.addressof(raw)
    off = (16 - addr % 16) % 16
    ctx = CONTEXT64.from_buffer(raw, off)
    ctx._raw_keepalive = raw          # keep the backing buffer alive
    return ctx


def sym_name(hproc, addr):
    si = SYMBOL_INFO()
    si.SizeOfStruct = ctypes.sizeof(SYMBOL_INFO) - 1024
    si.MaxNameLen = 1023
    disp = ctypes.c_ulonglong(0)
    if dbghelp.SymFromAddr(hproc, addr, ctypes.byref(disp), ctypes.byref(si)):
        name = si.Name.decode('latin1', 'replace')
        return f'{name}+0x{disp.value:x}' if disp.value else name
    return None


def walk_thread(hproc, tid, mods, max_frames=96):
    hthread = k32.OpenThread(THREAD_GET_CONTEXT | THREAD_SUSPEND_RESUME |
                             THREAD_QUERY_INFORMATION, False, tid)
    if not hthread:
        return None
    frames = []
    try:
        if k32.SuspendThread(hthread) == 0xFFFFFFFF:
            return None
        try:
            ctx = aligned_context()
            ctx.ContextFlags = CONTEXT_FULL
            if not k32.GetThreadContext(hthread, ctypes.byref(ctx)):
                return None
            sf = STACKFRAME64()
            sf.AddrPC.Offset = ctx.Rip
            sf.AddrPC.Mode = 3            # AddrModeFlat
            sf.AddrFrame.Offset = ctx.Rbp
            sf.AddrFrame.Mode = 3
            sf.AddrStack.Offset = ctx.Rsp
            sf.AddrStack.Mode = 3
            for _ in range(max_frames):
                if not dbghelp.StackWalk64(
                        IMAGE_FILE_MACHINE_AMD64, hproc, hthread,
                        ctypes.byref(sf), ctypes.byref(ctx), None,
                        ctypes.cast(dbghelp.SymFunctionTableAccess64,
                                    ctypes.c_void_p),
                        ctypes.cast(dbghelp.SymGetModuleBase64,
                                    ctypes.c_void_p), None):
                    break
                pc = sf.AddrPC.Offset
                if not pc:
                    break
                frames.append(pc)
        finally:
            k32.ResumeThread(hthread)
    finally:
        k32.CloseHandle(hthread)
    return frames


# ------------------------------------------------------------------ windows


def window_tree(pid):
    """[(depth, hwnd, tid, class, text)] for every window owned by `pid`."""
    rows = []

    def describe(hwnd, depth):
        owner = wintypes.DWORD()
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return False
        cls = ctypes.create_unicode_buffer(256)
        txt = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW(hwnd, cls, 256)
        user32.GetWindowTextW(hwnd, txt, 512)
        rows.append((depth, hwnd, tid, cls.value, txt.value,
                     bool(user32.IsWindowVisible(hwnd)),
                     bool(user32.IsWindowEnabled(hwnd))))
        return True

    def enum_children(parent, depth):
        def cb(hwnd, _):
            if describe(hwnd, depth):
                enum_children(hwnd, depth + 1)
            return True
        user32.EnumChildWindows(parent, WNDENUMPROC(cb), 0)

    def top(hwnd, _):
        if describe(hwnd, 0):
            enum_children(hwnd, 1)
        return True

    user32.EnumWindows(WNDENUMPROC(top), 0)
    return rows


# ------------------------------------------------------------------ main


def capture(pid, hproc, args, emit):
    """Write the full window tree + every thread's stack through `emit`."""
    emit(f'pid {pid}')
    mods = module_map(hproc)
    if args.modules:
        emit(f'\n{len(mods)} modules:')
        for base, end, path in mods:
            emit(f'  {base:#018x}-{end:#018x}  {os.path.basename(path)}')

    rows = window_tree(pid)
    if args.windows or args.watch:
        emit(f'\n{len(rows)} windows:')
        for depth, hwnd, tid, cls, txt, vis, en in rows:
            flags = ('vis' if vis else 'HIDDEN') + ('' if en else ' DISABLED')
            emit(f'  {"  " * depth}{hwnd:#010x} tid={tid} [{flags}] '
                 f'{cls!r} {txt!r}')

    dbghelp.SymSetOptions(SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS |
                          SYMOPT_NO_PROMPTS | SYMOPT_FAIL_CRITICAL_ERRORS)
    # No symbol path and no server: exported names are all we want, and a
    # symbol-server round trip would stall for minutes per module.
    if not dbghelp.SymInitialize(hproc, None, True):
        emit('  (SymInitialize failed -- frames will still be correct, '
             'names will be missing)')

    ui_tids = {tid for _, _, tid, _, _, _, _ in rows}
    tids = thread_ids(pid)
    emit(f'\n{len(tids)} threads')
    shown = 0
    for tid in tids:
        frames = walk_thread(hproc, tid, mods, args.max_frames)
        if frames is None:
            emit(f'\nthread {tid}: <could not open/suspend>')
            continue
        named = [(pc,) + resolve(mods, pc) for pc in frames]
        interesting = any(m and m.lower() not in SYSTEM_MODULES
                          for _, m, _ in named)
        if not interesting and not args.all_threads:
            continue
        shown += 1
        tag = ' [OWNS WINDOWS]' if tid in ui_tids else ''
        emit(f'\nthread {tid}{tag}  ({len(named)} frames)')
        for i, (pc, mod, rva) in enumerate(named):
            nm = sym_name(hproc, pc)
            loc = f'{mod}+{rva:#x}' if mod else f'{pc:#018x}'
            emit(f'  #{i:<3} {loc:<34} {nm or ""}')
    if not args.all_threads:
        emit(f'\n{shown} of {len(tids)} threads touch a non-system module '
             f'(--all-threads for the rest)')
    dbghelp.SymCleanup(hproc)


def open_proc(pid):
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
                        False, pid)
    if not h:
        sys.exit(f'OpenProcess({pid}) failed: {ctypes.get_last_error()}')
    return h


def watch(args):
    """Poll until the target stops consuming CPU, then capture.

    A hang and a slow phase look identical from outside; only the CPU counter
    separates them, so the capture trigger is "no CPU at all for
    --idle-seconds", not "it feels stuck".
    """
    import time
    pid = args.pid
    if not pid:
        print(f'waiting for {args.name}... (Ctrl-C to stop)')
        while pid is None or not pid:
            pid = find_pid(args.name, required=False)
            if not pid:
                time.sleep(args.poll)
    hproc = open_proc(pid)
    print(f'attached to pid {pid}')

    last_cpu = cpu_seconds(hproc)
    idle_for = 0.0
    last_phase = None
    while True:
        time.sleep(args.poll)
        cpu = cpu_seconds(hproc)
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(hproc, ctypes.byref(code))
        if cpu is None or code.value != 259:      # STILL_ACTIVE
            print('process exited before it went idle')
            return
        delta = cpu - last_cpu
        last_cpu = cpu
        phase = loading_phase(pid)
        if phase != last_phase:
            print(f'  phase: {phase!r}')
            last_phase = phase
        # "Busy" is generous on purpose: a background thread ticking a
        # progress bar must not reset the idle timer.
        if delta < args.busy_threshold * args.poll:
            idle_for += args.poll
        else:
            idle_for = 0.0
        print(f'  cpu +{delta:6.2f}s over {args.poll}s   '
              f'idle {idle_for:.0f}/{args.idle_seconds}s')
        if idle_for >= args.idle_seconds:
            break

    print(f'\nno CPU for {idle_for:.0f}s -- capturing stacks')
    lines = []
    capture(pid, hproc, args, lambda s: (print(s), lines.append(s)))
    out = args.out or os.path.join('temp', 'hang_stacks.txt')
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(f'phase at capture: {last_phase!r}\n')
        fh.write('\n'.join(lines) + '\n')
    print(f'\nwritten to {out}')
    k32.CloseHandle(hproc)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--pid', type=int, default=0)
    ap.add_argument('--name', default='CreationKit',
                    help='process name if --pid is not given')
    ap.add_argument('--all-threads', action='store_true',
                    help='print every thread, not just those touching a '
                         'non-system module')
    ap.add_argument('--windows', action='store_true',
                    help='dump the window tree (finds an invisible modal '
                         'dialog that is really what is "hanging")')
    ap.add_argument('--modules', action='store_true',
                    help='list loaded modules with base/size')
    ap.add_argument('--max-frames', type=int, default=96)
    ap.add_argument('--watch', action='store_true',
                    help='wait for the process, follow its loading phase, and '
                         'capture automatically once it goes idle')
    ap.add_argument('--poll', type=float, default=5.0,
                    help='seconds between samples in --watch')
    ap.add_argument('--idle-seconds', type=float, default=45.0,
                    help='how long CPU must stay flat before capturing')
    ap.add_argument('--busy-threshold', type=float, default=0.02,
                    help='CPU-seconds per wall second still counted as idle')
    ap.add_argument('--out', help='file to write the capture to '
                                  '(default temp/hang_stacks.txt)')
    args = ap.parse_args()

    if args.watch:
        watch(args)
        return

    pid = args.pid or find_pid(args.name)
    hproc = open_proc(pid)
    capture(pid, hproc, args, print)
    k32.CloseHandle(hproc)


if __name__ == '__main__':
    main()

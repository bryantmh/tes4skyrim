"""Time a real conversion step by step and record how many CPU cores each part keeps busy.

Runs one convert.py (the source script, or TESRACT.exe's compiled copy with --exe) and writes,
as the run goes, every output line with its time and a machine-wide busy-core sample every
SAMPLE_EVERY seconds. The summary splits each phase into its sections (the `=== Title ===`
headers its stages print) with wall time, CPU seconds and the mean busy cores.

    python -m tools.validate.profile_convert -f Tribunal.esm --steps export meshes --out temp/profile
See: docs/commentary/core_gui.md#run-estimates
"""

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from core.gui.config import STEPS
from core.phase_info import STEP_BY_BANNER
from core.subprocess_flags import POPEN_FLAGS
from core.worker_budget import WORKERS_ENV_VAR, free_memory_mb, worker_count

#: The repository root, where convert.py runs.
ROOT = Path(__file__).resolve().parents[2]
#: Seconds between CPU samples.
SAMPLE_EVERY = 0.25
#: OpenProcess rights AssignProcessToJobObject needs.
PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001
#: OpenProcess right GetProcessMemoryInfo needs.
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
#: Most processes read from the job at once.
MAX_PROCESSES = 512
#: Step key -> its convert.py flag.
FLAGS = {key: flag for key, flag, *_ in STEPS}


def _filetime(ft) -> int:
    """A FILETIME as 100 ns ticks."""
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def system_busy() -> int:
    """Busy CPU time summed over every logical core, in 100 ns ticks."""
    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    return _filetime(kernel) + _filetime(user) - _filetime(idle)


class JobTimes(ctypes.Structure):
    """JOBOBJECT_BASIC_ACCOUNTING_INFORMATION."""

    _fields_ = (("user", ctypes.c_int64), ("kernel", ctypes.c_int64), ("this_user", ctypes.c_int64),
                ("this_kernel", ctypes.c_int64), ("faults", wintypes.DWORD), ("total", wintypes.DWORD),
                ("active", wintypes.DWORD), ("terminated", wintypes.DWORD))


class MemoryCounters(ctypes.Structure):
    """PROCESS_MEMORY_COUNTERS_EX."""

    _fields_ = (("cb", wintypes.DWORD), ("faults", wintypes.DWORD), ("peak_ws", ctypes.c_size_t),
                ("ws", ctypes.c_size_t), ("peak_paged", ctypes.c_size_t), ("paged", ctypes.c_size_t),
                ("peak_nonpaged", ctypes.c_size_t), ("nonpaged", ctypes.c_size_t), ("pagefile", ctypes.c_size_t),
                ("peak_pagefile", ctypes.c_size_t), ("private", ctypes.c_size_t))


def process_memory_mb(pid: int) -> tuple:
    """(private, resident) MB of one process; (0, 0) once it has gone."""
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return 0.0, 0.0
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(wintypes.HANDLE(handle), ctypes.byref(counters), counters.cb)
    k32.CloseHandle(wintypes.HANDLE(handle))
    return (counters.private / 2 ** 20, counters.ws / 2 ** 20) if ok else (0.0, 0.0)


class Job:
    """A job object holding the run and every process it starts, so its CPU time is the run's alone."""

    def __init__(self, pid: int):
        """Create the job and put process `pid` in it (its children join on their own)."""
        k32 = ctypes.windll.kernel32
        k32.CreateJobObjectW.restype = k32.OpenProcess.restype = wintypes.HANDLE
        self.handle = k32.CreateJobObjectW(None, None)
        process = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not k32.AssignProcessToJobObject(wintypes.HANDLE(self.handle), wintypes.HANDLE(process)):
            raise OSError("AssignProcessToJobObject failed")

    def busy(self) -> int:
        """CPU time of every process ever in the job, finished ones included, in 100 ns ticks."""
        info = JobTimes()
        ctypes.windll.kernel32.QueryInformationJobObject(wintypes.HANDLE(self.handle), 1, ctypes.byref(info),
                                                         ctypes.sizeof(info), None)
        return info.user + info.kernel

    def pids(self) -> list:
        """The processes in the job now (64-bit layout: two DWORD counts share the first slot)."""
        ids = (ctypes.c_uint64 * (MAX_PROCESSES + 1))()
        ctypes.windll.kernel32.QueryInformationJobObject(wintypes.HANDLE(self.handle), 3, ctypes.byref(ids),
                                                         ctypes.sizeof(ids), None)
        return list(ids[1:1 + (ids[0] >> 32)])

    def memory(self) -> tuple:
        """(total private MB, total resident MB, largest process's private MB, process count) of the job now."""
        sizes = [process_memory_mb(pid) for pid in self.pids()]
        return (sum(p for p, _w in sizes), sum(w for _p, w in sizes), max((p for p, _w in sizes), default=0.0),
                len(sizes))


class Sampler(threading.Thread):
    """Writes `t, run cores, machine cores, private MB, resident MB, largest MB, processes, free MB` per sample."""

    def __init__(self, path: Path, t0: float, job: Job):
        """Start counting from `t0` (time.monotonic)."""
        super().__init__(daemon=True)
        self.path, self.t0, self.job, self.stop = path, t0, job, threading.Event()
        self.samples = []

    def run(self) -> None:
        """Sample until stopped, appending each sample to the file."""
        last = (time.monotonic(), self.job.busy(), system_busy())
        with self.path.open("w", encoding="utf-8") as fh:
            while not self.stop.wait(SAMPLE_EVERY):
                now = (time.monotonic(), self.job.busy(), system_busy())
                dt = max(now[0] - last[0], 1e-6)
                ours, machine = (now[1] - last[1]) / 1e7 / dt, (now[2] - last[2]) / 1e7 / dt
                sample = (now[0] - self.t0, dt, ours, machine, *self.job.memory(), free_memory_mb())
                self.samples.append(sample)
                fh.write("\t".join(f"{v:.2f}" for v in sample[:1] + sample[2:]) + "\n")
                fh.flush()
                last = now


def command(args) -> list:
    """The convert argv for the plugins and steps asked for."""
    head = [args.exe, "--tesract-run", "convert"] if args.exe else [sys.executable, "-u", str(ROOT / "convert.py")]
    return head + ["-f", *args.files] + [FLAGS[s] for s in args.steps] + ["--no-collision-winding-fix"]


def is_section(line: str, prev: str) -> bool:
    """A section header is the title line right after a `====` rule."""
    return set(prev.strip()) == {"="} and bool(line.strip()) and set(line.strip()) != {"="}


def summarize(lines: list, samples: list, end: float) -> list:
    """[{phase, section, start, wall, cpu, cores, others}] from the timed lines and the CPU samples.

    `cores` is the run's own mean busy cores; `others` is what everything else on the machine kept busy.
    """
    marks, prev, phase = [(0.0, "startup", "startup")], "", "startup"
    for t, line in lines:
        if is_section(line, prev):
            phase = STEP_BY_BANNER.get(line.strip(), phase)
            marks.append((t, phase, line.strip()))
        prev = line
    out = []
    for i, (start, phase, title) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else end
        inside = [s for s in samples if start < s[0] <= stop]
        cpu = sum(s[1] * s[2] for s in inside)
        others = sum(s[1] * (s[3] - s[2]) for s in inside)
        wall = stop - start
        out.append({"phase": phase, "section": title, "start": round(start, 2), "wall": round(wall, 2),
                    "cpu": round(cpu, 1), "cores": round(cpu / wall, 2) if wall > 0 else 0.0,
                    "others": round(max(0.0, others) / wall, 2) if wall > 0 else 0.0,
                    "peak_private_mb": round(max((s[4] for s in inside), default=0.0)),
                    "peak_process_mb": round(max((s[6] for s in inside), default=0.0)),
                    "peak_processes": max((s[7] for s in inside), default=0),
                    "least_free_mb": round(min((s[8] for s in inside), default=0.0))})
    return out


def run_timed(args, env: dict, out: Path, tag: str, t0: float) -> tuple:
    """Run convert under a job, sampling it, writing each line with its time; (exit code, lines, samples)."""
    lines = []
    proc = subprocess.Popen(command(args), cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **POPEN_FLAGS)
    sampler = Sampler(out / f"{tag}.cpu.tsv", t0, Job(proc.pid))
    sampler.start()
    with (out / f"{tag}.lines.tsv").open("w", encoding="utf-8") as fh:
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            t = time.monotonic() - t0
            lines.append((t, line))
            fh.write(f"{t:.2f}\t{line}\n")
            fh.flush()
            print(f"[{t:7.1f}] {line}", flush=True)
    code = proc.wait()
    sampler.stop.set()
    sampler.join()
    return code, lines, sampler.samples


def profile(args) -> int:
    """Run, record, summarize; returns the run's exit code."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = args.tag or "_".join(args.files)
    env = dict(os.environ, **{WORKERS_ENV_VAR: str(args.workers or worker_count())})
    t0 = time.monotonic()
    code, lines, samples = run_timed(args, env, out, tag, t0)
    end = time.monotonic() - t0
    sections = summarize(lines, samples, end)
    report = {"files": args.files, "steps": args.steps, "workers": int(env[WORKERS_ENV_VAR]),
              "logical_cores": os.cpu_count(), "exit": code, "wall": round(end, 2), "sections": sections}
    (out / f"{tag}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    for s in sections:
        print(f"{s['phase']:>10} {s['wall']:8.1f}s {s['cores']:6.2f} cores (others {s['others']:5.2f}) "
              f"{s['peak_private_mb']:6d} MB in {s['peak_processes']:3.0f} (largest {s['peak_process_mb']:5d}, "
              f"free {s['least_free_mb']:6d})  {s['section']}")
    print(f"EXIT {code} in {end:.1f}s")
    return code


def main() -> int:
    """Parse the arguments and profile one run."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-f", "--files", nargs="+", required=True, help="plugin filenames, as convert.py -f takes them")
    ap.add_argument("--steps", nargs="+", required=True, choices=sorted(FLAGS), help="step keys to run")
    ap.add_argument("--out", default=str(ROOT / "temp" / "profile"), help="folder for the results")
    ap.add_argument("--exe", help="TESRACT.exe to run convert from (its compiled copy)")
    ap.add_argument("--workers", type=int, help="worker count (default: the machine's)")
    ap.add_argument("--tag", help="name for the result files (default: the plugin names)")
    return profile(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())

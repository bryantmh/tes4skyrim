"""Stage-weighted progress for the long calls the page waits on.

A spinner cannot tell "working" from "hung", and a cold cell open measured
43.9s.  Each job publishes its stage and a fraction; the page polls it.
Weights are MEASURED shares of wall time, so the bar tracks reality.

See: docs/commentary/tes5_import_navmesh.md#cellview-progress
"""

import threading
import time

#: stage -> (label, measured share of the whole job); shares sum to 1.0.
STAGES = {
    'index': (('parsing export records', 0.60),
              ('merging master records', 0.22),
              ('writing index', 0.18)),
    'mesh': (('loading export index', 0.49),
             ('resolving cell + collision', 0.36),
             ('generating navmesh', 0.15)),
    'seams': (('generating neighbour cells', 1.0),),
}

#: job id -> live state; see read().
_JOBS = {}
_LOCK = threading.Lock()


def start(job_id, kind):
    """Open a progress job of `kind`, replacing any previous one."""
    with _LOCK:
        _JOBS[job_id] = {'kind': kind, 'step': 0, 'within': 0.0,
                         'done': False, 'error': '', 'started': time.time(),
                         'stage_start': time.time(),
                         'label': STAGES[kind][0][0]}


def creep(job_id, seconds):
    """Advance the CURRENT stage toward its end on a time estimate.

    A stage with no internal checkpoints -- unpickling a 2 GB index is one
    opaque call -- would otherwise sit at a dead number for its whole run,
    which reads as a hang.  Approaches, never reaches, the stage boundary.
    """
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None or job['done']:
            return
        elapsed = time.time() - job.get('stage_start', job['started'])
        job['within'] = max(job['within'],
                            1.0 - 0.5 ** (elapsed / max(0.1, seconds)))


def step(job_id, index, within=0.0):
    """Enter stage `index` of the job, `within` 0..1 through it."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        stages = STAGES[job['kind']]
        job['step'] = max(0, min(index, len(stages) - 1))
        job['within'] = max(0.0, min(1.0, within))
        job['label'] = stages[job['step']][0]
        job['stage_start'] = time.time()


def finish(job_id, error=''):
    """Mark the job complete, or failed with `error`."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job['done'] = True
            job['error'] = error


def read(job_id):
    """`{percent, label, done, error, elapsed}` for one job.

    Percent is the completed stages' weights plus the fraction of the current
    one, so it advances monotonically and never rewinds.
    """
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return {'percent': 0, 'label': '', 'done': True, 'error': '',
                    'elapsed': 0.0, 'missing': True}
        stages = STAGES[job['kind']]
        base = sum(w for (_label, w) in stages[:job['step']])
        pct = base + stages[job['step']][1] * job['within']
        return {'percent': round(100.0 * (1.0 if job['done'] else pct), 1),
                'label': 'done' if job['done'] else job['label'],
                'done': job['done'], 'error': job['error'],
                'elapsed': round(time.time() - job['started'], 1)}


#: Rough seconds a stage runs, for `creep`; measured, see the doc anchor.
STAGE_SECONDS = 25.0


def poll(job_id):
    """What `/progress` returns: `read`, after creeping the open stage."""
    creep(job_id, STAGE_SECONDS)
    return read(job_id)


def clear(job_id):
    """Drop a finished job's state."""
    with _LOCK:
        _JOBS.pop(job_id, None)

"""Shared plumbing and the GUI.

A real package, never a namespace one: `tools/` was only a namespace
package once, and a module-scope import of it killed the entire GUI --
invisibly, because under `gui.pyw` (pythonw, no console) the traceback
has nowhere to go and the window simply never appears.
"""

import os

#: Environment variable OpenBLAS reads, at import, for how many threads to reserve buffers for.
BLAS_THREADS_ENV_VAR = 'OPENBLAS_NUM_THREADS'


def limit_blas_threads():
    """One math-library thread for this process and its children, unless already set.

    See: docs/commentary/performance.md#one-blas-thread-per-process
    """
    os.environ.setdefault(BLAS_THREADS_ENV_VAR, '1')


limit_blas_threads()

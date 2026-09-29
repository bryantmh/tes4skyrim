"""Interleaved REGN surplus entries append in struct order under every hash seed.

See: docs/commentary/tes5_import_override.md#interleaved-subrecords
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_PROBE = r'''
import sys
sys.path.insert(0, sys.argv[1])
from tes5_import.overrides.builder import _apply_generic
master = [(b'EDID', b'R\x00'), (b'RPLI', b'a'), (b'RPLD', b'A')]
subs = {b'RPLD': [b'A', b'B'], b'RPLI': [b'a', b'b']}
grown = _apply_generic(master, subs, set())
fresh = _apply_generic([(b'EDID', b'R\x00')], subs, set())
print(repr((grown, fresh)))
'''


def _run(seed: int) -> str:
    """The probe's output with PYTHONHASHSEED=`seed`."""
    env = dict(os.environ, PYTHONHASHSEED=str(seed), PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable, '-c', _PROBE, str(ROOT)], env=env,
                          capture_output=True, text=True, check=True).stdout


def test_surplus_is_paired_and_seed_independent():
    """Every seed gives RPLI RPLD RPLI RPLD, once, byte-identically."""
    want = [(b'EDID', b'R\x00'), (b'RPLI', b'a'), (b'RPLD', b'A'),
            (b'RPLI', b'b'), (b'RPLD', b'B')]
    outputs = {seed: _run(seed) for seed in range(8)}
    assert set(outputs.values()) == {repr((want, want)) + '\n'}, outputs

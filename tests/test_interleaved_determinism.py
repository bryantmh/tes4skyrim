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


_PROBE_TAIL = r'''
import sys
sys.path.insert(0, sys.argv[1])
from tes5_import.overrides.builder import _apply_generic
master = [(b'EDID', b'R\x00'), (b'RCLR', b'c'), (b'WNAM', b'w'),
          (b'RPLI', b'a'), (b'RPLD', b'A'), (b'RDAT', b'1'), (b'RDAT', b'2'),
          (b'RDMO', b'm'), (b'RDWT', b't')]
subs = {b'RPLD': [b'A', b'B', b'C'], b'RPLI': [b'a', b'b', b'c']}
print([s.decode() + ':' + p.decode() for s, p in _apply_generic(master, subs, set())])
'''


def test_surplus_goes_after_the_family_not_at_the_end():
    """Entries past the master's count stay contiguous with the family, before RDAT."""
    want = ['EDID:R\x00', 'RCLR:c', 'WNAM:w', 'RPLI:a', 'RPLD:A', 'RPLI:b',
            'RPLD:B', 'RPLI:c', 'RPLD:C', 'RDAT:1', 'RDAT:2', 'RDMO:m', 'RDWT:t']
    outputs = set()
    for seed in range(8):
        env = dict(os.environ, PYTHONHASHSEED=str(seed),
                   PYTHONDONTWRITEBYTECODE='1')
        outputs.add(subprocess.run(
            [sys.executable, '-c', _PROBE_TAIL, str(ROOT)], env=env,
            capture_output=True, text=True, check=True).stdout)
    assert outputs == {repr(want) + '\n'}, outputs


def test_family_the_master_lacks_goes_before_the_region_data():
    """A master REGN with no areas gets the plugin's RPLI/RPLD before RDAT, paired."""
    from tes5_import.overrides.builder import _apply_generic
    master = [(b'EDID', b'R\x00'), (b'RCLR', b'c'), (b'WNAM', b'w'),
              (b'RDAT', b'1'), (b'RDOT', b'o')]
    subs = {b'RPLI': [b'a', b'b'], b'RPLD': [b'A', b'B']}
    got = [s.decode() + ':' + p.decode()
           for s, p in _apply_generic(master, subs, set())]
    assert got == ['EDID:R\x00', 'RCLR:c', 'WNAM:w', 'RPLI:a', 'RPLD:A',
                   'RPLI:b', 'RPLD:B', 'RDAT:1', 'RDOT:o']

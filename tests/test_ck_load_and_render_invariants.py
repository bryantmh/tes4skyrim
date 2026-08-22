"""Invariants behind "the CK loads it" and "the game draws it".

Neither claim can literally be asserted offline -- these tests cannot launch
CreationKit.exe or SkyrimSE.exe. What they CAN do is pin the invariants whose
violation produced each failure, so the same defect cannot come back silently.
Each one is tied to a measured vanilla census; the counts in the docstrings are
from Skyrim.esm, never from our own output (censusing our own output is exactly
how the transposed block-sort key was once mistaken for vanilla's).

Two layers:

  * converter unit tests -- fast, no build artifact, always run;
  * built-ESM invariants -- skipped when output/<plugin> has not been built.

Background: docs/ck_vs_game_missing_objects.md and
docs/ck_reference_init_hang.md.
"""

import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tes5_import.record_types.items import convert_STAT, convert_TREE

PLUGIN = 'Oblivion.esm'
CELL_SIZE = 4096.0

# Record header flags.
F_PERSISTENT = 0x00000400
F_COMPRESSED = 0x00040000
# Bit 28. Vanilla Skyrim.esm sets it on 143 FURN, 16 REFR and exactly 1 STAT
# of 9,720 -- never on a TREE. It is a furniture flag; "Show in World Map" is
# not a STAT record-header flag and never was.
F_BOGUS_WORLD_MAP = 0x10000000


# --------------------------------------------------------------------------
# Converter unit tests
# --------------------------------------------------------------------------

def _persistent_rate_ok(straddle_pers, straddle_tot,
                        inside_pers, inside_tot, headroom=0.05):
    """Straddling a cell edge must not RAISE the persistent rate.

    Vanilla Skyrim.esm: 1.2% of straddlers vs 2.3% of non-straddlers, so the
    relationship -- not a fixed threshold -- is the invariant. The headroom
    keeps an authored plugin that genuinely persists a few large landmarks
    from failing.
    """
    if not straddle_tot or not inside_tot:
        return True
    return (straddle_pers / straddle_tot) <= (inside_pers / inside_tot) + headroom


def _flags_of(record: bytes) -> int:
    """RecordFlags out of a packed record header."""
    return struct.unpack_from('<I', record, 8)[0]


# A bounding box big enough to cross every size threshold the converters
# test. Without this the record falls back to the 100x100x80 STAT default and
# no size-gated branch fires at all -- the first version of these tests passed
# happily with the defect reintroduced, because of exactly that.
BIG_OBND = (-900, -900, -900, 900, 900, 900)


@pytest.fixture
def big_mesh(monkeypatch):
    """Give a known model path large cached bounds, and hand back the record."""
    from tes5_import import mesh_bounds
    from tes5_import.record_types.common import _prefix_path

    model = 'Architecture' + chr(92) + 'HugeTestWall.nif'
    key = _prefix_path(model).lower().replace(chr(92), '/')
    monkeypatch.setitem(mesh_bounds._MESH_BOUNDS, key, BIG_OBND)

    def make(sig):
        return {'Signature': sig, 'FormID': '00012345', 'RecordFlags': '0',
                'EditorID': f'HugeTest{sig}', 'Model.MODL': model}
    return make


def test_big_mesh_fixture_actually_bites(big_mesh):
    """Guard the guard: prove the fixture reaches the size-gated branches.

    0x8000 ("Has Distant LOD") is set for anything over 256 units, so seeing
    it is proof the converter read BIG_OBND rather than the small default.
    """
    assert _flags_of(convert_STAT(big_mesh('STAT'))) & 0x8000


@pytest.mark.parametrize('sig', ['STAT', 'TREE'])
def test_converter_never_sets_the_furniture_flag(big_mesh, sig):
    """0x10000000 must never reach a STAT or TREE, at any size.

    It was written on every object over 1024 units under the invented name
    "Show in World Map" -- 1,372 STATs and 70 TREEs, against a vanilla count
    of 1 and 0.
    """
    rec = big_mesh(sig)
    out = convert_STAT(rec) if sig == 'STAT' else convert_TREE(rec)
    assert not (_flags_of(out) & F_BOGUS_WORLD_MAP), (
        f'{sig} converter set 0x10000000; vanilla Skyrim.esm sets it on '
        f'1 STAT of 9,720 and no TREE at all')


def test_converter_preserves_authored_flags(big_mesh):
    """The guard above must not be implemented by blanking RecordFlags.

    0x8000 is legal on a STAT -- vanilla sets it on 826 of 9,720 -- so a
    converter that simply zeroed the field would pass the test above while
    destroying real data.
    """
    rec = big_mesh('STAT')
    rec['RecordFlags'] = str(0x8000)
    assert _flags_of(convert_STAT(rec)) & 0x8000


def test_persistent_rate_predicate_rejects_the_historical_defect():
    """The rate check must reject the numbers the shipped defect produced.

    Measured before the revert: 74,467 of 137,745 straddling refs persistent
    (54.1%) against 113 of 486,335 (0.02%) that straddle nothing. After:
    134 of 482,539 STAT and 3 of 141,541 TREE, i.e. flat.
    """
    assert not _persistent_rate_ok(74467, 137745, 113, 486335)
    assert _persistent_rate_ok(842, 67751, 4020, 173864)      # vanilla
    assert _persistent_rate_ok(134, 482539, 113, 486335)      # ours, fixed


# --------------------------------------------------------------------------
# Built-ESM invariants
# --------------------------------------------------------------------------

def _esm_path():
    from output_layout import paths
    return Path(str(paths(PLUGIN, out_root=str(ROOT / 'output')).esm))


@pytest.fixture(scope='module')
def esm():
    p = _esm_path()
    if not p.is_file():
        pytest.skip(f'no converted plugin at {p}; run convert.py --import-only')
    return p


@pytest.fixture(scope='module')
def placements(esm):
    """(base OBND radius, position, flags) for every exterior STAT/TREE ref.

    Parsed once for the whole module: the file is ~600 MB and each walk costs
    seconds, which is the difference between a test file that gets run and one
    that gets skipped.
    """
    data = esm.read_bytes()

    def subs(body):
        out, p, n = {}, 0, len(body)
        while p + 6 <= n:
            sig = body[p:p + 4]
            size = struct.unpack_from('<H', body, p + 4)[0]
            out[sig] = body[p + 6:p + 6 + size]
            p += 6 + size
        return out

    def body_of(off, size, flags):
        raw = data[off + 24:off + 24 + size]
        if flags & F_COMPRESSED:
            try:
                return zlib.decompress(raw[4:])
            except zlib.error:
                return b''
        return raw

    radius, base_flags = {}, {}
    p, n = 0, len(data)
    while p + 24 <= n:
        if data[p:p + 4] == b'GRUP':
            p += 24
            continue
        sig = data[p:p + 4]
        size, flags, fid = struct.unpack_from('<III', data, p + 4)
        if sig in (b'STAT', b'TREE'):
            base_flags[fid] = (sig.decode(), flags)
            v = subs(body_of(p, size, flags)).get(b'OBND')
            if v and len(v) >= 12:
                x1, y1, _z1, x2, y2, _z2 = struct.unpack('<6h', v)
                r = max(abs(x1), abs(x2)) ** 2 + max(abs(y1), abs(y2)) ** 2
                if r:
                    radius[fid] = r ** 0.5
        p += 24 + size

    refs = []

    def walk(off, end, stack):
        p = off
        while p + 24 <= end:
            if data[p:p + 4] == b'GRUP':
                gsize, label, gtype = struct.unpack_from('<IiI', data, p + 4)
                walk(p + 24, p + gsize, stack + [gtype])
                p += gsize
                continue
            sig = data[p:p + 4]
            size, flags, _fid = struct.unpack_from('<III', data, p + 4)
            if sig == b'REFR' and 1 in stack:
                s = subs(body_of(p, size, flags))
                nm, dat = s.get(b'NAME'), s.get(b'DATA')
                if nm and len(nm) >= 4 and dat and len(dat) >= 12:
                    base = struct.unpack_from('<I', nm, 0)[0]
                    r = radius.get(base)
                    if r:
                        px, py, _pz = struct.unpack_from('<fff', dat, 0)
                        refs.append((r, px, py, flags))
            p += 24 + size

    walk(0, len(data), [])
    return refs, base_flags


def test_no_base_record_carries_the_furniture_flag(placements):
    """Whole-file guard for the unit test above."""
    _refs, base_flags = placements
    bad = [(sig, fid) for fid, (sig, fl) in base_flags.items()
           if fl & F_BOGUS_WORLD_MAP]
    assert not bad, (
        f'{len(bad)} STAT/TREE records carry 0x10000000 '
        f'(vanilla: 1 STAT of 9,720, 0 TREE)')


def test_straddling_refs_are_not_force_persisted(placements):
    """A static crossing a cell edge must not be made Persistent for it.

    The CK warning "Ref ... should be persistent but is not" was read as a
    rule and applied to 74,467 refs. The objects then stopped rendering in
    game while the CK kept showing them (ICMarketBlock03House01 invisible,
    its untouched neighbour House02 fine, same cell and mesh family).

    Vanilla refutes the premise: of 67,751 cell-edge-straddling exterior
    STAT/TREE refs in Skyrim.esm only 842 (1.2%) are persistent -- LOWER than
    the 2.3% among the 173,864 refs that straddle nothing. Persistence marks
    "something references this", not "this is big".

    The assertion is the shape of that relationship rather than a fixed
    threshold: straddling must not RAISE the persistent rate. 5% absolute
    headroom keeps an authored plugin that genuinely persists a few large
    landmarks from failing.
    """
    refs, _base_flags = placements
    straddle = [0, 0]   # [total, persistent]
    inside = [0, 0]
    for r, px, py, flags in refs:
        ex, ey = px % CELL_SIZE, py % CELL_SIZE
        edge = min(ex, CELL_SIZE - ex, ey, CELL_SIZE - ey)
        bucket = straddle if edge < r else inside
        bucket[0] += 1
        bucket[1] += bool(flags & F_PERSISTENT)

    if straddle[0] < 100 or inside[0] < 100:
        pytest.skip('too few exterior STAT/TREE refs to compare rates')

    s_rate = straddle[1] / straddle[0]
    i_rate = inside[1] / inside[0]
    assert s_rate <= i_rate + 0.05, (
        'straddling a cell edge is inflating the persistent rate: '
        f'{s_rate:.1%} of {straddle[0]} straddling refs vs {i_rate:.1%} of '
        f'{inside[0]} that straddle nothing. Vanilla Skyrim.esm: 1.2% vs 2.3%')


# --------------------------------------------------------------------------
# CK load gates -- the tools own the logic, the test owns the wiring
# --------------------------------------------------------------------------

@pytest.mark.parametrize('tool,args', [
    # A cell filed in the block tree with no XCLC leaves the grid entry
    # unallocated and the streaming tick indexes a null array. Also checks
    # every XTEL destination grid square resolves: an empty one is the null
    # deref behind the "Initializing References" stall.
    ('cell_grid_check.py', ['--teleport-cells']),
    # A base-object GRUP written after CELL is not in the form map when a
    # REFR resolves its NAME, and the CK deletes the reference.
    ('esm_group_anchors.py', ['--order-only']),
])
def test_ck_load_gate(esm, tool, args):
    r = subprocess.run(
        [sys.executable, str(ROOT / 'tools' / tool), str(esm), *args],
        capture_output=True, text=True, timeout=300,
        encoding='utf-8', errors='replace')
    assert r.returncode == 0, (
        f'{tool} {" ".join(args)} failed:\n{r.stdout}\n{r.stderr}')


def test_render_audit_matches_authored_export(esm):
    """No render-blocking class may drift from what Oblivion authored.

    Absolute counts are meaningless here -- Oblivion.esm itself authors 1,007
    disabled-with-no-parent placements, 12 sub-0.01 scales and 9,661 enable
    parents, and a faithful conversion reproduces every one. Only a delta is a
    defect, so this needs the export dump and skips without it.
    """
    export = ROOT / 'export' / PLUGIN
    if not (export / 'REFR.txt').is_file():
        pytest.skip(f'no export dump at {export}')
    r = subprocess.run(
        [sys.executable, str(ROOT / 'tools' / 'refr_render_audit.py'),
         '--plugin', PLUGIN, '--export-dir', str(export)],
        capture_output=True, text=True, timeout=600,
        encoding='utf-8', errors='replace')
    assert r.returncode == 0, f'render audit reported a delta:\n{r.stdout}'

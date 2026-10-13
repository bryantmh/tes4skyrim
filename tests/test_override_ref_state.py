"""An override of a placed reference keeps the header flags its author set.

See: docs/commentary/tes5_import_override.md#override-reference-flags
"""

import struct
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import.base.text_reader import get_formid
from tes5_import.overrides import nested as OV
from tes5_import.record_types import world

FID = 0x01001234
DISABLED, DISTANT, PERSISTENT, DELETED = 0x800, 0x8000, 0x400, 0x20


def _record(sig, flags, subs, fid=FID):
    """A packed record from (signature, payload) pairs."""
    body = b''.join(s + struct.pack('<H', len(p)) + p for s, p in subs)
    return (sig + struct.pack('<II', len(body), flags)
            + struct.pack('<I', fid) + b'\x00' * 8 + body)


def _base(sig=b'REFR', flags=0, full=b'Old\x00', z=3.0):
    """A converted master reference: NAME, a map-marker FULL and DATA."""
    return _record(sig, flags, [
        (b'NAME', struct.pack('<I', 0x0100BBBB)), (b'FULL', full),
        (b'DATA', struct.pack('<6f', 1.0, 2.0, z, 0.0, 0.0, 0.0))])


def _export(sig, flags, **extra):
    """A TES4 export record of a placed reference."""
    rec = {'Signature': sig, 'FormID': '%08X' % FID, 'NAME': '0100BBBB',
           'ParentCELL': '0100AAAA', 'PosX': '1.0', 'PosY': '2.0',
           'PosZ': '3.0', 'RotX': '0.0', 'RotY': '0.0', 'RotZ': '0.0',
           'MapMarker.FULL': 'Old'}
    if flags is not None:
        rec['RecordFlags'] = str(flags)
    rec.update(extra)
    return rec


class _Index:
    """A converted master holding one record."""

    def __init__(self, record):
        """Keep the record `record()` answers with."""
        self._record = record

    def record(self, fid):
        """The record at `fid`, or b''."""
        return self._record if fid == FID else b''


def _ctx(monkeypatch, base, master_rec):
    """An OverrideContext over one fake master record; ids map 1:1."""
    monkeypatch.setattr(OV, 'master_output_formid',
                        lambda src, manifest: int(src, 16))
    ctx = object.__new__(OV.OverrideContext)
    ctx.master_index = _Index(base)
    ctx.master_manifest = None
    ctx.master_export = {'%08X' % FID: master_rec}
    ctx.stats = Counter()
    ctx.unmapped_keys = Counter()
    return ctx


def _build(monkeypatch, master_flags, plugin_flags, base_flags=None,
           sig='REFR', out_sig=b'REFR', **extra):
    """(Override, base bytes, context) for one override of the fake master."""
    base = _base(out_sig, master_flags if base_flags is None else base_flags)
    ctx = _ctx(monkeypatch, base, _export(sig, master_flags))
    return ctx.build(_export(sig, plugin_flags, **extra), sig), base, ctx


def _flags(record):
    """The header flags of a packed record; None for a dropped override."""
    return struct.unpack_from('<I', record, 8)[0] if record else None


def test_flags_only_disable_is_emitted(monkeypatch):
    """An author's Initially Disabled ships; every other byte is the master's."""
    ov, base, ctx = _build(monkeypatch, 0, DISABLED)
    assert ov.status == 'emitted'
    assert _flags(ov.record_bytes) == DISABLED
    assert ov.record_bytes[:8] == base[:8]
    assert ov.record_bytes[12:] == base[12:]
    assert (ctx.stats['emitted'], ctx.stats['unchanged']) == (1, 0)


def test_enable_clears_the_masters_bit(monkeypatch):
    """A reference the master disabled and the author enabled ships enabled."""
    ov, _base_bytes, _ctx_ = _build(monkeypatch, DISABLED, 0)
    assert ov.status == 'emitted'
    assert _flags(ov.record_bytes) == 0


@pytest.mark.parametrize('master, plugin, want', [
    (0, DISTANT, DISTANT),
    (DISTANT, 0, 0),
    (DISTANT, DISABLED, DISABLED),
    (0, DISABLED | DISTANT, DISABLED | DISTANT),
])
def test_visible_when_distant_follows_the_author(monkeypatch, master, plugin,
                                                 want):
    """Visible When Distant moves either way, alone or with Initially Disabled."""
    ov, _base_bytes, _ctx_ = _build(monkeypatch, master, plugin)
    assert _flags(ov.record_bytes) == want


def test_bits_the_master_run_set_survive(monkeypatch):
    """Bits only the master's converted record carries stay on the override."""
    ov, _base_bytes, _ctx_ = _build(monkeypatch, 0, DISABLED,
                                    base_flags=0x20000400)
    assert _flags(ov.record_bytes) == 0x20000C00


def test_a_bit_both_exports_agree_on_keeps_the_masters_value(monkeypatch):
    """Only a CHANGED bit moves: an agreed one is not copied from the plugin."""
    ov, _base_bytes, _ctx_ = _build(monkeypatch, DISABLED, DISABLED | DISTANT,
                                    base_flags=0)
    assert _flags(ov.record_bytes) == DISTANT
    ov, _base_bytes, _ctx_ = _build(monkeypatch, 0, DISTANT,
                                    base_flags=DISABLED)
    assert _flags(ov.record_bytes) == DISABLED | DISTANT


@pytest.mark.parametrize('bit', [0x80, 0x200, 0x1000, 0x10000, 0x20000000])
def test_other_header_bits_are_not_taken(monkeypatch, bit):
    """A header bit outside the two taken ones is still the master's."""
    ov, _base_bytes, _ctx_ = _build(monkeypatch, 0, bit)
    assert ov.status == 'unchanged'
    ov, base, _ctx_ = _build(monkeypatch, 0, bit | DISABLED)
    assert _flags(ov.record_bytes) == DISABLED
    assert ov.record_bytes[12:] == base[12:]


@pytest.mark.parametrize('master, plugin', [
    (0, PERSISTENT),
    (PERSISTENT, 0),
    (0, PERSISTENT | DISABLED),
    (PERSISTENT, DISABLED),
    (PERSISTENT | DISTANT, 0),
    (PERSISTENT, DISABLED | DISTANT),
])
def test_a_persistence_change_is_left_as_before(monkeypatch, master, plugin):
    """An override that also flips Persistent takes none of the author's flags."""
    ov, _base_bytes, ctx = _build(monkeypatch, master, plugin)
    assert ov.status == 'unchanged'
    assert (ctx.stats['emitted'], ctx.stats['unchanged']) == (0, 1)


def test_a_persistence_change_with_another_edit_keeps_master_flags(monkeypatch):
    """The other edit ships; the header stays the master's, bit for bit."""
    ov, base, _ctx_ = _build(monkeypatch, PERSISTENT, DISABLED,
                             **{'MapMarker.FULL': 'New'})
    assert ov.status == 'emitted'
    assert _flags(ov.record_bytes) == PERSISTENT
    assert ov.record_bytes == _base(flags=PERSISTENT, full=b'New\x00')
    assert ov.record_bytes != base


def test_disable_with_another_edit_ships_both(monkeypatch):
    """A renamed map marker that is also disabled carries the name and the bit."""
    ov, _base_bytes, _ctx_ = _build(monkeypatch, PERSISTENT,
                                    PERSISTENT | DISABLED,
                                    **{'MapMarker.FULL': 'New'})
    assert ov.record_bytes == _base(flags=PERSISTENT | DISABLED,
                                    full=b'New\x00')


def test_a_sunk_and_disabled_reference_ships_both(monkeypatch):
    """Moved below the world and Initially Disabled: the override has both."""
    ov, _base_bytes, _ctx_ = _build(monkeypatch, 0, DISABLED, PosZ='-30000.0')
    assert ov.record_bytes == _base(flags=DISABLED, z=-30000.0)


def test_an_edit_without_a_flag_change_keeps_the_header(monkeypatch):
    """With equal flags in both exports the header is the master's own."""
    ov, _base_bytes, _ctx_ = _build(monkeypatch, DISABLED, DISABLED,
                                    base_flags=0x20000000,
                                    **{'MapMarker.FULL': 'New'})
    assert ov.record_bytes == _base(flags=0x20000000, full=b'New\x00')


@pytest.mark.parametrize('plugin', [DISABLED, DISTANT, DISABLED | DISTANT])
def test_an_override_of_a_deleted_master_record_is_left_alone(monkeypatch,
                                                              plugin):
    """A deleted stub is no baseline: its override takes no flag."""
    ov, _base_bytes, _ctx_ = _build(monkeypatch, DELETED, plugin)
    assert ov.status == 'unchanged'
    ov, _base_bytes, _ctx_ = _build(monkeypatch, DELETED, plugin, base_flags=0)
    assert ov.status == 'unchanged'


@pytest.mark.parametrize('sig, out_sig', [
    ('ACHR', b'ACHR'), ('ACRE', b'ACHR'), ('REFR', b'ACHR'), ('REFR', b'REFR')])
def test_every_kind_of_reference_takes_the_flags(monkeypatch, sig, out_sig):
    """Placed NPCs, creatures and a REFR converted to ACHR are covered too."""
    ov, base, _ctx_ = _build(monkeypatch, 0, DISABLED | DISTANT, sig=sig,
                             out_sig=out_sig)
    assert ov.status == 'emitted'
    assert _flags(ov.record_bytes) == DISABLED | DISTANT
    assert ov.record_bytes[:4] == out_sig
    assert ov.record_bytes[12:] == base[12:]


@pytest.mark.parametrize('sig', ['ACTI', 'CELL', 'LAND', 'DOOR'])
def test_non_reference_flags_stay_ignored(monkeypatch, sig):
    """A record that is not a placed reference keeps the master's header."""
    ov, _base_bytes, _ctx_ = _build(monkeypatch, 0, DISABLED | DISTANT,
                                    sig=sig, out_sig=sig.encode())
    assert ov.status == 'unchanged'


@pytest.mark.parametrize('sig, master_sig, want', [
    ('REFR', 'ACTI', 'emitted'), ('ACTI', 'REFR', 'unchanged')])
def test_the_overrides_own_signature_decides(monkeypatch, sig, master_sig,
                                             want):
    """What the plugin's record is counts, not what the master's export says."""
    ctx = _ctx(monkeypatch, _base(sig.encode()), _export(master_sig, 0))
    assert ctx.build(_export(sig, DISABLED), sig).status == want


def test_a_compressed_master_record_keeps_its_body(monkeypatch):
    """Only the two bits move: the Compressed bit and the packed body stay."""
    base = _record(b'REFR', 0x40000, [(b'ZZZZ', b'\x01\x02\x03')])
    ctx = _ctx(monkeypatch, base, _export('REFR', 0))
    ov = ctx.build(_export('REFR', DISABLED), 'REFR')
    assert _flags(ov.record_bytes) == 0x40800
    assert ov.record_bytes[12:] == base[12:]


@pytest.mark.parametrize('master, plugin, base_flags, want', [
    (None, DISABLED, 0, DISABLED),
    (DISABLED, None, DISABLED, 0),
    ('', DISTANT, 0, DISTANT),
])
def test_a_missing_flags_value_reads_as_zero(monkeypatch, master, plugin,
                                             base_flags, want):
    """An export record with no usable RecordFlags counts as no flag set."""
    ctx = _ctx(monkeypatch, _base(flags=base_flags), _export('REFR', master))
    ov = ctx.build(_export('REFR', plugin), 'REFR')
    assert ov.status == 'emitted'
    assert _flags(ov.record_bytes) == want


def test_unmapped_keys_are_still_counted(monkeypatch):
    """A change with no output mapping is reported as before the flag step."""
    monkeypatch.setattr(OV, 'apply_changes',
                        lambda base, *_a: (base, set(), {'Some.Key'}))
    ov, _base_bytes, ctx = _build(monkeypatch, 0, 0, XSCL='2.0')
    assert ov.status == 'unchanged'
    assert ctx.unmapped_keys == Counter({'Some.Key': 1})


def test_a_persistence_only_change_gains_no_location(monkeypatch):
    """With the cell in a Location, +0x400 alone still changes nothing."""
    master, plugin = _export('REFR', 0), _export('REFR', PERSISTENT)
    for rec in (master, plugin):
        del rec['MapMarker.FULL']
    monkeypatch.setitem(world._CELL_LOCATION,
                        get_formid(plugin, 'ParentCELL'), 0x0100CCCC)
    base = world.convert_REFR(master)
    assert b'XLCN' in world.convert_REFR(plugin) and b'XLCN' not in base
    ctx = _ctx(monkeypatch, base, master)
    assert ctx.build(plugin, 'REFR').status == 'unchanged'

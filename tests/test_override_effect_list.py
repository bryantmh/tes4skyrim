"""An override that changes a magic item's effect list is reconverted, not spliced.

See: docs/commentary/tes5_import_override.md#effect-lists-are-reconverted
"""
import struct
from collections import Counter
from types import SimpleNamespace

import pytest

from tes5_import import pipeline_records
from tes5_import.base.writer import PluginWriter
from tes5_import.overrides import nested
from tes5_import.overrides.builder import RECONVERT_KEYS
from tes5_import.record_types import equipment, magic

ITEM_TYPES = ('ALCH', 'INGR', 'SGST')
CONVERT = {'ALCH': equipment.convert_ALCH, 'INGR': equipment.convert_INGR, 'SGST': equipment.convert_SGST,
           'SPEL': equipment.convert_SPEL, 'ENCH': equipment.convert_ENCH}
FLAGS = magic.T4_SELF | magic.T4_DETRIMENTAL
EFFECTS = [{'EditorID': code, 'FormID': f'{0x1200 + n:08X}', 'FULL': code, 'DATA.School': '5', 'DATA.Flags': str(FLAGS)}
           for n, code in enumerate(('FIDG', 'FRDG', 'SHDG'))]
TOUCH = {'EditorID': 'DGHE', 'FormID': '00001210', 'FULL': 'DGHE', 'DATA.School': '5',
         'DATA.Flags': str(magic.T4_TOUCH)}


@pytest.fixture(autouse=True)
def _effects_indexed():
    """Every test converts against the same three indexed effects."""
    magic.register_mgef_formids(EFFECTS)
    yield
    magic.register_mgef_formids([])


def _item(sig: str, effects: list, name: str = 'Item') -> dict:
    """An exported magic item of type `sig` holding `effects` as (code, magnitude)."""
    rec = {'Signature': sig, 'FormID': '00000A30', 'EditorID': 'Item', 'RecordFlags': '0', 'FULL': name,
           'Model.MODL': 'x.nif', 'DATA.Weight': '0.1', 'DATA.Value': '5', 'ENIT.Value': '5', 'ENIT.Flags': '0',
           'EffectCount': str(len(effects))}
    for i, (code, magnitude) in enumerate(effects):
        rec.update({f'Effect[{i}].EFID': code, f'Effect[{i}].Magnitude': str(magnitude), f'Effect[{i}].Area': '0',
                    f'Effect[{i}].Duration': '10', f'Effect[{i}].Type': 'Self'})
    return rec


def _shape(record: bytes) -> list:
    """The record's effect subrecords in order, an EFIT with its magnitude."""
    out, pos = [], 24
    while pos + 6 <= len(record):
        sig, size = record[pos:pos + 4], struct.unpack_from('<H', record, pos + 4)[0]
        if sig == b'EFID':
            out.append('EFID')
        elif sig == b'EFIT':
            out.append(f"EFIT {struct.unpack_from('<f', record, pos + 6)[0]:g}")
        pos += 6 + size
    return out


class _Index:
    """A master index holding one converted record."""

    def __init__(self, record: bytes):
        """Keep the record every id resolves to."""
        self._record = record

    def record(self, _fid: int) -> bytes:
        """The master's converted record."""
        return self._record


def _context(master_rec: dict) -> nested.OverrideContext:
    """An override context whose one master record is `master_rec`, converted by its own converter."""
    ctx = nested.OverrideContext.__new__(nested.OverrideContext)
    ctx.master_export = {master_rec['FormID']: master_rec}
    ctx.master_manifest = None
    ctx.master_index = _Index(CONVERT[master_rec['Signature']](master_rec))
    ctx.stats, ctx.unmapped_keys = Counter(), Counter()
    return ctx


@pytest.mark.parametrize('sig', ITEM_TYPES + ('SPEL', 'ENCH'))
@pytest.mark.parametrize('key', ['Effect[]', 'ScriptEffect[]'])
def test_every_effect_list_key_is_reconverted(sig, key):
    """Each magic item type reconverts on either effect-list key."""
    assert (sig, key) in RECONVERT_KEYS


@pytest.mark.parametrize('sig', ITEM_TYPES)
@pytest.mark.parametrize('changed', [
    [('FIDG', 5), ('FRDG', 9)],
    [('FIDG', 5), ('FRDG', 5), ('SHDG', 5)],
    [('FIDG', 5)],
    [('SHDG', 5), ('FRDG', 5)],
], ids=['a magnitude', 'an effect more', 'an effect less', 'another effect'])
def test_a_changed_effect_list_reconverts_the_item(sig, changed):
    """The override pass hands such an item back to the normal converter."""
    ctx = _context(_item(sig, [('FIDG', 5), ('FRDG', 5)]))
    override = ctx.build(_item(sig, changed), sig)
    assert (override.status, override.record_bytes) == ('reconvert', b'')
    assert ctx.stats == Counter(reconverted=1)


@pytest.mark.parametrize('sig', ITEM_TYPES)
def test_an_item_renamed_only_is_still_spliced(sig):
    """A change outside the effect list keeps the master's bytes and its effect list."""
    master = _item(sig, [('FIDG', 5), ('FRDG', 7)])
    ctx = _context(master)
    override = ctx.build(_item(sig, [('FIDG', 5), ('FRDG', 7)], name='Renamed'), sig)
    assert override.status == 'emitted' and b'Renamed\0' in override.record_bytes
    assert _shape(override.record_bytes) == _shape(CONVERT[sig](master))
    assert _shape(override.record_bytes)[:4] == ['EFID', 'EFIT 5', 'EFID', 'EFIT 7']


@pytest.mark.parametrize('sig', ITEM_TYPES)
def test_an_item_that_changes_nothing_is_dropped(sig):
    """An identical copy of the master's item is no override."""
    ctx = _context(_item(sig, [('FIDG', 5), ('FRDG', 7)]))
    assert ctx.build(_item(sig, [('FIDG', 5), ('FRDG', 7)]), sig).status == 'unchanged'


@pytest.mark.parametrize('sig', ITEM_TYPES)
def test_an_effect_change_beside_another_change_reconverts_the_item(sig):
    """A changed effect list reconverts the item whatever else changed."""
    ctx = _context(_item(sig, [('FIDG', 5), ('FRDG', 5)]))
    assert ctx.build(_item(sig, [('FIDG', 5), ('FRDG', 9)], name='Renamed'), sig).status == 'reconvert'


@pytest.mark.parametrize('sig', ITEM_TYPES)
def test_another_script_on_the_same_effects_reconverts_the_item(sig):
    """The script an effect names is part of the effect list."""
    master, plugin = (dict(_item(sig, [('FIDG', 5), ('FRDG', 5)]), **{'ScriptEffect[1].FormID': scpt})
                      for scpt in ('00000B01', '00000B02'))
    assert _context(master).build(plugin, sig).status == 'reconvert'


@pytest.mark.parametrize('sig', ITEM_TYPES)
def test_a_deleted_item_is_deleted_not_reconverted(sig):
    """The author's deletion wins over the stub's missing effect list."""
    ctx = _context(_item(sig, [('FIDG', 5), ('FRDG', 5)]))
    stub = {'Signature': sig, 'FormID': '00000A30', 'RecordFlags': '32'}
    assert ctx.build(stub, sig).status == 'deleted'


@pytest.mark.parametrize('sig', ITEM_TYPES)
def test_phase_1_writes_the_reconverted_item_once_with_the_writer(sig, monkeypatch):
    """Phase 1 converts it as a new record: with the writer, once, in the converted signature's group."""
    magic.register_mgef_formids(EFFECTS + [TOUCH])
    master, plugin = _item(sig, [('FIDG', 5), ('DGHE', 5)]), _item(sig, [('FIDG', 5), ('DGHE', 9)])
    monkeypatch.setattr(pipeline_records, '_stage_runtime_sidecars', lambda st, export_dir: None)
    state = SimpleNamespace(by_type={sig: [plugin]}, all_skip=set(), ctx=_context(master),
                            writer=PluginWriter(masters=['Skyrim.esm', 'Master.esm']))
    pipeline_records._phase1_simple_records(state, '', lambda label: None, set())
    want = CONVERT[sig](plugin, writer=PluginWriter(masters=['Skyrim.esm', 'Master.esm']))
    assert want != CONVERT[sig](plugin)
    written = {group: records for group, records in state.writer._top_groups.items() if group != 'MGEF'}
    assert written == {want[:4].decode(): [want]}

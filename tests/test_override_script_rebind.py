"""A quest override binds the plugin's properties when the plugin edits its script.

An override is the master's bytes plus the authored field changes. Frostcrag
Reborn edits DLCFrostcrag's quest SCRIPT, not the quest, so the quest shipped
the master's 14 properties and FR's MagesGuild/DLCFrostcragLevel were None.
"""
from collections import Counter
from types import SimpleNamespace

from tes5_import import pipeline_records as pr
from tes5_import.base.writer import pack_record, pack_subrecord
from tes5_import.overrides.builder import replace_vmad, split_subrecords
from tes5_import.overrides.nested import Override, OverrideContext


def _qust(*subs):
    return pack_record('QUST', 0x01000D53, 0, b''.join(
        pack_subrecord(sig, data) for sig, data in subs))


def _ctx(master_scpts):
    ctx = OverrideContext.__new__(OverrideContext)
    ctx.master_export = master_scpts
    ctx.stats = Counter()
    ctx.master_index = SimpleNamespace(record=lambda fid: MASTER)
    return ctx


MASTER = _qust(('EDID', b'Q\0'), ('VMAD', b'master'), ('FULL', b'x\0'))


class TestReplaceVmad:

    def test_replaces_in_place(self):
        out = split_subrecords(replace_vmad(MASTER, b'plugin'))
        assert out == [(b'EDID', b'Q\0'), (b'VMAD', b'plugin'), (b'FULL', b'x\0')]

    def test_inserts_after_edid_when_absent(self):
        rec = _qust(('EDID', b'Q\0'), ('FULL', b'x\0'))
        assert [s for s, _ in split_subrecords(replace_vmad(rec, b'v'))] == \
            [b'EDID', b'VMAD', b'FULL']

    def test_unparseable_is_empty(self):
        assert replace_vmad(b'QUST', b'v') == b''


class TestEditsMasterScript:

    def test_changed_source(self):
        ctx = _ctx({'01000D54': {'FormID': '01000D54', 'SCTX': 'scn A'}})
        assert ctx.edits_master_script({'FormID': '01000D54', 'SCTX': 'scn A\nx'})

    def test_same_source_is_not_an_edit(self):
        ctx = _ctx({'01000D54': {'FormID': '01000D54', 'SCTX': 'scn A'}})
        assert not ctx.edits_master_script({'FormID': '01000D54', 'SCTX': 'scn A'})

    def test_own_script_or_none_is_not_a_master_edit(self):
        ctx = _ctx({})
        assert not ctx.edits_master_script({'FormID': '03000804', 'SCTX': 'scn B'})
        assert not ctx.edits_master_script(None)


class TestRebind:

    def _st(self, plugin_sctx):
        return SimpleNamespace(
            ctx=_ctx({'01000D54': {'FormID': '01000D54', 'SCTX': 'scn A'}}),
            by_type={'SCPT': [{'FormID': '01000D54', 'SCTX': plugin_sctx}]},
            _scpt_by_fid=None)

    def _run(self, st, status, record_bytes, monkeypatch):
        monkeypatch.setattr(pr, '_convert_qust_record',
                            lambda st, rec: _qust(('EDID', b'Q\0'), ('VMAD', b'plugin')))
        rec = {'FormID': '01000D53', 'EditorID': 'Q', 'SCRI': '01000D54'}
        return pr._rebind_edited_script(st, rec, Override(status, 0x01000D53, record_bytes))

    def test_edited_script_takes_this_runs_vmad(self, monkeypatch):
        out = self._run(self._st('scn A\nx'), 'emitted', MASTER, monkeypatch)
        assert dict(split_subrecords(out))[b'VMAD'] == b'plugin'

    def test_unchanged_quest_is_emitted_when_its_script_changed(self, monkeypatch):
        """No authored quest field change, but the script still differs."""
        out = self._run(self._st('scn A\nx'), 'unchanged', b'', monkeypatch)
        assert dict(split_subrecords(out))[b'VMAD'] == b'plugin'

    def test_untouched_script_keeps_the_masters_vmad(self, monkeypatch):
        assert self._run(self._st('scn A'), 'emitted', MASTER, monkeypatch) == b''

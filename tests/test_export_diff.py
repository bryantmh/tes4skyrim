"""Author-intent diffing between master and plugin exports."""

from tes5_import.export_diff import changed_keys, diff_records


class TestScalars:
    def test_identical_records_report_no_change(self):
        rec = {'FormID': '00001234', 'EditorID': 'X', 'FULL': 'Name'}
        assert diff_records(rec, dict(rec)) == {}

    def test_changed_scalar_is_reported_with_the_plugin_value(self):
        m = {'FormID': '00001234', 'FULL': 'Skelett'}
        p = {'FormID': '00001234', 'FULL': 'Skeleton'}
        assert diff_records(m, p) == {'FULL': 'Skeleton'}

    def test_untouched_reference_is_never_reported(self):
        """The defect that hung the game: comparing two CONVERSIONS reported
        1821 changed NPC_ RNAMs; the authors changed none of them."""
        m = {'FormID': '1', 'FULL': 'Skelett', 'RNAM.Race': '00000B98'}
        p = {'FormID': '1', 'FULL': 'Skeleton', 'RNAM.Race': '00000B98'}
        assert changed_keys(m, p) == {'FULL'}

    def test_key_present_on_only_one_side_is_a_change(self):
        assert changed_keys({'FormID': '1'}, {'FormID': '1', 'FULL': 'N'}) \
            == {'FULL'}
        assert changed_keys({'FormID': '1', 'FULL': 'N'}, {'FormID': '1'}) \
            == {'FULL'}

    def test_identity_keys_are_ignored(self):
        m = {'FormID': '00001234', 'Signature': 'NPC_', 'EditorID': 'A'}
        p = {'FormID': '00009999', 'Signature': 'NPC_', 'EditorID': 'B'}
        assert diff_records(m, p) == {}


class TestIndexedLists:
    def test_reordered_list_is_not_a_change(self):
        """Oblivion does not preserve list order between a master and an
        overriding plugin: 1166 of 1264 Nehrim NPC_ inventories differ
        positionally, only 5 differ as a set."""
        m = {'Item[0].FormID': 'A', 'Item[1].FormID': 'B',
             'Item[2].FormID': 'C'}
        p = {'Item[0].FormID': 'C', 'Item[1].FormID': 'A',
             'Item[2].FormID': 'B'}
        assert diff_records(m, p) == {}

    def test_genuinely_different_list_is_a_change(self):
        m = {'Item[0].FormID': 'A', 'Item[1].FormID': 'B'}
        p = {'Item[0].FormID': 'A', 'Item[1].FormID': 'Z'}
        assert changed_keys(m, p) == {'Item[]'}

    def test_list_length_change_is_reported(self):
        m = {'Item[0].FormID': 'A'}
        p = {'Item[0].FormID': 'A', 'Item[1].FormID': 'B'}
        assert changed_keys(m, p) == {'Item[]'}

    def test_entry_fields_are_compared_together(self):
        """Same FormIDs, different counts -> a real change."""
        m = {'Item[0].FormID': 'A', 'Item[0].Count': '1'}
        p = {'Item[0].FormID': 'A', 'Item[0].Count': '5'}
        assert changed_keys(m, p) == {'Item[]'}

    def test_reorder_keeps_entry_fields_paired(self):
        m = {'Item[0].FormID': 'A', 'Item[0].Count': '1',
             'Item[1].FormID': 'B', 'Item[1].Count': '2'}
        p = {'Item[1].FormID': 'A', 'Item[1].Count': '1',
             'Item[0].FormID': 'B', 'Item[0].Count': '2'}
        assert diff_records(m, p) == {}

    def test_derived_counts_are_ignored(self):
        """ItemCount follows from the list; comparing it double-reports."""
        m = {'ItemCount': '2', 'Item[0].FormID': 'A', 'Item[1].FormID': 'B'}
        p = {'ItemCount': '2', 'Item[1].FormID': 'A', 'Item[0].FormID': 'B'}
        assert diff_records(m, p) == {}

    def test_independent_lists_do_not_interfere(self):
        m = {'Item[0].FormID': 'A', 'Faction[0].FormID': 'F'}
        p = {'Item[0].FormID': 'Z', 'Faction[0].FormID': 'F'}
        assert changed_keys(m, p) == {'Item[]'}


class TestRealWorldShape:
    def test_translation_record_changes_only_its_name(self):
        m = {'FormID': '000018DC', 'Signature': 'NPC_', 'EditorID': 'Guard',
             'FULL': 'Wache', 'RNAM.Race': '00000B98',
             'ItemCount': '3',
             'Item[0].FormID': '000229A7', 'Item[1].FormID': '000229A8',
             'Item[2].FormID': '0000000F'}
        p = {'FormID': '000018DC', 'Signature': 'NPC_', 'EditorID': 'Guard',
             'FULL': 'Guard', 'RNAM.Race': '00000B98',
             'ItemCount': '3',
             'Item[0].FormID': '0000000F', 'Item[1].FormID': '000229A8',
             'Item[2].FormID': '000229A7'}
        assert diff_records(m, p) == {'FULL': 'Guard'}

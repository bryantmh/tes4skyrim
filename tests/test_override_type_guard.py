"""An override must resolve to a master record of the SAME record type.

A plugin's source FormID can convert to an id that already belongs to an
unrelated record in the master's own space. Elsweyr Anequina's NPC_ 0100110C
converts to 0200110C, which in Oblivion.esm's converted output is a REFR.
Adopting that record's bytes and its GRUP nesting shipped the NPC_ as a
"REFR" inside a fabricated top-level group -- xEdit: "File contains top level
group without known sort order: GRUP Top 'REFR'" -- with a full NPC_ body
(ACBS/AIDT/VTCK/CNTO) under a REFR signature.

The guard treats a type mismatch as "no master record to override", so the
caller converts the record as the new record it actually is.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import.overrides.nested import _expected_output_sig, _signature_mismatch


class TestExpectedOutputSig:
    def test_unrenamed_types_map_to_themselves(self):
        for sig in ('NPC_', 'REFR', 'LAND', 'CELL', 'WRLD', 'STAT'):
            assert _expected_output_sig(sig) == sig.encode()

    @pytest.mark.parametrize('tes4, tes5', [
        ('CREA', b'NPC_'),
        ('CLOT', b'ARMO'),
        ('LVLC', b'LVLN'),
        ('HAIR', b'HDPT'),
        ('SGST', b'SCRL'),
        ('APPA', b'MISC'),
        ('SBSP', b'STAT'),
        ('ACRE', b'ACHR'),
    ])
    def test_renamed_types_use_TYPE_MAP(self, tes4, tes5):
        """The renames are real and must NOT be rejected by the guard."""
        assert _expected_output_sig(tes4) == tes5

    def test_empty_signature_disables_the_check(self):
        assert _expected_output_sig('') == b''


class TestGuardLogic:
    """The comparison the guard performs."""

    def test_npc_resolving_to_a_refr_is_rejected(self):
        # The exact ElsweyrAnequina case.
        assert _signature_mismatch('NPC_', b'REFR')

    def test_land_resolving_to_a_crea_is_rejected(self):
        assert _signature_mismatch('LAND', b'CREA')

    def test_matching_type_is_accepted(self):
        assert not _signature_mismatch('NPC_', b'NPC_')
        assert not _signature_mismatch('LAND', b'LAND')

    def test_legitimate_rename_is_accepted(self):
        """CREA overriding the master's converted NPC_ must still work."""
        assert not _signature_mismatch('CREA', b'NPC_')
        assert not _signature_mismatch('ACRE', b'ACHR')

    def test_refr_placing_a_levelled_creature_becomes_an_achr(self):
        """A REFR whose base is an LVLC converts to ACHR + a shell NPC_.

        Rejecting it dropped TWMP's REFR 0201A56B, whose base 0206B333 is one
        of ElsweyrAnequina's LVLC records.
        """
        assert not _signature_mismatch('REFR', b'ACHR')

    def test_enchanted_book_becomes_a_scroll(self):
        """A BOOK with ENAM converts to SCRL, so its override must be accepted."""
        assert not _signature_mismatch('BOOK', b'SCRL')
        assert _signature_mismatch('BOOK', b'NPC_')

    def test_refr_still_rejects_an_unrelated_type(self):
        assert _signature_mismatch('REFR', b'LAND')
        assert _signature_mismatch('REFR', b'CELL')

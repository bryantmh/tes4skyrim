"""
Per-record-type tests for the TES4 export tool.

Tests parse actual Oblivion.esm records and validate the export output
for correctness, field presence, and data integrity.

Usage:
    python -m pytest tes4_export/tests/test_export.py -v
    python -m pytest tes4_export/tests/test_export.py -v -k "test_STAT"
    python -m tes4_export/tests/test_export.py   (standalone)
"""

import os
import unittest

# Determine Oblivion.esm path
OBLIVION_ESM = None
_candidates = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Oblivion\Data\Oblivion.esm",
    r"C:\Program Files\Steam\steamapps\common\Oblivion\Data\Oblivion.esm",
]
for p in _candidates:
    if os.path.isfile(p):
        OBLIVION_ESM = p
        break

# Lazy-loaded parsed records cache
_cache = {}


def get_records():
    """Parse Oblivion.esm once and cache result."""
    if "records" not in _cache:
        from tes4_export.tes4_reader import read_file
        header, records = read_file(OBLIVION_ESM)
        _cache["header"] = header
        _cache["records"] = records
        # Index by type for fast lookup
        from collections import defaultdict
        by_type = defaultdict(list)
        for rec in records:
            by_type[rec.type].append(rec)
        _cache["by_type"] = by_type
    return _cache


def get_by_type(sig: str) -> list:
    """Get all records of a given type."""
    data = get_records()
    return data["by_type"].get(sig, [])


def find_record(sig: str, edid: str = None, form_id: int = None):
    """Find a specific record by EditorID or FormID."""
    from tes4_export.tes4_reader import get_string, get_subrecord
    recs = get_by_type(sig)
    for rec in recs:
        if edid:
            edid_sub = get_subrecord(rec, "EDID")
            if edid_sub and get_string(edid_sub) == edid:
                return rec
        if form_id is not None and rec.form_id == form_id:
            return rec
    return None


def export_record(rec) -> dict:
    """Export a record and parse the output lines into a dict."""
    from tes4_export.export import format_record
    text = format_record(rec)
    result = {}
    for line in text.split("\n"):
        if line.startswith("---") or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key] = value
    return result


def skip_if_no_esm(fn):
    """Decorator to skip tests if Oblivion.esm not found."""
    def wrapper(*args, **kwargs):
        if OBLIVION_ESM is None:
            raise unittest.SkipTest("Oblivion.esm not found")
        return fn(*args, **kwargs)
    return wrapper


class TestBinaryReader(unittest.TestCase):
    """Test the core binary reader."""

    @skip_if_no_esm
    def test_parse_file(self):
        data = get_records()
        self.assertGreater(len(data["records"]), 1000000)

    @skip_if_no_esm
    def test_header(self):
        data = get_records()
        header = data["header"]
        self.assertEqual(header.type, "TES4")

    @skip_if_no_esm
    def test_record_types_present(self):
        data = get_records()
        types = set(data["by_type"].keys())
        expected = {"STAT", "NPC_", "CREA", "WEAP", "ARMO", "CELL", "REFR",
                    "ENCH", "SPEL", "ALCH", "BOOK", "CONT", "DOOR", "MISC",
                    "KEYM", "FACT", "RACE", "CLAS", "DIAL", "INFO", "QUST",
                    "PACK", "WRLD", "LAND", "LTEX", "GLOB", "GMST"}
        self.assertTrue(expected.issubset(types),
                        f"Missing types: {expected - types}")


class TestSTAT(unittest.TestCase):
    @skip_if_no_esm
    def test_basic_stat(self):
        recs = get_by_type("STAT")
        self.assertGreater(len(recs), 5000)
        # Just check the first record has the right fields
        d = export_record(recs[0])
        self.assertEqual(d["Signature"], "STAT")
        self.assertIn("EditorID", d)
        self.assertIn("Model.MODL", d)


class TestNPC(unittest.TestCase):
    @skip_if_no_esm
    def test_npc_fields(self):
        # Imperial Watch Captain is a well-known NPC
        rec = find_record("NPC_", edid="ImperialWatchCaptain")
        if rec is None:
            # Try another common NPC
            recs = get_by_type("NPC_")
            self.assertGreater(len(recs), 0)
            rec = recs[0]
        d = export_record(rec)
        self.assertEqual(d["Signature"], "NPC_")
        self.assertIn("ACBS.Flags", d)
        self.assertIn("ACBS.Level", d)
        self.assertIn("FactionCount", d)

    @skip_if_no_esm
    def test_npc_data_skills(self):
        """Verify NPC_ DATA has all 21 skill fields."""
        recs = get_by_type("NPC_")
        # Find one with DATA
        for rec in recs[:50]:
            d = export_record(rec)
            if "DATA.Blade" in d:
                self.assertIn("DATA.Armorer", d)
                self.assertIn("DATA.Speechcraft", d)
                self.assertIn("DATA.Health", d)
                self.assertIn("DATA.Strength", d)
                self.assertIn("DATA.Luck", d)
                return
        self.skipTest("No NPC_ with DATA skills found in first 50")

    @skip_if_no_esm
    def test_faction_rank_reasonable(self):
        """Verify faction ranks are small values (not garbage from misparse)."""
        recs = get_by_type("NPC_")
        for rec in recs[:100]:
            d = export_record(rec)
            fc = int(d.get("FactionCount", "0"))
            for i in range(fc):
                rank = d.get(f"Faction[{i}].Rank")
                if rank is not None:
                    rank_val = int(rank)
                    self.assertGreaterEqual(rank_val, -1,
                                            f"Rank too low for {d.get('EditorID')}")
                    self.assertLessEqual(rank_val, 127,
                                         f"Rank too high for {d.get('EditorID')}: {rank_val}")


class TestCREA(unittest.TestCase):
    @skip_if_no_esm
    def test_creature_fields(self):
        recs = get_by_type("CREA")
        self.assertGreater(len(recs), 0)
        d = export_record(recs[0])
        self.assertEqual(d["Signature"], "CREA")
        self.assertIn("ACBS.Flags", d)

    @skip_if_no_esm
    def test_creature_data(self):
        recs = get_by_type("CREA")
        for rec in recs[:20]:
            d = export_record(rec)
            if "DATA.Type" in d:
                self.assertIn("DATA.CombatSkill", d)
                self.assertIn("DATA.Health", d)
                self.assertIn("DATA.Strength", d)
                return
        self.skipTest("No CREA with DATA found")


class TestWEAP(unittest.TestCase):
    @skip_if_no_esm
    def test_weapon_data(self):
        """Verify WEAP DATA fields parse correctly."""
        recs = get_by_type("WEAP")
        found = False
        for rec in recs[:50]:
            d = export_record(rec)
            if "DATA.Type" in d:
                found = True
                self.assertIn("DATA.Speed", d)
                self.assertIn("DATA.Reach", d)
                self.assertIn("DATA.Value", d)
                self.assertIn("DATA.Damage", d)
                # Sanity check values
                speed = float(d["DATA.Speed"])
                self.assertGreater(speed, 0)
                self.assertLess(speed, 10)
                damage = int(d["DATA.Damage"])
                self.assertGreaterEqual(damage, 0)
                self.assertLess(damage, 1000)
                break
        self.assertTrue(found, "No WEAP with DATA found")


class TestARMO(unittest.TestCase):
    @skip_if_no_esm
    def test_armor_fields(self):
        recs = get_by_type("ARMO")
        self.assertGreater(len(recs), 0)
        d = export_record(recs[0])
        self.assertEqual(d["Signature"], "ARMO")
        self.assertIn("BMDT.BipedFlags", d)

    @skip_if_no_esm
    def test_armor_data(self):
        recs = get_by_type("ARMO")
        for rec in recs[:20]:
            d = export_record(rec)
            if "DATA.ArmorRating" in d:
                rating = int(d["DATA.ArmorRating"])
                self.assertGreaterEqual(rating, 0)
                self.assertLess(rating, 10000)
                return
        self.skipTest("No ARMO with DATA found")


class TestCLOT(unittest.TestCase):
    @skip_if_no_esm
    def test_clothing_fields(self):
        recs = get_by_type("CLOT")
        self.assertGreater(len(recs), 0)
        d = export_record(recs[0])
        self.assertEqual(d["Signature"], "CLOT")


class TestENCH(unittest.TestCase):
    @skip_if_no_esm
    def test_enchantment_effects(self):
        """Verify ENCH effect magnitudes are sane values."""
        recs = get_by_type("ENCH")
        for rec in recs[:30]:
            d = export_record(rec)
            if "EffectCount" in d:
                count = int(d["EffectCount"])
                if count > 0 and "Effect[0].Magnitude" in d:
                    mag = int(d["Effect[0].Magnitude"])
                    # Magnitudes should be reasonable (0-500 typical)
                    self.assertGreaterEqual(mag, 0)
                    self.assertLess(mag, 100000,
                                    f"Magnitude {mag} suspiciously large for {d.get('EditorID')}")
                    return
        self.skipTest("No ENCH with effects found")

    @skip_if_no_esm
    def test_enit_flags_u8(self):
        """Verify ENIT.Flags is a small value (u8, not garbage from u32 read)."""
        recs = get_by_type("ENCH")
        for rec in recs[:50]:
            d = export_record(rec)
            if "ENIT.Flags" in d:
                flags = int(d["ENIT.Flags"])
                self.assertLessEqual(flags, 255,
                                     f"ENIT.Flags={flags} too large, should be u8")


class TestSPEL(unittest.TestCase):
    @skip_if_no_esm
    def test_spell_fields(self):
        recs = get_by_type("SPEL")
        self.assertGreater(len(recs), 0)
        d = export_record(recs[0])
        self.assertEqual(d["Signature"], "SPEL")

    @skip_if_no_esm
    def test_spit_flags_u8(self):
        recs = get_by_type("SPEL")
        for rec in recs[:50]:
            d = export_record(rec)
            if "SPIT.Flags" in d:
                flags = int(d["SPIT.Flags"])
                self.assertLessEqual(flags, 255)


class TestALCH(unittest.TestCase):
    @skip_if_no_esm
    def test_potion_fields(self):
        recs = get_by_type("ALCH")
        self.assertGreater(len(recs), 0)
        d = export_record(recs[0])
        self.assertEqual(d["Signature"], "ALCH")

    @skip_if_no_esm
    def test_potion_effects(self):
        recs = get_by_type("ALCH")
        for rec in recs[:20]:
            d = export_record(rec)
            if "EffectCount" in d and int(d["EffectCount"]) > 0:
                self.assertIn("Effect[0].EFID", d)
                self.assertIn("Effect[0].Magnitude", d)
                return


class TestCELL(unittest.TestCase):
    @skip_if_no_esm
    def test_cell_count(self):
        recs = get_by_type("CELL")
        self.assertGreater(len(recs), 30000)

    @skip_if_no_esm
    def test_interior_cell(self):
        """Find an interior cell with lighting data."""
        recs = get_by_type("CELL")
        for rec in recs[:100]:
            d = export_record(rec)
            if "XCLL.AmbientR" in d:
                # Has lighting data - must be interior
                r = int(d["XCLL.AmbientR"])
                self.assertGreaterEqual(r, 0)
                self.assertLessEqual(r, 255)
                return

    @skip_if_no_esm
    def test_exterior_cell(self):
        """Find an exterior cell with grid coordinates."""
        recs = get_by_type("CELL")
        for rec in recs[:1000]:
            d = export_record(rec)
            if "XCLC.X" in d:
                x = int(d["XCLC.X"])
                int(d["XCLC.Y"])  # Verify Y is parseable
                # Grid coordinates should be reasonable
                self.assertGreater(x, -200)
                self.assertLess(x, 200)
                return


class TestREFR(unittest.TestCase):
    @skip_if_no_esm
    def test_refr_count(self):
        recs = get_by_type("REFR")
        self.assertGreater(len(recs), 1000000)

    @skip_if_no_esm
    def test_refr_has_name(self):
        """Most REFRs should have NAME (base object reference)."""
        recs = get_by_type("REFR")
        count_with_name = sum(1 for rec in recs[:1000]
                              if any(s.type == "NAME" for s in rec.subrecords))
        self.assertGreater(count_with_name, 900)

    @skip_if_no_esm
    def test_refr_placement(self):
        recs = get_by_type("REFR")
        for rec in recs[:50]:
            d = export_record(rec)
            if "PosX" in d:
                # Position should be finite
                px = float(d["PosX"])
                self.assertTrue(-1e6 < px < 1e6,
                                f"PosX={px} out of range")
                return


class TestLAND(unittest.TestCase):
    @skip_if_no_esm
    def test_land_count(self):
        recs = get_by_type("LAND")
        self.assertGreater(len(recs), 30000)

    @skip_if_no_esm
    def test_land_layers(self):
        """Some LAND records should have layers."""
        recs = get_by_type("LAND")
        for rec in recs[:200]:
            d = export_record(rec)
            if "LayerCount" in d:
                count = int(d["LayerCount"])
                self.assertGreater(count, 0)
                self.assertIn("Layer[0].Type", d)
                return


class TestWRLD(unittest.TestCase):
    @skip_if_no_esm
    def test_worldspaces(self):
        recs = get_by_type("WRLD")
        self.assertGreater(len(recs), 5)
        # Tamriel should be there
        found = False
        for rec in recs:
            d = export_record(rec)
            if d.get("EditorID") == "Tamriel":
                found = True
                self.assertIn("DATA.Flags", d)
                break
        self.assertTrue(found, "Tamriel worldspace not found")


class TestDIAL(unittest.TestCase):
    @skip_if_no_esm
    def test_dialog_count(self):
        recs = get_by_type("DIAL")
        self.assertGreater(len(recs), 3000)

    @skip_if_no_esm
    def test_dialog_type(self):
        recs = get_by_type("DIAL")
        for rec in recs[:20]:
            d = export_record(rec)
            if "DATA.Type" in d:
                dtype = int(d["DATA.Type"])
                self.assertGreaterEqual(dtype, 0)
                self.assertLessEqual(dtype, 4)
                return


class TestINFO(unittest.TestCase):
    @skip_if_no_esm
    def test_info_count(self):
        recs = get_by_type("INFO")
        self.assertGreater(len(recs), 15000)

    @skip_if_no_esm
    def test_info_has_parent_dial(self):
        """INFO records should have ParentDIAL set."""
        recs = get_by_type("INFO")
        count_with_dial = 0
        for rec in recs[:100]:
            if rec.parent_dial:
                count_with_dial += 1
        self.assertGreater(count_with_dial, 50)


class TestFACT(unittest.TestCase):
    @skip_if_no_esm
    def test_faction_fields(self):
        recs = get_by_type("FACT")
        self.assertGreater(len(recs), 100)
        d = export_record(recs[0])
        self.assertEqual(d["Signature"], "FACT")


class TestGLOB(unittest.TestCase):
    @skip_if_no_esm
    def test_global_fields(self):
        recs = get_by_type("GLOB")
        for rec in recs[:10]:
            d = export_record(rec)
            self.assertEqual(d["Signature"], "GLOB")
            self.assertIn("FNAM.Type", d)
            self.assertIn("FLTV.Value", d)
            return


class TestGMST(unittest.TestCase):
    @skip_if_no_esm
    def test_gamesetting_count(self):
        recs = get_by_type("GMST")
        self.assertGreater(len(recs), 300)


class TestRACE(unittest.TestCase):
    @skip_if_no_esm
    def test_race_data(self):
        recs = get_by_type("RACE")
        self.assertGreater(len(recs), 10)
        for rec in recs:
            d = export_record(rec)
            if d.get("EditorID") in ("Imperial", "Nord", "Breton"):
                self.assertIn("DATA.MaleHeight", d)
                self.assertIn("DATA.Flags", d)
                return


class TestLTEX(unittest.TestCase):
    @skip_if_no_esm
    def test_ltex_fields(self):
        recs = get_by_type("LTEX")
        self.assertGreater(len(recs), 100)
        for rec in recs[:10]:
            d = export_record(rec)
            if "ICON" in d:
                self.assertIn("HNAM.Material", d)
                return


class TestSOUN(unittest.TestCase):
    @skip_if_no_esm
    def test_sound_fields(self):
        recs = get_by_type("SOUN")
        self.assertGreater(len(recs), 500)
        for rec in recs[:10]:
            d = export_record(rec)
            if "FNAM.Filename" in d:
                return
        self.skipTest("No SOUN with filename found")


class TestQUST(unittest.TestCase):
    @skip_if_no_esm
    def test_quest_fields(self):
        recs = get_by_type("QUST")
        self.assertGreater(len(recs), 100)
        d = export_record(recs[0])
        self.assertEqual(d["Signature"], "QUST")


class TestBOOK(unittest.TestCase):
    @skip_if_no_esm
    def test_book_data(self):
        recs = get_by_type("BOOK")
        self.assertGreater(len(recs), 500)
        for rec in recs[:20]:
            d = export_record(rec)
            if "DATA.Value" in d:
                val = int(d["DATA.Value"])
                self.assertGreaterEqual(val, 0)
                return


class TestINGR(unittest.TestCase):
    @skip_if_no_esm
    def test_ingredient_effects(self):
        recs = get_by_type("INGR")
        self.assertGreater(len(recs), 100)
        for rec in recs[:20]:
            d = export_record(rec)
            if "EffectCount" in d:
                count = int(d["EffectCount"])
                self.assertGreater(count, 0)
                return


class TestLVLI(unittest.TestCase):
    @skip_if_no_esm
    def test_leveled_item_entries(self):
        recs = get_by_type("LVLI")
        self.assertGreater(len(recs), 1000)
        for rec in recs[:10]:
            d = export_record(rec)
            if "EntryCount" in d:
                count = int(d["EntryCount"])
                if count > 0:
                    self.assertIn("Entry[0].FormID", d)
                    self.assertIn("Entry[0].Level", d)
                    return


class TestSCPT(unittest.TestCase):
    @skip_if_no_esm
    def test_script_source(self):
        recs = get_by_type("SCPT")
        self.assertGreater(len(recs), 1000)
        for rec in recs[:20]:
            d = export_record(rec)
            if "SCTX" in d:
                # Should have some script text
                self.assertGreater(len(d["SCTX"]), 0)
                return


class TestCLAS(unittest.TestCase):
    @skip_if_no_esm
    def test_class_data(self):
        recs = get_by_type("CLAS")
        self.assertGreater(len(recs), 50)
        for rec in recs[:10]:
            d = export_record(rec)
            if "DATA.Specialization" in d:
                spec = int(d["DATA.Specialization"])
                self.assertIn(spec, [0, 1, 2])  # Combat, Magic, Stealth
                return


class TestACHR(unittest.TestCase):
    @skip_if_no_esm
    def test_placed_npc(self):
        recs = get_by_type("ACHR")
        self.assertGreater(len(recs), 1000)
        for rec in recs[:20]:
            d = export_record(rec)
            if "NAME" in d:
                self.assertIn("PosX", d)
                return


class TestACRE(unittest.TestCase):
    @skip_if_no_esm
    def test_placed_creature(self):
        recs = get_by_type("ACRE")
        self.assertGreater(len(recs), 1000)


if __name__ == "__main__":
    # Allow running standalone
    unittest.main(verbosity=2)

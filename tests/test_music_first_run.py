"""Music records must survive a plugin's FIRST conversion.

The stage order is import (phase 6) then sounds (phase 7), but the music
manifest is written by the SOUND stage -- so on a first run the importer found
no `music_tracks.json`, silently registered nothing, and shipped an ESM with
zero MUST/MUSC and no CELL.XCMO / WRLD.ZNAM while the .xwm files themselves
converted fine.  Music was simply absent in game until the plugin happened to
be converted a second time.

`load_music_manifest` now scans the extracted music folder itself, so the
records no longer depend on stage order.
"""

import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert.audio.music_convert import track_entry
from tes5_import.record_types.music import (
    build_MUST, build_music_records, is_silent_stem, load_music_manifest)

from pathlib import Path


class _Writer:
    """Minimal stand-in: build_music_records only needs derive_formid."""

    def derive_formid(self, site, key):
        return (abs(hash((site, key))) & 0xFFFFFF) | 0x01000000


def _make_export(root, plugin, rels):
    """A fake extracted music tree: export/<plugin>/music/<rel>."""
    music = Path(root) / plugin / 'music'
    for rel in rels:
        p = music / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'\0' * 16)
    return music


class TestMusicFirstRun(unittest.TestCase):

    RELS = ['Explore/atmo_01.mp3', 'Explore/atmo_02.mp3',
            'Dungeon/dun_01.mp3', 'Public/town_01.mp3',
            'Battle/fight_01.mp3', 'Special/Silence.mp3']

    def test_manifest_absent_still_builds_records(self):
        """The bug: no manifest yet -> zero music records."""
        with tempfile.TemporaryDirectory() as tmp:
            exp = os.path.join(tmp, 'export')
            out = os.path.join(tmp, 'output')
            _make_export(exp, 'Test.esm', self.RELS)
            pdir = os.path.join(out, 'Test.esm')
            os.makedirs(pdir)

            self.assertFalse(
                os.path.isfile(os.path.join(pdir, 'music_tracks.json')),
                'precondition: first run has no manifest')

            m = load_music_manifest(pdir, export_dir=exp, plugin='Test.esm')
            self.assertEqual(len(m.get('tracks') or []), len(self.RELS))

            rec = build_music_records(m, _Writer(), 'Test.esm')
            self.assertEqual(len(rec['must']), len(self.RELS))
            self.assertTrue(rec['musc'], 'no MUSC built')
            # by_enum drives CELL.XCMO / WRLD.ZNAM; empty = silent world.
            self.assertTrue(rec['by_enum'], 'no enum->MUSC table registered')

    def test_scan_writes_a_readable_manifest(self):
        """The scan must leave a manifest the normal reader accepts."""
        with tempfile.TemporaryDirectory() as tmp:
            exp = os.path.join(tmp, 'export')
            out = os.path.join(tmp, 'output')
            _make_export(exp, 'Test.esm', self.RELS)
            pdir = os.path.join(out, 'Test.esm')
            os.makedirs(pdir)

            load_music_manifest(pdir, export_dir=exp, plugin='Test.esm')
            self.assertTrue(
                os.path.isfile(os.path.join(pdir, 'music_tracks.json')))
            # Second call reads the file rather than rescanning.
            again = load_music_manifest(pdir, export_dir=exp,
                                        plugin='Test.esm')
            self.assertEqual(len(again['tracks']), len(self.RELS))

    def test_no_music_folder_is_not_an_error(self):
        """A plugin with masters ships no music; that is the normal state."""
        with tempfile.TemporaryDirectory() as tmp:
            exp = os.path.join(tmp, 'export')
            pdir = os.path.join(tmp, 'output', 'Dep.esp')
            os.makedirs(os.path.join(exp, 'Dep.esp'))
            os.makedirs(pdir)
            self.assertEqual(
                load_music_manifest(pdir, export_dir=exp, plugin='Dep.esp'),
                {})


class TestMustFltv(unittest.TestCase):
    """A MUST carries exactly the subrecords its track TYPE takes.

    Censused over all 258 vanilla MUST in references/Skyrim.esm/MUST.txt:

        Single (240)  EDID CNAM ANAM   -- FLTV 0/240, DNAM 0/240
        Silent   (5)  EDID CNAM FLTV   -- DNAM 0/5
        Palette (13)  EDID CNAM FLTV DNAM

    DNAM is the PALETTE fade-out, not a per-track one.  Emitting it on a
    single track (with FLTV dropped) makes a combination that occurs in NO
    vanilla record, and the exterior worldspace goes silent.
    """

    def _build(self, stem):
        rel = Path('Special') / (stem + '.mp3')
        t = track_entry(rel, 'Test.esm', duration=42.0)
        return build_MUST(t, 0x01000001, 'Test.esm')

    def _shape(self, blob):
        i, out = 24, []
        while i + 6 <= len(blob):
            sig = blob[i:i + 4].decode('latin1')
            size = struct.unpack('<H', blob[i + 4:i + 6])[0]
            out.append(sig)
            i += 6 + size
        return out

    def test_single_track_is_exactly_edid_cnam_anam(self):
        """The 203-of-240 vanilla shape.  DNAM here silences the track."""
        self.assertEqual(self._shape(self._build('atmo_01')),
                         ['EDID', 'CNAM', 'ANAM'])

    def test_silent_track_is_exactly_edid_cnam_fltv(self):
        self.assertEqual(self._shape(self._build('Silence')),
                         ['EDID', 'CNAM', 'FLTV'])

    def test_no_track_carries_dnam(self):
        """DNAM belongs to Palette tracks only, which we never author."""
        for stem in ('atmo_01', 'Silence'):
            self.assertNotIn(b'DNAM', self._build(stem), stem)

    def test_single_track_has_anam_and_no_fltv(self):
        blob = self._build('atmo_01')
        self.assertIn(b'ANAM', blob)
        self.assertNotIn(b'FLTV', blob)

    def test_silent_track_has_fltv_and_no_anam(self):
        self.assertTrue(is_silent_stem('Silence'))
        blob = self._build('Silence')
        self.assertIn(b'FLTV', blob)
        self.assertNotIn(b'ANAM', blob)

    def test_silent_fltv_carries_the_duration(self):
        blob = self._build('Silence')
        i = blob.index(b'FLTV')
        size = struct.unpack('<H', blob[i + 4:i + 6])[0]
        self.assertEqual(size, 4)
        self.assertAlmostEqual(
            struct.unpack('<f', blob[i + 6:i + 10])[0], 42.0, places=3)


if __name__ == '__main__':
    unittest.main()


class TestRegionMusic(unittest.TestCase):
    """Exterior music reaches the player through REGN.RDMO, not WRLD.ZNAM.

    Vanilla Tamriel's ZNAM points at `_NONE`, whose single track is
    `_MUSExploreSILENT30` -- a SILENT 30-second track.  Every real explore
    type (MUSExploreTundra, MUSExploreMountain, ...) is delivered by a
    region: 28 vanilla REGN carry RDMO, referencing 5 distinct MUSC.

    TES4 authors the same thing as RDMD, the 3-value music enum, on 127 of
    Oblivion's 211 regions and 60 of Nehrim's 78.  Dropping it left the
    countryside silent while cities (authored XCMT=1 cells) still played.
    """

    def test_export_emits_region_music_type(self):
        """tes4_export must carry RDMD through as RegionData[i].MusicType."""
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, 'export', 'Oblivion.esm', 'REGN.txt')
        if not os.path.isfile(path):
            self.skipTest('Oblivion.esm not exported in this tree')
        with open(path, encoding='utf-8', errors='replace') as fh:
            text = fh.read()
        self.assertTrue(re.search(r'RegionData\[\d+\]\.MusicType=\d', text),
                        'no region MusicType exported - RDMD was dropped')

    def test_convert_regn_emits_rdmo(self):
        from tes5_import.record_types import common, region

        rec = {
            'FormID': '01000001', 'RecordFlags': 0, 'EditorID': 'TestRegion',
            'RCLR.R': 1, 'RCLR.G': 2, 'RCLR.B': 3,
            'AreaCount': 1, 'Area[0].EdgeFalloff': 0,
            'Area[0].PointsHex': '00000000' * 4,
            'RegionDataCount': 1,
            'RegionData[0].Type': 7, 'RegionData[0].Override': 0,
            'RegionData[0].Priority': 50, 'RegionData[0].MusicType': 0,
        }
        common.register_music_types({0: 0x01ABCDEF})
        try:
            blob = region.convert_REGN(rec)
        finally:
            common.register_music_types({})
        self.assertIsNotNone(blob, 'music-only region was dropped entirely')
        self.assertIn(b'RDMO', blob)
        self.assertIn(b'RDAT', blob)

    def test_region_with_music_but_no_weather_survives(self):
        """A sound-only region used to be dropped (weather list required)."""
        from tes5_import.record_types import common, region

        rec = {
            'FormID': '01000002', 'RecordFlags': 0, 'EditorID': 'SoundOnly',
            'RCLR.R': 0, 'RCLR.G': 0, 'RCLR.B': 0,
            'AreaCount': 1, 'Area[0].EdgeFalloff': 0,
            'Area[0].PointsHex': '00000000' * 4,
            'RegionDataCount': 1,
            'RegionData[0].Type': 7, 'RegionData[0].Override': 0,
            'RegionData[0].Priority': 50, 'RegionData[0].MusicType': 0,
        }
        common.register_music_types({0: 0x01ABCDEF})
        try:
            self.assertIsNotNone(region.convert_REGN(rec))
        finally:
            common.register_music_types({})


class TestCityMusicPrecedence(unittest.TestCase):
    """A DEFAULT-valued RDMD must not override an authored worldspace SNAM.

    Oblivion's cities are their own worldspaces with SNAM=1 (Public), but each
    also has a weather region whose RDMD is the CS's unset default (0).  126 of
    Oblivion's 127 RDMD values are 0 -- only WaitingRoomRegion authors anything
    else -- so RDMD=0 is "not set", not "use Explore".  Honouring it pinned all
    21 city worldspaces to Explore and the track never changed on entering.
    """

    BASE = {
        'FormID': '01000001', 'RecordFlags': 0, 'EditorID': 'R',
        'RCLR.R': 0, 'RCLR.G': 0, 'RCLR.B': 0,
        'AreaCount': 1, 'Area[0].EdgeFalloff': 0,
        'Area[0].PointsHex': '00000000' * 4,
        'RegionDataCount': 1, 'RegionData[0].Type': 7,
        'RegionData[0].Override': 0, 'RegionData[0].Priority': 50,
    }
    WORLD = 0x0001C318

    def _emits_rdmo(self, rdmd, world_snam):
        from tes5_import.record_types import common, region
        rec = dict(self.BASE)
        rec['RegionData[0].MusicType'] = rdmd
        rec['WNAM.Worldspace'] = '%08X' % self.WORLD
        common.register_music_types({0: 0x0AAA, 1: 0x0BBB, 2: 0x0CCC})
        common.register_world_music(
            {self.WORLD: world_snam} if world_snam is not None else {})
        try:
            blob = region.convert_REGN(rec)
        finally:
            common.register_music_types({})
            common.register_world_music({})
        return blob is not None and b'RDMO' in blob

    def test_default_rdmd_yields_to_public_worldspace(self):
        """The city case: region says 0, world says Public -> world wins."""
        self.assertFalse(self._emits_rdmo(0, 1))

    def test_default_rdmd_applies_when_world_is_unauthored(self):
        """The countryside case: nothing else supplies music."""
        self.assertTrue(self._emits_rdmo(0, None))

    def test_authored_rdmd_still_wins(self):
        """A region that names a NON-default type keeps its say."""
        self.assertTrue(self._emits_rdmo(2, 1))

    def test_default_rdmd_applies_when_world_also_default(self):
        self.assertTrue(self._emits_rdmo(0, 0))


class TestCombatCuePoints(unittest.TestCase):
    """Combat tracks need FNAM cue points or the MUSC never engages.

    All 10 vanilla MUST carrying FNAM are combat tracks; no ambient track has
    them.  Per the CK wiki (Music Track, "Choose Finale") the engine
    crossfades "from one of the Cue Points" when combat ends early -- so the
    cues are how a combat track both enters and leaves.  Without them our
    Battle MUSC was selected, failed to play, and displaced Skyrim's own
    MUSCombat (both are found by scanning for the combat flags, not by any
    FormID reference).

    Measured vanilla shape: 12-20 cues, gaps 3.74-8.00 s, evenly spaced with
    the first cue about one gap in.
    """

    def _cues(self, duration):
        from tes5_import.record_types.music import combat_cue_points
        return combat_cue_points(duration)

    def test_cue_count_and_gap_match_vanilla_ranges(self):
        for dur in (61.7, 68.4, 75.8, 128.1):
            cues = self._cues(dur)
            self.assertGreaterEqual(len(cues), 12, dur)
            self.assertLessEqual(len(cues), 20, dur)
            gap = (cues[-1] - cues[0]) / (len(cues) - 1)
            self.assertGreater(gap, 3.7, dur)
            self.assertLess(gap, 8.1, dur)

    def test_first_cue_is_one_gap_in(self):
        cues = self._cues(75.8)
        gap = (cues[-1] - cues[0]) / (len(cues) - 1)
        self.assertAlmostEqual(cues[0], gap, delta=gap * 0.1)

    def test_cues_stay_inside_the_track(self):
        for dur in (61.7, 128.1):
            self.assertLess(self._cues(dur)[-1], dur)

    def test_unusable_duration_yields_no_cues(self):
        for dur in (0, None, 5.0):
            self.assertEqual(self._cues(dur or 0), [])

    def test_only_battle_tracks_get_fnam(self):
        from asset_convert.audio.music_convert import track_entry
        from tes5_import.record_types.music import build_MUST

        for cat, expect in (('Battle', True), ('Explore', False),
                            ('Public', False), ('Dungeon', False)):
            rel = Path(cat) / 'track_01.mp3'
            entry = track_entry(rel, 'Test.esm', duration=75.0)
            blob = build_MUST(entry, 0x01000001, 'Test.esm')
            self.assertEqual(b'FNAM' in blob, expect, cat)

    def test_battle_track_without_duration_omits_fnam(self):
        """FNAM is optional in xEdit - better absent than zero-length."""
        from asset_convert.audio.music_convert import track_entry
        from tes5_import.record_types.music import build_MUST

        entry = track_entry(Path('Battle') / 'x.mp3', 'Test.esm')
        self.assertNotIn(b'FNAM', build_MUST(entry, 0x01000001, 'Test.esm'))


class TestBattleMusicReachability(unittest.TestCase):
    """Combat music is reached ONLY through DOBJ's BTMS default object.

    The engine does not scan MUSC records for the combat flags -- it asks
    BGSDefaultObjectManager for kBattleMusic, whose form comes from DOBJ's
    BTMS entry (hardcoded in Skyrim.esm to MUSCombat, 0003418E).  Confirmed
    against SeaSparrowOG/CombatMusic, whose hooks all gate on exactly that:

        const auto MUSCombat = defaultObjects->GetObject<RE::BGSMusicType>(
            RE::BGSDefaultObjectManager::DefaultObject::kBattleMusic);
        if (!MUSCombat || a_music != MUSCombat) { return a_music; }

    Battle is the one category with no CELL/WRLD/REGN route -- by_enum only
    carries the 3 TES4 enum values -- so without the DOBJ override our Battle
    MUSC is unreachable no matter how correctly it is built.
    """

    def test_build_music_records_exposes_battle_musc(self):
        from tes5_import.record_types.music import build_music_records
        from asset_convert.audio.music_convert import track_entry

        class _W:
            def derive_formid(self, site, key):
                return (abs(hash((site, key))) & 0xFFFFFF) | 0x01000000

        tracks = [track_entry(Path('Battle') / 'b_01.mp3', 'T.esm',
                               duration=70.0),
                  track_entry(Path('Explore') / 'e_01.mp3', 'T.esm')]
        out = build_music_records({'plugin': 'T.esm', 'tracks': tracks},
                                  _W(), 'T.esm')
        self.assertIsNotNone(out.get('battle'))
        self.assertNotIn(out['battle'], out['by_enum'].values(),
                         'battle must NOT be reachable via the TES4 enum')

    def test_no_battle_tracks_means_no_battle_musc(self):
        from tes5_import.record_types.music import build_music_records
        from asset_convert.audio.music_convert import track_entry

        class _W:
            def derive_formid(self, site, key):
                return (abs(hash((site, key))) & 0xFFFFFF) | 0x01000000

        out = build_music_records(
            {'plugin': 'T.esm',
             'tracks': [track_entry(Path('Explore') / 'e.mp3', 'T.esm')]},
            _W(), 'T.esm')
        self.assertIsNone(out.get('battle'))

    def test_dobj_override_repoints_only_btms(self):
        """Every other default object must survive untouched."""
        import struct as _s
        from tes5_import.base.equivalents import (
            _read_master_dobj, build_DOBJ_override)
        from asset_convert.sources.skyrim_assets import find_skyrim_data

        data = find_skyrim_data()
        esm = os.path.join(data, 'Skyrim.esm') if data else None
        if not esm or not os.path.isfile(esm):
            self.skipTest('Skyrim.esm not available')

        fid, entries = _read_master_dobj(esm)
        built = build_DOBJ_override({b'BTMS': 0x0ABCDEF0}, esm)
        self.assertIsNotNone(built)
        self.assertEqual(built[0], fid, 'must override the master FormID')

        body = built[1][24:]
        i, dnam = 0, None
        while i + 6 <= len(body):
            sig = body[i:i + 4]
            size = _s.unpack('<H', body[i + 4:i + 6])[0]
            if sig == b'DNAM':
                dnam = body[i + 6:i + 6 + size]
            i += 6 + size
        self.assertIsNotNone(dnam)
        got = [(dnam[j:j + 4], _s.unpack('<I', dnam[j + 4:j + 8])[0])
               for j in range(0, len(dnam), 8)]
        self.assertEqual(len(got), len(entries), 'entry count changed')
        for (tag_a, fid_a), (tag_b, fid_b) in zip(got, entries):
            self.assertEqual(tag_a, tag_b)
            if tag_a == b'BTMS':
                self.assertEqual(fid_a, 0x0ABCDEF0)
            else:
                self.assertEqual(fid_a, fid_b, tag_a)

    def test_dobj_override_binds_lockpick_slots(self):
        """LKPK/SKLK must name Skyrim's own forms, not our remapped MISC."""
        import struct as _s
        from tes5_import.base.equivalents import (
            _read_master_dobj, build_DOBJ_override, DOBJ_ITEM_TAGS)
        from asset_convert.sources.skyrim_assets import find_skyrim_data

        data = find_skyrim_data()
        esm = os.path.join(data, 'Skyrim.esm') if data else None
        if not esm or not os.path.isfile(esm):
            self.skipTest('Skyrim.esm not available')

        fid, entries = _read_master_dobj(esm)
        built = build_DOBJ_override(dict(DOBJ_ITEM_TAGS), esm)
        self.assertIsNotNone(built)

        body = built[1][24:]
        i, dnam = 0, None
        while i + 6 <= len(body):
            sig = body[i:i + 4]
            size = _s.unpack('<H', body[i + 6 - 2:i + 6])[0]
            if sig == b'DNAM':
                dnam = body[i + 6:i + 6 + size]
            i += 6 + size
        got = dict((dnam[j:j + 4], _s.unpack('<I', dnam[j + 4:j + 8])[0])
                   for j in range(0, len(dnam), 8))
        self.assertEqual(got[b'LKPK'], 0x0000000A)
        self.assertEqual(got[b'SKLK'], 0x0003A070)
        self.assertEqual(len(got), len(dict(entries)))

    def test_lockpick_references_substitute_to_vanilla(self):
        """A reference to the TES4 lockpick must land on Skyrim's form."""
        from tes5_import.base.text_reader import get_formid

        self.assertEqual(get_formid({'CNTO': '0000000A'}, 'CNTO'), 0x0000000A)
        self.assertEqual(get_formid({'CNTO': '0000000B'}, 'CNTO'), 0x0003A070)
        self.assertEqual(get_formid({'FormID': '0000000B'}, 'FormID') & 0xFFFFFF,
                         0x00000B, 'own id must skip the substitution')

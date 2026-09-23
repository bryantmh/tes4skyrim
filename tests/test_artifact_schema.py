"""Versioned pipeline artifacts (tes5_import/base/artifact_schema.py).

The guard test is `test_schema_matches_frozen_hash`: it fails when an
artifact's required-key set changes without a version bump.  That is the exact
slip that caused the original bug -- commit a4fdb47 added `behavior_hkx` to
creature_projects.json's contract, nothing bumped a version, and every plugin
reading a master's older file died with a bare KeyError.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tes5_import.base.artifact_schema import (
    ARTIFACTS, StaleArtifactError, read_artifact, write_artifact)


def _schema_hash(art):
    """Stable digest of the contract a version number stands for."""
    return hashlib.sha256(
        json.dumps(sorted(art.required)).encode()).hexdigest()[:16]


# (version, sha256(sorted(required))[:16]) per artifact.  BUMP THE VERSION and
# update the hash together -- never the hash alone, which would silently accept
# a contract change that breaks every file already on disk.
# Literal digests -- deriving them from ARTIFACTS would make this test vacuous.
FROZEN = {
    'creature_projects.json': (1, '1538a9ac96e2b58a'),
}


class TestSchemaGuard(unittest.TestCase):
    """(b) -- catch a required-field addition that forgot its version bump."""

    def test_every_artifact_is_frozen(self):
        self.assertEqual(sorted(ARTIFACTS), sorted(FROZEN),
                         'a new artifact must be added to FROZEN too')

    def test_schema_matches_frozen_hash(self):
        for name, (version, digest) in FROZEN.items():
            art = ARTIFACTS[name]
            self.assertEqual(
                art.version, version,
                f'{name}: version moved to {art.version}; update FROZEN')
            self.assertEqual(
                _schema_hash(art), digest,
                f'{name}: required keys changed without a version bump. '
                f'Bump Artifact.version and update FROZEN together, or every '
                f'{name} already on disk breaks with no way to detect it.')


class TestRoundTrip(unittest.TestCase):

    def _path(self, td, name='creature_projects.json'):
        return os.path.join(td, name)

    def _good(self):
        return {'boar': {'behavior_hkx': 'Actors\\TES4\\boar\\Behaviors\\b.hkx',
                         'body_dir': 'Actors\\TES4\\boar',
                         'skeleton_nif': 'actors\\boar\\skeleton.nif',
                         'project_hkx': 'Actors\\TES4\\boar\\p.hkx',
                         'bodies': ['boar.nif']}}

    def test_write_then_read_returns_the_data(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._path(td)
            write_artifact(p, 'Oblivion.esm', self._good())
            self.assertEqual(read_artifact(p), self._good())

    def test_envelope_carries_plugin_and_stage(self):
        """The two fields that make the error actionable."""
        with tempfile.TemporaryDirectory() as td:
            p = self._path(td)
            write_artifact(p, 'Oblivion.esm', self._good())
            with open(p, encoding='utf-8') as f:
                payload = json.load(f)
            self.assertEqual(payload['plugin'], 'Oblivion.esm')
            self.assertEqual(payload['stage'], '--creatures-only')
            self.assertEqual(payload['version'], 1)


class TestStaleDetection(unittest.TestCase):

    def _write_raw(self, td, payload, name='creature_projects.json'):
        p = os.path.join(td, name)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
        return p

    def test_v0_bare_dict_is_rejected(self):
        """Every file written before the envelope existed reads as v0."""
        with tempfile.TemporaryDirectory() as td:
            p = self._write_raw(td, {'boar': {'project_hkx': 'x'}})
            with self.assertRaises(StaleArtifactError) as cm:
                read_artifact(p, 'Oblivion.esm')
            self.assertIn('v0', str(cm.exception))

    def test_v0_error_names_the_hinted_plugin_and_command(self):
        """The reported bug: the stale file belongs to the MASTER."""
        with tempfile.TemporaryDirectory() as td:
            p = self._write_raw(td, {'boar': {'project_hkx': 'x'}})
            with self.assertRaises(StaleArtifactError) as cm:
                read_artifact(p, 'Oblivion.esm')
            msg = str(cm.exception)
            self.assertIn(
                'python convert.py -f Oblivion.esm --creatures-only', msg)

    def test_older_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._write_raw(td, {'version': 0, 'plugin': 'Nehrim.esm',
                                     'stage': '--creatures-only', 'data': {}})
            with self.assertRaises(StaleArtifactError):
                read_artifact(p)

    def test_missing_required_key_at_current_version_is_rejected(self):
        """(a) -- the backstop for a required field added without a bump.

        This is exactly the a4fdb47 shape: right version, missing field.
        """
        with tempfile.TemporaryDirectory() as td:
            entry = {'body_dir': 'd', 'skeleton_nif': 's',
                     'project_hkx': 'p', 'bodies': ['b.nif']}
            p = self._write_raw(td, {'version': 1, 'plugin': 'Oblivion.esm',
                                     'stage': '--creatures-only',
                                     'data': {'boar': entry}})
            with self.assertRaises(StaleArtifactError) as cm:
                read_artifact(p)
            self.assertIn('behavior_hkx', str(cm.exception))


class TestPreflight(unittest.TestCase):
    """Fail at the TOP of the import, not 15 phase-0 steps in.

    build_creature_races runs deep in phase 0 (after the master load, fid
    maps, cross-ref graph and script plans), so relying on the consumer alone
    made a stale file cost a minute of work before saying anything.
    """

    def _plugin(self, td, name, master=None, projects=None):
        d = os.path.join(td, name)
        os.makedirs(d, exist_ok=True)
        if master:
            with open(os.path.join(d, '_HEADER.txt'), 'w',
                      encoding='utf-8') as f:
                f.write('Master[0]=%s' % master + chr(10))
        if projects is not None:
            with open(os.path.join(d, 'creature_projects.json'), 'w',
                      encoding='utf-8') as f:
                json.dump(projects, f)      # bare == v0
        return d

    def _patch_layout(self, td):
        import tes5_import.overrides.nested as ov
        self._saved = (ov.export_root, ov.master_export_dir)
        ov.export_root = lambda d: td
        ov.master_export_dir = lambda root, name: os.path.join(root, name)
        self.addCleanup(self._restore)

    def _restore(self):
        import tes5_import.overrides.nested as ov
        ov.export_root, ov.master_export_dir = self._saved

    def test_missing_artifacts_are_not_an_error(self):
        """Plenty of plugins ship no creatures and no music."""
        from tes5_import.base.artifact_schema import preflight_artifacts
        with tempfile.TemporaryDirectory() as td:
            self._patch_layout(td)
            d = self._plugin(td, 'Bare.esp')
            preflight_artifacts(d)      # must not raise

    def test_own_stale_artifact_names_this_plugin(self):
        from tes5_import.base.artifact_schema import preflight_artifacts
        with tempfile.TemporaryDirectory() as td:
            self._patch_layout(td)
            d = self._plugin(td, 'Nehrim.esm',
                             projects={'rat': {'project_hkx': 'x'}})
            with self.assertRaises(StaleArtifactError) as cm:
                preflight_artifacts(d)
            self.assertIn('-f Nehrim.esm --creatures-only', str(cm.exception))

    def test_stale_master_artifact_names_the_MASTER(self):
        """The reported bug: DLCFrostcrag died over Oblivion.esm's file."""
        from tes5_import.base.artifact_schema import preflight_artifacts
        with tempfile.TemporaryDirectory() as td:
            self._patch_layout(td)
            self._plugin(td, 'Oblivion.esm',
                         projects={'rat': {'project_hkx': 'x'}})
            child = self._plugin(td, 'DLCFrostcrag.esp', master='Oblivion.esm')
            with self.assertRaises(StaleArtifactError) as cm:
                preflight_artifacts(child)
            msg = str(cm.exception)
            self.assertIn('-f Oblivion.esm --creatures-only', msg)
            self.assertNotIn('-f DLCFrostcrag.esp', msg)


if __name__ == '__main__':
    unittest.main()

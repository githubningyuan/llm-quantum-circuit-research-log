"""Offline tests for v0.4.0 DB initialization regression and fail-closed guard."""
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from openevolve_seed_adapter import load_database_with_verified_stats

class BuggyDB:
    def __init__(self, cfg):
        self.calls = 0
        self.feature_stats = {}
        self.load(cfg.db_path)        # simulates v0.4.0 constructor
        self.feature_stats = {}      # buggy late reset

    def load(self, path):
        self.calls += 1
        self.feature_stats = json.loads((Path(path) / 'metadata.json').read_text()).get('feature_stats', {})

    def _serialize_feature_stats(self):
        return self.feature_stats

class CannotRestoreDB(BuggyDB):
    def load(self, path):
        self.calls += 1
        self.feature_stats = {}  # never correctly restores

class ResumeGuardTests(unittest.TestCase):
    def make_cfg(self, root: Path, stats):
        (root / 'metadata.json').write_text(json.dumps({'feature_stats': stats}))
        return SimpleNamespace(db_path=str(root))

    def test_buggy_constructor_recovers(self):
        with tempfile.TemporaryDirectory() as t:
            expected = {'toffoli': {'min': 2000.0, 'max': 6400.0},
                        'qubits': {'min': 220.0, 'max': 260.0}}
            db = load_database_with_verified_stats(self.make_cfg(Path(t), expected), BuggyDB)
            self.assertEqual(db.calls, 2)
            self.assertEqual(db._serialize_feature_stats(), expected)

    def test_cannot_restore_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = self.make_cfg(Path(t), {'toffoli': {'min': 2500}})
            with self.assertRaisesRegex(RuntimeError, 'still inconsistent'):
                load_database_with_verified_stats(cfg, CannotRestoreDB)

    def test_empty_stats_safe(self):
        with tempfile.TemporaryDirectory() as t:
            db = load_database_with_verified_stats(self.make_cfg(Path(t), {}), BuggyDB)
            self.assertEqual(db.calls, 1)

    def test_invalid_metadata_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = self.make_cfg(Path(t), ['not-a-map'])
            with self.assertRaisesRegex(RuntimeError, 'malformed'):
                load_database_with_verified_stats(cfg, BuggyDB)

if __name__ == '__main__':
    unittest.main()

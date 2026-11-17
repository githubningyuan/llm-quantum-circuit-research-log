"""Offline tests: no installed OpenEvolve and no DeepSeek needed.
A stub API is used to TEST adapter contracts; not a real OpenEvolve integration test.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SRC = Path(__file__).with_name('openevolve_seed_adapter.py')
spec = importlib.util.spec_from_file_location('seed_adapter_under_test', SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
SOURCE = Path(__file__).parent / 'fixtures/opencode_evaluations'

class FakeConfig:
    def __init__(self, **kw):
        self.db_path = kw['db_path']
        self.num_islands = kw['num_islands']

class FakeProgram:
    def __init__(self, **kw):
        self.__dict__.update(kw)

class FakeDB:
    def __init__(self, config):
        self.path = Path(config.db_path)
        self.programs = {}
        self.disk = self.path / 'programs.json'
        if self.disk.exists():
            all_data = json.loads(self.disk.read_text())
            self.programs = {k: FakeProgram(**v) for k, v in all_data.items()}
    def add(self, p, iteration):
        self.programs[p.id] = p
        return p.id
    def save(self, path):
        Path(path, 'programs.json').write_text(json.dumps({k: vars(p) for k,p in self.programs.items()}))
    def get(self, id):
        return self.programs.get(id)
    def sample(self, num_inspirations):
        return sorted(self.programs.values(), key=lambda p: p.metrics['combined_score'])[-1], []

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = self.root / 'archives'
        self.archive.mkdir()
        # Synthetic source files; never execute them.
        for name, code, t, q in [('one','print(1)\n',4,7),('two','print(2)\n',3,7),('dup','print(1)\n',4,7)]:
            d = self.archive / name
            d.mkdir()
            blob = code.encode()
            (d / 'circuit.py').write_bytes(blob)
            (d / 'result.json').write_text(json.dumps({'valid':True, 'sha256': hashlib.sha256(blob).hexdigest(),
                'metrics':{'toffoli':t,'qubits':q,'tq_cost':t*q}}))
    def tearDown(self):
        self.temp.cleanup()
    def test_real_archives_readonly(self):
        if not SOURCE.exists():
            self.skipTest('Historical archive not mounted')
        seeds, report = mod.audit(SOURCE)
        self.assertEqual((report['snapshot_count'], report['unique_program_count'],report['best_tq']), (7,5,680430))
        self.assertEqual(report['best_sha256'],mod.EXPECTED_BEST_SHA)
        self.assertEqual(len(seeds),5)
        self.assertTrue(all(seeds[i].fitness >= seeds[i+1].fitness for i in range(4)))
    def test_deduplicate(self):
        seeds, report = mod.audit(self.archive)
        self.assertEqual((report['snapshot_count'],report['unique_program_count'],report['best_tq']), (3,2,21))
    def test_changed_snapshot_fails(self):
        (self.archive/'one'/'circuit.py').write_text('modified\n')
        with self.assertRaisesRegex(ValueError,'SHA mismatch'):
            mod.audit(self.archive)
    def test_invalid_or_inconsistent_metric_fails(self):
        p = self.archive/'two'/'result.json'
        data=json.loads(p.read_text())
        data['metrics']['tq_cost']=777
        p.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'inconsistent'):
            mod.audit(self.archive)
    def test_symlink_source_refused(self):
        source=self.archive/'one'/'circuit.py'
        source.unlink()
        source.symlink_to(self.archive/'two'/'circuit.py')
        with self.assertRaisesRegex(ValueError,'regular file'):
            mod.audit(self.archive)
    def test_prohibit_overwriting_db(self):
        with self.assertRaisesRegex(ValueError,'protected project/archive'):
            mod._db_guard(self.archive, self.archive)
        db=self.root/'db'
        db.mkdir()
        with self.assertRaisesRegex(ValueError,'exists'):
            mod._db_guard(db, self.archive)
    def test_import_and_sample_stub_only(self):
        seeds,_=mod.audit(self.archive)
        db_path=self.root/'newdb'
        with patch.object(mod,'_openevolve_api',return_value=(FakeConfig,FakeProgram,FakeDB)):
            created=mod.seed_database(seeds,db_path,self.archive)
            self.assertEqual(created['native_population_count'],2)
            self.assertEqual(created['best_sha256'],seeds[0].digest)
            sel=mod.sample_database(db_path)
            self.assertEqual(sel['parent_sha256'],seeds[0].digest)
        self.assertTrue((db_path/'programs.json').is_file())
        self.assertEqual(len(list(self.archive.iterdir())),3)
    def test_real_openevolve_not_installed_fails_cleanly(self):
        # If this environment gets OpenEvolve installed later, this test isn't applicable.
        try:
            import openevolve  # noqa
        except ImportError:
            seeds,_=mod.audit(self.archive)
            db=self.root/'newdb'
            with self.assertRaisesRegex(RuntimeError,'OpenEvolve is not installed'):
                mod.seed_database(seeds,db,self.archive)
            self.assertFalse(db.exists())
    def test_no_paid_or_shell_invocation(self):
        text=SRC.read_text()
        self.assertNotIn('subprocess',text)
        self.assertNotIn('DEEPSEEK_API_KEY',text)
        self.assertNotIn('Popen',text)

if __name__=='__main__':
    unittest.main(verbosity=2)

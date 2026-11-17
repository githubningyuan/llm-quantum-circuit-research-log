import json
from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from coulomb_selective_sidecar import (
    catalog_from_zip, choose_inspiration, build_overlay, valid_candidate,
    runtime_overlay, features, BEST_SHA
)
from build_memory_from_audit import make_memory
from create_trial_controller import patch_text
from clone_db_for_ab import regular_tree, snapshot

ROOT=Path(__file__).parent
ARCHIVE=ROOT/'coulomb_research_audit_20261101T092158Z.zip'
MINIMAL_CONTROLLER = """def dummy():
    for attempt in range(1):
        if attempt == 0:
            title = "quantum-evolve-" + folder.name
            if attempt == 0:
                cmd = [executable, "run", "--model", MODEL, "--agent", "build",
                           "--title", title, prompt(parent_tq)]
"""

class TestMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest,cls.cat=catalog_from_zip(ARCHIVE)
        cls.memory=make_memory(ARCHIVE)
        cls.best=next(c for c in cls.cat if c['id']==BEST_SHA)

    def test_provenance(self):
        self.assertEqual(len(self.cat),7)
        self.assertEqual(self.memory['data_scope']['sessions'],39)
        self.assertEqual(self.memory['summary']['distinct_returned_offspring'],50)
        self.assertEqual(self.memory['summary']['best_TQ'],348775)
        self.assertEqual(len(self.memory['notes']),5)

    def test_pair_and_shas(self):
        self.assertTrue(all(valid_candidate(p) for p in self.cat))
        other,detail=choose_inspiration(self.best,self.cat)
        self.assertIsNotNone(other)
        self.assertEqual(other['id'],'44cc82ac79972d7e8fd80f0b3f8c7af24dc0ce6cb261cca55d795a80682d38b2')
        self.assertEqual(detail['decision'],'inspiration')

    def test_no_secondary(self):
        other,detail=choose_inspiration(self.best,[self.best])
        self.assertIsNone(other)
        self.assertEqual(detail['decision'],'single_parent')

    def test_invalid_candidate_not_eligible(self):
        corrupt=dict(self.cat[0],id='0'*64)
        self.assertFalse(valid_candidate(corrupt))
        other,detail=choose_inspiration(self.best,[corrupt])
        self.assertIsNone(other)

    def test_no_code_executed(self):
        overlay,decision=build_overlay(self.best,self.cat,self.memory)
        self.assertIn('ONLY',overlay.upper())
        self.assertIn('Reference-only B code',overlay)
        self.assertLessEqual(len(overlay.encode()),22000)
        self.assertEqual(decision['decision'],'inspiration')

    def test_memory_schema_fails_closed(self):
        with self.assertRaises(ValueError):
            build_overlay(self.best,self.cat,{'schema':'other','notes':[]})

    def test_patch_preserves_control_call_branch(self):
        modified=patch_text(MINIMAL_CONTROLLER)
        self.assertIn('prompt(parent_tq) + trial_addendum',modified)
        self.assertIn('COULOMB_TRIAL_MODE',modified)
        self.assertIn('def dummy',modified)
        self.assertNotEqual(modified,MINIMAL_CONTROLLER)

    def test_patch_fails_changed_controller(self):
        with self.assertRaises(RuntimeError):
            patch_text('different source')

    def test_readonly_db_preview(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);home=d/'home';home.mkdir();src=d/'source';src.mkdir();(src/'test.db').write_bytes(b'untouched')
            dst=d/'ab';(d/'abparent').mkdir()
            with patch('clone_db_for_ab.assert_idle'):
                result=snapshot(home,src,d/'abparent'/'db-clones',False)
            self.assertEqual(result['mode'],'PREVIEW_0_API_NO_WRITES')
            self.assertFalse((d/'abparent'/'db-clones').exists())
            self.assertEqual((src/'test.db').read_bytes(),b'untouched')

if __name__=='__main__':unittest.main()

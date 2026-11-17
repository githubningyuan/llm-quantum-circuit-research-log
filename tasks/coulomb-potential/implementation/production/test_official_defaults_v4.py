"""Offline no-network/no-API contract tests for 0.4.0 official feature defaults."""
import json
import tempfile
from pathlib import Path
import unittest
from openevolve_seed_adapter import require_profile, DEFAULT_FEATURES, PROFILE_FILE

ROOT = Path(__file__).resolve().parent

class Tests(unittest.TestCase):
    def test_native_gate_uses_defaults(self):
        self.assertIn('feature_dimensions=["complexity", "diversity"]', (ROOT/'native_map_elites_gate.py').read_text())
    def test_adapter_seed_and_preview_use_defaults(self):
        self.assertEqual((ROOT/'openevolve_seed_adapter.py').read_text().count('feature_dimensions=["complexity", "diversity"]'), 2)
    def test_loop_uses_defaults(self):
        self.assertIn('feature_dimensions=["complexity", "diversity"]', (ROOT/'openevolve_opencode_loop.py').read_text())
    def test_saved_profile_guard(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            with self.assertRaisesRegex(RuntimeError, 'Unknown OpenEvolve DB'):
                require_profile(p)
            (p/PROFILE_FILE).write_text(json.dumps({'schema':1,'features':['toffoli','qubits'],'fitness':'1601460/tq_cost'}))
            with self.assertRaisesRegex(RuntimeError, 'differs'):
                require_profile(p)
            (p/PROFILE_FILE).write_text(json.dumps({'schema':1,'features':list(DEFAULT_FEATURES),'fitness':'1601460/tq_cost'}))
            require_profile(p)
    def test_no_bypass_of_paid_confirmation(self):
        src = (ROOT/'openevolve_opencode_loop.py').read_text()
        self.assertIn('--ack-paid', src)
        self.assertIn('orphaned_opencode_process', src)
        self.assertIn('terminate_group(proc)', src)
    def test_old_successful_prompt_retained(self):
        src = (ROOT/'openevolve_opencode_loop.py').read_text()
        self.assertIn('Make a substantive implementation attempt, then call quantum_evaluate', src)
        self.assertIn('You may inspect the source code and task references', src)
    def test_native_gate_checks_exact_reload(self):
        src = (ROOT/'native_map_elites_gate.py').read_text()
        self.assertIn('before == after', src)
        self.assertIn('"grids":',src)
        self.assertIn('"islands":',src)

if __name__ == '__main__':
    unittest.main()

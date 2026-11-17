"""No API, Docker, or OpenEvolve dependency; fail-closed contract checks."""
import ast
from pathlib import Path
import unittest
from unittest.mock import patch

import native_map_elites_gate as gate


class NativeGateOfflineTests(unittest.TestCase):
    def test_real_history_integrity(self):
        seeds, report = gate.audit(gate.FIXTURES)
        self.assertEqual(report["snapshot_count"], 7)
        self.assertEqual(len(seeds), 5)
        self.assertEqual(seeds[0].digest, gate.EXPECTED_BEST_SHA)
        self.assertEqual(seeds[0].tq, 680430)

    def test_refuses_missing_real_distribution(self):
        with patch("native_map_elites_gate.importlib.metadata.distribution", side_effect=gate.importlib.metadata.PackageNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "NATIVE gate NOT RUN"):
                gate.verify_actual_package()

    def test_refuses_wrong_installed_version(self):
        class D:
            version = "0.0.0"
        with patch("native_map_elites_gate.importlib.metadata.distribution", return_value=D()):
            with patch("native_map_elites_gate.importlib.util.find_spec"):
                with self.assertRaisesRegex(RuntimeError, "Expected pinned"):
                    gate.verify_actual_package()

    def test_no_hidden_execution_path(self):
        source = Path(gate.__file__).read_text()
        tree = ast.parse(source)
        imports = [n for n in ast.walk(tree) if isinstance(n, ast.Import)]
        modules = {alias.name for n in imports for alias in n.names}
        self.assertNotIn("subprocess", modules)
        self.assertNotIn("requests", modules)
        self.assertNotIn("openai", modules)

    def test_synthetic_child_explicitly_unvalidated(self):
        source = Path(gate.__file__).read_text()
        self.assertIn('"never_independently_evaluated": True', source)
        self.assertIn("TemporaryDirectory", source)


if __name__ == "__main__":
    unittest.main()

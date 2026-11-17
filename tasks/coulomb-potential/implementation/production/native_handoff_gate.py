#!/usr/bin/env python3
"""Offline end-to-end parent→mock OpenCode→mock grade→native MAP-Elites gate.

Only a disposable TemporaryDirectory is written. No real OpenCode, Docker,
DeepSeek or original worktrees are invoked. Synthetic fitness is *not* evidence
that any real circuit works. The installed OpenEvolve 0.4.0 DB is REAL.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from unittest.mock import patch

import native_map_elites_gate as native
import openevolve_opencode_loop as loop
from openevolve_seed_adapter import audit, seed_database, load_database_with_verified_stats, DEFAULT_FEATURES

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'opencode_evaluations'


def require(condition, message):
    if not condition:
        raise RuntimeError('NATIVE HANDOFF FAILED: ' + message)


def new_project(temp_root: Path) -> tuple[loop.Paths, bytes, int]:
    home = temp_root / 'simulated-home'
    lab = home / 'opencode-lab'
    trial = lab / 'quantum-trial'
    trial.mkdir(parents=True)
    agent = home / 'quantum-agent'
    (agent / '.venv/bin').mkdir(parents=True)
    (agent / '.venv/bin/python').write_text('# Mock only; NEVER executed\n')
    (agent / 'opencode_eval_bridge.py').write_text('# Mock only; NEVER executed\n')
    archive = agent / 'runs' / 'opencode_evaluations'
    shutil.copytree(FIXTURES, archive)
    cfg = {'permission': {
        '*': 'deny', 'read': 'allow', 'glob': 'allow', 'grep': 'allow',
        'edit': {'*': 'allow', 'opencode.json': 'deny', '.opencode/**': 'deny',
                 'quantum-trial/reference/**': 'deny'},
        'external_directory': 'deny', 'bash': 'deny',
        'quantum_evaluate': 'allow'}}
    (lab / 'opencode.json').write_text(json.dumps(cfg))
    seeds, _ = audit(FIXTURES)
    best = seeds[0].code.encode('utf-8')
    require(loop.digest(best) == loop.EXPECTED_BEST_SHA, 'Historical best hash mismatch')
    (trial / 'circuit.py').write_bytes(best)
    dbpath = temp_root / 'independent-native-db'
    p = loop.Paths(home=home, db=dbpath)
    return p, best, len(seeds)


def gate() -> dict:
    installed_version, module_path = native.verify_actual_package()
    with tempfile.TemporaryDirectory(prefix='oe-native-handoff-') as td:
        root = Path(td)
        p, original, expected_seeds = new_project(root)
        seeds, _ = audit(p.archive)
        result = seed_database(seeds, p.db, p.archive)
        require(result['best_sha256'] == loop.EXPECTED_BEST_SHA, 'Native DB lost original best')
        require(result['native_population_count'] == expected_seeds, 'Native seeded population wrong')

        seen = {}
        child_bytes = None

        def fake_runner(argv, cwd, env, timeout, logfile, capture_json=False):
            nonlocal child_bytes
            require(p.lab == cwd if not capture_json else p.home / 'quantum-agent' == cwd,
                    'Wrong subprocess working directory')
            if not capture_json:
                require('--model' in argv and loop.MODEL in argv, 'Wrong DeepSeek model input')
                require('--agent' in argv and 'build' in argv, 'Wrong OpenCode build agent')
                require(env.get('PWD') == str(p.lab), 'Wrong PWD provided to OpenCode')
                current = loop.regular_bytes(p.candidate)
                h = loop.digest(current)
                require(h in {s.digest for s in seeds}, 'OpenCode did not receive historical parent')
                parent = next(s for s in seeds if s.digest == h)
                require(f'T×Q = {parent.tq}' in argv[-1], 'Prompt has wrong parent cost')
                require('Make a substantive implementation attempt' in argv[-1], 'Successful Prompt missing')
                seen['parent_sha256'] = h
                seen['parent_tq'] = parent.tq
                child_bytes = current + b'\n# SYNTHETIC OFFSPRING - NOT VALIDATED\n'
                snap = p.archive / 'isolated-fake-validated-snapshot'
                snap.mkdir()
                (snap / 'circuit.py').write_bytes(child_bytes)
                (snap / 'result.json').write_text(json.dumps({
                    'valid': True,
                    'sha256': loop.digest(child_bytes),
                    'metrics': {'toffoli': 2500, 'qubits': 200, 'tq_cost': 500000}
                }))
                p.candidate.write_bytes(b'CORRUPT ENDING - NOT A CIRCUIT\n')
                return 0
            require(child_bytes is not None, 'Regrade ran before fake child created')
            require(loop.regular_bytes(p.candidate) == child_bytes,
                    'Regrade did not receive the saved intermediate candidate')
            seen['regrade_called'] = True
            return {'valid': True,
                    'sha256': loop.digest(child_bytes),
                    'metrics': {'toffoli': 2500, 'qubits': 200, 'tq_cost': 500000}}

        # Never launch any real child process. The real OpenEvolve DB remains
        # active, but all model/bridge execution is deliberately mocked.
        with patch.object(loop, 'orphaned_opencode_process', return_value=False), \
             patch.object(loop.shutil, 'which', return_value='/fake/opencode'), \
             patch.object(loop, 'run_command', side_effect=fake_runner), \
             patch.object(loop, 'inspect_opencode_session', return_value={'session_id':'ses_NATIVEMOCK', 'last_finish':'stop', 'tool_counts':{}, 'assistant_errors':[]}), \
             patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'OFFLINE-NO-NETWORK'}, clear=False):
            summary = loop.paid_step(p, timeout=120)

        require(summary.get('status') == 'completed', 'Loop did not complete')
        require(summary.get('restored_original') is True, 'Original worktree not restored')
        require(loop.regular_bytes(p.candidate) == original, 'Best circuit changed')
        require(not (p.runroot / 'active.json').exists(), 'Stale active marker')
        require(seen.get('regrade_called') is True, 'Independent regrade missing')
        imported = summary.get('returned_to_database', [])
        require(len(imported) == 1, 'Native DB did not receive exactly one child')
        require(imported[0]['sha256'] == loop.digest(child_bytes), 'Imported child hash mismatch')
        require(imported[0]['parent'] == seen['parent_sha256'], 'Parent ID not linked')

        from openevolve.config import DatabaseConfig
        from openevolve.database import ProgramDatabase
        cfg = DatabaseConfig(db_path=str(p.db), population_size=50, archive_size=50,
                             num_islands=1, feature_dimensions=list(DEFAULT_FEATURES),
                             feature_bins=16, random_seed=42)
        reloaded = load_database_with_verified_stats(cfg, ProgramDatabase)
        saved_child = reloaded.get(loop.digest(child_bytes))
        require(saved_child is not None, 'Native MAP-Elites DB lost synthetic child')
        require(saved_child.parent_id == seen['parent_sha256'], 'Persisted parent linkage wrong')
        require(saved_child.metadata.get('snapshot') == 'isolated-fake-validated-snapshot',
                'Saved snapshot provenance wrong')
        require(reloaded.get(loop.EXPECTED_BEST_SHA) is not None,
                'Original known best missing after reload')

        # Second isolated action: force an interrupt and validate restoration.
        def interrupted_runner(argv, cwd, env, timeout, logfile, capture_json=False):
            p.candidate.write_bytes(b'CORRUPT INTERRUPTED ENDING\n')
            raise KeyboardInterrupt('FAKE CTRL+C - NOT AN ACTUAL OPENCODE PROCESS')

        interrupted = False
        with patch.object(loop, 'orphaned_opencode_process', return_value=False), \
             patch.object(loop.shutil, 'which', return_value='/fake/opencode'), \
             patch.object(loop, 'run_command', side_effect=interrupted_runner), \
             patch.object(loop, 'inspect_opencode_session', return_value={'session_id':'ses_NATIVEMOCK2', 'last_finish':'stop', 'tool_counts':{}, 'assistant_errors':[]}), \
             patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'OFFLINE-NO-NETWORK'}, clear=False):
            try:
                loop.paid_step(p, timeout=120)
            except KeyboardInterrupt:
                interrupted = True
        require(interrupted, 'Simulated interrupt was not propagated')
        require(loop.regular_bytes(p.candidate) == original, 'Original not restored on interrupt')
        require(not (p.runroot / 'active.json').exists(), 'Stale active marker after interrupt')

        return {
            'status': 'NATIVE_HANDOFF_PASS',
            'openevolve_distribution_version': installed_version,
            'native_module': module_path,
            'map_elites_dimensions': list(DEFAULT_FEATURES),
            'verified_historical_seeds': expected_seeds,
            'selected_parent_sha256': seen['parent_sha256'],
            'selected_parent_tq': seen['parent_tq'],
            'model_handoff_checked': True,
            'saved_intermediate_child_imported_and_reloaded': True,
            'original_best_restored_after_both_runs': True,
            'interrupt_cleanup_logic_checked': True,
            'synthetic_metrics_are_not_quantum_validation': True,
            'deepseek_api_calls': 0,
            'real_opencode_calls': 0,
            'real_evaluator_calls': 0,
            'original_worktree_edits': 0,
            'writes': 'DISPOSABLE_TEMPORARY_DIRECTORY_ONLY',
        }

if __name__ == '__main__':
    try:
        print(json.dumps(gate(), ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({'status': 'NATIVE_HANDOFF_NOT_PASSED',
                          'error': type(e).__name__ + ': ' + str(e)}, ensure_ascii=False, indent=2))
        raise SystemExit(2)

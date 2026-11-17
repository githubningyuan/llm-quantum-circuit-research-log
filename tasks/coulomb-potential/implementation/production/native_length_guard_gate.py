#!/usr/bin/env python3
"""Native OpenEvolve 0.4.0 + mocked length continuation + recovery.

No real OpenCode or evaluator. ONLY disposable temporary directories.
A synthetic offspring is not a validated quantum circuit.
"""
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

import native_map_elites_gate as native
import native_handoff_gate as handoff
import openevolve_opencode_loop as loop
from openevolve_seed_adapter import audit, seed_database


def require(cond, msg):
    if not cond:
        raise RuntimeError('NATIVE LENGTH GUARD FAILED: ' + msg)


def main():
    version, module = native.verify_actual_package()
    with tempfile.TemporaryDirectory(prefix='qevo-v6-length-native-') as td:
        root = Path(td)
        p, original, n = handoff.new_project(root)
        # Regression: inspect the actual subprocess argv shape used by
        # OpenCode 1.19.4, rather than mocking away the session inspector.
        import subprocess
        checked_commands = []
        def fake_session_cli(argv, **kwargs):
            checked_commands.append(argv)
            if argv[1:3] == ['session', 'list']:
                return subprocess.CompletedProcess(argv, 0,
                    json.dumps([{'id':'ses_V6CLI','title':'quantum-evolve-cli-check',
                                 'directory':str(p.lab)}]), '')
            if argv[1:] == ['export', 'ses_V6CLI', '--sanitize']:
                return subprocess.CompletedProcess(argv, 0,
                    json.dumps({'messages':[{'info':{'role':'assistant','finish':'length'},
                                              'parts':[]}]}), '')
            raise RuntimeError('Wrong OpenCode CLI call: ' + repr(argv))
        with patch.object(loop.subprocess, 'run', side_effect=fake_session_cli):
            cli_check = loop.inspect_opencode_session('/mock/opencode', p.lab, {},
                                                      'quantum-evolve-cli-check')
        require(cli_check['last_finish'] == 'length', 'session finish parser error')
        require(len(checked_commands) == 2, 'unexpected extra CLI calls')
        require(checked_commands[1][1:] == ['export','ses_V6CLI','--sanitize'],
                'wrong export command for OpenCode 1.19.4')
        seeds, _ = audit(p.archive)
        seed_database(seeds, p.db, p.archive)
        child, calls = [None], []

        def runner(argv, cwd, env, timeout, logfile, capture_json=False):
            if capture_json:
                require(child[0] is not None, 'regrade before child')
                require(loop.regular_bytes(p.candidate) == child[0], 'wrong regrade input')
                return {'valid':True,'sha256':loop.digest(child[0]),
                        'metrics':{'toffoli':2500,'qubits':200,'tq_cost':500000}}
            calls.append(argv)
            if len(calls) == 1:
                require('--title' in argv and '--session' not in argv,
                        'initial title missing or unexpected session option')
                require(env.get('PWD') == str(p.lab), 'wrong working PWD')
            elif len(calls) == 2:
                require('--session' in argv and argv[argv.index('--session')+1]=='ses_V6GATE',
                        'did not continue EXACT saved session')
                require('Continue the unfinished' in argv[-1], 'follow-up is not substantive')
                base = loop.regular_bytes(p.candidate)
                child[0] = base + b'\n# SYNTHETIC, NOT A VALID CIRCUIT\n'
                snap=p.archive/'synthetic-two-turn-validated-snapshot'
                snap.mkdir()
                (snap/'circuit.py').write_bytes(child[0])
                (snap/'result.json').write_text(json.dumps({'valid':True,
                    'sha256':loop.digest(child[0]),
                    'metrics':{'toffoli':2500,'qubits':200,'tq_cost':500000}}))
            else:
                raise RuntimeError('Unexpected third paid turn!')
            return 0

        inspections=iter([
            {'session_id':'ses_V6GATE','last_finish':'length','tool_counts':{'read':17,'quantum_evaluate':1},'assistant_errors':[]},
            {'session_id':'ses_V6GATE','last_finish':'stop','tool_counts':{'read':17,'edit':1,'quantum_evaluate':2},'assistant_errors':[]},
        ])
        with patch.object(loop,'orphaned_opencode_process',return_value=False), \
             patch.object(loop.shutil,'which',return_value='/fake/opencode'), \
             patch.object(loop,'run_command',side_effect=runner), \
             patch.object(loop,'inspect_opencode_session',side_effect=lambda *args:next(inspections)), \
             patch.dict(os.environ, {'DEEPSEEK_API_KEY':'SYNTHETIC-NO-NETWORK'}):
            result=loop.paid_step(p,timeout=600,max_continuations=1)
        require(result['status']=='completed', 'continuation not completed')
        require(len(calls)==2, 'expected two SAME-session turns')
        require(result['opencode_session_id']=='ses_V6GATE', 'session missing from summary')
        require(len(result['returned_to_database'])==1, 'synthetic child was not imported')
        require(loop.regular_bytes(p.candidate)==original,'original best modified')
        require(result.get('restored_original') is True, 'restoration not verified')
        require(not (p.runroot/'active.json').exists(),'active run remains')
        _P, db=loop.database(p)
        require(db.get(loop.digest(child[0])) is not None, 'synthetic child not saved in real native DB')
        require(db.get(loop.EXPECTED_BEST_SHA) is not None,'best missing from real native DB')
        return {
            'status':'NATIVE_LENGTH_GUARD_PASS',
            'openevolve_version':version,'native_module':module,
            'model_turns':result['model_turns'],
            'continued_exact_session':result['opencode_session_id'],
            'opencode_1194_export_cli_contract': 'PASS',
            'synthetic_offspring_saved_and_reloaded':True,
            'original_best_restored':True,
            'simulated_result_not_quantum_validation':True,
            'real_opencode_calls':0,'real_deepseek_calls':0,'real_evaluator_calls':0,
            'writes':'DISPOSABLE_TEMPORARY_DIRECTORY_ONLY'
        }

if __name__=='__main__':
    try:
        print(json.dumps(main(),ensure_ascii=False,indent=2))
    except Exception as exc:
        print(json.dumps({'status':'NATIVE_LENGTH_GUARD_FAILED',
            'error':f'{type(exc).__name__}: {exc}'},ensure_ascii=False,indent=2))
        raise SystemExit(2)

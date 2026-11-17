import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import sys

import openevolve_opencode_loop as m
from openevolve_seed_adapter import audit, EXPECTED_BEST_SHA

REAL_ARCHIVE = Path(__file__).parent / 'fixtures/opencode_evaluations'
GOOD = b'# mock valid offspring\n'
BAD = b'# mock ungraded invalid final edits\n'


class FakeProgram:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeDB:
    def __init__(self, root, parent):
        self.config = SimpleNamespace(db_path=str(root))
        self.programs = {parent.id: parent}
        self.last_iteration = 0
        self.saved = 0

    def sample(self, num_inspirations=0):
        return list(self.programs.values())[0], []

    def get(self, h):
        return self.programs.get(h)

    def add(self, program, iteration=None):
        self.programs[program.id] = program
        self.last_iteration = iteration
        return program.id

    def save(self, path, iteration=0):
        self.saved += 1


class LoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seeds, report = audit(REAL_ARCHIVE)
        cls.best = seeds[0]
        cls.audit_report = report

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.dbpath = self.home / 'map-db'
        self.dbpath.mkdir()
        self.p = m.Paths(self.home, self.dbpath)
        self.p.candidate.parent.mkdir(parents=True)
        self.p.candidate.write_bytes(self.best.code.encode())
        self.original = self.p.candidate.read_bytes()
        (self.p.lab / 'opencode.json').write_text(json.dumps({
            'permission': {'read': 'allow', 'external_directory':'deny','bash':'deny',
                           'quantum_evaluate':'allow',
                           'edit': {'quantum-trial/reference/**':'deny'}}
        }))
        self.p.bridge.parent.mkdir(parents=True)
        self.p.bridge.write_text('#bridge')
        self.p.python.parent.mkdir(parents=True)
        self.p.python.write_text('#python')
        self.p.archive.mkdir(parents=True)
        parent = FakeProgram(id=EXPECTED_BEST_SHA, code=self.best.code,
                             metrics={'tq_cost':float(self.best.tq),
                                      'combined_score': m.BASELINE_TQ / self.best.tq})
        self.db = FakeDB(self.dbpath, parent)

    def _mock_command(self, argv, cwd, env, timeout, logfile, capture_json=False):
        if not capture_json:
            self.assertEqual(env['PWD'], str(self.p.lab))
            self.assertIn('deepseek/deepseek-v4-pro', argv)
            self.assertIn('Make a substantive implementation attempt', argv[-1])
            self.assertEqual(m.digest(self.p.candidate.read_bytes()), EXPECTED_BEST_SHA)
            snap = self.p.archive / 'test-valid-snapshot'
            snap.mkdir()
            (snap/'circuit.py').write_bytes(GOOD)
            (snap/'result.json').write_text(json.dumps({'valid': True,
                'sha256': m.digest(GOOD),
                'metrics':{'toffoli':3000,'qubits':200,'tq_cost':600000}}))
            self.p.candidate.write_bytes(BAD)
            return 0
        self.assertEqual(self.p.candidate.read_bytes(), GOOD)
        return {'valid': True, 'sha256': m.digest(GOOD),
                'metrics':{'toffoli':3000,'qubits':200,'tq_cost':600000}}

    def _live(self, fake):
        with patch.object(m, 'database', return_value=(FakeProgram,self.db)), \
             patch.object(m, 'orphaned_opencode_process', return_value=False), \
             patch.object(m.shutil, 'which', return_value='/usr/bin/fake-opencode'), \
             patch.object(m, 'run_command', side_effect=fake), \
             patch.object(m, 'inspect_opencode_session', return_value={
                 'session_id':'ses_OFFLINETEST', 'last_finish':'stop',
                 'tool_counts':{},'assistant_errors':[]}), \
             patch.dict(m.os.environ, {'DEEPSEEK_API_KEY':'TEST_ONLY_NO_NETWORK'}):
            return m.paid_step(self.p, timeout=120)

    def test_seed_audit_genuine(self):
        self.assertEqual(self.audit_report['snapshot_count'], 7)
        self.assertEqual(self.audit_report['unique_program_count'], 5)
        self.assertEqual(self.audit_report['best_sha256'], EXPECTED_BEST_SHA)

    def test_original_success_prompt(self):
        p = m.prompt(680430)
        self.assertIn('You may inspect the source code and task references', p)
        self.assertIn('If it fails, use the feedback to repair', p)
        self.assertIn('T×Q = 680430', p)
        self.assertNotIn('research the API before', p)

    def test_full_parent_llm_eval_db_cycle_restores_worktree(self):
        result = self._live(self._mock_command)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(len(result['returned_to_database']),1)
        self.assertEqual(result['returned_to_database'][0]['tq_cost'],600000)
        self.assertEqual(self.db.get(m.digest(GOOD)).parent_id, EXPECTED_BEST_SHA)
        self.assertEqual(self.db.saved,1)
        self.assertEqual(self.p.candidate.read_bytes(),self.original)
        self.assertFalse((self.p.runroot/'active.json').exists())

    def test_interrupt_restores_parent_and_active_state(self):
        def interrupted(*args, **kwargs):
            self.p.candidate.write_bytes(BAD)
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self._live(interrupted)
        self.assertEqual(self.p.candidate.read_bytes(),self.original)
        self.assertFalse((self.p.runroot/'active.json').exists())

    def test_cli_failure_restores_parent_no_import(self):
        def failure(*args,**kwargs):
            self.p.candidate.write_bytes(BAD)
            return 1
        result=self._live(failure)
        self.assertEqual(result['status'],'opencode_failed')
        self.assertEqual(self.p.candidate.read_bytes(),self.original)
        self.assertEqual(self.db.saved,0)

    def test_alternate_map_elites_parent_reaches_deepseek_input(self):
        seeds, _ = audit(REAL_ARCHIVE)
        alternate = seeds[1]
        alternate_prog = FakeProgram(id=alternate.digest, code=alternate.code,
            metrics={'tq_cost': float(alternate.tq),
                     'combined_score': m.BASELINE_TQ / alternate.tq})
        self.db.programs[alternate.digest] = alternate_prog
        self.db.sample = lambda num_inspirations=0: (alternate_prog, [])
        def fake_cmd(argv, cwd, env, timeout, logfile, capture_json=False):
            self.assertFalse(capture_json)
            self.assertEqual(m.digest(self.p.candidate.read_bytes()), alternate.digest)
            self.assertIn(f'T×Q = {alternate.tq}', argv[-1])
            self.assertIn('--model', argv)
            self.assertIn('deepseek/deepseek-v4-pro', argv)
            self.p.candidate.write_bytes(BAD)  # unvalidated final never imported
            return 0
        result = self._live(fake_cmd)
        self.assertEqual(result['parent_sha256'], alternate.digest)
        self.assertEqual(result['returned_to_database'], [])
        self.assertEqual(self.p.candidate.read_bytes(), self.original)

    def test_timeout_restores_original_and_marks_incomplete(self):
        def simulated_timeout(*args, **kwargs):
            self.p.candidate.write_bytes(BAD)
            raise TimeoutError('subprocess exceeded time budget')
        result = self._live(simulated_timeout)
        self.assertEqual(result['status'], 'model_timeout_no_new_offspring')
        self.assertEqual(result['successful_snapshots'], 0)
        self.assertEqual(result['model_turns'], 1)
        self.assertTrue(result['model_time_budget_exhausted'])
        self.assertTrue(result['restored_original'])
        self.assertEqual(self.p.candidate.read_bytes(), self.original)
        self.assertEqual(self.db.saved, 0)
        self.assertFalse((self.p.runroot/'active.json').exists())

    def test_timeout_keeps_valid_snapshot_and_regrades_before_import(self):
        calls = []
        def simulated_timeout(argv, cwd, env, timeout, logfile, capture_json=False):
            calls.append((list(argv), capture_json))
            if capture_json:
                self.assertEqual(self.p.candidate.read_bytes(), GOOD)
                return {'valid':True, 'sha256':m.digest(GOOD),
                        'metrics':{'toffoli':3000,'qubits':200,'tq_cost':600000}}
            snap=self.p.archive/'timed-out-valid-snapshot'
            snap.mkdir()
            (snap/'circuit.py').write_bytes(GOOD)
            (snap/'result.json').write_text(json.dumps({'valid':True,
                'sha256':m.digest(GOOD),
                'metrics':{'toffoli':3000,'qubits':200,'tq_cost':600000}}))
            self.p.candidate.write_bytes(BAD)
            raise TimeoutError('subprocess exceeded time budget')
        result=self._live(simulated_timeout)
        self.assertEqual(result['status'], 'model_timeout_with_verified_offspring')
        self.assertEqual(result['successful_snapshots'],1)
        self.assertEqual(result['model_turns'],1)
        self.assertEqual(len(result['returned_to_database']),1)
        self.assertEqual(result['returned_to_database'][0]['tq_cost'],600000)
        self.assertEqual(self.db.saved,1)
        self.assertEqual(len(calls),2)
        self.assertFalse(calls[0][1]); self.assertTrue(calls[1][1])
        self.assertEqual(self.p.candidate.read_bytes(),self.original)
        self.assertTrue(result['restored_original'])
        self.assertFalse((self.p.runroot/'active.json').exists())

    def test_timeout_with_invalid_snapshot_never_imports_or_keeps_bad_worktree(self):
        def simulated_timeout(*args, **kwargs):
            snap=self.p.archive/'timed-out-invalid-snapshot'
            snap.mkdir()
            (snap/'circuit.py').write_bytes(BAD)
            (snap/'result.json').write_text(json.dumps({'valid':False,'error':'incorrect circuit'}))
            self.p.candidate.write_bytes(BAD)
            raise TimeoutError('subprocess exceeded time budget')
        result=self._live(simulated_timeout)
        self.assertEqual(result['status'],'model_timeout_no_new_offspring')
        self.assertEqual(result['successful_snapshots'],0)
        self.assertEqual(result['returned_to_database'],[])
        self.assertEqual(self.p.candidate.read_bytes(),self.original)
        self.assertEqual(self.db.saved,0)

    def test_untrusted_candidate_sha_rejected(self):
        with self.assertRaisesRegex(ValueError,'SHA mismatch'):
            m.validated_metrics({'valid':True,'sha256':'wrong','metrics':{'toffoli':1,'qubits':1,'tq_cost':1}},b'content')

    def test_invalid_snapshot_metric_rejected(self):
        snap=self.p.archive/'fail'; snap.mkdir()
        (snap/'circuit.py').write_bytes(GOOD)
        (snap/'result.json').write_text(json.dumps({'valid':True,'sha256':m.digest(GOOD),
                                                    'metrics':{'toffoli':3000,'qubits':200,'tq_cost':600001}}))
        with self.assertRaisesRegex(ValueError,'inconsistency'):
            m.snapshot_codes(self.p.archive,set())

    def test_symlink_snapshot_rejected(self):
        snap=self.p.archive/'sym'
        snap.symlink_to(self.p.candidate.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'Unexpected archive'):
            m.snapshot_codes(self.p.archive, set())

    def test_security_guard_detects_permissions_change(self):
        cfg=self.p.lab/'opencode.json'
        data=json.loads(cfg.read_text()); data['permission']['bash']='allow'
        cfg.write_text(json.dumps(data))
        with self.assertRaisesRegex(RuntimeError,'permission'):
            m._assert_security(self.p)

    def test_other_active_run_prevents_staging(self):
        self.p.runroot.mkdir(parents=True)
        (self.p.runroot/'active.json').write_text('{}')
        def assert_no_command(*a,**k): raise AssertionError('Unexpected paid request')
        with self.assertRaisesRegex(RuntimeError,'Unresolved prior'):
            self._live(assert_no_command)
        self.assertEqual(self.p.candidate.read_bytes(),self.original)

    def test_no_api_without_explicit_opt_in(self):
        with patch.object(sys, 'argv', ['prog', '--db', str(self.dbpath), '--home',str(self.home), '--paid-step']), self.assertRaises(SystemExit):
            m.cli()
        self.assertEqual(self.p.candidate.read_bytes(),self.original)

    def test_recover_after_hard_termination(self):
        self.p.runroot.mkdir(parents=True)
        folder=self.p.runroot/'testhardkill'; folder.mkdir()
        (folder/'original-circuit.py').write_bytes(self.original)
        m.private_json(self.p.runroot/'active.json', {'folder':str(folder),'sha256':m.digest(self.original)})
        self.p.candidate.write_bytes(BAD)
        with patch.object(m,'orphaned_opencode_process',return_value=False):
            report=m.recover(self.p)
        self.assertEqual(report['status'],'restored')
        self.assertEqual(self.p.candidate.read_bytes(),self.original)
        self.assertFalse((self.p.runroot/'active.json').exists())

    def test_no_paid_with_other_opencode_active(self):
        def assert_no_command(*a,**k): raise AssertionError('Unexpected paid request')
        with patch.object(m,'database',return_value=(FakeProgram,self.db)), \
             patch.object(m,'orphaned_opencode_process',return_value=True), \
             patch.dict(m.os.environ, {'DEEPSEEK_API_KEY':'TEST_ONLY_NO_NETWORK'}):
            with self.assertRaisesRegex(RuntimeError,'OpenCode run still exists'):
                m.paid_step(self.p)
        self.assertEqual(self.p.candidate.read_bytes(),self.original)

    def test_length_resumes_exact_same_session_before_offspring_import(self):
        seen = []
        def runner(argv, cwd, env, timeout, logfile, capture_json=False):
            if capture_json:
                return {'valid': True, 'sha256': m.digest(GOOD),
                    'metrics':{'toffoli':3000,'qubits':200,'tq_cost':600000}}
            seen.append((list(argv), logfile.name))
            if len(seen) == 2:
                self.assertEqual(argv[argv.index('--session')+1], 'ses_OFFLINETEST')
                self.assertNotIn('--title',argv)
                self.assertIn('Continue the unfinished', argv[-1])
                snap = self.p.archive / 'continuation-result'
                snap.mkdir()
                (snap / 'circuit.py').write_bytes(GOOD)
                (snap / 'result.json').write_text(json.dumps({'valid':True,
                    'sha256':m.digest(GOOD),
                    'metrics':{'toffoli':3000,'qubits':200,'tq_cost':600000}}))
            return 0
        inspections=iter([
            {'session_id':'ses_OFFLINETEST','last_finish':'length','tool_counts':{'read':17,'quantum_evaluate':1},'assistant_errors':[]},
            {'session_id':'ses_OFFLINETEST','last_finish':'stop','tool_counts':{'read':17,'quantum_evaluate':2,'edit':1},'assistant_errors':[]},
        ])
        with patch.object(m,'database',return_value=(FakeProgram,self.db)), \
             patch.object(m,'orphaned_opencode_process',return_value=False), \
             patch.object(m.shutil,'which',return_value='/usr/bin/fake-opencode'), \
             patch.object(m,'run_command',side_effect=runner), \
             patch.object(m,'inspect_opencode_session',side_effect=lambda *a: next(inspections)), \
             patch.dict(m.os.environ,{'DEEPSEEK_API_KEY':'TEST_ONLY_NO_NETWORK'}):
            result=m.paid_step(self.p,timeout=360,max_continuations=1)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['model_turns'],2)
        self.assertEqual(result['model_last_finish'],'stop')
        self.assertEqual(len(result['returned_to_database']),1)
        self.assertTrue(result['restored_original'])
        self.assertEqual(self.p.candidate.read_bytes(),self.original)
        self.assertEqual(seen[0][1],'opencode.log')
        self.assertEqual(seen[1][1],'opencode-continue-1.log')

    def test_length_cap_zero_marks_incomplete_not_completed(self):
        def no_edits(*args,**kwargs): return 0
        with patch.object(m,'database',return_value=(FakeProgram,self.db)), \
             patch.object(m,'orphaned_opencode_process',return_value=False), \
             patch.object(m.shutil,'which',return_value='/usr/bin/fake-opencode'), \
             patch.object(m,'run_command',side_effect=no_edits), \
             patch.object(m,'inspect_opencode_session',return_value={
                 'session_id':'ses_OFFLINETEST','last_finish':'length',
                 'tool_counts':{},'assistant_errors':[]}), \
             patch.dict(m.os.environ,{'DEEPSEEK_API_KEY':'TEST_ONLY_NO_NETWORK'}):
            result=m.paid_step(self.p,timeout=120,max_continuations=0)
        self.assertEqual(result['status'],'model_length_unfinished')
        self.assertEqual(result['continuation_stop_reason'],'continuation_cap_reached')
        self.assertEqual(result['returned_to_database'],[])
        self.assertTrue(result['restored_original'])

    def test_session_inspection_bad_export_fails_closed_and_restores(self):
        with patch.object(m,'database',return_value=(FakeProgram,self.db)), \
             patch.object(m,'orphaned_opencode_process',return_value=False), \
             patch.object(m.shutil,'which',return_value='/usr/bin/fake-opencode'), \
             patch.object(m,'run_command',return_value=0), \
             patch.object(m,'inspect_opencode_session',side_effect=RuntimeError('Unmatched session')), \
             patch.dict(m.os.environ,{'DEEPSEEK_API_KEY':'TEST_ONLY_NO_NETWORK'}):
            with self.assertRaisesRegex(RuntimeError,'Unmatched session'):
                m.paid_step(self.p,timeout=120)
        self.assertEqual(self.p.candidate.read_bytes(),self.original)
        self.assertEqual(self.db.saved,0)

    def test_python_interpreter_symlink_allowed_but_protected_files_not(self):
        py=self.p.python
        py.unlink()
        actual=py.parent/'python-real'
        actual.write_text('fake interpreter')
        py.symlink_to(actual)
        original,_=m._assert_security(self.p)
        self.assertEqual(original,self.original)
        self.p.bridge.unlink()
        self.p.bridge.symlink_to(actual)
        with self.assertRaisesRegex(RuntimeError,'Required file absent or symlinked'):
            m._assert_security(self.p)

    def test_session_export_metadata_extraction(self):
        import subprocess
        entries=[{'id':'ses_FAKE123','title':'quantum-evolve-test','directory':str(self.p.lab)}]
        transcript={'messages':[
            {'info':{'role':'assistant','finish':'tool-calls'},'parts':[{'type':'tool','tool':'quantum_evaluate'}]},
            {'info':{'role':'assistant','finish':'length'},'parts':[{'type':'tool','tool':'read'}]}]}
        with patch.object(m.subprocess,'run',side_effect=[
            subprocess.CompletedProcess([],0,json.dumps(entries),''),
            subprocess.CompletedProcess([],0,json.dumps(transcript),'')]) as run:
            result=m.inspect_opencode_session('/usr/bin/fake-opencode',self.p.lab,{},'quantum-evolve-test')
        self.assertEqual(result['session_id'],'ses_FAKE123')
        self.assertEqual(result['last_finish'],'length')
        self.assertEqual(result['tool_counts'],{'quantum_evaluate':1,'read':1})
        self.assertEqual(run.call_count,2)

    def test_actual_cli_1194_export_subprocess_contract(self):
        """Subprocess fake enforces the real OpenCode 1.19.4 export shape.

        This fake rejects `session export`, which passed the previous
        mocked tests and broke a real paid run after the model had exited.
        """
        fake = self.home / "fake-opencode"
        arguments = self.home / "opencode-arguments.jsonl"
        fake.write_text("#!" + sys.executable + "\n" + r"""
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['FAKE_OPENCODE_ARGUMENT_LOG']).open('a') as log:
    log.write(json.dumps(args) + '\n')
if args[:2] == ['session', 'list'] and '--format' in args:
    print(json.dumps([{'id': 'ses_FAKETEST123', 'title': 'quantum-evolve-check',
                       'directory': os.getcwd()}]))
elif args == ['export', 'ses_FAKETEST123', '--sanitize']:
    print(json.dumps({'messages': [{'info': {'role': 'assistant', 'finish': 'length',
                                            'tokens': {'reasoning': 32000}},
                                     'parts': [{'type': 'tool', 'tool': 'quantum_evaluate'}]}]}))
else:
    print('Unsupported CLI shape', file=sys.stderr)
    sys.exit(13)
""")
        fake.chmod(0o700)
        env = dict(m.os.environ, FAKE_OPENCODE_ARGUMENT_LOG=str(arguments))
        result = m.inspect_opencode_session(str(fake), self.p.lab, env,
                                           'quantum-evolve-check')
        self.assertEqual(result['session_id'], 'ses_FAKETEST123')
        self.assertEqual(result['last_finish'], 'length')
        self.assertEqual(result['tool_counts']['quantum_evaluate'], 1)
        issued = [json.loads(line) for line in arguments.read_text().splitlines()]
        self.assertEqual(len(issued), 2)
        self.assertEqual(issued[0][:2], ['session', 'list'])
        self.assertEqual(issued[1], ['export', 'ses_FAKETEST123', '--sanitize'])

    def test_known_session_uses_export_without_list(self):
        import subprocess
        payload = {'messages': [{'info': {'role':'assistant', 'finish':'stop'},
                                 'parts': []}]}
        with patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(payload), '')) as run:
            result = m.inspect_opencode_session('/usr/bin/fake-opencode',
                self.p.lab, {}, 'unused', 'ses_EXISTING123')
        self.assertEqual(result['last_finish'], 'stop')
        self.assertEqual(run.call_args.args[0], ['/usr/bin/fake-opencode',
                                                 'export', 'ses_EXISTING123', '--sanitize'])

    def test_actual_subprocess_timeout_group_reaped(self):
        path=self.home/'timeout.log'
        start=time.monotonic()
        with self.assertRaises(TimeoutError):
            m.run_command([sys.executable,'-c','import time;time.sleep(30)'],
                          self.home, dict(m.os.environ),1,path)
        self.assertLess(time.monotonic()-start,8)


if __name__=='__main__':
    unittest.main(verbosity=2)

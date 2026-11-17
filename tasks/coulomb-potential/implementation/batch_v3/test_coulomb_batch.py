import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import coulomb_batch as b


class StubDB:
    def __init__(self, best=None, children=None):
        self.best = best or SimpleNamespace(id=b.EXPECTED_NEW_BEST_SHA,
                                            code='fake improved program',
                                            metrics={'tq_cost': b.EXPECTED_NEW_BEST_TQ})
        self.programs = {str(i): True for i in range(9)}
        self.children = children if children is not None else set(b.PROVEN_OFFSPRING)

    def get(self, sha):
        return True if sha in self.children else None

    def get_best_program(self):
        return self.best


class TestSafePreflight(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.dbpath = self.home/'db'
        self.dbpath.mkdir()
        self.runroot = self.home/'runs'
        self.runroot.mkdir()
        self.lock = self.home/'pilot.lock'
        self.good = b'protected best test bytes'
        self.current_sha = hashlib.sha256(self.good).hexdigest()
        self._expected = b.EXPECTED_WORKTREE_SHA
        b.EXPECTED_WORKTREE_SHA = self.current_sha
        self.addCleanup(setattr, b, 'EXPECTED_WORKTREE_SHA', self._expected)
        self.parent = SimpleNamespace(candidate=self.home/'circuit.py',runroot=self.runroot,lock=self.lock)
        self.parent.candidate.write_bytes(self.good)
        self.db = StubDB()
        self.mod = SimpleNamespace(Paths=lambda home,db: self.parent,
                                   _assert_security=lambda p:(self.good,0o600),
                                   orphaned_opencode_process=lambda:False,
                                   database=lambda p:(object,self.db))

    def test_pass_reports_nine_candidates_and_best(self):
        r = b.read_preflight(self.mod,self.home,self.dbpath)
        self.assertEqual(r['db_program_count'],9)
        self.assertEqual(r['db_best_tq'],577700)
        self.assertEqual(r['deepseek_api_calls'],0)

    def test_stops_if_db_has_only_eight_candidates(self):
        self.db.programs.pop("8")
        with self.assertRaisesRegex(RuntimeError,"at least nine"):
            b.read_preflight(self.mod,self.home,self.dbpath)

    def test_stops_when_one_verified_offspring_missing(self):
        self.db.children.remove(b.PROVEN_OFFSPRING[1])
        with self.assertRaisesRegex(RuntimeError,'offspring absent'):
            b.read_preflight(self.mod,self.home,self.dbpath)

    def test_after_valid_evolution_allows_normal_old_cell_pruning(self):
        self.db.children.remove(b.PROVEN_OFFSPRING[0])
        self.db.programs.pop("8")
        r=b.read_preflight(self.mod,self.home,self.dbpath, require_historical=False)
        self.assertFalse(r["three_v62_children_present"])
        self.assertEqual(len(r["missing_historical_offspring"]),1)

    def test_stops_on_regressed_best(self):
        self.db.best.metrics['tq_cost'] = 680430
        with self.assertRaisesRegex(RuntimeError,'expected 577700'):
            b.read_preflight(self.mod,self.home,self.dbpath)

    def test_stops_if_active_marker_exists(self):
        (self.runroot/'active.json').write_text('{}')
        with self.assertRaisesRegex(RuntimeError,'active run'):
            b.read_preflight(self.mod,self.home,self.dbpath)

    def test_stops_on_orphaned_model_process(self):
        self.mod.orphaned_opencode_process=lambda:True
        with self.assertRaisesRegex(RuntimeError,'another OpenCode'):
            b.read_preflight(self.mod,self.home,self.dbpath)

    def test_stops_on_v62_held_lock(self):
        import fcntl
        with self.lock.open('w') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            # flock same process via separate file description conflicts on Linux.
            with self.assertRaisesRegex(RuntimeError,'holds its lock'):
                b.read_preflight(self.mod,self.home,self.dbpath)
            fcntl.flock(f,fcntl.LOCK_UN)

    def test_accept_future_genuinely_better_best(self):
        code='future circuit'
        h=hashlib.sha256(code.encode()).hexdigest()
        self.db.best=SimpleNamespace(id=h,code=code,metrics={'tq_cost':500000})
        self.assertEqual(b.read_preflight(self.mod,self.home,self.dbpath)['db_best_tq'],500000)

    def test_future_best_reject_hash_mismatch(self):
        self.db.best=SimpleNamespace(id='bad',code='future',metrics={'tq_cost':500000})
        with self.assertRaisesRegex(RuntimeError,'hash mismatch'):
            b.read_preflight(self.mod,self.home,self.dbpath)


class TestBoundedBatch(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.runroot=self.root/'runs'
        self.runroot.mkdir()
        self.mod=SimpleNamespace(Paths=lambda home, db:SimpleNamespace(runroot=self.runroot))
        self.env=patch.dict(os.environ, {
            'DEEPSEEK_API_KEY':'fake_key_not_used_in_mock',
            'OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX':'65536',
            'OPENCODE_CONFIG_CONTENT':json.dumps({'provider':{'deepseek':{'models':{
                'deepseek-v4-pro':{'options':{'reasoningEffort':'low'}}}}}})
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.c= self.root/'openevolve_opencode_loop.py'
        self.c.write_text('# mocked v6.2')
        self.db= self.root/'db'
        self.db.mkdir()
        self.batch= self.root/'batches'

    def mock_execution(self, statuses, parent_tqs=None, parent_shas=None):
        data=list(statuses)
        def step(controller,dbpath,timeout,max_grades,max_continuations,logfile):
            status,off,rc,restored=data.pop(0)
            index=len(statuses)-len(data)-1
            outdir=self.runroot/f'20261026T00000{len(statuses)-len(data)}Z-mock'
            outdir.mkdir()
            (outdir/'summary.json').write_text(json.dumps({
                'status':status,'returned_to_database':off,'restored_original':restored,
                'parent_sha256':(parent_shas[index] if parent_shas else
                                 '3db1f5b20361a80041fc67f37ad412c64cdec05e3008a5cfd52f6e0322b5e6f8'),
                'parent_tq':(parent_tqs[index] if parent_tqs else 1601460),
                'model_turns':1,'successful_snapshots':len(off or [])}))
            logfile.write_text('mock step. no actual model call')
            return rc
        return step

    def run_with(self, statuses, rounds=3, max_empty=2, max_no_best=None,
                 best_values=None, max_no_progress=None,
                 min_parent_improvement_pct=1.0, retained=True,
                 parent_tqs=None, parent_shas=None):
        value_iter = iter(best_values) if best_values is not None else None
        def mocked_preflight(*args, **kwargs):
            tq=next(value_iter) if value_iter is not None else 577700
            hashes=kwargs.get('offspring_hashes', ())
            return {'status':'COULOMB_BATCH_PREFLIGHT_PASS', 'db_best_tq':tq,
                    'retained_new_offspring_sha256':list(hashes) if retained else [],
                    'missing_new_offspring_sha256':[] if retained else list(hashes)}
        with patch.object(b,'read_preflight',side_effect=mocked_preflight),\
             patch.object(b,'execute_step',side_effect=self.mock_execution(
                 statuses, parent_tqs=parent_tqs, parent_shas=parent_shas)) as sub:
            args=(self.mod,self.c,self.db,self.root,rounds,1200,8,1,max_empty,
                  self.batch,max_no_best,max_no_progress,min_parent_improvement_pct)
            return b.run_batch(*args),sub.call_count

    def test_two_success_then_stop_on_no_new(self):
        success=[{'sha256':'a'*64,'tq_cost':550000}]
        result,calls=self.run_with([('completed',success,0,True),
                                     ('model_timeout_no_new_offspring',[],0,True),
                                     ('completed_no_new_offspring',[],0,True)],3,2)
        self.assertEqual(calls,3)
        self.assertEqual(result['status'],'finished')
        self.assertEqual(len(result['rounds']),3)
        self.assertIn('2 consecutive', result['stop_reason'])
        self.assertEqual(result['rounds'][0]['new_offspring'],success)

    def test_stops_at_first_failed_round(self):
        with self.assertRaisesRegex(RuntimeError,'not cleanly accepted'):
            self.run_with([('completed',[{'sha256':'a'*64,'tq_cost':600000}],0,True),
                           ('failed',[],2,True),
                           ('completed',[],0,True)],3,2)
        reports=list(self.batch.glob('*/batch.json'))
        self.assertEqual(len(reports),1)
        d=json.loads(reports[0].read_text())
        self.assertEqual(d['status'],'failed')
        self.assertEqual(len(d['rounds']),2)

    def test_stops_when_worktree_not_restored(self):
        with self.assertRaisesRegex(RuntimeError,'not cleanly accepted'):
            self.run_with([('completed',[],0,False)],3,1)

    def test_no_new_offspring_stops_after_first_when_requested(self):
        r,c=self.run_with([('model_timeout_no_new_offspring',[],0,True)],3,1)
        self.assertEqual(c,1)
        self.assertEqual(r['status'],'finished')

    def test_paid_env_required(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':''}):
            with self.assertRaisesRegex(RuntimeError,'key missing'):
                self.run_with([])

    def test_reject_high_effort_not_just_any_low_word(self):
        config={'provider':{'deepseek':{'models':{'deepseek-v4-pro':{'options':{'reasoningEffort':'high'}}}}},'comment':'low'}
        with patch.dict(os.environ,{'OPENCODE_CONFIG_CONTENT':json.dumps(config)}):
            with self.assertRaisesRegex(RuntimeError,'expected tested'):
                self.run_with([])

    def test_rounds_bounded(self):
        with self.assertRaisesRegex(ValueError,'Bounded'):
            self.run_with([],rounds=11,max_empty=1)


class TestResearchProgress(unittest.TestCase):
    def setUp(self):
        self.base = TestBoundedBatch('test_rounds_bounded')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)

    def test_progress_indicates_improved_parent_but_not_global(self):
        inner={'parent_sha256':'a'*64,'parent_tq':1601460,
               'returned_to_database':[{'sha256':'b'*64,'tq_cost':1465380}],
               'model_turns':1,'successful_snapshots':5}
        r=b.round_progress(inner, {'db_best_tq':577700},{'db_best_tq':577700})
        self.assertTrue(r['parent_improved_by_child'])
        self.assertFalse(r['new_global_best'])
        self.assertEqual(r['best_offspring_tq'],1465380)
        self.assertEqual(r['global_best_tq_reduction'],0)

    def test_progress_rejects_best_regression(self):
        inner={'parent_sha256':'a'*64,'parent_tq':600000,'returned_to_database':[]}
        with self.assertRaisesRegex(RuntimeError,'regressed'):
            b.round_progress(inner, {'db_best_tq':500000},{'db_best_tq':510000})

    def test_progress_rejects_non_finite_or_missing_child_metric(self):
        inner={'parent_sha256':'a'*64,'parent_tq':600000,
               'returned_to_database':[{'sha256':'b'*64,'tq_cost':float('nan')}]}
        with self.assertRaisesRegex(RuntimeError,'invalid offspring'):
            b.round_progress(inner,{'db_best_tq':577700},{'db_best_tq':577700})

    def test_two_rounds_with_new_offspring_but_no_global_record_stop_by_plateau(self):
        child=[{'sha256':'c'*64,'tq_cost':1465380}]
        r,calls=self.base.run_with([
            ('model_timeout_with_verified_offspring',child,0,True),
            ('model_timeout_with_verified_offspring',child,0,True),
            ('completed',child,0,True)],rounds=3,max_empty=3,max_no_best=2)
        self.assertEqual(calls,2)
        self.assertEqual(r['status'],'finished')
        self.assertIn('without a new global best',r['stop_reason'])
        self.assertEqual(r['rounds'][1]['progress']['db_best_tq_after'],577700)
        self.assertFalse(r['rounds'][1]['progress']['new_global_best'])
        eventfiles=list(self.base.batch.glob('*/events.jsonl'))
        self.assertEqual(len(eventfiles),1)
        events=[json.loads(line) for line in eventfiles[0].read_text().splitlines()]
        self.assertEqual([e['event'] for e in events],
                         ['batch_started','round_started','round_completed',
                          'round_started','round_completed','batch_finished'])
        self.assertEqual(events[-2]['best_offspring_tq'],1465380)
        self.assertNotIn('DEEPSEEK_API_KEY',eventfiles[0].read_text())

    def test_improved_global_resets_plateau_counter(self):
        c=[{'sha256':'b'*64,'tq_cost':550000}]
        # read_preflight happens at: batch start, then before & after each step
        r,calls=self.base.run_with([
            ('completed',c,0,True),('completed',c,0,True),
            ('completed',c,0,True)],rounds=3,max_empty=3,max_no_best=2,
            best_values=[577700,577700,577700,577700,550000,550000,550000])
        self.assertEqual(calls,3)
        self.assertTrue(r['rounds'][1]['progress']['new_global_best'])
        self.assertEqual(r['rounds'][1]['progress']['db_best_tq_after'],550000)
        self.assertIsNone(r.get('stop_reason'))

    def test_regressed_best_stops_without_another_model_call(self):
        c=[{'sha256':'b'*64,'tq_cost':600000}]
        with self.assertRaisesRegex(RuntimeError,'regressed'):
            self.base.run_with([('completed',c,0,True),('completed',c,0,True)],
                rounds=2,max_empty=2,best_values=[577700,577700,600000])
        d=json.loads(next(self.base.batch.glob('*/batch.json')).read_text())
        self.assertEqual(d['status'],'failed')
        self.assertEqual(len(d['rounds']),1)

    def test_reject_invalid_max_no_best_before_creating_batch(self):
        with self.assertRaisesRegex(ValueError,'max-no-best'):
            self.base.run_with([],rounds=3,max_empty=3,max_no_best=4)

    def test_event_file_refuses_symlink(self):
        p=self.base.root/'event.jsonl'
        target=self.base.root/'target'
        target.write_text('cannot overwrite')
        p.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError,'symlink'):
            b.append_event(p,{'event':'x'})
        self.assertEqual(target.read_text(),'cannot overwrite')


class TestRealLocalSubprocessBoundary(unittest.TestCase):
    def test_execute_step_invokes_only_supplied_fake_script(self):
        # Real subprocess, but the child is a deliberately network-free tiny Python file.
        with TemporaryDirectory() as td:
            root = Path(td)
            fake = root / 'openevolve_opencode_loop.py'
            fake.write_text("""import json,sys
print(json.dumps({'received':sys.argv[1:]}))
""")
            log = root / 'fake-execution.log'
            rc = b.execute_step(fake, root / 'db', 1200, 8, 1, log)
            self.assertEqual(rc, 0)
            row = json.loads(log.read_text())
            self.assertEqual(row['received'][0:2], ['--paid-step','--ack-paid'])
            self.assertIn('--timeout', row['received'])
            self.assertIn('1200', row['received'])
            self.assertIn('--max-continuations', row['received'])


if __name__=='__main__':
    unittest.main()


class TestV3ProgressStop(unittest.TestCase):
    """Scenario tests exercising the real batch loop with a network-free v6.2 stub."""

    def setUp(self):
        self.base = TestBoundedBatch('test_rounds_bounded')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)

    def test_two_successive_branch_improvements_do_not_stop_search(self):
        a='a'*64
        bsha='b'*64
        csha='c'*64
        scenarios = [
            ('model_timeout_with_verified_offspring',[{'sha256':bsha,'tq_cost':642675}],0,True),
            ('model_timeout_with_verified_offspring',[{'sha256':csha,'tq_cost':593553}],0,True),
            ('model_timeout_no_new_offspring',[],0,True),
        ]
        report,n=self.base.run_with(scenarios, rounds=3, max_empty=3,
             max_no_progress=2, parent_tqs=[821755,642675,593553],
             parent_shas=[a,bsha,csha])
        self.assertEqual(n,3)
        self.assertEqual(report['status'],'finished')
        self.assertIsNone(report.get('stop_reason'))
        self.assertTrue(report['rounds'][0]['progress']['made_research_progress'])
        self.assertTrue(report['rounds'][1]['progress']['made_research_progress'])
        self.assertTrue(report['rounds'][1]['progress']['parent_is_previous_round_offspring'])
        self.assertLess(report['rounds'][1]['progress']['best_retained_offspring_tq'],577700+20000)

    def test_two_bad_rounds_stop_despite_new_offspring(self):
        c=[{'sha256':'b'*64,'tq_cost':1550052}]
        report,n=self.base.run_with([
            ('model_timeout_with_verified_offspring',c,0,True),
            ('model_timeout_with_verified_offspring',c,0,True),
            ('model_timeout_with_verified_offspring',c,0,True)],
            rounds=3,max_empty=3,max_no_progress=2,
            parent_tqs=[680430,680430,680430])
        self.assertEqual(n,2)
        self.assertIn('2 consecutive rounds without global-best',report['stop_reason'])
        self.assertFalse(report['rounds'][0]['progress']['made_research_progress'])

    def test_child_better_but_evicted_does_not_reset_no_progress_counter(self):
        child=[{'sha256':'b'*64,'tq_cost':642675}]
        report,n=self.base.run_with([
            ('model_timeout_with_verified_offspring',child,0,True),
            ('model_timeout_with_verified_offspring',child,0,True)],
            rounds=2,max_empty=2,max_no_progress=1,
            parent_tqs=[821755,821755],retained=False)
        self.assertEqual(n,1)
        self.assertFalse(report['rounds'][0]['progress']['made_research_progress'])
        self.assertEqual(report['rounds'][0]['progress']['retained_offspring_count'],0)
        self.assertEqual(len(report['rounds'][0]['new_offspring']),1)

    def test_minor_parent_improvement_below_threshold_is_not_progress(self):
        child=[{'sha256':'b'*64,'tq_cost':995000}]
        report,n=self.base.run_with([
            ('completed',child,0,True),('completed',child,0,True)],
            rounds=2,max_empty=2,max_no_progress=1,
            min_parent_improvement_pct=1.0,parent_tqs=[1000000,1000000])
        self.assertEqual(n,1)
        self.assertAlmostEqual(report['rounds'][0]['progress']['retained_parent_improvement_pct'],0.5)
        self.assertTrue(report['rounds'][0]['progress']['parent_improved_by_child'])
        self.assertFalse(report['rounds'][0]['progress']['meaningful_parent_progress'])

    def test_global_best_always_qualifies_as_progress(self):
        child=[{'sha256':'b'*64,'tq_cost':550000}]
        report,n=self.base.run_with([
            ('completed',child,0,True),('completed_no_new_offspring',[],0,True)],
            rounds=2,max_empty=2,max_no_progress=1,
            parent_tqs=[500000,500000], best_values=[577700,577700,550000,
                                                      550000,550000])
        self.assertEqual(n,2)
        self.assertTrue(report['rounds'][0]['progress']['new_global_best'])
        self.assertTrue(report['rounds'][0]['progress']['made_research_progress'])

    def test_invalid_stagnation_combinations_refused_before_expense(self):
        with self.assertRaisesRegex(ValueError,'only ONE'):
            self.base.run_with([],rounds=3,max_no_best=2,max_no_progress=2)
        self.assertFalse(list(self.base.batch.glob('*/batch.json')))

    def test_invalid_threshold_refused_before_expense(self):
        for value in [0,100,float('nan'),float('inf')]:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError,'improvement-pct'):
                    self.base.run_with([],rounds=3,max_no_progress=2,
                                       min_parent_improvement_pct=value)

    def test_audit_rejects_child_sha_not_submitted(self):
        inner={'parent_sha256':'a'*64,'parent_tq':821755,
               'returned_to_database':[{'sha256':'b'*64,'tq_cost':642675}]}
        with self.assertRaisesRegex(RuntimeError,'unexpected hashes'):
            b.round_progress(inner, {'db_best_tq':577700},
                             {'db_best_tq':577700,'retained_new_offspring_sha256':['c'*64]})

    def test_no_audit_does_not_mislabel_persistent_progress(self):
        inner={'parent_sha256':'a'*64,'parent_tq':821755,
               'returned_to_database':[{'sha256':'b'*64,'tq_cost':642675}]}
        result=b.round_progress(inner, {'db_best_tq':577700},{'db_best_tq':577700})
        self.assertFalse(result['retained_offspring_audited'])
        self.assertFalse(result['made_research_progress'])
        self.assertTrue(result['parent_improved_by_child'])

    def test_db_persistence_check_piggybacks_on_native_reload(self):
        st=TestSafePreflight('test_pass_reports_nine_candidates_and_best')
        st.setUp()
        self.addCleanup(st.doCleanups)
        good=b.PROVEN_OFFSPRING[0]
        missing='a'*64
        res=b.read_preflight(st.mod,st.home,st.dbpath,offspring_hashes=(good,missing))
        self.assertEqual(res['retained_new_offspring_sha256'],[good])
        self.assertEqual(res['missing_new_offspring_sha256'],[missing])

import unittest
from coulomb_report import summarize

class TestReport(unittest.TestCase):
    def test_old_v1_report_is_readable(self):
        d={'status':'finished','initial_state':{'db_best_tq':577700},
           'requested_rounds':2,
           'rounds':[{'number':1,'status':'verified_completed','inner_status':'model_timeout_with_verified_offspring',
                      'preflight':{'db_best_tq':577700},'postflight':{'db_best_tq':577700},
                      'new_offspring':[{'sha256':'x','tq_cost':1465380}], 'restored_original':True}]}
        r=summarize(d)
        self.assertEqual(r['latest_best_tq'],577700)
        self.assertEqual(r['rounds'][0]['offspring_count'],1)
        self.assertFalse(r['rounds'][0]['new_global_best'])
        self.assertEqual(r['rounds'][0]['best_offspring_tq'],1465380)

    def test_new_v2_report_has_parent_and_global_improvement(self):
        d={'status':'finished','initial_state':{'db_best_tq':577700},
           'requested_rounds':1,'rounds':[{'number':1,'status':'verified_completed',
           'progress':{'parent_tq':680430,'offspring_count':2,'best_offspring_tq':500000,
                       'db_best_tq_before':577700,'db_best_tq_after':500000,
                       'new_global_best':True}, 'restored_original':True}]}
        r=summarize(d)
        self.assertEqual(r['latest_best_tq'],500000)
        self.assertTrue(r['rounds'][0]['new_global_best'])
        self.assertEqual(r['rounds'][0]['parent_tq'],680430)

    def test_historical_v2_lineage_reconstructed_by_sha_not_only_metric(self):
        first='a'*64
        second='b'*64
        d={'status':'finished', 'initial_state':{'db_best_tq':577700},
           'requested_rounds':2, 'rounds':[
            {'number':1,'status':'verified_completed',
             'progress':{'parent_sha256':'0'*64,'parent_tq':821755},
             'new_offspring':[{'sha256':first,'tq_cost':642675}],
             'postflight':{'db_best_tq':577700}},
            {'number':2,'status':'verified_completed',
             'progress':{'parent_sha256':first,'parent_tq':642675},
             'new_offspring':[{'sha256':second,'tq_cost':593553}],
             'postflight':{'db_best_tq':577700}}]}
        r=summarize(d)
        self.assertIsNone(r['rounds'][0]['parent_is_previous_round_offspring'])
        self.assertTrue(r['rounds'][1]['parent_is_previous_round_offspring'])
        self.assertAlmostEqual(r['rounds'][0]['parent_improvement_pct'],
                               100*(821755-642675)/821755)
        self.assertAlmostEqual(r['rounds'][1]['parent_improvement_pct'],
                               100*(642675-593553)/642675)
        self.assertAlmostEqual(r['rounds'][1]['best_offspring_gap_to_global_pct'],
                               100*(593553-577700)/577700)

    def test_same_cost_but_different_sha_not_reuse(self):
        r=summarize({'rounds':[
            {'number':1,'new_offspring':[{'sha256':'a'*64,'tq_cost':642675}]},
            {'number':2,'progress':{'parent_sha256':'b'*64,'parent_tq':642675}}]})
        self.assertFalse(r['rounds'][1]['parent_is_previous_round_offspring'])

    def test_running_batch_does_not_claim_done(self):
        r=summarize({'status':'running','initial_state':{'db_best_tq':577700},
                    'rounds':[{'number':1,'status':'running'}]})
        self.assertEqual(r['completed_rounds'],0)
        self.assertEqual(r['latest_best_tq'],577700)
        self.assertIsNone(r['rounds'][0]['best_offspring_tq'])

if __name__ == '__main__':
    unittest.main()

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from research_review import summarize,check_experiment,review_registry,backtest_reference

class ReviewChecks(unittest.TestCase):
    def environment(self):
        return {'datasets':{'indices':{'status':'ok','data':[{'name':'A','change_pct':-2},{'name':'B','change_pct':-1.2}]},'breadth':{'status':'ok','data':{'count':100,'up':10}},'broken':{'status':'ok'},'down_limit':{'status':'ok'}}}
    def test_joint_stress_not_buy_signal(self):
        r=summarize({'roles':[]},self.environment(),{'status':'not_configured'})
        self.assertEqual(r['posture'],'defensive_research');self.assertFalse(r['auto_orders'])
    def test_missing_environment_overrides_other_signals(self):
        e=self.environment();e['datasets'].pop('broken')
        self.assertEqual(summarize({'roles':[]},e,{'status':'analyzed'})['posture'],'data_gap')
    def test_same_claim_not_independent_consensus(self):
        claims=[{'subject':'机器人','view':'等待确认','url':u} for u in ['https://a','https://b']]
        r=summarize({'roles':[]},self.environment(),{'status':'analyzed','claims':claims})
        self.assertFalse(r['duplicate_claim_leads'][0]['independent_consensus'])
    def trial(self):
        return dict(version='v2',hypothesis='test',parent_version='v1',train_start='2024-01-01',train_end='2025-01-01',holdout_start='2025-01-02',holdout_end='2026-01-01',fees_bps=3,slippage_bps=5,fill_model='conservative proxy',failed_trials=[],multiple_testing_control='fixed search budget and untouched holdout',excluded_samples=[],evidence_files=[])
    def test_no_artifact_no_promotion(self):
        with tempfile.TemporaryDirectory() as d:r=check_experiment(Path(d),self.trial())
        self.assertEqual(r['status'],'BLOCKED');self.assertFalse(r['automatic_adoption'])
    def test_hash_checked_and_holdout_disjoint(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'evidence.json';p.write_text('{}');t=self.trial();t['evidence_files']=[{'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}]
            r=check_experiment(root,t);self.assertEqual(r['status'],'READY_FOR_REVIEW');self.assertFalse(r['automatic_adoption'])
            p.write_text('{"changed":true}');self.assertEqual(check_experiment(root,t)['status'],'BLOCKED')
            t['holdout_start']=t['train_end'];self.assertEqual(check_experiment(root,t)['status'],'BLOCKED')
    def test_missing_and_corrupt_backtest_reference(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.assertEqual(backtest_reference(root),'')
            p=root/'state/backtests/LATEST.json';p.parent.mkdir(parents=True);p.write_text('bad')
            self.assertIn('未能读取',backtest_reference(root))
    def test_invalid_registry_record_visible(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state/experiments/bad.json';p.parent.mkdir(parents=True);p.write_text('bad')
            self.assertEqual(review_registry(Path(d))[0]['status'],'BLOCKED')
if __name__=='__main__':unittest.main()

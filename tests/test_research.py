import datetime as dt
import json
import os
import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from leader_study import build,apply_plan,themes,ledger
from research_data import minute,daily
from author_analysis import analyze,validate
from l2_import import validate as validate_l2,summary
from test_pipeline import market

class ResearchChecks(unittest.TestCase):
    def test_height_core_not_old_seal_score(self):
        s=market('2026-10-21','close','2026-10-21T16:00:00+08:00')
        low=dict(s['rows'][0],symbol='600001',streak=1,seal_ratio_pct=99)
        s['rows'].append(low)
        study=build(s,[],{})
        self.assertEqual(study['roles'][0]['symbol'],'002929');self.assertEqual(study['roles'][0]['role'],'市场高度锚')
        self.assertIsNone(study['groups'][0]['prior_count'])
    def test_alias_and_plan_version(self):
        self.assertEqual(themes('AI短剧+短剧'),['传媒/短剧'])
        study=build(market('2026-10-21','close','2026-10-21T16:00:00+08:00'),[],{})
        p=apply_plan({},study);self.assertFalse(p['candidates'][0]['buy_signal']);self.assertEqual(p['version'],study['version'])
    def test_minutes_reject_wrong_date(self):
        data={'data':{'sz002929':{'data':{'date':'20261020','data':['0930 10 100']}}}}
        with patch('research_data.fetch',return_value=data):r=minute('002929','2026-10-21','2026-10-21T12:00:00+08:00')
        self.assertEqual(r['status'],'failed')
    def test_minutes_filter_future(self):
        data={'data':{'sz002929':{'data':{'date':'20261021','data':['0930 10 100','0931 11 200']}}}}
        with patch('research_data.fetch',return_value=data):r=minute('002929','2026-10-21','2026-10-21T09:30:30+08:00')
        self.assertEqual(len(r['data']['bars']),1)
    def test_daily_historical_cutoff(self):
        data={'data':{'sz002929':{'qfqday':[['2026-10-20','10','11','12','9','100'],['2026-10-22','12','13','14','11','200']]}}}
        with patch('research_data.fetch',return_value=data):r=daily('002929','2026-10-21')
        self.assertEqual(len(r['data']),1)
    def test_model_unconfigured_explicit(self):
        with patch.dict(os.environ,{},clear=True):r=analyze([],'2026-10-21T20:17:00+08:00')
        self.assertEqual(r['status'],'not_configured')
    def test_model_rejects_fabricated_evidence(self):
        p={'url':'https://example.com','author':'A','_analysis_text':'等待确认'}
        c={'url':p['url'],'view':'看好','subject':'AI','condition':'未说明','invalid':'未说明','evidence_quote':'必涨'}
        with self.assertRaises(ValueError):validate({'claims':[c]},[p])
        c['evidence_quote']='等待确认';self.assertEqual(len(validate({'claims':[c]},[p])),1)
    def test_l2_retention_not_queue_fill(self):
        r={'symbol':'002929','quote_at':'2026-10-21T10:00:00+08:00','price':10,'limit_up_price':10,'bid_amount_yuan':1000,'ask_amount_yuan':0,'seal_amount_yuan':1000}
        p={'date':'2026-10-21','exported_at':'2026-10-21T11:00:00+08:00','source':'test','records':[r,dict(r,quote_at='2026-10-21T10:01:00+08:00',seal_amount_yuan=500)]}
        result=summary(validate_l2(p))[0];self.assertEqual(result['seal_snapshot_retention'],0.5);self.assertEqual(result['tradable_fill'],'unknown')
        p['records'][1]['quote_at']='2026-10-21T12:00:00+08:00'
        with self.assertRaises(ValueError):validate_l2(p)
    def test_all_frozen_candidates_retained_with_missing_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'state/plans/2026-10-21/p.json';path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'created_at':'2026-10-21T09:05:00+08:00','version':'v1','candidates':[{'symbol':'002929','name':'A'},{'symbol':'600001','name':'B'}]}))
            enrich={'datasets':{'daily':[{'name':'002929','status':'ok','data':[{'date':'2026-10-20','close':10},{'date':'2026-10-21','open':11,'close':9}]}]}}
            s=market('2026-10-21','close','2026-10-21T16:00:00+08:00');s['rows']=[]
            r=ledger(root,s,'2026-10-21T20:17:00+08:00',enrich)
            self.assertEqual(r['sample_count'],2);self.assertIsNone(r['records'][1]['benchmark'])
            self.assertFalse(r['records'][0]['in_close_limit_pool']);self.assertAlmostEqual(r['records'][0]['benchmark']['close_change_pct'],-10)
            self.assertIsNone(r['records'][0]['net_return'])
    def test_workflow_has_three_reports_and_data_only_track(self):
        w=(Path(__file__).resolve().parents[1]/'.github/workflows/research.yml').read_text()
        self.assertNotIn("cron: '37 7",w);self.assertIn("cron: '17 12",w);self.assertIn("phase=track",w)
if __name__=='__main__':unittest.main()

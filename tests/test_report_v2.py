import datetime as dt
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from report_v2 import risk_filter,trend_pool,render,to_html
from leader_study import build,apply_plan
from test_pipeline import market,ROW

class ReportChecks(unittest.TestCase):
    def snapshot(self):return market('2026-10-23','close','2026-10-23T16:00:00+08:00')
    def test_known_risk_excluded_unknown_not_certified(self):
        s=self.snapshot();s['rows']+=[dict(ROW,symbol='600001',name='*ST测试')]
        a=risk_filter(s,{})
        self.assertEqual(len(a['excluded']),1);self.assertFalse(a['complete'])
        self.assertEqual(a['unverified_symbols'],['002929'])
    def test_sourced_dated_status_required(self):
        s=self.snapshot();e={'security_status':{'002929':{'date':'2026-10-23','source':'https://example.com/exchange','is_st':False,'delisting_risk':False}}}
        self.assertTrue(risk_filter(s,e)['complete'])
        e['security_status']['002929']['date']='2026-10-22'
        self.assertFalse(risk_filter(s,e)['complete'])
    def test_exact_returns_and_broken_trend(self):
        s=self.snapshot();day=dt.date(2026,10,23)
        bars=[dict(date=(day-dt.timedelta(days=30-i)).isoformat(),close=10 if i<30 else 17,volume=100,open=10,high=17,low=10) for i in range(31)]
        e={'datasets':{'daily':[{'name':'002929','status':'ok','data':bars}]}}
        r=trend_pool(s,e)['rows'][0];self.assertAlmostEqual(r['return15_pct'],70)
        self.assertEqual(r['start15'],bars[-16]['date'])
        bars[-15:]=[dict(b,close=25) for b in bars[-15:]];bars[-1]['close']=17
        r=trend_pool(s,e);self.assertFalse(r['rows']);self.assertEqual(r['removed'][0]['state'],'趋势破坏')
    def test_30_day_gain_needs_actual_streak_evidence(self):
        s=self.snapshot();day=dt.date(2026,10,23)
        bars=[dict(date=(day-dt.timedelta(days=30-i)).isoformat(),close=10 if i==0 else 21,volume=100,open=10,high=21,low=10) for i in range(31)]
        e={'datasets':{'daily':[{'name':'002929','status':'ok','data':bars}]}}
        self.assertFalse(trend_pool(s,e)['rows'])
        e['limit_history']={'002929':[dict(date=b['date'],limit_up_close=(i<4)) for i,b in enumerate(bars)]}
        self.assertEqual(len(trend_pool(s,e)['rows']),1)
    def test_twelve_modules_and_no_legacy_rank(self):
        s=self.snapshot();study=build(s,[],{});p=apply_plan({},study)
        self.assertNotIn('baseline_ranking',study)
        self.assertIsNone(p['candidates'][0]['research_score']);self.assertEqual(p['candidates'][0]['score_coverage'],2)
        text=render(s,[],study,{}, {'status':'not_configured'},{'eligible_prospective':0})
        self.assertEqual(sum(line.startswith('## ') and '、' in line for line in text.splitlines()),12)
        self.assertNotIn('55',text);self.assertIn('严格剔除',text);self.assertIn('缺失',text)
    def test_rates_use_disjoint_same_scope_denominator(self):
        from report_v2 import metrics
        s=self.snapshot();s['rows']=[dict(ROW,symbol='002929'),dict(ROW,symbol='600001')]
        status={c:{'date':s['date'],'source':'https://example.com/status','is_st':False,'delisting_risk':False} for c in ('002929','600001','600002')}
        e={'security_status':status,'datasets':{'broken':{'status':'ok','data':{'tc':1,'pool':[{'c':'600002','n':'测试'}]}}}}
        result=metrics(s,[],e);self.assertAlmostEqual(result['seal_rate'],200/3);self.assertAlmostEqual(result['broken_rate'],100/3)
        e['datasets']['broken']['data']['pool'][0]['c']='002929'
        self.assertIsNone(metrics(s,[],e)['seal_rate'])
    def test_unknown_stock_risk_prevents_strict_total(self):
        from report_v2 import metrics
        self.assertIsNone(metrics(self.snapshot(),[],{})['up_limit'])
    def test_whole_cohort_premium_includes_failures(self):
        from report_v2 import metrics
        s=self.snapshot();s['rows']=[dict(ROW,symbol='002929',streak=2)]
        old=market('2026-10-22','close','2026-10-22T16:00:00+08:00');old['rows']=[dict(ROW,symbol=c,streak=1) for c in ('002929','600001')]
        statuses={d:{c:{'date':d,'source':'https://example.com','is_st':False,'delisting_risk':False} for c in ('002929','600001')} for d in ('2026-10-22','2026-10-23')}
        e={'security_status_by_date':statuses,'datasets':{'daily':[{'status':'ok','name':c,'data':[{'date':'2026-10-22','close':10},{'date':'2026-10-23','close':close}]} for c,close in [('002929',11),('600001',8)]]}}
        result=metrics(s,[old],e)
        self.assertAlmostEqual(result['first_promotion'],50);self.assertAlmostEqual(result['limit_premium'],-5)
        self.assertEqual(result['big_loss'],1)
    def test_old_saved_study_cannot_silently_reuse_score(self):
        with self.assertRaises(ValueError):apply_plan({}, {'version':'leader-evidence-v1.2'})
    def test_safe_html(self):
        text=to_html('# 标题\n<script>alert(1)</script>\n|a|b|\n|---|---|\n|x|y|')
        self.assertNotIn('<script>',text);self.assertIn('<table>',text)

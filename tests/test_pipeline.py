import datetime as dt
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from pipeline import perform,write_json,load_snapshots,evaluate
from public_sources import parse_post
from research_report import condition_plan

TZ=dt.timezone(dt.timedelta(hours=8))
ROW={'symbol':'002929','name':'示例','close':56.14,'change_pct':9.99,'seal_ratio_pct':1.32,'turnover_pct':8.86,'first_limit_time':'09:35:57','last_limit_time':'09:38:54','break_count':1,'streak':3,'theme_label':'测试+AI'}
def market(date,phase,observed):
    return {'date':date,'source_updated_at':date+('T11:35:00+08:00' if phase=='midday' else 'T15:35:00+08:00'),'observed_at':observed,'rows':[ROW.copy()],'count':1,'scope':'test','phase':phase}
def sources(config,observed):
    return {'collected_at':observed,'catalogues':[],'posts':[{'author':'测试','url':'https://www.tgb.cn/a/test','title':'测试','status':'partial_login_required','published_at':None}]}
def external(phase,observed,start):
    return {'phase':phase,'observed_at':observed,'news_window_start':start,'markets':[], 'events':[], 'news_sources':[{'source':'test','url':'https://example.com','status':'ok'}]}
class PipelineChecks(unittest.TestCase):
    def setUp(self):
        self.patch=patch('pipeline.collect_research',return_value={'datasets':{}});self.patch.start()
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'config').mkdir();write_json(self.root/'config/sources.json',{'authors':[]})
    def tearDown(self):self.patch.stop();self.tmp.cleanup()
    def test_real_state_paths(self):
        r=perform(self.root,'close',dt.datetime(2026,10,21,16,tzinfo=TZ),sources,market)
        self.assertEqual(r['exit_code'],0);self.assertTrue((self.root/r['report_path']).is_file());self.assertEqual(len(load_snapshots(self.root)),1)
    def test_validation_does_not_fake_close(self):
        r=perform(self.root,'validation',dt.datetime(2026,10,22,14,tzinfo=TZ),sources,market,external)
        self.assertEqual(r['exit_code'],0)
        self.assertIn('非收盘复盘',(self.root/r['report_path']).read_text())
        self.assertEqual(load_snapshots(self.root)[0]['phase'],'validation')
        self.assertFalse((self.root/'state/plans').exists())
    def test_morning_without_history_fails_visible(self):
        r=perform(self.root,'morning',dt.datetime(2026,10,22,9,tzinfo=TZ),sources,market,external)
        self.assertEqual(r['exit_code'],1);self.assertEqual(r['report_status'],'not_generated');self.assertTrue((self.root/'state/status.json').is_file())
    def test_morning_uses_previous(self):
        write_json(self.root/'state/snapshots/2026-10-21/close.json',market('2026-10-21','close','2026-10-21T16:00:00+08:00'))
        r=perform(self.root,'morning',dt.datetime(2026,10,22,9,tzinfo=TZ),sources,market,external)
        self.assertEqual(r['market_date'],'2026-10-21');self.assertEqual(r['exit_code'],0)
    def test_late_morning_never_fetches_or_freezes(self):
        with patch('pipeline.collect_external') as loader:
            r=perform(self.root,'morning',dt.datetime(2026,10,23,9,41,tzinfo=TZ),sources,market,loader)
        loader.assert_not_called()
        self.assertEqual(r['delivery_status'],'missed_deadline')
        self.assertEqual(r['result'],'failed')
        self.assertFalse((self.root/'state/plans').exists())
        self.assertFalse((self.root/'state/latest_by_phase.json').exists())
    def test_morning_fetch_crossing_deadline_never_freezes(self):
        write_json(self.root/'state/snapshots/2026-10-22/close.json',market('2026-10-22','close','2026-10-22T16:00:00+08:00'))
        with patch('pipeline.time.monotonic',side_effect=[0,61]):
            r=perform(self.root,'morning',dt.datetime(2026,10,23,9,14,tzinfo=TZ),sources,market,external)
        self.assertEqual(r['delivery_status'],'missed_deadline')
        self.assertFalse((self.root/'state/plans').exists())
    def test_deadline_exact_boundary(self):
        from pipeline import check_morning_deadline,MorningDeadlineMissed
        check_morning_deadline(dt.datetime(2026,10,23,9,14,59,tzinfo=TZ))
        with self.assertRaises(MorningDeadlineMissed):check_morning_deadline(dt.datetime(2026,10,23,9,15,tzinfo=TZ))
    def test_late_plan_excluded_from_tracking(self):
        write_json(self.root/'state/plans/2026-10-23/late.json',{'created_at':'2026-10-23T09:41:00+08:00','candidates':[{'symbol':'002929'}]})
        with patch('pipeline.minute') as fetch:
            r=perform(self.root,'track',dt.datetime(2026,10,23,10,5,tzinfo=TZ),sources,market,external)
        fetch.assert_not_called();self.assertEqual(r['market_status'],'no_frozen_plan')
    def test_late_midday_rejected(self):
        r=perform(self.root,'midday',dt.datetime(2026,10,21,15,tzinfo=TZ),sources,market)
        self.assertEqual(r['exit_code'],1);self.assertEqual(load_snapshots(self.root),[])
    def test_source_failure_does_not_fake_complete(self):
        def broken(*a):raise ValueError('source unavailable')
        r=perform(self.root,'close',dt.datetime(2026,10,21,16,tzinfo=TZ),broken,market)
        self.assertEqual(r['result'],'partial');self.assertEqual(r['source_readable'],0)
    def test_public_gate(self):
        html='<title>测试</title><div>淘股吧原创 2026-10-21 16:00</div><div>展开</div><p>可见内容</p><div>登录可查看全文</div><p>不可加入正文</p>'
        r=parse_post('https://www.tgb.cn/a/test',html,'测试','2026-10-21T17:00:00+08:00')
        self.assertEqual(r['status'],'partial_login_required');self.assertNotIn('不可加入正文',r['excerpt']);self.assertIn('可见内容',r['excerpt'])
    def test_missing_candidate_is_accounted(self):
        s=market('2026-10-21','close','2026-10-21T16:00:00+08:00');p=condition_plan(s,'2026-10-22','2026-10-21T18:00:00+08:00')
        write_json(self.root/'state/plans/2026-10-22/p.json',p)
        nextday=market('2026-10-22','close','2026-10-22T16:00:00+08:00');nextday['rows']=[]
        results=evaluate(self.root,nextday,'2026-10-22T17:00:00+08:00')
        self.assertEqual(len(results[0]['outcomes']),1);self.assertFalse(results[0]['outcomes'][0]['in_today_limit_pool']);self.assertIsNone(results[0]['pnl'])
    def test_evening_combines_close_and_external(self):
        r=perform(self.root,'evening',dt.datetime(2026,10,21,20,tzinfo=TZ),sources,market,external)
        self.assertEqual(r['exit_code'],0)
        text=(self.root/r['report_path']).read_text()
        self.assertIn('收盘复盘',text);self.assertIn('晚间外盘与事件补充',text)
        self.assertEqual(len(list((self.root/'reports/2026-10-21').glob('*.md'))),1)
        self.assertEqual(load_snapshots(self.root)[0]['phase'],'close')
        self.assertFalse((self.root/'state/plans').exists())
    def test_evening_domestic_failure_preserves_external(self):
        def broken(*a):raise ValueError('market unavailable')
        r=perform(self.root,'evening',dt.datetime(2026,10,21,20,tzinfo=TZ),sources,broken,external)
        self.assertEqual(r['result'],'partial');self.assertIn('收盘复盘缺失',(self.root/r['report_path']).read_text())
    def test_evening_external_failure_preserves_close(self):
        def broken(*a):raise ValueError('external unavailable')
        r=perform(self.root,'evening',dt.datetime(2026,10,21,20,tzinfo=TZ),sources,market,broken)
        self.assertEqual(r['result'],'partial');self.assertIn('外盘与事件补充失败',(self.root/r['report_path']).read_text())
    def test_delayed_midday_and_phase_index(self):
        r=perform(self.root,'midday',dt.datetime(2026,10,21,12,10,31,tzinfo=TZ),sources,market)
        self.assertEqual(r['exit_code'],0)
        self.assertIn('午间盘内观察',(self.root/r['report_path']).read_text())
        perform(self.root,'evening',dt.datetime(2026,10,21,20,tzinfo=TZ),sources,market,external)
        index=(self.root/'reports/LATEST.md').read_text()
        self.assertIn(r['run_key'],index)
        self.assertIn('evening',index)
    def test_morning_uses_evening_news_boundary(self):
        write_json(self.root/'state/snapshots/2026-10-21/close.json',market('2026-10-21','close','2026-10-21T16:00:00+08:00'))
        perform(self.root,'evening',dt.datetime(2026,10,21,20,tzinfo=TZ),sources,market,external)
        seen=[]
        def capture(phase,observed,start):
            seen.append(start);return external(phase,observed,start)
        r=perform(self.root,'morning',dt.datetime(2026,10,22,9,tzinfo=TZ),sources,market,capture)
        self.assertEqual(seen,['2026-10-21T20:00:00+08:00'])
        plan=json.loads((self.root/r['plan_path']).read_text())
        self.assertIsNotNone(plan['evening_context_path'])
        self.assertIn('external_revision',plan)
if __name__=='__main__':unittest.main()

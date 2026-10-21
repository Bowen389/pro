import datetime as dt
import json
import tempfile
import unittest
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
class PipelineChecks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'config').mkdir();write_json(self.root/'config/sources.json',{'authors':[]})
    def tearDown(self):self.tmp.cleanup()
    def test_real_state_paths(self):
        r=perform(self.root,'close',dt.datetime(2026,10,21,16,tzinfo=TZ),sources,market)
        self.assertEqual(r['exit_code'],0);self.assertTrue((self.root/r['report_path']).is_file());self.assertEqual(len(load_snapshots(self.root)),1)
    def test_morning_without_history_fails_visible(self):
        r=perform(self.root,'morning',dt.datetime(2026,10,22,9,tzinfo=TZ),sources,market)
        self.assertEqual(r['exit_code'],1);self.assertEqual(r['report_status'],'not_generated');self.assertTrue((self.root/'state/status.json').is_file())
    def test_morning_uses_previous(self):
        write_json(self.root/'state/snapshots/2026-10-21/close.json',market('2026-10-21','close','2026-10-21T16:00:00+08:00'))
        r=perform(self.root,'morning',dt.datetime(2026,10,22,9,tzinfo=TZ),sources,market)
        self.assertEqual(r['market_date'],'2026-10-21');self.assertEqual(r['exit_code'],0)
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
if __name__=='__main__':unittest.main()

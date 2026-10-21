import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import quant as q
import ai_bridge as ai


def fixture():
    rows=[]
    for i in range(90):
        day=dt.date(2025,1,1)+dt.timedelta(days=i)
        close=10+i*.02
        rows.append({'symbol':'DEMO','name':'合成标的','asset':'stock','date':day.isoformat(),'available_at':day.isoformat()+'T16:00:00+08:00','open':close-.01,'high':close+.1,'low':close-.1,'close':close,'volume':1000.,'amount':10000.,'prev_close':close-.02,'limit_up':round((close-.02)*1.1,2),'limit_down':round((close-.02)*.9,2),'board':'main','is_st':'false','tradable':'true','theme':'合成题材'})
    return rows


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=q.database(str(Path(self.tmp.name)/'db.sqlite'));self.rows=fixture()
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def test_cutoff(self):
        a=q.features(self.rows,'2025-03-01T17:00:00+08:00')
        b=q.features(self.rows+[dict(self.rows[-1],date='2025-04-02',available_at='2025-04-02T16:00:00+08:00',close=999)],'2025-03-01T17:00:00+08:00')
        self.assertEqual(a,b)
    def test_known_at_import_not_backfilled(self):
        q.learn(self.db,{'source':'test','available_at':'2024-01-01T16:00:00+08:00','claim':'主动性','tags':['主动性']})
        self.assertEqual(q.retrieve(self.db,'主动性','2025-01-01T00:00:00+08:00'),[])
    def test_correction(self):
        k=q.learn(self.db,{'source':'test','available_at':'2024-01-01T00:00:00+08:00','claim':'旧主动性','tags':['主动性']})
        q.learn(self.db,{'source':'test','available_at':'2024-01-02T00:00:00+08:00','claim':'新主动性','tags':['主动性'],'supersedes':k})
        results=q.retrieve(self.db,'主动性',q.clock());self.assertEqual(len(results),1);self.assertEqual(results[0]['claim'],'新主动性')
    def test_incomplete_idempotent(self):
        key,p=q.build_plan(self.db,self.rows,'2025-03-31T17:00:00+08:00','2025-04-01',demo=True)
        self.assertEqual(p['status'],'INCOMPLETE');self.assertEqual(key,q.build_plan(self.db,self.rows,'2025-03-31T17:00:00+08:00','2025-04-01',demo=True)[0])
        q.report(key,p,self.tmp.name);self.assertTrue((Path(self.tmp.name)/'pre_market.md').is_file())
    def test_stale_excluded(self):
        rows=self.rows+[dict(self.rows[0],symbol='STALE')]
        _,p=q.build_plan(self.db,rows,'2025-03-31T17:00:00+08:00','2025-04-01',2)
        self.assertTrue(any('陈旧' in x for x in p['warnings']));self.assertEqual(p['market']['universe_count'],1)
    def test_ai_unknown_reference(self):
        key,_=q.build_plan(self.db,self.rows,'2025-03-31T17:00:00+08:00','2025-04-01')
        request=ai.export_request(self.db,key)
        with self.assertRaises(ValueError):ai.accept(self.db,request,{'statements':[{'kind':'fact','text':'x','evidence_ids':['invented']}],'experiments':[]})
        self.assertTrue(ai.accept(self.db,request,{'statements':[{'kind':'hypothesis','text':'待验证','evidence_ids':[key]}],'experiments':[]}))
    def test_experiment_split_t1(self):
        _,p=q.experiment(self.db,self.rows,'2025-02-15','2025-02-20')
        self.assertTrue(p['trades'])
        for t in p['trades']:
            self.assertGreater(t['exit_date'],t['entry_date'])
            self.assertTrue(t['exit_date']<='2025-02-15' if t['phase']=='train' else t['signal_date']>='2025-02-20')
    def test_locked_entry_skipped(self):
        rows=[dict(r,limit_up=r['open']) for r in self.rows]
        _,p=q.experiment(self.db,rows,'2025-02-15','2025-02-20');self.assertEqual(p['trades'],[])
    def test_csv_invalid(self):
        f=Path(self.tmp.name)/'bad.csv';f.write_text('symbol,date,asset,available_at,open,high,low,close,volume,amount\nDEMO,2025-01-01,stock,2025-01-01T16:00:00+08:00,10,9,8,10,1,1\n')
        with self.assertRaises(ValueError):q.read_bars(f)
    def test_review_wrong_day(self):
        key,_=q.build_plan(self.db,self.rows,'2025-03-31T17:00:00+08:00','2025-04-01')
        f=Path(self.tmp.name)/'fills.csv';f.write_text('symbol,time,side,quantity,price\nDEMO,2025-04-02T10:00:00+08:00,BUY,100,10\n')
        with self.assertRaises(ValueError):q.review(self.db,key,f,Path(self.tmp.name)/'review.md')


if __name__=='__main__':unittest.main()

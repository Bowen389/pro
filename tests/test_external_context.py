import datetime as dt
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from external_context import MARKETS,parse_quote,parse_sina,item,overlay

class ExternalChecks(unittest.TestCase):
    def quote(self,observed='2026-10-22T09:05:00+08:00'):
        tick=int(dt.datetime.fromisoformat('2026-10-21T16:00:00-04:00').timestamp())
        p={'chart':{'result':[{'meta':{'regularMarketTime':tick,'regularMarketPrice':110,'chartPreviousClose':80},'timestamp':[tick-86400,tick], 'indicators':{'quote':[{'close':[100,110]}]}}]}}
        return p,parse_quote(p,MARKETS[0],observed)
    def test_previous_day_not_range_start(self):
        _,q=self.quote();self.assertEqual(q['previous_close'],100);self.assertAlmostEqual(q['change_pct'],10)
    def test_us_closed_after_overnight(self):
        _,q=self.quote();self.assertIn('收盘',q['session']);self.assertIn('T04:00:00+08:00',q['quote_at'])
    def test_evening_us_not_open(self):
        p,_=self.quote();tick=int(dt.datetime.fromisoformat('2026-10-20T16:00:00-04:00').timestamp());p['chart']['result'][0]['meta']['regularMarketTime']=tick
        q=parse_quote(p,MARKETS[0],'2026-10-21T20:17:00+08:00');self.assertIn('未开盘',q['session'])
    def test_future_quote_rejected(self):
        p,_=self.quote()
        with self.assertRaises(ValueError):parse_quote(p,MARKETS[0],'2026-10-21T09:05:00+08:00')
    def test_news_cutoff_and_future(self):
        now='2026-10-22T09:05:00+08:00';start='2026-10-21T20:17:00+08:00'
        for t in ['2026-10-21T19:00:00+08:00','2026-10-22T10:00:00+08:00']:
            self.assertIsNone(item('test','芯片','https://example.com',dt.datetime.fromisoformat(t),now,start))
        t=dt.datetime.fromisoformat('2026-10-22T08:00:00+08:00')
        raw=json.dumps({'result':{'data':[{'title':'芯片新闻','url':'https://example.com','ctime':str(int(t.timestamp()))}]}})
        records=parse_sina(raw,now,start);self.assertEqual(len(records),1);self.assertIn('半导体/算力',records[0]['topics'])
    def test_missing_external_not_fabricated(self):
        revision=overlay({'source_snapshot_hash':'test'},{'markets':[],'events':[]})
        self.assertEqual(revision['changes'],[]);self.assertFalse(revision['candidate_order_changed'])

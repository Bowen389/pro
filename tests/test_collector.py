import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import collector as c
import quant as q

HEADER=['代码','名称','最新价','涨幅','封单比','换手率','流通市值','总市值','首次封板','最后封板','炸板次数','连板数','行业']
def page(date='2026-10-21'):
    rows=[['进度','机率',date+' 涨停连板'],HEADER,['002929','测试','56.14','9.99%','1.32%','8.86%','1亿','1亿','09:35:57','09:38:54','1','3连板','测试']]
    return '最后更新时间：'+date+' 17:25:22<table>'+''.join('<tr>'+''.join('<td>'+x+'</td>' for x in row)+'</tr>' for row in rows)+'</table>'
class Checks(unittest.TestCase):
    def test_parse(self):self.assertEqual(c.parse(page(),'2026-10-21')['rows'][0]['streak'],3)
    def test_wrong_date(self):
        with self.assertRaises(ValueError):c.parse(page(),'2026-10-22')
    def test_missing_timestamp(self):
        with self.assertRaises(ValueError):c.parse(page().replace('最后更新时间：','不存在'),'2026-10-21')
    def test_failure_is_retained(self):
        with tempfile.TemporaryDirectory() as t:
            db=q.database(Path(t)/'db.sqlite')
            with self.assertRaises(ValueError):c.collect(db,'2026-10-22',body=page(),observed_at='2026-10-22T18:00:00+08:00')
            self.assertEqual(len(q.events(db,'collection_failure')),1);self.assertEqual(len(q.events(db,'public_snapshot')),0);db.close()
    def test_future_update_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            db=q.database(Path(t)/'db.sqlite')
            with self.assertRaises(ValueError):c.collect(db,'2026-10-21',body=page(),observed_at='2026-10-21T16:00:00+08:00')
            db.close()
if __name__=='__main__':unittest.main()

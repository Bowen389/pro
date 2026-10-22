import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from public_backtest import simulate,limit,build_signals,parse_bars,validate_config

def config():return dict(initial_cash=10000,max_positions=1,max_new_per_day=1,commission_bps=3,minimum_commission_yuan=5,sell_stamp_bps=5,slippage_bps=0,holding_sessions=2,train_end='2026-10-08',holdout_start='2026-10-09')
def bar(day,o=10,c=10,h=10.2,l=9.8):return dict(date=day,open=o,close=c,high=h,low=l,volume_lots=10000)
def signal(day='2026-10-09'):return dict(symbol='600001',name='test',signal_date=day,streak=2,close=10,ma20=9,volume_price_proxy=1e7)
class BacktestChecks(unittest.TestCase):
 def setup_data(self):
  days=['2026-10-09','2026-10-12','2026-10-13','2026-10-14'];return days,{'600001':[bar(d) for d in days]}
 def test_tplus1_costs_and_lot(self):
  days,h=self.setup_data();r=simulate([signal()],h,days,config(),'two_board','holdout');t=r['trades'][0]
  self.assertEqual(t['entry_date'],'2026-10-12');self.assertEqual(t['exit_date'],'2026-10-13');self.assertEqual(t['qty']%100,0);self.assertLess(t['net_pnl'],0)
 def test_open_at_limit_not_assumed_filled(self):
  days,h=self.setup_data();h['600001'][1]=bar(days[1],11,11,11,11)
  r=simulate([signal()],h,days,config(),'two_board','holdout');self.assertEqual(r['closed_trades'],0);self.assertEqual(r['events'][0]['reason'],'open_at_upper_limit')
 def test_limit_down_exit_deferred(self):
  days,h=self.setup_data();h['600001'][2]=bar(days[2],9,9,9,9);h['600001'][3]=bar(days[3],9,9,9.1,8.9)
  r=simulate([signal()],h,days,config(),'two_board','holdout');self.assertEqual(r['trades'][0]['exit_date'],days[3]);self.assertTrue(any(e['status']=='exit_deferred' for e in r['events']))
 def test_unresolved_not_dropped(self):
  days,h=self.setup_data();r=simulate([signal()],h,days[:2],config(),'two_board','holdout');self.assertEqual(r['unresolved_positions'],1);self.assertEqual(r['closed_trades'],0)
 def test_cannot_use_later_signal(self):
  days,h=self.setup_data();r=simulate([signal(days[-1])],h,days,config(),'two_board','holdout');self.assertEqual(r['closed_trades'],0);self.assertEqual(r['pending_signal_count'],1)
 def test_train_never_uses_holdout_signal(self):
  days,h=self.setup_data();r=simulate([signal()],h,days,config(),'two_board','train');self.assertEqual(r['closed_trades'],0);self.assertEqual(r['events'],[])
 def test_calendar_gap_not_skipped_to_future_stock_open(self):
  days,h=self.setup_data();h['600001'].pop(1);r=simulate([signal()],h,days,config(),'two_board','holdout');self.assertEqual(r['closed_trades'],0);self.assertEqual(r['events'][0]['reason'],'missing_or_suspended_entry')
 def test_pool_price_mismatch_rejects_entire_date(self):
  pools={'2026-10-09':{'status':'ok','data':{'provider_qdate':'20261022','rows':[{'c':'600001','n':'test','p':20000,'lbc':2}]}}}
  signals,audit=build_signals(pools,{'600001':[bar('2026-10-09')]},config());self.assertEqual(signals,[]);self.assertEqual(audit[0]['status'],'rejected_incomplete_or_mismatched')
 def test_reject_same_day_exit_and_negative_cost(self):
  c=config();c['holding_sessions']=1
  with self.assertRaises(ValueError):validate_config(c)
  c=config();c['commission_bps']=-1
  with self.assertRaises(ValueError):validate_config(c)
 def test_tick_rounding_half_up(self):self.assertEqual(limit(10.05,1.1),11.06)
 def test_no_future_bars_and_duplicate_dates(self):
  p={'data':{'sh600001':{'day':[['2026-10-01','10','10','11','9','1']]*60}}}
  with self.assertRaises(ValueError):parse_bars(p,'sh600001','2026-10-22')
if __name__=='__main__':unittest.main()

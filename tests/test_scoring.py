import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from scoring import score, ranked

class ScoringTests(unittest.TestCase):
    def row(self, **kw):
        return dict(symbol='000001', first_limit_time='09:25:00', break_count=0, seal_ratio_pct=7.39, streak=3)|kw
    def test_original_formula(self):
        for time in ['09:25:00','10:00:00','11:30:00','14:00:00']:
            for breaks in [0,4,15,20]:
                for seal in [0,.32,5,7.39]:
                    for streak in [1,2,3,5]:
                        expected=round((20 if time<'10:00:00' else 10 if time<'11:30:00' else 0)+max(0,15-min(breaks,15))+min(seal,5)*2+(10 if streak in (1,2,3) else 0),2)
                        self.assertEqual(score(self.row(first_limit_time=time,break_count=breaks,seal_ratio_pct=seal,streak=streak))['research_score'],expected)
    def test_missing_not_zero(self):
        for kw in [dict(seal_ratio_pct=None),dict(break_count=-1),dict(seal_ratio_pct=float('nan')),dict(first_limit_time='bad')]:
            self.assertIsNone(score(self.row(**kw))['research_score'])
    def test_height_does_not_silently_change_baseline(self):
        rows=ranked([self.row(symbol='000002',streak=5),self.row()])
        self.assertEqual([r['research_score'] for r in rows],[55,45])
        self.assertEqual(rows[0]['score_components']['low_board_bonus'],10)

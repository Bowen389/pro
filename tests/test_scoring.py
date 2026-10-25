import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from scoring import score,DIMENSIONS

class ScoringTests(unittest.TestCase):
    def evidence(self,v=7):
        return dict(value=v,basis='公告已披露的业务收入，研究判断而非收益概率',source='https://example.com/notice',observed_at='2026-10-23T15:00:00+08:00')
    def test_missing_never_zero_or_normalized(self):
        r=score({}, {'逻辑硬度':self.evidence()},'2026-10-23T16:00:00+08:00')
        self.assertIsNone(r['research_score']);self.assertEqual(r['score_coverage'],1)
        self.assertEqual(r['score_observed_max'],10);self.assertEqual(r['score_subtotal'],7)
        self.assertIsNone(r['score_components']['资金强度']['value'])
    def test_complete_sourced_card(self):
        r=score({}, {k:self.evidence() for k in DIMENSIONS},'2026-10-23T16:00:00+08:00')
        self.assertEqual(r['research_score'],70);self.assertEqual(r['score_max'],100)
    def test_invalid_or_future_evidence_rejected(self):
        cases=[self.evidence(11),self.evidence(float('nan')),self.evidence()|{'source':''},self.evidence()|{'basis':''},self.evidence()|{'observed_at':'2026-10-24T15:00:00+08:00'},self.evidence()|{'observed_at':'yesterday'}]
        for e in cases:self.assertEqual(score({}, {'逻辑硬度':e},'2026-10-23T16:00:00+08:00')['score_coverage'],0)
    def test_explicit_zero_is_scored(self):
        r=score({}, {'逻辑硬度':self.evidence(0)},'2026-10-23T16:00:00+08:00')
        self.assertEqual(r['score_coverage'],1);self.assertEqual(r['score_subtotal'],0)

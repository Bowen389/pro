import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import model
import quant as q
from test_quant import fixture


class ModelChecks(unittest.TestCase):
    def test_mock_transport_and_invalid_reference(self):
        with tempfile.TemporaryDirectory() as t:
            db=q.database(Path(t)/'db.sqlite')
            key,_=q.build_plan(db,fixture(),'2025-03-31T17:00:00+08:00','2025-04-01')
            def send(payload):
                self.assertFalse(payload['store'])
                return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'statements':[{'kind':'hypothesis','text':'需验证','evidence_ids':[key]}],'experiments':[]})}}],'model':'mock','usage':{}}
            proposal,_=model.analyze(db,key,'mock',api_key='test-only',send=send)
            self.assertTrue(proposal)
            db.close()


if __name__=='__main__':unittest.main()

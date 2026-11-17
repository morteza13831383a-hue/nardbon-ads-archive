import os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import source_budget

class MirrorBudgetStopTests(unittest.TestCase):
    def test_one_mirror_access_block_pauses_only_its_host(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'CRAWL_BUDGET_DIR':temp}):
            source_budget.report('https://tgads.biz',403)
            self.assertGreater(source_budget.status('https://tgads.biz')['pause_left_s'],0)
            self.assertFalse(source_budget.status('https://tgads.biz')['halted'])
            self.assertFalse(source_budget.status('https://telegramadsarchive.com')['halted'])

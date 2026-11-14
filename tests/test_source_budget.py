import hashlib,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import source_budget as b
class BudgetTests(unittest.TestCase):
    def test_future_timestamp_after_environment_restart_does_not_stall(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/(hashlib.sha256(b'tgstat.com').hexdigest()[:16]+'.lock')
            p.write_text('50000')
            with patch.dict(os.environ,{'CRAWL_BUDGET_DIR':tmp}),patch.object(b.time,'monotonic',return_value=100),patch.object(b.time,'sleep') as sleep,patch.object(b.random,'uniform',return_value=0):
                b.reserve('https://tgstat.com',4)
                sleep.assert_called_once_with(0)
            self.assertEqual(p.read_text(),'100')

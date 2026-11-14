import json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import source_budget as b


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'CRAWL_BUDGET_DIR':self.tmp.name}); self.env.start()
    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def state(self,base):
        return json.loads(b._paths(base)[1].read_text())

    def test_future_timestamp_after_environment_restart_does_not_stall(self):
        _,path=b._paths('https://tgstat.com')
        path.write_text(json.dumps({'delay':4,'streak':0,'last':10**12,'pause_until':0}))
        with patch.object(b.time,'time',return_value=100.0),patch.object(b.time,'sleep') as sleep,patch.object(b.random,'uniform',return_value=0):
            b.reserve('https://tgstat.com',4)
        self.assertLessEqual(sleep.call_args[0][0],4.0)        # waits one normal gap, not until 10**12

    def test_rate_limit_pauses_everyone_and_doubles_the_delay(self):
        with patch.object(b.time,'time',return_value=1000.0):
            b.report('https://tgadsspy.com',429)
        s=self.state('https://tgadsspy.com')
        self.assertEqual(s['delay'],8.0)
        self.assertEqual(s['pause_until'],1000.0+b.BLOCK_PAUSE)
        with patch.object(b.time,'time',return_value=1000.0),patch.object(b.time,'sleep') as sleep,patch.object(b.random,'uniform',return_value=0):
            b.reserve('https://tgadsspy.com',1)
        self.assertGreaterEqual(sleep.call_args[0][0],b.BLOCK_PAUSE)

    def test_clean_streak_speeds_up_but_not_below_the_floor(self):
        for _ in range(b.SPEEDUP_EVERY*20):
            b.report('https://tgadsspy.com',200)
        self.assertEqual(self.state('https://tgadsspy.com')['delay'],b.MIN_DELAY)

    def test_account_host_keeps_a_higher_floor(self):
        for _ in range(b.SPEEDUP_EVERY*20):
            b.report('https://tgstat.com',200)
        self.assertEqual(self.state('https://tgstat.com')['delay'],4.0)

    def test_challenge_page_counts_as_a_block(self):
        b.report('https://tgadsspy.com','challenge')
        self.assertEqual(self.state('https://tgadsspy.com')['delay'],8.0)


if __name__=='__main__':
    unittest.main()

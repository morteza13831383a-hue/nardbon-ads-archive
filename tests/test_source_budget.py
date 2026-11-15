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
        self.assertTrue(not sleep.called or sleep.call_args[0][0]<=4.0)        # waits one normal gap, not until 10**12

    def test_rate_limit_pauses_everyone_and_doubles_the_delay(self):
        with patch.object(b.time,'time',return_value=1000.0):
            b.report('https://tgadsspy.com',429)
        s=self.state('https://tgadsspy.com')
        self.assertEqual(s['delay'],8.0)
        self.assertEqual(s['pause_until'],1000.0+b.BLOCK_PAUSE)
        clock=[1000.0]
        def advance(seconds):clock[0]+=seconds
        with patch.object(b.time,'time',side_effect=lambda:clock[0]),patch.object(b.time,'sleep',side_effect=advance)as sleep,patch.object(b.random,'uniform',return_value=0):
            b.reserve('https://tgadsspy.com',1)
        self.assertGreaterEqual(clock[0],1000+b.BLOCK_PAUSE)
        self.assertTrue(all(c.args[0]<=30 for c in sleep.call_args_list))

    def test_clean_streak_speeds_up_but_not_below_the_floor(self):
        for _ in range(b.SPEEDUP_EVERY*20):
            b.report('https://tgadsspy.com',200)
        self.assertEqual(self.state('https://tgadsspy.com')['delay'],b.MIN_DELAY)

    def test_account_host_keeps_a_higher_floor(self):
        for _ in range(b.SPEEDUP_EVERY*20):
            b.report('https://tgstat.com',200)
        self.assertEqual(self.state('https://tgstat.com')['delay'],15.0)

    def test_challenge_page_counts_as_a_block(self):
        b.report('https://tgadsspy.com','challenge')
        self.assertEqual(self.state('https://tgadsspy.com')['delay'],8.0)

    def test_third_block_inside_six_hours_latches_host_stop(self):
        with patch.object(b.time,'time',return_value=1000):
            b.report('https://tgstat.com',403);b.report('https://ir.tgstat.com',403)
            with self.assertRaises(b.HostBlocked):b.report('https://tgstat.com',429)
        self.assertTrue(self.state('https://tgstat.com')['halted'])
        with self.assertRaises(b.HostBlocked):b.reserve('https://ir.tgstat.com',4)

    def test_old_blocks_do_not_count_against_new_six_hour_window(self):
        with patch.object(b.time,'time',return_value=1000):b.report('https://tgadsspy.com',403)
        with patch.object(b.time,'time',return_value=1000+7*3600):b.report('https://tgadsspy.com',403)
        self.assertEqual(len(self.state('https://tgadsspy.com')['blocks']),1)


class AccountHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'CRAWL_BUDGET_DIR':self.tmp.name}); self.env.start()
    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def test_captcha_on_account_host_halts_at_once(self):
        self.assertTrue(b.is_challenge('<div class="g-recaptcha"></div>'))
        with self.assertRaises(b.HostBlocked):
            b.report('https://ir.tgstat.com','challenge')
        with self.assertRaises(b.HostBlocked):
            b.reserve('https://tgstat.com',1)

    def test_daily_cap_waits_for_next_day(self):
        _,path=b._paths('https://tgstat.com')
        path.write_text(json.dumps({'delay':15,'streak':0,'last':0,'pause_until':0,'blocks':[],'halted':False,
                                    'reported_requests':0,'day':'2026-11-15','day_count':1500}))
        waits=[]
        clock=[1794700800.0 + 3600]                  # 2026-11-15 01:00 UTC
        def sleep(sec): waits.append(sec); clock[0]+=sec
        with patch.object(b.time,'time',side_effect=lambda:clock[0]),patch.object(b.time,'sleep',side_effect=sleep),patch.object(b.random,'uniform',return_value=0):
            b.reserve('https://tgstat.com',1)
        self.assertGreater(sum(waits), 3600)          # slept until the next UTC day


if __name__=='__main__':
    unittest.main()

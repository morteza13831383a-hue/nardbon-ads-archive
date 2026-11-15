import json,sqlite3,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import collection_ownership as own
import collection_supervisor as supervisor

class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.work=self.root/'ads';self.work.mkdir();self.state=self.root/'state';self.state.mkdir()
        self.cfg={'code':str(self.root),'state_dir':str(self.state),'tgadsspy_budget_policy':own.POLICY,'ads_work':str(self.work),'ads_process_file':str(self.root/'ad-process.json'),'ads_command':['python','archive_collect.py'],'spy_work':str(self.root/'channels'),'tg_work':str(self.root/'tgstat'),'python':'python','repo':'owner/repo','cookie_file':str(self.root/'missing-private-cookie')}
        (self.root/'channels').mkdir();(self.root/'tgstat').mkdir()
        self.db=sqlite3.connect(self.work/'checkpoint.sqlite3');self.db.executescript('CREATE TABLE queue(kind TEXT,status TEXT);CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE records(id INTEGER PRIMARY KEY,payload TEXT);CREATE TABLE github_delivered(id INTEGER PRIMARY KEY);INSERT INTO queue VALUES("detail","iran");')
        self.db.executemany('INSERT INTO records VALUES(?,?)',[(i,'{}')for i in range(10)]);self.db.executemany('INSERT INTO github_delivered VALUES(?)',[(i,)for i in range(10)]);self.db.commit()
        own.save_state(self.cfg,{'owner':'ads','changed_at':time.time()-3600,'ads_saved_at_change':10,'before_ads_per_hour':29,'before_window_seconds':3600})
        self.budget=patch.object(own,'status',return_value={'halted':False,'pause_left_s':0});self.budget.start();self.active=patch.object(own,'active_ads',return_value=[]);self.proc=self.active.start()
    def tearDown(self):self.active.stop();self.budget.stop();self.db.close();self.tmp.cleanup()
    def pending(self,kind):self.db.execute('INSERT INTO queue VALUES(?,"pending")',(kind,));self.db.commit()
    def test_every_pending_job_including_sitemap_and_listing_defers_handoff(self):
        for kind in('detail','sitemap','listing'):
            self.pending(kind);self.assertEqual(own.advance(self.cfg)['owner'],'ads');self.db.execute("DELETE FROM queue WHERE status='pending'");self.db.commit()
    def test_drained_checkpoint_switches_once_then_stays_with_channels(self):
        self.assertEqual(own.advance(self.cfg)['owner'],'channels');self.pending('detail');self.assertEqual(own.advance(self.cfg)['owner'],'channels')
    def test_source_holds_preserve_ad_ownership_and_failed_rows_remain_reported(self):
        self.db.execute('INSERT INTO meta VALUES("last_stop_reason","HTTP 403")');self.db.commit();self.assertEqual(own.advance(self.cfg)['owner'],'ads')
        self.db.execute('DELETE FROM meta');self.db.execute('INSERT INTO queue VALUES("detail","failed")');self.db.commit();result=own.advance(self.cfg);self.assertEqual(result['owner'],'channels');self.assertEqual(result['archive']['unresolved'],{'failed':1})
    def test_active_orphan_slice_and_terminal_host_halt_prevent_switch(self):
        self.proc.return_value=[123];self.assertEqual(own.advance(self.cfg)['owner'],'ads');self.proc.return_value=[]
        with patch.object(own,'status',return_value={'halted':True,'pause_left_s':0}):self.assertEqual(own.advance(self.cfg)['owner'],'ads')
    def test_missing_checkpoint_does_not_look_complete(self):
        self.cfg['ads_work']=str(self.root/'missing');self.assertEqual(own.advance(self.cfg)['owner'],'ads')
    def test_rate_counts_new_records_and_freezes_after_handoff(self):
        state=own.load_state(self.cfg);state['changed_at']=1000;own.save_state(self.cfg,state)
        self.db.execute('INSERT INTO records VALUES(100,"{}")');self.db.execute('INSERT INTO github_delivered VALUES(100)');self.db.commit()
        with patch.object(own.time,'time',return_value=4600):
            self.assertEqual(own.describe(self.cfg)['after_ads_per_hour'],1);self.assertEqual(own.advance(self.cfg)['owner'],'channels')
        with patch.object(own.time,'time',return_value=8200):self.assertEqual(own.describe(self.cfg)['after_ads_per_hour'],1)
    def test_supervisor_stops_channel_and_leaves_tgstat_alone_during_ads(self):
        self.pending('sitemap');self.proc.return_value=[999]
        meta=Path(self.cfg['spy_work'])/'process.json';meta.write_text(json.dumps({'pid':123,'command':['python','channels_html.py']}))
        with patch.object(supervisor,'alive',return_value=True),patch.object(supervisor.os,'kill')as kill,patch.object(supervisor.subprocess,'Popen')as spawn:
            supervisor.ensure(self.cfg);kill.assert_called_once();self.assertEqual(kill.call_args.args[0],123);spawn.assert_not_called()
    def test_supervisor_resumes_channel_only_after_ad_checkpoint_drains(self):
        with patch.object(supervisor,'alive',return_value=False),patch.object(supervisor,'status',return_value={'halted':False}),patch.object(supervisor.subprocess,'Popen')as spawn:
            spawn.return_value.pid=123;supervisor.ensure(self.cfg)
            self.assertEqual(spawn.call_count,1);self.assertTrue(spawn.call_args.args[0][1].endswith('channels_html.py'))

    def test_undelivered_ads_prevent_early_handoff(self):
        self.db.execute('DELETE FROM github_delivered WHERE id=0');self.db.commit()
        self.assertEqual(own.advance(self.cfg)['owner'],'ads')
        self.db.execute('INSERT INTO github_delivered VALUES(0)');self.db.commit()
        self.assertEqual(own.advance(self.cfg)['owner'],'channels')

    def test_supervisor_restarts_missing_ad_worker_with_secrets_excluded(self):
        self.pending('detail')
        with patch.object(supervisor,'alive',return_value=False),patch.object(supervisor,'status',return_value={'halted':False}),patch.object(supervisor.subprocess,'Popen')as spawn,patch.dict(supervisor.os.environ,{'TGSTAT_COOKIE':'TEST_SECRET','TGSTAT_COOKIE_FILE':'/test/private'}):
            spawn.return_value.pid=123;supervisor.ensure(self.cfg)
            self.assertEqual(spawn.call_count,1);self.assertEqual(spawn.call_args.args[0],self.cfg['ads_command'])
            self.assertNotIn('TGSTAT_COOKIE',spawn.call_args.kwargs['env']);self.assertNotIn('TGSTAT_COOKIE_FILE',spawn.call_args.kwargs['env'])
    def test_terminal_host_halt_prevents_ad_restart(self):
        self.pending('detail')
        with patch.object(supervisor,'alive',return_value=False),patch.object(supervisor,'status',return_value={'halted':True}),patch.object(supervisor.subprocess,'Popen')as spawn:
            supervisor.ensure(self.cfg);spawn.assert_not_called()

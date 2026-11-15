import base64,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import channels_html as spy
import channel_slices as slices
import tgstat_collect as tg
import tgstat_listing as listing
from html_core import Document
from tgstat_session import Session

class FinalCollectionTests(unittest.TestCase):
    def test_robots_disallowed_slice_does_not_crash_or_retry_forever(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=spy.connect(Path(tmp));slices.ensure(spy,db)
            class Denied:
                def get(self,url):raise ValueError('robots.txt disallows: '+url)
            self.assertTrue(slices.step(spy,db,Denied()))
            self.assertEqual(db.execute("select count(*) from spy_slices where status='robots_disallowed'").fetchone()[0],1)
            self.assertEqual(db.execute('select count(*) from requests').fetchone()[0],0)
            db.close()

    def test_tgstat_list_rows_save_members_without_profile(self):
        body='<div class="peer-item-box"><a href="/channel/@Example"><div class="font-16">کانال نمونه</div><div class="line-clamp-2">شرح افغانستان</div><div><b>12 345</b> subscribers</div></a></div><div class="peer-item-box"><a href="/chat/private-key"><div class="font-16">گروه</div><b>77</b> participants</a></div>'
        rows=listing.parse_rows(Document(body),'news','channel')
        self.assertEqual(rows['example']['members'],12345)
        with tempfile.TemporaryDirectory()as tmp:
            db=tg.connect(Path(tmp));listing.ensure(db);self.assertEqual(listing.save_rows(tg,db,rows),1)
            self.assertEqual(json.loads(db.execute('select payload from records').fetchone()[0])['record_detail'],'listing')
            private=listing.parse_rows(Document(body),'news','chat');self.assertIsNone(next(iter(private.values()))['username'])
            listing.save_rows(tg,db,private);self.assertEqual(db.execute('select count(*)from records').fetchone()[0],2);db.close()
    def test_csrf_is_derived_locally_without_any_profile_get(self):
        token='TEST_TOKEN_012345678901234567890'
        self.assertEqual(len(token),32)
        raw=json.dumps([{'domain':'.tgstat.com','name':'_tgstat_csrk','value':'signed:2:{i:1;s:32:"'+token+'";}','session':True,'secure':True}])
        with patch.dict(os.environ,{'TGSTAT_COOKIE':raw}):s=Session.from_environment()
        self.assertTrue(s.csrf_from_cookie());decoded=base64.urlsafe_b64decode(s.csrf+'='*((-len(s.csrf))%4));size=len(decoded)//2
        self.assertEqual(bytes(a^b for a,b in zip(decoded[:size],decoded[size:])),token.encode())
    def test_tgstat_cap_schedules_other_sort_not_an_account_retry(self):
        with tempfile.TemporaryDirectory()as tmp:
            db=tg.connect(Path(tmp));db.execute("insert into categories values('news','News')");listing.ensure(db)
            job=listing.next_slice(db);listing.capped(db,job,'repeated')
            self.assertEqual(db.execute("select count(*)from tgstat_slices where sort='ci'").fetchone()[0],1);db.close()
    def test_saved_spy_username_never_enters_profile_work(self):
        with tempfile.TemporaryDirectory()as tmp:
            db=spy.connect(Path(tmp));slices.ensure(spy,db)
            row={'username':'example','title':'Example','members':0,'avg_views':0,'metrics':{},'classification':'persian_candidate','source_language_tags':['fa'],'source_url':spy.BASE+'/channels?lang=fa'}
            spy.enqueue(db,'/channels/example','channel',0)
            self.assertEqual(slices.persist(spy,db,[row]),1);self.assertEqual(slices.persist(spy,db,[row]),0)
            self.assertEqual(db.execute("select status from queue where url=?",(spy.BASE+'/channels/example',)).fetchone()[0],'listing_saved');db.close()
    def test_slices_keep_category_and_sort_and_detect_ignored_bounds(self):
        self.assertIn('category=news',slices.url(spy,{'category':'news','sort':'new'},3))
        with tempfile.TemporaryDirectory()as tmp:
            db=spy.connect(Path(tmp));slices.ensure(spy,db)
            k=slices.add(db,{'minMembers':'0','maxMembers':'100'})
            html='<table><tr><th>Channel</th><th>Members</th><th>Avg. views</th></tr><tr><td><a href="/channels/example">Example @example</a></td><td>100000</td><td>25</td></tr></table><a href="/channels?lang=fa&page=2">Next</a>'
            raw=db.execute('select filters from spy_slices where key=?',(k,)).fetchone()[0];slices.inspect_result(spy,db,(k,raw,1),Document(html))
            self.assertEqual(db.execute("select verdict from spy_controls where name='maxMembers'").fetchone()[0],'ignored');db.close()
    def test_post_descriptions_do_not_masquerade_as_account_notices(self):
        from tgstat_session import account_problem
        d=Document('<div class="peer-item-box">Your account is limited — channel topic</div>')
        self.assertIsNone(account_problem(d,fragment=True))
        self.assertIsNotNone(account_problem(Document('<div class="alert alert-warning">Account limit exceeded</div>'),fragment=True))
    def test_unknown_usernames_are_retained_in_import_database(self):
        import channel_import,channels,gzip
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);(root/'data').mkdir();folder=root/'data'/'tgstat-import';folder.mkdir()
            with gzip.open(folder/'test.jsonl.gz','wt')as h:h.write(json.dumps({'type':'group','username':None,'source_id':'private-identity','title':'گروه افغانستان','record_detail':'listing'})+'\n')
            with patch.object(channels,'ROOT',root),patch.object(channel_import,'TGSTAT_DIR',folder):
                n=channel_import.import_tgstat({}, {},lambda _:None,lambda:'2026-11-14T00:00:00Z')
            self.assertEqual(n,1);self.assertTrue((root/'data'/'tgstat-entities.json').exists())
    def test_final_csrf_bootstrap_never_fetches_an_ordinary_profile(self):
        from tgstat_session import Session
        from email.message import Message
        token='TEST_TOKEN_012345678901234567890'
        raw=json.dumps([{'domain':'.tgstat.com','name':'_tgstat_csrk','value':'signed:2:{i:1;s:32:"'+token+'";}','session':True,'secure':True}])
        class Response:
            status=200
            def __init__(self):self.headers=Message();self.headers['Content-Type']='application/json'
            def info(self):return self.headers
            def read(self,*args):return b'{"status":"ok","html":"","hasMore":false,"nextPage":1,"nextOffset":0}'
            def __enter__(self):return self
            def __exit__(self,*args):pass
        with tempfile.TemporaryDirectory()as tmp,patch.dict(os.environ,{'TGSTAT_COOKIE':raw,'CRAWL_BUDGET_DIR':tmp}):
            db=tg.connect(Path(tmp));db.execute("insert into categories values('news','News')");db.commit();c=tg.Client(db,session=Session.from_environment(),final_plan=True);c.rules=[tg.Robots('User-agent: *\nAllow: /')]
            with patch.object(tg,'reserve'),patch.object(c.session_opener,'open',return_value=Response())as opened,patch.object(c.opener,'open',side_effect=AssertionError('Unexpected anonymous/profile bootstrap')):
                c.post_catalog('news','channel',0,0)
                self.assertEqual(opened.call_count,1);self.assertEqual(opened.call_args.args[0].get_method(),'POST')
            db.close();tg._SECRET_FILTER=None
    def test_captcha_error_halts_the_account_host(self):
        from email.message import Message
        from tgstat_session import Session
        from source_budget import Challenge
        raw=json.dumps([{'domain':'.tgstat.com','name':'tgstat_sirk','value':'TEST_SESSION_FOR_CAPTCHA_ONLY','session':True,'secure':True}])
        class Response:
            status=200
            def __init__(self):self.headers=Message();self.headers['Content-Type']='application/json'
            def info(self):return self.headers
            def read(self,*args):return b'{"status":"error","error":"captcha"}'
            def __enter__(self):return self
            def __exit__(self,*args):pass
        with tempfile.TemporaryDirectory()as tmp,patch.dict(os.environ,{'TGSTAT_COOKIE':raw,'CRAWL_BUDGET_DIR':tmp}):
            db=tg.connect(Path(tmp));db.execute("insert into categories values('news','News')");db.commit();s=Session.from_environment();s.csrf='TEST_CSRF';s.csrf_param='_tgstat_csrk';c=tg.Client(db,session=s,final_plan=True);c.rules=[tg.Robots('User-agent: *\nAllow: /')]
            with patch.object(tg,'reserve'),patch.object(c.session_opener,'open',return_value=Response()):
                # policy (Nov 15): a captcha on the user's own account halts TGStat until a person
                # clears it in the browser; it is no longer a 30-minute cooldown
                from source_budget import HostBlocked
                with self.assertRaises((Challenge,HostBlocked)):c.post_catalog('news','channel',0,0)
            st=__import__('source_budget').status('https://tgstat.com')
            self.assertEqual(st['blocks_6h'],1);self.assertTrue(st['halted']);db.close();tg._SECRET_FILTER=None

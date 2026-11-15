import contextlib,gzip,io,json,logging,sqlite3,sys,tempfile,unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import tgstat_collect as t
from tgstat_session import Session,endpoint_allowed,account_problem
from html_core import Document

SENTINEL='DUMMY_SESSION_0123456789abcdef'
CSRF='DUMMY_CSRF_FOR_TEST_ONLY_b3db4b28'
ROTATED='DUMMY_ROTATED_COOKIE_FOR_TEST_ONLY_a9da'
def cookie_json(domain='.tgstat.com'):
    return json.dumps([{'domain':domain,'name':'tgstat_sirk','value':SENTINEL,'path':'/','secure':True,'session':True,'hostOnly':False}])
def session():
    with patch.dict('os.environ',{'TGSTAT_COOKIE':cookie_json()}):return Session.from_environment()
class Response:
    status=200
    def __init__(self,body,kind='text/html',rotated=False):
        self.body=body.encode();self.headers=Message();self.headers['Content-Type']=kind
        if rotated:self.headers['Set-Cookie']='tgstat_sirk='+ROTATED+'; Domain=.tgstat.com; Path=/; Secure'
    def info(self):return self.headers
    def read(self,*args):return self.body
    def __enter__(self):return self
    def __exit__(self,*args):pass

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.budget=tempfile.TemporaryDirectory();self.budget_env=patch.dict('os.environ',{'CRAWL_BUDGET_DIR':self.budget.name});self.budget_env.start()
    def tearDown(self):
        t._SECRET_FILTER=None;self.budget_env.stop();self.budget.cleanup()
    def test_cookie_is_environment_only_and_domain_validated(self):
        with patch.dict('os.environ',{'TGSTAT_COOKIE':'','TGSTAT_COOKIE_FILE':''}):
            with self.assertRaisesRegex(ValueError,'not configured'):Session.from_environment()
        with patch.dict('os.environ',{'TGSTAT_COOKIE':cookie_json('.tgadsspy.com')}):
            with self.assertRaises(ValueError)as error:Session.from_environment()
            self.assertNotIn(SENTINEL,str(error.exception))
        for url,method in [('https://tgadsspy.com/channel/@example','GET'),('https://tgstat.com.evil.test/news/items','POST'),(t.BASE+'/news','GET'),(t.BASE+'/my/settings','POST'),(t.BASE+'/channel/@example/stat','GET'),(t.BASE+'/news/items','GET')]:
            self.assertFalse(endpoint_allowed(url,method))
    def test_cookie_only_reaches_approved_requests(self):
        with tempfile.TemporaryDirectory()as tmp:
            db=t.connect(Path(tmp));t.enqueue(db,'/channel/@example');db.execute("INSERT INTO categories VALUES('news','News')");db.commit()
            c=t.Client(db,session=session());c.rules=[t.Robots('User-agent: *\nAllow: /')]
            c.session.csrf=CSRF;c.session.csrf_param='_tgstat_csrk'
            sent=[]
            def opened(req,**kwargs):
                sent.append((req.full_url,req.get_method(),req.get_header('Cookie'),req.data))
                return Response('<h1>Public profile</h1>')if req.get_method()=='GET'else Response(json.dumps({'status':'ok','html':'','hasMore':False,'nextPage':1,'nextOffset':0}),'application/json')
            with patch.object(t,'reserve'),patch.object(c.opener,'open',side_effect=opened),patch.object(c.session_opener,'open',side_effect=opened):
                c.get(t.BASE+'/robots.txt',robots=True);c.get(t.BASE+'/news');c.get(t.BASE+'/channel/@example');c.post_catalog('news','chat',0,0)
                with self.assertRaises(ValueError):c.get('https://tgadsspy.com/channel/@example')
            self.assertIsNone(sent[0][2]);self.assertIsNone(sent[1][2]);self.assertIn(SENTINEL,sent[2][2]);self.assertIn(SENTINEL,sent[3][2])
            self.assertEqual(parse_qs(sent[3][3].decode())['peer_type'],['chat']);db.close()
    def test_redirect_cannot_forward_session_to_another_host(self):
        with tempfile.TemporaryDirectory()as tmp:
            db=t.connect(Path(tmp));t.enqueue(db,'/channel/@example');db.commit();c=t.Client(db,session=session());c.rules=[t.Robots('User-agent: *\nAllow: /')]
            with patch.object(t,'reserve'),patch.object(c.session_opener,'open',side_effect=HTTPError(t.BASE,302,'redirect',{'Location':'https://tgadsspy.com/'},None))as opened:
                with self.assertRaises(t.AccessStop):c.get(t.BASE+'/channel/@example')
                self.assertEqual(opened.call_count,1)
            db.close()
    def test_logout_warning_and_http_limits_stop_without_retry(self):
        for body in ('<p>Sign in to access this page</p>','<h1>Profile</h1><a data-src="/login">Sign In</a>','<div class="alert alert-warning">Your account has been limited</div>'):
            with tempfile.TemporaryDirectory()as tmp:
                db=t.connect(Path(tmp));t.enqueue(db,'/channel/@example');db.commit();c=t.Client(db,session=session());c.rules=[t.Robots('User-agent: *\nAllow: /')]
                with patch.object(t,'reserve'),patch.object(c.session_opener,'open',return_value=Response(body)):
                    with self.assertRaises(t.AccessStop):c.get(t.BASE+'/channel/@example')
                db.close()
        for status in(401,403,429):
            with tempfile.TemporaryDirectory()as tmp:
                db=t.connect(Path(tmp));t.enqueue(db,'/channel/@example');db.commit();c=t.Client(db,session=session());c.rules=[t.Robots('User-agent: *\nAllow: /')]
                with patch.object(t,'reserve'),patch.object(c.session_opener,'open',side_effect=HTTPError(t.BASE,status,'denied',{},None))as opened:
                    with self.assertRaises((t.AccessStop,__import__('source_budget').HostBlocked)):c.get(t.BASE+'/channel/@example')
                    self.assertEqual(opened.call_count,1)
                db.close()
    def test_private_iran_profile_can_redirect_only_to_same_type_public_profile(self):
        import tgstat_listing as listing
        for target,accepted in [(t.BASE+'/channel/@example',True),(t.BASE+'/chat/@example',False),
                                (t.BASE+'/login',False),('https://tgstat.com/channel/@example',False),
                                (t.BASE+'/channel/@example?login=1',False)]:
            with tempfile.TemporaryDirectory() as tmp:
                db=t.connect(Path(tmp));listing.ensure(db)
                source=t.BASE+'/channel/private123'
                db.execute('INSERT INTO tgstat_unresolved(url,payload) VALUES(?,?)',(source,'{}'));db.commit()
                c=t.Client(db,session=session());c.rules=[t.Robots('User-agent: *\nAllow: /')]
                effects=[HTTPError(source,302,'redirect',{'Location':target},None),Response('<h1>Channel</h1>')]
                with patch.object(t,'reserve'),patch.object(c.session_opener,'open',side_effect=effects) as opened:
                    if accepted:self.assertIn('Channel',c.get(source))
                    else:
                        with self.assertRaises(t.AccessStop):c.get(source)
                    self.assertEqual(opened.call_count,2 if accepted else 1)
                self.assertFalse(c.known_profile(target))
                db.close()

    def test_cookie_csrf_and_rotated_cookie_never_reach_exports_or_logs(self):
        body=f'<meta name="csrf-token"content="{CSRF}"><meta name="csrf-param"content="_tgstat_csrk"><h1>Title {SENTINEL}</h1><p class="card-text">Bio {SENTINEL} {CSRF} {ROTATED}</p><div class="post-text">Post {SENTINEL}</div>'
        with tempfile.TemporaryDirectory()as tmp:
            work=Path(tmp);db=t.connect(work);db.execute("DELETE FROM queue");t.enqueue(db,'/channel/@example');db.commit();c=t.Client(db,session=session());c.rules=[t.Robots('User-agent: *\nAllow: /')]
            capture=io.StringIO();handler=logging.StreamHandler(capture);logging.getLogger().addHandler(handler);old_level=logging.getLogger().level;logging.getLogger().setLevel(logging.DEBUG)
            try:
                with contextlib.redirect_stdout(capture),contextlib.redirect_stderr(capture),patch('http.cookiejar.debug',True),patch.object(t,'reserve'),patch.object(c.session_opener,'open',return_value=Response(body,rotated=True)):
                    t.run(db,c,1)
                    db.execute("INSERT INTO categories VALUES('news','News')");t.seed_catalog_pages(db)
                    payload=json.dumps({'status':'ok','html':f'<a href="/channel/@{SENTINEL}">Reflected</a>','hasMore':False,'nextPage':1,'nextOffset':0})
                    with patch.object(c.session_opener,'open',return_value=Response(payload,'application/json')):t.run_catalog_page(db,c)
                    t.enqueue(db,'/channel/@'+SENTINEL)
                    t.export(db,work)
            finally:logging.getLogger().removeHandler(handler);logging.getLogger().setLevel(old_level)
            dump='\n'.join(db.iterdump());db.close()
            artifacts=[capture.getvalue(),dump]
            for path in(work/'exports').iterdir():
                artifacts.append(gzip.decompress(path.read_bytes()).decode()if path.suffix=='.gz'else path.read_text())
            for value in artifacts:
                for secret in(SENTINEL,CSRF,ROTATED,cookie_json()):self.assertNotIn(secret,value)
            self.assertIn('[redacted]',dump)
    def test_server_cursors_resume_and_depth_end_is_recorded(self):
        with tempfile.TemporaryDirectory()as tmp:
            db=t.connect(Path(tmp));db.execute("INSERT INTO categories VALUES('news','News')");t.seed_catalog_pages(db)
            # Keep one entity type for this pagination test.
            db.execute("DELETE FROM catalog_pages WHERE peer_type='chat'");db.commit()
            class Fake:
                def __init__(self):self.calls=[]
                def post_catalog(self,cat,peer,page,offset):
                    self.calls.append((page,offset));name='example'if page==0 else'second'
                    return json.dumps({'status':'ok','html':f'<a href="/channel/@{name}/stat">Channel</a>','hasMore':page==0,'nextPage':7,'nextOffset':123})
            c=Fake();self.assertTrue(t.run_catalog_page(db,c));self.assertTrue(t.run_catalog_page(db,c));self.assertEqual(c.calls,[(0,0),(7,123)])
            coverage=t.catalog_coverage(db)[0];self.assertTrue(coverage['end_reached']);self.assertEqual(coverage['unique_handles'],2);self.assertEqual(coverage['pages_fetched'],2)
            self.assertFalse(t.run_catalog_page(db,c));db.close()
    def test_error_envelope_and_cyclic_cursor_pause(self):
        with self.assertRaises(t.AccessStop):t.catalog_payload(json.dumps({'status':'error','error':SENTINEL}))
        with tempfile.TemporaryDirectory()as tmp:
            db=t.connect(Path(tmp));db.execute("INSERT INTO categories VALUES('news','News')");t.seed_catalog_pages(db)
            class Fake:
                def post_catalog(self,*args):return json.dumps({'status':'ok','html':'','hasMore':True,'nextPage':0,'nextOffset':0})
            with self.assertRaises(t.AccessStop):t.run_catalog_page(db,Fake())
            self.assertEqual(db.execute("SELECT count(*)FROM catalog_pages WHERE status='done'").fetchone()[0],0);db.close()

    def test_private_handle_page_can_continue_without_inventing_usernames(self):
        with tempfile.TemporaryDirectory()as tmp:
            db=t.connect(Path(tmp));db.execute("INSERT INTO categories VALUES('news','News')");t.seed_catalog_pages(db);db.execute("DELETE FROM catalog_pages WHERE peer_type='channel'");db.commit()
            class Fake:
                def post_catalog(self,*args):return json.dumps({'status':'ok','html':'<a href="/chat/private-id/stat">Private group</a>','hasMore':True,'nextPage':1,'nextOffset':100})
            self.assertTrue(t.run_catalog_page(db,Fake()))
            self.assertEqual(db.execute("SELECT count(*)FROM queue WHERE kind='profile'").fetchone()[0],0)
            c=t.catalog_coverage(db)[0];self.assertEqual(c['rows_per_page'],[1]);self.assertFalse(c['end_reached']);self.assertEqual(c['pending'],1);db.close()


    def test_private_secret_file_is_supported_and_insecure_mode_is_rejected(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)/'cookie.json';path.write_text(cookie_json());path.chmod(0o600)
            with patch.dict('os.environ',{'TGSTAT_COOKIE':'','TGSTAT_COOKIE_FILE':str(path)}):
                self.assertEqual(len(list(Session.from_environment().jar)),1)
                path.chmod(0o644)
                with self.assertRaises(ValueError)as caught:Session.from_environment()
                self.assertNotIn(SENTINEL,str(caught.exception))

    def test_unexpected_authenticated_failure_does_not_expose_secret(self):
        with tempfile.TemporaryDirectory()as tmp:
            args=['tgstat_collect.py','--session','--work-dir',tmp,'--max-slices','1']
            with patch.dict('os.environ',{'TGSTAT_COOKIE':cookie_json(),'TGSTAT_COOKIE_FILE':''}),patch.object(sys,'argv',args),patch.object(t.Client,'initialize',side_effect=RuntimeError(SENTINEL)):
                with self.assertRaises(RuntimeError)as caught:t.main()
                self.assertNotIn(SENTINEL,str(caught.exception))
            for path in(Path(tmp)/'exports').iterdir():
                value=gzip.decompress(path.read_bytes()).decode()if path.suffix=='.gz'else path.read_text()
                self.assertNotIn(SENTINEL,value)


# Synthetic HTTP outcomes must never change a running collector's host budget.
def setUpModule():
    global _isolated_budget_dir,_isolated_budget_env
    import os,tempfile
    from unittest.mock import patch
    _isolated_budget_dir=tempfile.TemporaryDirectory()
    _isolated_budget_env=patch.dict(os.environ,{'CRAWL_BUDGET_DIR':_isolated_budget_dir.name})
    _isolated_budget_env.start()

def tearDownModule():
    _isolated_budget_env.stop();_isolated_budget_dir.cleanup()


class AccountNoticeScopeTests(unittest.TestCase):
    def test_login_link_in_channel_post_does_not_invalidate_authenticated_session(self):
        body='<div class="profile-dropdown"><a href="/logout">Logout</a></div><h1>Iran channel</h1><div class="post-body"><div class="post-text"><a href="https://student.example.edu/login">University login</a></div></div>'
        self.assertIsNone(account_problem(Document(body)))
    def test_external_login_link_outside_posts_is_not_a_tgstat_logout(self):
        for url in('https://student.example.edu/login','https://tgstat.com.example.edu/login'):
            self.assertIsNone(account_problem(Document('<h1>Channel</h1><a href="'+url+'">Login</a>')))
    def test_post_login_forms_and_words_are_excluded_from_fragment_fallback(self):
        body='<div class="post-body"><form id="login"action="/login">Please log in; account is limited</form></div>'
        self.assertIsNone(account_problem(Document(body),fragment=True))
    def test_real_navigation_login_and_account_warning_still_stop(self):
        for url in('/login','https://tgstat.com/login','https://ir.tgstat.com/login'):
            self.assertEqual(account_problem(Document('<nav><a href="'+url+'">Login</a></nav><h1>Channel</h1>')),'Session is logged out')
        self.assertEqual(account_problem(Document('<form id="login"action="/login"></form>')),'Session is logged out')
        self.assertEqual(account_problem(Document('<div class="alert-warning">Account is limited</div><div class="post-body">Public post</div>')),'Account warning or limit detected')

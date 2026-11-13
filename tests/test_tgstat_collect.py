import gzip,json,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import tgstat_collect as t
from html_core import Document

class TGStatTests(unittest.TestCase):
    def test_profile_fields_and_recent_text(self):
        body='''<meta property="og:description"content="کتابخانه افغانستان"><h1>کانال نمونه</h1><p class="card-text">کتابخانه افغانستان - متن کامل</p><div><h5>Channel's geo and language</h5>Iran, Persian</div><div><h5>Category</h5><a>News</a></div><div><h2>12 345</h2><div>subscribers</div></div><div class="post-text">این پست درباره <b>تهران</b> است<br>متن جدید</div>'''
        v=t.extract(Document(body),t.BASE+'/channel/@Example')
        self.assertEqual((v['username'],v['members'],v['country'],v['language']),('example',12345,'Iran','Persian'))
        self.assertEqual(v['description'],'کتابخانه افغانستان - متن کامل');self.assertIn('تهران',v['text_sample']);self.assertIsNone(v['avg_reach'])
        self.assertEqual(v['category'],'News')
    def test_group_and_unknown_fields_not_invented(self):
        v=t.extract(Document('<h1>درخواست کتاب افغانستان</h1><div><h5>Groups\'s geo and language</h5>Iran, Persian</div>'),t.BASE+'/chat/@Example')
        self.assertEqual(v['type'],'group');self.assertEqual(v['text_sample'],'');self.assertIsNone(v['members'])
        body='<h1>Foreign</h1><div><h5>Channel\'s geo and language</h5>Russia, Russian</div>'
        self.assertIsNone(t.extract(Document(body),t.BASE+'/channel/@example'))
        with self.assertRaises(ValueError):t.extract(Document('<p>Sign in to access this page</p>'),t.BASE+'/chat/@example')
    def test_samples_bounded_and_stat_links_never_queued(self):
        row=t.extract(Document('<h1>Test</h1><div class="post-text">'+'الف '*600+'</div>'),t.BASE+'/channel/@example')
        self.assertLessEqual(len(row['text_sample']),1000)
        with tempfile.TemporaryDirectory()as tmp:
            db=t.connect(Path(tmp));t.enqueue(db,t.BASE+'/chat/@Example/stat');t.enqueue(db,t.BASE+'/chat/@Example/quotes');t.enqueue(db,'https://tgstat.com/channel/@foreign');t.enqueue(db,t.BASE+'/channel/@example/1/stat')
            self.assertEqual(db.execute("SELECT url FROM queue WHERE kind='profile'").fetchall(),[(t.BASE+'/chat/@example',)])
            db.close()
    def test_listing_country_taxonomy_and_gated_depth(self):
        with tempfile.TemporaryDirectory()as tmp:
            db=t.connect(Path(tmp));doc=Document('<h1>News</h1><a href="/ratings/chats/news">News</a><a href="/channel/@Example/stat">One</a><a href="/channel/@example/stat">same</a><a href="https://tgstat.ru/channel/@other">foreign</a><form method="POST" action="/news/items"></form>')
            t.listing(db,doc,t.BASE+'/news')
            row=db.execute('SELECT unique_handles,continuation FROM listings').fetchone();self.assertEqual(row,(1,'form_show_more_not_followed'))
            self.assertEqual(db.execute("SELECT count(*)FROM queue WHERE kind='profile'").fetchone()[0],1);db.close()
    def test_resume_export_and_stable_buckets(self):
        with tempfile.TemporaryDirectory()as tmp:
            work=Path(tmp);db=t.connect(work);r=t.extract(Document('<h1>Test</h1>'),t.BASE+'/channel/@example');db.execute('INSERT INTO records VALUES(?,?)',('example',json.dumps(r)));db.commit()
            first=t.export(db,work);second=t.export(db,work);self.assertEqual(first['files'],second['files'])
            rows=[]
            for f in(work/'exports').glob('*.gz'):
                with gzip.open(f,'rt')as h:rows.extend(json.loads(s)for s in h if s.strip())
            self.assertEqual(len(rows),1);db.close()
    def test_access_stop_is_not_retried(self):
        with tempfile.TemporaryDirectory()as tmp:
            db=t.connect(Path(tmp));c=t.Client(db);c.rules=[t.Robots('User-agent: *\nAllow: /')]
            with patch.object(t,'reserve'),patch.object(c.opener,'open',side_effect=HTTPError(t.BASE,429,'limited',{},None))as opened:
                with self.assertRaises(t.AccessStop):c.get(t.BASE+'/channel/@example')
                self.assertEqual(opened.call_count,1)
            self.assertEqual(db.execute('SELECT status FROM requests').fetchone()[0],429);db.close()

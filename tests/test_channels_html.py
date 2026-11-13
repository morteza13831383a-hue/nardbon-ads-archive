import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import channels_html as c
from html_core import Document

class Fake:
    def __init__(self, pages): self.pages=pages; self.calls=[]
    def initialize(self): pass
    def get(self,url):
        self.calls.append(url)
        value=self.pages[url]
        if isinstance(value,Exception): raise value
        return value

class ChannelsTests(unittest.TestCase):
    def test_resume_casefold_and_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=c.connect(Path(tmp)); c.enqueue(db,'/channels/Example'); c.enqueue(db,'https://evil.test/channels/Evil'); c.enqueue(db,'/api/channels')
            url=c.BASE+'/channels/example'
            client=Fake({url:'<h1>کانال نمونه</h1><a href="/channels/Example">self</a>'})
            c.run(db,client,2); c.run(db,client,2)
            self.assertEqual(len(client.calls),1)
            self.assertEqual(db.execute('select username from channels').fetchone()[0],'example')
            report=c.export(db,Path(tmp)); self.assertEqual(report['channels'],1)
            self.assertEqual(report['classifications'],{'persian_candidate':1})
            with gzip.open(Path(tmp)/'exports/channels.jsonl.gz','rt') as f: self.assertEqual(json.loads(f.readline())['username'],'example')
            db.close()
    def test_no_inferred_country(self):
        doc=Document('<h1>کانال ایرانی</h1><a href="/countries/ir">Iran</a>')
        self.assertEqual(c.extract(doc,c.BASE+'/channels/example')['classification'],'persian_candidate')
        doc=Document('<h1>Example</h1><script type="application/ld+json">{"@type":"ProfilePage","url":"https://tgadsspy.com/channels/example","mainEntity":{"address":{"addressCountry":"IR"}}}</script>')
        self.assertEqual(c.extract(doc,c.BASE+'/channels/example')['classification'],'iran_confirmed')
    def test_block_stops_without_completing_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=c.connect(Path(tmp)); c.enqueue(db,'/channels/example')
            url=c.BASE+'/channels/example'
            with self.assertRaises(HTTPError): c.run(db,Fake({url:HTTPError(url,429,'limited',{},None)}),3)
            self.assertEqual(db.execute('select status from queue').fetchone()[0],'pending'); db.close()
    def test_layout_failure_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=c.connect(Path(tmp)); c.enqueue(db,'/channels/example')
            c.run(db,Fake({c.BASE+'/channels/example':'<title>Challenge</title>'}),1)
            self.assertEqual(db.execute('select status from queue').fetchone()[0],'failed'); db.close()

    def test_delivery_checkpoint_only_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp); db=c.connect(work)
            row=c.extract(Document('<h1>کانال نمونه</h1>'),c.BASE+'/channels/example')
            db.execute('insert into channels values(?,?)',('example',json.dumps(row)));db.commit();c.export(db,work)
            calls=[]
            def gh(repo,path,payload=None):
                calls.append((path,payload))
                if path=='commits/main': return {'sha':'head','commit':{'tree':{'sha':'tree'}}}
                return {'sha':'new'}
            with patch('archive_collect.gh',side_effect=gh): c.publish(db,work,'owner/repo')
            self.assertEqual(db.execute("select count(*) from meta where key like 'published_bucket_%'").fetchone()[0],16)
            manifest=[x for x in calls if x[0]=='git/trees'][0][1]['tree'][-1]['content']
            self.assertEqual(len(json.loads(manifest)['files']),16)
            with patch('archive_collect.gh',side_effect=AssertionError('unchanged data uploaded')): c.publish(db,work,'owner/repo')
            db.close()


class IranPriorityTests(unittest.TestCase):
    def test_iranian_evidence_is_fetched_first(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'iran_advertisers', return_value={'tg-shop_ir'}):
            db=c.connect(Path(tmp))
            c.enqueue(db,'/channels/random_latin')                          # broad snowball: late
            c.enqueue(db,'/advertisers/tg-shop_ir/channels')                # Iranian advertiser page: first
            c.enqueue(db,'/advertisers/tg-unknown/channels')                # not Iranian: ignored
            order=[u for (u,) in db.execute("select url from queue order by priority,url")]
            self.assertEqual(order[0], c.BASE+'/advertisers/tg-shop_ir/channels')
            self.assertNotIn(c.BASE+'/advertisers/tg-unknown/channels', order)
            db.close()

    def test_children_get_priority_from_page_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'iran_advertisers', return_value={'tg-shop_ir'}):
            db=c.connect(Path(tmp))
            page=c.BASE+'/advertisers/tg-shop_ir/channels'
            c.enqueue(db,page)
            html='<a href="/channels/bought_here">Some Name</a><a href="/channels/farsi_name">کانال فارسی</a>'
            c.run(db,Fake({page:html}),1)
            pr=dict(db.execute("select url,priority from queue where kind='channel'"))
            self.assertEqual(pr[c.BASE+'/channels/bought_here'], c.P_IRAN_PLACEMENT)
            self.assertEqual(pr[c.BASE+'/channels/farsi_name'], c.P_IRAN_PLACEMENT)
            # a channel found again with stronger evidence moves forward, never back
            c.enqueue(db,'/channels/bought_here',priority=c.P_OTHER)
            self.assertEqual(db.execute("select priority from queue where url=?",(c.BASE+'/channels/bought_here',)).fetchone()[0], c.P_IRAN_PLACEMENT)
            db.close()


class OwnerApprovedListing(unittest.TestCase):
    def test_only_persian_listing_is_excepted_from_robots(self):
        from html_core import Robots
        r=Robots("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /ads?*\nDisallow: /channels?*\n")
        self.assertTrue(r.allowed(c.BASE+'/channels?lang=fa'))
        self.assertTrue(r.allowed(c.BASE+'/channels?lang=fa&page=12'))
        for blocked in ('/channels?lang=ru','/channels?q=shop','/channels?lang=fa&sort=x','/ads?geo=IR','/api/v1/ads'):
            self.assertFalse(r.allowed(c.BASE+blocked), blocked)


class RotationTests(unittest.TestCase):
    def test_big_advertiser_backlog_does_not_starve_listing_and_profiles(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c, 'iran_advertisers', return_value={f'tg-a{i:03d}' for i in range(50)}):
            db=c.connect(Path(tmp))
            for i in range(50):
                c.enqueue(db, f'/advertisers/tg-a{i:03d}/channels', 'listing', c.P_IRAN_ADV_PAGE)
            c.enqueue(db, '/channels?lang=fa', 'listing', c.P_PERSIAN_LINK)
            c.enqueue(db, '/channels/some_profile', priority=c.P_IRAN_PLACEMENT)
            picked=[]
            for turn in range(4):
                url,kind=c.next_job(db, turn); picked.append(url)
                db.execute("UPDATE queue SET status='done' WHERE url=?", (url,))
            self.assertIn(c.BASE+'/channels?lang=fa', picked)
            self.assertIn(c.BASE+'/channels/some_profile', picked)
            self.assertTrue(any('/advertisers/' in u for u in picked))
            db.close()


    def test_listing_row_saves_numbers_and_defers_complete_profile(self):
        html='''<div role="table"><div role="row"><span role="columnheader">Channel</span><span role="columnheader">Avg. views</span><span role="columnheader">Members</span><span role="columnheader">Category</span></div>
        <div role="row"><div role="cell"><a href="/channels/Example">کانال نمونه<span>@Example</span></a></div><span role="cell">12.3K</span><span role="cell">۱٬۲۳۴</span><span role="cell">News</span></div></div>'''
        rows=c.extract_listing(Document(html),c.BASE+'/channels?lang=fa&page=2')
        self.assertEqual(len(rows),1);r=rows[0]
        self.assertEqual((r['username'],r['title'],r['members'],r['avg_views']),('example','کانال نمونه',1234,12300))
        self.assertEqual(r['classification'],'persian_candidate');self.assertEqual(r['source_url'],c.BASE+'/channels?lang=fa&page=2')
        with tempfile.TemporaryDirectory() as tmp:
            db=c.connect(Path(tmp));c.save_listing(db,rows);c.enqueue(db,'/channels/example',priority=1)
            self.assertEqual(db.execute('select status from queue').fetchone()[0],'listing_saved')
            c.enqueue(db,'/channels/example',priority=0)
            self.assertEqual(db.execute('select status from queue').fetchone()[0],'pending');db.close()

    def test_listing_missing_numbers_keeps_profile_and_does_not_erase_full_record(self):
        html='''<table><tr><th>Channel</th><th>Members</th><th>Avg. views</th></tr><tr><td><a href="/channels/example">Example @example</a></td><td>0</td><td>—</td></tr></table>'''
        rows=c.extract_listing(Document(html),c.BASE+'/channels?lang=fa')
        self.assertEqual(rows[0]['members'],0);self.assertIsNone(rows[0]['avg_views'])
        self.assertEqual(c.extract_listing(Document(html),c.BASE+'/channels?lang=en'),[])
        with tempfile.TemporaryDirectory() as tmp:
            db=c.connect(Path(tmp));c.save_listing(db,rows)
            self.assertEqual(db.execute('select status from queue').fetchone()[0],'pending')
            old={'username':'example','title':'Full profile','classification':'iran_confirmed','members':999,'iran_advertisers':['tg-example'],'description':'bio'}
            db.execute('insert or replace into channels values(?,?)',('example',json.dumps(old)))
            c.save_listing(db,rows)
            self.assertEqual(json.loads(db.execute('select payload from channels').fetchone()[0]),old);db.close()

    def test_listing_strengthens_unclassified_profile_without_erasing_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=c.connect(Path(tmp))
            old={'username':'example','title':'Profile title','classification':'unclassified','members':999,'description':'bio'}
            db.execute('insert into channels values(?,?)',('example',json.dumps(old)))
            c.save_listing(db,[{'username':'example','source_url':c.BASE+'/channels?lang=fa'}])
            row=json.loads(db.execute('select payload from channels').fetchone()[0])
            self.assertEqual(row['classification'],'persian_candidate');self.assertEqual(row['description'],'bio');self.assertEqual(row['members'],999)
            db.close()

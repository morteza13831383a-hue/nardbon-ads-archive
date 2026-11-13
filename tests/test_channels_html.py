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

import gzip
from email.message import Message
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import archive_html as archive
import mirror_ads as mirror
from ad_claims import Claims
from html_core import Robots, Document, Client

CUTOFF='2026-11-13T08:53:18+00:00'
ID='cmo909khl04xlm6ox6e9qskja'

class MirrorTests(unittest.TestCase):
 def test_tgadsspy_access_header_never_goes_to_mirror(self):
  class Reply:
   status=200
   headers=Message()
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,n):return b'<article></article>'
  client=Client(mirror.BASE,4);client.robots=Robots('User-agent: *\nDisallow: /ads/')
  Reply.headers['Content-Type']='text/html'
  with patch.dict('os.environ',{'ARCHIVE_ACCESS_TOKEN':'private-test-token'}),patch('html_core.reserve'),patch('html_core.report'),patch.object(client.opener,'open',return_value=Reply()) as opened:
   client.get(mirror.BASE+'/ads/'+ID)
  self.assertNotIn('X-nardbon-archive',dict(opened.call_args.args[0].header_items()))
 def test_owner_exception_host_and_exact_path(self):
  robots=Robots('User-agent: *\nDisallow: /ads/\nDisallow: /api/')
  self.assertTrue(robots.allowed(mirror.BASE+'/ads/'+ID))
  self.assertTrue(robots.allowed('https://telegramadsanalytics.com/ads/'+ID))
  self.assertFalse(robots.allowed(mirror.BASE+'/ads/'+ID+'?x=1'))
  self.assertFalse(robots.allowed('https://another.example/ads/'+ID))
  self.assertFalse(robots.allowed(mirror.BASE+'/api/v1/ads'))
 def test_claims_are_atomic_and_release_unverified(self):
  with tempfile.TemporaryDirectory() as temp:
   a=Claims(Path(temp)/'claims.sqlite3');b=Claims(Path(temp)/'claims.sqlite3')
   self.assertEqual(a.acquire(ID,'tgadsspy'),'acquired')
   self.assertEqual(b.acquire(ID,'mirror'),'claimed')
   a.finish(ID,'tgadsspy',False)
   self.assertEqual(b.acquire(ID,'mirror'),'acquired')
   b.finish(ID,'mirror',True)
   self.assertEqual(a.acquire(ID,'tgadsspy'),'done')
   self.assertEqual(b.acquire('unverified-id','https://telegramadsarchive.com'),'acquired')
   b.finish('unverified-id','https://telegramadsarchive.com','unverified')
   self.assertEqual(b.acquire('unverified-id','https://telegramadsanalytics.com'),'unverified')
   self.assertEqual(a.acquire('unverified-id','tgadsspy'),'acquired')
   a.close();b.close()
 def test_mirror_identity_imports_with_original_id(self):
  with tempfile.TemporaryDirectory() as temp:
   db=archive.connect(Path(temp)/'spy',CUTOFF)
   aid='abc';url=mirror.BASE+'/ads/'+aid
   ld={'@type':'CreativeWork','identifier':aid,'name':'Iran ad','datePublished':'2026-11-12T00:00:00Z','contentLocation':{'name':'IR'}}
   row,status=archive.extract(Document('<article><script type="application/ld+json">'+json.dumps(ld)+'</script></article>'),url,CUTOFF)
   self.assertEqual(status,'iran')
   path=Path(temp)/'ads-mirror-test.jsonl.gz'
   with gzip.open(path,'wt')as f:f.write(json.dumps(row)+'\n')
   self.assertEqual(archive.import_records(db,[path]),1)
   self.assertEqual(db.execute('SELECT status FROM queue WHERE url=?',(archive.BASE+'/ads/'+aid,)).fetchone()[0],'iran')
   row['source_url']=mirror.BASE+'/ads/other'
   with gzip.open(path,'wt')as f:f.write(json.dumps(row)+'\n')
   with self.assertRaises(ValueError):archive.import_records(db,[path])
   row['source_url']='https://telegramadsanalytics.com/ads/'+aid
   with gzip.open(path,'wt')as f:f.write(json.dumps(row)+'\n')
   self.assertEqual(archive.import_records(db,[path]),1)
   db.close()
 def test_seed_oldest_and_skip_completed(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);(root/'data/ads').mkdir(parents=True)
   (root/'data/ads/2026-04.json').write_text('[]')
   spy=archive.connect(root/'spy',CUTOFF)
   spy.execute('DELETE FROM queue')
   first='cmo90991e04wcm6oxnvwsztlr';second=ID
   for ad_id in (first,second):
    archive.enqueue(spy,archive.BASE+'/ads/'+ad_id,'detail',30)
    spy.execute("UPDATE queue SET month='2026-04' WHERE url=?",(archive.BASE+'/ads/'+ad_id,))
   spy.commit();spy.close()
   db=mirror.connect(root/'mirror',CUTOFF)
   mirror.seed(db,root/'spy/checkpoint.sqlite3',root)
   self.assertEqual([r[0]for r in db.execute("SELECT id FROM jobs WHERE status='pending' ORDER BY id")],[first,second])
   spy=sqlite3.connect(root/'spy/checkpoint.sqlite3');spy.execute("UPDATE queue SET status='iran' WHERE url=?",(archive.BASE+'/ads/'+first,));spy.commit();spy.close()
   mirror.seed(db,root/'spy/checkpoint.sqlite3',root)
   self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(first,)).fetchone()[0],'spy_done')
   db.close()
 def test_other_worker_claim_is_removed_from_mirror_pending_queue(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);(root/'data/ads').mkdir(parents=True)
   spy=archive.connect(root/'spy',CUTOFF);spy.execute('DELETE FROM queue')
   archive.enqueue(spy,archive.BASE+'/ads/'+ID,'detail',30)
   spy.execute("UPDATE queue SET month='2026-04' WHERE url=?",(archive.BASE+'/ads/'+ID,));spy.commit();spy.close()
   db=mirror.connect(root/'mirror',CUTOFF)
   claims=Claims(root/'claims.sqlite3')
   self.assertEqual(claims.acquire(ID,'tgadsspy'),'acquired');claims.finish(ID,'tgadsspy',True)
   class NoNetwork:
    base=mirror.BASE
    def get(self,url):raise AssertionError('No duplicate fetch allowed')
   self.assertEqual(mirror.crawl_slice(db,NoNetwork(),claims,root/'spy/checkpoint.sqlite3',root,1),0)
   self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(ID,)).fetchone()[0],'shared_done')
   claims.close();db.close()

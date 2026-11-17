import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import archive_html
import mirror_niche_collect as niche
from html_core import Document


class NicheCollectorTests(unittest.TestCase):
    def test_listing_skips_known_ids_and_completes_small_niche(self):
        class Client:
            base='https://telegramadsarchive.com'
            def get(self,url):
                self.url=url
                return '<div>Found 2</div><a href="/ads/abc">A</a><a href="/ads/def">B</a>'
        with tempfile.TemporaryDirectory() as temp:
            db=niche.connect(Path(temp)/'checkpoint.sqlite3','telegramadsarchive.com','2026-11-13T00:00:00+00:00')
            db.execute("INSERT INTO niches(name,site_count)VALUES('test',2)")
            client=Client()
            seen,new,done=niche.listing(client,db,'test',1,{'abc'})
            self.assertEqual((seen,new,done),(2,1,True))
            self.assertEqual(db.execute('SELECT id FROM candidates').fetchall(),[('def',)])
            self.assertEqual(db.execute("SELECT status FROM niches WHERE name='test'").fetchone()[0],'done')
            db.close()

    def test_listing_country_evidence_allows_missing_jsonld_country(self):
        url='https://tgads.biz/ads/abc'
        item={'@type':'CreativeWork','identifier':'abc','name':'آگهی',
              'datePublished':'2026-04-01T00:00:00Z'}
        doc=Document('<article><script type="application/ld+json">'+json.dumps(item)+'</script></article>')
        self.assertEqual(archive_html.extract(doc,url,'2026-11-13T00:00:00+00:00')[1],'not_iran')
        record,status=archive_html.extract(doc,url,'2026-11-13T00:00:00+00:00',iran_listing=True)
        self.assertEqual(status,'iran')
        self.assertEqual(record['source_url'],url)

    def test_listing_uses_next_link_when_page_has_fewer_cards(self):
        class Client:
            base='https://telegramadsarchive.com'
            def get(self,url):
                return '<div>Found 91</div><a href="/ads/abc">A</a>' \
                    '<a href="/ads?geo=IR&niche=test&page=2">Next</a>'
        with tempfile.TemporaryDirectory() as temp:
            db=niche.connect(Path(temp)/'checkpoint.sqlite3','telegramadsarchive.com','2026-11-13T00:00:00+00:00')
            db.execute("INSERT INTO niches(name,site_count)VALUES('test',91)")
            self.assertEqual(niche.listing(Client(),db,'test',1,set()),(1,1,False))
            db.close()

    def test_missing_next_link_does_not_finish_before_count(self):
        class Client:
            base='https://telegramadsarchive.com'
            def get(self,url):
                return '<div>Found 91</div><a href="/ads/abc">A</a>'
        with tempfile.TemporaryDirectory() as temp:
            db=niche.connect(Path(temp)/'checkpoint.sqlite3','telegramadsarchive.com','2026-11-13T00:00:00+00:00')
            db.execute("INSERT INTO niches(name,site_count)VALUES('test',91)")
            self.assertEqual(niche.listing(Client(),db,'test',1,set()),(1,1,False))
            self.assertEqual(db.execute("SELECT page,status FROM niches WHERE name='test'").fetchone(),(2,'pending'))
            db.close()

from pathlib import Path
import sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import mirror_gap_collect as gap

class GapTests(unittest.TestCase):
    def test_iran_search_keeps_query_and_stops_on_no_new_ids(self):
        class Client:
            base='https://telegramadsarchive.com'
            def get(self,url):
                self.url=url
                return '<div>Found 500</div><a href="/ads/abc">A</a>' \
                    '<a href="/ads?geo=IR&q=%D8%A7%DB%8C%D8%B1%D8%A7%D9%86&page=2">Next</a>'
        with tempfile.TemporaryDirectory()as temp:
            root=Path(temp);(root/'data').mkdir()
            db=gap.connect(root/'db.sqlite3','2026-11-13T00:00:00+00:00',root)
            db.execute("INSERT INTO jobs(key,kind)VALUES('ایران','keyword')")
            client=Client()
            self.assertEqual(gap.listing(db,client,'ایران','keyword',1,{'abc'}),(1,0,True))
            self.assertIn('geo=IR',client.url)
            self.assertIn('q=',client.url)
            db.close()

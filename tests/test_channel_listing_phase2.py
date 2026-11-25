import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import channels_html as c
import fast_iran_channels_crawler as worker
from channel_listing_pass import FIRST, store
from channel_document import parse


class DirectListings(unittest.TestCase):
    def test_generic_cards_keep_geo_and_language_separate(self):
        html = """<main><article data-country="IR" data-members="1.2K" data-niche="tech">
            <h2>Example</h2><a href="/channels/Example">@Example</a>
            <p itemprop="description">Daily updates</p></article>
            <a href="https://outside.test/channels/Other">Invalid</a></main>"""
        row, = c.extract_listing(parse(html), c.BASE+'/niches/tech',all_listings=True)
        self.assertEqual((row['username'],row['members'],row['category'],row['description']),('example',1200,'tech','Daily updates'))
        self.assertTrue(row['iran_geo_tag'])
        self.assertIsNone(row['language'])

    def test_sitemap_and_critical_profiles(self):
        with tempfile.TemporaryDirectory() as folder:
            db=c.connect(Path(folder))
            try:
                with db:
                    c.consume(db,c.BASE+'/sitemap.xml','sitemap','<urlset><url><loc>https://tgadsspy.com/channels/Example</loc></url></urlset>')
                self.assertEqual(db.execute('select username from channels').fetchall(),[('example',)])
                self.assertEqual(worker.claim(db,2,critical_profiles=True),[])
                with db:
                    row=c.direct_row('example','اخبار',FIRST,{'Members':'25'})
                    c.save_listing(db,[row])
                self.assertEqual(worker.claim(db,2,critical_profiles=True),[])
                with db:
                    c.save_listing(db,[c.direct_row('missing','اخبار',FIRST,{})])
                self.assertEqual(worker.claim(db,2,critical_profiles=True),[(c.BASE+'/channels/missing','channel')])
            finally:
                db.close()

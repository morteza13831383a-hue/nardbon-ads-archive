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


class DeeperSample(unittest.TestCase):
    def test_restart_does_not_skip_holes_or_repeat_sample(self):
        from channel_listing_pass import next_page
        with sqlite3.connect(':memory:') as db:
            db.execute('create table listing_pages(page integer primary key,more integer)')
            db.executemany('insert into listing_pages values(?,?)',[(1,1),(2,1),(201,1)])
            self.assertEqual(next_page(db,201,3),202)
            db.execute('insert into listing_pages values(202,0)')
            self.assertEqual(next_page(db,201,3),3)
            self.assertEqual(next_page(db),3)
            db.execute('insert into listing_pages values(3,0)')
            self.assertIsNone(next_page(db))


class DeepFirst(unittest.TestCase):
    def test_deferred_blocks_resume_inside_visible_end(self):
        from channel_listing_pass import deep_page
        with sqlite3.connect(':memory:') as db:
            db.execute('create table meta(key primary key,value)')
            db.execute('create table listing_pages(page primary key,more)')
            db.executemany('insert into listing_pages values(?,?)',[(1,1),(7,0)])
            self.assertEqual(deep_page(db,4,2),4)
            db.execute("insert into meta values('deep_cursor','8')")
            self.assertEqual(deep_page(db,4,2),5)
            db.executemany('insert into listing_pages values(?,1)',[(5,),(6,)])
            self.assertEqual(deep_page(db,4,2),3)
            db.executemany('insert into listing_pages values(?,1)',[(2,),(3,),(4,)])
            self.assertIsNone(deep_page(db,4,2))

    def test_low_yield_window_skips_one_block_and_persists(self):
        from channel_listing_pass import deep_page, store
        with tempfile.TemporaryDirectory() as folder:
            db=c.connect(Path(folder))
            try:
                db.execute('create table listing_pages(page primary key,fingerprint,rows,new_local,iran,started,more)')
                db.execute('create table listing_yield(page primary key,net,rows)')
                known={'known_one','known_two'}
                for page,u in enumerate(sorted(known),1):
                    body=f'<main><a href="/channels/{u}">{u}</a><a href="/channels?lang=fa&amp;page={page+1}">Next</a></main>'
                    store(db,page,body,page,known,2)
                self.assertEqual(deep_page(db,None,2),5)
                self.assertEqual(db.execute('select sum(net),sum(rows) from listing_yield').fetchone(),(0,2))
            finally:
                db.close()

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import channel_discovery_run as runner


class Discovery(unittest.TestCase):
    def test_route_boundary_for_seed_and_redirect(self):
        base=runner.ch.BASE
        for path in ['/ads/test','/api/v1/channels','/ads?geo=IR','/channels?lang=fa','/channels/example?go=1']:
            self.assertIsNone(runner.kind(base+path))
        self.assertIsNone(runner.kind('https://other.test/channels/example'))
        for path in ['/sitemap.xml','/sitemap/3.xml','/advertisers/tg-seed/channels?page=2','/channels/example']:
            self.assertIsNotNone(runner.kind(base+path))

    def test_sitemap_handles_are_not_language_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            db=runner.ch.connect(Path(folder))
            try:
                db.executescript("""create table discovery_jobs(url primary key,source);
                                    create table discovery_seen(username primary key,source,net);""")
                runner.ingest(db,runner.ch.BASE+'/sitemap.xml','sitemap','<urlset><url><loc>https://tgadsspy.com/channels/TestName</loc></url><url><loc>https://tgadsspy.com/ads/test</loc></url></urlset>',set())
                self.assertEqual(db.execute('select username,net from discovery_seen').fetchall(),[('testname',1)])
                self.assertEqual(db.execute('select count(*) from channels').fetchone()[0],0)
                self.assertEqual(db.execute('select count(*) from discovery_jobs').fetchone()[0],1)
            finally:
                db.close()

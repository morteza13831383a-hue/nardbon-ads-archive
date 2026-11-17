import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import niche_listing_status


class ListingStatusTests(unittest.TestCase):
    def test_gap_records_are_deduplicated_against_baseline_and_niches(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);work=root/'sweep';work.mkdir()
            with sqlite3.connect(work/'checkpoint.sqlite3') as db:
                db.executescript("""CREATE TABLE known_ids(id TEXT);CREATE TABLE records(id TEXT);
                    CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE pages(status TEXT);""")
                db.executemany('INSERT INTO known_ids VALUES(?)',[('old',),('old2',)])
                db.execute("INSERT INTO records VALUES('niche')")
                db.execute("INSERT INTO meta VALUES('base_known_count','2')")
                db.execute("INSERT INTO pages VALUES('done')")
            for host,ids in [('one',['old','niche','gap']),('two',['gap','gap2'])]:
                directory=root/host;directory.mkdir()
                with sqlite3.connect(directory/'checkpoint.sqlite3') as db:
                    db.executescript('CREATE TABLE records(id TEXT);CREATE TABLE jobs(status TEXT);')
                    db.executemany('INSERT INTO records VALUES(?)',[(i,)for i in ids])
                    db.execute("INSERT INTO jobs VALUES('pending')")
            config=root/'runtime.json'
            config.write_text(json.dumps({'mirror_gap_workers':[{'work_dir':str(root/h)}for h in ('one','two')]}))
            work.joinpath('status-state.json').write_text(json.dumps({'ads':1}))
            body,state=niche_listing_status.build(work,config)
            self.assertIn('New Iran ads since prior report: 2;',body)
            self.assertIn('new unique total: 3;',body)
            self.assertIn('Numerical gap to 57,492: 57,487;',body)
            self.assertIn('gap jobs pending 2.',body)
            self.assertEqual(state['ads'],3)


if __name__=='__main__':unittest.main()

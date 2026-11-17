from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from mirror_niche_collect import HOSTS,NICHES
from collection_supervisor import niches_complete
from niche_coverage import audit

class NicheCoverageTests(unittest.TestCase):
    def test_gap_waits_for_all_niche_pages_and_pending_details(self):
        with tempfile.TemporaryDirectory() as temp:
            workers=[]
            for host in HOSTS:
                work=Path(temp)/host;work.mkdir()
                db=sqlite3.connect(work/'checkpoint.sqlite3')
                db.executescript('CREATE TABLE niches(name TEXT,site_count INTEGER,page INTEGER,status TEXT);'
                                 'CREATE TABLE candidates(id TEXT,status TEXT);'
                                 'CREATE TABLE requests(kind TEXT,key TEXT);')
                db.close();workers.append({'host':host,'work_dir':str(work)})
            for i,name in enumerate(NICHES):
                work=Path(workers[i%6]['work_dir'])
                with sqlite3.connect(work/'checkpoint.sqlite3') as db:
                    db.execute("INSERT INTO niches VALUES(?,30,2,'done')",(name,))
                    db.execute("INSERT INTO requests VALUES('listing',?)",(name+':1',))
            config={'mirror_niche_workers':workers}
            self.assertTrue(niches_complete(config))
            work=Path(workers[0]['work_dir'])
            with sqlite3.connect(work/'checkpoint.sqlite3') as db:
                db.execute('UPDATE niches SET site_count=91 WHERE name=?',(NICHES[0],))
            self.assertFalse(niches_complete(config))
            self.assertTrue(audit(Path(temp))[0]['early'])
            with sqlite3.connect(work/'checkpoint.sqlite3') as db:
                db.execute("INSERT INTO requests VALUES('listing',?)",(NICHES[0]+':2',))
                db.execute("INSERT INTO requests VALUES('listing',?)",(NICHES[0]+':3',))
                db.execute("INSERT INTO candidates VALUES('abc','pending')")
            self.assertFalse(niches_complete(config))
            with sqlite3.connect(work/'checkpoint.sqlite3') as db:
                db.execute("UPDATE candidates SET status='iran' WHERE id='abc'")
            self.assertTrue(niches_complete(config))

if __name__=='__main__':unittest.main()

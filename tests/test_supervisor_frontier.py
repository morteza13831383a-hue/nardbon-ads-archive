import sqlite3,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from collection_supervisor import tgstat_frontier_exhausted
class FrontierTests(unittest.TestCase):
 def test_empty_frontier_stays_idle_but_work_or_delivery_resumes(self):
  with tempfile.TemporaryDirectory()as tmp:
   p=Path(tmp)/'state.sqlite3'
   self.assertFalse(tgstat_frontier_exhausted(p))
   db=sqlite3.connect(p)
   db.executescript('CREATE TABLE meta(key TEXT,value TEXT);CREATE TABLE tgstat_slices(status TEXT);CREATE TABLE tgstat_unresolved(status TEXT);')
   self.assertFalse(tgstat_frontier_exhausted(p))
   db.execute("INSERT INTO tgstat_slices VALUES('done')");db.execute("INSERT INTO tgstat_unresolved VALUES('failed')");db.commit()
   self.assertTrue(tgstat_frontier_exhausted(p))
   db.execute("INSERT INTO tgstat_unresolved VALUES('pending')");db.commit()
   self.assertFalse(tgstat_frontier_exhausted(p))
   db.execute("DELETE FROM tgstat_unresolved WHERE status='pending'");db.execute("INSERT INTO meta VALUES('github_delivery_pending','1')");db.commit()
   self.assertFalse(tgstat_frontier_exhausted(p));db.close()

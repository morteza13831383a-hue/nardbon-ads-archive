import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import archive_html as archive
import archive_collect as collector

class CollectorTests(unittest.TestCase):
    def test_only_new_records_are_published_and_checkpointed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            db=archive.connect(root,'2026-11-13T00:00:00Z')
            db.execute('INSERT INTO records VALUES(?,?)',('a',json.dumps({'id':'a','text':'متن'})))
            db.commit()
            calls=[]
            def fake(repo,path,payload=None):
                calls.append((path,payload))
                if path=='commits/main':return {'sha':'parent','commit':{'tree':{'sha':'oldtree'}}}
                return {'sha':{'git/blobs':'blob','git/trees':'tree','git/commits':'commit','git/refs/heads/main':'commit'}[path]}
            with patch.object(collector,'gh',fake):
                self.assertEqual(collector.publish(db,root,'owner/repo',root/'absent-seed'),1)
                self.assertEqual(collector.publish(db,root,'owner/repo',root/'absent-seed'),0)
            self.assertEqual(sum(p=='git/commits' for p,_ in calls),1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM github_delivered').fetchone()[0],1)
            db.close()

if __name__=='__main__':unittest.main()

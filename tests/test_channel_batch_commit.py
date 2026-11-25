import gzip
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from commit_channel_parts import publish


class BatchCommit(unittest.TestCase):
    def test_local_bare_remote_delivery_and_duplicate_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root/'repo'
            repo.mkdir()
            def git(*args):
                return subprocess.check_output(['git','-C',repo,*args],stderr=subprocess.DEVNULL)
            git('init','-b','main')
            git('config','user.email','test@example.invalid')
            git('config','user.name','Test')
            (repo/'scripts').mkdir()
            (repo/'scripts/commit_data.py').write_bytes((Path(__file__).resolve().parents[1]/'scripts/commit_data.py').read_bytes())
            git('add','scripts')
            git('commit','-m','Initial')
            remote=root/'remote.git'
            git('init','--bare',str(remote))
            git('remote','add','origin',str(remote))
            git('push','origin','main')
            out=root/'out';out.mkdir()
            body=b'{"username":"example"}\n'
            part=out/('channels-batch-'+hashlib.sha256(body).hexdigest()+'.jsonl.gz')
            for _ in range(2):
                part.write_bytes(gzip.compress(body,mtime=0))
                publish(repo,out)
                self.assertFalse(part.exists())
                self.assertEqual(gzip.decompress(git('show','origin/main:data/channel-html-import/'+part.name)),body)
            self.assertEqual(git('rev-list','--count','origin/main').strip(),b'2')

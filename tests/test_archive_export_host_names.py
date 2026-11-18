import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from archive_publish import host_export_manifest,host_export_name


class HostExportNamesTests(unittest.TestCase):
    def test_manifest_preserves_checksums_and_explains_text_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'manifest.json'
            source={'files':[{'name':'ads-00001.csv','sha256':'csv-hash'},
                             {'name':'ads-00001.jsonl.gz','sha256':'gz-hash'}]}
            path.write_text(json.dumps(source),encoding='utf-8')
            host=host_export_manifest(path)
            self.assertEqual([item['host_name'] for item in host['files']],
                             ['ads-00001.csv.txt','ads-00001.jsonl.gz.txt'])
            self.assertEqual([item['sha256'] for item in host['files']],['csv-hash','gz-hash'])
            self.assertEqual(json.loads(path.read_text()),source)
            self.assertEqual(host_export_name('manifest.json'),'manifest.json')


if __name__=='__main__':unittest.main()

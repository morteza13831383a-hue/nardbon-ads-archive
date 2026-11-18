import csv
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import upload_channel_exports as exporter


class ChannelExportTests(unittest.TestCase):
    def test_parts_preserve_rows_and_utf8_and_escape_csv_formulas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'channels.json';out=root/'out'
            source.write_text(json.dumps({'at':'2026-11-18T00:00:00Z',
                'fields':['u','t','m','kd','d'],
                'rows':[['a','تهران',10,'channel','متن'],['b','=CMD()',2,'group','توضیح']]},
                ensure_ascii=False),encoding='utf-8')
            manifest=exporter.export(source,out,max_part_bytes=1000)
            self.assertEqual(manifest['records'],2)
            self.assertEqual(manifest['files'][0]['host_name'],'channels-00001.jsonl.gz.txt')
            with gzip.open(out/'channels-00001.jsonl.gz','rt',encoding='utf-8') as handle:
                rows=[json.loads(line)for line in handle]
            self.assertEqual([r['type']for r in rows],['channel','group'])
            self.assertEqual(rows[0]['title'],'تهران')
            with (out/'channels-00001.csv').open('r',encoding='utf-8-sig',newline='') as handle:
                rows=list(csv.DictReader(handle))
            self.assertEqual(rows[1]['title'],"'=CMD()")
            self.assertEqual(rows[0]['username'],'a')


if __name__=='__main__':unittest.main()

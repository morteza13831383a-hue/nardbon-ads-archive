import gzip
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from html_core import Document
import archive_html
import niche_listing_sweep as sweep

AD='cmt00otab000go401lfjfusln'
CUTOFF='2026-11-13T00:00:00+00:00'

def page_html():
    listing={'@type':'CollectionPage','mainEntity':{'@type':'ItemList','numberOfItems':31,
             'itemListElement':[{'@type':'ListItem','position':1,'url':'https://tgadsspy.com/ads/'+AD,'name':'عنوان'}]}}
    return ('<script type="application/ld+json">'+json.dumps(listing,ensure_ascii=False)+'</script>'
            '<li class="[content-visibility:auto]"><a href="/ads/'+AD+'">'
            '<p class="font-bold">عنوان</p><div class="line-clamp-4">متن تبلیغ</div>'
            '<img src="/m/ad.jpg"><time datetime="2026-08-19T11:37:35.556Z">old</time>'
            '</a></li>')

class ListingSweepTests(unittest.TestCase):
    def test_card_import_uses_listing_country_date_and_fields(self):
        url='https://telegramadsarchive.com/ads?geo=IR&niche=fashion&page=1'
        records,count,missing,future=sweep.card_rows(Document(page_html()),url,'fashion',CUTOFF)
        self.assertEqual((count,missing,future),(31,[],[]))
        row=records[AD]
        self.assertEqual(row['geo'],'IR')
        self.assertEqual(row['record_detail'],'listing')
        self.assertEqual(row['title'],'عنوان')
        self.assertEqual(row['text'],'متن تبلیغ')
        self.assertEqual(row['first_seen'],'2026-08-19T11:37:35.556Z')
        self.assertEqual(row['media_src'],'https://telegramadsarchive.com/m/ad.jpg')
        self.assertEqual(row['source_url'],'https://telegramadsarchive.com/ads/'+AD)

    def test_page_claim_released_to_another_host_and_deduped(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'db.sqlite3'
            first=sweep.db_open(path)
            first.execute("INSERT INTO pages(niche,page)VALUES('fashion',1)");first.commit()
            second=sweep.db_open(path)
            claim=sweep.claim(first,'tgads.biz')
            self.assertIsNone(sweep.claim(second,'telegramstats.net'))
            sweep.release(first,*claim,'blocked')
            new_claim=sweep.claim(second,'telegramstats.net')
            self.assertEqual(new_claim[:2],('fashion',1))
            row=sweep.card_rows(Document(page_html()),'https://telegramstats.net/ads?geo=IR&niche=fashion&page=1','fashion',CUTOFF)[0]
            self.assertEqual(sweep.finish_page(second,'telegramstats.net','fashion',1,new_claim[2],row,31,[],[]),1)
            self.assertEqual(first.execute('SELECT COUNT(*) FROM records').fetchone()[0],1)
            self.assertEqual(sweep.finish_page(first,'tgads.biz','fashion',1,claim[2],row,31,[],[]),0)
            first.close();second.close()

    def test_listing_part_does_not_replace_richer_detail(self):
        with tempfile.TemporaryDirectory() as temp:
            work=Path(temp);db=archive_html.connect(work,CUTOFF)
            url='https://telegramadsarchive.com/ads/'+AD
            rich={'id':AD,'geo':'IR','source_url':url,'first_seen':'2026-08-19T11:37:35.556Z',
                  'title':'عنوان کامل','text':'متن کامل','advertiser':'آگهی‌دهنده','archive_cutoff':CUTOFF,
                  'collected_at':'2026-11-17T00:00:00Z','details':{'placements':3}}
            db.execute('INSERT INTO records VALUES(?,?)',(AD,json.dumps(rich,ensure_ascii=False)));db.commit()
            listing=sweep.card_rows(Document(page_html()),'https://telegramadsarchive.com/ads?geo=IR&niche=fashion&page=1','fashion',CUTOFF)[0][AD]
            part=work/'ads-test.jsonl.gz'
            with gzip.open(part,'wt',encoding='utf-8') as f:f.write(json.dumps(listing,ensure_ascii=False)+'\n')
            archive_html.import_records(db,[part])
            merged=json.loads(db.execute('SELECT payload FROM records WHERE id=?',(AD,)).fetchone()[0])
            self.assertEqual(merged['advertiser'],'آگهی‌دهنده')
            self.assertEqual(merged['details'],{'placements':3})
            db.close()

if __name__=='__main__':unittest.main()

import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import verified_sitemap_ads as v

ID='c'+'a'*24
URL='https://telegramads.it/ads/'+ID
CUTOFF='2026-11-13T08:53:18+00:00'

def creative(identifier=ID,country='Iran',when='2026-07-02T12:00:00Z'):
    doc={'@type':'CreativeWork','identifier':identifier,'contentLocation':{'name':country},'datePublished':when,'name':'Example'}
    return '<script type="application/ld+json">'+json.dumps(doc)+'</script><article>Example</article>'

class MirrorVerification(unittest.TestCase):
    def test_requires_identity_country_and_date(self):
        self.assertEqual(v.verified(creative(),URL,CUTOFF)[0]['id'],ID)
        self.assertIsNone(v.verified(creative(country='Canada'),URL,CUTOFF)[0])
        self.assertIsNone(v.verified(creative(when='2026-12-01T00:00:00Z'),URL,CUTOFF)[0])
        with self.assertRaises(ValueError):v.verified(creative(identifier='other'),URL,CUTOFF)

    def test_save_requires_active_primary_job_and_survives_duplicate(self):
        with sqlite3.connect(':memory:') as db:
            db.executescript('CREATE TABLE records(id TEXT PRIMARY KEY,payload TEXT);CREATE TABLE queue(url TEXT PRIMARY KEY,status TEXT,via TEXT);')
            row=v.verified(creative(),URL,CUTOFF)[0]
            self.assertFalse(v.save(db,row,'telegramads.it',0))
            db.execute("INSERT INTO queue VALUES(?,'pending','sitemap')",(v.BASE+ID,));db.commit()
            self.assertTrue(v.save(db,row,'telegramads.it',0))
            self.assertFalse(v.save(db,row,'telegramads.it',0))
            self.assertEqual(db.execute('SELECT count(*) FROM records').fetchone()[0],1)

    def test_distinct_claims_and_fixed_cutoff(self):
        with TemporaryDirectory() as tmp, v.connect(Path(tmp)/'work.sqlite3') as db, sqlite3.connect(':memory:') as spy:
            spy.execute('CREATE TABLE meta(key TEXT,value TEXT)')
            spy.execute("INSERT INTO meta VALUES('cutoff',?)",(CUTOFF,))
            self.assertEqual(v.check_cutoff(db,spy),CUTOFF)
            spy.execute("UPDATE meta SET value='2026-12-13T00:00:00+00:00'")
            with self.assertRaises(ValueError):v.check_cutoff(db,spy)
            db.executemany("INSERT INTO jobs(id,band,sort) VALUES(?,0,?)",[(ID,'1'),('c'+'b'*24,'2')]);db.commit()
            self.assertNotEqual(v.claim(db,'one')[0],v.claim(db,'two')[0])
            self.assertIsNone(v.claim(db,'three'))

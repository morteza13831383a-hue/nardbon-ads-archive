import gzip
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import channels_html as ch
import channel_document
import fast_iran_channels_crawler as worker
import channel_import


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.db = ch.connect(self.work)
        self.addCleanup(self.db.close)
        worker.prepare(self.db)

    def test_claim_crash_recovery_and_import(self):
        url = ch.BASE + '/channels/sports'
        with self.db:
            ch.enqueue(self.db,url)
        self.assertEqual(worker.claim(self.db,3),[(url,'channel')])
        self.assertEqual(worker.claim(self.db,3),[])
        worker.prepare(self.db)
        self.assertEqual(worker.claim(self.db,1),[(url,'channel')])
        body = '<h1>Sport</h1><meta property="og:description" content="کانال فارسی"><a href="/channels/Other">More</a>'
        with self.db:
            ch.consume(self.db,url,'channel',body,document=channel_document.parse)
        out = self.work/'parts'
        worker.flush(self.db,out)
        worker.flush(self.db,out)
        part, = out.glob('*.gz')
        record = json.loads(gzip.decompress(part.read_bytes()))
        self.assertEqual(record['related_channels'],['other'])
        self.assertTrue(record['has_persian_script'])
        self.assertFalse(record['iran_geo_tag'])
        catalog = {}
        with patch.object(channel_import,'HTML_DIR',out):
            channel_import.import_html_buckets(catalog,{'rows':{}},lambda _:None,ch.now)
        self.assertIn('sports',catalog)

    def test_failed_flush_retains_outbox_and_claim_is_released(self):
        with self.db:
            self.db.execute("insert into channels values('sports','{}')")
        with patch.object(Path,'replace',side_effect=OSError('full disk')):
            with self.assertRaises(OSError):
                worker.flush(self.db,self.work/'out')
        self.assertEqual(self.db.execute('select count(*) from channel_outgoing').fetchone()[0],1)

    def test_language_country_backend_parity(self):
        url = ch.BASE+'/channels/example'
        for tag,country,confirmed in [('Farsi','AF',False),('Persian','IR',True)]:
            entity={'url':url,'mainEntity':{'inLanguage':tag,'address':{'addressCountry':country}}}
            html='<h1>News</h1><script type="application/ld+json">'+json.dumps(entity)+'</script>'
            for backend in (ch.Document,channel_document.parse):
                row=ch.extract(backend(html),url)
                self.assertEqual(row['language'],'fa')
                self.assertEqual(row['iran_geo_tag'],confirmed)
        with patch.object(channel_document,'LexborHTMLParser',None):
            self.assertIsInstance(channel_document.parse(html),ch.Document)

    def test_archived_challenge_prevents_network_access(self):
        path=self.work/'archive'
        with sqlite3.connect(path) as db:
            db.executescript("create table meta(key,value);insert into meta values('last_stop_reason','Cloudflare challenge');")
        with patch.object(worker,'status',return_value={'halted':False}):
            self.assertTrue(worker.held(self.db,path))

    def test_worker_restores_claims_on_interrupted_fetch(self):
        from types import SimpleNamespace
        args = SimpleNamespace(work_dir=self.work, outbox=self.work/'parts',
                               ads_checkpoint=self.work/'archive',transport='stdlib',
                               seed=[],advertisers=None,keywords=[],catalog=None,batch=4)
        with self.db:
            ch.enqueue(self.db,'/channels/example')
        with patch.object(worker,'held',return_value=False), patch.object(ch,'seed'), \
             patch.object(worker.signal,'signal'), patch.object(worker,'Client') as factory:
            factory.return_value.get.side_effect = KeyboardInterrupt
            self.assertEqual(worker.collect(args),0)
        self.assertEqual(self.db.execute('select status from queue').fetchone()[0],'pending')

    def test_seed_inputs_and_foreign_origin(self):
        seeds=self.work/'seeds'
        seeds.write_text('@HelloWorld\nhttps://other.example/channels/ignoreme\n')
        advertisers=self.work/'advs'
        advertisers.write_text('@Example\n')
        terms=self.work/'terms'
        terms.write_text('ورزش\n')
        catalog=self.work/'catalog'
        catalog.write_text(json.dumps({'fields':['u','t'],'rows':[['sports_net','ورزش'],['music_net','Music']]}))
        worker.seed_files(self.db,[seeds],advertisers,[terms],catalog)
        paths = {url.removeprefix(ch.BASE) for url, in self.db.execute('select url from queue')}
        self.assertEqual(paths,{'/channels/helloworld','/channels/sports_net','/advertisers/tg-example/channels'})

    def test_listing_only_saves_unclassified_handles_and_follows_pagination(self):
        url = ch.BASE + '/channels?lang=fa'
        with self.db:
            ch.enqueue(self.db, url, 'listing')
            ch.enqueue(self.db, '/channels/Example', 'channel')
            worker.consume_listing_handles(self.db, url,
                '<main><a href="/channels/Example">Example</a>'
                '<a href="/channels?lang=fa&amp;page=2">Next</a></main>')
        record = json.loads(self.db.execute('SELECT payload FROM channels').fetchone()[0])
        self.assertEqual(record['username'], 'example')
        self.assertEqual(record['classification'], 'unclassified')
        self.assertNotIn('language', record)
        self.assertEqual(worker.claim(self.db, 3, listings_only=True), [(url+'&page=2', 'listing')])

import csv
import gzip
import hashlib
import io
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import archive_html as a
from html_core import Document, Robots, Client
import archive_publish as p

CUTOFF='2026-11-13T00:00:00+00:00'
URL=a.BASE+'/ads/abc'


def html(country='IR',date='2026-11-12T00:00:00Z'):
    ld={'@type':'CreativeWork','identifier':'abc','name':'آزمون','description':'متن فارسی',
        'contentLocation':{'name':country},'datePublished':date,
        'dateModified':date,'sponsor':{'name':'@test','sameAs':'https://t.me/test'},
        'associatedMedia':{'contentUrl':a.BASE+'/m/test.jpg'}}
    return '<article><script type="application/ld+json">'+json.dumps(ld)+'</script><a rel="nofollow sponsored" class="uppercase" href="https://t.me/test?start=ad">View Bot</a><div><div>Total impressions</div><div>42</div></div><section aria-label="Impressions by day"><span title="42 impressions · 11/12/2026"></span></section><time title="2025-01-01T00:00:00Z"></time><section><h2>Where this ad ran</h2><a href="/channels/example">کانال نمونه 12 placements</a></section></article>'


class FakeClient:
    robots=Robots('User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /ads?*')
    def __init__(self,pages): self.pages,self.calls=pages,[]
    def get(self,url):
        self.calls.append(url)
        value=self.pages[url]
        if isinstance(value,Exception): raise value
        return value


class Tests(unittest.TestCase):
    def test_other_writer_can_deliver_while_fetching(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = a.connect(Path(tmp), CUTOFF)
            mirror = sqlite3.connect(Path(tmp) / 'checkpoint.sqlite3', timeout=0)
            self.addCleanup(db.close)
            self.addCleanup(mirror.close)
            a.enqueue(db, URL, 'detail', 1, via='test_concurrency')
            db.commit()
            client = FakeClient({URL: html()})
            def fetch(url):
                self.assertFalse(db.in_transaction)
                with mirror:
                    mirror.execute("INSERT INTO meta VALUES ('test_mirror_delivery', '1')")
                return client.pages[url]
            client.get = fetch
            a.crawl(db, client, 1, 30, only_via=['test_concurrency'])
            self.assertEqual(db.execute("SELECT status,tries FROM queue WHERE url=?", (URL,)).fetchone(), ('iran', 1))
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='test_mirror_delivery'").fetchone(), ('1',))

    def test_extract_iran_and_cutoff(self):
        row,status=a.extract(Document(html()),URL,CUTOFF)
        self.assertEqual(status,'iran')
        self.assertEqual(row['cta_url'],'https://t.me/test?start=ad')
        self.assertEqual(row['details']['metrics']['Total impressions'],['42'])
        self.assertEqual(row['details']['daily_impressions'],[{'date':'2026-11-12','impressions':42,'label':'42 impressions · 11/12/2026'}])
        self.assertFalse(row['details']['placements_complete'])
        self.assertEqual(len(row['details']['placements_shown']),1)
        self.assertEqual(a.extract(Document(html('US')),URL,CUTOFF),(None,'not_iran'))
        self.assertEqual(a.extract(Document(html(date='2026-11-14T00:00:00Z')),URL,CUTOFF),(None,'after_cutoff'))
        with self.assertRaises(ValueError):a.extract(Document('<h1>Cloudflare challenge</h1>'),URL,CUTOFF)
    def test_sitemap_scope_and_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=a.connect(Path(tmp),CUTOFF)
            xml='<urlset><url><loc>'+URL+'</loc></url><url><loc>'+URL+'</loc></url><url><loc>'+a.BASE+'/channels/x</loc></url><url><loc>https://evil.test/ads/a</loc></url><url><loc>'+a.BASE+'/api/v1/ads</loc></url></urlset>'
            a.sitemap(db,xml)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM queue WHERE kind='detail'").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM queue WHERE url LIKE '%api/%'").fetchone()[0],0)
            db.close()
    def test_resume_uses_fixed_cutoff_and_does_not_recrawl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            db=a.connect(path,CUTOFF)
            db.execute('DELETE FROM queue')
            a.enqueue(db,a.BASE+'/countries/ir','listing',0)
            listing='<a href="/ads/abc"><span aria-label="Iran">Iran</span></a>'
            client=FakeClient({a.BASE+'/countries/ir':listing,URL:html(),a.BASE+'/channels/example/advertisers':'<html></html>'})
            a.crawl(db,client,1,20)
            db.close()
            db=a.connect(path,'2030-01-01T00:00:00Z')
            a.crawl(db,client,10,20)
            self.assertEqual(client.calls,[a.BASE+'/countries/ir',URL,a.BASE+'/channels/example/advertisers'])
            self.assertEqual(a.report(db)['cutoff'],CUTOFF)
            self.assertEqual(a.report(db)['iran_ads_extracted'],1)
            self.assertFalse(a.report(db)['complete_iran_archive_verified'])
            db.close()
    def test_parser_failure_remains_visible_and_retries_next_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=a.connect(Path(tmp),CUTOFF)
            db.execute('DELETE FROM queue')
            a.enqueue(db,URL,'detail',1)
            client=FakeClient({URL:'<h1>wrong</h1>'})
            for i in range(3):a.crawl(db,client,10,20)
            self.assertEqual(len(client.calls),3)
            self.assertEqual(a.report(db)['queue_status'],{'failed':1})
            self.assertFalse(a.report(db)['discovered_queue_exhausted'])
            db.close()
    def test_import_deduplicates_and_marks_collected_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            db=a.connect(path,CUTOFF)
            row,_=a.extract(Document(html()),URL,CUTOFF)
            file=path/'ads-00001.jsonl.gz'
            with gzip.open(file,'wt',encoding='utf-8') as handle:
                handle.write(json.dumps(row)+'\n'+json.dumps(row)+'\n')
            a.import_records(db,[file])
            self.assertEqual(a.report(db)['iran_ads_extracted'],1)
            self.assertEqual(db.execute('SELECT status FROM queue WHERE url=?',(URL,)).fetchone()[0],'iran')
            db.close()
    def test_403_stops_and_is_reported(self):
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as tmp:
            db=a.connect(Path(tmp),CUTOFF)
            db.execute('DELETE FROM queue')
            a.enqueue(db,URL,'detail',1)
            client=FakeClient({URL:HTTPError(URL,403,'Forbidden',{},None)})
            a.crawl(db,client,100,20)
            self.assertEqual(len(client.calls),1)
            self.assertIn('403',a.report(db)['last_stop_reason'])
            self.assertEqual(a.report(db)['queue_status'],{'pending':1})
            db.close()
    def test_related_graph_uses_advertiser_and_skips_niche_recommendations(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=a.connect(Path(tmp),CUTOFF)
            record={'source_url':URL,'advertiser':'@test','details':{'article_links':[
                {'href':a.BASE+'/ads/related','text':'@test Another creative'},
                {'href':a.BASE+'/ads/random','text':'@other Unrelated niche creative'},
                {'href':a.BASE+'/advertisers/tg-test','text':'See all by · @test →'},
                {'href':a.BASE+'/channels/test','text':'@test Channel'},
            ]}}
            a.enqueue(db,a.BASE+'/ads/related','detail',30)
            a.discover_related(db,record)
            self.assertEqual(db.execute('SELECT priority FROM queue WHERE url=?',(a.BASE+'/ads/related',)).fetchone()[0],2)
            self.assertIsNone(db.execute('SELECT 1 FROM queue WHERE url=?',(a.BASE+'/ads/random',)).fetchone())
            self.assertEqual(db.execute('SELECT kind,priority FROM queue WHERE url=?',(a.BASE+'/advertisers/tg-test',)).fetchone(),('listing',3))
            db.execute("UPDATE queue SET status='iran' WHERE url=?",(a.BASE+'/ads/related',))
            a.discover_related(db,record)
            self.assertEqual(db.execute('SELECT status FROM queue WHERE url=?',(a.BASE+'/ads/related',)).fetchone()[0],'iran')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM discovery_edges').fetchone()[0],2)
            db.close()
    def test_near_cutoff_cuid_outlier_is_deferred_not_dropped(self):
        import archive_yield as y
        with tempfile.TemporaryDirectory() as tmp:
            db=a.connect(Path(tmp),'2026-05-16T00:00:00Z')
            url=a.BASE+'/ads/cmpaeawsn0nvhm6b4tgp9bdbp'
            a.enqueue(db,url,'detail',30,via='sitemap')
            y.reprioritize(db,'2026-05-16T00:00:00Z')
            self.assertEqual(db.execute('SELECT status,month,priority FROM queue WHERE url=?',(url,)).fetchone(),('pending','~late',29))
            db.close()
    def test_only_via_selects_inherited_ad_and_leaves_other_work_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=a.connect(Path(tmp),CUTOFF)
            db.execute('DELETE FROM queue')
            parent=a.BASE+'/advertisers/tg-test'
            a.enqueue(db,parent,'listing',3,via='advertiser')
            a.discover(db,Document('<a href="/ads/abc"><span aria-label="Iran">Iran</span></a>'),parent)
            db.execute("UPDATE queue SET status='done' WHERE url=?",(parent,))
            a.enqueue(db,a.BASE+'/ads/outside','detail',0,via='sitemap')
            client=FakeClient({URL:html()})
            a.crawl(db,client,1,20,only_via=['advertiser'])
            self.assertEqual(client.calls,[URL])
            self.assertEqual(db.execute('SELECT via,status FROM queue WHERE url=?',(URL,)).fetchone(),('advertiser>ad','iran'))
            self.assertEqual(db.execute('SELECT status FROM queue WHERE url=?',(a.BASE+'/ads/outside',)).fetchone()[0],'pending')
            db.close()
    def test_robots_wildcard(self):
        robots=Robots('User-agent: *\nAllow: /\nDisallow: /ads?*\nDisallow: /api/\nCrawl-delay: 4')
        self.assertFalse(robots.allowed(a.BASE+'/ads?geo=IR'))
        self.assertTrue(robots.allowed(URL))
        self.assertFalse(robots.allowed(a.BASE+'/api/v1/ads'))
        self.assertEqual(robots.delay,4)
    def test_only_one_source_worker_per_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            first=a.source_lock(Path(directory))
            try:
                with self.assertRaises(RuntimeError):a.source_lock(Path(directory))
            finally:
                first.close()
            second=a.source_lock(Path(directory))
            second.close()
    def test_transport_does_not_retry_rate_limit(self):
        from urllib.error import HTTPError
        from unittest.mock import Mock
        client=Client(a.BASE,4)
        client.robots=Robots('User-agent: *\nAllow: /')
        client.opener=Mock()
        client.opener.open.side_effect=HTTPError(URL,429,'Too Many Requests',{'Retry-After':'120'},None)
        with patch('html_core.time.sleep'), self.assertRaises(HTTPError):
            client.get(URL)
        self.assertEqual(client.opener.open.call_count,1)
    def test_transport_rejects_api_and_external_redirect_target(self):
        client=Client(a.BASE)
        client.robots=Robots('User-agent: *\nAllow: /')
        for url in (a.BASE+'/api/v1/ads','https://evil.test/ads/abc'):
            with self.assertRaises(ValueError):client.get(url)
    def test_export_checksums_and_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);db=a.connect(path,CUTOFF)
            row,_=a.extract(Document(html()),URL,CUTOFF)
            db.execute('INSERT INTO records VALUES(?,?)',(row['id'],json.dumps(row)))
            report=a.export(db,path)
            self.assertEqual(len(report['files']),2)
            for entry in report['files']:
                content=(path/'exports'/entry['name']).read_bytes()
                self.assertEqual(hashlib.sha256(content).hexdigest(),entry['sha256'])
            gz=next((path/'exports').glob('*.jsonl.gz'))
            self.assertEqual(json.loads(gzip.decompress(gz.read_bytes()))['geo'],'IR')
            csvfile=next((path/'exports').glob('*.csv'))
            self.assertTrue(csvfile.read_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertIn("'@test",csvfile.read_text(encoding='utf-8-sig'))
            db.close()
    def test_merge_preserves_fresher_api_data_and_media(self):
        row,_=a.extract(Document(html()),URL,CUTOFF)
        row['html_sha256']='test'
        ads={}
        p.merge_record(ads,row)
        self.assertEqual(ads['abc']['media_status'],'pending')
        ads['abc'].update(last_seen='2026-11-15T00:00:00Z',media_file='media/abc.webp',media_status='ok',title='Fresh API title')
        p.merge_record(ads,row)
        self.assertEqual(ads['abc']['last_seen'],'2026-11-15T00:00:00Z')
        self.assertEqual(ads['abc']['title'],'Fresh API title')
        self.assertEqual(ads['abc']['media_file'],'media/abc.webp')
        self.assertEqual(ads['abc']['media_status'],'ok')


class SnapshotRevisit(unittest.TestCase):
    def test_done_snapshot_without_totals_is_requeued_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=a.connect(Path(tmp),CUTOFF)
            url=a.BASE+'/countries/ir/snapshot/2026-09'
            db.execute("UPDATE queue SET status='done' WHERE url=?",(url,)); db.commit()
            import archive_yield as ay
            self.assertEqual(ay.requeue_missing_snapshots(db),1)
            self.assertEqual(db.execute('SELECT status,priority FROM queue WHERE url=?',(url,)).fetchone(),('pending',0))
            ay.parse_snapshot(db,url,'16,687 Telegram ad creatives targeting Iran during September 2026 Top niches VPN 2,331 Top advertisers')
            db.execute("UPDATE queue SET status='done' WHERE url=?",(url,)); db.commit()
            self.assertEqual(ay.requeue_missing_snapshots(db),0)


class StrategyOrderTests(unittest.TestCase):
    def test_ad_links_from_discovered_advertiser_are_verified_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=a.connect(Path(tmp),CUTOFF)
            parent=a.BASE+'/advertisers/example'
            a.enqueue(db,parent,'listing',5,via='advertiser_via_channel')
            ad=a.BASE+'/ads/'+('c'+'a'*24)
            a.discover(db,Document('<a href="'+ad+'">Ad</a>'),parent)
            self.assertEqual(db.execute('SELECT priority,via FROM queue WHERE url=?',(ad,)).fetchone(),
                             (3,'advertiser_via_channel>ad'))
            db.close()

    def test_discovered_advertisers_precede_more_channel_graph_pages(self):
        import archive_yield as ay
        self.assertEqual(ay.classify_listing('/channels/example/advertisers','/advertisers/ordinary','English')[0],5)
        self.assertEqual(ay.classify_listing('/channels/example/advertisers','/advertisers/persian','فارسی')[0],4)
        self.assertEqual(ay.classify_listing('/channels/example','/channels/another/advertisers','')[0],6)


class ChannelPageSeeds(unittest.TestCase):
    def test_live_export_refresh_seeds_new_links_without_reopening_completed_ads(self):
        import archive_yield as ay
        with tempfile.TemporaryDirectory()as tmp:
            d=Path(tmp)/'exports';d.mkdir();db=a.connect(Path(tmp),CUTOFF)
            ids=['c'+'a'*24,'c'+'b'*24]
            for limit in(1,2):
                with gzip.open(d/'channels.jsonl.gz','wt',encoding='utf-8')as f:
                    f.write(json.dumps({'classification':'persian_candidate','links':[{'url':a.BASE+'/ads/'+v}for v in ids[:limit]]})+'\n')
                ay.seed_from_channel_pages(db,a.enqueue,d)
                if limit==1:db.execute("UPDATE queue SET status='not_iran'WHERE url=?",(a.BASE+'/ads/'+ids[0],));db.commit()
            self.assertEqual(db.execute('SELECT status FROM queue WHERE url=?',(a.BASE+'/ads/'+ids[0],)).fetchone()[0],'not_iran')
            self.assertEqual(db.execute('SELECT priority,via,status FROM queue WHERE url=?',(a.BASE+'/ads/'+ids[1],)).fetchone(),(1,'persian_channel_page','pending'))
            db.close()

    def test_seed_cache_persists_and_failed_files_retry_atomically(self):
        import archive_yield as ay
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); d=root/'exports'; d.mkdir()
            path=d/'channels.jsonl.gz'
            row={'classification':'persian_candidate','links':[{'url':a.BASE+'/ads/'+'c'+'a'*24}]}
            def write(lines):
                with gzip.open(path,'wt',encoding='utf-8') as f:f.write(lines)
            write(json.dumps(row)+'\n')
            db=a.connect(root,CUTOFF)
            self.assertEqual(ay.seed_from_channel_pages(db,a.enqueue,d),1)
            db.close();db=a.connect(root,CUTOFF)
            with patch.object(a,'enqueue',side_effect=AssertionError('unchanged file parsed')):
                self.assertEqual(ay.seed_from_channel_pages(db,a.enqueue,d),0)
            row['links'][0]['url']=a.BASE+'/ads/'+'c'+'b'*24
            write(json.dumps(row)+'\nINVALID\n')
            with self.assertRaises(ValueError):ay.seed_from_channel_pages(db,a.enqueue,d)
            self.assertIsNone(db.execute('SELECT 1 FROM queue WHERE url=?',(row['links'][0]['url'],)).fetchone())
            write(json.dumps(row)+'\n')
            self.assertEqual(ay.seed_from_channel_pages(db,a.enqueue,d),1)
            db.close()

    def test_duplicate_enqueue_does_not_write_but_better_evidence_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=a.connect(Path(tmp),CUTOFF)
            a.enqueue(db,URL,'detail',30,via='sitemap')
            before=db.total_changes
            a.enqueue(db,URL,'detail',30,via='other')
            self.assertEqual(db.total_changes,before)
            a.enqueue(db,URL,'detail',1,via='persian_channel_page')
            self.assertEqual(db.execute('SELECT priority,via FROM queue WHERE url=?',(URL,)).fetchone(),(1,'persian_channel_page'))
            db.close()

    def test_observed_yield_counts_retries_and_excludes_unrequested_known_rows(self):
        import archive_yield as ay
        with tempfile.TemporaryDirectory() as tmp:
            db=a.connect(Path(tmp),CUTOFF)
            ay.RequestLedger(db,None)
            for suffix in ('a','b'):
                a.enqueue(db,a.BASE+'/ads/'+suffix,'detail',1,via='route')
            db.execute("UPDATE queue SET status='iran' WHERE via='route'")
            for status in (503,200):
                db.execute('INSERT INTO source_requests(url,via,status,at) VALUES(?,?,?,?)',
                           (a.BASE+'/ads/a','route',status,'2026-11-15'))
            result=ay.coverage_report(db)['observed_http_by_strategy']
            self.assertEqual(result,[{'via':'route','requests':2,'iran':1,'iran_per_100':50.0}])
            db.close()

    def test_ad_runner_seeds_at_each_slice_boundary(self):
        import archive_collect as runner
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory()as tmp:
            db=a.connect(Path(tmp),CUTOFF)
            argv=['archive_collect','--work-dir',tmp,'--repo','owner/repo','--max-slices','2','--channel-import-dir',tmp]
            with patch.object(sys,'argv',argv),patch.object(runner.archive,'connect',return_value=db),patch.object(runner,'publish'),patch.object(runner.subprocess,'run',return_value=SimpleNamespace(returncode=0)),patch.object(runner.archive_yield,'seed_from_channel_pages')as seed:
                runner.main()
            self.assertEqual(seed.call_count,4)

    def test_ads_linked_on_persian_channel_pages_are_queued_first(self):
        import archive_yield as ay
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp)/'imp'; d.mkdir()
            ad='c'+'a'*24; other='c'+'b'*24
            rows=[{'username':'fa_chan','classification':'persian_candidate','links':[{'url':a.BASE+'/ads/'+ad}]},
                  {'username':'en_chan','classification':'unclassified','links':[{'url':a.BASE+'/ads/'+other}]}]
            with gzip.open(d/'channels-00.jsonl.gz','wt',encoding='utf-8') as f:
                for r in rows: f.write(json.dumps(r)+'\n')
            db=a.connect(Path(tmp),CUTOFF)
            self.assertEqual(ay.seed_from_channel_pages(db,a.enqueue,d),1)
            self.assertEqual(db.execute('SELECT priority,via FROM queue WHERE url=?',(a.BASE+'/ads/'+ad,)).fetchone(),(1,'persian_channel_page'))
            self.assertIsNone(db.execute('SELECT 1 FROM queue WHERE url=?',(a.BASE+'/ads/'+other,)).fetchone())


if __name__=='__main__':unittest.main()


# Synthetic HTTP outcomes must never change a running collector's host budget.
def setUpModule():
    global _isolated_budget_dir,_isolated_budget_env
    import os,tempfile
    from unittest.mock import patch
    _isolated_budget_dir=tempfile.TemporaryDirectory()
    _isolated_budget_env=patch.dict(os.environ,{'CRAWL_BUDGET_DIR':_isolated_budget_dir.name})
    _isolated_budget_env.start()

def tearDownModule():
    _isolated_budget_env.stop();_isolated_budget_dir.cleanup()

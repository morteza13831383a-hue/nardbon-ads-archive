import json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import channel_priority as priority,channels_html as spy,channel_slices as slices,archive_html as ads,archive_yield as ay
from html_core import Document

class PriorityTests(unittest.TestCase):
    def test_listing_keeps_eligibility_distinct_from_cabinet_and_unknown(self):
        html='<table><tr><th>Channel</th><th>Members</th><th>Cabinet type</th><th>Sponsored eligible</th></tr><tr><td><a href="/channels/example">نمونه</a></td><td>22.5K</td><td>EUR</td><td>yes</td></tr></table>'
        row=spy.extract_listing(Document(html),spy.BASE+'/channels?lang=fa')[0]
        self.assertEqual(row['members'],22500);self.assertTrue(row['sponsored_eligible']);self.assertEqual(row['cabinet_type'],'EUR')
        self.assertIsNone(spy.listing_eligibility({}))
    def test_priority_order_and_no_refetch_of_completed_profiles(self):
        with tempfile.TemporaryDirectory()as tmp:
            db=spy.connect(Path(tmp));slices.ensure(spy,db)
            rows=[{'username':u,'classification':'persian_candidate','members':n,'sponsored_eligible':e,'cabinet_type':c,'record_detail':'listing'}for u,n,e,c in [('large',1000000,None,None),('eligible',100,True,None),('cabinet',200,None,'EUR'),('small',100,None,None)]]
            slices.persist(spy,db,rows);self.assertEqual(priority.next_job(db)[0],'eligible')
            db.execute("update priority_profiles set status='done'where username='eligible'");self.assertEqual(priority.next_job(db)[0],'large')
            self.assertIsNone(db.execute("select 1 from priority_profiles where username='small'").fetchone())
            full={'username':'large','classification':'persian_candidate','record_detail':'profile','profile_fetched_at':spy.now(),'meta':{},'links':[],'source_url':'profile','members':1000000}
            slices.persist(spy,db,[full]);slices.persist(spy,db,[rows[0]])
            result=json.loads(db.execute("select payload from channels where username='large'").fetchone()[0]);self.assertEqual(result['record_detail'],'profile');self.assertEqual(result['source_url'],'profile')
            client=SimpleNamespace(get=lambda u:self.fail('already saved profile refetched'))
            priority.step(spy,db,client);self.assertEqual(db.execute("select status from priority_profiles where username='large'").fetchone()[0],'already_fetched');db.close()
    def test_profile_slot_does_not_starve_listings(self):
        with tempfile.TemporaryDirectory()as tmp:
            db=spy.connect(Path(tmp));slices.ensure(spy,db);slices.persist(spy,db,[{'username':'large','classification':'persian_candidate','members':50000,'record_detail':'listing'}]);db.commit()
            class Client:
                def get(self,url):return '<table></table>'
            with patch.object(priority,'step',return_value=True)as profile:
                for _ in range(5):slices.step(spy,db,Client())
                self.assertEqual(profile.call_count,1)
            db.close()
    def test_priority_profile_ads_have_separate_strategy_and_keep_iran_verification(self):
        import gzip
        with tempfile.TemporaryDirectory()as tmp:
            folder=Path(tmp)/'exports';folder.mkdir();ad='c'+'a'*24
            with gzip.open(folder/'channels.jsonl.gz','wt')as h:h.write(json.dumps({'classification':'persian_candidate','ad_discovery_via':priority.VIA,'links':[{'url':spy.BASE+'/ads/'+ad}]})+'\n')
            db=ads.connect(Path(tmp),'2026-11-13T00:00:00+00:00');ay.seed_from_channel_pages(db,ads.enqueue,folder)
            self.assertEqual(db.execute('select via,status,priority from queue where url=?',(spy.BASE+'/ads/'+ad,)).fetchone(),(priority.VIA,'pending',1));self.assertEqual(db.execute('select count(*)from records').fetchone()[0],0);db.close()
    def test_ledger_counts_actual_requests_separately_from_already_known_queue_rows(self):
        from urllib.request import Request
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory()as tmp:
            db=ads.connect(Path(tmp),'2026-11-13T00:00:00+00:00');url=spy.BASE+'/ads/'+('c'+'a'*24);ads.enqueue(db,url,'detail',1,via=priority.VIA)
            class Opener:
                def open(self,request,**kwargs):raise HTTPError(url,404,'missing',{},None)
            ledger=ay.RequestLedger(db,Opener())
            with self.assertRaises(HTTPError):ledger.open(Request(url))
            self.assertEqual(db.execute('select via,status from source_requests').fetchone(),(priority.VIA,404));db.close()

    def test_yield_counts_profile_cost_and_retries_without_known_queue_inflation(self):
        from channel_ad_yield import snapshot
        with tempfile.TemporaryDirectory()as tmp:
            ad_work=Path(tmp)/'ads';ch_work=Path(tmp)/'channels';a=ads.connect(ad_work,'2026-11-13T00:00:00+00:00');c=spy.connect(ch_work);slices.ensure(spy,c)
            url=spy.BASE+'/ads/'+('c'+'a'*24);ads.enqueue(a,url,'detail',1,via=priority.VIA)
            a.execute("update queue set status='iran'where url=?",(url,));ay.RequestLedger(a,None)
            for status in(500,200):a.execute("insert into source_requests(url,via,status,at)values(?,?,?,'2026-11-14T00:00:00Z')",(url,priority.VIA,status))
            ads.enqueue(a,spy.BASE+'/ads/'+('c'+'b'*24),'detail',1,via=priority.VIA);a.execute("update queue set status='known'where url!=?and via=?",(url,priority.VIA));a.commit()
            for _ in range(2):c.execute("insert into requests(url,status,at,via)values('profile',200,'now',?)",(priority.VIA,))
            c.commit();a.close();c.close();r=snapshot(ad_work/'checkpoint.sqlite3',ch_work/'channels.sqlite3')
            self.assertEqual(r['ad_requests'],2);self.assertEqual(r['verified_iran_ads'],1);self.assertEqual(r['iran_per_100_ad_requests'],50);self.assertEqual(r['iran_per_100_profile_plus_ad_requests'],25)

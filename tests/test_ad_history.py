from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock
import gzip
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import ad_history
import archive_publish
import sync

CUTOFF = '2026-11-13T08:53:18+00:00'
ID = 'c' + 'a' * 24


class HistoricalRoutes(TestCase):
    def test_live_quota_reserve_stops_history(self):
        fake=mock.Mock(KEY='test', API_INFO={'X-RateLimit-Limit':'600','X-RateLimit-Remaining':'150'})
        ad_history.collect({}, {}, CUTOFF, 36, fake)
        fake.SESSION.get.assert_not_called()

    @mock.patch('ad_history.time.sleep')
    def test_ignored_date_and_filtered_advertiser(self, sleep):
        ads = {'seed': {'id': 'seed', 'geo': 'IR', 'advertiser_slug': 'tg-example'}}
        response = mock.Mock(status_code=200, is_redirect=False, headers={'X-RateLimit-Remaining':'500'})
        response.json.return_value = {'data': [{'id': ID, 'geo': 'IR', 'firstSeenAt': '2026-05-11T00:00:00Z',
                                                'advertiserSlug': 'tg-example'}], 'meta': {'total': 1}}
        state = {}
        with mock.patch.object(sync, 'KEY', 'dummy'), mock.patch.dict(sync.API_INFO, {'X-RateLimit-Remaining':'550'}), mock.patch.object(sync.SESSION, 'get', return_value=response) as fetch:
            ad_history.collect(ads, state, CUTOFF, 2, sync)
            ad_history.collect(ads, state, CUTOFF, 2, sync)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(ads[ID]['source'], 'history_advertiser')
        self.assertIn('date_unsupported',state['ad_history_v1'])
        self.assertEqual(state['ad_history_v1']['last']['new'],1)

    def test_pagination(self):
        rows = [{}] * 100
        self.assertEqual(ad_history.has_another(rows, {'totalReachable':100,'total':250}, 0, 100), 'plan_limit')
        self.assertEqual(ad_history.has_another(rows, {'total':100}, 0, 100), 'read')
        self.assertEqual(ad_history.has_another(rows, {'total':250}, 0, 100), 'more')

    def test_hourly_parts_retry_and_cutoff(self):
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            p = directory / 'ads-test.jsonl.gz'
            record = {'id':ID, 'title':'title', 'text':'text', 'source_url':'https://tgadsspy.com/ads/'+ID,
                      'geo':'IR','collected_at':CUTOFF,'first_seen':'2026-04-01T00:00:00Z'}
            def write(row):
                with gzip.open(p,'wt') as out: out.write(json.dumps(row)+'\n')
            ads, state = {}, {}
            write(dict(record, geo='AE'))
            with self.assertRaises(ValueError): archive_publish.import_new_parts(ads,state,directory,CUTOFF)
            self.assertFalse(ads)
            write(dict(record,first_seen='2026-11-14T00:00:00Z'))
            self.assertEqual(archive_publish.import_new_parts(ads,state,directory,CUTOFF),(0,1))
            write(record)
            self.assertEqual(archive_publish.import_new_parts(ads,state,directory,CUTOFF),(1,1))
            self.assertEqual(archive_publish.import_new_parts(ads,state,directory,CUTOFF),(0,0))
            self.assertEqual(ads[ID]['geo'],'IR')

import datetime
import sys
import unittest
import sqlite3
import tempfile
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from tglist_collect import HARD_STOP, allowed_time, main, parse


class TGListTests(unittest.TestCase):
    def test_permission_deadline_is_inclusive_then_closed(self):
        self.assertEqual(HARD_STOP.isoformat(), '2026-12-16T23:59:59.999999+00:00')
        self.assertTrue(allowed_time(HARD_STOP))
        self.assertFalse(allowed_time(HARD_STOP + datetime.timedelta(microseconds=1)))

    def test_listing_rows_and_next_page_are_scoped_to_site(self):
        html = '''<a href="/channel/@Shop_1">فروشگاه 1,234 subscribers</a>
        <a href="/group/@Group_1">گروه 12.3k participants</a>
        <a href="https://other.example/channel/@Bad_1">خارجی 20 subscribers</a>
        <a href="/search?q=%D9%81%D8%B1%D9%88%D8%B4&page=2">Next</a>'''
        rows, next_url = parse(html, 'https://tglist.io/search?q=%D9%81%D8%B1%D9%88%D8%B4')
        self.assertEqual((rows['shop_1']['type'], rows['shop_1']['members']), ('channel', 1234))
        self.assertEqual((rows['group_1']['type'], rows['group_1']['members']), ('group', '12.3k'))
        self.assertEqual(len(rows), 2)
        self.assertTrue(next_url.endswith('page=2'))

    def test_expired_permission_prevents_all_network_setup(self):
        with patch('tglist_collect.allowed_time', return_value=False), patch('tglist_collect.build_opener') as opener, patch.object(sys, 'argv', ['tglist_collect', '--work-dir', '/tmp/unused', '--repo-root', '/tmp/unused']):
            with self.assertRaises(SystemExit):
                main()
            opener.assert_not_called()

    def test_existing_403_hold_prevents_all_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            for name in ('channel_keywords.txt', 'channel_keywords_auto.txt'):
                (root / 'data' / name).write_text('فروشگاه\n')
            work = root / 'work'
            work.mkdir()
            db = sqlite3.connect(work / 'checkpoint.sqlite3')
            db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
            db.execute("INSERT INTO meta VALUES('stop_reason','HTTP 403')")
            db.commit()
            db.close()
            args = ['tglist_collect', '--work-dir', str(work), '--repo-root', str(root)]
            with patch.object(sys, 'argv', args), patch('tglist_collect.build_opener') as opener:
                with self.assertRaises(SystemExit):
                    main()
                opener.assert_not_called()

from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from telegram_preview_worker import inspect_page


class PublicPreview(unittest.TestCase):
    header = """<div class="tgme_channel_info">
    <div class="tgme_channel_info_header_title">خبر امروز</div>
    <a class="tgme_channel_info_header_username">@sample</a>
    <div class="tgme_channel_info_description">گزارش‌های تهران</div>
    <div class="tgme_channel_info_counter"><span class="counter_value">1.2K</span>
    <span class="counter_type">subscribers</span></div></div>"""

    def test_confirm_and_classify(self):
        status,row = inspect_page(self.header,'sample',False)
        self.assertEqual(status,'channel')
        self.assertEqual((row['members'],row['language'],row['region_signal']),(1200,'fa','ir'))
        self.assertNotIn('country',row)

    def test_group_private_identity_mismatch(self):
        for body,u,expected in [(self.header.replace('subscribers','members'),'sample','group'),
                                (self.header,'different','identity_unverified'),
                                ('<title>Telegram</title>','sample','no_public_preview'),
                                ('<div class="tgme_page_extra">15 members</div>','sample','group')]:
            self.assertEqual(inspect_page(body,u,True),(expected,None))


class AdaptiveConcurrency(unittest.TestCase):
    def test_recovery_requires_clean_window(self):
        from telegram_preview_worker import Concurrency
        control = Concurrency(5)
        self.assertEqual(control.limit, 3)
        control.observe([(0, '', 'timeout')]*2)
        self.assertEqual(control.limit, 1)
        control.observe([(0, '', '200')]*399)
        self.assertEqual(control.limit, 1)
        control.observe([(0, '', '200')])
        self.assertEqual(control.limit, 2)
        control.observe([(0, '', '429')])
        self.assertEqual(control.limit, 1)

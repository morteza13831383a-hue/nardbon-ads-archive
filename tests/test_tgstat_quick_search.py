import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from tgstat_quick_search import parse
from tgstat_session import endpoint_allowed
class QuickTests(unittest.TestCase):
 def test_channel_group_and_member_counts_without_private_ids(self):
  html='<a href="https://ir.tgstat.com/channel/@Testchan">کانال 23.4k subscribers</a><a href="https://tgstat.com/chat/@Testgroup">گروه 234 participants</a><a href="https://ir.tgstat.com/channel/private123">خصوصی 12 subscribers</a>'
  rows=parse(html,'تست')
  self.assertEqual((rows['testchan']['type'],rows['testchan']['members']),('channel','23.4k'))
  self.assertEqual((rows['testgroup']['type'],rows['testgroup']['members']),('group',234))
  self.assertEqual(len(rows),2)
 def test_session_exact_endpoint(self):
  self.assertTrue(endpoint_allowed('https://tgstat.com/channels/global-search','POST'))
  self.assertFalse(endpoint_allowed('https://tgstat.com/channels/global-search','GET'))
  self.assertFalse(endpoint_allowed('https://tgstat.com/channels/global-search?q=x','POST'))

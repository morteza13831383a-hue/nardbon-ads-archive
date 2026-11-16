import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from tgstat_keyword_search import parse,keywords
from tgstat_session import endpoint_allowed
class SearchTests(unittest.TestCase):
 def test_only_matching_tgstat_profiles_are_saved(self):
  html='<a href="https://ir.tgstat.com/channel/@Aaaa/stat">فروشگاه</a><a href="https://ir.tgstat.com/chat/@Bbbb/stat">گروه</a><a href="https://evil.test/channel/@Badg/stat">bad</a>'
  self.assertEqual(list(parse(html,'channel','فروشگاه')),['aaaa'])
  self.assertEqual(list(parse(html,'chat','فروشگاه')),['bbbb'])
 def test_cookie_boundary(self):
  self.assertTrue(endpoint_allowed('https://tgstat.com/channels/search','POST'))
  self.assertFalse(endpoint_allowed('https://tgstat.com/channels/search','GET'))
  self.assertFalse(endpoint_allowed('https://tgstat.com/channels/search?x=1','POST'))
  self.assertFalse(endpoint_allowed('https://evil.test/channels/search','POST'))

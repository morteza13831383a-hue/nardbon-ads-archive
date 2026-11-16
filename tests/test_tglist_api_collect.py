import sys,unittest
from datetime import timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from tglist_api_collect import DEADLINE,permitted,parse,BASE
from html_core import Robots
class TGListApiTests(unittest.TestCase):
 def test_hard_deadline(self):
  self.assertTrue(permitted(DEADLINE));self.assertFalse(permitted(DEADLINE+timedelta(microseconds=1)))
 def test_exact_owner_api_scope(self):
  robots=Robots('User-agent: *\nDisallow: /api/')
  self.assertTrue(robots.allowed(BASE+'/api/search_channels_v2?page=1&page_size=50&lang=fa&value=&category_slug=&sort='))
  self.assertFalse(robots.allowed(BASE+'/api/other'))
  self.assertFalse(robots.allowed(BASE+'/api/search_channels_v2?page=1&page_size=500&lang=fa&value=&category_slug=&sort='))
 def test_channel_group_rows(self):
  rows=parse({'records':[{'username':'@OneTest','label':'کانال','subscribers':123,'language_code':'fa'}, {'username':'GroupTest','label':'گروه','peer_type':'group','members':42}, {'username':'bad/path','label':'bad'}]},'2026-11-16T00:00:00Z')
  self.assertEqual(rows['onetest']['members'],123)
  self.assertEqual(rows['grouptest']['type'],'group')
  self.assertEqual(len(rows),2)

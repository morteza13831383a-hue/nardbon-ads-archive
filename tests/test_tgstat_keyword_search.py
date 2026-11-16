import sys,unittest,sqlite3,tempfile,base64
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from tgstat_keyword_search import parse,keywords,refresh_auto
from tgstat_session import endpoint_allowed
class SearchTests(unittest.TestCase):
 def test_only_matching_tgstat_profiles_are_saved(self):
  html='<a href="https://ir.tgstat.com/channel/@Aaaa/stat">فروشگاه</a><a href="https://ir.tgstat.com/chat/@Bbbb/stat">گروه</a><a href="https://evil.test/channel/@Badg/stat">bad</a>'
  self.assertEqual(list(parse(html,'channel','فروشگاه')),['aaaa'])
  self.assertEqual(list(parse(html,'chat','فروشگاه')),['bbbb'])
 def test_daily_auto_refresh_appends_without_repeating_finished_terms(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'auto.txt';db=sqlite3.connect(':memory:')
   db.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE keyword_jobs(keyword TEXT,kind TEXT,status TEXT DEFAULT 'pending',PRIMARY KEY(keyword,kind));INSERT INTO keyword_jobs VALUES('فروش','channel','done');")
   with patch('tgstat_keyword_search.delivery_gh',return_value={'content':base64.b64encode('فروش\nتخفیف\n'.encode()).decode()}):
    refresh_auto(db,path,'owner/repo')
   self.assertEqual(db.execute("SELECT keyword,status FROM keyword_jobs ORDER BY rowid").fetchall(),[('فروش','done'),('تخفیف','pending')])
   db.close()
 def test_cookie_boundary(self):
  self.assertTrue(endpoint_allowed('https://tgstat.com/channels/search','POST'))
  self.assertFalse(endpoint_allowed('https://tgstat.com/channels/search','GET'))
  self.assertFalse(endpoint_allowed('https://tgstat.com/channels/search?x=1','POST'))
  self.assertFalse(endpoint_allowed('https://evil.test/channels/search','POST'))

import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from lyzem_collect import parse
class LyzemTests(unittest.TestCase):
 def test_persian_rows_and_bot_exclusion(self):
  html='''<ul><li class="search-result"><div class="search-result-type-wrapper" title="channel"></div><p class="search-result-title"><a href="https://t.me/GoodName">فروشگاه تهران</a></p><p class="search-result-descr">خرید آنلاین</p></li>
  <li class="search-result"><div class="search-result-type-wrapper" title="group"></div><p class="search-result-title"><a href="https://t.me/GroupName">گروه فارسی</a></p></li>
  <li class="search-result"><div class="search-result-type-wrapper" title="channel"></div><p class="search-result-title"><a href="https://t.me/lyzembot">ربات فارسی</a></p></li>
  <li class="search-result"><div class="search-result-type-wrapper" title="channel"></div><p class="search-result-title"><a href="https://t.me/EnglishOnly">English</a></p></li>
  <li class="search-result"><div class="search-result-type-wrapper" title="channel"></div><p class="search-result-title"><a href="https://evil.example/OtherName">فارسی</a></p></li></ul>'''
  channels=parse(html,'channel','2026-11-16T00:00:00Z');groups=parse(html,'group','2026-11-16T00:00:00Z')
  self.assertEqual(list(channels),['goodname']);self.assertEqual(channels['goodname']['language'],'fa')
  self.assertEqual(list(groups),['groupname'])

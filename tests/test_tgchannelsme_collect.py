import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from tgchannelsme_collect import parse
class CatalogTests(unittest.TestCase):
 def test_card_and_pagination(self):
  html='''<div class="card media-card"><div class="card-content"><a href="https://telegramchannels.me/fa/channels/GoodName">نام کانال</a><div class="subtitle">3.98K</div><div class="card-description">توضیح فارسی</div></div></div><a href="https://telegramchannels.me/fa/channels?page=58">58</a><a href="https://evil.example/fa/channels?page=999">other</a>'''
  rows,limit=parse(html,'channel','2026-11-16T00:00:00Z')
  self.assertEqual(limit,58)
  self.assertEqual(rows['goodname']['members'],3980)
  self.assertEqual(rows['goodname']['language'],'fa')
  self.assertEqual(rows['goodname']['description'],'توضیح فارسی')
  self.assertEqual(parse(html,'group')[0],{})

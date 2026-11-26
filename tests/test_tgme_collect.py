import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from tgme_collect import START, parse


class TgMeDirectory(unittest.TestCase):
    def test_country_ranking_and_matching_handle(self):
        html = """<h1>Top channels: Iran</h1><a class="rl-row" href="/Example">
        <span class="rl-name-txt">Example</span><span class="rl-sub">@Example</span></a>
        <a href="/top/news?geo=ir">News</a><a href="/api/search">Search</a>"""
        rows, links = parse(html, START)
        self.assertEqual(set(rows), {'example'})
        self.assertEqual(rows['example']['country'], 'IR')
        self.assertNotIn('language', rows['example'])
        self.assertEqual(links, ['https://tg.me/top/news?geo=ir'])
        self.assertFalse(parse(html.replace('@Example','@Elsewhere'), START)[0])

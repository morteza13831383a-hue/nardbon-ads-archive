import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from archive_yield import seed_known
from telegramsearchengine_collect import channel, connect, ingest, page_data


def catalog(items):
    body = {"items": items, "categories": [{"id": "news"}]}
    return '<script id="__SSR_DIRECTORY_DATA__">window.__INITIAL_DIRECTORY_DATA__ = ' + json.dumps(body) + ';</script>'


def item(u, kind="channel", title="آموزش فارسی"):
    return {"username": u, "type": kind, "name": title, "description": "", "subscriberCount": 1920}


class CatalogTests(unittest.TestCase):
    def test_listing_saves_only_new_persian_channels(self):
        body = catalog([item("ExampleChannel"), item("EXAMPLECHANNEL"), item("KnownName"),
                        item("ExampleBot", "bot"), item("OneGroup", "group"), item("OtherLanguage", title="Global news")])
        with tempfile.TemporaryDirectory() as tmp, connect(Path(tmp)) as db:
            self.assertEqual(ingest(db, "/directory", "directory", body, {"knownname"}), (6,1))
            self.assertEqual(ingest(db, "/directory", "directory", body, {"knownname"}), (6,0))
            row = json.loads(db.execute("SELECT payload FROM records").fetchone()[0])
            self.assertEqual((row["username"],row["members"],row["language"]), ("examplechannel",1920,"fa"))
            self.assertEqual(db.execute("SELECT count(*) FROM pages WHERE path='/news'").fetchone()[0], 1)

    def test_no_credentials_in_snippets(self):
        data = item("ExampleChannel")
        data["description"] = "Secret: dummy-test-value"
        self.assertEqual(channel(data, "now")["description"], "")

    def test_sitemap_only_on_site_profiles(self):
        urls = ["https://telegramsearchengine.com/channel/ValidName", "https://elsewhere.test/channel/OtherName",
                "https://telegramsearchengine.com/channel/BadName?foo=1", "https://telegramsearchengine.com/directory?search=q"]
        body = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(f'<url><loc>{u}</loc></url>' for u in urls) + '</urlset>'
        with tempfile.TemporaryDirectory() as tmp, connect(Path(tmp)) as db:
            ingest(db, "/sitemap.xml","sitemap",body,set())
            self.assertEqual(db.execute("SELECT path FROM pages WHERE kind='profile'").fetchall(), [("/channel/ValidName",)])

    def test_invalid_profile_cannot_emit_rows(self):
        bad = '<script id="__SSR_CHANNEL_PROFILE__">window.__INITIAL_CHANNEL_PROFILE__ = ' + json.dumps(item("Mismatch")) + ';</script>'
        with tempfile.TemporaryDirectory() as tmp, connect(Path(tmp)) as db:
            with self.assertRaises(ValueError):
                ingest(db, "/channel/another", "profile", bad, set())
            self.assertEqual(db.execute("SELECT count(*) FROM records").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            page_data("<p>Missing result data</p>", "directory")


class CatalogPublicationTests(unittest.TestCase):
    def test_merge_preserves_existing_entries_case_insensitively(self):
        from publish_discovered_channels import append_new
        panel = {"fields":["u","t","kd","sr"],"rows":[["EXAMPLE","Existing","channel","existing"]]}
        candidate = {"u":"second","t":"Another","kd":"channel","sr":"telegramsearchengine","tse":"2026-11-23"}
        result = append_new(panel,{"example":{"u":"example","kd":"channel"},"second":candidate})
        self.assertEqual(result,1)
        self.assertEqual(panel["rows"][0],["EXAMPLE","Existing","channel","existing",None])
        self.assertEqual(panel["rows"][1],["second","Another","channel","telegramsearchengine","2026-11-23"])
        self.assertEqual(append_new(panel,{"second":candidate}),0)


class AdShardTests(unittest.TestCase):
    def test_changed_shard_updates_queue_without_overwriting_final_states(self):
        ids = ["c" + x*24 for x in "abc"]
        base = "https://tgadsspy.com/ads/"
        with tempfile.TemporaryDirectory() as tmp, sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE queue(url TEXT PRIMARY KEY,kind TEXT,priority INTEGER,status TEXT,via TEXT)")
            db.executemany("INSERT INTO queue VALUES(?,'detail',1,?,'original')", [(base+ids[0],"pending"),(base+ids[2],"blocked_robots")])
            path = Path(tmp) / "2026-10.json"
            path.write_text(json.dumps([{"id": ids[0]}]))
            self.assertEqual(seed_known(db,tmp), 1)
            self.assertEqual(seed_known(db,tmp), 0)
            path.write_text(json.dumps([{"id": i} for i in ids]))
            self.assertEqual(seed_known(db,tmp), 1)
            self.assertEqual(db.execute("SELECT status FROM queue ORDER BY url").fetchall(), [("known",),("known",),("blocked_robots",)])


if __name__ == "__main__":
    unittest.main()

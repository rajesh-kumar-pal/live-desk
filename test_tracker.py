"""Offline test: feeds fake sitemaps to tracker.py and checks the JSON output.

Run with:  python -m unittest discover tests
"""
import importlib.util
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
HERE = Path(__file__).resolve().parent


def iso(minutes_ago: int) -> str:
    return (datetime.now(IST) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def news_map(rows):
    urls = "".join(
        f"<url><loc>{u}</loc><news:news><news:publication><news:name>X</news:name>"
        f"<news:language>hi</news:language></news:publication>"
        f"<news:publication_date>{iso(m)}</news:publication_date>"
        f"<news:title>{t}</news:title></news:news></url>" for u, t, m in rows)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
            f"{urls}</urlset>").encode()


FAKE = {
    "https://www.patrika.com/google-news-sitemap-v1.xml": news_map([
        ("https://www.patrika.com/kota-news/kota-btech-student-murdered-20962849",
         "कोटा में B.Tech स्टूडेंट की हत्या, परिवार का इकलौता बेटा", 20),
        ("https://www.patrika.com/x/old-story-20000001", "पुरानी खबर", 60 * 20),
    ]),
    "https://www.amarujala.com/sitemap-news-v1.xml": news_map([
        ("https://www.amarujala.com/rajasthan/jodhpur/hanuman-beniwal-high-court-relief-2026-10-06",
         "हनुमान बेनीवाल को हाईकोर्ट से बड़ी राहत, एफआईआर में कार्रवाई पर रोक", 30),
        ("https://www.amarujala.com/rajasthan/kota/kota-btech-student-murder-2026-10-06",
         "कोटा में बीटेक छात्र की हत्या, इकलौता बेटा था; परिवार में मातम", 15),
        ("https://www.amarujala.com/astrology/aaj-ka-rashifal-2026-10-06",
         "आज का राशिफल", 10),
        ("https://www.amarujala.com/world/old-war-story", "ईरान युद्ध", 60 * 5),
    ]),
    "https://www.aajtak.in/rssfeeds/news-sitemap.xml": b"<not xml",
}
ROBOTS = {
    "https://www.bhaskar.com/robots.txt":
        b"User-agent: *\nSitemap: https://www.bhaskar.com/sitemaps-v1--news.xml\n",
}
FAKE["https://www.bhaskar.com/sitemaps-v1--news.xml"] = news_map([
    ("https://www.bhaskar.com/local/rajasthan/jaipur/chomu/news/chomu-fraud-139265029.html",
     "चौमूं: मौसेरे भाइयों ने 100 करोड़ ठगे, ऑडी जब्त", 40),
])


class TrackerTest(unittest.TestCase):
    def test_run(self):
        spec = importlib.util.spec_from_file_location("tracker", HERE.parent / "scripts/tracker.py")
        tr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tr)

        def fake_fetch(url, timeout=25):
            if url in FAKE:
                return FAKE[url]
            if url in ROBOTS:
                return ROBOTS[url]
            raise tr.urllib.error.HTTPError(url, 403, "Forbidden", None, None)

        tr.fetch = fake_fetch
        with tempfile.TemporaryDirectory() as d:
            tr.DATA = Path(d)
            rc = tr.main()
            self.assertEqual(rc, 0)
            wire = json.loads((Path(d) / "wire.json").read_text())["items"]
            tracker = json.loads((Path(d) / "tracker.json").read_text())
            patrika = json.loads((Path(d) / "patrika.json").read_text())

        by = {w["title"][:10]: w for w in wire}
        titles = " | ".join(w["title"] for w in wire)
        self.assertIn("बेनीवाल", titles)
        self.assertIn("चौमूं", titles)
        self.assertNotIn("राशिफल", titles)          # noise filtered
        self.assertNotIn("ईरान युद्ध", titles)       # outside 3h window
        beni = next(w for w in wire if "बेनीवाल" in w["title"])
        kota = next(w for w in wire if "बीटेक" in w["title"])
        self.assertEqual(beni["status"], "GAP")
        self.assertIn("RAJ", beni["tags"])
        self.assertEqual(kota["status"], "COVERED", kota)
        self.assertEqual(len(patrika["items"]), 1)    # 20h-old item dropped
        self.assertTrue(tracker["sources"]["bh"]["ok"])
        self.assertFalse(tracker["sources"]["at"]["ok"])
        self.assertFalse(tracker["sources"]["ndtv"]["ok"])
        self.assertEqual(tracker["sources"]["ndtv"]["note"], "HTTP 403")
        for w in wire:
            self.assertRegex(w["id"], r"^[A-Za-z0-9_\-.~:@+]{1,200}$")
        print("\n" + json.dumps(wire, ensure_ascii=False, indent=1)[:1500])


if __name__ == "__main__":
    unittest.main()

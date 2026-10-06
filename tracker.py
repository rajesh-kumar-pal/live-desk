#!/usr/bin/env python3
"""Patrika Live Desk - hourly sitemap tracker.

Reads the Google News sitemaps of Patrika and its Hindi rivals, keeps rival
stories from the last few hours that matter to Patrika's audience, marks each
one GAP (Patrika has nothing similar) or COVERED, and writes three JSON files:

  data/wire.json     rival stories, newest first
  data/patrika.json  Patrika's own stories from the last 12 hours
  data/tracker.json  per-source health for this run

Standard library only, so the GitHub Action needs no installs.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))

WIRE_HOURS = float(os.environ.get("WIRE_HOURS", "3"))      # rival window
PATRIKA_HOURS = float(os.environ.get("PATRIKA_HOURS", "12"))
MAX_WIRE = int(os.environ.get("MAX_WIRE", "80"))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 PatrikaLiveDesk/1.0")

# key -> (display name, home, known news sitemaps). Empty list = discover from
# robots.txt at run time (any Sitemap line containing "news").
SOURCES: dict[str, tuple[str, str, list[str]]] = {
    "pat": ("Patrika", "https://www.patrika.com",
            ["https://www.patrika.com/google-news-sitemap-v1.xml"]),
    "au": ("Amar Ujala", "https://www.amarujala.com",
           ["https://www.amarujala.com/sitemap-news-v1.xml"]),
    "at": ("Aaj Tak", "https://www.aajtak.in",
           ["https://www.aajtak.in/rssfeeds/news-sitemap.xml"]),
    "zee": ("Zee News Hindi", "https://zeenews.india.com",
            ["https://zeenews.india.com/hindi/sitemaps/news-sitemap.xml",
             "https://zeenews.india.com/hindi/india/rajasthan/news-sitemap.xml"]),
    "bh": ("Bhaskar", "https://www.bhaskar.com", []),
    "abp": ("ABP Live", "https://www.abplive.com", []),
    "ndtv": ("NDTV Hindi", "https://ndtv.in", []),
    "nbt": ("Navbharat Times", "https://navbharattimes.indiatimes.com", []),
}

NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}

# ---------------------------------------------------------------- relevance
# (weight, tag, words). Words match Hindi titles or English URL slugs.
RULES: list[tuple[int, str, list[str]]] = [
    (5, "RAJ", ["राजस्थान", "rajasthan", "जयपुर", "jaipur", "जोधपुर", "jodhpur",
                "उदयपुर", "udaipur", "कोटा", "kota", "अजमेर", "ajmer", "बीकानेर",
                "bikaner", "सीकर", "sikar", "अलवर", "alwar", "भरतपुर", "bharatpur",
                "झुंझुनूं", "jhunjhunu", "चूरू", "churu", "नागौर", "nagaur", "पाली",
                "pali", "बाड़मेर", "barmer", "जैसलमेर", "jaisalmer", "भीलवाड़ा",
                "bhilwara", "चित्तौड़", "chittorgarh", "टोंक", "tonk", "दौसा", "dausa",
                "बांसवाड़ा", "banswara", "डूंगरपुर", "dungarpur", "श्रीगंगानगर",
                "sriganganagar", "हनुमानगढ़", "hanumangarh", "चौमूं", "chomu",
                "भजनलाल", "bhajanlal", "गहलोत", "gehlot", "पायलट", "beniwal",
                "बेनीवाल", "शेखावाटी", "shekhawati", "बहरोड़", "behror"]),
    (4, "MP", ["मध्य प्रदेश", "मध्यप्रदेश", "madhya-pradesh", "एमपी", "भोपाल",
               "bhopal", "इंदौर", "indore", "ग्वालियर", "gwalior", "जबलपुर",
               "jabalpur", "उज्जैन", "ujjain", "रीवा", "rewa", "सागर", "विदिशा",
               "vidisha", "छिंदवाड़ा", "chhindwara", "मोहन यादव", "mohan-yadav",
               "लाडली बहना", "ladli-behna", "नरोत्तम"]),
    (3, "CG", ["छत्तीसगढ़", "chhattisgarh", "रायपुर", "raipur", "बिलासपुर",
               "bilaspur", "बस्तर", "bastar", "दुर्ग", "durg", "जगदलपुर",
               "jagdalpur", "विष्णुदेव", "sai-govt"]),
    (2, "HINDI", ["उत्तर प्रदेश", "uttar-pradesh", "लखनऊ", "lucknow", "बिहार",
                  "bihar", "पटना", "patna", "योगी", "yogi", "वाराणसी", "varanasi",
                  "प्रयागराज", "prayagraj", "हरियाणा", "haryana", "दिल्ली"]),
    (3, "CRIME", ["हत्या", "murder", "दुष्कर्म", "rape", "गिरफ्तार", "arrest",
                  "अपहरण", "kidnap", "ठगी", "ठगे", "fraud", "scam", "रिश्वत",
                  "bribe", "acb", "लूट", "loot", "robbery", "चोरी", "theft",
                  "गोली", "firing", "शव", "body", "पुलिस", "police", "एनकाउंटर",
                  "encounter", "crime", "क्राइम", "sog", "ed-raid", "cbi"]),
    (3, "ACC", ["हादसा", "accident", "दुर्घटना", "आग", "fire", "धमाका", "blast",
                "मौत", "death", "died", "डूब", "drown", "collapse", "ढहा"]),
    (2, "POL", ["भाजपा", "bjp", "कांग्रेस", "congress", "चुनाव", "election",
                "मुख्यमंत्री", "cm-", "मोदी", "modi", "राहुल", "rahul", "विधायक",
                "mla", "सांसद", "पंचायत", "panchayat", "हाईकोर्ट", "high-court",
                "सुप्रीम कोर्ट", "supreme-court"]),
    (2, "WAR", ["युद्ध", "war", "ईरान", "iran", "इजरायल", "israel", "पाकिस्तान",
                "pakistan", "चीन", "china", "रूस", "russia", "यूक्रेन", "ukraine",
                "ट्रंप", "trump", "हमला", "attack", "missile", "मिसाइल"]),
    (2, "CRK", ["क्रिकेट", "cricket", "टीम इंडिया", "team-india", "ind-vs", "t20",
                "वनडे", "odi", "bcci", "ipl", "कोहली", "kohli", "रोहित", "rohit",
                "हरमनप्रीत", "harmanpreet", "सूर्यवंशी", "sooryavanshi"]),
    (2, "MONEY", ["rbi", "आरबीआई", "रेपो", "repo", "emi", "sensex", "सेंसेक्स",
                  "nifty", "निफ्टी", "सोना", "gold-price", "चांदी", "silver",
                  "पेट्रोल", "petrol", "डीजल", "diesel", "da-hike", "महंगाई",
                  "ipo", "fd-", "किस्त", "installment", "pm-kisan", "सैलरी", "salary"]),
    (1, "ENT", ["बॉक्स ऑफिस", "box-office", "bollywood", "बॉलीवुड", "सलमान",
                "salman", "शाहरुख", "shahrukh", "ott", "trailer", "ट्रेलर",
                "bigg-boss", "बिग बॉस", "film", "फिल्म"]),
]
NOISE = ["horoscope", "rashifal", "राशिफल", "lottery", "web-stories", "webstory",
         "photo-gallery", "quiz", "aaj-ka-mausam-live"]


def score_item(title: str, url: str) -> tuple[int, list[str]]:
    hay = f"{title} {url}".lower()
    if any(n in hay for n in NOISE):
        return 0, []
    total, tags = 0, []
    for weight, tag, words in RULES:
        if any(w in hay for w in words):
            total += weight
            tags.append(tag)
    return total, tags


# ---------------------------------------------------------------- fetching
def fetch(url: str, timeout: int = 25) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/xml,text/xml,*/*",
        "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or url.endswith(".gz"):
            body = gzip.decompress(body)
        return body


def discover(home: str) -> list[str]:
    txt = fetch(home + "/robots.txt").decode("utf-8", "replace")
    maps = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", txt)
    news = [m for m in maps if "news" in m.lower() and "video" not in m.lower()
            and "image" not in m.lower() and "photo" not in m.lower()]
    hindi = [m for m in news if not re.search(r"/(english|marathi|bengali|gujarati|"
                                              r"tamil|telugu|punjabi|odisha)/", m)]
    return (hindi or news)[:3]


def parse_dt(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%d"):
        try:
            d = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=IST)
            return d.timestamp()
        except ValueError:
            continue
    return None


def slug_title(url: str) -> str:
    path = url.split("?")[0].rstrip("/").split("/")[-1]
    path = re.sub(r"\.(cms|html?|php)$", "", path)
    path = re.sub(r"[-_]?\d{5,}$", "", path)
    return path.replace("-", " ").strip()


def parse_sitemap(body: bytes, depth: int = 0) -> list[dict]:
    root = ET.fromstring(body)
    tag = root.tag.split("}")[-1]
    if tag == "sitemapindex" and depth == 0:
        locs = [e.text.strip() for e in root.iterfind("sm:sitemap/sm:loc", NS) if e.text]
        out: list[dict] = []
        for loc in locs[:2]:          # newest child sitemaps are listed first
            try:
                out += parse_sitemap(fetch(loc), depth + 1)
            except Exception:
                pass
        return out
    items = []
    for u in root.iterfind("sm:url", NS):
        loc = (u.findtext("sm:loc", "", NS) or "").strip()
        if not loc:
            continue
        title = (u.findtext("news:news/news:title", "", NS) or "").strip()
        pub = (u.findtext("news:news/news:publication_date", "", NS)
               or u.findtext("sm:lastmod", "", NS))
        items.append({"url": loc, "title": title or slug_title(loc),
                      "t": parse_dt(pub)})
    return items


def read_source(key: str) -> tuple[list[dict], dict]:
    name, home, maps = SOURCES[key]
    status = {"name": name, "ok": False}
    try:
        maps = maps or discover(home)
        if not maps:
            status["note"] = "no news sitemap listed in robots.txt"
            return [], status
        items, errors = [], []
        for m in maps:
            try:
                items += parse_sitemap(fetch(m))
            except Exception as e:  # keep going with the other maps
                errors.append(f"{m.split('/')[-1]}: {type(e).__name__}")
        seen, uniq = set(), []
        for it in items:
            if it["url"] not in seen and it["t"]:
                seen.add(it["url"])
                uniq.append(it)
        status.update(ok=bool(uniq), count=len(uniq), sitemaps=maps)
        if uniq:
            status["newest"] = int(max(i["t"] for i in uniq) * 1000)
        if errors:
            status["note"] = "; ".join(errors)[:200]
        elif not uniq:
            status["note"] = "sitemap empty or without dates"
        return uniq, status
    except urllib.error.HTTPError as e:
        status["note"] = f"HTTP {e.code}"
    except Exception as e:
        status["note"] = f"{type(e).__name__}: {str(e)[:120]}"
    return [], status


# ---------------------------------------------------------------- matching
STOP = set("""के की का में से पर और को है हैं ने भी एक यह वह कि तो हुआ हुई अब बड़ा बड़ी
जानें क्या कैसे क्यों कौन लिए साथ बाद दिया किया होगा गया गई news latest today
the a an of in on to for and is are with from by after big new india hindi""".split())


def tokens(text: str) -> set[str]:
    words = re.findall(r"[\wऀ-ॿ]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in STOP and not w.isdigit()}


def best_match(item: dict, patrika: list[dict]) -> tuple[float, dict | None]:
    a = tokens(item["title"]) | tokens(slug_title(item["url"]))
    best, hit = 0.0, None
    for p in patrika:
        b = p["_tok"]
        if not a or not b:
            continue
        sim = len(a & b) / min(len(a), len(b))
        if sim > best:
            best, hit = sim, p
    return best, hit


# ---------------------------------------------------------------- main
def make_id(key: str, url: str) -> str:
    tail = url.split("?")[0].rstrip("/").split("/")[-1] or str(abs(hash(url)))
    doc = re.sub(r"[^A-Za-z0-9_\-.~:@+]", "-", f"{key}-{tail}")[:190]
    return doc


def main() -> int:
    now = time.time()
    DATA.mkdir(parents=True, exist_ok=True)
    results = {k: read_source(k) for k in SOURCES}

    pat_items, _ = results["pat"]
    pat_recent = sorted((i for i in pat_items if i["t"] >= now - PATRIKA_HOURS * 3600),
                        key=lambda i: -i["t"])
    for p in pat_recent:
        p["_tok"] = tokens(p["title"]) | tokens(slug_title(p["url"]))

    wire = []
    for key, (items, _) in results.items():
        if key == "pat":
            continue
        for it in items:
            if it["t"] < now - WIRE_HOURS * 3600 or it["t"] > now + 600:
                continue
            score, tags = score_item(it["title"], it["url"])
            if score < 2:
                continue
            sim, hit = best_match(it, pat_recent)
            entry = {
                "id": make_id(key, it["url"]),
                "src": key, "pub": SOURCES[key][0],
                "title": it["title"], "url": it["url"],
                "t": int(it["t"] * 1000),
                "time": datetime.fromtimestamp(it["t"], IST).strftime("%H:%M"),
                "score": score, "tags": tags,
                "status": "COVERED" if sim >= 0.5 else "GAP",
                "match": round(sim, 2),
            }
            if hit and sim >= 0.3:
                entry["patrikaMatch"] = {"title": hit["title"], "url": hit["url"]}
            wire.append(entry)

    wire.sort(key=lambda e: (-e["score"], -e["t"]))
    wire = sorted(wire[:MAX_WIRE], key=lambda e: -e["t"])

    run_ms = int(now * 1000)
    tracker = {"lastRunAt": run_ms, "method": "GitHub Actions",
               "windowHours": WIRE_HOURS, "kept": len(wire),
               "gaps": sum(1 for w in wire if w["status"] == "GAP"),
               "sources": {k: s for k, (_, s) in results.items()}}
    patrika = {"updatedAt": run_ms,
               "newest": int(pat_recent[0]["t"] * 1000) if pat_recent else None,
               "items": [{"slug": slug_title(p["url"]).replace(" ", "-"),
                          "url": p["url"], "title": p["title"],
                          "t": int(p["t"] * 1000)} for p in pat_recent]}

    write(DATA / "wire.json", {"generatedAt": run_ms, "items": wire})
    write(DATA / "patrika.json", patrika)
    write(DATA / "tracker.json", tracker)

    ok = [k for k, (_, s) in results.items() if s["ok"]]
    print(f"{datetime.fromtimestamp(now, IST):%H:%M} IST  sources ok: {','.join(ok) or 'none'}"
          f"  patrika={len(pat_recent)}  wire={len(wire)}  gaps={tracker['gaps']}")
    for k, (_, s) in results.items():
        if not s["ok"]:
            print(f"  ! {k}: {s.get('note')}")
    # Fail the job only if Patrika itself could not be read (no GAP check possible).
    return 0 if results["pat"][1]["ok"] else 1


def write(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())

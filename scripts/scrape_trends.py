"""
Aggregate daily trends from free public sources.

Sources:
  - Google Trends (US daily searches, RSS) → "what America is searching for"
  - Reddit /r/popular                       → viral posts / memes
  - Reddit niche subs (marketing, entrepreneur, social media, traders)
                                            → niche-specific trends for content

Output: data/trends.json
"""

import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "trends.json"

UA = "Mozilla/5.0 (compatible; trending-audio-tracker/1.0)"

NICHE_SUBS = [
    ("marketing", "Marketing"),
    ("Entrepreneur", "Entrepreneurs"),
    ("socialmedia", "Social Media"),
    ("wallstreetbets", "Traders / WSB"),
    ("OutOfTheLoop", "What's everyone talking about"),
]


def fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "en-US,en",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_json(url: str, timeout: int = 30):
    return json.loads(fetch(url, timeout))


# ---------- Google Trends Daily (US) ----------
def google_trends() -> list[dict]:
    """Parse the public Google Trends RSS for US daily trending searches."""
    xml = fetch("https://trends.google.com/trending/rss?geo=US")
    items: list[dict] = []
    for m in re.finditer(r"<item>(.*?)</item>", xml, re.S):
        block = m.group(1)
        title = _xml_value(block, "title")
        traffic = _xml_value(block, "ht:approx_traffic")
        pic = _xml_value(block, "ht:picture")

        news_url = None
        news_title = None
        news_source = None
        first_news = re.search(r"<ht:news_item>(.*?)</ht:news_item>", block, re.S)
        if first_news:
            n = first_news.group(1)
            news_title = _xml_value(n, "ht:news_item_title")
            news_url = _xml_value(n, "ht:news_item_url")
            news_source = _xml_value(n, "ht:news_item_source")

        items.append(
            {
                "title": title,
                "subtitle": news_title or "",
                "url": news_url
                or f"https://www.google.com/search?q={urllib.parse.quote(title)}",
                "source": news_source or "Google Trends",
                "thumbnail": pic,
                "meta": (traffic + " searches") if traffic else "",
            }
        )
    return items[:20]


def _xml_value(s: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", s, re.S)
    if not m:
        return ""
    raw = m.group(1).strip()
    cdata = re.match(r"<!\[CDATA\[(.*?)\]\]>", raw, re.S)
    return unescape(cdata.group(1) if cdata else raw).strip()


# ---------- Reddit ----------
def reddit_section(sub: str, label: str, listing: str = "hot", limit: int = 8, t: str = "week") -> list[dict]:
    qs = f"limit={limit}"
    if listing == "top":
        qs += f"&t={t}"
    url = f"https://www.reddit.com/r/{sub}/{listing}.json?{qs}"
    body = fetch_json(url)
    items: list[dict] = []
    for c in body.get("data", {}).get("children", []):
        p = c.get("data", {})
        if p.get("stickied"):
            continue
        title = p.get("title") or ""
        if not title:
            continue
        ups = p.get("ups", 0)
        comments = p.get("num_comments", 0)
        permalink = "https://www.reddit.com" + p.get("permalink", "")
        thumb = p.get("thumbnail") or ""
        if thumb in ("self", "default", "nsfw", "spoiler", ""):
            # Try preview image
            try:
                thumb = (
                    p.get("preview", {})
                    .get("images", [{}])[0]
                    .get("source", {})
                    .get("url", "")
                    .replace("&amp;", "&")
                )
            except Exception:
                thumb = ""
        items.append(
            {
                "title": title,
                "subtitle": f"r/{p.get('subreddit')} • {comments} comments",
                "url": permalink,
                "source": f"r/{p.get('subreddit')}",
                "thumbnail": thumb,
                "meta": _short_int(ups) + " upvotes",
            }
        )
        if len(items) >= limit:
            break
    return items


def _short_int(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


# ---------- Main ----------
def main() -> int:
    sections: list[dict] = []
    notes: list[dict] = []

    def add(key: str, label: str, fn):
        try:
            items = fn()
            sections.append({"key": key, "label": label, "items": items})
            notes.append({"key": key, "count": len(items)})
            print(f"[trends] {label}: {len(items)} items", flush=True)
        except Exception as e:
            notes.append({"key": key, "error": str(e)})
            print(f"[trends] {label}: ERROR {e}", flush=True)

    add("search", "Trending Searches (US)", google_trends)
    add("viral", "Viral on Reddit", lambda: reddit_section("popular", "Viral", limit=15))

    for sub, label in NICHE_SUBS:
        add(f"sub_{sub}", label, lambda s=sub: reddit_section(s, "top", listing="top", limit=6))

    payload = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "sections": sections,
        "_notes": notes,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    total = sum(len(s["items"]) for s in sections)
    print(f"[done] wrote {total} items in {len(sections)} sections → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

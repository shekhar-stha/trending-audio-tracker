"""
Scrape US trending TikTok sounds from tokchart.com.

Why tokchart and not TikTok Creative Center directly: TikTok now requires
ad-account login on the Creative Center API (returns 40101 "no permission").
tokchart.com publishes the same data on plain HTML pages with no auth.

Output: data/trending.json
"""

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "trending.json"
DEBUG_DIR = ROOT / "debug"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Each "lens" = a tokchart page. Maps to the period_days field for back-compat
# with the dashboard pills (7/30/120 → Trending / Growing / Top).
LENSES = [
    {"period_days": 7, "label": "Trending in US", "url": "https://tokchart.com/trending/US"},
    {"period_days": 30, "label": "Fastest growing", "url": "https://tokchart.com/growing"},
]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def parse(html: str, period: int) -> list[dict]:
    """
    Each tokchart sound card has the rough shape:

      <span class="text-3xl md:text-4xl">{rank}</span>
      ... +{growth_24h} videos ...
      <img ... src="https://tokchart.com/img/{id}/600"/>
      <span class="text-slate-800">{artist}</span>
      <a href=".../dashboard/sounds/{id}" class="...font-brico font-bold...">{title}</a>
      :data="[{...numbers...}]"     <- ticker series, last value = current usage
      <a href="https://www.tiktok.com/music/{slug}-{id}">

    We split the page on the cover image (`/img/{id}/600`), then within
    each chunk pull out the fields. This is resilient to surrounding
    whitespace/markup tweaks.
    """
    rows: list[dict] = []
    # Split into per-sound chunks anchored on the cover image
    chunks = re.split(r'(?=<img[^>]+src="https://tokchart\.com/img/\d+/600")', html)
    for chunk in chunks:
        m_id = re.search(r'tokchart\.com/img/(\d+)/600', chunk)
        if not m_id:
            continue
        clip_id = m_id.group(1)

        # Rank is the most recent <span class="text-3xl..."> before this chunk —
        # but since we split on cover, the rank lives in the *previous* chunk.
        # Easier: grep for it relative to the cover by using the original html.
        rank = 0

        # Artist: <span class="text-slate-800">…</span>
        m_artist = re.search(r'<span class="text-slate-800">\s*([^<]+?)\s*</span>', chunk)
        artist = unescape(m_artist.group(1)).strip() if m_artist else ""

        # Title: anchor with text-2xl/text-4xl font-brico font-bold
        m_title = re.search(
            r'<a[^>]+href="[^"]*?/dashboard/sounds/\d+"[^>]*?'
            r'class="[^"]*font-brico[^"]*font-bold[^"]*"[^>]*>\s*([^<]+?)\s*</a>',
            chunk,
            re.S,
        )
        title = unescape(m_title.group(1)).strip() if m_title else ""

        # Current usage: last value in the :data="[…]" array
        m_data = re.search(r':data="\[([^\]]+)\]"', chunk)
        usage = 0
        if m_data:
            nums = [int(x) for x in re.findall(r"\d+", m_data.group(1))]
            if nums:
                usage = nums[-1]

        # 24h growth: "+{n} videos"
        m_growth = re.search(r"\+([\d,]+)\s*videos", chunk)
        growth_24h = int(m_growth.group(1).replace(",", "")) if m_growth else 0

        # TikTok deep link
        m_tt = re.search(
            r'href="(https://www\.tiktok\.com/music/[^"]+)"', chunk
        )
        tiktok_link = m_tt.group(1) if m_tt else f"https://www.tiktok.com/music/-{clip_id}"

        if not title:
            continue
        rows.append(
            {
                "rank": 0,  # filled in below from page order
                "title": title,
                "artist": artist,
                "clip_id": clip_id,
                "tiktok_uses": usage,
                "tiktok_uses_24h": growth_24h,
                "tiktok_link": tiktok_link,
                "cover": f"https://tokchart.com/img/{clip_id}/600",
                "period_days": period,
            }
        )

    # Assign ranks by appearance order, deduping clip_ids within this lens
    seen: set[str] = set()
    out: list[dict] = []
    rank = 1
    for r in rows:
        if r["clip_id"] in seen:
            continue
        seen.add(r["clip_id"])
        r["rank"] = rank
        rank += 1
        out.append(r)
    return out


def main() -> int:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []
    notes: list[dict] = []

    for lens in LENSES:
        print(f"[scrape] {lens['label']} ← {lens['url']}", flush=True)
        try:
            html = fetch(lens["url"])
            (DEBUG_DIR / f"raw_{lens['period_days']}.html").write_text(html[:500_000])
            rows = parse(html, lens["period_days"])
        except Exception as e:
            print(f"  error: {e}", flush=True)
            notes.append({"lens": lens["label"], "error": str(e)})
            rows = []
        print(f"  {len(rows)} rows", flush=True)
        notes.append({"lens": lens["label"], "rows": len(rows)})
        all_rows.extend(rows)

    (DEBUG_DIR / "summary.json").write_text(
        json.dumps({"runs": notes, "total_rows": len(all_rows)}, indent=2)
    )

    payload = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "region": "US",
        "source": "tokchart.com",
        "count": len(all_rows),
        "lenses": [{"period_days": l["period_days"], "label": l["label"]} for l in LENSES],
        "sounds": all_rows,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"[done] wrote {len(all_rows)} sounds → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

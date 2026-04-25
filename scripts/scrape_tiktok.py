"""
Scrape TikTok Creative Center trending sounds (US region).

Strategy: load the trending-music page in a headless browser and intercept
the internal JSON API response. This avoids reverse-engineering the
request signing scheme TikTok uses.

Output: data/trending.json
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "trending.json"

PAGE_URL = (
    "https://ads.tiktok.com/business/creativecenter/inspiration/popular/"
    "music/pc/en?period=7&countryCode=US"
)

API_MATCH = "/creative_radar_api/v1/popular_trend/sound/list"

PERIODS = [7, 30, 120]


def scrape_period(page, period: int) -> list[dict]:
    captured: list[dict] = []

    def on_response(response):
        if API_MATCH in response.url:
            try:
                body = response.json()
            except Exception:
                return
            data = body.get("data") or {}
            sounds = data.get("sound_list") or data.get("list") or []
            for s in sounds:
                captured.append(s)

    page.on("response", on_response)

    url = (
        "https://ads.tiktok.com/business/creativecenter/inspiration/popular/"
        f"music/pc/en?period={period}&countryCode=US"
    )
    page.goto(url, wait_until="networkidle", timeout=60_000)

    # Scroll a few times to trigger pagination
    for _ in range(4):
        page.mouse.wheel(0, 4000)
        time.sleep(1.5)

    page.remove_listener("response", on_response)
    return captured


def normalize(raw: dict, period: int) -> dict:
    title = raw.get("title") or raw.get("song_name") or ""
    author = raw.get("author") or raw.get("artist") or ""
    clip_id = raw.get("clip_id") or raw.get("id") or ""
    cover = raw.get("cover") or raw.get("cover_thumb") or ""
    rank = raw.get("rank") or 0
    user_count = raw.get("user_count") or raw.get("usage") or 0
    link = (
        raw.get("link")
        or (f"https://www.tiktok.com/music/-{clip_id}" if clip_id else "")
    )
    return {
        "rank": rank,
        "title": title,
        "artist": author,
        "clip_id": clip_id,
        "tiktok_uses": user_count,
        "tiktok_link": link,
        "cover": cover,
        "period_days": period,
    }


def main() -> int:
    all_rows: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            locale="en-US",
        )
        page = context.new_page()

        for period in PERIODS:
            print(f"[scrape] period={period}d", flush=True)
            raw = scrape_period(page, period)
            print(f"  captured {len(raw)} rows", flush=True)
            all_rows.extend(normalize(r, period) for r in raw)

        browser.close()

    # Dedupe per (clip_id, period)
    seen: set[tuple[str, int]] = set()
    unique: list[dict] = []
    for row in all_rows:
        key = (row["clip_id"], row["period_days"])
        if not row["clip_id"] or key in seen:
            continue
        seen.add(key)
        unique.append(row)

    unique.sort(key=lambda r: (r["period_days"], r["rank"] or 9999))

    payload = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "region": "US",
        "count": len(unique),
        "sounds": unique,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"[done] wrote {len(unique)} sounds → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

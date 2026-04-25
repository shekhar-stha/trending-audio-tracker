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
DEBUG_DIR = ROOT / "debug"

# Match anything that looks like the Creative Center sound endpoint.
# TikTok renames these occasionally — keep the matcher generous.
API_HINTS = ("sound", "music", "popular_trend", "creative_radar")

PERIODS = [7, 30, 120]


def url_for(period: int) -> str:
    return (
        "https://ads.tiktok.com/business/creativecenter/inspiration/popular/"
        f"music/pc/en?period={period}&countryCode=US"
    )


def looks_like_sound_payload(body) -> bool:
    if not isinstance(body, dict):
        return False
    data = body.get("data")
    if not isinstance(data, dict):
        return False
    for key in ("sound_list", "list", "music_list", "items"):
        v = data.get(key)
        if isinstance(v, list) and v and isinstance(v[0], dict):
            sample = v[0]
            keys = set(sample.keys())
            if keys & {"clip_id", "song_name", "title", "artist", "author", "music_id"}:
                return True
    return False


def scrape_period(page, period: int, debug_log: list) -> list[dict]:
    captured: list[dict] = []
    seen_endpoints: set[str] = set()

    def on_response(response):
        url = response.url
        if not any(h in url for h in API_HINTS):
            return
        try:
            body = response.json()
        except Exception:
            return
        if not looks_like_sound_payload(body):
            return
        seen_endpoints.add(url.split("?")[0])
        data = body.get("data") or {}
        for key in ("sound_list", "list", "music_list", "items"):
            v = data.get(key)
            if isinstance(v, list):
                captured.extend(v)
                break

    page.on("response", on_response)

    page.goto(url_for(period), wait_until="domcontentloaded", timeout=60_000)

    # Give TikTok time to fire its XHRs.
    page.wait_for_timeout(6000)

    # Scroll a few times to trigger pagination
    for _ in range(5):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(1500)

    page.remove_listener("response", on_response)

    debug_log.append(
        {
            "period": period,
            "captured_rows": len(captured),
            "matched_endpoints": sorted(seen_endpoints),
        }
    )
    return captured


def normalize(raw: dict, period: int) -> dict:
    title = raw.get("title") or raw.get("song_name") or raw.get("clip_title") or ""
    author = raw.get("author") or raw.get("artist") or raw.get("singer") or ""
    clip_id = raw.get("clip_id") or raw.get("music_id") or raw.get("id") or ""
    cover = raw.get("cover") or raw.get("cover_thumb") or raw.get("cover_large") or ""
    rank = raw.get("rank") or 0
    user_count = (
        raw.get("user_count") or raw.get("usage") or raw.get("post_count") or 0
    )
    link = raw.get("link") or (
        f"https://www.tiktok.com/music/-{clip_id}" if clip_id else ""
    )
    return {
        "rank": rank,
        "title": title,
        "artist": author,
        "clip_id": str(clip_id),
        "tiktok_uses": user_count,
        "tiktok_link": link,
        "cover": cover,
        "period_days": period,
    }


def main() -> int:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []
    debug_log: list[dict] = []
    all_seen_urls: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1440, "height": 900},
            timezone_id="America/New_York",
        )
        page = context.new_page()

        # Log every response URL so we can diagnose if matching fails
        page.on("response", lambda r: all_seen_urls.append(r.url))

        for period in PERIODS:
            print(f"[scrape] period={period}d", flush=True)
            try:
                raw = scrape_period(page, period, debug_log)
            except Exception as e:
                print(f"  error: {e}", flush=True)
                raw = []
            print(f"  captured {len(raw)} rows", flush=True)
            all_rows.extend(normalize(r, period) for r in raw)

        # Snapshot for diagnostics
        try:
            page.screenshot(path=str(DEBUG_DIR / "last_page.png"), full_page=False)
        except Exception:
            pass
        try:
            (DEBUG_DIR / "last_page.html").write_text(page.content())
        except Exception:
            pass

        browser.close()

    # Diagnostics
    interesting = [
        u
        for u in all_seen_urls
        if any(h in u for h in API_HINTS) or "ads.tiktok.com" in u
    ]
    (DEBUG_DIR / "network.txt").write_text(
        "\n".join(interesting[:300]) if interesting else "no matched URLs captured"
    )
    (DEBUG_DIR / "summary.json").write_text(
        json.dumps({"runs": debug_log, "total_rows": len(all_rows)}, indent=2)
    )

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

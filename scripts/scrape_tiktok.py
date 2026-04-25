"""
Scrape TikTok Creative Center trending sounds (US region).

We bootstrap a browser session (so TikTok sets ttwid + anti-bot cookies),
then call the /sound/rank_list API directly with pagination to collect
many more rows than the page itself loads.

Output: data/trending.json
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "trending.json"
DEBUG_DIR = ROOT / "debug"

PERIODS = [7, 30, 120]
PAGES_PER_PERIOD = 7  # 7 pages × ~limit returns enough trending rows
LIMIT = 50

API = "https://ads.tiktok.com/creative_radar_api/v1/popular_trend/sound/rank_list"


def bootstrap_url(period: int) -> str:
    return (
        "https://ads.tiktok.com/business/creativecenter/inspiration/popular/"
        f"music/pc/en?period={period}&countryCode=US"
    )


def fetch_period(context, page, period: int, debug_log: list) -> list[dict]:
    # Visit the matching page so the SPA primes any period-specific state.
    page.goto(bootstrap_url(period), wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(3500)

    rows: list[dict] = []
    page_errors: list[dict] = []

    for pg in range(1, PAGES_PER_PERIOD + 1):
        params = {
            "period": str(period),
            "page": str(pg),
            "limit": str(LIMIT),
            "rank_type": "popular",
            "new_on_board": "false",
            "commercial_music": "false",
            "country_code": "US",
        }
        url = API + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        resp = context.request.get(
            url,
            headers={
                "accept": "application/json, text/plain, */*",
                "accept-language": "en-US,en;q=0.9",
                "referer": bootstrap_url(period),
                "lang": "en",
                "anonymous-user-id": "",
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            },
        )
        try:
            body = resp.json()
        except Exception as e:
            page_errors.append({"page": pg, "error": str(e), "status": resp.status})
            break

        if pg == 1 and period == PERIODS[0]:
            (DEBUG_DIR / "sample_response.json").write_text(
                json.dumps(body, indent=2)[:8000]
            )

        data = body.get("data") or {}
        sounds = (
            data.get("sound_list")
            or data.get("list")
            or data.get("music_list")
            or data.get("items")
            or []
        )
        if not sounds:
            page_errors.append(
                {"page": pg, "error": "no sounds in payload", "code": body.get("code")}
            )
            break
        rows.extend(sounds)

        # Stop if response indicates last page
        has_more = data.get("has_more")
        if has_more is False:
            break

    debug_log.append(
        {"period": period, "rows": len(rows), "page_notes": page_errors}
    )
    return rows


def normalize(raw: dict, period: int) -> dict:
    title = raw.get("title") or raw.get("song_name") or raw.get("clip_title") or ""
    author = raw.get("author") or raw.get("artist") or raw.get("singer") or ""
    clip_id = raw.get("clip_id") or raw.get("music_id") or raw.get("id") or ""
    cover = raw.get("cover") or raw.get("cover_thumb") or raw.get("cover_large") or ""
    rank = raw.get("rank") or 0
    user_count = (
        raw.get("user_count")
        or raw.get("usage")
        or raw.get("post_count")
        or raw.get("usage_amount")
        or 0
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

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
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

        for period in PERIODS:
            print(f"[scrape] period={period}d", flush=True)
            try:
                raw = fetch_period(context, page, period, debug_log)
            except Exception as e:
                print(f"  error: {e}", flush=True)
                raw = []
            print(f"  {len(raw)} raw rows", flush=True)
            all_rows.extend(normalize(r, period) for r in raw)

        browser.close()

    (DEBUG_DIR / "summary.json").write_text(
        json.dumps({"runs": debug_log, "total_rows": len(all_rows)}, indent=2)
    )

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

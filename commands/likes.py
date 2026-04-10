import json
import time
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

from config import LIKES_FILE, EXPORTS_DIR, DATA_DIR, SCROLL_STEP, SCROLL_PAUSE, STALL_SLEEP, X_USERNAME
from core.platform import get_screen_size
from core.cookies import get_x_cookies
from core.graphql import extract_from_graphql
from core.dom import scrape_visible
from core.merge import merge


def _load_existing():
    if LIKES_FILE.exists():
        data = json.loads(LIKES_FILE.read_text(encoding="utf-8"))
        if data:
            return data, {t["url"] for t in data}
    return [], set()


def _oldest_timestamp(tweets):
    for t in reversed(tweets):
        ts = t.get("timestamp")
        if ts:
            return ts
    return None


def _save(results, label="likes"):
    DATA_DIR.mkdir(exist_ok=True)
    EXPORTS_DIR.mkdir(exist_ok=True)
    LIKES_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    export_path = EXPORTS_DIR / f"{label}_{stamp}.json"
    export_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(results)} likes -> {LIKES_FILE}")
    print(f"Snapshot -> {export_path}")


def make_response_handler(url_map, full_text_map):
    def handle(response):
        if "/graphql/" not in response.url or "Likes" not in response.url:
            return
        try:
            extract_from_graphql(response.json(), url_map, full_text_map)
        except Exception as e:
            print(f"GraphQL parse error: {e}")
    return handle


def run(limit=None):
    existing, seen_urls = _load_existing()
    existing_urls = set(seen_urls)
    cutoff_ts = _oldest_timestamp(existing)

    if cutoff_ts:
        print(f"Continuation run — collecting likes older than {cutoff_ts}")
    else:
        print("No existing data — full scrape")

    if limit:
        print(f"Limit override: {limit}")

    url_map       = {}
    full_text_map = {}
    cookies       = get_x_cookies()
    width, height = get_screen_size()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(viewport={"width": width, "height": height})
        context.add_cookies(cookies)
        page = context.new_page()
        page.on("response", make_response_handler(url_map, full_text_map))

        page.goto(f"https://x.com/{X_USERNAME}/likes")
        page.wait_for_selector("article[data-testid='tweet']")

        dom_snapshots = []
        stall         = 0
        done          = False

        while not done:
            tweets    = scrape_visible(page)
            new_count = 0
            past_existing = any(
                t["url"] and t["url"] not in existing_urls
                for t in tweets if t["url"]
            )

            for t in tweets:
                if not t["url"]:
                    continue
                if t["url"] in existing_urls or t["url"] in seen_urls:
                    continue
                if cutoff_ts and t.get("timestamp") and t["timestamp"] >= cutoff_ts:
                    continue
                seen_urls.add(t["url"])
                dom_snapshots.append(t)
                new_count += 1
                if limit and len(dom_snapshots) >= limit:
                    done = True
                    break

            if not done:
                page.evaluate(f"window.scrollBy(0, {SCROLL_STEP})")
                time.sleep(SCROLL_PAUSE)

                if new_count == 0 and past_existing:
                    stall += 1
                    if stall >= 5:
                        print(f"Stall {stall}: waiting {STALL_SLEEP}s...")
                        time.sleep(STALL_SLEEP)
                    if stall >= 8:
                        print("No new tweets after 8 scrolls, stopping.")
                        done = True
                elif new_count > 0:
                    stall = 0

            print(f"Collected: {len(dom_snapshots)} new | URL map: {len(url_map)}")

        browser.close()

    # merge after loop — all graphql responses guaranteed to have arrived
    new_results = [merge(t, url_map, full_text_map) for t in dom_snapshots]
    print(f"Scraped {len(new_results)} new likes")

    combined = existing + new_results
    _save(combined)

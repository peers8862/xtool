import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

from config import BOOKMARKS_FILE, EXPORTS_DIR, DATA_DIR, DEBUG_DIR, SCROLL_STEP, SCROLL_PAUSE, STALL_SLEEP
from core.platform import get_screen_size
from core.cookies import get_x_cookies
from core.graphql import extract_from_graphql
from core.dom import scrape_visible
from core.merge import merge


def _load_existing():
    if BOOKMARKS_FILE.exists():
        data = json.loads(BOOKMARKS_FILE.read_text(encoding="utf-8"))
        if data:
            return data, {t["url"] for t in data}
    return [], set()


def _newest_timestamp(tweets):
    for t in tweets:
        ts = t.get("timestamp")
        if ts:
            return ts
    return None


def _save(results, label="bookmarks"):
    DATA_DIR.mkdir(exist_ok=True)
    EXPORTS_DIR.mkdir(exist_ok=True)
    BOOKMARKS_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    export_path = EXPORTS_DIR / f"{label}_{stamp}.json"
    export_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(results)} bookmarks -> {BOOKMARKS_FILE}")
    print(f"Snapshot -> {export_path}")


def make_response_handler(url_map, full_text_map):
    def handle(response):
        if not any(k in response.url for k in ["Bookmarks", "TweetDetail", "UserTweets", "TweetResultByRestId", "timeline"]):
            return
        try:
            extract_from_graphql(response.json(), url_map, full_text_map)
        except Exception as e:
            print(f"GraphQL parse error: {e}")
    return handle


def save_debug(page, label="error"):
    DEBUG_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    for fn, action in [
        (f"{label}_{ts}.png",       lambda p: p.screenshot(path=str(DEBUG_DIR / f"{label}_{ts}.png"), full_page=True)),
        (f"{label}_{ts}.html",      lambda p: (DEBUG_DIR / f"{label}_{ts}.html").write_text(p.content(), encoding="utf-8")),
        (f"{label}_{ts}_meta.json", lambda p: (DEBUG_DIR / f"{label}_{ts}_meta.json").write_text(
            json.dumps({"url": p.url, "title": p.title(), "timestamp": ts}, indent=2))),
    ]:
        try:
            action(page)
            print(f"  [debug] -> {DEBUG_DIR / fn}")
        except Exception as e:
            print(f"  [debug] {fn} failed: {e}")


def run(limit=None):
    existing, seen_urls = _load_existing()
    existing_urls = set(seen_urls)  # snapshot of pre-existing URLs — used for stop condition
    cutoff_ts = _newest_timestamp(existing)

    if cutoff_ts:
        print(f"Incremental run — collecting bookmarks newer than {cutoff_ts}")
    else:
        print("No existing data — full scrape")

    if limit:
        print(f"Limit override: {limit}")

    url_map       = {}
    full_text_map = {}
    console_logs  = []
    cookies       = get_x_cookies()
    width, height = get_screen_size()

    with sync_playwright() as p:
        browser = None
        context = None
        page    = None
        try:
            browser = p.chromium.launch(headless=False, args=["--start-maximized"])
            context = browser.new_context(viewport={"width": width, "height": height})
            context.add_cookies(cookies)

            page = context.new_page()
            page.on("response",      make_response_handler(url_map, full_text_map))
            page.on("console",       lambda msg: console_logs.append({"type": msg.type, "text": msg.text}))
            page.on("pageerror",     lambda err: console_logs.append({"type": "pageerror", "text": str(err)}))
            page.on("requestfailed", lambda req: console_logs.append({"type": "requestfailed", "url": req.url, "text": req.failure or ""}))

            print("Navigating to bookmarks...")
            page.goto("https://x.com/i/bookmarks", wait_until="domcontentloaded")

            loaded = False
            for attempt in range(3):
                try:
                    page.wait_for_selector("article[data-testid='tweet']", timeout=30000)
                    loaded = True
                    break
                except Exception:
                    print(f"  attempt {attempt + 1} timed out, retrying...")
                    page.reload(wait_until="domcontentloaded")
                    time.sleep(3)

            if not loaded:
                print("Tweets did not load - saving debug snapshot...")
                save_debug(page, label="no_tweets")
                input("Browser left open - check if logged in. Press Enter to close...")
                return

            dom_snapshots = []
            stall_count   = 0
            last_count    = 0
            done          = False

            print("Scrolling and collecting...")
            while not done and stall_count < 5:
                for t in scrape_visible(page):
                    if not t["url"]:
                        continue
                    # clean stop — reached a tweet we already had BEFORE this run
                    if t["url"] in existing_urls:
                        if not limit:
                            print("Reached existing data, stopping.")
                            done = True
                        break
                    if cutoff_ts and t.get("timestamp") and t["timestamp"] <= cutoff_ts:
                        print(f"Reached existing data at {t['timestamp']}, stopping.")
                        done = True
                        break
                    if t["url"] in seen_urls:
                        continue
                    seen_urls.add(t["url"])
                    dom_snapshots.append(t)
                    if limit and len(dom_snapshots) >= limit:
                        done = True
                        break

                if not done:
                    page.evaluate(f"window.scrollBy(0, {SCROLL_STEP})")
                    time.sleep(SCROLL_PAUSE)

                    at_bottom = page.evaluate(
                        "window.scrollY + window.innerHeight >= document.body.scrollHeight - 100"
                    )
                    if at_bottom:
                        if len(dom_snapshots) == last_count:
                            stall_count += 1
                            time.sleep(STALL_SLEEP)
                        else:
                            stall_count = 0
                        last_count = len(dom_snapshots)

                print(f"  collected: {len(dom_snapshots)}  network urls seen: {len(url_map)}  stalls: {stall_count}")

            # merge after loop — all graphql responses guaranteed to have arrived
            new_results = [merge(t, url_map, full_text_map) for t in dom_snapshots]
            print(f"Scraped {len(new_results)} new bookmarks")

            combined = new_results + existing
            _save(combined)

        except Exception as e:
            print(f"FAILED: {e}")
            DEBUG_DIR.mkdir(exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            try:
                save_debug(page, label="failure")
            except Exception:
                pass
            if console_logs:
                (DEBUG_DIR / f"console_{ts}.json").write_text(json.dumps(console_logs, indent=2))
            (DEBUG_DIR / f"traceback_{ts}.txt").write_text(traceback.format_exc())
            print(f"  [debug] traceback -> {DEBUG_DIR / f'traceback_{ts}.txt'}")
            input("Press Enter to close...")

        finally:
            try:
                if context: context.close()
                if browser: browser.close()
            except Exception:
                pass

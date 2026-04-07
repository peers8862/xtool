import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

from config import LIKES_FILE, BOOKMARKS_FILE, DATA_DIR, DEBUG_DIR, DELAY_BETWEEN
from core.cookies import get_x_cookies
from core.graphql import extract_from_graphql, tweet_id_from_url
from core.merge import classify_links, TCO_RE


def _paths(kind):
    if kind == "likes":
        return (
            DATA_DIR / "x_likes_needs_enrichment.json",
            LIKES_FILE,
            DATA_DIR / "scrape_missing_likes_progress.json",
        )
    return (
        DATA_DIR / "x_bookmarks_needs_enrichment.json",
        BOOKMARKS_FILE,
        DATA_DIR / "scrape_missing_bookmarks_progress.json",
    )


def _load_progress(progress_file):
    if progress_file.exists():
        try:
            return set(json.loads(progress_file.read_text()))
        except Exception:
            pass
    return set()


def _save_progress(visited, progress_file):
    progress_file.write_text(json.dumps(list(visited)))


def _apply_network_data(tweet, url_map):
    tid = tweet_id_from_url(tweet.get("url", ""))
    net = url_map.get(tid, {})
    if not net:
        return tweet

    existing_links = tweet.get("links", {})
    existing_urls  = existing_links.get("urls", []) if isinstance(existing_links, dict) else existing_links
    merged = classify_links([], net.get("urls", []) + existing_urls)
    tweet["links"] = merged

    existing_media = tweet.get("media", [])
    for m in net.get("media", []):
        if m not in existing_media:
            existing_media.append(m)
    tweet["media"]     = existing_media
    tweet["_enriched"] = True
    tweet.pop("_enrichment_reasons", None)
    return tweet


def make_response_handler(url_map, full_text_map):
    def handle(response):
        if not any(k in response.url for k in ["TweetDetail", "TweetResultByRestId", "timeline"]):
            return
        try:
            extract_from_graphql(response.json(), url_map, full_text_map)
        except Exception as e:
            print(f"GraphQL parse error: {e}")
    return handle


def run(kind):
    flagged_file, full_file, progress_file = _paths(kind)

    if not flagged_file.exists():
        print(f"ERROR: {flagged_file} not found. Run: python xtool.py audit --type {kind}")
        return
    if not full_file.exists():
        print(f"ERROR: {full_file} not found.")
        return

    flagged   = json.loads(flagged_file.read_text(encoding="utf-8"))
    tweets    = json.loads(full_file.read_text(encoding="utf-8"))
    tweet_map = {t["url"]: i for i, t in enumerate(tweets)}

    visited   = _load_progress(progress_file)
    remaining = [t for t in flagged if t["url"] not in visited]

    print(f"Flagged: {len(flagged)} | Already visited: {len(visited)} | Remaining: {len(remaining)}")

    if not remaining:
        print(f"All flagged tweets already visited. Delete {progress_file} to re-run.")
        return

    cookies = get_x_cookies()

    url_map        = {}
    full_text_map  = {}
    enriched_count = 0

    with sync_playwright() as p:
        browser = None
        context = None
        page    = None
        try:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(viewport={"width": 1280, "height": 1800})
            context.add_cookies(cookies)
            page = context.new_page()
            page.on("response", make_response_handler(url_map, full_text_map))

            for idx, tweet in enumerate(remaining):
                tweet_url = tweet["url"]
                tid       = tweet_id_from_url(tweet_url)
                print(f"[{idx + 1}/{len(remaining)}] {tweet_url}")

                try:
                    page.goto(tweet_url, wait_until="domcontentloaded")
                    try:
                        page.wait_for_selector("article[data-testid='tweet']", timeout=15000)
                        time.sleep(1.5)
                    except Exception:
                        print("  timed out")
                        visited.add(tweet_url)
                        _save_progress(visited, progress_file)
                        time.sleep(DELAY_BETWEEN)
                        continue

                    net = url_map.get(tid, {})
                    if net.get("urls") or net.get("media"):
                        i = tweet_map.get(tweet_url)
                        if i is not None:
                            tweets[i] = _apply_network_data(tweets[i], url_map)
                            enriched_count += 1
                            links = tweets[i].get("links", {})
                            print(f"  enriched: {links.get('urls', [])}")
                    else:
                        print("  no new data found")

                    visited.add(tweet_url)
                    _save_progress(visited, progress_file)
                    full_file.write_text(json.dumps(tweets, ensure_ascii=False, indent=2), encoding="utf-8")

                except Exception as e:
                    print(f"  ERROR: {e}")
                    visited.add(tweet_url)
                    _save_progress(visited, progress_file)

                time.sleep(DELAY_BETWEEN)

            print(f"\nDone. Enriched: {enriched_count} | Output: {full_file}")

        except Exception as e:
            print(f"FATAL: {e}")
            DEBUG_DIR.mkdir(exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            (DEBUG_DIR / f"traceback_{ts}.txt").write_text(traceback.format_exc())

        finally:
            try:
                if context: context.close()
                if browser: browser.close()
            except Exception:
                pass

import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

from config import LIKES_FILE, BOOKMARKS_FILE, DATA_DIR, DEBUG_DIR, DELAY_BETWEEN
from core.cookies import get_x_cookies
from core.merge import TCO_RE


PROGRESS_FILE = DATA_DIR / "enrich_progress.json"


def _load_progress():
    if PROGRESS_FILE.exists():
        try:
            return set(json.loads(PROGRESS_FILE.read_text()))
        except Exception:
            pass
    return set()


def _save_progress(visited):
    PROGRESS_FILE.write_text(json.dumps(list(visited)))


def _needs_enrichment(tweet):
    links = tweet.get("links", {})
    urls  = links.get("urls", []) if isinstance(links, dict) else links
    return not urls and not tweet.get("media") and tweet.get("url")


def _author_handle(tweet):
    author = tweet.get("author", "")
    if isinstance(author, dict):
        return author.get("handle", "").lower()
    for part in author.split("\n"):
        part = part.strip()
        if part.startswith("@"):
            return part.lower()
    return ""


def _scrape_thread_links(page, author_handle):
    return page.evaluate("""
    (authorHandle) => {
        function resolveLink(a) {
            if (a.title && a.title.startsWith('http')) return a.title;
            return a.href;
        }
        function getLinks(scope) {
            if (!scope) return [];
            const seen = new Set();
            const links = [];
            for (const a of scope.querySelectorAll('a[href]')) {
                const h = resolveLink(a);
                if (!h || seen.has(h) || !h.startsWith('http')) continue;
                if (h.includes('/hashtag/') || h.includes('/status/') || h.includes('t.co/')) continue;
                const noProto = h.replace('https://','').replace('http://','');
                const parts = noProto.split('/');
                if ((parts[0] === 'twitter.com' || parts[0] === 'x.com') && parts.length === 2) continue;
                seen.add(h);
                links.push(h);
            }
            return links;
        }
        function getHandle(article) {
            const el = article.querySelector('[data-testid="User-Name"]');
            if (!el) return "";
            for (const p of (el.innerText || "").split("\\n")) {
                if (p.trim().startsWith("@")) return p.trim().toLowerCase();
            }
            return "";
        }
        const articles = [...document.querySelectorAll('article[data-testid="tweet"]')];
        const results = [];
        for (let i = 1; i < articles.length; i++) {
            if (getHandle(articles[i]) !== authorHandle) continue;
            const textEl = articles[i].querySelector('[data-testid="tweetText"]');
            const links  = getLinks(textEl);
            const cardEl = articles[i].querySelector('[data-testid="card.wrapper"]');
            if (cardEl) {
                for (const a of cardEl.querySelectorAll('a[href]')) {
                    const h = resolveLink(a);
                    if (h && h.startsWith('http') && !h.includes('t.co/') && !links.includes(h))
                        links.push(h);
                }
            }
            results.push(...links);
        }
        return [...new Set(results)];
    }
    """, author_handle)


def run(kind):
    input_file = LIKES_FILE if kind == "likes" else BOOKMARKS_FILE
    if not input_file.exists():
        print(f"ERROR: {input_file} not found.")
        return

    tweets     = json.loads(input_file.read_text(encoding="utf-8"))
    candidates = [t for t in tweets if _needs_enrichment(t)]
    print(f"Loaded {len(tweets)} tweets | {len(candidates)} need enrichment")

    if not candidates:
        print("Nothing to enrich.")
        return

    visited   = _load_progress()
    remaining = [t for t in candidates if t["url"] not in visited]
    print(f"Already visited: {len(visited)} | Remaining: {len(remaining)}")

    if not remaining:
        print(f"All candidates visited. Delete {PROGRESS_FILE} to re-run.")
        return

    cookies   = get_x_cookies()
    tweet_map = {t["url"]: i for i, t in enumerate(tweets)}
    enriched  = 0

    with sync_playwright() as p:
        browser = None
        context = None
        page    = None
        try:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(viewport={"width": 1280, "height": 1800})
            context.add_cookies(cookies)
            page = context.new_page()

            for idx, tweet in enumerate(remaining):
                tweet_url = tweet["url"]
                handle    = _author_handle(tweet)
                print(f"[{idx + 1}/{len(remaining)}] {tweet_url} ({handle})")

                if not handle:
                    print("  skipping - no author handle")
                    visited.add(tweet_url)
                    _save_progress(visited)
                    continue

                try:
                    page.goto(tweet_url, wait_until="domcontentloaded")
                    try:
                        page.wait_for_selector("article[data-testid='tweet']", timeout=15000)
                        time.sleep(2.0)
                    except Exception:
                        print("  timed out")
                        visited.add(tweet_url)
                        _save_progress(visited)
                        time.sleep(DELAY_BETWEEN)
                        continue

                    found = [l for l in _scrape_thread_links(page, handle) if not TCO_RE.match(l)]
                    if found:
                        print(f"  found {len(found)} link(s): {found}")
                        i = tweet_map[tweet_url]
                        tweets[i]["links"]    = {"urls": found, "mentions": []}
                        tweets[i]["_enriched"] = True
                        enriched += 1
                    else:
                        print("  no author reply links found")

                    visited.add(tweet_url)
                    _save_progress(visited)
                    input_file.write_text(json.dumps(tweets, ensure_ascii=False, indent=2), encoding="utf-8")

                except Exception as e:
                    print(f"  ERROR: {e}")
                    visited.add(tweet_url)
                    _save_progress(visited)

                time.sleep(DELAY_BETWEEN)

            print(f"\nDone. Enriched: {enriched} | Output: {input_file}")

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

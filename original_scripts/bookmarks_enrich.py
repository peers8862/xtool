import json
import time
import traceback
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright
import browser_cookie3

# === CONFIG ===
INPUT_FILE     = "x_bookmarks.json"   # or x_likes.json
OUTPUT_FILE    = "x_bookmarks_enriched.json"
PROGRESS_FILE  = "enrich_progress.json"  # tracks which tweets have been visited
ERROR_DIR      = Path("debug_output")
CHROME_PROFILE = Path("/Users/mp/Library/Application Support/Google/Chrome/Profile 1")

# Delay between tweet page visits — be polite to avoid rate limiting
DELAY_BETWEEN  = 4.0  # seconds

def get_x_cookies():
    print("Extracting cookies from Chrome...")
    cookies = []
    seen_names = set()
    for domain in [".x.com", "x.com", ".twitter.com", "twitter.com"]:
        try:
            jar = browser_cookie3.chrome(
                domain_name=domain,
                cookie_file=str(CHROME_PROFILE / "Cookies"),
            )
            for c in jar:
                key = (c.name, c.domain)
                if key not in seen_names:
                    seen_names.add(key)
                    cookies.append({
                        "name":     c.name,
                        "value":    c.value,
                        "domain":   c.domain,
                        "path":     c.path,
                        "secure":   bool(c.secure),
                        "httpOnly": False,
                        "sameSite": "Lax",
                    })
        except Exception as e:
            print("  " + domain + ": failed - " + str(e))
    print("  Total cookies: " + str(len(cookies)))
    return cookies

def load_progress():
    p = Path(PROGRESS_FILE)
    if p.exists():
        try:
            return set(json.loads(p.read_text()))
        except Exception:
            return set()
    return set()

def save_progress(visited):
    Path(PROGRESS_FILE).write_text(json.dumps(list(visited)))

def normalize_url(url):
    if not url:
        return url
    if "youtube.com/embed/" in url:
        video_id = url.split("youtube.com/embed/")[1].split("?")[0]
        return "https://www.youtube.com/watch?v=" + video_id
    return url

def extract_author_handle(author_str):
    # author field looks like "Name\n@handle\n·\ntime"
    for part in author_str.split("\n"):
        part = part.strip()
        if part.startswith("@"):
            return part.lower()
    return ""

def scrape_thread_links(page, original_author_handle):
    """
    Scrape links posted by the original author in their own thread replies.
    Only looks at articles authored by the same handle as the original tweet.
    """
    return page.evaluate("""
    (authorHandle) => {
        function resolveLink(a) {
            if (a.title && a.title.startsWith('http')) return a.title;
            if (a.getAttribute('data-expanded-url')) return a.getAttribute('data-expanded-url');
            return a.href;
        }

        function getLinks(scope) {
            if (!scope) return [];
            var seen = {};
            var links = [];
            Array.from(scope.querySelectorAll('a[href]')).forEach(function(a) {
                var h = resolveLink(a);
                if (!h || seen[h]) return;
                if (!h.startsWith('http')) return;
                if (h.includes('/hashtag/')) return;
                if (h.includes('/status/')) return;
                if (h.includes('t.co/')) return;
                var noProto = h.replace('https://','').replace('http://','');
                var parts = noProto.split('/');
                var isProfile = (
                    (parts[0] === 'twitter.com' || parts[0] === 'x.com') &&
                    parts.length === 2 && parts[1].length > 0
                );
                if (isProfile) return;
                seen[h] = true;
                links.push(h);
            });
            return links;
        }

        function getAuthorHandle(article) {
            var userEl = article.querySelector('[data-testid="User-Name"]');
            if (!userEl) return "";
            var text = userEl.innerText || "";
            var parts = text.split("\\n");
            for (var i = 0; i < parts.length; i++) {
                var p = parts[i].trim();
                if (p.startsWith("@")) return p.toLowerCase();
            }
            return "";
        }

        var articles = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
        var results = [];

        // skip the first article (that's the original tweet)
        for (var i = 1; i < articles.length; i++) {
            var article = articles[i];
            var handle = getAuthorHandle(article);
            if (handle !== authorHandle) continue;

            var textEl = article.querySelector('[data-testid="tweetText"]');
            var links  = getLinks(textEl);

            // also check card
            var cardEl = article.querySelector('[data-testid="card.wrapper"]');
            if (cardEl) {
                var cardAnchors = Array.from(cardEl.querySelectorAll('a[href]'));
                for (var j = 0; j < cardAnchors.length; j++) {
                    var resolved = resolveLink(cardAnchors[j]);
                    if (resolved && resolved.startsWith('http') && !resolved.includes('t.co/')) {
                        if (links.indexOf(resolved) === -1) links.push(resolved);
                    }
                }
            }

            if (links.length > 0) {
                results = results.concat(links);
            }
        }

        // deduplicate
        var seen = {};
        return results.filter(function(l) {
            if (seen[l]) return false;
            seen[l] = true;
            return true;
        });
    }
    """, original_author_handle)

def needs_enrichment(tweet):
    return (
        len(tweet.get("links", [])) == 0 and
        not tweet.get("cardUrl", "") and
        tweet.get("url", "")
    )

def main():
    # Load input
    input_path = Path(INPUT_FILE)
    if not input_path.exists():
        print("ERROR: " + INPUT_FILE + " not found. Run the bookmarks scraper first.")
        return

    tweets = json.loads(input_path.read_text(encoding="utf-8"))
    total  = len(tweets)
    print("Loaded " + str(total) + " tweets from " + INPUT_FILE)

    # Filter to only tweets that need enrichment
    candidates = [t for t in tweets if needs_enrichment(t)]
    print(str(len(candidates)) + " tweets need enrichment (empty links and cardUrl)")

    if not candidates:
        print("Nothing to enrich. All tweets already have links or cards.")
        return

    # Load progress — skip already-visited tweet URLs
    visited = load_progress()
    remaining = [t for t in candidates if t["url"] not in visited]
    print(str(len(visited)) + " already visited, " + str(len(remaining)) + " remaining")

    if not remaining:
        print("All candidates already visited. Delete " + PROGRESS_FILE + " to re-run.")
        return

    cookies = get_x_cookies()
    if not cookies:
        print("No X cookies found. Open Chrome, log into x.com, Cmd+Q, then retry.")
        return

    # Build a lookup map of url -> tweet index for easy update
    tweet_map = {}
    for i, t in enumerate(tweets):
        tweet_map[t["url"]] = i

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

            for idx, tweet in enumerate(remaining):
                tweet_url     = tweet["url"]
                author_handle = extract_author_handle(tweet.get("author", ""))

                print(
                    "[" + str(idx + 1) + "/" + str(len(remaining)) + "] " +
                    tweet_url +
                    " (" + author_handle + ")"
                )

                if not author_handle:
                    print("  skipping - could not parse author handle")
                    visited.add(tweet_url)
                    save_progress(visited)
                    continue

                try:
                    page.goto(tweet_url, wait_until="domcontentloaded")

                    # wait for thread to load
                    try:
                        page.wait_for_selector("article[data-testid='tweet']", timeout=15000)
                        time.sleep(2.0)  # allow replies to hydrate
                    except Exception:
                        print("  timed out waiting for thread")
                        visited.add(tweet_url)
                        save_progress(visited)
                        time.sleep(DELAY_BETWEEN)
                        continue

                    found_links = scrape_thread_links(page, author_handle)
                    found_links = [normalize_url(l) for l in found_links]

                    if found_links:
                        print("  found " + str(len(found_links)) + " link(s): " + str(found_links))
                        i = tweet_map[tweet_url]
                        tweets[i]["links"] = found_links
                        tweets[i]["enriched"] = True
                        enriched_count += 1
                    else:
                        print("  no author reply links found")

                    visited.add(tweet_url)
                    save_progress(visited)

                    # Save output after every tweet in case of interruption
                    Path(OUTPUT_FILE).write_text(
                        json.dumps(tweets, ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )

                except Exception as e:
                    print("  ERROR on " + tweet_url + ": " + str(e))
                    visited.add(tweet_url)
                    save_progress(visited)

                time.sleep(DELAY_BETWEEN)

            print("\nEnrichment complete.")
            print("  Tweets enriched with new links: " + str(enriched_count))
            print("  Output saved to: " + OUTPUT_FILE)

        except Exception as e:
            print("FATAL: " + str(e))
            ERROR_DIR.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            (ERROR_DIR / ("traceback_" + ts + ".txt")).write_text(traceback.format_exc())
            print("  traceback -> " + str(ERROR_DIR / ("traceback_" + ts + ".txt")))

        finally:
            try:
                if context: context.close()
                if browser: browser.close()
            except Exception:
                pass

if __name__ == "__main__":
    main()
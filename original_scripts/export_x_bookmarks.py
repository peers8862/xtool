import json
import re
import time
import traceback
from pathlib import Path
from datetime import datetime
from AppKit import NSScreen
from playwright.sync_api import sync_playwright
import browser_cookie3

OUTPUT_FILE    = "x_bookmarks.json"
ERROR_DIR      = Path("debug_output")
CHROME_PROFILE = Path("/Users/mp/Library/Application Support/Google/Chrome/Profile 1")

MAX_BOOKMARKS  = None  # set to a number e.g. 500, or None for all

SCROLL_STEP    = 600
SCROLL_PAUSE   = 2.5
STALL_SLEEP    = 2.0


# -------------------------------
# REGEXES
# -------------------------------
MENTION_RE = re.compile(r'^https://(www\.)?x\.com/[A-Za-z0-9_]+/?$')
TCO_RE     = re.compile(r'^https://t\.co/')


# -------------------------------
# COOKIES
# -------------------------------
def get_x_cookies():
    print("Extracting cookies from Chrome...")
    cookies = []
    seen = set()
    for domain in [".x.com", "x.com", ".twitter.com", "twitter.com"]:
        try:
            jar = browser_cookie3.chrome(
                domain_name=domain,
                cookie_file=str(CHROME_PROFILE / "Cookies"),
            )
            for c in jar:
                key = (c.name, c.domain)
                if key not in seen:
                    seen.add(key)
                    cookies.append({
                        "name": c.name,
                        "value": c.value,
                        "domain": c.domain,
                        "path": c.path,
                        "secure": bool(c.secure),
                        "httpOnly": False,
                        "sameSite": "Lax",
                    })
        except Exception as e:
            print(f"  {domain}: {e}")
    print(f"Total cookies: {len(cookies)}")
    auth = [c for c in cookies if c["name"] in ("auth_token", "ct0")]
    if not auth:
        print("WARNING: no auth_token or ct0 found")
    return cookies


# -------------------------------
# GRAPHQL PARSER
# -------------------------------
def extract_from_graphql(data, url_map, full_text_map):
    if isinstance(data, dict):
        rest_id = data.get("rest_id") or data.get("id_str")
        legacy  = data.get("legacy", {})

        if rest_id and legacy:
            urls  = []
            media = []
            quote_data = None

            # TEXT URLS
            for u in legacy.get("entities", {}).get("urls", []):
                exp = u.get("expanded_url")
                if exp and not TCO_RE.match(exp):
                    urls.append(exp)

            # MEDIA — prefer best mp4 for video/gif
            for m in legacy.get("entities", {}).get("media", []):
                if not m.get("media_url_https"):
                    continue
                media_url  = m["media_url_https"]
                media_type = m.get("type", "")
                if media_type in ("video", "animated_gif"):
                    mp4s = [v for v in m.get("video_info", {}).get("variants", []) if v.get("content_type") == "video/mp4"]
                    if mp4s:
                        media_url = max(mp4s, key=lambda v: v.get("bitrate", 0))["url"]
                media.append({"type": media_type, "url": media_url})

            # CARD LINKS — skip card_url (always t.co); check other bindings for real URL
            card = data.get("card", {})
            if card:
                binding = card.get("legacy", {}).get("binding_values", [])
                if isinstance(binding, list):
                    binding = {b["key"]: b.get("value", {}) for b in binding}
                for key in ["website_url", "url", "player_url"]:
                    val = binding.get(key, {}).get("string_value", "")
                    if val and val.startswith("http") and not TCO_RE.match(val):
                        if val not in urls:
                            urls.append(val)
                        break

            # QUOTE TWEET
            quoted = data.get("quoted_status_result", {})
            if quoted:
                try:
                    q = quoted.get("result", {})
                    if q.get("__typename") == "TweetWithVisibilityResults":
                        q = q.get("tweet", {})
                    q_legacy = q.get("legacy", {})
                    q_core   = q.get("core", {})
                    q_user_result = q_core.get("user_results", {}).get("result", {})
                    q_user_legacy = q_user_result.get("legacy", {})
                    q_user_core   = q_user_result.get("core", {})
                    screen_name   = q_user_core.get("screen_name", "") or q_user_legacy.get("screen_name", "")
                    if not screen_name:
                        print(f"Quote author missing | parent={rest_id}")
                    q_urls = [u["expanded_url"] for u in q_legacy.get("entities", {}).get("urls", [])
                              if u.get("expanded_url") and not TCO_RE.match(u["expanded_url"])]
                    q_card = q.get("card", {})
                    if q_card:
                        q_binding = q_card.get("legacy", {}).get("binding_values", [])
                        if isinstance(q_binding, list):
                            q_binding = {b["key"]: b.get("value", {}) for b in q_binding}
                        for key in ["website_url", "url", "player_url"]:
                            val = q_binding.get(key, {}).get("string_value", "")
                            if val and val.startswith("http") and not TCO_RE.match(val):
                                if val not in q_urls:
                                    q_urls.append(val)
                                break
                    q_note = q.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
                    q_text = q_note.get("text") or q_legacy.get("full_text", "")
                    quote_data = {
                        "tweetId":   q.get("rest_id"),
                        "text":      q_text,
                        "author":    screen_name,
                        "timestamp": q_legacy.get("created_at", ""),
                        "links":     q_urls,
                    }
                except Exception as e:
                    print(f"Quote parse error for {rest_id}: {e}")

            if rest_id not in url_map:
                url_map[rest_id] = {"urls": [], "media": [], "quote": None}

            url_map[rest_id]["urls"] += [u for u in urls if u not in url_map[rest_id]["urls"]]
            for m in media:
                if m not in url_map[rest_id]["media"]:
                    url_map[rest_id]["media"].append(m)
            if quote_data:
                url_map[rest_id]["quote"] = quote_data

            # FULL TEXT — prefer note_tweet for long-form content
            note = data.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
            full_text = note.get("text") or legacy.get("full_text", "")
            if full_text:
                full_text_map[rest_id] = full_text

        for v in data.values():
            extract_from_graphql(v, url_map, full_text_map)

    elif isinstance(data, list):
        for i in data:
            extract_from_graphql(i, url_map, full_text_map)


def make_response_handler(url_map, full_text_map):
    def handle(response):
        if not any(k in response.url for k in ["Bookmarks", "TweetDetail", "UserTweets", "TweetResultByRestId", "timeline"]):
            return
        try:
            extract_from_graphql(response.json(), url_map, full_text_map)
        except Exception as e:
            print(f"GraphQL parse error: {e}")
    return handle


# -------------------------------
# DOM SCRAPER
# -------------------------------
def scrape_visible(page):
    return page.evaluate(r"""
() => {

function getId(article) {
    const a = article.querySelector('a[href*="/status/"]');
    if (!a) return "";
    return a.href.split("/status/")[1]?.split("?")[0] || "";
}

function getLinks(scope) {
    if (!scope) return [];
    const seen = new Set();
    const links = [];
    for (const a of scope.querySelectorAll('a[href]')) {
        const h = a.href;
        if (!h.startsWith("http") || h.includes("/status/") || seen.has(h)) continue;
        seen.add(h);
        links.push(h);
    }
    const textEl = scope.querySelector('[data-testid="tweetText"]') || scope;
    if (textEl) {
        for (const m of (textEl.innerText || "").matchAll(/https:\/\/\n([^\s]+)/gu)) {
            const h = "https://" + m[1];
            if (!seen.has(h)) { seen.add(h); links.push(h); }
        }
    }
    return links;
}

return [...document.querySelectorAll('article[data-testid="tweet"]')]
.map(article => {
    const textEl  = article.querySelector('[data-testid="tweetText"]');
    const linkEl  = article.querySelector('a[href*="/status/"]');
    const timeEl  = article.querySelector("time");
    const userEl  = article.querySelector('[data-testid="User-Name"]');
    const quoteEl = article.querySelector('[data-testid="quoteTweet"]');

    const quoteTextEl = quoteEl?.querySelector('[data-testid="tweetText"]');
    const quoteLinkEl = quoteEl?.querySelector('a[href*="/status/"]');
    const quoteTimeEl = quoteEl?.querySelector("time");
    const quoteUserEl = quoteEl?.querySelector('[data-testid="User-Name"]');

    let outerScope = article;
    if (quoteEl) {
        const clone = article.cloneNode(true);
        clone.querySelector('[data-testid="quoteTweet"]')?.remove();
        outerScope = clone;
    }

    return {
        tweetId:   getId(article),
        url:       linkEl ? linkEl.href : "",
        text:      textEl ? textEl.innerText : "",
        author:    userEl ? userEl.innerText : "",
        timestamp: timeEl ? timeEl.getAttribute("datetime") : "",
        domLinks:  getLinks(outerScope.querySelector('[data-testid="tweetText"]')),
        quote: quoteEl ? {
            tweetId:   quoteLinkEl ? quoteLinkEl.href.split("/status/")[1]?.split("?")[0] : "",
            url:       quoteLinkEl ? quoteLinkEl.href : "",
            text:      quoteTextEl ? quoteTextEl.innerText : "",
            author:    quoteUserEl ? quoteUserEl.innerText : "",
            timestamp: quoteTimeEl ? quoteTimeEl.getAttribute("datetime") : "",
            domLinks:  getLinks(quoteTextEl),
        } : null,
    };
});

}
""")


# -------------------------------
# MERGE
# -------------------------------
def classify_links(raw_links, net_urls):
    seen = set()
    urls, mentions = [], []
    for h in net_urls + raw_links:
        if h in seen or TCO_RE.match(h):
            continue
        seen.add(h)
        if MENTION_RE.match(h):
            mentions.append(h)
        else:
            urls.append(h)
    return {"urls": urls, "mentions": mentions}


def parse_author(raw):
    parts = [p.strip() for p in raw.split("\n") if p.strip() and p.strip() != "·"]
    name   = parts[0] if len(parts) > 0 else ""
    handle = parts[1] if len(parts) > 1 else ""
    return {"name": name, "handle": handle}


def merge(dom, url_map, full_text_map):
    tid  = dom.get("tweetId")
    net  = url_map.get(tid, {})
    text = full_text_map.get(tid) or dom.get("text", "")

    q_dom = dom.get("quote")
    quote_out = None
    if q_dom:
        qid   = q_dom.get("tweetId")
        qnet  = url_map.get(qid, {})
        q_text = full_text_map.get(qid) or q_dom.get("text", "")
        # prefer graphql quote if available (has card links, note_tweet, etc.)
        gql_quote = net.get("quote")
        if gql_quote:
            quote_out = gql_quote
            # fill in any DOM fallback fields graphql may have missed
            if not quote_out.get("text"):
                quote_out["text"] = q_text
        else:
            quote_out = {
                "tweetId":   qid,
                "text":      q_text,
                "author":    parse_author(q_dom.get("author", "")),
                "timestamp": q_dom.get("timestamp", ""),
                "links":     classify_links(q_dom.get("domLinks", []), qnet.get("urls", [])),
                "media":     qnet.get("media", []),
            }

    return {
        "url":       dom.get("url"),
        "text":      text,
        "author":    parse_author(dom.get("author", "")),
        "timestamp": dom.get("timestamp"),
        "links":     classify_links(dom.get("domLinks", []), net.get("urls", [])),
        "media":     net.get("media", []),
        "quote":     quote_out,
    }


# -------------------------------
# DEBUG
# -------------------------------
def save_debug(page, label="error"):
    ERROR_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for fn, action in [
        (f"{label}_{ts}.png",       lambda p: p.screenshot(path=str(ERROR_DIR / f"{label}_{ts}.png"), full_page=True)),
        (f"{label}_{ts}.html",      lambda p: (ERROR_DIR / f"{label}_{ts}.html").write_text(p.content(), encoding="utf-8")),
        (f"{label}_{ts}_meta.json", lambda p: (ERROR_DIR / f"{label}_{ts}_meta.json").write_text(
            json.dumps({"url": p.url, "title": p.title(), "timestamp": ts}, indent=2))),
    ]:
        try:
            action(page)
            print(f"  [debug] -> {ERROR_DIR / fn}")
        except Exception as e:
            print(f"  [debug] {fn} failed: {e}")


# -------------------------------
# MAIN
# -------------------------------
def get_screen_size():
    frame = NSScreen.mainScreen().frame()
    return int(frame.size.width), int(frame.size.height)


def main():
    console_logs  = []
    url_map       = {}
    full_text_map = {}
    cookies       = get_x_cookies()

    if not cookies:
        print("No X cookies found.\n1. Open Chrome and log into x.com\n2. Quit Chrome with Cmd+Q\n3. Run this script again")
        return

    limit_msg = f"up to {MAX_BOOKMARKS}" if MAX_BOOKMARKS else "all"
    print(f"Will collect {limit_msg} bookmarks")

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
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                if console_logs:
                    ERROR_DIR.mkdir(exist_ok=True)
                    (ERROR_DIR / f"console_{ts}.json").write_text(json.dumps(console_logs, indent=2))
                input("Browser left open - check if logged in. Press Enter to close...")
                context.close()
                browser.close()
                return

            seen          = set()
            dom_snapshots = []
            stall_count   = 0
            last_count    = 0

            print("Scrolling and collecting...")
            while stall_count < 5:
                if MAX_BOOKMARKS and len(dom_snapshots) >= MAX_BOOKMARKS:
                    print(f"  limit of {MAX_BOOKMARKS} reached, stopping")
                    break

                for t in scrape_visible(page):
                    if MAX_BOOKMARKS and len(dom_snapshots) >= MAX_BOOKMARKS:
                        break
                    if t["url"] and t["url"] not in seen:
                        seen.add(t["url"])
                        dom_snapshots.append(t)

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
            results = [merge(t, url_map, full_text_map) for t in dom_snapshots]
            if MAX_BOOKMARKS:
                results = results[:MAX_BOOKMARKS]

            Path(OUTPUT_FILE).write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            print(f"Saved {len(results)} bookmarks -> {OUTPUT_FILE}")

        except Exception as e:
            print(f"FAILED: {e}")
            ERROR_DIR.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            try:
                save_debug(page, label="failure")
            except Exception:
                print("  [debug] could not capture page state")
            if console_logs:
                (ERROR_DIR / f"console_{ts}.json").write_text(json.dumps(console_logs, indent=2))
                print(f"  [debug] console -> {ERROR_DIR / f'console_{ts}.json'}")
            (ERROR_DIR / f"traceback_{ts}.txt").write_text(traceback.format_exc())
            print(f"  [debug] traceback -> {ERROR_DIR / f'traceback_{ts}.txt'}")
            input("Press Enter to close...")

        finally:
            try:
                if context: context.close()
                if browser: browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()

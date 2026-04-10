import json
import re
import time
from pathlib import Path
from AppKit import NSScreen
from playwright.sync_api import sync_playwright
import browser_cookie3

OUTPUT_FILE    = "x_likes_test_all.json"
CHROME_PROFILE = Path("/Users/mp/Library/Application Support/Google/Chrome/Profile 1")
X_USERNAME     = "peers8862"

MAX_LIKES      = None
SCROLL_STEP    = 900
SCROLL_PAUSE   = 1.5


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
            print(f"{domain}: {e}")

    print("Total cookies:", len(cookies))
    return cookies


# -------------------------------
# REGEXES
# -------------------------------
MENTION_RE = re.compile(r'^https://(www\.)?x\.com/[A-Za-z0-9_]+/?$')
TCO_RE     = re.compile(r'^https://t\.co/')

# -------------------------------
# GRAPHQL PARSER
# -------------------------------
def extract_from_graphql(data, url_map, full_text_map):
    if isinstance(data, dict):
        rest_id = data.get("rest_id") or data.get("id_str")
        legacy  = data.get("legacy", {})

        if rest_id and legacy:

            urls = []
            media = []
            quote_data = None

            # TEXT URLS
            for u in legacy.get("entities", {}).get("urls", []):
                exp = u.get("expanded_url")
                if exp:
                    urls.append(exp)

            # MEDIA
            for m in legacy.get("entities", {}).get("media", []):
                if m.get("media_url_https"):
                    media.append({
                        "type": m.get("type"),
                        "url": m.get("media_url_https")
                    })

            # CARD LINKS — skip card_url (always t.co); use card_url expanded via entities instead
            card = data.get("card", {})
            if card:
                for b in card.get("legacy", {}).get("binding_values", []):
                    if b.get("key") == "card_url":
                        val = b.get("value", {}).get("string_value")
                        if val and not TCO_RE.match(val):
                            urls.append(val)

            # QUOTE TWEET
            quoted = data.get("quoted_status_result", {})
            if quoted:
                try:
                    q = quoted.get("result", {})
                    typename = q.get("__typename", "")
                    if typename == "TweetWithVisibilityResults":
                        q = q.get("tweet", {})
                    q_legacy = q.get("legacy", {})
                    q_core   = q.get("core", {})
                    q_user_results = q_core.get("user_results", {})
                    q_user_result  = q_user_results.get("result", {})
                    q_user_core    = q_user_result.get("core", {})
                    q_user_legacy  = q_user_result.get("legacy", {})
                    screen_name    = q_user_core.get("screen_name", "") or q_user_legacy.get("screen_name", "")
                    if not screen_name:
                        print(f"Quote author still missing | parent={rest_id} | core={q_user_core}")
                    q_urls = [u["expanded_url"] for u in q_legacy.get("entities", {}).get("urls", []) if u.get("expanded_url") and not TCO_RE.match(u["expanded_url"])]
                    q_card = q.get("card", {})
                    if q_card:
                        for b in q_card.get("legacy", {}).get("binding_values", []):
                            if b.get("key") == "card_url":
                                val = b.get("value", {}).get("string_value")
                                if val and not TCO_RE.match(val) and val not in q_urls:
                                    q_urls.append(val)
                    q_note = q.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
                    q_text = q_note.get("text") or q_legacy.get("full_text", "")
                    quote_data = {
                        "tweetId": q.get("rest_id"),
                        "text": q_text,
                        "author": screen_name,
                        "timestamp": q_legacy.get("created_at", ""),
                        "links": q_urls,
                    }
                except Exception as e:
                    print(f"Quote parse error for {rest_id}: {e}")

            if rest_id not in url_map:
                url_map[rest_id] = {"urls": [], "media": [], "quote": None}

            # DEDUPE
            url_map[rest_id]["urls"] += [u for u in urls if u not in url_map[rest_id]["urls"]]

            for m in media:
                if m not in url_map[rest_id]["media"]:
                    url_map[rest_id]["media"].append(m)

            if quote_data:
                url_map[rest_id]["quote"] = quote_data

            # FULL TEXT — prefer note_tweet for long-form content
            note = data.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
            full_text = note.get("text") or legacy.get("full_text", "")
            if rest_id and full_text:
                full_text_map[rest_id] = full_text

        for v in data.values():
            extract_from_graphql(v, url_map, full_text_map)

    elif isinstance(data, list):
        for i in data:
            extract_from_graphql(i, url_map, full_text_map)


def make_response_handler(url_map, full_text_map):
    def handle(response):
        if "/graphql/" not in response.url or "Likes" not in response.url:
            return
        try:
            extract_from_graphql(response.json(), url_map, full_text_map)
        except Exception as e:
            print(f"GraphQL parse error: {e}")
    return handle


# -------------------------------
# DOM SCRAPER (TEXT + FALLBACK LINKS)
# -------------------------------
def scrape_visible(page):
    return page.evaluate(r"""
() => {

function getId(article) {
    const a = article.querySelector('a[href*="/status/"]');
    if (!a) return "";
    return a.href.split("/status/")[1]?.split("?")[0] || "";
}

function getLinks(article) {
    const seen = new Set();
    const links = [];
    const selectors = [
        '[data-testid="tweetText"] a[href]',
        '[data-testid="card.wrapper"] a[href]',
        'a[href*="t.co"]'
    ];
    for (const sel of selectors) {
        for (const a of article.querySelectorAll(sel)) {
            const h = a.href;
            if (h.startsWith("http") && !h.includes("/status/") && !seen.has(h)) {
                seen.add(h);
                links.push(h);
            }
        }
    }
    const textEl = article.querySelector('[data-testid="tweetText"]');
    if (textEl) {
        for (const m of textEl.innerText.matchAll(/https:\/\/\n([^\s]+)/gu)) {
            const h = "https://" + m[1];
            if (!seen.has(h)) { seen.add(h); links.push(h); }
        }
    }
    return links;
}

return [...document.querySelectorAll('article[data-testid="tweet"]')]
.map(a => {
    const textEl = a.querySelector('[data-testid="tweetText"]');
    const linkEl = a.querySelector('a[href*="/status/"]');
    const timeEl = a.querySelector("time");
    const userEl = a.querySelector('[data-testid="User-Name"]');

    return {
        tweetId: getId(a),
        url: linkEl ? linkEl.href : "",
        text: textEl ? textEl.innerText : "",
        author: userEl ? userEl.innerText : "",
        timestamp: timeEl ? timeEl.getAttribute("datetime") : "",
        domLinks: getLinks(a),
    };
});

}
""")


# -------------------------------
# MERGE
# -------------------------------
def classify_links(raw_links, net_urls):
    # net_urls are already expanded; raw_links are DOM fallback only
    # always strip t.co — they are never useful in output
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
    tid = dom.get("tweetId")
    net = url_map.get(tid, {})
    text = full_text_map.get(tid) or dom.get("text", "")
    return {
        "url": dom.get("url"),
        "text": text,
        "author": parse_author(dom.get("author", "")),
        "timestamp": dom.get("timestamp"),
        "links": classify_links(dom.get("domLinks", []), net.get("urls", [])),
        "media": net.get("media", []),
        "quote": net.get("quote")
    }


# -------------------------------
# MAIN
# -------------------------------
def get_screen_size():
    frame = NSScreen.mainScreen().frame()
    return int(frame.size.width), int(frame.size.height)


def main():
    url_map      = {}
    full_text_map = {}
    cookies      = get_x_cookies()
    width, height = get_screen_size()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--start-maximized"]
        )

        context = browser.new_context(viewport={"width": width, "height": height})
        context.add_cookies(cookies)

        page = context.new_page()
        page.on("response", make_response_handler(url_map, full_text_map))

        page.goto(f"https://x.com/{X_USERNAME}/likes")
        page.wait_for_selector("article[data-testid='tweet']")

        seen    = set()
        dom_snapshots = []  # collect all dom snapshots, merge after loop
        results = []
        stall   = 0

        while len(dom_snapshots) < MAX_LIKES:
            tweets = scrape_visible(page)
            new_count = 0

            for t in tweets:
                if t["url"] and t["url"] not in seen:
                    seen.add(t["url"])
                    dom_snapshots.append(t)
                    new_count += 1

            page.evaluate(f"window.scrollBy(0, {SCROLL_STEP})")
            time.sleep(SCROLL_PAUSE)

            if new_count == 0:
                stall += 1
                if stall >= 5:
                    print(f"Stall {stall}: waiting 3s for feed to catch up...")
                    time.sleep(3)
                if stall >= 8:
                    print("No new tweets after 8 scrolls, stopping.")
                    break
            else:
                stall = 0

            print(f"Collected: {len(dom_snapshots)} | URL map: {len(url_map)}")

        # merge after loop — all graphql responses guaranteed to have arrived
        for t in dom_snapshots:
            results.append(merge(t, url_map, full_text_map))

        Path(OUTPUT_FILE).write_text(
            json.dumps(results[:MAX_LIKES], indent=2),
            encoding="utf-8"
        )
        print("Saved", len(results[:MAX_LIKES]), "tweets")


if __name__ == "__main__":
    main()
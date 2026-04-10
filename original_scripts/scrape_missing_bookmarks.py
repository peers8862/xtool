import json
import re
import time
import traceback
from pathlib import Path
from datetime import datetime
from playwright.sync_api import sync_playwright
import browser_cookie3

INPUT_FLAGGED  = "x_bookmarks_needs_enrichment.json"
INPUT_FULL     = "x_bookmarks.json"
OUTPUT_FILE    = "x_bookmarks_enriched.json"
PROGRESS_FILE  = "scrape_missing_bookmarks_progress.json"
ERROR_DIR      = Path("debug_output")
CHROME_PROFILE = Path("/Users/mp/Library/Application Support/Google/Chrome/Profile 1")

DELAY_BETWEEN  = 3.0

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

def reconstruct_urls_from_text(text):
    if not text:
        return []
    cleaned = re.sub(r'(https?://)\s*\n\s*', r'\1', text)
    cleaned = re.sub(r'([a-zA-Z0-9/._%-])\s*\n\s*([a-zA-Z0-9/._%-])', r'\1\2', cleaned)
    found = re.findall(r'https?://[^\s\n"\'<>]+', cleaned)
    results = []
    for u in found:
        u = u.rstrip('.,;)')
        if "t.co" not in u and len(u) > 15:
            results.append(u)
    return results

def extract_urls_from_graphql(data, url_map):
    if isinstance(data, dict):
        rest_id = data.get("rest_id") or data.get("id_str")
        legacy  = data.get("legacy", {})

        if rest_id and legacy:
            urls = []
            for u in legacy.get("entities", {}).get("urls", []):
                exp = u.get("expanded_url", "")
                if not exp: continue
                if "twitter.com" in exp and "/status/" not in exp and "/i/article/" not in exp: continue
                if "x.com" in exp and "/status/" not in exp and "/i/article/" not in exp: continue
                urls.append(exp)

            media = []
            for m in legacy.get("entities", {}).get("media", []):
                media_url  = m.get("media_url_https", "")
                media_type = m.get("type", "")
                if media_url:
                    if media_type in ("video", "animated_gif"):
                        variants = m.get("video_info", {}).get("variants", [])
                        mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
                        if mp4s:
                            best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                            media_url = best.get("url", media_url)
                    media.append({"type": media_type, "url": media_url})

            card_url   = ""
            card_title = ""
            card_desc  = ""
            card = data.get("card", {})
            if card:
                legacy_card = card.get("legacy", {})
                binding = legacy_card.get("binding_values", {})
                if isinstance(binding, list):
                    binding = {b["key"]: b.get("value", {}) for b in binding}
                card_url_binding = binding.get("card_url", {})
                card_url = (
                    card_url_binding.get("scribe_value", {}).get("page", "") or
                    card_url_binding.get("string_value", "")
                )
                if not card_url or "t.co" in card_url:
                    for key in ["website_url", "url", "player_url"]:
                        val = binding.get(key, {}).get("string_value", "")
                        if val and val.startswith("http") and "t.co" not in val:
                            card_url = val
                            break
                if not card_url or "t.co" in card_url:
                    for u in legacy.get("entities", {}).get("urls", []):
                        exp = u.get("expanded_url", "")
                        if exp and "t.co" not in exp:
                            card_url = exp
                            break
                card_title = (
                    binding.get("title",       {}).get("string_value", "") or
                    binding.get("description", {}).get("string_value", "")
                )
                card_desc = (
                    binding.get("description",       {}).get("string_value", "") or
                    binding.get("card_description",  {}).get("string_value", "")
                )

            if rest_id not in url_map:
                url_map[rest_id] = {"urls": [], "cardUrl": "", "cardTitle": "", "cardDesc": "", "media": []}
            url_map[rest_id]["urls"] += [u for u in urls if u not in url_map[rest_id]["urls"]]
            for m in media:
                if m not in url_map[rest_id]["media"]:
                    url_map[rest_id]["media"].append(m)
            if card_url:   url_map[rest_id]["cardUrl"]   = card_url
            if card_title: url_map[rest_id]["cardTitle"] = card_title
            if card_desc:  url_map[rest_id]["cardDesc"]  = card_desc

        for v in data.values():
            extract_urls_from_graphql(v, url_map)

    elif isinstance(data, list):
        for item in data:
            extract_urls_from_graphql(item, url_map)

def make_response_handler(url_map):
    def handle_response(response):
        url = response.url
        if not any(k in url for k in ["TweetDetail", "TweetResultByRestId", "timeline"]):
            return
        try:
            data = response.json()
            extract_urls_from_graphql(data, url_map)
        except Exception:
            pass
    return handle_response

def get_tweet_id_from_url(url):
    if "/status/" in url:
        parts = url.split("/status/")
        return parts[1].split("/")[0].split("?")[0]
    return ""

def apply_network_data(tweet, url_map):
    tid = get_tweet_id_from_url(tweet.get("url", ""))
    net = url_map.get(tid, {})

    net_urls   = net.get("urls", [])
    existing   = tweet.get("links", [])
    all_links  = list(dict.fromkeys(net_urls + existing))
    real_urls  = [l for l in all_links if "t.co" not in l]
    tco_only   = [l for l in all_links if "t.co" in l]
    final_links = [normalize_url(l) for l in (real_urls if real_urls else tco_only)]

    card_url = normalize_url(net.get("cardUrl", "") or tweet.get("cardUrl", ""))
    if card_url in ("https://twitter.com", "https://x.com", "https://twitter.com/", "https://x.com/"):
        card_url = ""
    if card_url and "t.co" in card_url and real_urls:
        card_url = ""

    card_title = net.get("cardTitle", "") or tweet.get("cardTitle", "")
    card_desc  = net.get("cardDesc",  "") or tweet.get("cardDesc",  "")
    if card_desc == card_title:
        card_desc = ""

    for u in reconstruct_urls_from_text(card_title):
        if u not in final_links:
            final_links.append(u)

    media = tweet.get("media", [])
    for m in net.get("media", []):
        if m not in media:
            media.append(m)

    tweet["links"]     = final_links
    tweet["cardUrl"]   = card_url
    tweet["cardTitle"] = card_title
    tweet["cardDesc"]  = card_desc
    tweet["media"]     = media
    tweet["_enriched"] = True
    tweet.pop("_enrichment_reasons", None)
    return tweet

def main():
    if not Path(INPUT_FLAGGED).exists():
        print("ERROR: " + INPUT_FLAGGED + " not found. Run audit_bookmarks.py first.")
        return
    if not Path(INPUT_FULL).exists():
        print("ERROR: " + INPUT_FULL + " not found.")
        return

    flagged = json.loads(Path(INPUT_FLAGGED).read_text(encoding="utf-8"))
    tweets  = json.loads(Path(INPUT_FULL).read_text(encoding="utf-8"))

    print("Flagged tweets to enrich : " + str(len(flagged)))
    print("Total tweets in full file: " + str(len(tweets)))

    tweet_map = {t["url"]: i for i, t in enumerate(tweets)}
    visited   = load_progress()
    remaining = [t for t in flagged if t["url"] not in visited]
    print("Already visited : " + str(len(visited)))
    print("Remaining       : " + str(len(remaining)))

    if not remaining:
        print("All flagged tweets already visited.")
        print("Delete " + PROGRESS_FILE + " to re-run.")
        return

    cookies = get_x_cookies()
    if not cookies:
        print("No X cookies found. Open Chrome, log into x.com, Cmd+Q, then retry.")
        return

    url_map        = {}
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
            page.on("response", make_response_handler(url_map))

            for idx, tweet in enumerate(remaining):
                tweet_url = tweet["url"]
                tid       = get_tweet_id_from_url(tweet_url)
                print("[" + str(idx + 1) + "/" + str(len(remaining)) + "] " + tweet_url)

                try:
                    page.goto(tweet_url, wait_until="domcontentloaded")
                    try:
                        page.wait_for_selector("article[data-testid='tweet']", timeout=15000)
                        time.sleep(1.5)
                    except Exception:
                        print("  timed out")
                        visited.add(tweet_url)
                        save_progress(visited)
                        time.sleep(DELAY_BETWEEN)
                        continue

                    net = url_map.get(tid, {})
                    if net.get("urls") or net.get("cardUrl") or net.get("media"):
                        i = tweet_map.get(tweet_url)
                        if i is not None:
                            tweets[i] = apply_network_data(tweets[i], url_map)
                            enriched_count += 1
                            print("  enriched: links=" + str(tweets[i].get("links", [])) + " media=" + str(len(tweets[i].get("media", []))))
                    else:
                        print("  no new data found")

                    visited.add(tweet_url)
                    save_progress(visited)
                    Path(OUTPUT_FILE).write_text(
                        json.dumps(tweets, ensure_ascii=False, indent=2),
                        encoding="utf-8"
                    )

                except Exception as e:
                    print("  ERROR: " + str(e))
                    visited.add(tweet_url)
                    save_progress(visited)

                time.sleep(DELAY_BETWEEN)

            print("\nDone.")
            print("Enriched : " + str(enriched_count))
            print("Output   : " + OUTPUT_FILE)

        except Exception as e:
            print("FATAL: " + str(e))
            ERROR_DIR.mkdir(exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            (ERROR_DIR / ("traceback_" + ts + ".txt")).write_text(traceback.format_exc())

        finally:
            try:
                if context: context.close()
                if browser: browser.close()
            except Exception:
                pass

if __name__ == "__main__":
    main()
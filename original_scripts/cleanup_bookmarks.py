import json
import re
from pathlib import Path

INPUT_FILE  = "x_bookmarks_enriched2.json"
OUTPUT_FILE = "x_bookmarks_clean.json"

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

def clean_tweet(tweet):
    links = tweet.get("links", [])
    real  = [normalize_url(l) for l in links if "t.co" not in l]
    tco   = [l for l in links if "t.co" in l]
    tweet["links"] = real if real else tco

    card_url = normalize_url(tweet.get("cardUrl", ""))
    if card_url in ("https://twitter.com", "https://x.com", "https://twitter.com/", "https://x.com/"):
        card_url = ""
    if card_url and "t.co" in card_url and real:
        card_url = ""
    tweet["cardUrl"] = card_url

    card_title = tweet.get("cardTitle", "")
    card_desc  = tweet.get("cardDesc",  "")
    if card_desc == card_title:
        tweet["cardDesc"] = ""

    for u in reconstruct_urls_from_text(card_title):
        if u not in tweet["links"]:
            tweet["links"].append(u)

    q = tweet.get("quote")
    if q:
        q_links = q.get("links", [])
        q_real  = [normalize_url(l) for l in q_links if "t.co" not in l]
        q_tco   = [l for l in q_links if "t.co" in l]
        q["links"] = q_real if q_real else q_tco

        q_card = normalize_url(q.get("cardUrl", ""))
        if q_card in ("https://twitter.com", "https://x.com", "https://twitter.com/", "https://x.com/"):
            q_card = ""
        q["cardUrl"] = q_card

        q_title = q.get("cardTitle", "")
        q_desc  = q.get("cardDesc",  "")
        if q_desc == q_title:
            q["cardDesc"] = ""

        for u in reconstruct_urls_from_text(q_title):
            if u not in q["links"]:
                q["links"].append(u)

        tweet["quote"] = q

    tweet.pop("_enrichment_reasons", None)
    tweet.pop("_enriched", None)
    return tweet

def main():
    path = Path(INPUT_FILE)
    if not path.exists():
        print("ERROR: " + INPUT_FILE + " not found.")
        return

    tweets = json.loads(path.read_text(encoding="utf-8"))
    print("Loaded " + str(len(tweets)) + " tweets from " + INPUT_FILE)

    fixed_youtube    = 0
    fixed_card_dedup = 0
    fixed_fragments  = 0
    fixed_bad_card   = 0

    for tweet in tweets:
        orig_links     = list(tweet.get("links", []))
        orig_card_url  = tweet.get("cardUrl", "")
        orig_card_desc = tweet.get("cardDesc", "")

        tweet = clean_tweet(tweet)

        if tweet.get("cardUrl", "") != orig_card_url:
            fixed_bad_card += 1
        if tweet.get("cardDesc", "") != orig_card_desc:
            fixed_card_dedup += 1
        for l in tweet.get("links", []):
            if l not in orig_links:
                if "youtube.com/watch" in l:
                    fixed_youtube += 1
                else:
                    fixed_fragments += 1

    Path(OUTPUT_FILE).write_text(
        json.dumps(tweets, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print("Cleanup complete:")
    print("  YouTube embed -> watch URLs fixed : " + str(fixed_youtube))
    print("  Duplicate cardDesc cleared        : " + str(fixed_card_dedup))
    print("  Fragmented URLs reconstructed     : " + str(fixed_fragments))
    print("  Bad cardUrls cleared              : " + str(fixed_bad_card))
    print("Output -> " + OUTPUT_FILE)

if __name__ == "__main__":
    main()
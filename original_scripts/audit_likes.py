import json
from pathlib import Path

INPUT_FILE  = "x_likes.json"
OUTPUT_FILE = "x_likes_needs_enrichment.json"
REPORT_FILE = "x_likes_audit_report.txt"

def needs_enrichment(tweet):
    no_links   = len(tweet.get("links", [])) == 0
    no_card    = not tweet.get("cardUrl", "")
    has_text   = bool(tweet.get("text", "").strip())
    truncated  = tweet.get("text", "").rstrip().endswith("...")
    is_article = any(
        "x.com/i/article/" in l or "twitter.com/i/article/" in l
        for l in tweet.get("links", [])
    )
    # flag if no links and no cardUrl
    # exclude: pure image/video tweets (no text, no card = intentionally empty)
    # exclude: already has article link (that is the content)
    if is_article:
        return False
    if no_links and no_card:
        return True
    return False

def classify(tweet):
    text      = tweet.get("text", "").strip()
    truncated = text.endswith("...")
    no_text   = not text
    card_title = tweet.get("cardTitle", "")
    card_desc  = tweet.get("cardDesc", "")

    reasons = []
    if truncated:
        reasons.append("truncated_text")
    if no_text:
        reasons.append("no_text")
    if not card_title and not card_desc:
        reasons.append("no_card_data")
    if card_title and card_desc and card_title == card_desc:
        reasons.append("duplicate_card_fields")
    return reasons

def main():
    path = Path(INPUT_FILE)
    if not path.exists():
        print("ERROR: " + INPUT_FILE + " not found.")
        return

    tweets = json.loads(path.read_text(encoding="utf-8"))
    total  = len(tweets)
    print("Loaded " + str(total) + " tweets from " + INPUT_FILE)

    flagged    = []
    stats      = {
        "total":                total,
        "needs_enrichment":     0,
        "truncated_text":       0,
        "no_text":              0,
        "no_card_data":         0,
        "duplicate_card_fields":0,
        "has_links":            0,
        "has_card_url":         0,
        "has_quote":            0,
    }

    for tweet in tweets:
        if tweet.get("links"):
            stats["has_links"] += 1
        if tweet.get("cardUrl"):
            stats["has_card_url"] += 1
        if tweet.get("quote"):
            stats["has_quote"] += 1

        reasons = classify(tweet)
        for r in reasons:
            if r in stats:
                stats[r] += 1

        if needs_enrichment(tweet):
            stats["needs_enrichment"] += 1
            tweet["_enrichment_reasons"] = reasons
            flagged.append(tweet)

    # write flagged tweets
    Path(OUTPUT_FILE).write_text(
        json.dumps(flagged, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # write human readable report
    lines = [
        "=== X Likes Audit Report ===",
        "",
        "Input file    : " + INPUT_FILE,
        "Total tweets  : " + str(stats["total"]),
        "",
        "--- Coverage ---",
        "Has links     : " + str(stats["has_links"]) + " (" + str(round(stats["has_links"] / total * 100, 1)) + "%)",
        "Has cardUrl   : " + str(stats["has_card_url"]) + " (" + str(round(stats["has_card_url"] / total * 100, 1)) + "%)",
        "Has quote     : " + str(stats["has_quote"]) + " (" + str(round(stats["has_quote"] / total * 100, 1)) + "%)",
        "",
        "--- Issues ---",
        "Needs enrichment     : " + str(stats["needs_enrichment"]),
        "Truncated text       : " + str(stats["truncated_text"]),
        "No text at all       : " + str(stats["no_text"]),
        "No card data         : " + str(stats["no_card_data"]),
        "Duplicate card fields: " + str(stats["duplicate_card_fields"]),
        "",
        "--- Output ---",
        "Flagged tweets written to: " + OUTPUT_FILE,
    ]
    report = "\n".join(lines)
    print(report)
    Path(REPORT_FILE).write_text(report)
    print("\nReport saved to: " + REPORT_FILE)

if __name__ == "__main__":
    main()
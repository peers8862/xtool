import json
from pathlib import Path

from config import LIKES_FILE, BOOKMARKS_FILE, DATA_DIR


def needs_enrichment(tweet):
    links = tweet.get("links", {})
    urls  = links.get("urls", []) if isinstance(links, dict) else links
    is_article = any(
        "x.com/i/article/" in u or "twitter.com/i/article/" in u
        for u in urls
    )
    if is_article:
        return False
    return not urls and not tweet.get("media")


def classify(tweet):
    text   = tweet.get("text", "").strip()
    links  = tweet.get("links", {})
    urls   = links.get("urls", []) if isinstance(links, dict) else links
    reasons = []
    if text.endswith("..."):
        reasons.append("truncated_text")
    if not text:
        reasons.append("no_text")
    if not urls:
        reasons.append("no_links")
    return reasons


def run(kind):
    if kind == "likes":
        input_file  = LIKES_FILE
        output_file = DATA_DIR / "x_likes_needs_enrichment.json"
        report_file = DATA_DIR / "x_likes_audit_report.txt"
        label       = "X Likes"
    else:
        input_file  = BOOKMARKS_FILE
        output_file = DATA_DIR / "x_bookmarks_needs_enrichment.json"
        report_file = DATA_DIR / "x_bookmarks_audit_report.txt"
        label       = "X Bookmarks"

    if not input_file.exists():
        print(f"ERROR: {input_file} not found.")
        return

    tweets = json.loads(input_file.read_text(encoding="utf-8"))
    total  = len(tweets)
    print(f"Loaded {total} tweets from {input_file}")

    flagged = []
    stats   = {
        "total":            total,
        "needs_enrichment": 0,
        "truncated_text":   0,
        "no_text":          0,
        "no_links":         0,
        "has_links":        0,
        "has_media":        0,
        "has_quote":        0,
    }

    for tweet in tweets:
        links = tweet.get("links", {})
        urls  = links.get("urls", []) if isinstance(links, dict) else links
        if urls:              stats["has_links"] += 1
        if tweet.get("media"): stats["has_media"] += 1
        if tweet.get("quote"): stats["has_quote"] += 1

        reasons = classify(tweet)
        for r in reasons:
            if r in stats:
                stats[r] += 1

        if needs_enrichment(tweet):
            stats["needs_enrichment"] += 1
            tweet["_enrichment_reasons"] = reasons
            flagged.append(tweet)

    output_file.write_text(json.dumps(flagged, ensure_ascii=False, indent=2), encoding="utf-8")

    pct = lambda n: f"{round(n / total * 100, 1)}%" if total else "0%"
    lines = [
        f"=== {label} Audit Report ===",
        "",
        f"Input file   : {input_file}",
        f"Total tweets : {total}",
        "",
        "--- Coverage ---",
        f"Has links    : {stats['has_links']} ({pct(stats['has_links'])})",
        f"Has media    : {stats['has_media']} ({pct(stats['has_media'])})",
        f"Has quote    : {stats['has_quote']} ({pct(stats['has_quote'])})",
        "",
        "--- Issues ---",
        f"Needs enrichment : {stats['needs_enrichment']}",
        f"Truncated text   : {stats['truncated_text']}",
        f"No text          : {stats['no_text']}",
        f"No links         : {stats['no_links']}",
        "",
        "--- Output ---",
        f"Flagged tweets -> {output_file}",
    ]
    report = "\n".join(lines)
    print(report)
    report_file.write_text(report)
    print(f"\nReport saved -> {report_file}")

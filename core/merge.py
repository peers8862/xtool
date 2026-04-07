import re

MENTION_RE = re.compile(r'^https://(www\.)?x\.com/[A-Za-z0-9_]+/?$')
TCO_RE     = re.compile(r'^https://t\.co/')


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
    return {
        "name":   parts[0] if len(parts) > 0 else "",
        "handle": parts[1] if len(parts) > 1 else "",
    }


def merge(dom, url_map, full_text_map):
    tid  = dom.get("tweetId")
    net  = url_map.get(tid, {})
    text = full_text_map.get(tid) or dom.get("text", "")

    q_dom     = dom.get("quote")
    quote_out = None
    if q_dom:
        qid       = q_dom.get("tweetId")
        qnet      = url_map.get(qid, {})
        q_text    = full_text_map.get(qid) or q_dom.get("text", "")
        gql_quote = net.get("quote")
        if gql_quote:
            quote_out = gql_quote
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

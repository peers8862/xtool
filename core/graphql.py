import re

TCO_RE = re.compile(r'^https://t\.co/')


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
                    mp4s = [v for v in m.get("video_info", {}).get("variants", [])
                            if v.get("content_type") == "video/mp4"]
                    if mp4s:
                        media_url = max(mp4s, key=lambda v: v.get("bitrate", 0))["url"]
                media.append({"type": media_type, "url": media_url})

            # CARD — check real URL bindings, skip card_url (always t.co)
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
                    q_legacy      = q.get("legacy", {})
                    q_user_result = q.get("core", {}).get("user_results", {}).get("result", {})
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
            note      = data.get("note_tweet", {}).get("note_tweet_results", {}).get("result", {})
            full_text = note.get("text") or legacy.get("full_text", "")
            if full_text:
                full_text_map[rest_id] = full_text

        for v in data.values():
            extract_from_graphql(v, url_map, full_text_map)

    elif isinstance(data, list):
        for i in data:
            extract_from_graphql(i, url_map, full_text_map)


def tweet_id_from_url(url):
    if "/status/" in url:
        return url.split("/status/")[1].split("/")[0].split("?")[0]
    return ""

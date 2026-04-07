# Lessons Learned: xtool — X Scraping Toolkit

## What This Tool Does

Scrapes liked tweets and bookmarks from X into structured JSON. Each record captures the tweet URL, full text, author (name + handle), timestamp, media, outbound links (split into resource URLs and @mentions), and quoted tweet data. It works by combining two data sources — the live DOM for tweet identity and structure, and intercepted GraphQL network responses for rich data that isn't fully rendered in HTML.

The tool is structured as a CLI (`xtool.py`) with shared core modules and per-command logic. All scraping logic is shared between likes and bookmarks via `core/`.

---

## Architecture: Why Two Sources

- **The DOM** gives you what's visible — tweet text, author name, timestamp, the status URL. But it's lossy. Text gets truncated at "Show more". Links appear as rendered anchor tags which may be split across lines. Media and quote data are partially rendered or behind lazy-load.
- **The GraphQL response** gives you the raw data model — `full_text`, expanded URLs, media objects, quote tweet objects with full author info. But it's keyed by `rest_id`, not by position on screen, so you can't use it alone without knowing which tweet is which.

The solution is to use the DOM to establish identity (tweet URL → tweet ID) and the GraphQL response to enrich everything else. The `url_map` and `full_text_map` dictionaries in `core/graphql.py` are the bridge between the two.

---

## Key Problems Encountered and How They Were Solved

### 1. Quote tweet author was always empty
The GraphQL `quoted_status_result.result` object sometimes has `__typename: TweetWithVisibilityResults`, which wraps the actual tweet one level deeper under a `tweet` key. Fix: check `__typename` and unwrap before reading `legacy` and `core`.

### 2. Tweet text was truncated
DOM `innerText` on `[data-testid="tweetText"]` stops at the "Show more" fold. GraphQL always returns `legacy.full_text` in full. Fix: populate `full_text_map` during GraphQL parsing and prefer it over DOM text in `merge()`. Long-form tweets also have a `note_tweet` field that takes priority over `full_text`.

### 3. Links were underpopulated
Three separate causes:
- DOM `getLinks` was missing links in card preview elements (`[data-testid="card.wrapper"]`)
- Some tweet texts render URLs split across a line break as `https://\n<domain>` in `innerText` — not valid anchor hrefs. Fix: regex scan `innerText` for the broken pattern and reconstruct
- Quote tweet links were never extracted. Fix: pull `entities.urls[].expanded_url` from `q_legacy`

### 4. t.co links appearing in output
Two sources of leakage: the DOM scraper picks up raw `t.co` hrefs, and the card's `card_url` binding is always a `t.co` link. Fix: strip t.co unconditionally in `classify_links` — never store them regardless of whether an expanded equivalent exists. The GraphQL `entities.urls` always provides the expanded form.

### 5. Quote tweet card links missing
The quoted tweet's card URL was never extracted. Fix: read `website_url`, `url`, `player_url` bindings from `q.get("card", {})` the same way as the parent tweet's card.

### 6. Mentions mixed in with resource links
`@mentions` render as `<a href="https://x.com/username">` — indistinguishable from external links at DOM level. Fix: classify in Python using a regex matching `x.com/<handle>` with no further path. Output as `links: { "urls": [...], "mentions": [...] }`.

### 7. Stale merge — GraphQL arriving after DOM scrape
The original loop scraped DOM and immediately called `merge()`. For tweets near the top of the viewport, the GraphQL response hadn't arrived yet. Fix: collect all DOM snapshots first, finish the scroll loop, then merge. By the time scrolling completes all network responses have landed.

### 8. Incremental stop triggering stall detection instead of clean exit
When running incrementally, the scroll loop kept seeing already-known tweet URLs, `new_count` stayed 0, and the stall counter fired instead of a clean stop. Fix: when a URL already in `seen_urls` is encountered during a non-limit run, set `done = True` immediately and break.

### 11. False stop on full scrape when no existing file
After collecting the first batch of tweets, scrolling back over the same viewport caused those freshly-collected URLs to match `seen_urls`, incorrectly triggering "Reached existing data" even with no prior file. Root cause: `seen_urls` was serving double duty — both deduplication within the run and the stop condition against prior data. Fix: split into two sets. `existing_urls` is snapshotted from the file before the run starts and is the only thing checked for the stop condition. `seen_urls` grows during the run and is only used to skip duplicates within the current scroll session.

### 9. Grey gap at bottom of browser window
`viewport=None` with `--start-maximized` on macOS doesn't fully work in Playwright's Chromium. Fix: set viewport explicitly to actual screen dimensions using `NSScreen.mainScreen().frame()` via `pyobjc` — no extra browser process needed.

### 10. Bare `except: pass` hiding failures
Silent exception swallowing on quote parsing meant structural changes in the GraphQL response produced `quote: null` with no indication why. Fix: always `except Exception as e: print(...)` with context so failures are visible during a run.

---

## Incremental Scraping Logic

On each run the tool:
1. Loads the existing dataset from `data/x_likes.json` (or bookmarks)
2. Reads the timestamp of the newest stored tweet as `cutoff_ts`
3. Scrolls and collects only tweets newer than `cutoff_ts` or not yet in `seen_urls`
4. Stops immediately when a known URL is encountered
5. Prepends new results to the existing dataset and saves

Use `--limit N` to override incremental mode and force collect a specific number regardless of what's already stored — useful for first runs or full re-pulls.

---

## Data Flow

```
Chrome cookies (browser_cookie3)
        ↓
Playwright Chromium browser
        ↓
page.on("response") → core/graphql.py → url_map, full_text_map
page.evaluate()     → core/dom.py     → dom_snapshots
        ↓
core/merge.py → merge(dom, url_map, full_text_map)
        ↓
data/x_likes.json  (cumulative, newest first)
exports/likes_YYYY-MM-DD_HHMM.json  (timestamped snapshot)
```

---

## GraphQL Response Structure Reference

Key paths within a tweet object:
```
result.rest_id                                    → tweet ID
result.legacy.full_text                           → complete tweet text
result.note_tweet.note_tweet_results.result.text  → long-form text (prefer over full_text)
result.legacy.entities.urls[]                     → { url (t.co), expanded_url }
result.legacy.entities.media[]                    → { media_url_https, type, video_info }
result.card.legacy.binding_values[]               → card data (website_url, url, player_url)
result.quoted_status_result.result                → quoted tweet (may be TweetWithVisibilityResults)
result.core.user_results.result.legacy            → author info (screen_name, name)
```

---

## DOM Selector Reference

| Selector | What it finds |
|---|---|
| `article[data-testid="tweet"]` | Each tweet card |
| `[data-testid="tweetText"]` | The visible tweet body text |
| `[data-testid="User-Name"]` | Author display name + handle block |
| `[data-testid="card.wrapper"]` | Link preview card |
| `[data-testid="quoteTweet"]` | Quoted tweet block |
| `a[href*="/status/"]` | The permalink anchor (used to extract tweet ID) |
| `time` | Timestamp element (`getAttribute("datetime")` for ISO string) |

---

## General Lessons for This Kind of Scraping

**Intercept the network, don't just parse the DOM.** Modern SPAs like X render a fraction of their data model into HTML. The real data lives in the API responses. Playwright's `page.on("response", ...)` gives you everything the browser receives.

**DOM and network data have a timing relationship.** The browser fires the response event before finishing rendering. Always let at least one scroll-pause cycle elapse before merging — collect snapshots first, merge after the loop.

**`data-testid` attributes are your most stable hook.** Class names on X change constantly. `data-testid` values are tied to component identity and change far less often.

**GraphQL responses are recursive and schema-less from the outside.** Walk the whole tree and collect everything that looks like a tweet object (`rest_id` + `legacy` present). This handles pagination wrappers, timeline entries, and nested quotes without special-casing each level.

**`__typename` is a reliable discriminator.** When X wraps objects (e.g. `TweetWithVisibilityResults`), `__typename` tells you what you're looking at before you assume the shape of `result`.

**Strip t.co unconditionally.** Never store `t.co` links as a final value. They rot when tweets are deleted. The GraphQL `entities.urls` always provides the expanded destination URL.

**`innerText` is layout-aware.** It inserts `\n` at block boundaries, which is why `https://` and the domain can end up on separate lines. Use GraphQL `full_text` where possible; otherwise account for the line-break pattern with a regex.

**Cookies from a real browser session are the simplest auth strategy.** Extracting cookies from Chrome via `browser_cookie3` sidesteps token rotation entirely — you're continuing a session the user already has. Chrome must be fully closed when the script runs.

**Keep browser-side JS dumb.** `page.evaluate` runs synchronously and blocks rendering. Collect raw data only — all classification, deduplication, and transformation belongs in Python.

**Stall detection is not optional.** Infinite scroll feeds can end, rate-limit, or hiccup. Always track whether each scroll produced new content and bail after a few dry iterations.

**Screen size matters.** More viewport height = more tweets rendered per scroll = fewer cycles = less chance of hitting rate limits. Always set viewport to actual screen dimensions.

---

## Project Structure

```
twitter-extraction/
├── xtool.py              ← CLI entry point
├── config.py             ← user config and paths
├── core/
│   ├── cookies.py        ← Chrome cookie extraction
│   ├── graphql.py        ← GraphQL response parser + tweet_id_from_url
│   ├── dom.py            ← DOM scraper (page.evaluate)
│   └── merge.py          ← classify_links, parse_author, merge
├── commands/
│   ├── likes.py          ← likes scrape logic
│   ├── bookmarks.py      ← bookmarks scrape logic
│   ├── audit.py          ← audit command
│   ├── scrape_missing.py ← scrape-missing command
│   └── enrich.py         ← enrich command
├── data/                 ← canonical JSON outputs and audit files
├── exports/              ← timestamped snapshots per run
├── debug_output/         ← failure screenshots and tracebacks
└── original_scripts/     ← original standalone scripts (archived)
```

---

## Python Environment Setup

```bash
python3 -m venv venv_playwright
source venv_playwright/bin/activate
pip install playwright browser-cookie3 pyobjc-framework-Cocoa
playwright install chromium
```

`playwright install chromium` downloads the actual browser binary — it is not captured by `pip freeze` and must be run manually after every fresh install.

| Package | Why it's needed |
|---|---|
| `playwright` | Controls Chromium, intercepts network responses, evaluates JS in the page |
| `browser-cookie3` | Reads cookies from Chrome profile for authenticated session |
| `pyobjc-framework-Cocoa` | macOS only — provides `NSScreen` to read actual screen dimensions |

To freeze dependencies:
```bash
pip freeze > requirements.txt
```

### macOS notes
- Terminal needs Full Disk Access in System Settings → Privacy & Security → Full Disk Access for `browser_cookie3` to read the Chrome Cookies file
- Chrome must be fully closed before running — `browser_cookie3` reads the Cookies SQLite file directly from disk
- If the Playwright browser window doesn't open, check System Settings → Privacy & Security for a blocked executable

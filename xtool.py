#!/usr/bin/env python3
"""
xtool — X (Twitter) scraping CLI

Usage:
  python xtool.py start                        # first-time setup — configure username and Chrome profile
  python xtool.py likes                        # incremental scrape (auto-detects new since last run)
  python xtool.py likes --limit 100            # force collect exactly 100 (ignores cutoff)
  python xtool.py bookmarks                    # incremental scrape
  python xtool.py bookmarks --limit 500        # force collect exactly 500
  python xtool.py audit --type likes           # audit likes for missing data
  python xtool.py audit --type bookmarks       # audit bookmarks for missing data
  python xtool.py scrape-missing --type likes  # enrich flagged likes via individual page visits
  python xtool.py scrape-missing --type bookmarks
  python xtool.py enrich --type likes          # enrich via author thread scraping
  python xtool.py enrich --type bookmarks
"""
import argparse
import sys


def main():
    parser = argparse.ArgumentParser(prog="xtool", description="X scraping toolkit")
    sub    = parser.add_subparsers(dest="command")

    sub.add_parser("start", help="Interactive setup — configure username and Chrome profile")

    p_likes = sub.add_parser("likes", help="Scrape liked tweets")
    p_likes.add_argument("--limit", type=int, default=None, help="Max tweets to collect (overrides incremental)")

    p_bm = sub.add_parser("bookmarks", help="Scrape bookmarks")
    p_bm.add_argument("--limit", type=int, default=None, help="Max tweets to collect (overrides incremental)")

    p_audit = sub.add_parser("audit", help="Audit a dataset for missing data")
    p_audit.add_argument("--type", choices=["likes", "bookmarks"], required=True)

    p_sm = sub.add_parser("scrape-missing", help="Enrich flagged tweets via individual page visits")
    p_sm.add_argument("--type", choices=["likes", "bookmarks"], required=True)

    p_enrich = sub.add_parser("enrich", help="Enrich tweets via author thread scraping")
    p_enrich.add_argument("--type", choices=["likes", "bookmarks"], required=True)

    args = parser.parse_args()

    if args.command == "start":
        from commands.start import run
        run()

    elif args.command == "likes":
        from commands.likes import run
        run(limit=args.limit)

    elif args.command == "bookmarks":
        from commands.bookmarks import run
        run(limit=args.limit)

    elif args.command == "audit":
        from commands.audit import run
        run(kind=args.type)

    elif args.command == "scrape-missing":
        from commands.scrape_missing import run
        run(kind=args.type)

    elif args.command == "enrich":
        from commands.enrich import run
        run(kind=args.type)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

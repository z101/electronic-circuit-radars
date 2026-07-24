import argparse
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from db import Database
from scraper import RlocmanScraper
from config import RZ_CATEGORIES
from analyzer.normalizer import normalize_scores
from analyzer.report import generate_report, generate_report_text

logger = logging.getLogger("rlocman-radar")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "rlocman.db")


def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def create_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rlocman-radar",
        description="CLI for rlocman.ru/shem/ article radar"
    )

    g_info = p.add_argument_group("Information options")
    g_info.add_argument("--db-summary", action="store_true", help="Show database summary")
    g_info.add_argument("--db-schema", action="store_true", help="Show database schema")
    g_info.add_argument("--latest", type=int, metavar="N", nargs="?", const=20,
                        help="Show N latest articles")

    g_fetch = p.add_argument_group("Fetch options")
    g_fetch.add_argument("--fetch", type=str, metavar="RZ", nargs="?",
                         const="auto", help="Fetch articles by category RZ code")
    g_fetch.add_argument("--fetch-all", action="store_true",
                         help="Fetch all categories")
    g_fetch.add_argument("--fetch-article", type=int, metavar="DI",
                         help="Fetch a single article by DI")

    g_flags = p.add_argument_group("Flags (I/R)")
    g_flags.add_argument("--mark-interesting", type=int, nargs="+", metavar="DI",
                         help="Mark articles as interesting")
    g_flags.add_argument("--unmark-interesting", type=int, nargs="+", metavar="DI",
                         help="Unmark interesting")
    g_flags.add_argument("--mark-read", type=int, nargs="+", metavar="DI",
                         help="Mark articles as read")
    g_flags.add_argument("--unmark-read", type=int, nargs="+", metavar="DI",
                         help="Unmark read")
    g_flags.add_argument("--list-interesting", action="store_true",
                         help="List interesting articles")
    g_flags.add_argument("--list-unread", action="store_true",
                         help="List unread articles")

    g_other = p.add_argument_group("Other options")
    g_other.add_argument("--json", action="store_true", help="Output as JSON")
    g_other.add_argument("--log-level", default="INFO",
                         choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    sub = p.add_subparsers(dest="subcommand")

    sp_search = sub.add_parser("search", help="Search pipeline commands")
    sp_search.add_argument("action", choices=["init", "get-batch", "set-batch", "status", "report"])
    sp_search.add_argument("index", type=int, nargs="?", default=None)
    sp_search.add_argument("--query-file", type=str, required=True)
    sp_search.add_argument("--batch-size", type=int, default=2000)
    sp_search.add_argument("--batch-file", type=str, action="append")
    sp_search.add_argument("--top", type=int, default=None)
    sp_search.add_argument("--min-score", type=int, default=0)
    sp_search.add_argument("--compact", action="store_true")
    sp_search.add_argument("--raw", action="store_true")

    return p


def _resolve_rz(value: str) -> str:
    if value == "auto":
        return None
    if value in RZ_CATEGORIES:
        return value
    name_lower = value.lower().replace(" ", "")
    for rz, name in RZ_CATEGORIES.items():
        if name_lower == name.lower().replace(" ", ""):
            return rz
    return value


def _read_query(path: str) -> tuple:
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read().strip()
    name = os.path.splitext(os.path.basename(path))[0]
    import hashlib
    qhash = hashlib.md5(text.encode("utf-8")).hexdigest()
    return text, name, qhash


def _format_article_brief(a: dict) -> str:
    title = (a.get("title_ru") or a.get("title_en") or "?")[:48]
    author = (a.get("author") or "?")[:18]
    di = a.get("di", 0)
    return f"[{di:>7d}] {title:50s} {author:20s}"


def handle_info(args, db: Database):
    if args.db_summary:
        summary = db.get_db_summary()
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(f"Total articles: {summary['total_articles']}")
            if summary["by_category"]:
                for name, cnt in sorted(summary["by_category"].items()):
                    print(f"  {name}: {cnt}")
            if summary["last_session"]:
                ls = summary["last_session"]
                print(f"Last session: {ls.get('status', '?')} "
                      f"({ls.get('articles_fetched', 0)} articles)")
        return True

    if args.db_schema:
        tables = db.get_schema()
        if args.json:
            print(json.dumps(tables, ensure_ascii=False, indent=2))
        else:
            for t in tables:
                print(f"\nTable: {t['table']}")
                for c in t["columns"]:
                    print(f"  {c['name']:25s} {c['type']}")
        return True

    if args.latest is not None:
        articles = db.get_latest_articles(args.latest)
        if args.json:
            print(json.dumps(articles, ensure_ascii=False, indent=2, default=str))
        else:
            if not articles:
                print("No articles in database.")
            else:
                for a in articles:
                    print(_format_article_brief(a))
        return True

    return False


def handle_fetch(args, db: Database):
    scraper = RlocmanScraper()

    if args.fetch_article is not None:
        di = args.fetch_article
        meta = {"di": di, "category_rz": None, "summary_ru": None, "english_url": None}
        article = scraper._fetch_article(meta)
        if article:
            db.insert_article(article)
            print(f"Fetched article {di}: {article.get('title_ru', '?')}")
        else:
            print(f"Article {di} not found")
        return True

    if args.fetch_all:
        for rz in sorted(RZ_CATEGORIES.keys()):
            _fetch_category(rz, db, scraper)
        return True

    if args.fetch:
        if args.fetch == "auto":
            print("Available categories:")
            for rz, name in sorted(RZ_CATEGORIES.items()):
                print(f"  {rz}: {name}")
            print("\nUse --fetch <RZ> to fetch a specific category")
            return True
        rz = _resolve_rz(args.fetch)
        if rz not in RZ_CATEGORIES:
            print(f"Unknown category: {args.fetch}")
            return True
        _fetch_category(rz, db, scraper)
        return True

    return False


def _fetch_category(rz: str, db: Database, scraper: RlocmanScraper = None):
    session_id = db.create_session(rz=rz)
    start = time.time()
    category_name = RZ_CATEGORIES.get(rz, rz)
    print(f"Fetching category: {category_name} (rz={rz})...")

    if scraper is None:
        scraper = RlocmanScraper()

    full = scraper.fetch_category(rz, db=db)

    if full:
        db.insert_articles_batch(full)

    elapsed = time.time() - start
    db.finish_session(session_id, "completed",
                      pages=0, articles=len(full))
    print(f"Done. {len(full)} new articles in {elapsed:.0f}s.")


def handle_flags(args, db: Database):
    if args.mark_interesting:
        db.mark_interesting(args.mark_interesting)
        print(f"Marked interesting: {args.mark_interesting}")
        return True
    if args.unmark_interesting:
        db.unmark_interesting(args.unmark_interesting)
        print(f"Unmarked interesting: {args.unmark_interesting}")
        return True
    if args.mark_read:
        db.mark_read(args.mark_read)
        print(f"Marked read: {args.mark_read}")
        return True
    if args.unmark_read:
        db.unmark_read(args.unmark_read)
        print(f"Unmarked read: {args.unmark_read}")
        return True
    if args.list_interesting:
        articles = db.get_interesting()
        if args.json:
            print(json.dumps(articles, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"Interesting articles ({len(articles)}):")
            for a in articles:
                print(_format_article_brief(a))
        return True
    if args.list_unread:
        articles = db.get_unread()
        if args.json:
            print(json.dumps(articles, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"Unread articles ({len(articles)}):")
            for a in articles:
                print(_format_article_brief(a))
        return True
    return False


def handle_search(args, db: Database):
    query_text, query_name, query_hash = _read_query(args.query_file)

    if args.action == "init":
        status = db.get_search_status(query_hash)
        print(f"Query: {query_name}")
        print(f"Total articles: {status['total']}")
        print(f"Already scored: {status['scored']}")
        print(f"Pending: {status['pending']}")
        if status["pending"] > 0:
            batches = (status["pending"] + args.batch_size - 1) // args.batch_size
            print(f"Batches of {args.batch_size}: {batches}")
        return True

    if args.action == "get-batch":
        batch_index = args.index if args.index is not None else 0
        candidates = db.get_articles_for_search(
            query_hash, batch_index, args.batch_size
        )
        if args.compact:
            for c in candidates:
                body = (c.get("body_ru") or "")[:500]
                summary = c.get("summary_ru") or ""
                text = f"[{c['id']}] {c.get('title_ru', '')}\n"
                text += f"Author: {c.get('author', '')}  "
                text += f"Components: {c.get('components', '')}\n"
                if summary:
                    text += f"Summary: {summary[:300]}\n"
                if body:
                    text += f"Body: {body[:500]}...\n"
                out = {"id": c["id"], "text": text}
                print(json.dumps(out, ensure_ascii=False))
        else:
            print(json.dumps(candidates, ensure_ascii=False, indent=2, default=str))
        return True

    if args.action == "set-batch":
        if not args.batch_file:
            print("Error: --batch-file required for set-batch")
            return True
        for bf in args.batch_file:
            with open(bf, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                pid = item.get("id") or item.get("di")
                article = db.get_article(pid)
                if not article:
                    logger.warning("Article %s not found, skipping", pid)
                    continue
                ch = db._get_conn().execute(
                    "SELECT content_hash FROM articles WHERE di=?", (pid,)
                ).fetchone()
                ch = ch["content_hash"] if ch else ""
                score = item.get("relevance") or item.get("score", 0)
                comment = item.get("reason") or item.get("comment", "")
                db.save_search_result(
                    pid, query_hash, query_name, query_text, ch,
                    score, comment=comment
                )
        print(f"Saved batch from {len(args.batch_file)} file(s)")
        return True

    if args.action == "status":
        status = db.get_search_status(query_hash)
        print(f"Query: {query_name}")
        print(f"Total: {status['total']}")
        print(f"Scored: {status['scored']}")
        print(f"Pending: {status['pending']}")
        return True

    if args.action == "report":
        xlsx_path = generate_report(db, query_hash, query_name,
                                     min_score=args.min_score, top=args.top,
                                     raw=args.raw)
        text_report = generate_report_text(db, query_hash, query_name,
                                            min_score=args.min_score, top=args.top,
                                            raw=args.raw)
        if text_report:
            print(text_report)
        return True

    return False


def main(argv: list = None):
    parser = create_parser()
    args = parser.parse_args(argv)

    setup_logging(args.log_level)

    db_path = os.path.abspath(DB_PATH)
    db = Database(db_path)

    if args.subcommand == "search":
        handle_search(args, db)
        return

    action_handled = (
        handle_info(args, db)
        or handle_fetch(args, db)
        or handle_flags(args, db)
    )

    if not action_handled:
        parser.print_help()


if __name__ == "__main__":
    main()
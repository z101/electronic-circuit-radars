import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_src = Path(__file__).parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from database import Database
from scraper.fetcher import InstructablesSession, ProjectFetcher
from scraper.logging import setup_logging
from scraper.parser import KNOWN_CATEGORIES, parse_archive_page, parse_project_jsonld
from xlsx_exporter import export_search_xlsx

logger = logging.getLogger(__name__)


def _read_query(path: str) -> tuple:
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read().strip()
    name = os.path.splitext(os.path.basename(path))[0]
    qhash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return text, name, qhash


def scrape_category_pages(
    db: Database,
    category: str,
    session: InstructablesSession,
    session_id: int,
    max_pages: int | None = None,
    dry_run: bool = False,
) -> int:
    """Scrape the project listing page via Playwright."""
    base_url = "https://www.instructables.com/circuits/leds/projects/"
    logger.info("Fetching: %s", base_url)

    html = session.fetch_html(base_url)
    articles = parse_archive_page(html)

    if not articles:
        logger.warning("No articles found. Site structure may have changed.")
        return 0

    if dry_run:
        print(f"\n=== DRY RUN: {category} ===")
        print(f"Found {len(articles)} projects on page 1")
        print()
        for a in articles[:5]:
            tags = ", ".join(a["tags"]) if a["tags"] else "\u2014"
            print(f"  {a['title']}")
            print(f"    URL: {a['url']}")
            print(f"    Author: {a['author']}  Tags: {tags}")
            print()
        if len(articles) > 5:
            print(f"  ... and {len(articles) - 5} more")
        return len(articles)

    now = datetime.now(timezone.utc).isoformat()
    for a in articles:
        db.upsert_article(category, a["title"], a["url"], session_id, now,
                          a["date"], a["excerpt"], a["tags"], a["author"])
    db.mark_page_done(category, 1, session_id, len(articles))
    db.finish_session(session_id, "running", total_pages=1, total_found=len(articles))

    logger.info("Scraping complete: %d articles collected", len(articles))
    return len(articles)


def enrich_articles(
    db: Database,
    category: str,
    session_id: int,
    workers: int = 50,
):
    """Parallel enrichment of articles via JSON-LD from individual project pages.

    Uses ProjectFetcher (requests + ThreadPoolExecutor) instead of Playwright.
    """
    from tqdm import tqdm

    articles = db.get_articles_for_full_text(category)
    if not articles:
        logger.info("No articles waiting for full text.")
        return

    logger.info("Enriching %d articles via parallel HTTP (workers=%d)...", len(articles), workers)

    fetcher = ProjectFetcher(workers=workers)
    results = fetcher.fetch_many(articles)

    full_count = 0
    error_count = 0
    for item in tqdm(results, desc="Enriching articles", unit="article"):
        if item["html"] is None:
            db.mark_article_error(item["id"])
            error_count += 1
            continue

        data = parse_project_jsonld(item["html"])
        db.update_article_full_text(
            item["id"],
            content_raw=item["html"],
            content_md=data.get("content_md"),
            session_id=session_id,
            author=data.get("author"),
        )

        # Also update date and excerpt if we got them
        if data.get("date") or data.get("excerpt"):
            fields = []
            params = []
            if data.get("date"):
                fields.append("date = ?")
                params.append(data["date"])
            if data.get("excerpt"):
                fields.append("excerpt = ?")
                params.append(data["excerpt"])
            if fields:
                params.append(item["id"])
                db._execute(
                    f"UPDATE articles SET {', '.join(fields)} WHERE id = ?",
                    params,
                )

        full_count += 1

    logger.info("Enrichment done: %d enriched, %d errors", full_count, error_count)


def handle_search(args, db):
    query_text, query_name, query_hash = _read_query(args.query_file)

    if args.action == "init":
        status = db.get_search_status(args.category, query_hash)
        print(f"Query: {query_name}")
        print(f"Hash: {query_hash}")
        print(f"Total articles: {status['total_articles']}")
        print(f"Already scored: {status['scored']}")
        print(f"Pending: {status['pending']}")
        if status["pending"] > 0:
            batches = (status["pending"] + args.batch_size - 1) // args.batch_size
            print(f"Batches of {args.batch_size}: {batches}")
        return

    if args.action == "get-batch":
        batch_index = args.index if args.index is not None else 0
        candidates = db.get_search_candidates_batch(args.category, query_hash, batch_index, args.batch_size)
        if args.compact:
            for c in candidates:
                tags_str = ", ".join(c["tags"]) if c["tags"] else ""
                text = f"{c['title']} | {c['excerpt']} | {tags_str} | {c['content_md']}"
                print(json.dumps({"id": c["id"], "text": text}, ensure_ascii=False))
        else:
            print(json.dumps(candidates, ensure_ascii=False, indent=2))
        return

    if args.action == "set-batch":
        if not args.batch_file:
            print("Error: --batch-file required for set-batch")
            return 1
        for bf in args.batch_file:
            with open(bf, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                aid = item.get("id")
                article = db.get_article(aid)
                if not article:
                    logger.warning("Article %s not found, skipping", aid)
                    continue
                ch = db._compute_content_hash(
                    article.get("content_md"), article.get("title", ""), article.get("excerpt", "")
                )
                score = item.get("relevance") or item.get("score", 0)
                comment = item.get("reason") or item.get("comment", "")
                db.save_search_result(aid, query_hash, ch, score, comment=comment,
                                      query_name=query_name, query_text=query_text)
        print(f"Saved batch from {len(args.batch_file)} file(s)")
        return

    if args.action == "status":
        status = db.get_search_status(args.category, query_hash)
        print(f"Query: {query_name}")
        print(f"Hash: {query_hash}")
        print(f"Total: {status['total_articles']}")
        print(f"Scored: {status['scored']}")
        print(f"Pending: {status['pending']}")
        return

    if args.action == "report":
        report = db.get_search_report(args.category, query_hash, min_total=args.min_score, top=args.top)
        if not report:
            print(f"No results with score >= {args.min_score}.")
            return
        print(f"\nQuery: {query_name}")
        print(f"Hash: {query_hash}")
        print(f"Threshold: >= {args.min_score}  |  Passed: {len(report)}")
        print()
        print(f"  {'ID':>4}  {'Score':>5}  {'Date':<12}  {'Author':<18}  Title")
        print(f"  {'---':>4}  {'-----':>5}  {'----':<12}  {'------':<18}  -----")
        for r in report:
            print(f"  {r['id']:>4}  {r['total']:>5}  {r['date']:<12}  {r['author']:<18}  {r['title']}")
            if r.get("comment"):
                print(f"  {'':>4}  {'':>5}  {'':>12}  {'':>18}  -> {r['comment']}")

        # Export XLSX if requested
        if args.export_xlsx:
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            if args.export_xlsx == "auto":
                reports_dir = Path(__file__).resolve().parent.parent.parent.parent.parent / "reports" / "instructables-radar"
                reports_dir.mkdir(parents=True, exist_ok=True)
                xlsx_path = str(reports_dir / f"search_{query_name}_min{args.min_score}_{today}.xlsx")
            else:
                xlsx_path = args.export_xlsx
            path = export_search_xlsx(report, xlsx_path)
            print(f"\nXLSX: {path}")
        return


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape and analyze Instructables LED projects",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s -c leds --dry-run              # Preview first page\n"
            "  %(prog)s -c leds                        # Scrape metadata\n"
            "  %(prog)s -f                             # Enrich existing articles\n"
            "  %(prog)s -c leds -f                     # Scrape + enrich\n"
            "  %(prog)s --db-summary -c leds           # Show DB stats\n"
            "  %(prog)s --workers 100 -f               # 100 parallel requests\n"
        ),
    )
    parser.add_argument("--category", "-c", help="Category slug")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db", help="Path to database")
    parser.add_argument("--workers", type=int, default=50, metavar="N",
                        help="Parallel HTTP workers for enrichment (default: 50)")

    scrape = parser.add_argument_group("Scraping options")
    scrape.add_argument("--full-text", "-f", action="store_true",
                        help="Enrich articles with full text (parallel HTTP + JSON-LD)")
    scrape.add_argument("--max-pages", type=int, metavar="N")
    scrape.add_argument("--dry-run", action="store_true")

    info = parser.add_argument_group("Information options")
    info.add_argument("--list-categories", action="store_true")
    info.add_argument("--db-schema", action="store_true")
    info.add_argument("--db-summary", action="store_true")
    info.add_argument("--search", type=str, metavar="TEXT")
    info.add_argument("--latest", type=int, metavar="N")

    # ── Search pipeline subcommand ──
    sub = parser.add_subparsers(dest="command")
    sp_search = sub.add_parser("search", help="Search pipeline (keyword scoring + scoring)")
    sp_search.add_argument("action", choices=["init", "get-batch", "set-batch", "status", "report"])
    sp_search.add_argument("index", type=int, nargs="?", default=None,
                           help="Batch index (for get-batch)")
    sp_search.add_argument("--query-file", type=str, required=True,
                           help="Path to query .md file")
    sp_search.add_argument("--category", "-c", default="leds",
                           help="Category slug (default: leds)")
    sp_search.add_argument("--batch-size", type=int, default=100, metavar="N",
                           help="Batch size (default: 100)")
    sp_search.add_argument("--batch-file", type=str, action="append",
                           help="Batch file(s) with scored results")
    sp_search.add_argument("--top", type=int, default=None, metavar="N",
                           help="Top N results for report (default: all)")
    sp_search.add_argument("--min-score", type=int, default=50, metavar="N",
                           help="Minimum score threshold for report (default: 50)")
    sp_search.add_argument("--compact", action="store_true",
                           help="Compact JSON output (one object per line)")
    sp_search.add_argument("--export-xlsx", type=str, nargs="?", const="auto", metavar="PATH",
                           help="Export report to XLSX (default: reports/search_<query>_<date>.xlsx)")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    log_level = "DEBUG" if args.verbose else "INFO"
    setup_logging(log_level)

    db_path = args.db or "data/instructables.db"
    db = Database(db_path)

    # ── Search pipeline ──
    if args.command == "search":
        return handle_search(args, db)

    if args.list_categories:
        print(f"\nKnown categories ({len(KNOWN_CATEGORIES)}):")
        for slug, name, url in KNOWN_CATEGORIES:
            print(f"  {slug:20s} {name}")
            print(f"  {'':20s} {url}")
        return 0

    if args.db_schema:
        schema = db.get_schema()
        for table in schema:
            print(f"\nTable: {table['table']}")
            print("-" * (len(table["table"]) + 8))
            for c in table["columns"]:
                pk = " PK" if c["pk"] else ""
                nn = " NOT NULL" if c["notnull"] else ""
                print(f"  {c['name']:20s} {c['type']}{pk}{nn}")
        return 0

    if args.db_summary:
        if args.category:
            info = db.get_category_info(args.category)
            if not info or info["total_articles"] == 0:
                print(f"No articles for '{args.category}'.")
                return 0
            print(f"\nCategory: {args.category}")
            print(f"  Articles:   {info['total_articles']}")
            print(f"  Full texts: {info['full_text_count']}")
            session = db.get_session_info(args.category)
            if session:
                print(f"  Last session: #{session['id']} ({session['status']})")
                print(f"    Pages: {session['total_pages']}, Articles: {session['total_found']}")
        else:
            cats = db.get_categories()
            if not cats:
                print("No articles in database.")
            else:
                print(f"\nCategories ({len(cats)}):")
                for c in cats:
                    print(f"  {c['name']:20s} {c['count']} articles")
        return 0

    if args.search:
        if not args.category:
            logger.error("--category is required")
            return 1
        results = db.search_articles(args.search, args.category)
        if not results:
            print(f"No articles found matching '{args.search}'.")
            return 0
        print(f"\nFound {len(results)} article(s):")
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['title']} ({r['date']})")
            print(f"   URL: {r['url']}")
        return 0

    if args.latest:
        if not args.category:
            logger.error("--category is required")
            return 1
        articles = db.list_latest_articles(args.category, args.latest)
        if not articles:
            print(f"No articles for '{args.category}'.")
            return 1
        for a in articles:
            print(f"[{a['id']}] {a['title']} ({a['date']})")
            print(f"  {a['url']}")
        return 0

    # ── Enrichment-only mode (no category = fetch from all) ──
    if args.full_text and not args.category:
        cats = db.get_categories()
        if not cats:
            print("No articles in database. Use -c leds to scrape first.")
            return 0
        for c in cats:
            session_id = db.create_session(c["name"])
            enrich_articles(db, c["name"], session_id, workers=args.workers)
            info = db.get_category_info(c["name"])
            if info:
                print(f"  -> {c['name']}: {info['full_text_count']}/{info['total_articles']} enriched")
        return 0

    if not args.category:
        parser.print_help()
        return 1

    # ── Full-text enrichment (articles already exist) ──
    if args.full_text and not args.dry_run and not args.max_pages:
        status = db.get_category_info(args.category)
        if status and status["total_articles"] > 0:
            session_id = db.create_session(args.category)
            enrich_articles(db, args.category, session_id, workers=args.workers)
            info = db.get_category_info(args.category)
            if info:
                print(f"\nDone. {info['full_text_count']}/{info['total_articles']} enriched in '{args.category}'.")
            return 0

    # ── Full scraping mode (Playwright) ──
    with InstructablesSession() as session:
        session_id = db.create_session(args.category)

        if args.dry_run:
            scrape_category_pages(db, args.category, session, session_id, dry_run=True)
            return 0

        count = scrape_category_pages(db, args.category, session, session_id, max_pages=args.max_pages)

        if args.full_text:
            enrich_articles(db, args.category, session_id, workers=args.workers)

        info = db.get_category_info(args.category)
        if info:
            print(f"\nDone. {info['total_articles']} articles in '{args.category}'.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
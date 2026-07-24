import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from html2text import HTML2Text

from database import Database
from fetcher import (
    AdaptiveRateLimiter, RequestConfig, fetch_all_parallel, fetch_html,
)
from parser import parse_article, parse_sitemap, SITEMAP_BASE, SITEMAP_PAGES
from xlsx_exporter import export_to_xlsx

logger = logging.getLogger(__name__)

SKILL_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SKILL_ROOT.parent.parent.parent


def _make_markdown_converter() -> HTML2Text:
    h = HTML2Text()
    h.body_width = 0
    h.ignore_links = False
    h.ignore_images = False
    h.ignore_emphasis = False
    h.protect_links = True
    h.unicode_snob = True
    h.skip_internal_links = True
    h.inline_links = True
    return h


def _clean_content(html: str) -> str:
    return _make_markdown_converter().handle(html).strip()


def _fetch_article(article_row: dict, cfg: RequestConfig, rl: AdaptiveRateLimiter) -> dict:
    url = article_row["url"]
    try:
        html = fetch_html(url, cfg, rl)
        parsed = parse_article(html)
        content_md = _clean_content(parsed["content_html"]) if parsed["content_html"] else None
        return {
            "id": article_row["id"],
            "url": url,
            "success": True,
            "content_md": content_md,
            **parsed,
        }
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return {"id": article_row["id"], "url": url, "success": False, "error": str(e)}


def scrape_sitemap(db: Database, cfg: RequestConfig) -> int:
    rate_limiter = AdaptiveRateLimiter(cfg)
    total = 0
    for page in range(1, SITEMAP_PAGES + 1):
        url = f"{SITEMAP_BASE}?page={page}"
        logger.info("Fetching sitemap page %d/%d...", page, SITEMAP_PAGES)
        xml = fetch_html(url, cfg, rate_limiter)
        entries = parse_sitemap(xml)
        for e in entries:
            db.upsert_article(
                url=e["url"],
                lastmod=e["lastmod"],
                image_url=e["image_url"],
                title=e["image_title"] or None,
            )
            total += 1
    logger.info("Sitemap done: %d articles", total)
    return total


def scrape_full_articles(db: Database, cfg: RequestConfig):
    articles = db.get_articles_by_status("metadata")
    if not articles:
        logger.info("No articles waiting for full text.")
        return

    logger.info("Fetching full text for %d articles...", len(articles))
    from tqdm import tqdm

    pbar = tqdm(total=len(articles), desc="Fetching articles", unit="article")

    def progress(n=1):
        pbar.update(n)

    results = fetch_all_parallel(
        articles,
        lambda row, c, rl: _fetch_article(row, c, rl),
        cfg,
        progress_callback=progress,
    )

    success = 0
    for item, result, error in results:
        if error is not None or not result.get("success"):
            continue
        db.update_full_text(result["id"], result.get("content_md") or "")
        db.update_article_details(
            result["id"],
            author=result.get("author"),
            date=result.get("published"),
            category=result.get("category"),
            tags=result.get("tags"),
            meta_description=result.get("meta_description"),
            meta_keywords=result.get("meta_keywords"),
            image_url=result.get("main_image"),
            circuit_diagram=result.get("circuit_diagram"),
        )
        success += 1

    pbar.close()
    logger.info("Full text: %d/%d successful", success, len(articles))


def format_article_text(a: dict) -> str:
    date = a.get("date", "")
    title = a.get("title", "")
    author = (a.get("author") or "").strip()
    tags = ", ".join(a.get("tags", [])) if a.get("tags") else ""
    content = (a.get("content_md") or "").strip()
    parts = f'[{date}] "{title}"'
    if author:
        parts += f" by {author}"
    if tags:
        parts += f" [{tags}]"
    if content:
        if len(content) > 200:
            content = content[:197] + "..."
        parts += f" — {content}"
    return parts


def _load_query_file(path: str) -> tuple[str, str]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Query file not found: {path}")
    query_text = p.read_text(encoding="utf-8").strip()
    if not query_text:
        raise ValueError(f"Query file is empty: {path}")
    return p.stem, query_text


def _compute_query_hash(query_text: str) -> str:
    import hashlib
    return hashlib.md5(query_text.encode("utf-8")).hexdigest()[:16]


def _handle_search_subcommand(args, db: Database) -> int:
    if not args.command:
        logger.error("search requires a command: init, get-batch, set-batch, status, report")
        return 1
    if not args.query_file:
        logger.error("--query-file is required")
        return 1

    try:
        query_name, query_text = _load_query_file(args.query_file)
    except (FileNotFoundError, ValueError) as e:
        logger.error("%s", e)
        return 1
    query_hash = _compute_query_hash(query_text)

    if args.command == "init":
        status = db.get_search_status(query_hash)
        total = status["total_articles"]
        scored = status["scored"]
        pending = status["pending"]
        batch_size = args.batch_size or 100
        batches = (pending + batch_size - 1) // batch_size if pending > 0 else 0

        if pending <= 0:
            print(f"\nAll {total} articles already scored for '{query_name}'.")
            return 0

        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        print(f"\n=== SEARCH SESSION: {session_id} ===")
        print(f"Query: {query_name}")
        print(f"Pending: {pending}, Total: {total}, Scored: {scored}")
        print(f"Batch size: {batch_size}, Batches: {batches}")
        return 0

    if args.command == "get-batch":
        batch_size = args.batch_size or 100
        all_candidates = db.get_search_candidates(query_hash)
        start = args.batch_index * batch_size
        batch = all_candidates[start:start + batch_size]
        if args.compact:
            for a in batch:
                print(json.dumps({"id": a["id"], "text": format_article_text(a)}, ensure_ascii=False))
        else:
            out = [{"id": a["id"], "text": format_article_text(a)} for a in batch]
            print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    if args.command == "set-batch":
        if args.batch_file:
            results = []
            for fp in args.batch_file:
                path = Path(fp)
                if not path.is_absolute():
                    path = (PROJECT_ROOT / fp).resolve()
                if not path.exists():
                    logger.error("Batch file not found: %s", fp)
                    return 1
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, list):
                    logger.error("Expected a JSON array in %s", fp)
                    return 1
                results.extend(data)
        else:
            raw = sys.stdin.read()
            if raw.startswith('\ufeff'):
                raw = raw[1:]
            results = json.loads(raw)

        saved = 0
        for item in results:
            article_id = item.get("id")
            if article_id is None:
                continue
            score = item.get("score") or item.get("relevance", 0)
            reason = item.get("reason", "")
            db.save_search_result(article_id, query_hash, query_name, score, reason)
            saved += 1
        logger.info("Saved %d results", saved)
        return 0

    if args.command == "status":
        status = db.get_search_status(query_hash)
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            print(f"\nSearch status for '{query_name}':")
            print(f"  Total articles:  {status['total_articles']}")
            print(f"  Scored:          {status['scored']}")
            print(f"  Pending:         {status['pending']}")
        return 0

    if args.command == "report":
        report = db.get_search_report(query_hash, min_total=args.min_score or 0, top=args.top)
        if not report:
            print(f"No scored results for '{query_name}'.")
            return 0
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        reports_dir = SKILL_ROOT.parent.parent.parent / "reports" / "circuitdigest-radar"
        reports_dir.mkdir(parents=True, exist_ok=True)
        xlsx_path = reports_dir / f"search_{query_name}_{today}.xlsx"
        rows = []
        for r in report:
            rows.append({
                "id": r["id"],
                "title": r["title"],
                "score": r["total"],
                "date": r["date"],
                "url": r["url"],
                "author": r["author"],
                "tags": r["tags"],
                "summary_ru": "",
            })
        export_to_xlsx(rows, str(xlsx_path))
        print(f"\nSearch report: {len(report)} article(s) scored")
        print(f"Saved: {xlsx_path}")
        print()
        display_n = min(args.top or 10, len(report))
        for r in report[:display_n]:
            reason = r["comment"][:80] if r["comment"] else ""
            sep = " — " if reason else ""
            print(f"[{r['id']:4d}] ({r['total']:>3d}pt) {r['title'][:60]}{sep}{reason}")
        return 0

    return 0


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape and analyze articles from circuitdigest.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db", default="data/circuitdigest.db", help="Database path")
    parser.add_argument("--workers", type=int, default=10, help="Parallel workers")
    parser.add_argument("--delay", type=float, nargs=2, metavar=("MIN", "MAX"),
                        default=[0.5, 1.0], help="Request delay range")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout")
    parser.add_argument("--top", type=int, metavar="N", help="Limit report to top N")
    parser.add_argument("--min-score", type=int, default=0, metavar="N",
                        help="Minimum score for report")
    parser.add_argument("--batch-size", type=int, default=100, metavar="N",
                        help="Batch size")

    # Actions
    parser.add_argument("--scrape", action="store_true", help="Scrape sitemap + articles")
    parser.add_argument("--status", action="store_true", help="Show database status")
    parser.add_argument("--db-schema", action="store_true", help="Show database schema")
    parser.add_argument("--search", type=str, metavar="TEXT", help="Ad-hoc search")
    parser.add_argument("--export-xlsx", action="store_true", help="Export all articles to XLSX")
    parser.add_argument("--since", type=str, metavar="YYYY-MM-DD",
                        help="Only process articles newer than this date (from sitemap lastmod)")

    # Search subcommand
    subparsers = parser.add_subparsers(dest="mode", help="Modes")
    search_parser = subparsers.add_parser("search", help="Semantic search via searcher subagents")
    search_sub = search_parser.add_subparsers(dest="command", help="Search command")

    search_common = argparse.ArgumentParser(add_help=False)
    search_common.add_argument("--query-file", type=str, metavar="PATH", required=True)

    search_init = search_sub.add_parser("init", parents=[search_common],
                                         help="Initialize search session")
    search_init.add_argument("--batch-size", type=int, default=100)

    search_get = search_sub.add_parser("get-batch", parents=[search_common],
                                       help="Get batch data")
    search_get.add_argument("batch_index", type=int, metavar="INDEX")
    search_get.add_argument("--batch-size", type=int, default=100)
    search_get.add_argument("--compact", action="store_true")

    search_set = search_sub.add_parser("set-batch", parents=[search_common],
                                       help="Save batch results")
    search_set.add_argument("--batch-file", type=str, metavar="PATH", action="append")

    search_status = search_sub.add_parser("status", parents=[search_common],
                                          help="Show search progress")
    search_status.add_argument("--json", action="store_true")

    search_report = search_sub.add_parser("report", parents=[search_common],
                                          help="Generate ranked report")
    search_report.add_argument("--top", type=int, metavar="N")
    search_report.add_argument("--min-score", type=int, default=50)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    log_level = "DEBUG" if args.verbose else "INFO"
    logging.basicConfig(
        level=log_level,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    db_path = args.db if Path(args.db).is_absolute() else str(SKILL_ROOT / args.db)
    db = Database(db_path)

    cfg = RequestConfig()
    cfg.parallel_workers = args.workers
    cfg.delay_min, cfg.delay_max = args.delay
    cfg.timeout = args.timeout

    if args.db_schema:
        schema = db.get_schema()
        if args.json:
            print(json.dumps(schema, indent=2, ensure_ascii=False))
            return 0
        for table in schema:
            print(f"\nTable: {table['table']}")
            print("-" * (len(table["table"]) + 8))
            for c in table["columns"]:
                pk = " PK" if c["pk"] else ""
                nn = " NOT NULL" if c["notnull"] else ""
                print(f"  {c['name']:20s} {c['type']}{pk}{nn}")
        return 0

    if args.status:
        total = db.get_total_count()
        full = db.get_full_text_count()
        earliest, latest = db.get_date_range()
        if args.json:
            print(json.dumps({
                "total_articles": total,
                "full_texts": full,
                "date_range": {"earliest": earliest, "latest": latest},
            }, indent=2))
        else:
            print(f"\nCircuitDigest Database Status")
            print(f"  Articles: {total}")
            print(f"  Full texts: {full}")
            print(f"  Date range: {earliest or '?'} — {latest or '?'}")
        return 0

    if args.mode == "search":
        return _handle_search_subcommand(args, db)

    if args.search:
        query_hash = _compute_query_hash(args.search)
        report = db.get_search_report(query_hash, min_total=0, top=args.top or 20)
        if report:
            print(f"\nResults for '{args.search}':\n")
            for r in report[:args.top or 20]:
                print(f"[{r['id']:4d}] ({r['total']:>3d}pt) {r['title']}")
                print(f"       {r['date']}  {r['url']}")
                if r["comment"]:
                    print(f"       {r['comment'][:200]}")
                print()
            return 0
        # Fallback: keyword search in DB
        results = db.search_articles(args.search)
        if not results:
            print(f"No articles found matching '{args.search}'.")
            return 0
        print(f"\nFound {len(results)} article(s) matching '{args.search}':\n")
        for r in results:
            tags = ", ".join(r["tags"]) if r["tags"] else "—"
            print(f"[{r['id']}] {r['title']} ({r['date']})")
            print(f"   Author: {r['author'] or '?'}  Tags: {tags}")
            print(f"   {r['url']}")
            print()
        return 0

    if args.export_xlsx:
        articles = db.export_articles()
        if not articles:
            print("No articles in database.")
            return 0
        today = datetime.now().strftime("%Y-%m-%d")
        reports_dir = SKILL_ROOT.parent.parent.parent / "reports" / "circuitdigest-radar"
        reports_dir.mkdir(parents=True, exist_ok=True)
        xlsx_path = reports_dir / f"circuitdigest_articles_{today}.xlsx"
        export_to_xlsx(articles, str(xlsx_path))
        print(f"Exported {len(articles)} articles to {xlsx_path}")
        return 0

    if args.scrape:
        logger.info("Phase 1: scraping sitemap...")
        count = scrape_sitemap(db, cfg)
        logger.info("Phase 1 done: %d articles from sitemap", count)

        if count > 0:
            logger.info("Phase 2: fetching full article texts...")
            scrape_full_articles(db, cfg)

        total = db.get_total_count()
        full = db.get_full_text_count()
        print(f"\nScrape complete: {total} articles, {full} with full text")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from html2text import HTML2Text

from database import Database
from fetcher import (
    AdaptiveRateLimiter, RequestConfig, fetch_all_parallel, fetch_html,
)
from parser import parse_sitemap, parse_article, CATEGORY_MAP
from config import SITEMAP_URLS
from xlsx_exporter import export_to_xlsx, import_from_xlsx

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


def _fetch_and_save(article_row: dict, cfg: RequestConfig, rl: AdaptiveRateLimiter, db: Database) -> dict:
    url = article_row["url"]
    try:
        html = fetch_html(url, cfg, rl)
        parsed = parse_article(html, article_url=url)
        content_md = _clean_content(parsed["content_html"]) if parsed["content_html"] else None

        db.update_full_text(article_row["id"], content_md or "")
        db.update_article_details(
            article_row["id"],
            title=parsed.get("title"),
            author=parsed.get("author"),
            date=parsed.get("published"),
            category=parsed.get("category"),
            tags=parsed.get("tags"),
            meta_description=parsed.get("meta_description"),
            meta_keywords=parsed.get("meta_keywords"),
            image_url=parsed.get("main_image"),
        )
        return {"id": article_row["id"], "url": url, "success": True}
    except Exception as e:
        logger.warning("Failed to fetch %s: %s", url, e)
        return {"id": article_row["id"], "url": url, "success": False, "error": str(e)}


def scrape_sitemap(db: Database, cfg: RequestConfig, since: str | None = None) -> int:
    rate_limiter = AdaptiveRateLimiter(cfg)
    total = 0
    for sitemap_url in SITEMAP_URLS:
        logger.info("Fetching sitemap: %s", sitemap_url)
        xml = fetch_html(sitemap_url, cfg, rate_limiter)
        entries = parse_sitemap(xml)
        for e in entries:
            if since and e["lastmod"] and e["lastmod"][:10] < since:
                continue
            db.upsert_article(
                url=e["url"],
                lastmod=e["lastmod"],
                image_url=e["image_url"],
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

    success = 0
    for item, result, error in fetch_all_parallel(
        articles,
        lambda row, c, rl: _fetch_and_save(row, c, rl, db),
        cfg,
        progress_callback=progress,
    ):
        if error is None and result.get("success"):
            success += 1

    pbar.close()
    logger.info("Full text: %d/%d successful", success, len(articles))


def _fetch_title_only(article_row: dict, cfg: RequestConfig, rl: AdaptiveRateLimiter, db: Database) -> bool:
    url = article_row["url"]
    try:
        headers = {"User-Agent": cfg.user_agent}
        rl.wait()
        resp = requests.get(url, headers=headers, timeout=cfg.timeout, stream=True)
        resp.raise_for_status()
        rl.report_success()
        chunk = ""
        for part in resp.iter_content(chunk_size=16384, decode_unicode=True):
            if part:
                chunk += part
            if len(chunk) > 50000:
                break
        resp.close()

        soup = BeautifulSoup(chunk, "html.parser")
        title_el = (
            soup.find("h1", class_="entry-title")
            or soup.find("h1", itemprop="headline")
            or soup.find("title")
        )
        if title_el:
            title = title_el.get_text(strip=True)
            if title:
                db._execute("UPDATE articles SET title = ? WHERE id = ?", (title, article_row["id"]))
                return True
    except Exception as e:
        logger.debug("Failed to fetch title for %s: %s", url, e)
    return False


def backfill_titles(db: Database, cfg: RequestConfig):
    articles = db.get_articles_by_null_title()
    if not articles:
        logger.info("All articles already have titles.")
        return
    logger.info("Backfilling titles for %d articles...", len(articles))
    from tqdm import tqdm

    pbar = tqdm(total=len(articles), desc="Backfill titles", unit="article")

    def progress(n=1):
        pbar.update(n)

    results = fetch_all_parallel(
        articles,
        lambda row, c, rl: _fetch_title_only(row, c, rl, db),
        cfg,
        progress_callback=progress,
    )
    pbar.close()
    success = sum(1 for item, result, error in results if result is True)
    logger.info("Titles backfilled: %d/%d", success, len(articles))


def _extract_slug(url: str) -> str:
    parts = [p for p in url.rstrip("/").split("/") if p]
    if not parts:
        return "?"
    cand = parts[-1]
    import re
    if re.match(r"^\d[\d\-]*\d$", cand) or re.match(r"^\d{4}-\d{2}-\d{2}-\d+$", cand):
        cand = parts[-2] if len(parts) >= 2 else cand
    return cand.replace("-", " ").replace("_", " ")[:60]


def format_article_text(a: dict) -> str:
    date = a.get("date", "")
    title = a.get("title", "")
    if not title:
        title = _extract_slug(a.get("url", ""))
    base = title[:60]
    author = (a.get("author") or "").strip()
    tags = ", ".join(a.get("tags", [])) if a.get("tags") else ""
    content = (a.get("content_md") or "").strip()
    parts = f'[{date}] "{base}"'
    if author:
        parts += f" by {author}"
    if tags:
        parts += f" [{tags}]"
    if content:
        parts += f" \u2014 {content}"
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
        report = db.get_search_report(query_hash, min_total=args.min_score or 50)
        if not report:
            print(f"No scored results for '{query_name}'.")
            return 0
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        reports_dir = PROJECT_ROOT / "reports" / "radioskot-radar"
        reports_dir.mkdir(parents=True, exist_ok=True)

        scores = [r["total"] for r in report]
        score_min, score_max = min(scores), max(scores)

        rows = []
        for r in report:
            rows.append({
                "id": r["id"],
                "score": r["total"],
                "is_interesting": r["is_interesting"],
                "is_read": r["is_read"],
                "date": r["date"],
                "url": r["url"],
                "summary_ru": r["summary_ru"],
                "author": r["author"],
                "title": r["title"],
                "category": r["category"],
                "tags": r["tags"],
                "comment": r["comment"],
            })
        xlsx_path = reports_dir / f"search_{query_name}_{today}.xlsx"
        export_to_xlsx(rows, str(xlsx_path))
        print(f"\nSearch report: {len(report)} article(s) scored >= {args.min_score or 50}")
        print(f"Score range: {score_min} \u2013 {score_max}")
        print(f"Saved: {xlsx_path}")
        print()

        for r in report[:5]:
            title = (r["title"] or _extract_slug(r["url"]))[:60]
            reason = r["comment"][:80] if r["comment"] else ""
            sep = " \u2014 " if reason else ""
            print(f"[{r['id']:4d}] ({r['total']:>3d}pt) {title}{sep}{reason}")
        if len(report) > 5:
            print(f"... and {len(report) - 5} more")
        return 0

    return 0


def _handle_summarize_subcommand(args, db: Database) -> int:
    if args.command == "status":
        status = db.get_summary_status()
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            print(f"\nSummarization status:")
            print(f"  Total articles:  {status['total']}")
            print(f"  Summarized:      {status['summarized']}")
            print(f"  Pending:         {status['pending']}")
        return 0

    if args.command == "candidates":
        batch = args.batch_index or 0
        candidates = db.get_candidates_for_summary(batch=batch, batch_size=args.batch_size or 100)
        if args.json:
            for c in candidates:
                print(json.dumps(c, ensure_ascii=False))
        else:
            print(json.dumps(candidates, indent=2, ensure_ascii=False))
        return 0

    if args.command == "save":
        if args.batch_file:
            results = []
            for fp in args.batch_file:
                path = Path(fp)
                if not path.is_absolute():
                    path = (Path.cwd() / fp).resolve()
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
            summary = item.get("summary_ru", "")
            db.save_summary(article_id, summary)
            saved += 1
        logger.info("Saved %d summaries", saved)
        return 0

    if args.command == "import":
        if not args.xlsx:
            logger.error("--xlsx is required for import")
            return 1
        path = Path(args.xlsx)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        updated = import_from_xlsx(db, str(path))
        print(f"Imported I/R flags: {updated} article(s) updated")
        return 0

    logger.error("summarize requires a command: status, candidates, save, import")
    return 1


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape and analyze articles from radioskot.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db", default="data/radioskot.db", help="Database path")
    parser.add_argument("--workers", type=int, default=20, help="Parallel workers")
    parser.add_argument("--delay", type=float, nargs=2, metavar=("MIN", "MAX"),
                        default=[0.0, 0.0], help="Request delay range")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout")
    parser.add_argument("--min-score", type=int, default=0, metavar="N",
                        help="Minimum score for report")
    parser.add_argument("--batch-size", type=int, default=100, metavar="N",
                        help="Batch size")

    parser.add_argument("--scrape", action="store_true", help="Scrape sitemap + articles")
    parser.add_argument("--backfill-titles", action="store_true", help="Fill missing titles from live pages")
    parser.add_argument("--status", action="store_true", help="Show database status")
    parser.add_argument("--db-schema", action="store_true", help="Show database schema")
    parser.add_argument("--search", type=str, metavar="TEXT", help="Ad-hoc search")
    parser.add_argument("--export-xlsx", action="store_true", help="Export all articles to XLSX")
    parser.add_argument("--since", type=str, metavar="YYYY-MM-DD",
                        help="Only process articles newer than this date (from sitemap lastmod)")

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
    search_report.add_argument("--min-score", type=int, default=50)

    summarize_parser = subparsers.add_parser("summarize", help="LLM summarization")
    summarize_sub = summarize_parser.add_subparsers(dest="command", help="Summarize command")

    summarize_common = argparse.ArgumentParser(add_help=False)
    summarize_status = summarize_sub.add_parser("status", help="Show summarization progress")
    summarize_status.add_argument("--json", action="store_true")

    summarize_candidates = summarize_sub.add_parser("candidates", help="Get candidates for summarization")
    summarize_candidates.add_argument("batch_index", type=int, metavar="BATCH", nargs="?",
                                      default=0)
    summarize_candidates.add_argument("--batch-size", type=int, default=100)
    summarize_candidates.add_argument("--json", action="store_true")

    summarize_save = summarize_sub.add_parser("save", help="Save summarization results")
    summarize_save.add_argument("--batch-file", type=str, metavar="PATH", action="append")

    summarize_import = summarize_sub.add_parser("import", help="Import I/R flags from XLSX")
    summarize_import.add_argument("--xlsx", type=str, metavar="PATH", required=True)

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
            print(f"\nRadioskot Database Status")
            print(f"  Articles: {total}")
            print(f"  Full texts: {full}")
            print(f"  Date range: {earliest or '?'} \u2014 {latest or '?'}")
        return 0

    if args.mode == "search":
        return _handle_search_subcommand(args, db)

    if args.mode == "summarize":
        return _handle_summarize_subcommand(args, db)

    if args.search:
        query_hash = _compute_query_hash(args.search)
        report = db.get_search_report(query_hash, min_total=0)
        if report:
            print(f"\nResults for '{args.search}':\n")
            for r in report[:20]:
                print(f"[{r['id']:4d}] ({r['total']:>3d}pt) {r['title']}")
                print(f"       {r['date']}  {r['url']}")
                if r["comment"]:
                    print(f"       {r['comment'][:200]}")
                print()
            if len(report) > 20:
                print(f"... and {len(report) - 20} more")
            return 0
        results = db.search_articles(args.search)
        if not results:
            print(f"No articles found matching '{args.search}'.")
            return 0
        print(f"\nFound {len(results)} article(s) matching '{args.search}':\n")
        for r in results:
            tags = ", ".join(r["tags"]) if r["tags"] else "\u2014"
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
        reports_dir = PROJECT_ROOT / "reports" / "radioskot-radar"
        reports_dir.mkdir(parents=True, exist_ok=True)
        xlsx_path = reports_dir / f"radioskot_articles_{today}.xlsx"
        export_to_xlsx(articles, str(xlsx_path))
        print(f"Exported {len(articles)} articles to {xlsx_path}")
        return 0

    if args.backfill_titles:
        backfill_titles(db, cfg)
        return 0

    if args.scrape:
        if db.get_total_count() == 0:
            logger.info("Phase 1: scraping sitemap...")
            count = scrape_sitemap(db, cfg, since=args.since)
            logger.info("Phase 1 done: %d articles from sitemap", count)
        else:
            logger.info("Sitemap already loaded (%d articles). Skipping.", db.get_total_count())

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
import argparse
import collections
import io
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from database import Database
from scraper.api_client import ApiClient, DEFAULT_LIMIT
from scraper.logging import setup_logging
from analyzer.hashes import compute_query_hash, compute_content_hash
from analyzer.config import DEFAULT_ANALYZE_CONFIG
from analyzer.report import generate_report, generate_report_text
from analyzer.normalizer import normalize_scores
from analyzer.prompts import format_project, build_prompt
from xlsx_exporter import export_to_xlsx, import_from_xlsx, BASE_COLUMNS, BASE_HEADER_NAMES, BASE_EDITABLE
from tqdm import tqdm

logger = logging.getLogger("hackaday-io-radar")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "hackaday-io.db")


def _api_to_project(api: dict) -> dict:
    return {
        "id": api.get("id"),
        "name": api.get("name"),
        "slug": api.get("slug", ""),
        "summary": api.get("summary"),
        "description": api.get("description"),
        "owner_id": api.get("ownerId") or api.get("owner_id"),
        "owner_name": api.get("ownerName") or api.get("owner_name"),
        "created_at": str(api.get("created")) if api.get("created") else None,
        "updated_at": str(api.get("updated")) if api.get("updated") else None,
        "view_count": api.get("viewCount", 0),
        "followers_count": api.get("followerCount", 0),
        "tags": api.get("tags", []),
        "content_hash": compute_content_hash(
            api.get("name", ""),
            api.get("summary", "")
        ),
    }


def create_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hackaday-io-radar",
        description="CLI for hackaday.io project radar"
    )

    g_info = p.add_argument_group("Information options")
    g_info.add_argument("--db-summary", action="store_true", help="Show database summary")
    g_info.add_argument("--db-schema", action="store_true", help="Show database schema")
    g_info.add_argument("--latest", type=int, metavar="N", nargs="?", const=20,
                        help="Show N latest projects")

    g_fetch = p.add_argument_group("Fetch options (API)")
    g_fetch.add_argument("--fetch", action="store_true",
                         help="Incremental fetch: new projects only")
    g_fetch.add_argument("--fetch-full", action="store_true",
                         help="Full fetch: all projects from offset 0 to end")
    g_fetch.add_argument("--fetch-since", type=int, metavar="OFFSET",
                         help="Resume fetch from offset N")
    g_fetch.add_argument("--fetch-page", type=int, metavar="PAGE",
                         help="Fetch a single page (debug)")
    g_fetch.add_argument("--fetch-project", type=int, metavar="ID",
                         help="Fetch a single project by ID")

    g_flags = p.add_argument_group("Flags (I/R)")
    g_flags.add_argument("--mark-interesting", type=int, nargs="+", metavar="ID",
                         help="Mark projects as interesting")
    g_flags.add_argument("--unmark-interesting", type=int, nargs="+", metavar="ID",
                         help="Unmark interesting")
    g_flags.add_argument("--mark-read", type=int, nargs="+", metavar="ID",
                         help="Mark projects as read")
    g_flags.add_argument("--unmark-read", type=int, nargs="+", metavar="ID",
                         help="Unmark read")
    g_flags.add_argument("--list-interesting", action="store_true",
                         help="List interesting projects")
    g_flags.add_argument("--list-unread", action="store_true",
                         help="List unread projects")

    g_xlsx = p.add_argument_group("XLSX Export / Import")
    g_xlsx.add_argument("--export-xlsx", metavar="PATH", nargs="?", const="auto",
                        help="Export all projects to XLSX")
    g_xlsx.add_argument("--import-xlsx", metavar="PATH",
                        help="Import I/R flags from XLSX")

    g_other = p.add_argument_group("Other options")
    g_other.add_argument("--json", action="store_true", help="Output as JSON")
    g_other.add_argument("--log-level", default="INFO",
                         choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    # Subcommands
    sub = p.add_subparsers(dest="subcommand")

    sp_search = sub.add_parser("search", help="Search pipeline commands")
    sp_search.add_argument("action", choices=["init", "get-batch", "set-batch", "status", "report"])
    sp_search.add_argument("index", type=int, nargs="?", default=None,
                           help="Batch index (for get-batch)")
    sp_search.add_argument("--query-file", type=str, help="Path to query file")
    sp_search.add_argument("--batch-size", type=int, default=2000, help="Batch size")
    sp_search.add_argument("--batch-file", type=str, action="append", help="Batch file(s)")
    sp_search.add_argument("--top", type=int, default=None, help="Top N results (default: all)")
    sp_search.add_argument("--min-score", type=int, default=0, help="Minimum score")
    sp_search.add_argument("--compact", action="store_true", help="Compact JSON output")
    sp_search.add_argument("--raw", action="store_true", help="Show raw scores (skip normalization)")

    sp_summarize = sub.add_parser("summarize", help="Summarization commands")
    sp_summarize.add_argument("action", choices=["status", "candidates", "save"])
    sp_summarize.add_argument("--batch", type=int, default=0, help="Batch index")
    sp_summarize.add_argument("--json", action="store_true", help="JSON output")
    sp_summarize.add_argument("--file", type=str, help="File with summaries")

    return p


def _read_query(path: str) -> tuple:
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read().strip()
    name = os.path.splitext(os.path.basename(path))[0]
    qhash = compute_query_hash(text)
    return text, name, qhash


def handle_info(args, db: Database):
    if args.db_summary:
        summary = db.get_db_summary()
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            print(f"Total projects: {summary['total_projects']}")
            if summary["last_session"]:
                ls = summary["last_session"]
                print(f"Last session:   {ls.get('status', '?')} "
                      f"({ls.get('total_fetched', 0)} projects, "
                      f"offset {ls.get('last_offset', 0)})")
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
        projects = db.get_latest_projects(args.latest)
        if args.json:
            print(json.dumps(projects, ensure_ascii=False, indent=2, default=str))
        else:
            if not projects:
                print("No projects in database.")
            else:
                print(f"{'ID':>8s} {'Name':50s} {'Owner':20s}")
                print("-" * 80)
                for p in projects:
                    name = (p.get("name") or "")[:48]
                    owner = (p.get("owner_name") or str(p.get("owner_id") or "?"))[:18]
                    print(f"{p['id']:>8d} {name:50s} {owner:20s}")
        return True

    return False


def handle_fetch(args, db: Database):
    if args.fetch_page is not None:
        client = ApiClient()
        offset = args.fetch_page * DEFAULT_LIMIT
        projects = client.fetch_projects(offset=offset)
        if args.json:
            print(json.dumps(projects, ensure_ascii=False, indent=2))
        else:
            print(f"Page {args.fetch_page} (offset={offset}, limit={DEFAULT_LIMIT}): "
                  f"{len(projects)} projects")
            for p in projects:
                print(f"  [{p.get('id')}] {p.get('name', '?')}")
        return True

    if args.fetch_project is not None:
        client = ApiClient()
        project = client.fetch_project(args.fetch_project)
        if project is None:
            print(f"Project {args.fetch_project} not found.")
            return True
        db.insert_project(_api_to_project(project))
        if args.json:
            print(json.dumps(project, ensure_ascii=False, indent=2))
        else:
            p = _api_to_project(project)
            print(f"Project {p['id']}: {p['name']} (by {p['owner_name'] or '?'})")
        return True

    if args.fetch_since is not None:
        client = ApiClient()
        session_id = db.create_session()
        _fetch_loop(client, db, args.fetch_since, session_id, incremental=False, total_estimate=44214)
        return True

    if args.fetch_full:
        client = ApiClient()
        session_id = db.create_session()
        _fetch_loop(client, db, 0, session_id, incremental=False, total_estimate=44214)
        return True

    if args.fetch:
        client = ApiClient()
        session_id = db.create_session()
        _fetch_loop(client, db, 0, session_id, incremental=True, total_estimate=44214)
        return True

    return False


def _fetch_loop(client: ApiClient, db: Database, start_offset: int, session_id: int,
                incremental: bool, total_estimate: int = 44214) -> int:
    offset = start_offset
    total = 0
    empty_pages = 0
    start_time = time.time()

    logger.info("Connecting to %s/projects (offset %d)...", client.BASE_URL, offset)
    if incremental:
        logger.info("Incremental mode: will stop at first known project")

    pbar = tqdm(total=total_estimate, unit="proj", desc="Fetching",
                bar_format="{desc}: {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]")

    pool = ThreadPoolExecutor(max_workers=3)
    futures = collections.deque()

    # Seed the first 3 requests
    for _ in range(3):
        o = offset
        futures.append(pool.submit(lambda oo=o: (oo, client.fetch_projects(oo))))
        offset += DEFAULT_LIMIT

    try:
        while futures:
            done_offset, projects = futures.popleft().result()

            if not projects:
                empty_pages += 1
                if empty_pages >= 3:
                    logger.info("3 consecutive empty pages — done.")
                    break
            else:
                empty_pages = 0

                for p in projects:
                    pid = p.get("id")
                    if incremental and db.project_exists(pid):
                        logger.info("Hit known project %d at offset %d — stopping.", pid, done_offset)
                        db.finish_session(session_id, "completed", total, done_offset,
                                          f"incremental: stop at known project {pid}")
                        pool.shutdown(wait=False)
                        pbar.close()
                        elapsed = time.time() - start_time
                        print(f"\nDone. {total} new projects added in {elapsed:.0f}s. "
                              f"Cache up to date.")
                        return total
                    db.insert_project(_api_to_project(p))
                    total += 1

                pbar.update(len(projects))

            # Add next page to the queue
            futures.append(pool.submit(lambda o=offset: (o, client.fetch_projects(o))))
            offset += DEFAULT_LIMIT

    except KeyboardInterrupt:
        logger.warning("Interrupted at offset %d, fetched %d projects", offset, total)
        db.finish_session(session_id, "interrupted", total, offset)
        pool.shutdown(wait=False)
        pbar.close()
        elapsed = time.time() - start_time
        print(f"\nInterrupted after {elapsed:.0f}s. {total} projects saved. "
              f"Resume with --fetch-since {offset}")
        return total
    except Exception as e:
        logger.error("Fetch failed at offset %d: %s", offset, e)
        db.finish_session(session_id, "error", total, offset, str(e))
        pool.shutdown(wait=False)
        pbar.close()
        print(f"\nError at offset {offset}: {e}")
        return total

    db.finish_session(session_id, "completed", total, offset)
    pool.shutdown(wait=False)
    pbar.close()
    elapsed = time.time() - start_time
    print(f"\nDone. Fetched {total} projects in {elapsed:.0f}s.")
    return total


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
        projects = db.get_interesting()
        if args.json:
            print(json.dumps(projects, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"Interesting projects ({len(projects)}):")
            for p in projects:
                print(f"  [{p['id']}] {p.get('name', '?')}")
        return True
    if args.list_unread:
        projects = db.get_unread()
        if args.json:
            print(json.dumps(projects, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"Unread projects ({len(projects)}):")
            for p in projects:
                print(f"  [{p['id']}] {p.get('name', '?')}")
        return True
    return False


def handle_xlsx(args, db: Database):
    if args.export_xlsx:
        projects = db.get_all_projects()
        if args.export_xlsx == "auto":
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = f"../../../reports/hackaday-io-radar/projects_{today}.xlsx"
        else:
            path = args.export_xlsx
        result = export_to_xlsx(projects, path)
        print(f"Exported {len(projects)} projects to {result}")
        return True

    if args.import_xlsx:
        result = import_from_xlsx(args.import_xlsx, db)
        print(f"Imported: {result['updated_interesting']} interesting, "
              f"{result['updated_read']} read ({result['total_rows']} rows)")
        return True
    return False


def handle_search(args, db: Database):
    if not args.query_file:
        print("Error: --query-file is required for search subcommand")
        return True

    query_text, query_name, query_hash = _read_query(args.query_file)

    if args.action == "init":
        status = db.get_search_status(query_hash)
        print(f"Query: {query_name}")
        print(f"Total projects: {status['total']}")
        print(f"Already scored: {status['scored']}")
        print(f"Pending: {status['pending']}")
        if status["pending"] > 0:
            batches = (status["pending"] + args.batch_size - 1) // args.batch_size
            print(f"Batches of {args.batch_size}: {batches}")
        return True

    if args.action == "get-batch":
        batch_index = args.index if args.index is not None else 0
        candidates = db.get_search_candidates_batch(
            query_hash, batch_index, args.batch_size
        )
        if args.compact:
            for c in candidates:
                text = format_project(1, c)
                out = {"id": c["id"], "text": text}
                print(json.dumps(out, ensure_ascii=False))
        else:
            print(json.dumps(candidates, ensure_ascii=False, indent=2))
        return True

    if args.action == "set-batch":
        if not args.batch_file:
            print("Error: --batch-file required for set-batch")
            return True
        for bf in args.batch_file:
            with open(bf, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                pid = item.get("id")
                project = db.get_project(pid)
                if not project:
                    logger.warning("Project %s not found, skipping", pid)
                    continue
                ch = compute_content_hash(
                    project.get("name", ""), project.get("summary", "")
                )
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
        generate_report_text(db, query_hash, query_name,
                             min_score=args.min_score, top=args.top,
                             raw=args.raw)
        return True

    return False


def handle_summarize(args, db: Database):
    if args.action == "status":
        status = db.get_summary_status()
        print(f"Total projects: {status['total']}")
        print(f"Summarized:     {status['summarized']}")
        print(f"Pending:        {status['pending']}")
        return True

    if args.action == "candidates":
        candidates = db.get_candidates_for_summary(limit=100)
        batch_size = 100
        offset = args.batch * batch_size
        batch = candidates[offset:offset + batch_size]
        if args.json:
            print(json.dumps(batch, ensure_ascii=False, indent=2))
        else:
            for c in batch:
                print(f"[{c['id']}] {c['name']}")
        return True

    if args.action == "save":
        if not args.file:
            print("Error: --file required for summarize save")
            return True
        with open(args.file, "r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            db.save_summary(item["id"], item.get("summary_ru", ""))
        print(f"Saved {len(data)} summaries")
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
    if args.subcommand == "summarize":
        handle_summarize(args, db)
        return

    action_handled = (
        handle_info(args, db)
        or handle_fetch(args, db)
        or handle_flags(args, db)
        or handle_xlsx(args, db)
    )

    if not action_handled:
        parser.print_help()


if __name__ == "__main__":
    main()
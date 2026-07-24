import json
from datetime import datetime, timezone
from pathlib import Path

from analyzer.config import DEFAULT_ANALYZE_CONFIG
from analyzer.normalizer import normalize_scores
from database import Database
from xlsx_exporter import export_to_xlsx, SEARCH_COLUMNS, SEARCH_HEADER_NAMES, SEARCH_EDITABLE


def generate_report(
    db: Database,
    category: str,
    query_name: str,
    query_hash: str,
    min_total: int = 0,
    top: int | None = None,
) -> str | None:
    """Generate an XLSX search report. Returns the path to the file, or None if no data."""
    report = db.get_search_report(
        category, query_hash,
        min_total=min_total, top=top,
    )
    if not report:
        return None

    normalize_scores(report, key="total")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reports_dir = Path("../../../reports/hackaday-blog-radar")
    reports_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = reports_dir / f"{category}_search_{query_name}_{today}.xlsx"

    rows = []
    for r in report:
        row = {
            "id": r["id"],
            "title": r["title"],
            "score": r["total"],
            "is_interesting": r["is_interesting"],
            "is_read": r["is_read"],
            "author": r["author"] or "",
            "date": r["date"],
            "url": r["url"],
            "tags": r["tags"],
            "summary_ru": r.get("summary_ru", ""),
            "comments": r["comments"],
        }
        rows.append(row)

    export_to_xlsx(
        rows, str(xlsx_path),
        columns=SEARCH_COLUMNS,
        header_names=SEARCH_HEADER_NAMES,
        editable=SEARCH_EDITABLE,
    )

    print(f"\nAnalysis report: {len(report)} article(s) scored")
    print(f"Query: {query_name}")
    print(f"Saved: {xlsx_path}")
    print()

    top_n = min(10, len(report))
    print(f"Top {top_n} (raw score):")
    print("-" * 70)
    for r in report[:top_n]:
        print(f"[{r['id']:4d}] {r['total']:>3d}pt  {r['title'][:55]}")
        print(f"       {r['date']}  {r['url']}")
        if r["comment"]:
            print(f"       {r['comment'][:100]}")
        print()

    return str(xlsx_path)


def generate_report_text(
    db: Database,
    category: str,
    query_name: str,
    query_hash: str,
    min_total: int = 0,
    top: int | None = None,
) -> str | None:
    report = db.get_search_report(
        category, query_hash,
        min_total=min_total, top=top,
    )
    if not report:
        return None

    normalize_scores(report, key="total")

    lines = []
    lines.append(f"Results for '{query_name}' ({category}):\n")
    for r in report[:top or 20]:
        lines.append(f"[{r['id']:4d}] ({r['total']:>3d}pt) {r['title']}")
        lines.append(f"       {r['date']}  {r['url']}")
        if r["comment"]:
            lines.append(f"       {r['comment'][:200]}")
        lines.append("")
    return "\n".join(lines)


def generate_digest_report(
    db: Database,
    category: str,
    query_name: str,
    query_text: str,
    query_hash: str,
    period_start: str,
    period_end: str,
    top: int = 5,
) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reports_dir = Path("../../../reports/hackaday-blog-radar")
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"digest_{category}_{query_name}_{today}.md"

    report = db.get_search_report(
        category, query_hash,
        min_total=0, top=top,
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Digest: {query_name}\n")
        f.write(f"Period: {period_start} — {period_end}\n")
        f.write(f"Category: {category}\n\n")
        if report:
            f.write(f"Top {min(top, len(report))} relevant articles:\n\n")
            for r in report:
                tags = ", ".join(r["tags"]) if r["tags"] else "-"
                f.write(f"## [{r['id']}] {r['title']}\n")
                f.write(f"- **Date:** {r['date']}\n")
                f.write(f"- **Score:** {r['total']}/100\n")
                f.write(f"- **URL:** {r['url']}\n")
                f.write(f"- **Tags:** {tags}\n")
                if r["comment"]:
                    f.write(f"- **Why:** {r['comment']}\n")
                f.write("\n")
        else:
            f.write("No relevant articles found in this period.\n")

    return str(path)
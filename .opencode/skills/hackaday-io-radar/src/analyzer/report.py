import json
from datetime import datetime
from pathlib import Path

from analyzer.config import DEFAULT_ANALYZE_CONFIG
from analyzer.normalizer import normalize_scores
from xlsx_exporter import export_to_xlsx, SEARCH_COLUMNS, SEARCH_HEADER_NAMES, SEARCH_EDITABLE

REPORTS_DIR = Path("../../../reports/hackaday-io-radar")


def _fmt_date(ts: str) -> str:
    if ts and ts.isdigit():
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    return ts or ""


def generate_report(db, query_hash: str, query_name: str,
                    min_score: int = 0, top: int = None, raw: bool = False) -> str | None:
    report = db.get_search_report(query_hash, min_score=min_score, top=top)
    if not report:
        return None
    if not raw:
        normalize_scores(report, key="score")
    today = datetime.now().strftime("%Y-%m-%d")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    xlsx_path = REPORTS_DIR / f"search_{query_name}_{today}.xlsx"

    for r in report:
        r["url"] = r.get("id", "")
        r["created_at"] = _fmt_date(r.get("created_at"))
    export_to_xlsx(report, str(xlsx_path),
                   columns=SEARCH_COLUMNS,
                   header_names=SEARCH_HEADER_NAMES,
                   editable=SEARCH_EDITABLE)
    print(f"Search report: {len(report)} project(s) scored")
    display_count = top if top is not None else len(report)
    for r in report[:display_count]:
        display_score = r.get("score_normalized", r.get("score", 0))
        if isinstance(display_score, float):
            display_score = round(display_score)
        print(f"  [{r['id']:6d}] ({display_score:>3d}pt) {r.get('name', '?')}")
    return str(xlsx_path)


def generate_report_text(db, query_hash: str, query_name: str,
                         min_score: int = 0, top: int = None, raw: bool = False) -> str | None:
    report = db.get_search_report(query_hash, min_score=min_score, top=top)
    if not report:
        return None
    if not raw:
        normalize_scores(report, key="score")
    lines = [f"Results for '{query_name}':\n"]
    display_count = top if top is not None else len(report)
    for r in report[:display_count]:
        display_score = r.get("score_normalized", r.get("score", 0))
        if isinstance(display_score, float):
            display_score = round(display_score)
        lines.append(
            f"[{r['id']:6d}] ({display_score:>3d}pt) {r.get('name', '?')} "
            f"— {r.get('comment', '')}"
        )
    return "\n".join(lines)
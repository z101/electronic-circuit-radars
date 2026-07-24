from datetime import datetime
from pathlib import Path

from analyzer.normalizer import normalize_scores
from xlsx_exporter import export_to_xlsx, SEARCH_COLUMNS, SEARCH_HEADER_NAMES, SEARCH_EDITABLE

REPORTS_DIR = Path("../../../reports/rlocman-radar")


def generate_report(db, query_hash: str, query_name: str,
                    min_score: int = 0, top: int = None, raw: bool = False) -> str | None:
    report = db.get_search_report(query_hash, min_score=min_score, top=top)
    if not report:
        return None
    if not raw:
        all_range = db.get_search_score_range(query_hash)
        mn, mx = all_range["min"], all_range["max"]
        if mx != mn:
            for r in report:
                r["score"] = int(round((r["score"] - mn) / (mx - mn) * 100))
        else:
            for r in report:
                r["score"] = 100

    today = datetime.now().strftime("%Y-%m-%d")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    xlsx_path = REPORTS_DIR / f"search_{query_name}_{today}.xlsx"

    export_to_xlsx(report, str(xlsx_path),
                   columns=SEARCH_COLUMNS,
                   header_names=SEARCH_HEADER_NAMES,
                   editable=SEARCH_EDITABLE)

    print(f"Search report: {len(report)} article(s) scored")
    display_count = top if top is not None else len(report)
    for r in report[:display_count]:
        display_score = r.get("score", 0)
        print(f"  [{r['di']:7d}] ({display_score:>3d}pt) {r.get('title_ru', '?')[:48]}")
    return str(xlsx_path)


def generate_report_text(db, query_hash: str, query_name: str,
                         min_score: int = 0, top: int = None, raw: bool = False) -> str | None:
    report = db.get_search_report(query_hash, min_score=min_score, top=top)
    if not report:
        return None
    if not raw:
        all_range = db.get_search_score_range(query_hash)
        mn, mx = all_range["min"], all_range["max"]
        if mx != mn:
            for r in report:
                r["score"] = int(round((r["score"] - mn) / (mx - mn) * 100))
        else:
            for r in report:
                r["score"] = 100

    lines = [f"Results for '{query_name}':\n"]
    display_count = top if top is not None else len(report)
    for r in report[:display_count]:
        display_score = r.get("score", 0)
        lines.append(
            f"[{r['di']:7d}] ({display_score:>3d}pt) {r.get('title_ru', '?')} "
            f"— {r.get('comment', '')}"
        )
    return "\n".join(lines)
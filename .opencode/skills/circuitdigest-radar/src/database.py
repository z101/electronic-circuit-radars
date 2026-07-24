import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT UNIQUE NOT NULL,
    title           TEXT,
    author          TEXT,
    date            TEXT,
    category        TEXT,
    tags            TEXT,
    content_md      TEXT,
    meta_description TEXT,
    meta_keywords   TEXT,
    image_url       TEXT,
    circuit_diagram TEXT,
    status          TEXT DEFAULT 'metadata',
    summary_ru      TEXT,
    loaded_at       TEXT NOT NULL,
    lastmod         TEXT
);

CREATE TABLE IF NOT EXISTS search_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      INTEGER NOT NULL REFERENCES articles(id),
    query_hash      TEXT NOT NULL,
    query_name      TEXT,
    content_hash    TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',
    total           INTEGER,
    comment         TEXT,
    attempts        INTEGER DEFAULT 0,
    last_error      TEXT,
    scored_at       TEXT,
    UNIQUE(article_id, query_hash, content_hash)
);
"""

INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url)",
    "CREATE INDEX IF NOT EXISTS idx_articles_date ON articles(date)",
    "CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)",
    "CREATE INDEX IF NOT EXISTS idx_search_query_total ON search_scores(query_hash, total)",
    "CREATE INDEX IF NOT EXISTS idx_search_query_status ON search_scores(query_hash, status)",
    "CREATE INDEX IF NOT EXISTS idx_search_article ON search_scores(article_id)",
]


class Database:
    def __init__(self, db_path: str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _execute(self, sql: str, params=()):
        conn = self._get_conn()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur
        finally:
            conn.close()

    def _fetchone(self, sql: str, params=()):
        conn = self._get_conn()
        try:
            return conn.execute(sql, params).fetchone()
        finally:
            conn.close()

    def _fetchall(self, sql: str, params=()):
        conn = self._get_conn()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def _init_schema(self):
        for stmt in SCHEMA_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._execute(stmt)
        for idx in INDEXES_SQL:
            self._execute(idx)

    # Articles
    def upsert_article(self, url: str, **fields):
        now = datetime.now(timezone.utc).isoformat()
        tags_json = json.dumps(fields.get("tags", []), ensure_ascii=False)
        self._execute(
            """INSERT OR IGNORE INTO articles
               (url, title, author, date, category, tags, meta_description, meta_keywords,
                image_url, circuit_diagram, loaded_at, lastmod, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                url,
                fields.get("title"),
                fields.get("author"),
                fields.get("date"),
                fields.get("category"),
                tags_json,
                fields.get("meta_description"),
                fields.get("meta_keywords"),
                fields.get("image_url"),
                fields.get("circuit_diagram"),
                now,
                fields.get("lastmod"),
                "metadata",
            ),
        )

    def update_full_text(self, article_id: int, content_md: str):
        self._execute(
            "UPDATE articles SET content_md = ?, status = 'full' WHERE id = ?",
            (content_md, article_id),
        )

    def update_article_details(self, article_id: int, **fields):
        sets = []
        params = []
        for key in ("author", "date", "category", "tags", "meta_description",
                     "meta_keywords", "image_url", "circuit_diagram"):
            if key in fields and fields[key] is not None:
                if key == "tags":
                    val = json.dumps(fields[key], ensure_ascii=False)
                else:
                    val = fields[key]
                sets.append(f"{key} = ?")
                params.append(val)
        if not sets:
            return
        params.append(article_id)
        self._execute(
            f"UPDATE articles SET {', '.join(sets)} WHERE id = ?",
            params,
        )

    def get_articles_by_status(self, status: str = "metadata"):
        rows = self._fetchall(
            "SELECT id, url FROM articles WHERE (status = ? OR content_md IS NULL OR content_md = '')",
            (status,),
        )
        return [dict(r) for r in rows]

    def get_total_count(self) -> int:
        row = self._fetchone("SELECT COUNT(*) as cnt FROM articles")
        return row["cnt"] if row else 0

    def get_full_text_count(self) -> int:
        row = self._fetchone(
            "SELECT COUNT(*) as cnt FROM articles WHERE status = 'full'"
        )
        return row["cnt"] if row else 0

    def get_date_range(self):
        row = self._fetchone(
            "SELECT MIN(date) as earliest, MAX(date) as latest FROM articles WHERE date != ''"
        )
        return (row["earliest"], row["latest"]) if row else (None, None)

    def get_schema(self) -> list[dict]:
        tables = self._fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        schema = []
        for t in tables:
            cols = self._fetchall(f"PRAGMA table_info('{t['name']}')")
            schema.append({
                "table": t["name"],
                "columns": [
                    {"name": c["name"], "type": c["type"],
                     "notnull": bool(c["notnull"]), "pk": bool(c["pk"])}
                    for c in cols
                ],
            })
        return schema

    def search_articles(self, keyword: str, limit: int = 20) -> list[dict]:
        like = f"%{keyword}%"
        rows = self._fetchall(
            """SELECT id, title, url, author, date, tags,
                      SUBSTR(content_md, 1, 500) as content_preview
               FROM articles
               WHERE title LIKE ? OR content_md LIKE ? OR tags LIKE ?
               ORDER BY date DESC LIMIT ?""",
            (like, like, like, limit),
        )
        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "title": r["title"],
                "url": r["url"],
                "author": r["author"],
                "date": r["date"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "content_preview": r["content_preview"],
            })
        return results

    # Search scores
    def get_search_candidates(self, query_hash: str, max_retries: int = 3) -> list[dict]:
        rows = self._fetchall(
            """SELECT a.id, a.title, a.content_md, a.date, a.url, a.tags, a.author, a.content_md as content_hash
               FROM articles a
               WHERE a.status = 'full'
               AND (
                 NOT EXISTS (
                   SELECT 1 FROM search_scores s
                   WHERE s.article_id = a.id AND s.query_hash = ?
                 )
                 OR EXISTS (
                   SELECT 1 FROM search_scores s
                   WHERE s.article_id = a.id AND s.query_hash = ?
                     AND s.status = 'error' AND s.attempts < ?
                 )
               )
               ORDER BY a.date DESC""",
            (query_hash, query_hash, max_retries),
        )
        results = []
        for r in rows:
            tags = json.loads(r["tags"]) if r["tags"] else []
            results.append({
                "id": r["id"],
                "title": r["title"],
                "content_md": r["content_md"] or "",
                "date": r["date"],
                "url": r["url"],
                "tags": tags,
                "author": r["author"] or "",
            })
        return results

    def save_search_result(self, article_id: int, query_hash: str, query_name: str,
                           score: int, comment: str = ""):
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            """INSERT INTO search_scores
               (article_id, query_hash, query_name, content_hash, status, total, comment, scored_at)
               VALUES (?, ?, ?, 'na', 'scored', ?, ?, ?)
               ON CONFLICT(article_id, query_hash, content_hash) DO UPDATE SET
                 status='scored', total=excluded.total, comment=excluded.comment, scored_at=excluded.scored_at""",
            (article_id, query_hash, query_name, score, comment, now),
        )

    def get_search_report(self, query_hash: str, min_total: int = 0, top: int | None = None) -> list[dict]:
        query = """SELECT a.id, a.title, a.date, a.url, a.author, a.tags,
                          s.total, s.comment, a.summary_ru
                   FROM search_scores s
                   JOIN articles a ON a.id = s.article_id
                   WHERE s.query_hash = ? AND s.status = 'scored' AND s.total >= ?
                   ORDER BY s.total DESC, a.date DESC"""
        params = [query_hash, min_total]
        if top:
            query += " LIMIT ?"
            params.append(top)
        rows = self._fetchall(query, params)
        results = []
        for r in rows:
            tags = json.loads(r["tags"]) if r["tags"] else []
            results.append({
                "id": r["id"],
                "title": r["title"],
                "date": r["date"],
                "url": r["url"],
                "author": r["author"] or "",
                "tags": tags,
                "total": r["total"],
                "comment": r["comment"] or "",
                "summary_ru": r["summary_ru"] or "",
            })
        return results

    def get_search_status(self, query_hash: str) -> dict:
        total = self._fetchone(
            "SELECT COUNT(*) as cnt FROM articles WHERE status = 'full'"
        )["cnt"]
        scored = self._fetchone(
            "SELECT COUNT(*) as cnt FROM search_scores WHERE query_hash = ? AND status = 'scored'",
            (query_hash,),
        )["cnt"]
        return {"total_articles": total, "scored": scored, "pending": total - scored}

    def export_articles(self) -> list[dict]:
        rows = self._fetchall(
            """SELECT id, title, url, author, date, category, tags,
                      meta_description, meta_keywords, image_url, circuit_diagram
               FROM articles ORDER BY date DESC"""
        )
        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "title": r["title"],
                "url": r["url"],
                "author": r["author"],
                "date": r["date"],
                "category": r["category"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "meta_description": r["meta_description"],
                "meta_keywords": r["meta_keywords"],
                "image_url": r["image_url"],
                "circuit_diagram": r["circuit_diagram"],
            })
        return results

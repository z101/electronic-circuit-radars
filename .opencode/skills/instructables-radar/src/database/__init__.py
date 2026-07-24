import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .schema import INDEXES_SQL, SCHEMA_SQL

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    def _execute(self, sql: str, params=()):
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(sql, params)
            conn.commit()
            return cur

    def _fetchone(self, sql: str, params=()):
        conn = self._get_conn()
        return conn.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params=()):
        conn = self._get_conn()
        return conn.execute(sql, params).fetchall()

    def _init_schema(self):
        for stmt in SCHEMA_SQL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._execute(stmt)
        for idx in INDEXES_SQL:
            self._execute(idx)

    def create_session(self, category: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._execute("INSERT INTO scrape_sessions (category, started_at) VALUES (?, ?)", (category, now))
        return cur.lastrowid

    def finish_session(self, session_id, status="completed", total_pages=None, total_found=None):
        now = datetime.now(timezone.utc).isoformat()
        fields = ["finished_at = ?", "status = ?"]
        params = [now, status]
        if total_pages is not None:
            fields.append("total_pages = ?"); params.append(total_pages)
        if total_found is not None:
            fields.append("total_found = ?"); params.append(total_found)
        params.append(session_id)
        self._execute(f"UPDATE scrape_sessions SET {', '.join(fields)} WHERE id = ?", params)

    def get_session_info(self, category):
        return self._fetchone(
            "SELECT id, started_at, finished_at, status, total_pages, total_found "
            "FROM scrape_sessions WHERE category = ? ORDER BY id DESC LIMIT 1", (category,))

    def mark_page_done(self, category, page_number, session_id, article_count):
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            "INSERT OR REPLACE INTO pages (category, page_number, session_id, scraped_at, status, article_count) "
            "VALUES (?, ?, ?, ?, 'done', ?)", (category, page_number, session_id, now, article_count))

    def mark_page_error(self, category, page_number, session_id, error_message):
        now = datetime.now(timezone.utc).isoformat()
        existing = self._fetchone("SELECT retry_count FROM pages WHERE category = ? AND page_number = ?", (category, page_number))
        retry = (existing["retry_count"] + 1) if existing else 1
        self._execute(
            "INSERT OR REPLACE INTO pages (category, page_number, session_id, scraped_at, status, retry_count, error_message) "
            "VALUES (?, ?, ?, ?, 'error', ?, ?)", (category, page_number, session_id, now, retry, error_message))

    def upsert_article(self, category, title, url, session_id, loaded_at, date="", excerpt="", tags=None, author=None):
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        content_hash = self._compute_content_hash(None, title or "", excerpt or "")
        self._execute(
            "INSERT OR IGNORE INTO articles "
            "(category, title, url, date, excerpt, tags, content_hash, session_id, loaded_at, author, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'metadata')",
            (category, title, url, date, excerpt, tags_json, content_hash, session_id, loaded_at, author))

    def update_article_full_text(self, article_id, content_raw, content_md, session_id, author=None):
        now = datetime.now(timezone.utc).isoformat()
        content_hash = self._compute_content_hash(content_md)
        fields = ["content_raw = ?", "content_md = ?", "content_hash = ?", "article_scraped_at = ?", "status = 'full'"]
        params = [content_raw, content_md, content_hash, now]
        if author:
            fields.append("author = ?"); params.append(author)
        params.append(article_id)
        self._execute(f"UPDATE articles SET {', '.join(fields)} WHERE id = ?", params)

    def mark_article_error(self, article_id):
        self._execute("UPDATE articles SET status = 'error' WHERE id = ?", (article_id,))

    def get_articles_for_full_text(self, category, since=None):
        query = "SELECT id, url, date FROM articles WHERE category = ? AND (status = 'metadata' OR content_md IS NULL)"
        params = [category]
        if since:
            query += " AND date >= ?"; params.append(since)
        return self._fetchall(query, params)

    def get_category_info(self, category):
        return self._fetchone(
            "SELECT COUNT(*) as total_articles, "
            "  COALESCE(SUM(CASE WHEN status = 'full' THEN 1 ELSE 0 END), 0) as full_text_count, "
            "  MIN(date) as earliest, MAX(date) as latest "
            "FROM articles WHERE category = ?", (category,))

    def get_latest_date(self, category):
        row = self._fetchone(
            "SELECT MAX(date) as latest FROM articles WHERE category = ? AND status IN ('metadata', 'full')", (category,))
        return row["latest"] if row and row["latest"] else None

    def get_categories(self):
        rows = self._fetchall("SELECT category, COUNT(*) as cnt FROM articles GROUP BY category ORDER BY category")
        return [{"name": r["category"], "count": r["cnt"]} for r in rows]

    def get_schema(self):
        tables = self._fetchall("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        schema = []
        for t in tables:
            cols = self._fetchall(f"PRAGMA table_info('{t['name']}')")
            schema.append({"table": t["name"], "columns": [{"name": c["name"], "type": c["type"], "notnull": bool(c["notnull"]), "pk": bool(c["pk"])} for c in cols]})
        return schema

    def search_articles(self, keyword, category=None, limit=20):
        like = f"%{keyword}%"
        query = "SELECT id, title, url, author, date, excerpt, SUBSTR(content_md, 1, 500) as content_preview, tags, category FROM articles WHERE (title LIKE ? OR excerpt LIKE ? OR content_md LIKE ?)"
        params = [like, like, like]
        if category:
            query += " AND category = ?"; params.append(category)
        query += " ORDER BY date DESC LIMIT ?"; params.append(limit)
        rows = self._fetchall(query, params)
        return [{"id": r["id"], "title": r["title"], "url": r["url"], "author": r["author"],
                 "date": r["date"], "excerpt": r["excerpt"], "content_preview": r["content_preview"],
                 "tags": json.loads(r["tags"]) if r["tags"] else [], "category": r["category"]} for r in rows]

    def get_article(self, article_id: int) -> dict | None:
        row = self._fetchone(
            "SELECT id, category, title, url, author, date, excerpt, "
            "content_md, content_hash, tags, status FROM articles WHERE id = ?",
            (article_id,))
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("tags"), str):
            try:
                d["tags"] = json.loads(d["tags"])
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
        return d

    def list_latest_articles(self, category, limit=5):
        rows = self._fetchall(
            "SELECT id, title, date, excerpt, tags, url, content_md, is_interesting, is_read "
            "FROM articles WHERE category = ? ORDER BY date DESC LIMIT ?", (category, limit))
        return [{"id": r["id"], "title": r["title"], "date": r["date"], "excerpt": r["excerpt"],
                 "tags": json.loads(r["tags"]) if r["tags"] else [], "url": r["url"],
                 "content_md": r["content_md"], "is_interesting": bool(r["is_interesting"]),
                 "is_read": bool(r["is_read"])} for r in rows]

    def get_search_candidates(self, category, query_hash, max_retries=3, scope_since=None, scope_until=None, scope_limit=None):
        clauses = [
            "SELECT a.id, a.title, a.excerpt, a.tags, a.date, a.url, a.content_md, a.content_hash, a.author"
            " FROM articles a WHERE a.category = ?"
            " AND (NOT EXISTS (SELECT 1 FROM search_scores s WHERE s.article_id = a.id AND s.query_hash = ? AND s.content_hash = a.content_hash)"
            "  OR EXISTS (SELECT 1 FROM search_scores s WHERE s.article_id = a.id AND s.query_hash = ? AND s.content_hash = a.content_hash AND s.status = 'error' AND s.attempts < ?))"
        ]
        params = [category, query_hash, query_hash, max_retries]
        if scope_since: clauses.append("AND a.date >= ?"); params.append(scope_since)
        if scope_until: clauses.append("AND a.date <= ?"); params.append(scope_until)
        clauses.append("ORDER BY a.date DESC")
        if scope_limit is not None: clauses.append("LIMIT ?"); params.append(scope_limit)
        rows = self._fetchall(" ".join(clauses), params)
        return [{"id": r["id"], "title": r["title"], "excerpt": r["excerpt"] or "", "tags": json.loads(r["tags"]) if r["tags"] else [],
                 "date": r["date"], "url": r["url"], "content_md": r["content_md"] or "", "author": r["author"] or "",
                 "content_hash": r["content_hash"], "has_excerpt": bool(r["excerpt"])} for r in rows]

    def get_search_candidates_batch(self, category, query_hash, batch, batch_size, max_retries=3, scope_since=None, scope_until=None, scope_limit=None):
        all_candidates = self.get_search_candidates(category, query_hash, max_retries, scope_since=scope_since, scope_until=scope_until, scope_limit=scope_limit)
        start = batch * batch_size
        return all_candidates[start:start + batch_size]

    def save_search_result(self, article_id, query_hash, content_hash, score, comment="", query_name="", query_text=""):
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            "INSERT INTO search_scores (article_id, query_hash, query_name, query_text, content_hash, status, total, comment, scored_at) "
            "VALUES (?, ?, ?, ?, ?, 'scored', ?, ?, ?) "
            "ON CONFLICT(article_id, query_hash, content_hash) DO UPDATE SET "
            "status='scored', total=excluded.total, comment=excluded.comment, scored_at=excluded.scored_at, last_error=NULL",
            (article_id, query_hash, query_name, query_text, content_hash, score, comment, now))

    def get_search_status(self, category, query_hash, scope_since=None, scope_until=None, scope_limit=None):
        scope_clauses = ["SELECT id FROM articles WHERE category = ?"]
        scope_params = [category]
        if scope_since: scope_clauses.append("AND date >= ?"); scope_params.append(scope_since)
        if scope_until: scope_clauses.append("AND date <= ?"); scope_params.append(scope_until)
        scope_clauses.append("ORDER BY date DESC")
        if scope_limit is not None: scope_clauses.append("LIMIT ?"); scope_params.append(scope_limit)
        scope_sql = " ".join(scope_clauses)
        total = self._fetchone(f"SELECT COUNT(*) as cnt FROM ({scope_sql})", scope_params)["cnt"]
        scored = self._fetchone(
            f"SELECT COUNT(*) as cnt FROM search_scores s JOIN ({scope_sql}) scope ON scope.id = s.article_id "
            "WHERE s.query_hash = ? AND s.status = 'scored'", scope_params + [query_hash])
        scored_cnt = scored["cnt"] if scored else 0
        return {"total_articles": total, "scored": scored_cnt, "pending": total - scored_cnt}

    def get_search_report(self, category, query_hash, min_total=0, top=None):
        query = ("SELECT a.id, a.title, a.date, a.url, a.author, a.tags, a.summary_ru, a.is_interesting, a.is_read, s.total, s.comment "
                 "FROM search_scores s JOIN articles a ON a.id = s.article_id "
                 "WHERE a.category = ? AND s.query_hash = ? AND s.status = 'scored' AND s.total >= ? "
                 "ORDER BY a.date DESC, s.total DESC")
        params = [category, query_hash, min_total]
        if top: query += " LIMIT ?"; params.append(top)
        rows = self._fetchall(query, params)
        return [{"id": r["id"], "title": r["title"], "date": r["date"], "url": r["url"],
                 "author": r["author"] or "", "tags": json.loads(r["tags"]) if r["tags"] else [],
                 "summary_ru": r["summary_ru"] or "", "is_interesting": bool(r["is_interesting"]),
                 "is_read": bool(r["is_read"]), "total": r["total"], "comment": r["comment"] or ""} for r in rows]

    def _update_flag(self, ids, column, value):
        if not ids: return
        placeholders = ",".join("?" for _ in ids)
        self._execute(f"UPDATE articles SET {column} = ? WHERE id IN ({placeholders})", [value, *ids])

    def mark_interesting(self, ids): self._update_flag(ids, "is_interesting", 1)
    def unmark_interesting(self, ids): self._update_flag(ids, "is_interesting", 0)
    def mark_read(self, ids): self._update_flag(ids, "is_read", 1)
    def unmark_read(self, ids): self._update_flag(ids, "is_read", 0)

    def get_interesting_articles(self, category):
        rows = self._fetchall("SELECT id, title, date, url, author, tags, summary_ru, is_read FROM articles WHERE category = ? AND is_interesting = 1 ORDER BY date DESC", (category,))
        return [{"id": r["id"], "title": r["title"], "date": r["date"], "url": r["url"], "author": r["author"],
                 "tags": json.loads(r["tags"]) if r["tags"] else [], "summary_ru": r["summary_ru"], "is_read": bool(r["is_read"])} for r in rows]

    def get_unread_articles(self, category):
        rows = self._fetchall("SELECT id, title, date, url, author, tags, summary_ru, is_interesting FROM articles WHERE category = ? AND is_read = 0 ORDER BY date DESC", (category,))
        return [{"id": r["id"], "title": r["title"], "date": r["date"], "url": r["url"], "author": r["author"],
                 "tags": json.loads(r["tags"]) if r["tags"] else [], "summary_ru": r["summary_ru"],
                 "is_interesting": bool(r["is_interesting"])} for r in rows]

    def get_summary_status(self, category):
        total = self._fetchone("SELECT COUNT(*) FROM articles WHERE category = ?", (category,))[0]
        with_content = self._fetchone("SELECT COUNT(*) FROM articles WHERE category = ? AND content_md IS NOT NULL AND content_md != ''", (category,))[0]
        with_summary = self._fetchone("SELECT COUNT(*) FROM articles WHERE category = ? AND summary_ru IS NOT NULL AND summary_ru != ''", (category,))[0]
        return {"total_articles": total, "with_full_text": with_content, "with_summary": with_summary, "pending": with_content - with_summary}

    def save_summary(self, article_id, summary_ru):
        if summary_ru is not None and summary_ru.strip() == "":
            summary_ru = None
        self._execute("UPDATE articles SET summary_ru = ? WHERE id = ?", (summary_ru, article_id))

    @staticmethod
    def _compute_content_hash(content_md, title="", excerpt=""):
        import hashlib
        raw = (content_md or "") + "|" + title + "|" + excerpt
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from analyzer.hashes import compute_content_hash

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
            self._local.conn = sqlite3.connect(
                str(self.path), check_same_thread=False
            )
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

    def _executemany(self, sql: str, params_list):
        if not params_list:
            return
        with self._lock:
            conn = self._get_conn()
            cur = conn.executemany(sql, params_list)
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
        self._migrate()
        for idx in INDEXES_SQL:
            self._execute(idx)
        self._execute(
            "CREATE INDEX IF NOT EXISTS idx_articles_contenthash ON articles(content_hash)"
        )

    def _migrate(self):
        art_cols = [c["name"] for c in self._fetchall("PRAGMA table_info('articles')")]
        if "content_hash" not in art_cols:
            self._execute("ALTER TABLE articles ADD COLUMN content_hash TEXT")
        rows = self._fetchall(
            "SELECT id, title, excerpt, content_md FROM articles WHERE content_hash IS NULL"
        )
        for r in rows:
            ch = compute_content_hash(r["content_md"], r["title"] or "", r["excerpt"] or "")
            self._execute("UPDATE articles SET content_hash = ? WHERE id = ?", (ch, r["id"]))
        if "summary_ru" not in art_cols:
            self._execute("ALTER TABLE articles ADD COLUMN summary_ru TEXT")
        if "is_interesting" not in art_cols:
            self._execute("ALTER TABLE articles ADD COLUMN is_interesting INTEGER DEFAULT 0")
        if "is_read" not in art_cols:
            self._execute("ALTER TABLE articles ADD COLUMN is_read INTEGER DEFAULT 0")

        # Migrate search_scores: remove rubric_hash + add scored_at
        ss_cols = [c["name"] for c in self._fetchall("PRAGMA table_info('search_scores')")]
        if "rubric_hash" in ss_cols:
            logger.info("Migrating search_scores table (removing rubric_hash, filter columns)...")
            self._execute("""
                CREATE TABLE search_scores_new (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id      INTEGER NOT NULL REFERENCES articles(id),
                    query_hash      TEXT NOT NULL,
                    query_name      TEXT,
                    query_text      TEXT,
                    content_hash    TEXT NOT NULL,
                    status          TEXT DEFAULT 'pending',
                    scores_json     TEXT,
                    total           INTEGER,
                    comment         TEXT,
                    attempts        INTEGER DEFAULT 0,
                    last_error      TEXT,
                    scored_at       TEXT,
                    UNIQUE(article_id, query_hash, content_hash)
                )
            """)
            self._execute("""
                INSERT INTO search_scores_new
                    (id, article_id, query_hash, query_name, query_text,
                     content_hash, status, scores_json, total, comment, attempts, last_error)
                SELECT id, article_id, query_hash, query_name, query_text,
                       content_hash, status, scores_json, total, comment, attempts, last_error
                FROM search_scores
            """)
            self._execute("DROP TABLE search_scores")
            self._execute("ALTER TABLE search_scores_new RENAME TO search_scores")
            self._execute("CREATE INDEX IF NOT EXISTS idx_search_article ON search_scores(article_id)")
            self._execute("CREATE INDEX IF NOT EXISTS idx_search_query_total ON search_scores(query_hash, total)")
            self._execute("CREATE INDEX IF NOT EXISTS idx_search_query_status ON search_scores(query_hash, status)")

        if "scored_at" not in ss_cols:
            self._execute("ALTER TABLE search_scores ADD COLUMN scored_at TEXT")

    # ------------------------------------------------------------------
    # Scrape sessions
    # ------------------------------------------------------------------

    def create_session(self, category: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._execute(
            "INSERT INTO scrape_sessions (category, started_at) VALUES (?, ?)",
            (category, now),
        )
        return cur.lastrowid

    def finish_session(
        self,
        session_id: int,
        status: str = "completed",
        total_pages: Optional[int] = None,
        total_found: Optional[int] = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        fields = ["finished_at = ?", "status = ?"]
        params: list = [now, status]
        if total_pages is not None:
            fields.append("total_pages = ?")
            params.append(total_pages)
        if total_found is not None:
            fields.append("total_found = ?")
            params.append(total_found)
        params.append(session_id)
        self._execute(
            f"UPDATE scrape_sessions SET {', '.join(fields)} WHERE id = ?",
            params,
        )

    def get_last_session(self, category: str):
        return self._fetchone(
            "SELECT * FROM scrape_sessions WHERE category = ? AND status = 'completed' "
            "ORDER BY id DESC LIMIT 1",
            (category,),
        )

    def get_session_info(self, category: str):
        return self._fetchone(
            "SELECT id, started_at, finished_at, status, total_pages, total_found "
            "FROM scrape_sessions WHERE category = ? ORDER BY id DESC LIMIT 1",
            (category,),
        )

    def reset_category(self, category: str):
        self._execute("DELETE FROM comments WHERE article_url IN (SELECT url FROM articles WHERE category = ?)", (category,))
        self._execute("DELETE FROM articles WHERE category = ?", (category,))
        self._execute("DELETE FROM pages WHERE category = ?", (category,))

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def mark_page_done(self, category: str, page_number: int, session_id: int, article_count: int):
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            "INSERT OR REPLACE INTO pages (category, page_number, session_id, scraped_at, status, article_count) "
            "VALUES (?, ?, ?, ?, 'done', ?)",
            (category, page_number, session_id, now, article_count),
        )

    def mark_page_error(self, category: str, page_number: int, session_id: int, error_message: str):
        now = datetime.now(timezone.utc).isoformat()
        existing = self._fetchone(
            "SELECT retry_count FROM pages WHERE category = ? AND page_number = ?",
            (category, page_number),
        )
        retry = (existing["retry_count"] + 1) if existing else 1
        self._execute(
            "INSERT OR REPLACE INTO pages (category, page_number, session_id, scraped_at, status, retry_count, error_message) "
            "VALUES (?, ?, ?, ?, 'error', ?, ?)",
            (category, page_number, session_id, now, retry, error_message),
        )

    def get_done_pages(self, category: str):
        rows = self._fetchall(
            "SELECT page_number FROM pages WHERE category = ? AND status = 'done' ORDER BY page_number",
            (category,),
        )
        return {r["page_number"] for r in rows}

    def get_error_pages(self, category: str, max_retries: int = 3):
        rows = self._fetchall(
            "SELECT page_number, retry_count FROM pages WHERE category = ? AND status = 'error' AND retry_count < ?",
            (category, max_retries),
        )
        return {r["page_number"] for r in rows}

    # ------------------------------------------------------------------
    # Articles
    # ------------------------------------------------------------------

    def upsert_article(
        self,
        category: str,
        title: str,
        url: str,
        session_id: int,
        loaded_at: str,
        date: str = "",
        excerpt: str = "",
        tags: Optional[list] = None,
        author: Optional[str] = None,
    ):
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        content_hash = compute_content_hash(None, title or "", excerpt or "")
        self._execute(
            "INSERT OR IGNORE INTO articles "
            "(category, title, url, date, excerpt, tags, content_hash, session_id, loaded_at, author, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'metadata')",
            (category, title, url, date, excerpt, tags_json, content_hash, session_id, loaded_at, author),
        )

    def get_articles_for_full_text(self, category: str, since: Optional[str] = None):
        query = "SELECT id, url, date FROM articles WHERE category = ? AND (status = 'metadata' OR content_md IS NULL)"
        params = [category]
        if since:
            query += " AND date >= ?"
            params.append(since)
        return self._fetchall(query, params)

    def update_article_full_text(
        self,
        article_id: int,
        content_raw: str,
        content_md: str,
        session_id: int,
        author: Optional[str] = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        content_hash = compute_content_hash(content_md)
        fields = [
            "content_raw = ?",
            "content_md = ?",
            "content_hash = ?",
            "article_scraped_at = ?",
            "status = 'full'",
        ]
        params: list = [content_raw, content_md, content_hash, now]
        if author:
            fields.append("author = ?")
            params.append(author)
        params.append(article_id)
        self._execute(
            f"UPDATE articles SET {', '.join(fields)} WHERE id = ?",
            params,
        )

    def mark_article_error(self, article_id: int):
        self._execute(
            "UPDATE articles SET status = 'error' WHERE id = ?",
            (article_id,),
        )

    def insert_comment(self, article_id: int, article_url: str, author: str, date: str, content_md: str):
        self._execute(
            "INSERT INTO comments (article_id, article_url, author, date, content_md) VALUES (?, ?, ?, ?, ?)",
            (article_id, article_url, author, date, content_md),
        )

    # ------------------------------------------------------------------
    # Query / Info
    # ------------------------------------------------------------------

    def get_category_info(self, category: str):
        return self._fetchone(
            "SELECT "
            "  COUNT(*) as total_articles, "
            "  COALESCE(SUM(CASE WHEN status = 'full' THEN 1 ELSE 0 END), 0) as full_text_count, "
            "  MIN(date) as earliest, "
            "  MAX(date) as latest "
            "FROM articles WHERE category = ?",
            (category,),
        )

    def list_latest_articles(self, category: str, limit: int = 5):
        rows = self._fetchall(
            "SELECT id, title, date, excerpt, tags, url, content_md, is_interesting, is_read FROM articles WHERE category = ? "
            "ORDER BY date DESC LIMIT ?",
            (category, limit),
        )
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "title": r["title"],
                "date": r["date"],
                "excerpt": r["excerpt"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "url": r["url"],
                "content_md": r["content_md"],
                "is_interesting": bool(r["is_interesting"]),
                "is_read": bool(r["is_read"]),
            })
        return result

    def get_latest_date(self, category: str) -> str | None:
        row = self._fetchone(
            "SELECT MAX(date) as latest FROM articles WHERE category = ? AND status IN ('metadata', 'full')",
            (category,),
        )
        return row["latest"] if row and row["latest"] else None

    def get_categories(self):
        rows = self._fetchall(
            "SELECT category, COUNT(*) as cnt FROM articles GROUP BY category ORDER BY category"
        )
        return [{"name": r["category"], "count": r["cnt"]} for r in rows]

    def export_articles(self, category: str, since: Optional[str] = None):
        query = (
            "SELECT title, url, author, date, excerpt, content_md, tags "
            "FROM articles WHERE category = ?"
        )
        params = [category]
        if since:
            query += " AND date >= ?"
            params.append(since)
        query += " ORDER BY date DESC"
        rows = self._fetchall(query, params)
        result = []
        for r in rows:
            article = {
                "title": r["title"],
                "url": r["url"],
                "author": r["author"],
                "date": r["date"],
                "excerpt": r["excerpt"],
                "content_md": r["content_md"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
            }
            result.append(article)
        return result

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
                    {"name": c["name"], "type": c["type"], "notnull": bool(c["notnull"]), "pk": bool(c["pk"])}
                    for c in cols
                ],
            })
        return schema

    def search_articles(self, keyword: str, category: Optional[str] = None, limit: int = 20) -> list[dict]:
        like = f"%{keyword}%"
        query = (
            "SELECT id, title, url, author, date, excerpt, "
            "  SUBSTR(content_md, 1, 500) as content_preview, "
            "  tags, category "
            "FROM articles WHERE (title LIKE ? OR excerpt LIKE ? OR content_md LIKE ?)"
        )
        params = [like, like, like]
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        rows = self._fetchall(query, params)
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "title": r["title"],
                "url": r["url"],
                "author": r["author"],
                "date": r["date"],
                "excerpt": r["excerpt"],
                "content_preview": r["content_preview"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "category": r["category"],
            })
        return result

    def query(self, sql: str, params=None) -> list[dict]:
        upper = sql.strip().upper()
        if not upper.startswith("SELECT"):
            raise ValueError("Only SELECT queries are allowed")
        rows = self._fetchall(sql, params or ())
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Analysis scoring
    # ------------------------------------------------------------------

    def get_search_candidates(
        self,
        category: str,
        query_hash: str,
        max_retries: int = 3,
        scope_since: str | None = None,
        scope_until: str | None = None,
        scope_limit: int | None = None,
    ) -> list[dict]:
        clauses = [
            "SELECT a.id, a.title, a.excerpt, a.tags, a.date, a.url,"
            "       a.content_md, a.content_hash, a.author"
            " FROM articles a"
            " WHERE a.category = ?"
            " AND ("
            "  NOT EXISTS ("
            "    SELECT 1 FROM search_scores s"
            "    WHERE s.article_id = a.id"
            "      AND s.query_hash = ?"
            "      AND s.content_hash = a.content_hash"
            "  )"
            "  OR EXISTS ("
            "    SELECT 1 FROM search_scores s"
            "    WHERE s.article_id = a.id"
            "      AND s.query_hash = ?"
            "      AND s.content_hash = a.content_hash"
            "      AND s.status = 'error'"
            "      AND s.attempts < ?"
            "  )"
            ")"
        ]
        params: list = [category, query_hash, query_hash, max_retries]

        if scope_since:
            clauses.append("AND a.date >= ?")
            params.append(scope_since)
        if scope_until:
            clauses.append("AND a.date <= ?")
            params.append(scope_until)

        clauses.append("ORDER BY a.date DESC")

        if scope_limit is not None:
            clauses.append("LIMIT ?")
            params.append(scope_limit)

        sql = " ".join(clauses)
        rows = self._fetchall(sql, params)
        result = []
        for r in rows:
            tags = json.loads(r["tags"]) if r["tags"] else []
            result.append({
                "id": r["id"],
                "title": r["title"],
                "excerpt": r["excerpt"] or "",
                "tags": tags,
                "date": r["date"],
                "url": r["url"],
                "content_md": r["content_md"] or "",
                "author": r["author"] or "",
                "content_hash": r["content_hash"],
                "has_excerpt": bool(r["excerpt"]),
            })
        return result

    def get_search_candidates_batch(
        self,
        category: str,
        query_hash: str,
        batch: int,
        batch_size: int,
        max_retries: int = 3,
        scope_since: str | None = None,
        scope_until: str | None = None,
        scope_limit: int | None = None,
    ) -> list[dict]:
        all_candidates = self.get_search_candidates(
            category, query_hash, max_retries,
            scope_since=scope_since, scope_until=scope_until, scope_limit=scope_limit,
        )
        start = batch * batch_size
        return all_candidates[start:start + batch_size]

    def get_search_comments(self, article_id: int) -> list[dict]:
        rows = self._fetchall(
            "SELECT author, date, content_md FROM comments WHERE article_id = ? ORDER BY id",
            (article_id,),
        )
        return [{"author": r["author"], "date": r["date"], "content_md": r["content_md"]} for r in rows]

    def save_search_result(
        self,
        article_id: int,
        query_hash: str,
        content_hash: str,
        score: int,
        comment: str = "",
        query_name: str = "",
        query_text: str = "",
    ):
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            """
            INSERT INTO search_scores
                (article_id, query_hash, query_name, query_text,
                 content_hash, status, total, comment, scored_at)
            VALUES (?, ?, ?, ?, ?, 'scored', ?, ?, ?)
            ON CONFLICT(article_id, query_hash, content_hash) DO UPDATE SET
                status      = 'scored',
                total       = excluded.total,
                comment     = excluded.comment,
                scored_at   = excluded.scored_at,
                last_error  = NULL
            """,
            (article_id, query_hash, query_name, query_text,
             content_hash, score, comment, now),
        )

    def mark_search_error(
        self,
        article_id: int,
        query_hash: str,
        content_hash: str,
        error: str,
        query_name: str = "",
        query_text: str = "",
    ):
        existing = self._fetchone(
            "SELECT id, attempts FROM search_scores "
            "WHERE article_id = ? AND query_hash = ? AND content_hash = ?",
            (article_id, query_hash, content_hash),
        )
        if existing:
            self._execute(
                "UPDATE search_scores SET status='error', attempts=attempts+1, last_error=? "
                "WHERE article_id=? AND query_hash=? AND content_hash=?",
                (error, article_id, query_hash, content_hash),
            )
        else:
            self._execute(
                """
                INSERT INTO search_scores
                    (article_id, query_hash, query_name, query_text,
                     content_hash, status, attempts, last_error)
                VALUES (?, ?, ?, ?, ?, 'error', 1, ?)
                """,
                (article_id, query_hash, query_name, query_text,
                 content_hash, error),
            )

    def get_search_report(
        self,
        category: str,
        query_hash: str,
        min_total: int = 0,
        top: int | None = None,
    ) -> list[dict]:
        query = (
            "SELECT a.id, a.title, a.date, a.url, a.author, a.tags, a.summary_ru, "
            "  a.is_interesting, a.is_read, "
            "  (SELECT COUNT(*) FROM comments c WHERE c.article_id = a.id) AS comments, "
            "  s.total, s.comment "
            "FROM search_scores s "
            "JOIN articles a ON a.id = s.article_id "
            "WHERE a.category = ? "
            "  AND s.query_hash = ? "
            "  AND s.status = 'scored' AND s.total >= ? "
            "ORDER BY a.date DESC, s.total DESC"
        )
        params: list = [category, query_hash, min_total]
        if top:
            query += " LIMIT ?"
            params.append(top)
        rows = self._fetchall(query, params)
        result = []
        for r in rows:
            tags = json.loads(r["tags"]) if r["tags"] else []
            result.append({
                "id": r["id"],
                "title": r["title"],
                "date": r["date"],
                "url": r["url"],
                "author": r["author"] or "",
                "tags": tags,
                "summary_ru": r["summary_ru"] or "",
                "is_interesting": bool(r["is_interesting"]),
                "is_read": bool(r["is_read"]),
                "comments": r["comments"] or 0,
                "total": r["total"],
                "comment": r["comment"] or "",
            })
        return result

    def get_search_status(
        self,
        category: str,
        query_hash: str,
        scope_since: str | None = None,
        scope_until: str | None = None,
        scope_limit: int | None = None,
    ) -> dict:
        # Build scope subquery
        scope_clauses = ["SELECT id FROM articles WHERE category = ?"]
        scope_params: list = [category]

        if scope_since:
            scope_clauses.append("AND date >= ?")
            scope_params.append(scope_since)
        if scope_until:
            scope_clauses.append("AND date <= ?")
            scope_params.append(scope_until)

        scope_clauses.append("ORDER BY date DESC")

        if scope_limit is not None:
            scope_clauses.append("LIMIT ?")
            scope_params.append(scope_limit)

        scope_sql = " ".join(scope_clauses)

        total_articles = self._fetchone(
            f"SELECT COUNT(*) as cnt FROM ({scope_sql})", scope_params
        )["cnt"]

        scored = self._fetchone(
            f"""
            SELECT COUNT(*) as cnt
            FROM search_scores s
            JOIN ({scope_sql}) scope ON scope.id = s.article_id
            WHERE s.query_hash = ? AND s.status = 'scored'
            """,
            scope_params + [query_hash],
        )
        scored_cnt = scored["cnt"] if scored else 0

        return {
            "total_articles": total_articles,
            "scored": scored_cnt,
            "pending": total_articles - scored_cnt,
        }

    # ------------------------------------------------------------------
    # Trend analysis
    # ------------------------------------------------------------------

    def get_trend_aggregates(
        self,
        category: str,
        period_start: str,
        period_end: str,
        keyword: str | None = None,
    ) -> dict:
        article_filter = " AND a.date >= ? AND a.date <= ?"
        params: list = [category, period_start, period_end]

        total = self._fetchone(
            f"SELECT COUNT(*) as cnt FROM articles a WHERE a.category = ?{article_filter}",
            params,
        )["cnt"]

        with_full_text = self._fetchone(
            f"SELECT COUNT(*) as cnt FROM articles a WHERE a.category = ? AND a.status = 'full'{article_filter}",
            params,
        )["cnt"]

        comment_count = self._fetchone(
            f"SELECT COUNT(*) as cnt FROM comments c JOIN articles a ON a.id = c.article_id WHERE a.category = ?{article_filter}",
            params,
        )["cnt"]

        top_authors = self._fetchall(
            f"SELECT a.author, COUNT(*) as cnt FROM articles a WHERE a.category = ? AND a.author IS NOT NULL AND a.author != ''{article_filter} GROUP BY a.author ORDER BY cnt DESC LIMIT 10",
            params,
        )
        top_authors_list = [{"author": r["author"], "count": r["cnt"]} for r in top_authors]

        result = {
            "period": {"start": period_start, "end": period_end},
            "category": category,
            "total_articles": total,
            "full_texts": with_full_text,
            "total_comments": comment_count,
            "top_authors": top_authors_list,
        }

        if keyword:
            like = f"%{keyword}%"
            kw_params = params + [like, like, like]
            kw_count = self._fetchone(
                f"SELECT COUNT(*) as cnt FROM articles a WHERE a.category = ?{article_filter} AND (a.title LIKE ? OR a.excerpt LIKE ? OR a.content_md LIKE ?)",
                kw_params,
            )["cnt"]
            result["keyword_matches"] = kw_count

        return result

    def get_comment_spikes(
        self,
        category: str,
        period_start: str,
        period_end: str,
        stddev_mult: float = 2.0,
        limit: int = 20,
    ) -> list[dict]:
        rows = self._fetchall(
            """
            WITH stats AS (
                SELECT id, title, date, url, cnt,
                       ROUND(AVG(cnt) OVER (), 1) as avg_cnt,
                       ROUND(AVG(cnt * cnt) OVER () - AVG(cnt) OVER () * AVG(cnt) OVER (), 1) as var_cnt
                FROM (
                    SELECT a.id, a.title, a.date, a.url, COUNT(c.id) as cnt
                    FROM articles a
                    LEFT JOIN comments c ON c.article_id = a.id
                    WHERE a.category = ? AND a.date >= ? AND a.date <= ?
                    GROUP BY a.id
                )
            )
            SELECT id, title, date, url, cnt, avg_cnt, var_cnt
            FROM stats
            WHERE cnt > avg_cnt + ? * SQRT(var_cnt + 0.0001)
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (category, period_start, period_end, stddev_mult, limit),
        )
        return [dict(r) for r in rows]

    def get_keyword_frequency(
        self,
        category: str,
        period_start: str,
        period_end: str,
        keywords: list[str],
    ) -> dict:
        result = {}
        for keyword in keywords:
            like = f"%{keyword}%"
            monthly = self._fetchall(
                """
                SELECT strftime('%Y-%m', a.date) as month, COUNT(*) as freq
                FROM articles a
                WHERE a.category = ?
                  AND a.date >= ? AND a.date <= ?
                  AND (a.title LIKE ? OR a.excerpt LIKE ? OR a.tags LIKE ?)
                GROUP BY month
                ORDER BY month
                """,
                (category, period_start, period_end, like, like, like),
            )
            result[keyword] = [dict(r) for r in monthly]
        return result

    def get_novel_topics(
        self,
        category: str,
        lookback_start: str,
        period_start: str,
        period_end: str,
    ) -> list[dict]:
        rows = self._fetchall(
            """
            SELECT a.id, a.title, a.date, a.url, a.tags, a.excerpt
            FROM articles a
            WHERE a.category = ?
              AND a.date >= ? AND a.date <= ?
              AND a.id NOT IN (
                SELECT a2.id FROM articles a2
                WHERE a2.category = ? AND a2.date < ?
              )
            ORDER BY a.date DESC
            """,
            (category, period_start, period_end, category, lookback_start),
        )
        result = []
        for r in rows:
            tags = json.loads(r["tags"]) if r["tags"] else []
            result.append({
                "id": r["id"],
                "title": r["title"],
                "date": r["date"],
                "url": r["url"],
                "tags": tags,
                "excerpt": r["excerpt"] or "",
            })
        return result

    # ------------------------------------------------------------------
    # Summarization
    # ------------------------------------------------------------------

    def get_articles_for_summary(
        self,
        category: str,
        batch: int,
        batch_size: int = 100,
    ) -> list[dict]:
        rows = self._fetchall(
            """
            SELECT id, title, content_md, url, date
            FROM articles
            WHERE category = ?
              AND content_md IS NOT NULL
              AND content_md != ''
              AND (summary_ru IS NULL OR summary_ru = '')
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            (category, batch_size, batch * batch_size),
        )
        return [dict(r) for r in rows]

    def save_summary(self, article_id: int, summary_ru: str):
        if summary_ru is not None and summary_ru.strip() == "":
            summary_ru = None
        self._execute(
            "UPDATE articles SET summary_ru = ? WHERE id = ?",
            (summary_ru, article_id),
        )

    def get_summary_status(self, category: str) -> dict:
        total = self._fetchone(
            "SELECT COUNT(*) as cnt FROM articles WHERE category = ?", (category,)
        )["cnt"]
        with_content = self._fetchone(
            "SELECT COUNT(*) as cnt FROM articles WHERE category = ? AND content_md IS NOT NULL AND content_md != ''",
            (category,),
        )["cnt"]
        with_summary = self._fetchone(
            "SELECT COUNT(*) as cnt FROM articles WHERE category = ? AND summary_ru IS NOT NULL AND summary_ru != ''",
            (category,),
        )["cnt"]
        return {
            "total_articles": total,
            "with_full_text": with_content,
            "with_summary": with_summary,
            "pending": with_content - with_summary,
        }

    # ------------------------------------------------------------------
    # Interesting / Read flags
    # ------------------------------------------------------------------

    def _update_flag(self, ids: list[int], column: str, value: int):
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        self._execute(
            f"UPDATE articles SET {column} = ? WHERE id IN ({placeholders})",
            [value, *ids],
        )

    def mark_interesting(self, ids: list[int]):
        self._update_flag(ids, "is_interesting", 1)

    def unmark_interesting(self, ids: list[int]):
        self._update_flag(ids, "is_interesting", 0)

    def mark_read(self, ids: list[int]):
        self._update_flag(ids, "is_read", 1)

    def unmark_read(self, ids: list[int]):
        self._update_flag(ids, "is_read", 0)

    def get_interesting_articles(self, category: str) -> list[dict]:
        rows = self._fetchall(
            "SELECT id, title, date, url, author, tags, summary_ru, is_read "
            "FROM articles WHERE category = ? AND is_interesting = 1 "
            "ORDER BY date DESC",
            (category,),
        )
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "title": r["title"],
                "date": r["date"],
                "url": r["url"],
                "author": r["author"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "summary_ru": r["summary_ru"],
                "is_read": bool(r["is_read"]),
            })
        return result

    def get_unread_articles(self, category: str) -> list[dict]:
        rows = self._fetchall(
            "SELECT id, title, date, url, author, tags, summary_ru, is_interesting "
            "FROM articles WHERE category = ? AND is_read = 0 "
            "ORDER BY date DESC",
            (category,),
        )
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "title": r["title"],
                "date": r["date"],
                "url": r["url"],
                "author": r["author"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "summary_ru": r["summary_ru"],
                "is_interesting": bool(r["is_interesting"]),
            })
        return result

    def get_articles_for_export(
        self, category: str,
        filter_mode: str = "all",
    ) -> list[dict]:
        if filter_mode == "unread":
            where = "WHERE category = ? AND is_read = 0"
        elif filter_mode == "interesting":
            where = "WHERE category = ? AND is_interesting = 1"
        else:
            where = "WHERE category = ?"
        rows = self._fetchall(
            f"SELECT a.id, a.is_interesting, a.is_read, a.summary_ru, a.date, a.url, a.tags, a.author, "
            f"(SELECT COUNT(*) FROM comments c WHERE c.article_id = a.id) AS comments "
            f"FROM articles a {where} ORDER BY a.date DESC",
            (category,),
        )
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "is_interesting": bool(r["is_interesting"]),
                "is_read": bool(r["is_read"]),
                "summary_ru": r["summary_ru"],
                "date": r["date"],
                "url": r["url"],
                "tags": json.loads(r["tags"]) if r["tags"] else [],
                "author": r["author"] or "",
                "comments": r["comments"] or 0,
            })
        return result

    # ------------------------------------------------------------------
    # Trend cache
    # ------------------------------------------------------------------

    def save_trend_cache(
        self,
        category: str,
        period_start: str,
        period_end: str,
        params_json: str,
        params_hash: str,
        sql_data_json: str,
        interpretation_json: str | None = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        self._execute(
            """
            INSERT OR REPLACE INTO trend_cache
                (category, period_start, period_end, params_json, params_hash,
                 sql_data_json, interpretation_json, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (category, period_start, period_end, params_json, params_hash,
             sql_data_json, interpretation_json, now),
        )

    def get_trend_cache(
        self,
        category: str,
        params_hash: str,
        period_start: str,
        period_end: str,
    ) -> dict | None:
        row = self._fetchone(
            "SELECT * FROM trend_cache WHERE category = ? AND params_hash = ? AND period_start = ? AND period_end = ?",
            (category, params_hash, period_start, period_end),
        )
        if row:
            return {
                "params_json": row["params_json"],
                "sql_data_json": row["sql_data_json"],
                "interpretation_json": row["interpretation_json"],
                "generated_at": row["generated_at"],
            }
        return None

    def get_trend_status(
        self,
        category: str,
        params_hash: str,
        period_start: str,
        period_end: str,
    ) -> dict:
        cached = self.get_trend_cache(category, params_hash, period_start, period_end)
        if cached is None:
            return {"cached": False}
        return {
            "cached": True,
            "has_interpretation": cached["interpretation_json"] is not None,
            "generated_at": cached["generated_at"],
        }
import sqlite3
import os
import json
import threading
from datetime import datetime, timezone

from config import RZ_CATEGORIES

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS categories (
    rz TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    total_articles INTEGER DEFAULT 0,
    pages INTEGER DEFAULT 0,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS scrape_sessions (
    id INTEGER PRIMARY KEY,
    rz TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT DEFAULT 'running',
    pages_fetched INTEGER DEFAULT 0,
    articles_fetched INTEGER DEFAULT 0,
    last_page INTEGER DEFAULT 0,
    last_di INTEGER DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS articles (
    di INTEGER PRIMARY KEY,
    title_ru TEXT,
    title_en TEXT,
    author TEXT,
    author_url TEXT,
    date_published TEXT,
    date_modified TEXT,
    categories TEXT,
    category_rz TEXT,
    manufacturer TEXT,
    components TEXT,
    summary_ru TEXT,
    body_ru TEXT,
    english_url TEXT,
    article_url TEXT,
    tags TEXT,
    component_count INTEGER DEFAULT 0,
    content_hash TEXT,
    is_interesting INTEGER DEFAULT 0,
    is_read INTEGER DEFAULT 0,
    loaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_scores (
    id INTEGER PRIMARY KEY,
    article_di INTEGER NOT NULL,
    query_hash TEXT NOT NULL,
    query_name TEXT,
    query_text TEXT,
    content_hash TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    scores_json TEXT,
    score INTEGER,
    comment TEXT,
    attempts INTEGER DEFAULT 0,
    last_error TEXT,
    scored_at TEXT,
    UNIQUE(article_di, query_hash, content_hash)
);
"""

INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_articles_rz ON articles(category_rz);
CREATE INDEX IF NOT EXISTS idx_articles_date ON articles(date_published);
CREATE INDEX IF NOT EXISTS idx_articles_loaded ON articles(loaded_at);
CREATE INDEX IF NOT EXISTS idx_search_query ON search_scores(query_hash, score);
CREATE INDEX IF NOT EXISTS idx_search_article ON search_scores(article_di);
"""


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    def _init_db(self):
        with self._lock:
            conn = self._get_conn()
            conn.executescript(SCHEMA_SQL)
            conn.executescript(INDEXES_SQL)
            conn.commit()

    # ---- Summary ----

    def get_db_summary(self) -> dict:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        by_rz = {}
        for row in conn.execute(
            "SELECT category_rz, COUNT(*) as cnt FROM articles GROUP BY category_rz"
        ).fetchall():
            rz = row["category_rz"]
            name = RZ_CATEGORIES.get(rz, rz)
            by_rz[name] = row["cnt"]
        last_session = conn.execute(
            "SELECT * FROM scrape_sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "total_articles": total,
            "by_category": by_rz,
            "last_session": dict(last_session) if last_session else None
        }

    def get_schema(self) -> list:
        conn = self._get_conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        result = []
        for t in tables:
            cols = conn.execute(f"PRAGMA table_info({t[0]})").fetchall()
            result.append({
                "table": t[0],
                "columns": [{"name": c[1], "type": c[2]} for c in cols]
            })
        return result

    # ---- Articles ----

    def article_exists(self, di: int) -> bool:
        conn = self._get_conn()
        return conn.execute("SELECT 1 FROM articles WHERE di=?", (di,)).fetchone() is not None

    def insert_article(self, article: dict):
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT OR IGNORE INTO articles (
                di, title_ru, title_en, author, author_url,
                date_published, date_modified, categories, category_rz,
                manufacturer, components, summary_ru, body_ru,
                english_url, article_url, tags, component_count,
                content_hash, loaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            article.get("di"),
            article.get("title_ru"),
            article.get("title_en"),
            article.get("author"),
            article.get("author_url"),
            article.get("date_published"),
            article.get("date_modified"),
            article.get("categories"),
            article.get("category_rz"),
            article.get("manufacturer"),
            article.get("components"),
            article.get("summary_ru"),
            article.get("body_ru"),
            article.get("english_url"),
            article.get("article_url"),
            article.get("tags"),
            article.get("component_count", 0),
            article.get("content_hash"),
            now
        ))
        conn.commit()

    def insert_articles_batch(self, articles: list):
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        for article in articles:
            conn.execute("""
                INSERT OR IGNORE INTO articles (
                    di, title_ru, title_en, author, author_url,
                    date_published, date_modified, categories, category_rz,
                    manufacturer, components, summary_ru, body_ru,
                    english_url, article_url, tags, component_count,
                    content_hash, loaded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                article.get("di"),
                article.get("title_ru"),
                article.get("title_en"),
                article.get("author"),
                article.get("author_url"),
                article.get("date_published"),
                article.get("date_modified"),
                article.get("categories"),
                article.get("category_rz"),
                article.get("manufacturer"),
                article.get("components"),
                article.get("summary_ru"),
                article.get("body_ru"),
                article.get("english_url"),
                article.get("article_url"),
                article.get("tags"),
                article.get("component_count", 0),
                article.get("content_hash"),
                now
            ))
        conn.commit()

    def get_latest_articles(self, limit: int = 20) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM articles ORDER BY loaded_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_article(self, di: int) -> dict:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM articles WHERE di=?", (di,)).fetchone()
        return dict(row) if row else None

    def get_articles_by_rz(self, rz: str) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM articles WHERE category_rz=? ORDER BY date_published DESC",
            (rz,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unfetched_dis(self, limit: int = 100) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT di FROM articles WHERE body_ru IS NULL ORDER BY loaded_at ASC LIMIT ?",
            (limit,)
        ).fetchall()
        return [r["di"] for r in rows]

    def count_unfetched(self) -> int:
        conn = self._get_conn()
        return conn.execute("SELECT COUNT(*) FROM articles WHERE body_ru IS NULL").fetchone()[0]

    # ---- Sessions ----

    def create_session(self, rz: str = None) -> int:
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO scrape_sessions (rz, started_at, status) VALUES (?, ?, 'running')",
            (rz, now)
        )
        conn.commit()
        return cur.lastrowid

    def update_session(self, session_id: int, **kwargs):
        conn = self._get_conn()
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k}=?")
            vals.append(v)
        if sets:
            conn.execute(
                f"UPDATE scrape_sessions SET {', '.join(sets)} WHERE id=?",
                (*vals, session_id)
            )
            conn.commit()

    def finish_session(self, session_id: int, status: str = "completed",
                       pages: int = 0, articles: int = 0, error: str = None):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute("""
            UPDATE scrape_sessions SET
                finished_at=?, status=?, pages_fetched=?, articles_fetched=?, error_message=?
            WHERE id=?
        """, (now, status, pages, articles, error, session_id))
        conn.commit()

    # ---- Flags I/R ----

    def mark_interesting(self, ids: list):
        if not ids:
            return
        conn = self._get_conn()
        conn.executemany("UPDATE articles SET is_interesting=1 WHERE di=?", [(i,) for i in ids])
        conn.commit()

    def unmark_interesting(self, ids: list):
        if not ids:
            return
        conn = self._get_conn()
        conn.executemany("UPDATE articles SET is_interesting=0 WHERE di=?", [(i,) for i in ids])
        conn.commit()

    def mark_read(self, ids: list):
        if not ids:
            return
        conn = self._get_conn()
        conn.executemany("UPDATE articles SET is_read=1 WHERE di=?", [(i,) for i in ids])
        conn.commit()

    def unmark_read(self, ids: list):
        if not ids:
            return
        conn = self._get_conn()
        conn.executemany("UPDATE articles SET is_read=0 WHERE di=?", [(i,) for i in ids])
        conn.commit()

    def get_interesting(self) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM articles WHERE is_interesting=1 ORDER BY loaded_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unread(self) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM articles WHERE is_read=0 ORDER BY loaded_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Search pipeline ----

    def get_articles_for_search(self, query_hash: str, batch_index: int,
                                 batch_size: int) -> list:
        conn = self._get_conn()
        offset = batch_index * batch_size
        rows = conn.execute("""
            SELECT a.di as id, a.title_ru, a.title_en, a.summary_ru,
                   a.body_ru, a.author, a.categories, a.manufacturer,
                   a.components, a.tags
            FROM articles a
            LEFT JOIN search_scores s ON s.article_di = a.di
                AND s.query_hash = ? AND s.status = 'scored'
            WHERE s.id IS NULL
            ORDER BY a.di DESC
            LIMIT ? OFFSET ?
        """, (query_hash, batch_size, offset)).fetchall()
        return [dict(r) for r in rows]

    def save_search_result(self, article_di: int, query_hash: str, query_name: str,
                            query_text: str, content_hash: str, score: int,
                            scores_json: str = None, comment: str = None):
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT OR REPLACE INTO search_scores
                (article_di, query_hash, query_name, query_text, content_hash,
                 status, scores_json, score, comment, scored_at)
            VALUES (?, ?, ?, ?, ?, 'scored', ?, ?, ?, ?)
        """, (article_di, query_hash, query_name, query_text, content_hash,
              scores_json, score, comment, now))
        conn.commit()

    def get_search_status(self, query_hash: str) -> dict:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        scored = conn.execute(
            "SELECT COUNT(*) FROM search_scores WHERE query_hash=? AND status='scored'",
            (query_hash,)
        ).fetchone()[0]
        return {"total": total, "scored": scored, "pending": total - scored}

    def get_search_report(self, query_hash: str, min_score: int = 0, top: int = None) -> list:
        conn = self._get_conn()
        limit_sql = f"LIMIT {top}" if top else ""
        rows = conn.execute(f"""
            SELECT a.di, a.title_ru, a.title_en, a.author, a.date_published,
                   a.categories, a.manufacturer, a.components, a.summary_ru,
                   a.article_url, a.is_interesting, a.is_read,
                   s.score, s.comment
            FROM search_scores s
            JOIN articles a ON a.di = s.article_di
            WHERE s.query_hash=? AND s.status='scored' AND s.score >= ?
            ORDER BY a.date_published DESC
            {limit_sql}
        """, (query_hash, min_score)).fetchall()
        return [dict(r) for r in rows]

    def get_search_score_range(self, query_hash: str) -> dict:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT MIN(score) as min, MAX(score) as max
            FROM search_scores
            WHERE query_hash=? AND status='scored'
        """, (query_hash,)).fetchone()
        return {"min": row[0] or 0, "max": row[1] or 0}
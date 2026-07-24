import sqlite3
import threading
import os
import json
from datetime import datetime, timezone

from .schema import SCHEMA_SQL, INDEXES_SQL


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
            self._migrate()
            conn.executescript(INDEXES_SQL)
            conn.commit()

    def _migrate(self):
        conn = self._get_conn()
        existing = set()
        try:
            existing = {row[0] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
        except sqlite3.OperationalError:
            pass
        for col in ('summary_ru',):
            if col not in existing:
                try:
                    conn.execute(f"ALTER TABLE projects ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
        if 'summary_ru' not in existing:
            conn.commit()

    # ---- Summary ----

    def get_db_summary(self) -> dict:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        last_session = conn.execute(
            "SELECT * FROM scrape_sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "total_projects": total,
            "last_session": dict(last_session) if last_session else None
        }

    # ---- Projects CRUD ----

    def insert_project(self, project: dict):
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT OR IGNORE INTO projects (
                id, name, slug, summary, description, owner_id,
                owner_name, created_at, updated_at,
                view_count, followers_count,
                tags, content_hash, loaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            project.get("id"),
            project.get("name"),
            project.get("slug"),
            project.get("summary"),
            project.get("description"),
            project.get("owner_id"),
            project.get("owner_name"),
            str(project.get("created_at")) if project.get("created_at") else None,
            str(project.get("updated_at")) if project.get("updated_at") else None,
            project.get("view_count", 0),
            project.get("followers_count", 0),
            json.dumps(project.get("tags", []), ensure_ascii=False) if project.get("tags") else None,
            project.get("content_hash"),
            now
        ))
        conn.commit()

    def project_exists(self, project_id: int) -> bool:
        conn = self._get_conn()
        return conn.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is not None

    def get_project(self, project_id: int) -> dict:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row:
            return dict(row)
        return None

    # ---- Sessions ----

    def create_session(self) -> int:
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "INSERT INTO scrape_sessions (started_at, status) VALUES (?, 'running')",
            (now,)
        )
        conn.commit()
        return cur.lastrowid

    def finish_session(self, session_id: int, status: str = "completed", total: int = 0, offset: int = 0, error: str = None):
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute("""
            UPDATE scrape_sessions SET
                finished_at=?, status=?, total_fetched=?, last_offset=?, error_message=?
            WHERE id=?
        """, (now, status, total, offset, error, session_id))
        conn.commit()

    # ---- Listing ----

    def get_latest_projects(self, limit: int = 20) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM projects ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_projects_by_ids(self, ids: list) -> list:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        conn = self._get_conn()
        rows = conn.execute(
            f"SELECT * FROM projects WHERE id IN ({placeholders})",
            ids
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_projects(self) -> list:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
        return [dict(r) for r in rows]

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

    # ---- Search pipeline ----

    def get_search_candidates_batch(self, query_hash: str, batch_index: int,
                                     batch_size: int, scope: dict = None) -> list:
        conn = self._get_conn()
        offset = batch_index * batch_size
        sql = f"""
            SELECT p.id, p.name, p.summary, p.owner_name, p.tags,
                   p.view_count, p.followers_count, p.created_at
            FROM projects p
            LEFT JOIN search_scores s ON s.project_id = p.id
                AND s.query_hash = ? AND s.status = 'scored'
            WHERE s.id IS NULL
            ORDER BY p.id DESC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(sql, (query_hash, batch_size, offset)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("tags"), str):
                try:
                    d["tags"] = json.loads(d["tags"])
                except (json.JSONDecodeError, TypeError):
                    d["tags"] = []
            result.append(d)
        return result

    def save_search_result(self, project_id: int, query_hash: str, query_name: str,
                            query_text: str, content_hash: str, score: int,
                            scores_json: str = None, comment: str = None):
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT OR REPLACE INTO search_scores
                (project_id, query_hash, query_name, query_text, content_hash,
                 status, scores_json, score, comment, scored_at)
            VALUES (?, ?, ?, ?, ?, 'scored', ?, ?, ?, ?)
        """, (project_id, query_hash, query_name, query_text, content_hash,
              scores_json, score, comment, now))
        conn.commit()

    def get_search_status(self, query_hash: str) -> dict:
        conn = self._get_conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM projects"
        ).fetchone()[0]
        scored = conn.execute(
            "SELECT COUNT(*) FROM search_scores WHERE query_hash=? AND status='scored'",
            (query_hash,)
        ).fetchone()[0]
        return {"total": total, "scored": scored, "pending": total - scored}

    def get_search_report(self, query_hash: str, min_score: int = 0, top: int = None) -> list:
        conn = self._get_conn()
        limit_sql = f"LIMIT {top}" if top else ""
        rows = conn.execute(f"""
            SELECT p.id, p.name, p.owner_name, p.created_at, p.tags,
                   p.view_count, p.followers_count, p.summary_ru,
                   p.description, p.summary,
                   s.score, s.comment
            FROM search_scores s
            JOIN projects p ON p.id = s.project_id
            WHERE s.query_hash=? AND s.status='scored' AND s.score >= ?
            ORDER BY p.created_at DESC
            {limit_sql}
        """, (query_hash, min_score)).fetchall()
        return [dict(r) for r in rows]

    # ---- Flags I/R ----

    def mark_interesting(self, ids: list):
        if not ids:
            return
        conn = self._get_conn()
        conn.executemany(
            "UPDATE projects SET is_interesting=1 WHERE id=?",
            [(i,) for i in ids]
        )
        conn.commit()

    def unmark_interesting(self, ids: list):
        if not ids:
            return
        conn = self._get_conn()
        conn.executemany(
            "UPDATE projects SET is_interesting=0 WHERE id=?",
            [(i,) for i in ids]
        )
        conn.commit()

    def mark_read(self, ids: list):
        if not ids:
            return
        conn = self._get_conn()
        conn.executemany(
            "UPDATE projects SET is_read=1 WHERE id=?",
            [(i,) for i in ids]
        )
        conn.commit()

    def unmark_read(self, ids: list):
        if not ids:
            return
        conn = self._get_conn()
        conn.executemany(
            "UPDATE projects SET is_read=0 WHERE id=?",
            [(i,) for i in ids]
        )
        conn.commit()

    def get_interesting(self) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM projects WHERE is_interesting=1 ORDER BY loaded_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_unread(self) -> list:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM projects WHERE is_read=0 ORDER BY loaded_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Summary ----

    def get_candidates_for_summary(self, limit: int = 100) -> list:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT id, name, summary, description, tags
            FROM projects
            WHERE summary_ru IS NULL
            ORDER BY loaded_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def save_summary(self, project_id: int, summary_ru: str):
        conn = self._get_conn()
        conn.execute(
            "UPDATE projects SET summary_ru=? WHERE id=?",
            (summary_ru, project_id)
        )
        conn.commit()

    def get_summary_status(self) -> dict:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        done = conn.execute(
            "SELECT COUNT(*) FROM projects WHERE summary_ru IS NOT NULL"
        ).fetchone()[0]
        return {"total": total, "summarized": done, "pending": total - done}
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scrape_sessions (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT DEFAULT 'running',
    total_fetched INTEGER DEFAULT 0,
    enriched_count INTEGER DEFAULT 0,
    last_offset INTEGER DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT,
    summary TEXT,
    description TEXT,
    owner_id INTEGER,
    owner_name TEXT,
    created_at TEXT,
    updated_at TEXT,
    view_count INTEGER DEFAULT 0,
    followers_count INTEGER DEFAULT 0,
    tags TEXT,
    content_hash TEXT,
    summary_ru TEXT,
    is_interesting INTEGER DEFAULT 0,
    is_read INTEGER DEFAULT 0,
    loaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_scores (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
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
    UNIQUE(project_id, query_hash, content_hash)
);
"""

INDEXES_SQL = """
CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id);
CREATE INDEX IF NOT EXISTS idx_projects_loaded ON projects(loaded_at);
CREATE INDEX IF NOT EXISTS idx_search_query ON search_scores(query_hash, score);
CREATE INDEX IF NOT EXISTS idx_search_project ON search_scores(project_id);
"""
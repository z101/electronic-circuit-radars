SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scrape_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT DEFAULT 'running',
    total_pages     INTEGER,
    total_found     INTEGER,
    full_text_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT NOT NULL,
    page_number     INTEGER NOT NULL,
    session_id      INTEGER REFERENCES scrape_sessions(id),
    scraped_at      TEXT NOT NULL,
    status          TEXT DEFAULT 'done',
    article_count   INTEGER,
    retry_count     INTEGER DEFAULT 0,
    error_message   TEXT,
    UNIQUE(category, page_number)
);

CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT NOT NULL,
    title           TEXT NOT NULL,
    url             TEXT NOT NULL,
    author          TEXT,
    date            TEXT,
    excerpt         TEXT,
    content_raw     TEXT,
    content_md      TEXT,
    tags            TEXT,
    content_hash    TEXT,
    summary_ru      TEXT,
    is_interesting  INTEGER DEFAULT 0,
    is_read         INTEGER DEFAULT 0,
    session_id      INTEGER REFERENCES scrape_sessions(id),
    loaded_at       TEXT NOT NULL,
    article_scraped_at TEXT,
    status          TEXT DEFAULT 'metadata',
    UNIQUE(category, url)
);

CREATE TABLE IF NOT EXISTS comments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      INTEGER NOT NULL REFERENCES articles(id),
    article_url     TEXT NOT NULL,
    author          TEXT,
    date            TEXT,
    content_md      TEXT
);

CREATE TABLE IF NOT EXISTS search_scores (
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
);

CREATE TABLE IF NOT EXISTS trend_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT NOT NULL,
    period_start    TEXT NOT NULL,
    period_end      TEXT NOT NULL,
    params_json     TEXT NOT NULL,
    params_hash     TEXT NOT NULL,
    sql_data_json   TEXT NOT NULL,
    interpretation_json   TEXT,
    generated_at    TEXT NOT NULL,
    UNIQUE(category, params_hash, period_start, period_end)
);
"""

INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category)",
    "CREATE INDEX IF NOT EXISTS idx_articles_date ON articles(date)",
    "CREATE INDEX IF NOT EXISTS idx_articles_loaded ON articles(loaded_at)",
    "CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status)",
    "CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url)",
    "CREATE INDEX IF NOT EXISTS idx_articles_interesting ON articles(is_interesting)",
    "CREATE INDEX IF NOT EXISTS idx_pages_category ON pages(category)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_category ON scrape_sessions(category)",
    "CREATE INDEX IF NOT EXISTS idx_comments_article ON comments(article_id)",
    "CREATE INDEX IF NOT EXISTS idx_search_query_total ON search_scores(query_hash, total)",
    "CREATE INDEX IF NOT EXISTS idx_search_query_status ON search_scores(query_hash, status)",
    "CREATE INDEX IF NOT EXISTS idx_search_article ON search_scores(article_id)",
    "CREATE INDEX IF NOT EXISTS idx_trend_params ON trend_cache(category, params_hash, period_start, period_end)",
]
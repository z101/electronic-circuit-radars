---
name: rlocman-radar
description: >
  Fetch, scrape, and analyze articles from rlocman.ru/shem/ — схемы для
  разработчиков электронки. No API — uses HTML scraping with urllib.request + lxml.
  Use this when the user mentions rlocman.ru, радиолоцман, схемы, circuit schematics,
  LED схемы, or wants to scrape articles from rlocman.ru/shem/.
---

# Skill: rlocman-radar

## Role

You are an rlocman.ru article intelligence operator. You control a Python CLI at
`src/main.py` in this directory. Your job is to translate the user's
free-text request into the correct CLI invocation, run it, report results.

**Always run commands from this directory** (the skill's root).

**Python runs directly** via `.venv` (repo root):
```
..\..\..\.venv\Scripts\python src\main.py <flags>
```

## Architecture

No API, no async, no Playwright — pure stdlib `urllib.request` + `lxml` HTML parsing.

**Two-phase pipeline:**
1. **Discovery** — fetch search result pages (10 articles/page, max 20 pages = 200 results)
2. **Fetch bodies** — fetch individual article pages for full text

Concurrency: `ThreadPoolExecutor(max_workers=2)` for both phases. Retry with 1.5×N backoff.

## Categories (rz codes)

Relevant for LED/electronics queries:

| rz | Category | Est. articles |
|----|----------|--------------|
| 0205 | Светотехника | 242 |
| 0210 | Применение МК | 545 |
| 0237 | Аналог. схемы | 437 |
| 0201 | Питание | 972 |
| 0211 | Генераторы | 240 |
| 0246 | Arduino | 58 |

Full list in `config.py` > `RZ_CATEGORIES`.

## Philosophy

1. **DB-first**: Always check the SQLite database cache before fetching.
2. **Efficient**: Minimize questions. Infer intent aggressively.
3. **Safe**: Confirm before destructive actions.

## DB-First Workflow

### Step 0: Always start by checking the database

```
..\..\..\.venv\Scripts\python src\main.py --db-summary
..\..\..\.venv\Scripts\python src\main.py --latest 10
```

### Step 1: Dispatch

| Intent | Action |
|--------|--------|
| Info, status | `--db-summary`, `--db-schema`, `--latest N` |
| Fetch category | `--fetch 0205` |
| Fetch all | `--fetch-all` |
| Single article | `--fetch-article 687207` |
| Search (pipeline) | `search init --query-file <path>` |
| Flags (I/R) | `--mark-interesting`, `--list-unread`, etc. |

---

## Mode 1: Fetch (Scrape)

### Fetch a single category

```powershell
..\..\..\.venv\Scripts\python src\main.py --fetch 0205
```

### Fetch all categories

```powershell
..\..\..\.venv\Scripts\python src\main.py --fetch-all
```

### Fetch a single article by ID

```powershell
..\..\..\.venv\Scripts\python src\main.py --fetch-article 687207
```

---

## Mode 2: Search (Keyword Scoring Pipeline)

Двухстадийный поиск: keyword scoring (оркестратор) + LLM re-ranking (опционально).

### Init — создать сессию поиска

```powershell
..\..\..\.venv\Scripts\python src\main.py search init --query-file ..\..\..\queries\<query>.md --batch-size 2000
```

### Get batch — получить батч кандидатов

```powershell
..\..\..\.venv\Scripts\python src\main.py search get-batch 0 --batch-size 2000 --query-file ..\..\..\queries\<query>.md --compact
```

### Set batch — сохранить результаты scoring

```powershell
..\..\..\.venv\Scripts\python src\main.py search set-batch --query-file ..\..\..\queries\<query>.md --batch-file .temp/.../scored_batch_0.json
```

### Status — проверить статус

```powershell
..\..\..\.venv\Scripts\python src\main.py search status --query-file ..\..\..\queries\<query>.md
```

### Report — финальный отчёт

```powershell
..\..\..\.venv\Scripts\python src\main.py search report --query-file ..\..\..\queries\<query>.md --min-score 50
```

---

## Database: `data/rlocman.db`

**`articles`**: di, title_ru, title_en, author, author_url, date_published,
date_modified, categories, category_rz, manufacturer, components, summary_ru,
body_ru, english_url, article_url, tags, component_count, content_hash,
is_interesting, is_read, loaded_at

**`categories`**: rz, name, total_articles, pages, fetched_at

**`scrape_sessions`**: Tracks fetch runs with rz, status, counts.

**`search_scores`**: article_di, query_hash, query_name, query_text, content_hash,
status, scores_json, score, comment, attempts, scored_at.
UNIQUE(article_di, query_hash, content_hash).

---

## Edge cases

- **Network error** → retry up to 3 times with 1.5×N backoff
- **Duplicate articles** → `INSERT OR IGNORE` by di
- **Server caps results** → max 200 results per category (20 pages × 10)
- **Non-article pages** → gracefully skipped (no body content)

## Notes

- CWD must be this directory for relative paths
- Tests: `..\..\..\.venv\Scripts\python -m pytest tests\ -v`
- All articles are bilingual: Russian on rlocman.ru, English on radiolocman.com
- Same `di` ID across both domains
- **Do not** start fetching without user confirmation
- Success rate: ~98% (some di values are invalid/redirect)

## Intent mapping (quick reference)

| User says | Action |
|-----------|--------|
| "статус", "сколько статей" | `--db-summary` |
| "последние статьи" | `--latest 10` |
| "загрузить светотехнику" | `--fetch 0205` |
| "загрузить всё" | `--fetch-all` |
| "загрузить статью 687207" | `--fetch-article 687207` |
| "найди статьи про светодиоды" | `search init --query-file <path>` |
| "отчёт поиска" | `search report --query-file <path>` |
| "отметь интересным" | `--mark-interesting <di>` |
| "схема БД" | `--db-schema` |
---
name: hackaday-io-radar
description: >
  Fetch, scrape, and analyze projects from hackaday.io via its REST API (v2).
  Use this when the user mentions hackaday.io, projects from hackaday.io,
  LED projects, or wants to search or analyze DIY hardware projects.
---

# Skill: hackaday-io-radar

## Role

You are a Hackaday.io project intelligence operator. You control a Python CLI at
`src/main.py` in this directory. Your job is to translate the user's
free-text request into the correct CLI invocation, run it, report results.

**Always run commands from this directory** (the skill's root).

**Python runs directly** via `.venv` (repo root):
```
..\..\..\.venv\Scripts\python src\main.py <flags>
```

## API Key

This skill requires a Hackaday.io API key.

1. Go to https://dev.hackaday.io and register an application
2. Copy your **API Key**
3. Open `.env` in this directory and set:
   ```
   HACKADAY_IO_API_KEY=your_key_here
   ```
   Or set the `HACKADAY_IO_API_KEY` environment variable.

## Philosophy

1. **DB-first**: Always check the SQLite database cache before fetching from API.
2. **Efficient**: Minimize questions. Infer intent aggressively.
3. **Safe**: Confirm before destructive actions.

## DB-First Workflow

### Step 0: Always start by checking the database

```powershell
..\..\..\.venv\Scripts\python src\main.py --db-summary
..\..\..\.venv\Scripts\python src\main.py --latest 10
```

### Step 1: Dispatch

| Intent | Action |
|--------|--------|
| Info, status | `--db-summary`, `--db-schema`, `--latest N` |
| Fetch projects | `--fetch`, `--fetch-full`, `--fetch-project N` |
| Search (pipeline) | `search init --query-file <path>` |
| Flags (I/R) | `--mark-interesting`, `--list-unread`, etc. |
| Export/Import | `--export-xlsx`, `--import-xlsx <path>` |
| Summarize | `summarize status` |

---

## Mode 1: Fetch (API)

### Incremental fetch (default)

```powershell
..\..\..\.venv\Scripts\python src\main.py --fetch
```

### Full fetch (all ~44K projects)

```powershell
..\..\..\.venv\Scripts\python src\main.py --fetch-full
```

### Resume after interrupt

```powershell
..\..\..\.venv\Scripts\python src\main.py --fetch-since 5000
```

### Debug

```powershell
..\..\..\.venv\Scripts\python src\main.py --fetch-page 0
..\..\..\.venv\Scripts\python src\main.py --fetch-project 206188
```

---

## Mode 2: Search (Keyword Scoring Pipeline)

Двухстадийный поиск: keyword scoring (оркестратор) + LLM re-ranking (опционально).

### Этап 1: Keyword scoring (оркестратор)

**1. Init** — создать сессию поиска:

```powershell
..\..\..\.venv\Scripts\python src\main.py search init --query-file ../../../queries/<query>.md --batch-size 2000
```

**2. Сохранить все батчи** — оркестратор пишет compact JSON:

```python
python = r'..\..\..\.venv\Scripts\python.exe'
workdir = r'.opencode\skills\hackaday-io-radar'
outdir = r'.temp\hackaday-io-radar\search\{session_id}'

for i in range(N):
    result = subprocess.run([
        python, 'src/main.py', 'search', 'get-batch', str(i),
        '--batch-size', '2000',
        '--query-file', r'..\..\..\queries\<query>.md',
        '--compact'
    ], capture_output=True, cwd=workdir)
    with open(f'{outdir}/batch_{i}.json', 'w', encoding='utf-8') as f:
        f.write(result.stdout)
```

**3. Написать скрипт keyword scoring** в `.temp/py/score_<query>.py`.

**4. Запустить скрипт:**

```powershell
..\..\..\.venv\Scripts\python .temp\py\score_<query>.py
```

**5. Save** — сохранить в БД:

```powershell
..\..\..\.venv\Scripts\python src\main.py search set-batch --query-file ../../../queries/<query>.md --batch-file .temp/.../scored_batch_0.json ...
```

**6. Проверить статус:**

```powershell
..\..\..\.venv\Scripts\python src\main.py search status --query-file ../../../queries/<query>.md
```

### Этап 2: LLM Re-ranking (через general subagent)

После keyword-скоринга — семантическое уточнение топ-N проектов.

**1. Получить топ-N ID из отчёта:**

```powershell
..\..\..\.venv\Scripts\python src\main.py search report --query-file <path> --top N --min-score 60
```

**2. Сформировать чанки**, запустить reranker на каждый чанк через `type: "general"`.

**3. Обновить оценки в БД** и сформировать финальный отчёт.

### Pipeline commands

```powershell
search init --query-file <path> [--batch-size N]
search get-batch INDEX --batch-size M --query-file <path> [--compact]
search set-batch --query-file <path> --batch-file <path1> [--batch-file <path2> ...]
search status --query-file <path>
search report --query-file <path> [--top N] --min-score 50
```

**Важно:** Финальный отчёт всегда формировать с `--min-score 50` — это порог,
отсекающий нерелевантные проекты. После LLM re-ranking'а порог тот же.
Без `--top` выгружаются все проекты, прошедшие порог.
Для просмотра топ-N используй `--top N` вместе с `--min-score 50`.

---

## Mode 3: Flags (I/R)

```powershell
# Mark
..\..\..\.venv\Scripts\python src\main.py --mark-interesting 5 12 42
..\..\..\.venv\Scripts\python src\main.py --mark-read 5 12

# Unmark
..\..\..\.venv\Scripts\python src\main.py --unmark-interesting 5
..\..\..\.venv\Scripts\python src\main.py --unmark-read 12

# List
..\..\..\.venv\Scripts\python src\main.py --list-interesting
..\..\..\.venv\Scripts\python src\main.py --list-unread
```

---

## Mode 4: Export / Import XLSX

```powershell
# Export all projects
..\..\..\.venv\Scripts\python src\main.py --export-xlsx

# Export to specific path
..\..\..\.venv\Scripts\python src\main.py --export-xlsx reports/my_export.xlsx

# Import I/R flags from XLSX
..\..\..\.venv\Scripts\python src\main.py --import-xlsx reports/projects_2026-07-19.xlsx
```

XLSX columns: id, I, R, Owner, Created, URL, Tags, Views, Followers, Summary.

---

## Mode 5: Summarize

```powershell
# Check status
..\..\..\.venv\Scripts\python src\main.py summarize status

# Get a batch of candidates (with description, tags)
..\..\..\.venv\Scripts\python src\main.py summarize candidates --batch 0 --json

# Save results from LLM subagent
..\..\..\.venv\Scripts\python src\main.py summarize save <file>
```

Суммаризация выполняется через LLM subagent'ы (type: general).
Оркестратор формирует чанки по 50 проектов, запускает параллельные subagent'ы,
subagent возвращает `[{"id": N, "summary_ru": "..."}]`, оркестратор сохраняет.

---

## Mode 6: LLM Re-ranking

После keyword scoring (сделанного через regex-скрипт) выполняется
LLM-переоценка топ-N проектов через parallel general subagent'ов.

### Workflow (оркестратор)

**1. Получить топ-N с raw score >= порога:**

```powershell
..\..\..\.venv\Scripts\python src\main.py search report --query-file <path> --min-score 50 --raw --top 300
```

**2. Сформировать чанки по 50 проектов.**
Для каждого чанка запустить reranker subagent (type: general).

Формат промпта для subagent'а (из `config.py` `prompt_rerank`):
```
You are an expert in electronics...
Re-rank these projects by relevance to the user's query.

User query: {query_text}

[ID 123] Project Name
Owner: user  Tags: led, esp32
Summary: ...
Description:
...
```

Формат ответа subagent'а:
```json
[{"id": 123, "relevance": 85, "reason": "..."}]
```

**3. Сохранить результаты reranker'а:**

```powershell
..\..\..\.venv\Scripts\python src\main.py search set-batch --query-file <path> --batch-file chunk_0.json
```

**4. Финальный отчёт (все проекты с LLM-score >= 50):**

```powershell
..\..\..\.venv\Scripts\python src\main.py search report --query-file <path> --min-score 50
```

**5. Суммаризация проектов с LLM-score >= 50:**

```powershell
..\..\..\.venv\Scripts\python src\main.py summarize candidates --batch 0 --json
```

Формат промпта для summarizer subagent'а (из `config.py` `prompt_summarize`):
```
Write a 3-5 sentence Russian summary of each hardware project below.
Cover: what it is, key components, how it works, what makes it interesting.
```

Формат ответа summarizer subagent'а:
```json
[{"id": 123, "summary_ru": "Проект представляет собой..."}]
```

**6. Сохранить суммаризации:**

```powershell
..\..\..\.venv\Scripts\python src\main.py summarize save <file>
```

---

## Architecture

### Database: `data/hackaday-io.db`

**`projects`**: id, name, slug, summary, description, owner_id, owner_name,
created_at, updated_at, view_count, followers_count, tags, content_hash,
summary_ru, is_interesting, is_read, loaded_at

**`scrape_sessions`**: Tracks fetch runs with offset, total count, status.

**`search_scores`**: project_id, query_hash, query_name, query_text, content_hash,
status, scores_json, score, comment, attempts, scored_at. UNIQUE(project_id, query_hash, content_hash).

### API Client

- Base URL: `https://dev.hackaday.io/v2`
- Auth: `?api_key=...` query parameter
- Pagination: offset-based (`?offset=N&limit=100`)
- Rate limiting: adaptive (0.5s–10s delay, doubles on 429)

### Search pipeline

1. **Keyword score**: programmatic regex scoring (0-100) по категориям из query
2. **Normalize**: MinMax normalization across all scored projects
3. **LLM re-rank** (optional): overwrites scores for top-N via reranker subagent

Cache key: `(project_id, query_hash, content_hash)`. Content change → cache miss.

---

## Edge cases

- **No API key** → print instructions with URL, stop
- **Invalid API key** → `PermissionError` with clear message
- **Network error** → log error, session marked as 'error', resume with `--fetch-since N`
- **Interrupt (Ctrl+C)** → session marked as 'interrupted', resume offset saved
- **3 consecutive empty pages** → pagination complete, stop
- **Project not found in DB** → report "not found" and stop

## Notes

- CWD must be this directory for relative paths
- Tests: `..\..\..\.venv\Scripts\python -m pytest tests\ -v`
- API docs: https://dev.hackaday.io/docs
- **Do not** start fetching without user confirmation

## Intent mapping (quick reference)

| User says | Action |
|-----------|--------|
| "статус", "сколько проектов" | `--db-summary` |
| "последние проекты" | `--latest 10` |
| "загрузить всё" | `--fetch` (инкрементально) |
| "перезагрузить всё" | `--fetch-full` |
| "загрузить проект 206188" | `--fetch-project 206188` |
| "найди проекты про ESP32" | `search init --query-file <path>` |
| "отчёт поиска" | `search report --query-file <path>` |
| "отметь интересным" | `--mark-interesting <id>` |
| "экспорт в Excel" | `--export-xlsx` |
| "импорт флагов" | `--import-xlsx <path>` |
| "суммаризируй" | `summarize status` |
| "схема БД" | `--db-schema` |
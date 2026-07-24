---
name: radio-ru-radar
description: >
  Fetch, scrape, and analyze articles from radio.ru magazine archive. Use this
  when the user mentions radio.ru, журнал Радио, архив радиожурнала,
  "Содержание номера", or wants to scrape articles from radio.ru archive.
---

# Skill: radio-ru-radar

## Role

You are a Radio.ru magazine archive intelligence operator. You control a Python scraper CLI at
`src/main.py` in this directory. Your job is to translate the user's
free-text request into the correct CLI invocation, run it, report results.

**Always run commands from this directory** (the skill's root).

**Python runs directly** via `.venv` (repo root):
```
..\..\..\.venv\Scripts\python src\main.py <flags>
```

## Philosophy

1. **DB-first**: Always check the SQLite database cache before scraping. The database is a cache
   that is easily rebuilt — use it liberally.
2. **Efficient**: Minimize questions. Infer intent aggressively. Never ask questions during cache update — execute immediately.
3. **Safe**: Confirm before destructive actions (`--reset`, if implemented).

## DB-First Workflow

### Step 0: Always start by checking the database

```powershell
..\..\..\.venv\Scripts\python src\main.py --db-summary
..\..\..\.venv\Scripts\python src\main.py --db-summary --json
..\..\..\.venv\Scripts\python src\main.py --latest 10
```

### Step 1: Classify intent

| Intent | Action |
|--------|--------|
| Info, status | `--db-summary`, `--db-schema`, `--latest N`, `--db-search` |
| Scrape | scraper flags (`--auto-scan`, `--since`, `--year`) |
| Search (semantic) | `search init --query-file <path>`, orchestrated via searcher subagents |
| Track (trends/anomalies) | not implemented (stub) |
| Summarize | not implemented (stub) |
| Flags (I/R) | `--mark-interesting`, `--mark-read`, `--list-*` |
| Export | `--export-xlsx` |
| Import | `--import-xlsx <path>` |

---

## Mode: Scraping

### How auto-scan works

`--auto-scan` (default mode) starts from **current month** and walks backwards through months,
decrementing year/month. It automatically switches between two URL formats:

| Years | URL format | Encoding | Content |
|-------|-----------|----------|---------|
| 1994–2001 | `/archive/YYYY/MM/` | KOI8-R | Only table of contents (no links) |
| 2002–2004 | `/archive/YYYY/MM/` | KOI8-R | Annotations (`d.gif`) |
| 2005–2008 | `/archive/YYYY/MM/` | KOI8-R | Annotations + DjVu (`d1.gif`) |
| 2009–2012 | `/archive/YYYY/MM/` | KOI8-R | Annotations + PDF (`d1.gif`) |
| 2010–2026 | `/arhiv/YYYY/M.shtml` | UTF-8 | Annotations + PDF (`d1.gif`) |

- **200 with content** → parses and saves articles
- **404** → increments consecutive_404 counter
- After **10 consecutive 404s** → stops (end reached, newer months not yet published)
- **Empty TOC pages (1994–2001)** → tracked via separate counter; stops after **25 consecutive** empty months (end of digital archive)

### Commands

```powershell
# Full auto-scan (default, from current month backwards)
..\..\..\.venv\Scripts\python src\main.py

# Auto-scan with article annotations (slower, fetches descriptions)
..\..\..\.venv\Scripts\python src\main.py --with-excerpt

# Single month (auto-detects format by year)
..\..\..\.venv\Scripts\python src\main.py --year 2026 --month 4
..\..\..\.venv\Scripts\python src\main.py --year 2005 --month 1

# Range (newest to oldest, auto-detects format)
..\..\..\.venv\Scripts\python src\main.py --since 2005-01 --until 2026-04

# Force a specific URL format
..\..\..\.venv\Scripts\python src\main.py --year 2010 --month 1 --archive  # Force /archive/2010/01/
..\..\..\.venv\Scripts\python src\main.py --year 2009 --month 12 --arhiv  # Force /arhiv/2009/12.shtml

# Dry run (validate without saving)
..\..\..\.venv\Scripts\python src\main.py --year 2005 --month 1 --dry-run

# Custom 404 threshold
..\..\..\.venv\Scripts\python src\main.py --max-404 5
```

### After scraping

Always show final stats:
```powershell
..\..\..\.venv\Scripts\python src\main.py --db-summary
```

To show recent articles:
```powershell
..\..\..\.venv\Scripts\python src\main.py --latest 10
```

### Post-scraping preview format

```
2026-04 Сумерки над заводами [Наука и техника]
    Author: А. ГОЛЫШКО  Page: 4
    Annotation text...

2026-04 Приёмники «Океан»/Selena [Радиоприем]
    Author: Х. ЛОХНИ  Page: 10
    ...
```

---

## Mode: Search

Семантический поиск в два этапа:
1. **Keyword scoring** — быстрый Python-скрипт в оркестраторе (секунды на весь архив)
2. **LLM re-ranking** (опционально) — семантическое уточнение через reranker subagent

### Этап 1: Keyword scoring (оркестратор)

Оркестратор (текущая сессия opencode) выполняет всё сам, без subagent'ов.

**1. Init** — создать сессию поиска:

```powershell
..\..\..\.venv\Scripts\python src\main.py search init --query-file ../../../queries/<query>.md --batch-size 2000
```

**2. Сохранить все батчи** — оркестратор пишет compact JSON через Python:

```python
import json, subprocess

python = r'..\..\..\.venv\Scripts\python.exe'
workdir = r'.opencode\skills\radio-ru-radar'
outdir = r'.temp\radio-ru-radar\search\{session_id}'

for i in range(N):
    result = subprocess.run([
        python, 'src/main.py', 'search', 'get-batch', str(i),
        '--batch-size', '2000',
        '--query-file', r'..\..\..\queries\<query>.md',
        '--compact'
    ], capture_output=True, cwd=workdir)
    data = json.loads(result.stdout)
    with open(f'{outdir}/batch_{i}.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
```

**3. Написать скрипт keyword scoring** в `.temp/py/score_<query>.py` — динамически под query.

Скрипт содержит regex-паттерны из query-файла, разбитые по категориям с весами:
- `core_led` (35): светодиод, led, rgb, ws2812, светоэффект, мигалка
- `patterns` (25): мигание, fading, pwm, breathing, 4017 chaser, гирлянда
- `analog` (25): 555, мультивибратор, транзистор, cmos, cd4017, без МК
- `mcu` (20): arduino, микроконтроллер, atmega, stm32, esp
- `lowpower` (15): cr2032, coin cell, boost, автономный
- `ewaste` (15): реиспользование, salvaging, извлечение, драйвер из LCD
- `russian_terms` (10): новогодний, декоративный, светильник

Негативные паттерны: 3D-печать, крупные инсталляции, только декоративные.

Функция: `score(text) → (0-100, reason)`.

**4. Запустить скрипт:**

```powershell
..\..\..\.venv\Scripts\python .temp\py\score_<query>.py
```

Результат: `.temp/radio-ru-radar/search/{session}/scored_batch_0.json` … `scored_batch_N.json`

**5. Save** — сохранить в БД (по одному, SQLite не любит параллельные writes):

```powershell
..\..\..\.venv\Scripts\python src\main.py search set-batch --query-file ../../../queries/<query>.md --batch-file .temp/.../scored_batch_0.json
```

**6. Проверить статус:**

```powershell
..\..\..\.venv\Scripts\python src\main.py search status --query-file ../../../queries/<query>.md
```

> **Оценка времени:** ~30 секунд на 12 000 статей (init + save batches + python scorer + set-batch).

### Этап 2: LLM Re-ranking (через general subagent)

После keyword-скоринга — семантическое уточнение топ-N статей. Re-ranking выполняется через `type: "general"` с инструкциями reranker'а.

**Оркестратор:**

1. Получить топ-N ID из keyword-оценок:

   ```python
   import json, sqlite3, subprocess
   # Получить топ-N из отчёта
   subprocess.run([python, 'src/main.py', 'search', 'report',
       '--query-file', '<path>', '--top', str(N), '--min-score', '60'])
   
   # Или напрямую из БД:
   db = sqlite3.connect('data/radio.db')
   rows = db.execute('''
       SELECT a.id, a.year, a.month, a.topic, a.author, a.excerpt
       FROM articles a
       JOIN search_scores s ON s.article_id = a.id
       WHERE s.query_hash = ? AND s.score >= ?
       ORDER BY s.score DESC LIMIT ?
   ''', (query_hash, min_score, N)).fetchall()
   ```

2. Сформировать чанки по K статей (K=100):

   ```python
   chunks = [articles[i:i+K] for i in range(0, len(articles), K)]
   ```

3. Запустить reranker на каждый чанк (параллельно). Использовать `type: "general"` с инструкциями reranker'а.
   Input: `{query_text, chunk, output_path}`
   Output: `[{id, score, reason}]`

   Пример запуска:
   ```json
   {
     "subagent_type": "general",
     "prompt": "Ты — reranker. Оцени статьи по рубрике: {query_text}\n\nСтатьи: {chunk}\n\nЗапиши результат через write tool в {output_path}"
   }
   ```

4. Собрать пути от всех reranker'ов.

5. Обновить оценки в БД:

   ```powershell
   ..\..\..\.venv\Scripts\python src\main.py search set-batch --query-file <path> --batch-file chunk_0.json --batch-file chunk_1.json ...
   ```

6. Финальный отчёт:

   ```powershell
   ..\..\..\.venv\Scripts\python src\main.py search report --query-file <path> --top N
   ```

### Pipeline commands

```powershell
# Init / orchestrator
..\..\..\.venv\Scripts\python src\main.py search init --query-file <path> [--batch-size N] [scope]

# Fetch batch N (compact JSON, 1 строка на статью)
..\..\..\.venv\Scripts\python src\main.py search get-batch INDEX --batch-size M --query-file <path> --compact

# Save batch results (from scored JSON files)
..\..\..\.venv\Scripts\python src\main.py search set-batch --query-file <path> --batch-file <path1> [--batch-file <path2> ...]

# Status
..\..\..\.venv\Scripts\python src\main.py search status --query-file <path>

# Report
..\..\..\.venv\Scripts\python src\main.py search report --query-file <path> [--top N] [--min-score N]
```

### Scope flags

```
--limit N        # Только N самых свежих статей
--since YYYY-MM  # С этой даты (включительно)
--until YYYY-MM  # По эту дату (включительно)
```

### Cache

- Key: `(article_id, query_hash, content_hash)`
- Content hash: `(author, topic, excerpt)`
- Изменение query-файла → cache miss

### Architecture

Two-stage search cached in `search_scores`:
1. **Keyword score**: programmatic regex scoring (0-100) по категориям из query
2. **Normalize**: MinMax normalization across all scored articles
3. **LLM re-rank** (optional): overwrites scores for top-N via reranker subagent

---

## Mode: Track (not implemented)

Анализ аномалий и трендов по временным интервалам.

Команда: `--track` (заглушка, вернёт сообщение "not implemented").

---

## Mode: Summarize (not implemented)

Суммаризация статей из базы данных.

Команда: `--summarize` (заглушка, вернёт сообщение "not implemented").

---

## Mode: Flags

Флаги `is_interesting` (I) и `is_read` (R) для статей.

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

## Mode: Export

Экспорт статей в XLSX.

```powershell
# Export all articles
..\..\..\.venv\Scripts\python src\main.py --export-xlsx

# Export only unread
..\..\..\.venv\Scripts\python src\main.py --export-xlsx --filter unread

# Export only interesting
..\..\..\.venv\Scripts\python src\main.py --export-xlsx --filter interesting
```

XLSX columns (base): id, I, R, Date, URL, PDF, Page, Section, Author, Excerpt, Topic.
Columns **I** and **R** are editable (yellow background).

---

## Mode: Import

Импорт флагов (I/R) из XLSX обратно в БД.

```powershell
..\..\..\.venv\Scripts\python src\main.py --import-xlsx reports/radio-ru-radar/articles_2026-07-07.xlsx
```

- Читает колонки **I** и **R** (Y = true)
- Обновляет `is_interesting` и `is_read` в БД
- Остальные колонки игнорируются

---

## Architecture

### Database: `data/radio.db`

- **`articles`**: year, month, section, topic, author, page, excerpt, detail_url, pdf_url, is_interesting, is_read, content_hash, format_type, has_d1
- **`search_scores`**: article_id, query_hash, rubric_hash, content_hash, status, scores_json, score, comment
- **`scraped_months`**: tracks which (year, month) pairs are scraped, with consecutive_404 counter
- **`scrape_sessions`**: logging of each scrape run

`format_type` values: `new` (2010+), `old_pdf` (2009), `old_djvu` (2005–2008), `old_annotation` (2002–2004), `old_toc` (1994–2001).
`has_d1` indicates the article has a downloadable PDF/DjVu file (`d1.gif` icon).

### URL format

Two formats are supported, auto-selected by year:

```
# New format (2010+, UTF-8)
http://www.radio.ru/arhiv/{year}/{month}.shtml
No leading zero for months 1-9 (e.g., /2026/4.shtml, /2025/10.shtml)

# Old format (1994–2012, KOI8-R)
http://www.radio.ru/archive/{year}/{month:02d}/
Leading zero for months 1-9 (e.g., /archive/2005/01/, /archive/1995/12/)
```

Overlap years 2010–2012 work in both formats.

### Parser

**New format (2010+):** Parses `<table class="t_sod">` structure with direct hash URLs:
- `d.gif` → annotation page
- `d1.gif` → PDF download

**Old format (1994–2012):** Parses plain `<table>` with `javascript:opendescription(N)` links:
- `d.gif` → `/archive/YYYY/MM/aN.shtml` (annotation)
- `d1.gif` → DjVu (2005–2008) or PDF (2009+)
- No icons (1994–2001) → TOC only, no detail URLs

### Annotation fetching

Optional `--with-excerpt` fetches each article's detail page via `d.gif`/`d1.gif` links.
Two parsers are used depending on format:

- **New format** (`/arhiv/`): extracts second paragraph under "Аннотация статьи"
- **Old format** (`/archive/`): extracts non-metadata paragraphs from `/archive/YYYY/MM/aN.shtml`

The `--archive` flag forces old format URL construction; `--arhiv` forces new format.

### Search pipeline

Single-stage search cached in `search_scores`:
1. **Score**: each article scored 0-100 by a LLM subagent using a rubric
2. **Normalize**: MinMax normalization across all scored articles to compensate for agent drift

---

## Edge cases

- **No articles in DB** → report "No articles in database." and stop
- **All months already scraped** → auto-scan skips them, checks for newer months
- **Network error** → month marked as 'error', continues to next month
- **Interrupt (Ctrl+C)** → session marked as 'interrupted', partial data preserved  
- **Empty content page** → month marked as done with 0 articles, continues
- **No annotation found** → excerpt stays NULL, no error
- **All search candidates in cache** → report from DB, no LLM
- **Subagent returned invalid JSON** → retry up to 2 times

## Notes

- CWD must be this directory for relative paths
- Tests: `..\..\..\.venv\Scripts\python -m pytest tests/ -v` (from skill dir)
- URL base: `http://www.radio.ru` (not HTTPS — site uses HTTP)
- Reports go to `../../../reports/radio-ru-radar/`

Base directory for this skill: C:\Users\z1011100\repos\electronic-circuit-radars\.opencode\skills\radio-ru-radar
Relative paths in this skill (e.g., src/, data/) are relative to this base directory.
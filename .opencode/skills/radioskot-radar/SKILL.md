---
name: radioskot-radar
description: >
  Scrape and analyze articles from radioskot.com (electronic circuits and projects).
  Use this when the user mentions radioskot, радиосхемы, электронные схемы, or wants
  to scrape articles from radioskot.com.
---

# Skill: radioskot-radar

## Role

You are a Radioskot intelligence operator. You control a Python scraper at
`src/main.py` in this directory. Your job is to translate the user's request into
the correct CLI invocation, run it, report results, and optionally analyze scraped data.

**Always run commands from this directory.**

**Python runs via repo root venv:**
```
..\..\..\.venv\Scripts\python src\main.py <flags>
```

## DB-First Workflow

### Step 0: Always start by checking the database

```powershell
..\..\..\.venv\Scripts\python src\main.py --status
```

### Step 1: Classify intent

| Intent | Action |
|--------|--------|
| Info, status | `--status` |
| Scrape, update, refresh | `--scrape` |
| Search, relevance | `--search` or `search init` |
| Export to Excel | `--export-xlsx` |

### Step 2: Dispatch

---

## Mode 1: Scraping

### First scrape (full archive):

```powershell
..\..\..\.venv\Scripts\python src\main.py --scrape
```

This reads all 4 sitemap files (~4000+ articles), then fetches full text in parallel.
Estimated time for full scrape: ~5-10 minutes.

### Incremental update:

```powershell
..\..\..\.venv\Scripts\python src\main.py --scrape --since 2026-07-01
```

Filters sitemap entries by `lastmod >= date`.

### Post-scraping:

```powershell
..\..\..\.venv\Scripts\python src\main.py --status
```

---

## Mode 2: Search

### Ad-hoc search (quick keyword):

```powershell
..\..\..\.venv\Scripts\python src\main.py --search "LED 555 timer"
```

### Semantic search pipeline (3-stage):

**Stage 1: Keyword scoring — все статьи**

Сначала инициализация — показывает сколько статей ждут оценки:

```powershell
..\..\..\.venv\Scripts\python src\main.py search init --query-file ../../../queries/<query>.md
```

**Save all batches** — orchestrator пишет компактный JSON для всех кандидатов:

```powershell
python = r'..\..\..\.venv\Scripts\python.exe'
workdir = r'.opencode\skills\radioskot-radar'
for i in range(N):
    result = subprocess.run([python, 'src/main.py', 'search', 'get-batch', str(i),
        '--batch-size', '2000', '--query-file', r'..\..\..\queries\<query>.md', '--compact'
    ], capture_output=True, cwd=workdir)
    with open(f'.temp/radioskot-radar/search/batch_{i}.json', 'w') as f:
        f.write(result.stdout)
```

**Write keyword scoring script** в `.temp/py/score_<query>.py` — regex-паттерны, извлечённые из query-файла.

**Run scorer** → `scored_batch_N.json`, затем сохранить:

```powershell
..\..\..\.venv\Scripts\python src\main.py search set-batch --query-file ../../../queries/<query>.md --batch-file .temp/.../scored_batch_0.json --batch-file .temp/.../scored_batch_1.json ...
```

Проверить статус:

```powershell
..\..\..\.venv\Scripts\python src\main.py search status --query-file ../../../queries/<query>.md
```

**Stage 2: LLM реранжирование (только score >= 50)**

Получить кандидатов для LLM:

```powershell
..\..\..\.venv\Scripts\python src\main.py search report --query-file ../../../queries/<query>.md --min-score 50
```

Orchestrator разбивает результат на чанки по 100 статей и отправляет parallel `general` subagent'ам.
Каждый subagent возвращает `[{"id": N, "relevance": 0-100, "reason": "..."}]`.

Сохранить LLM-оценки:

```powershell
..\..\..\.venv\Scripts\python src\main.py search set-batch --query-file ../../../queries/<query>.md --batch-file scored_llm_chunk_0.json ...
```

**Stage 3: Финальный отчёт (с обновлёнными scores)**

```powershell
..\..\..\.venv\Scripts\python src\main.py search report --query-file ../../../queries/<query>.md --min-score 50
```

Генерирует XLSX-отчёт в `reports/radioskot-radar/search_<query>_YYYY-MM-DD.xlsx`

Параметры:
- `--min-score N` — порог включения в отчёт (по умолчанию 50)
- Scores хранятся как raw (0–100), без нормализации

---

## Mode 3: Summarize (LLM)

Создаёт 3–5-предложные русские саммари для всех статей с полным текстом.

### Шаг 1: Проверить статус

```powershell
..\..\..\.venv\Scripts\python src\main.py summarize status
```

### Шаг 2: Получить батч кандидатов

```powershell
..\..\..\.venv\Scripts\python src\main.py summarize candidates 0 --batch-size 100 --json
```

Каждый кандидат возвращается как JSON-строка с полями `id`, `title`, `content_md`, `tags`.

### Шаг 3: LLM через parallel subagents

Orchestrator отправляет чанки по 50 статей в параллельные `general` subagent'ы.
Промпт для subagent'а:

```
You are a technical editor. Write a 3-5 sentence Russian summary
of each electronics article below. Cover: what it is, key components,
how it works, what makes it interesting.
Return a strict JSON array, no markdown:
[{"id": N, "summary_ru": "..."}]
```

Каждый subagent возвращает `[{"id": N, "summary_ru": "..."}]`.

### Шаг 4: Сохранить результаты

```powershell
..\..\..\.venv\Scripts\python src\main.py summarize save --batch-file scored_summary_chunk_0.json ...
```

Или через stdin:

```powershell
..\..\..\.venv\Scripts\python src\main.py summarize save < scored_summary.json
```

### Шаг 5 (опционально): Импорт I/R флагов из XLSX

После ручной курации в Excel (колонки I/R) можно импортировать изменения обратно:

```powershell
..\..\..\.venv\Scripts\python src\main.py summarize import --xlsx reports/radioskot-radar/search_<query>_YYYY-MM-DD.xlsx
```

---

## Mode 4: Export

```powershell
..\..\..\.venv\Scripts\python src\main.py --export-xlsx
```

Saves to `reports/radioskot-radar/radioskot_articles_YYYY-MM-DD.xlsx`.

---

## Notes

- CWD must be this directory
- Tests: `pytest tests/ -v`
- All articles from all categories are scraped via 4 sitemap files
- DB at `data/radioskot.db`
- Parallel workers: 20, delay: 0s between requests
- Content hash (SHA-256) используется для инвалидации кэша scores при перескрапинге
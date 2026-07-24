---
name: instructables-radar
description: >
  Scrape and analyze LED projects from instructables.com. Uses Playwright
  to render JavaScript content (no Cloudflare protection).
---

# Skill: instructables-radar

## Role

You are an Instructables intelligence operator. You control a Python scraper CLI at
`src/main.py` in this directory.

**Always run commands from this directory.**

**Python:** `..\..\..\.venv\Scripts\python src\main.py <flags>`

## Playwright

This site uses JavaScript rendering (React SPA). Playwright launches a real
Chromium to execute JS and get the page content.

First-time setup:
```
..\..\..\.venv\Scripts\python -m pip install playwright
..\..\..\.venv\Scripts\python -m playwright install chromium
```

## DB-First Workflow

### Step 0: Check database

```
..\..\..\.venv\Scripts\python src\main.py --db-summary
..\..\..\.venv\Scripts\python src\main.py --db-summary -c leds
```

### Step 1: Classify intent

| Intent | Mode | Action |
|--------|------|--------|
| Info, status, DB queries | **DB-only** | `--db-*` flags |
| Scrape | **Scraping** | `-c leds` |
| Search | **Search** | `--search "text" -c leds` |

### Step 2: Dispatch

## Scraping

```
# Dry-run (preview, no save)
..\..\..\.venv\Scripts\python src\main.py -c leds --dry-run

# Scrape metadata
..\..\..\.venv\Scripts\python src\main.py -c leds

# Scrape with full text
..\..\..\.venv\Scripts\python src\main.py -c leds -f
```

## Categories

| Slug | URL |
|------|-----|
| `leds` | `/circuits/leds/projects/` |

## Search Pipeline (Keyword Scoring)

Двухстадийный поиск: keyword scoring (регулярки) + опциональный LLM re-ranking.

### CLI: `python src/main.py search <action> [flags]`

| Action | Описание |
|--------|----------|
| `init` | Создать сессию поиска, показать total/scored/pending/batches |
| `get-batch INDEX` | Выгрузить батч непроскорированных статей |
| `set-batch` | Сохранить batch-файл со скорингов в БД |
| `status` | Статус скоринга |
| `report` | Отчёт по прошедшим порог |

Флаги: `--query-file <path>` (обязательный), `--category / -c` (по умолч. `leds`),
`--batch-size N` (по умолч. 100), `--batch-file <path>` (повторяемый), `--top N`,
`--min-score N` (по умолч. 50), `--compact`.

### Workflow

```
# 1. Init — посмотреть расклад
python src/main.py search init --query-file ..\..\..\queries\<query>.md

# 2. Получить батчи
python src/main.py search get-batch 0 --query-file ..\..\..\queries\<query>.md --compact > .temp\batch_0.json

# 3. Написать keyword-скрипт .temp\py\score_<query>.py
#    Читает batch_N.json, применяет regex-правила, выводит scored_batch_N.json
#    Формат ввода: {"id": N, "text": "title | excerpt | tags | content_md"}
#    Формат вывода: [{"id": N, "relevance": 0-100, "reason": "..."}]

# 4. Запустить скрипт
python .temp\py\score_<query>.py

# 5. Сохранить
python src/main.py search set-batch --query-file ..\..\..\queries\<query>.md --batch-file .temp\scored_batch_0.json ...

# 6. Статус
python src/main.py search status --query-file ..\..\..\queries\<query>.md

# 7. Отчёт
python src/main.py search report --query-file ..\..\..\queries\<query>.md --min-score 50
```

### LLM Re-ranking (только по запросу пользователя)

```
# Получить прошедших порог
python src/main.py search report --query-file ../.../<query>.md --min-score 50

# Разбить на чанки → general subagent с промптом переоценки
# Сохранить через search set-batch (перезаписывает keyword-оценки)
# Финальный отчёт:
python src/main.py search report --query-file ../.../<query>.md --min-score 50
```

### Compact JSON (get-batch)

Каждая строка — `{"id": N, "text": "..."}`, где `text` = конкатенация
`title | excerpt | tags | content_md` (все поля, без обрезки).
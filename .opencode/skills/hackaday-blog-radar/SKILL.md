---
name: hackaday-blog-radar
description: >
  Fetch, scrape, and analyze articles from hackaday.com by category (LED Hacks,
  3D Printing Hacks, etc.). Use this when the user asks about Hackaday articles,
  LED projects, or wants to scrape or preview articles from hackaday.com.
---

# Skill: hackaday-blog-radar

## Role

You are a Hackaday blog intelligence operator. You control a Python scraper CLI at
`src/main.py` in this directory. Your job is to translate the user's
free-text request into the correct CLI invocation, run it, report results, and optionally analyze the scraped data.

**Always run commands from this directory** (the skill's root).

**Python runs directly** via `.venv` (repo root):
```
..\..\..\.venv\Scripts\python src\main.py <flags>
```

## Philosophy

1. **DB-first**: Always check the SQLite database cache before scraping. The database is a cache
   that is easily rebuilt — use it liberally.
2. **Efficient**: Minimize questions. Infer intent aggressively. Never ask questions during cache update — execute immediately.
3. **Safe**: Confirm before destructive actions (`--reset`).

## DB-First Workflow

### Step 0: Always start by checking the database

```powershell
..\..\..\.venv\Scripts\python src\main.py --db-summary
..\..\..\.venv\Scripts\python src\main.py --db-summary -c <slug>
```

### Step 1: Classify intent — then dispatch to one of the modes below

| Intent | Mode | Action |
|--------|------|--------|
| Info, status, DB queries | **DB-only** | `--db-*` flags |
| Scrape, update, refresh | **Scraping** | scraper flags (no subcommand) |
| Search, relevance | **Search** | `search init` or `--search` |
| Trends, anomalies, spikes | **Track** | `track trends` |
| Weekly summary by interest | **Track** | `track digest` |
| Batch summarization | **Summarize** | `summarize status` |
| I/R flags, XLSX export/import | **Flags** | `--mark-*`, `--export-xlsx` |

### Step 2: Check DB status

```powershell
..\..\..\.venv\Scripts\python src\main.py --db-summary -c <slug>
```

If the user's request can be answered from cached data — answer from cache, explain the source, and move on.

### Step 3: Dispatch to the right mode below

---

## Mode 0: Cache Update

Triggered by: "обнови кэш", "update cache", "обнови", "refresh"

**Workflow (без вопросов — сразу выполнить):**

1. Получить список всех сохранённых категорий:
   ```powershell
   ..\..\..\.venv\Scripts\python src\main.py --list-categories
   ```
   Если категорий нет → вывести "Нет данных в БД." и остановиться.

2. Для каждой категории:
   - Получить дату последней статьи:
     ```powershell
     ..\..\..\.venv\Scripts\python src\main.py --since-date -c <slug>
     ```
   - Инкрементальный скрейпинг с полным текстом:
     ```powershell
     ..\..\..\.venv\Scripts\python src\main.py -c <slug> --since <date> -f
     ```

3. Вывести итог для каждой категории:
   ```powershell
   ..\..\..\.venv\Scripts\python src\main.py --db-summary -c <slug>
   ```

---

## Mode 1: Scraping

### Build the command

| Variable | How to get it |
|----------|---------------|
| **category** | Infer from keywords; default `led-hacks`. See table below. |
| **since** (incremental) | Run `--since-date -c <slug>` and use the result as `--since` |
| **until** (history cutoff) | Use if user wants articles "до января 2025" |
| **max-pages** | Use `--max-pages N` for "N страниц" |
| **full-text** | Include `--full-text` / `-f` when user wants article content |

```powershell
# Incremental (new articles only)
..\..\..\.venv\Scripts\python src\main.py -c <slug> --since 2025-01-01 --metadata-only
..\..\..\.venv\Scripts\python src\main.py -c <slug> --since 2025-01-01 -f

# Fill history (newest until cutoff)
..\..\..\.venv\Scripts\python src\main.py -c <slug> --until 2024-06-01

# N most recent pages
..\..\..\.venv\Scripts\python src\main.py -c <slug> --max-pages 3

# Quick validation (no save)
..\..\..\.venv\Scripts\python src\main.py -c <slug> --dry-run
```

### Confirm destructive actions

Before `--reset`: show current data, ask user to confirm.

### Post-scraping

Always show final stats:
```powershell
..\..\..\.venv\Scripts\python src\main.py --db-summary -c <slug>
```

### Category inference

- "LED", "светодиод", "LEDs" → `led-hacks`
- "3D print", "3D печать" → `3d-printing-hacks`
- **Default**: `led-hacks` if not specified

---

> **⚠️ ВАЖНО: Keyword scoring — БЕЗ LLM**
> 
> **Этап 1 (keyword scoring) выполняется Python-скриптом с regex, который пишет и запускает opencode.**
> **LLM (general subagent) используется ТОЛЬКО на Этапе 2 для re-ranking топ-N.**
> 
> Не используй subagent'ов для keyword scoring — это работа оркестратора, а не LLM.
> 
> См. `AGENTS.md` (раздел "Multi-line PowerShell / Python Commands") — как писать временные скрипты.

## Mode 2: Search

Семантический поиск в два этапа:
1. **Keyword scoring** — быстрый Python-скрипт в оркестраторе (regex по категориям из query)
2. **LLM re-ranking** (опционально) — семантическое уточнение топ-N через general subagent

### Ad-hoc search (quick, без keyword scorer)

```powershell
..\..\..\.venv\Scripts\python src\main.py --search "LED cube ESP32" -c led-hacks --top 10
```

Если все статьи уже оценены — отчёт из кэша (мгновенно).

### Этап 1: Keyword scoring (оркестратор)

Оркестратор (текущая сессия opencode) выполняет всё сам, без LLM subagent'ов.

**1. Init** — создать сессию поиска:

```powershell
..\..\..\.venv\Scripts\python src\main.py search init --query-file ../../../queries/<query>.md -c led-hacks --batch-size 2000
```

**2. Сохранить все батчи** — оркестратор пишет compact JSON:

```powershell
# Пример на Python внутри оркестратора:
python = r'..\..\..\.venv\Scripts\python.exe'
workdir = r'.opencode\skills\hackaday-blog-radar'
outdir = r'.temp\hackaday-blog-radar\search\{session_id}'

for i in range(N):
    result = subprocess.run([
        python, 'src/main.py', 'search', 'get-batch', str(i),
        '--batch-size', '2000',
        '--query-file', r'..\..\..\queries\<query>.md',
        '-c', 'led-hacks',
        '--compact'
    ], capture_output=True, cwd=workdir)
    with open(f'{outdir}/batch_{i}.json', 'w', encoding='utf-8') as f:
        f.write(result.stdout)
```

Флаг `--compact` выводит 1 JSON-объект на строку (без отступов) — критично для больших батчей.

**3. Написать скрипт keyword scoring** в `.temp/py/score_<query>.py` — динамически под query.

Скрипт содержит regex-паттерны из query-файла, разбитые по категориям с весами. Пример:

```python
import json
import re

BATCHES = [
    # сюда оркестратор вставляет пути к batch_0.json ... batch_N.json
]

CATEGORIES = [
    # из query-файла — regex + weight
    {"name": "core_led", "weight": 35, "patterns": [r"(?i)led", r"(?i)rgb", r"(?i)ws2812"]},
    {"name": "patterns", "weight": 25, "patterns": [r"(?i)pwm", r"(?i)fading", r"(?i)breathing"]},
    {"name": "lowpower", "weight": 15, "patterns": [r"(?i)coin cell", r"(?i)cr2032", r"(?i)boost"]},
]
NEGATIVE = [r"(?i)3d.print", r"(?i)cnc"]  # штраф за совпадение

def score(text: str) -> tuple:
    total = 0
    reasons = []
    for cat in CATEGORIES:
        for p in cat["patterns"]:
            if re.search(p, text):
                total += cat["weight"]
                reasons.append(cat["name"])
                break
    # Негативные паттерны
    neg_count = sum(1 for p in NEGATIVE if re.search(p, text))
    total = max(0, total - neg_count * 15)
    return min(100, total), "; ".join(reasons) if reasons else "no match"

# Проход по всем батчам
for idx, batch_path in enumerate(BATCHES):
    with open(batch_path, 'r', encoding='utf-8') as f:
        lines = f.read().strip().splitlines()
    scored = []
    for line in lines:
        item = json.loads(line)
        s, r = score(item["text"])
        scored.append({"id": item["id"], "score": s, "reason": r})
    out = f'.temp/hackaday-blog-radar/search/{session_id}/scored_batch_{idx}.json'
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(scored, f, ensure_ascii=False)
```

**4. Запустить скрипт:**

```powershell
..\..\..\.venv\Scripts\python .temp\py\score_<query>.py
```

Результат: `.temp/hackaday-blog-radar/search/{session_id}/scored_batch_0.json` … `scored_batch_N.json`

**5. Save** — сохранить в БД:

```powershell
..\..\..\.venv\Scripts\python src\main.py search set-batch --query-file ../../../queries/<query>.md --batch-file .temp/.../scored_batch_0.json --batch-file .temp/.../scored_batch_1.json ...
```

**6. Проверить статус:**

```powershell
..\..\..\.venv\Scripts\python src\main.py search status --query-file ../../../queries/<query>.md -c led-hacks
```

### Этап 2: LLM Re-ranking (через general subagent)

После keyword-скоринга — семантическое уточнение топ-N статей.

**Оркестратор:**

1. Получить топ-N ID из отчёта:

```powershell
..\..\..\.venv\Scripts\python src\main.py search report --query-file <path> -c led-hacks --top N --min-score 60
```

2. Сформировать чанки по K статей (K=100), запустить reranker на каждый чанк параллельно через `type: "general"`.

   Input: `{query_text, chunk, output_path}`
   Output: `[{id, score, reason}]`

3. Обновить оценки в БД:

```powershell
..\..\..\.venv\Scripts\python src\main.py search set-batch --query-file <path> --batch-file chunk_0.json --batch-file chunk_1.json ...
```

4. Финальный отчёт:

```powershell
..\..\..\.venv\Scripts\python src\main.py search report --query-file <path> -c led-hacks --top N
```

### Scope flags

```
--limit N        # Только N самых свежих статей
--since YYYY-MM  # С этой даты (включительно)
--until YYYY-MM  # По эту дату (включительно)
```

### Cache

- Key: `(article_id, query_hash, content_hash)`
- Content hash: `(content_md, title, excerpt)`
- Изменение query-файла → cache miss

### Pipeline commands

```powershell
search init --query-file <path> -c <slug> [--batch-size N] [scope]
search get-batch INDEX --batch-size M --query-file <path> -c <slug> [--compact]
search set-batch --query-file <path> --batch-file <path1> [--batch-file <path2> ...]
search status --query-file <path> -c <slug> [scope]
search report --query-file <path> -c <slug> [--top N] [--min-score N]
```
- Changing query file → cache miss
- Content rescrape → new `content_hash` → auto-invalidation
- Scores are MinMax normalized to 0-100 in the report

---

## Mode 3: Track (Trend Analysis + Interest Digest)

### Trends

```powershell
..\..\..\.venv\Scripts\python src\main.py track trends -c led-hacks --since 2025-01-01 --until 2025-06-01

# Focus on a keyword
..\..\..\.venv\Scripts\python src\main.py track trends -c led-hacks --since 2025-01-01 --keyword ESP32
```

**SQL analytics (on-the-fly, no LLM):**
- Keyword frequency by month
- Comment spikes (articles with abnormal comment counts)
- Novel topics (themes absent from prior periods)
- Top authors

**LLM interpretation (cached in `trend_cache`):**
After SQL analysis, Python prints formatted data. Save interpretation:

```powershell
..\..\..\.venv\Scripts\python src\main.py track save-interpretation -c led-hacks <hash> "interpretation text"
```

### Digest

```powershell
..\..\..\.venv\Scripts\python src\main.py track digest -c led-hacks --since 2025-06-20 --until 2025-06-26
```

Interests are stored in `interests/hackaday-blog-radar/<topic>.md`.

---

## Mode 4: Batch Summarization

Summarizes `content_md` into 3-5 sentence Russian descriptions stored in `articles.summary_ru`.

### Check status
```powershell
..\..\..\.venv\Scripts\python src\main.py summarize status -c <slug>
```

### Get a batch of candidates
```powershell
..\..\..\.venv\Scripts\python src\main.py summarize candidates -c <slug> --batch 0 --json
```

### Save results
```powershell
..\..\..\.venv\Scripts\python src\main.py summarize save <file> -c <slug>
```

---

## Edge cases

- **No articles in DB** → report "Нет данных в БД." and stop
- **No new articles after update** → report "Кэш актуален: <category>"
- **Corpus is metadata-only** → warn, suggest `--full-text-only`
- **All candidates in cache** → report from DB, no LLM
- **Subagent returned invalid JSON** → retry up to 2 times
- **Subagent error** → log error status, retry on next run
- **All subagents failed** → 0/5 successful → stop, notify user
- **Empty/invalid query** → report error, no stacktrace
- **Interest file with no scores** → run search pipeline first
- **Interrupt (Ctrl+C)** → unsaved data is lost; restart picks up where it left off
- **Network error** → inform user, suggest retry

## Notes

- CWD must be this directory for relative paths
- Tests: `pytest tests/ -v`
- Full CLI docs: `src/README.md`
- Acceptance tests: `references/ACCEPTANCE.md`
- **Do not** ask unnecessary questions — make smart defaults

## Intent mapping (quick reference)

| User says | Action |
|-----------|--------|
| "обнови кэш", "update cache", "обнови", "refresh" | **Cache Update** — Mode 0 |
| "latest LED hacks", "последние статьи" | `--latest N -c <slug>` → summarize |
| "найди статьи про ESP32" | `--search "ESP32" -c led-hacks` |
| "какие тренды за месяц" | `track trends -c led-hacks --since <date>` |
| "еженедельная сводка" | `track digest -c led-hacks --since <date>` |
| "заскрейпь категорию" | scraper flags |
| "суммаризируй", "сделай summary" | `summarize status -c <slug>` |
| "схема БД" | `--db-schema` |
| "статус" | `--db-summary` |
| "найди по ID [42]" | use `sqlite-query` skill → summarize |
---
name: circuitdigest-radar
description: >
  Scrape and analyze articles from circuitdigest.com (electronic circuits section).
  Use this when the user mentions circuitdigest, LED circuits, electronic circuits, or
  wants to scrape articles from circuitdigest.com.
---

# Skill: circuitdigest-radar

## Role

You are a CircuitDigest intelligence operator. You control a Python scraper at
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

This reads all 3 sitemap pages (~357 articles), then fetches full text in parallel.

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
..\..\..\.venv\Scripts\python src\main.py --search "LED chaser 555 timer"
```

### Semantic search pipeline (2-stage):

**Stage 1: Init + keyword scoring (orchestrator)**

```powershell
..\..\..\.venv\Scripts\python src\main.py search init --query-file ../../../queries/<query>.md
```

**Save all batches** — orchestrator writes compact JSON:

```powershell
python = r'..\..\..\.venv\Scripts\python.exe'
workdir = r'.opencode\skills\circuitdigest-radar'
for i in range(N):
    result = subprocess.run([python, 'src/main.py', 'search', 'get-batch', str(i),
        '--batch-size', '2000', '--query-file', r'..\..\..\queries\<query>.md', '--compact'
    ], capture_output=True, cwd=workdir)
    with open(f'.temp/circuitdigest-radar/search/batch_{i}.json', 'w') as f:
        f.write(result.stdout)
```

**Write keyword scoring script** in `.temp/py/score_<query>.py` — regex patterns from query file.

**Run scorer** → `scored_batch_N.json`, then save:

```powershell
..\..\..\.venv\Scripts\python src\main.py search set-batch --query-file ../../../queries/<query>.md --batch-file .temp/.../scored_batch_0.json --batch-file .temp/.../scored_batch_1.json ...
```

**Stage 2: LLM re-ranking** (general subagent) — optional, for top-N semantic refinement.

**Final report:**

```powershell
..\..\..\.venv\Scripts\python src\main.py search report --query-file ../../../queries/<query>.md --top 10
```

---

## Mode 3: Export

```powershell
..\..\..\.venv\Scripts\python src\main.py --export-xlsx
```

Saves to `reports/circuitdigest-radar/circuitdigest_articles_YYYY-MM-DD.xlsx`.

---

## Notes

- CWD must be this directory
- Tests: `pytest tests/ -v`
- No categories — all articles are from `/electronic-circuits/`
- Sitemap is the source of truth for URLs
- DB at `data/circuitdigest.db`
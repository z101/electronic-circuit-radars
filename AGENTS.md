# info-radar

Semantic analysis of blog articles. Each site has a self-contained radar skill under `.opencode/skills/<site>-radar/`.

All radars follow the same contract: `SKILL.md` with scrape + analyze instructions, scraper package, pytest suite, and a `data/` directory for SQLite storage.

## Workflow

1. User specifies a target site (or "all"). Find the matching skill directory under `.opencode/skills/<site>-radar/`.
2. Load that skill's `SKILL.md` — it contains site-specific scrape + analyze instructions.
3. Scraper saves articles to `data/<site>.db` inside the skill.
4. Read the user's query. Resolution order:
   - `queries/<query>.md` (shared, any radar)
   - `queries/<site>-radar/<query>.md` (skill-specific)
   If no file exists, ask the user to describe what they're looking for and create one.
5. AI processes all articles via parallel subagents, searching against the query.
6. Writes report to `reports/<site>-radar/<query>_YYYY-MM-DD.md`.

## Available Skills

All skills live under `.opencode/skills/<name>/`. Each has a `SKILL.md` with full instructions.

| Skill | Directory | Auto-detection Triggers |
|-------|-----------|------------------------|
| **instructables-radar** | `.opencode/skills/instructables-radar/` | User mentions instructables.com, LED projects, Instructables LED, или wants to scrape projects from instructables |
| **circuitdigest-radar** | `.opencode/skills/circuitdigest-radar/` | User mentions circuitdigest.com, LED схемы, electronic circuits, electronic projects, PWM LED dimmer, или wants to scrape articles from circuitdigest.com |
| **hackaday-io-radar** | `.opencode/skills/hackaday-io-radar/` | User mentions hackaday.io, проекты с hackaday.io, API hackaday.io, портал проектов Hackaday |
| **hackaday-blog-radar** | `.opencode/skills/hackaday-blog-radar/` | User mentions hackaday.com, LED Hacks, 3D Printing Hacks, or wants to scrape articles |
| **radio-ru-radar** | `.opencode/skills/radio-ru-radar/` | User mentions radio.ru, журнал Радио, архив радиожурнала, "Содержание номера", или wants to scrape articles from radio.ru archive |
| **rlocman-radar** | `.opencode/skills/rlocman-radar/` | User mentions rlocman.ru, радиолоцман, схемы, circuit schematics, LED схемы, или wants to scrape articles from rlocman.ru/shem/ |
| **radioskot-radar** | `.opencode/skills/radioskot-radar/` | User mentions radioskot.com, радиосхемы, электронные схемы, радиолюбительские схемы, или wants to scrape articles from radioskot.com |
| **sqlite-query** | `.opencode/skills/sqlite-query/` | User mentions `.db`/`.sqlite` path, "база данных", "бд", "sqlite", "кеш", "сколько записей", "покажи данные из базы", "выполни запрос", or asks about counts/stats/aggregations from a database |

### Auto-detection Priority

When the user asks about **data in a database** (counts, search, stats, SQL queries, `.db` file paths):

1. **First** try to load `sqlite-query` — it handles ad-hoc DB queries
2. **Only if** the question requires scraping new content (not querying existing data), use a domain radar skill instead

Domain skills (`*-radar/`) are for scraping & analyzing articles. `sqlite-query` is for ad-hoc database queries against already-scraped SQLite databases.

## Notes

- Venv is in repo root: `.venv\Scripts\activate` (from repo root).
  From a skill directory (`.opencode/skills/<site>-radar/`), use `..\..\..\.venv\Scripts\python <flags>`.
- Each skill is self-contained — run commands from within the skill directory
- After loading any skill via the `skill` tool, always read `<skill_dir>/SKILL.md` from disk — the inline tool output may be truncated or outdated.

## Naming conventions

- Subagent for semantic re-ranking: **general** with reranker prompt (LLM-based refinement after keyword scoring)

## SQLite Queries

- **All SELECT queries** to `.db`/`.sqlite` files — use the `sqlite-query` skill and its `executor.py`:
  ```
  python .opencode/skills/sqlite-query/scripts/executor.py "<db_path>" "<SQL>"
  ```
- The executor is read-only (blocks INSERT/UPDATE/DELETE/DROP).
- Always investigate schema before writing SQL.

## Multi-line PowerShell / Python Commands

PowerShell on Windows has severe quoting issues with multi-line commands and nested quotes. **Always write multi-line PowerShell or python snippets to a temporary file first**, then execute that file.

Temp files go to `.temp/pwsh/` (`.ps1`) or `.temp/py/` (`.py`) in the repo root — **never delete them**, just overwrite on reuse. This preserves history for debugging.

Examples:

```powershell
# PowerShell script
$tmp = "$pwd\.temp\pwsh\radio_check.ps1"
@"
..\..\..\.venv\Scripts\python -c "print('hello')"
"@ | Set-Content -Path $tmp -Encoding UTF8
& $tmp
```

```powershell
# Python script
$tmp = "$pwd\.temp\py\radio_check.py"
@"
import sqlite3
db = sqlite3.connect(r'data/radio.db')
print(db.execute('SELECT COUNT(*) FROM articles').fetchone()[0])
"@ | Set-Content -Path $tmp -Encoding UTF8
.venv\Scripts\python $tmp
```

This avoids quoting hell and makes debugging easier.
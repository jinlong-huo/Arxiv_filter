# QLab Stack

LLM inference / datacenter networking research group toolkit.

## Architecture

```
arxiv_digest/               # Pipeline (fetch → filter → select → digest)
  ├── Arxiv_filter.py       #   Main orchestrator
  ├── config.py             #   All settings: keywords, thresholds, paths
  ├── fetch.py              #   arXiv API: query builder, retry logic, error classification
  ├── filter.py             #   Text cleaning, keyword scoring (main + OCS, context-gated acronyms)
  ├── digest.py             #   State I/O, top-N selection, markdown generation
  ├── download_papers.py    #   PDF downloader
  ├── rename_papers.py      #   PDF renamer (Zotero format)
  └── test_catchup.py       #   Catch-up mechanism test (no network)
members/<name>/           # Personal workspace — paper notes, projects, repros
paper-notes/              # Shared paper note template
knowledge-base/           # Glossary, reading roadmap, topic deep-dives
templates/                # Reusable templates (LaTeX weekly report, meeting notes, reviews)
survival-guide/           # Career advice, how-to's, conference list
onboarding/ / offboarding/ # Join/leave procedures
```

## Key conventions

- **Paper notes**: copy `paper-notes/template.md` → `members/<name>/paper-notes/<year>/<paper-slug>.md`
- **Python**: single dependency (`feedparser`), install with `pip install -r requirements.txt`
- **Git**: `main` is protected; work on `feature/*` branches; commit types per [CONTRIBUTING.md](CONTRIBUTING.md)
- **Quick commands**: `make run`, `make daily`, `make test`, `make note-new NAME=... FILE=...`

## Pipeline

1. **fetch.py** — Pulls 8 categories from `export.arxiv.org/api/query`, 200 papers per page with auto-pagination. Default daily plan: last 3 days **plus an automatic lookback window** (8→4 days ago) that covers arXiv listing lag and forgotten days. `--from`/`--to` backfill a date range (chunked into 3-day windows, no lookback). Retry with exponential backoff; fatal-error detection for SSL/DNS failures.
2. **filter.py** — Scores each paper against two independent keyword filters: main (LLM/GPU/RDMA/scheduling) and OCS spotlight (optical switching + CPO ecosystem). Clash-prone acronyms (`cpo`/`lpo`/`npo`, `slo`) are context-gated: they only score when optical/serving context words are present.
3. **digest.py** — Selects top-15 main + top-10 OCS + top-5 carry-over, writes `daily_digest.md`. Manages `seen_papers.json` (write-only ledger) and `digest_papers.json` (two-tier gate) state.
4. **Arxiv_filter.py** — Orchestrates the pipeline. `--wait` auto-retries on 429; `--from YYYY-MM-DD --to YYYY-MM-DD` backfills a period; `--ignore-seen` re-scores regardless of digest history (use with `--from/--to`).

### Two-tier digest gate + carry-over

Papers that match but get cut by top-N are stamped `shown: false` (pending) in `digest_papers.json` instead of being permanently skipped. Within `RESURFACE_DAYS` (7) days, a pending paper scoring ≥ `RESURFACE_MIN_SCORE` (12) resurfaces via the **High-Score Carry-Over** digest section (max `MAX_RESURFACED` = 5), labeled with its original first-seen date. Only papers actually shown in a section get `shown: true` (permanent skip). Legacy entries without a `shown` field migrate to `shown: true`.

All knobs live in `config.py`: `CATEGORIES`, `KEYWORDS`, `OCS_KEYWORDS`, `MIN_SCORE`, `MAX_PAPERS`, `LOOKBACK_*`, `RESURFACE_*`.

## Dependencies

- Python 3.11+ with `feedparser` (see `requirements.txt`)

# QLab Stack

LLM inference / datacenter networking research group toolkit.

## Architecture

```
Arxiv_filter.py           # Main orchestrator (fetch → filter → select → digest → send)
arxiv_digest/              # Pipeline modules
  ├── config.py            #   All settings: keywords, thresholds, paths, email
  ├── fetch.py             #   arXiv API: query builder, retry logic, error classification
  ├── filter.py            #   Text cleaning, keyword scoring (main + OCS)
  ├── digest.py            #   State I/O, top-N selection, markdown generation
  └── emailer.py           #   SMTP email (plain text + HTML)
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
- **Quick commands**: `make run`, `make send`, `make note-new NAME=... FILE=...`

## Pipeline

1. **fetch.py** — Pulls 8 CS categories from `export.arxiv.org/api/query`, 200 papers each. Retry with exponential backoff; fatal-error detection for SSL/DNS failures.
2. **filter.py** — Scores each paper against two independent keyword filters: main (LLM/GPU/RDMA/scheduling) and OCS spotlight (optical switching).
3. **digest.py** — Selects top-15 main + top-10 OCS, writes `daily_digest.md`. Manages `seen_papers.json` and `digest_papers.json` state.
4. **emailer.py** — Sends multipart email (plain + HTML) via Gmail SMTP.
5. **Arxiv_filter.py** — Orchestrates the pipeline. `--send` triggers email; `--send-only` re-sends existing digest.

All knobs live in `config.py`: `CATEGORIES`, `KEYWORDS`, `OCS_KEYWORDS`, `MIN_SCORE`, `MAX_PAPERS`.

## Dependencies

- Python 3.11+ with `feedparser` (see `requirements.txt`)
- Gmail app password for email (stored in `.email_password`, gitignored)

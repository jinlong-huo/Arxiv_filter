#!/usr/bin/env python3
"""
Verify that "useful" digest papers actually exist on disk; optionally backfill.

Two audit scopes:
  default         — today's daily_digest.md (all sections incl. carry-over)
  --days N        — digest_papers.json history: papers actually SHOWN in a
                    digest within the last N days (shown=True, i.e. made
                    top-N / carry-over — the "useful papers")

Matching a paper to a file on disk (in order):
  1. arXiv ID embedded in the filename (e.g. ..._2608.28511v1.pdf)
  2. normalized-title containment — files are Author_Year_Title[...].pdf, so
     the digest title's normalized form must appear inside the stem's
     normalized form (robust to renaming, truncation, unicode, punctuation)

Files under _archive_seismic/ are ignored (archived, out of active use).

  --download      — fetch missing papers into their classified subfolders
                    (classify.py routing); routing + downloads are appended
                    to download_log.json with source "verify-downloads"

Usage:
    python3 arxiv_digest/verify_downloads.py                     # audit current digest
    python3 arxiv_digest/verify_downloads.py --days 14          # audit last 14 days of digest history
    python3 arxiv_digest/verify_downloads.py --days 7 --download  # audit + backfill missing
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

# Ensure the project root is on sys.path so `arxiv_digest` is importable
# when this script is run directly (python3 arxiv_digest/verify_downloads.py
# or via run_daily.sh from inside arxiv_digest/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from arxiv_digest import classify                                    # noqa: E402
from arxiv_digest import config                                     # noqa: E402
from arxiv_digest.download_papers import (                          # noqa: E402
    ARXIV_DELAY,
    append_download_log,
    download_pdf,
    extract_papers,
    sanitize_title,
)
from arxiv_digest.rename_papers import extract_arxiv_id, query_arxiv_batch  # noqa: E402

EXCLUDE_DIRS = {"_archive_seismic", "__pycache__"}
TITLE_KEY_LEN = 60   # normalized-title prefix used for containment matching
MIN_KEY_LEN = 10     # shorter titles rely on arXiv-ID matching only


def normalize_key(text: str) -> str:
    """Filename-safe comparison key: lowercase, keep only [a-z0-9]."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def strip_version(paper_id: str) -> str:
    """'2608.28511v1' → '2608.28511'."""
    return re.sub(r"v\d+$", "", paper_id)


def index_tree(root: Path) -> tuple[dict[str, Path], list[tuple[str, Path]]]:
    """Walk the download tree once.

    Returns:
      ids    — {versionless arXiv ID: relative path}
      titles — [(normalized filename stem, relative path)]
    Archived dirs (_archive_seismic) are skipped.
    """
    ids: dict[str, Path] = {}
    titles: list[tuple[str, Path]] = []
    if not root.exists():
        return ids, titles
    for path in sorted(root.rglob("*.pdf")):
        rel = path.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        stem = path.stem
        pid = extract_arxiv_id(stem)
        if pid:
            ids.setdefault(pid, rel)
        titles.append((normalize_key(stem), rel))
    return ids, titles


def find_on_disk(paper_id: str, title: str,
                 ids: dict[str, Path], titles: list[tuple[str, Path]]) -> Path | None:
    """Locate a paper on disk by arXiv ID, then by normalized-title containment."""
    pid = strip_version(paper_id)
    if pid in ids:
        return ids[pid]
    key = normalize_key(title)
    if len(key) >= MIN_KEY_LEN:
        key = key[:TITLE_KEY_LEN]
        for stem, rel in titles:
            if key in stem:
                return rel
    return None


def select_recent_shown(digest_seen: dict, days: int, today: str) -> list[dict]:
    """digest_papers.json 中最近 N 天内实际展示过（shown=True）的论文。

    展示日期 = shown_date（无则回退 date，首次 matched 日期）。
    返回 [{id, title, keywords, ocs_keywords}]。
    """
    from datetime import datetime, timedelta

    try:
        now = datetime.strptime(today, "%Y-%m-%d").date()
    except ValueError:
        return []
    earliest = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")

    out = []
    for pid, entry in digest_seen.items():
        if not entry.get("shown", True):
            continue  # pending（被 top-N 挤掉）— 不算 useful
        shown_date = entry.get("shown_date") or entry.get("date", "")
        if shown_date < earliest:
            continue
        out.append({
            "id": pid,
            "title": entry.get("title", ""),
            "keywords": entry.get("keywords", []),
            "ocs_keywords": entry.get("ocs_keywords", []),
        })
    return out


def fetch_arxiv_meta(papers: list[dict]) -> dict[str, tuple[str, str]]:
    """批量查 arXiv API：{versionless_id: (first_author_last_name, year)}。

    仅为历史论文补全 golden 命名所需的 author/year。
    """
    ids = sorted({strip_version(p["id"]) for p in papers})
    meta: dict[str, tuple[str, str]] = {}
    for i in range(0, len(ids), 100):
        batch = ids[i:i + 100]
        results = query_arxiv_batch(batch)
        for pid, (author, year, _title) in results.items():
            meta[strip_version(pid)] = (author or "Unknown", year or "")
        if i + 100 < len(ids):
            time.sleep(1.5)
    return meta


def download_missing(missing: list[dict], dest: Path, today: str) -> int:
    """把缺失论文下载到 classify.py 路由的子文件夹。返回成功下载数。"""
    # 当前 digest 的论文自带 author/year；历史论文需 API 补全
    needs_meta = any(not p.get("author") for p in missing)
    meta = fetch_arxiv_meta(missing) if needs_meta else {}

    downloaded = 0
    log_entries = []
    for i, p in enumerate(missing, 1):
        if p.get("section") is None:
            # 历史论文：纯 OCS 命中 → OCS 侧；否则走 LLM 侧 + classify 的
            # 光通信词改判逻辑（历史条目无摘要片段，信号较弱）
            p["section"] = "OCS" if (p["ocs_keywords"] and not p["keywords"]) else "LLM"
        route = classify.classify_paper(
            p["title"], p.get("snippet", ""),
            p["keywords"], p["ocs_keywords"], p["section"],
        )
        dest_dir = dest / route["path"]
        dest_dir.mkdir(parents=True, exist_ok=True)

        if p.get("author"):
            author, year = p["author"], p["year"]
        else:
            author, year = meta.get(strip_version(p["id"]), ("Unknown", ""))
            if not year:
                yy = strip_version(p["id"])[:2]
                year = ("20" + yy) if yy.isdigit() else "0000"

        filename = f"{author}_{year}_{sanitize_title(p['title'])}_{p['id']}.pdf"
        print(f"[{i}/{len(missing)}] {p['id']}  →  {route['path']}/", end=" ", flush=True)

        ok = download_pdf(p["id"], dest_dir, filename=filename)
        if ok:
            downloaded += 1

        log_entries.append({
            "date": today,
            "id": p["id"],
            "title": p["title"],
            "path": route["path"],
            "evidence": route["evidence"],
            "fallback": route["fallback"],
            "downloaded": ok,
            "source": "verify-downloads",
        })

        if i < len(missing):
            time.sleep(ARXIV_DELAY)

    append_download_log(log_entries)
    return downloaded


def main():
    parser = argparse.ArgumentParser(
        description="Verify digest papers exist on disk; optionally download missing ones")
    parser.add_argument("--dest", type=Path, default=Path.home() / "Downloads" / "Paper",
                        help="Download tree root (default: ~/Downloads/Paper)")
    parser.add_argument("--digest", type=Path, default=config.OUTPUT_FILE,
                        help=f"Path to digest file (default: {config.OUTPUT_FILE})")
    parser.add_argument("--days", type=int, default=None,
                        help="Audit digest_papers.json shown history within the last N days "
                             "instead of the current digest")
    parser.add_argument("--download", action="store_true",
                        help="Download missing papers into their classified subfolders")
    args = parser.parse_args()

    today = config.bj_today_str()

    # ── 组装审计范围 ──
    if args.days is not None:
        if not config.DIGEST_STATE_FILE.exists():
            print(f"Digest state file not found: {config.DIGEST_STATE_FILE}")
            return
        digest_seen = json.loads(config.DIGEST_STATE_FILE.read_text(encoding="utf-8"))
        papers = select_recent_shown(digest_seen, args.days, today)
        source_desc = f"digest history — papers shown in the last {args.days} day(s)"
    else:
        if not args.digest.exists():
            print(f"Digest file not found: {args.digest}")
            return
        papers = extract_papers(args.digest)
        source_desc = f"{args.digest.name} (current digest)"

    if not papers:
        print("No papers to audit.")
        return

    ids, titles = index_tree(args.dest)

    print(f"Auditing {len(papers)} paper(s) — {source_desc}")
    print(f"Download tree: {args.dest}\n")

    found, missing = 0, []
    for p in papers:
        loc = find_on_disk(p["id"], p["title"], ids, titles)
        if loc is not None:
            found += 1
            print(f"  ✓ {loc}")
        else:
            missing.append(p)
            print(f"  ✗ MISSING  {p['title'][:70]}  ({p['id']})")

    print(f"\nFound {found} / {len(papers)} on disk; {len(missing)} missing.")

    if not missing:
        return

    if not args.download:
        print("Re-run with --download to fetch missing papers. (Review the list first —")
        print("a 'missing' paper may exist under a heavily renamed file that no longer")
        print("matches by ID or title.)")
        return

    print(f"\nDownloading {len(missing)} missing paper(s) …\n")
    downloaded = download_missing(missing, args.dest, today)
    print(f"\nDownloaded {downloaded} / {len(missing)} missing paper(s) to {args.dest}/")
    print(f"Routing decisions logged to {config.DOWNLOAD_LOG.name}")


if __name__ == "__main__":
    main()
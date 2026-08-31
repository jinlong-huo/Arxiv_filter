#!/usr/bin/env python3
"""
Download arXiv papers listed in daily_digest.md into per-topic subfolders.

Each paper is routed by classify.py (keyword rules in config.SUBFOLDER_RULES)
to a topic subfolder under the destination root:

    LLM/moe  LLM/memory  LLM/agents  LLM/train  LLM/eval  LLM/inference  LLM/misc
    OCS/hardware  OCS/topology  OCS/algorithms  OCS/applications
    Distributed/      (top-level; collectives / distributed-training infra)

Usage:
    python3 arxiv_digest/download_papers.py                          # downloads to ~/Downloads/Paper
    python3 arxiv_digest/download_papers.py --dest /some/folder      # custom destination
    python3 arxiv_digest/download_papers.py --dry-run                # show subfolder + evidence, no download
    python3 arxiv_digest/download_papers.py --digest path/to/digest.md  # use a custom digest file
    make download ARGS="--dry-run"                                    # via Makefile

Routing decisions (subfolder + evidence keywords) are appended to
arxiv_digest/download_log.json for review and rule tuning.
"""

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

# Ensure the project root is on sys.path so `arxiv_digest` is importable
# when this script is run directly (python3 arxiv_digest/download_papers.py
# or via run_daily.sh from inside arxiv_digest/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from arxiv_digest import classify  # noqa: E402
from arxiv_digest import config    # noqa: E402

DIGEST_FILE = config.OUTPUT_FILE
DEFAULT_DEST = Path.home() / "Downloads" / "Paper"
ARXIV_DELAY = 3.0  # seconds between downloads, be polite
MAX_TITLE_CHARS = 80   # max chars of title in filename


def sanitize_title(title: str, max_chars: int = MAX_TITLE_CHARS) -> str:
    """Convert a paper title into a safe filename fragment."""
    # remove anything not a word char, dash, or space; collapse spaces
    safe = re.sub(r"[^\w\s-]", "", title)
    safe = re.sub(r"\s+", "_", safe)
    safe = safe.strip("_")
    if len(safe) > max_chars:
        safe = safe[:max_chars].rstrip("_")
    return safe


def extract_papers(digest_path: Path) -> list[dict]:
    """Parse daily_digest.md and return a list of paper dicts:
      {id, title, author, year, section, keywords, ocs_keywords, snippet}
    section is 'LLM' (Main Digest / Carry-Over) or 'OCS' (OCS Spotlight).
    """
    if not digest_path.exists():
        print(f"Digest file not found: {digest_path}")
        return []

    content = digest_path.read_text(encoding="utf-8")

    papers = []
    seen = set()

    # Split into sections by ## headers
    sections = re.split(r"\n## (.+)\n", content)

    for i in range(1, len(sections), 2):
        section_title = sections[i].strip()
        section_body = sections[i + 1] if i + 1 < len(sections) else ""

        if "ocs" in section_title.lower() or "optical" in section_title.lower():
            section = "OCS"
        else:
            section = "LLM"

        # Split into individual papers by ### N. header
        entries = re.split(r"\n### \d+\. ", section_body)

        for entry in entries:
            link_match = re.search(r"\*\*Link:\*\*\s*(https://arxiv\.org/abs/[^\s\n]+)", entry)
            if not link_match:
                continue

            paper_id = link_match.group(1).replace("https://arxiv.org/abs/", "").strip()
            if paper_id in seen:
                continue
            seen.add(paper_id)

            title_match = re.search(r"^(.*)$", entry, re.MULTILINE)
            author_match = re.search(r"\*\*Author:\*\*\s*(.+)$", entry, re.MULTILINE)
            year_match = re.search(r"\*\*Year:\*\*\s*(.+)$", entry, re.MULTILINE)
            kw_match = re.search(r"\*\*Keywords:\*\*\s*(.+)$", entry, re.MULTILINE)
            ocs_kw_match = re.search(r"\*\*OCS Keywords:\*\*\s*(.+)$", entry, re.MULTILINE)
            snippet_match = re.search(
                r"\*\*Abstract snippet:\*\*\s*\n\n(.*?)\n\n---", entry, re.DOTALL
            )

            papers.append({
                "id": paper_id,
                "title": title_match.group(1).strip() if title_match else paper_id,
                "author": author_match.group(1).strip() if author_match else "Unknown",
                "year": year_match.group(1).strip() if year_match else "0000",
                "section": section,
                "keywords": [k.strip() for k in kw_match.group(1).split(",")] if kw_match else [],
                "ocs_keywords": [k.strip() for k in ocs_kw_match.group(1).split(",")] if ocs_kw_match else [],
                "snippet": snippet_match.group(1).strip() if snippet_match else "",
            })

    return papers


def download_pdf(paper_id: str, dest_dir: Path, filename: str | None = None) -> bool:
    """Download a single arXiv paper PDF. Returns True on success."""
    if filename is None:
        filename = f"{paper_id}.pdf"
    elif not filename.endswith(".pdf"):
        filename += ".pdf"

    dest_path = dest_dir / filename

    if dest_path.exists():
        print(f"  [skip] already exists: {filename}")
        return False

    url = f"https://arxiv.org/pdf/{paper_id}.pdf"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "QLab-Stack-paper-downloader/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            pdf_data = resp.read()
        dest_path.write_bytes(pdf_data)
        print(f"  [ok] downloaded: {filename}  ({len(pdf_data) // 1024} KB)")
        return True
    except urllib.error.HTTPError as e:
        print(f"  [fail] HTTP {e.code} for {paper_id}: {e.reason}")
        return False
    except Exception as e:
        print(f"  [fail] {paper_id}: {e}")
        return False


def append_download_log(entries: list[dict]) -> None:
    """Append routing decisions to download_log.json (skip silently on error)."""
    log = []
    if config.DOWNLOAD_LOG.exists():
        try:
            log = json.loads(config.DOWNLOAD_LOG.read_text(encoding="utf-8"))
            if not isinstance(log, list):
                log = []
        except (json.JSONDecodeError, OSError):
            log = []
    log.extend(entries)
    config.DOWNLOAD_LOG.write_text(
        json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description="Download arXiv papers from daily_digest.md")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"Destination folder (default: {DEFAULT_DEST})")
    parser.add_argument("--dry-run", action="store_true", help="Show routing decisions without downloading")
    parser.add_argument("--digest", type=Path, default=DIGEST_FILE, help=f"Path to digest file (default: {DIGEST_FILE})")
    args = parser.parse_args()

    digest_path = args.digest
    if not digest_path.exists():
        print(f"Digest file not found: {digest_path}")
        return

    papers = extract_papers(digest_path)

    if not papers:
        print("No papers found in digest.")
        return

    # Route every paper to its topic subfolder
    for p in papers:
        p["route"] = classify.classify_paper(
            p["title"], p["snippet"], p["keywords"], p["ocs_keywords"], p["section"]
        )

    print(f"\nFound {len(papers)} paper(s) in {digest_path.name}:\n")
    for i, p in enumerate(papers, 1):
        route = p["route"]
        ev = ", ".join(route["evidence"][:3]) if route["evidence"] else "no strong signal"
        print(f"  {i:02d}. [{route['path']:17s}] {p['author']}_{p['year']} | {p['title'][:80]}"
              f"{'...' if len(p['title']) > 80 else ''}")
        print(f"       {'':17s} ← {ev}")
    print()

    if args.dry_run:
        print("--dry-run enabled, no downloads made.")
        return

    args.dest.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    log_entries = []
    for i, p in enumerate(papers, 1):
        route = p["route"]
        section_dir = args.dest / route["path"]
        section_dir.mkdir(parents=True, exist_ok=True)

        # Golden naming: Author_Year_ShortTitle_arXivID.pdf
        slug = sanitize_title(p["title"])
        filename = f"{p['author']}_{p['year']}_{slug}_{p['id']}.pdf"
        print(f"[{i}/{len(papers)}] {p['id']}  →  {route['path']}/", end=" ", flush=True)

        ok = download_pdf(p["id"], section_dir, filename=filename)
        if ok:
            downloaded += 1

        log_entries.append({
            "date": config.bj_today_str(),
            "id": p["id"],
            "title": p["title"],
            "path": route["path"],
            "evidence": route["evidence"],
            "fallback": route["fallback"],
            "downloaded": ok,
        })

        if i < len(papers):
            time.sleep(ARXIV_DELAY)

    append_download_log(log_entries)

    print(f"\nDone. Downloaded {downloaded} / {len(papers)} paper(s) to {args.dest}/")
    print(f"Routing decisions logged to {config.DOWNLOAD_LOG.name}")


if __name__ == "__main__":
    main()
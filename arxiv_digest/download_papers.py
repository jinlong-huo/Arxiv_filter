#!/usr/bin/env python3
"""
Download arXiv papers listed in daily_digest.md to a target folder.

Usage:
    python3 arxiv_digest/download_papers.py                          # downloads to ~/Downloads/Paper
    python3 arxiv_digest/download_papers.py --dest /some/folder      # custom destination
    python3 arxiv_digest/download_papers.py --dry-run                # list what would be downloaded
    make download ARGS="--dry-run"                                    # via Makefile
"""

import argparse
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DIGEST_FILE = SCRIPT_DIR / "daily_digest.md"
DEFAULT_DEST = Path.home() / "Downloads" / "Paper" / "LLM"
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


def extract_paper_ids(digest_path: Path) -> list[tuple[str, str, str, str, str]]:
    """Parse daily_digest.md and return list of (paper_id, title, author, year, section)
    tuples. Section is 'LLM' or 'LLM/OCS-Infra'.
    """
    if not digest_path.exists():
        print(f"Digest file not found: {digest_path}")
        return []

    content = digest_path.read_text(encoding="utf-8")

    papers = []
    seen = set()

    # Split into sections by ## headers
    sections = re.split(r"\n## (.+)\n", content)

    current_section = "LLM"

    for i in range(1, len(sections), 2):
        section_title = sections[i].strip()
        section_body = sections[i + 1] if i + 1 < len(sections) else ""

        if "ocs" in section_title.lower() or "optical" in section_title.lower():
            current_section = "LLM/OCS-Infra"
        else:
            current_section = "LLM"

        # Split into individual papers by ### N. header
        entries = re.split(r"\n### \d+\. ", section_body)

        for entry in entries:
            link_match = re.search(r"\*\*Link:\*\*\s*(https://arxiv\.org/abs/[^\s\n]+)", entry)
            title_match = re.search(r"^(.*)$", entry, re.MULTILINE)
            author_match = re.search(r"\*\*Author:\*\*\s*(.+)$", entry, re.MULTILINE)
            year_match = re.search(r"\*\*Year:\*\*\s*(.+)$", entry, re.MULTILINE)

            if not link_match:
                continue

            link = link_match.group(1)
            paper_id = link.replace("https://arxiv.org/abs/", "").strip()

            if paper_id in seen:
                continue
            seen.add(paper_id)

            title = title_match.group(1).strip() if title_match else paper_id
            author = author_match.group(1).strip() if author_match else "Unknown"
            year = year_match.group(1).strip() if year_match else "0000"
            papers.append((paper_id, title, author, year, current_section))

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


def main():
    parser = argparse.ArgumentParser(description="Download arXiv papers from daily_digest.md")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help=f"Destination folder (default: {DEFAULT_DEST})")
    parser.add_argument("--dry-run", action="store_true", help="List papers without downloading")
    args = parser.parse_args()

    papers = extract_paper_ids(DIGEST_FILE)

    if not papers:
        print("No papers found in digest.")
        return

    print(f"\nFound {len(papers)} paper(s) in {DIGEST_FILE.name}:\n")
    for i, (pid, title, author, year, section) in enumerate(papers, 1):
        print(f"  {i:02d}. [{section:14s}] {author}_{year} | {title[:80]}{'...' if len(title) > 80 else ''}")
    print()

    if args.dry_run:
        print("--dry-run enabled, no downloads made.")
        return

    args.dest.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    for i, (pid, title, author, year, section) in enumerate(papers, 1):
        # Subfolder per section: LLM/ or LLM/OCS-Infra/
        section_dir = args.dest / section
        section_dir.mkdir(parents=True, exist_ok=True)

        # Golden naming: Author_Year_ShortTitle_arXivID.pdf
        slug = sanitize_title(title)
        filename = f"{author}_{year}_{slug}_{pid}.pdf"
        print(f"[{i}/{len(papers)}] {pid}", end=" ", flush=True)

        if download_pdf(pid, section_dir, filename=filename):
            downloaded += 1

        if i < len(papers):
            time.sleep(ARXIV_DELAY)

    print(f"\nDone. Downloaded {downloaded} / {len(papers)} paper(s) to {args.dest}/")


if __name__ == "__main__":
    main()

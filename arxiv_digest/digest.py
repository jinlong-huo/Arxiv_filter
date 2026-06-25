#!/usr/bin/env python3
"""Digest 管理：状态文件读写、Top-N 选择、Markdown 生成、已有 digest 解析。"""

import json
import re

from arxiv_digest import config
from arxiv_digest import filter as flt


# ── JSON state I/O ─────────────────────────────────────────────

def _load_json_migrated(filepath):
    """Load JSON, migrating old http://arxiv.org/abs/... keys to canonical IDs."""
    if not filepath.exists():
        return {}, False
    with open(filepath, "r") as f:
        data = json.load(f)
    migrated = False
    new_data = {}
    for key, value in data.items():
        new_key = flt.normalize_arxiv_id(key)
        if new_key != key:
            migrated = True
        new_data[new_key] = value
    return new_data, migrated


def load_seen():
    """{arxiv_id: {title, keywords, ocs_keywords}} — 所有查阅过的论文。"""
    data, migrated = _load_json_migrated(config.STATE_FILE)
    if migrated:
        save_seen(data)
    return data


def save_seen(seen):
    with open(config.STATE_FILE, "w") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)


def load_digest_seen():
    """{arxiv_id: {title, keywords, ocs_keywords, date}} — 上过 digest 的论文。"""
    data, migrated = _load_json_migrated(config.DIGEST_STATE_FILE)
    if migrated:
        save_digest_seen(data)
    return data


def save_digest_seen(digest_seen):
    with open(config.DIGEST_STATE_FILE, "w") as f:
        json.dump(digest_seen, f, indent=2, ensure_ascii=False)


def save_no_kw(papers):
    """保存零关键词论文供人工复查。"""
    out = {}
    for p in papers:
        # 用 title 做 key（无 arxiv id 可用）
        key = p["title"][:120]
        out[key] = {
            "title": p["title"],
            "link": p["link"],
            "first_author": p.get("first_author", ""),
            "year": p.get("year", ""),
            "summary": p.get("summary", ""),
        }
    no_kw_file = config.SCRIPT_DIR / "no_keyword_papers.json"
    with open(no_kw_file, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"Zero-keyword papers: {len(papers)} → saved to {no_kw_file.name}")


# ── Top-N selection ────────────────────────────────────────────

def select_top(scored_papers, max_n):
    """按 score 降序排列，截取 top-N。"""
    scored_papers.sort(key=lambda x: x["score"], reverse=True)
    total = len(scored_papers)
    return scored_papers[:max_n], total


# ── Markdown generation ────────────────────────────────────────

def generate_markdown(papers, ocs_papers, total_main, total_ocs):
    """生成 daily_digest.md。"""

    today = config.bj_today_str()

    lines = [
        f"# arXiv Daily Digest ({today})",
        "",
    ]

    # ── 主 Digest ──
    main_note = (
        f"Showing top {len(papers)} of {total_main} matched"
        if total_main > len(papers) else f"Total: {len(papers)}"
    )
    lines.extend(["## Main Digest", "", f"{main_note}", ""])

    if papers:
        for i, paper in enumerate(papers, start=1):
            lines.extend([
                f"### {i}. {paper['title']}",
                "",
                f"**Author:** {paper.get('first_author', 'N/A')}",
                "",
                f"**Year:** {paper.get('year', 'N/A')}",
                "",
                f"**Score:** {paper['score']}",
                "",
                f"**Keywords:** {', '.join(paper['matched'])}",
                "",
                f"**Link:** {paper['link']}",
                "",
                "**Abstract snippet:**",
                "",
                paper["summary"],
                "",
                "---",
                ""
            ])
    else:
        lines.extend([
            "_No papers matched the main filter today._",
            "",
            "---",
            ""
        ])

    # ── OCS Spotlight ──
    ocs_note = (
        f"Showing top {len(ocs_papers)} of {total_ocs} matched"
        if total_ocs > len(ocs_papers) else f"Total: {len(ocs_papers)}"
    )
    lines.extend(["## OCS & Optical Networking Spotlight", "", f"{ocs_note}", ""])

    if ocs_papers:
        for i, paper in enumerate(ocs_papers, start=1):
            lines.extend([
                f"### {i}. {paper['title']}",
                "",
                f"**Author:** {paper.get('first_author', 'N/A')}",
                "",
                f"**Year:** {paper.get('year', 'N/A')}",
                "",
                f"**OCS Score:** {paper['score']}",
                "",
                f"**OCS Keywords:** {', '.join(paper['matched'])}",
                "",
                f"**Link:** {paper['link']}",
                "",
                "**Abstract snippet:**",
                "",
                paper["summary"],
                "",
                "---",
                ""
            ])
    else:
        lines.extend([
            "_No OCS-related papers found today._",
            "",
            "---",
            ""
        ])

    config.OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")


# ── Console output ──────────────────────────────────────────────

def print_results(papers, ocs_papers):
    """Print selected papers directly to stdout so you see results immediately."""
    width = 80

    def _print_section(heading, papers):
        if not papers:
            print(f"\n{'─'*width}")
            print(f"  {heading}: 0 papers matched")
            print(f"{'─'*width}")
            return
        print(f"\n{'═'*width}")
        print(f"  {heading} — {len(papers)} papers")
        print(f"{'═'*width}")
        for i, p in enumerate(papers, 1):
            title = p["title"].replace("\n", " ").strip()
            score = p["score"]
            keywords = ", ".join(p.get("matched", [])[:5])
            link = p.get("link", "")
            author = p.get("first_author", "N/A")
            year = p.get("year", "N/A")
            snippet = p.get("summary", "")[:200].replace("\n", " ").strip()

            print(f"\n  {i:2d}. {title}")
            print(f"      Score: {score}  |  Author: {author}  |  Year: {year}")
            print(f"      Keywords: {keywords}")
            print(f"      Link: {link}")
            print(f"      Abstract: {snippet}...")
        print(f"\n{'─'*width}")

    _print_section("Main Digest", papers)
    _print_section("OCS & Optical Networking Spotlight", ocs_papers)


# ── Helper ─────────────────────────────────────────────────────

def _is_empty(md_text):
    """Markdown 内容是否为空 digest。"""
    return "Total: 0" in md_text or "_No papers matched the main filter today._" in md_text


# ── Parse existing digest ──────────────────────────────────────

def parse_existing():
    """从已有 daily_digest.md 解析论文列表，供 --send-only 使用。"""
    if not config.OUTPUT_FILE.exists():
        print(f"[error] {config.OUTPUT_FILE} not found — run without --send-only first.")
        return [], []

    text = config.OUTPUT_FILE.read_text(encoding="utf-8")

    def _extract(section_label):
        papers = []
        section_re = re.compile(
            r"^## " + re.escape(section_label) + r"\s*$", re.MULTILINE)
        m = section_re.search(text)
        if not m:
            return papers

        section_text = text[m.end():]
        next_section = re.search(r"^## ", section_text, re.MULTILINE)
        if next_section:
            section_text = section_text[:next_section.start()]

        paper_blocks = re.split(r"^### \d+\. ", section_text, flags=re.MULTILINE)
        for block in paper_blocks:
            title_match = re.match(r"^(.*)$", block, re.MULTILINE)
            link_match = re.search(r"^\*\*Link:\*\*\s*(https?://\S+)",
                                   block, re.MULTILINE)
            if title_match and link_match:
                papers.append({
                    "title": title_match.group(1).strip(),
                    "link": link_match.group(1).strip(),
                })
        return papers

    main_papers = _extract("Main Digest")
    ocs_papers = _extract("OCS & Optical Networking Spotlight")
    return main_papers, ocs_papers

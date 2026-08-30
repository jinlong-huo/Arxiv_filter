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
    """{arxiv_id: {title, keywords, ocs_keywords, date, shown, score}} — 上过 digest 的论文。

    shown=True  → 已实际出现在 digest 板块中，永久跳过。
    shown=False → matched 但被 top-N 挤掉（pending），可在补遗窗口内复活。
    旧格式条目无 shown 字段 → 迁移时一律视为 shown=True（保守，行为不变）。
    """
    data, migrated = _load_json_migrated(config.DIGEST_STATE_FILE)
    for value in data.values():
        if "shown" not in value:
            value["shown"] = True
            migrated = True
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


def finalize_stamps(digest_seen, selected, ocs_selected, carry_over, today):
    """Top-N 选择后调用：把实际出现在 digest 各板块的论文标记为 shown=True。

    matched 但未被任何板块选中的论文保持 shown=False（pending），
    在 config.RESURFACE_DAYS 天内仍可通过 carry-over 板块复活。
    """
    for p in list(selected) + list(ocs_selected) + list(carry_over):
        pid = flt.normalize_arxiv_id(p.get("link", ""))
        entry = digest_seen.get(pid)
        if entry is None:
            continue
        entry["shown"] = True
        entry["shown_date"] = today


# ── Markdown generation ────────────────────────────────────────

def generate_markdown(papers, ocs_papers, total_main, total_ocs, carry_over=None):
    """生成 daily_digest.md。carry_over: 高分补遗论文（含 first_seen 日期）。"""

    today = config.bj_today_str()
    carry_over = carry_over or []

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

    # ── High-Score Carry-Over（高分补遗）──
    lines.extend(["## High-Score Carry-Over (missed earlier this week)", ""])

    if carry_over:
        lines.extend([f"Total: {len(carry_over)}", ""])
        for i, paper in enumerate(carry_over, start=1):
            lines.extend([
                f"### {i}. {paper['title']}",
                "",
                f"**Author:** {paper.get('first_author', 'N/A')}",
                "",
                f"**Year:** {paper.get('year', 'N/A')}",
                "",
                f"**Score:** {paper['score']}",
                "",
                f"**First seen:** {paper.get('first_seen', 'N/A')}",
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
            "_No carry-over papers this week._",
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

def print_results(papers, ocs_papers, carry_over=None):
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
            first_seen = p.get("first_seen")

            print(f"\n  {i:2d}. {title}")
            print(f"      Score: {score}  |  Author: {author}  |  Year: {year}")
            if first_seen:
                print(f"      First seen: {first_seen} (carry-over)")
            print(f"      Keywords: {keywords}")
            print(f"      Link: {link}")
            print(f"      Abstract: {snippet}...")
        print(f"\n{'─'*width}")

    _print_section("Main Digest", papers)
    _print_section("High-Score Carry-Over", carry_over or [])
    _print_section("OCS & Optical Networking Spotlight", ocs_papers)


# ── Helper ─────────────────────────────────────────────────────

def _is_empty(md_text):
    """Markdown 内容是否为空 digest（任何板块有论文条目即非空）。"""
    return re.search(r"^### \d+\. ", md_text, re.MULTILINE) is None

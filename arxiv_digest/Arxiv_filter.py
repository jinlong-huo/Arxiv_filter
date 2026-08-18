#!/usr/bin/env python3
"""
ArXiv Daily Digest — 主入口。

Pipeline:  fetch → filter → select → digest → send
模块:       fetch    filter    digest   digest   emailer

Usage:
    python3 arxiv_digest/Arxiv_filter.py                dry-run (不发送邮件)
    python3 arxiv_digest/Arxiv_filter.py --send          fetch + filter + send
    python3 arxiv_digest/Arxiv_filter.py --send-only     仅重发已有 digest
    python3 arxiv_digest/Arxiv_filter.py --wait          遇到 429 限流时等待 5 分钟后自动重试
    python3 arxiv_digest/Arxiv_filter.py --date YYYY-MM-DD  回填指定日期的论文（如 --date 2026-07-10）
    python3 arxiv_digest/Arxiv_filter.py --from YYYY-MM-DD [--to YYYY-MM-DD]
                                                          补拉一段时间内的论文（长时间未运行后用，
                                                          如 --from 2026-07-01 --to 2026-08-17）
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so `arxiv_digest` is importable
# when this script is run directly (python3 arxiv_digest/Arxiv_filter.py).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from arxiv_digest import config
from arxiv_digest import fetch
from arxiv_digest import filter as flt
from arxiv_digest import digest
from arxiv_digest import emailer


def _arg_value(flag):
    """取 --flag 之后的参数；flag 未出现返回 None，出现但缺值抛 IndexError。"""
    if flag not in sys.argv:
        return None
    idx = sys.argv.index(flag)
    if idx + 1 >= len(sys.argv):
        raise IndexError(f"{flag} requires a value: {flag} YYYY-MM-DD")
    return sys.argv[idx + 1]


def _parse_range_args():
    """解析 --from / --to，返回 (date_from, date_to) 或 (None, None)。

    格式错误 / --from 晚于 --to 时打印错误并返回哨兵 (False, False)。
    """
    if "--from" not in sys.argv and "--to" not in sys.argv:
        return None, None

    from datetime import datetime as _dt
    try:
        range_from = _arg_value("--from")
        range_to = _arg_value("--to")
    except IndexError as ex:
        print(f"[error] {ex}")
        return False, False

    for name, val in (("--from", range_from), ("--to", range_to)):
        if val is None:
            continue
        try:
            _dt.strptime(val, "%Y-%m-%d")
        except ValueError:
            print(f"[error] Invalid date format: {val}. Use YYYY-MM-DD.")
            return False, False

    if range_from and range_to and range_from > range_to:
        print("[error] --from must be on or before --to.")
        return False, False

    print(f"[backfill] Range: {range_from or '(unbounded)'} → {range_to or 'today'}")
    if range_to:
        # digest 标题 / 台账日期以区间终点为准
        config.DATE_OVERRIDE = range_to
    return range_from, range_to


def main():
    do_send = "--send" in sys.argv
    send_only = "--send-only" in sys.argv
    do_wait = "--wait" in sys.argv

    # ── --date YYYY-MM-DD：回填指定日期的论文 ──
    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        if idx + 1 < len(sys.argv):
            date_arg = sys.argv[idx + 1]
            # Validate format
            try:
                from datetime import datetime as _dt
                _dt.strptime(date_arg, "%Y-%m-%d")
            except ValueError:
                print(f"[error] Invalid date format: {date_arg}. Use YYYY-MM-DD.")
                return
            config.DATE_OVERRIDE = date_arg
            print(f"[backfill] Targeting date: {date_arg}")
        else:
            print("[error] --date requires a value: --date YYYY-MM-DD")
            return

    # ── --from / --to：区间回填（优先于 --date 的 3 天窗口）──
    range_from, range_to = _parse_range_args()
    if range_from is False:
        return

    # ── --send-only：仅重发已有 digest ──
    if send_only:
        papers, ocs_papers = digest.parse_existing()
        if not papers and not ocs_papers:
            print("[send-only] Digest is empty — nothing to send.")
            return
        print(f"[send-only] Loaded {len(papers)} main + {len(ocs_papers)} OCS "
              f"from {config.OUTPUT_FILE}")
        emailer.send(papers, ocs_papers)
        return

    # ── Stage 1: Fetch ─────────────────────────────────────
    print("Fetching from arXiv...")
    entries_by_cat, stats = fetch.fetch_all(wait_on_429=do_wait,
                                            date_from=range_from,
                                            date_to=range_to)

    total_in_feeds = sum(s["total"] for s in stats.values())
    if total_in_feeds == 0:
        # CRITICAL: 所有分类均返回 0
        print("=" * 60)
        print("⚠️  CRITICAL: arXiv API returned 0 entries for ALL categories.")
        print("    This is almost certainly rate-limiting or an API outage.")
        print("    Digest will be empty — check logs and try again later.")
        print("=" * 60)
        if do_send:
            print("[skip] Nothing to send.")
        return

    # ── Stage 2: Filter ────────────────────────────────────
    print(f"Filtering {total_in_feeds} entries...")

    seen = digest.load_seen()
    new_seen = dict(seen)
    digest_seen = digest.load_digest_seen()
    new_digest_seen = dict(digest_seen)
    seen_this_run = set()

    selected = []
    ocs_selected = []
    no_kw_papers = []          # 零关键词论文 — 供人工复查
    near_misses = []
    total_already_seen = 0
    total_skipped = 0
    today = config.bj_today_str()

    for cat, entries in entries_by_cat:
        feed_selected = 0
        feed_ocs_selected = 0
        feed_skipped = 0
        feed_already_seen = 0

        for entry in entries:
            paper_id = flt.normalize_arxiv_id(entry.id)

            # 跨 feed 去重（同次运行内）
            if paper_id in seen_this_run:
                feed_skipped += 1
                total_skipped += 1
                continue
            seen_this_run.add(paper_id)

            # 跨天去重（之前上过 digest 的）
            if paper_id in digest_seen:
                if digest_seen[paper_id].get("date", "") != today:
                    feed_already_seen += 1
                    total_already_seen += 1
                    continue

            title = entry.title
            summary = entry.summary
            link = entry.link
            first_author, year = flt.extract_author_year(entry)

            # ── 主过滤器 ──
            score, max_single, matched = flt.score_main(title, summary)

            # ── OCS 过滤器 ──
            ocs_score, ocs_matched = flt.score_ocs(title, summary)

            # 两次过滤器均未命中 → 零关键词，单独记录供复查
            if score == 0 and ocs_score == 0:
                no_kw_papers.append({
                    "title": title, "link": link,
                    "first_author": first_author, "year": year,
                    "summary": summary[:300],
                })

            if score >= config.MIN_SCORE:
                selected.append({
                    "title": title, "score": score,
                    "max_single": max_single, "matched": matched,
                    "link": link, "first_author": first_author,
                    "year": year, "summary": summary[:400],
                })
                feed_selected += 1
            elif score >= config.MIN_SCORE - 2:
                near_misses.append({
                    "title": title, "score": score,
                    "max_single": max_single, "matched": matched,
                    "link": link,
                })

            if ocs_score >= config.OCS_MIN_SCORE:
                ocs_selected.append({
                    "title": title, "score": ocs_score,
                    "matched": ocs_matched,
                    "link": link, "first_author": first_author,
                    "year": year, "summary": summary[:400],
                })
                feed_ocs_selected += 1

            # 流水账
            new_seen[paper_id] = {
                "title": title,
                "keywords": matched[:3],
                "ocs_keywords": ocs_matched[:3],
            }

            # 台账（仅上过 digest 的）
            if score >= config.MIN_SCORE or ocs_score >= config.OCS_MIN_SCORE:
                new_digest_seen[paper_id] = {
                    "title": title,
                    "keywords": matched[:3],
                    "ocs_keywords": ocs_matched[:3],
                    "date": today,
                }

        # 更新该分类的统计
        s = stats[cat]
        s["skipped"] = feed_skipped
        s["already_seen"] = feed_already_seen
        s["selected"] = feed_selected
        s["ocs_selected"] = feed_ocs_selected

    # ── Stage 3: Select ────────────────────────────────────
    selected, total_main = digest.select_top(selected, config.MAX_PAPERS)
    ocs_selected, total_ocs = digest.select_top(ocs_selected, config.MAX_OCS_PAPERS)

    # 保存状态
    digest.save_seen(new_seen)
    digest.save_digest_seen(new_digest_seen)
    digest.save_no_kw(no_kw_papers)

    # ── 零关键词建议 ──
    if no_kw_papers:
        suggestions = flt.suggest_keywords(no_kw_papers)
        if suggestions:
            print(f"\nKeyword suggestions from zero-kw papers:")
            for bg, cnt in suggestions:
                print(f"  + \"{bg}\"  (×{cnt})")
            print()

    # ── 统计输出 ───────────────────────────────────────────
    print(f"Total entries across feeds: {total_in_feeds}")
    print(f"Duplicates skipped (this run): {total_skipped}")
    print(f"All-time papers seen: {len(new_seen)}")
    print(f"Already in previous digest: {len(digest_seen)} → "
          f"skipped {total_already_seen} today")
    print(f"Main filter matched: {total_main} → top {len(selected)}")
    print(f"OCS spotlight matched: {total_ocs} → top {len(ocs_selected)}")
    print(f"Zero-keyword papers (no filter hit): {len(no_kw_papers)}")
    new_today = total_in_feeds - total_skipped - total_already_seen
    print(f"New papers seen this run: {new_today}")
    print()

    # ── 0-match 诊断 ───────────────────────────────────────
    if total_main == 0 and total_ocs == 0:
        near_misses.sort(key=lambda x: x["score"], reverse=True)
        top_near = near_misses[:10]
        if top_near:
            print("─" * 60)
            print(f"Top {len(top_near)} near-miss papers "
                  f"(score {config.MIN_SCORE-2}–{config.MIN_SCORE-1}, "
                  f"threshold={config.MIN_SCORE}):")
            for i, p in enumerate(top_near, 1):
                print(f"  {i:2d}. [{p['score']}/{p['max_single']}] "
                      f"{p['title'][:90]}")
                if p["matched"]:
                    print(f"       matched: {', '.join(p['matched'][:5])}")
            print()
            print("If these look relevant, consider lowering MIN_SCORE "
                  "or adding keywords.")
            print("─" * 60)
            print()
        else:
            print(f"No papers scored ≥ {config.MIN_SCORE-2} — "
                  f"keyword coverage may be too narrow.")
            print()

    # ── Fallback: 结果为空但今天已有非空 digest → 保留 ──────
    if not selected and not ocs_selected:
        if config.OUTPUT_FILE.exists():
            existing = config.OUTPUT_FILE.read_text(encoding="utf-8")
            if today in existing and not digest._is_empty(existing):
                print(f"[skip] Result is empty but today's digest already "
                      f"has content — keeping existing {config.OUTPUT_FILE}")
                if do_send:
                    print("[send] Re-sending existing digest.")
                    ep, eo = digest.parse_existing()
                    emailer.send(ep, eo)
                return
        print("[skip] No papers matched and no existing digest to fall back "
              "on — nothing to send. This may be a rate-limit or API outage.")
        return

    # ── Stage 4: Digest ────────────────────────────────────
    digest.generate_markdown(selected, ocs_selected, total_main, total_ocs)
    digest.print_results(selected, ocs_selected)
    print(f"Saved to {config.OUTPUT_FILE}")

    # ── Stage 5: Send ──────────────────────────────────────
    if do_send:
        emailer.send(selected, ocs_selected)


if __name__ == "__main__":
    main()

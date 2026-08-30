#!/usr/bin/env python3
"""
ArXiv Daily Digest — 主入口。

Pipeline:  fetch → filter → select → digest
模块:       fetch    filter    digest   digest

Usage:
    python3 arxiv_digest/Arxiv_filter.py                日常运行（抓取 + 生成 digest）
    python3 arxiv_digest/Arxiv_filter.py --wait          遇到 429 限流时等待 5 分钟后自动重试
    python3 arxiv_digest/Arxiv_filter.py --date YYYY-MM-DD  回填指定日期的论文（如 --date 2026-07-10）
    python3 arxiv_digest/Arxiv_filter.py --from YYYY-MM-DD [--to YYYY-MM-DD]
                                                          补拉一段时间内的论文（长时间未运行后用，
                                                          如 --from 2026-07-01 --to 2026-08-17）
    python3 arxiv_digest/Arxiv_filter.py --ignore-seen   忽略 digest 历史重新打分
                                                          （配合 --from/--to 重评某段时期）

默认日常模式 = 最近 3 天窗口 + 自动回看窗口（防漏跑 / arXiv 延迟上架）。
matched 但被 top-N 挤掉的高分论文记入 pending，7 天内分数 ≥ 12 者
通过 "High-Score Carry-Over" 板块补遗。
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
        range_to = _arg_value(flag="--to")
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


def _carry_over_eligible(prev, score, today):
    """pending（matched 但被 top-N 挤掉）论文是否达到补遗条件：
    本次分数 ≥ RESURFACE_MIN_SCORE 且首次 matched 日期在 RESURFACE_DAYS 天内。"""
    if score < config.RESURFACE_MIN_SCORE:
        return False
    from datetime import datetime as _dt
    try:
        stamp = _dt.strptime(prev.get("date", ""), "%Y-%m-%d").date()
        now = _dt.strptime(today, "%Y-%m-%d").date()
    except ValueError:
        return False
    return 0 <= (now - stamp).days <= config.RESURFACE_DAYS


def evaluate_entries(entries_by_cat, today, seen, digest_seen, stats,
                     ignore_seen=False):
    """Filter + score + gate 全部拉取结果（纯函数式，无网络 — 可测试）。

    返回 dict:
      selected / ocs_selected / carry_over — 候选列表（top-N 之前）
      no_kw_papers / near_misses           — 诊断用
      new_seen / new_digest_seen           — 更新后的状态（matched 未选中者
                                             记为 pending: shown=False）
      counts                               — {skipped, already_seen}
      stats                                — 就地更新各分类计数
    """
    selected = []
    ocs_selected = []
    carry_over = []
    no_kw_papers = []          # 零关键词论文 — 供人工复查
    near_misses = []
    counts = {"skipped": 0, "already_seen": 0}
    seen_this_run = set()
    new_seen = dict(seen)
    new_digest_seen = dict(digest_seen)

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
                counts["skipped"] += 1
                continue
            seen_this_run.add(paper_id)

            prev = digest_seen.get(paper_id)
            # 旧格式条目无 shown 字段 → 视为已上过 digest
            prev_shown = prev is None or prev.get("shown", True)
            prev_date = prev.get("date", "") if prev else ""

            title = entry.title
            summary = entry.summary
            link = entry.link
            first_author, year = flt.extract_author_year(entry)

            # ── 主过滤器 ──
            score, max_single, matched = flt.score_main(title, summary)

            # ── OCS 过滤器 ──
            ocs_score, ocs_matched = flt.score_ocs(title, summary)

            # ── 跨天去重 / 补遗门 ──
            if not ignore_seen and prev is not None and prev_date != today:
                if prev_shown:
                    # 已实际展示过 → 永久跳过
                    feed_already_seen += 1
                    counts["already_seen"] += 1
                    continue
                # pending：高分 → carry-over 补遗；低分 → 继续跳过（保留 pending）
                if _carry_over_eligible(prev, score, today):
                    carry_over.append({
                        "title": title, "score": score,
                        "max_single": max_single, "matched": matched,
                        "link": link, "first_author": first_author,
                        "year": year, "summary": summary[:400],
                        "first_seen": prev_date,
                    })
                else:
                    feed_already_seen += 1
                    counts["already_seen"] += 1
                # 刷新 pending 记录（保留首次 matched 日期，更新分数/关键词）
                new_digest_seen[paper_id] = {
                    **prev, "title": title,
                    "keywords": matched[:3],
                    "ocs_keywords": ocs_matched[:3],
                    "score": score,
                }
                continue

            # ── 正常打分路径（新论文 / 今日已见过的重跑）──

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

            # 台账：matched 者先记为 pending（shown=False），
            # 待 top-N 选择后由 finalize_stamps 把实际展示者置 True
            if score >= config.MIN_SCORE or ocs_score >= config.OCS_MIN_SCORE:
                new_digest_seen[paper_id] = {
                    "title": title,
                    "keywords": matched[:3],
                    "ocs_keywords": ocs_matched[:3],
                    "date": today,
                    "shown": False,
                    "score": score,
                }

        # 更新该分类的统计
        s = stats[cat]
        s["skipped"] = feed_skipped
        s["already_seen"] = feed_already_seen
        s["selected"] = feed_selected
        s["ocs_selected"] = feed_ocs_selected

    return {
        "selected": selected,
        "ocs_selected": ocs_selected,
        "carry_over": carry_over,
        "no_kw_papers": no_kw_papers,
        "near_misses": near_misses,
        "new_seen": new_seen,
        "new_digest_seen": new_digest_seen,
        "counts": counts,
    }


def main():
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
        return

    # ── Stage 2: Filter ────────────────────────────────────
    print(f"Filtering {total_in_feeds} entries...")

    seen = digest.load_seen()
    digest_seen = digest.load_digest_seen()
    today = config.bj_today_str()
    ignore_seen = "--ignore-seen" in sys.argv
    if ignore_seen:
        print("[ignore-seen] Reprocessing all entries "
              "regardless of digest history.")

    result = evaluate_entries(entries_by_cat, today, seen, digest_seen,
                              stats, ignore_seen=ignore_seen)

    no_kw_papers = result["no_kw_papers"]
    near_misses = result["near_misses"]
    new_seen = result["new_seen"]
    new_digest_seen = result["new_digest_seen"]
    total_skipped = result["counts"]["skipped"]
    total_already_seen = result["counts"]["already_seen"]

    # ── Stage 3: Select ────────────────────────────────────
    selected, total_main = digest.select_top(result["selected"],
                                             config.MAX_PAPERS)
    ocs_selected, total_ocs = digest.select_top(result["ocs_selected"],
                                                config.MAX_OCS_PAPERS)
    carry_over, total_carry = digest.select_top(result["carry_over"],
                                                config.MAX_RESURFACED)

    # 实际展示者置 shown=True，其余 matched 保持 pending
    digest.finalize_stamps(new_digest_seen, selected, ocs_selected,
                           carry_over, today)

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
    print(f"Carry-over candidates (pending, score ≥ {config.RESURFACE_MIN_SCORE}): "
          f"{total_carry} → top {len(carry_over)}")
    print(f"Zero-keyword papers (no filter hit): {len(no_kw_papers)}")
    new_today = total_in_feeds - total_skipped - total_already_seen
    print(f"New papers seen this run: {new_today}")
    print()

    # ── 0-match 诊断 ───────────────────────────────────────
    if total_main == 0 and total_ocs == 0 and total_carry == 0:
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
    if not selected and not ocs_selected and not carry_over:
        if config.OUTPUT_FILE.exists():
            existing = config.OUTPUT_FILE.read_text(encoding="utf-8")
            if today in existing and not digest._is_empty(existing):
                print(f"[skip] Result is empty but today's digest already "
                      f"has content — keeping existing {config.OUTPUT_FILE}")
                return
        print("[skip] No papers matched and no existing digest to fall back "
              "on — nothing to send. This may be a rate-limit or API outage.")
        return

    # ── Stage 4: Digest ────────────────────────────────────
    digest.generate_markdown(selected, ocs_selected, total_main, total_ocs,
                             carry_over)
    digest.print_results(selected, ocs_selected, carry_over)
    print(f"Saved to {config.OUTPUT_FILE}")


if __name__ == "__main__":
    main()

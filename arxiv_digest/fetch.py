#!/usr/bin/env python3
"""ArXiv API 获取：构造查询、拉取 feed、带重试和错误分类。

每个分类独立请求（每页 max 200 results，自动翻页），请求间延时 API_DELAY 秒，
避免单次大查询触发 arXiv 限流。

支持区间回填：fetch_all(date_from=..., date_to=...) 按 RANGE_WINDOW_DAYS 天
分窗口逐窗口拉取，用于长时间未运行后补拉漏掉的论文。
"""

import random
import sys
import time
import urllib.parse
from datetime import datetime, timedelta

import feedparser

from arxiv_digest import config
from arxiv_digest import filter as flt


def _parse_date_arg(s):
    """'YYYY-MM-DD' → datetime.date。格式错误抛 ValueError。"""
    return datetime.strptime(s, "%Y-%m-%d").date()


def _iter_date_windows(d_from, d_to):
    """(from, to) 日期窗口生成器：新→旧，每个窗口最多 RANGE_WINDOW_DAYS 天（含端点）。"""
    step = timedelta(days=config.RANGE_WINDOW_DAYS - 1)
    cur_end = d_to
    while cur_end >= d_from:
        cur_start = max(d_from, cur_end - step)
        yield cur_start, cur_end
        cur_end = cur_start - timedelta(days=1)


def _build_category_url(category, date_from, date_to, start=0):
    """构造单个分类的查询 URL。

    date_from/date_to: datetime.date（闭区间）。
    start: 翻页偏移（每页 max_results = MAX_PER_CATEGORY）。
    """
    search_query = (f"cat:{category} AND submittedDate:"
                    f"[{date_from:%Y%m%d}0000 TO {date_to:%Y%m%d}2359]")
    params = {
        "search_query": search_query,
        "start": str(start),
        "max_results": str(config.MAX_PER_CATEGORY),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    qs = urllib.parse.urlencode(params)
    return f"https://export.arxiv.org/api/query?{qs}"


def _is_fatal_error(bozo_exception):
    """不可重试的错误（SSL 证书、DNS 解析）→ 重试是浪费时间。"""
    if bozo_exception is None:
        return False
    msgs = []
    ex = bozo_exception
    seen = set()
    while ex is not None and id(ex) not in seen:
        seen.add(id(ex))
        msgs.append(str(ex))
        msgs.append(type(ex).__name__)
        if hasattr(ex, 'reason') and not isinstance(ex.reason, type(ex)):
            if isinstance(ex.reason, str):
                msgs.append(ex.reason)
                break
            else:
                ex = ex.reason
        elif hasattr(ex, '__cause__') and ex.__cause__ is not None:
            ex = ex.__cause__
        else:
            break
    combined = ' '.join(msgs)
    if 'SSL' in combined or 'certificate verify failed' in combined.lower():
        return True
    if 'gaierror' in combined or 'errno 8' in combined.lower():
        return True
    return False


def _classify_status(feed):
    """返回 (status_int, bozo_exception, is_429, is_503, is_fatal)。

    统一的状态分类，供 _fetch_once 和重试逻辑使用。
    """
    bozo = getattr(feed, 'bozo_exception', None)
    status = getattr(feed, 'status', 'N/A')
    status_int = int(status) if str(status).isdigit() else 0
    is_429 = (status_int == 429)
    is_503 = (status_int == 503)
    fatal = _is_fatal_error(bozo)
    return status_int, bozo, is_429, is_503, fatal


def _feed_total(feed):
    """arXiv feed 的总命中数（opensearch_totalresults），未知时为 None。"""
    try:
        return int(feed.feed.get('opensearch_totalresults'))
    except (AttributeError, TypeError, ValueError):
        return None


def _prompt_retry(reason, wait_sec=None):
    """Ask user interactively whether to wait and retry. Returns True if retrying."""
    if wait_sec is None:
        wait_sec = 300 + random.randint(0, 60)
    mins = wait_sec // 60
    secs = wait_sec % 60

    # 非交互环境（管道 / cron）：不要卡在 input() 上
    if not sys.stdin.isatty():
        print(f"\n  {reason}")
        print("  (non-interactive — skipping retry; use --wait for auto-retry)")
        return False

    print(f"\n{'─'*55}")
    print(f"  {reason}")
    print(f"  Wait {mins}m{secs}s and retry automatically? [Y/n] ", end="", flush=True)

    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if answer in ("", "y", "yes"):
        print(f"  Waiting {wait_sec}s...", end="", flush=True)
        for remaining in range(wait_sec, 0, -1):
            time.sleep(1)
            if remaining % 30 == 0:
                print(f"\n  ({remaining // 60}m{remaining % 60:02d}s remaining)...", end="", flush=True)
        print(" retrying!")
        return True
    else:
        print("  Skipping — run again later or use --wait for auto-retry.")
        return False


USER_AGENT = "arXivDailyDigest/1.0 (mailto:rawking1621@gmail.com)"


def _is_arxiv_error_feed(feed):
    """Detect arXiv API error masquerading as HTTP 200 with a single 'Error' entry.

    arXiv's legacy API returns HTTP 200 with one Atom entry whose title is
    "Error" when the query is malformed (e.g. max_results too high, bad
    boolean syntax).  That entry has no arxiv_primary_category, so it all
    lands in 'unknown' and looks like "0 entries for ALL categories."
    """
    if not feed.entries or len(feed.entries) != 1:
        return None
    entry = feed.entries[0]
    title = getattr(entry, 'title', '').strip()
    if title.lower() == 'error':
        summary = getattr(entry, 'summary', '')
        return f"arXiv API rejected the query: {summary}" if summary else \
               "arXiv API returned 'Error' entry (no details)"
    return None


def _do_fetch(url):
    """Single fetch attempt.  Returns (feed, status_int, bozo, is_429, is_503, fatal)."""
    feed = feedparser.parse(url, agent=USER_AGENT)
    status_int, bozo, is_429, is_503, fatal = _classify_status(feed)
    return feed, status_int, bozo, is_429, is_503, fatal


def _fetch_one_page(url, wait_on_429, label):
    """Fetch a single page URL with full retry logic.

    Returns (entries, total): list of feedparser entries (empty on
    failure/skip) and arXiv's opensearch_totalresults (None if unknown).
    Prints progress using ``label`` (e.g. the category name).

    Handles: HTTP 429 (rate-limit), HTTP 503 (unavailable), parse errors,
    fatal errors (SSL/DNS), and arXiv "Error" pseudo-entries.
    """
    # ── Attempt 1 ──────────────────────────────────────────────
    feed, status_int, bozo, is_429, is_503, fatal = _do_fetch(url)

    # Debug: surface arXiv "Error" entry (malformed query masquerading as success)
    error_msg = _is_arxiv_error_feed(feed)
    if error_msg:
        print(f"\n  [{label}] ⛔ {error_msg}")
        print(f"         This is NOT a rate-limit — the query itself is invalid.")
        print(f"         URL: {url}")
        return [], None

    # Success on first attempt
    if feed.entries:
        print(f"  [{label}] ✓ {len(feed.entries)} entries")
        return feed.entries, _feed_total(feed)

    # HTTP 200 + 干净解析 + 0 条目 = 该窗口确实没有论文（如周末 / 冷门分类）
    if status_int == 200 and bozo is None:
        print(f"  [{label}] ✓ 0 entries (no papers in this window)")
        return [], _feed_total(feed)

    # ── No entries — classify the failure ─────────────────────
    if is_429:
        wait_sec = 300 + random.randint(0, 60)
        if wait_on_429:
            print(f"\n  [{label}] ⛔ HTTP 429 rate-limit. "
                  f"Waiting {wait_sec}s ({wait_sec // 60}min) then retrying...")
            time.sleep(wait_sec)
            return _fetch_one_page(url, wait_on_429=False, label=label)
        if _prompt_retry(f"[{label}] arXiv is rate-limiting this IP (HTTP 429).", wait_sec):
            return _fetch_one_page(url, wait_on_429=False, label=label)
        return [], None

    if fatal:
        print(f"  [{label}] ⛔ [FATAL] non-retryable error: {str(bozo)[:200]}")
        return [], None

    if is_503:
        retry_reason = "HTTP 503 — arXiv API is temporarily unavailable"
    elif status_int == 200 and bozo is not None:
        retry_reason = f"HTTP 200 but feed parse failed" \
                       f"{f' — {str(bozo)[:100]}' if bozo else ''}"
    else:
        retry_reason = f"HTTP {status_int} — empty response (no entries)"

    # ── Retry loop ────────────────────────────────────────────
    for attempt in range(config.MAX_RETRIES):
        base = config.RETRY_BACKOFF_503_BASE if is_503 else config.RETRY_BACKOFF_BASE
        if attempt < len(base):
            delay = base[attempt] * (0.75 + random.random() * 0.5)
        else:
            delay = base[-1] * (0.75 + random.random() * 0.5)
        print(f"  [{label}] [retry] {retry_reason[:80]} — "
              f"attempt {attempt + 2}/{config.MAX_RETRIES + 1}, "
              f"waiting {delay:.1f}s...")
        time.sleep(delay)

        feed, status_int2, bozo2, is_429_2, is_503_2, fatal2 = _do_fetch(url)

        # Check for arXiv error entry on retry too
        error_msg = _is_arxiv_error_feed(feed)
        if error_msg:
            print(f"\n  [{label}] ⛔ {error_msg}")
            print(f"         URL: {url}")
            return [], None

        if feed.entries:
            print(f"  [{label}] ✓ {len(feed.entries)} entries (after retry)")
            return feed.entries, _feed_total(feed)

        # 重试后仍 200 + 空：该窗口确实没有论文
        if status_int2 == 200 and bozo2 is None:
            print(f"  [{label}] ✓ 0 entries (no papers in this window)")
            return [], _feed_total(feed)

        if is_429_2:
            wait_sec = 300 + random.randint(0, 60)
            if wait_on_429:
                print(f"  [{label}] [RATE-LIMITED] HTTP 429 on retry — "
                      f"waiting {wait_sec}s ({wait_sec // 60}min) then retrying...")
                time.sleep(wait_sec)
                return _fetch_one_page(url, wait_on_429=False, label=label)
            if _prompt_retry(f"[{label}] HTTP 429 on retry — arXiv is rate-limiting.", wait_sec):
                return _fetch_one_page(url, wait_on_429=False, label=label)
            return [], None

        if fatal2:
            print(f"  [{label}] [FATAL] non-retryable error on retry — aborting")
            return [], None

        # Update retry_reason for next iteration
        if is_503_2:
            retry_reason = "HTTP 503 — arXiv API is temporarily unavailable"
            is_503 = True
        elif status_int2 == 200 and bozo2 is not None:
            retry_reason = "HTTP 200 but feed parse failed"
            is_503 = False
        else:
            retry_reason = f"HTTP {status_int2} — empty response"
            is_503 = False

    # ── After all retries ──
    if feed.entries:
        print(f"  [{label}] ✓ {len(feed.entries)} entries (final)")
        return feed.entries, _feed_total(feed)

    # All attempts exhausted, no data — one last prompt
    status_final = getattr(feed, 'status', 'N/A')
    print(f"\n  [{label}] ⛔ 0 entries after "
          f"{config.MAX_RETRIES + 1} attempt(s) (HTTP {status_final}).")
    if not wait_on_429:
        if _prompt_retry(f"[{label}] All {config.MAX_RETRIES + 1} attempts exhausted "
                         f"(HTTP {status_final})."):
            return _fetch_one_page(url, wait_on_429=False, label=label)
    return [], None


def _fetch_window(category, date_from, date_to, wait_on_429):
    """拉取单个分类在 [date_from, date_to] 窗口内的全部论文（自动翻页）。

    逐页请求（每页 MAX_PER_CATEGORY），直到页不满一页或达到
    opensearch_totalresults / MAX_PAGES 上限。返回 entries 列表。
    """
    label = category
    all_entries = []
    start = 0
    total = None

    for page in range(config.MAX_PAGES):
        url = _build_category_url(category, date_from, date_to, start=start)
        entries, total = _fetch_one_page(url, wait_on_429=wait_on_429, label=label)
        if not entries:
            break
        all_entries.extend(entries)
        start += len(entries)

        if (total is not None and start >= total) or len(entries) < config.MAX_PER_CATEGORY:
            break

        delay = config.API_DELAY * (0.75 + random.random() * 0.5)
        print(f"  [{label}] [next page] waiting {delay:.1f}s...")
        time.sleep(delay)
    else:
        # for 循环未被 break → 翻页上限
        if total is None or start < total:
            print(f"  [{label}] ⚠ page cap reached ({config.MAX_PAGES} pages) — "
                  f"got {len(all_entries)} entries; consider a smaller range")

    return all_entries


def fetch_all(wait_on_429=False, date_from=None, date_to=None):
    """拉取全部 CATEGORIES 的论文（每个分类独立请求），返回 (entries_by_category, stats)。

    date_from/date_to: 'YYYY-MM-DD' 字符串（闭区间）。缺省时只拉最近
    3 天（或 --date 覆盖日的前 3 天）；给定区间时按 RANGE_WINDOW_DAYS 天
    分窗口、逐窗口逐分类拉取（用于长时间未运行后的补拉）。

    结果按 arxiv id 全局去重（arXiv 跨列表：同一论文可能出现在多个分类
    的查询结果中）。

    entries_by_category: list of (category, [feedparser entries])
    stats: dict[category] = {total, skipped, already_seen, selected, ocs_selected}

    wait_on_429: if True, wait 5 min and retry when rate-limited instead of prompting.
    """
    # ── 决定拉取窗口 ────────────────────────────────────────
    if date_from or date_to:
        try:
            d_to = _parse_date_arg(date_to) if date_to else config.bj_now().date()
            d_from = _parse_date_arg(date_from) if date_from else d_to
        except ValueError:
            print("[error] 日期格式应为 YYYY-MM-DD")
            return _empty_result()
        if d_from > d_to:
            print("[error] --from 不能晚于 --to")
            return _empty_result()
        windows = list(_iter_date_windows(d_from, d_to))
    else:
        d_to = config.bj_now().date()
        d_from = d_to - timedelta(days=3)
        windows = [(d_from, d_to)]

    print(f"  Plan: {len(windows)} window(s), {d_from} → {d_to} "
          f"across {len(config.CATEGORIES)} categories")

    # Pre-fetch jitter — avoid hitting the API at predictable instants
    pre_jitter = random.uniform(1.0, config.API_DELAY)
    time.sleep(pre_jitter)

    entries_by_cat = {cat: [] for cat in config.CATEGORIES}
    seen_ids = set()   # 跨窗口 / 跨分类去重

    first_request = True
    for w_from, w_to in windows:
        print(f"\n  [window] {w_from} → {w_to}")
        for cat in config.CATEGORIES:
            # Delay between requests (skip the very first one; pre_jitter covers it)
            if not first_request:
                delay = config.API_DELAY * (0.75 + random.random() * 0.5)
                print(f"  [wait] {delay:.1f}s before next category...")
                time.sleep(delay)
            first_request = False

            win_entries = _fetch_window(cat, w_from, w_to, wait_on_429)

            for e in win_entries:
                eid = flt.normalize_arxiv_id(e.id)
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    entries_by_cat[cat].append(e)

    # ── Report ────────────────────────────────────────────────
    total_entries = sum(len(v) for v in entries_by_cat.values())
    succeeded = sum(1 for v in entries_by_cat.values() if v)
    print(f"\n  Summary: {succeeded}/{len(config.CATEGORIES)} categories returned data, "
          f"{total_entries} total entries (deduped)")
    empty_cats = [cat for cat in config.CATEGORIES if not entries_by_cat[cat]]
    if empty_cats:
        print(f"  Empty: {', '.join(empty_cats)}")

    return _build_result(entries_by_cat)


def _empty_result():
    """无数据时的空结果。"""
    return ([(cat, []) for cat in config.CATEGORIES],
            {cat: {"total": 0, "skipped": 0, "already_seen": 0,
                   "selected": 0, "ocs_selected": 0}
             for cat in config.CATEGORIES})


def _build_result(entries_by_cat):
    """将 {cat: [entries]} 转为 (entries_by_category, stats)。"""
    entries_by_category = []
    stats = {}
    for cat in config.CATEGORIES:
        cat_entries = entries_by_cat.get(cat, [])
        entries_by_category.append((cat, cat_entries))
        stats[cat] = {"total": len(cat_entries), "skipped": 0,
                       "already_seen": 0, "selected": 0, "ocs_selected": 0}
    return entries_by_category, stats

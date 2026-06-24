#!/usr/bin/env python3
"""ArXiv API 获取：构造查询、拉取 feed、带重试和错误分类。"""

import random
import time
import urllib.parse

import feedparser

from arxiv_digest import config


def _build_combined_url():
    """构造合并查询 URL — 所有分类用 OR 串成一次请求。

    arXiv API 单次最多返回 2,000 条，我们 8×200=1,600 条，一次搞定。
    """
    categories = " OR ".join(f"cat:{c}" for c in config.CATEGORIES)
    params = {
        "search_query": categories,
        "start": "0",
        "max_results": str(config.MAX_PER_CATEGORY * len(config.CATEGORIES)),
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


def _parse_primary_category(entry):
    """从 feedparser entry 提取主分类标签（如 cs.NI）。"""
    # arXiv Atom feed 的 arxiv_primary_category 字段
    primary = entry.get('arxiv_primary_category', {})
    if isinstance(primary, dict) and primary.get('term'):
        return primary['term']
    # fallback: tags 的第一个
    tags = entry.get('tags', [])
    if tags:
        tag0 = tags[0]
        if isinstance(tag0, dict) and tag0.get('term'):
            return tag0['term']
    return 'unknown'


def _group_by_category(entries):
    """将 entries 按主分类分组，返回 {category: [entries], ...}。"""
    grouped = {}
    for e in entries:
        cat = _parse_primary_category(e)
        grouped.setdefault(cat, []).append(e)
    return grouped


def fetch_all(wait_on_429=False):
    """拉取全部 CATEGORIES 的论文（单次合并查询），返回 (entries_by_category, stats)。

    entries_by_category: list of (category, [feedparser entries])
    stats: dict[category] = {total, skipped, already_seen, selected, ocs_selected}

    wait_on_429: if True, wait 5 min and retry when rate-limited instead of aborting.
    """
    # Pre-fetch jitter — avoid hitting the API at predictable instants
    pre_jitter = random.uniform(1.0, 5.0)
    time.sleep(pre_jitter)

    url = _build_combined_url()

    # arXiv API 要求 User-Agent，否则限流更严格
    print(f"  [fetch] {url[:120]}...")

    # ── Attempt 1 ──
    feed = feedparser.parse(url, agent="arXivDailyDigest/1.0 (mailto:rawking1621@gmail.com)")
    status_int, bozo, is_429, is_503, fatal = _classify_status(feed)

    # HTTP 429 → immediate rate-limit
    if is_429:
        if wait_on_429:
            wait_sec = 300 + random.randint(0, 60)
            print(f"\n⛔ arXiv is rate-limiting this IP (HTTP 429). "
                  f"Waiting {wait_sec}s ({wait_sec // 60}min) then retrying...")
            time.sleep(wait_sec)
            return fetch_all(wait_on_429=False)
        print(f"\n⛔ arXiv is rate-limiting this IP (HTTP 429). "
              f"Wait 5-10 minutes and re-run (or use --wait for auto-retry).")
        return [], _empty_stats()

    # HTTP 503 → server-side trouble, retry with long backoff
    retry_reason = None
    if is_503:
        retry_reason = f"HTTP 503 — arXiv API is temporarily unavailable"
    elif status_int == 200 and bozo is not None:
        retry_reason = f"HTTP 200 but feed parse failed" \
                       f"{f' — {str(bozo)[:100]}' if bozo else ''}"
    elif fatal:
        print(f"⛔ [FATAL] non-retryable error: {str(bozo)[:200]}")
        return [], _empty_stats()

    # ── Retry loop (503 / parse errors only; 429 bails above) ──
    for attempt in range(config.MAX_RETRIES):
        if retry_reason is None:
            break  # first attempt succeeded

        # 503 退避比解析错误退避更长
        base = config.RETRY_BACKOFF_503_BASE if is_503 else config.RETRY_BACKOFF_BASE
        if attempt < len(base):
            delay = base[attempt] * (0.75 + random.random() * 0.5)
        else:
            delay = base[-1] * (0.75 + random.random() * 0.5)
        print(f"  [retry] {retry_reason[:80]} — "
              f"attempt {attempt + 2}/{config.MAX_RETRIES + 1}, "
              f"waiting {delay:.1f}s...")
        time.sleep(delay)
        feed = feedparser.parse(url)

        # Re-check after retry
        status_int2, bozo2, is_429_2, is_503_2, fatal2 = _classify_status(feed)

        if feed.entries:
            # Success — got data
            grouped = _group_by_category(feed.entries)
            return _build_result(grouped)

        if is_429_2:
            if wait_on_429:
                wait_sec = 300 + random.randint(0, 60)
                print(f"  [RATE-LIMITED] HTTP 429 on retry — "
                      f"waiting {wait_sec}s ({wait_sec // 60}min) then retrying...")
                time.sleep(wait_sec)
                return fetch_all(wait_on_429=False)
            print(f"  [RATE-LIMITED] HTTP 429 on retry — giving up")
            return [], _empty_stats()

        if fatal2:
            print(f"  [FATAL] non-retryable error on retry — aborting")
            return [], _empty_stats()

        # Update retry_reason for next iteration
        if is_503_2:
            retry_reason = "HTTP 503 — arXiv API is temporarily unavailable"
            is_503 = True
        elif status_int2 == 200 and bozo2 is not None:
            retry_reason = f"HTTP 200 but feed parse failed"
        else:
            retry_reason = f"HTTP {status_int2} — empty response"

    # ── After all retries ──
    if feed.entries:
        grouped = _group_by_category(feed.entries)
        return _build_result(grouped)

    # All attempts exhausted, no data
    status_final = getattr(feed, 'status', 'N/A')
    print(f"\n⛔ arXiv API returned 0 entries after "
          f"{config.MAX_RETRIES + 1} attempt(s) (HTTP {status_final}).")
    if not wait_on_429:
        print("   This may be rate-limiting or an API outage.")
        print("   Try again in 5-10 minutes, or use --wait for auto-retry.")
    return [], _empty_stats()


def _empty_stats():
    """全部分类 stats 归零。"""
    return {cat: {"total": 0, "skipped": 0, "already_seen": 0,
                  "selected": 0, "ocs_selected": 0}
            for cat in config.CATEGORIES}


def _build_result(grouped):
    """将分组好的 {cat: [entries]} 转为 (entries_by_category, stats)。"""
    entries_by_cat = []
    stats = {}
    for cat in config.CATEGORIES:
        cat_entries = grouped.get(cat, [])
        entries_by_cat.append((cat, cat_entries))
        stats[cat] = {"total": len(cat_entries), "skipped": 0,
                       "already_seen": 0, "selected": 0, "ocs_selected": 0}
    return entries_by_cat, stats

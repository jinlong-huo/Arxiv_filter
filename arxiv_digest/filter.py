#!/usr/bin/env python3
"""论文过滤：文本清洗、关键词打分（主 + OCS）、作者/年份提取、ID 标准化。"""

import re

from arxiv_digest import config


def clean_text(text):
    """归一化文本：合并空白、转小写。"""
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def normalize_arxiv_id(raw_id):
    """从 oai: 或 http:// 格式中提取规范 arXiv ID（如 '2606.16943v1'）。"""
    m = re.search(r"(\d{4}\.\d{4,}(?:v\d+)?)", raw_id)
    if m:
        return m.group(1)
    return raw_id


def extract_author_year(entry):
    """从 feedparser entry 提取 (第一作者姓氏, 年份)。"""
    author = getattr(entry, "author", "") or ""
    last_name = author.split()[-1] if author else "Unknown"
    year = str(config.bj_now().year)
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        year = str(entry.published_parsed.tm_year)
    return last_name, year


def _kw_match(kw, text):
    """词边界匹配 — 'gpu' 匹配 'GPU cluster' 但不匹配 'egpu' 或 'gpun'。"""
    return bool(re.search(r'\b' + re.escape(kw) + r'\b', text))


def score_main(title, summary):
    """主关键词打分。返回 (score, max_single, matched_keywords)。"""
    text = clean_text(title + " " + summary)

    score = 0
    matched = []
    max_single = 0

    for kw, value in config.KEYWORDS.items():
        if _kw_match(kw, text):
            score += value
            matched.append((kw, value))
            if value > max_single:
                max_single = value

    for kw, value in config.NEGATIVE_KEYWORDS.items():
        if _kw_match(kw, text):
            score += value

    matched.sort(key=lambda x: x[1], reverse=True)
    matched_keywords = [kw for kw, _ in matched]

    return score, max_single, matched_keywords


def score_ocs(title, summary):
    """OCS / 光交换 关键词打分。返回 (score, matched_keywords)。"""
    text = clean_text(title + " " + summary)

    score = 0
    matched = []

    for kw, value in config.OCS_KEYWORDS.items():
        if _kw_match(kw, text):
            score += value
            matched.append((kw, value))

    for kw, value in config.OCS_NEGATIVE_KEYWORDS.items():
        if _kw_match(kw, text):
            score += value

    matched.sort(key=lambda x: x[1], reverse=True)
    matched_keywords = [kw for kw, _ in matched]

    return score, matched_keywords


# ── 零关键词建议 ───────────────────────────────────────────────

STOP_WORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "to", "for",
    "with", "by", "from", "at", "as", "is", "it", "its", "be",
    "are", "was", "were", "been", "not", "no", "but", "this",
    "that", "these", "those", "can", "will", "may", "has", "have",
    "had", "all", "each", "every", "both", "few", "more", "most",
    "other", "some", "such", "only", "over", "into", "up", "out",
    "if", "then", "so", "than", "too", "very", "just", "about",
    "also", "new", "use", "used", "using", "one", "two", "via",
    "based", "towards", "toward", "we", "you", "he", "she", "they",
    "do", "does", "did", "which", "what", "when", "where", "who",
    "how", "after", "before", "between", "under", "above", "below",
    "through", "during", "while", "though", "although", "without",
    "within", "along", "among", "across", "around", "beyond",
    "enable", "enables", "enabled", "enabling", "well",
}


def suggest_keywords(no_kw_papers, top_n=15):
    """从零关键词论文标题提取高频 bigram，建议新增关键词。"""
    from collections import Counter

    bigrams = Counter()
    for p in no_kw_papers:
        words = clean_text(p["title"]).split()
        # 过滤纯标点 / 单字符 / 停用词
        words = [w.strip("(),;:!?.\"'-") for w in words]
        words = [w for w in words if len(w) > 1 and w not in STOP_WORDS]
        for i in range(len(words) - 1):
            bg = words[i] + " " + words[i + 1]
            bigrams[bg] += 1

    # 取频率 ≥ 3 的 bigram
    candidates = [(bg, cnt) for bg, cnt in bigrams.most_common(top_n * 3) if cnt >= 3]
    return candidates[:top_n]

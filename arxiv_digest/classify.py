#!/usr/bin/env python3
"""下载子文件夹路由：把 digest 论文归入 LLM/moe、OCS/hardware 等子文件夹。

信号 = 标题 + 摘要片段 + digest 已匹配关键词（**Keywords:** / **OCS Keywords:** 行）。
规则、优先级、阈值见 config.SUBFOLDER_RULES / SUBFOLDER_PRECEDENCE / SUBFOLDER_*。

路由逻辑（确定性、纯离线）：
  1. 侧别 — OCS Spotlight 论文一律走 OCS 侧；Main / Carry-Over 论文默认
     LLM 侧，仅当文本含明确光通信词（SUBFOLDER_OCS_SIDE_WORDS）且 OCS 侧
     总分更高时改判 OCS 侧（修复 carry-over 光网络论文被误归 LLM/ 的问题）。
  2. 组内各子文件夹按规则打分（词边界匹配，同 filter.py 风格）；
     最高分 < SUBFOLDER_MIN_SCORE → fallback（LLM/misc、OCS/applications）。
  3. 与最高分差距 ≤ SUBFOLDER_TIE_WINDOW 的候选中，按 SUBFOLDER_PRECEDENCE
     （特异优先）取最先者 — MoE-serving 论文归 moe 而非 inference。
  4. "distributed" 胜出 → 顶层 Distributed/（无 LLM/ 前缀）。

测试: python3 arxiv_digest/test_classify.py   （无网络，纯离线）
"""

from arxiv_digest import config
from arxiv_digest.filter import _kw_match, clean_text


def _score_rules(rules, text, matched_kws):
    """单张规则表打分：词边界命中 title+snippet，或该词出现在 digest
    已匹配关键词行中即计分。返回 {keyword: weight} 命中表。"""
    hits = {}
    for kw, weight in rules.items():
        if _kw_match(kw, text) or kw in matched_kws:
            hits[kw] = weight
    return hits


def _score_group(group, text, matched_kws):
    """对一侧（LLM/OCS）的全部候选子文件夹打分。
    返回 ({sub: score}, {sub: [命中词，权重降序]})。"""
    scores, evidence = {}, {}
    for sub, rules in config.SUBFOLDER_RULES[group].items():
        hits = _score_rules(rules, text, matched_kws)
        scores[sub] = sum(hits.values())
        evidence[sub] = sorted(hits, key=hits.get, reverse=True)
    return scores, evidence


def _pick(scores, precedence):
    """「特异优先 + 平局窗口」选子文件夹。返回 (sub or None, best_score)；
    sub 为 None 表示最高分低于 SUBFOLDER_MIN_SCORE → fallback。"""
    best = max(scores.values(), default=0)
    if best < config.SUBFOLDER_MIN_SCORE:
        return None, best
    threshold = max(config.SUBFOLDER_MIN_SCORE, best - config.SUBFOLDER_TIE_WINDOW)
    for sub in precedence:
        if scores.get(sub, 0) >= threshold:
            return sub, scores[sub]
    # precedence 未覆盖的候选兜底（正常配置下不会走到）
    return max(scores, key=scores.get), best


def classify_paper(title, snippet, matched_kws=(), ocs_matched_kws=(), section="LLM"):
    """把一篇 digest 论文归入下载子文件夹。

    参数:
      title            — 论文标题
      snippet          — 摘要片段（digest **Abstract snippet:** 段）
      matched_kws      — **Keywords:** 行的关键词（主过滤器命中）
      ocs_matched_kws — **OCS Keywords:** 行的关键词（OCS 过滤器命中）
      section          — 论文所在板块："LLM"（Main/Carry-Over）或 "OCS"

    返回 dict:
      side      — "LLM" / "OCS"
      subfolder — 胜出子文件夹名（None = fallback）
      path      — 相对下载根目录路径，如 "LLM/moe"、"OCS/hardware"、"Distributed"
      score     — 胜出子文件夹得分
      evidence  — 命中关键词（权重降序，至多 5 个）
      fallback  — True 表示走 fallback（LLM/misc / OCS/applications）
    """
    text = clean_text(f"{title} {snippet}")
    matched = {kw.lower() for kw in matched_kws} | {kw.lower() for kw in ocs_matched_kws}

    ocs_scores, ocs_evidence = _score_group("OCS", text, matched)

    if section == "OCS":
        side = "OCS"
    else:
        llm_scores, llm_evidence = _score_group("LLM", text, matched)
        has_optical = any(w in text for w in config.SUBFOLDER_OCS_SIDE_WORDS)
        side = ("OCS" if has_optical
                and max(ocs_scores.values(), default=0) > max(llm_scores.values(), default=0)
                else "LLM")

    if side == "OCS":
        scores, evidence = ocs_scores, ocs_evidence
        precedence = config.SUBFOLDER_PRECEDENCE["OCS"]
    else:
        scores, evidence = llm_scores, llm_evidence
        precedence = config.SUBFOLDER_PRECEDENCE["LLM"]

    sub, score = _pick(scores, precedence)

    if sub is None:
        best_sub = max(scores, key=scores.get)
        return {
            "side": side,
            "subfolder": None,
            "path": config.SUBFOLDER_FALLBACK[side],
            "score": scores[best_sub],
            "evidence": evidence[best_sub][:5],
            "fallback": True,
        }

    path = "Distributed" if sub == "distributed" else f"{side}/{sub}"
    return {
        "side": side,
        "subfolder": sub,
        "path": path,
        "score": score,
        "evidence": evidence[sub][:5],
        "fallback": False,
    }
#!/usr/bin/env python3

import json
import os
import re
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from datetime import datetime, timezone, timedelta

import random
import time
import urllib.parse

import feedparser

# ── Timezone ──────────────────────────────────────────────────
# All date logic uses Beijing time (UTC+8 / Asia/Shanghai).
TZ = timezone(timedelta(hours=8))

def bj_now():
    return datetime.now(TZ)

def bj_today_str():
    return bj_now().strftime("%Y-%m-%d")

# =========================
# 配置
# =========================

# arXiv 分类（从 API 拉取，不受 RSS skipDays 限制）
CATEGORIES = [
    "cs.NI",
    "cs.DC",
    "cs.OS",
    "cs.AR",
    "cs.PF",
    "cs.AI",
    "cs.CV",
    "cs.SY",
]

# 每个分类拉取的最大论文数（按提交日期倒序）
MAX_PER_CATEGORY = 200

# API 请求间隔（秒），礼貌起见别打太快
# arXiv 对频繁请求较敏感，3s 是安全值
API_DELAY = 3.0

# 遇到空返回时的重试等待（秒）和最大重试次数
# 使用指数退避 + 随机抖动，避免与 arXiv 限流窗口谐振
RETRY_BACKOFF_BASE = [20.0, 60.0]  # 第 1/2 次重试的基础等待秒数（±25% 抖动）
MAX_RETRIES = 2

# 关键词权重
# 设计原则：高分给能明确指向 LLM 推理 / GPU 数据中心 / RDMA 网络的词；
# 通用系统词（network, memory, distributed）降为辅助分，不能单独撑过阈值。
KEYWORDS = {
    # === 核心：LLM 推理 ===
    "llm": 6,
    "large language model": 6,
    "inference": 6,
    "serving": 6,
    "kv cache": 6,
    "prefill": 5,
    "decode": 5,
    "transformer": 4,

    # === 核心：高性能网络 / 数据中心 ===
    "rdma": 6,
    "datacenter": 5,
    "congestion": 5,
    "tail latency": 5,

    # === 调度与资源 ===
    "scheduling": 5,
    "resource allocation": 5,
    "load balancing": 5,

    # === GPU / 性能 ===
    "gpu": 4,
    "throughput": 4,
    "latency": 4,

    # === 辅助：分布式 / 并行（单独分值低，避免通用论文误入）===
    "distributed": 3,
    "communication": 3,
    "pipeline": 3,
    "parallelism": 3,
    "network": 2,
    "memory": 2,
}

# 负向词（综述、教程等）
NEGATIVE_KEYWORDS = {
    "survey": -6,
    "a survey": -6,
    "tutorial": -6,
    "benchmark dataset": -6,
    "review": -4,
}

# 第一轮：宽松海选（低门槛，不漏论文）
# 参考：gpu(4)+throughput(4)=8 / distributed(3)+communication(3)+pipeline(3)=9 / network(2)+memory(2)=4
MIN_SCORE = 5

# 第二轮：按分数排序后只保留 TOP-N（控制每日阅读量）
MAX_PAPERS = 15
MAX_OCS_PAPERS = 10

# =========================
# OCS / 光交换 / 新基础设施 关键词（独立于主过滤器）
# =========================

OCS_KEYWORDS = {
    # === 光电路交换 ===
    "optical circuit switch": 6,
    "optical circuit switching": 6,
    "optical switch": 5,
    "optical switching": 5,
    "circuit switch": 5,
    "circuit switching": 6,

    # === 光互连 / 光网络 ===
    "optical interconnect": 5,
    "optical network": 5,
    "optical fabric": 5,
    "photonic interconnect": 5,
    "photonic network": 5,
    "photonic switch": 5,

    # === 可重构光拓扑 ===
    "reconfigurable optical": 5,
    "optical topology": 5,
    "reconfigurable topology": 5,
    "reconfigurable network": 5,

    # === 波分 / MEMS / 交叉连接 ===
    "wavelength division": 5,
    "wdm": 5,
    "optical mems": 5,
    "optical cross-connect": 5,
    "optical cross connect": 5,

    # === 共封装光学 / 新 infra ===
    "co-packaged optics": 5,
    "co-packaged optical": 5,
    "cpo": 6,
    "ccl": 4,
    "collective communication library": 6,
    "optical i/o": 5,
    "optical io": 4,
    "optical transceiver": 5,
    "silicon photonic": 4,

    # === 调度与光网络交叉 ===
    "optical scheduling": 6,
    "topology engineering": 6,
    "topology reconfiguration": 6,
}

OCS_NEGATIVE_KEYWORDS = {
    "survey": -6,
    "a survey": -6,
    "tutorial": -6,
    "review": -4,
}

OCS_MIN_SCORE = 5

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "seen_papers.json"         # 流水账：所有查阅过的论文
DIGEST_STATE_FILE = SCRIPT_DIR / "digest_papers.json" # 台账：上过 digest 的论文（去重用）
OUTPUT_FILE = SCRIPT_DIR / "daily_digest.md"
LOCK_FILE = SCRIPT_DIR / ".last_run_date"
LOCK_WINDOW_HOURS = 2

# =========================
# 邮件配置（可选：python Arxiv_filter.py --send）
# =========================
# 密码不要写在代码里。二选一：
#   1. 环境变量:  export ARXIV_DIGEST_EMAIL_PASSWORD="你的Gmail应用专用密码"
#   2. 本地文件:  在同目录下创建 .email_password（只包含密码一行，已在 .gitignore）
#
# 获取 Gmail 应用专用密码: https://myaccount.google.com/apppasswords

def _load_email_password():
    """从环境变量或本地文件加载密码，避免明文入 repo"""
    pw = os.environ.get("ARXIV_DIGEST_EMAIL_PASSWORD", "")
    if pw:
        return pw
    pw_file = SCRIPT_DIR / ".email_password"
    if pw_file.exists():
        return pw_file.read_text(encoding="utf-8").strip()
    return ""

EMAIL_CONFIG = {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "sender": "rawking1621@gmail.com",
    "password": _load_email_password(),
    "recipient": "rawking1621@gmail.com",
}

# =========================
# 工具函数
# =========================

def _load_json_migrated(filepath):
    """Load JSON file. Migrate old-format http://arxiv.org/abs/... keys to canonical arXiv IDs."""
    if not filepath.exists():
        return {}, False
    with open(filepath, "r") as f:
        data = json.load(f)
    migrated = False
    new_data = {}
    for key, value in data.items():
        new_key = normalize_arxiv_id(key)
        if new_key != key:
            migrated = True
        new_data[new_key] = value
    return new_data, migrated


def load_seen():
    """Return dict: {arxiv_id: {"title": ..., "keywords": [...]}}"""
    data, migrated = _load_json_migrated(STATE_FILE)
    if migrated:
        save_seen(data)
    return data


def save_seen(seen):
    with open(STATE_FILE, "w") as f:
        json.dump(seen, f, indent=2, ensure_ascii=False)


def load_digest_seen():
    """Return dict of papers that have appeared in a previous digest (for dedup)."""
    data, migrated = _load_json_migrated(DIGEST_STATE_FILE)
    if migrated:
        save_digest_seen(data)
    return data


def save_digest_seen(digest_seen):
    with open(DIGEST_STATE_FILE, "w") as f:
        json.dump(digest_seen, f, indent=2, ensure_ascii=False)


def check_lock():
    """Return True if this run should be skipped (already ran recently today)."""
    if not LOCK_FILE.exists():
        return False
    try:
        content = LOCK_FILE.read_text().strip()
        lock_date = content.split()[0]
        today = bj_today_str()
        if lock_date != today:
            return False
        mtime = LOCK_FILE.stat().st_mtime
        age_seconds = time.time() - mtime
        return age_seconds < LOCK_WINDOW_HOURS * 3600
    except Exception:
        return False


def write_lock():
    """Record that a run completed today (Beijing time)."""
    LOCK_FILE.write_text(bj_now().strftime("%Y-%m-%d %H:%M:%S"))


def normalize_arxiv_id(raw_id):
    """Extract canonical arXiv ID (e.g. '2606.16943v1') from oai: or http:// formats."""
    m = re.search(r"(\d{4}\.\d{4,}(?:v\d+)?)", raw_id)
    if m:
        return m.group(1)
    return raw_id


def clean_text(text):
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def extract_author_year(entry):
    """Extract (first_author_last_name, year) from a feedparser entry."""
    author = getattr(entry, "author", "") or ""
    last_name = author.split()[-1] if author else "Unknown"
    year = str(bj_now().year)
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        year = str(entry.published_parsed.tm_year)
    return last_name, year


def score_paper(title, summary):
    text = clean_text(title + " " + summary)

    score = 0
    matched = []
    max_single = 0  # 最高单关键词分，用于核心词门槛

    for kw, value in KEYWORDS.items():
        if kw in text:
            score += value
            matched.append((kw, value))
            if value > max_single:
                max_single = value

    for kw, value in NEGATIVE_KEYWORDS.items():
        if kw in text:
            score += value

    # sort by score descending, then return just the keyword names
    matched.sort(key=lambda x: x[1], reverse=True)
    matched_keywords = [kw for kw, _ in matched]

    return score, max_single, matched_keywords


def score_paper_ocs(title, summary):
    """独立 OCS 评分，不与主关键词混算"""
    text = clean_text(title + " " + summary)

    score = 0
    matched = []

    for kw, value in OCS_KEYWORDS.items():
        if kw in text:
            score += value
            matched.append((kw, value))

    for kw, value in OCS_NEGATIVE_KEYWORDS.items():
        if kw in text:
            score += value

    matched.sort(key=lambda x: x[1], reverse=True)
    matched_keywords = [kw for kw, _ in matched]

    return score, matched_keywords


def send_email(papers, ocs_papers):
    """发送邮件：仅包含标题 + arXiv 链接"""
    config = EMAIL_CONFIG

    if not config["password"]:
        print("[email] 未配置密码，跳过发送。请编辑 EMAIL_CONFIG。")
        return False

    today = bj_today_str()

    def format_paper_list(paper_list, title_label):
        if not paper_list:
            return f"{title_label}: 0 papers\n"
        lines = [f"{title_label} ({len(paper_list)} papers):", ""]
        for i, p in enumerate(paper_list, 1):
            lines.append(f"  {i}. {p['title']}")
            lines.append(f"     {p['link']}")
            lines.append("")
        return "\n".join(lines)

    plain_text = (
        f"arXiv Daily Digest ({today})\n"
        f"{'=' * 50}\n\n"
        f"{format_paper_list(papers, 'Main Digest')}\n"
        f"{format_paper_list(ocs_papers, 'OCS & Optical Networking Spotlight')}\n"
        f"---\n"
        f"详细评分见 daily_digest.md 或运行 Arxiv_filter.py\n"
    )

    def format_html_list(paper_list, title_label):
        if not paper_list:
            return f"<h3>{title_label}: 0 papers</h3>"
        items = "".join(
            f'<li><a href="{p["link"]}">{p["title"]}</a></li>'
            for p in paper_list
        )
        return f"<h3>{title_label} ({len(paper_list)} papers)</h3><ol>{items}</ol>"

    html_body = (
        f"<html><body>"
        f"<h2>arXiv Daily Digest ({today})</h2>"
        f"{format_html_list(papers, 'Main Digest')}"
        f"{format_html_list(ocs_papers, 'OCS & Optical Networking Spotlight')}"
        f"<hr><p style='color:#888;font-size:12px;'>"
        f"详细评分见 daily_digest.md</p>"
        f"</body></html>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"arXiv Daily Digest ({today}) — {len(papers)}m + {len(ocs_papers)}o"
    msg["From"] = config["sender"]
    msg["To"] = config["recipient"]

    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        server = smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=30)
        server.starttls()
        server.login(config["sender"], config["password"])
        server.sendmail(config["sender"], [config["recipient"]], msg.as_string())
        server.quit()
        print(f"[email] 已发送到 {config['recipient']}")
        return True
    except Exception as e:
        print(f"[email] 发送失败: {e}")
        return False


# =========================
# 主逻辑
# =========================

def _build_api_url(category, max_results):
    """构造 arXiv API 查询 URL（Atom 格式，feedparser 可直接解析）"""
    query = f"cat:{category}"
    params = {
        "search_query": query,
        "start": "0",
        "max_results": str(max_results),
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    qs = urllib.parse.urlencode(params)
    return f"https://export.arxiv.org/api/query?{qs}"


def fetch_papers():

    seen = load_seen()                 # 流水账：所有查阅过的论文（纯记录）
    new_seen = dict(seen)              # 本次运行累加
    digest_seen = load_digest_seen()   # 台账：上过 digest 的论文（去重用）
    new_digest_seen = dict(digest_seen)
    seen_this_run = set()              # 本次运行已处理的 paper_id，避免同次运行内重复打分

    selected = []
    ocs_selected = []
    total_already_seen = 0       # 之前 digest 里出现过的论文数
    stats = {}  # category -> {total, skipped, already_seen, selected, ocs_selected}

    for i, cat in enumerate(CATEGORIES):

        # 礼貌延迟，避免触发 arXiv 速率限制
        if i > 0:
            time.sleep(API_DELAY + random.uniform(0, 2))

        url = _build_api_url(cat, MAX_PER_CATEGORY)
        feed_total = 0
        feed_skipped = 0
        feed_already_seen = 0
        feed_selected = 0
        feed_ocs_selected = 0

        # 带重试的获取：arXiv 偶发返回空 feed（通常是限流），等一等再试
        feed = feedparser.parse(url)
        for attempt in range(MAX_RETRIES):
            if feed.entries:
                break
            delay = RETRY_BACKOFF_BASE[attempt] * (0.75 + random.random() * 0.5)
            print(f"  [retry] {cat}: got 0 entries (attempt {attempt+1}/{MAX_RETRIES}), "
                  f"waiting {delay:.1f}s...")
            time.sleep(delay)
            feed = feedparser.parse(url)

        if not feed.entries:
            print(f"  [WARNING] {cat}: 0 entries after {MAX_RETRIES+1} attempts — "
                  f"likely rate-limited or API down")
            stats[cat] = {
                "total": 0, "skipped": 0, "already_seen": 0, "selected": 0, "ocs_selected": 0,
            }
            continue

        for entry in feed.entries:

            paper_id = normalize_arxiv_id(entry.id)
            feed_total += 1

            # 仅跳过同一次运行内已经处理过的（跨 feed 重复）
            if paper_id in seen_this_run:
                feed_skipped += 1
                continue

            seen_this_run.add(paper_id)

            # 跳过之前 digest 里已经出现过的论文，保证每天都是新的
            if paper_id in digest_seen:
                feed_already_seen += 1
                total_already_seen += 1
                continue

            title = entry.title
            summary = entry.summary
            link = entry.link
            first_author, year = extract_author_year(entry)

            # —— 主过滤器 ——
            score, max_single, matched = score_paper(title, summary)

            if score >= MIN_SCORE:
                selected.append({
                    "title": title,
                    "score": score,
                    "max_single": max_single,
                    "matched": matched,
                    "link": link,
                    "first_author": first_author,
                    "year": year,
                    "summary": summary[:400]
                })
                feed_selected += 1

            # —— OCS 过滤器（独立） ——
            ocs_score, ocs_matched = score_paper_ocs(title, summary)

            if ocs_score >= OCS_MIN_SCORE:
                ocs_selected.append({
                    "title": title,
                    "score": ocs_score,
                    "matched": ocs_matched,
                    "link": link,
                    "first_author": first_author,
                    "year": year,
                    "summary": summary[:400]
                })
                feed_ocs_selected += 1

            # 流水账：记录所有查阅过的论文
            new_seen[paper_id] = {
                "title": title,
                "keywords": matched[:3],
                "ocs_keywords": ocs_matched[:3]
            }

            # 台账：只记上过 digest 的论文，用于明天去重
            if score >= MIN_SCORE or ocs_score >= OCS_MIN_SCORE:
                new_digest_seen[paper_id] = {
                    "title": title,
                    "keywords": matched[:3],
                    "ocs_keywords": ocs_matched[:3]
                }

        stats[cat] = {
            "total": feed_total,
            "skipped": feed_skipped,
            "already_seen": feed_already_seen,
            "selected": feed_selected,
            "ocs_selected": feed_ocs_selected,
        }

    selected.sort(
        key=lambda x: x["score"],
        reverse=True
    )
    ocs_selected.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # 截断到 TOP-N
    total_main = len(selected)
    total_ocs = len(ocs_selected)
    selected = selected[:MAX_PAPERS]
    ocs_selected = ocs_selected[:MAX_OCS_PAPERS]

    save_seen(new_seen)
    save_digest_seen(new_digest_seen)

    # 打印统计信息
    total_in_feeds = sum(s["total"] for s in stats.values())
    total_skipped = sum(s["skipped"] for s in stats.values())
    print(f"Total entries across feeds: {total_in_feeds}")
    print(f"Duplicates skipped (this run): {total_skipped}")
    print(f"All-time papers seen: {len(new_seen)}")
    print(f"Already in previous digest: {len(digest_seen)} → skipped {total_already_seen} today")
    print(f"Main filter matched: {total_main} → top {len(selected)}")
    print(f"OCS spotlight matched: {total_ocs} → top {len(ocs_selected)}")
    print(f"New papers seen this run: {total_in_feeds - total_skipped - total_already_seen}")
    print()

    # 全局异常检测：所有分类均返回 0 篇 → 极可能是限流或 API 故障
    if total_in_feeds == 0:
        print("=" * 60)
        print("⚠️  CRITICAL: arXiv API returned 0 entries for ALL categories.")
        print("    This is almost certainly rate-limiting or an API outage.")
        print("    Digest will be empty — check logs and try again later.")
        print("=" * 60)
        print()

    return selected, ocs_selected, total_main, total_ocs, total_in_feeds


def generate_markdown(papers, ocs_papers, total_main, total_ocs):

    today = bj_today_str()

    lines = [
        f"# arXiv Daily Digest ({today})",
        "",
    ]

    # ========================
    # 主 Digest
    # ========================
    main_note = (
        f"Showing top {len(papers)} of {total_main} matched"
        if total_main > len(papers) else f"Total: {len(papers)}"
    )
    lines.extend([
        "## Main Digest",
        "",
        f"{main_note}",
        ""
    ])

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

    # ========================
    # OCS / 光交换 Spotlight
    # ========================
    ocs_note = (
        f"Showing top {len(ocs_papers)} of {total_ocs} matched"
        if total_ocs > len(ocs_papers) else f"Total: {len(ocs_papers)}"
    )
    lines.extend([
        "## OCS & Optical Networking Spotlight",
        "",
        f"{ocs_note}",
        ""
    ])

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

    OUTPUT_FILE.write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


def _digest_is_empty(md_text):
    """Return True if the digest markdown contains no papers."""
    # Empty main section means no papers (either "Total: 0" or "_No papers matched_")
    return "Total: 0" in md_text or "_No papers matched the main filter today._" in md_text


def parse_existing_digest():
    """从已有的 daily_digest.md 中解析论文列表，供 --send-only 使用。"""
    if not OUTPUT_FILE.exists():
        print(f"[error] {OUTPUT_FILE} not found — run without --send-only first.")
        return [], []

    text = OUTPUT_FILE.read_text(encoding="utf-8")

    def _extract(section_label):
        papers = []
        # 匹配 section 标题之后的内容
        section_re = re.compile(
            r"^## " + re.escape(section_label) + r"\s*$",
            re.MULTILINE
        )
        m = section_re.search(text)
        if not m:
            return papers

        section_text = text[m.end():]
        # 在下一个 ## 处截断
        next_section = re.search(r"^## ", section_text, re.MULTILINE)
        if next_section:
            section_text = section_text[:next_section.start()]

        # 解析每篇论文：### N. Title ... **Link:** url
        paper_blocks = re.split(r"^### \d+\. ", section_text, flags=re.MULTILINE)
        for block in paper_blocks:
            title_match = re.match(r"^(.*)$", block, re.MULTILINE)
            link_match = re.search(r"^\*\*Link:\*\*\s*(https?://\S+)", block, re.MULTILINE)
            if title_match and link_match:
                papers.append({
                    "title": title_match.group(1).strip(),
                    "link": link_match.group(1).strip(),
                })
        return papers

    main_papers = _extract("Main Digest")
    ocs_papers = _extract("OCS & Optical Networking Spotlight")
    return main_papers, ocs_papers


def main():

    do_send = "--send" in sys.argv
    send_only = "--send-only" in sys.argv
    force_run = "--force" in sys.argv
    is_weekend = bj_now().weekday() >= 5  # 5=Sat, 6=Sun (Beijing time)

    # --send-only: 仅发送已有 digest，不重新拉取
    if send_only:
        papers, ocs_papers = parse_existing_digest()
        if not papers and not ocs_papers:
            print("[send-only] Digest is empty — nothing to send.")
            return
        print(f"[send-only] Loaded {len(papers)} main + {len(ocs_papers)} OCS from {OUTPUT_FILE}")
        send_email(papers, ocs_papers)
        return

    # 周末 arXiv 不发布新论文，跳过以节省配额
    if is_weekend:
        print("[skip] Weekend — arXiv does not publish on Sat/Sun.")
        return

    if not force_run and check_lock():
        print(f"[skip] Already ran within the last {LOCK_WINDOW_HOURS} hours "
              f"(lock file: {LOCK_FILE})")
        print("[skip] Use --force to override, or delete .last_run_date to re-run.")
        return

    papers, ocs_papers, total_main, total_ocs, total_in_feeds = fetch_papers()

    today_tag = bj_today_str()

    # 安全保护：如果本次结果为空，且今天已有非空 digest，保留已有内容
    if not papers and not ocs_papers:
        if OUTPUT_FILE.exists():
            existing = OUTPUT_FILE.read_text(encoding="utf-8")
            if today_tag in existing and not _digest_is_empty(existing):
                print(f"[skip] Result is empty but today's digest already has content — "
                      f"keeping existing {OUTPUT_FILE}")
                if do_send:
                    print("[send] Re-sending existing digest since --send was requested.")
                    existing_papers, existing_ocs = parse_existing_digest()
                    send_email(existing_papers, existing_ocs)
                return
        # 没有任何数据可用 → 不覆盖，不发邮件
        print("[skip] No papers matched and no existing digest to fall back on — "
              "nothing to send. This may be a rate-limit or API outage.")
        return

    generate_markdown(papers, ocs_papers, total_main, total_ocs)

    # 只有 API 真正返回数据时才写锁，避免 VPN 没开 / 限流导致空跑后锁住
    if total_in_feeds > 0:
        write_lock()

    print(f"Main filter: {len(papers)} papers")
    print(f"OCS spotlight: {len(ocs_papers)} papers")
    print(f"Saved to {OUTPUT_FILE}")

    if do_send:
        send_email(papers, ocs_papers)


if __name__ == "__main__":
    main()
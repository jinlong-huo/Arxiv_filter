#!/usr/bin/env python3
"""邮件发送：纯文本 + HTML 双格式，Gmail SMTP。"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from arxiv_digest import config


def send(papers, ocs_papers):
    """发送每日 digest 邮件。返回是否成功。"""
    cfg = config.EMAIL_CONFIG

    if not cfg["password"]:
        print("[email] 未配置密码，跳过发送。请设置 ARXIV_DIGEST_EMAIL_PASSWORD 或 .email_password。")
        return False

    today = config.bj_today_str()

    # ── 纯文本 ──
    def _fmt_text(plist, label):
        if not plist:
            return f"{label}: 0 papers\n"
        lines = [f"{label} ({len(plist)} papers):", ""]
        for i, p in enumerate(plist, 1):
            lines.append(f"  {i}. {p['title']}")
            lines.append(f"     {p['link']}")
            lines.append("")
        return "\n".join(lines)

    plain = (
        f"arXiv Daily Digest ({today})\n"
        f"{'=' * 50}\n\n"
        f"{_fmt_text(papers, 'Main Digest')}\n"
        f"{_fmt_text(ocs_papers, 'OCS & Optical Networking Spotlight')}\n"
        f"---\n"
        f"详细评分见 daily_digest.md\n"
    )

    # ── HTML ──
    def _fmt_html(plist, label):
        if not plist:
            return f"<h3>{label}: 0 papers</h3>"
        items = "".join(
            f'<li><a href="{p["link"]}">{p["title"]}</a></li>'
            for p in plist
        )
        return f"<h3>{label} ({len(plist)} papers)</h3><ol>{items}</ol>"

    html = (
        f"<html><body>"
        f"<h2>arXiv Daily Digest ({today})</h2>"
        f"{_fmt_html(papers, 'Main Digest')}"
        f"{_fmt_html(ocs_papers, 'OCS & Optical Networking Spotlight')}"
        f"<hr><p style='color:#888;font-size:12px;'>"
        f"详细评分见 daily_digest.md</p>"
        f"</body></html>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"arXiv Daily Digest ({today}) — {len(papers)}m + {len(ocs_papers)}o"
    msg["From"] = cfg["sender"]
    msg["To"] = cfg["recipient"]
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        server = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30)
        server.starttls()
        server.login(cfg["sender"], cfg["password"])
        server.sendmail(cfg["sender"], [cfg["recipient"]], msg.as_string())
        server.quit()
        print(f"[email] 已发送到 {cfg['recipient']}")
        return True
    except Exception as e:
        print(f"[email] 发送失败: {e}")
        return False

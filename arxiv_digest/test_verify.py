#!/usr/bin/env python3
"""verify_downloads.py 的离线测试：文件索引、盘上匹配（ID / 标题）、历史选择窗口。

覆盖：
  - normalize_key / strip_version 纯函数
  - index_tree：ID 索引、标题索引、_archive_seismic 排除
  - find_on_disk：ID 命中、标题命中（含重命名 / 截断 / 标点）、未命中
  - select_recent_shown：shown 窗口过滤（pending 排除、shown_date 回退、过期排除）

运行: python3 arxiv_digest/test_verify.py   （或 make test；无网络，纯离线）
"""

import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from arxiv_digest import verify_downloads as vd

CHECKS = 0


def check(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    print(f"  ✓ {label}")


print("─" * 60)
print("  verify_downloads.py — audit matching golden cases")
print("─" * 60)

# ── 纯函数 ─────────────────────────────────────────────────────
check(vd.normalize_key("Don't Overthink, Don't Underthink!")
      == "dontoverthinkdontunderthink", "normalize_key strips punctuation/whitespace")
check(vd.strip_version("2608.28511v1") == "2608.28511", "strip_version removes vN suffix")
check(vd.strip_version("2608.28511") == "2608.28511", "strip_version keeps bare IDs")

# ── index_tree + find_on_disk ──────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / "LLM" / "inference").mkdir(parents=True)
    (root / "OCS" / "topology").mkdir(parents=True)
    (root / "_archive_seismic").mkdir(parents=True)

    # ID 后缀命名（download_papers 产物，rename_papers 保留）
    (root / "LLM" / "inference" /
     "Shen_2026_Characterization_of_Request_and_Token_Energy_Costs_2608.28044v1.pdf"
     ).write_bytes(b"x")
    # 纯标题命名（老式 Zotero 重命名，无 ID、标点被清洗）
    (root / "OCS" / "topology" /
     "Farrington_et_al_2010_Helios_a_hybrid_electricaloptical_switch_architecture"
     "_for_modular_data_centers.pdf").write_bytes(b"x")
    # 归档目录 — 应被忽略
    (root / "_archive_seismic" / "Ghost_2020_Ghost_Paper_2608.11111v1.pdf").write_bytes(b"x")

    ids, titles = vd.index_tree(root)

    check(len(ids) == 1 and "2608.28044" in ids, "index_tree extracts arXiv IDs (archive excluded)")
    check(len(titles) == 2, "index_tree indexes title keys of non-archived PDFs")

    loc = vd.find_on_disk(
        "2608.28044v1",
        "Characterization of Request and Token Energy Costs for LLM Inference Workloads",
        ids, titles)
    check(loc is not None and loc.as_posix().startswith("LLM/inference/"),
          "paper found by arXiv ID in filename")

    loc = vd.find_on_disk(
        "2010.12345v1",
        "Helios: a hybrid electrical/optical switch architecture for modular data centers",
        ids, titles)
    check(loc is not None and loc.as_posix().startswith("OCS/topology/"),
          "paper found by normalized-title containment (renamed, no ID)")

    check(vd.find_on_disk("2608.11111v1", "Ghost Paper", ids, titles) is None,
          "archived PDFs do not count as present")

    # 长标题截断：文件名只保留标题前 80 字符（download_papers MAX_TITLE_CHARS）
    long_title = "Scaling " + "Deep " * 25 + "Inference"
    (root / "LLM" / "inference" / f"Asad_2026_{long_title[:80]}.pdf").write_bytes(b"x")
    ids2, titles2 = vd.index_tree(root)
    loc = vd.find_on_disk("2608.12345v1", long_title, ids2, titles2)
    check(loc is not None and loc.as_posix().startswith("LLM/inference/Asad_"),
          "long title matches against 80-char truncated filename")

    check(vd.find_on_disk("2608.99999v1", "Totally Unrelated Paper Title Here", ids2, titles2) is None,
          "unknown paper reported missing")

# ── select_recent_shown ───────────────────────────────────────
digest_seen = {
    "2608.11111v1": {"title": "A", "keywords": ["gpu"], "ocs_keywords": [],
                     "shown": True, "shown_date": "2026-08-31"},
    # legacy 条目无 shown_date → 回退 date
    "2608.22222v1": {"title": "B", "keywords": [], "ocs_keywords": ["optical"],
                     "shown": True, "date": "2026-08-28"},
    # pending（被 top-N 挤掉）→ 不算 useful，排除
    "2608.33333v1": {"title": "C", "keywords": ["gpu"], "ocs_keywords": [],
                     "shown": False, "date": "2026-08-30"},
    # 超出窗口 → 排除
    "2608.44444v1": {"title": "D", "keywords": ["gpu"], "ocs_keywords": [],
                     "shown": True, "shown_date": "2026-08-01"},
}

papers = vd.select_recent_shown(digest_seen, days=7, today="2026-08-31")
got_ids = {p["id"] for p in papers}
check(got_ids == {"2608.11111v1", "2608.22222v1"},
      "window selects shown papers only (pending + stale excluded)")
check({"id", "title", "keywords", "ocs_keywords"} <= set(papers[0]),
      "selected entries carry keywords for classification")

check(vd.select_recent_shown(digest_seen, days=1, today="2026-08-31")
      and all(p["id"] == "2608.11111v1" for p in
              vd.select_recent_shown(digest_seen, days=1, today="2026-08-31")),
      "--days 1 selects only today's shown papers")

print("─" * 60)
print(f"  All {CHECKS} checks passed.")
print("─" * 60)
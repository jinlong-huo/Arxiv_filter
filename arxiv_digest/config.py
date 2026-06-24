#!/usr/bin/env python3
"""ArXiv Daily Digest — 全部配置常量、路径、时区工具。"""

import os
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ── SSL cert fix for macOS + conda Python ──────────────────────
try:
    import ssl
    import certifi
    _cafile = certifi.where()
    ssl._create_default_https_context = lambda cafile=_cafile: ssl.create_default_context(cafile=cafile)
    os.environ.setdefault('SSL_CERT_FILE', _cafile)
except ImportError:
    pass

# ── Timezone (Beijing) ─────────────────────────────────────────
TZ = timezone(timedelta(hours=8))

def bj_now():
    return datetime.now(TZ)

def bj_today_str():
    return bj_now().strftime("%Y-%m-%d")

# ── arXiv API ──────────────────────────────────────────────────

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

MAX_PER_CATEGORY = 200
API_DELAY = 5.0
RETRY_BACKOFF_BASE = [20.0, 60.0]       # 解析错误退避
RETRY_BACKOFF_503_BASE = [60.0, 120.0]  # 503 服务端故障退避（更长，尊重 arXiv 恢复时间）
MAX_RETRIES = 2

# ── 主关键词权重 ────────────────────────────────────────────────

KEYWORDS = {
    # LLM 推理
    "llm": 6,
    "large language model": 6,
    "inference": 6,
    "serving": 6,
    "kv cache": 6,
    "prefill": 5,
    "decode": 5,
    "transformer": 4,
    # MoE / 稀疏专家
    "mixture of experts": 6,
    "mixture-of-experts": 6,
    "sparse mixture": 5,
    "expert parallelism": 5,
    "expert routing": 5,
    "moe": 5,
    "moe model": 5,
    "moe layer": 5,
    # 基础 ML（低权重 catch-all）
    "machine learning": 2,
    "deep learning": 2,
    "neural network": 2,
    "training": 3,
    "fine-tuning": 3,
    "agentic": 4,
    "ai agent": 4,
    # 高性能网络 / 数据中心
    "rdma": 6,
    "datacenter": 5,
    "data center": 5,
    "congestion": 5,
    "tail latency": 5,
    # 调度与资源
    "scheduling": 5,
    "resource allocation": 5,
    "load balancing": 5,
    # GPU / 性能
    "gpu": 4,
    "throughput": 4,
    "latency": 4,
    # 分布式 / 并行（辅助分）
    "distributed": 3,
    "communication": 3,
    "pipeline": 3,
    "parallelism": 3,
    "network": 2,
    "memory": 2,
    # GPU 集合通信库
    "collective communication": 6,
    "nccl": 5,
    "rccl": 5,
    "all-reduce": 5,
    "allreduce": 5,
    "gpu direct": 5,
    "gpudirect": 5,
}

NEGATIVE_KEYWORDS = {
    "a survey": -6,
    "survey paper": -6,
    "comprehensive survey": -8,
    "this survey": -6,
    "tutorial": -6,
    "benchmark dataset": -6,
}

MIN_SCORE = 5
MAX_PAPERS = 15

# ── OCS / 光交换 关键词（独立过滤器）────────────────────────────

OCS_KEYWORDS = {
    # 光电路交换 (core OCS)
    "optical circuit switch": 7,
    "optical circuit switching": 7,
    "optical switch": 5,
    "optical switching": 5,
    "photonic switch": 6,
    "photonic switching": 5,
    # 光互连 / 光网络
    "optical interconnect": 5,
    "optical network": 5,
    "optical fabric": 5,
    "photonic interconnect": 5,
    "photonic network": 5,
    "optical data center": 5,
    "optical datacenter": 5,
    "all-optical network": 5,
    "all optical network": 5,
    # 可重构光拓扑
    "reconfigurable optical": 5,
    "optical topology": 5,
    "reconfigurable topology": 5,
    "reconfigurable network": 5,
    "topology engineering": 7,
    "topology reconfiguration": 7,
    "optical reconfiguration": 5,
    # 波分 / MEMS / 交叉连接 / ROADM
    "wavelength division multiplex": 5,
    "optical mems": 5,
    "optical cross-connect": 5,
    "optical cross connect": 5,
    "reconfigurable optical add-drop": 5,
    "roadm": 3,
    # 共封装光学 / 硅光
    "co-packaged optics": 6,
    "co-packaged optical": 6,
    "co packaged optics": 6,
    "optical transceiver": 5,
    "silicon photonic": 4,
    "silicon photonics": 4,
    # 光 I/O
    "optical i/o": 5,
    "optical io": 4,
    # 弹性光网络 / 空分复用
    "elastic optical network": 5,
    "flexgrid": 5,
    "flex grid": 5,
    "space division multiplex": 5,
    "multi-core fiber": 5,
    "multicore fiber": 5,
    "hollow-core fiber": 5,
    "hollow core fiber": 5,
    "anti-resonant fiber": 5,
    # AI/ML 驱动的光网络
    "optical network optimization": 6,
    "optical network automation": 5,
    "digital twin optical": 6,
    "machine learning optical network": 6,
    "reinforcement learning optical": 6,
    "llm optical network": 5,
    "optical network planning": 5,
    # 光网络调度
    "optical scheduling": 6,
    "optical resource allocation": 5,
}

OCS_NEGATIVE_KEYWORDS = {
    "a survey": -6,
    "survey paper": -6,
    "comprehensive survey": -8,
    "this survey": -6,
    "tutorial": -6,
    # CV / medical 噪声
    "optical flow": -8,
    "optical coherence tomography": -8,
    "optical character recognition": -8,
    "optical sensor": -6,
    "fiber sensor": -6,
    "optical camera": -6,
    "optical image": -6,
}

OCS_MIN_SCORE = 5
MAX_OCS_PAPERS = 10

# ── 路径 ───────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "seen_papers.json"
DIGEST_STATE_FILE = SCRIPT_DIR / "digest_papers.json"
OUTPUT_FILE = SCRIPT_DIR / "daily_digest.md"

# ── 邮件（密码通过环境变量或 .email_password 文件加载）──────────

def _load_email_password():
    # 1) env var
    pw = os.environ.get("ARXIV_DIGEST_EMAIL_PASSWORD", "")
    if pw:
        return pw
    # 2) .email_password file
    pw_file = SCRIPT_DIR.parent / ".email_password"
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

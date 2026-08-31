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

# Set by --date flag for backfilling a specific day.
# When non-None, bj_now() and bj_today_str() return this date instead of "now".
DATE_OVERRIDE: str | None = None

def bj_now():
    if DATE_OVERRIDE is not None:
        d = datetime.strptime(DATE_OVERRIDE, "%Y-%m-%d")
        return d.replace(hour=23, minute=59, second=59, tzinfo=TZ)
    return datetime.now(TZ)

def bj_today_str():
    if DATE_OVERRIDE is not None:
        return DATE_OVERRIDE
    return bj_now().strftime("%Y-%m-%d")

# ── arXiv API ──────────────────────────────────────────────────

CATEGORIES = [
    "cs.NI",
    "cs.DC",
    "cs.AR",
    "cs.PF",
    "cs.AI",
    "physics.optics",   # device/hardware OCS papers live here, not cross-listed to cs.*
    "physics.app-ph",   # applied physics — optical device / photonic integration
    "eess.SP",          # signal processing — optical comms / fiber transmission
]

MAX_PER_CATEGORY = 200
API_DELAY = 5.0
RETRY_BACKOFF_BASE = [20.0, 60.0]       # 解析错误退避
RETRY_BACKOFF_503_BASE = [60.0, 120.0]  # 503 服务端故障退避（更长，尊重 arXiv 恢复时间）
MAX_RETRIES = 2
MAX_429_RETRIES = 3                     # --wait 模式下每页 429 冷却重试上限（每次 ~5min）

# ── 区间回填（--from / --to）───────────────────────────────────
RANGE_WINDOW_DAYS = 3   # 每个拉取窗口的天数（含端点），逐窗口分页拉取
MAX_PAGES = 10          # 每个窗口每分类最多翻页数（2000 篇上限保护）

# ── 自动回看窗口（默认日常模式专用，防漏跑）─────────────────────
# 日常运行除最近 3 天窗口外，自动再拉一个回看窗口，覆盖 arXiv 延迟上架
# 和忘记运行的日子。--date / --from / --to 模式下不启用。
LOOKBACK_FROM_DAYS_AGO = 8   # 回看窗口起点（今天前 N 天）
LOOKBACK_TO_DAYS_AGO = 4     # 回看窗口终点（紧接日常 3 天窗口）

# ── 高分补遗（carry-over）──────────────────────────────────────
# matched 但被 top-N 挤掉的论文记为 pending（shown:false），
# RESURFACE_DAYS 天内若再次被拉到且分数 ≥ RESURFACE_MIN_SCORE，
# 进入 "High-Score Carry-Over" 板块，展示后才永久跳过。
RESURFACE_DAYS = 7        # pending 论文可补遗的天数窗口
RESURFACE_MIN_SCORE = 12  # 补遗分数线（高于日常 MIN_SCORE）
MAX_RESURFACED = 5        # 每日补遗展示上限

# ── 主关键词权重 ────────────────────────────────────────────────

KEYWORDS = {
    # LLM 推理
    "llm": 6,
    "large language model": 5,
    "inference": 7,
    "serving": 7,
    "kv cache": 7,
    "prefill": 7,
    "decode": 7,
    "transformer": 4,
    # MoE / 稀疏专家
    "mixture of experts": 7,
    "mixture-of-experts": 7,
    "sparse mixture": 5,
    "expert parallelism": 5,
    "expert routing": 5,
    "moe": 7,
    "moe model": 5,
    "moe layer": 5,
    # 基础 ML（低权重 catch-all）
    "machine learning": 2,
    "deep learning": 2,
    "neural network": 2,
    "training": 3,
    "fine-tuning": 3,
    "agentic": 7,
    "ai agent": 7,
    # 高性能网络 / 数据中心
    "rdma": 6,
    "datacenter": 7,
    "data center": 7,
    "congestion": 5,
    "tail latency": 5,
    # 调度与资源
    "scheduling": 7,
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
    "network": 7,
    "memory": 2,
    # LLM serving 系统 / 推理优化（高优先级 — CacheRoute 类）
    "prefix caching": 8,
    "prefix cache": 7,
    "prefix-aware": 7,
    "prefix affinity": 8,
    "prefix-affinity": 8,
    "kv cache reuse": 7,
    "cache-aware routing": 8,
    "request routing": 6,
    "model routing": 6,
    "llm router": 6,
    "disaggregated serving": 8,
    "disaggregation": 6,
    "pd disaggregation": 7,
    "prefill-decode": 7,
    "continuous batching": 7,
    "speculative decoding": 7,
    "goodput": 6,
    "time to first token": 7,
    "ttft": 6,
    "tpot": 6,
    "time per output token": 6,
    # GPU 集合通信库
    "collective communication": 7,
    "nccl": 5,
    "rccl": 5,
    "all-reduce": 5,
    "allreduce": 5,
    "gpu direct": 5,
    "gpudirect": 5,
}

NEGATIVE_KEYWORDS = {
    "a survey": -2,
    "survey paper": -2,
    "comprehensive survey": -2,
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
    # OCS 器件 / 波长选择开关
    "wavelength selective switch": 6,
    "wavelength-selective switch": 6,
    "arrayed waveguide grating": 5,
    "arrayed-waveguide grating": 5,
    "micro-ring resonator": 5,
    "microring resonator": 5,
    "microresonator": 4,
    "optical resonator": 3,
    "mach-zehnder modulator": 5,
    "mach zehnder modulator": 5,
    "mach-zehnder interferometer": 4,
    "optical frequency comb": 4,
    "frequency comb": 3,
    "sagnac": 3,
    # 光交换范式
    "optical burst switching": 6,
    "optical packet switching": 6,
    "optical label switching": 5,
    "optical flow switching": 5,
    "hybrid optical-electrical": 5,
    "optical-electrical switch": 5,
    "optoelectronic switch": 5,
    "free-space optical": 4,
    "free space optical": 4,
    "visible light communication": 3,
    # 数据中心光网络
    "optical data center network": 6,
    "optical datacenter network": 6,
    "optical dcn": 5,
    "intra-datacenter optical": 5,
    "intra-data center optical": 5,
    "intra-datacenter optical": 5,
    # 波分复用 / 网格
    "dense wavelength division": 5,
    "wdm": 3,
    "optical grid": 4,
    "flex-rate": 4,
    "flex rate": 4,
    # 光网络 AI/ML 控制面
    "optical network control": 5,
    "software-defined optical": 5,
    "sdn optical": 4,
    "optical network security": 5,
    "optical network survivability": 5,
    "optical network resilience": 5,
    "quality of transmission": 4,
    "optical performance monitoring": 5,
    # 量子 / 光网络融合
    "quantum optical network": 4,
    "entanglement distribution optical": 5,
    # CPO / 共封装光学生态（含 LPO / NPO / 光引擎）
    "co-packaged": 5,
    "linear pluggable optics": 6,
    "linear-pluggable optics": 6,
    "linear receive optics": 5,
    "near-packaged optics": 5,
    "optical engine": 5,
    "external laser source": 5,
    "elsfp": 5,
    "optical io chiplet": 5,
    "optical i/o chiplet": 5,
    "chiplet optical": 4,
    "fiber attach unit": 4,
}

# 需光通信上下文才生效的缩写（防 "Naive Prompt Optimization" 类误匹配）
OCS_CONTEXT_KEYWORDS = {
    "cpo": 5,
    "lpo": 4,
    "npo": 4,
}
OCS_CONTEXT_REQUIRED = (
    "optical", "optic", "photonic", "transceiver", "packag",
    "interconnect", "fiber", "datacenter", "data center", "switch",
)

OCS_NEGATIVE_KEYWORDS = {
    "a survey": -6,
    "survey paper": -6,
    "comprehensive survey": -8,
    "this survey": -6,
    "tutorial": -6,
    # CV / medical 噪声（完全无关，保留重罚）
    "optical flow": -8,
    "optical coherence tomography": -8,
    "optical character recognition": -8,
    # 传感 / 成像（降低权重 — 光网络论文偶尔会提及，不应一票否决）
    "optical sensor": -4,
    "fiber sensor": -4,
    "optical camera": -4,
    "optical image": -4,
}

OCS_MIN_SCORE = 3
MAX_OCS_PAPERS = 10

# 需 LLM serving 上下文才生效的缩写（防 "SLO 图优化" 类误匹配）
MAIN_CONTEXT_KEYWORDS = {
    "slo": 5,
}
MAIN_CONTEXT_REQUIRED = (
    "serving", "inference", "llm", "latency", "throughput",
    "goodput", "gpu", "token", "request",
)

# ── 下载子文件夹路由（classify.py / download_papers.py）─────────
# download_papers.py 按标题+摘要片段+digest 已匹配关键词为每篇论文选子文件夹。
# 打分：词边界匹配（同 filter.py），或该词出现在 **Keywords:** 行中即计分。
#   1. 最高分 < SUBFOLDER_MIN_SCORE → fallback（LLM/misc、OCS/applications）
#   2. 与最高分差距 ≤ SUBFOLDER_TIE_WINDOW 的候选中，按 SUBFOLDER_PRECEDENCE
#      （特异优先）取最先者 — MoE-serving 论文归 moe 而非 inference
#   3. "distributed" 胜出 → 顶层 Distributed/（无 LLM/ 前缀）
#   4. OCS Spotlight 论文一律走 OCS 侧；Main/Carry-Over 论文仅当文本含
#      SUBFOLDER_OCS_SIDE_WORDS 中任一词且 OCS 侧得分更高时改判 OCS 侧
#      （修复 carry-over 光网络论文被误归 LLM/ 的问题）
# 路由决策（子文件夹+证据）追加到 download_log.json 供复查与调参。

SUBFOLDER_MIN_SCORE = 5     # 低于此分 → fallback 子文件夹
SUBFOLDER_TIE_WINDOW = 6    # 特异优先裁决的平局窗口

SUBFOLDER_OCS_SIDE_WORDS = (
    "optical", "photonic", "photonics", "fiber", "fibre",
    "wavelength", "roadm", "mems", "transceiver", "silicon photonic",
)

SUBFOLDER_PRECEDENCE = {
    "LLM": ["moe", "memory", "agents", "distributed", "train", "eval", "inference"],
    "OCS": ["hardware", "topology", "algorithms"],
}

SUBFOLDER_FALLBACK = {"LLM": "LLM/misc", "OCS": "OCS/applications"}

SUBFOLDER_RULES = {
    "LLM": {
        # MoE / 稀疏专家（最特异，优先级最高）
        "moe": {
            "mixture of experts": 7,
            "mixture-of-experts": 7,
            "moe": 7,
            "sparse mixture": 5,
            "sparse expert": 5,
            "expert parallelism": 6,
            "expert routing": 6,
            "expert placement": 5,
            "expert offloading": 5,
            "expert locality": 5,
            "expert capacity": 4,
            "expert": 4,
            "experts": 4,
            "gating network": 3,
            "router": 3,
        },
        # KV cache / 内存管理
        "memory": {
            "kv cache": 7,
            "kv-cache": 7,
            "pagedattention": 7,
            "paged attention": 7,
            "kv cache reuse": 7,
            "prefix caching": 6,
            "prefix cache": 6,
            "kv compression": 5,
            "memory management": 6,
            "memory pool": 6,
            "memory pooling": 6,
            "memory hierarchy": 5,
            "offloading": 5,
            "long-term memory": 5,
            "agent memory": 5,
            "memory": 3,
            "cache": 3,
        },
        # Agentic AI / 智能体
        "agents": {
            "agentic": 7,
            "ai agent": 7,
            "llm agent": 7,
            "multi-agent": 6,
            "multiagent": 6,
            "web agent": 6,
            "tool call": 5,
            "tool calling": 5,
            "tool use": 5,
            "orchestration": 4,
            "trajectory": 3,
            "reflexion": 4,
            "agent": 5,
            "agents": 5,
        },
        # 分布式训练 / 集合通信 → 顶层 Distributed/
        "distributed": {
            "collective communication": 7,
            "nccl": 6,
            "rccl": 6,
            "all-reduce": 6,
            "allreduce": 6,
            "all-gather": 5,
            "allgather": 5,
            "reduce-scatter": 5,
            "collective": 5,
            "rdma": 6,
            "roce": 5,
            "infiniband": 5,
            "congestion control": 5,
            "ecn": 4,
            "distributed training": 6,
            "data parallelism": 5,
            "tensor parallelism": 4,
            "pipeline parallelism": 4,
            "parallelism": 3,
            "cluster scheduling": 5,
            "job scheduling": 5,
            "communication optimization": 4,
            "communication scheduling": 5,
            "kernel bypass": 4,
            "gpudirect": 5,
            "gpu direct": 5,
            "dpdk": 4,
        },
        # 训练 / 微调
        "train": {
            "fine-tuning": 6,
            "fine tuning": 6,
            "fine-tune": 6,
            "finetuning": 6,
            "lora": 5,
            "pre-training": 5,
            "pretraining": 5,
            "training": 4,
            "train": 3,
            "rlhf": 4,
            "dpo": 4,
            "sft": 4,
            "distillation": 5,
            "knowledge distillation": 6,
            "data curation": 4,
            "data mixing": 4,
            "curriculum": 3,
            "gradient": 3,
            "checkpoint": 4,
            "optimizer": 3,
            "scaling law": 4,
        },
        # 评测 / 基准
        "eval": {
            "benchmark": 6,
            "evaluation": 5,
            "evaluate": 5,
            "eval": 5,
            "leaderboard": 5,
            "llm-as-a-judge": 6,
            "llm as a judge": 6,
            "reward model": 5,
            "human evaluation": 5,
            "contamination": 4,
            "metric": 4,
            "test suite": 4,
            "probing": 3,
        },
        # 推理 / serving（最泛化，优先级最低；低权重 catch-all 保证不落 misc）
        "inference": {
            "inference": 6,
            "serving": 6,
            "llm serving": 7,
            "inference serving": 7,
            "decode": 4,
            "decoding": 5,
            "prefill": 6,
            "continuous batching": 7,
            "speculative decoding": 7,
            "disaggregated serving": 8,
            "pd disaggregation": 7,
            "prefill-decode": 6,
            "ttft": 5,
            "time to first token": 6,
            "tpot": 5,
            "batching": 4,
            "scheduling": 4,
            "load balancing": 4,
            "request routing": 4,
            "model routing": 4,
            "goodput": 5,
            "throughput": 4,
            "latency": 4,
            "quantization": 4,
            "attention": 3,
            "kernel": 3,
            "gpu": 3,
            "transformer": 3,
            "token": 3,
            "llm": 3,
            "large language model": 3,
        },
    },
    "OCS": {
        # 器件 / 物理：硅光、MEMS、WSS、调制器、光纤传输实验
        "hardware": {
            "silicon photonics": 6,
            "silicon photonic": 6,
            "photonic integrated circuit": 6,
            "integrated photonics": 5,
            "photonic chip": 5,
            "photonic chiplet": 6,
            "mems": 5,
            "optical mems": 6,
            "wavelength selective switch": 6,
            "wavelength-selective switch": 6,
            "micro-ring resonator": 6,
            "microring resonator": 6,
            "microresonator": 5,
            "mach-zehnder": 5,
            "modulator": 5,
            "vcsel": 6,
            "semiconductor optical amplifier": 6,
            "external laser source": 5,
            "laser": 3,
            "waveguide": 5,
            "co-packaged optics": 5,
            "co-packaged": 5,
            "optical engine": 5,
            "transceiver": 4,
            "photodetector": 5,
            "hollow-core fiber": 5,
            "hollow core fiber": 5,
            "optical fiber": 4,
            "fiber transmission": 5,
            "fiber": 3,
            "transmission": 4,
            "optical frequency comb": 5,
            "frequency comb": 4,
            "injection locking": 4,
            "beam steering": 4,
            "epitaxial": 4,
            "iii-v": 4,
            "fabrication": 3,
            "experimental demonstration": 4,
            "insertion loss": 4,
            "crosstalk": 4,
            "photonic": 3,
            "optical": 2,
        },
        # 拓扑 / 架构：OCS fabric、可重构 DCN、光电混合交换
        "topology": {
            "optical circuit switch": 7,
            "optical circuit switching": 7,
            "ocs": 6,
            "topology engineering": 6,
            "topology reconfiguration": 6,
            "reconfigurable topology": 6,
            "optical topology": 6,
            "reconfigurable optical": 5,
            "reconfigurable data center": 6,
            "reconfigurable network": 5,
            "hybrid electrical-optical": 5,
            "hybrid optical-electrical": 5,
            "optical switch": 5,
            "optical switching": 5,
            "photonic switch": 6,
            "optical fabric": 5,
            "switch architecture": 5,
            "network topology": 5,
            "data center network": 4,
            "optical interconnect": 4,
            "photonic interconnect": 4,
            "scale-up": 4,
            "interconnect": 3,
            "topology": 5,
            "reconfigurable": 4,
            "fabric": 3,
        },
        # 算法 / 控制：路由、调度、RL 优化、QoT、数字孪生
        "algorithms": {
            "routing": 5,
            "routing and wavelength": 6,
            "traffic engineering": 5,
            "traffic grooming": 6,
            "spectrum allocation": 5,
            "resource allocation": 4,
            "scheduling": 4,
            "optimization": 4,
            "reinforcement learning": 5,
            "machine learning": 4,
            "graph neural": 4,
            "birkhoff": 6,
            "matching": 4,
            "algorithm": 4,
            "heuristic": 3,
            "digital twin": 5,
            "control plane": 4,
            "sdn": 4,
            "software-defined": 4,
            "quality of transmission": 5,
            "provisioning": 4,
            "survivability": 4,
            "monitoring": 3,
            "planning": 3,
        },
        # applications = fallback（OCS/applications），无规则
    },
}

# ── 路径 ───────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "seen_papers.json"
DIGEST_STATE_FILE = SCRIPT_DIR / "digest_papers.json"
OUTPUT_FILE = SCRIPT_DIR / "daily_digest.md"
DOWNLOAD_LOG = SCRIPT_DIR / "download_log.json"

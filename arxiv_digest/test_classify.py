#!/usr/bin/env python3
"""classify.py 子文件夹路由的黄金用例测试（纯离线，无网络）。

覆盖：
  - 特异优先裁决 — MoE-serving / KV-cache 论文归特异子文件夹而非 inference
  - Distributed 顶层路由（集合通信 / 分布式训练基础设施）
  - fallback — 无强信号论文归 LLM/misc、OCS/applications
  - carry-over 光网络论文改判 OCS 侧（板块标为 LLM 但内容是光网络）
  - OCS 组内 hardware / topology / algorithms 三路
  - 规则表与优先级表的一致性（每个候选子文件夹都有优先级位）

运行: python3 arxiv_digest/test_classify.py   （或 make test）
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from arxiv_digest import classify
from arxiv_digest import config

CHECKS = 0


def check(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(f"FAIL: {label}")
    print(f"  ✓ {label}")


def check_route(title, snippet, expected_path, section="LLM",
                matched_kws=(), ocs_kws=(), label=""):
    v = classify.classify_paper(title, snippet, matched_kws, ocs_kws, section)
    check(v["path"] == expected_path,
          f"{label or title[:44]!r} → {expected_path} (got {v['path']})")
    return v


print("─" * 60)
print("  classify.py — subfolder routing golden cases")
print("─" * 60)

# ── 规则表 / 优先级表一致性 ────────────────────────────────────
for side, subs in config.SUBFOLDER_PRECEDENCE.items():
    check(set(subs) == set(config.SUBFOLDER_RULES[side]),
          f"precedence covers every {side} candidate subfolder")
for side in ("LLM", "OCS"):
    check(side in config.SUBFOLDER_FALLBACK, f"fallback defined for {side} side")

# ── LLM 侧 ─────────────────────────────────────────────────────

# 特异优先：MoE-serving 论文归 moe 而非 inference（inference 词汇同样密集）
v = check_route(
    "ELDR: Expert-Locality-Aware Decode Routing for PD-Disaggregated MoE Serving",
    "We route decode requests to experts with locality awareness, cutting "
    "expert parallelism communication in pd disaggregation serving systems.",
    "LLM/moe",
    label="MoE-serving paper",
)
check("moe" in v["evidence"], "MoE routing evidence includes 'moe'")
check(v["fallback"] is False, "MoE routing is not fallback")

# KV cache / 内存管理
check_route(
    "Efficient Memory Management for Large Language Model Serving with PagedAttention",
    "We manage the kv cache in fixed-size blocks, reducing fragmentation and "
    "enabling flexible memory sharing across requests.",
    "LLM/memory",
    label="KV-cache / memory paper",
)

# Agentic AI
check_route(
    "Dont Overthink, Dont Underthink: Toward Adaptive Reasoning in Agentic AI",
    "We adaptively budget reasoning depth for ai agents, letting each agent "
    "decide when to stop thinking in multi-agent workflows.",
    "LLM/agents",
    label="agentic paper",
)

# 集合通信 / 分布式训练 → 顶层 Distributed/
v = check_route(
    "Optimizing All-Reduce for Distributed Training on GPU Clusters",
    "We accelerate collective communication, tuning nccl algorithms for "
    "allreduce over rdma networks in large-scale training clusters.",
    "Distributed",
    label="collectives paper",
)
check(v["subfolder"] == "distributed", "Distributed routing keeps subfolder name")

# 训练 / 微调（含 memory 一词但信号弱 → 不被 memory 抢走）
check_route(
    "How Small Can You Go: LoRA Rank, Target Modules and Quantization",
    "A controlled study of lora rank and target module choice when "
    "fine-tuning small language models under memory constraints.",
    "LLM/train",
    label="fine-tuning paper",
)

# 评测 / 基准
check_route(
    "CONDA: Detecting Data Contamination in LLM Benchmark Evaluation",
    "We propose a metric to detect benchmark contamination, giving a "
    "reliable evaluation of what test data models have seen.",
    "LLM/eval",
    label="benchmark paper",
)

# 泛化 serving 论文 → inference（无更特异信号）
check_route(
    "Sangam: Efficiently Serving Diffusion LLMs with the AR Stack",
    "We improve serving throughput for diffusion large language model "
    "inference, interleaving denoising and decoding steps.",
    "LLM/inference",
    label="generic serving paper",
)

# 无强信号 → LLM/misc
v = check_route(
    "A Survey of Network Function Virtualization in Carrier Networks",
    "We review virtual network functions deployed by carriers.",
    "LLM/misc",
    label="no-signal paper",
)
check(v["fallback"] is True, "no-signal routing flags fallback")

# ── OCS 侧 ─────────────────────────────────────────────────────

# 器件 / 硅光
check_route(
    "Monolithic Silicon Photonics Platform with Microring Resonator-Based Optical Switch",
    "We fabricate a photonic integrated circuit with microring resonator "
    "switches, measuring low insertion loss and crosstalk.",
    "OCS/hardware",
    section="OCS",
    label="silicon photonics device paper",
)

# 拓扑 / 架构
check_route(
    "Helios: A Hybrid Electrical-Optical Switch Architecture for Modular Data Centers",
    "A hybrid electrical-optical circuit switch (ocs) combining optical "
    "circuit switching with electrical packet switching, reconfiguring the "
    "network topology per traffic demand.",
    "OCS/topology",
    section="OCS",
    label="OCS fabric architecture paper",
)

# 算法 / 控制（RL 路由）
check_route(
    "RL-Based Routing and Spectrum Allocation in Elastic Optical Networks",
    "Reinforcement learning for joint routing and spectrum allocation, "
    "optimizing resource allocation under transmission constraints.",
    "OCS/algorithms",
    section="OCS",
    label="optical routing algorithm paper",
)

# OCS 板块无强信号 → OCS/applications
check_route(
    "Optical Coherence Tomography Image Super-Resolution",
    "We enhance oct images of the retina.",
    "OCS/applications",
    section="OCS",
    label="weak-signal OCS paper",
)

# ── Carry-Over 改判 ────────────────────────────────────────────
# 板块是 LLM（Main/Carry-Over），但内容是光网络 → 应改判 OCS 侧
check_route(
    "Topology Engineering for GPU Cluster Fabrics with Optical Circuit Switch",
    "We engineer data center network topologies using an optical circuit "
    "switch, reconfiguring the fabric to match traffic matrices.",
    "OCS/topology",
    section="LLM",
    matched_kws=("gpu", "network"),
    label="carry-over optical paper (LLM section)",
)

# 对照：LLM 板块的普通集群调度论文（含 topology/scheduling 词但无光通信词）
# → 不得被误判到 OCS 侧
check_route(
    "Topology-Aware GPU Cluster Scheduling for Training Workloads",
    "We place training jobs on gpu clusters respecting the network "
    "topology, improving cluster scheduling efficiency.",
    "Distributed",
    section="LLM",
    matched_kws=("gpu", "scheduling"),
    label="topology-aware cluster paper stays LLM-side",
)

print("─" * 60)
print(f"  All {CHECKS} checks passed.")
print("─" * 60)
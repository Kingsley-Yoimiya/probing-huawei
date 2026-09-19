#!/usr/bin/env python3
"""D51 Wait DAG V1: schema constants and stable node/edge identifiers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "wait_dag_v1"
GRAPH_GENERATION_ID = "d51_v2b_rank0_active_generation_v1"
EXPECTED_DB_SHA256 = "7d57eb2a62ed79ba35706f40e29622ebe723f99697c5c5d524c685c64220c6fa"
FROZEN_ACTIVE_WINDOW = (1787496728400464831, 1787496731923567250)
FROZEN_RANK0_PID = 26025
DENOMINATORS = {"record": 51, "wait": 24, "allreduce": 12}
A6_WAIT_CALL_SEQUENCES = frozenset(
    {176, 177, 178, 179, 221, 222, 223, 224, 266, 267, 268, 269}
)
A6_RECORD_STREAM = 94341995468752
A6_WAIT_STREAM = 94341958677056

EVIDENCE_TIERS = frozenset(
    {"proven_event_generation", "observed_structural", "unknown"}
)
IDENTITY_SOURCES = frozenset(
    {
        "event_generation",
        "profiler_same_stream_fifo",
        "a5_comm_completion_structure",
        "host_thread_order",
        "unclassified",
        "a2_preload_cann",
        "cann_task_projection",
    }
)
SEMANTIC_CLASSES = frozenset(
    {
        "comm_completion_to_compute",
        "compute_ready_to_comm_candidate",
        "same_stream_event_wait",
        "cross_stream_event_wait",
        "unclassified",
    }
)
EDGE_TYPES = frozenset(
    {
        "event_generation",
        "profiler_same_stream_fifo",
        "a5_comm_completion_structure",
        "host_thread_order",
        "unknown_dependency",
    }
)


def record_node_id(pid: int, raw_event: int, lifetime: int, reset: int, record: int) -> str:
    return f"event_record:{pid}:{raw_event}:{lifetime}:{reset}:{record}"


def wait_node_id(pid: int, call_sequence: int) -> str:
    return f"event_wait:{pid}:{call_sequence}"


def task_node_id(rowid: int) -> str:
    return f"task:{rowid}"


def comm_node_id(op_name: str) -> str:
    return f"comm:{op_name}"


def host_sync_node_id(rowid: int) -> str:
    return f"host_sync:{rowid}"


def unknown_node_id(reason_code: str, anchor_node: str, ordinal: int) -> str:
    safe_anchor = anchor_node.replace(":", "_")
    return f"unknown:{reason_code}:{safe_anchor}:{ordinal}"


def record_key_tuple_str(pid: int, raw_event: int, lifetime: int, reset: int, record: int) -> str:
    return f"({pid},{raw_event},{lifetime},{reset},{record})"


@dataclass
class DagNode:
    node_id: str
    node_type: str
    primary_key: str
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class IdentityLink:
    link_id: str
    src_node: str
    dst_node: str
    link_type: str
    evidence_tier: str
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class CausalEdge:
    edge_id: str
    src: str
    dst: str
    edge_type: str
    direction: str = "forward"
    evidence_tier: str = "unknown"
    identity_source: str = "unclassified"
    semantic_class: str = "unclassified"
    evidence_refs: list[str] = field(default_factory=list)
    rank_scope: str = "rank0"
    stream_domain: str = ""
    cone_mode: str = "active"
    reason_code: str = ""


@dataclass
class UnknownEntry:
    unknown_id: str
    anchor_node: str
    missing_edge_type: str
    missing_peer_role: str
    reason_code: str
    observed_evidence: str
    required_evidence: str
    blocks_strict_cone: bool
    blocks_observed_cone: bool
    slice_b_can_resolve: bool


def build_allowed_claims(coverage: dict[str, Any]) -> list[str]:
    """按 coverage.json 动态生成允许 claims；零 FIFO 边时禁止宣称「可重放」。"""
    fifo = coverage.get("profiler_same_stream_fifo", {})
    fifo_obs = int(fifo.get("observed_structural", 0) or 0)
    fifo_unk = int(fifo.get("unknown", 0) or 0)
    base = (
        "在冻结 V2b rank0 active 窗内，51 个 Event Record 代次、24 个 Wait "
        "及 active profiler TASK 已进入同一可审计图；24/24 Event 代次边"
    )
    if fifo_obs > 0:
        fifo_clause = f"和 profiler 同流观察序可重放（{fifo_obs} 条 FIFO 边）"
    else:
        fifo_clause = (
            f"；active 窗内可排序邻接对因时间重叠标 fifo_order_ambiguous"
            f"（{fifo_unk} 条 stream），零条 FIFO 边"
            f"（Plan 要求歧义不任取，故不造 FIFO 边）"
        )
    tail = (
        "；未覆盖的 Notify、Synchronize、跨 rank、缺 TASK 与语义分类"
        "以 unknown 显式保留。"
    )
    return [base + fifo_clause + tail]


ALLOWED_CLAIMS: list[str] = []

FORBIDDEN_CLAIMS = [
    "已获得 16-rank 全图",
    "Notify 已配对",
    "跨 rank 已闭合",
    "FIFO 有文档保证",
    "已能 delay",
    "延长 kernel 后真实时间线已经移动",
    "性能收益已证明",
]

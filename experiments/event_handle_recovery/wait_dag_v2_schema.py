#!/usr/bin/env python3
"""D51 Wait DAG V2: schema constants and pair-level FIFO claims."""
from __future__ import annotations

from typing import Any

from wait_dag_schema import (  # noqa: F401
    A6_RECORD_STREAM,
    A6_WAIT_CALL_SEQUENCES,
    A6_WAIT_STREAM,
    DENOMINATORS,
    EXPECTED_DB_SHA256,
    FORBIDDEN_CLAIMS,
    FROZEN_ACTIVE_WINDOW,
    FROZEN_RANK0_PID,
    comm_node_id,
    host_sync_node_id,
    record_key_tuple_str,
    record_node_id,
    task_node_id,
    unknown_node_id,
    wait_node_id,
    CausalEdge,
    DagNode,
    IdentityLink,
    UnknownEntry,
)

SCHEMA_VERSION = "wait_dag_v2"
GRAPH_GENERATION_ID = "d51_v2b_rank0_active_generation_v2"
ANALYZER_VERSION = "wait_dag_v2_frozen_v2b"

NON_A6_WAIT_CS = frozenset({144, 153, 162, 171, 189, 198, 207, 216, 234, 243, 252, 261})


def build_allowed_claims(coverage: dict[str, Any], candidate_count: int = 0) -> list[str]:
    fifo = coverage.get("profiler_same_stream_fifo", {})
    fifo_obs = int(fifo.get("observed_structural", 0) or 0)
    overlap_unk = int(fifo.get("overlap_unknown_pairs", 0) or 0)
    malformed_unk = int(fifo.get("malformed_unknown_pairs", 0) or 0)
    base = (
        "在冻结 V2b rank0 active 窗内，24/24 Event 代次边保持不变；"
        "同 profiler stream 的相邻 TASK 已按非重叠区间逐对恢复 observed FIFO"
    )
    fifo_clause = (
        f"（{fifo_obs} 条 FIFO 边；重叠对 unknown={overlap_unk}、"
        f"非法对 unknown={malformed_unk}，逐对审计不整流清零）"
    )
    rev = (
        f"；另外 12 条非 A6 Wait 已按 C1–C8 离散充分谓词逐条分类"
        f"（compute_ready_to_comm_candidate={candidate_count}），"
        f"未通过者保持 unclassified"
    )
    tail = (
        "；Notify、Synchronize、跨 rank、缺 TASK 与未闭合 cone 仍以 unknown 显式保留。"
        "FIFO 为 observed_structural/adjacent_nonoverlap，非平台文档保证；未做 delay。"
    )
    return [base + fifo_clause + rev + tail]

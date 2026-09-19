#!/usr/bin/env python3
"""D51 Wait DAG V1: human-readable casebook and schema docs."""
from __future__ import annotations

from typing import Any

from wait_dag_schema import ALLOWED_CLAIMS, FORBIDDEN_CLAIMS, GRAPH_GENERATION_ID, build_allowed_claims


def build_wait_dag_casebook(
    nodes: dict,
    causal_edges: list,
    unknowns: list,
    wait_classification_rows: list[dict],
    record_without_wait_rows: list[dict],
    comm_results: list,
    acceptance: dict,
) -> str:
    lines = [
        "# D51 Wait DAG V1 Casebook\n",
        f"**图代：** `{GRAPH_GENERATION_ID}`\n",
        f"**验收：** `passed={acceptance.get('passed')}`\n",
    ]

    lines.append("## 24 Wait 逐条\n")
    lines.append("| cs | semantic | reason | record_stream | wait_stream | A6 | blockers |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in sorted(wait_classification_rows, key=lambda x: x["wait_call_sequence"]):
        lines.append(
            f"| {row['wait_call_sequence']} | {row['semantic_class']} | "
            f"{row['classification_reason']} | {row.get('record_raw_stream')} | "
            f"{row.get('wait_raw_stream')} | {row.get('a6_member')} | "
            f"{row.get('blockers', '')} |"
        )

    lines.append("\n## 12 条 A6 重放\n")
    a6_rows = [r for r in wait_classification_rows if r.get("a6_member")]
    lines.append(f"cs 集合：`{sorted(r['wait_call_sequence'] for r in a6_rows)}`\n")
    for cr in sorted(comm_results, key=lambda x: x.comm_op_name):
        lines.append(
            f"- **{cr.comm_op_name}**: cs={cr.preload_wait_call_sequence}, "
            f"A6={'PASS' if cr.a6_pass else 'FAIL'}, "
            f"event_wait_task_rowid={cr.event_wait_task_rowid}, "
            f"cann_cid={cr.cann_wait_connection_id}"
        )

    lines.append("\n## 另外 12 条分类\n")
    other = [r for r in wait_classification_rows if not r.get("a6_member")]
    dist: dict[str, int] = {}
    for r in other:
        dist[r["semantic_class"]] = dist.get(r["semantic_class"], 0) + 1
    lines.append(f"分布：{dist}\n")
    for row in sorted(other, key=lambda x: x["wait_call_sequence"]):
        note = ""
        if row.get("classification_reason", "").startswith("a6_stream_polarity"):
            note = "（与 A6 流对调观察，非 candidate）"
        elif "a6_stream_polarity" in str(row.get("blockers", "")):
            note = "（与 A6 流对调观察，非 candidate）"
        lines.append(
            f"- cs={row['wait_call_sequence']}: {row['semantic_class']} "
            f"({row['classification_reason']}) blockers={row.get('blockers', '')}{note}"
        )

    lines.append("\n## record_without_wait（逐键）\n")
    if not record_without_wait_rows:
        lines.append("无（全部 Record 均被至少一条 Wait 消费）\n")
    else:
        for row in record_without_wait_rows:
            lines.append(
                f"- `{row['record_key']}` cs={row.get('call_sequence')} "
                f"stream={row.get('raw_stream')}"
            )

    lines.append("\n## cid 5559/5560 缺 TASK 链\n")
    for cr in comm_results:
        if cr.preload_wait_call_sequence in (221, 222):
            lines.append(
                f"- cs={cr.preload_wait_call_sequence}, cid={cr.cann_wait_connection_id}, "
                f"event_wait_task_rowid={cr.event_wait_task_rowid} "
                f"（Event 边存在，下游 TASK 投影 unknown）"
            )

    lines.append("\n## Unknown 摘要\n")
    by_reason: dict[str, int] = {}
    for u in unknowns:
        rc = u.reason_code if hasattr(u, "reason_code") else u.get("reason_code")
        by_reason[rc] = by_reason.get(rc, 0) + 1
    for rc, n in sorted(by_reason.items()):
        lines.append(f"- `{rc}`: {n}")

    lines.append("\n## 依赖锥传播说明\n")
    lines.append(
        "- **计算 kernel 影响：** kernel → 同流 FIFO → Record TASK/代次 → "
        "event_generation → Wait → 下游 FIFO；缺 TASK 投影或 unknown 处停止。\n"
        "- **通信影响：** 通信 TASK/COMM → A5 terminal→Record → event_generation → "
        "compute Wait → 下游 FIFO；cid 5559/5560 下游 TASK 位置 unknown。\n"
        "- **禁止：** 有图 ≠ 能 delay；observed cone ≠ 真实时长移动。\n"
    )

    # Example chains
    lines.append("\n## 示例链\n")
    eg_edges = [e for e in causal_edges if getattr(e, "edge_type", None) == "event_generation"
                or (isinstance(e, dict) and e.get("edge_type") == "event_generation")]
    if eg_edges:
        e0 = eg_edges[0]
        src = e0.src if hasattr(e0, "src") else e0.get("src")
        dst = e0.dst if hasattr(e0, "dst") else e0.get("dst")
        lines.append(f"1. Event 边样例：`{src}` → `{dst}`\n")

    gap_cr = next((cr for cr in comm_results if cr.preload_wait_call_sequence == 221), None)
    if gap_cr:
        lines.append(
            f"2. 缺 TASK A6 链：comm={gap_cr.comm_op_name}, "
            f"cs=221, cid={gap_cr.cann_wait_connection_id}, task=NULL\n"
        )

    if record_without_wait_rows:
        r0 = record_without_wait_rows[0]
        lines.append(
            f"3. record_without_wait：`{r0['record_key']}`\n"
        )
    elif other:
        o0 = other[0]
        lines.append(
            f"3. 非 A6 Wait：cs={o0['wait_call_sequence']}, "
            f"class={o0['semantic_class']}\n"
        )

    return "\n".join(lines) + "\n"


def write_schema_md(path) -> None:
    text = """# Wait DAG Schema V1

## 节点类型

- `event_record_generation`：五元 Record 代次键
- `event_wait`：`(pid, call_sequence)`
- `profiler_task`：`task rowid`
- `communication_op`：AllReduce op_name
- `host_sync_observation`：CANN Synchronize API 行
- `unknown_stub`：缺边占位

## 边类型

- `event_generation`：Record→Wait（`proven_event_generation`）
- `profiler_same_stream_fifo`：同 profiler streamId 相邻 TASK（`observed_structural`）
- `a5_comm_completion_structure`：通信 terminal→Record TASK（`observed_structural`）
- `unknown_dependency`：显式 unknown

## identity_links vs causal_edges

- A2 preload↔CANN、CANN↔TASK 投影在 `identity_links`
- Event/FIFO/A5 在 `causal_edges`

## 锥模式

- `strict`：仅 `proven_event_generation`
- `observed`：Event + `observed_structural`
"""
    path.write_text(text, encoding="utf-8")


def write_claims_md(path, acceptance: dict, coverage: dict | None = None) -> None:
    lines = ["# Claims\n", "## 允许\n"]
    claims = build_allowed_claims(coverage) if coverage else ALLOWED_CLAIMS
    for c in claims:
        lines.append(f"- {c}")
    lines.append("\n## 禁止\n")
    for c in FORBIDDEN_CLAIMS:
        lines.append(f"- {c}")
    lines.append(f"\n## 本轮验收\n- `passed={acceptance.get('passed')}`\n")
    if acceptance.get("slice_b_gate"):
        sg = acceptance["slice_b_gate"]
        lines.append(f"- Slice B gate: `{sg.get('status')}`\n")
    path.write_text("\n".join(lines), encoding="utf-8")

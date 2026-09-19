#!/usr/bin/env python3
"""D51 Wait DAG V2: casebook with V1 vs V2 FIFO comparison."""
from __future__ import annotations

from typing import Any

from wait_dag_v2_schema import GRAPH_GENERATION_ID, build_allowed_claims


def build_wait_dag_v2_casebook(
    nodes: dict,
    causal_edges: list,
    unknowns: list,
    wait_classification_rows: list[dict],
    record_without_wait_rows: list[dict],
    comm_results: list,
    acceptance: dict,
    fifo_coverage_meta: dict[str, Any],
    predicate_summaries: list[dict],
) -> str:
    fifo_edges = sum(
        1
        for e in causal_edges
        if getattr(e, "edge_type", None) == "profiler_same_stream_fifo"
        or (isinstance(e, dict) and e.get("edge_type") == "profiler_same_stream_fifo")
    )
    adj = fifo_coverage_meta.get("adjacent_pair_count", 0)
    sortable = fifo_coverage_meta.get("sortable_pair_count", 0)
    overlap = fifo_coverage_meta.get("overlap_pair_count", 0)
    malformed = fifo_coverage_meta.get("malformed_pair_count", 0)

    lines = [
        "# D51 Wait DAG V2 Casebook\n",
        f"**图代：** `{GRAPH_GENERATION_ID}`\n",
        f"**验收：** `passed={acceptance.get('passed')}`\n",
        "\n## V1 vs V2 FIFO 对照\n",
        "| 口径 | V1 Slice A | V2 本轮 |",
        "|---|---|---|",
        f"| FIFO 边数 | 0（整流 fifo_order_ambiguous×2） | {fifo_edges} |",
        f"| 相邻对分母 | stream 级清零 | {adj} |",
        f"| sortable 对 | 0 | {sortable} |",
        f"| overlap unknown 对 | 2 stream 级 | {overlap} |",
        f"| malformed unknown 对 | — | {malformed} |",
        "\n**说明：** V2 `profiler_same_stream_fifo` 仅为 adjacent_nonoverlap 观察边，"
        "不是 ACL/HCCL 文档 FIFO 保证；不得写成平台规范已证。\n",
    ]

    lines.append("## 24 Wait 逐条\n")
    lines.append("| cs | semantic | reason | kernel_pred | comm_succ | comm_op | A6 |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in sorted(wait_classification_rows, key=lambda x: x["wait_call_sequence"]):
        lines.append(
            f"| {row['wait_call_sequence']} | {row['semantic_class']} | "
            f"{row['classification_reason']} | {row.get('kernel_predecessor_rowid')} | "
            f"{row.get('comm_successor_rowid')} | {row.get('comm_op_name')} | "
            f"{row.get('a6_member')} |"
        )

    lines.append("\n## 非 A6 12 条 C1–C8 汇总\n")
    dist: dict[str, int] = {}
    for s in predicate_summaries:
        dist[s.get("semantic_class", "unclassified")] = (
            dist.get(s.get("semantic_class", "unclassified"), 0) + 1
        )
    lines.append(f"分布：{dist}\n")
    for s in sorted(predicate_summaries, key=lambda x: x["wait_call_sequence"]):
        lines.append(
            f"- cs={s['wait_call_sequence']}: {s['semantic_class']}, "
            f"first_blocker={s.get('first_blocker')}, "
            f"kernel={s.get('kernel_predecessor_rowid')}, "
            f"comm_entry={s.get('comm_successor_rowid')}"
        )

    lines.append("\n## 12 条 A6 重放\n")
    for cr in sorted(comm_results, key=lambda x: x.comm_op_name):
        lines.append(
            f"- **{cr.comm_op_name}**: cs={cr.preload_wait_call_sequence}, "
            f"A6={'PASS' if cr.a6_pass else 'FAIL'}"
        )

    lines.append("\n## record_without_wait\n")
    lines.append(f"计数：{len(record_without_wait_rows)}（V1 分母 27 须不变）\n")

    lines.append("\n## strict vs observed cone\n")
    lines.append(
        "- **strict**：仅 `proven_event_generation` Event 边；不声称 kernel/COMM TASK 可达。\n"
        "- **observed**：Event + V2 逐对 FIFO + 冻结 A5；唯一投影作查询别名，非因果边。\n"
        "- overlap pair 处 observed cone 停止，不跨对补边。\n"
    )

    lines.append("\n## Unknown 摘要\n")
    by_reason: dict[str, int] = {}
    for u in unknowns:
        rc = u.reason_code if hasattr(u, "reason_code") else u.get("reason_code")
        by_reason[rc] = by_reason.get(rc, 0) + 1
    for rc, n in sorted(by_reason.items()):
        lines.append(f"- `{rc}`: {n}")

    lines.append("\n## Hook 决策\n")
    lines.append(f"- `{acceptance.get('hook_decision', 'HOOK_NOT_TRIGGERED')}`\n")
    lines.append("- 本 Plan 默认不实现 Notify hook；缺 TASK / overlap / 跨 rank 不得伪装成 Notify 可解。\n")

    return "\n".join(lines) + "\n"


def write_schema_md(path) -> None:
    text = """# Wait DAG Schema V2

## V2 相对 V1

- FIFO：逐相邻对 `(startNs,endNs,rowid)` 排序；`a.endNs<=b.startNs` 才发边；重叠仅该对 unknown。
- 反向 12 Wait：`C1–C8` 全 PASS 才 `compute_ready_to_comm_candidate`。
- cone：`strict` 仅 Event；`observed` 含 FIFO/A5 + 投影别名折叠。

## 边类型

- `profiler_same_stream_fifo`：`observed_structural`，`reason=adjacent_nonoverlap`
- pair unknown：`adjacent_interval_overlap` / `malformed_or_nonunique_task_interval`

## 禁止 claims

- 平台 FIFO 已证明；已能 delay；整代图闭合；Notify 已配对
"""
    path.write_text(text, encoding="utf-8")


def write_claims_md(path, acceptance: dict, coverage: dict | None = None) -> None:
    lines = ["# Claims V2\n", "## 允许\n"]
    cand = acceptance.get("reverse_wait_candidate_count", 0)
    claims = build_allowed_claims(coverage, cand) if coverage else []
    for c in claims:
        lines.append(f"- {c}")
    lines.append("\n## 禁止\n")
    from wait_dag_schema import FORBIDDEN_CLAIMS

    for c in FORBIDDEN_CLAIMS:
        lines.append(f"- {c}")
    lines.append(f"\n## 本轮验收\n- `passed={acceptance.get('passed')}`\n")
    lines.append(f"- FIFO 边：{acceptance.get('fifo_edges')}\n")
    lines.append(f"- pair overlap unknown：{acceptance.get('fifo_pair_overlap_unknown')}\n")
    if acceptance.get("slice_b_gate"):
        sg = acceptance["slice_b_gate"]
        lines.append(f"- Slice B gate: `{sg.get('status')}`\n")
    path.write_text("\n".join(lines), encoding="utf-8")

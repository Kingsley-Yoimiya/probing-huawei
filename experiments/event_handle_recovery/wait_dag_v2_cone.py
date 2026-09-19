#!/usr/bin/env python3
"""D51 Wait DAG V2: strict/observed cone path witnesses."""
from __future__ import annotations

import json
from typing import Any

from wait_dag_schema import CausalEdge, DagNode, task_node_id
from wait_dag_v2_schema import NON_A6_WAIT_CS


def build_bidirectional_projection_aliases(
    nodes: dict[str, DagNode],
) -> tuple[dict[str, str], dict[str, str | None]]:
    """Event→TASK and TASK→Event unique projection aliases for cone queries only."""
    event_to_task: dict[str, str] = {}
    task_to_event: dict[str, str | None] = {}
    for nid, node in nodes.items():
        if node.node_type not in ("event_record_generation", "event_wait"):
            continue
        tr = node.attrs.get("task_rowid")
        if tr is None or node.attrs.get("task_projection_status") != "projected":
            continue
        task_nid = task_node_id(int(tr))
        if task_nid not in nodes:
            continue
        event_to_task[nid] = task_nid
        prev = task_to_event.get(task_nid)
        if prev is None and task_nid not in task_to_event:
            task_to_event[task_nid] = nid
        elif prev != nid:
            task_to_event[task_nid] = None
    return event_to_task, task_to_event


def build_fifo_edge_index(
    edges: list[CausalEdge],
) -> tuple[dict[int, list[int]], dict[tuple[int, int], CausalEdge]]:
    fwd: dict[int, list[int]] = {}
    edge_by_pair: dict[tuple[int, int], CausalEdge] = {}
    for e in edges:
        if e.edge_type != "profiler_same_stream_fifo":
            continue
        if e.evidence_tier != "observed_structural":
            continue
        src = int(e.src.split(":")[1])
        dst = int(e.dst.split(":")[1])
        fwd.setdefault(src, []).append(dst)
        edge_by_pair[(src, dst)] = e
    return fwd, edge_by_pair


def _step(
    src: str,
    dst: str,
    *,
    edge_type: str,
    evidence_tier: str,
    projection_ref: str = "",
    step_kind: str = "edge",
) -> dict[str, str]:
    return {
        "src_node": src,
        "dst_node": dst,
        "edge_type": edge_type,
        "evidence_tier": evidence_tier,
        "projection_ref": projection_ref,
        "step_kind": step_kind,
    }


def walk_fifo_path(
    src_rid: int,
    dst_rid: int,
    fifo_fwd: dict[int, list[int]],
    edge_by_pair: dict[tuple[int, int], CausalEdge],
    *,
    max_hops: int = 64,
) -> tuple[list[dict[str, str]] | None, str]:
    """Follow unique accepted FIFO successors; no same-stream BFS flood."""
    if src_rid == dst_rid:
        return [], "ok"
    steps: list[dict[str, str]] = []
    cur = src_rid
    visited: set[int] = set()
    for _ in range(max_hops):
        if cur == dst_rid:
            return steps, "ok"
        if cur in visited:
            return None, f"fifo_cycle_at:{cur}"
        visited.add(cur)
        succs = fifo_fwd.get(cur, [])
        if len(succs) != 1:
            return None, f"fifo_not_unique:{cur}:{succs}"
        nxt = succs[0]
        edge = edge_by_pair.get((cur, nxt))
        if edge is None:
            return None, f"fifo_edge_missing:{cur}:{nxt}"
        steps.append(
            _step(
                task_node_id(cur),
                task_node_id(nxt),
                edge_type=edge.edge_type,
                evidence_tier=edge.evidence_tier,
                projection_ref=edge.edge_id,
                step_kind="edge",
            )
        )
        cur = nxt
    return None, f"fifo_path_exceeded:{src_rid}:{dst_rid}"


def find_event_generation_edge(
    record_nid: str | None,
    wait_nid: str | None,
    causal_edges: list[CausalEdge],
) -> CausalEdge | None:
    if not record_nid or not wait_nid:
        return None
    for e in causal_edges:
        if e.edge_type == "event_generation" and e.src == record_nid and e.dst == wait_nid:
            return e
    return None


def build_reverse_wait_witness(
    cs: int,
    rev_summary: dict[str, Any],
    wait_nid: str | None,
    record_nid: str | None,
    nodes: dict[str, DagNode],
    causal_edges: list[CausalEdge],
    event_to_task: dict[str, str],
    task_to_event: dict[str, str | None],
) -> tuple[list[dict[str, str]], bool, str]:
    """Minimal kernel→…FIFO…→Record⇔Event→Wait⇔wait_task→comm_entry witness."""
    kernel_rid = rev_summary.get("kernel_predecessor_rowid")
    record_rid = rev_summary.get("record_task_rowid")
    wait_rid = rev_summary.get("wait_task_rowid")
    comm_rid = rev_summary.get("comm_successor_rowid")

    if not all(x is not None for x in (kernel_rid, record_rid, wait_rid, comm_rid)):
        missing = [
            name
            for name, val in (
                ("kernel", kernel_rid),
                ("record_task", record_rid),
                ("wait_task", wait_rid),
                ("comm_entry", comm_rid),
            )
            if val is None
        ]
        return [], False, f"missing_predicate_fields:{','.join(missing)}"

    fifo_fwd, edge_by_pair = build_fifo_edge_index(causal_edges)
    steps: list[dict[str, str]] = []

    fifo_up, err = walk_fifo_path(
        int(kernel_rid), int(record_rid), fifo_fwd, edge_by_pair
    )
    if fifo_up is None:
        return [], False, err
    steps.extend(fifo_up)

    record_task_nid = task_node_id(int(record_rid))
    if record_nid is None:
        record_nid = task_to_event.get(record_task_nid)
    if record_nid is None:
        return steps, False, "record_event_projection_missing"
    if record_nid not in nodes:
        return steps, False, "record_event_node_missing"

    steps.append(
        _step(
            record_task_nid,
            record_nid,
            edge_type="projection_alias",
            evidence_tier="observed_structural",
            projection_ref=f"task:{record_rid}->{record_nid}",
            step_kind="projection_ref",
        )
    )

    eg = find_event_generation_edge(record_nid, wait_nid, causal_edges)
    if eg is None or wait_nid is None:
        return steps, False, "event_generation_edge_missing"
    steps.append(
        _step(
            record_nid,
            wait_nid,
            edge_type=eg.edge_type,
            evidence_tier=eg.evidence_tier,
            projection_ref=eg.edge_id,
            step_kind="edge",
        )
    )

    wait_task_nid = task_node_id(int(wait_rid))
    steps.append(
        _step(
            wait_nid,
            wait_task_nid,
            edge_type="projection_alias",
            evidence_tier="observed_structural",
            projection_ref=f"{wait_nid}->task:{wait_rid}",
            step_kind="projection_ref",
        )
    )

    fifo_down, err = walk_fifo_path(
        int(wait_rid), int(comm_rid), fifo_fwd, edge_by_pair
    )
    if fifo_down is None:
        return steps, False, err
    steps.extend(fifo_down)

    comm_op = rev_summary.get("comm_op_name")
    if comm_op:
        comm_nid = f"comm:{comm_op}"
        if comm_nid in nodes:
            steps.append(
                _step(
                    task_node_id(int(comm_rid)),
                    comm_nid,
                    edge_type="comm_membership",
                    evidence_tier="observed_structural",
                    projection_ref=f"comm_op={comm_op}",
                    step_kind="membership_ref",
                )
            )

    return steps, True, ""


def traverse_strict_event_cone(
    start: str,
    nodes: dict[str, DagNode],
    edges: list[CausalEdge],
    max_depth: int = 8,
) -> list[dict[str, str]]:
    """Strict cone: proven Event edges only, no TASK/FIFO flood."""
    adj: dict[str, list[CausalEdge]] = {}
    for e in edges:
        if e.evidence_tier != "proven_event_generation":
            continue
        if e.edge_type != "event_generation":
            continue
        adj.setdefault(e.src, []).append(e)

    steps: list[dict[str, str]] = []
    cur = start
    for _ in range(max_depth):
        outs = adj.get(cur, [])
        if len(outs) != 1:
            break
        e = outs[0]
        if e.dst not in nodes:
            break
        steps.append(
            _step(
                cur,
                e.dst,
                edge_type=e.edge_type,
                evidence_tier=e.evidence_tier,
                projection_ref=e.edge_id,
                step_kind="edge",
            )
        )
        cur = e.dst
    return steps


def build_a6_comm_to_compute_witness(
    wait_cs: int,
    wait_nid: str,
    a6_cr,
    nodes: dict[str, DagNode],
    causal_edges: list[CausalEdge],
    event_to_task: dict[str, str],
    task_to_event: dict[str, str | None],
) -> tuple[list[dict[str, str]], bool, str]:
    """comm/A5 → record TASK → Record → Wait → wait TASK (+ downstream if projected)."""
    steps: list[dict[str, str]] = []
    comm_nid = f"comm:{a6_cr.comm_op_name}"
    if comm_nid not in nodes:
        return [], False, "comm_node_missing"

    record_nid = None
    for e in causal_edges:
        if e.edge_type == "event_generation" and e.dst == wait_nid:
            record_nid = e.src
            break
    if record_nid is None:
        return [], False, "event_generation_missing"

    wait_task_nid = event_to_task.get(wait_nid)
    record_task_nid = event_to_task.get(record_nid)

    a5 = None
    if record_task_nid:
        for e in causal_edges:
            if (
                e.edge_type == "a5_comm_completion_structure"
                and e.dst == record_task_nid
            ):
                a5 = e
                break

    if a5:
        steps.append(
            _step(
                comm_nid,
                a5.src,
                edge_type="comm_to_terminal",
                evidence_tier="observed_structural",
                projection_ref=a6_cr.comm_op_name,
                step_kind="membership_ref",
            )
        )
        steps.append(
            _step(
                a5.src,
                a5.dst,
                edge_type=a5.edge_type,
                evidence_tier=a5.evidence_tier,
                projection_ref=a5.edge_id,
                step_kind="edge",
            )
        )
    elif record_task_nid:
        steps.append(
            _step(
                comm_nid,
                record_task_nid,
                edge_type="comm_to_record_task",
                evidence_tier="observed_structural",
                projection_ref=f"wait_cs={wait_cs}",
                step_kind="membership_ref",
            )
        )

    if record_task_nid and record_nid:
        steps.append(
            _step(
                record_task_nid,
                record_nid,
                edge_type="projection_alias",
                evidence_tier="observed_structural",
                projection_ref=f"{record_task_nid}->{record_nid}",
                step_kind="projection_ref",
            )
        )
    elif record_nid:
        steps.append(
            _step(
                comm_nid,
                record_nid,
                edge_type="comm_to_record_event",
                evidence_tier="proven_event_generation",
                projection_ref=f"wait_cs={wait_cs}",
                step_kind="membership_ref",
            )
        )
    else:
        return steps, False, "record_event_missing"

    eg = find_event_generation_edge(record_nid, wait_nid, causal_edges)
    if eg is None:
        return steps, False, "event_generation_edge_missing"
    steps.append(
        _step(
            record_nid,
            wait_nid,
            edge_type=eg.edge_type,
            evidence_tier=eg.evidence_tier,
            projection_ref=eg.edge_id,
            step_kind="edge",
        )
    )

    if wait_task_nid:
        steps.append(
            _step(
                wait_nid,
                wait_task_nid,
                edge_type="projection_alias",
                evidence_tier="observed_structural",
                projection_ref=f"{wait_nid}->{wait_task_nid}",
                step_kind="projection_ref",
            )
        )
        fifo_fwd, edge_by_pair = build_fifo_edge_index(causal_edges)
        wait_rid = int(wait_task_nid.split(":")[1])
        succ = fifo_fwd.get(wait_rid, [])
        if len(succ) == 1:
            nxt = succ[0]
            edge = edge_by_pair.get((wait_rid, nxt))
            if edge:
                steps.append(
                    _step(
                        wait_task_nid,
                        task_node_id(nxt),
                        edge_type=edge.edge_type,
                        evidence_tier=edge.evidence_tier,
                        projection_ref=edge.edge_id,
                        step_kind="edge",
                    )
                )
        return steps, True, ""
    return steps, False, "wait_task_projection_missing"


def _path_summary(steps: list[dict[str, str]]) -> str:
    if not steps:
        return ""
    nodes = [steps[0]["src_node"]]
    for s in steps:
        nodes.append(s["dst_node"])
    return " -> ".join(nodes)


def _cone_row(
    case_id: str,
    wait_cs: int | str,
    mode: str,
    semantic_class: str,
    first_blocker: str,
    witness_start: str,
    witness_end: str,
    steps: list[dict[str, str]],
    witness_complete: bool,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "wait_cs": wait_cs,
        "mode": mode,
        "semantic_class": semantic_class,
        "first_blocker": first_blocker,
        "witness_complete": witness_complete,
        "path_steps": len(steps),
        "witness_start": witness_start,
        "witness_end": witness_end,
        "path_summary": _path_summary(steps),
        "witness_steps_json": json.dumps(steps, ensure_ascii=False, separators=(",", ":")),
    }


def build_cone_paths(
    nodes: dict[str, DagNode],
    causal_edges: list[CausalEdge],
    wait_classification_rows: list[dict],
    predicate_summaries: list[dict],
    record_without_wait_rows: list[dict],
    comm_results: list,
    pair_unknown_specs: list[dict],
) -> list[dict]:
    event_to_task, task_to_event = build_bidirectional_projection_aliases(nodes)
    rows: list[dict] = []

    rev_summary = next(
        (s for s in predicate_summaries if s.get("semantic_class") == "compute_ready_to_comm_candidate"),
        predicate_summaries[0] if predicate_summaries else None,
    )
    if rev_summary is None:
        rev_row = next(
            (r for r in wait_classification_rows if r["wait_call_sequence"] in NON_A6_WAIT_CS),
            None,
        )
    else:
        rev_row = next(
            (
                r
                for r in wait_classification_rows
                if r["wait_call_sequence"] == rev_summary["wait_call_sequence"]
            ),
            None,
        )

    if rev_row:
        cs = rev_row["wait_call_sequence"]
        wait_nid = next(
            (
                nid
                for nid, n in nodes.items()
                if n.node_type == "event_wait" and n.attrs.get("call_sequence") == cs
            ),
            None,
        )
        record_nid = None
        for e in causal_edges:
            if e.edge_type == "event_generation" and e.dst == wait_nid:
                record_nid = e.src
                break

        if rev_summary and rev_summary.get("semantic_class") == "compute_ready_to_comm_candidate":
            steps, complete, blocker = build_reverse_wait_witness(
                cs,
                rev_summary,
                wait_nid,
                record_nid,
                nodes,
                causal_edges,
                event_to_task,
                task_to_event,
            )
            rows.append(
                _cone_row(
                    "reverse_wait_kernel_to_comm",
                    cs,
                    "observed",
                    rev_row.get("semantic_class", ""),
                    blocker if not complete else "",
                    steps[0]["src_node"] if steps else "",
                    steps[-1]["dst_node"] if steps else "",
                    steps,
                    complete,
                )
            )
        elif rev_summary:
            fb = rev_summary.get("first_blocker") or rev_row.get("classification_reason") or "predicate_fail"
            rows.append(
                _cone_row(
                    "reverse_wait_kernel_to_comm",
                    cs,
                    "observed",
                    rev_row.get("semantic_class", ""),
                    fb,
                    f"wait_cs={cs}",
                    "frontier",
                    [],
                    False,
                )
            )
        elif pair_unknown_specs:
            p = pair_unknown_specs[0]
            rows.append(
                _cone_row(
                    "reverse_wait_overlap_frontier",
                    cs,
                    "observed",
                    rev_row.get("semantic_class", ""),
                    f"overlap_pair:{p['src_rowid']}:{p['dst_rowid']}",
                    task_node_id(p["src_rowid"]),
                    task_node_id(p["dst_rowid"]),
                    [],
                    False,
                )
            )

    a6_cr = next(
        (
            cr
            for cr in comm_results
            if cr.a6_pass
            and cr.event_wait_task_rowid
            and cr.preload_wait_call_sequence not in (221, 222)
        ),
        None,
    )
    if a6_cr:
        wait_nid = next(
            (
                nid
                for nid, n in nodes.items()
                if n.node_type == "event_wait"
                and n.attrs.get("call_sequence") == a6_cr.preload_wait_call_sequence
            ),
            None,
        )
        if wait_nid:
            steps, complete, blocker = build_a6_comm_to_compute_witness(
                a6_cr.preload_wait_call_sequence,
                wait_nid,
                a6_cr,
                nodes,
                causal_edges,
                event_to_task,
                task_to_event,
            )
            rows.append(
                _cone_row(
                    "a6_comm_to_compute",
                    a6_cr.preload_wait_call_sequence,
                    "observed",
                    "comm_completion_to_compute",
                    blocker if not complete else "",
                    steps[0]["src_node"] if steps else wait_nid,
                    steps[-1]["dst_node"] if steps else "",
                    steps,
                    complete,
                )
            )

    for cs in (221, 222):
        cr = next((c for c in comm_results if c.preload_wait_call_sequence == cs), None)
        if cr:
            wait_nid = next(
                (
                    nid
                    for nid, n in nodes.items()
                    if n.node_type == "event_wait" and n.attrs.get("call_sequence") == cs
                ),
                None,
            )
            rows.append(
                _cone_row(
                    "a6_missing_wait_task",
                    cs,
                    "observed",
                    "comm_completion_to_compute",
                    "event_task_projection_missing",
                    wait_nid or "",
                    "unknown:downstream_compute",
                    [],
                    False,
                )
            )

    if record_without_wait_rows:
        r0 = record_without_wait_rows[0]
        nid = r0["node_id"]
        steps = traverse_strict_event_cone(nid, nodes, causal_edges)
        rows.append(
            _cone_row(
                "record_without_wait",
                "",
                "strict",
                "unclassified",
                "no_consuming_wait",
                nid,
                steps[-1]["dst_node"] if steps else nid,
                steps,
                bool(steps),
            )
        )

    return rows

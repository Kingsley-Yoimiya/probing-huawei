#!/usr/bin/env python3
"""D51 Wait DAG V2: pair-level FIFO and reverse Wait predicate helpers."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from analyze_event_pairs import COMPUTE_TASK_NAMES, TaskRow, resolve_string
from wait_dag_schema import A6_RECORD_STREAM, A6_WAIT_STREAM, CausalEdge, task_node_id
from wait_dag_v2_schema import NON_A6_WAIT_CS


def sort_active_tasks(
    tasks: list[TaskRow], active_start: int, active_end: int
) -> list[TaskRow]:
    active = [t for t in tasks if active_start <= t.start_ns <= active_end]
    active.sort(key=lambda t: (t.start_ns, t.end_ns, t.rowid))
    return active


def evaluate_adjacent_pair(a: TaskRow, b: TaskRow) -> tuple[str, str]:
    """Return (verdict, reason_code). verdict: sortable|overlap|malformed."""
    if a.rowid == b.rowid:
        return "malformed", "malformed_or_nonunique_task_interval"
    if a.start_ns > a.end_ns or b.start_ns > b.end_ns:
        return "malformed", "malformed_or_nonunique_task_interval"
    if a.end_ns > b.start_ns:
        return "overlap", "adjacent_interval_overlap"
    return "sortable", "adjacent_nonoverlap"


def build_pair_level_fifo(
    stream_tasks: dict[int, list[TaskRow]],
    active_start: int,
    active_end: int,
    next_edge_id,
) -> tuple[list[CausalEdge], list[dict], list[dict], dict[str, Any]]:
    """Emit pair-level FIFO edges and per-pair audit rows."""
    fifo_edges: list[CausalEdge] = []
    fifo_pair_rows: list[dict] = []
    pair_unknown_specs: list[dict] = []
    stream_stats: dict[int, dict[str, int]] = {}

    global_sortable = 0
    global_overlap = 0
    global_malformed = 0
    global_adjacent = 0

    for sid in sorted(stream_tasks):
        ordered = sort_active_tasks(stream_tasks[sid], active_start, active_end)
        task_count = len(ordered)
        adjacent = max(task_count - 1, 0)
        sortable = overlap = malformed = 0

        for ord_i in range(adjacent):
            a, b = ordered[ord_i], ordered[ord_i + 1]
            verdict, reason = evaluate_adjacent_pair(a, b)
            emitted_edge_id = ""
            if verdict == "sortable":
                eid = next_edge_id("fifo")
                emitted_edge_id = eid
                fifo_edges.append(
                    CausalEdge(
                        edge_id=eid,
                        src=task_node_id(a.rowid),
                        dst=task_node_id(b.rowid),
                        edge_type="profiler_same_stream_fifo",
                        evidence_tier="observed_structural",
                        identity_source="profiler_same_stream_fifo",
                        semantic_class="unclassified",
                        reason_code="adjacent_nonoverlap",
                        stream_domain=f"profiler_streamId:{sid}",
                        evidence_refs=[f"pair:{a.rowid}:{b.rowid}"],
                    )
                )
                sortable += 1
            else:
                pair_unknown_specs.append(
                    {
                        "stream_id": sid,
                        "ordinal": ord_i,
                        "src_rowid": a.rowid,
                        "dst_rowid": b.rowid,
                        "reason_code": reason,
                        "verdict": verdict,
                    }
                )
                if verdict == "overlap":
                    overlap += 1
                else:
                    malformed += 1

            fifo_pair_rows.append(
                {
                    "stream_id": sid,
                    "ordinal": ord_i,
                    "src_rowid": a.rowid,
                    "dst_rowid": b.rowid,
                    "src_start_ns": a.start_ns,
                    "src_end_ns": a.end_ns,
                    "dst_start_ns": b.start_ns,
                    "dst_end_ns": b.end_ns,
                    "verdict": verdict,
                    "reason_code": reason,
                    "emitted_edge_id": emitted_edge_id,
                }
            )

        stream_stats[sid] = {
            "task_count": task_count,
            "adjacent_pair_count": adjacent,
            "sortable_pair_count": sortable,
            "overlap_pair_count": overlap,
            "malformed_pair_count": malformed,
        }
        global_adjacent += adjacent
        global_sortable += sortable
        global_overlap += overlap
        global_malformed += malformed

    coverage_meta = {
        "adjacent_pair_count": global_adjacent,
        "sortable_pair_count": global_sortable,
        "overlap_pair_count": global_overlap,
        "malformed_pair_count": global_malformed,
        "stream_stats": stream_stats,
    }
    return fifo_edges, fifo_pair_rows, pair_unknown_specs, coverage_meta


def build_fifo_adjacency(fifo_edges: list[CausalEdge]) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
    fwd: dict[int, list[int]] = defaultdict(list)
    rev: dict[int, list[int]] = defaultdict(list)
    for e in fifo_edges:
        if e.edge_type != "profiler_same_stream_fifo":
            continue
        src = int(e.src.split(":")[1])
        dst = int(e.dst.split(":")[1])
        fwd[src].append(dst)
        rev[dst].append(src)
    return fwd, rev


def is_kernel_task(task: TaskRow, string_ids: dict[int, str]) -> bool:
    name = resolve_string(string_ids, task.task_type) or ""
    return any(k in name for k in COMPUTE_TASK_NAMES) or (
        "KERNEL" in name.upper() and "EVENT" not in name.upper()
    )


def nearest_kernel_predecessor(
    record_task: TaskRow,
    all_tasks_by_rowid: dict[int, TaskRow],
    fifo_rev: dict[int, list[int]],
    overlap_pairs: set[tuple[int, int]],
    string_ids: dict[int, str],
) -> tuple[TaskRow | None, str, list[int]]:
    """Walk backwards along accepted FIFO only."""
    visited: set[int] = set()
    queue = [record_task.rowid]
    candidates: list[int] = []

    while queue:
        cur = queue.pop(0)
        if cur in visited:
            continue
        visited.add(cur)
        task = all_tasks_by_rowid.get(cur)
        if task is None:
            continue
        if is_kernel_task(task, string_ids):
            candidates.append(cur)
            continue
        for pred in fifo_rev.get(cur, []):
            if (pred, cur) in overlap_pairs:
                return None, "overlap_frontier_blocking_backward_walk", candidates
            if pred not in visited:
                queue.append(pred)

    if len(candidates) == 1:
        return all_tasks_by_rowid[candidates[0]], "ok", candidates
    if len(candidates) > 1:
        return None, "multiple_kernel_predecessors", candidates
    return None, "no_kernel_predecessor_on_fifo_path", candidates


def unique_fifo_successors(
    task_rowid: int, fifo_fwd: dict[int, list[int]]
) -> tuple[list[int], str]:
    succ = fifo_fwd.get(task_rowid, [])
    if len(succ) == 1:
        return succ, "ok"
    if len(succ) == 0:
        return [], "no_fifo_successor"
    return succ, "multiple_fifo_successors"


def comm_op_for_task(
    task: TaskRow,
    comm_ops: list[dict],
    tasks_by_cid: dict[int, list[TaskRow]],
    string_ids: dict[int, str],
) -> tuple[dict | None, str, list[str]]:
    """TASK connectionId membership only; no Event/CANN cid shortcut."""
    matches: list[dict] = []
    for op in comm_ops:
        cid = op["connection_id"]
        comm_tasks = tasks_by_cid.get(cid, [])
        if not comm_tasks:
            continue
        if task.rowid not in {t.rowid for t in comm_tasks}:
            continue
        name = op.get("op_name") or ""
        if not name.startswith("hcom_allReduce_"):
            continue
        matches.append(op)
    if len(matches) == 1:
        return matches[0], "ok", [m["op_name"] for m in matches]
    if len(matches) == 0:
        return None, "no_comm_op_membership", []
    return None, "multiple_comm_op_membership", [m["op_name"] for m in matches]


def first_comm_entry_on_stream(
    comm_op: dict,
    tasks_by_cid: dict[int, list[TaskRow]],
    stream_id: int,
    active_start: int,
    active_end: int,
) -> tuple[TaskRow | None, str, list[int]]:
    cid = comm_op["connection_id"]
    comm_tasks = [
        t
        for t in tasks_by_cid.get(cid, [])
        if t.stream_id == stream_id and active_start <= t.start_ns <= active_end
    ]
    if not comm_tasks:
        return None, "no_comm_tasks_on_stream", []
    comm_tasks.sort(key=lambda t: (t.start_ns, t.end_ns, t.rowid))
    first = comm_tasks[0]
    same_first = [t.rowid for t in comm_tasks if (t.start_ns, t.end_ns, t.rowid) == (first.start_ns, first.end_ns, first.rowid)]
    if len(same_first) != 1:
        return None, "ambiguous_first_comm_entry", same_first
    return first, "ok", [first.rowid]


def evaluate_reverse_wait_predicate(
    wait_cs: int,
    wait_rec,
    record_rec,
    record_task: TaskRow | None,
    wait_task: TaskRow | None,
    gen_info: dict,
    all_tasks_by_rowid: dict[int, TaskRow],
    fifo_fwd: dict[int, list[int]],
    fifo_rev: dict[int, list[int]],
    overlap_pairs: set[tuple[int, int]],
    comm_ops: list[dict],
    tasks_by_cid: dict[int, list[TaskRow]],
    string_ids: dict[int, str],
    active_start: int,
    active_end: int,
) -> tuple[str, list[dict], dict[str, Any]]:
    """C1–C8 trace; returns (semantic_class, trace_rows, summary)."""
    trace: list[dict] = []

    def row(cond: str, passed: bool, detail: str = "", **extra: Any) -> None:
        trace.append(
            {
                "wait_call_sequence": wait_cs,
                "condition": cond,
                "passed": passed,
                "detail": detail,
                **extra,
            }
        )

    # C1
    c1 = wait_cs in NON_A6_WAIT_CS
    rk = gen_info.get("wait_bindings", {}).get(wait_cs)
    c1 = c1 and rk is not None and record_rec is not None
    row("C1", c1, f"non_a6={wait_cs in NON_A6_WAIT_CS}, a4_bound={rk is not None}")

    # C2
    c2 = False
    if record_rec is not None and wait_rec is not None:
        c2 = (
            record_rec.raw_stream == A6_WAIT_STREAM
            and wait_rec.raw_stream == A6_RECORD_STREAM
        )
    row(
        "C2",
        c2,
        f"record_raw={getattr(record_rec, 'raw_stream', None)} wait_raw={getattr(wait_rec, 'raw_stream', None)}",
    )

    # C3
    c3 = record_task is not None and wait_task is not None
    row(
        "C3",
        c3,
        f"record_task={record_task.rowid if record_task else None} wait_task={wait_task.rowid if wait_task else None}",
        record_task_rowid=record_task.rowid if record_task else None,
        wait_task_rowid=wait_task.rowid if wait_task else None,
    )

    # C4
    kernel_task = None
    kernel_rowids: list[int] = []
    c4 = False
    c4_blocker = ""
    if record_task is not None:
        kernel_task, c4_blocker, kernel_rowids = nearest_kernel_predecessor(
            record_task,
            all_tasks_by_rowid,
            fifo_rev,
            overlap_pairs,
            string_ids,
        )
        c4 = kernel_task is not None and c4_blocker == "ok"
    else:
        c4_blocker = "record_task_missing"
    row(
        "C4",
        c4,
        c4_blocker,
        kernel_predecessor_rowid=kernel_task.rowid if kernel_task else None,
        kernel_candidates=";".join(str(x) for x in kernel_rowids),
    )

    # C5
    comm_succ: list[int] = []
    c5 = False
    c5_blocker = ""
    if wait_task is not None:
        comm_succ, c5_blocker = unique_fifo_successors(wait_task.rowid, fifo_fwd)
        c5 = c5_blocker == "ok"
    else:
        c5_blocker = "wait_task_missing"
    row(
        "C5",
        c5,
        c5_blocker,
        comm_successor_rowid=comm_succ[0] if len(comm_succ) == 1 else None,
        comm_successor_candidates=";".join(str(x) for x in comm_succ),
    )

    # C6
    comm_op = None
    comm_entry = None
    c6 = False
    c6_blocker = ""
    comm_candidates: list[str] = []
    if c5 and comm_succ:
        q = all_tasks_by_rowid.get(comm_succ[0])
        if q is None:
            c6_blocker = "successor_task_missing"
        else:
            comm_op, memb_blocker, comm_candidates = comm_op_for_task(
                q, comm_ops, tasks_by_cid, string_ids
            )
            if comm_op is None:
                c6_blocker = memb_blocker
            else:
                comm_entry, entry_blocker, entry_cands = first_comm_entry_on_stream(
                    comm_op,
                    tasks_by_cid,
                    q.stream_id,
                    active_start,
                    active_end,
                )
                if comm_entry is None or comm_entry.rowid != q.rowid:
                    c6_blocker = entry_blocker if comm_entry is None else "q_not_first_comm_entry"
                else:
                    c6 = True
    else:
        c6_blocker = "c5_failed"
    row(
        "C6",
        c6,
        c6_blocker,
        comm_op_name=comm_op.get("op_name") if comm_op else None,
        comm_op_connection_id=comm_op.get("connection_id") if comm_op else None,
        comm_entry_rowid=comm_entry.rowid if comm_entry else None,
        comm_op_candidates=";".join(comm_candidates),
    )

    # C7 composite path
    c7 = c1 and c2 and c3 and c4 and c5 and c6
    c7_blocker = ""
    if not c7:
        for cond, ok in [
            ("C1", c1),
            ("C2", c2),
            ("C3", c3),
            ("C4", c4),
            ("C5", c5),
            ("C6", c6),
        ]:
            if not ok:
                c7_blocker = f"first_fail={cond}"
                break
    row("C7", c7, c7_blocker or "composite_path_ok")

    # C8 independence (no tie-break) — encoded in single-candidate checks above
    c8 = c7 and len(kernel_rowids) <= 1 and len(comm_succ) <= 1 and len(comm_candidates) <= 1
    row("C8", c8, "no_multi_candidate_tiebreak" if c8 else "multi_candidate_or_prior_fail")

    all_pass = c1 and c2 and c3 and c4 and c5 and c6 and c7 and c8
    semantic = "compute_ready_to_comm_candidate" if all_pass else "unclassified"
    first_blocker = next((t["condition"] for t in trace if not t["passed"]), "")

    summary = {
        "wait_call_sequence": wait_cs,
        "semantic_class": semantic,
        "all_pass": all_pass,
        "first_blocker": first_blocker,
        "record_task_rowid": record_task.rowid if record_task else None,
        "wait_task_rowid": wait_task.rowid if wait_task else None,
        "kernel_predecessor_rowid": kernel_task.rowid if kernel_task else None,
        "comm_successor_rowid": comm_succ[0] if len(comm_succ) == 1 else None,
        "comm_op_name": comm_op.get("op_name") if comm_op else None,
        "comm_op_connection_id": comm_op.get("connection_id") if comm_op else None,
    }
    return semantic, trace, summary

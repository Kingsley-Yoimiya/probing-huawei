#!/usr/bin/env python3
"""D51 Wait DAG V2: pair-level FIFO and reverse Wait predicate on frozen V2b."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from a6_predicate_v6 import (  # noqa: E402
    WAIT_IDENTITY_SOURCE,
    a2_unique_wait,
    a4_bound_waits,
    apply_task_diagnostic,
    check_global_wait_reuse,
    drop_same_stream_waits,
    evaluate_a6_per_comm,
    record_key_str,
    wait_ordinal,
)
from analyze_event_pairs import (  # noqa: E402
    RECORD_OP,
    WAIT_OP,
    RecordKey,
    TaskRow,
    TraceRecord,
    align_api,
    build_allreduce_chains,
    build_cann_ordinal_maps,
    build_preload_ordinal_maps,
    discover_event_task_types,
    freeze_compute_streams,
    global_tid_parts,
    load_cann_api,
    load_comm_ops,
    load_profile_window,
    load_string_ids,
    load_tasks,
    pick_rank0_trace,
    rebuild_generations,
    resolve_string,
    stream_tasks_by_id,
)
from event_preload_v6_analyze import (  # noqa: E402
    build_cann_ordinal_maps_with_rowid,
    scan_tasks_by_cid,
    sha256_file,
)
from wait_dag_v2_casebook import build_wait_dag_v2_casebook, write_claims_md, write_schema_md  # noqa: E402
from wait_dag_v2_cone import build_cone_paths  # noqa: E402
from wait_dag_v2_fifo import (  # noqa: E402
    build_fifo_adjacency,
    build_pair_level_fifo,
    evaluate_reverse_wait_predicate,
)
from wait_dag_v2_schema import (  # noqa: E402
    A6_RECORD_STREAM,
    A6_WAIT_CALL_SEQUENCES,
    A6_WAIT_STREAM,
    ANALYZER_VERSION,
    build_allowed_claims,
    CausalEdge,
    DagNode,
    DENOMINATORS,
    EXPECTED_DB_SHA256,
    FORBIDDEN_CLAIMS,
    FROZEN_ACTIVE_WINDOW,
    FROZEN_RANK0_PID,
    GRAPH_GENERATION_ID,
    IdentityLink,
    NON_A6_WAIT_CS,
    SCHEMA_VERSION,
    UnknownEntry,
    comm_node_id,
    host_sync_node_id,
    record_key_tuple_str,
    record_node_id,
    task_node_id,
    unknown_node_id,
    wait_node_id,
)
API_RECORD = "aclrtRecordEvent"
API_WAIT = "aclrtStreamWaitEvent"
SYNC_API_SUBSTRINGS = ("SynchronizeStream", "SynchronizeDevice")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                keys.append(k)
                seen.add(k)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def load_cann_rowids(db_path: Path) -> dict[tuple, int]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    out: dict[tuple, int] = {}
    for rowid, start_ns, end_ns, global_tid, connection_id, name_id in cur.execute(
        "SELECT rowid, startNs, endNs, globalTid, connectionId, name FROM CANN_API"
    ):
        pid, tid = global_tid_parts(int(global_tid))
        out[(pid, tid, int(start_ns), int(end_ns), connection_id, name_id)] = int(rowid)
    con.close()
    return out


def enrich_cann_rowids(cann_rows: list[dict], rowid_map: dict[tuple, int]) -> None:
    for r in cann_rows:
        pid, tid = global_tid_parts(r["global_tid"])
        rid = rowid_map.get(
            (pid, tid, r["start_ns"], r["end_ns"], r["connection_id"], None)
        )
        r["rowid"] = rid


def active_records(rank0_records: list[TraceRecord], start: int, end: int) -> list[TraceRecord]:
    return [
        r
        for r in rank0_records
        if r.op == RECORD_OP
        and r.acl_ret == 0
        and start <= r.enter_realtime_ns <= end
    ]


def active_waits(rank0_records: list[TraceRecord], start: int, end: int) -> list[TraceRecord]:
    return [
        r
        for r in rank0_records
        if r.op == WAIT_OP
        and r.acl_ret == 0
        and start <= r.enter_realtime_ns <= end
    ]


def wait_ordinal_for(
    rank0_records: list[TraceRecord], pre_wait: TraceRecord, start: int, end: int
) -> int:
    return wait_ordinal(rank0_records, pre_wait, start, end)


def align_cann_for_record(
    pre_rec: TraceRecord,
    rank0_records: list[TraceRecord],
    cann_by_ordinal: dict,
    start: int,
    end: int,
) -> tuple[dict | None, int]:
    ord_i = sum(
        1
        for r in rank0_records
        if r.op == RECORD_OP
        and r.acl_ret == 0
        and r.pid == pre_rec.pid
        and r.tid == pre_rec.tid
        and start <= r.enter_realtime_ns <= end
        and r.call_sequence < pre_rec.call_sequence
    )
    cann = cann_by_ordinal.get((pre_rec.pid, pre_rec.tid, API_RECORD, ord_i))
    cand = 1 if cann is not None else 0
    return cann, cand


def align_cann_for_wait(
    pre_wait: TraceRecord,
    rank0_records: list[TraceRecord],
    cann_by_ordinal: dict,
    start: int,
    end: int,
) -> tuple[dict | None, int]:
    ord_i = wait_ordinal_for(rank0_records, pre_wait, start, end)
    cann = cann_by_ordinal.get((pre_wait.pid, pre_wait.tid, API_WAIT, ord_i))
    cand = 1 if cann is not None else 0
    return cann, cand


def task_type_compatible(api_name: str, task_type_name: str) -> bool:
    tl = task_type_name.lower()
    if api_name == API_RECORD:
        return "record" in tl and "notify" not in tl
    if api_name == API_WAIT:
        return "wait" in tl and "notify" not in tl
    return False


def project_cann_to_task(
    cann_row: dict,
    api_name: str,
    tasks_by_cid: dict[Any, list[TaskRow]],
    string_ids: dict[int, str],
    event_record_type: int | None,
    event_wait_type: int | None,
    used_rowids: set[int],
) -> tuple[TaskRow | None, str]:
    cid = cann_row["connection_id"]
    candidates = tasks_by_cid.get(cid, [])
    if api_name == API_RECORD:
        type_id = event_record_type
    elif api_name == API_WAIT:
        type_id = event_wait_type
    else:
        return None, "api_incompatible"
    if type_id is None:
        return None, "event_task_type_unknown"
    typed = [t for t in candidates if t.task_type == type_id]
    if len(typed) != 1:
        return None, f"cid_candidates_not_unique_{len(typed)}"
    task = typed[0]
    name = resolve_string(string_ids, task.task_type) or ""
    if not task_type_compatible(api_name, name):
        return None, "api_task_type_incompatible"
    if task.rowid in used_rowids:
        return None, "task_rowid_reused"
    return task, "ok"


def detect_fifo_ambiguity(stream_tasks: list[TaskRow]) -> list[str]:
    issues: list[str] = []
    for i in range(len(stream_tasks) - 1):
        a, b = stream_tasks[i], stream_tasks[i + 1]
        if (b.start_ns, b.end_ns, b.rowid) < (a.start_ns, a.end_ns, a.rowid):
            issues.append(f"order_violation:{a.rowid}:{b.rowid}")
        if a.end_ns > b.start_ns and a.rowid != b.rowid:
            issues.append(f"overlap:{a.rowid}:{b.rowid}")
    seen_rowids = {t.rowid for t in stream_tasks}
    if len(seen_rowids) != len(stream_tasks):
        issues.append("duplicate_rowid")
    return issues


def fifo_ambiguous_stream_ids(
    stream_tasks: dict[int, list[TaskRow]],
    active_start: int,
    active_end: int,
) -> set[int]:
    ambiguous: set[int] = set()
    for sid, stasks in stream_tasks.items():
        active_on_stream = [
            t for t in stasks if active_start <= t.start_ns <= active_end
        ]
        if len(active_on_stream) >= 2 and detect_fifo_ambiguity(active_on_stream):
            ambiguous.add(sid)
    return ambiguous


def required_evidence_for_blockers(blockers: list[str]) -> str:
    parts: list[str] = []
    if any("a6_stream_polarity" in b for b in blockers):
        parts.append(
            "notify_handle_epoch_or_cross_stream_semantic_evidence_beyond_stream_polarity"
        )
    if any("fifo_order_ambiguous" in b for b in blockers):
        parts.append("unambiguous_profiler_fifo_adjacency_on_both_streams")
    if any(
        k in b
        for b in blockers
        for k in (
            "kernel_predecessor",
            "comm_successor",
            "discrete_unique",
        )
    ):
        parts.append("unique_compute_kernel_predecessor_task_rowid")
        parts.append("unique_comm_successor_or_comm_envelope_task_rowid")
    return ";".join(parts)


def classify_non_a6_wait(
    wait_rec: TraceRecord,
    record_rec: TraceRecord | None,
    record_task: TaskRow | None,
    wait_task: TaskRow | None,
    fifo_ambiguous_streams: set[int],
) -> tuple[str, str, list[str], str]:
    """Plan step 5：离散唯一证据不足则 unclassified（含 A6 流对调观察）。"""
    blockers: list[str] = []
    if record_rec is None:
        return "unclassified", "no_a4_record", ["a4_binding"], "a4_event_generation_binding"

    same_stream = record_rec.raw_stream == wait_rec.raw_stream
    if same_stream:
        return "same_stream_event_wait", "same_raw_stream", blockers, ""

    if (
        record_rec.raw_stream == A6_WAIT_STREAM
        and wait_rec.raw_stream == A6_RECORD_STREAM
    ):
        blockers.append("a6_stream_polarity_observation_only")

    if record_task is None:
        blockers.append("record_task_projection_missing")
    if wait_task is None:
        blockers.append("wait_task_projection_missing")
    if record_task and record_task.stream_id in fifo_ambiguous_streams:
        blockers.append(
            f"fifo_order_ambiguous_on_record_profiler_stream:{record_task.stream_id}"
        )
    if wait_task and wait_task.stream_id in fifo_ambiguous_streams:
        blockers.append(
            f"fifo_order_ambiguous_on_wait_profiler_stream:{wait_task.stream_id}"
        )

    blockers.append("discrete_unique_kernel_predecessor_unverified")
    blockers.append("discrete_unique_comm_successor_unverified")

    required = required_evidence_for_blockers(blockers)
    return "unclassified", ";".join(blockers), blockers, required


def _record_key_tuple(rk: RecordKey) -> tuple[int, int, int, int, int]:
    return (rk.pid, rk.raw_event, rk.lifetime_epoch, rk.reset_epoch, rk.record_epoch)


def event_generation_src_by_a4_only(
    wait_cs: int,
    wait_bindings: dict[int, RecordKey],
    record_nid_by_tuple: dict[tuple[int, int, int, int, int], str],
    nearer_foreign_key: RecordKey | None,
) -> str | None:
    """仅 A4 绑定；时间更近的 foreign Record 不得替代。"""
    rk = wait_bindings.get(wait_cs)
    if rk is None:
        return None
    bound_nid = record_nid_by_tuple.get(_record_key_tuple(rk))
    if nearer_foreign_key is not None and _record_key_tuple(nearer_foreign_key) != _record_key_tuple(rk):
        foreign_nid = record_nid_by_tuple.get(_record_key_tuple(nearer_foreign_key))
        if foreign_nid and foreign_nid != bound_nid:
            return bound_nid
    return bound_nid


def a5_edge_from_chain(chain: dict[str, Any]) -> tuple[str, str] | None:
    """cid 碰巧相等不得单独造 A5 边；须完整结构链且无 reject。"""
    if chain.get("reject_reason"):
        return None
    if chain.get("cid_only_match"):
        return None
    term = chain.get("terminal_rowid")
    rec = chain.get("event_record_rowid")
    if not term or not rec:
        return None
    return task_node_id(int(term)), task_node_id(int(rec))


def profiler_fifo_endpoints(task_a: TaskRow, task_b: TaskRow) -> tuple[int, int] | None:
    """同 profiler streamId 才允许 FIFO；raw stream 数值相等不得跨域。"""
    if task_a.stream_id != task_b.stream_id:
        return None
    return task_a.rowid, task_b.rowid


def evaluate_slice_a_acceptance(
    baseline: dict[str, Any],
    eg_count: int,
    cycles: list[Any],
    a6_cs_ok: bool,
    global_reuse_ok: bool = True,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    a2 = baseline.get("a2", {})
    if a2.get("preload_record") != DENOMINATORS["record"]:
        failures.append(f"a2_record={a2.get('preload_record')}")
    if a2.get("preload_wait") != DENOMINATORS["wait"]:
        failures.append(f"a2_wait={a2.get('preload_wait')}")
    if a2.get("api_unmatched", 0) != 0:
        failures.append(f"api_unmatched={a2.get('api_unmatched')}")
    if baseline.get("a4_active") != DENOMINATORS["wait"]:
        failures.append(f"a4={baseline.get('a4_active')}")
    if baseline.get("a5_pass") != DENOMINATORS["allreduce"]:
        failures.append(f"a5={baseline.get('a5_pass')}")
    if baseline.get("a6_pass") != DENOMINATORS["allreduce"]:
        failures.append(f"a6={baseline.get('a6_pass')}")
    if not global_reuse_ok:
        failures.append("a6_wait_reuse")
    if eg_count != DENOMINATORS["wait"]:
        failures.append(f"event_generation={eg_count}")
    if cycles:
        failures.append("cycle_detected")
    if not a6_cs_ok:
        failures.append("a6_cs_mismatch")
    return len(failures) == 0, failures


def replay_baseline(
    rank0_records: list[TraceRecord],
    cann_rows: list[dict],
    gen_info: dict,
    chains: list[dict],
    active_start: int,
    active_end: int,
    api_unmatched: int,
    comm_results: list,
) -> dict[str, Any]:
    a2 = {
        "preload_record": len(active_records(rank0_records, active_start, active_end)),
        "preload_wait": len(active_waits(rank0_records, active_start, active_end)),
        "cann_record": sum(
            1
            for r in cann_rows
            if r["name"] == API_RECORD and active_start <= r["start_ns"] <= active_end
        ),
        "cann_wait": sum(
            1
            for r in cann_rows
            if r["name"] == API_WAIT and active_start <= r["start_ns"] <= active_end
        ),
        "api_unmatched": api_unmatched,
    }
    active_wait_list = active_waits(rank0_records, active_start, active_end)
    a4_active = sum(1 for w in active_wait_list if w.call_sequence in gen_info["wait_bindings"])
    a5_pass = sum(
        1
        for c in chains
        if c.get("preload_record_call_sequence") is not None
        and c.get("event_record_rowid") is not None
        and c.get("reject_reason", "") == ""
    )
    a6_pass = sum(1 for cr in comm_results if cr.a6_pass)
    return {
        "a2": a2,
        "a4_active": a4_active,
        "a5_pass": a5_pass,
        "a6_pass": a6_pass,
        "denominator_allreduce": len(comm_results),
    }


def build_unknown_frontier(
    nodes: dict[str, DagNode],
    edges: list[CausalEdge],
    unknowns: list[UnknownEntry],
    tier_filter: set[str],
    mode: str,
) -> list[dict]:
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.evidence_tier not in tier_filter:
            continue
        if e.src in nodes and e.dst in nodes:
            adj[e.src].append(e.dst)

    stub_to_unknown = {u.unknown_id: u for u in unknowns}
    blocks_field = "blocks_strict_cone" if mode == "strict" else "blocks_observed_cone"
    rows: list[dict] = []
    for start in sorted(nodes.keys()):
        reached: set[str] = set()
        q: deque[str] = deque([start])
        while q:
            u = q.popleft()
            if u in reached:
                continue
            reached.add(u)
            for v in adj.get(u, []):
                if v not in reached:
                    q.append(v)

        frontier_unknowns = [
            u
            for u in unknowns
            if getattr(u, blocks_field) and u.anchor_node in reached
        ]
        border_stubs: set[str] = set()
        for u in reached:
            for e in edges:
                if (
                    e.evidence_tier == "unknown"
                    and e.src == u
                    and e.dst not in reached
                ):
                    unk = stub_to_unknown.get(e.dst)
                    if mode == "strict":
                        if unk is None or not unk.blocks_strict_cone:
                            continue
                    border_stubs.add(e.dst)

        if not frontier_unknowns and not border_stubs:
            continue
        rows.append(
            {
                "start_node": start,
                "mode": mode,
                "reachable_count": len(reached),
                "unknown_frontier_ids": ";".join(
                    sorted(u.unknown_id for u in frontier_unknowns)
                ),
                "unknown_frontier_reasons": ";".join(
                    sorted({u.reason_code for u in frontier_unknowns})
                ),
                "frontier_stub_nodes": ";".join(sorted(border_stubs)),
                "frontier_count": len(frontier_unknowns) + len(border_stubs),
            }
        )
    return rows


def resolve_hook_decision(
    unknowns: list[UnknownEntry],
    cone_path_rows: list[dict],
) -> str:
    """Acceptance §5: only Notify-only frontiers may request hook Plan."""
    notify_codes = frozenset({"notify_pair_unknown"})
    non_notify_blockers = frozenset(
        {
            "event_task_projection_missing",
            "adjacent_interval_overlap",
            "unknown_cross_rank",
            "overlap_pair",
            "missing_predicate_fields",
            "fifo_not_unique",
            "event_generation_edge_missing",
        }
    )
    for row in cone_path_rows:
        fb = str(row.get("first_blocker") or "")
        if not fb:
            continue
        if any(code in fb for code in non_notify_blockers):
            continue
        if fb in notify_codes or "notify" in fb.lower():
            return "HOOK_PLAN_REQUIRED_NOTIFY"
    notify_only = [
        u
        for u in unknowns
        if u.reason_code in notify_codes and u.blocks_observed_cone
    ]
    if notify_only and not any(
        u.reason_code not in notify_codes and u.blocks_observed_cone for u in unknowns
    ):
        return "HOOK_PLAN_REQUIRED_NOTIFY"
    return "HOOK_NOT_TRIGGERED"


def build_reachability(
    nodes: dict[str, DagNode],
    edges: list[CausalEdge],
    tier_filter: set[str],
) -> tuple[dict[str, set[str]], list[dict]]:
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.evidence_tier not in tier_filter:
            continue
        if e.src in nodes and e.dst in nodes:
            adj[e.src].append(e.dst)
    reachable: dict[str, set[str]] = {}
    for start in nodes:
        seen: set[str] = set()
        q: deque[str] = deque([start])
        while q:
            u = q.popleft()
            if u in seen:
                continue
            seen.add(u)
            for v in adj.get(u, []):
                if v not in seen:
                    q.append(v)
        reachable[start] = seen
    frontier_rows: list[dict] = []
    for start, reached in reachable.items():
        border: set[str] = set()
        for u in reached:
            for v in adj.get(u, []):
                if v not in reached:
                    border.add(v)
        if border:
            frontier_rows.append(
                {
                    "start_node": start,
                    "mode": "strict" if tier_filter == {"proven_event_generation"} else "observed",
                    "reachable_count": len(reached),
                    "frontier_nodes": ";".join(sorted(border)),
                }
            )
    return reachable, frontier_rows


def detect_cycles(edges: list[CausalEdge], node_ids: set[str]) -> list[list[str]]:
    adj: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.evidence_tier == "unknown":
            continue
        if e.src in node_ids and e.dst in node_ids:
            adj[e.src].append(e.dst)
    cycles: list[list[str]] = []
    visited: set[str] = set()
    stack: list[str] = []
    on_stack: set[str] = set()

    def dfs(u: str) -> None:
        visited.add(u)
        on_stack.add(u)
        stack.append(u)
        for v in adj.get(u, []):
            if v not in visited:
                dfs(v)
            elif v in on_stack:
                idx = stack.index(v)
                cycles.append(stack[idx:] + [v])
        stack.pop()
        on_stack.remove(u)

    for n in sorted(node_ids):
        if n not in visited:
            dfs(n)
    return cycles


class WaitDagBuilder:
    def __init__(
        self,
        trace_dir: Path,
        db_path: Path,
        active_start: int,
        active_end: int,
        pid_min: int,
        pid_max: int,
        run_id: str,
    ) -> None:
        self.trace_dir = trace_dir
        self.db_path = db_path
        self.active_start = active_start
        self.active_end = active_end
        self.pid_min = pid_min
        self.pid_max = pid_max
        self.run_id = run_id
        self.nodes: dict[str, DagNode] = {}
        self.identity_links: list[IdentityLink] = []
        self.causal_edges: list[CausalEdge] = []
        self.unknowns: list[UnknownEntry] = []
        self._unknown_ordinals: dict[tuple[str, str], int] = defaultdict(int)
        self._edge_counter = 0
        self._link_counter = 0
        self.used_task_rowids: set[int] = set()

    def _next_edge_id(self, prefix: str) -> str:
        self._edge_counter += 1
        return f"{prefix}_{self._edge_counter}"

    def _add_unknown(
        self,
        anchor: str,
        missing_edge_type: str,
        missing_peer_role: str,
        reason_code: str,
        observed: str,
        required: str,
        blocks_strict: bool,
        blocks_observed: bool,
        slice_b: bool,
    ) -> str:
        key = (reason_code, anchor)
        ord_i = self._unknown_ordinals[key]
        self._unknown_ordinals[key] += 1
        uid = unknown_node_id(reason_code, anchor, ord_i)
        self.unknowns.append(
            UnknownEntry(
                unknown_id=uid,
                anchor_node=anchor,
                missing_edge_type=missing_edge_type,
                missing_peer_role=missing_peer_role,
                reason_code=reason_code,
                observed_evidence=observed,
                required_evidence=required,
                blocks_strict_cone=blocks_strict,
                blocks_observed_cone=blocks_observed,
                slice_b_can_resolve=slice_b,
            )
        )
        self.nodes[uid] = DagNode(
            node_id=uid,
            node_type="unknown_stub",
            primary_key=uid,
            attrs={"reason_code": reason_code, "anchor_node": anchor},
        )
        self.causal_edges.append(
            CausalEdge(
                edge_id=self._next_edge_id("unk"),
                src=anchor,
                dst=uid,
                edge_type="unknown_dependency",
                evidence_tier="unknown",
                identity_source="unclassified",
                semantic_class="unclassified",
                reason_code=reason_code,
                cone_mode="frontier",
            )
        )
        return uid

    def build(self) -> dict[str, Any]:
        rank0_meta, rank0_records, _ = pick_rank0_trace(
            self.trace_dir, pid_min=self.pid_min, pid_max=self.pid_max
        )
        rank0_pid = int(rank0_meta["pid"])
        gen_rows, gen_errors, gen_info = rebuild_generations(rank0_records)
        cann_rows = load_cann_api(self.db_path)
        rowid_map = load_cann_rowids(self.db_path)
        enrich_cann_rowids(cann_rows, rowid_map)
        aligned, api_unmatched = align_api(
            rank0_records,
            cann_rows,
            {API_RECORD, API_WAIT},
            self.active_start,
            self.active_end,
        )
        cann_by_ord = build_cann_ordinal_maps_with_rowid(
            self.db_path, cann_rows, self.active_start, self.active_end
        )

        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        string_ids = load_string_ids(con.cursor())
        con.close()

        record_type, wait_type, _, _ = discover_event_task_types(
            self.db_path, self.active_start, self.active_end, string_ids
        )
        all_tasks = load_tasks(self.db_path)
        tasks_by_cid: dict[Any, list[TaskRow]] = defaultdict(list)
        for t in all_tasks:
            tasks_by_cid[t.connection_id].append(t)

        chains, unmatched_a5, fifo_meta = build_allreduce_chains(
            self.db_path,
            gen_info,
            self.active_start,
            self.active_end,
            rank0_pid,
            rank0_records,
            aligned,
            record_type,
            wait_type,
            string_ids,
        )

        chain_by_name: dict[str, dict] = {}
        for c in chains:
            chain_by_name[c["comm_op_name"]] = c
        comm_ops = load_comm_ops(self.db_path, self.active_start, self.active_end)
        comm_results = []
        for op in comm_ops:
            name = op["op_name"]
            chain = dict(chain_by_name.get(name, op))
            rec_seq = chain.get("preload_record_call_sequence")
            if rec_seq is not None:
                pre_rec = next((r for r in rank0_records if r.call_sequence == rec_seq), None)
                chain["_preload_record"] = pre_rec
            cr = evaluate_a6_per_comm(
                comm_op_name=name,
                comm_connection_id=op["connection_id"],
                chain=chain,
                gen_info=gen_info,
                rank0_records=rank0_records,
                cann_by_ordinal=cann_by_ord,
                active_start=self.active_start,
                active_end=self.active_end,
            )
            comm_results.append(cr)

        _, global_reuse_ok = check_global_wait_reuse(comm_results)
        cids = [cr.cann_wait_connection_id for cr in comm_results if cr.cann_wait_connection_id]
        task_rows_scan = scan_tasks_by_cid(self.db_path, list(set(cids)))
        task_by_cid_scan: dict[Any, list[dict]] = defaultdict(list)
        for t in task_rows_scan:
            task_by_cid_scan.setdefault(t["connectionId"], []).append(t)
        apply_task_diagnostic(comm_results, task_by_cid_scan)

        baseline = replay_baseline(
            rank0_records,
            cann_rows,
            gen_info,
            chains,
            self.active_start,
            self.active_end,
            len(api_unmatched),
            comm_results,
        )

        contradiction: list[str] = []
        if baseline["a2"]["preload_record"] != DENOMINATORS["record"]:
            contradiction.append(f"a2_record={baseline['a2']['preload_record']}")
        if baseline["a2"]["preload_wait"] != DENOMINATORS["wait"]:
            contradiction.append(f"a2_wait={baseline['a2']['preload_wait']}")
        if baseline["a2"]["api_unmatched"] != 0:
            contradiction.append(f"api_unmatched={baseline['a2']['api_unmatched']}")
        if baseline["a4_active"] != DENOMINATORS["wait"]:
            contradiction.append(f"a4={baseline['a4_active']}")
        if baseline["a5_pass"] != DENOMINATORS["allreduce"]:
            contradiction.append(f"a5={baseline['a5_pass']}")
        if baseline["a6_pass"] != DENOMINATORS["allreduce"]:
            contradiction.append(f"a6={baseline['a6_pass']}")
        if not global_reuse_ok:
            contradiction.append("a6_wait_reuse")

        active_task_rows = [
            t
            for t in all_tasks
            if self.active_start <= t.start_ns <= self.active_end
        ]
        stream_tasks = stream_tasks_by_id(all_tasks)
        all_tasks_by_rowid = {t.rowid: t for t in all_tasks}
        tasks_by_cid_map: dict[int, list[TaskRow]] = defaultdict(list)
        for t in all_tasks:
            tasks_by_cid_map[t.connection_id].append(t)
        comm_cids = {op["connection_id"] for op in comm_ops}

        # --- Record nodes ---
        record_with_wait: set[str] = set()
        for pre_rec in active_records(rank0_records, self.active_start, self.active_end):
            rk = gen_info["record_keys"].get(pre_rec.call_sequence)
            if rk is None:
                continue
            nid = record_node_id(
                rk.pid, rk.raw_event, rk.lifetime_epoch, rk.reset_epoch, rk.record_epoch
            )
            cann, cand = align_cann_for_record(
                pre_rec, rank0_records, cann_by_ord, self.active_start, self.active_end
            )
            rec_task, proj_status = (
                (None, "no_cann")
                if cann is None
                else project_cann_to_task(
                    cann,
                    API_RECORD,
                    tasks_by_cid,
                    string_ids,
                    record_type,
                    wait_type,
                    self.used_task_rowids,
                )
            )
            if rec_task:
                self.used_task_rowids.add(rec_task.rowid)
                proj_status = "projected"
            self.nodes[nid] = DagNode(
                node_id=nid,
                node_type="event_record_generation",
                primary_key=record_key_tuple_str(
                    rk.pid, rk.raw_event, rk.lifetime_epoch, rk.reset_epoch, rk.record_epoch
                ),
                attrs={
                    "call_sequence": pre_rec.call_sequence,
                    "raw_stream": pre_rec.raw_stream,
                    "cann_connection_id": cann.get("connection_id") if cann else None,
                    "cann_rowid": cann.get("rowid") if cann else None,
                    "a2_candidate_count": cand,
                    "task_projection_status": proj_status,
                    "task_rowid": rec_task.rowid if rec_task else None,
                },
            )
            if cann and cand == 1:
                self.identity_links.append(
                    IdentityLink(
                        link_id=f"a2_rec_{pre_rec.call_sequence}",
                        src_node=nid,
                        dst_node=f"cann_api:{cann.get('rowid')}",
                        link_type="a2_preload_cann",
                        evidence_tier="proven_event_generation",
                        attrs={"preload_cs": pre_rec.call_sequence, "api": API_RECORD},
                    )
                )
            if rec_task and cann:
                self.identity_links.append(
                    IdentityLink(
                        link_id=f"proj_rec_{pre_rec.call_sequence}",
                        src_node=f"cann_api:{cann.get('rowid')}",
                        dst_node=task_node_id(rec_task.rowid),
                        link_type="cann_task_projection",
                        evidence_tier="observed_structural",
                        attrs={"task_rowid": rec_task.rowid},
                    )
                )

        # --- Wait nodes + event_generation edges ---
        wait_classification_rows: list[dict] = []
        reverse_predicate_trace_rows: list[dict] = []
        predicate_summaries: list[dict] = []
        a6_replay_cs: set[int] = set()
        # FIFO built before reverse predicate (C4/C5 need accepted edges)
        fifo_edges, fifo_pair_rows, pair_unknown_specs, fifo_coverage_meta = (
            build_pair_level_fifo(
                stream_tasks,
                self.active_start,
                self.active_end,
                self._next_edge_id,
            )
        )
        self.causal_edges.extend(fifo_edges)
        overlap_pairs = {
            (p["src_rowid"], p["dst_rowid"])
            for p in pair_unknown_specs
            if p.get("reason_code") == "adjacent_interval_overlap"
        }
        fifo_fwd, fifo_rev = build_fifo_adjacency(fifo_edges)
        for pspec in pair_unknown_specs:
            anchor = task_node_id(pspec["src_rowid"])
            self._add_unknown(
                anchor,
                "profiler_same_stream_fifo",
                "adjacent_task",
                pspec["reason_code"],
                f"pair:{pspec['src_rowid']}:{pspec['dst_rowid']}",
                "unambiguous adjacent profiler order or issue-order observation",
                False,
                True,
                False,
            )

        for wait_rec in active_waits(rank0_records, self.active_start, self.active_end):
            cs = wait_rec.call_sequence
            wnid = wait_node_id(wait_rec.pid, cs)
            rk = gen_info["wait_bindings"].get(cs)
            record_nid = None
            record_rec = None
            if rk:
                record_nid = record_node_id(
                    rk.pid, rk.raw_event, rk.lifetime_epoch, rk.reset_epoch, rk.record_epoch
                )
                record_rec = next(
                    (r for r in rank0_records if r.call_sequence in gen_info["record_keys"]
                     and gen_info["record_keys"].get(r.call_sequence) == rk),
                    None,
                )
                if record_rec is None:
                    for rseq, rkey in gen_info["record_keys"].items():
                        if rkey == rk:
                            record_rec = next(
                                (r for r in rank0_records if r.call_sequence == rseq), None
                            )
                            break
                record_with_wait.add(record_nid)

            cann_w, cand_w = align_cann_for_wait(
                wait_rec, rank0_records, cann_by_ord, self.active_start, self.active_end
            )
            wait_task, wait_proj = (
                (None, "no_cann")
                if cann_w is None
                else project_cann_to_task(
                    cann_w,
                    API_WAIT,
                    tasks_by_cid,
                    string_ids,
                    record_type,
                    wait_type,
                    self.used_task_rowids,
                )
            )
            if wait_task:
                self.used_task_rowids.add(wait_task.rowid)
                wait_proj = "projected"
            elif cann_w and wait_proj != "no_cann":
                wait_proj = "missing"

            self.nodes[wnid] = DagNode(
                node_id=wnid,
                node_type="event_wait",
                primary_key=f"({wait_rec.pid},{cs})",
                attrs={
                    "call_sequence": cs,
                    "raw_stream": wait_rec.raw_stream,
                    "bound_record_key": record_key_str(rk) if rk else None,
                    "cann_connection_id": cann_w.get("connection_id") if cann_w else None,
                    "cann_rowid": cann_w.get("rowid") if cann_w else None,
                    "a2_candidate_count": cand_w,
                    "task_projection_status": wait_proj,
                    "task_rowid": wait_task.rowid if wait_task else None,
                },
            )

            if record_nid:
                stream_rel = (
                    "same_raw_stream"
                    if record_rec and record_rec.raw_stream == wait_rec.raw_stream
                    else "cross_raw_stream"
                )
                self.causal_edges.append(
                    CausalEdge(
                        edge_id=self._next_edge_id("eg"),
                        src=record_nid,
                        dst=wnid,
                        edge_type="event_generation",
                        evidence_tier="proven_event_generation",
                        identity_source="event_generation",
                        semantic_class=(
                            "same_stream_event_wait"
                            if stream_rel == "same_raw_stream"
                            else "cross_stream_event_wait"
                        ),
                        evidence_refs=[f"a4:wait_cs={cs}"],
                        stream_domain="preload_raw_stream",
                    )
                )

            if wait_proj == "missing" and cann_w:
                self._add_unknown(
                    wnid,
                    "profiler_task_projection",
                    "EVENT_WAIT TASK",
                    "event_task_projection_missing",
                    f"cann_cid={cann_w.get('connection_id')}",
                    "unique EVENT_WAIT TASK at CANN cid",
                    True,
                    True,
                    False,
                )

            # Classification
            rec_task_rowid = self.nodes.get(record_nid or "", DagNode("", "", "")).attrs.get(
                "task_rowid"
            )
            rec_task = next((t for t in all_tasks if t.rowid == rec_task_rowid), None) if rec_task_rowid else None

            if cs in A6_WAIT_CALL_SEQUENCES:
                semantic = "comm_completion_to_compute"
                reason = "a6_frozen_replay"
                a6_replay_cs.add(cs)
                wait_classification_rows.append(
                    {
                        "wait_call_sequence": cs,
                        "semantic_class": semantic,
                        "classification_reason": reason,
                        "record_raw_stream": record_rec.raw_stream if record_rec else None,
                        "wait_raw_stream": wait_rec.raw_stream,
                        "a6_member": True,
                        "blockers": "",
                        "required_evidence": "",
                    }
                )
            else:
                semantic, trace_rows, pred_summary = evaluate_reverse_wait_predicate(
                    cs,
                    wait_rec,
                    record_rec,
                    rec_task,
                    wait_task,
                    gen_info,
                    all_tasks_by_rowid,
                    fifo_fwd,
                    fifo_rev,
                    overlap_pairs,
                    comm_ops,
                    tasks_by_cid_map,
                    string_ids,
                    self.active_start,
                    self.active_end,
                )
                reverse_predicate_trace_rows.extend(trace_rows)
                predicate_summaries.append(pred_summary)
                blockers = [t["condition"] for t in trace_rows if not t["passed"]]
                reason = pred_summary.get("first_blocker") or ";".join(blockers)
                required = (
                    ""
                    if semantic == "compute_ready_to_comm_candidate"
                    else f"blocked_at:{reason}"
                )
                wait_classification_rows.append(
                    {
                        "wait_call_sequence": cs,
                        "semantic_class": semantic,
                        "classification_reason": reason,
                        "record_raw_stream": record_rec.raw_stream if record_rec else None,
                        "wait_raw_stream": wait_rec.raw_stream,
                        "a6_member": False,
                        "blockers": ";".join(blockers),
                        "required_evidence": required,
                        "kernel_predecessor_rowid": pred_summary.get("kernel_predecessor_rowid"),
                        "comm_successor_rowid": pred_summary.get("comm_successor_rowid"),
                        "comm_op_name": pred_summary.get("comm_op_name"),
                    }
                )

        # --- TASK nodes (active) ---
        for t in active_task_rows:
            tname = resolve_string(string_ids, t.task_type) or f"id:{t.task_type}"
            self.nodes[task_node_id(t.rowid)] = DagNode(
                node_id=task_node_id(t.rowid),
                node_type="profiler_task",
                primary_key=str(t.rowid),
                attrs={
                    "connection_id": t.connection_id,
                    "stream_id": t.stream_id,
                    "task_type": tname,
                    "start_ns": t.start_ns,
                    "end_ns": t.end_ns,
                    "in_active_window": True,
                },
            )

        # --- COMM nodes ---
        for op in comm_ops:
            cnid = comm_node_id(op["op_name"])
            self.nodes[cnid] = DagNode(
                node_id=cnid,
                node_type="communication_op",
                primary_key=op["op_name"],
                attrs={
                    "connection_id": op["connection_id"],
                    "start_ns": op["start_ns"],
                    "end_ns": op["end_ns"],
                },
            )
            self._add_unknown(
                cnid,
                "cross_rank_peer",
                "remote_rank_progress",
                "unknown_cross_rank",
                f"comm_cid={op['connection_id']}",
                "multi-rank profiler observation",
                True,
                True,
                False,
            )

        # --- A5 structure edges ---
        for chain in chains:
            if chain.get("reject_reason"):
                continue
            term_rid = chain.get("terminal_rowid")
            rec_rid = chain.get("event_record_rowid")
            if term_rid and rec_rid:
                self.causal_edges.append(
                    CausalEdge(
                        edge_id=self._next_edge_id("a5"),
                        src=task_node_id(term_rid),
                        dst=task_node_id(rec_rid),
                        edge_type="a5_comm_completion_structure",
                        evidence_tier="observed_structural",
                        identity_source="a5_comm_completion_structure",
                        semantic_class="comm_completion_to_compute",
                        evidence_refs=[f"comm:{chain.get('comm_op_name')}"],
                        stream_domain="profiler_streamId",
                    )
                )


        # --- Boundary sentinels ---
        for sid, stasks in stream_tasks.items():
            before = [t for t in stasks if t.start_ns < self.active_start]
            after = [t for t in stasks if t.start_ns > self.active_end]
            if before:
                last = max(before, key=lambda t: (t.start_ns, t.end_ns, t.rowid))
                sentinel = unknown_node_id("window_boundary", f"stream_{sid}_before", 0)
                self.nodes[sentinel] = DagNode(
                    node_id=sentinel,
                    node_type="unknown_stub",
                    primary_key=sentinel,
                    attrs={"role": "boundary_sentinel_before", "stream_id": sid},
                )
            if after:
                first = min(after, key=lambda t: (t.start_ns, t.end_ns, t.rowid))
                sentinel = unknown_node_id("window_boundary", f"stream_{sid}_after", 0)
                self.nodes[sentinel] = DagNode(
                    node_id=sentinel,
                    node_type="unknown_stub",
                    primary_key=sentinel,
                    attrs={"role": "boundary_sentinel_after", "stream_id": sid},
                )

        # --- Host sync observations ---
        for r in cann_rows:
            name = r.get("name") or ""
            if not any(s in name for s in SYNC_API_SUBSTRINGS):
                continue
            if not (self.active_start <= r["start_ns"] <= self.active_end):
                continue
            rid = r.get("rowid")
            if rid is None:
                continue
            hnid = host_sync_node_id(rid)
            pid, tid = global_tid_parts(r["global_tid"])
            self.nodes[hnid] = DagNode(
                node_id=hnid,
                node_type="host_sync_observation",
                primary_key=str(rid),
                attrs={
                    "api_name": name,
                    "pid": pid,
                    "tid": tid,
                    "start_ns": r["start_ns"],
                    "end_ns": r["end_ns"],
                },
            )
            self._add_unknown(
                hnid,
                "host_device_completion",
                "device_stream",
                "synchronize_params_missing",
                f"api={name}",
                "Synchronize raw stream hook",
                True,
                True,
                True,
            )

        # --- Notify TASK: unknown pairing ---
        notify_tasks = [
            t
            for t in active_task_rows
            if "NOTIFY" in (resolve_string(string_ids, t.task_type) or "")
        ]
        for t in notify_tasks:
            self._add_unknown(
                task_node_id(t.rowid),
                "notify_pairing",
                "notify_counterpart",
                "notify_pair_unknown",
                f"task_rowid={t.rowid}",
                "Notify handle epoch hook",
                True,
                True,
                True,
            )

        # --- record_without_wait ---
        all_record_nids = {
            nid
            for nid, n in self.nodes.items()
            if n.node_type == "event_record_generation"
        }
        without_wait = sorted(all_record_nids - record_with_wait)
        record_without_wait_rows = []
        for nid in without_wait:
            n = self.nodes[nid]
            record_without_wait_rows.append(
                {
                    "node_id": nid,
                    "record_key": n.primary_key,
                    "call_sequence": n.attrs.get("call_sequence"),
                    "raw_stream": n.attrs.get("raw_stream"),
                }
            )

        cone_path_rows = build_cone_paths(
            self.nodes,
            self.causal_edges,
            wait_classification_rows,
            predicate_summaries,
            record_without_wait_rows,
            comm_results,
            pair_unknown_specs,
        )
        hook_decision = resolve_hook_decision(self.unknowns, cone_path_rows)

        # --- Reachability ---
        node_ids = set(self.nodes.keys())
        proven_edges = [e for e in self.causal_edges if e.evidence_tier != "unknown"]
        cycles = detect_cycles(proven_edges, node_ids)

        strict_reach, strict_frontier = build_reachability(
            self.nodes,
            self.causal_edges,
            {"proven_event_generation"},
        )
        observed_reach, observed_frontier = build_reachability(
            self.nodes,
            self.causal_edges,
            {"proven_event_generation", "observed_structural"},
        )
        unknown_frontier_strict = build_unknown_frontier(
            self.nodes,
            self.causal_edges,
            self.unknowns,
            {"proven_event_generation"},
            "strict",
        )
        unknown_frontier_observed = build_unknown_frontier(
            self.nodes,
            self.causal_edges,
            self.unknowns,
            {"proven_event_generation", "observed_structural"},
            "observed",
        )
        reachable_rows: list[dict] = []
        for start, reached in strict_reach.items():
            reachable_rows.append(
                {
                    "start_node": start,
                    "mode": "strict",
                    "reachable_count": len(reached),
                    "reachable_nodes": ";".join(sorted(reached)[:50]),
                }
            )
        for start, reached in observed_reach.items():
            reachable_rows.append(
                {
                    "start_node": start,
                    "mode": "observed",
                    "reachable_count": len(reached),
                    "reachable_nodes": ";".join(sorted(reached)[:50]),
                }
            )
        frontier_rows = (
            strict_frontier
            + observed_frontier
            + unknown_frontier_strict
            + unknown_frontier_observed
        )

        # --- Coverage ---
        eg_count = sum(1 for e in self.causal_edges if e.edge_type == "event_generation")
        fifo_count = sum(
            1 for e in self.causal_edges if e.edge_type == "profiler_same_stream_fifo"
        )
        a5_count = sum(
            1 for e in self.causal_edges if e.edge_type == "a5_comm_completion_structure"
        )
        pair_overlap_unknown = fifo_coverage_meta["overlap_pair_count"]
        pair_malformed_unknown = fifo_coverage_meta["malformed_pair_count"]
        adjacent_pair_count = fifo_coverage_meta["adjacent_pair_count"]
        candidate_count = sum(
            1
            for r in wait_classification_rows
            if r.get("semantic_class") == "compute_ready_to_comm_candidate"
        )
        non_a6_accounted = sum(
            1 for r in wait_classification_rows if r["wait_call_sequence"] in NON_A6_WAIT_CS
        )
        coverage = {
            "event_generation": {
                "expected": DENOMINATORS["wait"],
                "proven": eg_count,
                "observed_structural": 0,
                "unknown": 0,
                "not_observed": max(0, DENOMINATORS["wait"] - eg_count),
                "denominator_source": "active_wait_count",
            },
            "profiler_same_stream_fifo": {
                "expected": adjacent_pair_count,
                "proven": 0,
                "observed_structural": fifo_count,
                "unknown": 0,
                "overlap_unknown_pairs": pair_overlap_unknown,
                "malformed_unknown_pairs": pair_malformed_unknown,
                "adjacent_pair_count": adjacent_pair_count,
                "sortable_pair_count": fifo_coverage_meta["sortable_pair_count"],
                "not_observed": 0,
                "denominator_source": "sum_max_task_count_minus_1_per_stream",
                "v1_stream_ambiguous_count": 0,
            },
            "reverse_wait_predicate": {
                "expected": len(NON_A6_WAIT_CS),
                "accounted": non_a6_accounted,
                "compute_ready_to_comm_candidate": candidate_count,
                "unclassified": non_a6_accounted - candidate_count,
            },
            "a5_comm_completion_structure": {
                "expected": DENOMINATORS["allreduce"],
                "proven": 0,
                "observed_structural": a5_count,
                "unknown": 0,
                "not_observed": max(0, DENOMINATORS["allreduce"] - a5_count),
                "denominator_source": "allreduce_count",
            },
        }

        a6_cs_ok = a6_replay_cs == set(A6_WAIT_CALL_SEQUENCES)

        passed, _failures = evaluate_slice_a_acceptance(
            baseline,
            eg_count,
            cycles,
            a6_cs_ok,
            global_reuse_ok,
        )
        fifo_pair_ok = (
            fifo_count > 0
            or adjacent_pair_count == 0
            or (
                fifo_coverage_meta["sortable_pair_count"]
                + pair_overlap_unknown
                + pair_malformed_unknown
                == adjacent_pair_count
            )
        )
        acceptance = {
            "db_sha256_expected": EXPECTED_DB_SHA256,
            "baseline": baseline,
            "contradiction": contradiction,
            "event_generation_edges": eg_count,
            "fifo_edges": fifo_count,
            "fifo_pair_overlap_unknown": pair_overlap_unknown,
            "fifo_pair_malformed_unknown": pair_malformed_unknown,
            "fifo_adjacent_pair_count": adjacent_pair_count,
            "fifo_pair_audit_ok": fifo_pair_ok,
            "reverse_wait_candidate_count": candidate_count,
            "reverse_wait_accounted": non_a6_accounted,
            "hook_decision": hook_decision,
            "record_nodes": sum(
                1 for n in self.nodes.values() if n.node_type == "event_record_generation"
            ),
            "wait_nodes": sum(1 for n in self.nodes.values() if n.node_type == "event_wait"),
            "record_without_wait_count": len(without_wait),
            "record_with_wait_count": len(record_with_wait),
            "a6_replay_cs_ok": a6_cs_ok,
            "a6_replay_cs": sorted(a6_replay_cs),
            "global_wait_reuse_ok": global_reuse_ok,
            "cycle_witness": cycles[:3],
            "has_cycle": len(cycles) > 0,
            "unknown_count": len(self.unknowns),
            "passed": (
                passed
                and len(contradiction) == 0
                and fifo_pair_ok
                and non_a6_accounted == len(NON_A6_WAIT_CS)
            ),
        }
        # fix unknown_by_reason
        ubr: dict[str, int] = defaultdict(int)
        for u in self.unknowns:
            ubr[u.reason_code] += 1
        acceptance["unknown_by_reason"] = dict(ubr)
        acceptance["slice_b_gate"] = self._slice_b_gate()

        return {
            "rank0_pid": rank0_pid,
            "baseline": baseline,
            "contradiction": contradiction,
            "wait_classification_rows": wait_classification_rows,
            "reverse_predicate_trace_rows": reverse_predicate_trace_rows,
            "predicate_summaries": predicate_summaries,
            "fifo_pair_rows": fifo_pair_rows,
            "fifo_coverage_meta": fifo_coverage_meta,
            "cone_path_rows": cone_path_rows,
            "record_without_wait_rows": record_without_wait_rows,
            "reachable_rows": reachable_rows,
            "frontier_rows": frontier_rows,
            "coverage": coverage,
            "acceptance": acceptance,
            "comm_results": comm_results,
            "chains": chains,
            "gen_errors": gen_errors,
        }

    def _slice_b_gate(self) -> dict[str, Any]:
        resolvable = [u for u in self.unknowns if u.slice_b_can_resolve]
        blocks = [u for u in self.unknowns if u.blocks_observed_cone]
        triggered = len(resolvable) > 0 and any(
            u.blocks_observed_cone for u in resolvable
        )
        return {
            "status": "NOT_TRIGGERED" if not triggered else "RECOMMENDED",
            "slice_b_can_resolve_count": len(resolvable),
            "blocks_cone_count": len(blocks),
            "reason_codes": sorted({u.reason_code for u in resolvable}),
        }


def export_graph(
    builder: WaitDagBuilder,
    out_dir: Path,
    db_path: Path,
    trace_dir: Path,
    active_start: int,
    active_end: int,
    run_id: str,
    db_hash: str,
    build_meta: dict[str, Any],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    graph = {
        "schema_version": SCHEMA_VERSION,
        "graph_generation_id": GRAPH_GENERATION_ID,
        "analyzer_version": ANALYZER_VERSION,
        "run_id": run_id,
        "provenance": {
            "db_path": str(db_path),
            "db_sha256": db_hash,
            "trace_dir": str(trace_dir),
            "rank0_pid": build_meta["rank0_pid"],
        },
        "active_window": {"start_ns": active_start, "end_ns": active_end},
        "rank_scope": "rank0",
        "denominators": DENOMINATORS,
        "nodes": [asdict(n) for n in builder.nodes.values()],
        "causal_edges": [asdict(e) for e in builder.causal_edges],
        "identity_links": [asdict(l) for l in builder.identity_links],
        "unknowns": [asdict(u) for u in builder.unknowns],
        "coverage": build_meta["coverage"],
        "claims": {
            "allowed": build_allowed_claims(
                build_meta["coverage"],
                build_meta.get("acceptance", {}).get("reverse_wait_candidate_count", 0),
            ),
            "forbidden": list(FORBIDDEN_CLAIMS),
        },
        "acceptance": build_meta["acceptance"],
    }
    (out_dir / "graph.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False))

    write_csv(
        out_dir / "nodes.csv",
        [
            {"node_id": n.node_id, "node_type": n.node_type, "primary_key": n.primary_key, **n.attrs}
            for n in builder.nodes.values()
        ],
    )
    write_csv(out_dir / "edges.csv", [asdict(e) for e in builder.causal_edges])
    write_csv(out_dir / "identity_links.csv", [asdict(l) for l in builder.identity_links])
    write_csv(out_dir / "unknowns.csv", [asdict(u) for u in builder.unknowns])
    (out_dir / "coverage.json").write_text(
        json.dumps(build_meta["coverage"], indent=2, ensure_ascii=False)
    )
    write_csv(out_dir / "record_without_wait.csv", build_meta["record_without_wait_rows"])
    write_csv(out_dir / "wait_classification.csv", build_meta["wait_classification_rows"])
    write_csv(out_dir / "fifo_pair_audit.csv", build_meta.get("fifo_pair_rows", []))
    write_csv(
        out_dir / "reverse_wait_predicate_trace.csv",
        build_meta.get("reverse_predicate_trace_rows", []),
    )
    write_csv(out_dir / "cone_paths.csv", build_meta.get("cone_path_rows", []))
    write_csv(out_dir / "reachable_nodes.csv", build_meta["reachable_rows"])
    write_csv(out_dir / "unknown_frontier.csv", build_meta["frontier_rows"])

    casebook = build_wait_dag_v2_casebook(
        builder.nodes,
        builder.causal_edges,
        builder.unknowns,
        build_meta["wait_classification_rows"],
        build_meta["record_without_wait_rows"],
        build_meta["comm_results"],
        build_meta["acceptance"],
        build_meta.get("fifo_coverage_meta", {}),
        build_meta.get("predicate_summaries", []),
    )
    (out_dir / "wait_dag_casebook.md").write_text(casebook, encoding="utf-8")
    write_schema_md(out_dir / "schema.md")
    write_claims_md(out_dir / "claims.md", build_meta["acceptance"], build_meta["coverage"])


def run(args: argparse.Namespace) -> int:
    trace_dir = Path(args.trace_dir)
    db_path = Path(args.db_path)
    analysis_dir = Path(args.analysis_dir)
    log_dir = Path(args.log_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    db_hash_before = sha256_file(db_path)
    if db_hash_before != EXPECTED_DB_SHA256:
        msg = f"DB hash mismatch: {db_hash_before} != {EXPECTED_DB_SHA256}"
        (log_dir / "contradiction.log").write_text(msg)
        print(msg, file=sys.stderr)
        return 2

    if args.profile_window:
        active_start, active_end = load_profile_window(Path(args.profile_window))
    else:
        pw = trace_dir.parent / "profiler" / "profile_window.json"
        if not pw.exists():
            pw = trace_dir.parent / "profile_window.json"
        if pw.exists():
            active_start, active_end = load_profile_window(pw)
        else:
            active_start, active_end = FROZEN_ACTIVE_WINDOW

    if (active_start, active_end) != FROZEN_ACTIVE_WINDOW:
        msg = f"active window drift: {(active_start, active_end)}"
        (log_dir / "contradiction.log").write_text(msg)
        print(msg, file=sys.stderr)
        return 2

    builder = WaitDagBuilder(
        trace_dir=trace_dir,
        db_path=db_path,
        active_start=active_start,
        active_end=active_end,
        pid_min=args.pid_min,
        pid_max=args.pid_max,
        run_id=args.run_id,
    )
    build_meta = builder.build()

    if build_meta["contradiction"]:
        (log_dir / "contradiction.log").write_text(
            "\n".join(build_meta["contradiction"])
        )
        export_graph(
            builder,
            analysis_dir,
            db_path,
            trace_dir,
            active_start,
            active_end,
            args.run_id,
            db_hash_before,
            build_meta,
        )
        return 3

    export_graph(
        builder,
        analysis_dir,
        db_path,
        trace_dir,
        active_start,
        active_end,
        args.run_id,
        db_hash_before,
        build_meta,
    )

    db_hash_after = sha256_file(db_path)
    acceptance = build_meta["acceptance"]
    acceptance["db_sha256_after"] = db_hash_after
    acceptance["db_unchanged"] = db_hash_before == db_hash_after
    (analysis_dir / "acceptance.json").write_text(
        json.dumps(acceptance, indent=2, ensure_ascii=False)
    )
    (log_dir / "acceptance.log").write_text(json.dumps(acceptance, indent=2))

    analysis_summary = {
        "run_id": args.run_id,
        "acceptance": acceptance,
        "coverage": build_meta["coverage"],
        "db_sha256_before": db_hash_before,
        "db_sha256_after": db_hash_after,
        "nodes": len(builder.nodes),
        "causal_edges": len(builder.causal_edges),
        "unknowns": len(builder.unknowns),
    }
    analysis_text = json.dumps(analysis_summary, indent=2, ensure_ascii=False)
    print(analysis_text)

    if not acceptance["passed"] or db_hash_after != EXPECTED_DB_SHA256:
        return 1
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--trace-dir", required=True)
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--analysis-dir", required=True)
    ap.add_argument("--log-dir", required=True)
    ap.add_argument("--profile-window", default="")
    ap.add_argument("--pid-min", type=int, default=26025)
    ap.add_argument("--pid-max", type=int, default=26040)
    args = ap.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""D51 Wait DAG V4.2: fresh-run reverse candidate extraction (no A6 builder)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from analyze_event_pairs import (
    RECORD_OP,
    WAIT_OP,
    TaskRow,
    align_api,
    discover_event_task_types,
    freeze_compute_streams,
    load_cann_api,
    load_comm_ops,
    load_string_ids,
    load_tasks,
    pick_rank0_trace,
    rebuild_generations,
    resolve_string,
    stream_tasks_by_id,
)
from event_preload_v6_analyze import build_cann_ordinal_maps_with_rowid
from wait_dag_v2_build import (
    active_records,
    active_waits,
    align_cann_for_record,
    align_cann_for_wait,
    enrich_cann_rowids,
    load_cann_rowids,
    project_cann_to_task,
)
from wait_dag_schema import CausalEdge
from wait_dag_v2_fifo import (
    build_fifo_adjacency,
    build_pair_level_fifo,
    comm_op_for_task,
    first_comm_entry_on_stream,
    nearest_kernel_predecessor,
    unique_fifo_successors,
)

API_RECORD = "aclrtRecordEvent"
API_WAIT = "aclrtStreamWaitEvent"
RECORD_TID_ROLE = "AFTER_ARM_FIRST_SUCCESSFUL_RECORD_TID"
SCHEMA = "d51_reverse_candidate_v1"
EXTRACTOR_VERSION = "wait_dag_v4_2_reverse_candidate_v1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    if fieldnames is None:
        fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def load_profile_window(path: Path) -> tuple[int, int]:
    w = json.loads(path.read_text())
    return int(w["active_start_realtime_ns"]), int(w["active_end_realtime_ns"])


def record_ord_on_thread(
    pre_rec: Any, rank0_records: list, active_start: int, active_end: int
) -> int:
    return sum(
        1
        for r in rank0_records
        if r.op == RECORD_OP
        and r.acl_ret == 0
        and r.pid == pre_rec.pid
        and r.tid == pre_rec.tid
        and active_start <= r.enter_realtime_ns <= active_end
        and r.enter_realtime_ns < pre_rec.enter_realtime_ns
    )


def wait_ord_on_thread(
    pre_wait: Any, rank0_records: list, active_start: int, active_end: int
) -> int:
    return sum(
        1
        for r in rank0_records
        if r.op == WAIT_OP
        and r.acl_ret == 0
        and r.pid == pre_wait.pid
        and r.tid == pre_wait.tid
        and active_start <= r.enter_realtime_ns <= active_end
        and r.enter_realtime_ns < pre_wait.enter_realtime_ns
    )


def comm_stream_ids(
    comm_ops: list[dict],
    tasks_by_cid: dict[int, list[TaskRow]],
    active_start: int,
    active_end: int,
) -> set[int]:
    streams: set[int] = set()
    for op in comm_ops:
        if not str(op.get("op_name", "")).startswith("hcom_allReduce_"):
            continue
        cid = int(op["connection_id"])
        for t in tasks_by_cid.get(cid, []):
            if active_start <= t.start_ns <= active_end:
                streams.add(t.stream_id)
    return streams


def is_a6_polarity(
    record_task: TaskRow,
    wait_task: TaskRow,
    compute_streams: set[int],
    comm_streams: set[int],
) -> bool:
    """A6: comm-side Record stream -> compute-side Wait stream."""
    return (
        record_task.stream_id in comm_streams
        and wait_task.stream_id in compute_streams
        and record_task.stream_id != wait_task.stream_id
    )


@dataclass
class RunContext:
    run_dir: Path
    run_id: str
    db_path: Path
    active_start: int
    active_end: int
    rank0_pid: int
    rank0_records: list
    gen_info: dict
    cann_rows: list[dict]
    cann_by_ord: dict
    string_ids: dict[int, str]
    record_type: int
    wait_type: int
    all_tasks: list[TaskRow]
    all_tasks_by_rowid: dict[int, TaskRow]
    tasks_by_cid: dict[int, list[TaskRow]]
    comm_ops: list[dict]
    compute_streams: set[int]
    comm_streams: set[int]
    fifo_fwd: dict[int, list[int]]
    fifo_rev: dict[int, list[int]]
    overlap_pairs: set[tuple[int, int]]


def build_run_context(run_dir: Path) -> RunContext:
    prof_dir = run_dir / "out" / "args_on"
    db_files = list(prof_dir.glob("**/ascend_pytorch_profiler_0.db"))
    if not db_files:
        raise FileNotFoundError(f"no profiler db under {prof_dir}")
    db_path = db_files[0]
    pw_path = prof_dir / "profile_window.json"
    active_start, active_end = load_profile_window(pw_path)

    rank0_meta, rank0_records, _ = pick_rank0_trace(run_dir / "event_trace")
    rank0_pid = int(rank0_meta["pid"])
    _, _, gen_info = rebuild_generations(rank0_records)

    cann_rows = load_cann_api(db_path)
    rowid_map = load_cann_rowids(db_path)
    enrich_cann_rowids(cann_rows, rowid_map)
    align_api(rank0_records, cann_rows, {API_RECORD, API_WAIT}, active_start, active_end)
    cann_by_ord = build_cann_ordinal_maps_with_rowid(db_path, cann_rows, active_start, active_end)

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    string_ids = load_string_ids(con.cursor())
    con.close()
    record_type, wait_type, _, _ = discover_event_task_types(
        db_path, active_start, active_end, string_ids
    )
    all_tasks = load_tasks(db_path)
    all_tasks_by_rowid = {t.rowid: t for t in all_tasks}
    tasks_by_cid: dict[int, list[TaskRow]] = {}
    for t in all_tasks:
        tasks_by_cid.setdefault(t.connection_id, []).append(t)
    comm_ops = load_comm_ops(db_path, active_start, active_end)
    compute_streams = freeze_compute_streams(db_path, active_start, active_end, string_ids)
    comm_streams = comm_stream_ids(comm_ops, tasks_by_cid, active_start, active_end)

    stream_tasks = stream_tasks_by_id(all_tasks)
    _, fifo_pair_rows, _, _ = build_pair_level_fifo(
        stream_tasks, active_start, active_end, lambda _k: ""
    )
    overlap_pairs = {
        (int(r["src_rowid"]), int(r["dst_rowid"]))
        for r in fifo_pair_rows
        if r.get("verdict") == "overlap"
    }
    fifo_edges = [
        CausalEdge(
            edge_id=f"fifo:{a}:{b}",
            src=f"task:{a}",
            dst=f"task:{b}",
            edge_type="profiler_same_stream_fifo",
            evidence_tier="observed_structural",
            identity_source="profiler_same_stream_fifo",
            semantic_class="unclassified",
            reason_code="adjacent_nonoverlap",
            stream_domain="",
            evidence_refs=[],
        )
        for a, b in (
            (int(r["src_rowid"]), int(r["dst_rowid"]))
            for r in fifo_pair_rows
            if r.get("verdict") == "sortable"
        )
    ]
    fifo_fwd, fifo_rev = build_fifo_adjacency(fifo_edges)

    return RunContext(
        run_dir=run_dir,
        run_id="",
        db_path=db_path,
        active_start=active_start,
        active_end=active_end,
        rank0_pid=rank0_pid,
        rank0_records=rank0_records,
        gen_info=gen_info,
        cann_rows=cann_rows,
        cann_by_ord=cann_by_ord,
        string_ids=string_ids,
        record_type=record_type,
        wait_type=wait_type,
        all_tasks=all_tasks,
        all_tasks_by_rowid=all_tasks_by_rowid,
        tasks_by_cid=tasks_by_cid,
        comm_ops=comm_ops,
        compute_streams=compute_streams,
        comm_streams=comm_streams,
        fifo_fwd=fifo_fwd,
        fifo_rev=fifo_rev,
        overlap_pairs=overlap_pairs,
    )


def enumerate_generation_candidates(ctx: RunContext) -> list[dict[str, Any]]:
    wait_bindings = ctx.gen_info.get("wait_bindings", {})
    record_keys = ctx.gen_info.get("record_keys", {})
    waits = {w.call_sequence: w for w in active_waits(ctx.rank0_records, ctx.active_start, ctx.active_end)}
    records = {r.call_sequence: r for r in active_records(ctx.rank0_records, ctx.active_start, ctx.active_end)}
    candidates: list[dict[str, Any]] = []

    for wait_cs, rk in wait_bindings.items():
        wait_rec = waits.get(wait_cs)
        if wait_rec is None:
            continue
        record_cs = next((cs for cs, key in record_keys.items() if key == rk), None)
        if record_cs is None:
            continue
        record_rec = records.get(record_cs)
        if record_rec is None:
            continue
        candidates.append(
            {
                "wait_cs": wait_cs,
                "record_cs": record_cs,
                "record_rec": record_rec,
                "wait_rec": wait_rec,
                "record_key": rk,
            }
        )
    return candidates


def evaluate_candidate(
    ctx: RunContext,
    cand: dict[str, Any],
    target_comm: str,
) -> tuple[bool, list[dict], dict[str, Any]]:
    wait_cs = int(cand["wait_cs"])
    record_rec = cand["record_rec"]
    wait_rec = cand["wait_rec"]
    trace: list[dict] = []

    def row(cond: str, passed: bool, detail: str = "", **extra: Any) -> None:
        trace.append(
            {
                "wait_call_sequence": wait_cs,
                "record_call_sequence": cand["record_cs"],
                "condition": cond,
                "passed": passed,
                "detail": detail,
                **extra,
            }
        )

    # C1: complete generation with unique Wait binding in window
    c1 = cand["record_key"] is not None and wait_cs in ctx.gen_info.get("wait_bindings", {})
    row("C1", c1, f"generation_bound={c1}")

    rec_ord = record_ord_on_thread(record_rec, ctx.rank0_records, ctx.active_start, ctx.active_end)
    wait_ord = wait_ord_on_thread(wait_rec, ctx.rank0_records, ctx.active_start, ctx.active_end)
    rec_cann, _ = align_cann_for_record(
        record_rec, ctx.rank0_records, ctx.cann_by_ord, ctx.active_start, ctx.active_end
    )
    wait_cann, _ = align_cann_for_wait(
        wait_rec, ctx.rank0_records, ctx.cann_by_ord, ctx.active_start, ctx.active_end
    )
    used: set[int] = set()
    record_task = None
    wait_task = None
    if rec_cann:
        record_task, rec_proj = project_cann_to_task(
            rec_cann,
            API_RECORD,
            ctx.tasks_by_cid,
            ctx.string_ids,
            ctx.record_type,
            ctx.wait_type,
            used,
        )
        if rec_proj != "ok":
            record_task = None
    if wait_cann:
        wait_task, wait_proj = project_cann_to_task(
            wait_cann,
            API_WAIT,
            ctx.tasks_by_cid,
            ctx.string_ids,
            ctx.record_type,
            ctx.wait_type,
            used,
        )
        if wait_proj != "ok":
            wait_task = None

    # C2: compute Record -> comm Wait (opposite of A6)
    c2 = False
    c2_detail = "tasks_missing"
    if record_task is not None and wait_task is not None:
        reverse_polarity = (
            record_task.stream_id in ctx.compute_streams
            and wait_task.stream_id in ctx.comm_streams
            and record_task.stream_id != wait_task.stream_id
        )
        a6 = is_a6_polarity(record_task, wait_task, ctx.compute_streams, ctx.comm_streams)
        c2 = reverse_polarity and not a6
        c2_detail = (
            f"record_stream={record_task.stream_id} wait_stream={wait_task.stream_id} "
            f"compute={record_task.stream_id in ctx.compute_streams} "
            f"comm={wait_task.stream_id in ctx.comm_streams} a6={a6}"
        )
    row("C2", c2, c2_detail)

    # C3: bidirectional unique projection
    c3 = record_task is not None and wait_task is not None
    row(
        "C3",
        c3,
        f"record_task={getattr(record_task, 'rowid', None)} wait_task={getattr(wait_task, 'rowid', None)}",
    )

    # C4
    kernel_task = None
    kernel_rowids: list[int] = []
    c4 = False
    c4_blocker = ""
    if record_task is not None:
        kernel_task, c4_blocker, kernel_rowids = nearest_kernel_predecessor(
            record_task,
            ctx.all_tasks_by_rowid,
            ctx.fifo_rev,
            ctx.overlap_pairs,
            ctx.string_ids,
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
        comm_succ, c5_blocker = unique_fifo_successors(wait_task.rowid, ctx.fifo_fwd)
        c5 = c5_blocker == "ok"
    else:
        c5_blocker = "wait_task_missing"
    row(
        "C5",
        c5,
        c5_blocker,
        comm_successor_rowid=comm_succ[0] if len(comm_succ) == 1 else None,
    )

    # C6
    comm_op = None
    comm_entry = None
    c6 = False
    c6_blocker = ""
    comm_candidates: list[str] = []
    if c5 and comm_succ:
        q = ctx.all_tasks_by_rowid.get(comm_succ[0])
        if q is None:
            c6_blocker = "successor_task_missing"
        else:
            comm_op, memb_blocker, comm_candidates = comm_op_for_task(
                q, ctx.comm_ops, ctx.tasks_by_cid, ctx.string_ids
            )
            if comm_op is None:
                c6_blocker = memb_blocker
            elif comm_op.get("op_name") != target_comm:
                c6_blocker = f"comm_op_mismatch:{comm_op.get('op_name')}"
            else:
                comm_entry, entry_blocker, _ = first_comm_entry_on_stream(
                    comm_op,
                    ctx.tasks_by_cid,
                    q.stream_id,
                    ctx.active_start,
                    ctx.active_end,
                )
                if comm_entry is None or comm_entry.rowid != q.rowid:
                    c6_blocker = (
                        entry_blocker if comm_entry is None else "q_not_first_comm_entry"
                    )
                else:
                    c6 = True
    else:
        c6_blocker = "c5_failed"
    row(
        "C6",
        c6,
        c6_blocker,
        comm_op_name=comm_op.get("op_name") if comm_op else None,
        comm_entry_rowid=comm_entry.rowid if comm_entry else None,
        comm_op_candidates=";".join(comm_candidates),
    )

    # C7 composite
    c7 = c1 and c2 and c3 and c4 and c5 and c6
    c7_blocker = ""
    if not c7:
        for cond, ok in [("C1", c1), ("C2", c2), ("C3", c3), ("C4", c4), ("C5", c5), ("C6", c6)]:
            if not ok:
                c7_blocker = f"first_fail={cond}"
                break
    row("C7", c7, c7_blocker or "composite_path_ok")

    # C8 no tie-break
    c8 = c7 and len(kernel_rowids) <= 1 and len(comm_succ) <= 1 and len(comm_candidates) <= 1
    row("C8", c8, "no_multi_candidate_tiebreak" if c8 else "multi_candidate_or_prior_fail")

    all_pass = c1 and c2 and c3 and c4 and c5 and c6 and c7 and c8
    summary = {
        "wait_call_sequence": wait_cs,
        "record_call_sequence": cand["record_cs"],
        "all_pass": all_pass,
        "first_blocker": next((t["condition"] for t in trace if not t["passed"]), ""),
        "record_active_success_ordinal": rec_ord,
        "wait_active_success_ordinal": wait_ord,
        "record_task_rowid": record_task.rowid if record_task else None,
        "wait_task_rowid": wait_task.rowid if wait_task else None,
        "kernel_predecessor_rowid": kernel_task.rowid if kernel_task else None,
        "comm_successor_rowid": comm_succ[0] if len(comm_succ) == 1 else None,
        "comm_op_name": comm_op.get("op_name") if comm_op else None,
        "comm_entry_rowid": comm_entry.rowid if comm_entry else None,
        "record_stream_id": record_task.stream_id if record_task else None,
        "wait_stream_id": wait_task.stream_id if wait_task else None,
        "record_rec": record_rec,
        "wait_rec": wait_rec,
        "record_task": record_task,
        "wait_task": wait_task,
        "kernel_task": kernel_task,
        "comm_entry": comm_entry,
        "comm_op": comm_op,
        "fifo_successor_rowid": comm_succ[0] if len(comm_succ) == 1 else None,
        "is_a6_polarity": (
            is_a6_polarity(record_task, wait_task, ctx.compute_streams, ctx.comm_streams)
            if record_task and wait_task
            else False
        ),
    }
    return all_pass, trace, summary


def build_normalized_key(summary: dict[str, Any], target_comm: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "rank": 0,
        "comm_op": target_comm,
        "record_stream_role": "compute",
        "wait_stream_role": "comm",
        "record_api": API_RECORD,
        "record_tid_role": RECORD_TID_ROLE,
        "record_active_success_ordinal": summary["record_active_success_ordinal"],
        "event_generation_role": "UNIQUE_REVERSE_CANDIDATE_FOR_TARGET_COMM",
        "wait_tid_role": "SAME_ISSUING_THREAD_AS_SELECTED_GENERATION",
        "wait_active_success_ordinal": summary["wait_active_success_ordinal"],
        "upstream_kernel_role": "UNIQUE_ORIGINAL_KERNEL_BEFORE_TARGET_RECORD",
        "comm_entry_role": "FIRST_SORTABLE_ENTRY_AFTER_TARGET_WAIT",
    }


def build_run_local_evidence(summary: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    record_rec = summary.get("record_rec")
    wait_rec = summary.get("wait_rec")
    rk = summary.get("record_key") if "record_key" in summary else None
    return {
        "record_call_sequence": summary.get("record_call_sequence"),
        "wait_call_sequence": summary.get("wait_call_sequence"),
        "record_task_rowid": summary.get("record_task_rowid"),
        "wait_task_rowid": summary.get("wait_task_rowid"),
        "record_stream_id": summary.get("record_stream_id"),
        "wait_stream_id": summary.get("wait_stream_id"),
        "kernel_predecessor_rowid": summary.get("kernel_predecessor_rowid"),
        "comm_entry_rowid": summary.get("comm_entry_rowid"),
        "record_pid": getattr(record_rec, "pid", None),
        "record_tid": getattr(record_rec, "tid", None),
        "wait_pid": getattr(wait_rec, "pid", None),
        "wait_tid": getattr(wait_rec, "tid", None),
        "record_raw_stream": getattr(record_rec, "raw_stream", None),
        "wait_raw_stream": getattr(wait_rec, "raw_stream", None),
        "db_path": str(ctx.db_path),
        "rank0_pid": ctx.rank0_pid,
        "record_key": str(rk) if rk is not None else None,
    }


def extract_reverse_candidates(
    run_dir: Path,
    *,
    run_id: str = "",
    target_comm: str = "hcom_allReduce__612_0_1",
    block: str = "b1",
) -> dict[str, Any]:
    ctx = build_run_context(run_dir)
    ctx.run_id = run_id
    base_candidates = enumerate_generation_candidates(ctx)

    ledger_rows: list[dict] = []
    trace_rows: list[dict] = []
    passing: list[dict[str, Any]] = []

    for cand in base_candidates:
        ok, trace, summary = evaluate_candidate(ctx, cand, target_comm)
        summary["record_key"] = cand["record_key"]
        trace_rows.extend(trace)
        ledger_rows.append(
            {
                "wait_call_sequence": cand["wait_cs"],
                "record_call_sequence": cand["record_cs"],
                "target_comm": target_comm,
                "all_pass": ok,
                "first_blocker": summary.get("first_blocker", ""),
                "comm_op_name": summary.get("comm_op_name"),
                "record_ordinal": summary.get("record_active_success_ordinal"),
                "wait_ordinal": summary.get("wait_active_success_ordinal"),
                "is_a6_polarity": summary.get("is_a6_polarity"),
            }
        )
        if ok:
            passing.append(summary)

    target_passing = [p for p in passing if p.get("comm_op_name") == target_comm]
    status = "OK" if len(target_passing) == 1 else "REVERSE_CANDIDATE_NOT_UNIQUE"
    witness = None
    manifest = None
    normalized_key = None

    if len(target_passing) == 1:
        winner = target_passing[0]
        normalized_key = build_normalized_key(winner, target_comm)
        witness = {
            "schema": SCHEMA,
            "run_id": run_id,
            "target_comm": target_comm,
            "normalized_key": normalized_key,
            "run_local_evidence": build_run_local_evidence(winner, ctx),
            "chain": {
                "upstream_kernel_rowid": winner.get("kernel_predecessor_rowid"),
                "record_task_rowid": winner.get("record_task_rowid"),
                "wait_task_rowid": winner.get("wait_task_rowid"),
                "fifo_successor_rowid": winner.get("fifo_successor_rowid"),
                "comm_entry_rowid": winner.get("comm_entry_rowid"),
                "comm_op_name": winner.get("comm_op_name"),
            },
        }
        witness_text = json.dumps(witness, sort_keys=True, default=str)
        manifest = {
            "schema": SCHEMA,
            "block": block,
            "source_run_id": run_id,
            "source_run_dir": str(run_dir),
            "target_comm": target_comm,
            "candidate_count": 1,
            "normalized_key": normalized_key,
            "record_active_success_ordinal": winner["record_active_success_ordinal"],
            "wait_active_success_ordinal": winner["wait_active_success_ordinal"],
            "input_hashes": {"db_sha256": sha256_file(ctx.db_path)},
            "extractor_hash": sha256_text(EXTRACTOR_VERSION),
            "candidate_witness_hash": sha256_text(witness_text),
            "run_local_evidence": build_run_local_evidence(winner, ctx),
        }

    return {
        "status": status,
        "candidate_count": len(target_passing),
        "total_evaluated": len(base_candidates),
        "passing_count": len(passing),
        "ledger_rows": ledger_rows,
        "trace_rows": trace_rows,
        "witness": witness,
        "manifest": manifest,
        "normalized_key": normalized_key,
        "winner": target_passing[0] if len(target_passing) == 1 else None,
        "ctx": ctx,
    }


def write_casebook(path: Path, result: dict[str, Any], target_comm: str) -> None:
    lines = [
        "# D51 Wait DAG V4.2 reverse candidate casebook",
        "",
        f"- status: **{result['status']}**",
        f"- target_comm: `{target_comm}`",
        f"- candidate_count (target comm): **{result['candidate_count']}**",
        f"- generations evaluated: {result['total_evaluated']}",
        "",
    ]
    if result["candidate_count"] != 1:
        blockers = [r for r in result["ledger_rows"] if r.get("comm_op_name") == target_comm or r.get("all_pass")]
        lines.append("## Blockers (first per failing candidate)")
        for r in result["ledger_rows"]:
            if r.get("comm_op_name") == target_comm and not r.get("all_pass"):
                lines.append(
                    f"- cs={r['wait_call_sequence']}: {r.get('first_blocker')}"
                )
        if result["candidate_count"] > 1:
            lines.append("\n## Multiple passing candidates")
            for r in result["ledger_rows"]:
                if r.get("all_pass") and r.get("comm_op_name") == target_comm:
                    lines.append(
                        f"- record_ord={r.get('record_ordinal')} wait_ord={r.get('wait_ordinal')} cs={r['wait_call_sequence']}"
                    )
    else:
        w = result["witness"]
        lines.append("## Unique witness")
        lines.append(f"```json\n{json.dumps(w, indent=2, default=str)}\n```")
    path.write_text("\n".join(lines) + "\n")


def run_extraction(
    run_dir: Path,
    out_dir: Path,
    *,
    run_id: str,
    target_comm: str,
    block: str,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = extract_reverse_candidates(
        run_dir, run_id=run_id, target_comm=target_comm, block=block
    )
    write_csv(out_dir / "reverse_candidate_ledger.csv", result["ledger_rows"])
    write_csv(out_dir / "reverse_candidate_predicate_trace.csv", result["trace_rows"])
    if result["witness"]:
        (out_dir / "reverse_candidate_witness.json").write_text(
            json.dumps(result["witness"], indent=2, default=str) + "\n"
        )
    write_casebook(out_dir / "reverse_candidate_casebook.md", result, target_comm)
    if result["manifest"]:
        (out_dir / f"selector_manifest_{block}.json").write_text(
            json.dumps(result["manifest"], indent=2) + "\n"
        )
    summary = {
        "status": result["status"],
        "candidate_count": result["candidate_count"],
        "target_comm": target_comm,
        "run_id": run_id,
        "normalized_key": result["normalized_key"],
    }
    (out_dir / "reverse_candidate_summary.json").write_text(
        json.dumps(summary, indent=2, default=str) + "\n"
    )
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", required=True)
    p.add_argument("--run-id", default="")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--target-comm", default="hcom_allReduce__612_0_1")
    p.add_argument("--block", default="b1")
    args = p.parse_args()
    result = run_extraction(
        Path(args.run_dir),
        Path(args.out_dir),
        run_id=args.run_id,
        target_comm=args.target_comm,
        block=args.block,
    )
    print(json.dumps({"status": result["status"], "candidate_count": result["candidate_count"]}))
    if result["status"] != "OK":
        sys.exit(2)


if __name__ == "__main__":
    main()

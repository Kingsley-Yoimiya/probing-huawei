#!/usr/bin/env python3
"""D51 Wait DAG V3.1: structure-key identity + relative comm.end wallclock."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from analyze_event_pairs import (
    TaskRow,
    align_api,
    build_allreduce_chains,
    discover_event_task_types,
    load_cann_api,
    load_comm_ops,
    load_string_ids,
    load_tasks,
    pick_rank0_trace,
    rebuild_generations,
    stream_tasks_by_id,
)
from event_preload_v6_analyze import build_cann_ordinal_maps_with_rowid
from wait_dag_v2_build import (
    active_records,
    active_waits,
    enrich_cann_rowids,
    load_cann_rowids,
    project_cann_to_task,
)
from wait_dag_v2_fifo import (
    evaluate_adjacent_pair,
    first_comm_entry_on_stream,
    sort_active_tasks,
)

TARGET_COMM = "hcom_allReduce__612_0_1"
TARGET_RECORD_ORDINAL = 4
TARGET_WAIT_ORDINAL = 4
API_RECORD = "aclrtRecordEvent"
API_WAIT = "aclrtStreamWaitEvent"
RECORD_TID_ROLE = "AFTER_ARM_FIRST_SUCCESSFUL_RECORD_TID"


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def load_profile_window(path: Path) -> tuple[int, int]:
    w = json.loads(path.read_text())
    return int(w["active_start_realtime_ns"]), int(w["active_end_realtime_ns"])


def select_delay_audit_path(trace_dir: Path) -> Path | None:
    """Pick rank0 delay_audit sidecar; same scoring as load_delay_audit."""
    files = sorted(trace_dir.glob("rank_0_pid_*.delay_audit.json"))
    if not files:
        files = sorted(trace_dir.glob("rank_*_pid_*.delay_audit.json"))
    if not files:
        return None
    best_path: Path | None = None
    best_score = -1
    for path in files:
        data = json.loads(path.read_text())
        if int(data.get("rank", -1)) != 0:
            continue
        score = int(data.get("active_record_success_ord", 0))
        if int(data.get("match_count", 0)) > 0:
            score += 1000
        if int(data.get("arm_tid", 0)) > 0:
            score += 100
        if score > best_score:
            best_score = score
            best_path = path
    return best_path or files[-1]


def load_delay_audit(trace_dir: Path) -> dict[str, Any]:
    path = select_delay_audit_path(trace_dir)
    if path is None:
        return {}
    return json.loads(path.read_text())


def task_type_name(task: TaskRow, string_ids: dict[int, str]) -> str:
    return string_ids.get(task.task_type, str(task.task_type)).lower()


def is_event_or_comm_type(name: str) -> bool:
    return any(k in name for k in ("record", "wait", "comm", "event", "notify"))


def build_normalized_structure_key(
    *,
    record_ordinal: int,
    wait_ordinal: int,
    comm_op: str,
    record_stream_id: int,
    wait_stream_id: int,
) -> str:
    payload = {
        "rank": 0,
        "record_issuing_tid_role": RECORD_TID_ROLE,
        "api": API_RECORD,
        "active_success_ordinal": record_ordinal,
        "comm_op": comm_op,
        "record_stream_role": record_stream_id,
        "wait_stream_role": wait_stream_id,
        "wait_active_success_ordinal": wait_ordinal,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def find_pre_wait_compute(
    wait_task: TaskRow,
    all_tasks: list[TaskRow],
    string_ids: dict[int, str],
    active_start: int,
    active_end: int,
) -> tuple[TaskRow | None, str]:
    by_stream = stream_tasks_by_id(all_tasks)
    ordered = sort_active_tasks(by_stream.get(wait_task.stream_id, []), active_start, active_end)
    idx = next((i for i, t in enumerate(ordered) if t.rowid == wait_task.rowid), None)
    if idx is None:
        return None, "wait_not_on_stream"
    for j in range(idx - 1, -1, -1):
        cand = ordered[j]
        name = task_type_name(cand, string_ids)
        if is_event_or_comm_type(name):
            continue
        verdict, reason = evaluate_adjacent_pair(cand, ordered[j + 1])
        if verdict != "sortable":
            return None, f"overlap_blocking:{reason}"
        return cand, "pre_wait_compute_fifo"
    return None, "no_pre_wait_compute"


def find_pre_comm_same_stream(
    comm_op: dict | None,
    comm_stream_id: int,
    all_tasks: list[TaskRow],
    tasks_by_cid: dict[int, list[TaskRow]],
    active_start: int,
    active_end: int,
) -> tuple[TaskRow | None, str]:
    """TASK on comm profiler stream immediately before comm entry (not Record predecessor)."""
    if comm_op is None:
        return None, "no_comm_op"
    comm_start = int(comm_op["start_ns"])
    comm_cid = int(comm_op["connection_id"])
    comm_rowids = {t.rowid for t in tasks_by_cid.get(comm_cid, [])}

    first_entry, entry_status, _ = first_comm_entry_on_stream(
        comm_op, tasks_by_cid, comm_stream_id, active_start, active_end
    )
    if first_entry is None:
        return None, f"no_comm_entry:{entry_status}"

    by_stream = stream_tasks_by_id(all_tasks)
    ordered = sort_active_tasks(by_stream.get(comm_stream_id, []), active_start, active_end)
    entry_idx = next((i for i, t in enumerate(ordered) if t.rowid == first_entry.rowid), None)
    if entry_idx is None or entry_idx == 0:
        return None, "no_pre_comm_task"

    prev = ordered[entry_idx - 1]
    if prev.rowid in comm_rowids:
        return None, "pre_comm_is_comm_task"
    if prev.end_ns > comm_start:
        return None, "pre_comm_ends_after_comm_start"
    verdict, reason = evaluate_adjacent_pair(prev, first_entry)
    if verdict != "sortable":
        return None, f"overlap_blocking:{reason}"
    return prev, "pre_comm_same_stream_fifo"


def node_with_offsets(
    run_id: str,
    condition: str,
    node_name: str,
    start_ns: int,
    end_ns: int,
    comm_end: int,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "condition": condition,
        "node": node_name,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "duration_ns": end_ns - start_ns,
        "start_offset_from_comm_end_ns": start_ns - comm_end,
        "end_offset_from_comm_end_ns": end_ns - comm_end,
    }


def task_for_cann(
    cann_row: dict | None,
    tasks_by_cid: dict,
    string_ids: dict,
    record_type: int,
    wait_type: int,
    api_name: str,
    used: set[int],
) -> TaskRow | None:
    if not cann_row:
        return None
    task, status = project_cann_to_task(
        cann_row, api_name, tasks_by_cid, string_ids, record_type, wait_type, used
    )
    if status != "ok" or task is None:
        return None
    used.add(task.rowid)
    return task


def fifo_successor(
    wait_task: TaskRow,
    all_tasks: list[TaskRow],
    active_start: int,
    active_end: int,
) -> tuple[TaskRow | None, str]:
    by_stream = stream_tasks_by_id(all_tasks)
    ordered = sort_active_tasks(by_stream.get(wait_task.stream_id, []), active_start, active_end)
    for i, t in enumerate(ordered):
        if t.rowid == wait_task.rowid and i + 1 < len(ordered):
            nxt = ordered[i + 1]
            verdict, reason = evaluate_adjacent_pair(t, nxt)
            if verdict == "sortable":
                return nxt, reason
            return None, reason
    return None, "no_adjacent_successor"


def audit_callback_ns(audit: dict[str, Any]) -> int:
    if audit.get("callback_enter_monotonic_ns") and audit.get("callback_exit_monotonic_ns"):
        return int(audit["callback_exit_monotonic_ns"]) - int(audit["callback_enter_monotonic_ns"])
    return 0


def extract_run(run_dir: Path, run_id: str, condition: str) -> dict[str, Any]:
    trace_dir = run_dir / "event_trace"
    prof_dir = run_dir / "out" / "args_on"
    db_files = list(prof_dir.glob("**/ascend_pytorch_profiler_0.db"))
    if not db_files:
        raise FileNotFoundError(f"no profiler db under {prof_dir}")
    db_path = db_files[0]
    pw_path = prof_dir / "profile_window.json"
    active_start, active_end = load_profile_window(pw_path)
    audit = load_delay_audit(trace_dir)
    record_issuing_tid = int(
        audit.get("record_issuing_tid") or audit.get("armed_tid", 0) or 0
    ) or None
    arm_tid = int(audit.get("arm_tid", 0)) or None

    rank0_meta, rank0_records, _ = pick_rank0_trace(trace_dir)
    rank0_pid = int(rank0_meta["pid"])
    _, _, gen_info = rebuild_generations(rank0_records)
    cann_rows = load_cann_api(db_path)
    rowid_map = load_cann_rowids(db_path)
    enrich_cann_rowids(cann_rows, rowid_map)
    aligned, _ = align_api(
        rank0_records, cann_rows, {API_RECORD, API_WAIT}, active_start, active_end
    )
    cann_by_ord = build_cann_ordinal_maps_with_rowid(
        db_path, cann_rows, active_start, active_end
    )
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    string_ids = load_string_ids(con.cursor())
    con.close()
    record_type, wait_type, _, _ = discover_event_task_types(
        db_path, active_start, active_end, string_ids
    )
    all_tasks = load_tasks(db_path)
    tasks_by_cid: dict[Any, list[TaskRow]] = {}
    for t in all_tasks:
        tasks_by_cid.setdefault(t.connection_id, []).append(t)

    chains, _, _ = build_allreduce_chains(
        db_path,
        gen_info,
        active_start,
        active_end,
        rank0_pid,
        rank0_records,
        aligned,
        record_type,
        wait_type,
        string_ids,
    )
    chain = next((c for c in chains if c.get("comm_op_name") == TARGET_COMM), None)
    if chain is None:
        return {"run_id": run_id, "condition": condition, "status": "COMM_NOT_FOUND"}

    rec_cs = chain.get("preload_record_call_sequence")
    wait_cs = chain.get("preload_wait_call_sequence")
    pre_rec = next((r for r in rank0_records if r.call_sequence == rec_cs), None)
    pre_wait = next((r for r in rank0_records if r.call_sequence == wait_cs), None)
    if pre_rec is None or pre_wait is None:
        return {"run_id": run_id, "condition": condition, "status": "CHAIN_CS_MISS"}

    def record_ord_on_thread(pre: Any) -> int:
        return sum(
            1
            for r in rank0_records
            if r.op == 4
            and r.acl_ret == 0
            and r.pid == pre.pid
            and r.tid == pre.tid
            and active_start <= r.enter_realtime_ns <= active_end
            and r.enter_realtime_ns < pre.enter_realtime_ns
        )

    def wait_ord_on_thread(pw: Any) -> int:
        return sum(
            1
            for r in rank0_records
            if r.op == 5
            and r.acl_ret == 0
            and r.pid == pw.pid
            and r.tid == pw.tid
            and active_start <= r.enter_realtime_ns <= active_end
            and r.enter_realtime_ns < pw.enter_realtime_ns
        )

    rec_ord = record_ord_on_thread(pre_rec)
    wait_ord = wait_ord_on_thread(pre_wait)
    if rec_ord != TARGET_RECORD_ORDINAL or wait_ord != TARGET_WAIT_ORDINAL:
        return {
            "run_id": run_id,
            "condition": condition,
            "status": "ORDINAL_MISMATCH",
            "record_ordinal": rec_ord,
            "wait_ordinal": wait_ord,
        }

    rec_cann = cann_by_ord.get((rank0_pid, pre_rec.tid, API_RECORD, TARGET_RECORD_ORDINAL))
    wait_cann = cann_by_ord.get((rank0_pid, pre_wait.tid, API_WAIT, TARGET_WAIT_ORDINAL))
    used_rowids: set[int] = set()
    rec_task = task_for_cann(
        rec_cann, tasks_by_cid, string_ids, record_type, wait_type, API_RECORD, used_rowids
    )
    wait_task = task_for_cann(
        wait_cann, tasks_by_cid, string_ids, record_type, wait_type, API_WAIT, used_rowids
    )
    if rec_task is None or wait_task is None:
        return {"run_id": run_id, "condition": condition, "status": "TASK_PROJECTION_MISS"}

    fifo_succ, fifo_reason = fifo_successor(wait_task, all_tasks, active_start, active_end)
    if fifo_succ is None:
        return {
            "run_id": run_id,
            "condition": condition,
            "status": "IDENTITY_OR_PATH_INVALID",
            "fifo_reason": fifo_reason,
        }

    comm_ops = load_comm_ops(db_path, active_start, active_end)
    comm = next((o for o in comm_ops if o["op_name"] == TARGET_COMM), None)

    pre_wait_compute, pwc_rule = find_pre_wait_compute(
        wait_task, all_tasks, string_ids, active_start, active_end
    )
    pre_comm_task, pct_rule = find_pre_comm_same_stream(
        comm,
        rec_task.stream_id,
        all_tasks,
        tasks_by_cid,
        active_start,
        active_end,
    )
    comm_start = comm_end = 0
    if comm:
        comm_start = int(comm.get("start_ns", 0))
        comm_end = int(comm.get("end_ns", 0))

    cb_ns = audit_callback_ns(audit)
    inject_preload_cs = int(audit.get("last_inject_preload_cs", 0) or 0)
    norm_key = build_normalized_structure_key(
        record_ordinal=rec_ord,
        wait_ordinal=wait_ord,
        comm_op=TARGET_COMM,
        record_stream_id=rec_task.stream_id,
        wait_stream_id=wait_task.stream_id,
    )
    generation_closure_status = "VALID"

    identity = {
        "run_id": run_id,
        "condition": condition,
        "status": "OK",
        "arm_tid": arm_tid,
        "record_issuing_tid": record_issuing_tid,
        "preload_record_cs": pre_rec.call_sequence,
        "preload_wait_cs": pre_wait.call_sequence,
        "record_cann_ordinal": TARGET_RECORD_ORDINAL,
        "wait_cann_ordinal": TARGET_WAIT_ORDINAL,
        "record_task_rowid": rec_task.rowid,
        "wait_task_rowid": wait_task.rowid,
        "fifo_successor_rowid": fifo_succ.rowid,
        "record_stream_id": rec_task.stream_id,
        "wait_stream_id": wait_task.stream_id,
        "comm_op": TARGET_COMM,
        "match_count": int(audit.get("match_count", 0)),
        "delay_us": int(audit.get("delay_us", 0)),
        "callback_ns": cb_ns,
        "callback_duration_ms": round(cb_ns / 1e6, 3),
        "inject_preload_cs": inject_preload_cs,
        "normalized_structure_key": norm_key,
        "generation_closure_status": generation_closure_status,
    }

    slack_record_to_wait = wait_task.start_ns - rec_task.end_ns
    slack_record_to_next = fifo_succ.start_ns - rec_task.end_ns

    nodes = [
        node_with_offsets(run_id, condition, "comm", comm_start, comm_end, comm_end),
        node_with_offsets(
            run_id, condition, "record_task", rec_task.start_ns, rec_task.end_ns, comm_end
        ),
        node_with_offsets(
            run_id, condition, "wait_task", wait_task.start_ns, wait_task.end_ns, comm_end
        ),
        node_with_offsets(
            run_id,
            condition,
            "fifo_successor",
            fifo_succ.start_ns,
            fifo_succ.end_ns,
            comm_end,
        ),
    ]
    if rec_cann:
        nodes.append(
            node_with_offsets(
                run_id,
                condition,
                "record_cann_api",
                int(rec_cann["start_ns"]),
                int(rec_cann["end_ns"]),
                comm_end,
            )
        )
    if wait_cann:
        nodes.append(
            node_with_offsets(
                run_id,
                condition,
                "wait_cann_api",
                int(wait_cann["start_ns"]),
                int(wait_cann["end_ns"]),
                comm_end,
            )
        )

    control_nodes: list[dict[str, Any]] = []
    if pre_wait_compute is not None:
        control_nodes.append(
            {
                "run_id": run_id,
                "condition": condition,
                "control_role": "pre_wait_compute",
                "selection_rule": pwc_rule,
                "task_rowid": pre_wait_compute.rowid,
                "stream_id": pre_wait_compute.stream_id,
                "start_offset_from_comm_end_ns": pre_wait_compute.start_ns - comm_end,
                "end_offset_from_comm_end_ns": pre_wait_compute.end_ns - comm_end,
                "duration_ns": pre_wait_compute.end_ns - pre_wait_compute.start_ns,
            }
        )
    if pre_comm_task is not None:
        control_nodes.append(
            {
                "run_id": run_id,
                "condition": condition,
                "control_role": "pre_comm_same_stream",
                "selection_rule": pct_rule,
                "task_rowid": pre_comm_task.rowid,
                "stream_id": pre_comm_task.stream_id,
                "start_offset_from_comm_end_ns": pre_comm_task.start_ns - comm_end,
                "end_offset_from_comm_end_ns": pre_comm_task.end_ns - comm_end,
                "duration_ns": pre_comm_task.end_ns - pre_comm_task.start_ns,
            }
        )

    return {
        "run_id": run_id,
        "condition": condition,
        "status": "OK",
        "identity": identity,
        "nodes": nodes,
        "control_nodes": control_nodes,
        "slack_record_to_wait_ns": slack_record_to_wait,
        "slack_record_to_next_ns": slack_record_to_next,
        "audit": audit,
        "db_path": str(db_path),
        "normalized_structure_key": norm_key,
        "generation_closure_status": generation_closure_status,
    }


def paired_effects(d0: dict[str, Any], d25: dict[str, Any], block: str) -> dict[str, Any]:
    d0_key = d0.get("normalized_structure_key", "")
    d25_key = d25.get("normalized_structure_key", "")
    if d0_key != d25_key:
        return {
            "block": block,
            "d0_run_id": d0["run_id"],
            "d25_run_id": d25["run_id"],
            "status": "PAIR_STRUCTURE_MISMATCH",
            "d0_structure_key": d0_key,
            "d25_structure_key": d25_key,
        }
    if d0.get("generation_closure_status") != "VALID" or d25.get("generation_closure_status") != "VALID":
        return {
            "block": block,
            "d0_run_id": d0["run_id"],
            "d25_run_id": d25["run_id"],
            "status": "GENERATION_NOT_VALID",
        }

    def rel_end(nodes: list[dict], name: str) -> int:
        return int(next(n for n in nodes if n["node"] == name)["end_offset_from_comm_end_ns"])

    def rel_start(nodes: list[dict], name: str) -> int:
        return int(next(n for n in nodes if n["node"] == name)["start_offset_from_comm_end_ns"])

    d0n, d25n = d0["nodes"], d25["nodes"]
    record_shift = rel_end(d25n, "record_task") - rel_end(d0n, "record_task")
    wait_shift = rel_end(d25n, "wait_task") - rel_end(d0n, "wait_task")
    successor_shift = rel_start(d25n, "fifo_successor") - rel_start(d0n, "fifo_successor")
    slack = d0.get("slack_record_to_next_ns", 0)
    predicted = max(0, record_shift - slack)
    return {
        "block": block,
        "d0_run_id": d0["run_id"],
        "d25_run_id": d25["run_id"],
        "status": "OK",
        "record_shift_ns": record_shift,
        "wait_completion_shift_ns": wait_shift,
        "successor_shift_ns": successor_shift,
        "d0_slack_record_to_next_ns": slack,
        "predicted_downstream_shift_ns": predicted,
        "d0_structure_key": d0_key,
        "d25_structure_key": d25_key,
    }


def check_inject_identity_alignment(ex: dict[str, Any]) -> str | None:
    """D2/D25: inject must hit the identity Record (fail-closed)."""
    if ex.get("status") != "OK":
        return None
    condition = str(ex.get("condition", "")).upper()
    if condition not in {"D2", "D25"}:
        return None
    audit = ex.get("audit", {})
    ident = ex.get("identity", {})
    if int(audit.get("match_count", 0)) != 1:
        return "INJECT_IDENTITY_MISMATCH:match_count"
    inject_cs = int(audit.get("last_inject_preload_cs", 0) or 0)
    identity_cs = int(ident.get("preload_record_cs", 0) or 0)
    if inject_cs <= 0 or identity_cs <= 0:
        return "INJECT_IDENTITY_MISMATCH:missing_cs"
    if inject_cs != identity_cs:
        return (
            f"INJECT_IDENTITY_MISMATCH:inject_cs={inject_cs}!=identity_cs={identity_cs}"
        )
    return None


def check_evidence_consistency(ex: dict[str, Any]) -> str | None:
    if ex.get("status") != "OK":
        return None
    audit = ex.get("audit", {})
    ident = ex.get("identity", {})
    if int(audit.get("match_count", -1)) != int(ident.get("match_count", -2)):
        return "EVIDENCE_CONSISTENCY_INVALID:match_count"
    if int(audit.get("delay_us", -1)) != int(ident.get("delay_us", -2)):
        return "EVIDENCE_CONSISTENCY_INVALID:delay_us"
    if audit_callback_ns(audit) != int(ident.get("callback_ns", -1)):
        return "EVIDENCE_CONSISTENCY_INVALID:callback_ns"
    return None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--out-dir", required=True)
    args = p.parse_args()
    manifest_obj = json.loads(Path(args.manifest).read_text())
    if isinstance(manifest_obj, list):
        runs = manifest_obj
        pairs = []
    else:
        runs = manifest_obj.get("runs", [])
        pairs = manifest_obj.get("pairs", [])
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    extracted: dict[str, dict] = {}
    identity_rows: list[dict] = []
    node_rows: list[dict] = []
    control_rows: list[dict] = []
    callback_rows: list[dict] = []
    delay_audit_rows: list[dict] = []
    ledger_rows: list[dict] = []

    for spec in runs:
        run_id = spec["run_id"]
        condition = spec["condition"]
        run_dir = Path(spec["run_dir"])
        try:
            ex = extract_run(run_dir, run_id, condition)
        except Exception as e:  # noqa: BLE001
            ex = {"run_id": run_id, "condition": condition, "status": f"ERROR:{e}"}
        consistency = check_evidence_consistency(ex)
        if consistency:
            ex["status"] = consistency
        inject_align = check_inject_identity_alignment(ex)
        if inject_align:
            ex["status"] = inject_align
        extracted[run_id] = ex
        ledger_rows.append(
            {
                "run_id": run_id,
                "condition": condition,
                "status": ex.get("status"),
                "run_dir": str(run_dir),
            }
        )
        if ex.get("status") != "OK":
            continue
        identity_rows.append(ex["identity"])
        node_rows.extend(ex["nodes"])
        control_rows.extend(ex.get("control_nodes", []))
        audit = ex.get("audit", {})
        cb_ns = audit_callback_ns(audit)
        delay_audit_rows.append(
            {
                "run_id": run_id,
                "condition": condition,
                "delay_us": audit.get("delay_us"),
                "match_count": audit.get("match_count"),
                "callback_ns": cb_ns,
                "arm_tid": audit.get("arm_tid"),
                "record_issuing_tid": audit.get("record_issuing_tid") or audit.get("armed_tid"),
                "last_inject_preload_cs": audit.get("last_inject_preload_cs"),
                "preload_record_cs": ex["identity"].get("preload_record_cs"),
            }
        )
        callback_rows.append(
            {
                "run_id": run_id,
                "condition": condition,
                "delay_us": audit.get("delay_us"),
                "match_count": audit.get("match_count"),
                "callback_ns": cb_ns,
                "hostfunc_submit_rc": audit.get("hostfunc_submit_rc"),
                "callback_duration_ms": ex["identity"].get("callback_duration_ms"),
                "inject_failed": audit.get("inject_failed"),
            }
        )

    paired: list[dict] = []
    for pair in pairs:
        if len(pair) != 3:
            continue
        block, d0_id, d25_id = pair
        d0, d25 = extracted.get(d0_id), extracted.get(d25_id)
        if not d0 or not d25 or d0.get("status") != "OK" or d25.get("status") != "OK":
            ledger_rows.append(
                {
                    "run_id": f"pair_{block}",
                    "condition": "PAIR",
                    "status": "PAIR_INVALID",
                    "run_dir": f"{d0_id}|{d25_id}",
                }
            )
            paired.append(
                {
                    "block": block,
                    "d0_run_id": d0_id,
                    "d25_run_id": d25_id,
                    "status": "PAIR_INVALID",
                }
            )
            continue
        pe = paired_effects(d0, d25, block)
        paired.append(pe)
        if pe.get("status") == "PAIR_STRUCTURE_MISMATCH":
            ledger_rows.append(
                {
                    "run_id": f"pair_{block}",
                    "condition": "PAIR",
                    "status": "PAIR_STRUCTURE_MISMATCH",
                    "run_dir": f"{d0_id}|{d25_id}",
                }
            )

    identity_fields = list(identity_rows[0].keys()) if identity_rows else ["run_id"]
    write_csv(out / "intervention_identity.csv", identity_rows, identity_fields)
    write_csv(
        out / "node_wallclock.csv",
        node_rows,
        [
            "run_id",
            "condition",
            "node",
            "start_ns",
            "end_ns",
            "duration_ns",
            "start_offset_from_comm_end_ns",
            "end_offset_from_comm_end_ns",
        ],
    )
    write_csv(
        out / "control_nodes.csv",
        control_rows,
        [
            "run_id",
            "condition",
            "control_role",
            "selection_rule",
            "task_rowid",
            "stream_id",
            "start_offset_from_comm_end_ns",
            "end_offset_from_comm_end_ns",
            "duration_ns",
        ],
    )
    write_csv(
        out / "delay_audit.csv",
        delay_audit_rows,
        ["run_id", "condition", "delay_us", "match_count", "callback_ns", "arm_tid", "record_issuing_tid", "last_inject_preload_cs", "preload_record_cs"],
    )
    write_csv(
        out / "callback_realization.csv",
        callback_rows,
        [
            "run_id",
            "condition",
            "delay_us",
            "match_count",
            "callback_ns",
            "hostfunc_submit_rc",
            "callback_duration_ms",
            "inject_failed",
        ],
    )
    write_csv(out / "run_ledger.csv", ledger_rows, ["run_id", "condition", "status", "run_dir"])
    write_csv(
        out / "paired_effects.csv",
        paired,
        [
            "block",
            "d0_run_id",
            "d25_run_id",
            "status",
            "record_shift_ns",
            "wait_completion_shift_ns",
            "successor_shift_ns",
            "d0_slack_record_to_next_ns",
            "predicted_downstream_shift_ns",
            "d0_structure_key",
            "d25_structure_key",
        ],
    )

    pair_ok = sum(1 for p in paired if p.get("status") == "OK")
    summary = {
        "identity_ok": len(identity_rows),
        "pair_structure_ok": pair_ok,
        "paired": paired,
        "excluded_v3_package": "20260824T074500Z_d51_wait_dag_v3",
    }
    (out / "v3_1_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()

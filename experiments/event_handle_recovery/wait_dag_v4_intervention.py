#!/usr/bin/env python3
"""D51 Wait DAG V4: device-work delay + upstream-kernel wallclock."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from analyze_event_pairs import (
    TaskRow,
    discover_event_task_types,
    load_comm_ops,
    load_string_ids,
    load_tasks,
    pick_rank0_trace,
    stream_tasks_by_id,
)
from wait_dag_v2_fifo import (
    evaluate_adjacent_pair,
    first_comm_entry_on_stream,
    sort_active_tasks,
)
from wait_dag_v4_2_reverse_candidate import (
    build_normalized_key,
    extract_reverse_candidates,
    load_profile_window,
)

TARGET_COMM = "hcom_allReduce__612_0_1"
INJECTED_KERNEL_NAME = "d51_compute_delay_kernel"
TREATMENT_CONDITIONS = frozenset({"DSMALL", "DLARGE", "D2", "D25"})
API_RECORD = "aclrtRecordEvent"
API_WAIT = "aclrtStreamWaitEvent"
RECORD_TID_ROLE = "AFTER_ARM_FIRST_SUCCESSFUL_RECORD_TID"
NODE_WALLCLOCK_FIELDS = [
    "run_id",
    "condition",
    "node",
    "start_ns",
    "end_ns",
    "duration_ns",
    "start_offset_from_upstream_kernel_end_ns",
    "end_offset_from_upstream_kernel_end_ns",
]
PAIRED_EFFECTS_FIELDS = [
    "block",
    "d0_run_id",
    "dtreat_run_id",
    "extraction_status",
    "structure_gate_pass",
    "dose_gate_pass",
    "causal_gate_pass",
    "structure_reason",
    "dose_reason",
    "causal_reason",
    "legacy_illegal_dose",
    "record_shift_ns",
    "wait_completion_shift_ns",
    "comm_entry_shift_ns",
    "d0_slack_record_to_comm_ns",
    "d0_slack_record_to_wait_ns",
    "predicted_comm_entry_shift_ns",
    "predicted_wait_shift_ns",
    "realized_work_ns",
    "d0_structure_key",
    "dtreat_structure_key",
]

LEGACY_ILLEGAL_DOSE_ITERS = frozenset({200, 2000})
LEGACY_ILLEGAL_DOSE_MAX_NS = 10_000  # ~10 µs ceiling for V4.2 flat kernel


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def load_selector_manifest(manifest_path: Path | None) -> dict[str, Any] | None:
    if manifest_path is None or not manifest_path.exists():
        return None
    return json.loads(manifest_path.read_text())


def normalized_key_to_json(normalized_key: dict[str, Any]) -> str:
    return json.dumps(normalized_key, sort_keys=True, separators=(",", ":"))


def select_device_work_audit_path(trace_dir: Path) -> Path | None:
    files = sorted(trace_dir.glob("rank_0_pid_*.device_work_audit.json"))
    if not files:
        files = sorted(trace_dir.glob("rank_*_pid_*.device_work_audit.json"))
    return files[0] if files else None


def load_device_work_audit(trace_dir: Path) -> dict[str, Any]:
    path = select_device_work_audit_path(trace_dir)
    if path is None:
        return {}
    return json.loads(path.read_text())


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


def build_normalized_structure_key_legacy(
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


def resolve_inject_cti_name_id(db_path: Path) -> int | None:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    row = cur.execute(
        "SELECT id FROM STRING_IDS WHERE value = ? LIMIT 1",
        (INJECTED_KERNEL_NAME,),
    ).fetchone()
    con.close()
    return int(row[0]) if row else None


def inject_cti_rowids(
    db_path: Path, active_start: int, active_end: int, cti_name_id: int | None = None
) -> set[int]:
    if cti_name_id is None:
        cti_name_id = resolve_inject_cti_name_id(db_path)
    if cti_name_id is None:
        return set()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    rows = {
        int(r[0])
        for r in cur.execute(
            """
            SELECT t.rowid
            FROM TASK AS t
            JOIN COMPUTE_TASK_INFO AS c ON t.globalTaskId = c.globalTaskId
            WHERE c.name = ? AND t.startNs BETWEEN ? AND ?
            """,
            (cti_name_id, active_start, active_end),
        )
    }
    con.close()
    return rows


def extract_inject_kernel_duration_ns(
    db_path: Path, active_start: int, active_end: int
) -> int | None:
    cti_name_id = resolve_inject_cti_name_id(db_path)
    if cti_name_id is None:
        return None
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    row = cur.execute(
        """
        SELECT MAX(t.endNs - t.startNs)
        FROM TASK AS t
        JOIN COMPUTE_TASK_INFO AS c ON t.globalTaskId = c.globalTaskId
        WHERE c.name = ? AND t.startNs BETWEEN ? AND ?
        """,
        (cti_name_id, active_start, active_end),
    ).fetchone()
    con.close()
    if row is None or row[0] is None:
        return None
    dur = int(row[0])
    return dur if dur > 0 else None


def find_upstream_kernel_aivec(
    rec_task: TaskRow,
    all_tasks: list[TaskRow],
    string_ids: dict[int, str],
    active_start: int,
    active_end: int,
) -> tuple[TaskRow | None, str]:
    by_stream = stream_tasks_by_id(all_tasks)
    ordered = sort_active_tasks(by_stream.get(rec_task.stream_id, []), active_start, active_end)
    idx = next((i for i, t in enumerate(ordered) if t.rowid == rec_task.rowid), None)
    if idx is None:
        return None, "record_not_on_stream"
    for j in range(idx - 1, -1, -1):
        cand = ordered[j]
        name = task_type_name(cand, string_ids)
        if "kernel_aivec" in name or "aivec" in name:
            return cand, "upstream_kernel_fifo"
    return None, "no_upstream_kernel"


def find_injected_kernel_before_record(
    rec_task: TaskRow,
    all_tasks: list[TaskRow],
    string_ids: dict[int, str],
    active_start: int,
    active_end: int,
    db_path: Path | None = None,
) -> tuple[TaskRow | None, str]:
    by_stream = stream_tasks_by_id(all_tasks)
    ordered = sort_active_tasks(by_stream.get(rec_task.stream_id, []), active_start, active_end)
    idx = next((i for i, t in enumerate(ordered) if t.rowid == rec_task.rowid), None)
    if idx is None or idx == 0:
        return None, "no_pre_record_task"
    prev = ordered[idx - 1]
    verdict, reason = evaluate_adjacent_pair(prev, rec_task)
    if verdict != "sortable":
        return None, f"inject_record_overlap:{reason}"

    cti_hits: set[int] = set()
    if db_path is not None:
        cti_hits = inject_cti_rowids(db_path, active_start, active_end)
        if prev.rowid in cti_hits:
            return prev, "injected_kernel_cti_adjacent"
        if len(cti_hits) == 1:
            only = next(iter(cti_hits))
            only_task = next((t for t in all_tasks if t.rowid == only), None)
            if only_task is not None and only_task.rowid == prev.rowid:
                return only_task, "injected_kernel_cti_unique_adjacent"

    name = task_type_name(prev, string_ids)
    if INJECTED_KERNEL_NAME in name or "d51_compute_delay" in name.lower():
        return prev, "injected_kernel_name_adjacent"
    if prev.rowid in cti_hits:
        return prev, "injected_kernel_cti_adjacent"
    return None, "no_injected_kernel"


def find_upstream_kernel_skipping_inject(
    rec_task: TaskRow,
    inject_rowid: int | None,
    all_tasks: list[TaskRow],
    string_ids: dict[int, str],
    active_start: int,
    active_end: int,
    inject_cti_rowids_set: set[int] | None = None,
) -> tuple[TaskRow | None, str]:
    by_stream = stream_tasks_by_id(all_tasks)
    ordered = sort_active_tasks(by_stream.get(rec_task.stream_id, []), active_start, active_end)
    idx = next((i for i, t in enumerate(ordered) if t.rowid == rec_task.rowid), None)
    if idx is None:
        return None, "record_not_on_stream"
    start_idx = idx - 1
    if inject_rowid is not None and start_idx >= 0 and ordered[start_idx].rowid == inject_rowid:
        start_idx -= 1
    for j in range(start_idx, -1, -1):
        cand = ordered[j]
        if inject_rowid is not None and cand.rowid == inject_rowid:
            continue
        if inject_cti_rowids_set and cand.rowid in inject_cti_rowids_set:
            return None, "inject_kernel_on_upstream_path"
        name = task_type_name(cand, string_ids)
        if "kernel_aivec" in name or "aivec" in name:
            if j + 1 < len(ordered):
                nxt = ordered[j + 1]
                verdict, reason = evaluate_adjacent_pair(cand, nxt)
                if verdict != "sortable":
                    return None, f"upstream_overlap:{reason}"
            return cand, "upstream_kernel_skip_inject"
    return None, "no_upstream_kernel_after_skip"


def find_pre_wait_control(
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
    anchor_end: int,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "condition": condition,
        "node": node_name,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "duration_ns": end_ns - start_ns,
        "start_offset_from_upstream_kernel_end_ns": start_ns - anchor_end,
        "end_offset_from_upstream_kernel_end_ns": end_ns - anchor_end,
    }


def extract_run(
    run_dir: Path,
    run_id: str,
    condition: str,
    selector_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace_dir = run_dir / "event_trace"
    audit = load_device_work_audit(trace_dir)
    delay_audit = load_delay_audit(trace_dir)
    if not audit and delay_audit:
        audit = delay_audit
    record_issuing_tid = int(
        audit.get("record_issuing_tid") or audit.get("armed_tid", 0) or 0
    ) or None
    arm_tid = int(audit.get("arm_tid", 0)) or None

    rev = extract_reverse_candidates(run_dir, run_id=run_id, target_comm=TARGET_COMM)
    if rev["status"] != "OK" or rev["winner"] is None:
        return {
            "run_id": run_id,
            "condition": condition,
            "status": rev["status"],
            "candidate_count": rev["candidate_count"],
        }

    winner = rev["winner"]
    ctx = rev["ctx"]
    active_start, active_end = ctx.active_start, ctx.active_end
    all_tasks = ctx.all_tasks
    string_ids = ctx.string_ids
    tasks_by_cid = ctx.tasks_by_cid

    pre_rec = winner["record_rec"]
    pre_wait = winner["wait_rec"]
    rec_task = winner["record_task"]
    wait_task = winner["wait_task"]
    rec_ord = winner["record_active_success_ordinal"]
    wait_ord = winner["wait_active_success_ordinal"]
    upstream_kernel = winner.get("kernel_task")
    comm = winner.get("comm_op")
    comm_entry = winner.get("comm_entry")

    if rec_task is None or wait_task is None:
        return {"run_id": run_id, "condition": condition, "status": "TASK_PROJECTION_MISS"}

    paired_key = None
    if selector_manifest:
        paired_key = selector_manifest.get("normalized_key")

    norm_key_dict = rev["normalized_key"] or build_normalized_key(winner, TARGET_COMM)
    norm_key = normalized_key_to_json(norm_key_dict)

    if paired_key is not None and condition.upper() != "D0":
        if json.dumps(paired_key, sort_keys=True) != json.dumps(norm_key_dict, sort_keys=True):
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "TREATMENT_REVERSE_STRUCTURE_MISMATCH",
                "d0_structure_key": json.dumps(paired_key, sort_keys=True),
                "treatment_structure_key": json.dumps(norm_key_dict, sort_keys=True),
            }
        manifest_ord = int(selector_manifest.get("record_active_success_ordinal", -1))
        audit_ord = int(audit.get("target_record_ordinal", audit.get("active_record_success_ord", -1)))
        if audit_ord >= 0 and audit_ord != manifest_ord:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "SELECTOR_ORDINAL_MISMATCH",
                "manifest_ordinal": manifest_ord,
                "audit_ordinal": audit_ord,
            }
        inject_cs = int(audit.get("trigger_record_preload_cs", 0) or 0)
        if inject_cs > 0 and inject_cs != int(pre_rec.call_sequence):
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "INJECT_IDENTITY_MISMATCH:preload_cs",
                "inject_cs": inject_cs,
                "identity_cs": pre_rec.call_sequence,
            }

    injected_kernel, inj_rule = find_injected_kernel_before_record(
        rec_task, all_tasks, string_ids, active_start, active_end, db_path=ctx.db_path
    )
    cti_rows = inject_cti_rowids(ctx.db_path, active_start, active_end)
    cond_upper = condition.upper()
    launch_count = int(audit.get("launch_count", audit.get("match_count", 0)) or 0)

    if cond_upper == "D0":
        if launch_count != 0:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "D0_UNEXPECTED_LAUNCH",
                "launch_count": launch_count,
            }
        if injected_kernel is not None:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "INJECT_UNEXPECTED_ON_D0",
                "injected_kernel_rowid": injected_kernel.rowid,
            }
        upstream_kernel = winner.get("kernel_task")
        if upstream_kernel is None or (
            cti_rows and upstream_kernel.rowid in cti_rows
        ):
            upstream_kernel, uk_rule = find_upstream_kernel_skipping_inject(
                rec_task,
                None,
                all_tasks,
                string_ids,
                active_start,
                active_end,
                cti_rows,
            )
        else:
            uk_rule = "reverse_candidate_c4"
    elif cond_upper in TREATMENT_CONDITIONS:
        if launch_count != 1:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "INJECT_IDENTITY_MISMATCH:launch_count",
                "launch_count": launch_count,
            }
        if injected_kernel is None:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "INJECT_KERNEL_NOT_PROJECTED",
                "injected_kernel_rule": inj_rule,
            }
        upstream_kernel, uk_rule = find_upstream_kernel_skipping_inject(
            rec_task,
            injected_kernel.rowid,
            all_tasks,
            string_ids,
            active_start,
            active_end,
            cti_rows,
        )
        if upstream_kernel is None:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "KERNEL_ROLE_AMBIGUOUS",
                "upstream_rule": uk_rule,
            }
    else:
        upstream_kernel = winner.get("kernel_task")
        if upstream_kernel is None or (cti_rows and upstream_kernel.rowid in cti_rows):
            upstream_kernel, uk_rule = find_upstream_kernel_aivec(
                rec_task, all_tasks, string_ids, active_start, active_end
            )
        else:
            uk_rule = "reverse_candidate_c4"
    anchor_end = upstream_kernel.end_ns if upstream_kernel else rec_task.start_ns

    fifo_succ_rowid = winner.get("fifo_successor_rowid")
    fifo_succ = ctx.all_tasks_by_rowid.get(fifo_succ_rowid) if fifo_succ_rowid else None
    if fifo_succ is None:
        fifo_succ, fifo_reason = fifo_successor(wait_task, all_tasks, active_start, active_end)
        if fifo_succ is None:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "IDENTITY_OR_PATH_INVALID",
                "fifo_reason": fifo_reason,
            }

    pre_wait_compute, pwc_rule = find_pre_wait_control(
        wait_task, all_tasks, string_ids, active_start, active_end
    )
    pre_comm_task, pct_rule = find_pre_comm_same_stream(
        comm,
        wait_task.stream_id,
        all_tasks,
        tasks_by_cid,
        active_start,
        active_end,
    )
    comm_start = comm_end = 0
    if comm:
        comm_start = int(comm.get("start_ns", 0))
        comm_end = int(comm.get("end_ns", 0))

    inject_preload_cs = int(
        audit.get("trigger_record_preload_cs", 0) or audit.get("last_inject_preload_cs", 0) or 0
    )
    requested_iters = int(audit.get("requested_iters", 0) or audit.get("delay_us", 0) or 0)
    generation_closure_status = "VALID"

    identity = {
        "run_id": run_id,
        "condition": condition,
        "status": "OK",
        "arm_tid": arm_tid,
        "record_issuing_tid": record_issuing_tid,
        "preload_record_cs": pre_rec.call_sequence,
        "preload_wait_cs": pre_wait.call_sequence,
        "record_cann_ordinal": rec_ord,
        "wait_cann_ordinal": wait_ord,
        "record_task_rowid": rec_task.rowid,
        "wait_task_rowid": wait_task.rowid,
        "fifo_successor_rowid": fifo_succ.rowid,
        "record_stream_id": rec_task.stream_id,
        "wait_stream_id": wait_task.stream_id,
        "comm_op": TARGET_COMM,
        "match_count": int(audit.get("launch_count", audit.get("match_count", 0))),
        "requested_iters": requested_iters,
        "launch_rc": int(audit.get("launch_rc", 0)),
        "inject_preload_cs": inject_preload_cs,
        "target_record_preload_cs": pre_rec.call_sequence,
        "normalized_structure_key": norm_key,
        "generation_closure_status": generation_closure_status,
        "extraction_method": "reverse_candidate_v4_2",
    }

    slack_record_to_wait = wait_task.end_ns - rec_task.end_ns
    slack_record_to_comm = (
        (comm_entry.start_ns - rec_task.end_ns) if comm_entry is not None else slack_record_to_wait
    )
    slack_record_to_next = (
        fifo_succ.start_ns - rec_task.end_ns if fifo_succ else slack_record_to_wait
    )

    nodes = []
    if upstream_kernel is not None:
        nodes.append(
            node_with_offsets(
                run_id,
                condition,
                "upstream_kernel",
                upstream_kernel.start_ns,
                upstream_kernel.end_ns,
                anchor_end,
            )
        )
    if injected_kernel is not None:
        nodes.append(
            node_with_offsets(
                run_id,
                condition,
                "injected_kernel",
                injected_kernel.start_ns,
                injected_kernel.end_ns,
                anchor_end,
            )
        )
    if comm_entry is not None:
        nodes.append(
            node_with_offsets(
                run_id,
                condition,
                "comm_entry",
                comm_entry.start_ns,
                comm_entry.end_ns,
                anchor_end,
            )
        )
    nodes.extend([
        node_with_offsets(run_id, condition, "comm", comm_start, comm_end, anchor_end),
        node_with_offsets(
            run_id, condition, "record_task", rec_task.start_ns, rec_task.end_ns, anchor_end
        ),
        node_with_offsets(
            run_id, condition, "wait_task", wait_task.start_ns, wait_task.end_ns, anchor_end
        ),
    ])
    if fifo_succ is not None:
        nodes.append(
            node_with_offsets(
                run_id,
                condition,
                "fifo_successor",
                fifo_succ.start_ns,
                fifo_succ.end_ns,
                anchor_end,
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
        "slack_record_to_comm_ns": slack_record_to_comm,
        "upstream_kernel_rule": uk_rule,
        "injected_kernel_rule": inj_rule,
        "injected_kernel_rowid": injected_kernel.rowid if injected_kernel else None,
        "realized_work_ns": (
            int(injected_kernel.end_ns - injected_kernel.start_ns) if injected_kernel else 0
        ),
        "audit": audit,
        "db_path": str(ctx.db_path),
        "normalized_structure_key": norm_key,
        "generation_closure_status": generation_closure_status,
    }


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




def is_legacy_illegal_dose(dtreat: dict[str, Any], realized_work: int) -> bool:
    req_iters = int(dtreat.get("identity", {}).get("requested_iters", 0) or 0)
    if req_iters in LEGACY_ILLEGAL_DOSE_ITERS and realized_work <= LEGACY_ILLEGAL_DOSE_MAX_NS:
        return True
    if str(dtreat.get("run_id", "")).endswith(("small_dsmall", "effect_dlarge_b1")):
        if realized_work <= LEGACY_ILLEGAL_DOSE_MAX_NS:
            return True
    return False


def pair_condition_kind(block: str, dtreat: dict[str, Any]) -> str:
    blk = block.lower()
    cond = str(dtreat.get("condition", "")).upper()
    if "dsmall" in blk or cond == "DSMALL":
        return "Dsmall"
    if "dlarge" in blk or cond in {"DLARGE", "D2", "D25"}:
        return "Dlarge"
    return "unknown"


def evaluate_structure_gate(d0: dict[str, Any], dtreat: dict[str, Any]) -> tuple[bool, str | None]:
    d0_key = d0.get("normalized_structure_key", "")
    dtreat_key = dtreat.get("normalized_structure_key", "")
    if d0_key != dtreat_key:
        return False, "PAIR_STRUCTURE_MISMATCH"
    if d0.get("generation_closure_status") != "VALID" or dtreat.get("generation_closure_status") != "VALID":
        return False, "GENERATION_NOT_VALID"
    return True, None


def evaluate_dose_gate(
    *,
    kind: str,
    realized_work: int,
    slack_comm: int,
    legacy_illegal: bool,
    dtreat: dict[str, Any],
) -> tuple[bool, str | None]:
    if legacy_illegal:
        return False, "legacy_illegal_dose"
    if realized_work <= 0:
        return False, "REALIZED_WORK_INVALID"
    if kind == "Dsmall":
        lo, hi = int(0.15 * slack_comm), int(0.40 * slack_comm)
        if not (lo <= realized_work <= hi):
            return False, f"Dsmall_band_miss:{realized_work} not in [{lo},{hi}]"
        return True, None
    if kind == "Dlarge":
        if realized_work < slack_comm + 1_000_000:
            return False, f"Dlarge_slack_miss:{realized_work} < S+1ms"
        if realized_work > 20_000_000:
            return False, f"Dlarge_ceiling:{realized_work} > 20ms"
        return True, None
    return False, f"unknown_condition:{kind}"


def evaluate_causal_gate(
    *,
    kind: str,
    record_shift: int,
    wait_shift: int,
    comm_entry_shift: int,
    realized_work: int,
    slack_comm: int,
    predicted_comm: int,
) -> tuple[bool, str | None]:
    half_ms = 500_000
    if kind == "Dsmall":
        tol = max(half_ms, int(0.20 * realized_work))
        if abs(record_shift - realized_work) > tol:
            return False, f"Dsmall_record_shift_mismatch:{record_shift} vs {realized_work}"
        expected_comm = max(0, record_shift - slack_comm)
        if comm_entry_shift < expected_comm - half_ms:
            return False, f"Dsmall_comm_shift_low:{comm_entry_shift} < {expected_comm}"
        return True, None
    if kind == "Dlarge":
        if record_shift <= slack_comm + half_ms:
            return False, f"Dlarge_record_shift_low:{record_shift}"
        if comm_entry_shift <= half_ms:
            return False, f"Dlarge_comm_entry_shift_low:{comm_entry_shift}"
        if wait_shift <= 0:
            return False, "Dlarge_wait_not_same_direction"
        if predicted_comm > 0:
            tol = max(half_ms, int(0.30 * predicted_comm))
            if abs(comm_entry_shift - predicted_comm) > tol:
                return False, (
                    f"Dlarge_comm_predict_mismatch:{comm_entry_shift} vs {predicted_comm}"
                )
        return True, None
    return False, "causal_not_evaluated"


def paired_effects(d0: dict[str, Any], dtreat: dict[str, Any], block: str) -> dict[str, Any]:
    base = {
        "block": block,
        "d0_run_id": d0["run_id"],
        "dtreat_run_id": dtreat["run_id"],
        "extraction_status": "OK",
        "legacy_illegal_dose": False,
        "causal_gate_pass": None,
        "causal_reason": None,
    }
    d0_key = d0.get("normalized_structure_key", "")
    dtreat_key = dtreat.get("normalized_structure_key", "")

    structure_ok, structure_reason = evaluate_structure_gate(d0, dtreat)
    base["structure_gate_pass"] = structure_ok
    base["structure_reason"] = structure_reason or ""
    if not structure_ok:
        base["extraction_status"] = structure_reason or "STRUCTURE_FAIL"
        base["dose_gate_pass"] = False
        base["dose_reason"] = "structure_not_pass"
        base["d0_structure_key"] = d0_key
        base["dtreat_structure_key"] = dtreat_key
        return base

    def rel_end(nodes: list[dict], name: str) -> int:
        return int(
            next(n for n in nodes if n["node"] == name)["end_offset_from_upstream_kernel_end_ns"]
        )

    def rel_start(nodes: list[dict], name: str) -> int:
        return int(
            next(n for n in nodes if n["node"] == name)["start_offset_from_upstream_kernel_end_ns"]
        )

    d0n, dtn = d0["nodes"], dtreat["nodes"]
    realized_work = 0
    if any(n["node"] == "injected_kernel" for n in dtn):
        inj = next(n for n in dtn if n["node"] == "injected_kernel")
        realized_work = int(inj["duration_ns"])
    elif int(dtreat.get("realized_work_ns", 0) or 0) > 0:
        realized_work = int(dtreat["realized_work_ns"])

    legacy = is_legacy_illegal_dose(dtreat, realized_work)
    base["legacy_illegal_dose"] = legacy
    kind = pair_condition_kind(block, dtreat)
    slack_comm = int(d0.get("slack_record_to_comm_ns", d0.get("slack_record_to_next_ns", 0)))
    slack_wait = int(d0.get("slack_record_to_wait_ns", 0))

    dose_ok, dose_reason = evaluate_dose_gate(
        kind=kind,
        realized_work=realized_work,
        slack_comm=slack_comm,
        legacy_illegal=legacy,
        dtreat=dtreat,
    )
    base["dose_gate_pass"] = dose_ok
    base["dose_reason"] = dose_reason or ""
    base["realized_work_ns"] = realized_work
    base["d0_structure_key"] = d0_key
    base["dtreat_structure_key"] = dtreat_key
    base["d0_slack_record_to_comm_ns"] = slack_comm
    base["d0_slack_record_to_wait_ns"] = slack_wait

    if not dose_ok:
        base["causal_gate_pass"] = None
        base["causal_reason"] = "dose_not_pass"
        return base

    record_shift = rel_end(dtn, "record_task") - rel_end(d0n, "record_task")
    wait_shift = 0
    if any(n["node"] == "wait_task" for n in d0n) and any(n["node"] == "wait_task" for n in dtn):
        wait_shift = rel_end(dtn, "wait_task") - rel_end(d0n, "wait_task")
    comm_entry_shift = 0
    if any(n["node"] == "comm_entry" for n in d0n) and any(
        n["node"] == "comm_entry" for n in dtn
    ):
        comm_entry_shift = rel_start(dtn, "comm_entry") - rel_start(d0n, "comm_entry")
    predicted_comm = max(0, record_shift - slack_comm)
    predicted_wait = max(0, record_shift - slack_wait)

    base.update(
        {
            "record_shift_ns": record_shift,
            "wait_completion_shift_ns": wait_shift,
            "comm_entry_shift_ns": comm_entry_shift,
            "predicted_comm_entry_shift_ns": predicted_comm,
            "predicted_wait_shift_ns": predicted_wait,
        }
    )

    causal_ok, causal_reason = evaluate_causal_gate(
        kind=kind,
        record_shift=record_shift,
        wait_shift=wait_shift,
        comm_entry_shift=comm_entry_shift,
        realized_work=realized_work,
        slack_comm=slack_comm,
        predicted_comm=predicted_comm,
    )
    base["causal_gate_pass"] = causal_ok
    base["causal_reason"] = causal_reason or ""
    return base


def check_inject_identity_alignment(ex: dict[str, Any]) -> str | None:
    """Treatment inject must hit the identity Record (fail-closed)."""
    if ex.get("status") != "OK":
        return None
    condition = str(ex.get("condition", "")).upper()
    if condition not in TREATMENT_CONDITIONS:
        return None
    audit = ex.get("audit", {})
    ident = ex.get("identity", {})
    launch_count = int(audit.get("launch_count", audit.get("match_count", 0)) or 0)
    if launch_count != 1:
        return "INJECT_IDENTITY_MISMATCH:launch_count"
    inject_cs = int(
        audit.get("trigger_record_preload_cs", 0)
        or audit.get("last_inject_preload_cs", 0)
        or 0
    )
    identity_cs = int(ident.get("preload_record_cs", 0) or 0)
    if inject_cs <= 0 or identity_cs <= 0:
        return "INJECT_IDENTITY_MISMATCH:missing_cs"
    if inject_cs != identity_cs:
        return (
            f"INJECT_IDENTITY_MISMATCH:inject_cs={inject_cs}!=identity_cs={identity_cs}"
        )
    if int(ex.get("realized_work_ns", 0) or 0) <= 0:
        return "REALIZED_WORK_INVALID"
    return None


def check_evidence_consistency(ex: dict[str, Any]) -> str | None:
    if ex.get("status") != "OK":
        return None
    audit = ex.get("audit", {})
    ident = ex.get("identity", {})
    audit_hits = int(audit.get("launch_count", audit.get("match_count", -1)))
    ident_hits = int(ident.get("match_count", -2))
    if audit_hits != ident_hits:
        return "EVIDENCE_CONSISTENCY_INVALID:match_count"
    if "delay_us" in audit and "delay_us" in ident:
        if int(audit.get("delay_us", -1)) != int(ident.get("delay_us", -2)):
            return "EVIDENCE_CONSISTENCY_INVALID:delay_us"
    if "callback_ns" in ident:
        if audit_callback_ns(audit) != int(ident.get("callback_ns", -1)):
            return "EVIDENCE_CONSISTENCY_INVALID:callback_ns"
    return None


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--selector-manifest", default="")
    args = p.parse_args()
    manifest_obj = json.loads(Path(args.manifest).read_text())
    selector_manifest = load_selector_manifest(
        Path(args.selector_manifest) if args.selector_manifest else None
    )
    if selector_manifest is None and isinstance(manifest_obj, dict):
        sm_path = manifest_obj.get("selector_manifest")
        if sm_path:
            selector_manifest = load_selector_manifest(Path(sm_path))
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
    kernel_rows: list[dict] = []
    ledger_rows: list[dict] = []

    for spec in runs:
        run_id = spec["run_id"]
        condition = spec["condition"]
        run_dir = Path(spec["run_dir"])
        try:
            ex = extract_run(run_dir, run_id, condition, selector_manifest=selector_manifest)
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
        if ex.get("injected_kernel_rowid"):
            kernel_rows.append(
                {
                    "run_id": run_id,
                    "condition": condition,
                    "inject_task_rowid": ex.get("injected_kernel_rowid"),
                    "profiler_duration_ns": ex.get("realized_work_ns", 0),
                    "requested_iters": ex.get("identity", {}).get("requested_iters", 0),
                    "launch_count": ex.get("audit", {}).get("launch_count"),
                    "projection_rule": ex.get("injected_kernel_rule"),
                    "upstream_kernel_rule": ex.get("upstream_kernel_rule"),
                }
            )
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
                    "dtreat_run_id": d25_id,
                    "extraction_status": "PAIR_INVALID",
                    "structure_gate_pass": False,
                    "dose_gate_pass": False,
                    "causal_gate_pass": None,
                    "structure_reason": "PAIR_INVALID",
                    "dose_reason": "PAIR_INVALID",
                    "causal_reason": None,
                    "legacy_illegal_dose": False,
                }
            )
            continue
        pe = paired_effects(d0, d25, block)
        paired.append(pe)
        if not pe.get("structure_gate_pass"):
            ledger_rows.append(
                {
                    "run_id": f"pair_{block}",
                    "condition": "PAIR",
                    "status": pe.get("structure_reason", "PAIR_INVALID"),
                    "run_dir": f"{d0_id}|{d25_id}",
                }
            )
        elif pe.get("causal_gate_pass") is False:
            ledger_rows.append(
                {
                    "run_id": f"pair_{block}",
                    "condition": "PAIR",
                    "status": pe.get("causal_reason", "CAUSAL_FAIL"),
                    "run_dir": f"{d0_id}|{d25_id}",
                }
            )

    identity_fields = list(identity_rows[0].keys()) if identity_rows else ["run_id"]
    write_csv(out / "intervention_identity.csv", identity_rows, identity_fields)
    write_csv(out / "node_wallclock.csv", node_rows, NODE_WALLCLOCK_FIELDS)
    write_csv(
        out / "kernel_realization.csv",
        kernel_rows,
        [
            "run_id",
            "condition",
            "inject_task_rowid",
            "profiler_duration_ns",
            "requested_iters",
            "launch_count",
            "projection_rule",
            "upstream_kernel_rule",
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
    write_csv(out / "paired_effects.csv", paired, PAIRED_EFFECTS_FIELDS)

    identity_ok = len(identity_rows)
    pair_structure_pass_count = sum(1 for p in paired if p.get("structure_gate_pass"))
    dose_pass_count = sum(1 for p in paired if p.get("dose_gate_pass"))
    causal_gate_pass_count = sum(
        1 for p in paired if p.get("causal_gate_pass") is True and not p.get("legacy_illegal_dose")
    )
    causal_gate_fail_count = sum(1 for p in paired if p.get("causal_gate_pass") is False)
    realized_ok = sum(
        1
        for ex in extracted.values()
        if ex.get("status") == "OK"
        and str(ex.get("condition", "")).upper() in TREATMENT_CONDITIONS
        and int(ex.get("realized_work_ns", 0) or 0) > 0
        and not is_legacy_illegal_dose(ex, int(ex.get("realized_work_ns", 0) or 0))
    )
    summary = {
        "identity_ok": identity_ok,
        "pair_structure_pass_count": pair_structure_pass_count,
        "dose_pass_count": dose_pass_count,
        "causal_gate_pass_count": causal_gate_pass_count,
        "causal_gate_fail_count": causal_gate_fail_count,
        "realized_work_ok": realized_ok,
        "paired": paired,
        "excluded_v3_package": "20260824T074500Z_d51_wait_dag_v3",
    }
    summary_path = out / "v3_1_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()

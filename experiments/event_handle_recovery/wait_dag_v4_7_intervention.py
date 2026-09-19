#!/usr/bin/env python3
"""D51 Wait DAG V4.7: layered identity + unmasked comm-stream occupancy estimand."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from analyze_event_pairs import load_string_ids, stream_tasks_by_id
from wait_dag_v2_fifo import (
    evaluate_adjacent_pair,
    first_comm_entry_on_stream,
    is_kernel_task,
    nearest_kernel_predecessor,
    sort_active_tasks,
)
from wait_dag_v4_2_reverse_candidate import (
    API_RECORD,
    API_WAIT,
    build_run_context,
    enumerate_generation_candidates,
    evaluate_candidate,
    record_ord_on_thread,
    wait_ord_on_thread,
)
from wait_dag_v4_intervention import (
    INJECTED_KERNEL_NAME,
    inject_cti_rowids,
    load_device_work_audit,
    load_delay_audit,
    load_selector_manifest,
    node_with_offsets,
    normalized_key_to_json,
    write_csv,
)

TARGET_COMM = "hcom_allReduce__612_0_1"
SCHEMA = "d51_post_wait_comm_stream_v2"
TREATMENT_CONDITIONS = frozenset({"DSMALL", "DLARGE", "D2", "D25"})
V47_DLARGE_REALIZED_REF_NS = 3226068
V47_DSMALL_REALIZED_REF_NS = 197304
REL_TOL = 0.20

PAIRED_FIELDS = [
    "block",
    "d0_run_id",
    "dtreat_run_id",
    "extraction_status",
    "structure_gate_pass",
    "dose_gate_pass",
    "local_causal_gate_pass",
    "control_gate_pass",
    "causal_eligibility",
    "structure_reason",
    "dose_reason",
    "local_causal_reason",
    "control_reason",
    "S0_ns",
    "launch_offset_ns",
    "realized_work_ns",
    "injected_end_offset_ns",
    "predicted_unmasked_occupancy_ns",
    "observed_post_wait_shift_ns",
    "occupancy_residual_ns",
    "post_inject_gap_ns",
    "occupancy_identity_ok",
    "pair_base_key",
    "identity_method_d0",
    "identity_method_treatment",
    "reverse_c8_candidate_count",
]


def build_pair_base_key() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "rank_role": "RANK0",
        "collective_role": "TARGET_HCOM_ALLREDUCE_612_0_1",
        "event_direction_role": "COMPUTE_RECORD_TO_COMM_WAIT",
        "generation_role": "UNIQUE_COMPLETE_GENERATION_FOR_TARGET_COMM_ROLE",
        "record_stream_role": "COMPUTE",
        "wait_api_role": "SUCCESSFUL_ACLRT_STREAM_WAIT_EVENT",
        "wait_stream_role": "COMM",
        "issuing_thread_relation": "SAME_GENERATION_RECORD_WAIT_ISSUING_THREAD",
        "comm_entry_role": "FIRST_TARGET_COMM_MEMBER_ON_SELECTED_WAIT_STREAM",
    }


def task_type_name(task, string_ids: dict[int, str]) -> str:
    return string_ids.get(task.task_type, str(task.task_type)).lower()


def is_wait_task(task, wait_type: int, string_ids: dict[int, str]) -> bool:
    if task.task_type == wait_type:
        return True
    name = task_type_name(task, string_ids)
    return "wait" in name and "event" in name


def is_inject_task(task, string_ids: dict[int, str], cti_hits: set[int]) -> bool:
    if task.rowid in cti_hits:
        return True
    name = task_type_name(task, string_ids)
    return INJECTED_KERNEL_NAME in name or "d51_compute_delay" in name.lower()


def sort_stream_tasks_expanded(tasks: list[Any], active_end: int) -> list[Any]:
    """Same-stream tasks up to active_end; no active_start floor (profiler capture gap fix)."""
    visible = [t for t in tasks if t.start_ns <= active_end]
    visible.sort(key=lambda t: (t.start_ns, t.end_ns, t.rowid))
    return visible


def fifo_immediate_predecessor(
    task, all_tasks, active_start: int, active_end: int
) -> tuple[Any | None, str]:
    by_stream = stream_tasks_by_id(all_tasks)
    ordered = sort_active_tasks(by_stream.get(task.stream_id, []), active_start, active_end)
    idx = next((i for i, t in enumerate(ordered) if t.rowid == task.rowid), None)
    if idx is None or idx == 0:
        return None, "no_predecessor"
    prev = ordered[idx - 1]
    verdict, reason = evaluate_adjacent_pair(prev, task)
    if verdict != "sortable":
        return None, reason
    return prev, reason


def fifo_immediate_successor(
    task, all_tasks, active_start: int, active_end: int
) -> tuple[Any | None, str]:
    by_stream = stream_tasks_by_id(all_tasks)
    ordered = sort_active_tasks(by_stream.get(task.stream_id, []), active_start, active_end)
    idx = next((i for i, t in enumerate(ordered) if t.rowid == task.rowid), None)
    if idx is None or idx + 1 >= len(ordered):
        return None, "no_adjacent_successor"
    nxt = ordered[idx + 1]
    verdict, reason = evaluate_adjacent_pair(task, nxt)
    if verdict != "sortable":
        return None, reason
    return nxt, reason


def find_pre_wait_predecessor(
    wait_task, all_tasks, active_start: int, active_end: int
) -> tuple[Any | None, str]:
    """Direct FIFO predecessor on wait stream; expanded window closes pre-profiler tasks."""
    by_stream = stream_tasks_by_id(all_tasks)
    ordered = sort_stream_tasks_expanded(by_stream.get(wait_task.stream_id, []), active_end)
    idx = next((i for i, t in enumerate(ordered) if t.rowid == wait_task.rowid), None)
    if idx is None or idx == 0:
        return None, "no_predecessor_in_expanded_stream"
    prev = ordered[idx - 1]
    verdict, reason = evaluate_adjacent_pair(prev, wait_task)
    if verdict != "sortable":
        return None, f"pre_wait_overlap:{reason}"
    return prev, "pre_wait_direct_predecessor_expanded_window"


def find_bypass_compute(
    ctx,
    record_task,
    upstream_kernel,
    wait_task,
    comm_entry,
    inject_task=None,
) -> tuple[Any | None, str]:
    """Compute-stream kernel off target Event generation path; unique latest before record."""
    path_rowids = {record_task.rowid, wait_task.rowid, comm_entry.rowid}
    if upstream_kernel is not None:
        path_rowids.add(upstream_kernel.rowid)
    if inject_task is not None:
        path_rowids.add(inject_task.rowid)
    cti_hits = inject_cti_rowids(ctx.db_path, ctx.active_start, ctx.active_end)
    candidates: list[Any] = []
    for task in ctx.all_tasks:
        if not (ctx.active_start <= task.start_ns <= ctx.active_end):
            continue
        if task.stream_id not in ctx.compute_streams:
            continue
        if task.rowid in path_rowids or task.rowid in cti_hits:
            continue
        if not is_kernel_task(task, ctx.string_ids):
            continue
        if task.end_ns > record_task.start_ns:
            continue
        candidates.append(task)
    if not candidates:
        return None, "bypass_compute_not_found:0"
    best = max(candidates, key=lambda t: (t.end_ns, t.start_ns, t.rowid))
    ties = [
        c
        for c in candidates
        if (c.end_ns, c.start_ns, c.rowid) == (best.end_ns, best.start_ns, best.rowid)
    ]
    if len(ties) != 1:
        return None, f"bypass_compute_ambiguous:{len(ties)}"
    return best, "bypass_compute_latest_before_record_off_path"


def host_issue_gap_ns(comm_op: dict | None, wait_task) -> tuple[int | None, str]:
    """Host-side issue anchor: COMM op start minus Wait completion."""
    if comm_op is None:
        return None, "no_comm_op"
    gap = int(comm_op["start_ns"]) - int(wait_task.end_ns)
    return gap, "comm_op.startNs_minus_wait.endNs"


def build_control_anchors(
    ctx,
    wait_task,
    record_task,
    comm_entry,
    comm_op: dict | None,
    upstream_kernel,
    pre_wait_p,
    pre_wait_p_rule: str,
    inject_task=None,
) -> dict[str, Any]:
    bypass, bypass_rule = find_bypass_compute(
        ctx, record_task, upstream_kernel, wait_task, comm_entry, inject_task
    )
    host_gap, host_rule = host_issue_gap_ns(comm_op, wait_task)
    comm_duration = None
    if comm_op is not None:
        comm_duration = int(comm_op["end_ns"]) - int(comm_op["start_ns"])
    return {
        "pre_wait_p_rowid": pre_wait_p.rowid if pre_wait_p else None,
        "pre_wait_p_start_ns": pre_wait_p.start_ns if pre_wait_p else None,
        "pre_wait_p_end_ns": pre_wait_p.end_ns if pre_wait_p else None,
        "pre_wait_p_source": pre_wait_p_rule if pre_wait_p else None,
        "pre_wait_p_unique": pre_wait_p is not None,
        "bypass_compute_rowid": bypass.rowid if bypass else None,
        "bypass_compute_start_ns": bypass.start_ns if bypass else None,
        "bypass_compute_end_ns": bypass.end_ns if bypass else None,
        "bypass_compute_source": bypass_rule if bypass else bypass_rule,
        "bypass_compute_unique": bypass is not None,
        "host_issue_gap_ns": host_gap,
        "host_issue_comm_start_ns": int(comm_op["start_ns"]) if comm_op else None,
        "host_issue_wait_end_ns": int(wait_task.end_ns),
        "host_issue_source": host_rule if host_gap is not None else host_rule,
        "host_issue_unique": host_gap is not None,
        "target_comm_duration_ns": comm_duration,
        "wait_duration_ns": int(wait_task.end_ns - wait_task.start_ns),
        "record_end_ns": int(record_task.end_ns),
    }


def verify_comm_entry(comm_entry, comm_op, stream_id, tasks_by_cid, active_start, active_end):
    first, status, _ = first_comm_entry_on_stream(
        comm_op, tasks_by_cid, stream_id, active_start, active_end
    )
    if first is None:
        return False, f"comm_entry_resolve_fail:{status}"
    if first.rowid != comm_entry.rowid:
        return False, "comm_entry_not_first_on_stream"
    return True, "ok"


def generation_layer_pass(cand: dict, ctx) -> tuple[bool, str, dict]:
    ok, trace, summary = evaluate_candidate(ctx, cand, TARGET_COMM)
  # IT-1 / I0 generation: C1-C4 only for treatment layer audit
    c_conds = {t["condition"]: t["passed"] for t in trace}
    gen_ok = all(c_conds.get(c, False) for c in ("C1", "C2", "C3", "C4"))
    c8 = c_conds.get("C8", False)
    reverse_c8 = {
        "all_pass": ok,
        "c8_passed": c8,
        "candidate_count": 1 if ok else 0,
        "first_blocker": summary.get("first_blocker", ""),
    }
    if not gen_ok:
        blocker = next((t["condition"] for t in trace if not t["passed"]), "generation_fail")
        return False, blocker, reverse_c8
    return True, "ok", reverse_c8


def extract_d0_i0(ctx, run_id: str, condition: str) -> dict[str, Any]:
    comm_op = next((c for c in ctx.comm_ops if c.get("op_name") == TARGET_COMM), None)
    if comm_op is None:
        return {"run_id": run_id, "condition": condition, "status": "STOP_D0_REVERSE_IDENTITY_NOT_UNIQUE"}

    candidates: list[dict[str, Any]] = []
    streams = {
        t.stream_id
        for t in ctx.tasks_by_cid.get(int(comm_op["connection_id"]), [])
        if ctx.active_start <= t.start_ns <= ctx.active_end
    }

    for stream_id in streams:
        q, q_status, _ = first_comm_entry_on_stream(
            comm_op, ctx.tasks_by_cid, stream_id, ctx.active_start, ctx.active_end
        )
        if q is None:
            continue
        pred, pred_rule = fifo_immediate_predecessor(
            q, ctx.all_tasks, ctx.active_start, ctx.active_end
        )
        if pred is None:
            continue
        if not is_wait_task(pred, ctx.wait_type, ctx.string_ids):
            continue
        gen_matches = []
        for cand in enumerate_generation_candidates(ctx):
            g_ok, _, rev_c8 = generation_layer_pass(cand, ctx)
            if not g_ok:
                continue
            _, trace, summary = evaluate_candidate(ctx, cand, TARGET_COMM)
            if summary.get("wait_task_rowid") != pred.rowid:
                continue
            if summary.get("comm_entry_rowid") != q.rowid:
                continue
            gen_matches.append({"cand": cand, "summary": summary, "reverse_c8": rev_c8})
        if len(gen_matches) == 1:
            candidates.append(
                {
                    "q": q,
                    "wait_task": pred,
                    "comm_op": comm_op,
                    "summary": gen_matches[0]["summary"],
                    "reverse_c8": gen_matches[0]["reverse_c8"],
                }
            )

    if len(candidates) != 1:
        return {
            "run_id": run_id,
            "condition": condition,
            "status": "STOP_D0_REVERSE_IDENTITY_NOT_UNIQUE",
            "candidate_count": len(candidates),
        }

    win = candidates[0]
    summary = win["summary"]
    wait_task = win["wait_task"]
    q = win["q"]
    comm = win["comm_op"]
    rec_task = ctx.all_tasks_by_rowid.get(summary["record_task_rowid"])
    upstream_kernel = (
        ctx.all_tasks_by_rowid.get(summary["kernel_predecessor_rowid"])
        if summary.get("kernel_predecessor_rowid")
        else None
    )
    pre_wait_p, pre_wait_p_rule = find_pre_wait_predecessor(
        wait_task, ctx.all_tasks, ctx.active_start, ctx.active_end
    )
    norm_key = normalized_key_to_json(build_pair_base_key())
    anchor_end = upstream_kernel.end_ns if upstream_kernel else (rec_task.end_ns if rec_task else wait_task.end_ns)
    control_anchors = build_control_anchors(
        ctx,
        wait_task,
        rec_task,
        q,
        comm,
        upstream_kernel,
        pre_wait_p,
        pre_wait_p_rule,
    )

    identity = {
        "run_id": run_id,
        "condition": condition,
        "status": "OK",
        "identity_method": "UNIQUE_REVERSE_WAIT_TO_TARGET_COMM",
        "pair_base_key": norm_key,
        "wait_task_rowid": wait_task.rowid,
        "comm_entry_rowid": q.rowid,
        "inject_task_rowid": None,
        "record_task_rowid": summary.get("record_task_rowid"),
        "wait_stream_id": wait_task.stream_id,
        "reverse_c8_candidate_count": win["reverse_c8"].get("candidate_count", 0),
        "run_local_identity": {
            "preload_record_cs": summary.get("record_call_sequence"),
            "preload_wait_cs": summary.get("wait_call_sequence"),
            "wait_task_rowid": wait_task.rowid,
            "comm_entry_rowid": q.rowid,
            "wait_stream_id": wait_task.stream_id,
        },
    }

    nodes = [
        node_with_offsets(run_id, condition, "wait_task", wait_task.start_ns, wait_task.end_ns, anchor_end),
        node_with_offsets(run_id, condition, "comm_entry", q.start_ns, q.end_ns, anchor_end),
    ]
    if rec_task:
        nodes.append(
            node_with_offsets(
                run_id, condition, "record_task", rec_task.start_ns, rec_task.end_ns, anchor_end
            )
        )

    return {
        "run_id": run_id,
        "condition": condition,
        "status": "OK",
        "identity": identity,
        "nodes": nodes,
        "post_wait_to_entry_ns": q.start_ns - wait_task.end_ns,
        "realized_work_ns": 0,
        "pre_wait_p_rowid": pre_wait_p.rowid if pre_wait_p else None,
        "control_anchors": control_anchors,
        "normalized_structure_key": norm_key,
        "wait_task": wait_task,
        "comm_entry": q,
        "inject_task": None,
        "comm_op": comm,
    }


def project_inject_from_sidecar(
    ctx, audit: dict[str, Any], wait_task, all_tasks, string_ids, active_start, active_end
) -> tuple[Any | None, str]:
    launch_count = int(audit.get("launch_count", audit.get("match_count", 0)) or 0)
    if launch_count != 1:
        return None, "launch_count_not_one"
    inject_site = str(audit.get("inject_site", ""))
    if inject_site != "AFTER_SUCCESSFUL_TARGET_WAIT":
        return None, f"inject_site_mismatch:{inject_site}"
    cti_hits = inject_cti_rowids(ctx.db_path, active_start, active_end)
    if not cti_hits:
        return None, "no_cti_hits"
    cti_tasks = [ctx.all_tasks_by_rowid[r] for r in cti_hits if r in ctx.all_tasks_by_rowid]
    if len(cti_tasks) != 1:
        return None, f"cti_not_unique:{len(cti_tasks)}"
    inject_task = cti_tasks[0]
    if not is_inject_task(inject_task, string_ids, cti_hits):
        return None, "cti_not_inject_kernel"
    raw_stream = int(audit.get("raw_stream", 0) or 0)
    if raw_stream and inject_task.stream_id != wait_task.stream_id:
        return None, "inject_stream_mismatch_profiler"
    trigger_cs = int(audit.get("trigger_wait_preload_cs", 0) or 0)
    if trigger_cs <= 0:
        return None, "trigger_wait_cs_missing"
    return inject_task, "sidecar_cti_bidirectional_ok"


def extract_treatment_it(
    ctx,
    run_id: str,
    condition: str,
    audit: dict[str, Any],
    selector_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    chains: list[dict[str, Any]] = []
    reverse_c8_audit: dict[str, Any] = {}

    for cand in enumerate_generation_candidates(ctx):
        g_ok, g_reason, rev_c8 = generation_layer_pass(cand, ctx)
        reverse_c8_audit = rev_c8
        if not g_ok:
            continue
        _, _, summary = evaluate_candidate(ctx, cand, TARGET_COMM)
        wait_task = summary.get("wait_task")
        rec_task = summary.get("record_task")
        if wait_task is None or rec_task is None:
            continue

        comm_op = next((c for c in ctx.comm_ops if c.get("op_name") == TARGET_COMM), None)
        if comm_op is None:
            continue
        q, q_status, _ = first_comm_entry_on_stream(
            comm_op,
            ctx.tasks_by_cid,
            wait_task.stream_id,
            ctx.active_start,
            ctx.active_end,
        )
        if q is None:
            continue
        ok_ce, ce_reason = verify_comm_entry(
            q, comm_op, wait_task.stream_id, ctx.tasks_by_cid, ctx.active_start, ctx.active_end
        )
        if not ok_ce:
            continue

        injected_kernel, inj_rule = project_inject_from_sidecar(
            ctx,
            audit,
            wait_task,
            ctx.all_tasks,
            ctx.string_ids,
            ctx.active_start,
            ctx.active_end,
        )
        if injected_kernel is None:
            continue

        w_inj, wr = evaluate_adjacent_pair(wait_task, injected_kernel)
        i_q, ir = evaluate_adjacent_pair(injected_kernel, q)
        if w_inj != "sortable" or i_q != "sortable":
            continue

        inject_cs = int(audit.get("trigger_wait_preload_cs", 0) or 0)
        wait_rec = cand["wait_rec"]
        if inject_cs != int(wait_rec.call_sequence):
            continue

        chains.append(
            {
                "summary": summary,
                "wait_task": wait_task,
                "rec_task": rec_task,
                "comm_op": comm_op,
                "comm_entry": q,
                "injected_kernel": injected_kernel,
                "reverse_c8": rev_c8,
            }
        )

    if selector_manifest and chains:
        sm_ord = int(selector_manifest.get("wait_active_success_ordinal", -1))
        chains = [
            c
            for c in chains
            if c["summary"].get("wait_active_success_ordinal") == sm_ord
        ]
        if not chains:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "STOP_MANIFEST_GUIDED_IDENTITY_BYPASS",
            }

    if len(chains) != 1:
        return {
            "run_id": run_id,
            "condition": condition,
            "status": "STOP_TREATMENT_LAYERED_IDENTITY_NOT_UNIQUE",
            "candidate_count": len(chains),
            "reverse_c8_candidate_count": reverse_c8_audit.get("candidate_count", 0),
        }

    win = chains[0]
    wait_task = win["wait_task"]
    q = win["comm_entry"]
    injected_kernel = win["injected_kernel"]
    rec_task = win["rec_task"]
    summary = win["summary"]
    upstream_kernel = (
        ctx.all_tasks_by_rowid.get(summary.get("kernel_predecessor_rowid"))
        if summary.get("kernel_predecessor_rowid")
        else None
    )
    pre_wait_p, pre_wait_p_rule = find_pre_wait_predecessor(
        wait_task, ctx.all_tasks, ctx.active_start, ctx.active_end
    )
    norm_key = normalized_key_to_json(build_pair_base_key())
    anchor_end = upstream_kernel.end_ns if upstream_kernel else rec_task.end_ns
    control_anchors = build_control_anchors(
        ctx,
        wait_task,
        rec_task,
        q,
        win["comm_op"],
        upstream_kernel,
        pre_wait_p,
        pre_wait_p_rule,
        inject_task=injected_kernel,
    )
    launch_count = int(audit.get("launch_count", 0) or 0)
    inject_site = str(audit.get("inject_site", ""))

    identity = {
        "run_id": run_id,
        "condition": condition,
        "status": "OK",
        "identity_method": "LAYERED_TREATMENT_GENERATION_INJECT_COMM_PROJECTION",
        "pair_base_key": norm_key,
        "wait_task_rowid": wait_task.rowid,
        "comm_entry_rowid": q.rowid,
        "inject_task_rowid": injected_kernel.rowid,
        "record_task_rowid": rec_task.rowid,
        "wait_stream_id": wait_task.stream_id,
        "reverse_c8_candidate_count": win["reverse_c8"].get("candidate_count", 0),
        "reverse_c8_all_pass": win["reverse_c8"].get("all_pass", False),
        "inject_site": inject_site,
        "launch_count": launch_count,
        "requested_iters": int(audit.get("requested_iters", 0) or 0),
        "trigger_wait_preload_cs": int(audit.get("trigger_wait_preload_cs", 0) or 0),
        "run_local_identity": {
            "preload_record_cs": summary.get("record_call_sequence"),
            "preload_wait_cs": summary.get("wait_call_sequence"),
            "inject_task_rowid": injected_kernel.rowid,
            "comm_entry_rowid": q.rowid,
            "wait_stream_id": wait_task.stream_id,
            "raw_stream": audit.get("raw_stream"),
        },
    }

    nodes = [
        node_with_offsets(
            run_id, condition, "wait_task", wait_task.start_ns, wait_task.end_ns, anchor_end
        ),
        node_with_offsets(
            run_id,
            condition,
            "injected_kernel",
            injected_kernel.start_ns,
            injected_kernel.end_ns,
            anchor_end,
        ),
        node_with_offsets(run_id, condition, "comm_entry", q.start_ns, q.end_ns, anchor_end),
        node_with_offsets(
            run_id, condition, "record_task", rec_task.start_ns, rec_task.end_ns, anchor_end
        ),
    ]

    return {
        "run_id": run_id,
        "condition": condition,
        "status": "OK",
        "identity": identity,
        "nodes": nodes,
        "post_wait_to_entry_ns": q.start_ns - wait_task.end_ns,
        "realized_work_ns": int(injected_kernel.end_ns - injected_kernel.start_ns),
        "pre_wait_p_rowid": pre_wait_p.rowid if pre_wait_p else None,
        "control_anchors": control_anchors,
        "normalized_structure_key": norm_key,
        "wait_task": wait_task,
        "comm_entry": q,
        "inject_task": injected_kernel,
        "comm_op": win["comm_op"],
        "audit": audit,
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

    ctx = build_run_context(run_dir)
    cond_upper = condition.upper()

    if cond_upper == "D0":
        if int(audit.get("launch_count", audit.get("match_count", 0)) or 0) != 0:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "D0_UNEXPECTED_LAUNCH",
            }
        return extract_d0_i0(ctx, run_id, condition)

    if cond_upper in TREATMENT_CONDITIONS:
        return extract_treatment_it(ctx, run_id, condition, audit, selector_manifest)

    return {"run_id": run_id, "condition": condition, "status": "UNKNOWN_CONDITION"}


def dose_reference_ns(iters: int) -> int:
    if iters >= 5000:
        return V47_DLARGE_REALIZED_REF_NS
    return V47_DSMALL_REALIZED_REF_NS


def evaluate_dose_gate(realized_work: int, requested_iters: int) -> tuple[bool, str | None]:
    if realized_work <= 0:
        return False, "REALIZED_WORK_INVALID"
    ref = dose_reference_ns(requested_iters)
    tol = int(REL_TOL * ref)
    if abs(realized_work - ref) > tol:
        return False, f"dose_ref_miss:{realized_work} vs ref {ref}"
    return True, None


def causal_eligibility(
    predicted_unmasked: int, post_inject_gap: int, realized: int
) -> tuple[str, str | None]:
    reasons: list[str] = []
    if predicted_unmasked <= 0:
        reasons.append("MASKED_BY_D0_SLACK")
    if realized > 0 and post_inject_gap / realized > REL_TOL:
        reasons.append("POST_INJECT_GAP_TOO_LARGE")
    if reasons:
        return "STRUCTURE_ONLY_NOT_CAUSAL", ";".join(reasons)
    return "CAUSAL_ELIGIBLE", None


def evaluate_causal_closure(
    observed_shift: int,
    predicted_unmasked: int,
    occupancy_residual: int,
    post_inject_gap: int,
    realized: int,
) -> tuple[bool | None, str | None]:
    elig, elig_reason = causal_eligibility(predicted_unmasked, post_inject_gap, realized)
    if elig != "CAUSAL_ELIGIBLE":
        return None, elig_reason
    if observed_shift <= 0 or predicted_unmasked <= 0:
        return False, "non_positive_observed_or_predicted"
    rel_res = abs(occupancy_residual) / predicted_unmasked
    rel_gap = post_inject_gap / realized if realized > 0 else 1.0
    if rel_res > REL_TOL or rel_gap > REL_TOL:
        return False, f"closure_fail:res={rel_res:.4f} gap={rel_gap:.4f}"
    return True, None


def occupancy_identity_check(
    q_start: int,
    inject_end: int,
    observed_shift: int,
    predicted_unmasked: int,
    occupancy_residual: int,
    post_inject_gap: int,
    s0: int,
    wait_end: int,
    inject_start: int,
) -> bool:
    if predicted_unmasked <= 0:
        return True
    gap_from_q = q_start - inject_end
    alt_residual = observed_shift - predicted_unmasked
    return (
        occupancy_residual == post_inject_gap == gap_from_q
        and alt_residual == gap_from_q
        and (q_start - wait_end - s0) - (inject_end - wait_end - s0) == gap_from_q
    )


def paired_effects_v4_7(d0: dict[str, Any], dtreat: dict[str, Any], block: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "block": block,
        "d0_run_id": d0["run_id"],
        "dtreat_run_id": dtreat["run_id"],
        "extraction_status": "OK",
    }
    d0_key = d0.get("normalized_structure_key", "")
    dt_key = dtreat.get("normalized_structure_key", "")
    if d0_key != dt_key:
        base.update(
            {
                "extraction_status": "PAIR_STRUCTURE_MISMATCH",
                "structure_gate_pass": False,
                "structure_reason": "PAIR_BASE_KEY_MISMATCH",
                "dose_gate_pass": False,
                "local_causal_gate_pass": None,
                "control_gate_pass": None,
                "causal_eligibility": None,
                "pair_base_key": d0_key,
            }
        )
        return base

    base["structure_gate_pass"] = True
    base["structure_reason"] = ""
    base["pair_base_key"] = d0_key
    base["identity_method_d0"] = d0.get("identity", {}).get("identity_method", "")
    base["identity_method_treatment"] = dtreat.get("identity", {}).get("identity_method", "")
    base["reverse_c8_candidate_count"] = dtreat.get("identity", {}).get(
        "reverse_c8_candidate_count", ""
    )

    realized = int(dtreat.get("realized_work_ns", 0) or 0)
    req_iters = int(dtreat.get("identity", {}).get("requested_iters", 0) or 0)
    dose_ok, dose_reason = evaluate_dose_gate(realized, req_iters)
    base["dose_gate_pass"] = dose_ok
    base["dose_reason"] = dose_reason or ""
    base["realized_work_ns"] = realized

    if not dose_ok:
        base["local_causal_gate_pass"] = None
        base["control_gate_pass"] = None
        base["causal_eligibility"] = None
        return base

    wait0 = d0["wait_task"]
    waitT = dtreat["wait_task"]
    q0 = d0["comm_entry"]
    qT = dtreat["comm_entry"]
    inject = dtreat["inject_task"]

    s0 = int(q0.start_ns - wait0.end_ns)
    launch_offset = int(inject.start_ns - waitT.end_ns)
    injected_end_offset = int(inject.end_ns - waitT.end_ns)
    predicted_unmasked = max(0, injected_end_offset - s0)
    observed_shift = int((qT.start_ns - waitT.end_ns) - s0)
    post_inject_gap = int(qT.start_ns - inject.end_ns)
    occupancy_residual = observed_shift - predicted_unmasked

    base.update(
        {
            "S0_ns": s0,
            "launch_offset_ns": launch_offset,
            "injected_end_offset_ns": injected_end_offset,
            "predicted_unmasked_occupancy_ns": predicted_unmasked,
            "observed_post_wait_shift_ns": observed_shift,
            "occupancy_residual_ns": occupancy_residual,
            "post_inject_gap_ns": post_inject_gap,
        }
    )

    id_ok = occupancy_identity_check(
        qT.start_ns,
        inject.end_ns,
        observed_shift,
        predicted_unmasked,
        occupancy_residual,
        post_inject_gap,
        s0,
        waitT.end_ns,
        inject.start_ns,
    )
    base["occupancy_identity_ok"] = id_ok

    elig, elig_reason = causal_eligibility(predicted_unmasked, post_inject_gap, realized)
    base["causal_eligibility"] = elig

    causal_ok, causal_reason = evaluate_causal_closure(
        observed_shift, predicted_unmasked, occupancy_residual, post_inject_gap, realized
    )
    base["local_causal_gate_pass"] = causal_ok
    base["local_causal_reason"] = causal_reason or elig_reason or ""

    def rel_end(nodes: list[dict], name: str) -> int:
        return int(next(n for n in nodes if n["node"] == name)["end_offset_from_upstream_kernel_end_ns"])

    def rel_start(nodes: list[dict], name: str) -> int:
        return int(
            next(n for n in nodes if n["node"] == name)["start_offset_from_upstream_kernel_end_ns"]
        )

    d0n, dtn = d0["nodes"], dtreat["nodes"]
    rec_shift = rel_end(dtn, "record_task") - rel_end(d0n, "record_task")
    wait_shift = rel_end(dtn, "wait_task") - rel_end(d0n, "wait_task")
    ctrl_tol = int(REL_TOL * realized)

    ca_d0 = d0.get("control_anchors") or {}
    ca_t = dtreat.get("control_anchors") or {}
    control_reasons: list[str] = []

    if not ca_d0.get("pre_wait_p_unique") or not ca_t.get("pre_wait_p_unique"):
        control_reasons.append("pre_wait_p_unavailable")
    if not ca_d0.get("bypass_compute_unique") or not ca_t.get("bypass_compute_unique"):
        control_reasons.append("bypass_compute_unavailable")
    if not ca_d0.get("host_issue_unique") or not ca_t.get("host_issue_unique"):
        control_reasons.append("host_issue_unavailable")

    if control_reasons:
        base["control_gate_pass"] = False
        base["control_reason"] = ";".join(control_reasons)
        if "pre_wait_p_unavailable" in control_reasons:
            base["pre_wait_p_evidence"] = (
                f"d0:{ca_d0.get('pre_wait_p_source')};dt:{ca_t.get('pre_wait_p_source')}"
            )
    elif abs(rec_shift) > ctrl_tol or abs(wait_shift) > ctrl_tol:
        base["control_gate_pass"] = False
        base["control_reason"] = f"pre_wait_confound:rec={rec_shift} wait={wait_shift}"
    else:
        p_shift = int(ca_t["pre_wait_p_end_ns"]) - int(ca_d0["pre_wait_p_end_ns"])
        bypass_shift = int(ca_t["bypass_compute_end_ns"]) - int(ca_d0["bypass_compute_end_ns"])
        host_shift = int(ca_t["host_issue_gap_ns"]) - int(ca_d0["host_issue_gap_ns"])
        confounds: list[str] = []
        if abs(p_shift) > ctrl_tol:
            confounds.append(f"pre_wait_p={p_shift}")
        if abs(bypass_shift) > ctrl_tol:
            confounds.append(f"bypass_compute={bypass_shift}")
        if abs(host_shift) > ctrl_tol:
            confounds.append(f"host_issue_gap={host_shift}")
        if confounds:
            base["control_gate_pass"] = False
            base["control_reason"] = "STOP_GLOBAL_SCHEDULING_OR_CONTENTION_CONFOUND:" + ",".join(
                confounds
            )
        else:
            base["control_gate_pass"] = True
            base["control_reason"] = ""

    return base


CONTROL_EFFECT_FIELDS = [
    "metric",
    "d0_run_id",
    "dtreat_run_id",
    "shift_ns",
    "d0_endpoint_ns",
    "dtreat_endpoint_ns",
    "d0_source",
    "dtreat_source",
    "unique",
]


def _shift_row(
    metric: str,
    d0: dict,
    dtreat: dict,
    d0_val,
    dtreat_val,
    d0_src: str,
    dtreat_src: str,
    unique: bool,
) -> dict:
    if not unique or d0_val is None or dtreat_val is None:
        return {
            "metric": metric,
            "d0_run_id": d0["run_id"],
            "dtreat_run_id": dtreat["run_id"],
            "shift_ns": "CONTROL_UNAVAILABLE",
            "d0_endpoint_ns": d0_val if d0_val is not None else "",
            "dtreat_endpoint_ns": dtreat_val if dtreat_val is not None else "",
            "d0_source": d0_src,
            "dtreat_source": dtreat_src,
            "unique": unique,
        }
    shift = int(dtreat_val) - int(d0_val)
    return {
        "metric": metric,
        "d0_run_id": d0["run_id"],
        "dtreat_run_id": dtreat["run_id"],
        "shift_ns": shift,
        "d0_endpoint_ns": d0_val,
        "dtreat_endpoint_ns": dtreat_val,
        "d0_source": d0_src,
        "dtreat_source": dtreat_src,
        "unique": unique,
    }


def build_control_effects(d0: dict, dtreat: dict) -> list[dict]:
    rows = []
    if d0.get("status") != "OK" or dtreat.get("status") != "OK":
        return rows
    d0n, dtn = d0["nodes"], dtreat["nodes"]
    ca_d0 = d0.get("control_anchors") or {}
    ca_t = dtreat.get("control_anchors") or {}

    def rel_start(nodes, name):
        return int(next(n for n in nodes if n["node"] == name)["start_offset_from_upstream_kernel_end_ns"])

    def rel_end(nodes, name):
        return int(next(n for n in nodes if n["node"] == name)["end_offset_from_upstream_kernel_end_ns"])

    rows.append(
        _shift_row(
            "record_offset_shift",
            d0,
            dtreat,
            rel_start(d0n, "record_task"),
            rel_start(dtn, "record_task"),
            "node_wallclock.record_task.start_offset",
            "node_wallclock.record_task.start_offset",
            True,
        )
    )
    rows.append(
        _shift_row(
            "wait_duration_shift",
            d0,
            dtreat,
            ca_d0.get("wait_duration_ns"),
            ca_t.get("wait_duration_ns"),
            "wait_task.endNs-startNs",
            "wait_task.endNs-startNs",
            ca_d0.get("wait_duration_ns") is not None and ca_t.get("wait_duration_ns") is not None,
        )
    )
    rows.append(
        _shift_row(
            "wait_completion_shift",
            d0,
            dtreat,
            rel_start(d0n, "wait_task"),
            rel_start(dtn, "wait_task"),
            "node_wallclock.wait_task.start_offset",
            "node_wallclock.wait_task.start_offset",
            True,
        )
    )
    rows.append(
        _shift_row(
            "comm_entry_shift",
            d0,
            dtreat,
            rel_start(d0n, "comm_entry"),
            rel_start(dtn, "comm_entry"),
            "node_wallclock.comm_entry.start_offset",
            "node_wallclock.comm_entry.start_offset",
            True,
        )
    )
    rows.append(
        _shift_row(
            "pre_wait_p_shift",
            d0,
            dtreat,
            ca_d0.get("pre_wait_p_end_ns"),
            ca_t.get("pre_wait_p_end_ns"),
            ca_d0.get("pre_wait_p_source") or "",
            ca_t.get("pre_wait_p_source") or "",
            bool(ca_d0.get("pre_wait_p_unique") and ca_t.get("pre_wait_p_unique")),
        )
    )
    rows.append(
        _shift_row(
            "bypass_compute_shift",
            d0,
            dtreat,
            ca_d0.get("bypass_compute_end_ns"),
            ca_t.get("bypass_compute_end_ns"),
            ca_d0.get("bypass_compute_source") or "",
            ca_t.get("bypass_compute_source") or "",
            bool(ca_d0.get("bypass_compute_unique") and ca_t.get("bypass_compute_unique")),
        )
    )
    rows.append(
        _shift_row(
            "host_issue_gap_shift",
            d0,
            dtreat,
            ca_d0.get("host_issue_gap_ns"),
            ca_t.get("host_issue_gap_ns"),
            ca_d0.get("host_issue_source") or "",
            ca_t.get("host_issue_source") or "",
            bool(ca_d0.get("host_issue_unique") and ca_t.get("host_issue_unique")),
        )
    )
    rows.append(
        _shift_row(
            "target_comm_duration_shift",
            d0,
            dtreat,
            ca_d0.get("target_comm_duration_ns"),
            ca_t.get("target_comm_duration_ns"),
            "comm_op.endNs-startNs",
            "comm_op.endNs-startNs",
            ca_d0.get("target_comm_duration_ns") is not None
            and ca_t.get("target_comm_duration_ns") is not None,
        )
    )
    p_ok = ca_d0.get("pre_wait_p_unique") and ca_t.get("pre_wait_p_unique")
    rows.append(
        {
            "metric": "pre_wait_p_available",
            "d0_run_id": d0["run_id"],
            "dtreat_run_id": dtreat["run_id"],
            "shift_ns": "yes" if p_ok else "CONTROL_UNAVAILABLE",
            "d0_endpoint_ns": ca_d0.get("pre_wait_p_rowid") or "",
            "dtreat_endpoint_ns": ca_t.get("pre_wait_p_rowid") or "",
            "d0_source": ca_d0.get("pre_wait_p_source") or "",
            "dtreat_source": ca_t.get("pre_wait_p_source") or "",
            "unique": p_ok,
        }
    )
    return rows


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
    if isinstance(manifest_obj, list):
        runs, pairs = manifest_obj, []
    else:
        runs = manifest_obj.get("runs", [])
        pairs = manifest_obj.get("pairs", [])

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    extracted: dict[str, dict] = {}
    identity_rows: list[dict] = []
    injection_rows: list[dict] = []
    kernel_rows: list[dict] = []
    ledger_rows: list[dict] = []
    node_rows: list[dict] = []
    comm_projection_rows: list[dict] = []

    for spec in runs:
        run_id = spec["run_id"]
        condition = spec["condition"]
        run_dir = Path(spec["run_dir"])
        try:
            ex = extract_run(run_dir, run_id, condition, selector_manifest=selector_manifest)
        except Exception as e:  # noqa: BLE001
            ex = {"run_id": run_id, "condition": condition, "status": f"ERROR:{e}"}
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
        injection_rows.append(
            {
                "run_id": run_id,
                "condition": condition,
                "inject_site": ex["identity"].get("inject_site"),
                "trigger_wait_preload_cs": ex["identity"].get("trigger_wait_preload_cs"),
                "launch_count": ex["identity"].get("launch_count"),
                "post_wait_to_entry_ns": ex.get("post_wait_to_entry_ns"),
                "identity_method": ex["identity"].get("identity_method"),
            }
        )
        comm_projection_rows.append(
            {
                "run_id": run_id,
                "condition": condition,
                "comm_entry_rowid": ex["identity"].get("comm_entry_rowid"),
                "wait_stream_id": ex["identity"].get("wait_stream_id"),
                "inject_task_rowid": ex["identity"].get("inject_task_rowid"),
            }
        )
        if ex.get("realized_work_ns", 0) > 0:
            kernel_rows.append(
                {
                    "run_id": run_id,
                    "condition": condition,
                    "inject_task_rowid": ex["identity"].get("inject_task_rowid"),
                    "profiler_duration_ns": ex["realized_work_ns"],
                    "requested_iters": ex["identity"].get("requested_iters"),
                }
            )

    paired: list[dict] = []
    control_rows: list[dict] = []
    for pair in pairs:
        if len(pair) != 3:
            continue
        block, d0_id, dt_id = pair
        d0, dt = extracted.get(d0_id), extracted.get(dt_id)
        if not d0 or not dt or d0.get("status") != "OK" or dt.get("status") != "OK":
            paired.append(
                {
                    "block": block,
                    "d0_run_id": d0_id,
                    "dtreat_run_id": dt_id,
                    "extraction_status": "PAIR_INVALID",
                    "structure_gate_pass": False,
                }
            )
            continue
        paired.append(paired_effects_v4_7(d0, dt, block))
        control_rows.extend(build_control_effects(d0, dt))

    id_fields = list(identity_rows[0].keys()) if identity_rows else ["run_id"]
    write_csv(out / "intervention_identity.csv", identity_rows, id_fields)
    write_csv(
        out / "post_wait_injection_audit.csv",
        injection_rows,
        list(injection_rows[0].keys()) if injection_rows else ["run_id"],
    )
    write_csv(
        out / "kernel_realization.csv",
        kernel_rows,
        ["run_id", "condition", "inject_task_rowid", "profiler_duration_ns", "requested_iters"],
    )
    write_csv(
        out / "comm_entry_projection.csv",
        comm_projection_rows,
        list(comm_projection_rows[0].keys()) if comm_projection_rows else ["run_id"],
    )
    write_csv(
        out / "node_wallclock.csv",
        node_rows,
        list(node_rows[0].keys()) if node_rows else ["run_id"],
    )
    write_csv(out / "paired_effects_v4_7.csv", paired, PAIRED_FIELDS)
    write_csv(
        out / "control_effects.csv",
        control_rows,
        CONTROL_EFFECT_FIELDS,
    )
    write_csv(out / "run_ledger.csv", ledger_rows, ["run_id", "condition", "status", "run_dir"])

    claims = [
        "# D51 Wait DAG V4.7 claims (Builder draft — not GO)",
        "",
        "Main estimand: observed_post_wait_shift_ns vs predicted_unmasked_occupancy_ns.",
        "When predicted_unmasked>0: occupancy_residual_ns ≡ post_inject_gap_ns (FIFO tightness).",
        "",
    ]
    (out / "claims.md").write_text("\n".join(claims) + "\n")

    summary = {"identity_ok": len(identity_rows), "paired": paired, "schema": SCHEMA}
    (out / "v4_7_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()

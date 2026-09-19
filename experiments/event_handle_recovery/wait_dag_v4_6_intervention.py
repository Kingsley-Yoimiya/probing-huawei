#!/usr/bin/env python3
"""D51 Wait DAG V4.6: post-Wait comm-stream device work + local entry delay estimand."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from analyze_event_pairs import load_comm_ops, load_string_ids, load_tasks, stream_tasks_by_id
from wait_dag_v2_fifo import (
    evaluate_adjacent_pair,
    first_comm_entry_on_stream,
    nearest_kernel_predecessor,
    sort_active_tasks,
)
from wait_dag_v4_2_reverse_candidate import (
    API_RECORD as REV_API_RECORD,
    API_WAIT as REV_API_WAIT,
    build_run_context,
    enumerate_generation_candidates,
    extract_reverse_candidates,
    record_ord_on_thread,
    wait_ord_on_thread,
)
from wait_dag_v2_build import align_cann_for_record, align_cann_for_wait, project_cann_to_task
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
API_WAIT = "aclrtStreamWaitEvent"
RECORD_TID_ROLE = "AFTER_ARM_FIRST_SUCCESSFUL_RECORD_TID"
SCHEMA = "d51_post_wait_comm_stream_v1"
TREATMENT_CONDITIONS = frozenset({"DSMALL", "DLARGE", "D2", "D25"})
V45_DLARGE_REALIZED_REF_NS = 3225872
V45_DSMALL_REALIZED_REF_NS = 184000  # V4.5 b1 Dsmall median order-of-magnitude anchor

PAIRED_FIELDS = [
    "block",
    "d0_run_id",
    "dtreat_run_id",
    "extraction_status",
    "structure_gate_pass",
    "dose_gate_pass",
    "local_causal_gate_pass",
    "control_gate_pass",
    "structure_reason",
    "dose_reason",
    "local_causal_reason",
    "control_reason",
    "observed_entry_delay_ns",
    "expected_entry_delay_ns",
    "realized_work_ns",
    "post_wait_to_entry_d0_ns",
    "post_wait_to_entry_treat_ns",
    "pre_wait_anchored_shift_ns",
    "record_shift_ns",
    "wait_completion_shift_ns",
    "comm_entry_shift_ns",
    "legacy_metric_tier",
    "pair_base_key",
    "intervention_identity_key",
]


def build_pair_base_key(summary: dict[str, Any], target_comm: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "rank": 0,
        "comm_op": target_comm,
        "event_generation_role": "UNIQUE_REVERSE_CANDIDATE_FOR_TARGET_COMM",
        "record_stream_role": "compute",
        "wait_api": API_WAIT,
        "wait_stream_role": "comm",
        "wait_tid_role": "SAME_ISSUING_THREAD_AS_SELECTED_GENERATION",
        "wait_active_success_ordinal": summary["wait_active_success_ordinal"],
        "comm_entry_role": "FIRST_TASK_OF_TARGET_COMM_ON_SELECTED_WAIT_STREAM",
    }


def build_intervention_identity_key(is_treatment: bool) -> dict[str, Any]:
    if not is_treatment:
        return {}
    return {
        "site": "AFTER_SUCCESSFUL_TARGET_WAIT",
        "raw_stream_relation": "EXACT_TARGET_WAIT_RAW_STREAM",
        "launch_count": 1,
        "injected_task_role": "UNIQUE_D51_KERNEL_IMMEDIATE_SUCCESSOR_OF_WAIT",
        "target_comm_entry_role": "FIRST_TARGET_COMM_TASK_IMMEDIATE_SUCCESSOR_OF_INJECT",
    }


def task_type_name(task, string_ids: dict[int, str]) -> str:
    return string_ids.get(task.task_type, str(task.task_type)).lower()


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


def is_inject_task(task, string_ids: dict[int, str], cti_hits: set[int]) -> bool:
    if task.rowid in cti_hits:
        return True
    name = task_type_name(task, string_ids)
    return INJECTED_KERNEL_NAME in name or "d51_compute_delay" in name.lower()


def find_inject_after_wait(
    wait_task, all_tasks, string_ids, active_start, active_end, db_path: Path
) -> tuple[Any | None, str]:
    succ, rule = fifo_immediate_successor(wait_task, all_tasks, active_start, active_end)
    if succ is None:
        return None, f"no_wait_successor:{rule}"
    cti_hits = inject_cti_rowids(db_path, active_start, active_end)
    if is_inject_task(succ, string_ids, cti_hits):
        return succ, "inject_immediate_successor_of_wait"
    return None, "wait_successor_not_inject"


def find_pre_wait_predecessor(
    wait_task, all_tasks, active_start, active_end
) -> tuple[Any | None, str]:
    by_stream = stream_tasks_by_id(all_tasks)
    ordered = sort_active_tasks(by_stream.get(wait_task.stream_id, []), active_start, active_end)
    idx = next((i for i, t in enumerate(ordered) if t.rowid == wait_task.rowid), None)
    if idx is None or idx == 0:
        return None, "no_pre_wait_task"
    prev = ordered[idx - 1]
    verdict, reason = evaluate_adjacent_pair(prev, wait_task)
    if verdict != "sortable":
        return None, f"pre_wait_overlap:{reason}"
    return prev, "pre_wait_direct_predecessor"


def verify_comm_entry(
    comm_entry,
    comm_op: dict,
    stream_id: int,
    tasks_by_cid: dict,
    active_start: int,
    active_end: int,
) -> tuple[bool, str]:
    first, status, _ = first_comm_entry_on_stream(
        comm_op, tasks_by_cid, stream_id, active_start, active_end
    )
    if first is None:
        return False, f"comm_entry_resolve_fail:{status}"
    if first.rowid != comm_entry.rowid:
        return False, "comm_entry_not_first_on_stream"
    return True, "ok"


def manifest_guided_winner(
    run_dir: Path,
    audit: dict[str, Any],
    selector_manifest: dict[str, Any],
    target_comm: str = TARGET_COMM,
) -> dict[str, Any] | None:
    """Treatment fallback when inject breaks reverse-candidate uniqueness (C8)."""
    wait_cs = int(audit.get("trigger_wait_preload_cs", 0) or 0)
    if wait_cs <= 0:
        return None
    ctx = build_run_context(run_dir)
    cand = next(
        (c for c in enumerate_generation_candidates(ctx) if c["wait_cs"] == wait_cs),
        None,
    )
    if cand is None:
        return None
    record_rec = cand["record_rec"]
    wait_rec = cand["wait_rec"]
    rec_ord = record_ord_on_thread(
        record_rec, ctx.rank0_records, ctx.active_start, ctx.active_end
    )
    wait_ord = wait_ord_on_thread(
        wait_rec, ctx.rank0_records, ctx.active_start, ctx.active_end
    )
    manifest_rec_ord = int(selector_manifest.get("record_active_success_ordinal", -1))
    manifest_wait_ord = int(selector_manifest.get("wait_active_success_ordinal", -1))
    if rec_ord != manifest_rec_ord or wait_ord != manifest_wait_ord:
        return None
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
            REV_API_RECORD,
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
            REV_API_WAIT,
            ctx.tasks_by_cid,
            ctx.string_ids,
            ctx.record_type,
            ctx.wait_type,
            used,
        )
        if wait_proj != "ok":
            wait_task = None
    if record_task is None or wait_task is None:
        return None
    comm_op = next((c for c in ctx.comm_ops if c.get("op_name") == target_comm), None)
    if comm_op is None:
        return None
    comm_entry, status, _ = first_comm_entry_on_stream(
        comm_op,
        ctx.tasks_by_cid,
        wait_task.stream_id,
        ctx.active_start,
        ctx.active_end,
    )
    if comm_entry is None:
        return None
    kernel_task, _, _ = nearest_kernel_predecessor(
        record_task,
        ctx.all_tasks_by_rowid,
        ctx.fifo_rev,
        ctx.overlap_pairs,
        ctx.string_ids,
    )
    winner = {
        "record_rec": record_rec,
        "wait_rec": wait_rec,
        "record_task": record_task,
        "wait_task": wait_task,
        "kernel_task": kernel_task,
        "comm_op": comm_op,
        "comm_entry": comm_entry,
        "record_active_success_ordinal": rec_ord,
        "wait_active_success_ordinal": wait_ord,
    }
    return {"ctx": ctx, "winner": winner}


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

    rev = extract_reverse_candidates(run_dir, run_id=run_id, target_comm=TARGET_COMM)
    manifest_guided = False
    if rev["status"] != "OK" or rev["winner"] is None:
        cond_upper = condition.upper()
        if selector_manifest and cond_upper != "D0":
            guided = manifest_guided_winner(run_dir, audit, selector_manifest, TARGET_COMM)
            if guided is None:
                return {
                    "run_id": run_id,
                    "condition": condition,
                    "status": rev["status"],
                    "candidate_count": rev["candidate_count"],
                }
            manifest_guided = True
            winner = guided["winner"]
            ctx = guided["ctx"]
        else:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": rev["status"],
                "candidate_count": rev["candidate_count"],
            }
    else:
        winner = rev["winner"]
        ctx = rev["ctx"]
    active_start, active_end = ctx.active_start, ctx.active_end
    all_tasks = ctx.all_tasks
    string_ids = ctx.string_ids
    tasks_by_cid = ctx.tasks_by_cid

    wait_task = winner["wait_task"]
    rec_task = winner["record_task"]
    comm = winner.get("comm_op")
    comm_entry = winner.get("comm_entry")
    pre_rec = winner["record_rec"]
    pre_wait = winner["wait_rec"]

    if wait_task is None or comm_entry is None or comm is None:
        return {"run_id": run_id, "condition": condition, "status": "IDENTITY_OR_PATH_INVALID"}

    norm_key_dict = build_pair_base_key(winner, TARGET_COMM)
    norm_key = normalized_key_to_json(norm_key_dict)

    if selector_manifest:
        sm_key = selector_manifest.get("normalized_key") or build_pair_base_key(
            {
                "wait_active_success_ordinal": selector_manifest.get(
                    "wait_active_success_ordinal", -1
                ),
            },
            TARGET_COMM,
        )
        if not manifest_guided and condition.upper() != "D0":
            if json.dumps(sm_key, sort_keys=True) != json.dumps(norm_key_dict, sort_keys=True):
                return {
                    "run_id": run_id,
                    "condition": condition,
                    "status": "TREATMENT_REVERSE_STRUCTURE_MISMATCH",
                }

    cond_upper = condition.upper()
    launch_count = int(audit.get("launch_count", audit.get("match_count", 0)) or 0)
    inject_site = str(audit.get("inject_site", "BEFORE_RECORD"))
    injected_kernel = None
    inj_rule = ""

    if cond_upper == "D0":
        if launch_count != 0:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "D0_UNEXPECTED_LAUNCH",
                "launch_count": launch_count,
            }
        succ, succ_rule = fifo_immediate_successor(wait_task, all_tasks, active_start, active_end)
        if succ is None or succ.rowid != comm_entry.rowid:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "D0_WAIT_TO_ENTRY_CHAIN_INVALID",
                "fifo_reason": succ_rule,
            }
        v, vr = evaluate_adjacent_pair(wait_task, comm_entry)
        if v != "sortable":
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "D0_WAIT_ENTRY_NOT_ADJACENT",
                "adjacency": vr,
            }
    elif cond_upper in TREATMENT_CONDITIONS:
        if inject_site != "AFTER_SUCCESSFUL_TARGET_WAIT":
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "INJECT_SITE_MISMATCH",
                "inject_site": inject_site,
            }
        if launch_count != 1:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "INJECT_IDENTITY_MISMATCH:launch_count",
                "launch_count": launch_count,
            }
        injected_kernel, inj_rule = find_inject_after_wait(
            wait_task, all_tasks, string_ids, active_start, active_end, ctx.db_path
        )
        if injected_kernel is None:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "INJECT_KERNEL_NOT_PROJECTED",
                "rule": inj_rule,
            }
        w_inj, wr = evaluate_adjacent_pair(wait_task, injected_kernel)
        i_q, ir = evaluate_adjacent_pair(injected_kernel, comm_entry)
        if w_inj != "sortable" or i_q != "sortable":
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "TREATMENT_ADJACENCY_FAIL",
                "wait_inject": wr,
                "inject_entry": ir,
            }
        ok_ce, ce_reason = verify_comm_entry(
            comm_entry, comm, wait_task.stream_id, tasks_by_cid, active_start, active_end
        )
        if not ok_ce:
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "COMM_ENTRY_IDENTITY_INVALID",
                "reason": ce_reason,
            }
        inject_cs = int(audit.get("trigger_wait_preload_cs", 0) or 0)
        if inject_cs <= 0 or inject_cs != int(pre_wait.call_sequence):
            return {
                "run_id": run_id,
                "condition": condition,
                "status": "INJECT_IDENTITY_MISMATCH:wait_cs",
                "inject_cs": inject_cs,
                "identity_cs": pre_wait.call_sequence,
            }

    ce_ok, _ = verify_comm_entry(
        comm_entry, comm, wait_task.stream_id, tasks_by_cid, active_start, active_end
    )
    if not ce_ok:
        return {
            "run_id": run_id,
            "condition": condition,
            "status": "STOP_COMM_ENTRY_IDENTITY_CHANGED",
        }

    pre_wait_p, _ = find_pre_wait_predecessor(wait_task, all_tasks, active_start, active_end)
    upstream_kernel = winner.get("kernel_task")
    anchor_end = upstream_kernel.end_ns if upstream_kernel else rec_task.end_ns

    post_wait_to_entry = comm_entry.start_ns - wait_task.end_ns
    realized_work = (
        int(injected_kernel.end_ns - injected_kernel.start_ns) if injected_kernel else 0
    )

    identity = {
        "run_id": run_id,
        "condition": condition,
        "status": "OK",
        "pair_base_key": norm_key,
        "intervention_identity_key": build_intervention_identity_key(
            cond_upper in TREATMENT_CONDITIONS
        ),
        "preload_record_cs": pre_rec.call_sequence,
        "preload_wait_cs": pre_wait.call_sequence,
        "wait_task_rowid": wait_task.rowid,
        "comm_entry_rowid": comm_entry.rowid,
        "inject_task_rowid": injected_kernel.rowid if injected_kernel else None,
        "record_task_rowid": rec_task.rowid,
        "wait_stream_id": wait_task.stream_id,
        "record_stream_id": rec_task.stream_id,
        "inject_site": inject_site,
        "launch_count": launch_count,
        "requested_iters": int(audit.get("requested_iters", 0) or 0),
        "trigger_wait_preload_cs": int(audit.get("trigger_wait_preload_cs", 0) or 0),
        "real_wait_rc": int(audit.get("real_wait_rc", 0) or 0),
        "generation_closure_status": "VALID",
    }

    nodes = [
        node_with_offsets(
            run_id, condition, "wait_task", wait_task.start_ns, wait_task.end_ns, anchor_end
        ),
        node_with_offsets(
            run_id, condition, "comm_entry", comm_entry.start_ns, comm_entry.end_ns, anchor_end
        ),
        node_with_offsets(
            run_id, condition, "record_task", rec_task.start_ns, rec_task.end_ns, anchor_end
        ),
    ]
    if injected_kernel is not None:
        nodes.insert(
            1,
            node_with_offsets(
                run_id,
                condition,
                "injected_kernel",
                injected_kernel.start_ns,
                injected_kernel.end_ns,
                anchor_end,
            ),
        )
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

    return {
        "run_id": run_id,
        "condition": condition,
        "status": "OK",
        "identity": identity,
        "nodes": nodes,
        "post_wait_to_entry_ns": post_wait_to_entry,
        "realized_work_ns": realized_work,
        "pre_wait_p_rowid": pre_wait_p.rowid if pre_wait_p else None,
        "normalized_structure_key": norm_key,
        "audit": audit,
    }


def dose_reference_ns(iters: int) -> int:
    if iters >= 5000:
        return V45_DLARGE_REALIZED_REF_NS
    return V45_DSMALL_REALIZED_REF_NS


def evaluate_local_causal(
    observed_delay: int, realized_work: int
) -> tuple[bool, str | None]:
    if observed_delay <= max(50_000, int(0.50 * realized_work)):
        return False, f"observed_delay_low:{observed_delay}"
    tol = max(100_000, int(0.20 * realized_work))
    if abs(observed_delay - realized_work) > tol:
        return False, f"delay_work_mismatch:{observed_delay} vs {realized_work}"
    return True, None


def evaluate_dose_gate(realized_work: int, requested_iters: int) -> tuple[bool, str | None]:
    if realized_work <= 0:
        return False, "REALIZED_WORK_INVALID"
    ref = dose_reference_ns(requested_iters)
    tol = max(100_000, int(0.20 * ref))
    if abs(realized_work - ref) > tol:
        return False, f"dose_ref_miss:{realized_work} vs ref {ref}"
    return True, None


def paired_effects_v4_6(d0: dict[str, Any], dtreat: dict[str, Any], block: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "block": block,
        "d0_run_id": d0["run_id"],
        "dtreat_run_id": dtreat["run_id"],
        "extraction_status": "OK",
        "legacy_metric_tier": "legacy_non_acceptance_metric",
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
                "pair_base_key": d0_key,
            }
        )
        return base

    base["structure_gate_pass"] = True
    base["structure_reason"] = ""
    base["pair_base_key"] = d0_key
    base["intervention_identity_key"] = json.dumps(
        dtreat.get("identity", {}).get("intervention_identity_key", {}), sort_keys=True
    )

    realized = int(dtreat.get("realized_work_ns", 0) or 0)
    req_iters = int(dtreat.get("identity", {}).get("requested_iters", 0) or 0)
    dose_ok, dose_reason = evaluate_dose_gate(realized, req_iters)
    base["dose_gate_pass"] = dose_ok
    base["dose_reason"] = dose_reason or ""
    base["realized_work_ns"] = realized
    base["expected_entry_delay_ns"] = realized

    if not dose_ok:
        base["local_causal_gate_pass"] = None
        base["control_gate_pass"] = None
        return base

    post_d0 = int(d0.get("post_wait_to_entry_ns", 0))
    post_t = int(dtreat.get("post_wait_to_entry_ns", 0))
    observed = post_t - post_d0
    base["post_wait_to_entry_d0_ns"] = post_d0
    base["post_wait_to_entry_treat_ns"] = post_t
    base["observed_entry_delay_ns"] = observed

    causal_ok, causal_reason = evaluate_local_causal(observed, realized)
    base["local_causal_gate_pass"] = causal_ok
    base["local_causal_reason"] = causal_reason or ""

    def rel_end(nodes: list[dict], name: str) -> int:
        return int(next(n for n in nodes if n["node"] == name)["end_offset_from_upstream_kernel_end_ns"])

    d0n, dtn = d0["nodes"], dtreat["nodes"]
    base["record_shift_ns"] = rel_end(dtn, "record_task") - rel_end(d0n, "record_task")
    base["wait_completion_shift_ns"] = rel_end(dtn, "wait_task") - rel_end(d0n, "wait_task")
    base["comm_entry_shift_ns"] = (
        int(next(n for n in dtn if n["node"] == "comm_entry")["start_offset_from_upstream_kernel_end_ns"])
        - int(next(n for n in d0n if n["node"] == "comm_entry")["start_offset_from_upstream_kernel_end_ns"])
    )

  # control: pre-wait anchor
    p_d0 = d0.get("pre_wait_p_rowid")
    p_t = dtreat.get("pre_wait_p_rowid")
    if p_d0 is None or p_t is None or p_d0 != p_t:
        base["pre_wait_anchored_shift_ns"] = "CONTROL_UNAVAILABLE"
        base["control_gate_pass"] = True
        base["control_reason"] = "pre_wait_p_unavailable"
    else:
        # dose should not move pre-wait nodes materially
        ctrl_tol = max(100_000, int(0.25 * realized))
        rec_shift = abs(base["record_shift_ns"])
        wait_shift = abs(base["wait_completion_shift_ns"])
        if rec_shift > ctrl_tol or wait_shift > ctrl_tol:
            base["control_gate_pass"] = False
            base["control_reason"] = f"pre_wait_confound:rec={rec_shift} wait={wait_shift}"
        else:
            base["control_gate_pass"] = True
            base["control_reason"] = ""
        base["pre_wait_anchored_shift_ns"] = base["comm_entry_shift_ns"]

    return base


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
                "preload_wait_cs": ex["identity"].get("preload_wait_cs"),
                "launch_count": ex["identity"].get("launch_count"),
                "post_wait_to_entry_ns": ex.get("post_wait_to_entry_ns"),
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
        paired.append(paired_effects_v4_6(d0, dt, block))

    write_csv(out / "intervention_identity.csv", identity_rows, list(identity_rows[0].keys()) if identity_rows else ["run_id"])
    write_csv(out / "post_wait_injection_audit.csv", injection_rows, list(injection_rows[0].keys()) if injection_rows else ["run_id"])
    write_csv(out / "kernel_realization.csv", kernel_rows, ["run_id", "condition", "inject_task_rowid", "profiler_duration_ns", "requested_iters"])
    write_csv(out / "node_wallclock.csv", node_rows, list(node_rows[0].keys()) if node_rows else ["run_id"])
    write_csv(out / "paired_effects_v4_6.csv", paired, PAIRED_FIELDS)
    write_csv(out / "run_ledger.csv", ledger_rows, ["run_id", "condition", "status", "run_dir"])

    summary = {
        "identity_ok": len(identity_rows),
        "paired": paired,
        "schema": SCHEMA,
    }
    (out / "v4_6_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()

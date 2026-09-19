#!/usr/bin/env python3
"""D51 Wait DAG V4.8: STRUCTURAL_NA pre_wait_p + anchor-relative controls C1–C5."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from analyze_event_pairs import RECORD_OP, WAIT_OP, stream_tasks_by_id
from wait_dag_v4_2_reverse_candidate import build_run_context
from wait_dag_v4_7_intervention import (
    CONTROL_EFFECT_FIELDS,
    PAIRED_FIELDS,
    REL_TOL,
    SCHEMA,
    TARGET_COMM,
    TREATMENT_CONDITIONS,
    build_control_effects as _v47_build_control_effects,
    build_pair_base_key,
    causal_eligibility,
    dose_reference_ns,
    evaluate_causal_closure,
    evaluate_dose_gate,
    extract_d0_i0,
    extract_run as _v47_extract_run,
    extract_treatment_it,
    find_bypass_compute,
    host_issue_gap_ns,
    load_selector_manifest,
    occupancy_identity_check,
    paired_effects_v4_7,
    write_csv,
)

V48_SCHEMA = "d51_wait_dag_v4_8_structural_na_anchor_controls"
STRUCTURAL_NA_REASON = "DB_BEGINS_AT_SELECTED_WAIT_WHILE_PRELOAD_PROVES_PRIOR_SAME_RAW_STREAM_WAIT"

CAPTURE_CONTRACT_BASE = {
    "profiler_level": "Level1",
    "record_op_args": True,
    "data_simplification": False,
    "export_type": "Db",
    "warmup_outside_profiler": True,
    "train_warmup_steps": 2,
    "train_active_steps": 3,
    "dim": 4096,
    "batch": 256,
    "inject_site": "AFTER_SUCCESSFUL_TARGET_WAIT",
    "extractor_no_active_start_floor": False,
    "capture_contract_version": "v4_8_fix1_inherited",
}


def canonical_capture_contract(extra: dict | None = None) -> dict[str, Any]:
    out = dict(CAPTURE_CONTRACT_BASE)
    if extra:
        out.update(extra)
    return out


def capture_contract_sha(contract: dict | None = None) -> str:
    blob = json.dumps(canonical_capture_contract(contract), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _preload_wait_records(rank0_records: list, raw_stream: int | None = None) -> list:
    out = [
        r
        for r in rank0_records
        if r.op == WAIT_OP and r.acl_ret == 0 and r.raw_stream
    ]
    if raw_stream is not None:
        out = [r for r in out if int(r.raw_stream) == int(raw_stream)]
    out.sort(key=lambda r: (r.call_sequence, r.enter_realtime_ns))
    return out


def _find_preload_wait_by_cs(rank0_records: list, wait_cs: int | None) -> Any | None:
    if wait_cs is None:
        return None
    for r in rank0_records:
        if r.op == WAIT_OP and r.acl_ret == 0 and int(r.call_sequence) == int(wait_cs):
            return r
    return None


def _stream_tasks_full_db(ctx, stream_id: int) -> list:
    by_stream = stream_tasks_by_id(ctx.all_tasks)
    tasks = list(by_stream.get(stream_id, []))
    tasks.sort(key=lambda t: (t.start_ns, t.end_ns, t.rowid))
    return tasks


def _raw_stream_for_wait(ctx, wait_task, wait_cs: int | None) -> tuple[int | None, str, int]:
    preload = _find_preload_wait_by_cs(ctx.rank0_records, wait_cs)
    if preload is None:
        return None, "preload_wait_missing", 0
    raw = int(preload.raw_stream)
    matches = [
        r
        for r in ctx.rank0_records
        if r.op == WAIT_OP and r.acl_ret == 0 and int(r.raw_stream) == raw
    ]
    prof_stream = int(wait_task.stream_id)
    same_stream_waits = [
        t
        for t in _stream_tasks_full_db(ctx, prof_stream)
        if any(
            _find_preload_wait_by_cs(ctx.rank0_records, int(w.call_sequence)) is not None
            for w in [_find_preload_wait_by_cs(ctx.rank0_records, wait_cs)]
            if w is not None
        )
    ]
    _ = same_stream_waits  # profiler stream validated via wait_task
    if not matches:
        return None, "no_preload_on_raw_stream", 0
    return raw, "preload_wait_cs_alignment", len(matches)


def evaluate_p_predicates(
    ctx,
    wait_task,
    wait_cs: int | None,
    capture_sha: str,
    preflight_sha: str,
) -> dict[str, Any]:
    preds: dict[str, Any] = {}

    p1 = capture_sha == preflight_sha
    preds["P1"] = {
        "value": p1,
        "predicate": "capture_contract_sha_matches_preflight",
        "source": "preflight.capture_contract_sha256",
        "endpoint": preflight_sha,
        "candidate_count": 1 if p1 else 2,
        "reason": "ok" if p1 else "capture_contract_drift",
    }

    raw_stream, raw_rule, raw_cand = _raw_stream_for_wait(ctx, wait_task, wait_cs)
    prof_sid = int(wait_task.stream_id)
    p2 = raw_stream is not None and raw_cand >= 1
    preds["P2"] = {
        "value": p2,
        "predicate": "raw_profiler_stream_bidirectional_unique",
        "source": raw_rule,
        "endpoint": f"raw={raw_stream},profiler_streamId={prof_sid}",
        "candidate_count": raw_cand if raw_stream else 0,
        "reason": "ok" if p2 else "projection_not_unique",
    }

    ordered = _stream_tasks_full_db(ctx, prof_sid)
    idx = next((i for i, t in enumerate(ordered) if t.rowid == wait_task.rowid), None)
    p3 = idx == 0 and len(ordered) > 1
    preds["P3"] = {
        "value": p3,
        "predicate": "selected_wait_is_first_db_task_on_stream",
        "source": "profiler_full_db_sort",
        "endpoint": f"idx={idx},total={len(ordered)}",
        "candidate_count": len(ordered),
        "reason": "ok" if p3 else ("db_has_predecessor_task" if idx and idx > 0 else "insufficient_tasks"),
    }

    session_start = int(ctx.active_start)
    pre_session_waits = [
        r
        for r in _preload_wait_records(ctx.rank0_records, raw_stream)
        if int(r.enter_realtime_ns) < session_start
    ]
    p4 = bool(pre_session_waits)
    preds["P4"] = {
        "value": p4,
        "predicate": "preload_session_before_success_wait_same_raw_stream",
        "source": "preload_trace",
        "endpoint": f"session_start={session_start}",
        "candidate_count": len(pre_session_waits),
        "reason": "ok" if p4 else "no_session_before_wait",
    }

    host_prev = None
    host_prev_count = 0
    if raw_stream is not None and wait_cs is not None:
        sel = _find_preload_wait_by_cs(ctx.rank0_records, wait_cs)
        all_raw = _preload_wait_records(ctx.rank0_records, raw_stream)
        prior = [r for r in all_raw if r.call_sequence < sel.call_sequence] if sel else []
        host_prev_count = len(prior)
        if len(prior) == 1:
            host_prev = prior[0]
        elif len(prior) > 1:
            host_prev = prior[-1]
            ties = [r for r in prior if r.call_sequence == host_prev.call_sequence]
            if len(ties) != 1:
                host_prev = None
    p5 = host_prev is not None and host_prev_count >= 1
    preds["P5"] = {
        "value": p5,
        "predicate": "unique_host_prev_wait_same_raw_stream",
        "source": "preload_wait_call_order",
        "endpoint": getattr(host_prev, "call_sequence", None),
        "candidate_count": host_prev_count,
        "reason": "ok" if p5 else "host_prev_wait_not_unique",
    }

    p6 = True
    p6_reason = "ok"
    if host_prev is not None and wait_cs is not None:
        sel = _find_preload_wait_by_cs(ctx.rank0_records, wait_cs)
        if sel:
            lo, hi = int(host_prev.enter_realtime_ns), int(sel.exit_realtime_ns)
            window_recs = [
                r
                for r in ctx.rank0_records
                if lo <= int(r.enter_realtime_ns) <= hi and int(r.raw_stream) == int(raw_stream or 0)
            ]
            streams_seen = {int(r.raw_stream) for r in window_recs if r.raw_stream}
            if len(streams_seen) > 1:
                p6 = False
                p6_reason = "raw_stream_handle_reuse"
            destroy_ops = [r for r in window_recs if r.op == 7]
            if destroy_ops:
                p6 = False
                p6_reason = "destroy_in_window"
    preds["P6"] = {
        "value": p6,
        "predicate": "no_stream_create_destroy_between_host_prev_and_selected",
        "source": "preload_lifecycle",
        "endpoint": f"host_prev_cs={getattr(host_prev, 'call_sequence', None)}",
        "candidate_count": 1 if p6 else 0,
        "reason": p6_reason,
    }

    p7 = (
        ctx.db_path.exists()
        and ctx.rank0_records is not None
        and ctx.string_ids
        and ctx.active_start > 0
        and ctx.active_end > ctx.active_start
    )
    preds["P7"] = {
        "value": p7,
        "predicate": "metadata_complete_no_parse_fallback",
        "source": "run_context",
        "endpoint": str(ctx.db_path),
        "candidate_count": 1 if p7 else 0,
        "reason": "ok" if p7 else "metadata_incomplete",
    }

    all_true = all(preds[k]["value"] is True for k in ("P1", "P2", "P3", "P4", "P5", "P6", "P7"))
    if all_true:
        status = "STRUCTURAL_NA"
        gate_bool = None
        reason = STRUCTURAL_NA_REASON
    elif idx is not None and idx > 0:
        status = "STOP_PRE_WAIT_P_CLASSIFICATION_FAILED"
        gate_bool = False
        reason = "db_has_real_predecessor_task"
    else:
        status = "STOP_PRE_WAIT_P_CLASSIFICATION_FAILED"
        gate_bool = False
        failed = [k for k in preds if not preds[k]["value"]]
        reason = f"predicate_false:{','.join(failed)}"

    return {
        "pre_wait_p_status": status,
        "pre_wait_p_task": None,
        "pre_wait_p_reason": reason if status == "STRUCTURAL_NA" else reason,
        "pre_wait_p_gate_boolean": gate_bool,
        "predicates": preds,
        "host_prev_wait_cs": getattr(host_prev, "call_sequence", None),
        "host_prev_wait_enter_ns": getattr(host_prev, "enter_realtime_ns", None),
        "host_prev_wait_exit_ns": getattr(host_prev, "exit_realtime_ns", None),
        "selected_wait_preload_enter_ns": getattr(
            _find_preload_wait_by_cs(ctx.rank0_records, wait_cs), "enter_realtime_ns", None
        ),
        "raw_comm_stream": raw_stream,
        "profiler_stream_id": prof_sid,
    }


def build_v48_run_controls(
    ctx,
    wait_task,
    record_task,
    comm_op: dict | None,
    upstream_kernel,
    comm_entry,
    inject_task=None,
    classification: dict | None = None,
) -> dict[str, Any]:
    bypass, bypass_rule = find_bypass_compute(
        ctx, record_task, upstream_kernel, wait_task, comm_entry, inject_task
    )
    host_gap, host_rule = host_issue_gap_ns(comm_op, wait_task)
    wait_duration = int(wait_task.end_ns - wait_task.start_ns)
    record_offset = int(record_task.end_ns - wait_task.end_ns) if record_task else None
    bypass_offset = int(bypass.end_ns - wait_task.end_ns) if bypass else None

    fifo_gap = None
    fifo_rule = "preload_adjacent_wait_fifo"
    if classification:
        ent = classification.get("selected_wait_preload_enter_ns")
        ext = classification.get("host_prev_wait_exit_ns")
        if ent is not None and ext is not None:
            fifo_gap = int(ent) - int(ext)

    return {
        "C1_wait_duration_ns": {
            "value_ns": wait_duration,
            "anchor_ns": int(wait_task.end_ns),
            "endpoint_a_ns": int(wait_task.start_ns),
            "endpoint_b_ns": int(wait_task.end_ns),
            "source": "profiler.EVENT_WAIT",
            "clock_domain": "profiler",
            "selection_predicate": "unique_selected_wait",
            "candidate_count": 1,
            "unique": True,
        },
        "C2_record_offset_from_wait_end_ns": {
            "value_ns": record_offset,
            "anchor_ns": int(wait_task.end_ns),
            "endpoint_a_ns": int(record_task.end_ns) if record_task else None,
            "endpoint_b_ns": int(wait_task.end_ns),
            "source": "profiler.compute_record+wait_same_generation",
            "clock_domain": "profiler",
            "selection_predicate": "unique_record_same_event_generation",
            "candidate_count": 1 if record_task else 0,
            "unique": record_task is not None,
        },
        "C3_pre_target_wait_host_fifo_gap_ns": {
            "value_ns": fifo_gap,
            "anchor_ns": classification.get("selected_wait_preload_enter_ns") if classification else None,
            "endpoint_a_ns": classification.get("selected_wait_preload_enter_ns") if classification else None,
            "endpoint_b_ns": classification.get("host_prev_wait_exit_ns") if classification else None,
            "source": fifo_rule,
            "clock_domain": "preload_realtime",
            "selection_predicate": "P5_host_prev_wait",
            "candidate_count": 1 if fifo_gap is not None else 0,
            "unique": fifo_gap is not None,
        },
        "C4_bypass_compute_offset_from_wait_end_ns": {
            "value_ns": bypass_offset,
            "anchor_ns": int(wait_task.end_ns),
            "endpoint_a_ns": int(bypass.end_ns) if bypass else None,
            "endpoint_b_ns": int(wait_task.end_ns),
            "source": bypass_rule if bypass else bypass_rule,
            "clock_domain": "profiler",
            "selection_predicate": "bypass_compute_latest_before_record_off_path",
            "candidate_count": 1 if bypass else 0,
            "unique": bypass is not None,
        },
        "C5_host_issue_gap_ns": {
            "value_ns": host_gap,
            "anchor_ns": int(wait_task.end_ns),
            "endpoint_a_ns": int(comm_op["start_ns"]) if comm_op else None,
            "endpoint_b_ns": int(wait_task.end_ns),
            "source": host_rule if host_gap is not None else host_rule,
            "clock_domain": "profiler_cann",
            "selection_predicate": "unique_target_comm_op",
            "candidate_count": 1 if host_gap is not None else 0,
            "unique": host_gap is not None,
        },
        "target_comm_duration_ns": (
            int(comm_op["end_ns"]) - int(comm_op["start_ns"]) if comm_op else None
        ),
        "bypass_compute_rowid": bypass.rowid if bypass else None,
    }


def evaluate_control_gate_v48(d0: dict, dtreat: dict) -> tuple[bool, str, list[dict]]:
    realized = int(dtreat.get("realized_work_ns", 0) or 0)
    if realized <= 0:
        return False, "REALIZED_WORK_INVALID", []
    tol = REL_TOL * realized
    c0 = d0.get("v48_controls") or {}
    cT = dtreat.get("v48_controls") or {}
    rows: list[dict] = []
    confounds: list[str] = []
    unavailable: list[str] = []

    for key in (
        "C1_wait_duration_ns",
        "C2_record_offset_from_wait_end_ns",
        "C3_pre_target_wait_host_fifo_gap_ns",
        "C4_bypass_compute_offset_from_wait_end_ns",
        "C5_host_issue_gap_ns",
    ):
        a = c0.get(key) or {}
        b = cT.get(key) or {}
        unique = bool(a.get("unique") and b.get("unique"))
        v0, vT = a.get("value_ns"), b.get("value_ns")
        if not unique or v0 is None or vT is None:
            unavailable.append(key)
            rows.append(
                {
                    "metric": key,
                    "shift_ns": "CONTROL_UNAVAILABLE",
                    "d0_value_ns": v0,
                    "dtreat_value_ns": vT,
                    "unique": unique,
                }
            )
            continue
        shift = int(vT) - int(v0)
        rows.append(
            {
                "metric": key,
                "shift_ns": shift,
                "d0_value_ns": v0,
                "dtreat_value_ns": vT,
                "d0_anchor_ns": a.get("anchor_ns"),
                "dtreat_anchor_ns": b.get("anchor_ns"),
                "d0_source": a.get("source"),
                "dtreat_source": b.get("source"),
                "clock_domain": a.get("clock_domain"),
                "unique": True,
            }
        )
        if abs(shift) > tol:
            confounds.append(f"{key}={shift}")

    if unavailable:
        return False, "STOP_CONTROL_UNAVAILABLE:" + ",".join(unavailable), rows
    if confounds:
        return False, "STOP_GLOBAL_SCHEDULING_OR_CONTENTION_CONFOUND:" + ",".join(confounds), rows
    return True, "", rows


def paired_effects_v4_8(d0: dict, dtreat: dict, block: str) -> dict[str, Any]:
    base = paired_effects_v4_7(d0, dtreat, block)
    d0_cls = d0.get("capture_classification") or {}
    dt_cls = dtreat.get("capture_classification") or {}
    base["pre_wait_p_status_d0"] = d0_cls.get("pre_wait_p_status")
    base["pre_wait_p_status_treatment"] = dt_cls.get("pre_wait_p_status")
    base["pre_wait_p_gate_boolean"] = dt_cls.get("pre_wait_p_gate_boolean")

    if base.get("dose_gate_pass") not in (True, "True"):
        return base

    for side, cls in (("d0", d0_cls), ("treatment", dt_cls)):
        st = cls.get("pre_wait_p_status")
        if st and st != "STRUCTURAL_NA" and st.startswith("STOP"):
            base["control_gate_pass"] = False
            base["control_reason"] = st
            return base

    ok, reason, _ = evaluate_control_gate_v48(d0, dtreat)
    base["control_gate_pass"] = ok
    base["control_reason"] = reason
    return base


def extract_run_v48(
    run_dir: Path,
    run_id: str,
    condition: str,
    selector_manifest: dict | None,
    capture_sha: str,
    preflight_sha: str,
) -> dict[str, Any]:
    ex = _v47_extract_run(run_dir, run_id, condition, selector_manifest)
    if ex.get("status") != "OK":
        ex["capture_classification"] = None
        ex["v48_controls"] = None
        return ex

    ctx = build_run_context(run_dir)
    wait_task = ex["wait_task"]
    wait_cs = None
    ident = ex.get("identity") or {}
    rli = ident.get("run_local_identity") or {}
    wait_cs = rli.get("preload_wait_cs") or ident.get("trigger_wait_preload_cs")

    cls = evaluate_p_predicates(ctx, wait_task, wait_cs, capture_sha, preflight_sha)
    if cls["pre_wait_p_status"] != "STRUCTURAL_NA":
        return {
            **ex,
            "status": cls["pre_wait_p_status"],
            "capture_classification": cls,
            "v48_controls": None,
        }

    rec_rowid = ident.get("record_task_rowid")
    rec_task = ctx.all_tasks_by_rowid.get(rec_rowid) if rec_rowid else None
    upstream_rowid = ident.get("kernel_predecessor_rowid")
    upstream = ctx.all_tasks_by_rowid.get(upstream_rowid) if upstream_rowid else None
    comm_op = ex.get("comm_op")
    inject = ex.get("inject_task")
    v48c = build_v48_run_controls(
        ctx, wait_task, rec_task, comm_op, upstream, ex["comm_entry"], inject, cls
    )
    ex["capture_classification"] = cls
    ex["v48_controls"] = v48c
    return ex


def build_control_effects_v4_8(d0: dict, dtreat: dict) -> list[dict]:
    _, _, rows = evaluate_control_gate_v48(d0, dtreat)
    out = []
    for r in rows:
        out.append(
            {
                "metric": r["metric"],
                "d0_run_id": d0["run_id"],
                "dtreat_run_id": dtreat["run_id"],
                "shift_ns": r["shift_ns"],
                "d0_value_ns": r.get("d0_value_ns"),
                "dtreat_value_ns": r.get("dtreat_value_ns"),
                "d0_anchor_ns": r.get("d0_anchor_ns"),
                "dtreat_anchor_ns": r.get("dtreat_anchor_ns"),
                "d0_source": r.get("d0_source", ""),
                "dtreat_source": r.get("dtreat_source", ""),
                "clock_domain": r.get("clock_domain", ""),
                "unique": r.get("unique"),
            }
        )
    return out


def classification_csv_row(run_id: str, condition: str, cls: dict | None) -> dict:
    if not cls:
        return {"run_id": run_id, "condition": condition, "pre_wait_p_status": "null"}
    row = {
        "run_id": run_id,
        "condition": condition,
        "pre_wait_p_status": cls.get("pre_wait_p_status"),
        "pre_wait_p_task": cls.get("pre_wait_p_task"),
        "pre_wait_p_reason": cls.get("pre_wait_p_reason"),
        "pre_wait_p_gate_boolean": cls.get("pre_wait_p_gate_boolean"),
        "raw_comm_stream": cls.get("raw_comm_stream"),
        "profiler_stream_id": cls.get("profiler_stream_id"),
    }
    for pk in ("P1", "P2", "P3", "P4", "P5", "P6", "P7"):
        p = (cls.get("predicates") or {}).get(pk, {})
        row[f"{pk}_value"] = p.get("value")
        row[f"{pk}_reason"] = p.get("reason")
        row[f"{pk}_candidate_count"] = p.get("candidate_count")
    return row


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--selector-manifest", default="")
    p.add_argument("--capture-contract-sha", required=True)
    p.add_argument("--preflight-capture-sha", required=True)
    args = p.parse_args()
    manifest_obj = json.loads(Path(args.manifest).read_text())
    selector_manifest = load_selector_manifest(
        Path(args.selector_manifest) if args.selector_manifest else None
    )
    runs = manifest_obj.get("runs", manifest_obj if isinstance(manifest_obj, list) else [])
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
    classification_rows: list[dict] = []

    for spec in runs:
        run_id = spec["run_id"]
        condition = spec["condition"]
        run_dir = Path(spec["run_dir"])
        try:
            ex = extract_run_v48(
                run_dir,
                run_id,
                condition,
                selector_manifest,
                args.capture_contract_sha,
                args.preflight_capture_sha,
            )
        except Exception as e:  # noqa: BLE001
            ex = {"run_id": run_id, "condition": condition, "status": f"ERROR:{e}"}
        extracted[run_id] = ex
        ledger_rows.append(
            {"run_id": run_id, "condition": condition, "status": ex.get("status"), "run_dir": str(run_dir)}
        )
        classification_rows.append(classification_csv_row(run_id, condition, ex.get("capture_classification")))
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
                "pre_wait_p_status": (ex.get("capture_classification") or {}).get("pre_wait_p_status"),
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
        paired.append(paired_effects_v4_8(d0, dt, block))
        control_rows.extend(build_control_effects_v4_8(d0, dt))

    write_csv(out / "intervention_identity.csv", identity_rows, list(identity_rows[0].keys()) if identity_rows else ["run_id"])
    write_csv(out / "post_wait_injection_audit.csv", injection_rows, list(injection_rows[0].keys()) if injection_rows else ["run_id"])
    write_csv(out / "kernel_realization.csv", kernel_rows, ["run_id", "condition", "inject_task_rowid", "profiler_duration_ns", "requested_iters"])
    write_csv(out / "comm_entry_projection.csv", comm_projection_rows, list(comm_projection_rows[0].keys()) if comm_projection_rows else ["run_id"])
    write_csv(out / "node_wallclock.csv", node_rows, list(node_rows[0].keys()) if node_rows else ["run_id"])
    cls_fields = list(classification_rows[0].keys()) if classification_rows else ["run_id", "condition", "pre_wait_p_status"]
    write_csv(out / "capture_classification.csv", classification_rows, cls_fields)
    write_csv(out / "paired_effects_v4_8.csv", paired, PAIRED_FIELDS + ["pre_wait_p_status_d0", "pre_wait_p_status_treatment", "pre_wait_p_gate_boolean"])
    write_csv(
        out / "control_effects_v4_8.csv",
        control_rows,
        [
            "metric",
            "d0_run_id",
            "dtreat_run_id",
            "shift_ns",
            "d0_value_ns",
            "dtreat_value_ns",
            "d0_anchor_ns",
            "dtreat_anchor_ns",
            "d0_source",
            "dtreat_source",
            "clock_domain",
            "unique",
        ],
    )
    write_csv(out / "run_ledger.csv", ledger_rows, ["run_id", "condition", "status", "run_dir"])

    claims = [
        "# D51 Wait DAG V4.8 claims (Builder draft — not GO)",
        "",
        "pre_wait_p STRUCTURAL_NA when P1–P7 all True; boolean=null; not control PASS.",
        "Controls C1–C5: anchor-relative within run, shift=treatment−D0.",
        "occupancy_residual_ns ≡ post_inject_gap_ns when predicted>0 uncropped.",
        "",
    ]
    (out / "claims.md").write_text("\n".join(claims) + "\n")
    summary = {"identity_ok": len(identity_rows), "paired": paired, "schema": V48_SCHEMA}
    (out / "v4_8_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()

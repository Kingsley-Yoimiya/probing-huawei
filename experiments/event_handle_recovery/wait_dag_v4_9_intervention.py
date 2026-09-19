#!/usr/bin/env python3
"""D51 Wait DAG V4.9: path-external C3 DID + C5 diagnostic-only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from analyze_event_pairs import WAIT_OP, iter_trace_bins, load_trace_bin
from wait_dag_v4_7_intervention import (
    PAIRED_FIELDS,
    REL_TOL,
    TREATMENT_CONDITIONS,
    build_pair_base_key,
    causal_eligibility,
    dose_reference_ns,
    evaluate_causal_closure,
    evaluate_dose_gate,
    load_selector_manifest,
    occupancy_identity_check,
    paired_effects_v4_7,
    write_csv,
)
from wait_dag_v4_8_intervention import (
    STRUCTURAL_NA_REASON,
    build_v48_run_controls,
    capture_contract_sha,
    canonical_capture_contract,
    classification_csv_row,
    evaluate_p_predicates,
    extract_run_v48,
)

V49_SCHEMA = "d51_wait_dag_v4_9_path_external_c3_c5_diagnostic"
C3_K = 8
C3_REQUIRED_WAITS = 9
C3_TARGET_RANK = 0
C3_SENTINEL_RANKS = list(range(1, 16))
C3_CONTRACT = {
    "target_logical_rank": C3_TARGET_RANK,
    "sentinel_ranks": C3_SENTINEL_RANKS,
    "K": C3_K,
    "required_success_waits": C3_REQUIRED_WAITS,
    "gap_formula": "W[k+1].enter - W[k].exit, k=-9..-2",
    "median_formula": "median(gaps)",
    "did_formula": "d[T,r]=m[T,r]-m[D0,r]; C3=d[T,r*]-median(S); noise=max_S|d-median|",
    "stationarity_gate": "abs(C3_did_ns) <= C3_noise_envelope_ns",
    "sensitivity_gate": "C3_noise_envelope_ns / predicted_unmasked_occupancy_ns <= 0.20",
}
C5_DIAG = {
    "C5_status": "PATH_DIAGNOSTIC_ONLY",
    "C5_gate_boolean": None,
    "C5_in_control_denominator": False,
}


def c3_contract_sha() -> str:
    blob = json.dumps(C3_CONTRACT, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def load_all_rank_traces(trace_dir: Path) -> dict[int, list]:
    from collections import defaultdict

    candidates: dict[int, list[tuple[dict, list]]] = defaultdict(list)
    for path in iter_trace_bins(trace_dir):
        meta, recs = load_trace_bin(path)
        rank = int(meta["rank"])
        if rank < 0:
            continue
        candidates[rank].append((meta, recs))
    by_rank: dict[int, list] = {}
    for rank, items in candidates.items():
        meta, recs = max(items, key=lambda x: (len(x[1]), -int(x[0]["pid"])))
        if not recs:
            continue
        by_rank[rank] = recs
    return by_rank


def _successful_waits_on_stream(
    records: list,
    raw_stream: int,
    *,
    before_ns: int | None = None,
    before_session: int | None = None,
) -> list:
    out = [
        r
        for r in records
        if r.op == WAIT_OP and r.acl_ret == 0 and int(r.raw_stream) == int(raw_stream)
    ]
    if before_session is not None:
        out = [r for r in out if int(r.enter_realtime_ns) < int(before_session)]
    if before_ns is not None:
        out = [r for r in out if int(r.enter_realtime_ns) < int(before_ns)]
    out.sort(key=lambda r: (r.call_sequence, r.enter_realtime_ns))
    return out


def identify_comm_raw_stream(
    records: list, session_start: int, reference_count: int | None = None
) -> tuple[int | None, str]:
    streams: dict[int, int] = {}
    for r in records:
        if r.op != WAIT_OP or r.acl_ret != 0 or not r.raw_stream:
            continue
        if int(r.enter_realtime_ns) >= session_start:
            continue
        streams[int(r.raw_stream)] = streams.get(int(r.raw_stream), 0) + 1
    if not streams:
        return None, "no_pre_session_waits"
    if reference_count is not None:
        candidates = [s for s, c in streams.items() if c >= reference_count]
        if candidates:
            return max(candidates, key=lambda s: streams[s]), "reference_count_match"
    return max(streams, key=lambda s: streams[s]), "max_pre_session_waits"


def compute_rank_preintervention_fifo(
    records: list,
    raw_stream: int,
    session_start: int,
    selected_enter_ns: int,
) -> tuple[dict[str, Any] | None, str]:
    waits = _successful_waits_on_stream(
        records,
        raw_stream,
        before_ns=selected_enter_ns,
        before_session=session_start,
    )
    if len(waits) < C3_REQUIRED_WAITS:
        return None, f"insufficient_waits:{len(waits)}<{C3_REQUIRED_WAITS}"
    window = waits[-C3_REQUIRED_WAITS:]
    gaps = [
        int(window[i + 1].enter_realtime_ns) - int(window[i].exit_realtime_ns)
        for i in range(C3_K)
    ]
    if any(g < 0 for g in gaps):
        return None, "negative_gap"
    m = int(statistics.median(gaps))
    return {
        "median_gap_ns": m,
        "gaps_ns": gaps,
        "wait_count": len(window),
        "first_wait_cs": window[0].call_sequence,
        "last_wait_cs": window[-1].call_sequence,
        "window_endpoints": [
            {
                "call_sequence": int(w.call_sequence),
                "enter_ns": int(w.enter_realtime_ns),
                "exit_ns": int(w.exit_realtime_ns),
            }
            for w in window
        ],
        "raw_stream": int(raw_stream),
        "clock_domain": "preload_realtime",
    }, "ok"


def load_rank_device_work_audit(trace_dir: Path, logical_rank: int) -> dict[str, Any]:
    audit: dict[str, Any] = {}
    for path in sorted(trace_dir.glob("rank_*_pid_*.device_work_audit.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if int(data.get("rank", -1)) == int(logical_rank):
            audit = data
    return audit


def load_sidecar_timeline(trace_dir: Path, logical_rank: int) -> dict[int, int]:
    """Map preload call_sequence -> sidecar launch_count at wait exit."""
    timeline: dict[int, int] = {}
    for path in sorted(trace_dir.glob("rank_*_pid_*.sidecar_timeline.jsonl")):
        parts = path.name.split("_")
        if len(parts) < 2:
            continue
        try:
            rank = int(parts[1])
        except ValueError:
            continue
        if rank != int(logical_rank):
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            cs = int(row.get("preload_cs", 0) or 0)
            if cs > 0:
                lc_raw = row.get("launch_count")
                timeline[cs] = int(lc_raw) if lc_raw is not None else -1
    return timeline


def _generation_path_cs(audit: dict[str, Any]) -> set[int]:
    out: set[int] = set()
    for key in ("trigger_wait_preload_cs", "trigger_record_preload_cs"):
        v = int(audit.get(key, 0) or 0)
        if v > 0:
            out.add(v)
    return out


def prove_path_external(
    run_dir: Path,
    condition: str,
    rank_c3: dict[int, dict],
    session_start: int,
    selected_enter_ns: int,
) -> tuple[bool, dict[str, Any]]:
    trace_dir = run_dir / "event_trace"
    rank0_audit = load_rank_device_work_audit(trace_dir, C3_TARGET_RANK)
    generation_cs = _generation_path_cs(rank0_audit)
    trigger_wait_cs = int(rank0_audit.get("trigger_wait_preload_cs", 0) or 0)
    cond_upper = condition.upper()

    proofs: dict[str, Any] = {
        "all_endpoints_before_session": True,
        "all_endpoints_before_selected_wait": True,
        "sidecar_launch_count_zero_at_c3_endpoints": True,
        "not_on_wait_inject_comm_path": True,
        "condition": condition,
        "session_start_ns": session_start,
        "selected_wait_enter_ns": selected_enter_ns,
        "generation_path_cs": sorted(generation_cs),
        "rank_proofs": [],
        "endpoint_rows": [],
        "failures": [],
    }

    cond_upper = condition.upper()

    def endpoint_launch_count(rank: int, cs: int, timeline: dict[int, int], audit_lc: int) -> int:
        lc_at_cs = timeline.get(cs)
        if lc_at_cs is not None:
            return lc_at_cs
        if cond_upper == "D0" or rank != C3_TARGET_RANK:
            return 0 if audit_lc == 0 else audit_lc
        return -1

    for rank, data in sorted(rank_c3.items()):
        audit = load_rank_device_work_audit(trace_dir, rank)
        launch_count = int(audit.get("launch_count", audit.get("match_count", 0)) or 0)
        host_enter_ns = int(audit.get("host_enter_ns", 0) or 0)
        rank_trigger_cs = int(audit.get("trigger_wait_preload_cs", 0) or 0)
        endpoints = data.get("window_endpoints") or []
        max_c3_exit = max((int(e["exit_ns"]) for e in endpoints), default=0)
        max_c3_cs = max((int(e["call_sequence"]) for e in endpoints), default=0)
        sidecar_timeline = load_sidecar_timeline(trace_dir, rank)

        path_ok = True
        endpoint_launch_counts: list[int] = []
        for ep in endpoints:
            enter_ns = int(ep["enter_ns"])
            exit_ns = int(ep["exit_ns"])
            cs = int(ep["call_sequence"])
            on_path = cs in generation_cs
            lc_at_cs = endpoint_launch_count(rank, cs, sidecar_timeline, launch_count)
            endpoint_launch_counts.append(lc_at_cs)
            row = {
                "logical_rank": rank,
                "call_sequence": cs,
                "enter_ns": enter_ns,
                "exit_ns": exit_ns,
                "before_session": enter_ns < session_start and exit_ns < session_start,
                "before_selected_wait": enter_ns < selected_enter_ns and exit_ns < selected_enter_ns,
                "sidecar_launch_count_at_audit": launch_count,
                "c3_endpoint_launch_count": lc_at_cs,
                "sidecar_timeline_present": lc_at_cs >= 0,
                "on_generation_path": on_path,
            }
            proofs["endpoint_rows"].append(row)

            if not row["before_session"]:
                proofs["all_endpoints_before_session"] = False
                proofs["failures"].append(f"rank{rank}:cs{cs}:not_before_session")
            if not row["before_selected_wait"]:
                proofs["all_endpoints_before_selected_wait"] = False
                proofs["failures"].append(f"rank{rank}:cs{cs}:not_before_selected_wait")
            if on_path:
                path_ok = False
                proofs["failures"].append(f"rank{rank}:cs{cs}:on_generation_path")
            if lc_at_cs != 0:
                proofs["sidecar_launch_count_zero_at_c3_endpoints"] = False
                proofs["failures"].append(
                    f"rank{rank}:cs{cs}:c3_endpoint_launch_count={lc_at_cs}"
                )

        if trigger_wait_cs > 0 and rank == C3_TARGET_RANK and max_c3_cs >= trigger_wait_cs:
            path_ok = False
            proofs["failures"].append(f"rank{rank}:c3_window_reaches_selected_wait_cs")

        pre_inject_ok = all(lc == 0 for lc in endpoint_launch_counts) and endpoint_launch_counts
        c3_launch_count = max(endpoint_launch_counts) if endpoint_launch_counts else -1

        if not pre_inject_ok and endpoint_launch_counts:
            proofs["sidecar_launch_count_zero_at_c3_endpoints"] = False
        if not path_ok:
            proofs["not_on_wait_inject_comm_path"] = False

        rank_proof = {
            "logical_rank": rank,
            "sidecar_launch_count_at_audit": launch_count,
            "c3_endpoint_launch_count": c3_launch_count,
            "sidecar_host_enter_ns": host_enter_ns,
            "sidecar_trigger_wait_cs": rank_trigger_cs,
            "max_c3_exit_ns": max_c3_exit,
            "max_c3_call_sequence": max_c3_cs,
            "sidecar_timeline_entries": len(sidecar_timeline),
            "pre_inject_proof": "sidecar_timeline_at_wait_exit",
            "pre_inject_launch_count_zero": pre_inject_ok,
            "not_on_wait_inject_comm_path": path_ok,
        }
        proofs["rank_proofs"].append(rank_proof)

    ok = all(
        proofs[k]
        for k in (
            "all_endpoints_before_session",
            "all_endpoints_before_selected_wait",
            "sidecar_launch_count_zero_at_c3_endpoints",
            "not_on_wait_inject_comm_path",
        )
    )
    return ok, proofs


def extract_c3_all_ranks(
    run_dir: Path,
    condition: str,
    session_start: int,
    selected_enter_ns: int,
    rank0_raw_stream: int | None,
) -> tuple[dict[int, dict] | None, dict[str, Any] | None, str]:
    trace_dir = run_dir / "event_trace"
    by_rank = load_all_rank_traces(trace_dir)
    if len(by_rank) < 16:
        return None, None, f"STOP_C3_CONTROL_UNAVAILABLE:rank_count={len(by_rank)}"

    ref_count = C3_REQUIRED_WAITS
    if rank0_raw_stream is not None:
        ref_waits = _successful_waits_on_stream(
            by_rank.get(0, []),
            rank0_raw_stream,
            before_ns=selected_enter_ns,
            before_session=session_start,
        )
        ref_count = len(ref_waits)

    rank_c3: dict[int, dict] = {}
    for logical_rank in range(16):
        records = by_rank.get(logical_rank)
        if not records:
            return None, None, f"STOP_C3_CONTROL_UNAVAILABLE:missing_rank_{logical_rank}"
        if logical_rank == 0 and rank0_raw_stream is not None:
            raw = int(rank0_raw_stream)
            rule = "rank0_classification"
        else:
            raw, rule = identify_comm_raw_stream(records, session_start, ref_count)
        if raw is None:
            return None, None, f"STOP_C3_CONTROL_UNAVAILABLE:raw_stream_rank_{logical_rank}"
        fifo, err = compute_rank_preintervention_fifo(
            records, raw, session_start, selected_enter_ns
        )
        if fifo is None:
            return None, None, f"STOP_C3_CONTROL_UNAVAILABLE:{logical_rank}:{err}"
        fifo["logical_rank"] = logical_rank
        fifo["stream_ident_rule"] = rule
        rank_c3[logical_rank] = fifo

    path_ok, path_proofs = prove_path_external(
        run_dir, condition, rank_c3, session_start, selected_enter_ns
    )
    if not path_ok:
        return None, path_proofs, "STOP_C3_NOT_PATH_EXTERNAL"
    return rank_c3, path_proofs, "ok"


def compute_c3_did(
    d0_medians: dict[int, int], dt_medians: dict[int, int]
) -> tuple[dict[str, Any], str]:
    shifts = {r: int(dt_medians[r]) - int(d0_medians[r]) for r in range(16)}
    sentinel_shifts = [shifts[r] for r in C3_SENTINEL_RANKS]
    center = int(statistics.median(sentinel_shifts))
    c3_did = int(shifts[C3_TARGET_RANK]) - center
    noise = max(abs(int(shifts[r]) - center) for r in C3_SENTINEL_RANKS)
    return {
        "C3_preintervention_fifo_did_ns": c3_did,
        "C3_noise_envelope_ns": noise,
        "sentinel_center_ns": center,
        "target_shift_ns": shifts[C3_TARGET_RANK],
        "per_rank_shift_ns": shifts,
    }, "ok"


def evaluate_c3_stationarity(c3_did: int, noise: int) -> bool:
    return abs(int(c3_did)) <= int(noise)


def evaluate_c3_sensitivity(noise: int, predicted_unmasked: int) -> bool | None:
    if predicted_unmasked <= 0:
        return None
    return int(noise) / int(predicted_unmasked) <= REL_TOL


def build_c5_path_diagnostics(
    wait_task,
    comm_op: dict | None,
    inject_task=None,
) -> dict[str, Any]:
    wait_end = int(wait_task.end_ns)
    comm_start = int(comm_op["start_ns"]) if comm_op else None
    total_gap = (comm_start - wait_end) if comm_start is not None else None
    if inject_task is None:
        inject_span = 0
        post_tail = total_gap
    else:
        inject_span = int(inject_task.end_ns) - wait_end
        post_tail = (comm_start - int(inject_task.end_ns)) if comm_start is not None else None
    return {
        **C5_DIAG,
        "C5_total_gap_ns": total_gap,
        "C5_inject_span_ns": inject_span,
        "C5_post_path_tail_ns": post_tail,
        "anchor_ns": wait_end,
        "clock_domain": "profiler",
    }


def build_v49_run_controls(
    ctx,
    wait_task,
    record_task,
    comm_op: dict | None,
    upstream_kernel,
    comm_entry,
    inject_task=None,
    classification: dict | None = None,
    c3_rank: dict[int, dict] | None = None,
) -> dict[str, Any]:
    base = build_v48_run_controls(
        ctx, wait_task, record_task, comm_op, upstream_kernel, comm_entry, inject_task, classification
    )
    if c3_rank and C3_TARGET_RANK in c3_rank:
        tr = c3_rank[C3_TARGET_RANK]
        base["C3_preintervention_fifo_median_ns"] = {
            "value_ns": tr["median_gap_ns"],
            "unique": True,
            "source": "preload_last9_waits_K8",
            "anchor_ns": tr.get("last_wait_cs"),
            "clock_domain": "preload_realtime",
            "logical_rank": C3_TARGET_RANK,
        }
    base.pop("C3_pre_target_wait_host_fifo_gap_ns", None)
    base.pop("C5_host_issue_gap_ns", None)
    base["C5_path_diagnostic"] = build_c5_path_diagnostics(wait_task, comm_op, inject_task)
    return base


def evaluate_control_gate_v49(
    d0: dict, dtreat: dict, c3_block: dict | None
) -> tuple[bool, str, list[dict]]:
    realized = int(dtreat.get("realized_work_ns", 0) or 0)
    if realized <= 0 and dtreat.get("condition", "").upper() != "D0":
        return False, "REALIZED_WORK_INVALID", []
    tol = REL_TOL * max(realized, 1)
    c0 = d0.get("v49_controls") or d0.get("v48_controls") or {}
    cT = dtreat.get("v49_controls") or dtreat.get("v48_controls") or {}
    rows: list[dict] = []
    confounds: list[str] = []
    unavailable: list[str] = []

    for key in (
        "C1_wait_duration_ns",
        "C2_record_offset_from_wait_end_ns",
        "C4_bypass_compute_offset_from_wait_end_ns",
    ):
        a = c0.get(key) or {}
        b = cT.get(key) or {}
        unique = bool(a.get("unique") and b.get("unique"))
        v0, vT = a.get("value_ns"), b.get("value_ns")
        if not unique or v0 is None or vT is None:
            unavailable.append(key)
            rows.append({"metric": key, "shift_ns": "CONTROL_UNAVAILABLE", "unique": unique})
            continue
        shift = int(vT) - int(v0)
        rows.append({"metric": key, "shift_ns": shift, "unique": True})
        if abs(shift) > tol:
            confounds.append(f"{key}={shift}")

    if c3_block is None:
        unavailable.append("C3_preintervention_fifo_did")
        rows.append({"metric": "C3_preintervention_fifo_did_ns", "shift_ns": "CONTROL_UNAVAILABLE"})
    else:
        c3_did = int(c3_block.get("C3_preintervention_fifo_did_ns", 0))
        noise = int(c3_block.get("C3_noise_envelope_ns", 0))
        stationarity = evaluate_c3_stationarity(c3_did, noise)
        predicted = int(dtreat.get("predicted_unmasked_occupancy_ns", 0) or 0)
        if predicted <= 0 and dtreat.get("causal_eligibility") is None:
            elig, _ = causal_eligibility(
                predicted,
                int(dtreat.get("post_inject_gap_ns", 0) or 0),
                realized,
            )
        else:
            elig = dtreat.get("causal_eligibility")
        sensitivity = evaluate_c3_sensitivity(noise, predicted)
        rows.append(
            {
                "metric": "C3_preintervention_fifo_did_ns",
                "shift_ns": c3_did,
                "C3_noise_envelope_ns": noise,
                "C3_stationarity_pass": stationarity,
                "C3_sensitivity_pass": sensitivity,
                "unique": True,
            }
        )
        if not stationarity:
            return False, "STOP_GLOBAL_SCHEDULING_OR_CONTENTION_CONFOUND:C3_stationarity", rows
        if elig == "CAUSAL_ELIGIBLE":
            if sensitivity is None:
                pass
            elif not sensitivity:
                return False, "STOP_C3_CONTROL_SENSITIVITY_INSUFFICIENT", rows

    if unavailable:
        return False, "STOP_CONTROL_UNAVAILABLE:" + ",".join(unavailable), rows
    if confounds:
        return False, "STOP_GLOBAL_SCHEDULING_OR_CONTENTION_CONFOUND:" + ",".join(confounds), rows
    return True, "", rows


def extract_run_v49(
    run_dir: Path,
    run_id: str,
    condition: str,
    selector_manifest: dict | None,
    capture_sha: str,
    preflight_sha: str,
) -> dict[str, Any]:
    ex = extract_run_v48(run_dir, run_id, condition, selector_manifest, capture_sha, preflight_sha)
    if ex.get("status") != "OK":
        return ex

    cls = ex.get("capture_classification") or {}
    session_start = int(ex.get("active_start") or 0)
    if session_start <= 0:
        from wait_dag_v4_2_reverse_candidate import build_run_context

        ctx_tmp = build_run_context(run_dir)
        session_start = int(ctx_tmp.active_start)

    selected_enter = cls.get("selected_wait_preload_enter_ns")
    if selected_enter is None:
        return {**ex, "status": "STOP_C3_CONTROL_UNAVAILABLE:selected_enter_missing"}

    rank0_raw = cls.get("raw_comm_stream")
    c3_rank, path_proofs, c3_status = extract_c3_all_ranks(
        run_dir,
        condition,
        session_start,
        int(selected_enter),
        rank0_raw,
    )
    if c3_status != "ok":
        return {**ex, "status": c3_status, "c3_path_proofs": path_proofs}

    from wait_dag_v4_2_reverse_candidate import build_run_context

    ctx = build_run_context(run_dir)
    wait_task = ex["wait_task"]
    ident = ex.get("identity") or {}
    rec_rowid = ident.get("record_task_rowid")
    rec_task = ctx.all_tasks_by_rowid.get(rec_rowid) if rec_rowid else None
    upstream_rowid = ident.get("kernel_predecessor_rowid")
    upstream = ctx.all_tasks_by_rowid.get(upstream_rowid) if upstream_rowid else None
    v49c = build_v49_run_controls(
        ctx,
        wait_task,
        rec_task,
        ex.get("comm_op"),
        upstream,
        ex["comm_entry"],
        ex.get("inject_task"),
        cls,
        c3_rank,
    )
    ex["c3_rank_pairs"] = c3_rank
    ex["c3_path_proofs"] = path_proofs
    ex["v49_controls"] = v49c
    ex["active_start"] = session_start
    ex["c5_diagnostic"] = v49c.get("C5_path_diagnostic")
    return ex


def paired_effects_v4_9(d0: dict, dtreat: dict, block: str) -> dict[str, Any]:
    base = paired_effects_v4_8_bridge(d0, dtreat, block)
    if base.get("dose_gate_pass") not in (True, "True"):
        return base

    d0_cls = d0.get("capture_classification") or {}
    dt_cls = dtreat.get("capture_classification") or {}
    for cls in (d0_cls, dt_cls):
        st = cls.get("pre_wait_p_status")
        if st and st != "STRUCTURAL_NA" and str(st).startswith("STOP"):
            base["control_gate_pass"] = False
            base["control_reason"] = st
            return base

    d0_medians = {
        r: d["median_gap_ns"] for r, d in (d0.get("c3_rank_pairs") or {}).items()
    }
    dt_medians = {
        r: d["median_gap_ns"] for r, d in (dtreat.get("c3_rank_pairs") or {}).items()
    }
    if len(d0_medians) != 16 or len(dt_medians) != 16:
        base["control_gate_pass"] = False
        base["control_reason"] = "STOP_C3_CONTROL_UNAVAILABLE:rank_pairs_incomplete"
        return base

    c3_block, _ = compute_c3_did(d0_medians, dt_medians)
    base["C3_preintervention_fifo_did_ns"] = c3_block["C3_preintervention_fifo_did_ns"]
    base["C3_noise_envelope_ns"] = c3_block["C3_noise_envelope_ns"]
    base["C3_stationarity_pass"] = evaluate_c3_stationarity(
        c3_block["C3_preintervention_fifo_did_ns"], c3_block["C3_noise_envelope_ns"]
    )
    predicted = int(base.get("predicted_unmasked_occupancy_ns", 0) or 0)
    base["C3_sensitivity_pass"] = evaluate_c3_sensitivity(
        c3_block["C3_noise_envelope_ns"], predicted
    )

    c5_d0 = (d0.get("c5_diagnostic") or {}).get("C5_total_gap_ns")
    c5_dt = (dtreat.get("c5_diagnostic") or {}).get("C5_total_gap_ns")
    if c5_d0 is not None and c5_dt is not None:
        c5_shift = int(c5_dt) - int(c5_d0)
        realized = int(base.get("realized_work_ns", 0) or 0)
        base["C5_total_shift_ns"] = c5_shift
        base["C5_dose_follow_ratio"] = (c5_shift / realized) if realized > 0 else None
    base["C5_gate_boolean"] = None
    base["C5_in_control_denominator"] = False

    ok, reason, _ = evaluate_control_gate_v49(d0, {**dtreat, **base}, c3_block)
    base["control_gate_pass"] = ok
    base["control_reason"] = reason
    base["c3_block"] = c3_block
    return base


def paired_effects_v4_8_bridge(d0: dict, dtreat: dict, block: str) -> dict[str, Any]:
    base = paired_effects_v4_7(d0, dtreat, block)
    d0_cls = d0.get("capture_classification") or {}
    dt_cls = dtreat.get("capture_classification") or {}
    base["pre_wait_p_status_d0"] = d0_cls.get("pre_wait_p_status")
    base["pre_wait_p_status_treatment"] = dt_cls.get("pre_wait_p_status")
    base["pre_wait_p_gate_boolean"] = dt_cls.get("pre_wait_p_gate_boolean")
    return base


def build_c3_csv_rows(d0: dict, dtreat: dict, block: str, c3_block: dict) -> list[dict]:
    rows = []
    for rank in range(16):
        d0m = (d0.get("c3_rank_pairs") or {}).get(rank, {})
        dtm = (dtreat.get("c3_rank_pairs") or {}).get(rank, {})
        rows.append(
            {
                "block": block,
                "logical_rank": rank,
                "is_target": rank == C3_TARGET_RANK,
                "is_sentinel": rank in C3_SENTINEL_RANKS,
                "d0_median_gap_ns": d0m.get("median_gap_ns"),
                "dtreat_median_gap_ns": dtm.get("median_gap_ns"),
                "shift_ns": c3_block["per_rank_shift_ns"].get(rank),
                "d0_raw_stream": d0m.get("raw_stream"),
                "dtreat_raw_stream": dtm.get("raw_stream"),
                "K": C3_K,
            }
        )
    return rows


def build_c3_envelope_row(block: str, c3_block: dict, paired: dict) -> dict:
    return {
        "block": block,
        "C3_preintervention_fifo_did_ns": c3_block["C3_preintervention_fifo_did_ns"],
        "C3_noise_envelope_ns": c3_block["C3_noise_envelope_ns"],
        "sentinel_center_ns": c3_block["sentinel_center_ns"],
        "target_shift_ns": c3_block["target_shift_ns"],
        "C3_stationarity_pass": paired.get("C3_stationarity_pass"),
        "C3_sensitivity_pass": paired.get("C3_sensitivity_pass"),
        "target_logical_rank": C3_TARGET_RANK,
        "sentinel_count": len(C3_SENTINEL_RANKS),
    }


def build_c5_csv_row(block: str, d0: dict, dtreat: dict, paired: dict) -> dict:
    c5d = d0.get("c5_diagnostic") or {}
    c5t = dtreat.get("c5_diagnostic") or {}
    return {
        "block": block,
        "C5_status": "PATH_DIAGNOSTIC_ONLY",
        "C5_gate_boolean": None,
        "C5_in_control_denominator": False,
        "d0_total_gap_ns": c5d.get("C5_total_gap_ns"),
        "dtreat_total_gap_ns": c5t.get("C5_total_gap_ns"),
        "d0_inject_span_ns": c5d.get("C5_inject_span_ns"),
        "dtreat_inject_span_ns": c5t.get("C5_inject_span_ns"),
        "d0_post_path_tail_ns": c5d.get("C5_post_path_tail_ns"),
        "dtreat_post_path_tail_ns": c5t.get("C5_post_path_tail_ns"),
        "C5_total_shift_ns": paired.get("C5_total_shift_ns"),
        "C5_dose_follow_ratio": paired.get("C5_dose_follow_ratio"),
    }


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
    classification_rows: list[dict] = []
    ledger_rows: list[dict] = []
    identity_rows: list[dict] = []
    injection_rows: list[dict] = []
    path_external_all: list[dict] = []
    path_external_rows: list[dict] = []

    for spec in runs:
        run_id = spec["run_id"]
        condition = spec["condition"]
        run_dir = Path(spec["run_dir"])
        try:
            ex = extract_run_v49(
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
        pp = ex.get("c3_path_proofs")
        if pp:
            path_external_all.append(
                {
                    "run_id": run_id,
                    "condition": condition,
                    "all_endpoints_before_session": pp.get("all_endpoints_before_session"),
                    "all_endpoints_before_selected_wait": pp.get(
                        "all_endpoints_before_selected_wait"
                    ),
                    "sidecar_launch_count_zero_at_c3_endpoints": pp.get(
                        "sidecar_launch_count_zero_at_c3_endpoints"
                    ),
                    "not_on_wait_inject_comm_path": pp.get("not_on_wait_inject_comm_path"),
                    "failures": pp.get("failures"),
                }
            )
            for row in pp.get("endpoint_rows") or []:
                path_external_rows.append({"run_id": run_id, "condition": condition, **row})
        identity_rows.append(ex["identity"])
        injection_rows.append(
            {
                "run_id": run_id,
                "condition": condition,
                "launch_count": ex["identity"].get("launch_count"),
                "pre_wait_p_status": (ex.get("capture_classification") or {}).get("pre_wait_p_status"),
            }
        )

    paired: list[dict] = []
    c3_rows: list[dict] = []
    c3_env_rows: list[dict] = []
    c5_rows: list[dict] = []
    control_rows: list[dict] = []

    for pair in pairs:
        if len(pair) != 3:
            continue
        block, d0_id, dt_id = pair
        d0, dt = extracted.get(d0_id), extracted.get(dt_id)
        if not d0 or not dt or d0.get("status") != "OK" or dt.get("status") != "OK":
            paired.append({"block": block, "extraction_status": "PAIR_INVALID", "control_gate_pass": False})
            continue
        row = paired_effects_v4_9(d0, dt, block)
        paired.append(row)
        c3_block = row.get("c3_block") or {}
        if c3_block:
            c3_rows.extend(build_c3_csv_rows(d0, dt, block, c3_block))
            c3_env_rows.append(build_c3_envelope_row(block, c3_block, row))
        c5_rows.append(build_c5_csv_row(block, d0, dt, row))
        _, _, ctrl = evaluate_control_gate_v49(d0, dt, c3_block if c3_block else None)
        control_rows.extend({"block": block, **r} for r in ctrl)

    v49_fields = PAIRED_FIELDS + [
        "pre_wait_p_status_d0",
        "pre_wait_p_status_treatment",
        "pre_wait_p_gate_boolean",
        "C3_preintervention_fifo_did_ns",
        "C3_noise_envelope_ns",
        "C3_stationarity_pass",
        "C3_sensitivity_pass",
        "C5_total_shift_ns",
        "C5_dose_follow_ratio",
        "C5_gate_boolean",
        "C5_in_control_denominator",
    ]
    write_csv(out / "paired_effects_v4_9.csv", paired, v49_fields)
    write_csv(out / "c3_preintervention_rank_pairs.csv", c3_rows, list(c3_rows[0].keys()) if c3_rows else ["block"])
    write_csv(out / "c3_noise_envelope.csv", c3_env_rows, list(c3_env_rows[0].keys()) if c3_env_rows else ["block"])
    write_csv(out / "c5_path_diagnostics.csv", c5_rows, list(c5_rows[0].keys()) if c5_rows else ["block"])
    write_csv(out / "capture_classification.csv", classification_rows, list(classification_rows[0].keys()) if classification_rows else ["run_id"])
    write_csv(out / "run_ledger.csv", ledger_rows, ["run_id", "condition", "status", "run_dir"])
    write_csv(out / "control_effects_v4_9.csv", control_rows, ["block", "metric", "shift_ns"])
    (out / "path_external_proofs.json").write_text(
        json.dumps({"runs": path_external_all, "endpoint_rows": path_external_rows}, indent=2) + "\n"
    )
    if path_external_rows:
        write_csv(
            out / "path_external_proofs.csv",
            path_external_rows,
            list(path_external_rows[0].keys()),
        )

    summary = {"schema": V49_SCHEMA, "paired": paired, "c3_contract_sha256": c3_contract_sha()}
    (out / "v4_9_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()

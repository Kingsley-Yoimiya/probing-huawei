#!/usr/bin/env python3
"""D51 Wait DAG V4.10: C3 ordinal paired studentized envelope + global location gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

from wait_dag_v4_7_intervention import (
    PAIRED_FIELDS,
    causal_eligibility,
    load_selector_manifest,
    paired_effects_v4_7,
    write_csv,
)
from wait_dag_v4_8_intervention import (
    build_v48_run_controls,
    capture_contract_sha,
    classification_csv_row,
    extract_run_v48,
)
from wait_dag_v4_9_intervention import (
    C3_K,
    C3_REQUIRED_WAITS,
    C3_SENTINEL_RANKS,
    C3_TARGET_RANK,
    C5_DIAG,
    build_c5_csv_row,
    build_c5_path_diagnostics,
    compute_rank_preintervention_fifo,
    extract_c3_all_ranks as _extract_c3_all_ranks_v49,
    load_all_rank_traces,
    load_rank_device_work_audit,
    load_sidecar_timeline,
    prove_path_external as _prove_path_external_v49,
)

V410_SCHEMA = "d51_wait_dag_v4_10_c3_ordinal_studentized"
C3_ORDINAL_INDICES = list(range(-9, -1))  # k = -9 .. -2, K=8
C3_EPSILON_NS = 1
C3_MAD_SCALE = 1.4826
C3_LAMBDA = 3
C3_CONTRACT = {
    "target_logical_rank": C3_TARGET_RANK,
    "sentinel_ranks": C3_SENTINEL_RANKS,
    "K": C3_K,
    "ordinal_indices": C3_ORDINAL_INDICES,
    "required_success_waits": C3_REQUIRED_WAITS,
    "gap_formula": "W[k+1].enter - W[k].exit, k=-9..-2",
    "x_formula": "x[T,r,k]=g[T,r,k]-g[D0,r,k]",
    "target_formula": "y*[k]=x[r*,k]-median_S(x[s,k]); theta*=median_k(y*)",
    "sentinel_formula": "y_s[k]=x[s,k]-median_{S\\{s}}(x[j,k]); theta_s=median_k(y_s)",
    "scale_formula": "v=1.4826*MAD_k(y); h=max(1ns,median(v_s)); z=|theta|/(v+h)",
    "outer_fence": "Q3+lambda*IQR nearest-rank Q1=a_(4) Q3=a_(12) for n=15",
    "lambda": C3_LAMBDA,
    "epsilon_ns": C3_EPSILON_NS,
    "global_location_gate": "abs(G)<=3*max(1ns,V_G)",
    "dsmall_degrade_codes": [
        "STOP_C3_CONTROL_SENSITIVITY_INSUFFICIENT:TARGET_SCALE_NONEXCHANGEABLE",
        "STOP_GLOBAL_SCHEDULING_OR_CONTENTION_CONFOUND:C3_target_studentized",
    ],
}
DSMALL_C3_DEGRADE_CODES = frozenset(C3_CONTRACT["dsmall_degrade_codes"])


def c3_contract_sha() -> str:
    blob = json.dumps(C3_CONTRACT, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def prove_path_external(
    run_dir: Path,
    condition: str,
    rank_c3: dict[int, dict],
    session_start: int,
    selected_enter_ns: int,
) -> tuple[bool, dict[str, Any]]:
    """V4.10: treatment r*=0 must hit sidecar timeline with literal launch_count=0."""
    ok, proofs = _prove_path_external_v49(
        run_dir, condition, rank_c3, session_start, selected_enter_ns
    )
    cond_upper = condition.upper()
    if cond_upper in ("DSMALL", "DLARGE"):
        trace_dir = run_dir / "event_trace"
        target_data = rank_c3.get(C3_TARGET_RANK, {})
        endpoints = target_data.get("window_endpoints") or []
        timeline = load_sidecar_timeline(trace_dir, C3_TARGET_RANK)
        for ep in endpoints:
            cs = int(ep["call_sequence"])
            if cs not in timeline:
                proofs["sidecar_launch_count_zero_at_c3_endpoints"] = False
                proofs["failures"].append(
                    f"rank{C3_TARGET_RANK}:cs{cs}:missing_sidecar_timeline"
                )
                ok = False
            elif timeline[cs] != 0:
                proofs["sidecar_launch_count_zero_at_c3_endpoints"] = False
                proofs["failures"].append(
                    f"rank{C3_TARGET_RANK}:cs{cs}:LITERAL_TREATMENT_LAUNCH_COUNT={timeline[cs]}"
                )
                ok = False
        if not ok and any("LITERAL_TREATMENT" in f or "missing_sidecar" in f for f in proofs.get("failures", [])):
            proofs["stop_code"] = "STOP_C3_NOT_PATH_EXTERNAL:LITERAL_TREATMENT_LAUNCH_COUNT"
    return ok, proofs


def extract_c3_all_ranks(
    run_dir: Path,
    condition: str,
    session_start: int,
    selected_enter_ns: int,
    rank0_raw_stream: int | None,
) -> tuple[dict[int, dict] | None, dict[str, Any] | None, str]:
    from wait_dag_v4_9_intervention import (
        _generation_path_cs,
        _successful_waits_on_stream,
        identify_comm_raw_stream,
    )

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
        code = path_proofs.get("stop_code", "STOP_C3_NOT_PATH_EXTERNAL")
        return None, path_proofs, code
    return rank_c3, path_proofs, "ok"


def _median_int(vals: list[int]) -> int:
    return int(statistics.median(vals))


def _mad_scale(vals: list[int], center: int) -> int:
    devs = [abs(int(v) - int(center)) for v in vals]
    if not devs:
        return 0
    return int(round(C3_MAD_SCALE * statistics.median(devs)))


def nearest_rank_quartiles(sorted_vals: list[float]) -> tuple[float, float, float]:
    """Nearest-rank Q1/Q3/IQR for n=15: Q1=a_(4), Q3=a_(12)."""
    n = len(sorted_vals)
    if n != 15:
        raise ValueError(f"nearest_rank_quartiles requires n=15, got {n}")
    q1 = sorted_vals[3]
    q3 = sorted_vals[11]
    return q1, q3, q3 - q1


def compute_ordinal_x(
    d0_c3: dict[int, dict], dt_c3: dict[int, dict]
) -> dict[int, list[int]]:
    x: dict[int, list[int]] = {}
    for r in range(16):
        g0 = (d0_c3.get(r) or {}).get("gaps_ns")
        gT = (dt_c3.get(r) or {}).get("gaps_ns")
        if not g0 or not gT or len(g0) != C3_K or len(gT) != C3_K:
            raise ValueError(f"ordinal_misaligned:rank_{r}")
        x[r] = [int(gT[i]) - int(g0[i]) for i in range(C3_K)]
    return x


def compute_c3_ordinal_block(x: dict[int, list[int]]) -> dict[str, Any]:
    """Full V4.10 C3 ordinal studentized block from x[T,r,k]."""
    sentinels = C3_SENTINEL_RANKS
    k_count = C3_K

    y_star = []
    for ki in range(k_count):
        xs = [x[s][ki] for s in sentinels]
        center = _median_int(xs)
        y_star.append(int(x[C3_TARGET_RANK][ki]) - center)
    theta_star = _median_int(y_star)
    v_star = _mad_scale(y_star, theta_star)

    pseudo: dict[int, dict[str, int | float]] = {}
    v_s_list: list[int] = []
    z_s_list: list[float] = []
    for s in sentinels:
        others = [j for j in sentinels if j != s]
        y_s = []
        for ki in range(k_count):
            center = _median_int([x[j][ki] for j in others])
            y_s.append(int(x[s][ki]) - center)
        theta_s = _median_int(y_s)
        v_s = _mad_scale(y_s, theta_s)
        pseudo[s] = {"theta_s": theta_s, "v_s": v_s, "y_s": y_s}
        v_s_list.append(v_s)

    h = max(C3_EPSILON_NS, _median_int(v_s_list))
    for s in sentinels:
        theta_s = int(pseudo[s]["theta_s"])
        v_s = int(pseudo[s]["v_s"])
        z_s = abs(theta_s) / (v_s + h)
        pseudo[s]["z_s"] = z_s
        z_s_list.append(z_s)

    z_sorted = sorted(z_s_list)
    v_sorted = sorted(float(v) for v in v_s_list)
    q1_z, q3_z, iqr_z = nearest_rank_quartiles(z_sorted)
    q1_v, q3_v, iqr_v = nearest_rank_quartiles(v_sorted)
    z_outer = q3_z + C3_LAMBDA * iqr_z
    v_outer = q3_v + C3_LAMBDA * iqr_v

    z_star = abs(theta_star) / (v_star + h)
    noise_envelope_ns = int(round(z_outer * (v_star + h)))

    q_k = [_median_int([x[s][ki] for s in sentinels]) for ki in range(k_count)]
    G = _median_int(q_k)
    V_G = _mad_scale(q_k, G)
    global_pass = abs(G) <= C3_LAMBDA * max(C3_EPSILON_NS, V_G)

    max_z_s = max(z_s_list)
    sentinel_coverage = max_z_s <= z_outer
    target_scale_ok = v_star <= max(C3_EPSILON_NS, int(round(v_outer)))
    target_stationarity = z_star <= z_outer

    return {
        "C3_target_did_ns": theta_star,
        "C3_noise_envelope_ns": noise_envelope_ns,
        "theta_star_ns": theta_star,
        "v_star_ns": v_star,
        "h_ns": h,
        "z_star": z_star,
        "z_outer": z_outer,
        "v_outer_ns": int(round(v_outer)),
        "Q1_z": q1_z,
        "Q3_z": q3_z,
        "IQR_z": iqr_z,
        "Q1_v_ns": int(round(q1_v)),
        "Q3_v_ns": int(round(q3_v)),
        "IQR_v_ns": int(round(iqr_v)),
        "sentinel_coverage_pass": sentinel_coverage,
        "target_scale_exchangeable_pass": target_scale_ok,
        "C3_target_stationarity_pass": target_stationarity,
        "C3_global_location_pass": global_pass,
        "G_ns": G,
        "V_G_ns": V_G,
        "max_z_s": max_z_s,
        "y_star_ns": y_star,
        "pseudo_targets": {
            str(s): {
                "theta_s_ns": pseudo[s]["theta_s"],
                "v_s_ns": pseudo[s]["v_s"],
                "z_s": pseudo[s]["z_s"],
            }
            for s in sentinels
        },
        "ordinal_x_ns": {str(r): x[r] for r in range(16)},
        "q_k_ns": q_k,
    }


def c3_failure_reason(c3_block: dict[str, Any], path_external: bool = True) -> str | None:
    if not path_external:
        return "STOP_C3_NOT_PATH_EXTERNAL"
    if not c3_block.get("C3_global_location_pass"):
        return "STOP_GLOBAL_SCHEDULING_OR_CONTENTION_CONFOUND:C3_global_location"
    if not c3_block.get("sentinel_coverage_pass"):
        return "STOP_C3_NOISE_MODEL_UNSTABLE:SENTINEL_OUTER_FENCE"
    if not c3_block.get("target_scale_exchangeable_pass"):
        return "STOP_C3_CONTROL_SENSITIVITY_INSUFFICIENT:TARGET_SCALE_NONEXCHANGEABLE"
    if not c3_block.get("C3_target_stationarity_pass"):
        return "STOP_GLOBAL_SCHEDULING_OR_CONTENTION_CONFOUND:C3_target_studentized"
    return None


def c3_pass(c3_block: dict[str, Any], path_external: bool = True) -> bool:
    return c3_failure_reason(c3_block, path_external) is None


def dsmall_c3_degrade_allowed(
    c3_block: dict[str, Any],
    structure_only: bool,
    path_external: bool = True,
) -> tuple[bool, str | None]:
    """Only target-specific C3 failures with full structure predicates."""
    if not structure_only:
        return False, None
    reason = c3_failure_reason(c3_block, path_external)
    if reason is None:
        return False, None
    if reason in DSMALL_C3_DEGRADE_CODES:
        return True, reason
    return False, reason


def evaluate_control_gate_v410(
    d0: dict,
    dtreat: dict,
    c3_block: dict | None,
    *,
    is_dsmall: bool = False,
    structure_only: bool = False,
    path_external: bool = True,
) -> tuple[bool, str, list[dict], dict[str, Any]]:
    from wait_dag_v4_9_intervention import evaluate_control_gate_v49

    realized = int(dtreat.get("realized_work_ns", 0) or 0)
    if realized <= 0 and dtreat.get("condition", "").upper() != "D0":
        return False, "REALIZED_WORK_INVALID", [], {}

    c0 = d0.get("v49_controls") or d0.get("v48_controls") or {}
    cT = dtreat.get("v49_controls") or dtreat.get("v48_controls") or {}
    rows: list[dict] = []
    confounds: list[str] = []
    unavailable: list[str] = []
    meta: dict[str, Any] = {
        "C3_gate_boolean": None,
        "C3_status": None,
        "continue_to_Dlarge": False,
        "C3_in_control_denominator": False,
    }

    tol = 0.20 * max(realized, 1)
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
        unavailable.append("C3_ordinal_did")
        rows.append({"metric": "C3_target_did_ns", "shift_ns": "CONTROL_UNAVAILABLE"})
    else:
        fail = c3_failure_reason(c3_block, path_external)
        rows.append(
            {
                "metric": "C3_target_did_ns",
                "shift_ns": c3_block.get("C3_target_did_ns"),
                "C3_noise_envelope_ns": c3_block.get("C3_noise_envelope_ns"),
                "z_star": c3_block.get("z_star"),
                "z_outer": c3_block.get("z_outer"),
                "C3_global_location_pass": c3_block.get("C3_global_location_pass"),
                "sentinel_coverage_pass": c3_block.get("sentinel_coverage_pass"),
                "target_scale_exchangeable_pass": c3_block.get("target_scale_exchangeable_pass"),
                "C3_target_stationarity_pass": c3_block.get("C3_target_stationarity_pass"),
                "unique": True,
            }
        )
        if fail:
            degrade_ok, _ = dsmall_c3_degrade_allowed(
                c3_block, structure_only and is_dsmall, path_external
            )
            if degrade_ok:
                meta["C3_status"] = "STRUCTURE_ONLY_NOT_CAUSAL_C3_TARGET_UNSTABLE"
                meta["C3_gate_boolean"] = None
                meta["continue_to_Dlarge"] = True
                meta["C3_in_control_denominator"] = False
            else:
                return False, fail, rows, meta

    if unavailable:
        return False, "STOP_CONTROL_UNAVAILABLE:" + ",".join(unavailable), rows, meta
    if confounds:
        return False, "STOP_GLOBAL_SCHEDULING_OR_CONTENTION_CONFOUND:" + ",".join(confounds), rows, meta

    if c3_block is not None and c3_pass(c3_block, path_external):
        meta["C3_gate_boolean"] = True
        meta["C3_status"] = "PASS"
        meta["C3_in_control_denominator"] = True
    elif meta.get("continue_to_Dlarge"):
        pass
    else:
        meta["C3_gate_boolean"] = False

    return True, "", rows, meta


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
    from wait_dag_v4_9_intervention import build_v49_run_controls as _b

    return _b(
        ctx, wait_task, record_task, comm_op, upstream_kernel, comm_entry,
        inject_task, classification, c3_rank,
    )


def extract_run_v410(
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
        run_dir, condition, session_start, int(selected_enter), rank0_raw,
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
        ctx, wait_task, rec_task, ex.get("comm_op"), upstream,
        ex["comm_entry"], ex.get("inject_task"), cls, c3_rank,
    )
    ex["c3_rank_pairs"] = c3_rank
    ex["c3_path_proofs"] = path_proofs
    ex["v49_controls"] = v49c
    ex["active_start"] = session_start
    ex["c5_diagnostic"] = v49c.get("C5_path_diagnostic")
    return ex


def paired_effects_v4_8_bridge(d0: dict, dtreat: dict, block: str) -> dict[str, Any]:
    base = paired_effects_v4_7(d0, dtreat, block)
    d0_cls = d0.get("capture_classification") or {}
    dt_cls = dtreat.get("capture_classification") or {}
    base["pre_wait_p_status_d0"] = d0_cls.get("pre_wait_p_status")
    base["pre_wait_p_status_treatment"] = dt_cls.get("pre_wait_p_status")
    base["pre_wait_p_gate_boolean"] = dt_cls.get("pre_wait_p_gate_boolean")
    return base


def paired_effects_v4_10(d0: dict, dtreat: dict, block: str) -> dict[str, Any]:
    base = paired_effects_v4_8_bridge(d0, dtreat, block)
    if base.get("dose_gate_pass") not in (True, "True"):
        return base

    for cls in (d0.get("capture_classification") or {}, dtreat.get("capture_classification") or {}):
        st = cls.get("pre_wait_p_status")
        if st and st != "STRUCTURAL_NA" and str(st).startswith("STOP"):
            base["control_gate_pass"] = False
            base["control_reason"] = st
            return base

    d0_c3 = d0.get("c3_rank_pairs") or {}
    dt_c3 = dtreat.get("c3_rank_pairs") or {}
    if len(d0_c3) != 16 or len(dt_c3) != 16:
        base["control_gate_pass"] = False
        base["control_reason"] = "STOP_C3_CONTROL_UNAVAILABLE:rank_pairs_incomplete"
        return base

    try:
        x = compute_ordinal_x(d0_c3, dt_c3)
        c3_block = compute_c3_ordinal_block(x)
    except ValueError as e:
        base["control_gate_pass"] = False
        base["control_reason"] = f"STOP_C3_CONTROL_UNAVAILABLE:{e}"
        return base

    cond = str(dtreat.get("condition", "")).upper()
    is_dsmall = cond == "DSMALL"
    structure_only = base.get("causal_eligibility") == "STRUCTURE_ONLY_NOT_CAUSAL"

    base.update(
        {
            "C3_target_did_ns": c3_block["C3_target_did_ns"],
            "C3_noise_envelope_ns": c3_block["C3_noise_envelope_ns"],
            "C3_studentized_z_star": c3_block["z_star"],
            "C3_z_outer": c3_block["z_outer"],
            "C3_global_location_pass": c3_block["C3_global_location_pass"],
            "C3_sentinel_coverage_pass": c3_block["sentinel_coverage_pass"],
            "C3_target_scale_exchangeable_pass": c3_block["target_scale_exchangeable_pass"],
            "C3_target_stationarity_pass": c3_block["C3_target_stationarity_pass"],
            "C3_pass": c3_pass(c3_block),
        }
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

    ok, reason, _, meta = evaluate_control_gate_v410(
        d0,
        {**dtreat, **base},
        c3_block,
        is_dsmall=is_dsmall,
        structure_only=structure_only,
    )
    base["control_gate_pass"] = ok
    base["control_reason"] = reason
    base["C3_gate_boolean"] = meta.get("C3_gate_boolean")
    base["C3_status"] = meta.get("C3_status")
    base["continue_to_Dlarge"] = meta.get("continue_to_Dlarge", False)
    base["C3_in_control_denominator"] = meta.get("C3_in_control_denominator", False)
    base["c3_block"] = c3_block
    return base


def build_ordinal_pair_rows(
    d0: dict, dtreat: dict, block: str, x: dict[int, list[int]]
) -> list[dict]:
    rows = []
    for r in range(16):
        d0g = (d0.get("c3_rank_pairs") or {}).get(r, {}).get("gaps_ns", [])
        dtg = (dtreat.get("c3_rank_pairs") or {}).get(r, {}).get("gaps_ns", [])
        for ki, k_ord in enumerate(C3_ORDINAL_INDICES):
            rows.append(
                {
                    "block": block,
                    "logical_rank": r,
                    "k_ordinal": k_ord,
                    "is_target": r == C3_TARGET_RANK,
                    "is_sentinel": r in C3_SENTINEL_RANKS,
                    "d0_gap_ns": d0g[ki] if ki < len(d0g) else None,
                    "dtreat_gap_ns": dtg[ki] if ki < len(dtg) else None,
                    "x_ns": x[r][ki],
                }
            )
    return rows


def build_pseudotarget_rows(block: str, c3_block: dict) -> list[dict]:
    rows = []
    for s_str, pdata in (c3_block.get("pseudo_targets") or {}).items():
        rows.append(
            {
                "block": block,
                "pseudo_target_rank": int(s_str),
                "theta_s_ns": pdata["theta_s_ns"],
                "v_s_ns": pdata["v_s_ns"],
                "z_s": pdata["z_s"],
            }
        )
    return rows


def build_studentized_envelope_row(block: str, c3_block: dict, paired: dict) -> dict:
    return {
        "block": block,
        "C3_target_did_ns": c3_block["C3_target_did_ns"],
        "C3_noise_envelope_ns": c3_block["C3_noise_envelope_ns"],
        "theta_star_ns": c3_block["theta_star_ns"],
        "v_star_ns": c3_block["v_star_ns"],
        "h_ns": c3_block["h_ns"],
        "z_star": c3_block["z_star"],
        "z_outer": c3_block["z_outer"],
        "v_outer_ns": c3_block["v_outer_ns"],
        "Q1_z": c3_block["Q1_z"],
        "Q3_z": c3_block["Q3_z"],
        "IQR_z": c3_block["IQR_z"],
        "max_z_s": c3_block["max_z_s"],
        "sentinel_coverage_pass": c3_block["sentinel_coverage_pass"],
        "target_scale_exchangeable_pass": c3_block["target_scale_exchangeable_pass"],
        "C3_target_stationarity_pass": c3_block["C3_target_stationarity_pass"],
        "C3_pass": paired.get("C3_pass"),
        "C3_gate_boolean": paired.get("C3_gate_boolean"),
        "C3_status": paired.get("C3_status"),
    }


def build_global_location_row(block: str, c3_block: dict) -> dict:
    return {
        "block": block,
        "G_ns": c3_block["G_ns"],
        "V_G_ns": c3_block["V_G_ns"],
        "C3_global_location_pass": c3_block["C3_global_location_pass"],
        "q_k_ns": json.dumps(c3_block.get("q_k_ns")),
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
    path_external_all: list[dict] = []
    path_external_rows: list[dict] = []

    for spec in runs:
        run_id = spec["run_id"]
        condition = spec["condition"]
        run_dir = Path(spec["run_dir"])
        try:
            ex = extract_run_v410(
                run_dir, run_id, condition, selector_manifest,
                args.capture_contract_sha, args.preflight_capture_sha,
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
                    "all_endpoints_before_selected_wait": pp.get("all_endpoints_before_selected_wait"),
                    "sidecar_launch_count_zero_at_c3_endpoints": pp.get(
                        "sidecar_launch_count_zero_at_c3_endpoints"
                    ),
                    "not_on_wait_inject_comm_path": pp.get("not_on_wait_inject_comm_path"),
                    "failures": pp.get("failures"),
                }
            )
            for row in pp.get("endpoint_rows") or []:
                path_external_rows.append({"run_id": run_id, "condition": condition, **row})

    paired: list[dict] = []
    ordinal_rows: list[dict] = []
    pseudo_rows: list[dict] = []
    envelope_rows: list[dict] = []
    global_rows: list[dict] = []
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
        row = paired_effects_v4_10(d0, dt, block)
        paired.append(row)
        c3_block = row.get("c3_block") or {}
        if c3_block:
            try:
                x = compute_ordinal_x(d0.get("c3_rank_pairs") or {}, dt.get("c3_rank_pairs") or {})
                ordinal_rows.extend(build_ordinal_pair_rows(d0, dt, block, x))
            except ValueError:
                pass
            pseudo_rows.extend(build_pseudotarget_rows(block, c3_block))
            envelope_rows.append(build_studentized_envelope_row(block, c3_block, row))
            global_rows.append(build_global_location_row(block, c3_block))
        c5_rows.append(build_c5_csv_row(block, d0, dt, row))
        _, _, ctrl, _ = evaluate_control_gate_v410(
            d0, dt, c3_block if c3_block else None,
            is_dsmall=str(dt.get("condition", "")).upper() == "DSMALL",
            structure_only=row.get("causal_eligibility") == "STRUCTURE_ONLY_NOT_CAUSAL",
        )
        control_rows.extend({"block": block, **r} for r in ctrl)

    v410_fields = PAIRED_FIELDS + [
        "pre_wait_p_status_d0",
        "pre_wait_p_status_treatment",
        "pre_wait_p_gate_boolean",
        "C3_target_did_ns",
        "C3_noise_envelope_ns",
        "C3_studentized_z_star",
        "C3_z_outer",
        "C3_global_location_pass",
        "C3_sentinel_coverage_pass",
        "C3_target_scale_exchangeable_pass",
        "C3_target_stationarity_pass",
        "C3_pass",
        "C3_gate_boolean",
        "C3_status",
        "C3_in_control_denominator",
        "continue_to_Dlarge",
        "C5_total_shift_ns",
        "C5_dose_follow_ratio",
        "C5_gate_boolean",
        "C5_in_control_denominator",
    ]
    write_csv(out / "paired_effects_v4_10.csv", paired, v410_fields)
    write_csv(
        out / "c3_ordinal_rank_pairs_v4_10.csv",
        ordinal_rows,
        list(ordinal_rows[0].keys()) if ordinal_rows else ["block"],
    )
    write_csv(
        out / "c3_pseudotarget_scores_v4_10.csv",
        pseudo_rows,
        list(pseudo_rows[0].keys()) if pseudo_rows else ["block"],
    )
    write_csv(
        out / "c3_studentized_envelope_v4_10.csv",
        envelope_rows,
        list(envelope_rows[0].keys()) if envelope_rows else ["block"],
    )
    write_csv(
        out / "c3_global_location_v4_10.csv",
        global_rows,
        list(global_rows[0].keys()) if global_rows else ["block"],
    )
    write_csv(out / "c5_path_diagnostics.csv", c5_rows, list(c5_rows[0].keys()) if c5_rows else ["block"])
    write_csv(
        out / "capture_classification.csv",
        classification_rows,
        list(classification_rows[0].keys()) if classification_rows else ["run_id"],
    )
    write_csv(out / "run_ledger.csv", ledger_rows, ["run_id", "condition", "status", "run_dir"])
    write_csv(out / "control_effects_v4_10.csv", control_rows, ["block", "metric", "shift_ns"])
    (out / "path_external_proofs.json").write_text(
        json.dumps({"runs": path_external_all, "endpoint_rows": path_external_rows}, indent=2) + "\n"
    )
    if path_external_rows:
        write_csv(out / "path_external_proofs.csv", path_external_rows, list(path_external_rows[0].keys()))

    summary = {"schema": V410_SCHEMA, "paired": paired, "c3_contract_sha256": c3_contract_sha()}
    (out / "v4_10_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()

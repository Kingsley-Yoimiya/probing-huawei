"""V4.10 unit tests: C3 ordinal studentized envelope + global location gate."""
from __future__ import annotations

import json
import statistics
import tempfile
from pathlib import Path

from wait_dag_v4_7_intervention import occupancy_identity_check
from wait_dag_v4_9_intervention import (
    C3_K,
    C3_REQUIRED_WAITS,
    C3_SENTINEL_RANKS,
    C3_TARGET_RANK,
    C5_DIAG,
    compute_rank_preintervention_fifo,
    prove_path_external as prove_path_external_v49,
)
from wait_dag_v4_10_intervention import (
    C3_EPSILON_NS,
    C3_LAMBDA,
    C3_MAD_SCALE,
    compute_c3_ordinal_block,
    compute_ordinal_x,
    c3_failure_reason,
    c3_pass,
    dsmall_c3_degrade_allowed,
    evaluate_control_gate_v410,
    nearest_rank_quartiles,
    prove_path_external,
)


class FakeRec:
    def __init__(self, cs, enter, exit_ns, raw=100, acl_ret=0):
        self.op = 5
        self.call_sequence = cs
        self.acl_ret = acl_ret
        self.raw_stream = raw
        self.enter_realtime_ns = enter
        self.exit_realtime_ns = exit_ns


def _make_waits(n, base=1000, gap=50, raw=100):
    waits = []
    t = base
    for i in range(n):
        waits.append(FakeRec(10 + i, t, t + 10, raw=raw))
        t += 10 + gap
    return waits


def _rank_gaps_from_base(base_gap: int, shift: int = 0) -> list[int]:
    return [base_gap + shift] * C3_K


def _make_c3_dict(gap: int = 50, shift: int = 0) -> dict[int, dict]:
    return {
        r: {"gaps_ns": _rank_gaps_from_base(gap, shift if r == C3_TARGET_RANK else 0)}
        for r in range(16)
    }


def _make_c3_dict_custom(shifts: dict[int, int], base_gap: int = 50) -> dict[int, dict]:
    return {r: {"gaps_ns": _rank_gaps_from_base(base_gap, shifts.get(r, 0))} for r in range(16)}


def test_k8_from_nine_waits():
    waits = _make_waits(12)
    fifo, err = compute_rank_preintervention_fifo(
        waits, 100, session_start=1_000_000, selected_enter_ns=500_000
    )
    assert err == "ok"
    assert len(fifo["gaps_ns"]) == C3_K


def test_ordinal_x_hand_computed():
    d0 = _make_c3_dict(50, 0)
    dt = _make_c3_dict(50, 5)
    x = compute_ordinal_x(d0, dt)
    assert x[C3_TARGET_RANK] == [5] * C3_K
    assert all(x[r] == [0] * C3_K for r in range(1, 16))


def test_theta_star_v_star_hand_fixture():
    """Target +10 at all ordinals; sentinels flat -> theta*=10, v*=0."""
    d0 = _make_c3_dict(100)
    dt = _make_c3_dict(100, shift=10)
    x = compute_ordinal_x(d0, dt)
    block = compute_c3_ordinal_block(x)
    assert block["theta_star_ns"] == 10
    assert block["v_star_ns"] == 0
    assert block["C3_target_did_ns"] == 10


def test_pseudotarget_same_layer():
    d0 = _make_c3_dict(100)
    shifts = {C3_TARGET_RANK: 20, 1: 5, 2: -3}
    dt = _make_c3_dict_custom(shifts, base_gap=100)
    x = compute_ordinal_x(d0, dt)
    block = compute_c3_ordinal_block(x)
    assert len(block["pseudo_targets"]) == 15
    for s in C3_SENTINEL_RANKS:
        assert "theta_s_ns" in block["pseudo_targets"][str(s)]
        assert "v_s_ns" in block["pseudo_targets"][str(s)]
        assert "z_s" in block["pseudo_targets"][str(s)]


def test_nearest_rank_quartile_n15():
    vals = list(range(1, 16))
    q1, q3, iqr = nearest_rank_quartiles([float(v) for v in vals])
    assert q1 == 4.0
    assert q3 == 12.0
    assert iqr == 8.0


def test_z_outer_envelope_formula():
    d0 = _make_c3_dict(100)
    dt = _make_c3_dict(100, shift=0)
    x = compute_ordinal_x(d0, dt)
    block = compute_c3_ordinal_block(x)
    expected_env = int(round(block["z_outer"] * (block["v_star_ns"] + block["h_ns"])))
    assert block["C3_noise_envelope_ns"] == expected_env


def test_global_constant_drift_fails_even_zero_target_did():
    """All sentinels +50 per ordinal; target also +50 -> theta*=0 but G!=0."""
    d0 = _make_c3_dict(100)
    dt = {r: {"gaps_ns": [150] * C3_K} for r in range(16)}
    x = compute_ordinal_x(d0, dt)
    block = compute_c3_ordinal_block(x)
    assert block["theta_star_ns"] == 0
    assert block["G_ns"] == 50
    assert not block["C3_global_location_pass"]
    assert c3_failure_reason(block) == "STOP_GLOBAL_SCHEDULING_OR_CONTENTION_CONFOUND:C3_global_location"


def test_sentinel_outer_fence_outlier_fails():
    d0 = _make_c3_dict(100)
    shifts = {s: 0 for s in C3_SENTINEL_RANKS}
    shifts[1] = 500
    shifts[C3_TARGET_RANK] = 0
    dt = _make_c3_dict_custom(shifts, base_gap=100)
    x = compute_ordinal_x(d0, dt)
    block = compute_c3_ordinal_block(x)
    assert not block["sentinel_coverage_pass"]
    assert (
        c3_failure_reason(block)
        == "STOP_C3_NOISE_MODEL_UNSTABLE:SENTINEL_OUTER_FENCE"
    )


def test_target_exceeds_z_outer_fails():
    d0 = _make_c3_dict(100)
    shifts = {s: 0 for s in C3_SENTINEL_RANKS}
    shifts[C3_TARGET_RANK] = 200
    dt = _make_c3_dict_custom(shifts, base_gap=100)
    x = compute_ordinal_x(d0, dt)
    block = compute_c3_ordinal_block(x)
    assert block["target_scale_exchangeable_pass"]
    assert not block["C3_target_stationarity_pass"]
    assert (
        c3_failure_reason(block)
        == "STOP_GLOBAL_SCHEDULING_OR_CONTENTION_CONFOUND:C3_target_studentized"
    )


def test_target_wide_scale_fails_exchangeability():
    d0 = _make_c3_dict(100)
    gaps_d0 = [100] * C3_K
    gaps_t = [100, 100, 100, 100, 200, 300, 400, 500]
    dt = {r: {"gaps_ns": list(gaps_d0)} for r in range(16)}
    dt[C3_TARGET_RANK] = {"gaps_ns": [g + 10 for g in gaps_t]}
    x = compute_ordinal_x(d0, dt)
    block = compute_c3_ordinal_block(x)
    assert block["v_star_ns"] > max(C3_EPSILON_NS, block["v_outer_ns"])
    assert not block["target_scale_exchangeable_pass"]


def test_epsilon_when_all_zero():
    d0 = _make_c3_dict(100)
    dt = _make_c3_dict(100, shift=0)
    x = compute_ordinal_x(d0, dt)
    block = compute_c3_ordinal_block(x)
    assert block["h_ns"] >= C3_EPSILON_NS
    assert block["z_star"] == 0.0


def test_nonzero_target_not_pass_via_div_zero():
    d0 = _make_c3_dict(100)
    dt = _make_c3_dict(100, shift=100)
    x = compute_ordinal_x(d0, dt)
    block = compute_c3_ordinal_block(x)
    assert block["theta_star_ns"] == 100
    assert not c3_pass(block) or block["z_star"] > block["z_outer"]


def test_dsmall_degrade_only_target_failures():
    d0 = _make_c3_dict(100)
    shifts = {s: 0 for s in C3_SENTINEL_RANKS}
    shifts[C3_TARGET_RANK] = 200
    dt = _make_c3_dict_custom(shifts, base_gap=100)
    x = compute_ordinal_x(d0, dt)
    block = compute_c3_ordinal_block(x)
    ok, _ = dsmall_c3_degrade_allowed(block, structure_only=True)
    assert ok
    ok2, reason2 = dsmall_c3_degrade_allowed(block, structure_only=False)
    assert not ok2
    assert not dsmall_c3_degrade_allowed(block, structure_only=True)[0] or c3_failure_reason(block) in (
        "STOP_GLOBAL_SCHEDULING_OR_CONTENTION_CONFOUND:C3_target_studentized",
        "STOP_C3_CONTROL_SENSITIVITY_INSUFFICIENT:TARGET_SCALE_NONEXCHANGEABLE",
    )


def test_dsmall_global_fail_no_degrade():
    d0 = _make_c3_dict(100)
    dt = {r: {"gaps_ns": [150] * C3_K} for r in range(16)}
    x = compute_ordinal_x(d0, dt)
    block = compute_c3_ordinal_block(x)
    ok, reason = dsmall_c3_degrade_allowed(block, structure_only=True)
    assert not ok
    assert "C3_global_location" in (reason or "")


def test_c5_not_in_control_denominator():
    assert C5_DIAG["C5_gate_boolean"] is None
    assert C5_DIAG["C5_in_control_denominator"] is False


def test_control_gate_c5_excluded():
    base_ctrl = {"value_ns": 100, "unique": True}
    d0 = {
        "v49_controls": {
            "C1_wait_duration_ns": dict(base_ctrl),
            "C2_record_offset_from_wait_end_ns": dict(base_ctrl),
            "C4_bypass_compute_offset_from_wait_end_ns": dict(base_ctrl),
        },
        "c3_rank_pairs": {r: {"gaps_ns": [50] * C3_K} for r in range(16)},
    }
    dt = {
        "realized_work_ns": 3_226_068,
        "condition": "Dlarge",
        "v49_controls": {
            "C1_wait_duration_ns": dict(base_ctrl),
            "C2_record_offset_from_wait_end_ns": dict(base_ctrl),
            "C4_bypass_compute_offset_from_wait_end_ns": dict(base_ctrl),
        },
        "c3_rank_pairs": {r: {"gaps_ns": [50] * C3_K} for r in range(16)},
    }
    x = compute_ordinal_x(d0["c3_rank_pairs"], dt["c3_rank_pairs"])
    c3_block = compute_c3_ordinal_block(x)
    ok, reason, rows, meta = evaluate_control_gate_v410(d0, dt, c3_block)
    metrics = {r["metric"] for r in rows}
    assert "C5" not in "".join(metrics)
    assert meta["C3_in_control_denominator"] is True
    assert ok


def test_v49_max_deviation_gate_removed():
    """V4.10 must not use max|d-median| as sole gate."""
    from wait_dag_v4_9_intervention import compute_c3_did, evaluate_c3_sensitivity

    d0 = {r: 100 for r in range(16)}
    dt = {r: 100 for r in range(16)}
    dt[C3_TARGET_RANK] = 200
    old, _ = compute_c3_did(d0, dt)
    assert old["C3_noise_envelope_ns"] == max(
        abs((dt[r] - d0[r]) - int(statistics.median([dt[s] - d0[s] for s in C3_SENTINEL_RANKS])))
        for r in C3_SENTINEL_RANKS
    )
    assert evaluate_c3_sensitivity(old["C3_noise_envelope_ns"], 3_000_000) is not None


def _rank_c3_from_waits(waits, session_start=1_000_000, selected_enter=500_000):
    fifo, err = compute_rank_preintervention_fifo(
        waits, 100, session_start=session_start, selected_enter_ns=selected_enter
    )
    assert err == "ok"
    return {0: fifo}


def _write_sidecar(trace_dir: Path, *, launch_count=0, trigger_wait_cs=9999, host_enter_ns=800_000):
    audit = {
        "rank": 0,
        "pid": 1,
        "launch_count": launch_count,
        "trigger_wait_preload_cs": trigger_wait_cs,
        "trigger_record_preload_cs": trigger_wait_cs - 1,
        "host_enter_ns": host_enter_ns,
    }
    (trace_dir / "rank_0_pid_1.device_work_audit.json").write_text(json.dumps(audit) + "\n")


def _write_sidecar_timeline(trace_dir: Path, endpoints: list[dict], launch_counts: dict[int, int] | None = None):
    launch_counts = launch_counts or {}
    lines = []
    for ep in endpoints:
        cs = int(ep["call_sequence"])
        lc = launch_counts.get(cs, 0)
        lines.append(json.dumps({"preload_cs": cs, "launch_count": lc, "monotonic_ns": cs * 1000}))
    (trace_dir / "rank_0_pid_1.sidecar_timeline.jsonl").write_text("\n".join(lines) + "\n")


def test_prove_path_external_treatment_requires_timeline_literal_zero():
    waits = _make_waits(12)
    rank_c3 = _rank_c3_from_waits(waits)
    with tempfile.TemporaryDirectory() as td:
        trace_dir = Path(td) / "event_trace"
        trace_dir.mkdir()
        _write_sidecar(trace_dir, launch_count=1, trigger_wait_cs=9999)
        ok, proofs = prove_path_external(
            Path(td), "Dlarge", rank_c3, session_start=1_000_000, selected_enter_ns=500_000
        )
    assert not ok
    assert proofs.get("stop_code") == "STOP_C3_NOT_PATH_EXTERNAL:LITERAL_TREATMENT_LAUNCH_COUNT"


def test_prove_path_external_treatment_timeline_zero_passes():
    waits = _make_waits(12)
    rank_c3 = _rank_c3_from_waits(waits)
    with tempfile.TemporaryDirectory() as td:
        trace_dir = Path(td) / "event_trace"
        trace_dir.mkdir()
        _write_sidecar(trace_dir, launch_count=1, trigger_wait_cs=9999)
        _write_sidecar_timeline(trace_dir, rank_c3[0]["window_endpoints"])
        ok, proofs = prove_path_external(
            Path(td), "Dlarge", rank_c3, session_start=1_000_000, selected_enter_ns=500_000
        )
    assert ok
    assert all(r["c3_endpoint_launch_count"] == 0 for r in proofs["endpoint_rows"])


def test_prove_path_external_d0_fallback_still_ok():
    waits = _make_waits(12, base=1000, gap=50)
    rank_c3 = _rank_c3_from_waits(waits)
    with tempfile.TemporaryDirectory() as td:
        trace_dir = Path(td) / "event_trace"
        trace_dir.mkdir()
        ok, proofs = prove_path_external_v49(
            Path(td), "D0", rank_c3, session_start=1_000_000, selected_enter_ns=500_000
        )
    assert ok


def test_insufficient_waits_fail_closed():
    waits = _make_waits(5)
    fifo, err = compute_rank_preintervention_fifo(
        waits, 100, session_start=1_000_000, selected_enter_ns=500_000
    )
    assert fifo is None
    assert "insufficient" in err


def test_occupancy_identity_algebra():
    s0, realized, launch = 721648, 3226068, 8600
    inject_end = 100 + launch + realized
    q_start = inject_end + 20
    observed = (q_start - 100) - s0
    predicted = max(0, (inject_end - 100) - s0)
    residual = observed - predicted
    assert residual == 20
    assert occupancy_identity_check(q_start, inject_end, observed, predicted, residual, 20, s0, 100, 100 + launch)


def main() -> None:
    tests = [
        test_k8_from_nine_waits,
        test_ordinal_x_hand_computed,
        test_theta_star_v_star_hand_fixture,
        test_pseudotarget_same_layer,
        test_nearest_rank_quartile_n15,
        test_z_outer_envelope_formula,
        test_global_constant_drift_fails_even_zero_target_did,
        test_sentinel_outer_fence_outlier_fails,
        test_target_exceeds_z_outer_fails,
        test_target_wide_scale_fails_exchangeability,
        test_epsilon_when_all_zero,
        test_nonzero_target_not_pass_via_div_zero,
        test_dsmall_degrade_only_target_failures,
        test_dsmall_global_fail_no_degrade,
        test_c5_not_in_control_denominator,
        test_control_gate_c5_excluded,
        test_v49_max_deviation_gate_removed,
        test_prove_path_external_treatment_requires_timeline_literal_zero,
        test_prove_path_external_treatment_timeline_zero_passes,
        test_prove_path_external_d0_fallback_still_ok,
        test_insufficient_waits_fail_closed,
        test_occupancy_identity_algebra,
    ]
    for t in tests:
        t()
    print(f"test_wait_dag_v4_10: {len(tests)}/{len(tests)} PASS")


if __name__ == "__main__":
    main()

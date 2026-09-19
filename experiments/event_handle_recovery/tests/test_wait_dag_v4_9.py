"""V4.9 unit tests: path-external C3 DID + C5 diagnostic-only."""
from __future__ import annotations

import json
import statistics
import tempfile
from pathlib import Path

from wait_dag_v4_7_intervention import build_pair_base_key, occupancy_identity_check, REL_TOL
from wait_dag_v4_9_intervention import (
    C3_K,
    C3_REQUIRED_WAITS,
    C3_SENTINEL_RANKS,
    C3_TARGET_RANK,
    C5_DIAG,
    compute_c3_did,
    compute_rank_preintervention_fifo,
    evaluate_c3_sensitivity,
    evaluate_c3_stationarity,
    evaluate_control_gate_v49,
    load_sidecar_timeline,
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


def test_k8_from_nine_waits():
    waits = _make_waits(12)
    fifo, err = compute_rank_preintervention_fifo(
        waits, 100, session_start=1_000_000, selected_enter_ns=500_000
    )
    assert err == "ok"
    assert len(fifo["gaps_ns"]) == C3_K
    assert fifo["median_gap_ns"] == 50


def test_insufficient_waits_fail_closed():
    waits = _make_waits(5)
    fifo, err = compute_rank_preintervention_fifo(
        waits, 100, session_start=1_000_000, selected_enter_ns=500_000
    )
    assert fifo is None
    assert "insufficient" in err


def test_did_and_envelope_hand_computed():
    d0 = {r: 100 + r for r in range(16)}
    dt = {r: 100 + r + (5 if r == C3_TARGET_RANK else 0) for r in range(16)}
    block, _ = compute_c3_did(d0, dt)
    sentinel_shifts = [dt[r] - d0[r] for r in C3_SENTINEL_RANKS]
    center = int(statistics.median(sentinel_shifts))
    expected_c3 = (dt[C3_TARGET_RANK] - d0[C3_TARGET_RANK]) - center
    noise = max(abs((dt[r] - d0[r]) - center) for r in C3_SENTINEL_RANKS)
    assert block["C3_preintervention_fifo_did_ns"] == expected_c3
    assert block["C3_noise_envelope_ns"] == noise


def test_target_exceeds_envelope_fails_stationarity():
    d0 = {r: 100 for r in range(16)}
    dt = {r: 100 for r in range(16)}
    dt[C3_TARGET_RANK] = 200
    dt[1] = 105
    block, _ = compute_c3_did(d0, dt)
    assert not evaluate_c3_stationarity(
        block["C3_preintervention_fifo_did_ns"], block["C3_noise_envelope_ns"]
    )


def test_causal_eligible_sensitivity_fail():
    noise = 1_000_000
    predicted = 3_000_000
    assert evaluate_c3_sensitivity(noise, predicted) is False


def test_dsmall_predicted_zero_sensitivity_null():
    assert evaluate_c3_sensitivity(1000, 0) is None
    assert evaluate_c3_stationarity(0, 1000) is True


def test_value_driven_sentinel_exclusion_fails_closed():
    """Post-hoc sentinel removal changes C3 DID/noise; canonical set must stay fixed."""
    d0 = {r: 100 for r in range(16)}
    dt = {r: 100 for r in range(16)}
    dt[C3_TARGET_RANK] = 145
    dt[1] = 140
    dt[2] = 125
    block_full, _ = compute_c3_did(d0, dt)
    shifts = {r: int(dt[r]) - int(d0[r]) for r in range(16)}
    assert not evaluate_c3_stationarity(
        block_full["C3_preintervention_fifo_did_ns"], block_full["C3_noise_envelope_ns"]
    )
    hacked_sentinels = [r for r in C3_SENTINEL_RANKS if shifts[r] == max(shifts[r] for r in C3_SENTINEL_RANKS)]
    assert len(hacked_sentinels) < len(C3_SENTINEL_RANKS)
    hacked_center = int(statistics.median([shifts[r] for r in hacked_sentinels]))
    hacked_noise = max(abs(shifts[r] - hacked_center) for r in hacked_sentinels)
    hacked_c3 = shifts[C3_TARGET_RANK] - hacked_center
    assert hacked_center != block_full["sentinel_center_ns"]
    assert hacked_noise != block_full["C3_noise_envelope_ns"]
    assert hacked_c3 != block_full["C3_preintervention_fifo_did_ns"]


def test_c5_not_in_control_denominator():
    assert C5_DIAG["C5_gate_boolean"] is None
    assert C5_DIAG["C5_in_control_denominator"] is False


def test_c5_diagnostic_fixture_078():
    wait_end = 100
    s0 = 721648
    realized = 3_226_068
    c5_shift = 2_517_276
    c5_d0_total = s0
    c5_dt_total = s0 + c5_shift
    ratio = c5_shift / realized
    assert 0.75 < ratio < 0.82
    assert C5_DIAG["C5_gate_boolean"] is None
    assert C5_DIAG["C5_in_control_denominator"] is False
    assert abs(ratio - 0.780) < 0.01


def test_control_gate_only_c1_c2_c3_c4():
    base_ctrl = {"value_ns": 100, "unique": True}
    d0 = {
        "v49_controls": {
            "C1_wait_duration_ns": dict(base_ctrl),
            "C2_record_offset_from_wait_end_ns": dict(base_ctrl),
            "C4_bypass_compute_offset_from_wait_end_ns": dict(base_ctrl),
        },
        "c3_rank_pairs": {r: {"median_gap_ns": 100} for r in range(16)},
    }
    dt = {
        "realized_work_ns": 3_226_068,
        "predicted_unmasked_occupancy_ns": 3_000_000,
        "causal_eligibility": "CAUSAL_ELIGIBLE",
        "v49_controls": {
            "C1_wait_duration_ns": dict(base_ctrl),
            "C2_record_offset_from_wait_end_ns": dict(base_ctrl),
            "C4_bypass_compute_offset_from_wait_end_ns": dict(base_ctrl),
        },
        "c3_rank_pairs": {r: {"median_gap_ns": 100} for r in range(16)},
    }
    c3_block = {
        "C3_preintervention_fifo_did_ns": 0,
        "C3_noise_envelope_ns": 100,
        "per_rank_shift_ns": {r: 0 for r in range(16)},
    }
    ok, reason, rows = evaluate_control_gate_v49(d0, dt, c3_block)
    metrics = {r["metric"] for r in rows}
    assert "C5" not in "".join(metrics)
    assert ok is True


def test_v48_absolute_bypass_still_fails():
    wait_end = 100
    d0 = {
        "realized_work_ns": 3_000_000,
        "v49_controls": {
            "C1_wait_duration_ns": {"value_ns": 10, "unique": True},
            "C2_record_offset_from_wait_end_ns": {"value_ns": 5, "unique": True},
            "C4_bypass_compute_offset_from_wait_end_ns": {
                "value_ns": 50_000_000_000_000,
                "unique": True,
            },
        },
    }
    dt = {
        "realized_work_ns": 3_000_000,
        "v49_controls": {
            "C1_wait_duration_ns": {"value_ns": 10, "unique": True},
            "C2_record_offset_from_wait_end_ns": {"value_ns": 5, "unique": True},
            "C4_bypass_compute_offset_from_wait_end_ns": {
                "value_ns": 60_000_000_000_000,
                "unique": True,
            },
        },
    }
    ok, reason, _ = evaluate_control_gate_v49(d0, dt, None)
    assert ok is False


def test_occupancy_identity_algebra():
    s0, realized, launch = 721648, 3226068, 8600
    inject_end = 100 + launch + realized
    q_start = inject_end + 20
    observed = (q_start - 100) - s0
    predicted = max(0, (inject_end - 100) - s0)
    residual = observed - predicted
    assert residual == 20
    assert occupancy_identity_check(q_start, inject_end, observed, predicted, residual, 20, s0, 100, 100 + launch)


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


def test_prove_path_external_four_classes_pass():
    waits = _make_waits(12, base=1000, gap=50)
    rank_c3 = _rank_c3_from_waits(waits)
    with tempfile.TemporaryDirectory() as td:
        trace_dir = Path(td) / "event_trace"
        trace_dir.mkdir()
        _write_sidecar(trace_dir, launch_count=0, trigger_wait_cs=9999, host_enter_ns=400_000)
        _write_sidecar_timeline(trace_dir, rank_c3[0]["window_endpoints"])
        ok, proofs = prove_path_external(
            Path(td), "D0", rank_c3, session_start=1_000_000, selected_enter_ns=500_000
        )
    assert ok
    assert proofs["all_endpoints_before_session"]
    assert proofs["sidecar_launch_count_zero_at_c3_endpoints"]
    assert proofs["not_on_wait_inject_comm_path"]
    assert proofs["endpoint_rows"]
    assert all(r["c3_endpoint_launch_count"] == 0 for r in proofs["endpoint_rows"])


def test_prove_path_external_fails_d0_launch_count_nonzero():
    waits = _make_waits(12)
    rank_c3 = _rank_c3_from_waits(waits)
    endpoints = rank_c3[0]["window_endpoints"]
    bad_cs = int(endpoints[0]["call_sequence"])
    with tempfile.TemporaryDirectory() as td:
        trace_dir = Path(td) / "event_trace"
        trace_dir.mkdir()
        _write_sidecar(trace_dir, launch_count=1)
        _write_sidecar_timeline(trace_dir, endpoints, {bad_cs: 1})
        ok, proofs = prove_path_external(
            Path(td), "D0", rank_c3, session_start=1_000_000, selected_enter_ns=500_000
        )
    assert not ok
    assert not proofs["sidecar_launch_count_zero_at_c3_endpoints"]


def test_prove_path_external_fails_generation_path_membership():
    waits = _make_waits(12, base=1000, gap=50)
    rank_c3 = _rank_c3_from_waits(waits)
    cs_on_path = rank_c3[0]["window_endpoints"][0]["call_sequence"]
    with tempfile.TemporaryDirectory() as td:
        trace_dir = Path(td) / "event_trace"
        trace_dir.mkdir()
        _write_sidecar(
            trace_dir,
            launch_count=0,
            trigger_wait_cs=cs_on_path,
            host_enter_ns=400_000,
        )
        _write_sidecar_timeline(trace_dir, rank_c3[0]["window_endpoints"])
        ok, proofs = prove_path_external(
            Path(td), "Dlarge", rank_c3, session_start=1_000_000, selected_enter_ns=500_000
        )
    assert not ok
    assert not proofs["not_on_wait_inject_comm_path"]


def test_prove_path_external_fails_exit_after_session():
    rank_c3 = {
        0: {
            "window_endpoints": [
                {"call_sequence": 10 + i, "enter_ns": 900_000 + i * 10, "exit_ns": 1_100_000 + i * 10}
                for i in range(C3_REQUIRED_WAITS)
            ]
        }
    }
    with tempfile.TemporaryDirectory() as td:
        trace_dir = Path(td) / "event_trace"
        trace_dir.mkdir()
        _write_sidecar(trace_dir, launch_count=0)
        _write_sidecar_timeline(trace_dir, rank_c3[0]["window_endpoints"])
        ok, proofs = prove_path_external(
            Path(td), "D0", rank_c3, session_start=1_000_000, selected_enter_ns=2_000_000
        )
    assert not ok
    assert not proofs["all_endpoints_before_session"]


def test_prove_path_external_treatment_rank0_timeline_launch_count_zero():
    waits = _make_waits(12)
    rank_c3 = _rank_c3_from_waits(waits)
    max_cs = max(e["call_sequence"] for e in rank_c3[0]["window_endpoints"])
    inject_cs = max_cs + 5
    with tempfile.TemporaryDirectory() as td:
        trace_dir = Path(td) / "event_trace"
        trace_dir.mkdir()
        _write_sidecar(trace_dir, launch_count=1, trigger_wait_cs=inject_cs, host_enter_ns=1)
        timeline = {int(e["call_sequence"]): 0 for e in rank_c3[0]["window_endpoints"]}
        timeline[inject_cs] = 1
        _write_sidecar_timeline(trace_dir, rank_c3[0]["window_endpoints"], timeline)
        ok, proofs = prove_path_external(
            Path(td), "Dlarge", rank_c3, session_start=1_000_000, selected_enter_ns=500_000
        )
    assert ok
    assert proofs["sidecar_launch_count_zero_at_c3_endpoints"]
    assert proofs["rank_proofs"][0]["c3_endpoint_launch_count"] == 0
    assert all(r["c3_endpoint_launch_count"] == 0 for r in proofs["endpoint_rows"])


def test_prove_path_external_d0_without_timeline_passes():
    waits = _make_waits(12, base=1000, gap=50)
    rank_c3 = _rank_c3_from_waits(waits)
    with tempfile.TemporaryDirectory() as td:
        trace_dir = Path(td) / "event_trace"
        trace_dir.mkdir()
        ok, proofs = prove_path_external(
            Path(td), "D0", rank_c3, session_start=1_000_000, selected_enter_ns=500_000
        )
    assert ok
    assert proofs["sidecar_launch_count_zero_at_c3_endpoints"]


def test_prove_path_external_fails_missing_sidecar_timeline():
    waits = _make_waits(12)
    rank_c3 = _rank_c3_from_waits(waits)
    with tempfile.TemporaryDirectory() as td:
        trace_dir = Path(td) / "event_trace"
        trace_dir.mkdir()
        _write_sidecar(trace_dir, launch_count=1)
        ok, proofs = prove_path_external(
            Path(td), "Dlarge", rank_c3, session_start=1_000_000, selected_enter_ns=500_000
        )
    assert not ok
    assert not proofs["sidecar_launch_count_zero_at_c3_endpoints"]


def test_evaluate_control_gate_missing_c3_fail_closed():
    base_ctrl = {"value_ns": 100, "unique": True}
    d0 = {
        "v49_controls": {
            "C1_wait_duration_ns": dict(base_ctrl),
            "C2_record_offset_from_wait_end_ns": dict(base_ctrl),
            "C4_bypass_compute_offset_from_wait_end_ns": dict(base_ctrl),
        },
    }
    dt = {
        "realized_work_ns": 3_000_000,
        "v49_controls": {
            "C1_wait_duration_ns": dict(base_ctrl),
            "C2_record_offset_from_wait_end_ns": dict(base_ctrl),
            "C4_bypass_compute_offset_from_wait_end_ns": dict(base_ctrl),
        },
    }
    ok, reason, _ = evaluate_control_gate_v49(d0, dt, None)
    assert ok is False
    assert "STOP_CONTROL_UNAVAILABLE" in reason


def main() -> None:
    test_k8_from_nine_waits()
    test_insufficient_waits_fail_closed()
    test_did_and_envelope_hand_computed()
    test_target_exceeds_envelope_fails_stationarity()
    test_value_driven_sentinel_exclusion_fails_closed()
    test_causal_eligible_sensitivity_fail()
    test_dsmall_predicted_zero_sensitivity_null()
    test_c5_not_in_control_denominator()
    test_c5_diagnostic_fixture_078()
    test_control_gate_only_c1_c2_c3_c4()
    test_v48_absolute_bypass_still_fails()
    test_occupancy_identity_algebra()
    test_prove_path_external_four_classes_pass()
    test_prove_path_external_fails_d0_launch_count_nonzero()
    test_prove_path_external_fails_generation_path_membership()
    test_prove_path_external_fails_exit_after_session()
    test_prove_path_external_treatment_rank0_timeline_launch_count_zero()
    test_prove_path_external_d0_without_timeline_passes()
    test_prove_path_external_fails_missing_sidecar_timeline()
    test_evaluate_control_gate_missing_c3_fail_closed()
    print("test_wait_dag_v4_9: all PASS")


if __name__ == "__main__":
    main()

"""V4.7 unit tests: estimand algebra + pair_base_key hygiene."""
from __future__ import annotations

import json

from wait_dag_v4_7_intervention import (
    build_pair_base_key,
    causal_eligibility,
    evaluate_causal_closure,
    occupancy_identity_check,
    paired_effects_v4_7,
    REL_TOL,
)


def test_pair_base_key_no_run_literals():
    key = build_pair_base_key()
    blob = json.dumps(key).lower()
    for forbidden in ("rowid", "streamid", "connectionid", "ordinal", "preload", "tid", "cs"):
        assert forbidden not in blob


def test_v46_dlarge_algebra_identity():
    """V4.6 Dlarge numbers — formula check only, not V4.7 PASS."""
    s0 = 721648
    launch_offset = 8600
    realized = 3226068
    inject_end_offset = launch_offset + realized
    predicted = max(0, inject_end_offset - s0)
    observed = 2513040
    post_inject_gap = 20
    residual = observed - predicted
    assert predicted == 2513020
    assert residual == post_inject_gap == 20
    assert occupancy_identity_check(
        inject_end_offset + post_inject_gap,
        inject_end_offset,
        observed,
        predicted,
        residual,
        post_inject_gap,
        s0,
        launch_offset,
        launch_offset,
    )


def test_dsmall_structure_only_masked():
    elig, reason = causal_eligibility(0, 100, 197304)
    assert elig == "STRUCTURE_ONLY_NOT_CAUSAL"
    assert "MASKED_BY_D0_SLACK" in reason


def test_dsmall_structure_only_large_gap():
    elig, reason = causal_eligibility(1000, 634568, 197304)
    assert elig == "STRUCTURE_ONLY_NOT_CAUSAL"
    assert "POST_INJECT_GAP_TOO_LARGE" in reason


def test_causal_closure_relative_only():
    ok, _ = evaluate_causal_closure(2513040, 2513020, 20, 20, 3226068)
    assert ok is True
    ok2, reason = evaluate_causal_closure(100000, 2513020, 500000, 700000, 3226068)
    assert ok2 is not True
    assert reason


def test_paired_mock_dlarge_closure():
    class T:
        def __init__(self, start, end):
            self.start_ns = start
            self.end_ns = end
            self.rowid = id(self)

    wait0 = T(0, 100)
    q0 = T(100 + 721648, 200 + 721648)
    waitT = T(0, 100)
    inject = T(100 + 8600, 100 + 8600 + 3226068)
    qT = T(inject.end_ns + 20, inject.end_ns + 100)

    d0 = {
        "run_id": "d0",
        "status": "OK",
        "normalized_structure_key": json.dumps(build_pair_base_key(), sort_keys=True),
        "identity": {"identity_method": "UNIQUE_REVERSE_WAIT_TO_TARGET_COMM"},
        "wait_task": wait0,
        "comm_entry": q0,
        "inject_task": None,
        "nodes": [
            {"node": "wait_task", "end_offset_from_upstream_kernel_end_ns": 0},
            {"node": "comm_entry", "start_offset_from_upstream_kernel_end_ns": 721648},
            {"node": "record_task", "end_offset_from_upstream_kernel_end_ns": 0},
        ],
        "pre_wait_p_rowid": 1,
    }
    dt = {
        "run_id": "dt",
        "status": "OK",
        "normalized_structure_key": json.dumps(build_pair_base_key(), sort_keys=True),
        "identity": {
            "identity_method": "LAYERED_TREATMENT_GENERATION_INJECT_COMM_PROJECTION",
            "requested_iters": 5382,
            "reverse_c8_candidate_count": 0,
        },
        "wait_task": waitT,
        "comm_entry": qT,
        "inject_task": inject,
        "realized_work_ns": 3226068,
        "nodes": [
            {"node": "wait_task", "end_offset_from_upstream_kernel_end_ns": 0},
            {"node": "injected_kernel", "end_offset_from_upstream_kernel_end_ns": 3226068},
            {"node": "comm_entry", "start_offset_from_upstream_kernel_end_ns": 3226068 + 20},
            {"node": "record_task", "end_offset_from_upstream_kernel_end_ns": 0},
        ],
        "pre_wait_p_rowid": 1,
    }
    row = paired_effects_v4_7(d0, dt, "b1_dlarge")
    assert row["occupancy_identity_ok"]
    assert row["local_causal_gate_pass"] is True
    assert row["occupancy_residual_ns"] == row["post_inject_gap_ns"] == 20


def main() -> None:
    test_pair_base_key_no_run_literals()
    test_v46_dlarge_algebra_identity()
    test_dsmall_structure_only_masked()
    test_dsmall_structure_only_large_gap()
    test_causal_closure_relative_only()
    test_paired_mock_dlarge_closure()
    print("test_wait_dag_v4_7: all PASS")


if __name__ == "__main__":
    main()

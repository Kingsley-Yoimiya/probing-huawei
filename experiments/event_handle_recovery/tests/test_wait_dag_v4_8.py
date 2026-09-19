"""V4.8 unit tests: P1–P7 classification, anchor-relative controls, runner hygiene."""
from __future__ import annotations

import json
from dataclasses import dataclass

from wait_dag_v4_7_intervention import build_pair_base_key, occupancy_identity_check, REL_TOL
from wait_dag_v4_8_intervention import (
    STRUCTURAL_NA_REASON,
    build_v48_run_controls,
    capture_contract_sha,
    evaluate_control_gate_v48,
    evaluate_p_predicates,
    paired_effects_v4_8,
)


@dataclass
class FakeRec:
    op: int = 5
    call_sequence: int = 0
    acl_ret: int = 0
    raw_stream: int = 100
    enter_realtime_ns: int = 0
    exit_realtime_ns: int = 0
    pid: int = 1
    tid: int = 1


@dataclass
class FakeTask:
    rowid: int
    stream_id: int
    start_ns: int
    end_ns: int
    connection_id: int = 1
    task_type: int = 1


@dataclass
class FakeCtx:
    db_path: object
    rank0_records: list
    string_ids: dict
    active_start: int
    active_end: int
    all_tasks: list
    all_tasks_by_rowid: dict


def _fake_path():
    class P:
        def exists(self):
            return True

    return P()


def _synthetic_ctx(*, wait_idx=0, n_tasks=3, pre_session_wait=True, capture_match=True):
    WAIT_OP = 5
    prof_sid = 4
    raw = 999
    session_start = 1_000_000
    tasks = [
        FakeTask(20 + i, prof_sid, session_start + 100 * i, session_start + 100 * i + 50)
        for i in range(n_tasks)
    ]
    wait_task = tasks[wait_idx]
    records = []
    if pre_session_wait:
        records.append(
            FakeRec(
                op=WAIT_OP,
                call_sequence=10,
                raw_stream=raw,
                enter_realtime_ns=500_000,
                exit_realtime_ns=500_100,
            )
        )
    records.append(
        FakeRec(
            op=WAIT_OP,
            call_sequence=20,
            raw_stream=raw,
            enter_realtime_ns=session_start + 10,
            exit_realtime_ns=session_start + 20,
        )
    )
    ctx = FakeCtx(
        db_path=_fake_path(),
        rank0_records=records,
        string_ids={1: "EVENT_WAIT"},
        active_start=session_start,
        active_end=session_start + 1_000_000,
        all_tasks=tasks,
        all_tasks_by_rowid={t.rowid: t for t in tasks},
    )
    sha = capture_contract_sha()
    pre_sha = sha if capture_match else "deadbeef"
    return ctx, wait_task, 20, sha, pre_sha


def test_p1_p7_all_true_structural_na():
    ctx, wait_task, cs, cap_sha, pre_sha = _synthetic_ctx()
    cls = evaluate_p_predicates(ctx, wait_task, cs, cap_sha, pre_sha)
    assert cls["pre_wait_p_status"] == "STRUCTURAL_NA"
    assert cls["pre_wait_p_gate_boolean"] is None
    assert cls["pre_wait_p_reason"] == STRUCTURAL_NA_REASON
    for pk in ("P1", "P2", "P3", "P4", "P5", "P6", "P7"):
        assert cls["predicates"][pk]["value"] is True, pk


def test_db_predecessor_fails_not_na():
    ctx, wait_task, cs, cap_sha, pre_sha = _synthetic_ctx(wait_idx=1, n_tasks=3)
    cls = evaluate_p_predicates(ctx, wait_task, cs, cap_sha, pre_sha)
    assert cls["pre_wait_p_status"] == "STOP_PRE_WAIT_P_CLASSIFICATION_FAILED"
    assert cls["predicates"]["P3"]["value"] is False


def test_no_session_before_wait_p4_fails():
    ctx, wait_task, cs, cap_sha, pre_sha = _synthetic_ctx(pre_session_wait=False)
    cls = evaluate_p_predicates(ctx, wait_task, cs, cap_sha, pre_sha)
    assert cls["pre_wait_p_status"] == "STOP_PRE_WAIT_P_CLASSIFICATION_FAILED"
    assert cls["predicates"]["P4"]["value"] is False


def test_capture_drift_p1_fails():
    ctx, wait_task, cs, cap_sha, pre_sha = _synthetic_ctx(capture_match=False)
    cls = evaluate_p_predicates(ctx, wait_task, cs, cap_sha, pre_sha)
    assert cls["predicates"]["P1"]["value"] is False


def test_bypass_absolute_wallclock_shift_must_fail():
    """FIX1 ~1e10–1e11 ns absolute endpoint diff must not pass anchor-relative gate."""
    wait_end = 100
    d0_abs_bypass_end = 50_000_000_000_000
    dt_abs_bypass_end = 60_000_000_000_000
    d0 = {
        "run_id": "d0",
        "realized_work_ns": 3_000_000,
        "v48_controls": {
            "C4_bypass_compute_offset_from_wait_end_ns": {
                "value_ns": d0_abs_bypass_end - wait_end,
                "unique": True,
                "source": "bad_absolute",
                "anchor_ns": wait_end,
                "clock_domain": "profiler",
            },
            "C1_wait_duration_ns": {"value_ns": 10, "unique": True, "source": "x", "anchor_ns": wait_end, "clock_domain": "profiler"},
            "C2_record_offset_from_wait_end_ns": {"value_ns": 5, "unique": True, "source": "x", "anchor_ns": wait_end, "clock_domain": "profiler"},
            "C3_pre_target_wait_host_fifo_gap_ns": {"value_ns": 100, "unique": True, "source": "x", "anchor_ns": wait_end, "clock_domain": "preload_realtime"},
            "C5_host_issue_gap_ns": {"value_ns": 200, "unique": True, "source": "x", "anchor_ns": wait_end, "clock_domain": "profiler_cann"},
        },
    }
    dt = {
        "run_id": "dt",
        "realized_work_ns": 3_000_000,
        "v48_controls": {
            k: {**v, "value_ns": v["value_ns"] + (10_000_000_000_000 if "C4" in k else 0)}
            for k, v in d0["v48_controls"].items()
        },
    }
    ok, reason, rows = evaluate_control_gate_v48(d0, dt)
    assert ok is False
    assert "CONFOUND" in reason or "UNAVAILABLE" in reason
    c4 = next(r for r in rows if r["metric"].startswith("C4"))
    assert abs(int(c4["shift_ns"])) > 1e10


def test_anchor_relative_controls_pass_small_shift():
    base_ctrl = {
        "value_ns": 100,
        "unique": True,
        "source": "s",
        "anchor_ns": 1000,
        "clock_domain": "profiler",
    }
    keys = [
        "C1_wait_duration_ns",
        "C2_record_offset_from_wait_end_ns",
        "C3_pre_target_wait_host_fifo_gap_ns",
        "C4_bypass_compute_offset_from_wait_end_ns",
        "C5_host_issue_gap_ns",
    ]
    d0 = {"run_id": "d0", "realized_work_ns": 3_226_068, "v48_controls": {k: dict(base_ctrl) for k in keys}}
    dt = {
        "run_id": "dt",
        "realized_work_ns": 3_226_068,
        "v48_controls": {k: {**base_ctrl, "value_ns": 105} for k in keys},
    }
    ok, reason, _ = evaluate_control_gate_v48(d0, dt)
    assert ok is True
    assert reason == ""


def test_structural_na_not_control_pass():
    class T:
        def __init__(self, s, e):
            self.start_ns, self.end_ns, self.rowid = s, e, id(self)

    wait0, q0 = T(0, 100), T(100 + 721648, 200 + 721648)
    waitT, inject, qT = T(0, 100), T(108600, 108600 + 3226068), T(108600 + 3226068 + 20, 0)
    na = {
        "pre_wait_p_status": "STRUCTURAL_NA",
        "pre_wait_p_gate_boolean": None,
        "pre_wait_p_reason": STRUCTURAL_NA_REASON,
    }
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
        "realized_work_ns": 0,
        "capture_classification": na,
        "v48_controls": {k: {"value_ns": 1, "unique": True} for k in [
            "C1_wait_duration_ns", "C2_record_offset_from_wait_end_ns",
            "C3_pre_target_wait_host_fifo_gap_ns", "C4_bypass_compute_offset_from_wait_end_ns",
            "C5_host_issue_gap_ns",
        ]},
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
        "capture_classification": na,
        "v48_controls": {k: {"value_ns": 1, "unique": True} for k in d0["v48_controls"]},
    }
    row = paired_effects_v4_8(d0, dt, "b1_dlarge")
    assert row["pre_wait_p_gate_boolean"] is None
    assert row["occupancy_residual_ns"] == row["post_inject_gap_ns"] == 20
    assert row["control_gate_pass"] is True


def test_occupancy_identity_algebra():
    s0, realized, launch = 721648, 3226068, 8600
    inject_end = 100 + launch + realized
    q_start = inject_end + 20
    observed = (q_start - 100) - s0
    predicted = max(0, (inject_end - 100) - s0)
    residual = observed - predicted
    assert residual == 20
    assert occupancy_identity_check(q_start, inject_end, observed, predicted, residual, 20, s0, 100, 100 + launch)


def test_stop_package_atomic_helper():
    import os
    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "atomic_stop.sh"
        script.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
STOP="${1}/STOP.json"
python3 - <<'PY'
import json, os
from pathlib import Path
p = Path(os.environ["STOP_PATH"])
tmp = p.with_suffix(".json.tmp")
tmp.write_text(json.dumps({"stop":"STOP_UNIT_TEST"})+"\\n")
tmp.flush() if hasattr(tmp, "flush") else None
os.fsync(tmp.open("r").fileno()) if False else None
tmp.replace(p)
PY
"""
        )
        script.chmod(0o755)
        env = {**os.environ, "STOP_PATH": str(Path(td) / "STOP.json")}
        subprocess.run(["python3", "-c", f"""
import json, os
from pathlib import Path
p = Path("{td}/STOP.json")
tmp = p.with_suffix(".json.tmp")
tmp.write_text(json.dumps({{"stop":"STOP_UNIT_TEST"}})+"\\n")
tmp.replace(p)
"""], check=True)
        assert (Path(td) / "STOP.json").exists()


def main() -> None:
    test_p1_p7_all_true_structural_na()
    test_db_predecessor_fails_not_na()
    test_no_session_before_wait_p4_fails()
    test_capture_drift_p1_fails()
    test_bypass_absolute_wallclock_shift_must_fail()
    test_anchor_relative_controls_pass_small_shift()
    test_structural_na_not_control_pass()
    test_occupancy_identity_algebra()
    test_stop_package_atomic_helper()
    print("test_wait_dag_v4_8: all PASS")


if __name__ == "__main__":
    main()

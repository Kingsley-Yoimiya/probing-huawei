#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from buffer_audit import aggregate_buffer_audits, validate_rank_buffer_audit


def _payload(rank: int = 0) -> dict:
    counters = {
        "buffer_request_count": 2,
        "buffer_complete_callback_count": 2,
        "buffer_complete_count": 2,
        "inflight": 0,
        "inflight_final": 0,
        "max_inflight": 2,
        "parsed_kernel": 3,
        "parsed_comm": 1,
        "parsed_unknown": 0,
        "enqueued_kernel": 3,
        "enqueued_comm": 1,
        "queue_rejected_kernel": 0,
        "queue_rejected_comm": 0,
        "queue_rejected_other": 0,
        "queue_rejected_total": 0,
        "worker_consumed_kernel": 3,
        "worker_consumed_comm": 1,
        "processed_kernel": 3,
        "processed_comm": 1,
        "emitted_total": 4,
        "emitted_comm": 1,
        "emitted_kseg": 1,
        "emitted_kseg_raw_count": 3,
        "valid_while_stopping": 0,
        "callback_wall_total_ns": 40,
        "callback_wall_max_ns": 30,
        "malformed_timestamp_count": 0,
        "malformed_sample_count": 0,
        "ledger_capacity": 4096,
        "ledger_entry_count": 2,
        "ledger_completed_count": 2,
        "ledger_overflow_count": 0,
    }
    conservation = {
        "request_complete_balanced": True,
        "kernel_parse_queue_balanced": True,
        "kernel_worker_balanced": True,
        "kernel_process_balanced": True,
        "kernel_emit_balanced": True,
        "comm_parse_queue_balanced": True,
        "comm_worker_balanced": True,
        "comm_process_balanced": True,
        "comm_emit_balanced": True,
        "unknown_kind_zero": True,
        "valid_while_stopping_zero": True,
        "inflight_final_zero": True,
        "ledger_complete_balanced": True,
        "ledger_no_overflow": True,
        "fingerprint_all_ok": True,
        "lifecycle_snapshots_complete": True,
        "lifecycle_ordered": True,
        "before_free_quiescent": True,
        "all_ok": True,
    }
    fingerprint = {
        "kernel": {"count": 3, "xor64": 123, "sum64": 456},
        "communication": {"count": 1, "xor64": 789, "sum64": 789},
    }
    fingerprints = {
        stage: json.loads(json.dumps(fingerprint))
        for stage in ("parsed", "enqueued", "worker", "processed", "emitted")
    }
    lifecycle_snapshots = {
        name: {
            "captured": True,
            "monotonic_ns": timestamp,
            "inflight": 0,
            "active_calls": 0,
            "worker_busy": False,
        }
        for name, timestamp in (
            ("before_disable", 100),
            ("after_flush", 200),
            ("before_free", 300),
        )
    }
    buffer_ledger = [
        {
            "request_id": 1,
            "pointer_token": 1001,
            "reuse_generation": 1,
            "requested_size": 8192,
            "completion_size": 8192,
            "valid_size": 4096,
            "callback_wall_ns": 10,
            "kernel_records": 3,
            "comm_records": 0,
            "unknown_records": 0,
            "get_next_calls": 4,
            "completed": True,
        },
        {
            "request_id": 2,
            "pointer_token": 1001,
            "reuse_generation": 2,
            "requested_size": 8192,
            "completion_size": 8192,
            "valid_size": 2048,
            "callback_wall_ns": 30,
            "kernel_records": 0,
            "comm_records": 1,
            "unknown_records": 0,
            "get_next_calls": 2,
            "completed": True,
        },
    ]
    return {
        "schema_version": 2,
        "instrumentation": "mspti_buffer_audit_v2",
        "rank": rank,
        "counters": counters,
        "conservation": conservation,
        "activity_integrity_ok": True,
        "malformed_timestamp_samples": [],
        "fingerprints": fingerprints,
        "lifecycle_snapshots": lifecycle_snapshots,
        "buffer_ledger": buffer_ledger,
    }


def test_validate_and_exact_inventory() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "rank_0000.buffer_audit.json"
        path.write_text(json.dumps(_payload()), encoding="utf-8")
        aggregate, errors = aggregate_buffer_audits(root, expected_paths={0: path})
        assert not errors
        assert aggregate["pass"] is True
        assert (root / "buffer_audit.json").is_file()


def test_missing_selected_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _, errors = aggregate_buffer_audits(
            root, expected_paths={0: root / "rank_0000.buffer_audit.json"}
        )
        assert any("missing_selected=[0]" in error for error in errors)


def test_unselected_and_conservation_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        selected = root / "rank_0000.buffer_audit.json"
        selected.write_text(json.dumps(_payload()), encoding="utf-8")
        unexpected = root / "rank_0001.buffer_audit.json"
        bad = _payload(rank=1)
        bad["counters"]["worker_consumed_comm"] = 0
        unexpected.write_text(json.dumps(bad), encoding="utf-8")
        _, errors = aggregate_buffer_audits(root, expected_paths={0: selected})
        assert any("unexpected=" in error for error in errors)
        assert any("comm_worker_balanced" in error for error in errors)


def test_malformed_timestamp_fails_integrity() -> None:
    payload = _payload()
    payload["counters"]["malformed_timestamp_count"] = 1
    payload["counters"]["malformed_sample_count"] = 1
    payload["activity_integrity_ok"] = False
    payload["malformed_timestamp_samples"] = [{"start_ns": 20, "end_ns": 10}]
    errors = validate_rank_buffer_audit(payload, expected_rank=0)
    assert any("malformed_timestamp_count=1" in error for error in errors)


def test_fingerprint_ledger_and_lifecycle_corruption_fail() -> None:
    payload = _payload()
    payload["fingerprints"]["processed"]["communication"]["xor64"] += 1
    payload["conservation"]["fingerprint_all_ok"] = False
    payload["conservation"]["all_ok"] = False
    payload["activity_integrity_ok"] = False
    errors = validate_rank_buffer_audit(payload, expected_rank=0)
    assert any("fingerprint" in error for error in errors)

    payload = _payload()
    payload["buffer_ledger"][1]["reuse_generation"] = 3
    payload["lifecycle_snapshots"]["before_free"]["inflight"] = 1
    errors = validate_rank_buffer_audit(payload, expected_rank=0)
    assert any("reuse generation" in error for error in errors)
    assert any("before_free snapshot not quiescent" in error for error in errors)


if __name__ == "__main__":
    test_validate_and_exact_inventory()
    test_missing_selected_fails_closed()
    test_unselected_and_conservation_failure()
    test_malformed_timestamp_fails_integrity()
    test_fingerprint_ledger_and_lifecycle_corruption_fail()

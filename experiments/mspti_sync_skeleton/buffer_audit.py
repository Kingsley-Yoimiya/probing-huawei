#!/usr/bin/env python3
"""Validate and aggregate per-rank MSPTI native buffer-audit sidecars."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


AUDIT_NAME_RE = re.compile(r"^rank_(\d{4})\.buffer_audit\.json$")


def _int_field(counters: dict[str, Any], name: str, errors: list[str], rank: int) -> int:
    value = counters.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append(f"rank={rank} audit counter {name} is not a nonnegative integer")
        return -1
    return value


def validate_rank_buffer_audit(payload: dict[str, Any], *, expected_rank: int) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 2:
        errors.append(f"rank={expected_rank} audit schema_version={payload.get('schema_version')!r}")
    if payload.get("instrumentation") != "mspti_buffer_audit_v2":
        errors.append(f"rank={expected_rank} audit instrumentation mismatch")
    if payload.get("rank") != expected_rank:
        errors.append(f"rank={expected_rank} audit payload rank={payload.get('rank')!r}")
    counters = payload.get("counters")
    if not isinstance(counters, dict):
        return errors + [f"rank={expected_rank} audit counters missing"]

    names = (
        "buffer_request_count",
        "buffer_complete_callback_count",
        "buffer_complete_count",
        "inflight",
        "inflight_final",
        "max_inflight",
        "parsed_kernel",
        "parsed_comm",
        "parsed_unknown",
        "enqueued_kernel",
        "enqueued_comm",
        "queue_rejected_kernel",
        "queue_rejected_comm",
        "queue_rejected_other",
        "queue_rejected_total",
        "worker_consumed_kernel",
        "worker_consumed_comm",
        "processed_kernel",
        "processed_comm",
        "emitted_total",
        "emitted_comm",
        "emitted_kseg",
        "emitted_kseg_raw_count",
        "valid_while_stopping",
        "callback_wall_total_ns",
        "callback_wall_max_ns",
        "malformed_timestamp_count",
        "malformed_sample_count",
        "ledger_capacity",
        "ledger_entry_count",
        "ledger_completed_count",
        "ledger_overflow_count",
    )
    values = {
        name: _int_field(counters, name, errors, expected_rank) for name in names
    }
    if errors:
        return errors

    checks = {
        "request_complete_balanced": (
            values["buffer_request_count"] == values["buffer_complete_count"]
        ),
        "kernel_parse_queue_balanced": (
            values["parsed_kernel"]
            == values["enqueued_kernel"] + values["queue_rejected_kernel"]
        ),
        "kernel_worker_balanced": (
            values["enqueued_kernel"] == values["worker_consumed_kernel"]
        ),
        "kernel_process_balanced": (
            values["worker_consumed_kernel"] == values["processed_kernel"]
        ),
        "kernel_emit_balanced": (
            values["processed_kernel"] == values["emitted_kseg_raw_count"]
        ),
        "comm_parse_queue_balanced": (
            values["parsed_comm"]
            == values["enqueued_comm"] + values["queue_rejected_comm"]
        ),
        "comm_worker_balanced": (
            values["enqueued_comm"] == values["worker_consumed_comm"]
        ),
        "comm_process_balanced": (
            values["worker_consumed_comm"] == values["processed_comm"]
        ),
        "comm_emit_balanced": (
            values["processed_comm"] == values["emitted_comm"]
        ),
        "unknown_kind_zero": values["parsed_unknown"] == 0,
        "valid_while_stopping_zero": values["valid_while_stopping"] == 0,
        "inflight_final_zero": values["inflight_final"] == 0,
        "ledger_complete_balanced": (
            values["ledger_completed_count"] == values["ledger_entry_count"]
        ),
        "ledger_no_overflow": values["ledger_overflow_count"] == 0,
    }

    stages = ("parsed", "enqueued", "worker", "processed", "emitted")
    kinds = ("kernel", "communication")
    fingerprints = payload.get("fingerprints")
    parsed_fingerprints: dict[str, dict[str, tuple[int, int, int]]] = {}
    if not isinstance(fingerprints, dict):
        errors.append(f"rank={expected_rank} fingerprints missing")
    else:
        for stage in stages:
            stage_payload = fingerprints.get(stage)
            if not isinstance(stage_payload, dict):
                errors.append(f"rank={expected_rank} fingerprint stage {stage} missing")
                continue
            parsed_fingerprints[stage] = {}
            for kind in kinds:
                item = stage_payload.get(kind)
                if not isinstance(item, dict):
                    errors.append(
                        f"rank={expected_rank} fingerprint {stage}/{kind} missing"
                    )
                    continue
                fields: list[int] = []
                for field in ("count", "xor64", "sum64"):
                    value = item.get(field)
                    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                        errors.append(
                            f"rank={expected_rank} fingerprint {stage}/{kind}/{field} invalid"
                        )
                        value = -1
                    fields.append(value)
                parsed_fingerprints[stage][kind] = tuple(fields)  # type: ignore[assignment]

    expected_fingerprint_counts = {
        "parsed": {"kernel": values["parsed_kernel"], "communication": values["parsed_comm"]},
        "enqueued": {"kernel": values["enqueued_kernel"], "communication": values["enqueued_comm"]},
        "worker": {"kernel": values["worker_consumed_kernel"], "communication": values["worker_consumed_comm"]},
        "processed": {"kernel": values["processed_kernel"], "communication": values["processed_comm"]},
        "emitted": {"kernel": values["emitted_kseg_raw_count"], "communication": values["emitted_comm"]},
    }
    for stage in stages:
        for kind in kinds:
            item = parsed_fingerprints.get(stage, {}).get(kind)
            if item is not None and item[0] != expected_fingerprint_counts[stage][kind]:
                errors.append(
                    f"rank={expected_rank} fingerprint {stage}/{kind} count={item[0]} "
                    f"counter={expected_fingerprint_counts[stage][kind]}"
                )
    checks["fingerprint_all_ok"] = all(
        parsed_fingerprints.get(stage, {}).get(kind)
        == parsed_fingerprints.get("parsed", {}).get(kind)
        for stage in stages[1:]
        for kind in kinds
    ) and all(kind in parsed_fingerprints.get("parsed", {}) for kind in kinds)

    lifecycle = payload.get("lifecycle_snapshots")
    lifecycle_names = ("before_disable", "after_flush", "before_free")
    lifecycle_items = (
        [lifecycle.get(name) for name in lifecycle_names]
        if isinstance(lifecycle, dict)
        else []
    )
    checks["lifecycle_snapshots_complete"] = len(lifecycle_items) == 3 and all(
        isinstance(item, dict) and item.get("captured") is True
        for item in lifecycle_items
    )
    lifecycle_times = [
        item.get("monotonic_ns") if isinstance(item, dict) else None
        for item in lifecycle_items
    ]
    checks["lifecycle_ordered"] = (
        checks["lifecycle_snapshots_complete"]
        and all(isinstance(value, int) and value > 0 for value in lifecycle_times)
        and lifecycle_times == sorted(lifecycle_times)
    )
    before_free_item = lifecycle_items[2] if len(lifecycle_items) == 3 else None
    checks["before_free_quiescent"] = (
        isinstance(before_free_item, dict)
        and before_free_item.get("inflight") == 0
        and before_free_item.get("active_calls") == 0
        and before_free_item.get("worker_busy") is False
    )
    if values["inflight"] != values["inflight_final"]:
        errors.append(f"rank={expected_rank} inflight alias mismatch")
    if values["queue_rejected_total"] != (
        values["queue_rejected_kernel"]
        + values["queue_rejected_comm"]
        + values["queue_rejected_other"]
    ):
        errors.append(f"rank={expected_rank} queue_rejected_total mismatch")
    if values["queue_rejected_total"] != 0:
        errors.append(
            f"rank={expected_rank} queue_rejected_total="
            f"{values['queue_rejected_total']}"
        )
    checks["all_ok"] = all(checks.values())
    native_checks = payload.get("conservation")
    if not isinstance(native_checks, dict):
        errors.append(f"rank={expected_rank} audit conservation missing")
    else:
        for name, expected in checks.items():
            if native_checks.get(name) is not expected:
                errors.append(
                    f"rank={expected_rank} audit conservation {name}="
                    f"{native_checks.get(name)!r} recomputed={expected}"
                )
    if not checks["all_ok"]:
        failed = sorted(name for name, ok in checks.items() if name != "all_ok" and not ok)
        errors.append(f"rank={expected_rank} audit conservation failed: {failed}")
    if values["malformed_timestamp_count"] != 0:
        errors.append(
            f"rank={expected_rank} malformed_timestamp_count="
            f"{values['malformed_timestamp_count']}"
        )
    if payload.get("activity_integrity_ok") is not (
        checks["all_ok"] and values["malformed_timestamp_count"] == 0
    ):
        errors.append(f"rank={expected_rank} audit activity_integrity_ok mismatch")
    if values["buffer_complete_callback_count"] < values["buffer_complete_count"]:
        errors.append(f"rank={expected_rank} complete callback count below owned completes")
    if values["max_inflight"] > values["buffer_request_count"]:
        errors.append(f"rank={expected_rank} max_inflight exceeds requests")

    ledger = payload.get("buffer_ledger")
    if not isinstance(ledger, list):
        errors.append(f"rank={expected_rank} buffer ledger missing")
    else:
        if len(ledger) != values["ledger_entry_count"]:
            errors.append(
                f"rank={expected_rank} ledger inventory={len(ledger)} "
                f"counter={values['ledger_entry_count']}"
            )
        if values["buffer_request_count"] != (
            values["ledger_entry_count"] + values["ledger_overflow_count"]
        ):
            errors.append(f"rank={expected_rank} ledger request coverage mismatch")
        completed = 0
        kernel_records = 0
        comm_records = 0
        unknown_records = 0
        callback_total = 0
        callback_max = 0
        generation_by_token: dict[int, int] = {}
        for index, entry in enumerate(ledger, start=1):
            if not isinstance(entry, dict):
                errors.append(f"rank={expected_rank} ledger entry {index} invalid")
                continue
            if entry.get("request_id") != index:
                errors.append(f"rank={expected_rank} ledger request_id sequence mismatch")
            for name in (
                "pointer_token", "reuse_generation", "requested_size", "completion_size",
                "valid_size", "callback_wall_ns", "kernel_records", "comm_records",
                "unknown_records", "get_next_calls",
            ):
                value = entry.get(name)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    errors.append(f"rank={expected_rank} ledger {index} field {name} invalid")
            if entry.get("completed") is True:
                completed += 1
            token = entry.get("pointer_token")
            generation = entry.get("reuse_generation")
            if isinstance(token, int) and isinstance(generation, int):
                expected_generation = generation_by_token.get(token, 0) + 1
                if generation != expected_generation:
                    errors.append(
                        f"rank={expected_rank} pointer token reuse generation "
                        f"got={generation} expected={expected_generation}"
                    )
                generation_by_token[token] = generation
            if isinstance(entry.get("valid_size"), int) and isinstance(entry.get("completion_size"), int):
                if entry["valid_size"] > entry["completion_size"]:
                    errors.append(f"rank={expected_rank} ledger {index} valid_size exceeds size")
            kernel_records += entry.get("kernel_records", 0) if isinstance(entry.get("kernel_records"), int) else 0
            comm_records += entry.get("comm_records", 0) if isinstance(entry.get("comm_records"), int) else 0
            unknown_records += entry.get("unknown_records", 0) if isinstance(entry.get("unknown_records"), int) else 0
            wall = entry.get("callback_wall_ns")
            if isinstance(wall, int):
                callback_total += wall
                callback_max = max(callback_max, wall)
        if completed != values["ledger_completed_count"]:
            errors.append(f"rank={expected_rank} ledger completed inventory mismatch")
        if (kernel_records, comm_records, unknown_records) != (
            values["parsed_kernel"], values["parsed_comm"], values["parsed_unknown"]
        ):
            errors.append(f"rank={expected_rank} ledger record totals mismatch")
        if callback_total != values["callback_wall_total_ns"]:
            errors.append(f"rank={expected_rank} ledger callback wall total mismatch")
        if callback_max != values["callback_wall_max_ns"]:
            errors.append(f"rank={expected_rank} ledger callback wall max mismatch")

    if not isinstance(lifecycle, dict):
        errors.append(f"rank={expected_rank} lifecycle snapshots missing")
    else:
        timestamps: list[int] = []
        for name in lifecycle_names:
            snapshot = lifecycle.get(name)
            if not isinstance(snapshot, dict) or snapshot.get("captured") is not True:
                errors.append(f"rank={expected_rank} lifecycle snapshot {name} missing")
                continue
            timestamp = snapshot.get("monotonic_ns")
            if not isinstance(timestamp, int) or timestamp <= 0:
                errors.append(f"rank={expected_rank} lifecycle snapshot {name} time invalid")
            else:
                timestamps.append(timestamp)
        if len(timestamps) == 3 and timestamps != sorted(timestamps):
            errors.append(f"rank={expected_rank} lifecycle snapshots out of order")
        before_free = lifecycle.get("before_free")
        if isinstance(before_free, dict) and (
            before_free.get("inflight") != 0
            or before_free.get("active_calls") != 0
            or before_free.get("worker_busy") is not False
        ):
            errors.append(f"rank={expected_rank} before_free snapshot not quiescent")
    samples = payload.get("malformed_timestamp_samples")
    if not isinstance(samples, list):
        errors.append(f"rank={expected_rank} malformed samples missing")
    elif len(samples) != values["malformed_sample_count"]:
        errors.append(
            f"rank={expected_rank} malformed sample inventory="
            f"{len(samples)} counter={values['malformed_sample_count']}"
        )
    elif len(samples) > values["malformed_timestamp_count"]:
        errors.append(f"rank={expected_rank} malformed samples exceed total")
    return errors


def atomic_json(path: Path, payload: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with tmp.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    directory_fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def aggregate_buffer_audits(
    root: Path,
    *,
    expected_paths: dict[int, Path],
    output_path: Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Require exact selected-rank inventory and independently recompute invariants."""
    root = Path(root)
    output_path = output_path or root / "buffer_audit.json"
    errors: list[str] = []
    actual_paths = sorted(root.rglob("rank_*.buffer_audit.json"))
    expected_resolved = {rank: path.resolve() for rank, path in expected_paths.items()}
    actual_resolved = {path.resolve() for path in actual_paths}
    expected_set = set(expected_resolved.values())
    if actual_resolved != expected_set:
        missing = sorted(
            rank for rank, path in expected_resolved.items() if path not in actual_resolved
        )
        unexpected = sorted(
            str(path.relative_to(root.resolve()))
            for path in actual_resolved - expected_set
        )
        errors.append(
            f"buffer audit inventory mismatch missing_selected={missing} "
            f"unexpected={unexpected}"
        )

    per_rank: list[dict[str, Any]] = []
    seen_ranks: set[int] = set()
    for path in actual_paths:
        match = AUDIT_NAME_RE.fullmatch(path.name)
        if match is None:
            errors.append(f"bad buffer audit filename: {path.name}")
            continue
        rank = int(match.group(1))
        if rank in seen_ranks:
            errors.append(f"duplicate buffer audit rank={rank}")
        seen_ranks.add(rank)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"rank={rank} cannot read buffer audit: {exc}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"rank={rank} buffer audit root is not an object")
            continue
        rank_errors = validate_rank_buffer_audit(payload, expected_rank=rank)
        errors.extend(rank_errors)
        per_rank.append(
            {
                "rank": rank,
                "path": str(path.relative_to(root)),
                "valid": not rank_errors and rank in expected_paths,
                "counters": payload.get("counters"),
                "conservation": payload.get("conservation"),
                "activity_integrity_ok": payload.get("activity_integrity_ok"),
                "malformed_timestamp_samples": payload.get(
                    "malformed_timestamp_samples"
                ),
                "fingerprints": payload.get("fingerprints"),
                "lifecycle_snapshots": payload.get("lifecycle_snapshots"),
                "buffer_ledger": payload.get("buffer_ledger"),
            }
        )

    aggregate = {
        "schema_version": 2,
        "instrumentation": "mspti_buffer_audit_v2",
        "pass": not errors,
        "errors": errors,
        "expected_ranks": sorted(expected_paths),
        "observed_ranks": sorted(seen_ranks),
        "audit_count": len(actual_paths),
        "per_rank": sorted(per_rank, key=lambda item: int(item["rank"])),
    }
    atomic_json(output_path, aggregate)
    return aggregate, errors

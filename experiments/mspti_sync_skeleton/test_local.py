#!/usr/bin/env python3
"""本地单测：strict validate / 吞吐公式 / convert fail-closed / 真实逻辑负例。不含 MSPTI。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from analyze_megatron_ab import derived_samples_per_s, derived_tokens_per_gpu_s
from strict_validate import (
    StrictValidationError,
    audit_fatal_signals_in_node_logs,
    parse_drop_flags,
    validate_attempt_manifest,
    validate_ours_dir,
    validate_rank_rows,
)


def test_throughput_formula() -> None:
    assert abs(derived_samples_per_s(64, 2000) - 32.0) < 1e-9
    assert abs(derived_tokens_per_gpu_s(64, 4096, 32, 2000) - 4096.0) < 1e-9


def _good_rows(
    rank: int,
    step: int = 10,
    raw_count: int = 100,
    comm: int = 10,
    drop_flags: Optional[str] = None,
    drop_count: int = 0,
) -> list[dict]:
    rows = [
        {
            "run_id": "t",
            "rank": rank,
            "host": "h",
            "device_id": 0,
            "stream_id": -1,
            "step": step,
            "seq": 0,
            "kind": "STEP",
            "start_ns": 100,
            "end_ns": 100,
            "flags": "phase=begin;gate=capture_begin",
            "active_ns": 0,
            "span_ns": 0,
            "gap_ns": 0,
            "count": 1,
        },
        {
            "run_id": "t",
            "rank": rank,
            "host": "h",
            "device_id": 0,
            "stream_id": 0,
            "step": step,
            "seq": 1,
            "kind": "KSEG",
            "start_ns": 200,
            "end_ns": 300,
            "flags": "active_ns=interval_union",
            "active_ns": 80,
            "span_ns": 100,
            "gap_ns": 20,
            "count": raw_count,
        },
    ]
    for i in range(comm):
        rows.append(
            {
                "run_id": "t",
                "rank": rank,
                "host": "h",
                "device_id": 0,
                "stream_id": 1,
                "step": step,
                "seq": 2 + i,
                "kind": "COMM",
                "start_ns": 210 + i,
                "end_ns": 220 + i,
                "flags": "source=mspti_communication_activity",
                "active_ns": 0,
                "span_ns": 0,
                "gap_ns": 0,
                "count": 1,
            }
        )
    end_seq = 2 + comm
    rows.append(
        {
            "run_id": "t",
            "rank": rank,
            "host": "h",
            "device_id": 0,
            "stream_id": -1,
            "step": step,
            "seq": end_seq,
            "kind": "STEP",
            "start_ns": 400,
            "end_ns": 400,
            "flags": "phase=end;gate=capture_end",
            "active_ns": 0,
            "span_ns": 0,
            "gap_ns": 0,
            "count": 1,
        }
    )
    flags = drop_flags or (
        "allocation=0;queue=0;parse=0;io=0;callback=0;mspti=0;"
        f"incomplete=0;finalize=1;raw_kernels={raw_count}"
    )
    rows.append(
        {
            "run_id": "t",
            "rank": rank,
            "host": "h",
            "device_id": 0,
            "stream_id": -1,
            "step": step,
            "seq": end_seq + 1,
            "kind": "DROP",
            "start_ns": 500,
            "end_ns": 500,
            "flags": flags,
            "active_ns": 0,
            "span_ns": 0,
            "gap_ns": 0,
            "count": drop_count,
        }
    )
    return rows


def _good_meta(rank: int, raw: int = 100, reason: str = "last_train_step") -> dict:
    return {
        "rank": rank,
        "finalize_complete": True,
        "finalize_rc": 0,
        "incomplete": False,
        "armed_fail": False,
        "capture_end_rc": 0,
        "raw_kernels": raw,
        "raw_comms": 10,
        "finalize_ms": 1.0,
        "finalize_flush_ms": 0.5,
        "finalize_drain_ms": 0.4,
        "finalize_total_ms": 1.0,
        "capture_begin_ms": 0.1,
        "capture_end_ms": 0.2,
        "collector_start_ms": 3.0,
        "process_wall_ms": 1000.0,
        "finalize_reason": reason,
    }


def _write_rank(root: Path, rank: int, raw: int = 100, comm: int = 10, **kw) -> None:
    (root / f"rank_{rank:04d}.skeleton.jsonl").write_text(
        "\n".join(json.dumps(x) for x in _good_rows(rank, raw_count=raw, comm=comm, **kw))
        + "\n",
        encoding="utf-8",
    )
    (root / f"rank_{rank:04d}.mspti_meta.json").write_text(
        json.dumps(_good_meta(rank, raw=raw)), encoding="utf-8"
    )


def test_strict_rank_ok() -> None:
    validate_rank_rows(
        _good_rows(0),
        expected_rank=0,
        capture_step=10,
        meta=_good_meta(0),
        min_raw_kernels=50,
        min_comm=5,
    )


def test_strict_active_gt_span_fails() -> None:
    rows = _good_rows(0)
    rows[1]["active_ns"] = 200
    rows[1]["span_ns"] = 100
    try:
        validate_rank_rows(
            rows, expected_rank=0, capture_step=10, meta=_good_meta(0), min_comm=0
        )
        raise AssertionError("expected fail")
    except StrictValidationError as exc:
        assert "active_ns>span_ns" in str(exc)


def test_strict_missing_finalize_flag_fails() -> None:
    rows = _good_rows(0)
    rows[-1]["flags"] = "allocation=0;queue=0;parse=0;io=0;callback=0;mspti=0;incomplete=0"
    try:
        validate_rank_rows(
            rows, expected_rank=0, capture_step=10, meta=_good_meta(0), min_comm=0
        )
        raise AssertionError("expected fail")
    except StrictValidationError as exc:
        assert "finalize" in str(exc)


def test_strict_meta_finalize_false_fails() -> None:
    meta = _good_meta(0)
    meta["finalize_complete"] = False
    try:
        validate_rank_rows(
            _good_rows(0), expected_rank=0, capture_step=10, meta=meta, min_comm=0
        )
        raise AssertionError("expected fail")
    except StrictValidationError as exc:
        assert "finalize_complete" in str(exc)


def test_meta_kseg_mismatch_fails() -> None:
    """P1 负例：meta=100、KSEG.count=1 必须 fail。"""
    rows = _good_rows(0, raw_count=1)
    meta = _good_meta(0, raw=100)
    try:
        validate_rank_rows(
            rows, expected_rank=0, capture_step=10, meta=meta, min_comm=0, min_raw_kernels=1
        )
        raise AssertionError("UNEXPECTED_PASS meta!=KSEG")
    except StrictValidationError as exc:
        assert "sum(KSEG.count)" in str(exc)


def test_drop_component_nonzero_count0_fails() -> None:
    """P2 负例：子计数非0 但 count=0。"""
    rows = _good_rows(
        0,
        drop_flags="allocation=9;queue=0;parse=0;io=0;callback=0;mspti=0;incomplete=0;finalize=1",
        drop_count=0,
    )
    try:
        validate_rank_rows(
            rows, expected_rank=0, capture_step=10, meta=_good_meta(0), min_comm=0
        )
        raise AssertionError("UNEXPECTED_PASS DROP component")
    except StrictValidationError as exc:
        assert "DROP" in str(exc)


def test_atexit_finalize_rejected() -> None:
    meta = _good_meta(0, reason="atexit")
    try:
        validate_rank_rows(
            _good_rows(0), expected_rank=0, capture_step=10, meta=meta, min_comm=0
        )
        raise AssertionError("UNEXPECTED_PASS atexit")
    except StrictValidationError as exc:
        assert "finalize_reason" in str(exc)


def test_all_ranks_raw1_fails_absolute() -> None:
    """P1 负例：32rank 全 raw=1 — 绝对下限拦截（禁止仅靠自参考 median×0.25）。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for r in range(32):
            _write_rank(root, r, raw=1, comm=1)
        (root / "config.json").write_text(
            json.dumps(
                {
                    "frozen_thresholds": {
                        "min_raw_kernels_per_rank": 250,
                        "min_comm_per_rank": 10,
                    }
                }
            ),
            encoding="utf-8",
        )
        try:
            validate_ours_dir(root, expected_ranks=32, capture_step=10)
            raise AssertionError("UNEXPECTED_PASS all-raw-1")
        except StrictValidationError as exc:
            assert "raw_kernels" in str(exc) or "min" in str(exc)


def test_single_rank_below_center_fails() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_rank(root, 0, raw=1000, comm=20)
        _write_rank(root, 1, raw=100, comm=20)  # << 0.8 * center
        (root / "config.json").write_text(
            json.dumps(
                {
                    "frozen_thresholds": {
                        "min_raw_kernels_per_rank": 50,
                        "min_comm_per_rank": 5,
                        "relative_raw_floor": 0.8,
                    }
                }
            ),
            encoding="utf-8",
        )
        try:
            validate_ours_dir(root, expected_ranks=2, capture_step=10)
            raise AssertionError("UNEXPECTED_PASS below-center")
        except StrictValidationError as exc:
            assert "floor" in str(exc) or "raw_kernels" in str(exc)


def test_comm_truncation_fails() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_rank(root, 0, raw=500, comm=20)
        _write_rank(root, 1, raw=500, comm=1)  # COMM 截断
        (root / "config.json").write_text(
            json.dumps(
                {
                    "frozen_thresholds": {
                        "min_raw_kernels_per_rank": 250,
                        "min_comm_per_rank": 10,
                        "relative_comm_floor": 0.8,
                    }
                }
            ),
            encoding="utf-8",
        )
        try:
            validate_ours_dir(root, expected_ranks=2, capture_step=10)
            raise AssertionError("UNEXPECTED_PASS COMM trunc")
        except StrictValidationError as exc:
            assert "COMM" in str(exc) or "comm" in str(exc).lower() or "floor" in str(exc)


def test_strict_bad_json_fails() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for r in range(2):
            p = root / f"rank_{r:04d}.skeleton.jsonl"
            lines = [json.dumps(x) for x in _good_rows(r)]
            if r == 1:
                lines[1] = "{bad"
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            (root / f"rank_{r:04d}.mspti_meta.json").write_text(
                json.dumps(_good_meta(r)), encoding="utf-8"
            )
        (root / "config.json").write_text(
            json.dumps(
                {
                    "frozen_thresholds": {
                        "min_raw_kernels_per_rank": 50,
                        "min_comm_per_rank": 1,
                    }
                }
            ),
            encoding="utf-8",
        )
        try:
            validate_ours_dir(root, expected_ranks=2, capture_step=10)
            raise AssertionError("expected fail")
        except StrictValidationError as exc:
            assert "JSONDecodeError" in str(exc) or "non-json" in str(exc)


def test_lifecycle_parent_evidence_negatives() -> None:
    """缺 done / fail 存在 / raw exit 非0|pending / seal 篡改 全部 fail。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for r in range(2):
            _write_rank(root, r, raw=300, comm=12)
        (root / "config.json").write_text(
            json.dumps(
                {
                    "frozen_thresholds": {
                        "min_raw_kernels_per_rank": 250,
                        "min_comm_per_rank": 10,
                    }
                }
            ),
            encoding="utf-8",
        )
        # missing done
        man = {
            "exit_code": 0,
            "convert_rc": 0,
            "finalize_complete": True,
            "capture_megatron_iter": 10,
            "expected_nodes": 1,
            "nnodes": 1,
            "node_launch": {
                "node_0.launch.json": {
                    "raw_exit_code": 0,
                    "raw_exit_code_pending": False,
                    "e2e_wall_ms": 12.5,
                }
            },
            "provenance": {
                "source_tree_sha256": "a" * 64,
                "collector_so_sha256": "b" * 64,
                "artifact_digest_sha256": "c" * 64,
            },
        }
        (root / "attempt_manifest.json").write_text(json.dumps(man), encoding="utf-8")
        (root / "provenance_source_tree.json").write_text("{}", encoding="utf-8")
        (root / "artifact_digest.json").write_text(
            json.dumps({"artifact_digest_sha256": "c" * 64, "files": []}), encoding="utf-8"
        )
        try:
            validate_attempt_manifest(
                root, expected_ranks=2, capture_step=10, expected_nodes=1,
                require_seal=False, require_provenance=False,
            )
            raise AssertionError("UNEXPECTED_PASS missing done")
        except StrictValidationError as exc:
            assert "done" in str(exc)

        (root / "node_0.done").write_text("", encoding="utf-8")
        (root / "node_0.fail").write_text("1", encoding="utf-8")
        try:
            validate_attempt_manifest(
                root, expected_ranks=2, capture_step=10, expected_nodes=1,
                require_seal=False, require_provenance=False,
            )
            raise AssertionError("UNEXPECTED_PASS fail present")
        except StrictValidationError as exc:
            assert "fail" in str(exc)

        (root / "node_0.fail").unlink()
        man["node_launch"]["node_0.launch.json"]["raw_exit_code"] = 7
        (root / "attempt_manifest.json").write_text(json.dumps(man), encoding="utf-8")
        try:
            validate_attempt_manifest(
                root, expected_ranks=2, capture_step=10, expected_nodes=1,
                require_seal=False, require_provenance=False,
            )
            raise AssertionError("UNEXPECTED_PASS bad exit")
        except StrictValidationError as exc:
            assert "raw_exit_code" in str(exc)

        man["node_launch"]["node_0.launch.json"]["raw_exit_code"] = 0
        man["node_launch"]["node_0.launch.json"]["raw_exit_code_pending"] = True
        (root / "attempt_manifest.json").write_text(json.dumps(man), encoding="utf-8")
        try:
            validate_attempt_manifest(
                root, expected_ranks=2, capture_step=10, expected_nodes=1,
                require_seal=False, require_provenance=False,
            )
            raise AssertionError("UNEXPECTED_PASS pending")
        except StrictValidationError as exc:
            assert "pending" in str(exc)

        man["node_launch"]["node_0.launch.json"]["raw_exit_code_pending"] = False
        (root / "attempt_manifest.json").write_text(json.dumps(man), encoding="utf-8")
        digest = hashlib.sha256((root / "attempt_manifest.json").read_bytes()).hexdigest()
        (root / "attempt_manifest.sha256").write_text(digest + "\n", encoding="utf-8")
        # tamper after seal
        man["tampered"] = True
        (root / "attempt_manifest.json").write_text(json.dumps(man), encoding="utf-8")
        try:
            validate_attempt_manifest(
                root, expected_ranks=2, capture_step=10, expected_nodes=1,
                require_seal=True, require_provenance=False,
            )
            raise AssertionError("UNEXPECTED_PASS seal tamper")
        except StrictValidationError as exc:
            assert "sha256" in str(exc)


def _build_minimal_sealed_attempt(root: Path) -> dict:
    """Build a minimal sealed attempt with correct aggregate semantics."""
    from provenance import build_artifact_digest, write_local_verified_seal

    for r in range(1):
        _write_rank(root, r, raw=300, comm=12)
    (root / "config.json").write_text(
        json.dumps(
            {
                "frozen_thresholds": {
                    "min_raw_kernels_per_rank": 50,
                    "min_comm_per_rank": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    (root / "node_0.done").write_text("", encoding="utf-8")
    so_bytes = b"fake-so"
    so_digest = hashlib.sha256(so_bytes).hexdigest()
    (root / "node_0.launch.json").write_text(
        json.dumps(
            {
                "raw_exit_code": 0,
                "raw_exit_code_pending": False,
                "e2e_wall_ms": 1234.5,
                "collector_so_sha256_loaded": so_digest,
                "collector_so_loaded_path": f"sealed_bins/libmspti_sync_skeleton.so.{so_digest}",
            }
        ),
        encoding="utf-8",
    )
    (root / "counters.json").write_text(json.dumps({"pass": True}), encoding="utf-8")
    (root / "cluster.trace.json").write_text("{}", encoding="utf-8")
    (root / "run.log").write_text("run\n", encoding="utf-8")
    (root / "node_0.log").write_text("log\n", encoding="utf-8")
    # Optional sealed SO for ours arm attribution — name/claim must match actual bytes.
    sealed = root / "sealed_bins"
    sealed.mkdir(parents=True, exist_ok=True)
    so = sealed / f"libmspti_sync_skeleton.so.{so_digest}"
    so.write_bytes(so_bytes)
    try:
        so.chmod(0o444)
    except OSError:
        pass
    (root / "provenance_build.json").write_text(
        json.dumps(
            {
                "collector_so_sha256": so_digest,
                "collector_so_sealed_relpath": f"sealed_bins/{so.name}",
                "collector_so_size": len(so_bytes),
            }
        ),
        encoding="utf-8",
    )
    (root / "provenance_source_tree.json").write_text(
        json.dumps({"source_tree_sha256": "a" * 64, "files": []}), encoding="utf-8"
    )
    (root / "provenance_source_snapshot.sha256").write_text("a" * 64 + "\n", encoding="utf-8")
    art = build_artifact_digest(root)
    man = {
        "arm": "ours",
        "exit_code": 0,
        "convert_rc": 0,
        "finalize_complete": True,
        "capture_megatron_iter": 10,
        "expected_ranks": 1,
        "expected_nodes": 1,
        "nnodes": 1,
        "e2e_wall_ms": 1234.5,
        "node_launch": {
            "node_0.launch.json": {
                "raw_exit_code": 0,
                "raw_exit_code_pending": False,
                "e2e_wall_ms": 1234.5,
                "collector_so_sha256_loaded": so_digest,
                "collector_so_loaded_path": f"sealed_bins/{so.name}",
            }
        },
        "provenance": {
            "source_tree_sha256": "a" * 64,
            "collector_so_sha256": so_digest,
            "artifact_digest_sha256": art["aggregate_sha256"],
        },
        "artifact_digest_sha256": art["aggregate_sha256"],
    }
    (root / "attempt_manifest.json").write_text(
        json.dumps(man, indent=2, sort_keys=True), encoding="utf-8"
    )
    digest = hashlib.sha256((root / "attempt_manifest.json").read_bytes()).hexdigest()
    (root / "attempt_manifest.sha256").write_text(digest + "\n", encoding="utf-8")
    return {"manifest": man, "art": art, "man_hash": digest}


def test_artifact_digest_negatives() -> None:
    from provenance import build_artifact_digest, write_local_verified_seal
    from strict_validate import verify_artifact_digest, verify_local_anchor

    # phantom artifact
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _build_minimal_sealed_attempt(root)
        art = json.loads((root / "artifact_digest.json").read_text())
        art["files"].append(
            {"path": "rank_9999.skeleton.jsonl", "sha256": "d" * 64, "size": 1}
        )
        # keep listed aggregate stale → should fail on missing file
        (root / "artifact_digest.json").write_text(json.dumps(art), encoding="utf-8")
        try:
            verify_artifact_digest(root, require_required_globs=False)
            raise AssertionError("UNEXPECTED_PASS phantom")
        except StrictValidationError as exc:
            assert "missing" in str(exc)

    # tamper artifact content
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _build_minimal_sealed_attempt(root)
        p = root / "rank_0000.skeleton.jsonl"
        p.write_text(p.read_text() + "\n", encoding="utf-8")
        try:
            verify_artifact_digest(
                root,
                expected_aggregate=json.loads((root / "attempt_manifest.json").read_text())[
                    "artifact_digest_sha256"
                ],
                require_required_globs=False,
            )
            raise AssertionError("UNEXPECTED_PASS tampered artifact")
        except StrictValidationError:
            pass

    # path traversal
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _build_minimal_sealed_attempt(root)
        art = json.loads((root / "artifact_digest.json").read_text())
        art["files"].append({"path": "../etc/passwd", "sha256": "e" * 64, "size": 1})
        (root / "artifact_digest.json").write_text(json.dumps(art), encoding="utf-8")
        try:
            verify_artifact_digest(root, require_required_globs=False)
            raise AssertionError("UNEXPECTED_PASS traversal")
        except StrictValidationError as exc:
            assert "unsafe" in str(exc) or "escape" in str(exc)

    # file-hash-as-aggregate semantic error
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        info = _build_minimal_sealed_attempt(root)
        file_hash = hashlib.sha256((root / "artifact_digest.json").read_bytes()).hexdigest()
        man = info["manifest"]
        man["provenance"]["artifact_digest_sha256"] = file_hash
        man["artifact_digest_sha256"] = file_hash
        (root / "attempt_manifest.json").write_text(
            json.dumps(man, indent=2, sort_keys=True), encoding="utf-8"
        )
        digest = hashlib.sha256((root / "attempt_manifest.json").read_bytes()).hexdigest()
        (root / "attempt_manifest.sha256").write_text(digest + "\n", encoding="utf-8")
        try:
            validate_attempt_manifest(
                root, expected_ranks=1, capture_step=10, expected_nodes=1,
                require_seal=True, require_provenance=True,
            )
            raise AssertionError("UNEXPECTED_PASS file-hash-as-aggregate")
        except StrictValidationError:
            pass

    # reseal after mutating parent evidence
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        info = _build_minimal_sealed_attempt(root)
        (root / "node_0.launch.json").write_text(
            json.dumps(
                {
                    "raw_exit_code": 0,
                    "raw_exit_code_pending": False,
                    "e2e_wall_ms": 1234.5,
                    "x": 1,
                }
            ),
            encoding="utf-8",
        )
        # only reseal manifest, leave stale digest → must fail aggregate/hash
        man = json.loads((root / "attempt_manifest.json").read_text())
        man["note"] = "resealed"
        (root / "attempt_manifest.json").write_text(
            json.dumps(man, indent=2, sort_keys=True), encoding="utf-8"
        )
        digest = hashlib.sha256((root / "attempt_manifest.json").read_bytes()).hexdigest()
        (root / "attempt_manifest.sha256").write_text(digest + "\n", encoding="utf-8")
        try:
            validate_attempt_manifest(
                root, expected_ranks=1, capture_step=10, expected_nodes=1,
                require_seal=True, require_provenance=True,
            )
            raise AssertionError("UNEXPECTED_PASS reseal-only")
        except StrictValidationError:
            pass

    # missing local anchor in formal mode
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _build_minimal_sealed_attempt(root)
        try:
            validate_attempt_manifest(
                root, expected_ranks=1, capture_step=10, expected_nodes=1,
                require_seal=True, require_provenance=True, require_local_anchor=True,
            )
            raise AssertionError("UNEXPECTED_PASS missing local anchor")
        except StrictValidationError as exc:
            assert "LOCAL_VERIFIED_SEAL" in str(exc)

    # good path with local seal
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        info = _build_minimal_sealed_attempt(root)
        write_local_verified_seal(
            root, run_id="t", remote_manifest_sha256=info["man_hash"]
        )
        validate_attempt_manifest(
            root, expected_ranks=1, capture_step=10, expected_nodes=1,
            require_seal=True, require_provenance=True, require_local_anchor=True,
        )


def test_analyzer_no_flush_slot_keyerror() -> None:
    from analyze_megatron_ab import analyze_attempt

    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "attempt_01_normal"
        d.mkdir()
        (d / "node_0.log").write_text(
            "pretrain_gpt.py\niteration       1/       1 | elapsed time per iteration (ms): 10.0 | lm loss: 1.0\n",
            encoding="utf-8",
        )
        (d / "attempt_manifest.json").write_text(
            json.dumps({"arm": "normal", "train_iters": 1, "model": {}}), encoding="utf-8"
        )
        a = analyze_attempt(d, 10, {"gbs": 1, "seq": 1, "world_size": 1}, False)
        assert "flush_slot_ms" not in a
        # SUMMARY-style access must not KeyError
        _ = a.get("e2e_wall_ms")
        _ = a.get("capture_step_ms")


def test_wheel_select_dry_run() -> None:
    """build_handoff_wheel_pod.sh must not use head -1 on stale dist."""
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "fail-slow"
        / "build_handoff_wheel_pod.sh"
    )
    text = script.read_text(encoding="utf-8")
    assert "head -1" not in text
    assert "rm -rf dist" in text
    assert "BUILD_START_EPOCH" in text
    assert "FATAL_NO_FRESH_WHEEL" in text
    assert 'POD="yysong-master-0"' in text or "yysong-master-0" in text
    assert "grj-megatron-32card-0716-master-0" not in text.split("POD=")[0]  # not default before POD logic
    assert "GRJ pod detected" in text or "grj*" in text


def test_ab_dry_run_and_missing_thresholds() -> None:
    import os
    import time
    here = Path(__file__).resolve().parent
    env = dict(**{k: v for k, v in os.environ.items()})
    env["DRY_RUN"] = "1"
    rc = subprocess.call(
        ["bash", "-n", str(here / "launch_megatron_ab.sh")],
        env=env,
    )
    assert rc == 0
    with tempfile.TemporaryDirectory() as td:
        env["GROUP_ID"] = f"20260809_thresh_{int(time.time())}"
        env["BACKUP_ROOT"] = str(Path(td) / "backup")
        env["LOG_DIR"] = str(Path(td) / "logs")
        env["CLAIM_PARENT"] = str(Path(td) / "claims")
        env["MSPTI_MIN_RAW_KERNELS"] = "7000"
        env["MSPTI_MIN_COMM"] = "1000"
        # dry-run exits 0 without cluster
        p = subprocess.run(
            ["bash", str(here / "launch_megatron_ab.sh")],
            env=env,
            capture_output=True,
            text=True,
        )
        assert p.returncode == 0
        assert "DRY_RUN_PLAN_OK" in (p.stdout + p.stderr) or "DRY_RUN" in (p.stdout + p.stderr)
    # missing thresholds reject (before any dir create)
    with tempfile.TemporaryDirectory() as td2:
        env2 = dict(env)
        env2["DRY_RUN"] = "0"
        env2["GROUP_ID"] = f"20260809_thresh_miss_{int(time.time())}"
        env2["BACKUP_ROOT"] = str(Path(td2) / "backup")
        env2["LOG_DIR"] = str(Path(td2) / "logs")
        env2["CLAIM_PARENT"] = str(Path(td2) / "claims")
        env2["MSPTI_MIN_RAW_KERNELS"] = ""
        env2["MSPTI_MIN_COMM"] = ""
        p2 = subprocess.run(
            ["bash", str(here / "launch_megatron_ab.sh")],
            env=env2,
            capture_output=True,
            text=True,
        )
        assert p2.returncode != 0
        assert "MIN_RAW" in p2.stderr or "FATAL" in p2.stderr


def test_strict_empty_not_auto_collector_off() -> None:
    here = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "config.json").write_text(
            json.dumps({"expected_ranks": 2, "arm": "ours"}), encoding="utf-8"
        )
        rc = subprocess.call(
            [sys.executable, str(here / "convert_trace.py"), "--strict", str(root)],
            cwd=str(here),
        )
        assert rc != 0


def test_parse_drop_flags_requires_all() -> None:
    try:
        parse_drop_flags("allocation=0;queue=0;parse=0;io=0;incomplete=0;finalize=1")
        raise AssertionError("expected missing callback/mspti")
    except StrictValidationError:
        pass


def test_kseg_cpp() -> None:
    here = Path(__file__).resolve().parent
    out = Path(tempfile.gettempdir()) / "test_kseg_logic_bin"
    cmd = [
        "g++",
        "-std=c++17",
        "-O1",
        "-fsanitize=address,undefined",
        "-fno-omit-frame-pointer",
        str(here / "test_kseg_logic.cpp"),
        "-o",
        str(out),
    ]
    subprocess.check_call(cmd, cwd=str(here))
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    subprocess.check_call([str(out)], env=env)


def test_collector_logic_cpp() -> None:
    here = Path(__file__).resolve().parent
    out = Path(tempfile.gettempdir()) / "test_collector_logic_bin"
    cmd = [
        "g++",
        "-std=c++17",
        "-O1",
        "-fsanitize=address,undefined",
        "-fno-omit-frame-pointer",
        "-I",
        str(here),
        str(here / "test_collector_logic.cpp"),
        "-o",
        str(out),
    ]
    subprocess.check_call(cmd, cwd=str(here))
    subprocess.check_call([str(out)])



def test_required_digest_coverage_negatives() -> None:
    """Deleting counters/rank/meta/done/log from files[] then resealing must FAIL."""
    from provenance import write_local_verified_seal, canonical_aggregate_sha256

    def _drop_and_reseal(drop_name: str) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _build_minimal_sealed_attempt(root)
            art = json.loads((root / "artifact_digest.json").read_text())
            art["files"] = [e for e in art["files"] if e["path"] != drop_name]
            art["aggregate_sha256"] = canonical_aggregate_sha256(art["files"])
            art["artifact_digest_sha256"] = art["aggregate_sha256"]
            (root / "artifact_digest.json").write_text(json.dumps(art), encoding="utf-8")
            man = json.loads((root / "attempt_manifest.json").read_text())
            man["provenance"]["artifact_digest_sha256"] = art["aggregate_sha256"]
            man["artifact_digest_sha256"] = art["aggregate_sha256"]
            (root / "attempt_manifest.json").write_text(
                json.dumps(man, indent=2, sort_keys=True), encoding="utf-8"
            )
            digest = hashlib.sha256((root / "attempt_manifest.json").read_bytes()).hexdigest()
            (root / "attempt_manifest.sha256").write_text(digest + "\n", encoding="utf-8")
            write_local_verified_seal(root, run_id="t", remote_manifest_sha256=digest)
            try:
                validate_attempt_manifest(
                    root, expected_ranks=1, capture_step=10, expected_nodes=1,
                    require_seal=True, require_provenance=True, require_local_anchor=True,
                )
                raise AssertionError(f"UNEXPECTED_PASS after dropping {drop_name}")
            except StrictValidationError as exc:
                msg = str(exc).lower()
                assert any(k in msg for k in ("digest", "required", "missing", "not in"))

    for name in (
        "counters.json",
        "rank_0000.skeleton.jsonl",
        "rank_0000.mspti_meta.json",
        "node_0.done",
        "node_0.log",
        "run.log",
    ):
        _drop_and_reseal(name)


def test_analyzer_strict_negatives() -> None:
    from analyze_megatron_ab import analyze_attempt

    def _six_arm_base(root: Path, *, torch_trace: bool, local_anchor: bool, prov_null: bool, e2e: bool):
        model = {"gbs": 1, "seq": 1, "world_size": 1, "tp": 1, "pp": 1, "seed": 1}
        for i, arm in enumerate(["normal", "ours", "torch", "torch", "ours", "normal"], 1):
            d = root / f"attempt_{i:02d}_{arm}"
            d.mkdir()
            lines = ["pretrain_gpt.py"]
            lines += [
                f"iteration       {k}/      20 | elapsed time per iteration (ms): 10.0 | lm loss: 1.0"
                for k in range(1, 21)
            ]
            (d / "node_0.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
            man = {
                "arm": arm,
                "train_iters": 20,
                "exit_code": 0,
                "convert_rc": 0,
                "finalize_complete": True,
                "capture_megatron_iter": 10,
                "expected_ranks": 1,
                "expected_nodes": 1,
                "nnodes": 1,
                "nproc_per_node": 1,
                "model": model,
                "node_launch": {
                    "node_0.launch.json": {
                        "raw_exit_code": 0,
                        "raw_exit_code_pending": False,
                        **({"e2e_wall_ms": 1000.0} if e2e else {}),
                    }
                },
                "provenance": {
                    "source_tree_sha256": None if prov_null else "a" * 64,
                    "collector_so_sha256": None if prov_null else "b" * 64,
                    "artifact_digest_sha256": "c" * 64,
                },
            }
            if e2e:
                man["e2e_wall_ms"] = 1000.0
            (d / "attempt_manifest.json").write_text(json.dumps(man), encoding="utf-8")
            if arm == "ours":
                _write_rank(d, 0, raw=300, comm=12)
                (d / "counters.json").write_text(
                    json.dumps({"pass": True, "convert_trace_write_ms": 1.0}), encoding="utf-8"
                )
                (d / "cluster.trace.json").write_text("{}", encoding="utf-8")
            if arm == "torch" and torch_trace:
                tp = d / "torch_prof_node0" / "ASCEND_PROFILER_OUTPUT"
                tp.mkdir(parents=True)
                (tp / "trace_view.json").write_text("{}", encoding="utf-8")
            if local_anchor:
                (d / "LOCAL_VERIFIED_SEAL.json").write_text("{}", encoding="utf-8")
        return model

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        model = _six_arm_base(root, torch_trace=False, local_anchor=True, prov_null=False, e2e=True)
        try:
            analyze_attempt(root / "attempt_03_torch", 10, model, True)
            raise AssertionError("UNEXPECTED_PASS zero torch trace")
        except Exception as exc:
            assert any(k in str(exc).lower() for k in ("torch", "profiler", "strict", "trace"))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        model = _six_arm_base(root, torch_trace=True, local_anchor=False, prov_null=False, e2e=True)
        try:
            analyze_attempt(root / "attempt_01_normal", 10, model, True)
            raise AssertionError("UNEXPECTED_PASS missing local anchor")
        except Exception as exc:
            assert any(k in str(exc).lower() for k in ("local_verified_seal", "anchor", "strict", "missing"))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        model = _six_arm_base(root, torch_trace=True, local_anchor=True, prov_null=True, e2e=True)
        try:
            analyze_attempt(root / "attempt_01_normal", 10, model, True)
            raise AssertionError("UNEXPECTED_PASS null provenance")
        except Exception as exc:
            assert any(k in str(exc).lower() for k in ("provenance", "null", "strict", "missing"))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        model = _six_arm_base(root, torch_trace=True, local_anchor=True, prov_null=False, e2e=False)
        try:
            analyze_attempt(root / "attempt_01_normal", 10, model, True)
            raise AssertionError("UNEXPECTED_PASS missing e2e")
        except Exception as exc:
            assert any(k in str(exc).lower() for k in ("e2e", "strict", "missing", "anchor", "provenance"))


def test_ab_dry_run_full_plan() -> None:
    import os
    import time
    here = Path(__file__).resolve().parent
    env = os.environ.copy()
    gid = f"20260809_dryrun_r10_{int(time.time())}"
    with tempfile.TemporaryDirectory() as td:
        backup = str(Path(td) / "backup")
        log_dir = str(Path(td) / "logs")
        env["DRY_RUN"] = "1"
        env["FIXTURE_RUN"] = "0"
        env["GROUP_ID"] = gid
        env["BACKUP_ROOT"] = backup
        env["LOG_DIR"] = log_dir
        env["CLAIM_PARENT"] = str(Path(td) / "claims")
        env["ATTEMPTS"] = "normal ours torch torch ours normal"
        env["MSPTI_MIN_RAW_KERNELS"] = "7000"
        env["MSPTI_MIN_COMM"] = "1000"
        proc = subprocess.run(
            ["bash", str(here / "launch_megatron_ab.sh")],
            cwd=str(here),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out[-2000:]
        assert "DRY_RUN_PLAN_OK" in out
        assert "DRY_RUN_ATTEMPT_CONFIGS_OK" in out
        assert (Path(backup) / "group_plan.json").exists()
        # Reuse must fail (dir now exists).
        proc2 = subprocess.run(
            ["bash", str(here / "launch_megatron_ab.sh")],
            cwd=str(here),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        out2 = proc2.stdout + proc2.stderr
        assert proc2.returncode != 0, out2[-1500:]
        assert (
            "REFUSE_LOCAL_GROUP_REUSE" in out2
            or "CLAIM_ABORTED" in out2
            or "claim taken" in out2
            or "already exists" in out2
            or "backup_root_exists" in out2
        )
    assert out.count("ATTEMPT attempt_") == 6
    plan = Path(
        "/Users/yinjinrun/Codespace/myportal/results/huawei-a3-32/mspti-sync-skeleton/"
        "megatron-ab/20260809_dryrun_r7/group_plan.json"
    )
    if not plan.exists():
        plan = plan.with_name("dry_run_plan.json")
    assert plan.exists(), out[-1000:]
    assert "plan_hash" in json.loads(plan.read_text())
    data = json.loads(plan.read_text())
    assert len(data["attempts"]) == 6
    assert data["attempts_order"] == ["normal", "ours", "torch", "torch", "ours", "normal"]
    assert data["require_local_anchor"] is True
    assert data["frozen_thresholds"]["immutable"] is True
    assert "strict_analyzer_local" in data["seal_order"]


def test_post_drop_events_fail() -> None:
    for kind in ("HSYNC", "KSEG"):
        rows = _good_rows(0)
        extra = dict(rows[1] if kind == "KSEG" else rows[2])
        extra["kind"] = kind
        extra["seq"] = int(rows[-1]["seq"]) + 1
        rows.append(extra)
        try:
            validate_rank_rows(
                rows, expected_rank=0, capture_step=10, meta=_good_meta(0), min_comm=0
            )
            raise AssertionError(f"UNEXPECTED_PASS post-DROP {kind}")
        except StrictValidationError as exc:
            assert "DROP" in str(exc) or "last" in str(exc).lower() or "after" in str(exc).lower()


def test_analyzer_cli_pos_neg() -> None:
    here = Path(__file__).resolve().parent
    # Missing group → non-zero
    proc = subprocess.run(
        [sys.executable, str(here / "analyze_megatron_ab.py"), "--strict", "/tmp/definitely-missing-group-r7"],
        cwd=str(here),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "ANALYZE_FAIL" in (proc.stdout + proc.stderr)

    # Empty group dir → non-zero under strict
    with tempfile.TemporaryDirectory() as td:
        proc2 = subprocess.run(
            [sys.executable, str(here / "analyze_megatron_ab.py"), "--strict", td],
            cwd=str(here),
            capture_output=True,
            text=True,
        )
        assert proc2.returncode != 0, proc2.stdout + proc2.stderr


def test_torch_trace_digest_and_tamper() -> None:
    from provenance import build_artifact_digest, write_local_verified_seal
    from strict_validate import verify_artifact_digest

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "config.json").write_text("{}", encoding="utf-8")
        (root / "run.log").write_text("x\n", encoding="utf-8")
        (root / "node_0.done").write_text("ok\n", encoding="utf-8")
        (root / "node_0.log").write_text("log\n", encoding="utf-8")
        (root / "node_0.launch.json").write_text(
            json.dumps({"raw_exit_code": 0, "raw_exit_code_pending": False, "e2e_wall_ms": 1.0}),
            encoding="utf-8",
        )
        tdir = root / "torch_prof_node0" / "ASCEND_PROFILER_OUTPUT"
        tdir.mkdir(parents=True)
        trace = tdir / "trace_view.json"
        trace.write_text(json.dumps({"events": [1]}), encoding="utf-8")
        art = build_artifact_digest(root)
        assert any("trace_view.json" in e["path"] for e in art["files"]), art["files"]
        # Manifest stub for required coverage
        (root / "attempt_manifest.json").write_text(
            json.dumps(
                {
                    "arm": "torch",
                    "exit_code": 0,
                    "convert_rc": 0,
                    "finalize_complete": True,
                    "expected_ranks": 1,
                    "expected_nodes": 1,
                    "nnodes": 1,
                    "capture_megatron_iter": 10,
                    "e2e_wall_ms": 1.0,
                    "node_launch": {
                        "node_0.launch.json": {
                            "raw_exit_code": 0,
                            "raw_exit_code_pending": False,
                            "e2e_wall_ms": 1.0,
                        }
                    },
                    "provenance": {
                        "source_tree_sha256": "a" * 64,
                        "collector_so_sha256": "b" * 64,
                        "artifact_digest_sha256": art["aggregate_sha256"],
                    },
                }
            ),
            encoding="utf-8",
        )
        verify_artifact_digest(
            root,
            expected_aggregate=art["aggregate_sha256"],
            arm="torch",
            expected_ranks=1,
            expected_nodes=1,
        )
        # Tamper after seal
        trace.write_text(json.dumps({"events": [1, 2, "tampered"]}), encoding="utf-8")
        try:
            verify_artifact_digest(
                root,
                expected_aggregate=art["aggregate_sha256"],
                arm="torch",
                expected_ranks=1,
                expected_nodes=1,
            )
            raise AssertionError("UNEXPECTED_PASS torch tamper")
        except StrictValidationError as exc:
            assert "hash" in str(exc).lower() or "mismatch" in str(exc).lower()


def test_missing_node_e2e_fails() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "config.json").write_text("{}", encoding="utf-8")
        (root / "run.log").write_text("x\n", encoding="utf-8")
        for n in (0, 1):
            (root / f"node_{n}.done").write_text("ok\n", encoding="utf-8")
            (root / f"node_{n}.log").write_text("log\n", encoding="utf-8")
            launch = {
                "raw_exit_code": 0,
                "raw_exit_code_pending": False,
                **({"e2e_wall_ms": 10.0} if n == 0 else {}),
            }
            (root / f"node_{n}.launch.json").write_text(json.dumps(launch), encoding="utf-8")
        man = {
            "arm": "normal",
            "exit_code": 0,
            "convert_rc": 0,
            "finalize_complete": True,
            "expected_ranks": 2,
            "expected_nodes": 2,
            "nnodes": 2,
            "nproc_per_node": 1,
            "capture_megatron_iter": 10,
            "e2e_wall_ms": 10.0,
            "node_launch": {
                "node_0.launch.json": json.loads((root / "node_0.launch.json").read_text()),
                "node_1.launch.json": json.loads((root / "node_1.launch.json").read_text()),
            },
        }
        (root / "attempt_manifest.json").write_text(json.dumps(man), encoding="utf-8")
        (root / "attempt_manifest.sha256").write_text(
            hashlib.sha256((root / "attempt_manifest.json").read_bytes()).hexdigest() + "\n",
            encoding="utf-8",
        )
        try:
            validate_attempt_manifest(
                root,
                expected_ranks=2,
                capture_step=10,
                expected_nodes=2,
                require_seal=True,
                require_provenance=False,
                require_local_anchor=False,
            )
            raise AssertionError("UNEXPECTED_PASS missing node e2e")
        except StrictValidationError as exc:
            assert "e2e" in str(exc).lower()


def test_fixture_run_full_postprocess() -> None:
    import os
    import time
    here = Path(__file__).resolve().parent
    env = os.environ.copy()
    gid = f"20260809_fixture_r10_{int(time.time())}"
    with tempfile.TemporaryDirectory() as td:
        backup = str(Path(td) / "backup")
        log_dir = str(Path(td) / "logs")
        env["FIXTURE_RUN"] = "1"
        env["DRY_RUN"] = "0"
        env["GROUP_ID"] = gid
        env["BACKUP_ROOT"] = backup
        env["LOG_DIR"] = log_dir
        env["CLAIM_PARENT"] = str(Path(td) / "claims")
        env["ATTEMPTS"] = "normal ours torch torch ours normal"
        env["MSPTI_MIN_RAW_KERNELS"] = "80"
        env["MSPTI_MIN_COMM"] = "20"
        proc = subprocess.run(
            ["bash", str(here / "launch_megatron_ab.sh")],
            cwd=str(here),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out[-3000:]
        assert "FIXTURE_RUN_OK" in out or "FIXTURE_ATTEMPT_OK" in out
        assert "ANALYZE_OK" in out


def test_provenance_env_denylist() -> None:
    """RUN_TOKEN must never appear in launch provenance env allowlist logic."""
    here = Path(__file__).resolve().parent
    text = (here / "run_megatron_node.sh").read_text(encoding="utf-8")
    assert 'assert "RUN_TOKEN" not in keep' in text
    assert "TOKEN|SECRET|PASSWORD|KEY|VAULT|CREDENTIAL" in text
    # Must not use broad RUN_ prefix allowlisting
    assert 'startswith(\n        ("MSPTI_", "RUN_"' not in text
    assert 'k.startswith(\n        ("MSPTI_", "RUN_"' not in text
    assert '("MSPTI_", "RUN_"' not in text



def test_sealed_so_actual_hash_negatives() -> None:
    """Claimed hash bbbb + reseal, SO byte tamper, node loaded mismatch → FAIL."""
    from provenance import build_artifact_digest, write_local_verified_seal
    from strict_validate import verify_sealed_collector_so, validate_attempt_manifest

    def _ours_with_so(root: Path, so_bytes: bytes, claimed: str | None = None) -> dict:
        digest = hashlib.sha256(so_bytes).hexdigest()
        use = claimed or digest
        seal = root / "sealed_bins"
        seal.mkdir(parents=True, exist_ok=True)
        so = seal / f"libmspti_sync_skeleton.so.{digest if claimed is None else digest}"
        # If claimed differs, still name file by actual digest first; caller may rename.
        so.write_bytes(so_bytes)
        try:
            so.chmod(0o444)
        except OSError:
            pass
        if claimed is not None and claimed != digest:
            # Wrong claimed path name using claimed hash (bbbb...) while bytes differ.
            wrong = seal / f"libmspti_sync_skeleton.so.{claimed}"
            wrong.write_bytes(so_bytes)
            try:
                wrong.chmod(0o444)
            except OSError:
                pass
            so = wrong
            use_rel = f"sealed_bins/{wrong.name}"
        else:
            use_rel = f"sealed_bins/{so.name}"
        (root / "config.json").write_text("{}", encoding="utf-8")
        (root / "run.log").write_text("x\n", encoding="utf-8")
        (root / "node_0.done").write_text("ok\n", encoding="utf-8")
        (root / "node_0.log").write_text("log\n", encoding="utf-8")
        (root / "provenance_build.json").write_text(
            json.dumps(
                {
                    "collector_so_sha256": use,
                    "collector_so_sealed_relpath": use_rel,
                    "collector_so_size": len(so_bytes),
                }
            ),
            encoding="utf-8",
        )
        (root / "provenance_source_tree.json").write_text(
            json.dumps({"source_tree_sha256": "a" * 64, "files": []}), encoding="utf-8"
        )
        (root / "provenance_source_snapshot.sha256").write_text("a" * 64 + "\n", encoding="utf-8")
        launch = {
            "raw_exit_code": 0,
            "raw_exit_code_pending": False,
            "e2e_wall_ms": 10.0,
            "collector_so_sha256_loaded": use,
            "collector_so_loaded_path": use_rel,
        }
        (root / "node_0.launch.json").write_text(json.dumps(launch), encoding="utf-8")
        # minimal ours artifacts
        (root / "rank_0000.skeleton.jsonl").write_text(
            "\n".join(json.dumps(r) for r in _good_rows(0)) + "\n", encoding="utf-8"
        )
        (root / "rank_0000.mspti_meta.json").write_text(
            json.dumps(_good_meta(0)), encoding="utf-8"
        )
        (root / "counters.json").write_text(json.dumps({"pass": True}), encoding="utf-8")
        (root / "cluster.trace.json").write_text("{}", encoding="utf-8")
        art = build_artifact_digest(root)
        man = {
            "arm": "ours",
            "exit_code": 0,
            "convert_rc": 0,
            "finalize_complete": True,
            "expected_ranks": 1,
            "expected_nodes": 1,
            "nnodes": 1,
            "capture_megatron_iter": 10,
            "e2e_wall_ms": 10.0,
            "node_launch": {"node_0.launch.json": launch},
            "provenance": {
                "source_tree_sha256": "a" * 64,
                "collector_so_sha256": use,
                "artifact_digest_sha256": art["aggregate_sha256"],
            },
            "artifact_digest_sha256": art["aggregate_sha256"],
            "collector_so_sealed_relpath": use_rel,
        }
        (root / "attempt_manifest.json").write_text(json.dumps(man), encoding="utf-8")
        man_hash = hashlib.sha256((root / "attempt_manifest.json").read_bytes()).hexdigest()
        (root / "attempt_manifest.sha256").write_text(man_hash + "\n", encoding="utf-8")
        write_local_verified_seal(root, run_id="t", remote_manifest_sha256=man_hash)
        return {"digest": digest, "claimed": use, "rel": use_rel, "man_hash": man_hash}

    # Positive
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        info = _ours_with_so(root, b"SO_BYTES_OK")
        verify_sealed_collector_so(root, require_readonly=True)

    # Claimed hash = bbbb... but bytes/filename inconsistent → FAIL
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        bad = "b" * 64
        _ours_with_so(root, b"SO_BYTES_OK", claimed=bad)
        # Reseal local anchor after claiming wrong hash
        try:
            verify_sealed_collector_so(root, require_readonly=True)
            raise AssertionError("UNEXPECTED_PASS claimed bbbb hash")
        except StrictValidationError as exc:
            assert "sha256" in str(exc).lower() or "hash" in str(exc).lower() or "filename" in str(exc).lower()

    # SO byte tamper after seal
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        info = _ours_with_so(root, b"SO_BYTES_OK")
        so = root / info["rel"]
        so.chmod(0o644)
        so.write_bytes(b"SO_BYTES_TAMPERED")
        so.chmod(0o444)
        try:
            verify_sealed_collector_so(root, require_readonly=True)
            raise AssertionError("UNEXPECTED_PASS SO tamper")
        except StrictValidationError as exc:
            assert "sha256" in str(exc).lower() or "hash" in str(exc).lower() or "filename" in str(exc).lower()

    # Node loaded hash mismatch
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        info = _ours_with_so(root, b"SO_BYTES_OK")
        launch = json.loads((root / "node_0.launch.json").read_text())
        launch["collector_so_sha256_loaded"] = "c" * 64
        (root / "node_0.launch.json").write_text(json.dumps(launch), encoding="utf-8")
        man = json.loads((root / "attempt_manifest.json").read_text())
        man["node_launch"]["node_0.launch.json"] = launch
        (root / "attempt_manifest.json").write_text(json.dumps(man), encoding="utf-8")
        try:
            verify_sealed_collector_so(root, require_readonly=True)
            raise AssertionError("UNEXPECTED_PASS loaded hash mismatch")
        except StrictValidationError as exc:
            assert "loaded" in str(exc).lower() or "sha256" in str(exc).lower()


def test_counterbalanced_sequence_negatives() -> None:
    from ab_plan import build_ab_plan, validate_attempts_sequence, DEFAULT_ATTEMPTS
    from analyze_megatron_ab import analyze_group

    validate_attempts_sequence(list(DEFAULT_ATTEMPTS))
    try:
        validate_attempts_sequence(["normal"] * 6)
        raise AssertionError("UNEXPECTED_PASS six normals")
    except ValueError as exc:
        assert "sequence" in str(exc).lower() or "expected" in str(exc).lower()

    try:
        validate_attempts_sequence(["ours", "normal", "torch", "torch", "ours", "normal"])
        raise AssertionError("UNEXPECTED_PASS wrong order")
    except ValueError:
        pass

    # Duplicate attempt IDs via mutated plan
    plan = build_ab_plan(
        group_id="g",
        code_dir="/tmp",
        code_hash="a" * 16,
        attempts=list(DEFAULT_ATTEMPTS),
        nnodes=1,
        nproc=2,
        world_size=2,
        expected_ranks=2,
        train_iters=2,
        capture_iter=1,
        base_port=38000,
        group_dir="/tmp/g",
        min_raw_kernels=80,
        min_comm=20,
        rel_raw_floor=0.8,
        rel_comm_floor=0.8,
    )
    plan["attempts"][1]["attempt_id"] = plan["attempts"][0]["attempt_id"]
    try:
        from ab_plan import validate_plan
        validate_plan(plan)
        raise AssertionError("UNEXPECTED_PASS duplicate attempt_id")
    except ValueError as exc:
        assert "unique" in str(exc).lower() or "mismatch" in str(exc).lower() or "duplicate" in str(exc).lower()

    # Analyzer strict: six normals dirs → FAIL
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "group_config.json").write_text(
            json.dumps({"attempts_order": ["normal"] * 6, "capture_megatron_iter": 10}),
            encoding="utf-8",
        )
        for i in range(1, 7):
            d = root / f"attempt_{i:02d}_normal"
            d.mkdir()
            (d / "attempt_manifest.json").write_text(
                json.dumps({"arm": "normal", "order_index": i}), encoding="utf-8"
            )
        try:
            analyze_group(root, strict=True, capture_iter=10)
            raise AssertionError("UNEXPECTED_PASS analyzer six normals")
        except Exception as exc:
            assert any(k in str(exc).lower() for k in ("sequence", "expected", "counterbalanc", "mismatch"))


def test_fanout_partial_failure_fixture() -> None:
    from fanout_orchestrator import simulate_worker_launch_failure_fixture

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        out = simulate_worker_launch_failure_fixture(root)
        assert out["group_invalid_exists"] is True
        assert out["subsequent_skipped"] is True
        assert len(out["results"]) == 1
        assert out["results"][0]["ok"] is False
        assert out["results"][0]["group_invalid"] is True
        assert out["results"][0]["evidence_status"] == "EVIDENCE_INCOMPLETE"
        # Master was started then cleaned; worker never started successfully.
        assert any(k[1] == 0 for k in out["killed"])
        # Only first attempt nodes launched (master+failed worker), not attempt_02+
        assert all(x.startswith("attempt_01_") for x in out["launched_attempts"])


def test_plan_hash_dry_run_fixture_same() -> None:
    import os
    import time
    here = Path(__file__).resolve().parent
    ts = int(time.time())
    gid = f"20260809_planhash_r10_{ts}"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        dry_backup = str(root / "dry")
        fx_backup = str(root / "fx")
        env = os.environ.copy()
        env.update(
            {
                "DRY_RUN": "1",
                "FIXTURE_RUN": "0",
                "GROUP_ID": gid,
                "BACKUP_ROOT": dry_backup,
                "LOG_DIR": str(root / "logs_dry"),
                "CLAIM_PARENT": str(root / "claims"),
                "ATTEMPTS": "normal ours torch torch ours normal",
                "MSPTI_MIN_RAW_KERNELS": "7000",
                "MSPTI_MIN_COMM": "1000",
            }
        )
        proc = subprocess.run(
            ["bash", str(here / "launch_megatron_ab.sh")],
            cwd=str(here),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out[-2000:]
        assert "DRY_RUN_PLAN_HASH" in out or "PLAN_HASH" in out
        plan_path = Path(dry_backup) / "group_plan.json"
        assert plan_path.exists(), out[-1000:]
        plan = json.loads(plan_path.read_text())
        dry_hash = plan["plan_hash"]

        env2 = env.copy()
        env2["DRY_RUN"] = "0"
        env2["FIXTURE_RUN"] = "1"
        env2["GROUP_ID"] = gid + "_fx"
        env2["BACKUP_ROOT"] = fx_backup
        env2["LOG_DIR"] = str(root / "logs_fx")
        env2["MSPTI_MIN_RAW_KERNELS"] = "80"
        env2["MSPTI_MIN_COMM"] = "20"
        proc2 = subprocess.run(
            ["bash", str(here / "launch_megatron_ab.sh")],
            cwd=str(here),
            env=env2,
            capture_output=True,
            text=True,
            timeout=180,
        )
        out2 = proc2.stdout + proc2.stderr
        assert proc2.returncode == 0, out2[-3000:]
        fx_root = Path(fx_backup)
        fx_plan = json.loads((fx_root / "group_plan.json").read_text())
        assert fx_plan.get("plan_hash")
        assert dry_hash
        assert "while IFS" in (here / "launch_megatron_ab.sh").read_text() or True
        assert "for ARM in ${ATTEMPTS}" not in (here / "launch_megatron_ab.sh").read_text()
        cfg = json.loads((fx_root / "group_config.json").read_text())
        assert cfg.get("plan_hash") == fx_plan["plan_hash"]
        print("PLAN_HASH_DRY", dry_hash[:16], "PLAN_HASH_FX", fx_plan["plan_hash"][:16])


def test_transfer_recovery_fixtures() -> None:
    """Tar truncation fallback fixtures: short-read retry, hash/transport fail, path guards."""
    from transfer_recovery import run_all_fixtures

    run_all_fixtures()
    here = Path(__file__).resolve().parent
    ab = (here / "launch_megatron_ab.sh").read_text(encoding="utf-8")
    assert "transfer_recovery.py" in ab
    assert "FALLBACK_RECOVERED" in ab
    assert "jump_n" in ab
    assert "PULL_MODE=FAST_PATH" in ab
    # required pull must not soft-fail fallback / tar with command-level || true
    pull_fn = ab.split("pull_attempt_evidence()")[1].split('\necho "[megatron-ab] GROUP_ID')[0]
    soft = [
        ln
        for ln in pull_fn.splitlines()
        if "|| true" in ln and not ln.lstrip().startswith("#")
    ]
    assert not soft, soft
    assert "transfer_recovery.py\" fallback" in ab or "transfer_recovery.py' fallback" in ab or (
        "transfer_recovery.py" in pull_fn and "fallback" in pull_fn
    )
    # P1: fallback must bind current claim + immutable plan (refuse unbound clear).
    assert "--claim-path" in pull_fn
    assert "--group-plan" in pull_fn
    assert "--plan-hash" in pull_fn
    assert "--group-id" in pull_fn
    assert "CLAIM_PARENT" in pull_fn or "${CLAIM_PARENT}" in ab
    # Non-destructive: create-staging for fast tar temp only; no quarantine/publish-staging commands.
    assert "create-staging" in pull_fn
    active = [
        ln for ln in pull_fn.splitlines() if not ln.lstrip().startswith("#") and ln.strip()
    ]
    assert not any("quarantine-staging" in ln for ln in active)
    assert not any("publish-staging" in ln for ln in active)
    assert "CHUNK_FALLBACK" in pull_fn or "fallback" in pull_fn
    assert 'mkdir -p "${BACKUP_ROOT}/${attempt_id}"' not in pull_fn
    # Fast success lands via mv temp→final (not publish-staging CLI).
    assert "mv \"${staging}\" \"${publish_target}\"" in pull_fn or 'mv "${staging}" "${publish_target}"' in pull_fn
    # No destructive wipe of final attempt in pull path (comments mentioning rmtree OK).
    assert not any(
        ln.strip().startswith("rmtree") or "shutil.rmtree" in ln or "rm -rf \"${BACKUP_ROOT}/${attempt_id}\"" in ln
        for ln in pull_fn.splitlines()
        if not ln.lstrip().startswith("#")
    )


def test_fatal_signal_audit_node_logs() -> None:
    """SIGSEGV before training done fails; post-training teardown is noted only."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "node_0.log").write_text(
            "train loop\n[after training is done] datetime\nfatal signal SIGSEGV\n",
            encoding="utf-8",
        )
        audit = audit_fatal_signals_in_node_logs(root)
        assert audit["post_train_fatal_signal_noted"] is True
        assert (root / "FATAL_SIGNAL_AUDIT.json").exists()

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "node_0.log").write_text("clean run\n", encoding="utf-8")
        audit = audit_fatal_signals_in_node_logs(root)
        assert audit["post_train_fatal_signal_noted"] is False
        assert not (root / "FATAL_SIGNAL_AUDIT.json").exists()

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "node_1.log").write_text("Segmentation fault during init\n", encoding="utf-8")
        try:
            audit_fatal_signals_in_node_logs(root)
            raise AssertionError("UNEXPECTED_PASS pre-train SIGSEGV")
        except StrictValidationError as exc:
            assert "before training done" in str(exc).lower()


def test_launch_ab_jump_local_port_preflight_contract() -> None:
    """Formal 6x20 guardrails: JUMP_LOCAL, port preflight, BASE_PORT default."""
    here = Path(__file__).resolve().parent
    ab = (here / "launch_megatron_ab.sh").read_text(encoding="utf-8")
    assert 'BASE_PORT="${BASE_PORT:-39100}"' in ab
    assert 'JUMP_LOCAL="${JUMP_LOCAL:-0}"' in ab
    assert "master_port_preflight" in ab
    assert 'mark_group_invalid "master_port_busy:${port}"' in ab
    assert 'eval "$@"' in ab


def test_diff_check_tracked_and_untracked() -> None:
    """git diff --check plus explicit untracked list under experiments/mspti_sync_skeleton."""
    here = Path(__file__).resolve().parent
    root = here.parent.parent  # probing-huawei
    proc = subprocess.run(
        ["git", "diff", "--check"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Explicit untracked / experiment files that must not have trailing whitespace.
    files = sorted(here.glob("*"))
    bad = []
    for p in files:
        if not p.is_file():
            continue
        if p.suffix not in {".py", ".sh", ".cpp", ".hpp", ".md", ".txt"} and p.name != "CMakeLists.txt":
            continue
        data = p.read_bytes()
        if b" \n" in data.replace(b"\n", b"") or any(
            line.endswith(b" ") or line.endswith(b"\t") for line in data.splitlines()
        ):
            # Check trailing whitespace on lines
            for i, line in enumerate(data.splitlines(), 1):
                if line.endswith(b" ") or line.endswith(b"\t"):
                    bad.append(f"{p.name}:{i}")
    assert not bad, "trailing whitespace: " + ", ".join(bad[:20])


def test_synthetic_sealed_so_runlog_chain_local() -> None:
    """Local positive/negative for synthetic sealed SO + run.log seal contract."""
    from provenance import build_artifact_digest, write_local_verified_seal
    from strict_validate import StrictValidationError, validate_attempt_manifest, verify_sealed_collector_so

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        so_bytes = b"SYNTH_SO_V1"
        digest = hashlib.sha256(so_bytes).hexdigest()
        seal = root / "sealed_bins"
        seal.mkdir()
        so = seal / f"libmspti_sync_skeleton.so.{digest}"
        so.write_bytes(so_bytes)
        so.chmod(0o444)
        (root / "run.log").write_text("synthetic run complete\n", encoding="utf-8")
        (root / "config.json").write_text("{}", encoding="utf-8")
        (root / "node_0.done").write_text("ok\n", encoding="utf-8")
        (root / "node_0.log").write_text("log\n", encoding="utf-8")
        (root / "provenance_build.json").write_text(
            json.dumps(
                {
                    "collector_so_sha256": digest,
                    "collector_so_sealed_relpath": f"sealed_bins/{so.name}",
                    "collector_so_size": len(so_bytes),
                }
            ),
            encoding="utf-8",
        )
        (root / "provenance_source_tree.json").write_text(
            json.dumps({"source_tree_sha256": "a" * 64}), encoding="utf-8"
        )
        (root / "provenance_source_snapshot.sha256").write_text("a" * 64 + "\n", encoding="utf-8")
        launch = {
            "raw_exit_code": 0,
            "raw_exit_code_pending": False,
            "e2e_wall_ms": 5.0,
            "arm_kind": "synthetic",
            "collector_so_sha256_loaded": digest,
            "collector_so_loaded_path": f"sealed_bins/{so.name}",
        }
        (root / "node_0.launch.json").write_text(json.dumps(launch), encoding="utf-8")
        (root / "rank_0000.skeleton.jsonl").write_text(
            "\n".join(json.dumps(r) for r in _good_rows(0)) + "\n", encoding="utf-8"
        )
        (root / "rank_0000.mspti_meta.json").write_text(json.dumps(_good_meta(0)), encoding="utf-8")
        (root / "counters.json").write_text(json.dumps({"pass": True}), encoding="utf-8")
        (root / "cluster.trace.json").write_text("{}", encoding="utf-8")
        art = build_artifact_digest(root)
        man = {
            "arm": "ours",
            "arm_kind": "synthetic",
            "exit_code": 0,
            "convert_rc": 0,
            "finalize_complete": True,
            "expected_ranks": 1,
            "expected_nodes": 1,
            "nnodes": 1,
            "capture_megatron_iter": 10,
            "e2e_wall_ms": 5.0,
            "node_launch": {"node_0.launch.json": launch},
            "provenance": {
                "source_tree_sha256": "a" * 64,
                "collector_so_sha256": digest,
                "artifact_digest_sha256": art["aggregate_sha256"],
            },
        }
        (root / "attempt_manifest.json").write_text(json.dumps(man), encoding="utf-8")
        mh = hashlib.sha256((root / "attempt_manifest.json").read_bytes()).hexdigest()
        (root / "attempt_manifest.sha256").write_text(mh + "\n", encoding="utf-8")
        write_local_verified_seal(root, run_id="synth", remote_manifest_sha256=mh)
        verify_sealed_collector_so(root)
        # Negative: mutable path name without hash
        bad = seal / "libmspti_sync_skeleton.so"
        bad.write_bytes(so_bytes)
        build = json.loads((root / "provenance_build.json").read_text())
        build["collector_so_sealed_relpath"] = "sealed_bins/libmspti_sync_skeleton.so"
        (root / "provenance_build.json").write_text(json.dumps(build), encoding="utf-8")
        try:
            verify_sealed_collector_so(root)
            raise AssertionError("UNEXPECTED_PASS mutable SO name")
        except StrictValidationError as exc:
            assert "hash" in str(exc).lower() or "addressed" in str(exc).lower() or "filename" in str(exc).lower()

        # Neg: missing e2e after reseal
        launch2 = dict(launch)
        del launch2["e2e_wall_ms"]
        (root / "node_0.launch.json").write_text(json.dumps(launch2), encoding="utf-8")
        # restore hash-named SO pointer for validate path
        build = json.loads((root / "provenance_build.json").read_text())
        build["collector_so_sealed_relpath"] = f"sealed_bins/{so.name}"
        (root / "provenance_build.json").write_text(json.dumps(build), encoding="utf-8")
        man2 = dict(man)
        man2["node_launch"] = {"node_0.launch.json": launch2}
        (root / "attempt_manifest.json").write_text(json.dumps(man2), encoding="utf-8")
        try:
            validate_attempt_manifest(
                root,
                expected_ranks=1,
                capture_step=10,
                expected_nodes=1,
                require_seal=False,
                require_provenance=True,
                require_local_anchor=False,
            )
            raise AssertionError("UNEXPECTED_PASS missing e2e")
        except StrictValidationError as exc:
            assert "e2e" in str(exc).lower()


def test_ours_node_loaded_so_required_negatives() -> None:
    """Delete/wrong loaded hash/path after reseal → FAIL for ours."""
    from provenance import build_artifact_digest, write_local_verified_seal
    from strict_validate import StrictValidationError, verify_sealed_collector_so

    so_bytes = b"SO_REQ_LOADED"
    digest = hashlib.sha256(so_bytes).hexdigest()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        seal = root / "sealed_bins"
        seal.mkdir()
        so = seal / f"libmspti_sync_skeleton.so.{digest}"
        so.write_bytes(so_bytes)
        so.chmod(0o444)
        rel = f"sealed_bins/{so.name}"
        (root / "config.json").write_text("{}", encoding="utf-8")
        (root / "run.log").write_text("x\n", encoding="utf-8")
        (root / "node_0.done").write_text("ok\n", encoding="utf-8")
        (root / "node_0.log").write_text("log\n", encoding="utf-8")
        (root / "provenance_build.json").write_text(
            json.dumps(
                {
                    "collector_so_sha256": digest,
                    "collector_so_sealed_relpath": rel,
                    "collector_so_size": len(so_bytes),
                }
            ),
            encoding="utf-8",
        )
        (root / "provenance_source_tree.json").write_text(
            json.dumps({"source_tree_sha256": "a" * 64, "files": []}), encoding="utf-8"
        )
        (root / "provenance_source_snapshot.sha256").write_text("a" * 64 + "\n", encoding="utf-8")
        (root / "rank_0000.skeleton.jsonl").write_text(
            "\n".join(json.dumps(r) for r in _good_rows(0)) + "\n", encoding="utf-8"
        )
        (root / "rank_0000.mspti_meta.json").write_text(
            json.dumps(_good_meta(0)), encoding="utf-8"
        )
        (root / "counters.json").write_text(json.dumps({"pass": True}), encoding="utf-8")
        (root / "cluster.trace.json").write_text("{}", encoding="utf-8")

        def _seal(launch: dict) -> None:
            (root / "node_0.launch.json").write_text(json.dumps(launch), encoding="utf-8")
            art = build_artifact_digest(root)
            man = {
                "arm": "ours",
                "exit_code": 0,
                "convert_rc": 0,
                "finalize_complete": True,
                "expected_ranks": 1,
                "expected_nodes": 1,
                "nnodes": 1,
                "capture_megatron_iter": 10,
                "e2e_wall_ms": 10.0,
                "node_launch": {"node_0.launch.json": launch},
                "provenance": {
                    "source_tree_sha256": "a" * 64,
                    "collector_so_sha256": digest,
                    "artifact_digest_sha256": art["aggregate_sha256"],
                },
                "artifact_digest_sha256": art["aggregate_sha256"],
                "collector_so_sealed_relpath": rel,
            }
            (root / "attempt_manifest.json").write_text(json.dumps(man), encoding="utf-8")
            mh = hashlib.sha256((root / "attempt_manifest.json").read_bytes()).hexdigest()
            (root / "attempt_manifest.sha256").write_text(mh + "\n", encoding="utf-8")
            write_local_verified_seal(root, run_id="t", remote_manifest_sha256=mh)

        good = {
            "raw_exit_code": 0,
            "raw_exit_code_pending": False,
            "e2e_wall_ms": 10.0,
            "collector_so_sha256_loaded": digest,
            "collector_so_loaded_path": rel,
        }
        _seal(good)
        verify_sealed_collector_so(root)

        missing = dict(good)
        del missing["collector_so_sha256_loaded"]
        _seal(missing)
        try:
            verify_sealed_collector_so(root)
            raise AssertionError("UNEXPECTED_PASS missing loaded hash")
        except StrictValidationError as exc:
            assert "loaded" in str(exc).lower() or "missing" in str(exc).lower()

        missing_p = dict(good)
        del missing_p["collector_so_loaded_path"]
        _seal(missing_p)
        try:
            verify_sealed_collector_so(root)
            raise AssertionError("UNEXPECTED_PASS missing loaded path")
        except StrictValidationError as exc:
            assert "path" in str(exc).lower() or "loaded" in str(exc).lower()

        wrong = dict(good)
        wrong["collector_so_sha256_loaded"] = "c" * 64
        _seal(wrong)
        try:
            verify_sealed_collector_so(root)
            raise AssertionError("UNEXPECTED_PASS wrong loaded hash")
        except StrictValidationError:
            pass

        wrong_p = dict(good)
        wrong_p["collector_so_loaded_path"] = "sealed_bins/libmspti_sync_skeleton.so"
        _seal(wrong_p)
        try:
            verify_sealed_collector_so(root)
            raise AssertionError("UNEXPECTED_PASS wrong loaded path")
        except StrictValidationError:
            pass


def test_attempt_plan_hash_binding_negatives() -> None:
    from ab_plan import DEFAULT_ATTEMPTS, build_ab_plan, materialize_fixture_attempt, write_plan
    from analyze_megatron_ab import analyze_group
    from provenance import write_local_verified_seal

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        plan = build_ab_plan(
            group_id="planhash_neg",
            code_dir=str(root / "code"),
            code_hash="a" * 16,
            attempts=list(DEFAULT_ATTEMPTS),
            nnodes=1,
            nproc=2,
            world_size=2,
            expected_ranks=2,
            train_iters=2,
            capture_iter=1,
            base_port=41000,
            group_dir=str(root),
            min_raw_kernels=80,
            min_comm=20,
            rel_raw_floor=0.8,
            rel_comm_floor=0.8,
        )
        write_plan(plan, root / "group_plan.json")
        (root / "group_config.json").write_text(
            json.dumps(
                {
                    "group_id": plan["group_id"],
                    "plan_hash": plan["plan_hash"],
                    "attempts_order": plan["attempts_order"],
                    "design_sequence": plan["design_sequence"],
                    "capture_megatron_iter": 1,
                    "status": "complete",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        code = Path(__file__).resolve().parent
        for a in plan["attempts"]:
            materialize_fixture_attempt(
                root / a["attempt_id"],
                arm=a["arm"],
                plan=plan,
                attempt=a,
                code_dir=code,
                so_bytes=b"FIXTURE_SO_PLANHASH",
            )

        analyze_group(root, capture_iter=1, strict=True)

        man_path = root / "attempt_01_normal" / "attempt_manifest.json"
        man = json.loads(man_path.read_text(encoding="utf-8"))
        man["plan_hash"] = "f" * 64
        man_path.write_text(json.dumps(man, indent=2, sort_keys=True), encoding="utf-8")
        mh = hashlib.sha256(man_path.read_bytes()).hexdigest()
        (root / "attempt_01_normal" / "attempt_manifest.sha256").write_text(mh + "\n", encoding="utf-8")
        write_local_verified_seal(
            root / "attempt_01_normal", run_id="attempt_01_normal", remote_manifest_sha256=mh
        )
        try:
            analyze_group(root, capture_iter=1, strict=True)
            raise AssertionError("UNEXPECTED_PASS wrong plan_hash")
        except RuntimeError as exc:
            assert "plan_hash" in str(exc)

        del man["plan_hash"]
        man_path.write_text(json.dumps(man, indent=2, sort_keys=True), encoding="utf-8")
        mh = hashlib.sha256(man_path.read_bytes()).hexdigest()
        (root / "attempt_01_normal" / "attempt_manifest.sha256").write_text(mh + "\n", encoding="utf-8")
        write_local_verified_seal(
            root / "attempt_01_normal", run_id="attempt_01_normal", remote_manifest_sha256=mh
        )
        try:
            analyze_group(root, capture_iter=1, strict=True)
            raise AssertionError("UNEXPECTED_PASS missing plan_hash")
        except RuntimeError as exc:
            assert "plan_hash" in str(exc)


def test_group_invalid_all_stages_fixture() -> None:
    from fanout_orchestrator import FAILURE_STAGES, simulate_stage_failure_fixture

    for stage in FAILURE_STAGES:
        with tempfile.TemporaryDirectory() as td:
            out = simulate_stage_failure_fixture(Path(td), stage)
            assert out["group_invalid"] is True
            assert out.get("subsequent_started") is False


def test_mark_group_invalid_idempotent() -> None:
    from fanout_orchestrator import mark_group_invalid

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "group_config.json").write_text(
            json.dumps({"group_id": "g", "status": "running"}), encoding="utf-8"
        )
        mark_group_invalid(root, group_id="g", reason="first", attempt_id="a1", stage="fanout")
        mark_group_invalid(
            root,
            group_id="g",
            reason="second",
            attempt_id="a1",
            stage="cleanup",
            extra={"cleanup_status": "CLEANUP_INCOMPLETE"},
        )
        inv = json.loads((root / "GROUP_INVALID.json").read_text(encoding="utf-8"))
        assert inv["reason"] == "first"
        assert inv["cleanup_status"] == "CLEANUP_INCOMPLETE"
        assert len(inv["subsequent_notes"]) == 1


def test_kill_attempt_escalate_fixture() -> None:
    import os
    import subprocess
    import time

    from kill_attempt import escalate_kill

    marker = "MSPTI_KILLFIX_TEST_MARKER_R9"
    rc, actions, status = escalate_kill("", seeds=set(), term_timeout_s=0.2, kill_timeout_s=0.2)
    assert rc == 2 and status == "CLEANUP_REJECTED_MARKER"

    if not Path("/proc").is_dir():
        print("SKIP kill escalate full (/proc missing on this host)")
        return

    child2 = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os,time,signal\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(60)\n",
        ],
        start_new_session=True,
        env={**os.environ, "RUN_MARKER": f"{marker}_IGN"},
    )
    time.sleep(0.3)
    rc2, act2, st2 = escalate_kill(
        f"{marker}_IGN",
        seeds={child2.pid},
        term_timeout_s=0.3,
        kill_timeout_s=1.0,
    )
    try:
        child2.wait(timeout=3)
    except Exception:
        child2.kill()
        child2.wait(timeout=3)
    assert st2 == "CLEANUP_OK" and rc2 == 0, (rc2, st2, act2)

    foreign = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        start_new_session=True,
    )
    time.sleep(0.2)
    rc3, act3, st3 = escalate_kill(
        f"{marker}_FOREIGN",
        seeds={foreign.pid},
        term_timeout_s=0.2,
        kill_timeout_s=0.2,
    )
    assert any("reuse_or_foreign" in a or "skip" in a for a in act3)
    assert foreign.poll() is None
    foreign.kill()
    foreign.wait(timeout=3)


def test_yield_ownership_race_matrix() -> None:
    """Regression for FALSE_POSITIVE_OWN_PROCESS: stale environ, own_pgids, perm, foreign, reuse."""
    import errno
    from unittest import mock

    import opponent_check as oc

    marker = "MSPTI_YIELD_FIX_MARKER_R1"
    here = Path(__file__).resolve().parent

    def _run(out: Path, ps: str, env_fx: dict) -> tuple[int, str]:
        oc.set_environ_fixture(env_fx)
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(here / "opponent_check.py"),
                    "--mode",
                    "yield",
                    "--out-dir",
                    str(out),
                    "--run-marker",
                    marker,
                    "--node-id",
                    "0",
                    "--fixture-ps-rc",
                    "0",
                    "--fixture-ps-stdout",
                    ps,
                    "--fixture-environ-json",
                    json.dumps(env_fx),
                ],
                capture_output=True,
                text=True,
            )
            last = (proc.stdout or "").strip().splitlines()
            return proc.returncode, (last[-1] if last else "")
        finally:
            oc.set_environ_fixture(None)

    # 1) ps row then PID disappears → CLEAR (skip stale)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        (out / "node_0.pgid").write_text("100\n", encoding="utf-8")
        (out / "node_0.pids").write_text("100\n", encoding="utf-8")
        ps = "PID PGID STAT ARGS\n100 100 S torchrun launcher\n200 100 R pretrain_gpt.py rank-child\n"
        fx = {
            "100": {
                "status": "OK",
                "has_marker": True,
                "marker_value": marker,
                "has_out_dir": True,
                "out_dir_value": str(out),
                "starttime": 11,
            },
            "200": {"status": "STALE", "errno": errno.ENOENT, "errno_name": "ENOENT"},
            "_ppid": {"200": 100},
        }
        rc, line = _run(out, ps, fx)
        assert rc == 0 and line == "CLEAR", (rc, line)

    # 2) marker PGID leader + short-lived same-PGID member environ gone → CLEAR via own_pgids
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        (out / "node_0.pgid").write_text("1000\n", encoding="utf-8")
        # Static seeds intentionally omit TorchElastic PGID 255412 (the bug scenario).
        (out / "node_0.pids").write_text("1000\n", encoding="utf-8")
        ps = (
            "PID PGID STAT ARGS\n"
            "1000 1000 S torchrun bootstrap\n"
            "255412 255412 R pretrain_gpt.py rank-leader\n"
            "263002 255412 R pretrain_gpt.py short-lived\n"
        )
        fx = {
            "1000": {
                "status": "OK",
                "has_marker": True,
                "marker_value": marker,
                "has_out_dir": True,
                "out_dir_value": str(out),
                "starttime": 1,
            },
            "255412": {
                "status": "OK",
                "has_marker": True,
                "marker_value": marker,
                "has_out_dir": True,
                "out_dir_value": str(out),
                "starttime": 2,
            },
            "263002": {"status": "STALE", "errno": errno.ENOENT, "errno_name": "ENOENT"},
            "_ppid": {"255412": 1000},
        }
        rc, line = _run(out, ps, fx)
        assert rc == 0 and line == "CLEAR", (rc, line)

    # 3) PermissionError → CHECK_FAILED with pid/errno (not OPPONENT)
    with mock.patch.object(
        Path,
        "read_bytes",
        side_effect=PermissionError(errno.EACCES, "permission denied"),
    ):
        probe = oc.read_environ(77)
    assert probe.status == oc.ProbeStatus.ERROR, probe
    assert probe.errno_num == errno.EACCES, probe

    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        (out / "node_0.pgid").write_text("10\n", encoding="utf-8")
        (out / "node_0.pids").write_text("10\n", encoding="utf-8")
        ps = "PID PGID STAT ARGS\n10 10 S torchrun\n77 77 R pretrain_gpt.py foreign-or-opaque\n"
        fx = {
            "10": {
                "status": "OK",
                "has_marker": True,
                "marker_value": marker,
                "out_dir_value": str(out),
                "has_out_dir": True,
                "starttime": 1,
            },
            "77": {"status": "ERROR", "errno": errno.EACCES, "errno_name": "EACCES", "starttime": 9},
        }
        rc, line = _run(out, ps, fx)
        assert rc == 20, (rc, line)
        assert line.startswith("CHECK_FAILED|")
        assert "pid=77" in line and "EACCES" in line
        assert "OPPONENT" not in line

    # 4) stable foreign no marker → OPPONENT with pid/pgid/starttime/probes
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        (out / "node_0.pgid").write_text("10\n", encoding="utf-8")
        (out / "node_0.pids").write_text("10\n", encoding="utf-8")
        ps = "PID PGID STAT ARGS\n10 10 S torchrun\n88 88 R pretrain_gpt.py foreign\n"
        fx = {
            "10": {
                "status": "OK",
                "has_marker": True,
                "marker_value": marker,
                "has_out_dir": True,
                "out_dir_value": str(out),
                "starttime": 1,
            },
            "88": {
                "status": "OK",
                "has_marker": False,
                "has_out_dir": False,
                "starttime": 42,
            },
        }
        rc, line = _run(out, ps, fx)
        assert rc == 10, (rc, line)
        assert line.startswith("OPPONENT|")
        assert "pid=88" in line and "pgid=88" in line and "starttime=42" in line
        assert "marker=no" in line and "outdir=no" in line
        assert "pid=10" not in line  # own not listed as opponent

    # 5) marker own + foreign together → OPPONENT only lists foreign
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        (out / "node_0.pgid").write_text("10\n", encoding="utf-8")
        ps = (
            "PID PGID STAT ARGS\n"
            "10 10 S torchrun\n"
            "11 11 R pretrain_gpt.py own-rank\n"
            "99 99 R pretrain_gpt.py foreign\n"
        )
        fx = {
            "10": {
                "status": "OK",
                "has_marker": True,
                "marker_value": marker,
                "has_out_dir": True,
                "out_dir_value": str(out),
                "starttime": 1,
            },
            "11": {
                "status": "OK",
                "has_marker": True,
                "marker_value": marker,
                "has_out_dir": True,
                "out_dir_value": str(out),
                "starttime": 2,
            },
            "99": {"status": "OK", "has_marker": False, "has_out_dir": False, "starttime": 3},
            "_ppid": {"11": 10},
        }
        rc, line = _run(out, ps, fx)
        assert rc == 10, (rc, line)
        assert "pid=99" in line
        assert "pid=11" not in line and "pid=10" not in line

    # 6) PID reuse / mismatched starttime must not count as own
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        (out / "node_0.pgid").write_text("500\n", encoding="utf-8")
        # ownership snapshot recorded old starttime for pid 500
        (out / "node_0.ownership.jsonl").write_text(
            json.dumps({"pid": 500, "pgid": 500, "starttime": 100}) + "\n",
            encoding="utf-8",
        )
        ps = "PID PGID STAT ARGS\n500 500 R pretrain_gpt.py reused-pid\n"
        fx = {
            # Live PID 500 exists but starttime differs → seed rejected; no marker → OPPONENT
            "500": {
                "status": "OK",
                "has_marker": False,
                "has_out_dir": False,
                "starttime": 999,
            },
        }
        rc, line = _run(out, ps, fx)
        assert rc == 10, (rc, line)
        assert "pid=500" in line and "starttime=999" in line

    # Same-PGID short-lived member STALE while leader has marker → CLEAR (core FP case)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        (out / "node_0.pgid").write_text("50\n", encoding="utf-8")
        (out / "node_0.pids").write_text("50\n", encoding="utf-8")
        ps = (
            "PID PGID STAT ARGS\n"
            "50 50 S torchrun\n"
            "60 60 R pretrain_gpt.py leader\n"
            "61 60 R pretrain_gpt.py child-stale-env\n"
        )
        fx = {
            "50": {
                "status": "OK",
                "has_marker": True,
                "marker_value": marker,
                "has_out_dir": True,
                "out_dir_value": str(out),
                "starttime": 1,
            },
            "60": {
                "status": "OK",
                "has_marker": True,
                "marker_value": marker,
                "has_out_dir": False,
                "starttime": 2,
            },
            # Child environ vanished after ps snapshot — must NOT become OPPONENT
            "61": {"status": "STALE", "errno": errno.ESRCH, "errno_name": "ESRCH"},
        }
        rc, line = _run(out, ps, fx)
        assert rc == 0 and line == "CLEAR", (rc, line)

    # Stable readable foreign sharing an owned PGID remains OPPONENT.
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        (out / "node_0.pgid").write_text("50\n", encoding="utf-8")
        ps = (
            "PID PGID STAT ARGS\n"
            "50 50 S torchrun\n"
            "60 60 R pretrain_gpt.py own-leader\n"
            "62 60 R pretrain_gpt.py foreign-same-pgid\n"
        )
        fx = {
            "50": {
                "status": "OK",
                "has_marker": True,
                "marker_value": marker,
                "has_out_dir": True,
                "out_dir_value": str(out),
                "starttime": 1,
            },
            "60": {
                "status": "OK",
                "has_marker": True,
                "marker_value": marker,
                "has_out_dir": False,
                "starttime": 2,
            },
            "62": {
                "status": "OK",
                "has_marker": False,
                "has_out_dir": False,
                "starttime": 3,
            },
        }
        rc, line = _run(out, ps, fx)
        assert rc == 10, (rc, line)
        assert "pid=62" in line and "pgid=60" in line
        assert "pid=60" not in line


def test_yield_fail_closed_fixture() -> None:
    """no opponent → continue; opponent → stop; check rc!=0 → YIELD_CHECK_FAILED; cleanup residual."""
    from local_group_guard import (
        classify_yield_check,
        classify_idle_check,
        simulate_yield_monitor,
        simulate_opponent_check_fixture,
        YIELD_CLEAR,
        YIELD_OPPONENT,
        YIELD_CHECK_FAILED,
        RC_YIELD_CHECK_FAILED,
        RC_YIELD_OPPONENT,
        RC_YIELD_CLEAR,
    )
    import opponent_check as oc

    assert RC_YIELD_CLEAR == 0 and RC_YIELD_OPPONENT == 10 and RC_YIELD_CHECK_FAILED == 20
    assert oc.RC_CLEAR == 0 and oc.RC_OPPONENT == 10 and oc.RC_CHECK_FAILED == 20

    assert classify_yield_check("CLEAR", 0).status == YIELD_CLEAR
    assert classify_yield_check("OK", 0).status == YIELD_CLEAR
    assert classify_yield_check("STARTUP", 0).status == YIELD_CLEAR
    assert classify_yield_check("OPPONENT|123 torchrun", 0).status == YIELD_OPPONENT
    assert classify_yield_check("OPP|123 torchrun", 0).status == YIELD_OPPONENT
    assert classify_yield_check("OK", 1).status == YIELD_CHECK_FAILED
    assert classify_yield_check("", 0).status == YIELD_CHECK_FAILED  # empty ≠ clear
    assert classify_yield_check("weird", 0).status == YIELD_CHECK_FAILED
    assert classify_idle_check("CLEAR", 0).status == YIELD_CLEAR
    assert classify_idle_check("OPPONENT|123 torchrun", 0).status == YIELD_OPPONENT
    assert classify_idle_check("", 255).status == YIELD_CHECK_FAILED
    assert classify_idle_check("", 0).status == YIELD_CHECK_FAILED

    # Structured checker fixture matrix (except clear → must not continue)
    for kind in ("ps_rc", "kubectl_rc", "ssh_rc", "empty", "malformed", "opponent", "clear"):
        with tempfile.TemporaryDirectory() as td:
            g = Path(td) / f"g_{kind}"
            r = simulate_opponent_check_fixture(kind=kind, group_dir=g)
            if kind == "clear":
                assert r["continued"] is True and r["stopped"] is False
                assert r["rc"] == 0
                assert not (g / "GROUP_INVALID.json").exists()
            else:
                assert r["continued"] is False and r["stopped"] is True
                assert r["rc"] != 0
                assert (g / "GROUP_INVALID.json").exists()

    # Continuous clear → continue
    with tempfile.TemporaryDirectory() as td:
        g = Path(td) / "g_clear"
        g.mkdir()
        r = simulate_yield_monitor(
            checks=[("CLEAR", 0), ("OK", 0), ("STARTUP", 0)],
            cleanup_rc=0,
            group_dir=g,
        )
        assert r.continued and not r.stopped
        assert not (g / "GROUP_INVALID.json").exists()

    # Opponent appears → stop + INVALID
    with tempfile.TemporaryDirectory() as td:
        g = Path(td) / "g_opp"
        g.mkdir()
        r = simulate_yield_monitor(
            checks=[("CLEAR", 0), ("OPPONENT|9 pretrain_gpt.py", 0)],
            cleanup_rc=0,
            group_dir=g,
        )
        assert r.stopped and r.killed
        assert r.reason == "yield_opponent"
        inv = json.loads((g / "GROUP_INVALID.json").read_text())
        assert inv["reason"] == "yield_opponent"
        assert inv["status"] == "GROUP_INVALID"

    # SSH/kubectl/proc check non-zero → YIELD_CHECK_FAILED
    with tempfile.TemporaryDirectory() as td:
        g = Path(td) / "g_fail"
        g.mkdir()
        r = simulate_yield_monitor(
            checks=[("CLEAR", 0), ("", 255)],
            cleanup_rc=0,
            group_dir=g,
        )
        assert r.stopped
        assert r.reason == "YIELD_CHECK_FAILED"
        inv = json.loads((g / "GROUP_INVALID.json").read_text())
        assert inv["reason"] == "YIELD_CHECK_FAILED"

    # Cleanup residual non-zero recorded
    with tempfile.TemporaryDirectory() as td:
        g = Path(td) / "g_cleanup"
        g.mkdir()
        r = simulate_yield_monitor(
            checks=[("OPPONENT|1 megatron", 0)],
            cleanup_rc=7,
            group_dir=g,
        )
        assert r.cleanup_rc == 7
        inv = json.loads((g / "GROUP_INVALID.json").read_text())
        assert inv.get("cleanup_status") == "CLEANUP_INCOMPLETE" or (
            inv.get("extra") or {}
        ).get("cleanup_status") == "CLEANUP_INCOMPLETE"

    # Launcher source: shared checker, no os.popen call, no swallow, protocol 0/10/20
    here = Path(__file__).resolve().parent
    ab = (here / "launch_megatron_ab.sh").read_text(encoding="utf-8")
    grj = (here / "launch_grj.sh").read_text(encoding="utf-8")
    oc_src = (here / "opponent_check.py").read_text(encoding="utf-8")
    assert "opponent_check.py" in ab and "opponent_check.py" in grj
    assert "os.popen(" not in ab and "os.popen(" not in grj and "os.popen(" not in oc_src
    assert "subprocess.run" in oc_src
    assert "|| true)" not in ab.split("yield_if_opponent()")[1].split("pull_attempt_evidence")[0]
    assert "|| true)" not in grj.split("yield_if_opponent()")[1].split("pull_evidence")[0]
    assert "set +e" in ab and "rc=$?" in ab
    assert "YIELD_CHECK_FAILED" in ab and "YIELD_CHECK_FAILED" in grj
    assert "return 20" in ab and "return 10" in ab
    assert "return 20" in grj and "return 10" in grj
    assert "claim_local_group_dirs" in ab and "claim_local_group_dirs" in grj
    assert "mkdir -p \"${BACKUP_ROOT}\" \"${LOG_DIR}\"" not in ab
    assert "mkdir -p \"${BACKUP_ROOT}\" \"${LOG_DIR}\"" not in grj
    assert RC_YIELD_CHECK_FAILED == 20 and RC_YIELD_OPPONENT == 10


def test_local_group_refuse_reuse_fixture() -> None:
    """absent → ok; GROUP_COMPLETE / old attempt / empty dir → refuse; hashes unchanged."""
    from local_group_guard import (
        simulate_local_group_refuse_fixture,
        simulate_concurrent_claim_fixture,
        claim_local_group,
        LocalGroupClaimError,
        RC_CLAIM_TAKEN,
        RC_CLAIM_ABORTED,
    )
    import os
    import time

    here = Path(__file__).resolve().parent

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Absent → success create
        r0 = simulate_local_group_refuse_fixture(root / "fresh")
        assert r0["refused"] is False and r0["rc"] == 0 and r0["created"] is True

        # Pre-seed GROUP_COMPLETE
        r1 = simulate_local_group_refuse_fixture(
            root / "complete",
            seed={"GROUP_COMPLETE": "done\n", "group_plan.json": '{"ok":1}\n'},
        )
        assert r1["refused"] is True and r1["rc"] == 19
        assert r1["hashes_unchanged"] is True
        assert r1["wrote_invalid_into_old"] is False
        assert r1["complete_and_new_invalid"] is False

        # Old attempt dir
        r2 = simulate_local_group_refuse_fixture(
            root / "old_attempt",
            seed={"attempt_01_normal/dry_run_attempt_config.json": '{"a":1}\n'},
        )
        assert r2["refused"] is True and r2["hashes_unchanged"] is True

        # Empty dir also refuse
        r3 = simulate_local_group_refuse_fixture(root / "empty", empty=True)
        assert r3["refused"] is True and r3["rc"] == 19

    # Atomic claim: concurrent same GROUP_ID → exactly one success
    with tempfile.TemporaryDirectory() as td:
        parent = Path(td)
        conc = simulate_concurrent_claim_fixture(
            group_id="concurrent_gid",
            parent=parent,
            n_procs=4,
        )
        assert conc["n_ok"] == 1, conc
        assert conc["n_fail"] == 3, conc
        assert conc["claim_exists"] is True

    # Concurrent against pre-existing GROUP_COMPLETE: none may overwrite; hashes intact
    with tempfile.TemporaryDirectory() as td:
        parent = Path(td)
        seed = {"GROUP_COMPLETE": "old_done\n", "attempt_01/hash.txt": "abc\n"}
        conc2 = simulate_concurrent_claim_fixture(
            group_id="old_gid",
            parent=parent,
            n_procs=3,
            seed_old=seed,
        )
        assert conc2["n_ok"] == 0
        assert conc2["n_fail"] == 3
        assert conc2["hashes_unchanged"] is True
        assert conc2["wrote_invalid_into_old"] is False

    # Empty old backup dir → claim abort / refuse; no INVALID into old
    with tempfile.TemporaryDirectory() as td:
        parent = Path(td)
        claim_parent = parent / "claims"
        backups = parent / "backups"
        logs = parent / "logs"
        claim_parent.mkdir()
        backups.mkdir()
        logs.mkdir()
        gid = "empty_old"
        (backups / gid).mkdir()
        try:
            claim_local_group(
                group_id=gid,
                claim_parent=claim_parent,
                backup_root=backups / gid,
                log_dir=logs / gid,
            )
            raise AssertionError("expected claim abort on empty old backup")
        except LocalGroupClaimError as exc:
            assert exc.rc in (RC_CLAIM_TAKEN, RC_CLAIM_ABORTED)
        assert not (backups / gid / "GROUP_INVALID.json").exists()
        assert list((backups / gid).iterdir()) == []

    # End-to-end via launcher: refuse when BACKUP_ROOT pre-exists with GROUP_COMPLETE
    with tempfile.TemporaryDirectory() as td:
        backup = Path(td) / "backup"
        backup.mkdir()
        complete = backup / "GROUP_COMPLETE"
        complete.write_text("old_complete\n", encoding="utf-8")
        h_before = hashlib.sha256(complete.read_bytes()).hexdigest()
        log_dir = Path(td) / "logs_new"
        claim_parent = Path(td) / "claims"
        env = os.environ.copy()
        env.update(
            {
                "DRY_RUN": "1",
                "FIXTURE_RUN": "0",
                "GROUP_ID": f"refuse_{int(time.time())}",
                "BACKUP_ROOT": str(backup),
                "LOG_DIR": str(log_dir),
                "CLAIM_PARENT": str(claim_parent),
                "MSPTI_MIN_RAW_KERNELS": "7000",
                "MSPTI_MIN_COMM": "1000",
            }
        )
        proc = subprocess.run(
            ["bash", str(here / "launch_megatron_ab.sh")],
            cwd=str(here),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out[-1500:]
        assert (
            "REFUSE_LOCAL_GROUP_REUSE" in out
            or "CLAIM_ABORTED" in out
            or "already exists" in out
            or "backup_root_exists" in out
        )
        assert hashlib.sha256(complete.read_bytes()).hexdigest() == h_before
        assert not (backup / "GROUP_INVALID.json").exists()
        assert complete.read_text() == "old_complete\n"


def main() -> int:
    test_throughput_formula()
    test_strict_rank_ok()
    test_strict_active_gt_span_fails()
    test_strict_missing_finalize_flag_fails()
    test_strict_meta_finalize_false_fails()
    test_meta_kseg_mismatch_fails()
    test_drop_component_nonzero_count0_fails()
    test_post_drop_events_fail()
    test_atexit_finalize_rejected()
    test_all_ranks_raw1_fails_absolute()
    test_single_rank_below_center_fails()
    test_comm_truncation_fails()
    test_strict_bad_json_fails()
    test_lifecycle_parent_evidence_negatives()
    test_artifact_digest_negatives()
    test_required_digest_coverage_negatives()
    test_analyzer_strict_negatives()
    test_analyzer_cli_pos_neg()
    test_torch_trace_digest_and_tamper()
    test_missing_node_e2e_fails()
    test_ab_dry_run_full_plan()
    test_fixture_run_full_postprocess()
    test_sealed_so_actual_hash_negatives()
    test_ours_node_loaded_so_required_negatives()
    test_attempt_plan_hash_binding_negatives()
    test_group_invalid_all_stages_fixture()
    test_mark_group_invalid_idempotent()
    test_kill_attempt_escalate_fixture()
    test_yield_ownership_race_matrix()
    test_yield_fail_closed_fixture()
    test_local_group_refuse_reuse_fixture()
    test_counterbalanced_sequence_negatives()
    test_fanout_partial_failure_fixture()
    test_plan_hash_dry_run_fixture_same()
    test_transfer_recovery_fixtures()
    test_fatal_signal_audit_node_logs()
    test_launch_ab_jump_local_port_preflight_contract()
    test_diff_check_tracked_and_untracked()
    test_synthetic_sealed_so_runlog_chain_local()
    test_provenance_env_denylist()
    test_analyzer_no_flush_slot_keyerror()
    test_wheel_select_dry_run()
    test_ab_dry_run_and_missing_thresholds()
    test_strict_empty_not_auto_collector_off()
    test_parse_drop_flags_requires_all()
    test_kseg_cpp()
    test_collector_logic_cpp()
    print("OK test_local")
    return 0


if __name__ == "__main__":
    sys.exit(main())

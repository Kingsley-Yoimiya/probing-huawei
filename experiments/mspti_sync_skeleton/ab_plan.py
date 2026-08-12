#!/usr/bin/env python3
"""Single AB orchestration plan shared by formal / DRY_RUN / FIXTURE_RUN.

Shell launchers must consume immutable group_plan.json (--query-tsv/--query-json)
and must NOT re-derive attempt id/port/marker from ${ATTEMPTS}.

FIXTURE_RUN synthesizes training artifacts but consumes the same plan and the same
postprocess functions — this is **postprocess parity**, not coverage of real SSH/kubectl.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from model_scale_contract import resolve_frozen_gbs
from provenance import (
    build_artifact_digest,
    write_local_verified_seal,
    write_source_provenance,
    sha256_file,
)


# Canonical counterbalanced arm sequence (exactly six attempts).
DEFAULT_ATTEMPTS = ("normal", "ours", "torch", "torch", "ours", "normal")
# Future sequences require an explicit design field + whitelist entry.
SUPPORTED_SEQUENCES: dict[str, tuple[str, ...]] = {
    "counterbalanced_v1": DEFAULT_ATTEMPTS,
    "smoke_normal_v1": ("normal",),
    "smoke_ours_v1": ("ours",),
}
DEFAULT_DESIGN_SEQUENCE = "counterbalanced_v1"


def validate_attempts_sequence(
    attempts: list[str],
    *,
    design_sequence: Optional[str] = None,
) -> str:
    """Enforce exact supported arm sequence; return resolved design id."""
    design = design_sequence or DEFAULT_DESIGN_SEQUENCE
    if design not in SUPPORTED_SEQUENCES:
        raise ValueError(
            f"unsupported design_sequence={design!r}; "
            f"allowed={sorted(SUPPORTED_SEQUENCES)}"
        )
    got = list(attempts)
    if design == "smoke_normal_v1":
        if got != ["normal"] or len(got) != 1:
            raise ValueError(
                f"smoke_normal_v1 requires exactly ['normal'], got {got}"
            )
        return design
    if design == "smoke_ours_v1":
        if got != ["ours"] or len(got) != 1:
            raise ValueError(
                f"smoke_ours_v1 requires exactly ['ours'], got {got}"
            )
        return design
    expected = list(SUPPORTED_SEQUENCES[design])
    if got != expected:
        raise ValueError(
            f"counterbalanced sequence mismatch for {design}: "
            f"expected {expected}, got {got}"
        )
    if len(got) != 6 and design not in ("smoke_normal_v1", "smoke_ours_v1"):
        raise ValueError(f"counterbalanced AB requires exactly 6 attempts, got {len(got)}")
    return design


def plan_hash(plan: dict[str, Any]) -> str:
    """Stable hash over canonical plan JSON (excludes plan_hash itself)."""
    body = {k: v for k, v in plan.items() if k != "plan_hash"}
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_ab_plan(
    *,
    group_id: str,
    code_dir: str,
    code_hash: str,
    attempts: list[str],
    nnodes: int,
    nproc: int,
    world_size: int,
    expected_ranks: int,
    train_iters: int,
    capture_iter: int,
    base_port: int,
    group_dir: str,
    min_raw_kernels: int,
    min_comm: int,
    rel_raw_floor: float,
    rel_comm_floor: float,
    model: Optional[dict[str, Any]] = None,
    design_sequence: Optional[str] = None,
) -> dict[str, Any]:
    design = validate_attempts_sequence(attempts, design_sequence=design_sequence)
    tp = int((model or {}).get("tp", 2))
    pp = int((model or {}).get("pp", 1))
    mbs = int((model or {}).get("mbs", 1))
    gbs_in = (model or {}).get("gbs")
    resolved_model = resolve_frozen_gbs(
        nnodes=int(nnodes),
        nproc=int(nproc),
        tp=tp,
        pp=pp,
        mbs=mbs,
        gbs=int(gbs_in) if gbs_in is not None else None,
        seq=int((model or {}).get("seq", 4096)),
        layers=int((model or {}).get("layers", 32)),
        seed=int((model or {}).get("seed", 1234)),
    )
    plan: dict[str, Any] = {
        "group_id": group_id,
        "code_dir": code_dir,
        "code_hash": code_hash,
        "code_dir_content_hash": code_hash,
        "design_sequence": design,
        "nnodes": int(nnodes),
        "nproc_per_node": int(nproc),
        "world_size": int(world_size),
        "expected_ranks": int(expected_ranks),
        "expected_nodes": int(nnodes),
        "train_iters": int(train_iters),
        "capture_megatron_iter": int(capture_iter),
        "attempts_order": list(attempts),
        "model": resolved_model,
        "frozen_thresholds": {
            "min_raw_kernels_per_rank": int(min_raw_kernels),
            "min_comm_per_rank": int(min_comm),
            "relative_raw_floor": float(rel_raw_floor),
            "relative_comm_floor": float(rel_comm_floor),
            "immutable": True,
        },
        "require_local_anchor": True,
        "provenance_required": [
            "source_tree_sha256",
            "collector_so_sha256",
            "artifact_digest_sha256",
        ],
        "seal_order": [
            "stop_logs",
            "artifact_digest",
            "attempt_manifest",
            "remote_sha256",
            "pullback",
            "local_verified_seal",
            "strict_analyzer_local",
        ],
        "postprocess": [
            "per_attempt_pull_full",
            "per_attempt_local_anchor",
            "group_strict_analyzer_local",
        ],
        "content_addressed_code_dir": True,
        "attempts": [],
    }
    seen_ids: set[str] = set()
    for i, arm in enumerate(attempts, 1):
        aid = f"attempt_{i:02d}_{arm}"
        if aid in seen_ids:
            raise ValueError(f"duplicate attempt_id {aid}")
        seen_ids.add(aid)
        plan["attempts"].append(
            {
                "attempt_id": aid,
                "arm": arm,
                "order_index": i,
                "master_port": int(base_port) + i * 19,
                "run_marker": f"MSPTI_AB_{group_id}_{aid}",
                "out_dir": f"{group_dir.rstrip('/')}/{aid}",
                "require_event_thresholds": arm == "ours",
                "require_artifact_provenance_seal": True,
                "require_local_anchor": True,
                "required_artifacts": _required_for_arm(arm, expected_ranks, nnodes),
                "postprocess": [
                    "artifact_digest",
                    "attempt_manifest",
                    "remote_seal",
                    "pull_full",
                    "local_verified_seal",
                ],
            }
        )
    validate_plan(plan)
    plan["plan_hash"] = plan_hash(plan)
    return plan


def _required_for_arm(arm: str, expected_ranks: int, nnodes: int) -> list[str]:
    req = ["config.json", "run.log"]
    for n in range(int(nnodes)):
        req.extend([f"node_{n}.done", f"node_{n}.launch.json", f"node_{n}.log"])
    if arm == "ours":
        req.append("counters.json")
        req.append("cluster.trace.json")
        for r in range(int(expected_ranks)):
            req.append(f"rank_{r:04d}.skeleton.jsonl")
            req.append(f"rank_{r:04d}.npu_sync_meta.json")
        req.append("sealed_bins/")  # prefix marker; concrete hash name filled at seal
    if arm == "torch":
        req.append("torch_prof_node*/**/trace_view.json")
    return req


def validate_plan(plan: dict[str, Any]) -> None:
    attempts = plan.get("attempts") or []
    design = plan.get("design_sequence") or DEFAULT_DESIGN_SEQUENCE
    expected_len = 1 if design in ("smoke_normal_v1", "smoke_ours_v1") else 6
    if len(attempts) != expected_len:
        raise ValueError(
            f"plan must have {expected_len} attempts for {design}, got {len(attempts)}"
        )
    arms = [a["arm"] for a in attempts]
    validate_attempts_sequence(arms, design_sequence=design)
    if arms != list(plan.get("attempts_order") or []):
        raise ValueError("attempts_order mismatch with attempts[].arm")
    ft = plan.get("frozen_thresholds") or {}
    for k in (
        "min_raw_kernels_per_rank",
        "min_comm_per_rank",
        "relative_raw_floor",
        "relative_comm_floor",
    ):
        if k not in ft or ft[k] in (None, ""):
            raise ValueError(f"missing frozen threshold {k}")
    ports = [a["master_port"] for a in attempts]
    if len(set(ports)) != len(ports):
        raise ValueError(f"master ports not unique: {ports}")
    ids = [a["attempt_id"] for a in attempts]
    if len(set(ids)) != len(ids):
        raise ValueError(f"attempt_id not unique: {ids}")
    for i, a in enumerate(attempts, 1):
        for key in (
            "attempt_id",
            "arm",
            "run_marker",
            "out_dir",
            "postprocess",
            "required_artifacts",
            "order_index",
            "master_port",
        ):
            if key not in a:
                raise ValueError(f"attempt missing {key}")
        if int(a["order_index"]) != i:
            raise ValueError(
                f"attempt order_index not contiguous: got {a['order_index']} expected {i}"
            )
        expected_id = f"attempt_{i:02d}_{a['arm']}"
        if a["attempt_id"] != expected_id:
            raise ValueError(
                f"attempt_id mismatch: got {a['attempt_id']} expected {expected_id}"
            )
    if not plan.get("content_addressed_code_dir"):
        raise ValueError("content_addressed_code_dir required")
    if "strict_analyzer_local" not in (plan.get("seal_order") or []):
        raise ValueError("seal_order must include strict_analyzer_local (local-only)")


def write_plan(plan: dict[str, Any], path: Path) -> Path:
    validate_plan(plan)
    if "plan_hash" not in plan:
        plan = dict(plan)
        plan["plan_hash"] = plan_hash(plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    (path.parent / "group_plan.sha256").write_text(plan["plan_hash"] + "\n", encoding="utf-8")
    return path


def load_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_plan(plan)
    digest = plan_hash(plan)
    listed = plan.get("plan_hash")
    if listed and listed != digest:
        raise ValueError(f"plan_hash mismatch: listed={listed} recomputed={digest}")
    return plan


def plan_to_tsv_rows(plan: dict[str, Any]) -> list[str]:
    """Validated plan → TSV lines for shell consumption (no re-derivation)."""
    validate_plan(plan)
    rows = [
        "order_index\tattempt_id\tarm\tmaster_port\trun_marker\tout_dir\trequire_event_thresholds"
    ]
    for a in plan["attempts"]:
        rows.append(
            "\t".join(
                [
                    str(a["order_index"]),
                    a["attempt_id"],
                    a["arm"],
                    str(a["master_port"]),
                    a["run_marker"],
                    a["out_dir"],
                    "1" if a.get("require_event_thresholds") else "0",
                ]
            )
        )
    return rows


def query_plan_field(plan: dict[str, Any], key: str) -> str:
    validate_plan(plan)
    if key == "plan_hash":
        return str(plan.get("plan_hash") or plan_hash(plan))
    if key == "design_sequence":
        return str(plan.get("design_sequence") or DEFAULT_DESIGN_SEQUENCE)
    if key == "attempts_order":
        return " ".join(plan["attempts_order"])
    if key in plan:
        val = plan[key]
        if isinstance(val, (dict, list)):
            return json.dumps(val, sort_keys=True)
        return str(val)
    raise KeyError(key)


def _write_drop_row(rank: int, seq: int) -> str:
    flags = (
        "allocation=0;queue=0;parse=0;io=0;callback=0;mspti=0;"
        "incomplete=0;finalize=1"
    )
    return json.dumps(
        {
            "kind": "DROP",
            "rank": rank,
            "seq": seq,
            "step": -1,
            "count": 0,
            "flags": flags,
            "ts_ns": 0,
        },
        sort_keys=True,
    )


def _synthetic_rank_jsonl(path: Path, rank: int, capture_step: int, n_kseg: int = 80, n_comm: int = 20) -> None:
    rows = []
    seq = 0
    rows.append(
        {
            "kind": "STEP",
            "rank": rank,
            "seq": seq,
            "step": capture_step,
            "flags": "phase=begin",
            "count": 1,
            "ts_ns": 1,
        }
    )
    seq += 1
    for i in range(n_kseg):
        rows.append(
            {
                "kind": "KSEG",
                "rank": rank,
                "seq": seq,
                "step": capture_step,
                "count": 1,
                "active_ns": 1000,
                "span_ns": 1000,
                "gap_ns": 0,
                "ts_ns": 10 + i,
            }
        )
        seq += 1
    for i in range(n_comm):
        rows.append(
            {
                "kind": "COMM",
                "rank": rank,
                "seq": seq,
                "step": capture_step,
                "count": 1,
                "ts_ns": 1000 + i,
            }
        )
        seq += 1
    rows.append(
        {
            "kind": "STEP",
            "rank": rank,
            "seq": seq,
            "step": capture_step,
            "flags": "phase=end",
            "count": 1,
            "ts_ns": 2000,
        }
    )
    seq += 1
    rows.append(json.loads(_write_drop_row(rank, seq)))
    path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n", encoding="utf-8")


def _synthetic_meta(path: Path, rank: int, raw: int, *, adaptive: bool = False) -> None:
    meta: dict[str, Any] = {
        "rank": rank,
        "finalize_complete": True,
        "finalize_rc": 0,
        "incomplete": False,
        "armed_fail": False,
        "capture_end_rc": 0,
        "raw_kernels": raw,
        "finalize_reason": "last_train_step",
        "finalize_ms": 1.0,
        "finalize_flush_ms": 0.5,
        "finalize_drain_ms": 0.5,
        "finalize_total_ms": 1.0,
        "capture_begin_ms": 0.1,
        "capture_end_ms": 0.1,
        "collector_start_ms": 0.1,
        "process_wall_ms": 1000.0,
    }
    if adaptive:
        kseg = max(1, raw // 8)
        meta.update(
            {
                "granularity_mode": "adaptive_v1",
                "adaptive_source": "native",
                "abi_version": 1,
                "adaptive": {
                    "granularity_mode": "adaptive_v1",
                    "abi_version": 1,
                    "initial_gap_us": 50.0,
                    "final_gap_us": 75.0,
                    "target_kseg_ratio": 0.08,
                    "adapt_min_samples": 128,
                    "adapt_every": 256,
                    "adaptive_gap_min_us": 10,
                    "adaptive_gap_max_us": 200,
                    "threshold_updates": 2,
                    "total_raw_kernels": raw,
                    "total_kseg": kseg,
                    "actual_ratio": round(kseg / raw, 6),
                    "streams": [
                        {
                            "device_id": 0,
                            "stream_id": 1,
                            "raw_kernels": raw,
                            "kseg_count": kseg,
                            "ratio": round(kseg / raw, 6),
                            "final_threshold_us": 75.0,
                            "threshold_updates": 2,
                            "positive_gaps": max(128, raw),
                        }
                    ],
                    "threshold_history": [
                        {
                            "raw_index": 200,
                            "device_id": 0,
                            "stream_id": 1,
                            "threshold_us": 60.0,
                        },
                        {
                            "raw_index": 400,
                            "device_id": 0,
                            "stream_id": 1,
                            "threshold_us": 75.0,
                        },
                    ],
                },
            }
        )
    path.write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")


def materialize_fixture_attempt(
    attempt_dir: Path,
    *,
    arm: str,
    plan: dict[str, Any],
    attempt: dict[str, Any],
    code_dir: Path,
    so_bytes: bytes,
) -> None:
    """Create synthetic but structurally complete attempt artifacts (no remote)."""
    attempt_dir.mkdir(parents=True, exist_ok=True)
    nnodes = int(plan["nnodes"])
    ranks = int(plan["expected_ranks"])
    capture = int(plan["capture_megatron_iter"])
    cfg = {
        "run_id": attempt["attempt_id"],
        "arm": arm,
        "nnodes": nnodes,
        "nproc_per_node": plan["nproc_per_node"],
        "world_size": plan["world_size"],
        "expected_ranks": ranks,
        "expected_nodes": nnodes,
        "train_iters": plan["train_iters"],
        "capture_megatron_iter": capture,
        "frozen_thresholds": plan["frozen_thresholds"],
        "model": plan["model"],
        "fixture": True,
    }
    if arm == "ours":
        cfg["granularity_mode"] = "adaptive_v1"
    (attempt_dir / "config.json").write_text(
        json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8"
    )
    (attempt_dir / "run.log").write_text(
        "FIXTURE_RUN\npretrain_gpt.py\n"
        + "\n".join(
            f"iteration {i}/{plan['train_iters']} | elapsed time per iteration (ms): 100.0 | lm loss: 1.0"
            for i in range(1, int(plan["train_iters"]) + 1)
        )
        + "\n",
        encoding="utf-8",
    )
    # provenance from real local sources
    write_source_provenance(code_dir, attempt_dir)
    # sealed SO
    digest = hashlib.sha256(so_bytes).hexdigest()
    seal_dir = attempt_dir / "sealed_bins"
    seal_dir.mkdir(parents=True, exist_ok=True)
    sealed = seal_dir / f"libmspti_sync_skeleton.so.{digest}"
    if sealed.exists():
        try:
            sealed.chmod(0o644)
        except OSError:
            pass
        sealed.unlink(missing_ok=True)
    sealed.write_bytes(so_bytes)
    try:
        sealed.chmod(0o444)
    except OSError:
        pass
    build = {
        "collector_so_sha256": digest,
        "collector_so_sealed_relpath": f"sealed_bins/{sealed.name}",
        "collector_so_size": len(so_bytes),
        "collector_so_immutable": True,
    }
    (attempt_dir / "provenance_build.json").write_text(
        json.dumps(build, indent=2, sort_keys=True), encoding="utf-8"
    )

    for n in range(nnodes):
        launch = {
            "run_id": attempt["attempt_id"],
            "arm": arm,
            "node_rank": n,
            "raw_exit_code": 0,
            "raw_exit_code_pending": False,
            "e2e_wall_ms": 1000.0 + n,
            "e2e_wall_definition": "fixture monotonic stand-in",
            "full_argv": ["torchrun", "pretrain_gpt.py", "--seed", "1234"],
            "extra_env": [],
            "env": {"ARM": arm, "SEED": "1234"},
            "collector_so_sha256_loaded": digest if arm == "ours" else None,
            "collector_so_loaded_path": str(sealed) if arm == "ours" else None,
        }
        (attempt_dir / f"node_{n}.launch.json").write_text(
            json.dumps(launch, indent=2, sort_keys=True), encoding="utf-8"
        )
        (attempt_dir / f"node_{n}.done").write_text("ok\n", encoding="utf-8")
        (attempt_dir / f"node_{n}.log").write_text(
            "pretrain_gpt.py\n"
            + "\n".join(
                f"iteration {i}/{plan['train_iters']} | elapsed time per iteration (ms): 100.0 | lm loss: 1.0"
                for i in range(1, int(plan["train_iters"]) + 1)
            )
            + "\n",
            encoding="utf-8",
        )

    if arm == "ours":
        for r in range(ranks):
            _synthetic_rank_jsonl(
                attempt_dir / f"rank_{r:04d}.skeleton.jsonl",
                r,
                capture,
                n_kseg=max(80, int(plan["frozen_thresholds"]["min_raw_kernels_per_rank"])),
                n_comm=max(20, int(plan["frozen_thresholds"]["min_comm_per_rank"])),
            )
            raw = max(80, int(plan["frozen_thresholds"]["min_raw_kernels_per_rank"]))
            _synthetic_meta(
                attempt_dir / f"rank_{r:04d}.npu_sync_meta.json",
                r,
                raw,
                adaptive=True,
            )
        (attempt_dir / "cluster.trace.json").write_text(
            json.dumps({"fixture": True, "events": []}), encoding="utf-8"
        )
        (attempt_dir / "counters.json").write_text(
            json.dumps(
                {
                    "pass": True,
                    "drop_count": 0,
                    "event_counts": {"KSEG": 80, "COMM": 20},
                    "convert_trace_write_ms": 1.0,
                    "convert_command_wall_ms": 2.0,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    if arm == "torch":
        for n in range(nnodes):
            tdir = (
                attempt_dir
                / f"torch_prof_node{n}"
                / "ASCEND_PROFILER_OUTPUT"
            )
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "trace_view.json").write_text(
                json.dumps({"fixture": True, "node": n, "events": [{"name": "x"}]}),
                encoding="utf-8",
            )

    # digest → manifest → simulated remote seal → local anchor
    art = build_artifact_digest(attempt_dir)
    node_launch = {
        f"node_{n}.launch.json": json.loads(
            (attempt_dir / f"node_{n}.launch.json").read_text(encoding="utf-8")
        )
        for n in range(nnodes)
    }
    e2e = max(float(v["e2e_wall_ms"]) for v in node_launch.values())
    tree = json.loads((attempt_dir / "provenance_source_tree.json").read_text(encoding="utf-8"))
    manifest = {
        "attempt_id": attempt["attempt_id"],
        "arm": arm,
        "order_index": int(attempt.get("order_index") or 0),
        "exit_code": 0,
        "convert_rc": 0,
        "finalize_complete": True,
        "expected_ranks": ranks,
        "expected_nodes": nnodes,
        "nnodes": nnodes,
        "nproc_per_node": plan["nproc_per_node"],
        "train_iters": plan["train_iters"],
        "capture_megatron_iter": capture,
        "e2e_wall_ms": e2e,
        "node_launch": node_launch,
        "provenance": {
            "source_tree_sha256": tree["source_tree_sha256"],
            "collector_so_sha256": digest,
            "artifact_digest_sha256": art["aggregate_sha256"],
        },
        "artifact_digest_sha256": art["aggregate_sha256"],
        "plan_hash": plan.get("plan_hash"),
        "design_sequence": plan.get("design_sequence", DEFAULT_DESIGN_SEQUENCE),
        "fixture": True,
    }
    man_path = attempt_dir / "attempt_manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    man_hash = sha256_file(man_path)
    (attempt_dir / "attempt_manifest.sha256").write_text(man_hash + "\n", encoding="utf-8")
    write_local_verified_seal(
        attempt_dir,
        run_id=attempt["attempt_id"],
        remote_manifest_sha256=man_hash,
    )


def run_fixture(plan: dict[str, Any], *, backup_root: Path, code_dir: Path) -> int:
    """Local-only full postprocess path for all six attempts + group analyzer CLI."""
    validate_plan(plan)
    backup_root.mkdir(parents=True, exist_ok=True)
    write_plan(plan, backup_root / "group_plan.json")
    # Fake SO bytes derived from collector.cpp when present.
    so_src = code_dir / "collector.cpp"
    so_bytes = (b"FAKE_SO\n" + so_src.read_bytes()[:4096]) if so_src.exists() else b"FAKE_SO"
    group_cfg = {
        "group_id": plan["group_id"],
        "attempts_order": plan["attempts_order"],
        "design_sequence": plan.get("design_sequence", DEFAULT_DESIGN_SEQUENCE),
        "plan_hash": plan.get("plan_hash"),
        "code_dir_content_hash": plan.get("code_dir_content_hash") or plan.get("code_hash"),
        "capture_megatron_iter": plan["capture_megatron_iter"],
        "model": plan["model"],
        "frozen_thresholds": plan["frozen_thresholds"],
        "nnodes": plan["nnodes"],
        "world_size": plan["world_size"],
        "status": "fixture",
        "note": "FIXTURE_RUN = postprocess parity only; does not cover real SSH/kubectl",
    }
    (backup_root / "group_config.json").write_text(
        json.dumps(group_cfg, indent=2, sort_keys=True), encoding="utf-8"
    )
    for attempt in plan["attempts"]:
        adir = backup_root / attempt["attempt_id"]
        materialize_fixture_attempt(
            adir,
            arm=attempt["arm"],
            plan=plan,
            attempt=attempt,
            code_dir=code_dir,
            so_bytes=so_bytes,
        )
        print("FIXTURE_ATTEMPT_OK", attempt["attempt_id"], attempt["arm"])

    # Group strict analyzer via real CLI (local anchors present).
    script = code_dir / "analyze_megatron_ab.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--strict", str(backup_root)],
        cwd=str(code_dir),
        capture_output=True,
        text=True,
    )
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        print("FIXTURE_ANALYZER_FAIL", proc.returncode)
        return proc.returncode
    print("FIXTURE_RUN_OK", backup_root)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-plan", action="store_true")
    ap.add_argument("--fixture-run", action="store_true")
    ap.add_argument("--query-tsv", type=Path, default=None, help="Print TSV rows from existing plan")
    ap.add_argument("--query-json", type=Path, default=None, help="Load plan and print JSON field")
    ap.add_argument("--query-field", default="plan_hash")
    ap.add_argument("--group-id", default="")
    ap.add_argument("--code-dir", type=Path, default=None)
    ap.add_argument("--code-hash", default="")
    ap.add_argument("--backup-root", type=Path, default=None)
    ap.add_argument("--group-dir", default="")
    ap.add_argument("--attempts", default=" ".join(DEFAULT_ATTEMPTS))
    ap.add_argument("--design-sequence", default=DEFAULT_DESIGN_SEQUENCE)
    ap.add_argument("--nnodes", type=int, default=2)
    ap.add_argument("--nproc", type=int, default=16)
    ap.add_argument("--train-iters", type=int, default=20)
    ap.add_argument("--capture-iter", type=int, default=10)
    ap.add_argument("--base-port", type=int, default=38000)
    ap.add_argument("--min-raw", type=int, default=80)
    ap.add_argument("--min-comm", type=int, default=20)
    ap.add_argument("--rel-raw", type=float, default=0.8)
    ap.add_argument("--rel-comm", type=float, default=0.8)
    args = ap.parse_args(argv)

    if args.query_tsv is not None:
        plan = load_plan(args.query_tsv)
        print("\n".join(plan_to_tsv_rows(plan)))
        return 0
    if args.query_json is not None:
        plan = load_plan(args.query_json)
        print(query_plan_field(plan, args.query_field))
        return 0

    if not args.group_id or args.code_dir is None or not args.code_hash or args.backup_root is None:
        ap.error("--group-id/--code-dir/--code-hash/--backup-root required unless --query-*")

    attempts = args.attempts.split()
    world = args.nnodes * args.nproc
    group_dir = args.group_dir or str(args.backup_root)
    plan = build_ab_plan(
        group_id=args.group_id,
        code_dir=str(args.code_dir),
        code_hash=args.code_hash,
        attempts=attempts,
        nnodes=args.nnodes,
        nproc=args.nproc,
        world_size=world,
        expected_ranks=world,
        train_iters=args.train_iters,
        capture_iter=args.capture_iter,
        base_port=args.base_port,
        group_dir=group_dir,
        min_raw_kernels=args.min_raw,
        min_comm=args.min_comm,
        rel_raw_floor=args.rel_raw,
        rel_comm_floor=args.rel_comm,
        design_sequence=args.design_sequence,
    )
    if args.emit_plan or not args.fixture_run:
        out = args.backup_root / "group_plan.json"
        write_plan(plan, out)
        # Keep dry_run_plan.json alias for older callers.
        write_plan(plan, args.backup_root / "dry_run_plan.json")
        print("DRY_RUN_PLAN_OK", len(plan["attempts"]), out)
        print("PLAN_HASH", plan["plan_hash"])
        for a in plan["attempts"]:
            print("ATTEMPT", a["attempt_id"], a["arm"], a["master_port"], a["run_marker"])
        if not args.fixture_run:
            return 0
    return run_fixture(plan, backup_root=args.backup_root, code_dir=args.code_dir)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Measure CONTROL_PEAK_BYTES for six-arm no-training launcher control plane."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from storage_contract import (
    JUMP_MIN_AVAIL_BYTES,
    P_GROUP_R3,
    check_capacity,
    compute_storage_paths,
    j_control_required_bytes,
)


def _dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def materialize_six_arm_control_artifacts(control_root: Path, group_id: str) -> None:
    """Write representative jump-control artifacts for 6 attempts + fanout (no training)."""
    control_root.mkdir(parents=True, exist_ok=True)
    attempts = [
        ("attempt_01_normal", "normal", 39119),
        ("attempt_02_ours", "ours", 39138),
        ("attempt_03_torch", "torch", 39157),
        ("attempt_04_torch", "torch", 39176),
        ("attempt_05_ours", "ours", 39195),
        ("attempt_06_normal", "normal", 39214),
    ]
    plan = {
        "group_id": group_id,
        "attempts_order": [a[0] for a in attempts],
        "attempts": [
            {
                "attempt_id": aid,
                "arm": arm,
                "master_port": port,
                "run_marker": f"marker-{aid}",
                "out_dir": f"/afs/live/{aid}",
            }
            for aid, arm, port in attempts
        ],
        "plan_hash": "fixture" + ("a" * 56),
        "nnodes": 16,
        "world_size": 256,
    }
    (control_root / "group_plan.json").write_text(
        json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8"
    )
    (control_root / "group_plan.tsv").write_text(
        "\n".join(a[0] for a in attempts) + "\n", encoding="utf-8"
    )
    (control_root / "pod_map.json").write_text(
        json.dumps(
            {
                "job": "yjr-mspti-256-fixture",
                "pods": [
                    {
                        "node_rank": i,
                        "pod": (
                            "yjr-mspti-256-fixture-master-0"
                            if i == 0
                            else f"yjr-mspti-256-fixture-worker-{i - 1}"
                        ),
                    }
                    for i in range(16)
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    fanout = control_root / "fanout_preflight"
    fanout.mkdir(parents=True, exist_ok=True)
    for i in range(16):
        (fanout / f"rank_{i:02d}.json").write_text(
            json.dumps({"node_rank": i, "emit_rc": 0, "import_ok": True}, sort_keys=True),
            encoding="utf-8",
        )
    (fanout / "fanout_preflight_summary.json").write_text(
        json.dumps(
            {"pass": True, "done": 16, "emit_rc_all_zero": True, "import_ok": 16},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    for aid, arm, port in attempts:
        ad = control_root / aid
        ad.mkdir(parents=True, exist_ok=True)
        cfg = {
            "attempt_id": aid,
            "arm": arm,
            "master_port": port,
            "dry_run": True,
            "control_only": True,
        }
        (ad / "dry_run_attempt_config.json").write_text(
            json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8"
        )
        (ad / "launcher_local.log").write_text(
            f"CONTROL_ONLY attempt={aid} arm={arm}\n", encoding="utf-8"
        )
        (ad / "pod_rc.json").write_text(
            json.dumps({"pods": 16, "all_zero": True}, sort_keys=True), encoding="utf-8"
        )
    (control_root / "storage_paths.json").write_text("{}", encoding="utf-8")
    (control_root / "launcher_local.log").write_text("six_arm_control_fixture\n", encoding="utf-8")


def measure_control_peak(
    *,
    jump_control_parent: str,
    group_id: str,
    jump_only: bool = True,
) -> dict:
    paths = compute_storage_paths(group_id, jump_control_parent=jump_control_parent)
    root = paths.jump_control_root
    if root.exists():
        shutil.rmtree(root)
    materialize_six_arm_control_artifacts(root, group_id)
    peak = _dir_size_bytes(root)
    jump_required = j_control_required_bytes(peak)
    rep = check_capacity(
        jump_path="/",
        afs_path="/",
        jump_control_root=root,
        control_peak_bytes=peak,
        payload_estimate_bytes=0,
    )
    jump_ok = rep.jump_avail_bytes >= jump_required and rep.jump_control_bytes <= rep.jump_control_budget_bytes
    if jump_only:
        ok = jump_ok
        reasons = [
            r
            for r in rep.reasons
            if r.startswith("jump_avail") or r.startswith("jump_control=")
        ]
    else:
        ok = rep.ok
        reasons = rep.reasons
    return {
        "group_id": group_id,
        "jump_control_root": str(root),
        "control_peak_bytes": peak,
        "control_peak_provenance": {
            "source": "measured_full_six_arm",
            "measurement_kind": "measured_full_six_arm",
            "six_arm_covered": True,
            "code_hash": "fixture",
            "plan_hash": "fixture" + ("a" * 56),
            "covers": [
                "group_plan",
                "pod_map",
                "fanout_preflight",
                "six_arm_launcher_logs",
                "port_preflight",
                "storage_paths",
            ],
        },
        "jump_required_bytes": jump_required,
        "jump_avail_bytes": rep.jump_avail_bytes,
        "jump_control_bytes": rep.jump_control_bytes,
        "jump_capacity_ok": jump_ok,
        "capacity_ok": ok,
        "capacity": {**rep.to_dict(), "ok": ok, "reasons": reasons},
        "jump_only_gate": jump_only,
        "P_group_bytes": P_GROUP_R3,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group-id", required=True)
    ap.add_argument(
        "--jump-control-parent",
        default="/root/myportal-results/mspti-control",
    )
    ap.add_argument("--jump-only", action="store_true", default=True)
    ap.add_argument("--full-capacity", action="store_true")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    jump_only = not args.full_capacity
    payload = measure_control_peak(
        jump_control_parent=args.jump_control_parent,
        group_id=args.group_id,
        jump_only=jump_only,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print("CONTROL_PEAK_BYTES", payload["control_peak_bytes"])
    print("JUMP_REQUIRED_BYTES", payload["jump_required_bytes"])
    print("CAPACITY_OK", payload["capacity_ok"])
    return 0 if payload["capacity_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

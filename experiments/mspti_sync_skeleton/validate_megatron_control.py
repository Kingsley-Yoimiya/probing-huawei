#!/usr/bin/env python3
"""Validate a no-observer MindSpeed/Megatron control attempt."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--expected-nodes", type=int, required=True)
    args = parser.parse_args()
    out = args.out_dir
    errors: list[str] = []
    launches = []
    for node in range(args.expected_nodes):
        done = out / f"node_{node}.done"
        fail = out / f"node_{node}.fail"
        launch_path = out / f"node_{node}.launch.json"
        if not done.is_file():
            errors.append(f"missing node_{node}.done")
        if fail.exists():
            errors.append(f"unexpected node_{node}.fail")
        if not launch_path.is_file():
            errors.append(f"missing {launch_path.name}")
            continue
        try:
            launch = json.loads(launch_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"invalid {launch_path.name}: {exc}")
            continue
        launches.append(launch)
        if launch.get("arm") != "normal":
            errors.append(f"node {node}: arm={launch.get('arm')!r}")
        if launch.get("raw_exit_code_pending") is not False:
            errors.append(f"node {node}: exit code still pending")
        if launch.get("raw_exit_code") != 0:
            errors.append(f"node {node}: raw_exit_code={launch.get('raw_exit_code')!r}")
        if not isinstance(launch.get("e2e_wall_ms"), (int, float)) or launch.get("e2e_wall_ms", 0) <= 0:
            errors.append(f"node {node}: invalid e2e_wall_ms")
        env = launch.get("env") or {}
        for key, expected in {
            "PROBING": "0",
            "PROBING_NPU_SYNC_SKELETON": "0",
            "MSPTI_SKELETON": "0",
        }.items():
            if env.get(key) != expected:
                errors.append(f"node {node}: {key}={env.get(key)!r}, expected {expected!r}")

    traces = sorted(out.glob("rank_*.skeleton.jsonl"))
    metas = sorted(out.glob("rank_*.mspti_meta.json")) + sorted(out.glob("rank_*.npu_sync_meta.json"))
    if traces:
        errors.append(f"control unexpectedly produced {len(traces)} skeleton traces")
    if metas:
        errors.append(f"control unexpectedly produced {len(metas)} MSPTI meta files")

    result = {
        "schema_version": 1,
        "analysis_eligibility": "no_observer_control" if not errors else "invalid",
        "pass": not errors,
        "expected_nodes": args.expected_nodes,
        "launch_count": len(launches),
        "e2e_wall_ms": [x.get("e2e_wall_ms") for x in launches],
        "errors": errors,
    }
    (out / "CONTROL_GATE.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    marker = out / ("ATTEMPT_COMPLETE" if not errors else "ATTEMPT_INVALID")
    marker.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if not errors else 3


if __name__ == "__main__":
    raise SystemExit(main())

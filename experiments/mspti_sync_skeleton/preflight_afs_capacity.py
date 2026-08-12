#!/usr/bin/env python3
"""AFS capacity gate on target pod mount (not jump overlay).

Uses r3 frozen P_group from storage_contract (REPLAN Step 2.3).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from storage_contract import (
    AFS_ROOT_DEFAULT,
    P_GROUP_R3,
    afs_required_start_bytes,
)

# Legacy alias for wrappers that read this key.
FULL_GROUP_PAYLOAD_ESTIMATE = P_GROUP_R3


def compute_afs_required_bytes(payload_estimate: int = P_GROUP_R3) -> int:
    return afs_required_start_bytes(payload_estimate)


def measure_afs_capacity(
    *,
    afs_path: str = AFS_ROOT_DEFAULT,
    payload_estimate_bytes: int = P_GROUP_R3,
) -> dict:
    usage = shutil.disk_usage(afs_path)
    afs_avail = int(usage.free)
    afs_required = compute_afs_required_bytes(payload_estimate_bytes)
    ok = afs_avail >= afs_required
    formula = (
        f"max(200 GiB, ceil(1.2×2×P_group)) "
        f"= max({afs_required_start_bytes(0)}, ceil(1.2×2×{payload_estimate_bytes})) "
        f"= {afs_required}"
    )
    return {
        "afs_path": afs_path,
        "afs_avail_bytes": afs_avail,
        "afs_required_bytes": afs_required,
        "full_group_payload_estimate_bytes": payload_estimate_bytes,
        "P_group_bytes": P_GROUP_R3,
        "full_group_payload_estimate_gib": round(payload_estimate_bytes / 1024**3, 3),
        "capacity_formula": formula,
        "capacity_ok": ok,
        "jump_only_gate": False,
        "pod_mount_gate": True,
        "reason": (
            f"afs_avail={afs_avail} >= required={afs_required}"
            if ok
            else f"afs_avail={afs_avail} < required={afs_required}"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="AFS capacity gate on pod mount")
    ap.add_argument("--afs-path", default=AFS_ROOT_DEFAULT)
    ap.add_argument(
        "--payload-estimate-bytes",
        type=int,
        default=P_GROUP_R3,
    )
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    payload = measure_afs_capacity(
        afs_path=args.afs_path,
        payload_estimate_bytes=args.payload_estimate_bytes,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print("AFS_AVAIL_BYTES", payload["afs_avail_bytes"])
    print("AFS_REQUIRED_BYTES", payload["afs_required_bytes"])
    print("FULL_GROUP_PAYLOAD_ESTIMATE", payload["full_group_payload_estimate_bytes"])
    print("CAPACITY_OK", payload["capacity_ok"])
    return 0 if payload["capacity_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Derive a frozen HCCL-issued -> MSPTI per-op floor from healthy references."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path


OP_MAP = {
    "HcclAllGather": "hcom_allGather_",
    "HcclAllReduce": "hcom_allReduce_",
    "HcclBroadcast": "hcom_broadcast_",
    "HcclReduceScatter": "hcom_reduceScatter_",
    "HcclSend": "hcom_send_",
    "HcclRecv": "hcom_receive_",
}


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def load_run(root: Path) -> dict:
    strict = json.loads((root / "STRICT_SUBSET16.json").read_text())
    hccl = {int(item["rank"]): item for item in strict["hccl_issued"]}
    capture_ranks = [int(rank) for rank in strict["capture_ranks"]]
    mspti: dict[int, Counter[str]] = {}
    for rank in capture_ranks:
        counter: Counter[str] = Counter()
        path = root / f"rank_{rank:04d}.skeleton.jsonl"
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row.get("kind") in {"COMM", "P2P"}:
                counter[str(row.get("op"))] += 1
        mspti[rank] = counter
    return {
        "root": str(root),
        "capture_ranks": capture_ranks,
        "hccl": hccl,
        "mspti": mspti,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    parser.add_argument("--reference", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    references = [load_run(path) for path in args.reference]
    target = load_run(args.target)
    errors: list[str] = []
    reference_hccl_schedules = {
        json.dumps(item["op_counts"], sort_keys=True)
        for reference in references
        for item in reference["hccl"].values()
    }
    if len(reference_hccl_schedules) != 1:
        errors.append("reference HCCL accepted schedules are not invariant")
        expected_hccl: dict[str, int] = {}
    else:
        expected_hccl = json.loads(next(iter(reference_hccl_schedules)))

    reference_floors: dict[str, int] = {}
    for hccl_op, mspti_op in OP_MAP.items():
        if expected_hccl.get(hccl_op, 0) <= 0:
            continue
        values = [
            int(reference["mspti"][rank].get(mspti_op, 0))
            for reference in references
            for rank in reference["capture_ranks"]
        ]
        if not values:
            errors.append(f"no reference MSPTI values for {mspti_op}")
        else:
            reference_floors[mspti_op] = min(values)

    per_rank: list[dict] = []
    for rank in target["capture_ranks"]:
        hccl_counts = target["hccl"][rank]["op_counts"]
        schedule_match = hccl_counts == expected_hccl
        if not schedule_match:
            errors.append(f"rank={rank} HCCL accepted schedule differs from reference")
        op_rows = []
        complete = schedule_match
        for hccl_op, mspti_op in OP_MAP.items():
            if hccl_op not in expected_hccl:
                continue
            observed = int(target["mspti"][rank].get(mspti_op, 0))
            floor = int(reference_floors.get(mspti_op, 0))
            op_complete = observed >= floor
            complete = complete and op_complete
            op_rows.append(
                {
                    "hccl_op": hccl_op,
                    "hccl_accepted": int(hccl_counts.get(hccl_op, 0)),
                    "mspti_op": mspti_op,
                    "mspti_observed": observed,
                    "reference_floor": floor,
                    "accepted_minus_mspti": int(hccl_counts.get(hccl_op, 0))
                    - observed,
                    "complete": op_complete,
                }
            )
        per_rank.append(
            {
                "rank": rank,
                "hccl_schedule_match": schedule_match,
                "mapping_complete": complete,
                "ops": op_rows,
            }
        )

    payload = {
        "schema_version": 1,
        "analysis": "hccl_issued_to_mspti_per_op_floor",
        "evidence_boundary": (
            "post-hoc mapping gate derived only from supplied healthy references; "
            "HCCL ledger enters before downstream libmspti Hccl wrapper and returns after it"
        ),
        "target": str(args.target),
        "references": [str(path) for path in args.reference],
        "reference_hccl_schedule": expected_hccl,
        "reference_mspti_floors": reference_floors,
        "evaluable": not errors,
        "pass": not errors and all(item["mapping_complete"] for item in per_rank),
        "errors": errors,
        "per_rank": per_rank,
    }
    atomic_json(args.out, payload)
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["pass"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

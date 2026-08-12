#!/usr/bin/env python3
"""process_wall_ms runtime negatives — fail-closed evidence for preflight."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def run_negatives() -> dict:
    from analyze_megatron_ab import _resolve_process_wall_ms

    cases: list[dict] = []

    manifest = {"e2e_wall_ms": 999.0, "node_launch": {"node_0.launch.json": {"e2e_wall_ms": 999.0}}}

    def check(name: str, meta: dict, *, allow_legacy: bool = False) -> None:
        proc, src, disc = _resolve_process_wall_ms(
            meta,
            manifest,
            group_id="test-group",
            allow_legacy_process_wall_proxy=allow_legacy,
        )
        cases.append(
            {
                "name": name,
                "process_wall_ms": proc,
                "source": src,
                "disclosure": disc,
                "fail_closed": proc is None and src is None,
            }
        )

    base = {
        "finalize_complete": True,
        "finalize_reason": "smoke.fixture",
        "process_wall_ms": 617.95,
        "granularity_mode": "adaptive_v1",
        "adaptive_source": "native",
    }
    check("positive_meta", dict(base))

    missing = dict(base)
    del missing["process_wall_ms"]
    check("missing_field", missing)

    negative = dict(base)
    negative["process_wall_ms"] = -1.0
    check("negative_value", negative)

    nan_case = dict(base)
    nan_case["process_wall_ms"] = float("nan")
    check("nan_value", nan_case)

    inf_case = dict(base)
    inf_case["process_wall_ms"] = float("inf")
    check("inf_value", inf_case)

    proxy = dict(base)
    del proxy["process_wall_ms"]
    proxy["e2e_wall_ms"] = 999.0
    check("manifest_e2e_proxy_blocked", proxy)

    legacy = dict(base)
    del legacy["process_wall_ms"]
    check("legacy_allow_blocked", legacy, allow_legacy=True)

    expected_fail = {
        "missing_field",
        "negative_value",
        "nan_value",
        "inf_value",
        "manifest_e2e_proxy_blocked",
        "legacy_allow_blocked",
    }
    for c in cases:
        if c["name"] in expected_fail:
            c["expected"] = "fail_closed"
            c["ok"] = c["fail_closed"]
        elif c["name"] == "positive_meta":
            c["expected"] = "pass"
            c["ok"] = c["source"] == "meta.process_wall_ms" and c["process_wall_ms"] == 617.95
        else:
            c["expected"] = "unknown"
            c["ok"] = False

    return {
        "cases": cases,
        "all_ok": all(c["ok"] for c in cases),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    exp = Path(__file__).resolve().parent
    sys.path.insert(0, str(exp))
    payload = run_negatives()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print("PROCESS_WALL_NEGATIVES_OK", payload["all_ok"])
    return 0 if payload["all_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

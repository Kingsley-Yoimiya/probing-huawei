#!/usr/bin/env python3
"""C4: 64-rank machine-readable verification table + evidence manifest (Strategy A).

Uses skeleton.summarize_rank + read_meta for trustworthy predicate — never fabricates
a literal sidecar ``trustworthy`` field.  Distinguishes event_counts.DROP (record
count, one per rank) from drop_count / DROP.count (actual dropped events).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo layout: experiments/mspti_sync_skeleton beside python/probing
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "python") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "python"))

from probing.profiling.npu_sync.skeleton import (  # noqa: E402
    read_meta,
    read_skeleton_jsonl,
    summarize_rank,
)
from strict_validate import (  # noqa: E402
    REQUIRED_DROP_KEYS,
    parse_drop_flags,
    resolve_meta_raw_kernels,
    validate_adaptive_native_meta,
)

OURS_ATTEMPTS = ("attempt_02_ours", "attempt_05_ours")
EXPECTED_RANKS = 32
CAPTURE_STEP = 10


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _abi_version(meta: dict[str, Any]) -> int | None:
    adaptive = meta.get("adaptive")
    if isinstance(adaptive, dict) and adaptive.get("abi_version") is not None:
        return int(adaptive["abi_version"])
    if meta.get("abi_version") is not None:
        return int(meta["abi_version"])
    return None


def _verify_one_rank(
    attempt: str,
    run_dir: Path,
    rank: int,
    *,
    capture_step: int,
) -> dict[str, Any]:
    skel = run_dir / f"rank_{rank:04d}.skeleton.jsonl"
    meta_path = run_dir / f"rank_{rank:04d}.npu_sync_meta.json"
    rows = read_skeleton_jsonl(skel)
    meta = read_meta(skel)
    summary = summarize_rank(
        rows,
        meta=meta,
        expected_rank=rank,
        expected_step=capture_step,
    )

    kinds = summary.kind_counts
    drop_rows = [r for r in rows if str(r.get("kind")) == "DROP"]
    drop_row = drop_rows[0] if len(drop_rows) == 1 else {}
    drop_flags = parse_drop_flags(str(drop_row.get("flags", ""))) if drop_row else {}
    drop_count = int(drop_row.get("count", -1)) if drop_row else -1
    raw_from_kseg = summary.raw_kernel_count
    meta_raw = resolve_meta_raw_kernels(meta) if meta else None

    adaptive_ok = False
    adaptive_errors: list[str] = []
    if meta:
        try:
            validate_adaptive_native_meta(meta, rank=rank, expected_mode="adaptive_v1")
            adaptive_ok = True
        except Exception as exc:  # noqa: BLE001 — collect per-rank, do not abort table
            adaptive_errors.append(str(exc))

    row: dict[str, Any] = {
        "attempt": attempt,
        "rank": rank,
        "jsonl_sha256": _sha256_file(skel),
        "sidecar_sha256": _sha256_file(meta_path) if meta_path.exists() else None,
        "host": summary.host,
        "device_id": rows[0].get("device_id") if rows else None,
        "capture_step": summary.capture_step,
        "seq_max": max(int(r.get("seq", -1)) for r in rows) if rows else -1,
        "step_begin_end_ok": summary.integrity_ok
        and not any("STEP" in p for p in summary.integrity_problems),
        "raw_kernels_kseg": raw_from_kseg,
        "raw_kernels_meta": meta_raw,
        "raw_kseg_match": meta_raw == raw_from_kseg if meta_raw is not None else False,
        "kseg_records": summary.kseg_records,
        "adaptive_source": (meta or {}).get("adaptive_source"),
        "granularity_mode": (meta or {}).get("granularity_mode"),
        "adaptive_native_ok": adaptive_ok,
        "adaptive_native_errors": adaptive_errors,
        "abi_version": _abi_version(meta or {}),
        "window_start_step": (meta or {}).get("window_start_step"),
        "window_steps": (meta or {}).get("window_steps"),
        "finalize_complete": (meta or {}).get("finalize_complete"),
        "finalize_rc": (meta or {}).get("finalize_rc"),
        "finalize_reason": (meta or {}).get("finalize_reason"),
        "meta_clean": (meta or {}).get("clean"),
        "meta_incomplete": (meta or {}).get("incomplete"),
        "drop_record_count": len(drop_rows),
        "drop_count": drop_count,
        "drop_component_sum": (
            sum(drop_flags[k] for k in REQUIRED_DROP_KEYS) if drop_flags else 0
        ),
        "drop_flags": drop_flags,
        "drop_total_predicate": summary.drop_total,
        "finalize_marked": summary.finalize_marked,
        "incomplete_flag": summary.incomplete,
        "event_counts_DROP_note": (
            "event_counts.DROP counts DROP records (one per rank), not dropped events"
        ),
        "trustworthy_predicate": summary.trustworthy,
        "trustworthy_evidence_source": "probing.profiling.npu_sync.skeleton.summarize_rank",
        "integrity_ok": summary.integrity_ok,
        "meta_ok": summary.meta_ok,
        "untrustworthy_reasons": summary.problems,
    }
    row["zero_drop_ok"] = (
        row["drop_count"] == 0
        and row["drop_component_sum"] == 0
        and summary.drop_total == 0
    )
    return row


def build_table(
    group_dir: Path,
    *,
    capture_step: int = CAPTURE_STEP,
) -> dict[str, Any]:
    group_dir = group_dir.resolve()
    rows: list[dict[str, Any]] = []
    for attempt in OURS_ATTEMPTS:
        run_dir = group_dir / attempt
        if not run_dir.is_dir():
            raise FileNotFoundError(f"missing ours attempt: {run_dir}")
        for rank in range(EXPECTED_RANKS):
            rows.append(
                _verify_one_rank(attempt, run_dir, rank, capture_step=capture_step)
            )

    all_trustworthy = all(r["trustworthy_predicate"] for r in rows)
    all_zero_drop = all(r["zero_drop_ok"] for r in rows)
    all_adaptive = all(r["adaptive_native_ok"] for r in rows)

    # Attempt-level event_counts disclosure (DROP record count vs drop_count aggregate)
    attempt_summaries: dict[str, Any] = {}
    for attempt in OURS_ATTEMPTS:
        counters_path = group_dir / attempt / "counters.json"
        if counters_path.exists():
            counters = json.loads(counters_path.read_text(encoding="utf-8"))
            ec = counters.get("event_counts") or {}
            attempt_summaries[attempt] = {
                "event_counts_DROP_records": ec.get("DROP"),
                "aggregate_drop_count": counters.get("drop_count"),
                "note": (
                    "event_counts.DROP=32 means 32 DROP rows (one per rank); "
                    "zero discarded events requires drop_count=0 and each DROP.count=0"
                ),
            }

    return {
        "group_id": group_dir.name,
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "ours_attempts": list(OURS_ATTEMPTS),
        "expected_ranks_per_attempt": EXPECTED_RANKS,
        "total_rows": len(rows),
        "capture_step": capture_step,
        "all_trustworthy": all_trustworthy,
        "all_zero_drop": all_zero_drop,
        "all_adaptive_native": all_adaptive,
        "strategy_a_rank_gate_pass": all_trustworthy and all_zero_drop and all_adaptive,
        "attempt_drop_disclosure": attempt_summaries,
        "rows": rows,
    }


def _write_csv(path: Path, table: dict[str, Any]) -> None:
    fieldnames = [
        "attempt",
        "rank",
        "jsonl_sha256",
        "sidecar_sha256",
        "host",
        "capture_step",
        "seq_max",
        "raw_kernels_kseg",
        "raw_kernels_meta",
        "raw_kseg_match",
        "kseg_records",
        "adaptive_source",
        "granularity_mode",
        "adaptive_native_ok",
        "abi_version",
        "window_start_step",
        "finalize_complete",
        "finalize_reason",
        "meta_clean",
        "drop_record_count",
        "drop_count",
        "drop_component_sum",
        "zero_drop_ok",
        "trustworthy_predicate",
        "untrustworthy_reasons",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in table["rows"]:
            out = dict(row)
            if isinstance(out.get("untrustworthy_reasons"), list):
                out["untrustworthy_reasons"] = "; ".join(out["untrustworthy_reasons"])
            writer.writerow(out)


def build_seal_manifest_summary(group_dir: Path) -> dict[str, Any]:
    """Six-arm seal + manifest sha256 summary for evidence pack."""
    arms: list[dict[str, Any]] = []
    for attempt_dir in sorted(group_dir.glob("attempt_*")):
        if not attempt_dir.is_dir():
            continue
        entry: dict[str, Any] = {"attempt": attempt_dir.name}
        for name in ("LOCAL_VERIFIED_SEAL.json", "attempt_manifest.json"):
            p = attempt_dir / name
            if p.exists():
                entry[name] = {
                    "bytes": p.stat().st_size,
                    "sha256": _sha256_file(p),
                }
        arms.append(entry)
    return {"group_id": group_dir.name, "arms": arms}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build C4 64-rank verification table.")
    p.add_argument("group_dir", type=Path)
    p.add_argument("--capture-step", type=int, default=CAPTURE_STEP)
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: group_dir/c4_evidence)",
    )
    args = p.parse_args(argv)

    group_dir = args.group_dir.resolve()
    out_dir = args.out_dir or (group_dir / "c4_evidence")
    out_dir.mkdir(parents=True, exist_ok=True)

    table = build_table(group_dir, capture_step=args.capture_step)
    json_path = out_dir / "rank_verification_64.json"
    csv_path = out_dir / "rank_verification_64.csv"
    seal_path = out_dir / "seal_manifest_summary.json"

    json_path.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_csv(csv_path, table)
    seal_path.write_text(
        json.dumps(build_seal_manifest_summary(group_dir), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest = {
        "group_id": group_dir.name,
        "generated_at_utc": table["generated_at_utc"],
        "artifacts": {
            "rank_verification_64.json": _sha256_file(json_path),
            "rank_verification_64.csv": _sha256_file(csv_path),
            "seal_manifest_summary.json": _sha256_file(seal_path),
        },
        "strategy_a_rank_gate_pass": table["strategy_a_rank_gate_pass"],
        "all_trustworthy": table["all_trustworthy"],
        "trustworthy_evidence_source": "skeleton.summarize_rank (no fabricated sidecar field)",
    }
    manifest_path = out_dir / "c4_evidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    status = "PASS" if table["strategy_a_rank_gate_pass"] else "FAIL"
    print(
        f"C4_VERIFY_{status} rows={table['total_rows']} "
        f"trustworthy={table['all_trustworthy']} zero_drop={table['all_zero_drop']} "
        f"adaptive={table['all_adaptive_native']}"
    )
    print(f"WROTE {json_path}")
    print(f"WROTE {csv_path}")
    print(f"WROTE {manifest_path}")
    return 0 if table["strategy_a_rank_gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Strategy A append-only qualification record (C1): sha256 audit trail, no training edits."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPLAN_PATH = (
    "plans/case-b-512card/sync_stall/MSPTI_PROBING_CHAIR_REPLAN_COMPLETE_20260810.md"
)
GROUP_ID = "20260809_192117-megatron-ab-formal6x20"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _analyzer_source_hash(analyzer_path: Path) -> str:
    return _sha256_file(analyzer_path)


def _collect_group_hashes(group_dir: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    patterns = (
        "attempt_*/LOCAL_VERIFIED_SEAL.json",
        "attempt_*/attempt_manifest.json",
        "attempt_*/rank_0000.npu_sync_meta.json",
        "attempt_*/rank_0000.skeleton.jsonl",
        "group_plan.json",
        "group_config.json",
    )
    for pat in patterns:
        for path in sorted(group_dir.glob(pat)):
            rel = str(path.relative_to(group_dir))
            entries.append(
                {
                    "path": rel,
                    "bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
    return {"group_id": group_dir.name, "files": entries}


def _read_tail(path: Path, n: int = 5) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(errors="replace").splitlines()
    return lines[-n:]


def build_record(
    group_dir: Path,
    *,
    analyzer_path: Path,
    boot_stdout: Path | None,
    analyze_log: Path | None,
    replan_path: Path | None,
    post_process_utc: str | None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    analyze_log = analyze_log or (group_dir / "analyze.log")
    record: dict[str, Any] = {
        "strategy": "A",
        "group_id": group_dir.name,
        "qualification_status": "post_process_marker_not_yet_qualifying",
        "recorded_at_utc": now,
        "replan_ref": str(replan_path or REPLAN_PATH),
        "launcher_terminal_state": "TRAP_ERR ec=1 — marking GROUP_INVALID (no GROUP_COMPLETE)",
        "original_analyzer_failure": (
            "tail_tax.process_wall_ms null/neg on attempt_02_ours and attempt_05_ours "
            "(npu_sync_meta omitted process_wall_ms)"
        ),
        "post_process_utc": post_process_utc,
        "analyzer_source_sha256": _analyzer_source_hash(analyzer_path),
        "analyzer_derivation_version": None,
        "group_complete_write_mode": "analyzer_post_process_non_launcher_atomic",
        "sealed_artifact_hashes": _collect_group_hashes(group_dir),
        "analyze_log_tail": _read_tail(analyze_log),
        "boot_stdout_tail": _read_tail(boot_stdout) if boot_stdout else [],
        "notes": [
            "Do not retro-edit sealed meta/JSONL/sidecar.",
            "manifest_e2e_wall_ms_proxy is disclosure-only; not collector measured.",
        ],
    }
    metrics = group_dir / "metrics.json"
    if metrics.exists():
        m = json.loads(metrics.read_text(encoding="utf-8"))
        record["analyzer_derivation_version"] = m.get("analyzer_derivation_version")
        record["steady_exclude_iters"] = m.get("steady_exclude_iters")
        record["qualifying_gates"] = m.get("qualifying_gates")
    return record


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Append Strategy A qualification record (C1).")
    p.add_argument("group_dir", type=Path)
    p.add_argument(
        "--analyzer",
        type=Path,
        default=Path(__file__).with_name("analyze_megatron_ab.py"),
    )
    p.add_argument("--boot-stdout", type=Path, default=None)
    p.add_argument("--analyze-log", type=Path, default=None)
    p.add_argument("--replan", type=Path, default=None)
    p.add_argument("--post-process-utc", default=None)
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output JSON path (default: group_dir/strategy_a_qualification.json)",
    )
    p.add_argument("--verify-only", action="store_true", help="Re-hash and compare prior record")
    args = p.parse_args(argv)

    group_dir = args.group_dir.resolve()
    out = args.out or (group_dir / "strategy_a_qualification.json")
    if args.verify_only:
        if not out.exists():
            print(f"VERIFY_FAIL: missing {out}", file=sys.stderr)
            return 1
        prior = json.loads(out.read_text(encoding="utf-8"))
        fresh = _collect_group_hashes(group_dir)
        if prior.get("sealed_artifact_hashes") != fresh:
            print("VERIFY_FAIL: sealed artifact hashes changed", file=sys.stderr)
            return 1
        print("VERIFY_OK sealed hashes unchanged")
        return 0

    record = build_record(
        group_dir,
        analyzer_path=args.analyzer.resolve(),
        boot_stdout=args.boot_stdout,
        analyze_log=args.analyze_log,
        replan_path=args.replan,
        post_process_utc=args.post_process_utc,
    )
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(out)
    sha_path = out.with_suffix(".json.sha256")
    sha_path.write_text(_sha256_file(out) + "\n", encoding="utf-8")
    print(f"QUALIFICATION_OK {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

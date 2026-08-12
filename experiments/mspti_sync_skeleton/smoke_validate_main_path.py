#!/usr/bin/env python3
"""Post-run validation for Plan §7 main-path smoke (ingest + skill SQL + negatives)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load_skill_steps(skill_root: Path) -> list[dict]:
    import yaml

    steps_yaml = skill_root / "steps.yaml"
    data = yaml.safe_load(steps_yaml.read_text(encoding="utf-8"))
    return list(data["spec"]["steps"])


def run_skill_sql(out_dir: Path, skill_root: Path, top_n: int = 20) -> dict:
    from probing.core.engine import query
    from probing.profiling.npu_sync.ingest import ingest_run_dir

    # Fresh ingest from on-disk artifacts (live path already ingested in-process;
    # this proves offline re-ingest + DataFusion query works).
    ingested = ingest_run_dir(out_dir, expected_step=int(os.environ.get("CAPTURE_STEP", "1")))
    results: dict[str, object] = {"ingested": ingested, "steps": {}}
    for step in _load_skill_steps(skill_root):
        sid = step["id"]
        sql = step["sql"].format(top_n=top_n)
        try:
            frame = query(sql)
            rows = frame.to_dict(orient="records") if frame is not None and len(frame) else []
        except Exception as exc:
            results["steps"][sid] = {"error": str(exc)}
            if step.get("on_empty") == "abort":
                raise
            continue
        results["steps"][sid] = {"row_count": len(rows), "rows": rows[:5]}
        if not rows and step.get("on_empty") == "abort":
            raise RuntimeError(f"skill step {sid} aborted on empty: {step.get('empty_message')}")
    return results


def validate_positive(out_dir: Path) -> dict:
    from probing.profiling.npu_sync.skeleton import read_meta, read_skeleton_jsonl

    meta_path = out_dir / "rank_0000.npu_sync_meta.json"
    skel_path = out_dir / "rank_0000.skeleton.jsonl"
    problems: list[str] = []
    if not skel_path.exists():
        problems.append("missing skeleton jsonl")
    if not meta_path.exists():
        problems.append("missing npu_sync_meta sidecar")
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    if meta.get("finalize_complete") is not True:
        problems.append(f"finalize_complete={meta.get('finalize_complete')}")
    reason = meta.get("finalize_reason")
    if reason not in {"last_train_step", "train.finally", "pretrain.finally"}:
        problems.append(f"non-explicit finalize_reason={reason!r}")
    expected_mode = os.environ.get("EXPECT_GRANULARITY_MODE")
    if expected_mode and meta.get("granularity_mode") != expected_mode:
        problems.append(
            f"granularity_mode={meta.get('granularity_mode')!r} expected {expected_mode!r}"
        )
    events = read_skeleton_jsonl(skel_path) if skel_path.exists() else []
    kinds = [e.get("kind") for e in events]
    step_events = [e for e in events if e.get("kind") == "STEP"]
    if len(step_events) != 2:
        problems.append(f"expected 2 STEP events, got {len(step_events)}")
    phases = sorted(
        e.get("phase")
        or (
            "begin"
            if "phase=begin" in str(e.get("flags", ""))
            else "end"
            if "phase=end" in str(e.get("flags", ""))
            else None
        )
        for e in step_events
    )
    if phases != ["begin", "end"]:
        problems.append(f"STEP phases {phases}")
    kseg = [e for e in events if e.get("kind") == "KSEG"]
    if not kseg:
        problems.append("no KSEG")
    if expected_mode == "adaptive_v1":
        if meta.get("adaptive_source") != "native":
            problems.append(f"adaptive_source={meta.get('adaptive_source')!r}")
        adaptive = meta.get("adaptive") or {}
        if adaptive.get("granularity_mode") != "adaptive_v1":
            problems.append("adaptive.granularity_mode mismatch")
        if adaptive.get("abi_version", 0) < 1:
            problems.append(f"adaptive.abi_version={adaptive.get('abi_version')}")
        raw_kernels = int(adaptive.get("total_raw_kernels") or meta.get("raw_kernels") or 0)
        if raw_kernels > 0 and kseg:
            kseg_raw = sum(int(e.get("count", 0)) for e in kseg)
            if kseg_raw != raw_kernels:
                problems.append(f"raw/kseg mismatch {kseg_raw}!={raw_kernels}")
    drop = [e for e in events if e.get("kind") == "DROP"]
    if len(drop) != 1 or kinds[-1] != "DROP":
        problems.append("DROP not unique last row")
    return {
        "ok": not problems,
        "problems": problems,
        "event_count": len(events),
        "raw_kernels": meta.get("raw_kernels"),
        "finalize_reason": reason,
    }


def _sealed_skeleton_fixture() -> str:
    """Minimal trustworthy-shaped JSONL for negative sidecar tests (no sidecar file)."""
    rows = [
        {
            "run_id": "smoke_neg_fixture",
            "rank": 0,
            "host": "fixture",
            "device_id": 0,
            "stream_id": 1,
            "step": 2,
            "kind": "STEP",
            "start_ns": 1,
            "end_ns": 2,
            "seq": 0,
            "peer_stream": -1,
            "correlation_id": 0,
            "count": 1,
            "bytes": 0,
            "active_ns": 0,
            "span_ns": 0,
            "gap_ns": 0,
            "op": "",
            "comm_name": "",
            "flags": "phase=begin",
        },
        {
            "run_id": "smoke_neg_fixture",
            "rank": 0,
            "host": "fixture",
            "device_id": 0,
            "stream_id": 1,
            "step": 2,
            "kind": "KSEG",
            "start_ns": 3,
            "end_ns": 4,
            "seq": 1,
            "peer_stream": -1,
            "correlation_id": 0,
            "count": 3,
            "bytes": 0,
            "active_ns": 1,
            "span_ns": 2,
            "gap_ns": 0,
            "op": "",
            "comm_name": "",
            "flags": "source=mspti_kernel",
        },
        {
            "run_id": "smoke_neg_fixture",
            "rank": 0,
            "host": "fixture",
            "device_id": 0,
            "stream_id": -1,
            "step": 2,
            "kind": "STEP",
            "start_ns": 5,
            "end_ns": 6,
            "seq": 2,
            "peer_stream": -1,
            "correlation_id": 0,
            "count": 1,
            "bytes": 0,
            "active_ns": 0,
            "span_ns": 0,
            "gap_ns": 0,
            "op": "",
            "comm_name": "",
            "flags": "phase=end",
        },
        {
            "run_id": "smoke_neg_fixture",
            "rank": 0,
            "host": "fixture",
            "device_id": 0,
            "stream_id": -1,
            "step": -1,
            "kind": "DROP",
            "start_ns": 7,
            "end_ns": 8,
            "seq": 3,
            "peer_stream": -1,
            "correlation_id": 0,
            "count": 0,
            "bytes": 0,
            "active_ns": 0,
            "span_ns": 0,
            "gap_ns": 0,
            "op": "",
            "comm_name": "",
            "flags": "allocation=0;queue=0;parse=0;io=0;callback=0;mspti=0;incomplete=0;finalize=1",
        },
    ]
    return "\n".join(json.dumps(row) for row in rows) + "\n"


def negative_missing_sidecar(tmp_dir: Path) -> dict:
    from probing.profiling.npu_sync.ingest import ingest_rank

    dst = tmp_dir / "rank_0000.skeleton.jsonl"
    dst.write_text(_sealed_skeleton_fixture(), encoding="utf-8")
    ingest_rank(dst, runtime_meta={"clean": True, "finalize_complete": True, "finalize_rc": 0})
    from probing.core.engine import query

    rows = query(
        "SELECT trustworthy, untrustworthy_reason FROM python.npu_sync_capture WHERE rank=0"
    ).to_dict(orient="records")
    ok = len(rows) == 1 and rows[0].get("trustworthy") == 0
    return {"ok": ok, "rows": rows}


def negative_dual_switch() -> dict:
    os.environ["PROBING_NPU_SYNC_SKELETON"] = "1"
    os.environ["MSPTI_SKELETON"] = "1"
    os.environ.setdefault("PROBING_NPU_SYNC_SKELETON_LIB", "/dev/null")
    from importlib import reload

    import probing.profiling.npu_sync.config as cfg_mod

    reload(cfg_mod)
    cfg = cfg_mod.load()
    ok = not cfg.enabled and bool(cfg.conflict)
    return {"ok": ok, "conflict": cfg.conflict, "enabled": cfg.enabled}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--phase", choices=("positive", "negatives", "all"), default="all")
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    skill_root = Path(args.skill_root)
    report: dict[str, object] = {"out_dir": str(out_dir)}

    if args.phase in ("positive", "all"):
        report["positive_artifacts"] = validate_positive(out_dir)
        if report["positive_artifacts"].get("ok"):
            report["skill_sql"] = run_skill_sql(out_dir, skill_root)
            trusted = report["skill_sql"]["steps"].get("trusted_ranks", {})
            tr_rows = trusted.get("rows") or []
            trustworthy = bool(tr_rows and tr_rows[0].get("trusted_ranks"))
            report["positive_artifacts"]["trustworthy_via_skill"] = trustworthy
        else:
            report["skill_sql"] = {
                "skipped": True,
                "reason": "positive artifact validation failed",
            }
            report["positive_artifacts"]["trustworthy_via_skill"] = False

    if args.phase in ("negatives", "all"):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            report["negative_missing_sidecar"] = negative_missing_sidecar(Path(td))
        report["negative_dual_switch"] = negative_dual_switch()

    Path(args.report).write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"ok": _all_ok(report), "report": args.report}, indent=2))
    return 0 if _all_ok(report) else 1


def _all_ok(report: dict) -> bool:
    pos = report.get("positive_artifacts") or {}
    if pos and not pos.get("ok"):
        return False
    if pos and not pos.get("trustworthy_via_skill"):
        return False
    neg1 = report.get("negative_missing_sidecar") or {}
    neg2 = report.get("negative_dual_switch") or {}
    if neg1 and not neg1.get("ok"):
        return False
    if neg2 and not neg2.get("ok"):
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())

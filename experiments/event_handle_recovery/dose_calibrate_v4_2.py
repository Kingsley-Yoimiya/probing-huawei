#!/usr/bin/env python3
"""Profiler-based dose calibration for D51 Wait DAG V4.2 (no host fallback)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from wait_dag_v4_intervention import extract_inject_kernel_duration_ns
from wait_dag_v4_2_reverse_candidate import load_profile_window


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--d0-node-wallclock", required=True)
    p.add_argument("--out-json", required=True)
    p.add_argument("--probe-root", required=True)
    p.add_argument("--preload-lib", required=True)
    p.add_argument("--kernel-binary", required=True)
    p.add_argument("--selector-manifest", required=True)
    p.add_argument("--iter-candidates", type=int, nargs="+", required=True)
    p.add_argument("--timeout-s", type=int, default=180)
    return p.parse_args()


def slack_record_to_comm_ns(node_wallclock: Path) -> int:
    rows = list(__import__("csv").DictReader(node_wallclock.open()))
    rec = next((x for x in rows if x.get("node") == "record_task"), None)
    ce = next((x for x in rows if x.get("node") == "comm_entry"), None)
    if not rec or not ce:
        raise SystemExit("STOP_DOSE: missing record_task or comm_entry in node_wallclock")
    start_key = "start_offset_from_upstream_kernel_end_ns"
    end_key = "end_offset_from_upstream_kernel_end_ns"
    if start_key not in ce or end_key not in rec:
        raise SystemExit("STOP_DOSE: node_wallclock missing upstream offset columns")
    slack = int(ce[start_key]) - int(rec[end_key])
    if slack <= 0:
        raise SystemExit(f"STOP_DOSE: invalid S_record_to_comm_ns={slack}")
    return slack


def run_profiler_probe(
    *,
    probe_dir: Path,
    preload_lib: str,
    kernel_binary: str,
    selector_manifest: str,
    iters: int,
    timeout_s: int,
) -> dict:
    probe_dir.mkdir(parents=True, exist_ok=True)
    trace_dir = probe_dir / "event_trace"
    prof_dir = probe_dir / "out" / "args_on"
    trace_dir.mkdir(parents=True, exist_ok=True)
    prof_dir.mkdir(parents=True, exist_ok=True)
    code = r'''
import argparse, json, os, sys, time
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trace-dir"); p.add_argument("--prof-dir")
    p.add_argument("--preload-lib"); p.add_argument("--kernel-binary")
    p.add_argument("--selector-manifest"); p.add_argument("--iters", type=int)
    a = p.parse_args()
    os.environ["ACL_EVENT_TRACE_DIR"] = a.trace_dir
    os.environ["ACL_EVENT_WORK_BINARY"] = a.kernel_binary
    os.environ["ACL_EVENT_SELECTOR_MANIFEST"] = a.selector_manifest
    os.environ["ACL_EVENT_WORK_ITERS"] = str(a.iters)
    os.environ["RANK"] = "0"
    os.environ["LOCAL_RANK"] = "0"
    sys.argv = [sys.argv[0]]
    import torch
    import torch_npu
    from torch_npu.profiler import (
        ExportType,
        ProfilerActivity,
        ProfilerLevel,
        _ExperimentalConfig,
        profile,
        tensorboard_trace_handler,
    )
    from preload_bindings import bind_work_api, load_preload_lib

    lib = bind_work_api(load_preload_lib(a.preload_lib))
    device = torch.device("npu:0")
    torch.npu.set_device(0)
    torch.npu.synchronize()
    comm = torch.npu.Stream()
    compute = torch.npu.Stream()
    event = torch.npu.Event()
    lib.acl_event_work_prepare()
    os.environ["ACL_EVENT_WORK_ITERS"] = str(a.iters)
    lib.acl_event_delay_arm()
    prof_path = Path(a.prof_dir)
    prof_path.mkdir(parents=True, exist_ok=True)
    active_start = time.time_ns()
    exp = _ExperimentalConfig(
        profiler_level=ProfilerLevel.Level1,
        record_op_args=True,
        data_simplification=False,
        export_type=[ExportType.Db],
    )
    with profile(
        activities=[ProfilerActivity.CPU, ProfilerActivity.NPU],
        record_shapes=False,
        profile_memory=False,
        with_stack=False,
        experimental_config=exp,
        on_trace_ready=tensorboard_trace_handler(str(prof_path)),
    ) as prof:
        with torch.npu.stream(comm):
            x = torch.ones(4096, device=device)
            for _ in range(6):
                event.record(comm)
        with torch.npu.stream(compute):
            compute.wait_event(event)
            _ = x + 1
        torch.npu.synchronize()
        prof.step()
    active_end = time.time_ns()
    window = {
        "active_start_realtime_ns": active_start,
        "active_end_realtime_ns": active_end,
        "active_steps": 1,
    }
    (prof_path / "profile_window.json").write_text(json.dumps(window, indent=2) + "\n")
    lib.acl_event_delay_disarm()
    fin = int(lib.acl_event_trace_finalize())
    lib.acl_event_work_cleanup()
    audit_files = list(Path(a.trace_dir).glob("rank_*_pid_*.device_work_audit.json"))
    audit = json.loads(audit_files[0].read_text()) if audit_files else {}
    ok = fin == 0 and int(audit.get("inject_failed", 0)) == 0
    if a.iters > 0:
        ok = ok and int(audit.get("launch_count", 0)) == 1 and int(audit.get("launch_rc", -1)) == 0
    print(json.dumps({"pass": ok, "audit": audit, "finalize_rc": fin}))
    raise SystemExit(0 if ok else 2)
if __name__ == "__main__":
    main()
'''
    cmd = [
        sys.executable,
        "-c",
        code,
        "--trace-dir",
        str(trace_dir),
        "--prof-dir",
        str(prof_dir),
        "--preload-lib",
        preload_lib,
        "--kernel-binary",
        kernel_binary,
        "--selector-manifest",
        selector_manifest,
        "--iters",
        str(iters),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if proc.returncode != 0:
        return {"iters": iters, "pass": False, "stderr": proc.stderr[-800:]}
    meta = json.loads(proc.stdout.strip().splitlines()[-1])
    db_files = list(prof_dir.glob("**/ascend_pytorch_profiler_0.db"))
    if not db_files:
        return {"iters": iters, "pass": False, "error": "no_profiler_db"}
    pw = prof_dir / "profile_window.json"
    active_start, active_end = load_profile_window(pw)
    dur = extract_inject_kernel_duration_ns(db_files[0], active_start, active_end)
    if dur is None or dur <= 0:
        return {"iters": iters, "pass": False, "error": "no_inject_cti_duration"}
    return {
        "iters": iters,
        "pass": True,
        "profiler_duration_ns": dur,
        "audit": meta.get("audit", {}),
    }


def pick_iters(
    curve: list[tuple[int, int]],
    lo: float,
    hi: float,
    prefer: float | None = None,
) -> int | None:
    hits = [it for it, dur in curve if lo <= dur <= hi]
    if not hits:
        return None
    if prefer is None:
        return hits[0]
    best = min(hits, key=lambda it: abs(next(d for i, d in curve if i == it) - prefer))
    return best


def calibrate(
    *,
    s_ns: int,
    curve: list[tuple[int, int]],
) -> dict:
    small_lo, small_hi = 0.15 * s_ns, 0.40 * s_ns
    large_target = s_ns + max(1_000_000, 0.5 * s_ns)
    large_lo = s_ns + 1_000_000
    large_hi = s_ns + max(4_000_000, 0.75 * s_ns)
    large_hi = min(large_hi, s_ns + 20_000_000)
    dsmall = pick_iters(curve, small_lo, small_hi)
    dlarge = pick_iters(curve, large_lo, large_hi, prefer=large_target)
    if dsmall is None or dlarge is None:
        raise SystemExit(
            json.dumps(
                {
                    "status": "STOP_DOSE_CALIBRATION_FAILED",
                    "S_record_to_comm_ns": s_ns,
                    "curve": [{"iters": it, "profiler_duration_ns": dur} for it, dur in curve],
                    "bands": {
                        "Dsmall": [small_lo, small_hi],
                        "Dlarge": [large_lo, large_hi],
                        "Dlarge_target": large_target,
                    },
                },
                indent=2,
            )
        )
    return {
        "S_record_to_comm_ns": int(s_ns),
        "Dsmall_iters": int(dsmall),
        "Dlarge_iters": int(dlarge),
        "calibration_curve": [
            {"iters": it, "profiler_duration_ns": dur} for it, dur in curve
        ],
        "bands": {
            "Dsmall_ns": [int(small_lo), int(small_hi)],
            "Dlarge_ns": [int(large_lo), int(large_hi)],
            "Dlarge_target_ns": int(large_target),
        },
    }


def main() -> None:
    args = parse_args()
    s_ns = slack_record_to_comm_ns(Path(args.d0_node_wallclock))
    probe_root = Path(args.probe_root)
    curve: list[tuple[int, int]] = []
    for iters in args.iter_candidates[:6]:
        probe_dir = probe_root / f"iters_{iters}"
        result = run_profiler_probe(
            probe_dir=probe_dir,
            preload_lib=args.preload_lib,
            kernel_binary=args.kernel_binary,
            selector_manifest=args.selector_manifest,
            iters=iters,
            timeout_s=args.timeout_s,
        )
        if not result.get("pass"):
            print(json.dumps({"status": "PROBE_FAIL", "iters": iters, "result": result}), flush=True)
            raise SystemExit(3)
        curve.append((iters, int(result["profiler_duration_ns"])))
        print(json.dumps({"probe_ok": iters, "duration_ns": result["profiler_duration_ns"]}), flush=True)
    out = calibrate(s_ns=s_ns, curve=curve)
    Path(args.out_json).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out), flush=True)


if __name__ == "__main__":
    main()

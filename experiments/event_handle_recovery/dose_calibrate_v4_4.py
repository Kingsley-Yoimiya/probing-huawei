#!/usr/bin/env python3
"""V4.4 profiler scaling curve + dose freeze (chainout launch, no host fallback)."""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from wait_dag_v4_intervention import extract_inject_kernel_duration_ns
from wait_dag_v4_2_reverse_candidate import load_profile_window

CURVE_ITERS_BASE = [1, 4, 16, 64, 256, 1024, 4096]
CURVE_ITERS_MAX = 1 << 24
REPEATS_PER_POINT = 3
PLATFORM_NS = 20_000  # 20 µs
MAX_PROBE_NS = 20_000_000  # 20 ms


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--d0-node-wallclock", required=True)
    p.add_argument("--out-json", required=True)
    p.add_argument("--probe-root", required=True)
    p.add_argument("--preload-lib", required=True)
    p.add_argument("--kernel-binary", required=True)
    p.add_argument("--selector-manifest", required=True)
    p.add_argument("--kernel-binary-sha256", default="")
    p.add_argument("--block-dim", type=int, default=1)
    p.add_argument("--scratch-bytes", type=int, default=1024)
    p.add_argument("--timeout-s", type=int, default=180)
    p.add_argument("--mode", choices=["curve", "freeze", "full"], default="full")
    return p.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def slack_record_to_comm_ns(node_wallclock: Path) -> int:
    rows = list(csv.DictReader(node_wallclock.open()))
    rec = next((x for x in rows if x.get("node") == "record_task"), None)
    ce = next((x for x in rows if x.get("node") == "comm_entry"), None)
    if not rec or not ce:
        raise SystemExit("STOP_DOSE: missing record_task or comm_entry")
    slack = int(ce["start_offset_from_upstream_kernel_end_ns"]) - int(
        rec["end_offset_from_upstream_kernel_end_ns"]
    )
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
    repeat_idx: int,
) -> dict:
    probe_dir.mkdir(parents=True, exist_ok=True)
    trace_dir = probe_dir / "event_trace"
    prof_dir = probe_dir / "out" / "args_on"
    trace_dir.mkdir(parents=True, exist_ok=True)
    prof_dir.mkdir(parents=True, exist_ok=True)
    code = r'''
import argparse, ctypes, json, os, sys, time
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trace-dir"); p.add_argument("--prof-dir")
    p.add_argument("--preload-lib"); p.add_argument("--kernel-binary")
    p.add_argument("--selector-manifest"); p.add_argument("--iters", type=int)
    p.add_argument("--runtime-nonce", type=int, default=0xD05E0001)
    a = p.parse_args()
    os.environ["ACL_EVENT_TRACE_DIR"] = a.trace_dir
    os.environ["ACL_EVENT_WORK_BINARY"] = a.kernel_binary
    os.environ["ACL_EVENT_SELECTOR_MANIFEST"] = a.selector_manifest
    os.environ["RANK"] = "0"
    os.environ["LOCAL_RANK"] = "0"
    sys.argv = [sys.argv[0]]
    import torch
    import torch_npu
    from torch_npu.profiler import (
        ExportType, ProfilerActivity, ProfilerLevel, _ExperimentalConfig, profile,
        tensorboard_trace_handler,
    )
    from preload_bindings import bind_work_api, load_preload_lib

    SCRATCH_ELEMS = 256
    lib = bind_work_api(load_preload_lib(a.preload_lib))
    lib.acl_event_work_chainout_proof.argtypes = [
        ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_int32),
    ]
    lib.acl_event_work_chainout_proof.restype = ctypes.c_int32

    device = torch.device("npu:0")
    torch.npu.set_device(0)
    torch.npu.synchronize()
    lib.acl_event_work_prepare()
    prof_path = Path(a.prof_dir)
    active_start = time.time_ns()
    exp = _ExperimentalConfig(
        profiler_level=ProfilerLevel.Level1,
        record_op_args=True,
        data_simplification=False,
        export_type=[ExportType.Db],
    )
    launch_rc = ctypes.c_int32(-1)
    out_arr = (ctypes.c_uint32 * SCRATCH_ELEMS)()
    with profile(
        activities=[ProfilerActivity.CPU, ProfilerActivity.NPU],
        record_shapes=False,
        profile_memory=False,
        with_stack=False,
        experimental_config=exp,
        on_trace_ready=tensorboard_trace_handler(str(prof_path)),
    ) as prof:
        nonce = (a.runtime_nonce ^ (a.iters * 0x10001)) & 0xFFFFFFFF
        read_rc = int(lib.acl_event_work_chainout_proof(
            ctypes.c_uint32(a.iters), ctypes.c_uint32(nonce),
            out_arr, ctypes.c_uint32(SCRATCH_ELEMS), ctypes.byref(launch_rc)))
        torch.npu.synchronize()
        prof.step()
    active_end = time.time_ns()
    (prof_path / "profile_window.json").write_text(json.dumps({
        "active_start_realtime_ns": active_start,
        "active_end_realtime_ns": active_end,
        "active_steps": 1,
        "iters": a.iters,
        "runtime_nonce": nonce,
    }, indent=2) + "\n")
    lib.acl_event_work_cleanup()
    audit_files = list(Path(a.trace_dir).glob("rank_*_pid_*.chainout_launch_audit.json"))
    audit = json.loads(audit_files[0].read_text()) if audit_files else {}
    ok = int(launch_rc.value) == 0 and read_rc == 0 and int(audit.get("launch_rc", -1)) == 0
    if a.iters > 0:
        ok = ok and int(audit.get("launch_seq", 0)) >= 1
    print(json.dumps({"pass": ok, "audit": audit, "launch_rc": int(launch_rc.value), "read_rc": read_rc}))
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
        return {
            "iters": iters,
            "repeat": repeat_idx,
            "pass": False,
            "stderr": proc.stderr[-800:],
        }
    meta = json.loads(proc.stdout.strip().splitlines()[-1])
    db_files = list(prof_dir.glob("**/ascend_pytorch_profiler_0.db"))
    if not db_files:
        return {"iters": iters, "repeat": repeat_idx, "pass": False, "error": "no_profiler_db"}
    pw = prof_dir / "profile_window.json"
    active_start, active_end = load_profile_window(pw)
    dur = extract_inject_kernel_duration_ns(db_files[0], active_start, active_end)
    if dur is None or dur <= 0:
        return {"iters": iters, "repeat": repeat_idx, "pass": False, "error": "no_inject_cti_duration"}
    return {
        "iters": iters,
        "repeat": repeat_idx,
        "pass": True,
        "profiler_duration_ns": int(dur),
        "audit": meta.get("audit", {}),
    }


def linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    n = len(xs)
    if n < 2:
        return 0.0, 0.0, 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0, my, 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope, intercept, r2


def evaluate_scaling(curve_points: list[dict]) -> dict:
    """Evaluate Step 5 scaling gates on median curve."""
    medians = []
    for pt in curve_points:
        durs = [int(r["profiler_duration_ns"]) for r in pt["repeats"] if r.get("pass")]
        if not durs:
            pt["status"] = "PROBE_FAIL"
            continue
        pt["durations_ns"] = durs
        pt["median_ns"] = int(statistics.median(durs))
        pt["min_ns"] = min(durs)
        pt["max_ns"] = max(durs)
        pt["mad_ns"] = int(statistics.median([abs(d - pt["median_ns"]) for d in durs]))
        pt["cv"] = (statistics.pstdev(durs) / statistics.mean(durs)) if len(durs) > 1 else 0.0
        medians.append((pt["iters"], pt["median_ns"]))

    reasons: list[str] = []
    if len(medians) < 4:
        return {"pass": False, "status": "DOSE_KERNEL_SCALING_UNAVAILABLE", "reasons": ["too_few_points"]}

    # Linear region: above platform
    lin = [(it, dur) for it, dur in medians if dur > PLATFORM_NS]
    if len(lin) < 4:
        reasons.append("linear_region_lt_4")
    xs = [float(it) for it, _ in lin]
    ys = [float(dur) for _, dur in lin]
    slope, intercept, r2 = linear_fit(xs, ys)
    if slope <= 0:
        reasons.append("slope_nonpos")
    if r2 < 0.98:
        reasons.append(f"r2_low:{r2:.4f}")

    # Strict monotonicity on all medians
    for i in range(1, len(medians)):
        if medians[i][1] <= medians[i - 1][1]:
            reasons.append(f"not_strict_monotone_at_{medians[i][0]}")

  # Per-point fit error and CV
    for pt in curve_points:
        if "median_ns" not in pt:
            continue
        pred = slope * pt["iters"] + intercept
        err = abs(pt["median_ns"] - pred)
        tol = max(PLATFORM_NS, 0.15 * pred)
        if err > tol:
            reasons.append(f"fit_err_{pt['iters']}:{err:.0f}>{tol:.0f}")
        if pt.get("cv", 0) > 0.15:
            reasons.append(f"cv_high_{pt['iters']}:{pt['cv']:.3f}")

    # Adjacent positive unit slope
    for i in range(1, len(lin)):
        dit = lin[i][0] - lin[i - 1][0]
        ddur = lin[i][1] - lin[i - 1][1]
        if ddur <= 0 or ddur / dit <= 0:
            reasons.append(f"adj_slope_nonpos_{lin[i][0]}")

    has_100_500 = any(100_000 <= m[1] <= 500_000 for m in medians)
    has_1_10ms = any(1_000_000 <= m[1] <= 10_000_000 for m in medians)
    if not has_100_500:
        reasons.append("missing_band_0.10_0.50ms")
    if not has_1_10ms:
        reasons.append("missing_band_1_10ms")

    return {
        "pass": len(reasons) == 0,
        "status": "PASS" if not reasons else "DOSE_KERNEL_SCALING_UNAVAILABLE",
        "reasons": reasons,
        "fit": {"slope_ns_per_iter": slope, "intercept_ns": intercept, "r2": r2},
        "medians": medians,
    }


def predict_iters(target_ns: float, slope: float, intercept: float) -> int:
    if slope <= 0:
        return 1
    return max(1, int(round((target_ns - intercept) / slope)))


def probe_dose_candidate(
    *,
    probe_root: Path,
    preload_lib: str,
    kernel_binary: str,
    selector_manifest: str,
    iters: int,
    timeout_s: int,
    label: str,
) -> dict:
    repeats = []
    for rep in range(3):
        pdir = probe_root / label / f"iters_{iters}_rep{rep}"
        repeats.append(
            run_profiler_probe(
                probe_dir=pdir,
                preload_lib=preload_lib,
                kernel_binary=kernel_binary,
                selector_manifest=selector_manifest,
                iters=iters,
                timeout_s=timeout_s,
                repeat_idx=rep,
            )
        )
    durs = [int(r["profiler_duration_ns"]) for r in repeats if r.get("pass")]
    med = int(statistics.median(durs)) if durs else None
    return {"iters": iters, "repeats": repeats, "median_ns": med, "durations_ns": durs}


def freeze_doses(
    *,
    s_ns: int,
    slope: float,
    intercept: float,
    probe_root: Path,
    preload_lib: str,
    kernel_binary: str,
    selector_manifest: str,
    timeout_s: int,
) -> dict:
    small_lo, small_hi = 0.15 * s_ns, 0.40 * s_ns
    large_target = s_ns + max(1_000_000, 0.5 * s_ns)
    large_lo = s_ns + 1_000_000
    large_hi = min(s_ns + max(4_000_000, 0.75 * s_ns), s_ns + 20_000_000)
    bands = {
        "Dsmall_ns": [int(small_lo), int(small_hi)],
        "Dlarge_ns": [int(large_lo), int(large_hi)],
        "Dlarge_target_ns": int(large_target),
    }
    candidates_log: list[dict] = []
    frozen: dict[str, int | None] = {"Dsmall_iters": None, "Dlarge_iters": None}

    for label, lo, hi, prefer in [
        ("Dsmall", small_lo, small_hi, (small_lo + small_hi) / 2),
        ("Dlarge", large_lo, large_hi, large_target),
    ]:
        seed = predict_iters(prefer, slope, intercept)
        tried: list[int] = []
        lo_i, hi_i = max(1, int(lo / max(slope, 1))), max(1, int(hi / max(slope, 1)))
        cand_list = [seed]
        for _ in range(5):
            mid = (lo_i + hi_i) // 2
            if mid not in cand_list:
                cand_list.append(mid)
        for it in cand_list[:6]:
            if it in tried:
                continue
            tried.append(it)
            result = probe_dose_candidate(
                probe_root=probe_root,
                preload_lib=preload_lib,
                kernel_binary=kernel_binary,
                selector_manifest=selector_manifest,
                iters=it,
                timeout_s=timeout_s,
                label=f"freeze_{label}",
            )
            candidates_log.append({"dose": label, **result})
            med = result.get("median_ns")
            if med is None:
                continue
            margin = 0.15 * med
            in_band = lo <= med <= hi
            margin_ok = all(lo - margin <= d <= hi + margin for d in result.get("durations_ns", []))
            if in_band and margin_ok:
                frozen[f"{label}_iters"] = it
                frozen[f"{label}_median_ns"] = med
                break
    if frozen["Dsmall_iters"] is None or frozen["Dlarge_iters"] is None:
        return {
            "status": "STOP_DOSE_CALIBRATION_FAILED",
            "bands": bands,
            "candidates": candidates_log,
            "S_record_to_comm_ns": s_ns,
        }
    return {
        "status": "PASS",
        "bands": bands,
        "S_record_to_comm_ns": s_ns,
        "Dsmall_iters": frozen["Dsmall_iters"],
        "Dlarge_iters": frozen["Dlarge_iters"],
        "Dsmall_median_ns": frozen.get("Dsmall_median_ns"),
        "Dlarge_median_ns": frozen.get("Dlarge_median_ns"),
        "candidates": candidates_log,
    }


def curve_iters_grid() -> list[int]:
    grid = list(CURVE_ITERS_BASE)
    nxt = grid[-1]
    while nxt < CURVE_ITERS_MAX:
        nxt *= 4
        if nxt > CURVE_ITERS_MAX:
            break
        grid.append(nxt)
    return grid


def run_curve(
    *,
    probe_root: Path,
    preload_lib: str,
    kernel_binary: str,
    selector_manifest: str,
    timeout_s: int,
) -> tuple[list[dict], bool]:
    curve_points: list[dict] = []
    curve_iters = curve_iters_grid()
    stop_early = False
    for iters in curve_iters:
        if stop_early:
            curve_points.append({"iters": iters, "skipped": True, "repeats": []})
            continue
        repeats = []
        for rep in range(REPEATS_PER_POINT):
            pdir = probe_root / "curve" / f"iters_{iters}_rep{rep}"
            repeats.append(
                run_profiler_probe(
                    probe_dir=pdir,
                    preload_lib=preload_lib,
                    kernel_binary=kernel_binary,
                    selector_manifest=selector_manifest,
                    iters=iters,
                    timeout_s=timeout_s,
                    repeat_idx=rep,
                )
            )
        curve_points.append({"iters": iters, "repeats": repeats})
        durs = [int(r["profiler_duration_ns"]) for r in repeats if r.get("pass")]
        if durs and statistics.median(durs) > MAX_PROBE_NS:
            stop_early = True
    return curve_points, stop_early


def main() -> None:
    args = parse_args()
    s_ns = slack_record_to_comm_ns(Path(args.d0_node_wallclock))
    probe_root = Path(args.probe_root)
    doc: dict = {
        "utc": utc_now(),
        "status": "IN_PROGRESS",
        "kernel_binary_sha256": args.kernel_binary_sha256,
        "block_dim": args.block_dim,
        "scratch_bytes": args.scratch_bytes,
        "S_record_to_comm_ns": s_ns,
        "curve_iters_grid": curve_iters_grid(),
        "repeats_per_point": REPEATS_PER_POINT,
    }

    if args.mode in ("curve", "full"):
        curve_points, _ = run_curve(
            probe_root=probe_root,
            preload_lib=args.preload_lib,
            kernel_binary=args.kernel_binary,
            selector_manifest=args.selector_manifest,
            timeout_s=args.timeout_s,
        )
        doc["curve"] = curve_points
        scaling = evaluate_scaling(curve_points)
        doc["scaling_eval"] = scaling
        if not scaling["pass"]:
            doc["status"] = "DOSE_KERNEL_SCALING_UNAVAILABLE"
            Path(args.out_json).write_text(json.dumps(doc, indent=2) + "\n")
            print(json.dumps(doc), flush=True)
            raise SystemExit(4)

    if args.mode in ("freeze", "full"):
        scaling = doc.get("scaling_eval") or evaluate_scaling(doc.get("curve", []))
        if not scaling.get("pass"):
            doc["status"] = "DOSE_KERNEL_SCALING_UNAVAILABLE"
            Path(args.out_json).write_text(json.dumps(doc, indent=2) + "\n")
            raise SystemExit(4)
        fit = scaling["fit"]
        freeze = freeze_doses(
            s_ns=s_ns,
            slope=fit["slope_ns_per_iter"],
            intercept=fit["intercept_ns"],
            probe_root=probe_root,
            preload_lib=args.preload_lib,
            kernel_binary=args.kernel_binary,
            selector_manifest=args.selector_manifest,
            timeout_s=args.timeout_s,
        )
        doc["freeze"] = freeze
        doc["bands"] = freeze.get("bands")
        if freeze["status"] != "PASS":
            doc["status"] = freeze["status"]
        else:
            doc["status"] = "PASS"
            doc["Dsmall_iters"] = freeze["Dsmall_iters"]
            doc["Dlarge_iters"] = freeze["Dlarge_iters"]

    Path(args.out_json).write_text(json.dumps(doc, indent=2) + "\n")
    print(json.dumps(doc), flush=True)
    if doc["status"] not in ("PASS",):
        raise SystemExit(5)


if __name__ == "__main__":
    main()

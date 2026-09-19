#!/usr/bin/env python3
"""V4.6 post-Wait comm-stream smoke: explicit compute/comm streams, profiler chain audit."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--trace-root", required=True)
    p.add_argument("--preload-lib", required=True)
    p.add_argument("--kernel-binary", required=True)
    p.add_argument("--selector-manifest", required=True)
    p.add_argument("--iters", type=int, nargs="+", default=[0, 325, 5382])
    p.add_argument("--timeout-s", type=int, default=600)
    p.add_argument("--out-json", required=True)
    return p.parse_args()


def run_one(
    trace_dir: Path,
    preload_lib: str,
    kernel_binary: str,
    selector_manifest: str,
    iters: int,
    timeout_s: int,
) -> dict:
    code = r'''
import argparse, ctypes, json, os, sys, time
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trace-dir"); p.add_argument("--preload-lib")
    p.add_argument("--kernel-binary"); p.add_argument("--iters", type=int)
    p.add_argument("--selector-manifest"); p.add_argument("--timeout-s", type=int)
    a = p.parse_args()
    os.environ["ACL_EVENT_TRACE_DIR"] = a.trace_dir
    os.environ["ACL_EVENT_WORK_BINARY"] = a.kernel_binary
    os.environ["ACL_EVENT_SELECTOR_MANIFEST"] = a.selector_manifest
    os.environ["ACL_EVENT_INJECT_SITE"] = "AFTER_SUCCESSFUL_TARGET_WAIT"
    os.environ["RANK"] = "0"
    os.environ["LOCAL_RANK"] = "0"
    Path(a.trace_dir).mkdir(parents=True, exist_ok=True)
    sys.argv = [sys.argv[0]]
    import torch, torch_npu
    from preload_bindings import bind_work_api, load_preload_lib

    lib = bind_work_api(load_preload_lib(a.preload_lib))
    device = torch.device("npu:0")
    torch.npu.set_device(0)
    torch.npu.synchronize()
    comm = torch.npu.Stream()
    compute = torch.npu.Stream()
    event = torch.npu.Event()
    lib.acl_event_work_prepare()
    t0 = time.time()
    os.environ["ACL_EVENT_WORK_ITERS"] = str(a.iters)
    lib.acl_event_delay_arm()
    with torch.npu.stream(compute):
        x = torch.ones(4096, device=device)
        for _ in range(4):
            event.record(compute)
    with torch.npu.stream(comm):
        comm.wait_event(event)
        y = x + 1
    torch.npu.synchronize()
    lib.acl_event_delay_disarm()
    fin = int(lib.acl_event_trace_finalize())
    lib.acl_event_work_cleanup()
    elapsed = time.time() - t0
    audits = list(Path(a.trace_dir).glob("rank_*_pid_*.device_work_audit.json"))
    audit = json.loads(audits[0].read_text()) if audits else {}
    lc = int(audit.get("launch_count", 0))
    site = str(audit.get("inject_site", ""))
    ok = elapsed < a.timeout_s and fin == 0 and int(audit.get("inject_failed", 0)) == 0
    if a.iters > 0:
        ok = ok and lc == 1 and site == "AFTER_SUCCESSFUL_TARGET_WAIT"
    else:
        ok = ok and lc == 0
    out = {"iters": a.iters, "elapsed_s": round(elapsed, 3), "finalize_rc": fin, "audit": audit, "pass": ok}
    print(json.dumps(out))
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
        "--preload-lib",
        preload_lib,
        "--kernel-binary",
        kernel_binary,
        "--iters",
        str(iters),
        "--selector-manifest",
        selector_manifest,
        "--timeout-s",
        str(timeout_s),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 30)
    body = proc.stdout.strip().splitlines()
    last = body[-1] if body else "{}"
    try:
        row = json.loads(last)
    except json.JSONDecodeError:
        row = {"iters": iters, "pass": False, "error": proc.stdout + proc.stderr}
    row["exit_code"] = proc.returncode
    return row


def main() -> None:
    args = parse_args()
    root = Path(args.trace_root)
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for iters in args.iters:
        tag = f"iters_{iters}"
        trace_dir = root / tag / "event_trace"
        rows.append(
            run_one(
                trace_dir,
                args.preload_lib,
                args.kernel_binary,
                args.selector_manifest,
                iters,
                args.timeout_s,
            )
        )
    summary = {
        "pass": all(r.get("pass") for r in rows),
        "rows": rows,
        "inject_site": "AFTER_SUCCESSFUL_TARGET_WAIT",
    }
    Path(args.out_json).write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    if not summary["pass"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

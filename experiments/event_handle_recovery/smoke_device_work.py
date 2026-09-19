#!/usr/bin/env python3
"""V4 kernel smoke: measure realized duration vs iteration on device stream."""
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
    p.add_argument("--trace-dir", required=True)
    p.add_argument("--preload-lib", required=True)
    p.add_argument("--kernel-binary", required=True)
    p.add_argument("--selector-manifest", default="")
    p.add_argument("--iters", type=int, nargs="+", default=[0, 10, 100, 500, 2000])
    p.add_argument("--timeout-s", type=int, default=300)
    return p.parse_args()


def run_one(
    trace_dir: Path,
    preload_lib: str,
    kernel_binary: str,
    iters: int,
    timeout_s: int,
    selector_manifest: str = "",
) -> dict:
    code = r'''
import argparse, ctypes, json, os, time, sys
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trace-dir"); p.add_argument("--preload-lib")
    p.add_argument("--kernel-binary"); p.add_argument("--iters", type=int)
    p.add_argument("--selector-manifest", default="")
    p.add_argument("--timeout-s", type=int)
    a = p.parse_args()
    os.environ["ACL_EVENT_TRACE_DIR"] = a.trace_dir
    os.environ["ACL_EVENT_WORK_BINARY"] = a.kernel_binary
    if a.selector_manifest:
        os.environ["ACL_EVENT_SELECTOR_MANIFEST"] = a.selector_manifest
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
    with torch.npu.stream(comm):
        x = torch.ones(4096, device=device)
        for _ in range(6):
            event.record(comm)
    with torch.npu.stream(compute):
        compute.wait_event(event)
        _ = x + 1
    torch.npu.synchronize()
    lib.acl_event_delay_disarm()
    fin = int(lib.acl_event_trace_finalize())
    lib.acl_event_work_cleanup()
    elapsed = time.time() - t0
    audit_files = list(Path(a.trace_dir).glob("rank_*_pid_*.device_work_audit.json"))
    audit = json.loads(audit_files[0].read_text()) if audit_files else {}
    ok = elapsed < a.timeout_s and fin == 0 and int(audit.get("inject_failed", 0)) == 0
    if int(audit.get("scratch_bytes", 0)) <= 0:
        ok = False
    if a.iters > 0:
        ok = ok and int(audit.get("launch_count", 0)) == 1 and int(audit.get("launch_rc", -1)) == 0
    else:
        ok = ok and int(audit.get("launch_count", 0)) == 0
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
        "--timeout-s",
        str(timeout_s),
    ]
    if selector_manifest:
        cmd.extend(["--selector-manifest", selector_manifest])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 60)
    if proc.returncode != 0:
        return {"iters": iters, "pass": False, "stderr": proc.stderr[-800:]}
    line = proc.stdout.strip().splitlines()[-1]
    return json.loads(line)


def main() -> None:
    args = parse_args()
    trace_root = Path(args.trace_dir)
    trace_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for iters in args.iters:
        sub = trace_root / f"iters_{iters}"
        rows.append(
            run_one(
                sub,
                args.preload_lib,
                args.kernel_binary,
                iters,
                args.timeout_s,
                args.selector_manifest,
            )
        )
    out = {"results": rows}
    (trace_root / "smoke_summary.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    if not all(r.get("pass") for r in rows):
        raise SystemExit(2)


if __name__ == "__main__":
    main()

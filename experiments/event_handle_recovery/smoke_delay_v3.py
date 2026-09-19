#!/usr/bin/env python3
"""V3 single-card stream smoke: D0 then D2ms HostFunc delay without deadlock."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--trace-dir", required=True)
    p.add_argument("--preload-lib", required=True)
    p.add_argument("--delay-us", type=int, default=0)
    p.add_argument("--tag", default="d0")
    p.add_argument("--timeout-s", type=int, default=300)
    p.add_argument("--child", action="store_true")
    return p.parse_args()


def run_case_child(preload_lib: str, trace_dir: Path, delay_us: int, tag: str, timeout_s: int) -> dict:
    code = r'''
import argparse, ctypes, json, os, time
from pathlib import Path
import torch, torch_npu

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trace-dir"); p.add_argument("--preload-lib")
    p.add_argument("--delay-us", type=int); p.add_argument("--tag"); p.add_argument("--timeout-s", type=int)
    a = p.parse_args()
    os.environ["ACL_EVENT_TRACE_DIR"] = a.trace_dir
    os.environ["ACL_EVENT_DELAY_US"] = str(a.delay_us)
    os.environ["RANK"] = "0"
    os.environ["LOCAL_RANK"] = "0"
    Path(a.trace_dir).mkdir(parents=True, exist_ok=True)
    lib = ctypes.CDLL(a.preload_lib)
    lib.acl_event_trace_finalize.restype = ctypes.c_int
    lib.acl_event_delay_arm.restype = None
    lib.acl_event_delay_disarm.restype = None
    t0 = time.time()
    device = torch.device("npu:0")
    torch.npu.set_device(0)
    comm = torch.npu.Stream()
    compute = torch.npu.Stream()
    event = torch.npu.Event()
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
    elapsed = time.time() - t0
    audit_files = list(Path(a.trace_dir).glob("rank_*_pid_*.delay_audit.json"))
    audit = json.loads(audit_files[0].read_text()) if audit_files else {}
    cb_ns = 0
    if audit.get("callback_enter_monotonic_ns") and audit.get("callback_exit_monotonic_ns"):
        cb_ns = int(audit["callback_exit_monotonic_ns"]) - int(audit["callback_enter_monotonic_ns"])
    ok = elapsed < a.timeout_s and fin == 0 and int(audit.get("inject_failed", 0)) == 0
    if a.delay_us > 0:
        ok = ok and int(audit.get("match_count", 0)) == 1 and int(audit.get("hostfunc_submit_rc", -1)) == 0
    else:
        ok = ok and int(audit.get("match_count", 0)) == 0
    out = {"tag": a.tag, "delay_us": a.delay_us, "elapsed_s": round(elapsed, 3), "finalize_rc": fin,
           "audit": audit, "callback_duration_ms": round(cb_ns/1e6, 3) if cb_ns else 0.0, "pass": ok}
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
        "--delay-us",
        str(delay_us),
        "--tag",
        tag,
        "--timeout-s",
        str(timeout_s),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 30)
    if proc.returncode != 0:
        return {"tag": tag, "delay_us": delay_us, "pass": False, "stderr": proc.stderr[-500:]}
    line = proc.stdout.strip().splitlines()[-1]
    return json.loads(line)


def main() -> None:
    args = parse_args()
    root = Path(args.trace_dir)
    results = [
        run_case_child(args.preload_lib, root / "smoke_d0", 0, "d0", args.timeout_s),
        run_case_child(
            args.preload_lib, root / "smoke_d2ms", max(args.delay_us, 2000), "d2ms", args.timeout_s
        ),
    ]
    out = {"cases": results, "pass": all(r.get("pass") for r in results)}
    root.mkdir(parents=True, exist_ok=True)
    (root / "smoke_delay_v3.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out), flush=True)
    if not out["pass"]:
        raise SystemExit(2)
    print("SMOKE_DELAY_V3_PASS", flush=True)


if __name__ == "__main__":
    main()


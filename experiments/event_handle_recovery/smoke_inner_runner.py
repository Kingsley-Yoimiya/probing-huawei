#!/usr/bin/env python3
import argparse, ctypes, json, os, sys, time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--trace-dir", required=True)
p.add_argument("--preload-lib", required=True)
p.add_argument("--kernel-binary", required=True)
p.add_argument("--iters", type=int, required=True)
p.add_argument("--timeout-s", type=int, default=120)
a = p.parse_args()
os.environ["ACL_EVENT_TRACE_DIR"] = a.trace_dir
os.environ["ACL_EVENT_WORK_BINARY"] = a.kernel_binary
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
audit_files = list(Path(a.trace_dir).glob("rank_*_pid_*.device_work_audit.json"))
audit = json.loads(audit_files[0].read_text()) if audit_files else {}
ok = fin == 0 and int(audit.get("inject_failed", 0)) == 0 and int(audit.get("scratch_bytes", 0)) > 0
if a.iters > 0:
    ok = ok and int(audit.get("launch_count", 0)) == 1
else:
    ok = ok and int(audit.get("launch_count", 0)) == 0
print(json.dumps({"iters": a.iters, "pass": ok, "audit": audit}))
sys.exit(0 if ok else 2)

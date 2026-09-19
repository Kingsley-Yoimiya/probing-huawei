#!/usr/bin/env python3
"""Step-by-step smoke debug to locate buffer overflow."""
import ctypes
import os
import sys
import time
from pathlib import Path


def step(msg: str) -> None:
    print(f"STEP {msg}", flush=True)


trace_dir = os.environ.get("ACL_EVENT_TRACE_DIR", "/tmp/debug_smoke_steps")
os.makedirs(trace_dir, exist_ok=True)
os.environ["ACL_EVENT_TRACE_DIR"] = trace_dir
os.environ.setdefault(
    "ACL_EVENT_WORK_BINARY",
    "/root/event_handle_recovery_v4/build/kernels/d51_compute_delay_kernel.o",
)
os.environ["ACL_EVENT_WORK_ITERS"] = os.environ.get("ACL_EVENT_WORK_ITERS", "0")
os.environ["RANK"] = "0"
os.environ["LOCAL_RANK"] = "0"

step("import torch")
import torch

step("import torch_npu")
import torch_npu

step("set_device")
torch.npu.set_device(0)
torch.npu.synchronize()

step("load lib")
lib_path = os.environ.get(
    "PRELOAD_LIB",
    "/root/event_handle_recovery_v4/build/libacl_event_trace_v2.so",
)
lib = ctypes.CDLL(lib_path)
lib.acl_event_trace_finalize.restype = ctypes.c_int
lib.acl_event_work_prepare.restype = None
lib.acl_event_work_arm.restype = None
lib.acl_event_work_disarm.restype = None
lib.acl_event_work_cleanup.restype = None

step("prepare")
lib.acl_event_work_prepare()

step("arm")
lib.acl_event_work_arm()

device = torch.device("npu:0")
comm = torch.npu.Stream()
compute = torch.npu.Stream()
event = torch.npu.Event()

step("comm stream records")
with torch.npu.stream(comm):
    x = torch.ones(4096, device=device)
    for i in range(6):
        print(f"  record {i}", flush=True)
        event.record(comm)

step("compute stream")
with torch.npu.stream(compute):
    compute.wait_event(event)
    _ = x + 1

step("sync")
torch.npu.synchronize()

step("disarm")
lib.acl_event_work_disarm()

step("finalize")
fin = int(lib.acl_event_trace_finalize())
print(f"finalize_rc={fin}", flush=True)

step("cleanup")
lib.acl_event_work_cleanup()

audit_files = list(Path(trace_dir).glob("rank_*_pid_*.device_work_audit.json"))
if audit_files:
    print(audit_files[0].read_text(), flush=True)

step("done")

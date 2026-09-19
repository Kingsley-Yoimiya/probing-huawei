#!/usr/bin/env python3
"""Direct (no subprocess) prepare test for iters 0 vs 50."""
import ctypes
import os
import sys

os.environ.setdefault(
    "ACL_EVENT_WORK_BINARY",
    "/root/event_handle_recovery_v4/build/kernels/d51_compute_delay_kernel.o",
)
sys.argv = [sys.argv[0]]
import torch
import torch_npu

torch.npu.set_device(0)
torch.npu.synchronize()
lib = ctypes.CDLL("/root/event_handle_recovery_v4/build/libacl_event_trace_v2.so")
lib.acl_event_work_prepare.restype = None
lib.acl_event_work_cleanup.restype = None

for iters in (0, 50, 0):
    td = f"/tmp/direct_prep_{iters}"
    os.makedirs(td, exist_ok=True)
    os.environ["ACL_EVENT_TRACE_DIR"] = td
    os.environ["ACL_EVENT_WORK_ITERS"] = str(iters)
    print(f"prepare iters={iters}", flush=True)
    lib.acl_event_work_prepare()
    lib.acl_event_work_cleanup()
    print(f"done iters={iters}", flush=True)

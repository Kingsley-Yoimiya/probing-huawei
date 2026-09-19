#!/usr/bin/env python3
"""Prepare-only debug - no ctypes reload."""
import os
import sys

os.environ.setdefault("ACL_EVENT_TRACE_DIR", "/tmp/debug_prepare_only")
os.environ.setdefault(
    "ACL_EVENT_WORK_BINARY",
    "/root/event_handle_recovery_v4/build/kernels/d51_compute_delay_kernel.o",
)
os.environ["RANK"] = "0"
os.makedirs(os.environ["ACL_EVENT_TRACE_DIR"], exist_ok=True)

print("import torch_npu", flush=True)
import torch
import torch_npu

torch.npu.set_device(0)
torch.npu.synchronize()

# Use LD_PRELOAD-loaded lib via ctypes RTLD_DEFAULT
import ctypes
lib = ctypes.CDLL(None)  # search global symbols from LD_PRELOAD
prepare = ctypes.CFUNCTYPE(None)("acl_event_work_prepare")
print("calling prepare", flush=True)
prepare()
print("prepare_ok", flush=True)

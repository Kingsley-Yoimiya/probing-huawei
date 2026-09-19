#!/usr/bin/env python3
"""Single-card ACL event Record/Wait smoke with V2 LD_PRELOAD trace finalize."""
from __future__ import annotations

import argparse
import ctypes
import glob
import json
import os
import struct
import sys
import time
from pathlib import Path

import torch
import torch_npu  # noqa: F401

ACL_EVENT_TRACE_MAGIC = 0x41435445
ACL_EVENT_TRACE_VERSION = 2
RECORD_FMT = "<IBBHQQIIiiQQQQQQIiBBBBQ"
HEADER_FMT = "<IIQQQQQiiIIQQQQQQQ"
TRAILER_FMT = "<QQII"
RECORD_SIZE = 112
HEADER_SIZE = 120
TRAILER_SIZE = 24

OP_RECORD = 4
OP_WAIT = 5


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--trace-dir", required=True)
    p.add_argument("--preload-lib", required=True)
    p.add_argument("--timeout-s", type=int, default=60)
    return p.parse_args()


def finalize_trace(preload_lib: str) -> int:
    lib = ctypes.CDLL(preload_lib)
    lib.acl_event_trace_finalize.restype = ctypes.c_int
    return int(lib.acl_event_trace_finalize())


def load_header(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < HEADER_SIZE + TRAILER_SIZE:
        raise ValueError("trace file too small")
    header = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])
    trailer_off = len(data) - TRAILER_SIZE
    trailer = struct.unpack(TRAILER_FMT, data[trailer_off:])
    return {
        "magic": header[0],
        "version": header[1],
        "capacity": header[2],
        "committed_count": header[3],
        "dropped": header[4],
        "fatal": header[5],
        "late_calls": header[6],
        "pid": header[7],
        "rank": header[8],
        "resolver_queries": header[11],
        "resolver_wrappers_returned": header[12],
        "resolver_target_conflict": header[13],
        "resolver_audit_overflow": header[14],
        "acl_create_wrapper_calls": header[15],
        "rt_create_wrapper_calls": header[16],
        "logical_create_count": header[17],
        "trailer_committed": trailer[0],
        "record_checksum": trailer[1],
    }


def count_ops(path: Path) -> dict[str, int]:
    data = path.read_bytes()
    body = data[HEADER_SIZE:-TRAILER_SIZE]
    n = len(body) // RECORD_SIZE
    counts = {"create": 0, "record": 0, "wait": 0, "destroy": 0, "reset": 0}
    for i in range(n):
        op = body[i * RECORD_SIZE + 4]
        ret = struct.unpack_from("<i", body, i * RECORD_SIZE + 92)[0]
        if ret != 0:
            continue
        if op in (1, 2, 3, 9, 10, 11):
            counts["create"] += 1
        elif op == OP_RECORD:
            counts["record"] += 1
        elif op == OP_WAIT:
            counts["wait"] += 1
        elif op == 7:
            counts["destroy"] += 1
        elif op == 6:
            counts["reset"] += 1
    return counts


def main() -> None:
    args = parse_args()
    os.environ["ACL_EVENT_TRACE_DIR"] = args.trace_dir
    Path(args.trace_dir).mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    device = torch.device("npu:0")
    torch.npu.set_device(0)
    comm = torch.npu.Stream()
    compute = torch.npu.Stream()
    event = torch.npu.Event()
    with torch.npu.stream(comm):
        x = torch.ones(4, device=device)
        event.record(comm)
    with torch.npu.stream(compute):
        compute.wait_event(event)
        y = x + 1
    torch.npu.synchronize()
    fin = finalize_trace(args.preload_lib)
    elapsed = time.time() - t0
    bins = sorted(glob.glob(os.path.join(args.trace_dir, "rank_*_pid_*.events.bin")))
    if not bins:
        print("SMOKE_FAIL no trace bin", flush=True)
        raise SystemExit(1)
    hdr = load_header(Path(bins[0]))
    if hdr["version"] != ACL_EVENT_TRACE_VERSION:
        print("SMOKE_FAIL bad trace version", flush=True)
        raise SystemExit(1)
    ops = count_ops(Path(bins[0]))
    logical_create = int(hdr["logical_create_count"])
    result = {
        "elapsed_s": round(elapsed, 3),
        "finalize_rc": fin,
        "bin": bins[0],
        "header": hdr,
        "ops": ops,
        "logical_create": logical_create,
        "pass": (
            elapsed < args.timeout_s
            and fin == 0
            and hdr["dropped"] == 0
            and hdr["fatal"] == 0
            and hdr["late_calls"] == 0
            and hdr["resolver_target_conflict"] == 0
            and hdr["resolver_audit_overflow"] == 0
            and logical_create >= 1
            and hdr["acl_create_wrapper_calls"] >= 1
            and hdr["resolver_wrappers_returned"] >= 1
            and ops["record"] >= 1
            and ops["wait"] >= 1
            and ops["record"] == ops["wait"] == 1
        ),
    }
    out = Path(args.trace_dir) / "smoke_result.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    if not result["pass"]:
        raise SystemExit(2)
    print("SMOKE_PASS", flush=True)


if __name__ == "__main__":
    main()

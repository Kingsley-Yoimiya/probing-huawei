#!/usr/bin/env python3
"""V4.4 GM reachability probe via device_work chain-out proof path."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from pathlib import Path

from d51_work_unit_reference import (
    DEFAULT_SCRATCH_ELEMS,
    DONE_MARK_SLOT,
    FINAL_STATE_SLOT,
    SUMMARY_SLOT,
    guard_slots,
    init_scratch_with_nonce,
    proof_slots,
    run_reference,
)
from preload_bindings import bind_work_api, load_preload_lib

# Distinct runtime nonces per Plan Step 4 probe point.
PROBE_NONCES = {1: 0xC0FFEE01, 2: 0xC0FFEE02, 17: 0xC0FFEE11, 257: 0xC0FFEEFF}
FIXED_NONCE_SUMMARY_ITERS = [1, 2, 17, 257, 1024]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--preload-lib", required=True)
    p.add_argument("--kernel-binary", required=True)
    p.add_argument("--selector-manifest", required=True)
    p.add_argument("--trace-dir", required=True)
    p.add_argument("--out-json", required=True)
    p.add_argument("--iters", type=int, nargs="+", default=[1, 2, 17, 257])
    return p.parse_args()


def bind_chainout_proof(lib: ctypes.CDLL) -> None:
    lib.acl_event_work_chainout_proof.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_int32),
    ]
    lib.acl_event_work_chainout_proof.restype = ctypes.c_int32


def run_one_probe(lib: ctypes.CDLL, iters: int, runtime_nonce: int) -> dict:
    init = init_scratch_with_nonce(runtime_nonce)
    init_copy = list(init)
    ref_scratch, ref_meta = run_reference(init_copy, iters)

    out_arr = (ctypes.c_uint32 * DEFAULT_SCRATCH_ELEMS)()
    launch_rc = ctypes.c_int32(-1)
    read_rc = int(
        lib.acl_event_work_chainout_proof(
            ctypes.c_uint32(iters),
            ctypes.c_uint32(runtime_nonce),
            out_arr,
            ctypes.c_uint32(DEFAULT_SCRATCH_ELEMS),
            ctypes.byref(launch_rc),
        )
    )
    device = [int(out_arr[i]) & 0xFFFFFFFF for i in range(DEFAULT_SCRATCH_ELEMS)]

    guards_ok = all(device[i] == init[i] for i in guard_slots())
    proof_changed = any(device[i] != init[i] for i in proof_slots())
    full_match = device == ref_scratch
    diff_slots = [i for i in range(DEFAULT_SCRATCH_ELEMS) if device[i] != ref_scratch[i]]

    return {
        "iters": iters,
        "runtime_nonce": runtime_nonce,
        "launch_rc": int(launch_rc.value),
        "read_rc": read_rc,
        "device_proof_match": full_match,
        "guards_unchanged": guards_ok,
        "proof_area_modified": proof_changed,
        "device_nonce": device[0],
        "input_nonce": init[0],
        "device_summary": device[SUMMARY_SLOT],
        "reference_summary": ref_meta["summary"],
        "device_done_mark": device[DONE_MARK_SLOT],
        "reference_done_mark": ref_meta["done_mark"],
        "device_scratch_slots": {
            str(i): device[i] for i in (0, 4, 68, SUMMARY_SLOT, FINAL_STATE_SLOT, DONE_MARK_SLOT)
        },
        "reference_scratch_slots": {
            str(i): ref_scratch[i]
            for i in (0, 4, 68, SUMMARY_SLOT, FINAL_STATE_SLOT, DONE_MARK_SLOT)
        },
        "diff_slot_count": len(diff_slots),
        "diff_slots_head": diff_slots[:16],
        "pass": int(launch_rc.value) == 0
        and read_rc == 0
        and full_match
        and guards_ok
        and proof_changed,
    }


def main() -> None:
    args = parse_args()
    os.environ["ACL_EVENT_TRACE_DIR"] = args.trace_dir
    os.environ["ACL_EVENT_WORK_BINARY"] = args.kernel_binary
    os.environ["ACL_EVENT_SELECTOR_MANIFEST"] = args.selector_manifest
    os.environ["RANK"] = "0"
    os.environ["LOCAL_RANK"] = "0"
    Path(args.trace_dir).mkdir(parents=True, exist_ok=True)

    sys.argv = [sys.argv[0]]
    import torch
    import torch_npu  # noqa: F401

    torch.npu.set_device(0)
    torch.npu.synchronize()

    lib = bind_work_api(load_preload_lib(args.preload_lib))
    bind_chainout_proof(lib)
    lib.acl_event_work_prepare()

    probe_rows = []
    for iters in args.iters:
        nonce = PROBE_NONCES.get(iters, 0xD51E0000 + iters)
        probe_rows.append(run_one_probe(lib, iters, nonce))

    fixed_nonce = 0xFACEFEED
    summary_rows = []
    for iters in FIXED_NONCE_SUMMARY_ITERS:
        row = run_one_probe(lib, iters, fixed_nonce)
        summary_rows.append(
            {
                "iters": iters,
                "summary": row["device_summary"],
                "done_mark": row["device_done_mark"],
            }
        )
    unique_summaries = len({r["summary"] for r in summary_rows})
    summary_gate = unique_summaries >= 3

    lib.acl_event_work_cleanup()

    out = {
        "kernel_binary": args.kernel_binary,
        "scratch_elems": DEFAULT_SCRATCH_ELEMS,
        "probe_points": probe_rows,
        "fixed_nonce_summary_probe": {
            "runtime_nonce": fixed_nonce,
            "rows": summary_rows,
            "unique_summary_count": unique_summaries,
            "pass": summary_gate,
        },
        "pass": all(r["pass"] for r in probe_rows) and summary_gate,
        "path": "device_work_chainout_proof",
    }
    Path(args.out_json).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    if not out["pass"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

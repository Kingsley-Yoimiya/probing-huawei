#!/usr/bin/env python3
"""Chain-out checksum smoke for V4.3 via device_work launch path."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from d51_work_unit_reference import (
    DEFAULT_SCRATCH_ELEMS,
    init_scratch_pattern,
    run_reference,
)
from preload_bindings import bind_work_api, load_preload_lib


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--preload-lib", required=True)
    p.add_argument("--kernel-binary", required=True)
    p.add_argument("--selector-manifest", required=True)
    p.add_argument("--trace-dir", required=True)
    p.add_argument("--out-json", required=True)
    p.add_argument("--iters", type=int, nargs="+", default=[1, 2, 17, 257])
    return p.parse_args()


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
    lib.acl_event_work_chainout_checksum = lib.acl_event_work_chainout_checksum
    lib.acl_event_work_chainout_checksum.argtypes = [
        __import__("ctypes").c_uint32,
        __import__("ctypes").POINTER(__import__("ctypes").c_int32),
    ]
    lib.acl_event_work_chainout_checksum.restype = __import__("ctypes").c_uint32

    lib.acl_event_work_prepare()
    rows = []
    init = init_scratch_pattern(DEFAULT_SCRATCH_ELEMS)
    for iters in args.iters:
        launch_rc = __import__("ctypes").c_int32(-1)
        checksum = int(lib.acl_event_work_chainout_checksum(iters, __import__("ctypes").byref(launch_rc)))
        ref_scratch, ref_meta = run_reference(list(init), iters)
        ref_checksum = ref_meta.get("summary", ref_scratch[0])
        ok = int(launch_rc.value) == 0 and (iters == 0 or checksum == ref_checksum)
        rows.append(
            {
                "iters": iters,
                "pass": ok,
                "device_checksum": checksum,
                "reference_checksum": ref_checksum,
                "launch_rc": int(launch_rc.value),
            }
        )
    lib.acl_event_work_cleanup()

    out = {
        "kernel_binary": args.kernel_binary,
        "scratch_elems": DEFAULT_SCRATCH_ELEMS,
        "results": rows,
        "pass": all(r.get("pass") for r in rows),
        "path": "device_work_chainout",
    }
    Path(args.out_json).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    if not out["pass"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""V4.4 bidirectional unique TASK projection probe (single launch)."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from d51_work_unit_reference import DEFAULT_SCRATCH_ELEMS, init_scratch_with_nonce, run_reference
from preload_bindings import bind_work_api, load_preload_lib
from wait_dag_v4_2_reverse_candidate import load_profile_window
from wait_dag_v4_intervention import inject_cti_rowids, resolve_inject_cti_name_id

KERNEL_NAME = "d51_compute_delay_kernel"
PROBE_ITERS = 17
PROBE_NONCE = 0x0E0BE001


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--preload-lib", required=True)
    p.add_argument("--kernel-binary", required=True)
    p.add_argument("--selector-manifest", required=True)
    p.add_argument("--probe-dir", required=True)
    p.add_argument("--out-json", required=True)
    p.add_argument("--timeout-s", type=int, default=180)
    return p.parse_args()


def cti_tasks_in_window(db_path: Path, active_start: int, active_end: int) -> list[dict]:
    name_id = resolve_inject_cti_name_id(db_path)
    if name_id is None:
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    rows = cur.execute(
        """
        SELECT t.rowid, t.startNs, t.endNs, t.endNs - t.startNs AS dur, t.streamId
        FROM TASK AS t
        JOIN COMPUTE_TASK_INFO AS c ON t.globalTaskId = c.globalTaskId
        WHERE c.name = ? AND t.startNs BETWEEN ? AND ?
        ORDER BY t.startNs
        """,
        (name_id, active_start, active_end),
    ).fetchall()
    con.close()
    return [
        {
            "rowid": int(r[0]),
            "start_ns": int(r[1]),
            "end_ns": int(r[2]),
            "duration_ns": int(r[3]),
            "stream_id": int(r[4]) if r[4] is not None else None,
        }
        for r in rows
    ]


def run_profiler_launch_probe(
    probe_dir: Path,
    preload_lib: str,
    kernel_binary: str,
    selector_manifest: str,
    iters: int,
    runtime_nonce: int,
    timeout_s: int,
) -> dict:
    code = r'''
import argparse, ctypes, json, os, sys, time
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trace-dir"); p.add_argument("--prof-dir")
    p.add_argument("--preload-lib"); p.add_argument("--kernel-binary")
    p.add_argument("--selector-manifest"); p.add_argument("--iters", type=int)
    p.add_argument("--runtime-nonce", type=int)
    a = p.parse_args()
    os.environ["ACL_EVENT_TRACE_DIR"] = a.trace_dir
    os.environ["ACL_EVENT_WORK_BINARY"] = a.kernel_binary
    os.environ["ACL_EVENT_SELECTOR_MANIFEST"] = a.selector_manifest
    os.environ["RANK"] = "0"; os.environ["LOCAL_RANK"] = "0"
    sys.argv = [sys.argv[0]]
    import torch, torch_npu
    from torch_npu.profiler import (
        ExportType, ProfilerActivity, ProfilerLevel, _ExperimentalConfig, profile,
        tensorboard_trace_handler,
    )
    from preload_bindings import bind_work_api, load_preload_lib
    SCRATCH_ELEMS = 256
    lib = bind_work_api(load_preload_lib(a.preload_lib))
    lib.acl_event_work_chainout_proof.argtypes = [
        ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_int32),
    ]
    lib.acl_event_work_chainout_proof.restype = ctypes.c_int32
    torch.npu.set_device(0); torch.npu.synchronize()
    lib.acl_event_work_prepare()
    prof_path = Path(a.prof_dir)
    active_start = time.time_ns()
    exp = _ExperimentalConfig(
        profiler_level=ProfilerLevel.Level1, record_op_args=True,
        data_simplification=False, export_type=[ExportType.Db],
    )
    launch_rc = ctypes.c_int32(-1)
    out_arr = (ctypes.c_uint32 * SCRATCH_ELEMS)()
    with profile(
        activities=[ProfilerActivity.CPU, ProfilerActivity.NPU],
        record_shapes=False, profile_memory=False, with_stack=False,
        experimental_config=exp,
        on_trace_ready=tensorboard_trace_handler(str(prof_path)),
    ) as prof:
        read_rc = int(lib.acl_event_work_chainout_proof(
            ctypes.c_uint32(a.iters), ctypes.c_uint32(a.runtime_nonce),
            out_arr, ctypes.c_uint32(SCRATCH_ELEMS), ctypes.byref(launch_rc)))
        torch.npu.synchronize(); prof.step()
    active_end = time.time_ns()
    (prof_path / "profile_window.json").write_text(json.dumps({
        "active_start_realtime_ns": active_start,
        "active_end_realtime_ns": active_end,
        "iters": a.iters,
        "runtime_nonce": a.runtime_nonce,
    }, indent=2) + "\n")
    lib.acl_event_work_cleanup()
    audit_files = list(Path(a.trace_dir).glob("rank_*_pid_*.chainout_launch_audit.json"))
    audit = json.loads(audit_files[0].read_text()) if audit_files else {}
    print(json.dumps({
        "launch_rc": int(launch_rc.value), "read_rc": read_rc, "audit": audit,
        "device_scratch_head": [int(out_arr[i]) for i in range(8)],
    }))
if __name__ == "__main__":
    main()
'''
    trace_dir = probe_dir / "event_trace"
    prof_dir = probe_dir / "out"
    trace_dir.mkdir(parents=True, exist_ok=True)
    prof_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-c",
        code,
        "--trace-dir",
        str(trace_dir),
        "--prof-dir",
        str(prof_dir),
        "--preload-lib",
        preload_lib,
        "--kernel-binary",
        kernel_binary,
        "--selector-manifest",
        selector_manifest,
        "--iters",
        str(iters),
        "--runtime-nonce",
        str(runtime_nonce),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if proc.returncode != 0:
        return {"pass": False, "stderr": proc.stderr[-1000:]}
    meta = json.loads(proc.stdout.strip().splitlines()[-1])
    db_files = list(prof_dir.glob("**/ascend_pytorch_profiler_0.db"))
    if not db_files:
        return {"pass": False, "error": "no_profiler_db", **meta}
    pw = prof_dir / "profile_window.json"
    active_start, active_end = load_profile_window(pw)
    tasks = cti_tasks_in_window(db_files[0], active_start, active_end)
    cti_rowids = inject_cti_rowids(db_files[0], active_start, active_end)
    unique_forward = len(tasks) == 1
    unique_reverse = len(cti_rowids) == 1 and (
        not tasks or tasks[0]["rowid"] in cti_rowids
    )
    gm_closure = False
    init = init_scratch_with_nonce(runtime_nonce)
    ref, _ = run_reference(list(init), iters)
    device_head = meta.get("device_scratch_head", [])
    if device_head:
        gm_closure = device_head[0] == ref[0] and ref[250] == ref[250]
        gm_closure = ref[250] != init[250] or ref[251] != init[251]
    audit = meta.get("audit", {})
    launch_ok = int(meta.get("launch_rc", -1)) == 0 and int(audit.get("launch_rc", -1)) == 0
    ok = unique_forward and unique_reverse and launch_ok and gm_closure
    return {
        "pass": ok,
        "iters": iters,
        "runtime_nonce": runtime_nonce,
        "active_window": [active_start, active_end],
        "cti_tasks": tasks,
        "cti_rowids": sorted(cti_rowids),
        "unique_forward": unique_forward,
        "unique_reverse": unique_reverse,
        "gm_closure_pass": gm_closure,
        "audit": audit,
        "profiler_db": str(db_files[0]),
    }


def main() -> None:
    args = parse_args()
    probe_dir = Path(args.probe_dir)
    result = run_profiler_launch_probe(
        probe_dir,
        args.preload_lib,
        args.kernel_binary,
        args.selector_manifest,
        PROBE_ITERS,
        PROBE_NONCE,
        args.timeout_s,
    )
    out = {
        "kernel_name": KERNEL_NAME,
        "probe_iters": PROBE_ITERS,
        "probe_nonce": PROBE_NONCE,
        "bidirectional_unique": result.get("unique_forward") and result.get("unique_reverse"),
        "reachability_gate_pass": result.get("pass", False),
        **result,
    }
    if not out["bidirectional_unique"]:
        out["stop"] = "STOP_KERNEL_TASK_PROJECTION_NOT_UNIQUE"
    Path(args.out_json).write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    if not result.get("pass"):
        raise SystemExit(3)


if __name__ == "__main__":
    main()

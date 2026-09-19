#!/usr/bin/env python3
"""Single-card profiler Level smoke: export DB and verify EVENT_RECORD/EVENT_WAIT TASK."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path

import torch
import torch_npu  # noqa: F401
from torch_npu.profiler import (
    ExportType,
    ProfilerActivity,
    ProfilerLevel,
    _ExperimentalConfig,
    profile,
    tensorboard_trace_handler,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    p.add_argument("--profiler-level", default="Level2", choices=["Level1", "Level2"])
    p.add_argument("--timeout-s", type=int, default=60)
    return p.parse_args()


def discover_types(db_path: Path) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    string_ids = {i: v for i, v in cur.execute("SELECT id, value FROM STRING_IDS")}
    rec_type = next((i for i, v in string_ids.items() if v == "EVENT_RECORD"), None)
    wait_type = next((i for i, v in string_ids.items() if v == "EVENT_WAIT"), None)
    rec_n = wait_n = 0
    if rec_type is not None:
        rec_n = cur.execute("SELECT COUNT(*) FROM TASK WHERE taskType=?", (rec_type,)).fetchone()[0]
    if wait_type is not None:
        wait_n = cur.execute("SELECT COUNT(*) FROM TASK WHERE taskType=?", (wait_type,)).fetchone()[0]
    cann_rec = cur.execute(
        """
        SELECT COUNT(*) FROM CANN_API c JOIN STRING_IDS s ON s.id=c.name
        WHERE s.value='aclrtRecordEvent'
        """
    ).fetchone()[0]
    cann_wait = cur.execute(
        """
        SELECT COUNT(*) FROM CANN_API c JOIN STRING_IDS s ON s.id=c.name
        WHERE s.value='aclrtStreamWaitEvent'
        """
    ).fetchone()[0]
    con.close()
    return {
        "event_record_type": rec_type,
        "event_wait_type": wait_type,
        "event_record_tasks": rec_n,
        "event_wait_tasks": wait_n,
        "cann_record": cann_rec,
        "cann_wait": cann_wait,
    }


def main() -> None:
    args = parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    level = getattr(ProfilerLevel, args.profiler_level)
    t0 = time.time()
    device = torch.device("npu:0")
    torch.npu.set_device(0)
    comm = torch.npu.Stream()
    compute = torch.npu.Stream()
    event = torch.npu.Event()
    active_start = time.time_ns()
    exp = _ExperimentalConfig(
        profiler_level=level,
        record_op_args=True,
        data_simplification=False,
        export_type=[ExportType.Db],
    )
    with profile(
        activities=[ProfilerActivity.CPU, ProfilerActivity.NPU],
        experimental_config=exp,
        on_trace_ready=tensorboard_trace_handler(str(out)),
    ) as prof:
        with torch.npu.stream(comm):
            x = torch.ones(4, device=device)
            event.record(comm)
        with torch.npu.stream(compute):
            compute.wait_event(event)
            y = x + 1
        torch.npu.synchronize()
        prof.step()
    active_end = time.time_ns()
    elapsed = time.time() - t0
    dbs = sorted(out.glob("**/ascend_pytorch_profiler*.db"))
    dbs = [p for p in dbs if "analysis.db" not in p.name]
    if not dbs:
        print("SMOKE_PROFILER_FAIL no db", flush=True)
        raise SystemExit(1)
    stats = discover_types(dbs[0])
    result = {
        "elapsed_s": round(elapsed, 3),
        "profiler_level": args.profiler_level,
        "db": str(dbs[0]),
        "active_start_ns": active_start,
        "active_end_ns": active_end,
        **stats,
        "pass": (
            elapsed < args.timeout_s
            and stats["event_record_tasks"] >= 1
            and stats["event_wait_tasks"] >= 1
            and stats["cann_record"] >= 1
            and stats["cann_wait"] >= 1
            and stats["event_record_tasks"] == stats["cann_record"]
            and stats["event_wait_tasks"] == stats["cann_wait"]
        ),
    }
    (out / "smoke_profiler_result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    if not result["pass"]:
        raise SystemExit(2)
    print("SMOKE_PROFILER_PASS", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""In-process block benchmark for stall observer overhead on Ascend."""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import time
from pathlib import Path
from typing import Any

from probe import _event_elapsed_ms, _init_probing_table, _sample_plan


MODES = (
    "control",
    "host",
    "host_full",
    "rotate",
    "full",
    "rotate_pool",
    "full_pool",
    "full_sparse1_pool",
    "full_sparse5_pool",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--plan", default="control,host,control,rotate,control,full,control")
    parser.add_argument("--block-steps", type=int, default=30_000)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--matmul-size", type=int, default=4096)
    parser.add_argument("--ar-bytes", type=int, default=1 << 20)
    parser.add_argument("--sample-ranks", type=int, default=4)
    parser.add_argument("--hole-ms", type=float, default=200.0)
    parser.add_argument("--heartbeat-every", type=int, default=10_000)
    parser.add_argument("--probing-table", choices=("auto", "off"), default="auto")
    parser.add_argument("--seed", type=int, default=20260806)
    args = parser.parse_args()
    args.plan = tuple(item.strip() for item in args.plan.split(",") if item.strip())
    unknown = sorted(set(args.plan) - set(MODES))
    if unknown:
        parser.error(f"unknown modes: {unknown}")
    if not args.plan or args.block_steps <= 0:
        parser.error("plan must be non-empty and block-steps must be positive")
    return args


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def summarize(values: list[float]) -> dict[str, float]:
    return {
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p99": percentile(values, 0.99),
        "mean": sum(values) / len(values) if values else 0.0,
    }


def main() -> None:
    args = parse_args()

    import torch
    import torch.distributed as dist
    import torch_npu  # noqa: F401

    dist.init_process_group("hccl")
    rank = dist.get_rank()
    world = dist.get_world_size()
    local_rank = int(os.environ.get("LOCAL_RANK", "0"))
    node_rank = int(os.environ.get("GROUP_RANK", os.environ.get("NODE_RANK", "0")))
    torch.npu.set_device(local_rank)
    device = torch.device(f"npu:{local_rank}")

    out = Path(args.out)
    rank_dir = out / f"rank_{rank:04d}"
    rank_dir.mkdir(parents=True, exist_ok=True)
    full_path = rank_dir / "full_timeline.jsonl"

    if args.probing_table == "off":
        table_cls = probing_mod = None
    else:
        table_cls, probing_mod = _init_probing_table()

    dtype = torch.float16
    a = torch.randn(args.matmul_size, args.matmul_size, device=device, dtype=dtype)
    elem_size = torch.tensor([], dtype=dtype).element_size()
    ar = torch.ones(max(1, args.ar_bytes // elem_size), device=device, dtype=dtype)

    for _ in range(args.warmup):
        _ = a @ a
        dist.all_reduce(ar)
        torch.npu.synchronize()
    dist.barrier()

    # Reusing timing events is safe here because every iteration ends at the
    # workload's existing device synchronize and elapsed times are read before
    # the next record. This avoids allocator work on the training hot path.
    pooled_events = tuple(torch.npu.Event(enable_timing=True) for _ in range(3))

    global_step = 0
    anomalies: list[dict[str, Any]] = []
    block_reports: list[dict[str, Any]] = []
    total_saved_rows = 0

    with full_path.open("w") as full_file:
        for block_index, mode in enumerate(args.plan):
            dist.barrier()
            outer_values: list[float] = []
            host_compute_values: list[float] = []
            host_allreduce_values: list[float] = []
            host_sync_values: list[float] = []
            device_compute_values: list[float] = []
            device_collective_values: list[float] = []
            block_saved_rows = 0

            block_start = time.perf_counter()
            for block_step in range(args.block_steps):
                sampled = False
                inclusion_p = 0.0
                device_mode = mode in (
                    "rotate",
                    "full",
                    "rotate_pool",
                    "full_pool",
                    "full_sparse1_pool",
                    "full_sparse5_pool",
                )
                sparse_step = (
                    mode not in ("full_sparse1_pool", "full_sparse5_pool")
                    or global_step % (100 if mode == "full_sparse1_pool" else 20) == 0
                )
                if device_mode and sparse_step:
                    sampled, inclusion_p = _sample_plan(
                        "rotate",
                        step=global_step,
                        rank=rank,
                        world=world,
                        rate=0.0,
                        sample_ranks=args.sample_ranks,
                        seed=args.seed,
                    )

                outer_start = time.perf_counter_ns()
                ev0 = ev1 = ev2 = None
                if sampled:
                    if mode in (
                        "rotate_pool",
                        "full_pool",
                        "full_sparse1_pool",
                        "full_sparse5_pool",
                    ):
                        ev0, ev1, ev2 = pooled_events
                    else:
                        ev0 = torch.npu.Event(enable_timing=True)
                        ev1 = torch.npu.Event(enable_timing=True)
                        ev2 = torch.npu.Event(enable_timing=True)
                    ev0.record()

                host_compute_ms = -1.0
                host_allreduce_ms = -1.0
                host_sync_ms = -1.0
                if mode == "control":
                    _ = a @ a
                    dist.all_reduce(ar)
                    torch.npu.synchronize()
                else:
                    phase_start = time.perf_counter_ns()
                    _ = a @ a
                    host_compute_ms = (time.perf_counter_ns() - phase_start) / 1e6
                    if sampled and ev1 is not None:
                        ev1.record()

                    phase_start = time.perf_counter_ns()
                    dist.all_reduce(ar)
                    host_allreduce_ms = (time.perf_counter_ns() - phase_start) / 1e6
                    if sampled and ev2 is not None:
                        ev2.record()

                    phase_start = time.perf_counter_ns()
                    torch.npu.synchronize()
                    host_sync_ms = (time.perf_counter_ns() - phase_start) / 1e6

                outer_ms = (time.perf_counter_ns() - outer_start) / 1e6
                device_compute_ms = (
                    _event_elapsed_ms(ev0, ev1)
                    if sampled and ev0 is not None and ev1 is not None
                    else -1.0
                )
                device_collective_ms = (
                    _event_elapsed_ms(ev1, ev2)
                    if sampled and ev1 is not None and ev2 is not None
                    else -1.0
                )

                if rank == 0:
                    outer_values.append(outer_ms)
                    if mode != "control":
                        host_compute_values.append(host_compute_ms)
                        host_allreduce_values.append(host_allreduce_ms)
                        host_sync_values.append(host_sync_ms)
                    if sampled:
                        device_compute_values.append(device_compute_ms)
                        device_collective_values.append(device_collective_ms)

                if outer_ms >= args.hole_ms:
                    anomalies.append(
                        {
                            "block_index": block_index,
                            "block_step": block_step,
                            "global_step": global_step,
                            "mode": mode,
                            "outer_ms": outer_ms,
                            "rank": rank,
                        }
                    )

                if mode in (
                    "host_full",
                    "full",
                    "full_pool",
                    "full_sparse1_pool",
                    "full_sparse5_pool",
                ):
                    row = {
                        "ts_ns": time.time_ns(),
                        "rank": rank,
                        "local_rank": local_rank,
                        "node_rank": node_rank,
                        "step": global_step,
                        "op_seq": global_step,
                        "host": socket.gethostname(),
                        "scheme": "rotate",
                        "sampled": int(sampled),
                        "sample_probability": inclusion_p,
                        "injected": 0,
                        "inject_kind": "none",
                        "host_inject_ms": 0.0,
                        "host_compute_enqueue_ms": host_compute_ms,
                        "host_allreduce_call_ms": host_allreduce_ms,
                        "host_sync_ms": host_sync_ms,
                        "host_iter_ms": outer_ms,
                        "device_compute_ms": device_compute_ms,
                        "device_collective_ms": device_collective_ms,
                        "device_phase_ms": (
                            device_compute_ms + device_collective_ms
                            if sampled and device_compute_ms >= 0 and device_collective_ms >= 0
                            else -1.0
                        ),
                    }
                    should_save = (
                        outer_ms >= args.hole_ms
                        or global_step % max(1, args.heartbeat_every) == 0
                    )
                    if should_save:
                        full_file.write(json.dumps(row, sort_keys=True) + "\n")
                        if outer_ms >= args.hole_ms:
                            full_file.flush()
                        block_saved_rows += 1
                        total_saved_rows += 1
                        if table_cls is not None:
                            table_cls(**row).save()

                global_step += 1

            local_elapsed = time.perf_counter() - block_start
            # This Ascend/HCCL build does not implement all-reduce for float64.
            # float32 is easily precise enough for a sub-minute block duration.
            elapsed_tensor = torch.tensor([local_elapsed], dtype=torch.float32, device=device)
            dist.all_reduce(elapsed_tensor, op=dist.ReduceOp.MAX)
            torch.npu.synchronize()
            global_elapsed = float(elapsed_tensor.item())

            rows_tensor = torch.tensor([block_saved_rows], dtype=torch.int64, device=device)
            dist.all_reduce(rows_tensor, op=dist.ReduceOp.SUM)
            torch.npu.synchronize()

            if rank == 0:
                clean_outer = [value for value in outer_values if value < args.hole_ms]
                block_reports.append(
                    {
                        "block_index": block_index,
                        "mode": mode,
                        "steps": args.block_steps,
                        "elapsed_s": global_elapsed,
                        "steps_per_s": args.block_steps / global_elapsed,
                        "outer_ms": summarize(outer_values),
                        "clean_outer_ms": summarize(clean_outer),
                        "host_compute_ms": summarize(host_compute_values),
                        "host_allreduce_ms": summarize(host_allreduce_values),
                        "host_sync_ms": summarize(host_sync_values),
                        "device_compute_ms": summarize(device_compute_values),
                        "device_collective_ms": summarize(device_collective_values),
                        "rank0_anomaly_steps": len(outer_values) - len(clean_outer),
                        "saved_rows_global": int(rows_tensor.item()),
                    }
                )
            dist.barrier()

        full_file.flush()

    (rank_dir / "anomalies.json").write_text(json.dumps(anomalies, indent=2, sort_keys=True))
    (rank_dir / "meta.json").write_text(
        json.dumps(
            {
                **vars(args),
                "plan": list(args.plan),
                "rank": rank,
                "world_size": world,
                "local_rank": local_rank,
                "node_rank": node_rank,
                "host": socket.gethostname(),
                "total_saved_rows": total_saved_rows,
                "probing_table": int(table_cls is not None),
            },
            indent=2,
            sort_keys=True,
        )
    )

    if rank == 0:
        (out / "blocks.json").write_text(json.dumps(block_reports, indent=2, sort_keys=True))

    if probing_mod is not None and table_cls is not None:
        try:
            frame = probing_mod.query("SELECT * FROM python.stall_timeline ORDER BY step")
            (rank_dir / "probing_rows.txt").write_text(str(len(frame)))
        except Exception as exc:  # noqa: BLE001
            (rank_dir / "probing_query_error.txt").write_text(repr(exc))

    print(
        f"OVERHEAD_DONE rank={rank} blocks={len(args.plan)} steps={global_step} "
        f"saved_rows={total_saved_rows}",
        flush=True,
    )
    os._exit(0)


if __name__ == "__main__":
    main()

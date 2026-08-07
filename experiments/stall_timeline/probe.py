#!/usr/bin/env python3
"""Distributed host/device stall timeline prototype for Ascend NPU.

The script intentionally uses only one coarse device event pair per sampled
rank-step.  It validates the observation primitive before considering an
expensive per-kernel profiler.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument(
        "--scheme",
        choices=("host", "sentinel", "aligned", "rotate", "random"),
        default="sentinel",
    )
    p.add_argument("--iters", type=int, default=120)
    p.add_argument("--duration-s", type=float, default=0.0)
    p.add_argument("--stop-check-every", type=int, default=10000)
    p.add_argument("--warmup", type=int, default=20)
    p.add_argument("--matmul-size", type=int, default=1024)
    p.add_argument("--ar-bytes", type=int, default=1 << 20)
    p.add_argument("--sample-rate", type=float, default=0.1)
    p.add_argument("--sample-ranks", type=int, default=4)
    p.add_argument("--inject-kind", choices=("none", "host", "device"), default="device")
    p.add_argument("--inject-rank", type=int, default=7)
    p.add_argument("--inject-start", type=int, default=20)
    p.add_argument("--inject-stop", type=int, default=100)
    p.add_argument("--inject-every", type=int, default=20)
    p.add_argument("--inject-ms", type=float, default=500.0)
    p.add_argument("--inject-matmul-size", type=int, default=2048)
    p.add_argument("--hole-ms", type=float, default=200.0)
    p.add_argument(
        "--record-mode",
        choices=("full", "sampled", "anomaly"),
        default="full",
        help="sampled keeps sampled ranks; anomaly keeps only anomalies and heartbeats",
    )
    p.add_argument("--heartbeat-every", type=int, default=1000)
    p.add_argument("--flush-every", type=int, default=100)
    p.add_argument("--seed", type=int, default=20260806)
    return p.parse_args()


def _init_probing_table() -> tuple[Optional[type], Optional[Any]]:
    """Return the custom table class and probing module, or degrade cleanly."""
    try:
        import probing
        from probing.core import table

        # The fixed cluster wheel predates ``lazy=`` while the current source
        # supports it.  Keep the prototype compatible with both so the first
        # smoke can validate the data path without forcing a wheel rebuild.
        try:
            table_decorator = table("stall_timeline", lazy=True)
        except TypeError:
            table_decorator = table("stall_timeline")

        @table_decorator
        @dataclass
        class StallTimeline:
            ts_ns: int = 0
            rank: int = -1
            local_rank: int = -1
            node_rank: int = -1
            step: int = -1
            op_seq: int = -1
            host: str = ""
            scheme: str = ""
            sampled: int = 0
            sample_probability: float = 0.0
            injected: int = 0
            inject_kind: str = "none"
            host_inject_ms: float = 0.0
            host_compute_enqueue_ms: float = 0.0
            host_allreduce_call_ms: float = 0.0
            host_sync_ms: float = 0.0
            host_iter_ms: float = 0.0
            device_compute_ms: float = -1.0
            device_collective_ms: float = -1.0
            device_phase_ms: float = -1.0

        return StallTimeline, probing
    except Exception as exc:  # noqa: BLE001
        print(f"PROBING_TABLE_UNAVAILABLE err={exc!r}", flush=True)
        return None, None


def _sample_plan(
    scheme: str,
    *,
    step: int,
    rank: int,
    world: int,
    rate: float,
    sample_ranks: int,
    seed: int,
) -> tuple[bool, float]:
    if scheme == "host":
        return False, 0.0
    if scheme == "sentinel":
        return True, 1.0
    if scheme == "aligned":
        rate = min(1.0, max(0.0, rate))
        if rate <= 0:
            return False, 0.0
        period = max(1, int(round(1.0 / rate)))
        return (step % period) == 0, 1.0 / period
    if scheme == "random":
        rate = min(1.0, max(0.0, rate))
        # SplitMix64-style stable hash: independent rank-step exploration with
        # no Python hash randomization and no cross-rank communication.
        x = (seed ^ (step * 0x9E3779B97F4A7C15) ^ (rank * 0xBF58476D1CE4E5B9)) & ((1 << 64) - 1)
        x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & ((1 << 64) - 1)
        x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & ((1 << 64) - 1)
        x ^= x >> 31
        return (x / float(1 << 64)) < rate, rate
    k = min(world, max(1, sample_ranks))
    # Spread the fixed event budget across the world instead of sampling a
    # contiguous rank block.  On 2x16 this observes both nodes every step and
    # still covers every rank in at most ceil(world/k) steps.
    stride = max(1, world // k)
    selected = {(step + j * stride) % world for j in range(k)}
    return rank in selected, k / world


def _is_injected(args: argparse.Namespace, step: int, rank: int) -> bool:
    if args.inject_kind == "none" or rank != args.inject_rank:
        return False
    if step < args.inject_start or step >= args.inject_stop:
        return False
    return (step - args.inject_start) % max(1, args.inject_every) == 0


def _event_elapsed_ms(start: Any, end: Any) -> float:
    try:
        return float(start.elapsed_time(end))
    except Exception:
        return -1.0


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
    timeline_path = rank_dir / "timeline.jsonl"

    table_cls, probing_mod = _init_probing_table()

    dtype = torch.float16
    a = torch.randn(args.matmul_size, args.matmul_size, device=device, dtype=dtype)
    ar_elems = max(1, args.ar_bytes // torch.tensor([], dtype=dtype).element_size())
    ar = torch.ones(ar_elems, device=device, dtype=dtype)

    inj_a = None
    inject_repeats = 0
    inject_one_ms = 0.0
    if args.inject_kind == "device":
        inj_a = torch.randn(
            args.inject_matmul_size,
            args.inject_matmul_size,
            device=device,
            dtype=dtype,
        )
        # Calibrate on every rank to avoid a special-rank setup bubble.  Only
        # the configured culprit uses the resulting repeat count later.
        for _ in range(3):
            _ = inj_a @ inj_a
        torch.npu.synchronize()
        c0, c1 = torch.npu.Event(enable_timing=True), torch.npu.Event(enable_timing=True)
        c0.record()
        for _ in range(4):
            _ = inj_a @ inj_a
        c1.record()
        torch.npu.synchronize()
        inject_one_ms = max(0.001, _event_elapsed_ms(c0, c1) / 4.0)
        inject_repeats = max(1, int(math.ceil(args.inject_ms / inject_one_ms)))

    for _ in range(args.warmup):
        _ = a @ a
        dist.all_reduce(ar)
        torch.npu.synchronize()
    dist.barrier()

    meta = {
        **vars(args),
        "rank": rank,
        "world_size": world,
        "local_rank": local_rank,
        "node_rank": node_rank,
        "host": socket.gethostname(),
        "inject_one_ms": inject_one_ms,
        "inject_repeats": inject_repeats,
        "probing_table": int(table_cls is not None),
    }
    (rank_dir / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True))
    # Shared-filesystem metadata writes can skew rank entry by hundreds of ms.
    # Re-align after all setup I/O so that the first measured step is not a
    # false host-side stall.
    dist.barrier()

    completed_steps = 0
    measure_started = time.perf_counter()
    stop_flag = torch.zeros(1, dtype=torch.int32, device=device)
    with timeline_path.open("w") as f:
        pending_rows = 0
        for step in range(args.iters):
            sampled, inclusion_p = _sample_plan(
                args.scheme,
                step=step,
                rank=rank,
                world=world,
                rate=args.sample_rate,
                sample_ranks=args.sample_ranks,
                seed=args.seed,
            )
            injected = _is_injected(args, step, rank)
            t_iter0 = time.perf_counter_ns()

            host_inject_ms = 0.0
            if injected and args.inject_kind == "host":
                t_h0 = time.perf_counter_ns()
                time.sleep(args.inject_ms / 1000.0)
                host_inject_ms = (time.perf_counter_ns() - t_h0) / 1e6

            ev0 = ev1 = ev2 = None
            if sampled:
                ev0 = torch.npu.Event(enable_timing=True)
                ev1 = torch.npu.Event(enable_timing=True)
                ev2 = torch.npu.Event(enable_timing=True)
                ev0.record()

            t_compute0 = time.perf_counter_ns()
            _ = a @ a
            if injected and args.inject_kind == "device" and inj_a is not None:
                for _ in range(inject_repeats):
                    _ = inj_a @ inj_a
            host_compute_enqueue_ms = (time.perf_counter_ns() - t_compute0) / 1e6

            if sampled and ev1 is not None:
                ev1.record()

            t_ar0 = time.perf_counter_ns()
            dist.all_reduce(ar)
            host_allreduce_call_ms = (time.perf_counter_ns() - t_ar0) / 1e6
            if sampled and ev2 is not None:
                ev2.record()

            t_sync0 = time.perf_counter_ns()
            torch.npu.synchronize()
            host_sync_ms = (time.perf_counter_ns() - t_sync0) / 1e6
            host_iter_ms = (time.perf_counter_ns() - t_iter0) / 1e6
            device_compute_ms = (
                _event_elapsed_ms(ev0, ev1) if sampled and ev0 is not None and ev1 is not None else -1.0
            )
            device_collective_ms = (
                _event_elapsed_ms(ev1, ev2) if sampled and ev1 is not None and ev2 is not None else -1.0
            )
            device_phase_ms = (
                _event_elapsed_ms(ev0, ev2) if sampled and ev0 is not None and ev2 is not None else -1.0
            )

            should_record = (
                args.record_mode == "full"
                or (args.record_mode == "sampled" and sampled)
                or injected
                or host_iter_ms >= args.hole_ms
                or step % max(1, args.heartbeat_every) == 0
            )
            row = {
                "ts_ns": time.time_ns(),
                "rank": rank,
                "local_rank": local_rank,
                "node_rank": node_rank,
                "step": step,
                "op_seq": step,
                "host": socket.gethostname(),
                "scheme": args.scheme,
                "sampled": int(sampled),
                "sample_probability": inclusion_p,
                "injected": int(injected),
                "inject_kind": args.inject_kind if injected else "none",
                "host_inject_ms": host_inject_ms,
                "host_compute_enqueue_ms": host_compute_enqueue_ms,
                "host_allreduce_call_ms": host_allreduce_call_ms,
                "host_sync_ms": host_sync_ms,
                "host_iter_ms": host_iter_ms,
                "device_compute_ms": device_compute_ms,
                "device_collective_ms": device_collective_ms,
                "device_phase_ms": device_phase_ms,
            }
            if should_record:
                f.write(json.dumps(row, sort_keys=True) + "\n")
                pending_rows += 1
                if host_iter_ms >= args.hole_ms or pending_rows >= max(1, args.flush_every):
                    f.flush()
                    pending_rows = 0
                if table_cls is not None:
                    try:
                        table_cls(**row).save()
                    except Exception as exc:  # noqa: BLE001
                        if step == 0:
                            print(f"PROBING_TABLE_SAVE_FAILED rank={rank} err={exc!r}", flush=True)

            completed_steps = step + 1
            if args.duration_s > 0 and completed_steps % max(1, args.stop_check_every) == 0:
                if rank == 0:
                    stop_flag.fill_(
                        1 if time.perf_counter() - measure_started >= args.duration_s else 0
                    )
                dist.broadcast(stop_flag, src=0)
                torch.npu.synchronize()
                if int(stop_flag.item()) != 0:
                    break
        f.flush()

    meta["completed_steps"] = completed_steps
    meta["completed_span_s"] = time.perf_counter() - measure_started
    (rank_dir / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True))

    # Prove that the records are queryable through Probing, rather than only
    # through the side JSONL ground truth.
    if probing_mod is not None and table_cls is not None:
        try:
            df = probing_mod.query("SELECT * FROM python.stall_timeline ORDER BY step")
            df.to_json(rank_dir / "probing_timeline.jsonl", orient="records", lines=True)
            print(f"PROBING_QUERY_OK rank={rank} rows={len(df)}", flush=True)
        except Exception as exc:  # noqa: BLE001
            (rank_dir / "probing_query_error.txt").write_text(repr(exc))
            print(f"PROBING_QUERY_FAILED rank={rank} err={exc!r}", flush=True)

    print(
        "STALL_TIMELINE_DONE "
        f"rank={rank} scheme={args.scheme} steps={completed_steps} repeats={inject_repeats}",
        flush=True,
    )
    # HCCL teardown can hang in this hold image. Evidence is already flushed.
    os._exit(0)


if __name__ == "__main__":
    main()

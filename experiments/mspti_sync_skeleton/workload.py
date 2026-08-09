#!/usr/bin/env python3
"""短时训练式 NPU compute + HCCL loop，用于 MSPTI 同步骨架采集。"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--collector-lib", required=True)
    parser.add_argument("--collector", choices=("on", "off"), default="on")
    parser.add_argument(
        "--capture-ranks",
        default="0",
        help="all、0 或逗号分隔 rank；未选 rank 不加载 MSPTI",
    )
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--steps", type=int, default=2)
    parser.add_argument("--matmul-size", type=int, default=2048)
    parser.add_argument("--allreduce-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--gap-us", type=float, default=50.0)
    parser.add_argument("--reorder-us", type=float, default=1000.0)
    return parser.parse_args()


def rank_selected(spec: str, rank: int) -> bool:
    if spec == "all":
        return True
    return rank in {int(value.strip()) for value in spec.split(",") if value.strip()}


class Collector:
    def __init__(
        self,
        library_path: str,
        output_path: Path,
        rank: int,
        device_id: int,
        gap_ns: int,
        reorder_ns: int,
    ) -> None:
        self.lib = ctypes.CDLL(library_path, mode=ctypes.RTLD_GLOBAL)
        self.lib.mspti_skeleton_start.argtypes = [
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_uint64,
        ]
        self.lib.mspti_skeleton_start.restype = ctypes.c_int
        self.lib.mspti_skeleton_step.argtypes = [ctypes.c_int64, ctypes.c_int]
        self.lib.mspti_skeleton_step.restype = ctypes.c_int
        self.lib.mspti_skeleton_stop.argtypes = []
        self.lib.mspti_skeleton_stop.restype = ctypes.c_int
        self.lib.mspti_skeleton_record_sync.argtypes = [
            ctypes.c_char_p,
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.c_uint64,
        ]
        self.lib.mspti_skeleton_record_sync.restype = None
        rc = self.lib.mspti_skeleton_start(
            os.fsencode(output_path), rank, device_id, gap_ns, reorder_ns
        )
        if rc != 0:
            raise RuntimeError(f"mspti_skeleton_start failed: rc={rc}")
        self.running = True

    def step(self, step: int, phase: int) -> None:
        rc = self.lib.mspti_skeleton_step(step, phase)
        if rc != 0:
            raise RuntimeError(f"mspti_skeleton_step failed: rc={rc}")

    def record_sync(self, name: str, start_ns: int, end_ns: int, stream_token: int = 0) -> None:
        self.lib.mspti_skeleton_record_sync(
            name.encode("utf-8"), start_ns, end_ns, stream_token
        )

    def stop(self) -> None:
        if self.running:
            rc = self.lib.mspti_skeleton_stop()
            self.running = False
            if rc != 0:
                raise RuntimeError(f"mspti_skeleton_stop failed: rc={rc}")


def host_monotonic_raw_ns() -> int:
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)


def synchronize_current_stream(torch_module: object, collector: Collector | None) -> None:
    """必要 stream sync；优先让 MSPTI STREAM_SYNCHRONIZED 上报，并补 host marker。

    不经 ctypes 直调 aclrtSynchronizeStream：smoke9 在 ACL interpose + 写
    correlationData 路径上 SIGSEGV。这里只用公开 torch.npu stream sync。
    """
    start_ns = host_monotonic_raw_ns()
    torch_module.npu.current_stream().synchronize()
    end_ns = host_monotonic_raw_ns()
    if collector is not None:
        collector.record_sync(
            "torch.npu.Stream.synchronize",
            start_ns,
            end_ns,
            0,
        )


def main() -> None:
    args = parse_args()

    import torch
    import torch.distributed as dist
    import torch_npu  # noqa: F401

    rank = int(os.environ["RANK"])
    world_size = int(os.environ["WORLD_SIZE"])
    local_rank = int(os.environ["LOCAL_RANK"])
    node_rank = int(os.environ.get("GROUP_RANK", os.environ.get("NODE_RANK", "0")))

    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)
    skeleton_path = output_dir / f"rank_{rank:04d}.skeleton.jsonl"
    meta_path = output_dir / f"rank_{rank:04d}.meta.json"

    # 官方 CANN sample 在 aclInit / context 创建前注册 MSPTI。这里同样在
    # init_process_group 和首次 set_device 前订阅，避免 late attach 丢失 Activity。
    selected = args.collector == "on" and rank_selected(args.capture_ranks, rank)
    collector = None
    collector_start_ns = time.perf_counter_ns()
    if selected:
        collector = Collector(
            args.collector_lib,
            skeleton_path,
            rank,
            local_rank,
            int(args.gap_us * 1000),
            int(args.reorder_us * 1000),
        )
    collector_start_ms = (time.perf_counter_ns() - collector_start_ns) / 1e6

    dist.init_process_group("hccl")
    torch.npu.set_device(local_rank)
    device = torch.device(f"npu:{local_rank}")

    dtype = torch.float16
    matrix = torch.randn(
        args.matmul_size, args.matmul_size, dtype=dtype, device=device
    )
    vector_elements = max(1, args.allreduce_bytes // torch.tensor([], dtype=dtype).element_size())
    collective = torch.ones(vector_elements, dtype=dtype, device=device)

    for _ in range(args.warmup):
        hidden = matrix @ matrix
        hidden = torch.nn.functional.gelu(hidden)
        dist.all_reduce(collective)
    synchronize_current_stream(torch, collector)
    dist.barrier()

    step_host_ms: list[float] = []
    measure_start = time.perf_counter_ns()
    for step in range(args.steps):
        if collector is not None:
            collector.step(step, 0)
        started = time.perf_counter_ns()
        hidden = matrix @ matrix
        hidden = torch.nn.functional.gelu(hidden)
        hidden = hidden @ matrix
        collective.add_(hidden.flatten()[0])
        dist.all_reduce(collective)
        step_host_ms.append((time.perf_counter_ns() - started) / 1e6)
        if collector is not None:
            collector.step(step, 1)

    sync_started = time.perf_counter_ns()
    synchronize_current_stream(torch, collector)
    final_sync_ms = (time.perf_counter_ns() - sync_started) / 1e6
    measured_ms = (time.perf_counter_ns() - measure_start) / 1e6

    collector_stop_ns = time.perf_counter_ns()
    if collector is not None:
        collector.stop()
    collector_stop_ms = (time.perf_counter_ns() - collector_stop_ns) / 1e6

    metadata = {
        **vars(args),
        "run_id": os.environ.get("RUN_ID", ""),
        "rank": rank,
        "world_size": world_size,
        "local_rank": local_rank,
        "node_rank": node_rank,
        "host": socket.gethostname(),
        "collector_selected": selected,
        "collector_start_ms": collector_start_ms,
        "collector_stop_ms": collector_stop_ms,
        "step_host_ms": step_host_ms,
        "final_sync_ms": final_sync_ms,
        "measured_ms": measured_ms,
    }
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"MSPTI_SKELETON_DONE rank={rank} selected={int(selected)} "
        f"measured_ms={measured_ms:.3f} final_sync_ms={final_sync_ms:.3f}",
        flush=True,
    )
    dist.barrier()
    dist.destroy_process_group()


if __name__ == "__main__":
    main()

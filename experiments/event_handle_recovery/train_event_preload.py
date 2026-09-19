#!/usr/bin/env python3
"""16-rank DDP short train + torch_npu profiler + ACL event LD_PRELOAD finalize."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from preload_bindings import bind_work_api, load_preload_lib

import torch
import torch.distributed as dist
import torch.nn as nn
import torch.nn.functional as F
import torch_npu  # noqa: F401
from torch.nn.parallel import DistributedDataParallel as DDP
from torch_npu.profiler import (
    ExportType,
    ProfilerActivity,
    ProfilerLevel,
    _ExperimentalConfig,
    profile,
    tensorboard_trace_handler,
)


class TinyMLP(nn.Module):
    def __init__(self, dim: int = 4096, n_layers: int = 4):
        super().__init__()
        self.layers = nn.ModuleList(
            [nn.Linear(dim, dim, bias=False) for _ in range(n_layers)]
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for i, layer in enumerate(self.layers):
            x = layer(x)
            if i + 1 < len(self.layers):
                x = torch.relu(x)
        return x


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    p.add_argument("--trace-dir", required=True, help="ACL_EVENT_TRACE_DIR for preload bins")
    p.add_argument("--dim", type=int, default=4096)
    p.add_argument("--batch", type=int, default=256)
    p.add_argument("--warmup", type=int, default=2)
    p.add_argument("--steps", type=int, default=3)
    p.add_argument("--preload-lib", default="")
    p.add_argument(
        "--profiler-level",
        default="Level1",
        choices=["Level0", "Level1", "Level2", "Level_none"],
    )
    return p.parse_args()


def resolve_profiler_level(name: str) -> ProfilerLevel:
    return getattr(ProfilerLevel, name)


def _work_lib(preload_lib: str):
    if not preload_lib:
        return None
    return bind_work_api(load_preload_lib(preload_lib))


def finalize_event_trace(preload_lib: str) -> int:
    lib = _work_lib(preload_lib)
    if lib is None:
        return 0
    return int(lib.acl_event_trace_finalize())


def work_prepare(preload_lib: str) -> None:
    lib = _work_lib(preload_lib)
    if lib is None:
        return
    lib.acl_event_work_prepare()


def work_cleanup(preload_lib: str) -> None:
    lib = _work_lib(preload_lib)
    if lib is None:
        return
    lib.acl_event_work_cleanup()


def delay_arm(preload_lib: str) -> None:
    lib = _work_lib(preload_lib)
    if lib is None:
        return
    lib.acl_event_delay_arm()


def delay_disarm(preload_lib: str) -> None:
    lib = _work_lib(preload_lib)
    if lib is None:
        return
    lib.acl_event_delay_disarm()


def one_step(model: DDP, opt: torch.optim.Optimizer, x: torch.Tensor, y: torch.Tensor) -> float:
    opt.zero_grad(set_to_none=True)
    loss = F.mse_loss(model(x), y)
    loss.backward()
    opt.step()
    return float(loss.detach())


def main() -> None:
    args = parse_args()
    os.environ.setdefault("ACL_EVENT_TRACE_DIR", args.trace_dir)
    local = int(os.environ["LOCAL_RANK"])
    torch.npu.set_device(local)
    dist.init_process_group(backend="hccl")
    rank = dist.get_rank()
    world = dist.get_world_size()
    if rank == 0:
        print(f"INIT_DONE ranks={world} device={torch.npu.get_device_name(0)}", flush=True)

    out = Path(args.output)
    trace_dir = Path(args.trace_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)
    if rank == 0:
        out.mkdir(parents=True, exist_ok=True)
        (out / "manifests").mkdir(exist_ok=True)
        man = {
            "world_size": world,
            "dim": args.dim,
            "batch": args.batch,
            "warmup": args.warmup,
            "steps": args.steps,
            "backend": "hccl",
            "ddp": True,
            "profiler_level": args.profiler_level,
            "data_simplification": False,
            "record_op_args": True,
            "record_shapes": False,
            "with_stack": False,
            "trace_dir": str(trace_dir),
            "preload_lib": args.preload_lib,
            "expected_train_s": "25-180",
        }
        (out / "manifests" / "run.json").write_text(json.dumps(man, indent=2) + "\n")

    model = TinyMLP(args.dim).npu().to(dtype=torch.bfloat16)
    model = DDP(model, device_ids=[local], bucket_cap_mb=25, gradient_as_bucket_view=True)
    opt = torch.optim.SGD(model.parameters(), lr=0.01)
    x = torch.randn(args.batch, args.dim, device="npu", dtype=torch.bfloat16)
    y = torch.randn(args.batch, args.dim, device="npu", dtype=torch.bfloat16)

    t0 = time.time()
    if rank == 0:
        work_prepare(args.preload_lib)
    dist.barrier()
    for i in range(args.warmup):
        loss = one_step(model, opt, x, y)
        torch.npu.synchronize()
        if rank == 0:
            print(f"WARMUP {i + 1}/{args.warmup} loss={loss:.6f}", flush=True)
    if rank == 0:
        print(f"WARMUP_DONE elapsed_s={time.time() - t0:.1f}", flush=True)

    prof_dir = out / "args_on"
    prof_dir.mkdir(parents=True, exist_ok=True)
    rc = 0
    try:
        if rank == 0:
            exp = _ExperimentalConfig(
                profiler_level=resolve_profiler_level(args.profiler_level),
                record_op_args=True,
                data_simplification=False,
                export_type=[ExportType.Db],
            )
            activities = [ProfilerActivity.CPU, ProfilerActivity.NPU]
            print(
                f"PROFILE_START dir={prof_dir} record_op_args=True steps={args.steps}",
                flush=True,
            )
            delay_arm(args.preload_lib)
            active_start = time.time_ns()
            active_start_mono = time.monotonic_ns()
            with profile(
                activities=activities,
                record_shapes=False,
                profile_memory=False,
                with_stack=False,
                experimental_config=exp,
                on_trace_ready=tensorboard_trace_handler(str(prof_dir)),
            ) as prof:
                for i in range(args.steps):
                    loss = one_step(model, opt, x, y)
                    torch.npu.synchronize()
                    prof.step()
                    print(f"PROFILE_STEP args_on {i} loss={loss:.6f}", flush=True)
            active_end = time.time_ns()
            active_end_mono = time.monotonic_ns()
            window = {
                "active_start_realtime_ns": active_start,
                "active_end_realtime_ns": active_end,
                "active_start_monotonic_ns": active_start_mono,
                "active_end_monotonic_ns": active_end_mono,
                "warmup_steps": args.warmup,
                "active_steps": args.steps,
            }
            (prof_dir / "profile_window.json").write_text(json.dumps(window, indent=2) + "\n")
            delay_disarm(args.preload_lib)
            print("PROFILE_DONE args_on", flush=True)
        else:
            for i in range(args.steps):
                loss = one_step(model, opt, x, y)
                torch.npu.synchronize()
                print(f"NOPROF_STEP args_on {i} loss={loss:.6f}", flush=True)
    finally:
        dist.barrier()
        fin_rc = finalize_event_trace(args.preload_lib)
        if rank == 0:
            work_cleanup(args.preload_lib)
        if rank == 0:
            print(f"EVENT_TRACE_FINALIZE_RC={fin_rc}", flush=True)
        if fin_rc != 0:
            rc = fin_rc

    dist.barrier()
    if rank == 0:
        print("TRAIN_DONE", flush=True)
        print(f"TRAIN_RC={rc}", flush=True)
    dist.destroy_process_group()
    if rc != 0:
        raise SystemExit(rc)


if __name__ == "__main__":
    main()

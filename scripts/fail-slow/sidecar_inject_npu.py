#!/usr/bin/env python3
"""sidecar_inject_npu.py — Ascend NPU 外部干扰 sidecar（cube/hbm）。

与训练共卡：默认用逻辑 device（与 torchrun LOCAL_RANK 同坐标系，尊重 ASCEND_VISIBLE_DEVICES 重排）。
勿频繁 synchronize（与 MetaX 同理，会削弱咬合 / 卡死 START）。

用法:
  SIDECAR_DEVICE=7 python3 sidecar_inject_npu.py --kind cube --duty 0.9 --size 8192 --warmup-seconds 8 --seconds 1800
"""
from __future__ import annotations

import argparse
import os
import signal
import time


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["cube", "hbm"], default="cube")
    ap.add_argument("--duty", type=float, default=0.9)
    ap.add_argument("--period-ms", type=float, default=200)
    ap.add_argument("--seconds", type=float, default=1800)
    ap.add_argument("--size", type=int, default=4096, help="cube: matrix N; hbm: MB")
    ap.add_argument("--warmup-seconds", type=float, default=8.0)
    ap.add_argument("--device", type=int, default=None, help="logical npu id (default SIDECAR_DEVICE or 7)")
    ap.add_argument("--sync-during-pressure", action="store_true")
    args = ap.parse_args()

    if not 0.0 <= args.duty <= 1.0:
        ap.error("--duty must be within [0, 1]")

    import torch
    import torch_npu  # noqa: F401

    dev = args.device
    if dev is None:
        dev = int(os.environ.get("SIDECAR_DEVICE", os.environ.get("LOCAL_RANK", "7")))
    torch.npu.set_device(dev)
    device = torch.device(f"npu:{dev}")
    print(
        f"SIDECAR_NPU_DEVICE logical={dev} ASCEND_VISIBLE_DEVICES={os.environ.get('ASCEND_VISIBLE_DEVICES','')}",
        flush=True,
    )

    if args.kind == "cube":
        n = int(args.size)
        # fp16 GEMM；过大易 OOM，失败则降半
        dtype = torch.float16
        A = B = None
        for try_n in (n, n // 2, 4096, 2048):
            if try_n < 256:
                break
            try:
                A = torch.randn(try_n, try_n, device=device, dtype=dtype)
                B = torch.randn(try_n, try_n, device=device, dtype=dtype)
                for _ in range(3):
                    torch.mm(A, B)
                torch.npu.synchronize()
                n = try_n
                print(f"SIDECAR_CUBE_ALLOC size={n}", flush=True)
                break
            except Exception as exc:  # noqa: BLE001
                print(f"SIDECAR_CUBE_ALLOC_FAIL size={try_n} err={exc}", flush=True)
                A = B = None
                try:
                    torch.npu.empty_cache()
                except Exception:
                    pass
        if A is None or B is None:
            raise SystemExit("cube alloc failed")

        def burst() -> None:
            torch.mm(A, B)

    else:
        mb = max(64, min(int(args.size), 2048))
        nelems = mb * 1024 * 1024 // 2
        src = torch.randn(nelems, device=device, dtype=torch.float16)
        dst = torch.empty_like(src)
        for _ in range(3):
            dst.copy_(src)
            src.copy_(dst)
        torch.npu.synchronize()
        print(f"SIDECAR_HBM_ALLOC mb={mb} elems={nelems}", flush=True)

        def burst() -> None:
            dst.copy_(src)
            src.copy_(dst)

    ops = 0
    stopping = False

    def _on_signal(signum, _frame):
        nonlocal stopping
        stopping = True
        print(f"SIDECAR_SIGNAL signum={signum} ops={ops}", flush=True)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    if args.warmup_seconds:
        print(f"SIDECAR_WARMUP kind={args.kind} seconds={args.warmup_seconds}", flush=True)
    print(
        f"SIDECAR_START kind={args.kind} duty={args.duty} period={args.period_ms}ms size={args.size}",
        flush=True,
    )

    if args.warmup_seconds:
        warm_end = time.time() + args.warmup_seconds
        while time.time() < warm_end and not stopping:
            burst()
            ops += 1

    period_s = args.period_ms / 1000.0
    busy_s = period_s * args.duty
    t_end = time.time() + args.seconds

    while time.time() < t_end and not stopping:
        t0 = time.perf_counter()
        while (time.perf_counter() - t0) < busy_s and not stopping:
            burst()
            ops += 1
        if args.sync_during_pressure:
            torch.npu.synchronize()
        idle_s = period_s - (time.perf_counter() - t0)
        if idle_s > 0 and not stopping:
            time.sleep(idle_s)

    print(f"SIDECAR_STOP ops={ops}", flush=True)


if __name__ == "__main__":
    main()
